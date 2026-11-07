"""Where each dynamic / memory construct lives in AgentState, and what its values may be.

The code form of plan/09 §3.4-3.5 (field names) over 角色状态说明v1 §5-6 (value forms).
`SLOTS[construct_id]` gives:
  keys         the key levels below the construct, e.g. ("partner",) for d.relation.trust
  fields       field -> kind for one instance; "" means the instance itself is a value;
               "*" stands for any one segment (a map's own keys)
  banded       numeric fields that take a magnitude band (the construct's tempo from the
               registry), with the band scale (valence x2, self-evaluation x4: 状态变化规则 §5.0)
  append_only  existing instances are never removed (facts and history are kept)
  fixed        fields that may not change once written (the facts of a harm or a memory)
  not_modeled  fields that may not be written in this version

Kinds: u 0..1 · v -1..1 · z -2..2 · int (>= 0) · grade 1..4 · text · bool · refs (list of ids)
· list · map · labels (Plutchik families + 轻蔑) · opt (a registered option of the decision
process named by the first key segment) · enum(a|b|...). A trailing "?" allows null.

Addresses are (construct_id, key, field): `key` has one "/"-separated segment per key level,
`field` is a "."-path inside the instance, and None means the whole instance.

Which field names and enum values exist is an engineering reading of the sources (plan/09);
open items are listed there (§7, F1). Ranges come from 角色状态说明 §3.1 / §5.
"""
import copy
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from .interfaces import AgentState, EnableConfig, InterfaceError

STATE_SCHEMA_VERSION = "construct-state-1"
EMOTION_FAMILIES = ("喜悦", "信任", "恐惧", "惊讶", "悲伤", "厌恶", "愤怒", "期待", "轻蔑")
RANGES = {"u": (0.0, 1.0), "v": (-1.0, 1.0), "z": (-2.0, 2.0)}


class AddressError(InterfaceError):
    """An address does not fit the construct's layout."""


@dataclass(frozen=True)
class Slot:
    keys: tuple[str, ...] = ()
    fields: Mapping[str, str] = field(default_factory=dict)
    banded: Mapping[str, float] = field(default_factory=dict)
    append_only: bool = False
    fixed: frozenset = frozenset()
    not_modeled: frozenset = frozenset()
    basis: str = ""


def _slot(fields, keys=(), banded=None, append_only=False, fixed=(), not_modeled=(), basis=""):
    return Slot(tuple(keys), MappingProxyType(dict(fields)), MappingProxyType(dict(banded or {})),
                append_only, frozenset(fixed), frozenset(not_modeled), basis)


