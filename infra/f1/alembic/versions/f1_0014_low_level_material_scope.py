"""Carry material knowledge scope into low-level P3 source objects."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0014"
down_revision: str | None = "f1_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _column_and_backfill()
    _guards()
    _restrict_api_rows()


def _column_and_backfill() -> None:
    op.execute("ALTER TABLE f1.document ADD COLUMN knowledge_scope_id uuid")
    # document/document_version/upload_task already use FORCE RLS. The outer
    # connection is the validated bootstrap session, so bypass it only for
    # this deterministic record -> version -> source-document backfill.
    op.execute("RESET ROLE")
    op.execute(
        """
        DO $low_level_scope_backfill$
        BEGIN
          IF EXISTS (
            SELECT version.enterprise_id, version.source_document_id
            FROM f1.document_version AS version
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
            GROUP BY version.enterprise_id, version.source_document_id
            HAVING count(DISTINCT record.knowledge_scope_id) <> 1
          ) THEN
            RAISE EXCEPTION 'P3_SOURCE_DOCUMENT_SCOPE_AMBIGUOUS';
          END IF;
        END
        $low_level_scope_backfill$
        """
    )
    op.execute(
        """
        UPDATE f1.document AS source
        SET knowledge_scope_id = mapped.knowledge_scope_id
        FROM (
          SELECT DISTINCT
            version.enterprise_id,
            version.source_document_id,
            record.knowledge_scope_id
          FROM f1.document_version AS version
          JOIN f1.document_record AS record
            ON record.enterprise_id = version.enterprise_id
           AND record.id = version.document_record_id
        ) AS mapped
        WHERE source.enterprise_id = mapped.enterprise_id
          AND source.id = mapped.source_document_id
        """
    )
    op.execute(
        """
        DO $low_level_scope_complete$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM f1.upload_task AS task
            JOIN f1.document AS source
              ON source.enterprise_id = task.enterprise_id
             AND source.id = task.document_id
            WHERE task.pipeline_kind = 'controlled_ingestion'
              AND source.knowledge_scope_id IS NULL
          ) THEN
            RAISE EXCEPTION 'P3_CONTROLLED_DOCUMENT_SCOPE_BACKFILL_INCOMPLETE';
          END IF;
        END
        $low_level_scope_complete$
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")
    op.execute(
        """
        ALTER TABLE f1.document
          ADD CONSTRAINT document_knowledge_scope_enterprise_fk
          FOREIGN KEY (enterprise_id, knowledge_scope_id)
          REFERENCES f1.material_knowledge_scope(enterprise_id, id)
        """
    )


