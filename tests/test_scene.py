"""Scene files and the world agent (scene.py), the reference catalog, strict JSON, code version."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.catalog import build_catalog, digest
from rolesim.stage.providers import strict_json
from rolesim.role.roles import fit_cast, load_role, load_scene
from rolesim.run.scene import WorldAgent, code_version, run_name, validate_scenario

ROOT = Path(__file__).resolve().parents[1]
CAST = ["role1", "role2", "role3"]


def scene(name="test3"):
    return json.loads((ROOT / "scenes" / f"{name}.json").read_text(encoding="utf-8"))


class SceneSchema(unittest.TestCase):
    def setUp(self):
        self.scene = scene()

    def patched(self, **patch):
        return {**copy.deepcopy(self.scene), **patch}

    def test_shipped_scenes_are_valid(self):
        for name in ("test1", "test2", "test3"):
            validate_scenario(scene(name))
        self.assertEqual([a["kind"] for a in self.scene["agents"]], ["role", "role", "role", "environment"])

    def test_background_and_seats_only(self):
        base = scene("test1")
        for patch in ({"events": []}, {"schema_version": "1.2"}, {"max_turns": 1}, {"max_turns": True},
                      {"agents": base["agents"][:1]}, {"allowed_actions": ["declare_success"]},
                      {"agents": [{**a, "kind": "observer"} for a in base["agents"]]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                validate_scenario({**base, **patch})

    def test_role_count_and_single_environment(self):
        roles = [a for a in self.scene["agents"] if a["kind"] == "role"]
        env = self.scene["agents"][-1]
        many = [{"id": f"s{i}", "kind": "role", "title": f"座位{i}"} for i in range(7)]
        for agents in (roles[:1], many, roles + [env, {**env, "id": "env2", "title": "走廊"}]):
            with self.subTest(n=len(agents)), self.assertRaises(ValueError):
                validate_scenario(self.patched(agents=agents, max_turns=12))

    def test_old_scene_schema_is_rejected(self):
        old = {k: v for k, v in scene("test1").items() if k != "agents"}
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
        for item in bad:
            with self.subTest(scene=item["agents"][-1]), self.assertRaises(ValueError):
                validate_scenario(item)

    def test_cast_must_fill_every_role_seat(self):
        test3 = load_scene("test3")
        self.assertFalse(fit_cast([load_role("role1"), load_role("role2")], test3)["ok"])
        self.assertTrue(fit_cast([load_role(r) for r in CAST], test3)["ok"])


class World(unittest.TestCase):
    def test_clock_wraps_and_events_follow_their_rounds(self):
        spec = copy.deepcopy(scene()["agents"][-1])
        spec["world"]["time"] = "23:55"
        world = WorldAgent(spec, {"project": "role1", "partner": "role2", "pi": "role3"})
        items = world.tick(3, 7, CAST)
        self.assertEqual(world.world["time"], "00:05")
        private = [i for i in items if i[0] == "world:3:1"][0]
        self.assertEqual(private[2], ["role1"])
        self.assertEqual(world.history[-1]["changed"], {"time": "00:05"})


class Catalog(unittest.TestCase):
    def test_catalog_matches_sources_and_references(self):
        catalog = build_catalog(ROOT / "data" / "sources")
        saved = json.loads((ROOT / "data" / "catalog.json").read_text())
        self.assertEqual(digest(catalog), digest(saved))
        ids = [r["id"] for r in catalog["sections"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["status"] == "reference_only" for r in catalog["sections"]))
        persona = load_role("role1")["persona"]
        for source in persona["provenance"].values():
            self.assertIn(source["source_ref"], ids)

    def test_nonstandard_json_is_rejected(self):
        with self.assertRaises(ValueError):
            strict_json('{"value": NaN}')


class RunRecords(unittest.TestCase):
    def test_code_version_degrades_explicitly_outside_a_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(code_version(Path(tmp)), {"git_sha": "unavailable", "git_dirty": None})
        self.assertNotEqual(code_version()["git_sha"], "unavailable")

    def test_run_names_start_with_local_time(self):
        name = run_name("test3", "Qwen/Qwen3 8B")
        self.assertRegex(name, r"^\d{8}-\d{6}_test3_Qwen-Qwen3-8B$")


if __name__ == "__main__":
    unittest.main()
