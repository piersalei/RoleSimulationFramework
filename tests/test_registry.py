"""IRegistry (rolesim/registry.py): extraction from the psychology v1 sources and the lookups."""
import dataclasses
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rolesim import interfaces as I
from rolesim.registry import (FILES, REGISTRY_PATH, SOURCE_DIR, Registry, RegistryBuildError,
                              build_registry)
from rolesim.state import OPERATORS

ROOT = Path(__file__).resolve().parents[1]
REG = Registry.load()


class SavedFile(unittest.TestCase):
    def test_registry_json_is_current(self):
        """Regenerate with `python3 -m rolesim registry` after an intentional source update."""
        self.assertEqual(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")), build_registry(SOURCE_DIR))

    def test_cli_writes_the_same_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "registry.json"
            subprocess.run([sys.executable, "-m", "rolesim", "registry", "--out", str(out)], cwd=ROOT,
                           check=True, capture_output=True)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), build_registry(SOURCE_DIR))

    def test_sources_hashed(self):
        self.assertEqual([s["file"] for s in REG.sources], list(FILES.values()))
        self.assertTrue(all(len(s["sha256"]) == 64 for s in REG.sources))


class Counts(unittest.TestCase):
    def test_counts_stated_by_the_sources(self):
        layers = [REG.construct(c).layer for c in REG.ids("construct")]
        self.assertEqual((layers.count("stable"), layers.count("dynamic"), layers.count("memory")), (42, 42, 3))
        self.assertEqual(len(REG.ids("mechanism")), 75)
        self.assertEqual(REG.ids("operator"), I.OPERATOR_NAMES)
        self.assertEqual(set(REG.ids("operator")), set(OPERATORS))


class Writers(unittest.TestCase):
    def test_one_primary_writer_per_dynamic_or_memory_construct(self):
        for cid in REG.ids("construct"):
            c = REG.construct(cid)
            if c.layer == "stable":
                self.assertIsNone(c.writer, cid)
                continue
            self.assertIn(c.writer, I.OPERATOR_NAMES, cid)
            self.assertIn(cid, REG.operator(c.writer).writes, cid)
            others = [op for op in REG.ids("operator")
                      if op not in (c.writer, "decay_or_recovery") and cid in REG.operator(op).writes]
            self.assertEqual(others, [], cid)

    def test_no_operator_writes_stable(self):
        for op in REG.ids("operator"):
            self.assertFalse([w for w in REG.operator(op).writes if w.startswith("s.")], op)

    def test_decay_writes_exactly_the_constructs_with_a_time_rule(self):
        timed = {cid for cid in REG.ids("construct") if REG.construct(cid).decay}
        self.assertEqual(set(REG.operator("decay_or_recovery").writes), timed)
        self.assertIn("朝向情绪基线", REG.construct("d.affect.emotion").decay)
        self.assertIsNone(REG.construct("d.relation.trust").decay)         # 只能由关系事件或修复证据改变


class StagesAndGroups(unittest.TestCase):
    """Each stage skill §4.2 lists what it calls and what it does not call."""

    def test_operator_stages(self):
        stages = {op: REG.operator(op).stages for op in REG.ids("operator")}
        self.assertEqual(stages["relation_update"], ("S3",))
        self.assertEqual(stages["other_model_update"], ("S1",))
        self.assertEqual(stages["decay_or_recovery"], ("S3",))
        self.assertEqual(stages["emotion_update"], ("S1", "S2"))
        self.assertEqual(stages["belief_revision"], ("S1", "S3"))
        self.assertEqual(len([op for op, s in stages.items() if "S3" in s]), 9)   # "调用九个更新算子"
        for op in ("relation_update", "belief_revision", "other_model_update", "decay_or_recovery"):
            self.assertNotIn("S2", stages[op], op)

    def test_dependency_groups(self):
        group = {op: REG.operator(op).group for op in REG.ids("operator")}
        self.assertEqual({group[o] for o in ("emotion_update", "self_state_update", "relation_update",
                                             "group_state_update")}, {"affect_self_relation_group"})
        self.assertEqual({group[o] for o in ("goal_update", "commitment_update", "strategy_learning")},
                         {"goal_commitment_strategy"})
        self.assertEqual((group["memory_update"], group["decay_or_recovery"]), ("memory", "decay"))
        self.assertEqual({group["belief_revision"], group["other_model_update"]}, {"ungrouped"})

    def test_mechanism_stages_point_at_registered_sections(self):
        appraisal = REG.mechanism("dm.affect.primary_secondary_appraisal")
        self.assertEqual(appraisal.stages, ("S1",))
        self.assertEqual(REG.mechanism("dm.prosocial.responsibility_taking").stages, ("S2", "S3"))
        self.assertEqual(REG.mechanism("dm.conflict.aggression_punishment").stages, ())


