"""IValidator (plan/10 §4.3): hard errors and soft anomalies for one drafted change.

状态变化规则v1 §八. Hard (the change's dependency group is rejected):
  unregistered_construct · writes_stable · not_in_whitelist · stage_not_allowed · wrong_group
  bad_address · unwritable_status (disabled / not_modeled / not_applicable) · not_modeled_field
  bad_value / bad_field / out_of_range (no clipping) · evidence_not_visible · stale_before
  deletes_record / rewrites_fact (append-only records, fixed facts) · no_reason · bad_grade
  unregistered_mechanism · unregistered_option (strategy values come from registered options)
  banded_in_instance_write · no_band · over_band / under_band (outside the event-grade band)
  decay_wrong_direction ("朝向零" rules) · tom_ceiling_fixed · tom_out_of_bounds
  tom_order_exceeded · objective_power_write
Soft (written and flagged):
  extended_range (grade 4, beyond "large" but within the extended range)
  unregistered_handoff (the decision process does not hand to this operator in the registry)
Store-level soft checks that need history or several changes live in store.py.

Not checkable here: "他人模型或信念中出现只能来自对方真实隐状态的内容" needs the other role's
hidden state, which this side never holds; the visible-evidence rule is its structural proxy.
"""
import math
from typing import Mapping

from ..interfaces import UNWRITABLE, Violation
from .slots import OPTION_SLOTS, AddressError, band_scale, check_instance, check_kind, get, kind_of, resolve

EPS = 1e-9


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _flat(value, prefix=""):
    """(dotted path, leaf) pairs of a nested record."""
    if isinstance(value, Mapping) and value:
        for k, v in value.items():
            yield from _flat(v, f"{prefix}{k}.")
    else:
        yield prefix[:-1], value


