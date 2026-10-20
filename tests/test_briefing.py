import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.audit import audit_run
from rolesim.briefing import mentions, validate_briefing
from rolesim.providers import MockBackend
from rolesim.runtime import make_agent, run_episode, validate_scenario
from fixtures import load, setup



class Capture(MockBackend):
    def __init__(self, speech=None, cite=None):
        self.payloads, self.speech, self.cite = [], speech, cite

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        decision = super().decide(payload)
        if self.cite:
            decision["evidence_ids"] = [self.cite]
        return decision

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return self.speech or super().express(payload)


class BriefingValidationTests(unittest.TestCase):
    def setUp(self):
        self.scenario = load("test1")
        self.briefing = setup("role1")

    def test_example_scene_is_valid(self):
        validate_scenario(self.scenario)
        validate_briefing(self.briefing, {"e1"})

    def test_invalid_briefings_are_rejected(self):
        def patched(fn):
            data = copy.deepcopy(self.briefing)
            fn(data)
            return data
        cases = {
            "direct lie": patched(lambda b: b["conceal"][0].update(strategy="direct_lie")),
            "recap id prefix": patched(lambda b: b["recap"][0].update(id="r1")),
            "prefix on non-recap": patched(lambda b: b["disclose"][0].update(id="recap:d1")),
            "event id collision": patched(lambda b: b["disclose"][0].update(id="e1")),
            "duplicate id": patched(lambda b: b["conceal"][0].update(id="d1")),
            "empty markers": patched(lambda b: b["disclose"][0].update(markers=[])),
            "one-char marker": patched(lambda b: b["disclose"][0].update(markers=["批"])),
            "recap repeats a secret": patched(lambda b: b["recap"][0].update(text="其实第三批还没清洗完。")),
            "missing goal": patched(lambda b: b.pop("goal")),
        }
        for label, data in cases.items():
            with self.subTest(case=label), self.assertRaises(ValueError):
                validate_briefing(data, {"e1"})

    def test_scene_is_background_only(self):
        for broken in (dict(self.scenario, schema_version="1.1"),
                       dict(self.scenario, briefings={"*": self.briefing}),
                       dict(self.scenario, goal="指导角色的目标")):
            with self.assertRaises(ValueError):
                validate_scenario(broken)

    def test_setup_ids_must_not_collide_with_scene_events(self):
        clash = copy.deepcopy(self.briefing)
        clash["disclose"][0]["id"] = "e2"
        with self.assertRaisesRegex(ValueError, "e2"):
            make_agent(load("role1"), self.scenario, MockBackend(), briefing=clash)

    def test_mentions_use_markers(self):
        self.assertEqual(mentions("第三批还在处理", self.briefing["conceal"]), ["c1"])
        self.assertEqual(mentions("先交付前两批", self.briefing["disclose"]), ["d1"])
        self.assertEqual(mentions("稍后答复", self.briefing["conceal"]), [])


class BriefingRunTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")
        self.scenario = load("test1")
        self.briefing = setup("role1")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_with(self, backend, name="run"):
        out = Path(self.tmp.name) / name
        manifest = run_episode(self.persona, self.scenario, backend, out, briefing=self.briefing)
        return manifest, out

    def test_recap_is_seeded_into_memory_and_citable(self):
        backend = Capture(cite="recap:r1")
        manifest, out = self.run_with(backend)
        history = backend.payloads[0]["visible_history"]
        self.assertEqual(history[0]["id"], "recap:r1")
        self.assertEqual(history[0]["type"], "recap")
        self.assertTrue(manifest["briefing"])
        self.assertTrue(manifest["audit"]["passed"])
        first = json.loads((out / "states.jsonl").read_text().splitlines()[0])
        self.assertNotIn("recap:r1", first["memory"])

    def test_concealed_text_reaches_decision_but_never_expression(self):
        backend = Capture()
        manifest, out = self.run_with(backend)
        secret = self.briefing["conceal"][0]["text"]
        decides, expresses = backend.payloads[0::2], backend.payloads[1::2]
        self.assertTrue(all(p["briefing"]["keep_private"][0]["text"] == secret for p in decides))
        self.assertFalse(any(secret in json.dumps(p, ensure_ascii=False) for p in expresses))
        self.assertTrue(all(p["disclosure"]["may_share"] == [self.briefing["disclose"][0]["text"]] for p in expresses))
        report = audit_run(out)
        check = next(c for c in report["checks"] if c["id"] == "concealed_info_withheld")
        self.assertEqual((check["status"], check["checked"]), ("pass", 4))

    def test_spoken_secret_fails_audit_and_disclosure_is_only_counted(self):
        manifest, _ = self.run_with(Capture(speech="第三批其实还没清洗完。"), "leak")
        self.assertIn("concealed_info_withheld", manifest["audit"]["failed_checks"])
        _, out = self.run_with(Capture(speech="前两批已经核对完，可以先交付。"), "share")
        report = audit_run(out)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["disclosed_items"]), 4)

    def test_secret_in_expression_prompt_fails_audit(self):
        _, out = self.run_with(Capture())
        rows = [json.loads(l) for l in (out / "audit.jsonl").read_text().splitlines()]
        rows[0]["expression_input"]["leak"] = self.briefing["conceal"][0]["text"]
        (out / "audit.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        failed = [c["id"] for c in audit_run(out)["checks"] if c["status"] == "fail"]
        self.assertEqual(failed, ["concealed_info_withheld"])


if __name__ == "__main__":
    unittest.main()
