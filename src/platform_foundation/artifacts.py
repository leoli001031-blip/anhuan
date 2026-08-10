"""Deterministic, aggregate-only acceptance artifacts for F0-D."""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path

from .database import DatabaseConfig, role_transaction, tenant_transaction
from .service import PlatformService


ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[2]
    / "artifacts/f0d-upload-foundation/v0.1"
)
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})
_POSTGRES_IMAGE = (
    "postgres:18.3-bookworm@sha256:"
    "80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
)
_FROZEN = {
    "f0_a": "3096e49e79536e03a86aacb28eac764e017ff0282a44243ad47f6b5474e3db99",
    "f0_b": "28646fe34e1c31bd0663f0584f8abaa4a00dfba7e1b20da4db23d5e1f9eca075",
    "f0_c": "15ca3e7b8b20d9b75b72f59e4cec83f07a3be62f565c0a09c344f08cd38358c9",
    "core_manifest": "e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae",
    "negative_manifest": "2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04",
    "f0_c_full_plan": "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436",
}
_TENANT_TABLES = frozenset(
    {
        "enterprise",
        "enterprise_membership",
        "local_fixture_session",
        "fixture_source_registry",
        "upload_session",
        "object_blob",
        "document",
        "document_version",
        "document_processing_plan",
        "document_processing_unit",
        "idempotency_record",
        "audit_event",
        "outbox_event",
        "job",
    }
)
_ACTOR_WRITE_TABLES = frozenset(
    {"upload_session", "idempotency_record", "audit_event"}
)
_LINEAGE_CONSTRAINTS = {
    "audit_event_actor_membership_fk": (
        "audit_event",
        ("enterprise_id", "actor_id"),
        "enterprise_membership",
        ("enterprise_id", "actor_id"),
    ),
    "object_blob_content_identity_fk": (
        "object_blob",
        ("enterprise_id", "upload_session_id", "sha256", "size_bytes"),
        "upload_session",
        ("enterprise_id", "id", "expected_sha256", "expected_size_bytes"),
    ),
    "document_version_upload_source_fk": (
        "document_version",
        ("enterprise_id", "upload_session_id", "source_document_id"),
        "upload_session",
        ("enterprise_id", "id", "source_document_id"),
    ),
    "document_version_blob_upload_fk": (
        "document_version",
        ("enterprise_id", "object_blob_id", "upload_session_id"),
        "object_blob",
        ("enterprise_id", "id", "upload_session_id"),
    ),
    "processing_plan_version_source_fk": (
        "document_processing_plan",
        ("enterprise_id", "document_version_id", "source_document_id"),
        "document_version",
        ("enterprise_id", "id", "source_document_id"),
    ),
}
_FORBIDDEN_TABLE_PRIVILEGES = (
    ("f0d_runtime", "audit_event", "INSERT"),
    ("f0d_runtime", "outbox_event", "INSERT"),
    ("f0d_runtime", "upload_session", "INSERT"),
    ("f0d_runtime", "upload_session", "UPDATE"),
    ("f0d_runtime", "idempotency_record", "UPDATE"),
    ("f0d_worker", "upload_session", "UPDATE"),
    ("f0d_worker", "idempotency_record", "UPDATE"),
    ("f0d_worker", "outbox_event", "UPDATE"),
    ("f0d_worker", "job", "UPDATE"),
)
_FORBIDDEN_COLUMN_UPDATES = (
    ("f0d_runtime", "upload_session", "id"),
    ("f0d_runtime", "upload_session", "enterprise_id"),
    ("f0d_runtime", "upload_session", "actor_id"),
    ("f0d_runtime", "upload_session", "source_document_id"),
    ("f0d_runtime", "upload_session", "expected_sha256"),
    ("f0d_runtime", "upload_session", "expected_size_bytes"),
    ("f0d_runtime", "upload_session", "completed_at"),
    ("f0d_worker", "upload_session", "id"),
    ("f0d_worker", "upload_session", "enterprise_id"),
    ("f0d_worker", "upload_session", "actor_id"),
    ("f0d_worker", "upload_session", "source_document_id"),
    ("f0d_worker", "upload_session", "expected_sha256"),
    ("f0d_worker", "upload_session", "expected_size_bytes"),
    ("f0d_worker", "idempotency_record", "enterprise_id"),
    ("f0d_worker", "idempotency_record", "actor_id"),
    ("f0d_worker", "idempotency_record", "idempotency_key_sha256"),
    ("f0d_worker", "idempotency_record", "request_sha256"),
    ("f0d_worker", "outbox_event", "enterprise_id"),
    ("f0d_worker", "outbox_event", "document_version_id"),
    ("f0d_worker", "outbox_event", "idempotency_key"),
    ("f0d_worker", "job", "enterprise_id"),
    ("f0d_worker", "job", "upload_session_id"),
    ("f0d_worker", "job", "document_version_id"),
    ("f0d_worker", "job", "idempotency_key"),
    ("f0d_worker", "job", "input_version"),
)