_STATE = "角色状态说明v1"
SLOTS = MappingProxyType({
    # -- affect_state ----------------------------------------------------------------------
    "d.affect.emotion": _slot(
        {"label": "labels", "valence": "v", "arousal": "u", "intensity": "u", "duration_hint": "text?",
         "confidence": "u", "attribution": "text?", "norm_evaluation": "text?", "object": "text?"},
        banded={"valence": 2.0, "arousal": 1.0, "intensity": 1.0}, not_modeled=("dominance",),
        basis=f"{_STATE} §5.1：label、valence、arousal、intensity、duration_hint、confidence；dominance 默认 not_modeled"),
    "d.affect.stress": _slot({"level": "u"}, banded={"level": 1.0}, basis=f"{_STATE} §5.1：压力水平为 0—1"),
    "d.affect.threat_reward_activation": _slot(
        {"bas": "u", "bis": "u", "fffs": "u"}, banded={"bas": 1.0, "bis": 1.0, "fffs": 1.0},
        basis=f"{_STATE} §5.1：分别保存 BAS、BIS、FFFS 当前的激活程度（0—1 为工程读法）"),
    "d.affect.regulation_state": _slot({"items": "list"}, basis=f"{_STATE} §5.1：逐条保存；没有正在维持的调节时为 inactive"),
    # -- cognition_state -------------------------------------------------------------------
    "d.cognition.belief": _slot(
        {"belief_id": "text", "proposition": "text", "kind": "enum(fact|interpretation)", "about": "refs",
         "context": "text?", "time": "text?", "confidence": "u?", "conflict_group": "text?", "adopted": "bool",
         "isolated": "bool", "superseded_by": "text?", "evidence": "list"},
        keys=("belief_id",), banded={"confidence": 1.0}, append_only=True, fixed=("belief_id",),
        basis=f"{_STATE} §5.2：逐条保存，未形成判断时 confidence 为 unknown；冲突信念并存，不覆盖历史"),
    "d.cognition.attention": _slot({"focus": "list", "neglect": "list"}, basis=f"{_STATE} §5.2：焦点条目与忽略条目"),
    "d.cognition.attribution": _slot(
        {"event": "text", "about": "text", "locus": "enum(internal|external)?", "stability": "enum(stable|temporary)?",
         "globality": "enum(global|specific)?", "controllability": "enum(controllable|uncontrollable)?",
         "intent": "text?", "responsibility": "map", "confidence": "u"},
        keys=("attribution_id",), basis=f"{_STATE} §5.2：针对具体事件和对象逐条保存，附确信度"),
    "d.cognition.self_evaluation": _slot(
        {"global": "z", "domains.*": "z"}, banded={"global": 4.0, "domains.*": 4.0},
        basis=f"{_STATE} §5.2：与自尊基线使用同一刻度（-2—+2），分总体和领域"),
    "d.cognition.task_self_efficacy": _slot({"level": "u", "efficacy_domain": "text?"}, keys=("task",),
                                            basis=f"{_STATE} §5.2：按具体任务保存"),
    "d.cognition.shame": _slot({"load": "u"}, banded={"load": 1.0}, basis=f"{_STATE} §5.2：羞耻负荷为 0—1"),
    "d.cognition.face_cost": _slot({"damage": "u", "maintenance_need": "u", "audience": "refs"},
                                   banded={"damage": 1.0, "maintenance_need": 1.0},
                                   basis=f"{_STATE} §5.2：形象受损程度、维护面子的需要强度和在场观众"),
    "d.cognition.self_responsibility": _slot(
        {"event": "text", "degree": "u", "type": "enum(factual|moral|relational|institutional)", "mechanism": "text?"},
        keys=("entry_id",), basis=f"{_STATE} §5.2：按事件保存责任程度与类型"),
    "d.cognition.other_model": _slot(
        {"inference": "text", "kind": "enum(goal|belief|intention|emotion|ability|constraint)", "order": "int",
         "confidence": "u?", "evidence": "list", "hypothesis_ref": "text?", "superseded_by": "text?"},
        keys=("partner", "inference_id"), append_only=True,
        basis=f"{_STATE} §5.2：按对象分别保存推断，阶数不得超过对该对象的可用层级"),
    "d.cognition.tom_level": _slot(
        {"ceiling": "int", "current": "int", "per_object.*": "int", "allowed_tasks": "list",
         "downgrade_reason": "text?"},
        basis=f"{_STATE} §5.2：能力上限、当前可用层级、针对特定对象的层级和允许的推理任务；上限在轨迹内不变"),
    # -- goal_state ------------------------------------------------------------------------
    "d.goal.goals_now": _slot(
        {"goal_id": "text", "content": "text", "priority": "u", "stage": "enum(进行中|完成|受阻|降级|暂停|放弃)",
         "core_goal": "text?", "source": "enum(核心目标|情绪|习惯|规范|关系|安全|场景|其他)", "reason": "text"},
        keys=("goal_id",), append_only=True, fixed=("goal_id",), basis=f"{_STATE} §5.3：完成或放弃的目标保留，不删除"),
    "d.goal.value_activation": _slot({"": "u"}, keys=("value",), banded={"": 1.0},
                                     basis=f"{_STATE} §5.3：按施瓦兹价值保存当前激活程度"),
    "d.goal.goal_activation": _slot({"": "u"}, keys=("core_goal",), banded={"": 1.0},
                                    basis=f"{_STATE} §5.3：按核心目标保存当前激活程度"),
    "d.goal.goal_commitment": _slot({"": "u"}, keys=("goal",), banded={"": 1.0},
                                    basis=f"{_STATE} §5.3：可针对核心目标或当前目标分别保存"),
    "d.goal.feasibility_estimate": _slot({"": "u?"}, keys=("goal",), banded={"": 1.0},
                                         basis=f"{_STATE} §5.3：可行性尚未评估时为 unknown"),
    "d.goal.threat_to_goal": _slot({"": "u"}, keys=("goal",), basis=f"{_STATE} §5.3"),
    "d.goal.goal_conflict": _slot(
        {"involved": "refs", "order": "refs", "non_negotiable": "text", "acceptable_compensation": "text",
         "resolution": "text?"}, keys=("conflict_id",), basis=f"{_STATE} §5.3：逐条保存涉入的目标或价值、优先顺序…"),
    "d.goal.action_orientation": _slot({"direction": "text", "short_term_goal": "text?", "toward": "refs"},
                                       basis=f"{_STATE} §5.3：推进方向、短期优先的当前目标和指向对象"),
    "d.goal.revision_candidate": _slot(
        {"core_goal": "text", "proposal": "enum(降级|替换|合并|分裂)", "content": "text", "evidence": "refs",
         "independent_evidence": "int", "window_start": "text?", "self_commitment": "bool",
         "review": "enum(pending|approved|rejected)"},
        keys=("candidate_id",), append_only=True, basis=f"{_STATE} §5.3：修订候选不改变 core_goals，属累积型"),
    # -- relation_state (本角色 -> 对象) --------------------------------------------------
    "d.relation.trust": _slot({"value": "u", "evidence_by_basis": "map"}, keys=("partner",), banded={"value": 1.0},
                              basis=f"{_STATE} §5.4：trust、closeness、dependence、commitment 四项均为 0—1，彼此独立"),
    "d.relation.closeness": _slot({"value": "u"}, keys=("partner",), banded={"value": 1.0}, basis=f"{_STATE} §5.4"),
    "d.relation.dependence": _slot({"value": "u"}, keys=("partner",), banded={"value": 1.0}, basis=f"{_STATE} §5.4"),
    "d.relation.commitment": _slot({"value": "u"}, keys=("partner",), banded={"value": 1.0}, basis=f"{_STATE} §5.4"),
    "d.relation.harm_history": _slot(
        {"harm_id": "text", "event": "text", "description": "text", "severity": "grade",
         "acknowledged": "enum(是|部分|否|不明)", "repair": "enum(无|部分|完全)", "last_activated": "text?",
         "salience": "u", "memory": "text?"},
        keys=("partner", "harm_id"), append_only=True, fixed=("harm_id", "event", "description", "severity"),
        basis=f"{_STATE} §5.4：伤害事件只能追加，或更新承认、修复、激活和显著性，不得删除"),
    "d.relation.power_asymmetry": _slot(
        {"objective": "map", "subjective.direction": "enum(self|other|none)",
         "subjective.bases": "list", "subjective.confidence": "u", "note": "text?"},
        keys=("partner",), basis=f"{_STATE} §5.4：客观部分来自场景输入，relation_update 只写主观部分"),
    # -- group_state -------------------------------------------------------------------------
    "d.group.membership": _slot({"degree": "u", "position": "text?", "status": "enum(成员|边缘|候选|被排斥|曾属)"},
                                keys=("group",), basis=f"{_STATE} §5.5"),
    "d.group.identity_salience": _slot({"": "u"}, keys=("identity",), banded={"": 1.0}, basis=f"{_STATE} §5.5"),
    "d.group.norm_salience": _slot({"type": "enum(描述性|命令性)", "source": "text?", "salience": "u"}, keys=("norm",),
                                   banded={"salience": 1.0}, basis=f"{_STATE} §5.5"),
    "d.group.alliance": _slot({"allies": "refs", "opponents": "refs", "stability": "u", "basis": "text?"},
                              basis=f"{_STATE} §5.5"),
    "d.group.boundary": _slot({"us": "refs", "them": "refs", "clarity": "u"}, keys=("group",), basis=f"{_STATE} §5.5"),
    "d.group.responsibility_allocation": _slot({"self": "u", "others.*": "u"}, keys=("situation",),
                                               basis=f"{_STATE} §5.5：份额是主观判断，不要求总和为 1"),
    # -- policy_state ----------------------------------------------------------------------
    "d.policy.action_commitment": _slot(
        {"commitment_id": "text", "content": "text", "to": "text", "formed_at": "text", "deadline": "text?",
         "stage": "enum(形成|维持|兑现|违约|暂停|撤销)", "public": "bool", "means": "text?", "verification": "text?"},
        keys=("commitment_id",), append_only=True, fixed=("commitment_id", "formed_at"),
        basis=f"{_STATE} §5.6：兑现、违约或撤销的承诺保留，不删除（plan/09 的“延误”“重新协商”待确认，未收）"),
    "d.policy.current_strategy": _slot({"": "opt"}, keys=("dm", "context"),
                                       basis=f"{_STATE} §5.6：策略取值为登记表第七部分该过程的可选项"),
    "d.policy.fallback_strategy": _slot({"": "opt"}, keys=("dm", "context"), basis=f"{_STATE} §5.6"),
    "d.policy.switch_condition": _slot({"signal": "text", "threshold": "text?", "then": "opt"}, keys=("dm", "context"),
                                       basis=f"{_STATE} §5.6：切换条件须写成可观察的信号"),
    "d.policy.strategy_weight": _slot({"*": "u"}, keys=("dm",), banded={"*": 1.0},
                                      basis=f"{_STATE} §5.6：按决策过程保存各选项的相对权重"),
    "d.policy.learning_state": _slot({"recent_feedback": "refs", "exploration": "u", "cumulative": "text?"},
                                     keys=("dm",), basis=f"{_STATE} §5.6"),
    "d.policy.mechanism_preference_state": _slot({"option": "text", "strength": "u", "caused_by": "text",
                                                  "scope": "text?"}, keys=("entry_id",), basis=f"{_STATE} §5.6"),
    # -- memory ------------------------------------------------------------------------------
    "m.entry": _slot(
        {"entry_id": "text", "summary": "text", "source_events": "refs", "time": "text?", "involved": "refs",
         "channel": "enum(亲见|听说|推断|入场前)", "importance": "u", "valence": "v", "interpretations": "list"},
        keys=("entry_id",), append_only=True, fixed=("entry_id", "summary", "source_events", "time", "involved", "channel"),
        basis=f"{_STATE} §六：不可见的事件不能形成记忆；条目只追加（plan/09：原条目与后来的解释分开）"),
    "m.accessibility": _slot({"": "u"}, keys=("entry_id",), basis=f"{_STATE} §六"),
    "m.reactivation": _slot({"": "list"}, keys=("entry_id",), append_only=True,
                            basis=f"{_STATE} §六：每次再激活的事件和线索"),
})

