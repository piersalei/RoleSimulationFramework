import json
import tempfile
import unittest
from pathlib import Path

from rolesim.package import DIMENSIONS, export_package
from fixtures import run_scene, setup


SENTINEL = "HIDDEN_WORLD_SENTINEL_47"




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
            run_scene(base / mode, mode=mode)
        self.out = base / "pkg"
        export_package(self.runs, self.out, seed=3)

    def folders(self):
        return sorted(p for p in self.out.iterdir() if p.is_dir())

    def test_layout_and_sample_counts(self):
        # Two runs x two roles; each role speaks four times -> (step, perceive) x 4.
        self.assertEqual([p.name for p in self.folders()], ["R01", "R02", "R03", "R04"])
        for folder in self.folders():
            samples = rows(folder / "evaluator_samples.jsonl")
            self.assertEqual(len(samples), 8)
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
        self.assertEqual(sorted((v["run"], v["role_id"]) for v in key.values()),
                         [(m, r) for m in ("profile_only", "profile_state_mechanisms") for r in ("role1", "role2")])
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
        for seed in range(8):
            out = Path(self.tmp.name) / f"s{seed}"
            export_package(self.runs, out, seed=seed)
            assignments.add(json.loads((out / "blind_key.json").read_text())["runs"]["R01"]["mode"])
        self.assertEqual(assignments, {"profile_only", "profile_state_mechanisms"})

    def test_world_truth_and_the_other_role_only_in_counterpart_file(self):
        key = json.loads((self.out / "blind_key.json").read_text())["runs"]
        for folder in self.folders():
            role = key[folder.name]["role_id"]
            other = {"role1": "role2", "role2": "role1"}[role]
            other_secret = setup(other)["conceal"][0]["text"]
            truth = json.loads((folder / "counterpart_truth.json").read_text())
            self.assertIn(SENTINEL, json.dumps(truth, ensure_ascii=False))
            (counterpart,) = truth["counterparts"].values()
            self.assertEqual(counterpart["briefing"]["conceal"][0]["text"], other_secret)
            self.assertIn("dynamic", counterpart["final_state"])
            for name in ("role_visible_trace.jsonl", "evaluator_samples.jsonl", "annotation_template.json",
                         "auto_audit.json", "role_setup.json"):
                text = (folder / name).read_text()
                self.assertNotIn(SENTINEL, text, name)
                self.assertNotIn(other_secret, text, name)

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

    def test_trace_names_speakers_by_seat(self):
        for folder in self.folders():
            speakers = {item["speaker"] for item in rows(folder / "role_visible_trace.jsonl")}
            self.assertEqual(speakers, {"env", "项目组数据负责人", "合作方负责人"})

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
            run_scene(run, use_skills=use)
        out = base / "skills_pkg"
        export_package(pair, out, seed=1)
        key = json.loads((out / "blind_key.json").read_text())["runs"]
        mine = [b for b, v in key.items() if v["role_id"] == "role1"]
        self.assertEqual(sorted(key[b]["skills"]["enabled"] for b in mine), [False, True])
        setups = [(out / blind / "role_setup.json").read_text() for blind in mine]
        self.assertEqual(setups[0], setups[1])
        shared = json.loads(setups[0])
        self.assertEqual(shared["seat"], "项目组数据负责人")
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
