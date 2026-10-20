"""Export review packages from finished runs (psychology reply, section 3.3-3.4).

Each role of each run is one blinded unit. For each unit the export writes:
  - evaluator_samples.jsonl: one sample per state transition with the context an
    evaluator needs (pre/post state, role-visible trigger events, model output, every
    candidate and its fate);
  - role_visible_trace.jsonl: only what the role itself could see and do, for judging
    outward behaviour without access to internal states or world truth;
  - counterpart_truth.json: world-private data, role-invisible events, and every
    other role's opening setup and final state, for comparison only. Whether the role's inference was reasonable is judged from role-visible
    evidence, never from agreement with this file.
Method labels (mode, backend, model, code version) live only in blind_key.json at the
package root, so the per-run folders can be handed to raters on their own.
"""
import json
import random
from pathlib import Path

from .catalog import write_json
from .state import VERSIONS

DIMENSIONS = ["字段有效性", "证据与可见性", "机制与写回相容性", "稳定一致性", "动态合理性",
              "角色可区分性", "外显行为与隐状态匹配", "错误推断的合理性", "轨迹可追溯性"]
AUTO_DIMENSIONS = {"字段有效性": "dynamic_fields_valid", "证据与可见性": "evidence_visible",
                   "稳定一致性": "stable_unchanged", "轨迹可追溯性": "trajectory_traceable",
                   "机制与写回相容性": "operator_write_compatible"}
ANCHORS = {0: "结构/证据/理论上无法成立或完全无法解释",
           1: "方向可能相关，但缺少关键证据、机制或幅度依据",
           2: "基本符合角色与事件，只有轻微遗漏或表述问题",
           3: "证据可见、机制匹配、方向和幅度合适、前后状态连续且能解释外显行为"}
# Proposed list: the reply asks for a hard-error type on the form but does not enumerate them.
HARD_ERROR_TYPES = ["invisible_evidence", "missing_evidence", "out_of_range", "unregistered_operator",
                    "no_write_permission", "inactive_module", "stable_modified", "unparseable", "other"]
TRAJECTORY_DIMENSIONS = ["stable一致性", "动态连续性", "因果可追溯性", "角色可区分性", "心理理论符合度",
                         "外显行为与隐状态匹配", "错误推断的合理性"]


def _rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if path.exists() else []


def _event_view(event):
    return {"event_id": event["event_id"], "turn": event["turn"], "type": event["type"],
            "actor": event["actor"], "content": event["content"], "outcome": event.get("outcome")}


def _state_view(snap):
    return None if snap is None else {"state_id": snap["state_id"], "dynamic": snap["dynamic"],
                                      "module_status": snap["module_status"]}


