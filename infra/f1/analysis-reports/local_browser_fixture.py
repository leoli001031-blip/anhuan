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

import hashlib
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
CLIENT_SCOPE_ID = uuid.uuid5(FIXTURE_NS, "client-scope:provider-a:audience-b")
PROVIDER_SCOPE_FALLBACK_ID = uuid.uuid5(
    FIXTURE_NS, "provider-scope:enterprise-a"
)
CRM_DISPLAY_NAME = "Local analysis-report audience B"
PARSER_VERSION = "arfix1"
PROVIDER_MATERIAL_LABEL = "arfix-provider"
CLIENT_MATERIAL_LABEL = "arfix-client"

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


def _stable_material_id(kind: str, label: str) -> uuid.UUID:
    return uuid.uuid5(FIXTURE_NS, f"{kind}:{label}")


def _provider_scope_id(connection: psycopg.Connection) -> uuid.UUID:
    rows = connection.execute(
        "SELECT id FROM f1.material_knowledge_scope "
        "WHERE enterprise_id=%s AND scope_kind='service_provider' "
        "AND client_account_id IS NULL ORDER BY id",
        (local_seed.ENTERPRISE_A,),
    ).fetchall()
    if len(rows) > 1:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_PROVIDER_SCOPE_EXTRA")
    if len(rows) == 1:
        return rows[0][0]
    connection.execute(
        "INSERT INTO f1.material_knowledge_scope "
        "(id,enterprise_id,scope_kind,client_account_id) "
        "VALUES (%s,%s,'service_provider',NULL)",
        (PROVIDER_SCOPE_FALLBACK_ID, local_seed.ENTERPRISE_A),
    )
    return PROVIDER_SCOPE_FALLBACK_ID


def _ensure_client_scope(connection: psycopg.Connection) -> uuid.UUID:
    connection.execute(
        "INSERT INTO f1.material_knowledge_scope "
        "(id,enterprise_id,scope_kind,client_account_id) "
        "VALUES (%s,%s,'client',%s) ON CONFLICT (id) DO NOTHING",
        (CLIENT_SCOPE_ID, local_seed.ENTERPRISE_A, CRM_ACCOUNT_ID),
    )
    row = connection.execute(
        "SELECT id,enterprise_id,scope_kind,client_account_id "
        "FROM f1.material_knowledge_scope WHERE id=%s",
        (CLIENT_SCOPE_ID,),
    ).fetchone()
    if row is None or tuple(row) != (
        CLIENT_SCOPE_ID,
        local_seed.ENTERPRISE_A,
        "client",
        CRM_ACCOUNT_ID,
    ):
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CLIENT_SCOPE_MISMATCH")
    extras = connection.execute(
        "SELECT count(*) FROM f1.material_knowledge_scope "
        "WHERE enterprise_id=%s AND scope_kind='client' AND client_account_id=%s",
        (local_seed.ENTERPRISE_A, CRM_ACCOUNT_ID),
    ).fetchone()
    if extras is None or int(extras[0]) != 1:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_CLIENT_SCOPE_EXTRA")
    return CLIENT_SCOPE_ID


def _insert_synthetic_unit(
    connection: psycopg.Connection,
    *,
    label: str,
    scope_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> None:
    document_id = _stable_material_id("document", label)
    record_id = _stable_material_id("record", label)
    task_id = _stable_material_id("task", label)
    version_id = _stable_material_id("version", label)
    unit_id = _stable_material_id("unit", label)
    source_sha = hashlib.sha256(f"arfix|{label}|{local_seed.ENTERPRISE_A}".encode()).hexdigest()
    object_key = f"arfix/{label}"
    title = f"{label}-current"
    body = f"{label} 合成材料用于分析报告本地夹具。"
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    aad_sha = hashlib.sha256(f"arfix-aad|{label}".encode()).hexdigest()
    ciphertext = b"ARFIX1" + bytes.fromhex(source_sha[:64])
    connection.execute(
        "INSERT INTO f1.document "
        "(id,enterprise_id,object_key,filename,size,content_type,status,"
        "knowledge_scope_id) VALUES (%s,%s,%s,%s,32,'application/pdf','done',%s) "
        "ON CONFLICT (id) DO NOTHING",
        (document_id, local_seed.ENTERPRISE_A, object_key, f"{label}.pdf", scope_id),
    )
    connection.execute(
        "INSERT INTO f1.document_record "
        "(id,enterprise_id,title,status,latest_version_no,created_by_user_id,"
        "declared_material_kind,knowledge_scope_id,scope_selection_source,"
        "scope_selected_by_user_id,scope_selected_at) "
        "VALUES (%s,%s,%s,'active',1,%s,'unknown',%s,'upload_selection',%s,"
        "statement_timestamp()) ON CONFLICT (id) DO NOTHING",
        (record_id, local_seed.ENTERPRISE_A, title, actor_id, scope_id, actor_id),
    )
    connection.execute(
        "INSERT INTO f1.upload_task "
        "(id,enterprise_id,document_id,object_key,content_sha256,status,"
        "object_state,pipeline_kind,processing_stage,quarantine_status,"
        "scan_verdict,preview_status,preview_kind,released_at) "
        "VALUES (%s,%s,%s,%s,%s,'done','ready','controlled_ingestion','ready',"
        "'released','clean','ready','page_text',statement_timestamp()) "
        "ON CONFLICT (id) DO NOTHING",
        (task_id, local_seed.ENTERPRISE_A, document_id, object_key, source_sha),
    )
    connection.execute(
        "INSERT INTO f1.document_version "
        "(id,enterprise_id,document_record_id,version_no,source_document_id,"
        "upload_task_id,display_filename,idempotency_key_sha256,"
        "created_by_user_id) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (
            version_id,
            local_seed.ENTERPRISE_A,
            record_id,
            document_id,
            task_id,
            f"{label}.pdf",
            hashlib.sha256(f"idem|{label}".encode()).hexdigest(),
            actor_id,
        ),
    )
    connection.execute(
        "INSERT INTO f1.material_rag_unit "
        "(id,enterprise_id,knowledge_scope_id,document_record_id,"
        "document_version_id,source_sha256,page_number,ordinal,parser_version,"
        "body_ciphertext,body_sha256,body_aad_sha256) "
        "VALUES (%s,%s,%s,%s,%s,%s,1,1,%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (
            unit_id,
            local_seed.ENTERPRISE_A,
            scope_id,
            record_id,
            version_id,
            source_sha,
            PARSER_VERSION,
            ciphertext,
            body_sha,
            aad_sha,
        ),
    )


