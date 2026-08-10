"""F1.1.1 repair: authoritative membership, durable claims, and CAS owners.

This migration is deliberately additive.  ``f1_0003`` may already be applied
to local acceptance databases, so its bytes remain frozen and every repair is
carried by this linear successor.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "f1_0004"
down_revision: str | None = "f1_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PUBLIC_REVOKED_SIGNATURES = (
    "f1.current_task_id()",
    "f1.current_lease_token()",
    "f1.session_authorized(uuid)",
    "f1.resolve_current_enterprises()",
    "f1.create_enterprise_for_current_sub(uuid,text,text,text)",
    "f1.create_invite_for_current_sub(text,text,text,timestamptz)",
    "f1.consume_invite(text,text,text,uuid,timestamptz,text)",
    "f1.claim_upload_task(uuid,text,integer)",
    "f1.renew_upload_lease(uuid,uuid,integer)",
    "f1.claim_pending_dispatch(integer,integer)",
    "f1.complete_dispatch(uuid,uuid,boolean)",
    "f1.claim_qa_request(uuid,text,integer)",
    "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)",
)

DEFINER_ROLES = (
    "f1_auth_definer",
    "f1_identity_read_definer",
    "f1_enterprise_create_definer",
    "f1_invite_create_definer",
    "f1_invite_consume_definer",
    "f1_upload_definer",
    "f1_outbox_definer",
    "f1_qa_definer",
)
DEFINER_POLICIES = (
    ("f111_auth_profile_select", "user_profile"),
    ("f111_auth_membership_select", "enterprise_user"),
    ("f111_auth_upload_select", "upload_task"),
    ("f111_identity_profile_select", "user_profile"),
    ("f111_identity_membership_select", "enterprise_user"),
    ("f111_identity_enterprise_select", "enterprise"),
    ("f111_enterprise_profile_select", "user_profile"),
    ("f111_enterprise_membership_select", "enterprise_user"),
    ("f111_enterprise_insert", "enterprise"),
    ("f111_enterprise_profile_insert", "user_profile"),
    ("f111_enterprise_membership_insert", "enterprise_user"),
    ("f111_enterprise_audit_insert", "audit_log"),
    ("f111_invite_create_profile_select", "user_profile"),
    ("f111_invite_create_membership_select", "enterprise_user"),
    ("f111_invite_create_insert", "invite_jti"),
    ("f111_invite_create_audit_insert", "audit_log"),
    ("f111_invite_consume_select", "invite_jti"),
    ("f111_invite_consume_update", "invite_jti"),
    ("f111_invite_consume_profile_select", "user_profile"),
    ("f111_invite_consume_profile_insert", "user_profile"),
    ("f111_invite_consume_membership_select", "enterprise_user"),
    ("f111_invite_consume_membership_insert", "enterprise_user"),
    ("f111_invite_consume_audit_insert", "audit_log"),
    ("f111_upload_select", "upload_task"),
    ("f111_upload_update", "upload_task"),
    ("f111_outbox_select", "outbox"),
    ("f111_outbox_update", "outbox"),
    ("f111_outbox_upload_select", "upload_task"),
    ("f111_qa_select", "qa_request"),
    ("f111_qa_insert", "qa_request"),
    ("f111_qa_update", "qa_request"),
    ("f111_qa_audit_insert", "audit_log"),
    ("f111_bridge_enterprise_select", "enterprise"),
)


def upgrade() -> None:
    _require_definer_roles()
    _tenant_bound_foreign_keys()
    _durable_owner_columns()
    _context_and_authorization()
    _enterprise_and_membership_writes()
    _invitation_writes()
    _worker_claims()
    _outbox_claims()
    _qa_claims()
    _revoke_public_contract()
    _policies_and_grants()


def _require_definer_roles() -> None:
    role_list = ",".join(f"'{role}'" for role in DEFINER_ROLES)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF (SELECT count(*) FROM pg_roles WHERE rolname IN ({role_list})) <> 8
          THEN RAISE EXCEPTION 'F1_DEFINER_ROLES_REQUIRED'; END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname IN ({role_list})
               AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole
                    OR rolinherit OR rolreplication OR rolbypassrls)
          ) THEN RAISE EXCEPTION 'F1_DEFINER_ROLE_UNSAFE'; END IF;
          IF EXISTS (
            SELECT 1 FROM pg_auth_members AS membership
            JOIN pg_roles AS granted_role ON granted_role.oid = membership.roleid
            JOIN pg_roles AS member_role ON member_role.oid = membership.member
            WHERE granted_role.rolname IN ({role_list})
               OR member_role.rolname IN ({role_list})
          ) THEN RAISE EXCEPTION 'F1_DEFINER_ROLE_MEMBERSHIP_FORBIDDEN'; END IF;
        END
        $$
        """
    )


