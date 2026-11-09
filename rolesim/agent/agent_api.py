"""Agent interface v2: what the runner (environment group) calls on this side
(data/sources/contracts/Agent接口说明_环境组_2026-11-07.md).

Three calls:

- `IRoleAgent.initialize(interaction_id, character_id, bootstrap)`: once per character at the
  start of an interaction; returns nothing.
- `IRoleAgent.act(interaction_id, character_id, context, request_id)`: the character acts;
  `context` carries every record it has seen so far and who is present. Returns thought /
  speech / action and their order, nothing else (no cognition, no ids).
- `IEnvironment.act(interaction_id, context, request_id)`: an objective environment reaction.

The environment group's document calls the two sides `AgentAdapter` and `EnvironmentAdapter`;
here they are `IRoleAgent` (we implement it) and `IEnvironment` (they implement it), so every
interface in the framework reads as `I...` (see interfaces.py).

There is no perceive call: a character takes in what it has seen when it is next asked to act.
Routing is by `interaction_id + character_id`; an interaction is also what tells branches apart.
The runner validates actions, resolves recipients, builds records and keeps observations.

This module is the executable form of that definition: data shapes, the checks each side runs on
what it receives, and runner-side helpers used by the local reference runner (loop.py). It does
not implement any agent.

Agreed with the environment group (plan/08, section 8.1):
- Character ids are fixed in advance and stay the same across interactions: they are the ids of
  a static, extensible role library (here: the role files under roles/).
- `present_characters` in a turn input contains the acting character itself.
- `bootstrap.personality` may name a role file (`role_ref`); `bootstrap.mechanism` may carry the
  ablation mode (`mode`) and an enable-config id (`enable_config`).
Our own reading: a thought may be addressed to others; ours always go to the actor only.
Character names need not be unique: everything is matched by character id.
"""
from typing import Literal, Optional, Protocol, TypedDict

KINDS = ("thought", "speech", "action")
ENV_ID = "environment"
ENV_NAME = "环境"

Kind = Literal["thought", "speech", "action"]


class Setting(TypedDict):
    place: Optional[str]
    time_text: Optional[str]


class Present(TypedDict):
    name: str


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


class CharacterBootstrap(TypedDict):
    setting: Setting
    trigger_facts: list[str]
    name: str
    profile_summary: str
    core_goals: Optional[str]
    relationships: list[str]
    history_memory: Optional[str]
    personality: dict           # branch-specific personality config (see ROLE_REF)
    mechanism: dict             # branch-specific action-mechanism config, kept on record


class CharacterTurnInput(TypedDict):
    bootstrap: None             # already sent by initialize
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


AgentActionResult = AgentActionContent


class EnvironmentActorView(TypedDict):
    setting: Setting
    story_background: str
    trigger_facts: list[str]
    present_characters: dict[str, Present]
    environment_directive: str


class IRoleAgent(Protocol):
    """The role side. Ours: staged.StagedRoleAgent. (Environment group: AgentAdapter.)"""

    async def initialize(self, interaction_id: str, character_id: str, bootstrap: CharacterBootstrap) -> None: ...

    async def act(self, interaction_id: str, character_id: str, context: CharacterTurnInput,
                  request_id: str) -> AgentActionResult: ...


class IEnvironment(Protocol):
    """The environment side. Theirs; locally loop.EchoEnvironment. (Environment group: EnvironmentAdapter.)"""

    async def act(self, interaction_id: str, context: EnvironmentActorView, request_id: str) -> AgentActionContent: ...


# Optional keys this side reads from the branch configs. `personality.role_ref` names a role
# file when it differs from the character id; `mechanism.mode` is an ablation mode;
# `mechanism.enable_config` names an enable configuration (kept on record, not applied yet).
ROLE_REF = "role_ref"
MODE = "mode"
ENABLE_CONFIG = "enable_config"


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
        if cid == ENV_ID:
            _fail(where, f"{ENV_ID!r} is the environment, not a character")
        _keys(entry, {"name"}, f"{where}.{cid}")
        _text(entry["name"], f"{where}.{cid}.name")


def check_id(value, where):
    _text(value, where)
    if value == ENV_ID:
        _fail(where, f"{ENV_ID!r} is reserved for the environment")


