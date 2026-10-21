"""Versioned authoring contract (persona schema 2.0); stable fields never change during an episode.

Structure follows the psychology group's reply: `identity`, `traits` (eight sub-objects),
`values` (Schwartz priorities), `core_goals`, `speech_style`, and a status entry for every
stable module. A module that is not modelled says so; a missing measurement is `unknown`
(null), never a neutral number.
"""
import copy
import math

from .modules import STABLE_MODULES, check_consistency

SCHEMA_VERSION = "2.0"

FACETS = {
    "openness": ["fantasy", "aesthetics", "feelings", "actions", "ideas", "values"],
    "conscientiousness": ["competence", "order", "dutifulness", "achievement_striving", "self_discipline", "deliberation"],
    "extraversion": ["warmth", "gregariousness", "assertiveness", "activity", "excitement_seeking", "positive_emotions"],
    "agreeableness": ["trust", "straightforwardness", "altruism", "compliance", "modesty", "tender_mindedness"],
    "neuroticism": ["anxiety", "angry_hostility", "depression", "self_consciousness", "impulsiveness", "vulnerability"],
}
FACET_KEYS = {f"{domain}.{facet}" for domain, facets in FACETS.items() for facet in facets}

# The nineteen refined Schwartz values (mind library 4.1.2-4.1.5).
SCHWARTZ_VALUES = (
    "self_direction_thought", "self_direction_action", "stimulation", "hedonism", "achievement",
    "power_dominance", "power_resources", "face", "security_personal", "security_societal", "tradition",
    "conformity_rules", "conformity_interpersonal", "humility", "benevolence_dependability",
    "benevolence_caring", "universalism_concern", "universalism_nature", "universalism_tolerance")

STABLE_KEYS = {"identity", "traits", "values", "core_goals", "speech_style", "mechanism_priors", "module_status"}
TRAIT_KEYS = {"personality", "attachment", "motivation_baseline", "affective_capacities", "social_cognition_71d",
              "cognitive_style", "developmental_position", "personality_function"}
# Modules the runtime reads; they must be active.
REQUIRED_ACTIVE = {"stable.traits.personality", "stable.traits.attachment", "stable.traits.motivation_baseline",
                   "stable.traits.affective_capacities", "stable.traits.cognitive_style",
                   "stable.values.schwartz_priorities", "stable.core_goals", "stable.speech_style.baseline",
                   "stable.mechanism_priors"}
# Modules with no adapter yet: they may be unknown / not_modeled / disabled, never active.
NO_ADAPTER = {"stable.traits.social_cognition_71d", "stable.traits.developmental_position",
              "stable.traits.personality_function", "stable.values.moral_reasoning",
              "stable.values.cultural_orientation"}


def bounded(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: expected finite number in [{low}, {high}]")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected non-empty text")


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label}: expected fields {sorted(expected)}")


def _scalar_map(value, label):
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{label}: expected a non-empty object")
    for key, item in value.items():
        text(key, label)
        if isinstance(item, bool) or not isinstance(item, (str, int, float)) or (isinstance(item, str) and not item.strip()):
            raise ValueError(f"{label}.{key}: expected text or number")


