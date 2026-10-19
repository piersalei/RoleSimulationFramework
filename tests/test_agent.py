import copy
import json
import unittest
from pathlib import Path

from rolesim.agent import Agent
from rolesim.catalog import digest
from rolesim.contract import (Event, PerceiveRequest, StepRequest, Visibility, to_dict)
from rolesim.providers import MockBackend
from rolesim.runtime import ScriptedEnvironment
from rolesim.state import DOMAINS

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "examples" / name).read_text())


class Capture(MockBackend):
    def __init__(self):
        self.payloads = []

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("cautious.json")
        self.scenario = load("cooperation_conflict.json")
        self.env = ScriptedEnvironment(self.scenario, "cautious")
        self.events = [event for _, event in self.env.scheduled()]
        self.backend = Capture()
        self.agent = Agent(self.backend, self.scenario["allowed_actions"], self.scenario["partner"])

    def step(self, turn, events, prev=None, profile=None, **extra):
        return self.agent.step(StepRequest("cautious", turn, self.scenario["goal"], events, prev,
                                           profile, **extra))

    def first(self):
        return self.step(1, [self.events[0]], profile=self.persona)

    def test_step_returns_serialisable_proposal_and_state(self):
        response = self.first()
        self.assertEqual(response.status, "ok")
        self.assertEqual(len(response.actions), 1)
        self.assertEqual(response.actions[0].proposal_id, "cautious@t001#1")
        self.assertEqual(response.state["state_id"], "cautious@t001")
        self.assertIsNone(response.state["prev_state_id"])
        self.assertEqual(response.state["memory"], ["e1"])
        self.assertEqual(response.delta["trigger"], "step")
        json.dumps(to_dict(response), ensure_ascii=False)

    def test_role_profile_is_required_on_first_call(self):
        response = self.step(1, [self.events[0]])
        self.assertEqual(response.status, "error")
        self.assertEqual(response.actions, [])

    def test_invisible_event_is_dropped_and_never_referenced(self):
        hidden = Event("partner_private", 1, "narration", "env", "HIDDEN_PARTNER_NOTE",
                       Visibility("private", ["partner"]),
                       signals={"cues": {"pressure": 1}, "reliability_signal": -1})
        response = self.step(1, [hidden, self.events[0]], profile=self.persona)
        self.assertEqual(response.status, "ok")
        self.assertNotIn("partner_private", json.dumps(to_dict(response), ensure_ascii=False))
        self.assertNotIn("HIDDEN_PARTNER_NOTE", json.dumps(self.backend.payloads, ensure_ascii=False))
        self.assertEqual(self.agent.warnings[0]["event_id"], "partner_private")

    def test_next_step_reads_the_state_after_perceive(self):
        stepped = self.first()
        _, outcomes = self.env.adjudicate(stepped.actions[0], 1)
        perceived = self.agent.perceive(PerceiveRequest("cautious", 1, outcomes, stepped.state["state_id"]))
        self.assertEqual(perceived.state["state_id"], "cautious@t001.p1")
        self.assertEqual(perceived.state["prev_state_id"], "cautious@t001")
        self.assertIn(outcomes[0].event_id, perceived.state["memory"])
        second = self.step(2, [self.events[1]], perceived.state["state_id"])
        self.assertEqual(second.state["prev_state_id"], "cautious@t001.p1")
        history = self.backend.payloads[-2]["visible_history"]
        self.assertIn(outcomes[0].event_id, [item["id"] for item in history])

    def test_stale_prev_state_id_is_rejected_without_advancing(self):
        stepped = self.first()
        before = self.agent.latest_state()
        response = self.step(2, [self.events[1]], prev="cautious@t000")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.actions, [])
        self.assertEqual(self.agent.latest_state(), before)
        self.assertEqual(stepped.state, before)

    def test_failed_decision_returns_error_without_leaking_thought_or_state(self):
        class Broken(MockBackend):
            def decide(self, payload):
                return {"action": "wait", "evidence_ids": ["not_seen"], "brief_reason": "SECRET_REASON"}
        agent = Agent(Broken(), self.scenario["allowed_actions"], self.scenario["partner"])
        response = agent.step(StepRequest("cautious", 1, self.scenario["goal"], [self.events[0]], None,
                                          self.persona))
        self.assertEqual(response.status, "error")
        self.assertEqual((response.actions, response.thought), ([], ""))
        self.assertIsNone(agent.state_id)
        self.assertEqual(agent.state["seen_ids"], [])

    def test_repeated_full_history_is_deduplicated(self):
        stepped = self.first()
        again = self.step(2, [self.events[0], self.events[1]], stepped.state["state_id"])
        self.assertEqual(again.state["memory"].count("e1"), 1)
        self.assertTrue(all(c["evidence"] == ["e2"] for c in again.delta["changes"]))

    def test_every_change_carries_visible_evidence(self):
        stepped = self.first()
        for change in stepped.delta["changes"]:
            self.assertTrue(set(change["evidence"]) <= set(stepped.state["memory"]))

    def test_outcome_returns_to_actor_and_private_fields_stay_out_of_events(self):
        stepped = self.first()
        speech, outcomes = self.env.adjudicate(stepped.actions[0], 1)
        self.assertIn("partner", speech.visibility.audience)
        for event in outcomes:
            self.assertEqual(event.type, "outcome")
            self.assertIn("cautious", event.visibility.audience)
            self.assertEqual(event.outcome["of_proposal"], stepped.actions[0].proposal_id)
        produced = json.dumps([to_dict(e) for e in self.env.log], ensure_ascii=False)
        self.assertNotIn(stepped.thought, produced)
        self.assertNotIn('"intent_note"', produced)

    def test_obtained_evidence_is_not_requested_again(self):
        agent = Agent(MockBackend(), ["request_evidence", "wait"], self.scenario["partner"])
        first = agent.step(StepRequest("cautious", 1, "g", [self.events[0]], None, self.persona))
        self.assertEqual(first.actions[0].intent_note, "request_evidence")
        _, outcomes = self.env.adjudicate(first.actions[0], 1)
        perceived = agent.perceive(PerceiveRequest("cautious", 1, outcomes, first.state["state_id"]))
        second = agent.step(StepRequest("cautious", 2, "g", [self.events[1]], perceived.state["state_id"]))
        self.assertEqual(second.actions[0].intent_note, "wait")

    def test_situational_speech_rule_reaches_only_the_expression_prompt_as_style(self):
        self.first()
        decide_payload, express_payload = self.backend.payloads[-2:]
        styles = [r["source_field"] for r in express_payload["style_constraints"]]
        self.assertIn("speech_style.situational_rules", styles)
        self.assertTrue(all(s.startswith("speech_style.") for s in styles))
        goals = decide_payload["behavioral_constraints"]["core_goals"]
        self.assertEqual(goals[0]["id"], "g1")
        self.assertNotIn("core_goals", json.dumps(express_payload, ensure_ascii=False))

    def test_snapshot_carries_versions_module_status_and_stable_id(self):
        state = self.first().state
        self.assertEqual(state["schema_version"], "state-v1")
        self.assertEqual(set(state["versions"]), {"construct_definition_version", "state_schema_version",
                                                  "update_rule_version", "evaluation_protocol_version"})
        self.assertEqual(state["stable_snapshot_id"], digest(self.persona["stable"]))
        self.assertEqual(state["module_status"]["dynamic.group_state"]["status"], "not_modeled")
        self.assertEqual(tuple(state["dynamic"]), DOMAINS)

    def test_mechanism_log_links_input_and_output_state_ids(self):
        stepped = self.first()
        _, outcomes = self.env.adjudicate(stepped.actions[0], 1)
        perceived = self.agent.perceive(PerceiveRequest("cautious", 1, outcomes, stepped.state["state_id"]))
        log = self.agent.mechanism_log
        self.assertEqual([(e["trigger"], e["input_state_id"], e["output_state_id"]) for e in log],
                         [("step", None, "cautious@t001"),
                          ("perceive", "cautious@t001", perceived.state["state_id"])])
        self.assertEqual([e["commit_key"] for e in log], ["cautious/e1/step", f"cautious/{outcomes[0].event_id}/perceive"])
        self.assertTrue(all(e["rejected"] == [] for e in log))

    def test_stable_persona_cannot_be_swapped_mid_episode(self):
        stepped = self.first()
        other = load("assertive.json")
        other["id"] = "cautious"
        response = self.step(2, [self.events[1]], stepped.state["state_id"], other)
        self.assertEqual(response.status, "error")


if __name__ == "__main__":
    unittest.main()
