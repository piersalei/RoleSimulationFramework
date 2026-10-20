import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.catalog import build_catalog, digest
from rolesim.persona import compile_views, validate_persona
from rolesim.providers import MockBackend, strict_json
from rolesim.agent import Agent, MODES, initial_state, make_payload, observe, observation, validate_decision
from rolesim.contract import ProposedAction, Visibility
from rolesim.runtime import ScriptedEnvironment, code_version, run_episode, validate_scenario

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    folder = "scenes" if name == "cooperation_conflict.json" else "personas"
    return json.loads((ROOT / "examples" / folder / name).read_text())


def scheduled(scenario, role_id="cautious"):
    return [event for _, event in ScriptedEnvironment(scenario, role_id).scheduled()]


def propose(action, role_id="cautious", turn=1):
    return ProposedAction(f"{role_id}@t{turn:03d}#1", "speech", "……", Visibility("public", [role_id, "partner"]),
                          ["partner"], action)


class CaptureBackend(MockBackend):
    def __init__(self):
        self.payloads = []

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("cautious.json")
        self.scenario = load("cooperation_conflict.json")

    def test_hidden_world_and_partner_information_never_reaches_either_call(self):
        backend = CaptureBackend()
        with tempfile.TemporaryDirectory() as folder:
            run_episode(self.persona, self.scenario, backend, Path(folder) / "run")
        calls = json.dumps(backend.payloads, ensure_ascii=False)
        self.assertNotIn("HIDDEN_WORLD_SENTINEL", calls)
        self.assertNotIn("下周的人员安排", calls)
        self.assertNotIn("system_truth", calls)
        self.assertEqual(len(backend.payloads), 8)

    def test_views_do_not_expose_psychological_truth_to_other_roles(self):
        views = compile_views(self.persona)
        self.assertEqual(set(views["other_visible"]), {"id", "name", "description"})
        self.assertNotIn("stable", views["role_self"])
        views["system_truth"]["stable"]["traits"]["attachment"]["anxiety"] = -2
        self.assertEqual(self.persona["stable"]["traits"]["attachment"]["anxiety"], .8)

    def test_repeated_and_invisible_events_cannot_change_state(self):
        events = scheduled(self.scenario)
        hidden = copy.deepcopy(events[0])
        hidden.event_id, hidden.visibility = "partner_private", Visibility("private", ["partner"])
        state = initial_state()
        observe(state, events[0], self.persona)
        prior = copy.deepcopy(state)
        self.assertIsNone(observe(state, events[0], self.persona))
        self.assertIsNone(observe(state, hidden, self.persona))
        self.assertEqual(state, prior)

    def test_same_observation_has_persona_conditioned_state_response(self):
        a, b = initial_state(), initial_state()
        observe(a, scheduled(self.scenario)[0], self.persona)
        observe(b, scheduled(self.scenario, "assertive")[0], load("assertive.json"))
        self.assertGreater(a["dynamic"]["affect_state"]["emotion"]["intensity"], b["dynamic"]["affect_state"]["emotion"]["intensity"])
        self.assertEqual(a["dynamic"]["relation_state"], b["dynamic"]["relation_state"])

    def test_promises_and_own_plans_do_not_count_as_fulfilment(self):
        events = scheduled(self.scenario)
        state = initial_state()
        observe(state, events[2], self.persona)
        _, outcomes = ScriptedEnvironment(self.scenario, "cautious").adjudicate(propose("propose_plan"), 1)
        self.assertEqual(outcomes[0].outcome["status"], "partial")
        observe(state, outcomes[0], self.persona)
        self.assertEqual(state["dynamic"]["relation_state"]["partner"]["trust"], .55)
        observe(state, events[3], self.persona)
        self.assertGreater(state["dynamic"]["relation_state"]["partner"]["trust"], .55)

    def test_unavailable_record_is_not_revealed_by_request_action(self):
        self.scenario["evidence"]["readers"] = ["partner"]
        _, outcomes = ScriptedEnvironment(self.scenario, "cautious").adjudicate(propose("request_evidence"), 1)
        self.assertNotEqual(outcomes[0].event_id, "status_receipt")
        self.assertEqual(outcomes[0].outcome["status"], "failed")
        self.assertNotIn("部分数据仍在复核", outcomes[0].content)

    def test_action_and_evidence_validation_rejects_fabrication(self):
        state = initial_state()
        event = scheduled(self.scenario)[0]
        observe(state, event, self.persona)
        payload = make_payload(self.persona, state, observation(event), self.scenario["goal"],
                               self.scenario["partner"], self.scenario["allowed_actions"], "profile_state_mechanisms")
        valid = {"action": "wait", "evidence_ids": ["e1"], "brief_reason": "需要核对"}
        validate_decision(valid, payload)
        for patch in ({"action": "declare_success"}, {"evidence_ids": ["partner_private"]},
                      {"stable_patch": {"values": {}}}, {"evidence_ids": []}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_decision({**valid, **patch}, payload)

    def test_failed_model_is_recorded_without_mock_fallback(self):
        class Broken(MockBackend):
            def decide(self, payload):
                return {"action": "invent", "evidence_ids": [], "brief_reason": "bad"}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "run"
            with self.assertRaises(ValueError):
                run_episode(self.persona, self.scenario, Broken(), output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual((output / "audit.jsonl").read_text(), "")

    def test_episode_reproducible_and_stable_persona_immutable(self):
        before = copy.deepcopy(self.persona)
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "a", Path(folder) / "b"
            for target in (a, b):
                run_episode(self.persona, self.scenario, MockBackend(), target)
            self.assertEqual((a / "audit.jsonl").read_bytes(), (b / "audit.jsonl").read_bytes())
            self.assertEqual((a / "manifest.json").read_bytes(), (b / "manifest.json").read_bytes())
            with self.assertRaises(FileExistsError):
                run_episode(self.persona, self.scenario, MockBackend(), a)
        self.assertEqual(self.persona, before)

    def test_profile_only_uses_same_observation_without_mechanism_or_state_prompt(self):
        state = initial_state()
        event = scheduled(self.scenario)[0]
        observe(state, event, self.persona)
        args = (observation(event), self.scenario["goal"], self.scenario["partner"], self.scenario["allowed_actions"])
        a = make_payload(self.persona, state, *args, "profile_state_mechanisms")
        b = make_payload(self.persona, state, *args, "profile_only")
        self.assertEqual(a["visible_history"], b["visible_history"])
        self.assertNotIn("active_mechanisms", b)
        self.assertNotIn("current_state", b)

    def test_ablation_levels_add_state_then_mechanisms(self):
        state = initial_state()
        event = scheduled(self.scenario)[0]
        observe(state, event, self.persona)
        args = (observation(event), self.scenario["goal"], self.scenario["partner"], self.scenario["allowed_actions"])
        payloads = {m: make_payload(self.persona, state, *args, m) for m in MODES}
        self.assertEqual(set(payloads["profile_only"]) & {"current_state", "active_mechanisms"}, set())
        self.assertIn("current_state", payloads["profile_state"])
        self.assertNotIn("active_mechanisms", payloads["profile_state"])
        self.assertIn("active_mechanisms", payloads["profile_state_mechanisms"])
        base = {k: v for k, v in payloads["profile_only"].items()}
        for mode in ("profile_state", "profile_state_mechanisms"):
            self.assertEqual({k: v for k, v in payloads[mode].items() if k in base}, base)
        for old in ("mechanisms", "profile", ""):
            with self.subTest(mode=old), self.assertRaises(ValueError):
                make_payload(self.persona, state, *args, old)
            with self.assertRaises(ValueError):
                Agent(MockBackend(), ["wait"], "p", mode=old)

    def test_manifest_records_code_version_and_served_model(self):
        with tempfile.TemporaryDirectory() as folder:
            manifest = run_episode(self.persona, self.scenario, MockBackend(), Path(folder) / "run")
        version = manifest["code_version"]
        self.assertTrue(version["git_sha"] == "unavailable" or len(version["git_sha"]) == 40)
        self.assertIn(version["git_dirty"], (True, False, None))
        self.assertEqual(manifest["model_served"], [])
        self.assertEqual(manifest["model"], "deterministic-fixture-v1")

    def test_code_version_degrades_explicitly_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(code_version(folder), {"git_sha": "unavailable", "git_dirty": None})

    def test_adding_state_to_the_prompt_does_not_change_bookkeeping(self):
        finals = {}
        with tempfile.TemporaryDirectory() as folder:
            for mode in MODES:
                out = Path(folder) / mode
                manifest = run_episode(self.persona, self.scenario, MockBackend(), out, mode)
                finals[mode] = json.loads((out / "state.json").read_text())["dynamic"]
                self.assertEqual(manifest["mode"], mode)
        # The mock reads the active cards but not current_state, so adding state to the
        # prompt must not change the trajectory; adding cards may (it picks by card order).
        self.assertEqual(finals["profile_only"], finals["profile_state"])

    def test_invalid_profiles_rejected(self):
        for value in (True, float("nan"), 9):
            bad = copy.deepcopy(self.persona)
            bad["stable"]["traits"]["personality"]["facets"]["neuroticism.anxiety"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_persona(bad)
        bad = copy.deepcopy(self.persona)
        bad["stable"]["traits"]["personality"]["scheme"] = "P1+P3"
        with self.assertRaises(ValueError):
            validate_persona(bad)

    def test_claim_cannot_be_used_as_verified_trust_signal(self):
        self.scenario["events"][2]["reliability_signal"] = .9
        with self.assertRaises(ValueError):
            validate_scenario(self.scenario)

    def test_catalog_matches_sources_and_references(self):
        catalog = build_catalog(ROOT / "data" / "sources")
        saved = json.loads((ROOT / "data" / "catalog.json").read_text())
        self.assertEqual(digest(catalog), digest(saved))
        ids = [r["id"] for r in catalog["sections"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["status"] == "reference_only" for r in catalog["sections"]))
        for source in self.persona["provenance"].values():
            self.assertIn(source["source_ref"], ids)

    def test_nonstandard_json_is_rejected(self):
        with self.assertRaises(ValueError):
            strict_json('{"value": NaN}')


if __name__ == "__main__":
    unittest.main()
