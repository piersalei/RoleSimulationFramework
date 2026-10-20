"""Role files and scene files: the two pluggable inputs of a run.

    roles/<id>.json    one file per role: persona (schema 2.0, embedded unchanged),
                       skills (name / priority / note) and the opening setup
    scenes/<id>.json   one background file per scene (scene schema 1.2)

The file name is the id. Any role can be seated in any scene that `fit` accepts; swapping
either side is a matter of naming another file. The scene format is a stand-in until the
environment group's background format lands; it does not direct the role.
"""
from collections import namedtuple
from pathlib import Path

from . import skills as sk
from .briefing import validate_briefing
from .catalog import digest
from .mechanisms import CARDS as MECHANISM_CARDS
from .persona import validate_persona
from .providers import strict_json
from .runtime import make_agent, run_episode, scene_event_ids, validate_scenario

ROOT = Path(__file__).resolve().parents[1]
ROLES_DIR = ROOT / "roles"
SCENES_DIR = ROOT / "scenes"
ROLE_FORMAT = "role-1.0"
ROLE_KEYS = {"format", "persona", "skills", "setup"}

Seated = namedtuple("Seated", "agent role_id role_profile scene_context")


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
        validate_briefing(role_file["setup"])
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
    """Errors stop a seating; notes are things an author may want to know."""
    persona, scenario, setup = role["persona"], scene["scenario"], role["setup"]
    errors, notes = [], []
    if set(persona["stable"]["mechanism_priors"]) != {c["id"] for c in MECHANISM_CARDS}:
        errors.append("persona mechanism priors do not match the enabled mechanism cards")
    if setup is not None:
        try:
            validate_briefing(setup, scene_event_ids(scenario))
        except ValueError as exc:
            errors.append(f"setup: {exc}")
    allowed = set(scenario["allowed_actions"])
    # Outcome events carry no cues, so the scheduled events bound what can ever cue a skill.
    cued = {cue for event in scenario["events"] + [scenario["evidence"]]
            for cue, value in event["cues"].items() if value >= sk.CUE_THRESHOLD}
    for skill in role["bound"] or []:
        if skill["trigger"]:
            if not (setup or {}).get(skill["trigger"]):
                notes.append(f"skill {skill['name']}: never active here (setup has no '{skill['trigger']}' items)")
        elif skill["actions"] and not set(skill["actions"]) & allowed:
            notes.append(f"skill {skill['name']}: never active here (none of its actions are allowed)")
        elif not set(skill["cues"]) & cued:
            notes.append(f"skill {skill['name']}: never active here (no event carries its cues)")
    return {"ok": not errors, "errors": errors, "notes": notes}


def _check(role, scene):
    result = fit(role, scene)
    if not result["ok"]:
        raise ValueError(f"role {role['id']} does not fit scene {scene['id']}: " + "; ".join(result["errors"]))


def seat(role, scene, backend, mode="profile_state_mechanisms", use_skills=True):
    """Seat a role in a scene. An environment drives the returned Agent through
    `step`/`perceive`, sending `role_profile` on the first step as the contract requires."""
    _check(role, scene)
    agent = make_agent(role["persona"], scene["scenario"], backend, mode, role["bound"], use_skills, role["setup"])
    return Seated(agent, role["id"], role["persona"], scene["scenario"]["background"])


def origin(role, scene):
    return {"role": {"id": role["id"], "file_hash": digest(role["file"])},
            "scene": {"id": scene["id"], "file_hash": digest(scene["scenario"])}}


def run(role, scene, backend, output, mode="profile_state_mechanisms", use_skills=True):
    """The scripted-environment run for a role file in a scene file."""
    _check(role, scene)
    if not use_skills and role["binding"] is None:
        raise ValueError("--no-skills needs a role that lists skills")
    return run_episode(role["persona"], scene["scenario"], backend, output, mode, role["binding"],
                       use_skills, role["setup"], origin(role, scene))
