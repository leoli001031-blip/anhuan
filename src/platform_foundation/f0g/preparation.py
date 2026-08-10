"""Idempotent preparation of the three-person blind Fixture workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import uuid

from ..auth import SessionContext
from ..database import DatabaseConfig, role_transaction
from ..f0e.hashing import canonical_sha256, stable_uuid4
from .config import validate_local_database_config
from .contracts import F0GError, FixtureActorSession
from .identity import load_fixture_actor_sessions
from .service import verify_function_catalog
from .tokens import FixtureTokenBundle, create_token_bundle, load_token_bundle


GUIDELINE_VERSION = "f0g_fixture_blind_v1"
GUIDELINE_SPEC = {
    "schema": "f0g-fixture-annotation-guideline-v1",
    "scope": "LOCAL_FIXTURE_ONLY",
    "workflow": "TWO_INDEPENDENT_LABELS_THEN_THIRD_PARTY_ADJUDICATION",
    "normalization": "UTF8_NFC_LF_V1",
    "blind_peer_labels": True,
    "acceptance_gold": False,
    "benchmark_tier": "NONE",
    "external_processing": "DENY",
    "professional_status": "NOT_REVIEWED",
    "production_allowed": False,
}
GUIDELINE_SHA256 = canonical_sha256(GUIDELINE_SPEC)


@dataclass(frozen=True, slots=True)
class PrepareResult:
    guidelines: int
    assignments: int
    actor_sessions: int
    guideline_delta: int
    assignment_delta: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "f0g-prepare-result-v1",
            "status": "LOCAL_FIXTURE_ANNOTATION_WORKFLOW_PREPARED",
            "fixture_label": "FIXTURE_ONLY",
            "benchmark_tier": "NONE",
            "gold_status": "HUMAN_LABELS_REQUIRED",
            "production_allowed": False,
            "guidelines": self.guidelines,
            "assignments": self.assignments,
            "actor_sessions": self.actor_sessions,
            "delta": {
                "guidelines": self.guideline_delta,
                "assignments": self.assignment_delta,
            },
        }


def prepare_workflow(
    config: DatabaseConfig,
    operator: SessionContext,
    token_bundle_path: str,
) -> PrepareResult:
    try:
        validate_local_database_config(config)
        if not isinstance(operator, SessionContext):
            raise F0GError("ANNOTATION_PREPARE_FAILED")

        # Complete the database/catalog preflight before creating or loading
        # the capability bundle and before making any session-state write.
        verify_function_catalog(config)
        queue_ids = _eligible_queue_ids(config, operator)
        if not queue_ids:
            raise F0GError("ANNOTATION_PREPARE_FAILED")

        _ensure_token_bundle(token_bundle_path)
        sessions = load_fixture_actor_sessions(
            operator.enterprise_id, token_bundle_path
        )
        if len({item.actor_id for item in sessions}) != 3 or len(
            {item.session_id for item in sessions}
        ) != 3:
            raise F0GError("ANNOTATION_PREPARE_FAILED")

        # Session seeds, guideline, assignments and the one prepare audit are
        # a single migration-role transaction.  Any validation or SQL failure
        # below therefore rolls back the complete database mutation set.  A
        # safely-created token bundle intentionally remains retryable.
        with role_transaction(config, "f0d_migration") as connection:
            _set_context(connection, operator)
            _seed_viewer_sessions(connection, operator, sessions)
            guideline_id = stable_uuid4(
                "f0g-guideline-v1", operator.enterprise_id, GUIDELINE_SHA256
            )
            assignment_ids = tuple(
                stable_uuid4(
                    "f0g-blind-assignment-v1",
                    operator.enterprise_id,
                    guideline_id,
                    queue_id,
                )
                for queue_id in queue_ids
            )
            by_role = {item.role: item for item in sessions}
            row = connection.execute(
                "SELECT * FROM f0g.prepare_annotation_workflow("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    guideline_id,
                    GUIDELINE_VERSION,
                    GUIDELINE_SHA256,
                    list(assignment_ids),
                    list(queue_ids),
                    by_role["ANNOTATOR_ONE"].actor_id,
                    by_role["ANNOTATOR_TWO"].actor_id,
                    by_role["ADJUDICATOR"].actor_id,
                    stable_uuid4(
                        "f0g-prepare-audit-v1", operator.enterprise_id, guideline_id
                    ),
                ),
            ).fetchone()
            counts = _workflow_counts(connection)
            if row is None:
                raise F0GError("ANNOTATION_PREPARE_FAILED")
            result = PrepareResult(
                guidelines=int(counts["guidelines"]),
                assignments=int(counts["assignments"]),
                actor_sessions=len(sessions),
                guideline_delta=int(row["guideline_delta"]),
                assignment_delta=int(row["assignment_delta"]),
            )
            if result.guidelines != 1 or result.assignments != len(queue_ids):
                raise F0GError("ANNOTATION_PREPARE_FAILED")
        return result
    except F0GError:
        raise
    except Exception:
        raise F0GError("ANNOTATION_PREPARE_FAILED") from None


def _ensure_token_bundle(path: str) -> None:
    if not os.path.lexists(path):
        try:
            create_token_bundle(path)
        except F0GError as error:
            if error.code != "ANNOTATION_STATE_INVALID":
                raise
    with load_token_bundle(path) as bundle:
        if not isinstance(bundle, FixtureTokenBundle):
            raise F0GError("ANNOTATION_PREPARE_FAILED")


def _seed_viewer_sessions(
    connection: object,
    operator: SessionContext,
    sessions: tuple[FixtureActorSession, ...],
) -> None:
    expires_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    for session in sessions:
        token_sha256 = hashlib.sha256(session.token.encode("utf-8")).hexdigest()
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO f0d.actor(id,actor_kind) VALUES (%s,'FIXTURE_VIEWER') "
            "ON CONFLICT DO NOTHING",
            (session.actor_id,),
        )
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO f0d.enterprise_membership(enterprise_id,actor_id,role_code) "
            "VALUES (%s,%s,'FIXTURE_VIEWER') ON CONFLICT DO NOTHING",
            (operator.enterprise_id, session.actor_id),
        )
        connection.execute(  # type: ignore[attr-defined]
            "INSERT INTO f0d.local_fixture_session("
            "id,enterprise_id,actor_id,token_sha256,expires_at) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (
                session.session_id,
                operator.enterprise_id,
                session.actor_id,
                token_sha256,
                expires_at,
            ),
        )
    records = connection.execute(  # type: ignore[attr-defined]
        "SELECT actor.id,actor.actor_kind,actor.status,membership.role_code,"
        "membership.status AS membership_status,session.id AS session_id,"
        "session.token_sha256,session.expires_at,session.revoked_at "
        "FROM f0d.actor AS actor JOIN f0d.enterprise_membership AS membership "
        "ON membership.actor_id=actor.id JOIN f0d.local_fixture_session AS session "
        "ON session.enterprise_id=membership.enterprise_id "
        "AND session.actor_id=membership.actor_id "
        "WHERE membership.enterprise_id=%s AND actor.id=ANY(%s)",
        (operator.enterprise_id, [session.actor_id for session in sessions]),
    ).fetchall()
    expected = {
        session.actor_id: (
            session.session_id,
            hashlib.sha256(session.token.encode("utf-8")).hexdigest(),
        )
        for session in sessions
    }
    if len(records) != 3:
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    for record in records:
        identity = expected.get(record["id"])
        if (
            identity is None
            or record["actor_kind"] != "FIXTURE_VIEWER"
            or record["status"] != "ACTIVE"
            or record["role_code"] != "FIXTURE_VIEWER"
            or record["membership_status"] != "ACTIVE"
            or record["session_id"] != identity[0]
            or record["token_sha256"] != identity[1]
            or record["expires_at"] != expires_at
            or record["revoked_at"] is not None
        ):
            raise F0GError("ANNOTATION_PREPARE_FAILED")


def _eligible_queue_ids(
    config: DatabaseConfig, operator: SessionContext
) -> tuple[uuid.UUID, ...]:
    with role_transaction(config, "f0d_migration") as connection:
        _set_context(connection, operator)
        rows = connection.execute(
            "SELECT id FROM f0f.gold_annotation_queue "
            "WHERE queue_status='ANNOTATION_REQUIRED' AND benchmark_tier='NONE' "
            "AND NOT acceptance_gold AND NOT production_allowed "
            "ORDER BY selection_ordinal"
        ).fetchall()
        total = connection.execute(
            "SELECT count(*) AS count FROM f0f.gold_annotation_queue"
        ).fetchone()
    if total is None or len(rows) != int(total["count"]):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    return tuple(row["id"] for row in rows)


def _workflow_counts(connection: object) -> dict[str, object]:
    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT (SELECT count(*) FROM f0g.annotation_guideline) AS guidelines,"
        "(SELECT count(*) FROM f0g.blind_assignment) AS assignments"
    ).fetchone()
    if row is None:
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    return row


def _set_context(connection: object, operator: SessionContext) -> None:
    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT set_config('f0d.enterprise_id',%s,true) AS enterprise_id,"
        "set_config('f0d.actor_id',%s,true) AS actor_id,"
        "set_config('f0d.session_token_sha256',%s,true) AS token_sha256",
        (
            str(operator.enterprise_id),
            str(operator.actor_id),
            operator.session_token_sha256,
        ),
    ).fetchone()
    if (
        row is None
        or row["enterprise_id"] != str(operator.enterprise_id)
        or row["actor_id"] != str(operator.actor_id)
        or row["token_sha256"] != operator.session_token_sha256
    ):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    authorized = connection.execute(  # type: ignore[attr-defined]
        "SELECT f0d.context_session_authorized(%s) AS authorized",
        (operator.enterprise_id,),
    ).fetchone()
    if authorized is None or authorized.get("authorized") is not True:
        raise F0GError("ANNOTATION_PREPARE_FAILED")


__all__ = (
    "GUIDELINE_SHA256",
    "GUIDELINE_SPEC",
    "GUIDELINE_VERSION",
    "PrepareResult",
    "prepare_workflow",
)
