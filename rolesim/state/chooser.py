"""IValueChooser (plan/10 §7): a value inside each slot's band, chosen by the model with a reason.

C8 / 状态变化规则v1 §5.0 "模型的选择范围": the program sets the legal range, the event grade and the
hard caps; the model only picks the concrete change inside the range and says why. Here:

  numeric slot   the model gives |change| inside [band.low, band.high]; after = before ± change
  initial slot   the model gives the first value inside the field's range (an unknown field)
  fixed slot     no choice: the operator's content is written as is

One "values" call per phase, only when some slot needs a value. The decoding schema allows each
slot exactly once with its own bounds, so an out-of-band number cannot be decoded; the store's
validator checks the band again. If the call fails or a slot gets no valid value, those slots come
back as rejected (`no_value`) and are not written; the act goes on.

The view is need-to-know: the role's self-known profile, its state in words it can be aware of,
the records the slots rest on, this phase's product, and per slot only the construct's name, the
current value, the direction, the event grade, the band and the rule it follows.
"""
from ..interfaces import ModelCallRecord, ModelError, PromptView, RejectedChange, StateChange, Violation
from ..stage.evidence import EvidenceGate
from ..stage.model import plain
from ..stage.schemas import TEXT, _object, validate
from ..stage.stages import self_profile, state_text, visible_records

RECENT = 12


def _unvalued(slot, detail):
    return RejectedChange(slot, slot.group, Violation("hard", "no_value", detail))


def _change(slot, after, reason):
    return StateChange(slot.construct_id, slot.key, slot.field, slot.before, after, slot.evidence, slot.update_operator,
                       slot.decision_mechanisms, slot.stage, slot.band, slot.group, reason, grade=slot.grade)


def values_schema(slots):
    """Each slot that needs a value exactly once, with its own bounds."""
    branches = []
    for s in slots:
        if s.band is not None:
            bound = {"type": "number", "minimum": s.band.low, "maximum": s.band.high}
            branches.append(_object({"slot": {"const": s.slot_id}, "change": bound, "reason": TEXT}))
        else:
            low, high = s.value_range
            branches.append(_object({"slot": {"const": s.slot_id}, "value": {"type": "number", "minimum": low, "maximum": high},
                                     "reason": TEXT}))
    return _object({"values": {"type": "array", "minItems": len(slots), "maxItems": len(slots),
                               "items": {"anyOf": branches}}})


class ModelValueChooser:
    """IValueChooser over IModel."""

    def __init__(self, registry, model, trace=None, recent=RECENT):
        self.registry = registry
        self.model = model
        self.trace = trace
        self.recent = recent
        self.gate = EvidenceGate(k=0)

    def describe(self, slot):
        construct = self.registry.construct(slot.construct_id)
        item = {"slot": slot.slot_id, "状态": construct.name + (f"（{slot.key}）" if slot.key else ""),
                "字段": slot.field, "依据": slot.basis}
        if slot.band is not None:
            item.update(现值=slot.before, 方向="上升" if slot.direction > 0 else "下降", 事件等级=slot.grade,
                        变化幅度范围=[slot.band.low, slot.band.high], 档位=slot.band.name)
        else:
            item.update(现值="未知（首次形成）", 取值范围=list(slot.value_range), 事件等级=slot.grade)
        return item

    def view(self, ctx, output, slots):
        evidence = self.gate.build(ctx)
        cited = {e.event for s in slots for e in s.evidence}
        records = [r for r in visible_records(ctx, evidence, self.recent)
                   if f"{r['action_id']}#{r['order_index']}" in cited] or list(visible_records(ctx, evidence, self.recent))
        task = {"slots": [self.describe(s) for s in slots],
                "要求": "每个 slot 给一个值：有变化幅度范围的写 change（变化量的绝对值，必须在范围内，方向已定），"
                        "首次形成的写 value（必须在取值范围内）；reason 用一句话说明为什么取这个值。"}
        return PromptView(self_profile(ctx.stable, self.registry), tuple(records),
                          state_text(ctx.state, self.registry, (), ctx.mode != "profile_only"),
                          {"product": plain(output.product)}, ctx.key[1], dict(ctx.present), task, dict(ctx.background))

    def choose(self, ctx, output, slots):
        fixed = [_change(s, s.content, s.basis) for s in slots if not s.needs_value]
        need = [s for s in slots if s.needs_value]
        if not need:
            return fixed, []
        schema = values_schema(need)
        view = self.view(ctx, output, need)
        try:
            result = self.model.complete("values", view, schema)
        except ModelError as exc:
            if self.trace is not None and exc.raw is not None:
                self.trace.model_call(ctx.key, ModelCallRecord("values", view, exc.raw, exc.reasoning))
            return fixed, [_unvalued(s, f"values call failed: {exc}") for s in need]
        if self.trace is not None:
            self.trace.model_call(ctx.key, ModelCallRecord("values", view, result.raw, result.reasoning))
        errors = validate(schema, result.output)
        if errors:
            return fixed, [_unvalued(s, f"values break the schema: {'; '.join(errors[:4])}") for s in need]
        given = {}
        for item in result.output["values"]:
            given.setdefault(item["slot"], item)
        changes, unvalued = list(fixed), []
        for s in need:
            item = given.get(s.slot_id)
            if item is None:
                unvalued.append(_unvalued(s, "no value given"))
            elif s.band is not None:
                changes.append(_change(s, round(s.before + s.direction * item["change"], 6), item["reason"]))
            else:
                changes.append(_change(s, item["value"], item["reason"]))
        return changes, unvalued
