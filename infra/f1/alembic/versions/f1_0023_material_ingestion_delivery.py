"""Durable controlled-ingestion delivery and bounded OCR checkpoint purge.

The delivery stores only stable identities, an authenticated actor ``sub``,
fixed state, and fixed reason codes.  File names, object keys, source bodies,
OCR text, credentials, and exception details never enter this table or its
queue payload.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0023"
down_revision: str | None = "f1_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "material_ingestion_delivery"
_DEFINER_ROLE = "f1_material_ingestion_definer"
_REPORT_DEFINER_ROLE = "f1_analysis_report_definer"
_REPORT_DELIVERY_TABLE = "analysis_report_generation_delivery"


def upgrade() -> None:
    _require_definer_role()
    _table()
    _functions()
    _rls_and_grants()
    _ready_ocr_successor_boundary()
    _material_analysis_api_update_boundary()
    _backfill_eligible_latest_versions()
    _backfill_material_pipeline_deliveries()
    _ocr_purge_boundary()
    _report_actor_revocation_boundary()
    _analysis_report_api_write_boundary()
    _report_generation_delivery_boundary()


def _require_definer_role() -> None:
    for role, missing_code in (
        (_DEFINER_ROLE, "F1_MATERIAL_INGESTION_DEFINER_REQUIRED"),
        (_REPORT_DEFINER_ROLE, "F1_ANALYSIS_REPORT_DEFINER_REQUIRED"),
    ):
        op.execute(
            f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = '{role}'
          ) THEN
            RAISE EXCEPTION '{missing_code}';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname = '{role}'
              AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
                   OR rolinherit OR rolreplication OR rolbypassrls)
          ) THEN
            RAISE EXCEPTION 'F1_DEFINER_ROLE_UNSAFE';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_auth_members AS membership
            JOIN pg_roles AS granted_role
              ON granted_role.oid = membership.roleid
            JOIN pg_roles AS member_role
              ON member_role.oid = membership.member
            WHERE granted_role.rolname = '{role}'
               OR member_role.rolname = '{role}'
          ) THEN
            RAISE EXCEPTION 'F1_DEFINER_ROLE_MEMBERSHIP_FORBIDDEN';
          END IF;
        END
        $$
        """
        )


def _table() -> None:
    op.execute(
        f"""
        CREATE TABLE f1.{_TABLE} (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          document_version_id uuid NOT NULL,
          delivery_kind text NOT NULL DEFAULT 'resume',
          actor_sub text NOT NULL,
          state text NOT NULL DEFAULT 'pending',
          attempt integer NOT NULL DEFAULT 0,
          dispatch_token uuid,
          dispatch_lease_until timestamptz,
          next_attempt_at timestamptz,
          reason_code text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          completed_at timestamptz,
          CONSTRAINT material_ingestion_delivery_enterprise_id_id_uq
            UNIQUE (enterprise_id,id),
          CONSTRAINT material_ingestion_delivery_identity_uq
            UNIQUE (enterprise_id,document_version_id,delivery_kind),
          CONSTRAINT material_ingestion_delivery_version_fk
            FOREIGN KEY (enterprise_id,document_version_id)
            REFERENCES f1.document_version(enterprise_id,id),
          CONSTRAINT material_ingestion_delivery_kind_ck
            CHECK (delivery_kind = 'resume'),
          CONSTRAINT material_ingestion_delivery_actor_ck CHECK (
            char_length(actor_sub) BETWEEN 1 AND 255
            AND actor_sub = btrim(actor_sub)
            AND actor_sub !~ '[[:cntrl:]]'
          ),
          CONSTRAINT material_ingestion_delivery_state_ck CHECK (
            state IN ('pending','dispatched','retry_wait','done','blocked')
          ),
          CONSTRAINT material_ingestion_delivery_attempt_ck
            CHECK (attempt BETWEEN 0 AND 100),
          CONSTRAINT material_ingestion_delivery_reason_ck CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z0-9_]{{1,80}}$'
          ),
          CONSTRAINT material_ingestion_delivery_shape_ck CHECK (
            (state = 'pending'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NULL)
            OR
            (state = 'dispatched'
             AND dispatch_token IS NOT NULL AND dispatch_lease_until IS NOT NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NULL)
            OR
            (state = 'retry_wait'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NOT NULL AND reason_code IS NOT NULL
             AND completed_at IS NULL)
            OR
            (state = 'done'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NOT NULL)
            OR
            (state = 'blocked'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NOT NULL
             AND completed_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        f"CREATE INDEX material_ingestion_delivery_due_idx ON f1.{_TABLE}("
        "state,next_attempt_at,dispatch_lease_until,updated_at,id)"
    )


def _functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION f1.register_material_ingestion_delivery(
          p_enterprise_id uuid, p_document_version_id uuid,
          p_actor_sub text, p_rearm_terminal boolean
        ) RETURNS TABLE(
          delivery_id uuid, enterprise_id uuid, document_version_id uuid,
          actor_sub text, state text, attempt integer, reason_code text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_id uuid;
        BEGIN
          v_id := md5(
            'material-ingestion:resume:' || p_enterprise_id::text || ':' ||
            p_document_version_id::text
          )::uuid;
          IF session_user <> 'f1_api'
             OR p_enterprise_id IS NULL OR p_document_version_id IS NULL
             OR p_rearm_terminal IS NULL
             OR p_actor_sub IS NULL OR char_length(p_actor_sub) NOT BETWEEN 1 AND 255
             OR p_actor_sub <> btrim(p_actor_sub)
             OR p_actor_sub ~ '[[:cntrl:]]'
             OR NULLIF(current_setting('f1.enterprise_id',true),'')::uuid
                IS DISTINCT FROM p_enterprise_id
             OR NULLIF(current_setting('f1.sub',true),'')
                IS DISTINCT FROM p_actor_sub
             OR NOT f1.session_authorized(p_enterprise_id)
             OR NOT EXISTS (
               SELECT 1
               FROM f1.document_version AS visible_version
               JOIN f1.document_record AS visible_record
                 ON visible_record.enterprise_id=visible_version.enterprise_id
                AND visible_record.id=visible_version.document_record_id
               JOIN f1.material_knowledge_scope AS visible_scope
                 ON visible_scope.enterprise_id=visible_record.enterprise_id
                AND visible_scope.id=visible_record.knowledge_scope_id
               JOIN f1.enterprise_user AS actor
                 ON actor.enterprise_id=visible_scope.enterprise_id
               JOIN f1.user_profile AS profile ON profile.id=actor.user_id
               WHERE visible_version.enterprise_id=p_enterprise_id
                 AND visible_version.id=p_document_version_id
                 AND profile.keycloak_sub=p_actor_sub
                 AND actor.role IN ('super_admin','enterprise_admin','plant_admin')
                 AND (
                   visible_scope.scope_kind='service_provider'
                   OR actor.role IN ('super_admin','enterprise_admin')
                   OR (
                     actor.role='plant_admin'
                     AND EXISTS (
                       SELECT 1 FROM f1.crm_account AS owned_account
                       WHERE owned_account.enterprise_id=visible_scope.enterprise_id
                         AND owned_account.id=visible_scope.client_account_id
                         AND owned_account.owner_user_id=actor.user_id
                     )
                   )
                 )
             ) THEN
            RAISE EXCEPTION 'MATERIAL_INGESTION_DELIVERY_REGISTER_INVALID';
          END IF;

          RETURN QUERY
          INSERT INTO f1.{_TABLE} AS delivery (
            id,enterprise_id,document_version_id,delivery_kind,actor_sub,state
          ) VALUES (
            v_id,p_enterprise_id,p_document_version_id,'resume',p_actor_sub,'pending'
          )
          ON CONFLICT ON CONSTRAINT material_ingestion_delivery_identity_uq
          DO UPDATE SET actor_sub=EXCLUDED.actor_sub,state='pending',attempt=0,
            dispatch_token=NULL,dispatch_lease_until=NULL,next_attempt_at=NULL,
            reason_code=NULL,completed_at=NULL,updated_at=statement_timestamp()
          WHERE p_rearm_terminal IS TRUE
            AND delivery.state IN ('done','blocked')
          RETURNING delivery.id,delivery.enterprise_id,
            delivery.document_version_id,delivery.actor_sub,delivery.state,
            delivery.attempt,delivery.reason_code;
          IF NOT FOUND THEN
            RETURN QUERY
            SELECT delivery.id,delivery.enterprise_id,
              delivery.document_version_id,delivery.actor_sub,delivery.state,
              delivery.attempt,delivery.reason_code
            FROM f1.{_TABLE} AS delivery
            WHERE delivery.enterprise_id=p_enterprise_id
              AND delivery.document_version_id=p_document_version_id
              AND delivery.delivery_kind='resume';
          END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.claim_material_ingestion_deliveries(
          p_limit integer, p_lease_seconds integer
        ) RETURNS TABLE(
          delivery_id uuid, enterprise_id uuid, document_version_id uuid,
          actor_sub text, dispatch_token uuid, attempt integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
            RAISE EXCEPTION 'MATERIAL_INGESTION_DELIVERY_CLAIM_INVALID';
          END IF;

          UPDATE f1.{_TABLE} AS exhausted
             SET state = 'blocked',
                 dispatch_token = NULL, dispatch_lease_until = NULL,
                 next_attempt_at = NULL,
                 reason_code = 'MATERIAL_INGESTION_RETRIES_EXHAUSTED',
                 completed_at = statement_timestamp(),
                 updated_at = statement_timestamp()
           WHERE exhausted.attempt >= 100
             AND (
               exhausted.state = 'pending'
               OR (exhausted.state = 'retry_wait'
                   AND exhausted.next_attempt_at <= statement_timestamp())
               OR (exhausted.state = 'dispatched'
                   AND exhausted.dispatch_lease_until <= statement_timestamp())
             );

          RETURN QUERY
          WITH candidates AS MATERIALIZED (
            SELECT delivery.id
            FROM f1.{_TABLE} AS delivery
            WHERE delivery.attempt < 100
              AND (
                delivery.state = 'pending'
                OR (delivery.state = 'retry_wait'
                    AND delivery.next_attempt_at <= statement_timestamp())
                OR (delivery.state = 'dispatched'
                    AND delivery.dispatch_lease_until <= statement_timestamp())
              )
            ORDER BY COALESCE(
                       delivery.next_attempt_at,
                       delivery.dispatch_lease_until,
                       delivery.updated_at
                     ), delivery.id
            LIMIT p_limit
            FOR UPDATE OF delivery SKIP LOCKED
          )
          UPDATE f1.{_TABLE} AS delivery
             SET state = 'dispatched',
                 attempt = delivery.attempt + 1,
                 dispatch_token = gen_random_uuid(),
                 dispatch_lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 next_attempt_at = NULL, reason_code = NULL,
                 completed_at = NULL, updated_at = statement_timestamp()
            FROM candidates
           WHERE delivery.id = candidates.id
          RETURNING delivery.id, delivery.enterprise_id,
                    delivery.document_version_id, delivery.actor_sub,
                    delivery.dispatch_token, delivery.attempt;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.read_material_ingestion_delivery_claim(
          p_delivery_id uuid, p_dispatch_token uuid
        ) RETURNS TABLE(
          delivery_id uuid, enterprise_id uuid, document_version_id uuid,
          actor_sub text, dispatch_token uuid, attempt integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
          IF session_user NOT IN ('f1_api','f1_worker')
             OR (session_user = 'f1_api' AND (
               NULLIF(current_setting('f1.enterprise_id', true), '') IS NOT NULL
               OR NULLIF(current_setting('f1.sub', true), '') IS NOT NULL
             ))
             OR p_delivery_id IS NULL OR p_dispatch_token IS NULL THEN
            RAISE EXCEPTION 'MATERIAL_INGESTION_DELIVERY_READ_INVALID';
          END IF;
          RETURN QUERY
          SELECT delivery.id, delivery.enterprise_id,
                 delivery.document_version_id, delivery.actor_sub,
                 delivery.dispatch_token, delivery.attempt
          FROM f1.{_TABLE} AS delivery
          WHERE delivery.id = p_delivery_id
            AND delivery.state = 'dispatched'
            AND delivery.dispatch_token = p_dispatch_token
            AND delivery.dispatch_lease_until > statement_timestamp();
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.finish_material_ingestion_delivery(
          p_delivery_id uuid, p_dispatch_token uuid, p_outcome text,
          p_reason_code text, p_retry_seconds integer
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF session_user NOT IN ('f1_api','f1_worker')
             OR (session_user = 'f1_api' AND (
               NULLIF(current_setting('f1.enterprise_id', true), '') IS NOT NULL
               OR NULLIF(current_setting('f1.sub', true), '') IS NOT NULL
             ))
             OR p_delivery_id IS NULL OR p_dispatch_token IS NULL
             OR p_outcome IS NULL
             OR p_outcome NOT IN ('done','retry','blocked')
             OR (p_reason_code IS NOT NULL
                 AND p_reason_code !~ '^[A-Z0-9_]{{1,80}}$')
             OR (p_outcome = 'done'
                 AND (p_reason_code IS NOT NULL OR p_retry_seconds IS NOT NULL))
             OR (p_outcome = 'retry'
                 AND (p_reason_code IS NULL OR p_retry_seconds IS NULL
                      OR p_retry_seconds < 1 OR p_retry_seconds > 3600))
             OR (p_outcome = 'blocked'
                 AND (p_reason_code IS NULL OR p_retry_seconds IS NOT NULL)) THEN
            RAISE EXCEPTION 'MATERIAL_INGESTION_DELIVERY_FINISH_INVALID';
          END IF;

          UPDATE f1.{_TABLE} AS delivery
             SET state = CASE p_outcome
                           WHEN 'done' THEN 'done'
                           WHEN 'retry' THEN 'retry_wait'
                           ELSE 'blocked'
                         END,
                 dispatch_token = NULL,
                 dispatch_lease_until = NULL,
                 next_attempt_at = CASE WHEN p_outcome = 'retry'
                   THEN statement_timestamp()
                        + make_interval(secs => p_retry_seconds)
                   ELSE NULL END,
                 reason_code = CASE WHEN p_outcome = 'done'
                   THEN NULL ELSE p_reason_code END,
                 completed_at = CASE WHEN p_outcome IN ('done','blocked')
                   THEN statement_timestamp() ELSE NULL END,
                 updated_at = statement_timestamp()
           WHERE delivery.id = p_delivery_id
             AND delivery.state = 'dispatched'
             AND delivery.dispatch_token = p_dispatch_token
             AND delivery.dispatch_lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count = 1;
        END
        $$
        """
    )
    for signature in (
        "f1.register_material_ingestion_delivery(uuid,uuid,text,boolean)",
        "f1.claim_material_ingestion_deliveries(integer,integer)",
        "f1.read_material_ingestion_delivery_claim(uuid,uuid)",
        "f1.finish_material_ingestion_delivery(uuid,uuid,text,text,integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.register_material_ingestion_delivery(uuid,uuid,text,boolean) "
        "TO f1_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.claim_material_ingestion_deliveries(integer,integer) TO f1_worker"
    )
    for signature in (
        "f1.read_material_ingestion_delivery_claim(uuid,uuid)",
        "f1.finish_material_ingestion_delivery(uuid,uuid,text,text,integer)",
    ):
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api,f1_worker"
        )


