"""IEvidenceGate (evidence.py), IStage (stages.py) and IExpresser (expresser.py)."""
import copy
import json
import re
import unittest

from rolesim import interfaces as I
from rolesim.state import slots
from rolesim.agent.agent_api import validate_action_content
from rolesim.stage.evidence import EvidenceGate
from rolesim.stage.expresser import ModelExpresser, feeling_text
from rolesim.stage.model import EXPRESS_PROMPT, ChatModel, MockModel, view_payload
from rolesim.role.registry import Registry
from rolesim.stage.schemas import EXPRESS_SCHEMA
from rolesim.stage.stages import ModelStage, model_stages, self_profile, state_text, visible_records
from rolesim.state.store import ConstructStore

REG = Registry.load()
KEY = ("i1", "c1")
PRESENT = {"c1": {"name": "林澄"}, "c2": {"name": "周越"}}
SECRET, HIDDEN_TRAIT, OFFSTAGE = "SECRET-周越心里话", "HIDDEN-71d", "OFFSTAGE-私下通话"


def rec(action_id, order, actor, kind, content, to):
    names = {"c1": "林澄", "c2": "周越", "c3": "王岚", "environment": "环境"}
    return {"action_id": action_id, "turn_index": order, "order_index": order, "actor": {"actor_id": actor, "name": names[actor]},
            "kind": kind, "content": content, "recipients": {r: {"name": names[r]} for r in to}}


MINE = rec("a6", 0, "c1", "speech", "周五前我交初稿。", ["c2"])
MY_THOUGHT = rec("a6", 1, "c1", "thought", "希望他别追问。", ["c1"])
THEIR_THOUGHT = rec("a6", 2, "c2", "thought", SECRET, ["c1"])        # misrouted to us: must still be excluded
ELSEWHERE = rec("a5", 0, "c2", "speech", OFFSTAGE, ["c3"])
THEIRS = rec("a7", 0, "c2", "speech", "我听老王说你周五交不了，为什么不早说？", ["c1"])
HISTORY = (ELSEWHERE, MINE, MY_THOUGHT, THEIR_THOUGHT)


def profile():
    modules = {"s.speech_style.baseline": "说话慢，爱用反问，不直接拒绝",
               "s.core_goals.primary": [{"goal_id": "g1", "goal_definition": "按期交付项目"}],
               "s.traits.personality.p1_big_five": {"neuroticism.anxiety": 1.0},
               "s.traits.social_cognition_71d": {"note": HIDDEN_TRAIT},         # unaware: never in a prompt
               "s.identity.social_roles": {"note": "项目负责人"}}                 # self-visibility not stated
    return I.StableProfile("snap1", "c1", {cid: I.StableModule(cid, v, "active", "explicit", .9) for cid, v in modules.items()})


def entry_state(**dynamic):
    st = slots.blank_state(REG, "c1", "snap1")
    dyn, mem, status = dict(st.dynamic), dict(st.memory), dict(st.status)
    for cid, value in dynamic.items():
        dyn[cid.replace("__", ".")] = value
        status[cid.replace("__", ".")] = "active"
    mem["m.entry"] = {"m1": {"entry_id": "m1", "summary": "上次周越催交付时我答应过周五", "source_events": ["a1#0"], "time": None,
                             "involved": ["c2"], "channel": "亲见", "importance": .8, "valence": -.2, "interpretations": []},
                      "m2": {"entry_id": "m2", "summary": "食堂今天有饺子", "source_events": ["a2#0"], "time": None,
                             "involved": [], "channel": "亲见", "importance": .3, "valence": .1, "interpretations": []}}
    status["m.entry"] = "active"
    return I.AgentState(st.state_id, None, "snap1", dyn, mem, st.versions, status)


def ctx(kind="event", state=None, records=(THEIRS,), history=HISTORY, upstream=None, phase_no=1, present=PRESENT):
    return I.StageContext(I.Phase("a7", phase_no, kind), state or entry_state(), profile(), tuple(records), upstream or {},
                          present, KEY, tuple(history))


