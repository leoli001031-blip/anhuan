"""Add encrypted page bodies and append-only Fixture Gold workflow.

Revision ID: f0d_0004
Revises: f0d_0003
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0004"
down_revision: str | None = "f0d_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_F0F_TABLES = (
    "body_configuration",
    "page_body_evidence",
    "gold_annotation_queue",
    "gold_label_evidence",
    "gold_adjudication",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA f0f AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f0f FROM PUBLIC")
    op.execute("CREATE SCHEMA f0f_crypto AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f0f_crypto FROM PUBLIC")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA f0f_crypto")

    op.execute(
        """
        CREATE TABLE f0f.body_configuration (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          normalization_rule text NOT NULL DEFAULT 'UTF8_NFC_LF_V1'
            CHECK (normalization_rule = 'UTF8_NFC_LF_V1'),
          cipher_profile text NOT NULL DEFAULT 'PGP_SYM_AES256_V1'
            CHECK (cipher_profile = 'PGP_SYM_AES256_V1'),
          key_source text NOT NULL DEFAULT 'LOCAL_FIXTURE_FILE_0600'
            CHECK (key_source = 'LOCAL_FIXTURE_FILE_0600'),
          runner_image_id char(71) NOT NULL
            CHECK (runner_image_id ~ '^sha256:[0-9a-f]{64}$'),
          runner_lock_sha256 char(64) NOT NULL
            CHECK (runner_lock_sha256 ~ '^[0-9a-f]{64}$'),
          runner_profile_sha256 char(64) NOT NULL
            CHECK (runner_profile_sha256 ~ '^[0-9a-f]{64}$'),
          base_f0e_image_id char(71) NOT NULL
            CHECK (base_f0e_image_id ~ '^sha256:[0-9a-f]{64}$'),
          base_f0e_execution_profile_sha256 char(64) NOT NULL
            CHECK (base_f0e_execution_profile_sha256 ~ '^[0-9a-f]{64}$'),
          runner_protocol text NOT NULL
            CHECK (runner_protocol = 'f0f-body-result-v1'),
          key_verifier_plaintext_sha256 char(64) NOT NULL
            CHECK (key_verifier_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          key_verifier_ciphertext bytea NOT NULL
            CHECK (octet_length(key_verifier_ciphertext) BETWEEN 32 AND 4096),
          key_verifier_ciphertext_sha256 char(64) NOT NULL
            CHECK (key_verifier_ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          max_plaintext_bytes integer NOT NULL DEFAULT 4194304
            CHECK (max_plaintext_bytes = 4194304),
          timeout_seconds integer NOT NULL DEFAULT 120
            CHECK (timeout_seconds = 120),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          configuration_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              normalization_rule, cipher_profile, key_source,
              runner_image_id::text, runner_lock_sha256::text,
              runner_profile_sha256::text, base_f0e_image_id::text,
              base_f0e_execution_profile_sha256::text, runner_protocol,
              key_verifier_plaintext_sha256::text,
              key_verifier_ciphertext_sha256::text,
              max_plaintext_bytes::text, timeout_seconds::text,
              benchmark_tier, external_processing_policy,
              production_allowed::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT body_configuration_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT body_configuration_identity_uq UNIQUE (
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT body_configuration_hash_uq UNIQUE (
            enterprise_id, configuration_sha256
          ),
          CONSTRAINT body_configuration_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT body_configuration_ciphertext_hash_ck CHECK (
            key_verifier_ciphertext_sha256 = encode(
              f0f_crypto.digest(key_verifier_ciphertext, 'sha256'), 'hex'
            )::char(64)
          )
        )
        """
    )

    # Freeze the exact F0-E run/page identities consumed by F0-F.
    op.execute(
        """
        ALTER TABLE f0e.local_ocr_run
        ADD CONSTRAINT local_ocr_run_f0f_chain_uq UNIQUE (
          enterprise_id, id, processing_plan_id, document_version_id,
          source_plan_sha256,
          local_ocr_configuration_id, local_ocr_configuration_sha256,
          output_manifest_sha256
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0e.page_evidence_selection
        ADD CONSTRAINT page_evidence_selection_f0f_chain_uq UNIQUE (
          enterprise_id, id, local_ocr_run_id, processing_plan_id,
          document_version_id, source_document_id, source_plan_sha256,
          local_ocr_configuration_id, local_ocr_configuration_sha256,
          processing_unit_id, source_unit_id, unit_ordinal, selected_route,
          output_sha256, evidence_chain_sha256
        )
        """
    )

    # Existing job meanings remain unchanged.  A controlled-body job is bound
    # to one immutable F0-E run and one immutable body configuration.
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_kind_ck")
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_target_ck")
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_input_version_ck")
    op.execute(
        """
        ALTER TABLE f0d.job
        ADD COLUMN controlled_body_configuration_id uuid,
        ADD COLUMN controlled_body_configuration_sha256 char(64)
          CHECK (
            controlled_body_configuration_sha256 IS NULL
            OR controlled_body_configuration_sha256 ~ '^[0-9a-f]{64}$'
          ),
        ADD COLUMN local_ocr_run_id uuid,
        ADD COLUMN local_ocr_output_manifest_sha256 char(64)
          CHECK (
            local_ocr_output_manifest_sha256 IS NULL
            OR local_ocr_output_manifest_sha256 ~ '^[0-9a-f]{64}$'
          ),
        ADD CONSTRAINT job_kind_ck CHECK (kind IN (
          'VERIFY_AND_STORE_UPLOAD','ATTACH_NATIVE_PLAN',
          'RECONCILE_LOCAL_VAULT','EXECUTE_LOCAL_OCR',
          'CAPTURE_CONTROLLED_BODY'
        )),
        ADD CONSTRAINT job_input_version_ck CHECK (
          (
            kind = 'CAPTURE_CONTROLLED_BODY'
            AND input_version ~ '^[A-Za-z0-9_.:-]+$'
            AND char_length(input_version) BETWEEN 1 AND 320
          ) OR (
            kind <> 'CAPTURE_CONTROLLED_BODY'
            AND (
              input_version IS NULL
              OR input_version ~ '^[A-Za-z0-9_.:-]{1,160}$'
            )
          )
        ),
        ADD CONSTRAINT job_controlled_body_configuration_fk FOREIGN KEY (
          enterprise_id, controlled_body_configuration_id,
          controlled_body_configuration_sha256
        ) REFERENCES f0f.body_configuration(
          enterprise_id, id, configuration_sha256
        ),
        ADD CONSTRAINT job_controlled_body_ocr_run_fk FOREIGN KEY (
          enterprise_id, local_ocr_run_id, processing_plan_id,
          document_version_id, source_plan_sha256,
          local_ocr_configuration_id, local_ocr_configuration_sha256,
          local_ocr_output_manifest_sha256
        ) REFERENCES f0e.local_ocr_run(
          enterprise_id, id, processing_plan_id, document_version_id,
          source_plan_sha256, local_ocr_configuration_id,
          local_ocr_configuration_sha256, output_manifest_sha256
        ),
        ADD CONSTRAINT job_target_ck CHECK (
          (
            kind = 'VERIFY_AND_STORE_UPLOAD'
            AND upload_session_id IS NOT NULL
            AND document_version_id IS NULL
            AND processing_plan_id IS NULL
            AND source_plan_sha256 IS NULL
            AND local_ocr_configuration_id IS NULL
            AND local_ocr_configuration_sha256 IS NULL
            AND controlled_body_configuration_id IS NULL
            AND controlled_body_configuration_sha256 IS NULL
            AND local_ocr_run_id IS NULL
            AND local_ocr_output_manifest_sha256 IS NULL
          ) OR (
            kind = 'ATTACH_NATIVE_PLAN'
            AND upload_session_id IS NULL
            AND document_version_id IS NOT NULL
            AND processing_plan_id IS NULL
            AND source_plan_sha256 IS NULL
            AND local_ocr_configuration_id IS NULL
            AND local_ocr_configuration_sha256 IS NULL
            AND controlled_body_configuration_id IS NULL
            AND controlled_body_configuration_sha256 IS NULL
            AND local_ocr_run_id IS NULL
            AND local_ocr_output_manifest_sha256 IS NULL
          ) OR (
            kind = 'RECONCILE_LOCAL_VAULT'
            AND upload_session_id IS NULL
            AND document_version_id IS NULL
            AND processing_plan_id IS NULL
            AND source_plan_sha256 IS NULL
            AND local_ocr_configuration_id IS NULL
            AND local_ocr_configuration_sha256 IS NULL
            AND controlled_body_configuration_id IS NULL
            AND controlled_body_configuration_sha256 IS NULL
            AND local_ocr_run_id IS NULL
            AND local_ocr_output_manifest_sha256 IS NULL
          ) OR (
            kind = 'EXECUTE_LOCAL_OCR'
            AND upload_session_id IS NULL
            AND document_version_id IS NOT NULL
            AND processing_plan_id IS NOT NULL
            AND source_plan_sha256 IS NOT NULL
            AND local_ocr_configuration_id IS NOT NULL
            AND local_ocr_configuration_sha256 IS NOT NULL
            AND controlled_body_configuration_id IS NULL
            AND controlled_body_configuration_sha256 IS NULL
            AND local_ocr_run_id IS NULL
            AND local_ocr_output_manifest_sha256 IS NULL
            AND input_version =
              source_plan_sha256::text || ':' ||
              local_ocr_configuration_sha256::text
          ) OR (
            kind = 'CAPTURE_CONTROLLED_BODY'
            AND upload_session_id IS NULL
            AND document_version_id IS NOT NULL
            AND processing_plan_id IS NOT NULL
            AND source_plan_sha256 IS NOT NULL
            AND local_ocr_configuration_id IS NOT NULL
            AND local_ocr_configuration_sha256 IS NOT NULL
            AND controlled_body_configuration_id IS NOT NULL
            AND controlled_body_configuration_sha256 IS NOT NULL
            AND local_ocr_run_id IS NOT NULL
            AND local_ocr_output_manifest_sha256 IS NOT NULL
            AND input_version =
              source_plan_sha256::text || ':' ||
              local_ocr_configuration_sha256::text || ':' ||
              local_ocr_output_manifest_sha256::text || ':' ||
              controlled_body_configuration_sha256::text
          )
        ),
        ADD CONSTRAINT job_controlled_body_provenance_uq UNIQUE (
          enterprise_id, id, kind, lease_generation, lease_token,
          processing_plan_id, document_version_id, source_plan_sha256,
          local_ocr_configuration_id, local_ocr_configuration_sha256,
          local_ocr_run_id, local_ocr_output_manifest_sha256,
          controlled_body_configuration_id,
          controlled_body_configuration_sha256
        )
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION f0f.controlled_body_job_idempotency_key(
          p_enterprise_id uuid,
          p_processing_plan_id uuid,
          p_source_plan_sha256 text,
          p_local_ocr_configuration_id uuid,
          p_local_ocr_configuration_sha256 text,
          p_local_ocr_run_id uuid,
          p_local_ocr_output_manifest_sha256 text,
          p_body_configuration_id uuid,
          p_body_configuration_sha256 text
        ) RETURNS char(64)
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT f0e.sha256_chain(ARRAY[
            'CAPTURE_CONTROLLED_BODY', p_enterprise_id::text,
            p_processing_plan_id::text, p_source_plan_sha256,
            p_local_ocr_configuration_id::text,
            p_local_ocr_configuration_sha256,
            p_local_ocr_run_id::text,
            p_local_ocr_output_manifest_sha256,
            p_body_configuration_id::text,
            p_body_configuration_sha256
          ])
        $$
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.job
        ADD CONSTRAINT job_controlled_body_idempotency_ck CHECK (
          kind <> 'CAPTURE_CONTROLLED_BODY'
          OR idempotency_key = f0f.controlled_body_job_idempotency_key(
            enterprise_id, processing_plan_id, source_plan_sha256::text,
            local_ocr_configuration_id,
            local_ocr_configuration_sha256::text,
            local_ocr_run_id, local_ocr_output_manifest_sha256::text,
            controlled_body_configuration_id,
            controlled_body_configuration_sha256::text
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX job_controlled_body_plan_uq
        ON f0d.job(enterprise_id, processing_plan_id)
        WHERE kind = 'CAPTURE_CONTROLLED_BODY'
        """
    )

    op.execute(
        """
        CREATE TABLE f0f.page_body_evidence (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          job_id uuid NOT NULL,
          job_kind text NOT NULL DEFAULT 'CAPTURE_CONTROLLED_BODY'
            CHECK (job_kind = 'CAPTURE_CONTROLLED_BODY'),
          lease_generation bigint NOT NULL CHECK (lease_generation > 0),
          lease_token uuid NOT NULL,
          body_configuration_id uuid NOT NULL,
          body_configuration_sha256 char(64) NOT NULL
            CHECK (body_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          local_ocr_run_id uuid NOT NULL,
          local_ocr_output_manifest_sha256 char(64) NOT NULL
            CHECK (local_ocr_output_manifest_sha256 ~ '^[0-9a-f]{64}$'),
          page_evidence_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          local_ocr_configuration_id uuid NOT NULL,
          local_ocr_configuration_sha256 char(64) NOT NULL
            CHECK (local_ocr_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          processing_unit_id uuid NOT NULL,
          source_unit_id char(64) NOT NULL
            CHECK (source_unit_id ~ '^[0-9a-f]{64}$'),
          unit_ordinal integer NOT NULL CHECK (unit_ordinal > 0),
          selected_route text NOT NULL
            CHECK (selected_route IN ('NATIVE_REFERENCE','LOCAL_OCR')),
          source_output_sha256 char(64) NOT NULL
            CHECK (source_output_sha256 ~ '^[0-9a-f]{64}$'),
          source_page_evidence_sha256 char(64) NOT NULL
            CHECK (source_page_evidence_sha256 ~ '^[0-9a-f]{64}$'),
          normalization_rule text NOT NULL DEFAULT 'UTF8_NFC_LF_V1'
            CHECK (normalization_rule = 'UTF8_NFC_LF_V1'),
          plaintext_sha256 char(64) NOT NULL
            CHECK (plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          plaintext_size_bytes bigint NOT NULL
            CHECK (plaintext_size_bytes BETWEEN 0 AND 4194304),
          plaintext_character_count integer NOT NULL
            CHECK (plaintext_character_count >= 0),
          plaintext_non_blank_character_count integer NOT NULL CHECK (
            plaintext_non_blank_character_count >= 0
            AND plaintext_non_blank_character_count <= plaintext_character_count
          ),
          ciphertext bytea NOT NULL CHECK (octet_length(ciphertext) >= 32),
          ciphertext_sha256 char(64) NOT NULL
            CHECK (ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          cipher_profile text NOT NULL DEFAULT 'PGP_SYM_AES256_V1'
            CHECK (cipher_profile = 'PGP_SYM_AES256_V1'),
          terminal_status text NOT NULL DEFAULT 'CONTROLLED_BODY_EVIDENCE'
            CHECK (terminal_status = 'CONTROLLED_BODY_EVIDENCE'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          body_evidence_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, job_id::text, lease_generation::text,
              lease_token::text, body_configuration_id::text,
              body_configuration_sha256::text, local_ocr_run_id::text,
              local_ocr_output_manifest_sha256::text,
              page_evidence_id::text, processing_plan_id::text,
              document_version_id::text, source_document_id::text,
              source_plan_sha256::text,
              local_ocr_configuration_id::text,
              local_ocr_configuration_sha256::text,
              processing_unit_id::text, source_unit_id::text,
              unit_ordinal::text, selected_route,
              source_output_sha256::text,
              source_page_evidence_sha256::text, normalization_rule,
              plaintext_sha256::text, plaintext_size_bytes::text,
              plaintext_character_count::text,
              plaintext_non_blank_character_count::text,
              ciphertext_sha256::text, cipher_profile, terminal_status,
              benchmark_tier, external_processing_policy,
              production_allowed::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT page_body_evidence_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT page_body_evidence_unit_uq UNIQUE (
            enterprise_id, processing_unit_id
          ),
          CONSTRAINT page_body_evidence_page_uq UNIQUE (
            enterprise_id, page_evidence_id
          ),
          CONSTRAINT page_body_evidence_chain_uq UNIQUE (
            enterprise_id, id, body_configuration_id,
            body_configuration_sha256, processing_unit_id,
            body_evidence_chain_sha256
          ),
          CONSTRAINT page_body_evidence_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT page_body_evidence_configuration_fk FOREIGN KEY (
            enterprise_id, body_configuration_id,
            body_configuration_sha256
          ) REFERENCES f0f.body_configuration(
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT page_body_evidence_job_fk FOREIGN KEY (
            enterprise_id, job_id, job_kind, lease_generation, lease_token,
            processing_plan_id, document_version_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            local_ocr_run_id, local_ocr_output_manifest_sha256,
            body_configuration_id, body_configuration_sha256
          ) REFERENCES f0d.job(
            enterprise_id, id, kind, lease_generation, lease_token,
            processing_plan_id, document_version_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            local_ocr_run_id, local_ocr_output_manifest_sha256,
            controlled_body_configuration_id,
            controlled_body_configuration_sha256
          ) DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT page_body_evidence_source_fk FOREIGN KEY (
            enterprise_id, page_evidence_id, local_ocr_run_id,
            processing_plan_id, document_version_id, source_document_id,
            source_plan_sha256, local_ocr_configuration_id,
            local_ocr_configuration_sha256, processing_unit_id,
            source_unit_id, unit_ordinal, selected_route,
            source_output_sha256, source_page_evidence_sha256
          ) REFERENCES f0e.page_evidence_selection(
            enterprise_id, id, local_ocr_run_id, processing_plan_id,
            document_version_id, source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            processing_unit_id, source_unit_id, unit_ordinal, selected_route,
            output_sha256, evidence_chain_sha256
          ),
          CONSTRAINT page_body_evidence_ciphertext_hash_ck CHECK (
            ciphertext_sha256 = encode(
              f0f_crypto.digest(ciphertext, 'sha256'), 'hex'
            )::char(64)
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0f.gold_annotation_queue (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          page_body_evidence_id uuid NOT NULL,
          body_configuration_id uuid NOT NULL,
          body_configuration_sha256 char(64) NOT NULL
            CHECK (body_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          processing_unit_id uuid NOT NULL,
          body_evidence_chain_sha256 char(64) NOT NULL
            CHECK (body_evidence_chain_sha256 ~ '^[0-9a-f]{64}$'),
          selection_ordinal smallint NOT NULL
            CHECK (selection_ordinal BETWEEN 1 AND 15),
          selection_reason text NOT NULL DEFAULT
            'FIXTURE_SEED_CANDIDATE_ROUND_ROBIN'
            CHECK (selection_reason = 'FIXTURE_SEED_CANDIDATE_ROUND_ROBIN'),
          queue_status text NOT NULL DEFAULT 'ANNOTATION_REQUIRED'
            CHECK (queue_status = 'ANNOTATION_REQUIRED'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          acceptance_gold boolean NOT NULL DEFAULT false
            CHECK (NOT acceptance_gold),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT gold_annotation_queue_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT gold_annotation_queue_page_uq UNIQUE (
            enterprise_id, page_body_evidence_id
          ),
          CONSTRAINT gold_annotation_queue_ordinal_uq UNIQUE (
            enterprise_id, selection_ordinal
          ),
          CONSTRAINT gold_annotation_queue_chain_uq UNIQUE (
            enterprise_id, id, page_body_evidence_id,
            body_configuration_id, body_configuration_sha256,
            processing_unit_id, body_evidence_chain_sha256
          ),
          CONSTRAINT gold_annotation_queue_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT gold_annotation_queue_body_fk FOREIGN KEY (
            enterprise_id, page_body_evidence_id,
            body_configuration_id, body_configuration_sha256,
            processing_unit_id, body_evidence_chain_sha256
          ) REFERENCES f0f.page_body_evidence(
            enterprise_id, id, body_configuration_id,
            body_configuration_sha256, processing_unit_id,
            body_evidence_chain_sha256
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0f.gold_label_evidence (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          annotation_queue_id uuid NOT NULL,
          page_body_evidence_id uuid NOT NULL,
          body_configuration_id uuid NOT NULL,
          body_configuration_sha256 char(64) NOT NULL
            CHECK (body_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          processing_unit_id uuid NOT NULL,
          body_evidence_chain_sha256 char(64) NOT NULL
            CHECK (body_evidence_chain_sha256 ~ '^[0-9a-f]{64}$'),
          annotator_actor_id uuid NOT NULL,
          label_ordinal smallint NOT NULL CHECK (label_ordinal IN (1,2)),
          normalization_rule text NOT NULL DEFAULT 'UTF8_NFC_LF_V1'
            CHECK (normalization_rule = 'UTF8_NFC_LF_V1'),
          label_plaintext_sha256 char(64) NOT NULL
            CHECK (label_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          label_plaintext_size_bytes bigint NOT NULL
            CHECK (label_plaintext_size_bytes BETWEEN 0 AND 4194304),
          label_ciphertext bytea NOT NULL
            CHECK (octet_length(label_ciphertext) >= 32),
          label_ciphertext_sha256 char(64) NOT NULL
            CHECK (label_ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          label_status text NOT NULL DEFAULT 'INDEPENDENT_FIXTURE_LABEL'
            CHECK (label_status = 'INDEPENDENT_FIXTURE_LABEL'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          acceptance_gold boolean NOT NULL DEFAULT false
            CHECK (NOT acceptance_gold),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          label_evidence_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, annotation_queue_id::text,
              page_body_evidence_id::text, body_configuration_id::text,
              body_configuration_sha256::text, processing_unit_id::text,
              body_evidence_chain_sha256::text, annotator_actor_id::text,
              label_ordinal::text, normalization_rule,
              label_plaintext_sha256::text,
              label_plaintext_size_bytes::text,
              label_ciphertext_sha256::text, label_status,
              benchmark_tier, acceptance_gold::text,
              production_allowed::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT gold_label_evidence_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT gold_label_evidence_actor_uq UNIQUE (
            enterprise_id, annotation_queue_id, annotator_actor_id
          ),
          CONSTRAINT gold_label_evidence_ordinal_uq UNIQUE (
            enterprise_id, annotation_queue_id, label_ordinal
          ),
          CONSTRAINT gold_label_evidence_chain_uq UNIQUE (
            enterprise_id, id, annotation_queue_id,
            page_body_evidence_id, annotator_actor_id,
            label_plaintext_sha256, label_evidence_chain_sha256
          ),
          CONSTRAINT gold_label_evidence_actor_fk FOREIGN KEY (
            enterprise_id, annotator_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT gold_label_evidence_queue_fk FOREIGN KEY (
            enterprise_id, annotation_queue_id,
            page_body_evidence_id, body_configuration_id,
            body_configuration_sha256, processing_unit_id,
            body_evidence_chain_sha256
          ) REFERENCES f0f.gold_annotation_queue(
            enterprise_id, id, page_body_evidence_id,
            body_configuration_id, body_configuration_sha256,
            processing_unit_id, body_evidence_chain_sha256
          ),
          CONSTRAINT gold_label_evidence_ciphertext_hash_ck CHECK (
            label_ciphertext_sha256 = encode(
              f0f_crypto.digest(label_ciphertext, 'sha256'), 'hex'
            )::char(64)
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0f.gold_adjudication (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          annotation_queue_id uuid NOT NULL,
          page_body_evidence_id uuid NOT NULL,
          first_label_id uuid NOT NULL,
          first_annotator_actor_id uuid NOT NULL,
          first_label_plaintext_sha256 char(64) NOT NULL
            CHECK (first_label_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          first_label_evidence_chain_sha256 char(64) NOT NULL
            CHECK (first_label_evidence_chain_sha256 ~ '^[0-9a-f]{64}$'),
          second_label_id uuid NOT NULL,
          second_annotator_actor_id uuid NOT NULL,
          second_label_plaintext_sha256 char(64) NOT NULL
            CHECK (second_label_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          second_label_evidence_chain_sha256 char(64) NOT NULL
            CHECK (second_label_evidence_chain_sha256 ~ '^[0-9a-f]{64}$'),
          adjudicator_actor_id uuid NOT NULL,
          decision_code text NOT NULL CHECK (decision_code IN (
            'ACCEPT_LABEL_ONE','ACCEPT_LABEL_TWO','NO_CONSENSUS'
          )),
          selected_label_id uuid,
          gold_status text NOT NULL CHECK (gold_status IN (
            'FIXTURE_SEED_GOLD','ADJUDICATION_UNRESOLVED'
          )),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          acceptance_gold boolean NOT NULL DEFAULT false
            CHECK (NOT acceptance_gold),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          adjudication_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, annotation_queue_id::text,
              page_body_evidence_id::text, first_label_id::text,
              first_annotator_actor_id::text,
              first_label_plaintext_sha256::text,
              first_label_evidence_chain_sha256::text,
              second_label_id::text, second_annotator_actor_id::text,
              second_label_plaintext_sha256::text,
              second_label_evidence_chain_sha256::text,
              adjudicator_actor_id::text, decision_code,
              selected_label_id::text, gold_status, benchmark_tier,
              acceptance_gold::text, production_allowed::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT gold_adjudication_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT gold_adjudication_queue_uq UNIQUE (
            enterprise_id, annotation_queue_id
          ),
          CONSTRAINT gold_adjudication_actor_fk FOREIGN KEY (
            enterprise_id, adjudicator_actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT gold_adjudication_first_label_fk FOREIGN KEY (
            enterprise_id, first_label_id, annotation_queue_id,
            page_body_evidence_id, first_annotator_actor_id,
            first_label_plaintext_sha256,
            first_label_evidence_chain_sha256
          ) REFERENCES f0f.gold_label_evidence(
            enterprise_id, id, annotation_queue_id,
            page_body_evidence_id, annotator_actor_id,
            label_plaintext_sha256, label_evidence_chain_sha256
          ),
          CONSTRAINT gold_adjudication_second_label_fk FOREIGN KEY (
            enterprise_id, second_label_id, annotation_queue_id,
            page_body_evidence_id, second_annotator_actor_id,
            second_label_plaintext_sha256,
            second_label_evidence_chain_sha256
          ) REFERENCES f0f.gold_label_evidence(
            enterprise_id, id, annotation_queue_id,
            page_body_evidence_id, annotator_actor_id,
            label_plaintext_sha256, label_evidence_chain_sha256
          ),
          CONSTRAINT gold_adjudication_distinct_actors_ck CHECK (
            first_label_id <> second_label_id
            AND first_annotator_actor_id <> second_annotator_actor_id
            AND adjudicator_actor_id <> first_annotator_actor_id
            AND adjudicator_actor_id <> second_annotator_actor_id
          ),
          CONSTRAINT gold_adjudication_decision_ck CHECK (
            (
              decision_code = 'ACCEPT_LABEL_ONE'
              AND selected_label_id = first_label_id
              AND gold_status = 'FIXTURE_SEED_GOLD'
            ) OR (
              decision_code = 'ACCEPT_LABEL_TWO'
              AND selected_label_id = second_label_id
              AND gold_status = 'FIXTURE_SEED_GOLD'
            ) OR (
              decision_code = 'NO_CONSENSUS'
              AND selected_label_id IS NULL
              AND gold_status = 'ADJUDICATION_UNRESOLVED'
            )
          )
        )
        """
    )

    # Every F0-F fact is append-only, including attempts by the owning role.
    for table in _F0F_TABLES:
        op.execute(
            f"CREATE TRIGGER reject_immutable_row_mutation BEFORE UPDATE OR DELETE "
            f"ON f0f.{table} FOR EACH ROW EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER reject_immutable_truncate BEFORE TRUNCATE "
            f"ON f0f.{table} FOR EACH STATEMENT EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(f"ALTER TABLE f0f.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f0f.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f0f.{table}
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
            CREATE POLICY migration_f0f_read ON f0f.{table}
            FOR SELECT TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY migration_f0f_insert ON f0f.{table}
            FOR INSERT TO f0d_migration
            WITH CHECK (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        # The owning migration role can see a target row for mutation probes,
        # but the immutable triggers always reject the operation.  Without
        # these policies FORCE RLS would silently turn an attack into 0 rows.
        op.execute(
            f"""
            CREATE POLICY migration_f0f_update_probe ON f0f.{table}
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
            CREATE POLICY migration_f0f_delete_probe ON f0f.{table}
            FOR DELETE TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )

    op.execute(
        """
        CREATE POLICY migration_f0f_job_update ON f0d.job
        FOR UPDATE TO f0d_migration
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

    _create_functions()
    _lock_down_privileges()


def _create_functions() -> None:
    """Create the authenticated, key-by-parameter-only F0-F interfaces."""

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.register_body_configuration(
          p_configuration_id uuid,
          p_key_verifier_id uuid,
          p_runner_image_id text,
          p_runner_lock_sha256 text,
          p_runner_profile_sha256 text,
          p_base_f0e_image_id text,
          p_base_f0e_execution_profile_sha256 text,
          p_runner_protocol text,
          p_key bytea
        ) RETURNS char(64)
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_plaintext bytea;
          v_ciphertext bytea;
          v_plaintext_sha256 char(64);
          v_existing f0f.body_configuration%ROWTYPE;
          v_decoded bytea;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_configuration_id IS NULL OR p_key_verifier_id IS NULL
             OR p_runner_image_id IS NULL
             OR p_runner_lock_sha256 IS NULL
             OR p_runner_profile_sha256 IS NULL
             OR p_base_f0e_image_id IS NULL
             OR p_base_f0e_execution_profile_sha256 IS NULL
             OR p_runner_protocol IS NULL
             OR p_runner_image_id !~ '^sha256:[0-9a-f]{64}$'
             OR p_runner_lock_sha256 !~ '^[0-9a-f]{64}$'
             OR p_runner_profile_sha256 !~ '^[0-9a-f]{64}$'
             OR p_base_f0e_image_id !~ '^sha256:[0-9a-f]{64}$'
             OR p_base_f0e_execution_profile_sha256 !~ '^[0-9a-f]{64}$'
             OR p_runner_protocol <> 'f0f-body-result-v1'
             OR p_key IS NULL OR octet_length(p_key) < 32
             OR octet_length(p_key) > 1024 THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'CONTROLLED_BODY_CONTEXT_INVALID';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f0d.enterprise_membership AS m
            WHERE m.enterprise_id = v_enterprise_id
              AND m.actor_id = v_actor_id
              AND m.role_code = 'FIXTURE_OPERATOR'
              AND m.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'CONTROLLED_BODY_OPERATOR_REQUIRED';
          END IF;

          v_plaintext := uuid_send(p_key_verifier_id);
          v_plaintext_sha256 := encode(
            f0f_crypto.digest(v_plaintext, 'sha256'), 'hex'
          )::char(64);
          SELECT * INTO v_existing FROM f0f.body_configuration
          WHERE enterprise_id = v_enterprise_id
            AND id = p_configuration_id;
          IF FOUND THEN
            BEGIN
              v_decoded := f0f_crypto.pgp_sym_decrypt_bytea(
                v_existing.key_verifier_ciphertext, encode(p_key, 'hex'),
                'cipher-algo=aes256,compress-algo=0'
              );
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING
                ERRCODE = '39000', MESSAGE = 'CONTROLLED_BODY_KEY_INVALID';
            END;
            IF v_existing.actor_id <> v_actor_id
               OR v_existing.runner_image_id::text <> p_runner_image_id
               OR v_existing.runner_lock_sha256::text <>
                    p_runner_lock_sha256
               OR v_existing.runner_profile_sha256::text <>
                    p_runner_profile_sha256
               OR v_existing.base_f0e_image_id::text <> p_base_f0e_image_id
               OR v_existing.base_f0e_execution_profile_sha256::text <>
                    p_base_f0e_execution_profile_sha256
               OR v_existing.runner_protocol <> p_runner_protocol
               OR v_existing.key_verifier_plaintext_sha256 <>
                    v_plaintext_sha256
               OR v_decoded <> v_plaintext
               OR v_existing.key_verifier_ciphertext_sha256 <>
                    encode(f0f_crypto.digest(
                      v_existing.key_verifier_ciphertext, 'sha256'
                    ), 'hex')::char(64) THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_CONFIG_CONFLICT';
            END IF;
            RETURN v_existing.configuration_sha256;
          END IF;

          v_ciphertext := f0f_crypto.pgp_sym_encrypt_bytea(
            v_plaintext, encode(p_key, 'hex'),
            'cipher-algo=aes256,compress-algo=0'
          );
          INSERT INTO f0f.body_configuration(
            id, enterprise_id, actor_id,
            runner_image_id, runner_lock_sha256,
            runner_profile_sha256, base_f0e_image_id,
            base_f0e_execution_profile_sha256, runner_protocol,
            key_verifier_plaintext_sha256,
            key_verifier_ciphertext,
            key_verifier_ciphertext_sha256
          ) VALUES (
            p_configuration_id, v_enterprise_id, v_actor_id,
            p_runner_image_id::char(71), p_runner_lock_sha256::char(64),
            p_runner_profile_sha256::char(64),
            p_base_f0e_image_id::char(71),
            p_base_f0e_execution_profile_sha256::char(64),
            p_runner_protocol,
            v_plaintext_sha256, v_ciphertext,
            encode(f0f_crypto.digest(v_ciphertext, 'sha256'), 'hex')::char(64)
          ) RETURNING configuration_sha256 INTO v_plaintext_sha256;
          RETURN v_plaintext_sha256;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.claim_controlled_body_job(
          p_worker_id text,
          p_lease_token uuid
        ) RETURNS TABLE(
          job_id uuid,
          lease_generation bigint,
          lease_token uuid
        )
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_job_id uuid;
          v_timeout integer;
        BEGIN
          IF v_enterprise_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_worker_id IS NULL
             OR p_worker_id !~ '^[A-Za-z0-9_-]{1,64}$'
             OR p_lease_token IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'CONTROLLED_BODY_CONTEXT_INVALID';
          END IF;
          SELECT j.id, c.timeout_seconds INTO v_job_id, v_timeout
          FROM f0d.job AS j
          JOIN f0f.body_configuration AS c
            ON c.enterprise_id = j.enterprise_id
           AND c.id = j.controlled_body_configuration_id
           AND c.configuration_sha256 =
                j.controlled_body_configuration_sha256
          WHERE j.enterprise_id = v_enterprise_id
            AND j.kind = 'CAPTURE_CONTROLLED_BODY'
            AND j.attempts < 100
            AND (
              (j.status = 'PENDING'
                AND j.run_after <= statement_timestamp())
              OR (j.status = 'RUNNING'
                AND j.lease_until < statement_timestamp())
            )
          ORDER BY j.priority, j.created_at, j.id
          FOR UPDATE OF j SKIP LOCKED LIMIT 1;
          IF NOT FOUND THEN
            RETURN;
          END IF;
          UPDATE f0d.job AS claimed
          SET status = 'RUNNING', attempts = attempts + 1,
              lease_owner = p_worker_id,
              lease_until = statement_timestamp()
                + (v_timeout + 30) * interval '1 second',
              lease_generation = claimed.lease_generation + 1,
              lease_token = p_lease_token,
              heartbeat_at = statement_timestamp(), error_code = NULL
          WHERE enterprise_id = v_enterprise_id
            AND id = v_job_id
            AND kind = 'CAPTURE_CONTROLLED_BODY'
          RETURNING claimed.id, claimed.lease_generation,
            claimed.lease_token
          INTO job_id, lease_generation, lease_token;
          RETURN NEXT;
        END
        $$
        """
    )

    _create_ocr_sequence_function()
    _create_finalize_function()
    _create_decrypt_and_gold_functions()


def _create_ocr_sequence_function() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.ocr_text_sequence_sha256(
          p_body bytea,
          p_block_byte_lengths integer[]
        ) RETURNS char(64)
        LANGUAGE plpgsql IMMUTABLE
        SET search_path = pg_catalog AS $$
        DECLARE
          v_material bytea :=
            convert_to('F0E_TEXT_SEQUENCE_V1', 'UTF8') || decode('00', 'hex') ||
            convert_to('ocr-text-nfc-lf-v1', 'UTF8') || decode('00', 'hex');
          v_offset integer := 1;
          v_count integer;
          v_index integer;
          v_length integer;
          v_block bytea;
        BEGIN
          IF p_body IS NULL OR p_block_byte_lengths IS NULL
             OR array_ndims(p_block_byte_lengths) > 1 THEN
            RETURN NULL;
          END IF;
          v_count := cardinality(p_block_byte_lengths);
          IF v_count > 4096 THEN
            RETURN NULL;
          END IF;
          IF v_count > 0 THEN
            FOR v_index IN 1..v_count LOOP
              v_length := p_block_byte_lengths[v_index];
              IF v_length IS NULL OR v_length < 0
                 OR v_offset + v_length - 1 > octet_length(p_body) THEN
                RETURN NULL;
              END IF;
              v_block := substring(p_body FROM v_offset FOR v_length);
              v_material := v_material || int4send(v_index - 1) ||
                int8send(v_length::bigint) || v_block;
              v_offset := v_offset + v_length;
              IF v_index < v_count THEN
                IF substring(p_body FROM v_offset FOR 1) <> decode('0a', 'hex') THEN
                  RETURN NULL;
                END IF;
                v_offset := v_offset + 1;
              END IF;
            END LOOP;
          END IF;
          IF v_offset <> octet_length(p_body) + 1 THEN
            RETURN NULL;
          END IF;
          RETURN encode(f0f_crypto.digest(v_material, 'sha256'), 'hex')::char(64);
        END
        $$
        """
    )


def _create_finalize_function() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.finalize_controlled_body_capture(
          p_job_id uuid,
          p_lease_generation bigint,
          p_lease_token uuid,
          p_audit_id uuid,
          p_page_metadata jsonb,
          p_bodies bytea[],
          p_key bytea
        ) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_job record;
          v_key_check bytea;
          v_count integer;
          v_invalid integer;
          v_distinct_body_ids integer;
          v_distinct_page_ids integer;
          v_distinct_body_indexes integer;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_lease_generation <= 0 OR p_lease_token IS NULL
             OR p_audit_id IS NULL OR p_key IS NULL
             OR octet_length(p_key) < 32 OR octet_length(p_key) > 1024
             OR jsonb_typeof(p_page_metadata) <> 'array'
             OR p_bodies IS NULL OR array_lower(p_bodies, 1) <> 1
             OR EXISTS (
               SELECT 1 FROM unnest(p_bodies) AS body(value)
               WHERE body.value IS NULL
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'CONTROLLED_BODY_PAYLOAD_INVALID';
          END IF;
          IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(p_page_metadata) AS item(value)
            WHERE jsonb_typeof(item.value) <> 'object'
               OR NOT item.value ?& ARRAY[
                 'body_evidence_id','page_evidence_id','body_index',
                 'plaintext_sha256','plaintext_size_bytes',
                 'ocr_block_byte_lengths'
               ]
               OR EXISTS (
                 SELECT 1 FROM jsonb_object_keys(item.value) AS key(name)
                 WHERE key.name <> ALL (ARRAY[
                   'body_evidence_id','page_evidence_id','body_index',
                   'plaintext_sha256','plaintext_size_bytes',
                   'ocr_block_byte_lengths'
                 ])
               )
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'CONTROLLED_BODY_PAYLOAD_INVALID';
          END IF;

          SELECT j.*, p.source_document_id, p.visual_unit_count,
            p.raw_text_persisted AS plan_raw_text_persisted,
            p.ocr_executed AS plan_ocr_executed,
            r.output_manifest_sha256 AS run_manifest_sha256,
            r.terminal_status AS run_terminal_status,
            c.normalization_rule, c.cipher_profile,
            c.max_plaintext_bytes, c.key_verifier_plaintext_sha256,
            c.key_verifier_ciphertext,
            c.key_verifier_ciphertext_sha256,
            c.benchmark_tier AS config_benchmark_tier,
            c.external_processing_policy AS config_external_policy,
            c.production_allowed AS config_production_allowed
          INTO v_job
          FROM f0d.job AS j
          JOIN f0d.document_processing_plan AS p
            ON p.enterprise_id = j.enterprise_id
           AND p.id = j.processing_plan_id
           AND p.document_version_id = j.document_version_id
           AND p.source_plan_sha256 = j.source_plan_sha256
          JOIN f0e.local_ocr_run AS r
            ON r.enterprise_id = j.enterprise_id
           AND r.id = j.local_ocr_run_id
           AND r.processing_plan_id = j.processing_plan_id
           AND r.document_version_id = j.document_version_id
           AND r.source_plan_sha256 = j.source_plan_sha256
           AND r.local_ocr_configuration_id =
                j.local_ocr_configuration_id
           AND r.local_ocr_configuration_sha256 =
                j.local_ocr_configuration_sha256
           AND r.output_manifest_sha256 =
                j.local_ocr_output_manifest_sha256
          JOIN f0f.body_configuration AS c
            ON c.enterprise_id = j.enterprise_id
           AND c.id = j.controlled_body_configuration_id
           AND c.configuration_sha256 =
                j.controlled_body_configuration_sha256
          WHERE j.enterprise_id = v_enterprise_id
            AND j.id = p_job_id
          FOR UPDATE OF j;
          IF NOT FOUND OR v_job.kind <> 'CAPTURE_CONTROLLED_BODY'
             OR v_job.visual_unit_count <= 0
             OR v_job.plan_raw_text_persisted
             OR v_job.plan_ocr_executed
             OR v_job.run_terminal_status <> 'CANDIDATE_EVIDENCE_RECORDED'
             OR v_job.run_manifest_sha256 <>
                  v_job.local_ocr_output_manifest_sha256
             OR v_job.progress_total <> v_job.visual_unit_count
             OR v_job.config_benchmark_tier <> 'NONE'
             OR v_job.config_external_policy <> 'DENY'
             OR v_job.config_production_allowed THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_SOURCE_CHAIN_INVALID';
          END IF;
          IF jsonb_array_length(p_page_metadata) <> v_job.visual_unit_count
             OR cardinality(p_bodies) <> v_job.visual_unit_count THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'CONTROLLED_BODY_PAYLOAD_INVALID';
          END IF;

          BEGIN
            IF v_job.key_verifier_ciphertext_sha256 <>
                 encode(f0f_crypto.digest(
                   v_job.key_verifier_ciphertext, 'sha256'
                 ), 'hex')::char(64) THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
            v_key_check := f0f_crypto.pgp_sym_decrypt_bytea(
              v_job.key_verifier_ciphertext, encode(p_key, 'hex'),
              'cipher-algo=aes256,compress-algo=0'
            );
            IF encode(f0f_crypto.digest(v_key_check, 'sha256'), 'hex')::char(64)
                 <> v_job.key_verifier_plaintext_sha256 THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'CONTROLLED_BODY_KEY_INVALID';
          END;

          IF v_job.status = 'SUCCEEDED' THEN
            IF v_job.lease_generation <> p_lease_generation
               OR v_job.lease_token <> p_lease_token THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_TERMINAL_CONFLICT';
            END IF;
            BEGIN
              WITH payload AS (
                SELECT item.*, p_bodies[item.body_index] AS raw_body
                FROM jsonb_to_recordset(p_page_metadata) AS item(
                  body_evidence_id uuid,
                  page_evidence_id uuid,
                  body_index integer,
                  plaintext_sha256 text,
                  plaintext_size_bytes bigint,
                  ocr_block_byte_lengths integer[]
                )
              ), matched AS (
                SELECT payload.*, stored.ciphertext,
                  stored.ciphertext_sha256,
                  stored.plaintext_sha256 AS stored_plaintext_sha256,
                  stored.plaintext_size_bytes AS stored_plaintext_size_bytes,
                  stored.selected_route AS stored_selected_route,
                  stored.source_output_sha256 AS stored_source_output_sha256
                FROM payload
                JOIN f0f.page_body_evidence AS stored
                  ON stored.enterprise_id = v_enterprise_id
                 AND stored.job_id = p_job_id
                 AND stored.id = payload.body_evidence_id
                 AND stored.page_evidence_id = payload.page_evidence_id
              )
              SELECT count(*), count(*) FILTER (
                WHERE body_index NOT BETWEEN 1 AND v_job.visual_unit_count
                   OR plaintext_sha256 !~ '^[0-9a-f]{64}$'
                   OR plaintext_sha256::char(64) <>
                        stored_plaintext_sha256
                   OR plaintext_size_bytes <> stored_plaintext_size_bytes
                   OR plaintext_size_bytes <> octet_length(raw_body)
                   OR encode(f0f_crypto.digest(
                        raw_body, 'sha256'
                      ), 'hex') <> plaintext_sha256
                   OR encode(f0f_crypto.digest(
                        ciphertext, 'sha256'
                      ), 'hex')::char(64) <> ciphertext_sha256
                   OR (
                     stored_selected_route = 'NATIVE_REFERENCE' AND (
                       ocr_block_byte_lengths IS NOT NULL
                       OR plaintext_sha256::char(64) <> stored_source_output_sha256
                     )
                   )
                   OR (
                     stored_selected_route = 'LOCAL_OCR' AND (
                       ocr_block_byte_lengths IS NULL
                       OR f0f.ocr_text_sequence_sha256(
                         raw_body, ocr_block_byte_lengths
                       ) <> stored_source_output_sha256
                     )
                   )
                   OR f0f_crypto.pgp_sym_decrypt_bytea(
                        ciphertext, encode(p_key, 'hex'),
                        'cipher-algo=aes256,compress-algo=0'
                      ) <> raw_body
              ) INTO v_count, v_invalid
              FROM matched;
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_TERMINAL_CONFLICT';
            END;
            IF v_count <> v_job.visual_unit_count OR v_invalid <> 0 THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_TERMINAL_CONFLICT';
            END IF;
            RETURN v_count;
          END IF;
          IF v_job.status <> 'RUNNING'
             OR v_job.lease_generation <> p_lease_generation
             OR v_job.lease_token <> p_lease_token
             OR v_job.lease_until IS NULL
             OR v_job.lease_until <= statement_timestamp() THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_LEASE_STALE';
          END IF;

          BEGIN
            WITH payload AS (
              SELECT item.*, p_bodies[item.body_index] AS raw_body
              FROM jsonb_to_recordset(p_page_metadata) AS item(
                body_evidence_id uuid,
                page_evidence_id uuid,
                body_index integer,
                plaintext_sha256 text,
                plaintext_size_bytes bigint,
                ocr_block_byte_lengths integer[]
              )
            ), canonical AS (
              SELECT payload.*,
                convert_from(raw_body, 'UTF8') AS body_text
              FROM payload
            ), matched AS (
              SELECT canonical.*,
                e.local_ocr_run_id, e.processing_plan_id,
                e.document_version_id, e.source_document_id,
                e.source_plan_sha256, e.local_ocr_configuration_id,
                e.local_ocr_configuration_sha256,
                e.processing_unit_id, e.source_unit_id, e.unit_ordinal,
                e.selected_route, e.output_sha256, e.output_block_count,
                e.evidence_chain_sha256
              FROM canonical
              JOIN f0e.page_evidence_selection AS e
                ON e.enterprise_id = v_enterprise_id
               AND e.local_ocr_run_id = v_job.local_ocr_run_id
               AND e.processing_plan_id = v_job.processing_plan_id
               AND e.id = canonical.page_evidence_id
            )
            SELECT count(*) FILTER (
              WHERE body_evidence_id IS NULL
                 OR page_evidence_id IS NULL
                 OR body_index NOT BETWEEN 1 AND v_job.visual_unit_count
                 OR plaintext_sha256 !~ '^[0-9a-f]{64}$'
                 OR plaintext_size_bytes < 0
                 OR plaintext_size_bytes > v_job.max_plaintext_bytes
                 OR plaintext_size_bytes <> octet_length(raw_body)
                 OR plaintext_sha256 <> encode(
                      f0f_crypto.digest(raw_body, 'sha256'), 'hex'
                    )
                 OR (
                   selected_route = 'NATIVE_REFERENCE' AND (
                     ocr_block_byte_lengths IS NOT NULL
                     OR plaintext_sha256::char(64) <> output_sha256
                   )
                 )
                 OR (
                   selected_route = 'LOCAL_OCR' AND (
                     ocr_block_byte_lengths IS NULL
                     OR cardinality(ocr_block_byte_lengths) <> output_block_count
                     OR f0f.ocr_text_sequence_sha256(
                       raw_body, ocr_block_byte_lengths
                     ) <> output_sha256
                   )
                 )
                 OR body_text <> normalize(
                      replace(replace(body_text, E'\r\n', E'\n'), E'\r', E'\n'),
                      NFC
                    )
            ), count(*), count(DISTINCT body_evidence_id),
               count(DISTINCT page_evidence_id),
               count(DISTINCT body_index)
            INTO v_invalid, v_count, v_distinct_body_ids,
              v_distinct_page_ids, v_distinct_body_indexes
            FROM matched;
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'CONTROLLED_BODY_PAYLOAD_INVALID';
          END;
          IF v_count <> v_job.visual_unit_count
             OR v_distinct_body_ids <> v_job.visual_unit_count
             OR v_distinct_page_ids <> v_job.visual_unit_count
             OR v_distinct_body_indexes <> v_job.visual_unit_count
             OR v_invalid <> 0 THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'CONTROLLED_BODY_PAYLOAD_INVALID';
          END IF;
          -- The single INSERT below repeats strict checks so no plaintext is
          -- materialized in a temporary relation or persisted in a helper row.
          WITH payload AS (
            SELECT item.*, p_bodies[item.body_index] AS raw_body
            FROM jsonb_to_recordset(p_page_metadata) AS item(
              body_evidence_id uuid,
              page_evidence_id uuid,
              body_index integer,
              plaintext_sha256 text,
              plaintext_size_bytes bigint,
              ocr_block_byte_lengths integer[]
            )
          ), matched AS (
            SELECT payload.*,
              e.local_ocr_run_id, e.processing_plan_id,
              e.document_version_id, e.source_document_id,
              e.source_plan_sha256, e.local_ocr_configuration_id,
              e.local_ocr_configuration_sha256,
              e.processing_unit_id, e.source_unit_id, e.unit_ordinal,
              e.selected_route, e.output_sha256, e.output_block_count,
              e.evidence_chain_sha256,
              convert_from(payload.raw_body, 'UTF8') AS body_text
            FROM payload
            JOIN f0e.page_evidence_selection AS e
              ON e.enterprise_id = v_enterprise_id
             AND e.local_ocr_run_id = v_job.local_ocr_run_id
             AND e.processing_plan_id = v_job.processing_plan_id
             AND e.id = payload.page_evidence_id
          ), encrypted AS (
            SELECT matched.*,
              f0f_crypto.pgp_sym_encrypt_bytea(
                raw_body, encode(p_key, 'hex'),
                'cipher-algo=aes256,compress-algo=0'
              ) AS encrypted_body
            FROM matched
            WHERE body_evidence_id IS NOT NULL
              AND page_evidence_id IS NOT NULL
              AND body_index BETWEEN 1 AND v_job.visual_unit_count
              AND plaintext_sha256 ~ '^[0-9a-f]{64}$'
              AND plaintext_size_bytes BETWEEN 0 AND v_job.max_plaintext_bytes
              AND plaintext_size_bytes = octet_length(raw_body)
              AND plaintext_sha256 = encode(
                f0f_crypto.digest(raw_body, 'sha256'), 'hex'
              )
              AND (
                (
                  selected_route = 'NATIVE_REFERENCE'
                  AND ocr_block_byte_lengths IS NULL
                  AND plaintext_sha256::char(64) = output_sha256
                ) OR (
                  selected_route = 'LOCAL_OCR'
                  AND ocr_block_byte_lengths IS NOT NULL
                  AND cardinality(ocr_block_byte_lengths) = output_block_count
                  AND f0f.ocr_text_sequence_sha256(
                    raw_body, ocr_block_byte_lengths
                  ) = output_sha256
                )
              )
              AND body_text = normalize(
                replace(replace(body_text, E'\r\n', E'\n'), E'\r', E'\n'),
                NFC
              )
          )
          INSERT INTO f0f.page_body_evidence(
            id, enterprise_id, actor_id, job_id, lease_generation,
            lease_token, body_configuration_id,
            body_configuration_sha256, local_ocr_run_id,
            local_ocr_output_manifest_sha256, page_evidence_id,
            processing_plan_id, document_version_id, source_document_id,
            source_plan_sha256, local_ocr_configuration_id,
            local_ocr_configuration_sha256, processing_unit_id,
            source_unit_id, unit_ordinal, selected_route,
            source_output_sha256, source_page_evidence_sha256,
            plaintext_sha256, plaintext_size_bytes,
            plaintext_character_count,
            plaintext_non_blank_character_count,
            ciphertext, ciphertext_sha256
          )
          SELECT body_evidence_id, v_enterprise_id, v_actor_id, p_job_id,
            p_lease_generation, p_lease_token,
            v_job.controlled_body_configuration_id,
            v_job.controlled_body_configuration_sha256,
            local_ocr_run_id, v_job.local_ocr_output_manifest_sha256,
            page_evidence_id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            processing_unit_id, source_unit_id, unit_ordinal,
            selected_route, output_sha256, evidence_chain_sha256,
            plaintext_sha256::char(64), plaintext_size_bytes,
            char_length(body_text),
            char_length(regexp_replace(body_text, '[[:space:]]', '', 'g')),
            encrypted_body,
            encode(f0f_crypto.digest(encrypted_body, 'sha256'), 'hex')::char(64)
          FROM encrypted;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count <> v_job.visual_unit_count THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_PAGE_CROSSWIRE';
          END IF;

          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'CONTROLLED_BODY_FINALIZED', 'CONTROLLED_BODY_JOB',
            p_job_id, p_job_id, 'SUCCESS'
          );
          UPDATE f0d.job
          SET status = 'SUCCEEDED', finished_at = statement_timestamp(),
              progress_done = v_job.visual_unit_count,
              progress_total = v_job.visual_unit_count,
              lease_owner = NULL, lease_until = NULL,
              heartbeat_at = NULL, error_code = NULL
          WHERE enterprise_id = v_enterprise_id
            AND id = p_job_id
            AND kind = 'CAPTURE_CONTROLLED_BODY'
            AND status = 'RUNNING'
            AND lease_generation = p_lease_generation
            AND lease_token = p_lease_token
            AND lease_until > statement_timestamp();
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_LEASE_STALE';
          END IF;
          RETURN v_count;
        END
        $$
        """
    )


def _create_decrypt_and_gold_functions() -> None:
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.decrypt_verified_body(
          p_body_evidence_id uuid,
          p_key bytea
        ) RETURNS bytea
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_body f0f.page_body_evidence%ROWTYPE;
          v_plaintext bytea;
        BEGIN
          IF v_enterprise_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_body_evidence_id IS NULL OR p_key IS NULL THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'CONTROLLED_BODY_CONTEXT_INVALID';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f0d.enterprise_membership AS m
            WHERE m.enterprise_id = v_enterprise_id
              AND m.actor_id = f0d.current_actor_id()
              AND m.role_code = 'FIXTURE_OPERATOR'
              AND m.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'CONTROLLED_BODY_OPERATOR_REQUIRED';
          END IF;
          SELECT * INTO v_body FROM f0f.page_body_evidence
          WHERE enterprise_id = v_enterprise_id
            AND id = p_body_evidence_id;
          IF NOT FOUND OR v_body.ciphertext_sha256 <>
               encode(f0f_crypto.digest(v_body.ciphertext, 'sha256'), 'hex')::char(64) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_EVIDENCE_INVALID';
          END IF;
          BEGIN
            v_plaintext := f0f_crypto.pgp_sym_decrypt_bytea(
              v_body.ciphertext, encode(p_key, 'hex'),
              'cipher-algo=aes256,compress-algo=0'
            );
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'CONTROLLED_BODY_KEY_INVALID';
          END;
          IF octet_length(v_plaintext) <> v_body.plaintext_size_bytes
             OR encode(f0f_crypto.digest(v_plaintext, 'sha256'), 'hex')::char(64)
                  <> v_body.plaintext_sha256 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'CONTROLLED_BODY_EVIDENCE_INVALID';
          END IF;
          RETURN v_plaintext;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.enqueue_gold_annotation(
          p_queue_id uuid,
          p_body_evidence_id uuid,
          p_selection_ordinal integer
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_body f0f.page_body_evidence%ROWTYPE;
          v_existing f0f.gold_annotation_queue%ROWTYPE;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_queue_id IS NULL OR p_body_evidence_id IS NULL
             OR p_selection_ordinal NOT BETWEEN 1 AND 15 THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'ANNOTATION_QUEUE_INVALID';
          END IF;
          SELECT b.* INTO v_body
          FROM f0f.page_body_evidence AS b
          JOIN f0d.fixture_source_registry AS r
            ON r.enterprise_id = b.enterprise_id
           AND r.source_document_id = b.source_document_id
          WHERE b.enterprise_id = v_enterprise_id
            AND b.id = p_body_evidence_id
            AND r.source_group = 'core'
            AND r.corpus_role = 'CORE_FIXTURE'
            AND NOT r.current_regulation_allowed
            AND NOT r.search_publish_allowed;
          IF NOT FOUND THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'ANNOTATION_SOURCE_INVALID';
          END IF;
          SELECT * INTO v_existing FROM f0f.gold_annotation_queue
          WHERE enterprise_id = v_enterprise_id
            AND page_body_evidence_id = p_body_evidence_id;
          IF FOUND THEN
            IF v_existing.id <> p_queue_id
               OR v_existing.selection_ordinal <> p_selection_ordinal THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'ANNOTATION_QUEUE_CONFLICT';
            END IF;
            RETURN v_existing.id;
          END IF;
          INSERT INTO f0f.gold_annotation_queue(
            id, enterprise_id, actor_id, page_body_evidence_id,
            body_configuration_id, body_configuration_sha256,
            processing_unit_id, body_evidence_chain_sha256,
            selection_ordinal
          ) VALUES (
            p_queue_id, v_enterprise_id, v_actor_id, v_body.id,
            v_body.body_configuration_id,
            v_body.body_configuration_sha256,
            v_body.processing_unit_id,
            v_body.body_evidence_chain_sha256,
            p_selection_ordinal
          );
          RETURN p_queue_id;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.record_gold_label(
          p_label_id uuid,
          p_queue_id uuid,
          p_key bytea,
          p_label_body bytea,
          p_label_plaintext_sha256 text,
          p_label_plaintext_size_bytes bigint
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_queue f0f.gold_annotation_queue%ROWTYPE;
          v_config f0f.body_configuration%ROWTYPE;
          v_raw bytea;
          v_text text;
          v_ciphertext bytea;
          v_ordinal integer;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_label_id IS NULL OR p_queue_id IS NULL
             OR p_key IS NULL OR octet_length(p_key) < 32
             OR p_label_body IS NULL
             OR p_label_plaintext_sha256 !~ '^[0-9a-f]{64}$'
             OR p_label_plaintext_size_bytes NOT BETWEEN 0 AND 4194304 THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'GOLD_LABEL_INVALID';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f0d.enterprise_membership AS m
            WHERE m.enterprise_id = v_enterprise_id
              AND m.actor_id = v_actor_id
              AND m.role_code = 'FIXTURE_OPERATOR'
              AND m.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'GOLD_ANNOTATOR_REQUIRED';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(p_queue_id::text, 0));
          SELECT * INTO v_queue FROM f0f.gold_annotation_queue
          WHERE enterprise_id = v_enterprise_id AND id = p_queue_id;
          IF NOT FOUND OR EXISTS (
            SELECT 1 FROM f0f.gold_adjudication
            WHERE enterprise_id = v_enterprise_id
              AND annotation_queue_id = p_queue_id
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'GOLD_LABEL_STATE_INVALID';
          END IF;
          SELECT * INTO v_config FROM f0f.body_configuration
          WHERE enterprise_id = v_enterprise_id
            AND id = v_queue.body_configuration_id
            AND configuration_sha256 = v_queue.body_configuration_sha256;
          BEGIN
            IF encode(f0f_crypto.digest(f0f_crypto.pgp_sym_decrypt_bytea(
                 v_config.key_verifier_ciphertext, encode(p_key, 'hex'),
                 'cipher-algo=aes256,compress-algo=0'
               ), 'sha256'), 'hex')::char(64)
                 <> v_config.key_verifier_plaintext_sha256 THEN
              RAISE EXCEPTION USING ERRCODE = '39000';
            END IF;
            v_raw := p_label_body;
            v_text := convert_from(v_raw, 'UTF8');
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION USING
              ERRCODE = '39000', MESSAGE = 'GOLD_LABEL_KEY_OR_BODY_INVALID';
          END;
          IF octet_length(v_raw) <> p_label_plaintext_size_bytes
             OR encode(f0f_crypto.digest(v_raw, 'sha256'), 'hex') <>
                  p_label_plaintext_sha256
             OR v_text <> normalize(
                  replace(replace(v_text, E'\r\n', E'\n'), E'\r', E'\n'),
                  NFC
                ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'GOLD_LABEL_INVALID';
          END IF;
          IF EXISTS (
            SELECT 1 FROM f0f.gold_label_evidence
            WHERE enterprise_id = v_enterprise_id
              AND annotation_queue_id = p_queue_id
              AND annotator_actor_id = v_actor_id
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'GOLD_LABEL_ACTOR_DUPLICATE';
          END IF;
          SELECT count(*) + 1 INTO v_ordinal
          FROM f0f.gold_label_evidence
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = p_queue_id;
          IF v_ordinal NOT BETWEEN 1 AND 2 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'GOLD_LABEL_PAIR_COMPLETE';
          END IF;
          v_ciphertext := f0f_crypto.pgp_sym_encrypt_bytea(
            v_raw, encode(p_key, 'hex'),
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
            p_label_id, v_enterprise_id, p_queue_id,
            v_queue.page_body_evidence_id, v_queue.body_configuration_id,
            v_queue.body_configuration_sha256, v_queue.processing_unit_id,
            v_queue.body_evidence_chain_sha256, v_actor_id, v_ordinal,
            p_label_plaintext_sha256::char(64),
            p_label_plaintext_size_bytes, v_ciphertext,
            encode(f0f_crypto.digest(v_ciphertext, 'sha256'), 'hex')::char(64)
          );
          RETURN p_label_id;
        END
        $$
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0f.adjudicate_gold_labels(
          p_adjudication_id uuid,
          p_queue_id uuid,
          p_first_label_id uuid,
          p_second_label_id uuid,
          p_decision_code text,
          p_selected_label_id uuid DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_first f0f.gold_label_evidence%ROWTYPE;
          v_second f0f.gold_label_evidence%ROWTYPE;
          v_status text;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id)
             OR p_adjudication_id IS NULL OR p_queue_id IS NULL
             OR p_first_label_id IS NULL OR p_second_label_id IS NULL
             OR p_first_label_id = p_second_label_id
             OR p_decision_code NOT IN (
               'ACCEPT_LABEL_ONE','ACCEPT_LABEL_TWO','NO_CONSENSUS'
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'GOLD_ADJUDICATION_INVALID';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f0d.enterprise_membership AS m
            WHERE m.enterprise_id = v_enterprise_id
              AND m.actor_id = v_actor_id
              AND m.role_code = 'FIXTURE_OPERATOR'
              AND m.status = 'ACTIVE'
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'GOLD_ADJUDICATOR_REQUIRED';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(p_queue_id::text, 0));
          SELECT * INTO v_first FROM f0f.gold_label_evidence
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = p_queue_id
            AND id = p_first_label_id;
          SELECT * INTO v_second FROM f0f.gold_label_evidence
          WHERE enterprise_id = v_enterprise_id
            AND annotation_queue_id = p_queue_id
            AND id = p_second_label_id;
          IF v_first.id IS NULL OR v_second.id IS NULL
             OR v_first.label_ordinal <> 1
             OR v_second.label_ordinal <> 2
             OR v_first.annotator_actor_id = v_second.annotator_actor_id
             OR v_actor_id IN (
               v_first.annotator_actor_id, v_second.annotator_actor_id
             ) OR EXISTS (
               SELECT 1 FROM f0f.gold_adjudication
               WHERE enterprise_id = v_enterprise_id
                 AND annotation_queue_id = p_queue_id
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'GOLD_ADJUDICATION_STATE_INVALID';
          END IF;
          IF p_decision_code = 'ACCEPT_LABEL_ONE'
             AND p_selected_label_id = p_first_label_id THEN
            v_status := 'FIXTURE_SEED_GOLD';
          ELSIF p_decision_code = 'ACCEPT_LABEL_TWO'
                AND p_selected_label_id = p_second_label_id THEN
            v_status := 'FIXTURE_SEED_GOLD';
          ELSIF p_decision_code = 'NO_CONSENSUS'
                AND p_selected_label_id IS NULL THEN
            v_status := 'ADJUDICATION_UNRESOLVED';
          ELSE
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'GOLD_ADJUDICATION_INVALID';
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
            p_adjudication_id, v_enterprise_id, p_queue_id,
            v_first.page_body_evidence_id, v_first.id,
            v_first.annotator_actor_id, v_first.label_plaintext_sha256,
            v_first.label_evidence_chain_sha256, v_second.id,
            v_second.annotator_actor_id, v_second.label_plaintext_sha256,
            v_second.label_evidence_chain_sha256, v_actor_id,
            p_decision_code, p_selected_label_id, v_status
          );
          RETURN p_adjudication_id;
        END
        $$
        """
    )


def _lock_down_privileges() -> None:
    op.execute("REVOKE ALL ON SCHEMA f0f FROM PUBLIC")
    op.execute("REVOKE ALL ON SCHEMA f0f_crypto FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA f0f FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA f0f FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA f0f TO f0d_runtime, f0d_worker")
    op.execute(
        """
        GRANT SELECT ON f0f.body_configuration,
          f0f.page_body_evidence, f0f.gold_annotation_queue,
          f0f.gold_label_evidence, f0f.gold_adjudication
        TO f0d_runtime, f0d_worker
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION
          f0f.controlled_body_job_idempotency_key(
            uuid,uuid,text,uuid,text,uuid,text,uuid,text
          ) TO f0d_worker
        """
    )
    for signature in (
        "f0f.register_body_configuration(uuid,uuid,text,text,text,text,text,text,bytea)",
        "f0f.claim_controlled_body_job(text,uuid)",
        "f0f.finalize_controlled_body_capture(uuid,bigint,uuid,uuid,jsonb,bytea[],bytea)",
        "f0f.decrypt_verified_body(uuid,bytea)",
        "f0f.enqueue_gold_annotation(uuid,uuid,integer)",
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f0d_worker")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f0f.decrypt_verified_body(uuid,bytea) "
        "TO f0d_runtime"
    )
    for signature in (
        "f0f.record_gold_label(uuid,uuid,bytea,bytea,text,bigint)",
        "f0f.adjudicate_gold_labels(uuid,uuid,uuid,uuid,text,uuid)",
    ):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {signature} FROM f0d_runtime, f0d_worker"
        )


def downgrade() -> None:
    raise RuntimeError("F0F_CONTROLLED_BODY_EVIDENCE_IS_IRREVERSIBLE")