class ArtifactError(RuntimeError):
    def __init__(self, code: str = "ACCEPTANCE_ARTIFACT_FAILED") -> None:
        self.code = code
        super().__init__(code)


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _atomic_write(name: str, payload: bytes) -> None:
    if name not in _OUTPUTS:
        raise ArtifactError()
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ARTIFACT_ROOT, 0o700)
    destination = ARTIFACT_ROOT / name
    temporary = ARTIFACT_ROOT / f".{name}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise ArtifactError()
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        directory = os.open(ARTIFACT_ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except ArtifactError:
        raise
    except OSError:
        raise ArtifactError() from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _database_evidence(config: DatabaseConfig) -> dict[str, object]:
    from .bootstrap import TENANT_A, TENANT_B

    with role_transaction(config, "f0d_migration") as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_setting('server_version_num')::integer AS version_num"
            )
            version_num = int(cursor.fetchone()["version_num"])
            cursor.execute(
                "SELECT c.relname AS tablename FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='f0d' AND c.relkind='r' "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            )
            force_rls_tables = {str(row["tablename"]) for row in cursor.fetchall()}
            cursor.execute(
                "SELECT count(*) AS count FROM pg_roles WHERE rolname IN "
                "('f0d_migration','f0d_runtime','f0d_worker') AND "
                "(rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls)"
            )
            privileged = int(cursor.fetchone()["count"])
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = str(cursor.fetchone()["version_num"])
            cursor.execute(
                "SELECT c.conname,c.contype,srcn.nspname AS source_schema,"
                "src.relname AS source_table,refn.nspname AS target_schema,"
                "ref.relname AS target_table,"
                "ARRAY(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY "
                "AS key(attnum,position) JOIN pg_attribute a "
                "ON a.attrelid=c.conrelid AND a.attnum=key.attnum "
                "ORDER BY key.position) AS source_columns,"
                "ARRAY(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY "
                "AS key(attnum,position) JOIN pg_attribute a "
                "ON a.attrelid=c.confrelid AND a.attnum=key.attnum "
                "ORDER BY key.position) AS target_columns "
                "FROM pg_constraint c "
                "JOIN pg_namespace n ON n.oid=c.connamespace "
                "JOIN pg_class src ON src.oid=c.conrelid "
                "JOIN pg_namespace srcn ON srcn.oid=src.relnamespace "
                "JOIN pg_class ref ON ref.oid=c.confrelid "
                "JOIN pg_namespace refn ON refn.oid=ref.relnamespace "
                "WHERE n.nspname='f0d' AND conname IN ("
                "'audit_event_actor_membership_fk','object_blob_content_identity_fk',"
                "'document_version_upload_source_fk','document_version_blob_upload_fk',"
                "'processing_plan_version_source_fk')"
            )
            lineage_rows = cursor.fetchall()
            lineage_definitions = {
                str(row["conname"]): (
                    str(row["source_table"]),
                    tuple(str(column) for column in row["source_columns"]),
                    str(row["target_table"]),
                    tuple(str(column) for column in row["target_columns"]),
                )
                for row in lineage_rows
                if row["contype"] == "f"
                and row["source_schema"] == "f0d"
                and row["target_schema"] == "f0d"
            }
            cursor.execute(
                "SELECT tablename,permissive,roles,cmd,qual,with_check "
                "FROM pg_policies WHERE schemaname='f0d' "
                "AND policyname='tenant_boundary'"
            )
            session_policy_rows = cursor.fetchall()
            session_policy_tables = {
                str(row["tablename"]) for row in session_policy_rows
            }
            session_policy_exact = (
                session_policy_tables == _TENANT_TABLES
                and len(session_policy_rows) == len(_TENANT_TABLES)
                and all(
                    row["permissive"] == "PERMISSIVE"
                    and set(row["roles"]) == {"f0d_runtime", "f0d_worker"}
                    and row["cmd"] == "ALL"
                    and "context_session_authorized" in str(row["qual"])
                    and "context_session_authorized" in str(row["with_check"])
                    for row in session_policy_rows
                )
            )
            cursor.execute(
                "SELECT tablename,permissive,roles,cmd,qual,with_check "
                "FROM pg_policies WHERE schemaname='f0d' "
                "AND policyname='actor_insert_boundary'"
            )
            actor_policy_rows = cursor.fetchall()
            actor_policy_tables = {
                str(row["tablename"]) for row in actor_policy_rows
            }
            actor_policy_exact = (
                actor_policy_tables == _ACTOR_WRITE_TABLES
                and len(actor_policy_rows) == len(_ACTOR_WRITE_TABLES)
                and all(
                    row["permissive"] == "RESTRICTIVE"
                    and set(row["roles"]) == {"f0d_runtime", "f0d_worker"}
                    and row["cmd"] == "INSERT"
                    and row["qual"] is None
                    and "current_actor_id" in str(row["with_check"])
                    for row in actor_policy_rows
                )
            )
            cursor.execute(
                "SELECT ("
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','SELECT')::int + "
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','INSERT')::int + "
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','UPDATE')::int + "
                "has_table_privilege('f0d_worker','f0d.local_fixture_session','SELECT')::int + "
                "has_table_privilege('f0d_runtime','f0d.actor','SELECT')::int + "
                "has_table_privilege('f0d_worker','f0d.actor','SELECT')::int"
                ") AS count"
            )
            direct_auth_grants = int(cursor.fetchone()["count"])
            unsafe_privileges = 0
            for role in ("f0d_runtime", "f0d_worker"):
                cursor.execute(
                    "SELECT has_schema_privilege(%s,'f0d','CREATE') AS allowed",
                    (role,),
                )
                unsafe_privileges += int(cursor.fetchone()["allowed"])
                for table in sorted(_TENANT_TABLES | {"actor", "capability_gate"}):
                    cursor.execute(
                        "SELECT has_table_privilege(%s,%s,'TRUNCATE') AS allowed",
                        (role, f"f0d.{table}"),
                    )
                    unsafe_privileges += int(cursor.fetchone()["allowed"])
            for role, table, privilege in _FORBIDDEN_TABLE_PRIVILEGES:
                cursor.execute(
                    "SELECT has_table_privilege(%s,%s,%s) AS allowed",
                    (role, f"f0d.{table}", privilege),
                )
                unsafe_privileges += int(cursor.fetchone()["allowed"])
            for role, table, column in _FORBIDDEN_COLUMN_UPDATES:
                cursor.execute(
                    "SELECT has_column_privilege(%s,%s,%s,'UPDATE') AS allowed",
                    (role, f"f0d.{table}", column),
                )
                unsafe_privileges += int(cursor.fetchone()["allowed"])
            source_counts: dict[str, int] = {}
            for label, enterprise_id in (
                ("fixture", TENANT_A.enterprise_id),
                ("synthetic_canary", TENANT_B.enterprise_id),
            ):
                cursor.execute(
                    "SELECT set_config('f0d.enterprise_id', %s, true)",
                    (str(enterprise_id),),
                )
                cursor.execute(
                    "SELECT count(*) AS count FROM f0d.fixture_source_registry"
                )
                source_counts[label] = int(cursor.fetchone()["count"])
    return {
        "engine": "PostgreSQL",
        "major": version_num // 10_000,
        "image": _POSTGRES_IMAGE,
        "alembic_revision": revision,
        "force_rls_tables": len(force_rls_tables),
        "force_rls_table_set_exact": force_rls_tables == _TENANT_TABLES,
        "session_bound_rls_policies": len(session_policy_rows),
        "session_bound_rls_table_set_exact": session_policy_exact,
        "actor_bound_write_policies": len(actor_policy_rows),
        "actor_bound_write_table_set_exact": actor_policy_exact,
        "lineage_constraints": len(lineage_rows),
        "lineage_constraint_definitions_exact": (
            lineage_definitions == _LINEAGE_CONSTRAINTS
        ),
        "high_privilege_runtime_roles": privileged,
        "direct_auth_table_grants": direct_auth_grants,
        "unsafe_runtime_worker_privileges": unsafe_privileges,
        "fixture_registered_sources": source_counts["fixture"],
        "synthetic_canary_registered_sources": source_counts["synthetic_canary"],
        "sqlite_used": False,
    }


