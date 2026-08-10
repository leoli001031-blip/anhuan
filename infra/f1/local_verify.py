"""Read-only, body-free verifier for the isolated engineering stack.

The verifier intentionally uses the bootstrap DSN: PostgreSQL catalog checks
and the complete synthetic seed contract must not depend on an application
role's row visibility.  Successful output contains counts only.  Every failure
is reduced to a fixed reason code so credentials, row values, and tracebacks
cannot escape through the command surface.
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


P2_P7_TABLES = (
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

ENTERPRISE_A = "20000000-0000-4000-8000-00000000000a"
ENTERPRISE_B = "20000000-0000-4000-8000-00000000000b"

EXPECTED_ENTERPRISES = (
    (ENTERPRISE_A, "Local Enterprise A", "LOCAL-A", None),
    (ENTERPRISE_B, "Local Enterprise B", "LOCAL-B", None),
)

# enterprise_id, OIDC sub, email, membership role.  These literals form an
# independent check of local_seed.py rather than importing its live values.
EXPECTED_BINDINGS = (
    (
        ENTERPRISE_A,
        "d561ffe2-3be8-40cc-a87e-598dd7d84758",
        "admin@fixture.invalid",
        "super_admin",
    ),
    (
        ENTERPRISE_B,
        "d561ffe2-3be8-40cc-a87e-598dd7d84758",
        "admin@fixture.invalid",
        "super_admin",
    ),
    (
        ENTERPRISE_A,
        "db906685-6906-4bc4-9d3a-9011975fd132",
        "tenant-a@fixture.invalid",
        "enterprise_admin",
    ),
    (
        ENTERPRISE_A,
        "3247dddb-69bc-4ad1-841c-8fc338b603ce",
        "employee@fixture.invalid",
        "plant_admin",
    ),
    (
        ENTERPRISE_A,
        "7e9978c7-106f-4221-a6d7-79e8104a659b",
        "auditor@fixture.invalid",
        "auditor",
    ),
    (
        ENTERPRISE_A,
        "f1f70ce5-465f-489c-a89d-974a63216ab4",
        "tester@fixture.invalid",
        "partner",
    ),
    (
        ENTERPRISE_B,
        "ddc4e27e-ccde-4c89-958f-798fc8f30175",
        "tenant-b@fixture.invalid",
        "enterprise_admin",
    ),
)

EXPECTED_RUNTIME_ROLES = (
    ("f1_api", True, False, False, False, False, False, False, 20),
    ("f1_worker", True, False, False, False, False, False, False, 10),
)


class _Cursor(Protocol):
    def fetchall(self) -> list[tuple[object, ...]]: ...

    def fetchone(self) -> tuple[object, ...] | None: ...


class _Connection(Protocol):
    def execute(
        self,
        query: str,
        parameters: tuple[object, ...] | None = None,
    ) -> _Cursor: ...


@dataclass(frozen=True, slots=True)
class Snapshot:
    identity: tuple[str, str, str]
    f0_heads: tuple[str, ...]
    f1_heads: tuple[str, ...]
    rls_rows: tuple[tuple[str, bool, bool], ...]
    runtime_roles: tuple[tuple[object, ...], ...]
    runtime_role_memberships: int
    enterprises: tuple[tuple[object, ...], ...]
    bindings: tuple[tuple[object, ...], ...]


@dataclass(frozen=True, slots=True)
class VerificationCounts:
    database_identity_mismatch_count: int = 0
    migration_head_count: int = 2
    migration_head_mismatch_count: int = 0
    rls_table_count: int = 31
    rls_mismatch_count: int = 0
    runtime_role_count: int = 2
    runtime_role_mismatch_count: int = 0
    runtime_role_membership_count: int = 0
    seed_enterprise_count: int = 2
    seed_enterprise_mismatch_count: int = 0
    seed_binding_count: int = 7
    seed_binding_mismatch_count: int = 0


class VerificationError(RuntimeError):
    """A fixed, non-sensitive local verification failure."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _rows(cursor: _Cursor) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in cursor.fetchall())


