import copy
import json
import unittest
from pathlib import Path
from unittest import mock

from fixtures import cued_events, load
from rolesim.agent import initial_state, observe
from rolesim.mechanisms import CARD_MECHANISMS, CARDS
from rolesim.modules import STATUSES, transition
from rolesim.state import (DOMAINS, OPERATORS, SOFT_RULES, VERSIONS, commit_phase, may_write,
                           validate_dynamic)

ROOT = Path(__file__).resolve().parents[1]
TRUST = "dynamic.relation_state.partner.trust"
INTENSITY = "dynamic.affect_state.emotion.intensity"


def change(path=TRUST, before=.55, after=.4, **patch):
    item = {"path": path, "before": before, "after": after, "evidence": ["e2"],
            "mechanism": "relation_update", "reason": "说明仍未到达"}
    item.update(patch)
    return item


def trust_change(**patch):
    return change(**patch)


def intensity_change(**patch):
    item = change(INTENSITY, .15, .3, mechanism="emotion_update")
    item.update(patch)
    return item


class CommitTests(unittest.TestCase):
    def setUp(self):
        state = initial_state()
        self.dynamic, self.status = state["dynamic"], state["module_status"]
        self.visible = {"e1", "e2", "action:1"}

    def commit(self, *changes, **kwargs):
        return commit_phase(self.dynamic, self.status, {"changes": list(changes)}, self.visible, **kwargs)

    def rejected(self, *changes):
        _, adopted, log = self.commit(*changes)
        self.assertEqual(adopted, [], log)
        return log["rejected"]

    def test_valid_change_is_applied_to_a_copy(self):
        result, adopted, log = self.commit(trust_change())
        self.assertEqual(result["relation_state"]["partner"]["trust"], .4)
        self.assertEqual(self.dynamic["relation_state"]["partner"]["trust"], .55)
        self.assertEqual(len(adopted), 1)
        self.assertEqual(log["rejected"], [])

    def test_evidence_must_be_non_empty_and_visible(self):
        for evidence in ([], ["e9_hidden"], ["e1", "partner_private"], "e1", [None]):
            with self.subTest(evidence=evidence):
                self.assertTrue(self.rejected(trust_change(evidence=evidence)))

    def test_own_proposal_counts_as_evidence(self):
        self.assertEqual(self.commit(trust_change(evidence=["action:1"]))[2]["rejected"], [])

    def test_stable_and_non_dynamic_paths_are_rejected(self):
        for path in ("stable.emotion.threat_sensitivity", "stable", "memory.0", "dynamic", "dynamic..trust",
                     "dynamic.relation_state.partner", "dynamic.affect_state.emotion"):
            with self.subTest(path=path):
                self.assertTrue(self.rejected(trust_change(path=path)))

    def test_old_v0_operators_and_foreign_operators_are_rejected(self):
        for mechanism in ("appraisal", "decay", "relation_update_v2", "emotion_update", None):
            with self.subTest(mechanism=mechanism):
                self.assertTrue(self.rejected(trust_change(mechanism=mechanism)))

    def test_every_operator_is_checked_against_its_own_paths(self):
        self.assertEqual(len(OPERATORS), 11)
        self.assertTrue(may_write("relation_update", TRUST))
        self.assertFalse(may_write("belief_revision", TRUST))
        self.assertTrue(may_write("decay_or_recovery", "dynamic.relation_state.partner.harm_history.events"))
        self.assertFalse(may_write("memory_update", "dynamic.affect_state.emotion.intensity"))

    def test_out_of_range_values_are_rejected_not_clipped(self):
        for value in (1.2, -.1, float("nan"), True, "high"):
            with self.subTest(value=value):
                self.assertTrue(self.rejected(trust_change(after=value)))
        self.assertTrue(self.rejected(change("dynamic.affect_state.emotion.valence", None, -1.5,
                                             mechanism="emotion_update", evidence=["e1"])))

    def test_stale_before_value_is_rejected(self):
        self.assertTrue(self.rejected(trust_change(before=.9)))

    def test_unknown_change_fields_are_rejected(self):
        self.assertTrue(self.rejected(trust_change(stable_patch={})))

    def test_beliefs_and_other_models_require_sources(self):
        belief = {"content": "说明会在今天补发", "confidence": .4}
        path = "dynamic.cognition_state.beliefs.delivery"
        base = dict(mechanism="belief_revision", evidence=["e1"], reason="x")
        self.assertTrue(self.rejected(change(path, None, belief, **base)))
        result, adopted, _ = self.commit(change(path, None, {**belief, "source": ["e1"]}, **base))
        self.assertEqual(len(adopted), 1)
        validate_dynamic(result, self.status)


