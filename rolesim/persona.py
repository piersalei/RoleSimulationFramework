"""Versioned authoring contract; stable fields never change during an episode."""
import copy
import math

FACETS = {
    "openness": ["fantasy", "aesthetics", "feelings", "actions", "ideas", "values"],
    "conscientiousness": ["competence", "order", "dutifulness", "achievement_striving", "self_discipline", "deliberation"],
    "extraversion": ["warmth", "gregariousness", "assertiveness", "activity", "excitement_seeking", "positive_emotions"],
    "agreeableness": ["trust", "straightforwardness", "altruism", "compliance", "modesty", "tender_mindedness"],
    "neuroticism": ["anxiety", "angry_hostility", "depression", "self_consciousness", "impulsiveness", "vulnerability"],
}
FACET_KEYS = {f"{domain}.{facet}" for domain, facets in FACETS.items() for facet in facets}


def bounded(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: expected finite number in [{low}, {high}]")


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected non-empty text")


def validate_persona(persona):
    required = {"schema_version", "id", "name", "public_description", "self_view", "stable", "provenance", "deferred_modules"}
    if set(persona) != required or persona["schema_version"] != "1.0":
        raise ValueError("persona: unsupported schema or fields")
    for key in ("id", "name", "public_description"):
        text(persona[key], key)
    if not isinstance(persona["self_view"], list) or not persona["self_view"]:
        raise ValueError("self_view must be a non-empty list")
    for item in persona["self_view"]:
        text(item, "self_view")
    stable = persona["stable"]
    expected = {"personality", "attachment", "needs", "values", "abilities", "emotion", "cognition", "communication", "mechanism_priors"}
    if set(stable) != expected:
        raise ValueError("stable: unexpected/missing module; record disabled modules explicitly")
    personality = stable["personality"]
    if set(personality) != {"scheme", "facets"} or personality["scheme"] != "P1":
        raise ValueError("v0.1 supports exactly one P1 encoding; P2/P3 are reference-only")
    if set(personality["facets"]) != FACET_KEYS:
        raise ValueError("P1 requires exactly 30 canonical facets")
    for key, value in personality["facets"].items():
        bounded(value, -2, 2, key)
    if set(stable["attachment"]) != {"anxiety", "avoidance"}:
        raise ValueError("attachment requires two independent axes")
    for key, value in stable["attachment"].items():
        bounded(value, -2, 2, key)
    for group, keys in (("emotion", {"threat_sensitivity", "recovery_rate"}),
                        ("cognition", {"verification_bias"}),
                        ("communication", {"directness"})):
        if set(stable[group]) != keys:
            raise ValueError(f"{group}: wrong fields")
        for key, value in stable[group].items():
            bounded(value, 0, 1, key)
    for group in ("needs", "values", "mechanism_priors"):
        if not isinstance(stable[group], dict) or not stable[group]:
            raise ValueError(f"{group} must be a non-empty sparse mapping")
        for key, value in stable[group].items():
            text(key, group)
            bounded(value, 0, 1, key)
    if stable["abilities"] != {"status": "pending_source_definition", "scores": {}}:
        raise ValueError("71-dimension ability adapter is not implemented; do not invent scores")
    if set(persona["provenance"]) != set(stable):
        raise ValueError("each stable module requires provenance")
    for key, source in persona["provenance"].items():
        if set(source) != {"source_ref", "assignment", "confidence"}:
            raise ValueError(f"{key}: wrong provenance fields")
        text(source["source_ref"], "source_ref")
        text(source["assignment"], "assignment")
        bounded(source["confidence"], 0, 1, "confidence")
    if not isinstance(persona["deferred_modules"], dict):
        raise ValueError("deferred_modules requires explicit reasons")
    for reason in persona["deferred_modules"].values():
        text(reason, "deferred reason")
    return persona


def conditional_rules(persona):
    """A small explicit compiler, not an unvalidated psychological score estimator."""
    s = persona["stable"]
    f = s["personality"]["facets"]
    rules = []
    choices = [
        ("neuroticism.anxiety", "面对含糊的催促容易担心出错，通常先确认要求；明确证据与承诺可缓解担忧。", "通常能容忍暂时的不确定；有明确风险证据时仍会认真核查。"),
        ("conscientiousness.deliberation", "涉及不可逆承诺时倾向先核对条件；时间紧迫时可提出分阶段方案。", "低成本任务中倾向较快尝试；出现错误证据时应允许修正。"),
        ("extraversion.assertiveness", "分歧中较愿意直接提出要求；对方有合理约束时可以协商。", "分歧中较少抢先主导；涉及重要承诺时仍可能明确表达。"),
        ("agreeableness.compliance", "合作中倾向寻找双方可接受的方案；重要边界受损时可以拒绝。", "分歧中较愿意坚持条件；有可靠新证据或合理交换时也可以让步。"),
    ]
    for key, high, low in choices:
        if abs(f[key]) >= .75:
            rules.append({"source_field": f"personality.facets.{key}", "rule": high if f[key] > 0 else low})
    rules.append({"source_field": "cognition.verification_bias", "rule":
                  "对信息不足的任务通常优先索取证据；证据清楚且代价低时可以直接推进。"
                  if s["cognition"]["verification_bias"] >= .5 else
                  "对可逆任务愿意在信息尚不完整时先推进；重要承诺仍需核对。"})
    rules.append({"source_field": "communication.directness", "rule":
                  "通常直说条件和需求，关系修复时也可以解释原因。"
                  if s["communication"]["directness"] >= .5 else
                  "通常先承认对方处境，再委婉提出条件；紧急时可以直说。"})
    return rules


def compile_views(persona):
    validate_persona(persona)
    return {"system_truth": copy.deepcopy(persona),
            "role_self": {"id": persona["id"], "name": persona["name"],
                          "self_view": copy.deepcopy(persona["self_view"])},
            "other_visible": {"id": persona["id"], "name": persona["name"],
                              "description": persona["public_description"]},
            "controller_only": {"conditional_rules": conditional_rules(persona),
                                "needs": copy.deepcopy(persona["stable"]["needs"]),
                                "values": copy.deepcopy(persona["stable"]["values"])}}
