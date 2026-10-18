import copy
import json
import unittest
from pathlib import Path

from rolesim.mechanisms import CARD_MECHANISMS, CARDS
from rolesim.agent import initial_state, observe
from rolesim.runtime import ScriptedEnvironment
from rolesim.state import MECHANISMS, apply_delta, validate_dynamic

ROOT = Path(__file__).resolve().parents[1]


def trust_change(**patch):
    change = {"path": "dynamic.relations.partner.trust", "before": .55, "after": .4,
              "evidence": ["e2"], "mechanism": "relation_update", "reason": "说明仍未到达"}
    change.update(patch)
    return change


class StateDeltaTests(unittest.TestCase):
    def setUp(self):
        self.dynamic = initial_state()["dynamic"]
        self.visible = {"e1", "e2", "action:1"}

    def apply(self, *changes):
        return apply_delta(self.dynamic, {"changes": list(changes)}, self.visible)

    def test_valid_change_is_applied_to_a_copy(self):
        result = self.apply(trust_change())
        self.assertEqual(result["relations"]["partner"]["trust"], .4)
        self.assertEqual(self.dynamic["relations"]["partner"]["trust"], .55)

    def test_evidence_must_be_non_empty_and_visible(self):
        for evidence in ([], ["e9_hidden"], ["e1", "partner_private"], "e1", [None]):
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                self.apply(trust_change(evidence=evidence))

    def test_own_proposal_counts_as_evidence(self):
        self.apply(trust_change(evidence=["action:1"]))

    def test_stable_paths_are_rejected(self):
        for path in ("stable.emotion.threat_sensitivity", "stable", "memory.0", "dynamic", "dynamic..trust"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.apply(trust_change(path=path))

    def test_mechanism_must_be_registered_and_own_the_block(self):
        for mechanism in ("belief_revision", "decay", "free_update", None):
            with self.subTest(mechanism=mechanism), self.assertRaises(ValueError):
                self.apply(trust_change(mechanism=mechanism))

    def test_out_of_range_values_are_rejected_not_clipped(self):
        for value in (1.2, -.1, float("nan"), True, "high"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.apply(trust_change(after=value))
        with self.assertRaises(ValueError):
            self.apply({"path": "dynamic.emotion.valence", "before": None, "after": -1.5,
                        "evidence": ["e1"], "mechanism": "appraisal", "reason": "x"})

    def test_stale_before_value_is_rejected(self):
        with self.assertRaises(ValueError):
            self.apply(trust_change(before=.9))

    def test_one_bad_change_rejects_the_whole_delta(self):
        good, bad = trust_change(), trust_change(path="dynamic.emotion.intensity", before=.15, after=.3)
        with self.assertRaises(ValueError):
            self.apply(good, bad)
        self.assertNotIn("flag", good)

    def test_large_trust_step_is_written_and_flagged(self):
        change = trust_change(after=.1)
        result = self.apply(change)
        self.assertEqual(result["relations"]["partner"]["trust"], .1)
        self.assertEqual(change["flag"], "soft_violation")

    def test_beliefs_and_other_models_require_sources(self):
        belief = {"content": "说明会在今天补发", "confidence": .4}
        with self.assertRaises(ValueError):
            self.apply({"path": "dynamic.beliefs.delivery", "before": None, "after": belief,
                        "evidence": ["e1"], "mechanism": "belief_revision", "reason": "x"})
        result = self.apply({"path": "dynamic.beliefs.delivery", "before": None,
                             "after": {**belief, "source": ["e1"]}, "evidence": ["e1"],
                             "mechanism": "belief_revision", "reason": "x"})
        validate_dynamic(result)

    def test_unknown_change_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            self.apply(trust_change(stable_patch={}))


class RuntimeStateTests(unittest.TestCase):
    def test_every_perceive_writes_an_evidenced_delta(self):
        persona = json.loads((ROOT / "examples" / "cautious.json").read_text())
        scenario = json.loads((ROOT / "examples" / "cooperation_conflict.json").read_text())
        state = initial_state()
        for _, event in ScriptedEnvironment(scenario, persona["id"]).scheduled():
            observe(state, event, persona)
        for delta in state["updates"]:
            for change in delta["changes"]:
                self.assertEqual(change["evidence"], [delta["event_id"]])
                self.assertTrue(change["path"].startswith("dynamic."))
        validate_dynamic(state["dynamic"])
        self.assertEqual(state["schema_version"], "state-v0")


class CardMechanismMappingTests(unittest.TestCase):
    def test_every_card_maps_to_registered_mechanisms(self):
        self.assertEqual(set(CARD_MECHANISMS), {card["id"] for card in CARDS})
        for card, mechanisms in CARD_MECHANISMS.items():
            with self.subTest(card=card):
                self.assertTrue(mechanisms)
                self.assertLessEqual(set(mechanisms), set(MECHANISMS))


if __name__ == "__main__":
    unittest.main()
