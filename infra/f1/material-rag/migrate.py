"""Task-local migration entrypoint for the isolated material-RAG stack.

The default local engineering migrator deliberately remains frozen at its
``f1_0014`` contract.  This opt-in entrypoint advances only the dedicated
material-RAG database and verifies the three appended RLS tables in addition
to the existing catalog.
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


MATERIAL_RAG_TABLES = (
    "material_rag_scope_binding",
    "material_rag_unit",
    "material_rag_job",
)
EXPECTED_RLS_TABLES = local_migrate.P2_P7_TABLES + MATERIAL_RAG_TABLES


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
    if tuple(heads) != (1, "f0d_0006", 1, "f1_0015"):
        raise RuntimeError("LOCAL_MATERIAL_RAG_MIGRATION_HEAD_MISMATCH")

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
        raise RuntimeError("LOCAL_MATERIAL_RAG_RLS_CATALOG_MISMATCH")

    role_rows = connection.execute(
        text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls,rolconnlimit "
            "FROM pg_roles WHERE rolname IN ('f1_api','f1_worker') "
            "ORDER BY rolname"
        )
    ).all()
    expected_roles = [
        ("f1_api", True, False, False, False, False, False, False, 20),
        ("f1_worker", True, False, False, False, False, False, False, 10),
    ]
    if [tuple(row) for row in role_rows] != expected_roles:
        raise RuntimeError("LOCAL_MATERIAL_RAG_RUNTIME_ROLE_MISMATCH")
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
        raise RuntimeError("LOCAL_MATERIAL_RAG_RUNTIME_ROLE_MEMBERSHIP_MISMATCH")


def migrate() -> None:
    """Upgrade the dedicated database through ``f1_0015`` atomically."""
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
                        "LOCAL_MATERIAL_RAG_DATABASE_IDENTITY_MISMATCH"
                    )
                connection.exec_driver_sql("SET LOCAL ROLE f0d_migration")
                try:
                    local_migrate._upgrade_f0(connection)
                finally:
                    connection.exec_driver_sql("RESET ROLE")
                migrate_f1.migrate_with_connection(
                    connection,
                    target=migrate_f1.F1_MATERIAL_RAG_MIGRATE_TARGET,
                )
                _verify_catalog(connection)
    finally:
        engine.dispose()


def main() -> int:
    migrate()
    print("LOCAL_MATERIAL_RAG_MIGRATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
