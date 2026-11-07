"""Role adapter (rolesim/adapter.py) and local runner (rolesim/loop.py) for the agent interface v2."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.adapter import AgentCallError, RoleAgentAdapter
from rolesim.agent_api import InterfaceError, record_id, split, validate_action_result, validate_turn_input
from rolesim.loop import EchoEnvironment, LocalRunner, bootstrap, episode, run_agent_api
from rolesim.providers import MockBackend
from rolesim.roles import load_role, load_scene

CAST = ["role1", "role2", "role3"]
IID = "i1"


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


EP = episode(roles(), load_scene("test3"))
SETTING = {"place": "会议室", "time_text": "15:00"}
PRESENT = {m["character_id"]: {"name": m["name"]} for m in EP["cast"]}
LIN = EP["cast"][0]["character_id"]
ZHOU = EP["cast"][1]["character_id"]


def initialize(adapter, iid=IID, members=None):
    for member in members or EP["cast"]:
        run(adapter.initialize(iid, member["character_id"], bootstrap(EP, member, SETTING)))


def opening(turn=1, present=None):
    content = {"thought": None, "speech": None, "action": {"content": EP["opening"], "recipient_ids": None},
               "order": ["action"]}
    return split(content, "environment", "环境", present or PRESENT, f"t{turn:03d}-env1", turn, 0)


def turn_input(observations, present=None):
    return {"bootstrap": None, "observations": list(observations), "present_characters": copy.deepcopy(present or PRESENT)}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.backend = Counting()
        self.adapter = RoleAgentAdapter(roles(), self.backend)
        initialize(self.adapter)
        self.context = turn_input(opening())

    def act(self, context, request_id, cid=LIN, iid=IID, adapter=None):
        return run((adapter or self.adapter).act(iid, cid, copy.deepcopy(context), request_id))

    def test_act_returns_speech_only_and_no_cognition(self):
        result = self.act(self.context, "r1")
        validate_action_result(result, self.context, LIN)
        self.assertEqual(set(result), {"thought", "speech", "action", "order"})
        self.assertEqual(result["order"], ["speech"])
        self.assertIsNone(result["speech"]["recipient_ids"])

    def test_character_ids_are_role_library_ids(self):
        self.assertEqual([m["character_id"] for m in EP["cast"]], CAST)
        self.assertEqual(self.adapter.sessions[(IID, LIN)].role_id, LIN)

    def test_initialize_routes_by_role_ref_id_or_name_and_once_per_role(self):
        adapter = RoleAgentAdapter(roles(), MockBackend())
        member = EP["cast"][0]
        data = bootstrap(EP, member, SETTING)
        run(adapter.initialize(IID, LIN, data))
        run(adapter.initialize(IID, LIN, copy.deepcopy(data)))          # retry is harmless
        with self.assertRaisesRegex(InterfaceError, "another bootstrap"):
            run(adapter.initialize(IID, LIN, {**data, "core_goals": "别的"}))
        with self.assertRaisesRegex(InterfaceError, "already character"):
            run(adapter.initialize(IID, "c-other", data))               # same role by name
        run(adapter.initialize("i2", "x1", data))                       # not a library id: by name
        self.assertEqual(adapter.sessions[("i2", "x1")].role_id, LIN)
        run(adapter.initialize("i3", "x1", {**data, "personality": {"role_ref": "role2"}}))
        self.assertEqual(adapter.sessions[("i3", "x1")].role_id, "role2")
        with self.assertRaisesRegex(InterfaceError, "not in the role library"):
            run(adapter.initialize("i4", "x1", {**data, "name": "陌生人"}))
        with self.assertRaisesRegex(InterfaceError, "no role 'nobody'"):
            run(adapter.initialize("i4", "x1", {**data, "personality": {"role_ref": "nobody"}}))

    def test_mechanism_mode_sets_the_ablation_per_character(self):
        adapter = RoleAgentAdapter(roles(), MockBackend())
        data = bootstrap(EP, EP["cast"][0], SETTING, mode="profile_only")
        run(adapter.initialize(IID, LIN, data))
        self.assertEqual(adapter.sessions[(IID, LIN)].mode, "profile_only")
        for member in EP["cast"][1:]:
            run(adapter.initialize(IID, member["character_id"], bootstrap(EP, member, SETTING)))
        self.act(self.context, "r1", adapter=adapter)
        self.assertEqual(adapter.sessions[(IID, LIN)].agent.mode, "profile_only")
        self.assertEqual(adapter.log[0]["mode"], "profile_only")
        with self.assertRaisesRegex(InterfaceError, "unknown mode"):
            run(adapter.initialize("i2", LIN, {**data, "mechanism": {"mode": "everything"}}))

    def test_act_needs_initialized_characters(self):
        with self.assertRaisesRegex(InterfaceError, "not initialized"):
            self.act(self.context, "r1", iid="other")
        adapter = RoleAgentAdapter(roles(), MockBackend())
        initialize(adapter, members=EP["cast"][:1])
        with self.assertRaisesRegex(InterfaceError, "not initialized"):
            self.act(self.context, "r1", adapter=adapter)              # the others never were

    def test_repeated_request_id_returns_the_first_result_without_new_model_calls(self):
        first = self.act(self.context, "r1")
        calls = len(self.backend.payloads)
        self.assertEqual(self.act(self.context, "r1"), first)
        self.assertEqual(len(self.backend.payloads), calls)
        with self.assertRaisesRegex(InterfaceError, "different request"):
            self.act(turn_input([]), "r1")

    def test_own_records_are_taken_in_at_the_next_act_and_thoughts_never(self):
        result = self.act(self.context, "r1")
        own = split({**result, "thought": {"content": "先稳住。", "recipient_ids": [LIN]},
                     "order": ["thought", "speech"]}, LIN, "林澄", PRESENT, f"t001-{LIN}", 1, 1)
        other = split({"thought": {"content": "OTHER_THOUGHT_SENTINEL", "recipient_ids": [ZHOU, LIN]},
                       "speech": None, "action": None, "order": ["thought"]}, ZHOU, "周越", PRESENT, f"t002-{ZHOU}", 2, 0)
        self.act(turn_input(opening() + own + other), "r2")
        taken = [e["taken_in"] for e in self.adapter.log if e["character_id"] == LIN]
        self.assertEqual(taken[1], [record_id(own[1])])          # own speech yes, thoughts no
        session = self.adapter.sessions[(IID, LIN)]
        self.assertEqual([m["actor"] for m in session.agent.state["memory"] if m["id"] == record_id(own[1])], ["我"])
        self.assertTrue(any("thought" in w.get("warning", "") for w in session.warnings))
        self.assertNotIn("OTHER_THOUGHT_SENTINEL", json.dumps(self.backend.payloads, ensure_ascii=False))

    def test_every_state_change_cites_a_record_or_recap(self):
        self.act(self.context, "r1")
        known = {record_id(r) for r in self.context["observations"]}
        known |= {m["id"] for m in self.adapter.sessions[(IID, LIN)].agent.state["memory"]}
        for entry in self.adapter.log:
            for change in entry["delta"]["changes"]:
                self.assertTrue(set(change["evidence"]) <= known, change)

    def test_a_newcomer_gets_a_relation_state_on_first_meeting(self):
        early = {cid: e for cid, e in PRESENT.items() if cid != ZHOU}
        self.act(turn_input(opening(present=early), early), "r1")
        agent = self.adapter.sessions[(IID, LIN)].agent
        zhou_role = self.adapter.sessions[(IID, ZHOU)].role_id
        self.assertNotIn(zhou_role, agent.state["dynamic"]["relation_state"])
        self.act(turn_input(opening(present=early)), "r2")
        self.assertEqual(agent.state["dynamic"]["relation_state"][zhou_role]["trust"], .55)
        self.assertIn(zhou_role, agent.others)

    def test_interactions_are_separate_sessions(self):
        initialize(self.adapter, iid="i2")
        self.act(self.context, "r1")
        self.act(self.context, "r2", iid="i2")
        one, two = self.adapter.sessions[(IID, LIN)].agent, self.adapter.sessions[("i2", LIN)].agent
        self.assertIsNot(one, two)
        self.assertEqual(one.state["dynamic"], two.state["dynamic"])

    def test_failure_raises_and_a_retry_starts_clean(self):
        adapter = RoleAgentAdapter(roles(), Counting(fail_decide=1))
        initialize(adapter)
        with self.assertRaises(AgentCallError):
            self.act(self.context, "r1", adapter=adapter)
        self.assertIsNone(adapter.sessions[(IID, LIN)].agent.state_id)
        result = self.act(self.context, "r2", adapter=adapter)
        validate_action_result(result, self.context, LIN)
        self.assertEqual(adapter.log[0]["taken_in"], [record_id(r) for r in self.context["observations"]])


class LoopTests(unittest.TestCase):
    def test_three_roles_run_end_to_end_with_valid_views(self):
        ep = episode(roles(), load_scene("test3"))
        adapter = RoleAgentAdapter(roles(), MockBackend(), allowed_actions=ep["allowed_actions"])
        runner = LocalRunner(ep, adapter, EchoEnvironment())
        turns = run(runner.run())
        self.assertEqual(turns, ep["max_turns"])
        for cid, observations in runner.observations.items():
            validate_turn_input(turn_input(observations, runner.present), cid)
        orders = [(r["turn_index"], r["order_index"]) for r in runner.history]
        self.assertEqual(orders, sorted(set(orders)))

    def test_private_world_event_reaches_only_its_audience(self):
        ep = episode(roles(), load_scene("test3"))
        runner = LocalRunner(ep, RoleAgentAdapter(roles(), MockBackend()), EchoEnvironment())
        run(runner.run())
        secret = "第三批数据今晚能清洗完"
        for cid, observations in runner.observations.items():
            seen = any(secret in r["content"] for r in observations)
            self.assertEqual(seen, cid == LIN, cid)

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
