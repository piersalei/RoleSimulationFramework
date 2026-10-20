"""Roles in one scene, talking to each other through the environment contract.

A scene (schema 3.0) is shared background plus the agents present: two to six role
agents and at most one environment agent. It holds no script. The environment opens the
scene, lets the roles speak in the scene's seat order, relays each line to everyone
present and stops after `max_turns` lines or when a role closes the conversation.

The environment agent is not a person: it has no psychology, owns the world state (time,
weather, ...) and changes it only by its declared rules, telling the roles through
narration events. It never speaks for a role, never decides the result of a role's move
and never writes a role's inner state; a role's state changes only through its own
appraisal and mechanism cards.
"""
import copy
import datetime
import json
import os
import re
import subprocess
import zoneinfo
from pathlib import Path

from . import skills as sk
from .agent import Agent
from .audit import write_report
from .briefing import validate_briefing
from .catalog import digest, write_json
from .contract import (Event, PerceiveRequest, StepRequest, Visibility, to_dict,
                       validate_event as validate_contract_event)
from .mechanisms import CARDS, snapshot as mechanism_snapshot
from .persona import bounded, compile_views, text, validate_persona
from .providers import MockBackend
from .readable import write_readable
from .state import VERSIONS
from .vocab import ACTIONS, CUES

CODE_ROOT = Path(__file__).resolve().parents[1]

