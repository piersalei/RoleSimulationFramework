"""What each stage's model call must return, and how that becomes a StageOutput (plan/10 §3, §3.3).

  stage_schema(stage, registry)   JSON schema of one stage's model output: the product, the
                                  judgments (only processes this stage may call, registry §4.1 of
                                  each skill) and the proposals (only constructs an operator
                                  allowed in this stage may write); S1 also marks which records'
                                  content is hearsay or inferred (evidence_notes)
  stage_task(stage, registry)     the data the prompt carries next to the schema: those processes
                                  with their registered options, those constructs with their
                                  fields, the event-grade scale
  validate(schema, value)         a small JSON-schema subset (standard library only)
  parse_output(stage, output, registry, evidence)
                                  -> StageOutput, or StageError when anything breaks the schema or
                                  the registry (an option a process does not have, a proposal
                                  resting on a process the stage did not judge, an evidence note
                                  that upgrades a record)

The evidence set, theory-of-mind limits, bands and versions are program-filled, never asked of
the model. Judgments' hands_to also comes from the registry.

  EXPRESS_SCHEMA / parse_expression(output, actor_id, audience, present)
                                  the expression call (IExpresser): thought / speech / action and
                                  their order -> AgentActionContent; our own thought goes to the
                                  role alone, speech and action to the plan's audience (everyone
                                  present when it names nobody), checked by agent_api
"""
import math
from typing import Mapping

from .agent_api import validate_action_content
from .interfaces import (STAGE_OF_KIND, ActionCandidate, ActionPlan, Appraisal, ChangeProposal, Commitment,
                         CommitmentRuling, EvidenceRef, EvidenceSet, EvolutionResult, Expression, Hypothesis,
                         InterfaceError, Judgment, RelationCourse, SituationInterpretation, StageError, StageOutput,
                         SwitchCondition)
from .slots import SLOTS

STAGES = tuple(STAGE_OF_KIND.values())
TEXT = {"type": "string", "minLength": 1, "maxLength": 400}
TEXT_OR_NULL = {"type": ["string", "null"], "maxLength": 400}
IDS = {"type": "array", "items": {"type": "string", "minLength": 1}}
TEXTS = {"type": "array", "items": TEXT}
# 状态变化规则v1 §3.6, in short; the grade is this role's view of this dimension.
GRADES = {1: "日常事件：信息交换、例行互动、轻微的积极或消极线索", 2: "一般事件：普通失礼、小失误、一般性的帮助或拒绝",
          3: "显著事件：明确违约、公开羞辱、确证欺骗、明确兑现承诺或重要帮助",
          4: "重大事件：关系重大背叛、对安全或核心目标的严重威胁、身份转折"}
CHANNEL_RANK = {"witnessed": 0, "hearsay": 1, "inferred": 2}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


PRODUCTS = {
    "S1": _object({
        "situation_definition": TEXT,
        "appraisal": {"type": "array", "items": _object({"goal": TEXT, "kind": {"enum": ["gain", "loss", "threat", "challenge"]},
                                                          "detail": {"type": "string", "maxLength": 400}})},
        "attribution": {"type": "object"},
        "hypotheses": {"type": "array", "minItems": 1, "maxItems": 3, "items": _object({
            "statement": TEXT, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "support": IDS, "counter": IDS, "discriminator": TEXT})},
        "judgment_state": {"enum": ["settled", "multiple", "suspended"]},
        "information_needs": TEXTS,
        "relation_signals": {"type": "object", "additionalProperties": TEXT},
    }),
    "S2": _object({
        "candidates": {"type": "array", "minItems": 1, "items": _object({"action": TEXT, "kept": {"type": "boolean"},
                                                                          "constraint": TEXT_OR_NULL})},
        "chosen": TEXT,
        "fallbacks": TEXTS,
        "switch_conditions": {"type": "array", "items": _object({"signal": TEXT, "then": TEXT})},
        "depends_on": TEXTS,
        "expected_outcomes": TEXTS,
        "commitment": _object({"formed": {"type": "boolean"}, "content": TEXT_OR_NULL}),
        "expression": _object({"goal": TEXT, "audience": IDS, "manner": TEXT}),
    }),
    "S3": _object({
        "expected_vs_actual": TEXT,
        "commitment_rulings": {"type": "array", "items": _object({
            "commitment": TEXT, "ruling": {"enum": ["kept", "broken", "pending", "released"]}})},
        "relation_course": {"type": "array", "items": _object({
            "partner": TEXT, "course": {"enum": ["improving", "stable", "worsening", "rupture"]}, "repair_conditions": TEXTS})},
        "learning_attribution": TEXT,
        "carry_forward": TEXTS,
    }),
}


