from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from psycopg.types.json import Jsonb

from platform_foundation.auth import SessionContext, authenticate_local_session
from platform_foundation.bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
)
from platform_foundation.database import (
    DatabaseConfig,
    role_transaction,
    tenant_transaction,
)
from platform_foundation.f0e.contracts import (
    F0EError,
    OcrPageEvidence,
    OcrRunEnvelope,
)
from platform_foundation.f0e.hashing import stable_uuid4
from platform_foundation.f0e.service import LocalOcrService
from platform_foundation.service import JobLease
from platform_foundation.vault import LocalFixtureVault, VaultError


_ROOT = Path(__file__).resolve().parents[1]
_METRIC_ORDER = (
    "valid_exit",
    "tampered_exit",
    "restored_exit",
    "tenant_leaks",
    "page_crosswires",
    "stale_lease_writes",
    "external_calls",
    "body_leaks",
    "temp_residuals",
)
_EXPECTED = (0, 2, 0, 0, 0, 0, 0, 0, 0)
_F0E_TABLES = (
    "f0e.local_ocr_configuration",
    "f0e.local_ocr_run",
    "f0e.page_evidence_selection",
    "f0e.deferred_document_evidence",
)
_SNAPSHOT_TABLES = (
    "f0d.job",
    "f0e.local_ocr_run",
    "f0e.page_evidence_selection",
    "f0e.deferred_document_evidence",
    "f0d.audit_event",
)
_PROVIDER_MODULES = frozenset(
    {
        "anthropic",
        "boto3",
        "googleapiclient",
        "httpx",
        "openai",
        "requests",
        "socket",
        "socketio",
        "urllib",
        "urllib3",
    }
)
_PROVIDER_MARKERS = (
    b"api.anthropic.com",
    b"api.openai.com",
    b"azure.com/openai",
    b"generativelanguage.googleapis.com",
)


class _VerificationFailure(RuntimeError):
    pass


