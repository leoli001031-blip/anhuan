"""Report soft archive: management attribute columns and guard.

Soft archive is an internal management attribute, not a publication state.
Archiving a published report requires explicit withdrawal first.  Recovery
does not auto-publish.  Versions, citations, and audit trails are preserved.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0025"
down_revision: str | None = "f1_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_at timestamptz"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_by_user_id uuid"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD COLUMN IF NOT EXISTS archived_reason text"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD CONSTRAINT analysis_report_archived_actor_fk "
        "FOREIGN KEY (enterprise_id, archived_by_user_id) "
        "REFERENCES f1.enterprise_user (enterprise_id, user_id)"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "ADD CONSTRAINT analysis_report_archived_reason_ck "
        "CHECK (archived_reason IS NULL OR char_length(archived_reason) BETWEEN 1 AND 500)"
    )
    # f1_0023 revoked table-level sensitive DML and granted only specific
    # columns to f1_api.  Soft-archive adds three more management columns;
    # grant UPDATE on exactly those columns, not the whole table.
    op.execute(
        "GRANT UPDATE (archived_at, archived_by_user_id, archived_reason) "
        "ON f1.analysis_report TO f1_api"
    )
    # The f1_0023 guard fall-through requires a published version for any
    # update that doesn't change version/visibility — but archive targets
    # exactly the non-published reports.  Add an archive-column branch
    # before that fall-through that allows archive/unarchive when no
    # version is in an active or published state.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.guard_analysis_report_write()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_actor_id uuid; v_visible boolean;
        BEGIN
          SELECT membership.user_id INTO v_actor_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND membership.role IN ('super_admin','enterprise_admin');
          IF v_actor_id IS NULL THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_WRITE_ACTOR_INVALID';
          END IF;

          IF TG_OP='INSERT' THEN
            IF NEW.created_by_user_id IS DISTINCT FROM v_actor_id
               OR NEW.current_version_id IS NOT NULL
               OR NEW.current_version_no<>0 OR NEW.client_visible IS TRUE
               OR NEW.template_id<>'enterprise-ehs-material-analysis-v1'
               OR NEW.title<>'企业安环资料分析报告' THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_INSERT_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
             OR NEW.client_account_id IS DISTINCT FROM OLD.client_account_id
             OR NEW.template_id IS DISTINCT FROM OLD.template_id
             OR NEW.title IS DISTINCT FROM OLD.title
             OR NEW.create_request_id IS DISTINCT FROM OLD.create_request_id
             OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.updated_at IS DISTINCT FROM statement_timestamp() THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_IDENTITY_IMMUTABLE';
          END IF;

          IF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN
            IF NEW.current_version_no<>OLD.current_version_no+1
               OR NEW.client_visible IS DISTINCT FROM OLD.client_visible
               OR NOT EXISTS (
                 SELECT 1 FROM f1.analysis_report_version AS version
                 JOIN f1.analysis_report_generation_job AS job
                   ON job.enterprise_id=version.enterprise_id
                  AND job.report_id=version.report_id
                  AND job.version_id=version.id
                 WHERE version.enterprise_id=NEW.enterprise_id
                   AND version.report_id=NEW.id
                   AND version.id=NEW.current_version_id
                   AND version.version_number=NEW.current_version_no
                   AND version.status='queued' AND job.status='queued'
               ) THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_CURRENT_VERSION_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.current_version_no IS DISTINCT FROM OLD.current_version_no THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_CURRENT_VERSION_INVALID';
          END IF;
          IF NEW.client_visible IS DISTINCT FROM OLD.client_visible THEN
            SELECT EXISTS (
              SELECT 1 FROM f1.analysis_report_version AS version
              WHERE version.enterprise_id=NEW.enterprise_id
                AND version.report_id=NEW.id
                AND version.status='published'
                AND version.artifact_ready IS TRUE
            ) INTO v_visible;
            IF NEW.client_visible IS DISTINCT FROM v_visible THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_VISIBILITY_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          -- Soft-archive branch: only archive columns changed
          IF NEW.archived_at IS DISTINCT FROM OLD.archived_at
             OR NEW.archived_by_user_id IS DISTINCT FROM OLD.archived_by_user_id
             OR NEW.archived_reason IS DISTINCT FROM OLD.archived_reason THEN
            IF NEW.archived_by_user_id IS NOT NULL
               AND NEW.archived_by_user_id <> v_actor_id THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_ACTOR_INVALID';
            END IF;
            IF EXISTS (
              SELECT 1 FROM f1.analysis_report_version AS version
              WHERE version.enterprise_id=NEW.enterprise_id
                AND version.report_id=NEW.id
                AND version.status IN
                ('generating','review_pending','approved','published')
            ) THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_ACTIVE';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.current_version_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM f1.analysis_report_version AS version
            WHERE version.enterprise_id=NEW.enterprise_id
              AND version.id=NEW.current_version_id
              AND version.report_id=NEW.id AND version.status='published'
          ) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_UPDATE_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP CONSTRAINT IF EXISTS analysis_report_archived_reason_ck"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP CONSTRAINT IF EXISTS analysis_report_archived_actor_fk"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_reason"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_by_user_id"
    )
    op.execute(
        "ALTER TABLE f1.analysis_report "
        "DROP COLUMN IF EXISTS archived_at"
    )
