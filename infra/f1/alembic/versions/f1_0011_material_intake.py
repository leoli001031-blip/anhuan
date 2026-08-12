"""P3 material analysis and human-confirmed P5 draft intake."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0011"
down_revision: str | None = "f1_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _tables()
    _guards()
    _row_level_security()
    _grants()


def _tables() -> None:
    op.execute(
        """
        CREATE TABLE f1.material_analysis (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_version_id uuid NOT NULL,
          source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
          analysis_version text NOT NULL DEFAULT 'material-v1'
            CHECK (analysis_version = 'material-v1'),
          parser_backend text NOT NULL DEFAULT 'pypdf_heuristic'
            CHECK (parser_backend = 'pypdf_heuristic'),
          status text NOT NULL CHECK (status IN ('ready','failed','confirmed')),
          document_profile text NOT NULL CHECK (document_profile IN (
            'text','scanned','mixed','table','two_column','unknown'
          )),
          shadow_status text NOT NULL DEFAULT 'disabled' CHECK (
            shadow_status IN ('disabled','unavailable','ready','failed')
          ),
          reason_code text CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z0-9_]{1,80}$'
          ),
          page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 128),
          candidate_count integer NOT NULL DEFAULT 0 CHECK (
            candidate_count BETWEEN 0 AND 100
          ),
          confirmed_by_user_id uuid,
          confirmed_at timestamptz,
          policy_source_id uuid,
          policy_version_id uuid,
          confirmation_key_sha256 text CHECK (
            confirmation_key_sha256 IS NULL
            OR confirmation_key_sha256 ~ '^[0-9a-f]{64}$'
          ),
          confirmation_payload_sha256 text CHECK (
            confirmation_payload_sha256 IS NULL
            OR confirmation_payload_sha256 ~ '^[0-9a-f]{64}$'
          ),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_analysis_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_analysis_version_uq
            UNIQUE (enterprise_id, document_version_id, analysis_version),
          CONSTRAINT material_analysis_document_enterprise_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id),
          CONSTRAINT material_analysis_confirmer_enterprise_fk
            FOREIGN KEY (enterprise_id, confirmed_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          CONSTRAINT material_analysis_source_enterprise_fk
            FOREIGN KEY (enterprise_id, policy_source_id)
            REFERENCES f1.policy_source(enterprise_id, id),
          CONSTRAINT material_analysis_policy_version_enterprise_fk
            FOREIGN KEY (enterprise_id, policy_version_id)
            REFERENCES f1.policy_version(enterprise_id, id),
          CONSTRAINT material_analysis_outcome_ck CHECK (
            (status = 'ready' AND reason_code IS NULL
             AND confirmed_by_user_id IS NULL AND confirmed_at IS NULL
             AND policy_source_id IS NULL AND policy_version_id IS NULL
             AND confirmation_key_sha256 IS NULL
             AND confirmation_payload_sha256 IS NULL)
            OR (status = 'failed' AND reason_code IS NOT NULL
                AND confirmed_by_user_id IS NULL AND confirmed_at IS NULL
                AND policy_source_id IS NULL AND policy_version_id IS NULL
                AND confirmation_key_sha256 IS NULL
                AND confirmation_payload_sha256 IS NULL)
            OR (status = 'confirmed' AND reason_code IS NULL
                AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL
                AND policy_source_id IS NOT NULL AND policy_version_id IS NOT NULL
                AND confirmation_key_sha256 IS NOT NULL
                AND confirmation_payload_sha256 IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.material_page_classification (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          analysis_id uuid NOT NULL,
          page_number integer NOT NULL CHECK (page_number BETWEEN 1 AND 128),
          primary_kind text NOT NULL CHECK (
            primary_kind IN ('text','scanned','mixed','unknown')
          ),
          ocr_required boolean NOT NULL,
          table_candidate boolean NOT NULL DEFAULT false,
          two_column_candidate boolean NOT NULL DEFAULT false,
          text_character_count integer NOT NULL CHECK (
            text_character_count BETWEEN 0 AND 100000
          ),
          text_confidence_ppm integer NOT NULL CHECK (
            text_confidence_ppm BETWEEN 0 AND 1000000
          ),
          scan_confidence_ppm integer NOT NULL CHECK (
            scan_confidence_ppm BETWEEN 0 AND 1000000
          ),
          table_confidence_ppm integer NOT NULL CHECK (
            table_confidence_ppm BETWEEN 0 AND 1000000
          ),
          two_column_confidence_ppm integer NOT NULL CHECK (
            two_column_confidence_ppm BETWEEN 0 AND 1000000
          ),
          reason_codes jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (
            jsonb_typeof(reason_codes) = 'array'
            AND octet_length(reason_codes::text) <= 2048
          ),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_page_enterprise_id_id_uq UNIQUE (enterprise_id, id),
          CONSTRAINT material_page_number_uq
            UNIQUE (enterprise_id, analysis_id, page_number),
          CONSTRAINT material_page_analysis_enterprise_fk
            FOREIGN KEY (enterprise_id, analysis_id)
            REFERENCES f1.material_analysis(enterprise_id, id)
            ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE f1.material_field_candidate (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          analysis_id uuid NOT NULL,
          field_name text NOT NULL CHECK (field_name IN (
            'source_title','publisher','source_type','jurisdiction',
            'source_reference','version_title','domain','effect_status',
            'issued_on','effective_from','effective_to','summary',
            'report_title','report_date','report_summary'
          )),
          candidate_value text NOT NULL CHECK (
            char_length(candidate_value) BETWEEN 1 AND 4000
          ),
          page_number integer NOT NULL CHECK (page_number BETWEEN 1 AND 128),
          evidence_snippet text NOT NULL CHECK (
            char_length(evidence_snippet) BETWEEN 1 AND 300
          ),
          confidence_ppm integer NOT NULL CHECK (
            confidence_ppm BETWEEN 0 AND 1000000
          ),
          confidence_basis text NOT NULL CHECK (
            confidence_basis ~ '^[a-z0-9_.-]{1,80}$'
          ),
          calibrated boolean NOT NULL DEFAULT false CHECK (calibrated IS FALSE),
          producer text NOT NULL CHECK (
            producer IN ('pypdf_heuristic','pdf_inspector_shadow')
          ),
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_candidate_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_candidate_page_enterprise_fk
            FOREIGN KEY (enterprise_id, analysis_id, page_number)
            REFERENCES f1.material_page_classification(
              enterprise_id, analysis_id, page_number
            ) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX material_analysis_document_idx ON f1.material_analysis "
        "(enterprise_id, document_version_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX material_candidate_analysis_idx "
        "ON f1.material_field_candidate(enterprise_id, analysis_id, field_name)"
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_analysis_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid;
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.document_version_id <> OLD.document_version_id
             OR NEW.source_sha256 <> OLD.source_sha256
             OR NEW.analysis_version <> OLD.analysis_version
             OR NEW.parser_backend <> OLD.parser_backend
             OR NEW.document_profile <> OLD.document_profile
             OR NEW.shadow_status <> OLD.shadow_status
             OR NEW.page_count <> OLD.page_count
             OR NEW.candidate_count <> OLD.candidate_count
             OR NEW.created_at <> OLD.created_at
          THEN RAISE EXCEPTION 'MATERIAL_ANALYSIS_IDENTITY_IMMUTABLE'; END IF;
          IF OLD.status <> 'ready' OR NEW.status <> 'confirmed'
          THEN RAISE EXCEPTION 'MATERIAL_ANALYSIS_TRANSITION_INVALID'; END IF;
          SELECT membership.user_id INTO actor_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id = membership.user_id
          WHERE membership.enterprise_id = NEW.enterprise_id
            AND profile.keycloak_sub = f1.current_sub()
            AND membership.role IN ('super_admin','enterprise_admin');
          IF actor_id IS NULL
             OR NEW.confirmed_by_user_id IS DISTINCT FROM actor_id
          THEN RAISE EXCEPTION 'MATERIAL_CONFIRMING_ACTOR_INVALID'; END IF;
          PERFORM 1
          FROM f1.policy_source AS source
          JOIN f1.policy_version AS policy_version
            ON policy_version.enterprise_id = source.enterprise_id
           AND policy_version.source_id = source.id
          JOIN f1.document_version AS document_version
            ON document_version.enterprise_id = policy_version.enterprise_id
           AND document_version.id = policy_version.document_version_id
          JOIN f1.upload_task AS task
            ON task.enterprise_id = document_version.enterprise_id
           AND task.id = document_version.upload_task_id
          WHERE source.enterprise_id = NEW.enterprise_id
            AND source.id = NEW.policy_source_id
            AND source.status = 'active'
            AND source.created_by_user_id = actor_id
            AND policy_version.id = NEW.policy_version_id
            AND policy_version.workflow_status = 'draft'
            AND policy_version.created_by_user_id = actor_id
            AND policy_version.document_version_id = NEW.document_version_id
            AND policy_version.document_sha256 = NEW.source_sha256
            AND task.pipeline_kind = 'controlled_ingestion'
            AND task.content_sha256 = NEW.source_sha256
            AND task.status = 'done'
            AND task.processing_stage = 'ready'
            AND task.object_state = 'ready'
            AND task.quarantine_status = 'released'
            AND task.scan_verdict = 'clean'
            AND task.preview_status = 'ready'
            AND task.released_at IS NOT NULL;
          IF NOT FOUND
          THEN RAISE EXCEPTION 'MATERIAL_POLICY_OUTPUT_INVALID'; END IF;
          NEW.confirmed_at := statement_timestamp();
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_analysis_update_guard "
        "BEFORE UPDATE ON f1.material_analysis FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_analysis_update()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.material_guard_analysis_update() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.material_guard_analysis_update() TO f1_api"
    )


def _actor_roles(table: str, roles: str) -> str:
    return f"""
      EXISTS (
        SELECT 1 FROM f1.enterprise_user AS actor
        JOIN f1.user_profile AS profile ON profile.id = actor.user_id
        WHERE actor.enterprise_id = {table}.enterprise_id
          AND profile.keycloak_sub = f1.current_sub()
          AND actor.role IN ({roles})
      )
    """


def _row_level_security() -> None:
    tables = (
        "material_analysis",
        "material_page_classification",
        "material_field_candidate",
    )
    for table in tables:
        op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")
        viewer = _actor_roles(
            table, "'super_admin','enterprise_admin','plant_admin'"
        )
        op.execute(
            f"CREATE POLICY material_{table}_select ON f1.{table} "
            "FOR SELECT TO f1_api USING ("
            "enterprise_id = f1.current_enterprise_id() AND " + viewer + ")"
        )
        writer = _actor_roles(
            table, "'super_admin','enterprise_admin','plant_admin'"
        )
        if table == "material_analysis":
            writer += """
              AND status IN ('ready','failed')
              AND parser_backend = 'pypdf_heuristic'
              AND shadow_status = 'disabled'
              AND EXISTS (
                SELECT 1 FROM f1.document_version AS version
                JOIN f1.upload_task AS task
                  ON task.enterprise_id = version.enterprise_id
                 AND task.id = version.upload_task_id
                WHERE version.enterprise_id = material_analysis.enterprise_id
                  AND version.id = material_analysis.document_version_id
                  AND task.pipeline_kind = 'controlled_ingestion'
                  AND task.content_sha256 = material_analysis.source_sha256
                  AND task.status = 'done'
                  AND task.processing_stage = 'ready'
                  AND task.object_state = 'ready'
                  AND task.scan_verdict = 'clean'
                  AND task.preview_status = 'ready'
                  AND task.quarantine_status IN ('held','released')
              )
            """
        elif table == "material_page_classification":
            writer += """
              AND EXISTS (
                SELECT 1 FROM f1.material_analysis AS analysis
                WHERE analysis.enterprise_id = material_page_classification.enterprise_id
                  AND analysis.id = material_page_classification.analysis_id
                  AND analysis.status = 'ready'
                  AND analysis.parser_backend = 'pypdf_heuristic'
                  AND analysis.shadow_status = 'disabled'
                  AND material_page_classification.page_number <= analysis.page_count
              )
            """
        else:
            writer += """
              AND producer = 'pypdf_heuristic'
              AND calibrated IS FALSE
              AND EXISTS (
                SELECT 1 FROM f1.material_analysis AS analysis
                WHERE analysis.enterprise_id = material_field_candidate.enterprise_id
                  AND analysis.id = material_field_candidate.analysis_id
                  AND analysis.status = 'ready'
                  AND analysis.parser_backend = 'pypdf_heuristic'
                  AND analysis.shadow_status = 'disabled'
                  AND material_field_candidate.page_number <= analysis.page_count
              )
            """
        op.execute(
            f"CREATE POLICY material_{table}_insert ON f1.{table} "
            "FOR INSERT TO f1_api WITH CHECK ("
            "enterprise_id = f1.current_enterprise_id() AND " + writer + ")"
        )
    confirmer = _actor_roles(
        "material_analysis", "'super_admin','enterprise_admin'"
    )
    op.execute(
        "CREATE POLICY material_analysis_confirm ON f1.material_analysis "
        "FOR UPDATE TO f1_api USING ("
        "enterprise_id = f1.current_enterprise_id() AND " + confirmer + ") "
        "WITH CHECK (enterprise_id = f1.current_enterprise_id() AND "
        + confirmer + ")"
    )


def _grants() -> None:
    op.execute(
        "GRANT SELECT, INSERT ON f1.material_analysis, "
        "f1.material_page_classification, f1.material_field_candidate TO f1_api"
    )
    op.execute("GRANT UPDATE ON f1.material_analysis TO f1_api")
    op.execute(
        "REVOKE UPDATE ON f1.material_page_classification, "
        "f1.material_field_candidate FROM f1_api"
    )
    op.execute(
        "REVOKE DELETE ON f1.material_analysis, f1.material_page_classification, "
        "f1.material_field_candidate FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.material_analysis, f1.material_page_classification, "
        "f1.material_field_candidate FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    for table in (
        "material_analysis",
        "material_page_classification",
        "material_field_candidate",
    ):
        op.execute(f"ALTER TABLE f1.{table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $material_downgrade$
        BEGIN
          IF EXISTS (SELECT 1 FROM f1.material_analysis LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.material_page_classification LIMIT 1)
             OR EXISTS (SELECT 1 FROM f1.material_field_candidate LIMIT 1)
          THEN RAISE EXCEPTION 'MATERIAL_DOWNGRADE_REQUIRES_EMPTY_SCOPE'; END IF;
        END
        $material_downgrade$
        """
    )
    op.execute("DROP TABLE f1.material_field_candidate")
    op.execute("DROP TABLE f1.material_page_classification")
    op.execute("DROP TABLE f1.material_analysis")
    op.execute("DROP FUNCTION f1.material_guard_analysis_update()")
