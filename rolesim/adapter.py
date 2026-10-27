"""Role agents behind the agent interface v1 (`AgentAdapter.act` / `perceive`).

The runner sends a full view on every call; the adapter keeps one stateful `Agent` per role
and feeds it only the records it has not seen yet, as env-contract Events, so appraisal,
mechanism cards, the state chain and the audit trail are the same as in `run_dialogue`.

- Identity: the view's `name` picks our role (`Directory`); present characters and record
  actors are matched by name, character ids are only used to address recipients.
- Evidence: a record becomes an Event whose id is `record_id(record)`, so every state change
  still cites the record it came from.
- The actor is not asked to perceive its own records; they reach it through its next view
  and are taken in then (own thoughts are skipped: the agent produced them).
- A repeated `request_id` returns the first result; the same id with a different input is
  refused.
- v1 limits: speech only (no thought, no physical action); the set of present characters is
  fixed by the role's first call; one branch per adapter (personality / mechanism are passed
  through unread until their fields are agreed).
"""
import asyncio
import copy

from .agent import MODES, Agent
from .agent_api import (ENV_ID, Directory, InterfaceError, record_id, validate_character_view,
                        validate_perception_input)
from .catalog import digest
from .cognition import project
from .contract import Event, PerceiveRequest, StepRequest, Visibility
from .vocab import ACTIONS

RESERVED_PREFIXES = ("recap:", "speech:", "world:")


class AgentCallError(RuntimeError):
    """The role agent failed; no partial result is returned."""


def scene_context(view):
    """What the role is told about the situation, as one background text."""
    setting = view["setting"]
    lines = [f"地点：{setting['place']}" if setting["place"] else None,
             f"时间：{setting['time_text']}" if setting["time_text"] else None,
             *view["trigger_facts"], f"关于我：{view['profile_summary']}", *view["relationships"],
             f"此前经历：{view['history_memory']}" if view["history_memory"] else None]
    return "\n".join(line for line in lines if line)


class Session:
    """One role in one episode: its Agent plus what the adapter has handed it."""

    def __init__(self, agent, role_id, name, present):
        self.agent, self.role_id, self.name = agent, role_id, name
        self.present = dict(present)       # runner character id -> our role id, fixed at first call
        self.seen = set()                  # record ids already taken in (or deliberately skipped)
        self.last_cognition = None
        self.moves = []                    # chosen interaction moves, in order
        self.warnings = []