# -- registry views ----------------------------------------------------------------------------
def stage_operators(stage, registry):
    return [op for op in registry.ids("operator") if stage in registry.operator(op).stages]


def stage_mechanisms(stage, registry):
    return [dm for dm in registry.ids("mechanism") if stage in registry.mechanism(dm).stages]


def stage_constructs(stage, registry):
    writable = {cid for op in stage_operators(stage, registry) for cid in registry.operator(op).writes}
    return [cid for cid in registry.ids("construct") if cid in writable]


def _check_stage(stage):
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}, got {stage!r}")


def stage_schema(stage, registry):
    _check_stage(stage)
    mechanisms = stage_mechanisms(stage, registry)
    properties = {
        "product": PRODUCTS[stage],
        "judgments": {"type": "array", "items": _object({
            "dm_id": {"enum": mechanisms}, "option": TEXT_OR_NULL, "output": {"type": "object"}, "evidence": IDS})},
        "proposals": {"type": "array", "items": _object({
            "construct_id": {"enum": stage_constructs(stage, registry)}, "key": TEXT_OR_NULL, "field": TEXT_OR_NULL,
            "after": {}, "reason": TEXT, "grade": {"type": ["integer", "null"], "minimum": 1, "maximum": 4},
            "evidence": IDS, "mechanisms": {"type": "array", "items": {"enum": mechanisms}},
            "operator": {"enum": stage_operators(stage, registry)}},
            required=["construct_id", "key", "field", "after", "reason", "grade", "evidence", "mechanisms"])},
    }
    if stage == "S1":
        properties["evidence_notes"] = {"type": "array", "items": _object({
            "event": {"type": "string", "minLength": 1}, "channel": {"enum": list(CHANNEL_RANK)}, "claim": TEXT_OR_NULL})}
    return _object(properties)


def stage_task(stage, registry):
    _check_stage(stage)
    operators = stage_operators(stage, registry)
    mechanisms = []
    for dm_id in stage_mechanisms(stage, registry):
        m = registry.mechanism(dm_id)
        mechanisms.append({"dm_id": dm_id, "name": m.name, "options": list(m.options),
                           "hands_to": [op for op in m.hands_to if op in operators]})
    constructs = []
    for cid in stage_constructs(stage, registry):
        c, slot = registry.construct(cid), SLOTS[cid]
        constructs.append({"construct_id": cid, "name": c.name,
                           "operators": [op for op in operators if cid in registry.operator(op).writes],
                           "keys": list(slot.keys), "fields": dict(slot.fields), "tempo": c.tempo,
                           "banded": {f: scale for f, scale in slot.banded.items()}})
    return {"stage": stage, "operators": operators, "mechanisms": mechanisms, "constructs": constructs,
            "grades": {str(g): text for g, text in GRADES.items()}, "bands": band_guide()}


def band_guide():
    """Typical |change| per event grade for each tempo class (状态变化规则 §5.0), as guidance:
    a banded field's scale multiplies it; personality and evidence quality may move it by one
    band; the program checks the final value."""
    from .magnitude import FAST, GRADE_LEVEL, SLOW
    return {name: {str(g): list(table[level]) for g, level in GRADE_LEVEL.items()}
            for name, table in (("slow", SLOW), ("fast", FAST))}


