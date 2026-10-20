"""Persona skills: Agent Skills-compatible SKILL.md files bound to a persona.

A skill is a habitual coping or speaking strategy (persona line). It can only shape
which feasible action the controller favours and how the line is phrased; it never
writes state. Loading is progressive: every bound skill contributes name and
description; a skill's body enters the decision prompt only when the current event
or the role's briefing activates it; the expression prompt only receives the
"表达要点" section of active skills.

Our own fields live under `metadata`, which the Agent Skills specification allows,
so the same files stay readable by other skill-aware agents.
"""
import json
import re
from pathlib import Path

from .catalog import digest
from .persona import bounded, text

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "library" / "skills"
CATALOG = ROOT / "data" / "catalog.json"
BINDING_VERSION = "skills-binding-1.0"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
KINDS = {"decision", "expression", "disclosure"}
TRIGGERS = {"conceal", "disclose"}
# Same vocabularies as the scripted environment (runtime.CUES / runtime.ACTIONS).
CUES = {"uncertainty", "pressure", "commitment", "repair", "conflict"}
ACTIONS = {"request_evidence", "ask_clarification", "propose_plan", "set_boundary", "wait"}
SECTIONS = {"决策要点": "decision", "表达要点": "expression", "不会做的事": "avoid", "边界": "limits"}
CUE_THRESHOLD = .5
MAX_ACTIVE = 3


