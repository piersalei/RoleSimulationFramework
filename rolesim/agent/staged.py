"""IRoleAgent over the staged pipeline (docs/14 §1): initialize / act -> ITurnRunner.

Same routing as the old adapter (RoleAgentBase: role library, a session per interaction_id +
character_id, retries by request_id, one call at a time). At a character's first act:
IRoleRepository.load + enter (repository.ProfileRepository: profiles/<id>.json, else the role
file converted; compiled by the profile compiler with the same model) and ITurnRunner.begin. Each act
passes the records it has not taken in yet to ITurnRunner.run; another character's thought, if
the runner ever shows one, is dropped with a warning. The result is the full thought / speech /
action content of IExpresser; nothing about the internal state is returned.

Character ids are role-library ids (the same in every interaction), so relation state is keyed
by character id directly. mechanism.mode picks the ablation mode per character (interfaces.MODES);
the role's opening setup and bound persona skills come from the repository.
"""
import copy

from .adapter import RoleAgentBase
from .agent_api import MODE, InterfaceError, record_id
from ..role.profile import ProfileCompiler
from ..role.repository import ProfileRepository
from ..stage.expresser import ModelExpresser
from ..interfaces import FULL_MODE, MODES, AgentCallError, ModelError, StageError
from .runner import TurnRunner
from ..stage.stages import model_stages
from ..state.chooser import ModelValueChooser
from ..state.store import ConstructStore
from .trace import MemoryTrace


class StagedRoleAgent(RoleAgentBase):
    """IRoleAgent: evidence gate -> S1 / S3 / S2 -> store -> expression, per act."""

    def __init__(self, roles, registry, model, trace=None, repository=None, store=None, mode=FULL_MODE,
                 use_skills=True):
        """`mode` is the default ablation mode (a bootstrap's mechanism.mode overrides it per
        character); `use_skills` False keeps the skills on record but out of every prompt."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self._setup(roles, mode)
        self.registry, self.model, self.use_skills = registry, model, bool(use_skills)
        self.trace = trace if trace is not None else MemoryTrace()
        self.repository = repository or ProfileRepository(registry, ProfileCompiler(registry, model), roles)
        self.store = store or ConstructStore(registry)
        self.runner = TurnRunner(self.store, model_stages(registry, model, trace=self.trace),
                                 ModelExpresser(registry, model, self.trace),
                                 ModelValueChooser(registry, model, self.trace), self.trace)

    def _check_mode(self, mode):
        if mode not in MODES:
            raise InterfaceError(f"bootstrap.mechanism.{MODE}: unknown mode {mode!r}; one of {list(MODES)}")
        return mode

    def _begin(self, session):
        key = (session.interaction_id, session.character_id)
        if self.runner.started(key):
            return key
        profile = self.repository.load(session.role_id)
        state = self.repository.enter(profile, session.bootstrap)
        skills = self.repository.skills(session.role_id) if self.use_skills else []
        self.runner.begin(key, state, profile, self.repository.background(session.role_id, session.bootstrap),
                          session.mode, tuple(skills), self.repository.setup(session.role_id))
        return key

    def _act(self, session, context, request_id):
        key = self._begin(session)
        new = []
        for record in context["observations"]:
            rid = record_id(record)
            if rid in session.seen:
                continue
            if record["kind"] == "thought" and record["actor"]["actor_id"] != session.character_id:
                session.seen.add(rid)
                session.warnings.append({"warning": f"{rid}: another actor's thought was shown; not taken in"})
                continue
            new.append(record)
        try:
            content, state = self.runner.run(key, new, context["present_characters"])
        except (StageError, ModelError) as exc:
            raise AgentCallError(f"{session.name}: {exc}") from exc
        session.seen.update(record_id(r) for r in new)
        self.log.append({"call": "act", "pipeline": "stages", "request_id": request_id,
                         "interaction_id": session.interaction_id, "character_id": session.character_id,
                         "name": session.name, "role_id": session.role_id, "taken_in": [record_id(r) for r in new],
                         "state_id": state.state_id, "result": copy.deepcopy(content)})
        return content

