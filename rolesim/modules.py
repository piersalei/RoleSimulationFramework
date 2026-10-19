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
