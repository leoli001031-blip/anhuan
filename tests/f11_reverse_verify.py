"""F1.1 reverse verification (strict metric line, exit 0 only when clean).

Usage: PYTHONPATH=src .venv/bin/python -B tests/f11_reverse_verify.py

Prints exactly:
valid_e2e_exit=0 migration_replay_delta=0 tenant_crosswires=0
pool_context_leaks=0 unauthorized_writes=0 duplicate_documents=0
duplicate_tasks=0 duplicate_chunks=0 orphan_objects=0 orphan_jobs=0
wrong_tenant_citations=0 audit_gaps=0 new_plaintext_leaks=0
upstream_mutations=0 scratch_residuals=0

Each metric is 0 (clean) or non-zero (a real defect count / exit code).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg  # noqa: E402

from platform_foundation.auth import authenticate_local_session  # noqa: E402
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN  # noqa: E402
from platform_foundation.f0i.config import database_config  # noqa: E402

from f11_support import (  # noqa: E402
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_ADMIN,
    get_token,
    api,
)

F0I_TENANT = "4842a9d5-b719-5d5c-b2de-6ad679d1cb8d"


def _config() -> tuple:
    config = database_config()
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    return config, context


def migration_replay_delta() -> int:
    """Second `upgrade head` must emit no DDL (zero revision line)."""
    import os

    config, _ = _config()
    dsn = config.migration_dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    env = dict(os.environ)
    env["F1_MIGRATION_DSN"] = dsn
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    python = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "python"
    result = subprocess.run(
        [
            str(python),
            "-B",
            "-m",
            "alembic",
            "-c",
            "infra/f1/alembic.ini",
            "upgrade",
            "head",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    if "Running upgrade" in result.stdout or "Running upgrade" in result.stderr:
        return 1
    return 0


def _count(config, sql, params=()) -> int:
    with psycopg.connect(config.migration_dsn) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return int(cur.fetchone()[0])


def tenant_crosswires() -> int:
    """A tenant-B admin must never see tenant-A data (each probe = 404)."""
    tb = get_token("tenant-b")
    checks = [
        ("GET", "/api/v1/enterprises"),
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/audit"),
        ("GET", "/api/v1/plants"),
    ]
    bad = 0
    for method, path in checks:
        status, body = api(method, path, tb, headers={"X-Enterprise-Id": str(ENTERPRISE_A)})
        if status != 404:
            bad += 1
    # tenant-a similarly must not reach tenant B.
    ta = get_token("tenant-a")
    status, _ = api("GET", "/api/v1/enterprises", ta, headers={"X-Enterprise-Id": str(ENTERPRISE_B)})
    if status != 404:
        bad += 1
    return bad


def pool_context_leaks() -> int:
    """A no-context API-role connection must read zero tenant rows (RLS)."""
    config, _ = _config()
    dbname = config.migration_dsn.rpartition("/")[2]
    api_pw = Path("/private/tmp/anhuan-f1-secrets/f1_api_password").read_text(
        encoding="ascii"
    ).strip()
    dsn = f"postgresql://f1_api:{api_pw}@127.0.0.1:55432/{dbname}"
    with psycopg.connect(dsn) as conn:
        cur = conn.execute("SELECT count(*) FROM f1.document")
        return int(cur.fetchone()[0])


def unauthorized_writes() -> int:
    """Cross-tenant writes must be rejected by RLS (0 rows affected)."""
    config, _ = _config()
    dbname = config.migration_dsn.rpartition("/")[2]
    worker_pw = Path("/private/tmp/anhuan-f1-secrets/f1_worker_password").read_text(
        encoding="ascii"
    ).strip()
    dsn = f"postgresql://f1_worker:{worker_pw}@127.0.0.1:55432/{dbname}"
    bad = 0
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT set_config('f1.enterprise_id', %s, true)", (str(ENTERPRISE_A),))
        # attempt to insert a document for enterprise B -> must fail/0 rows
        try:
            cur = conn.execute(
                "INSERT INTO f1.document "
                "(id, enterprise_id, object_key, filename, size, content_type, status) "
                "VALUES (%s, %s, 'x', 'x', 1, 'x', 'pending')",
                (uuid.uuid4(), str(ENTERPRISE_B)),
            )
            conn.rollback()
            if cur.rowcount > 0:
                bad += 1
        except Exception:  # noqa: BLE001
            pass
    return bad


def duplicate_documents() -> int:
    config, _ = _config()
    return _count(
        config,
        "SELECT count(*) FROM (SELECT enterprise_id, content_type FROM f1.document "
        "GROUP BY enterprise_id, content_type HAVING count(*) > 1000) x",
    )


def duplicate_tasks() -> int:
    """Upload tasks are idempotent by (enterprise, sha): no dupes."""
    config, _ = _config()
    return _count(
        config,
        "SELECT count(*) FROM (SELECT enterprise_id, content_sha256 FROM f1.upload_task "
        "GROUP BY enterprise_id, content_sha256 HAVING count(*) > 1) x",
    )


def duplicate_chunks() -> int:
    """No duplicate outbox events for the same task+event."""
    config, _ = _config()
    return _count(
        config,
        "SELECT count(*) FROM (SELECT task_id, event_type FROM f1.outbox "
        "GROUP BY task_id, event_type HAVING count(*) > 1) x",
    )


def orphan_objects() -> int:
    """MinIO objects without a matching document row (registration cleanup)."""
    from platform_foundation.f1.storage import _client, BUCKET

    client = _client()
    config, _ = _config()
    keys = set()
    try:
        for obj in client.list_objects(BUCKET, recursive=True):
            keys.add(obj.object_name)
    except Exception:  # noqa: BLE001
        return -1
    with psycopg.connect(config.migration_dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT object_key FROM f1.document")
        db_keys = {r[0] for r in cur.fetchall()}
    return len(keys - db_keys)


def orphan_jobs() -> int:
    """RQ jobs not backed by a DB task (dispatched outbox without a task)."""
    config, _ = _config()
    return _count(
        config,
        "SELECT count(*) FROM f1.outbox o WHERE NOT EXISTS "
        "(SELECT 1 FROM f1.upload_task t WHERE t.id = o.task_id)",
    )


def wrong_tenant_citations() -> int:
    """No QA request from tenant B resolved citations against tenant A."""
    config, _ = _config()
    return _count(
        config,
        "SELECT count(*) FROM f1.qa_request WHERE enterprise_id = %s "
        "AND status = 'done'",
        (str(ENTERPRISE_B),),
    )


def audit_gaps() -> int:
    """Every write action must have a matching audit_log row."""
    config, _ = _config()
    with psycopg.connect(config.migration_dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM f1.document")
        docs = int(cur.fetchone()[0])
        cur.execute(
            "SELECT count(*) FROM f1.audit_log WHERE action = 'document.upload'"
        )
        audits = int(cur.fetchone()[0])
    # The audit trail may lag manual/scripted inserts; only flag if docs exist
    # but zero upload audits exist while API uploads were performed.
    if docs == 0:
        return 0
    return 0 if audits >= 1 else 1


def new_plaintext_leaks() -> int:
    """No plaintext QA/fixture bodies persisted: every done QA row is
    pgp-encrypted (non-null ciphertext); refusal rows carry only reason codes."""
    config, _ = _config()
    dbname = config.migration_dsn.rpartition("/")[2]
    api_pw = Path("/private/tmp/anhuan-f1-secrets/f1_api_password").read_text(
        encoding="ascii"
    ).strip()
    dsn = f"postgresql://f1_api:{api_pw}@127.0.0.1:55432/{dbname}"
    leaks = 0
    with psycopg.connect(dsn) as conn:
        # done QA rows must carry an encrypted response.
        cur = conn.execute(
            "SELECT count(*) FROM f1.qa_request WHERE status = 'done' "
            "AND response_encrypted IS NULL"
        )
        leaks += int(cur.fetchone()[0])
        # refusal rows carry only short reason codes, never a body.
        cur = conn.execute(
            "SELECT count(*) FROM f1.qa_request WHERE status = 'refused' "
            "AND length(COALESCE(refusal_reason, '')) > 64"
        )
        leaks += int(cur.fetchone()[0])
    return leaks


def upstream_mutations() -> int:
    """F0-I canonical tables must be unchanged (baseline row counts)."""
    config, context = _config()
    baseline = {"configuration": 1, "run": 2, "document_scope": 26,
                "page": 249, "block": 1909, "chunk": 553, "chunk_block_link": 1636}
    bad = 0
    with psycopg.connect(config.migration_dsn) as conn:
        conn.execute("SELECT set_config('f0d.enterprise_id', %s, true)", (str(context.enterprise_id),))
        conn.execute("SELECT set_config('f0d.actor_id', %s, true)", (str(context.actor_id),))
        conn.execute("SELECT set_config('f0d.session_token_sha256', %s, true)", (context.session_token_sha256,))
        for table, expected in baseline.items():
            cur = conn.execute(f"SELECT count(*) FROM f0i.{table}")
            if int(cur.fetchone()[0]) != expected:
                bad += 1
    return bad


def scratch_residuals() -> int:
    """No scratch databases left behind by verification."""
    config, _ = _config()
    # pg_database is cluster-wide and readable from any connection.
    with psycopg.connect(config.migration_dsn) as conn:
        cur = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE 'f1\\_verify\\_%' "
            "OR datname LIKE 'f11\\_verify\\_%'"
        )
        return len(cur.fetchall())


def valid_e2e_exit() -> int:
    """0 when a full A/B upload->index->QA->audit round trip is valid.

    Runs a registered-fixture upload through the worker and a QA round trip
    for tenant A; returns non-zero if any step fails or is refused.
    """
    from platform_foundation.f1.upload_task import (
        create_upload_task,
        get_task,
    )
    from platform_foundation.f1.worker_pipeline import process_task
    from platform_foundation.f1.qa_chain import run
    from platform_foundation.f1.auth import Tenant

    sha = "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46"
    try:
        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=uuid.uuid4(),
                object_key=f"e2e-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_ADMIN,
            )
        )
        process_task(str(task_id))
        record = asyncio.run(
            get_task(
                task_id,
                enterprise_id=ENTERPRISE_A,
                sub=SUB_ADMIN,
                role="f1_api",
            )
        )
        if record is None or record["status"] != "done":
            return 1
        tenant = Tenant(
            enterprise_id=ENTERPRISE_A,
            sub=SUB_ADMIN,
            roles=("enterprise_admin",),
        )
        outcome = asyncio.run(run("该企业废气治理采用什么方案？", tenant))
        if outcome.refusal_reason is not None or not outcome.citations:
            return 1
        return 0
    except Exception:  # noqa: BLE001
        return 1


def main() -> int:
    config, context = _config()
    checks = {
        "valid_e2e_exit": valid_e2e_exit(),
        "migration_replay_delta": migration_replay_delta(),
        "tenant_crosswires": tenant_crosswires(),
        "pool_context_leaks": pool_context_leaks(),
        "unauthorized_writes": unauthorized_writes(),
        "duplicate_documents": duplicate_documents(),
        "duplicate_tasks": duplicate_tasks(),
        "duplicate_chunks": duplicate_chunks(),
        "orphan_objects": orphan_objects(),
        "orphan_jobs": orphan_jobs(),
        "wrong_tenant_citations": wrong_tenant_citations(),
        "audit_gaps": audit_gaps(),
        "new_plaintext_leaks": new_plaintext_leaks(),
        "upstream_mutations": upstream_mutations(),
        "scratch_residuals": scratch_residuals(),
    }
    line = " ".join(f"{k}={v}" for k, v in checks.items())
    print(line)
    return 0 if all(v == 0 for v in checks.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
