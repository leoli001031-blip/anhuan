from __future__ import annotations

import base64
import os
from pathlib import Path
import re
import uuid

import psycopg

from platform_foundation.auth import SessionContext, authenticate_local_session
from platform_foundation.bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
)
from platform_foundation.database import DatabaseConfig, tenant_transaction
from platform_foundation.f0f import F0FError, create_keyfile, load_keyfile
from platform_foundation.f0f.acceptance import ACCEPTANCE_KEY_FILE
from platform_foundation.f0f.service import ControlledBodyService


_ROOT = Path(__file__).resolve().parents[1]
_ORDER = (
    "valid_exit",
    "tampered_exit",
    "restored_exit",
    "wrong_key_reads",
    "tenant_leaks",
    "page_crosswires",
    "gold_false_promotions",
    "plaintext_or_key_leaks",
    "external_calls",
)


def _first_environment(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _config() -> DatabaseConfig:
    base = "127.0.0.1:55432/f0f_acceptance_v01"
    return DatabaseConfig(
        migration_dsn=_first_environment(
            ("F0F_MIGRATION_DSN", "F0D_MIGRATION_DSN"),
            "postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        ),
        runtime_dsn=_first_environment(
            ("F0F_RUNTIME_DSN", "F0D_RUNTIME_DSN"),
            "postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        ),
        worker_dsn=_first_environment(
            ("F0F_WORKER_DSN", "F0D_WORKER_DSN"),
            "postgresql://f0d_worker:f0d-worker-local-v01@" + base,
        ),
    )


def _empty_metrics() -> dict[str, int]:
    return {
        "valid_exit": 2,
        "tampered_exit": 0,
        "restored_exit": 2,
        "wrong_key_reads": 1,
        "tenant_leaks": 1,
        "page_crosswires": 1,
        "gold_false_promotions": 1,
        "plaintext_or_key_leaks": 1,
        "external_calls": 1,
    }


def _rows(config: DatabaseConfig, context: SessionContext) -> list[dict[str, object]]:
    with tenant_transaction(config, "f0d_runtime", context) as connection:
        return connection.execute(
            "SELECT id,plaintext_sha256,plaintext_size_bytes "
            "FROM f0f.page_body_evidence ORDER BY processing_unit_id"
        ).fetchall()


def _valid_decryptions(
    service: ControlledBodyService,
    context: SessionContext,
    rows: list[dict[str, object]],
    key: object,
) -> tuple[int, list[bytes]]:
    bodies: list[bytes] = []
    try:
        for row in rows:
            body = service.decrypt_verified(context, row["id"], key)  # type: ignore[arg-type]
            try:
                if (
                    body.sha256 != str(row["plaintext_sha256"]).strip()
                    or body.byte_count != int(row["plaintext_size_bytes"])
                ):
                    return 2, bodies
                value = bytes(body.view())
                if len(value) >= 20:
                    bodies.append(value)
            finally:
                body.wipe()
        return 0, bodies
    except Exception:
        return 2, bodies


def _tamper_and_restore(
    config: DatabaseConfig,
    context: SessionContext,
    service: ControlledBodyService,
    body_id: uuid.UUID,
    key: object,
) -> tuple[int, int]:
    material = bytearray(key.view())  # type: ignore[attr-defined]
    tampered = 0
    connection = psycopg.connect(config.migration_dsn)
    try:
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('f0d.enterprise_id',%s,true),"
                    "set_config('f0d.actor_id',%s,true),"
                    "set_config('f0d.session_token_sha256',%s,true)",
                    (
                        str(context.enterprise_id),
                        str(context.actor_id),
                        context.session_token_sha256,
                    ),
                )
                connection.execute(
                    "ALTER TABLE f0f.page_body_evidence DISABLE ROW LEVEL SECURITY"
                )
                connection.execute(
                    "ALTER TABLE f0f.page_body_evidence DISABLE TRIGGER "
                    "reject_immutable_row_mutation"
                )
                connection.execute(
                    "UPDATE f0f.page_body_evidence SET "
                    "ciphertext=set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%256),"
                    "ciphertext_sha256=encode(f0f_crypto.digest("
                    "set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%256),"
                    "'sha256'),'hex')::char(64) WHERE id=%s",
                    (body_id,),
                )
                connection.execute(
                    "SELECT f0f.decrypt_verified_body(%s,%s)",
                    (body_id, material),
                )
        except psycopg.Error:
            tampered = 2
    finally:
        connection.close()
        material[:] = b"\0" * len(material)
        material.clear()

    restored = 2
    try:
        body = service.decrypt_verified(context, body_id, key)  # type: ignore[arg-type]
        body.wipe()
        restored = 0
    except Exception:
        pass
    return tampered, restored


def _wrong_key_reads(
    service: ControlledBodyService,
    context: SessionContext,
    body_id: uuid.UUID,
) -> int:
    path = "/private/tmp/anhuan-f0f-reverse-" + uuid.uuid4().hex + ".key"
    reads = 0
    create_keyfile(path)
    try:
        with load_keyfile(path) as wrong:
            try:
                body = service.decrypt_verified(context, body_id, wrong)
            except F0FError:
                pass
            else:
                body.wipe()
                reads += 1
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    return reads


def _tenant_leaks(
    config: DatabaseConfig,
    context_a: SessionContext,
    context_b: SessionContext,
    service: ControlledBodyService,
    body_id: uuid.UUID,
    key: object,
) -> int:
    leaks = 0
    tables = (
        "f0f.body_configuration",
        "f0f.page_body_evidence",
        "f0f.gold_annotation_queue",
        "f0f.gold_label_evidence",
        "f0f.gold_adjudication",
    )
    with tenant_transaction(config, "f0d_runtime", context_b) as connection:
        for table in tables:
            leaks += int(
                connection.execute(f"SELECT count(*) AS count FROM {table}").fetchone()[
                    "count"
                ]
            )
    try:
        body = service.decrypt_verified(context_b, body_id, key)  # type: ignore[arg-type]
    except F0FError:
        pass
    else:
        body.wipe()
        leaks += 1
    forged = SessionContext(
        context_b.enterprise_id,
        context_a.actor_id,
        context_a.session_token_sha256,
    )
    try:
        with tenant_transaction(config, "f0d_runtime", forged):
            leaks += 1
    except Exception:
        pass
    return leaks


def _page_crosswires(config: DatabaseConfig, context: SessionContext) -> int:
    with tenant_transaction(config, "f0d_runtime", context) as connection:
        row = connection.execute(
            "SELECT "
            "(SELECT count(*)-count(DISTINCT processing_unit_id) "
            "FROM f0f.page_body_evidence) + "
            "(SELECT count(*)-count(DISTINCT page_evidence_id) "
            "FROM f0f.page_body_evidence) + "
            "(SELECT count(*) FROM f0f.page_body_evidence b LEFT JOIN "
            "f0e.page_evidence_selection e ON e.enterprise_id=b.enterprise_id "
            "AND e.id=b.page_evidence_id AND e.local_ocr_run_id=b.local_ocr_run_id "
            "AND e.processing_plan_id=b.processing_plan_id "
            "AND e.document_version_id=b.document_version_id "
            "AND e.source_document_id=b.source_document_id "
            "AND e.source_plan_sha256=b.source_plan_sha256 "
            "AND e.local_ocr_configuration_id=b.local_ocr_configuration_id "
            "AND e.local_ocr_configuration_sha256=b.local_ocr_configuration_sha256 "
            "AND e.processing_unit_id=b.processing_unit_id "
            "AND e.source_unit_id=b.source_unit_id "
            "AND e.unit_ordinal=b.unit_ordinal "
            "AND e.selected_route=b.selected_route "
            "AND e.output_sha256=b.source_output_sha256 "
            "AND e.evidence_chain_sha256=b.source_page_evidence_sha256 "
            "WHERE e.id IS NULL) + "
            "abs((SELECT count(*) FROM f0f.page_body_evidence)-"
            "(SELECT count(*) FROM f0e.page_evidence_selection)) AS violations"
        ).fetchone()
    return int(row["violations"])


def _gold_false_promotions(config: DatabaseConfig, context: SessionContext) -> int:
    with tenant_transaction(config, "f0d_runtime", context) as connection:
        row = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM f0f.gold_label_evidence) + "
            "(SELECT count(*) FROM f0f.gold_adjudication) + "
            "(SELECT count(*) FROM f0f.gold_annotation_queue WHERE "
            "queue_status<>'ANNOTATION_REQUIRED' OR benchmark_tier<>'NONE' "
            "OR acceptance_gold OR production_allowed) + "
            "(SELECT count(*) FROM f0f.gold_annotation_queue q LEFT JOIN "
            "f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
            "AND b.id=q.page_body_evidence_id "
            "AND b.processing_unit_id=q.processing_unit_id "
            "AND b.body_evidence_chain_sha256=q.body_evidence_chain_sha256 "
            "WHERE b.id IS NULL) AS violations"
        ).fetchone()
    return int(row["violations"])


