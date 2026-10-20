"""Automatic audit of a finished run directory (psychology reply, section 3.2-3.3).

The audit re-derives its verdicts from the run's own artifacts instead of trusting the
engine's bookkeeping. It covers only the dimensions the reply assigns to automatic audit:
field validity, evidence and visibility, mechanism/write compatibility, stable
consistency and traceability. The other dimensions need human scoring, and a clean
report says nothing about whether the simulated role is psychologically plausible.
"""
import json
from pathlib import Path

from .briefing import mentions, recap_entries
from .catalog import digest, write_json
from .state import OPERATORS, VERSIONS, may_write, validate_dynamic
from .modules import governing

HUMAN_DIMENSIONS = ["机制与写回相容性（理论层面）", "动态合理性", "角色可区分性", "外显行为与隐状态匹配",
                    "错误推断的合理性"]
MAX_DETAILS = 20


def _lines(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _check(check_id, dimension, failures, total, note=""):
    return {"id": check_id, "dimension": dimension, "status": "fail" if failures else "pass",
            "checked": total, "failures": len(failures), "details": failures[:MAX_DETAILS], "note": note}


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _prompts(rec):
    """Everything this role's model calls were given in one turn."""
    parts = [rec.get("controller_input"), rec.get("expression_input")]
    parts += [a.get("input") for a in rec.get("appraisals", [])]
    return json.dumps(parts, ensure_ascii=False)


def audit_role(run, role, manifest, events):
    folder = run / "roles" / role
    inputs = _read(folder / "inputs.json")
    states = _lines(folder / "states.jsonl")
    records = _lines(folder / "audit.jsonl")
    mech_log = _lines(folder / "mechanism_log.jsonl")
    briefing = inputs.get("briefing")
    visible = {e["event_id"] for e in events if role in e["visibility"]["audience"]}
    # The recap is the role's own memory of earlier history, seeded before the first event.
    visible |= {e["id"] for e in recap_entries(briefing)}
    hidden = [e for e in events if role not in e["visibility"]["audience"]]
    by_id = {s["state_id"]: s for s in states}

    # -- field validity -----------------------------------------------------
    failures = []
    for snap in states:
        try:
            validate_dynamic(snap["dynamic"], snap["module_status"])
        except (ValueError, KeyError, TypeError) as exc:
            failures.append({"state_id": snap.get("state_id"), "error": str(exc)})
    field_check = _check("dynamic_fields_valid", "字段有效性", failures, len(states),
                         "ranges, null/active consistency, ungoverned values, source lists")

    # -- stable consistency -------------------------------------------------
    stable_id = digest(inputs["persona"]["stable"])
    failures = [{"state_id": s["state_id"], "error": "stable differs from the run's persona"}
                for s in states if digest(s["stable"]) != stable_id or s.get("stable_snapshot_id") != stable_id]
    if manifest["roles"][role].get("stable_snapshot_id") not in (None, stable_id):
        failures.append({"state_id": None, "error": "manifest stable_snapshot_id disagrees with inputs"})
    stable_check = _check("stable_unchanged", "稳定一致性", failures, len(states))

    # -- traceability -------------------------------------------------------
    failures, previous = [], None
    for snap in states:
        if snap["prev_state_id"] != previous:
            failures.append({"state_id": snap["state_id"], "error": f"prev_state_id {snap['prev_state_id']!r} != {previous!r}"})
        if set(snap.get("versions", {})) != set(VERSIONS):
            failures.append({"state_id": snap["state_id"], "error": "missing or extra version numbers"})
        previous = snap["state_id"]
    for entry in mech_log:
        if entry.get("output_state_id") not in by_id:
            failures.append({"commit_key": entry.get("commit_key"), "error": "mechanism_log points at an unknown state"})
    if set(manifest.get("versions", {})) != set(VERSIONS):
        failures.append({"state_id": None, "error": "manifest lacks the four version numbers"})
    trace_check = _check("trajectory_traceable", "轨迹可追溯性", failures, len(states) + len(mech_log))

    # -- evidence, visibility, write compatibility ---------------------------
    ev_fail, write_fail, changes_seen = [], [], 0
    own = set()
    for rec in records:
        own.add(rec["proposal"]["proposal_id"])
        for delta in (rec["step_delta"], rec["perceive_delta"]):
            snap = by_id.get(delta.get("to_state_id"))
            known = (set(snap["memory"]) if snap else set()) | own | {e["id"] for e in recap_entries(briefing)}
            for change in delta.get("changes", []):
                changes_seen += 1
                where = {"state_id": delta.get("to_state_id"), "path": change.get("path")}
                evidence = change.get("evidence")
                if not evidence or not isinstance(evidence, list):
                    ev_fail.append({**where, "error": "change without evidence"})
                    continue
                for ref in evidence:
                    if ref not in visible and ref not in own:
                        ev_fail.append({**where, "error": f"evidence {ref!r} was never visible to the role"})
                    elif ref not in known:
                        ev_fail.append({**where, "error": f"evidence {ref!r} not yet seen at this state"})
                operator, path = change.get("mechanism"), change.get("path", "")
                if operator not in OPERATORS:
                    write_fail.append({**where, "error": f"unregistered operator {operator!r}"})
                elif not may_write(operator, path):
                    write_fail.append({**where, "error": f"{operator} may not write {path}"})
                elif snap is not None:
                    module = governing(snap["module_status"], path)
                    if module is None or snap["module_status"][module]["status"] != "active":
                        write_fail.append({**where, "error": "write to a module that is not active"})
                if str(path).startswith("stable"):
                    write_fail.append({**where, "error": "change addresses stable"})
        decision_ids = rec["decision"].get("evidence_ids", [])
        if not decision_ids or any(i not in visible and i not in own for i in decision_ids):
            ev_fail.append({"state_id": None, "turn": rec["turn"], "error": "decision cites unavailable information"})
    # Role-invisible events must not appear in anything handed to the controller.
    prompts = [json.dumps(rec["controller_input"], ensure_ascii=False) for rec in records]
    leaks = [{"turn": i + 1, "event_id": e["event_id"], "error": "hidden event content reached the controller"}
             for e in hidden for i, text in enumerate(prompts)
             if e["event_id"] in text or (len(e["content"]) > 6 and e["content"] in text)]
    evidence_check = _check("evidence_visible", "证据与可见性", ev_fail + leaks,
                            changes_seen + len(records) * (1 + len(hidden)))
    write_check = _check("operator_write_compatible", "机制与写回相容性（权限层）", write_fail, changes_seen,
                         "operator registry, path permission and module status only; theory fit needs human scoring")

    # -- concealed information (role setup) ----------------------------------
    secrets = briefing["conceal"] if briefing else []
    conceal_fail, disclosed = [], []
    for rec in records:
        speech = rec["proposal"]["content"]
        expression = json.dumps(rec.get("expression_input"), ensure_ascii=False)
        for item in secrets:
            if item["text"] in expression:
                conceal_fail.append({"turn": rec["turn"], "item": item["id"],
                                     "error": "concealed text reached the expression prompt"})
        for item_id in mentions(speech, secrets):
            conceal_fail.append({"turn": rec["turn"], "item": item_id, "error": "concealed item spoken"})
        disclosed += [{"turn": rec["turn"], "item": i} for i in mentions(speech, briefing["disclose"] if briefing else [])]
    conceal_check = _check("concealed_info_withheld", "证据与可见性", conceal_fail, len(records) * len(secrets),
                           "keyword markers only; paraphrased leaks need human review")

    # -- the other roles' private material and world-private data ------------
    private = []
    for other in manifest["cast"]:
        if other["role_id"] == role:
            continue
        theirs = _read(run / "roles" / other["role_id"] / "inputs.json").get("briefing") or {}
        private += [(f"{other['role_id']}:{i['id']}", i["text"]) for i in theirs.get("conceal", [])]
    private += [("world_private", s) for s in _strings(inputs["scenario"]["world_private"]) if len(s) >= 6]
    cross_fail = [{"turn": rec["turn"], "item": label, "error": "another role's secret or world-private data "
                   "reached this role's model calls"}
                  for rec in records for label, value in private if value in _prompts(rec)]
    cross_check = _check("cross_role_isolation", "证据与可见性", cross_fail, len(records) * len(private),
                         "exact text of the other roles' concealed items and world-private strings")

    # -- soft constraints, exceptions, rejections (reported, not failures) ---
    flags = [{"commit_key": e["commit_key"], **f} for e in mech_log for f in e.get("flags", [])]
    rejected = [{"commit_key": e["commit_key"], **r} for e in mech_log for r in e.get("rejected", [])]
    candidates = sum(len(e.get("candidates", [])) for e in mech_log)
    final = states[-1] if states else None
    coverage = {}
    if final:
        for item in final["module_status"].values():
            coverage[item["status"]] = coverage.get(item["status"], 0) + 1

    checks = [field_check, evidence_check, write_check, stable_check, trace_check, conceal_check, cross_check]
    return {
        "run": run.name, "role_id": role, "manifest_status": manifest.get("status"),
        "passed": all(c["status"] == "pass" for c in checks) and manifest.get("status") == "completed",
        "checks": checks,
        "soft_violations": [f for f in flags if f["flag"] == "soft_violation"],
        "exceptions_approved": [f for f in flags if f["flag"] == "exception_approved"],
        "rejected_groups": rejected,
        "disclosed_items": disclosed,
        "error_rate": {"candidate_changes": candidates, "rejected_groups": len(rejected)},
        "module_coverage": coverage,
        "status_transitions": manifest["roles"][role].get("status_transitions"),
        "needs_human_scoring": HUMAN_DIMENSIONS,
        "evidence_boundary": "Automatic audit of structure, evidence, visibility, write permission and "
                             "stable consistency. Passing does not show psychological validity; "
                             "mock runs never do.",
    }


def render_markdown(report):
    lines = [f"# 自动审计报告：{report['run']}", "",
             f"- 角色：{report['role_id']}；运行状态：{report['manifest_status']}",
             f"- 结论：{'通过' if report['passed'] else '未通过'}（仅结构与证据层）", "",
             "| 检查 | 维度 | 结果 | 检查数 | 失败数 |", "|---|---|---|---|---|"]
    for c in report["checks"]:
        lines.append(f"| {c['id']} | {c['dimension']} | {c['status']} | {c['checked']} | {c['failures']} |")
    rate = report["error_rate"]
    lines += ["", f"- 软约束违反：{len(report['soft_violations'])}；豁免：{len(report['exceptions_approved'])}",
              f"- 被拒依赖组：{rate['rejected_groups']} / 候选变化 {rate['candidate_changes']}",
              f"- 主动透露（简报 disclose 条目被说出）：{len(report['disclosed_items'])} 次（只统计，不判错）",
              f"- 模块覆盖（末态）：{json.dumps(report['module_coverage'], ensure_ascii=False)}",
              f"- 需人工评分的维度：{'、'.join(report['needs_human_scoring'])}", ""]
    for c in report["checks"]:
        if c["details"]:
            lines.append(f"## {c['id']} 失败详情（最多 {MAX_DETAILS} 条）")
            lines += [f"- `{json.dumps(d, ensure_ascii=False)}`" for d in c["details"]]
            lines.append("")
    lines += ["", f"> {report['evidence_boundary']}", ""]
    return "\n".join(lines)


def audit_run(run_dir):
    """Audit every role of a run; the run passes only if every role does."""
    run = Path(run_dir)
    manifest = _read(run / "manifest.json")
    events = _read(run / "environment_events.json")
    roles = {c["role_id"]: audit_role(run, c["role_id"], manifest, events) for c in manifest["cast"]}
    return {"run": run.name, "manifest_status": manifest.get("status"),
            "passed": manifest.get("status") == "completed" and all(r["passed"] for r in roles.values()),
            "roles": roles}


def write_report(run_dir):
    run = Path(run_dir)
    report = audit_run(run)
    for role, item in report["roles"].items():
        write_json(run / "roles" / role / "audit_report.json", item)
        (run / "roles" / role / "audit_report.md").write_text(render_markdown(item), encoding="utf-8")
    write_json(run / "audit_report.json", report)
    head = [f"# 自动审计：{report['run']}", "", f"- 结论：{'通过' if report['passed'] else '未通过'}"]
    head += [f"- {role}：{'通过' if item['passed'] else '未通过'}" for role, item in report["roles"].items()]
    body = [render_markdown(item).replace("# ", "## ", 1) for item in report["roles"].values()]
    (run / "audit_report.md").write_text("\n".join(head + [""] + body), encoding="utf-8")
    return report