class Mechanisms(unittest.TestCase):
    def test_cards(self):
        cards = {d for d in REG.ids("mechanism") if REG.mechanism(d).card}
        self.assertEqual(cards, {"dm.affect.primary_secondary_appraisal", "dm.cog.belief_updating",
                                 "dm.cog.attribution_responsibility", "dm.social.trust_reputation_update",
                                 "dm.coop.trust_building", "dm.affect.defense", "dm.conflict.relationship_repair"})
        self.assertEqual(set(REG.mechanism("dm.affect.primary_secondary_appraisal").hands_to),
                         {"emotion_update", "goal_update", "self_state_update"})
        self.assertEqual(REG.mechanism("dm.coop.trust_building").hands_to, ("relation_update",))

    def test_options(self):
        appraisal = REG.mechanism("dm.affect.primary_secondary_appraisal")
        self.assertEqual(dict(appraisal.option_groups)["初级"], ("无关", "积极", "损失", "威胁", "挑战", "收益"))
        self.assertIn("每轮沿用首次评价为评价固着", appraisal.notes)
        # brackets stay inside one option
        self.assertIn("前景理论（参考点、损失厌恶、概率加权）", REG.mechanism("dm.choice.choice_rule").options)
        self.assertEqual(REG.mechanism("dm.choice.temporal_discounting").options,
                         ("即时回报优先", "平衡", "未来回报优先"))
        for dm_id in REG.ids("mechanism"):
            m = REG.mechanism(dm_id)
            self.assertTrue(m.options, dm_id)
            for item in m.options:
                self.assertTrue(item and "；" not in item and "：" not in item, (dm_id, item))
            self.assertEqual(set(m.options), {i for _, items in m.option_groups for i in items}, dm_id)
            self.assertIn(m.basis["options"].split("§7：", 1)[1], (SOURCE_DIR / FILES["registry"]).read_text())


class Constructs(unittest.TestCase):
    def test_spot_checks_against_cited_text(self):
        trust = REG.construct("d.relation.trust")
        self.assertEqual((trust.writer, trust.tempo, trust.self_visibility, trust.high_impact),
                         ("relation_update", "slow", "aware", True))
        self.assertEqual(REG.construct("d.affect.emotion").tempo, "fast")
        self.assertEqual(REG.construct("d.policy.action_commitment").tempo, "categorical")
        self.assertEqual(REG.construct("s.traits.social_cognition_71d").self_visibility, "unaware")
        self.assertEqual(REG.construct("s.traits.affect.regulation_capacity").high_impact, True)
        self.assertEqual(REG.construct("s.traits.cognitive.flexibility").high_impact, False)
        self.assertEqual(REG.construct("d.cognition.shame").self_visibility, "partial")
        self.assertEqual(REG.construct("d.relation.trust").module, "relation_state")
        self.assertEqual(REG.construct("s.traits.personality.p1_big_five").section, "4.2")

    def test_every_derived_attribute_has_a_basis(self):
        for cid in REG.ids("construct"):
            c = REG.construct(cid)
            for attr in ("writer", "tempo", "decay", "self_visibility"):
                if getattr(c, attr) is not None:
                    self.assertIn(attr, c.basis, (cid, attr))
            if c.high_impact:
                self.assertIn("high_impact", c.basis, cid)
        for op in REG.ids("operator"):
            self.assertEqual(set(REG.operator(op).basis), {"writes", "group", "stages"}, op)

    def test_review_lists_what_the_sources_leave_open(self):
        topics = {r["topic"]: r["items"] for r in REG.review}
        self.assertEqual(len(topics), 5)
        no_stage = next(items for t, items in topics.items() if t.startswith("没有任何阶段调用"))
        self.assertTrue(any(i.startswith("dm.conflict.aggression_punishment") for i in no_stage))


class Lookups(unittest.TestCase):
    def test_types_and_errors(self):
        self.assertIsInstance(REG.construct("m.entry"), I.Construct)
        self.assertIsInstance(REG.mechanism("dm.cog.belief_updating"), I.DecisionMechanism)
        self.assertIsInstance(REG.operator("goal_update"), I.OperatorRule)
        for call, arg in ((REG.construct, "d.affect.mood"), (REG.mechanism, "dm.cog.none"), (REG.operator, "free_write")):
            with self.assertRaises(KeyError):
                call(arg)
        with self.assertRaises(ValueError):
            REG.ids("stage")

    def test_conforms_to_iregistry(self):
        for method in ("construct", "mechanism", "operator", "ids"):
            self.assertTrue(callable(getattr(REG, method)))
            self.assertTrue(hasattr(I.IRegistry, method))

    def test_read_only(self):
        c = REG.construct("d.relation.trust")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            c.writer = "emotion_update"
        with self.assertRaises(TypeError):
            c.basis["writer"] = "x"

    def test_wrong_schema_rejected(self):
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            Registry({**data, "schema_version": "registry-0"})


class SafetyNet(unittest.TestCase):
    """A changed source must fail the build instead of producing a silently different registry."""

    def build_with(self, key, old, new):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "src"
            shutil.copytree(SOURCE_DIR, target)
            path = target / FILES[key]
            text = path.read_text(encoding="utf-8")
            self.assertIn(old, text)
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return build_registry(target)

    def test_unchanged_copy_builds_the_same(self):
        self.assertEqual(self.build_with("rules", "", ""), build_registry(SOURCE_DIR))

    def test_reading_phrase_gone(self):
        with self.assertRaises(RegistryBuildError):
            self.build_with("state", "角色对自己的人格只有部分自知", "角色对自己的人格完全自知")

    def test_new_decay_bullet(self):
        with self.assertRaises(RegistryBuildError):
            self.build_with("rules", "- 羞耻负荷，朝向零：7 天。", "- 羞耻负荷，朝向零：7 天。\n- 亲近，朝向零：30 天。")

    def test_stated_count_changes(self):
        with self.assertRaises(RegistryBuildError):
            self.build_with("overview", "登记 75 个决策过程", "登记 76 个决策过程")

    def test_second_writer(self):
        with self.assertRaises(RegistryBuildError):
            self.build_with("rules", "写入行动承诺（d.policy.action_commitment）",
                            "写入行动承诺（d.policy.action_commitment）、信任（d.relation.trust）")

    def test_option_fix_stale(self):
        with self.assertRaises(RegistryBuildError):
            self.build_with("registry", "依 BAS、BIS、FFFS 基线接近", "依 BAS、BIS、FFFS 基线趋近")


if __name__ == "__main__":
    unittest.main()
