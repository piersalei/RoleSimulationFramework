"""The framework's key interfaces (plan/10-interfaces.md), as Protocols and data types.

Four top-level interfaces, each with a few sub-interfaces:

    1 IRoleAgent        what the environment group calls        1.1 ITurnRunner  1.2 ITrace
      (IEnvironment is the other side of that interface: theirs, called only by the local runner)
    2 IRoleRepository   role library, profiles, entry state     2.1 IProfileCompiler  2.2 IRegistry
    3 IStage[P]         S1 event modeling / S2 action plan / S3 long-term evolution
                        3.1 IEvidenceGate  3.2 IMechanism  3.3 IModel  3.4 IExpresser
    4 IStateStore       the only place state is written         4.1 IUpdateOperator  4.2 IMagnitude
                                                                4.3 IValidator

Dependencies point down only: IRoleAgent -> ITurnRunner -> IStage / IStateStore. A stage only
*proposes* changes (ChangeProposal); IStateStore.commit turns accepted ones into a new AgentState.

This module holds contracts only: no behaviour beyond small checks on the data types. Values that
must not change once produced (products, profiles, states, changes) are frozen dataclasses with
tuple fields. Full field lists are in plan/09-interface-and-field-spec.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, Mapping, Optional, Protocol, Sequence, TypeVar, Union, runtime_checkable

from .agent_api import (AgentActionContent, CharacterBootstrap, IEnvironment, InteractionRecord,
                        InterfaceError, IRoleAgent, Present)

__all__ = [
    # errors
    "InterfaceError", "AgentCallError", "StageError", "ModelError",
    # vocabularies
    "STAGES", "CHANNELS", "SOURCE_KINDS", "TEMPOS", "BANDS", "JUDGMENT_STATES", "PHASE_KINDS",
    "OPERATOR_NAMES", "LAYERS", "SELF_VISIBILITY", "STAGE_OF_KIND", "STATUSES", "UNWRITABLE",
    # 1
    "IRoleAgent", "IEnvironment", "ITurnRunner", "ITrace", "SessionKey", "PhaseLog", "ModelCallRecord",
    # 2
    "IRoleRepository", "IProfileCompiler", "IRegistry", "StableModule", "StableProfile",
    "ReviewItem", "EnableConfig", "AgentState", "Construct", "DecisionMechanism", "OperatorRule",
    # 3
    "IStage", "IEvidenceGate", "IMechanism", "IModel", "IExpresser", "Phase", "StageContext",
    "StageOutput", "ChangeProposal", "EvidenceRef", "Excluded", "EvidenceSet", "Judgment",
    "PromptView", "ModelResult", "Appraisal", "Hypothesis", "SituationInterpretation",
    "ActionCandidate", "SwitchCondition", "Commitment", "Expression", "ActionPlan",
    "CommitmentRuling", "RelationCourse", "EvolutionResult",
    # 4
    "IStateStore", "IUpdateOperator", "IMagnitude", "IValidator", "Band", "StateChange",
    "RejectedChange", "CommitResult", "Violation",
]


# -- errors ------------------------------------------------------------------
class AgentCallError(RuntimeError):
    """The role agent failed; no partial result is returned and state does not advance."""


class StageError(RuntimeError):
    """A stage failed (e.g. model output breaks the schema); it produces no proposals."""


class ModelError(RuntimeError):
    """A model call failed or its output breaks the requested schema. Never answered by a mock."""


# -- vocabularies ------------------------------------------------------------
StageName = Literal["S1", "S2", "S3"]
STAGES: tuple[StageName, ...] = ("S1", "S2", "S3")

# 亲见 / 听说 / 推断 / 本人行动 / 入场前
Channel = Literal["witnessed", "hearsay", "inferred", "own_action", "pre_entry"]
CHANNELS: tuple[Channel, ...] = ("witnessed", "hearsay", "inferred", "own_action", "pre_entry")

# 明写 / 规则推断 / 模型推断 / 无依据
SourceKind = Literal["explicit", "inferred", "model_inferred", "unknown"]
SOURCE_KINDS: tuple[SourceKind, ...] = ("explicit", "inferred", "model_inferred", "unknown")

# 快变 / 慢变 / 类别
Tempo = Literal["fast", "slow", "categorical"]
TEMPOS: tuple[Tempo, ...] = ("fast", "slow", "categorical")

# 微小 / 小 / 中 / 大 / 扩展, in increasing order
BandName = Literal["tiny", "small", "medium", "large", "extended"]
BANDS: tuple[BandName, ...] = ("tiny", "small", "medium", "large", "extended")

# 定论 / 多假设 / 暂缓
JudgmentState = Literal["settled", "multiple", "suspended"]
JUDGMENT_STATES: tuple[JudgmentState, ...] = ("settled", "multiple", "suspended")

# event: a group of new records (S1, maybe S3); plan: the closing S2 phase of one act
PhaseKind = Literal["event", "feedback", "plan"]
PHASE_KINDS: tuple[PhaseKind, ...] = ("event", "feedback", "plan")
STAGE_OF_KIND: Mapping[str, str] = {"event": "S1", "plan": "S2", "feedback": "S3"}

# 状态标记 (心理构念登记 §3.2); only the last three block writes
STATUSES = ("active", "unknown", "inactive", "disabled", "not_modeled", "not_applicable")
UNWRITABLE = ("disabled", "not_modeled", "not_applicable")

OPERATOR_NAMES: tuple[str, ...] = (
    "emotion_update", "belief_revision", "other_model_update", "self_state_update",
    "relation_update", "group_state_update", "goal_update", "commitment_update",
    "strategy_learning", "memory_update", "decay_or_recovery")

Layer = Literal["stable", "dynamic", "memory"]
LAYERS: tuple[Layer, ...] = ("stable", "dynamic", "memory")

# 能觉察并直接表述 / 有自我理解但可能偏差 / 不能觉察或表述 (角色状态说明 §七)
SelfVisibility = Literal["aware", "partial", "unaware"]
SELF_VISIBILITY: tuple[SelfVisibility, ...] = ("aware", "partial", "unaware")

SessionKey = tuple[str, str]          # (interaction_id, character_id)


# ============================================================================
# 2 IRoleRepository: where a role comes from
# ============================================================================
@dataclass(frozen=True)
class Construct:
    """One construct of the psychology registry (心理构念登记v1 §4-6).

    `basis` says where each derived attribute comes from (source file, section, quoted phrase).
    None means the sources do not state it; consumers treat a None self_visibility as unaware.
    """
    construct_id: str                       # s.* / d.* / m.*
    layer: Layer
    module: str                             # e.g. "traits.personality", "relation_state"
    section: str                            # registry section, e.g. "4.2"
    name: str                               # registry name, e.g. "信任"
    writer: Optional[str]                   # the one primary operator; None for stable
    tempo: Optional[Tempo]                  # magnitude class (状态变化规则 §5.0); None: not classified
    decay: Optional[str]                    # time rule (状态变化规则 §九); None: never decays with time
    self_visibility: Optional[SelfVisibility]
    high_impact: bool                       # the construct is, or contains, a high-impact field
    basis: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionMechanism:
    """One decision process (dm.*, 心理构念登记v1 §7). It judges; it never writes state."""
    dm_id: str
    section: str                            # decision library chapter.section, e.g. "2.1"
    name: str
    options: tuple[str, ...]                # every option item, deduplicated; Judgment.option is one
    option_groups: tuple[tuple[str, tuple[str, ...]], ...]   # (label or "", items) as written
    stages: tuple[StageName, ...]           # stages whose skill lists it (skill §4.1)
    hands_to: tuple[str, ...]               # operators its judgments go to (状态变化规则 §4 / §4.1)
    card: bool                              # has a mechanism card (high-impact process)
    notes: tuple[str, ...] = ()             # parts of the source text that are not options
    basis: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OperatorRule:
    """One of the 11 update operators (状态变化规则v1 §5)."""
    name: str
    section: str                            # rule card, e.g. "5.5"
    writes: tuple[str, ...]                 # construct id whitelist
    stages: tuple[StageName, ...]           # stages allowed to call it (skill §4.2)
    group: str                              # dependency group for atomic commit (§六)
    timing: str                             # the card's 时间与优先级 text
    basis: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StableModule:
    construct_id: str
    value: Any
    status: Literal["active", "inactive", "unknown"]
    source_kind: SourceKind
    confidence: Optional[float]
    needs_review: bool = False
    source_text: Optional[str] = None       # required when source_kind == "model_inferred"

    def __post_init__(self):
        if self.source_kind == "model_inferred" and not self.source_text:
            raise InterfaceError(f"{self.construct_id}: model_inferred needs source_text")
        if self.source_kind == "unknown" and self.value is not None:
            raise InterfaceError(f"{self.construct_id}: unknown must not carry a value")


@dataclass(frozen=True)
class StableProfile:
    """Stable persona snapshot. Immutable within an episode."""
    snapshot_id: str
    role_ref: str
    modules: Mapping[str, StableModule]

    def get(self, construct_id: str) -> Optional[StableModule]:
        return self.modules.get(construct_id)


@dataclass(frozen=True)
class ReviewItem:
    construct_id: str
    value: Any
    source_kind: SourceKind
    source_text: Optional[str]
    reason: str


@dataclass(frozen=True)
class EnableConfig:
    """Which modules / fields / decision processes are on. Missing ids default to `default`."""
    name: str = "default"
    modules: Mapping[str, bool] = field(default_factory=dict)
    fields: Mapping[str, bool] = field(default_factory=dict)
    mechanisms: Mapping[str, bool] = field(default_factory=dict)
    default: bool = True

    def enabled(self, item_id: str) -> bool:
        for table in (self.mechanisms, self.fields, self.modules):
            if item_id in table:
                return table[item_id]
        return self.default


@dataclass(frozen=True)
class AgentState:
    """One committed state. Only IStateStore.commit creates a new one (entry: IRoleRepository.enter).

    `dynamic` / `memory` map each d.* / m.* construct id to its value, shaped as rolesim/slots.py
    says (e.g. d.relation.trust -> {partner: {"value": u}}). `status` maps a construct id, or
    "construct_id/key" for one instance, to its status mark (active, unknown, inactive, disabled,
    not_modeled, not_applicable).
    """
    state_id: str
    prev_state_id: Optional[str]
    stable_snapshot_id: str
    dynamic: Mapping[str, Any]
    memory: Mapping[str, Any]
    versions: Mapping[str, str] = field(default_factory=dict)
    status: Mapping[str, str] = field(default_factory=dict)


class IRoleRepository(Protocol):
    def load(self, role_ref: str) -> StableProfile:
        """Stable profile for a role-library id; same id + version -> same object. Raises if absent."""
        ...

    def enter(self, profile: StableProfile, bootstrap: CharacterBootstrap,
              config: EnableConfig) -> AgentState:
        """Entry state: dynamic from profile entry state -> bootstrap/scene -> prior; else unknown."""
        ...


class IProfileCompiler(Protocol):
    def compile(self, raw: str | dict, config: EnableConfig) -> tuple[StableProfile, list[ReviewItem]]:
        """Profile text -> stable profile + review list. No evidence -> unknown, never a random value."""
        ...


class IRegistry(Protocol):
    """Read-only view of the psychology group's registry, regenerated from their documents."""

    def construct(self, construct_id: str) -> Construct: ...

    def mechanism(self, dm_id: str) -> DecisionMechanism: ...

    def operator(self, name: str) -> OperatorRule: ...

    def ids(self, kind: Literal["construct", "mechanism", "operator"]) -> tuple[str, ...]:
        """Every id of one kind, in source order. Unknown ids raise KeyError in the lookups."""
        ...


