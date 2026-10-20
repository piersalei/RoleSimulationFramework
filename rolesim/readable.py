"""Render a finished (or failed) run directory as one Markdown file for human readers.

Everything here is read back from the files the run wrote; nothing is recomputed or judged.
The output contains each role's private setup, so it belongs with the run, never in a review package."""
import json
from pathlib import Path

from .vocab import ACTION_TEXT

FILENAME = "transcript.md"
TENSION = "dynamic.affect_state.emotion.intensity"
TRUST = "dynamic.relation_state.partner.trust"
CUE_NAME = {"uncertainty": "不确定", "pressure": "压力", "commitment": "承诺", "repair": "修复", "conflict": "冲突"}
MODE_TEXT = {"profile_only": "只给画像", "profile_state": "画像 + 动态状态",
             "profile_state_mechanisms": "画像 + 动态状态 + 机制卡"}
STRATEGY_TEXT = {"omission": "不提", "strategic_ambiguity": "说得模糊"}
STAGE_TEXT = {"appraise": "感知评价", "decide": "决策", "express": "表达"}
FILES = [
    ("transcript.md", "本文件"),
    ("public.jsonl", "双方台词（每行一句）"),
    ("environment_events.json", "环境记录的全部事件"),
    ("manifest.json", "运行配置、版本、调用数、审计结论"),
    ("audit_report.md", "自动审计报告"),
    ("roles/<id>/inputs.json", "该角色的画像、场景、skill、开场设定"),
    ("roles/<id>/audit.jsonl", "每次发言的模型输入输出与思考过程"),
    ("roles/<id>/states.jsonl", "状态链（每次发言两个快照）"),
    ("roles/<id>/mechanism_log.jsonl", "状态更新的候选、采纳与拒绝"),
]


