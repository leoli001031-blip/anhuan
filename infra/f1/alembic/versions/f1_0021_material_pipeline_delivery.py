"""Dedicated durable delivery for the local material auto pipeline.

This table is intentionally separate from the legacy upload ``f1.outbox``.
Only durable identities, an authenticated actor ``sub``, and fixed reason
codes are persisted; source names, document bodies, credentials, and queue
payloads never cross this boundary.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0021"
down_revision: str | None = "f1_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "material_pipeline_delivery"
_DEFINER_ROLE = "f1_material_pipeline_definer"


def upgrade() -> None:
    _require_definer_role()
    _table()
    _functions()
    _rls_and_grants()


def _require_definer_role() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = '{_DEFINER_ROLE}'
          ) THEN
            RAISE EXCEPTION 'F1_MATERIAL_PIPELINE_DEFINER_REQUIRED';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname = '{_DEFINER_ROLE}'
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
            WHERE granted_role.rolname = '{_DEFINER_ROLE}'
               OR member_role.rolname = '{_DEFINER_ROLE}'
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
          delivery_kind text NOT NULL DEFAULT 'advance',
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
          CONSTRAINT material_pipeline_delivery_enterprise_id_id_uq
            UNIQUE (enterprise_id, id),
          CONSTRAINT material_pipeline_delivery_identity_uq
            UNIQUE (enterprise_id, document_version_id, delivery_kind),
          CONSTRAINT material_pipeline_delivery_version_fk
            FOREIGN KEY (enterprise_id, document_version_id)
            REFERENCES f1.document_version(enterprise_id, id),
          CONSTRAINT material_pipeline_delivery_kind_ck
            CHECK (delivery_kind = 'advance'),
          CONSTRAINT material_pipeline_delivery_actor_ck CHECK (
            char_length(actor_sub) BETWEEN 1 AND 255
            AND actor_sub = btrim(actor_sub)
            AND actor_sub !~ '[[:cntrl:]]'
          ),
          CONSTRAINT material_pipeline_delivery_state_ck CHECK (
            state IN ('pending','dispatched','retry_wait','done','blocked')
          ),
          CONSTRAINT material_pipeline_delivery_attempt_ck
            CHECK (attempt BETWEEN 0 AND 100),
          CONSTRAINT material_pipeline_delivery_reason_ck CHECK (
            reason_code IS NULL OR reason_code ~ '^[A-Z0-9_]{{1,80}}$'
          ),
          CONSTRAINT material_pipeline_delivery_shape_ck CHECK (
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
        f"CREATE INDEX material_pipeline_delivery_due_idx ON f1.{_TABLE}("
        "state,next_attempt_at,dispatch_lease_until,updated_at,id)"
    )


def _functions() -> None:
    op.execute(
        f"""
        CREATE FUNCTION f1.register_material_pipeline_delivery(
          p_enterprise_id uuid, p_document_version_id uuid,
          p_actor_sub text, p_rearm_terminal boolean
        ) RETURNS TABLE(
          delivery_id uuid, enterprise_id uuid, document_version_id uuid,
          actor_sub text, state text, attempt integer, reason_code text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_id uuid;
        BEGIN
          v_id := md5(
            'material-pipeline:advance:' || p_enterprise_id::text || ':' ||
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
               SELECT 1 FROM f1.resolve_current_enterprises() AS membership
               WHERE membership.enterprise_id=p_enterprise_id
                 AND membership.role IN ('super_admin','enterprise_admin')
             ) THEN
            RAISE EXCEPTION 'MATERIAL_PIPELINE_DELIVERY_REGISTER_INVALID';
          END IF;

          RETURN QUERY
          INSERT INTO f1.{_TABLE} AS delivery (
            id,enterprise_id,document_version_id,delivery_kind,actor_sub,state
          ) VALUES (
            v_id,p_enterprise_id,p_document_version_id,'advance',p_actor_sub,'pending'
          )
          ON CONFLICT ON CONSTRAINT material_pipeline_delivery_identity_uq
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
              AND delivery.delivery_kind='advance';
          END IF;
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION f1.claim_material_pipeline_deliveries(
          p_limit integer, p_lease_seconds integer
        ) RETURNS TABLE(
          delivery_id uuid, enterprise_id uuid, document_version_id uuid,
          actor_sub text, dispatch_token uuid, attempt integer
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
          IF session_user <> 'f1_worker'
             OR p_limit IS NULL OR p_limit < 1 OR p_limit > 100
             OR p_lease_seconds IS NULL
             OR p_lease_seconds < 30 OR p_lease_seconds > 900 THEN
            RAISE EXCEPTION 'MATERIAL_PIPELINE_DELIVERY_CLAIM_INVALID';
          END IF;

          UPDATE f1.{_TABLE} AS exhausted
             SET state = 'blocked',
                 dispatch_token = NULL, dispatch_lease_until = NULL,
                 next_attempt_at = NULL,
                 reason_code = 'MATERIAL_PIPELINE_RETRIES_EXHAUSTED',
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
        CREATE FUNCTION f1.read_material_pipeline_delivery_claim(
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
            RAISE EXCEPTION 'MATERIAL_PIPELINE_DELIVERY_READ_INVALID';
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
        CREATE FUNCTION f1.finish_material_pipeline_delivery(
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
                      OR p_retry_seconds < 1 OR p_retry_seconds > 900))
             OR (p_outcome = 'blocked'
                 AND (p_reason_code IS NULL OR p_retry_seconds IS NOT NULL)) THEN
            RAISE EXCEPTION 'MATERIAL_PIPELINE_DELIVERY_FINISH_INVALID';
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
        "f1.register_material_pipeline_delivery(uuid,uuid,text,boolean)",
        "f1.claim_material_pipeline_deliveries(integer,integer)",
        "f1.read_material_pipeline_delivery_claim(uuid,uuid)",
        "f1.finish_material_pipeline_delivery(uuid,uuid,text,text,integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.register_material_pipeline_delivery(uuid,uuid,text,boolean) "
        "TO f1_api"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.claim_material_pipeline_deliveries(integer,integer) TO f1_worker"
    )
    for signature in (
        "f1.read_material_pipeline_delivery_claim(uuid,uuid)",
        "f1.finish_material_pipeline_delivery(uuid,uuid,text,text,integer)",
    ):
        op.execute(
            f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api, f1_worker"
        )


def _provider_admin() -> str:
    return (
        f"{_TABLE}.enterprise_id = f1.current_enterprise_id() "
        f"AND f1.session_authorized({_TABLE}.enterprise_id) "
        "AND EXISTS ("
        " SELECT 1 FROM f1.enterprise_user AS actor "
        " JOIN f1.user_profile AS profile ON profile.id = actor.user_id "
        f" WHERE actor.enterprise_id = {_TABLE}.enterprise_id "
        " AND profile.keycloak_sub = f1.current_sub() "
        " AND actor.role IN ('super_admin','enterprise_admin')"
        ")"
    )


def _rls_and_grants() -> None:
    admin = _provider_admin()
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {_DEFINER_ROLE}")
    op.execute(f"ALTER TABLE f1.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE f1.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY material_pipeline_delivery_api_select ON f1.{_TABLE} "
        f"FOR SELECT TO f1_api USING ({admin})"
    )
    op.execute(
        f"CREATE POLICY material_pipeline_delivery_definer_all ON f1.{_TABLE} "
        f"FOR ALL TO {_DEFINER_ROLE} "
        "USING (session_user IN ('f1_api','f1_worker')) "
        "WITH CHECK (session_user IN ('f1_api','f1_worker'))"
    )
    op.execute(f"REVOKE ALL ON f1.{_TABLE} FROM PUBLIC, f1_worker")
    op.execute(
        f"GRANT SELECT (id,enterprise_id,document_version_id,delivery_kind,"
        f"actor_sub,state,attempt,reason_code) ON f1.{_TABLE} TO f1_api"
    )
    op.execute(
        f"GRANT SELECT,INSERT,UPDATE ON f1.{_TABLE} TO {_DEFINER_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.register_material_pipeline_delivery(uuid,uuid,text,boolean)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.read_material_pipeline_delivery_claim(uuid,uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.finish_material_pipeline_delivery(uuid,uuid,text,text,integer)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "f1.claim_material_pipeline_deliveries(integer,integer)"
    )
    op.execute(f"DROP TABLE IF EXISTS f1.{_TABLE}")
