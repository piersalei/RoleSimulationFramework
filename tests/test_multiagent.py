import copy
import json
import tempfile
import unittest
from pathlib import Path

from fixtures import load, setup
from rolesim.contract import ProposedAction, Visibility
from rolesim.providers import MockBackend
from rolesim.readable import FILENAME
from rolesim.roles import fit_cast, load_role, load_scene, run
from rolesim.runtime import DialogueEnvironment, WorldAgent, validate_scenario

CAST = ["role1", "role2", "role3"]


def three_role_run(folder, backend=None):
    roles = [load_role(r) for r in CAST]
    return run(roles, load_scene("test3"), backend or MockBackend(), Path(folder) / "run")


class Capture(MockBackend):
    def __init__(self):
        self.calls = []

    def appraise(self, payload):
        self.calls.append(copy.deepcopy(payload))
        return super().appraise(payload)

    def decide(self, payload):
        self.calls.append(copy.deepcopy(payload))
        return super().decide(payload)

    def express(self, payload):
        self.calls.append(copy.deepcopy(payload))
        return super().express(payload)


class SceneSchemaTests(unittest.TestCase):
    def setUp(self):
        self.scene = load("test3")

    def patched(self, **patch):
        return {**copy.deepcopy(self.scene), **patch}

    def test_shipped_three_role_scene_is_valid(self):
        validate_scenario(self.scene)
        self.assertEqual([a["kind"] for a in self.scene["agents"]], ["role", "role", "role", "environment"])

    def test_role_count_and_single_environment(self):
        roles = [a for a in self.scene["agents"] if a["kind"] == "role"]
        env = self.scene["agents"][-1]
        many = [{"id": f"s{i}", "kind": "role", "title": f"座位{i}"} for i in range(7)]
        for agents in (roles[:1], many, roles + [env, {**env, "id": "env2", "title": "走廊"}]):
            with self.subTest(n=len(agents)), self.assertRaises(ValueError):
                validate_scenario(self.patched(agents=agents, max_turns=12))

    def test_old_scene_schema_is_rejected(self):
        old = {k: v for k, v in load("test1").items() if k != "agents"}
        old.update(schema_version="2.0", seats=[{"id": "a", "title": "甲"}, {"id": "b", "title": "乙"}])
        with self.assertRaises(ValueError):
            validate_scenario(old)

    def test_environment_rules_are_checked(self):
        def env_with(**patch):
            env = copy.deepcopy(self.scene["agents"][-1])
            for key, value in patch.items():
                env[key] = value if key != "rules" else {**env["rules"], **value}
            return self.patched(agents=self.scene["agents"][:-1] + [env])
        bad = [env_with(world={"time": "25:00"}), env_with(world={"mood": "紧张"}),
               env_with(rules={"events": [{"round": 1, "text": "太早"}]}),
               env_with(rules={"events": [{"round": 2, "text": "x", "audience": ["nobody"]}]}),
               env_with(rules={"events": [{"round": 2, "text": "x", "set": {"noise": "吵"}}]}),
               env_with(rules={"events": [{"round": 2, "text": "x", "cues": {"anger": .5}}]}),
               env_with(rules={"minutes_per_round": -1}), env_with(psychology={"tension": .5})]
        for scene in bad:
            with self.subTest(scene=scene["agents"][-1]), self.assertRaises(ValueError):
                validate_scenario(scene)

    def test_cast_must_fill_every_role_seat(self):
        scene = load_scene("test3")
        self.assertFalse(fit_cast([load_role("role1"), load_role("role2")], scene)["ok"])
        self.assertTrue(fit_cast([load_role(r) for r in CAST], scene)["ok"])


