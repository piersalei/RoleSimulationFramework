import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import ROOT, load, role_file
from rolesim.run.loop import run_scene
from rolesim.stage.model import MockModel
from rolesim.role.registry import Registry
from rolesim.role.roles import ROLES_DIR, SCENES_DIR, available, fit, fit_cast, load_role, load_scene


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
    def test_shipped_roles_and_scenes_load_and_the_cast_fits(self):
        self.assertEqual(available(ROLES_DIR), ["role1", "role2", "role3"])
        self.assertEqual(available(SCENES_DIR), ["test1", "test2", "test3"])
        roles = [load_role(r) for r in available(ROLES_DIR)]
        self.assertEqual([r["id"] for r in roles], ["role1", "role2", "role3"])
        for scene_id, cast in (("test1", roles[:2]), ("test2", roles[:2]), ("test3", roles)):
            result = fit_cast(cast, load_scene(scene_id))
            self.assertTrue(result["ok"])
            self.assertEqual(result["notes"], [])

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
        self.cast = [self.role, load_role("role2")]

    def test_notes_skills_that_can_never_activate(self):
        role = copy.deepcopy(self.role)
        role["setup"]["conceal"] = []
        notes = fit(role, self.scene)["notes"]
        self.assertTrue(any("selective-disclosure: never active" in n for n in notes))

    def test_setup_errors_block_seating(self):
        role = copy.deepcopy(self.role)
        role["setup"]["disclose"][0]["id"] = "open"
        self.assertTrue(fit(role, self.scene)["errors"][0].startswith("setup:"))

    def test_cast_needs_one_distinct_role_per_seat(self):
        registry = Registry.load()
        for cast in ([self.role], [self.role, self.role], self.cast + [self.role]):
            with self.subTest(n=len(cast)):
                self.assertFalse(fit_cast(cast, self.scene)["ok"])
                with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "does not fit"):
                    run_scene(cast, self.scene, MockModel(registry), Path(tmp) / "run", registry)


class RunTests(unittest.TestCase):
    def test_no_skills_control_needs_skills(self):
        data = role_file("role1")
        data["skills"] = []
        other = role_file("role2")
        other["skills"] = []
        scratch = Scratch(self)
        roles = [load_role(scratch.write("role1", data)), load_role(scratch.write("role2", other))]
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "lists skills"):
            run_scene(roles, load_scene("test1"), MockModel(Registry.load()), Path(tmp) / "run", use_skills=False)

    def test_bootstrap_reproduces_the_shipped_files(self):
        import subprocess, sys
        before = {p: p.read_bytes() for p in list(ROLES_DIR.glob("*.json")) + list(SCENES_DIR.glob("*.json"))}
        subprocess.run([sys.executable, "scripts/bootstrap_examples.py"], cwd=ROOT, check=True,
                       env={"PYTHONPATH": str(ROOT), "PATH": ""})
        self.assertEqual(before, {p: p.read_bytes() for p in before})


if __name__ == "__main__":
    unittest.main()
