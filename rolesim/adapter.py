"""Role agents behind the agent interface v2 (`IRoleAgent.initialize` / `act`).

One `Session` per `interaction_id + character_id`. `initialize` picks our role file and keeps
the bootstrap; the stateful `Agent` is built at the character's first `act`, when the present
characters are known. Each `act` feeds the agent only the records it has not taken in yet, as
env-contract Events, so appraisal, mechanism cards, the state chain and the audit trail are the
same as in `run_dialogue`.

- Identity: character ids are role-library ids, the same in every interaction; a character id
  picks the role file of that id (or `personality.role_ref`, or failing both the name). Every
  character of an interaction is initialized first; record actors, recipients and present
  characters are matched by character id. Names are only shown in prompts.
- Branch config: `mechanism.mode` sets the ablation mode for this character in this interaction
  (default: the adapter's); `mechanism.enable_config` is kept on record only.
- Branches: a different `interaction_id` is a separate session with its own state.
- Evidence: a record becomes an Event whose id is `record_id(record)`, so every state change
  still cites the record it came from.
- There is no perceive call: what a character saw since its last turn is appraised when it is
  next asked to act. Its own speech and actions come back in its observations and enter memory
  then; its own thoughts are skipped (it produced them); another character's thought, if the
  runner ever shows one, never reaches a prompt (warned).
- Presence may change: a newcomer gets a relation state on first meeting; a character who left
  stays known but is no longer addressed.
- A repeated `request_id` returns the first result; the same id with a different input is refused.
- Output: speech to everyone present; no thought or physical action yet. Nothing about the
  internal state is returned: the interface has no cognition field.
"""
import asyncio
import copy

from .agent import MODES, Agent
from .agent_api import (ENABLE_CONFIG, ENV_ID, MODE, ROLE_REF, InterfaceError, check_id, record_id, validate_bootstrap,
                        validate_turn_input)
from .catalog import digest
from .interfaces import AgentCallError
from .contract import Event, StepRequest, Visibility
from .vocab import ACTIONS

RESERVED_PREFIXES = ("recap:", "speech:", "world:")
ENV_ACTOR = "env"          # the environment's actor id inside env-contract Events


def scene_context(bootstrap):
    """What the role is told about the situation, as one background text."""
    setting = bootstrap["setting"]
    lines = [f"地点：{setting['place']}" if setting["place"] else None,
             f"时间：{setting['time_text']}" if setting["time_text"] else None,
             *bootstrap["trigger_facts"], f"关于我：{bootstrap['profile_summary']}",
             f"我在这次互动里的总体目标：{bootstrap['core_goals']}" if bootstrap["core_goals"] else None,
             *bootstrap["relationships"],
             f"此前经历：{bootstrap['history_memory']}" if bootstrap["history_memory"] else None]
    return "\n".join(line for line in lines if line)


class Session:
    """One character in one interaction: its bootstrap, our role, and (after its first act) its Agent."""

    def __init__(self, interaction_id, character_id, role_id, bootstrap, mode):
        self.interaction_id, self.character_id, self.role_id = interaction_id, character_id, role_id
        self.mode = mode
        self.enable_config = bootstrap["mechanism"].get(ENABLE_CONFIG)
        self.bootstrap = copy.deepcopy(bootstrap)
        self.name = bootstrap["name"]
        self.agent = None
        self.seen = set()                  # record ids already taken in (or deliberately skipped)
        self.moves = []                    # chosen interaction moves, in order
        self.warnings = []


