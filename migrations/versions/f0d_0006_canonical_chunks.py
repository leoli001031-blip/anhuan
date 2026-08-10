"""Add encrypted canonical blocks and parent/child chunks.

Revision ID: f0d_0006
Revises: f0d_0005
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f0d_0006"
down_revision: str | None = "f0d_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = (
    "configuration",
    "run",
    "document_scope",
    "page",
    "block",
    "chunk",
    "chunk_block_link",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA f0i AUTHORIZATION f0d_migration")
    op.execute("REVOKE ALL ON SCHEMA f0i FROM PUBLIC, f0d_runtime, f0d_worker")

    _strengthen_upstream_keys()
    _create_validation_functions()
    _create_configuration()
    _create_run()
    _create_document_scope()
    _create_page()
    _create_block()
    _create_chunk()
    _create_chunk_block_link()
    _make_append_only_and_tenant_scoped()
    _lock_down_privileges()


def _strengthen_upstream_keys() -> None:
    # This adds no mutable state.  It only makes the complete registered
    # source/gate tuple addressable by the downstream composite FK, so a
    # negative Fixture cannot be relabelled as core inside F0-I.
    op.execute(
        """
        ALTER TABLE f0d.fixture_source_registry
        ADD CONSTRAINT fixture_source_f0i_provenance_uq UNIQUE (
          enterprise_id, source_document_id, expected_sha256,
          expected_size_bytes, source_group, document_type, corpus_role,
          enterprise_fact_allowed, current_regulation_allowed,
          search_publish_allowed
        )
        """
    )


def _create_validation_functions() -> None:
    # JSON is used only for the observed OCR quadrilateral.  This validator
    # deliberately accepts no object keys or string values, preventing the
    # geometry column from becoming an accidental plaintext escape hatch.
    op.execute(
        r"""
        CREATE FUNCTION f0i.valid_bbox_ppm(p_bbox jsonb)
        RETURNS boolean
        LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        DECLARE
          v_point jsonb;
          v_coordinate jsonb;
          v_min_x integer := 1000001;
          v_max_x integer := -1;
          v_min_y integer := 1000001;
          v_max_y integer := -1;
        BEGIN
          IF jsonb_typeof(p_bbox) <> 'array'
             OR jsonb_array_length(p_bbox) <> 4 THEN
            RETURN false;
          END IF;
          FOR v_point IN SELECT value FROM jsonb_array_elements(p_bbox)
          LOOP
            IF jsonb_typeof(v_point) <> 'array'
               OR jsonb_array_length(v_point) <> 2 THEN
              RETURN false;
            END IF;
            FOR v_coordinate IN
              SELECT value FROM jsonb_array_elements(v_point)
            LOOP
              IF jsonb_typeof(v_coordinate) <> 'number'
                 OR v_coordinate::text !~ '^(0|[1-9][0-9]{0,6})$'
                 OR (v_coordinate::text)::integer > 1000000 THEN
                RETURN false;
              END IF;
            END LOOP;
            v_min_x := least(v_min_x, (v_point ->> 0)::integer);
            v_max_x := greatest(v_max_x, (v_point ->> 0)::integer);
            v_min_y := least(v_min_y, (v_point ->> 1)::integer);
            v_max_y := greatest(v_max_y, (v_point ->> 1)::integer);
          END LOOP;
          RETURN v_min_x < v_max_x AND v_min_y < v_max_y;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f0i.valid_bbox_ppm(jsonb) FROM PUBLIC")


def _create_configuration() -> None:
    op.execute(
        """
        CREATE TABLE f0i.configuration (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          schema_version text NOT NULL DEFAULT 'f0i-canonical-chunks-v0.1'
            CHECK (schema_version = 'f0i-canonical-chunks-v0.1'),
          normalization_rule text NOT NULL DEFAULT 'UTF8_NFC_LF_V1'
            CHECK (normalization_rule = 'UTF8_NFC_LF_V1'),
          parser_rule_version text NOT NULL
            CHECK (parser_rule_version ~ '^[A-Za-z0-9_.:/-]{1,96}$'),
          chunk_rule_version text NOT NULL
            CHECK (chunk_rule_version ~ '^[A-Za-z0-9_.:/-]{1,96}$'),
          location_rule_version text NOT NULL
            CHECK (location_rule_version ~ '^[A-Za-z0-9_.:/-]{1,96}$'),
          pypdf_version text NOT NULL DEFAULT '6.14.2'
            CHECK (pypdf_version = '6.14.2'),
          pypdf_license_expression text NOT NULL DEFAULT 'BSD-3-Clause'
            CHECK (pypdf_license_expression = 'BSD-3-Clause'),
          ocr_model_id text NOT NULL DEFAULT 'PP-OCRv6-small'
            CHECK (ocr_model_id = 'PP-OCRv6-small'),
          ocr_family text NOT NULL DEFAULT 'PP-OCRv6'
            CHECK (ocr_family = 'PP-OCRv6'),
          rapidocr_version text NOT NULL DEFAULT '3.9.2'
            CHECK (rapidocr_version = '3.9.2'),
          ocr_model_status text NOT NULL DEFAULT 'NOT_EVALUATED'
            CHECK (ocr_model_status = 'NOT_EVALUATED'),
          ocr_configuration_sha256 char(64) NOT NULL
            CHECK (ocr_configuration_sha256 ~ '^[0-9a-f]{64}$'),
          ocr_model_bundle_sha256 char(64) NOT NULL
            CHECK (ocr_model_bundle_sha256 ~ '^[0-9a-f]{64}$'),
          ocr_execution_profile_sha256 char(64) NOT NULL
            CHECK (ocr_execution_profile_sha256 ~ '^[0-9a-f]{64}$'),
          ocr_runtime_image_id char(71) NOT NULL
            CHECK (ocr_runtime_image_id ~ '^sha256:[0-9a-f]{64}$'),
          ocr_runtime_lock_sha256 char(64) NOT NULL
            CHECK (ocr_runtime_lock_sha256 ~ '^[0-9a-f]{64}$'),
          ocr_output_contract_sha256 char(64) NOT NULL
            CHECK (ocr_output_contract_sha256 ~ '^[0-9a-f]{64}$'),
          registered_full_plan_sha256 char(64) NOT NULL
            CHECK (registered_full_plan_sha256 ~ '^[0-9a-f]{64}$'),
          cipher_profile text NOT NULL DEFAULT 'PGP_SYM_AES256_V1'
            CHECK (cipher_profile = 'PGP_SYM_AES256_V1'),
          key_source text NOT NULL DEFAULT 'LOCAL_FIXTURE_FILE_0600'
            CHECK (key_source = 'LOCAL_FIXTURE_FILE_0600'),
          key_fingerprint_sha256 char(64) NOT NULL
            CHECK (key_fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
          key_verifier_plaintext_sha256 char(64) NOT NULL
            CHECK (key_verifier_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          key_verifier_ciphertext bytea NOT NULL
            CHECK (octet_length(key_verifier_ciphertext) BETWEEN 32 AND 4096),
          key_verifier_ciphertext_sha256 char(64) NOT NULL
            CHECK (key_verifier_ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          child_min_characters integer NOT NULL DEFAULT 300
            CHECK (child_min_characters = 300),
          child_max_characters integer NOT NULL DEFAULT 800
            CHECK (child_max_characters = 800),
          child_overlap_characters integer NOT NULL DEFAULT 0
            CHECK (child_overlap_characters = 0),
          maximum_body_bytes bigint NOT NULL DEFAULT 4194304
            CHECK (maximum_body_bytes = 4194304),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          search_status text NOT NULL DEFAULT 'SEARCH_NOT_READY'
            CHECK (search_status = 'SEARCH_NOT_READY'),
          production_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT production_allowed),
          raw_plaintext_columns_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT raw_plaintext_columns_allowed),
          configuration_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              schema_version, normalization_rule, parser_rule_version,
              chunk_rule_version, location_rule_version, pypdf_version,
              pypdf_license_expression, ocr_model_id, ocr_family,
              rapidocr_version, ocr_model_status,
              ocr_configuration_sha256::text,
              ocr_model_bundle_sha256::text,
              ocr_execution_profile_sha256::text,
              ocr_runtime_image_id::text,
              ocr_runtime_lock_sha256::text,
              ocr_output_contract_sha256::text,
              registered_full_plan_sha256::text, cipher_profile, key_source,
              key_fingerprint_sha256::text,
              key_verifier_plaintext_sha256::text,
              child_min_characters::text, child_max_characters::text,
              child_overlap_characters::text, maximum_body_bytes::text,
              benchmark_tier, external_processing_policy, search_status,
              production_allowed::text,
              raw_plaintext_columns_allowed::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT configuration_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT configuration_identity_uq UNIQUE (
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT configuration_hash_uq UNIQUE (
            enterprise_id, configuration_sha256
          ),
          CONSTRAINT configuration_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT configuration_ciphertext_hash_ck CHECK (
            key_verifier_ciphertext_sha256 = encode(
              f0f_crypto.digest(key_verifier_ciphertext, 'sha256'), 'hex'
            )::char(64)
          )
        )
        """
    )