class Recorder:
    def __init__(self):
        self.calls = []

    def model_call(self, key, call):
        self.calls.append((key, call))

    def phase(self, key, log):
        pass


class Scripted:
    """An IModel that returns a fixed output (or raises)."""

    def __init__(self, output=None, error=None):
        self.output, self.error, self.views = output, error, []

    def complete(self, stage, view, schema):
        self.views.append(view)
        if self.error:
            raise self.error
        return I.ModelResult(self.output, json.dumps(self.output, ensure_ascii=False))


def mock(stage, context):
    return copy.deepcopy(MockModel(REG).complete(stage, ModelStage(stage, REG, MockModel(REG)).view(
        context, EvidenceGate().build(context)), ModelStage(stage, REG, MockModel(REG)).schema).output)


# -- IEvidenceGate ------------------------------------------------------------------------------------
class Gate(unittest.TestCase):
    def test_visibility_and_channels(self):
        ev = EvidenceGate().build(ctx(records=(THEIRS, MINE)))
        self.assertEqual(ev.included, (I.EvidenceRef("a6#0", "own_action"), I.EvidenceRef("a7#0", "witnessed")))
        self.assertEqual({(x.event, x.reason) for x in ev.excluded},
                         {("a5#0", "not_recipient"), ("a6#1", "own_thought"), ("a6#2", "other_thought")})

    def test_memories_recalled_by_what_was_just_seen(self):
        ev = EvidenceGate(k=1).build(ctx())
        self.assertEqual([m["entry_id"] for m in ev.retrieved_memories], ["m1"])
        self.assertEqual(EvidenceGate().build(ctx(records=())).retrieved_memories, ())


# -- views ------------------------------------------------------------------------------------------
class Views(unittest.TestCase):
    def test_self_profile_keeps_only_what_the_role_can_know(self):
        view = self_profile(profile(), REG)
        self.assertIn("表达基线", view)
        self.assertTrue(view["表达基线"].startswith("（只有部分自知）"))
        self.assertIn("主核心目标", view)
        self.assertNotIn("社会心智能力向量", view)                       # unaware
        self.assertNotIn("社会角色与制度位置", view)                     # not stated -> withheld
        self.assertEqual(set(self_profile(profile(), REG, ("s.speech_style.baseline",))), {"表达基线"})

    def test_state_text(self):
        state = entry_state(d__relation__trust={"c2": {"value": .6}}, d__cognition__shame={"load": .2})
        text = state_text(state, REG, EvidenceGate().build(ctx(state=state)).retrieved_memories)
        self.assertIn('信任 d.relation.trust = {"c2": {"value": 0.6}}', text)
        self.assertIn("羞耻负荷（只有部分自知） d.cognition.shame", text)
        self.assertNotIn("d.affect.stress", text)                         # unknown: nothing to show
        self.assertIn("记得（m1）：上次周越催交付时我答应过周五", text)

    def test_visible_records_window(self):
        c = ctx(records=(THEIRS,))
        records = visible_records(c, EvidenceGate().build(c), window=1)
        self.assertEqual([r["action_id"] + str(r["order_index"]) for r in records], ["a60", "a70"])
        self.assertEqual(len(visible_records(c, EvidenceGate().build(c), window=0)), 1)


