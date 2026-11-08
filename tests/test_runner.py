"""ITurnRunner (runner.py), ITrace (trace.py), the transitional repository (entry.py) and the
staged IRoleAgent (staged.py) behind the local runner (loop.run_staged)."""
import asyncio
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rolesim import interfaces as I
from rolesim.agent_api import validate_action_content
from rolesim.repository import ENTRY_GOAL, ENTRY_MEMORY, ProfileRepository, background
from rolesim.expresser import ModelExpresser
from rolesim.loop import run_scene
from rolesim.model import MockModel
from rolesim.registry import Registry
from rolesim.roles import load_role, load_scene
from rolesim.runner import TurnRunner
from rolesim.stages import model_stages
from rolesim.staged import StagedRoleAgent
from rolesim.store import ConstructStore
from rolesim.trace import JsonlTrace, MemoryTrace

ROOT = Path(__file__).resolve().parents[1]
REG = Registry.load()
ROLES = [load_role(r) for r in ("role1", "role2", "role3")]
KEY = ("i1", "role1")
PRESENT = {"role1": {"name": "林澄"}, "role2": {"name": "周越"}, "role3": {"name": "王岚"}}
BOOT = {"setting": {"place": "会议室", "time_text": "周一上午"}, "trigger_facts": ["项目延期了。"], "name": "林澄",
        "profile_summary": "数据负责人。", "core_goals": "按期交付可核验的数据", "relationships": ["周越是合作方负责人。"],
        "history_memory": "上周答应过周五交初稿。", "personality": {}, "mechanism": {}}


def rec(action_id, order, actor, kind, content, to):
    return {"action_id": action_id, "turn_index": int(action_id[1:4]), "order_index": order,
            "actor": {"actor_id": actor, "name": PRESENT.get(actor, {"name": "环境"})["name"]}, "kind": kind,
            "content": content, "recipients": {r: PRESENT[r] for r in to}}


ENV = rec("t001-env1", 0, "environment", "action", "会议开始。", ["role1", "role2", "role3"])
ZHOU = rec("t002-role2", 0, "role2", "speech", "这周五必须交，为什么还没有？", ["role1", "role3"])
WANG = rec("t003-role3", 0, "role3", "speech", "先别急，我们看看进度。", ["role1", "role2"])


class Flaky(MockModel):
    """The mock, with failures or replacement outputs injected for chosen calls of a stage."""

    def __init__(self, registry, plan=None):
        super().__init__(registry)
        self.plan, self.seen, self.views = dict(plan or {}), {}, []

    def complete(self, stage, view, schema):
        n = self.seen[stage] = self.seen.get(stage, 0) + 1
        self.views.append((stage, view))
        action = self.plan.get((stage, n))
        if isinstance(action, Exception):
            raise action
        result = super().complete(stage, view, schema)
        if callable(action):
            output = action(copy.deepcopy(result.output))
            return I.ModelResult(output, json.dumps(output, ensure_ascii=False))
        return result


def runner(model=None, trace=None):
    model = model or MockModel(REG)
    trace = trace if trace is not None else MemoryTrace()
    store = ConstructStore(REG)
    run = TurnRunner(store, model_stages(REG, model, trace=trace), ModelExpresser(REG, model, trace), trace)
    repo = ProfileRepository(REG, roles=ROLES)
    profile = repo.load("role1")
    run.begin(KEY, repo.enter(profile, BOOT), profile, repo.background("role1", BOOT))
    return run, store, trace


def stages_of(trace):
    return [next(iter(p["products"])) for p in trace.phases]


# -- ITrace -------------------------------------------------------------------------------------------
class Trace(unittest.TestCase):
    def test_jsonl_files_per_interaction_and_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, _, _ = runner(trace=JsonlTrace(tmp))
            run.run(KEY, [ZHOU], PRESENT)
            folder = Path(tmp) / "i1" / "role1"
            phases = [json.loads(l) for l in (folder / "phases.jsonl").read_text().splitlines()]
            calls = [json.loads(l) for l in (folder / "model_calls.jsonl").read_text().splitlines()]
            self.assertEqual([next(iter(p["products"])) for p in phases], ["S1", "S2"])
            self.assertEqual([c["stage"] for c in calls], ["S1", "S2", "express"])
            self.assertEqual(phases[0]["commit"]["output_state_id"], phases[1]["input_state_id"])
            self.assertIn("这周五必须交", calls[0]["input"]["records"][-1]["content"])
        with self.assertRaises(I.InterfaceError):
            JsonlTrace("x").folder(("../up", "role1"))


