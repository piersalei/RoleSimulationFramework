"""A transitional IRoleRepository (plan/10 §2) over the hand-written role files.

Until the profile compiler (plan/10 step 6) exists, the new pipeline needs a stable profile and
an entry state from what we already have:

  load(role_ref)     the role file's persona (schema 2.0) -> StableProfile, for the modules
                     that map one-to-one onto registered constructs (LEGACY) and are active in
                     the persona; everything else stays out (unknown). snapshot_id is the digest
                     of the persona's stable part, as in the old Agent.
  enter(profile, bootstrap, config)
                     a blank state (slots.blank_state) plus what the bootstrap gives: core_goals
                     text -> one current goal, history_memory -> one entry memory (角色状态说明
                     §8.2). Relations start when a partner first matters (unknown until then).
  background(bootstrap, persona)
                     the scene and self texts the role is told at initialize, for prompts.

Not done here (step 6): source kinds per field, review items, natural-language profiles,
conditioned phrases for what the role cannot be aware of, the 4.10 prior mapping.
"""
from .catalog import digest
from .interfaces import AgentState, EnableConfig, InterfaceError, StableModule, StableProfile
from .slots import blank_state, put

# (persona path under "stable", persona module_status key, construct id, how to read the value)
LEGACY = (
    ("traits.personality", "stable.traits.personality", "s.traits.personality.p1_big_five",
     lambda v: v.get("facets") if isinstance(v, dict) and v.get("scheme") == "P1" else None),
    ("traits.attachment", "stable.traits.attachment", "s.traits.attachment.global", lambda v: v),
    ("values.schwartz_priorities", "stable.values.schwartz_priorities", "s.values.schwartz_priorities", lambda v: v),
    ("core_goals", "stable.core_goals", "s.core_goals.primary", lambda v: v),
    ("speech_style", "stable.speech_style.baseline", "s.speech_style.baseline", lambda v: v),
    ("identity.social_roles", "stable.identity.social_roles", "s.identity.social_roles", lambda v: v),
)
ENTRY_GOAL, ENTRY_MEMORY = "g-entry", "m-entry"


def _at(tree, path):
    for part in path.split("."):
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


def background(bootstrap, persona=None):
    setting = bootstrap["setting"]
    out = {"姓名": bootstrap["name"], "人物概况": bootstrap["profile_summary"]}
    if persona and persona.get("self_view"):
        out["自我认识"] = persona["self_view"]
    for key, value in (("地点", setting.get("place")), ("时间", setting.get("time_text")),
                       ("触发事实", list(bootstrap["trigger_facts"])), ("总体目标", bootstrap["core_goals"]),
                       ("关系", list(bootstrap["relationships"])), ("此前经历", bootstrap["history_memory"])):
        if value:
            out[key] = value
    return out


class RoleFileRepository:
    """IRoleRepository over loaded role files (roles.load_role), keyed by role id."""

    def __init__(self, roles, registry):
        self.roles = {r["id"]: r for r in roles}
        self.registry = registry

    def load(self, role_ref):
        role = self.roles.get(role_ref)
        if role is None:
            raise InterfaceError(f"no role {role_ref!r} in the role library")
        stable = role["persona"]["stable"]
        status = stable.get("module_status", {})
        modules = {}
        for path, status_key, cid, read in LEGACY:
            value = _at(stable, path)
            if value is None or status.get(status_key, {}).get("status") != "active":
                continue
            value = read(value)
            if value is not None:
                self.registry.construct(cid)               # must be registered
                modules[cid] = StableModule(cid, value, "active", "explicit", None)
        return StableProfile(digest(stable), role_ref, modules)

    def enter(self, profile, bootstrap, config=None):
        state = blank_state(self.registry, profile.role_ref, profile.snapshot_id, config or EnableConfig())
        dynamic, memory, status = dict(state.dynamic), dict(state.memory), dict(state.status)
        if bootstrap.get("core_goals") and status.get("d.goal.goals_now") != "disabled":
            put(dynamic, "d.goal.goals_now", ENTRY_GOAL, None,
                {"goal_id": ENTRY_GOAL, "content": bootstrap["core_goals"], "priority": 0.5, "stage": "进行中",
                 "core_goal": None, "source": "场景", "reason": "入场时给出的总体目标；优先级取中性默认值"})
            status["d.goal.goals_now"] = "active"
        if bootstrap.get("history_memory") and status.get("m.entry") != "disabled":
            put(memory, "m.entry", ENTRY_MEMORY, None,
                {"entry_id": ENTRY_MEMORY, "summary": bootstrap["history_memory"][:400], "source_events": [], "time": None,
                 "involved": [], "channel": "入场前", "importance": 0.5, "valence": 0.0, "interpretations": []})
            put(memory, "m.accessibility", ENTRY_MEMORY, None, 0.5)
            status["m.entry"] = status["m.accessibility"] = "active"
        return AgentState(state.state_id, None, state.stable_snapshot_id, dynamic, memory, state.versions, status)
