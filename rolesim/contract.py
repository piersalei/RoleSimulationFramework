"""Data objects of the environment-group interface contract v0 (data/sources/contracts/).

All objects are JSON-serialisable dataclasses for in-process calls. Field names follow
the contract appendix. `Event.signals` is a local extension, see below.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional

EVENT_TYPES = {"speech", "action", "narration", "outcome"}
OUTCOME_STATUS = {"success", "partial", "failed", "blocked"}
SCOPES = {"public", "private"}


@dataclass
class Visibility:
    scope: str
    audience: list


@dataclass
class Event:
    event_id: str
    turn: int
    type: str
    actor: str
    content: str
    visibility: Visibility
    target: list = field(default_factory=list)
    outcome: Optional[dict] = None
    caused_by: Optional[str] = None
    # Extension, not in contract v0: perceptual cues annotated by the scripted test
    # environment ({"cues": {...}, "reliability_signal": x}). A real environment may omit
    # it; the agent then treats the event as carrying no cue.
    signals: Optional[dict] = None


@dataclass
class ProposedAction:
    proposal_id: str
    kind: str
    content: str
    proposed_visibility: Visibility
    target: list = field(default_factory=list)
    # Private note for adjudication; never copied into any Event. The scripted
    # environment reads the chosen action label from here.
    intent_note: Optional[str] = None


@dataclass
class StepRequest:
    role_id: str
    turn: int
    scene_context: str
    new_events: list
    prev_state_id: Optional[str]
    role_profile: Optional[dict] = None
    allowed_kinds: list = field(default_factory=lambda: ["speech", "action"])


@dataclass
class StepResponse:
    role_id: str
    turn: int
    actions: list
    thought: str
    state: dict
    delta: dict
    status: str = "ok"
    error: Optional[str] = None


@dataclass
class PerceiveRequest:
    role_id: str
    turn: int
    events: list
    prev_state_id: Optional[str]


@dataclass
class PerceiveResponse:
    role_id: str
    turn: int
    state: dict
    delta: dict
    status: str = "ok"
    error: Optional[str] = None


def to_dict(obj):
    return asdict(obj)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def validate_event(event):
    if not isinstance(event, Event):
        raise ValueError("expected a contract Event")
    _text(event.event_id, "event_id")
    _text(event.actor, "actor")
    _text(event.content, "content")
    if isinstance(event.turn, bool) or not isinstance(event.turn, int) or event.turn < 1:
        raise ValueError("turn must be an integer >= 1")
    if event.type not in EVENT_TYPES:
        raise ValueError("unsupported event type")
    if not isinstance(event.visibility, Visibility) or event.visibility.scope not in SCOPES:
        raise ValueError("unsupported visibility")
    if not isinstance(event.visibility.audience, list) or not event.visibility.audience:
        raise ValueError("visibility.audience must be a non-empty list")
    if (event.type == "outcome") != (event.outcome is not None):
        raise ValueError("outcome is required for, and only for, type=outcome")
    if event.outcome is not None:
        if set(event.outcome) != {"of_proposal", "status", "detail"} or event.outcome["status"] not in OUTCOME_STATUS:
            raise ValueError("outcome must be {of_proposal, status, detail} with a known status")
    if event.signals is not None and not isinstance(event.signals, dict):
        raise ValueError("signals must be an object")
