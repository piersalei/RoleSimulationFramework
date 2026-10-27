"""Agent interface v1: the runner (environment group) calls `act` / `perceive` on role agents
and `act` on the environment actor (data/sources/contracts/Agent接口定义_v1.md).

This module is the executable form of that definition: the data shapes, the checks each side
runs on what it receives, and the runner-side helpers that turn one act result into
interaction records and decide who sees them. It does not implement any agent.

Identity: a role is addressed by its `name`, unique within an episode; this side maps the
name to its own role. Character ids are the runner's and only appear as keys of
`present_characters` / `recipients` and in `recipient_ids`. `present_characters` never
contains the acting role itself, so a thought carries `recipient_ids=None` and the runner
shows it to the actor only.
"""
from typing import Literal, Optional, Protocol, TypedDict

KINDS = ("thought", "speech", "action")
COGNITION_KEYS = ("knowledge", "beliefs", "emotions", "motivations", "goals")
ENV_ID = "env"
ENV_NAME = "环境"

Kind = Literal["thought", "speech", "action"]


class Setting(TypedDict):
    place: Optional[str]
    time_text: Optional[str]


class Present(TypedDict):
    name: str


class CognitionBasis(TypedDict):
    knowledge: Optional[str]
    beliefs: Optional[str]
    emotions: Optional[str]
    motivations: Optional[str]
    goals: Optional[str]


class Actor(TypedDict):
    actor_id: str
    name: str


class InteractionRecord(TypedDict):
    action_id: str
    turn_index: int
    order_index: int
    actor: Actor
    kind: Kind
    content: str
    recipients: dict[str, Present]


class CharacterView(TypedDict):
    setting: Setting
    trigger_facts: list[str]
    name: str
    profile_summary: str
    relationships: list[str]
    history_memory: Optional[str]
    personality: dict           # opaque until its fields are agreed
    mechanism: dict             # opaque until its fields are agreed
    cognition: CognitionBasis
    observations: list[InteractionRecord]
    present_characters: dict[str, Present]


class ActionPart(TypedDict):
    content: str
    recipient_ids: Optional[list[str]]


class AgentActionContent(TypedDict):
    thought: Optional[ActionPart]
    speech: Optional[ActionPart]
    action: Optional[ActionPart]
    order: list[Kind]


class AgentActionResult(TypedDict):
    cognition: CognitionBasis
    action: AgentActionContent


class CharacterPerceptionInput(TypedDict):
    character_view: CharacterView
    new_observations: list[InteractionRecord]


class AgentPerceptionResult(TypedDict):
    cognition: CognitionBasis


class EnvironmentActorView(TypedDict):
    setting: Setting
    story_background: str
    trigger_facts: list[str]
    present_characters: dict[str, Present]
    interaction_history: list[InteractionRecord]
    environment_directive: str


class AgentAdapter(Protocol):
    async def act(self, context: CharacterView, request_id: str) -> AgentActionResult: ...

    async def perceive(self, context: CharacterPerceptionInput, request_id: str) -> AgentPerceptionResult: ...


class EnvironmentAdapter(Protocol):
    async def act(self, context: EnvironmentActorView, request_id: str) -> AgentActionContent: ...


class InterfaceError(ValueError):
    """A payload breaks the interface definition. Raised instead of returning partial output."""


# -- checks ------------------------------------------------------------------
def _fail(where, message):
    raise InterfaceError(f"{where}: {message}")


def _keys(value, expected, where):
    if not isinstance(value, dict) or set(value) != set(expected):
        _fail(where, f"expected exactly the fields {sorted(expected)}")


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        _fail(where, "must be a non-empty string")


def _optional_text(value, where):
    if value is not None:
        _text(value, where)


def _index(value, where, low):
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        _fail(where, f"must be an integer >= {low}")


def _texts(value, where):
    if not isinstance(value, list):
        _fail(where, "must be a list")
    for i, item in enumerate(value):
        _text(item, f"{where}[{i}]")


def _setting(value, where):
    _keys(value, Setting.__annotations__, where)
    for key in Setting.__annotations__:
        _optional_text(value[key], f"{where}.{key}")


def _present(value, where):
    if not isinstance(value, dict):
        _fail(where, "must be an object keyed by character id")
    for cid, entry in value.items():
        _text(cid, f"{where} key")
        _keys(entry, {"name"}, f"{where}.{cid}")
        _text(entry["name"], f"{where}.{cid}.name")
    names = [e["name"] for e in value.values()]
    if len(set(names)) != len(names):
        _fail(where, "names must be unique: roles are addressed by name")