def _text_list(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: expected a non-empty list")
    for item in value:
        text(item, label)


def _schwartz(value):
    _keys(value, {"weights", "conflict_thresholds", "concession_ranges"}, "schwartz_priorities")
    weights = value["weights"]
    if not isinstance(weights, dict) or not weights or not set(weights) <= set(SCHWARTZ_VALUES):
        raise ValueError("schwartz_priorities.weights: use the nineteen canonical value names")
    for key, weight in weights.items():
        bounded(weight, 0, 1, key)
    if abs(sum(weights.values()) - 1) > 1e-6:
        raise ValueError("schwartz_priorities.weights must sum to 1; unlisted values are unknown, not zero")
    for key, threshold in value["conflict_thresholds"].items():
        if key not in weights:
            raise ValueError(f"conflict threshold for unlisted value: {key}")
        bounded(threshold, 0, 1, key)
    for key, span in value["concession_ranges"].items():
        if key not in weights or not isinstance(span, list) or len(span) != 2:
            raise ValueError(f"concession range for {key} must be [low, high] on a listed value")
        bounded(span[0], 0, 1, key)
        bounded(span[1], 0, 1, key)
        if span[0] > span[1]:
            raise ValueError(f"concession range for {key} is reversed")


def _core_goals(goals):
    if not isinstance(goals, list) or not 1 <= len(goals) <= 3:
        raise ValueError("core_goals: keep one to three primary goals")
    seen = set()
    for goal in goals:
        _keys(goal, {"id", "text", "priority", "stability", "source", "supports"}, "core_goal")
        for key in ("id", "text", "source"):
            text(goal[key], key)
        bounded(goal["priority"], 0, 1, "priority")
        bounded(goal["stability"], 0, 1, "stability")
        if not isinstance(goal["supports"], list) or len(goal["supports"]) > 2:
            raise ValueError("core_goal: at most two supporting sub-goals")
        for sub in goal["supports"]:
            _keys(sub, {"id", "text"}, "sub-goal")
            text(sub["id"], "sub-goal id")
            text(sub["text"], "sub-goal text")
        for ident in [goal["id"]] + [sub["id"] for sub in goal["supports"]]:
            if ident in seen:
                raise ValueError(f"duplicate goal id: {ident}")
            seen.add(ident)


def _rules(rules):
    if not isinstance(rules, list):
        raise ValueError("situational_rules must be a list")
    for rule in rules:
        _keys(rule, {"when", "style"}, "situational rule")
        text(rule["when"], "when")
        text(rule["style"], "style")


def _validate_module(path, value, provenance_ok):
    """Structure of one active module's value."""
    if path in NO_ADAPTER:
        raise ValueError(f"{path}: no adapter yet; keep it unknown/not_modeled and do not invent scores")
    if path == "stable.identity.demographics":
        _scalar_map(value, path)
    elif path == "stable.identity.resources":
        _scalar_map(value, path)
    elif path in ("stable.identity.social_roles", "stable.identity.life_history"):
        _text_list(value, path)
    elif path == "stable.traits.personality":
        _keys(value, {"scheme", "facets"}, path)
        if value["scheme"] != "P1":
            raise ValueError("only the P1 encoding is implemented; P2/P3 await the three-way comparison design")
        if not isinstance(value["facets"], dict) or set(value["facets"]) != FACET_KEYS:
            raise ValueError("P1 requires exactly 30 canonical facets")
        for key, score in value["facets"].items():
            bounded(score, -2, 2, key)
    elif path == "stable.traits.attachment":
        _keys(value, {"anxiety", "avoidance"}, path)
        for key, score in value.items():
            bounded(score, -2, 2, key)
    elif path in ("stable.traits.motivation_baseline", "stable.mechanism_priors"):
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{path} must be a non-empty sparse mapping")
        for key, weight in value.items():
            text(key, path)
            bounded(weight, 0, 1, key)
    elif path == "stable.traits.affective_capacities":
        _keys(value, {"threat_sensitivity", "recovery_rate"}, path)
        for key, score in value.items():
            bounded(score, 0, 1, key)
    elif path == "stable.traits.cognitive_style":
        _keys(value, {"verification_bias"}, path)
        bounded(value["verification_bias"], 0, 1, "verification_bias")
    elif path == "stable.values.schwartz_priorities":
        _schwartz(value)
    elif path == "stable.values.investment":
        bounded(value, 0, 1, path)
    elif path == "stable.core_goals":
        _core_goals(value)
    elif path == "stable.speech_style.baseline":
        _keys(value, {"directness"}, path)
        bounded(value["directness"], 0, 1, "directness")
    elif path == "stable.speech_style.situational_rules":
        _rules(value)


def validate_persona(persona):
    required = {"schema_version", "id", "name", "public_description", "self_view", "stable", "provenance"}
    if not isinstance(persona, dict) or set(persona) != required:
        raise ValueError("persona: unsupported schema or fields")
    if persona["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"persona schema {persona['schema_version']!r} is not supported; "
                         f"rebuild it as {SCHEMA_VERSION} (no silent conversion)")
    for key in ("id", "name", "public_description"):
        text(persona[key], key)
    if not isinstance(persona["self_view"], list) or not persona["self_view"]:
        raise ValueError("self_view must be a non-empty list")
    for item in persona["self_view"]:
        text(item, "self_view")
    stable = persona["stable"]
    if not isinstance(stable, dict) or set(stable) != STABLE_KEYS:
        raise ValueError("stable: unexpected/missing module; record unused modules in module_status")
    _keys(stable["identity"], {"demographics", "social_roles", "resources", "life_history"}, "identity")
    _keys(stable["traits"], TRAIT_KEYS, "traits")
    _keys(stable["values"], {"schwartz_priorities", "investment", "moral_reasoning", "cultural_orientation"}, "values")
    _keys(stable["speech_style"], {"baseline", "situational_rules"}, "speech_style")
    status = stable["module_status"]
    if not isinstance(status, dict) or set(status) != set(STABLE_MODULES):
        raise ValueError("module_status must cover exactly the registered stable modules")
    check_consistency("stable", {k: v for k, v in stable.items() if k != "module_status"}, status)
    for path in REQUIRED_ACTIVE:
        if status[path]["status"] != "active":
            raise ValueError(f"{path} must be active for this runtime")
    active = {path for path, item in status.items() if item["status"] == "active"}
    for path in sorted(active):
        node = {k: v for k, v in stable.items() if k != "module_status"}
        for part in path.split(".")[1:]:
            node = node[part]
        _validate_module(path, node, True)
    if not isinstance(persona["provenance"], dict) or set(persona["provenance"]) != active:
        raise ValueError("each active stable module requires provenance, and only active ones")
    for key, source in persona["provenance"].items():
        if set(source) != {"source_ref", "assignment", "confidence"}:
            raise ValueError(f"{key}: wrong provenance fields")
        text(source["source_ref"], "source_ref")
        text(source["assignment"], "assignment")
        bounded(source["confidence"], 0, 1, "confidence")
    return persona


def _when_clause(when):
    """A situation as a clause ending in 时, whether the author wrote "被催促" or "被催促时"."""
    when = when.strip()
    return when if when.endswith("时") else when + "时"


def conditional_rules(persona):
    """A small explicit compiler, not an unvalidated psychological score estimator."""
    s = persona["stable"]
    f = s["traits"]["personality"]["facets"]
    rules = []
    choices = [
        ("neuroticism.anxiety", "面对含糊的催促容易担心出错，通常先确认要求；明确证据与承诺可缓解担忧。", "通常能容忍暂时的不确定；有明确风险证据时仍会认真核查。"),
        ("conscientiousness.deliberation", "涉及不可逆承诺时倾向先核对条件；时间紧迫时可提出分阶段方案。", "低成本任务中倾向较快尝试；出现错误证据时应允许修正。"),
        ("extraversion.assertiveness", "分歧中较愿意直接提出要求；对方有合理约束时可以协商。", "分歧中较少抢先主导；涉及重要承诺时仍可能明确表达。"),
        ("agreeableness.compliance", "合作中倾向寻找双方可接受的方案；重要边界受损时可以拒绝。", "分歧中较愿意坚持条件；有可靠新证据或合理交换时也可以让步。"),
    ]
    for key, high, low in choices:
        if abs(f[key]) >= .75:
            rules.append({"source_field": f"traits.personality.facets.{key}", "rule": high if f[key] > 0 else low})
    bias = s["traits"]["cognitive_style"]["verification_bias"]
    rules.append({"source_field": "traits.cognitive_style.verification_bias", "rule":
                  "对信息不足的任务通常优先索取证据；证据清楚且代价低时可以直接推进。"
                  if bias >= .5 else
                  "对可逆任务愿意在信息尚不完整时先推进；重要承诺仍需核对。"})
    directness = s["speech_style"]["baseline"]["directness"]
    rules.append({"source_field": "speech_style.baseline.directness", "rule":
                  "通常直说条件和需求，关系修复时也可以解释原因。"
                  if directness >= .5 else
                  "通常先承认对方处境，再委婉提出条件；紧急时可以直说。"})
    for rule in s["speech_style"]["situational_rules"] or []:
        rules.append({"source_field": "speech_style.situational_rules", "rule": f"{_when_clause(rule['when'])}：{rule['style']}"})
    return rules


def compile_views(persona):
    validate_persona(persona)
    s = persona["stable"]
    return {"system_truth": copy.deepcopy(persona),
            "role_self": {"id": persona["id"], "name": persona["name"],
                          "self_view": copy.deepcopy(persona["self_view"])},
            "other_visible": {"id": persona["id"], "name": persona["name"],
                              "description": persona["public_description"]},
            "controller_only": {"conditional_rules": conditional_rules(persona),
                                "needs": copy.deepcopy(s["traits"]["motivation_baseline"]),
                                "values": copy.deepcopy(s["values"]["schwartz_priorities"]),
                                "core_goals": copy.deepcopy(s["core_goals"])}}
