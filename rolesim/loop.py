"""Local reference runner for the agent interface v2 (Agent接口说明_环境组_2026-11-07.md).

The real runner belongs to the environment group. This one follows the same rules so the
role adapter can be run and tested offline, and so both sides have a worked example:

    start: initialize every character once (interaction_id + character_id + bootstrap);
    each turn: environment directives (if any) -> env.act -> records; then the actor's act
    -> records. The runner keeps every character's observations; nobody is called to
    perceive: a character reads what it saw when it is next asked to act.

Episode inputs come from the existing role and scene files: character ids are role-library ids
(the role file ids, fixed in advance and the same in every interaction), the bootstrap carries
the ablation mode in `mechanism.mode`, and the scene's environment
rules become director directives (clock announcements and scheduled world events, with their
audience). `EchoEnvironment` stands in for the environment group's IEnvironment: it plays
each directive back as an objective action. Scene `world_private` is never read.

As agreed with the environment group, `present_characters` given to an actor contains everyone
present, the actor included, so `recipient_ids=None` reaches the actor too.
"""
import asyncio
import copy
import datetime
import json
from pathlib import Path

from .adapter import RoleAgentAdapter
from .agent_api import (ENV_ID, ENV_NAME, MODE, split, validate_action_result, validate_bootstrap,
                        validate_environment_content, validate_environment_view, validate_turn_input,
                        visible)
from .catalog import digest, write_json
from .mechanisms import CARDS
from .roles import fit_cast, origin
from .runtime import WorldAgent, code_version, environment_agent, role_seats
from .state import VERSIONS

INTERFACE = "agent-api-v2"
TRANSCRIPT = "transcript.md"
KIND_TEXT = {"thought": "想", "speech": "说", "action": "做"}


