"""What every IRoleAgent here shares (agent interface v2: `IRoleAgent.initialize` / `act`).

One `Session` per `interaction_id + character_id`. `initialize` picks our role file and keeps the
bootstrap; `act` is routed to the session and handed to the subclass (staged.StagedRoleAgent).

- Identity: character ids are role-library ids, the same in every interaction; a character id
  picks the role file of that id (or `personality.role_ref`, or failing both the name). Every
  character of an interaction is initialized first.
- Branch config: `mechanism.mode` sets the ablation mode for this character in this interaction
  (default: the agent's); `mechanism.enable_config` is kept on record only.
- Branches: a different `interaction_id` is a separate session with its own state.
- A repeated `request_id` returns the first result; the same id with a different input is refused.
- One call at a time: model calls run serially in a worker thread.
- No end-of-conversation signal: the interface has none, so a local run lasts the scene's
  max_turns.
"""
import asyncio
import copy

from .agent_api import ENABLE_CONFIG, MODE, ROLE_REF, InterfaceError, check_id, validate_bootstrap, validate_turn_input
from .catalog import digest

class Session:
    """One character in one interaction: its bootstrap, our role, what it has taken in."""

    def __init__(self, interaction_id, character_id, role_id, bootstrap, mode):
        self.interaction_id, self.character_id, self.role_id = interaction_id, character_id, role_id
        self.mode = mode
        self.enable_config = bootstrap["mechanism"].get(ENABLE_CONFIG)
        self.bootstrap = copy.deepcopy(bootstrap)
        self.name = bootstrap["name"]
        self.seen = set()                  # record ids already taken in (or deliberately skipped)
        self.warnings = []


class RoleAgentBase:
    """What every IRoleAgent here shares: the role library, one Session per interaction_id +
    character_id, retries by request_id, and one call at a time. Subclasses implement _act."""

    def _setup(self, roles, mode):
        """`roles` are loaded role files (roles.load_role): the role library. A character picks
        one by `personality.role_ref`, else by its character id, else by persona name."""
        self.roles = {r["id"]: r for r in roles}
        names = [r["persona"]["name"] for r in roles]
        if len(set(names)) != len(names):
            raise ValueError("role names must be unique to be picked by name")
        self.by_name = {r["persona"]["name"]: r["id"] for r in roles}
        self.mode = mode
        self.sessions = {}                 # (interaction_id, character_id) -> Session
        self.cache = {}                    # request_id -> (input digest, result)
        self.log = []                      # one audit entry per successful act
        # One model call at a time: backends keep per-call state (last_reasoning, call counts).
        self.lock = asyncio.Lock()

    def _check_mode(self, mode):
        """The mechanism.mode a bootstrap may ask for; returns the mode to use."""
        return mode

    def _act(self, session, context, request_id):
        raise NotImplementedError

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
        mode = self._check_mode(bootstrap["mechanism"].get(MODE, self.mode))
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