def _verify_eligible_materials(connection: psycopg.Connection) -> None:
    rows = connection.execute(
        "SELECT scope.scope_kind, count(*) "
        "FROM f1.document_version AS version "
        "JOIN f1.document_record AS record "
        "  ON record.enterprise_id = version.enterprise_id "
        " AND record.id = version.document_record_id "
        "JOIN f1.upload_task AS task "
        "  ON task.enterprise_id = version.enterprise_id "
        " AND task.id = version.upload_task_id "
        "JOIN f1.material_knowledge_scope AS scope "
        "  ON scope.enterprise_id = record.enterprise_id "
        " AND scope.id = record.knowledge_scope_id "
        "JOIN f1.material_rag_unit AS unit "
        "  ON unit.enterprise_id = version.enterprise_id "
        " AND unit.document_version_id = version.id "
        " AND unit.document_record_id = record.id "
        " AND unit.source_sha256 = task.content_sha256 "
        "WHERE version.enterprise_id = %s "
        "  AND record.status = 'active' "
        "  AND version.version_no = record.latest_version_no "
        "  AND task.pipeline_kind = 'controlled_ingestion' "
        "  AND task.quarantine_status = 'released' "
        "  AND task.released_at IS NOT NULL "
        "  AND task.rejected_at IS NULL "
        "  AND task.scan_verdict = 'clean' "
        "  AND task.preview_status = 'ready' "
        "  AND task.object_state = 'ready' "
        "  AND task.status = 'done' "
        "  AND ("
        "    (scope.scope_kind = 'service_provider' AND scope.client_account_id IS NULL) "
        "    OR (scope.scope_kind = 'client' AND scope.client_account_id = %s)"
        "  ) "
        "GROUP BY scope.scope_kind",
        (local_seed.ENTERPRISE_A, CRM_ACCOUNT_ID),
    ).fetchall()
    counts = {str(kind): int(n) for kind, n in rows}
    if counts.get("service_provider") != 1 or counts.get("client") != 1:
        raise RuntimeError("LOCAL_REPORT_FIXTURE_MATERIAL_MISMATCH")


def _ensure_synthetic_materials(
    connection: psycopg.Connection, actor_id: uuid.UUID
) -> None:
    provider_scope = _provider_scope_id(connection)
    client_scope = _ensure_client_scope(connection)
    connection.execute(
        "ALTER TABLE f1.material_rag_unit DISABLE TRIGGER material_rag_unit_guard"
    )
    try:
        _insert_synthetic_unit(
            connection,
            label=PROVIDER_MATERIAL_LABEL,
            scope_id=provider_scope,
            actor_id=actor_id,
        )
        _insert_synthetic_unit(
            connection,
            label=CLIENT_MATERIAL_LABEL,
            scope_id=client_scope,
            actor_id=actor_id,
        )
    finally:
        connection.execute(
            "ALTER TABLE f1.material_rag_unit ENABLE TRIGGER material_rag_unit_guard"
        )
    _verify_eligible_materials(connection)


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
        _ensure_synthetic_materials(connection, provider_id)
        connection.commit()


def main() -> int:
    apply()
    print("LOCAL_ANALYSIS_REPORT_BROWSER_FIXTURE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