class EchoEnvironment:
    """Stand-in IEnvironment: the directive itself happens, as an action. `routes` maps a
    directive to the names that receive it (default: everyone present)."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})

    async def act(self, interaction_id, context, request_id):
        validate_environment_view(context)
        names = self.routes.get(context["environment_directive"])
        ids = None if names is None else [cid for cid, e in context["present_characters"].items() if e["name"] in names]
        return {"thought": None, "speech": None, "order": ["action"],
                "action": {"content": context["environment_directive"], "recipient_ids": ids}}


def episode(roles, scene):
    """Episode inputs from loaded role files and a loaded scene (scene schema 3.0)."""
    fitted = fit_cast(roles, scene)
    if not fitted["ok"]:
        raise ValueError(f"cast does not fit scene {scene['id']}: " + "; ".join(fitted["errors"]))
    scenario = scene["scenario"]
    seats = role_seats(scenario)
    if ENV_ID in {r["id"] for r in roles}:
        raise ValueError(f"role id {ENV_ID!r} is reserved for the environment")
    names = [r["persona"]["name"] for r in roles]
    spec = environment_agent(scenario)
    world = WorldAgent(spec, {s["id"]: r["id"] for s, r in zip(seats, roles)}) if spec else None
    cast = [{"character_id": r["id"], "seat_id": s["id"], "name": n, "role_id": r["id"], "title": s["title"],
             "profile_summary": r["persona"]["public_description"]} for s, n, r in zip(seats, names, roles)]
    present = "、".join(names[:-1]) + "与" + names[-1]
    return {"scene_id": scenario["id"], "background": scenario["background"], "cast": cast, "world": world,
            "max_turns": scenario["max_turns"], "allowed_actions": list(scenario["allowed_actions"]),
            "opening": f"{present}开始就这件事当面沟通。"}


def _setting(world):
    if world is None:
        return {"place": None, "time_text": None}
    return {"place": world.world.get("place"), "time_text": world.world.get("time")}


def bootstrap(episode, member, setting, mode=None):
    relationships = [f"{m['name']}是{m['title']}。" for m in episode["cast"] if m is not member]
    return {"setting": dict(setting), "trigger_facts": [episode["background"]], "name": member["name"],
            "profile_summary": member["profile_summary"], "core_goals": None,
            "relationships": [f"我是{member['title']}。", *relationships], "history_memory": None,
            "personality": {}, "mechanism": {} if mode is None else {MODE: mode}}


class LocalRunner:
    def __init__(self, episode, agents, environment, on_call=None, interaction_id=None, mode=None):
        """`on_call(entry)` receives every call with its full input and output, for the run record."""
        self.episode, self.agents, self.environment = episode, agents, environment
        self.interaction_id = interaction_id or f"local-{episode['scene_id']}"
        self.mode = mode
        self.on_call = on_call or (lambda entry: None)
        self.setting = _setting(episode["world"])
        self.ids = [m["character_id"] for m in episode["cast"]]
        self.present = {m["character_id"]: {"name": m["name"]} for m in episode["cast"]}
        self.observations = {cid: [] for cid in self.ids}
        self.history = []          # every record, in occurrence order
        self.requests = 0
        self.closed_by = None

    def _request_id(self, call, who):
        self.requests += 1
        return f"r{self.requests:04d}:{call}:{who}"

    def _record(self, call, request_id, who, data, result):
        self.on_call({"call": call, "request_id": request_id, "who": who, "input": copy.deepcopy(data),
                      "output": copy.deepcopy(result)})

    def _publish(self, records):
        self.history += records
        for cid in self.ids:
            self.observations[cid] += visible(records, cid)

    async def initialize(self):
        for member in self.episode["cast"]:
            data = bootstrap(self.episode, member, self.setting, self.mode)
            validate_bootstrap(data)
            await self.agents.initialize(self.interaction_id, member["character_id"], copy.deepcopy(data))
            self._record("initialize", None, member["character_id"], data, None)

    async def _environment(self, turn, order, index, directive, recipients):
        view = {"setting": dict(self.setting), "story_background": self.episode["background"],
                "trigger_facts": [self.episode["background"]], "present_characters": copy.deepcopy(self.present),
                "environment_directive": directive}
        if recipients is not None and isinstance(self.environment, EchoEnvironment):
            self.environment.routes[directive] = recipients
        request_id = self._request_id("environment", ENV_ID)
        content = await self.environment.act(self.interaction_id, copy.deepcopy(view), request_id)
        validate_environment_content(content, view)
        self._record("environment", request_id, ENV_ID, view, content)
        records = split(content, ENV_ID, ENV_NAME, view["present_characters"], f"t{turn:03d}-env{index}", turn, order)
        self._publish(records)
        return order + len(records)

    def _directives(self, turn, actor_index):
        """(directive, recipient names or None) at the start of this turn."""
        if turn == 1:
            return [(self.episode["opening"], None)]
        world = self.episode["world"]
        if world is None or actor_index != 0:
            return []
        cast = [m["role_id"] for m in self.episode["cast"]]
        names = {m["role_id"]: m["name"] for m in self.episode["cast"]}
        out = []
        for _, text, audience, _cues in world.tick((turn - 1) // len(cast) + 1, turn, cast):
            out.append((text, None if set(audience) == set(cast) else [names[r] for r in audience]))
        self.setting = _setting(world)
        return out

    async def run(self):
        await self.initialize()
        cast = self.episode["cast"]
        turn = 0
        for turn in range(1, self.episode["max_turns"] + 1):
            index = (turn - 1) % len(cast)
            order = 0
            for k, (directive, recipients) in enumerate(self._directives(turn, index)):
                order = await self._environment(turn, order, k + 1, directive, recipients)
            member = cast[index]
            cid = member["character_id"]
            context = {"bootstrap": None, "observations": copy.deepcopy(self.observations[cid]),
                       "present_characters": copy.deepcopy(self.present)}
            validate_turn_input(context, cid)
            request_id = self._request_id("act", cid)
            result = await self.agents.act(self.interaction_id, cid, copy.deepcopy(context), request_id)
            validate_action_result(result, context, cid)
            self._record("act", request_id, cid, context, result)
            records = split(result, cid, member["name"], context["present_characters"],
                            f"t{turn:03d}-{cid}", turn, order)
            self._publish(records)
            closed = getattr(self.agents, "closed_by", None)
            if closed and closed(self.interaction_id):
                self.closed_by = closed(self.interaction_id)
                break
        return turn


# -- run directory -------------------------------------------------------------------
def _jsonl(path, items):
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def write_transcript(output, episode, history, manifest):
    lines = [f"# {episode['scene_id']}（{INTERFACE}）", "",
             f"- 状态：{manifest['status']}；轮数：{manifest.get('turns')}；后端：{manifest['backend']}；模式：{manifest['mode']}",
             "- 证据层级：工程运行，只说明管线可用，不说明心理机制有效。", "", "## 互动记录", ""]
    current = None
    for r in history:
        if r["turn_index"] != current:
            current = r["turn_index"]
            lines += ["", f"### 第 {current} 轮", ""]
        who = r["actor"]["name"]
        ids = set(r["recipients"])
        to = "仅本人" if ids == {r["actor"]["actor_id"]} else "、".join(e["name"] for e in r["recipients"].values())
        lines.append(f"- **{who}**{KIND_TEXT[r['kind']]}（给：{to}）：{r['content']}")
    Path(output, TRANSCRIPT).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_agent_api(roles, scene, backend, output, mode="profile_state_mechanisms", use_skills=True,
                  appraise_state=True):
    """Run a scene through the agent interface v2 with the local runner and the echo environment."""
    ep = episode(roles, scene)
    adapter = RoleAgentAdapter(roles, backend, mode, use_skills, appraise_state, ep["allowed_actions"])
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    calls = []
    runner = LocalRunner(ep, adapter, EchoEnvironment(), calls.append, mode=mode)
    manifest = {"schema_version": "agent-api-run-1.0", "interface": INTERFACE, "status": "running",
                "started_at": _now(), "backend": backend.label, "model": backend.model, "sampling": backend.sampling,
                "mode": mode, "skills_enabled": bool(use_skills), "appraise_state": bool(appraise_state),
                "versions": dict(VERSIONS), "code_version": code_version(), "scene_id": ep["scene_id"],
                "scenario_hash": digest(scene["scenario"]), "mechanism_hash": digest(CARDS),
                "cast": [{k: m[k] for k in ("character_id", "name", "role_id", "title")} for m in ep["cast"]],
                "environment": "EchoEnvironment (local stand-in)", "origin": origin(roles, scene),
                "evidence_boundary": "engineering run; no human-validity or method-effectiveness claim"}
    write_json(output / "manifest.json", manifest)
    try:
        turns = asyncio.run(runner.run())
        manifest.update(status="completed", turns=turns, closed_by=runner.closed_by)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
        raise
    finally:
        manifest.update(finished_at=_now(), api_calls=backend.calls, model_served=sorted(backend.served_models),
                        records=len(runner.history), calls=len(calls),
                        interaction_id=runner.interaction_id,
                roles={s.role_id: {"name": s.name, "character_id": s.character_id, "moves": s.moves,
                                   "warnings": s.warnings,
                                   "final_state_id": s.agent.state_id if s.agent else None,
                                   "visibility_warnings": s.agent.warnings if s.agent else []}
                       for s in adapter.sessions.values()})
        _jsonl(output / "records.jsonl", runner.history)
        _jsonl(output / "calls.jsonl", calls)
        for member in ep["cast"]:
            folder = output / "roles" / member["role_id"]
            folder.mkdir(parents=True)
            entries = [e for e in adapter.log if e["role_id"] == member["role_id"]]
            _jsonl(folder / "audit.jsonl", entries)
            _jsonl(folder / "states.jsonl", [e["state_after"] for e in entries])
            session = adapter.sessions.get((runner.interaction_id, member["character_id"]))
            agent = session.agent if session else None
            _jsonl(folder / "mechanism_log.jsonl", agent.mechanism_log if agent else [])
        write_json(output / "manifest.json", manifest)
        write_transcript(output, ep, runner.history, manifest)