# -- validation -----------------------------------------------------------------------------------
def _is(type_name, value):
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "null":
        return value is None
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, (list, tuple))
    raise ValueError(f"unsupported schema type {type_name!r}")


def validate(schema, value, path="$"):
    """Errors of `value` against a JSON-schema subset (type, enum, const, properties, required,
    additionalProperties, items, minItems / maxItems, minimum / maximum, minLength / maxLength)."""
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: {value!r} is not one of the allowed values"]
    if "const" in schema and value != schema["const"]:
        return [f"{path}: must be {schema['const']!r}"]
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else list(types)
        if not any(_is(t, value) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]
    errors = []
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
    if _is("number", value):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, (list, tuple)):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(schema["items"], item, f"{path}[{i}]")
    if isinstance(value, Mapping):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing {name}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors += validate(properties[name], item, f"{path}.{name}")
            elif extra is False:
                errors.append(f"{path}: unexpected field {name}")
            elif isinstance(extra, Mapping):
                errors += validate(extra, item, f"{path}.{name}")
    return errors


# -- parsing ----------------------------------------------------------------------------------------
def _product(stage, d):
    if stage == "S1":
        return SituationInterpretation(
            d["situation_definition"], tuple(Appraisal(a["goal"], a["kind"], a["detail"]) for a in d["appraisal"]),
            dict(d["attribution"]),
            tuple(Hypothesis(h["statement"], h["confidence"], tuple(h["support"]), tuple(h["counter"]), h["discriminator"])
                  for h in d["hypotheses"]),
            d["judgment_state"], tuple(d["information_needs"]), dict(d["relation_signals"]))
    if stage == "S2":
        return ActionPlan(
            tuple(ActionCandidate(c["action"], c["kept"], c["constraint"]) for c in d["candidates"]), d["chosen"],
            tuple(d["fallbacks"]), tuple(SwitchCondition(s["signal"], s["then"]) for s in d["switch_conditions"]),
            tuple(d["depends_on"]), tuple(d["expected_outcomes"]),
            Commitment(d["commitment"]["formed"], d["commitment"]["content"]),
            Expression(d["expression"]["goal"], tuple(d["expression"]["audience"]), d["expression"]["manner"]))
    return EvolutionResult(
        d["expected_vs_actual"], tuple(CommitmentRuling(r["commitment"], r["ruling"]) for r in d["commitment_rulings"]),
        tuple(RelationCourse(r["partner"], r["course"], tuple(r["repair_conditions"])) for r in d["relation_course"]),
        d["learning_attribution"], tuple(d["carry_forward"]))


def _refine(evidence, notes, stage):
    """S1's evidence notes may mark a seen record's content as hearsay or inferred, never upgrade it."""
    refs = {e.event: e for e in evidence.included}
    for note in notes:
        current = refs.get(note["event"])
        if current is None:
            raise StageError(f"{stage}: evidence note on a record this role has not seen: {note['event']}")
        if note["channel"] == "hearsay" and not note["claim"]:
            raise StageError(f"{stage}: a hearsay note must state the relayed claim ({note['event']})")
        if current.channel not in CHANNEL_RANK or CHANNEL_RANK[note["channel"]] < CHANNEL_RANK[current.channel]:
            if note["channel"] != current.channel:
                raise StageError(f"{stage}: cannot turn {current.channel} evidence into {note['channel']} ({note['event']})")
            continue
        refs[note["event"]] = EvidenceRef(note["event"], note["channel"], note["claim"])
    return EvidenceSet(tuple(refs[e.event] for e in evidence.included), evidence.excluded, evidence.retrieved_memories)