def _create_run() -> None:
    op.execute(
        """
        CREATE TABLE f0i.run (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          profile text NOT NULL CHECK (profile IN ('smoke','full')),
          input_manifest_sha256 char(64) NOT NULL
            CHECK (input_manifest_sha256 ~ '^[0-9a-f]{64}$'),
          input_summary_sha256 char(64) NOT NULL
            CHECK (input_summary_sha256 ~ '^[0-9a-f]{64}$'),
          replay_summary_sha256 char(64) NOT NULL
            CHECK (replay_summary_sha256 ~ '^[0-9a-f]{64}$'),
          requested_document_count integer NOT NULL CHECK (
            (profile = 'smoke' AND requested_document_count = 10)
            OR (profile = 'full' AND requested_document_count = 26)
          ),
          resolved_document_count integer NOT NULL CHECK (
            resolved_document_count = requested_document_count
          ),
          visual_document_count integer NOT NULL CHECK (
            visual_document_count BETWEEN 0 AND resolved_document_count
          ),
          structure_document_count integer NOT NULL CHECK (
            structure_document_count BETWEEN 0 AND resolved_document_count
          ),
          deferred_document_count integer NOT NULL CHECK (
            deferred_document_count BETWEEN 0 AND resolved_document_count
          ),
          visual_unit_count integer NOT NULL CHECK (visual_unit_count >= 0),
          native_visual_count integer NOT NULL CHECK (native_visual_count >= 0),
          ocr_visual_count integer NOT NULL CHECK (ocr_visual_count >= 0),
          structure_unit_count integer NOT NULL CHECK (structure_unit_count >= 0),
          parent_chunk_count integer NOT NULL CHECK (parent_chunk_count >= 0),
          child_chunk_count integer NOT NULL CHECK (child_chunk_count >= 0),
          block_count integer NOT NULL CHECK (block_count >= 0),
          ocr_call_count integer NOT NULL CHECK (ocr_call_count >= 0),
          error_count integer NOT NULL DEFAULT 0 CHECK (error_count = 0),
          terminal_status text NOT NULL DEFAULT 'LOCAL_CANONICAL_CHUNKS_READY'
            CHECK (terminal_status = 'LOCAL_CANONICAL_CHUNKS_READY'),
          accuracy_status text NOT NULL DEFAULT 'ACCURACY_NOT_EVALUATED'
            CHECK (accuracy_status = 'ACCURACY_NOT_EVALUATED'),
          search_status text NOT NULL DEFAULT 'SEARCH_NOT_READY'
            CHECK (search_status = 'SEARCH_NOT_READY'),
          production_status text NOT NULL DEFAULT 'NOT_PRODUCTION'
            CHECK (production_status = 'NOT_PRODUCTION'),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          run_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, profile,
              input_manifest_sha256::text, input_summary_sha256::text
            ])
          ) STORED,
          run_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, actor_id::text,
              configuration_id::text, configuration_sha256::text, profile,
              input_manifest_sha256::text, input_summary_sha256::text,
              replay_summary_sha256::text,
              requested_document_count::text, resolved_document_count::text,
              visual_document_count::text, structure_document_count::text,
              deferred_document_count::text, visual_unit_count::text,
              native_visual_count::text, ocr_visual_count::text,
              structure_unit_count::text, parent_chunk_count::text,
              child_chunk_count::text, block_count::text,
              ocr_call_count::text, error_count::text, terminal_status,
              accuracy_status, search_status, production_status,
              benchmark_tier, external_processing_policy
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT run_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT run_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            run_identity_sha256
          ),
          CONSTRAINT run_replay_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            profile, input_manifest_sha256, input_summary_sha256
          ),
          CONSTRAINT run_actor_fk FOREIGN KEY (
            enterprise_id, actor_id
          ) REFERENCES f0d.enterprise_membership(enterprise_id, actor_id),
          CONSTRAINT run_configuration_fk FOREIGN KEY (
            enterprise_id, configuration_id, configuration_sha256
          ) REFERENCES f0i.configuration(
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT run_partition_ck CHECK (
            visual_document_count + structure_document_count
              + deferred_document_count = resolved_document_count
            AND native_visual_count + ocr_visual_count = visual_unit_count
          )
        )
        """
    )


