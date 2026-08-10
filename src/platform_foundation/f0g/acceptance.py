"""Aggregate-only acceptance snapshot for the real F0-G Fixture database."""

from __future__ import annotations

import hashlib

from ..auth import SessionContext
from ..database import DatabaseConfig, role_transaction
from ..f0e.hashing import canonical_sha256
from .config import validate_local_database_config
from .contracts import F0GError
from .identity import load_fixture_actor_sessions
from .service import verify_function_catalog


def acceptance_snapshot(
    config: DatabaseConfig, operator: SessionContext
) -> dict[str, object]:
    try:
        validate_local_database_config(config)
        verify_function_catalog(config)
        with role_transaction(config, "f0d_migration") as connection:
            _set_context(connection, operator)
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0f.gold_annotation_queue) AS annotation_queue,"
                "(SELECT count(*) FROM f0g.annotation_guideline) AS guidelines,"
                "(SELECT count(*) FROM f0g.blind_assignment) AS assignments,"
                "(SELECT count(DISTINCT annotation_queue_id) FROM f0g.blind_assignment) "
                "AS unique_assignment_queues,"
                "(SELECT count(*) * 2 FROM f0g.blind_assignment) AS label_slots,"
                "(SELECT count(*) FROM f0f.gold_label_evidence) AS labels,"
                "(SELECT count(*) FROM f0f.gold_adjudication) AS adjudications,"
                "(SELECT count(*) FROM f0f.gold_adjudication "
                " WHERE gold_status='FIXTURE_SEED_GOLD') AS fixture_seed_gold,"
                "(SELECT count(*) FROM f0g.annotation_guideline WHERE "
                " benchmark_tier<>'NONE' OR acceptance_gold OR public_display_allowed "
                " OR production_allowed OR external_processing_policy<>'DENY') + "
                "(SELECT count(*) FROM f0g.blind_assignment WHERE "
                " benchmark_tier<>'NONE' OR acceptance_gold OR public_display_allowed "
                " OR production_allowed OR external_processing_policy<>'DENY') "
                "AS policy_bypasses,"
                "(SELECT count(*) FROM f0g.blind_assignment AS assignment JOIN "
                " f0f.gold_annotation_queue AS queue ON queue.enterprise_id=assignment.enterprise_id "
                " AND queue.id=assignment.annotation_queue_id JOIN f0f.page_body_evidence AS body "
                " ON body.enterprise_id=queue.enterprise_id AND body.id=queue.page_body_evidence_id "
                " JOIN f0d.fixture_source_registry AS source ON source.enterprise_id=body.enterprise_id "
                " AND source.source_document_id=body.source_document_id "
                " WHERE source.source_group='negative' OR source.corpus_role<>'CORE_FIXTURE') "
                "AS invalid_assignments,"
                "(SELECT count(*) FROM f0d.audit_event WHERE event_code IN "
                " ('F0G_ASSIGNED_BODY_READ','F0G_BLIND_LABEL_RECORDED',"
                "  'F0G_LABEL_PAIR_READ','F0G_ASSIGNMENT_ADJUDICATED')) AS real_actions,"
                "(SELECT count(*) FROM f0d.audit_event WHERE enterprise_id=%s "
                " AND event_code='F0G_WORKFLOW_PREPARED') AS prepare_audits,"
                "(SELECT count(*) FROM information_schema.columns WHERE table_schema IN ('f0f','f0g') "
                " AND column_name IN ('body','body_text','label_body','plaintext','raw_text')) "
                "AS plaintext_columns,"
                "(SELECT count(*) FROM f0d.capability_gate WHERE status<>'CLOSED') "
                "AS gate_bypasses",
                (operator.enterprise_id,),
            ).fetchone()
            guideline_rows = connection.execute(
                "SELECT id,guideline_version,guideline_sha256,workflow_status,benchmark_tier,"
                "external_processing_policy FROM f0g.annotation_guideline ORDER BY id"
            ).fetchall()
            assignment_rows = connection.execute(
                "SELECT id,annotation_queue_id,guideline_id,guideline_sha256,"
                "annotator_one_actor_id,annotator_two_actor_id,adjudicator_actor_id,"
                "assignment_status FROM f0g.blind_assignment ORDER BY annotation_queue_id"
            ).fetchall()
            actor_row = connection.execute(
                "WITH assigned(enterprise_id,actor_id) AS ("
                " SELECT enterprise_id,annotator_one_actor_id FROM f0g.blind_assignment UNION "
                " SELECT enterprise_id,annotator_two_actor_id FROM f0g.blind_assignment UNION "
                " SELECT enterprise_id,adjudicator_actor_id FROM f0g.blind_assignment),"
                "actor_state AS (SELECT assigned.enterprise_id,assigned.actor_id,"
                " actor.actor_kind,actor.status AS actor_status,"
                " membership.role_code,membership.status AS membership_status "
                " FROM assigned LEFT JOIN f0d.actor AS actor ON actor.id=assigned.actor_id "
                " LEFT JOIN f0d.enterprise_membership AS membership "
                " ON membership.enterprise_id=assigned.enterprise_id "
                " AND membership.actor_id=assigned.actor_id),"
                "current_sessions AS (SELECT session.enterprise_id,session.actor_id,"
                " session.token_sha256 FROM assigned JOIN f0d.local_fixture_session AS session "
                " ON session.enterprise_id=assigned.enterprise_id "
                " AND session.actor_id=assigned.actor_id WHERE session.revoked_at IS NULL "
                " AND session.expires_at>statement_timestamp()),"
                "session_state AS (SELECT assigned.enterprise_id,assigned.actor_id,"
                " count(current_sessions.actor_id) AS current_session_count FROM assigned "
                " LEFT JOIN current_sessions ON current_sessions.enterprise_id=assigned.enterprise_id "
                " AND current_sessions.actor_id=assigned.actor_id "
                " GROUP BY assigned.enterprise_id,assigned.actor_id),"
                "token_state AS (SELECT count(*) AS current_sessions,"
                " count(DISTINCT token_sha256) AS unique_tokens,"
                " count(*) FILTER (WHERE token_sha256::text !~ '^[0-9a-f]{64}$') "
                " AS malformed_tokens FROM current_sessions) "
                "SELECT (SELECT count(*) FROM assigned) AS fixture_actors,"
                " (SELECT count(*) FROM actor_state WHERE actor_kind IS DISTINCT FROM "
                " 'FIXTURE_VIEWER' OR actor_status IS DISTINCT FROM 'ACTIVE') "
                " AS fixture_actor_violations,"
                " (SELECT count(*) FROM actor_state WHERE role_code IS DISTINCT FROM "
                " 'FIXTURE_VIEWER' OR membership_status IS DISTINCT FROM 'ACTIVE') "
                " AS fixture_membership_violations,"
                " (SELECT count(*) FROM actor_state WHERE role_code='FIXTURE_VIEWER' "
                " AND membership_status='ACTIVE') AS active_fixture_memberships,"
                " (SELECT count(*) FROM session_state WHERE current_session_count<>1) "
                " AS fixture_session_violations,"
                " token_state.current_sessions AS active_fixture_sessions,"
                " token_state.unique_tokens AS unique_fixture_session_token_hashes,"
                " (token_state.malformed_tokens + token_state.current_sessions "
                " - token_state.unique_tokens) AS fixture_session_token_violations "
                "FROM token_state"
            ).fetchone()
        if row is None or actor_row is None:
            raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
        result = {key: int(value) for key, value in row.items()}
        result.update({key: int(value) for key, value in actor_row.items()})
        result["workflow_summary_sha256"] = canonical_sha256(
            {
                "guidelines": [
                    tuple(str(value) for value in item.values()) for item in guideline_rows
                ],
                "assignments": [
                    tuple(str(value) for value in item.values()) for item in assignment_rows
                ],
            }
        )
        return result
    except F0GError:
        raise
    except Exception:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH") from None


