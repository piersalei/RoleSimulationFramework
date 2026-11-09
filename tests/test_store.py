"""State writes (plan/10 §4): layout, IMagnitude, IValidator, IUpdateOperator, IStateStore."""
import dataclasses
import re
import unittest

from rolesim import interfaces as I
from rolesim.state import slots
from rolesim.state.magnitude import BASIS, FAST, SLOW, Magnitude
from rolesim.role.registry import FILES, SOURCE_DIR, Registry
from rolesim.state.store import ConstructStore

REG = Registry.load()
KEY = ("i1", "c1")
W, W2 = I.EvidenceRef("r1", "witnessed"), I.EvidenceRef("r2", "witnessed")
EV = I.EvidenceSet((W, W2), (I.Excluded("r9", "other_thought"),))
NOBODY = I.StableProfile("snap1", "c1", {})
BASE = {
    "d.relation.trust": {"c2": {"value": 0.6}}, "d.relation.closeness": {"c2": {"value": 0.6}},
    "d.relation.dependence": {"c2": {"value": 0.6}}, "d.relation.commitment": {"c2": {"value": 0.6}},
    "d.affect.stress": {"level": 0.2},
    "d.affect.emotion": {"label": ["恐惧"], "valence": -0.2, "arousal": 0.4, "intensity": 0.3, "confidence": 0.7},
    "d.cognition.face_cost": {"damage": 0.4, "maintenance_need": 0.5, "audience": []},
    "d.relation.harm_history": {"c2": {"h1": {"harm_id": "h1", "event": "r0", "description": "当众揭短", "severity": 3,
                                              "acknowledged": "否", "repair": "无", "salience": 0.8}}},
    "d.cognition.tom_level": {"ceiling": 2, "current": 2, "per_object": {"c2": 2}, "allowed_tasks": []},
    "d.relation.power_asymmetry": {"c2": {"objective": {"职位": {"higher": "other", "known_to_self": True}},
                                          "subjective": {"direction": "none", "bases": [], "confidence": 0.5}}},
    "m.reactivation": {"e1": [{"event": "r0", "cue": "电梯"}]},
}
EVENT, PLAN, FEEDBACK = "event", "plan", "feedback"


def entry(values=BASE, config=None):
    st = slots.blank_state(REG, "c1", "snap1", config)
    dynamic, memory, status = dict(st.dynamic), dict(st.memory), dict(st.status)
    for cid, value in values.items():
        (memory if cid.startswith("m.") else dynamic)[cid] = value
        status[cid] = "active"
    return I.AgentState(st.state_id, None, "snap1", dynamic, memory, st.versions, status)


def P(cid, key, field, after, grade=None, evidence=(W,), mechanisms=(), operator=None, reason="依据可见事件"):
    return I.ChangeProposal(cid, key, field, after, reason, grade, tuple(evidence), tuple(mechanisms), operator)


def profile(**modules):
    return I.StableProfile("snap1", "c1", {cid: I.StableModule(cid, value, "active", "explicit", .8)
                                           for cid, value in modules.items()})


class StoreCase(unittest.TestCase):
    values = BASE
    config = None

    def setUp(self):
        self.store = ConstructStore(REG)
        self.store.open(KEY, entry(self.values, self.config))
        self.phase = 0

    def ctx(self, kind, stable=NOBODY):
        self.phase += 1
        return I.StageContext(I.Phase("a1", self.phase, kind), self.store.snapshot(KEY), stable, (), {}, {}, KEY)

    def commit(self, kind, *proposals, judgments=(), evidence=EV, stable=NOBODY):
        return self.store.commit(self.ctx(kind, stable), I.StageOutput(None, proposals, tuple(judgments), evidence))

    def codes(self, result):
        return [r.error.code for r in result.rejected]

    def value(self, cid, key=None, field=None):
        return slots.get(self.store.snapshot(KEY), cid, key, field)


