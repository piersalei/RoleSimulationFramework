"""IStage (plan/10 §3): the three psychology stages, each one model call.

ModelStage(name) runs one stage on one StageContext:
  1. the phase kind must match the stage (event -> S1, plan -> S2, feedback -> S3); S3 needs the
     earlier ActionPlan in `upstream["plan"]` (it judges feedback on it);
  2. IEvidenceGate builds the evidence set;
  3. the PromptView carries only the self-known layer: stable modules the role can be aware of
     (aware / partial in the registry), active dynamic constructs with their values, retrieved
     memories, the visible records (recent history + this phase), upstream products, who is
     present, and the stage task from the registry;
  4. IModel.complete; the call is written to ITrace (when given) before parsing, so failed
     outputs are kept too;
  5. schemas.parse_output -> StageOutput; S2's audience must be present.
A model failure or an output that breaks the schema or the registry fails the whole stage
(StageError) and proposes nothing.

Not yet: stable modules the role cannot be aware of (unaware, or not stated in the sources) are
withheld from every prompt; turning them into conditioned phrases (心智建模库 5.4) belongs to the
profile compiler (plan/10 step 6). Rule-based IMechanism implementations are not used: the
decision processes are judged inside the stage call, as the schema's judgments.
"""
import json

from .agent_api import record_id
from .evidence import EvidenceGate
from .interfaces import STAGE_OF_KIND, InterfaceError, ModelCallRecord, ModelError, PromptView, StageError
from .schemas import parse_output, stage_schema, stage_task

KIND_OF = {stage: kind for kind, stage in STAGE_OF_KIND.items()}
HISTORY_WINDOW = 30
SELF_KNOWN = ("aware", "partial")


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def self_profile(stable, registry, modules=None):
    """The self-known layer of the stable profile: {construct name: text}. Modules the role
    cannot be aware of, or whose self-visibility the sources leave open, are withheld."""
    out = {}
    for cid, module in (stable.modules.items() if stable is not None else ()):
        if modules is not None and cid not in modules:
            continue
        try:
            construct = registry.construct(cid)
        except KeyError:
            continue
        if module.status != "active" or module.value is None or construct.self_visibility not in SELF_KNOWN:
            continue
        out[construct.name] = ("（只有部分自知）" if construct.self_visibility == "partial" else "") + _text(module.value)
    return out


def state_text(state, registry, memories=()):
    """Active dynamic constructs the role can be aware of, with their values, then what it recalls."""
    lines = []
    for cid in registry.ids("construct"):
        construct = registry.construct(cid)
        value = state.dynamic.get(cid)
        if construct.layer != "dynamic" or state.status.get(cid) != "active" or value in (None, {}, []):
            continue
        if construct.self_visibility not in SELF_KNOWN:
            continue
        mark = "（只有部分自知）" if construct.self_visibility == "partial" else ""
        lines.append(f"{construct.name}{mark} {cid} = {_text(value)}")
    for m in memories:
        lines.append(f"记得（{m['entry_id']}）：{m['summary']}")
    return "\n".join(lines)


def visible_records(ctx, evidence, window=HISTORY_WINDOW):
    """Records the evidence set includes: the last `window` from history, then this phase's."""
    ids = {e.event for e in evidence.included}
    history = [r for r in ctx.history if record_id(r) in ids]
    history = history[len(history) - window:] if window > 0 else []
    shown = {record_id(r) for r in history}
    current = [r for r in ctx.records if record_id(r) in ids and record_id(r) not in shown]
    return tuple(history + current)


class ModelStage:
    """IStage: one of S1 / S2 / S3 over IModel."""

    def __init__(self, name, registry, model, gate=None, trace=None, window=HISTORY_WINDOW):
        if name not in KIND_OF:
            raise ValueError(f"stage must be one of {sorted(KIND_OF)}, got {name!r}")
        self.name = name
        self.registry = registry
        self.model = model
        self.gate = gate or EvidenceGate()
        self.trace = trace
        self.window = window
        self.schema = stage_schema(name, registry)
        self.task = stage_task(name, registry)

    def view(self, ctx, evidence):
        return PromptView(self_profile(ctx.stable, self.registry), visible_records(ctx, evidence, self.window),
                          state_text(ctx.state, self.registry, evidence.retrieved_memories), dict(ctx.upstream),
                          ctx.key[1], dict(ctx.present), self.task)

    def run(self, ctx):
        if ctx.phase.kind != KIND_OF[self.name]:
            raise InterfaceError(f"{self.name} runs in a {KIND_OF[self.name]} phase, not {ctx.phase.kind}")
        if self.name == "S3" and "plan" not in ctx.upstream:
            raise InterfaceError("S3 runs only on feedback to an earlier plan (upstream['plan'])")
        evidence = self.gate.build(ctx)
        view = self.view(ctx, evidence)
        try:
            result = self.model.complete(self.name, view, self.schema)
        except ModelError as exc:
            raise StageError(f"{self.name}: {exc}") from exc
        if self.trace is not None:
            self.trace.model_call(ctx.key, ModelCallRecord(self.name, view, result.raw, result.reasoning))
        output = parse_output(self.name, result.output, self.registry, evidence)
        if self.name == "S2":
            me = ctx.key[1]
            strangers = [a for a in output.product.expression.audience if a not in ctx.present or a == me]
            if strangers:
                raise StageError(f"S2: audience must be other present characters, got {strangers}")
        return output


def model_stages(registry, model, gate=None, trace=None):
    """{"S1": ..., "S2": ..., "S3": ...} sharing one model, gate and trace."""
    gate = gate or EvidenceGate()
    return {name: ModelStage(name, registry, model, gate, trace) for name in KIND_OF}