class WorldAgentTests(unittest.TestCase):
    def test_clock_wraps_and_events_follow_their_rounds(self):
        spec = copy.deepcopy(load("test3")["agents"][-1])
        spec["world"]["time"] = "23:55"
        world = WorldAgent(spec, {"project": "role1", "partner": "role2", "pi": "role3"})
        items = world.tick(3, 7, CAST)
        self.assertEqual(world.world["time"], "00:05")
        private = [i for i in items if i[0] == "world:3:1"][0]
        self.assertEqual(private[2], ["role1"])
        self.assertEqual(world.history[-1]["changed"], {"time": "00:05"})

    def test_environment_ticks_at_the_start_of_each_later_round(self):
        env = DialogueEnvironment(load("test3"), CAST)
        env.open()
        order = []
        for turn, role in env.turns():
            order.append(role)
            line = ProposedAction(f"{role}@t{turn:03d}#1", "speech", "嗯。", Visibility("public", CAST), [], "wait")
            env.relay(line, turn, role)
        self.assertEqual(order, CAST * 4)
        ids = [e.event_id for e in env.log if e.actor == "env"]
        self.assertEqual(ids, ["open", "world:2:time", "world:2:1", "world:3:time", "world:3:1",
                               "world:4:time", "world:4:1"])
        private = next(e for e in env.log if e.event_id == "world:3:1")
        self.assertEqual((private.visibility.scope, private.visibility.audience), ("private", ["role1"]))
        self.assertEqual(env.world.world, {"time": "15:30", "place": "项目组会议室", "weather": "小雨"})


class ThreeRoleRunTests(unittest.TestCase):
    def test_three_roles_and_the_environment_run_and_pass_audit(self):
        backend = Capture()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = three_role_run(tmp, backend)
            out = Path(tmp) / "run"
            self.assertTrue(manifest["audit"]["passed"], manifest["audit"])
            self.assertEqual(manifest["environment"], {"id": "env", "title": "会议室"})
            self.assertEqual([c["role_id"] for c in manifest["cast"]], CAST)
            public = [json.loads(l) for l in (out / "public.jsonl").read_text().splitlines()]
            self.assertEqual([p["speaker"] for p in public if p["speaker"] != "environment"], CAST * 4)
            world = [json.loads(l) for l in (out / "world.jsonl").read_text().splitlines()]
            self.assertEqual([w["world"]["time"] for w in world], ["15:00", "15:10", "15:20", "15:30"])
            state = json.loads((out / "roles" / "role1" / "state.json").read_text())
            self.assertEqual(set(state["dynamic"]["relation_state"]), {"role2", "role3"})
            transcript = (out / FILENAME).read_text()
            self.assertIn("环境：会议室", transcript)
            self.assertIn("只有林澄知道", transcript)
            self.assertIn("对周越", transcript)
            self.assertIn("对王岚", transcript)
        private = "第三批数据今晚能清洗完"
        seen = {p["role"]["id"] for p in backend.calls if private in json.dumps(p, ensure_ascii=False)}
        self.assertEqual(seen, {"role1"})
        decide = next(p for p in backend.calls if "allowed_actions" in p and p["role"]["id"] == "role3")
        self.assertEqual(decide["others_present"], ["项目组数据负责人", "合作方负责人"])
        for role in CAST:
            for item in setup(role)["conceal"]:
                owners = {p["role"]["id"] for p in backend.calls if item["text"] in json.dumps(p, ensure_ascii=False)}
                self.assertEqual(owners, {role})

    def test_environment_cues_drive_state_without_appraisal(self):
        with tempfile.TemporaryDirectory() as tmp:
            three_role_run(tmp)
            records = [json.loads(l) for l in (Path(tmp) / "run" / "roles" / "role2" / "audit.jsonl")
                       .read_text().splitlines()]
        appraised = {a["event_id"] for r in records for a in r["appraisals"]}
        self.assertNotIn("world:4:1", appraised)
        changes = [c for r in records for c in r["step_delta"]["changes"] if c["evidence"] == ["world:4:1"]]
        self.assertEqual(changes[0]["mechanism"], "emotion_update")
        self.assertEqual(changes[0]["inputs"]["pressure"], .5)


if __name__ == "__main__":
    unittest.main()
