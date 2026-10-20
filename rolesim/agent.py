"""Role agent behind the environment contract: `step` proposes, `perceive` updates.

The agent only sees contract Events whose audience includes its role. It keeps its
own memory and state chain; the environment stores snapshots but never edits them.
"""
import copy

from . import memory as mem
from . import skills as sk
from .briefing import controller_view, expression_view, recap_entries
from .catalog import digest
from .contract import (PerceiveRequest, PerceiveResponse, ProposedAction, StepRequest,
                       StepResponse, Visibility, validate_event)
from .mechanisms import CARDS, select_cards
from .persona import compile_views, text, validate_persona
from .state import (PSYCHOLOGY_VERSION, SCHEMA_VERSION, VERSIONS, activate, commit_phase,
                    new_dynamic, validate_dynamic)

PARTNER = "partner"  # relation key for the single focal partner in this slice
# Ablation levels. State is computed and logged in every mode; the mode only decides
# what the controller prompt may contain.
MODES = ("profile_only", "profile_state", "profile_state_mechanisms")


def initial_state(briefing=None):
    dynamic, status = new_dynamic([PARTNER])
    log = []
    # Prototype starting values, not measured baselines: marked origin "default" and
    # written through the audited activation step. Everything else stays unknown/not_modeled.
    base = "dynamic.affect_state.emotion"
    for path, value in ((f"{base}.label", "紧张"), (f"{base}.intensity", .15),
                        (f"dynamic.relation_state.{PARTNER}.trust", .55)):
        activate(dynamic, status, log, path, value, "prototype starting value, not a measurement",
                 ["engineering_default"])
    validate_dynamic(dynamic, status)
    return {"schema_version": SCHEMA_VERSION, "psychology_version": PSYCHOLOGY_VERSION,
            "dynamic": dynamic, "module_status": status, "status_log": log,
            "memory": recap_entries(briefing), "seen_ids": [], "updates": [], "mechanism_log": [], "commits": {}}


def observation(event):
    # Only what the role perceives: no audience list, no environment-side annotations
    # beyond cues.
    item = {"id": event.event_id, "type": event.type, "actor": event.actor,
            "content": event.content, "cues": copy.deepcopy((event.signals or {}).get("cues", {}))}
    if event.outcome is not None:
        item["outcome"] = copy.deepcopy(event.outcome)
    return item


def observe(state, event, persona, phase="perceive"):
    """Apply one visible contract Event to `state`; return the delta, or None if skipped.

    Candidate changes are committed by dependency group against one snapshot. A rejected
    group is recorded in `mechanism_log` and in `delta["rejected"]`, never silently dropped.
    """
    if persona["id"] not in event.visibility.audience or event.event_id in state["seen_ids"]:
        return None
    s = persona["stable"]
    dynamic = state["dynamic"]
    signals = event.signals or {}
    tension = dynamic["affect_state"]["emotion"]["intensity"]
    trust = dynamic["relation_state"][PARTNER]["trust"]
    pressure = signals.get("cues", {}).get("pressure", 0)
    # Someone's words are not evidence of fulfilment: only narrated facts and
    # adjudicated outcomes may move reliability.
    signal = signals.get("reliability_signal", 0) if event.type in {"narration", "outcome"} else 0
    sensitivity = s["traits"]["affective_capacities"]["threat_sensitivity"]
    recovery = s["traits"]["affective_capacities"]["recovery_rate"]
    changes = []
    # Unknown (null) inputs skip their branch instead of being replaced by a neutral guess.
    if tension is not None:
        # Explicit prototype dynamics, not fitted psychological coefficients.
        new_tension = round(min(1, max(0, tension * (1 - recovery) + .35 * pressure * sensitivity)), 4)
        if new_tension != tension:
            changes.append({"path": "dynamic.affect_state.emotion.intensity", "before": tension,
                            "after": new_tension, "evidence": [event.event_id], "group": "affect",
                            "mechanism": "emotion_update" if pressure else "decay_or_recovery",
                            "reason": "事件带有压力线索" if pressure else "无新压力，紧张按恢复速率回落"})
    if trust is not None:
        new_trust = round(min(1, max(0, trust + .15 * signal)), 4)
        if new_trust != trust:
            changes.append({"path": f"dynamic.relation_state.{PARTNER}.trust", "before": trust,
                            "after": new_trust, "evidence": [event.event_id], "group": "relation",
                            "mechanism": "relation_update", "reason": "可核验的兑现/失约记录"})
    delta = {"trigger": phase, "event_id": event.event_id, "changes": changes,
             "rule_version": VERSIONS["update_rule_version"]}
    known = {item["id"] for item in state["memory"]} | {event.event_id}
    key = f"{persona['id']}/{event.event_id}/{phase}"
    state["dynamic"], adopted, log = commit_phase(dynamic, state["module_status"], delta, known,
                                                  commit_key=key, committed=state["commits"])
    delta["changes"] = adopted
    delta["rejected"] = log["rejected"]
    state["mechanism_log"].append(log)
    mem.add(state["memory"], observation(event))
    state["seen_ids"].append(event.event_id)
    state["updates"].append(delta)
    return delta