def _tenant_bound_foreign_keys() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
              FROM f1.upload_task AS t
              JOIN f1.document AS d ON d.id = t.document_id
             WHERE t.document_id IS NOT NULL
               AND t.enterprise_id <> d.enterprise_id
          ) THEN
            RAISE EXCEPTION 'F1_UPLOAD_DOCUMENT_CROSSWIRE_PRESENT';
          END IF;
          IF EXISTS (
            SELECT 1
              FROM f1.outbox AS o
              JOIN f1.upload_task AS t ON t.id = o.task_id
             WHERE o.enterprise_id <> t.enterprise_id
          ) THEN
            RAISE EXCEPTION 'F1_OUTBOX_TASK_CROSSWIRE_PRESENT';
          END IF;
        END
        $$
        """
    )
    op.execute(
        "ALTER TABLE f1.document ADD CONSTRAINT document_enterprise_id_id_uq "
        "UNIQUE (enterprise_id, id)"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_enterprise_id_id_uq "
        "UNIQUE (enterprise_id, id)"
    )
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS upload_task_document_id_fkey")
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_document_enterprise_fk "
        "FOREIGN KEY (enterprise_id, document_id) "
        "REFERENCES f1.document(enterprise_id, id)"
    )
    op.execute("ALTER TABLE f1.outbox DROP CONSTRAINT IF EXISTS outbox_task_id_fkey")
    op.execute(
        "ALTER TABLE f1.outbox ADD CONSTRAINT outbox_task_enterprise_fk "
        "FOREIGN KEY (enterprise_id, task_id) "
        "REFERENCES f1.upload_task(enterprise_id, id)"
    )


def _durable_owner_columns() -> None:
    # Existing local Fixture rows predate the reserve/finalize split and are
    # treated as ready; new rows default to reserved until object verification.
    op.execute(
        "ALTER TABLE f1.upload_task "
        "ADD COLUMN object_state text NOT NULL DEFAULT 'ready', "
        "ADD COLUMN source_etag text, "
        "ADD COLUMN source_size bigint, "
        "ADD COLUMN lease_token uuid, "
        "ADD COLUMN lease_owner text, "
        "ADD COLUMN lease_acquired_at timestamptz, "
        "ADD COLUMN next_attempt_at timestamptz"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ALTER COLUMN object_state SET DEFAULT 'reserved'"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_object_state_ck "
        "CHECK (object_state IN ('reserved','ready','write_failed'))"
    )
    op.execute(
        "ALTER TABLE f1.upload_task ADD CONSTRAINT upload_task_source_ready_ck "
        "CHECK (object_state <> 'ready' OR (source_size IS NULL OR source_size >= 0))"
    )

    op.execute(
        "ALTER TABLE f1.outbox "
        "ADD COLUMN rq_job_id text, "
        "ADD COLUMN dispatch_token uuid, "
        "ADD COLUMN dispatch_lease_until timestamptz, "
        "ADD COLUMN dispatch_attempt integer NOT NULL DEFAULT 0"
    )
    # FORCE RLS applies to the table owner.  This transaction-only policy is
    # deliberately limited to the one null-to-stable-id backfill and is
    # removed before any runtime function becomes available.
    op.execute(
        """
        CREATE POLICY f111_outbox_backfill ON f1.outbox
        FOR UPDATE TO f0d_migration
        USING (session_user = 'f0d_migration' AND rq_job_id IS NULL)
        WITH CHECK (session_user = 'f0d_migration' AND rq_job_id IS NOT NULL)
        """
    )
    op.execute(
        "UPDATE f1.outbox SET rq_job_id = CASE event_type "
        "WHEN 'upload.dispatched' THEN 'f1-upload-' "
        "WHEN 'upload.indexing' THEN 'f1-indexing-' "
        "WHEN 'upload.failed' THEN 'f1-failed-' "
        "WHEN 'upload.indexed' THEN 'f1-indexed-' "
        "ELSE 'f1-event-' END || task_id::text "
        "WHERE rq_job_id IS NULL"
    )
    op.execute("DROP POLICY f111_outbox_backfill ON f1.outbox")
    op.execute("ALTER TABLE f1.outbox ALTER COLUMN rq_job_id SET NOT NULL")
    op.execute(
        "ALTER TABLE f1.outbox ADD CONSTRAINT outbox_rq_job_id_uq UNIQUE (rq_job_id)"
    )

    op.execute(
        "ALTER TABLE f1.qa_request "
        "ADD COLUMN owner_token uuid, "
        "ADD COLUMN owner_lease_until timestamptz, "
        "ADD COLUMN attempt integer NOT NULL DEFAULT 0"
    )
    op.execute(
        """
        CREATE POLICY f111_qa_backfill ON f1.qa_request
        FOR UPDATE TO f0d_migration
        USING (
          session_user = 'f0d_migration' AND status = 'accepted'
          AND owner_token IS NULL
        )
        WITH CHECK (
          session_user = 'f0d_migration' AND status = 'accepted'
          AND owner_token IS NOT NULL AND owner_lease_until IS NOT NULL
        )
        """
    )
    op.execute(
        "UPDATE f1.qa_request SET owner_token = gen_random_uuid(), "
        "owner_lease_until = statement_timestamp() "
        "WHERE status = 'accepted' AND owner_token IS NULL"
    )
    op.execute("DROP POLICY f111_qa_backfill ON f1.qa_request")
    op.execute(
        "ALTER TABLE f1.qa_request ADD CONSTRAINT qa_request_state_ck CHECK ("
        "(status = 'accepted' AND owner_token IS NOT NULL "
        " AND response_encrypted IS NULL AND response_sha256 IS NULL "
        " AND refusal_reason IS NULL AND completed_at IS NULL) OR "
        "(status = 'done' AND owner_token IS NULL AND owner_lease_until IS NULL "
        " AND response_encrypted IS NOT NULL AND response_sha256 IS NOT NULL "
        " AND refusal_reason IS NULL AND completed_at IS NOT NULL) OR "
        "(status = 'refused' AND owner_token IS NULL AND owner_lease_until IS NULL "
        " AND response_encrypted IS NULL AND response_sha256 IS NULL "
        " AND refusal_reason IS NOT NULL AND completed_at IS NOT NULL)) NOT VALID"
    )


def _context_and_authorization() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.current_task_id()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT CASE
            WHEN current_setting('f1.task_id', true)
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN current_setting('f1.task_id', true)::uuid ELSE NULL END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.current_lease_token()
        RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
          SELECT CASE
            WHEN current_setting('f1.lease_token', true)
              ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN current_setting('f1.lease_token', true)::uuid ELSE NULL END
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION f1.session_authorized(p_enterprise_id uuid)
        RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog AS $$
          SELECT CASE
            WHEN session_user = 'f1_api' THEN
              f1.current_sub() IS NOT NULL AND EXISTS (
                SELECT 1 FROM f1.enterprise_user AS eu
                JOIN f1.user_profile AS up ON up.id = eu.user_id
                WHERE eu.enterprise_id = p_enterprise_id
                  AND up.keycloak_sub = f1.current_sub())
            WHEN session_user = 'f1_worker' THEN
              EXISTS (
                SELECT 1 FROM f1.upload_task AS t
                WHERE t.id = f1.current_task_id()
                  AND t.enterprise_id = p_enterprise_id
                  AND t.lease_token = f1.current_lease_token()
                  AND t.lease_until > statement_timestamp()
                  AND t.status IN ('scanning','indexing'))
            ELSE false
          END
        $$
        """
    )
    for signature in (
        "f1.current_task_id()",
        "f1.current_lease_token()",
        "f1.session_authorized(uuid)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.current_task_id() TO f1_worker")
    op.execute("GRANT EXECUTE ON FUNCTION f1.current_lease_token() TO f1_worker")
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.session_authorized(uuid) TO f1_api, f1_worker"
    )

    op.execute("DROP FUNCTION IF EXISTS f1.resolve_enterprise_for_sub(text)")
    op.execute(
        """
        CREATE FUNCTION f1.resolve_current_enterprises()
        RETURNS TABLE(enterprise_id uuid, name text, role text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
          SELECT e.id, e.name, eu.role
            FROM f1.user_profile AS up
            JOIN f1.enterprise_user AS eu ON eu.user_id = up.id
            JOIN f1.enterprise AS e ON e.id = eu.enterprise_id
           WHERE up.keycloak_sub = f1.current_sub()
           ORDER BY e.id
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION f1.resolve_current_enterprises() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION f1.resolve_current_enterprises() TO f1_api")


def _enterprise_and_membership_writes() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.create_enterprise_for_current_sub(
          p_enterprise_id uuid, p_name text, p_license_no text, p_email text
        ) RETURNS uuid
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_sub text; v_profile uuid;
        BEGIN
          IF p_enterprise_id IS NULL THEN
            RAISE EXCEPTION 'ENTERPRISE_ID_REQUIRED';
          END IF;
          PERFORM set_config(
            'f1.enterprise_create_target', p_enterprise_id::text, true
          );
          v_sub := f1.current_sub();
          IF v_sub IS NULL OR NOT EXISTS (
            SELECT 1 FROM f1.enterprise_user AS eu
            JOIN f1.user_profile AS up ON up.id = eu.user_id
            WHERE up.keycloak_sub = v_sub AND eu.role = 'super_admin'
          ) THEN RAISE EXCEPTION 'PLATFORM_ADMIN_REQUIRED'; END IF;
          INSERT INTO f1.enterprise(id, name, license_no)
          VALUES (p_enterprise_id, p_name, p_license_no);
          SELECT id INTO v_profile FROM f1.user_profile WHERE keycloak_sub = v_sub;
          IF v_profile IS NULL THEN
            INSERT INTO f1.user_profile(id, keycloak_sub, email)
            VALUES (gen_random_uuid(), v_sub, p_email) RETURNING id INTO v_profile;
          END IF;
          INSERT INTO f1.enterprise_user(id, enterprise_id, user_id, role)
          VALUES (gen_random_uuid(), p_enterprise_id, v_profile, 'super_admin');
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), p_enterprise_id, v_sub, 'enterprise.create',
                  'enterprise', p_enterprise_id::text, 'success');
          RETURN p_enterprise_id;
        END $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.create_enterprise_for_current_sub(uuid,text,text,text) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.create_enterprise_for_current_sub(uuid,text,text,text) "
        "TO f1_api"
    )


def _invitation_writes() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.create_invite_for_current_sub(
          p_jti text, p_email text, p_role text, p_expires_at timestamptz
        ) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_eid uuid; v_sub text; v_actor_role text;
        BEGIN
          v_eid := f1.current_enterprise_id(); v_sub := f1.current_sub();
          IF v_eid IS NULL OR p_jti IS NULL THEN
            RAISE EXCEPTION 'INVITE_CONTEXT_REQUIRED';
          END IF;
          PERFORM set_config('f1.invite_target_jti', p_jti, true);
          SELECT eu.role INTO v_actor_role
            FROM f1.enterprise_user AS eu
            JOIN f1.user_profile AS up ON up.id = eu.user_id
           WHERE eu.enterprise_id = v_eid AND up.keycloak_sub = v_sub;
          IF v_actor_role IS NULL THEN RAISE EXCEPTION 'INVITE_FORBIDDEN'; END IF;
          IF NOT (
            (v_actor_role = 'super_admin' AND p_role IN
              ('enterprise_admin','plant_admin','partner','auditor')) OR
            (v_actor_role = 'enterprise_admin' AND p_role IN
              ('plant_admin','partner','auditor')) OR
            (v_actor_role = 'plant_admin' AND p_role IN ('partner','auditor'))
          ) THEN RAISE EXCEPTION 'INVITE_ROLE_ESCALATION'; END IF;
          INSERT INTO f1.invite_jti(jti, enterprise_id, email, role, expires_at)
          VALUES (p_jti, v_eid, lower(p_email), p_role, p_expires_at);
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), v_eid, v_sub, 'invite.create', 'invite', p_jti, 'success');
          RETURN true;
        END $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION f1.create_invite_for_current_sub(text,text,text,timestamptz) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.create_invite_for_current_sub(text,text,text,timestamptz) "
        "TO f1_api"
    )

    op.execute(
        "DROP FUNCTION IF EXISTS f1.consume_invite(text,text,text,text,uuid,timestamptz)"
    )
    op.execute(
        """
        CREATE FUNCTION f1.consume_invite(
          p_jti text, p_email text, p_role text, p_enterprise_id uuid,
          p_expires_at timestamptz, p_oidc_email text
        ) RETURNS TABLE(
          out_jti text, out_enterprise_id uuid, out_email text, out_role text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_row f1.invite_jti; v_sub text; v_profile uuid;
        BEGIN
          IF p_jti IS NULL OR p_enterprise_id IS NULL THEN
            RAISE EXCEPTION 'INVITE_CONTEXT_REQUIRED';
          END IF;
          PERFORM set_config('f1.invite_target_jti', p_jti, true);
          PERFORM set_config(
            'f1.invite_target_enterprise', p_enterprise_id::text, true
          );
          v_sub := f1.current_sub();
          IF v_sub IS NULL THEN RAISE EXCEPTION 'OIDC_IDENTITY_REQUIRED'; END IF;
          SELECT * INTO v_row FROM f1.invite_jti AS i
           WHERE i.jti = p_jti FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'INVITE_NOT_FOUND'; END IF;
          IF v_row.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'INVITE_ALREADY_USED'; END IF;
          IF v_row.enterprise_id <> p_enterprise_id
             OR lower(v_row.email) <> lower(p_email)
             OR v_row.role <> p_role
             OR extract(epoch FROM v_row.expires_at)::bigint <>
                extract(epoch FROM p_expires_at)::bigint
          THEN RAISE EXCEPTION 'INVITE_CLAIMS_MISMATCH'; END IF;
          IF p_oidc_email IS NULL OR lower(p_oidc_email) <> lower(v_row.email)
          THEN RAISE EXCEPTION 'INVITE_IDENTITY_MISMATCH'; END IF;
          IF v_row.expires_at <= statement_timestamp()
          THEN RAISE EXCEPTION 'INVITE_EXPIRED'; END IF;
          IF EXISTS (
            SELECT 1 FROM f1.enterprise_user AS eu
            JOIN f1.user_profile AS up ON up.id = eu.user_id
            WHERE eu.enterprise_id = v_row.enterprise_id
              AND up.keycloak_sub = v_sub
          ) THEN RAISE EXCEPTION 'MEMBERSHIP_ALREADY_EXISTS'; END IF;
          UPDATE f1.invite_jti SET consumed_by_sub = v_sub,
                 consumed_at = statement_timestamp()
           WHERE jti = p_jti AND consumed_at IS NULL;
          IF NOT FOUND THEN RAISE EXCEPTION 'INVITE_ALREADY_USED'; END IF;
          SELECT id INTO v_profile FROM f1.user_profile WHERE keycloak_sub = v_sub;
          IF v_profile IS NULL THEN
            INSERT INTO f1.user_profile(id, keycloak_sub, email)
            VALUES (gen_random_uuid(), v_sub, lower(p_oidc_email))
            RETURNING id INTO v_profile;
          END IF;
          INSERT INTO f1.enterprise_user(id, enterprise_id, user_id, role)
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_profile, v_row.role);
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_sub,
                  'invite.consume', 'invite', p_jti, 'success');
          RETURN QUERY SELECT v_row.jti, v_row.enterprise_id, v_row.email, v_row.role;
        END $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "f1.consume_invite(text,text,text,uuid,timestamptz,text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "f1.consume_invite(text,text,text,uuid,timestamptz,text) TO f1_api"
    )


