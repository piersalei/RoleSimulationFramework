import datetime
import json
import re
import tempfile
import unittest
from pathlib import Path

from fixtures import run_scene
from rolesim.providers import MockBackend
from rolesim.readable import FILENAME, render, write_readable
from rolesim.runtime import run_name
from rolesim.vocab import ACTION_TEXT


class ReadableTests(unittest.TestCase):
    def test_finished_run_writes_transcript_with_scene_roles_and_every_line(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            run_scene(output)
            text = (output / FILENAME).read_text(encoding="utf-8")
            scenario = json.loads((output / "roles" / "role1" / "inputs.json").read_text(encoding="utf-8"))["scenario"]
            self.assertIn(scenario["background"], text)
            self.assertIn("林澄", text)
            self.assertIn("周越", text)
            spoken = [json.loads(line) for line in (output / "public.jsonl").read_text(encoding="utf-8").splitlines()]
            for line in spoken:
                self.assertIn(line["text"], text)
            for line in spoken[1:]:
                self.assertIn(ACTION_TEXT[line["action"]], text)
            self.assertEqual(len(re.findall(r"^### 第 \d+ 句", text, re.M)), len(spoken) - 1)
            self.assertIn("自动审计：通过", text)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("started_at", manifest)
            self.assertIn("finished_at", manifest)

    def test_failed_run_still_gets_a_transcript(self):
        class Broken(MockBackend):
            def decide(self, payload):
                return {"action": "invent", "evidence_ids": [], "brief_reason": "bad"}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            with self.assertRaises(ValueError):
                run_scene(output, Broken())
            text = (output / FILENAME).read_text(encoding="utf-8")
            self.assertIn("状态：failed", text)
            self.assertIn("infeasible action", text)

    def test_rewriting_is_deterministic(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            run_scene(output)
            before = (output / FILENAME).read_text(encoding="utf-8")
            write_readable(output)
            self.assertEqual((output / FILENAME).read_text(encoding="utf-8"), before)

    def test_rejects_directories_that_are_not_two_role_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "manifest.json").write_text('{"schema_version": "1.0"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                render(folder)

    def test_run_names_start_with_local_time(self):
        when = datetime.datetime(2026, 10, 20, 6, 43, 5)
        self.assertEqual(run_name("test1", MockBackend.model, when), "20261020-064305_test1_mock")
        self.assertEqual(run_name("test2", "Qwen/Qwen3 8B", when), "20261020-064305_test2_Qwen-Qwen3-8B")


if __name__ == "__main__":
    unittest.main()
