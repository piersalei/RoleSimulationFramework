"""Dynamic state (state-v1): six domains, update operators and staged atomic commits.

Structure and processes follow the psychology group's reply (plan/01-roadmap.md, section 1).
The reply fixes structure, operator list and error policy but not rule-card numbers; the
numeric parameters below are engineering placeholders, not fitted values.
"""
import copy
import json
import math

from .modules import (check_consistency, default_module_status, governing, initial_slots,
                      relation_entries, transition)

SCHEMA_VERSION = "state-v1"
PSYCHOLOGY_VERSION = "psy-v1"
# Four version numbers recorded with every trajectory (reply, section 4.4). The construct
# registry and the evaluation protocol are not frozen yet, so they say so explicitly.
VERSIONS = {"construct_definition_version": "unregistered",
            "state_schema_version": SCHEMA_VERSION,
            "update_rule_version": "engineering-v2",
            "evaluation_protocol_version": "unfrozen"}

DOMAINS = ("affect_state", "cognition_state", "goal_state", "relation_state", "group_state", "policy_state")

# The eleven update operators and the dynamic paths each may write. "*" matches one
# path segment (a partner id). An empty list means the operator writes no dynamic path.
OPERATORS = {
    "emotion_update": ["dynamic.affect_state"],
    "belief_revision": ["dynamic.cognition_state.beliefs"],
    "other_model_update": ["dynamic.cognition_state.other_models"],
    "self_state_update": ["dynamic.cognition_state.self_state"],
    "relation_update": ["dynamic.relation_state"],
    "group_state_update": ["dynamic.group_state"],
    "goal_update": ["dynamic.goal_state"],
    "commitment_update": ["dynamic.policy_state.commitments"],
    "strategy_learning": ["dynamic.policy_state.strategy"],
    "memory_update": [],   # memory entries live in AgentState.memory, not under dynamic
    "decay_or_recovery": ["dynamic.affect_state.emotion", "dynamic.relation_state.*.harm_history",
                          "dynamic.policy_state.strategy"],
}

# Closed ranges by leaf field name; None means "unknown" and is allowed only where the
# module status says so.
RANGES = {"valence": (-1, 1), "arousal": (0, 1), "intensity": (0, 1), "confidence": (0, 1),
          "priority": (0, 1), "trust": (0, 1), "closeness": (0, 1), "dependence": (0, 1),
          "commitment": (0, 1), "severity": (0, 1)}

# Soft rules: violations are written and flagged. `exceptionable` is True only when a
# rule card explicitly lets a major event waive the boundary; no card does yet.
SOFT_RULES = {"trust_step": {"field": "trust", "limit": .3, "exceptionable": False}}

CHANGE_FIELDS = {"path", "before", "after", "evidence", "mechanism", "reason"}
# card / rule / when / inputs attribute a change to the mechanism card that produced it.
OPTIONAL_FIELDS = {"flag", "group", "exception", "card", "rule", "when", "inputs"}
EXCEPTION_FIELDS = {"soft_rule", "evidence", "approved_by", "review"}
HARM_FIELDS = {"event_id", "severity", "acknowledged", "repaired", "last_activated"}


# -- construction ------------------------------------------------------------
def new_dynamic(partner_ids=()):
    """Return (dynamic, module_status) with every module unknown/not_modeled as registered."""
    return initial_slots(partner_ids), default_module_status(partner_ids)


def add_partner(dynamic, status, partner):
    if partner in dynamic["relation_state"]:
        raise ValueError(f"partner already present: {partner}")
    dynamic["relation_state"][partner] = initial_slots([partner])["relation_state"][partner]
    status.update(relation_entries(partner))


def activate(dynamic, status, log, path, value, basis, source, origin="default"):
    """Fill an unknown module with a value and mark it active in one audited step."""
    keys = _split(path)
    transition(status, log, path, "active", basis, source=source)
    status[path]["origin"] = origin
    _set(dynamic, keys, copy.deepcopy(value))
    validate_dynamic(dynamic, status)


