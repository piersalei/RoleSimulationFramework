"""The offline tools on a staged run directory: audit.py, package.py, readable.py."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from rolesim.audit import audit_run, write_report
from rolesim.loop import run_scene
from rolesim.model import MockModel
from rolesim.package import export_package
from rolesim.readable import FILENAME, render, write_readable
from rolesim.registry import Registry
from rolesim.roles import load_role, load_scene

REG = Registry.load()
CHECKS = {"trajectory_traceable", "operator_write_compatible", "evidence_visible", "prompt_isolation",
          "concealed_withheld", "expression_need_to_know", "unaware_withheld", "thoughts_self_only",
          "stable_unchanged", "fields_valid"}


def lines(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def write_lines(path, items):
    Path(path).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


class Offline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls.tmp.name) / "run"
        roles = [load_role(r) for r in ("role1", "role2", "role3")]
        cls.manifest = run_scene(roles, load_scene("test3"), MockModel(REG), cls.run_dir, REG)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def copy(self):
        target = Path(tempfile.mkdtemp()) / "run"
        shutil.copytree(self.run_dir, target)
        self.addCleanup(shutil.rmtree, target.parent)
        return target

    def trace(self, run, role="role1"):
        return run / "trace" / self.manifest["interaction_id"] / role

    def failed(self, run, role="role1"):
        return {c["id"] for c in audit_run(run, REG)["roles"][role]["checks"] if c["status"] == "fail"}

    # -- audit ----------------------------------------------------------------------------------
    def test_a_clean_run_passes_every_check(self):
        report = audit_run(self.run_dir, REG)
        self.assertTrue(report["passed"])
        self.assertTrue(self.manifest["audit_passed"])
        for role in ("role1", "role2", "role3"):
            self.assertEqual({c["id"] for c in report["roles"][role]["checks"]}, CHECKS)
        self.assertTrue((self.run_dir / "audit_report.md").exists())

    def test_the_audit_catches_tampering(self):
        secret = [v for v in json.loads((self.run_dir / "scene.json").read_text())["world_private"].values()
                  if isinstance(v, str) and len(v) >= 4][0]
        concealed = json.loads((self.run_dir / "roles" / "role1" / "setup.json").read_text())["briefing"]["conceal"][0]["text"]

        def tamper(name, change):
            run = self.copy()
            change(run)
            return self.failed(run)

        def leak(run):
            calls = lines(self.trace(run) / "model_calls.jsonl")
            calls[0]["input"]["state_text"] += secret
            write_lines(self.trace(run) / "model_calls.jsonl", calls)

        def unseen(run):
            phases = lines(self.trace(run) / "phases.jsonl")
            change = next(c for p in phases for c in p["commit"]["accepted"])
            change["evidence"][0]["event"] = "zz#9"
            write_lines(self.trace(run) / "phases.jsonl", phases)

        def stable(run):
            path = run / "roles" / "role1" / "final_state.json"
            state = json.loads(path.read_text())
            state["stable_snapshot_id"] = "other"
            path.write_text(json.dumps(state, ensure_ascii=False))

        def express_secret(run, extra):
            calls = lines(self.trace(run) / "model_calls.jsonl")
            next(c for c in calls if c["stage"] == "express")["input"]["state_text"] += extra
            write_lines(self.trace(run) / "model_calls.jsonl", calls)

        def chain(run):
            phases = lines(self.trace(run) / "phases.jsonl")
            phases[1]["input_state_id"] = "role1@nowhere"
            write_lines(self.trace(run) / "phases.jsonl", phases)

        def other_setup(run):
            hidden = json.loads((run / "roles" / "role2" / "setup.json").read_text())["briefing"]["conceal"][0]["text"]
            calls = lines(self.trace(run) / "model_calls.jsonl")
            calls[0]["input"]["state_text"] += hidden
            write_lines(self.trace(run) / "model_calls.jsonl", calls)

        self.assertIn("prompt_isolation", tamper("leak", leak))
        self.assertIn("prompt_isolation", tamper("other_setup", other_setup))
        self.assertIn("evidence_visible", tamper("unseen", unseen))
        self.assertIn("stable_unchanged", tamper("stable", stable))
        self.assertIn("concealed_withheld", tamper("secret", lambda r: express_secret(r, concealed)))
        self.assertIn("expression_need_to_know", tamper("ids", lambda r: express_secret(r, "d.relation.trust")))
        self.assertIn("trajectory_traceable", tamper("chain", chain))

    def test_write_report_and_refusals(self):
        run = self.copy()
        self.assertTrue(write_report(run, REG)["passed"])
        self.assertTrue((run / "roles" / "role2" / "audit_report.json").exists())
        old = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, old)
        (old / "manifest.json").write_text(json.dumps({"status": "completed", "cast": []}))
        with self.assertRaises(ValueError):
            audit_run(old, REG)

    # -- package ----------------------------------------------------------------------------------
    def test_package_units_and_blind(self):
        out = Path(tempfile.mkdtemp()) / "pkg"
        self.addCleanup(shutil.rmtree, out.parent)
        result = export_package([self.run_dir], out, seed=1)
        self.assertEqual(sorted(result["runs"]), ["R01", "R02", "R03"])
        key = json.loads((out / "blind_key.json").read_text())["runs"]
        self.assertEqual({k["character_id"] for k in key.values()}, {"role1", "role2", "role3"})
        self.assertEqual({k["mode"] for k in key.values()}, {"profile_state_mechanisms"})
        for unit in result["runs"]:
            folder = out / unit
            files = {p.name for p in folder.iterdir()}
            self.assertEqual(files, {"evaluator_samples.jsonl", "role_visible_trace.jsonl", "counterpart_truth.json",
                                     "role_setup.json", "annotation_template.json", "auto_audit.json"})
            samples = lines(folder / "evaluator_samples.jsonl")
            self.assertTrue(samples and all(s["stage"] in ("S1", "S2", "S3") for s in samples))
            for name in files - {"counterpart_truth.json"}:
                text = (folder / name).read_text(encoding="utf-8")
                for label in ("mock_fixture_not_model_evidence", "deterministic-stage-fixture", '"mode"', "use_skills"):
                    self.assertNotIn(label, text, (unit, name))
            truth = json.loads((folder / "counterpart_truth.json").read_text())
            self.assertIsNotNone(truth["world_private"])
            cid = key[unit]["character_id"]
            visible = lines(folder / "role_visible_trace.jsonl")
            self.assertEqual(len(visible), len([r for r in lines(self.run_dir / "records.jsonl")
                                                if r["actor"]["actor_id"] == cid or cid in r["recipients"]]))

    def test_package_refuses_failed_and_old_runs(self):
        run = self.copy()
        manifest = json.loads((run / "manifest.json").read_text())
        (run / "manifest.json").write_text(json.dumps({**manifest, "status": "failed"}))
        out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, out)
        with self.assertRaisesRegex(ValueError, "completed"):
            export_package([run], out / "pkg")
        (run / "manifest.json").write_text(json.dumps({k: v for k, v in manifest.items() if k != "pipeline"}))
        with self.assertRaisesRegex(ValueError, "staged pipeline"):
            export_package([run], out / "pkg2")

    # -- readable ----------------------------------------------------------------------------------
    def test_readable_is_deterministic_and_complete(self):
        first = render(self.run_dir)
        self.assertEqual(first, render(self.run_dir))
        self.assertEqual((self.run_dir / FILENAME).read_text(encoding="utf-8"), first)
        for part in ("## 角色", "## 互动记录", "## 各角色的内部过程", "林澄", "周越", "王岚", "事件建模", "行动计划", "写入"):
            self.assertIn(part, first)

    def test_failed_runs_still_render(self):
        run = self.copy()
        manifest = json.loads((run / "manifest.json").read_text())
        (run / "manifest.json").write_text(json.dumps({**manifest, "status": "failed", "error_type": "StageError",
                                                       "error": "S2: down"}, ensure_ascii=False))
        self.assertIn("失败原因：StageError：S2: down", Path(write_readable(run)).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
