"""P3 declared and reviewed material routing."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0012"
down_revision: str | None = "f1_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _columns_and_backfill()
    _constraints()
    _guards()
    _update_policy()


def _columns_and_backfill() -> None:
    op.execute(
        "ALTER TABLE f1.document_record ADD COLUMN declared_material_kind text "
        "NOT NULL DEFAULT 'unknown'"
    )
    op.execute(
        """
        ALTER TABLE f1.material_analysis
          ADD COLUMN suggested_kind text,
          ADD COLUMN suggested_kind_confidence_ppm integer,
          ADD COLUMN resolved_kind text,
          ADD COLUMN classification_source text,
          ADD COLUMN classification_by_user_id uuid,
          ADD COLUMN classification_at timestamptz
        """
    )
    # A historical confirmed analysis is already authoritative evidence that the
    # user created a policy draft.  Preserve that meaning instead of relabelling
    # it as an unreviewed unknown; all other historical rows remain pending.
    op.execute(
        """
        UPDATE f1.material_analysis
        SET suggested_kind = 'unknown',
            suggested_kind_confidence_ppm = 0,
            resolved_kind = CASE WHEN status = 'confirmed'
                                 THEN 'policy' ELSE 'unknown' END,
            classification_source = CASE WHEN status = 'confirmed'
                                         THEN 'human_review'
                                         ELSE 'machine_pending' END,
            classification_by_user_id = CASE WHEN status = 'confirmed'
                                             THEN confirmed_by_user_id END,
            classification_at = CASE WHEN status = 'confirmed'
                                     THEN confirmed_at END
        """
    )
    op.execute(
        """
        ALTER TABLE f1.material_analysis
          ALTER COLUMN suggested_kind SET NOT NULL,
          ALTER COLUMN suggested_kind SET DEFAULT 'unknown',
          ALTER COLUMN suggested_kind_confidence_ppm SET NOT NULL,
          ALTER COLUMN suggested_kind_confidence_ppm SET DEFAULT 0,
          ALTER COLUMN resolved_kind SET NOT NULL,
          ALTER COLUMN resolved_kind SET DEFAULT 'unknown',
          ALTER COLUMN classification_source SET NOT NULL,
          ALTER COLUMN classification_source SET DEFAULT 'machine_pending'
        """
    )


def _constraints() -> None:
    op.execute(
        "ALTER TABLE f1.document_record ADD CONSTRAINT "
        "document_record_declared_material_kind_ck CHECK "
        "(declared_material_kind IN ('policy','report','unknown'))"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis ADD CONSTRAINT "
        "material_analysis_suggested_kind_ck CHECK "
        "(suggested_kind IN ('policy','report','unknown'))"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis ADD CONSTRAINT "
        "material_analysis_suggested_confidence_ck CHECK "
        "(suggested_kind_confidence_ppm BETWEEN 0 AND 1000000)"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis ADD CONSTRAINT "
        "material_analysis_resolved_kind_ck CHECK "
        "(resolved_kind IN ('policy','report','unknown'))"
    )
    op.execute(
        "ALTER TABLE f1.material_analysis ADD CONSTRAINT "
        "material_analysis_classification_source_ck CHECK "
        "(classification_source IN "
        "('upload_selection','machine_pending','human_review'))"
    )
    op.execute(
        """
        ALTER TABLE f1.material_analysis ADD CONSTRAINT
          material_analysis_classification_state_ck CHECK (
            (classification_source = 'machine_pending'
             AND resolved_kind = 'unknown'
             AND classification_by_user_id IS NULL
             AND classification_at IS NULL)
            OR
            (classification_source IN ('upload_selection','human_review')
             AND classification_by_user_id IS NOT NULL
             AND classification_at IS NOT NULL)
          )
        """
    )
    op.execute(
        """
        ALTER TABLE f1.material_analysis ADD CONSTRAINT
          material_analysis_classifier_enterprise_fk
          FOREIGN KEY (enterprise_id, classification_by_user_id)
          REFERENCES f1.enterprise_user(enterprise_id, user_id)
        """
    )


def _guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.p3_guard_document_record_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
             OR NEW.declared_material_kind <> OLD.declared_material_kind THEN
            RAISE EXCEPTION 'P3_DOCUMENT_RECORD_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.latest_version_no < OLD.latest_version_no THEN
            RAISE EXCEPTION 'P3_DOCUMENT_VERSION_ROLLBACK';
          END IF;
          IF OLD.status = 'archived' AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'P3_DOCUMENT_ARCHIVE_FINAL';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_analysis_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE
          declared_kind text;
          declaring_actor uuid;
        BEGIN
          SELECT record.declared_material_kind, record.created_by_user_id
            INTO declared_kind, declaring_actor
          FROM f1.document_version AS version
          JOIN f1.document_record AS record
            ON record.enterprise_id = version.enterprise_id
           AND record.id = version.document_record_id
          WHERE version.enterprise_id = NEW.enterprise_id
            AND version.id = NEW.document_version_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'MATERIAL_DOCUMENT_RECORD_INVALID';
          END IF;
          IF declared_kind = 'unknown' THEN
            NEW.resolved_kind := 'unknown';
            NEW.classification_source := 'machine_pending';
            NEW.classification_by_user_id := NULL;
            NEW.classification_at := NULL;
          ELSE
            NEW.resolved_kind := declared_kind;
            NEW.classification_source := 'upload_selection';
            NEW.classification_by_user_id := declaring_actor;
            NEW.classification_at := statement_timestamp();
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_analysis_insert_guard "
        "BEFORE INSERT ON f1.material_analysis FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_analysis_insert()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.material_guard_analysis_insert() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.material_guard_analysis_insert() TO f1_api"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.material_guard_analysis_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid;
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
             OR NEW.document_version_id IS DISTINCT FROM OLD.document_version_id
             OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
             OR NEW.analysis_version IS DISTINCT FROM OLD.analysis_version
             OR NEW.parser_backend IS DISTINCT FROM OLD.parser_backend
             OR NEW.document_profile IS DISTINCT FROM OLD.document_profile
             OR NEW.shadow_status IS DISTINCT FROM OLD.shadow_status
             OR NEW.page_count IS DISTINCT FROM OLD.page_count
             OR NEW.candidate_count IS DISTINCT FROM OLD.candidate_count
             OR NEW.suggested_kind IS DISTINCT FROM OLD.suggested_kind
             OR NEW.suggested_kind_confidence_ppm IS DISTINCT FROM
                OLD.suggested_kind_confidence_ppm
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
          THEN RAISE EXCEPTION 'MATERIAL_ANALYSIS_IDENTITY_IMMUTABLE'; END IF;

          IF OLD.status = 'ready' AND NEW.status = 'ready' THEN
            IF NEW.reason_code IS DISTINCT FROM OLD.reason_code
               OR NEW.confirmed_by_user_id IS DISTINCT FROM OLD.confirmed_by_user_id
               OR NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at
               OR NEW.policy_source_id IS DISTINCT FROM OLD.policy_source_id
               OR NEW.policy_version_id IS DISTINCT FROM OLD.policy_version_id
               OR NEW.confirmation_key_sha256 IS DISTINCT FROM
                  OLD.confirmation_key_sha256
               OR NEW.confirmation_payload_sha256 IS DISTINCT FROM
                  OLD.confirmation_payload_sha256
            THEN RAISE EXCEPTION 'MATERIAL_CLASSIFICATION_UPDATE_INVALID'; END IF;
            IF NEW.classification_source <> 'human_review' THEN
              RAISE EXCEPTION 'MATERIAL_CLASSIFICATION_SOURCE_INVALID';
            END IF;
            SELECT membership.user_id INTO actor_id
            FROM f1.enterprise_user AS membership
            JOIN f1.user_profile AS profile ON profile.id = membership.user_id
            WHERE membership.enterprise_id = NEW.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND membership.role IN (
                'super_admin','enterprise_admin','plant_admin'
              );
            IF actor_id IS NULL THEN
              RAISE EXCEPTION 'MATERIAL_CLASSIFYING_ACTOR_INVALID';
            END IF;
            NEW.classification_source := 'human_review';
            NEW.classification_by_user_id := actor_id;
            NEW.classification_at := statement_timestamp();
            NEW.updated_at := statement_timestamp();
            RETURN NEW;
          END IF;

          IF OLD.status <> 'ready' OR NEW.status <> 'confirmed' THEN
            RAISE EXCEPTION 'MATERIAL_ANALYSIS_TRANSITION_INVALID';
          END IF;
          IF NEW.resolved_kind IS DISTINCT FROM OLD.resolved_kind
             OR NEW.classification_source IS DISTINCT FROM OLD.classification_source
             OR NEW.classification_by_user_id IS DISTINCT FROM
                OLD.classification_by_user_id
             OR NEW.classification_at IS DISTINCT FROM OLD.classification_at
             OR OLD.resolved_kind <> 'policy'
             OR OLD.classification_source = 'machine_pending'
          THEN RAISE EXCEPTION 'MATERIAL_POLICY_CLASSIFICATION_INVALID'; END IF;
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


def _update_policy() -> None:
    op.execute("DROP POLICY material_analysis_confirm ON f1.material_analysis")
    op.execute(
        """
        CREATE POLICY material_analysis_confirm ON f1.material_analysis
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = material_analysis.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN (
                'super_admin','enterprise_admin','plant_admin'
              )
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = material_analysis.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN (
                'super_admin','enterprise_admin','plant_admin'
              )
          )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $material_routing_downgrade$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM f1.document_record
            WHERE declared_material_kind <> 'unknown'
          ) OR EXISTS (
            SELECT 1 FROM f1.material_analysis
            WHERE suggested_kind <> 'unknown'
               OR suggested_kind_confidence_ppm <> 0
               OR resolved_kind <> 'unknown'
               OR classification_source <> 'machine_pending'
               OR classification_by_user_id IS NOT NULL
               OR classification_at IS NOT NULL
          ) THEN
            RAISE EXCEPTION
              'MATERIAL_ROUTING_DOWNGRADE_REQUIRES_DEFAULT_CLASSIFICATION';
          END IF;
        END
        $material_routing_downgrade$
        """
    )
    op.execute("DROP POLICY material_analysis_confirm ON f1.material_analysis")
    op.execute(
        """
        CREATE POLICY material_analysis_confirm ON f1.material_analysis
        FOR UPDATE TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = material_analysis.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        WITH CHECK (
          enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS actor
            JOIN f1.user_profile AS profile ON profile.id = actor.user_id
            WHERE actor.enterprise_id = material_analysis.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND actor.role IN ('super_admin','enterprise_admin')
          )
        )
        """
    )
    op.execute("DROP TRIGGER material_analysis_insert_guard ON f1.material_analysis")
    op.execute("DROP FUNCTION f1.material_guard_analysis_insert()")
    _restore_f1_0011_guards()
    op.execute(
        "ALTER TABLE f1.material_analysis DROP CONSTRAINT "
        "material_analysis_classifier_enterprise_fk"
    )
    for constraint in (
        "material_analysis_classification_state_ck",
        "material_analysis_classification_source_ck",
        "material_analysis_resolved_kind_ck",
        "material_analysis_suggested_confidence_ck",
        "material_analysis_suggested_kind_ck",
    ):
        op.execute(
            f"ALTER TABLE f1.material_analysis DROP CONSTRAINT {constraint}"
        )
    op.execute(
        """
        ALTER TABLE f1.material_analysis
          DROP COLUMN classification_at,
          DROP COLUMN classification_by_user_id,
          DROP COLUMN classification_source,
          DROP COLUMN resolved_kind,
          DROP COLUMN suggested_kind_confidence_ppm,
          DROP COLUMN suggested_kind
        """
    )
    op.execute(
        "ALTER TABLE f1.document_record DROP CONSTRAINT "
        "document_record_declared_material_kind_ck"
    )
    op.execute(
        "ALTER TABLE f1.document_record DROP COLUMN declared_material_kind"
    )


def _restore_f1_0011_guards() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.p3_guard_document_record_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.enterprise_id <> OLD.enterprise_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION 'P3_DOCUMENT_RECORD_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.latest_version_no < OLD.latest_version_no THEN
            RAISE EXCEPTION 'P3_DOCUMENT_VERSION_ROLLBACK';
          END IF;
          IF OLD.status = 'archived' AND NEW.status <> OLD.status THEN
            RAISE EXCEPTION 'P3_DOCUMENT_ARCHIVE_FINAL';
          END IF;
          NEW.updated_at := statement_timestamp();
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.material_guard_analysis_update()
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
