from __future__ import annotations

import base64
import ast
from contextlib import redirect_stderr, redirect_stdout
import io
import hashlib
import os
from pathlib import Path
import re
import uuid

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from platform_foundation.auth import SessionContext, authenticate_local_session
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN, LOCAL_TENANT_B_TOKEN
from platform_foundation.database import DatabaseConfig, DatabaseError, tenant_transaction
from platform_foundation.f0f.acceptance import ACCEPTANCE_KEY_FILE
from platform_foundation.f0f.keyfile import load_keyfile
from platform_foundation.f0g.acceptance import acceptance_snapshot
from platform_foundation.f0g.acceptance import verify_token_bundle_binding
from platform_foundation.f0g.contracts import CanonicalLabel, F0GError
from platform_foundation.f0g.identity import load_fixture_actor_sessions
from platform_foundation.f0g.preparation import prepare_workflow
from platform_foundation.f0g.service import AnnotationService
from platform_foundation.f0g.tokens import ACCEPTANCE_TOKEN_BUNDLE


_ROOT = Path(__file__).resolve().parents[1]
_BOOTSTRAP_DSN = (
    "postgresql://f0d_bootstrap:f0d-bootstrap-local-v01@127.0.0.1:55432/postgres"
)
_ORDER = (
    "valid_exit",
    "tampered_exit",
    "restored_exit",
    "wrong_actor_reads",
    "peer_label_leaks",
    "premature_adjudications",
    "self_adjudications",
    "tenant_leaks",
    "real_fixture_gold",
    "plaintext_or_key_leaks",
    "external_calls",
)
_EXPECTED = (0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0)
_ACTION_CODES = (
    "F0G_ASSIGNED_BODY_READ",
    "F0G_BLIND_LABEL_RECORDED",
    "F0G_LABEL_PAIR_READ",
    "F0G_ASSIGNMENT_ADJUDICATED",
)


def _empty_metrics() -> dict[str, int]:
    return {
        "valid_exit": 2,
        "tampered_exit": 0,
        "restored_exit": 2,
        "wrong_actor_reads": 1,
        "peer_label_leaks": 1,
        "premature_adjudications": 1,
        "self_adjudications": 1,
        "tenant_leaks": 1,
        "real_fixture_gold": 1,
        "plaintext_or_key_leaks": 1,
        "external_calls": 1,
    }


def _config(database: str) -> DatabaseConfig:
    base = "127.0.0.1:55432/" + database
    return DatabaseConfig(
        migration_dsn=(
            "postgresql://f0d_migration:f0d-migration-local-v01@" + base
        ),
        runtime_dsn=(
            "postgresql://f0d_runtime:f0d-runtime-local-v01@" + base
        ),
        worker_dsn=(
            "postgresql://f0d_worker:f0d-worker-local-v01@" + base
        ),
    )


def _real_fixture_gold() -> int:
    config = _config("f0g_acceptance_v01")
    operator = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    verify_token_bundle_binding(config, operator, ACCEPTANCE_TOKEN_BUNDLE)
    snapshot = acceptance_snapshot(config, operator)
    expected = {
        "annotation_queue": 15,
        "guidelines": 1,
        "assignments": 15,
        "unique_assignment_queues": 15,
        "label_slots": 30,
        "labels": 0,
        "adjudications": 0,
        "fixture_seed_gold": 0,
        "policy_bypasses": 0,
        "invalid_assignments": 0,
        "real_actions": 0,
        "plaintext_columns": 0,
        "gate_bypasses": 0,
        "fixture_actors": 3,
        "active_fixture_memberships": 3,
        "active_fixture_sessions": 3,
        "unique_fixture_session_token_hashes": 3,
        "fixture_actor_violations": 0,
        "fixture_membership_violations": 0,
        "fixture_session_violations": 0,
        "fixture_session_token_violations": 0,
        "prepare_audits": 1,
    }
    violations = sum(int(snapshot.get(name) != value) for name, value in expected.items())
    digest = snapshot.get("workflow_summary_sha256")
    violations += int(
        not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    )
    return violations


