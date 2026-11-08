"""IRoleRepository (plan/10 §2): where a role comes from, and the state it enters with.

load(role_ref)   profiles/<role_ref>.json (profile-1) when it exists, else the role file of that
                 id converted (profile.profile_from_persona), compiled by IProfileCompiler. The same
                 id gives the same StableProfile object; the review items stay in `review[role_ref]`.
enter(profile, bootstrap, config)
                 the entry state of 角色状态说明v1 §8.2, in this order:
                   1. the profile's explicit entry state ("entry", checked against the layout)
                   2. the opening setup: goal -> the current goal g-setup; recap items -> entry
                      memories (same ids); then the bootstrap: core_goals -> a current goal;
                      history_memory -> an entry memory
                   3. defaults written there: emotion intensity 0 and label empty, valence /
                      arousal from the emotion baseline; stress and threat / reward activation
                      without extra load; no regulation going on (inactive); shame and face cost 0;
                      self-evaluation at the self-esteem baseline; theory-of-mind ceiling from the
                      two 71-dimension items with the age limits, current = ceiling (to review);
                      value / goal activation, goal threat, goal conflict, revision candidates none;
                      goal commitment from the core goals' priority baselines; memories from the
                      life history
                 Anything else stays unknown. Where each value came from is kept in
                 `entry_notes[role_ref]`.

Not done (needs the scene or later steps): power asymmetry and group membership from the scene's
structure, strategies seeded from decision preferences on a process's first use, levels 3 / 4 of
the theory-of-mind ceiling (they need evidence about a particular partner).
"""
import json
from pathlib import Path
from typing import Mapping

from .interfaces import AgentState, EnableConfig, InterfaceError
from .profile import ProfileCompiler, profile_from_role
from .slots import SLOTS, blank_state, check_instance, put

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "profiles"
ENTRY_GOAL, ENTRY_MEMORY, SETUP_GOAL = "g-entry", "m-entry", "g-setup"
TOM_RULE = "角色状态说明 §8.2：1.1.4 为 -2 → 0 阶；1.5.2 ≤ -1 → 1 阶；否则 2 阶（两项未赋值取 default 0）；约 4 岁以下 ≤ 0 阶，4—6 岁 ≤ 1 阶"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _value(profile, cid):
    module = profile.get(cid) if profile is not None else None
    return module.value if module is not None and module.status == "active" else None


def tom_ceiling(profile):
    """(ceiling, note) from social-cognition items 1.1.4 / 1.5.2 and the age, if given."""
    items = _value(profile, "s.traits.social_cognition_71d") or {}
    first, higher = items.get("1.1.4", 0), items.get("1.5.2", 0)
    ceiling = 0 if first <= -2 else 1 if higher <= -1 else 2
    demographics = _value(profile, "s.identity.demographics")
    age = demographics.get("age") if isinstance(demographics, Mapping) else None
    if _number(age):
        ceiling = min(ceiling, 0) if age < 4 else min(ceiling, 1) if age < 7 else ceiling
    given = "未赋值，取 default 0" if "1.1.4" not in items and "1.5.2" not in items else f"1.1.4={first}, 1.5.2={higher}"
    return ceiling, f"{given}；{TOM_RULE}；入场推断，需审核"


def background(bootstrap, profile_raw=None):
    """What the role was told at initialize (and how it sees itself), for prompts."""
    setting = bootstrap["setting"]
    out = {"姓名": bootstrap["name"], "人物概况": bootstrap["profile_summary"]}
    if profile_raw and profile_raw.get("self_view"):
        out["自我认识"] = profile_raw["self_view"]
    for key, value in (("地点", setting.get("place")), ("时间", setting.get("time_text")),
                       ("触发事实", list(bootstrap["trigger_facts"])), ("总体目标", bootstrap["core_goals"]),
                       ("关系", list(bootstrap["relationships"])), ("此前经历", bootstrap["history_memory"])):
        if value:
            out[key] = value
    return out


