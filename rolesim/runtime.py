"""One focal role interacting with a scripted environment through the contract interface.

The scripted environment is one adjudicator implementation behind `step`/`perceive`;
it is a test double, not a free-form multi-agent world simulator.
"""
import copy
import json
from pathlib import Path

from .agent import Agent
from .catalog import digest, write_json
from .contract import (Event, PerceiveRequest, StepRequest, Visibility, to_dict,
                       validate_event as validate_contract_event)
from .mechanisms import CARDS
from .persona import bounded, compile_views, text, validate_persona
from .state import VERSIONS

ACTIONS = {"request_evidence", "ask_clarification", "propose_plan", "set_boundary", "wait"}
CUES = {"uncertainty", "pressure", "commitment", "repair", "conflict"}


def validate_event(event):
    if set(event) != {"id", "text", "readers", "cues", "reliability_signal", "kind"}:
        raise ValueError("event: unexpected or missing field")
    for key in ("id", "text"):
        text(event[key], key)
    if event["kind"] not in {"observation", "claim", "feedback"}:
        raise ValueError("unsupported event kind")
    if not isinstance(event["readers"], list) or not event["readers"]:
        raise ValueError("event requires readers")
    for reader in event["readers"]:
        text(reader, "reader")
    if not isinstance(event["cues"], dict) or not set(event["cues"]) <= CUES:
        raise ValueError("unsupported cue")
    for key, value in event["cues"].items():
        bounded(value, 0, 1, key)
    bounded(event["reliability_signal"], -1, 1, "reliability_signal")
    if event["kind"] == "claim" and event["reliability_signal"] != 0:
        raise ValueError("a claim cannot itself establish reliability")


def validate_scenario(scenario):
    if set(scenario) != {"schema_version", "id", "goal", "partner", "allowed_actions", "events", "evidence", "world_private"} or scenario["schema_version"] != "1.0":
        raise ValueError("scenario: unsupported schema")
    for key in ("id", "goal", "partner"):
        text(scenario[key], key)
    actions = scenario["allowed_actions"]
    if not isinstance(actions, list) or not actions or not set(actions) <= ACTIONS or len(set(actions)) != len(actions):
        raise ValueError("scenario: unsupported/duplicate action")
    if not isinstance(scenario["events"], list) or not scenario["events"]:
        raise ValueError("scenario requires scheduled events")
    events = scenario["events"] + [scenario["evidence"]]
    for event in events:
        validate_event(event)
        if event["id"].startswith(("action:", "feedback:", "speech:")):
            raise ValueError("reserved event id prefix")
    if len({e["id"] for e in events}) != len(events):
        raise ValueError("duplicate event ids")
    if not isinstance(scenario["world_private"], dict):
        raise ValueError("world_private must be an object")


PARTNER_ID = "partner"
# Fixture adjudications for actions that do not depend on world data.
REPLIES = {"request_evidence": ("failed", "当前没有对你开放的可核对记录。"),
           "ask_clarification": ("success", "合作方说明：请先明确今天可交付的范围，再商定剩余时间。"),
           "propose_plan": ("partial", "方案已送交合作方，尚未得到接受或实际执行的证据。"),
           "set_boundary": ("partial", "合作方听到了你提出的边界，尚未承诺接受。"),
           "wait": ("success", "核对时间已预留，当前没有新的事实到达。")}


class ScriptedEnvironment:
    """Converts a scripted scenario into contract Events and adjudicates proposals."""

    def __init__(self, scenario, role_id):
        validate_scenario(scenario)
        self.scenario, self.role_id = scenario, role_id
        self.cast = [role_id, PARTNER_ID]
        self.log = []   # every Event the environment produced, for replay

    def _event(self, raw, turn, type_, actor, outcome=None, caused_by=None):
        if "*" in raw["readers"]:
            visibility = Visibility("public", list(self.cast))
        else:
            visibility = Visibility("private", list(raw["readers"]))
        event = Event(event_id=raw["id"], turn=turn, type=type_, actor=actor, content=raw["text"],
                      visibility=visibility, outcome=outcome, caused_by=caused_by,
                      signals={"cues": copy.deepcopy(raw["cues"]),
                               "reliability_signal": raw["reliability_signal"]})
        validate_contract_event(event)
        self.log.append(event)
        return event

    def scheduled(self):
        """Yield (turn, Event) for scheduled events, already filtered to the focal role."""
        turn = 0
        for raw in self.scenario["events"]:
            if raw["kind"] == "claim":
                type_, actor = "speech", PARTNER_ID
            else:
                type_, actor = "narration", "env"
            if not visible(raw, self.role_id):
                self._event(raw, turn + 1, type_, actor)  # logged, never delivered
                continue
            turn += 1
            yield turn, self._event(raw, turn, type_, actor)

    def adjudicate(self, proposal, turn):
        """Return the Events a proposal produces; the outcome always reaches the actor."""
        action = proposal.intent_note
        speech = Event(event_id=f"speech:{turn}", turn=turn, type="speech", actor=self.role_id,
                       content=proposal.content, target=list(proposal.target),
                       visibility=Visibility("public", list(self.cast)), caused_by=proposal.proposal_id)
        validate_contract_event(speech)
        self.log.append(speech)
        evidence = self.scenario["evidence"]
        # The world, not the model, decides whether information/action results exist.
        if action == "request_evidence" and visible(evidence, self.role_id):
            raw, status = evidence, "success"
        elif action in self.scenario["allowed_actions"]:
            status, reply = REPLIES[action]
            raw = {"id": f"feedback:{turn}", "text": reply, "readers": [self.role_id],
                   "cues": {}, "reliability_signal": 0}
        else:
            status = "blocked"
            raw = {"id": f"feedback:{turn}", "text": "该行动在本场景中不可执行。", "readers": [self.role_id],
                   "cues": {}, "reliability_signal": 0}
        outcome = {"of_proposal": proposal.proposal_id, "status": status, "detail": raw["text"]}
        result = self._event(raw, turn, "outcome", "env", outcome=outcome, caused_by=proposal.proposal_id)
        return speech, [result]


