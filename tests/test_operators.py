"""C8 (plan/10 §7): update operators turn judgments into slots; the chooser picks values inside bands.

The model never names a construct or a number for state: these tests drive the operators with
judgments and products only, and check that every numeric slot has its band before any value
exists, that the chooser stays inside it, and what is skipped and why.
"""
import json
import unittest

from rolesim import interfaces as I
from rolesim.role.registry import Registry
from rolesim.stage.model import MockModel
from rolesim.state import slots
from rolesim.state.chooser import ModelValueChooser, values_schema
from rolesim.state.store import ConstructStore

REG = Registry.load()
KEY = ("i1", "c1")
NOBODY = I.StableProfile("snap1", "c1", {})
PRESENT = {"c1": {"name": "林澄"}, "c2": {"name": "周越"}}
RECORD = {"action_id": "a7", "turn_index": 3, "order_index": 0, "actor": {"actor_id": "c2", "name": "周越"},
          "kind": "speech", "content": "今天必须给我全部数据。", "recipients": {"c1": {"name": "林澄"}}}
W = I.EvidenceRef("a7#0", "witnessed")
EV = I.EvidenceSet((W,))
APPRAISAL, BELIEF, TRUST = "dm.affect.primary_secondary_appraisal", "dm.cog.belief_updating", "dm.social.trust_reputation_update"
BASE = {"d.affect.emotion": {"label": [], "valence": 0.0, "arousal": 0.2, "intensity": 0.1},
        "d.affect.stress": {"level": 0.0},
        "d.cognition.belief": {"b1": {"belief_id": "b1", "proposition": "说明本周一会到", "kind": "fact", "about": ["c2"],
                                      "confidence": 0.6, "adopted": True, "isolated": False, "evidence": []}},
        "d.cognition.tom_level": {"ceiling": 2, "current": 2, "per_object": {}, "allowed_tasks": []},
        "d.policy.action_commitment": {"c-old": {"commitment_id": "c-old", "content": "收到说明后三天内交付",
                                                 "to": "c2", "formed_at": "pre", "stage": "形成", "public": True}}}


def entry(values=BASE):
    st = slots.blank_state(REG, "c1", "snap1")
    dynamic, memory, status = dict(st.dynamic), dict(st.memory), dict(st.status)
    for cid, value in values.items():
        (memory if cid.startswith("m.") else dynamic)[cid] = value
        status[cid] = "active"
    return I.AgentState(st.state_id, None, "snap1", dynamic, memory, st.versions, status)


def judgment(dm, option, output, hands=None):
    hands = hands or REG.mechanism(dm).hands_to
    return I.Judgment(dm, option, output, (W,), tuple(hands))


def interpretation(hypotheses=()):
    return I.SituationInterpretation("被催交付", (I.Appraisal("按期交付", "threat"),), {},
                                     tuple(hypotheses) or (I.Hypothesis("他在施压", .5, ("a7#0",), (), "看他是否给期限"),),
                                     "settled", (), {})


def plan(formed=True, audience=("c2",)):
    return I.ActionPlan((I.ActionCandidate("先交付前两批", True),), "先交付前两批", (), (), (), (),
                        I.Commitment(formed, "明天上午给出确切日期" if formed else None),
                        I.Expression("说明情况", tuple(audience), "平静"))


def evolution(rulings=()):
    return I.EvolutionResult("他没再追问", tuple(rulings), (), "先交付缓和了局面", ())


class Case(unittest.TestCase):
    def setUp(self):
        self.store = ConstructStore(REG)
        self.store.open(KEY, entry())
        self.n = 0

    def ctx(self, kind, stable=NOBODY):
        self.n += 1
        return I.StageContext(I.Phase("a7", self.n, kind), self.store.snapshot(KEY), stable, (RECORD,), {}, PRESENT, KEY)

    def draft(self, kind, product, *judgments, stable=NOBODY):
        ctx = self.ctx(kind, stable)
        output = I.StageOutput(product, tuple(judgments), EV)
        slots_, skipped = self.store.draft(ctx, output)
        return ctx, output, slots_, skipped

    def run_phase(self, kind, product, *judgments, model=None):
        ctx, output, slots_, skipped = self.draft(kind, product, *judgments)
        changes, unvalued = ModelValueChooser(REG, model or MockModel(REG)).choose(ctx, output, slots_)
        return self.store.commit(ctx, changes, EV, skipped, unvalued)

    @staticmethod
    def by(slots_, cid, field=None):
        return [s for s in slots_ if s.construct_id == cid and s.field == field]


