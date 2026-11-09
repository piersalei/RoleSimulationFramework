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
                                  the registry (an option a process does not have, an evidence
                                  note that upgrades a record); a proposal resting on a process
                                  the stage did not judge is left to the store to reject

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
from ..interfaces import (STAGE_OF_KIND, ActionCandidate, ActionPlan, Appraisal, ChangeProposal, Commitment,
                         CommitmentRuling, EvidenceRef, EvidenceSet, EvolutionResult, Expression, Hypothesis,
                         InterfaceError, Judgment, RelationCourse, SituationInterpretation, StageError, StageOutput,
                         SwitchCondition)
from ..state.slots import EMOTION_FAMILIES, RANGES, SLOTS
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


# -- proposal addresses from the state layout (slots.SLOTS) ------------------------------------
def kind_schema(kind):
    """JSON schema of one slot value kind (slots.check_kind); a trailing "?" allows null."""
    nullable = kind.endswith("?")
    kind = kind.rstrip("?")
    if kind in RANGES:
        low, high = RANGES[kind]
        out = {"type": "number", "minimum": low, "maximum": high}
    elif kind == "int":
        out = {"type": "integer", "minimum": 0}
    elif kind == "grade":
        out = {"type": "integer", "minimum": 1, "maximum": 4}
    elif kind in ("text", "opt"):
        out = {"type": "string", "minLength": 1}
    elif kind == "bool":
        out = {"type": "boolean"}
    elif kind == "refs":
        out = {"type": "array", "items": {"type": "string", "minLength": 1}}
    elif kind == "list":
        out = {"type": "array"}
    elif kind == "map":
        out = FLAT
    elif kind == "labels":
        out = {"type": "array", "items": {"enum": list(EMOTION_FAMILIES)}}
    elif kind.startswith("enum(") and kind.endswith(")"):
        out = {"enum": kind[5:-1].split("|")}
    else:
        raise ValueError(f"unknown kind {kind!r}")
    if not nullable:
        return out
    if "enum" in out:
        return {"enum": [*out["enum"], None]}
    return {**out, "type": [out["type"], "null"]}


def _record_schema(fields):
    """A whole instance: declared fields (nested for dotted specs, a map for "*"), any subset."""
    tree = {}
    for spec, kind in fields.items():
        node = tree
        parts = spec.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = kind

    def build(node):
        if isinstance(node, str):
            return kind_schema(node)
        if set(node) == {"*"}:
            return {"type": "object", "additionalProperties": build(node["*"])}
        return {"type": "object", "additionalProperties": False, "required": [],
                "properties": {name: build(sub) for name, sub in node.items() if name != "*"}}
    return build(tree)


def _key_schema(slot):
    # No `pattern` anywhere: xgrammar on vLLM 0.17 stopped enforcing the whole schema once one was
    # present (seen 2026-11-09). keys_as_lists() turns this into one string per key level for decoding.
    return {"type": "null"} if not slot.keys else {"type": "string", "minLength": 1, "x-key-parts": len(slot.keys)}


def proposal_branches(cid, common):
    """One proposal shape per writable address of construct `cid`: the whole instance (field
    null) and each declared field (a wildcard spec such as domains.* as any field name, which the
    store then resolves); `common` holds the other proposal properties."""
    slot = SLOTS[cid]
    fields = {spec: kind for spec, kind in slot.fields.items() if spec.split(".")[0] not in slot.not_modeled}
    head = {"construct_id": {"const": cid}, "key": _key_schema(slot)}
    required = ["construct_id", "key", "field", "after", "reason", "grade", "evidence", "mechanisms"]
    if "" in fields:
        return [_object({**head, "field": {"type": "null"}, "after": kind_schema(fields[""]), **common}, required)]
    out = [_object({**head, "field": {"type": "null"}, "after": _record_schema(fields), **common}, required)]
    for spec, kind in fields.items():
        field = {"type": "string", "minLength": 1} if "*" in spec else {"const": spec}
        out.append(_object({**head, "field": field, "after": kind_schema(kind), **common}, required))
    return out