def _scan_targets() -> tuple[Path, ...]:
    targets = [
        _ROOT / "infra/f0f",
        _ROOT / "src/platform_foundation/f0f",
        _ROOT / "migrations/versions/f0d_0004_controlled_body_evidence.py",
        _ROOT / "tests/test_f0f_controlled_body_gold.py",
        _ROOT / "tests/f0f_reverse_verify.py",
        _ROOT / "artifacts/f0f-controlled-body/v0.1",
        _ROOT / "PROGRESS.md",
        _ROOT / "BLOCKED.md",
    ]
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            files.extend(
                path
                for path in target.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            )
    return tuple(sorted(set(files)))


def _plaintext_or_key_leaks(key: object, bodies: list[bytes]) -> int:
    key_bytes = bytes(key.view())  # type: ignore[attr-defined]
    needles = (
        key_bytes,
        key_bytes.hex().encode("ascii"),
        base64.b64encode(key_bytes),
        *bodies,
    )
    leaks = 0
    for path in _scan_targets():
        try:
            payload = path.read_bytes()
        except OSError:
            leaks += 1
            continue
        leaks += sum(int(needle in payload) for needle in needles if needle)
    return leaks


def _external_calls() -> int:
    forbidden = re.compile(
        rb"(?:^|\s)(?:from|import)\s+(?:anthropic|boto3|httpx|openai|requests|socket|urllib)(?:\s|\.|$)",
        re.MULTILINE,
    )
    violations = 0
    for root in (_ROOT / "infra/f0f", _ROOT / "src/platform_foundation/f0f"):
        for path in root.rglob("*.py"):
            violations += int(forbidden.search(path.read_bytes()) is not None)
    compose = (_ROOT / "infra/f0f/compose.yaml").read_bytes()
    violations += int(b"network_mode: none" not in compose)
    violations += int(b"pull_policy: never" not in compose)
    return violations