def _create_document_scope() -> None:
    op.execute(
        """
        CREATE TABLE f0i.document_scope (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          first_run_id uuid NOT NULL,
          first_run_identity_sha256 char(64) NOT NULL
            CHECK (first_run_identity_sha256 ~ '^[0-9a-f]{64}$'),
          document_version_id uuid NOT NULL,
          object_blob_id uuid NOT NULL,
          source_object_sha256 char(64) NOT NULL
            CHECK (source_object_sha256 ~ '^[0-9a-f]{64}$'),
          source_object_size_bytes bigint NOT NULL
            CHECK (source_object_size_bytes > 0),
          processing_plan_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          source_schema_version text NOT NULL
            CHECK (source_schema_version ~ '^[A-Za-z0-9_./-]{1,64}$'),
          source_rule_version text NOT NULL
            CHECK (source_rule_version ~ '^[A-Za-z0-9_./-]{1,64}$'),
          document_type text NOT NULL
            CHECK (document_type IN ('PDF','DOC','DOCX','JPEG','XLSX')),
          source_group text NOT NULL CHECK (source_group IN ('core','negative')),
          corpus_role text NOT NULL
            CHECK (corpus_role IN ('CORE_FIXTURE','NEGATIVE_TEST_ONLY')),
          scope_kind text NOT NULL CHECK (
            scope_kind IN ('VISUAL','STRUCTURE','DEFERRED')
          ),
          visual_unit_count integer NOT NULL CHECK (visual_unit_count >= 0),
          structure_unit_count integer NOT NULL CHECK (structure_unit_count >= 0),
          structure_summary_sha256 char(64)
            CHECK (
              structure_summary_sha256 IS NULL
              OR structure_summary_sha256 ~ '^[0-9a-f]{64}$'
            ),
          docx_paragraph_count integer CHECK (
            docx_paragraph_count IS NULL OR docx_paragraph_count >= 0
          ),
          docx_table_count integer CHECK (
            docx_table_count IS NULL OR docx_table_count >= 0
          ),
          docx_row_count integer CHECK (
            docx_row_count IS NULL OR docx_row_count >= 0
          ),
          docx_cell_count integer CHECK (
            docx_cell_count IS NULL OR docx_cell_count >= 0
          ),
          xlsx_sheet_count integer CHECK (
            xlsx_sheet_count IS NULL OR xlsx_sheet_count >= 0
          ),
          xlsx_cell_count integer CHECK (
            xlsx_cell_count IS NULL OR xlsx_cell_count >= 0
          ),
          xlsx_value_cell_count integer CHECK (
            xlsx_value_cell_count IS NULL OR xlsx_value_cell_count >= 0
          ),
          xlsx_formula_count integer CHECK (
            xlsx_formula_count IS NULL OR xlsx_formula_count >= 0
          ),
          xlsx_formula_cached_value_count integer CHECK (
            xlsx_formula_cached_value_count IS NULL
            OR xlsx_formula_cached_value_count >= 0
          ),
          deferred_reason_code text,
          enterprise_fact_allowed boolean NOT NULL,
          current_regulation_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT current_regulation_allowed),
          search_publish_allowed boolean NOT NULL DEFAULT false
            CHECK (NOT search_publish_allowed),
          terminal_status text NOT NULL CHECK (terminal_status IN (
            'CANONICAL_SCOPE_INCLUDED','DEFERRED_CONVERSION_REQUIRED'
          )),
          benchmark_tier text NOT NULL DEFAULT 'NONE'
            CHECK (benchmark_tier = 'NONE'),
          external_processing_policy text NOT NULL DEFAULT 'DENY'
            CHECK (external_processing_policy = 'DENY'),
          scope_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, document_version_id::text,
              object_blob_id::text, source_object_sha256::text,
              source_object_size_bytes::text,
              processing_plan_id::text, source_document_id::text,
              source_plan_sha256::text, source_schema_version,
              source_rule_version, document_type, source_group, corpus_role,
              scope_kind, visual_unit_count::text,
              structure_unit_count::text,
              structure_summary_sha256::text, deferred_reason_code,
              docx_paragraph_count::text, docx_table_count::text,
              docx_row_count::text, docx_cell_count::text,
              xlsx_sheet_count::text, xlsx_cell_count::text,
              xlsx_value_cell_count::text, xlsx_formula_count::text,
              xlsx_formula_cached_value_count::text,
              enterprise_fact_allowed::text,
              current_regulation_allowed::text,
              search_publish_allowed::text
            ])
          ) STORED,
          scope_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, first_run_id::text,
              first_run_identity_sha256::text, document_version_id::text,
              object_blob_id::text, source_object_sha256::text,
              processing_plan_id::text, source_document_id::text,
              source_plan_sha256::text, scope_kind, terminal_status,
              benchmark_tier, external_processing_policy
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT document_scope_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT document_scope_document_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            document_version_id
          ),
          CONSTRAINT document_scope_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256, scope_identity_sha256
          ),
          CONSTRAINT document_scope_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256
          ),
          CONSTRAINT document_scope_typed_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256, document_type
          ),
          CONSTRAINT document_scope_configuration_fk FOREIGN KEY (
            enterprise_id, configuration_id, configuration_sha256
          ) REFERENCES f0i.configuration(
            enterprise_id, id, configuration_sha256
          ),
          CONSTRAINT document_scope_first_run_fk FOREIGN KEY (
            enterprise_id, first_run_id, configuration_id,
            configuration_sha256, first_run_identity_sha256
          ) REFERENCES f0i.run(
            enterprise_id, id, configuration_id,
            configuration_sha256, run_identity_sha256
          ),
          CONSTRAINT document_scope_version_fk FOREIGN KEY (
            enterprise_id, document_version_id, object_blob_id,
            source_document_id
          ) REFERENCES f0d.document_version(
            enterprise_id, id, object_blob_id, source_document_id
          ),
          CONSTRAINT document_scope_object_fk FOREIGN KEY (
            enterprise_id, object_blob_id, source_object_sha256
          ) REFERENCES f0d.object_blob(enterprise_id, id, sha256),
          CONSTRAINT document_scope_registry_snapshot_fk FOREIGN KEY (
            enterprise_id, source_document_id, source_object_sha256,
            source_object_size_bytes, source_group, document_type,
            corpus_role, enterprise_fact_allowed,
            current_regulation_allowed, search_publish_allowed
          ) REFERENCES f0d.fixture_source_registry(
            enterprise_id, source_document_id, expected_sha256,
            expected_size_bytes, source_group, document_type, corpus_role,
            enterprise_fact_allowed, current_regulation_allowed,
            search_publish_allowed
          ),
          CONSTRAINT document_scope_plan_fk FOREIGN KEY (
            enterprise_id, processing_plan_id, document_version_id,
            source_document_id, source_plan_sha256
          ) REFERENCES f0d.document_processing_plan(
            enterprise_id, id, document_version_id,
            source_document_id, source_plan_sha256
          ),
          CONSTRAINT document_scope_kind_ck CHECK (
            (
              scope_kind = 'VISUAL'
              AND document_type IN ('PDF','JPEG')
              AND visual_unit_count > 0
              AND structure_unit_count = 0
              AND structure_summary_sha256 IS NULL
              AND docx_paragraph_count IS NULL
              AND docx_table_count IS NULL AND docx_row_count IS NULL
              AND docx_cell_count IS NULL AND xlsx_sheet_count IS NULL
              AND xlsx_cell_count IS NULL AND xlsx_value_cell_count IS NULL
              AND xlsx_formula_count IS NULL
              AND xlsx_formula_cached_value_count IS NULL
              AND deferred_reason_code IS NULL
              AND terminal_status = 'CANONICAL_SCOPE_INCLUDED'
            ) OR (
              scope_kind = 'STRUCTURE'
              AND document_type IN ('DOCX','XLSX')
              AND visual_unit_count = 0
              AND structure_unit_count > 0
              AND structure_summary_sha256 IS NOT NULL
              AND (
                (
                  document_type = 'DOCX'
                  AND docx_paragraph_count IS NOT NULL
                  AND docx_table_count IS NOT NULL
                  AND docx_row_count IS NOT NULL
                  AND docx_cell_count IS NOT NULL
                  AND xlsx_sheet_count IS NULL AND xlsx_cell_count IS NULL
                  AND xlsx_value_cell_count IS NULL
                  AND xlsx_formula_count IS NULL
                  AND xlsx_formula_cached_value_count IS NULL
                ) OR (
                  document_type = 'XLSX'
                  AND docx_paragraph_count IS NULL
                  AND docx_table_count IS NULL AND docx_row_count IS NULL
                  AND docx_cell_count IS NULL
                  AND xlsx_sheet_count IS NOT NULL
                  AND xlsx_cell_count IS NOT NULL
                  AND xlsx_value_cell_count IS NOT NULL
                  AND xlsx_formula_count IS NOT NULL
                  AND xlsx_formula_cached_value_count IS NOT NULL
                  AND xlsx_formula_cached_value_count <= xlsx_formula_count
                )
              )
              AND deferred_reason_code IS NULL
              AND terminal_status = 'CANONICAL_SCOPE_INCLUDED'
            ) OR (
              scope_kind = 'DEFERRED'
              AND document_type = 'DOC'
              AND visual_unit_count = 0
              AND structure_unit_count = 0
              AND structure_summary_sha256 IS NULL
              AND docx_paragraph_count IS NULL
              AND docx_table_count IS NULL AND docx_row_count IS NULL
              AND docx_cell_count IS NULL AND xlsx_sheet_count IS NULL
              AND xlsx_cell_count IS NULL AND xlsx_value_cell_count IS NULL
              AND xlsx_formula_count IS NULL
              AND xlsx_formula_cached_value_count IS NULL
              AND deferred_reason_code = 'DEFERRED_CONVERSION_REQUIRED'
              AND terminal_status = 'DEFERRED_CONVERSION_REQUIRED'
            )
          ),
          CONSTRAINT document_scope_negative_gates_ck CHECK (
            (
              source_group = 'core'
              AND corpus_role = 'CORE_FIXTURE'
            ) OR (
              source_group = 'negative'
              AND corpus_role = 'NEGATIVE_TEST_ONLY'
              AND NOT enterprise_fact_allowed
              AND NOT current_regulation_allowed
              AND NOT search_publish_allowed
            )
          )
        )
        """
    )