# ============================================================================
# 3 IStage: the three psychology stages
# ============================================================================
@dataclass(frozen=True)
class Phase:
    event_id: str                           # the record group's action_id, or "plan:<request_id>"
    phase_no: int
    kind: PhaseKind


@dataclass(frozen=True)
class EvidenceRef:
    event: str                              # record id
    channel: Channel
    claim: Optional[str] = None             # the relayed proposition, for hearsay

    def __post_init__(self):
        if self.channel not in CHANNELS:
            raise InterfaceError(f"evidence {self.event}: unknown channel {self.channel!r}")


@dataclass(frozen=True)
class Excluded:
    event: str
    reason: str                             # e.g. "other_thought", "world_private", "hidden_state"


@dataclass(frozen=True)
class EvidenceSet:
    included: tuple[EvidenceRef, ...]
    excluded: tuple[Excluded, ...] = ()
    retrieved_memories: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Judgment:
    """A decision process's output. Carries no write right."""
    dm_id: str
    option: Optional[str]                   # one of DecisionMechanism.options, or None
    output: Mapping[str, Any]
    evidence: tuple[EvidenceRef, ...]
    hands_to: tuple[str, ...]


@dataclass(frozen=True)
class ChangeProposal:
    """What a stage may produce instead of writing state.

    Address: `key` picks the instance ("/"-joined when a construct has two key levels, e.g.
    "c2/h1" for one harm event towards c2); `field` is a "."-path inside it; None writes the
    whole instance. `grade` is this role's event grade for this dimension (needed for numeric
    changes); `evidence` / `mechanisms` say what the change rests on (default: the judgments
    handed to the operator); `operator` defaults to the construct's primary writer.
    """
    construct_id: str
    key: Optional[str]
    field: Optional[str]
    after: Any
    reason: str
    grade: Optional[int] = None
    evidence: tuple[EvidenceRef, ...] = ()
    mechanisms: tuple[str, ...] = ()
    operator: Optional[str] = None


