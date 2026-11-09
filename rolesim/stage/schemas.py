"""What each stage's model call must return, and how that becomes a StageOutput (plan/10 §3, §3.3).

  stage_schema(stage, registry)   JSON schema of one stage's model output: the product and the
                                  judgments (only processes this stage may call, registry §4.1 of
                                  each skill; one branch per process so `option` is one of its
                                  own options; the mechanism-card processes of 规则 §4.1 have
                                  typed outputs the update operators read). No state: the model
                                  never names a construct or a value (C8). S1 also marks which
                                  records' content is hearsay or inferred (evidence_notes)
  stage_task(stage, registry)     the data the prompt carries next to the schema: those processes
                                  with their registered options and typed outputs, the
                                  event-grade scale
  validate(schema, value)         a small JSON-schema subset (standard library only)
  parse_output(stage, output, registry, evidence)
                                  -> StageOutput, or StageError when anything breaks the schema or
                                  the registry (an option a process does not have, an evidence
                                  note that upgrades a record)

The evidence set, theory-of-mind limits, bands and versions are program-filled, never asked of
the model. Judgments' hands_to also comes from the registry.

  EXPRESS_SCHEMA / parse_expression(output, actor_id, audience, present)
                                  the expression call (IExpresser): thought / speech / action and
                                  their order -> AgentActionContent; our own thought goes to the
                                  role alone; speech and action reach everyone present (the
                                  audience is only who is addressed) unless the plan marks the
                                  expression private, when only its audience receives them;
                                  checked by agent_api
"""
import math
from typing import Mapping

from ..agent.agent_api import validate_action_content
from ..interfaces import (STAGE_OF_KIND, ActionCandidate, ActionPlan, Appraisal, Commitment,
                         CommitmentRuling, EvidenceRef, EvidenceSet, EvolutionResult, Expression, Hypothesis,
                         InterfaceError, Judgment, RelationCourse, SituationInterpretation, StageError, StageOutput,
                         SwitchCondition)
from ..state.slots import EMOTION_FAMILIES
from ..vocab import CUES

STAGES = tuple(STAGE_OF_KIND.values())
TEXT = {"type": "string", "minLength": 1, "maxLength": 400}
TEXT_OR_NULL = {"type": ["string", "null"], "maxLength": 400}
IDS = {"type": "array", "items": {"type": "string", "minLength": 1}}
TEXTS = {"type": "array", "items": TEXT}
# A free-form record with scalar values only. Open objects let constrained decoding wander (Qwen3
# nested a whole proposals list inside product.attribution); one flat level keeps them in place.
FLAT = {"type": "object", "additionalProperties": {"type": ["string", "number", "boolean", "null"]}}
# 状态变化规则v1 §3.6, in short; the grade is this role's view of this dimension.
GRADES = {1: "日常事件：信息交换、例行互动、轻微的积极或消极线索", 2: "一般事件：普通失礼、小失误、一般性的帮助或拒绝",
          3: "显著事件：明确违约、公开羞辱、确证欺骗、明确兑现承诺或重要帮助",
          4: "重大事件：关系重大背叛、对安全或核心目标的严重威胁、身份转折"}
CHANNEL_RANK = {"witnessed": 0, "hearsay": 1, "inferred": 2}
GRADE = {"type": "integer", "minimum": 1, "maximum": 4}
# At most this many judgments per stage call: the processes that matter this time, not a tour of
# all of them (Qwen3-8B listed one per S2 process until it ran out of tokens, 2026-11-09).
MAX_JUDGMENTS = 6
UNIT = {"type": "number", "minimum": 0, "maximum": 1}
WHO = {"type": ["string", "null"]}                     # a character id; narrowed per call to those present
HYPOTHESIS_KINDS = ("goal", "belief", "intention", "emotion", "ability", "constraint")   # d.cognition.other_model kinds
TRUST_OUTPUT = {"about": WHO, "dimension": {"enum": ["能力", "善意", "诚信", "可靠性"]},
                "direction": {"enum": ["up", "down"]}, "grade": GRADE}
# S1 also rates the five engineering cues persona skills are activated by (vocab.CUES): how
# strongly the role feels each, 0..1. They choose prompt hints and never write state.
CUES_SCHEMA = {"type": "object", "additionalProperties": False,
               "properties": {cue: {"type": "number", "minimum": 0, "maximum": 1} for cue in CUES}}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


