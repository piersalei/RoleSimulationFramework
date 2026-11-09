"""IUpdateOperator (plan/10 §4.1, §7): the 11 update operators turn judgments into ChangeSlots.

Design constraint C8 (状态变化规则v1 §二, §5.0; 事件建模skill §3.9): decision processes only judge;
an update operator decides from its rule card whether to write, where, in which direction and at
which event grade, and IMagnitude gives the legal band before any value is chosen. The model never
names a construct, an address or a number here; IValueChooser later picks a value inside each
band. What an operator reads:

  judgments   the typed outputs of the mechanism-card processes (schemas.JUDGMENT_OUTPUTS), only
              those whose registry `hands_to` includes this operator
  product     the stage product (S1 hypotheses, S2 commitment and audience, S3 rulings)

Implemented in this version (plan/10 §7.5), each from its rule card:

  emotion_update      S1 appraisal (dm.affect.primary_secondary_appraisal): option 无关 or no
                      output -> nothing. Otherwise intensity and arousal up at the appraised grade
                      (§5.1 "强度和唤醒按事件等级取档"), valence up / down for a positive / negative
                      appraisal, labels written as given, stress up for 损失 / 威胁.
  belief_revision     belief_updating candidates: a new proposition -> a new belief record; 增强 /
                      减弱 of a held belief -> its confidence up / down at the candidate's grade;
                      悬置 -> no write (logged). attribution_responsibility -> an attribution record.
  other_model_update  S1 hypotheses that name whom they are about -> an inference record, order 1.
  relation_update     S3 trust judgments -> trust towards that character up / down at the grade;
                      an unknown trust gets a first value instead (unknown is not 0).
  commitment_update   S2 commitment formed -> a commitment record (stage 形成); S3 rulings on a held
                      commitment -> its stage (kept 兑现 / broken 违约 / released 撤销).
  strategy_learning   S2 judgments of processes handed to it, with an option -> the current
                      strategy for that process towards the plan's audience.
  memory_update       S3 feedback -> a memory entry (what was expected vs what happened).

Not yet (logged as Skipped whenever a judgment is handed to them): self_state_update,
group_state_update, goal_update, decay_or_recovery (needs time events, 规则 §3.5), relationship
repair (needs harm events), belief_revision's attention entries.
"""
import math
import re

from ..interfaces import STAGE_OF_KIND, Band, ChangeSlot, Skipped
from .slots import RANGES, SLOTS, get, kind_of

APPRAISAL = "dm.affect.primary_secondary_appraisal"
BELIEF = "dm.cog.belief_updating"
ATTRIBUTION = "dm.cog.attribution_responsibility"
TRUST = ("dm.social.trust_reputation_update", "dm.coop.trust_building")
REPAIR = "dm.conflict.relationship_repair"
NEGATIVE_PRIMARY = ("损失", "威胁")
RULING_STAGE = {"kept": "兑现", "broken": "违约", "released": "撤销"}
NOT_YET = ("self_state_update", "group_state_update", "goal_update", "decay_or_recovery")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _safe(text):
    return re.sub(r"[^0-9A-Za-z_.:-]+", "-", str(text)).strip("-") or "x"


