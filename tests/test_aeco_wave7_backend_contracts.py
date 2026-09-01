"""Targeted offline contracts for A-Eco review, client audience, and HTML artifacts."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_MIGRATION = (
    ROOT
    / "infra/f1/alembic/versions/f1_0019_analysis_report_review_evidence.py"
)
AUDIENCE_MIGRATION = (
    ROOT / "infra/f1/alembic/versions/f1_0020_aeco_client_operations.py"
)
SERVICE_CASE_ROUTER = (
    ROOT / "src/platform_foundation/f1/api/routers/service_cases.py"
)
MATERIAL_SERVICE = (
    ROOT / "src/platform_foundation/f1/features/material_rag/service.py"
)
ANALYSIS_REPORTS = (
    ROOT / "src/platform_foundation/f1/features/analysis_reports"
)


def _load_pure_analysis_report_modules() -> tuple[types.ModuleType, types.ModuleType]:
    """Load the two dependency-free modules without importing package __init__."""
    package_name = "_aeco_wave7_analysis_reports"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ANALYSIS_REPORTS)]  # type: ignore[attr-defined]
    sys.modules[package_name] = package

    loaded: list[types.ModuleType] = []
    for module_name in ("contracts", "artifact"):
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified,
            ANALYSIS_REPORTS / f"{module_name}.py",
        )
        if spec is None or spec.loader is None:
            raise AssertionError(f"cannot load {module_name}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded[0], loaded[1]


_CONTRACTS, _ARTIFACT = _load_pure_analysis_report_modules()
SECTION_KEYS = _CONTRACTS.SECTION_KEYS
SECTION_TITLES = _CONTRACTS.SECTION_TITLES
ReportArtifactInvalid = _ARTIFACT.ReportArtifactInvalid
render_html_artifact = _ARTIFACT.render_html_artifact


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))
    return source


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def _class_fields(path: Path, name: str) -> tuple[str, ...]:
    tree = ast.parse(_source(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return tuple(
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
            )
    raise AssertionError(f"missing class {name}")


def _async_function(path: Path, name: str) -> str:
    tree = ast.parse(_source(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"missing async function {name}")


class ReviewEvidenceMigrationContracts(unittest.TestCase):
    def test_three_review_actions_and_evidence_shapes_are_database_checked(self) -> None:
        source = _source(REVIEW_MIGRATION)
        action_clause = re.search(r"action IN \(([^)]*)\)", source)
        self.assertIsNotNone(action_clause)
        self.assertEqual(
            set(re.findall(r"'([a-z_]+)'", action_clause.group(1))),  # type: ignore[union-attr]
            {"submit", "return", "approve"},
        )
        shape = _between(
            source,
            "CONSTRAINT analysis_report_review_event_shape_ck CHECK (",
            "CONSTRAINT analysis_report_review_event_version_fk",
        )
        self.assertIn(
            "action = 'submit' AND checklist = '{{}}'::jsonb AND comment IS NULL",
            shape,
        )
        self.assertIn(
            "action = 'return' AND checklist = '{{}}'::jsonb AND comment IS NOT NULL",
            shape,
        )
        self.assertIn("action = 'approve' AND checklist = jsonb_build_object(", shape)
        checklist_keys = set(
            re.findall(
                r"'(citation_traceable|risks_complete|usage_boundary)'\s*,\s*true",
                shape,
            )
        )
        self.assertEqual(
            checklist_keys,
            {"citation_traceable", "risks_complete", "usage_boundary"},
        )
        self.assertIn("char_length(comment) BETWEEN 1 AND 2000", source)
        self.assertIn("jsonb_typeof(checklist) = 'object'", source)

    def test_review_events_are_append_only_and_provider_rls_scoped(self) -> None:
        source = _source(REVIEW_MIGRATION)
        for statement in (
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "FOR SELECT TO f1_api USING",
            "FOR INSERT TO f1_api WITH CHECK",
            "GRANT SELECT, INSERT ON f1.{_TABLE} TO f1_api",
            "REVOKE ALL ON f1.{_TABLE} FROM PUBLIC",
            "REVOKE ALL ON f1.{_TABLE} FROM f1_worker",
            "BEFORE UPDATE OR DELETE ON f1.{_TABLE}",
            "ANALYSIS_REPORT_REVIEW_EVENT_IMMUTABLE",
        ):
            self.assertIn(statement, source)
        self.assertIn("f1.current_enterprise_id()", source)
        self.assertIn("f1.session_authorized", source)
        self.assertIn("actor.role IN ('super_admin','enterprise_admin')", source)
        self.assertNotIn(
            "GRANT SELECT, INSERT, UPDATE",
            source,
        )


class ClientAudienceMigrationContracts(unittest.TestCase):
    def test_service_case_client_binding_is_composite_and_immutable(self) -> None:
        source = _source(AUDIENCE_MIGRATION)
        literal_source = "\n".join(
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        router = _source(SERVICE_CASE_ROUTER)
        for statement in (
            "ALTER TABLE f1.service_case ADD COLUMN client_account_id uuid",
            "FOREIGN KEY (enterprise_id, client_account_id)",
            "REFERENCES f1.crm_account(enterprise_id, id)",
            "WHERE client_account_id IS NOT NULL",
            "IF NEW.client_account_id IS DISTINCT FROM OLD.client_account_id",
            "AECO_SERVICE_CASE_CLIENT_IMMUTABLE",
            "BEFORE UPDATE ON f1.service_case",
        ):
            self.assertIn(statement, literal_source)
        self.assertIn("client_account_id: uuid.UUID | None = None", router)
        self.assertIn("await _ensure_client_account(session, tenant, body.client_account_id)", router)
        self.assertNotIn(
            "client_account_id",
            _between(router, "class ServiceCaseUpdate", "class ServiceAssignmentCreate"),
        )

    def test_client_service_function_returns_only_safe_summary(self) -> None:
        source = _source(AUDIENCE_MIGRATION)
        function = _between(
            source,
            "CREATE FUNCTION f1.aeco_client_service_cases()",
            "REVOKE ALL ON FUNCTION f1.aeco_client_service_cases()",
        )
        returned = _between(function, "RETURNS TABLE (", ")\n        LANGUAGE")
        returned_fields = tuple(
            re.findall(
                r"^\s*([a-z_]+)\s+(?:uuid|text|boolean|timestamptz),?\s*$",
                returned,
                flags=re.MULTILINE,
            )
        )
        self.assertEqual(
            returned_fields,
            (
                "id",
                "title",
                "service_type",
                "status",
                "planned_start_at",
                "planned_end_at",
                "assigned",
                "updated_at",
            ),
        )
        for internal_field in (
            "description",
            "enterprise_id",
            "client_account_id",
            "created_by_user_id",
            "assignments",
            "findings",
            "timeline",
        ):
            self.assertNotIn(internal_field, returned_fields)
        self.assertIn("LANGUAGE sql STABLE SECURITY DEFINER", function)
        self.assertIn("SET search_path = pg_catalog", function)
        self.assertIn("binding.status = 'active'", function)
        self.assertIn(
            "binding.audience_enterprise_id = f1.current_enterprise_id()",
            function,
        )
        self.assertIn("f1.session_authorized(f1.current_enterprise_id())", function)
        self.assertIn("assignment.status IN ('pending','accepted')", function)

    def test_portal_route_cannot_return_provider_detail_contract(self) -> None:
        safe_fields = _class_fields(SERVICE_CASE_ROUTER, "ClientServiceCaseOut")
        self.assertEqual(
            safe_fields,
            (
                "id",
                "title",
                "service_type",
                "status",
                "planned_start_at",
                "planned_end_at",
                "assigned",
                "updated_at",
            ),
        )
        function = _async_function(SERVICE_CASE_ROUTER, "list_client_service_cases")
        self.assertIn("ClientServiceCaseListOut", function)
        self.assertIn("FROM f1.aeco_client_service_cases()", function)
        self.assertIn("allowed_actions=[]", function)
        self.assertIn("SERVICE_CASES_NOT_FOUND", function)
        for forbidden in (
            "ServiceCaseDetailOut",
            "_detail_out",
            "_CASE_COLUMNS",
            "description",
            "assignments",
            "findings",
            "timeline",
        ):
            self.assertNotIn(forbidden, function)

    def test_audience_material_functions_are_bound_and_released_only(self) -> None:
        source = _source(AUDIENCE_MIGRATION)
        context = _between(
            source,
            "CREATE FUNCTION f1.aeco_client_material_context()",
            "CREATE FUNCTION f1.aeco_client_material_bindings()",
        )
        bindings = _between(
            source,
            "CREATE FUNCTION f1.aeco_client_material_bindings()",
            "CREATE FUNCTION f1.aeco_client_material_units(p_unit_ids uuid[])",
        )
        units = _between(
            source,
            "CREATE FUNCTION f1.aeco_client_material_units(p_unit_ids uuid[])",
            "for signature in (",
        )
        for statement in (
            "binding.audience_enterprise_id = f1.current_enterprise_id()",
            "binding.status = 'active'",
            "provider_scope.scope_kind = 'service_provider'",
            "provider_scope.client_account_id IS NULL",
            "client_scope.scope_kind = 'client'",
            "client_scope.client_account_id = binding.client_account_id",
            "f1.session_authorized(f1.current_enterprise_id())",
            "LIMIT 2",
        ):
            self.assertIn(statement, context)
        self.assertIn("scope_binding.backend = 'ragflow'", bindings)
        self.assertIn("scope_binding.status = 'ready'", bindings)
        for statement in (
            "cardinality(p_unit_ids) < 1",
            "cardinality(p_unit_ids) > 20",
            "unit.id = ANY(p_unit_ids)",
            "task.pipeline_kind = 'controlled_ingestion'",
            "task.status = 'done'",
            "task.processing_stage = 'ready'",
            "task.object_state = 'ready'",
            "task.scan_verdict = 'clean'",
            "task.preview_status = 'ready'",
            "task.quarantine_status = 'released'",
            "task.released_at IS NOT NULL",
            "task.content_sha256 = unit.source_sha256",
        ):
            self.assertIn(statement, units)
        for signature in (
            "f1.aeco_client_material_context()",
            "f1.aeco_client_material_bindings()",
            "f1.aeco_client_material_units(uuid[])",
        ):
            self.assertIn(f'"{signature}"', source)
        self.assertIn(
            'op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")',
            source,
        )
        self.assertIn(
            'op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")',
            source,
        )

        repository = _between(
            _source(MATERIAL_SERVICE),
            "class AudiencePostgresMaterialRagRepository",
            "def _production_service()",
        )
        for function_name in (
            "aeco_client_material_context()",
            "aeco_client_material_bindings()",
            "aeco_client_material_units(",
        ):
            self.assertIn(function_name, repository)
        self.assertNotIn("FROM f1.material_rag_unit", repository)
        self.assertNotIn("FROM f1.material_rag_scope_binding", repository)


def _complete_artifact_payload() -> dict[str, object]:
    sections = [
        {
            "key": key,
            "title": SECTION_TITLES[key],
            "body": f"{key} <script>alert('section')</script> & evidence",
        }
        for key in SECTION_KEYS
    ]
    return {
        "version_number": 3,
        "sections": sections,
        "citations": [
            {
                "document_name": "permit <img src=x onerror=alert(1)>",
                "document_version_id": 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\" data-x=\"1',
                "version_number": 2,
                "page_number": 7,
                "excerpt": "limit < 10 & operator > 2",
            }
        ],
    }


class HtmlArtifactContracts(unittest.TestCase):
    def test_complete_seven_sections_and_citation_are_escaped(self) -> None:
        artifact = render_html_artifact(_complete_artifact_payload())
        rendered = artifact.body.decode("utf-8")
        self.assertEqual(rendered.count('<div class="report-body">'), 7)
        for key in SECTION_KEYS:
            self.assertEqual(rendered.count(f'<section id="{key}">'), 1)
        self.assertIn('<section id="artifact-citations">', rendered)
        self.assertIn("[1] permit", rendered)
        self.assertIn("第 2 版 · 第 7 页", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img src=x", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("limit &lt; 10 &amp; operator &gt; 2", rendered)
        self.assertIn("&quot; data-x=&quot;1", rendered)
        self.assertEqual(artifact.filename, "a-eco-analysis-report-v3.html")
        self.assertEqual(artifact.sha256, hashlib.sha256(artifact.body).hexdigest())

    def test_incomplete_or_malformed_artifact_fails_closed(self) -> None:
        payloads_and_reasons: list[tuple[dict[str, object], str]] = []

        missing_section = _complete_artifact_payload()
        missing_section["sections"] = missing_section["sections"][:-1]  # type: ignore[index]
        payloads_and_reasons.append(
            (missing_section, "REPORT_ARTIFACT_SECTIONS_INCOMPLETE")
        )

        duplicate_section = _complete_artifact_payload()
        sections = list(duplicate_section["sections"])  # type: ignore[arg-type]
        sections[-1] = dict(sections[0])
        duplicate_section["sections"] = sections
        payloads_and_reasons.append(
            (duplicate_section, "REPORT_ARTIFACT_SECTIONS_INCOMPLETE")
        )

        no_citations = _complete_artifact_payload()
        no_citations["citations"] = []
        payloads_and_reasons.append(
            (no_citations, "REPORT_ARTIFACT_CITATIONS_REQUIRED")
        )

        malformed_citation = _complete_artifact_payload()
        malformed_citation["citations"] = [{"document_name": "missing fields"}]
        payloads_and_reasons.append(
            (malformed_citation, "REPORT_ARTIFACT_CONTENT_INVALID")
        )

        for payload, reason in payloads_and_reasons:
            with self.subTest(reason=reason), self.assertRaises(
                ReportArtifactInvalid
            ) as raised:
                render_html_artifact(payload)
            self.assertEqual(str(raised.exception), reason)


if __name__ == "__main__":
    unittest.main()
