"""What step 7 moved into the staged pipeline: the opening setup, persona skills, ablation modes, routing."""
import asyncio
import json
import unittest

from rolesim import interfaces as I
from rolesim.stage.model import MockModel, view_payload
from rolesim.role.registry import Registry
from rolesim.role.repository import SETUP_GOAL, ProfileRepository
from rolesim.role.roles import load_role
from rolesim.agent.staged import StagedRoleAgent

REG = Registry.load()
ROLES = [load_role(r) for r in ("role1", "role2", "role3")]
PRESENT = {"role1": {"name": "林澄"}, "role2": {"name": "周越"}, "role3": {"name": "王岚"}}
SETUP = ROLES[0]["setup"]
CONCEALED = [i["text"] for i in SETUP["conceal"]]
DISCLOSED = [i["text"] for i in SETUP["disclose"]]


def boot(name="林澄", **mechanism):
    return {"setting": {"place": "会议室", "time_text": None}, "trigger_facts": ["项目延期了。"], "name": name,
            "profile_summary": "x", "core_goals": None, "relationships": [], "history_memory": None,
            "personality": {}, "mechanism": mechanism}


ZHOU = {"action_id": "t002-role2", "turn_index": 2, "order_index": 0, "actor": {"actor_id": "role2", "name": "周越"},
        "kind": "speech", "content": "今天必须给我全部数据，为什么还没有？能不能说个时间？",
        "recipients": {"role1": PRESENT["role1"], "role3": PRESENT["role3"]}}


class Recorder(MockModel):
    def __init__(self, registry):
        super().__init__(registry)
        self.views = []

    def complete(self, stage, view, schema):
        self.views.append((stage, view, schema))
        return super().complete(stage, view, schema)

    def of(self, stage):
        return [(v, s) for st, v, s in self.views if st == stage]


def act(agent, observations, request_id, character="role1", interaction="i1"):
    context = {"bootstrap": None, "observations": observations, "present_characters": PRESENT}
    return asyncio.run(agent.act(interaction, character, context, request_id))


def text(view):
    return json.dumps(view_payload(view), ensure_ascii=False)


class Briefing(unittest.TestCase):
    def test_setup_seeds_goal_and_recap(self):
        repo = ProfileRepository(REG, roles=ROLES)
        state = repo.enter(repo.load("role1"), boot())
        self.assertEqual(state.dynamic["d.goal.goals_now"][SETUP_GOAL]["content"], SETUP["goal"])
        self.assertTrue({i["id"] for i in SETUP["recap"]} <= set(state.memory["m.entry"]))

    def test_concealed_reaches_planning_but_never_expression(self):
        model = Recorder(REG)
        agent = StagedRoleAgent(ROLES, REG, model)
        asyncio.run(agent.initialize("i1", "role1", boot()))
        act(agent, [ZHOU], "r1")
        for stage in ("S1", "S2"):
            planning = text(model.of(stage)[0][0])
            self.assertTrue(all(t in planning for t in CONCEALED), stage)
        expression = text(model.of("express")[0][0])
        self.assertFalse(any(t in expression for t in CONCEALED))
        self.assertTrue(all(t in expression for t in DISCLOSED))


class Skills(unittest.TestCase):
    def test_skills_reach_planning_and_their_style_reaches_expression(self):
        model = Recorder(REG)
        agent = StagedRoleAgent(ROLES, REG, model)
        asyncio.run(agent.initialize("i1", "role1", boot()))
        act(agent, [ZHOU], "r1")
        s1, s2, express = model.of("S1")[0][0], model.of("S2")[0][0], model.of("express")[0][0]
        self.assertNotIn("skills", s1.task)
        skills = s2.task["skills"]
        self.assertEqual(len(skills["catalog"]), len(ROLES[0]["file"]["skills"]))
        self.assertTrue(1 <= len(skills["active"]) <= 3)
        self.assertEqual(len(express.task["skill_style"]), len(skills["active"]))
        self.assertTrue(all("decision" in a and "avoid" in a for a in skills["active"]))

    def test_no_skills_keeps_them_out_of_every_prompt(self):
        model = Recorder(REG)
        agent = StagedRoleAgent(ROLES, REG, model, use_skills=False)
        asyncio.run(agent.initialize("i1", "role1", boot()))
        act(agent, [ZHOU], "r1")
        self.assertTrue(all("skills" not in v.task and "skill_style" not in v.task for _, v, _ in model.views))


