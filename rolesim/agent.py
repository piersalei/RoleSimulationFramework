"""Role agent behind the environment contract: `step` proposes, `perceive` updates.

The agent only sees contract Events whose audience includes its role. It keeps its
own memory and state chain; the environment stores snapshots but never edits them.

An event that arrives without environment cues (another role's speech) is first
appraised from this role's standpoint: the model reports which cues the role perceives
and how strongly (0-1). The mechanism cards alone turn those cues into state changes
(mechanisms.py), and the role reads its state back as level words (stateview.py).
"""
import copy

from . import memory as mem
from . import skills as sk
from .briefing import controller_view, expression_view, recap_entries
from .catalog import digest
from .contract import (PerceiveRequest, PerceiveResponse, ProposedAction, StepRequest,
                       StepResponse, Visibility, validate_event)
from .mechanisms import CARDS, candidate_changes, context, select_cards, triggered
from .persona import bounded, compile_views, text, validate_persona
from .state import (PSYCHOLOGY_VERSION, SCHEMA_VERSION, VERSIONS, activate, commit_phase,
                    new_dynamic, validate_dynamic)
from .stateview import state_view
from .vocab import ACTION_TEXT, CUE_TEXT, CUES

# Ablation levels. State is computed and logged in every mode; the mode decides what the
# prompts may contain: profile_only shows no state anywhere, profile_state shows the state
# view in all three prompts, profile_state_mechanisms adds the mechanism cards.
MODES = ("profile_only", "profile_state", "profile_state_mechanisms")


def initial_state(briefing=None, others=()):
    dynamic, status = new_dynamic(list(others))
    log = []
    # Prototype starting values, not measured baselines: marked origin "default" and
    # written through the audited activation step. Everything else stays unknown/not_modeled.
    base = "dynamic.affect_state.emotion"
    starts = [(f"{base}.label", "紧张"), (f"{base}.intensity", .15)]
    starts += [(f"dynamic.relation_state.{other}.trust", .55) for other in others]
    for path, value in starts:
        activate(dynamic, status, log, path, value, "prototype starting value, not a measurement",
                 ["engineering_default"])
    validate_dynamic(dynamic, status)
    return {"schema_version": SCHEMA_VERSION, "psychology_version": PSYCHOLOGY_VERSION,
            "dynamic": dynamic, "module_status": status, "status_log": log,
            "memory": recap_entries(briefing), "seen_ids": [], "updates": [], "mechanism_log": [], "commits": {}}


def observation(event, cues=None, names=None):
    """Only what the role perceives: no audience list, no environment-side annotations
    beyond cues. `cues` are the role's own appraisal when the event carried none;
    `names` shows speakers as the role knows them (itself as 我, others by title)."""
    item = {"id": event.event_id, "type": event.type, "actor": (names or {}).get(event.actor, event.actor),
            "content": event.content,
            "cues": copy.deepcopy(cues if cues is not None else (event.signals or {}).get("cues", {}))}
    if event.outcome is not None:
        item["outcome"] = copy.deepcopy(event.outcome)
    return item


def observe(state, event, persona, phase="perceive", cues=None, names=None, since=()):
    """Apply one visible contract Event to `state`; return the delta, or None if skipped.

    The mechanism cards produce the candidate changes; they are committed by dependency
    group against one snapshot. A rejected group is recorded in `mechanism_log` and in
    `delta["rejected"]`, never silently dropped.
    """
    if persona["id"] not in event.visibility.audience or event.event_id in state["seen_ids"]:
        return None
    dynamic = state["dynamic"]
    perceived = cues if cues is not None else (event.signals or {}).get("cues", {})
    others = list(dynamic["relation_state"])
    ctx = context(persona, dynamic, perceived, others, event.actor if event.actor in others else None,
                  since, event, appraised=cues is not None)
    fired = triggered(ctx)
    delta = {"trigger": phase, "event_id": event.event_id, "changes": candidate_changes(ctx, event.event_id),
             "rule_version": VERSIONS["update_rule_version"]}
    known = {item["id"] for item in state["memory"]} | {event.event_id}
    key = f"{persona['id']}/{event.event_id}/{phase}"
    state["dynamic"], adopted, log = commit_phase(dynamic, state["module_status"], delta, known,
                                                  commit_key=key, committed=state["commits"])
    log["cards"] = fired
    delta["changes"] = adopted
    delta["rejected"] = log["rejected"]
    state["mechanism_log"].append(log)
    mem.add(state["memory"], observation(event, cues, names))
    state["seen_ids"].append(event.event_id)
    state["updates"].append(delta)
    return delta


def _query(trigger, briefing):
    return " ".join(filter(None, [(trigger or {}).get("content"), (briefing or {}).get("goal")]))


