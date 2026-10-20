import copy
import json
import unittest
from pathlib import Path

from rolesim.catalog import digest
from rolesim.modules import STABLE_MODULES
from rolesim.persona import SCHWARTZ_VALUES, compile_views, conditional_rules, validate_persona
from fixtures import load, setup


ROOT = Path(__file__).resolve().parents[1]
DEMOGRAPHICS = "stable.identity.demographics"




class PersonaV2Tests(unittest.TestCase):
    def setUp(self):
        self.persona = load("role1")

    def bad(self, edit, message=None):
        persona = copy.deepcopy(self.persona)
        edit(persona)
        with self.assertRaisesRegex(ValueError, message or ""):
            validate_persona(persona)

    def test_examples_validate_and_cover_every_registered_module(self):
        for name in ("role1", "role2"):
            persona = load(name)
            validate_persona(persona)
            self.assertEqual(set(persona["stable"]["module_status"]), set(STABLE_MODULES))
        self.assertEqual(len(SCHWARTZ_VALUES), 19)
        self.assertEqual(len(set(SCHWARTZ_VALUES)), 19)

    def test_old_schema_is_refused_without_silent_conversion(self):
        persona = copy.deepcopy(self.persona)
        persona["schema_version"] = "1.0"
        with self.assertRaisesRegex(ValueError, "no silent conversion"):
            validate_persona(persona)

    def test_unknown_must_be_null_and_active_must_have_a_value(self):
        self.bad(lambda p: p["stable"]["identity"].update(demographics={"age": 0}), "unknown module must hold null")
        self.bad(lambda p: p["stable"]["module_status"][DEMOGRAPHICS].update(status="active"), "active module requires")
        self.bad(lambda p: p["stable"]["identity"].update(social_roles=None), "active module requires")
        self.bad(lambda p: p["stable"]["module_status"][DEMOGRAPHICS].update(reason=""), "requires a reason")
        self.bad(lambda p: p["stable"]["module_status"].pop(DEMOGRAPHICS), "exactly the registered")
        self.bad(lambda p: p["stable"]["module_status"].update({"stable.extra": copy.deepcopy(
            p["stable"]["module_status"][DEMOGRAPHICS])}))

    def test_unknown_demographics_can_be_filled_only_by_activating_with_provenance(self):
        persona = copy.deepcopy(self.persona)
        persona["stable"]["identity"]["demographics"] = {"age": 41, "occupation": "研究助理"}
        persona["stable"]["module_status"][DEMOGRAPHICS].update(status="active", reason="档案明写")
        with self.assertRaisesRegex(ValueError, "provenance"):
            validate_persona(persona)
        persona["provenance"][DEMOGRAPHICS] = {"source_ref": "mind.1.1", "assignment": "档案明写", "confidence": 1}
        validate_persona(persona)

    def test_modules_without_adapter_cannot_be_activated_or_filled_with_scores(self):
        path = "stable.traits.social_cognition_71d"
        def activate(p):
            p["stable"]["traits"]["social_cognition_71d"] = {"scores": {"x": 0}}
            p["stable"]["module_status"][path].update(status="active")
            p["provenance"][path] = {"source_ref": "mind.2.4", "assignment": "invented", "confidence": 0}
        self.bad(activate, "no adapter yet")
        self.bad(lambda p: p["stable"]["traits"].update(social_cognition_71d={"status": "pending_source_definition", "scores": {}}),
                 "may not hold a value")

    def test_runtime_required_modules_must_stay_active(self):
        def disable(path):
            def edit(p):
                node = p["stable"]
                *parents, leaf = path.split(".")[1:]
                for part in parents:
                    node = node[part]
                node[leaf] = None                       # a disabled module keeps no value
                p["stable"]["module_status"][path].update(status="disabled")
                p["provenance"].pop(path)
            return edit
        for path in ("stable.traits.personality", "stable.core_goals", "stable.speech_style.baseline"):
            with self.subTest(path=path):
                self.bad(disable(path), "must be active")

    def test_only_p1_is_implemented(self):
        for scheme in ("P2", "P3", "P1+P3"):
            self.bad(lambda p, scheme=scheme: p["stable"]["traits"]["personality"].update(scheme=scheme),
                     "only the P1 encoding")
        self.bad(lambda p: p["stable"]["traits"]["personality"]["facets"].pop("openness.ideas"), "30 canonical")

    def test_schwartz_priorities_rules(self):
        weights = lambda p: p["stable"]["values"]["schwartz_priorities"]
        self.assertAlmostEqual(sum(weights(self.persona)["weights"].values()), 1, places=6)
        self.bad(lambda p: weights(p)["weights"].update(face=.2), "sum to 1")
        self.bad(lambda p: weights(p).update(weights={"politeness": 1}), "nineteen canonical")
        self.bad(lambda p: weights(p).update(weights={"face": 1.5}), "expected finite number")
        self.bad(lambda p: weights(p)["conflict_thresholds"].update(face=.5), "unlisted value")
        self.bad(lambda p: weights(p)["concession_ranges"].update(benevolence_dependability=[.8, .2]), "reversed")
        persona = copy.deepcopy(self.persona)
        weights(persona)["conflict_thresholds"].update(benevolence_dependability=.6)
        weights(persona)["concession_ranges"].update(benevolence_dependability=[.1, .4])
        validate_persona(persona)

    def test_core_goals_rules(self):
        goal = lambda i: {"id": f"g{i}", "text": "t", "priority": .5, "stability": .5, "source": "s", "supports": []}
        self.bad(lambda p: p["stable"].update(core_goals=[]), "one to three")
        self.bad(lambda p: p["stable"].update(core_goals=[goal(i) for i in range(4)]), "one to three")
        self.bad(lambda p: p["stable"]["core_goals"][0].update(supports=[{"id": f"s{i}", "text": "x"} for i in range(3)]),
                 "at most two")
        self.bad(lambda p: p["stable"].update(core_goals=[goal(1), goal(1)]), "duplicate goal id")
        self.bad(lambda p: p["stable"]["core_goals"][0].update(priority=1.2), "expected finite number")
        self.bad(lambda p: p["stable"]["core_goals"][0].update(unexpected=1), "expected fields")
        persona = copy.deepcopy(self.persona)
        persona["stable"]["core_goals"] = [goal(i) for i in range(3)]
        validate_persona(persona)

    def test_provenance_covers_exactly_the_active_modules(self):
        self.bad(lambda p: p["provenance"].pop("stable.core_goals"), "requires provenance")
        self.bad(lambda p: p["provenance"].update({"stable.values.moral_reasoning": {
            "source_ref": "mind.4.2", "assignment": "x", "confidence": 0}}), "only active ones")

    def test_stable_snapshot_id_covers_module_status(self):
        changed = copy.deepcopy(self.persona)
        changed["stable"]["module_status"][DEMOGRAPHICS]["reason"] = "另一个理由"
        self.assertNotEqual(digest(self.persona["stable"]), digest(changed["stable"]))

    def test_views_carry_goals_values_and_situational_rules_to_the_controller_only(self):
        views = compile_views(self.persona)
        controller = views["controller_only"]
        self.assertEqual(controller["core_goals"][0]["id"], "g1")
        self.assertIn("weights", controller["values"])
        sources = [r["source_field"] for r in controller["conditional_rules"]]
        self.assertIn("speech_style.situational_rules", sources)
        self.assertIn("speech_style.baseline.directness", sources)
        blob = json.dumps([views["role_self"], views["other_visible"]], ensure_ascii=False)
        for secret in ("core_goals", "schwartz", "module_status", "weights", "g1a"):
            self.assertNotIn(secret, blob)
        no_rules = copy.deepcopy(self.persona)
        no_rules["stable"]["speech_style"]["situational_rules"] = []
        self.assertNotIn("speech_style.situational_rules", [r["source_field"] for r in conditional_rules(no_rules)])


if __name__ == "__main__":
    unittest.main()
