"""Agent interface v2 (rolesim/agent_api.py): shapes, visibility and the runner helpers."""
import copy
import unittest

from rolesim.agent.agent_api import (ENV_ID, ENV_NAME, InterfaceError, check_id, record_id, split,
                               validate_action_result, validate_bootstrap, validate_environment_content,
                               validate_environment_view, validate_turn_input, visible)

PRESENT = {"c1": {"name": "林澄"}, "c2": {"name": "周越"}, "c3": {"name": "王岚"}}


def bootstrap(**changes):
    data = {"setting": {"place": "会议室", "time_text": "周一上午"}, "trigger_facts": ["项目延期了。"],
            "name": "林澄", "profile_summary": "项目负责人。", "core_goals": "按期交付", "relationships": ["与周越是同事。"],
            "history_memory": None, "personality": {}, "mechanism": {}}
    data.update(changes)
    return data


def turn_input(observations=(), present=None):
    return {"bootstrap": None, "observations": list(observations),
            "present_characters": copy.deepcopy(PRESENT if present is None else present)}


def content(thought=None, speech=None, action=None, order=None):
    parts = {"thought": thought, "speech": speech, "action": action}
    return {**parts, "order": order if order is not None else [k for k, v in parts.items() if v is not None]}


def part(text, ids=None):
    return {"content": text, "recipient_ids": ids}


LIN_TURN = split(content(thought=part("先别急着认。", ["c1"]), speech=part("我们先对一下时间线。"),
                         action=part("把文件推到桌子中间", ["c2"])),
                 "c1", "林澄", PRESENT, "a1", 1, 0)


class SplitAndVisibilityTests(unittest.TestCase):
    def test_split_follows_order_and_recipients(self):
        self.assertEqual([r["kind"] for r in LIN_TURN], ["thought", "speech", "action"])
        self.assertEqual([r["order_index"] for r in LIN_TURN], [0, 1, 2])
        self.assertEqual(set(LIN_TURN[0]["recipients"]), {"c1"})
        self.assertEqual(set(LIN_TURN[1]["recipients"]), set(PRESENT))        # null = everyone present
        self.assertEqual(set(LIN_TURN[2]["recipients"]), {"c2"})
        self.assertEqual(record_id(LIN_TURN[1]), "a1#1")

    def test_each_character_sees_its_own_records_and_what_it_received(self):
        self.assertEqual([r["kind"] for r in visible(LIN_TURN, "c1")], ["thought", "speech", "action"])
        self.assertEqual([r["kind"] for r in visible(LIN_TURN, "c2")], ["speech", "action"])
        self.assertEqual([r["kind"] for r in visible(LIN_TURN, "c3")], ["speech"])

    def test_turn_input_accepts_visible_records_and_must_list_the_actor_as_present(self):
        validate_turn_input(turn_input(visible(LIN_TURN, "c2")), "c2")
        with self.assertRaisesRegex(InterfaceError, "acting character itself"):
            validate_turn_input(turn_input(visible(LIN_TURN, "c2"), {"c1": {"name": "林澄"}}), "c2")

    def test_turn_input_rejects_hidden_records_a_bootstrap_and_an_empty_room(self):
        with self.assertRaisesRegex(InterfaceError, "not visible"):
            validate_turn_input(turn_input(LIN_TURN), "c3")          # c1's thought and directed action
        bad = turn_input()
        bad["bootstrap"] = bootstrap()
        with self.assertRaisesRegex(InterfaceError, "bootstrap"):
            validate_turn_input(bad, "c1")
        with self.assertRaisesRegex(InterfaceError, "nobody else"):
            validate_turn_input(turn_input(present={"c1": {"name": "林澄"}}), "c1")

    def test_records_must_be_ordered_distinct_and_addressed(self):
        with self.assertRaisesRegex(InterfaceError, "occurrence order"):
            validate_turn_input(turn_input(list(reversed(visible(LIN_TURN, "c1")))), "c1")
        with self.assertRaisesRegex(InterfaceError, "duplicate"):
            validate_turn_input(turn_input([LIN_TURN[1], LIN_TURN[1]]), "c1")
        empty = copy.deepcopy(LIN_TURN[1])
        empty["recipients"] = {}
        with self.assertRaisesRegex(InterfaceError, "explicit recipient"):
            validate_turn_input(turn_input([empty]), "c1")


