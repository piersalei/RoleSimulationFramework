import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim import vocab
from rolesim.role.skills import (ACTIONS, BINDING_VERSION, CUES, LIBRARY, bind, load_library, parse_skill,
                            select)
from fixtures import load, setup


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


if __name__ == "__main__":
    unittest.main()
