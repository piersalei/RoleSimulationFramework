"""Automatic audit of a finished run directory (psychology reply, section 3.2-3.3).

The audit re-derives its verdicts from the run's own files (records.jsonl, scene.json,
roles/<id>/profile.json, setup.json, final_state.json, trace/<interaction>/<id>/phases.jsonl and
model_calls.jsonl) instead of trusting the engine's bookkeeping, and checks the registry again.
Per role:

  trajectory_traceable      every phase reads the state the previous one wrote; the final state is
                            the last one written                                  轨迹可追溯性
  operator_write_compatible accepted changes: registered operator, construct on its write list, the
                            stage it may run in, its dependency group, never stable  机制与写回相容性
  evidence_visible          accepted changes cite only records the role acted in or was addressed by
  prompt_isolation          model calls show only such records, never world-private facts, never
                            another role's thought, never another role's concealed setup items
                            (unless that role said them in a record this role saw) 证据与可见性
  concealed_withheld        no concealed setup item ever reaches an expression call
  unaware_withheld          stable values the role cannot be aware of (or whose self-visibility the
                            sources leave open) never reach a prompt, by name or by value
  thoughts_self_only        the role's thoughts are addressed to itself only
  stable_unchanged          the final state still points at the compiled profile   稳定一致性
  fields_valid              the final state's values fit the state layout          字段有效性
Counted, not judged: soft flags and rejected changes by code, review items, spoken mentions of
concealed / disclosable setup items (the plan may choose to reveal; only the expression prompt
is checked).

A clean report says nothing about psychological plausibility; mock runs never do.
"""
import json
import re
from collections import Counter
from pathlib import Path

from ..role.briefing import mentions
from ..catalog import write_json
from ..role.registry import Registry
from ..state.slots import OPTION_SLOTS, SLOTS, check_instance

HUMAN_DIMENSIONS = ["机制与写回相容性（理论层面）", "动态合理性", "角色可区分性", "外显行为与隐状态匹配", "错误推断的合理性"]
MAX_DETAILS = 20
ID_PATTERN = re.compile(r"\b[dsm]\.[a-z_]+\.[a-z_]+")


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _lines(path):
    path = Path(path)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def _check(check_id, dimension, failures, checked, note=""):
    return {"id": check_id, "dimension": dimension, "status": "pass" if not failures else "fail",
            "checked": checked, "failures": len(failures), "details": failures[:MAX_DETAILS], "note": note}


def _rid(record):
    return f"{record['action_id']}#{record['order_index']}"


