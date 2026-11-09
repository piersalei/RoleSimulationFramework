"""The local reference runner for the agent interface v2 (Agent接口说明_环境组_2026-11-07.md).

The real runner belongs to the environment group. This one follows the same rules so the roles
can be run and tested offline, and so both sides have a worked example:

    start: initialize every character once (interaction_id + character_id + bootstrap);
    each turn: environment directives (if any) -> env.act -> records; then the actor's act
    -> records. The runner keeps every character's observations; nobody is called to
    perceive: a character reads what it saw when it is next asked to act.

Episode inputs come from the role and scene files: character ids are role-library ids (the role
file ids, the same in every interaction), and the scene's environment rules become director
directives (clock announcements and scheduled world events, with their audience).
`EchoEnvironment` stands in for the environment group's IEnvironment: it plays each directive
back as an objective action. Scene `world_private` never reaches a role.

run_scene() puts StagedRoleAgent behind this runner and writes the run directory (see
readable.FILES); the audit and the readable transcript are written at the end.
"""
import asyncio
import copy
import json
from pathlib import Path

from .agent_api import (ENV_ID, ENV_NAME, MODE, split, validate_action_result, validate_bootstrap,
                        validate_environment_content, validate_environment_view, validate_turn_input,
                        visible)
from .catalog import digest, write_json
from .interfaces import FULL_MODE
from .roles import fit_cast, origin
from .scene import WorldAgent, code_version, environment_agent, now, role_seats

INTERFACE = "agent-api-v2"


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
        return turn


# -- run directory -------------------------------------------------------------------
def _jsonl(path, items):
    path.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


def _module(m):
    return {"value": m.value, "status": m.status, "source_kind": m.source_kind, "confidence": m.confidence,
            "needs_review": m.needs_review, "source_text": m.source_text, "phrase": m.phrase, "reason": m.reason}


def run_scene(roles, scene, model, output, registry=None, mode=FULL_MODE, use_skills=True):
    """Run a scene: StagedRoleAgent behind the local runner and the echo environment. The pipeline
    writes its own trace under trace/; the audit and transcript.md follow at the end."""
    from .audit import write_report
    from .readable import write_readable
    from .registry import Registry
    from .staged import StagedRoleAgent
    from .trace import JsonlTrace

    registry = registry or Registry.load()
    ep = episode(roles, scene)
    if not use_skills and not any((r.get("file") or {}).get("skills") for r in roles):
        raise ValueError("--no-skills needs at least one role that lists skills")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    agents = StagedRoleAgent(roles, registry, model, JsonlTrace(output / "trace"), mode=mode, use_skills=use_skills)
    calls = []
    runner = LocalRunner(ep, agents, EchoEnvironment(), calls.append)
    transport = getattr(model, "transport", None)
    write_json(output / "scene.json", scene["scenario"])
    manifest = {"schema_version": "run-2.0", "interface": INTERFACE, "pipeline": "stages", "status": "running",
                "started_at": now(), "backend": model.label, "model": model.model, "mode": mode, "use_skills": bool(use_skills),
                "registry_sources": list(registry.sources), "code_version": code_version(), "scene_id": ep["scene_id"],
                "scenario_hash": digest(scene["scenario"]),
                "cast": [{k: m[k] for k in ("character_id", "name", "role_id", "title")} for m in ep["cast"]],
                "environment": "EchoEnvironment (local stand-in)", "origin": origin(roles, scene),
                "evidence_boundary": "engineering run; no human-validity or method-effectiveness claim"}
    write_json(output / "manifest.json", manifest)
    try:
        turns = asyncio.run(runner.run())
        manifest.update(status="completed", turns=turns)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
        raise
    finally:
        roles_info = {}
        for member in ep["cast"]:
            cid = member["character_id"]
            key = (runner.interaction_id, cid)
            folder = output / "roles" / cid
            folder.mkdir(parents=True, exist_ok=True)
            session = agents.sessions.get(key)
            info = {"name": member["name"], "role_id": member["role_id"], "warnings": session.warnings if session else []}
            if agents.runner.started(key):
                profile = agents.repository.load(member["role_id"])
                write_json(folder / "profile.json", {
                    "snapshot_id": profile.snapshot_id, "role_ref": profile.role_ref,
                    "modules": {c: _module(m) for c, m in profile.modules.items()},
                    "review": [{"construct_id": r.construct_id, "value": r.value, "source_kind": r.source_kind,
                                "source_text": r.source_text, "reason": r.reason}
                               for r in agents.repository.review.get(member["role_id"], [])],
                    "entry_notes": agents.repository.entry_notes.get(profile.role_ref, [])})
                write_json(folder / "setup.json", {"seat": member["title"], "briefing": agents.repository.setup(member["role_id"]),
                                                   "bound_skills": [i["name"] for i in agents.repository.raw(member["role_id"]).get("skills") or []],
                                                   "skills_used": bool(use_skills)})
                snap = agents.store.snapshot(key)
                write_json(folder / "final_state.json", {
                    "state_id": snap.state_id, "prev_state_id": snap.prev_state_id, "stable_snapshot_id": snap.stable_snapshot_id,
                    "versions": dict(snap.versions), "status": dict(snap.status), "dynamic": snap.dynamic, "memory": snap.memory})
                info.update(final_state_id=snap.state_id, profile_snapshot=profile.snapshot_id,
                            review_items=len(agents.repository.review.get(member["role_id"], [])))
            roles_info[cid] = info
        manifest.update(finished_at=now(), records=len(runner.history), calls=len(calls),
                        interaction_id=runner.interaction_id, roles=roles_info,
                        model_calls=getattr(model, "calls", None) if transport is None else getattr(transport, "calls", None))
        if transport is not None:
            manifest.update(decoding=getattr(transport, "sampling", {}).get("decoding"),
                            trailing_dropped=len(getattr(transport, "trailing", [])))
        _jsonl(output / "records.jsonl", runner.history)
        _jsonl(output / "calls.jsonl", calls)
        write_json(output / "manifest.json", manifest)
        if manifest["status"] == "completed":
            try:
                manifest["audit_passed"] = write_report(output, registry)["passed"]
            except Exception as exc:
                manifest["audit_error"] = f"{type(exc).__name__}: {exc}"
            write_json(output / "manifest.json", manifest)
        try:
            write_readable(output)
        except Exception as exc:
            (output / "transcript_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