# -- layout ------------------------------------------------------------------------------------
class Layout(unittest.TestCase):
    def test_covers_the_registry(self):
        slots.check_layout(REG)
        self.assertEqual(len(slots.SLOTS), 45)

    def test_banded_fields_exactly_where_the_registry_has_a_tempo(self):
        for cid, slot in slots.SLOTS.items():
            self.assertEqual(bool(slot.banded), REG.construct(cid).tempo in ("slow", "fast"), cid)

    def test_blank_state(self):
        st = slots.blank_state(REG, "c1", "snap1", I.EnableConfig(fields={"d.group.alliance": False}))
        self.assertEqual(len(st.dynamic) + len(st.memory), 45)
        self.assertEqual(st.status["d.group.alliance"], "disabled")
        self.assertEqual(st.status["d.relation.trust"], "unknown")
        self.assertEqual(st.versions["state_schema_version"], slots.STATE_SCHEMA_VERSION)

    def test_addresses(self):
        self.assertEqual(slots.resolve("d.relation.harm_history", "c2/h1", "salience")[1:], (["c2", "h1"], ["salience"]))
        for args in (("d.relation.trust", None, "value"), ("d.relation.trust", "c2", "valu"),
                     ("d.goal.value_activation", "achievement", "level"), ("d.relation.harm_history", "c2", None)):
            with self.assertRaises(slots.AddressError):
                slots.resolve(*args)

    def test_kinds(self):
        self.assertIsNone(slots.check_kind("u?", None))
        self.assertEqual(slots.check_kind("u", 1.2)[0], "out_of_range")
        self.assertEqual(slots.check_kind("labels", ["焦虑"])[0], "bad_value")
        self.assertEqual(slots.check_kind("enum(是|否)", "也许")[0], "bad_value")
        self.assertEqual(slots.check_instance(slots.SLOTS["d.affect.emotion"], {"dominance": .1})[0], "not_modeled_field")


# -- IMagnitude ----------------------------------------------------------------------------------
class Bands(unittest.TestCase):
    m = Magnitude(REG)
    trust, emotion = REG.construct("d.relation.trust"), REG.construct("d.affect.emotion")

    def band(self, construct, field, grade, direction=-1, evidence=(W,), stable=NOBODY, instance=None):
        return self.m.range(construct, field, grade, direction, evidence, stable, instance)

    def test_tables_match_the_source(self):
        text = (SOURCE_DIR / FILES["rules"]).read_text(encoding="utf-8")
        for phrase in BASIS:
            self.assertIn(phrase, text)
        for table, phrase in ((SLOW, BASIS[0]), (FAST, BASIS[1])):
            numbers = [float(x) for x in re.findall(r"\d\.\d+", phrase.split("：")[1])]
            self.assertEqual(numbers, [table["tiny"][1], *table["small"], *table["medium"], *table["large"]])
        self.assertEqual(SLOW["extended"], (.45, .85))

    def test_grade_bands(self):
        self.assertEqual([self.band(self.trust, "value", g).name for g in (1, 2, 3, 4)], ["tiny", "small", "medium", "large"])
        self.assertEqual((self.band(self.trust, "value", 3).low, self.band(self.trust, "value", 3).high), (.15, .30))
        self.assertEqual(self.band(self.trust, "value", 4).extended, .85)
        self.assertIsNone(self.band(self.trust, "value", 3).extended)
        self.assertEqual(self.band(self.emotion, "intensity", 4).extended, 1.0)       # 快变构念可到量程上限

    def test_field_scales_and_unbanded_fields(self):
        self.assertEqual((self.band(self.emotion, "valence", 2).low, self.band(self.emotion, "valence", 2).high), (.2, .5))
        se = self.band(REG.construct("d.cognition.self_evaluation"), "global", 3)
        self.assertEqual((se.low, se.high), (.6, 1.2))
        self.assertIsNone(self.band(self.emotion, "label", 3))
        self.assertIsNone(self.band(REG.construct("d.goal.threat_to_goal"), None, 3))

    def test_evidence_caps(self):
        hearsay, inferred = I.EvidenceRef("r1", "hearsay"), I.EvidenceRef("r1", "inferred")
        self.assertEqual(self.band(self.trust, "value", 4, evidence=(hearsay,)).name, "medium")
        self.assertEqual(self.band(self.trust, "value", 3, evidence=(inferred,)).name, "small")
        self.assertEqual(self.band(self.trust, "value", 4, evidence=(hearsay, W)).name, "large")
        self.assertIsNone(self.band(self.trust, "value", 4, evidence=(hearsay,)).extended)

    def test_sensitivity_moves_at_most_one_band(self):
        anxious = profile(**{"s.traits.attachment.global": {"anxiety": 2.0}})
        drop = self.band(self.trust, "value", 2, -1, stable=anxious)
        self.assertEqual((drop.name, drop.low, drop.high), ("medium", .15, .225))     # 高一级档位的下半区
        self.assertEqual(self.band(self.trust, "value", 2, +1, stable=anxious).name, "small")
        calm = profile(**{"s.traits.personality.p1_big_five": {"neuroticism.anxiety": -2.0}})
        low = self.band(self.emotion, "intensity", 3, +1, stable=calm, instance={"valence": -.3})
        self.assertEqual((low.name, low.low, low.high), ("small", .175, .25))          # 低一级档位的上半区
        self.assertEqual(self.band(self.emotion, "intensity", 3, +1, stable=calm, instance={"valence": .3}).name, "medium")
        mild = profile(**{"s.traits.attachment.global": {"anxiety": 1.0}})
        self.assertEqual(self.band(self.trust, "value", 2, -1, stable=mild).name, "small")   # |z| <= 1

    def test_facets_merge_largest_plus_half_of_the_rest(self):
        p = profile(**{"s.traits.personality.p1_big_five": {"neuroticism.anxiety": 1.0, "neuroticism.depression": 1.0}})
        self.assertEqual(self.m.sensitivity("d.affect.stress", "level", +1, p), 1.5)
        self.assertEqual(self.band(REG.construct("d.affect.stress"), "level", 2, +1, stable=p).name, "medium")

    def test_bad_grade(self):
        with self.assertRaises(I.InterfaceError):
            self.band(self.trust, "value", 5)