def _options(options):
    return {"enum": [*options, None]} if options else {"type": "null"}


def stage_schema(stage, registry, mechanisms=True):
    """`mechanisms` False (the ablation modes without decision processes): no judgments, and
    proposals rest on their evidence alone. Properties every judgment / proposal shares live in
    `$defs` (evidence, mechanisms, operator, grade, reason) and are referenced, so the per-process
    and per-address branches stay small; compact_schema() is the short form for prompts."""
    _check_stage(stage)
    allowed = stage_mechanisms(stage, registry) if mechanisms else []
    defs = {"evidence": IDS, "reason": TEXT, "grade": {"type": ["integer", "null"], "minimum": 1, "maximum": 4},
            "mechanisms": {"type": "array", **({} if mechanisms else {"maxItems": 0}), "items": {"enum": allowed}},
            "operator": {"enum": stage_operators(stage, registry)}}
    ref = {name: {"$ref": f"#/$defs/{name}"} for name in defs}
    # One branch per decision process, so `option` can only be one of that process's own
    # registered options (or null); structured decoding then cannot invent an option.
    branches = [_object({"dm_id": {"const": dm}, "option": _options(registry.mechanism(dm).options),
                         "output": FLAT, "evidence": ref["evidence"]}) for dm in allowed]
    judgment = {"anyOf": branches} if branches else _object({
        "dm_id": {"enum": allowed}, "option": TEXT_OR_NULL, "output": FLAT, "evidence": ref["evidence"]})
    common = {name: ref[name] for name in ("reason", "grade", "evidence", "mechanisms", "operator")}
    properties = {
        "product": PRODUCTS[stage],
        "judgments": {"type": "array", **({} if mechanisms else {"maxItems": 0}), "items": judgment},
        # One branch per construct address (slots.SLOTS): field names, value kinds and the number
        # of key parts come from the state layout, so decoding cannot invent an address.
        "proposals": {"type": "array", "items": {"anyOf": [
            branch for cid in stage_constructs(stage, registry) for branch in proposal_branches(cid, common)]}},
    }
    if stage == "S1":
        properties["evidence_notes"] = {"type": "array", "items": _object({
            "event": {"type": "string", "minLength": 1}, "channel": {"enum": list(CHANNEL_RANK)}, "claim": TEXT_OR_NULL})}
        properties["cues"] = CUES_SCHEMA
    return {**_object(properties), "$defs": defs}


def keys_as_lists(schema):
    """A copy of a stage schema for decoding: each proposal key of a construct with N key levels
    is a list of exactly N strings instead of one "a/b" string (a regex would be needed otherwise).
    join_keys() turns a reply back."""
    import copy
    out = copy.deepcopy(schema)
    for branch in out["properties"]["proposals"]["items"].get("anyOf", []):
        key = branch["properties"]["key"]
        parts = key.pop("x-key-parts", None)
        if parts:
            branch["properties"]["key"] = {"type": "array", "minItems": parts, "maxItems": parts,
                                           "items": {"type": "string", "minLength": 1}}
    return out


def join_keys(output):
    """A reply decoded with keys_as_lists(): list keys back to "a/b" strings."""
    for proposal in output.get("proposals", []):
        if isinstance(proposal.get("key"), list):
            proposal["key"] = "/".join(proposal["key"])
    return output


