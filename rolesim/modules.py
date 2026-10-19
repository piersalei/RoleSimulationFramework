"""Module status registry for dynamic state (psychology reply, section 2.3).

Every dynamic module carries a status. Only `active` modules may be computed or written.
`unknown` holds null until a registered evidence rule fills it; `not_modeled` and
`disabled` modules take no part in scoring. Real 0 / neutral values and empty lists are
valid observations and are never a substitute for any of these statuses.
"""
import copy

STATUSES = {"active", "unknown", "disabled", "not_modeled", "inactive", "not_applicable"}
# Statuses that keep no value in the state tree (a slot, if present, must be null).
NO_VALUE = STATUSES - {"active"}
ORIGINS = {"default", "profile", "observed"}


MISSING = object()

# Registered stable modules (persona schema 2.0). A persona must carry a status entry for
# exactly these paths; adding one is a schema change.
STABLE_MODULES = (
    "stable.identity.demographics", "stable.identity.social_roles", "stable.identity.resources",
    "stable.identity.life_history",
    "stable.traits.personality", "stable.traits.attachment", "stable.traits.motivation_baseline",
    "stable.traits.affective_capacities", "stable.traits.social_cognition_71d",
    "stable.traits.cognitive_style", "stable.traits.developmental_position",
    "stable.traits.personality_function",
    "stable.values.schwartz_priorities", "stable.values.investment", "stable.values.moral_reasoning",
    "stable.values.cultural_orientation",
    "stable.core_goals",
    "stable.speech_style.baseline", "stable.speech_style.situational_rules",
    "stable.mechanism_priors",
)


def entry(status, reason, source=None, origin=None, version="state-v1"):
    if status not in STATUSES:
        raise ValueError(f"unknown module status: {status}")
    return {"status": status, "reason": reason, "source": list(source or []),
            "origin": origin, "version": version}


def default_module_status(partner_ids=()):
    """Status table for a fresh state; value slots exist only for unknown/active modules."""
    u, nm = "unknown", "not_modeled"
    no_rule = "no evidence-fill rule registered yet"
    table = {
        "dynamic.affect_state.emotion.label": entry(u, no_rule),
        "dynamic.affect_state.emotion.valence": entry(u, no_rule),
        "dynamic.affect_state.emotion.arousal": entry(u, no_rule),
        "dynamic.affect_state.emotion.intensity": entry(u, no_rule),
        "dynamic.affect_state.emotion.duration_hint": entry(u, no_rule),
        "dynamic.affect_state.emotion.confidence": entry(u, no_rule),
        "dynamic.affect_state.emotion.dominance": entry(nm, "only for power/fear/anger scenarios"),
        "dynamic.affect_state.stress": entry(nm, "not in v1 first cut"),
        "dynamic.affect_state.threat_reward": entry(nm, "not in v1 first cut"),
        "dynamic.affect_state.regulation": entry(nm, "regulation is a decision mechanism; no state yet"),
        "dynamic.cognition_state.beliefs": entry("active", "structure enabled; empty is a valid observation"),
        "dynamic.cognition_state.other_models": entry("active", "structure enabled; empty is a valid observation"),
        "dynamic.cognition_state.tom": entry(u, "tom_level needs profile and runtime constraints"),
        "dynamic.cognition_state.attention": entry(nm, "not in v1 first cut"),
        "dynamic.cognition_state.attribution": entry(nm, "not in v1 first cut"),
        "dynamic.cognition_state.self_state": entry(nm, "not in v1 first cut"),
        "dynamic.goal_state.goals_now": entry("active", "structure enabled; empty is a valid observation"),
        "dynamic.goal_state.value_activation": entry(nm, "not in v1 first cut"),
        "dynamic.goal_state.conflicts": entry(nm, "not in v1 first cut"),
        "dynamic.group_state": entry(nm, "no group scenario yet"),
        "dynamic.policy_state.commitments": entry(nm, "not in v1 first cut"),
        "dynamic.policy_state.strategy": entry(nm, "not in v1 first cut"),
    }
    for partner in partner_ids:
        table.update(relation_entries(partner))
    return table