class ModuleStatusTests(unittest.TestCase):
    def setUp(self):
        state = initial_state()
        self.state, self.visible = state, {"e1"}

    def test_unmodelled_unknown_and_inactive_modules_cannot_be_written(self):
        cases = [("dynamic.group_state.norms", "group_state_update", "x"),
                 ("dynamic.affect_state.emotion.dominance", "emotion_update", .2),
                 ("dynamic.relation_state.partner.closeness", "relation_update", .4),
                 ("dynamic.relation_state.partner.power_asymmetry", "relation_update", {"perceived": 1})]
        for path, operator, value in cases:
            with self.subTest(path=path):
                _, adopted, log = commit_phase(
                    self.state["dynamic"], self.state["module_status"],
                    {"changes": [change(path, None, value, mechanism=operator, evidence=["e1"])]}, self.visible)
                self.assertEqual(adopted, [])
                self.assertRegex(log["rejected"][0]["reason"], "only active modules|may not write")

    def test_unknown_values_are_null_and_zero_is_a_real_value(self):
        status = self.state["module_status"]
        self.assertEqual(status["dynamic.relation_state.partner.closeness"]["status"], "unknown")
        self.assertIsNone(self.state["dynamic"]["relation_state"]["partner"]["closeness"])
        self.assertEqual(status[TRUST]["status"], "active")
        self.assertEqual(status[TRUST]["origin"], "default")
        bad = copy.deepcopy(self.state["dynamic"])
        bad["relation_state"]["partner"]["closeness"] = 0
        with self.assertRaises(ValueError):
            validate_dynamic(bad, status)   # unknown must hold null; 0 would pass as an observation
        bad = copy.deepcopy(self.state["dynamic"])
        bad["relation_state"]["partner"]["trust"] = None
        with self.assertRaises(ValueError):
            validate_dynamic(bad, status)   # active may not be null

    def test_values_outside_registered_modules_are_rejected(self):
        bad = copy.deepcopy(self.state["dynamic"])
        bad["affect_state"]["mood_ring"] = 1
        with self.assertRaises(ValueError):
            validate_dynamic(bad, self.state["module_status"])

    def test_six_domains_in_fixed_order(self):
        self.assertEqual(tuple(self.state["dynamic"]), DOMAINS)
        self.assertEqual(len(DOMAINS), 6)

    def test_transitions_are_audited_and_refuse_silent_activation(self):
        status, log = copy.deepcopy(self.state["module_status"]), []
        path = "dynamic.relation_state.partner.closeness"
        with self.assertRaises(ValueError):
            transition(status, log, path, "active", "filled", source=None)       # unknown needs evidence
        with self.assertRaises(ValueError):
            transition(status, log, "dynamic.group_state", "active", "turn on")  # not_modeled needs validation
        with self.assertRaises(ValueError):
            transition(status, log, path, "bogus", "x")
        with self.assertRaises(ValueError):
            transition(status, log, path, "disabled", "")
        transition(status, log, "dynamic.group_state", "disabled", "off for this run")
        transition(status, log, "dynamic.group_state", "active", "rules checked", validated=True)
        self.assertEqual([r["to"] for r in log], ["disabled", "active"])
        self.assertTrue(STATUSES >= {r["to"] for r in log})

    def test_initial_defaults_leave_an_audit_trail(self):
        paths = {r["path"] for r in self.state["status_log"]}
        self.assertEqual(paths, {INTENSITY, "dynamic.affect_state.emotion.label", TRUST})
        self.assertTrue(all(r["source"] == ["engineering_default"] for r in self.state["status_log"]))