def _frontmatter(raw, label):
    lines = raw.split("\n")
    if not lines or lines[0].strip() != "---" or "---" not in [l.strip() for l in lines[1:]]:
        raise ValueError(f"{label}: SKILL.md must start with a --- frontmatter block")
    end = 1 + [l.strip() for l in lines[1:]].index("---")
    head, body = lines[1:end], "\n".join(lines[end + 1:])
    fields, metadata, in_meta = {}, {}, False
    for line in head:
        if not line.strip():
            continue
        indented = line.startswith("  ")
        key, sep, value = line.strip().partition(":")
        if not sep:
            raise ValueError(f"{label}: unsupported frontmatter line {line!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if indented:
            if not in_meta:
                raise ValueError(f"{label}: indented line outside metadata")
            metadata[key.strip()] = value
        elif key == "metadata" and not value:
            in_meta = True
        else:
            in_meta = False
            fields[key.strip()] = value
    fields["metadata"] = metadata
    return fields, body


def _sections(body, label):
    found, current = {}, None
    for line in body.split("\n"):
        if line.startswith("## "):
            title = line[3:].strip()
            if title not in SECTIONS:
                raise ValueError(f"{label}: unknown section {title!r}")
            current = SECTIONS[title]
            if current in found:
                raise ValueError(f"{label}: duplicate section {title!r}")
            found[current] = []
        elif current:
            found[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in found.items()}


def _words(value, allowed, label):
    items = value.split() if value else []
    if len(set(items)) != len(items) or not set(items) <= allowed:
        raise ValueError(f"{label}: unsupported or duplicate entries {items}")
    return items


def parse_skill(raw, folder, catalog_ids):
    label = f"skill {folder}"
    fields, body = _frontmatter(raw, label)
    if not set(fields) <= {"name", "description", "license", "compatibility", "metadata"}:
        raise ValueError(f"{label}: unsupported frontmatter field")
    name, description = fields.get("name", ""), fields.get("description", "")
    if not NAME.match(name) or len(name) > 64 or name != folder:
        raise ValueError(f"{label}: name must be lowercase-hyphenated, <=64 chars and match the folder")
    text(description, f"{label} description")
    if len(description) > 1024:
        raise ValueError(f"{label}: description longer than 1024 characters")
    meta = fields["metadata"]
    kind = meta.get("rolesim-kind")
    if kind not in KINDS:
        raise ValueError(f"{label}: rolesim-kind must be one of {sorted(KINDS)}")
    cues = _words(meta.get("rolesim-cues", ""), CUES, f"{label} cues")
    actions = _words(meta.get("rolesim-actions", ""), ACTIONS, f"{label} actions")
    trigger = meta.get("rolesim-trigger") or None
    if trigger is not None and trigger not in TRIGGERS:
        raise ValueError(f"{label}: rolesim-trigger must be one of {sorted(TRIGGERS)}")
    if bool(cues) == bool(trigger):
        raise ValueError(f"{label}: activate by cues or by a briefing trigger, exactly one")
    if kind == "decision" and not actions:
        raise ValueError(f"{label}: a decision skill must name the actions it supports")
    sources = _words(meta.get("rolesim-source", ""), set(catalog_ids), f"{label} sources")
    if not sources:
        raise ValueError(f"{label}: rolesim-source must cite catalog sections")
    text(meta.get("rolesim-evidence", ""), f"{label} rolesim-evidence")
    parts = _sections(body, label)
    required = {"expression", "avoid", "limits"} | ({"decision"} if kind != "expression" else set())
    missing = required - {k for k, v in parts.items() if v}
    if missing:
        raise ValueError(f"{label}: missing sections {sorted(missing)}")
    skill = {"name": name, "description": description, "kind": kind, "cues": cues, "actions": actions,
             "trigger": trigger, "sources": sources, "evidence": meta["rolesim-evidence"],
             "decision": parts.get("decision", ""), "expression": parts["expression"],
             "avoid": parts["avoid"], "limits": parts["limits"]}
    skill["digest"] = digest(skill)
    return skill


def load_library(root=LIBRARY, catalog_path=CATALOG):
    root = Path(root)
    catalog_ids = {s["id"] for s in json.loads(Path(catalog_path).read_text(encoding="utf-8"))["sections"]}
    library = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        library[folder.name] = parse_skill((folder / "SKILL.md").read_text(encoding="utf-8"), folder.name,
                                           catalog_ids)
    if not library:
        raise ValueError(f"no skills found under {root}")
    return library


def bind(binding, persona_id, library):
    """Validate a persona's skill binding; return its skills ordered by priority."""
    if not isinstance(binding, dict) or set(binding) != {"schema_version", "persona_id", "skills"}:
        raise ValueError("skills binding: expected schema_version, persona_id, skills")
    if binding["schema_version"] != BINDING_VERSION:
        raise ValueError(f"skills binding {binding['schema_version']!r} is not supported")
    if binding["persona_id"] != persona_id:
        raise ValueError("skills binding belongs to another persona")
    items = binding["skills"]
    if not isinstance(items, list) or not items:
        raise ValueError("skills binding: list at least one skill")
    bound, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "priority", "note"}:
            raise ValueError("skills binding entry: expected name, priority, note")
        if item["name"] not in library:
            raise ValueError(f"skills binding: unknown skill {item['name']!r}")
        if item["name"] in seen:
            raise ValueError(f"skills binding: duplicate skill {item['name']!r}")
        seen.add(item["name"])
        bounded(item["priority"], 0, 1, f"{item['name']} priority")
        text(item["note"], f"{item['name']} note")
        bound.append({**library[item["name"]], "priority": item["priority"]})
    return sorted(bound, key=lambda s: (-s["priority"], s["name"]))


def catalog(bound):
    return [{"name": s["name"], "description": s["description"]} for s in bound]


def select(bound, observation, allowed, briefing=None):
    """Skills activated by this event's cues or by the role's briefing, at most MAX_ACTIVE."""
    cues = (observation or {}).get("cues", {})
    active = []
    for skill in bound:
        if skill["trigger"]:
            if not briefing or not briefing.get(skill["trigger"]):
                continue
            strength = 1
        else:
            strength = max((cues.get(c, 0) for c in skill["cues"]), default=0)
            if strength < CUE_THRESHOLD:
                continue
        feasible = [a for a in skill["actions"] if a in allowed]
        if skill["actions"] and not feasible:
            continue
        active.append({"name": skill["name"], "actions": feasible,
                       "score": round(skill["priority"] * strength, 4),
                       "decision": skill["decision"], "avoid": skill["avoid"],
                       "trigger": skill["trigger"] or (observation or {}).get("id")})
    return sorted(active, key=lambda s: (-s["score"], s["name"]))[:MAX_ACTIVE]


def expression_hints(bound, active):
    by_name = {s["name"]: s for s in bound}
    return [by_name[a["name"]]["expression"] for a in active]
