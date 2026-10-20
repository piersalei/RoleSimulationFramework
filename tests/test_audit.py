import json
import tempfile
import unittest
from pathlib import Path

from rolesim.audit import audit_run, write_report
from fixtures import run_scene, setup

CHECKS = ["dynamic_fields_valid", "evidence_visible", "operator_write_compatible", "stable_unchanged",
          "trajectory_traceable", "concealed_info_withheld", "cross_role_isolation"]


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_lines(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / "run"
        self.manifest = run_scene(self.run_dir)
        self.role_dir = self.run_dir / "roles" / "role1"

    def failed(self, role="role1"):
        report = audit_run(self.run_dir)
        return {c["id"]: c for c in report["roles"][role]["checks"] if c["status"] == "fail"}

    def edit(self, name, edit):
        rows = read_lines(self.role_dir / name)
        for row in rows:
            edit(row)
        write_lines(self.role_dir / name, rows)

    def test_clean_run_passes_and_writes_reports(self):
        self.assertTrue(self.manifest["audit"]["passed"])
        self.assertEqual(self.manifest["audit"]["failed_checks"], {"role1": [], "role2": []})
        report = json.loads((self.run_dir / "audit_report.json").read_text())
        for role in ("role1", "role2"):
            item = json.loads((self.run_dir / "roles" / role / "audit_report.json").read_text())
            self.assertEqual(item, report["roles"][role])
            self.assertEqual([c["id"] for c in item["checks"]], CHECKS)
            self.assertEqual([c["status"] for c in item["checks"]], ["pass"] * len(CHECKS))
            self.assertEqual(item["error_rate"]["rejected_groups"], 0)
            self.assertIn("unknown", item["module_coverage"])
            self.assertIn("does not show", item["evidence_boundary"])
        self.assertIn("需人工评分的维度", (self.run_dir / "audit_report.md").read_text())

    def test_detects_modified_stable(self):
        self.edit("states.jsonl", lambda s: s["stable"]["traits"]["affective_capacities"].update(threat_sensitivity=-2))
        self.assertIn("stable_unchanged", self.failed())
        self.assertFalse(audit_run(self.run_dir)["passed"])

    def test_detects_out_of_range_and_inconsistent_status(self):
        self.edit("states.jsonl", lambda s: s["dynamic"]["relation_state"]["partner"].update(trust=1.7))
        self.assertIn("dynamic_fields_valid", self.failed())

    def test_detects_unknown_module_holding_a_value(self):
        self.edit("states.jsonl", lambda s: s["dynamic"]["relation_state"]["partner"].update(closeness=0))
        self.assertIn("dynamic_fields_valid", self.failed())

    def test_detects_change_without_or_with_invisible_evidence(self):
        def foreign(row):
            for key in ("step_delta", "perceive_delta"):
                for change in row[key]["changes"]:
                    change["evidence"] = ["partner_private"]
        self.edit("audit.jsonl", foreign)
        self.assertIn("evidence_visible", self.failed())
        self.edit("audit.jsonl", lambda r: [c.update(evidence=[]) for k in ("step_delta", "perceive_delta")
                                            for c in r[k]["changes"]])
        self.assertIn("evidence_visible", self.failed())

    def test_detects_write_outside_operator_permission_or_inactive_module(self):
        def redirect(row):
            for key in ("step_delta", "perceive_delta"):
                for change in row[key]["changes"]:
                    change["path"] = "dynamic.group_state.norms"
        self.edit("audit.jsonl", redirect)
        self.assertIn("operator_write_compatible", self.failed())

    def test_detects_the_other_roles_secret_or_world_data_in_any_call(self):
        secret = setup("role2")["conceal"][0]["text"]
        self.edit("audit.jsonl", lambda r: r["appraisals"][0]["input"].update(leak=secret) if r["appraisals"] else None)
        failed = self.failed()
        self.assertIn("cross_role_isolation", failed)
        self.assertEqual(failed["cross_role_isolation"]["details"][0]["item"], "role2:c1")
        self.assertNotIn("cross_role_isolation", self.failed("role2"))
        self.edit("audit.jsonl", lambda r: r["expression_input"].update(leak="HIDDEN_WORLD_SENTINEL_47"))
        self.assertIn("world_private", [d["item"] for d in self.failed()["cross_role_isolation"]["details"]])

    def test_detects_broken_state_chain_and_missing_versions(self):
        rows = read_lines(self.role_dir / "states.jsonl")
        rows[2]["prev_state_id"] = "role1@t000"
        del rows[1]["versions"]["update_rule_version"]
        write_lines(self.role_dir / "states.jsonl", rows)
        self.assertIn("trajectory_traceable", self.failed())

    def test_rejected_groups_and_soft_flags_are_reported_not_hidden(self):
        log = read_lines(self.role_dir / "mechanism_log.jsonl")
        log[0]["rejected"] = [{"group": "relation", "kind": "hard", "reason": "stale"}]
        log[0]["flags"] = [{"path": "dynamic.relation_state.partner.trust", "flag": "soft_violation"}]
        write_lines(self.role_dir / "mechanism_log.jsonl", log)
        report = audit_run(self.run_dir)["roles"]["role1"]
        self.assertEqual(len(report["rejected_groups"]), 1)
        self.assertEqual(len(report["soft_violations"]), 1)
        self.assertGreater(report["error_rate"]["rejected_groups"], 0)

    def test_write_report_can_be_rerun(self):
        self.assertTrue(write_report(self.run_dir)["passed"])


if __name__ == "__main__":
    unittest.main()
