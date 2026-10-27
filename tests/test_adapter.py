"""Role adapter (rolesim/adapter.py) and local runner (rolesim/loop.py) for the agent interface v1."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.adapter import AgentCallError, RoleAgentAdapter
from rolesim.agent_api import (COGNITION_KEYS, InterfaceError, record_id, split, validate_action_result,
                               validate_character_view)
from rolesim.loop import EchoEnvironment, LocalRunner, episode, initial_view, run_agent_api
from rolesim.providers import MockBackend
from rolesim.roles import load_role, load_scene

CAST = ["role1", "role2", "role3"]


def roles():
    return [load_role(r) for r in CAST]


def run(coro):
    return asyncio.run(coro)


class Counting(MockBackend):
    def __init__(self, fail_decide=0):
        self.payloads, self.fail_decide = [], fail_decide

    def appraise(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().appraise(payload)

    def decide(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        if self.fail_decide:
            self.fail_decide -= 1
            raise RuntimeError("model unavailable")
        return super().decide(payload)

    def express(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return super().express(payload)


def views():
    ep = episode(roles(), load_scene("test3"))
    setting = {"place": "会议室", "time_text": "15:00"}
    return ep, {m["name"]: initial_view(ep, m, setting) for m in ep["cast"]}


def opening(ep, turn=1):
    present = {m["character_id"]: {"name": m["name"]} for m in ep["cast"]}
    content = {"thought": None, "speech": None, "action": {"content": ep["opening"], "recipient_ids": None},
               "order": ["action"]}
    return split(content, "env", "环境", present, f"t{turn:03d}-env1", turn, 0)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.backend = Counting()
        self.adapter = RoleAgentAdapter(roles(), self.backend)
        self.ep, self.views = views()
        for view in self.views.values():
            view["observations"] = opening(self.ep)

    def test_act_returns_speech_and_full_cognition(self):
        view = self.views["林澄"]
        result = run(self.adapter.act(copy.deepcopy(view), "r1"))
        validate_action_result(result, view)
        self.assertEqual(result["action"]["order"], ["speech"])
        self.assertIsNone(result["action"]["thought"])
        self.assertEqual(set(result["cognition"]), set(COGNITION_KEYS))
        self.assertIsNotNone(result["cognition"]["goals"])

    def test_repeated_request_id_returns_the_first_result_without_new_model_calls(self):
        view = self.views["林澄"]
        first = run(self.adapter.act(copy.deepcopy(view), "r1"))
        calls = len(self.backend.payloads)
        self.assertEqual(run(self.adapter.act(copy.deepcopy(view), "r1")), first)
        self.assertEqual(len(self.backend.payloads), calls)
        changed = copy.deepcopy(view)
        changed["trigger_facts"] = ["别的事。"]
        with self.assertRaisesRegex(InterfaceError, "different request"):
            run(self.adapter.act(changed, "r1"))

    def test_perceive_may_come_before_the_first_act(self):
        zhou = self.views["周越"]
        zhou["observations"] = []
        result = run(self.adapter.perceive({"character_view": zhou, "new_observations": opening(self.ep)}, "p1"))
        self.assertEqual(set(result), {"cognition"})
        self.assertIn("开始就这件事当面沟通", result["cognition"]["knowledge"])

    def test_own_records_are_taken_in_once_at_the_next_act_and_thoughts_skipped(self):
        lin = self.views["林澄"]
        result = run(self.adapter.act(copy.deepcopy(lin), "r1"))
        own = split({**result["action"], "thought": {"content": "先稳住。", "recipient_ids": None},
                     "order": ["thought", "speech"]}, "project", "林澄", lin["present_characters"], "t001-project", 1, 1)
        lin["observations"] += own
        lin["cognition"] = result["cognition"]
        run(self.adapter.act(copy.deepcopy(lin), "r2"))
        taken = [e["taken_in"] for e in self.adapter.log if e["name"] == "林澄"]
        self.assertEqual(taken[1], [record_id(own[1])])          # speech yes, own thought no
        memory = self.adapter.sessions["林澄"].agent.state["memory"]
        self.assertEqual([m["actor"] for m in memory if m["id"] == record_id(own[1])], ["我"])

    def test_every_state_change_cites_a_record_or_recap(self):
        lin = self.views["林澄"]
        run(self.adapter.act(copy.deepcopy(lin), "r1"))
        known = {record_id(r) for r in lin["observations"]}
        known |= {m["id"] for m in self.adapter.sessions["林澄"].agent.state["memory"]}
        for entry in self.adapter.log:
            for change in entry["delta"]["changes"]:
                self.assertTrue(set(change["evidence"]) <= known, change)

    def test_unknown_name_and_changed_presence_are_refused(self):
        stranger = copy.deepcopy(self.views["林澄"])
        stranger["name"], stranger["observations"] = "陌生人", []
        with self.assertRaisesRegex(InterfaceError, "no role"):
            run(self.adapter.act(stranger, "r1"))
        lin = self.views["林澄"]
        run(self.adapter.act(copy.deepcopy(lin), "r2"))
        fewer = copy.deepcopy(lin)
        fewer["present_characters"].pop("pi")
        with self.assertRaisesRegex(InterfaceError, "present characters changed"):
            run(self.adapter.act(fewer, "r3"))

    def test_failure_raises_and_a_retry_starts_clean(self):
        adapter = RoleAgentAdapter(roles(), Counting(fail_decide=1))
        lin = self.views["林澄"]
        with self.assertRaises(AgentCallError):
            run(adapter.act(copy.deepcopy(lin), "r1"))
        self.assertIsNone(adapter.sessions["林澄"].agent.state_id)
        result = run(adapter.act(copy.deepcopy(lin), "r2"))
        validate_action_result(result, lin)
        self.assertEqual(adapter.log[0]["taken_in"], [record_id(r) for r in lin["observations"]])

    def test_edited_cognition_is_flagged_not_adopted(self):
        lin = self.views["林澄"]
        result = run(self.adapter.act(copy.deepcopy(lin), "r1"))
        lin["cognition"] = {**result["cognition"], "beliefs": "运行器改写的信念"}
        again = run(self.adapter.act(copy.deepcopy(lin), "r2"))
        self.assertTrue(self.adapter.sessions["林澄"].warnings)
        self.assertNotIn("运行器改写", json.dumps(again, ensure_ascii=False))


class LoopTests(unittest.TestCase):
    def test_three_roles_run_end_to_end_with_valid_views(self):
        ep = episode(roles(), load_scene("test3"))
        adapter = RoleAgentAdapter(roles(), MockBackend(), allowed_actions=ep["allowed_actions"])
        runner = LocalRunner(ep, adapter, EchoEnvironment())
        turns = run(runner.run())
        self.assertEqual(turns, ep["max_turns"])
        for view in runner.views.values():
            validate_character_view(view)
        orders = [(r["turn_index"], r["order_index"]) for r in runner.history]
        self.assertEqual(orders, sorted(set(orders)))

    def test_private_world_event_reaches_only_its_audience(self):
        ep = episode(roles(), load_scene("test3"))
        runner = LocalRunner(ep, RoleAgentAdapter(roles(), MockBackend()), EchoEnvironment())
        run(runner.run())
        secret = "第三批数据今晚能清洗完"
        for name, view in runner.views.items():
            seen = any(secret in r["content"] for r in view["observations"])
            self.assertEqual(seen, name == "林澄", name)

    def test_run_directory_and_isolation(self):
        backend = Counting()
        cast = roles()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "run"
            manifest = run_agent_api(cast, load_scene("test3"), backend, out)
            self.assertEqual(manifest["status"], "completed")
            for name in ("manifest.json", "records.jsonl", "calls.jsonl", "transcript.md"):
                self.assertTrue((out / name).is_file(), name)
            for role in CAST:
                self.assertTrue((out / "roles" / role / "audit.jsonl").stat().st_size > 0)
            everything = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*") if p.is_file())
            self.assertNotIn("HIDDEN_WORLD_SENTINEL_47", everything)
            calls = (out / "calls.jsonl").read_text(encoding="utf-8")
        prompts = json.dumps(backend.payloads, ensure_ascii=False)
        self.assertNotIn("HIDDEN_WORLD_SENTINEL_47", prompts)
        for role in cast:
            for secret in role["setup"]["conceal"]:
                # Concealed text stays inside this side: never in what the runner sees.
                self.assertNotIn(secret["text"], calls)
        expression = [p for p in backend.payloads if "action_meaning" in p]
        secrets = [s["text"] for r in cast for s in r["setup"]["conceal"]]
        for payload in expression:
            text = json.dumps(payload, ensure_ascii=False)
            self.assertFalse(any(s in text for s in secrets))


if __name__ == "__main__":
    unittest.main()
