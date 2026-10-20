import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim import vocab
from rolesim.agent import MODES, Agent
from rolesim.contract import StepRequest
from rolesim.providers import MockBackend
from rolesim.runtime import run_dialogue
from rolesim.skills import (ACTIONS, BINDING_VERSION, CUES, LIBRARY, bind, load_library, parse_skill,
                            select)
from fixtures import cued_events, load, setup


ROOT = Path(__file__).resolve().parents[1]
CATALOG_IDS = {s["id"] for s in json.loads((ROOT / "data" / "catalog.json").read_text())["sections"]}




VALID = """---
name: demo-skill
description: 演示用。
metadata:
  rolesim-kind: decision
  rolesim-cues: pressure
  rolesim-actions: wait
  rolesim-source: decision.2.3
  rolesim-evidence: engineering_hypothesis
---
# 演示

## 决策要点
- a

## 表达要点
- b

## 不会做的事
- c

## 边界
- d
"""


class Capture(MockBackend):
    def __init__(self):
        self.payloads = []

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


class LibraryTests(unittest.TestCase):
    def test_library_parses_and_cites_catalog_sections(self):
        library = load_library()
        self.assertEqual(len(library), 9)
        for name, skill in library.items():
            with self.subTest(skill=name):
                self.assertEqual(skill["name"], name)
                self.assertTrue(set(skill["sources"]) <= CATALOG_IDS)
                self.assertEqual(skill["evidence"], "engineering_hypothesis")
                self.assertTrue(skill["expression"] and skill["avoid"] and skill["limits"])

    def test_vocabularies_match_the_scripted_environment(self):
        self.assertEqual(CUES, vocab.CUES)
        self.assertEqual(ACTIONS, vocab.ACTIONS)

    def test_valid_fixture_parses(self):
        skill = parse_skill(VALID, "demo-skill", CATALOG_IDS)
        self.assertEqual((skill["kind"], skill["cues"], skill["actions"]), ("decision", ["pressure"], ["wait"]))

    def test_malformed_skills_are_rejected(self):
        cases = {
            "folder mismatch": (VALID, "other-skill"),
            "unknown kind": (VALID.replace("kind: decision", "kind: mood"), "demo-skill"),
            "cues and trigger": (VALID.replace("rolesim-cues: pressure", "rolesim-cues: pressure\n  rolesim-trigger: conceal"), "demo-skill"),
            "unknown cue": (VALID.replace("cues: pressure", "cues: boredom"), "demo-skill"),
            "decision without actions": (VALID.replace("  rolesim-actions: wait\n", ""), "demo-skill"),
            "source outside catalog": (VALID.replace("decision.2.3", "decision.99"), "demo-skill"),
            "missing section": (VALID.replace("## 边界\n- d\n", ""), "demo-skill"),
            "unknown section": (VALID + "\n## 备注\n- e\n", "demo-skill"),
            "no frontmatter": (VALID.split("---", 2)[2], "demo-skill"),
            "unknown field": (VALID.replace("description:", "persona: x\ndescription:"), "demo-skill"),
        }
        for label, (raw, folder) in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                parse_skill(raw, folder, CATALOG_IDS)

    def test_skill_folders_follow_the_agent_skills_layout(self):
        for folder in LIBRARY.iterdir():
            self.assertTrue((folder / "SKILL.md").exists(), folder)


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.library = load_library()
        self.binding = load("role1.skills")

    def test_examples_bind_and_sort_by_priority(self):
        bound = bind(self.binding, "role1", self.library)
        self.assertEqual(bound[0]["name"], "verify-before-commit")
        self.assertEqual([s["priority"] for s in bound], sorted((s["priority"] for s in bound), reverse=True))
        bind(load("role2.skills"), "role2", self.library)

    def test_invalid_bindings_are_rejected(self):
        def patched(fn):
            data = copy.deepcopy(self.binding)
            fn(data)
            return data
        cases = [
            (patched(lambda b: b.update(schema_version="skills-binding-0.9")), "role1"),
            (self.binding, "role2"),
            (patched(lambda b: b["skills"].append({"name": "telepathy", "priority": .5, "note": "x"})), "role1"),
            (patched(lambda b: b["skills"].append(dict(b["skills"][0]))), "role1"),
            (patched(lambda b: b["skills"][0].update(priority=1.5)), "role1"),
            (patched(lambda b: b["skills"][0].pop("note")), "role1"),
            (patched(lambda b: b.update(skills=[])), "role1"),
        ]
        for data, persona_id in cases:
            with self.subTest(binding=data["schema_version"], persona=persona_id), self.assertRaises(ValueError):
                bind(data, persona_id, self.library)
        self.assertEqual(BINDING_VERSION, self.binding["schema_version"])


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.bound = bind(load("role1.skills"), "role1", load_library())
        self.scenario = load("test1")
        self.allowed = self.scenario["allowed_actions"]
        self.briefing = setup("role1")

    def obs(self, **cues):
        return {"id": "e", "cues": cues}

    def test_cue_threshold_feasibility_and_cap(self):
        active = select(self.bound, self.obs(uncertainty=.9, pressure=.6, conflict=.9), self.allowed)
        self.assertLessEqual(len(active), 3)
        self.assertEqual(active[0]["name"], "verify-before-commit")
        self.assertEqual(select(self.bound, self.obs(pressure=.4), self.allowed), [])
        only_wait = select(self.bound, self.obs(pressure=.9), ["wait"])
        self.assertEqual([a["name"] for a in only_wait if a["actions"]], ["pause-under-pressure"])
        self.assertTrue(all(set(a["actions"]) <= {"wait"} for a in only_wait))

    def test_briefing_triggers_disclosure_skills_without_cues(self):
        names = [a["name"] for a in select(self.bound, self.obs(), self.allowed, self.briefing)]
        self.assertEqual(names, ["selective-disclosure", "proactive-disclosure"])
        self.assertEqual(select(self.bound, self.obs(), self.allowed), [])
        quiet = dict(self.briefing, conceal=[])
        self.assertEqual([a["name"] for a in select(self.bound, self.obs(), self.allowed, quiet)],
                         ["proactive-disclosure"])


class SkillRunTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")
        self.scenario = load("test1")
        self.binding = load("role1.skills")

    def run_capture(self, mode="profile_state_mechanisms", use_skills=True, binding="default"):
        """role1 carries the skills under test; role2 sits opposite without any."""
        backend = Capture()
        cast = [{"persona": self.persona, "skills": self.binding if binding == "default" else binding,
                 "briefing": None},
                {"persona": load("role2"), "skills": None, "briefing": None}]
        with tempfile.TemporaryDirectory() as folder:
            manifest = run_dialogue(cast, self.scenario, backend, Path(folder) / "run", mode, use_skills)
            public = (Path(folder) / "run" / "public.jsonl").read_text()
        mine = [p for p in backend.payloads if p["role"]["id"] == "role1"]
        return mine, manifest, public

    def test_skills_reach_decision_in_every_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                payloads, manifest, _ = self.run_capture(mode)
                self.assertIn("skill_catalog", payloads[0])
                self.assertIn("active_skills", payloads[0])
                self.assertTrue(manifest["roles"]["role1"]["skills"]["enabled"])
                self.assertTrue(manifest["audit"]["passed"])

    def test_expression_gets_style_hints_only(self):
        payloads, _, _ = self.run_capture()
        decide, express = payloads[0], payloads[1]
        self.assertNotIn("active_skills", express)
        self.assertNotIn("skill_catalog", express)
        dumped = json.dumps(express, ensure_ascii=False)
        for skill in decide["active_skills"]:
            self.assertNotIn(skill["name"], dumped)
            for part in filter(None, (skill["decision"], skill["avoid"])):
                self.assertNotIn(json.dumps(part, ensure_ascii=False)[1:-1], dumped)
        self.assertEqual(len(express["skill_style"]), len(decide["active_skills"]))

    def test_no_skills_control_keeps_binding_on_record_only(self):
        payloads, manifest, _ = self.run_capture(use_skills=False)
        self.assertFalse(any("skill_catalog" in p or "skill_style" in p for p in payloads))
        self.assertFalse(manifest["roles"]["role1"]["skills"]["enabled"])
        self.assertEqual(manifest["roles"]["role1"]["skills"]["names"][0], "verify-before-commit")

    def test_without_binding_runs_as_before(self):
        payloads, manifest, _ = self.run_capture(binding=None)
        self.assertIsNone(manifest["roles"]["role1"]["skills"])
        self.assertFalse(any("skill_catalog" in p for p in payloads))

    def test_mock_follows_active_skills_when_cards_are_absent(self):
        bound = bind(self.binding, "role1", load_library())
        actions = {}
        for label, skills in (("with", bound), ("without", None)):
            agent = Agent(MockBackend(), self.scenario["allowed_actions"], "合作方负责人", "role2", "profile_only",
                          skills=skills)
            response = agent.step(StepRequest("role1", 1, "g", [cued_events()[0]], None, self.persona))
            actions[label] = response.actions[0].intent_note
        self.assertEqual(actions, {"with": "request_evidence", "without": "propose_plan"})

    def test_agent_rejects_nothing_when_no_skills_are_given(self):
        event = cued_events()[0]
        agent = Agent(MockBackend(), self.scenario["allowed_actions"], "合作方负责人", "role2")
        response = agent.step(StepRequest("role1", 1, self.scenario["background"], [event], None, self.persona))
        self.assertEqual(response.status, "ok")
        self.assertNotIn("skill_catalog", agent.last_payload)


if __name__ == "__main__":
    unittest.main()
