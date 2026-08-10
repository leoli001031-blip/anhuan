"""F1.1 seed: synthetic dual tenants + user bindings (idempotent).

Seeds the f1.* schema with enterprise A (mapped to the F0-I acceptance
tenant) and synthetic enterprise B, plus the realm users' enterprise
memberships.  Runs as the f1_api role with transaction-local tenant
contexts; safe to re-run.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import psycopg  # noqa: E402

from platform_foundation.f1.config import (  # noqa: E402
    pg_database,
    pg_host,
    pg_port,
)
from platform_foundation.f1.secret_files import read_f1_secret_text  # noqa: E402

# Deterministic ids for the synthetic dual tenants.
ENTERPRISE_A = uuid.UUID("10000000-0000-4000-8000-00000000000a")
ENTERPRISE_B = uuid.UUID("10000000-0000-4000-8000-00000000000b")
F0I_TENANT_A = uuid.UUID("4842a9d5-b719-5d5c-b2de-6ad679d1cb8d")
SEED_NAMESPACE = uuid.UUID("3d2f5ef4-f633-4e31-bf50-99928b3dc98c")

# These ids are the Keycloak ``sub`` values in realm-import.json.  Keep even
# the deliberately-unbound invitee here so the clean seed and realm have one
# auditable identity contract rather than two handwritten lists.
IDENTITY_SUBS = {
    "admin": "d561ffe2-3be8-40cc-a87e-598dd7d84758",
    "tester": "f1f70ce5-465f-489c-a89d-974a63216ab4",
    "tenant_a": "db906685-6906-4bc4-9d3a-9011975fd132",
    "tenant_b": "ddc4e27e-ccde-4c89-958f-798fc8f30175",
    "invitee": "6f735662-672f-4aeb-9234-9a3390392f33",
    "auditor": "7e9978c7-106f-4221-a6d7-79e8104a659b",
}

# Realm sub -> (enterprise, role, synthetic OIDC email).  The invitee remains
# unbound until the invitation concurrency probe consumes its one-time token.
BINDINGS = [
    (IDENTITY_SUBS["tester"], ENTERPRISE_A, "partner", "tester@fixture.invalid"),
    (IDENTITY_SUBS["admin"], ENTERPRISE_A, "super_admin", "admin@fixture.invalid"),
    (IDENTITY_SUBS["tenant_a"], ENTERPRISE_A, "enterprise_admin", "tenant-a@fixture.invalid"),
    (IDENTITY_SUBS["tenant_b"], ENTERPRISE_B, "enterprise_admin", "tenant-b@fixture.invalid"),
    (IDENTITY_SUBS["auditor"], ENTERPRISE_A, "auditor", "auditor@fixture.invalid"),
]


def _seed_uuid(kind: str, *parts: object) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, ":".join((kind, *(str(part) for part in parts))))


def _api_connection_kwargs() -> dict[str, str]:
    return {
        "host": pg_host(),
        "port": pg_port(),
        "dbname": pg_database(),
        "user": "f1_api",
        "password": read_f1_secret_text(
            "f1_api_password", file_env="F1_API_PASSWORD_FILE"
        ),
    }


def _set_context(conn: psycopg.Connection, eid: uuid.UUID, sub: str) -> None:
    conn.execute(
        "SELECT set_config('f1.enterprise_id', %s, true)", (str(eid),)
    )
    conn.execute("SELECT set_config('f1.sub', %s, true)", (sub,))


def main() -> int:
    with psycopg.connect(**_api_connection_kwargs()) as conn:
        # Enterprise A (F0-I tenant) and synthetic B.
        _set_context(conn, ENTERPRISE_A, "seed")
        conn.execute(
            "INSERT INTO f1.enterprise (id, name, license_no, f0i_enterprise_id) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, "
            "f0i_enterprise_id = EXCLUDED.f0i_enterprise_id",
            (ENTERPRISE_A, "Tenant A", "FIX-A", F0I_TENANT_A),
        )
        conn.commit()
        _set_context(conn, ENTERPRISE_B, "seed")
        conn.execute(
            "INSERT INTO f1.enterprise (id, name, license_no, f0i_enterprise_id) "
            "VALUES (%s, %s, %s, NULL) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, "
            "f0i_enterprise_id = EXCLUDED.f0i_enterprise_id",
            (ENTERPRISE_B, "Tenant B", "FIX-B"),
        )
        conn.commit()

        for keycloak_sub, eid, role, email in BINDINGS:
            _set_context(conn, eid, keycloak_sub)
            profile = conn.execute(
                "SELECT id FROM f1.user_profile WHERE keycloak_sub = %s",
                (keycloak_sub,),
            ).fetchone()
            if profile is None:
                uid = _seed_uuid("profile", keycloak_sub)
                conn.execute(
                    "INSERT INTO f1.user_profile (id, keycloak_sub, email) "
                    "VALUES (%s, %s, %s)",
                    (uid, keycloak_sub, email),
                )
            else:
                uid = profile[0]
            conn.execute(
                "INSERT INTO f1.enterprise_user (id, enterprise_id, user_id, role) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (enterprise_id, user_id) DO UPDATE SET role = EXCLUDED.role",
                (_seed_uuid("membership", eid, keycloak_sub), eid, uid, role),
            )
            conn.commit()
    print("F1_SEED_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