class Operator:
    """One registered update operator; the rule it follows is RULES[name]."""

    def __init__(self, rule, registry, magnitude):
        self.name = rule.name
        self.rule = rule
        self.registry = registry
        self.magnitude = magnitude

    # -- helpers for the rules --------------------------------------------------------------
    def handed(self, output, dm_ids=None):
        """Judgments handed to this operator (registry hands_to), optionally of given processes."""
        return [j for j in output.judgments if self.name in j.hands_to and (dm_ids is None or j.dm_id in dm_ids)]

    @staticmethod
    def evidence(output, ids=()):
        """EvidenceRefs for record ids the stage could see; the phase's own records when none given."""
        refs = {e.event: e for e in output.evidence.included}
        picked = [refs[i] for i in dict.fromkeys(ids) if i in refs]
        return tuple(picked) if picked else ()

    @staticmethod
    def phase_evidence(ctx, output):
        """The records of this phase that the role could see, newest last."""
        refs = {e.event: e for e in output.evidence.included}
        ids = [f"{r['action_id']}#{r['order_index']}" for r in ctx.records]
        return tuple(refs[i] for i in ids if i in refs)

    def _slot(self, ctx, n, cid, key, field, evidence, mechanisms, basis, **kw):
        return ChangeSlot(f"{self.name}:{ctx.phase.phase_no}.{n}", cid, key, field, kw.pop("before", None),
                          kw.pop("direction", 0), kw.pop("grade", None), kw.pop("band", None), tuple(evidence),
                          self.name, tuple(mechanisms), STAGE_OF_KIND[ctx.phase.kind], self.rule.group, basis, **kw)

    def fixed(self, ctx, n, cid, key, field, content, evidence, mechanisms, basis):
        """A record or category written as the operator assembled it."""
        before = get(ctx.state, cid, key, field)
        return self._slot(ctx, n, cid, key, field, evidence, mechanisms, basis, before=before, content=content)

    def numeric(self, ctx, n, cid, key, field, direction, grade, evidence, mechanisms, basis):
        """A numeric change at an event grade: the band comes from IMagnitude now, before a value is
        chosen. An unknown field gets an initial slot over its range instead. Returns (slot, skip)."""
        slot_def = SLOTS[cid]
        kind = (kind_of(slot_def, field) or "").rstrip("?")
        before = get(ctx.state, cid, key, field)
        if before is None:
            if kind not in RANGES:
                return None, Skipped(self.name, "no_value_range", f"{cid}.{field}")
            low, high = RANGES[kind]
            if low < 0 < high and direction:
                # A signed field (valence, self-evaluation): the first value lies on the side the
                # judgment points to; a zero would contradict it.
                low, high = (0.0, high) if direction > 0 else (low, 0.0)
            return self._slot(ctx, n, cid, key, field, evidence, mechanisms, basis + "；原值未知，取首次值",
                              direction=direction, grade=grade, value_range=(low, high)), None
        if not _number(before):
            return None, Skipped(self.name, "not_numeric", f"{cid}.{field} = {before!r}")
        construct = self.registry.construct(cid)
        instance = get(ctx.state, cid, key, None)
        band = self.magnitude.range(construct, field, grade, direction, evidence, ctx.stable, instance)
        if band is None:
            return None, Skipped(self.name, "no_band", f"{cid}.{field}: tempo not classified")
        low, high = RANGES[kind]
        room = (high - before) if direction > 0 else (before - low)
        if room + 1e-9 < band.low:
            return None, Skipped(self.name, "no_room", f"{cid}.{field} = {before} has no room for a {band.name} change")
        if band.high > room:
            # The field's range ends inside the band: offer only what fits (values are never clipped).
            band = Band(band.name, band.low, round(room, 6), None)
        return self._slot(ctx, n, cid, key, field, evidence, mechanisms, basis, before=before, direction=direction,
                          grade=grade, band=band), None

    # -- IUpdateOperator --------------------------------------------------------------------
    def propose(self, ctx, output):
        if STAGE_OF_KIND[ctx.phase.kind] not in self.rule.stages:
            return [], []
        rule = RULES.get(self.name)
        if rule is None:
            handed = self.handed(output)
            return [], ([Skipped(self.name, "not_implemented", ", ".join(sorted({j.dm_id for j in handed})))]
                        if handed else [])
        return rule(self, ctx, output)


# -- the rules ---------------------------------------------------------------------------------
def emotion_update(op, ctx, output):
    slots, skipped = [], []
    for j in op.handed(output, (APPRAISAL,)):
        out = j.output or {}
        if j.option in (None, "无关") or not out.get("grade"):
            continue
        grade, ev, dm = out["grade"], j.evidence or op.phase_evidence(ctx, output), (j.dm_id,)
        if not ev:
            skipped.append(Skipped(op.name, "no_evidence", j.dm_id))
            continue
        basis = f"规则 §5.1：评价为「{j.option}」，{grade} 级事件"
        n = len(slots)
        if out.get("emotions"):
            slots.append(op.fixed(ctx, n, "d.affect.emotion", None, "label", list(out["emotions"]), ev, dm, basis + "；标签按评价写入"))
        wanted = [("d.affect.emotion", "intensity", +1), ("d.affect.emotion", "arousal", +1)]
        if out.get("valence") in ("positive", "negative"):
            wanted.append(("d.affect.emotion", "valence", +1 if out["valence"] == "positive" else -1))
        if j.option in NEGATIVE_PRIMARY:
            wanted.append(("d.affect.stress", "level", +1))
        for cid, field, direction in wanted:
            slot, skip = op.numeric(ctx, len(slots), cid, None, field, direction, grade, ev, dm, basis)
            slots += [slot] if slot else []
            skipped += [skip] if skip else []
        break                                   # one appraisal per phase writes the affect state
    return slots, skipped