class StagedCommitTests(unittest.TestCase):
    def setUp(self):
        state = initial_state()
        self.dynamic, self.status = state["dynamic"], state["module_status"]
        self.visible = {"e1", "e2"}

    def commit(self, *changes, **kwargs):
        return commit_phase(self.dynamic, self.status, {"changes": list(changes)}, self.visible, **kwargs)

    def test_hard_error_rejects_only_its_dependency_group(self):
        good = intensity_change(group="affect", evidence=["e1"])
        bad = trust_change(group="relation", before=.9)
        result, adopted, log = self.commit(good, bad)
        self.assertEqual(result["affect_state"]["emotion"]["intensity"], .3)
        self.assertEqual(result["relation_state"]["partner"]["trust"], .55)
        self.assertEqual([c["path"] for c in adopted], [INTENSITY])
        self.assertEqual([r["group"] for r in log["rejected"]], ["relation"])
        self.assertNotIn("flag", bad)

    def test_ungrouped_changes_form_one_batch(self):
        result, adopted, log = self.commit(intensity_change(evidence=["e1"]), trust_change(before=.9))
        self.assertEqual(adopted, [])
        self.assertEqual(result, self.dynamic)
        self.assertEqual(log["rejected"][0]["group"], "default")

    def test_group_is_atomic(self):
        first = intensity_change(group="g", evidence=["e1"])
        second = trust_change(group="g", after=1.5)
        result, adopted, _ = self.commit(first, second)
        self.assertEqual((adopted, result), ([], self.dynamic))

    def test_identical_duplicate_contributions_are_deduplicated(self):
        a, b = trust_change(group="x"), trust_change(group="y")
        result, adopted, log = self.commit(a, b)
        self.assertEqual(result["relation_state"]["partner"]["trust"], .4)
        self.assertEqual(len(adopted), 1)
        self.assertEqual(log["duplicates_dropped"], [TRUST])

    def test_conflicting_same_path_changes_reject_both_groups(self):
        a, b = trust_change(group="x", after=.4), trust_change(group="y", after=.5)
        result, adopted, log = self.commit(a, b)
        self.assertEqual((adopted, result), ([], self.dynamic))
        self.assertEqual({r["group"] for r in log["rejected"]}, {"x", "y"})
        self.assertIn("conflict", log["rejected"][0]["reason"])

    def test_candidates_see_the_shared_snapshot_not_each_others_results(self):
        # The second change's `before` is the value after the first would apply; it must be stale.
        first = intensity_change(group="a", evidence=["e1"])
        second = change(INTENSITY.replace("intensity", "label"), "紧张", "平静", mechanism="emotion_update",
                        group="b", evidence=["e1"])
        chained = intensity_change(group="c", before=.3, after=.4, evidence=["e1"])
        _, adopted, log = self.commit(first, second, chained)
        self.assertEqual({c["group"] for c in adopted}, {"a", "b"})
        self.assertEqual([r["group"] for r in log["rejected"]], ["c"])

    def test_soft_violation_is_written_and_flagged(self):
        item = trust_change(after=.1)
        result, adopted, log = self.commit(item)
        self.assertEqual(result["relation_state"]["partner"]["trust"], .1)
        self.assertEqual(item["flag"], "soft_violation")
        self.assertEqual(log["flags"], [{"path": TRUST, "flag": "soft_violation"}])

    def exception(self, **patch):
        record = {"soft_rule": "trust_step", "evidence": ["e2"], "approved_by": "card-trust-1",
                  "review": "pending"}
        record.update(patch)
        return record

    def test_exception_cannot_waive_a_rule_the_card_does_not_allow(self):
        _, adopted, log = self.commit(trust_change(after=.1, exception=self.exception()))
        self.assertEqual(adopted, [])
        self.assertIn("does not allow", log["rejected"][0]["reason"])

    def test_exception_on_an_exceptionable_rule_is_recorded(self):
        rules = {"trust_step": {**SOFT_RULES["trust_step"], "exceptionable": True}}
        item = trust_change(after=.1, exception=self.exception())
        with mock.patch.dict("rolesim.state.SOFT_RULES", rules):
            _, adopted, _ = self.commit(item)
            self.assertEqual(item["flag"], "exception_approved")
            self.assertEqual(item["exception"]["approved_by"], "card-trust-1")
            for patch in ({"evidence": ["hidden"]}, {"soft_rule": "other"}, {"review": ""}):
                with self.subTest(patch=patch):
                    self.assertEqual(self.commit(trust_change(after=.1, exception=self.exception(**patch)))[1], [])
            # An exception never waives a hard error and needs a violation to exist.
            self.assertEqual(self.commit(trust_change(after=.5, exception=self.exception()))[1], [])
            self.assertEqual(self.commit(trust_change(after=1.5, exception=self.exception()))[1], [])

    def test_commit_key_makes_retries_idempotent(self):
        committed = {}
        result, adopted, log = self.commit(trust_change(), commit_key="r/e2/perceive", committed=committed)
        again, adopted_again, replay = commit_phase(result, self.status, {"changes": [trust_change()]},
                                                    self.visible, commit_key="r/e2/perceive",
                                                    committed=committed)
        self.assertEqual((again, adopted_again), (result, []))
        self.assertTrue(replay["replayed"])

    def test_log_records_candidates_adoption_and_versions(self):
        _, _, log = self.commit(intensity_change(group="affect", evidence=["e1"]),
                                trust_change(group="relation", before=.9))
        self.assertEqual({c["path"] for c in log["candidates"]}, {INTENSITY, TRUST})
        self.assertEqual(log["adopted"], [{"group": "affect", "paths": [INTENSITY]}])
        self.assertEqual(log["versions"], VERSIONS)
        self.assertEqual(set(VERSIONS), {"construct_definition_version", "state_schema_version",
                                         "update_rule_version", "evaluation_protocol_version"})

    def test_malformed_delta_is_unparseable(self):
        for delta in (None, {}, {"changes": "x"}, {"changes": [trust_change(group="")]}):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                commit_phase(self.dynamic, self.status, delta, self.visible)


