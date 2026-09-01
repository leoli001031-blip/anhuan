"""Offline contract locks for analysis-report workflow UAT and demo handoff."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AnalysisReportWorkflowContractTests(unittest.TestCase):
    def test_runner_stage_is_independent_of_browser_verify(self) -> None:
        runner = (ROOT / "src/web/scripts/engineering-browser-verify.mjs").read_text(
            encoding="utf-8"
        )
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        self.assertIn('"analysis-report-workflow"', runner)
        self.assertIn("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK", runner)
        self.assertIn("async function executeAnalysisReportWorkflow", runner)
        all_stage = runner.split("async function executeAll", 1)[1].split(
            "async function executeBusiness", 1
        )[0]
        self.assertNotIn("executeAnalysisReportWorkflow", all_stage)
        self.assertIn("ANALYSIS_REPORT_WORKFLOW_IDENTITIES", runner)
        identities = runner.split("const ANALYSIS_REPORT_IDENTITIES", 1)[1].split(
            "const ANALYSIS_REPORT_EMPLOYEE_IDENTITY", 1
        )[0]
        self.assertNotIn("employee", identities)
        self.assertIn("oidc_employee", runner)
        self.assertIn(
            'BROWSER_STAGES = frozenset(\n    {"all", "business", "faults", "pwa-update", "pwa-os"}\n)',
            localctl,
        )
        self.assertIn('subparsers.add_parser("analysis-report-workflow-uat-check")', localctl)
        self.assertIn('subparsers.add_parser("analysis-report-demo-start")', localctl)
        self.assertIn("Input.dispatchMouseEvent", runner)
        self.assertIn(r'.replace(/\\s+/g, "")', runner)

    def test_fixture_adds_synthetic_materials_without_replica(self) -> None:
        source = (
            ROOT / "infra/f1/analysis-reports/local_browser_fixture.py"
        ).read_text(encoding="utf-8")
        ast.parse(source)
        self.assertNotIn("session_replication_role", source)
        self.assertNotIn("DELETE FROM f1.enterprise_user", source)
        self.assertIn("_ensure_synthetic_materials", source)
        apply_body = source.split("def apply()", 1)[1].split("def main()", 1)[0]
        self.assertLess(
            apply_body.index("_ensure_crm_and_binding"),
            apply_body.index("_ensure_synthetic_materials"),
        )
        self.assertNotIn("EMPLOYEE_SUB", apply_body)
        self.assertIn("_ensure_synthetic_service_case", source)
        self.assertIn('"service-case:provider-a:audience-b"', source)
        self.assertNotIn("DISABLE TRIGGER aeco_service_case_client_identity_guard", source)
        service_case = source.split("def _ensure_synthetic_service_case", 1)[1].split(
            "def _stable_material_id", 1
        )[0]
        self.assertIn("CRM_ACCOUNT_ID", service_case)
        self.assertIn("ON CONFLICT (id) DO NOTHING", service_case)
        self.assertNotIn("DO UPDATE", service_case)
        self.assertIn("LOCAL_REPORT_FIXTURE_SERVICE_CASE_MISMATCH", service_case)
        self.assertIn("unit_aad_for_identity", source)
        self.assertIn("encrypt_text(body, aad)", source)
        self.assertIn("decrypt_text(bytes(stored[0]), aad, str(stored[2]))", source)
        self.assertNotIn('b"ARFIX1"', source)
        self.assertLess(
            apply_body.index("_ensure_crm_and_binding"),
            apply_body.index("_ensure_synthetic_service_case"),
        )

    def test_demo_status_keys_are_strict(self) -> None:
        source = (ROOT / "infra/f1/analysis_report_demo.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("evidence_local", source)
        self.assertIn("NOT_PRODUCTION", source)
        self.assertIn("anhuan-analysis-report-demo-control-v1", source)
        self.assertIn("analysis-report-demo", source)
        self.assertNotIn("session_replication_role", source)

    def test_workflow_summary_key_set_is_closed(self) -> None:
        source = (ROOT / "infra/f1/analysis_report_uat.py").read_text(encoding="utf-8")
        runner = (ROOT / "src/web/scripts/engineering-browser-verify.mjs").read_text(
            encoding="utf-8"
        )
        ast.parse(source)
        self.assertIn(
            'WORKFLOW_OK_TAG = "LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK"',
            source,
        )
        self.assertIn("dedicated_c", source)
        self.assertIn("run_workflow_check", source)
        self.assertIn("analysis-report-workflow", source)
        self.assertIn("health_null_after_publish", source)
        self.assertIn("health_null_after_withdraw", source)
        self.assertIn("health_snapshot_count", source)
        self.assertIn("client_services", source)
        self.assertIn("client_qa", source)
        self.assertIn("qa_citation_count", source)
        self.assertNotIn("health_http_score", source)
        self.assertNotIn("health_test_provenance", source)
        self.assertIn('payload?.snapshot !== null', runner)
        self.assertIn('health_snapshot_count: 0', runner)
        self.assertIn('"暂不评分"', runner)
        self.assertNotIn("snapshot?.score !== 60", runner)
        self.assertIn("CLIENT_HEALTH_PAYLOAD_INVALID", runner)
        self.assertIn("CLIENT_HEALTH_WITHDRAW_PAYLOAD_INVALID", runner)
        self.assertIn("CLIENT_SERVICES_DTO_INVALID", runner)
        self.assertIn(
            'serviceItem.id !== "90403144-21d3-518b-bb41-1f52cca4e268"',
            runner,
        )
        self.assertIn("CLIENT_QA_PAYLOAD_INVALID", runner)


if __name__ == "__main__":
    unittest.main()
