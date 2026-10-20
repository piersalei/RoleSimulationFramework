import json
import tempfile
import unittest
from pathlib import Path

from rolesim.package import DIMENSIONS, export_package
from rolesim.providers import MockBackend
from rolesim.runtime import run_episode
from fixtures import load, setup


ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "HIDDEN_WORLD_SENTINEL_47"
HIDDEN_TEXT = "下周的人员安排可能改变"




def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.runs = []
        for mode in ("profile_only", "profile_state_mechanisms"):
            self.runs.append(base / mode)
            run_episode(load("role1"), load("test1"), MockBackend(),
                        base / mode, mode)
        self.out = base / "pkg"
        export_package(self.runs, self.out, seed=3)

    def folders(self):
        return sorted(p for p in self.out.iterdir() if p.is_dir())

    def test_layout_and_sample_counts(self):
        self.assertEqual([p.name for p in self.folders()], ["R01", "R02"])
        for folder in self.folders():
            samples = rows(folder / "evaluator_samples.jsonl")
            self.assertEqual(len(samples), 8)           # four turns x (step, perceive)
            self.assertEqual([s["phase"] for s in samples[:2]], ["step", "perceive"])
            self.assertTrue(all(s["post_state"] and s["delta"] and "mechanism_log" in s for s in samples))
            self.assertIsNone(samples[0]["pre_state"])
            self.assertIsNotNone(samples[0]["pre_state_note"])
            self.assertEqual(samples[3]["pre_state"]["state_id"], samples[2]["post_state"]["state_id"])
            self.assertIn("model_output", samples[0])
            self.assertNotIn("model_output", samples[1])
            template = json.loads((folder / "annotation_template.json").read_text())
            self.assertEqual(len(template["samples"]), 8)
            self.assertEqual(template["scoring_anchors"].keys(), {"0", "1", "2", "3"})

    def test_method_labels_stay_in_the_blind_key_only(self):
        key = json.loads((self.out / "blind_key.json").read_text())["runs"]
        self.assertEqual({v["mode"] for v in key.values()}, {"profile_only", "profile_state_mechanisms"})
        self.assertEqual(sorted(v["run"] for v in key.values()), ["profile_only", "profile_state_mechanisms"])
        for folder in self.folders():
            blob = "".join(p.read_text() for p in folder.iterdir())
            for label in ("profile_only", "profile_state", "mock_fixture", "deterministic-fixture",
                          "active_mechanisms", "current_state", "controller_input"):
                self.assertNotIn(label, blob, f"{folder.name} leaks {label}")

    def test_seed_controls_assignment_and_is_reproducible(self):
        key = lambda out: json.loads((out / "blind_key.json").read_text())["runs"]
        other = Path(self.tmp.name) / "again"
        export_package(self.runs, other, seed=3)
        self.assertEqual(key(self.out), key(other))
        assignments = set()
        for seed in range(6):
            out = Path(self.tmp.name) / f"s{seed}"
            export_package(self.runs, out, seed=seed)
            assignments.add(json.loads((out / "blind_key.json").read_text())["runs"]["R01"]["mode"])
        self.assertEqual(assignments, {"profile_only", "profile_state_mechanisms"})

    def test_world_truth_only_in_counterpart_file(self):
        for folder in self.folders():
            truth = (folder / "counterpart_truth.json").read_text()
            self.assertIn(SENTINEL, truth)
            self.assertIn(HIDDEN_TEXT, truth)
            for name in ("role_visible_trace.jsonl", "evaluator_samples.jsonl", "annotation_template.json",
                         "auto_audit.json"):
                text = (folder / name).read_text()
                self.assertNotIn(SENTINEL, text, name)
                self.assertNotIn(HIDDEN_TEXT, text, name)
                self.assertNotIn("partner_private", text, name)

    def test_role_visible_trace_has_no_internal_state_or_reasoning(self):
        for folder in self.folders():
            trace = rows(folder / "role_visible_trace.jsonl")
            self.assertTrue(trace)
            for item in trace:
                self.assertEqual(set(item) - {"action"}, {"turn", "speaker", "kind", "text"})
            blob = (folder / "role_visible_trace.jsonl").read_text()
            for forbidden in ("dynamic", "module_status", "thought", "离线夹具", "evidence_ids"):
                self.assertNotIn(forbidden, blob)
            self.assertTrue(any(i.get("action") for i in trace))

    def test_trigger_events_are_role_visible_only(self):
        for folder in self.folders():
            for sample in rows(folder / "evaluator_samples.jsonl"):
                for event in sample["trigger_events"] + sample["related_history"]:
                    self.assertNotEqual(event["event_id"], "partner_private")

    def test_refuses_overwrite_empty_input_and_unfinished_runs(self):
        with self.assertRaises(FileExistsError):
            export_package(self.runs, self.out)
        with self.assertRaises(ValueError):
            export_package([], Path(self.tmp.name) / "empty")
        broken = Path(self.tmp.name) / "broken"
        broken.mkdir()
        (broken / "manifest.json").write_text(json.dumps({"status": "failed"}))
        with self.assertRaises(ValueError):
            export_package([broken], Path(self.tmp.name) / "bad")

    def test_skills_control_pair_stays_blind_and_role_setup_carries_briefing(self):
        base = Path(self.tmp.name)
        pair = [base / "with_skills", base / "no_skills"]
        for run, use in zip(pair, (True, False)):
            run_episode(load("role1"), load("test1"), MockBackend(), run,
                        skills=load("role1.skills"), use_skills=use, briefing=setup("role1"))
        out = base / "skills_pkg"
        export_package(pair, out, seed=1)
        key = json.loads((out / "blind_key.json").read_text())["runs"]
        self.assertEqual(sorted(v["skills"]["enabled"] for v in key.values()), [False, True])
        setups = [(out / blind / "role_setup.json").read_text() for blind in key]
        self.assertEqual(setups[0], setups[1])
        shared = json.loads(setups[0])
        self.assertEqual(shared["briefing"]["conceal"][0]["id"], "c1")
        self.assertEqual(shared["bound_skills"][0], "verify-before-commit")
        for blind in key:
            blob = "".join(p.read_text() for p in (out / blind).iterdir())
            for label in ("skill_catalog", "active_skills", "skill_style", '"enabled"'):
                self.assertNotIn(label, blob)

    def test_package_declares_unfrozen_protocol_and_dimensions(self):
        meta = json.loads((self.out / "package.json").read_text())
        self.assertEqual(meta["versions"]["evaluation_protocol_version"], "unfrozen")
        self.assertEqual(meta["dimensions"], DIMENSIONS)
        self.assertEqual(len(DIMENSIONS), 9)
        self.assertIn("not the role", meta["evidence_boundary"])


if __name__ == "__main__":
    unittest.main()
