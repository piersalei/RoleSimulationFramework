import copy
import json
import unittest

from rolesim.agent import Agent
from rolesim.catalog import digest
from rolesim.contract import (Event, PerceiveRequest, StepRequest, Visibility, to_dict)
from rolesim.providers import MockBackend
from rolesim.runtime import DialogueEnvironment
from rolesim.state import DOMAINS
from fixtures import cued_events, load

PARTNER, SEAT = "合作方负责人", "项目组数据负责人"


class Capture(MockBackend):
    def __init__(self):
        self.payloads, self.appraisals = [], []

    def appraise(self, payload):
        self.appraisals.append(copy.deepcopy(payload))
        return super().appraise(payload)

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


def speech(event_id, turn, content, actor="role2"):
    return Event(event_id, turn, "speech", actor, content, Visibility("public", ["role1", "role2"]))


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")
        self.scenario = load("test1")
        self.env = DialogueEnvironment(self.scenario, ["role1", "role2"])
        self.events = cued_events()
        self.backend = Capture()
        self.agent = self.make(self.backend)

    def make(self, backend, allowed=None):
        return Agent(backend, allowed or self.scenario["allowed_actions"], {"role2": PARTNER}, seat=SEAT)

    def step(self, turn, events, prev=None, profile=None, **extra):
        return self.agent.step(StepRequest("role1", turn, self.scenario["background"], events, prev,
                                           profile, **extra))

    def first(self):
        return self.step(1, [self.events[0]], profile=self.persona)

    def said(self, stepped, turn=1):
        spoken = self.env.relay(stepped.actions[0], turn, "role1")
        return spoken, self.agent.perceive(PerceiveRequest("role1", turn, [spoken], stepped.state["state_id"]))

    def test_step_returns_serialisable_proposal_and_state(self):
        response = self.first()
        self.assertEqual(response.status, "ok")
        self.assertEqual(len(response.actions), 1)
        self.assertEqual(response.actions[0].proposal_id, "role1@t001#1")
        self.assertEqual(response.state["state_id"], "role1@t001")
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
                       Visibility("private", ["role2"]),
                       signals={"cues": {"pressure": 1}, "reliability_signal": -1})
        response = self.step(1, [hidden, self.events[0]], profile=self.persona)
        self.assertEqual(response.status, "ok")
        self.assertNotIn("partner_private", json.dumps(to_dict(response), ensure_ascii=False))
        self.assertNotIn("HIDDEN_PARTNER_NOTE", json.dumps(self.backend.payloads, ensure_ascii=False))
        self.assertEqual(self.agent.warnings[0]["event_id"], "partner_private")

    def test_own_line_enters_memory_when_reported_said(self):
        stepped = self.first()
        self.assertNotIn("speech:1", stepped.state["memory"])
        spoken, perceived = self.said(stepped)
        self.assertEqual(perceived.state["state_id"], "role1@t001.p1")
        self.assertEqual(perceived.state["prev_state_id"], "role1@t001")
        self.assertIn("speech:1", perceived.state["memory"])
        second = self.step(2, [self.events[1]], perceived.state["state_id"])
        self.assertEqual(second.state["prev_state_id"], "role1@t001.p1")
        history = self.backend.payloads[-2]["visible_history"]
        own = next(item for item in history if item["id"] == "speech:1")
        self.assertEqual((own["actor"], own["content"]), ("我", spoken.content))
        self.assertEqual(self.backend.payloads[-1]["my_previous_lines"], [spoken.content])
        self.assertEqual(self.backend.payloads[-2]["my_previous_actions"], [stepped.actions[0].intent_note])
        self.assertEqual(self.backend.appraisals, [], "own lines and cued events are not appraised")

    def test_uncued_partner_speech_is_appraised_and_drives_state(self):
        line = speech("speech:2", 2, "今天必须把全部数据交过来，能不能做到？")
        response = self.step(2, [line], profile=self.persona)
        self.assertEqual(response.status, "ok")
        appraisal = self.backend.appraisals[0]
        self.assertEqual(appraisal["event"], {"id": "speech:2", "type": "speech", "actor": PARTNER,
                                              "content": line.content})
        self.assertEqual(set(appraisal["cue_definitions"]),
                         {"uncertainty", "pressure", "commitment", "repair", "conflict"})
        tension = [c for c in response.delta["changes"] if c["path"].endswith("emotion.intensity")]
        self.assertEqual(tension[0]["evidence"], ["speech:2"])
        self.assertEqual(tension[0]["reason"], "角色感受到压力")
        decide, express = self.backend.payloads
        self.assertEqual(decide["respond_to"]["cues"]["pressure"], .7)
        self.assertEqual(express["respond_to"], {"speaker": PARTNER, "content": line.content})
        self.assertEqual(express["action_meaning"], decide["action_meanings"][express["action"]])
        self.assertEqual(self.agent.last_appraisals[0]["event_id"], "speech:2")

    def test_appraisal_cannot_invent_cues_or_out_of_range_values(self):
        for bad in ({"cues": {"anger": .5}, "reason": "x"}, {"cues": {"pressure": 1.5}, "reason": "x"},
                    {"cues": {"pressure": True}, "reason": "x"}, {"cues": {}, "reason": "x", "state": {}}):
            class Bad(MockBackend):
                def appraise(self, payload, bad=bad):
                    return bad
            agent = self.make(Bad())
            response = agent.step(StepRequest("role1", 1, "g", [speech("speech:1", 1, "你好")], None, self.persona))
            with self.subTest(bad=bad):
                self.assertEqual(response.status, "error")
                self.assertIsNone(agent.state_id)

    def test_stale_prev_state_id_is_rejected_without_advancing(self):
        stepped = self.first()
        before = self.agent.latest_state()
        response = self.step(2, [self.events[1]], prev="role1@t000")
        self.assertEqual(response.status, "error")
        self.assertEqual(response.actions, [])
        self.assertEqual(self.agent.latest_state(), before)
        self.assertEqual(stepped.state, before)

    def test_failed_decision_returns_error_without_leaking_thought_or_state(self):
        class Broken(MockBackend):
            def decide(self, payload):
                return {"action": "wait", "evidence_ids": ["not_seen"], "brief_reason": "SECRET_REASON"}
        agent = self.make(Broken())
        response = agent.step(StepRequest("role1", 1, self.scenario["background"], [self.events[0]], None,
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

    def test_relayed_line_is_public_and_private_fields_stay_out_of_events(self):
        stepped = self.first()
        spoken, _ = self.said(stepped)
        self.assertEqual(spoken.visibility.audience, ["role1", "role2"])
        self.assertEqual(spoken.caused_by, stepped.actions[0].proposal_id)
        self.assertEqual(self.env.collect("role2"), [spoken])
        self.assertEqual(self.env.collect("role1"), [], "the speaker hears its own line through perceive")
        produced = json.dumps([to_dict(e) for e in self.env.log], ensure_ascii=False)
        self.assertNotIn(stepped.thought, produced)
        self.assertNotIn('"intent_note"', produced)

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
        _, perceived = self.said(stepped)
        log = self.agent.mechanism_log
        self.assertEqual([(e["trigger"], e["input_state_id"], e["output_state_id"]) for e in log],
                         [("step", None, "role1@t001"),
                          ("perceive", "role1@t001", perceived.state["state_id"])])
        self.assertEqual([e["commit_key"] for e in log], ["role1/e1/step", "role1/speech:1/perceive"])
        self.assertTrue(all(e["rejected"] == [] for e in log))

    def test_stable_persona_cannot_be_swapped_mid_episode(self):
        stepped = self.first()
        other = load("role2")
        other["id"] = "role1"
        response = self.step(2, [self.events[1]], stepped.state["state_id"], other)
        self.assertEqual(response.status, "error")


if __name__ == "__main__":
    unittest.main()