class Modes(unittest.TestCase):
    def views(self, mode):
        model = Recorder(REG)
        agent = StagedRoleAgent(ROLES, REG, model)
        asyncio.run(agent.initialize("i1", "role1", boot(mode=mode)))
        act(agent, [ZHOU], "r1")
        return model

    def test_each_mode_shapes_the_prompts(self):
        only, state, full = (self.views(m) for m in I.MODES)
        for model, has_state, has_mechanisms in ((only, False, False), (state, True, False), (full, True, True)):
            view, schema = model.of("S2")[0]
            self.assertEqual("d.affect.emotion" in view.state_text, has_state)
            self.assertEqual(bool(view.task["mechanisms"]), has_mechanisms)
            self.assertEqual(schema["properties"]["judgments"].get("maxItems") == 0, not has_mechanisms)
            self.assertEqual(bool(model.of("express")[0][0].state_text), has_state)

    def test_mode_per_character_and_unknown_modes(self):
        agent = StagedRoleAgent(ROLES, REG, MockModel(REG), mode="profile_state")
        asyncio.run(agent.initialize("i1", "role1", boot(mode="profile_only")))
        asyncio.run(agent.initialize("i1", "role2", boot("周越")))
        self.assertEqual((agent.sessions[("i1", "role1")].mode, agent.sessions[("i1", "role2")].mode),
                         ("profile_only", "profile_state"))
        with self.assertRaises(ValueError):
            StagedRoleAgent(ROLES, REG, MockModel(REG), mode="cards_only")


class Routing(unittest.TestCase):
    def test_initialize_routes_by_role_ref_id_or_name_and_once_per_role(self):
        agent = StagedRoleAgent(ROLES, REG, MockModel(REG))
        asyncio.run(agent.initialize("i1", "c9", {**boot("别名"), "personality": {"role_ref": "role2"}}))
        asyncio.run(agent.initialize("i1", "role1", boot()))
        asyncio.run(agent.initialize("i1", "x3", boot("王岚")))
        self.assertEqual([agent.sessions[("i1", c)].role_id for c in ("c9", "role1", "x3")], ["role2", "role1", "role3"])
        asyncio.run(agent.initialize("i1", "role1", boot()))                      # a retried initialize is harmless
        for bootstrap, character in ((boot("林澄", mode="profile_only"), "role1"), ({**boot("别名"), "personality": {"role_ref": "role2"}}, "c10")):
            with self.assertRaises(I.InterfaceError):
                asyncio.run(agent.initialize("i1", character, bootstrap))

    def test_act_needs_an_initialized_character(self):
        with self.assertRaises(I.InterfaceError):
            act(StagedRoleAgent(ROLES, REG, MockModel(REG)), [ZHOU], "r1")

    def test_interactions_are_separate_sessions(self):
        agent = StagedRoleAgent(ROLES, REG, MockModel(REG))
        for interaction in ("i1", "i2"):
            asyncio.run(agent.initialize(interaction, "role1", boot()))
        act(agent, [ZHOU], "r1", interaction="i1")
        self.assertFalse(agent.runner.started(("i2", "role1")))              # a session starts at its first act
        act(agent, [], "r2", interaction="i2")
        one, two = agent.store.snapshot(("i1", "role1")), agent.store.snapshot(("i2", "role1"))
        belief = "b-t002-role2-1"                                            # what i1 took in from 周越
        self.assertIn(belief, one.dynamic["d.cognition.belief"])
        self.assertNotIn(belief, two.dynamic["d.cognition.belief"])


if __name__ == "__main__":
    unittest.main()
