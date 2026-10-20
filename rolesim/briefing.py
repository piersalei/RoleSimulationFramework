"""Per-role briefing carried by the scene package (scene schema 1.1).

A briefing initialises one role for one scene: its interaction goal, what it wants
the counterpart to know (`disclose`), what it knows but does not want to reveal
(`conceal`), and a recap of what happened before the scene (`recap`, seeded into the
role's memory). It is scene data, not persona data: the persona schema is unchanged.

Information isolation: concealed text reaches the decision controller (the role knows
it) but never the expression prompt, so the spoken line cannot quote it. `markers`
are keywords the audit uses to detect a leak or a disclosure in the public speech.
"""
from .persona import text

KEYS = {"goal", "disclose", "conceal", "recap"}
# Concealment strategies from decision library 6.7 that do not assert falsehoods.
STRATEGIES = {"omission", "strategic_ambiguity"}
RECAP_PREFIX = "recap:"


def _markers(value, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label}: markers must be a non-empty list")
    for marker in value:
        text(marker, label)
        if len(marker.strip()) < 2:
            raise ValueError(f"{label}: a marker needs at least two characters")


def validate_briefing(briefing, reserved_ids=()):
    if not isinstance(briefing, dict) or set(briefing) != KEYS:
        raise ValueError(f"briefing: expected fields {sorted(KEYS)}")
    text(briefing["goal"], "briefing goal")
    shapes = {"disclose": {"id", "text", "markers"}, "conceal": {"id", "text", "markers", "strategy"},
              "recap": {"id", "text"}}
    seen = set(reserved_ids)
    for key, fields in shapes.items():
        items = briefing[key]
        if not isinstance(items, list):
            raise ValueError(f"briefing {key} must be a list")
        for item in items:
            if not isinstance(item, dict) or set(item) != fields:
                raise ValueError(f"briefing {key} item: expected fields {sorted(fields)}")
            text(item["id"], f"briefing {key} id")
            text(item["text"], f"briefing {key} text")
            if item["id"] in seen:
                raise ValueError(f"briefing: duplicate or reserved id {item['id']!r}")
            seen.add(item["id"])
            if (key == "recap") != item["id"].startswith(RECAP_PREFIX):
                raise ValueError(f"briefing: only recap ids start with {RECAP_PREFIX!r}")
            if "markers" in fields:
                _markers(item["markers"], f"briefing {key} {item['id']}")
            if key == "conceal" and item["strategy"] not in STRATEGIES:
                raise ValueError(f"briefing conceal strategy must be one of {sorted(STRATEGIES)}; "
                                 "direct lies are not supported")
    for item in briefing["recap"]:
        for secret in briefing["conceal"]:
            if any(m in item["text"] for m in secret["markers"]):
                raise ValueError(f"recap {item['id']} repeats concealed item {secret['id']}; "
                                 "recap reaches the expression prompt")
    return briefing


def briefing_for(scenario, role_id):
    """The role's own briefing, else the scene's default ("*"), else None."""
    briefings = scenario.get("briefings") or {}
    return briefings.get(role_id, briefings.get("*"))


def recap_entries(briefing):
    return [{"id": item["id"], "type": "recap", "actor": "memory", "content": item["text"], "cues": {}}
            for item in (briefing or {}).get("recap", [])]


def controller_view(briefing):
    return {"goal": briefing["goal"], "want_known": [i["text"] for i in briefing["disclose"]],
            "keep_private": [{"text": i["text"], "strategy": i["strategy"]} for i in briefing["conceal"]]}


def expression_view(briefing):
    return {"goal": briefing["goal"], "may_share": [i["text"] for i in briefing["disclose"]]}


def mentions(speech, items):
    """Ids of briefing items whose markers appear in a spoken line."""
    return [item["id"] for item in items if any(m in speech for m in item["markers"])]
