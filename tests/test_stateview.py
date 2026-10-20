import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import cued_events, load
from rolesim.agent import initial_state, observe
from rolesim.contract import Event, Visibility
from rolesim.mechanisms import TENSION, context, select_cards, triggered
from rolesim.providers import MockBackend
from rolesim.roles import load_role, load_scene, run
from rolesim.stateview import level, state_view

OPERATOR_NAMES = ("emotion_update", "decay_or_recovery", "relation_update")
TRUST = "dynamic.relation_state.role2.trust"


class Capture(MockBackend):
    def __init__(self):
        self.calls = []

    def appraise(self, payload):
        self.calls.append(("appraise", copy.deepcopy(payload)))
        return super().appraise(payload)

    def decide(self, payload):
        self.calls.append(("decide", copy.deepcopy(payload)))
        return super().decide(payload)

    def express(self, payload):
        self.calls.append(("express", copy.deepcopy(payload)))
        return super().express(payload)


def capture_run(mode="profile_state_mechanisms", appraise_state=True):
    backend = Capture()
    with tempfile.TemporaryDirectory() as tmp:
        manifest = run([load_role("role1"), load_role("role2")], load_scene("test1"), backend, Path(tmp) / "run",
                       mode, appraise_state=appraise_state)
    return backend.calls, manifest


def neutral(i):
    return Event(f"n{i}", i, "narration", "env", "会议继续。", Visibility("public", ["role1", "role2"]))


def pressing(i):
    return Event(f"p{i}", i, "narration", "env", "又被催了。", Visibility("public", ["role1", "role2"]),
                 signals={"cues": {"pressure": 1}})


class LevelTests(unittest.TestCase):
    def test_equal_thirds_of_the_range(self):
        self.assertEqual([level(v, "intensity") for v in (0, .33, .34, .66, .67, 1)],
                         ["低", "低", "中", "中", "高", "高"])
        self.assertEqual(level(-.5, "valence"), "低")

    def test_view_shows_active_values_only_and_merges_changes(self):
        state = initial_state(others=["role2"])
        since = [{"path": TENSION, "before": .15, "after": .5, "mechanism": "emotion_update"},
                 {"path": TENSION, "before": .5, "after": .8, "mechanism": "emotion_update"}]
        quiet = state_view(state, {"role2": "合作方负责人"}, since)
        self.assertEqual([i["field"] for i in quiet["items"]], ["情绪（紧张）强度", "对合作方负责人的信任"])
        self.assertEqual(quiet["changes_since_my_last_turn"],
                         [{"field": "情绪（紧张）强度", "from": "低", "to": "高", "direction": "上升"}])
        full = state_view(state, {"role2": "合作方负责人"}, since, numbers=True, mechanisms=True)
        self.assertEqual(full["changes_since_my_last_turn"][0]["by"], ["emotion_update"])
        self.assertEqual((full["changes_since_my_last_turn"][0]["before"], full["items"][0]["value"]), (.15, .15))
        self.assertIn("工程假设", full["note"])