def visible(raw, role_id):
    return "*" in raw["readers"] or role_id in raw["readers"]


def _public(handle, turn, speaker, text_, action=None):
    record = {"turn": turn, "speaker": speaker}
    if action:
        record["action"] = action
    record["text"] = text_
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _raise(agent, response):
    if response.status != "ok":
        raise agent.last_error


def run_episode(persona, scenario, backend, output, mode="mechanisms"):
    validate_persona(persona)
    validate_scenario(scenario)
    if set(persona["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
        raise ValueError("mechanism priors must match the enabled card registry")
    role_id = persona["id"]
    env = ScriptedEnvironment(scenario, role_id)
    agent = Agent(backend, scenario["allowed_actions"], scenario["partner"], PARTNER_ID, mode)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    fingerprint = digest(persona)
    manifest = {"schema_version": "1.1", "status": "running", "backend": backend.label,
                "model": backend.model, "mode": mode, "interface": "env-contract-v0 step/perceive", "versions": dict(VERSIONS),
                "stable_snapshot_id": digest(persona["stable"]),
                "persona_hash": fingerprint,
                "scenario_hash": digest(scenario), "mechanism_hash": digest(CARDS),
                "sampling": "mock deterministic; real model temperature 0.3; server determinism not guaranteed",
                "evidence_boundary": "engineering smoke run; no human-validity or method-effectiveness claim"}
    write_json(output / "manifest.json", manifest)
    write_json(output / "inputs.json", {"persona": persona, "scenario": scenario, "mechanisms": CARDS})
    write_json(output / "views.json", compile_views(persona))
    try:
        with (output / "audit.jsonl").open("w", encoding="utf-8") as audit, \
                (output / "public.jsonl").open("w", encoding="utf-8") as public, \
                (output / "states.jsonl").open("w", encoding="utf-8") as states, \
                (output / "mechanism_log.jsonl").open("w", encoding="utf-8") as mech_log:
            turn = 0
            logged = 0
            for turn, event in env.scheduled():
                if event.visibility.scope == "public":
                    _public(public, turn, "environment", event.content)
                stepped = agent.step(StepRequest(role_id=role_id, turn=turn, scene_context=scenario["goal"],
                                                 new_events=[event], prev_state_id=agent.state_id,
                                                 role_profile=persona if turn == 1 else None,
                                                 allowed_kinds=["speech"]))
                _raise(agent, stepped)
                proposal = stepped.actions[0]
                _public(public, turn, role_id, proposal.content, proposal.intent_note)
                speech_event, outcomes = env.adjudicate(proposal, turn)
                perceived = agent.perceive(PerceiveRequest(role_id=role_id, turn=turn, events=outcomes,
                                                           prev_state_id=stepped.state["state_id"]))
                _raise(agent, perceived)
                for item in outcomes:
                    if item.visibility.scope == "public":
                        _public(public, turn, "environment", item.content)
                if digest(persona) != fingerprint:
                    raise AssertionError("stable persona was modified")
                for snapshot in (stepped.state, perceived.state):
                    states.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
                audit.write(json.dumps({"turn": turn, "controller_input": agent.last_payload,
                                        "decision": agent.last_decision, "thought": stepped.thought,
                                        "proposal": to_dict(proposal), "step_delta": stepped.delta,
                                        "outcomes": [to_dict(e) for e in outcomes],
                                        "perceive_delta": perceived.delta,
                                        "state_after": perceived.state}, ensure_ascii=False) + "\n")
                write_json(output / "state.json", perceived.state)
                for entry in agent.mechanism_log[logged:]:
                    mech_log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                logged = len(agent.mechanism_log)
        if not turn:
            raise ValueError("scenario has no events visible to this role")
        write_json(output / "environment_events.json", [to_dict(e) for e in env.log])
        manifest.update(status="completed", turns=turn, api_calls=backend.calls,
                        final_state_id=agent.state_id, visibility_warnings=agent.warnings,
                        rejected_groups=sum(len(e["rejected"]) for e in agent.mechanism_log),
                        status_transitions=len(agent.state["status_log"]))
        write_json(output / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, api_calls=backend.calls)
        write_json(output / "manifest.json", manifest)
        raise