def collect_snapshot(connection: _Connection) -> Snapshot:
    """Collect one repeatable-read snapshot without reading business bodies."""
    connection.execute(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    identity_row = connection.execute(
        "SELECT current_user,session_user,current_database()"
    ).fetchone()
    if identity_row is None or len(identity_row) != 3:
        raise VerificationError("LOCAL_VERIFY_DATABASE_IDENTITY_MISMATCH")

    f0_heads = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT version_num FROM f0d.alembic_version ORDER BY version_num"
        ).fetchall()
    )
    f1_heads = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT version_num FROM f1.alembic_version ORDER BY version_num"
        ).fetchall()
    )

    rls_rows = tuple(
        (str(name), bool(enabled), bool(forced))
        for name, enabled, forced in connection.execute(
            "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity "
            "FROM pg_class AS c "
            "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
            "WHERE n.nspname='f1' AND c.relkind IN ('r','p') "
            "AND c.relname=ANY(%s) ORDER BY c.relname",
            (list(P2_P7_TABLES),),
        ).fetchall()
    )

    runtime_roles = _rows(
        connection.execute(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls,rolconnlimit "
            "FROM pg_roles WHERE rolname IN ('f1_api','f1_worker') "
            "ORDER BY rolname"
        )
    )
    membership_row = connection.execute(
        "SELECT count(*) FROM pg_auth_members AS m "
        "JOIN pg_roles AS granted ON granted.oid=m.roleid "
        "JOIN pg_roles AS member ON member.oid=m.member "
        "WHERE granted.rolname IN ('f1_api','f1_worker') "
        "OR member.rolname IN ('f1_api','f1_worker')"
    ).fetchone()
    if membership_row is None or len(membership_row) != 1:
        raise VerificationError("LOCAL_VERIFY_ROLE_MEMBERSHIP_MISMATCH")

    enterprise_ids = (uuid.UUID(ENTERPRISE_A), uuid.UUID(ENTERPRISE_B))
    enterprises = _rows(
        connection.execute(
            "SELECT id::text,name,license_no,f0i_enterprise_id::text "
            "FROM f1.enterprise WHERE id=ANY(%s) ORDER BY id",
            (list(enterprise_ids),),
        )
    )
    seed_subs = sorted({binding[1] for binding in EXPECTED_BINDINGS})
    bindings = _rows(
        connection.execute(
            "SELECT eu.enterprise_id::text,up.keycloak_sub,up.email,eu.role "
            "FROM f1.enterprise_user AS eu "
            "JOIN f1.user_profile AS up ON up.id=eu.user_id "
            "WHERE eu.enterprise_id=ANY(%s) AND up.keycloak_sub=ANY(%s) "
            "ORDER BY eu.enterprise_id,up.keycloak_sub",
            (list(enterprise_ids), seed_subs),
        )
    )

    return Snapshot(
        identity=tuple(str(value) for value in identity_row),  # type: ignore[arg-type]
        f0_heads=f0_heads,
        f1_heads=f1_heads,
        rls_rows=rls_rows,
        runtime_roles=runtime_roles,
        runtime_role_memberships=int(membership_row[0]),
        enterprises=enterprises,
        bindings=bindings,
    )


def verify_snapshot(snapshot: Snapshot, *, expected_database: str) -> VerificationCounts:
    """Apply the exact engineering contract to a collected DB snapshot."""
    if snapshot.identity != (
        "f0d_bootstrap",
        "f0d_bootstrap",
        expected_database,
    ):
        raise VerificationError("LOCAL_VERIFY_DATABASE_IDENTITY_MISMATCH")
    if snapshot.f0_heads != ("f0d_0006",) or snapshot.f1_heads != ("f1_0010",):
        raise VerificationError("LOCAL_VERIFY_HEAD_MISMATCH")

    expected_rls = tuple((name, True, True) for name in sorted(P2_P7_TABLES))
    if tuple(sorted(snapshot.rls_rows)) != expected_rls:
        raise VerificationError("LOCAL_VERIFY_RLS_MISMATCH")
    if tuple(snapshot.runtime_roles) != EXPECTED_RUNTIME_ROLES:
        raise VerificationError("LOCAL_VERIFY_RUNTIME_ROLE_MISMATCH")
    if snapshot.runtime_role_memberships != 0:
        raise VerificationError("LOCAL_VERIFY_ROLE_MEMBERSHIP_MISMATCH")
    if tuple(sorted(snapshot.enterprises)) != tuple(sorted(EXPECTED_ENTERPRISES)):
        raise VerificationError("LOCAL_VERIFY_SEED_ENTERPRISE_MISMATCH")
    if tuple(sorted(snapshot.bindings)) != tuple(sorted(EXPECTED_BINDINGS)):
        raise VerificationError("LOCAL_VERIFY_SEED_BINDING_MISMATCH")
    return VerificationCounts()


def render_success(counts: VerificationCounts) -> tuple[str, str]:
    """Render the only successful stdout shape: fixed integer metrics + tag."""
    metrics = json.dumps(asdict(counts), separators=(",", ":"), sort_keys=True)
    return metrics, "LOCAL_VERIFY_OK"


def run() -> VerificationCounts:
    """Connect through the validated bootstrap secret and verify the snapshot."""
    try:
        import psycopg

        from infra.f1.migrate_f1 import _bootstrap_dsn
        from platform_foundation.f1.config import pg_database

        with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
            snapshot = collect_snapshot(connection)
        return verify_snapshot(snapshot, expected_database=pg_database())
    except VerificationError:
        raise
    except Exception:
        raise VerificationError("LOCAL_VERIFY_CONNECTION_FAILED") from None


def main() -> int:
    try:
        counts = run()
    except VerificationError as error:
        print(error.reason, file=sys.stderr)
        return 1
    for line in render_success(counts):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