def audit_role(run, cid, manifest, records, scene, registry):
    folder = run / "roles" / cid
    trace = run / "trace" / manifest["interaction_id"] / cid
    phases, calls = _lines(trace / "phases.jsonl"), _lines(trace / "model_calls.jsonl")
    profile, setup, final = _read(folder / "profile.json", {}), _read(folder / "setup.json", {}), _read(folder / "final_state.json")
    visible = {_rid(r): r for r in records if r["actor"]["actor_id"] == cid or cid in r["recipients"]}
    checks = []

    failures = []
    for previous, phase in zip(phases, phases[1:]):
        if phase["input_state_id"] != previous["output_state_id"]:
            failures.append({"phase": phase["phase_no"], "reads": phase["input_state_id"], "previous_wrote": previous["output_state_id"]})
    for phase in phases:
        if phase["commit"]["output_state_id"] != phase["output_state_id"]:
            failures.append({"phase": phase["phase_no"], "commit": phase["commit"]["output_state_id"], "log": phase["output_state_id"]})
    if final is not None and phases and final["state_id"] != phases[-1]["output_state_id"]:
        failures.append({"final_state": final["state_id"], "last_written": phases[-1]["output_state_id"]})
    if final is None:
        failures.append({"final_state": "missing"})
    checks.append(_check("trajectory_traceable", "轨迹可追溯性", failures, len(phases) + 1))

    accepted = [(p["phase_no"], c) for p in phases for c in p["commit"]["accepted"]]
    write_fail, evidence_fail = [], []
    for phase_no, c in accepted:
        try:
            rule = registry.operator(c["update_operator"])
            construct = registry.construct(c["construct_id"])
        except KeyError as exc:
            write_fail.append({"phase": phase_no, "error": str(exc)})
            continue
        problems = [p for p, bad in (("stable", construct.layer == "stable"), ("not_in_whitelist", c["construct_id"] not in rule.writes),
                                     ("stage", c["stage"] not in rule.stages), ("group", c["group"] != rule.group)) if bad]
        if problems:
            write_fail.append({"phase": phase_no, "construct": c["construct_id"], "operator": rule.name, "problems": problems})
        unseen = [e["event"] for e in c["evidence"] if e["event"] not in visible]
        if not c["evidence"] or unseen:
            evidence_fail.append({"phase": phase_no, "construct": c["construct_id"], "unseen": unseen or "no evidence"})
    checks.append(_check("operator_write_compatible", "机制与写回相容性（权限层）", write_fail, len(accepted)))
    checks.append(_check("evidence_visible", "证据与可见性", evidence_fail, len(accepted)))

    secrets = [s for s in _strings(scene.get("world_private")) if len(s) >= 4]
    other_thoughts = [r["content"] for r in records if r["kind"] == "thought" and r["actor"]["actor_id"] != cid and len(r["content"]) >= 4]
    heard = json.dumps([r["content"] for r in visible.values()], ensure_ascii=False)
    others_concealed = [i["text"] for m in manifest.get("cast", []) if m["character_id"] != cid
                        for i in ((_read(run / "roles" / m["character_id"] / "setup.json", {}).get("briefing") or {}).get("conceal") or [])
                        if len(i["text"]) >= 4 and i["text"] not in heard]
    isolation_fail = []
    for n, call in enumerate(calls):
        shown = [_rid(r) for r in call["input"].get("records", [])]
        text = json.dumps(call["input"], ensure_ascii=False)
        unseen = [i for i in shown if i not in visible]
        leaked = ([s for s in secrets if s in text] + [t[:20] for t in other_thoughts if t in text]
                  + [t[:20] for t in others_concealed if t in text])
        if unseen or leaked:
            isolation_fail.append({"call": n, "stage": call["stage"], "unseen_records": unseen, "leaked": leaked})
    checks.append(_check("prompt_isolation", "证据与可见性", isolation_fail, len(calls)))

    briefing = setup.get("briefing") or {}
    concealed = [i["text"] for i in briefing.get("conceal", [])]
    express = [c for c in calls if c["stage"] == "express"]
    conceal_fail = [{"call": n, "item": t[:30]} for n, c in enumerate(express)
                    for t in concealed if t in json.dumps(c["input"], ensure_ascii=False)]
    checks.append(_check("concealed_withheld", "证据与可见性", conceal_fail, len(express) * max(len(concealed), 1)))
    terms = [{"call": n, "found": sorted(set(ID_PATTERN.findall(text) + (["dm."] if "dm." in text else [])))}
             for n, text in ((n, json.dumps(c["input"], ensure_ascii=False)) for n, c in enumerate(express))
             if ID_PATTERN.search(text) or "dm." in text]
    checks.append(_check("expression_need_to_know", "证据与可见性", terms, len(express)))

    hidden = []
    for construct_id, module in (profile.get("modules") or {}).items():
        try:
            construct = registry.construct(construct_id)
        except KeyError:
            continue
        if module["status"] == "active" and module["value"] not in (None, {}, []) and construct.self_visibility not in ("aware", "partial"):
            value = module["value"] if isinstance(module["value"], str) else json.dumps(module["value"], ensure_ascii=False, sort_keys=True)
            hidden.append((construct_id, construct.name, value))
    unaware_fail = []
    for n, call in enumerate(calls):
        text = json.dumps(call["input"], ensure_ascii=False, sort_keys=True)
        names = json.dumps(call["input"].get("self_profile", {}), ensure_ascii=False)
        for construct_id, name, value in hidden:
            if f'"{name}"' in names or (len(value) >= 4 and value in text):
                unaware_fail.append({"call": n, "stage": call["stage"], "construct": construct_id})
    checks.append(_check("unaware_withheld", "证据与可见性", unaware_fail, len(calls) * max(len(hidden), 1)))

    own_thoughts = [r for r in records if r["actor"]["actor_id"] == cid and r["kind"] == "thought"]
    thought_fail = [{"record": _rid(r), "to": sorted(r["recipients"])} for r in own_thoughts if set(r["recipients"]) != {cid}]
    checks.append(_check("thoughts_self_only", "证据与可见性", thought_fail, len(own_thoughts)))

    stable_fail = [] if final and final["stable_snapshot_id"] == profile.get("snapshot_id") else \
        [{"final": (final or {}).get("stable_snapshot_id"), "profile": profile.get("snapshot_id")}]
    checks.append(_check("stable_unchanged", "稳定一致性", stable_fail, 1))

    field_fail, checked = [], 0
    for layer in ("dynamic", "memory"):
        for construct_id, value in ((final or {}).get(layer) or {}).items():
            slot = SLOTS.get(construct_id)
            if slot is None:
                field_fail.append({"construct": construct_id, "error": "not in the state layout"})
                continue
            for keys, item in ([((), value)] if not slot.keys else _instances(slot, value)):
                checked += 1
                options = None
                if construct_id in OPTION_SLOTS and keys:
                    try:
                        options = set(registry.mechanism(keys[0]).options)
                    except KeyError:
                        options = None
                problem = check_instance(slot, item, options)
                if problem:
                    field_fail.append({"construct": construct_id, "error": problem[1]})
    checks.append(_check("fields_valid", "字段有效性", field_fail, checked))

    spoken = [r["content"] for r in records if r["actor"]["actor_id"] == cid and r["kind"] in ("speech", "action")]
    soft = Counter(f["code"] for p in phases for f in p["commit"]["soft_flags"])
    rejected = Counter(r["error"]["code"] for p in phases for r in p["commit"]["rejected"])
    return {"run": run.name, "role_id": cid, "manifest_status": manifest.get("status"),
            "passed": all(c["status"] == "pass" for c in checks), "checks": checks,
            "phases": len(phases), "model_calls": len(calls), "accepted_changes": len(accepted),
            "soft_flags": dict(soft), "rejected_changes": dict(rejected),
            "review_items": len(profile.get("review") or []),
            "concealed_mentioned": sum(len(mentions(s, briefing.get("conceal", []))) for s in spoken),
            "disclosed_mentioned": sum(len(mentions(s, briefing.get("disclose", []))) for s in spoken),
            "needs_human_scoring": HUMAN_DIMENSIONS,
            "evidence_boundary": "Automatic audit of structure, evidence, visibility, write permission and "
                                 "stable consistency. Passing does not show psychological validity; mock runs never do."}


