"""Two roles in one scene, talking to each other through the environment contract.

A scene is shared background plus seats; it holds no script. The environment opens the
scene, relays each role's speech to everyone present and stops after `max_turns` or
when a role closes the conversation. It never writes a line for a role and never
invents an outcome: what one role says is the only new event the other receives.
"""
import json
import subprocess
from pathlib import Path

from . import skills as sk
from .agent import Agent
from .audit import write_report
from .briefing import validate_briefing
from .catalog import digest, write_json
from .contract import (Event, PerceiveRequest, StepRequest, Visibility, to_dict,
                       validate_event as validate_contract_event)
from .mechanisms import CARDS
from .persona import compile_views, text, validate_persona
from .state import VERSIONS
from .vocab import ACTIONS

CODE_ROOT = Path(__file__).resolve().parents[1]

# Background only: who is present and what everyone knows. Goals and secrets come with
# the role (roles.py, briefing.py); what happens next is up to the roles.
SCENE_VERSION = "2.0"
SCENE_KEYS = {"schema_version", "id", "background", "seats", "max_turns", "allowed_actions", "world_private"}
SEATS = 2
MAX_TURNS = 40
OPEN_ID = "open"
RESERVED_IDS = {OPEN_ID}


def validate_scenario(scenario):
    if not isinstance(scenario, dict) or scenario.get("schema_version") != SCENE_VERSION or set(scenario) != SCENE_KEYS:
        raise ValueError(f"scenario: expected scene schema {SCENE_VERSION} with fields {sorted(SCENE_KEYS)}")
    for key in ("id", "background"):
        text(scenario[key], key)
    seats = scenario["seats"]
    if not isinstance(seats, list) or len(seats) != SEATS:
        raise ValueError(f"scenario: exactly {SEATS} seats are supported")
    for seat in seats:
        if not isinstance(seat, dict) or set(seat) != {"id", "title"}:
            raise ValueError("scenario seat: expected fields id, title")
        text(seat["id"], "seat id")
        text(seat["title"], "seat title")
    if len({s["id"] for s in seats}) != len(seats) or len({s["title"] for s in seats}) != len(seats):
        raise ValueError("scenario: seat ids and titles must be distinct")
    turns = scenario["max_turns"]
    if isinstance(turns, bool) or not isinstance(turns, int) or not SEATS <= turns <= MAX_TURNS:
        raise ValueError(f"scenario: max_turns must be an integer from {SEATS} to {MAX_TURNS}")
    actions = scenario["allowed_actions"]
    if not isinstance(actions, list) or not actions or not set(actions) <= ACTIONS or len(set(actions)) != len(actions):
        raise ValueError("scenario: unsupported/duplicate action")
    if not isinstance(scenario["world_private"], dict):
        raise ValueError("world_private must be an object")


class DialogueEnvironment:
    """Opens the scene and relays speech; every event is public to the cast."""

    def __init__(self, scenario, cast):
        validate_scenario(scenario)
        if len(cast) != len(scenario["seats"]) or len(set(cast)) != len(cast):
            raise ValueError("cast: one distinct role per seat")
        self.scenario, self.cast = scenario, list(cast)
        self.titles = {role: seat["title"] for role, seat in zip(cast, scenario["seats"])}
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
        present = "与".join(self.titles[r] for r in self.cast)
        return self._publish(Event(OPEN_ID, 1, "narration", "env", f"{present}开始就这件事当面沟通。",
                                   Visibility("public", list(self.cast))))

    def turns(self):
        for turn in range(1, self.scenario["max_turns"] + 1):
            if self.closed_by:
                return
            yield turn, self.cast[(turn - 1) % len(self.cast)]

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


def make_agent(persona, scenario, backend, seat, partner_id, mode="profile_state_mechanisms", bound=None,
               use_skills=True, briefing=None):
    """The one place a role takes a seat. The scene supplies the action space and who sits
    where; the role brings its skills and its opening setup (`briefing`). The profile itself
    reaches the Agent through the first StepRequest, per the env contract."""
    validate_persona(persona)
    validate_scenario(scenario)
    if set(persona["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
        raise ValueError("mechanism priors must match the enabled card registry")
    if briefing is not None:
        validate_briefing(briefing, RESERVED_IDS)
    titles = [s["title"] for s in scenario["seats"]]
    return Agent(backend, scenario["allowed_actions"], titles[1 - seat], partner_id, mode,
                 skills=bound if use_skills else None, briefing=briefing, seat=titles[seat])


def run_dialogue(cast, scenario, backend, output, mode="profile_state_mechanisms", use_skills=True, origin=None):
    """Run one scene. `cast` lists one {persona, skills, briefing} per seat, in seat order:
    `skills` is the role's skills binding (or None), `briefing` its opening setup (or None).
    `use_skills=False` keeps skills on record but out of every prompt, as a no-skills control.
    `origin` records which role and scene files supplied the inputs."""
    validate_scenario(scenario)
    ids = [m["persona"]["id"] for m in cast]
    env = DialogueEnvironment(scenario, ids)
    agents, members = {}, {}
    for seat, member in enumerate(cast):
        role = ids[seat]
        bound = sk.bind(member["skills"], role, sk.load_library()) if member["skills"] is not None else None
        agents[role] = make_agent(member["persona"], scenario, backend, seat, ids[1 - seat], mode, bound,
                                  use_skills, member["briefing"])
        members[role] = {**member, "bound": bound, "fingerprint": digest(member["persona"])}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "2.0", "status": "running", "backend": backend.label,
                "model": backend.model, "sampling": backend.sampling, "mode": mode,
                "interface": "env-contract-v0 step/perceive", "versions": dict(VERSIONS),
                "code_version": code_version(), "scene_id": scenario["id"], "scenario_hash": digest(scenario),
                "mechanism_hash": digest(CARDS),
                "cast": [{"role_id": r, "seat": s["id"], "title": s["title"]} for r, s in zip(ids, scenario["seats"])],
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
        write_json(folder / "inputs.json", {"persona": m["persona"], "scenario": scenario, "seat": scenario["seats"][seat],
                                            "mechanisms": CARDS, "skills_binding": m["skills"], "skills": m["bound"],
                                            "briefing": m["briefing"]})
        write_json(folder / "views.json", compile_views(m["persona"]))
    handles = {}
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
            for turn, role in env.turns():
                agent, member, files = agents[role], members[role], handles[role]
                stepped = agent.step(StepRequest(role_id=role, turn=turn, scene_context=scenario["background"],
                                                 new_events=env.collect(role), prev_state_id=agent.state_id,
                                                 role_profile=member["persona"] if agent.state_id is None else None,
                                                 allowed_kinds=["speech"]))
                _raise(agent, stepped)
                proposal = stepped.actions[0]
                speech = env.relay(proposal, turn, role)
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
        for files in handles.values():
            for handle in files.values():
                handle.close()
        write_json(output / "environment_events.json", [to_dict(e) for e in env.log])
        manifest.update(status="completed", turns=turn, closed_by=env.closed_by, api_calls=backend.calls,
                        model_served=sorted(backend.served_models))
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
        return manifest
    except Exception as exc:
        for files in handles.values():
            for handle in files.values():
                handle.close()
        write_json(output / "environment_events.json", [to_dict(e) for e in env.log])
        manifest.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500], api_calls=backend.calls,
                        model_served=sorted(backend.served_models))
        write_json(output / "manifest.json", manifest)
        raise
