"""Role files and scene files: the two pluggable inputs of a run.

    roles/<id>.json    one file per role: persona (schema 2.0, embedded unchanged),
                       skills (name / priority / note) and the opening setup
    scenes/<id>.json   one background file per scene (scene schema 3.0): shared
                       background, the agents present (2-6 role seats, at most one
                       environment agent), turn limit, allowed moves

The file name is the id. A run seats one role per role seat, in order; what happens is then
up to the roles. The scene format is a stand-in until the environment group's
background format lands; it does not direct the roles.
"""
from pathlib import Path

from . import skills as sk
from .briefing import validate_briefing
from .catalog import digest
from .persona import validate_persona
from .providers import strict_json
from .scene import RESERVED_IDS, role_seats, validate_scenario

ROOT = Path(__file__).resolve().parents[1]
ROLES_DIR = ROOT / "roles"
SCENES_DIR = ROOT / "scenes"
ROLE_FORMAT = "role-1.0"
ROLE_KEYS = {"format", "persona", "skills", "setup"}


def _path(ref, folder):
    """A bare id names <folder>/<id>.json; anything with a slash or .json is a path."""
    ref = str(ref)
    return Path(ref) if ref.endswith(".json") or "/" in ref else Path(folder) / f"{ref}.json"


def _read(path, label):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{label} {path} not found")
    return strict_json(path.read_text(encoding="utf-8"))


def binding(role_file):
    """The role's skills as a skills binding, or None when it lists none."""
    items = role_file["skills"]
    if not items:
        return None
    return {"schema_version": sk.BINDING_VERSION, "persona_id": role_file["persona"]["id"], "skills": items}


def validate_role(role_file, library=None):
    if not isinstance(role_file, dict) or set(role_file) != ROLE_KEYS:
        raise ValueError(f"role file: expected fields {sorted(ROLE_KEYS)}")
    if role_file["format"] != ROLE_FORMAT:
        raise ValueError(f"role file: format must be {ROLE_FORMAT}")
    validate_persona(role_file["persona"])
    if not isinstance(role_file["skills"], list):
        raise ValueError("role file: skills must be a list (empty for none)")
    bound = None
    if role_file["skills"]:
        bound = sk.bind(binding(role_file), role_file["persona"]["id"],
                        library if library is not None else sk.load_library())
    if role_file["setup"] is not None:
        validate_briefing(role_file["setup"], RESERVED_IDS)
    return bound


def load_role(ref, folder=ROLES_DIR, library=None):
    path = _path(ref, folder)
    role_file = _read(path, "role file")
    bound = validate_role(role_file, library)
    role_id = role_file["persona"]["id"]
    if path.stem != role_id:
        raise ValueError(f"role file {path.name}: file name must be the persona id ({role_id}.json)")
    return {"id": role_id, "path": str(path), "file": role_file, "persona": role_file["persona"],
            "binding": binding(role_file), "bound": bound, "setup": role_file["setup"]}


def load_scene(ref, folder=SCENES_DIR):
    path = _path(ref, folder)
    scenario = _read(path, "scene file")
    validate_scenario(scenario)
    if path.stem != scenario["id"]:
        raise ValueError(f"scene file {path.name}: file name must be the scene id ({scenario['id']}.json)")
    return {"id": scenario["id"], "path": str(path), "scenario": scenario}


def available(folder):
    return sorted(p.stem for p in Path(folder).glob("*.json"))


def fit(role, scene):
    """Can this role sit in this scene? Errors stop a seating; notes are things an author may want to know."""
    setup = role["setup"]
    errors, notes = [], []
    if setup is not None:
        try:
            validate_briefing(setup, RESERVED_IDS)
        except ValueError as exc:
            errors.append(f"setup: {exc}")
    # Cues come from each role's own reading of what happens (S1), so only the setup can rule
    # a skill out in advance.
    for skill in role["bound"] or []:
        if skill["trigger"] and not (setup or {}).get(skill["trigger"]):
            notes.append(f"skill {skill['name']}: never active here (setup has no '{skill['trigger']}' items)")
    return {"ok": not errors, "errors": errors, "notes": notes}


def fit_cast(roles, scene):
    """One distinct role per seat, each fitting the scene."""
    seats = role_seats(scene["scenario"])
    errors, notes = [], []
    if len(roles) != len(seats):
        errors.append(f"scene {scene['id']} has {len(seats)} seats; got {len(roles)} roles")
    if len({r["id"] for r in roles}) != len(roles):
        errors.append("the same role cannot take two seats")
    for role, seat in zip(roles, seats):
        result = fit(role, scene)
        errors += [f"{role['id']} ({seat['title']}): {e}" for e in result["errors"]]
        notes += [f"{role['id']} ({seat['title']}): {n}" for n in result["notes"]]
    return {"ok": not errors, "errors": errors, "notes": notes}


def origin(roles, scene):
    return {"roles": [{"id": r["id"], "file_hash": digest(r["file"])} for r in roles],
            "scene": {"id": scene["id"], "file_hash": digest(scene["scenario"])}}