# Where the special rules in validator.py read their facts.
OPTION_SLOTS = {"d.policy.current_strategy": "", "d.policy.fallback_strategy": "", "d.policy.switch_condition": "then",
                "d.policy.strategy_weight": "*"}


# -- addressing ----------------------------------------------------------------------------
def layer_of(construct_id):
    return "memory" if construct_id.startswith("m.") else "dynamic"


def _match(spec, parts):
    names = spec.split(".")
    return len(names) == len(parts) and all(n in ("*", p) for n, p in zip(names, parts))


def kind_of(slot, field_name):
    """The kind of one field path, "record" for a prefix of dotted specs, None if undeclared."""
    if field_name is None:
        return slot.fields.get("", "record")
    parts = field_name.split(".")
    for spec, kind in slot.fields.items():
        if spec and _match(spec, parts):
            return kind
    if any(spec.split(".")[:len(parts)] == parts and len(spec.split(".")) > len(parts) for spec in slot.fields):
        return "record"
    return None


def band_scale(slot, field_name):
    if field_name is None:
        return slot.banded.get("")
    parts = field_name.split(".")
    for spec, scale in slot.banded.items():
        if spec and _match(spec, parts):
            return scale
    return None


def resolve(construct_id, key, field_name):
    """(slot, key parts, field parts) or AddressError."""
    slot = SLOTS.get(construct_id)
    if slot is None:
        raise AddressError(f"{construct_id}: no state slot")
    parts = [] if key is None else key.split("/")
    if len(parts) != len(slot.keys) or any(not p for p in parts):
        raise AddressError(f"{construct_id}: key {key!r} must have {len(slot.keys)} part(s) {slot.keys}")
    if field_name is not None:
        if slot.fields.get("") is not None:
            raise AddressError(f"{construct_id}: instances are plain values, no field {field_name!r}")
        if field_name.split(".")[0] in slot.not_modeled:
            return slot, parts, field_name.split(".")
        if kind_of(slot, field_name) is None:
            raise AddressError(f"{construct_id}: undeclared field {field_name!r}")
    return slot, parts, [] if field_name is None else field_name.split(".")