def _create_disposable_database(database: str) -> None:
    created = False
    try:
        with psycopg.connect(_BOOTSTRAP_DSN, autocommit=True) as connection:
            connection.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER f0d_migration TEMPLATE f0f_acceptance_v01"
                ).format(sql.Identifier(database))
            )
        created = True
        config = _config(database)
        previous = os.environ.get("F0D_MIGRATION_DSN")
        os.environ["F0D_MIGRATION_DSN"] = config.migration_dsn.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                command.upgrade(Config(str(_ROOT / "alembic.ini")), "f0d_0005")
        finally:
            if previous is None:
                os.environ.pop("F0D_MIGRATION_DSN", None)
            else:
                os.environ["F0D_MIGRATION_DSN"] = previous
    except Exception:
        if created:
            _drop_disposable_database(database)
        raise


def _drop_disposable_database(database: str) -> None:
    with psycopg.connect(_BOOTSTRAP_DSN, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database)
            )
        )


def _denied(operation: object) -> int:
    try:
        result = operation()  # type: ignore[operator]
    except F0GError:
        return 0
    if hasattr(result, "wipe"):
        result.wipe()
    elif isinstance(result, tuple):
        for item in result:
            if isinstance(item, tuple) and len(item) == 2 and hasattr(item[1], "wipe"):
                item[1].wipe()
    return 1