def _guards() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p3_guard_controlled_upload_scope()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.pipeline_kind = 'controlled_ingestion' THEN
            PERFORM 1
            FROM f1.document AS source
            WHERE source.enterprise_id = NEW.enterprise_id
              AND source.id = NEW.document_id
              AND source.knowledge_scope_id IS NOT NULL;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'P3_CONTROLLED_DOCUMENT_SCOPE_REQUIRED';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER upload_task_controlled_scope_guard "
        "BEFORE INSERT OR UPDATE OF enterprise_id,document_id,pipeline_kind "
        "ON f1.upload_task FOR EACH ROW "
        "EXECUTE FUNCTION f1.p3_guard_controlled_upload_scope()"
    )
    op.execute(
        """
        CREATE FUNCTION f1.p3_guard_source_document_scope_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.knowledge_scope_id IS DISTINCT FROM OLD.knowledge_scope_id
             AND EXISTS (
               SELECT 1
               FROM f1.upload_task AS task
               WHERE task.enterprise_id = OLD.enterprise_id
                 AND task.document_id = OLD.id
                 AND task.pipeline_kind = 'controlled_ingestion'
             )
          THEN
            RAISE EXCEPTION 'P3_SOURCE_DOCUMENT_SCOPE_IMMUTABLE';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_knowledge_scope_update_guard "
        "BEFORE UPDATE OF knowledge_scope_id ON f1.document FOR EACH ROW "
        "EXECUTE FUNCTION f1.p3_guard_source_document_scope_update()"
    )
    for signature in (
        "f1.p3_guard_controlled_upload_scope()",
        "f1.p3_guard_source_document_scope_update()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def _actor_clause(enterprise: str, scope_kind: str, client_id: str) -> str:
    return f"""
      EXISTS (
        SELECT 1
        FROM f1.enterprise_user AS scope_actor
        JOIN f1.user_profile AS scope_profile
          ON scope_profile.id = scope_actor.user_id
        WHERE scope_actor.enterprise_id = {enterprise}
          AND scope_profile.keycloak_sub = f1.current_sub()
          AND scope_actor.role IN (
            'super_admin','enterprise_admin','plant_admin'
          )
          AND (
            {scope_kind} = 'service_provider'
            OR scope_actor.role IN ('super_admin','enterprise_admin')
            OR (
              scope_actor.role = 'plant_admin'
              AND EXISTS (
                SELECT 1
                FROM f1.crm_account AS owned_account
                WHERE owned_account.enterprise_id = {enterprise}
                  AND owned_account.id = {client_id}
                  AND owned_account.owner_user_id = scope_actor.user_id
              )
            )
          )
      )
    """


def _document_scope_access(alias: str) -> str:
    actor = _actor_clause(
        "visible_scope.enterprise_id",
        "visible_scope.scope_kind",
        "visible_scope.client_account_id",
    )
    return f"""
      {alias}.knowledge_scope_id IS NULL
      OR EXISTS (
        SELECT 1
        FROM f1.material_knowledge_scope AS visible_scope
        WHERE visible_scope.enterprise_id = {alias}.enterprise_id
          AND visible_scope.id = {alias}.knowledge_scope_id
          AND {actor}
      )
    """


def _controlled_task_scope_access(alias: str) -> str:
    actor = _actor_clause(
        "visible_scope.enterprise_id",
        "visible_scope.scope_kind",
        "visible_scope.client_account_id",
    )
    return f"""
      {alias}.pipeline_kind <> 'controlled_ingestion'
      OR EXISTS (
        SELECT 1
        FROM f1.document AS visible_document
        JOIN f1.material_knowledge_scope AS visible_scope
          ON visible_scope.enterprise_id = visible_document.enterprise_id
         AND visible_scope.id = visible_document.knowledge_scope_id
        WHERE visible_document.enterprise_id = {alias}.enterprise_id
          AND visible_document.id = {alias}.document_id
          AND {actor}
      )
    """


def _restrict_api_rows() -> None:
    document_access = _document_scope_access("document")
    op.execute(
        "CREATE POLICY p3_low_level_document_scope ON f1.document "
        "AS RESTRICTIVE FOR ALL TO f1_api USING ("
        + document_access
        + ") WITH CHECK ("
        + document_access
        + ")"
    )
    task_access = _controlled_task_scope_access("upload_task")
    op.execute(
        "CREATE POLICY p3_low_level_upload_scope ON f1.upload_task "
        "AS RESTRICTIVE FOR ALL TO f1_api USING ("
        + task_access
        + ") WITH CHECK ("
        + task_access
        + ")"
    )


def downgrade() -> None:
    op.execute("DROP POLICY p3_low_level_upload_scope ON f1.upload_task")
    op.execute("DROP POLICY p3_low_level_document_scope ON f1.document")
    op.execute(
        "DROP TRIGGER document_knowledge_scope_update_guard ON f1.document"
    )
    op.execute("DROP FUNCTION f1.p3_guard_source_document_scope_update()")
    op.execute(
        "DROP TRIGGER upload_task_controlled_scope_guard ON f1.upload_task"
    )
    op.execute("DROP FUNCTION f1.p3_guard_controlled_upload_scope()")
    op.execute(
        "ALTER TABLE f1.document DROP CONSTRAINT "
        "document_knowledge_scope_enterprise_fk"
    )
    op.execute("ALTER TABLE f1.document DROP COLUMN knowledge_scope_id")