def _manager_access(alias: str) -> str:
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


def _rls_and_grants() -> None:
    access = _manager_access(_TABLE)
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {_DEFINER_ROLE}")
    op.execute(
        "CREATE POLICY material_ingestion_register_profile_select "
        "ON f1.user_profile FOR SELECT TO "
        f"{_DEFINER_ROLE} USING (session_user='f1_api' "
        "AND keycloak_sub=NULLIF(current_setting('f1.sub',true),''))"
    )
    op.execute(
        "CREATE POLICY material_ingestion_register_membership_select "
        "ON f1.enterprise_user FOR SELECT TO "
        f"{_DEFINER_ROLE} USING (session_user='f1_api' "
        "AND enterprise_id=NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
        "AND EXISTS (SELECT 1 FROM f1.user_profile AS profile "
        "WHERE profile.id=enterprise_user.user_id "
        "AND profile.keycloak_sub=NULLIF(current_setting('f1.sub',true),'')))"
    )
    for table in (
        "document_version",
        "document_record",
        "material_knowledge_scope",
        "crm_account",
    ):
        op.execute(
            f"CREATE POLICY material_ingestion_register_{table}_select "
            f"ON f1.{table} FOR SELECT TO {_DEFINER_ROLE} USING ("
            "session_user='f1_api' AND "
            "enterprise_id=NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
            "AND f1.session_authorized(enterprise_id))"
        )
    op.execute(
        f"GRANT SELECT (id,keycloak_sub) ON f1.user_profile TO {_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,user_id,role) ON f1.enterprise_user "
        f"TO {_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,id,document_record_id) "
        f"ON f1.document_version TO {_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,id,knowledge_scope_id) "
        f"ON f1.document_record TO {_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,id,scope_kind,client_account_id) "
        f"ON f1.material_knowledge_scope TO {_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,id,owner_user_id) "
        f"ON f1.crm_account TO {_DEFINER_ROLE}"
    )
    op.execute(f"ALTER TABLE f1.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE f1.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY material_ingestion_delivery_api_select ON f1.{_TABLE} "
        f"FOR SELECT TO f1_api USING ({access})"
    )
    op.execute(
        f"CREATE POLICY material_ingestion_delivery_definer_all ON f1.{_TABLE} "
        f"FOR ALL TO {_DEFINER_ROLE} "
        "USING (session_user IN ('f1_api','f1_worker')) "
        "WITH CHECK (session_user IN ('f1_api','f1_worker'))"
    )
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM PUBLIC,f1_worker")
    op.execute(
        f"GRANT SELECT (id,enterprise_id,document_version_id,delivery_kind,"
        f"actor_sub,state,attempt,reason_code) ON f1.{_TABLE} TO f1_api"
    )
    op.execute(
        f"GRANT SELECT,INSERT,UPDATE ON f1.{_TABLE} TO {_DEFINER_ROLE}"
    )


def _backfill_eligible_latest_versions() -> None:
    """Create one body-free recovery record for each stranded latest version.

    Existing controlled-ingestion tables use FORCE RLS and intentionally have
    no cross-tenant runtime reader.  As in the f1_0013/f1_0014 bounded
    backfills, temporarily return to the already-validated bootstrap session,
    insert only the exact latest-version recovery set, and immediately restore
    the migration role.  This grants no runtime or definer bypass.
    """
    op.execute("RESET ROLE")
    op.execute(
        f"""
        INSERT INTO f1.{_TABLE} (
          id,enterprise_id,document_version_id,delivery_kind,actor_sub,state,
          attempt,reason_code,completed_at
        )
        SELECT
          md5(
            'material-ingestion:resume:' || version.enterprise_id::text || ':' ||
            version.id::text
          )::uuid,
          version.enterprise_id,
          version.id,
          'resume',
          COALESCE(actor.keycloak_sub,'migration:actor-rebind-required'),
          CASE WHEN actor.keycloak_sub IS NULL OR (
            current_analysis.status='confirmed'
            AND current_analysis.ocr_required IS TRUE
          ) THEN 'blocked' ELSE 'pending' END,
          0,
          CASE
            WHEN current_analysis.status='confirmed'
                 AND current_analysis.ocr_required IS TRUE
              THEN 'MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED'
            WHEN actor.keycloak_sub IS NULL
              THEN 'MATERIAL_INGESTION_ACTOR_REBIND_REQUIRED'
            ELSE NULL
          END,
          CASE WHEN actor.keycloak_sub IS NULL OR (
            current_analysis.status='confirmed'
            AND current_analysis.ocr_required IS TRUE
          )
            THEN statement_timestamp() ELSE NULL END
        FROM f1.document_version AS version
        JOIN f1.document_record AS record
          ON record.enterprise_id=version.enterprise_id
         AND record.id=version.document_record_id
         AND record.latest_version_no=version.version_no
        JOIN f1.material_knowledge_scope AS scope
          ON scope.enterprise_id=record.enterprise_id
         AND scope.id=record.knowledge_scope_id
        JOIN f1.upload_task AS task
          ON task.enterprise_id=version.enterprise_id
         AND task.id=version.upload_task_id
        JOIN f1.document AS source
          ON source.enterprise_id=version.enterprise_id
         AND source.id=version.source_document_id
        LEFT JOIN LATERAL (
          SELECT analysis.id,analysis.status,analysis.source_sha256,
                 EXISTS (
                   SELECT 1 FROM f1.material_page_classification AS page
                   WHERE page.enterprise_id=analysis.enterprise_id
                     AND page.analysis_id=analysis.id
                     AND page.ocr_required IS TRUE
                 ) AS ocr_required
          FROM f1.material_analysis AS analysis
          WHERE analysis.enterprise_id=version.enterprise_id
            AND analysis.document_version_id=version.id
            AND analysis.analysis_version='material-v1'
          ORDER BY analysis.analysis_revision DESC,analysis.id DESC
          LIMIT 1
        ) AS current_analysis ON TRUE
        LEFT JOIN LATERAL (
          SELECT profile.keycloak_sub
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=version.enterprise_id
            AND membership.user_id=version.created_by_user_id
            AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
            AND profile.keycloak_sub=btrim(profile.keycloak_sub)
            AND profile.keycloak_sub !~ '[[:cntrl:]]'
            AND membership.role IN (
              'super_admin','enterprise_admin','plant_admin'
            )
            AND (
              scope.scope_kind='service_provider'
              OR membership.role IN ('super_admin','enterprise_admin')
              OR (
                membership.role='plant_admin'
                AND EXISTS (
                  SELECT 1 FROM f1.crm_account AS account
                  WHERE account.enterprise_id=scope.enterprise_id
                    AND account.id=scope.client_account_id
                    AND account.owner_user_id=membership.user_id
                )
              )
            )
          LIMIT 1
        ) AS actor ON TRUE
        WHERE task.pipeline_kind='controlled_ingestion'
          AND record.status='active'
          AND (
            (
              task.object_state='quarantined'
              AND task.quarantine_status='held'
              AND task.processing_stage IN (
                'received','scanning','validating','previewing','retry_wait'
              )
            )
            OR (
              task.object_state='ready'
              AND task.processing_stage='ready'
              AND task.scan_verdict='clean'
              AND task.preview_status='ready'
              AND source.content_type='application/pdf'
              AND (
                task.error_reason IN (
                  'OCR_DISABLED','OCR_UNAVAILABLE','OCR_PAGE_LIMIT',
                  'OCR_OUTPUT_INSUFFICIENT','OCR_REQUIRED',
                  'MATERIAL_ANALYSIS_FAILED','MATERIAL_SOURCE_READ_FAILED',
                  'MATERIAL_ANALYSIS_PERSIST_FAILED'
                )
                OR current_analysis.id IS NULL
                OR current_analysis.source_sha256<>task.content_sha256
                OR current_analysis.status NOT IN ('ready','confirmed')
                OR current_analysis.ocr_required IS TRUE
              )
            )
          )
        ON CONFLICT ON CONSTRAINT material_ingestion_delivery_identity_uq
        DO NOTHING
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")


def _ready_ocr_successor_boundary() -> None:
    """Permit a successor only for failed or machine-ready OCR debt.

    A confirmed snapshot remains a human-authored terminal fact.  It is never
    silently superseded by automated recovery.
    """
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
             OR (
               predecessor.status <> 'failed'
               AND NOT (
                 predecessor.status = 'ready'
                 AND EXISTS (
                   SELECT 1
                   FROM f1.material_page_classification AS page
                   WHERE page.enterprise_id = predecessor.enterprise_id
                     AND page.analysis_id = predecessor.id
                     AND page.ocr_required IS TRUE
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


def _material_analysis_api_update_boundary() -> None:
    """Limit tenant API writes to the two guarded business transitions."""
    op.execute("REVOKE UPDATE ON f1.material_analysis FROM f1_api")
    op.execute(
        "GRANT UPDATE (resolved_kind,classification_source,"
        "classification_by_user_id,classification_at,updated_at,status,"
        "confirmed_by_user_id,confirmed_at,policy_source_id,policy_version_id,"
        "confirmation_key_sha256,confirmation_payload_sha256) "
        "ON f1.material_analysis TO f1_api"
    )


def _backfill_material_pipeline_deliveries() -> None:
    """Durably hand legacy analyzed latest PDFs to idempotent reconciliation."""
    op.execute("RESET ROLE")
    op.execute(
        """
        INSERT INTO f1.material_pipeline_delivery (
          id,enterprise_id,document_version_id,delivery_kind,actor_sub,state,
          attempt,reason_code,completed_at
        )
        SELECT
          md5(
            'material-pipeline:advance:' || version.enterprise_id::text || ':' ||
            version.id::text
          )::uuid,
          version.enterprise_id,
          version.id,
          'advance',
          COALESCE(actor.keycloak_sub,'migration:pipeline-actor-rebind-required'),
          CASE WHEN actor.keycloak_sub IS NULL THEN 'blocked' ELSE 'pending' END,
          0,
          CASE WHEN actor.keycloak_sub IS NULL
            THEN 'MATERIAL_PIPELINE_ACTOR_REBIND_REQUIRED' ELSE NULL END,
          CASE WHEN actor.keycloak_sub IS NULL
            THEN statement_timestamp() ELSE NULL END
        FROM f1.document_version AS version
        JOIN f1.document_record AS record
          ON record.enterprise_id=version.enterprise_id
         AND record.id=version.document_record_id
         AND record.latest_version_no=version.version_no
        JOIN f1.upload_task AS task
          ON task.enterprise_id=version.enterprise_id
         AND task.id=version.upload_task_id
        JOIN f1.document AS source
          ON source.enterprise_id=version.enterprise_id
         AND source.id=version.source_document_id
        JOIN LATERAL (
          SELECT analysis.id,analysis.enterprise_id,analysis.status,
                 analysis.source_sha256
          FROM f1.material_analysis AS analysis
          WHERE analysis.enterprise_id=version.enterprise_id
            AND analysis.document_version_id=version.id
            AND analysis.analysis_version='material-v1'
          ORDER BY analysis.analysis_revision DESC,analysis.id DESC
          LIMIT 1
        ) AS current_analysis ON TRUE
        LEFT JOIN LATERAL (
          SELECT profile.keycloak_sub
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=version.enterprise_id
            AND membership.user_id=version.created_by_user_id
            AND membership.role IN ('super_admin','enterprise_admin')
            AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
            AND profile.keycloak_sub=btrim(profile.keycloak_sub)
            AND profile.keycloak_sub !~ '[[:cntrl:]]'
          LIMIT 1
        ) AS actor ON TRUE
        WHERE record.status='active'
          AND task.pipeline_kind='controlled_ingestion'
          AND task.status='done'
          AND task.processing_stage='ready'
          AND task.object_state='ready'
          AND task.scan_verdict='clean'
          AND task.preview_status='ready'
          AND source.content_type='application/pdf'
          AND current_analysis.source_sha256=task.content_sha256
          AND current_analysis.status IN ('ready','confirmed')
          AND NOT EXISTS (
            SELECT 1 FROM f1.material_page_classification AS page
            WHERE page.enterprise_id=current_analysis.enterprise_id
              AND page.analysis_id=current_analysis.id
              AND page.ocr_required IS TRUE
          )
        ON CONFLICT ON CONSTRAINT material_pipeline_delivery_identity_uq
        DO NOTHING
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")


def _ocr_purge_boundary() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.purge_expired_material_ocr_checkpoints(
          p_limit integer
        ) RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_limit IS NULL OR p_limit < 1 OR p_limit > 1000 THEN
            RAISE EXCEPTION 'MATERIAL_OCR_PURGE_INVALID';
          END IF;
          WITH candidates AS MATERIALIZED (
            SELECT checkpoint.id
            FROM f1.material_ocr_checkpoint AS checkpoint
            WHERE checkpoint.expires_at <= statement_timestamp()
            ORDER BY checkpoint.expires_at,checkpoint.id
            LIMIT p_limit
          )
          DELETE FROM f1.material_ocr_checkpoint AS checkpoint
          USING candidates
          WHERE checkpoint.id = candidates.id;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          RETURN v_count;
        END
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f1.purge_expired_material_ocr_checkpoints(integer) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.purge_expired_material_ocr_checkpoints(integer) TO f1_worker"
    )
    op.execute(
        f"CREATE POLICY material_ocr_checkpoint_ingestion_definer_select "
        "ON f1.material_ocr_checkpoint FOR SELECT "
        f"TO {_DEFINER_ROLE} USING ("
        "session_user='f1_worker' AND expires_at<=statement_timestamp())"
    )
    op.execute(
        f"CREATE POLICY material_ocr_checkpoint_ingestion_definer_delete "
        "ON f1.material_ocr_checkpoint FOR DELETE "
        f"TO {_DEFINER_ROLE} USING ("
        "session_user='f1_worker' AND expires_at<=statement_timestamp())"
    )
    op.execute(
        "GRANT SELECT (id,expires_at),DELETE ON "
        f"f1.material_ocr_checkpoint TO {_DEFINER_ROLE}"
    )