def verify_token_bundle_binding(
    config: DatabaseConfig,
    operator: SessionContext,
    token_bundle_path: str,
) -> None:
    """Prove the strict local bundle matches exactly three current sessions."""

    try:
        validate_local_database_config(config)
        sessions = load_fixture_actor_sessions(
            operator.enterprise_id, token_bundle_path
        )
        token_hashes = {
            hashlib.sha256(item.token.encode("utf-8")).hexdigest()
            for item in sessions
        }
        if (
            len(sessions) != 3
            or len({item.actor_id for item in sessions}) != 3
            or len({item.session_id for item in sessions}) != 3
            or len(token_hashes) != 3
            or {item.role for item in sessions}
            != {"ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR"}
        ):
            raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
        actor_by_role = {item.role: item.actor_id for item in sessions}
        expected = {
            (
                item.actor_id,
                item.session_id,
                hashlib.sha256(item.token.encode("utf-8")).hexdigest(),
            )
            for item in sessions
        }
        with role_transaction(config, "f0d_migration") as connection:
            _set_context(connection, operator)
            rows = connection.execute(
                "SELECT actor_id,id,token_sha256 FROM f0d.local_fixture_session "
                "WHERE enterprise_id=%s AND actor_id=ANY(%s) "
                "AND revoked_at IS NULL AND expires_at>statement_timestamp()",
                (
                    operator.enterprise_id,
                    [item.actor_id for item in sessions],
                ),
            ).fetchall()
            assignment_rows = connection.execute(
                "SELECT annotation_queue_id,annotator_one_actor_id,"
                "annotator_two_actor_id,adjudicator_actor_id "
                "FROM f0g.blind_assignment WHERE enterprise_id=%s "
                "ORDER BY annotation_queue_id",
                (operator.enterprise_id,),
            ).fetchall()
            queue_row = connection.execute(
                "SELECT count(*) AS queue_count FROM f0f.gold_annotation_queue "
                "WHERE enterprise_id=%s",
                (operator.enterprise_id,),
            ).fetchone()
        actual = {
            (row["actor_id"], row["id"], str(row["token_sha256"]))
            for row in rows
        }
        if actual != expected:
            raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
        if (
            queue_row is None
            or int(queue_row.get("queue_count", 0)) <= 0
            or len(assignment_rows) != int(queue_row["queue_count"])
            or len({row["annotation_queue_id"] for row in assignment_rows})
            != int(queue_row["queue_count"])
            or any(
                row["annotator_one_actor_id"]
                != actor_by_role["ANNOTATOR_ONE"]
                or row["annotator_two_actor_id"]
                != actor_by_role["ANNOTATOR_TWO"]
                or row["adjudicator_actor_id"]
                != actor_by_role["ADJUDICATOR"]
                for row in assignment_rows
            )
        ):
            raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
    except F0GError:
        raise
    except Exception:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH") from None


def _set_context(connection: object, context: SessionContext) -> None:
    row = connection.execute(  # type: ignore[attr-defined]
        "SELECT set_config('f0d.enterprise_id',%s,true) AS enterprise_id,"
        "set_config('f0d.actor_id',%s,true) AS actor_id,"
        "set_config('f0d.session_token_sha256',%s,true) AS token_sha256",
        (
            str(context.enterprise_id),
            str(context.actor_id),
            context.session_token_sha256,
        ),
    ).fetchone()
    if (
        row is None
        or row["enterprise_id"] != str(context.enterprise_id)
        or row["actor_id"] != str(context.actor_id)
        or row["token_sha256"] != context.session_token_sha256
    ):
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
    authorized = connection.execute(  # type: ignore[attr-defined]
        "SELECT f0d.context_session_authorized(%s) AS authorized",
        (context.enterprise_id,),
    ).fetchone()
    if authorized is None or authorized.get("authorized") is not True:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")


__all__ = ("acceptance_snapshot", "verify_token_bundle_binding")
