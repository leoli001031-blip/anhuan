"""Accept the page-layout-aware cloud OCR checkpoint contract.

Old rows remain for compatibility, but application readers reject v1 and
recompute them.  A database migration never relabels old text as newly parsed.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "f1_0028"
down_revision: str | None = "f1_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # DEFAULT 1 is deliberate: existing or old-runtime inserts have not
    # proved this extraction contract. New code explicitly writes 2.
    op.execute(
        "ALTER TABLE f1.material_analysis ADD COLUMN extraction_contract "
        "integer NOT NULL DEFAULT 1 CHECK (extraction_contract IN (1,2))"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.material_guard_analysis_revision_insert()
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
          SELECT * INTO predecessor FROM f1.material_analysis AS analysis
          WHERE analysis.enterprise_id = NEW.enterprise_id
            AND analysis.id = NEW.supersedes_analysis_id FOR UPDATE;
          IF NOT FOUND
             OR predecessor.document_version_id <> NEW.document_version_id
             OR predecessor.source_sha256 <> NEW.source_sha256
             OR predecessor.analysis_version <> NEW.analysis_version
             OR predecessor.parser_backend <> NEW.parser_backend
             OR predecessor.analysis_revision <> NEW.analysis_revision - 1
             OR NEW.extraction_contract < predecessor.extraction_contract
             OR (
               predecessor.status <> 'failed'
               AND NOT (
                 predecessor.status = 'ready'
                 AND (
                   predecessor.extraction_contract < NEW.extraction_contract
                   OR EXISTS (
                     SELECT 1 FROM f1.material_page_classification AS page
                     WHERE page.enterprise_id = predecessor.enterprise_id
                       AND page.analysis_id = predecessor.id
                       AND page.ocr_required IS TRUE
                   )
                 )
               )
             )
          THEN
            RAISE EXCEPTION 'MATERIAL_ANALYSIS_SUPERSESSION_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.material_guard_analysis_revision_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.analysis_revision IS DISTINCT FROM OLD.analysis_revision
             OR NEW.supersedes_analysis_id IS DISTINCT FROM OLD.supersedes_analysis_id
             OR NEW.extraction_contract IS DISTINCT FROM OLD.extraction_contract
          THEN
            RAISE EXCEPTION 'MATERIAL_ANALYSIS_REVISION_IMMUTABLE';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "DROP CONSTRAINT material_ocr_checkpoint_parser_backend_check"
    )
    op.execute(
        "ALTER TABLE f1.material_ocr_checkpoint "
        "ADD CONSTRAINT material_ocr_checkpoint_parser_backend_check CHECK ("
        "parser_backend IN ("
        "'f0h-ppocrv6-3.9.2','cloud-vision-chat-1','cloud-vision-chat-2'))"
    )


def downgrade() -> None:
    raise RuntimeError("MATERIAL_OCR_LAYOUT_CONTRACT_RESTORE_REQUIRED")
