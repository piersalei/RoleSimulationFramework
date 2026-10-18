"""Role agent behind the environment contract: `step` proposes, `perceive` updates.

The agent only sees contract Events whose audience includes its role. It keeps its
own memory and state chain; the environment stores snapshots but never edits them.
"""
import copy

from .contract import (PerceiveRequest, PerceiveResponse, ProposedAction, StepRequest,
                       StepResponse, Visibility, validate_event)
from .mechanisms import CARDS, select_cards
from .persona import compile_views, text, validate_persona
from .state import (PSYCHOLOGY_VERSION, SCHEMA_VERSION, apply_delta, empty_dynamic,
                    validate_dynamic)

PARTNER = "partner"  # relation key for the single focal partner in this slice


def initial_state():
    dynamic = empty_dynamic()
    # Prototype starting values, not measured baselines.
    dynamic["emotion"].update(label="紧张", intensity=.15)
    dynamic["relations"][PARTNER] = {"trust": .55, "closeness": None, "note": ""}
    validate_dynamic(dynamic)
    return {"schema_version": SCHEMA_VERSION, "psychology_version": PSYCHOLOGY_VERSION,
            "dynamic": dynamic, "memory": [], "seen_ids": [], "updates": []}


def observation(event):
    # Only what the role perceives: no audience list, no environment-side annotations
    # beyond cues.
    item = {"id": event.event_id, "type": event.type, "actor": event.actor,
            "content": event.content, "cues": copy.deepcopy((event.signals or {}).get("cues", {}))}
    if event.outcome is not None:
        item["outcome"] = copy.deepcopy(event.outcome)
    return item


def observe(state, event, persona):
    """Apply one visible contract Event to `state`; return the delta, or None if skipped."""
    if persona["id"] not in event.visibility.audience or event.event_id in state["seen_ids"]:
        return None
    s = persona["stable"]
    dynamic = state["dynamic"]
    signals = event.signals or {}
    tension = dynamic["emotion"]["intensity"]
    trust = dynamic["relations"][PARTNER]["trust"]
    pressure = signals.get("cues", {}).get("pressure", 0)
    # Someone's words are not evidence of fulfilment: only narrated facts and
    # adjudicated outcomes may move reliability.
    signal = signals.get("reliability_signal", 0) if event.type in {"narration", "outcome"} else 0
    sensitivity = s["emotion"]["threat_sensitivity"]
    recovery = s["emotion"]["recovery_rate"]
    # Explicit prototype dynamics, not fitted psychological coefficients.
    new_tension = round(min(1, max(0, tension * (1 - recovery) + .35 * pressure * sensitivity)), 4)
    new_trust = round(min(1, max(0, trust + .15 * signal)), 4)
    changes = []
    if new_tension != tension:
        changes.append({"path": "dynamic.emotion.intensity", "before": tension, "after": new_tension,
                        "evidence": [event.event_id], "mechanism": "appraisal" if pressure else "decay",
                        "reason": "事件带有压力线索" if pressure else "无新压力，紧张按恢复速率回落"})
    if new_trust != trust:
        changes.append({"path": f"dynamic.relations.{PARTNER}.trust", "before": trust, "after": new_trust,
                        "evidence": [event.event_id], "mechanism": "relation_update",
                        "reason": "可核验的兑现/失约记录"})
    delta = {"trigger": "perceive", "event_id": event.event_id, "changes": changes,
             "rule_version": "engineering-v1"}
    known = {item["id"] for item in state["memory"]} | {event.event_id}
    state["dynamic"] = apply_delta(dynamic, delta, known)
    state["memory"].append(observation(event))
    state["seen_ids"].append(event.event_id)
    state["updates"].append(delta)
    return delta


def make_payload(persona, state, trigger, scene_context, partner, allowed, mode):
    views = compile_views(persona)
    if not allowed:
        raise ValueError("no feasible action remains")
    data = {"role": views["role_self"], "behavioral_constraints": views["controller_only"],
            "goal": scene_context, "partner": partner,
            "visible_history": copy.deepcopy(state["memory"]),
            "allowed_actions": list(allowed)}
    if mode == "mechanisms":
        data["current_state"] = copy.deepcopy(state["dynamic"])
        data["active_mechanisms"] = select_cards(persona, state, trigger, allowed) if trigger else []
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