def appraisal_payload(persona, state, event_view, scene_context, others, seat=None, briefing=None, view=None):
    """Appraisal prompt. `view` is the role's state as level words, or None when the mode or
    the --appraise-state switch keeps state out of appraisal."""
    views = compile_views(persona)
    data = {"role": views["role_self"], "behavioral_constraints": views["controller_only"],
            "scene_background": scene_context, "self_seat": seat, "others_present": list(others),
            "visible_history": mem.recall(state["memory"], _query(event_view, briefing)),
            "event": {k: event_view[k] for k in ("id", "type", "actor", "content")},
            "cue_definitions": dict(CUE_TEXT)}
    if view is not None:
        data["current_state"] = view
    if briefing:
        data["briefing"] = controller_view(briefing)
    return data


def validate_appraisal(result):
    if not isinstance(result, dict) or set(result) != {"cues", "reason"}:
        raise ValueError("appraisal must contain cues and reason only")
    cues = result["cues"]
    if not isinstance(cues, dict) or not set(cues) <= CUES:
        raise ValueError("appraisal: unsupported cue")
    for key, value in cues.items():
        bounded(value, 0, 1, f"appraisal cue {key}")
    text(result["reason"], "appraisal reason")
    if len(result["reason"]) > 200:
        raise ValueError("appraisal reason too long")


