import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import ROOT, load, role_file
from rolesim.agent import Agent
from rolesim.contract import Event, StepRequest, Visibility
from rolesim.providers import MockBackend
from rolesim.roles import ROLES_DIR, SCENES_DIR, available, fit, load_role, load_scene, run, seat
from rolesim.runtime import run_episode


class Scratch:
    """A temporary roles/ or scenes/ folder."""

    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, name, data):
        path = self.root / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False))
        return path


class RoleFileTests(unittest.TestCase):
    def test_shipped_roles_and_scenes_load_and_all_pairs_fit(self):
        self.assertEqual(available(ROLES_DIR), ["role1", "role2"])
        self.assertEqual(available(SCENES_DIR), ["test1", "test2"])
        for role_id in available(ROLES_DIR):
            role = load_role(role_id)
            self.assertEqual(role["id"], role_id)
            for scene_id in available(SCENES_DIR):
                self.assertTrue(fit(role, load_scene(scene_id))["ok"])

    def test_id_or_path(self):
        self.assertEqual(load_role("role1")["persona"], load_role(str(ROLES_DIR / "role1.json"))["persona"])
        with self.assertRaisesRegex(ValueError, "not found"):
            load_role("role9")

    def test_file_name_must_be_the_id(self):
        scratch = Scratch(self)
        with self.assertRaisesRegex(ValueError, "file name must be the persona id"):
            load_role(scratch.write("someone", role_file("role1")))
        with self.assertRaisesRegex(ValueError, "file name must be the scene id"):
            load_scene(scratch.write("other", load("test1")))

    def test_skills_and_setup_are_optional(self):
        data = role_file("role1")
        data.update(skills=[], setup=None)
        role = load_role(Scratch(self).write("role1", data))
        self.assertIsNone(role["bound"])
        self.assertIsNone(role["binding"])
        self.assertIsNone(role["setup"])

    def test_invalid_role_files_are_rejected(self):
        cases = {
            "extra field": lambda d: d.update(extra=1),
            "wrong format": lambda d: d.update(format="role-0.9"),
            "unknown skill": lambda d: d["skills"].append({"name": "no-such-skill", "priority": .5, "note": "x"}),
            "skills not a list": lambda d: d.update(skills={}),
            "bad setup": lambda d: d["setup"]["conceal"][0].update(strategy="direct_lie"),
            "bad persona": lambda d: d["persona"].pop("stable"),
        }
        for label, change in cases.items():
            data = role_file("role1")
            change(data)
            with self.subTest(case=label), self.assertRaises(ValueError):
                load_role(Scratch(self).write("role1", data))


class FitTests(unittest.TestCase):
    def setUp(self):
        self.role, self.scene = load_role("role1"), load_scene("test1")

    def test_notes_skills_that_can_never_activate(self):
        scene, role = copy.deepcopy(self.scene), copy.deepcopy(self.role)
        scene["scenario"]["allowed_actions"] = ["wait"]
        role["setup"]["conceal"] = []
        notes = fit(role, scene)["notes"]
        self.assertTrue(any("verify-before-commit: never active" in n for n in notes))
        self.assertTrue(any("selective-disclosure: never active" in n for n in notes))

    def test_errors_block_seating(self):
        role = copy.deepcopy(self.role)
        role["persona"]["stable"]["mechanism_priors"].pop("verify")
        self.assertFalse(fit(role, self.scene)["ok"])
        with self.assertRaisesRegex(ValueError, "does not fit"):
            seat(role, self.scene, MockBackend())
        role = copy.deepcopy(self.role)
        role["setup"]["disclose"][0]["id"] = "e1"
        self.assertTrue(fit(role, self.scene)["errors"][0].startswith("setup:"))


class SeatAndRunTests(unittest.TestCase):
    def test_seat_gives_an_environment_a_ready_agent(self):
        role, scene = load_role("role2"), load_scene("test2")
        seated = seat(role, scene, MockBackend())
        self.assertIsInstance(seated.agent, Agent)
        self.assertEqual(seated.agent.allowed_actions, scene["scenario"]["allowed_actions"])
        self.assertEqual(seated.agent.briefing, role["setup"])
        self.assertEqual(seated.scene_context, scene["scenario"]["background"])
        self.assertIsNone(seat(role, scene, MockBackend(), use_skills=False).agent.skills)
        event = Event(event_id="x1", turn=1, type="narration", actor="env", content="合作方催促交付。",
                      visibility=Visibility("public", [seated.role_id, "partner"]),
                      signals={"cues": {"pressure": .8}, "reliability_signal": 0})
        response = seated.agent.step(StepRequest(role_id=seated.role_id, turn=1, scene_context=seated.scene_context,
                                                 new_events=[event], prev_state_id=None,
                                                 role_profile=seated.role_profile, allowed_kinds=["speech"]))
        self.assertEqual(response.status, "ok")

    def test_swapping_the_role_swaps_only_the_role(self):
        scene = load_scene("test2")
        with tempfile.TemporaryDirectory() as tmp:
            runs = {r: run(load_role(r), scene, MockBackend(), Path(tmp) / r) for r in ("role1", "role2")}
        for role_id, manifest in runs.items():
            self.assertEqual(manifest["turns"], 5)
            self.assertTrue(manifest["audit"]["passed"])
            self.assertEqual(manifest["origin"]["role"]["id"], role_id)
            self.assertEqual(manifest["origin"]["scene"]["id"], "test2")
        self.assertEqual(runs["role1"]["scenario_hash"], runs["role2"]["scenario_hash"])
        self.assertNotEqual(runs["role1"]["persona_hash"], runs["role2"]["persona_hash"])

    def test_no_skills_control_needs_skills(self):
        data = role_file("role1")
        data["skills"] = []
        role = load_role(Scratch(self).write("role1", data))
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "lists skills"):
            run(role, load_scene("test1"), MockBackend(), Path(tmp) / "run", use_skills=False)

    def test_role_file_run_matches_the_raw_call(self):
        role, scene = load_role("role1"), load_scene("test1")
        with tempfile.TemporaryDirectory() as tmp:
            run(role, scene, MockBackend(), Path(tmp) / "file")
            raw = run_episode(role["persona"], scene["scenario"], MockBackend(), Path(tmp) / "raw",
                              skills=role["binding"], briefing=role["setup"])
            self.assertEqual((Path(tmp) / "file" / "public.jsonl").read_text(),
                             (Path(tmp) / "raw" / "public.jsonl").read_text())
        self.assertIsNone(raw["origin"])

    def test_bootstrap_reproduces_the_shipped_files(self):
        import subprocess, sys
        before = {p: p.read_bytes() for p in list(ROLES_DIR.glob("*.json")) + list(SCENES_DIR.glob("*.json"))}
        subprocess.run([sys.executable, "scripts/bootstrap_examples.py"], cwd=ROOT, check=True,
                       env={"PYTHONPATH": str(ROOT), "PATH": ""})
        self.assertEqual(before, {p: p.read_bytes() for p in before})


if __name__ == "__main__":
    unittest.main()