def validate_bootstrap(bootstrap, where="bootstrap"):
    _keys(bootstrap, CharacterBootstrap.__annotations__, where)
    _setting(bootstrap["setting"], f"{where}.setting")
    _texts(bootstrap["trigger_facts"], f"{where}.trigger_facts")
    _text(bootstrap["name"], f"{where}.name")
    _text(bootstrap["profile_summary"], f"{where}.profile_summary")
    _optional_text(bootstrap["core_goals"], f"{where}.core_goals")
    _texts(bootstrap["relationships"], f"{where}.relationships")
    _optional_text(bootstrap["history_memory"], f"{where}.history_memory")
    for key in ("personality", "mechanism"):
        if not isinstance(bootstrap[key], dict):
            _fail(f"{where}.{key}", "must be an object")
    if ROLE_REF in bootstrap["personality"]:
        _text(bootstrap["personality"][ROLE_REF], f"{where}.personality.{ROLE_REF}")
    for key in (MODE, ENABLE_CONFIG):
        if key in bootstrap["mechanism"]:
            _text(bootstrap["mechanism"][key], f"{where}.mechanism.{key}")


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
    if not record["recipients"]:
        _fail(f"{where}.recipients", "a record has at least one explicit recipient")


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


def sees(record, character_id):
    """True when the character may see `record`: it acted, or it is an explicit recipient."""
    return record["actor"]["actor_id"] == character_id or character_id in record["recipients"]


def validate_turn_input(context, character_id, where="turn_input"):
    _keys(context, CharacterTurnInput.__annotations__, where)
    if context["bootstrap"] is not None:
        _fail(f"{where}.bootstrap", "must be null: the bootstrap was sent by initialize")
    _records(context["observations"], f"{where}.observations")
    _present(context["present_characters"], f"{where}.present_characters")
    if character_id not in context["present_characters"]:
        _fail(f"{where}.present_characters", "must contain the acting character itself")
    if not set(context["present_characters"]) - {character_id}:
        _fail(f"{where}.present_characters", "nobody else is present to act towards")
    for i, record in enumerate(context["observations"]):
        if not sees(record, character_id):
            _fail(f"{where}.observations[{i}]", "record is not visible to this character")


def validate_action_content(content, present, actor_id, where="action"):
    """`present` is the present_characters the actor was given; recipient ids must come
    from it. The actor's own id is always allowed (a private thought)."""
    _keys(content, AgentActionContent.__annotations__, where)
    filled = [k for k in KINDS if content[k] is not None]
    if not filled:
        _fail(where, "at least one of thought, speech, action must be filled")
    order = content["order"]
    if not isinstance(order, list) or len(order) != len(set(order)) or set(order) != set(filled):
        _fail(f"{where}.order", "must list each filled part exactly once, and nothing else")
    allowed = set(present) | ({actor_id} if actor_id != ENV_ID else set())
    for kind in filled:
        part = content[kind]
        _keys(part, ActionPart.__annotations__, f"{where}.{kind}")
        _text(part["content"], f"{where}.{kind}.content")
        ids = part["recipient_ids"]
        if ids is None:
            continue
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            _fail(f"{where}.{kind}.recipient_ids", "null, or a non-empty list of distinct ids")
        unknown = set(ids) - allowed
        if unknown:
            _fail(f"{where}.{kind}.recipient_ids", f"not present: {sorted(unknown)}")


def validate_action_result(result, context, character_id, where="act_result"):
    validate_action_content(result, context["present_characters"], character_id, where)


def validate_environment_view(view, where="environment_view"):
    _keys(view, EnvironmentActorView.__annotations__, where)
    _setting(view["setting"], f"{where}.setting")
    _text(view["story_background"], f"{where}.story_background")
    _texts(view["trigger_facts"], f"{where}.trigger_facts")
    _present(view["present_characters"], f"{where}.present_characters")
    _text(view["environment_directive"], f"{where}.environment_directive")


def validate_environment_content(content, view, where="environment_action"):
    validate_action_content(content, view["present_characters"], ENV_ID, where)


# -- runner-side helpers (local reference runner) ---------------------------------
def split(content, actor_id, actor_name, present, action_id, turn_index, first_order):
    """Turn one act result into records, in `content["order"]`. `present` is the
    present_characters the actor was given; recipient_ids=None means all of them."""
    records = []
    names = {**present, actor_id: {"name": actor_name}}
    for offset, kind in enumerate(content["order"]):
        part = content[kind]
        ids = part["recipient_ids"] if part["recipient_ids"] is not None else list(present)
        recipients = {cid: {"name": names[cid]["name"]} for cid in ids}
        records.append({"action_id": action_id, "turn_index": turn_index, "order_index": first_order + offset,
                        "actor": {"actor_id": actor_id, "name": actor_name}, "kind": kind,
                        "content": part["content"], "recipients": recipients})
    return records


def visible(records, character_id):
    """The records a character sees, in order."""
    return [r for r in records if sees(r, character_id)]
