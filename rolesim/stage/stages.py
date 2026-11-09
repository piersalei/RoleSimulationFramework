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
  5. schemas.parse_output -> StageOutput; S2's audience must be present, and a private
     expression must name one (audience is whom the role addresses; everyone present hears
     unless the expression is private).
A model failure or an output that breaks the schema or the registry fails the whole stage
(StageError) and proposes nothing.

Stable modules the role cannot be aware of reach a prompt only as their conditioned phrases
(written in the profile or by the profile compiler, 心智建模库 5.4), never as values or names.
Rule-based IMechanism implementations are not used: the decision processes are judged inside
the stage call, as the schema's judgments.
"""
import json

from ..role import skills as sk
from ..agent.agent_api import record_id
from .evidence import EvidenceGate
from ..interfaces import (FULL_MODE, MODES, STAGE_OF_KIND, InterfaceError, ModelCallRecord, ModelError, PromptView,
                         StageError)
from .schemas import parse_output, stage_schema, stage_task
from ..vocab import ACTIONS

KIND_OF = {stage: kind for kind, stage in STAGE_OF_KIND.items()}
HISTORY_WINDOW = 30
SELF_KNOWN = ("aware", "partial")
STRATEGY_TEXT = {"omission": "不提", "strategic_ambiguity": "说得模糊"}


def controller_briefing(briefing):
    """The opening setup as the role's own planning may use it: concealed items included."""
    return {"互动目标": briefing["goal"], "想让对方知道": [i["text"] for i in briefing["disclose"]],
            "知道但不打算说出口": [{"内容": i["text"], "方式": STRATEGY_TEXT[i["strategy"]]} for i in briefing["conceal"]]}


def expression_briefing(briefing):
    """The opening setup as the expression call may see it: never the concealed items."""
    return {"互动目标": briefing["goal"], "可以说出的": [i["text"] for i in briefing["disclose"]]}


def active_skills(ctx):
    """Bound persona skills the S1 cues (or the setup's disclose / conceal items) activate, at most
    skills.MAX_ACTIVE by priority x strength (skills.select)."""
    if not ctx.skills:
        return []
    return sk.select(list(ctx.skills), {"cues": dict(ctx.upstream.get("cues") or {})}, ACTIONS, ctx.briefing)


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


TENDENCIES = "说不出来、但会影响你的倾向"


def self_profile(stable, registry, modules=None, tendencies=True):
    """The stable profile as the role may meet it in a prompt (心智建模库 5.3 / 5.4):
    {construct name: text} for modules the role can be aware of, the conditioned phrase in place
    of the value when there is one; modules it cannot be aware of (or whose self-visibility the
    sources leave open) appear only as their phrases, unnamed, under TENDENCIES, and not at all
    without one. `modules` limits the named part; `tendencies` adds the unnamed part."""
    out, hidden = {}, []
    for cid, module in (stable.modules.items() if stable is not None else ()):
        try:
            construct = registry.construct(cid)
        except KeyError:
            continue
        if module.status != "active" or module.value is None:
            continue
        if construct.self_visibility not in SELF_KNOWN:
            if tendencies and module.phrase:
                hidden.append(module.phrase)
            continue
        if modules is not None and cid not in modules:
            continue
        out[construct.name] = ("（只有部分自知）" if construct.self_visibility == "partial" else "") + \
            (module.phrase or _text(module.value))
    if hidden:
        out[TENDENCIES] = "；".join(hidden)
    return out


def state_text(state, registry, memories=(), dynamic=True):
    """Active dynamic constructs the role can be aware of, with their values (unless `dynamic` is
    False, the profile_only mode), then what it recalls."""
    lines = []
    for cid in (registry.ids("construct") if dynamic else ()):
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
        self.schemas = {mode: stage_schema(name, registry, mode == FULL_MODE) for mode in MODES}
        self.tasks = {mode: stage_task(name, registry, mode == FULL_MODE) for mode in MODES}
        self.schema, self.task = self.schemas[FULL_MODE], self.tasks[FULL_MODE]

    def view(self, ctx, evidence):
        task = dict(self.tasks[ctx.mode])
        if self.name == "S2" and ctx.skills:
            task["skills"] = {"note": "该人物惯用的应对方式，供参考，不保证唯一反应",
                              "catalog": sk.catalog(list(ctx.skills)),
                              "active": [{"name": a["name"], "decision": a["decision"], "avoid": a["avoid"]}
                                         for a in active_skills(ctx)]}
        background = dict(ctx.background)
        if ctx.briefing:
            background["开场设定"] = controller_briefing(ctx.briefing)
        return PromptView(self_profile(ctx.stable, self.registry), visible_records(ctx, evidence, self.window),
                          state_text(ctx.state, self.registry, evidence.retrieved_memories, ctx.mode != "profile_only"),
                          dict(ctx.upstream), ctx.key[1], dict(ctx.present), task, background)

    def run(self, ctx):
        if ctx.mode not in MODES:
            raise InterfaceError(f"unknown mode {ctx.mode!r}; one of {MODES}")
        if ctx.phase.kind != KIND_OF[self.name]:
            raise InterfaceError(f"{self.name} runs in a {KIND_OF[self.name]} phase, not {ctx.phase.kind}")
        if self.name == "S3" and ctx.upstream.get("plan") is None:
            raise InterfaceError("S3 runs only on feedback to an earlier plan (upstream['plan'])")
        evidence = self.gate.build(ctx)
        view = self.view(ctx, evidence)
        try:
            result = self.model.complete(self.name, view, self.schemas[ctx.mode])
        except ModelError as exc:
            if self.trace is not None and exc.raw is not None:      # keep what came back, even unparsable
                self.trace.model_call(ctx.key, ModelCallRecord(self.name, view, exc.raw, exc.reasoning))
            raise StageError(f"{self.name}: {exc}") from exc
        if self.trace is not None:
            self.trace.model_call(ctx.key, ModelCallRecord(self.name, view, result.raw, result.reasoning))
        output = parse_output(self.name, result.output, self.registry, evidence, ctx.mode == FULL_MODE)
        if self.name == "S2":
            me = ctx.key[1]
            strangers = [a for a in output.product.expression.audience if a not in ctx.present or a == me]
            if strangers:
                raise StageError(f"S2: audience must be other present characters, got {strangers}")
            if output.product.expression.private and not output.product.expression.audience:
                raise StageError("S2: a private expression must name its audience")
        return output


def model_stages(registry, model, gate=None, trace=None):
    """{"S1": ..., "S2": ..., "S3": ...} sharing one model, gate and trace."""
    gate = gate or EvidenceGate()
    return {name: ModelStage(name, registry, model, gate, trace) for name in KIND_OF}
