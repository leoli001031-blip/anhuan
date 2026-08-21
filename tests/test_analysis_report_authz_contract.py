"""Static analysis-report authorization contracts. No Docker, no PostgreSQL."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0017_analysis_reports.py"
MIGRATOR = ROOT / "infra/f1/analysis-reports/migrate.py"
CLOSEOUT = ROOT / "tests/test_engineering_closeout_migration.py"
MODELS = ROOT / "src/platform_foundation/f1/models.py"
SERVICE = ROOT / "src/platform_foundation/f1/features/analysis_reports/service.py"
REPOSITORY = ROOT / "src/platform_foundation/f1/features/analysis_reports/repository.py"

P2_P7_31 = {
    "service_case",
    "service_assignment",
    "site_visit",
    "finding",
    "corrective_action",
    "finding_review",
    "business_timeline",
    "in_app_notification",
    "document_record",
    "document_version",
    "document_preview_unit",
    "crm_account",
    "crm_contact",
    "crm_follow_up",
    "business_report",
    "business_report_version",
    "business_report_artifact",
    "policy_source",
    "policy_version",
    "policy_review_event",
    "policy_impact_candidate",
    "policy_impact_task",
    "quality_suite",
    "quality_scenario",
    "quality_run",
    "quality_result",
    "quality_disagreement",
    "rehearsal_plan",
    "rehearsal_check",
    "rehearsal_run",
    "rehearsal_check_result",
}
MATERIAL_RAG_3 = {
    "material_rag_scope_binding",
    "material_rag_unit",
    "material_rag_job",
}
ANALYSIS_REPORT_7 = {
    "analysis_report_client_audience",
    "analysis_report",
    "analysis_report_version",
    "analysis_report_section",
    "analysis_report_citation",
    "analysis_report_generation_job",
    "analysis_report_audit_event",
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(_source(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {path}:{name}")


def _expanded(path: Path, function: str) -> str:
    node = _function(path, function)
    functions = {
        child.name: child
        for child in ast.parse(_source(path), filename=str(path)).body
        if isinstance(child, ast.FunctionDef)
    }
    rendered = ast.unparse(node)
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, helper in functions.items():
            if name in rendered and name not in seen:
                rendered += "\n" + ast.unparse(helper)
                seen.add(name)
                changed = True
    return rendered


def _assign(path: Path, function: str, name: str) -> str:
    node = _function(path, function)
    for child in ast.walk(node):
        if isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.unparse(child.value)
    raise AssertionError(f"missing assignment: {function}.{name}")


def _eval_tuple(node: ast.AST, env: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Tuple):
        values: list[str] = []
        for element in node.elts:
            values.extend(_eval_tuple(element, env))
        return tuple(values)
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise AssertionError(f"unresolved tuple name: {node.id}")
        return env[node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _eval_tuple(node.left, env) + _eval_tuple(node.right, env)
    raise AssertionError(f"unsupported tuple expression: {ast.dump(node)}")


def _tuple_names(path: Path, name: str) -> tuple[str, ...]:
    tree = ast.parse(_source(path), filename=str(path))
    env: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    env[target.id] = _eval_tuple(node.value, env)
                except AssertionError:
                    continue
    if name not in env:
        raise AssertionError(f"missing tuple: {name}")
    return env[name]


class AnalysisReportRlsAcyclicContracts(unittest.TestCase):
    def test_report_client_policy_does_not_query_analysis_report(self) -> None:
        source = _source(MIGRATION)
        self.assertNotIn("FROM f1.analysis_report AS parent", source)
        published = _assign(MIGRATION, "_rls_and_grants", "published_for_client")
        helper = ast.unparse(_function(MIGRATION, "_client_via_binding"))
        self.assertNotIn("f1.analysis_report AS parent", published)
        self.assertIn("client_visible", published)
        self.assertIn("analysis_report_client_audience", helper)
        self.assertIsNone(
            re.search(r"FROM f1\.analysis_report(?!_client_audience)\b", helper)
        )

    def test_report_and_version_client_policies_are_not_mutually_recursive(self) -> None:
        published = _assign(MIGRATION, "_rls_and_grants", "published_for_client")
        version_client = _assign(MIGRATION, "_rls_and_grants", "version_client")
        helper = ast.unparse(_function(MIGRATION, "_client_via_binding"))
        self.assertNotIn("analysis_report_version", published)
        self.assertNotIn("f1.analysis_report AS parent", version_client)
        self.assertIsNone(
            re.search(r"FROM f1\.analysis_report(?!_client_audience)\b", version_client)
        )
        self.assertIn("analysis_report_client_audience", helper)
        section_client = _assign(MIGRATION, "_rls_and_grants", "section_client")
        self.assertNotIn("JOIN f1.analysis_report AS parent", section_client)


class AnalysisReportMigratorCatalogContracts(unittest.TestCase):
    def test_migrator_verifies_audience_binding_force_rls_in_41_table_set(self) -> None:
        analysis_tables = _tuple_names(MIGRATOR, "ANALYSIS_REPORT_TABLES")
        material_tables = _tuple_names(MIGRATOR, "MATERIAL_RAG_TABLES")
        p2_tables = _tuple_names(MIGRATOR, "P2_P7_FORCE_RLS_TABLES")
        expected = _tuple_names(MIGRATOR, "EXPECTED_RLS_TABLES")
        self.assertEqual(set(p2_tables), P2_P7_31)
        self.assertEqual(set(material_tables), MATERIAL_RAG_3)
        self.assertEqual(set(analysis_tables), ANALYSIS_REPORT_7)
        self.assertIn("analysis_report_client_audience", analysis_tables)
        self.assertEqual(len(p2_tables) + len(material_tables) + len(analysis_tables), 41)
        self.assertEqual(set(expected), P2_P7_31 | MATERIAL_RAG_3 | ANALYSIS_REPORT_7)
        self.assertEqual(len(expected), 41)
        self.assertEqual(len(set(expected)), 41)
        source = _source(MIGRATOR)
        self.assertIn('"f1_0017"', source)
        self.assertIn("F1_ANALYSIS_REPORT_MIGRATE_TARGET", source)
        self.assertNotIn("local_migrate.P2_P7_TABLES", source)
        self.assertIn("relforcerowsecurity", source)


class AnalysisReportIntegrityContracts(unittest.TestCase):
    def test_current_version_job_and_audit_belong_to_the_same_report(self) -> None:
        source = _source(MIGRATION)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", source)
        self.assertIn("analysis_report_current_version_belongs_fk", source)
        self.assertIn("analysis_report_job_version_belongs_fk", source)
        self.assertIn("analysis_report_audit_version_belongs_fk", source)
        self.assertIn("UNIQUE (enterprise_id, report_id, id)", source)
        self.assertRegex(
            source,
            re.compile(
                r"FOREIGN KEY \(enterprise_id, id, current_version_id\).*?"
                r"REFERENCES f1\.analysis_report_version \(enterprise_id, report_id, id\)",
                re.DOTALL,
            ),
        )
        self.assertRegex(
            source,
            re.compile(
                r"FOREIGN KEY \(enterprise_id, report_id, version_id\).*?"
                r"REFERENCES f1\.analysis_report_version \(enterprise_id, report_id, id\)",
                re.DOTALL,
            ),
        )
        job_count = len(
            re.findall(
                r"FOREIGN KEY \(enterprise_id, report_id, version_id\)",
                source,
            )
        )
        self.assertGreaterEqual(job_count, 2)

    def test_fail_closed_grants_and_no_privilege_escape(self) -> None:
        source = _source(MIGRATION)
        self.assertNotIn("SECURITY DEFINER", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("USING (true)", source)
        self.assertNotIn("USING(true)", source)
        self.assertIn("REVOKE ALL ON {names} FROM PUBLIC", source)
        self.assertIn("REVOKE ALL ON {names} FROM f1_worker", source)
        self.assertEqual(re.findall(r"GRANT\s+.+\s+TO f1_worker", source), [])
        self.assertIn("analysis_report_client_audience", source)
        self.assertIn("client_visible", _source(MODELS))
        self.assertIn("client_visible", source)
        service = _source(SERVICE)
        self.assertIn("active_binding_for_provider", service)
        self.assertNotIn("tenant.roles", service)
        self.assertIn("withdraw", _source(REPOSITORY))


if __name__ == "__main__":
    unittest.main()