def make_payload(persona, state, trigger, scene_context, partner, allowed, mode, skills=None, briefing=None):
    """Decision prompt. Skills and briefing belong to the profile, so they appear in every mode."""
    views = compile_views(persona)
    if not allowed:
        raise ValueError("no feasible action remains")
    query = " ".join(filter(None, [(trigger or {}).get("content"), (briefing or {}).get("goal")]))
    data = {"role": views["role_self"], "behavioral_constraints": views["controller_only"],
            "scene_background": scene_context, "partner": partner,
            "visible_history": mem.recall(state["memory"], query),
            "allowed_actions": list(allowed)}
    if briefing:
        data["briefing"] = controller_view(briefing)
    if skills:
        data["skill_catalog"] = sk.catalog(skills)
        data["active_skills"] = sk.select(skills, trigger, allowed, briefing)
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    if mode != "profile_only":
        data["current_state"] = copy.deepcopy(state["dynamic"])
    if mode == "profile_state_mechanisms":
        data["active_mechanisms"] = select_cards(persona, state, trigger, allowed) if trigger else []
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
    def __init__(self, backend, allowed_actions, partner, partner_id=PARTNER, mode="profile_state_mechanisms",
                 skills=None, briefing=None):
        """`skills` is a bound skill list (skills.bind); `briefing` is this role's validated scene briefing."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        self.backend = backend
        self.allowed_actions = list(allowed_actions)
        self.partner, self.partner_id, self.mode = partner, partner_id, mode
        self.skills = copy.deepcopy(skills) or None
        self.briefing = copy.deepcopy(briefing) or None
        self.last_express = None
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
            self.state = initial_state(self.briefing)
        elif profile is not None and profile != self.persona:
            raise ValueError("stable persona cannot change within an episode")
        if role_id != self.persona["id"]:
            raise ValueError("request is addressed to another role")
        if prev_state_id != self.state_id:
            raise ValueError("prev_state_id does not match the latest state")

    def _ingest(self, state, events, role_id, phase):
        changes, last = [], None
        for event in events:
            validate_event(event)
            if role_id not in event.visibility.audience:
                # Contract 3.3.1: the environment filters; we drop and warn as a second check.
                self.warnings.append({"event_id": event.event_id, "warning": "audience excludes role; dropped"})
                continue
            delta = observe(state, event, self.persona, phase)
            if delta is not None:
                changes.extend(delta["changes"])
                last = observation(event)
        return changes, last

    def _commit(self, state, turn, trigger, changes, log_start):
        same_turn = sum(1 for sid in self.snapshots if sid.split("@t")[1].split(".")[0] == f"{turn:03d}")
        state_id = f"{self.persona['id']}@t{turn:03d}" + (f".p{same_turn}" if same_turn else "")
        snapshot = {"state_id": state_id, "role_id": self.persona["id"], "turn": turn,
                    "prev_state_id": self.state_id, "schema_version": state["schema_version"],
                    "psychology_version": state["psychology_version"],
                    "versions": dict(VERSIONS), "stable_snapshot_id": digest(self.persona["stable"]),
                    "stable": copy.deepcopy(self.persona["stable"]),
                    "dynamic": copy.deepcopy(state["dynamic"]),
                    "module_status": copy.deepcopy(state["module_status"]),
                    "memory": list(state["seen_ids"])}
        for entry in state["mechanism_log"][log_start:]:
            entry.update(input_state_id=self.state_id, output_state_id=state_id)
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

    @property
    def mechanism_log(self):
        return self.state["mechanism_log"] if self.state else []

    def latest_state(self):
        return copy.deepcopy(self.snapshots[self.state_id]) if self.state_id else None

    # -- contract interface ------------------------------------------------
    def step(self, req: StepRequest) -> StepResponse:
        try:
            self._check(req.role_id, req.prev_state_id, req.role_profile)
            if "speech" not in req.allowed_kinds:
                raise ValueError("this agent only proposes speech in v0")
            work = copy.deepcopy(self.state)
            log_start = len(work["mechanism_log"])
            changes, trigger = self._ingest(work, req.new_events, req.role_id, "step")
            payload = make_payload(self.persona, work, trigger, req.scene_context, self.partner,
                                   self._allowed(work["memory"]), self.mode, self.skills, self.briefing)
            self.last_payload = payload
            decision = self.backend.decide(copy.deepcopy(payload))
            self.last_decision = decision
            validate_decision(decision, payload)
            views = compile_views(self.persona)
            # Need-to-know: no concealed text, decision guidance or skill names reach this prompt.
            express = {"role": views["role_self"],
                       "style_constraints": [r for r in views["controller_only"]["conditional_rules"]
                                             if r["source_field"].startswith("speech_style.")],
                       "action": decision["action"], "visible_history": copy.deepcopy(payload["visible_history"])}
            if self.skills:
                express["skill_style"] = sk.expression_hints(self.skills, payload["active_skills"])
            if self.briefing:
                express["disclosure"] = expression_view(self.briefing)
            self.last_express = express
            speech = self.backend.express(copy.deepcopy(express))
            text(speech, "speech")
            if len(speech) > 600:
                raise ValueError("speech too long")
            proposal_id = f"{req.role_id}@t{req.turn:03d}#1"
            audience = [req.role_id, self.partner_id]
            action = ProposedAction(proposal_id=proposal_id, kind="speech", content=speech,
                                    proposed_visibility=Visibility("public", audience),
                                    target=[self.partner_id], intent_note=decision["action"])
            mem.add(work["memory"], {"id": proposal_id, "type": "own_action", "actor": req.role_id,
                                     "content": speech, "cues": {}})
            self.intents[proposal_id] = decision["action"]
            state, delta = self._commit(work, req.turn, "step", changes, log_start)
            return StepResponse(req.role_id, req.turn, [action], decision["brief_reason"], state, delta)
        except Exception as exc:
            self.last_error = exc
            return StepResponse(req.role_id, req.turn, [], "", self.latest_state() or {}, {},
                                status="error", error=f"{type(exc).__name__}: {exc}")

    def perceive(self, req: PerceiveRequest) -> PerceiveResponse:
        try:
            self._check(req.role_id, req.prev_state_id, None)
            work = copy.deepcopy(self.state)
            log_start = len(work["mechanism_log"])
            changes, _ = self._ingest(work, req.events, req.role_id, "perceive")
            state, delta = self._commit(work, req.turn, "perceive", changes, log_start)
            return PerceiveResponse(req.role_id, req.turn, state, delta)
        except Exception as exc:
            self.last_error = exc
            return PerceiveResponse(req.role_id, req.turn, self.latest_state() or {}, {},
                                    status="error", error=f"{type(exc).__name__}: {exc}")