def _create_page() -> None:
    op.execute(
        """
        CREATE TABLE f0i.page (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          document_scope_id uuid NOT NULL,
          first_run_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          source_processing_unit_id uuid NOT NULL,
          source_unit_id char(64) NOT NULL
            CHECK (source_unit_id ~ '^[0-9a-f]{64}$'),
          source_unit_ordinal integer NOT NULL CHECK (source_unit_ordinal > 0),
          source_unit_kind text NOT NULL
            CHECK (source_unit_kind IN ('PAGE','IMAGE')),
          page_no integer NOT NULL CHECK (page_no > 0),
          candidate_decision text NOT NULL CHECK (
            candidate_decision IN ('NATIVE_CANDIDATE','FULL_PAGE_OCR_REQUIRED')
          ),
          selected_route text NOT NULL
            CHECK (selected_route IN ('NATIVE_REFERENCE','LOCAL_OCR')),
          source_unit_evidence_sha256 char(64) NOT NULL
            CHECK (source_unit_evidence_sha256 ~ '^[0-9a-f]{64}$'),
          native_text_identity_sha256 char(64) NOT NULL
            CHECK (native_text_identity_sha256 ~ '^[0-9a-f]{64}$'),
          native_characters integer NOT NULL CHECK (native_characters >= 0),
          source_page_output_sha256 char(64) NOT NULL
            CHECK (source_page_output_sha256 ~ '^[0-9a-f]{64}$'),
          source_page_evidence_sha256 char(64) NOT NULL
            CHECK (source_page_evidence_sha256 ~ '^[0-9a-f]{64}$'),
          rotation smallint CHECK (
            rotation IS NULL OR rotation IN (0,90,180,270)
          ),
          media_left numeric(14,3),
          media_bottom numeric(14,3),
          media_right numeric(14,3),
          media_top numeric(14,3),
          crop_left numeric(14,3),
          crop_bottom numeric(14,3),
          crop_right numeric(14,3),
          crop_top numeric(14,3),
          width_px integer CHECK (width_px IS NULL OR width_px > 0),
          height_px integer CHECK (height_px IS NULL OR height_px > 0),
          ocr_render_width_px integer CHECK (
            ocr_render_width_px IS NULL OR ocr_render_width_px > 0
          ),
          ocr_render_height_px integer CHECK (
            ocr_render_height_px IS NULL OR ocr_render_height_px > 0
          ),
          ocr_render_dpi integer CHECK (
            ocr_render_dpi IS NULL OR ocr_render_dpi = 250
          ),
          ocr_render_origin text CHECK (
            ocr_render_origin IS NULL OR ocr_render_origin IN (
              'PDFIUM_250_DPI','JPEG_DECODED_SOURCE_PIXELS'
            )
          ),
          ocr_renderer_id text CHECK (
            ocr_renderer_id IS NULL
            OR ocr_renderer_id ~ '^[A-Za-z0-9_.-]{1,64}$'
          ),
          ocr_renderer_version text CHECK (
            ocr_renderer_version IS NULL
            OR ocr_renderer_version ~ '^[A-Za-z0-9_.:+-]{1,96}$'
          ),
          ocr_render_sha256 char(64) CHECK (
            ocr_render_sha256 IS NULL OR ocr_render_sha256 ~ '^[0-9a-f]{64}$'
          ),
          geometry_status text NOT NULL DEFAULT 'OBSERVED'
            CHECK (geometry_status = 'OBSERVED'),
          geometry_sha256 char(64) NOT NULL
            CHECK (geometry_sha256 ~ '^[0-9a-f]{64}$'),
          page_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, document_scope_id::text,
              document_version_id::text, processing_plan_id::text,
              source_document_id::text, source_plan_sha256::text,
              source_processing_unit_id::text, source_unit_id::text,
              source_unit_ordinal::text, source_unit_kind, page_no::text,
              candidate_decision, selected_route,
              source_unit_evidence_sha256::text,
              native_text_identity_sha256::text, native_characters::text,
              source_page_output_sha256::text,
              source_page_evidence_sha256::text, geometry_sha256::text
            ])
          ) STORED,
          page_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, document_scope_id::text,
              first_run_id::text, document_version_id::text,
              source_processing_unit_id::text, source_unit_id::text,
              page_no::text, rotation::text, media_left::text,
              media_bottom::text, media_right::text, media_top::text,
              crop_left::text, crop_bottom::text, crop_right::text,
              crop_top::text, width_px::text, height_px::text,
              ocr_render_width_px::text, ocr_render_height_px::text,
              ocr_render_dpi::text, ocr_render_origin, ocr_renderer_id,
              ocr_renderer_version, ocr_render_sha256::text,
              geometry_status, geometry_sha256::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT page_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT page_unit_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            source_processing_unit_id
          ),
          CONSTRAINT page_ordinal_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            document_scope_id, page_no
          ),
          CONSTRAINT page_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no, page_identity_sha256
          ),
          CONSTRAINT page_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no
          ),
          CONSTRAINT page_routed_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no, selected_route
          ),
          CONSTRAINT page_document_scope_fk FOREIGN KEY (
            enterprise_id, document_scope_id, configuration_id,
            configuration_sha256, first_run_id, document_version_id,
            processing_plan_id, source_document_id, source_plan_sha256
          ) REFERENCES f0i.document_scope(
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256
          ),
          CONSTRAINT page_processing_unit_fk FOREIGN KEY (
            enterprise_id, source_processing_unit_id, processing_plan_id,
            source_unit_id, source_unit_ordinal, source_unit_kind, page_no,
            candidate_decision, source_unit_evidence_sha256,
            native_text_identity_sha256, native_characters
          ) REFERENCES f0d.document_processing_unit(
            enterprise_id, id, processing_plan_id, source_unit_id,
            unit_ordinal, unit_kind, page_no, candidate_decision,
            evidence_sha256, native_text_identity_sha256, native_characters
          ),
          CONSTRAINT page_route_ck CHECK (
            (candidate_decision = 'NATIVE_CANDIDATE'
              AND selected_route = 'NATIVE_REFERENCE')
            OR (candidate_decision = 'FULL_PAGE_OCR_REQUIRED'
              AND selected_route = 'LOCAL_OCR')
          ),
          CONSTRAINT page_geometry_ck CHECK (
            (
              source_unit_kind = 'PAGE'
              AND rotation IS NOT NULL
              AND media_left IS NOT NULL AND media_bottom IS NOT NULL
              AND media_right IS NOT NULL AND media_top IS NOT NULL
              AND crop_left IS NOT NULL AND crop_bottom IS NOT NULL
              AND crop_right IS NOT NULL AND crop_top IS NOT NULL
              AND media_right > media_left AND media_top > media_bottom
              AND crop_right > crop_left AND crop_top > crop_bottom
            ) OR (
              source_unit_kind = 'IMAGE'
              AND rotation IS NULL
              AND media_left IS NULL AND media_bottom IS NULL
              AND media_right IS NULL AND media_top IS NULL
              AND crop_left IS NULL AND crop_bottom IS NULL
              AND crop_right IS NULL AND crop_top IS NULL
              AND width_px IS NOT NULL AND height_px IS NOT NULL
            )
          ),
          CONSTRAINT page_ocr_render_ck CHECK (
            (
              selected_route = 'LOCAL_OCR'
              AND ocr_render_width_px IS NOT NULL
              AND ocr_render_height_px IS NOT NULL
              AND (
                (source_unit_kind = 'PAGE'
                  AND ocr_render_dpi = 250
                  AND ocr_render_origin = 'PDFIUM_250_DPI')
                OR (source_unit_kind = 'IMAGE'
                  AND ocr_render_dpi IS NULL
                  AND ocr_render_origin = 'JPEG_DECODED_SOURCE_PIXELS')
              )
              AND ocr_renderer_id IS NOT NULL
              AND ocr_renderer_version IS NOT NULL
              AND ocr_render_sha256 IS NOT NULL
            ) OR (
              selected_route = 'NATIVE_REFERENCE'
              AND ocr_render_width_px IS NULL
              AND ocr_render_height_px IS NULL
              AND ocr_render_dpi IS NULL AND ocr_render_origin IS NULL
              AND ocr_renderer_id IS NULL AND ocr_renderer_version IS NULL
              AND ocr_render_sha256 IS NULL
            )
          )
        )
        """
    )