# Background only: who is present and what everyone knows. Goals and secrets come with
# the role (roles.py, briefing.py); what happens next is up to the roles.
SCENE_VERSION = "3.0"
SCENE_KEYS = {"schema_version", "id", "background", "agents", "max_turns", "allowed_actions", "world_private"}
KINDS = {"role", "environment"}
MIN_ROLES, MAX_ROLES = 2, 6
MAX_TURNS = 40
OPEN_ID = "open"
WORLD_PREFIX = "world:"
RESERVED_IDS = {OPEN_ID}
WORLD_KEYS = {"time", "weather", "place", "noise"}
WORLD_TEXT = {"time": "时间", "weather": "天气", "place": "地点", "noise": "环境声音"}
RULE_KEYS = {"minutes_per_round", "announce_time", "events"}
WORLD_EVENT_KEYS = {"round", "text", "set", "audience", "cues"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _world_value(key, value, label):
    text(value, label)
    if key == "time" and not TIME.match(value):
        raise ValueError(f"{label}: time must be HH:MM")


def _validate_environment(agent, roles):
    if set(agent) != {"id", "kind", "title", "world", "rules"}:
        raise ValueError("environment agent: expected fields id, kind, title, world, rules")
    world, rules = agent["world"], agent["rules"]
    if not isinstance(world, dict) or not world or not set(world) <= WORLD_KEYS:
        raise ValueError(f"environment world: keys must come from {sorted(WORLD_KEYS)}")
    for key, value in world.items():
        _world_value(key, value, f"world.{key}")
    if not isinstance(rules, dict) or set(rules) != RULE_KEYS:
        raise ValueError(f"environment rules: expected fields {sorted(RULE_KEYS)}")
    minutes = rules["minutes_per_round"]
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 240:
        raise ValueError("environment rules: minutes_per_round must be an integer from 0 to 240")
    if minutes and "time" not in world:
        raise ValueError("environment rules: advancing time needs world.time")
    if not isinstance(rules["announce_time"], bool) or (rules["announce_time"] and "time" not in world):
        raise ValueError("environment rules: announce_time must be a boolean and needs world.time")
    if not isinstance(rules["events"], list):
        raise ValueError("environment rules: events must be a list")
    for item in rules["events"]:
        if not isinstance(item, dict) or not {"round", "text"} <= set(item) <= WORLD_EVENT_KEYS:
            raise ValueError(f"world event: fields round and text, optionally {sorted(WORLD_EVENT_KEYS - {'round', 'text'})}")
        if isinstance(item["round"], bool) or not isinstance(item["round"], int) or item["round"] < 2:
            raise ValueError("world event: round must be an integer from 2 (round 1 is the opening)")
        text(item["text"], "world event text")
        for key, value in item.get("set", {}).items():
            if key not in world:
                raise ValueError(f"world event: may only set declared world variables, not {key!r}")
            _world_value(key, value, f"world event set.{key}")
        audience = item.get("audience")
        if audience is not None and (not isinstance(audience, list) or not audience or not set(audience) <= roles):
            raise ValueError("world event: audience must list role seat ids")
        cues = item.get("cues", {})
        if not isinstance(cues, dict) or not set(cues) <= CUES:
            raise ValueError("world event: unsupported cue")
        for key, value in cues.items():
            bounded(value, 0, 1, f"world event cue {key}")


def validate_scenario(scenario):
    if not isinstance(scenario, dict) or scenario.get("schema_version") != SCENE_VERSION or set(scenario) != SCENE_KEYS:
        raise ValueError(f"scenario: expected scene schema {SCENE_VERSION} with fields {sorted(SCENE_KEYS)}")
    for key in ("id", "background"):
        text(scenario[key], key)
    agents = scenario["agents"]
    if not isinstance(agents, list):
        raise ValueError("scenario: agents must be a list")
    for agent in agents:
        if not isinstance(agent, dict) or agent.get("kind") not in KINDS:
            raise ValueError(f"scenario agent: kind must be one of {sorted(KINDS)}")
        text(agent.get("id"), "agent id")
        text(agent.get("title"), "agent title")
        if agent["kind"] == "role" and set(agent) != {"id", "kind", "title"}:
            raise ValueError("scenario role agent: expected fields id, kind, title")
    if len({a["id"] for a in agents}) != len(agents) or len({a["title"] for a in agents}) != len(agents):
        raise ValueError("scenario: agent ids and titles must be distinct")
    roles = [a for a in agents if a["kind"] == "role"]
    worlds = [a for a in agents if a["kind"] == "environment"]
    if not MIN_ROLES <= len(roles) <= MAX_ROLES:
        raise ValueError(f"scenario: {MIN_ROLES} to {MAX_ROLES} role agents are supported")
    if len(worlds) > 1:
        raise ValueError("scenario: at most one environment agent")
    for agent in worlds:
        _validate_environment(agent, {r["id"] for r in roles})
    turns = scenario["max_turns"]
    if isinstance(turns, bool) or not isinstance(turns, int) or not len(roles) <= turns <= MAX_TURNS:
        raise ValueError(f"scenario: max_turns must be an integer from the number of roles to {MAX_TURNS}")
    actions = scenario["allowed_actions"]
    if not isinstance(actions, list) or not actions or not set(actions) <= ACTIONS or len(set(actions)) != len(actions):
        raise ValueError("scenario: unsupported/duplicate action")
    if not isinstance(scenario["world_private"], dict):
        raise ValueError("world_private must be an object")


def role_seats(scenario):
    return [a for a in scenario["agents"] if a["kind"] == "role"]


def environment_agent(scenario):
    return next((a for a in scenario["agents"] if a["kind"] == "environment"), None)


def describe_world(world):
    return "；".join(f"{WORLD_TEXT[k]}：{v}" for k, v in world.items())


class WorldAgent:
    """Rule-driven environment agent: advances the clock, applies scheduled world changes
    and announces them. Its events are narration only; it has no psychology."""

    def __init__(self, spec, seat_to_role):
        self.id, self.title = spec["id"], spec["title"]
        self.world = dict(spec["world"])
        self.rules = copy.deepcopy(spec["rules"])
        self.seat_to_role = dict(seat_to_role)
        self.history = [{"round": 1, "turn": 1, "world": dict(self.world), "changed": {}}]

    def _advance(self):
        if not self.rules["minutes_per_round"]:
            return {}
        hours, minutes = map(int, self.world["time"].split(":"))
        total = (hours * 60 + minutes + self.rules["minutes_per_round"]) % (24 * 60)
        self.world["time"] = f"{total // 60:02d}:{total % 60:02d}"
        return {"time": self.world["time"]}

    def tick(self, round_no, turn, cast):
        """World changes at the start of `round_no`, as (event_id, content, audience, cues) items."""
        changed = self._advance()
        out = []
        if self.rules["announce_time"]:
            out.append((f"{WORLD_PREFIX}{round_no}:time", f"现在是 {self.world['time']}。", list(cast), {}))
        for index, item in enumerate(e for e in self.rules["events"] if e["round"] == round_no):
            self.world.update(item.get("set", {}))
            changed.update(item.get("set", {}))
            audience = [self.seat_to_role[s] for s in item["audience"]] if item.get("audience") else list(cast)
            out.append((f"{WORLD_PREFIX}{round_no}:{index + 1}", item["text"], audience, dict(item.get("cues", {}))))
        self.history.append({"round": round_no, "turn": turn, "world": dict(self.world), "changed": changed})
        return out


class DialogueEnvironment:
    """Opens the scene, runs the world agent's rules and relays speech to everyone present."""

    def __init__(self, scenario, cast):
        validate_scenario(scenario)
        seats = role_seats(scenario)
        if len(cast) != len(seats) or len(set(cast)) != len(cast):
            raise ValueError("cast: one distinct role per role seat")
        self.scenario, self.cast = scenario, list(cast)
        self.titles = {role: seat["title"] for role, seat in zip(cast, seats)}
        spec = environment_agent(scenario)
        self.world = WorldAgent(spec, {s["id"]: r for s, r in zip(seats, cast)}) if spec else None
        self.log = []                               # every Event produced, for replay
        self.inbox = {role: [] for role in cast}    # delivered at the role's next step
        self.closed_by = None

    def _publish(self, event):
        validate_contract_event(event)
        self.log.append(event)
        for role in event.visibility.audience:
            if role != event.actor:
                self.inbox[role].append(event)
        return event

    def open(self):
        titles = [self.titles[r] for r in self.cast]
        present = "、".join(titles[:-1]) + "与" + titles[-1]
        content = f"{present}开始就这件事当面沟通。"
        if self.world:
            content += f"（{describe_world(self.world.world)}）"
        return self._publish(Event(OPEN_ID, 1, "narration", "env", content, Visibility("public", list(self.cast))))

    def turns(self):
        """Seat order, one line per turn; the world agent acts at the start of every later round."""
        for turn in range(1, self.scenario["max_turns"] + 1):
            if self.closed_by:
                return
            index = (turn - 1) % len(self.cast)
            if index == 0 and turn > 1 and self.world:
                self.tick((turn - 1) // len(self.cast) + 1, turn)
            yield turn, self.cast[index]

    def tick(self, round_no, turn):
        events = []
        for event_id, content, audience, cues in self.world.tick(round_no, turn, self.cast):
            scope = "public" if set(audience) == set(self.cast) else "private"
            events.append(self._publish(Event(event_id, turn, "narration", "env", content,
                                              Visibility(scope, audience),
                                              signals={"cues": cues} if cues else None)))
        return events

    def collect(self, role):
        events, self.inbox[role] = self.inbox[role], []
        return events

    def relay(self, proposal, turn, speaker):
        """Publish a proposed line as said. The chosen move stays private to the speaker."""
        if proposal.kind != "speech" or proposal.intent_note not in self.scenario["allowed_actions"]:
            raise ValueError("proposal is not an allowed speech move in this scene")
        event = Event(f"speech:{turn}", turn, "speech", speaker, proposal.content,
                      Visibility("public", list(self.cast)), target=list(proposal.target),
                      caused_by=proposal.proposal_id)
        self._publish(event)
        if proposal.intent_note == "close":
            self.closed_by = speaker
        return event


def _public(handle, record):
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _raise(agent, response):
    if response.status != "ok":
        raise agent.last_error


def code_version(root=CODE_ROOT):
    """Git commit of the code that produced a run; `git_dirty` is True when it differs from HEAD."""
    def git(*args):
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10)
        if done.returncode:
            raise OSError(done.stderr.strip())
        return done.stdout.strip()
    try:
        return {"git_sha": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"git_sha": "unavailable", "git_dirty": None}


def make_agent(persona, scenario, backend, seat, cast, mode="profile_state_mechanisms", bound=None,
               use_skills=True, briefing=None, appraise_state=True):
    """The one place a role takes a seat. The scene supplies the action space and who sits
    where (`cast` lists the role ids in seat order, `seat` is this role's index); the role
    brings its skills and its opening setup (`briefing`). The profile itself reaches the
    Agent through the first StepRequest, per the env contract."""
    validate_persona(persona)
    validate_scenario(scenario)
    if set(persona["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
        raise ValueError("mechanism priors must match the enabled card registry")
    if briefing is not None:
        validate_briefing(briefing, RESERVED_IDS)
    titles = [s["title"] for s in role_seats(scenario)]
    others = {role: titles[i] for i, role in enumerate(cast) if i != seat}
    return Agent(backend, scenario["allowed_actions"], others, mode, skills=bound if use_skills else None,
                 briefing=briefing, seat=titles[seat], appraise_state=appraise_state)


def run_dialogue(cast, scenario, backend, output, mode="profile_state_mechanisms", use_skills=True, origin=None,
                 appraise_state=True):
    """Run one scene. `cast` lists one {persona, skills, briefing} per role seat, in seat order:
    `skills` is the role's skills binding (or None), `briefing` its opening setup (or None).
    `use_skills=False` keeps skills on record but out of every prompt, as a no-skills control;
    `appraise_state=False` keeps the state out of every appraisal prompt.
    `origin` records which role and scene files supplied the inputs."""
    validate_scenario(scenario)
    ids = [m["persona"]["id"] for m in cast]
    env = DialogueEnvironment(scenario, ids)
    seats = role_seats(scenario)
    agents, members = {}, {}
    for seat, member in enumerate(cast):
        role = ids[seat]
        bound = sk.bind(member["skills"], role, sk.load_library()) if member["skills"] is not None else None
        agents[role] = make_agent(member["persona"], scenario, backend, seat, ids, mode, bound,
                                  use_skills, member["briefing"], appraise_state)
        members[role] = {**member, "bound": bound, "fingerprint": digest(member["persona"])}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    spec = environment_agent(scenario)
    manifest = {"schema_version": "3.0", "status": "running", "started_at": _now(), "backend": backend.label,
                "model": backend.model, "sampling": backend.sampling, "mode": mode,
                "appraise_state": bool(appraise_state),
                "interface": "env-contract-v0 step/perceive", "versions": dict(VERSIONS),
                "code_version": code_version(), "scene_id": scenario["id"], "scenario_hash": digest(scenario),
                "mechanism_hash": digest(CARDS),
                "cast": [{"role_id": r, "seat": s["id"], "title": s["title"]} for r, s in zip(ids, seats)],
                "environment": None if spec is None else {"id": spec["id"], "title": spec["title"]},
                "roles": {r: {"persona_hash": m["fingerprint"],
                              "stable_snapshot_id": digest(m["persona"]["stable"]),
                              "skills": None if m["bound"] is None else {
                                  "enabled": bool(use_skills), "binding_hash": digest(m["skills"]),
                                  "skills_hash": digest(m["bound"]), "names": [s["name"] for s in m["bound"]]},
                              "briefing": m["briefing"] is not None} for r, m in members.items()},
                "origin": origin,
                "evidence_boundary": "engineering run; no human-validity or method-effectiveness claim"}
    write_json(output / "manifest.json", manifest)
    folders = {}
    for seat, role in enumerate(ids):
        folder = folders[role] = output / "roles" / role
        folder.mkdir(parents=True)
        m = members[role]
        write_json(folder / "inputs.json", {"persona": m["persona"], "scenario": scenario, "seat": seats[seat],
                                            "mechanisms": mechanism_snapshot(), "skills_binding": m["skills"],
                                            "skills": m["bound"], "briefing": m["briefing"]})
        write_json(folder / "views.json", compile_views(m["persona"]))
    handles = {}

    def finish(status, **extra):
        for files in handles.values():
            for handle in files.values():
                handle.close()
        write_json(output / "environment_events.json", [to_dict(e) for e in env.log])
        if env.world:
            (output / "world.jsonl").write_text(
                "".join(json.dumps(h, ensure_ascii=False) + "\n" for h in env.world.history), encoding="utf-8")
        manifest.update(status=status, finished_at=_now(), api_calls=backend.calls,
                        model_served=sorted(backend.served_models), **extra)

    try:
        for role, folder in folders.items():
            handles[role] = {name: (folder / f"{name}.jsonl").open("w", encoding="utf-8")
                             for name in ("audit", "states", "mechanism_log")}
        logged = {role: 0 for role in ids}
        spoken = {role: 0 for role in ids}
        turn = 0
        with (output / "public.jsonl").open("w", encoding="utf-8") as public:
            opening = env.open()
            _public(public, {"turn": 0, "speaker": "environment", "text": opening.content})
            published = len(env.log)
            for turn, role in env.turns():
                for event in env.log[published:]:
                    _public(public, {"turn": event.turn, "speaker": "environment", "text": event.content,
                                     "audience": event.visibility.audience})
                agent, member, files = agents[role], members[role], handles[role]
                stepped = agent.step(StepRequest(role_id=role, turn=turn, scene_context=scenario["background"],
                                                 new_events=env.collect(role), prev_state_id=agent.state_id,
                                                 role_profile=member["persona"] if agent.state_id is None else None,
                                                 allowed_kinds=["speech"]))
                _raise(agent, stepped)
                proposal = stepped.actions[0]
                speech = env.relay(proposal, turn, role)
                published = len(env.log)
                _public(public, {"turn": turn, "speaker": role, "title": env.titles[role],
                                 "action": proposal.intent_note, "text": proposal.content})
                perceived = agent.perceive(PerceiveRequest(role_id=role, turn=turn, events=[speech],
                                                           prev_state_id=stepped.state["state_id"]))
                _raise(agent, perceived)
                if digest(member["persona"]) != member["fingerprint"]:
                    raise AssertionError("stable persona was modified")
                for snapshot in (stepped.state, perceived.state):
                    files["states"].write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                files["audit"].write(json.dumps({
                    "turn": turn, "appraisals": agent.last_appraisals, "controller_input": agent.last_payload,
                    "expression_input": agent.last_express, "decision": agent.last_decision,
                    "reasoning": agent.last_reasoning, "thought": stepped.thought, "proposal": to_dict(proposal),
                    "step_delta": stepped.delta, "delivered": [to_dict(speech)],
                    "perceive_delta": perceived.delta, "state_after": perceived.state}, ensure_ascii=False) + "\n")
                write_json(folders[role] / "state.json", perceived.state)
                for entry in agent.mechanism_log[logged[role]:]:
                    files["mechanism_log"].write(json.dumps(entry, ensure_ascii=False) + "\n")
                logged[role] = len(agent.mechanism_log)
                spoken[role] += 1
                for handle in [public, *files.values()]:
                    handle.flush()
        finish("completed", turns=turn, closed_by=env.closed_by)
        for role, agent in agents.items():
            manifest["roles"][role].update(
                turns=spoken[role], final_state_id=agent.state_id, visibility_warnings=agent.warnings,
                rejected_groups=sum(len(e["rejected"]) for e in agent.mechanism_log),
                status_transitions=len(agent.state["status_log"]) if agent.state else 0)
        write_json(output / "manifest.json", manifest)
        report = write_report(output)
        manifest["audit"] = {"passed": report["passed"],
                             "failed_checks": {r: [c["id"] for c in rep["checks"] if c["status"] != "pass"]
                                               for r, rep in report["roles"].items()}}
        write_json(output / "manifest.json", manifest)
        _readable(output)
        return manifest
    except Exception as exc:
        finish("failed", error_type=type(exc).__name__, error=str(exc)[:500])
        write_json(output / "manifest.json", manifest)
        _readable(output)
        raise


def _zone():
    # Some hosts set TZ without system zone files, where astimezone() silently falls back to UTC.
    try:
        return zoneinfo.ZoneInfo(os.environ["TZ"])
    except (KeyError, ValueError, zoneinfo.ZoneInfoNotFoundError):
        return None


def _now():
    return datetime.datetime.now(_zone()).astimezone(_zone()).isoformat(timespec="seconds")


def run_name(scene_id, model, when=None):
    """Directory name for a run: local start time first, so runs sort by time."""
    when = when or datetime.datetime.now(_zone())
    tag = re.sub(r"[^0-9A-Za-z.-]+", "-", "mock" if model == MockBackend.model else str(model)).strip("-")
    return f"{when:%Y%m%d-%H%M%S}_{scene_id}_{tag}"


def _readable(output):
    """The Markdown transcript is a convenience view; failing to write it must not change the run's outcome."""
    try:
        write_readable(output)
    except Exception as exc:
        (output / "transcript_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
