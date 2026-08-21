"""Due-queue claim_next for the dedicated material-RAG worker.

No new tables.  Default engineering remains on f1_0014.  This revision is
requested only by the dedicated material-RAG migrator.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0016"
down_revision: str | None = "f1_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.claim_next_material_rag_job(
          p_worker_id text, p_lease_seconds integer
        ) RETURNS TABLE(
          enterprise_id uuid, job_id uuid, lease_token uuid,
          knowledge_scope_id uuid, document_record_id uuid,
          document_version_id uuid, upload_task_id uuid,
          source_sha256 text, action text,
          attempt integer
        ) LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE v_job_id uuid;
        DECLARE v_token uuid := gen_random_uuid();
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9_.:-]{1,128}$'
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 1 OR p_lease_seconds > 900 THEN
            RAISE EXCEPTION 'MATERIAL_RAG_JOB_CLAIM_INVALID';
          END IF;
          SELECT job.id INTO v_job_id
            FROM f1.material_rag_job AS job
           WHERE job.attempt < 100
             AND (
               job.status = 'queued'
               OR (job.status = 'retry_wait'
                   AND job.next_attempt_at <= statement_timestamp())
               OR (job.status = 'running'
                   AND job.lease_until <= statement_timestamp())
             )
           ORDER BY COALESCE(job.next_attempt_at, job.created_at),
                    job.created_at, job.id
           LIMIT 1
           FOR UPDATE OF job SKIP LOCKED;
          IF v_job_id IS NULL THEN
            RETURN;
          END IF;
          PERFORM set_config('f1.material_rag_job_id', v_job_id::text, true);
          PERFORM set_config(
            'f1.material_rag_lease_token', v_token::text, true
          );
          RETURN QUERY
          UPDATE f1.material_rag_job AS job
             SET status = 'running', lease_token = v_token,
                 lease_owner = p_worker_id,
                 lease_acquired_at = statement_timestamp(),
                 lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 next_attempt_at = NULL, error_reason = NULL,
                 result_manifest_sha256 = NULL, indexed_unit_count = NULL,
                 attempt = job.attempt + 1,
                 updated_at = statement_timestamp()
           WHERE job.id = v_job_id AND job.attempt < 100
             AND (
               job.status = 'queued'
               OR (job.status = 'retry_wait'
                   AND job.next_attempt_at <= statement_timestamp())
               OR (job.status = 'running'
                   AND job.lease_until <= statement_timestamp())
             )
          RETURNING job.enterprise_id, job.id, job.lease_token,
                    job.knowledge_scope_id, job.document_record_id,
                    job.document_version_id, job.upload_task_id,
                    job.source_sha256, job.action, job.attempt;
        END $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.claim_next_material_rag_job(text,integer) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.claim_next_material_rag_job(text,integer) TO f1_worker"
    )
    op.execute(
        """
        CREATE POLICY material_rag_job_worker_due_select ON f1.material_rag_job
          FOR SELECT TO f1_worker
          USING (
            attempt < 100
            AND (
              status = 'queued'
              OR (status = 'retry_wait'
                  AND next_attempt_at <= statement_timestamp())
              OR (status = 'running'
                  AND lease_until <= statement_timestamp())
            )
          )
        """
    )
    op.execute(
        """
        CREATE POLICY material_rag_job_worker_due_update ON f1.material_rag_job
          FOR UPDATE TO f1_worker
          USING (
            attempt < 100
            AND (
              status = 'queued'
              OR (status = 'retry_wait'
                  AND next_attempt_at <= statement_timestamp())
              OR (status = 'running'
                  AND lease_until <= statement_timestamp())
            )
          )
          WITH CHECK (
            id = f1.current_material_rag_job_id()
            AND (
              (
                status = 'running'
                AND lease_token = f1.current_material_rag_lease_token()
                AND lease_until > statement_timestamp()
              )
              OR (
                status IN ('done','retry_wait','failed')
                AND lease_token IS NULL
              )
            )
          )
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS material_rag_job_worker_due_update ON f1.material_rag_job"
    )
    op.execute(
        "DROP POLICY IF EXISTS material_rag_job_worker_due_select ON f1.material_rag_job"
    )
    op.execute("DROP FUNCTION IF EXISTS f1.claim_next_material_rag_job(text,integer)")