# -- IStage -------------------------------------------------------------------------------------------
class Stages(unittest.TestCase):
    def test_three_stages_commit_through_the_store(self):
        stages, store = model_stages(REG, MockModel(REG)), ConstructStore(REG)
        store.open(KEY, entry_state())
        upstream, kinds = {}, {"S1": "event", "S2": "plan", "S3": "feedback"}
        for n, stage in enumerate(("S1", "S2", "S3"), 1):
            if stage == "S3":
                upstream["plan"] = upstream["S2"]
            c = ctx(kinds[stage], store.snapshot(KEY), upstream=dict(upstream), phase_no=n)
            out = stages[stage].run(c)
            upstream[stage] = out.product
            result = store.commit(c, out)
            self.assertEqual((result.rejected, bool(result.accepted)), ((), True), stage)
        self.assertIsInstance(upstream["S3"], I.EvolutionResult)

    def test_phase_and_preconditions(self):
        stages = model_stages(REG, MockModel(REG))
        with self.assertRaises(I.InterfaceError):
            stages["S1"].run(ctx("plan"))
        with self.assertRaisesRegex(I.InterfaceError, "earlier plan"):
            stages["S3"].run(ctx("feedback"))
        with self.assertRaises(ValueError):
            ModelStage("S4", REG, MockModel(REG))

    def test_model_failure_fails_the_stage(self):
        with self.assertRaises(I.StageError):
            ModelStage("S1", REG, Scripted(error=I.ModelError("timeout"))).run(ctx())

    def test_unparsable_replies_are_traced_too(self):
        trace = Recorder()
        with self.assertRaises(I.StageError):
            ModelStage("S1", REG, Scripted(error=I.ModelError("bad json", raw="{} {}", reasoning="r")), trace=trace).run(ctx())
        (key, call), = trace.calls
        self.assertEqual((call.stage, call.raw_output, call.reasoning), ("S1", "{} {}", "r"))
        with self.assertRaises(I.StageError):
            ModelStage("S1", REG, Scripted(error=I.ModelError("timeout")), trace=trace).run(ctx())
        self.assertEqual(len(trace.calls), 1)                                  # nothing came back: nothing to keep

    def test_calls_are_traced_even_when_the_output_is_rejected(self):
        trace = Recorder()
        with self.assertRaises(I.StageError):
            ModelStage("S1", REG, Scripted({"product": {}}), trace=trace).run(ctx())
        (key, call), = trace.calls
        self.assertEqual((key, call.stage, json.loads(call.raw_output)), (KEY, "S1", {"product": {}}))

    def test_audience_must_be_present(self):
        c = ctx("plan")
        output = mock("S2", c)
        output["product"]["expression"]["audience"] = ["c9"]
        with self.assertRaisesRegex(I.StageError, "audience"):
            ModelStage("S2", REG, Scripted(output)).run(c)

    def test_private_needs_an_audience_and_is_required(self):
        c = ctx("plan")
        output = mock("S2", c)
        output["product"]["expression"].update(audience=[], private=True)
        with self.assertRaisesRegex(I.StageError, "private"):
            ModelStage("S2", REG, Scripted(output)).run(c)
        output = mock("S2", c)
        del output["product"]["expression"]["private"]
        with self.assertRaises(I.StageError):
            ModelStage("S2", REG, Scripted(output)).run(c)

    def test_nothing_hidden_reaches_any_stage_prompt(self):
        model = Scripted(error=I.ModelError("stop"))
        for stage, kind in (("S1", "event"), ("S2", "plan"), ("S3", "feedback")):
            with self.assertRaises(I.StageError):
                ModelStage(stage, REG, model).run(ctx(kind, upstream={"plan": plan()}))
        self.assertEqual(len(model.views), 3)
        for view in model.views:
            text = json.dumps(view_payload(view), ensure_ascii=False)
            for sentinel in (SECRET, OFFSTAGE, HIDDEN_TRAIT):
                self.assertNotIn(sentinel, text)
            self.assertIn("我听老王说你周五交不了", text)


# -- IExpresser -----------------------------------------------------------------------------------------
def plan(audience=("c2",), private=False):
    return I.ActionPlan((I.ActionCandidate("说明情况并提出方案", True), I.ActionCandidate("推给老王", False, "CONSTRAINT-会被拆穿")),
                        "说明情况并提出方案", ("请求时间核对",), (I.SwitchCondition("对方连续追问", "请求时间核对"),),
                        ("HYPOTHESIS-他在试探我",), ("对方愿意等",), I.Commitment(True, "周一前给出新日期"),
                        I.Expression("说明延期原因", tuple(audience), "平静", private))