# -- S1 SituationInterpretation ---------------------------------------------
@dataclass(frozen=True)
class Appraisal:
    goal: str
    kind: Literal["gain", "loss", "threat", "challenge"]
    detail: str = ""


@dataclass(frozen=True)
class Hypothesis:
    statement: str
    confidence: float
    support: tuple[str, ...]                # record ids
    counter: tuple[str, ...]
    discriminator: str                      # what would tell it apart


@dataclass(frozen=True)
class SituationInterpretation:
    situation_definition: str
    appraisal: tuple[Appraisal, ...]
    attribution: Mapping[str, Any]
    hypotheses: tuple[Hypothesis, ...]
    judgment_state: JudgmentState
    information_needs: tuple[str, ...]
    relation_signals: Mapping[str, str]     # partner id -> signal

    def __post_init__(self):
        if not 1 <= len(self.hypotheses) <= 3:
            raise InterfaceError("S1: hypotheses must number 1-3")
        if self.judgment_state not in JUDGMENT_STATES:
            raise InterfaceError(f"S1: judgment_state {self.judgment_state!r}")


# -- S2 ActionPlan -----------------------------------------------------------
@dataclass(frozen=True)
class ActionCandidate:
    action: str
    kept: bool
    constraint: Optional[str] = None        # why it was dropped


