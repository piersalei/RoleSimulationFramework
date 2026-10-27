"""Agent interface v1 (rolesim/agent_api.py): shapes, visibility and the runner helpers."""
import copy
import unittest

from rolesim.agent_api import (ENV_ID, ENV_NAME, Directory, InterfaceError, deliveries, environment_history,
                               record_id, split, validate_action_result, validate_character_view,
                               validate_environment_content, validate_environment_view,
                               validate_perception_input, validate_perception_result)

PRESENT_FOR_LIN = {"c2": {"name": "周越"}, "c3": {"name": "王岚"}}
COGNITION = {"knowledge": "材料还没核对。", "beliefs": None, "emotions": "有些紧张", "motivations": None,
             "goals": "先弄清事实"}


def view(name="林澄", present=None, observations=()):
    return {"setting": {"place": "会议室", "time_text": "周一上午"}, "trigger_facts": ["项目延期了。"],
            "name": name, "profile_summary": "项目负责人。", "relationships": ["与周越是同事。"],
            "history_memory": None, "personality": {}, "mechanism": {}, "cognition": dict(COGNITION),
            "observations": list(observations),
            "present_characters": copy.deepcopy(PRESENT_FOR_LIN if present is None else present)}


def content(thought=None, speech=None, action=None, order=None):
    parts = {"thought": thought, "speech": speech, "action": action}
    return {**parts, "order": order if order is not None else [k for k, v in parts.items() if v is not None]}


def part(text, ids=None):
    return {"content": text, "recipient_ids": ids}


LIN_TURN = split(content(thought=part("先别急着认。"), speech=part("我们先对一下时间线。"),
                         action=part("把文件推到桌子中间", ["c2"])),
                 "c1", "林澄", PRESENT_FOR_LIN, "a1", 1, 0)


class SplitAndDeliverTests(unittest.TestCase):
    def test_split_follows_order_and_recipients(self):
        self.assertEqual([r["kind"] for r in LIN_TURN], ["thought", "speech", "action"])
        self.assertEqual([r["order_index"] for r in LIN_TURN], [0, 1, 2])
        self.assertEqual(LIN_TURN[0]["recipients"], {"c1": {"name": "林澄"}})
        self.assertEqual(set(LIN_TURN[1]["recipients"]), {"c2", "c3"})     # null -> everyone present
        self.assertEqual(set(LIN_TURN[2]["recipients"]), {"c2"})
        self.assertEqual(record_id(LIN_TURN[2]), "a1#2")

    def test_only_non_actors_who_see_something_perceive_and_never_thoughts(self):
        out = deliveries(LIN_TURN, "林澄", ["林澄", "周越", "王岚"])
        self.assertEqual(set(out), {"周越", "王岚"})
        self.assertEqual([r["kind"] for r in out["周越"]], ["speech", "action"])
        self.assertEqual([r["kind"] for r in out["王岚"]], ["speech"])
        only_thought = split(content(thought=part("算了。")), "c1", "林澄", PRESENT_FOR_LIN, "a2", 2, 0)
        self.assertEqual(deliveries(only_thought, "林澄", ["林澄", "周越"]), {})

    def test_environment_history_drops_thoughts(self):
        self.assertEqual([r["kind"] for r in environment_history(LIN_TURN)], ["speech", "action"])


class CharacterViewTests(unittest.TestCase):
    def test_actor_sees_own_thought_and_recipients_see_the_rest(self):
        validate_character_view(view(observations=LIN_TURN))
        zhou = view("周越", {"c1": {"name": "林澄"}, "c3": {"name": "王岚"}}, LIN_TURN[1:])
        validate_character_view(zhou)

    def test_another_roles_thought_or_hidden_record_is_rejected(self):
        zhou_present = {"c1": {"name": "林澄"}, "c3": {"name": "王岚"}}
        with self.assertRaisesRegex(InterfaceError, "not visible"):
            validate_character_view(view("周越", zhou_present, LIN_TURN))
        with self.assertRaisesRegex(InterfaceError, "not visible"):
            validate_character_view(view("王岚", {"c1": {"name": "林澄"}, "c2": {"name": "周越"}}, LIN_TURN[1:]))

    def test_self_must_not_be_present_and_names_are_unique(self):
        with self.assertRaisesRegex(InterfaceError, "itself"):
            validate_character_view(view(present={"c1": {"name": "林澄"}}))
        with self.assertRaisesRegex(InterfaceError, "unique"):
            validate_character_view(view(present={"c2": {"name": "周越"}, "c3": {"name": "周越"}}))

    def test_records_must_be_ordered_and_distinct(self):
        with self.assertRaisesRegex(InterfaceError, "occurrence order"):
            validate_character_view(view(observations=list(reversed(LIN_TURN))))
        with self.assertRaisesRegex(InterfaceError, "duplicate"):
            validate_character_view(view(observations=[LIN_TURN[1], LIN_TURN[1]]))

    def test_cognition_needs_all_five_fields(self):
        broken = view()
        del broken["cognition"]["goals"]
        with self.assertRaisesRegex(InterfaceError, "cognition"):
            validate_character_view(broken)