def _samples(run, role, blind):
    folder = run / "roles" / role
    inputs = json.loads((folder / "inputs.json").read_text(encoding="utf-8"))
    events = json.loads((run / "environment_events.json").read_text(encoding="utf-8"))
    visible = {e["event_id"]: e for e in events if role in e["visibility"]["audience"]}
    states = _rows(folder / "states.jsonl")
    records = _rows(folder / "audit.jsonl")
    log = _rows(folder / "mechanism_log.jsonl")
    samples, previous = [], None
    for index, snap in enumerate(states):
        record = records[index // 2]
        phase = "step" if index % 2 == 0 else "perceive"
        new_ids = [i for i in snap["memory"] if previous is None or i not in previous["memory"]]
        sample = {
            "sample_id": f"{blind}-t{snap['turn']:03d}-{phase}", "phase": phase, "turn": snap["turn"],
            "pre_state": _state_view(previous) if previous else None,
            "pre_state_note": None if previous else "initial state is not recorded in the run; "
                                                    "see each change's `before` values",
            "trigger_events": [_event_view(visible[i]) for i in new_ids if i in visible],
            "related_history": [_event_view(visible[i]) for i in (previous["memory"] if previous else [])
                                if i in visible],
            "post_state": _state_view(snap),
            "delta": record["step_delta" if phase == "step" else "perceive_delta"],
            "mechanism_log": [e for e in log if e.get("output_state_id") == snap["state_id"]],
        }
        if phase == "step":
            sample["model_output"] = {"decision": record["decision"], "thought": record["thought"],
                                      "proposal": record["proposal"]}
        samples.append(sample)
        previous = snap
    return samples, events, inputs, role, visible


def _role_trace(records, events, role, visible, titles):
    trace = []
    proposals = {r["proposal"]["proposal_id"]: r for r in records}
    for event in sorted(visible.values(), key=lambda e: e["turn"]):
        item = {"turn": event["turn"], "speaker": titles.get(event["actor"], event["actor"]), "kind": event["type"],
                "text": event["content"]}
        if event.get("caused_by") in proposals and event["actor"] == role:
            item["action"] = proposals[event["caused_by"]]["proposal"]["intent_note"]
        trace.append(item)
    return trace


def _template(samples, blind):
    return {"run": blind, "rater_id": None, "scoring_anchors": ANCHORS,
            "samples": [{"sample_id": s["sample_id"],
                         "scores": {d: None for d in DIMENSIONS if d not in AUTO_DIMENSIONS},
                         "hard_error": None, "hard_error_type": None, "reason": "", "event_ids": []}
                        for s in samples],
            "trajectory_scores": {d: None for d in TRAJECTORY_DIMENSIONS}, "trajectory_reason": ""}


def _counterparts(run, manifest, role):
    out = {}
    for seat in manifest["cast"]:
        if seat["role_id"] == role:
            continue
        folder = run / "roles" / seat["role_id"]
        inputs = json.loads((folder / "inputs.json").read_text(encoding="utf-8"))
        final = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        out[seat["title"]] = {"briefing": inputs.get("briefing"), "final_state": _state_view(final)}
    return out


def export_package(run_dirs, out_dir, seed=0):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=False)
    runs = [Path(r) for r in run_dirs]
    if not runs:
        raise ValueError("no run directories given")
    units = []
    for run in runs:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "completed":
            raise ValueError(f"{run.name}: only completed runs can be exported")
        units += [(run, manifest, seat["role_id"]) for seat in manifest["cast"]]
    random.Random(seed).shuffle(units)
    key, files = {}, []
    for rank, (run, manifest, role) in enumerate(units, 1):
        blind = f"R{rank:02d}"
        titles = {"env": "旁白", **{s["role_id"]: s["title"] for s in manifest["cast"]}}
        samples, events, inputs, role, visible = _samples(run, role, blind)
        folder = out / blind
        folder.mkdir()
        (folder / "evaluator_samples.jsonl").write_text(
            "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples), encoding="utf-8")
        trace = _role_trace(_rows(run / "roles" / role / "audit.jsonl"), events, role, visible, titles)
        (folder / "role_visible_trace.jsonl").write_text(
            "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in trace), encoding="utf-8")
        hidden = [_event_view(e) for e in events if role not in e["visibility"]["audience"]]
        write_json(folder / "counterpart_truth.json", {
            "use": "comparison only; plausibility of the role's inference is judged from role-visible "
                   "evidence, not from agreement with this file",
            "world_private": inputs["scenario"]["world_private"], "role_invisible_events": hidden,
            "counterparts": _counterparts(run, manifest, role)})
        # Bound skills are the same whether or not a run used them, so listing them keeps the blind.
        write_json(folder / "role_setup.json", {
            "seat": titles[role], "briefing": inputs.get("briefing"),
            "bound_skills": [s["name"] for s in inputs.get("skills") or []]})
        write_json(folder / "annotation_template.json", _template(samples, blind))
        report = run / "roles" / role / "audit_report.json"
        audit = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
        write_json(folder / "auto_audit.json", None if audit is None else {
            "checks": [{k: c[k] for k in ("id", "dimension", "status", "failures")} for c in audit["checks"]],
            "rejected_groups": audit["rejected_groups"], "soft_violations": audit["soft_violations"],
            "exceptions_approved": audit["exceptions_approved"]})
        info = manifest["roles"][role]
        key[blind] = {"run": run.name, "role_id": role, "seat": titles[role], "mode": manifest["mode"],
                      "appraise_state": manifest.get("appraise_state"), "environment": manifest.get("environment"),
                      "skills": info.get("skills"), "origin": manifest.get("origin"),
                      "backend": manifest["backend"], "model": manifest["model"],
                      "model_served": manifest.get("model_served"), "code_version": manifest.get("code_version"),
                      "persona_hash": info["persona_hash"], "scenario_hash": manifest["scenario_hash"]}
        files.append(blind)
    write_json(out / "blind_key.json", {"warning": "method labels; keep away from raters", "runs": key})
    write_json(out / "package.json", {
        "runs": files, "unit": "one role in one run", "seed": seed, "versions": dict(VERSIONS),
        "dimensions": DIMENSIONS, "auto_audited_dimensions": AUTO_DIMENSIONS, "hard_error_types": HARD_ERROR_TYPES,
        "protocol": "sample package -> auto audit -> human scoring -> second-rater re-score -> report",
        "per_run_files": ["evaluator_samples.jsonl", "role_visible_trace.jsonl", "counterpart_truth.json",
                          "role_setup.json", "annotation_template.json", "auto_audit.json"],
        "evidence_boundary": "The evaluation protocol is not frozen (evaluation_protocol_version "
                             "'unfrozen'). Controller prompts and method labels are deliberately excluded "
                             "from per-run files. A package built from mock runs checks the export, "
                             "not the role."})
    return {"package": str(out), "runs": files}