@dataclass(frozen=True)
class SwitchCondition:
    signal: str                             # observable
    then: str


@dataclass(frozen=True)
class Commitment:
    formed: bool
    content: Optional[str] = None


@dataclass(frozen=True)
class Expression:
    goal: str
    audience: tuple[str, ...]               # character ids
    manner: str


@dataclass(frozen=True)
class ActionPlan:
    candidates: tuple[ActionCandidate, ...]
    chosen: str
    fallbacks: tuple[str, ...]
    switch_conditions: tuple[SwitchCondition, ...]
    depends_on: tuple[str, ...]             # hypothesis statements
    expected_outcomes: tuple[str, ...]
    commitment: Commitment
    expression: Expression

    def __post_init__(self):
        if self.chosen not in {c.action for c in self.candidates if c.kept}:
            raise InterfaceError("S2: chosen must be a kept candidate")


# -- S3 EvolutionResult ------------------------------------------------------
@dataclass(frozen=True)
class CommitmentRuling:
    commitment: str
    ruling: Literal["kept", "broken", "pending", "released"]


@dataclass(frozen=True)
class RelationCourse:
    partner: str
    course: Literal["improving", "stable", "worsening", "rupture"]
    repair_conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvolutionResult:
    expected_vs_actual: str
    commitment_rulings: tuple[CommitmentRuling, ...]
    relation_course: tuple[RelationCourse, ...]
    learning_attribution: str
    carry_forward: tuple[str, ...]          # what to watch next time


P = TypeVar("P", SituationInterpretation, ActionPlan, EvolutionResult)
P_co = TypeVar("P_co", SituationInterpretation, ActionPlan, EvolutionResult, covariant=True)