def _first_environment(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _config() -> DatabaseConfig:
    base = "127.0.0.1:55432/f0e_acceptance_v01"
    return DatabaseConfig(
        migration_dsn=_first_environment(
            ("F0E_MIGRATION_DSN", "F0D_MIGRATION_DSN"),
            "postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        ),
        runtime_dsn=_first_environment(
            ("F0E_RUNTIME_DSN", "F0D_RUNTIME_DSN"),
            "postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        ),
        worker_dsn=_first_environment(
            ("F0E_WORKER_DSN", "F0D_WORKER_DSN"),
            "postgresql://f0d_worker:f0d-worker-local-v01@" + base,
        ),
    )


def _empty_metrics() -> dict[str, int]:
    return {
        "valid_exit": 2,
        "tampered_exit": 0,
        "restored_exit": 2,
        "tenant_leaks": 1,
        "page_crosswires": 1,
        "stale_lease_writes": 1,
        "external_calls": 1,
        "body_leaks": 0,
        "temp_residuals": 1,
    }


def _vault_reverse(canary: bytes, canary_text: str) -> tuple[int, int, int, int]:
    valid_exit = 2
    tampered_exit = 0
    restored_exit = 2
    body_leaks = 0
    with tempfile.TemporaryDirectory(prefix="f0e-reverse-", dir="/private/tmp") as root:
        with LocalFixtureVault(root) as vault:
            stored = vault.store_bytes(canary)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
                valid_exit = 0
            except VaultError as error:
                body_leaks += int(canary_text in str(error))

            final = Path(root) / "final" / stored.object_id
            final.write_bytes(b"X" * stored.size)
            os.chmod(final, 0o600)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
            except VaultError as error:
                tampered_exit = 2
                body_leaks += int(canary_text in str(error))

            final.write_bytes(canary)
            os.chmod(final, 0o600)
            try:
                vault.verify(stored.object_id, stored.sha256, stored.size)
                restored_exit = 0
            except VaultError as error:
                body_leaks += int(canary_text in str(error))
    return valid_exit, tampered_exit, restored_exit, body_leaks


def _tenant_leaks(
    config: DatabaseConfig, context_a: SessionContext, context_b: SessionContext
) -> int:
    leaks = 0
    tables = _F0E_TABLES + ("f0d.job", "f0d.audit_event")
    for context, foreign_enterprise in (
        (context_a, context_b.enterprise_id),
        (context_b, context_a.enterprise_id),
    ):
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            for table in tables:
                row = connection.execute(
                    f"SELECT count(*) AS count FROM {table} WHERE enterprise_id=%s",
                    (foreign_enterprise,),
                ).fetchone()
                leaks += int(row["count"] if row is not None else 1)

    forged = (
        SessionContext(
            context_b.enterprise_id,
            context_a.actor_id,
            context_a.session_token_sha256,
        ),
        SessionContext(
            context_b.enterprise_id,
            context_b.actor_id,
            context_a.session_token_sha256,
        ),
    )
    for context in forged:
        try:
            with tenant_transaction(config, "f0d_runtime", context):
                leaks += 1
        except Exception:
            pass
    return leaks


def _snapshot(config: DatabaseConfig, context: SessionContext) -> str:
    material: list[object] = []
    with tenant_transaction(config, "f0d_runtime", context) as connection:
        for table in _SNAPSHOT_TABLES:
            if table == "f0d.job":
                predicate = " WHERE kind='EXECUTE_LOCAL_OCR'"
            elif table == "f0d.audit_event":
                predicate = " WHERE event_code='LOCAL_OCR_EVIDENCE_FINALIZED'"
            else:
                predicate = ""
            rows = connection.execute(
                f"SELECT to_jsonb(item) AS payload FROM {table} AS item"
                f"{predicate} ORDER BY id"
            ).fetchall()
            material.append(
                {
                    "table": table,
                    "rows": [row["payload"] for row in rows],
                }
            )
    encoded = json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _page_evidence(row: dict[str, object]) -> OcrPageEvidence:
    def digest(name: str) -> str | None:
        value = row[name]
        return None if value is None else str(value).strip()

    return OcrPageEvidence(
        evidence_id=row["id"],
        processing_unit_id=row["processing_unit_id"],
        source_unit_id=str(row["source_unit_id"]).strip(),
        candidate_decision=str(row["candidate_decision"]),
        selected_route=str(row["selected_route"]),
        terminal_status=str(row["terminal_status"]),
        source_evidence_sha256=str(row["source_evidence_sha256"]).strip(),
        render_sha256=digest("render_sha256"),
        output_sha256=digest("output_sha256"),
        output_block_count=row["output_block_count"],
        output_character_count=int(row["output_character_count"]),
        output_non_blank_characters=int(
            row["output_non_blank_character_count"]
        ),
        mean_confidence_ppm=row["mean_confidence_ppm"],
        bbox_summary_sha256=digest("bbox_summary_sha256"),
        reason_code=str(row["reason_code"]),
        execution_profile_sha256=str(row["execution_profile_sha256"]).strip(),
    )


def _successful_envelopes(
    config: DatabaseConfig, context: SessionContext
) -> tuple[tuple[JobLease, OcrRunEnvelope], tuple[JobLease, OcrRunEnvelope]]:
    with tenant_transaction(config, "f0d_worker", context) as connection:
        runs = connection.execute(
            "SELECT r.id AS run_id,r.processing_plan_id,"
            "r.local_ocr_configuration_id,r.terminal_status,"
            "j.id AS job_id,j.lease_generation,j.lease_token,j.input_version,"
            "c.execution_profile_sha256 "
            "FROM f0e.local_ocr_run r JOIN f0d.job j "
            "ON j.enterprise_id=r.enterprise_id AND j.id=r.job_id "
            "JOIN f0e.local_ocr_configuration c "
            "ON c.enterprise_id=r.enterprise_id "
            "AND c.id=r.local_ocr_configuration_id "
            "AND c.configuration_sha256=r.local_ocr_configuration_sha256 "
            "WHERE r.terminal_status='CANDIDATE_EVIDENCE_RECORDED' "
            "AND j.kind='EXECUTE_LOCAL_OCR' AND j.status='SUCCEEDED' "
            "ORDER BY r.processing_plan_id LIMIT 2"
        ).fetchall()
        if len(runs) != 2:
            raise _VerificationFailure()

        output: list[tuple[JobLease, OcrRunEnvelope]] = []
        for run in runs:
            pages = connection.execute(
                "SELECT p.*,c.execution_profile_sha256 "
                "FROM f0e.page_evidence_selection p "
                "JOIN f0e.local_ocr_configuration c "
                "ON c.enterprise_id=p.enterprise_id "
                "AND c.id=p.local_ocr_configuration_id "
                "AND c.configuration_sha256=p.local_ocr_configuration_sha256 "
                "WHERE p.local_ocr_run_id=%s ORDER BY p.unit_ordinal",
                (run["run_id"],),
            ).fetchall()
            if not pages or run["lease_token"] is None:
                raise _VerificationFailure()
            lease = JobLease(
                job_id=run["job_id"],
                generation=int(run["lease_generation"]),
                token=run["lease_token"],
                worker_id="reverse_verify",
            )
            envelope = OcrRunEnvelope(
                run_id=run["run_id"],
                processing_plan_id=run["processing_plan_id"],
                configuration_id=run["local_ocr_configuration_id"],
                input_version=str(run["input_version"]),
                status=str(run["terminal_status"]),
                page_evidence=tuple(_page_evidence(dict(page)) for page in pages),
            )
            output.append((lease, envelope))
    return output[0], output[1]


def _replay_and_crosswire(
    config: DatabaseConfig,
    context: SessionContext,
    canary_text: str,
) -> tuple[int, int]:
    body_leaks = 0
    (lease, envelope), (_, foreign_envelope) = _successful_envelopes(config, context)
    service = LocalOcrService(config)

    before_exact = _snapshot(config, context)
    try:
        exact_id = service.finalize(context, lease, envelope)
    except Exception as error:
        body_leaks += int(canary_text in str(error))
        return 1, body_leaks
    after_exact = _snapshot(config, context)
    if exact_id != envelope.run_id or after_exact != before_exact:
        return 1, body_leaks

    crosswired_pages = list(envelope.page_evidence)
    crosswired_pages[0] = foreign_envelope.page_evidence[0]
    payload = [item.to_finalize_payload() for item in crosswired_pages]
    audit_id = stable_uuid4(
        "audit", "LOCAL_OCR_EVIDENCE_FINALIZED", envelope.run_id
    )
    before_tamper = _snapshot(config, context)
    rejected = False
    try:
        with tenant_transaction(config, "f0d_worker", context) as connection:
            connection.execute(
                "SELECT f0e.finalize_local_ocr_run(%s,%s,%s,%s,%s,%s,%s)",
                (
                    lease.job_id,
                    lease.generation,
                    lease.token,
                    envelope.run_id,
                    audit_id,
                    Jsonb(payload),
                    None,
                ),
            ).fetchone()
    except Exception as error:
        rejected = True
        body_leaks += int(canary_text in str(error))
    after_tamper = _snapshot(config, context)
    page_crosswires = int(not rejected or after_tamper != before_tamper)
    return page_crosswires, body_leaks


def _stale_lease_writes(
    config: DatabaseConfig,
    context: SessionContext,
    canary_text: str,
) -> tuple[int, int]:
    body_leaks = 0
    (lease, envelope), _ = _successful_envelopes(config, context)
    token = uuid.uuid4()
    while token == lease.token:
        token = uuid.uuid4()
    stale = JobLease(
        job_id=lease.job_id,
        generation=lease.generation,
        token=token,
        worker_id=lease.worker_id,
    )
    before = _snapshot(config, context)
    rejected = False
    try:
        LocalOcrService(config).finalize(context, stale, envelope)
    except F0EError as error:
        rejected = error.code == "JOB_LEASE_STALE"
        body_leaks += int(canary_text in str(error))
    except Exception as error:
        body_leaks += int(canary_text in str(error))
    after = _snapshot(config, context)
    return int(not rejected or after != before), body_leaks


def _static_hygiene(canary: bytes) -> tuple[int, int]:
    body_leaks = 0
    external_calls = 0
    files = tuple((_ROOT / "src/platform_foundation/f0e").glob("*.py")) + (
        _ROOT / "infra/f0e/runner.py",
    )
    for path in files:
        source = path.read_bytes()
        body_leaks += int(canary in source)
        external_calls += int(any(marker in source for marker in _PROVIDER_MARKERS))
        if path.suffix != ".py":
            continue
        tree = ast.parse(source.decode("utf-8", errors="strict"))
        for node in ast.walk(tree):
            names: tuple[str, ...]
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = (node.module,)
            else:
                continue
            external_calls += sum(
                name.split(".", 1)[0] in _PROVIDER_MODULES for name in names
            )
    return body_leaks, external_calls


def _database_hygiene(
    config: DatabaseConfig,
    contexts: tuple[SessionContext, SessionContext],
    canary_text: str,
) -> tuple[int, int]:
    body_leaks = 0
    external_calls = 0
    forbidden_columns = (
        "body",
        "content",
        "dsn",
        "page_image",
        "path",
        "raw_text",
        "recognized_text",
        "source_path",
        "text",
    )
    with role_transaction(config, "f0d_migration") as connection:
        row = connection.execute(
            "SELECT count(*) AS count FROM information_schema.columns "
            "WHERE table_schema='f0e' AND lower(column_name)=ANY(%s)",
            (list(forbidden_columns),),
        ).fetchone()
        body_leaks += int(row["count"] if row is not None else 1)

    scan_tables = _F0E_TABLES + ("f0d.job", "f0d.audit_event")
    for context in contexts:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            for table in scan_tables:
                row = connection.execute(
                    f"SELECT count(*) AS count FROM {table} AS item "
                    "WHERE position(%s in to_jsonb(item)::text)>0",
                    (canary_text,),
                ).fetchone()
                body_leaks += int(row["count"] if row is not None else 1)
            for table in _F0E_TABLES:
                row = connection.execute(
                    f"SELECT count(*) AS count FROM {table} "
                    "WHERE external_processing_policy<>'DENY'",
                ).fetchone()
                external_calls += int(row["count"] if row is not None else 1)
            row = connection.execute(
                "SELECT count(*) AS count FROM f0d.audit_event "
                "WHERE event_code LIKE '%EXTERNAL%' "
                "OR event_code LIKE '%PROVIDER%' OR event_code LIKE '%LLM%'"
            ).fetchone()
            external_calls += int(row["count"] if row is not None else 1)

    with role_transaction(config, "f0d_runtime") as connection:
        row = connection.execute(
            "SELECT count(*) AS count FROM f0d.capability_gate "
            "WHERE code='EXTERNAL_PROCESSING' AND status<>'CLOSED'"
        ).fetchone()
        external_calls += int(row["count"] if row is not None else 1)
    return body_leaks, external_calls


def _temp_residuals() -> int:
    residuals = sum(
        1 for _ in Path("/private/tmp").glob("f0e-reverse-*")
    )
    docker = "/usr/local/bin/docker"
    try:
        result = subprocess.run(
            (
                docker,
                "ps",
                "-a",
                "--filter",
                "name=anhuan-f0e-",
                "--format",
                "{{.ID}}",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            cwd="/private/tmp",
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"},
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return residuals + 1
    if result.returncode != 0:
        return residuals + 1
    return residuals + len(result.stdout.splitlines())


def _evaluate(canary: bytes, canary_text: str) -> dict[str, int]:
    metrics = _empty_metrics()
    valid, tampered, restored, vault_body_leaks = _vault_reverse(
        canary, canary_text
    )
    metrics["valid_exit"] = valid
    metrics["tampered_exit"] = tampered
    metrics["restored_exit"] = restored
    metrics["body_leaks"] = vault_body_leaks

    config = _config()
    context_a = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    context_b = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
    metrics["tenant_leaks"] = _tenant_leaks(config, context_a, context_b)

    page_crosswires, crosswire_body_leaks = _replay_and_crosswire(
        config, context_a, canary_text
    )
    metrics["page_crosswires"] = page_crosswires
    metrics["body_leaks"] += crosswire_body_leaks

    stale_writes, stale_body_leaks = _stale_lease_writes(
        config, context_a, canary_text
    )
    metrics["stale_lease_writes"] = stale_writes
    metrics["body_leaks"] += stale_body_leaks

    static_body, static_external = _static_hygiene(canary)
    database_body, database_external = _database_hygiene(
        config, (context_a, context_b), canary_text
    )
    metrics["body_leaks"] += static_body + database_body
    metrics["external_calls"] = static_external + database_external
    metrics["temp_residuals"] = _temp_residuals()
    return metrics


def main() -> int:
    canary_text = "F0E_BODY_" + uuid.uuid4().hex
    canary = canary_text.encode("ascii")
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            metrics = _evaluate(canary, canary_text)
    except BaseException as error:
        metrics = _empty_metrics()
        metrics["body_leaks"] = int(canary_text in str(error))

    captured = stdout_buffer.getvalue() + stderr_buffer.getvalue()
    metrics["body_leaks"] += int(canary_text in captured)
    for key in _METRIC_ORDER:
        print(f"{key}={metrics[key]}")
    observed = tuple(metrics[key] for key in _METRIC_ORDER)
    return 0 if observed == _EXPECTED else 2


if __name__ == "__main__":
    raise SystemExit(main())
