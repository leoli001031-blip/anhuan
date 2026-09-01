"""Encrypted OCR page checkpoints and immutable material-analysis revisions."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0022"
down_revision: str | None = "f1_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _analysis_revisions()
    _ocr_checkpoints()
    _ocr_checkpoint_guards()
    _ocr_checkpoint_rls()


def _analysis_revisions() -> None:
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD COLUMN analysis_revision integer NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD COLUMN supersedes_analysis_id uuid"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_revision_ck CHECK ("
        "analysis_revision BETWEEN 1 AND 100)"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_supersession_shape_ck CHECK ("
        "(analysis_revision=1 AND supersedes_analysis_id IS NULL) OR "
        "(analysis_revision>1 AND supersedes_analysis_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_supersedes_enterprise_fk "
        "FOREIGN KEY (enterprise_id,supersedes_analysis_id) "
        "REFERENCES f1.material_analysis(enterprise_id,id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_version_uq"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_revision_uq UNIQUE ("
        "enterprise_id,document_version_id,analysis_version,analysis_revision)"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_supersedes_uq UNIQUE ("
        "enterprise_id,supersedes_analysis_id)"
    )
    op.execute(
        "CREATE INDEX material_analysis_current_idx ON f1.material_analysis("
        "enterprise_id,document_version_id,analysis_version,"
        "analysis_revision DESC,id DESC)"
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_analysis_revision_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE predecessor f1.material_analysis%ROWTYPE;
        BEGIN
          IF NEW.analysis_revision = 1 THEN
            IF NEW.supersedes_analysis_id IS NOT NULL THEN
              RAISE EXCEPTION 'MATERIAL_ANALYSIS_REVISION_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          SELECT * INTO predecessor
          FROM f1.material_analysis AS analysis
          WHERE analysis.enterprise_id = NEW.enterprise_id
            AND analysis.id = NEW.supersedes_analysis_id
          FOR UPDATE;
          IF NOT FOUND
             OR predecessor.document_version_id <> NEW.document_version_id
             OR predecessor.source_sha256 <> NEW.source_sha256
             OR predecessor.analysis_version <> NEW.analysis_version
             OR predecessor.parser_backend <> NEW.parser_backend
             OR predecessor.analysis_revision <> NEW.analysis_revision - 1
             OR predecessor.status <> 'failed'
          THEN
            RAISE EXCEPTION 'MATERIAL_ANALYSIS_SUPERSESSION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_analysis_revision_insert_guard "
        "BEFORE INSERT ON f1.material_analysis FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_analysis_revision_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_analysis_revision_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.analysis_revision IS DISTINCT FROM OLD.analysis_revision
             OR NEW.supersedes_analysis_id IS DISTINCT FROM
                OLD.supersedes_analysis_id
          THEN
            RAISE EXCEPTION 'MATERIAL_ANALYSIS_REVISION_IMMUTABLE';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_analysis_revision_update_guard "
        "BEFORE UPDATE ON f1.material_analysis FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_analysis_revision_update()"
    )
    for function in (
        "f1.material_guard_analysis_revision_insert()",
        "f1.material_guard_analysis_revision_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO f1_api")


def _ocr_checkpoints() -> None:
    op.execute(
        """
        CREATE TABLE f1.material_ocr_checkpoint (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_version_id uuid NOT NULL,
          source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
          expected_page_count integer NOT NULL CHECK (
            expected_page_count BETWEEN 1 AND 128
          ),
          page_number integer NOT NULL CHECK (
            page_number BETWEEN 1 AND expected_page_count
          ),
          parser_backend text NOT NULL CHECK (
            parser_backend = 'f0h-ppocrv6-3.9.2'
          ),
          source_unit_id text NOT NULL CHECK (
            source_unit_id ~ '^[0-9a-f]{64}$'
          ),
          body_ciphertext bytea NOT NULL CHECK (
            octet_length(body_ciphertext) BETWEEN 34 AND 400033
          ),
          body_sha256 text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
          body_aad_sha256 text NOT NULL CHECK (
            body_aad_sha256 ~ '^[0-9a-f]{64}$'
          ),
          character_count integer NOT NULL CHECK (
            character_count BETWEEN 40 AND 100000
          ),
          confidence_mean_ppm integer CHECK (
            confidence_mean_ppm IS NULL OR
            confidence_mean_ppm BETWEEN 0 AND 1000000
          ),
          table_candidate boolean NOT NULL DEFAULT false,
          two_column_candidate boolean NOT NULL DEFAULT false,
          completed_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          expires_at timestamptz NOT NULL,
          CONSTRAINT material_ocr_checkpoint_enterprise_id_id_uq
            UNIQUE (enterprise_id,id),
          CONSTRAINT material_ocr_checkpoint_identity_uq UNIQUE (
            enterprise_id,document_version_id,source_sha256,page_number,
            parser_backend
          ),
          CONSTRAINT material_ocr_checkpoint_version_enterprise_fk
            FOREIGN KEY (enterprise_id,document_version_id)
            REFERENCES f1.document_version(enterprise_id,id) ON DELETE RESTRICT,
          CONSTRAINT material_ocr_checkpoint_ttl_ck CHECK (
            expires_at > completed_at AND
            expires_at <= completed_at + interval '24 hours'
          )
        )
        """
    )
    op.execute(
        "CREATE INDEX material_ocr_checkpoint_expiry_idx "
        "ON f1.material_ocr_checkpoint(enterprise_id,expires_at,"
        "document_version_id)"
    )


def _ocr_checkpoint_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_ocr_checkpoint_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          preview_pages integer;
          stored_bytes bigint;
        BEGIN
          SELECT task.preview_unit_count INTO preview_pages
          FROM f1.document_version AS version
          JOIN f1.upload_task AS task
            ON task.enterprise_id = version.enterprise_id
           AND task.id = version.upload_task_id
          JOIN f1.document AS source
            ON source.enterprise_id = version.enterprise_id
           AND source.id = version.source_document_id
          WHERE version.enterprise_id = NEW.enterprise_id
            AND version.id = NEW.document_version_id
            AND source.content_type = 'application/pdf'
            AND task.pipeline_kind = 'controlled_ingestion'
            AND task.content_sha256 = NEW.source_sha256
            AND task.status = 'done'
            AND task.processing_stage = 'ready'
            AND task.object_state = 'ready'
            AND task.scan_verdict = 'clean'
            AND task.preview_status = 'ready'
          FOR UPDATE OF task;
          IF NOT FOUND OR preview_pages IS NULL
             OR preview_pages <> NEW.expected_page_count
          THEN
            RAISE EXCEPTION 'MATERIAL_OCR_CHECKPOINT_SOURCE_INVALID';
          END IF;
          SELECT COALESCE(sum(octet_length(checkpoint.body_ciphertext)),0)
            INTO stored_bytes
          FROM f1.material_ocr_checkpoint AS checkpoint
          WHERE checkpoint.enterprise_id = NEW.enterprise_id
            AND checkpoint.document_version_id = NEW.document_version_id
            AND checkpoint.source_sha256 = NEW.source_sha256
            AND checkpoint.expected_page_count = NEW.expected_page_count
            AND checkpoint.parser_backend = NEW.parser_backend
            AND checkpoint.expires_at > statement_timestamp();
          IF stored_bytes + octet_length(NEW.body_ciphertext) > 33554432 THEN
            RAISE EXCEPTION 'MATERIAL_OCR_CHECKPOINT_SIZE_LIMIT';
          END IF;
          NEW.completed_at := statement_timestamp();
          NEW.expires_at := NEW.completed_at + interval '24 hours';
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_ocr_checkpoint_insert_guard "
        "BEFORE INSERT ON f1.material_ocr_checkpoint FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_ocr_checkpoint_insert()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f1.material_guard_ocr_checkpoint_insert() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.material_guard_ocr_checkpoint_insert() TO f1_api"
    )


def _ocr_checkpoint_scope(alias: str) -> str:
    return f"""
      {alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND EXISTS (
        SELECT 1
        FROM f1.document_version AS visible_version
        JOIN f1.document_record AS visible_record
          ON visible_record.enterprise_id = visible_version.enterprise_id
         AND visible_record.id = visible_version.document_record_id
        JOIN f1.material_knowledge_scope AS visible_scope
          ON visible_scope.enterprise_id = visible_record.enterprise_id
         AND visible_scope.id = visible_record.knowledge_scope_id
        JOIN f1.enterprise_user AS actor
          ON actor.enterprise_id = visible_scope.enterprise_id
        JOIN f1.user_profile AS profile ON profile.id = actor.user_id
        WHERE visible_version.enterprise_id = {alias}.enterprise_id
          AND visible_version.id = {alias}.document_version_id
          AND profile.keycloak_sub = f1.current_sub()
          AND actor.role IN ('super_admin','enterprise_admin','plant_admin')
          AND (
            visible_scope.scope_kind = 'service_provider'
            OR actor.role IN ('super_admin','enterprise_admin')
            OR (
              actor.role = 'plant_admin'
              AND EXISTS (
                SELECT 1 FROM f1.crm_account AS owned_account
                WHERE owned_account.enterprise_id = visible_scope.enterprise_id
                  AND owned_account.id = visible_scope.client_account_id
                  AND owned_account.owner_user_id = actor.user_id
              )
            )
          )
      )
    """


def _ocr_checkpoint_rls() -> None:
    op.execute("ALTER TABLE f1.material_ocr_checkpoint ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.material_ocr_checkpoint FORCE ROW LEVEL SECURITY")
    access = _ocr_checkpoint_scope("material_ocr_checkpoint")
    op.execute(
        "CREATE POLICY material_ocr_checkpoint_select "
        "ON f1.material_ocr_checkpoint FOR SELECT TO f1_api USING ("
        + access
        + ")"
    )
    op.execute(
        "CREATE POLICY material_ocr_checkpoint_insert "
        "ON f1.material_ocr_checkpoint FOR INSERT TO f1_api WITH CHECK ("
        + access
        + ")"
    )
    op.execute(
        "CREATE POLICY material_ocr_checkpoint_delete "
        "ON f1.material_ocr_checkpoint FOR DELETE TO f1_api USING ("
        + access
        + ")"
    )
    op.execute(
        "GRANT SELECT,INSERT,DELETE ON f1.material_ocr_checkpoint TO f1_api"
    )
    op.execute(
        "REVOKE UPDATE ON f1.material_ocr_checkpoint FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.material_ocr_checkpoint FROM PUBLIC,f1_worker"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $material_analysis_revision_downgrade$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM f1.material_analysis
            WHERE analysis_revision <> 1 OR supersedes_analysis_id IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'MATERIAL_ANALYSIS_REVISION_DOWNGRADE_REQUIRES_NO_SUCCESSORS';
          END IF;
        END
        $material_analysis_revision_downgrade$
        """
    )
    op.execute("DROP TABLE f1.material_ocr_checkpoint")
    op.execute("DROP FUNCTION f1.material_guard_ocr_checkpoint_insert()")
    op.execute(
        "DROP TRIGGER material_analysis_revision_update_guard "
        "ON f1.material_analysis"
    )
    op.execute(
        "DROP TRIGGER material_analysis_revision_insert_guard "
        "ON f1.material_analysis"
    )
    op.execute("DROP FUNCTION f1.material_guard_analysis_revision_update()")
    op.execute("DROP FUNCTION f1.material_guard_analysis_revision_insert()")
    op.execute("DROP INDEX f1.material_analysis_current_idx")
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_supersedes_uq"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_revision_uq"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "ADD CONSTRAINT material_analysis_version_uq UNIQUE ("
        "enterprise_id,document_version_id,analysis_version)"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_supersedes_enterprise_fk"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_supersession_shape_ck"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP CONSTRAINT material_analysis_revision_ck"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis "
        "DROP COLUMN supersedes_analysis_id, DROP COLUMN analysis_revision"
    )