class RoleAgentAdapter:
    def __init__(self, roles, backend, mode="profile_state_mechanisms", use_skills=True, appraise_state=True,
                 allowed_actions=None):
        """`roles` are loaded role files (roles.load_role): the role library. A character picks
        one by `personality.role_ref`, else by its character id, else by persona name.
        `mode` is the default ablation mode; `allowed_actions` the move space (default: all)."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.roles = {r["id"]: r for r in roles}
        names = [r["persona"]["name"] for r in roles]
        if len(set(names)) != len(names):
            raise ValueError("role names must be unique to be picked by name")
        self.by_name = {r["persona"]["name"]: r["id"] for r in roles}
        self.backend, self.mode = backend, mode
        self.use_skills, self.appraise_state = bool(use_skills), bool(appraise_state)
        self.allowed_actions = list(allowed_actions or sorted(ACTIONS))
        if not set(self.allowed_actions) <= ACTIONS:
            raise ValueError("unsupported action")
        self.sessions = {}                 # (interaction_id, character_id) -> Session
        self.cache = {}                    # request_id -> (input digest, result)
        self.log = []                      # one audit entry per successful act
        # One model call at a time: backends keep per-call state (last_reasoning, call counts).
        self.lock = asyncio.Lock()

    # -- interface ---------------------------------------------------------------
    async def initialize(self, interaction_id, character_id, bootstrap):
        check_id(interaction_id, "interaction_id")
        check_id(character_id, "character_id")
        validate_bootstrap(bootstrap)
        key = (interaction_id, character_id)
        if key in self.sessions:
            if self.sessions[key].bootstrap != bootstrap:
                raise InterfaceError(f"{character_id}: already initialized in {interaction_id} with another bootstrap")
            return None                    # a retried initialize is harmless
        role_id = self._pick(character_id, bootstrap)
        mode = bootstrap["mechanism"].get(MODE, self.mode)
        if mode not in MODES:
            raise InterfaceError(f"bootstrap.mechanism.{MODE}: unknown mode {mode!r}; one of {list(MODES)}")
        taken = [s.character_id for (iid, _), s in self.sessions.items()
                 if iid == interaction_id and s.role_id == role_id]
        if taken:
            raise InterfaceError(f"role {role_id} is already character {taken[0]} in {interaction_id}")
        self.sessions[key] = Session(interaction_id, character_id, role_id, bootstrap, mode)
        return None

    async def act(self, interaction_id, character_id, context, request_id):
        if not isinstance(request_id, str) or not request_id:
            raise InterfaceError("request_id must be a non-empty string")
        session = self._session(interaction_id, character_id)
        validate_turn_input(context, character_id)
        key = digest({"interaction_id": interaction_id, "character_id": character_id, "context": context})
        if request_id in self.cache:
            done_key, result = self.cache[request_id]
            if done_key != key:
                raise InterfaceError(f"request_id {request_id!r} was already used for a different request")
            return copy.deepcopy(result)
        async with self.lock:
            result = await asyncio.to_thread(self._act, session, copy.deepcopy(context), request_id)
        self.cache[request_id] = (key, copy.deepcopy(result))
        return result

    # -- sessions ----------------------------------------------------------------
    def _pick(self, character_id, bootstrap):
        ref = bootstrap["personality"].get(ROLE_REF)
        if ref is not None:
            if ref not in self.roles:
                raise InterfaceError(f"bootstrap.personality.{ROLE_REF}: no role {ref!r}")
            return ref
        if character_id in self.roles:
            return character_id
        if bootstrap["name"] not in self.by_name:
            raise InterfaceError(f"{character_id}: not in the role library and no role is called "
                                 f"{bootstrap['name']!r}; name one with personality.{ROLE_REF}")
        return self.by_name[bootstrap["name"]]

    def _session(self, interaction_id, character_id):
        session = self.sessions.get((interaction_id, character_id))
        if session is None:
            raise InterfaceError(f"{character_id} was not initialized in {interaction_id}")
        return session

    def _role_of(self, session, character_id):
        """Our role id of another character of the same interaction."""
        other = self.sessions.get((session.interaction_id, character_id))
        if other is None:
            raise InterfaceError(f"{character_id} was not initialized in {session.interaction_id}")
        return other.role_id

    def _others(self, session, present):
        """Our role id -> name shown in prompts, for everyone present except the actor."""
        return {self._role_of(session, cid): entry["name"]
                for cid, entry in present.items() if cid != session.character_id}

    def _ready(self, session, present):
        others = self._others(session, present)
        if session.agent is None:
            role = self.roles[session.role_id]
            agent = Agent(self.backend, self.allowed_actions, others, session.mode,
                          skills=role["bound"] if self.use_skills else None, briefing=role["setup"],
                          seat=session.name, appraise_state=self.appraise_state)
            agent.begin(role["persona"])
            agent.scene_context = scene_context(session.bootstrap)
            session.agent = agent
            return
        for role_id, name in others.items():
            if session.agent.meet(role_id, name):
                session.warnings.append({"info": f"met {name} ({role_id}) after the start; relation state created"})
        gone = set(session.agent.others) - set(others)
        if gone:
            session.warnings.append({"info": f"no longer present: {sorted(gone)}"})

    def _event(self, session, record):
        key = record_id(record)
        if record["action_id"].startswith(RESERVED_PREFIXES):
            raise InterfaceError(f"action_id {record['action_id']!r} uses a reserved prefix")
        actor_cid = record["actor"]["actor_id"]
        actor = ENV_ACTOR if actor_cid == ENV_ID else self._role_of(session, actor_cid)
        audience = {session.role_id, *(self._role_of(session, cid) for cid in record["recipients"])}
        if actor != ENV_ACTOR:
            audience.add(actor)
        everyone = {session.role_id, *session.agent.others}
        scope = "public" if audience >= everyone else "private"
        return Event(key, record["turn_index"], record["kind"], actor, record["content"],
                     Visibility(scope, sorted(audience)))

    def _unseen(self, session, records):
        """Events for records the agent has not taken in yet. Thoughts are never taken in:
        its own were produced by it, anyone else's are not its to read."""
        events = []
        for record in records:
            key = record_id(record)
            if key in session.seen:
                continue
            session.seen.add(key)
            if record["kind"] == "thought":
                if record["actor"]["actor_id"] != session.character_id:
                    session.warnings.append({"warning": f"{key}: another actor's thought was shown; not taken in"})
                continue
            events.append(self._event(session, record))
        return events

    @staticmethod
    def _turn(records):
        return max([1, *(r["turn_index"] for r in records)])

    # -- the call ------------------------------------------------------------------
    def _act(self, session, context, request_id):
        self._ready(session, context["present_characters"])
        agent = session.agent
        records = context["observations"]
        events = self._unseen(session, records)
        response = agent.step(StepRequest(role_id=session.role_id, turn=self._turn(records),
                                          scene_context=agent.scene_context, new_events=events,
                                          prev_state_id=agent.state_id, allowed_kinds=["speech"]))
        if response.status != "ok":
            for event in events:
                session.seen.discard(event.event_id)
            raise AgentCallError(f"{session.name}: {response.error}") from agent.last_error
        proposal = response.actions[0]
        session.moves.append(proposal.intent_note)
        result = {"thought": None, "speech": {"content": proposal.content, "recipient_ids": None},
                  "action": None, "order": ["speech"]}
        self.log.append({"call": "act", "request_id": request_id, "interaction_id": session.interaction_id,
                         "mode": session.mode, "enable_config": session.enable_config,
                         "character_id": session.character_id, "name": session.name, "role_id": session.role_id,
                         "taken_in": [e.event_id for e in events], "appraisals": agent.last_appraisals,
                         "controller_input": agent.last_payload, "expression_input": agent.last_express,
                         "decision": agent.last_decision, "reasoning": agent.last_reasoning,
                         "move": proposal.intent_note, "delta": response.delta, "state_after": response.state,
                         "result": copy.deepcopy(result)})
        return result

    # -- for the local loop ------------------------------------------------------
    def closed_by(self, interaction_id):
        """Name of a character whose last move was `close`, else None. Local only: the
        interface has no way to tell the runner (plan/08, section 8.1)."""
        for (iid, _), session in self.sessions.items():
            if iid == interaction_id and session.moves and session.moves[-1] == "close":
                return session.name
        return None