def _worker_claims() -> None:
    op.execute("DROP FUNCTION IF EXISTS f1.task_enterprise(uuid)")
    op.execute(
        """
        CREATE FUNCTION f1.claim_upload_task(
          p_task_id uuid, p_worker_id text, p_lease_seconds integer
        ) RETURNS TABLE(
          enterprise_id uuid, task_id uuid, lease_token uuid,
          document_id uuid, object_key text, content_sha256 text,
          source_etag text, source_size bigint
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE v_token uuid := gen_random_uuid();
        BEGIN
          IF p_lease_seconds < 1 OR p_lease_seconds > 900
          THEN RAISE EXCEPTION 'LEASE_DURATION_INVALID'; END IF;
          PERFORM set_config('f1.upload_target_task', p_task_id::text, true);
          RETURN QUERY
          UPDATE f1.upload_task AS t
             SET status = 'scanning', lease_token = v_token,
                 lease_owner = p_worker_id,
                 lease_acquired_at = statement_timestamp(),
                 lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 attempt = attempt + 1, updated_at = statement_timestamp()
           WHERE t.id = p_task_id AND t.object_state = 'ready'
             AND t.status IN ('pending','scanning','indexing')
             AND (t.next_attempt_at IS NULL OR t.next_attempt_at <= statement_timestamp())
             AND (t.lease_until IS NULL OR t.lease_until <= statement_timestamp())
          RETURNING t.enterprise_id, t.id, t.lease_token, t.document_id,
                    t.object_key, t.content_sha256, t.source_etag, t.source_size;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.renew_upload_lease(
          p_task_id uuid, p_token uuid, p_lease_seconds integer
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          IF p_lease_seconds < 1 OR p_lease_seconds > 900 THEN RETURN false; END IF;
          PERFORM set_config('f1.upload_target_task', p_task_id::text, true);
          UPDATE f1.upload_task SET lease_until = statement_timestamp()
              + make_interval(secs => p_lease_seconds),
              updated_at = statement_timestamp()
           WHERE id = p_task_id AND lease_token = p_token
             AND lease_until > statement_timestamp()
             AND status IN ('scanning','indexing');
          GET DIAGNOSTICS v_count = ROW_COUNT; RETURN v_count = 1;
        END $$
        """
    )
    for signature in (
        "f1.claim_upload_task(uuid,text,integer)",
        "f1.renew_upload_lease(uuid,uuid,integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_worker")


