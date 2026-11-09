"""IProfileCompiler (profile.py), IRoleRepository (repository.py) and conditioned phrases in prompts."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim import interfaces as I
from rolesim.stage.evidence import EvidenceGate
from rolesim.stage.expresser import ModelExpresser
from rolesim.stage.model import MockModel, view_payload
from rolesim.role.profile import (PROFILE_FORMAT, REVIEW_HIGH_IMPACT, REVIEW_NO_PHRASE, ProfileCompiler, check_phrase,
                             profile_from_persona)
from rolesim.role.registry import Registry
from rolesim.role.repository import ENTRY_MEMORY, ProfileRepository, tom_ceiling
from rolesim.role.roles import load_role
from rolesim.stage.stages import TENDENCIES, ModelStage, self_profile

REG = Registry.load()
ROLES = [load_role(r) for r in ("role1", "role2")]
P1 = "s.traits.personality.p1_big_five"
TOM_FACTS = "s.traits.social_cognition_71d"
PHRASE = "重要的人回复变慢时，你会更快担心被冷落，忍不住多问一句；对方给出清楚的答复后，你会慢慢放下心来。"
BOOT = {"setting": {"place": "会议室", "time_text": None}, "trigger_facts": ["项目延期了。"], "name": "林澄",
        "profile_summary": "数据负责人。", "core_goals": None, "relationships": [], "history_memory": None,
        "personality": {}, "mechanism": {}}


def profile(**stable):
    return {"format": PROFILE_FORMAT, "id": "p1", "name": "测试",
            "stable": {cid.replace("__", "."): entry for cid, entry in stable.items()}}


def compile_(raw, config=None, model=None):
    return ProfileCompiler(REG, model).compile(raw, config)


class Scripted:
    def __init__(self, modules):
        self.modules = modules

    def complete(self, stage, view, schema):
        return I.ModelResult({"modules": self.modules}, "{}")


# -- IProfileCompiler -------------------------------------------------------------------------------------
class Compile(unittest.TestCase):
    def test_explicit_unknown_default_and_disabled(self):
        prof, review = compile_(profile(s__traits__attachment__global={"value": {"anxiety": 1.5, "avoidance": -0.5}}),
                                I.EnableConfig(modules={"s.values.moral_reasoning": False}))
        attachment = prof.modules["s.traits.attachment.global"]
        self.assertEqual((attachment.status, attachment.source_kind, attachment.needs_review), ("active", "explicit", False))
        self.assertEqual(len(prof.modules), 42)
        self.assertEqual((prof.modules["s.traits.self.self_esteem"].status, prof.modules["s.traits.self.self_esteem"].reason),
                         ("unknown", "档案未提供"))
        self.assertEqual((prof.modules[TOM_FACTS].status, prof.modules[TOM_FACTS].source_kind, prof.modules[TOM_FACTS].value),
                         ("active", "default", {}))
        self.assertEqual(prof.modules["s.values.moral_reasoning"].status, "disabled")
        self.assertEqual(review, [])                            # attachment is partly self-known: no phrase needed

    def test_one_personality_scheme(self):
        prof, _ = compile_(profile(**{P1.replace(".", "__"): {"value": {"neuroticism.anxiety": 1.0}}}))
        self.assertEqual([prof.modules[c].status for c in (P1, "s.traits.personality.p2_16pf", "s.traits.personality.p3_jung_hexaco")],
                         ["active", "disabled", "disabled"])
        both = profile(**{P1.replace(".", "__"): {"value": {"neuroticism.anxiety": 1.0}},
                          "s__traits__personality__p2_16pf": {"value": {"warmth": 1}}})
        with self.assertRaisesRegex(I.InterfaceError, "one personality scheme"):
            compile_(both)

    def test_value_forms_and_structure(self):
        bad = [profile(**{P1.replace(".", "__"): {"value": {"neuroticism.nerves": 1}}}),
               profile(**{P1.replace(".", "__"): {"value": {"neuroticism.anxiety": 3}}}),
               profile(s__values__schwartz_priorities={"value": {"weights": {"achievement": 0.5}}}),
               profile(s__traits__social_cognition_71d={"value": {"1.1.4": 1.5}}),
               profile(s__traits__cognitive__flexibility={"value": "high"}),
               profile(s__traits__attachment__global={"value": {"anxiety": 1}, "source": "inferred"}),
               profile(s__traits__attachment__global={"value": {"anxiety": 1}, "confidence": 2}),
               profile(s__traits__attachment__global={"source": "explicit"}),
               profile(d__relation__trust={"value": {}}),
               profile(s__traits__nothing={"value": 1}),
               {**profile(), "colour": "blue"}, {"stable": {}}]
        for raw in bad:
            with self.assertRaises(I.InterfaceError, msg=json.dumps(raw, ensure_ascii=False)):
                compile_(raw)

    def test_review_items(self):
        raw = profile(s__traits__function__domains={"value": {"identity": 2}, "source": "inferred", "basis": "规则：长期叙述"},
                      s__values__schwartz_priorities={"value": {"weights": {"achievement": 1.0}}},
                      s__traits__development__position={"value": "E5", "phrase": PHRASE})
        prof, review = compile_(raw)
        function = prof.modules["s.traits.function.domains"]
        self.assertEqual((function.needs_review, function.reason), (True, "规则：长期叙述"))
        reasons = {(r.construct_id, r.reason) for r in review}
        self.assertIn(("s.traits.function.domains", REVIEW_HIGH_IMPACT), reasons)
        self.assertIn(("s.traits.function.domains", REVIEW_NO_PHRASE), reasons)          # unaware, no phrase
        self.assertNotIn("s.values.schwartz_priorities", {r.construct_id for r in review})  # explicit
        self.assertNotIn(("s.traits.development.position", REVIEW_NO_PHRASE), reasons)   # has a phrase

    def test_phrases_are_checked(self):
        for phrase in ("你总是担心被拒绝，会追问很多次。", "依恋焦虑为 2 时你会追问。", "受 s.traits.attachment 影响，你会追问。"):
            self.assertIsNotNone(check_phrase(phrase))
            prof, review = compile_(profile(s__traits__development__position={"value": "E5", "phrase": phrase}))
            self.assertIsNone(prof.modules["s.traits.development.position"].phrase)
            self.assertTrue(any(r.reason.startswith("条件化语句已去掉") for r in review))
        self.assertIsNone(check_phrase(PHRASE))

    def test_snapshot_ids(self):
        raw = profile(s__traits__attachment__global={"value": {"anxiety": 1.5}})
        self.assertEqual(compile_(raw)[0].snapshot_id, compile_(copy.deepcopy(raw))[0].snapshot_id)
        changed = profile(s__traits__attachment__global={"value": {"anxiety": 1.0}})
        self.assertNotEqual(compile_(raw)[0].snapshot_id, compile_(changed)[0].snapshot_id)


class FromText(unittest.TestCase):
    TEXT = "林澄三十出头，是项目的数据负责人。她说话很慢，习惯先确认再答应。"

    def test_mock_reads_a_quote_from_the_text(self):
        prof, review = compile_(self.TEXT, model=MockModel(REG))
        speech = prof.modules["s.speech_style.baseline"]
        self.assertEqual((speech.source_kind, speech.source_text), ("model_inferred", "她说话很慢，习惯先确认再答应"))
        self.assertEqual(review, [])
        self.assertEqual(compile_(self.TEXT)[0].modules["s.speech_style.baseline"].status, "unknown")    # no model

    def test_candidates_that_fail_go_to_review(self):
        modules = [{"construct_id": "s.traits.attachment.global", "value": {"anxiety": 1}, "quote": "她很怕被冷落", "phrase": None},
                   {"construct_id": "s.speech_style.baseline", "value": {"d": "慢"}, "quote": "她说话很慢", "phrase": None},
                   {"construct_id": "s.traits.cognitive.flexibility", "value": 9, "quote": "习惯先确认再答应", "phrase": None},
                   {"construct_id": "s.core_goals.primary", "value": ["按期交付"], "quote": "是项目的数据负责人", "phrase": None},
                   {"construct_id": "s.core_goals.primary", "value": ["别的"], "quote": "是项目的数据负责人", "phrase": None}]
        raw = {"format": PROFILE_FORMAT, "id": "p1", "text": self.TEXT,
               "stable": {"s.speech_style.baseline": {"value": {"d": "明写"}}}}
        prof, review = compile_(raw, model=Scripted(modules))
        reasons = [r.reason for r in review]
        self.assertTrue(any("引文不在档案原文里" in r for r in reasons))
        self.assertTrue(any("档案已明写" in r for r in reasons))
        self.assertTrue(any("取值不合形式" in r for r in reasons))
        self.assertTrue(any("推断了两次" in r for r in reasons))
        self.assertEqual(prof.modules["s.speech_style.baseline"].value, {"d": "明写"})
        self.assertEqual(prof.modules["s.core_goals.primary"].source_kind, "model_inferred")
        self.assertTrue(prof.modules["s.core_goals.primary"].needs_review)                  # high impact, inferred


class FromRoleFile(unittest.TestCase):
    def test_persona_modules_become_explicit(self):
        raw = profile_from_persona(ROLES[0]["persona"], "role1")
        stable = ROLES[0]["persona"]["stable"]
        self.assertEqual(raw["stable"][P1]["value"], stable["traits"]["personality"]["facets"])
        prof, _ = compile_(raw)
        self.assertEqual({c for c, m in prof.modules.items() if m.source_kind == "explicit"},
                         {P1, "s.traits.attachment.global", "s.values.schwartz_priorities", "s.core_goals.primary",
                          "s.speech_style.baseline", "s.identity.social_roles"})


# -- IRoleRepository --------------------------------------------------------------------------------------
class Repository(unittest.TestCase):
    def repo(self, root=None, files=None):
        root = Path(root or tempfile.mkdtemp())
        for name, data in (files or {}).items():
            (root / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return ProfileRepository(REG, roles=ROLES, root=root)

    def test_load(self):
        repo = self.repo()
        self.assertIs(repo.load("role1"), repo.load("role1"))
        self.assertIn("role1", repo.review)
        with self.assertRaises(I.InterfaceError):
            repo.load("nobody")
        own = {**profile(s__traits__attachment__global={"value": {"anxiety": -1}}), "id": "role1"}
        self.assertEqual(self.repo(files={"role1": own}).load("role1").modules["s.traits.attachment.global"].value,
                         {"anxiety": -1})                                           # the profile file wins
        with self.assertRaisesRegex(I.InterfaceError, "does not match"):
            self.repo(files={"role1": {**own, "id": "role9"}}).load("role1")

    def test_entry_defaults(self):
        raw = {**profile(s__traits__affect__emotion_baseline={"value": {"valence": 0.1, "arousal": 0.3}},
                         s__traits__self__self_esteem={"value": {"global": {"level": 0.5}}},
                         s__identity__life_history={"value": [{"event": "十年前项目失败过一次", "importance": 0.9}]},
                         s__core_goals__primary={"value": [{"goal_id": "g1", "text": "按期交付", "priority_baseline": 0.8}]}),
               "id": "role1"}
        repo = self.repo(files={"role1": raw})
        state = repo.enter(repo.load("role1"), {**BOOT, "history_memory": "上周答应过周五交初稿。"})
        d, m, status = state.dynamic, state.memory, state.status
        self.assertEqual(d["d.affect.emotion"], {"label": [], "intensity": 0.0, "valence": 0.1, "arousal": 0.3})
        self.assertEqual((d["d.affect.stress"], d["d.cognition.shame"]), ({"level": 0.0}, {"load": 0.0}))
        self.assertEqual(status["d.affect.regulation_state"], "inactive")
        self.assertEqual(d["d.cognition.self_evaluation"], {"global": 0.5})
        self.assertEqual(d["d.goal.goal_commitment"], {"g1": 0.8})
        self.assertEqual((d["d.cognition.tom_level"]["ceiling"], d["d.cognition.tom_level"]["current"]), (2, 2))
        self.assertEqual(sorted(m["m.entry"]), ["m-entry", "m-life-1"])
        self.assertEqual(m["m.accessibility"]["m-life-1"], 0.9)
        self.assertEqual(m["m.entry"][ENTRY_MEMORY]["channel"], "入场前")
        self.assertEqual((status["d.relation.trust"], status["d.goal.feasibility_estimate"]), ("unknown", "unknown"))
        self.assertTrue(any(n.startswith("d.cognition.tom_level") and "需审核" in n for n in repo.entry_notes["role1"]))

    def test_explicit_entry_state_wins_and_is_checked(self):
        raw = {**profile(), "id": "role1", "entry": {"d.affect.stress": {"level": 0.4},
                                                     "d.relation.trust": {"role2": {"value": 0.6}}}}
        repo = self.repo(files={"role1": raw})
        state = repo.enter(repo.load("role1"), BOOT)
        self.assertEqual((state.dynamic["d.affect.stress"], state.dynamic["d.relation.trust"]),
                         ({"level": 0.4}, {"role2": {"value": 0.6}}))
        for entry in ({"d.affect.stress": {"level": 1.4}}, {"d.relation.trust": [0.6]}, {"s.values.moral_reasoning": {}}):
            repo = self.repo(files={"role1": {**raw, "entry": entry}})
            with self.assertRaises(I.InterfaceError):
                repo.enter(repo.load("role1"), BOOT)

    def test_disabled_constructs_get_no_default(self):
        repo = ProfileRepository(REG, roles=ROLES, root=tempfile.mkdtemp(), config=I.EnableConfig(fields={"d.affect.stress": False}))
        state = repo.enter(repo.load("role1"), BOOT)
        self.assertEqual((state.status["d.affect.stress"], state.dynamic["d.affect.stress"]), ("disabled", {}))

    def test_theory_of_mind_ceiling(self):
        def ceiling(items=None, age=None):
            stable = {}
            if items is not None:
                stable["s__traits__social_cognition_71d"] = {"value": items}
            if age is not None:
                stable["s__identity__demographics"] = {"value": {"age": age}}
            return tom_ceiling(compile_(profile(**stable))[0])[0]
        self.assertEqual([ceiling(), ceiling({"1.1.4": -2}), ceiling({"1.5.2": -1}), ceiling({"1.1.4": 1, "1.5.2": 2})],
                         [2, 0, 1, 2])
        self.assertEqual([ceiling(age=3), ceiling(age=5), ceiling(age=30)], [0, 1, 2])


# -- conditioned phrases in prompts --------------------------------------------------------------------------
class Phrases(unittest.TestCase):
    STABLE = profile(s__traits__development__position={"value": "E5-SENTINEL", "phrase": PHRASE},
                     s__traits__function__domains={"value": {"identity": 3}, "source": "inferred", "basis": "规则"},
                     s__speech_style__baseline={"value": {"directness": 0.3}, "phrase": "你说话慢，先承认对方的处境再提条件。"})

    def test_self_profile(self):
        prof, _ = compile_(self.STABLE)
        view = self_profile(prof, REG)
        self.assertEqual(view[TENDENCIES], PHRASE)
        self.assertEqual(view["表达基线"], "（只有部分自知）你说话慢，先承认对方的处境再提条件。")
        text = json.dumps(view, ensure_ascii=False)
        for absent in ("E5-SENTINEL", "自我发展位置", "人格功能五域", "identity"):
            self.assertNotIn(absent, text)
        self.assertNotIn(TENDENCIES, self_profile(prof, REG, tendencies=False))

    def test_phrases_reach_the_stage_and_expression_prompts(self):
        prof, _ = compile_(self.STABLE)
        repo = ProfileRepository(REG, roles=ROLES, root=tempfile.mkdtemp())
        state = repo.enter(repo.load("role1"), BOOT)
        ctx = I.StageContext(I.Phase("a1", 1, "event"), state, prof, (), {}, {"role1": {"name": "林澄"}, "role2": {"name": "周越"}},
                             ("i1", "role1"))
        stage = ModelStage("S1", REG, MockModel(REG))
        s1 = json.dumps(view_payload(stage.view(ctx, EvidenceGate().build(ctx))), ensure_ascii=False)
        plan = I.ActionPlan((I.ActionCandidate("说明情况", True),), "说明情况", (), (), (), (), I.Commitment(False),
                            I.Expression("说明延期原因", ("role2",), "平静"))
        express = json.dumps(view_payload(ModelExpresser(REG, MockModel(REG)).view(plan, ctx)), ensure_ascii=False)
        for text in (s1, express):
            self.assertIn(PHRASE, text)
            self.assertNotIn("E5-SENTINEL", text)


if __name__ == "__main__":
    unittest.main()