def _tamper_and_restore(
    config: DatabaseConfig,
    context: SessionContext,
    service: AnnotationService,
    assignment_id: uuid.UUID,
    key_material: bytearray,
) -> tuple[int, int]:
    tampered_exit = 0
    database = config.migration_dsn.rsplit("/", 1)[-1]
    bootstrap_target = _BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + database
    connection = psycopg.connect(
        bootstrap_target, autocommit=False, row_factory=dict_row
    )
    try:
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
        row = connection.execute(
            "SELECT body.id FROM f0g.blind_assignment AS assignment "
            "JOIN f0f.gold_annotation_queue AS queue "
            "ON queue.enterprise_id=assignment.enterprise_id "
            "AND queue.id=assignment.annotation_queue_id "
            "JOIN f0f.page_body_evidence AS body "
            "ON body.enterprise_id=queue.enterprise_id "
            "AND body.id=queue.page_body_evidence_id "
            "WHERE assignment.enterprise_id=%s AND assignment.id=%s",
            (context.enterprise_id, assignment_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("REVERSE_TARGET_MISSING")
        # Replica mode is transaction-local and suppresses FK/immutability
        # triggers only in this disposable database.  The hash check remains
        # active and the unconditional rollback below restores every byte.
        connection.execute("SET LOCAL session_replication_role='replica'")
        connection.execute(
            "UPDATE f0f.page_body_evidence SET "
            "ciphertext=set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%%256),"
            "ciphertext_sha256=encode(f0f_crypto.digest("
            "set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%%256),"
            "'sha256'),'hex')::char(64) "
            "WHERE id=%s",
            (row["id"],),
        )
        try:
            connection.execute(
                "SELECT * FROM f0g.read_assigned_body(%s,%s,%s)",
                (assignment_id, key_material, uuid.uuid4()),
            ).fetchone()
        except psycopg.Error:
            tampered_exit = 2
    finally:
        # This rollback is unconditional: even an unexpectedly successful
        # tampered decrypt can never persist the mutation or its audit row.
        connection.rollback()
        connection.close()

    restored_exit = 2
    try:
        body = service.read_assigned_body(context, assignment_id)
        body.wipe()
        restored_exit = 0
    except F0GError:
        restored_exit = 2
    return tampered_exit, restored_exit


def _peer_probe(
    config: DatabaseConfig,
    context: SessionContext,
    service: AnnotationService,
    assignment_id: uuid.UUID,
) -> int:
    leaks = 0
    record = next(
        (item for item in service.list_assignments(context) if item.assignment_id == assignment_id),
        None,
    )
    leaks += int(
        record is None
        or record.own_label_submitted
        or record.labels_submitted is not None
        or record.assignment_status != "ANNOTATION_PENDING"
    )
    leaks += _denied(lambda: service.read_adjudication_labels(context, assignment_id))
    direct_select_denied = False
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            connection.execute(
                "SELECT label_ordinal FROM f0f.gold_label_evidence"
            ).fetchall()
    except DatabaseError:
        direct_select_denied = True
    leaks += int(not direct_select_denied)
    return leaks


def _tenant_probe(
    service: AnnotationService,
    foreign_context: SessionContext,
    assignment_id: uuid.UUID,
) -> int:
    leaks = len(service.list_assignments(foreign_context))
    leaks += _denied(
        lambda: service.read_assigned_body(foreign_context, assignment_id)
    )
    leaks += _denied(
        lambda: service.read_adjudication_labels(foreign_context, assignment_id)
    )
    return leaks


def _label_write_state(
    config: DatabaseConfig, context: SessionContext
) -> tuple[int, int]:
    connection = psycopg.connect(config.migration_dsn, row_factory=dict_row)
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
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0f.gold_label_evidence) AS labels,"
                "(SELECT count(*) FROM f0d.audit_event WHERE "
                " event_code='F0G_BLIND_LABEL_RECORDED') AS audits"
            ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("REVERSE_LABEL_STATE_MISSING")
    return int(row["labels"]), int(row["audits"])


def _cross_assignment_probe(
    config: DatabaseConfig,
    context: SessionContext,
    first_label_id: uuid.UUID,
    wrong_assignment_id: uuid.UUID,
    label: bytearray,
) -> int:
    before = _label_write_state(config, context)
    denied = False
    key_material = bytearray()
    connection = psycopg.connect(config.runtime_dsn, autocommit=False)
    try:
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
        with load_keyfile(ACCEPTANCE_KEY_FILE) as key:
            key_material.extend(key.view())
        connection.execute(
            "SELECT f0g.record_blind_label(%s,%s,%s,%s,%s,%s,%s)",
            (
                first_label_id,
                wrong_assignment_id,
                key_material,
                label,
                hashlib.sha256(label).hexdigest(),
                len(label),
                uuid.uuid4(),
            ),
        ).fetchone()
    except psycopg.Error:
        denied = True
    finally:
        # A regressed gate must still be unable to persist the crosswire probe.
        connection.rollback()
        connection.close()
        key_material[:] = b"\0" * len(key_material)
        key_material.clear()
    after = _label_write_state(config, context)
    return int(not denied) + int(before != after)


def _synthetic_state(
    config: DatabaseConfig, operator: SessionContext
) -> int:
    connection = psycopg.connect(config.migration_dsn, row_factory=dict_row)
    try:
        with connection.transaction():
            connection.execute(
                "SELECT set_config('f0d.enterprise_id',%s,true),"
                "set_config('f0d.actor_id',%s,true),"
                "set_config('f0d.session_token_sha256',%s,true)",
                (
                    str(operator.enterprise_id),
                    str(operator.actor_id),
                    operator.session_token_sha256,
                ),
            )
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0f.gold_label_evidence) AS labels,"
                "(SELECT count(*) FROM f0f.gold_adjudication) AS adjudications,"
                "(SELECT count(*) FROM f0f.gold_adjudication "
                " WHERE gold_status='FIXTURE_SEED_GOLD') AS accepted,"
                "(SELECT count(*) FROM f0f.gold_adjudication "
                " WHERE gold_status='ADJUDICATION_UNRESOLVED') AS unresolved,"
                "(SELECT count(*) FROM f0f.gold_label_evidence AS label "
                " JOIN f0g.blind_assignment AS assignment "
                " ON assignment.enterprise_id=label.enterprise_id "
                " AND assignment.annotation_queue_id=label.annotation_queue_id "
                " WHERE (label.label_ordinal=1 AND label.annotator_actor_id<>assignment.annotator_one_actor_id) "
                " OR (label.label_ordinal=2 AND label.annotator_actor_id<>assignment.annotator_two_actor_id)) "
                "AS actor_slot_mismatches"
            ).fetchone()
    finally:
        connection.close()
    if row is None:
        return 1
    expected = {
        "labels": 4,
        "adjudications": 2,
        "accepted": 1,
        "unresolved": 1,
        "actor_slot_mismatches": 0,
    }
    return sum(int(int(row[name]) != value) for name, value in expected.items())