def relation_entries(partner):
    base = f"dynamic.relation_state.{partner}"
    u, nm = "unknown", "not_modeled"
    return {
        f"{base}.trust": entry(u, "no initial value supplied"),
        f"{base}.closeness": entry(u, "no initial value supplied"),
        f"{base}.dependence": entry(u, "no initial value supplied"),
        f"{base}.commitment": entry(u, "no initial value supplied"),
        f"{base}.harm_history": entry("active", "no recorded harm; empty list is a valid observation"),
        # Objective power direction is system-side data and must not enter AgentState;
        # only the role's own perception may be stored here once a scenario needs it.
        f"{base}.power_asymmetry": entry("not_applicable", "no power difference declared by the scenario"),
        f"{base}.reputation": entry(nm, "planned for v1.1"),
        f"{base}.repair_state": entry(nm, "planned for v1.1"),
    }


def initial_slots(partner_ids=()):
    """Value tree matching `default_module_status`: null for unknown, containers for active."""
    return {
        "affect_state": {"emotion": {k: None for k in
                                     ("label", "valence", "arousal", "intensity", "duration_hint", "confidence")}},
        "cognition_state": {"beliefs": {}, "other_models": {}, "tom": None},
        "goal_state": {"goals_now": []},
        "relation_state": {p: {"trust": None, "closeness": None, "dependence": None,
                               "commitment": None, "harm_history": {"events": []}} for p in partner_ids},
        "group_state": {},
        "policy_state": {},
    }


def lookup(tree, parts):
    for part in parts:
        if isinstance(tree, dict) and part in tree:
            tree = tree[part]
        else:
            return MISSING
    return tree


def ungoverned(node, path, status):
    """First leaf path under `node` that no registered module governs, else None."""
    if path in status:
        return None
    if isinstance(node, dict):
        for key, child in node.items():
            found = ungoverned(child, f"{path}.{key}", status)
            if found:
                return found
        return None
    return path


def validate_status(status):
    if not isinstance(status, dict):
        raise ValueError("module_status must be an object")
    for path, item in status.items():
        if not isinstance(item, dict) or item.get("status") not in STATUSES:
            raise ValueError(f"{path}: unsupported module status")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError(f"{path}: module status requires a reason")
        if not isinstance(item.get("source", []), list):
            raise ValueError(f"{path}: source must be a list")
        if item.get("origin") not in {None, *ORIGINS}:
            raise ValueError(f"{path}: unsupported origin")


def check_consistency(root, tree, status):
    """Status and value must agree: active holds a value, unknown holds null, the rest hold none."""
    validate_status(status)
    for path, item in status.items():
        value = lookup({root: tree}, path.split("."))
        if item["status"] == "active":
            if value is MISSING or value is None:
                raise ValueError(f"{path}: active module requires a value; use unknown for missing evidence")
        elif item["status"] == "unknown":
            if value is MISSING or value is not None:
                raise ValueError(f"{path}: unknown module must hold null")
        elif value not in (MISSING, None, {}, []):
            raise ValueError(f"{path}: {item['status']} module may not hold a value")
    stray = ungoverned(tree, root, status)
    if stray:
        raise ValueError(f"{stray}: value is not governed by a registered module")


def governing(status, path):
    """Longest registered prefix of `path` (a dotted string); None if nothing governs it."""
    parts = path.split(".")
    for end in range(len(parts), 0, -1):
        key = ".".join(parts[:end])
        if key in status:
            return key
    return None


def transition(status, log, path, to, basis, *, source=None, validated=False):
    """Change a module's status and leave an audit record; refuse silent activation."""
    if path not in status:
        raise ValueError(f"unregistered module: {path}")
    if to not in STATUSES:
        raise ValueError(f"unknown module status: {to}")
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError("status transition requires a basis")
    old = status[path]["status"]
    if old == to:
        raise ValueError("module already has that status")
    if to == "active":
        if old == "unknown" and not source:
            raise ValueError("unknown -> active requires the evidence it was filled from")
        if old in {"disabled", "not_modeled"} and not validated:
            raise ValueError("re-enabling requires completed definition, initialisation and rule checks")
    record = {"path": path, "from": old, "to": to, "basis": basis,
              "source": list(source or []), "validated": bool(validated)}
    status[path] = {**copy.deepcopy(status[path]), "status": to, "reason": basis, "source": list(source or [])}
    log.append(record)
    return record