# -- IStateStore: the flow ------------------------------------------------------------------------
class Commit(StoreCase):
    def test_accepted_change_moves_the_state(self):
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3))
        self.assertEqual(self.codes(r), [])
        c = r.accepted[0]
        self.assertEqual((c.update_operator, c.stage, c.group, c.band.name, c.before), ("relation_update", "S3",
                                                                                         "affect_self_relation_group", "medium", .6))
        self.assertEqual(self.value("d.relation.trust", "c2", "value"), .4)
        snap = self.store.snapshot(KEY)
        self.assertEqual((snap.state_id, snap.prev_state_id), ("c1@a1#1.feedback", "c1@entry"))

    def test_stage_permissions_come_from_the_registry(self):
        self.assertEqual(self.codes(self.commit(EVENT, P("d.relation.trust", "c2", "value", .4, grade=3))), ["stage_not_allowed"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.affect.stress", None, "level", .3, grade=2))), ["stage_not_allowed"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.stress", None, "level", .3, grade=2))), [])

    def test_hard_error_rejects_its_group_only(self):
        r = self.commit(PLAN, P("d.affect.stress", None, "level", .35, grade=2),
                        P("d.cognition.face_cost", None, "damage", 1.4, grade=2),
                        P("d.policy.current_strategy", "dm.conflict.conflict_mode/c2", None, "回避"))
        self.assertEqual(sorted(self.codes(r)), ["group_rejected", "out_of_range"])
        self.assertEqual([c.construct_id for c in r.accepted], ["d.policy.current_strategy"])
        self.assertEqual(self.value("d.affect.stress", None, "level"), .2)

    def test_judgments_supply_evidence_and_mechanisms(self):
        j = I.Judgment("dm.affect.primary_secondary_appraisal", "威胁", {}, (W2,), ("emotion_update",))
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .35, grade=2, evidence=()), judgments=[j])
        self.assertEqual((r.accepted[0].evidence, r.accepted[0].decision_mechanisms), ((W2,), (j.dm_id,)))
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .5, grade=2, evidence=()))
        self.assertEqual(self.codes(r), ["no_evidence"])

    def test_duplicates_merge_and_conflicts_reject(self):
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .35, grade=2),
                        P("d.affect.stress", None, "level", .35, grade=2, evidence=(W2,)))
        self.assertEqual(r.accepted[0].evidence, (W, W2))
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .5, grade=2), P("d.affect.stress", None, "level", .45, grade=2))
        self.assertEqual(self.codes(r), ["conflict_unresolved", "conflict_unresolved"])
        self.assertEqual(self.value("d.affect.stress", None, "level"), .35)

    def test_unknown_becomes_active_and_no_change_keeps_the_state(self):
        r = self.commit(EVENT, P("d.cognition.shame", None, "load", .2))
        self.assertEqual(self.codes(r), [])
        self.assertEqual(self.store.snapshot(KEY).status["d.cognition.shame"], "active")
        before = self.store.snapshot(KEY).state_id
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .2, grade=1))
        self.assertEqual((r.accepted, r.output_state_id), ((), before))

    def test_retry_is_a_no_op_and_a_changed_retry_is_refused(self):
        ctx = self.ctx(FEEDBACK)
        out = I.StageOutput(None, (P("d.relation.trust", "c2", "value", .4, grade=3),), (), EV)
        first = self.store.commit(ctx, out)
        self.assertIs(self.store.commit(ctx, out), first)
        with self.assertRaises(I.InterfaceError):
            self.store.commit(ctx, I.StageOutput(None, (P("d.relation.trust", "c2", "value", .35, grade=3),), (), EV))
        self.assertEqual(len(self.store.history(KEY)), 1)

    def test_stale_context_and_sessions(self):
        old = self.ctx(FEEDBACK)
        self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3))
        with self.assertRaises(I.InterfaceError):
            self.store.commit(old, I.StageOutput(None, (P("d.relation.trust", "c2", "value", .3, grade=3),), (), EV))
        with self.assertRaises(I.InterfaceError):
            self.store.open(KEY, entry())
        with self.assertRaises(I.InterfaceError):
            self.store.snapshot(("i1", "nobody"))
        broken = entry()
        with self.assertRaises(I.InterfaceError):
            self.store.open(("i2", "c1"), dataclasses.replace(broken, dynamic={}))

    def test_snapshots_are_copies(self):
        snap = self.store.snapshot(KEY)
        snap.dynamic["d.relation.trust"]["c2"]["value"] = 0.0
        self.assertEqual(self.value("d.relation.trust", "c2", "value"), .6)

    def test_routing_refusals(self):
        r = self.commit(FEEDBACK, P("s.values.schwartz_priorities", None, None, {}), P("d.affect.mood", None, None, 1),
                        P("d.relation.trust", "c2", "value", .4, grade=3, operator="emotion_update"),
                        P("d.relation.trust", "c2", "value", .4, grade=3, operator="free_write"))
        self.assertEqual(self.codes(r), ["writes_stable", "unregistered_construct", "unregistered_operator", "not_in_whitelist"])