# -- entry through the repository (more in test_profile.py) -------------------------------------------------
class Entry(unittest.TestCase):
    def test_enter_takes_goal_and_memory_from_the_bootstrap(self):
        repo = ProfileRepository(REG, roles=ROLES)
        state = repo.enter(repo.load("role1"), BOOT)
        self.assertEqual(state.dynamic["d.goal.goals_now"][ENTRY_GOAL]["content"], "按期交付可核验的数据")
        self.assertEqual(state.memory["m.entry"][ENTRY_MEMORY]["channel"], "入场前")
        self.assertEqual((state.status["d.goal.goals_now"], state.status["m.entry"], state.status["d.relation.trust"]),
                         ("active", "active", "unknown"))
        bare = repo.enter(repo.load("role1"), {**BOOT, "core_goals": None, "history_memory": None})
        self.assertEqual(list(bare.dynamic["d.goal.goals_now"]), ["g-setup"])          # the role's own setup goal
        self.assertEqual(sorted(bare.memory["m.entry"]), sorted(i["id"] for i in ROLES[0]["setup"]["recap"]))
        self.assertEqual(background(BOOT)["总体目标"], "按期交付可核验的数据")


# -- ITurnRunner ------------------------------------------------------------------------------------------
class Flow(unittest.TestCase):
    def test_one_phase_per_record_group_then_plan_and_expression(self):
        run, store, trace = runner()
        content, state = run.run(KEY, [ENV, ZHOU, WANG], PRESENT)
        validate_action_content(content, PRESENT, "role1")
        self.assertEqual(stages_of(trace), ["S1", "S1", "S1", "S2"])
        self.assertEqual([c["stage"] for c in trace.calls], ["S1", "S1", "S1", "S2", "express"])
        self.assertEqual([p["event_id"] for p in trace.phases], ["t001-env1", "t002-role2", "t003-role3", "plan"])
        chain = [(p["input_state_id"], p["output_state_id"]) for p in trace.phases]
        self.assertTrue(all(a[1] == b[0] for a, b in zip(chain, chain[1:])))
        self.assertEqual(state.state_id, store.snapshot(KEY).state_id)
        self.assertEqual(content["thought"]["recipient_ids"], ["role1"])

    def test_feedback_from_the_plans_audience_opens_s3_once(self):
        run, _, trace = runner()
        run.run(KEY, [ZHOU], PRESENT)                       # plan addressed to role2 (the mock's partner)
        reply = rec("t004-role2", 0, "role2", "speech", "那你周一给我新日期。", ["role1"])
        again = rec("t005-role2", 0, "role2", "speech", "别忘了。", ["role1"])
        run.run(KEY, [reply, again], PRESENT)
        self.assertEqual(stages_of(trace)[2:], ["S1", "S3", "S1", "S2"])

    def test_no_feedback_from_someone_the_plan_did_not_address(self):
        run, _, trace = runner()
        run.run(KEY, [ZHOU], PRESENT)
        run.run(KEY, [rec("t004-role3", 0, "role3", "speech", "我插一句。", ["role1"])], PRESENT)
        self.assertNotIn("S3", stages_of(trace))

    def test_records_already_taken_in_are_skipped(self):
        run, _, trace = runner()
        run.run(KEY, [ZHOU], PRESENT)
        run.run(KEY, [ZHOU], PRESENT)
        self.assertEqual(stages_of(trace), ["S1", "S2", "S2"])

    def test_rejected_plan_is_replanned_with_the_reasons(self):
        def bad_strategy(output):
            output["proposals"][0]["key"] = "dm.none.thing/role2"
            return output

        model = Flaky(REG, {("S2", 1): bad_strategy})
        run, _, trace = runner(model)
        content, _ = run.run(KEY, [ZHOU], PRESENT)
        self.assertEqual(stages_of(trace), ["S1", "S2", "S2"])
        second = [v for s, v in model.views if s == "S2"][1]
        self.assertIn("unregistered_mechanism", {r["code"] for r in second.upstream["rejected"]})
        self.assertIsNotNone(content["speech"])

        model = Flaky(REG, {("S2", 1): bad_strategy, ("S2", 2): bad_strategy})
        run, _, _ = runner(model)
        with self.assertRaisesRegex(I.StageError, "rejected 2 times"):
            run.run(KEY, [ZHOU], PRESENT)

    def test_a_failed_act_resumes_without_repeating_committed_phases(self):
        model = Flaky(REG, {("express", 1): I.ModelError("timeout")})
        run, _, trace = runner(model)
        with self.assertRaises(I.StageError):
            run.run(KEY, [ZHOU], PRESENT)
        self.assertEqual(stages_of(trace), ["S1", "S2"])
        content, _ = run.run(KEY, [ZHOU], PRESENT)
        self.assertEqual(stages_of(trace), ["S1", "S2"])           # nothing committed twice
        validate_action_content(content, PRESENT, "role1")

        model = Flaky(REG, {("S1", 2): I.ModelError("timeout")})
        run, _, trace = runner(model)
        with self.assertRaises(I.StageError):
            run.run(KEY, [ZHOU, WANG], PRESENT)
        run.run(KEY, [ZHOU, WANG], PRESENT)
        self.assertEqual([p["event_id"] for p in trace.phases], ["t002-role2", "t003-role3", "plan"])

    def test_pending_feedback_survives_a_failure(self):
        model = Flaky(REG, {("S3", 1): I.ModelError("timeout")})
        run, _, trace = runner(model)
        run.run(KEY, [ZHOU], PRESENT)
        reply = rec("t004-role2", 0, "role2", "speech", "那你周一给我新日期。", ["role1"])
        with self.assertRaises(I.StageError):
            run.run(KEY, [reply], PRESENT)
        run.run(KEY, [reply], PRESENT)
        self.assertEqual(stages_of(trace), ["S1", "S2", "S1", "S3", "S2"])

    def test_sessions(self):
        run, _, _ = runner()
        with self.assertRaises(I.InterfaceError):
            run.begin(KEY, None, None)
        with self.assertRaises(I.InterfaceError):
            run.run(("i9", "role1"), [], PRESENT)


