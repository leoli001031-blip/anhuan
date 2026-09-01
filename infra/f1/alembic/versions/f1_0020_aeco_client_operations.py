"""Bind service cases and material QA to the active A-Eco client audience."""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0020"
down_revision: str | None = "f1_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_AECO_DEFINER_ROLE = "f1_aeco_read_definer"


def upgrade() -> None:
    _service_case_client_binding()
    _client_material_functions()
    _aeco_definer_security()


def _service_case_client_binding() -> None:
    op.execute(
        "ALTER TABLE f1.service_case ADD COLUMN client_account_id uuid"
    )
    op.execute(
        "ALTER TABLE f1.service_case ADD CONSTRAINT "
        "service_case_client_account_fk FOREIGN KEY "
        "(enterprise_id, client_account_id) "
        "REFERENCES f1.crm_account(enterprise_id, id)"
    )
    op.execute(
        "CREATE INDEX service_case_client_status_idx ON f1.service_case("
        "enterprise_id, client_account_id, status, planned_start_at) "
        "WHERE client_account_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE FUNCTION f1.aeco_guard_service_case_client_identity()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.client_account_id IS DISTINCT FROM OLD.client_account_id THEN
            RAISE EXCEPTION 'AECO_SERVICE_CASE_CLIENT_IMMUTABLE';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER aeco_service_case_client_identity_guard "
        "BEFORE UPDATE ON f1.service_case FOR EACH ROW "
        "EXECUTE FUNCTION f1.aeco_guard_service_case_client_identity()"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f1.aeco_guard_service_case_client_identity() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.aeco_guard_service_case_client_identity() TO f1_api"
    )
    op.execute(
        """
        CREATE FUNCTION f1.aeco_client_service_cases()
        RETURNS TABLE (
          id uuid,
          title text,
          service_type text,
          status text,
          planned_start_at timestamptz,
          planned_end_at timestamptz,
          assigned boolean,
          updated_at timestamptz
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          SELECT service.id,
                 service.title,
                 service.service_type,
                 service.status,
                 service.planned_start_at,
                 service.planned_end_at,
                 EXISTS (
                   SELECT 1 FROM f1.service_assignment AS assignment
                   WHERE assignment.enterprise_id = service.enterprise_id
                     AND assignment.service_case_id = service.id
                     AND assignment.status IN ('pending','accepted')
                 ),
                 service.updated_at
          FROM f1.service_case AS service
          JOIN f1.analysis_report_client_audience AS binding
            ON binding.enterprise_id = service.enterprise_id
           AND binding.client_account_id = service.client_account_id
           AND binding.status = 'active'
          WHERE binding.audience_enterprise_id = f1.current_enterprise_id()
            AND f1.session_authorized(f1.current_enterprise_id())
          ORDER BY service.updated_at DESC, service.id
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.aeco_client_service_cases() FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.aeco_client_service_cases() TO f1_api"
    )


def _client_material_functions() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.aeco_client_material_context()
        RETURNS TABLE (
          provider_enterprise_id uuid,
          client_account_id uuid,
          provider_scope_id uuid,
          client_scope_id uuid
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          WITH candidates AS MATERIALIZED (
            SELECT binding.enterprise_id AS provider_enterprise_id,
                   binding.client_account_id,
                   provider_scope.id AS provider_scope_id,
                   client_scope.id AS client_scope_id
            FROM f1.analysis_report_client_audience AS binding
            JOIN f1.material_knowledge_scope AS provider_scope
              ON provider_scope.enterprise_id = binding.enterprise_id
             AND provider_scope.scope_kind = 'service_provider'
             AND provider_scope.client_account_id IS NULL
            JOIN f1.material_knowledge_scope AS client_scope
              ON client_scope.enterprise_id = binding.enterprise_id
             AND client_scope.scope_kind = 'client'
             AND client_scope.client_account_id = binding.client_account_id
            WHERE binding.audience_enterprise_id = f1.current_enterprise_id()
              AND binding.status = 'active'
              AND f1.session_authorized(f1.current_enterprise_id())
            LIMIT 2
          )
          SELECT candidates.provider_enterprise_id,
                 candidates.client_account_id,
                 candidates.provider_scope_id,
                 candidates.client_scope_id
          FROM candidates
          WHERE (SELECT count(*) FROM candidates) = 1
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.aeco_client_material_bindings()
        RETURNS TABLE (
          provider_enterprise_id uuid,
          knowledge_scope_id uuid,
          binding_id uuid,
          dataset_ref_ciphertext bytea,
          dataset_ref_sha256 text,
          dataset_ref_aad_sha256 text
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
          WITH context AS (
            SELECT * FROM f1.aeco_client_material_context()
          )
          SELECT context.provider_enterprise_id,
                 scope_binding.knowledge_scope_id,
                 scope_binding.id,
                 scope_binding.dataset_ref_ciphertext,
                 scope_binding.dataset_ref_sha256,
                 scope_binding.dataset_ref_aad_sha256
          FROM context
          JOIN f1.material_rag_scope_binding AS scope_binding
            ON scope_binding.enterprise_id = context.provider_enterprise_id
           AND scope_binding.knowledge_scope_id IN (
             context.provider_scope_id, context.client_scope_id
           )
           AND scope_binding.backend = 'ragflow'
           AND scope_binding.status = 'ready'
          ORDER BY scope_binding.knowledge_scope_id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.aeco_client_material_units(p_unit_ids uuid[])
        RETURNS TABLE (
          provider_enterprise_id uuid,
          canonical_unit_id uuid,
          knowledge_scope_id uuid,
          document_record_id uuid,
          document_version_id uuid,
          source_sha256 text,
          page_number integer,
          ordinal integer,
          parser_version text,
          body_ciphertext bytea,
          body_sha256 text,
          body_aad_sha256 text,
          scope_kind text,
          document_name text,
          version_number integer
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
          IF p_unit_ids IS NULL OR cardinality(p_unit_ids) < 1
             OR cardinality(p_unit_ids) > 20 THEN
            RAISE EXCEPTION 'AECO_CLIENT_MATERIAL_UNIT_IDS_INVALID';
          END IF;
          RETURN QUERY
          WITH context AS (
            SELECT * FROM f1.aeco_client_material_context()
          )
          SELECT context.provider_enterprise_id,
                 unit.id,
                 unit.knowledge_scope_id,
                 unit.document_record_id,
                 unit.document_version_id,
                 unit.source_sha256,
                 unit.page_number,
                 unit.ordinal,
                 unit.parser_version,
                 unit.body_ciphertext,
                 unit.body_sha256,
                 unit.body_aad_sha256,
                 scope.scope_kind,
                 record.title,
                 version.version_no
          FROM context
          JOIN f1.material_rag_unit AS unit
            ON unit.enterprise_id = context.provider_enterprise_id
           AND unit.knowledge_scope_id IN (
             context.provider_scope_id, context.client_scope_id
           )
           AND unit.id = ANY(p_unit_ids)
          JOIN f1.material_knowledge_scope AS scope
            ON scope.enterprise_id = unit.enterprise_id
           AND scope.id = unit.knowledge_scope_id
          JOIN f1.document_record AS record
            ON record.enterprise_id = unit.enterprise_id
           AND record.id = unit.document_record_id
           AND record.knowledge_scope_id = unit.knowledge_scope_id
          JOIN f1.document_version AS version
            ON version.enterprise_id = unit.enterprise_id
           AND version.id = unit.document_version_id
           AND version.document_record_id = unit.document_record_id
          JOIN f1.upload_task AS task
            ON task.enterprise_id = version.enterprise_id
           AND task.id = version.upload_task_id
          WHERE record.status = 'active'
            AND version.version_no = record.latest_version_no
            AND task.pipeline_kind = 'controlled_ingestion'
            AND task.status = 'done'
            AND task.processing_stage = 'ready'
            AND task.object_state = 'ready'
            AND task.scan_verdict = 'clean'
            AND task.preview_status = 'ready'
            AND task.quarantine_status = 'released'
            AND task.released_at IS NOT NULL
            AND task.rejected_at IS NULL
            AND task.content_sha256 = unit.source_sha256
          ORDER BY unit.id;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.aeco_client_material_local_units(p_limit integer)
        RETURNS TABLE (
          provider_enterprise_id uuid,
          canonical_unit_id uuid,
          knowledge_scope_id uuid,
          document_record_id uuid,
          document_version_id uuid,
          source_sha256 text,
          page_number integer,
          ordinal integer,
          parser_version text,
          body_ciphertext bytea,
          body_sha256 text,
          body_aad_sha256 text,
          scope_kind text,
          document_name text,
          version_number integer
        )
        LANGUAGE plpgsql STABLE SECURITY DEFINER
        SET search_path = pg_catalog
        AS $$
        BEGIN
          IF p_limit IS NULL OR p_limit < 1 OR p_limit > 256 THEN
            RAISE EXCEPTION 'AECO_CLIENT_MATERIAL_LIMIT_INVALID';
          END IF;
          RETURN QUERY
          WITH context AS (
            SELECT * FROM f1.aeco_client_material_context()
          )
          SELECT context.provider_enterprise_id,
                 unit.id,
                 unit.knowledge_scope_id,
                 unit.document_record_id,
                 unit.document_version_id,
                 unit.source_sha256,
                 unit.page_number,
                 unit.ordinal,
                 unit.parser_version,
                 unit.body_ciphertext,
                 unit.body_sha256,
                 unit.body_aad_sha256,
                 scope.scope_kind,
                 record.title,
                 version.version_no
          FROM context
          JOIN f1.material_rag_unit AS unit
            ON unit.enterprise_id = context.provider_enterprise_id
           AND unit.knowledge_scope_id IN (
             context.provider_scope_id, context.client_scope_id
           )
          JOIN f1.material_knowledge_scope AS scope
            ON scope.enterprise_id = unit.enterprise_id
           AND scope.id = unit.knowledge_scope_id
          JOIN f1.document_record AS record
            ON record.enterprise_id = unit.enterprise_id
           AND record.id = unit.document_record_id
           AND record.knowledge_scope_id = unit.knowledge_scope_id
          JOIN f1.document_version AS version
            ON version.enterprise_id = unit.enterprise_id
           AND version.id = unit.document_version_id
           AND version.document_record_id = unit.document_record_id
          JOIN f1.upload_task AS task
            ON task.enterprise_id = version.enterprise_id
           AND task.id = version.upload_task_id
          WHERE record.status = 'active'
            AND version.version_no = record.latest_version_no
            AND task.pipeline_kind = 'controlled_ingestion'
            AND task.status = 'done'
            AND task.processing_stage = 'ready'
            AND task.object_state = 'ready'
            AND task.scan_verdict = 'clean'
            AND task.preview_status = 'ready'
            AND task.quarantine_status = 'released'
            AND task.released_at IS NOT NULL
            AND task.rejected_at IS NULL
            AND task.content_sha256 = unit.source_sha256
          ORDER BY unit.id
          LIMIT p_limit;
        END
        $$
        """
    )
    for signature in (
        "f1.aeco_client_material_context()",
        "f1.aeco_client_material_bindings()",
        "f1.aeco_client_material_units(uuid[])",
        "f1.aeco_client_material_local_units(integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _aeco_definer_security() -> None:
    """Give the client bridge a read-only, RLS-constrained owner surface."""
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA f1 FROM {_AECO_DEFINER_ROLE}"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA f1 FROM {_AECO_DEFINER_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {_AECO_DEFINER_ROLE}")
    op.execute(
        "GRANT SELECT ON f1.service_case, f1.service_assignment, "
        "f1.analysis_report_client_audience, f1.material_knowledge_scope, "
        "f1.material_rag_scope_binding, f1.material_rag_unit, "
        "f1.document_record, f1.document_version, f1.upload_task "
        f"TO {_AECO_DEFINER_ROLE}"
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_audience_select
        ON f1.analysis_report_client_audience
        FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND audience_enterprise_id = f1.current_enterprise_id()
          AND status = 'active'
          AND f1.session_authorized(f1.current_enterprise_id())
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_service_case_select
        ON f1.service_case FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.analysis_report_client_audience AS audience
            WHERE audience.enterprise_id = service_case.enterprise_id
              AND audience.client_account_id = service_case.client_account_id
              AND audience.audience_enterprise_id = f1.current_enterprise_id()
              AND audience.status = 'active'
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_service_assignment_select
        ON f1.service_assignment FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.service_case AS service
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = service.enterprise_id
             AND audience.client_account_id = service.client_account_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE service.enterprise_id = service_assignment.enterprise_id
              AND service.id = service_assignment.service_case_id
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_scope_select
        ON f1.material_knowledge_scope FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.analysis_report_client_audience AS audience
            WHERE audience.enterprise_id = material_knowledge_scope.enterprise_id
              AND audience.audience_enterprise_id = f1.current_enterprise_id()
              AND audience.status = 'active'
              AND (
                (material_knowledge_scope.scope_kind = 'service_provider'
                 AND material_knowledge_scope.client_account_id IS NULL)
                OR
                (material_knowledge_scope.scope_kind = 'client'
                 AND material_knowledge_scope.client_account_id =
                     audience.client_account_id)
              )
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_binding_select
        ON f1.material_rag_scope_binding FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.material_knowledge_scope AS scope
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = scope.enterprise_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE scope.enterprise_id = material_rag_scope_binding.enterprise_id
              AND scope.id = material_rag_scope_binding.knowledge_scope_id
              AND (
                (scope.scope_kind = 'service_provider'
                 AND scope.client_account_id IS NULL)
                OR
                (scope.scope_kind = 'client'
                 AND scope.client_account_id = audience.client_account_id)
              )
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_unit_select
        ON f1.material_rag_unit FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.material_knowledge_scope AS scope
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = scope.enterprise_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE scope.enterprise_id = material_rag_unit.enterprise_id
              AND scope.id = material_rag_unit.knowledge_scope_id
              AND (
                (scope.scope_kind = 'service_provider'
                 AND scope.client_account_id IS NULL)
                OR
                (scope.scope_kind = 'client'
                 AND scope.client_account_id = audience.client_account_id)
              )
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_record_select
        ON f1.document_record FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.material_knowledge_scope AS scope
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = scope.enterprise_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE scope.enterprise_id = document_record.enterprise_id
              AND scope.id = document_record.knowledge_scope_id
              AND (
                (scope.scope_kind = 'service_provider'
                 AND scope.client_account_id IS NULL)
                OR
                (scope.scope_kind = 'client'
                 AND scope.client_account_id = audience.client_account_id)
              )
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_version_select
        ON f1.document_version FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.document_record AS record
            JOIN f1.material_knowledge_scope AS scope
              ON scope.enterprise_id = record.enterprise_id
             AND scope.id = record.knowledge_scope_id
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = scope.enterprise_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE record.enterprise_id = document_version.enterprise_id
              AND record.id = document_version.document_record_id
              AND (
                (scope.scope_kind = 'service_provider'
                 AND scope.client_account_id IS NULL)
                OR
                (scope.scope_kind = 'client'
                 AND scope.client_account_id = audience.client_account_id)
              )
          )
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY aeco_definer_upload_select
        ON f1.upload_task FOR SELECT TO {_AECO_DEFINER_ROLE}
        USING (
          session_user = 'f1_api'
          AND f1.session_authorized(f1.current_enterprise_id())
          AND EXISTS (
            SELECT 1 FROM f1.document_version AS version
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
            JOIN f1.material_knowledge_scope AS scope
              ON scope.enterprise_id = record.enterprise_id
             AND scope.id = record.knowledge_scope_id
            JOIN f1.analysis_report_client_audience AS audience
              ON audience.enterprise_id = scope.enterprise_id
             AND audience.audience_enterprise_id = f1.current_enterprise_id()
             AND audience.status = 'active'
            WHERE version.enterprise_id = upload_task.enterprise_id
              AND version.upload_task_id = upload_task.id
              AND (
                (scope.scope_kind = 'service_provider'
                 AND scope.client_account_id IS NULL)
                OR
                (scope.scope_kind = 'client'
                 AND scope.client_account_id = audience.client_account_id)
              )
          )
        )
        """
    )


def _drop_aeco_definer_security() -> None:
    for policy, table in (
        ("aeco_definer_upload_select", "upload_task"),
        ("aeco_definer_version_select", "document_version"),
        ("aeco_definer_record_select", "document_record"),
        ("aeco_definer_unit_select", "material_rag_unit"),
        ("aeco_definer_binding_select", "material_rag_scope_binding"),
        ("aeco_definer_scope_select", "material_knowledge_scope"),
        ("aeco_definer_service_assignment_select", "service_assignment"),
        ("aeco_definer_service_case_select", "service_case"),
        ("aeco_definer_audience_select", "analysis_report_client_audience"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.{table}")
    op.execute(
        "REVOKE SELECT ON f1.service_case, f1.service_assignment, "
        "f1.analysis_report_client_audience, f1.material_knowledge_scope, "
        "f1.material_rag_scope_binding, f1.material_rag_unit, "
        "f1.document_record, f1.document_version, f1.upload_task "
        f"FROM {_AECO_DEFINER_ROLE}"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA f1 FROM {_AECO_DEFINER_ROLE}")


def downgrade() -> None:
    _drop_aeco_definer_security()
    for signature in (
        "f1.aeco_client_material_local_units(integer)",
        "f1.aeco_client_material_units(uuid[])",
        "f1.aeco_client_material_bindings()",
        "f1.aeco_client_material_context()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    op.execute("DROP FUNCTION IF EXISTS f1.aeco_client_service_cases()")
    op.execute(
        "DROP TRIGGER IF EXISTS aeco_service_case_client_identity_guard "
        "ON f1.service_case"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS f1.aeco_guard_service_case_client_identity()"
    )
    op.execute("DROP INDEX IF EXISTS f1.service_case_client_status_idx")
    op.execute(
        "ALTER TABLE f1.service_case DROP CONSTRAINT IF EXISTS "
        "service_case_client_account_fk"
    )
    op.execute("ALTER TABLE f1.service_case DROP COLUMN IF EXISTS client_account_id")