class Validator:
    """IValidator over the registry (permissions, options) and the state layout (slots)."""

    def __init__(self, registry):
        self.registry = registry

    def check(self, change, snapshot, evidence):
        out = []

        def hard(code, detail=""):
            out.append(Violation("hard", code, detail))

        cid = change.construct_id
        try:
            construct = self.registry.construct(cid)
        except KeyError:
            return [Violation("hard", "unregistered_construct", cid)]
        if construct.layer == "stable":
            return [Violation("hard", "writes_stable", cid)]
        rule = self.registry.operator(change.update_operator)
        if cid not in rule.writes:
            hard("not_in_whitelist", f"{rule.name} may not write {cid}")
        if change.stage not in rule.stages:
            hard("stage_not_allowed", f"{rule.name} may not be called in {change.stage}")
        if change.group != rule.group:
            hard("wrong_group", f"{change.group} != {rule.group}")
        if not change.reason or not change.reason.strip():
            hard("no_reason")
        if change.grade is not None and change.grade not in (1, 2, 3, 4):
            hard("bad_grade", repr(change.grade))
        for dm_id in change.decision_mechanisms:
            try:
                mechanism = self.registry.mechanism(dm_id)
            except KeyError:
                hard("unregistered_mechanism", dm_id)
                continue
            if rule.name not in mechanism.hands_to:
                out.append(Violation("soft", "unregistered_handoff", f"{dm_id} -> {rule.name}"))

        visible = {e.event for e in evidence.included} - {x.event for x in evidence.excluded}
        unseen = [e.event for e in change.evidence if e.event not in visible]
        if unseen:
            hard("evidence_not_visible", ", ".join(unseen))

        try:
            slot, key_parts, field_parts = resolve(cid, change.key, change.field)
        except AddressError as exc:
            hard("bad_address", str(exc))
            return out
        status = snapshot.status.get(f"{cid}/{change.key}") or snapshot.status.get(cid)
        if status in UNWRITABLE:
            hard("unwritable_status", f"{cid} is {status}")
        if field_parts and field_parts[0] in slot.not_modeled:
            hard("not_modeled_field", change.field)
            return out

        options = self._options(cid, key_parts)
        kind = kind_of(slot, change.field)
        if change.field is None or kind == "record":
            prefix = "" if change.field is None else f"{change.field}."
            problem = check_instance(slot, change.after, options, prefix) if change.after is not None else None
        else:
            problem = check_kind(kind, change.after, options)
        if problem:
            hard(*problem)
        if cid == "d.policy.strategy_weight":
            self._weight_options(change, options, hard)

        before = get(snapshot, cid, change.key, change.field)
        if before != change.before:
            hard("stale_before", f"snapshot has {before!r}")
        self._records(slot, change, hard)
        self._magnitude(slot, change, out, hard)
        if change.update_operator == "decay_or_recovery":
            self._decay(construct, change, hard)
        if cid == "d.cognition.tom_level":
            self._tom(change, snapshot, hard)
        if cid == "d.cognition.other_model":
            self._order(change, key_parts, snapshot, hard)
        if cid == "d.relation.power_asymmetry" and not (change.field or "").startswith("subjective"):
            hard("objective_power_write", "relation_update writes only the subjective part")
        return out

    # -- pieces ------------------------------------------------------------------------------
    def _options(self, cid, key_parts):
        if cid not in OPTION_SLOTS or not key_parts:
            return None
        try:
            return set(self.registry.mechanism(key_parts[0]).options)
        except KeyError:
            return None

    @staticmethod
    def _weight_options(change, options, hard):
        names = [change.field] if change.field else list(change.after or {})
        if options is None:
            hard("unregistered_mechanism", change.key)
        elif any(n not in options for n in names):
            hard("unregistered_option", ", ".join(n for n in names if n not in options))

    @staticmethod
    def _records(slot, change, hard):
        if not slot.append_only and not slot.fixed:
            return
        before, after = change.before, change.after
        if slot.append_only and change.field is None and before is not None and after is None:
            hard("deletes_record", "append-only records are never removed")
        if slot.fields.get("") == "list" and isinstance(before, (list, tuple)):
            if not isinstance(after, (list, tuple)) or list(after[:len(before)]) != list(before):
                hard("deletes_record", "this list only grows")
        if change.field is None and isinstance(before, Mapping) and isinstance(after, Mapping):
            changed = [f for f in slot.fixed if f in before and after.get(f) != before[f]]
            if changed:
                hard("rewrites_fact", ", ".join(changed))
        elif change.field in slot.fixed and before is not None and after != before:
            hard("rewrites_fact", change.field)

    @staticmethod
    def _magnitude(slot, change, out, hard):
        if change.update_operator == "decay_or_recovery" or change.before is None:
            return
        if isinstance(change.before, Mapping) and isinstance(change.after, Mapping):
            prefix = "" if change.field is None else f"{change.field}."
            before, after = dict(_flat(change.before, prefix)), dict(_flat(change.after, prefix))
            moved = [p for p in before if band_scale(slot, p) is not None and after.get(p) != before[p]]
            if moved:
                hard("banded_in_instance_write", f"write banded fields one at a time: {', '.join(moved)}")
            return
        if band_scale(slot, change.field) is None:
            return
        if not (_number(change.before) and _number(change.after)):
            return
        delta = abs(change.after - change.before)
        band = change.band
        if band is None:
            hard("no_band", "a numeric change needs an event grade")
        elif delta > band.high + EPS:
            if band.extended is not None and delta <= band.extended + EPS:
                out.append(Violation("soft", "extended_range", f"|Δ|={delta:.3f} beyond {band.high} (grade 4)"))
            else:
                hard("over_band", f"|Δ|={delta:.3f} above {band.name} [{band.low}, {band.high}]")
        elif delta < band.low - EPS:
            hard("under_band", f"|Δ|={delta:.3f} below {band.name} [{band.low}, {band.high}]")

    @staticmethod
    def _decay(construct, change, hard):
        if not construct.decay or "朝向零" not in construct.decay.split("：")[0]:
            return
        before, after = change.before, change.after
        if _number(before) and _number(after) and (abs(after) > abs(before) + EPS or after * before < 0):
            hard("decay_wrong_direction", f"{before} -> {after} does not move toward zero")

    @staticmethod
    def _tom(change, snapshot, hard):
        current = get(snapshot, "d.cognition.tom_level", None, None) or {}
        ceiling = current.get("ceiling")
        if change.field == "ceiling" or (change.field is None and ceiling is not None and isinstance(change.after, Mapping)
                                         and change.after.get("ceiling") != ceiling):
            hard("tom_ceiling_fixed", "the ceiling is set at entry and does not change in a trajectory")
            return
        if ceiling is None:
            return
        floor = 0 if ceiling == 0 else max(1, ceiling - 1)
        values = ([change.after] if change.field in ("current",) or (change.field or "").startswith("per_object.")
                  else [change.after.get("current")] + list((change.after.get("per_object") or {}).values())
                  if change.field is None and isinstance(change.after, Mapping) else [])
        bad = [v for v in values if isinstance(v, int) and not floor <= v <= ceiling]
        if bad:
            hard("tom_out_of_bounds", f"{bad} outside [{floor}, {ceiling}]")

    @staticmethod
    def _order(change, key_parts, snapshot, hard):
        order = change.after if change.field == "order" else (
            change.after.get("order") if change.field is None and isinstance(change.after, Mapping) else None)
        if not isinstance(order, int):
            return
        tom = get(snapshot, "d.cognition.tom_level", None, None) or {}
        level = (tom.get("per_object") or {}).get(key_parts[0], tom.get("current"))
        if isinstance(level, int) and order > level:
            hard("tom_order_exceeded", f"order {order} > available level {level} for {key_parts[0]}")
