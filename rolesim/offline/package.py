"""Export review packages from finished runs (psychology reply, section 3.3-3.4).

Each role of each run is one blinded unit (R01, R02, ... in a seeded shuffle). For each unit:
  evaluator_samples.jsonl   one sample per committed phase (S1 / S2 / S3): the records that
                            triggered it, the product, the judgments, the state changes written
                            and refused with their reasons, the soft flags, the state ids around it
  role_visible_trace.jsonl  only what the role itself could see and do, for judging outward
                            behaviour without internal states or world truth
  counterpart_truth.json    world-private facts, records the role never saw, every other role's
                            opening setup and final state; for comparison only: whether the role's
                            inference was reasonable is judged from role-visible evidence
  role_setup.json           the role's seat, opening setup and bound skills
  annotation_template.json  scoring sheet (dimensions, anchors, one row per sample)
  auto_audit.json           the automatic audit's per-check verdicts, if the run was audited
Method labels (mode, skills on / off, backend, model, code version) live only in blind_key.json
at the package root, so the per-unit folders can be handed to raters on their own.
"""
import json
import random
from pathlib import Path

from ..catalog import write_json

DIMENSIONS = ["字段有效性", "证据与可见性", "机制与写回相容性", "稳定一致性", "动态合理性",
              "角色可区分性", "外显行为与隐状态匹配", "错误推断的合理性", "轨迹可追溯性"]
AUTO_DIMENSIONS = {"字段有效性": "fields_valid", "证据与可见性": "evidence_visible", "稳定一致性": "stable_unchanged",
                   "轨迹可追溯性": "trajectory_traceable", "机制与写回相容性": "operator_write_compatible"}
TRAJECTORY_DIMENSIONS = ["动态合理性", "角色可区分性", "外显行为与隐状态匹配"]
HARD_ERROR_TYPES = ["证据不可见", "越权写入", "稳定属性被改", "前后状态断裂", "信息泄露"]
ANCHORS = {0: "结构/证据/理论上无法成立或完全无法解释",
           1: "方向可能相关，但缺少关键证据、机制或幅度依据",
           2: "基本符合角色与事件，只有轻微遗漏或表述问题",
           3: "证据可见、机制匹配、方向和幅度合适、前后状态连续且能解释外显行为"}


def _lines(path):
    path = Path(path)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _rid(record):
    return f"{record['action_id']}#{record['order_index']}"


def _record_view(record):
    return {"id": _rid(record), "turn": record["turn_index"], "speaker": record["actor"]["name"], "kind": record["kind"],
            "text": record["content"], "to": [e["name"] for e in record["recipients"].values()]}


def _samples(phases, visible, blind):
    samples, shown = [], []
    for phase in phases:
        stage = next(iter(phase["products"]))
        commit = phase["commit"]
        if stage == "S2":
            cited = {e["event"] for c in commit["accepted"] for e in c["evidence"]}
            cited |= {e["event"] for j in phase["judgments"] for e in j["evidence"]}
            trigger = [visible[i] for i in visible if i in cited] or shown[-3:]
        else:
            trigger = [r for i, r in visible.items() if r["action_id"] == phase["event_id"]]
        shown += [r for r in trigger if r not in shown]
        samples.append({"sample_id": f"{blind}-p{phase['phase_no']:03d}-{stage}", "stage": stage, "event_id": phase["event_id"],
                        "pre_state_id": phase["input_state_id"], "post_state_id": phase["output_state_id"],
                        "trigger_records": [_record_view(r) for r in trigger], "product": phase["products"][stage],
                        "judgments": phase["judgments"], "accepted_changes": commit["accepted"],
                        "rejected_changes": commit["rejected"], "soft_flags": commit["soft_flags"]})
    return samples


def _template(samples, blind):
    return {"run": blind, "rater_id": None, "scoring_anchors": ANCHORS,
            "samples": [{"sample_id": s["sample_id"], "scores": {d: None for d in DIMENSIONS if d not in AUTO_DIMENSIONS},
                         "hard_error": None, "hard_error_type": None, "reason": "", "event_ids": []} for s in samples],
            "trajectory_scores": {d: None for d in TRAJECTORY_DIMENSIONS}, "trajectory_reason": ""}


