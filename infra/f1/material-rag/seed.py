"""Task-local seed entrypoint for the isolated material-RAG database."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from infra.f1 import local_seed  # noqa: E402
from infra.f1.migrate_f1 import _bootstrap_dsn  # noqa: E402


def seed() -> None:
    """Seed synthetic identities after the task-local migration head."""
    with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
        head = connection.execute(
            "SELECT string_agg(version_num, ',' ORDER BY version_num) "
            "FROM f1.alembic_version"
        ).fetchone()
        if head is None or head[0] != "f1_0015":
            raise RuntimeError("LOCAL_MATERIAL_RAG_SEED_MIGRATION_REQUIRED")
        local_seed._ensure_enterprise(
            connection,
            local_seed.ENTERPRISE_A,
            "Local Enterprise A",
            "LOCAL-A",
        )
        local_seed._ensure_enterprise(
            connection,
            local_seed.ENTERPRISE_B,
            "Local Enterprise B",
            "LOCAL-B",
        )
        for binding in local_seed.BINDINGS:
            local_seed._ensure_binding(connection, binding)
        local_seed._ensure_durability_canary(connection)
        connection.commit()


def main() -> int:
    seed()
    print("LOCAL_MATERIAL_RAG_SEED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
