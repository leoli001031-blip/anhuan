"""Bind every tenant policy to a live authenticated fixture session.

Revision ID: f0d_0002
Revises: f0d_0001
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0002"
down_revision: str | None = "f0d_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SCOPED_TABLES = (
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
)


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f0d.current_actor_id()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
          WITH setting(value) AS (
            SELECT current_setting('f0d.actor_id', true)
          )
          SELECT CASE
            WHEN value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN CAST(value AS uuid)
            ELSE NULL
          END
          FROM setting
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f0d.context_session_authorized(p_enterprise_id uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, f0d AS $$
          SELECT EXISTS (
            SELECT 1
            FROM f0d.local_fixture_session AS session
            JOIN f0d.enterprise_membership AS membership
              ON membership.enterprise_id = session.enterprise_id
             AND membership.actor_id = session.actor_id
            JOIN f0d.actor AS actor ON actor.id = session.actor_id
            WHERE session.enterprise_id = p_enterprise_id
              AND session.enterprise_id = f0d.current_enterprise_id()
              AND session.actor_id = f0d.current_actor_id()
              AND session.token_sha256 = current_setting('f0d.session_token_sha256', true)
              AND current_setting('f0d.session_token_sha256', true) ~ '^[0-9a-f]{64}$'
              AND session.revoked_at IS NULL
              AND session.expires_at > statement_timestamp()
              AND membership.status = 'ACTIVE'
              AND actor.status = 'ACTIVE'
          )
        $$
        """
    )
    for table in _SCOPED_TABLES:
        op.execute(f"DROP POLICY tenant_boundary ON f0d.{table}")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f0d.{table}
            FOR ALL TO f0d_runtime, f0d_worker
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            WITH CHECK (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )

    for table in ("upload_session", "idempotency_record", "audit_event"):
        op.execute(
            f"""
            CREATE POLICY actor_insert_boundary ON f0d.{table}
            AS RESTRICTIVE FOR INSERT TO f0d_runtime, f0d_worker
            WITH CHECK (actor_id = f0d.current_actor_id())
            """
        )

    op.execute("REVOKE ALL ON FUNCTION f0d.current_actor_id() FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION f0d.context_session_authorized(uuid) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f0d.current_actor_id() TO f0d_runtime, f0d_worker"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f0d.context_session_authorized(uuid) "
        "TO f0d_runtime, f0d_worker"
    )
    op.execute(
        "REVOKE ALL ON f0d.local_fixture_session FROM f0d_runtime, f0d_worker"
    )
    op.execute("REVOKE ALL ON f0d.actor FROM f0d_runtime, f0d_worker")
    op.execute(
        "REVOKE INSERT ON f0d.audit_event, f0d.outbox_event FROM f0d_runtime"
    )
    op.execute(
        "REVOKE UPDATE ON f0d.upload_session, f0d.idempotency_record FROM f0d_runtime"
    )
    op.execute("REVOKE INSERT ON f0d.upload_session FROM f0d_runtime")
    op.execute(
        "GRANT INSERT(id,enterprise_id,actor_id,source_document_id,expected_sha256,"
        "expected_size_bytes,quarantine_object_key) ON f0d.upload_session TO f0d_runtime"
    )
    op.execute(
        "GRANT UPDATE(status,quarantine_object_key,captured_sha256,"
        "captured_size_bytes,rejection_code) ON f0d.upload_session TO f0d_runtime"
    )
    op.execute(
        "REVOKE UPDATE ON f0d.upload_session, f0d.idempotency_record,"
        "f0d.outbox_event, f0d.job FROM f0d_worker"
    )
    op.execute(
        "GRANT UPDATE(status,completed_at) ON f0d.upload_session TO f0d_worker"
    )
    op.execute(
        "GRANT UPDATE(status,response_status,response_reference_id,completed_at) "
        "ON f0d.idempotency_record TO f0d_worker"
    )
    op.execute(
        "GRANT UPDATE(status,attempts,published_at) ON f0d.outbox_event TO f0d_worker"
    )
    op.execute(
        "GRANT UPDATE(status,attempts,lease_owner,lease_until,lease_generation,"
        "lease_token,heartbeat_at,progress_done,progress_total,error_code,finished_at) "
        "ON f0d.job TO f0d_worker"
    )
    op.execute("GRANT SELECT ON f0d.capability_gate TO f0d_runtime, f0d_worker")
    op.execute(
        "ALTER TABLE f0d.audit_event DROP CONSTRAINT audit_event_actor_id_fkey"
    )
    op.execute(
        """
        ALTER TABLE f0d.audit_event
        ADD CONSTRAINT audit_event_actor_membership_fk
        FOREIGN KEY (enterprise_id, actor_id)
        REFERENCES f0d.enterprise_membership(enterprise_id, actor_id)
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.upload_session
        ADD CONSTRAINT upload_identity_uq UNIQUE (
          enterprise_id, id, expected_sha256, expected_size_bytes
        ),
        ADD CONSTRAINT upload_source_identity_uq UNIQUE (
          enterprise_id, id, source_document_id
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.object_blob
        ADD CONSTRAINT object_blob_upload_identity_uq UNIQUE (
          enterprise_id, id, upload_session_id
        ),
        ADD CONSTRAINT object_blob_content_identity_fk FOREIGN KEY (
          enterprise_id, upload_session_id, sha256, size_bytes
        ) REFERENCES f0d.upload_session(
          enterprise_id, id, expected_sha256, expected_size_bytes
        )
        """
    )
    op.execute(
        "ALTER TABLE f0d.document_version ADD COLUMN upload_session_id uuid"
    )
    op.execute(
        "ALTER TABLE f0d.document_version "
        "DISABLE TRIGGER reject_immutable_mutation"
    )
    op.execute("ALTER TABLE f0d.document_version NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f0d.object_blob NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE f0d.document_version AS version
        SET upload_session_id = blob.upload_session_id
        FROM f0d.object_blob AS blob
        WHERE blob.enterprise_id = version.enterprise_id
          AND blob.id = version.object_blob_id
        """
    )
    op.execute("ALTER TABLE f0d.document_version FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f0d.object_blob FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE f0d.document_version "
        "ENABLE TRIGGER reject_immutable_mutation"
    )
    op.execute(
        """
        ALTER TABLE f0d.document_version
        ALTER COLUMN upload_session_id SET NOT NULL,
        ADD CONSTRAINT document_version_upload_source_fk FOREIGN KEY (
          enterprise_id, upload_session_id, source_document_id
        ) REFERENCES f0d.upload_session(
          enterprise_id, id, source_document_id
        ),
        ADD CONSTRAINT document_version_blob_upload_fk FOREIGN KEY (
          enterprise_id, object_blob_id, upload_session_id
        ) REFERENCES f0d.object_blob(
          enterprise_id, id, upload_session_id
        ),
        ADD CONSTRAINT document_version_scope_source_uq UNIQUE (
          enterprise_id, id, source_document_id
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.document_processing_plan
        ADD CONSTRAINT processing_plan_version_source_fk FOREIGN KEY (
          enterprise_id, document_version_id, source_document_id
        ) REFERENCES f0d.document_version(
          enterprise_id, id, source_document_id
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError("F0D_SECURITY_HARDENING_IS_IRREVERSIBLE")