def belief_revision(op, ctx, output):
    slots, skipped = [], []
    held = ctx.state.dynamic.get("d.cognition.belief") or {}
    for j in op.handed(output, (BELIEF,)):
        for i, cand in enumerate((j.output or {}).get("beliefs") or []):
            ev = j.evidence or op.phase_evidence(ctx, output)
            if not ev:
                skipped.append(Skipped(op.name, "no_evidence", cand.get("proposition", "")))
                continue
            change, bid = cand.get("change"), cand.get("belief_id")
            if change == "悬置":
                skipped.append(Skipped(op.name, "suspended", cand.get("proposition", "")))
                continue
            if change in ("增强", "减弱") and bid:
                if bid not in held:
                    skipped.append(Skipped(op.name, "unknown_belief", bid))
                    continue
                slot, skip = op.numeric(ctx, len(slots), "d.cognition.belief", bid, "confidence",
                                        +1 if change == "增强" else -1, cand["grade"], ev, (j.dm_id,),
                                        f"规则 §5.2：信念{change}，{cand['grade']} 级证据")
                slots += [slot] if slot else []
                skipped += [skip] if skip else []
                continue
            new_id = f"b-{_safe(ctx.phase.event_id)}-{i + 1}"
            record = {"belief_id": new_id, "proposition": cand["proposition"], "kind": cand["kind"],
                      "about": list(cand.get("about") or []), "context": None, "time": None,
                      "confidence": cand["confidence"], "conflict_group": None, "adopted": True, "isolated": False,
                      "superseded_by": None, "evidence": [e.event for e in ev]}
            slots.append(op.fixed(ctx, len(slots), "d.cognition.belief", new_id, None, record, ev, (j.dm_id,),
                                  "规则 §5.2：新命题首次形成，确信度从 unknown 转为有值"))
    for k, j in enumerate(op.handed(output, (ATTRIBUTION,))):
        out = j.output or {}
        ev = j.evidence or op.phase_evidence(ctx, output)
        if not out or not ev:
            continue
        aid = f"a-{_safe(ctx.phase.event_id)}-{k + 1}"
        record = {"event": out["event"], "about": out.get("about") or "情境", "locus": out.get("locus"),
                  "stability": out.get("stability"), "globality": out.get("globality"),
                  "controllability": out.get("controllability"), "intent": out.get("intent"),
                  "responsibility": {"self": out["self_share"], "others": out["others_share"]},
                  "confidence": out["confidence"]}
        slots.append(op.fixed(ctx, len(slots), "d.cognition.attribution", aid, None, record, ev, (j.dm_id,),
                              "规则 §5.2：归因过程形成判断，写入归因记录"))
    return slots, skipped


def other_model_update(op, ctx, output):
    slots, skipped = [], []
    product = output.product
    present = {p for p in ctx.present if p != ctx.key[1]}
    for i, h in enumerate(getattr(product, "hypotheses", ()) or ()):
        if not h.about or h.about not in present:
            continue
        ev = op.evidence(output, h.support) or op.phase_evidence(ctx, output)
        if not ev:
            skipped.append(Skipped(op.name, "no_evidence", h.statement))
            continue
        iid = f"i-{_safe(ctx.phase.event_id)}-{i + 1}"
        record = {"inference": h.statement, "kind": h.kind or "intention", "order": 1, "confidence": h.confidence,
                  "evidence": [e.event for e in ev], "hypothesis_ref": None, "superseded_by": None}
        slots.append(op.fixed(ctx, len(slots), "d.cognition.other_model", f"{h.about}/{iid}", None, record, ev, (),
                              "规则 §5.3：观察到对方言行，形成一阶推断（保留确信度与证据）"))
    return slots, skipped


def relation_update(op, ctx, output):
    slots, skipped = [], []
    present = {p for p in ctx.present if p != ctx.key[1]}
    for j in op.handed(output, TRUST):
        out = j.output or {}
        who = out.get("about")
        if who not in present or not out.get("grade"):
            skipped.append(Skipped(op.name, "no_partner", f"{j.dm_id}: {who!r}"))
            continue
        ev = j.evidence or op.phase_evidence(ctx, output)
        if not ev:
            skipped.append(Skipped(op.name, "no_evidence", j.dm_id))
            continue
        direction = +1 if out["direction"] == "up" else -1
        slot, skip = op.numeric(ctx, len(slots), "d.relation.trust", who, "value", direction, out["grade"], ev,
                                (j.dm_id,), f"规则 §5.5：{out['dimension']}方面的证据，{out['grade']} 级")
        slots += [slot] if slot else []
        skipped += [skip] if skip else []
    if op.handed(output, (REPAIR,)):
        skipped.append(Skipped(op.name, "not_implemented", f"{REPAIR}: 修复程度要写在伤害事件上，本版本还没有伤害事件"))
    return slots, skipped


