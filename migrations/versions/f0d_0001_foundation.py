"""F0-D PostgreSQL tenant, upload, plan, and queue foundation.

Revision ID: f0d_0001
Revises: None
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATEMENTS = (
    """
    CREATE OR REPLACE FUNCTION f0d.current_enterprise_id()
    RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE SET search_path = pg_catalog AS $$
      WITH setting(value) AS (
        SELECT current_setting('f0d.enterprise_id', true)
      )
      SELECT CASE
        WHEN value ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
        THEN CAST(value AS uuid)
        ELSE NULL
      END
      FROM setting
    $$
    """,
    """
    CREATE TABLE f0d.enterprise (
      id uuid PRIMARY KEY,
      enterprise_id uuid GENERATED ALWAYS AS (id) STORED NOT NULL,
      opaque_label text NOT NULL CHECK (opaque_label ~ '^[A-Z0-9_]{1,64}$'),
      data_context text NOT NULL CHECK (data_context IN ('LOCAL_FIXTURE','SYNTHETIC_CANARY')),
      fixture_set_id text,
      fixture_version text,
      benchmark_tier text NOT NULL DEFAULT 'NONE' CHECK (benchmark_tier = 'NONE'),
      claim_scope text NOT NULL DEFAULT 'PIPELINE_REGRESSION_ONLY'
        CHECK (claim_scope = 'PIPELINE_REGRESSION_ONLY'),
      external_processing_policy text NOT NULL DEFAULT 'DENY'
        CHECK (external_processing_policy = 'DENY'),
      public_display_allowed boolean NOT NULL DEFAULT false CHECK (NOT public_display_allowed),
      production_allowed boolean NOT NULL DEFAULT false CHECK (NOT production_allowed),
      model_training_allowed boolean NOT NULL DEFAULT false CHECK (NOT model_training_allowed),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      CONSTRAINT enterprise_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT enterprise_fixture_context_ck CHECK (
        (data_context = 'LOCAL_FIXTURE' AND fixture_set_id IS NOT NULL AND fixture_version IS NOT NULL)
        OR (data_context = 'SYNTHETIC_CANARY' AND fixture_set_id IS NULL AND fixture_version IS NULL)
      )
    )
    """,
    """
    CREATE TABLE f0d.actor (
      id uuid PRIMARY KEY,
      actor_kind text NOT NULL CHECK (actor_kind IN ('FIXTURE_OPERATOR','FIXTURE_VIEWER')),
      status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','REVOKED')),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp()
    )
    """,
    """
    CREATE TABLE f0d.enterprise_membership (
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      actor_id uuid NOT NULL REFERENCES f0d.actor(id),
      role_code text NOT NULL CHECK (role_code IN ('FIXTURE_OPERATOR','FIXTURE_VIEWER')),
      status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','REVOKED')),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (enterprise_id, actor_id)
    )
    """,
    """
    CREATE TABLE f0d.local_fixture_session (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      actor_id uuid NOT NULL,
      token_sha256 char(64) NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
      issued_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      expires_at timestamptz NOT NULL,
      revoked_at timestamptz,
      PRIMARY KEY (id),
      CONSTRAINT fixture_session_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT fixture_session_membership_fk FOREIGN KEY (enterprise_id, actor_id)
        REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
      CONSTRAINT fixture_session_time_ck CHECK (
        expires_at > issued_at AND (revoked_at IS NULL OR revoked_at >= issued_at)
      )
    )
    """,
    """
    CREATE TABLE f0d.fixture_source_registry (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      source_document_id char(64) NOT NULL CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
      fixture_set_id text NOT NULL,
      fixture_version text NOT NULL,
      source_group text NOT NULL CHECK (source_group IN ('core','negative')),
      source_line integer NOT NULL CHECK (source_line > 0),
      expected_sha256 char(64) NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
      expected_size_bytes bigint NOT NULL CHECK (expected_size_bytes > 0),
      document_type text NOT NULL CHECK (document_type IN ('PDF','DOC','DOCX','JPEG','XLSX')),
      corpus_role text NOT NULL CHECK (corpus_role IN ('CORE_FIXTURE','NEGATIVE_TEST_ONLY')),
      enterprise_fact_allowed boolean NOT NULL,
      current_regulation_allowed boolean NOT NULL DEFAULT false CHECK (NOT current_regulation_allowed),
      search_publish_allowed boolean NOT NULL DEFAULT false CHECK (NOT search_publish_allowed),
      benchmark_tier text NOT NULL DEFAULT 'NONE' CHECK (benchmark_tier = 'NONE'),
      external_processing_policy text NOT NULL DEFAULT 'DENY'
        CHECK (external_processing_policy = 'DENY'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT fixture_source_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT fixture_source_doc_uq UNIQUE (enterprise_id, source_document_id),
      CONSTRAINT fixture_source_line_uq UNIQUE (
        enterprise_id, fixture_set_id, fixture_version, source_group, source_line
      ),
      CONSTRAINT fixture_source_snapshot_uq UNIQUE (
        enterprise_id, source_document_id, expected_sha256, expected_size_bytes
      ),
      CONSTRAINT fixture_source_role_ck CHECK (
        (source_group = 'core' AND corpus_role = 'CORE_FIXTURE')
        OR (
          source_group = 'negative' AND corpus_role = 'NEGATIVE_TEST_ONLY'
          AND NOT enterprise_fact_allowed
        )
      )
    )
    """,
    """
    CREATE TABLE f0d.upload_session (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      actor_id uuid NOT NULL,
      source_document_id char(64) NOT NULL,
      expected_sha256 char(64) NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
      expected_size_bytes bigint NOT NULL CHECK (expected_size_bytes > 0),
      quarantine_object_key text NOT NULL CHECK (quarantine_object_key ~ '^[a-z0-9/_-]{16,180}$'),
      status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','CONTENT_STORED','COMPLETED','REJECTED')),
      captured_sha256 char(64) CHECK (captured_sha256 IS NULL OR captured_sha256 ~ '^[0-9a-f]{64}$'),
      captured_size_bytes bigint CHECK (captured_size_bytes IS NULL OR captured_size_bytes >= 0),
      rejection_code text CHECK (rejection_code IS NULL OR rejection_code ~ '^[A-Z0-9_]{1,64}$'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      completed_at timestamptz,
      PRIMARY KEY (id),
      CONSTRAINT upload_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT upload_key_uq UNIQUE (enterprise_id, quarantine_object_key),
      CONSTRAINT upload_actor_fk FOREIGN KEY (enterprise_id, actor_id)
        REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
      CONSTRAINT upload_source_fk FOREIGN KEY (
        enterprise_id, source_document_id, expected_sha256, expected_size_bytes
      ) REFERENCES f0d.fixture_source_registry(
        enterprise_id, source_document_id, expected_sha256, expected_size_bytes
      ),
      CONSTRAINT upload_state_ck CHECK (
        (status = 'PENDING' AND captured_sha256 IS NULL AND captured_size_bytes IS NULL
          AND completed_at IS NULL AND rejection_code IS NULL)
        OR (status = 'CONTENT_STORED' AND captured_sha256 IS NOT NULL
          AND captured_size_bytes IS NOT NULL AND completed_at IS NULL AND rejection_code IS NULL)
        OR (status = 'COMPLETED' AND captured_sha256 = expected_sha256
          AND captured_size_bytes = expected_size_bytes AND completed_at IS NOT NULL
          AND rejection_code IS NULL)
        OR (status = 'REJECTED' AND completed_at IS NULL AND rejection_code IS NOT NULL)
      )
    )
    """,
    """
    CREATE TABLE f0d.object_blob (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      upload_session_id uuid NOT NULL,
      storage_backend text NOT NULL DEFAULT 'LOCAL_FIXTURE_VAULT'
        CHECK (storage_backend = 'LOCAL_FIXTURE_VAULT'),
      object_key text NOT NULL CHECK (object_key ~ '^[a-z0-9/_-]{16,180}$'),
      object_version_id uuid NOT NULL,
      sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
      size_bytes bigint NOT NULL CHECK (size_bytes > 0),
      immutability_state text NOT NULL DEFAULT 'FIXTURE_IMMUTABLE'
        CHECK (immutability_state = 'FIXTURE_IMMUTABLE'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT object_blob_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT object_blob_upload_uq UNIQUE (enterprise_id, upload_session_id),
      CONSTRAINT object_blob_key_uq UNIQUE (enterprise_id, object_key, object_version_id),
      CONSTRAINT object_blob_upload_fk FOREIGN KEY (enterprise_id, upload_session_id)
        REFERENCES f0d.upload_session(enterprise_id, id)
    )
    """,
    """
    CREATE TABLE f0d.document (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      source_document_id char(64) NOT NULL,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT document_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT document_source_uq UNIQUE (enterprise_id, source_document_id),
      CONSTRAINT document_scope_source_uq UNIQUE (enterprise_id, id, source_document_id),
      CONSTRAINT document_source_fk FOREIGN KEY (enterprise_id, source_document_id)
        REFERENCES f0d.fixture_source_registry(enterprise_id, source_document_id)
    )
    """,
    """
    CREATE TABLE f0d.document_version (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      document_id uuid NOT NULL,
      object_blob_id uuid NOT NULL,
      source_document_id char(64) NOT NULL,
      version_no integer NOT NULL CHECK (version_no > 0),
      lifecycle_status text NOT NULL DEFAULT 'FIXTURE_STORED'
        CHECK (lifecycle_status = 'FIXTURE_STORED'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT document_version_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT document_version_no_uq UNIQUE (enterprise_id, document_id, version_no),
      CONSTRAINT document_version_blob_uq UNIQUE (enterprise_id, object_blob_id),
      CONSTRAINT document_version_document_fk FOREIGN KEY (
        enterprise_id, document_id, source_document_id
      ) REFERENCES f0d.document(enterprise_id, id, source_document_id),
      CONSTRAINT document_version_blob_fk FOREIGN KEY (enterprise_id, object_blob_id)
        REFERENCES f0d.object_blob(enterprise_id, id)
    )
    """,
    """
    CREATE TABLE f0d.document_processing_plan (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      document_version_id uuid NOT NULL,
      source_document_id char(64) NOT NULL,
      source_plan_sha256 char(64) NOT NULL CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
      source_schema_version text NOT NULL
        CHECK (source_schema_version ~ '^[A-Za-z0-9_./-]{1,64}$'),
      source_rule_version text NOT NULL
        CHECK (source_rule_version ~ '^[A-Za-z0-9_./-]{1,64}$'),
      page_count integer NOT NULL CHECK (page_count >= 0),
      visual_unit_count integer NOT NULL CHECK (visual_unit_count >= 0),
      native_candidate_count integer NOT NULL CHECK (native_candidate_count >= 0),
      ocr_required_count integer NOT NULL CHECK (ocr_required_count >= 0),
      manual_review_count integer NOT NULL CHECK (manual_review_count >= 0),
      deferred_conversion boolean NOT NULL DEFAULT false,
      raw_text_persisted boolean NOT NULL DEFAULT false CHECK (NOT raw_text_persisted),
      ocr_executed boolean NOT NULL DEFAULT false CHECK (NOT ocr_executed),
      benchmark_tier text NOT NULL DEFAULT 'NONE' CHECK (benchmark_tier = 'NONE'),
      external_processing_policy text NOT NULL DEFAULT 'DENY'
        CHECK (external_processing_policy = 'DENY'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT processing_plan_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT processing_plan_version_uq UNIQUE (enterprise_id, document_version_id),
      CONSTRAINT processing_plan_version_fk FOREIGN KEY (enterprise_id, document_version_id)
        REFERENCES f0d.document_version(enterprise_id, id),
      CONSTRAINT processing_plan_partition_ck CHECK (
        native_candidate_count + ocr_required_count + manual_review_count = visual_unit_count
      )
    )
    """,
    """
    CREATE TABLE f0d.document_processing_unit (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      processing_plan_id uuid NOT NULL,
      source_unit_id char(64) NOT NULL CHECK (source_unit_id ~ '^[0-9a-f]{64}$'),
      unit_ordinal integer NOT NULL CHECK (unit_ordinal > 0),
      unit_kind text NOT NULL CHECK (unit_kind IN ('PAGE','IMAGE','DOCX_BLOCK','XLSX_SHEET','DOCUMENT')),
      page_no integer CHECK (page_no IS NULL OR page_no > 0),
      candidate_decision text NOT NULL CHECK (candidate_decision IN (
        'NATIVE_CANDIDATE','FULL_PAGE_OCR_REQUIRED','MANUAL_REVIEW_REQUIRED',
        'STRUCTURE_ONLY','DEFERRED_CONVERSION_REQUIRED'
      )),
      reason_codes text[] NOT NULL CHECK (
        cardinality(reason_codes) BETWEEN 1 AND 8
        AND reason_codes <@ CAST(ARRAY[
          'NATIVE_TEXT_THRESHOLD_MET','LOW_NATIVE_TEXT','IMAGE_INPUT',
          'BAD_NATIVE_TEXT_RATIO','GEOMETRY_ABNORMAL','HIDDEN_NATIVE_TEXT',
          'STRUCTURE_ANCHOR_ONLY','DEFERRED_CONVERSION_REQUIRED'
        ] AS text[])
      ),
      native_characters integer CHECK (native_characters IS NULL OR native_characters >= 0),
      bad_character_ppm integer CHECK (bad_character_ppm IS NULL OR bad_character_ppm BETWEEN 0 AND 1000000),
      native_text_sha256 char(64)
        CHECK (native_text_sha256 IS NULL OR native_text_sha256 ~ '^[0-9a-f]{64}$'),
      rotation smallint CHECK (rotation IS NULL OR rotation IN (0,90,180,270)),
      media_left numeric(14,3), media_bottom numeric(14,3),
      media_right numeric(14,3), media_top numeric(14,3),
      crop_left numeric(14,3), crop_bottom numeric(14,3),
      crop_right numeric(14,3), crop_top numeric(14,3),
      width_px integer CHECK (width_px IS NULL OR width_px > 0),
      height_px integer CHECK (height_px IS NULL OR height_px > 0),
      evidence_sha256 char(64)
        CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT processing_unit_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT processing_unit_source_uq UNIQUE (enterprise_id, processing_plan_id, source_unit_id),
      CONSTRAINT processing_unit_ordinal_uq UNIQUE (enterprise_id, processing_plan_id, unit_ordinal),
      CONSTRAINT processing_unit_plan_fk FOREIGN KEY (enterprise_id, processing_plan_id)
        REFERENCES f0d.document_processing_plan(enterprise_id, id),
      CONSTRAINT processing_unit_page_ck CHECK (
        (unit_kind IN ('PAGE','IMAGE') AND page_no IS NOT NULL)
        OR (unit_kind NOT IN ('PAGE','IMAGE') AND page_no IS NULL)
      )
    )
    """,
    """
    CREATE TABLE f0d.idempotency_record (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL,
      actor_id uuid NOT NULL,
      method text NOT NULL CHECK (method IN ('POST','PUT','PATCH','DELETE')),
      route_code text NOT NULL CHECK (route_code ~ '^[A-Z0-9_]{1,64}$'),
      idempotency_key_sha256 char(64) NOT NULL CHECK (idempotency_key_sha256 ~ '^[0-9a-f]{64}$'),
      request_sha256 char(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
      status text NOT NULL DEFAULT 'IN_PROGRESS' CHECK (status IN ('IN_PROGRESS','COMPLETED','FAILED')),
      response_status smallint CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
      response_reference_id uuid,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      completed_at timestamptz,
      PRIMARY KEY (id),
      CONSTRAINT idempotency_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT idempotency_key_uq UNIQUE (
        enterprise_id, actor_id, method, route_code, idempotency_key_sha256
      ),
      CONSTRAINT idempotency_actor_fk FOREIGN KEY (enterprise_id, actor_id)
        REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
      CONSTRAINT idempotency_state_ck CHECK (
        (status = 'IN_PROGRESS' AND response_status IS NULL AND completed_at IS NULL)
        OR (status IN ('COMPLETED','FAILED') AND response_status IS NOT NULL AND completed_at IS NOT NULL)
      )
    )
    """,
    """
    CREATE TABLE f0d.audit_event (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      actor_id uuid REFERENCES f0d.actor(id),
      event_code text NOT NULL CHECK (event_code ~ '^[A-Z0-9_]{1,64}$'),
      target_type text NOT NULL CHECK (target_type ~ '^[A-Z0-9_]{1,64}$'),
      target_id uuid NOT NULL,
      correlation_id uuid NOT NULL,
      outcome_code text NOT NULL CHECK (outcome_code ~ '^[A-Z0-9_]{1,64}$'),
      occurred_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      PRIMARY KEY (id),
      CONSTRAINT audit_event_scope_uq UNIQUE (enterprise_id, id)
    )
    """,
    """
    CREATE TABLE f0d.outbox_event (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      event_type text NOT NULL CHECK (event_type IN (
        'UPLOAD_COMPLETED','DOCUMENT_VERSION_STORED','FIXTURE_PLAN_ATTACHED'
      )),
      upload_session_id uuid,
      document_version_id uuid,
      idempotency_key char(64) NOT NULL CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
      status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','PROCESSING','PUBLISHED','FAILED')),
      attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 100),
      available_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      lease_owner text CHECK (lease_owner IS NULL OR lease_owner ~ '^[A-Za-z0-9_-]{1,64}$'),
      lease_until timestamptz,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      published_at timestamptz,
      PRIMARY KEY (id),
      CONSTRAINT outbox_event_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT outbox_event_idem_uq UNIQUE (enterprise_id, idempotency_key),
      CONSTRAINT outbox_upload_fk FOREIGN KEY (enterprise_id, upload_session_id)
        REFERENCES f0d.upload_session(enterprise_id, id),
      CONSTRAINT outbox_version_fk FOREIGN KEY (enterprise_id, document_version_id)
        REFERENCES f0d.document_version(enterprise_id, id),
      CONSTRAINT outbox_target_ck CHECK (
        (event_type = 'UPLOAD_COMPLETED' AND upload_session_id IS NOT NULL AND document_version_id IS NULL)
        OR (event_type IN ('DOCUMENT_VERSION_STORED','FIXTURE_PLAN_ATTACHED')
          AND upload_session_id IS NULL AND document_version_id IS NOT NULL)
      )
    )
    """,
    """
    CREATE TABLE f0d.job (
      id uuid NOT NULL,
      enterprise_id uuid NOT NULL REFERENCES f0d.enterprise(id),
      kind text NOT NULL CHECK (kind IN (
        'VERIFY_AND_STORE_UPLOAD','ATTACH_NATIVE_PLAN','RECONCILE_LOCAL_VAULT'
      )),
      upload_session_id uuid,
      document_version_id uuid,
      queue_class text NOT NULL DEFAULT 'document'
        CHECK (queue_class IN ('document','maintenance')),
      priority smallint NOT NULL DEFAULT 100 CHECK (priority BETWEEN 0 AND 1000),
      idempotency_key char(64) NOT NULL CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
      input_version text CHECK (
        input_version IS NULL OR input_version ~ '^[A-Za-z0-9_.:-]{1,128}$'
      ),
      status text NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD','STALE_INPUT')),
      attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 100),
      run_after timestamptz NOT NULL DEFAULT statement_timestamp(),
      lease_owner text CHECK (lease_owner IS NULL OR lease_owner ~ '^[A-Za-z0-9_-]{1,64}$'),
      lease_until timestamptz,
      lease_generation bigint NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
      lease_token uuid,
      heartbeat_at timestamptz,
      progress_done integer NOT NULL DEFAULT 0 CHECK (progress_done >= 0),
      progress_total integer CHECK (progress_total IS NULL OR progress_total >= progress_done),
      error_code text CHECK (error_code IS NULL OR error_code ~ '^[A-Z0-9_]{1,64}$'),
      trace_id uuid NOT NULL,
      created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
      finished_at timestamptz,
      PRIMARY KEY (id),
      CONSTRAINT job_scope_uq UNIQUE (enterprise_id, id),
      CONSTRAINT job_idem_uq UNIQUE (enterprise_id, idempotency_key),
      CONSTRAINT job_upload_fk FOREIGN KEY (enterprise_id, upload_session_id)
        REFERENCES f0d.upload_session(enterprise_id, id),
      CONSTRAINT job_version_fk FOREIGN KEY (enterprise_id, document_version_id)
        REFERENCES f0d.document_version(enterprise_id, id),
      CONSTRAINT job_target_ck CHECK (
        (kind = 'VERIFY_AND_STORE_UPLOAD' AND upload_session_id IS NOT NULL AND document_version_id IS NULL)
        OR (kind = 'ATTACH_NATIVE_PLAN' AND upload_session_id IS NULL AND document_version_id IS NOT NULL)
        OR (kind = 'RECONCILE_LOCAL_VAULT' AND upload_session_id IS NULL AND document_version_id IS NULL)
      ),
      CONSTRAINT job_lease_ck CHECK (
        (status = 'RUNNING' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL
          AND lease_token IS NOT NULL AND heartbeat_at IS NOT NULL)
        OR (status <> 'RUNNING')
      )
    )
    """,
    """
    CREATE TABLE f0d.capability_gate (
      code text PRIMARY KEY CHECK (code IN (
        'REAL_CUSTOMER_CONTEXT','REGION_INDUSTRY_SCOPE','ACCEPTANCE_GOLD',
        'EXTERNAL_PROCESSING','PROFESSIONAL_RESPONSIBILITY'
      )),
      status text NOT NULL DEFAULT 'CLOSED' CHECK (status = 'CLOSED'),
      reason_code text NOT NULL CHECK (reason_code ~ '^[A-Z0-9_]{1,96}$'),
      created_at timestamptz NOT NULL DEFAULT statement_timestamp()
    )
    """,
    """
    INSERT INTO f0d.capability_gate(code, reason_code) VALUES
      ('REAL_CUSTOMER_CONTEXT','REAL_CUSTOMER_UNCONFIRMED'),
      ('REGION_INDUSTRY_SCOPE','REGION_INDUSTRY_UNCONFIRMED'),
      ('ACCEPTANCE_GOLD','ACCEPTANCE_GOLD_UNAUTHORIZED'),
      ('EXTERNAL_PROCESSING','EXTERNAL_PROCESSING_DENY'),
      ('PROFESSIONAL_RESPONSIBILITY','PROFESSIONAL_RESPONSIBILITY_UNCONFIRMED')
    """,
    """
    CREATE OR REPLACE FUNCTION f0d.reject_immutable_mutation()
    RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
    BEGIN
      RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'IMMUTABLE_RECORD';
    END
    $$
    """,
    """
    DO $$
    DECLARE immutable_table text;
    BEGIN
      FOREACH immutable_table IN ARRAY ARRAY[
        'fixture_source_registry','object_blob','document_version','audit_event','capability_gate'
      ] LOOP
        EXECUTE format(
          'CREATE TRIGGER reject_immutable_mutation BEFORE UPDATE OR DELETE ON f0d.%I '
          'FOR EACH ROW EXECUTE FUNCTION f0d.reject_immutable_mutation()',
          immutable_table
        );
      END LOOP;
    END
    $$
    """,
    """
    DO $$
    DECLARE scoped_table text;
    BEGIN
      FOREACH scoped_table IN ARRAY ARRAY[
        'enterprise','enterprise_membership','local_fixture_session','fixture_source_registry',
        'upload_session','object_blob','document','document_version','document_processing_plan',
        'document_processing_unit','idempotency_record','audit_event','outbox_event','job'
      ] LOOP
        EXECUTE format('ALTER TABLE f0d.%I ENABLE ROW LEVEL SECURITY', scoped_table);
        EXECUTE format('ALTER TABLE f0d.%I FORCE ROW LEVEL SECURITY', scoped_table);
        EXECUTE format(
          'CREATE POLICY tenant_boundary ON f0d.%I FOR ALL TO f0d_runtime, f0d_worker '
          'USING (enterprise_id = f0d.current_enterprise_id()) '
          'WITH CHECK (enterprise_id = f0d.current_enterprise_id())',
          scoped_table
        );
      END LOOP;
      CREATE POLICY migration_auth_membership ON f0d.enterprise_membership
        FOR SELECT TO f0d_migration USING (true);
      CREATE POLICY migration_auth_session ON f0d.local_fixture_session
        FOR SELECT TO f0d_migration USING (true);
      CREATE POLICY migration_seed_enterprise ON f0d.enterprise
        FOR ALL TO f0d_migration USING (enterprise_id = f0d.current_enterprise_id())
        WITH CHECK (enterprise_id = f0d.current_enterprise_id());
      CREATE POLICY migration_seed_membership ON f0d.enterprise_membership
        FOR INSERT TO f0d_migration WITH CHECK (enterprise_id = f0d.current_enterprise_id());
      CREATE POLICY migration_seed_session ON f0d.local_fixture_session
        FOR INSERT TO f0d_migration WITH CHECK (enterprise_id = f0d.current_enterprise_id());
      CREATE POLICY migration_seed_source ON f0d.fixture_source_registry
        FOR INSERT TO f0d_migration WITH CHECK (enterprise_id = f0d.current_enterprise_id());
      CREATE POLICY migration_seed_source_read ON f0d.fixture_source_registry
        FOR SELECT TO f0d_migration USING (enterprise_id = f0d.current_enterprise_id());
    END
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION f0d.authenticate_local_fixture_session(p_token_sha256 text)
    RETURNS TABLE(enterprise_id uuid, actor_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, f0d AS $$
      SELECT session.enterprise_id, session.actor_id
      FROM f0d.local_fixture_session AS session
      JOIN f0d.enterprise_membership AS membership
        ON membership.enterprise_id = session.enterprise_id
       AND membership.actor_id = session.actor_id
      JOIN f0d.actor AS actor ON actor.id = session.actor_id
      WHERE session.token_sha256 = p_token_sha256
        AND p_token_sha256 ~ '^[0-9a-f]{64}$'
        AND session.revoked_at IS NULL
        AND session.expires_at > statement_timestamp()
        AND membership.status = 'ACTIVE'
        AND actor.status = 'ACTIVE'
      LIMIT 1
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION f0d.seed_local_fixture_principal(
      p_enterprise_id uuid, p_opaque_label text, p_data_context text,
      p_fixture_set_id text, p_fixture_version text,
      p_actor_id uuid, p_actor_kind text, p_role_code text,
      p_session_id uuid, p_token_sha256 text, p_expires_at timestamptz
    ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
    SET search_path = pg_catalog, f0d AS $$
    BEGIN
      PERFORM set_config('f0d.enterprise_id', p_enterprise_id::text, true);
      INSERT INTO f0d.enterprise(
        id, opaque_label, data_context, fixture_set_id, fixture_version
      ) VALUES (
        p_enterprise_id, p_opaque_label, p_data_context, p_fixture_set_id, p_fixture_version
      );
      INSERT INTO f0d.actor(id, actor_kind) VALUES (p_actor_id, p_actor_kind);
      INSERT INTO f0d.enterprise_membership(enterprise_id, actor_id, role_code)
        VALUES (p_enterprise_id, p_actor_id, p_role_code);
      INSERT INTO f0d.local_fixture_session(
        id, enterprise_id, actor_id, token_sha256, expires_at
      ) VALUES (p_session_id, p_enterprise_id, p_actor_id, p_token_sha256, p_expires_at);
    END
    $$
    """,
    """
    REVOKE ALL ON SCHEMA f0d FROM PUBLIC
    """,
    """REVOKE ALL ON ALL TABLES IN SCHEMA f0d FROM PUBLIC, f0d_runtime, f0d_worker""",
    """REVOKE ALL ON ALL FUNCTIONS IN SCHEMA f0d FROM PUBLIC, f0d_runtime, f0d_worker""",
    """GRANT USAGE ON SCHEMA f0d TO f0d_runtime, f0d_worker""",
    """GRANT SELECT ON f0d.actor, f0d.capability_gate TO f0d_runtime, f0d_worker""",
    """
    GRANT SELECT ON f0d.enterprise, f0d.enterprise_membership,
      f0d.fixture_source_registry, f0d.object_blob, f0d.document,
      f0d.document_version, f0d.document_processing_plan,
      f0d.document_processing_unit, f0d.audit_event, f0d.outbox_event, f0d.job
      TO f0d_runtime
    """,
    """
    GRANT SELECT, INSERT, UPDATE ON f0d.local_fixture_session, f0d.upload_session,
      f0d.idempotency_record TO f0d_runtime
    """,
    """GRANT INSERT ON f0d.audit_event, f0d.outbox_event TO f0d_runtime""",
    """
    GRANT SELECT ON f0d.enterprise, f0d.enterprise_membership, f0d.local_fixture_session,
      f0d.fixture_source_registry, f0d.upload_session, f0d.object_blob, f0d.document,
      f0d.document_version, f0d.document_processing_plan, f0d.document_processing_unit,
      f0d.audit_event, f0d.outbox_event, f0d.job TO f0d_worker
    """,
    """
    GRANT INSERT ON f0d.object_blob, f0d.document, f0d.document_version,
      f0d.document_processing_plan, f0d.document_processing_unit,
      f0d.audit_event, f0d.outbox_event, f0d.job TO f0d_worker
    """,
    """GRANT SELECT, INSERT, UPDATE ON f0d.idempotency_record TO f0d_worker""",
    """GRANT UPDATE ON f0d.upload_session, f0d.outbox_event, f0d.job TO f0d_worker""",
    """GRANT EXECUTE ON FUNCTION f0d.current_enterprise_id() TO f0d_runtime, f0d_worker""",
    """GRANT EXECUTE ON FUNCTION f0d.authenticate_local_fixture_session(text) TO f0d_runtime""",
    """
    ALTER DEFAULT PRIVILEGES FOR ROLE f0d_migration IN SCHEMA f0d
      REVOKE ALL ON TABLES FROM PUBLIC
    """,
    """
    ALTER DEFAULT PRIVILEGES FOR ROLE f0d_migration IN SCHEMA f0d
      REVOKE ALL ON FUNCTIONS FROM PUBLIC
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("F0D_INITIAL_MIGRATION_IS_IRREVERSIBLE")
