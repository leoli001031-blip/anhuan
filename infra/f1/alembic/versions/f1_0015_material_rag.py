"""Persist scoped material RAG units, bindings, jobs, and QA context."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0015"
down_revision: str | None = "f1_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_QUERY_CONTEXT = "0" * 64


def upgrade() -> None:
    _qa_context_contract()
    _tables()
    _guards()
    _job_functions()
    _rls_and_grants()


def _qa_context_contract() -> None:
    # A fixed legacy digest keeps pre-f1_0015 rows replayable without
    # pretending that they had a scoped query context.
    op.execute(
        "ALTER TABLE f1.qa_request ADD COLUMN query_context_sha256 text "
        f"NOT NULL DEFAULT '{_LEGACY_QUERY_CONTEXT}'"
    )
    op.execute(
        "ALTER TABLE f1.qa_request ADD CONSTRAINT "
        "qa_request_query_context_sha_ck CHECK "
        "(query_context_sha256 ~ '^[0-9a-f]{64}$')"
    )

    # Prior successful migrations leave the frozen definers owned by their
    # membership-free runtime role.  The bootstrap identity alone may return
    # them to the migration owner for this transactional replacement; the
    # migration runner re-finalizes the exact owner map after reaching head.
    op.execute("RESET ROLE")
    op.execute(
        "ALTER FUNCTION f1.claim_qa_request(uuid,text,integer) "
        "OWNER TO f0d_migration"
    )
    op.execute(
        "ALTER FUNCTION "
        "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text) "
        "OWNER TO f0d_migration"
    )
    op.execute("SET LOCAL ROLE f0d_migration")

    # Keep the two frozen SECURITY DEFINER signatures in place: migrate_f1.py
    # owns their exact owner map.  The new explicit-context overloads below
    # are SECURITY INVOKER wrappers; the legacy overloads use the fixed digest
    # when no wrapper has populated the transaction-local context GUC.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.claim_qa_request(
          p_request_id uuid, p_question_sha256 text, p_lease_seconds integer
        ) RETURNS TABLE(
          claim_state text, owner_token uuid, attempt integer, status text,
          refusal_reason text, response_encrypted bytea, response_sha256 text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
          v_eid uuid; v_token uuid := gen_random_uuid();
          v_row f1.qa_request; v_attempt integer;
          v_context text := COALESCE(
            NULLIF(current_setting('f1.qa_query_context_sha256', true), ''),
            repeat('0', 64)
          );
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR p_request_id IS NULL
             OR p_question_sha256 IS NULL
             OR p_question_sha256 !~ '^[0-9a-f]{64}$'
             OR v_context !~ '^[0-9a-f]{64}$'
             OR p_lease_seconds < 1 OR p_lease_seconds > 900
          THEN RAISE EXCEPTION 'QA_CLAIM_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);

          INSERT INTO f1.qa_request(
            request_id, enterprise_id, question_sha256,
            query_context_sha256, status, owner_token,
            owner_lease_until, attempt
          ) VALUES (
            p_request_id, v_eid, p_question_sha256,
            v_context, 'accepted', v_token,
            statement_timestamp() + make_interval(secs => p_lease_seconds), 1
          ) ON CONFLICT (request_id) DO NOTHING;
          IF FOUND THEN
            RETURN QUERY SELECT 'CLAIMED'::text, v_token, 1, 'accepted'::text,
                                NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;

          SELECT * INTO v_row FROM f1.qa_request AS q
           WHERE q.request_id = p_request_id FOR UPDATE;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'CONFLICT'::text, NULL::uuid, 0, NULL::text,
                                NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          IF v_row.enterprise_id <> v_eid
             OR v_row.question_sha256 <> p_question_sha256
             OR v_row.query_context_sha256 <> v_context THEN
            RETURN QUERY SELECT 'CONFLICT'::text, NULL::uuid, v_row.attempt,
                                NULL::text, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          IF v_row.status IN ('done','refused') THEN
            RETURN QUERY SELECT 'REPLAY'::text, NULL::uuid, v_row.attempt,
                                v_row.status, v_row.refusal_reason,
                                v_row.response_encrypted, v_row.response_sha256;
            RETURN;
          END IF;
          IF v_row.status <> 'accepted'
             OR v_row.owner_lease_until > statement_timestamp() THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
                                v_row.status, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;

          UPDATE f1.qa_request AS q
             SET owner_token = v_token,
                 owner_lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 attempt = q.attempt + 1
           WHERE q.request_id = p_request_id
             AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.query_context_sha256 = v_context
             AND q.status = 'accepted'
             AND (q.owner_lease_until IS NULL
                  OR q.owner_lease_until <= statement_timestamp())
          RETURNING q.attempt INTO v_attempt;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
                                'accepted'::text, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          RETURN QUERY SELECT 'CLAIMED'::text, v_token, v_attempt,
                              'accepted'::text, NULL::text, NULL::bytea, NULL::text;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.complete_qa_request(
          p_request_id uuid, p_owner_token uuid, p_question_sha256 text,
          p_status text, p_response_encrypted bytea, p_response_sha256 text,
          p_refusal_reason text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_eid uuid; v_sub text; v_count integer;
          v_context text := COALESCE(
            NULLIF(current_setting('f1.qa_query_context_sha256', true), ''),
            repeat('0', 64)
          );
        BEGIN
          v_eid := f1.current_enterprise_id(); v_sub := f1.current_sub();
          IF v_eid IS NULL OR v_sub IS NULL OR p_request_id IS NULL
             OR p_owner_token IS NULL OR p_question_sha256 IS NULL
             OR p_question_sha256 !~ '^[0-9a-f]{64}$'
             OR v_context !~ '^[0-9a-f]{64}$'
          THEN RAISE EXCEPTION 'QA_COMPLETE_INVALID'; END IF;
          IF NOT (
            (p_status = 'done' AND p_response_encrypted IS NOT NULL
             AND p_response_sha256 ~ '^[0-9a-f]{64}$'
             AND p_refusal_reason IS NULL) OR
            (p_status = 'refused' AND p_response_encrypted IS NULL
             AND p_response_sha256 IS NULL AND p_refusal_reason IS NOT NULL)
          ) THEN RAISE EXCEPTION 'QA_OUTCOME_STATE_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);
          UPDATE f1.qa_request AS q
             SET status = p_status, owner_token = NULL, owner_lease_until = NULL,
                 response_encrypted = p_response_encrypted,
                 response_sha256 = p_response_sha256,
                 refusal_reason = p_refusal_reason,
                 completed_at = statement_timestamp()
           WHERE q.request_id = p_request_id AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.query_context_sha256 = v_context
             AND q.status = 'accepted' AND q.owner_token = p_owner_token
             AND q.owner_lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count <> 1 THEN RETURN false; END IF;
          INSERT INTO f1.audit_log(
            id, enterprise_id, user_sub, action, resource_type, resource_id, result
          ) VALUES (
            gen_random_uuid(), v_eid, v_sub, 'qa.complete', 'qa_request',
            p_request_id::text, p_status
          );
          RETURN true;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.claim_qa_request(
          p_request_id uuid, p_question_sha256 text,
          p_query_context_sha256 text, p_lease_seconds integer
        ) RETURNS TABLE(
          claim_state text, owner_token uuid, attempt integer, status text,
          refusal_reason text, response_encrypted bytea, response_sha256 text
        ) LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        BEGIN
          IF p_query_context_sha256 IS NULL
             OR p_query_context_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'QA_CLAIM_INVALID';
          END IF;
          PERFORM set_config(
            'f1.qa_query_context_sha256', p_query_context_sha256, true
          );
          RETURN QUERY SELECT * FROM f1.claim_qa_request(
            p_request_id, p_question_sha256, p_lease_seconds
          );
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.complete_qa_request(
          p_request_id uuid, p_owner_token uuid, p_question_sha256 text,
          p_query_context_sha256 text, p_status text,
          p_response_encrypted bytea, p_response_sha256 text,
          p_refusal_reason text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF p_query_context_sha256 IS NULL
             OR p_query_context_sha256 !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'QA_COMPLETE_INVALID';
          END IF;
          PERFORM set_config(
            'f1.qa_query_context_sha256', p_query_context_sha256, true
          );
          RETURN f1.complete_qa_request(
            p_request_id, p_owner_token, p_question_sha256, p_status,
            p_response_encrypted, p_response_sha256, p_refusal_reason
          );
        END $$
        """
    )
    for signature in (
        "f1.claim_qa_request(uuid,text,text,integer)",
        "f1.complete_qa_request(uuid,uuid,text,text,text,bytea,text,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.material_rag_scope_binding (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          knowledge_scope_id uuid NOT NULL,
          backend text NOT NULL,
          dataset_ref_ciphertext bytea,
          dataset_ref_sha256 text,
          dataset_ref_aad_sha256 text,
          status text NOT NULL DEFAULT 'provisioning',
          error_reason text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_rag_binding_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_rag_binding_scope_uq
            UNIQUE (enterprise_id, knowledge_scope_id),
          CONSTRAINT material_rag_binding_scope_enterprise_fk
            FOREIGN KEY (enterprise_id, knowledge_scope_id)
            REFERENCES f1.material_knowledge_scope(enterprise_id, id),
          CONSTRAINT material_rag_binding_backend_ck CHECK (
            backend ~ '^[a-z0-9_.-]{1,40}$'
          ),
          CONSTRAINT material_rag_binding_status_ck CHECK (
            status IN ('provisioning','ready','deleting','failed','deleted')
          ),
          CONSTRAINT material_rag_binding_ref_sha_ck CHECK (
            dataset_ref_sha256 IS NULL
            OR dataset_ref_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_binding_aad_sha_ck CHECK (
            dataset_ref_aad_sha256 IS NULL
            OR dataset_ref_aad_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_binding_error_ck CHECK (
            error_reason IS NULL OR error_reason ~ '^[A-Z0-9_]{1,80}$'
          ),
          CONSTRAINT material_rag_binding_ref_triplet_ck CHECK (
            (dataset_ref_ciphertext IS NULL AND dataset_ref_sha256 IS NULL
             AND dataset_ref_aad_sha256 IS NULL)
            OR
            (octet_length(dataset_ref_ciphertext) BETWEEN 29 AND 4096
             AND dataset_ref_sha256 IS NOT NULL
             AND dataset_ref_aad_sha256 IS NOT NULL)
          ),
          CONSTRAINT material_rag_binding_state_ck CHECK (
            (status IN ('provisioning','deleted')
             AND dataset_ref_ciphertext IS NULL AND error_reason IS NULL)
            OR (status IN ('ready','deleting')
                AND dataset_ref_ciphertext IS NOT NULL
                AND error_reason IS NULL)
            OR (status = 'failed' AND error_reason IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.material_rag_unit (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          knowledge_scope_id uuid NOT NULL,
          document_record_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_sha256 text NOT NULL,
          page_number integer NOT NULL,
          ordinal integer NOT NULL,
          parser_version text NOT NULL,
          ocr_applied boolean NOT NULL DEFAULT false,
          table_candidate boolean NOT NULL DEFAULT false,
          two_column_candidate boolean NOT NULL DEFAULT false,
          body_ciphertext bytea NOT NULL,
          body_sha256 text NOT NULL,
          body_aad_sha256 text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_rag_unit_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_rag_unit_identity_uq UNIQUE (
            enterprise_id, knowledge_scope_id, document_record_id,
            document_version_id, source_sha256, page_number, ordinal,
            parser_version
          ),
          CONSTRAINT material_rag_unit_scope_enterprise_fk
            FOREIGN KEY (enterprise_id, knowledge_scope_id)
            REFERENCES f1.material_knowledge_scope(enterprise_id, id),
          CONSTRAINT material_rag_unit_record_enterprise_fk
            FOREIGN KEY (enterprise_id, document_record_id)
            REFERENCES f1.document_record(enterprise_id, id),
          CONSTRAINT material_rag_unit_version_enterprise_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id),
          CONSTRAINT material_rag_unit_source_sha_ck CHECK (
            source_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_unit_body_sha_ck CHECK (
            body_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_unit_aad_sha_ck CHECK (
            body_aad_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_unit_position_ck CHECK (
            page_number BETWEEN 1 AND 100000 AND ordinal BETWEEN 1 AND 100000
          ),
          CONSTRAINT material_rag_unit_parser_ck CHECK (
            parser_version ~ '^[A-Za-z0-9_.:+-]{1,80}$'
          ),
          CONSTRAINT material_rag_unit_ciphertext_ck CHECK (
            octet_length(body_ciphertext) BETWEEN 29 AND 1048576
          )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX material_rag_binding_dataset_ref_uq "
        "ON f1.material_rag_scope_binding (backend, dataset_ref_sha256) "
        "WHERE dataset_ref_sha256 IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX material_rag_unit_version_idx ON "
        "f1.material_rag_unit(enterprise_id,knowledge_scope_id,"
        "document_version_id,page_number,ordinal)"
    )
    op.execute(
        """
        CREATE TABLE f1.material_rag_job (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          knowledge_scope_id uuid NOT NULL,
          document_record_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          upload_task_id uuid NOT NULL,
          source_sha256 text NOT NULL,
          action text NOT NULL,
          status text NOT NULL DEFAULT 'queued',
          idempotency_sha256 text NOT NULL,
          attempt integer NOT NULL DEFAULT 0,
          lease_token uuid,
          lease_owner text,
          lease_acquired_at timestamptz,
          lease_until timestamptz,
          next_attempt_at timestamptz,
          error_reason text,
          result_manifest_sha256 text,
          indexed_unit_count integer,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_rag_job_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_rag_job_idempotency_uq
            UNIQUE (enterprise_id, idempotency_sha256),
          CONSTRAINT material_rag_job_scope_enterprise_fk
            FOREIGN KEY (enterprise_id, knowledge_scope_id)
            REFERENCES f1.material_knowledge_scope(enterprise_id, id),
          CONSTRAINT material_rag_job_record_enterprise_fk
            FOREIGN KEY (enterprise_id, document_record_id)
            REFERENCES f1.document_record(enterprise_id, id),
          CONSTRAINT material_rag_job_version_enterprise_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id),
          CONSTRAINT material_rag_job_upload_enterprise_fk
            FOREIGN KEY (enterprise_id, upload_task_id)
            REFERENCES f1.upload_task(enterprise_id, id),
          CONSTRAINT material_rag_job_action_ck CHECK (
            action IN ('index','rebuild','delete')
          ),
          CONSTRAINT material_rag_job_status_ck CHECK (
            status IN ('queued','running','retry_wait','done','failed')
          ),
          CONSTRAINT material_rag_job_idempotency_ck CHECK (
            idempotency_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_job_source_sha_ck CHECK (
            source_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_job_attempt_ck CHECK (
            attempt BETWEEN 0 AND 100
          ),
          CONSTRAINT material_rag_job_owner_ck CHECK (
            lease_owner IS NULL OR lease_owner ~ '^[A-Za-z0-9_.:-]{1,128}$'
          ),
          CONSTRAINT material_rag_job_error_ck CHECK (
            error_reason IS NULL OR error_reason ~ '^[A-Z0-9_]{1,80}$'
          ),
          CONSTRAINT material_rag_job_manifest_ck CHECK (
            result_manifest_sha256 IS NULL
            OR result_manifest_sha256 ~ '^[0-9a-f]{64}$'
          ),
          CONSTRAINT material_rag_job_count_ck CHECK (
            indexed_unit_count IS NULL
            OR indexed_unit_count BETWEEN 0 AND 10000000
          ),
          CONSTRAINT material_rag_job_lease_ck CHECK (
            (lease_token IS NULL AND lease_owner IS NULL
             AND lease_acquired_at IS NULL AND lease_until IS NULL)
            OR
            (lease_token IS NOT NULL AND lease_owner IS NOT NULL
             AND lease_acquired_at IS NOT NULL
             AND lease_until > lease_acquired_at)
          ),
          CONSTRAINT material_rag_job_state_ck CHECK (
            (status = 'queued' AND lease_token IS NULL
             AND next_attempt_at IS NULL AND error_reason IS NULL
             AND result_manifest_sha256 IS NULL
             AND indexed_unit_count IS NULL)
            OR (status = 'running' AND lease_token IS NOT NULL
                AND next_attempt_at IS NULL AND error_reason IS NULL
                AND result_manifest_sha256 IS NULL
                AND indexed_unit_count IS NULL)
            OR (status = 'retry_wait' AND lease_token IS NULL
                AND next_attempt_at IS NOT NULL AND error_reason IS NOT NULL
                AND result_manifest_sha256 IS NULL
                AND indexed_unit_count IS NULL)
            OR (status = 'done' AND lease_token IS NULL
                AND next_attempt_at IS NULL AND error_reason IS NULL
                AND result_manifest_sha256 IS NOT NULL
                AND indexed_unit_count IS NOT NULL)
            OR (status = 'failed' AND lease_token IS NULL
                AND next_attempt_at IS NULL AND error_reason IS NOT NULL
                AND result_manifest_sha256 IS NULL
                AND indexed_unit_count IS NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX material_rag_job_due_idx ON "
        "f1.material_rag_job(status,next_attempt_at,created_at,id)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.material_rag_released_version(
          p_enterprise_id uuid, p_scope_id uuid, p_record_id uuid,
          p_version_id uuid, p_source_sha256 text
        ) RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER
        SET search_path = pg_catalog AS $$
          SELECT EXISTS (
            SELECT 1
            FROM f1.document_version AS version
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
            JOIN f1.upload_task AS task
              ON task.enterprise_id = version.enterprise_id
             AND task.id = version.upload_task_id
            WHERE version.enterprise_id = p_enterprise_id
              AND version.id = p_version_id
              AND version.document_record_id = p_record_id
              AND record.knowledge_scope_id = p_scope_id
              AND task.content_sha256 = p_source_sha256
              AND task.pipeline_kind = 'controlled_ingestion'
              AND task.status = 'done'
              AND task.processing_stage = 'ready'
              AND task.object_state = 'ready'
              AND task.scan_verdict = 'clean'
              AND task.preview_status = 'ready'
              AND task.quarantine_status = 'released'
              AND task.released_at IS NOT NULL
          )
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_rag_guard_unit()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'MATERIAL_RAG_UNIT_IMMUTABLE';
          END IF;
          -- The API validated release state when it enqueued the immutable
          -- job identity.  The worker has only the three exact source rows
          -- selected by its live material-job lease; the copied identity must
          -- still match before an immutable unit can be inserted.
          IF session_user <> 'f1_worker' OR NOT EXISTS (
            SELECT 1 FROM f1.material_rag_job AS active_job
            WHERE active_job.id = f1.current_material_rag_job_id()
              AND active_job.lease_token = f1.current_material_rag_lease_token()
              AND active_job.status = 'running'
              AND active_job.lease_until > statement_timestamp()
              AND active_job.action IN ('index','rebuild')
              AND active_job.enterprise_id = NEW.enterprise_id
              AND active_job.knowledge_scope_id = NEW.knowledge_scope_id
              AND active_job.document_record_id = NEW.document_record_id
              AND active_job.document_version_id = NEW.document_version_id
              AND active_job.source_sha256 = NEW.source_sha256
          ) THEN
            RAISE EXCEPTION 'MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_rag_unit_guard BEFORE INSERT OR UPDATE "
        "ON f1.material_rag_unit FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_rag_guard_unit()"
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_rag_guard_job()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_scope_id uuid; v_record_id uuid; v_upload_task_id uuid;
          v_source_sha256 text;
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
               OR NEW.knowledge_scope_id IS DISTINCT FROM OLD.knowledge_scope_id
               OR NEW.document_record_id IS DISTINCT FROM OLD.document_record_id
               OR NEW.document_version_id IS DISTINCT FROM OLD.document_version_id
               OR NEW.upload_task_id IS DISTINCT FROM OLD.upload_task_id
               OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
               OR NEW.action IS DISTINCT FROM OLD.action
               OR NEW.idempotency_sha256 IS DISTINCT FROM OLD.idempotency_sha256
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
              RAISE EXCEPTION 'MATERIAL_RAG_JOB_IDENTITY_IMMUTABLE';
            END IF;
            IF (
              (
                (
                  OLD.status = 'queued'
                  OR (OLD.status = 'retry_wait'
                      AND OLD.next_attempt_at <= statement_timestamp())
                  OR (OLD.status = 'running'
                      AND OLD.lease_until <= statement_timestamp())
                )
                AND NEW.status = 'running'
                AND NEW.lease_token IS NOT NULL
                AND NEW.lease_token = f1.current_material_rag_lease_token()
                AND NEW.lease_owner IS NOT NULL
                AND NEW.lease_acquired_at = statement_timestamp()
                AND NEW.lease_until > statement_timestamp()
                AND NEW.lease_until <= statement_timestamp() + interval '900 seconds'
                AND NEW.attempt = OLD.attempt + 1
                AND NEW.next_attempt_at IS NULL
                AND NEW.error_reason IS NULL
                AND NEW.result_manifest_sha256 IS NULL
                AND NEW.indexed_unit_count IS NULL
              )
              OR (
                OLD.status = 'running'
                AND OLD.lease_token = f1.current_material_rag_lease_token()
                AND OLD.lease_until > statement_timestamp()
                AND NEW.status = 'running'
                AND NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token
                AND NEW.lease_owner IS NOT DISTINCT FROM OLD.lease_owner
                AND NEW.lease_acquired_at IS NOT DISTINCT FROM OLD.lease_acquired_at
                AND NEW.lease_until > statement_timestamp()
                AND NEW.lease_until <= statement_timestamp() + interval '900 seconds'
                AND NEW.next_attempt_at IS NOT DISTINCT FROM OLD.next_attempt_at
                AND NEW.error_reason IS NOT DISTINCT FROM OLD.error_reason
                AND NEW.result_manifest_sha256 IS NOT DISTINCT FROM OLD.result_manifest_sha256
                AND NEW.indexed_unit_count IS NOT DISTINCT FROM OLD.indexed_unit_count
                AND NEW.attempt = OLD.attempt
              )
              OR (
                OLD.status = 'running'
                AND OLD.lease_token = f1.current_material_rag_lease_token()
                AND OLD.lease_until > statement_timestamp()
                AND NEW.status IN ('done','retry_wait','failed')
                AND NEW.lease_token IS NULL
                AND NEW.lease_owner IS NULL
                AND NEW.lease_acquired_at IS NULL
                AND NEW.lease_until IS NULL
                AND NEW.attempt = OLD.attempt
              )
            ) IS NOT TRUE THEN
              RAISE EXCEPTION 'MATERIAL_RAG_JOB_TRANSITION_INVALID';
            END IF;
            NEW.updated_at := statement_timestamp();
            RETURN NEW;
          END IF;
          SELECT record.knowledge_scope_id, version.document_record_id,
                 version.upload_task_id, task.content_sha256
            INTO v_scope_id, v_record_id, v_upload_task_id, v_source_sha256
          FROM f1.document_version AS version
          JOIN f1.document_record AS record
            ON record.enterprise_id = version.enterprise_id
           AND record.id = version.document_record_id
          JOIN f1.upload_task AS task
            ON task.enterprise_id = version.enterprise_id
           AND task.id = version.upload_task_id
          WHERE version.enterprise_id = NEW.enterprise_id
            AND version.id = NEW.document_version_id
            AND task.pipeline_kind = 'controlled_ingestion';
          IF v_record_id IS NULL OR (
            NEW.action <> 'delete'
            AND NOT f1.material_rag_released_version(
              NEW.enterprise_id, NEW.knowledge_scope_id, v_record_id,
              NEW.document_version_id, v_source_sha256
            )
          ) THEN
            RAISE EXCEPTION 'MATERIAL_RAG_JOB_SOURCE_NOT_RELEASED';
          END IF;
          IF NEW.knowledge_scope_id IS DISTINCT FROM v_scope_id
             OR NEW.document_record_id IS DISTINCT FROM v_record_id
             OR NEW.upload_task_id IS DISTINCT FROM v_upload_task_id
             OR NEW.source_sha256 IS DISTINCT FROM v_source_sha256 THEN
            RAISE EXCEPTION 'MATERIAL_RAG_JOB_SOURCE_IDENTITY_INVALID';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_rag_job_guard BEFORE INSERT OR UPDATE "
        "ON f1.material_rag_job FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_rag_guard_job()"
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_rag_guard_binding()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
               OR NEW.knowledge_scope_id IS DISTINCT FROM OLD.knowledge_scope_id
               OR NEW.backend IS DISTINCT FROM OLD.backend
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
              RAISE EXCEPTION 'MATERIAL_RAG_BINDING_IDENTITY_IMMUTABLE';
            END IF;
            NEW.updated_at := statement_timestamp();
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_rag_binding_guard BEFORE UPDATE "
        "ON f1.material_rag_scope_binding FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_rag_guard_binding()"
    )
    for signature in (
        "f1.material_rag_released_version(uuid,uuid,uuid,uuid,text)",
        "f1.material_rag_guard_unit()",
        "f1.material_rag_guard_job()",
        "f1.material_rag_guard_binding()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.material_rag_released_version(uuid,uuid,uuid,uuid,text) "
        "TO f1_api, f1_worker"
    )
    for signature in (
        "f1.material_rag_guard_unit()",
        "f1.material_rag_guard_job()",
        "f1.material_rag_guard_binding()",
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api, f1_worker")


def _job_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.current_material_rag_job_id()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT CASE
            WHEN current_setting('f1.material_rag_job_id', true)
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN current_setting('f1.material_rag_job_id', true)::uuid
            ELSE NULL
          END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.current_material_rag_lease_token()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT CASE
            WHEN current_setting('f1.material_rag_lease_token', true)
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN current_setting('f1.material_rag_lease_token', true)::uuid
            ELSE NULL
          END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.claim_material_rag_job(
          p_job_id uuid, p_worker_id text, p_lease_seconds integer
        ) RETURNS TABLE(
          enterprise_id uuid, job_id uuid, lease_token uuid,
          knowledge_scope_id uuid, document_record_id uuid,
          document_version_id uuid, upload_task_id uuid,
          source_sha256 text, action text,
          attempt integer
        ) LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $$
        DECLARE v_token uuid := gen_random_uuid();
        BEGIN
          IF p_job_id IS NULL
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9_.:-]{1,128}$'
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 1 OR p_lease_seconds > 900 THEN
            RAISE EXCEPTION 'MATERIAL_RAG_JOB_CLAIM_INVALID';
          END IF;
          PERFORM set_config('f1.material_rag_job_id', p_job_id::text, true);
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
           WHERE job.id = p_job_id AND job.attempt < 100
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
        """
        CREATE FUNCTION f1.renew_material_rag_job_lease(
          p_job_id uuid, p_lease_token uuid, p_lease_seconds integer
        ) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF p_job_id IS NULL OR p_lease_token IS NULL
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 1 OR p_lease_seconds > 900 THEN
            RETURN false;
          END IF;
          PERFORM set_config('f1.material_rag_job_id', p_job_id::text, true);
          PERFORM set_config(
            'f1.material_rag_lease_token', p_lease_token::text, true
          );
          UPDATE f1.material_rag_job AS job
             SET lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 updated_at = statement_timestamp()
           WHERE job.id = p_job_id AND job.status = 'running'
             AND job.lease_token = p_lease_token
             AND job.lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count = 1;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.finish_material_rag_job(
          p_job_id uuid, p_lease_token uuid, p_outcome text,
          p_result_manifest_sha256 text, p_indexed_unit_count integer,
          p_error_reason text, p_retry_seconds integer
        ) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF p_job_id IS NULL OR p_lease_token IS NULL
             OR p_outcome IS NULL OR p_retry_seconds IS NULL THEN
            RETURN false;
          END IF;
          IF NOT (
            (p_outcome = 'done'
             AND p_result_manifest_sha256 ~ '^[0-9a-f]{64}$'
             AND p_indexed_unit_count BETWEEN 0 AND 10000000
             AND p_error_reason IS NULL AND p_retry_seconds = 0)
            OR (p_outcome = 'retry_wait'
                AND p_result_manifest_sha256 IS NULL
                AND p_indexed_unit_count IS NULL
                AND p_error_reason ~ '^[A-Z0-9_]{1,80}$'
                AND p_retry_seconds BETWEEN 1 AND 86400)
            OR (p_outcome = 'failed'
                AND p_result_manifest_sha256 IS NULL
                AND p_indexed_unit_count IS NULL
                AND p_error_reason ~ '^[A-Z0-9_]{1,80}$'
                AND p_retry_seconds = 0)
          ) THEN RAISE EXCEPTION 'MATERIAL_RAG_JOB_OUTCOME_INVALID'; END IF;
          PERFORM set_config('f1.material_rag_job_id', p_job_id::text, true);
          PERFORM set_config(
            'f1.material_rag_lease_token', p_lease_token::text, true
          );
          UPDATE f1.material_rag_job AS job
             SET status = p_outcome,
                 lease_token = NULL, lease_owner = NULL,
                 lease_acquired_at = NULL, lease_until = NULL,
                 next_attempt_at = CASE WHEN p_outcome = 'retry_wait'
                   THEN statement_timestamp()
                     + make_interval(secs => p_retry_seconds)
                   ELSE NULL END,
                 error_reason = p_error_reason,
                 result_manifest_sha256 = p_result_manifest_sha256,
                 indexed_unit_count = p_indexed_unit_count,
                 updated_at = statement_timestamp()
           WHERE job.id = p_job_id AND job.status = 'running'
             AND job.lease_token = p_lease_token
             AND job.lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count = 1;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.prepare_empty_material_rag_scope(
          p_job_id uuid, p_lease_token uuid, p_dataset_ref_sha256 text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_enterprise_id uuid; v_scope_id uuid; v_count integer;
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_job_id IS NULL OR p_lease_token IS NULL
             OR p_dataset_ref_sha256 IS NULL
             OR p_dataset_ref_sha256 !~ '^[0-9a-f]{64}$' THEN
            RETURN false;
          END IF;
          PERFORM set_config('f1.material_rag_job_id', p_job_id::text, true);
          PERFORM set_config(
            'f1.material_rag_lease_token', p_lease_token::text, true
          );
          SELECT job.enterprise_id, job.knowledge_scope_id
            INTO v_enterprise_id, v_scope_id
            FROM f1.material_rag_job AS job
           WHERE job.id = p_job_id AND job.action = 'delete'
             AND job.status = 'running' AND job.lease_token = p_lease_token
             AND job.lease_until > statement_timestamp()
           FOR UPDATE;
          IF NOT FOUND OR EXISTS (
            SELECT 1 FROM f1.material_rag_unit AS unit
             WHERE unit.enterprise_id = v_enterprise_id
               AND unit.knowledge_scope_id = v_scope_id
          ) THEN
            RETURN false;
          END IF;
          UPDATE f1.material_rag_scope_binding AS binding
             SET status = 'deleting'
           WHERE binding.enterprise_id = v_enterprise_id
             AND binding.knowledge_scope_id = v_scope_id
             AND binding.backend = 'ragflow'
             AND binding.status IN ('ready','deleting')
             AND binding.dataset_ref_sha256 = p_dataset_ref_sha256;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count = 1;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.finalize_empty_material_rag_scope(
          p_job_id uuid, p_lease_token uuid, p_dataset_ref_sha256 text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_enterprise_id uuid; v_scope_id uuid; v_count integer;
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_job_id IS NULL OR p_lease_token IS NULL
             OR p_dataset_ref_sha256 IS NULL
             OR p_dataset_ref_sha256 !~ '^[0-9a-f]{64}$' THEN
            RETURN false;
          END IF;
          PERFORM set_config('f1.material_rag_job_id', p_job_id::text, true);
          PERFORM set_config(
            'f1.material_rag_lease_token', p_lease_token::text, true
          );
          SELECT job.enterprise_id, job.knowledge_scope_id
            INTO v_enterprise_id, v_scope_id
            FROM f1.material_rag_job AS job
           WHERE job.id = p_job_id AND job.action = 'delete'
             AND job.status = 'running' AND job.lease_token = p_lease_token
             AND job.lease_until > statement_timestamp()
           FOR UPDATE;
          IF NOT FOUND OR EXISTS (
            SELECT 1 FROM f1.material_rag_unit AS unit
             WHERE unit.enterprise_id = v_enterprise_id
               AND unit.knowledge_scope_id = v_scope_id
          ) THEN
            RETURN false;
          END IF;
          UPDATE f1.material_rag_scope_binding AS binding
             SET status = 'deleted', dataset_ref_ciphertext = NULL,
                 dataset_ref_sha256 = NULL, dataset_ref_aad_sha256 = NULL,
                 error_reason = NULL
           WHERE binding.enterprise_id = v_enterprise_id
             AND binding.knowledge_scope_id = v_scope_id
             AND binding.backend = 'ragflow'
             AND binding.status = 'deleting'
             AND binding.dataset_ref_sha256 = p_dataset_ref_sha256;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count = 1;
        END $$
        """
    )
    for signature in (
        "f1.current_material_rag_job_id()",
        "f1.current_material_rag_lease_token()",
        "f1.claim_material_rag_job(uuid,text,integer)",
        "f1.renew_material_rag_job_lease(uuid,uuid,integer)",
        "f1.finish_material_rag_job(uuid,uuid,text,text,integer,text,integer)",
        "f1.prepare_empty_material_rag_scope(uuid,uuid,text)",
        "f1.finalize_empty_material_rag_scope(uuid,uuid,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_worker")


def _actor_clause(enterprise: str, scope_kind: str, client_id: str) -> str:
    return f"""
      EXISTS (
        SELECT 1
        FROM f1.enterprise_user AS scope_actor
        JOIN f1.user_profile AS scope_profile
          ON scope_profile.id = scope_actor.user_id
        WHERE scope_actor.enterprise_id = {enterprise}
          AND scope_profile.keycloak_sub = f1.current_sub()
          AND scope_actor.role IN (
            'super_admin','enterprise_admin','plant_admin'
          )
          AND (
            {scope_kind} = 'service_provider'
            OR scope_actor.role IN ('super_admin','enterprise_admin')
            OR (
              scope_actor.role = 'plant_admin'
              AND EXISTS (
                SELECT 1 FROM f1.crm_account AS owned_account
                WHERE owned_account.enterprise_id = {enterprise}
                  AND owned_account.id = {client_id}
                  AND owned_account.owner_user_id = scope_actor.user_id
              )
            )
          )
      )
    """


def _api_scope(alias: str) -> str:
    actor = _actor_clause(
        "visible_scope.enterprise_id",
        "visible_scope.scope_kind",
        "visible_scope.client_account_id",
    )
    return f"""
      {alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND EXISTS (
        SELECT 1
        FROM f1.material_knowledge_scope AS visible_scope
        WHERE visible_scope.enterprise_id = {alias}.enterprise_id
          AND visible_scope.id = {alias}.knowledge_scope_id
          AND {actor}
      )
    """


def _worker_job(
    alias: str, *, same_version: bool, same_source: bool = False
) -> str:
    version = (
        f"AND active_job.document_version_id = {alias}.document_version_id"
        if same_version
        else ""
    )
    source = (
        f"AND active_job.document_record_id = {alias}.document_record_id "
        f"AND active_job.source_sha256 = {alias}.source_sha256"
        if same_source
        else ""
    )
    return f"""
      EXISTS (
        SELECT 1 FROM f1.material_rag_job AS active_job
        WHERE active_job.id = f1.current_material_rag_job_id()
          AND active_job.lease_token = f1.current_material_rag_lease_token()
          AND active_job.status = 'running'
          AND active_job.lease_until > statement_timestamp()
          AND active_job.enterprise_id = {alias}.enterprise_id
          AND active_job.knowledge_scope_id = {alias}.knowledge_scope_id
          {version}
          {source}
      )
    """


def _rls_and_grants() -> None:
    for table in (
        "material_rag_scope_binding",
        "material_rag_unit",
        "material_rag_job",
    ):
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")

    binding_api = _api_scope("material_rag_scope_binding")
    unit_api = _api_scope("material_rag_unit")
    job_api = _api_scope("material_rag_job")
    op.execute(
        "CREATE POLICY material_rag_binding_api_select ON "
        "f1.material_rag_scope_binding FOR SELECT TO f1_api USING ("
        + binding_api
        + ")"
    )
    op.execute(
        "CREATE POLICY material_rag_unit_api_select ON f1.material_rag_unit "
        "FOR SELECT TO f1_api USING (" + unit_api + ")"
    )
    op.execute(
        "CREATE POLICY material_rag_job_api_select ON f1.material_rag_job "
        "FOR SELECT TO f1_api USING (" + job_api + ")"
    )
    op.execute(
        "CREATE POLICY material_rag_job_api_insert ON f1.material_rag_job "
        "FOR INSERT TO f1_api WITH CHECK (" + job_api + ")"
    )

    # The queue hands the worker one opaque UUID.  Before a lease exists the
    # worker can see/update only that target row; after claim, token+expiry
    # constrain every binding/unit access to the claimed scope and version.
    job_target = """
      id = f1.current_material_rag_job_id()
      AND (
        status = 'queued'
        OR (status = 'retry_wait'
            AND next_attempt_at <= statement_timestamp())
        OR (
          status = 'running'
          AND (
            lease_until <= statement_timestamp()
            OR (
              lease_token = f1.current_material_rag_lease_token()
              AND lease_until > statement_timestamp()
            )
          )
        )
      )
    """
    job_after_update = """
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
    """
    op.execute(
        "CREATE POLICY material_rag_job_worker_select ON f1.material_rag_job "
        "FOR SELECT TO f1_worker USING (" + job_target + ")"
    )
    op.execute(
        "CREATE POLICY material_rag_job_worker_update ON f1.material_rag_job "
        "FOR UPDATE TO f1_worker USING (" + job_target + ") WITH CHECK ("
        + job_after_update + ")"
    )

    # A material worker gets a live, read-only view of exactly the P3 source
    # chain frozen into its claimed job.  These policies deliberately do not
    # require the upload to remain released: index/rebuild code rechecks that
    # lifecycle before every remote mutation, while delete must still be able
    # to remove remote/local residue after a source is revoked.  Reading the
    # three rows as one join proves enterprise/scope/record/version/source;
    # none of the individual policies opens a tenant-wide P3 view.
    source_record_worker = """
      session_user = 'f1_worker'
      AND EXISTS (
        SELECT 1
        FROM f1.material_rag_job AS source_job
        JOIN f1.document_version AS source_version
          ON source_version.enterprise_id = source_job.enterprise_id
         AND source_version.id = source_job.document_version_id
         AND source_version.document_record_id = source_job.document_record_id
         AND source_version.upload_task_id = source_job.upload_task_id
        JOIN f1.upload_task AS source_task
          ON source_task.enterprise_id = source_job.enterprise_id
         AND source_task.id = source_job.upload_task_id
         AND source_task.content_sha256 = source_job.source_sha256
         AND source_task.pipeline_kind = 'controlled_ingestion'
        WHERE source_job.id = f1.current_material_rag_job_id()
          AND source_job.lease_token = f1.current_material_rag_lease_token()
          AND source_job.status = 'running'
          AND source_job.lease_until > statement_timestamp()
          AND source_job.enterprise_id = document_record.enterprise_id
          AND source_job.knowledge_scope_id =
              document_record.knowledge_scope_id
          AND source_job.document_record_id = document_record.id
      )
    """
    op.execute(
        "CREATE POLICY material_rag_source_record_worker_select ON "
        "f1.document_record FOR SELECT TO f1_worker USING ("
        + source_record_worker
        + ")"
    )
    source_version_worker = """
      session_user = 'f1_worker'
      AND EXISTS (
        SELECT 1
        FROM f1.material_rag_job AS source_job
        JOIN f1.upload_task AS source_task
          ON source_task.enterprise_id = source_job.enterprise_id
         AND source_task.id = source_job.upload_task_id
         AND source_task.content_sha256 = source_job.source_sha256
         AND source_task.pipeline_kind = 'controlled_ingestion'
        WHERE source_job.id = f1.current_material_rag_job_id()
          AND source_job.lease_token = f1.current_material_rag_lease_token()
          AND source_job.status = 'running'
          AND source_job.lease_until > statement_timestamp()
          AND source_job.enterprise_id = document_version.enterprise_id
          AND source_job.document_record_id =
              document_version.document_record_id
          AND source_job.document_version_id = document_version.id
          AND source_job.upload_task_id = document_version.upload_task_id
      )
    """
    op.execute(
        "CREATE POLICY material_rag_source_version_worker_select ON "
        "f1.document_version FOR SELECT TO f1_worker USING ("
        + source_version_worker
        + ")"
    )
    source_upload_worker = """
      session_user = 'f1_worker'
      AND EXISTS (
        SELECT 1 FROM f1.material_rag_job AS source_job
        WHERE source_job.id = f1.current_material_rag_job_id()
          AND source_job.lease_token = f1.current_material_rag_lease_token()
          AND source_job.status = 'running'
          AND source_job.lease_until > statement_timestamp()
          AND source_job.enterprise_id = upload_task.enterprise_id
          AND source_job.upload_task_id = upload_task.id
          AND source_job.source_sha256 = upload_task.content_sha256
          AND upload_task.pipeline_kind = 'controlled_ingestion'
      )
    """
    op.execute(
        "CREATE POLICY material_rag_source_upload_worker_select ON "
        "f1.upload_task FOR SELECT TO f1_worker USING ("
        + source_upload_worker
        + ")"
    )
    binding_worker = _worker_job(
        "material_rag_scope_binding", same_version=False
    )
    op.execute(
        "CREATE POLICY material_rag_binding_worker_all ON "
        "f1.material_rag_scope_binding FOR ALL TO f1_worker USING ("
        + binding_worker
        + ") WITH CHECK ("
        + binding_worker
        + ")"
    )
    unit_worker = _worker_job(
        "material_rag_unit", same_version=True, same_source=True
    )
    op.execute(
        "CREATE POLICY material_rag_unit_worker_select ON f1.material_rag_unit "
        "FOR SELECT TO f1_worker USING (" + unit_worker + ")"
    )
    # The final delete job must count every sibling unit in its exact scope.
    # It receives no scope parameter: both the scope and enterprise come from
    # the current live delete lease, so this does not open tenant-wide reads.
    scope_delete_worker = """
      session_user = 'f1_worker'
      AND EXISTS (
        SELECT 1 FROM f1.material_rag_job AS delete_job
        WHERE delete_job.id = f1.current_material_rag_job_id()
          AND delete_job.lease_token = f1.current_material_rag_lease_token()
          AND delete_job.status = 'running'
          AND delete_job.action = 'delete'
          AND delete_job.lease_until > statement_timestamp()
          AND delete_job.enterprise_id = material_rag_unit.enterprise_id
          AND delete_job.knowledge_scope_id =
              material_rag_unit.knowledge_scope_id
      )
    """
    op.execute(
        "CREATE POLICY material_rag_unit_scope_delete_worker_select ON "
        "f1.material_rag_unit FOR SELECT TO f1_worker USING ("
        + scope_delete_worker
        + ")"
    )
    op.execute(
        "CREATE POLICY material_rag_unit_worker_insert ON f1.material_rag_unit "
        "FOR INSERT TO f1_worker WITH CHECK (" + unit_worker
        + " AND EXISTS (SELECT 1 FROM f1.material_rag_job AS insert_job "
        "WHERE insert_job.id=f1.current_material_rag_job_id() "
        "AND insert_job.action IN ('index','rebuild')))"
    )
    op.execute(
        "CREATE POLICY material_rag_unit_worker_delete ON f1.material_rag_unit "
        "FOR DELETE TO f1_worker USING (" + unit_worker
        + " AND EXISTS (SELECT 1 FROM f1.material_rag_job AS delete_job "
        "WHERE delete_job.id=f1.current_material_rag_job_id() "
        "AND delete_job.action IN ('rebuild','delete')))"
    )

    op.execute(
        "GRANT SELECT ON f1.material_rag_scope_binding, "
        "f1.material_rag_unit, f1.material_rag_job TO f1_api"
    )
    op.execute("GRANT INSERT ON f1.material_rag_job TO f1_api")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "f1.material_rag_scope_binding TO f1_worker"
    )
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON f1.material_rag_unit TO f1_worker"
    )
    op.execute(
        "GRANT SELECT, UPDATE ON f1.material_rag_job TO f1_worker"
    )
    op.execute(
        "GRANT SELECT ON f1.document_record, f1.document_version, "
        "f1.upload_task TO f1_worker"
    )
    op.execute(
        "REVOKE ALL ON f1.material_rag_scope_binding, f1.material_rag_unit, "
        "f1.material_rag_job FROM PUBLIC"
    )
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON f1.material_rag_scope_binding, "
        "f1.material_rag_unit FROM f1_api"
    )
    op.execute("REVOKE UPDATE, DELETE ON f1.material_rag_job FROM f1_api")
    op.execute("REVOKE INSERT, DELETE ON f1.material_rag_job FROM f1_worker")


def downgrade() -> None:
    # Refuse before destructive DDL if the new schema contains information
    # that f1_0014 cannot represent.
    op.execute("RESET ROLE")
    op.execute(
        f"""
        DO $material_rag_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.material_rag_scope_binding)
             OR EXISTS (SELECT 1 FROM f1.material_rag_unit)
             OR EXISTS (SELECT 1 FROM f1.material_rag_job)
             OR EXISTS (
               SELECT 1 FROM f1.qa_request
               WHERE query_context_sha256 <> '{_LEGACY_QUERY_CONTEXT}'
             ) THEN
            RAISE EXCEPTION 'MATERIAL_RAG_DOWNGRADE_DATA_PRESENT';
          END IF;
        END
        $material_rag_downgrade$
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")
    for policy, table in (
        ("material_rag_source_upload_worker_select", "upload_task"),
        ("material_rag_source_version_worker_select", "document_version"),
        ("material_rag_source_record_worker_select", "document_record"),
    ):
        op.execute(f"DROP POLICY {policy} ON f1.{table}")
    # f1_0006 intentionally left document_record with no worker privilege;
    # document_version/upload_task SELECT predate this migration and remain.
    op.execute("REVOKE SELECT ON f1.document_record FROM f1_worker")
    # Drop the explicit empty-scope helpers first.  The remaining current-job
    # helpers are still referenced by RLS policies on the three material RAG
    # tables and cannot be removed until those tables (and their policies) are
    # gone.
    for signature in (
        "f1.finalize_empty_material_rag_scope(uuid,uuid,text)",
        "f1.prepare_empty_material_rag_scope(uuid,uuid,text)",
    ):
        op.execute(f"DROP FUNCTION {signature}")
    op.execute(
        "DROP TRIGGER material_rag_binding_guard "
        "ON f1.material_rag_scope_binding"
    )
    op.execute("DROP FUNCTION f1.material_rag_guard_binding()")
    op.execute("DROP TRIGGER material_rag_job_guard ON f1.material_rag_job")
    op.execute("DROP FUNCTION f1.material_rag_guard_job()")
    op.execute("DROP TRIGGER material_rag_unit_guard ON f1.material_rag_unit")
    op.execute("DROP FUNCTION f1.material_rag_guard_unit()")
    # Unit/binding worker policies select the active job row, so remove those
    # policy-owning tables before their referenced job table.
    op.execute("DROP TABLE f1.material_rag_unit")
    op.execute("DROP TABLE f1.material_rag_scope_binding")
    op.execute("DROP TABLE f1.material_rag_job")
    for signature in (
        "f1.finish_material_rag_job(uuid,uuid,text,text,integer,text,integer)",
        "f1.renew_material_rag_job_lease(uuid,uuid,integer)",
        "f1.claim_material_rag_job(uuid,text,integer)",
        "f1.current_material_rag_lease_token()",
        "f1.current_material_rag_job_id()",
    ):
        op.execute(f"DROP FUNCTION {signature}")
    op.execute(
        "DROP FUNCTION "
        "f1.material_rag_released_version(uuid,uuid,uuid,uuid,text)"
    )
    op.execute(
        "DROP FUNCTION "
        "f1.complete_qa_request(uuid,uuid,text,text,text,bytea,text,text)"
    )
    op.execute("DROP FUNCTION f1.claim_qa_request(uuid,text,text,integer)")
    op.execute("RESET ROLE")
    op.execute(
        "ALTER FUNCTION f1.claim_qa_request(uuid,text,integer) "
        "OWNER TO f0d_migration"
    )
    op.execute(
        "ALTER FUNCTION "
        "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text) "
        "OWNER TO f0d_migration"
    )
    op.execute("SET LOCAL ROLE f0d_migration")
    _restore_legacy_qa_functions()
    op.execute("RESET ROLE")
    op.execute(
        "ALTER FUNCTION f1.claim_qa_request(uuid,text,integer) "
        "OWNER TO f1_qa_definer"
    )
    op.execute(
        "ALTER FUNCTION "
        "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text) "
        "OWNER TO f1_qa_definer"
    )
    op.execute("SET LOCAL ROLE f0d_migration")
    op.execute(
        "ALTER TABLE f1.qa_request DROP CONSTRAINT "
        "qa_request_query_context_sha_ck"
    )
    op.execute("ALTER TABLE f1.qa_request DROP COLUMN query_context_sha256")


def _restore_legacy_qa_functions() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.claim_qa_request(
          p_request_id uuid, p_question_sha256 text, p_lease_seconds integer
        ) RETURNS TABLE(
          claim_state text, owner_token uuid, attempt integer, status text,
          refusal_reason text, response_encrypted bytea, response_sha256 text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
          v_eid uuid; v_token uuid := gen_random_uuid();
          v_row f1.qa_request; v_attempt integer;
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR p_request_id IS NULL
             OR p_question_sha256 !~ '^[0-9a-f]{64}$'
             OR p_lease_seconds < 1 OR p_lease_seconds > 900
          THEN RAISE EXCEPTION 'QA_CLAIM_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);
          INSERT INTO f1.qa_request(
            request_id, enterprise_id, question_sha256, status, owner_token,
            owner_lease_until, attempt
          ) VALUES (
            p_request_id, v_eid, p_question_sha256, 'accepted', v_token,
            statement_timestamp() + make_interval(secs => p_lease_seconds), 1
          ) ON CONFLICT (request_id) DO NOTHING;
          IF FOUND THEN
            RETURN QUERY SELECT 'CLAIMED'::text, v_token, 1, 'accepted'::text,
                                NULL::text, NULL::bytea, NULL::text; RETURN;
          END IF;
          SELECT * INTO v_row FROM f1.qa_request AS q
           WHERE q.request_id = p_request_id FOR UPDATE;
          IF NOT FOUND OR v_row.enterprise_id <> v_eid
             OR v_row.question_sha256 <> p_question_sha256 THEN
            RETURN QUERY SELECT 'CONFLICT'::text, NULL::uuid,
              COALESCE(v_row.attempt, 0), NULL::text, NULL::text,
              NULL::bytea, NULL::text; RETURN;
          END IF;
          IF v_row.status IN ('done','refused') THEN
            RETURN QUERY SELECT 'REPLAY'::text, NULL::uuid, v_row.attempt,
              v_row.status, v_row.refusal_reason,
              v_row.response_encrypted, v_row.response_sha256; RETURN;
          END IF;
          IF v_row.status <> 'accepted'
             OR v_row.owner_lease_until > statement_timestamp() THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
              v_row.status, NULL::text, NULL::bytea, NULL::text; RETURN;
          END IF;
          UPDATE f1.qa_request AS q
             SET owner_token = v_token,
                 owner_lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 attempt = q.attempt + 1
           WHERE q.request_id = p_request_id AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.status = 'accepted'
             AND (q.owner_lease_until IS NULL
                  OR q.owner_lease_until <= statement_timestamp())
          RETURNING q.attempt INTO v_attempt;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
              'accepted'::text, NULL::text, NULL::bytea, NULL::text; RETURN;
          END IF;
          RETURN QUERY SELECT 'CLAIMED'::text, v_token, v_attempt,
            'accepted'::text, NULL::text, NULL::bytea, NULL::text;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.complete_qa_request(
          p_request_id uuid, p_owner_token uuid, p_question_sha256 text,
          p_status text, p_response_encrypted bytea, p_response_sha256 text,
          p_refusal_reason text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE v_eid uuid; v_sub text; v_count integer;
        BEGIN
          v_eid := f1.current_enterprise_id(); v_sub := f1.current_sub();
          IF v_eid IS NULL OR v_sub IS NULL OR p_request_id IS NULL
             OR p_owner_token IS NULL OR p_question_sha256 !~ '^[0-9a-f]{64}$'
          THEN RAISE EXCEPTION 'QA_COMPLETE_INVALID'; END IF;
          IF NOT (
            (p_status = 'done' AND p_response_encrypted IS NOT NULL
             AND p_response_sha256 ~ '^[0-9a-f]{64}$'
             AND p_refusal_reason IS NULL) OR
            (p_status = 'refused' AND p_response_encrypted IS NULL
             AND p_response_sha256 IS NULL AND p_refusal_reason IS NOT NULL)
          ) THEN RAISE EXCEPTION 'QA_OUTCOME_STATE_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);
          UPDATE f1.qa_request AS q
             SET status = p_status, owner_token = NULL, owner_lease_until = NULL,
                 response_encrypted = p_response_encrypted,
                 response_sha256 = p_response_sha256,
                 refusal_reason = p_refusal_reason,
                 completed_at = statement_timestamp()
           WHERE q.request_id = p_request_id AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.status = 'accepted' AND q.owner_token = p_owner_token
             AND q.owner_lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count <> 1 THEN RETURN false; END IF;
          INSERT INTO f1.audit_log(
            id, enterprise_id, user_sub, action, resource_type, resource_id, result
          ) VALUES (
            gen_random_uuid(), v_eid, v_sub, 'qa.complete', 'qa_request',
            p_request_id::text, p_status
          );
          RETURN true;
        END $$
        """
    )
