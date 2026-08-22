"""Local dual-identity fixture for analysis-report browser UAT wiring.

Runtime is NOT a Keycloak or browser acceptance. This script never starts
the default f1_0014 compose, never creates realm users or passwords, and
never writes real personal data.

Requires dual local flags, pgint closed-set identity, and alembic head
exactly f1_0017. Reuses realm subjects tenant-a (provider A) and invitee
(client B). employee remains enterprise A / plant_admin and is never
rewritten. Extra memberships and wrong roles fail-closed; this fixture
never deletes memberships or overwrites roles.
"""
from __future__ import annotations

import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import psycopg  # noqa: E402

from infra.f1 import local_seed  # noqa: E402
from infra.f1.migrate_f1 import _bootstrap_dsn  # noqa: E402
from platform_foundation.f1.config import pg_database, pg_host  # noqa: E402

TENANT_A_SUB = "db906685-6906-4bc4-9d3a-9011975fd132"
TENANT_A_EMAIL = "tenant-a@fixture.invalid"
INVITEE_SUB = "6f735662-672f-4aeb-9234-9a3390392f33"
INVITEE_EMAIL = "invitee@fixture.invalid"
EMPLOYEE_SUB = "3247dddb-69bc-4ad1-841c-8fc338b603ce"
EMPLOYEE_EMAIL = "employee@fixture.invalid"

FIXTURE_NS = uuid.UUID("7c2a9e11-4d08-4b3e-9f1a-6e5c0b8d2a14")
CRM_ACCOUNT_ID = uuid.uuid5(FIXTURE_NS, "crm-account:provider-a:audience-b")
BINDING_ID = uuid.uuid5(FIXTURE_NS, "audience-binding:provider-a:audience-b")
CRM_DISPLAY_NAME = "Local analysis-report audience B"

PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PROJECT_NAME_RE = re.compile(r"^anhuan-ar-pgint-([0-9a-f]{12})$")
DATABASE_RE = re.compile(r"^f1_arpg_([0-9a-f]{12})$")
CONTROL_DIR_RE = re.compile(r"^/private/tmp/anhuan-ar-pgint-([0-9a-f]{12})$")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})
BOOTSTRAP_ROLE = "f0d_bootstrap"


@dataclass(frozen=True)
class PgintIdentity:
    project_id: str
    project_name: str
    database: str
    control_dir: Path


def _require_dual_local_flags() -> None:
    if (
        os.environ.get("F1_LOCAL_ENGINEERING") != "1"
        or os.environ.get("F1_MATERIAL_ANALYSIS_REPORT_LOCAL") != "1"
    ):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_ENGINEERING_REQUIRED")