def commitment_update(op, ctx, output):
    slots, skipped = [], []
    product = output.product
    ev = op.phase_evidence(ctx, output) or tuple(output.evidence.included[-1:])
    commitment = getattr(product, "commitment", None)
    if commitment is not None and commitment.formed and commitment.content:
        if not ev:
            return slots, [Skipped(op.name, "no_evidence", commitment.content)]
        audience = list(product.expression.audience)
        cid_ = f"c-{_safe(ctx.phase.event_id)}-{ctx.phase.phase_no}"
        record = {"commitment_id": cid_, "content": commitment.content, "to": "、".join(audience) or "在场的人",
                  "formed_at": ctx.phase.event_id, "deadline": None, "stage": "形成",
                  "public": not product.expression.private, "means": None, "verification": None}
        slots.append(op.fixed(ctx, 0, "d.policy.action_commitment", cid_, None, record, ev,
                              tuple(j.dm_id for j in op.handed(output)), "规则 §5.8：行动计划形成承诺"))
    held = ctx.state.dynamic.get("d.policy.action_commitment") or {}
    for ruling in getattr(product, "commitment_rulings", ()) or ():
        stage = RULING_STAGE.get(ruling.ruling)
        if stage is None:
            continue
        match = next((k for k, c in held.items() if ruling.commitment in (k, c.get("content"))), None)
        if match is None:
            skipped.append(Skipped(op.name, "unknown_commitment", ruling.commitment))
            continue
        if not ev:
            skipped.append(Skipped(op.name, "no_evidence", ruling.commitment))
            continue
        slots.append(op.fixed(ctx, len(slots), "d.policy.action_commitment", match, "stage", stage, ev, (),
                              f"规则 §5.8：反馈裁定承诺为 {ruling.ruling}"))
    return slots, skipped


def strategy_learning(op, ctx, output):
    slots, skipped = [], []
    product = output.product
    audience = list(getattr(getattr(product, "expression", None), "audience", ()) or ())
    context = audience[0] if len(audience) == 1 else "在场的人"
    for j in op.handed(output):
        if j.option is None or STAGE_OF_KIND[ctx.phase.kind] != "S2":
            continue
        ev = j.evidence or op.phase_evidence(ctx, output) or tuple(output.evidence.included[-1:])
        if not ev:
            skipped.append(Skipped(op.name, "no_evidence", j.dm_id))
            continue
        slots.append(op.fixed(ctx, len(slots), "d.policy.current_strategy", f"{j.dm_id}/{context}", None, j.option, ev,
                              (j.dm_id,), "规则 §5.9：本次计划所用的策略选项"))
    return slots, skipped


def memory_update(op, ctx, output):
    product = output.product
    if not hasattr(product, "expected_vs_actual"):
        return [], []
    ev = op.phase_evidence(ctx, output)
    if not ev:
        return [], [Skipped(op.name, "no_evidence", "feedback")]
    eid = f"m-{_safe(ctx.phase.event_id)}"
    involved = sorted({r["actor"]["actor_id"] for r in ctx.records if r["actor"]["actor_id"] != ctx.key[1]})
    record = {"entry_id": eid, "summary": product.expected_vs_actual[:400], "source_events": [e.event for e in ev],
              "time": None, "involved": involved, "channel": "亲见",
              "interpretations": [product.learning_attribution] if product.learning_attribution else []}
    return [op.fixed(ctx, 0, "m.entry", eid, None, record, ev, (), "规则 §5.10：对自己行动的反馈形成记忆条目")], []


RULES = {"emotion_update": emotion_update, "belief_revision": belief_revision,
         "other_model_update": other_model_update, "relation_update": relation_update,
         "commitment_update": commitment_update, "strategy_learning": strategy_learning,
         "memory_update": memory_update}


def operators(registry, magnitude):
    return {name: Operator(registry.operator(name), registry, magnitude) for name in registry.ids("operator")}