def _instances(slot, value):
    """(key parts, instance) pairs of a keyed construct's value."""
    level = [((), value)]
    for _ in slot.keys:
        level = [(keys + (k,), v) for keys, node in level if isinstance(node, dict) for k, v in node.items()]
    return level


def render_markdown(report):
    lines = [f"# 自动审计报告：{report['run']}", "",
             f"- 角色：{report['role_id']}；运行状态：{report['manifest_status']}",
             f"- 结论：{'通过' if report['passed'] else '未通过'}（仅结构、证据与可见性层）", "",
             "| 检查 | 维度 | 结果 | 检查数 | 失败数 |", "|---|---|---|---|---|"]
    for c in report["checks"]:
        lines.append(f"| {c['id']} | {c['dimension']} | {c['status']} | {c['checked']} | {c['failures']} |")
    lines += ["", f"- 阶段 {report['phases']}；模型调用 {report['model_calls']}；写入的变化 {report['accepted_changes']}",
              f"- 软异常：{json.dumps(report['soft_flags'], ensure_ascii=False)}；被拒绝的变化：{json.dumps(report['rejected_changes'], ensure_ascii=False)}",
              f"- 档案待审核项：{report['review_items']}",
              f"- 说出口的设定条目（只统计，不判错）：隐瞒 {report['concealed_mentioned']}，透露 {report['disclosed_mentioned']}",
              f"- 需人工评分的维度：{'、'.join(report['needs_human_scoring'])}", ""]
    for c in report["checks"]:
        if c["details"]:
            lines.append(f"## {c['id']} 失败详情（最多 {MAX_DETAILS} 条）")
            lines += [f"- `{json.dumps(d, ensure_ascii=False)}`" for d in c["details"]]
            lines.append("")
    lines += ["", f"> {report['evidence_boundary']}", ""]
    return "\n".join(lines)


def audit_run(run_dir, registry=None):
    """Audit every role of a run; the run passes only if it completed and every role passes."""
    run = Path(run_dir)
    manifest = _read(run / "manifest.json")
    if manifest is None or manifest.get("pipeline") != "stages":
        raise ValueError(f"{run}: not a run directory of the staged pipeline")
    registry = registry or Registry.load()
    records, scene = _lines(run / "records.jsonl"), _read(run / "scene.json", {})
    roles = {m["character_id"]: audit_role(run, m["character_id"], manifest, records, scene, registry) for m in manifest["cast"]}
    return {"run": run.name, "manifest_status": manifest.get("status"),
            "passed": manifest.get("status") == "completed" and all(r["passed"] for r in roles.values()), "roles": roles}


def write_report(run_dir, registry=None):
    run = Path(run_dir)
    report = audit_run(run, registry)
    for cid, item in report["roles"].items():
        folder = run / "roles" / cid
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / "audit_report.json", item)
        (folder / "audit_report.md").write_text(render_markdown(item), encoding="utf-8")
    write_json(run / "audit_report.json", report)
    head = [f"# 自动审计：{report['run']}", "", f"- 结论：{'通过' if report['passed'] else '未通过'}"]
    head += [f"- {cid}：{'通过' if item['passed'] else '未通过'}" for cid, item in report["roles"].items()]
    body = [render_markdown(item).replace("# ", "## ", 1) for item in report["roles"].values()]
    (run / "audit_report.md").write_text("\n".join(head + [""] + body), encoding="utf-8")
    return report