def _analysis_report_api_write_boundary() -> None:
    """Constrain the shared API role to the report service's legal writes."""
    for table in (
        "analysis_report",
        "analysis_report_version",
        "analysis_report_generation_job",
    ):
        op.execute(f"REVOKE INSERT,UPDATE ON f1.{table} FROM f1_api")
    op.execute(
        "GRANT INSERT (id,enterprise_id,client_account_id,template_id,title,"
        "current_version_id,current_version_no,create_request_id,created_by_user_id),"
        "UPDATE (current_version_id,current_version_no,client_visible,updated_at) "
        "ON f1.analysis_report TO f1_api"
    )
    op.execute(
        "GRANT INSERT (id,enterprise_id,report_id,client_account_id,version_number,"
        "status,source_fingerprint_sha256,artifact_ready,created_by_user_id),"
        "UPDATE (status,artifact_ready,published_at,updated_at) "
        "ON f1.analysis_report_version TO f1_api"
    )
    op.execute(
        "GRANT INSERT (id,enterprise_id,report_id,version_id,request_id,status,"
        "source_fingerprint_sha256,lease_token,lease_until,lease_owner),"
        "UPDATE (status,lease_token,lease_until,lease_owner,error_reason,updated_at) "
        "ON f1.analysis_report_generation_job TO f1_api"
    )
    for table, columns in (
        (
            "analysis_report_section",
            "id,enterprise_id,version_id,section_key,title,body,ordinal",
        ),
        (
            "analysis_report_citation",
            "id,enterprise_id,version_id,document_version_id,document_name,"
            "version_number,page_number,excerpt,ordinal",
        ),
    ):
        op.execute(f"REVOKE INSERT,DELETE ON f1.{table} FROM f1_api")
        op.execute(f"GRANT INSERT ({columns}),DELETE ON f1.{table} TO f1_api")
    for table, columns in (
        (
            "analysis_report_audit_event",
            "id,enterprise_id,report_id,version_id,actor_user_id,action,"
            "from_status,to_status",
        ),
        (
            "analysis_report_review_event",
            "id,enterprise_id,report_id,version_id,actor_user_id,action,"
            "checklist,comment",
        ),
        (
            "analysis_report_health_snapshot",
            "id,enterprise_id,report_id,version_id,client_account_id,payload,"
            "payload_sha256,score,max_score",
        ),
    ):
        op.execute(f"REVOKE INSERT ON f1.{table} FROM f1_api")
        op.execute(f"GRANT INSERT ({columns}) ON f1.{table} TO f1_api")

    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_write()
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
    op.execute(
        "CREATE TRIGGER analysis_report_write_guard "
        "BEFORE INSERT OR UPDATE ON f1.analysis_report FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_write()"
    )

    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_version_write()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_actor_id uuid;
        BEGIN
          IF TG_OP='INSERT' THEN
            SELECT membership.user_id INTO v_actor_id
            FROM f1.enterprise_user AS membership
            JOIN f1.user_profile AS profile ON profile.id=membership.user_id
            WHERE membership.enterprise_id=NEW.enterprise_id
              AND profile.keycloak_sub=f1.current_sub()
              AND membership.role IN ('super_admin','enterprise_admin');
            IF v_actor_id IS NULL
               OR NEW.created_by_user_id IS DISTINCT FROM v_actor_id
               OR NEW.status<>'queued' OR NEW.artifact_ready IS TRUE
               OR NEW.published_at IS NOT NULL OR NOT EXISTS (
                 SELECT 1 FROM f1.analysis_report AS report
                 WHERE report.enterprise_id=NEW.enterprise_id
                   AND report.id=NEW.report_id
                   AND report.client_account_id=NEW.client_account_id
                   AND NEW.version_number=report.current_version_no+1
               ) THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_INSERT_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
             OR NEW.report_id IS DISTINCT FROM OLD.report_id
             OR NEW.client_account_id IS DISTINCT FROM OLD.client_account_id
             OR NEW.version_number IS DISTINCT FROM OLD.version_number
             OR NEW.source_fingerprint_sha256 IS DISTINCT FROM
                OLD.source_fingerprint_sha256
             OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.updated_at IS DISTINCT FROM statement_timestamp() THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_IDENTITY_IMMUTABLE';
          END IF;

          IF current_user='f1_analysis_report_definer' THEN
            IF OLD.status NOT IN ('queued','generating') OR NEW.status<>'failed'
               OR NEW.artifact_ready IS DISTINCT FROM OLD.artifact_ready
               OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_REVOKED_TRANSITION_INVALID';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.status IN ('queued','generating','draft','review_pending',
                            'changes_requested','approved','failed')
             AND (NEW.artifact_ready IS TRUE OR NEW.published_at IS NOT NULL) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_ARTIFACT_INVALID';
          END IF;
          IF NEW.status NOT IN ('published','superseded','withdrawn')
             AND (NEW.artifact_ready IS DISTINCT FROM OLD.artifact_ready
                  OR NEW.published_at IS DISTINCT FROM OLD.published_at) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_ARTIFACT_INVALID';
          END IF;

          IF OLD.status='queued' AND NEW.status='generating'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='generating'
                 AND job.lease_token IS NOT NULL
                 AND job.lease_until>statement_timestamp()
             ) THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='generating'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='generating'
                 AND job.lease_token IS NOT NULL
                 AND job.lease_until>statement_timestamp()
             ) THEN RETURN NEW; END IF;
          IF OLD.status='queued' AND NEW.status='failed'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='failed'
             ) THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='failed'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id
                 AND ((job.status='generating' AND job.lease_token IS NOT NULL
                       AND job.lease_until>statement_timestamp())
                      OR job.status='failed')
             ) THEN RETURN NEW; END IF;
          IF OLD.status='failed' AND NEW.status='queued'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='failed'
                 AND job.error_reason IN (
                   'REPORT_QUEUE_DISPATCH_FAILED',
                   'REPORT_GENERATION_RETRIES_EXHAUSTED',
                   'REPORT_WORKER_GENERATION_DISABLED','REPORT_ACTOR_REVOKED'
                 )
             ) THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='queued'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='queued'
                 AND job.lease_token IS NULL AND job.lease_until IS NULL
                 AND job.lease_owner IS NULL
             ) THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='draft'
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_generation_job AS job
               WHERE job.enterprise_id=NEW.enterprise_id
                 AND job.version_id=NEW.id AND job.status='draft'
             )
             AND (SELECT count(*) FROM f1.analysis_report_section AS section
                  WHERE section.enterprise_id=NEW.enterprise_id
                    AND section.version_id=NEW.id)=7
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_citation AS citation
               WHERE citation.enterprise_id=NEW.enterprise_id
                 AND citation.version_id=NEW.id
             ) THEN RETURN NEW; END IF;
          IF (OLD.status,NEW.status) IN (
               ('draft','review_pending'),
               ('review_pending','changes_requested'),
               ('review_pending','approved')
             ) AND NOT (NEW.artifact_ready IS DISTINCT FROM OLD.artifact_ready)
             AND NOT (NEW.published_at IS DISTINCT FROM OLD.published_at)
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report AS report
               WHERE report.enterprise_id=NEW.enterprise_id
                 AND report.id=NEW.report_id
                 AND report.current_version_id=NEW.id
             ) THEN RETURN NEW; END IF;
          IF OLD.status='approved' AND NEW.status='published'
             AND NEW.artifact_ready IS TRUE
             AND NEW.published_at IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report AS report
               JOIN f1.analysis_report_client_audience AS binding
                 ON binding.enterprise_id=report.enterprise_id
                AND binding.client_account_id=report.client_account_id
                AND binding.status='active'
               WHERE report.enterprise_id=NEW.enterprise_id
                 AND report.id=NEW.report_id
                 AND report.current_version_id=NEW.id
             )
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_review_event AS review
               WHERE review.enterprise_id=NEW.enterprise_id
                 AND review.version_id=NEW.id AND review.action='approve'
             )
             AND (SELECT count(*) FROM f1.analysis_report_section AS section
                  WHERE section.enterprise_id=NEW.enterprise_id
                    AND section.version_id=NEW.id)=7
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_citation AS citation
               WHERE citation.enterprise_id=NEW.enterprise_id
                 AND citation.version_id=NEW.id
             ) THEN RETURN NEW; END IF;
          IF OLD.status='published' AND NEW.status='superseded'
             AND NOT (NEW.artifact_ready IS DISTINCT FROM OLD.artifact_ready)
             AND NOT (NEW.published_at IS DISTINCT FROM OLD.published_at)
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_version AS replacement
               WHERE replacement.enterprise_id=NEW.enterprise_id
                 AND replacement.report_id=NEW.report_id
                 AND replacement.id<>NEW.id AND replacement.status='published'
             ) THEN RETURN NEW; END IF;
          IF OLD.status='published' AND NEW.status='withdrawn'
             AND NOT (NEW.artifact_ready IS DISTINCT FROM OLD.artifact_ready)
             AND NOT (NEW.published_at IS DISTINCT FROM OLD.published_at)
             THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'ANALYSIS_REPORT_VERSION_TRANSITION_INVALID';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_version_write_guard "
        "BEFORE INSERT OR UPDATE ON f1.analysis_report_version FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_version_write()"
    )

    _analysis_report_job_write_boundary()
    _analysis_report_evidence_write_boundary()


