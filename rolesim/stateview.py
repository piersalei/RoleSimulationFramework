"""A readable summary of a role's dynamic state for its own prompts (`state_view`).

Only active scalar fields with a value are shown; unknown and not-modelled ones are left
out, and changes are merged into one net change per field. Values become three level
words by equal thirds of the field's range. The
cut points are an engineering assumption, computed here for display only and never
written into the state.
"""
from .state import RANGES

LEVELS = ("低", "中", "高")
NOTE = "等级按取值范围三等分换算，是工程假设，不是校准结果；未列出的方面尚未建模或未知。"
EMOTION = "dynamic.affect_state.emotion"
EMOTION_FIELDS = {"intensity": "强度", "valence": "效价（负到正）", "arousal": "唤醒度"}
RELATION_FIELDS = {"trust": "对{name}的信任", "closeness": "与{name}的亲近", "dependence": "对{name}的依赖",
                   "commitment": "对{name}的投入"}


def level(value, field):
    low, high = RANGES.get(field, (0, 1))
    share = (value - low) / (high - low)
    return LEVELS[0] if share < 1 / 3 else LEVELS[1] if share < 2 / 3 else LEVELS[2]


def _fields(dynamic, names):
    """(path, label, leaf) for every scalar field the view can describe."""
    label = dynamic["affect_state"]["emotion"].get("label") or "情绪"
    for leaf, text in EMOTION_FIELDS.items():
        yield f"{EMOTION}.{leaf}", f"情绪（{label}）{text}", leaf
    for other in dynamic["relation_state"]:
        for leaf, text in RELATION_FIELDS.items():
            yield f"dynamic.relation_state.{other}.{leaf}", text.format(name=names.get(other, other)), leaf


def _value(dynamic, path):
    for key in path.split(".")[1:]:
        dynamic = dynamic.get(key) if isinstance(dynamic, dict) else None
    return dynamic


def state_view(state, names, since=(), numbers=False, mechanisms=False):
    """`numbers` adds raw values; `mechanisms` adds which operator caused each change.
    Expression and appraisal prompts get neither."""
    dynamic, status = state["dynamic"], state["module_status"]
    items, labels = [], {}
    for path, label, leaf in _fields(dynamic, names):
        if status.get(path, {}).get("status") == "active":
            value = _value(dynamic, path)
            if isinstance(value, (int, float)):
                item = {"field": label, "level": level(value, leaf)}
                if numbers:
                    item["value"] = value
                items.append(item)
                labels[path] = (label, leaf)
    net = {}
    for change in since:
        if change["path"] in labels:
            first = net.setdefault(change["path"], {"before": change["before"], "by": []})
            first["after"] = change["after"]
            if change["mechanism"] not in first["by"]:
                first["by"].append(change["mechanism"])
    changes = []
    for path, move in net.items():
        label, leaf = labels[path]
        before, after = move["before"], move["after"]
        if before == after:
            continue
        item = {"field": label, "from": level(before, leaf), "to": level(after, leaf),
                "direction": "上升" if after > before else "下降"}
        if numbers:
            item.update(before=before, after=after)
        if mechanisms:
            item["by"] = move["by"]
        changes.append(item)
    return {"note": NOTE, "items": items, "changes_since_my_last_turn": changes}