def export_package(run_dirs, out_dir, seed=0):
    out = Path(out_dir)
    runs = [Path(r) for r in run_dirs]
    if not runs:
        raise ValueError("no run directories given")
    units = []
    for run in runs:
        manifest = _read(run / "manifest.json")
        if manifest is None or manifest.get("pipeline") != "stages":
            raise ValueError(f"{run.name}: not a run directory of the staged pipeline")
        if manifest.get("status") != "completed":
            raise ValueError(f"{run.name}: only completed runs can be exported")
        units += [(run, manifest, seat) for seat in manifest["cast"]]
    out.mkdir(parents=True, exist_ok=False)
    random.Random(seed).shuffle(units)
    key, files = {}, []
    for rank, (run, manifest, seat) in enumerate(units, 1):
        blind, cid = f"R{rank:02d}", seat["character_id"]
        records = _lines(run / "records.jsonl")
        visible = {_rid(r): r for r in records if r["actor"]["actor_id"] == cid or cid in r["recipients"]}
        phases = _lines(run / "trace" / manifest["interaction_id"] / cid / "phases.jsonl")
        samples = _samples(phases, visible, blind)
        folder = out / blind
        folder.mkdir()
        (folder / "evaluator_samples.jsonl").write_text("".join(json.dumps(s, ensure_ascii=False) + "\n" for s in samples),
                                                         encoding="utf-8")
        (folder / "role_visible_trace.jsonl").write_text(
            "".join(json.dumps(_record_view(r), ensure_ascii=False) + "\n" for r in visible.values()), encoding="utf-8")
        others = {}
        for other in manifest["cast"]:
            if other["character_id"] != cid:
                o = run / "roles" / other["character_id"]
                others[other["title"]] = {"setup": (_read(o / "setup.json", {}) or {}).get("briefing"),
                                          "final_state": _read(o / "final_state.json")}
        write_json(folder / "counterpart_truth.json", {
            "use": "comparison only; plausibility of the role's inference is judged from role-visible evidence, "
                   "not from agreement with this file",
            "world_private": (_read(run / "scene.json", {}) or {}).get("world_private"),
            "role_invisible_records": [_record_view(r) for r in records if _rid(r) not in visible],
            "counterparts": others})
        setup = _read(run / "roles" / cid / "setup.json", {}) or {}
        # Bound skills are listed whether or not the run used them, so the folder keeps the blind.
        write_json(folder / "role_setup.json", {"seat": seat["title"], "briefing": setup.get("briefing"),
                                                "bound_skills": setup.get("bound_skills", [])})
        write_json(folder / "annotation_template.json", _template(samples, blind))
        audit = _read(run / "roles" / cid / "audit_report.json")
        write_json(folder / "auto_audit.json", None if audit is None else {
            "checks": [{k: c[k] for k in ("id", "dimension", "status", "failures")} for c in audit["checks"]],
            "rejected_changes": audit["rejected_changes"], "soft_flags": audit["soft_flags"]})
        info = manifest["roles"].get(cid, {})
        key[blind] = {"run": run.name, "character_id": cid, "seat": seat["title"], "mode": manifest["mode"],
                      "use_skills": manifest.get("use_skills"), "backend": manifest["backend"], "model": manifest["model"],
                      "code_version": manifest.get("code_version"), "origin": manifest.get("origin"),
                      "profile_snapshot": info.get("profile_snapshot"), "scenario_hash": manifest.get("scenario_hash")}
        files.append(blind)
    write_json(out / "blind_key.json", {"warning": "method labels; keep away from raters", "runs": key})
    write_json(out / "package.json", {
        "runs": files, "unit": "one role in one run", "seed": seed, "dimensions": DIMENSIONS,
        "auto_audited_dimensions": AUTO_DIMENSIONS, "hard_error_types": HARD_ERROR_TYPES,
        "protocol": "sample package -> auto audit -> human scoring -> second-rater re-score -> report",
        "per_run_files": ["evaluator_samples.jsonl", "role_visible_trace.jsonl", "counterpart_truth.json",
                          "role_setup.json", "annotation_template.json", "auto_audit.json"],
        "evidence_boundary": "The evaluation protocol is not frozen. Prompts and method labels are deliberately "
                             "excluded from per-unit files. A package built from mock runs checks the export, not the role."})
    return {"package": str(out), "runs": files}
