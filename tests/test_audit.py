import copy
import json
import tempfile
import unittest
from pathlib import Path

from rolesim.audit import audit_run, write_report
from rolesim.providers import MockBackend
from rolesim.runtime import run_episode
from fixtures import load, setup


ROOT = Path(__file__).resolve().parents[1]




def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_lines(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        self.manifest = run_episode(load("role1"), load("test1"),
                                    MockBackend(), self.run_dir)

    def failed(self):
        return {c["id"]: c for c in audit_run(self.run_dir)["checks"] if c["status"] == "fail"}

    def edit_states(self, edit):
        rows = read_lines(self.run_dir / "states.jsonl")
        for row in rows:
            edit(row)
        write_lines(self.run_dir / "states.jsonl", rows)

    def edit_audit(self, edit):
        rows = read_lines(self.run_dir / "audit.jsonl")
        for row in rows:
            edit(row)
        write_lines(self.run_dir / "audit.jsonl", rows)

    def test_clean_run_passes_and_writes_reports(self):
        self.assertTrue(self.manifest["audit"]["passed"])
        report = json.loads((self.run_dir / "audit_report.json").read_text())
        self.assertEqual([c["status"] for c in report["checks"]], ["pass"] * 6)
        self.assertEqual(report["error_rate"]["rejected_groups"], 0)
        self.assertIn("unknown", report["module_coverage"])
        self.assertIn("does not show", report["evidence_boundary"])
        self.assertIn("需人工评分的维度", (self.run_dir / "audit_report.md").read_text())

    def test_detects_modified_stable(self):
        self.edit_states(lambda s: s["stable"]["traits"]["affective_capacities"].update(threat_sensitivity=-2))
        self.assertIn("stable_unchanged", self.failed())

    def test_detects_out_of_range_and_inconsistent_status(self):
        self.edit_states(lambda s: s["dynamic"]["relation_state"]["partner"].update(trust=1.7))
        self.assertIn("dynamic_fields_valid", self.failed())

    def test_detects_unknown_module_holding_a_value(self):
        self.edit_states(lambda s: s["dynamic"]["relation_state"]["partner"].update(closeness=0))
        self.assertIn("dynamic_fields_valid", self.failed())

    def test_detects_change_without_or_with_invisible_evidence(self):
        def strip(row):
            for key in ("step_delta", "perceive_delta"):
                for change in row[key]["changes"]:
                    change["evidence"] = ["partner_private"]
        self.edit_audit(strip)
        self.assertIn("evidence_visible", self.failed())
        self.edit_audit(lambda r: [c.update(evidence=[]) for k in ("step_delta", "perceive_delta")
                                   for c in r[k]["changes"]])
        self.assertIn("evidence_visible", self.failed())

    def test_detects_write_outside_operator_permission_or_inactive_module(self):
        def redirect(row):
            for key in ("step_delta", "perceive_delta"):
                for change in row[key]["changes"]:
                    change["path"] = "dynamic.group_state.norms"
        self.edit_audit(redirect)
        failed = self.failed()
        self.assertIn("operator_write_compatible", failed)

    def test_detects_hidden_event_content_in_controller_input(self):
        events = json.loads((self.run_dir / "environment_events.json").read_text())
        hidden = [e for e in events if "role1" not in e["visibility"]["audience"]]
        if not hidden:
            self.skipTest("example scenario has no role-invisible event")
        self.edit_audit(lambda r: r["controller_input"].update(leak=hidden[0]["content"]))
        self.assertIn("evidence_visible", self.failed())

    def test_detects_broken_state_chain_and_missing_versions(self):
        rows = read_lines(self.run_dir / "states.jsonl")
        rows[2]["prev_state_id"] = "role1@t000"
        del rows[1]["versions"]["update_rule_version"]
        write_lines(self.run_dir / "states.jsonl", rows)
        self.assertIn("trajectory_traceable", self.failed())

    def test_rejected_groups_and_soft_flags_are_reported_not_hidden(self):
        log = read_lines(self.run_dir / "mechanism_log.jsonl")
        log[0]["rejected"] = [{"group": "relation", "kind": "hard", "reason": "stale"}]
        log[0]["flags"] = [{"path": "dynamic.relation_state.partner.trust", "flag": "soft_violation"}]
        write_lines(self.run_dir / "mechanism_log.jsonl", log)
        report = audit_run(self.run_dir)
        self.assertEqual(len(report["rejected_groups"]), 1)
        self.assertEqual(len(report["soft_violations"]), 1)
        self.assertGreater(report["error_rate"]["rejected_groups"], 0)

    def test_write_report_can_be_rerun(self):
        self.assertTrue(write_report(self.run_dir)["passed"])


if __name__ == "__main__":
    unittest.main()