class Agent:
    def __init__(self, backend, allowed_actions, partner, partner_id=PARTNER, mode="mechanisms"):
        self.backend = backend
        self.allowed_actions = list(allowed_actions)
        self.partner, self.partner_id, self.mode = partner, partner_id, mode
        self.persona = None
        self.state = None
        self.state_id = None
        self.snapshots = {}     # state_id -> AgentState, for replay
        self.intents = {}       # proposal_id -> chosen action label
        self.warnings = []
        self.last_payload = self.last_decision = self.last_error = None

    # -- helpers -----------------------------------------------------------
    def _check(self, role_id, prev_state_id, profile):
        if self.persona is None:
            if profile is None:
                raise ValueError("role_profile is required on the first call")
            validate_persona(profile)
            if set(profile["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
                raise ValueError("mechanism priors must match the enabled card registry")
            self.persona = copy.deepcopy(profile)
            self.state = initial_state()
        elif profile is not None and profile != self.persona:
            raise ValueError("stable persona cannot change within an episode")
        if role_id != self.persona["id"]:
            raise ValueError("request is addressed to another role")
        if prev_state_id != self.state_id:
            raise ValueError("prev_state_id does not match the latest state")

    def _ingest(self, state, events, role_id):
        changes, last = [], None
        for event in events:
            validate_event(event)
            if role_id not in event.visibility.audience:
                # Contract 3.3.1: the environment filters; we drop and warn as a second check.
                self.warnings.append({"event_id": event.event_id, "warning": "audience excludes role; dropped"})
                continue
            delta = observe(state, event, self.persona)
            if delta is not None:
                changes.extend(delta["changes"])
                last = observation(event)
        return changes, last

    def _commit(self, state, turn, trigger, changes):
        same_turn = sum(1 for sid in self.snapshots if sid.split("@t")[1].split(".")[0] == f"{turn:03d}")
        state_id = f"{self.persona['id']}@t{turn:03d}" + (f".p{same_turn}" if same_turn else "")
        snapshot = {"state_id": state_id, "role_id": self.persona["id"], "turn": turn,
                    "prev_state_id": self.state_id, "schema_version": state["schema_version"],
                    "psychology_version": state["psychology_version"],
                    "stable": copy.deepcopy(self.persona["stable"]),
                    "dynamic": copy.deepcopy(state["dynamic"]), "memory": list(state["seen_ids"])}
        delta = {"from_state_id": self.state_id, "to_state_id": state_id, "trigger": trigger,
                 "changes": copy.deepcopy(changes)}
        self.state, self.state_id = state, state_id
        self.snapshots[state_id] = snapshot
        return copy.deepcopy(snapshot), delta

    def _allowed(self, memory):
        # Information already obtained should not be requested forever.
        obtained = any(self.intents.get(m["outcome"]["of_proposal"]) == "request_evidence"
                       and m["outcome"]["status"] == "success" for m in memory if "outcome" in m)
        return [a for a in self.allowed_actions if not (obtained and a == "request_evidence")]

    def latest_state(self):
        return copy.deepcopy(self.snapshots[self.state_id]) if self.state_id else None

    # -- contract interface ------------------------------------------------
    def step(self, req: StepRequest) -> StepResponse:
        try:
            self._check(req.role_id, req.prev_state_id, req.role_profile)
            if "speech" not in req.allowed_kinds:
                raise ValueError("this agent only proposes speech in v0")
            work = copy.deepcopy(self.state)
            changes, trigger = self._ingest(work, req.new_events, req.role_id)
            payload = make_payload(self.persona, work, trigger, req.scene_context,
                                   self.partner, self._allowed(work["memory"]), self.mode)
            self.last_payload = payload
            decision = self.backend.decide(copy.deepcopy(payload))
            self.last_decision = decision
            validate_decision(decision, payload)
            views = compile_views(self.persona)
            speech = self.backend.express({
                "role": views["role_self"],
                "style_constraints": [r for r in views["controller_only"]["conditional_rules"]
                                      if r["source_field"].startswith("communication.")],
                "action": decision["action"], "visible_history": copy.deepcopy(work["memory"])})
            text(speech, "speech")
            if len(speech) > 600:
                raise ValueError("speech too long")
            proposal_id = f"{req.role_id}@t{req.turn:03d}#1"
            audience = [req.role_id, self.partner_id]
            action = ProposedAction(proposal_id=proposal_id, kind="speech", content=speech,
                                    proposed_visibility=Visibility("public", audience),
                                    target=[self.partner_id], intent_note=decision["action"])
            work["memory"].append({"id": proposal_id, "type": "own_action", "actor": req.role_id,
                                   "content": speech, "cues": {}})
            self.intents[proposal_id] = decision["action"]
            state, delta = self._commit(work, req.turn, "step", changes)
            return StepResponse(req.role_id, req.turn, [action], decision["brief_reason"], state, delta)
        except Exception as exc:
            self.last_error = exc
            return StepResponse(req.role_id, req.turn, [], "", self.latest_state() or {}, {},
                                status="error", error=f"{type(exc).__name__}: {exc}")

    def perceive(self, req: PerceiveRequest) -> PerceiveResponse:
        try:
            self._check(req.role_id, req.prev_state_id, None)
            work = copy.deepcopy(self.state)
            changes, _ = self._ingest(work, req.events, req.role_id)
            state, delta = self._commit(work, req.turn, "perceive", changes)
            return PerceiveResponse(req.role_id, req.turn, state, delta)
        except Exception as exc:
            self.last_error = exc
            return PerceiveResponse(req.role_id, req.turn, self.latest_state() or {}, {},
                                    status="error", error=f"{type(exc).__name__}: {exc}")