def _scan_targets(extra_paths: tuple[Path, ...] = ()) -> tuple[Path, ...]:
    targets = (
        _ROOT / "src/platform_foundation/f0g",
        _ROOT / "migrations/versions/f0d_0005_fixture_annotation_workflow.py",
        _ROOT / "tests/test_f0g_fixture_annotation.py",
        _ROOT / "tests/f0g_reverse_verify.py",
        _ROOT / "artifacts/f0g-annotation-workflow/v0.1",
        _ROOT / "PROGRESS.md",
        _ROOT / "BLOCKED.md",
    )
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
    files.extend(path for path in extra_paths if path.is_file())
    return tuple(sorted(set(files)))


def _bounded_fragments(value: bytes | bytearray | memoryview) -> tuple[bytearray, ...]:
    """Keep representative canaries without retaining another full body copy."""

    view = memoryview(value)
    if not view:
        return ()
    if len(view) <= 32:
        return (bytearray(view),)
    width = 32
    offsets = {
        0,
        max(0, len(view) // 4 - width // 2),
        max(0, len(view) // 2 - width // 2),
        max(0, (len(view) * 3) // 4 - width // 2),
        len(view) - width,
    }
    return tuple(bytearray(view[offset : offset + width]) for offset in sorted(offsets))


def _encoded_canaries(needles: tuple[bytearray, ...]) -> tuple[bytes, ...]:
    encoded: list[bytes] = []
    for needle in needles:
        if needle:
            value = bytes(needle)
            fragments = _bounded_fragments(value)
            try:
                for fragment in fragments:
                    raw = bytes(fragment)
                    standard = base64.b64encode(raw)
                    urlsafe = base64.urlsafe_b64encode(raw)
                    encoded.extend(
                        (
                            raw,
                            raw.hex().encode("ascii"),
                            standard,
                            standard.rstrip(b"="),
                            urlsafe,
                            urlsafe.rstrip(b"="),
                        )
                    )
            finally:
                for fragment in fragments:
                    fragment[:] = b"\0" * len(fragment)
                    fragment.clear()
    return tuple(dict.fromkeys(item for item in encoded if item))


def _plaintext_or_key_leaks(
    needles: tuple[bytearray, ...], extra_paths: tuple[Path, ...] = ()
) -> int:
    encoded = _encoded_canaries(needles)
    leaks = 0
    for path in _scan_targets(extra_paths):
        try:
            payload = path.read_bytes()
        except OSError:
            leaks += 1
            continue
        leaks += sum(int(needle in payload) for needle in encoded if needle)
    return leaks


def _leak_detector_self_test(path: Path) -> bool:
    canary = bytearray(os.urandom(32))
    try:
        encoded = base64.urlsafe_b64encode(bytes(canary)).rstrip(b"=")
        path.write_bytes(encoded)
        detected = _plaintext_or_key_leaks((canary,), (path,)) > 0
        path.unlink()
        cleared = _plaintext_or_key_leaks((canary,), (path,)) == 0
        return detected and cleared
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            path_was_already_absent = True
        except OSError:
            path_was_already_absent = False
        else:
            path_was_already_absent = False
        canary[:] = b"\0" * len(canary)
        canary.clear()
        if not path_was_already_absent and path.exists():
            raise RuntimeError("REVERSE_LOG_CLEANUP_FAILED")


def _session_token_leak_needles(tokens: tuple[str, ...]) -> tuple[bytearray, ...]:
    ascii_needles: list[bytearray] = []
    raw_needles: list[bytearray] = []
    raw_bundle = bytearray()
    complete = False
    try:
        if len(tokens) != 3:
            raise RuntimeError("REVERSE_SESSION_TOKEN_MISMATCH")
        for token in tokens:
            if re.fullmatch(r"f0g_[0-9a-f]{64}", token) is None:
                raise RuntimeError("REVERSE_SESSION_TOKEN_MISMATCH")
            ascii_needles.append(bytearray(token.encode("ascii")))
            raw_needles.append(bytearray.fromhex(token[4:]))
            if len(raw_needles[-1]) != 32:
                raise RuntimeError("REVERSE_SESSION_TOKEN_MISMATCH")
            raw_bundle.extend(raw_needles[-1])
        if len(raw_bundle) != 96:
            raise RuntimeError("REVERSE_SESSION_TOKEN_MISMATCH")
        complete = True
        return (*ascii_needles, *raw_needles, raw_bundle)
    finally:
        if not complete:
            for needle in (*ascii_needles, *raw_needles, raw_bundle):
                needle[:] = b"\0" * len(needle)
                needle.clear()


def _external_calls() -> int:
    forbidden = frozenset(
        {
            "anthropic",
            "boto3",
            "fitz",
            "httpx",
            "openai",
            "requests",
            "socket",
            "subprocess",
            "tesseract",
            "urllib",
        }
    )
    violations = 0
    for path in _scan_targets():
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_bytes())
        except (OSError, SyntaxError, ValueError):
            violations += 1
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations += sum(
                    int(alias.name.split(".", 1)[0] in forbidden)
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                violations += int(
                    bool(node.module)
                    and node.module.split(".", 1)[0] in forbidden
                )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                violations += int(node.func.id in {"__import__", "eval", "exec"})
    return violations


def _exercise_disposable(
    metrics: dict[str, int], database: str, token_path: str
) -> tuple[bytearray, ...]:
    config = _config(database)
    operator = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    prepared = prepare_workflow(config, operator, token_path)
    if prepared.assignments != 15 or prepared.actor_sessions != 3:
        raise RuntimeError("REVERSE_PREPARE_MISMATCH")
    sessions = load_fixture_actor_sessions(operator.enterprise_id, token_path)
    contexts = {
        session.role: authenticate_local_session(config, session.token)
        for session in sessions
    }
    token_needles = _session_token_leak_needles(
        tuple(session.token for session in sessions)
    )
    owned_needles: list[bytearray] = []
    complete = False
    try:
        service = AnnotationService(config, ACCEPTANCE_KEY_FILE)
        assignments = service.list_assignments(contexts["ANNOTATOR_ONE"])
        if len(assignments) != 15:
            raise RuntimeError("REVERSE_ASSIGNMENT_MISMATCH")
        first, second = assignments[:2]
        body_fragments = 0
        for assignment in assignments:
            body = service.read_assigned_body(
                contexts["ANNOTATOR_ONE"], assignment.assignment_id
            )
            try:
                fragments = _bounded_fragments(body.view())
                owned_needles.extend(fragments)
                body_fragments += len(fragments)
            finally:
                body.wipe()
        if body_fragments < len(assignments):
            raise RuntimeError("REVERSE_BODY_CANARY_MISMATCH")
        metrics["valid_exit"] = 0

        with load_keyfile(ACCEPTANCE_KEY_FILE) as key:
            key_canary = bytearray(key.view())
            owned_needles.append(key_canary)
            metrics["tampered_exit"], metrics["restored_exit"] = _tamper_and_restore(
                config,
                contexts["ANNOTATOR_ONE"],
                service,
                first.assignment_id,
                key_canary,
            )

        metrics["wrong_actor_reads"] = _denied(
            lambda: service.read_assigned_body(operator, first.assignment_id)
        )
        foreign = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
        metrics["tenant_leaks"] = _tenant_probe(service, foreign, first.assignment_id)

        label_one = bytearray(("fixture-" + uuid.uuid4().hex).encode("ascii"))
        label_two = bytearray(("fixture-" + uuid.uuid4().hex).encode("ascii"))
        owned_needles.extend((label_one, label_two))
        with CanonicalLabel(label_two) as canonical:
            service.submit_label(contexts["ANNOTATOR_TWO"], first.assignment_id, canonical)
        metrics["peer_label_leaks"] = _peer_probe(
            config, contexts["ANNOTATOR_ONE"], service, first.assignment_id
        )
        metrics["premature_adjudications"] = sum(
            (
                _denied(
                    lambda: service.read_assigned_body(
                        contexts["ADJUDICATOR"], first.assignment_id
                    )
                ),
                _denied(
                    lambda: service.read_adjudication_labels(
                        contexts["ADJUDICATOR"], first.assignment_id
                    )
                ),
                _denied(
                    lambda: service.adjudicate(
                        contexts["ADJUDICATOR"],
                        first.assignment_id,
                        "NO_CONSENSUS",
                        None,
                    )
                ),
            )
        )
        with CanonicalLabel(label_one) as canonical:
            first_label_id = service.submit_label(
                contexts["ANNOTATOR_ONE"], first.assignment_id, canonical
            )
        metrics["wrong_actor_reads"] += _cross_assignment_probe(
            config,
            contexts["ANNOTATOR_ONE"],
            first_label_id,
            second.assignment_id,
            label_one,
        )

        pairs = service.read_adjudication_labels(
            contexts["ADJUDICATOR"], first.assignment_id
        )
        ordinal_payload: dict[int, bytearray] = {}
        try:
            ordinal_payload = {
                metadata.label_ordinal: bytearray(owner.view())
                for metadata, owner in pairs
            }
            metrics["peer_label_leaks"] += int(ordinal_payload.get(1) != label_one)
            metrics["peer_label_leaks"] += int(ordinal_payload.get(2) != label_two)
        finally:
            for _metadata, owner in pairs:
                owner.wipe()
            for payload in ordinal_payload.values():
                payload[:] = b"\0" * len(payload)
                payload.clear()

        metrics["self_adjudications"] = sum(
            _denied(
                lambda context=context: service.adjudicate(
                    context, first.assignment_id, "NO_CONSENSUS", None
                )
            )
            for context in (contexts["ANNOTATOR_ONE"], contexts["ANNOTATOR_TWO"])
        )
        service.adjudicate(
            contexts["ADJUDICATOR"], first.assignment_id, "ACCEPT_LABEL_ONE", 1
        )

        label_three = bytearray(("fixture-" + uuid.uuid4().hex).encode("ascii"))
        label_four = bytearray(("fixture-" + uuid.uuid4().hex).encode("ascii"))
        owned_needles.extend((label_three, label_four))
        with CanonicalLabel(label_four) as canonical:
            service.submit_label(contexts["ANNOTATOR_TWO"], second.assignment_id, canonical)
        with CanonicalLabel(label_three) as canonical:
            service.submit_label(contexts["ANNOTATOR_ONE"], second.assignment_id, canonical)
        service.adjudicate(
            contexts["ADJUDICATOR"], second.assignment_id, "NO_CONSENSUS", None
        )
        if _synthetic_state(config, operator) != 0:
            raise RuntimeError("REVERSE_SYNTHETIC_STATE_MISMATCH")
        complete = True
        return (*owned_needles, *token_needles)
    finally:
        if not complete:
            for needle in (*token_needles, *owned_needles):
                needle[:] = b"\0" * len(needle)
                needle.clear()


def main() -> int:
    metrics = _empty_metrics()
    database = "f0g_verify_" + uuid.uuid4().hex[:16]
    token_path = "/private/tmp/anhuan-f0g-reverse-" + uuid.uuid4().hex + ".tokens"
    log_path = Path(
        "/private/tmp/anhuan-f0g-reverse-" + uuid.uuid4().hex + ".log"
    )
    needles: tuple[bytearray, ...] = ()
    created = False
    cleanup_failed = False
    execution_failed = False
    try:
        metrics["real_fixture_gold"] = _real_fixture_gold()
        if not _leak_detector_self_test(log_path):
            raise RuntimeError("REVERSE_LEAK_DETECTOR_FAILED")
        _create_disposable_database(database)
        created = True
        needles = _exercise_disposable(metrics, database, token_path)
        metrics["plaintext_or_key_leaks"] = _plaintext_or_key_leaks(
            needles, (log_path,)
        )
        metrics["external_calls"] = _external_calls()
    except Exception:
        execution_failed = True
    finally:
        if created:
            try:
                _drop_disposable_database(database)
            except Exception:
                cleanup_failed = True
        try:
            os.unlink(token_path)
        except FileNotFoundError:
            token_was_already_absent = True
        except OSError:
            cleanup_failed = True
            token_was_already_absent = False
        else:
            token_was_already_absent = False
        try:
            log_path.unlink()
        except FileNotFoundError:
            log_was_already_absent = True
        except OSError:
            cleanup_failed = True
            log_was_already_absent = False
        else:
            log_was_already_absent = False
        for needle in needles:
            needle[:] = b"\0" * len(needle)
            needle.clear()
    if (
        execution_failed
        or cleanup_failed
        or (not token_was_already_absent and os.path.exists(token_path))
        or (not log_was_already_absent and log_path.exists())
    ):
        metrics["external_calls"] += 1
    for name in _ORDER:
        print(f"{name}={metrics[name]}")
    return 0 if tuple(metrics[name] for name in _ORDER) == _EXPECTED else 2


if __name__ == "__main__":
    raise SystemExit(main())