def _env_token(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
    return value


def _closed_suffix(project_name: str, database: str, control_dir: str) -> str:
    name_match = PROJECT_NAME_RE.fullmatch(project_name)
    database_match = DATABASE_RE.fullmatch(database)
    control_match = CONTROL_DIR_RE.fullmatch(control_dir)
    if name_match is None or database_match is None or control_match is None:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
    suffix = name_match.group(1)
    if suffix != database_match.group(1) or suffix != control_match.group(1):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
    return suffix


def _require_closed_path(path: Path, *, directory: bool, mode: int) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")
    if stat.S_IMODE(info.st_mode) != mode or info.st_uid != os.geteuid():
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")


def _read_identity_receipt(control_dir: Path) -> dict[str, str]:
    receipt = control_dir / "identity.receipt"
    _require_closed_path(receipt, directory=False, mode=0o600)
    parsed: dict[str, str] = {}
    for line in receipt.read_text(encoding="ascii").splitlines():
        if not line or "=" not in line:
            raise RuntimeError("LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")
        key, value = line.split("=", 1)
        parsed[key] = value
    return parsed


def _require_pgint_identity() -> PgintIdentity:
    project_id = _env_token("LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID")
    project_name = _env_token("LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME")
    database = _env_token("LOCAL_ANALYSIS_REPORT_PGINT_DATABASE")
    control_dir_raw = _env_token("LOCAL_ANALYSIS_REPORT_PGINT_CONTROL_DIR")
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
    _closed_suffix(project_name, database, control_dir_raw)
    declared = pg_database()
    if declared != database:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_DATABASE_MISMATCH")
    host = pg_host()
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_HOST_NOT_LOOPBACK")
    control_dir = Path(control_dir_raw)
    _require_closed_path(control_dir, directory=True, mode=0o700)
    receipt = _read_identity_receipt(control_dir)
    if receipt != {
        "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID": project_id,
        "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME": project_name,
        "LOCAL_ANALYSIS_REPORT_PGINT_DATABASE": database,
    }:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
    return PgintIdentity(
        project_id=project_id,
        project_name=project_name,
        database=database,
        control_dir=control_dir,
    )


def _preflight_target_identity(
    connection: psycopg.Connection, identity: PgintIdentity
) -> None:
    row = connection.execute(
        "SELECT current_database(), current_user, session_user"
    ).fetchone()
    if row is None:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_IDENTITY_MISMATCH")
    current_db, current_user, session_user = row
    if current_db != identity.database:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_DATABASE_MISMATCH")
    if current_user != BOOTSTRAP_ROLE or session_user != BOOTSTRAP_ROLE:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_IDENTITY_MISMATCH")
    head = connection.execute(
        "SELECT string_agg(version_num, ',' ORDER BY version_num), count(*) "
        "FROM f1.alembic_version"
    ).fetchone()
    if head is None or tuple(head) != ("f1_0017", 1):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_HEAD_MISMATCH")


def _preflight_enterprise(
    connection: psycopg.Connection, enterprise_id: uuid.UUID, name: str, license_no: str
) -> None:
    row = connection.execute(
        "SELECT name, license_no FROM f1.enterprise WHERE id=%s",
        (enterprise_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_ENTERPRISE_MISSING")
    if tuple(row) != (name, license_no):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_ENTERPRISE_MISMATCH")


def _preflight_membership_shape(connection: psycopg.Connection) -> None:
    for sub, email, enterprise_id, role in (
        (
            TENANT_A_SUB,
            TENANT_A_EMAIL,
            local_seed.ENTERPRISE_A,
            "enterprise_admin",
        ),
        (
            INVITEE_SUB,
            INVITEE_EMAIL,
            local_seed.ENTERPRISE_B,
            "plant_admin",
        ),
    ):
        profile_id = local_seed._stable_id("profile", sub)
        profile = connection.execute(
            "SELECT keycloak_sub,email FROM f1.user_profile WHERE id=%s",
            (profile_id,),
        ).fetchone()
        if profile is not None and tuple(profile) != (sub, email):
            raise RuntimeError("LOCAL_REPORT_FIXTURE_PROFILE_MISMATCH")
        rows = connection.execute(
            "SELECT enterprise_id, role FROM f1.enterprise_user WHERE user_id=%s",
            (profile_id,),
        ).fetchall()
        extras = [item for item in rows if item[0] != enterprise_id]
        if extras:
            raise RuntimeError("LOCAL_REPORT_FIXTURE_EXTRA_MEMBERSHIP")
        wrong_role = [item for item in rows if item[0] == enterprise_id and item[1] != role]
        if wrong_role:
            raise RuntimeError("LOCAL_REPORT_FIXTURE_ROLE_MISMATCH")
    _preflight_employee_frozen(connection)


def _preflight_employee_frozen(connection: psycopg.Connection) -> None:
    profile_id = local_seed._stable_id("profile", EMPLOYEE_SUB)
    profile = connection.execute(
        "SELECT keycloak_sub,email FROM f1.user_profile WHERE id=%s",
        (profile_id,),
    ).fetchone()
    if profile is None or tuple(profile) != (EMPLOYEE_SUB, EMPLOYEE_EMAIL):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_EMPLOYEE_MISMATCH")
    rows = connection.execute(
        "SELECT enterprise_id, role FROM f1.enterprise_user WHERE user_id=%s "
        "ORDER BY enterprise_id",
        (profile_id,),
    ).fetchall()
    if len(rows) != 1 or tuple(rows[0]) != (local_seed.ENTERPRISE_A, "plant_admin"):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_EMPLOYEE_MISMATCH")


def _ensure_profile(
    connection: psycopg.Connection, sub: str, email: str
) -> uuid.UUID:
    profile_id = local_seed._stable_id("profile", sub)
    connection.execute(
        "INSERT INTO f1.user_profile (id,keycloak_sub,email) VALUES (%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (profile_id, sub, email),
    )
    profile = connection.execute(
        "SELECT keycloak_sub,email FROM f1.user_profile WHERE id=%s",
        (profile_id,),
    ).fetchone()
    if profile is None or tuple(profile) != (sub, email):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROFILE_MISMATCH")
    return profile_id


def _ensure_target_membership(
    connection: psycopg.Connection,
    *,
    sub: str,
    profile_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    role: str,
) -> None:
    membership_id = local_seed._stable_id("membership", enterprise_id, sub)
    connection.execute(
        "INSERT INTO f1.enterprise_user "
        "(id,enterprise_id,user_id,role) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (enterprise_id,user_id) DO NOTHING",
        (membership_id, enterprise_id, profile_id, role),
    )
    rows = connection.execute(
        "SELECT id,enterprise_id,role FROM f1.enterprise_user WHERE user_id=%s",
        (profile_id,),
    ).fetchall()
    if len(rows) != 1 or tuple(rows[0]) != (membership_id, enterprise_id, role):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_MEMBERSHIP_MISMATCH")


def _ensure_crm_and_binding(
    connection: psycopg.Connection, creator_id: uuid.UUID
) -> None:
    connection.execute(
        "INSERT INTO f1.crm_account "
        "(id,enterprise_id,display_name,stage,created_by_user_id) "
        "VALUES (%s,%s,%s,'active',%s) "
        "ON CONFLICT (id) DO NOTHING",
        (
            CRM_ACCOUNT_ID,
            local_seed.ENTERPRISE_A,
            CRM_DISPLAY_NAME,
            creator_id,
        ),
    )
    account = connection.execute(
        "SELECT enterprise_id,display_name,stage,created_by_user_id "
        "FROM f1.crm_account WHERE id=%s",
        (CRM_ACCOUNT_ID,),
    ).fetchone()
    if account is None or tuple(account) != (
        local_seed.ENTERPRISE_A,
        CRM_DISPLAY_NAME,
        "active",
        creator_id,
    ):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CRM_MISMATCH")

    connection.execute(
        "INSERT INTO f1.analysis_report_client_audience "
        "(id,enterprise_id,client_account_id,audience_enterprise_id,status) "
        "VALUES (%s,%s,%s,%s,'active') "
        "ON CONFLICT (id) DO NOTHING",
        (
            BINDING_ID,
            local_seed.ENTERPRISE_A,
            CRM_ACCOUNT_ID,
            local_seed.ENTERPRISE_B,
        ),
    )
    binding = connection.execute(
        "SELECT enterprise_id,client_account_id,audience_enterprise_id,status "
        "FROM f1.analysis_report_client_audience WHERE id=%s",
        (BINDING_ID,),
    ).fetchone()
    if binding is None or tuple(binding) != (
        local_seed.ENTERPRISE_A,
        CRM_ACCOUNT_ID,
        local_seed.ENTERPRISE_B,
        "active",
    ):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_BINDING_MISMATCH")


def apply() -> None:
    _require_dual_local_flags()
    identity = _require_pgint_identity()
    with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
        _preflight_target_identity(connection, identity)
        _preflight_enterprise(
            connection, local_seed.ENTERPRISE_A, "Local Enterprise A", "LOCAL-A"
        )
        _preflight_enterprise(
            connection, local_seed.ENTERPRISE_B, "Local Enterprise B", "LOCAL-B"
        )
        _preflight_membership_shape(connection)
        provider_id = _ensure_profile(connection, TENANT_A_SUB, TENANT_A_EMAIL)
        client_id = _ensure_profile(connection, INVITEE_SUB, INVITEE_EMAIL)
        _ensure_target_membership(
            connection,
            sub=TENANT_A_SUB,
            profile_id=provider_id,
            enterprise_id=local_seed.ENTERPRISE_A,
            role="enterprise_admin",
        )
        _ensure_target_membership(
            connection,
            sub=INVITEE_SUB,
            profile_id=client_id,
            enterprise_id=local_seed.ENTERPRISE_B,
            role="plant_admin",
        )
        _ensure_crm_and_binding(connection, provider_id)
        connection.commit()


def main() -> int:
    apply()
    print("LOCAL_ANALYSIS_REPORT_BROWSER_FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
