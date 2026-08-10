"""Atomic F0D + F1 migration entrypoint for the isolated local stack."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from alembic.config import Config  # noqa: E402
from alembic.environment import EnvironmentContext  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

from infra.f1 import migrate_f1  # noqa: E402
from platform_foundation.f1.config import pg_database, pg_host, pg_port  # noqa: E402


P2_P7_TABLES = (
    "service_case", "service_assignment", "site_visit", "finding",
    "corrective_action", "finding_review", "business_timeline",
    "in_app_notification", "document_record", "document_version",
    "document_preview_unit", "crm_account", "crm_contact", "crm_follow_up",
    "business_report", "business_report_version", "business_report_artifact",
    "policy_source", "policy_version", "policy_review_event",
    "policy_impact_candidate", "policy_impact_task", "quality_suite",
    "quality_scenario", "quality_run", "quality_result",
    "quality_disagreement", "rehearsal_plan", "rehearsal_check",
    "rehearsal_run", "rehearsal_check_result",
)


def _root_migration_url() -> URL:
    try:
        value = make_url(migrate_f1._read_secret("f0d_migration_dsn"))
    except (TypeError, ValueError):
        raise RuntimeError("F0D_MIGRATION_DSN_INVALID") from None
    if (
        value.drivername != "postgresql"
        or value.username != "f0d_migration"
        or not value.password
        or value.host != pg_host()
        or value.port != int(pg_port())
        or value.database != pg_database()
        or bool(value.query)
    ):
        raise RuntimeError("F0D_MIGRATION_DSN_IDENTITY_MISMATCH")
    return value.set(drivername="postgresql+psycopg")


def _upgrade_f0(connection: object) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    def upgrade_revisions(revision: object, _context: object) -> object:
        return script._upgrade_revs("f0d_0006", revision)

    with EnvironmentContext(
        config,
        script,
        fn=upgrade_revisions,
        destination_rev="f0d_0006",
    ) as environment:
        environment.configure(
            connection=connection,
            target_metadata=None,
            transactional_ddl=True,
            include_schemas=True,
            version_table_schema="f0d",
        )
        with environment.begin_transaction():
            environment.run_migrations()


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
    if tuple(heads) != (1, "f0d_0006", 1, "f1_0010"):
        raise RuntimeError("LOCAL_MIGRATION_HEAD_MISMATCH")

    observed = connection.execute(
        text(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='f1' AND c.relname = ANY(:names) "
            "AND c.relkind IN ('r','p')"
        ),
        {"names": list(P2_P7_TABLES)},
    ).all()
    rows = {str(name): (bool(enabled), bool(forced)) for name, enabled, forced in observed}
    if set(rows) != set(P2_P7_TABLES) or any(value != (True, True) for value in rows.values()):
        raise RuntimeError("LOCAL_RLS_CATALOG_MISMATCH")

    role_rows = connection.execute(
        text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls,rolconnlimit "
            "FROM pg_roles WHERE rolname IN ('f1_api','f1_worker') ORDER BY rolname"
        )
    ).all()
    expected_roles = [
        ("f1_api", True, False, False, False, False, False, False, 20),
        ("f1_worker", True, False, False, False, False, False, False, 10),
    ]
    if [tuple(row) for row in role_rows] != expected_roles:
        raise RuntimeError("LOCAL_RUNTIME_ROLE_MISMATCH")
    membership = connection.execute(
        text(
            "SELECT count(*) FROM pg_auth_members AS m "
            "JOIN pg_roles AS granted ON granted.oid=m.roleid "
            "JOIN pg_roles AS member ON member.oid=m.member "
            "WHERE granted.rolname IN ('f1_api','f1_worker') "
            "OR member.rolname IN ('f1_api','f1_worker')"
        )
    ).scalar_one()
    if int(membership) != 0:
        raise RuntimeError("LOCAL_RUNTIME_ROLE_MEMBERSHIP_MISMATCH")


def migrate(*, after_f1_upgrade: object | None = None) -> None:
    """Upgrade both schema lines in one PostgreSQL transaction."""
    _root_migration_url()
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
                    raise RuntimeError("LOCAL_DATABASE_IDENTITY_MISMATCH")
                connection.exec_driver_sql("SET LOCAL ROLE f0d_migration")
                try:
                    _upgrade_f0(connection)
                finally:
                    connection.exec_driver_sql("RESET ROLE")
                migrate_f1.migrate_with_connection(
                    connection,
                    after_upgrade=after_f1_upgrade,
                )
                _verify_catalog(connection)
    finally:
        engine.dispose()


def main() -> int:
    migrate()
    print("LOCAL_MIGRATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