def compact_schema(schema):
    """The short form of a stage schema for the prompt: one generic judgment and one generic
    proposal (the ids, options and fields allowed are listed in the stage task) with `$defs`
    inlined. The full schema still drives decoding and checking."""
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
    props = out["properties"]
    for name, tag in (("judgments", "dm_id"), ("proposals", "construct_id")):
        items = props[name]["items"]
        branches = items.get("anyOf")
        if not branches:
            continue
        first = branches[0]["properties"]
        generic = {k: v for k, v in first.items()}
        generic[tag] = {"enum": list(dict.fromkeys(b["properties"][tag]["const"] for b in branches))}
        if name == "judgments":
            generic["option"] = TEXT_OR_NULL
        else:
            key = ({"type": ["array", "null"], "items": {"type": "string"}} if any(
                b["properties"]["key"].get("type") == "array" for b in branches) else TEXT_OR_NULL)
            generic.update(key=key, field=TEXT_OR_NULL, after={})
        props[name]["items"] = _object(generic, branches[0]["required"])
    return inline(out)


def _id_list(ids):
    return {"type": "array", "items": {"enum": list(ids)}} if ids else {"type": "array", "maxItems": 0}


def narrow_evidence(schema, record_ids, audience_ids=None):
    """A copy of a stage schema whose evidence fields (judgments / proposals `evidence`, S1
    evidence_notes `event`) can only take `record_ids`, the records this call shows, and whose S2
    expression audience can only take `audience_ids` (the other characters present) when given.
    Used for structured decoding and for checking the reply; the evidence gate still checks
    visibility and the stage still checks the audience."""
    import copy
    out = copy.deepcopy(schema)
    expression = out.get("properties", {}).get("product", {}).get("properties", {}).get("expression")
    if expression and audience_ids is not None:
        expression["properties"]["audience"] = _id_list(list(dict.fromkeys(audience_ids)))
    ids = list(dict.fromkeys(record_ids))
    props = out.get("properties", {})
    if "evidence" in out.get("$defs", {}):
        out["$defs"]["evidence"] = _id_list(ids)
    for name in ("judgments", "proposals"):
        items = props.get(name, {}).get("items", {})
        for branch in items.get("anyOf", [items]):
            item = branch.get("properties")
            if item and "evidence" in item and "$ref" not in item["evidence"]:
                item["evidence"] = _id_list(ids)
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
        listed.append({"dm_id": dm_id, "name": m.name, "options": list(m.options),
                       "hands_to": [op for op in m.hands_to if op in operators]})
    constructs = []
    for cid in stage_constructs(stage, registry):
        c, slot = registry.construct(cid), SLOTS[cid]
        constructs.append({"construct_id": cid, "name": c.name,
                           "operators": [op for op in operators if cid in registry.operator(op).writes],
                           "keys": list(slot.keys), "fields": dict(slot.fields), "tempo": c.tempo,
                           "banded": {f: scale for f, scale in slot.banded.items()}})
    return {"stage": stage, "operators": operators, "mechanisms": listed, "constructs": constructs,
            "grades": {str(g): text for g, text in GRADES.items()}, "bands": band_guide()}


def band_guide():
    """Typical |change| per event grade for each tempo class (状态变化规则 §5.0), as guidance:
    a banded field's scale multiplies it; personality and evidence quality may move it by one
    band; the program checks the final value."""
    from ..state.magnitude import FAST, GRADE_LEVEL, SLOW
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
            tuple(Hypothesis(h["statement"], h["confidence"], tuple(h["support"]), tuple(h["counter"]), h["discriminator"])
                  for h in d["hypotheses"]),
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
    # A proposal citing a process this stage did not judge is not a broken output: the store
    # rejects that change (unjudged_mechanism) with its dependency group, and the stage goes on.
    proposals = []
    for p in output["proposals"]:
        proposals.append(ChangeProposal(p["construct_id"], p["key"], p["field"], p["after"], p["reason"], p["grade"],
                                        cite(p["evidence"]), tuple(p["mechanisms"]), p.get("operator")))
    try:
        product = _product(stage, output["product"])
    except InterfaceError as exc:
        raise StageError(f"{stage}: {exc}") from None
    return StageOutput(product, tuple(proposals), tuple(judgments), evidence, dict(output.get("cues") or {}))


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
