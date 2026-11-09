"""Key interfaces (rolesim/interfaces.py, plan/10-interfaces.md): shapes, invariants, call chain."""
import dataclasses
import inspect
import unittest

from rolesim import interfaces as I
from rolesim.agent import staged
from rolesim.run.loop import EchoEnvironment
from rolesim.agent.runner import TurnRunner
from rolesim.agent.staged import StagedRoleAgent

EV = (I.EvidenceRef("r1", "witnessed"),)


def module(cid, value=.5, kind="explicit", text=None):
    return I.StableModule(cid, value, "active", kind, .8, source_text=text)


def profile():
    return I.StableProfile("snap1", "c1", {"s.trait.agreeableness": module("s.trait.agreeableness")})


def state(sid="st0", prev=None, dynamic=None):
    return I.AgentState(sid, prev, "snap1", dynamic or {"d.affect.emotion": 0.0}, {})


def ctx(kind="event", st=None, upstream=None):
    return I.StageContext(I.Phase("a1", 1, kind), st or state(), profile(), (), upstream or {}, {}, ("i1", "c1"))


def change(**kw):
    data = dict(construct_id="d.affect.emotion", key=None, field=None, before=0.0, after=.2, evidence=EV,
                update_operator="emotion_update", decision_mechanisms=("dm.appraisal",), stage="S1",
                band=I.Band("small", .05, .25), group="affect", reason="被当众批评")
    data.update(kw)
    return I.StateChange(**data)


def interpretation(n=1):
    return I.SituationInterpretation(
        "被当众批评", (I.Appraisal("按期交付", "threat"),), {"cause": "c2"},
        tuple(I.Hypothesis(f"h{i}", .5, ("r1",), (), "看他会后是否私下解释") for i in range(n)),
        "multiple" if n > 1 else "settled", (), {"c2": "冷淡"})


def plan(chosen="解释原因"):
    return I.ActionPlan((I.ActionCandidate("解释原因", True), I.ActionCandidate("反击", False, "s.trait.agreeableness")),
                        chosen, ("沉默",), (I.SwitchCondition("对方继续指责", "沉默"),), ("h0",), ("对方缓和",),
                        I.Commitment(True, "明天补交"), I.Expression("说明延期原因", ("c2",), "平静"))


class Vocabulary(unittest.TestCase):
    def test_eleven_operators(self):
        self.assertEqual(len(I.OPERATOR_NAMES), 11)

    def test_exports_resolve(self):
        for name in I.__all__:
            self.assertTrue(hasattr(I, name), name)

    def test_one_agent_call_error(self):
        self.assertIs(staged.AgentCallError, I.AgentCallError)


class Hierarchy(unittest.TestCase):
    """Each top-level interface and its sub-interfaces exist with the planned methods."""
    TREE = {
        "IRoleAgent": ["initialize", "act"], "ITurnRunner": ["begin", "run"], "ITrace": ["phase", "model_call"],
        "IRoleRepository": ["load", "enter"], "IProfileCompiler": ["compile"],
        "IRegistry": ["construct", "mechanism", "operator"],
        "IStage": ["run"], "IEvidenceGate": ["build"], "IMechanism": ["evaluate"], "IModel": ["complete"],
        "IExpresser": ["realize"],
        "IStateStore": ["open", "snapshot", "commit"], "IUpdateOperator": ["propose"], "IMagnitude": ["range"],
        "IValidator": ["check"],
    }

    def test_methods(self):
        for name, methods in self.TREE.items():
            proto = getattr(I, name)
            for m in methods:
                self.assertTrue(callable(getattr(proto, m, None)), f"{name}.{m}")

    def test_role_agent_is_async_and_adapter_conforms(self):
        for m in ("initialize", "act"):
            self.assertTrue(inspect.iscoroutinefunction(getattr(I.IRoleAgent, m)))
            self.assertTrue(inspect.iscoroutinefunction(getattr(StagedRoleAgent, m)))
            self.assertEqual(list(inspect.signature(getattr(I.IRoleAgent, m)).parameters),
                             list(inspect.signature(getattr(StagedRoleAgent, m)).parameters))

    def test_turn_runner_conforms(self):
        for m in ("begin", "run"):
            self.assertEqual(list(inspect.signature(getattr(I.ITurnRunner, m)).parameters),
                             list(inspect.signature(getattr(TurnRunner, m)).parameters))

    def test_local_environment_conforms(self):
        self.assertTrue(inspect.iscoroutinefunction(EchoEnvironment.act))
        self.assertEqual(list(inspect.signature(I.IEnvironment.act).parameters),
                         list(inspect.signature(EchoEnvironment.act).parameters))


