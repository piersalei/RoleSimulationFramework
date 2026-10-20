import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.catalog import build_catalog, digest
from rolesim.persona import compile_views, validate_persona
from rolesim.providers import MockBackend, strict_json
from rolesim.agent import Agent, MODES, initial_state, make_payload, observe, observation, validate_decision
from rolesim.contract import Event, ProposedAction, Visibility
from rolesim.runtime import DialogueEnvironment, code_version, validate_scenario
from fixtures import cued_events, load, run_scene, setup


ROOT = Path(__file__).resolve().parents[1]
PARTNER = "合作方负责人"


class CaptureBackend(MockBackend):
    def __init__(self):
        self.payloads = []

    def appraise(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().appraise(payload)

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")
        self.scenario = load("test1")

    def test_world_private_and_the_other_roles_secret_never_reach_any_call(self):
        backend = CaptureBackend()
        with tempfile.TemporaryDirectory() as folder:
            manifest = run_scene(Path(folder) / "run", backend)
        calls = json.dumps(backend.payloads, ensure_ascii=False)
        self.assertNotIn("HIDDEN_WORLD_SENTINEL", calls)
        self.assertNotIn("system_truth", calls)
        for role in ("role1", "role2"):
            for item in setup(role)["conceal"]:
                seen_by = [p for p in backend.payloads if item["text"] in json.dumps(p, ensure_ascii=False)]
                self.assertTrue(seen_by, "the owner's controller does see its own secret")
                self.assertEqual({p["role"]["id"] for p in seen_by}, {role})
                self.assertTrue(all("briefing" in p for p in seen_by), "only controller-side prompts carry it")
        # 8 turns of decide + express; 9 appraisals (role2 first hears the opening and role1's line).
        self.assertEqual(manifest["turns"], 8)
        self.assertEqual(len(backend.payloads), 8 * 2 + 9)

    def test_views_do_not_expose_psychological_truth_to_other_roles(self):
        views = compile_views(self.persona)
        self.assertEqual(set(views["other_visible"]), {"id", "name", "description"})
        self.assertNotIn("stable", views["role_self"])
        views["system_truth"]["stable"]["traits"]["attachment"]["anxiety"] = -2
        self.assertEqual(self.persona["stable"]["traits"]["attachment"]["anxiety"], .8)

    def test_repeated_and_invisible_events_cannot_change_state(self):
        events = cued_events()
        hidden = copy.deepcopy(events[0])
        hidden.event_id, hidden.visibility = "partner_private", Visibility("private", ["role2"])
        state = initial_state()
        observe(state, events[0], self.persona)
        prior = copy.deepcopy(state)
        self.assertIsNone(observe(state, events[0], self.persona))
        self.assertIsNone(observe(state, hidden, self.persona))
        self.assertEqual(state, prior)

    def test_same_observation_has_persona_conditioned_state_response(self):
        a, b = initial_state(), initial_state()
        observe(a, cued_events()[0], self.persona)
        observe(b, cued_events("role2", "role1")[0], load("role2"))
        self.assertGreater(a["dynamic"]["affect_state"]["emotion"]["intensity"], b["dynamic"]["affect_state"]["emotion"]["intensity"])
        self.assertEqual(a["dynamic"]["relation_state"], b["dynamic"]["relation_state"])

    def test_words_do_not_count_as_fulfilment(self):
        events = cued_events()
        state = initial_state()
        claim = copy.deepcopy(events[2])
        claim.signals["reliability_signal"] = .9
        observe(state, claim, self.persona)
        self.assertEqual(state["dynamic"]["relation_state"]["partner"]["trust"], .55)
        observe(state, events[3], self.persona)
        self.assertGreater(state["dynamic"]["relation_state"]["partner"]["trust"], .55)

    def test_appraised_cues_move_tension_but_never_trust(self):
        line = Event("speech:2", 2, "speech", "role2", "今天必须交！", Visibility("public", ["role1", "role2"]))
        state = initial_state()
        delta = observe(state, line, self.persona, cues={"pressure": 1, "commitment": 1})
        self.assertEqual([c["path"] for c in delta["changes"]], ["dynamic.affect_state.emotion.intensity"])
        self.assertEqual(state["memory"][-1]["cues"], {"pressure": 1, "commitment": 1})

    def test_environment_only_relays_and_stops(self):
        env = DialogueEnvironment(self.scenario, ["role1", "role2"])
        opening = env.open()
        self.assertEqual((opening.event_id, opening.type, opening.actor), ("open", "narration", "env"))
        self.assertEqual([r for _, r in env.turns()], ["role1", "role2"] * 4)
        env = DialogueEnvironment(self.scenario, ["role1", "role2"])
        close = ProposedAction("role1@t001#1", "speech", "先到这里。", Visibility("public", ["role1", "role2"]),
                               ["role2"], "close")
        turns = env.turns()
        turn, role = next(turns)
        env.relay(close, turn, role)
        self.assertEqual(list(turns), [])
        self.assertEqual(env.closed_by, "role1")
        bad = ProposedAction("role1@t002#1", "speech", "x", Visibility("public", ["role1"]), [], "declare_success")
        with self.assertRaises(ValueError):
            env.relay(bad, 2, "role1")
        for cast in (["role1"], ["role1", "role1"], ["role1", "role2", "role3"]):
            with self.subTest(cast=cast), self.assertRaises(ValueError):
                DialogueEnvironment(self.scenario, cast)

    def test_action_and_evidence_validation_rejects_fabrication(self):
        state = initial_state()
        event = cued_events()[0]
        observe(state, event, self.persona)
        payload = make_payload(self.persona, state, observation(event), self.scenario["background"],
                               PARTNER, self.scenario["allowed_actions"], "profile_state_mechanisms")
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
                run_scene(output, Broken())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["status"], "failed")
            self.assertIn("infeasible action", manifest["error"])
            self.assertEqual((output / "roles" / "role1" / "audit.jsonl").read_text(), "")

    def test_episode_reproducible_and_stable_persona_immutable(self):
        before = copy.deepcopy(self.persona)
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder) / "a", Path(folder) / "b"
            for target in (a, b):
                run_scene(target)
            for name in ("public.jsonl", "roles/role1/audit.jsonl", "roles/role2/audit.jsonl"):
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes(), name)
            clock = ("started_at", "finished_at")
            manifests = [{k: v for k, v in json.loads((d / "manifest.json").read_text()).items() if k not in clock}
                         for d in (a, b)]
            self.assertEqual(manifests[0], manifests[1])
            with self.assertRaises(FileExistsError):
                run_scene(a)
        self.assertEqual(self.persona, before)

    def test_each_role_hears_the_other_and_answers_in_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            run_scene(out)
            public = [json.loads(l) for l in (out / "public.jsonl").read_text().splitlines()]
            self.assertEqual([p["speaker"] for p in public], ["environment"] + ["role1", "role2"] * 4)
            records = [json.loads(l) for l in (out / "roles" / "role2" / "audit.jsonl").read_text().splitlines()]
            heard = [a["event_id"] for r in records for a in r["appraisals"]]
            self.assertEqual(heard, ["open", "speech:1", "speech:3", "speech:5", "speech:7"])
            self.assertEqual(records[0]["controller_input"]["respond_to"]["actor"], "项目组数据负责人")
            self.assertEqual(records[0]["controller_input"]["self_seat"], PARTNER)

    def test_profile_only_uses_same_observation_without_mechanism_or_state_prompt(self):
        state = initial_state()
        event = cued_events()[0]
        observe(state, event, self.persona)
        args = (observation(event), self.scenario["background"], PARTNER, self.scenario["allowed_actions"])
        a = make_payload(self.persona, state, *args, "profile_state_mechanisms")
        b = make_payload(self.persona, state, *args, "profile_only")
        self.assertEqual(a["visible_history"], b["visible_history"])
        self.assertNotIn("active_mechanisms", b)
        self.assertNotIn("current_state", b)

    def test_ablation_levels_add_state_then_mechanisms(self):
        state = initial_state()
        event = cued_events()[0]
        observe(state, event, self.persona)
        args = (observation(event), self.scenario["background"], PARTNER, self.scenario["allowed_actions"])
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
            manifest = run_scene(Path(folder) / "run")
        version = manifest["code_version"]
        self.assertTrue(version["git_sha"] == "unavailable" or len(version["git_sha"]) == 40)
        self.assertIn(version["git_dirty"], (True, False, None))
        self.assertEqual(manifest["model_served"], [])
        self.assertEqual(manifest["model"], "deterministic-fixture-v1")
        self.assertEqual([c["role_id"] for c in manifest["cast"]], ["role1", "role2"])

    def test_code_version_degrades_explicitly_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(code_version(folder), {"git_sha": "unavailable", "git_dirty": None})

    def test_adding_state_to_the_prompt_does_not_change_bookkeeping(self):
        finals = {}
        with tempfile.TemporaryDirectory() as folder:
            for mode in MODES:
                out = Path(folder) / mode
                manifest = run_scene(out, mode=mode)
                finals[mode] = json.loads((out / "roles" / "role1" / "state.json").read_text())["dynamic"]
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

    def test_scene_is_background_and_seats_only(self):
        validate_scenario(self.scenario)
        for patch in ({"events": []}, {"schema_version": "1.2"}, {"max_turns": 1}, {"max_turns": True},
                      {"seats": self.scenario["seats"][:1]}, {"allowed_actions": ["declare_success"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_scenario({**self.scenario, **patch})

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