def _outbox_claims() -> None:
    op.execute("DROP FUNCTION IF EXISTS f1.pending_dispatch_tasks()")
    op.execute(
        """
        CREATE FUNCTION f1.claim_pending_dispatch(
          p_limit integer, p_lease_seconds integer
        ) RETURNS TABLE(
          outbox_id uuid, enterprise_id uuid, task_id uuid,
          rq_job_id text, dispatch_token uuid
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        BEGIN
          IF p_limit < 1 OR p_limit > 100 OR p_lease_seconds < 1
             OR p_lease_seconds > 900 THEN
            RAISE EXCEPTION 'DISPATCH_CLAIM_INVALID';
          END IF;
          RETURN QUERY
          WITH candidates AS (
            SELECT o.id FROM f1.outbox AS o
            JOIN f1.upload_task AS t
              ON t.enterprise_id = o.enterprise_id AND t.id = o.task_id
            WHERE o.event_type = 'upload.dispatched'
              AND t.object_state = 'ready'
              AND (
                t.status = 'pending' OR
                (t.status IN ('scanning','indexing')
                 AND (t.lease_until IS NULL
                      OR t.lease_until <= statement_timestamp()))
              )
              AND (o.state = 'pending' OR
                   (o.state = 'dispatched' AND
                    (o.dispatch_lease_until IS NULL OR
                     o.dispatch_lease_until <= statement_timestamp())))
            ORDER BY o.created_at, o.id
            LIMIT p_limit FOR UPDATE OF o SKIP LOCKED
          )
          UPDATE f1.outbox AS o SET state = 'dispatched',
                 dispatch_token = gen_random_uuid(),
                 dispatch_lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 dispatch_attempt = dispatch_attempt + 1,
                 dispatched_at = statement_timestamp()
            FROM candidates AS c WHERE o.id = c.id
          RETURNING o.id, o.enterprise_id, o.task_id,
                    o.rq_job_id, o.dispatch_token;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.complete_dispatch(
          p_outbox_id uuid, p_token uuid, p_success boolean
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE v_count integer;
        BEGIN
          PERFORM set_config('f1.outbox_target_id', p_outbox_id::text, true);
          UPDATE f1.outbox SET
            state = CASE WHEN p_success THEN 'dispatched' ELSE 'pending' END,
            dispatch_token = CASE WHEN p_success THEN dispatch_token ELSE NULL END,
            dispatch_lease_until = CASE WHEN p_success THEN dispatch_lease_until ELSE NULL END
          WHERE id = p_outbox_id AND dispatch_token = p_token
            AND dispatch_lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT; RETURN v_count = 1;
        END $$
        """
    )
    for signature in (
        "f1.claim_pending_dispatch(integer,integer)",
        "f1.complete_dispatch(uuid,uuid,boolean)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_worker")