class Emotion(Case):
    def test_appraisal_gives_banded_slots_before_any_value(self):
        j = judgment(APPRAISAL, "威胁", {"grade": 3, "emotions": ["恐惧"], "valence": "negative", "about": "c2"})
        _, _, s, _ = self.draft("event", interpretation(), j)
        (intensity,) = self.by(s, "d.affect.emotion", "intensity")
        self.assertEqual((intensity.direction, intensity.grade, intensity.band.name, intensity.band.low, intensity.band.high),
                         (1, 3, "medium", .25, .45))
        self.assertTrue(intensity.needs_value)
        (valence,) = self.by(s, "d.affect.emotion", "valence")
        self.assertEqual((valence.direction, valence.band.low, valence.band.high), (-1, .5, .9))   # 效价 ×2
        (label,) = self.by(s, "d.affect.emotion", "label")
        self.assertEqual((label.content, label.needs_value), (["恐惧"], False))
        self.assertTrue(self.by(s, "d.affect.stress", "level"))                    # 威胁 -> stress
        self.assertTrue(all(x.evidence == (W,) and x.decision_mechanisms == (APPRAISAL,) for x in s))

    def test_an_unknown_signed_value_starts_on_the_judged_side(self):
        self.store = ConstructStore(REG)
        self.store.open(KEY, entry({**BASE, "d.affect.emotion": {"label": [], "intensity": 0.0}}))
        j = judgment(APPRAISAL, "威胁", {"grade": 2, "emotions": [], "valence": "negative", "about": None})
        _, _, s, _ = self.draft("event", interpretation(), j)
        (valence,) = self.by(s, "d.affect.emotion", "valence")
        self.assertEqual((valence.before, valence.value_range), (None, (-1.0, 0.0)))
        (arousal,) = self.by(s, "d.affect.emotion", "arousal")
        self.assertEqual(arousal.value_range, (0.0, 1.0))

    def test_irrelevant_or_positive_events(self):
        _, _, s, _ = self.draft("event", interpretation(), judgment(APPRAISAL, "无关", {"grade": 1, "emotions": [],
                                                                                       "valence": "neutral", "about": None}))
        self.assertEqual(s, [])
        _, _, s, _ = self.draft("event", interpretation(), judgment(APPRAISAL, "收益", {"grade": 1, "emotions": ["喜悦"],
                                                                                       "valence": "positive", "about": None}))
        self.assertEqual(self.by(s, "d.affect.emotion", "valence")[0].direction, 1)
        self.assertEqual(self.by(s, "d.affect.stress", "level"), [])

    def test_personality_moves_the_band_before_the_model_sees_it(self):
        anxious = I.StableProfile("snap1", "c1", {"s.traits.personality.p1_big_five": I.StableModule(
            "s.traits.personality.p1_big_five", {"neuroticism.anxiety": 2.0}, "active", "explicit", .8)})
        j = judgment(APPRAISAL, "威胁", {"grade": 2, "emotions": ["恐惧"], "valence": "negative", "about": None})
        _, _, s, _ = self.draft("event", interpretation(), j, stable=anxious)
        self.assertEqual(self.by(s, "d.affect.stress", "level")[0].band.name, "medium")   # small -> medium (§七)

    def test_no_room_left_in_the_field(self):
        self.store = ConstructStore(REG)
        self.store.open(KEY, entry({**BASE, "d.affect.stress": {"level": 1.0}}))
        j = judgment(APPRAISAL, "威胁", {"grade": 2, "emotions": [], "valence": "negative", "about": None})
        _, _, s, skipped = self.draft("event", interpretation(), j)
        self.assertEqual(self.by(s, "d.affect.stress", "level"), [])
        self.assertIn("no_room", {x.reason for x in skipped})


