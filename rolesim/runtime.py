"""One focal role interacting with an executable scripted environment.

This vertical slice is not a free-form multi-agent world simulator.
"""
import copy
import json
from pathlib import Path

from .catalog import digest, write_json
from .mechanisms import CARDS, select_cards
from .persona import bounded, compile_views, text, validate_persona
from .state import (PSYCHOLOGY_VERSION, SCHEMA_VERSION, apply_delta, empty_dynamic,
                    validate_dynamic)

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
        if event["id"].startswith(("action:", "feedback:")):
            raise ValueError("reserved event id prefix")
    if len({e["id"] for e in events}) != len(events):
        raise ValueError("duplicate event ids")
    if not isinstance(scenario["world_private"], dict):
        raise ValueError("world_private must be an object")


def visible(event, role_id):
    return "*" in event["readers"] or role_id in event["readers"]


def observation(event):
    # Do not return readers or any world-private fields to a role.
    return {k: copy.deepcopy(event[k]) for k in ("id", "text", "cues", "kind")}


def initial_state():
    dynamic = empty_dynamic()
    # Prototype starting values; the focal partner is keyed "partner" in this single-role slice.
    dynamic["emotion"].update(label="紧张", intensity=.15)
    dynamic["relations"]["partner"] = {"trust": .55, "closeness": None, "note": ""}
    validate_dynamic(dynamic)
    return {"schema_version": SCHEMA_VERSION, "psychology_version": PSYCHOLOGY_VERSION,
            "dynamic": dynamic, "memory": [], "seen_ids": [], "updates": []}


def observe(state, event, persona):
    if not visible(event, persona["id"]) or event["id"] in state["seen_ids"]:
        return False
    s = persona["stable"]
    dynamic = state["dynamic"]
    tension = dynamic["emotion"]["intensity"]
    trust = dynamic["relations"]["partner"]["trust"]
    pressure = event["cues"].get("pressure", 0)
    sensitivity = s["emotion"]["threat_sensitivity"]
    recovery = s["emotion"]["recovery_rate"]
    # Explicit prototype dynamics, not fitted psychological coefficients.
    new_tension = round(min(1, max(0, tension * (1 - recovery) + .35 * pressure * sensitivity)), 4)
    new_trust = round(min(1, max(0, trust + .15 * event["reliability_signal"])), 4)
    changes = []
    if new_tension != tension:
        changes.append({"path": "dynamic.emotion.intensity", "before": tension, "after": new_tension,
                        "evidence": [event["id"]], "mechanism": "appraisal" if pressure else "decay",
                        "reason": "事件带有压力线索" if pressure else "无新压力，紧张按恢复速率回落"})
    if new_trust != trust:
        changes.append({"path": "dynamic.relations.partner.trust", "before": trust, "after": new_trust,
                        "evidence": [event["id"]], "mechanism": "relation_update",
                        "reason": "可核验的兑现/失约记录"})
    delta = {"trigger": "perceive", "event_id": event["id"], "changes": changes,
             "rule_version": "engineering-v1"}
    known = {item["id"] for item in state["memory"]} | {event["id"]}
    state["dynamic"] = apply_delta(dynamic, delta, known)
    state["memory"].append(observation(event))
    state["seen_ids"].append(event["id"])
    state["updates"].append(delta)
    return True


def make_payload(persona, state, event, scenario, mode):
    views = compile_views(persona)
    allowed = list(scenario["allowed_actions"])
    # Information already obtained should not be requested forever.
    if scenario["evidence"]["id"] in state["seen_ids"]:
        allowed = [a for a in allowed if a != "request_evidence"]
    if not allowed:
        raise ValueError("no feasible action remains")
    data = {"role": views["role_self"], "behavioral_constraints": views["controller_only"],
            "goal": scenario["goal"], "partner": scenario["partner"],
            "visible_history": copy.deepcopy(state["memory"]),
            "allowed_actions": allowed}
    if mode == "mechanisms":
        data["current_state"] = copy.deepcopy(state["dynamic"])
        data["active_mechanisms"] = select_cards(persona, state, observation(event), allowed)
    elif mode != "profile_only":
        raise ValueError("unknown mode")
    return data