def main() -> int:
    metrics = _empty_metrics()
    try:
        config = _config()
        context_a = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
        context_b = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
        service = ControlledBodyService(config)
        rows = _rows(config, context_a)
        if not rows:
            raise RuntimeError("verification target is empty")
        with load_keyfile(ACCEPTANCE_KEY_FILE) as key:
            metrics["valid_exit"], bodies = _valid_decryptions(
                service, context_a, rows, key
            )
            first_id = rows[0]["id"]
            metrics["tampered_exit"], metrics["restored_exit"] = (
                _tamper_and_restore(config, context_a, service, first_id, key)
            )
            metrics["wrong_key_reads"] = _wrong_key_reads(
                service, context_a, first_id
            )
            metrics["tenant_leaks"] = _tenant_leaks(
                config, context_a, context_b, service, first_id, key
            )
            metrics["page_crosswires"] = _page_crosswires(config, context_a)
            metrics["gold_false_promotions"] = _gold_false_promotions(
                config, context_a
            )
            metrics["plaintext_or_key_leaks"] = _plaintext_or_key_leaks(
                key, bodies
            )
        metrics["external_calls"] = _external_calls()
    except Exception:
        pass
    for name in _ORDER:
        print(f"{name}={metrics[name]}")
    expected = (0, 2, 0, 0, 0, 0, 0, 0, 0)
    return 0 if tuple(metrics[name] for name in _ORDER) == expected else 2


if __name__ == "__main__":
    raise SystemExit(main())