def _analysis_report_job_write_boundary() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_job_write()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'queued' OR NEW.lease_token IS NOT NULL
               OR NEW.lease_until IS NOT NULL OR NEW.lease_owner IS NOT NULL
               OR NEW.error_reason IS NOT NULL OR NOT EXISTS (
                 SELECT 1 FROM f1.analysis_report_version AS version
                 WHERE version.enterprise_id=NEW.enterprise_id
                   AND version.report_id=NEW.report_id
                   AND version.id=NEW.version_id AND version.status='queued'
                   AND version.source_fingerprint_sha256=
                       NEW.source_fingerprint_sha256
               ) OR EXISTS (
                 SELECT 1 FROM f1.analysis_report_generation_job AS existing
                 WHERE existing.enterprise_id=NEW.enterprise_id
                   AND existing.version_id=NEW.version_id
               ) THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_JOB_INSERT_INVALID';
            END IF;
            RETURN NEW;
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.enterprise_id IS DISTINCT FROM OLD.enterprise_id
             OR NEW.report_id IS DISTINCT FROM OLD.report_id
             OR NEW.version_id IS DISTINCT FROM OLD.version_id
             OR NEW.request_id IS DISTINCT FROM OLD.request_id
             OR NEW.source_fingerprint_sha256 IS DISTINCT FROM
                OLD.source_fingerprint_sha256
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.updated_at IS DISTINCT FROM statement_timestamp() THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_JOB_IDENTITY_IMMUTABLE';
          END IF;
          IF current_user='f1_analysis_report_definer' THEN
            IF OLD.status NOT IN ('queued','generating') OR NEW.status<>'failed'
               OR NEW.error_reason<>'REPORT_ACTOR_REVOKED'
               OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL
               OR NEW.lease_owner IS NOT NULL THEN
              RAISE EXCEPTION 'ANALYSIS_REPORT_REVOKED_JOB_INVALID';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.status='queued' AND NEW.status='generating'
             AND OLD.lease_token IS NULL AND OLD.lease_until IS NULL
             AND OLD.lease_owner IS NULL AND OLD.error_reason IS NULL
             AND NEW.lease_token IS NOT NULL AND NEW.lease_until>statement_timestamp()
             AND NEW.lease_owner IS NOT NULL AND NEW.error_reason IS NULL
             THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='generating'
             AND OLD.lease_until<=statement_timestamp()
             AND NEW.lease_token IS NOT NULL
             AND NEW.lease_token IS DISTINCT FROM OLD.lease_token
             AND NEW.lease_until>statement_timestamp()
             AND NEW.lease_owner IS NOT NULL AND NEW.error_reason IS NULL
             THEN RETURN NEW; END IF;
          IF OLD.status='queued' AND NEW.status='failed'
             AND NEW.error_reason IS NOT NULL
             AND NEW.lease_token IS NULL AND NEW.lease_until IS NULL
             AND NEW.lease_owner IS NULL THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='failed'
             AND OLD.lease_token IS NOT NULL
             AND OLD.lease_until>statement_timestamp()
             AND NEW.error_reason IS NOT NULL
             AND NEW.lease_token IS NULL AND NEW.lease_until IS NULL
             AND NEW.lease_owner IS NULL THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='queued'
             AND OLD.lease_token IS NOT NULL
             AND OLD.lease_until>statement_timestamp()
             AND NEW.error_reason IS NULL
             AND NEW.lease_token IS NULL AND NEW.lease_until IS NULL
             AND NEW.lease_owner IS NULL THEN RETURN NEW; END IF;
          IF OLD.status='failed' AND NEW.status='queued'
             AND OLD.error_reason IN (
               'REPORT_QUEUE_DISPATCH_FAILED',
               'REPORT_GENERATION_RETRIES_EXHAUSTED',
               'REPORT_WORKER_GENERATION_DISABLED','REPORT_ACTOR_REVOKED'
             ) AND NEW.error_reason IS NULL
             AND NEW.lease_token IS NULL AND NEW.lease_until IS NULL
             AND NEW.lease_owner IS NULL THEN RETURN NEW; END IF;
          IF OLD.status='generating' AND NEW.status='draft'
             AND OLD.lease_token IS NOT NULL
             AND OLD.lease_until>statement_timestamp()
             AND NEW.lease_token IS NOT DISTINCT FROM OLD.lease_token
             AND NEW.lease_until IS NOT DISTINCT FROM OLD.lease_until
             AND NEW.lease_owner IS NOT DISTINCT FROM OLD.lease_owner
             AND NEW.error_reason IS NULL
             AND (SELECT count(*) FROM f1.analysis_report_section AS section
                  WHERE section.enterprise_id=NEW.enterprise_id
                    AND section.version_id=NEW.version_id)=7
             AND EXISTS (
               SELECT 1 FROM f1.analysis_report_citation AS citation
               WHERE citation.enterprise_id=NEW.enterprise_id
                 AND citation.version_id=NEW.version_id
             ) THEN RETURN NEW; END IF;
          RAISE EXCEPTION 'ANALYSIS_REPORT_JOB_TRANSITION_INVALID';
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_job_write_guard "
        "BEFORE INSERT OR UPDATE ON f1.analysis_report_generation_job "
        "FOR EACH ROW EXECUTE FUNCTION f1.guard_analysis_report_job_write()"
    )


def _analysis_report_evidence_write_boundary() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_content_write()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_enterprise_id uuid; v_version_id uuid;
        BEGIN
          IF TG_OP='DELETE' THEN
            v_enterprise_id:=OLD.enterprise_id; v_version_id:=OLD.version_id;
          ELSE
            v_enterprise_id:=NEW.enterprise_id; v_version_id:=NEW.version_id;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM f1.analysis_report_version AS version
            JOIN f1.analysis_report_generation_job AS job
              ON job.enterprise_id=version.enterprise_id
             AND job.version_id=version.id
            WHERE version.enterprise_id=v_enterprise_id
              AND version.id=v_version_id AND version.status='generating'
              AND job.status='generating'
              AND (TG_OP='DELETE' OR (
                job.lease_token IS NOT NULL
                AND job.lease_until>statement_timestamp()
              ))
          ) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_CONTENT_WRITE_INVALID';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END
        $$
        """
    )
    for table in ("analysis_report_section", "analysis_report_citation"):
        op.execute(
            f"CREATE TRIGGER {table}_write_guard BEFORE INSERT OR DELETE "
            f"ON f1.{table} FOR EACH ROW "
            "EXECUTE FUNCTION f1.guard_analysis_report_content_write()"
        )

    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_audit_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_actor_id uuid; v_expected_status text;
        BEGIN
          IF current_user='f1_analysis_report_definer' THEN
            IF NEW.action='actor_revoked' THEN
              IF NEW.to_status<>'failed'
                 OR NEW.from_status NOT IN ('queued','generating')
                 OR NOT EXISTS (
                   SELECT 1 FROM f1.analysis_report_version AS version
                   WHERE version.enterprise_id=NEW.enterprise_id
                     AND version.report_id=NEW.report_id
                     AND version.id=NEW.version_id
                     AND version.status='failed'
                     AND EXISTS (
                       SELECT 1
                       FROM f1.analysis_report_generation_delivery AS delivery
                       JOIN f1.user_profile AS profile
                         ON profile.keycloak_sub=delivery.actor_sub
                       WHERE delivery.enterprise_id=NEW.enterprise_id
                         AND delivery.report_id=NEW.report_id
                         AND delivery.version_id=NEW.version_id
                         AND profile.id=NEW.actor_user_id
                     )
                 ) THEN
                RAISE EXCEPTION 'ANALYSIS_REPORT_AUDIT_INVALID';
              END IF;
            ELSIF NEW.action='actor_rebound' THEN
              IF session_user<>'f1_api'
                 OR NULLIF(current_setting('f1.enterprise_id',true),'')::uuid
                    IS DISTINCT FROM NEW.enterprise_id
                 OR NEW.from_status<>'blocked'
                 OR NEW.to_status NOT IN ('queued','generating')
                 OR NOT f1.session_authorized(NEW.enterprise_id)
                 OR NOT EXISTS (
                   SELECT 1 FROM f1.enterprise_user AS membership
                   JOIN f1.user_profile AS profile
                     ON profile.id=membership.user_id
                   WHERE membership.enterprise_id=NEW.enterprise_id
                     AND membership.user_id=NEW.actor_user_id
                     AND membership.role IN ('super_admin','enterprise_admin')
                     AND profile.keycloak_sub=
                       NULLIF(current_setting('f1.sub',true),'')
                 )
                 OR NOT EXISTS (
                   SELECT 1
                   FROM f1.analysis_report_generation_delivery AS delivery
                   JOIN f1.analysis_report_generation_job AS job
                     ON job.enterprise_id=delivery.enterprise_id
                    AND job.id=delivery.job_id
                    AND job.report_id=delivery.report_id
                    AND job.version_id=delivery.version_id
                   JOIN f1.analysis_report_version AS version
                     ON version.enterprise_id=job.enterprise_id
                    AND version.report_id=job.report_id
                    AND version.id=job.version_id
                   JOIN f1.analysis_report AS report
                     ON report.enterprise_id=job.enterprise_id
                    AND report.id=job.report_id
                   WHERE delivery.enterprise_id=NEW.enterprise_id
                     AND delivery.report_id=NEW.report_id
                     AND delivery.version_id=NEW.version_id
                     AND delivery.state='blocked'
                     AND delivery.reason_code='REPORT_ACTOR_REBIND_REQUIRED'
                     AND job.status=NEW.to_status
                     AND version.status=NEW.to_status
                     AND report.current_version_id=NEW.version_id
                 ) THEN
                RAISE EXCEPTION 'ANALYSIS_REPORT_AUDIT_INVALID';
              END IF;
            ELSE
              RAISE EXCEPTION 'ANALYSIS_REPORT_AUDIT_INVALID';
            END IF;
            RETURN NEW;
          END IF;
          SELECT membership.user_id INTO v_actor_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND membership.role IN ('super_admin','enterprise_admin');
          v_expected_status:=CASE NEW.action
            WHEN 'generate' THEN 'queued'
            WHEN 'redispatch' THEN 'queued'
            WHEN 'submit' THEN 'review_pending'
            WHEN 'return' THEN 'changes_requested'
            WHEN 'approve' THEN 'approved'
            WHEN 'publish' THEN 'published'
            WHEN 'withdraw' THEN 'withdrawn'
            WHEN 'health_snapshot_created' THEN 'published'
            ELSE NULL END;
          IF v_actor_id IS NULL OR NEW.actor_user_id IS DISTINCT FROM v_actor_id
             OR NEW.version_id IS NULL OR v_expected_status IS NULL
             OR (NEW.action='generate'
                 AND NEW.from_status NOT IN ('empty','prior'))
             OR (NEW.action='redispatch'
                 AND (NEW.from_status<>'failed' OR NEW.to_status<>'queued'))
             OR (NEW.action='submit'
                 AND (NEW.from_status<>'draft'
                      OR NEW.to_status<>'review_pending'))
             OR (NEW.action='return'
                 AND (NEW.from_status<>'review_pending'
                      OR NEW.to_status<>'changes_requested'))
             OR (NEW.action='approve'
                 AND (NEW.from_status<>'review_pending'
                      OR NEW.to_status<>'approved'))
             OR (NEW.action IN ('publish','health_snapshot_created')
                 AND (NEW.from_status<>'approved'
                      OR NEW.to_status<>'published'))
             OR (NEW.action='withdraw'
                 AND (NEW.from_status<>'published'
                      OR NEW.to_status<>'withdrawn'))
             OR NEW.to_status<>v_expected_status
             OR NOT EXISTS (
               SELECT 1 FROM f1.analysis_report_version AS version
               WHERE version.enterprise_id=NEW.enterprise_id
                 AND version.report_id=NEW.report_id
                 AND version.id=NEW.version_id
                 AND version.status=v_expected_status
                 AND version.updated_at>=transaction_timestamp()
             ) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_AUDIT_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_audit_insert_guard "
        "BEFORE INSERT ON f1.analysis_report_audit_event FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_audit_insert()"
    )

    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_review_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        DECLARE v_actor_id uuid; v_expected_status text;
        BEGIN
          SELECT membership.user_id INTO v_actor_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=NEW.enterprise_id
            AND profile.keycloak_sub=f1.current_sub()
            AND membership.role IN ('super_admin','enterprise_admin');
          v_expected_status:=CASE NEW.action
            WHEN 'submit' THEN 'review_pending'
            WHEN 'return' THEN 'changes_requested'
            WHEN 'approve' THEN 'approved'
            ELSE NULL END;
          IF v_actor_id IS NULL OR NEW.actor_user_id IS DISTINCT FROM v_actor_id
             OR v_expected_status IS NULL OR NOT EXISTS (
               SELECT 1 FROM f1.analysis_report_version AS version
               JOIN f1.analysis_report AS report
                 ON report.enterprise_id=version.enterprise_id
                AND report.id=version.report_id
               WHERE version.enterprise_id=NEW.enterprise_id
                 AND version.report_id=NEW.report_id
                 AND version.id=NEW.version_id
                 AND version.status=v_expected_status
                 AND version.updated_at>=transaction_timestamp()
                 AND report.current_version_id=version.id
             ) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_REVIEW_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_review_insert_guard "
        "BEFORE INSERT ON f1.analysis_report_review_event FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_review_insert()"
    )
    op.execute(
        """
        CREATE FUNCTION f1.guard_analysis_report_health_insert()
        RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER
        SET search_path = pg_catalog AS $$
        BEGIN
          IF NEW.payload->>'report_id'<>NEW.report_id::text
             OR NEW.payload->>'version_id'<>NEW.version_id::text
             OR NEW.payload->>'report_title'<>'企业安环资料分析报告'
             OR (NEW.payload->>'score')::integer<>NEW.score
             OR (NEW.payload->>'max_score')::integer<>NEW.max_score
             OR NOT EXISTS (
               SELECT 1 FROM f1.analysis_report_version AS version
               JOIN f1.analysis_report AS report
                 ON report.enterprise_id=version.enterprise_id
                AND report.id=version.report_id
                AND report.client_account_id=version.client_account_id
               WHERE version.enterprise_id=NEW.enterprise_id
                 AND version.report_id=NEW.report_id
                 AND version.id=NEW.version_id
                 AND version.client_account_id=NEW.client_account_id
                 AND version.status='published'
                 AND version.artifact_ready IS TRUE
                 AND version.updated_at>=transaction_timestamp()
                 AND report.current_version_id=version.id
                 AND (NEW.payload->>'version_number')::integer=
                     version.version_number
             ) THEN
            RAISE EXCEPTION 'ANALYSIS_REPORT_HEALTH_SNAPSHOT_INVALID';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER analysis_report_health_insert_guard "
        "BEFORE INSERT ON f1.analysis_report_health_snapshot FOR EACH ROW "
        "EXECUTE FUNCTION f1.guard_analysis_report_health_insert()"
    )