def _sbom() -> dict[str, object]:
    packages: list[dict[str, str]] = []
    lock = (Path(__file__).resolve().parents[2] / "requirements/f0d.lock").read_text(
        encoding="utf-8"
    )
    for line in lock.splitlines():
        if not line or line.startswith("#"):
            continue
        name, version = line.split("==", 1)
        metadata = importlib.metadata.metadata(name)
        license_value = (
            metadata.get("License-Expression")
            or metadata.get("License")
            or "UNKNOWN"
        )
        packages.append(
            {"name": name, "version": version, "license": license_value}
        )
    return {
        "schema": "f0d-sbom-v1",
        "scope": "PROJECT_VENV_AND_DATABASE_IMAGE",
        "packages": packages,
        "database_image": _POSTGRES_IMAGE,
        "external_runtime_providers": [],
    }


def write_acceptance_artifacts(
    service: PlatformService,
    *,
    tests_run: int,
    failures: int,
    errors: int,
    skipped: int,
    reverse: dict[str, int],
    smoke: dict[str, object],
    second_full_delta: dict[str, int],
) -> dict[str, object]:
    from .auth import authenticate_local_session
    from .bootstrap import LOCAL_TENANT_A_TOKEN

    context = authenticate_local_session(service.config, LOCAL_TENANT_A_TOKEN)
    stats = service.stats(context)
    readiness = service.readiness()
    database = _database_evidence(service.config)
    expected_stats = {
        "uploads": 26,
        "blobs": 26,
        "bytes": 41_878_200,
        "versions": 26,
        "plans": 26,
        "units": 249,
        "native": 225,
        "ocr": 24,
        "deferred": 2,
        "jobs_succeeded": 26,
        "audit_events": 52,
    }
    expected_reverse = {
        "valid_exit": 0,
        "tampered_exit": 2,
        "restored_exit": 0,
        "tenant_leaks": 0,
        "body_leaks": 0,
        "external_calls": 0,
        "ocr_calls": 0,
        "gate_bypasses": 0,
    }
    expected_smoke = {
        "schema": "f0d-replay-result-v1",
        "profile": "smoke",
        "selected_documents": 10,
        "uploads": 10,
        "blobs": 10,
        "bytes": 13_568_633,
        "versions": 10,
        "plans": 10,
        "units": 110,
        "native": 105,
        "ocr": 5,
        "deferred": 2,
        "jobs_succeeded": 10,
        "audit_events": 20,
        "relayed_this_run": 10,
        "processed_this_run": 10,
        "vault_objects": 10,
        "external_calls": 0,
        "ocr_calls": 0,
        "gold_promotions": 0,
        "professional_publications": 0,
    }
    smoke_evidence = {key: smoke.get(key) for key in expected_smoke}
    with tenant_transaction(
        service.config, "f0d_runtime", context
    ) as connection:
        objects = connection.execute(
            "SELECT object_key,sha256,size_bytes FROM f0d.object_blob ORDER BY id"
        ).fetchall()
    verified_objects = 0
    for record in objects:
        service.vault.verify(
            str(record["object_key"]),
            str(record["sha256"]),
            int(record["size_bytes"]),
        )
        verified_objects += 1
    expected_delta = {
        "uploads": 0,
        "blobs": 0,
        "bytes": 0,
        "versions": 0,
        "plans": 0,
        "units": 0,
        "jobs": 0,
    }
    if (
        stats != expected_stats
        or database.get("major") != 18
        or database.get("alembic_revision") != "f0d_0002"
        or database.get("high_privilege_runtime_roles") != 0
        or database.get("sqlite_used") is not False
        or database.get("force_rls_tables") != 14
        or database.get("force_rls_table_set_exact") is not True
        or database.get("session_bound_rls_policies") != 14
        or database.get("session_bound_rls_table_set_exact") is not True
        or database.get("actor_bound_write_policies") != 3
        or database.get("actor_bound_write_table_set_exact") is not True
        or database.get("lineage_constraints") != 5
        or database.get("lineage_constraint_definitions_exact") is not True
        or database.get("direct_auth_table_grants") != 0
        or database.get("unsafe_runtime_worker_privileges") != 0
        or database.get("fixture_registered_sources") != 26
        or database.get("synthetic_canary_registered_sources") != 0
        or readiness.get("gate_store_integrity") != "VALID"
        or tests_run < 157
        or failures != 0
        or errors != 0
        or skipped != 0
        or reverse != expected_reverse
        or smoke_evidence != expected_smoke
        or second_full_delta != expected_delta
        or service.vault.final_count() != 26
        or verified_objects != 26
    ):
        raise ArtifactError("ACCEPTANCE_EVIDENCE_MISMATCH")
    acceptance: dict[str, object] = {
        "schema": "f0d-upload-foundation-acceptance-v1",
        "stage": "F0-D",
        "status": "LOCAL_FIXTURE_FOUNDATION_ACCEPTED",
        "claim_scope": "LOCAL_FIXTURE_PIPELINE_ONLY",
        "not_authorized": [
            "REAL_CUSTOMER",
            "REGION_INDUSTRY_CONTEXT",
            "CUSTOMER_UAT",
            "PRODUCTION",
            "ACCEPTANCE_GOLD",
            "EXTERNAL_OCR_LLM",
            "PROFESSIONAL_PUBLICATION",
        ],
        "database": database,
        "readiness": readiness,
        "replay": {
            "smoke": {
                "documents": smoke["selected_documents"],
                "versions": smoke["versions"],
                "visual_units": smoke["units"],
                "native_candidates": smoke["native"],
                "ocr_candidates": smoke["ocr"],
                "deferred_conversion": smoke["deferred"],
            },
            "full": stats,
            "second_full_delta": second_full_delta,
            "vault_objects": service.vault.final_count(),
            "vault_objects_hash_verified": verified_objects,
        },
        "verification": {
            "tests_run": tests_run,
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
            "reverse": reverse,
            "tenant_leaks": 0,
            "body_leaks": 0,
            "external_calls": 0,
            "ocr_calls": 0,
            "gold_promotions": 0,
            "professional_publications": 0,
        },
        "frozen_inputs": _FROZEN,
        "raw_body_persisted_in_workspace": False,
        "object_state": "FIXTURE_STORED",
    }
    status_html = f"""<!doctype html>
<html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>F0-D Local Fixture Foundation</title><style>body{{font-family:system-ui,sans-serif;max-width:920px;margin:40px auto;padding:0 20px;color:#14213d;background:#f7f8fa}}section{{background:white;border:1px solid #d9dee8;border-radius:12px;padding:20px;margin:16px 0}}h1{{font-size:28px}}strong{{color:#165d3a}}.closed{{color:#8a3b12}}code{{background:#eef1f5;padding:2px 5px;border-radius:4px}}</style></head>
    <body><h1>F0-D 本地 Fixture 上传底座</h1><section><strong>LOCAL_FIXTURE_FOUNDATION_ACCEPTED</strong><p>仅证明本地 Fixture、session-bound PostgreSQL RLS、应用层 create-only/0600/使用前 hash 复验的本地 vault、幂等队列及 F0-C 证据挂接；不是 WORM。</p></section>
    <section><h2>聚合证据</h2><p>26 documents / 26 versions / 249 visual units / 225 native candidates / 24 OCR candidates / 2 deferred conversions / 41,878,200 bytes。</p><p>{tests_run} tests，{failures} failures，{errors} errors，{skipped} skipped；第二次 full 业务增量为 0。</p></section>
<section><h2>保持关闭</h2><p class=\"closed\">真实客户、地区行业确认、Acceptance Gold、外部 OCR/LLM、专业责任、客户 UAT 与生产均未获授权。</p></section>
<section><h2>调用边界</h2><p>External calls: 0；OCR calls: 0；Gold promotions: 0；Professional publications: 0。</p></section></body></html>\n""".encode(
        "utf-8"
    )
    _atomic_write("acceptance.json", _json_bytes(acceptance))
    _atomic_write("status.html", status_html)
    _atomic_write("sbom.json", _json_bytes(_sbom()))
    return acceptance


__all__ = ("ARTIFACT_ROOT", "ArtifactError", "write_acceptance_artifacts")
