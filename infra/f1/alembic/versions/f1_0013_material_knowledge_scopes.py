"""Persist service-provider and client material knowledge scopes."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0013"
down_revision: str | None = "f1_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _table_and_backfill()
    _document_guard()
    _policy_confirmation_guard()
    _replace_scope_aware_policies()
    _grants()


def _table_and_backfill() -> None:
    op.execute(
        """
        CREATE TABLE f1.material_knowledge_scope (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          scope_kind text NOT NULL,
          client_account_id uuid,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT material_knowledge_scope_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_knowledge_scope_client_enterprise_fk
            FOREIGN KEY (enterprise_id, client_account_id)
            REFERENCES f1.crm_account(enterprise_id, id),
          CONSTRAINT material_knowledge_scope_kind_ck CHECK (
            scope_kind IN ('service_provider','client')
          ),
          CONSTRAINT material_knowledge_scope_target_ck CHECK (
            (scope_kind = 'service_provider' AND client_account_id IS NULL)
            OR (scope_kind = 'client' AND client_account_id IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX material_knowledge_scope_provider_uq "
        "ON f1.material_knowledge_scope(enterprise_id) "
        "WHERE scope_kind='service_provider'"
    )
    op.execute(
        "CREATE UNIQUE INDEX material_knowledge_scope_client_uq "
        "ON f1.material_knowledge_scope(enterprise_id,client_account_id) "
        "WHERE scope_kind='client'"
    )
    # Never infer ownership from content or filenames. Existing workspaces get
    # one stable provider scope and every existing document is linked to it.
    # The runtime tables use FORCE RLS and intentionally expose no bootstrap
    # migration view.  The outer migration connection is the validated
    # f0d_bootstrap session, so temporarily reset only for this bounded
    # backfill, then restore f0d_migration before continuing with DDL.
    op.execute("RESET ROLE")
    op.execute(
        """
        INSERT INTO f1.material_knowledge_scope (
          id, enterprise_id, scope_kind, client_account_id
        )
        SELECT gen_random_uuid(), enterprise.id, 'service_provider', NULL
        FROM f1.enterprise AS enterprise
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        ALTER TABLE f1.document_record
          ADD COLUMN knowledge_scope_id uuid,
          ADD COLUMN scope_selection_source text,
          ADD COLUMN scope_selected_by_user_id uuid,
          ADD COLUMN scope_selected_at timestamptz
        """
    )
    op.execute(
        """
        UPDATE f1.document_record AS record
        SET knowledge_scope_id = scope.id,
            scope_selection_source = 'migration_backfill',
            scope_selected_by_user_id = record.created_by_user_id,
            scope_selected_at = record.created_at
        FROM f1.material_knowledge_scope AS scope
        WHERE scope.enterprise_id = record.enterprise_id
          AND scope.scope_kind = 'service_provider'
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")
    op.execute(
        """
        ALTER TABLE f1.document_record
          ALTER COLUMN knowledge_scope_id SET NOT NULL,
          ALTER COLUMN scope_selection_source SET NOT NULL,
          ALTER COLUMN scope_selected_by_user_id SET NOT NULL,
          ALTER COLUMN scope_selected_at SET NOT NULL,
          ADD CONSTRAINT document_record_knowledge_scope_enterprise_fk
            FOREIGN KEY (enterprise_id, knowledge_scope_id)
            REFERENCES f1.material_knowledge_scope(enterprise_id, id),
          ADD CONSTRAINT document_record_scope_actor_enterprise_fk
            FOREIGN KEY (enterprise_id, scope_selected_by_user_id)
            REFERENCES f1.enterprise_user(enterprise_id, user_id),
          ADD CONSTRAINT document_record_scope_source_ck CHECK (
            scope_selection_source IN (
              'migration_backfill','upload_selection','human_review'
            )
          )
        """
    )
    op.execute(
        "CREATE INDEX document_record_knowledge_scope_idx "
        "ON f1.document_record(enterprise_id,knowledge_scope_id,updated_at DESC)"
    )


def _document_guard() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.p3_guard_document_record_update()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE actor_id uuid;
        BEGIN
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
             OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.declared_material_kind IS DISTINCT FROM
                OLD.declared_material_kind THEN
            RAISE EXCEPTION 'P3_DOCUMENT_RECORD_IDENTITY_IMMUTABLE';
          END IF;
          IF NEW.knowledge_scope_id IS DISTINCT FROM OLD.knowledge_scope_id
             OR NEW.scope_selection_source IS DISTINCT FROM
                OLD.scope_selection_source
             OR NEW.scope_selected_by_user_id IS DISTINCT FROM
                OLD.scope_selected_by_user_id
             OR NEW.scope_selected_at IS DISTINCT FROM OLD.scope_selected_at
          THEN
            IF EXISTS (
              SELECT 1
              FROM f1.document_version AS version
              JOIN f1.upload_task AS task
                ON task.enterprise_id = version.enterprise_id
               AND task.id = version.upload_task_id
              WHERE version.enterprise_id = OLD.enterprise_id
                AND version.document_record_id = OLD.id
                AND (task.quarantine_status = 'released'
                     OR task.released_at IS NOT NULL)
            ) THEN
              RAISE EXCEPTION 'P3_KNOWLEDGE_SCOPE_LOCKED';
            END IF;
            SELECT membership.user_id INTO actor_id
            FROM f1.enterprise_user AS membership
            JOIN f1.user_profile AS profile ON profile.id = membership.user_id
            WHERE membership.enterprise_id = OLD.enterprise_id
              AND profile.keycloak_sub = f1.current_sub()
              AND membership.role IN (
                'super_admin','enterprise_admin','plant_admin'
              );
            IF actor_id IS NULL
               OR NEW.scope_selection_source <> 'human_review'
               OR NEW.scope_selected_by_user_id IS DISTINCT FROM actor_id
            THEN
              RAISE EXCEPTION 'P3_KNOWLEDGE_SCOPE_ACTOR_INVALID';
            END IF;
            NEW.scope_selected_at := statement_timestamp();
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


def _policy_confirmation_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.p5_guard_policy_document_scope()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.document_version_id IS NOT NULL THEN
            PERFORM 1
            FROM f1.document_version AS version
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
            JOIN f1.material_knowledge_scope AS scope
              ON scope.enterprise_id = record.enterprise_id
             AND scope.id = record.knowledge_scope_id
            WHERE version.enterprise_id = NEW.enterprise_id
              AND version.id = NEW.document_version_id
              AND scope.scope_kind = 'service_provider';
            IF NOT FOUND THEN
              RAISE EXCEPTION 'P5_POLICY_DOCUMENT_SCOPE_INVALID';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER policy_version_document_scope_guard "
        "BEFORE INSERT ON f1.policy_version FOR EACH ROW "
        "EXECUTE FUNCTION f1.p5_guard_policy_document_scope()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.p5_guard_policy_document_scope() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.p5_guard_policy_document_scope() TO f1_api"
    )
    op.execute(
        """
        CREATE FUNCTION f1.material_guard_policy_scope()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF OLD.status = 'ready' AND NEW.status = 'confirmed' THEN
            PERFORM 1
            FROM f1.document_version AS version
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
            JOIN f1.material_knowledge_scope AS scope
              ON scope.enterprise_id = record.enterprise_id
             AND scope.id = record.knowledge_scope_id
            WHERE version.enterprise_id = NEW.enterprise_id
              AND version.id = NEW.document_version_id
              AND scope.scope_kind = 'service_provider';
            IF NOT FOUND THEN
              RAISE EXCEPTION 'MATERIAL_POLICY_SCOPE_INVALID';
            END IF;
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER material_analysis_policy_scope_guard "
        "BEFORE UPDATE ON f1.material_analysis FOR EACH ROW "
        "EXECUTE FUNCTION f1.material_guard_policy_scope()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.material_guard_policy_scope() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.material_guard_policy_scope() TO f1_api"
    )


def _actor_clause(enterprise: str, scope_kind: str, client_id: str) -> str:
    """Explicit access rule; callers never rely on nested RLS as authorization."""
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
                SELECT 1 FROM f1.crm_account AS owned_account
                WHERE owned_account.enterprise_id = {enterprise}
                  AND owned_account.id = {client_id}
                  AND owned_account.owner_user_id = scope_actor.user_id
              )
            )
          )
      )
    """


def _scope_direct_predicate(alias: str) -> str:
    actor = _actor_clause(
        f"{alias}.enterprise_id",
        f"{alias}.scope_kind",
        f"{alias}.client_account_id",
    )
    return f"""
      {alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND {actor}
    """


def _record_scope_predicate(alias: str) -> str:
    actor = _actor_clause(
        "visible_scope.enterprise_id",
        "visible_scope.scope_kind",
        "visible_scope.client_account_id",
    )
    return f"""
      {alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND EXISTS (
        SELECT 1 FROM f1.material_knowledge_scope AS visible_scope
        WHERE visible_scope.enterprise_id = {alias}.enterprise_id
          AND visible_scope.id = {alias}.knowledge_scope_id
          AND {actor}
      )
    """


def _chain_scope_predicate(
    alias: str, *, joins: str, scope_link: str
) -> str:
    actor = _actor_clause(
        "visible_scope.enterprise_id",
        "visible_scope.scope_kind",
        "visible_scope.client_account_id",
    )
    return f"""
      {alias}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({alias}.enterprise_id)
      AND EXISTS (
        SELECT 1
        {joins}
        JOIN f1.material_knowledge_scope AS visible_scope
          ON visible_scope.enterprise_id = visible_record.enterprise_id
         AND visible_scope.id = visible_record.knowledge_scope_id
        WHERE {scope_link}
          AND {actor}
      )
    """


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


def _drop_api_policies() -> None:
    for policy, table in (
        ("p3_document_record_select", "document_record"),
        ("p3_document_record_insert", "document_record"),
        ("p3_document_record_update", "document_record"),
        ("p3_document_version_select", "document_version"),
        ("p3_document_version_insert", "document_version"),
        ("p3_preview_unit_select", "document_preview_unit"),
        ("material_material_analysis_select", "material_analysis"),
        ("material_material_analysis_insert", "material_analysis"),
        (
            "material_material_page_classification_select",
            "material_page_classification",
        ),
        (
            "material_material_page_classification_insert",
            "material_page_classification",
        ),
        ("material_material_field_candidate_select", "material_field_candidate"),
        ("material_material_field_candidate_insert", "material_field_candidate"),
        ("material_analysis_confirm", "material_analysis"),
    ):
        op.execute(f"DROP POLICY {policy} ON f1.{table}")


def _replace_scope_aware_policies() -> None:
    op.execute(
        "ALTER TABLE f1.material_knowledge_scope ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE f1.material_knowledge_scope FORCE ROW LEVEL SECURITY"
    )
    scope_access = _scope_direct_predicate("material_knowledge_scope")
    op.execute(
        "CREATE POLICY material_knowledge_scope_select "
        "ON f1.material_knowledge_scope FOR SELECT TO f1_api USING ("
        + scope_access
        + ")"
    )
    op.execute(
        "CREATE POLICY material_knowledge_scope_insert "
        "ON f1.material_knowledge_scope FOR INSERT TO f1_api WITH CHECK ("
        + scope_access
        + ")"
    )
    _drop_api_policies()

    record_access = _record_scope_predicate("document_record")
    op.execute(
        "CREATE POLICY p3_document_record_select ON f1.document_record "
        "FOR SELECT TO f1_api USING (" + record_access + ")"
    )
    op.execute(
        "CREATE POLICY p3_document_record_insert ON f1.document_record "
        "FOR INSERT TO f1_api WITH CHECK (" + record_access + " AND EXISTS ("
        "SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_record.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()) "
        "AND scope_selection_source='upload_selection' "
        "AND scope_selected_by_user_id=created_by_user_id "
        "AND scope_selected_at IS NOT NULL)"
    )
    op.execute(
        "CREATE POLICY p3_document_record_update ON f1.document_record "
        "FOR UPDATE TO f1_api USING (" + record_access + ") WITH CHECK ("
        + record_access
        + ")"
    )

    version_access = _chain_scope_predicate(
        "document_version",
        joins="FROM f1.document_record AS visible_record",
        scope_link=(
            "visible_record.enterprise_id = document_version.enterprise_id "
            "AND visible_record.id = document_version.document_record_id"
        ),
    )
    op.execute(
        "CREATE POLICY p3_document_version_select ON f1.document_version "
        "FOR SELECT TO f1_api USING (" + version_access + ")"
    )
    op.execute(
        "CREATE POLICY p3_document_version_insert ON f1.document_version "
        "FOR INSERT TO f1_api WITH CHECK (" + version_access + " AND EXISTS ("
        "SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_version.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()))"
    )

    preview_access = _chain_scope_predicate(
        "document_preview_unit",
        joins=(
            "FROM f1.document_version AS visible_version "
            "JOIN f1.document_record AS visible_record "
            "ON visible_record.enterprise_id = visible_version.enterprise_id "
            "AND visible_record.id = visible_version.document_record_id"
        ),
        scope_link=(
            "visible_version.enterprise_id = "
            "document_preview_unit.enterprise_id "
            "AND visible_version.id = "
            "document_preview_unit.document_version_id"
        ),
    )
    op.execute(
        "CREATE POLICY p3_preview_unit_select ON f1.document_preview_unit "
        "FOR SELECT TO f1_api USING (" + preview_access + ")"
    )

    analysis_access = _chain_scope_predicate(
        "material_analysis",
        joins=(
            "FROM f1.document_version AS visible_version "
            "JOIN f1.document_record AS visible_record "
            "ON visible_record.enterprise_id = visible_version.enterprise_id "
            "AND visible_record.id = visible_version.document_record_id"
        ),
        scope_link=(
            "visible_version.enterprise_id = material_analysis.enterprise_id "
            "AND visible_version.id = material_analysis.document_version_id"
        ),
    )
    viewer = _actor_roles(
        "material_analysis", "'super_admin','enterprise_admin','plant_admin'"
    )
    op.execute(
        "CREATE POLICY material_material_analysis_select ON f1.material_analysis "
        "FOR SELECT TO f1_api USING ("
        + analysis_access
        + " AND "
        + viewer
        + ")"
    )
    analysis_writer = analysis_access + " AND " + viewer + """
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
    op.execute(
        "CREATE POLICY material_material_analysis_insert ON f1.material_analysis "
        "FOR INSERT TO f1_api WITH CHECK (" + analysis_writer + ")"
    )

    page_access = _chain_scope_predicate(
        "material_page_classification",
        joins=(
            "FROM f1.material_analysis AS visible_analysis "
            "JOIN f1.document_version AS visible_version "
            "ON visible_version.enterprise_id = visible_analysis.enterprise_id "
            "AND visible_version.id = visible_analysis.document_version_id "
            "JOIN f1.document_record AS visible_record "
            "ON visible_record.enterprise_id = visible_version.enterprise_id "
            "AND visible_record.id = visible_version.document_record_id"
        ),
        scope_link=(
            "visible_analysis.enterprise_id = "
            "material_page_classification.enterprise_id "
            "AND visible_analysis.id = "
            "material_page_classification.analysis_id"
        ),
    )
    page_viewer = _actor_roles(
        "material_page_classification",
        "'super_admin','enterprise_admin','plant_admin'",
    )
    op.execute(
        "CREATE POLICY material_material_page_classification_select "
        "ON f1.material_page_classification FOR SELECT TO f1_api USING ("
        + page_access
        + " AND "
        + page_viewer
        + ")"
    )
    page_writer = page_access + " AND " + page_viewer + """
      AND EXISTS (
        SELECT 1 FROM f1.material_analysis AS analysis
        WHERE analysis.enterprise_id =
              material_page_classification.enterprise_id
          AND analysis.id = material_page_classification.analysis_id
          AND analysis.status = 'ready'
          AND analysis.parser_backend = 'pypdf_heuristic'
          AND analysis.shadow_status = 'disabled'
          AND material_page_classification.page_number <= analysis.page_count
      )
    """
    op.execute(
        "CREATE POLICY material_material_page_classification_insert "
        "ON f1.material_page_classification FOR INSERT TO f1_api WITH CHECK ("
        + page_writer
        + ")"
    )

    candidate_access = _chain_scope_predicate(
        "material_field_candidate",
        joins=(
            "FROM f1.material_analysis AS visible_analysis "
            "JOIN f1.document_version AS visible_version "
            "ON visible_version.enterprise_id = visible_analysis.enterprise_id "
            "AND visible_version.id = visible_analysis.document_version_id "
            "JOIN f1.document_record AS visible_record "
            "ON visible_record.enterprise_id = visible_version.enterprise_id "
            "AND visible_record.id = visible_version.document_record_id"
        ),
        scope_link=(
            "visible_analysis.enterprise_id = "
            "material_field_candidate.enterprise_id "
            "AND visible_analysis.id = material_field_candidate.analysis_id"
        ),
    )
    candidate_viewer = _actor_roles(
        "material_field_candidate",
        "'super_admin','enterprise_admin','plant_admin'",
    )
    op.execute(
        "CREATE POLICY material_material_field_candidate_select "
        "ON f1.material_field_candidate FOR SELECT TO f1_api USING ("
        + candidate_access
        + " AND "
        + candidate_viewer
        + ")"
    )
    candidate_writer = candidate_access + " AND " + candidate_viewer + """
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
        "CREATE POLICY material_material_field_candidate_insert "
        "ON f1.material_field_candidate FOR INSERT TO f1_api WITH CHECK ("
        + candidate_writer
        + ")"
    )

    confirmer = _actor_roles(
        "material_analysis", "'super_admin','enterprise_admin','plant_admin'"
    )
    op.execute(
        "CREATE POLICY material_analysis_confirm ON f1.material_analysis "
        "FOR UPDATE TO f1_api USING ("
        + analysis_access
        + " AND "
        + confirmer
        + ") WITH CHECK ("
        + analysis_access
        + " AND "
        + confirmer
        + ")"
    )


def _grants() -> None:
    op.execute("GRANT SELECT, INSERT ON f1.material_knowledge_scope TO f1_api")
    op.execute(
        "REVOKE UPDATE, DELETE ON f1.material_knowledge_scope FROM f1_api"
    )
    op.execute(
        "REVOKE ALL ON f1.material_knowledge_scope FROM PUBLIC, f1_worker"
    )


def downgrade() -> None:
    # A provider-only migration backfill maps losslessly to f1_0012. Any
    # user-selected scope or client domain carries information f1_0012 cannot
    # represent, so refuse before changing policies or schema.
    op.execute(
        """
        DO $material_scope_downgrade$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM f1.material_knowledge_scope
            WHERE scope_kind = 'client'
          ) OR EXISTS (
            SELECT 1
            FROM f1.document_record AS record
            JOIN f1.material_knowledge_scope AS scope
              ON scope.enterprise_id = record.enterprise_id
             AND scope.id = record.knowledge_scope_id
            WHERE scope.scope_kind <> 'service_provider'
               OR record.scope_selection_source <> 'migration_backfill'
               OR record.scope_selected_by_user_id <>
                  record.created_by_user_id
               OR record.scope_selected_at <> record.created_at
          ) THEN
            RAISE EXCEPTION
              'MATERIAL_SCOPE_DOWNGRADE_REQUIRES_BACKFILL_ONLY';
          END IF;
        END
        $material_scope_downgrade$
        """
    )
    _drop_api_policies()
    op.execute(
        "DROP TRIGGER policy_version_document_scope_guard ON f1.policy_version"
    )
    op.execute("DROP FUNCTION f1.p5_guard_policy_document_scope()")
    op.execute(
        "DROP TRIGGER material_analysis_policy_scope_guard "
        "ON f1.material_analysis"
    )
    op.execute("DROP FUNCTION f1.material_guard_policy_scope()")
    _restore_f1_0012_document_guard()
    _restore_f1_0012_policies()
    op.execute("DROP INDEX f1.document_record_knowledge_scope_idx")
    op.execute(
        "ALTER TABLE f1.document_record DROP CONSTRAINT "
        "document_record_scope_source_ck"
    )
    op.execute(
        "ALTER TABLE f1.document_record DROP CONSTRAINT "
        "document_record_scope_actor_enterprise_fk"
    )
    op.execute(
        "ALTER TABLE f1.document_record DROP CONSTRAINT "
        "document_record_knowledge_scope_enterprise_fk"
    )
    op.execute(
        """
        ALTER TABLE f1.document_record
          DROP COLUMN scope_selected_at,
          DROP COLUMN scope_selected_by_user_id,
          DROP COLUMN scope_selection_source,
          DROP COLUMN knowledge_scope_id
        """
    )
    op.execute("DROP TABLE f1.material_knowledge_scope")


def _restore_f1_0012_document_guard() -> None:
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


def _legacy_manager(table: str) -> str:
    actor = _actor_roles(
        table, "'super_admin','enterprise_admin','plant_admin'"
    )
    return f"""
      {table}.enterprise_id = f1.current_enterprise_id()
      AND f1.session_authorized({table}.enterprise_id)
      AND {actor}
    """


def _restore_f1_0012_policies() -> None:
    record_access = _legacy_manager("document_record")
    op.execute(
        "CREATE POLICY p3_document_record_select ON f1.document_record "
        "FOR SELECT TO f1_api USING (" + record_access + ")"
    )
    op.execute(
        "CREATE POLICY p3_document_record_insert ON f1.document_record "
        "FOR INSERT TO f1_api WITH CHECK (" + record_access + " AND EXISTS ("
        "SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_record.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()))"
    )
    op.execute(
        "CREATE POLICY p3_document_record_update ON f1.document_record "
        "FOR UPDATE TO f1_api USING (" + record_access + ") WITH CHECK ("
        + record_access
        + ")"
    )
    version_access = _legacy_manager("document_version")
    op.execute(
        "CREATE POLICY p3_document_version_select ON f1.document_version "
        "FOR SELECT TO f1_api USING (" + version_access + ")"
    )
    op.execute(
        "CREATE POLICY p3_document_version_insert ON f1.document_version "
        "FOR INSERT TO f1_api WITH CHECK (" + version_access + " AND EXISTS ("
        "SELECT 1 FROM f1.user_profile AS creator "
        "WHERE creator.id=document_version.created_by_user_id "
        "AND creator.keycloak_sub=f1.current_sub()))"
    )
    preview_access = _legacy_manager("document_preview_unit")
    op.execute(
        "CREATE POLICY p3_preview_unit_select ON f1.document_preview_unit "
        "FOR SELECT TO f1_api USING (" + preview_access + ")"
    )

    for table in (
        "material_analysis",
        "material_page_classification",
        "material_field_candidate",
    ):
        viewer = _actor_roles(
            table, "'super_admin','enterprise_admin','plant_admin'"
        )
        op.execute(
            f"CREATE POLICY material_{table}_select ON f1.{table} "
            "FOR SELECT TO f1_api USING ("
            "enterprise_id = f1.current_enterprise_id() AND " + viewer + ")"
        )
        writer = viewer
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
                WHERE analysis.enterprise_id =
                      material_page_classification.enterprise_id
                  AND analysis.id = material_page_classification.analysis_id
                  AND analysis.status = 'ready'
                  AND analysis.parser_backend = 'pypdf_heuristic'
                  AND analysis.shadow_status = 'disabled'
                  AND material_page_classification.page_number <=
                      analysis.page_count
              )
            """
        else:
            writer += """
              AND producer = 'pypdf_heuristic'
              AND calibrated IS FALSE
              AND EXISTS (
                SELECT 1 FROM f1.material_analysis AS analysis
                WHERE analysis.enterprise_id =
                      material_field_candidate.enterprise_id
                  AND analysis.id = material_field_candidate.analysis_id
                  AND analysis.status = 'ready'
                  AND analysis.parser_backend = 'pypdf_heuristic'
                  AND analysis.shadow_status = 'disabled'
                  AND material_field_candidate.page_number <=
                      analysis.page_count
              )
            """
        op.execute(
            f"CREATE POLICY material_{table}_insert ON f1.{table} "
            "FOR INSERT TO f1_api WITH CHECK ("
            "enterprise_id = f1.current_enterprise_id() AND " + writer + ")"
        )
    confirmer = _actor_roles(
        "material_analysis", "'super_admin','enterprise_admin','plant_admin'"
    )
    op.execute(
        "CREATE POLICY material_analysis_confirm ON f1.material_analysis "
        "FOR UPDATE TO f1_api USING ("
        "enterprise_id = f1.current_enterprise_id() AND "
        + confirmer
        + ") WITH CHECK (enterprise_id = f1.current_enterprise_id() AND "
        + confirmer
        + ")"
    )