def _restore_analysis_report_api_write_boundary() -> None:
    for table, trigger in (
        ("analysis_report_health_snapshot", "analysis_report_health_insert_guard"),
        ("analysis_report_review_event", "analysis_report_review_insert_guard"),
        ("analysis_report_audit_event", "analysis_report_audit_insert_guard"),
        ("analysis_report_citation", "analysis_report_citation_write_guard"),
        ("analysis_report_section", "analysis_report_section_write_guard"),
        ("analysis_report_generation_job", "analysis_report_job_write_guard"),
        ("analysis_report_version", "analysis_report_version_write_guard"),
        ("analysis_report", "analysis_report_write_guard"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON f1.{table}")
    for function in (
        "f1.guard_analysis_report_health_insert()",
        "f1.guard_analysis_report_review_insert()",
        "f1.guard_analysis_report_audit_insert()",
        "f1.guard_analysis_report_content_write()",
        "f1.guard_analysis_report_job_write()",
        "f1.guard_analysis_report_version_write()",
        "f1.guard_analysis_report_write()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")
    op.execute(
        "REVOKE INSERT (id,enterprise_id,client_account_id,template_id,title,"
        "current_version_id,current_version_no,create_request_id,created_by_user_id),"
        "UPDATE (current_version_id,current_version_no,client_visible,updated_at) "
        "ON f1.analysis_report FROM f1_api"
    )
    op.execute(
        "REVOKE INSERT (id,enterprise_id,report_id,client_account_id,version_number,"
        "status,source_fingerprint_sha256,artifact_ready,created_by_user_id),"
        "UPDATE (status,artifact_ready,published_at,updated_at) "
        "ON f1.analysis_report_version FROM f1_api"
    )
    op.execute(
        "REVOKE INSERT (id,enterprise_id,report_id,version_id,request_id,status,"
        "source_fingerprint_sha256,lease_token,lease_until,lease_owner),"
        "UPDATE (status,lease_token,lease_until,lease_owner,error_reason,updated_at) "
        "ON f1.analysis_report_generation_job FROM f1_api"
    )
    for table, columns in (
        (
            "analysis_report_section",
            "id,enterprise_id,version_id,section_key,title,body,ordinal",
        ),
        (
            "analysis_report_citation",
            "id,enterprise_id,version_id,document_version_id,document_name,"
            "version_number,page_number,excerpt,ordinal",
        ),
    ):
        op.execute(
            f"REVOKE INSERT ({columns}),DELETE ON f1.{table} FROM f1_api"
        )
    for table, columns in (
        (
            "analysis_report_audit_event",
            "id,enterprise_id,report_id,version_id,actor_user_id,action,"
            "from_status,to_status",
        ),
        (
            "analysis_report_review_event",
            "id,enterprise_id,report_id,version_id,actor_user_id,action,"
            "checklist,comment",
        ),
        (
            "analysis_report_health_snapshot",
            "id,enterprise_id,report_id,version_id,client_account_id,payload,"
            "payload_sha256,score,max_score",
        ),
    ):
        op.execute(f"REVOKE INSERT ({columns}) ON f1.{table} FROM f1_api")
    for table in (
        "analysis_report","analysis_report_version",
        "analysis_report_generation_job","analysis_report_section",
        "analysis_report_citation","analysis_report_audit_event",
        "analysis_report_review_event","analysis_report_health_snapshot",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON f1.{table} FROM f1_api")
    op.execute(
        "GRANT SELECT,INSERT,UPDATE ON f1.analysis_report,"
        "f1.analysis_report_version,f1.analysis_report_generation_job TO f1_api"
    )
    op.execute(
        "GRANT SELECT,INSERT,DELETE ON f1.analysis_report_section,"
        "f1.analysis_report_citation TO f1_api"
    )
    op.execute(
        "GRANT SELECT,INSERT ON f1.analysis_report_audit_event,"
        "f1.analysis_report_review_event,f1.analysis_report_health_snapshot TO f1_api"
    )


def _report_actor_revocation_boundary() -> None:
    """Terminate only an exact current job whose delivery actor was revoked.

    The capability is callable only from an unscoped API runtime session.  It
    locks report -> job -> version, accepts a never-claimed queued job or an
    expired generating lease, and writes one fixed terminal reason/audit row.
    """
    op.execute(
        """
        CREATE FUNCTION f1.fail_revoked_report_generation(
          p_enterprise_id uuid, p_job_id uuid, p_provider_sub text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE
          v_report_id uuid;
          v_version_id uuid;
          v_actor_user_id uuid;
          v_current_version_id uuid;
          v_job_status text;
          v_version_status text;
          v_lease_token uuid;
          v_lease_until timestamptz;
          v_lease_owner text;
          v_actor_role text;
          v_count integer;
        BEGIN
          IF session_user <> 'f1_api'
             OR NULLIF(current_setting('f1.enterprise_id',true),'') IS NOT NULL
             OR NULLIF(current_setting('f1.sub',true),'') IS NOT NULL
             OR p_enterprise_id IS NULL OR p_job_id IS NULL
             OR p_provider_sub IS NULL
             OR char_length(p_provider_sub) NOT BETWEEN 1 AND 255
             OR p_provider_sub <> btrim(p_provider_sub)
             OR p_provider_sub ~ '[[:cntrl:]]' THEN
            RAISE EXCEPTION 'REPORT_ACTOR_REVOCATION_INVALID';
          END IF;

          SELECT job.report_id,job.version_id,profile.id
            INTO v_report_id,v_version_id,v_actor_user_id
          FROM f1.analysis_report_generation_job AS job
          JOIN f1.analysis_report_version AS version
            ON version.enterprise_id=job.enterprise_id
           AND version.report_id=job.report_id
           AND version.id=job.version_id
          JOIN f1.user_profile AS profile
            ON profile.keycloak_sub=p_provider_sub
          JOIN f1.analysis_report_generation_delivery AS delivery
            ON delivery.enterprise_id=job.enterprise_id
           AND delivery.report_id=job.report_id
           AND delivery.job_id=job.id
           AND delivery.version_id=job.version_id
           AND delivery.actor_sub=p_provider_sub
          WHERE job.enterprise_id=p_enterprise_id
            AND job.id=p_job_id;
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;

          SELECT report.current_version_id
            INTO v_current_version_id
          FROM f1.analysis_report AS report
          WHERE report.enterprise_id=p_enterprise_id
            AND report.id=v_report_id
          FOR UPDATE;
          IF NOT FOUND OR v_current_version_id IS DISTINCT FROM v_version_id THEN
            RETURN FALSE;
          END IF;

          SELECT job.status,job.lease_token,job.lease_until,job.lease_owner
            INTO v_job_status,v_lease_token,v_lease_until,v_lease_owner
          FROM f1.analysis_report_generation_job AS job
          WHERE job.enterprise_id=p_enterprise_id
            AND job.id=p_job_id
            AND job.report_id=v_report_id
            AND job.version_id=v_version_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;

          SELECT version.status
            INTO v_version_status
          FROM f1.analysis_report_version AS version
          WHERE version.enterprise_id=p_enterprise_id
            AND version.report_id=v_report_id
            AND version.id=v_version_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;

          SELECT membership.role INTO v_actor_role
          FROM f1.enterprise_user AS membership
          WHERE membership.enterprise_id=p_enterprise_id
            AND membership.user_id=v_actor_user_id
          FOR UPDATE OF membership;
          -- Report/version/audit actor foreign keys retain this membership;
          -- revocation is therefore represented by a non-admin role, not by
          -- deleting the membership row.
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;
          IF v_actor_role IN ('super_admin','enterprise_admin') THEN
            RETURN FALSE;
          END IF;

          IF v_job_status='queued' THEN
            IF v_version_status<>'queued'
               OR v_lease_token IS NOT NULL OR v_lease_until IS NOT NULL
               OR v_lease_owner IS NOT NULL THEN
              RETURN FALSE;
            END IF;
          ELSIF v_job_status='generating' THEN
            IF v_version_status<>'generating'
               OR v_lease_token IS NULL OR v_lease_until IS NULL
               OR v_lease_until>statement_timestamp() THEN
              RETURN FALSE;
            END IF;
          ELSE
            RETURN FALSE;
          END IF;

          UPDATE f1.analysis_report_generation_job AS job
             SET status='failed',error_reason='REPORT_ACTOR_REVOKED',
                 lease_token=NULL,lease_until=NULL,lease_owner=NULL,
                 updated_at=statement_timestamp()
           WHERE job.enterprise_id=p_enterprise_id
             AND job.id=p_job_id
             AND job.report_id=v_report_id
             AND job.version_id=v_version_id
             AND job.status=v_job_status;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count<>1 THEN
            RAISE EXCEPTION 'REPORT_ACTOR_REVOCATION_STATE_INVALID';
          END IF;

          UPDATE f1.analysis_report_version AS version
             SET status='failed',updated_at=statement_timestamp()
           WHERE version.enterprise_id=p_enterprise_id
             AND version.report_id=v_report_id
             AND version.id=v_version_id
             AND version.status=v_version_status;
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count<>1 THEN
            RAISE EXCEPTION 'REPORT_ACTOR_REVOCATION_STATE_INVALID';
          END IF;

          INSERT INTO f1.analysis_report_audit_event (
            id,enterprise_id,report_id,version_id,actor_user_id,
            action,from_status,to_status
          ) VALUES (
            gen_random_uuid(),p_enterprise_id,v_report_id,v_version_id,
            v_actor_user_id,'actor_revoked',v_version_status,'failed'
          );
          RETURN TRUE;
        END
        $$
        """
    )
    seam = (
        "session_user='f1_api' "
        "AND NULLIF(current_setting('f1.enterprise_id',true),'') IS NULL "
        "AND NULLIF(current_setting('f1.sub',true),'') IS NULL"
    )
    for table in (
        "analysis_report",
        "analysis_report_generation_job",
        "analysis_report_version",
        "user_profile",
        "enterprise_user",
    ):
        op.execute(
            f"CREATE POLICY analysis_report_revocation_{table}_select "
            f"ON f1.{table} FOR SELECT TO {_REPORT_DEFINER_ROLE} "
            f"USING ({seam})"
        )
    for table in (
        "analysis_report",
        "analysis_report_generation_job",
        "analysis_report_version",
    ):
        op.execute(
            f"CREATE POLICY analysis_report_revocation_{table}_update "
            f"ON f1.{table} FOR UPDATE TO {_REPORT_DEFINER_ROLE} "
            f"USING ({seam}) WITH CHECK ({seam})"
        )
    op.execute(
        "CREATE POLICY analysis_report_revocation_enterprise_user_lock "
        "ON f1.enterprise_user FOR UPDATE TO "
        f"{_REPORT_DEFINER_ROLE} USING ({seam}) WITH CHECK ({seam})"
    )
    op.execute(
        "CREATE POLICY analysis_report_revocation_audit_insert "
        "ON f1.analysis_report_audit_event FOR INSERT TO "
        f"{_REPORT_DEFINER_ROLE} WITH CHECK ({seam})"
    )
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {_REPORT_DEFINER_ROLE}")
    op.execute(
        "GRANT SELECT (enterprise_id,id,current_version_id),UPDATE (updated_at) "
        f"ON f1.analysis_report TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "GRANT SELECT (enterprise_id,id,report_id,version_id,status,error_reason,"
        "lease_token,lease_until,lease_owner),UPDATE (status,error_reason,lease_token,"
        "lease_until,lease_owner,updated_at) ON f1.analysis_report_generation_job "
        f"TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "GRANT SELECT (enterprise_id,id,report_id,status,created_by_user_id),"
        "UPDATE (status,updated_at) ON f1.analysis_report_version "
        f"TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (id,keycloak_sub) ON f1.user_profile "
        f"TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        f"GRANT SELECT (enterprise_id,user_id,role),UPDATE (role) "
        f"ON f1.enterprise_user "
        f"TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "GRANT INSERT (id,enterprise_id,report_id,version_id,actor_user_id,"
        "action,from_status,to_status) ON f1.analysis_report_audit_event "
        f"TO {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f1.fail_revoked_report_generation(uuid,uuid,text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.fail_revoked_report_generation(uuid,uuid,text) TO f1_api"
    )


def _report_generation_delivery_boundary() -> None:
    """Make PostgreSQL the recovery authority for report queue delivery."""
    op.execute(
        f"""
        CREATE TABLE f1.{_REPORT_DELIVERY_TABLE} (
          id uuid PRIMARY KEY,
          enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
          report_id uuid NOT NULL,
          job_id uuid NOT NULL,
          version_id uuid NOT NULL,
          actor_sub text NOT NULL,
          state text NOT NULL DEFAULT 'pending',
          attempt integer NOT NULL DEFAULT 0,
          dispatch_token uuid,
          dispatch_lease_until timestamptz,
          next_attempt_at timestamptz,
          reason_code text,
          created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
          completed_at timestamptz,
          CONSTRAINT analysis_report_generation_delivery_enterprise_id_id_uq
            UNIQUE (enterprise_id,id),
          CONSTRAINT analysis_report_generation_delivery_job_uq
            UNIQUE (enterprise_id,job_id),
          CONSTRAINT analysis_report_generation_delivery_job_fk
            FOREIGN KEY (enterprise_id,job_id)
            REFERENCES f1.analysis_report_generation_job(enterprise_id,id),
          CONSTRAINT analysis_report_generation_delivery_version_fk
            FOREIGN KEY (enterprise_id,report_id,version_id)
            REFERENCES f1.analysis_report_version(enterprise_id,report_id,id),
          CONSTRAINT analysis_report_generation_delivery_actor_ck CHECK (
            char_length(actor_sub) BETWEEN 1 AND 255
            AND actor_sub=btrim(actor_sub)
            AND actor_sub !~ '[[:cntrl:]]'
          ),
          CONSTRAINT analysis_report_generation_delivery_state_ck CHECK (
            state IN ('pending','dispatched','retry_wait','done','blocked')
          ),
          CONSTRAINT analysis_report_generation_delivery_attempt_ck
            CHECK (attempt >= 0),
          CONSTRAINT analysis_report_generation_delivery_reason_ck CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z0-9_]{{1,80}}$'
          ),
          CONSTRAINT analysis_report_generation_delivery_shape_ck CHECK (
            (state='pending'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NULL)
            OR
            (state='dispatched'
             AND dispatch_token IS NOT NULL AND dispatch_lease_until IS NOT NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NULL)
            OR
            (state='retry_wait'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NOT NULL AND reason_code IS NOT NULL
             AND completed_at IS NULL)
            OR
            (state='done'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NULL
             AND completed_at IS NOT NULL)
            OR
            (state='blocked'
             AND dispatch_token IS NULL AND dispatch_lease_until IS NULL
             AND next_attempt_at IS NULL AND reason_code IS NOT NULL
             AND completed_at IS NOT NULL)
          )
        )
        """
    )
    op.execute(
        f"CREATE INDEX analysis_report_generation_delivery_due_idx "
        f"ON f1.{_REPORT_DELIVERY_TABLE}("
        "state,next_attempt_at,dispatch_lease_until,updated_at,id)"
    )

    # Existing f1_0017 report jobs may predate this delivery table.  The
    # bootstrap session is the already-validated migration authority and can
    # read the FORCE-RLS report tables without inventing tenant GUCs.
    op.execute("RESET ROLE")
    op.execute(
        f"""
        INSERT INTO f1.{_REPORT_DELIVERY_TABLE} (
          id,enterprise_id,report_id,job_id,version_id,actor_sub,state,
          attempt,reason_code,completed_at
        )
        SELECT
          md5('analysis-report-generation:' || job.id::text)::uuid,
          job.enterprise_id,job.report_id,job.id,job.version_id,
          CASE
            WHEN membership.role IN ('super_admin','enterprise_admin')
             AND profile.keycloak_sub IS NOT NULL
             AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
             AND profile.keycloak_sub=btrim(profile.keycloak_sub)
             AND profile.keycloak_sub !~ '[[:cntrl:]]'
              THEN profile.keycloak_sub
            ELSE '__historical_actor_rebind_required__'
          END,
          CASE
            WHEN job.status='draft' THEN 'done'
            WHEN job.status='failed' THEN 'blocked'
            WHEN report.current_version_id=job.version_id
                 AND job.status IN ('queued','generating')
                 AND membership.role IN ('super_admin','enterprise_admin')
                 AND profile.keycloak_sub IS NOT NULL
                 AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
                 AND profile.keycloak_sub=btrim(profile.keycloak_sub)
                 AND profile.keycloak_sub !~ '[[:cntrl:]]' THEN 'pending'
            ELSE 'blocked'
          END,
          0,
          CASE
            WHEN job.status='failed'
              THEN COALESCE(job.error_reason,'REPORT_GENERATION_FAILED')
            WHEN job.status='draft' THEN NULL
            WHEN report.current_version_id=job.version_id
                 AND job.status IN ('queued','generating')
                 AND membership.role IN ('super_admin','enterprise_admin')
                 AND profile.keycloak_sub IS NOT NULL
                 AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
                 AND profile.keycloak_sub=btrim(profile.keycloak_sub)
                 AND profile.keycloak_sub !~ '[[:cntrl:]]' THEN NULL
            WHEN report.current_version_id=job.version_id
                 AND job.status IN ('queued','generating')
              THEN 'REPORT_ACTOR_REBIND_REQUIRED'
            ELSE 'REPORT_VERSION_NOT_CURRENT'
          END,
          CASE
            WHEN report.current_version_id=job.version_id
                 AND job.status IN ('queued','generating')
                 AND membership.role IN ('super_admin','enterprise_admin')
                 AND profile.keycloak_sub IS NOT NULL
                 AND char_length(profile.keycloak_sub) BETWEEN 1 AND 255
                 AND profile.keycloak_sub=btrim(profile.keycloak_sub)
                 AND profile.keycloak_sub !~ '[[:cntrl:]]' THEN NULL
            ELSE statement_timestamp()
          END
        FROM f1.analysis_report_generation_job AS job
        JOIN f1.analysis_report AS report
          ON report.enterprise_id=job.enterprise_id
         AND report.id=job.report_id
        JOIN f1.analysis_report_version AS version
          ON version.enterprise_id=job.enterprise_id
         AND version.report_id=job.report_id
         AND version.id=job.version_id
        JOIN f1.user_profile AS profile
          ON profile.id=version.created_by_user_id
        LEFT JOIN f1.enterprise_user AS membership
          ON membership.enterprise_id=job.enterprise_id
         AND membership.user_id=version.created_by_user_id
        ON CONFLICT ON CONSTRAINT analysis_report_generation_delivery_job_uq
        DO NOTHING
        """
    )
    op.execute("SET LOCAL ROLE f0d_migration")

    op.execute(
        f"""
        CREATE FUNCTION f1.register_analysis_report_generation_delivery(
          p_enterprise_id uuid,p_job_id uuid,p_actor_sub text,
          p_rearm_failed boolean
        ) RETURNS TABLE(
          delivery_id uuid,enterprise_id uuid,report_id uuid,job_id uuid,
          version_id uuid,actor_sub text,state text,attempt integer,
          reason_code text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE
          v_delivery_id uuid;
          v_report_id uuid;
          v_version_id uuid;
          v_current_version_id uuid;
          v_job_status text;
          v_job_reason text;
          v_version_status text;
          v_created_by_user_id uuid;
          v_actor_user_id uuid;
        BEGIN
          v_delivery_id := md5(
            'analysis-report-generation:' || p_job_id::text
          )::uuid;
          IF session_user<>'f1_api'
             OR p_enterprise_id IS NULL OR p_job_id IS NULL
             OR p_rearm_failed IS NULL
             OR p_actor_sub IS NULL
             OR char_length(p_actor_sub) NOT BETWEEN 1 AND 255
             OR p_actor_sub<>btrim(p_actor_sub)
             OR p_actor_sub ~ '[[:cntrl:]]'
             OR NULLIF(current_setting('f1.enterprise_id',true),'')::uuid
                IS DISTINCT FROM p_enterprise_id
             OR NULLIF(current_setting('f1.sub',true),'')
                IS DISTINCT FROM p_actor_sub
             OR NOT f1.session_authorized(p_enterprise_id) THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          SELECT job.report_id,job.version_id
            INTO v_report_id,v_version_id
          FROM f1.analysis_report_generation_job AS job
          WHERE job.enterprise_id=p_enterprise_id AND job.id=p_job_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          SELECT report.current_version_id INTO v_current_version_id
          FROM f1.analysis_report AS report
          WHERE report.enterprise_id=p_enterprise_id AND report.id=v_report_id
          FOR UPDATE;
          IF NOT FOUND OR v_current_version_id IS DISTINCT FROM v_version_id THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          SELECT job.status,job.error_reason
            INTO v_job_status,v_job_reason
          FROM f1.analysis_report_generation_job AS job
          WHERE job.enterprise_id=p_enterprise_id
            AND job.id=p_job_id
            AND job.report_id=v_report_id
            AND job.version_id=v_version_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          SELECT version.status,version.created_by_user_id
            INTO v_version_status,v_created_by_user_id
          FROM f1.analysis_report_version AS version
          WHERE version.enterprise_id=p_enterprise_id
            AND version.report_id=v_report_id
            AND version.id=v_version_id
          FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          SELECT membership.user_id INTO v_actor_user_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=p_enterprise_id
            AND profile.keycloak_sub=p_actor_sub
            AND membership.role IN ('super_admin','enterprise_admin')
          FOR UPDATE OF membership;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
          END IF;

          IF p_rearm_failed IS FALSE THEN
            IF v_job_status<>'queued' OR v_job_reason IS NOT NULL
               OR v_version_status<>'queued'
               OR v_created_by_user_id<>v_actor_user_id THEN
              RAISE EXCEPTION 'REPORT_DELIVERY_REGISTER_INVALID';
            END IF;
          ELSE
            IF v_job_status<>'failed' OR v_version_status<>'failed'
               OR v_job_reason NOT IN (
                 'REPORT_QUEUE_DISPATCH_FAILED',
                 'REPORT_GENERATION_RETRIES_EXHAUSTED',
                 'REPORT_WORKER_GENERATION_DISABLED',
                 'REPORT_ACTOR_REVOKED'
               ) THEN
              RAISE EXCEPTION 'REPORT_DELIVERY_REARM_INVALID';
            END IF;
          END IF;

          RETURN QUERY
          INSERT INTO f1.{_REPORT_DELIVERY_TABLE} AS delivery (
            id,enterprise_id,report_id,job_id,version_id,actor_sub,state
          ) VALUES (
            v_delivery_id,p_enterprise_id,v_report_id,p_job_id,v_version_id,
            p_actor_sub,'pending'
          )
          ON CONFLICT ON CONSTRAINT analysis_report_generation_delivery_job_uq
          DO UPDATE SET actor_sub=EXCLUDED.actor_sub,state='pending',attempt=0,
            dispatch_token=NULL,dispatch_lease_until=NULL,next_attempt_at=NULL,
            reason_code=NULL,completed_at=NULL,updated_at=statement_timestamp()
          WHERE p_rearm_failed IS TRUE
          RETURNING delivery.id,delivery.enterprise_id,delivery.report_id,
            delivery.job_id,delivery.version_id,delivery.actor_sub,
            delivery.state,delivery.attempt,delivery.reason_code;
          IF NOT FOUND THEN
            RETURN QUERY
            SELECT delivery.id,delivery.enterprise_id,delivery.report_id,
              delivery.job_id,delivery.version_id,delivery.actor_sub,
              delivery.state,delivery.attempt,delivery.reason_code
            FROM f1.{_REPORT_DELIVERY_TABLE} AS delivery
            WHERE delivery.enterprise_id=p_enterprise_id
              AND delivery.job_id=p_job_id;
          END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.rebind_analysis_report_generation_delivery(
          p_enterprise_id uuid,p_job_id uuid,p_actor_sub text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog AS $$
        DECLARE
          v_report_id uuid;
          v_version_id uuid;
          v_current_version_id uuid;
          v_job_status text;
          v_version_status text;
          v_lease_token uuid;
          v_lease_until timestamptz;
          v_lease_owner text;
          v_actor_user_id uuid;
          v_count integer;
        BEGIN
          IF session_user<>'f1_api'
             OR p_enterprise_id IS NULL OR p_job_id IS NULL
             OR p_actor_sub IS NULL
             OR char_length(p_actor_sub) NOT BETWEEN 1 AND 255
             OR p_actor_sub<>btrim(p_actor_sub)
             OR p_actor_sub ~ '[[:cntrl:]]'
             OR NULLIF(current_setting('f1.enterprise_id',true),'')::uuid
                IS DISTINCT FROM p_enterprise_id
             OR NULLIF(current_setting('f1.sub',true),'')
                IS DISTINCT FROM p_actor_sub
             OR NOT f1.session_authorized(p_enterprise_id) THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REBIND_INVALID';
          END IF;

          SELECT job.report_id,job.version_id
            INTO v_report_id,v_version_id
          FROM f1.analysis_report_generation_job AS job
          WHERE job.enterprise_id=p_enterprise_id AND job.id=p_job_id;
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;

          SELECT report.current_version_id INTO v_current_version_id
          FROM f1.analysis_report AS report
          WHERE report.enterprise_id=p_enterprise_id AND report.id=v_report_id
          FOR UPDATE;
          IF NOT FOUND OR v_current_version_id IS DISTINCT FROM v_version_id THEN
            RETURN FALSE;
          END IF;

          SELECT job.status,job.lease_token,job.lease_until,job.lease_owner
            INTO v_job_status,v_lease_token,v_lease_until,v_lease_owner
          FROM f1.analysis_report_generation_job AS job
          WHERE job.enterprise_id=p_enterprise_id
            AND job.id=p_job_id
            AND job.report_id=v_report_id
            AND job.version_id=v_version_id
          FOR UPDATE;
          IF NOT FOUND OR v_job_status NOT IN ('queued','generating') THEN
            RETURN FALSE;
          END IF;

          SELECT version.status INTO v_version_status
          FROM f1.analysis_report_version AS version
          WHERE version.enterprise_id=p_enterprise_id
            AND version.report_id=v_report_id
            AND version.id=v_version_id
          FOR UPDATE;
          IF NOT FOUND OR v_version_status IS DISTINCT FROM v_job_status THEN
            RETURN FALSE;
          END IF;
          IF (v_job_status='queued' AND (
                v_lease_token IS NOT NULL OR v_lease_until IS NOT NULL
                OR v_lease_owner IS NOT NULL
              )) OR (v_job_status='generating' AND (
                v_lease_token IS NULL OR v_lease_until IS NULL
                OR v_lease_owner IS NULL
                OR v_lease_until>statement_timestamp()
              )) THEN
            RETURN FALSE;
          END IF;

          SELECT membership.user_id INTO v_actor_user_id
          FROM f1.enterprise_user AS membership
          JOIN f1.user_profile AS profile ON profile.id=membership.user_id
          WHERE membership.enterprise_id=p_enterprise_id
            AND profile.keycloak_sub=p_actor_sub
            AND membership.role IN ('super_admin','enterprise_admin')
          FOR UPDATE OF membership;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REBIND_INVALID';
          END IF;

          PERFORM 1
          FROM f1.{_REPORT_DELIVERY_TABLE} AS delivery
          WHERE delivery.enterprise_id=p_enterprise_id
            AND delivery.report_id=v_report_id
            AND delivery.job_id=p_job_id
            AND delivery.version_id=v_version_id
            AND delivery.state='blocked'
            AND delivery.reason_code='REPORT_ACTOR_REBIND_REQUIRED'
          FOR UPDATE;
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;

          INSERT INTO f1.analysis_report_audit_event (
            id,enterprise_id,report_id,version_id,actor_user_id,
            action,from_status,to_status
          ) VALUES (
            gen_random_uuid(),p_enterprise_id,v_report_id,v_version_id,
            v_actor_user_id,'actor_rebound','blocked',v_job_status
          );

          UPDATE f1.{_REPORT_DELIVERY_TABLE} AS delivery
             SET actor_sub=p_actor_sub,state='pending',attempt=0,
                 dispatch_token=NULL,dispatch_lease_until=NULL,
                 next_attempt_at=NULL,reason_code=NULL,completed_at=NULL,
                 updated_at=statement_timestamp()
           WHERE delivery.enterprise_id=p_enterprise_id
             AND delivery.report_id=v_report_id
             AND delivery.job_id=p_job_id
             AND delivery.version_id=v_version_id
             AND delivery.state='blocked'
             AND delivery.reason_code='REPORT_ACTOR_REBIND_REQUIRED';
          GET DIAGNOSTICS v_count=ROW_COUNT;
          IF v_count<>1 THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_REBIND_STATE_INVALID';
          END IF;
          RETURN TRUE;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.claim_analysis_report_generation_deliveries(
          p_limit integer,p_lease_seconds integer
        ) RETURNS TABLE(delivery_id uuid,dispatch_token uuid,attempt integer)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF session_user<>'f1_worker'
             OR p_limit IS NULL OR p_limit<1 OR p_limit>100
             OR p_lease_seconds IS NULL
             OR p_lease_seconds<30 OR p_lease_seconds>1800 THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_CLAIM_INVALID';
          END IF;
          RETURN QUERY
          WITH candidates AS MATERIALIZED (
            SELECT delivery.id
            FROM f1.{_REPORT_DELIVERY_TABLE} AS delivery
            WHERE delivery.state='pending'
               OR (delivery.state='retry_wait'
                   AND delivery.next_attempt_at<=statement_timestamp())
               OR (delivery.state='dispatched'
                   AND delivery.dispatch_lease_until<=statement_timestamp())
            ORDER BY COALESCE(
              delivery.next_attempt_at,delivery.dispatch_lease_until,
              delivery.updated_at
            ),delivery.id
            LIMIT p_limit
            FOR UPDATE OF delivery SKIP LOCKED
          )
          UPDATE f1.{_REPORT_DELIVERY_TABLE} AS delivery
             SET state='dispatched',attempt=delivery.attempt+1,
                 dispatch_token=gen_random_uuid(),
                 dispatch_lease_until=statement_timestamp()
                   + make_interval(secs=>p_lease_seconds),
                 next_attempt_at=NULL,reason_code=NULL,completed_at=NULL,
                 updated_at=statement_timestamp()
          FROM candidates
          WHERE delivery.id=candidates.id
          RETURNING delivery.id,delivery.dispatch_token,delivery.attempt;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.read_analysis_report_generation_delivery_claim(
          p_delivery_id uuid,p_dispatch_token uuid
        ) RETURNS TABLE(
          delivery_id uuid,enterprise_id uuid,report_id uuid,job_id uuid,
          version_id uuid,actor_sub text,dispatch_token uuid,attempt integer,
          job_status text,error_reason text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        BEGIN
          IF session_user NOT IN ('f1_api','f1_worker')
             OR (session_user='f1_api' AND (
               NULLIF(current_setting('f1.enterprise_id',true),'') IS NOT NULL
               OR NULLIF(current_setting('f1.sub',true),'') IS NOT NULL
             ))
             OR p_delivery_id IS NULL OR p_dispatch_token IS NULL THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_READ_INVALID';
          END IF;
          RETURN QUERY
          SELECT delivery.id,delivery.enterprise_id,delivery.report_id,
                 delivery.job_id,delivery.version_id,delivery.actor_sub,
                 delivery.dispatch_token,delivery.attempt,
                 job.status,job.error_reason
          FROM f1.{_REPORT_DELIVERY_TABLE} AS delivery
          JOIN f1.analysis_report_generation_job AS job
            ON job.enterprise_id=delivery.enterprise_id
           AND job.id=delivery.job_id
           AND job.report_id=delivery.report_id
           AND job.version_id=delivery.version_id
          WHERE delivery.id=p_delivery_id
            AND delivery.state='dispatched'
            AND delivery.dispatch_token=p_dispatch_token
            AND delivery.dispatch_lease_until>statement_timestamp();
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.finish_analysis_report_generation_delivery(
          p_delivery_id uuid,p_dispatch_token uuid,p_outcome text,
          p_reason_code text,p_retry_seconds integer
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF session_user NOT IN ('f1_api','f1_worker')
             OR (session_user='f1_api' AND (
               NULLIF(current_setting('f1.enterprise_id',true),'') IS NOT NULL
               OR NULLIF(current_setting('f1.sub',true),'') IS NOT NULL
             ))
             OR p_delivery_id IS NULL OR p_dispatch_token IS NULL
             OR p_outcome IS NULL OR p_outcome NOT IN ('done','retry','blocked')
             OR (p_reason_code IS NOT NULL
                 AND p_reason_code !~ '^[A-Z0-9_]{{1,80}}$')
             OR (p_outcome='done'
                 AND (p_reason_code IS NOT NULL OR p_retry_seconds IS NOT NULL))
             OR (p_outcome='retry'
                 AND (p_reason_code IS NULL OR p_retry_seconds IS NULL
                      OR p_retry_seconds<1 OR p_retry_seconds>1800))
             OR (p_outcome='blocked'
                 AND (p_reason_code IS NULL OR p_retry_seconds IS NOT NULL)) THEN
            RAISE EXCEPTION 'REPORT_DELIVERY_FINISH_INVALID';
          END IF;

          UPDATE f1.{_REPORT_DELIVERY_TABLE} AS delivery
             SET state=CASE p_outcome
                         WHEN 'done' THEN 'done'
                         WHEN 'retry' THEN 'retry_wait'
                         ELSE 'blocked'
                       END,
                 dispatch_token=NULL,dispatch_lease_until=NULL,
                 next_attempt_at=CASE WHEN p_outcome='retry'
                   THEN statement_timestamp()+make_interval(secs=>p_retry_seconds)
                   ELSE NULL END,
                 reason_code=CASE WHEN p_outcome='done'
                   THEN NULL ELSE p_reason_code END,
                 completed_at=CASE WHEN p_outcome IN ('done','blocked')
                   THEN statement_timestamp() ELSE NULL END,
                 updated_at=statement_timestamp()
          FROM f1.analysis_report_generation_job AS job
          WHERE delivery.id=p_delivery_id
            AND delivery.state='dispatched'
            AND delivery.dispatch_token=p_dispatch_token
            AND delivery.dispatch_lease_until>statement_timestamp()
            AND job.enterprise_id=delivery.enterprise_id
            AND job.id=delivery.job_id
            AND job.report_id=delivery.report_id
            AND job.version_id=delivery.version_id
            AND (
              (p_outcome='done' AND job.status='draft')
              OR (p_outcome='retry' AND job.status IN ('queued','generating'))
              OR (p_outcome='blocked' AND job.status='failed'
                  AND job.error_reason=p_reason_code)
            );
          GET DIAGNOSTICS v_count=ROW_COUNT;
          RETURN v_count=1;
        END
        $$
        """
    )

    signatures = (
        "f1.register_analysis_report_generation_delivery(uuid,uuid,text,boolean)",
        "f1.rebind_analysis_report_generation_delivery(uuid,uuid,text)",
        "f1.claim_analysis_report_generation_deliveries(integer,integer)",
        "f1.read_analysis_report_generation_delivery_claim(uuid,uuid)",
        "f1.finish_analysis_report_generation_delivery(uuid,uuid,text,text,integer)",
    )
    for signature in signatures:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.register_analysis_report_generation_delivery(uuid,uuid,text,boolean) "
        "TO f1_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.rebind_analysis_report_generation_delivery(uuid,uuid,text) TO f1_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.claim_analysis_report_generation_deliveries(integer,integer) "
        "TO f1_worker"
    )
    for signature in signatures[3:]:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api,f1_worker")

    scoped = (
        "session_user='f1_api' "
        "AND enterprise_id=NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
        "AND f1.session_authorized(enterprise_id)"
    )
    unscoped = (
        "session_user IN ('f1_api','f1_worker') "
        "AND NULLIF(current_setting('f1.enterprise_id',true),'') IS NULL "
        "AND NULLIF(current_setting('f1.sub',true),'') IS NULL"
    )
    for table in (
        "analysis_report",
        "analysis_report_generation_job",
        "analysis_report_version",
    ):
        op.execute(
            f"CREATE POLICY analysis_report_delivery_{table}_select "
            f"ON f1.{table} FOR SELECT TO {_REPORT_DEFINER_ROLE} "
            f"USING (({scoped}) OR ({unscoped}))"
        )
    op.execute(
        "CREATE POLICY analysis_report_delivery_profile_select "
        f"ON f1.user_profile FOR SELECT TO {_REPORT_DEFINER_ROLE} USING ("
        "session_user='f1_api' AND ("
        "NULLIF(current_setting('f1.sub',true),'') IS NULL "
        "OR keycloak_sub=NULLIF(current_setting('f1.sub',true),'')))"
    )
    op.execute(
        "CREATE POLICY analysis_report_delivery_membership_select "
        f"ON f1.enterprise_user FOR SELECT TO {_REPORT_DEFINER_ROLE} USING ("
        "session_user='f1_api' AND ("
        "NULLIF(current_setting('f1.enterprise_id',true),'') IS NULL OR ("
        "enterprise_id=NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
        "AND EXISTS (SELECT 1 FROM f1.user_profile AS profile "
        "WHERE profile.id=enterprise_user.user_id "
        "AND profile.keycloak_sub=NULLIF(current_setting('f1.sub',true),'')))))"
    )

    # PostgreSQL row locks are governed by UPDATE as well as SELECT RLS.  Keep
    # the register/rebind lock seam scoped to the authenticated current admin;
    # the unscoped dispatcher/read/finish functions never acquire these locks.
    for table in (
        "analysis_report",
        "analysis_report_generation_job",
        "analysis_report_version",
    ):
        scoped_admin_lock = (
            "session_user='f1_api' "
            f"AND {table}.enterprise_id="
            "NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
            f"AND f1.session_authorized({table}.enterprise_id) "
            "AND EXISTS (SELECT 1 FROM f1.enterprise_user AS actor "
            "JOIN f1.user_profile AS profile ON profile.id=actor.user_id "
            f"WHERE actor.enterprise_id={table}.enterprise_id "
            "AND profile.keycloak_sub="
            "NULLIF(current_setting('f1.sub',true),'') "
            "AND actor.role IN ('super_admin','enterprise_admin'))"
        )
        op.execute(
            f"CREATE POLICY analysis_report_delivery_{table}_lock "
            f"ON f1.{table} FOR UPDATE TO {_REPORT_DEFINER_ROLE} "
            f"USING ({scoped_admin_lock}) WITH CHECK ({scoped_admin_lock})"
        )
    membership_lock = (
        "session_user='f1_api' "
        "AND enterprise_user.enterprise_id="
        "NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
        "AND f1.session_authorized(enterprise_user.enterprise_id) "
        "AND enterprise_user.role IN ('super_admin','enterprise_admin') "
        "AND EXISTS (SELECT 1 FROM f1.user_profile AS profile "
        "WHERE profile.id=enterprise_user.user_id "
        "AND profile.keycloak_sub="
        "NULLIF(current_setting('f1.sub',true),''))"
    )
    op.execute(
        "CREATE POLICY analysis_report_delivery_membership_lock "
        f"ON f1.enterprise_user FOR UPDATE TO {_REPORT_DEFINER_ROLE} "
        f"USING ({membership_lock}) WITH CHECK ({membership_lock})"
    )
    op.execute(
        "CREATE POLICY analysis_report_delivery_audit_insert "
        "ON f1.analysis_report_audit_event FOR INSERT TO "
        f"{_REPORT_DEFINER_ROLE} WITH CHECK ("
        "session_user='f1_api' "
        "AND analysis_report_audit_event.enterprise_id="
        "NULLIF(current_setting('f1.enterprise_id',true),'')::uuid "
        "AND f1.session_authorized(analysis_report_audit_event.enterprise_id) "
        "AND EXISTS (SELECT 1 FROM f1.enterprise_user AS actor "
        "JOIN f1.user_profile AS profile ON profile.id=actor.user_id "
        "WHERE actor.enterprise_id=analysis_report_audit_event.enterprise_id "
        "AND actor.user_id=analysis_report_audit_event.actor_user_id "
        "AND profile.keycloak_sub="
        "NULLIF(current_setting('f1.sub',true),'') "
        "AND actor.role IN ('super_admin','enterprise_admin')))"
    )

    admin = (
        f"{_REPORT_DELIVERY_TABLE}.enterprise_id=f1.current_enterprise_id() "
        f"AND f1.session_authorized({_REPORT_DELIVERY_TABLE}.enterprise_id) "
        "AND EXISTS (SELECT 1 FROM f1.enterprise_user AS actor "
        "JOIN f1.user_profile AS profile ON profile.id=actor.user_id "
        f"WHERE actor.enterprise_id={_REPORT_DELIVERY_TABLE}.enterprise_id "
        "AND profile.keycloak_sub=f1.current_sub() "
        "AND actor.role IN ('super_admin','enterprise_admin'))"
    )
    op.execute(f"ALTER TABLE f1.{_REPORT_DELIVERY_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE f1.{_REPORT_DELIVERY_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY analysis_report_generation_delivery_api_select "
        f"ON f1.{_REPORT_DELIVERY_TABLE} FOR SELECT TO f1_api USING ({admin})"
    )
    op.execute(
        f"CREATE POLICY analysis_report_generation_delivery_definer_all "
        f"ON f1.{_REPORT_DELIVERY_TABLE} FOR ALL TO {_REPORT_DEFINER_ROLE} "
        "USING (session_user IN ('f1_api','f1_worker')) "
        "WITH CHECK (session_user IN ('f1_api','f1_worker'))"
    )
    op.execute(
        f"REVOKE ALL ON f1.{_REPORT_DELIVERY_TABLE} FROM PUBLIC,f1_api,f1_worker"
    )
    op.execute(
        f"GRANT SELECT (id,enterprise_id,report_id,job_id,version_id,actor_sub,"
        f"state,attempt,dispatch_token,dispatch_lease_until,reason_code) "
        f"ON f1.{_REPORT_DELIVERY_TABLE} TO f1_api"
    )
    op.execute(
        f"GRANT SELECT,INSERT,UPDATE ON f1.{_REPORT_DELIVERY_TABLE} "
        f"TO {_REPORT_DEFINER_ROLE}"
    )


def _drop_report_generation_delivery_boundary() -> None:
    for signature in (
        "f1.register_analysis_report_generation_delivery(uuid,uuid,text,boolean)",
        "f1.rebind_analysis_report_generation_delivery(uuid,uuid,text)",
        "f1.read_analysis_report_generation_delivery_claim(uuid,uuid)",
        "f1.finish_analysis_report_generation_delivery(uuid,uuid,text,text,integer)",
        "f1.claim_analysis_report_generation_deliveries(integer,integer)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for table in (
        "enterprise_user",
        "user_profile",
        "analysis_report_version",
        "analysis_report_generation_job",
        "analysis_report",
    ):
        policy = {
            "enterprise_user": "analysis_report_delivery_membership_select",
            "user_profile": "analysis_report_delivery_profile_select",
        }.get(table, f"analysis_report_delivery_{table}_select")
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.{table}")
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_delivery_membership_lock "
        "ON f1.enterprise_user"
    )
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_delivery_audit_insert "
        "ON f1.analysis_report_audit_event"
    )
    for table in (
        "analysis_report",
        "analysis_report_generation_job",
        "analysis_report_version",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS analysis_report_delivery_{table}_lock "
            f"ON f1.{table}"
        )
    op.execute(f"DROP TABLE IF EXISTS f1.{_REPORT_DELIVERY_TABLE}")


def _restore_failed_only_analysis_successor_boundary() -> None:
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


def downgrade() -> None:
    _drop_report_generation_delivery_boundary()
    _restore_analysis_report_api_write_boundary()
    op.execute(
        "REVOKE UPDATE (resolved_kind,classification_source,"
        "classification_by_user_id,classification_at,updated_at,status,"
        "confirmed_by_user_id,confirmed_at,policy_source_id,policy_version_id,"
        "confirmation_key_sha256,confirmation_payload_sha256) "
        "ON f1.material_analysis FROM f1_api"
    )
    op.execute("GRANT UPDATE ON f1.material_analysis TO f1_api")
    _restore_failed_only_analysis_successor_boundary()
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.fail_revoked_report_generation(uuid,uuid,text)"
    )
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_revocation_audit_insert "
        "ON f1.analysis_report_audit_event"
    )
    op.execute(
        "DROP POLICY IF EXISTS analysis_report_revocation_enterprise_user_lock "
        "ON f1.enterprise_user"
    )
    for table in (
        "analysis_report_version",
        "analysis_report_generation_job",
        "analysis_report",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS analysis_report_revocation_{table}_update "
            f"ON f1.{table}"
        )
    for table in (
        "enterprise_user",
        "user_profile",
        "analysis_report_version",
        "analysis_report_generation_job",
        "analysis_report",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS analysis_report_revocation_{table}_select "
            f"ON f1.{table}"
        )
    op.execute(
        "REVOKE INSERT (id,enterprise_id,report_id,version_id,actor_user_id,"
        "action,from_status,to_status) ON f1.analysis_report_audit_event "
        f"FROM {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,user_id,role),UPDATE (role) "
        f"ON f1.enterprise_user "
        f"FROM {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (id,keycloak_sub) ON f1.user_profile "
        f"FROM {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "REVOKE SELECT (enterprise_id,id,report_id,status,created_by_user_id),"
        "UPDATE (status,updated_at) ON f1.analysis_report_version "
        f"FROM {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "REVOKE SELECT (enterprise_id,id,report_id,version_id,status,error_reason,"
        "lease_token,lease_until,lease_owner),UPDATE (status,error_reason,lease_token,"
        "lease_until,lease_owner,updated_at) ON f1.analysis_report_generation_job "
        f"FROM {_REPORT_DEFINER_ROLE}"
    )
    op.execute(
        "REVOKE SELECT (enterprise_id,id,current_version_id),UPDATE (updated_at) "
        f"ON f1.analysis_report FROM {_REPORT_DEFINER_ROLE}"
    )
    for table in (
        "crm_account",
        "material_knowledge_scope",
        "document_record",
        "document_version",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS material_ingestion_register_{table}_select "
            f"ON f1.{table}"
        )
    op.execute(
        "DROP POLICY IF EXISTS material_ingestion_register_membership_select "
        "ON f1.enterprise_user"
    )
    op.execute(
        "DROP POLICY IF EXISTS material_ingestion_register_profile_select "
        "ON f1.user_profile"
    )
    op.execute(
        f"REVOKE SELECT (id,keycloak_sub) ON f1.user_profile FROM {_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,user_id,role) ON f1.enterprise_user "
        f"FROM {_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,id,document_record_id) "
        f"ON f1.document_version FROM {_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,id,knowledge_scope_id) "
        f"ON f1.document_record FROM {_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,id,scope_kind,client_account_id) "
        f"ON f1.material_knowledge_scope FROM {_DEFINER_ROLE}"
    )
    op.execute(
        f"REVOKE SELECT (enterprise_id,id,owner_user_id) "
        f"ON f1.crm_account FROM {_DEFINER_ROLE}"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.register_material_ingestion_delivery(uuid,uuid,text,boolean)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.purge_expired_material_ocr_checkpoints(integer)"
    )
    op.execute(
        "DROP POLICY IF EXISTS material_ocr_checkpoint_ingestion_definer_delete "
        "ON f1.material_ocr_checkpoint"
    )
    op.execute(
        "DROP POLICY IF EXISTS material_ocr_checkpoint_ingestion_definer_select "
        "ON f1.material_ocr_checkpoint"
    )
    op.execute(
        f"REVOKE SELECT (id,expires_at),DELETE ON "
        f"f1.material_ocr_checkpoint FROM {_DEFINER_ROLE}"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.read_material_ingestion_delivery_claim(uuid,uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.finish_material_ingestion_delivery(uuid,uuid,text,text,integer)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.claim_material_ingestion_deliveries(integer,integer)"
    )
    op.execute(f"DROP TABLE IF EXISTS f1.{_TABLE}")
