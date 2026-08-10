"""Idempotent bootstrap-only synthetic identities for local engineering."""
from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from infra.f1.migrate_f1 import _bootstrap_dsn  # noqa: E402


ENTERPRISE_A = uuid.UUID("20000000-0000-4000-8000-00000000000a")
ENTERPRISE_B = uuid.UUID("20000000-0000-4000-8000-00000000000b")
SEED_NAMESPACE = uuid.UUID("c8a6ff26-9fb5-4f62-b12e-9945fd08eacd")


@dataclass(frozen=True, slots=True)
class Binding:
    name: str
    sub: str
    email: str
    enterprise_id: uuid.UUID
    role: str


BINDINGS = (
    Binding(
        "admin-a",
        "d561ffe2-3be8-40cc-a87e-598dd7d84758",
        "admin@fixture.invalid",
        ENTERPRISE_A,
        "super_admin",
    ),
    Binding(
        "admin-b",
        "d561ffe2-3be8-40cc-a87e-598dd7d84758",
        "admin@fixture.invalid",
        ENTERPRISE_B,
        "super_admin",
    ),
    Binding(
        "enterprise",
        "db906685-6906-4bc4-9d3a-9011975fd132",
        "tenant-a@fixture.invalid",
        ENTERPRISE_A,
        "enterprise_admin",
    ),
    Binding(
        "employee",
        "3247dddb-69bc-4ad1-841c-8fc338b603ce",
        "employee@fixture.invalid",
        ENTERPRISE_A,
        "plant_admin",
    ),
    Binding(
        "consultant",
        "7e9978c7-106f-4221-a6d7-79e8104a659b",
        "auditor@fixture.invalid",
        ENTERPRISE_A,
        "auditor",
    ),
    Binding(
        "partner",
        "f1f70ce5-465f-489c-a89d-974a63216ab4",
        "tester@fixture.invalid",
        ENTERPRISE_A,
        "partner",
    ),
    Binding(
        "tenant-b",
        "ddc4e27e-ccde-4c89-958f-798fc8f30175",
        "tenant-b@fixture.invalid",
        ENTERPRISE_B,
        "enterprise_admin",
    ),
)


def _stable_id(kind: str, *parts: object) -> uuid.UUID:
    value = ":".join((kind, *(str(part) for part in parts)))
    return uuid.uuid5(SEED_NAMESPACE, value)


def _ensure_enterprise(
    connection: psycopg.Connection,
    enterprise_id: uuid.UUID,
    name: str,
    license_no: str,
) -> None:
    connection.execute(
        "INSERT INTO f1.enterprise "
        "(id,name,license_no,f0i_enterprise_id) VALUES (%s,%s,%s,NULL) "
        "ON CONFLICT (id) DO NOTHING",
        (enterprise_id, name, license_no),
    )
    row = connection.execute(
        "SELECT name,license_no,f0i_enterprise_id FROM f1.enterprise WHERE id=%s",
        (enterprise_id,),
    ).fetchone()
    if row is None or tuple(row) != (name, license_no, None):
        raise RuntimeError("LOCAL_SEED_ENTERPRISE_MISMATCH")


def _ensure_binding(connection: psycopg.Connection, binding: Binding) -> None:
    profile_id = _stable_id("profile", binding.sub)
    connection.execute(
        "INSERT INTO f1.user_profile (id,keycloak_sub,email) VALUES (%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (profile_id, binding.sub, binding.email),
    )
    profile = connection.execute(
        "SELECT keycloak_sub,email FROM f1.user_profile WHERE id=%s",
        (profile_id,),
    ).fetchone()
    if profile is None or tuple(profile) != (binding.sub, binding.email):
        raise RuntimeError("LOCAL_SEED_PROFILE_MISMATCH")

    membership_id = _stable_id(
        "membership", binding.enterprise_id, binding.sub
    )
    connection.execute(
        "INSERT INTO f1.enterprise_user "
        "(id,enterprise_id,user_id,role) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (enterprise_id,user_id) DO NOTHING",
        (membership_id, binding.enterprise_id, profile_id, binding.role),
    )
    membership = connection.execute(
        "SELECT id,role FROM f1.enterprise_user "
        "WHERE enterprise_id=%s AND user_id=%s",
        (binding.enterprise_id, profile_id),
    ).fetchone()
    if membership is None or tuple(membership) != (
        membership_id,
        binding.role,
    ):
        raise RuntimeError("LOCAL_SEED_MEMBERSHIP_MISMATCH")


def main() -> int:
    with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
        head = connection.execute(
            "SELECT string_agg(version_num, ',' ORDER BY version_num) "
            "FROM f1.alembic_version"
        ).fetchone()
        if head is None or head[0] != "f1_0010":
            raise RuntimeError("LOCAL_SEED_MIGRATION_REQUIRED")
        _ensure_enterprise(
            connection, ENTERPRISE_A, "Local Enterprise A", "LOCAL-A"
        )
        _ensure_enterprise(
            connection, ENTERPRISE_B, "Local Enterprise B", "LOCAL-B"
        )
        for binding in BINDINGS:
            _ensure_binding(connection, binding)
        connection.commit()
    print("LOCAL_SEED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
