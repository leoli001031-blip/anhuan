"""Add the local blind Fixture annotation workflow.

Revision ID: f0d_0005
Revises: f0d_0004
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0005"
down_revision: str | None = "f0d_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("annotation_guideline", "blind_assignment")


def upgrade() -> None:
    op.execute("CREATE SCHEMA f0g AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f0g FROM PUBLIC")

    op.execute(
        """
        CREATE TABLE f0g.annotation_guideline (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          created_by_actor_id uuid NOT NULL,
          guideline_version text NOT NULL
            CHECK (guideline_version = 'f0g_fixture_blind_v1'),
          guideline_sha256 char(64) NOT NULL
            CHECK (guideline_sha256 =
              '676e18381e44b3cc4e0782ef0d947f1f0b2c4ec3d19af688f9810abaad9d2a64'
            ),
          normalization_rule text NOT NULL DEFAULT 'UTF8_NFC_LF_V1'
            CHECK (normalization_rule = 'UTF8_NFC_LF_V1'),
          workflow_status text NOT NULL DEFAULT 'HUMAN_LABELS_REQUIRED'
            CHECK (workflow_status = 'HUMAN_LABELS_REQUIRED'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          acceptance_gold boolean NOT NULL DEFAULT false
            CHECK (NOT acceptance_gold),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          public_display_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT public_display_allowed),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT annotation_guideline_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT annotation_guideline_version_uq UNIQUE (
            enterprise_id, guideline_version
          ),
          CONSTRAINT annotation_guideline_identity_uq UNIQUE (
            enterprise_id, id, guideline_sha256
          ),
          CONSTRAINT annotation_guideline_actor_fk FOREIGN KEY (
            enterprise_id, created_by_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0g.blind_assignment (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          annotation_queue_id uuid NOT NULL,
          guideline_id uuid NOT NULL,
          guideline_sha256 char(64) NOT NULL
            CHECK (guideline_sha256 ~ '^[0-9a-f]{64}$'),
          annotator_one_actor_id uuid NOT NULL,
          annotator_two_actor_id uuid NOT NULL,
          adjudicator_actor_id uuid NOT NULL,
          assignment_status text NOT NULL DEFAULT 'HUMAN_LABELS_REQUIRED'
            CHECK (assignment_status = 'HUMAN_LABELS_REQUIRED'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          acceptance_gold boolean NOT NULL DEFAULT false
            CHECK (NOT acceptance_gold),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          public_display_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT public_display_allowed),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT blind_assignment_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT blind_assignment_queue_uq UNIQUE (
            enterprise_id, annotation_queue_id
          ),
          CONSTRAINT blind_assignment_identity_uq UNIQUE (
            enterprise_id, id, annotation_queue_id
          ),
          CONSTRAINT blind_assignment_guideline_fk FOREIGN KEY (
            enterprise_id, guideline_id, guideline_sha256
          ) REFERENCES f0g.annotation_guideline(
            enterprise_id, id, guideline_sha256
          ),
          CONSTRAINT blind_assignment_queue_fk FOREIGN KEY (
            enterprise_id, annotation_queue_id
          ) REFERENCES f0f.gold_annotation_queue(enterprise_id, id),
          CONSTRAINT blind_assignment_annotator_one_fk FOREIGN KEY (
            enterprise_id, annotator_one_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT blind_assignment_annotator_two_fk FOREIGN KEY (
            enterprise_id, annotator_two_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT blind_assignment_adjudicator_fk FOREIGN KEY (
            enterprise_id, adjudicator_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT blind_assignment_distinct_actors_ck CHECK (
            annotator_one_actor_id <> annotator_two_actor_id
            AND adjudicator_actor_id <> annotator_one_actor_id
            AND adjudicator_actor_id <> annotator_two_actor_id
          )
        )
        """
    )

    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER reject_immutable_row_mutation BEFORE UPDATE OR DELETE "
            f"ON f0g.{table} FOR EACH ROW EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER reject_immutable_truncate BEFORE TRUNCATE "
            f"ON f0g.{table} FOR EACH STATEMENT EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(f"ALTER TABLE f0g.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f0g.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f0g.{table}
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
        op.execute(
            f"""
            CREATE POLICY migration_f0g_read ON f0g.{table}
            FOR SELECT TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY migration_f0g_insert ON f0g.{table}
            FOR INSERT TO f0d_migration
            WITH CHECK (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY migration_f0g_update_probe ON f0g.{table}
            FOR UPDATE TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            WITH CHECK (false)
            """
        )
        op.execute(
            f"""
            CREATE POLICY migration_f0g_delete_probe ON f0g.{table}
            FOR DELETE TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )

    _create_functions()
    _lock_down_privileges()


def _create_functions() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.prepare_annotation_workflow(
          p_guideline_id uuid,
          p_guideline_version text,
          p_guideline_sha256 text,
          p_assignment_ids uuid[],
          p_annotation_queue_ids uuid[],
          p_annotator_one_actor_id uuid,
          p_annotator_two_actor_id uuid,
          p_adjudicator_actor_id uuid,
          p_audit_id uuid
        ) RETURNS TABLE(guideline_delta integer, assignment_delta integer)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_guideline f0g.annotation_guideline%ROWTYPE;
          v_guideline_delta integer := 0;
          v_assignment_delta integer := 0;
          v_expected integer;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_guideline_id IS NULL OR p_audit_id IS NULL
             OR p_guideline_version IS DISTINCT FROM
                  'f0g_fixture_blind_v1'
             OR p_guideline_sha256 IS DISTINCT FROM
                  '676e18381e44b3cc4e0782ef0d947f1f0b2c4ec3d19af688f9810abaad9d2a64'
             OR p_assignment_ids IS NULL OR p_annotation_queue_ids IS NULL
             OR cardinality(p_assignment_ids) = 0
             OR cardinality(p_assignment_ids) <>
                  cardinality(p_annotation_queue_ids)
             OR p_annotator_one_actor_id IS NULL
             OR p_annotator_two_actor_id IS NULL
             OR p_adjudicator_actor_id IS NULL
             OR p_annotator_one_actor_id = p_annotator_two_actor_id
             OR p_adjudicator_actor_id IN (
                  p_annotator_one_actor_id, p_annotator_two_actor_id
                ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_PREPARE_INVALID';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f0d.enterprise_membership AS membership
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id = v_actor_id
              AND membership.role_code = 'FIXTURE_OPERATOR'
              AND membership.status = 'ACTIVE'
          ) OR 3 <> (
            SELECT count(*)
            FROM f0d.enterprise_membership AS membership
            JOIN f0d.actor AS actor ON actor.id = membership.actor_id
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id IN (
                p_annotator_one_actor_id, p_annotator_two_actor_id,
                p_adjudicator_actor_id
              )
              AND membership.role_code = 'FIXTURE_VIEWER'
              AND membership.status = 'ACTIVE'
              AND actor.actor_kind = 'FIXTURE_VIEWER'
              AND actor.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ACTORS_NOT_AUTHORIZED';
          END IF;
          IF cardinality(p_assignment_ids) <> (
               SELECT count(DISTINCT value) FROM unnest(p_assignment_ids) AS value
             ) OR cardinality(p_annotation_queue_ids) <> (
               SELECT count(DISTINCT value)
               FROM unnest(p_annotation_queue_ids) AS value
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_PREPARE_DUPLICATE_ID';
          END IF;
          v_expected := cardinality(p_annotation_queue_ids);
          IF v_expected <> (
            SELECT count(*)
            FROM f0f.gold_annotation_queue AS queue
            WHERE queue.enterprise_id = v_enterprise_id
              AND queue.id = ANY(p_annotation_queue_ids)
              AND queue.queue_status = 'ANNOTATION_REQUIRED'
              AND queue.benchmark_tier = 'NONE'
              AND NOT queue.acceptance_gold
              AND NOT queue.production_allowed
          ) OR v_expected <> (
            SELECT count(*)
            FROM f0f.gold_annotation_queue AS queue
            WHERE queue.enterprise_id = v_enterprise_id
              AND queue.queue_status = 'ANNOTATION_REQUIRED'
              AND queue.benchmark_tier = 'NONE'
              AND NOT queue.acceptance_gold
              AND NOT queue.production_allowed
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_QUEUE_SET_INVALID';
          END IF;

          PERFORM pg_advisory_xact_lock(
            hashtextextended(v_enterprise_id::text || '-f0g-prepare', 0)
          );
          SELECT * INTO v_guideline
          FROM f0g.annotation_guideline
          WHERE enterprise_id = v_enterprise_id
            AND guideline_version = p_guideline_version;
          IF FOUND THEN
            IF v_guideline.id <> p_guideline_id
               OR v_guideline.guideline_sha256::text <>
                    p_guideline_sha256 THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_GUIDELINE_CONFLICT';
            END IF;
          ELSE
            INSERT INTO f0g.annotation_guideline(
              id, enterprise_id, created_by_actor_id,
              guideline_version, guideline_sha256
            ) VALUES (
              p_guideline_id, v_enterprise_id, v_actor_id,
              p_guideline_version, p_guideline_sha256::char(64)
            );
            v_guideline_delta := 1;
          END IF;

          WITH requested AS (
            SELECT assignment_id, queue_id
            FROM unnest(p_assignment_ids, p_annotation_queue_ids)
              AS item(assignment_id, queue_id)
          )
          SELECT count(*) INTO v_expected
          FROM requested
          LEFT JOIN f0g.blind_assignment AS existing
            ON existing.enterprise_id = v_enterprise_id
           AND existing.annotation_queue_id = requested.queue_id
          WHERE existing.id IS NOT NULL
            AND (
              existing.id <> requested.assignment_id
              OR existing.guideline_id <> p_guideline_id
              OR existing.guideline_sha256::text <> p_guideline_sha256
              OR existing.annotator_one_actor_id <>
                   p_annotator_one_actor_id
              OR existing.annotator_two_actor_id <>
                   p_annotator_two_actor_id
              OR existing.adjudicator_actor_id <> p_adjudicator_actor_id
            );
          IF v_expected <> 0 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ASSIGNMENT_CONFLICT';
          END IF;

          WITH requested AS (
            SELECT assignment_id, queue_id
            FROM unnest(p_assignment_ids, p_annotation_queue_ids)
              AS item(assignment_id, queue_id)
          )
          INSERT INTO f0g.blind_assignment(
            id, enterprise_id, annotation_queue_id,
            guideline_id, guideline_sha256,
            annotator_one_actor_id, annotator_two_actor_id,
            adjudicator_actor_id
          )
          SELECT requested.assignment_id, v_enterprise_id,
            requested.queue_id, p_guideline_id,
            p_guideline_sha256::char(64), p_annotator_one_actor_id,
            p_annotator_two_actor_id, p_adjudicator_actor_id
          FROM requested
          LEFT JOIN f0g.blind_assignment AS existing
            ON existing.enterprise_id = v_enterprise_id
           AND existing.annotation_queue_id = requested.queue_id
          WHERE existing.id IS NULL;
          GET DIAGNOSTICS v_assignment_delta = ROW_COUNT;

          IF v_guideline_delta + v_assignment_delta > 0 THEN
            INSERT INTO f0d.audit_event(
              id, enterprise_id, actor_id, event_code, target_type,
              target_id, correlation_id, outcome_code
            ) VALUES (
              p_audit_id, v_enterprise_id, v_actor_id,
              'F0G_WORKFLOW_PREPARED', 'ANNOTATION_GUIDELINE',
              p_guideline_id, p_guideline_id, 'SUCCESS'
            );
          END IF;
          RETURN QUERY SELECT v_guideline_delta, v_assignment_delta;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.list_assigned_work()
        RETURNS TABLE(
          assignment_id uuid,
          annotation_queue_id uuid,
          assignment_role text,
          selection_ordinal smallint,
          guideline_version text,
          guideline_sha256 char(64),
          assignment_status text,
          own_label_submitted boolean,
          labels_submitted integer,
          adjudication_recorded boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog AS $$
          SELECT assignment.id, assignment.annotation_queue_id,
            CASE
              WHEN assignment.annotator_one_actor_id = f0d.current_actor_id()
                THEN 'ANNOTATOR_ONE'
              WHEN assignment.annotator_two_actor_id = f0d.current_actor_id()
                THEN 'ANNOTATOR_TWO'
              WHEN assignment.adjudicator_actor_id = f0d.current_actor_id()
                THEN 'ADJUDICATOR'
            END,
            queue.selection_ordinal,
            guideline.guideline_version, guideline.guideline_sha256,
            CASE
              WHEN adjudication.recorded THEN 'ADJUDICATED'
              WHEN assignment.adjudicator_actor_id = f0d.current_actor_id()
                   AND label_state.submitted = 2
                THEN 'ADJUDICATION_READY'
              WHEN assignment.adjudicator_actor_id = f0d.current_actor_id()
                THEN 'WAITING_FOR_LABELS'
              WHEN own_label.submitted THEN 'OWN_LABEL_SUBMITTED'
              ELSE 'ANNOTATION_PENDING'
            END,
            CASE
              WHEN f0d.current_actor_id() IN (
                assignment.annotator_one_actor_id,
                assignment.annotator_two_actor_id
              ) THEN own_label.submitted
              ELSE false
            END,
            CASE
              WHEN assignment.adjudicator_actor_id = f0d.current_actor_id()
                THEN label_state.submitted
              ELSE NULL
            END,
            adjudication.recorded
          FROM f0g.blind_assignment AS assignment
          JOIN f0g.annotation_guideline AS guideline
            ON guideline.enterprise_id = assignment.enterprise_id
           AND guideline.id = assignment.guideline_id
          JOIN f0f.gold_annotation_queue AS queue
            ON queue.enterprise_id = assignment.enterprise_id
           AND queue.id = assignment.annotation_queue_id
          CROSS JOIN LATERAL (
            SELECT count(*)::integer AS submitted
            FROM f0f.gold_label_evidence AS label
            WHERE label.enterprise_id = assignment.enterprise_id
              AND label.annotation_queue_id = assignment.annotation_queue_id
          ) AS label_state
          CROSS JOIN LATERAL (
            SELECT EXISTS (
              SELECT 1 FROM f0f.gold_label_evidence AS label
              WHERE label.enterprise_id = assignment.enterprise_id
                AND label.annotation_queue_id = assignment.annotation_queue_id
                AND label.annotator_actor_id = f0d.current_actor_id()
            ) AS submitted
          ) AS own_label
          CROSS JOIN LATERAL (
            SELECT EXISTS (
              SELECT 1 FROM f0f.gold_adjudication AS decision
              WHERE decision.enterprise_id = assignment.enterprise_id
                AND decision.annotation_queue_id =
                      assignment.annotation_queue_id
            ) AS recorded
          ) AS adjudication
          WHERE assignment.enterprise_id = f0d.current_enterprise_id()
            AND f0d.context_session_authorized(assignment.enterprise_id)
            AND f0d.current_actor_id() IN (
              assignment.annotator_one_actor_id,
              assignment.annotator_two_actor_id,
              assignment.adjudicator_actor_id
            )
            AND EXISTS (
              SELECT 1
              FROM f0d.enterprise_membership AS membership
              JOIN f0d.actor AS actor ON actor.id = membership.actor_id
              WHERE membership.enterprise_id = assignment.enterprise_id
                AND membership.actor_id = f0d.current_actor_id()
                AND membership.role_code = 'FIXTURE_VIEWER'
                AND membership.status = 'ACTIVE'
                AND actor.actor_kind = 'FIXTURE_VIEWER'
                AND actor.status = 'ACTIVE'
            )
          ORDER BY assignment.annotation_queue_id
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.read_assigned_body(
          p_assignment_id uuid,
          p_key bytea,
          p_audit_id uuid
        ) RETURNS TABLE(
          body bytea,
          plaintext_sha256 char(64),
          plaintext_size_bytes bigint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_assignment f0g.blind_assignment%ROWTYPE;
          v_queue f0f.gold_annotation_queue%ROWTYPE;
          v_evidence f0f.page_body_evidence%ROWTYPE;
          v_body bytea;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_assignment_id IS NULL OR p_key IS NULL
             OR octet_length(p_key) NOT BETWEEN 32 AND 1024
             OR p_audit_id IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_BODY_READ_INVALID';
          END IF;
          SELECT * INTO v_assignment FROM f0g.blind_assignment
          WHERE enterprise_id = v_enterprise_id AND id = p_assignment_id;
          IF NOT FOUND OR v_actor_id NOT IN (
               v_assignment.annotator_one_actor_id,
               v_assignment.annotator_two_actor_id,
               v_assignment.adjudicator_actor_id
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ASSIGNMENT_REQUIRED';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM f0d.enterprise_membership AS membership
            JOIN f0d.actor AS actor ON actor.id = membership.actor_id
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id = v_actor_id
              AND membership.role_code = 'FIXTURE_VIEWER'
              AND membership.status = 'ACTIVE'
              AND actor.actor_kind = 'FIXTURE_VIEWER'
              AND actor.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_VIEWER_SESSION_REQUIRED';
          END IF;
          IF v_actor_id = v_assignment.adjudicator_actor_id
             AND 2 <> (
               SELECT count(*) FROM f0f.gold_label_evidence
               WHERE enterprise_id = v_enterprise_id
                 AND annotation_queue_id = v_assignment.annotation_queue_id
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_NOT_READY';
          END IF;
          SELECT * INTO v_queue FROM f0f.gold_annotation_queue
          WHERE enterprise_id = v_enterprise_id
            AND id = v_assignment.annotation_queue_id;
          SELECT * INTO v_evidence FROM f0f.page_body_evidence
          WHERE enterprise_id = v_enterprise_id
            AND id = v_queue.page_body_evidence_id;
          IF NOT FOUND OR v_evidence.ciphertext_sha256 <>
               encode(f0f_crypto.digest(v_evidence.ciphertext, 'sha256'), 'hex')::char(64) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_BODY_EVIDENCE_INVALID';
          END IF;
          BEGIN
            v_body := f0f_crypto.pgp_sym_decrypt_bytea(
              v_evidence.ciphertext, encode(p_key, 'hex'),
              'cipher-algo=aes256,compress-algo=0'
            );
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'F0G_BODY_KEY_INVALID';
          END;
          IF octet_length(v_body) <> v_evidence.plaintext_size_bytes
             OR encode(f0f_crypto.digest(v_body, 'sha256'), 'hex')::char(64)
                  <> v_evidence.plaintext_sha256 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_BODY_EVIDENCE_INVALID';
          END IF;
          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'F0G_ASSIGNED_BODY_READ', 'BLIND_ASSIGNMENT',
            p_assignment_id, p_assignment_id, 'SUCCESS'
          );
          RETURN QUERY SELECT v_body, v_evidence.plaintext_sha256,
            v_evidence.plaintext_size_bytes;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.record_blind_label(
          p_label_id uuid,
          p_assignment_id uuid,
          p_key bytea,
          p_label_body bytea,
          p_label_plaintext_sha256 text,
          p_label_plaintext_size_bytes bigint,
          p_audit_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_assignment f0g.blind_assignment%ROWTYPE;
          v_queue f0f.gold_annotation_queue%ROWTYPE;
          v_config f0f.body_configuration%ROWTYPE;
          v_existing f0f.gold_label_evidence%ROWTYPE;
          v_text text;
          v_existing_plain bytea;
          v_existing_text text;
          v_ciphertext bytea;
          v_ordinal smallint;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_label_id IS NULL OR p_assignment_id IS NULL
             OR p_key IS NULL
             OR octet_length(p_key) NOT BETWEEN 32 AND 1024
             OR p_label_body IS NULL
             OR p_label_plaintext_sha256 IS NULL
             OR p_label_plaintext_sha256 !~ '^[0-9a-f]{64}$'
             OR p_label_plaintext_size_bytes IS NULL
             OR p_label_plaintext_size_bytes NOT BETWEEN 0 AND 4194304
             OR p_audit_id IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_LABEL_INVALID';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended(p_assignment_id::text, 0)
          );
          SELECT * INTO v_assignment FROM f0g.blind_assignment
          WHERE enterprise_id = v_enterprise_id AND id = p_assignment_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ASSIGNMENT_REQUIRED';
          ELSIF v_actor_id = v_assignment.annotator_one_actor_id THEN
            v_ordinal := 1;
          ELSIF v_actor_id = v_assignment.annotator_two_actor_id THEN
            v_ordinal := 2;
          ELSE
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ANNOTATOR_REQUIRED';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM f0d.enterprise_membership AS membership
            JOIN f0d.actor AS actor ON actor.id = membership.actor_id
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id = v_actor_id
              AND membership.role_code = 'FIXTURE_VIEWER'
              AND membership.status = 'ACTIVE'
              AND actor.actor_kind = 'FIXTURE_VIEWER'
              AND actor.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_VIEWER_SESSION_REQUIRED';
          END IF;
          SELECT * INTO v_queue FROM f0f.gold_annotation_queue
          WHERE enterprise_id = v_enterprise_id
            AND id = v_assignment.annotation_queue_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_LABEL_STATE_INVALID';
          END IF;
          SELECT * INTO v_config FROM f0f.body_configuration
          WHERE enterprise_id = v_enterprise_id
            AND id = v_queue.body_configuration_id
            AND configuration_sha256 = v_queue.body_configuration_sha256;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_LABEL_STATE_INVALID';
          END IF;
          BEGIN
            IF encode(f0f_crypto.digest(f0f_crypto.pgp_sym_decrypt_bytea(
                 v_config.key_verifier_ciphertext, encode(p_key, 'hex'),
                 'cipher-algo=aes256,compress-algo=0'
               ), 'sha256'), 'hex')::char(64)
                 <> v_config.key_verifier_plaintext_sha256 THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
            v_text := convert_from(p_label_body, 'UTF8');
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'F0G_LABEL_KEY_OR_BODY_INVALID';
          END;
          IF octet_length(p_label_body) <> p_label_plaintext_size_bytes
             OR encode(f0f_crypto.digest(p_label_body, 'sha256'), 'hex') <>
                  p_label_plaintext_sha256
             OR v_text <> normalize(
                  replace(replace(v_text, E'\r\n', E'\n'), E'\r', E'\n'), NFC
                ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_LABEL_INVALID';
          END IF;
          SELECT * INTO v_existing FROM f0f.gold_label_evidence
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = v_assignment.annotation_queue_id
            AND label_ordinal = v_ordinal;
          IF FOUND THEN
            IF v_existing.page_body_evidence_id <> v_queue.page_body_evidence_id
               OR v_existing.body_configuration_id <>
                    v_queue.body_configuration_id
               OR v_existing.body_configuration_sha256 <>
                    v_queue.body_configuration_sha256
               OR v_existing.processing_unit_id <> v_queue.processing_unit_id
               OR v_existing.body_evidence_chain_sha256 <>
                    v_queue.body_evidence_chain_sha256
               OR v_existing.annotator_actor_id <> v_actor_id
               OR v_existing.label_ordinal <> v_ordinal
               OR v_existing.normalization_rule <> 'UTF8_NFC_LF_V1'
               OR v_existing.label_status <> 'INDEPENDENT_FIXTURE_LABEL'
               OR v_existing.benchmark_tier <> 'NONE'
               OR v_existing.acceptance_gold
               OR v_existing.production_allowed
               OR v_existing.label_ciphertext_sha256 <> encode(
                    f0f_crypto.digest(
                      v_existing.label_ciphertext, 'sha256'
                    ), 'hex'
                  )::char(64) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            BEGIN
              v_existing_plain := f0f_crypto.pgp_sym_decrypt_bytea(
                v_existing.label_ciphertext, encode(p_key, 'hex'),
                'cipher-algo=aes256,compress-algo=0'
              );
              v_existing_text := convert_from(v_existing_plain, 'UTF8');
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END;
            IF octet_length(v_existing_plain) <>
                 v_existing.label_plaintext_size_bytes
               OR encode(
                    f0f_crypto.digest(v_existing_plain, 'sha256'), 'hex'
                  )::char(64) <> v_existing.label_plaintext_sha256
               OR v_existing_text <> normalize(
                    replace(
                      replace(v_existing_text, E'\r\n', E'\n'), E'\r', E'\n'
                    ), NFC
                  ) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            IF v_existing.id = p_label_id
               AND v_existing.label_plaintext_sha256::text =
                    p_label_plaintext_sha256
               AND v_existing.label_plaintext_size_bytes =
                    p_label_plaintext_size_bytes
               AND v_existing_plain = p_label_body THEN
              RETURN v_existing.id;
            END IF;
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_LABEL_RETRY_CONFLICT';
          END IF;
          IF EXISTS (
            SELECT 1 FROM f0f.gold_label_evidence
            WHERE enterprise_id = v_enterprise_id AND id = p_label_id
          ) OR EXISTS (
            SELECT 1 FROM f0f.gold_adjudication
            WHERE enterprise_id = v_enterprise_id
              AND annotation_queue_id = v_assignment.annotation_queue_id
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_LABEL_STATE_INVALID';
          END IF;
          v_ciphertext := f0f_crypto.pgp_sym_encrypt_bytea(
            p_label_body, encode(p_key, 'hex'),
            'cipher-algo=aes256,compress-algo=0'
          );
          INSERT INTO f0f.gold_label_evidence(
            id, enterprise_id, annotation_queue_id,
            page_body_evidence_id, body_configuration_id,
            body_configuration_sha256, processing_unit_id,
            body_evidence_chain_sha256, annotator_actor_id,
            label_ordinal, label_plaintext_sha256,
            label_plaintext_size_bytes, label_ciphertext,
            label_ciphertext_sha256
          ) VALUES (
            p_label_id, v_enterprise_id, v_queue.id,
            v_queue.page_body_evidence_id, v_queue.body_configuration_id,
            v_queue.body_configuration_sha256, v_queue.processing_unit_id,
            v_queue.body_evidence_chain_sha256, v_actor_id, v_ordinal,
            p_label_plaintext_sha256::char(64),
            p_label_plaintext_size_bytes, v_ciphertext,
            encode(f0f_crypto.digest(v_ciphertext, 'sha256'), 'hex')::char(64)
          );
          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'F0G_BLIND_LABEL_RECORDED', 'BLIND_ASSIGNMENT',
            p_assignment_id, p_assignment_id, 'SUCCESS'
          );
          RETURN p_label_id;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.read_adjudication_labels(
          p_assignment_id uuid,
          p_key bytea,
          p_audit_id uuid
        ) RETURNS TABLE(
          label_ordinal smallint,
          label_id uuid,
          label_body bytea,
          label_plaintext_sha256 char(64),
          label_plaintext_size_bytes bigint
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_assignment f0g.blind_assignment%ROWTYPE;
          v_config f0f.body_configuration%ROWTYPE;
          v_label f0f.gold_label_evidence%ROWTYPE;
          v_plain bytea;
          v_text text;
          v_count integer;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_assignment_id IS NULL OR p_key IS NULL
             OR octet_length(p_key) NOT BETWEEN 32 AND 1024
             OR p_audit_id IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_ADJUDICATION_READ_INVALID';
          END IF;
          SELECT * INTO v_assignment FROM f0g.blind_assignment
          WHERE enterprise_id = v_enterprise_id AND id = p_assignment_id;
          IF NOT FOUND
             OR v_assignment.adjudicator_actor_id <> v_actor_id THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ADJUDICATOR_REQUIRED';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM f0d.enterprise_membership AS membership
            JOIN f0d.actor AS actor ON actor.id = membership.actor_id
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id = v_actor_id
              AND membership.role_code = 'FIXTURE_VIEWER'
              AND membership.status = 'ACTIVE'
              AND actor.actor_kind = 'FIXTURE_VIEWER'
              AND actor.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_VIEWER_SESSION_REQUIRED';
          END IF;
          SELECT count(*) INTO v_count FROM f0f.gold_label_evidence
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = v_assignment.annotation_queue_id;
          IF v_count <> 2 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_NOT_READY';
          END IF;
          SELECT configuration.* INTO v_config
          FROM f0f.gold_annotation_queue AS queue
          JOIN f0f.body_configuration AS configuration
            ON configuration.enterprise_id = queue.enterprise_id
           AND configuration.id = queue.body_configuration_id
           AND configuration.configuration_sha256 =
                 queue.body_configuration_sha256
          WHERE queue.enterprise_id = v_enterprise_id
            AND queue.id = v_assignment.annotation_queue_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_STATE_INVALID';
          END IF;
          BEGIN
            IF encode(f0f_crypto.digest(f0f_crypto.pgp_sym_decrypt_bytea(
                 v_config.key_verifier_ciphertext, encode(p_key, 'hex'),
                 'cipher-algo=aes256,compress-algo=0'
               ), 'sha256'), 'hex')::char(64)
                 <> v_config.key_verifier_plaintext_sha256 THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'F0G_LABEL_KEY_INVALID';
          END;
          v_count := 0;
          FOR v_label IN
            SELECT * FROM f0f.gold_label_evidence AS stored
            WHERE stored.enterprise_id = v_enterprise_id
              AND stored.annotation_queue_id =
                    v_assignment.annotation_queue_id
            ORDER BY stored.label_ordinal
          LOOP
            IF v_label.label_ciphertext_sha256 <> encode(
                 f0f_crypto.digest(v_label.label_ciphertext, 'sha256'), 'hex'
               )::char(64)
               OR (v_label.label_ordinal = 1 AND
                   v_label.annotator_actor_id <>
                     v_assignment.annotator_one_actor_id)
               OR (v_label.label_ordinal = 2 AND
                   v_label.annotator_actor_id <>
                     v_assignment.annotator_two_actor_id) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            BEGIN
              v_plain := f0f_crypto.pgp_sym_decrypt_bytea(
                v_label.label_ciphertext, encode(p_key, 'hex'),
                'cipher-algo=aes256,compress-algo=0'
              );
              v_text := convert_from(v_plain, 'UTF8');
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING
                ERRCODE = '39000', MESSAGE = 'F0G_LABEL_KEY_INVALID';
            END;
            IF octet_length(v_plain) <> v_label.label_plaintext_size_bytes
               OR encode(f0f_crypto.digest(v_plain, 'sha256'), 'hex')::char(64)
                    <> v_label.label_plaintext_sha256
               OR v_text <> normalize(
                    replace(replace(v_text, E'\r\n', E'\n'), E'\r', E'\n'), NFC
                  ) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            label_ordinal := v_label.label_ordinal;
            label_id := v_label.id;
            label_body := v_plain;
            label_plaintext_sha256 := v_label.label_plaintext_sha256;
            label_plaintext_size_bytes := v_label.label_plaintext_size_bytes;
            v_count := v_count + 1;
            RETURN NEXT;
          END LOOP;
          IF v_count <> 2 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
          END IF;
          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'F0G_LABEL_PAIR_READ', 'BLIND_ASSIGNMENT',
            p_assignment_id, p_assignment_id, 'SUCCESS'
          );
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0g.adjudicate_assignment(
          p_adjudication_id uuid,
          p_assignment_id uuid,
          p_key bytea,
          p_decision_code text,
          p_selected_label_id uuid,
          p_audit_id uuid
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_assignment f0g.blind_assignment%ROWTYPE;
          v_first f0f.gold_label_evidence%ROWTYPE;
          v_second f0f.gold_label_evidence%ROWTYPE;
          v_label f0f.gold_label_evidence%ROWTYPE;
          v_existing f0f.gold_adjudication%ROWTYPE;
          v_config f0f.body_configuration%ROWTYPE;
          v_plain bytea;
          v_text text;
          v_seen integer := 0;
          v_status text;
          v_selected_label_id uuid;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_adjudication_id IS NULL OR p_assignment_id IS NULL
             OR p_key IS NULL
             OR octet_length(p_key) NOT BETWEEN 32 AND 1024
             OR p_audit_id IS NULL OR p_decision_code IS NULL
             OR p_decision_code NOT IN (
               'ACCEPT_LABEL_ONE','ACCEPT_LABEL_TWO','NO_CONSENSUS'
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_ADJUDICATION_INVALID';
          END IF;
          PERFORM pg_advisory_xact_lock(
            hashtextextended(p_assignment_id::text, 0)
          );
          SELECT * INTO v_assignment FROM f0g.blind_assignment
          WHERE enterprise_id = v_enterprise_id AND id = p_assignment_id;
          IF NOT FOUND
             OR v_assignment.adjudicator_actor_id <> v_actor_id THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_ADJUDICATOR_REQUIRED';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM f0d.enterprise_membership AS membership
            JOIN f0d.actor AS actor ON actor.id = membership.actor_id
            WHERE membership.enterprise_id = v_enterprise_id
              AND membership.actor_id = v_actor_id
              AND membership.role_code = 'FIXTURE_VIEWER'
              AND membership.status = 'ACTIVE'
              AND actor.actor_kind = 'FIXTURE_VIEWER'
              AND actor.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'F0G_VIEWER_SESSION_REQUIRED';
          END IF;
          SELECT configuration.* INTO v_config
          FROM f0f.gold_annotation_queue AS queue
          JOIN f0f.body_configuration AS configuration
            ON configuration.enterprise_id = queue.enterprise_id
           AND configuration.id = queue.body_configuration_id
           AND configuration.configuration_sha256 =
                 queue.body_configuration_sha256
          WHERE queue.enterprise_id = v_enterprise_id
            AND queue.id = v_assignment.annotation_queue_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_STATE_INVALID';
          END IF;
          BEGIN
            IF encode(f0f_crypto.digest(f0f_crypto.pgp_sym_decrypt_bytea(
                 v_config.key_verifier_ciphertext, encode(p_key, 'hex'),
                 'cipher-algo=aes256,compress-algo=0'
               ), 'sha256'), 'hex')::char(64)
                 <> v_config.key_verifier_plaintext_sha256 THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'F0G_ADJUDICATION_KEY_INVALID';
          END;
          FOR v_label IN
            SELECT * FROM f0f.gold_label_evidence AS stored
            WHERE stored.enterprise_id = v_enterprise_id
              AND stored.annotation_queue_id =
                    v_assignment.annotation_queue_id
            ORDER BY stored.label_ordinal
          LOOP
            IF v_label.label_ciphertext_sha256 <> encode(
                 f0f_crypto.digest(v_label.label_ciphertext, 'sha256'), 'hex'
               )::char(64)
               OR (v_label.label_ordinal = 1 AND
                   v_label.annotator_actor_id <>
                     v_assignment.annotator_one_actor_id)
               OR (v_label.label_ordinal = 2 AND
                   v_label.annotator_actor_id <>
                     v_assignment.annotator_two_actor_id) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            BEGIN
              v_plain := f0f_crypto.pgp_sym_decrypt_bytea(
                v_label.label_ciphertext, encode(p_key, 'hex'),
                'cipher-algo=aes256,compress-algo=0'
              );
              v_text := convert_from(v_plain, 'UTF8');
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING
                ERRCODE = '39000', MESSAGE = 'F0G_ADJUDICATION_KEY_INVALID';
            END;
            IF octet_length(v_plain) <> v_label.label_plaintext_size_bytes
               OR encode(f0f_crypto.digest(v_plain, 'sha256'), 'hex')::char(64)
                    <> v_label.label_plaintext_sha256
               OR v_text <> normalize(
                    replace(replace(v_text, E'\r\n', E'\n'), E'\r', E'\n'), NFC
                  ) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            IF v_label.label_ordinal = 1 THEN
              v_first := v_label;
            ELSIF v_label.label_ordinal = 2 THEN
              v_second := v_label;
            ELSE
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'F0G_LABEL_EVIDENCE_INVALID';
            END IF;
            v_seen := v_seen + 1;
          END LOOP;
          IF v_seen <> 2 OR v_first.id IS NULL OR v_second.id IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_NOT_READY';
          END IF;
          IF p_decision_code = 'ACCEPT_LABEL_ONE'
             AND p_selected_label_id IN (
               v_first.id, '00000000-0000-0000-0000-000000000001'::uuid
             ) THEN
            v_status := 'FIXTURE_SEED_GOLD';
            v_selected_label_id := v_first.id;
          ELSIF p_decision_code = 'ACCEPT_LABEL_TWO'
                AND p_selected_label_id IN (
                  v_second.id,
                  '00000000-0000-0000-0000-000000000002'::uuid
                ) THEN
            v_status := 'FIXTURE_SEED_GOLD';
            v_selected_label_id := v_second.id;
          ELSIF p_decision_code = 'NO_CONSENSUS'
                AND p_selected_label_id IS NULL THEN
            v_status := 'ADJUDICATION_UNRESOLVED';
            v_selected_label_id := NULL;
          ELSE
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'F0G_ADJUDICATION_INVALID';
          END IF;
          SELECT * INTO v_existing FROM f0f.gold_adjudication
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = v_assignment.annotation_queue_id;
          IF FOUND THEN
            IF v_existing.id = p_adjudication_id
               AND v_existing.page_body_evidence_id =
                    v_first.page_body_evidence_id
               AND v_existing.first_label_id = v_first.id
               AND v_existing.first_annotator_actor_id =
                    v_first.annotator_actor_id
               AND v_existing.first_label_plaintext_sha256 =
                    v_first.label_plaintext_sha256
               AND v_existing.first_label_evidence_chain_sha256 =
                    v_first.label_evidence_chain_sha256
               AND v_existing.second_label_id = v_second.id
               AND v_existing.second_annotator_actor_id =
                    v_second.annotator_actor_id
               AND v_existing.second_label_plaintext_sha256 =
                    v_second.label_plaintext_sha256
               AND v_existing.second_label_evidence_chain_sha256 =
                    v_second.label_evidence_chain_sha256
               AND v_existing.adjudicator_actor_id = v_actor_id
               AND v_existing.decision_code = p_decision_code
               AND v_existing.selected_label_id IS NOT DISTINCT FROM
                    v_selected_label_id
               AND v_existing.gold_status = v_status
               AND v_existing.benchmark_tier = 'NONE'
               AND NOT v_existing.acceptance_gold
               AND NOT v_existing.production_allowed THEN
              RETURN v_existing.id;
            END IF;
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_RETRY_CONFLICT';
          END IF;
          IF EXISTS (
            SELECT 1 FROM f0f.gold_adjudication
            WHERE enterprise_id = v_enterprise_id
              AND id = p_adjudication_id
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'F0G_ADJUDICATION_STATE_INVALID';
          END IF;
          INSERT INTO f0f.gold_adjudication(
            id, enterprise_id, annotation_queue_id,
            page_body_evidence_id, first_label_id,
            first_annotator_actor_id, first_label_plaintext_sha256,
            first_label_evidence_chain_sha256, second_label_id,
            second_annotator_actor_id, second_label_plaintext_sha256,
            second_label_evidence_chain_sha256, adjudicator_actor_id,
            decision_code, selected_label_id, gold_status
          ) VALUES (
            p_adjudication_id, v_enterprise_id,
            v_assignment.annotation_queue_id,
            v_first.page_body_evidence_id, v_first.id,
            v_first.annotator_actor_id, v_first.label_plaintext_sha256,
            v_first.label_evidence_chain_sha256, v_second.id,
            v_second.annotator_actor_id, v_second.label_plaintext_sha256,
            v_second.label_evidence_chain_sha256, v_actor_id,
            p_decision_code, v_selected_label_id, v_status
          );
          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'F0G_ASSIGNMENT_ADJUDICATED', 'BLIND_ASSIGNMENT',
            p_assignment_id, p_assignment_id, 'SUCCESS'
          );
          RETURN p_adjudication_id;
        END
        $$
        """
    )


def _lock_down_privileges() -> None:
    op.execute(
        "REVOKE ALL ON SCHEMA f0g FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    op.execute("GRANT USAGE ON SCHEMA f0g TO f0d_runtime")
    op.execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA f0g "
        "FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    op.execute(
        "REVOKE ALL ON f0f.page_body_evidence, "
        "f0f.gold_annotation_queue, f0f.gold_label_evidence, "
        "f0f.gold_adjudication FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    # Table ACL revocation does not remove independently granted column ACLs.
    # Revoke every current column explicitly so later catalog checks cannot be
    # bypassed by a residual SELECT/INSERT/UPDATE/REFERENCES column grant.
    op.execute(
        r"""
        DO $$
        DECLARE
          v_schema text;
          v_table text;
          v_columns text;
        BEGIN
          FOR v_schema, v_table IN
            SELECT item.schema_name, item.table_name
            FROM (VALUES
              ('f0g','annotation_guideline'),
              ('f0g','blind_assignment'),
              ('f0f','page_body_evidence'),
              ('f0f','gold_annotation_queue'),
              ('f0f','gold_label_evidence'),
              ('f0f','gold_adjudication')
            ) AS item(schema_name, table_name)
          LOOP
            SELECT string_agg(quote_ident(attribute.attname), ', '
                              ORDER BY attribute.attnum)
            INTO v_columns
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = v_schema
              AND relation.relname = v_table
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped;
            IF v_columns IS NULL THEN
              RAISE EXCEPTION 'F0G_PRIVILEGE_TARGET_MISSING';
            END IF;
            EXECUTE format(
              'REVOKE ALL PRIVILEGES (%s) ON TABLE %I.%I '
              'FROM PUBLIC, f0d_runtime, f0d_worker',
              v_columns, v_schema, v_table
            );
          END LOOP;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA f0g "
        "FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    for signature in (
        "f0g.list_assigned_work()",
        "f0g.read_assigned_body(uuid,bytea,uuid)",
        "f0g.record_blind_label(uuid,uuid,bytea,bytea,text,bigint,uuid)",
        "f0g.read_adjudication_labels(uuid,bytea,uuid)",
        "f0g.adjudicate_assignment(uuid,uuid,bytea,text,uuid,uuid)",
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f0d_runtime")
    # Preserve F0-F's explicit denial even if default privileges drift.
    for signature in (
        "f0f.decrypt_verified_body(uuid,bytea)",
        "f0f.record_gold_label(uuid,uuid,bytea,bytea,text,bigint)",
        "f0f.adjudicate_gold_labels(uuid,uuid,uuid,uuid,text,uuid)",
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {signature} "
            "FROM PUBLIC, f0d_runtime, f0d_worker"
        )


def downgrade() -> None:
    # Dropping this schema after human evidence exists would silently destroy
    # assignment provenance while leaving F0-F labels behind.  Refuse instead.
    raise RuntimeError("F0G_FIXTURE_ANNOTATION_WORKFLOW_IS_IRREVERSIBLE")