PRODUCTS = {
    "S1": _object({
        "situation_definition": TEXT,
        "appraisal": {"type": "array", "items": _object({"goal": TEXT, "kind": {"enum": ["gain", "loss", "threat", "challenge"]},
                                                          "detail": {"type": "string", "maxLength": 400}})},
        "attribution": FLAT,
        "hypotheses": {"type": "array", "minItems": 1, "maxItems": 3, "items": _object({
            "statement": TEXT, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "support": IDS, "counter": IDS, "discriminator": TEXT, "about": {"type": ["string", "null"]},
            "kind": {"enum": [*HYPOTHESIS_KINDS, None]}})},
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
        "expression": _object({"goal": TEXT, "audience": IDS, "manner": TEXT, "private": {"type": "boolean"}}),
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


# -- typed judgment outputs (状态变化规则 §4.1 mechanism cards; engineering minimum, awaiting F2) ------
# The update operators read these fields; every other process's output is a flat free-form record.
JUDGMENT_OUTPUTS = {
    "dm.affect.primary_secondary_appraisal": _object({
        "grade": GRADE, "emotions": {"type": "array", "maxItems": 3, "items": {"enum": list(EMOTION_FAMILIES)}},
        "valence": {"enum": ["positive", "negative", "mixed", "neutral"]}, "about": WHO}),
    "dm.cog.belief_updating": _object({"beliefs": {"type": "array", "maxItems": 3, "items": _object({
        "belief_id": {"type": ["string", "null"]}, "proposition": TEXT, "kind": {"enum": ["fact", "interpretation"]},
        "about": {"type": "array", "items": {"type": "string"}}, "change": {"enum": ["新形成", "增强", "减弱", "悬置"]},
        "confidence": UNIT, "grade": GRADE})}}),
    "dm.cog.attribution_responsibility": _object({
        "event": TEXT, "about": WHO, "locus": {"enum": ["internal", "external", None]},
        "stability": {"enum": ["stable", "temporary", None]}, "globality": {"enum": ["global", "specific", None]},
        "controllability": {"enum": ["controllable", "uncontrollable", None]}, "intent": TEXT_OR_NULL,
        "self_share": UNIT, "others_share": UNIT, "confidence": UNIT}),
    "dm.social.trust_reputation_update": _object(TRUST_OUTPUT),
    "dm.coop.trust_building": _object(TRUST_OUTPUT),
    "dm.conflict.relationship_repair": _object({"about": WHO, "assessment": {"enum": ["无", "部分", "完全"]},
                                                "outcome": TEXT_OR_NULL}),
}


def _options(options):
    return {"enum": [*options, None]} if options else {"type": "null"}


def stage_schema(stage, registry, mechanisms=True):
    """`mechanisms` False (the ablation modes without decision processes): no judgments. The
    evidence list every judgment shares lives in `$defs` and is referenced, so narrowing it per
    call is one edit; compact_schema() is the short form for prompts."""
    _check_stage(stage)
    allowed = stage_mechanisms(stage, registry) if mechanisms else []
    defs = {"evidence": IDS}
    evidence = {"$ref": "#/$defs/evidence"}
    branches = [_object({"dm_id": {"const": dm}, "option": _options(registry.mechanism(dm).options),
                         "output": JUDGMENT_OUTPUTS.get(dm, FLAT), "evidence": evidence}) for dm in allowed]
    judgment = {"anyOf": branches} if branches else _object({
        "dm_id": {"enum": allowed}, "option": TEXT_OR_NULL, "output": FLAT, "evidence": evidence})
    properties = {"product": PRODUCTS[stage],
                  "judgments": {"type": "array", "maxItems": MAX_JUDGMENTS if mechanisms else 0, "items": judgment}}
    if stage == "S1":
        properties["evidence_notes"] = {"type": "array", "items": _object({
            "event": {"type": "string", "minLength": 1}, "channel": {"enum": list(CHANNEL_RANK)}, "claim": TEXT_OR_NULL})}
        properties["cues"] = CUES_SCHEMA
    return {**_object(properties), "$defs": defs}


def compact_schema(schema):
    """The short form of a stage schema for the prompt: one generic judgment (the options and the
    typed outputs of each process are listed in the stage task) with `$defs` inlined. The full
    schema still drives decoding and checking."""
    import copy
    out = copy.deepcopy(schema)
    defs = out.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node
    judgments = out["properties"]["judgments"]
    branches = judgments["items"].get("anyOf")
    if branches:
        generic = dict(branches[0]["properties"])
        generic.update(dm_id={"enum": [b["properties"]["dm_id"]["const"] for b in branches]}, option=TEXT_OR_NULL,
                       output={"type": "object"})
        judgments["items"] = _object(generic, branches[0]["required"])
    return inline(out)


def _id_list(ids):
    return {"type": "array", "items": {"enum": list(ids)}} if ids else {"type": "array", "maxItems": 0}


def _narrow_who(node, others):
    """Every WHO field (a character id or null) inside `node` -> one of `others` or null."""
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if value == WHO:
                node[key] = {"enum": [*others, None]}
            else:
                _narrow_who(value, others)
    elif isinstance(node, list):
        for value in node:
            _narrow_who(value, others)


def narrow_evidence(schema, record_ids, audience_ids=None):
    """A copy of a stage schema narrowed to one call: evidence (the shared `$defs` list and S1
    evidence_notes `event`) can only name `record_ids`, the records this call shows; when
    `audience_ids` (the other characters present) are given, the S2 audience and every "who"
    field (judgment `about`, hypothesis `about`) can only name them. Used for structured decoding
    and for checking the reply; the evidence gate and the stage still check visibility."""
    import copy
    out = copy.deepcopy(schema)
    props = out.get("properties", {})
    if audience_ids is not None:
        others = list(dict.fromkeys(audience_ids))
        expression = props.get("product", {}).get("properties", {}).get("expression")
        if expression:
            expression["properties"]["audience"] = _id_list(others)
        _narrow_who(props, others)
    ids = list(dict.fromkeys(record_ids))
    if "evidence" in out.get("$defs", {}):
        out["$defs"]["evidence"] = _id_list(ids)
    notes = props.get("evidence_notes")
    if notes:
        if ids:
            notes["items"]["properties"]["event"] = {"enum": ids}
        else:
            notes["maxItems"] = 0
    return out


def stage_task(stage, registry, mechanisms=True):
    _check_stage(stage)
    operators = stage_operators(stage, registry)
    listed = []
    for dm_id in (stage_mechanisms(stage, registry) if mechanisms else []):
        m = registry.mechanism(dm_id)
        item = {"dm_id": dm_id, "name": m.name, "options": list(m.options),
                "hands_to": [op for op in m.hands_to if op in operators]}
        if dm_id in JUDGMENT_OUTPUTS:
            item["output_fields"] = list(JUDGMENT_OUTPUTS[dm_id]["properties"])
        listed.append(item)
    return {"stage": stage, "mechanisms": listed, "grades": {str(g): text for g, text in GRADES.items()}}


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


def validate(schema, value, path="$", root=None):
    """Errors of `value` against a JSON-schema subset (type, enum, const, anyOf, properties, required,
    additionalProperties, items, minItems / maxItems, minimum / maximum, minLength / maxLength,
    and $ref to "#/$defs/<name>" of the root schema)."""
    root = schema if root is None else root
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return validate(root.get("$defs", {})[name], value, path, root)
    if "anyOf" in schema:
        branches = schema["anyOf"]
        tried = [validate(branch, value, path, root) for branch in branches]
        if not all(tried):
            return []
        # Tagged branches (an object property with a const, like dm_id): report the branch whose
        # tag matches, or that the tag is not an allowed value.
        if isinstance(value, Mapping):
            tags = {k for b in branches for k, s in b.get("properties", {}).items() if "const" in s}
            for tag in sorted(tags):
                matching = [errors for b, errors in zip(branches, tried)
                            if b.get("properties", {}).get(tag, {}).get("const") == value.get(tag)]
                if matching:
                    return min(matching, key=len)
                return [f"{path}.{tag}: {value.get(tag)!r} is not one of the allowed values"]
        return min(tried, key=len)
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
                errors += validate(schema["items"], item, f"{path}[{i}]", root)
    if isinstance(value, Mapping):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}: missing {name}")
        properties = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                errors += validate(properties[name], item, f"{path}.{name}", root)
            elif extra is False:
                errors.append(f"{path}: unexpected field {name}")
            elif isinstance(extra, Mapping):
                errors += validate(extra, item, f"{path}.{name}", root)
    return errors


# -- parsing ----------------------------------------------------------------------------------------
def _product(stage, d):
    if stage == "S1":
        return SituationInterpretation(
            d["situation_definition"], tuple(Appraisal(a["goal"], a["kind"], a["detail"]) for a in d["appraisal"]),
            dict(d["attribution"]),
            tuple(Hypothesis(h["statement"], h["confidence"], tuple(h["support"]), tuple(h["counter"]), h["discriminator"],
                             h["about"], h["kind"]) for h in d["hypotheses"]),
            d["judgment_state"], tuple(d["information_needs"]), dict(d["relation_signals"]))
    if stage == "S2":
        return ActionPlan(
            tuple(ActionCandidate(c["action"], c["kept"], c["constraint"]) for c in d["candidates"]), d["chosen"],
            tuple(d["fallbacks"]), tuple(SwitchCondition(s["signal"], s["then"]) for s in d["switch_conditions"]),
            tuple(d["depends_on"]), tuple(d["expected_outcomes"]),
            Commitment(d["commitment"]["formed"], d["commitment"]["content"]),
            Expression(d["expression"]["goal"], tuple(d["expression"]["audience"]), d["expression"]["manner"],
                       d["expression"]["private"]))
    return EvolutionResult(
        d["expected_vs_actual"], tuple(CommitmentRuling(r["commitment"], r["ruling"]) for r in d["commitment_rulings"]),
        tuple(RelationCourse(r["partner"], r["course"], tuple(r["repair_conditions"])) for r in d["relation_course"]),
        d["learning_attribution"], tuple(d["carry_forward"]))


def _refine(evidence, notes, stage):
    """S1's evidence notes may mark a seen record's content as hearsay or inferred (weaker), never
    stronger. A note that cannot apply (one that would strengthen evidence, or one on the role's own
    action) changes nothing and is ignored rather than failing the stage; a note on a record the role
    has not seen still fails it (structured decoding normally makes that impossible)."""
    refs = {e.event: e for e in evidence.included}
    for note in notes:
        current = refs.get(note["event"])
        if current is None:
            raise StageError(f"{stage}: evidence note on a record this role has not seen: {note['event']}")
        if current.channel not in CHANNEL_RANK or CHANNEL_RANK[note["channel"]] <= CHANNEL_RANK[current.channel]:
            continue
        refs[note["event"]] = EvidenceRef(note["event"], note["channel"], note["claim"])
    return EvidenceSet(tuple(refs[e.event] for e in evidence.included), evidence.excluded, evidence.retrieved_memories)


def parse_output(stage, output, registry, evidence, mechanisms=True):
    """Model output -> StageOutput with the program's EvidenceSet (refined by S1's notes)."""
    _check_stage(stage)
    errors = validate(stage_schema(stage, registry, mechanisms), output)
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
    try:
        product = _product(stage, output["product"])
    except InterfaceError as exc:
        raise StageError(f"{stage}: {exc}") from None
    return StageOutput(product, tuple(judgments), evidence, dict(output.get("cues") or {}))


# -- expression (IExpresser) -------------------------------------------------------------------------
EXPRESS_KINDS = ("thought", "speech", "action")
EXPRESS_SCHEMA = _object({
    "thought": {"type": ["string", "null"], "maxLength": 300},
    "speech": {"type": ["string", "null"], "maxLength": 600},
    "action": {"type": ["string", "null"], "maxLength": 300},
    "order": {"type": "array", "maxItems": 3, "items": {"enum": list(EXPRESS_KINDS)}},
})


def parse_expression(output, actor_id, audience, present, private=False):
    """`audience` is whom the plan addresses. Being addressed is not the same as hearing: in a
    shared scene everyone present hears speech and sees actions (recipient_ids None, interface v2
    "本部分的可见范围"), unless the plan made the expression `private` to its audience."""
    errors = validate(EXPRESS_SCHEMA, output)
    if errors:
        raise StageError(f"express: output breaks the schema: {'; '.join(errors[:8])}")
    filled = [k for k in EXPRESS_KINDS if isinstance(output[k], str) and output[k].strip()]
    if not filled:
        raise StageError("express: nothing to say or do")
    order = list(output["order"])
    if len(order) != len(set(order)) or set(order) != set(filled):
        raise StageError(f"express: order {order} must list each filled part {filled} once")
    recipients = list(dict.fromkeys(audience)) if private else None
    if private and not recipients:
        raise StageError("express: a private expression needs an audience")
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
