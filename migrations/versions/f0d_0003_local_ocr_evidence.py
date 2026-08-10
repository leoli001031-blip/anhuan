"""Add immutable local OCR routing and terminal evidence.

Revision ID: f0d_0003
Revises: f0d_0002
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0003"
down_revision: str | None = "f0d_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA f0e AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f0e FROM PUBLIC")
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0e.sha256_chain(p_values text[])
        RETURNS char(64)
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT encode(
            sha256(
              convert_to(
                array_to_string(p_values, chr(31), '<NULL>'),
                'UTF8'
              )
            ),
            'hex'
          )::char(64)
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f0e.sha256_chain(text[]) FROM PUBLIC")

    # F0-C plans and units become immutable inputs.  The generated native-text
    # identity removes NULL from the one composite evidence foreign key.
    op.execute(
        """
        ALTER TABLE f0d.document_processing_plan
        ADD CONSTRAINT processing_plan_evidence_source_uq UNIQUE (
          enterprise_id, id, document_version_id, source_document_id,
          source_plan_sha256
        ),
        ADD CONSTRAINT processing_plan_job_source_uq UNIQUE (
          enterprise_id, id, document_version_id, source_plan_sha256
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.document_processing_unit
        ALTER COLUMN evidence_sha256 SET NOT NULL,
        ALTER COLUMN native_characters SET NOT NULL,
        ADD COLUMN native_text_identity_sha256 char(64)
          GENERATED ALWAYS AS (
            COALESCE(native_text_sha256, repeat('0', 64)::char(64))
          ) STORED,
        ADD CONSTRAINT processing_unit_evidence_source_uq UNIQUE (
          enterprise_id, id, processing_plan_id, source_unit_id,
          unit_ordinal, unit_kind, page_no, candidate_decision,
          evidence_sha256, native_text_identity_sha256, native_characters
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.object_blob
        ADD CONSTRAINT object_blob_evidence_source_uq UNIQUE (
          enterprise_id, id, sha256
        )
        """
    )
    op.execute(
        """
        ALTER TABLE f0d.document_version
        ADD CONSTRAINT document_version_evidence_source_uq UNIQUE (
          enterprise_id, id, object_blob_id, source_document_id
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_processing_plan_mutation
        BEFORE UPDATE OR DELETE ON f0d.document_processing_plan
        FOR EACH ROW EXECUTE FUNCTION f0d.reject_immutable_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER reject_processing_unit_mutation
        BEFORE UPDATE OR DELETE ON f0d.document_processing_unit
        FOR EACH ROW EXECUTE FUNCTION f0d.reject_immutable_mutation()
        """
    )

    op.execute(
        """
        CREATE TABLE f0e.local_ocr_configuration (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          renderer_id text NOT NULL
            CHECK (renderer_id ~ '^[A-Za-z0-9_.-]{1,64}$'),
          renderer_version text NOT NULL
            CHECK (renderer_version ~ '^[A-Za-z0-9_.:+-]{1,96}$'),
          renderer_binary_sha256 char(64) NOT NULL
            CHECK (renderer_binary_sha256 ~ '^[0-9a-f]{64}$'),
          ocr_engine_id text NOT NULL
            CHECK (ocr_engine_id ~ '^[A-Za-z0-9_.-]{1,64}$'),
          ocr_engine_version text NOT NULL
            CHECK (ocr_engine_version ~ '^[A-Za-z0-9_.:+-]{1,96}$'),
          ocr_engine_binary_sha256 char(64) NOT NULL
            CHECK (ocr_engine_binary_sha256 ~ '^[0-9a-f]{64}$'),
          language_pack_ids text NOT NULL CHECK (
            language_pack_ids ~
              '^[A-Za-z0-9_.-]{1,64}(,[A-Za-z0-9_.-]{1,64}){0,7}$'
          ),
          language_pack_bundle_sha256 char(64) NOT NULL
            CHECK (language_pack_bundle_sha256 ~ '^[0-9a-f]{64}$'),
          normalization_profile_sha256 char(64) NOT NULL
            CHECK (normalization_profile_sha256 ~ '^[0-9a-f]{64}$'),
          execution_profile_sha256 char(64) NOT NULL
            CHECK (execution_profile_sha256 ~ '^[0-9a-f]{64}$'),
          container_image_id char(71) NOT NULL
            CHECK (container_image_id ~ '^sha256:[0-9a-f]{64}$'),
          lock_sha256 char(64) NOT NULL
            CHECK (lock_sha256 ~ '^[0-9a-f]{64}$'),
          dpi integer NOT NULL DEFAULT 250 CHECK (dpi = 250),
          max_pdf_pages integer NOT NULL DEFAULT 128
            CHECK (max_pdf_pages = 128),
          max_selected_pages_per_run integer NOT NULL DEFAULT 16
            CHECK (max_selected_pages_per_run = 16),
          max_pixels_per_page integer NOT NULL DEFAULT 16000000
            CHECK (max_pixels_per_page = 16000000),
          manual_review_confidence_floor_ppm integer NOT NULL DEFAULT 0
            CHECK (manual_review_confidence_floor_ppm = 0),
          timeout_seconds integer NOT NULL
            CHECK (timeout_seconds BETWEEN 1 AND 3600),
          coordinate_space_version text NOT NULL CHECK (
            coordinate_space_version ~ '^[A-Za-z0-9_.:/-]{1,64}$'
          ),
          network_policy text NOT NULL DEFAULT 'DENY'
            CHECK (network_policy = 'DENY'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          raw_text_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT raw_text_persisted),
          page_image_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT page_image_persisted),
          configuration_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              renderer_id, renderer_version, renderer_binary_sha256::text,
              ocr_engine_id, ocr_engine_version,
              ocr_engine_binary_sha256::text, language_pack_ids,
              language_pack_bundle_sha256::text,
              normalization_profile_sha256::text,
              execution_profile_sha256::text, container_image_id::text,
              lock_sha256::text, dpi::text, max_pdf_pages::text,
              max_selected_pages_per_run::text,
              max_pixels_per_page::text,
              manual_review_confidence_floor_ppm::text,
              timeout_seconds::text,
              coordinate_space_version, network_policy,
              external_processing_policy, benchmark_tier,
              raw_text_persisted::text, page_image_persisted::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT local_ocr_configuration_scope_uq UNIQUE (
            enterprise_id, id
          ),
          CONSTRAINT local_ocr_configuration_identity_uq UNIQUE (
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT local_ocr_configuration_hash_uq UNIQUE (
            enterprise_id, configuration_sha256
          ),
          CONSTRAINT local_ocr_configuration_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id)
        )
        """
    )

    # A local evidence job is bound to one frozen F0-C plan and one immutable
    # configuration before it can be leased.  Existing F0-D targets remain
    # unchanged and must leave all new identity columns NULL.
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_kind_check")
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_target_ck")
    op.execute("ALTER TABLE f0d.job DROP CONSTRAINT job_input_version_check")
    op.execute(
        """
        ALTER TABLE f0d.job
        ADD COLUMN processing_plan_id uuid,
        ADD COLUMN source_plan_sha256 char(64)
          CHECK (source_plan_sha256 IS NULL OR source_plan_sha256 ~ '^[0-9a-f]{64}$'),
        ADD COLUMN local_ocr_configuration_id uuid,
        ADD COLUMN local_ocr_configuration_sha256 char(64)
          CHECK (
            local_ocr_configuration_sha256 IS NULL
            OR local_ocr_configuration_sha256 ~ '^[0-9a-f]{64}$'
          ),
        ADD CONSTRAINT job_kind_ck CHECK (kind IN (
          'VERIFY_AND_STORE_UPLOAD','ATTACH_NATIVE_PLAN',
          'RECONCILE_LOCAL_VAULT','EXECUTE_LOCAL_OCR'
        )),
        ADD CONSTRAINT job_input_version_ck CHECK (
          input_version IS NULL
          OR input_version ~ '^[A-Za-z0-9_.:-]{1,160}$'
        ),
        ADD CONSTRAINT job_processing_plan_fk FOREIGN KEY (
          enterprise_id, processing_plan_id, document_version_id,
          source_plan_sha256
        ) REFERENCES f0d.document_processing_plan(
          enterprise_id, id, document_version_id, source_plan_sha256
        ),
        ADD CONSTRAINT job_local_ocr_configuration_fk FOREIGN KEY (
          enterprise_id, local_ocr_configuration_id,
          local_ocr_configuration_sha256
        ) REFERENCES f0e.local_ocr_configuration(
          enterprise_id, id, configuration_sha256
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
          ) OR (
            kind = 'ATTACH_NATIVE_PLAN'
            AND upload_session_id IS NULL
            AND document_version_id IS NOT NULL
            AND processing_plan_id IS NULL
            AND source_plan_sha256 IS NULL
            AND local_ocr_configuration_id IS NULL
            AND local_ocr_configuration_sha256 IS NULL
          ) OR (
            kind = 'RECONCILE_LOCAL_VAULT'
            AND upload_session_id IS NULL
            AND document_version_id IS NULL
            AND processing_plan_id IS NULL
            AND source_plan_sha256 IS NULL
            AND local_ocr_configuration_id IS NULL
            AND local_ocr_configuration_sha256 IS NULL
          ) OR (
            kind = 'EXECUTE_LOCAL_OCR'
            AND upload_session_id IS NULL
            AND document_version_id IS NOT NULL
            AND processing_plan_id IS NOT NULL
            AND source_plan_sha256 IS NOT NULL
            AND local_ocr_configuration_id IS NOT NULL
            AND local_ocr_configuration_sha256 IS NOT NULL
            AND input_version =
              source_plan_sha256::text || ':' ||
              local_ocr_configuration_sha256::text
          )
        ),
        ADD CONSTRAINT job_local_ocr_provenance_uq UNIQUE (
          enterprise_id, id, kind, lease_generation, lease_token,
          processing_plan_id, document_version_id, source_plan_sha256,
          local_ocr_configuration_id, local_ocr_configuration_sha256
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f0e.local_ocr_job_idempotency_key(
          p_enterprise_id uuid,
          p_processing_plan_id uuid,
          p_source_plan_sha256 text,
          p_configuration_id uuid,
          p_configuration_sha256 text
        ) RETURNS char(64)
        LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, f0d AS $$
          SELECT f0e.sha256_chain(ARRAY[
            'EXECUTE_LOCAL_OCR', p_enterprise_id::text,
            p_processing_plan_id::text, p_source_plan_sha256,
            p_configuration_id::text, p_configuration_sha256
          ])
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f0e.local_ocr_job_idempotency_key(uuid,uuid,text,uuid,text) FROM PUBLIC"
    )
    op.execute(
        """
        ALTER TABLE f0d.job
        ADD CONSTRAINT job_local_ocr_idempotency_ck CHECK (
          kind <> 'EXECUTE_LOCAL_OCR'
          OR idempotency_key = f0e.local_ocr_job_idempotency_key(
            enterprise_id, processing_plan_id, source_plan_sha256::text,
            local_ocr_configuration_id,
            local_ocr_configuration_sha256::text
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX job_local_ocr_plan_uq
        ON f0d.job(enterprise_id, processing_plan_id)
        WHERE kind = 'EXECUTE_LOCAL_OCR'
        """
    )

    op.execute(
        """
        CREATE TABLE f0e.local_ocr_run (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          job_id uuid NOT NULL,
          job_kind text NOT NULL DEFAULT 'EXECUTE_LOCAL_OCR'
            CHECK (job_kind = 'EXECUTE_LOCAL_OCR'),
          lease_generation bigint NOT NULL CHECK (lease_generation > 0),
          lease_token uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          object_blob_id uuid NOT NULL,
          input_object_sha256 char(64) NOT NULL
            CHECK (input_object_sha256 ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          local_ocr_configuration_id uuid NOT NULL,
          local_ocr_configuration_sha256 char(64) NOT NULL
            CHECK (local_ocr_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          source_group text NOT NULL CHECK (source_group IN ('core','negative')),
          enterprise_fact_allowed boolean NOT NULL,
          current_regulation_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT current_regulation_allowed),
          search_publish_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT search_publish_allowed),
          terminal_status text NOT NULL CHECK (terminal_status IN (
            'CANDIDATE_EVIDENCE_RECORDED','DEFERRED_CONVERSION_REQUIRED'
          )),
          visual_unit_count integer NOT NULL CHECK (visual_unit_count >= 0),
          native_reference_count integer NOT NULL
            CHECK (native_reference_count >= 0),
          local_ocr_count integer NOT NULL CHECK (local_ocr_count >= 0),
          manual_review_required_count integer NOT NULL
            CHECK (manual_review_required_count >= 0),
          deferred_document_count integer NOT NULL
            CHECK (deferred_document_count IN (0,1)),
          output_manifest_sha256 char(64) NOT NULL
            CHECK (output_manifest_sha256 ~ '^[0-9a-f]{64}$'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          raw_text_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT raw_text_persisted),
          page_image_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT page_image_persisted),
          evidence_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, job_id::text, lease_generation::text,
              lease_token::text, processing_plan_id::text,
              document_version_id::text, source_document_id::text,
              object_blob_id::text, input_object_sha256::text,
              source_plan_sha256::text,
              local_ocr_configuration_id::text,
              local_ocr_configuration_sha256::text, source_group,
              enterprise_fact_allowed::text,
              current_regulation_allowed::text,
              search_publish_allowed::text, terminal_status,
              visual_unit_count::text, native_reference_count::text,
              local_ocr_count::text, manual_review_required_count::text,
              deferred_document_count::text,
              output_manifest_sha256::text, benchmark_tier,
              external_processing_policy, raw_text_persisted::text,
              page_image_persisted::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT local_ocr_run_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT local_ocr_run_plan_uq UNIQUE (
            enterprise_id, processing_plan_id
          ),
          CONSTRAINT local_ocr_run_job_uq UNIQUE (enterprise_id, job_id),
          CONSTRAINT local_ocr_run_chain_uq UNIQUE (
            enterprise_id, id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            terminal_status
          ),
          CONSTRAINT local_ocr_run_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT local_ocr_run_job_fk FOREIGN KEY (
            enterprise_id, job_id, job_kind, lease_generation, lease_token,
            processing_plan_id, document_version_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256
          ) REFERENCES f0d.job(
            enterprise_id, id, kind, lease_generation, lease_token,
            processing_plan_id, document_version_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256
          ),
          CONSTRAINT local_ocr_run_plan_fk FOREIGN KEY (
            enterprise_id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256
          ) REFERENCES f0d.document_processing_plan(
            enterprise_id, id, document_version_id, source_document_id,
            source_plan_sha256
          ),
          CONSTRAINT local_ocr_run_version_fk FOREIGN KEY (
            enterprise_id, document_version_id, object_blob_id,
            source_document_id
          ) REFERENCES f0d.document_version(
            enterprise_id, id, object_blob_id, source_document_id
          ),
          CONSTRAINT local_ocr_run_blob_fk FOREIGN KEY (
            enterprise_id, object_blob_id, input_object_sha256
          ) REFERENCES f0d.object_blob(enterprise_id, id, sha256),
          CONSTRAINT local_ocr_run_configuration_fk FOREIGN KEY (
            enterprise_id, local_ocr_configuration_id,
            local_ocr_configuration_sha256
          ) REFERENCES f0e.local_ocr_configuration(
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT local_ocr_run_gate_ck CHECK (
            source_group <> 'negative' OR NOT enterprise_fact_allowed
          ),
          CONSTRAINT local_ocr_run_partition_ck CHECK (
            (
              terminal_status = 'CANDIDATE_EVIDENCE_RECORDED'
              AND visual_unit_count > 0
              AND native_reference_count + local_ocr_count = visual_unit_count
              AND manual_review_required_count <= local_ocr_count
              AND deferred_document_count = 0
            ) OR (
              terminal_status = 'DEFERRED_CONVERSION_REQUIRED'
              AND visual_unit_count = 0
              AND native_reference_count = 0
              AND local_ocr_count = 0
              AND manual_review_required_count = 0
              AND deferred_document_count = 1
            )
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0e.page_evidence_selection (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          local_ocr_run_id uuid NOT NULL,
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
          unit_kind text NOT NULL CHECK (unit_kind IN ('PAGE','IMAGE')),
          page_no integer NOT NULL CHECK (page_no > 0),
          candidate_decision text NOT NULL CHECK (candidate_decision IN (
            'NATIVE_CANDIDATE','FULL_PAGE_OCR_REQUIRED'
          )),
          source_evidence_sha256 char(64) NOT NULL
            CHECK (source_evidence_sha256 ~ '^[0-9a-f]{64}$'),
          source_native_text_sha256 char(64) NOT NULL
            CHECK (source_native_text_sha256 ~ '^[0-9a-f]{64}$'),
          source_native_characters integer NOT NULL
            CHECK (source_native_characters >= 0),
          selected_route text NOT NULL CHECK (selected_route IN (
            'NATIVE_REFERENCE','LOCAL_OCR'
          )),
          render_sha256 char(64)
            CHECK (render_sha256 IS NULL OR render_sha256 ~ '^[0-9a-f]{64}$'),
          output_sha256 char(64) NOT NULL
            CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
          output_block_count integer NOT NULL CHECK (output_block_count >= 0),
          output_character_count integer NOT NULL
            CHECK (output_character_count >= 0),
          output_non_blank_character_count integer NOT NULL CHECK (
            output_non_blank_character_count >= 0
            AND output_non_blank_character_count <= output_character_count
          ),
          mean_confidence_ppm integer
            CHECK (
              mean_confidence_ppm IS NULL
              OR mean_confidence_ppm BETWEEN 0 AND 1000000
            ),
          bbox_summary_sha256 char(64) CHECK (
            bbox_summary_sha256 IS NULL
            OR bbox_summary_sha256 ~ '^[0-9a-f]{64}$'
          ),
          reason_code text NOT NULL CHECK (reason_code IN (
            'NATIVE_TEXT_REFERENCE_SELECTED',
            'LOCAL_OCR_CANDIDATE_CAPTURED',
            'LOCAL_OCR_EMPTY_REVIEW_REQUIRED'
          )),
          terminal_status text NOT NULL CHECK (terminal_status IN (
            'NATIVE_REFERENCE','LOCAL_OCR_EVIDENCE',
            'MANUAL_REVIEW_REQUIRED'
          )),
          run_terminal_status text NOT NULL
            DEFAULT 'CANDIDATE_EVIDENCE_RECORDED'
            CHECK (run_terminal_status = 'CANDIDATE_EVIDENCE_RECORDED'),
          ocr_executed boolean GENERATED ALWAYS AS (
            selected_route = 'LOCAL_OCR'
          ) STORED,
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          raw_text_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT raw_text_persisted),
          page_image_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT page_image_persisted),
          evidence_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, local_ocr_run_id::text,
              processing_plan_id::text, document_version_id::text,
              source_document_id::text, source_plan_sha256::text,
              local_ocr_configuration_id::text,
              local_ocr_configuration_sha256::text,
              processing_unit_id::text, source_unit_id::text,
              unit_ordinal::text, unit_kind, page_no::text,
              candidate_decision, source_evidence_sha256::text,
              source_native_text_sha256::text,
              source_native_characters::text, selected_route,
              render_sha256::text, output_sha256::text,
              output_block_count::text, output_character_count::text,
              output_non_blank_character_count::text,
              mean_confidence_ppm::text, bbox_summary_sha256::text,
              reason_code, terminal_status, run_terminal_status,
              benchmark_tier, external_processing_policy,
              raw_text_persisted::text, page_image_persisted::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT page_evidence_selection_scope_uq UNIQUE (
            enterprise_id, id
          ),
          CONSTRAINT page_evidence_selection_unit_uq UNIQUE (
            enterprise_id, processing_unit_id
          ),
          CONSTRAINT page_evidence_selection_run_ordinal_uq UNIQUE (
            enterprise_id, local_ocr_run_id, unit_ordinal
          ),
          CONSTRAINT page_evidence_selection_run_fk FOREIGN KEY (
            enterprise_id, local_ocr_run_id, processing_plan_id,
            document_version_id, source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            run_terminal_status
          ) REFERENCES f0e.local_ocr_run(
            enterprise_id, id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            terminal_status
          ) DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT page_evidence_selection_source_unit_fk FOREIGN KEY (
            enterprise_id, processing_unit_id, processing_plan_id,
            source_unit_id, unit_ordinal, unit_kind, page_no,
            candidate_decision, source_evidence_sha256,
            source_native_text_sha256, source_native_characters
          ) REFERENCES f0d.document_processing_unit(
            enterprise_id, id, processing_plan_id, source_unit_id,
            unit_ordinal, unit_kind, page_no, candidate_decision,
            evidence_sha256, native_text_identity_sha256, native_characters
          ),
          CONSTRAINT page_evidence_selection_route_ck CHECK (
            (
              selected_route = 'NATIVE_REFERENCE'
              AND candidate_decision = 'NATIVE_CANDIDATE'
              AND source_native_text_sha256 <> repeat('0', 64)::char(64)
              AND render_sha256 IS NULL
              AND output_sha256 = source_native_text_sha256
              AND output_block_count = 0
              AND output_character_count = source_native_characters
              AND output_non_blank_character_count = source_native_characters
              AND mean_confidence_ppm IS NULL
              AND bbox_summary_sha256 IS NULL
              AND reason_code = 'NATIVE_TEXT_REFERENCE_SELECTED'
              AND terminal_status = 'NATIVE_REFERENCE'
            ) OR (
              selected_route = 'LOCAL_OCR'
              AND candidate_decision = 'FULL_PAGE_OCR_REQUIRED'
              AND render_sha256 IS NOT NULL
              AND bbox_summary_sha256 IS NOT NULL
              AND (
                (
                  reason_code = 'LOCAL_OCR_EMPTY_REVIEW_REQUIRED'
                  AND output_non_blank_character_count = 0
                  AND mean_confidence_ppm IS NULL
                  AND terminal_status = 'MANUAL_REVIEW_REQUIRED'
                ) OR (
                  reason_code = 'LOCAL_OCR_CANDIDATE_CAPTURED'
                  AND output_block_count > 0
                  AND output_character_count > 0
                  AND output_non_blank_character_count > 0
                  AND mean_confidence_ppm IS NOT NULL
                  AND terminal_status = 'LOCAL_OCR_EVIDENCE'
                )
              )
            )
          )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE f0e.deferred_document_evidence (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          local_ocr_run_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          local_ocr_configuration_id uuid NOT NULL,
          local_ocr_configuration_sha256 char(64) NOT NULL
            CHECK (local_ocr_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          run_terminal_status text NOT NULL
            DEFAULT 'DEFERRED_CONVERSION_REQUIRED'
            CHECK (run_terminal_status = 'DEFERRED_CONVERSION_REQUIRED'),
          evidence_method text NOT NULL DEFAULT 'NO_CONVERSION_EXECUTED'
            CHECK (evidence_method = 'NO_CONVERSION_EXECUTED'),
          reason_code text NOT NULL DEFAULT 'DEFERRED_CONVERSION_REQUIRED'
            CHECK (reason_code = 'DEFERRED_CONVERSION_REQUIRED'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          raw_text_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT raw_text_persisted),
          page_image_persisted boolean NOT NULL DEFAULT false
            CHECK (NOT page_image_persisted),
          evidence_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, local_ocr_run_id::text,
              processing_plan_id::text, document_version_id::text,
              source_document_id::text, source_plan_sha256::text,
              local_ocr_configuration_id::text,
              local_ocr_configuration_sha256::text,
              run_terminal_status, evidence_method, reason_code,
              benchmark_tier, external_processing_policy,
              raw_text_persisted::text, page_image_persisted::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT deferred_document_evidence_scope_uq UNIQUE (
            enterprise_id, id
          ),
          CONSTRAINT deferred_document_evidence_plan_uq UNIQUE (
            enterprise_id, processing_plan_id
          ),
          CONSTRAINT deferred_document_evidence_run_uq UNIQUE (
            enterprise_id, local_ocr_run_id
          ),
          CONSTRAINT deferred_document_evidence_run_fk FOREIGN KEY (
            enterprise_id, local_ocr_run_id, processing_plan_id,
            document_version_id, source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            run_terminal_status
          ) REFERENCES f0e.local_ocr_run(
            enterprise_id, id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256,
            local_ocr_configuration_id, local_ocr_configuration_sha256,
            terminal_status
          ) DEFERRABLE INITIALLY DEFERRED
        )
        """
    )

    for table in (
        "local_ocr_configuration",
        "local_ocr_run",
        "page_evidence_selection",
        "deferred_document_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER reject_immutable_mutation BEFORE UPDATE OR DELETE "
            f"ON f0e.{table} FOR EACH ROW EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(f"ALTER TABLE f0e.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f0e.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f0e.{table}
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
        """
        CREATE POLICY actor_insert_boundary
        ON f0e.local_ocr_configuration
        AS RESTRICTIVE FOR INSERT TO f0d_worker
        WITH CHECK (actor_id = f0d.current_actor_id())
        """
    )

    # The terminal function runs as f0d_migration but retains the caller's
    # transaction-local authenticated context.  FORCE RLS therefore needs
    # narrow policies for only the rows and operations used by that function.
    for table in (
        "fixture_source_registry",
        "object_blob",
        "document_version",
        "document_processing_plan",
        "document_processing_unit",
        "job",
        "audit_event",
    ):
        op.execute(
            f"""
            CREATE POLICY migration_local_ocr_read ON f0d.{table}
            FOR SELECT TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
    for table in (
        "local_ocr_configuration",
        "local_ocr_run",
        "page_evidence_selection",
        "deferred_document_evidence",
    ):
        op.execute(
            f"""
            CREATE POLICY migration_local_ocr_read ON f0e.{table}
            FOR SELECT TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
    op.execute(
        """
        CREATE POLICY migration_local_ocr_job_update ON f0d.job
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
    for table in (
        "local_ocr_run",
        "page_evidence_selection",
        "deferred_document_evidence",
    ):
        op.execute(
            f"""
            CREATE POLICY migration_local_ocr_insert ON f0e.{table}
            FOR INSERT TO f0d_migration
            WITH CHECK (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
    op.execute(
        """
        CREATE POLICY migration_local_ocr_insert ON f0d.audit_event
        FOR INSERT TO f0d_migration
        WITH CHECK (
          enterprise_id = f0d.current_enterprise_id()
          AND f0d.context_session_authorized(enterprise_id)
        )
        """
    )

    op.execute(
        """
        REVOKE ALL ON f0e.local_ocr_configuration,
          f0e.local_ocr_run, f0e.page_evidence_selection,
          f0e.deferred_document_evidence
        FROM PUBLIC, f0d_runtime, f0d_worker
        """
    )
    op.execute(
        """
        GRANT SELECT ON f0e.local_ocr_configuration,
          f0e.local_ocr_run, f0e.page_evidence_selection,
          f0e.deferred_document_evidence
        TO f0d_runtime, f0d_worker
        """
    )
    op.execute(
        "GRANT INSERT ON f0e.local_ocr_configuration TO f0d_worker"
    )
    op.execute("GRANT USAGE ON SCHEMA f0e TO f0d_runtime, f0d_worker")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f0e.sha256_chain(text[]) "
        "TO f0d_runtime, f0d_worker"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f0e.local_ocr_job_idempotency_key(uuid,uuid,text,uuid,text) "
        "TO f0d_worker"
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION f0e.finalize_local_ocr_run(
          p_job_id uuid,
          p_lease_generation bigint,
          p_lease_token uuid,
          p_run_id uuid,
          p_audit_id uuid,
          p_page_evidence jsonb,
          p_deferred_evidence_id uuid DEFAULT NULL
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, f0d AS $$
        DECLARE
          v_enterprise_id uuid := f0d.current_enterprise_id();
          v_actor_id uuid := f0d.current_actor_id();
          v_job record;
          v_existing record;
          v_existing_audit record;
          v_normalized_payload jsonb := '[]'::jsonb;
          v_stored_payload jsonb := '[]'::jsonb;
          v_manifest_sha256 char(64);
          v_expected_run_chain_sha256 char(64);
          v_terminal_status text;
          v_native_count integer := 0;
          v_ocr_count integer := 0;
          v_manual_count integer := 0;
          v_deferred_count integer := 0;
          v_distinct_evidence_count integer := 0;
          v_distinct_unit_count integer := 0;
          v_invalid_count integer := 0;
          v_deferred_id uuid;
          v_row_count integer;
        BEGIN
          IF v_enterprise_id IS NULL OR v_actor_id IS NULL
             OR NOT f0d.context_session_authorized(v_enterprise_id) THEN
            RAISE EXCEPTION USING
              ERRCODE = '42501', MESSAGE = 'LOCAL_OCR_CONTEXT_INVALID';
          END IF;
          IF p_lease_generation <= 0 OR p_lease_token IS NULL
             OR p_run_id IS NULL OR p_audit_id IS NULL
             OR jsonb_typeof(p_page_evidence) <> 'array' THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'LOCAL_OCR_EVIDENCE_INVALID';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM jsonb_array_elements(p_page_evidence) AS item(value)
            WHERE jsonb_typeof(item.value) <> 'object'
               OR NOT item.value ?& ARRAY[
                 'evidence_id','processing_unit_id','selected_route',
                 'render_sha256','output_sha256','output_block_count',
                 'output_character_count',
                 'output_non_blank_character_count','mean_confidence_ppm',
                 'bbox_summary_sha256','reason_code','terminal_status'
               ]
               OR EXISTS (
                 SELECT 1 FROM jsonb_object_keys(item.value) AS key(name)
                 WHERE key.name <> ALL (ARRAY[
                   'evidence_id','processing_unit_id','selected_route',
                   'render_sha256','output_sha256','output_block_count',
                   'output_character_count',
                   'output_non_blank_character_count','mean_confidence_ppm',
                   'bbox_summary_sha256','reason_code','terminal_status'
                 ])
               )
          ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '22023', MESSAGE = 'LOCAL_OCR_EVIDENCE_INVALID';
          END IF;

          SELECT
            j.id AS job_id, j.kind, j.status, j.lease_generation,
            j.lease_token, j.lease_until, j.processing_plan_id,
            j.document_version_id, j.source_plan_sha256,
            j.local_ocr_configuration_id,
            j.local_ocr_configuration_sha256,
            p.source_document_id, p.visual_unit_count,
            p.native_candidate_count, p.ocr_required_count,
            p.manual_review_count, p.deferred_conversion,
            p.raw_text_persisted AS plan_raw_text_persisted,
            p.ocr_executed AS plan_ocr_executed,
            v.object_blob_id, b.sha256 AS input_object_sha256,
            r.source_group, r.document_type, r.corpus_role,
            r.enterprise_fact_allowed, r.current_regulation_allowed,
            r.search_publish_allowed, r.benchmark_tier AS source_benchmark_tier,
            r.external_processing_policy AS source_external_policy,
            c.benchmark_tier AS config_benchmark_tier,
            c.external_processing_policy AS config_external_policy,
            c.network_policy, c.raw_text_persisted AS config_raw_text_persisted,
            c.page_image_persisted AS config_page_image_persisted,
            c.max_pdf_pages, c.max_selected_pages_per_run,
            c.manual_review_confidence_floor_ppm
          INTO v_job
          FROM f0d.job AS j
          JOIN f0d.document_processing_plan AS p
            ON p.enterprise_id = j.enterprise_id
           AND p.id = j.processing_plan_id
           AND p.document_version_id = j.document_version_id
           AND p.source_plan_sha256 = j.source_plan_sha256
          JOIN f0d.document_version AS v
            ON v.enterprise_id = p.enterprise_id
           AND v.id = p.document_version_id
           AND v.source_document_id = p.source_document_id
          JOIN f0d.object_blob AS b
            ON b.enterprise_id = v.enterprise_id
           AND b.id = v.object_blob_id
          JOIN f0d.fixture_source_registry AS r
            ON r.enterprise_id = v.enterprise_id
           AND r.source_document_id = v.source_document_id
          JOIN f0e.local_ocr_configuration AS c
            ON c.enterprise_id = j.enterprise_id
           AND c.id = j.local_ocr_configuration_id
           AND c.configuration_sha256 = j.local_ocr_configuration_sha256
          WHERE j.enterprise_id = v_enterprise_id
            AND j.id = p_job_id
          FOR UPDATE OF j;

          IF NOT FOUND OR v_job.kind <> 'EXECUTE_LOCAL_OCR'
             OR v_job.manual_review_count <> 0
             OR v_job.plan_raw_text_persisted
             OR v_job.plan_ocr_executed
             OR v_job.source_benchmark_tier <> 'NONE'
             OR v_job.source_external_policy <> 'DENY'
             OR v_job.config_benchmark_tier <> 'NONE'
             OR v_job.config_external_policy <> 'DENY'
             OR v_job.network_policy <> 'DENY'
             OR v_job.manual_review_confidence_floor_ppm <> 0
             OR v_job.visual_unit_count > v_job.max_pdf_pages
             OR v_job.ocr_required_count > v_job.max_selected_pages_per_run
             OR v_job.config_raw_text_persisted
             OR v_job.config_page_image_persisted
             OR v_job.current_regulation_allowed
             OR v_job.search_publish_allowed
             OR (
               v_job.source_group = 'negative'
               AND v_job.enterprise_fact_allowed
             ) THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_SOURCE_CHAIN_INVALID';
          END IF;

          IF v_job.visual_unit_count > 0 AND NOT v_job.deferred_conversion THEN
            v_terminal_status := 'CANDIDATE_EVIDENCE_RECORDED';
            IF p_deferred_evidence_id IS NOT NULL
               OR jsonb_array_length(p_page_evidence) <> v_job.visual_unit_count
               OR v_job.native_candidate_count + v_job.ocr_required_count
                    <> v_job.visual_unit_count THEN
              RAISE EXCEPTION USING
                ERRCODE = '22023', MESSAGE = 'LOCAL_OCR_EVIDENCE_INVALID';
            END IF;
          ELSIF v_job.visual_unit_count = 0 AND v_job.deferred_conversion
                AND v_job.document_type = 'DOC' THEN
            v_terminal_status := 'DEFERRED_CONVERSION_REQUIRED';
            v_deferred_count := 1;
            IF p_deferred_evidence_id IS NULL
               OR jsonb_array_length(p_page_evidence) <> 0 THEN
              RAISE EXCEPTION USING
                ERRCODE = '22023', MESSAGE = 'LOCAL_OCR_EVIDENCE_INVALID';
            END IF;
          ELSE
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_PLAN_NOT_ELIGIBLE';
          END IF;

          IF v_job.visual_unit_count > 0 THEN
            WITH payload AS (
              SELECT *
              FROM jsonb_to_recordset(p_page_evidence) AS item(
                evidence_id uuid,
                processing_unit_id uuid,
                selected_route text,
                render_sha256 text,
                output_sha256 text,
                output_block_count integer,
                output_character_count integer,
                output_non_blank_character_count integer,
                mean_confidence_ppm integer,
                bbox_summary_sha256 text,
                reason_code text,
                terminal_status text
              )
            ), matched AS (
              SELECT item.*, unit.unit_ordinal, unit.candidate_decision,
                unit.native_text_identity_sha256,
                unit.native_characters
              FROM payload AS item
              JOIN f0d.document_processing_unit AS unit
                ON unit.enterprise_id = v_enterprise_id
               AND unit.processing_plan_id = v_job.processing_plan_id
               AND unit.id = item.processing_unit_id
            )
            SELECT
              count(*)::integer,
              count(DISTINCT evidence_id)::integer,
              count(DISTINCT processing_unit_id)::integer,
              count(*) FILTER (
                WHERE selected_route = 'NATIVE_REFERENCE'
              )::integer,
              count(*) FILTER (
                WHERE selected_route = 'LOCAL_OCR'
              )::integer,
              count(*) FILTER (
                WHERE selected_route = 'LOCAL_OCR'
                  AND output_non_blank_character_count = 0
              )::integer,
              count(*) FILTER (
                WHERE NOT COALESCE(
                  (
                    selected_route = 'NATIVE_REFERENCE'
                    AND candidate_decision = 'NATIVE_CANDIDATE'
                    AND native_text_identity_sha256 <>
                      repeat('0', 64)::char(64)
                    AND render_sha256 IS NULL
                    AND output_sha256 = native_text_identity_sha256
                    AND output_block_count = 0
                    AND output_character_count = native_characters
                    AND output_non_blank_character_count = native_characters
                    AND mean_confidence_ppm IS NULL
                    AND bbox_summary_sha256 IS NULL
                    AND reason_code = 'NATIVE_TEXT_REFERENCE_SELECTED'
                    AND terminal_status = 'NATIVE_REFERENCE'
                  ) OR (
                    selected_route = 'LOCAL_OCR'
                    AND candidate_decision = 'FULL_PAGE_OCR_REQUIRED'
                    AND render_sha256 ~ '^[0-9a-f]{64}$'
                    AND output_sha256 ~ '^[0-9a-f]{64}$'
                    AND bbox_summary_sha256 ~ '^[0-9a-f]{64}$'
                    AND output_block_count >= 0
                    AND output_character_count >= 0
                    AND output_non_blank_character_count BETWEEN 0
                      AND output_character_count
                    AND (
                      (
                        reason_code = 'LOCAL_OCR_EMPTY_REVIEW_REQUIRED'
                        AND output_non_blank_character_count = 0
                        AND mean_confidence_ppm IS NULL
                        AND terminal_status = 'MANUAL_REVIEW_REQUIRED'
                      ) OR (
                        reason_code = 'LOCAL_OCR_CANDIDATE_CAPTURED'
                        AND output_block_count > 0
                        AND output_character_count > 0
                        AND output_non_blank_character_count > 0
                        AND mean_confidence_ppm BETWEEN 0 AND 1000000
                        AND terminal_status = 'LOCAL_OCR_EVIDENCE'
                      )
                    )
                  ),
                  false
                )
              )::integer,
              COALESCE(
                jsonb_agg(
                  jsonb_build_object(
                    'evidence_id', evidence_id,
                    'processing_unit_id', processing_unit_id,
                    'selected_route', selected_route,
                    'render_sha256', render_sha256,
                    'output_sha256', output_sha256,
                    'output_block_count', output_block_count,
                    'output_character_count', output_character_count,
                    'output_non_blank_character_count',
                      output_non_blank_character_count,
                    'mean_confidence_ppm', mean_confidence_ppm,
                    'bbox_summary_sha256', bbox_summary_sha256,
                    'reason_code', reason_code,
                    'terminal_status', terminal_status
                  ) ORDER BY unit_ordinal
                ),
                '[]'::jsonb
              )
            INTO v_row_count, v_distinct_evidence_count,
              v_distinct_unit_count, v_native_count, v_ocr_count,
              v_manual_count, v_invalid_count, v_normalized_payload
            FROM matched;
            IF v_row_count <> v_job.visual_unit_count
               OR v_distinct_evidence_count <> v_job.visual_unit_count
               OR v_distinct_unit_count <> v_job.visual_unit_count
               OR v_invalid_count <> 0
               OR v_native_count <> v_job.native_candidate_count
               OR v_ocr_count <> v_job.ocr_required_count THEN
              RAISE EXCEPTION USING
                ERRCODE = '22023', MESSAGE = 'LOCAL_OCR_EVIDENCE_INVALID';
            END IF;
            v_deferred_count := 0;
          ELSE
            v_normalized_payload := '[]'::jsonb;
          END IF;

          SELECT * INTO v_existing
          FROM f0e.local_ocr_run
          WHERE enterprise_id = v_enterprise_id
            AND processing_plan_id = v_job.processing_plan_id;
          IF FOUND THEN
            SELECT COALESCE(
              jsonb_agg(
                jsonb_build_object(
                  'evidence_id', id,
                  'processing_unit_id', processing_unit_id,
                  'selected_route', selected_route,
                  'render_sha256', render_sha256,
                  'output_sha256', output_sha256,
                  'output_block_count', output_block_count,
                  'output_character_count', output_character_count,
                  'output_non_blank_character_count',
                    output_non_blank_character_count,
                  'mean_confidence_ppm', mean_confidence_ppm,
                  'bbox_summary_sha256', bbox_summary_sha256,
                  'reason_code', reason_code,
                  'terminal_status', terminal_status
                ) ORDER BY unit_ordinal
              ),
              '[]'::jsonb
            ) INTO v_stored_payload
            FROM f0e.page_evidence_selection
            WHERE enterprise_id = v_enterprise_id
              AND local_ocr_run_id = v_existing.id;
            SELECT id INTO v_deferred_id
            FROM f0e.deferred_document_evidence
            WHERE enterprise_id = v_enterprise_id
              AND local_ocr_run_id = v_existing.id;
            IF v_terminal_status = 'CANDIDATE_EVIDENCE_RECORDED' THEN
              SELECT encode(
                sha256(
                  convert_to(
                    jsonb_agg(
                      jsonb_build_object(
                        'unit_ordinal', unit_ordinal,
                        'evidence_chain_sha256', evidence_chain_sha256
                      ) ORDER BY unit_ordinal
                    )::text,
                    'UTF8'
                  )
                ),
                'hex'
              )::char(64)
              INTO v_manifest_sha256
              FROM f0e.page_evidence_selection
              WHERE enterprise_id = v_enterprise_id
                AND local_ocr_run_id = v_existing.id;
            ELSE
              SELECT f0e.sha256_chain(ARRAY[
                v_deferred_id::text,
                'DEFERRED_CONVERSION_REQUIRED',
                v_job.processing_plan_id::text,
                v_job.source_plan_sha256::text
              ]) INTO v_manifest_sha256;
            END IF;
            SELECT f0e.sha256_chain(ARRAY[
              v_existing.enterprise_id::text, v_existing.job_id::text,
              v_existing.lease_generation::text,
              v_existing.lease_token::text,
              v_existing.processing_plan_id::text,
              v_existing.document_version_id::text,
              v_existing.source_document_id::text,
              v_existing.object_blob_id::text,
              v_existing.input_object_sha256::text,
              v_existing.source_plan_sha256::text,
              v_existing.local_ocr_configuration_id::text,
              v_existing.local_ocr_configuration_sha256::text,
              v_existing.source_group,
              v_existing.enterprise_fact_allowed::text,
              v_existing.current_regulation_allowed::text,
              v_existing.search_publish_allowed::text,
              v_existing.terminal_status,
              v_existing.visual_unit_count::text,
              v_existing.native_reference_count::text,
              v_existing.local_ocr_count::text,
              v_existing.manual_review_required_count::text,
              v_existing.deferred_document_count::text,
              v_existing.output_manifest_sha256::text,
              v_existing.benchmark_tier,
              v_existing.external_processing_policy,
              v_existing.raw_text_persisted::text,
              v_existing.page_image_persisted::text
            ]) INTO v_expected_run_chain_sha256;
            SELECT id, actor_id, event_code, target_type, target_id,
              correlation_id, outcome_code
            INTO v_existing_audit
            FROM f0d.audit_event
            WHERE enterprise_id = v_enterprise_id AND id = p_audit_id;
            IF v_existing.id <> p_run_id
               OR v_existing.actor_id <> v_actor_id
               OR v_existing.job_id <> p_job_id
               OR v_existing.lease_generation <> p_lease_generation
               OR v_existing.lease_token <> p_lease_token
               OR v_existing.processing_plan_id <> v_job.processing_plan_id
               OR v_existing.document_version_id <> v_job.document_version_id
               OR v_existing.source_document_id <> v_job.source_document_id
               OR v_existing.object_blob_id <> v_job.object_blob_id
               OR v_existing.input_object_sha256 <> v_job.input_object_sha256
               OR v_existing.source_plan_sha256 <> v_job.source_plan_sha256
               OR v_existing.local_ocr_configuration_id <>
                    v_job.local_ocr_configuration_id
               OR v_existing.local_ocr_configuration_sha256 <>
                    v_job.local_ocr_configuration_sha256
               OR v_existing.source_group <> v_job.source_group
               OR v_existing.enterprise_fact_allowed <>
                    v_job.enterprise_fact_allowed
               OR v_existing.current_regulation_allowed
               OR v_existing.search_publish_allowed
               OR v_existing.terminal_status <> v_terminal_status
               OR v_existing.visual_unit_count <> v_job.visual_unit_count
               OR v_existing.native_reference_count <> v_native_count
               OR v_existing.local_ocr_count <> v_ocr_count
               OR v_existing.manual_review_required_count <> v_manual_count
               OR v_existing.deferred_document_count <> v_deferred_count
               OR v_existing.output_manifest_sha256 <> v_manifest_sha256
               OR v_existing.benchmark_tier <> 'NONE'
               OR v_existing.external_processing_policy <> 'DENY'
               OR v_existing.raw_text_persisted
               OR v_existing.page_image_persisted
               OR v_existing.evidence_chain_sha256 <>
                    v_expected_run_chain_sha256
               OR v_stored_payload <> v_normalized_payload
               OR v_deferred_id IS DISTINCT FROM p_deferred_evidence_id
               OR v_job.status <> 'SUCCEEDED'
               OR v_job.lease_generation <> p_lease_generation
               OR v_job.lease_token <> p_lease_token
               OR v_existing_audit.id IS NULL
               OR v_existing_audit.actor_id <> v_actor_id
               OR v_existing_audit.event_code <>
                    'LOCAL_OCR_EVIDENCE_FINALIZED'
               OR v_existing_audit.target_type <> 'LOCAL_OCR_RUN'
               OR v_existing_audit.target_id <> p_run_id
               OR v_existing_audit.correlation_id <> p_job_id
               OR v_existing_audit.outcome_code <> 'SUCCESS' THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_TERMINAL_CONFLICT';
            END IF;
            RETURN v_existing.id;
          END IF;

          IF v_job.status <> 'RUNNING'
             OR v_job.lease_generation <> p_lease_generation
             OR v_job.lease_token <> p_lease_token
             OR v_job.lease_until IS NULL
             OR v_job.lease_until <= statement_timestamp() THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_LEASE_STALE';
          END IF;

          IF v_job.visual_unit_count > 0 THEN
            INSERT INTO f0e.page_evidence_selection(
              id, enterprise_id, local_ocr_run_id, processing_plan_id,
              document_version_id, source_document_id, source_plan_sha256,
              local_ocr_configuration_id,
              local_ocr_configuration_sha256, processing_unit_id,
              source_unit_id, unit_ordinal, unit_kind, page_no,
              candidate_decision, source_evidence_sha256,
              source_native_text_sha256, source_native_characters,
              selected_route, render_sha256, output_sha256,
              output_block_count, output_character_count,
              output_non_blank_character_count,
              mean_confidence_ppm, bbox_summary_sha256, reason_code,
              terminal_status,
              run_terminal_status
            )
            SELECT
              item.evidence_id, v_enterprise_id, p_run_id,
              v_job.processing_plan_id, v_job.document_version_id,
              v_job.source_document_id, v_job.source_plan_sha256,
              v_job.local_ocr_configuration_id,
              v_job.local_ocr_configuration_sha256, unit.id,
              unit.source_unit_id, unit.unit_ordinal, unit.unit_kind,
              unit.page_no, unit.candidate_decision, unit.evidence_sha256,
              unit.native_text_identity_sha256, unit.native_characters,
              item.selected_route, item.render_sha256::char(64),
              item.output_sha256::char(64), item.output_block_count,
              item.output_character_count,
              item.output_non_blank_character_count,
              item.mean_confidence_ppm,
              item.bbox_summary_sha256::char(64), item.reason_code,
              item.terminal_status,
              'CANDIDATE_EVIDENCE_RECORDED'
            FROM jsonb_to_recordset(p_page_evidence) AS item(
              evidence_id uuid,
              processing_unit_id uuid,
              selected_route text,
              render_sha256 text,
              output_sha256 text,
              output_block_count integer,
              output_character_count integer,
              output_non_blank_character_count integer,
              mean_confidence_ppm integer,
              bbox_summary_sha256 text,
              reason_code text,
              terminal_status text
            )
            JOIN f0d.document_processing_unit AS unit
              ON unit.enterprise_id = v_enterprise_id
             AND unit.processing_plan_id = v_job.processing_plan_id
             AND unit.id = item.processing_unit_id;
            GET DIAGNOSTICS v_row_count = ROW_COUNT;
            IF v_row_count <> v_job.visual_unit_count THEN
              RAISE EXCEPTION USING
                ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_PAGE_CROSSWIRE';
            END IF;
            SELECT encode(
              sha256(
                convert_to(
                  jsonb_agg(
                    jsonb_build_object(
                      'unit_ordinal', unit_ordinal,
                      'evidence_chain_sha256', evidence_chain_sha256
                    ) ORDER BY unit_ordinal
                  )::text,
                  'UTF8'
                )
              ),
              'hex'
            )::char(64)
            INTO v_manifest_sha256
            FROM f0e.page_evidence_selection
            WHERE enterprise_id = v_enterprise_id
              AND local_ocr_run_id = p_run_id;
          ELSE
            INSERT INTO f0e.deferred_document_evidence(
              id, enterprise_id, local_ocr_run_id, processing_plan_id,
              document_version_id, source_document_id, source_plan_sha256,
              local_ocr_configuration_id,
              local_ocr_configuration_sha256
            ) VALUES (
              p_deferred_evidence_id, v_enterprise_id, p_run_id,
              v_job.processing_plan_id, v_job.document_version_id,
              v_job.source_document_id, v_job.source_plan_sha256,
              v_job.local_ocr_configuration_id,
              v_job.local_ocr_configuration_sha256
            );
            SELECT f0e.sha256_chain(ARRAY[
              p_deferred_evidence_id::text,
              'DEFERRED_CONVERSION_REQUIRED',
              v_job.processing_plan_id::text,
              v_job.source_plan_sha256::text
            ]) INTO v_manifest_sha256;
          END IF;

          INSERT INTO f0e.local_ocr_run(
            id, enterprise_id, actor_id, job_id, job_kind,
            lease_generation, lease_token, processing_plan_id,
            document_version_id, source_document_id, object_blob_id,
            input_object_sha256, source_plan_sha256,
            local_ocr_configuration_id,
            local_ocr_configuration_sha256, source_group,
            enterprise_fact_allowed, current_regulation_allowed,
            search_publish_allowed, terminal_status, visual_unit_count,
            native_reference_count, local_ocr_count,
            manual_review_required_count, deferred_document_count,
            output_manifest_sha256
          ) VALUES (
            p_run_id, v_enterprise_id, v_actor_id, p_job_id,
            'EXECUTE_LOCAL_OCR', p_lease_generation, p_lease_token,
            v_job.processing_plan_id, v_job.document_version_id,
            v_job.source_document_id, v_job.object_blob_id,
            v_job.input_object_sha256, v_job.source_plan_sha256,
            v_job.local_ocr_configuration_id,
            v_job.local_ocr_configuration_sha256, v_job.source_group,
            v_job.enterprise_fact_allowed,
            v_job.current_regulation_allowed,
            v_job.search_publish_allowed, v_terminal_status,
            v_job.visual_unit_count, v_native_count, v_ocr_count,
            v_manual_count, v_deferred_count, v_manifest_sha256
          );

          INSERT INTO f0d.audit_event(
            id, enterprise_id, actor_id, event_code, target_type,
            target_id, correlation_id, outcome_code
          ) VALUES (
            p_audit_id, v_enterprise_id, v_actor_id,
            'LOCAL_OCR_EVIDENCE_FINALIZED', 'LOCAL_OCR_RUN',
            p_run_id, p_job_id, 'SUCCESS'
          );

          UPDATE f0d.job
          SET status = 'SUCCEEDED',
              finished_at = statement_timestamp(),
              progress_done = v_job.visual_unit_count,
              progress_total = v_job.visual_unit_count,
              lease_owner = NULL,
              lease_until = NULL,
              heartbeat_at = NULL,
              error_code = NULL
          WHERE enterprise_id = v_enterprise_id
            AND id = p_job_id
            AND kind = 'EXECUTE_LOCAL_OCR'
            AND status = 'RUNNING'
            AND lease_generation = p_lease_generation
            AND lease_token = p_lease_token
            AND lease_until > statement_timestamp();
          GET DIAGNOSTICS v_row_count = ROW_COUNT;
          IF v_row_count <> 1 THEN
            RAISE EXCEPTION USING
              ERRCODE = '55000', MESSAGE = 'LOCAL_OCR_LEASE_STALE';
          END IF;
          RETURN p_run_id;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f0e.finalize_local_ocr_run(uuid,bigint,uuid,uuid,uuid,jsonb,uuid) "
        "FROM PUBLIC, f0d_runtime"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f0e.finalize_local_ocr_run(uuid,bigint,uuid,uuid,uuid,jsonb,uuid) "
        "TO f0d_worker"
    )


def downgrade() -> None:
    raise RuntimeError("F0E_LOCAL_OCR_EVIDENCE_IS_IRREVERSIBLE")