class PerceptionTests(unittest.TestCase):
    ZHOU = {"c1": {"name": "林澄"}, "c3": {"name": "王岚"}}

    def test_valid_perception_input(self):
        validate_perception_input({"character_view": view("周越", self.ZHOU), "new_observations": LIN_TURN[1:]})
        validate_perception_result({"cognition": dict(COGNITION)})

    def test_own_records_repeats_and_empty_batches_are_rejected(self):
        lin = view(observations=[])
        with self.assertRaisesRegex(InterfaceError, "own records"):
            validate_perception_input({"character_view": lin, "new_observations": LIN_TURN[1:]})
        with self.assertRaisesRegex(InterfaceError, "already"):
            validate_perception_input({"character_view": view("周越", self.ZHOU, LIN_TURN[1:]),
                                       "new_observations": LIN_TURN[1:]})
        with self.assertRaisesRegex(InterfaceError, "something new"):
            validate_perception_input({"character_view": view("周越", self.ZHOU), "new_observations": []})

    def test_perception_result_has_cognition_only(self):
        with self.assertRaises(InterfaceError):
            validate_perception_result({"cognition": dict(COGNITION), "action": None})


class ActResultTests(unittest.TestCase):
    def result(self, action):
        return {"cognition": dict(COGNITION), "action": action}

    def test_speech_only_and_thought_then_action_are_valid(self):
        validate_action_result(self.result(content(speech=part("好。"))), view())
        validate_action_result(self.result(content(thought=part("再等等。"), action=part("起身", ["c3"]))), view())

    def test_order_must_match_the_filled_parts(self):
        for bad in (content(), content(speech=part("好。"), order=["speech", "speech"]),
                    content(speech=part("好。"), order=["speech", "action"]),
                    content(thought=part("嗯"), speech=part("好。"), order=["speech"])):
            with self.assertRaises(InterfaceError):
                validate_action_result(self.result(bad), view())

    def test_recipients_must_be_present_and_thoughts_undirected(self):
        with self.assertRaisesRegex(InterfaceError, "not present"):
            validate_action_result(self.result(content(speech=part("好。", ["c9"]))), view())
        with self.assertRaisesRegex(InterfaceError, "stays with its actor"):
            validate_action_result(self.result(content(thought=part("嗯", ["c2"]))), view())
        with self.assertRaisesRegex(InterfaceError, "non-empty"):
            validate_action_result(self.result(content(speech=part("好。", []))), view())


class EnvironmentTests(unittest.TestCase):
    PRESENT = {"c1": {"name": "林澄"}, "c2": {"name": "周越"}, "c3": {"name": "王岚"}}

    def env_view(self, history):
        return {"setting": {"place": "会议室", "time_text": None}, "story_background": "项目复盘会。",
                "trigger_facts": [], "present_characters": dict(self.PRESENT), "interaction_history": history,
                "environment_directive": "会议室电话响起，财务找林澄。"}

    def test_history_without_thoughts_is_valid_and_with_thoughts_is_rejected(self):
        validate_environment_view(self.env_view(environment_history(LIN_TURN)))
        with self.assertRaisesRegex(InterfaceError, "never shown"):
            validate_environment_view(self.env_view(LIN_TURN))

    def test_environment_output_and_records(self):
        env_view = self.env_view([])
        out = content(action=part("会议室的电话响了。"), speech=part("林澄在吗？财务找。", ["c1"]),
                      order=["action", "speech"])
        validate_environment_content(out, env_view)
        with self.assertRaisesRegex(InterfaceError, "no thought"):
            validate_environment_content(content(thought=part("x"), action=part("门开了。")), env_view)
        records = split(out, ENV_ID, ENV_NAME, self.PRESENT, "env1", 2, 0)
        self.assertEqual(set(deliveries(records, ENV_NAME, ["林澄", "周越", "王岚"])), {"林澄", "周越", "王岚"})
        self.assertEqual([r["kind"] for r in deliveries(records, ENV_NAME, ["周越"])["周越"]], ["action"])


class DirectoryTests(unittest.TestCase):
    def test_names_route_to_our_role_ids(self):
        directory = Directory.from_personas([{"id": "role1", "name": "林澄"}, {"id": "role2", "name": "周越"},
                                             {"id": "role3", "name": "王岚"}])
        self.assertEqual(directory.role("林澄"), "role1")
        self.assertEqual(directory.present(PRESENT_FOR_LIN), {"c2": "role2", "c3": "role3"})
        self.assertEqual(directory.actor(LIN_TURN[0]), "role1")
        self.assertEqual(directory.actor({"actor": {"actor_id": ENV_ID, "name": ENV_NAME}}), ENV_ID)
        with self.assertRaisesRegex(InterfaceError, "no role"):
            directory.role("陌生人")


if __name__ == "__main__":
    unittest.main()
