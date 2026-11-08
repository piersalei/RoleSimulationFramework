"""Render a finished (or failed) run directory as one Markdown file for human readers.

Everything is read back from the files the run wrote; nothing is recomputed or judged. The
output contains each role's private setup and inner phases, so it belongs with the run, never
in a review package.
"""
import json
from pathlib import Path

FILENAME = "transcript.md"
KIND_TEXT = {"thought": "想", "speech": "说", "action": "做"}
MODE_TEXT = {"profile_only": "只给画像", "profile_state": "画像 + 动态状态", "profile_state_mechanisms": "画像 + 动态状态 + 决策过程"}
STAGE_TEXT = {"S1": "事件建模", "S2": "行动计划", "S3": "长期演化"}
STRATEGY_TEXT = {"omission": "不提", "strategic_ambiguity": "说得模糊"}
FILES = [
    ("transcript.md", "本文件"),
    ("manifest.json", "运行配置、版本、调用数、审计结论"),
    ("records.jsonl", "运行器视角的全部记录（每行一条）"),
    ("calls.jsonl", "每次 initialize / act / 环境调用的完整输入与输出"),
    ("scene.json", "剧情文件（含角色不可见的 world_private）"),
    ("audit_report.md", "自动审计报告"),
    ("roles/<id>/profile.json", "编译后的稳定画像、待审核项、入场取值来源"),
    ("roles/<id>/setup.json", "开场设定与绑定的 skill"),
    ("roles/<id>/final_state.json", "最终状态（按构念 ID）"),
    ("trace/<互动>/<id>/phases.jsonl", "每次提交的阶段记录：证据、判断、产物、写入与拒绝"),
    ("trace/<互动>/<id>/model_calls.jsonl", "每次模型调用的输入、原始输出与推理"),
]


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _lines(path):
    path = Path(path)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _short(value, limit=60):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


def _names():
    try:
        from .registry import Registry
        registry = Registry.load()
        return {cid: registry.construct(cid).name for cid in registry.ids("construct")}
    except Exception:
        return {}


def _role_section(member, folder):
    profile, setup = _read(folder / "profile.json", {}) or {}, _read(folder / "setup.json", {}) or {}
    modules = (profile.get("modules") or {}).values()
    count = {}
    for m in modules:
        count[m["source_kind"] if m["status"] == "active" else m["status"]] = count.get(
            m["source_kind"] if m["status"] == "active" else m["status"], 0) + 1
    lines = [f"### {member['name']}（{member['title']}，{member['character_id']}）", "",
             f"- 稳定画像：{'、'.join(f'{k} {v}' for k, v in sorted(count.items())) or '无'}；快照 `{str(profile.get('snapshot_id'))[:12]}`"]
    for item in (profile.get("review") or [])[:5]:
        lines.append(f"- 待审核：{item['construct_id']}：{item['reason']}")
    briefing = setup.get("briefing")
    if briefing:
        lines.append(f"- 互动目标：{briefing['goal']}")
        lines += [f"- 想让对方知道：{i['text']}" for i in briefing["disclose"]]
        lines += [f"- 不打算说出口（{STRATEGY_TEXT.get(i['strategy'], i['strategy'])}）：{i['text']}" for i in briefing["conceal"]]
        lines += [f"- 前情：{i['text']}" for i in briefing["recap"]]
    if setup.get("bound_skills"):
        lines.append(f"- 绑定的 skill：{'、'.join(setup['bound_skills'])}{'' if setup.get('skills_used', True) else '（本次不进提示）'}")
    return lines + [""]