def _create_block() -> None:
    op.execute(
        """
        CREATE TABLE f0i.block (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          document_scope_id uuid NOT NULL,
          first_run_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          source_document_type text NOT NULL
            CHECK (source_document_type IN ('PDF','DOCX','JPEG','XLSX')),
          container_id uuid NOT NULL,
          container_kind text NOT NULL CHECK (
            container_kind IN ('PAGE','DOCX_SECTION','XLSX_SHEET')
          ),
          page_id uuid,
          source_processing_unit_id uuid,
          source_unit_id char(64)
            CHECK (source_unit_id IS NULL OR source_unit_id ~ '^[0-9a-f]{64}$'),
          source_unit_ordinal integer
            CHECK (source_unit_ordinal IS NULL OR source_unit_ordinal > 0),
          page_no integer CHECK (page_no IS NULL OR page_no > 0),
          structure_unit_sha256 char(64) CHECK (
            structure_unit_sha256 IS NULL
            OR structure_unit_sha256 ~ '^[0-9a-f]{64}$'
          ),
          structure_anchor_sha256 char(64) CHECK (
            structure_anchor_sha256 IS NULL
            OR structure_anchor_sha256 ~ '^[0-9a-f]{64}$'
          ),
          block_ordinal integer NOT NULL CHECK (block_ordinal > 0),
          block_kind text NOT NULL CHECK (block_kind IN (
            'NATIVE_PAGE_TEXT','OCR_TEXT_BLOCK','OCR_EMPTY_PAGE',
            'DOCX_PARAGRAPH','DOCX_TABLE_CELL','XLSX_CELL','XLSX_SHEET',
            'CANONICAL_SEPARATOR'
          )),
          evidence_method text NOT NULL CHECK (evidence_method IN (
            'PYPDF_NATIVE','PP_OCRV6_SMALL','DOCX_XML','XLSX_CELL_XML',
            'CANONICAL_JOIN'
          )),
          source_route text NOT NULL CHECK (source_route IN (
            'NATIVE_REFERENCE','LOCAL_OCR','DOCX_XML','XLSX_CELL_XML'
          )),
          location_kind text NOT NULL CHECK (location_kind IN (
            'NATIVE_TEXT','OCR_QUADRILATERAL','OCR_EMPTY_PAGE','DOCX_PARAGRAPH',
            'DOCX_TABLE_CELL','XLSX_CELL','XLSX_SHEET',
            'SYNTHETIC_SEPARATOR'
          )),
          location_status text NOT NULL
            CHECK (location_status IN (
              'AVAILABLE','OBSERVED','UNAVAILABLE','SYNTHETIC'
            )),
          location_reason_code text CHECK (
            location_reason_code IS NULL OR location_reason_code IN (
              'NATIVE_LAYOUT_NOT_CAPTURED','OCR_EMPTY_RESULT',
              'CANONICAL_JOIN_SEPARATOR'
            )
          ),
          location_sha256 char(64) NOT NULL
            CHECK (location_sha256 ~ '^[0-9a-f]{64}$'),
          bbox_ppm jsonb,
          coordinate_space text NOT NULL CHECK (
            coordinate_space IN (
              'TOP_LEFT_PPM','UNAVAILABLE','SOURCE_STRUCTURE','SYNTHETIC'
            )
          ),
          reading_order_status text NOT NULL CHECK (
            reading_order_status IN (
              'READING_ORDER_CANDIDATE','UNAVAILABLE',
              'SOURCE_STRUCTURE_ORDER','SYNTHETIC'
            )
          ),
          confidence_ppm integer CHECK (
            confidence_ppm IS NULL OR confidence_ppm BETWEEN 0 AND 1000000
          ),
          structure_ordinal integer
            CHECK (structure_ordinal IS NULL OR structure_ordinal > 0),
          docx_block_ordinal integer
            CHECK (docx_block_ordinal IS NULL OR docx_block_ordinal > 0),
          docx_paragraph_ordinal integer
            CHECK (docx_paragraph_ordinal IS NULL OR docx_paragraph_ordinal > 0),
          docx_table_ordinal integer
            CHECK (docx_table_ordinal IS NULL OR docx_table_ordinal > 0),
          docx_row_ordinal integer
            CHECK (docx_row_ordinal IS NULL OR docx_row_ordinal > 0),
          docx_cell_ordinal integer
            CHECK (docx_cell_ordinal IS NULL OR docx_cell_ordinal > 0),
          xlsx_sheet_ordinal integer
            CHECK (xlsx_sheet_ordinal IS NULL OR xlsx_sheet_ordinal > 0),
          xlsx_row_ordinal integer
            CHECK (xlsx_row_ordinal IS NULL OR xlsx_row_ordinal > 0),
          xlsx_column_ordinal integer
            CHECK (xlsx_column_ordinal IS NULL OR xlsx_column_ordinal > 0),
          table_evidence_status text NOT NULL CHECK (
            table_evidence_status IN (
              'UNRESOLVED','NOT_APPLICABLE','OBSERVED_DOCX_XML',
              'OBSERVED_XLSX_CELL_XML'
            )
          ),
          canonical_body_plaintext_sha256 char(64) NOT NULL
            CHECK (canonical_body_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          canonical_body_plaintext_size_bytes bigint NOT NULL CHECK (
            canonical_body_plaintext_size_bytes BETWEEN 0 AND 4194304
          ),
          canonical_body_plaintext_character_count integer NOT NULL CHECK (
            canonical_body_plaintext_character_count >= 0
          ),
          body_plaintext_sha256 char(64) NOT NULL
            CHECK (body_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          body_plaintext_size_bytes bigint NOT NULL CHECK (
            body_plaintext_size_bytes BETWEEN 0 AND 4194304
          ),
          body_plaintext_character_count integer NOT NULL CHECK (
            body_plaintext_character_count >= 0
          ),
          body_ciphertext bytea NOT NULL CHECK (octet_length(body_ciphertext) >= 32),
          body_ciphertext_sha256 char(64) NOT NULL
            CHECK (body_ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          previous_source_chain_sha256 char(64) CHECK (
            previous_source_chain_sha256 IS NULL
            OR previous_source_chain_sha256 ~ '^[0-9a-f]{64}$'
          ),
          source_chain_sha256 char(64) NOT NULL
            CHECK (source_chain_sha256 ~ '^[0-9a-f]{64}$'),
          span_start_byte bigint NOT NULL CHECK (span_start_byte >= 0),
          span_end_byte bigint NOT NULL CHECK (span_end_byte >= span_start_byte),
          span_start_character integer NOT NULL CHECK (span_start_character >= 0),
          span_end_character integer NOT NULL CHECK (
            span_end_character >= span_start_character
          ),
          block_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, document_scope_id::text,
              document_version_id::text, processing_plan_id::text,
              source_document_id::text, source_plan_sha256::text,
              source_document_type, container_id::text, container_kind,
              page_id::text, source_processing_unit_id::text,
              source_unit_id::text, source_unit_ordinal::text, page_no::text,
              structure_unit_sha256::text,
              structure_anchor_sha256::text, block_ordinal::text, block_kind,
              evidence_method, source_route, location_kind, location_status,
              location_reason_code, location_sha256::text,
              canonical_body_plaintext_sha256::text,
              canonical_body_plaintext_size_bytes::text,
              canonical_body_plaintext_character_count::text,
              body_plaintext_sha256::text, body_plaintext_size_bytes::text,
              body_plaintext_character_count::text, span_start_byte::text,
              span_end_byte::text, span_start_character::text,
              span_end_character::text, previous_source_chain_sha256::text,
              source_chain_sha256::text
            ])
          ) STORED,
          block_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              document_scope_id::text, first_run_id::text,
              document_version_id::text, container_id::text,
              block_ordinal::text, block_kind, location_sha256::text,
              body_ciphertext_sha256::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT block_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT block_container_ordinal_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            document_scope_id, container_id, block_ordinal
          ),
          CONSTRAINT block_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, block_identity_sha256
          ),
          CONSTRAINT block_link_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id
          ),
          CONSTRAINT block_document_scope_fk FOREIGN KEY (
            enterprise_id, document_scope_id, configuration_id,
            configuration_sha256, first_run_id, document_version_id,
            processing_plan_id, source_document_id, source_plan_sha256,
            source_document_type
          ) REFERENCES f0i.document_scope(
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256, document_type
          ),
          CONSTRAINT block_page_fk FOREIGN KEY (
            enterprise_id, page_id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no, source_route
          ) REFERENCES f0i.page(
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no, selected_route
          ),
          CONSTRAINT block_body_ciphertext_hash_ck CHECK (
            body_ciphertext_sha256 = encode(
              f0f_crypto.digest(body_ciphertext, 'sha256'), 'hex'
            )::char(64)
          ),
          CONSTRAINT block_span_ck CHECK (
            span_end_byte - span_start_byte = body_plaintext_size_bytes
            AND span_end_character - span_start_character =
              body_plaintext_character_count
            AND span_end_byte <= canonical_body_plaintext_size_bytes
            AND span_end_character <=
              canonical_body_plaintext_character_count
          ),
          CONSTRAINT block_source_chain_ck CHECK (
            (block_ordinal = 1 AND previous_source_chain_sha256 IS NULL)
            OR (block_ordinal > 1 AND previous_source_chain_sha256 IS NOT NULL)
          ),
          CONSTRAINT block_source_xor_ck CHECK (
            (
              container_kind = 'PAGE'
              AND source_document_type IN ('PDF','JPEG')
              AND page_id IS NOT NULL AND container_id = page_id
              AND source_processing_unit_id IS NOT NULL
              AND source_unit_id IS NOT NULL
              AND source_unit_ordinal IS NOT NULL AND page_no IS NOT NULL
              AND structure_unit_sha256 IS NULL
              AND structure_anchor_sha256 IS NULL
              AND structure_ordinal IS NULL
            ) OR (
              container_kind IN ('DOCX_SECTION','XLSX_SHEET')
              AND source_document_type IN ('DOCX','XLSX')
              AND page_id IS NULL AND source_processing_unit_id IS NULL
              AND source_unit_id IS NULL AND source_unit_ordinal IS NULL
              AND page_no IS NULL AND structure_unit_sha256 IS NOT NULL
              AND structure_anchor_sha256 IS NOT NULL
              AND (
                structure_ordinal IS NOT NULL
                OR block_kind = 'CANONICAL_SEPARATOR'
              )
            )
          ),
          CONSTRAINT block_bbox_ck CHECK (
            (
              location_kind = 'OCR_QUADRILATERAL'
              AND block_kind = 'OCR_TEXT_BLOCK'
              AND evidence_method = 'PP_OCRV6_SMALL'
              AND source_route = 'LOCAL_OCR'
              AND location_status = 'AVAILABLE'
              AND location_reason_code IS NULL
              AND bbox_ppm IS NOT NULL AND f0i.valid_bbox_ppm(bbox_ppm)
              AND coordinate_space = 'TOP_LEFT_PPM'
              AND reading_order_status = 'READING_ORDER_CANDIDATE'
              AND confidence_ppm IS NOT NULL
            ) OR (
              location_kind = 'NATIVE_TEXT'
              AND block_kind = 'NATIVE_PAGE_TEXT'
              AND evidence_method = 'PYPDF_NATIVE'
              AND source_route = 'NATIVE_REFERENCE'
              AND location_status = 'UNAVAILABLE'
              AND location_reason_code = 'NATIVE_LAYOUT_NOT_CAPTURED'
              AND bbox_ppm IS NULL AND coordinate_space = 'UNAVAILABLE'
              AND reading_order_status = 'UNAVAILABLE'
              AND confidence_ppm IS NULL
            ) OR (
              location_kind = 'OCR_EMPTY_PAGE'
              AND block_kind = 'OCR_EMPTY_PAGE'
              AND evidence_method = 'PP_OCRV6_SMALL'
              AND source_route = 'LOCAL_OCR'
              AND location_status = 'UNAVAILABLE'
              AND location_reason_code = 'OCR_EMPTY_RESULT'
              AND bbox_ppm IS NULL AND coordinate_space = 'UNAVAILABLE'
              AND reading_order_status = 'UNAVAILABLE'
              AND confidence_ppm IS NULL
              AND body_plaintext_size_bytes = 0
              AND body_plaintext_character_count = 0
            ) OR (
              location_kind IN (
                'DOCX_PARAGRAPH','DOCX_TABLE_CELL','XLSX_CELL','XLSX_SHEET'
              )
              AND location_status = 'OBSERVED'
              AND location_reason_code IS NULL
              AND bbox_ppm IS NULL AND coordinate_space = 'SOURCE_STRUCTURE'
              AND reading_order_status = 'SOURCE_STRUCTURE_ORDER'
              AND confidence_ppm IS NULL
            ) OR (
              location_kind = 'SYNTHETIC_SEPARATOR'
              AND block_kind = 'CANONICAL_SEPARATOR'
              AND evidence_method = 'CANONICAL_JOIN'
              AND location_status = 'SYNTHETIC'
              AND location_reason_code = 'CANONICAL_JOIN_SEPARATOR'
              AND bbox_ppm IS NULL AND coordinate_space = 'SYNTHETIC'
              AND reading_order_status = 'SYNTHETIC'
              AND confidence_ppm IS NULL
            )
          ),
          CONSTRAINT block_structure_locator_ck CHECK (
            (
              location_kind = 'DOCX_PARAGRAPH'
              AND block_kind = 'DOCX_PARAGRAPH'
              AND evidence_method = 'DOCX_XML'
              AND source_route = 'DOCX_XML'
              AND location_reason_code IS NULL
              AND docx_block_ordinal IS NOT NULL
              AND docx_paragraph_ordinal IS NOT NULL
              AND docx_table_ordinal IS NULL AND docx_row_ordinal IS NULL
              AND docx_cell_ordinal IS NULL AND xlsx_sheet_ordinal IS NULL
              AND xlsx_row_ordinal IS NULL AND xlsx_column_ordinal IS NULL
            ) OR (
              location_kind = 'DOCX_TABLE_CELL'
              AND block_kind = 'DOCX_TABLE_CELL'
              AND evidence_method = 'DOCX_XML'
              AND source_route = 'DOCX_XML'
              AND location_reason_code IS NULL
              AND docx_block_ordinal IS NOT NULL
              AND docx_table_ordinal IS NOT NULL
              AND docx_row_ordinal IS NOT NULL
              AND docx_cell_ordinal IS NOT NULL
              AND xlsx_sheet_ordinal IS NULL AND xlsx_row_ordinal IS NULL
              AND xlsx_column_ordinal IS NULL
            ) OR (
              location_kind = 'XLSX_CELL'
              AND block_kind = 'XLSX_CELL'
              AND evidence_method = 'XLSX_CELL_XML'
              AND source_route = 'XLSX_CELL_XML'
              AND location_reason_code IS NULL
              AND xlsx_sheet_ordinal IS NOT NULL
              AND xlsx_row_ordinal IS NOT NULL
              AND xlsx_column_ordinal IS NOT NULL
              AND docx_block_ordinal IS NULL
              AND docx_paragraph_ordinal IS NULL
              AND docx_table_ordinal IS NULL AND docx_row_ordinal IS NULL
              AND docx_cell_ordinal IS NULL
            ) OR (
              location_kind = 'XLSX_SHEET'
              AND block_kind = 'XLSX_SHEET'
              AND evidence_method = 'XLSX_CELL_XML'
              AND source_route = 'XLSX_CELL_XML'
              AND location_reason_code IS NULL
              AND xlsx_sheet_ordinal IS NOT NULL
              AND xlsx_row_ordinal IS NULL AND xlsx_column_ordinal IS NULL
              AND docx_block_ordinal IS NULL
              AND docx_paragraph_ordinal IS NULL
              AND docx_table_ordinal IS NULL AND docx_row_ordinal IS NULL
              AND docx_cell_ordinal IS NULL
            ) OR (
              location_kind NOT IN (
                'DOCX_PARAGRAPH','DOCX_TABLE_CELL','XLSX_CELL','XLSX_SHEET'
              )
              AND docx_block_ordinal IS NULL
              AND docx_paragraph_ordinal IS NULL
              AND docx_table_ordinal IS NULL AND docx_row_ordinal IS NULL
              AND docx_cell_ordinal IS NULL AND xlsx_sheet_ordinal IS NULL
              AND xlsx_row_ordinal IS NULL AND xlsx_column_ordinal IS NULL
            )
          ),
          CONSTRAINT block_table_evidence_ck CHECK (
            (source_document_type = 'PDF'
              AND table_evidence_status = 'UNRESOLVED')
            OR (source_document_type = 'JPEG'
              AND table_evidence_status = 'NOT_APPLICABLE')
            OR (source_document_type = 'DOCX'
              AND (
                (block_kind = 'DOCX_TABLE_CELL'
                  AND table_evidence_status = 'OBSERVED_DOCX_XML')
                OR (block_kind <> 'DOCX_TABLE_CELL'
                  AND table_evidence_status = 'NOT_APPLICABLE')
              ))
            OR (source_document_type = 'XLSX'
              AND block_kind IN ('XLSX_CELL','XLSX_SHEET')
              AND table_evidence_status = 'OBSERVED_XLSX_CELL_XML')
            OR (source_document_type = 'XLSX'
              AND block_kind = 'CANONICAL_SEPARATOR'
              AND table_evidence_status = 'NOT_APPLICABLE')
          )
        )
        """
    )