# -- IValidator: each rule ---------------------------------------------------------------------------
class Rules(StoreCase):
    def test_bands(self):
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .55, grade=3))), ["under_band"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .2, grade=3))), ["over_band"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4))), ["no_band"])
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .0, grade=4))
        self.assertEqual((self.codes(r), r.accepted[0].flag), ([], "extended_range"))

    def test_values_and_fields(self):
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", 1.2, grade=4))), ["out_of_range"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.emotion", None, "label", ["焦虑"]))), ["bad_value"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.emotion", None, "dominance", .3))), ["not_modeled_field"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.emotion", None, "mood", .3))), ["bad_address"])

    def test_evidence_must_be_visible(self):
        r = self.commit(EVENT, P("d.affect.stress", None, "level", .35, grade=2, evidence=(I.EvidenceRef("r9", "witnessed"),)))
        self.assertEqual(self.codes(r), ["evidence_not_visible"])

    def test_stale_before(self):
        snap = self.store.snapshot(KEY)
        change = I.StateChange("d.affect.stress", None, "level", .9, .3, (W,), "emotion_update", (), "S1",
                               I.Band("small", .1, .25), "affect_self_relation_group", "r", grade=2)
        self.assertIn("stale_before", [v.code for v in self.store.validator.check(change, snap, EV)])

    def test_records_keep_their_facts(self):
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.harm_history", "c2/h1", None, None))), ["deletes_record"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.harm_history", "c2/h1", "severity", 2))), ["rewrites_fact"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.harm_history", "c2/h1", "repair", "部分"))), [])
        self.assertEqual(self.codes(self.commit(PLAN, P("m.reactivation", "e1", None, []))), ["deletes_record"])
        grown = [{"event": "r0", "cue": "电梯"}, {"event": "r1", "cue": "物业费"}]
        self.assertEqual(self.codes(self.commit(PLAN, P("m.reactivation", "e1", None, grown))), [])

    def test_status_blocks_writes(self):
        store = ConstructStore(REG)
        store.open(("i9", "c1"), entry(BASE, I.EnableConfig(fields={"d.cognition.shame": False})))
        ctx = I.StageContext(I.Phase("a1", 1, EVENT), store.snapshot(("i9", "c1")), NOBODY, (), {}, {}, ("i9", "c1"))
        r = store.commit(ctx, I.StageOutput(None, (P("d.cognition.shame", None, "load", .2),), (), EV))
        self.assertEqual(self.codes(r), ["unwritable_status"])

    def test_decay(self):
        r = self.commit(FEEDBACK, P("d.cognition.face_cost", None, "damage", .6, operator="decay_or_recovery"))
        self.assertEqual(self.codes(r), ["decay_wrong_direction"])
        r = self.commit(FEEDBACK, P("d.cognition.face_cost", None, "damage", .3, operator="decay_or_recovery"))
        self.assertEqual((self.codes(r), r.accepted[0].band), ([], None))
        r = self.commit(FEEDBACK, P("d.cognition.face_cost", None, "damage", .2, operator="decay_or_recovery"),
                        P("d.cognition.face_cost", None, "damage", .5, grade=2))
        self.assertEqual(self.codes(r), ["decay_superseded"])
        self.assertEqual(self.value("d.cognition.face_cost", None, "damage"), .5)
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.stress", None, "level", .1, operator="decay_or_recovery"))),
                         ["stage_not_allowed"])

    def test_theory_of_mind(self):
        self.assertEqual(self.codes(self.commit(EVENT, P("d.cognition.tom_level", None, "ceiling", 3))), ["tom_ceiling_fixed"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.cognition.tom_level", None, "current", 0))), ["tom_out_of_bounds"])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.cognition.tom_level", None, "current", 1))), [])
        inference = {"inference": "她以为我会同意", "kind": "belief", "order": 2, "confidence": .4, "evidence": []}
        self.assertEqual(self.codes(self.commit(EVENT, P("d.cognition.other_model", "c2/i1", None, inference))), [])
        self.assertEqual(self.codes(self.commit(EVENT, P("d.cognition.other_model", "c2/i2", None, {**inference, "order": 3}))),
                         ["tom_order_exceeded"])

    def test_power_and_strategy(self):
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.power_asymmetry", "c2", "objective", {}))),
                         ["objective_power_write"])
        self.assertEqual(self.codes(self.commit(FEEDBACK, P("d.relation.power_asymmetry", "c2", "subjective.direction", "other"))), [])
        self.assertEqual(self.codes(self.commit(PLAN, P("d.policy.current_strategy", "dm.conflict.conflict_mode/c2", None, "打一架"))),
                         ["unregistered_option"])
        self.assertEqual(self.codes(self.commit(PLAN, P("d.policy.current_strategy", "dm.none.thing/c2", None, "回避"))),
                         ["unregistered_mechanism"])

    def test_banded_fields_one_at_a_time(self):
        emotion = {**BASE["d.affect.emotion"], "intensity": .9}
        self.assertEqual(self.codes(self.commit(EVENT, P("d.affect.emotion", None, None, emotion, grade=2))),
                         ["banded_in_instance_write"])

    def test_unknown_mechanism_and_soft_handoff(self):
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3, mechanisms=("dm.cog.none",)))
        self.assertEqual(self.codes(r), ["unregistered_mechanism"])
        risk = I.Judgment("dm.choice.risk_ambiguity", None, {}, (W,), ())
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3, mechanisms=(risk.dm_id,)), judgments=[risk])
        self.assertEqual((self.codes(r), r.accepted[0].flag), ([], "unregistered_handoff"))

    def test_a_change_citing_an_unjudged_process_is_rejected_with_its_group(self):
        r = self.commit(PLAN, P("d.policy.current_strategy", "dm.conflict.conflict_mode/c2", None, "回避",
                                mechanisms=("dm.conflict.conflict_mode",)),
                        P("d.goal.goals_now", "g1", None, {"goal_id": "g1", "content": "说清楚", "priority": .6, "stage": "进行中",
                                                            "core_goal": None, "source": "场景", "reason": "x"}))
        self.assertIn("unjudged_mechanism", self.codes(r))
        self.assertEqual(r.accepted, ())


class HistoryChecks(StoreCase):
    def test_flip_without_new_evidence(self):
        self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3))
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .6, grade=3))
        self.assertEqual(r.accepted[0].flag, "flip_without_new_evidence")
        r = self.commit(FEEDBACK, P("d.relation.trust", "c2", "value", .4, grade=3, evidence=(W2,)))
        self.assertIsNone(r.accepted[0].flag)

    def test_uniform_relation_change(self):
        r = self.commit(FEEDBACK, *[P(f"d.relation.{d}", "c2", "value", .4, grade=3)
                                    for d in ("trust", "closeness", "dependence", "commitment")])
        self.assertEqual({c.flag for c in r.accepted}, {"uniform_relation_change"})
        self.assertEqual(len(r.soft_flags), 4)


if __name__ == "__main__":
    unittest.main()