def _load(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _get(data, path):
    for key in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _num(value):
    return "—" if value is None else f"{value:.2f}"


def _quote(text):
    return "\n".join("> " + line for line in str(text).splitlines()) or ">"


def _evidence(ids, names):
    out = []
    for item in ids or []:
        if item == "open":
            out.append("开场旁白")
        elif item.startswith("speech:"):
            out.append(f"第 {item.split(':', 1)[1]} 句")
        elif item.startswith("recap:"):
            out.append(f"前情 {item.split(':', 1)[1]}")
        else:
            out.append(names.get(item, item))
    return "、".join(out)


def _cues(cues):
    ranked = sorted((cues or {}).items(), key=lambda kv: -kv[1])
    return "、".join(f"{CUE_NAME.get(k, k)} {v:.2f}" for k, v in ranked) or "无"


def _change(delta, path):
    for change in (delta or {}).get("changes", []):
        if change["path"] == path:
            return change
    return None


def _role_section(role_id, seat, inputs, info, first):
    persona = inputs.get("persona", {})
    stable = persona.get("stable", {})
    name = persona.get("name", role_id)
    out = [f"### {name}（{role_id}）· {seat['title']}", ""]
    if persona.get("public_description"):
        out.append(f"- 简介：{persona['public_description']}")
    if persona.get("self_view"):
        out.append(f"- 自我理解：{' '.join(persona['self_view'])}")
    roles = _get(stable, "identity.social_roles")
    if roles:
        out.append(f"- 社会角色：{'；'.join(roles)}")
    goals = stable.get("core_goals") or []
    if goals:
        out.append(f"- 核心目标：{'；'.join(g['text'] for g in goals)}")
    style = stable.get("speech_style") or {}
    parts = [f"直接程度 {v}" for k, v in (style.get("baseline") or {}).items() if k == "directness"]
    parts += [f"{r['when']}：{r['style']}" for r in style.get("situational_rules") or []]
    if parts:
        out.append(f"- 说话风格：{'；'.join(parts)}")
    skills = (info or {}).get("skills")
    if skills:
        state = "已注入提示" if skills.get("enabled") else "只记录、未注入提示（对照组）"
        out.append(f"- 绑定的 skill（{state}）：{'、'.join(skills['names'])}")
    out.append(f"- 开始时状态：紧张 {_num(first[0])}，对对方的信任 {_num(first[1])}")
    briefing = inputs.get("briefing")
    if briefing:
        out += ["", f"**开场设定**（只有{name}自己知道；设定原文里的\"你\"指{name}一方）", ""]
        out.append(f"- 这次想达成：{briefing['goal']}")
        for item in briefing.get("disclose", []):
            out.append(f"- 愿意让对方知道：{item['text']}")
        for item in briefing.get("conceal", []):
            how = STRATEGY_TEXT.get(item.get("strategy"), item.get("strategy", ""))
            out.append(f"- 不想让对方知道（方式：{how}）：{item['text']}")
        for item in briefing.get("recap", []):
            out.append(f"- 前情：{item['text']}")
    return out + [""]


def _turn_section(record, role_id, name, title, names, states, previous):
    decision = record.get("decision") or {}
    action = decision.get("action") or (record.get("proposal") or {}).get("intent_note")
    out = [f"### 第 {record['turn']} 句 · {name}（{title}）", ""]
    for item in record.get("appraisals") or []:
        output = item.get("output") or {}
        heard = _evidence([item.get("event_id")], names)
        out.append(f"- 听到{heard}后的感受：{_cues(output.get('cues'))}。{output.get('reason', '')}")
    active = (record.get("controller_input") or {}).get("active_skills") or []
    if active:
        out.append(f"- 启用的 skill：{'、'.join(s['name'] for s in active)}")
    if action:
        reason = decision.get("brief_reason", "")
        cited = _evidence(decision.get("evidence_ids"), names)
        out.append(f"- 决定：{ACTION_TEXT.get(action, action)}（{action}）。{reason}"
                   + (f"（依据：{cited}）" if cited else ""))
    speech = (record.get("proposal") or {}).get("content")
    if speech:
        out += ["- 说：", "", _quote(speech), ""]
    before, heard, said = previous[0], states.get(f"{role_id}@t{record['turn']:03d}"), \
        states.get(f"{role_id}@t{record['turn']:03d}.p1")
    tension = [_get(heard, TENSION), _get(said, TENSION)]
    trust = [_get(heard, TRUST), _get(said, TRUST)]
    out.append(f"- 状态：紧张 {_num(before)} → {_num(tension[0])}（听完）→ {_num(tension[1])}（说完）；"
               f"信任 {_num(previous[1])} → {_num(trust[1])}")
    thinking = []
    for stage, texts in (record.get("reasoning") or {}).items():
        for text in [t for t in (texts if isinstance(texts, list) else [texts]) if t]:
            thinking += [f"**{STAGE_TEXT.get(stage, stage)}**", "", text.strip(), ""]
    if thinking:
        out += ["", "<details><summary>模型思考过程（只存在运行记录里，不进入任何提示）</summary>", ""]
        out += thinking + ["</details>"]
    previous[:] = [tension[1], trust[1]]
    return out + [""]


def render(run_dir):
    run = Path(run_dir)
    manifest = _load(run / "manifest.json")
    if not manifest or manifest.get("schema_version") != "2.0" or "cast" not in manifest:
        raise ValueError(f"{run} is not a two-role run directory (manifest schema 2.0)")
    cast = manifest["cast"]
    inputs = {c["role_id"]: _load(run / "roles" / c["role_id"] / "inputs.json", {}) for c in cast}
    names = {c["role_id"]: inputs[c["role_id"]].get("persona", {}).get("name", c["role_id"]) for c in cast}
    names["env"] = "旁白"
    scenario = next(iter(inputs.values()), {}).get("scenario", {})
    records = {c["role_id"]: _lines(run / "roles" / c["role_id"] / "audit.jsonl") for c in cast}
    states = {c["role_id"]: {s["state_id"]: s for s in _lines(run / "roles" / c["role_id"] / "states.jsonl")}
              for c in cast}
    events = _load(run / "environment_events.json", [])
    report = _load(run / "audit_report.json")

    first = {}
    for c in cast:
        role, opening = c["role_id"], records[c["role_id"]][:1]
        delta = opening[0].get("step_delta") if opening else None
        snapshot = next(iter(states[role].values()), None)
        start = [(_change(delta, path) or {}).get("before", _get(snapshot, path)) for path in (TENSION, TRUST)]
        first[role] = start

    title = f"{manifest.get('scene_id', '')} · {' × '.join(names[c['role_id']] for c in cast)}"
    out = [f"# {title}", "", f"运行目录：`{run.name}`", "",
           "> 工程运行记录，不是心理有效性证据。场景只提供背景，对话由两个角色的模型自主完成。", "", "## 概况", ""]
    status = manifest.get("status")
    turns = manifest.get("turns") or sum(len(r) for r in records.values())
    if status == "completed":
        closed = manifest.get("closed_by")
        ending = f"由{names.get(closed, closed)}选择结束" if closed else "到达发言次数上限"
        out.append(f"- 状态：已完成，共 {turns} 句，{ending}")
    else:
        out.append(f"- 状态：{status}，已完成 {turns} 句")
        if manifest.get("error"):
            out.append(f"- 出错：{manifest.get('error_type', '')} {manifest['error']}")
    for key, label in (("started_at", "开始时间"), ("finished_at", "结束时间")):
        if manifest.get(key):
            out.append(f"- {label}：{manifest[key]}")
    sampling = manifest.get("sampling") or {}
    knobs = "，".join(f"{k} {v}" for k, v in sampling.items() if k != "thinking")
    out.append(f"- 模型：{manifest.get('model')}" + (f"（{knobs}）" if knobs else ""))
    if sampling.get("thinking"):
        out.append(f"- 思考模式：{sampling['thinking']}")
    out.append(f"- 档位：{MODE_TEXT.get(manifest.get('mode'), manifest.get('mode'))}")
    out.append(f"- 模型调用：{manifest.get('api_calls', '—')} 次")
    if report:
        verdict = "通过" if report["passed"] else "未通过"
        out.append(f"- 自动审计：{verdict}（" + "，".join(
            f"{names.get(r, r)}{'通过' if v['passed'] else '未通过'}" for r, v in report["roles"].items()) + "）")
    code = manifest.get("code_version") or {}
    if code.get("git_sha"):
        out.append(f"- 代码版本：{code['git_sha'][:7]}" + ("（有未提交的改动）" if code.get("git_dirty") else ""))

    out += ["", "## 场景背景", "", scenario.get("background", "（无）"), ""]
    for c in cast:
        out.append(f"- {c['title']}：{names[c['role_id']]}（{c['role_id']}）")
    allowed = scenario.get("allowed_actions") or []
    if allowed:
        out += ["", "角色每次发言前从下面的交流动作里选一个（对方只听到台词，看不到动作）：", ""]
        out += [f"- {ACTION_TEXT.get(a, a)}（{a}）" for a in allowed]

    out += ["", "## 角色", ""]
    for c in cast:
        out += _role_section(c["role_id"], c, inputs[c["role_id"]], (manifest.get("roles") or {}).get(c["role_id"]),
                             first[c["role_id"]])

    out += ["## 对话过程", ""]
    for event in events:
        if event.get("type") == "narration":
            out += [f"**旁白**（第 {event['turn']} 句前）：{event['content']}", ""]
    titles = {c["role_id"]: c["title"] for c in cast}
    previous = {role: list(start) for role, start in first.items()}
    ordered = sorted(((r["turn"], role, r) for role, rs in records.items() for r in rs), key=lambda x: x[0])
    for _, role, record in ordered:
        out += _turn_section(record, role, names[role], titles[role], names, states[role], previous[role])
    if not ordered:
        out += ["（没有发言记录）", ""]

    out += ["## 结果", "", "| 角色 | 紧张：开始 → 最高 → 结束 | 信任：开始 → 结束 | 选过的动作 | 主动透露 | 隐瞒是否守住 |",
            "|---|---|---|---|---|---|"]
    for c in cast:
        role = c["role_id"]
        values = [_get(s, TENSION) for s in states[role].values()]
        values = [v for v in [first[role][0], *values] if v is not None]
        trust_end = _get(list(states[role].values())[-1], TRUST) if states[role] else None
        moves = {}
        for r in records[role]:
            act = (r.get("decision") or {}).get("action")
            if act:
                moves[act] = moves.get(act, 0) + 1
        moves_text = "、".join(f"{a} × {n}" for a, n in moves.items()) or "—"
        rep = (report or {}).get("roles", {}).get(role) or {}
        disclosed = len(rep.get("disclosed_items", [])) if rep else "—"
        kept = next((c2["status"] for c2 in rep.get("checks", []) if c2["id"] == "concealed_info_withheld"), None)
        kept_text = {"pass": "守住（只按关键词查）", None: "—"}.get(kept, "关键词出现在台词里")
        tension = f"{_num(values[0])} → {_num(max(values))} → {_num(values[-1])}" if values else "—"
        out.append(f"| {names[role]} | {tension} | {_num(first[role][1])} → {_num(trust_end)} | {moves_text} "
                   f"| {disclosed} 次 | {kept_text} |")
    out += ["", "信任只在有兑现证据时变化；纯对话里对方的口头承诺不算证据，所以通常保持不变。", "",
            "## 文件说明", ""]
    out += [f"- `{path}`：{text}" for path, text in FILES]
    return "\n".join(out) + "\n"


def write_readable(run_dir):
    path = Path(run_dir) / FILENAME
    path.write_text(render(run_dir), encoding="utf-8")
    return path