# -- validation --------------------------------------------------------------
def _check_ranges(value, name):
    if isinstance(value, dict):
        for key, item in value.items():
            _check_ranges(item, key)
    elif isinstance(value, list):
        for item in value:
            _check_ranges(item, name)
    elif name in RANGES and value is not None:
        low, high = RANGES[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        if not low <= value <= high:
            raise ValueError(f"{name} out of range [{low}, {high}]; values are rejected, not clipped")


def _require_source(item, where):
    source = item.get("source") if isinstance(item, dict) else None
    if not isinstance(source, list) or not source or not all(isinstance(s, str) and s for s in source):
        raise ValueError(f"{where} requires a non-empty source list")


def validate_dynamic(dynamic, status):
    if not isinstance(dynamic, dict) or tuple(dynamic) != DOMAINS:
        raise ValueError("dynamic: unexpected or missing domain")
    check_consistency("dynamic", dynamic, status)
    goals = dynamic["goal_state"].get("goals_now")
    if goals is not None and not isinstance(goals, list):
        raise ValueError("dynamic.goal_state.goals_now must be a list")
    for block in ("beliefs", "other_models"):
        items = dynamic["cognition_state"][block]
        if not isinstance(items, dict):
            raise ValueError(f"dynamic.cognition_state.{block} must be an object")
        for key, item in items.items():
            _require_source(item, f"{block}.{key}")
    for partner, relation in dynamic["relation_state"].items():
        events = relation["harm_history"]["events"]
        if not isinstance(events, list):
            raise ValueError("harm_history.events must be a list")
        for event in events:
            if not isinstance(event, dict) or set(event) != HARM_FIELDS:
                raise ValueError("harm_history event: unexpected or missing field")
            if not all(isinstance(event[k], bool) for k in ("acknowledged", "repaired")):
                raise ValueError("harm_history event: acknowledged/repaired must be booleans")
    _check_ranges(dynamic, "dynamic")


# -- paths -------------------------------------------------------------------
def _split(path):
    if not isinstance(path, str) or not path:
        raise ValueError("change path must be a non-empty string")
    parts = path.split(".")
    if parts[0] == "stable":
        raise ValueError("stable fields are read-only within an episode")
    if parts[0] != "dynamic" or len(parts) < 3 or not all(parts):
        raise ValueError("change path must address dynamic.<domain>.<module>[...]")
    return parts[1:]


def _get(tree, keys):
    for key in keys:
        if isinstance(tree, list):
            if not key.isdigit() or int(key) >= len(tree):
                return None
            tree = tree[int(key)]
        elif isinstance(tree, dict):
            if key not in tree:
                return None
            tree = tree[key]
        else:
            return None
    return copy.deepcopy(tree)


def _set(tree, keys, value):
    for key in keys[:-1]:
        tree = tree[int(key)] if isinstance(tree, list) else tree.setdefault(key, {})
    if isinstance(tree, list):
        tree[int(keys[-1])] = value
    else:
        tree[keys[-1]] = value


def _matches(pattern, path):
    want, have = pattern.split("."), path.split(".")
    return len(have) >= len(want) and all(w in ("*", h) for w, h in zip(want, have))


def may_write(operator, path):
    return any(_matches(pattern, path) for pattern in OPERATORS[operator])


# -- single change -----------------------------------------------------------
def _soft_rule(path, before, after):
    for rule_id, rule in SOFT_RULES.items():
        if path.split(".")[-1] == rule["field"] and isinstance(before, (int, float)) \
                and isinstance(after, (int, float)) and abs(after - before) > rule["limit"] + 1e-9:
            return rule_id
    return None


def check_change(change, visible_ids, dynamic, status):
    """Apply the hard rules to one change; return its soft flag (or None).

    Raises ValueError on any hard error: structure, evidence, permission, module status,
    range, stale `before`, or an exception request the rule card does not allow.
    """
    if not isinstance(change, dict) or not CHANGE_FIELDS <= set(change) <= CHANGE_FIELDS | OPTIONAL_FIELDS:
        raise ValueError("change: unexpected or missing field")
    path = change["path"]
    keys = _split(path)
    evidence = change["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("change requires evidence")
    if any(not isinstance(e, str) or e not in visible_ids for e in evidence):
        raise ValueError("change cites evidence this role has not seen")
    operator = change["mechanism"]
    if operator not in OPERATORS:
        raise ValueError("unregistered mechanism")
    if not may_write(operator, path):
        raise ValueError(f"{operator} may not write {path}")
    module = governing(status, path)
    if module is None:
        raise ValueError(f"{path}: no registered module governs this path")
    if status[module]["status"] != "active":
        raise ValueError(f"{module} is {status[module]['status']}; only active modules can be written")
    if not isinstance(change["reason"], str) or not change["reason"].strip():
        raise ValueError("change requires a reason")
    _check_ranges(change["after"], keys[-1])
    if _get(dynamic, keys) != change["before"]:
        raise ValueError(f"stale before value for {path}")
    rule_id = _soft_rule(path, change["before"], change["after"])
    exception = change.get("exception")
    if exception is not None:
        if not isinstance(exception, dict) or set(exception) != EXCEPTION_FIELDS:
            raise ValueError("exception: unexpected or missing field")
        if rule_id is None or exception["soft_rule"] != rule_id:
            raise ValueError("exception does not match a soft-rule violation")
        if not SOFT_RULES[rule_id]["exceptionable"]:
            raise ValueError(f"rule card does not allow waiving {rule_id}")
        if not isinstance(exception["evidence"], list) or not exception["evidence"] \
                or any(e not in visible_ids for e in exception["evidence"]):
            raise ValueError("exception requires visible evidence")
        if not all(isinstance(exception[k], str) and exception[k] for k in ("approved_by", "review")):
            raise ValueError("exception requires approved_by and review")
        return "exception_approved"
    return "soft_violation" if rule_id else None


# -- staged commit -----------------------------------------------------------
def _signature(change):
    return (change["mechanism"], tuple(sorted(change["evidence"])),
            json.dumps(change["after"], ensure_ascii=False, sort_keys=True))


def commit_phase(dynamic, status, delta, visible_ids, commit_key=None, committed=None):
    """Validate candidate changes against one shared snapshot and commit them by group.

    Changes with the same `group` form a dependency group and commit atomically; a change
    with no group joins "default". A hard error, or a same-path conflict with no
    registered aggregation rule, rejects the whole group; other groups still commit.
    Returns (new_dynamic, adopted_changes, log). A repeated `commit_key` is a no-op.
    """
    if commit_key is not None and committed is not None and commit_key in committed:
        return copy.deepcopy(dynamic), [], {**copy.deepcopy(committed[commit_key]), "replayed": True}
    if not isinstance(delta, dict) or not isinstance(delta.get("changes"), list):
        raise ValueError("delta requires a changes list")
    changes, visible = delta["changes"], set(visible_ids)
    snapshot = copy.deepcopy(dynamic)
    groups = {}
    for index, change in enumerate(changes):
        group = change.get("group", "default") if isinstance(change, dict) else "default"
        if not isinstance(group, str) or not group:
            raise ValueError("group must be a non-empty string")
        groups.setdefault(group, []).append(index)

    rejected, flags = {}, {}
    for group, indices in groups.items():
        for index in indices:
            try:
                flags[index] = check_change(changes[index], visible, snapshot, status)
            except (ValueError, TypeError, KeyError) as exc:
                path = changes[index].get("path") if isinstance(changes[index], dict) else None
                rejected.setdefault(group, f"{path}: {exc}" if path else str(exc))

    by_path = {}
    for group, indices in groups.items():
        if group not in rejected:
            for index in indices:
                by_path.setdefault(changes[index]["path"], []).append(index)
    dropped = set()
    for path, indices in by_path.items():
        if len(indices) < 2:
            continue
        if len({_signature(changes[i]) for i in indices}) == 1:
            dropped.update(indices[1:])
            continue
        for i in indices:
            rejected.setdefault(changes[i].get("group", "default"),
                                f"{path}: unresolvable same-path conflict; no aggregation rule registered")

    def apply(base, indices):
        result = copy.deepcopy(base)
        for index in indices:
            if index not in dropped:
                _set(result, _split(changes[index]["path"]), copy.deepcopy(changes[index]["after"]))
        validate_dynamic(result, status)
        return result

    for group, indices in groups.items():
        if group not in rejected:
            try:
                apply(snapshot, indices)
            except ValueError as exc:
                rejected[group] = str(exc)
    accepted = [i for g, indices in groups.items() if g not in rejected for i in indices]
    result = apply(snapshot, accepted)
    adopted = [changes[i] for i in accepted if i not in dropped]
    for i in accepted:
        if i not in dropped:
            changes[i]["flag"] = flags[i]
    log = {"commit_key": commit_key, "trigger": delta.get("trigger"), "event_id": delta.get("event_id"),
           "versions": dict(VERSIONS), "input_state_id": None, "output_state_id": None,
           "candidates": [{"index": i, "group": c.get("group", "default") if isinstance(c, dict) else None,
                           "path": c.get("path") if isinstance(c, dict) else None,
                           "mechanism": c.get("mechanism") if isinstance(c, dict) else None,
                           "card": c.get("card") if isinstance(c, dict) else None,
                           "when": c.get("when") if isinstance(c, dict) else None}
                          for i, c in enumerate(changes)],
           "adopted": [{"group": g, "paths": [changes[i]["path"] for i in indices if i not in dropped]}
                       for g, indices in groups.items() if g not in rejected],
           "rejected": [{"group": g, "kind": "hard", "reason": reason} for g, reason in rejected.items()],
           "duplicates_dropped": [changes[i]["path"] for i in sorted(dropped)],
           "flags": [{"path": changes[i]["path"], "flag": flags[i]} for i in accepted if flags[i]]}
    if commit_key is not None and committed is not None:
        committed[commit_key] = copy.deepcopy(log)
    return result, adopted, log
