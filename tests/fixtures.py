"""Shared loaders for the shipped role and scene files."""
import json
from pathlib import Path

from rolesim.roles import binding

ROOT = Path(__file__).resolve().parents[1]


def role_file(role_id="role1"):
    return json.loads((ROOT / "roles" / f"{role_id}.json").read_text())


def setup(role_id="role1"):
    return role_file(role_id)["setup"]


def load(name):
    """'role1' -> persona, 'role1.skills' -> skills binding, 'test1' -> scene."""
    if name.startswith("test"):
        return json.loads((ROOT / "scenes" / f"{name}.json").read_text())
    if name.endswith(".skills"):
        return binding(role_file(name[:-len(".skills")]))
    return role_file(name)["persona"]