class ProfileRepository:
    """IRoleRepository over profile files and, failing those, the hand-written role files."""

    def __init__(self, registry, compiler=None, roles=(), root=PROFILES_DIR, config=None):
        self.registry = registry
        self.compiler = compiler or ProfileCompiler(registry)
        self.roles = {r["id"]: r for r in roles}
        self.root = Path(root)
        self.config = config or EnableConfig()
        self._raw, self._profiles, self._library = {}, {}, None
        self.review, self.entry_notes = {}, {}

    def raw(self, role_ref):
        if role_ref not in self._raw:
            path = self.root / f"{role_ref}.json"
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("id") != role_ref:
                    raise InterfaceError(f"{path}: id {data.get('id')!r} does not match the file name")
            elif role_ref in self.roles:
                data = profile_from_role(self.roles[role_ref])
            else:
                raise InterfaceError(f"no profile and no role file for {role_ref!r}")
            self._raw[role_ref] = data
        return self._raw[role_ref]

    def load(self, role_ref):
        if role_ref not in self._profiles:
            profile, review = self.compiler.compile(self.raw(role_ref), self.config)
            self._profiles[role_ref], self.review[role_ref] = profile, review
        return self._profiles[role_ref]

    def background(self, role_ref, bootstrap):
        return background(bootstrap, self.raw(role_ref))

    def setup(self, role_ref):
        """The role's opening setup (briefing.py), or None."""
        return self.raw(role_ref).get("setup")

    def skills(self, role_ref):
        """The role's bound persona skills, by priority (skills.py); [] when it binds none."""
        items = self.raw(role_ref).get("skills") or []
        if not items:
            return []
        from .skills import load_library
        if self._library is None:
            self._library = load_library()
        unknown = [i["name"] for i in items if i["name"] not in self._library]
        if unknown:
            raise InterfaceError(f"{role_ref}: unknown skills {unknown}")
        bound = [{**self._library[i["name"]], "priority": i["priority"]} for i in items]
        return sorted(bound, key=lambda s: (-s["priority"], s["name"]))

    def enter(self, profile, bootstrap, config=None):
        config = config or self.config
        state = blank_state(self.registry, profile.role_ref, profile.snapshot_id, config)
        dynamic, memory, status = dict(state.dynamic), dict(state.memory), dict(state.status)
        notes = []

        def tree(cid):
            return memory if cid.startswith("m.") else dynamic

        def writable(cid):
            return status.get(cid) not in ("disabled", "not_modeled", "not_applicable")

        explicit = (self._raw.get(profile.role_ref) or {}).get("entry") or {}
        for cid, value in explicit.items():
            if cid not in SLOTS:
                raise InterfaceError(f"profile.entry: {cid} is not a dynamic or memory construct")
            slot = SLOTS[cid]
            if slot.keys and not isinstance(value, Mapping):
                raise InterfaceError(f"profile.entry.{cid}: expected a map keyed by {slot.keys}")
            instances = _instances(slot, value) if slot.keys else [value]
            problem = next((p for p in (check_instance(slot, v) for v in instances) if p), None)
            if problem:
                raise InterfaceError(f"profile.entry.{cid}: {problem[1]}")
            if writable(cid):
                tree(cid)[cid] = value
                status[cid] = "active"
                notes.append(f"{cid}: 档案入场状态（明写）")

        def default(cid, value, why, mark="active"):
            if cid in explicit or not writable(cid):
                return
            tree(cid)[cid] = value
            status[cid] = mark
            notes.append(f"{cid}: {why}")

        if bootstrap.get("core_goals") and "d.goal.goals_now" not in explicit and writable("d.goal.goals_now"):
            put(dynamic, "d.goal.goals_now", ENTRY_GOAL, None,
                {"goal_id": ENTRY_GOAL, "content": bootstrap["core_goals"], "priority": 0.5, "stage": "进行中",
                 "core_goal": None, "source": "场景", "reason": "入场时给出的总体目标；优先级取中性默认值"})
            status["d.goal.goals_now"] = "active"
            notes.append("d.goal.goals_now: bootstrap 的 core_goals（优先级为中性默认值 0.5）")
        setup = (self._raw.get(profile.role_ref) or {}).get("setup")
        if setup and "d.goal.goals_now" not in explicit and writable("d.goal.goals_now"):
            put(dynamic, "d.goal.goals_now", SETUP_GOAL, None,
                {"goal_id": SETUP_GOAL, "content": setup["goal"], "priority": 0.5, "stage": "进行中", "core_goal": None,
                 "source": "场景", "reason": "开场设定的互动目标；优先级取中性默认值"})
            status["d.goal.goals_now"] = "active"
            notes.append("d.goal.goals_now: 开场设定的互动目标（优先级为中性默认值 0.5）")
        memories = [(item["id"], item["text"][:400], 0.5) for item in (setup or {}).get("recap", [])]
        life = _value(profile, "s.identity.life_history")
        for i, item in enumerate(life if isinstance(life, list) else [], 1):
            text = item if isinstance(item, str) else next((item.get(k) for k in ("event", "text", "description", "summary")
                                                            if isinstance(item, Mapping) and item.get(k)), None)
            if text:
                importance = item.get("importance") if isinstance(item, Mapping) and _number(item.get("importance")) else 0.5
                memories.append((f"m-life-{i}", str(text)[:400], min(max(importance, 0.0), 1.0)))
        if bootstrap.get("history_memory"):
            memories.append((ENTRY_MEMORY, bootstrap["history_memory"][:400], 0.5))
        if memories and "m.entry" not in explicit and writable("m.entry"):
            for entry_id, text, importance in memories:
                put(memory, "m.entry", entry_id, None,
                    {"entry_id": entry_id, "summary": text, "source_events": [], "time": None, "involved": [],
                     "channel": "入场前", "importance": importance, "valence": 0.0, "interpretations": []})
                put(memory, "m.accessibility", entry_id, None, importance)
            status["m.entry"] = status["m.accessibility"] = "active"
            notes.append("m.entry: 开场设定的前情、生命经历与 bootstrap 的 history_memory 生成入场记忆（可及性取重要性）")

        emotion = {"label": [], "intensity": 0.0}
        baseline = _value(profile, "s.traits.affect.emotion_baseline")
        if isinstance(baseline, Mapping):
            emotion.update({k: baseline[k] for k in ("valence", "arousal") if _number(baseline.get(k))})
        default("d.affect.emotion", emotion, "默认先验：强度为零、标签为空，效价与唤醒取情绪基线（有则取）")
        default("d.affect.stress", {"level": 0.0}, "默认先验：无额外负荷")
        default("d.affect.threat_reward_activation", {"bas": 0.0, "bis": 0.0, "fffs": 0.0}, "默认先验：无额外负荷")
        default("d.affect.regulation_state", {}, "没有正在维持的调节", mark="inactive")
        default("d.cognition.shame", {"load": 0.0}, "默认先验：为零")
        default("d.cognition.face_cost", {"damage": 0.0, "maintenance_need": 0.0, "audience": []}, "默认先验：为零")
        esteem = _value(profile, "s.traits.self.self_esteem")
        level = esteem if _number(esteem) else (esteem.get("level") if isinstance(esteem, Mapping) else None)
        if isinstance(esteem, Mapping) and isinstance(esteem.get("global"), Mapping):
            level = esteem["global"].get("level")
        elif isinstance(esteem, Mapping) and _number(esteem.get("global")):
            level = esteem["global"]
        if _number(level) and -2 <= level <= 2:
            default("d.cognition.self_evaluation", {"global": float(level)}, "取自尊基线")
        ceiling, why = tom_ceiling(profile)
        default("d.cognition.tom_level", {"ceiling": ceiling, "current": ceiling, "per_object": {}, "allowed_tasks": [],
                                          "downgrade_reason": None}, why)
        for cid in ("d.goal.value_activation", "d.goal.goal_activation", "d.goal.threat_to_goal", "d.goal.goal_conflict",
                    "d.goal.revision_candidate"):
            default(cid, {}, "入场时为零 / 没有")
        commitments = {}
        goals = _value(profile, "s.core_goals.primary")
        for goal in goals if isinstance(goals, list) else []:
            if isinstance(goal, Mapping):
                gid = goal.get("goal_id") or goal.get("id")
                priority = goal.get("priority_baseline", goal.get("priority"))
                if gid and _number(priority) and 0 <= priority <= 1:
                    commitments[str(gid)] = float(priority)
        if commitments:
            default("d.goal.goal_commitment", commitments, "以核心目标的优先级基线为起点")
        self.entry_notes[profile.role_ref] = notes
        return AgentState(state.state_id, None, state.stable_snapshot_id, dynamic, memory, state.versions, status)


def _instances(slot, value):
    """The instances of a keyed construct's value ({k1: {k2: instance}})."""
    level = [value]
    for _ in slot.keys:
        level = [v for node in level if isinstance(node, Mapping) for v in node.values()]
    return level
