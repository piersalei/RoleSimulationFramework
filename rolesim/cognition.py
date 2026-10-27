"""Render a role's state as the five-field `CognitionBasis` of the agent interface v1.

The role's state (state-v1) stays the source of truth; this is a text summary of it for the
runner, built from templates with no model call, so it adds no unaudited state change.
Which state field feeds which cognition field is an engineering mapping, not a psychology
decision. A field is null when nothing feeds it yet (the module is unknown or not modelled).

Need-to-know: no numbers, mechanism names or concealed briefing text; the cognition leaves
this process and the runner keeps it.
"""
from .stateview import state_view

RECENT = 5


def _join(lines, sep="；"):
    lines = [line for line in lines if line]
    return sep.join(lines) if lines else None


def _items(state, names):
    return state_view(state, names)["items"]


def _knowledge(state):
    memory = state["memory"]
    recap = [e["content"] for e in memory if e["type"] == "recap"]
    recent = [f"{e['actor']}：{e['content']}" for e in memory if e["type"] != "recap"][-RECENT:]
    return _join(recap + recent, "\n")


def _beliefs(state, names):
    lines = [f"{i['field']}：{i['level']}" for i in _items(state, names) if not i["field"].startswith("情绪")]
    cognition = state["dynamic"]["cognition_state"]
    for block in ("beliefs", "other_models"):
        for key, item in cognition[block].items():
            content = item.get("content") if isinstance(item, dict) else None
            lines.append(f"{key}：{content}" if content else key)
    return _join(lines)


def _emotions(state, names):
    return _join([f"{i['field']}：{i['level']}" for i in _items(state, names) if i["field"].startswith("情绪")])


def _goals(state, briefing):
    goals = [g.get("text") for g in state["dynamic"]["goal_state"]["goals_now"] if isinstance(g, dict)]
    return _join(([briefing["goal"]] if briefing else []) + goals)


def _motivations(persona):
    return _join([g["text"] for g in persona["stable"]["core_goals"] or []])


def project(state, persona, names, briefing=None):
    """`names` maps role ids to the names this role knows them by (itself as 我)."""
    return {"knowledge": _knowledge(state), "beliefs": _beliefs(state, names),
            "emotions": _emotions(state, names), "motivations": _motivations(persona),
            "goals": _goals(state, briefing)}
