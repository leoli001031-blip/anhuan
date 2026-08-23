"""Task-local migration entrypoint for analysis-report tables.

Default engineering remains frozen at f1_0014. Material-RAG dedicated
migrator remains f1_0016. This entrypoint alone requests f1_0018.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from infra.f1 import local_migrate, migrate_f1  # noqa: E402
from platform_foundation.f1.config import pg_database  # noqa: E402


P2_P7_FORCE_RLS_TABLES = (
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
)
MATERIAL_RAG_TABLES = (
    "material_rag_scope_binding",
    "material_rag_unit",
    "material_rag_job",
)
ANALYSIS_REPORT_TABLES = (
    "analysis_report_client_audience",
    "analysis_report",
    "analysis_report_version",
    "analysis_report_section",
    "analysis_report_citation",
    "analysis_report_generation_job",
    "analysis_report_audit_event",
    "analysis_report_health_snapshot",
)
EXPECTED_RLS_TABLES = (
    P2_P7_FORCE_RLS_TABLES + MATERIAL_RAG_TABLES + ANALYSIS_REPORT_TABLES
)


def _verify_catalog(connection: object) -> None:
    heads = connection.execute(
        text(
            "SELECT "
            "(SELECT count(*) FROM f0d.alembic_version),"
            "(SELECT min(version_num) FROM f0d.alembic_version),"
            "(SELECT count(*) FROM f1.alembic_version),"
            "(SELECT min(version_num) FROM f1.alembic_version)"
        )
    ).one()
    if tuple(heads) != (1, "f0d_0006", 1, "f1_0018"):
        raise RuntimeError("LOCAL_ANALYSIS_REPORT_MIGRATION_HEAD_MISMATCH")

    observed = connection.execute(
        text(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='f1' AND c.relname = ANY(:names) "
            "AND c.relkind IN ('r','p')"
        ),
        {"names": list(EXPECTED_RLS_TABLES)},
    ).all()
    rows = {
        str(name): (bool(enabled), bool(forced))
        for name, enabled, forced in observed
    }
    if set(rows) != set(EXPECTED_RLS_TABLES) or any(
        value != (True, True) for value in rows.values()
    ):
        raise RuntimeError("LOCAL_ANALYSIS_REPORT_RLS_CATALOG_MISMATCH")


def migrate() -> None:
    local_migrate._root_migration_url()
    migrate_f1._migration_dsn()
    bootstrap_url = make_url(migrate_f1._bootstrap_dsn()).set(
        drivername="postgresql+psycopg"
    )
    engine = create_engine(bootstrap_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                identity = connection.execute(
                    text("SELECT current_user, current_database()")
                ).one()
                if tuple(identity) != ("f0d_bootstrap", pg_database()):
                    raise RuntimeError(
                        "LOCAL_ANALYSIS_REPORT_DATABASE_IDENTITY_MISMATCH"
                    )
                connection.exec_driver_sql("SET LOCAL ROLE f0d_migration")
                try:
                    local_migrate._upgrade_f0(connection)
                finally:
                    connection.exec_driver_sql("RESET ROLE")
                migrate_f1.migrate_with_connection(
                    connection,
                    target=migrate_f1.F1_ANALYSIS_REPORT_MIGRATE_TARGET,
                )
                _verify_catalog(connection)
    finally:
        engine.dispose()


def main() -> int:
    migrate()
    print("LOCAL_ANALYSIS_REPORT_MIGRATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