def _container(state, construct_id):
    return (state.memory if layer_of(construct_id) == "memory" else state.dynamic).get(construct_id)


def get(state, construct_id, key, field_name):
    """Current value at an address; None when nothing is there yet."""
    _, key_parts, field_parts = resolve(construct_id, key, field_name)
    node = _container(state, construct_id)
    for part in key_parts + field_parts:
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return copy.deepcopy(node)


def put(tree, construct_id, key, field_name, value):
    """Set a value in a plain {construct_id: value} tree, creating the instance if needed."""
    _, key_parts, field_parts = resolve(construct_id, key, field_name)
    path = [construct_id] + key_parts + field_parts
    node = tree
    for part in path[:-1]:
        node = node.setdefault(part, {})
    node[path[-1]] = copy.deepcopy(value)


def instance_after(state, construct_id, key, field_name, value):
    """The whole instance as it would be after writing `value` at the address."""
    if field_name is None:
        return copy.deepcopy(value)
    current = get(state, construct_id, key, None)
    instance = dict(current) if isinstance(current, Mapping) else {}
    node = instance
    parts = field_name.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = copy.deepcopy(value)
    return instance


# -- value checks --------------------------------------------------------------------------
def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_kind(kind, value, options=None):
    """None if `value` fits `kind`, else (code, detail). `options` serves kind "opt"."""
    if kind.endswith("?"):
        if value is None:
            return None
        kind = kind[:-1]
    if kind in RANGES:
        low, high = RANGES[kind]
        if not _number(value):
            return "bad_value", f"expected a number, got {value!r}"
        return None if low <= value <= high else ("out_of_range", f"{value} outside [{low}, {high}]")
    if kind == "int":
        return None if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else ("bad_value", f"expected int >= 0, got {value!r}")
    if kind == "grade":
        ok = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4
        return None if ok else ("out_of_range", f"grade must be 1-4, got {value!r}")
    if kind == "text":
        return None if isinstance(value, str) else ("bad_value", f"expected text, got {value!r}")
    if kind == "bool":
        return None if isinstance(value, bool) else ("bad_value", f"expected bool, got {value!r}")
    if kind == "refs":
        ok = isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)
        return None if ok else ("bad_value", f"expected a list of ids, got {value!r}")
    if kind == "list":
        return None if isinstance(value, (list, tuple)) else ("bad_value", f"expected a list, got {value!r}")
    if kind == "map":
        return None if isinstance(value, Mapping) else ("bad_value", f"expected a map, got {value!r}")
    if kind == "labels":
        ok = isinstance(value, (list, tuple)) and all(v in EMOTION_FAMILIES for v in value)
        return None if ok else ("bad_value", f"labels must come from {EMOTION_FAMILIES}, got {value!r}")
    if kind == "opt":
        if options is None:
            return "unregistered_mechanism", "no registered decision process for this option"
        return None if value in options else ("unregistered_option", f"{value!r} is not an option of the process")
    if kind.startswith("enum(") and kind.endswith(")"):
        allowed = kind[5:-1].split("|")
        return None if value in allowed else ("bad_value", f"{value!r} not in {allowed}")
    raise ValueError(f"unknown kind {kind!r}")