def _create_chunk() -> None:
    op.execute(
        """
        CREATE TABLE f0i.chunk (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          document_scope_id uuid NOT NULL,
          first_run_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          source_document_id char(64) NOT NULL
            CHECK (source_document_id ~ '^[0-9a-f]{64}$'),
          source_plan_sha256 char(64) NOT NULL
            CHECK (source_plan_sha256 ~ '^[0-9a-f]{64}$'),
          container_id uuid NOT NULL,
          container_kind text NOT NULL CHECK (
            container_kind IN ('PAGE','DOCX_SECTION','XLSX_SHEET')
          ),
          page_id uuid,
          source_processing_unit_id uuid,
          source_unit_id char(64)
            CHECK (source_unit_id IS NULL OR source_unit_id ~ '^[0-9a-f]{64}$'),
          source_unit_ordinal integer
            CHECK (source_unit_ordinal IS NULL OR source_unit_ordinal > 0),
          page_no integer CHECK (page_no IS NULL OR page_no > 0),
          structure_unit_sha256 char(64) CHECK (
            structure_unit_sha256 IS NULL
            OR structure_unit_sha256 ~ '^[0-9a-f]{64}$'
          ),
          structure_anchor_sha256 char(64) CHECK (
            structure_anchor_sha256 IS NULL
            OR structure_anchor_sha256 ~ '^[0-9a-f]{64}$'
          ),
          chunk_level text NOT NULL CHECK (chunk_level IN ('PARENT','CHILD')),
          parent_chunk_id uuid,
          parent_expected_level text GENERATED ALWAYS AS (
            CASE WHEN parent_chunk_id IS NULL THEN NULL ELSE 'PARENT' END
          ) STORED,
          chunk_ordinal integer NOT NULL CHECK (chunk_ordinal >= 0),
          is_tail boolean NOT NULL,
          overlap_characters integer NOT NULL DEFAULT 0
            CHECK (overlap_characters = 0),
          canonical_body_plaintext_sha256 char(64) NOT NULL
            CHECK (canonical_body_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          canonical_body_plaintext_size_bytes bigint NOT NULL CHECK (
            canonical_body_plaintext_size_bytes BETWEEN 0 AND 4194304
          ),
          canonical_body_plaintext_character_count integer NOT NULL CHECK (
            canonical_body_plaintext_character_count >= 0
          ),
          body_plaintext_sha256 char(64) NOT NULL
            CHECK (body_plaintext_sha256 ~ '^[0-9a-f]{64}$'),
          body_plaintext_size_bytes bigint NOT NULL CHECK (
            body_plaintext_size_bytes BETWEEN 0 AND 4194304
          ),
          body_plaintext_character_count integer NOT NULL CHECK (
            body_plaintext_character_count >= 0
          ),
          body_ciphertext bytea NOT NULL CHECK (octet_length(body_ciphertext) >= 32),
          body_ciphertext_sha256 char(64) NOT NULL
            CHECK (body_ciphertext_sha256 ~ '^[0-9a-f]{64}$'),
          span_start_byte bigint NOT NULL CHECK (span_start_byte >= 0),
          span_end_byte bigint NOT NULL CHECK (span_end_byte >= span_start_byte),
          span_start_character integer NOT NULL CHECK (span_start_character >= 0),
          span_end_character integer NOT NULL CHECK (
            span_end_character >= span_start_character
          ),
          previous_source_chain_sha256 char(64) CHECK (
            previous_source_chain_sha256 IS NULL
            OR previous_source_chain_sha256 ~ '^[0-9a-f]{64}$'
          ),
          source_chain_sha256 char(64) NOT NULL
            CHECK (source_chain_sha256 ~ '^[0-9a-f]{64}$'),
          unit_chain_sha256 char(64) NOT NULL
            CHECK (unit_chain_sha256 ~ '^[0-9a-f]{64}$'),
          chunk_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, document_scope_id::text,
              document_version_id::text, processing_plan_id::text,
              source_document_id::text, source_plan_sha256::text,
              container_id::text, container_kind, page_id::text,
              source_processing_unit_id::text, source_unit_id::text,
              source_unit_ordinal::text, page_no::text,
              structure_unit_sha256::text,
              structure_anchor_sha256::text, chunk_level,
              parent_chunk_id::text, chunk_ordinal::text, is_tail::text,
              overlap_characters::text,
              canonical_body_plaintext_sha256::text,
              canonical_body_plaintext_size_bytes::text,
              canonical_body_plaintext_character_count::text,
              body_plaintext_sha256::text, body_plaintext_size_bytes::text,
              body_plaintext_character_count::text, span_start_byte::text,
              span_end_byte::text, span_start_character::text,
              span_end_character::text, previous_source_chain_sha256::text,
              source_chain_sha256::text, unit_chain_sha256::text
            ])
          ) STORED,
          chunk_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              document_scope_id::text, first_run_id::text,
              document_version_id::text, container_id::text, chunk_level,
              parent_chunk_id::text, chunk_ordinal::text,
              source_chain_sha256::text, unit_chain_sha256::text,
              body_ciphertext_sha256::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT chunk_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT chunk_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, chunk_level,
            chunk_identity_sha256
          ),
          CONSTRAINT chunk_parent_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, chunk_level,
            unit_chain_sha256
          ),
          CONSTRAINT chunk_link_provenance_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id
          ),
          CONSTRAINT chunk_document_scope_fk FOREIGN KEY (
            enterprise_id, document_scope_id, configuration_id,
            configuration_sha256, first_run_id, document_version_id,
            processing_plan_id, source_document_id, source_plan_sha256
          ) REFERENCES f0i.document_scope(
            enterprise_id, id, configuration_id, configuration_sha256,
            first_run_id, document_version_id, processing_plan_id,
            source_document_id, source_plan_sha256
          ),
          CONSTRAINT chunk_page_fk FOREIGN KEY (
            enterprise_id, page_id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no
          ) REFERENCES f0i.page(
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, source_processing_unit_id, source_unit_id,
            source_unit_ordinal, page_no
          ),
          CONSTRAINT chunk_parent_fk FOREIGN KEY (
            enterprise_id, parent_chunk_id, configuration_id,
            configuration_sha256, document_scope_id, first_run_id,
            document_version_id, processing_plan_id, container_id,
            parent_expected_level, unit_chain_sha256
          ) REFERENCES f0i.chunk(
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, chunk_level,
            unit_chain_sha256
          ) DEFERRABLE INITIALLY DEFERRED,
          CONSTRAINT chunk_body_ciphertext_hash_ck CHECK (
            body_ciphertext_sha256 = encode(
              f0f_crypto.digest(body_ciphertext, 'sha256'), 'hex'
            )::char(64)
          ),
          CONSTRAINT chunk_span_ck CHECK (
            span_end_byte - span_start_byte = body_plaintext_size_bytes
            AND span_end_character - span_start_character =
              body_plaintext_character_count
            AND span_end_byte <= canonical_body_plaintext_size_bytes
            AND span_end_character <=
              canonical_body_plaintext_character_count
          ),
          CONSTRAINT chunk_level_ck CHECK (
            (
              chunk_level = 'PARENT'
              AND parent_chunk_id IS NULL AND parent_expected_level IS NULL
              AND chunk_ordinal = 0 AND NOT is_tail
              AND span_start_byte = 0
              AND span_end_byte = canonical_body_plaintext_size_bytes
              AND span_start_character = 0
              AND span_end_character =
                canonical_body_plaintext_character_count
              AND body_plaintext_sha256 =
                canonical_body_plaintext_sha256
              AND body_plaintext_size_bytes =
                canonical_body_plaintext_size_bytes
              AND body_plaintext_character_count =
                canonical_body_plaintext_character_count
              AND previous_source_chain_sha256 IS NULL
            ) OR (
              chunk_level = 'CHILD'
              AND parent_chunk_id IS NOT NULL
              AND parent_expected_level = 'PARENT'
              AND chunk_ordinal > 0
              AND body_plaintext_character_count <= 800
              AND (
                body_plaintext_character_count >= 300 OR is_tail
              )
              AND (
                (chunk_ordinal = 1 AND previous_source_chain_sha256 IS NULL)
                OR (chunk_ordinal > 1
                  AND previous_source_chain_sha256 IS NOT NULL)
              )
            )
          ),
          CONSTRAINT chunk_source_xor_ck CHECK (
            (
              container_kind = 'PAGE'
              AND page_id IS NOT NULL AND container_id = page_id
              AND source_processing_unit_id IS NOT NULL
              AND source_unit_id IS NOT NULL
              AND source_unit_ordinal IS NOT NULL AND page_no IS NOT NULL
              AND structure_unit_sha256 IS NULL
              AND structure_anchor_sha256 IS NULL
            ) OR (
              container_kind IN ('DOCX_SECTION','XLSX_SHEET')
              AND page_id IS NULL AND source_processing_unit_id IS NULL
              AND source_unit_id IS NULL AND source_unit_ordinal IS NULL
              AND page_no IS NULL AND structure_unit_sha256 IS NOT NULL
              AND structure_anchor_sha256 IS NOT NULL
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX chunk_one_parent_per_container_uq
        ON f0i.chunk(
          enterprise_id, configuration_id, configuration_sha256,
          document_scope_id, container_id
        ) WHERE chunk_level = 'PARENT'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX chunk_child_ordinal_uq
        ON f0i.chunk(
          enterprise_id, configuration_id, configuration_sha256,
          document_scope_id, parent_chunk_id, chunk_ordinal
        ) WHERE chunk_level = 'CHILD'
        """
    )