def validate_cognition(value, where="cognition"):
    _keys(value, COGNITION_KEYS, where)
    for key in COGNITION_KEYS:
        _optional_text(value[key], f"{where}.{key}")


def validate_record(record, where="record"):
    _keys(record, InteractionRecord.__annotations__, where)
    _text(record["action_id"], f"{where}.action_id")
    _index(record["turn_index"], f"{where}.turn_index", 1)
    _index(record["order_index"], f"{where}.order_index", 0)
    _keys(record["actor"], Actor.__annotations__, f"{where}.actor")
    _text(record["actor"]["actor_id"], f"{where}.actor.actor_id")
    _text(record["actor"]["name"], f"{where}.actor.name")
    if record["kind"] not in KINDS:
        _fail(where, f"kind must be one of {KINDS}")
    _text(record["content"], f"{where}.content")
    _present(record["recipients"], f"{where}.recipients")
    if record["kind"] == "thought" and set(record["recipients"]) != {record["actor"]["actor_id"]}:
        _fail(where, "a thought is visible to its actor only")
    if record["actor"]["actor_id"] == ENV_ID and record["kind"] == "thought":
        _fail(where, "the environment has no thoughts")


def record_id(record):
    """Stable id of one record; role state changes cite it as evidence."""
    return f"{record['action_id']}#{record['order_index']}"


def _records(records, where):
    if not isinstance(records, list):
        _fail(where, "must be a list")
    seen, last = set(), (0, -1)
    for i, record in enumerate(records):
        validate_record(record, f"{where}[{i}]")
        key = record_id(record)
        if key in seen:
            _fail(f"{where}[{i}]", f"duplicate record {key}")
        seen.add(key)
        position = (record["turn_index"], record["order_index"])
        if position <= last:
            _fail(f"{where}[{i}]", "records must be in occurrence order (turn_index, order_index)")
        last = position
    return seen


def sees(record, name):
    """True when the role called `name` may see `record`: it acted, or it is a recipient."""
    if record["actor"]["name"] == name:
        return True
    return record["kind"] != "thought" and any(e["name"] == name for e in record["recipients"].values())


def validate_character_view(view, where="character_view"):
    _keys(view, CharacterView.__annotations__, where)
    _setting(view["setting"], f"{where}.setting")
    _texts(view["trigger_facts"], f"{where}.trigger_facts")
    _text(view["name"], f"{where}.name")
    _text(view["profile_summary"], f"{where}.profile_summary")
    _texts(view["relationships"], f"{where}.relationships")
    _optional_text(view["history_memory"], f"{where}.history_memory")
    for key in ("personality", "mechanism"):
        if not isinstance(view[key], dict):
            _fail(f"{where}.{key}", "must be an object")
    validate_cognition(view["cognition"], f"{where}.cognition")
    _records(view["observations"], f"{where}.observations")
    _present(view["present_characters"], f"{where}.present_characters")
    if any(e["name"] == view["name"] for e in view["present_characters"].values()):
        _fail(f"{where}.present_characters", "must not contain the acting role itself")
    for i, record in enumerate(view["observations"]):
        if not sees(record, view["name"]):
            _fail(f"{where}.observations[{i}]", "record is not visible to this role")


def validate_perception_input(value, where="perception_input"):
    _keys(value, CharacterPerceptionInput.__annotations__, where)
    view = value["character_view"]
    validate_character_view(view, f"{where}.character_view")
    new = _records(value["new_observations"], f"{where}.new_observations")
    if not value["new_observations"]:
        _fail(f"{where}.new_observations", "perceive is called only when there is something new")
    if new & {record_id(r) for r in view["observations"]}:
        _fail(f"{where}.new_observations", "records already in character_view.observations")
    for i, record in enumerate(value["new_observations"]):
        if record["actor"]["name"] == view["name"]:
            _fail(f"{where}.new_observations[{i}]", "a role does not perceive its own records")
        if not sees(record, view["name"]):
            _fail(f"{where}.new_observations[{i}]", "record is not visible to this role")