class RoleAgentAdapter:
    def __init__(self, roles, backend, mode="profile_state_mechanisms", use_skills=True, appraise_state=True,
                 allowed_actions=None):
        """`roles` are loaded role files (roles.load_role); their persona names are the names the
        runner uses. `allowed_actions` is the move space (default: every move)."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.roles = {r["id"]: r for r in roles}
        self.directory = Directory.from_personas([r["persona"] for r in roles])
        self.backend, self.mode = backend, mode
        self.use_skills, self.appraise_state = bool(use_skills), bool(appraise_state)
        self.allowed_actions = list(allowed_actions or sorted(ACTIONS))
        if not set(self.allowed_actions) <= ACTIONS:
            raise ValueError("unsupported action")
        self.sessions = {}                 # name -> Session
        self.cache = {}                    # request_id -> (call, input digest, result)
        self.log = []                      # one audit entry per successful call
        # One model call at a time: backends keep per-call state (last_reasoning, call counts).
        self.lock = asyncio.Lock()

    # -- interface ---------------------------------------------------------------
    async def act(self, context, request_id):
        validate_character_view(context)
        return await self._serve("act", context, request_id, self._act)

    async def perceive(self, context, request_id):
        validate_perception_input(context)
        return await self._serve("perceive", context, request_id, self._perceive)

    async def _serve(self, call, context, request_id, work):
        if not isinstance(request_id, str) or not request_id:
            raise InterfaceError("request_id must be a non-empty string")
        key = digest(context)
        if request_id in self.cache:
            done_call, done_key, result = self.cache[request_id]
            if (done_call, done_key) != (call, key):
                raise InterfaceError(f"request_id {request_id!r} was already used for a different request")
            return copy.deepcopy(result)
        async with self.lock:
            result = await asyncio.to_thread(work, copy.deepcopy(context), request_id)
        self.cache[request_id] = (call, key, copy.deepcopy(result))
        return result

    # -- sessions ----------------------------------------------------------------
    def _session(self, view):
        name = view["name"]
        present = self.directory.present(view["present_characters"])
        session = self.sessions.get(name)
        if session is None:
            role_id = self.directory.role(name)
            if role_id in present.values():
                raise InterfaceError("present_characters must not contain the acting role itself")
            role = self.roles[role_id]
            others = {rid: view["present_characters"][cid]["name"] for cid, rid in present.items()}
            agent = Agent(self.backend, self.allowed_actions, others, self.mode,
                          skills=role["bound"] if self.use_skills else None, briefing=role["setup"],
                          seat=name, appraise_state=self.appraise_state)
            agent.begin(role["persona"])
            session = self.sessions[name] = Session(agent, role_id, name, present)
        elif present != session.present:
            raise InterfaceError(f"{name}: present characters changed; v1 fixes them at the role's first call")
        if session.last_cognition is not None and view["cognition"] != session.last_cognition:
            session.warnings.append({"warning": "cognition differs from the one last returned; "
                                                "internal state kept as the source of truth"})
        session.agent.scene_context = scene_context(view)
        return session

    def _event(self, session, record):
        key = record_id(record)
        if record["action_id"].startswith(RESERVED_PREFIXES):
            raise InterfaceError(f"action_id {record['action_id']!r} uses a reserved prefix")
        actor = self.directory.actor(record)
        audience = {session.role_id}
        if actor != ENV_ID:
            audience.add(actor)
        audience.update(self.directory.role(e["name"]) for e in record["recipients"].values())
        scope = "public" if audience >= {session.role_id, *session.present.values()} else "private"
        return Event(key, record["turn_index"], record["kind"], actor, record["content"],
                     Visibility(scope, sorted(audience)))

    def _unseen(self, session, records):
        """Events for records the agent has not taken in yet, skipping its own thoughts."""
        events = []
        for record in records:
            key = record_id(record)
            if key in session.seen:
                continue
            session.seen.add(key)
            if record["kind"] == "thought":
                continue        # validated: only the actor's own thoughts reach its view
            events.append(self._event(session, record))
        return events

    @staticmethod
    def _turn(records):
        return max([1, *(r["turn_index"] for r in records)])

    def _reply(self, session):
        agent = session.agent
        cognition = project(agent.state, agent.persona, agent.names, agent.briefing)
        session.last_cognition = copy.deepcopy(cognition)
        return cognition

    # -- calls -------------------------------------------------------------------
    def _act(self, view, request_id):
        session = self._session(view)
        agent = session.agent
        events = self._unseen(session, view["observations"])
        response = agent.step(StepRequest(role_id=session.role_id, turn=self._turn(view["observations"]),
                                          scene_context=agent.scene_context, new_events=events,
                                          prev_state_id=agent.state_id, allowed_kinds=["speech"]))
        if response.status != "ok":
            for event in events:
                session.seen.discard(event.event_id)
            raise AgentCallError(f"{session.name}: {response.error}") from agent.last_error
        proposal = response.actions[0]
        session.moves.append(proposal.intent_note)
        result = {"cognition": self._reply(session),
                  "action": {"thought": None, "speech": {"content": proposal.content, "recipient_ids": None},
                             "action": None, "order": ["speech"]}}
        self.log.append({"call": "act", "request_id": request_id, "name": session.name, "role_id": session.role_id,
                         "taken_in": [e.event_id for e in events], "appraisals": agent.last_appraisals,
                         "controller_input": agent.last_payload, "expression_input": agent.last_express,
                         "decision": agent.last_decision, "reasoning": agent.last_reasoning,
                         "move": proposal.intent_note, "delta": response.delta, "state_after": response.state,
                         "result": copy.deepcopy(result)})
        return result

    def _perceive(self, context, request_id):
        view = context["character_view"]
        session = self._session(view)
        agent = session.agent
        records = view["observations"] + context["new_observations"]
        events = self._unseen(session, records)
        agent.last_appraisals, agent.last_reasoning = [], {}
        response = agent.perceive(PerceiveRequest(role_id=session.role_id, turn=self._turn(records), events=events,
                                                  prev_state_id=agent.state_id))
        if response.status != "ok":
            for event in events:
                session.seen.discard(event.event_id)
            raise AgentCallError(f"{session.name}: {response.error}") from agent.last_error
        result = {"cognition": self._reply(session)}
        self.log.append({"call": "perceive", "request_id": request_id, "name": session.name,
                         "role_id": session.role_id, "taken_in": [e.event_id for e in events],
                         "appraisals": agent.last_appraisals, "reasoning": agent.last_reasoning,
                         "delta": response.delta, "state_after": response.state, "result": copy.deepcopy(result)})
        return result

    # -- for the local loop ------------------------------------------------------
    def closed_by(self):
        """Name of a role whose last move was `close`, else None. Local only: the interface
        has no way yet to tell the runner (plan/07, open question)."""
        for name, session in self.sessions.items():
            if session.moves and session.moves[-1] == "close":
                return name
        return None
