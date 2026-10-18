"""Dynamic state blocks and StateDelta validation (psychology contract v0, sections 2.2 and 3).

Field names follow the contract draft. Numeric parameters here are engineering
placeholders until the psychology group supplies rules; they are not fitted values.
"""
import copy
import math

SCHEMA_VERSION = "state-v0"
PSYCHOLOGY_VERSION = "psy-v0"

# Which dynamic blocks each state-change type may write (contract 3, hard rule 3).
MECHANISMS = {
    "appraisal": {"emotion", "beliefs"},
    "belief_revision": {"beliefs"},
    "tom_inference": {"other_models"},
    "relation_update": {"relations"},
    "goal_update": {"goals_now"},
    "self_commit": {"goals_now"},
    "decay": {"emotion"},
}

# Closed ranges by leaf field name; None means "not represented yet" and is allowed.
RANGES = {"valence": (-1, 1), "arousal": (0, 1), "intensity": (0, 1), "confidence": (0, 1),
          "priority": (0, 1), "trust": (0, 1), "closeness": (0, 1)}

# Soft limits (contract 3.2): violations are written and flagged, not rejected.
MAX_STEP = {"trust": .3}

CHANGE_FIELDS = {"path", "before", "after", "evidence", "mechanism", "reason"}


def empty_dynamic():
    return {"emotion": {"label": None, "valence": None, "arousal": None, "intensity": 0},
            "beliefs": {}, "goals_now": [], "relations": {}, "other_models": {}}


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


def validate_dynamic(dynamic):
    if not isinstance(dynamic, dict) or set(dynamic) != set(empty_dynamic()):
        raise ValueError("dynamic: unexpected or missing block")
    if not isinstance(dynamic["emotion"], dict) or set(dynamic["emotion"]) != set(empty_dynamic()["emotion"]):
        raise ValueError("dynamic.emotion: unexpected or missing field")
    if not isinstance(dynamic["goals_now"], list):
        raise ValueError("dynamic.goals_now must be a list")
    for block in ("beliefs", "relations", "other_models"):
        if not isinstance(dynamic[block], dict):
            raise ValueError(f"dynamic.{block} must be an object")
    for key, belief in dynamic["beliefs"].items():
        _require_source(belief, f"beliefs.{key}")
    for key, model in dynamic["other_models"].items():
        _require_source(model, f"other_models.{key}")
    _check_ranges(dynamic, "dynamic")


def _split(path):
    if not isinstance(path, str) or not path:
        raise ValueError("change path must be a non-empty string")
    parts = path.split(".")
    if parts[0] == "stable":
        raise ValueError("stable fields are read-only within an episode")
    if parts[0] != "dynamic" or len(parts) < 2 or not all(parts):
        raise ValueError("change path must address dynamic.<block>[...]")
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


def check_change(change, visible_ids):
    """Apply the four hard constraints to one change; return a soft-violation flag or None."""
    if not isinstance(change, dict) or not CHANGE_FIELDS <= set(change) <= CHANGE_FIELDS | {"flag"}:
        raise ValueError("change: unexpected or missing field")
    keys = _split(change["path"])
    evidence = change["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("change requires evidence")
    if any(not isinstance(e, str) or e not in visible_ids for e in evidence):
        raise ValueError("change cites evidence this role has not seen")
    if change["mechanism"] not in MECHANISMS:
        raise ValueError("unregistered mechanism")
    if keys[0] not in MECHANISMS[change["mechanism"]]:
        raise ValueError(f"{change['mechanism']} may not write dynamic.{keys[0]}")
    if not isinstance(change["reason"], str) or not change["reason"].strip():
        raise ValueError("change requires a reason")
    _check_ranges(change["after"], keys[-1])
    before, after = change["before"], change["after"]
    limit = MAX_STEP.get(keys[-1])
    if limit is not None and isinstance(before, (int, float)) and isinstance(after, (int, float)) \
            and abs(after - before) > limit + 1e-9:
        return "soft_violation"
    return None


def apply_delta(dynamic, delta, visible_ids):
    """Validate every change, then return an updated copy; any hard violation rejects the whole delta."""
    if not isinstance(delta, dict) or not isinstance(delta.get("changes"), list):
        raise ValueError("delta requires a changes list")
    visible_ids = set(visible_ids)
    result = copy.deepcopy(dynamic)
    flags = []
    for change in delta["changes"]:
        flags.append(check_change(change, visible_ids))
        keys = _split(change["path"])
        if _get(result, keys) != change["before"]:
            raise ValueError(f"stale before value for {change['path']}")
        _set(result, keys, copy.deepcopy(change["after"]))
    validate_dynamic(result)
    for change, flag in zip(delta["changes"], flags):
        change["flag"] = flag
    return result