def parse_output(stage, output, registry, evidence):
    """Model output -> StageOutput with the program's EvidenceSet (refined by S1's notes)."""
    _check_stage(stage)
    errors = validate(stage_schema(stage, registry), output)
    if errors:
        raise StageError(f"{stage}: output breaks the schema: {'; '.join(errors[:8])}")
    if stage == "S1":
        evidence = _refine(evidence, output["evidence_notes"], stage)
    refs = {e.event: e for e in evidence.included}

    def cite(ids):
        return tuple(refs.get(i, EvidenceRef(i, "inferred")) for i in dict.fromkeys(ids))

    operators = set(stage_operators(stage, registry))
    judgments = []
    for j in output["judgments"]:
        mechanism = registry.mechanism(j["dm_id"])
        if j["option"] is not None and j["option"] not in mechanism.options:
            raise StageError(f"{stage}: {j['dm_id']} has no option {j['option']!r}")
        judgments.append(Judgment(j["dm_id"], j["option"], dict(j["output"]), cite(j["evidence"]),
                                  tuple(op for op in mechanism.hands_to if op in operators)))
    judged = {j.dm_id for j in judgments}
    proposals = []
    for p in output["proposals"]:
        missing = [m for m in p["mechanisms"] if m not in judged]
        if missing:
            raise StageError(f"{stage}: proposal for {p['construct_id']} rests on processes not judged here: {missing}")
        proposals.append(ChangeProposal(p["construct_id"], p["key"], p["field"], p["after"], p["reason"], p["grade"],
                                        cite(p["evidence"]), tuple(p["mechanisms"]), p.get("operator")))
    try:
        product = _product(stage, output["product"])
    except InterfaceError as exc:
        raise StageError(f"{stage}: {exc}") from None
    return StageOutput(product, tuple(proposals), tuple(judgments), evidence)


# -- expression (IExpresser) -------------------------------------------------------------------------
EXPRESS_KINDS = ("thought", "speech", "action")
EXPRESS_SCHEMA = _object({
    "thought": {"type": ["string", "null"], "maxLength": 300},
    "speech": {"type": ["string", "null"], "maxLength": 600},
    "action": {"type": ["string", "null"], "maxLength": 300},
    "order": {"type": "array", "maxItems": 3, "items": {"enum": list(EXPRESS_KINDS)}},
})


def parse_expression(output, actor_id, audience, present):
    errors = validate(EXPRESS_SCHEMA, output)
    if errors:
        raise StageError(f"express: output breaks the schema: {'; '.join(errors[:8])}")
    filled = [k for k in EXPRESS_KINDS if isinstance(output[k], str) and output[k].strip()]
    if not filled:
        raise StageError("express: nothing to say or do")
    order = list(output["order"])
    if len(order) != len(set(order)) or set(order) != set(filled):
        raise StageError(f"express: order {order} must list each filled part {filled} once")
    recipients = list(dict.fromkeys(audience)) or None
    content = {k: None for k in EXPRESS_KINDS}
    for kind in filled:
        content[kind] = {"content": output[kind].strip(),
                         "recipient_ids": [actor_id] if kind == "thought" else (list(recipients) if recipients else None)}
    content["order"] = order
    try:
        validate_action_content(content, present, actor_id)
    except InterfaceError as exc:
        raise StageError(f"express: {exc}") from None
    return content


# -- profile text (IProfileCompiler) ------------------------------------------------------------------
def stable_constructs(registry):
    return [cid for cid in registry.ids("construct") if registry.construct(cid).layer == "stable"]


def profile_schema(registry):
    """What the "profile" call returns: stable constructs read from the text, each with its quote."""
    return _object({"modules": {"type": "array", "items": _object({
        "construct_id": {"enum": stable_constructs(registry)}, "value": {},
        "quote": {"type": "string", "minLength": 1, "maxLength": 400}, "phrase": TEXT_OR_NULL})}})


def profile_task(registry, exclude=()):
    """The constructs the text may speak to (those not written explicitly), and whether a
    conditioned phrase is needed (the role cannot be aware of them)."""
    out = []
    for cid in stable_constructs(registry):
        if cid in exclude:
            continue
        c = registry.construct(cid)
        out.append({"construct_id": cid, "name": c.name, "needs_phrase": c.self_visibility not in ("aware", "partial")})
    return {"stage": "profile", "constructs": out}
