"""IMagnitude (plan/10 §4.2): the legal band for one numeric change.

状态变化规则v1 §5.0 gives four bands per tempo class, one per event grade, plus an extended range
open only to grade-4 events; §七 lets personality sensitivity move the band by at most one level
and evidence quality cap it. Implemented here:

  base band ........ grade 1-4 -> tiny / small / medium / large of the construct's tempo
  field scale ...... valence x2, current self-evaluation x4 (slots.SLOTS banded scales)
  sensitivity ...... merged facet z (largest |z|, other same-sign facets at half); |z| >= 1.5
                     (nearest anchor +/-2) moves to the lower half of the next band up, or the
                     upper half of the next band down; never more than one level
  evidence cap ..... only hearsay -> at most medium; only inferred -> at most small
  grade 4 .......... `extended` = the top of the extended range (slow 0.85, fast: the field span)

Sensitivity reads P1 facets ("domain.facet": z, as in persona.FACETS), the attachment axes and
cognitive flexibility from the stable profile, for the unconditional mappings of §七 only. Not
yet: P2 / P3 profiles, conscientiousness after one's own breach (needs event classification),
self-esteem instability (needs the profile shape), and the peak / duration / recovery effects,
which place a value inside the band or act on decay rather than move the band.

All numbers are the psychology group's initial values awaiting calibration.
"""
from typing import Mapping

from ..interfaces import BANDS, Band, InterfaceError
from .slots import RANGES, SLOTS, band_scale, kind_of

SLOW = {"tiny": (0.0, .05), "small": (.05, .15), "medium": (.15, .30), "large": (.30, .45), "extended": (.45, .85)}
FAST = {"tiny": (0.0, .10), "small": (.10, .25), "medium": (.25, .45), "large": (.45, .70), "extended": (.70, None)}
GRADE_LEVEL = {1: "tiny", 2: "small", 3: "medium", 4: "large"}
SHIFT_AT = 1.5

# Phrases these tables rest on (状态变化规则v1); tests check them against the source.
BASIS = (
    "慢变构念（trust、closeness、dependence、commitment、确信度、羞耻负荷、动态目标承诺、可行性估计、策略权重等 0—1 构念）："
    "微小 ≤0.05，小 0.05—0.15，中 0.15—0.30，大 0.30—0.45",
    "快变构念（情绪强度与唤醒、压力、威胁与奖赏激活、价值与目标激活、身份与规范显著性、面子成本）："
    "微小 ≤0.10，小 0.10—0.25，中 0.25—0.45，大 0.45—0.70",
    "重大事件扩展区间只对四级事件开放：慢变构念 0.45—0.85，快变构念可到量程上限",
    "只有听说的证据最多取中档，只有推断的证据最多取小档",
    "敏感度：z = +2 时按高一级档位的下半区取值，z = -2 时按低一级档位的上半区取值，|z| ≤ 1 不改变档位",
    "多个分面作用于同一性质时，取影响最大的一项，其余同向影响折半计入，不连乘；人格调节合计最多移动一个档位",
)

P1 = "s.traits.personality.p1_big_five"
NEGATIVE = [(P1, "neuroticism.anxiety"), (P1, "neuroticism.depression"), (P1, "neuroticism.vulnerability")]
FLEXIBILITY = [("s.traits.cognitive.flexibility", None)]
ATTACHMENT_ANXIETY = [("s.traits.attachment.global", "anxiety")]
# (construct, field, direction 0 = either, condition on the instance after the change, facets)
SENSITIVITY = [
    ("d.affect.emotion", "intensity", +1, "negative", NEGATIVE),
    ("d.affect.emotion", "intensity", +1, "positive", [(P1, "extraversion.positive_emotions")]),
    ("d.affect.stress", "level", +1, None, NEGATIVE),
    ("d.cognition.shame", "load", +1, None, NEGATIVE),
    ("d.cognition.belief", "confidence", 0, None, FLEXIBILITY),
    ("d.policy.strategy_weight", "*", 0, None, FLEXIBILITY),
    *[(f"d.relation.{name}", "value", -1, None, ATTACHMENT_ANXIETY)
      for name in ("trust", "closeness", "dependence", "commitment")],
]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _facet(stable, construct_id, path):
    module = stable.get(construct_id) if stable is not None else None
    if module is None or module.status != "active" or module.value is None:
        return None
    value = module.value if path is None else (module.value.get(path) if isinstance(module.value, Mapping) else None)
    return float(value) if _number(value) else None


def _condition(condition, instance):
    if condition is None:
        return True
    valence = instance.get("valence") if isinstance(instance, Mapping) else None
    if not _number(valence):
        return False
    return valence < 0 if condition == "negative" else valence > 0


def evidence_cap(evidence):
    channels = {e.channel for e in evidence}
    if not channels or channels & {"witnessed", "own_action", "pre_entry"}:
        return None
    return "medium" if "hearsay" in channels else "small"


class Magnitude:
    """IMagnitude over the registry's tempo classes and the state layout's banded fields."""

    def __init__(self, registry):
        self.registry = registry

    def sensitivity(self, construct_id, field_name, direction, stable, instance=None):
        """Merged z of the facets that act on this change's sensitivity, or None."""
        values = []
        for cid, pattern, sign, condition, facets in SENSITIVITY:
            field_ok = pattern == field_name or (pattern == "*" and field_name is not None and "." not in field_name)
            if cid != construct_id or not field_ok or (sign and sign != direction) or not _condition(condition, instance):
                continue
            values += [z for z in (_facet(stable, c, p) for c, p in facets) if z is not None]
        if not values:
            return None
        top = max(values, key=abs)
        rest = [z for i, z in enumerate(values) if i != values.index(top) and z * top > 0]
        return top + 0.5 * sum(rest)

    def range(self, construct, field, grade, direction, evidence, stable, instance=None):
        slot = SLOTS.get(construct.construct_id)
        scale = band_scale(slot, field) if slot else None
        if scale is None or construct.tempo not in ("slow", "fast"):
            return None
        if grade not in GRADE_LEVEL:
            raise InterfaceError(f"{construct.construct_id}: event grade must be 1-4, got {grade!r}")
        table = SLOW if construct.tempo == "slow" else FAST
        low_, high_ = RANGES[kind_of(slot, field).rstrip("?")]
        span = (high_ - low_) / scale

        def bounds(level):
            low, high = table[level]
            return low, span if high is None else high

        level = GRADE_LEVEL[grade]
        low, high = bounds(level)
        z = self.sensitivity(construct.construct_id, field, direction, stable, instance)
        index = BANDS.index(level)
        if z is not None and z >= SHIFT_AT and index + 1 < len(BANDS):
            level = BANDS[index + 1]
            up_low, up_high = bounds(level)
            low, high = up_low, (up_low + up_high) / 2
        elif z is not None and z <= -SHIFT_AT and index > 0:
            level = BANDS[index - 1]
            down_low, down_high = bounds(level)
            low, high = (down_low + down_high) / 2, down_high
        cap = evidence_cap(evidence)
        if cap and BANDS.index(level) > BANDS.index(cap):
            level = cap
            low, high = bounds(cap)
        extended = bounds("extended")[1] if grade == 4 and level == "large" else None
        return Band(level, round(low * scale, 6), round(high * scale, 6),
                    None if extended is None else round(extended * scale, 6))