# -- the staged IRoleAgent ---------------------------------------------------------------------------------
class Agent(unittest.TestCase):
    def setUp(self):
        self.agent = StagedRoleAgent(ROLES, REG, Flaky(REG))

    def turn(self, observations, request_id):
        context = {"bootstrap": None, "observations": observations, "present_characters": PRESENT}
        return asyncio.run(self.agent.act("i1", "role1", context, request_id))

    def test_act_returns_validated_content(self):
        asyncio.run(self.agent.initialize("i1", "role1", BOOT))
        content = self.turn([ENV, ZHOU], "r1")
        validate_action_content(content, PRESENT, "role1")
        self.assertIs(type(self.turn([ENV, ZHOU], "r1")), dict)               # retried request: same result
        self.assertEqual(self.turn([ENV, ZHOU], "r1"), content)

    def test_other_thoughts_are_dropped_and_unknown_modes_refused(self):
        asyncio.run(self.agent.initialize("i1", "role1", BOOT))
        secret = rec("t002-role2", 1, "role2", "thought", "SECRET", ["role1"])
        self.turn([ZHOU, secret], "r1")
        session = self.agent.sessions[("i1", "role1")]
        self.assertTrue(any("another actor's thought" in w["warning"] for w in session.warnings))
        calls = json.dumps([c for c in self.agent.trace.calls], ensure_ascii=False)
        self.assertNotIn("SECRET", calls)
        with self.assertRaises(I.InterfaceError):
            asyncio.run(self.agent.initialize("i2", "role1", {**BOOT, "mechanism": {"mode": "no_such_mode"}}))

    def test_stage_failure_is_an_agent_call_error_and_can_be_retried(self):
        self.agent = StagedRoleAgent(ROLES, REG, Flaky(REG, {("S1", 1): I.ModelError("down")}))
        asyncio.run(self.agent.initialize("i1", "role1", BOOT))
        with self.assertRaises(I.AgentCallError):
            self.turn([ZHOU], "r1")
        validate_action_content(self.turn([ZHOU], "r2"), PRESENT, "role1")


# -- a whole scene ---------------------------------------------------------------------------------------------
class Scene(unittest.TestCase):
    def test_test3_runs_and_keeps_private_things_private(self):
        scene = load_scene("test3")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "run"
            manifest = run_scene(ROLES, scene, MockModel(REG), out, REG)
            self.assertEqual((manifest["status"], manifest["pipeline"]), ("completed", "stages"))
            for name in ("records.jsonl", "calls.jsonl", "manifest.json", "transcript.md"):
                self.assertTrue((out / name).exists(), name)
            records = [json.loads(l) for l in (out / "records.jsonl").read_text().splitlines()]
            self.assertTrue(all(set(r["recipients"]) == {r["actor"]["actor_id"]} for r in records if r["kind"] == "thought"))
            secrets = [s for s in _strings(json.loads((ROOT / "scenes" / "test3.json").read_text())["world_private"]) if len(s) >= 4]
            self.assertTrue(secrets)
            for path in out.rglob("*"):
                if path.is_file() and path.name != "scene.json":       # scene.json is the scene as given
                    text = path.read_text(encoding="utf-8")
                    self.assertFalse([s for s in secrets if s in text], path)
            self.assertTrue(all(s in (out / "scene.json").read_text(encoding="utf-8") for s in secrets))
            trace = out / "trace" / manifest["interaction_id"]
            for r in records:
                if r["actor"]["actor_id"] == "environment" and len(r["recipients"]) == 1:
                    rid = f"{r['action_id']}#{r['order_index']}"
                    holders = [f.name for f in trace.iterdir() if rid in (f / "model_calls.jsonl").read_text()]
                    self.assertEqual(holders, list(r["recipients"]))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = subprocess.run([sys.executable, "-m", "rolesim", "run", "--backend", "mock", "--scene", "test1",
                                   "--out", str(Path(tmp) / "r")],
                                  cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads((Path(tmp) / "r" / "manifest.json").read_text())["status"], "completed")
            refused = subprocess.run([sys.executable, "-m", "rolesim", "run", "--mode", "no_such_mode", "--backend", "mock",
                                      "--out", str(Path(tmp) / "r2")], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


if __name__ == "__main__":
    unittest.main()