def _qa_claims() -> None:
    op.execute(
        """
        CREATE FUNCTION f1.claim_qa_request(
          p_request_id uuid, p_question_sha256 text, p_lease_seconds integer
        ) RETURNS TABLE(
          claim_state text, owner_token uuid, attempt integer, status text,
          refusal_reason text, response_encrypted bytea, response_sha256 text
        ) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE
          v_eid uuid; v_token uuid := gen_random_uuid();
          v_row f1.qa_request; v_attempt integer;
        BEGIN
          v_eid := f1.current_enterprise_id();
          IF v_eid IS NULL OR p_request_id IS NULL
             OR p_question_sha256 !~ '^[0-9a-f]{64}$'
             OR p_lease_seconds < 1 OR p_lease_seconds > 900
          THEN RAISE EXCEPTION 'QA_CLAIM_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);

          INSERT INTO f1.qa_request(
            request_id, enterprise_id, question_sha256, status, owner_token,
            owner_lease_until, attempt
          ) VALUES (
            p_request_id, v_eid, p_question_sha256, 'accepted', v_token,
            statement_timestamp() + make_interval(secs => p_lease_seconds), 1
          ) ON CONFLICT (request_id) DO NOTHING;
          IF FOUND THEN
            RETURN QUERY SELECT 'CLAIMED'::text, v_token, 1, 'accepted'::text,
                                NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;

          SELECT * INTO v_row FROM f1.qa_request AS q
           WHERE q.request_id = p_request_id FOR UPDATE;
          -- A missing row after a uniqueness conflict is never reported as
          -- in-progress: fail closed without leaking which tenant owns it.
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'CONFLICT'::text, NULL::uuid, 0, NULL::text,
                                NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          IF v_row.enterprise_id <> v_eid
             OR v_row.question_sha256 <> p_question_sha256 THEN
            RETURN QUERY SELECT 'CONFLICT'::text, NULL::uuid, v_row.attempt,
                                NULL::text, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          IF v_row.status IN ('done','refused') THEN
            RETURN QUERY SELECT 'REPLAY'::text, NULL::uuid, v_row.attempt,
                                v_row.status, v_row.refusal_reason,
                                v_row.response_encrypted, v_row.response_sha256;
            RETURN;
          END IF;
          IF v_row.status <> 'accepted'
             OR v_row.owner_lease_until > statement_timestamp() THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
                                v_row.status, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;

          UPDATE f1.qa_request AS q
             SET owner_token = v_token,
                 owner_lease_until = statement_timestamp()
                   + make_interval(secs => p_lease_seconds),
                 attempt = q.attempt + 1
           WHERE q.request_id = p_request_id
             AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.status = 'accepted'
             AND (q.owner_lease_until IS NULL
                  OR q.owner_lease_until <= statement_timestamp())
          RETURNING q.attempt INTO v_attempt;
          IF NOT FOUND THEN
            RETURN QUERY SELECT 'IN_PROGRESS'::text, NULL::uuid, v_row.attempt,
                                'accepted'::text, NULL::text, NULL::bytea, NULL::text;
            RETURN;
          END IF;
          RETURN QUERY SELECT 'CLAIMED'::text, v_token, v_attempt,
                              'accepted'::text, NULL::text, NULL::bytea, NULL::text;
        END $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION f1.complete_qa_request(
          p_request_id uuid, p_owner_token uuid, p_question_sha256 text,
          p_status text, p_response_encrypted bytea, p_response_sha256 text,
          p_refusal_reason text
        ) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog AS $$
        DECLARE v_eid uuid; v_sub text; v_count integer;
        BEGIN
          v_eid := f1.current_enterprise_id(); v_sub := f1.current_sub();
          IF v_eid IS NULL OR v_sub IS NULL OR p_request_id IS NULL
             OR p_owner_token IS NULL OR p_question_sha256 !~ '^[0-9a-f]{64}$'
          THEN RAISE EXCEPTION 'QA_COMPLETE_INVALID'; END IF;
          IF NOT (
            (p_status = 'done' AND p_response_encrypted IS NOT NULL
             AND p_response_sha256 ~ '^[0-9a-f]{64}$'
             AND p_refusal_reason IS NULL) OR
            (p_status = 'refused' AND p_response_encrypted IS NULL
             AND p_response_sha256 IS NULL AND p_refusal_reason IS NOT NULL)
          ) THEN RAISE EXCEPTION 'QA_OUTCOME_STATE_INVALID'; END IF;
          PERFORM set_config('f1.qa_target_request', p_request_id::text, true);
          UPDATE f1.qa_request AS q
             SET status = p_status, owner_token = NULL, owner_lease_until = NULL,
                 response_encrypted = p_response_encrypted,
                 response_sha256 = p_response_sha256,
                 refusal_reason = p_refusal_reason,
                 completed_at = statement_timestamp()
           WHERE q.request_id = p_request_id AND q.enterprise_id = v_eid
             AND q.question_sha256 = p_question_sha256
             AND q.status = 'accepted' AND q.owner_token = p_owner_token
             AND q.owner_lease_until > statement_timestamp();
          GET DIAGNOSTICS v_count = ROW_COUNT;
          IF v_count <> 1 THEN RETURN false; END IF;
          INSERT INTO f1.audit_log(
            id, enterprise_id, user_sub, action, resource_type, resource_id, result
          ) VALUES (
            gen_random_uuid(), v_eid, v_sub, 'qa.complete', 'qa_request',
            p_request_id::text, p_status
          );
          RETURN true;
        END $$
        """
    )
    for signature in (
        "f1.claim_qa_request(uuid,text,integer)",
        "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_api")


