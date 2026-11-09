import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.role.briefing import mentions, validate_briefing
from rolesim.run.scene import RESERVED_IDS, validate_scenario
from fixtures import load, setup



class BriefingValidationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load("test1")
        self.briefing = setup("role1")

    def test_example_scene_and_setups_are_valid(self):
        validate_scenario(self.scenario)
        validate_briefing(self.briefing, {"open"})
        validate_briefing(setup("role2"), {"open"})

    def test_invalid_briefings_are_rejected(self):
        def patched(fn):
            data = copy.deepcopy(self.briefing)
            fn(data)
            return data
        cases = {
            "direct lie": patched(lambda b: b["conceal"][0].update(strategy="direct_lie")),
            "recap id prefix": patched(lambda b: b["recap"][0].update(id="r1")),
            "prefix on non-recap": patched(lambda b: b["disclose"][0].update(id="recap:d1")),
            "event id collision": patched(lambda b: b["disclose"][0].update(id="open")),
            "speech id prefix": patched(lambda b: b["conceal"][0].update(id="speech:1")),
            "duplicate id": patched(lambda b: b["conceal"][0].update(id="d1")),
            "empty markers": patched(lambda b: b["disclose"][0].update(markers=[])),
            "one-char marker": patched(lambda b: b["disclose"][0].update(markers=["批"])),
            "recap repeats a secret": patched(lambda b: b["recap"][0].update(text="其实第三批还没清洗完。")),
            "missing goal": patched(lambda b: b.pop("goal")),
        }
        for label, data in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                validate_briefing(data, {"open"})

    def test_scene_is_background_only(self):
        for broken in (dict(self.scenario, schema_version="1.2"),
                       dict(self.scenario, briefings={"*": self.briefing}),
                       dict(self.scenario, goal="指导角色的目标")):
            with self.assertRaises(ValueError):
                validate_scenario(broken)

    def test_setup_ids_must_not_collide_with_environment_events(self):
        clash = copy.deepcopy(self.briefing)
        clash["disclose"][0]["id"] = "open"
        with self.assertRaisesRegex(ValueError, "open"):
            validate_briefing(clash, RESERVED_IDS)

    def test_mentions_use_markers(self):
        self.assertEqual(mentions("第三批还在处理", self.briefing["conceal"]), ["c1"])
        self.assertEqual(mentions("先交付前两批", self.briefing["disclose"]), ["d1"])
        self.assertEqual(mentions("稍后答复", self.briefing["conceal"]), [])



if __name__ == "__main__":
    unittest.main()