def check_instance(slot, value, options=None, prefix=""):
    """Check a whole instance (or a record-valued field) against the declared fields."""
    if slot.fields.get("") is not None and not prefix:
        return check_kind(slot.fields[""], value, options)
    if not isinstance(value, Mapping):
        return "bad_value", f"expected a record, got {value!r}"
    for name, item in value.items():
        path = f"{prefix}{name}"
        if path.split(".")[0] in slot.not_modeled:
            return "not_modeled_field", f"{path} is not modelled in this version"
        kind = kind_of(slot, path)
        if kind is None:
            return "bad_field", f"undeclared field {path!r}"
        problem = check_instance(slot, item, options, f"{path}.") if kind == "record" else check_kind(kind, item, options)
        if problem:
            return problem
    return None


# -- entry ---------------------------------------------------------------------------------
def versions(registry):
    sha = {s["file"].rsplit("/", 1)[-1]: s["sha256"][:12] for s in registry.sources}
    return {"construct_definition_version": sha["心理构念登记v1.md"], "update_rule_version": sha["状态变化规则v1.md"],
            "state_schema_version": STATE_SCHEMA_VERSION, "evaluation_protocol_version": "unregistered"}


def blank_state(registry, character_id, stable_snapshot_id, config=None):
    """Every dynamic and memory construct present and empty: status unknown, or disabled when
    the enable configuration turns it off. IRoleRepository.enter fills it from there."""
    config = config or EnableConfig()
    dynamic, memory, status = {}, {}, {}
    for cid in registry.ids("construct"):
        construct = registry.construct(cid)
        if construct.layer == "stable":
            continue
        (memory if construct.layer == "memory" else dynamic)[cid] = {}
        status[cid] = "unknown" if config.enabled(cid) else "disabled"
    return AgentState(f"{character_id}@entry", None, stable_snapshot_id, dynamic, memory, versions(registry), status)


def check_layout(registry):
    """SLOTS must cover exactly the registered dynamic and memory constructs."""
    registered = {cid for cid in registry.ids("construct") if registry.construct(cid).layer != "stable"}
    missing, extra = registered - set(SLOTS), set(SLOTS) - registered
    if missing or extra:
        raise ValueError(f"state slots out of step with the registry: missing {sorted(missing)}, extra {sorted(extra)}")
