"""IExpresser (docs/14 §3.4): an ActionPlan becomes what the role outwardly does.

One model call ("express") on a need-to-know view (docs/14 §3.4; 角色状态说明 §七):
  given     who the role is, its goals and speech style (self-known stable modules only), how it
            feels in words (emotion label and a low / middle / high level, no numbers), the
            recent visible records, the plan's chosen action, expression goal, audience, manner
            and commitment, and who is present
  withheld  state values, construct and process ids, the other candidates and their
            constraints, hypotheses, and anything the role cannot be aware of
The reply becomes AgentActionContent (schemas.parse_expression): our own thought goes to the
role alone; speech and action reach everyone present, the audience being only whom the role
addresses, unless the plan made the expression private, when only its audience receives them.
A failure raises StageError; nothing is sent.
"""
from ..role import skills as sk
from .evidence import EvidenceGate
from ..interfaces import ModelCallRecord, ModelError, PromptView, StageError
from .schemas import EXPRESS_KINDS, EXPRESS_SCHEMA, parse_expression
from .stages import active_skills, expression_briefing, self_profile, visible_records

EXPRESSION_MODULES = ("s.identity.demographics", "s.core_goals.primary", "s.speech_style.baseline")
RECENT = 12


def _level(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    return "低" if value < 1 / 3 else "中" if value < 2 / 3 else "高"


def feeling_text(state):
    """The current emotion in words: labels and a three-level intensity (an engineering reading)."""
    emotion = state.dynamic.get("d.affect.emotion") or {}
    if state.status.get("d.affect.emotion") != "active" or not emotion:
        return ""
    labels = "、".join(emotion.get("label") or []) or "说不清"
    level = _level(emotion.get("intensity"))
    return f"此刻的感受：{labels}" + (f"（强度{level}）" if level else "")


class ModelExpresser:
    """IExpresser over IModel."""

    def __init__(self, registry, model, trace=None, recent=RECENT):
        self.registry = registry
        self.model = model
        self.trace = trace
        self.recent = recent
        self.gate = EvidenceGate(k=0)

    def view(self, plan, ctx):
        present = dict(ctx.present)
        audience = [a for a in plan.expression.audience if a in present]
        upstream = {"plan": {"chosen": plan.chosen,
                             "expression": {"goal": plan.expression.goal, "audience": audience,
                                            "manner": plan.expression.manner,
                                            "private": bool(plan.expression.private)},
                             "commitment": {"formed": plan.commitment.formed, "content": plan.commitment.content}}}
        task = {"kinds": list(EXPRESS_KINDS), "audience": {a: present[a].get("name", a) for a in audience}}
        if ctx.skills:
            hints = sk.expression_hints(list(ctx.skills), active_skills(ctx))
            if hints:
                task["skill_style"] = hints
        background = dict(ctx.background)
        if ctx.briefing:
            background["开场设定"] = expression_briefing(ctx.briefing)
        feeling = feeling_text(ctx.state) if ctx.mode != "profile_only" else ""
        records = visible_records(ctx, self.gate.build(ctx), self.recent)
        return PromptView(self_profile(ctx.stable, self.registry, EXPRESSION_MODULES), records, feeling,
                          upstream, ctx.key[1], present, task, background)

    def realize(self, plan, ctx):
        view = self.view(plan, ctx)
        try:
            result = self.model.complete("express", view, EXPRESS_SCHEMA)
        except ModelError as exc:
            if self.trace is not None and exc.raw is not None:      # keep what came back, even unparsable
                self.trace.model_call(ctx.key, ModelCallRecord("express", view, exc.raw, exc.reasoning))
            raise StageError(f"express: {exc}") from exc
        if self.trace is not None:
            self.trace.model_call(ctx.key, ModelCallRecord("express", view, result.raw, result.reasoning))
        expression = view.upstream["plan"]["expression"]
        return parse_expression(result.output, ctx.key[1], expression["audience"], ctx.present, expression["private"])