def validate_decision(decision, payload):
    if not isinstance(decision, dict) or set(decision) != {"action", "evidence_ids", "brief_reason"}:
        raise ValueError("decision must contain action, evidence_ids, brief_reason only")
    if decision["action"] not in payload["allowed_actions"]:
        raise ValueError("infeasible action")
    known = {item["id"] for item in payload["visible_history"]}
    ids = decision["evidence_ids"]
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in known for i in ids):
        raise ValueError("decision cites unavailable information")
    text(decision["brief_reason"], "brief_reason")
    if len(decision["brief_reason"]) > 200:
        raise ValueError("brief_reason too long")


def execute(action, role_id, scenario, turn):
    # The world, not the model, decides whether information/action results exist.
    if action == "request_evidence" and visible(scenario["evidence"], role_id):
        return copy.deepcopy(scenario["evidence"])
    replies = {"request_evidence": "当前没有对你开放的可核对记录。",
               "ask_clarification": "合作方说明：请先明确今天可交付的范围，再商定剩余时间。",
               "propose_plan": "方案已送交合作方，尚未得到接受或实际执行的证据。",
               "set_boundary": "合作方听到了你提出的边界，尚未承诺接受。",
               "wait": "核对时间已预留，当前没有新的事实到达。"}
    return {"id": f"feedback:{turn}", "text": replies[action], "readers": [role_id],
            "kind": "feedback", "cues": {}, "reliability_signal": 0}


def run_episode(persona, scenario, backend, output, mode="mechanisms"):
    validate_persona(persona)
    validate_scenario(scenario)
    if set(persona["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
        raise ValueError("mechanism priors must match the enabled card registry")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    state = initial_state()
    fingerprint = digest(persona)
    manifest = {"schema_version": "1.0", "status": "running", "backend": backend.label,
                "model": backend.model, "mode": mode, "persona_hash": fingerprint,
                "scenario_hash": digest(scenario), "mechanism_hash": digest(CARDS),
                "sampling": "mock deterministic; real model temperature 0.3; server determinism not guaranteed",
                "evidence_boundary": "engineering smoke run; no human-validity or method-effectiveness claim"}
    write_json(output / "manifest.json", manifest)
    write_json(output / "inputs.json", {"persona": persona, "scenario": scenario, "mechanisms": CARDS})
    write_json(output / "views.json", compile_views(persona))
    try:
        with (output / "audit.jsonl").open("w", encoding="utf-8") as audit, (output / "public.jsonl").open("w", encoding="utf-8") as public:
            turn = 0
            for event in scenario["events"]:
                if not observe(state, event, persona):
                    continue
                turn += 1
                if "*" in event["readers"]:
                    public.write(json.dumps({"turn": turn, "speaker": "environment", "text": event["text"]}, ensure_ascii=False) + "\n")
                payload = make_payload(persona, state, event, scenario, mode)
                decision = backend.decide(copy.deepcopy(payload))
                validate_decision(decision, payload)
                expression_input = {"role": compile_views(persona)["role_self"],
                                    "style_constraints": [r for r in compile_views(persona)["controller_only"]["conditional_rules"] if r["source_field"].startswith("communication.")],
                                    "action": decision["action"], "visible_history": copy.deepcopy(state["memory"])}
                speech = backend.express(expression_input)
                text(speech, "speech")
                if len(speech) > 600:
                    raise ValueError("speech too long")
                public.write(json.dumps({"turn": turn, "speaker": persona["id"], "action": decision["action"], "text": speech}, ensure_ascii=False) + "\n")
                state["memory"].append({"id": f"action:{turn}", "kind": "own_action", "text": speech, "cues": {}})
                feedback = execute(decision["action"], persona["id"], scenario, turn)
                observe(state, feedback, persona)
                if "*" in feedback["readers"]:
                    public.write(json.dumps({"turn": turn, "speaker": "environment", "text": feedback["text"]}, ensure_ascii=False) + "\n")
                if digest(persona) != fingerprint:
                    raise AssertionError("stable persona was modified")
                audit.write(json.dumps({"turn": turn, "controller_input": payload, "decision": decision,
                                        "speech": speech, "feedback": observation(feedback),
                                        "state_after": copy.deepcopy(state)}, ensure_ascii=False) + "\n")
                write_json(output / "state.json", state)
        if not turn:
            raise ValueError("scenario has no events visible to this role")
        manifest.update(status="completed", turns=turn, api_calls=backend.calls)
        write_json(output / "manifest.json", manifest)
        return manifest
    except Exception as exc:
        manifest.update(status="failed", error_type=type(exc).__name__, api_calls=backend.calls)
        write_json(output / "manifest.json", manifest)
        raise