def make_payload(persona, state, trigger, scene_context, others, allowed, mode, skills=None, briefing=None,
                 seat=None, previous_actions=(), view=None, cards=None):
    """Decision prompt. Skills and briefing belong to the profile, so they appear in every mode;
    `view` (state with numbers) and `cards` (selected mechanism cards) follow the mode."""
    views = compile_views(persona)
    if not allowed:
        raise ValueError("no feasible action remains")
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    data = {"role": views["role_self"], "behavioral_constraints": views["controller_only"],
            "scene_background": scene_context, "self_seat": seat, "others_present": list(others),
            "visible_history": mem.recall(state["memory"], _query(trigger, briefing)),
            "respond_to": copy.deepcopy(trigger),
            "my_previous_actions": list(previous_actions)[-3:],
            "allowed_actions": list(allowed),
            "action_meanings": {a: ACTION_TEXT[a] for a in allowed}}
    if briefing:
        data["briefing"] = controller_view(briefing)
    if skills:
        data["skill_catalog"] = sk.catalog(skills)
        data["active_skills"] = sk.select(skills, trigger, allowed, briefing)
    if mode != "profile_only" and view is not None:
        data["current_state"] = view
    if mode == "profile_state_mechanisms":
        data["active_mechanisms"] = list(cards or [])
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
    def __init__(self, backend, allowed_actions, others, mode="profile_state_mechanisms",
                 skills=None, briefing=None, seat=None, appraise_state=True):
        """`others` maps every other role present to its seat title, in seat order; `seat` is
        this role's own seat title. `skills` is a bound skill list (skills.bind); `briefing`
        is this role's validated opening setup. `appraise_state=False` keeps the state out of
        the appraisal prompt in every mode, as a clean control."""
        if mode not in MODES:
            raise ValueError(f"unknown mode: {mode}")
        if not isinstance(others, dict) or not others:
            raise ValueError("others: map each other role id to its seat title")
        self.backend = backend
        self.allowed_actions = list(allowed_actions)
        self.others, self.mode, self.seat = dict(others), mode, seat
        self.appraise_state = bool(appraise_state)
        self.skills = copy.deepcopy(skills) or None
        self.briefing = copy.deepcopy(briefing) or None
        self.scene_context = None
        self.persona = None
        self.state = None
        self.state_id = None
        self.snapshots = {}     # state_id -> AgentState, for replay
        self.actions = []       # moves this role has made, in order
        self.since = []         # committed changes since this role last decided
        self.warnings = []
        self.last_payload = self.last_decision = self.last_express = self.last_error = None
        self.last_appraisals, self.last_reasoning = [], {}

    # -- helpers -----------------------------------------------------------
    def _check(self, role_id, prev_state_id, profile):
        if self.persona is None:
            if profile is None:
                raise ValueError("role_profile is required on the first call")
            validate_persona(profile)
            if set(profile["stable"]["mechanism_priors"]) != {c["id"] for c in CARDS}:
                raise ValueError("mechanism priors must match the enabled card registry")
            if profile["id"] in self.others:
                raise ValueError("a role cannot be among its own others")
            self.persona = copy.deepcopy(profile)
            self.state = initial_state(self.briefing, list(self.others))
        elif profile is not None and profile != self.persona:
            raise ValueError("stable persona cannot change within an episode")
        if role_id != self.persona["id"]:
            raise ValueError("request is addressed to another role")
        if prev_state_id != self.state_id:
            raise ValueError("prev_state_id does not match the latest state")

    @property
    def names(self):
        return {self.persona["id"]: "我", **self.others, "env": "旁白"}

    @property
    def titles(self):
        return list(self.others.values())

    @property
    def shows_state(self):
        return self.mode != "profile_only"

    def _view(self, state, since=(), numbers=False):
        return state_view(state, self.others, since, numbers=numbers, mechanisms=numbers)

    def _model(self, stage, call, payload):
        result = call(copy.deepcopy(payload))
        reasoning = getattr(self.backend, "last_reasoning", None)
        if reasoning:
            self.last_reasoning.setdefault(stage, []).append(reasoning)
        return result

    def _appraise(self, state, event):
        view = self._view(state) if self.shows_state and self.appraise_state else None
        payload = appraisal_payload(self.persona, state, observation(event, {}, self.names), self.scene_context,
                                    self.titles, self.seat, self.briefing, view)
        result = self._model("appraise", self.backend.appraise, payload)
        self.last_appraisals.append({"event_id": event.event_id, "input": payload, "output": result})
        validate_appraisal(result)
        return copy.deepcopy(result["cues"])

    def _ingest(self, state, events, role_id, phase):
        changes, last, last_actor = [], None, None
        for event in events:
            validate_event(event)
            if role_id not in event.visibility.audience:
                # Contract 3.3.1: the environment filters; we drop and warn as a second check.
                self.warnings.append({"event_id": event.event_id, "warning": "audience excludes role; dropped"})
                continue
            if event.event_id in state["seen_ids"]:
                continue
            cues = None
            if event.actor != role_id and not (event.signals or {}).get("cues"):
                cues = self._appraise(state, event)
            delta = observe(state, event, self.persona, phase, cues, self.names, self.since + changes)
            if delta is not None:
                changes.extend(delta["changes"])
                if event.actor != role_id:
                    last, last_actor = observation(event, cues, self.names), event.actor
        return changes, last, last_actor

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

    def _express_payload(self, work, trigger, decision, payload, since):
        views = compile_views(self.persona)
        own = [m["content"] for m in work["memory"] if m["type"] == "speech" and m["actor"] == "我"]
        # Need-to-know: no concealed text, decision guidance, skill names, mechanism names or
        # state numbers reach this prompt.
        express = {"role": views["role_self"],
                   "style_constraints": [r for r in views["controller_only"]["conditional_rules"]
                                         if r["source_field"].startswith("speech_style.")],
                   "self_seat": self.seat, "others_present": self.titles,
                   "respond_to": None if trigger is None else {"speaker": trigger["actor"],
                                                               "content": trigger["content"]},
                   "action": decision["action"], "action_meaning": ACTION_TEXT[decision["action"]],
                   "my_previous_lines": own[-3:],
                   "visible_history": copy.deepcopy(payload["visible_history"])}
        if self.shows_state:
            express["current_feeling"] = self._view(work, since)
        if self.skills:
            express["skill_style"] = sk.expression_hints(self.skills, payload["active_skills"])
        if self.briefing:
            express["disclosure"] = expression_view(self.briefing)
        return express

    @property
    def mechanism_log(self):
        return self.state["mechanism_log"] if self.state else []

    def latest_state(self):
        return copy.deepcopy(self.snapshots[self.state_id]) if self.state_id else None

    # -- contract interface ------------------------------------------------
    def step(self, req: StepRequest) -> StepResponse:
        self.last_appraisals, self.last_reasoning = [], {}
        self.last_payload = self.last_decision = self.last_express = None
        try:
            self._check(req.role_id, req.prev_state_id, req.role_profile)
            if "speech" not in req.allowed_kinds:
                raise ValueError("this agent only proposes speech in v0")
            self.scene_context = req.scene_context
            work = copy.deepcopy(self.state)
            log_start = len(work["mechanism_log"])
            changes, trigger, actor = self._ingest(work, req.new_events, req.role_id, "step")
            since = self.since + changes
            ctx = context(self.persona, work["dynamic"], (trigger or {}).get("cues", {}), list(self.others),
                          actor if actor in self.others else None, since)
            cards = select_cards(self.persona, ctx, self.allowed_actions) if self.mode == MODES[2] else None
            view = self._view(work, since, numbers=True) if self.shows_state else None
            payload = make_payload(self.persona, work, trigger, req.scene_context, self.titles,
                                   self.allowed_actions, self.mode, self.skills, self.briefing, self.seat,
                                   self.actions, view, cards)
            self.last_payload = payload
            decision = self._model("decide", self.backend.decide, payload)
            self.last_decision = decision
            validate_decision(decision, payload)
            express = self.last_express = self._express_payload(work, trigger, decision, payload, since)
            speech = self._model("express", self.backend.express, express)
            text(speech, "speech")
            if len(speech) > 600:
                raise ValueError("speech too long")
            proposal_id = f"{req.role_id}@t{req.turn:03d}#1"
            action = ProposedAction(proposal_id=proposal_id, kind="speech", content=speech,
                                    proposed_visibility=Visibility("public", [req.role_id, *self.others]),
                                    target=list(self.others), intent_note=decision["action"])
            # The line enters memory when the environment reports it as said (perceive).
            state, delta = self._commit(work, req.turn, "step", changes, log_start)
            self.actions.append(decision["action"])
            self.since = []
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
            changes, _, _ = self._ingest(work, req.events, req.role_id, "perceive")
            state, delta = self._commit(work, req.turn, "perceive", changes, log_start)
            self.since += changes
            return PerceiveResponse(req.role_id, req.turn, state, delta)
        except Exception as exc:
            self.last_error = exc
            return PerceiveResponse(req.role_id, req.turn, self.latest_state() or {}, {},
                                    status="error", error=f"{type(exc).__name__}: {exc}")