@dataclass(frozen=True)
class StageContext:
    phase: Phase
    state: AgentState                       # the one committed snapshot this phase reads
    stable: StableProfile
    records: tuple[InteractionRecord, ...]  # this phase's record group (thoughts of others removed)
    upstream: Mapping[str, Any]             # committed upstream products, e.g. {"S1": ..., "plan": ...}
    present: Mapping[str, Present]
    key: SessionKey = ("", "")
    history: tuple[InteractionRecord, ...] = ()   # records seen before this phase, oldest first
    background: Mapping[str, Any] = field(default_factory=dict)   # what the role was told at initialize

    @property
    def stage(self) -> str:
        return STAGE_OF_KIND[self.phase.kind]


@dataclass(frozen=True)
class StageOutput(Generic[P]):
    product: P
    proposals: tuple[ChangeProposal, ...] = ()
    judgments: tuple[Judgment, ...] = ()
    evidence: EvidenceSet = field(default_factory=lambda: EvidenceSet(()))   # what the stage could see


@runtime_checkable
class IStage(Protocol[P_co]):
    name: StageName

    def run(self, ctx: StageContext) -> StageOutput[P_co]:
        """Read ctx.state only; return a product and proposals. Raise StageError on bad output."""
        ...


class IEvidenceGate(Protocol):
    def build(self, ctx: StageContext) -> EvidenceSet:
        """Only role-visible evidence; others' thoughts, hidden state, world-private -> excluded."""
        ...


class IMechanism(Protocol):
    dm_id: str

    def evaluate(self, ctx: StageContext, evidence: EvidenceSet) -> Judgment:
        """Judge only. An option outside the registry is rejected."""
        ...


@dataclass(frozen=True)
class PromptView:
    """All a model call may see: self-known profile, visible records, state text, upstream.

    `task` is program-built, from the registry: the decision processes this stage may call
    (with their options) and the constructs it may propose changes to (with their fields).
    """
    self_profile: Mapping[str, str]         # not-self-visible fields already turned into phrases
    records: tuple[InteractionRecord, ...]
    state_text: str
    upstream: Mapping[str, Any] = field(default_factory=dict)
    self_id: str = ""
    present: Mapping[str, Any] = field(default_factory=dict)
    task: Mapping[str, Any] = field(default_factory=dict)
    background: Mapping[str, Any] = field(default_factory=dict)   # scene and self texts from initialize


@dataclass(frozen=True)
class ModelResult:
    output: Mapping[str, Any]               # matches the schema
    raw: str
    reasoning: Optional[str] = None


class IModel(Protocol):
    def complete(self, stage: str, view: PromptView, schema: dict) -> ModelResult:
        """One structured call per stage. Raises on failure; never falls back to mock."""
        ...


class IExpresser(Protocol):
    def realize(self, plan: ActionPlan, ctx: StageContext) -> AgentActionContent:
        """Plan -> outward content. Our own thought goes to ourselves only."""
        ...


# ============================================================================
# 4 IStateStore: the only writer
# ============================================================================
@dataclass(frozen=True)
class Band:
    """Legal range for |after - before| of one numeric field (状态变化规则 §5.0, §七).

    `extended` is set only for a grade-4 event: values in (high, extended] are written with the
    soft flag "extended_range" (重大事件扩展区间), anything beyond is rejected.
    """
    name: BandName
    low: float
    high: float
    extended: Optional[float] = None

    def __post_init__(self):
        if self.name not in BANDS or not self.low <= self.high:
            raise InterfaceError(f"band {self.name!r} [{self.low}, {self.high}]")

    def contains(self, delta: float) -> bool:
        """Out of band is rejected, never clipped."""
        return self.low <= abs(delta) <= self.high


@dataclass(frozen=True)
class StateChange:
    construct_id: str
    key: Optional[str]
    field: Optional[str]
    before: Any
    after: Any
    evidence: tuple[EvidenceRef, ...]
    update_operator: str
    decision_mechanisms: tuple[str, ...]
    stage: StageName
    band: Optional[Band]
    group: str
    reason: str
    flag: Optional[str] = None              # soft anomaly code, written and flagged
    grade: Optional[int] = None             # event grade the band was taken from

    def __post_init__(self):
        if not self.evidence:
            raise InterfaceError(f"{self.construct_id}: a change needs evidence")
        if self.update_operator not in OPERATOR_NAMES:
            raise InterfaceError(f"{self.construct_id}: unknown operator {self.update_operator!r}")
        if self.construct_id.startswith("s."):
            raise InterfaceError(f"{self.construct_id}: stable constructs are never written")


