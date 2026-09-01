"""Pure offline contracts for the explicitly local extractive QA path."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import types
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATERIAL_RAG = ROOT / "src/platform_foundation/f1/features/material_rag"


def _load_pure_material_rag_module(name: str) -> types.ModuleType:
    package_name = "_material_rag_local_test"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        package.__path__ = [str(MATERIAL_RAG)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package
    qualified = f"{package_name}.{name}"
    spec = importlib.util.spec_from_file_location(
        qualified, MATERIAL_RAG / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


_load_pure_material_rag_module("contracts")
_LOCAL_EXTRACTIVE = _load_pure_material_rag_module("local_extractive")
LocalExtractiveIntegrityError = _LOCAL_EXTRACTIVE.LocalExtractiveIntegrityError
rank_local_evidence = _LOCAL_EXTRACTIVE.rank_local_evidence


SERVICE = ROOT / "src/platform_foundation/f1/features/material_rag/service.py"
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0020_aeco_client_operations.py"
MIGRATOR = ROOT / "infra/f1/migrate_f1.py"
FIXTURE = ROOT / "infra/f1/analysis-reports/local_browser_fixture.py"
DEMO = ROOT / "infra/f1/docker-compose.analysis-report-demo.yml"
UAT = ROOT / "infra/f1/docker-compose.analysis-report-uat.yml"
BASE_COMPOSE = ROOT / "infra/f1/docker-compose.local.yml"
UAT_DRIVER = ROOT / "infra/f1/analysis_report_uat.py"
DEMO_DRIVER = ROOT / "infra/f1/analysis_report_demo.py"


def _source(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        ast.parse(source, filename=str(path))
    return source


@dataclass(frozen=True)
class _Unit:
    canonical_unit_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    source_sha256: str
    page_number: int
    body_sha256: str
    body: str
    scope_kind: str


def _unit(label: str, body: str, *, scope_kind: str) -> _Unit:
    return _Unit(
        canonical_unit_id=uuid.uuid5(uuid.NAMESPACE_URL, f"unit:{label}"),
        document_record_id=uuid.uuid5(uuid.NAMESPACE_URL, f"record:{label}"),
        document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, f"version:{label}"),
        document_name=f"{label}制度",
        version_number=1,
        source_sha256=hashlib.sha256(f"source:{label}".encode()).hexdigest(),
        page_number=1,
        body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        body=body,
        scope_kind=scope_kind,
    )


class LocalExtractiveRankingTests(unittest.TestCase):
    def test_arbitrary_safe_question_ranks_real_canonical_text_with_citation(self) -> None:
        provider = _unit(
            "服务商",
            "服务商共享制度要求每月检查污染治理设施。",
            scope_kind="service_provider",
        )
        client = _unit(
            "企业安环",
            "企业废气治理采用活性炭吸附装置，运行台账每月归档。",
            scope_kind="client",
        )

        evidence = rank_local_evidence(
            "我们的废气治理采用什么方案？", (provider, client), limit=2
        )

        self.assertTrue(evidence)
        self.assertEqual(evidence[0].canonical_unit_id, client.canonical_unit_id)
        self.assertIn("活性炭吸附装置", evidence[0].snippet)
        self.assertEqual(evidence[0].body_sha256, client.body_sha256)

    def test_no_overlap_refuses_and_body_hash_mismatch_fails_closed(self) -> None:
        body = "企业废气治理采用活性炭吸附装置。"
        unit = _unit("安环", body, scope_kind="client")
        self.assertEqual(rank_local_evidence("食堂菜单", (unit,), limit=1), ())
        tampered = _Unit(**{**unit.__dict__, "body_sha256": "0" * 64})
        with self.assertRaises(LocalExtractiveIntegrityError):
            rank_local_evidence("废气治理", (tampered,), limit=1)


class LocalExtractiveWiringTests(unittest.TestCase):
    def test_dual_flag_local_branch_is_explicit_and_remote_remains_default(self) -> None:
        source = _source(SERVICE)
        self.assertIn('LOCAL_EXTRACTIVE_FLAG = "F1_MATERIAL_QA_LOCAL_EXTRACTIVE"', source)
        self.assertIn('LOCAL_ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"', source)
        self.assertIn("and os.environ.get(LOCAL_ENGINEERING_FLAG) == \"1\"", source)
        local_branch = source.split("async def _retrieve_local_authorized", 1)[1].split(
            "async def verify_remote_candidates", 1
        )[0]
        self.assertIn("load_local_released_units", local_branch)
        self.assertIn("rank_local_evidence", local_branch)
        self.assertNotIn("retrieve_candidates", local_branch)
        self.assertLess(
            source.index("if self._local_extractive"),
            source.index("load_ready_bindings", source.index("if self._local_extractive")),
        )
        self.assertNotIn("活性炭吸附装置", source)

    def test_db_bridge_is_single_audience_bounded_released_and_fail_closed(self) -> None:
        source = _source(MIGRATION)
        context = source.split(
            "CREATE FUNCTION f1.aeco_client_material_context()", 1
        )[1].split("CREATE FUNCTION f1.aeco_client_material_bindings()", 1)[0]
        self.assertIn("WITH candidates AS MATERIALIZED", context)
        # Two rows are sufficient to distinguish the one valid audience from
        # an ambiguous multi-provider audience while keeping the lookup bounded.
        self.assertIn("LIMIT 2", context)
        self.assertIn("(SELECT count(*) FROM candidates) = 1", context)
        local_units = source.split(
            "CREATE FUNCTION f1.aeco_client_material_local_units(p_limit integer)", 1
        )[1].split("for signature in", 1)[0]
        for token in (
            "p_limit > 256",
            "record.status = 'active'",
            "version.version_no = record.latest_version_no",
            "task.scan_verdict = 'clean'",
            "task.quarantine_status = 'released'",
            "task.rejected_at IS NULL",
            "LIMIT p_limit",
        ):
            self.assertIn(token, local_units)
        self.assertIn("SECURITY DEFINER", local_units)
        self.assertIn("SET search_path = pg_catalog", local_units)
        self.assertGreaterEqual(
            source.count("f1.aeco_client_material_local_units(integer)"), 2
        )
        self.assertIn(
            '"f1.aeco_client_material_local_units(integer)": "f1_aeco_read_definer"',
            _source(MIGRATOR),
        )
        self.assertIn('"f1_aeco_read_definer"', _source(MIGRATOR))
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION f1.current_enterprise_id()",
            _source(MIGRATOR),
        )
        security = source.split("def _aeco_definer_security()", 1)[1].split(
            "def _drop_aeco_definer_security()", 1
        )[0]
        self.assertIn("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA f1", security)
        self.assertIn("GRANT SELECT ON f1.service_case", security)
        self.assertIn("session_user = 'f1_api'", security)
        self.assertIn("f1.session_authorized(f1.current_enterprise_id())", security)
        self.assertNotIn("BYPASSRLS", security)

    def test_audience_rows_are_rechecked_and_decrypted_with_provider_aad(self) -> None:
        source = _source(SERVICE)
        audience_local = source.split(
            "class AudiencePostgresMaterialRagRepository", 1
        )[1].split("class RagflowMaterialRagTransport", 1)[0]
        self.assertIn("aeco_client_material_local_units", audience_local)
        self.assertIn("expected_scopes != set(context._scope_ids)", audience_local)
        self.assertIn('row["provider_enterprise_id"] != provider_id', audience_local)
        self.assertIn("aad_enterprise_id=provider_id", audience_local)
        self.assertIn("hmac.compare_digest(actual, body_sha256)", source)

    def test_remote_candidate_verification_rejects_stale_or_rejected_units(self) -> None:
        service = _source(SERVICE)
        provider_remote = service.split(
            "class PostgresMaterialRagRepository", 1
        )[1].split("async def load_local_released_units", 1)[0]
        migration = _source(MIGRATION)
        audience_remote = migration.split(
            "CREATE FUNCTION f1.aeco_client_material_units(p_unit_ids uuid[])", 1
        )[1].split(
            "CREATE FUNCTION f1.aeco_client_material_local_units(p_limit integer)",
            1,
        )[0]
        for source in (provider_remote, audience_remote):
            self.assertIn("record.status", source)
            self.assertIn("version.version_no", source)
            self.assertIn("record.latest_version_no", source)
            self.assertIn("task.rejected_at IS NULL", source)

    def test_demo_enables_flags_and_material_key_without_enabling_base_runtime(self) -> None:
        for path in (DEMO, UAT):
            overlay = _source(path)
            api = overlay.split("  api:", 1)[1].split("  worker:", 1)[0]
            self.assertIn('F1_MATERIAL_QA_LOCAL_EXTRACTIVE: "1"', api)
        base = _source(BASE_COMPOSE)
        self.assertNotIn("F1_MATERIAL_QA_LOCAL_EXTRACTIVE", base)
        self.assertIn("f1_material_rag_key;", base)
        self.assertGreaterEqual(base.count("F1_MATERIAL_RAG_KEY_FILE"), 2)
        uat_driver = _source(UAT_DRIVER)
        self.assertIn('"f1_material_rag_key"', uat_driver)
        self.assertIn('"F1_MATERIAL_QA_LOCAL_EXTRACTIVE": "1"', uat_driver)
        self.assertIn("local_qa", _source(DEMO_DRIVER))

    def test_fixture_contains_answerable_source_text_not_a_production_answer(self) -> None:
        fixture = _source(FIXTURE)
        self.assertIn("企业废气治理采用活性炭吸附装置", fixture)
        self.assertIn("body = _MATERIAL_BODIES[label]", fixture)
        self.assertIn('os.environ.get("F1_MATERIAL_QA_LOCAL_EXTRACTIVE")', fixture)


if __name__ == "__main__":
    unittest.main()