class Expresser(unittest.TestCase):
    STATE = dict(d__affect__emotion={"label": ["恐惧"], "valence": -.3, "arousal": .5, "intensity": .5137, "confidence": .7},
                 d__relation__trust={"c2": {"value": .6137}})

    def test_realize(self):
        content = ModelExpresser(REG, MockModel(REG)).realize(plan(), ctx("plan", entry_state(**self.STATE)))
        validate_action_content(content, PRESENT, "c1")
        self.assertEqual(content["thought"]["recipient_ids"], ["c1"])
        # Addressed to c2, but said in front of everyone present: everyone hears it.
        self.assertIsNone(content["speech"]["recipient_ids"])
        self.assertEqual(content["order"], ["thought", "speech"])
        to_all = ModelExpresser(REG, MockModel(REG)).realize(plan(audience=()), ctx("plan"))
        self.assertIsNone(to_all["speech"]["recipient_ids"])

    def test_private_expression_reaches_only_its_audience(self):
        content = ModelExpresser(REG, MockModel(REG)).realize(plan(private=True), ctx("plan"))
        self.assertEqual(content["speech"]["recipient_ids"], ["c2"])
        self.assertEqual(content["thought"]["recipient_ids"], ["c1"])
        with self.assertRaisesRegex(I.StageError, "private"):
            ModelExpresser(REG, MockModel(REG)).realize(plan(audience=(), private=True), ctx("plan"))

    def test_need_to_know_view(self):
        model = Scripted({"thought": None, "speech": "我先说明一下。", "action": None, "order": ["speech"]})
        ModelExpresser(REG, model).realize(plan(), ctx("plan", entry_state(**self.STATE)))
        text = json.dumps(view_payload(model.views[0]), ensure_ascii=False)
        for absent in ("0.6137", "0.5137", "CONSTRAINT-", "HYPOTHESIS-", SECRET, OFFSTAGE, HIDDEN_TRAIT, "dm."):
            self.assertNotIn(absent, text)
        self.assertIsNone(re.search(r"\b[dsm]\.[a-z_]+\.[a-z_]+", text))
        self.assertIn("说话慢，爱用反问", text)
        self.assertIn("此刻的感受：恐惧（强度中）", text)
        self.assertIn("周一前给出新日期", text)

    def test_bad_expressions_send_nothing(self):
        for output in ({"thought": None, "speech": None, "action": None, "order": []},
                       {"thought": None, "speech": "好。", "action": None, "order": ["speech", "action"]},
                       {"thought": None, "speech": "好。", "action": None}):
            with self.assertRaises(I.StageError):
                ModelExpresser(REG, Scripted(output)).realize(plan(), ctx("plan"))
        with self.assertRaises(I.StageError):
            ModelExpresser(REG, Scripted(error=I.ModelError("down"))).realize(plan(), ctx("plan"))

    def test_feeling_text(self):
        self.assertEqual(feeling_text(entry_state(**self.STATE)), "此刻的感受：恐惧（强度中）")
        self.assertEqual(feeling_text(entry_state()), "")

    def test_chat_model_uses_the_expression_prompt(self):
        class Transport:
            model, last_reasoning, sent = "fake", None, []

            def chat(self, system, payload, schema=None):
                self.sent.append(system)
                return {"thought": None, "speech": "好。", "action": None, "order": ["speech"]}, "{}"

        transport = Transport()
        ModelExpresser(REG, ChatModel(transport)).realize(plan(), ctx("plan"))
        self.assertEqual(transport.sent, [EXPRESS_PROMPT])
        self.assertEqual(EXPRESS_SCHEMA["required"], ["thought", "speech", "action", "order"])


if __name__ == "__main__":
    unittest.main()