class Invariants(unittest.TestCase):
    def test_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state().state_id = "x"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan().chosen = "反击"

    def test_change_needs_evidence_operator_and_dynamic_target(self):
        change()
        with self.assertRaises(I.InterfaceError):
            change(evidence=())
        with self.assertRaises(I.InterfaceError):
            change(update_operator="free_write")
        with self.assertRaises(I.InterfaceError):
            change(construct_id="s.trait.agreeableness")

    def test_band_rejects_rather_than_clips(self):
        band = I.Band("small", .05, .25)
        self.assertTrue(band.contains(-.2))
        self.assertFalse(band.contains(.4))
        with self.assertRaises(I.InterfaceError):
            I.Band("huge", 0, 1)
        with self.assertRaises(I.InterfaceError):
            I.Band("small", .3, .1)

    def test_stable_module_sources(self):
        with self.assertRaises(I.InterfaceError):
            module("s.x", kind="model_inferred")
        module("s.x", kind="model_inferred", text="她从不当面拒绝别人")
        with self.assertRaises(I.InterfaceError):
            module("s.x", value=.3, kind="unknown")
        module("s.x", value=None, kind="unknown")

    def test_products(self):
        interpretation(3)
        with self.assertRaises(I.InterfaceError):
            interpretation(0)
        with self.assertRaises(I.InterfaceError):
            interpretation(4)
        with self.assertRaises(I.InterfaceError):
            plan(chosen="反击")          # dropped candidate
        with self.assertRaises(I.InterfaceError):
            I.EvidenceRef("r1", "rumour")

    def test_enable_config(self):
        config = I.EnableConfig(mechanisms={"dm.a": False}, modules={"s.trait": True})
        self.assertFalse(config.enabled("dm.a"))
        self.assertTrue(config.enabled("s.trait"))
        self.assertTrue(config.enabled("dm.other"))
        self.assertFalse(I.EnableConfig(default=False).enabled("dm.other"))


# -- toy implementations: the protocols compose along the act call chain -----
class Gate:
    def build(self, ctx):
        return I.EvidenceSet(EV, (I.Excluded("r2", "other_thought"),))


class S1:
    name = "S1"

    def run(self, ctx):
        Gate().build(ctx)
        return I.StageOutput(interpretation(), (I.ChangeProposal("d.affect.emotion", None, None, .2, "批评"),))


class S2:
    name = "S2"

    def run(self, ctx):
        assert "S1" in ctx.upstream
        return I.StageOutput(plan())


class Store:
    def __init__(self):
        self.states = {("i1", "c1"): state()}
        self.n = 0

    def open(self, key, state):
        self.states[key] = state

    def snapshot(self, key):
        return self.states[key]

    def commit(self, ctx, output):
        changes = tuple(change(before=ctx.state.dynamic[p.construct_id], after=p.after) for p in output.proposals)
        self.n += 1
        dynamic = {**ctx.state.dynamic, **{c.construct_id: c.after for c in changes}}
        new = state(f"st{self.n}", ctx.state.state_id, dynamic)
        self.states[ctx.key] = new
        return I.CommitResult(new.state_id, changes)


class Expresser:
    def realize(self, plan, ctx):
        return {"thought": None, "speech": {"content": plan.expression.goal, "recipient_ids": list(plan.expression.audience)},
                "action": None, "order": ["speech"]}


class Runner:
    def __init__(self, store):
        self.store = store

    def run(self, key, new_records, present):
        upstream = {}
        st = self.store.snapshot(key)
        c = I.StageContext(I.Phase("a1", 1, "event"), st, profile(), (), {}, present, key)
        out = S1().run(c)
        self.store.commit(c, out)
        upstream["S1"] = out.product
        c = I.StageContext(I.Phase("plan:q1", 2, "plan"), self.store.snapshot(key), profile(), (), upstream, present, key)
        out2 = S2().run(c)
        self.store.commit(c, out2)
        return Expresser().realize(out2.product, c), self.store.snapshot(key)


class CallChain(unittest.TestCase):
    def test_runtime_checkable_stage(self):
        self.assertIsInstance(S1(), I.IStage)

    def test_one_act(self):
        store = Store()
        content, final = Runner(store).run(("i1", "c1"), [], {"c1": {"name": "林澄"}, "c2": {"name": "周越"}})
        self.assertEqual(content["speech"]["recipient_ids"], ["c2"])
        self.assertEqual(final.dynamic["d.affect.emotion"], .2)
        self.assertEqual(final.prev_state_id, "st1")     # S2 read S1's committed snapshot
        self.assertEqual(final.stable_snapshot_id, "snap1")


if __name__ == "__main__":
    unittest.main()
