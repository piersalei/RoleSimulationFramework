"""Scene files (scene schema 3.0) and the rule-driven world agent.

A scene is background only: the role seats, at most one environment agent with its world and
rules (clock, scheduled world events and who they reach), a turn limit and the world-private
facts no role may read. It holds no script; what happens is up to the roles. The format is a
stand-in until the environment group's background format lands.

Also here: the code version a run records and the run directory name.
"""
import copy
import datetime
import os
import re
import subprocess
import zoneinfo
from pathlib import Path

from .persona import bounded, text
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


def _zone():
    # Some hosts set TZ without system zone files, where astimezone() silently falls back to UTC.
    try:
        return zoneinfo.ZoneInfo(os.environ["TZ"])
    except (KeyError, ValueError, zoneinfo.ZoneInfoNotFoundError):
        return None


def now():
    return datetime.datetime.now(_zone()).astimezone(_zone()).isoformat(timespec="seconds")


def run_name(scene_id, model, when=None):
    """Directory name for a run: local start time first, so runs sort by time."""
    when = when or datetime.datetime.now(_zone())
    tag = re.sub(r"[^0-9A-Za-z.-]+", "-", str(model)).strip("-") or "model"
    return f"{when:%Y%m%d-%H%M%S}_{scene_id}_{tag}"