class BootstrapTests(unittest.TestCase):
    def test_valid_bootstrap_and_optional_fields(self):
        validate_bootstrap(bootstrap())
        validate_bootstrap(bootstrap(core_goals=None, personality={"role_ref": "role1"},
                                     mechanism={"mode": "profile_state", "enable_config": "default-v1"}))

    def test_missing_extra_or_malformed_fields_are_rejected(self):
        data = bootstrap()
        del data["core_goals"]
        with self.assertRaisesRegex(InterfaceError, "fields"):
            validate_bootstrap(data)
        with self.assertRaisesRegex(InterfaceError, "fields"):
            validate_bootstrap({**bootstrap(), "cognition": {}})
        with self.assertRaisesRegex(InterfaceError, "role_ref"):
            validate_bootstrap(bootstrap(personality={"role_ref": ""}))
        with self.assertRaisesRegex(InterfaceError, "mechanism.mode"):
            validate_bootstrap(bootstrap(mechanism={"mode": 3}))

    def test_environment_id_is_reserved(self):
        check_id("c1", "character_id")
        with self.assertRaisesRegex(InterfaceError, "reserved"):
            check_id(ENV_ID, "character_id")


class ActionResultTests(unittest.TestCase):
    def test_speech_only_and_thought_then_action_are_valid(self):
        validate_action_result(content(speech=part("好。")), turn_input(), "c1")
        validate_action_result(content(thought=part("想想。", ["c1"]), action=part("起身")), turn_input(), "c1")

    def test_order_must_match_the_filled_parts(self):
        with self.assertRaisesRegex(InterfaceError, "order"):
            validate_action_result(content(speech=part("好。"), order=["speech", "speech"]), turn_input(), "c1")
        with self.assertRaisesRegex(InterfaceError, "order"):
            validate_action_result(content(speech=part("好。"), order=["thought"]), turn_input(), "c1")
        with self.assertRaisesRegex(InterfaceError, "at least one"):
            validate_action_result(content(order=[]), turn_input(), "c1")

    def test_recipients_must_be_present_or_the_actor(self):
        validate_action_result(content(thought=part("想想。", ["c1"])), turn_input(), "c1")
        with self.assertRaisesRegex(InterfaceError, "not present"):
            validate_action_result(content(speech=part("好。", ["c9"])), turn_input(), "c1")
        with self.assertRaisesRegex(InterfaceError, "non-empty"):
            validate_action_result(content(speech=part("好。", [])), turn_input(), "c1")
        with self.assertRaisesRegex(InterfaceError, "fields"):
            validate_action_result({**content(speech=part("好。")), "cognition": {}}, turn_input(), "c1")


class EnvironmentTests(unittest.TestCase):
    VIEW = {"setting": {"place": "会议室", "time_text": None}, "story_background": "项目延期。",
            "trigger_facts": ["项目延期了。"], "present_characters": copy.deepcopy(PRESENT),
            "environment_directive": "会议室的电话响了。"}

    def test_view_has_no_history_and_output_is_addressed_to_present_characters(self):
        validate_environment_view(self.VIEW)
        with self.assertRaisesRegex(InterfaceError, "fields"):
            validate_environment_view({**self.VIEW, "interaction_history": []})
        validate_environment_content(content(action=part("电话响了。", ["c1"])), self.VIEW)
        with self.assertRaisesRegex(InterfaceError, "not present"):
            validate_environment_content(content(action=part("电话响了。", [ENV_ID])), self.VIEW)

    def test_environment_records(self):
        records = split(content(action=part("电话响了。")), ENV_ID, ENV_NAME, PRESENT, "e1", 1, 0)
        self.assertEqual(records[0]["actor"], {"actor_id": "environment", "name": "环境"})
        self.assertEqual(set(records[0]["recipients"]), set(PRESENT))


if __name__ == "__main__":
    unittest.main()
