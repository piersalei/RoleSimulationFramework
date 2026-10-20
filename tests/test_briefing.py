import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.audit import audit_run
from rolesim.briefing import mentions, validate_briefing
from rolesim.providers import MockBackend
from rolesim.runtime import make_agent, validate_scenario
from fixtures import load, run_scene, setup



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
            make_agent(load("role1"), self.scenario, MockBackend(), 0, "role2", briefing=clash)

    def test_mentions_use_markers(self):
        self.assertEqual(mentions("第三批还在处理", self.briefing["conceal"]), ["c1"])
        self.assertEqual(mentions("先交付前两批", self.briefing["disclose"]), ["d1"])
        self.assertEqual(mentions("稍后答复", self.briefing["conceal"]), [])


class BriefingRunTests(unittest.TestCase):
    def setUp(self):
        self.briefing = setup("role1")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_with(self, backend, name="run"):
        out = Path(self.tmp.name) / name
        return run_scene(out, backend), out

    def own(self, payloads, role="role1"):
        return [p for p in payloads if p["role"]["id"] == role]

    def test_recap_is_seeded_into_memory_and_citable(self):
        backend = Capture(cite="recap:r1")
        manifest, out = self.run_with(backend)
        decides = [p for p in self.own(backend.payloads) if "allowed_actions" in p]
        history = decides[0]["visible_history"]
        self.assertEqual(history[0]["id"], "recap:r1")
        self.assertEqual(history[0]["type"], "recap")
        self.assertTrue(manifest["roles"]["role1"]["briefing"])
        self.assertTrue(manifest["audit"]["passed"])
        first = json.loads((out / "roles" / "role1" / "states.jsonl").read_text().splitlines()[0])
        self.assertNotIn("recap:r1", first["memory"])

    def test_concealed_text_reaches_decision_but_never_expression(self):
        backend = Capture()
        _, out = self.run_with(backend)
        secret = self.briefing["conceal"][0]["text"]
        mine = self.own(backend.payloads)
        decides = [p for p in mine if "allowed_actions" in p]
        expresses = [p for p in mine if "action_meaning" in p]
        self.assertEqual(len(decides), 4)
        self.assertTrue(all(p["briefing"]["keep_private"][0]["text"] == secret for p in decides))
        self.assertFalse(any(secret in json.dumps(p, ensure_ascii=False) for p in expresses))
        self.assertTrue(all(p["disclosure"]["may_share"] == [self.briefing["disclose"][0]["text"]] for p in expresses))
        report = audit_run(out)["roles"]["role1"]
        check = next(c for c in report["checks"] if c["id"] == "concealed_info_withheld")
        self.assertEqual((check["status"], check["checked"]), ("pass", 4))

    def test_spoken_secret_fails_audit_and_disclosure_is_only_counted(self):
        manifest, _ = self.run_with(Capture(speech="第三批其实还没清洗完。"), "leak")
        self.assertIn("concealed_info_withheld", manifest["audit"]["failed_checks"]["role1"])
        self.assertNotIn("concealed_info_withheld", manifest["audit"]["failed_checks"]["role2"])
        _, out = self.run_with(Capture(speech="前两批已经核对完，可以先交付。"), "share")
        report = audit_run(out)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["roles"]["role1"]["disclosed_items"]), 4)

    def test_secret_in_expression_prompt_fails_audit(self):
        _, out = self.run_with(Capture())
        path = out / "roles" / "role1" / "audit.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines()]
        rows[0]["expression_input"]["leak"] = self.briefing["conceal"][0]["text"]
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        failed = [c["id"] for c in audit_run(out)["roles"]["role1"]["checks"] if c["status"] == "fail"]
        self.assertEqual(failed, ["concealed_info_withheld"])


if __name__ == "__main__":
    unittest.main()