class HarmHistoryTests(unittest.TestCase):
    def test_harm_events_need_the_full_record(self):
        state = initial_state()
        path = "dynamic.relation_state.partner.harm_history.events"
        event = {"event_id": "e1", "severity": .4, "acknowledged": False, "repaired": False,
                 "last_activated": "e1"}
        make = lambda after: commit_phase(state["dynamic"], state["module_status"], {"changes": [
            change(path, [], after, mechanism="relation_update", evidence=["e1"])]}, {"e1"})
        self.assertEqual(len(make([event])[1]), 1)
        for broken in ([{**event, "severity": 1.4}], [{**event, "repaired": "no"}],
                       [{k: v for k, v in event.items() if k != "severity"}]):
            with self.subTest(broken=broken):
                self.assertEqual(make(broken)[1], [])


class RuntimeStateTests(unittest.TestCase):
    def test_every_perceive_writes_an_evidenced_delta(self):
        persona = load("role1")
        state = initial_state()
        for event in cued_events():
            observe(state, event, persona)
        for delta in state["updates"]:
            self.assertEqual(delta["rejected"], [])
            for item in delta["changes"]:
                self.assertEqual(item["evidence"], [delta["event_id"]])
                self.assertTrue(item["path"].startswith("dynamic."))
        validate_dynamic(state["dynamic"], state["module_status"])
        self.assertEqual(state["schema_version"], "state-v1")
        self.assertEqual(len(state["mechanism_log"]), len(state["updates"]))

    def test_unknown_inputs_skip_their_branch_instead_of_guessing(self):
        persona = load("role1")
        state = initial_state()
        state["dynamic"]["relation_state"]["partner"]["trust"] = None
        state["module_status"][TRUST]["status"] = "unknown"
        for event in cued_events():
            delta = observe(state, event, persona)
            self.assertTrue(all(c["path"] != TRUST for c in delta["changes"]))
        self.assertIsNone(state["dynamic"]["relation_state"]["partner"]["trust"])


class CardMechanismMappingTests(unittest.TestCase):
    def test_every_card_maps_to_registered_operators(self):
        self.assertEqual(set(CARD_MECHANISMS), {card["id"] for card in CARDS})
        for card, operators in CARD_MECHANISMS.items():
            with self.subTest(card=card):
                self.assertTrue(operators)
                self.assertLessEqual(set(operators), set(OPERATORS))


if __name__ == "__main__":
    unittest.main()