class PromptTests(unittest.TestCase):
    def test_numbers_and_operator_names_reach_only_the_decision_prompt(self):
        calls, _ = capture_run()
        for stage, payload in calls:
            text = json.dumps(payload.get("current_state") or payload.get("current_feeling"), ensure_ascii=False)
            with self.subTest(stage=stage):
                if stage == "decide":
                    self.assertIn('"value"', text)
                else:
                    self.assertNotIn('"value"', text)
                    self.assertNotIn('"before"', text)
                    for name in OPERATOR_NAMES:
                        self.assertNotIn(name, json.dumps(payload, ensure_ascii=False))
        stages = {stage for stage, p in calls if "current_state" in p or "current_feeling" in p}
        self.assertEqual(stages, {"appraise", "decide", "express"})
        self.assertTrue(any(c["by"] for _, p in calls if "current_state" in p and "value" in json.dumps(p)
                            for c in p["current_state"]["changes_since_my_last_turn"]))

    def test_profile_only_shows_no_state_and_the_switch_keeps_it_out_of_appraisal(self):
        calls, _ = capture_run("profile_only")
        self.assertFalse(any("current_state" in p or "current_feeling" in p for _, p in calls))
        calls, manifest = capture_run(appraise_state=False)
        self.assertFalse(manifest["appraise_state"])
        self.assertFalse(any("current_state" in p for stage, p in calls if stage == "appraise"))
        self.assertTrue(all("current_state" in p for stage, p in calls if stage == "decide"))

    def test_same_action_reads_differently_by_state(self):
        payload = {"action": "wait", "current_feeling": {"items": [{"field": "情绪（紧张）强度", "level": "低"}]}}
        calm = MockBackend().express(payload)
        payload["current_feeling"]["items"][0]["level"] = "高"
        self.assertNotEqual(MockBackend().express(payload), calm)


class DynamicsTests(unittest.TestCase):
    def tension(self, persona, events):
        state = initial_state(others=["role2"])
        values = []
        for event in events:
            observe(state, event, persona)
            values.append(state["dynamic"]["affect_state"]["emotion"]["intensity"])
        return values

    def test_tension_decays_without_pressure(self):
        values = self.tension(load("role1"), [neutral(i) for i in range(1, 21)])
        self.assertTrue(all(b < a for a, b in zip(values, values[1:]) if a > 0))
        self.assertLess(values[-1], .01)

    def test_tension_is_bounded_and_settles_under_constant_pressure(self):
        values = self.tension(load("role1"), [pressing(i) for i in range(1, 31)])
        self.assertTrue(all(0 <= v <= 1 for v in values))
        calm = self.tension(load("role2"), [pressing(i) for i in range(1, 31)])
        self.assertAlmostEqual(calm[-1], .35 * .2 / .6, places=3)
        self.assertGreater(values[-1], calm[-1])


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")
        self.state = initial_state(others=["role2"])

    def ctx(self, cues=None, since=(), other="role2"):
        return context(self.persona, self.state["dynamic"], cues or {}, ["role2"], other, since)

    def test_high_tension_triggers_the_appraise_card_without_a_pressure_cue(self):
        self.assertEqual(triggered(self.ctx()), [])
        self.state["dynamic"]["affect_state"]["emotion"]["intensity"] = .8
        fired = triggered(self.ctx())
        self.assertEqual([(f["card_id"], f["matched"][0]["kind"]) for f in fired], [("appraise", "state")])
        self.assertEqual(select_cards(self.persona, self.ctx(), ["wait"])[0]["id"], "appraise")

    def test_falling_trust_toward_the_speaker_triggers_the_trust_card(self):
        drop = [{"path": TRUST, "before": .55, "after": .45, "mechanism": "relation_update"}]
        fired = {f["card_id"]: f for f in triggered(self.ctx(since=drop))}
        self.assertEqual(fired["trust"]["matched"][0]["observed"], -.1)
        self.assertNotIn("trust", {f["card_id"] for f in triggered(self.ctx(since=drop, other=None))})

    def test_cards_without_a_state_rule_say_so(self):
        fired = triggered(self.ctx({"uncertainty": .9}))
        self.assertEqual(fired[0]["effects"], "no_state_rule")

    def test_every_change_names_its_card_rule_and_inputs(self):
        delta = observe(self.state, cued_events()[1], self.persona)
        for change in delta["changes"]:
            self.assertIn(change["card"], {"appraise", "trust"})
            self.assertIn(change["rule"], {"tension_v1", "trust_v1"})
            self.assertTrue(change["inputs"])
        self.assertEqual({c["card_id"] for c in self.state["mechanism_log"][-1]["cards"]},
                         {"verify", "appraise", "trust", "boundary"})


if __name__ == "__main__":
    unittest.main()