def _phase_lines(phase, names):
    stage = next(iter(phase["products"]))
    product = phase["products"][stage]
    head = f"- 第 {phase['phase_no']} 阶段 · {STAGE_TEXT[stage]}（{phase['event_id']}）"
    if stage == "S1":
        hyps = "；".join(f"{h['statement']}（{h['confidence']}）" for h in product["hypotheses"])
        body = [f"{head}：{product['situation_definition']}；判断 {product['judgment_state']}；假设：{hyps}"]
    elif stage == "S2":
        e = product["expression"]
        body = [f"{head}：主行动「{product['chosen']}」；表达目标「{e['goal']}」，受众 {e['audience'] or '在场所有人'}，方式「{e['manner']}」"
                + (f"；承诺：{product['commitment']['content']}" if product["commitment"]["formed"] else "")]
    else:
        courses = "；".join(f"{c['partner']} {c['course']}" for c in product["relation_course"])
        body = [f"{head}：{product['expected_vs_actual']}" + (f"；关系：{courses}" if courses else "")]
    for c in phase["commit"]["accepted"]:
        where = (f"[{c['key']}]" if c["key"] else "") + (f".{c['field']}" if c["field"] else "")
        band = f"，{c['band']['name']}档" if c.get("band") else ""
        flag = f"，标记 {c['flag']}" if c.get("flag") else ""
        body.append(f"  - 写入 {names.get(c['construct_id'], c['construct_id'])}{where}：{_short(c['before'])} → "
                    f"{_short(c['after'])}（{c['update_operator']}{band}{flag}）")
    for r in phase["commit"]["rejected"]:
        change = r["change"]
        body.append(f"  - 拒绝 {names.get(change['construct_id'], change['construct_id'])}"
                    f"{'[' + change['key'] + ']' if change.get('key') else ''}：{r['error']['code']} {_short(r['error']['detail'], 80)}")
    return body


def render(run_dir):
    run = Path(run_dir)
    manifest = _read(run / "manifest.json")
    if manifest is None or manifest.get("pipeline") != "stages":
        raise ValueError(f"{run}: not a run directory of the staged pipeline")
    records = _lines(run / "records.jsonl")
    audit = _read(run / "audit_report.json")
    lines = [f"# {manifest['scene_id']}（{manifest.get('interface')}）", "",
             f"- 状态：{manifest['status']}；轮数：{manifest.get('turns')}；模式：{MODE_TEXT.get(manifest.get('mode'), manifest.get('mode'))}"
             f"；skill：{'启用' if manifest.get('use_skills', True) else '不进提示'}",
             f"- 后端：{manifest.get('backend')}（{manifest.get('model')}）；模型调用：{manifest.get('model_calls')}",
             f"- 自动审计：{'通过' if audit and audit['passed'] else '未通过' if audit else '未运行'}",
             "- 证据层级：工程运行，只说明管线可用，不说明心理机制有效。"]
    if manifest.get("error"):
        lines.append(f"- 失败原因：{manifest.get('error_type')}：{manifest['error']}")
    lines += ["", "## 角色", ""]
    for member in manifest["cast"]:
        lines += _role_section(member, run / "roles" / member["character_id"])
    lines += ["## 互动记录", ""]
    current = None
    for r in records:
        if r["turn_index"] != current:
            current = r["turn_index"]
            lines += ["", f"### 第 {current} 轮", ""]
        ids = set(r["recipients"])
        to = "仅本人" if ids == {r["actor"]["actor_id"]} else "、".join(e["name"] for e in r["recipients"].values())
        lines.append(f"- **{r['actor']['name']}**{KIND_TEXT[r['kind']]}（给：{to}）：{r['content']}")
    names = _names()
    lines += ["", "## 各角色的内部过程", ""]
    for member in manifest["cast"]:
        phases = _lines(run / "trace" / str(manifest.get("interaction_id")) / member["character_id"] / "phases.jsonl")
        lines += [f"### {member['name']}", ""]
        for phase in phases:
            lines += _phase_lines(phase, names)
        lines.append("")
    lines += ["## 文件", ""] + [f"- `{name}`：{text}" for name, text in FILES] + [""]
    return "\n".join(lines)


def write_readable(run_dir):
    path = Path(run_dir) / FILENAME
    path.write_text(render(run_dir), encoding="utf-8")
    return str(path)