def _revoke_public_contract() -> None:
    # One complete list makes omission machine-checkable even though the
    # individual helpers also revoke before granting their runtime roles.
    for signature in PUBLIC_REVOKED_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def _policies_and_grants() -> None:
    op.execute("ALTER TABLE f1.user_profile ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.user_profile FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.enterprise_user ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.enterprise_user FORCE ROW LEVEL SECURITY")
    # f1_0002/0003 gave the migration owner null-context global reads and
    # writes.  Remove every one.  Frozen bridge functions retain only the
    # current-enterprise SELECT policy created below.
    for table in (
        "enterprise", "plant", "document", "audit_log", "upload_task",
        "outbox", "qa_request", "invite_jti",
    ):
        op.execute(f"DROP POLICY IF EXISTS migration_f1_read ON f1.{table}")
    op.execute("DROP POLICY IF EXISTS migration_f1_invite_consume ON f1.invite_jti")
    op.execute("DROP POLICY IF EXISTS migration_f1_audit_insert ON f1.audit_log")
    for policy, table in DEFINER_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.{table}")

    # Authorization: an API identity may see only its own profile/membership
    # at the selected enterprise; a worker may see only its live task+token.
    op.execute(
        """
        CREATE POLICY f111_auth_profile_select ON f1.user_profile
        FOR SELECT TO f1_auth_definer
        USING (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_auth_membership_select ON f1.enterprise_user
        FOR SELECT TO f1_auth_definer
        USING (
          session_user = 'f1_api'
          AND enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_auth_upload_select ON f1.upload_task
        FOR SELECT TO f1_auth_definer
        USING (
          session_user = 'f1_worker'
          AND enterprise_id = f1.current_enterprise_id()
          AND id = f1.current_task_id()
          AND lease_token = f1.current_lease_token()
          AND lease_until > statement_timestamp()
          AND status IN ('scanning','indexing')
        )
        """
    )

    # Identity lookup has no enterprise GUC by design.  Its policies walk only
    # memberships attached to the authenticated, transaction-local OIDC sub.
    op.execute(
        """
        CREATE POLICY f111_identity_profile_select ON f1.user_profile
        FOR SELECT TO f1_identity_read_definer
        USING (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_identity_membership_select ON f1.enterprise_user
        FOR SELECT TO f1_identity_read_definer
        USING (
          session_user = 'f1_api' AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_identity_enterprise_select ON f1.enterprise
        FOR SELECT TO f1_identity_read_definer
        USING (
          session_user = 'f1_api' AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS eu
            JOIN f1.user_profile AS up ON up.id = eu.user_id
             WHERE eu.enterprise_id = enterprise.id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )

    # Enterprise mint is an exact target set by the definer before DML.  The
    # actor must already own a super_admin membership; the function itself
    # performs that role test while these policies constrain row visibility.
    op.execute(
        """
        CREATE POLICY f111_enterprise_profile_select ON f1.user_profile
        FOR SELECT TO f1_enterprise_create_definer
        USING (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_enterprise_membership_select ON f1.enterprise_user
        FOR SELECT TO f1_enterprise_create_definer
        USING (
          session_user = 'f1_api' AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_enterprise_insert ON f1.enterprise
        FOR INSERT TO f1_enterprise_create_definer
        WITH CHECK (
          session_user = 'f1_api' AND f1.current_sub() IS NOT NULL
          AND id::text = current_setting('f1.enterprise_create_target', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_enterprise_profile_insert ON f1.user_profile
        FOR INSERT TO f1_enterprise_create_definer
        WITH CHECK (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_enterprise_membership_insert ON f1.enterprise_user
        FOR INSERT TO f1_enterprise_create_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id::text = current_setting(
            'f1.enterprise_create_target', true
          )
          AND role = 'super_admin'
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_enterprise_audit_insert ON f1.audit_log
        FOR INSERT TO f1_enterprise_create_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id::text = current_setting(
            'f1.enterprise_create_target', true
          )
          AND user_sub = f1.current_sub() AND action = 'enterprise.create'
          AND resource_type = 'enterprise'
          AND resource_id = current_setting('f1.enterprise_create_target', true)
          AND result = 'success'
        )
        """
    )

    # Invite creation stays within the selected membership and target JTI.
    op.execute(
        """
        CREATE POLICY f111_invite_create_profile_select ON f1.user_profile
        FOR SELECT TO f1_invite_create_definer
        USING (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_create_membership_select ON f1.enterprise_user
        FOR SELECT TO f1_invite_create_definer
        USING (
          session_user = 'f1_api'
          AND enterprise_id = f1.current_enterprise_id()
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_create_insert ON f1.invite_jti
        FOR INSERT TO f1_invite_create_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id = f1.current_enterprise_id()
          AND jti = current_setting('f1.invite_target_jti', true)
          AND consumed_at IS NULL AND consumed_by_sub IS NULL
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_create_audit_insert ON f1.audit_log
        FOR INSERT TO f1_invite_create_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id = f1.current_enterprise_id()
          AND user_sub = f1.current_sub() AND action = 'invite.create'
          AND resource_type = 'invite'
          AND resource_id = current_setting('f1.invite_target_jti', true)
          AND result = 'success'
        )
        """
    )

    # Invite consume is the only pre-membership path.  It can see and update
    # exactly one ledger row selected by both signed JTI and enterprise claim.
    op.execute(
        """
        CREATE POLICY f111_invite_consume_select ON f1.invite_jti
        FOR SELECT TO f1_invite_consume_definer
        USING (
          session_user = 'f1_api'
          AND jti = current_setting('f1.invite_target_jti', true)
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_update ON f1.invite_jti
        FOR UPDATE TO f1_invite_consume_definer
        USING (
          session_user = 'f1_api'
          AND jti = current_setting('f1.invite_target_jti', true)
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
        )
        WITH CHECK (
          session_user = 'f1_api'
          AND jti = current_setting('f1.invite_target_jti', true)
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
          AND consumed_by_sub = f1.current_sub() AND consumed_at IS NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_profile_select ON f1.user_profile
        FOR SELECT TO f1_invite_consume_definer
        USING (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_profile_insert ON f1.user_profile
        FOR INSERT TO f1_invite_consume_definer
        WITH CHECK (session_user = 'f1_api' AND keycloak_sub = f1.current_sub())
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_membership_select ON f1.enterprise_user
        FOR SELECT TO f1_invite_consume_definer
        USING (
          session_user = 'f1_api'
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_membership_insert ON f1.enterprise_user
        FOR INSERT TO f1_invite_consume_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
          AND EXISTS (
            SELECT 1 FROM f1.user_profile AS up
             WHERE up.id = enterprise_user.user_id
               AND up.keycloak_sub = f1.current_sub()
          )
          AND EXISTS (
            SELECT 1 FROM f1.invite_jti AS i
             WHERE i.jti = current_setting('f1.invite_target_jti', true)
               AND i.enterprise_id = enterprise_user.enterprise_id
               AND i.role = enterprise_user.role
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_invite_consume_audit_insert ON f1.audit_log
        FOR INSERT TO f1_invite_consume_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id::text = current_setting(
            'f1.invite_target_enterprise', true
          )
          AND user_sub = f1.current_sub() AND action = 'invite.consume'
          AND resource_type = 'invite'
          AND resource_id = current_setting('f1.invite_target_jti', true)
          AND result = 'success'
        )
        """
    )

    # Worker claim domains are cross-tenant only inside their exact functions.
    op.execute(
        """
        CREATE POLICY f111_upload_select ON f1.upload_task
        FOR SELECT TO f1_upload_definer
        USING (
          session_user = 'f1_worker'
          AND id::text = current_setting('f1.upload_target_task', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_upload_update ON f1.upload_task
        FOR UPDATE TO f1_upload_definer
        USING (
          session_user = 'f1_worker'
          AND id::text = current_setting('f1.upload_target_task', true)
        )
        WITH CHECK (
          session_user = 'f1_worker'
          AND id::text = current_setting('f1.upload_target_task', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_outbox_select ON f1.outbox
        FOR SELECT TO f1_outbox_definer
        USING (session_user = 'f1_worker' AND event_type = 'upload.dispatched')
        """
    )
    op.execute(
        """
        CREATE POLICY f111_outbox_update ON f1.outbox
        FOR UPDATE TO f1_outbox_definer
        USING (session_user = 'f1_worker' AND event_type = 'upload.dispatched')
        WITH CHECK (session_user = 'f1_worker' AND event_type = 'upload.dispatched')
        """
    )
    op.execute(
        """
        CREATE POLICY f111_outbox_upload_select ON f1.upload_task
        FOR SELECT TO f1_outbox_definer
        USING (
          session_user = 'f1_worker' AND object_state = 'ready'
          AND status IN ('pending','scanning','indexing')
        )
        """
    )

    # QA detects a global request-id collision without exposing its tenant or
    # body.  The target GUC exists only inside the exact claim/complete calls.
    op.execute(
        """
        CREATE POLICY f111_qa_select ON f1.qa_request
        FOR SELECT TO f1_qa_definer
        USING (
          session_user = 'f1_api'
          AND request_id::text = current_setting('f1.qa_target_request', true)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_qa_insert ON f1.qa_request
        FOR INSERT TO f1_qa_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND request_id::text = current_setting('f1.qa_target_request', true)
          AND enterprise_id = f1.current_enterprise_id()
          AND status = 'accepted' AND owner_token IS NOT NULL
          AND owner_lease_until > statement_timestamp()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_qa_update ON f1.qa_request
        FOR UPDATE TO f1_qa_definer
        USING (
          session_user = 'f1_api'
          AND request_id::text = current_setting('f1.qa_target_request', true)
        )
        WITH CHECK (
          session_user = 'f1_api'
          AND request_id::text = current_setting('f1.qa_target_request', true)
          AND enterprise_id = f1.current_enterprise_id()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY f111_qa_audit_insert ON f1.audit_log
        FOR INSERT TO f1_qa_definer
        WITH CHECK (
          session_user = 'f1_api'
          AND enterprise_id = f1.current_enterprise_id()
          AND user_sub = f1.current_sub() AND action = 'qa.complete'
          AND resource_type = 'qa_request'
          AND resource_id = current_setting('f1.qa_target_request', true)
          AND result IN ('done','refused')
        )
        """
    )

    op.execute(
        """
        CREATE POLICY f111_bridge_enterprise_select ON f1.enterprise
        FOR SELECT TO f0d_migration
        USING (
          session_user IN ('f1_api','f1_worker')
          AND id = f1.current_enterprise_id()
        )
        """
    )

    # Reset and grant only the exact table/function surface for each domain.
    role_list = ", ".join(DEFINER_ROLES)
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA f1 FROM {role_list}"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA f1 FROM {role_list}")
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {role_list}")
    op.execute(
        "GRANT SELECT ON f1.user_profile, f1.enterprise_user, f1.upload_task "
        "TO f1_auth_definer"
    )
    op.execute(
        "GRANT SELECT ON f1.user_profile, f1.enterprise_user, f1.enterprise "
        "TO f1_identity_read_definer"
    )
    op.execute(
        "GRANT SELECT ON f1.user_profile, f1.enterprise_user "
        "TO f1_enterprise_create_definer"
    )
    op.execute(
        "GRANT INSERT ON f1.enterprise, f1.user_profile, f1.enterprise_user, "
        "f1.audit_log TO f1_enterprise_create_definer"
    )
    op.execute(
        "GRANT SELECT ON f1.user_profile, f1.enterprise_user "
        "TO f1_invite_create_definer"
    )
    op.execute(
        "GRANT INSERT ON f1.invite_jti, f1.audit_log TO f1_invite_create_definer"
    )
    op.execute(
        "GRANT SELECT ON f1.invite_jti, f1.user_profile, f1.enterprise_user "
        "TO f1_invite_consume_definer"
    )
    op.execute(
        "GRANT UPDATE (consumed_by_sub, consumed_at) ON f1.invite_jti "
        "TO f1_invite_consume_definer"
    )
    op.execute(
        "GRANT INSERT ON f1.user_profile, f1.enterprise_user, f1.audit_log "
        "TO f1_invite_consume_definer"
    )
    op.execute("GRANT SELECT, UPDATE ON f1.upload_task TO f1_upload_definer")
    op.execute("GRANT SELECT, UPDATE ON f1.outbox TO f1_outbox_definer")
    op.execute("GRANT SELECT ON f1.upload_task TO f1_outbox_definer")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.qa_request TO f1_qa_definer")
    op.execute("GRANT INSERT ON f1.audit_log TO f1_qa_definer")

    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.current_sub() TO "
        "f1_auth_definer, f1_identity_read_definer, "
        "f1_enterprise_create_definer, f1_invite_create_definer, "
        "f1_invite_consume_definer, f1_qa_definer"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.current_enterprise_id() TO "
        "f1_auth_definer, f1_invite_create_definer, f1_qa_definer, f0d_migration"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.current_task_id(), "
        "f1.current_lease_token() TO f1_auth_definer"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION f1.session_authorized(uuid) TO f0d_migration"
    )

    for policy in (
        "membership_self", "membership_self_insert",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.enterprise_user")
    op.execute(
        """
        CREATE POLICY membership_read ON f1.enterprise_user
        FOR SELECT TO f1_api
        USING (enterprise_id = f1.current_enterprise_id()
               AND f1.session_authorized(enterprise_id))
        """
    )
    op.execute("DROP POLICY IF EXISTS tenant_enterprise_mint ON f1.enterprise")
    op.execute("DROP POLICY IF EXISTS tenant_boundary ON f1.audit_log")
    op.execute(
        """
        CREATE POLICY audit_read ON f1.audit_log FOR SELECT TO f1_api
        USING (
          enterprise_id = f1.current_enterprise_id()
          AND f1.session_authorized(enterprise_id)
          AND EXISTS (
            SELECT 1 FROM f1.enterprise_user AS eu
            JOIN f1.user_profile AS up ON up.id = eu.user_id
            WHERE eu.enterprise_id = audit_log.enterprise_id
              AND up.keycloak_sub = f1.current_sub()
              AND eu.role IN ('super_admin','auditor')))
        """
    )
    op.execute(
        """
        CREATE POLICY audit_append_api ON f1.audit_log FOR INSERT TO f1_api
        WITH CHECK (enterprise_id = f1.current_enterprise_id()
                    AND f1.session_authorized(enterprise_id))
        """
    )
    op.execute(
        """
        CREATE POLICY audit_append_worker ON f1.audit_log FOR INSERT TO f1_worker
        WITH CHECK (enterprise_id = f1.current_enterprise_id()
                    AND f1.session_authorized(enterprise_id))
        """
    )

    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON f1.enterprise_user FROM f1_api, f1_worker"
    )
    op.execute("REVOKE INSERT, DELETE ON f1.enterprise FROM f1_api, f1_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON f1.user_profile FROM f1_api, f1_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON f1.invite_jti FROM f1_api, f1_worker")
    op.execute("REVOKE INSERT, UPDATE, DELETE ON f1.qa_request FROM f1_api, f1_worker")
    op.execute("REVOKE EXECUTE ON FUNCTION f1.current_sub() FROM f1_worker")


def downgrade() -> None:
    # Downgrade is for disposable scratch only.  It restores the f1_0003
    # signatures and direct grants without touching customer/source evidence.
    # The NOLOGIN owners cannot be inherited by f0d_migration; the bootstrap
    # runner must atomically restore the exact owners before Alembic can drop
    # these functions.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_proc AS p
            JOIN pg_roles AS r ON r.oid = p.proowner
            WHERE p.oid IN (
              to_regprocedure('f1.session_authorized(uuid)'),
              to_regprocedure('f1.resolve_current_enterprises()'),
              to_regprocedure('f1.create_enterprise_for_current_sub(uuid,text,text,text)'),
              to_regprocedure('f1.create_invite_for_current_sub(text,text,text,timestamptz)'),
              to_regprocedure('f1.consume_invite(text,text,text,uuid,timestamptz,text)'),
              to_regprocedure('f1.claim_upload_task(uuid,text,integer)'),
              to_regprocedure('f1.renew_upload_lease(uuid,uuid,integer)'),
              to_regprocedure('f1.claim_pending_dispatch(integer,integer)'),
              to_regprocedure('f1.complete_dispatch(uuid,uuid,boolean)'),
              to_regprocedure('f1.claim_qa_request(uuid,text,integer)'),
              to_regprocedure('f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)')
            ) AND r.rolname <> 'f0d_migration'
          ) THEN RAISE EXCEPTION 'F1_DEFINER_OWNER_RESTORE_REQUIRED'; END IF;
        END
        $$
        """
    )
    for signature in (
        "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)",
        "f1.claim_qa_request(uuid,text,integer)",
        "f1.complete_dispatch(uuid,uuid,boolean)",
        "f1.claim_pending_dispatch(integer,integer)",
        "f1.renew_upload_lease(uuid,uuid,integer)",
        "f1.claim_upload_task(uuid,text,integer)",
        "f1.consume_invite(text,text,text,uuid,timestamptz,text)",
        "f1.create_invite_for_current_sub(text,text,text,timestamptz)",
        "f1.create_enterprise_for_current_sub(uuid,text,text,text)",
        "f1.resolve_current_enterprises()",
        "f1.current_lease_token()",
        "f1.current_task_id()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")
    for policy, table in DEFINER_POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.{table}")
    for policy, table in (
        ("audit_append_worker", "audit_log"),
        ("audit_append_api", "audit_log"),
        ("audit_read", "audit_log"),
        ("membership_read", "enterprise_user"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON f1.{table}")
    op.execute("ALTER TABLE f1.user_profile NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE f1.enterprise_user NO FORCE ROW LEVEL SECURITY")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.enterprise_user TO f1_api")
    op.execute("GRANT SELECT ON f1.enterprise_user TO f1_worker")
    op.execute("GRANT INSERT ON f1.enterprise TO f1_api, f1_worker")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.user_profile TO f1_api")
    op.execute("GRANT SELECT, INSERT, UPDATE ON f1.invite_jti TO f1_api")
    op.execute("ALTER TABLE f1.qa_request DROP CONSTRAINT IF EXISTS qa_request_state_ck")
    op.execute(
        "ALTER TABLE f1.qa_request DROP COLUMN IF EXISTS attempt, "
        "DROP COLUMN IF EXISTS owner_lease_until, DROP COLUMN IF EXISTS owner_token"
    )
    op.execute("ALTER TABLE f1.outbox DROP CONSTRAINT IF EXISTS outbox_rq_job_id_uq")
    op.execute(
        "ALTER TABLE f1.outbox DROP COLUMN IF EXISTS dispatch_attempt, "
        "DROP COLUMN IF EXISTS dispatch_lease_until, DROP COLUMN IF EXISTS dispatch_token, "
        "DROP COLUMN IF EXISTS rq_job_id"
    )
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS upload_task_source_ready_ck")
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS upload_task_object_state_ck")
    op.execute(
        "ALTER TABLE f1.upload_task DROP COLUMN IF EXISTS next_attempt_at, "
        "DROP COLUMN IF EXISTS lease_acquired_at, DROP COLUMN IF EXISTS lease_owner, "
        "DROP COLUMN IF EXISTS lease_token, DROP COLUMN IF EXISTS source_size, "
        "DROP COLUMN IF EXISTS source_etag, DROP COLUMN IF EXISTS object_state"
    )
    op.execute("ALTER TABLE f1.outbox DROP CONSTRAINT IF EXISTS outbox_task_enterprise_fk")
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS upload_task_document_enterprise_fk")
    op.execute("ALTER TABLE f1.upload_task DROP CONSTRAINT IF EXISTS upload_task_enterprise_id_id_uq")
    op.execute("ALTER TABLE f1.document DROP CONSTRAINT IF EXISTS document_enterprise_id_id_uq")