def validate_action_content(content, present, where="action", environment=False):
    """`present` is the caller's present_characters; recipient ids must come from it."""
    _keys(content, AgentActionContent.__annotations__, where)
    filled = [k for k in KINDS if content[k] is not None]
    if not filled:
        _fail(where, "at least one of thought, speech, action must be filled")
    order = content["order"]
    if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(filled):
        _fail(f"{where}.order", "must list each filled part exactly once, and nothing else")
    for kind in filled:
        part = content[kind]
        _keys(part, ActionPart.__annotations__, f"{where}.{kind}")
        _text(part["content"], f"{where}.{kind}.content")
        ids = part["recipient_ids"]
        if kind == "thought":
            if ids is not None:
                _fail(f"{where}.thought.recipient_ids", "must be null: a thought stays with its actor")
            continue
        if ids is not None:
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
                _fail(f"{where}.{kind}.recipient_ids", "null, or a non-empty list of distinct ids")
            unknown = set(ids) - set(present)
            if unknown:
                _fail(f"{where}.{kind}.recipient_ids", f"not present: {sorted(unknown)}")
    if environment and content["thought"] is not None:
        _fail(f"{where}.thought", "the environment returns no thought")


def validate_action_result(result, view, where="act_result"):
    _keys(result, AgentActionResult.__annotations__, where)
    validate_cognition(result["cognition"], f"{where}.cognition")
    validate_action_content(result["action"], view["present_characters"], f"{where}.action")


def validate_perception_result(result, where="perceive_result"):
    _keys(result, AgentPerceptionResult.__annotations__, where)
    validate_cognition(result["cognition"], f"{where}.cognition")


def validate_environment_view(view, where="environment_view"):
    _keys(view, EnvironmentActorView.__annotations__, where)
    _setting(view["setting"], f"{where}.setting")
    _text(view["story_background"], f"{where}.story_background")
    _texts(view["trigger_facts"], f"{where}.trigger_facts")
    _present(view["present_characters"], f"{where}.present_characters")
    _records(view["interaction_history"], f"{where}.interaction_history")
    for i, record in enumerate(view["interaction_history"]):
        if record["kind"] == "thought":
            _fail(f"{where}.interaction_history[{i}]", "role thoughts are never shown to the environment")
    _text(view["environment_directive"], f"{where}.environment_directive")


def validate_environment_content(content, view, where="environment_action"):
    validate_action_content(content, view["present_characters"], where, environment=True)


# -- runner-side helpers -------------------------------------------------------
def split(content, actor_id, actor_name, present, action_id, turn_index, first_order):
    """Turn one act result into records, in `content["order"]`. `present` is the
    present_characters the actor saw; recipient_ids=None means all of them. A thought's
    only recipient is its actor."""
    records = []
    for offset, kind in enumerate(content["order"]):
        part = content[kind]
        if kind == "thought":
            recipients = {actor_id: {"name": actor_name}}
        else:
            ids = part["recipient_ids"] if part["recipient_ids"] is not None else list(present)
            recipients = {cid: {"name": present[cid]["name"]} for cid in ids}
        records.append({"action_id": action_id, "turn_index": turn_index, "order_index": first_order + offset,
                        "actor": {"actor_id": actor_id, "name": actor_name}, "kind": kind,
                        "content": part["content"], "recipients": recipients})
    return records


def deliveries(records, actor_name, names):
    """For each role in `names` other than the actor: the records it sees, in order.
    Roles that see nothing are left out (no perceive call for them)."""
    out = {}
    for name in names:
        if name == actor_name:
            continue
        seen = [r for r in records if sees(r, name)]
        if seen:
            out[name] = seen
    return out


def environment_history(records):
    """What the environment may read: every record except role thoughts."""
    return [r for r in records if r["kind"] != "thought"]


# -- this side: name-based routing -----------------------------------------------
class Directory:
    """Maps the runner's role names to this side's role ids. The runner never needs our ids:
    a view names its role, and present characters / record actors are matched by name."""

    def __init__(self, roles):
        """`roles` maps role name to this side's role id (e.g. persona name -> persona id)."""
        if not isinstance(roles, dict) or not roles:
            raise InterfaceError("directory: map each role name to a role id")
        if len(set(roles.values())) != len(roles):
            raise InterfaceError("directory: one name per role id")
        self.by_name = dict(roles)

    @classmethod
    def from_personas(cls, personas):
        return cls({p["name"]: p["id"] for p in personas})

    def role(self, name):
        if name not in self.by_name:
            raise InterfaceError(f"directory: no role is called {name!r}")
        return self.by_name[name]

    def present(self, present_characters):
        """Runner character id -> our role id, for everyone present."""
        return {cid: self.role(entry["name"]) for cid, entry in present_characters.items()}

    def actor(self, record):
        """Our role id of a record's actor, or ENV_ID for the environment."""
        if record["actor"]["actor_id"] == ENV_ID:
            return ENV_ID
        return self.role(record["actor"]["name"])
