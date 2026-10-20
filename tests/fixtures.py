"""Shared loaders for the shipped role and scene files, and small contract-event fixtures."""
import json
from pathlib import Path

from rolesim.contract import Event, Visibility
from rolesim.providers import MockBackend
from rolesim.roles import binding, load_role, load_scene, run

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


# An environment may annotate cues on its events (contract `signals` extension); the agent
# then uses them instead of appraising. These stand for the old delivery script.
CUED = [
    ("e1", "narration", "合作方催促：今天能否交出全部数据？你尚未收到完整的状态记录。",
     {"uncertainty": .9, "pressure": .6, "conflict": .9}, 0),
    ("e2", "narration", "你核对了约定的收件箱：合作方此前承诺补发的说明仍未到达。",
     {"commitment": .9, "uncertainty": .6, "pressure": .8, "conflict": .8}, -.7),
    ("e3", "speech", "合作方提出先核验一部分记录，再一起协商剩余数据的时间；这仍是一项提议。",
     {"repair": .9, "commitment": .5, "uncertainty": .3}, 0),
    ("e4", "narration", "核验完成的记录已按新约定收到，你检查了收件记录。",
     {"repair": .8, "commitment": .9}, .8),
]


def cued_events(role_id="role1", partner="role2"):
    events = []
    for turn, (event_id, type_, content, cues, signal) in enumerate(CUED, 1):
        events.append(Event(event_id, turn, type_, partner if type_ == "speech" else "env", content,
                            Visibility("public", [role_id, partner]),
                            signals={"cues": dict(cues), "reliability_signal": signal}))
    return events


def run_scene(out, backend=None, mode="profile_state_mechanisms", use_skills=True, roles=("role1", "role2"),
              scene="test1"):
    return run([load_role(r) for r in roles], load_scene(scene), backend or MockBackend(), out, mode, use_skills)