def _create_chunk_block_link() -> None:
    op.execute(
        """
        CREATE TABLE f0i.chunk_block_link (
          id uuid NOT NULL,
          enterprise_id uuid NOT NULL,
          configuration_id uuid NOT NULL,
          configuration_sha256 char(64) NOT NULL
            CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
          document_scope_id uuid NOT NULL,
          first_run_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          processing_plan_id uuid NOT NULL,
          container_id uuid NOT NULL,
          chunk_id uuid NOT NULL,
          linked_chunk_level text NOT NULL DEFAULT 'CHILD'
            CHECK (linked_chunk_level = 'CHILD'),
          block_id uuid NOT NULL,
          link_ordinal integer NOT NULL CHECK (link_ordinal > 0),
          intersection_start_byte bigint NOT NULL
            CHECK (intersection_start_byte >= 0),
          intersection_end_byte bigint NOT NULL CHECK (
            intersection_end_byte >= intersection_start_byte
          ),
          intersection_start_character integer NOT NULL
            CHECK (intersection_start_character >= 0),
          intersection_end_character integer NOT NULL CHECK (
            intersection_end_character >= intersection_start_character
          ),
          unit_chain_sha256 char(64) NOT NULL
            CHECK (unit_chain_sha256 ~ '^[0-9a-f]{64}$'),
          link_identity_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              configuration_sha256::text, document_scope_id::text,
              document_version_id::text, processing_plan_id::text,
              container_id::text, chunk_id::text, linked_chunk_level,
              block_id::text, link_ordinal::text,
              intersection_start_byte::text,
              intersection_end_byte::text,
              intersection_start_character::text,
              intersection_end_character::text, unit_chain_sha256::text
            ])
          ) STORED,
          link_chain_sha256 char(64) GENERATED ALWAYS AS (
            f0e.sha256_chain(ARRAY[
              enterprise_id::text, configuration_id::text,
              document_scope_id::text, first_run_id::text,
              document_version_id::text, container_id::text,
              chunk_id::text, block_id::text, link_ordinal::text,
              intersection_start_byte::text,
              intersection_end_byte::text,
              intersection_start_character::text,
              intersection_end_character::text, unit_chain_sha256::text
            ])
          ) STORED,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          PRIMARY KEY (id),
          CONSTRAINT chunk_block_link_scope_uq UNIQUE (enterprise_id, id),
          CONSTRAINT chunk_block_link_ordinal_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            document_scope_id, chunk_id, link_ordinal
          ),
          CONSTRAINT chunk_block_link_pair_uq UNIQUE (
            enterprise_id, configuration_id, configuration_sha256,
            document_scope_id, chunk_id, block_id
          ),
          CONSTRAINT chunk_block_link_identity_uq UNIQUE (
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, chunk_id, block_id,
            link_identity_sha256
          ),
          CONSTRAINT chunk_block_link_chunk_fk FOREIGN KEY (
            enterprise_id, chunk_id, configuration_id,
            configuration_sha256, document_scope_id, first_run_id,
            document_version_id, processing_plan_id, container_id,
            linked_chunk_level, unit_chain_sha256
          ) REFERENCES f0i.chunk(
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id, chunk_level,
            unit_chain_sha256
          ),
          CONSTRAINT chunk_block_link_block_fk FOREIGN KEY (
            enterprise_id, block_id, configuration_id,
            configuration_sha256, document_scope_id, first_run_id,
            document_version_id, processing_plan_id, container_id
          ) REFERENCES f0i.block(
            enterprise_id, id, configuration_id, configuration_sha256,
            document_scope_id, first_run_id, document_version_id,
            processing_plan_id, container_id
          )
        )
        """
    )
    # The foreign keys bind the link to one child and one block in the same
    # tenant/version/container.  This trigger additionally proves that the
    # claimed intersection is the exact observed overlap of their UTF-8 spans;
    # the json/body columns are never read here.
    op.execute(
        """
        CREATE FUNCTION f0i.validate_chunk_block_link()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_start_byte bigint;
          v_end_byte bigint;
          v_start_character integer;
          v_end_character integer;
          v_chunk_bytes bigint;
          v_chunk_characters integer;
          v_block_bytes bigint;
          v_block_characters integer;
        BEGIN
          SELECT greatest(chunk.span_start_byte, block.span_start_byte),
                 least(chunk.span_end_byte, block.span_end_byte),
                 greatest(
                   chunk.span_start_character, block.span_start_character
                 ),
                 least(chunk.span_end_character, block.span_end_character),
                 chunk.body_plaintext_size_bytes,
                 chunk.body_plaintext_character_count,
                 block.body_plaintext_size_bytes,
                 block.body_plaintext_character_count
          INTO v_start_byte, v_end_byte, v_start_character, v_end_character,
               v_chunk_bytes, v_chunk_characters,
               v_block_bytes, v_block_characters
          FROM f0i.chunk AS chunk
          JOIN f0i.block AS block
            ON block.enterprise_id = chunk.enterprise_id
           AND block.configuration_id = chunk.configuration_id
           AND block.configuration_sha256 = chunk.configuration_sha256
           AND block.document_scope_id = chunk.document_scope_id
           AND block.first_run_id = chunk.first_run_id
           AND block.document_version_id = chunk.document_version_id
           AND block.processing_plan_id = chunk.processing_plan_id
           AND block.container_id = chunk.container_id
          WHERE chunk.enterprise_id = NEW.enterprise_id
            AND chunk.id = NEW.chunk_id
            AND block.id = NEW.block_id
            AND chunk.chunk_level = 'CHILD'
            AND chunk.unit_chain_sha256 = NEW.unit_chain_sha256;
          IF NOT FOUND
             OR v_end_byte < v_start_byte
             OR v_end_character < v_start_character
             OR NOT (
               (
                 v_end_byte > v_start_byte
                 AND v_end_character > v_start_character
               ) OR (
                 v_end_byte = v_start_byte
                 AND v_end_character = v_start_character
                 AND v_chunk_bytes = 0 AND v_chunk_characters = 0
                 AND v_block_bytes = 0 AND v_block_characters = 0
               )
             )
             OR NEW.intersection_start_byte <> v_start_byte
             OR NEW.intersection_end_byte <> v_end_byte
             OR NEW.intersection_start_character <> v_start_character
             OR NEW.intersection_end_character <> v_end_character THEN
            RAISE EXCEPTION 'F0I_LINK_INTERSECTION_INVALID'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f0i.validate_chunk_block_link() FROM PUBLIC"
    )
    op.execute(
        "CREATE TRIGGER validate_chunk_block_link_insert "
        "BEFORE INSERT ON f0i.chunk_block_link FOR EACH ROW "
        "EXECUTE FUNCTION f0i.validate_chunk_block_link()"
    )


def _make_append_only_and_tenant_scoped() -> None:
    for table in _TABLES:
        op.execute(
            f"CREATE TRIGGER reject_immutable_row_mutation "
            f"BEFORE UPDATE OR DELETE ON f0i.{table} FOR EACH ROW "
            "EXECUTE FUNCTION f0d.reject_immutable_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER reject_immutable_truncate BEFORE TRUNCATE "
            f"ON f0i.{table} FOR EACH STATEMENT EXECUTE FUNCTION "
            "f0d.reject_immutable_mutation()"
        )
        op.execute(f"ALTER TABLE f0i.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f0i.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_boundary ON f0i.{table}
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
            CREATE POLICY migration_f0i_read ON f0i.{table}
            FOR SELECT TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY migration_f0i_insert ON f0i.{table}
            FOR INSERT TO f0d_migration
            WITH CHECK (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )
        # Mutation probes must see the row so the immutable trigger rejects
        # instead of converting an attempted attack into a misleading 0 rows.
        op.execute(
            f"""
            CREATE POLICY migration_f0i_update_probe ON f0i.{table}
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
            CREATE POLICY migration_f0i_delete_probe ON f0i.{table}
            FOR DELETE TO f0d_migration
            USING (
              enterprise_id = f0d.current_enterprise_id()
              AND f0d.context_session_authorized(enterprise_id)
            )
            """
        )


def _lock_down_privileges() -> None:
    op.execute(
        "REVOKE ALL ON SCHEMA f0i FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    op.execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA f0i "
        "FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    op.execute(
        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA f0i "
        "FROM PUBLIC, f0d_runtime, f0d_worker"
    )
    # Table ACL revocation does not clear a pre-existing column ACL.  Revoke
    # each column explicitly, including the ciphertext-bearing columns.
    op.execute(
        r"""
        DO $$
        DECLARE
          v_table text;
          v_columns text;
        BEGIN
          FOREACH v_table IN ARRAY ARRAY[
            'configuration','run','document_scope','page','block','chunk',
            'chunk_block_link'
          ]
          LOOP
            SELECT string_agg(quote_ident(attribute.attname), ', '
                              ORDER BY attribute.attnum)
            INTO v_columns
            FROM pg_catalog.pg_attribute AS attribute
            JOIN pg_catalog.pg_class AS relation
              ON relation.oid = attribute.attrelid
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'f0i'
              AND relation.relname = v_table
              AND attribute.attnum > 0
              AND NOT attribute.attisdropped;
            IF v_columns IS NULL THEN
              RAISE EXCEPTION 'F0I_PRIVILEGE_TARGET_MISSING';
            END IF;
            EXECUTE format(
              'REVOKE ALL PRIVILEGES (%s) ON TABLE %I.%I '
              'FROM PUBLIC, f0d_runtime, f0d_worker',
              v_columns, 'f0i', v_table
            );
          END LOOP;
        END
        $$
        """
    )


def downgrade() -> None:
    # F0-I is a fresh, isolated acceptance schema.  No upstream F0-A--H
    # relation is altered by this revision, so a deliberate downgrade can
    # remove only this schema and restore the exact f0d_0005 surface.
    op.execute("DROP SCHEMA f0i CASCADE")
    op.execute(
        "ALTER TABLE f0d.fixture_source_registry "
        "DROP CONSTRAINT fixture_source_f0i_provenance_uq"
    )