class Beliefs(Case):
    def test_new_strengthened_unknown_and_suspended(self):
        j = judgment(BELIEF, "证据加权", {"beliefs": [
            {"belief_id": None, "proposition": "他今天必须拿到数据", "kind": "interpretation", "about": ["c2"],
             "change": "新形成", "confidence": .4, "grade": 1},
            {"belief_id": "b1", "proposition": "说明本周一会到", "kind": "fact", "about": ["c2"], "change": "减弱",
             "confidence": .5, "grade": 2},
            {"belief_id": "b9", "proposition": "x", "kind": "fact", "about": [], "change": "增强", "confidence": .5, "grade": 1},
            {"belief_id": None, "proposition": "y", "kind": "fact", "about": [], "change": "悬置", "confidence": .5, "grade": 1}]})
        _, _, s, skipped = self.draft("event", interpretation(), j)
        new = [x for x in s if x.construct_id == "d.cognition.belief" and x.field is None]
        self.assertEqual(len(new), 1)
        self.assertEqual((new[0].content["proposition"], new[0].content["evidence"]), ("他今天必须拿到数据", ["a7#0"]))
        (weaker,) = self.by(s, "d.cognition.belief", "confidence")
        self.assertEqual((weaker.key, weaker.direction, weaker.before, weaker.band.name), ("b1", -1, .6, "small"))
        self.assertEqual({x.reason for x in skipped} & {"unknown_belief", "suspended"}, {"unknown_belief", "suspended"})

    def test_hypotheses_about_someone_present_become_inferences(self):
        hyps = (I.Hypothesis("他在施压", .5, ("a7#0",), (), "看期限", "c2", "intention"),
                I.Hypothesis("情境紧", .4, ("a7#0",), (), "看别人", None, None))
        result = self.run_phase("event", interpretation(hyps))
        written = [c for c in result.accepted if c.construct_id == "d.cognition.other_model"]
        self.assertEqual(len(written), 1)
        self.assertEqual((written[0].key.split("/")[0], written[0].after["order"], written[0].after["kind"]),
                         ("c2", 1, "intention"))


class Relations(Case):
    def test_trust_only_in_s3_and_unknown_gets_a_first_value(self):
        j = judgment(TRUST, "谨慎观察", {"about": "c2", "dimension": "可靠性", "direction": "down", "grade": 2})
        _, _, s, _ = self.draft("event", interpretation(), j)
        self.assertEqual(self.by(s, "d.relation.trust", "value"), [])               # relation_update is S3 only
        _, _, s, _ = self.draft("feedback", evolution(), j)
        (first,) = self.by(s, "d.relation.trust", "value")
        self.assertEqual((first.before, first.band, first.value_range), (None, None, (0.0, 1.0)))   # unknown is not 0
        result = self.run_phase("feedback", evolution(), j)
        self.assertEqual(slots.get(self.store.snapshot(KEY), "d.relation.trust", "c2", "value"), .5)
        later = self.run_phase("feedback", evolution(), j)
        (c,) = [c for c in later.accepted if c.construct_id == "d.relation.trust"]
        self.assertEqual((c.band.name, c.before, c.after < c.before), ("small", .5, True))
        self.assertEqual(result.rejected, ())

    def test_someone_not_present_is_skipped(self):
        j = judgment(TRUST, "谨慎观察", {"about": "c9", "dimension": "可靠性", "direction": "down", "grade": 2})
        _, _, s, skipped = self.draft("feedback", evolution(), j)
        self.assertEqual((self.by(s, "d.relation.trust", "value"), [x.reason for x in skipped]), ([], ["no_partner"]))


class Plans(Case):
    def test_commitment_and_strategy_from_the_plan(self):
        j = judgment("dm.conflict.conflict_mode", "合作", {})
        result = self.run_phase("plan", plan(), j)
        written = {c.construct_id: c for c in result.accepted}
        commitment = written["d.policy.action_commitment"].after
        self.assertEqual((commitment["content"], commitment["stage"], commitment["to"], commitment["public"]),
                         ("明天上午给出确切日期", "形成", "c2", True))
        self.assertEqual((written["d.policy.current_strategy"].key, written["d.policy.current_strategy"].after),
                         ("dm.conflict.conflict_mode/c2", "合作"))
        self.assertTrue(all(c.band is None for c in result.accepted))               # nothing numeric in a plan here

    def test_rulings_move_a_held_commitment(self):
        result = self.run_phase("feedback", evolution((I.CommitmentRuling("收到说明后三天内交付", "broken"),
                                                       I.CommitmentRuling("不存在的承诺", "kept"))))
        stage = [c for c in result.accepted if c.construct_id == "d.policy.action_commitment"]
        self.assertEqual((stage[0].key, stage[0].field, stage[0].after), ("c-old", "stage", "违约"))
        self.assertIn("unknown_commitment", {x.reason for x in result.skipped})
        self.assertTrue(any(c.construct_id == "m.entry" for c in result.accepted))   # memory of the feedback