@dataclass(frozen=True)
class Violation:
    kind: Literal["hard", "soft"]
    code: str
    detail: str = ""


@dataclass(frozen=True)
class RejectedChange:
    change: Union[StateChange, ChangeProposal]   # the proposal when no change could be drafted
    group: str
    error: Violation


@dataclass(frozen=True)
class CommitResult:
    output_state_id: str
    accepted: tuple[StateChange, ...]
    rejected: tuple[RejectedChange, ...] = ()
    soft_flags: tuple[Violation, ...] = ()


class IStateStore(Protocol):
    def open(self, key: SessionKey, state: AgentState) -> None:
        """Start a session from its entry state (IRoleRepository.enter)."""
        ...

    def snapshot(self, key: SessionKey) -> AgentState: ...

    def commit(self, ctx: StageContext, output: StageOutput) -> CommitResult:
        """Proposal -> operator -> band -> validator -> atomic commit per dependency group.

        `ctx.state` must be the current snapshot. A hard error rejects its whole group; other
        groups commit. Retrying the same (character, event, phase, kind) is a no-op. Stable is
        never written.
        """
        ...


class IUpdateOperator(Protocol):
    name: str                               # one of OPERATOR_NAMES

    def propose(self, ctx: StageContext, proposals: list[ChangeProposal],
                judgments: list[Judgment]) -> tuple[list[StateChange], list[RejectedChange]]:
        """Whitelisted proposals only; fills before / evidence / mechanisms / band / group.
        Proposals it cannot draft (no evidence, conflicting values, ...) come back rejected."""
        ...


class IMagnitude(Protocol):
    def range(self, construct: Construct, field: Optional[str], grade: int, direction: int,
              evidence: Sequence[EvidenceRef], stable: StableProfile,
              instance: Any = None) -> Optional[Band]:
        """Legal band for one numeric field, or None when the field has none. Base band by event
        grade 1-4; personality shifts at most one band; hearsay-only caps at medium,
        inferred-only at small. `direction` is +1 / -1; `instance` is the value after the change."""
        ...


class IValidator(Protocol):
    def check(self, change: StateChange, snapshot: AgentState,
              evidence: EvidenceSet) -> list[Violation]: ...


# ============================================================================
# 1 IRoleAgent: what the environment group calls
# ============================================================================
# IRoleAgent (initialize / act) is defined in agent_api.py with the rest of the environment
# group's interface and re-exported here; IEnvironment is the side we call, not implement.


@dataclass(frozen=True)
class ModelCallRecord:
    stage: str
    input: PromptView
    raw_output: str
    reasoning: Optional[str] = None


@dataclass(frozen=True)
class PhaseLog:
    event_id: str
    phase_no: int
    input_state_id: str
    output_state_id: str
    evidence: EvidenceSet
    judgments: tuple[Judgment, ...]
    products: Mapping[str, Any]             # {"S1": ..., "S2": ..., "S3": ...} as produced
    changes: tuple[StateChange, ...]
    commit: CommitResult
    versions: Mapping[str, str] = field(default_factory=dict)


class ITurnRunner(Protocol):
    def begin(self, key: SessionKey, state: AgentState, stable: StableProfile,
              background: Optional[Mapping[str, Any]] = None) -> None:
        """Open a session from its entry state (IRoleRepository.enter) and stable profile."""
        ...

    def run(self, key: SessionKey, new_records: list[InteractionRecord],
            present: dict[str, Present]) -> tuple[AgentActionContent, AgentState]:
        """Per action_id group: S1 -> commit, then S3 -> commit if it is feedback on our own
        action / commitment. Then S2 -> commit -> IExpresser.realize. Each phase reads the last
        committed snapshot. Any stage failing fails the act; committed phases stay."""
        ...


class ITrace(Protocol):
    """Write-only; no state-write right. Persisted per interaction_id."""

    def phase(self, key: SessionKey, log: PhaseLog) -> None: ...

    def model_call(self, key: SessionKey, call: ModelCallRecord) -> None: ...