class Bookkeeping(Case):
    def test_unimplemented_operators_say_so(self):
        j = judgment(APPRAISAL, "威胁", {"grade": 1, "emotions": [], "valence": "negative", "about": None})
        _, _, _, skipped = self.draft("event", interpretation(), j)
        self.assertIn(("self_state_update", "not_implemented"), {(x.update_operator, x.reason) for x in skipped})

    def test_same_address_merges_or_is_dropped(self):
        up = judgment(TRUST, "谨慎观察", {"about": "c2", "dimension": "可靠性", "direction": "down", "grade": 1})
        up3 = judgment("dm.coop.trust_building", "谨慎验证", {"about": "c2", "dimension": "能力", "direction": "down", "grade": 3})
        _, _, s, _ = self.draft("feedback", evolution(), up, up3)
        self.assertEqual([x.grade for x in self.by(s, "d.relation.trust", "value")], [3])
        other = judgment("dm.coop.trust_building", "谨慎验证", {"about": "c2", "dimension": "能力", "direction": "up", "grade": 1})
        _, _, s, skipped = self.draft("feedback", evolution(), up, other)
        self.assertEqual(self.by(s, "d.relation.trust", "value"), [])
        self.assertIn("conflict_unresolved", {x.reason for x in skipped})


class Scripted(MockModel):
    """Answers the values call with given changes."""
    def __init__(self, values=None, error=None):
        super().__init__(REG)
        self.values, self.error, self.views = values, error, []

    def complete(self, stage, view, schema):
        if stage != "values":
            return super().complete(stage, view, schema)
        self.views.append((view, schema))
        if self.error:
            raise self.error
        out = {"values": [{"slot": s["slot"], "change": self.values, "reason": "测试"} for s in view.task["slots"]]}
        return I.ModelResult(out, json.dumps(out, ensure_ascii=False))


class Chooser(Case):
    J = judgment(APPRAISAL, "威胁", {"grade": 2, "emotions": ["恐惧"], "valence": "negative", "about": "c2"})

    def test_bounds_go_to_the_model_and_the_decoder(self):
        model = Scripted(values=.2)
        self.run_phase("event", interpretation(), self.J, model=model)
        view, schema = model.views[0]
        described = {s["字段"]: s for s in view.task["slots"]}
        self.assertEqual((described["intensity"]["变化幅度范围"], described["intensity"]["方向"]), ([.1, .25], "上升"))
        self.assertNotIn("d.affect", json.dumps(view.task, ensure_ascii=False))    # names, not construct ids
        branch = schema["properties"]["values"]["items"]["anyOf"][0]["properties"]
        self.assertEqual((branch["change"]["minimum"], branch["change"]["maximum"]), (.1, .25))
        self.assertEqual(schema["properties"]["values"]["minItems"], len(view.task["slots"]))

    def test_out_of_band_is_refused_never_clipped(self):
        result = self.run_phase("event", interpretation(), self.J, model=Scripted(values=.6))
        self.assertIn("no_value", [r.error.code for r in result.rejected])
        self.assertEqual(slots.get(self.store.snapshot(KEY), "d.affect.emotion", None, "intensity"), .1)

    def test_a_failed_call_writes_nothing_numeric_and_the_act_goes_on(self):
        hyps = (I.Hypothesis("他在施压", .5, ("a7#0",), (), "看期限", "c2", "intention"),)
        result = self.run_phase("event", interpretation(hyps), self.J, model=Scripted(error=I.ModelError("timeout")))
        codes = [r.error.code for r in result.rejected]
        self.assertIn("no_value", codes)
        self.assertNotIn("d.affect.emotion", [c.construct_id for c in result.accepted])   # its group fails as one
        self.assertTrue(any(c.construct_id == "d.cognition.other_model" for c in result.accepted))   # other groups commit

    def test_no_call_when_nothing_needs_a_value(self):
        model = Scripted(values=.2)
        self.run_phase("plan", plan(), judgment("dm.conflict.conflict_mode", "合作", {}), model=model)
        self.assertEqual(model.views, [])

    def test_values_schema_shapes(self):
        slot = I.ChangeSlot("x:1.0", "d.relation.trust", "c2", "value", None, 0, 2, None, (W,), "relation_update", (), "S3",
                            "affect_self_relation_group", "规则 §5.5", value_range=(0.0, 1.0))
        branch = values_schema([slot])["properties"]["values"]["items"]["anyOf"][0]["properties"]
        self.assertEqual((branch["value"]["minimum"], branch["value"]["maximum"]), (0.0, 1.0))
        self.assertNotIn("change", branch)


if __name__ == "__main__":
    unittest.main()
