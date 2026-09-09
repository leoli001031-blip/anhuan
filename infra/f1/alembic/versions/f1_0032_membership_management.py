"""Soft membership revocation, bounded administration and historical actors."""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "f1_0032"
down_revision: str | None = "f1_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLE = "f1_membership_definer"


def upgrade() -> None:
    op.execute("ALTER TABLE f1.enterprise_user ADD COLUMN revoked_at timestamptz")
    # Permissive legacy policies still decide tenant visibility. This extra
    # restriction removes inactive actors from every ordinary authorization
    # lookup, including identity resolvers and existing definer/worker paths.
    op.execute(f"""CREATE POLICY membership_active ON f1.enterprise_user
      AS RESTRICTIVE FOR ALL TO PUBLIC USING (
        revoked_at IS NULL OR
        (current_user='{ROLE}' AND session_user='f1_api'
          AND enterprise_id=nullif(current_setting('f1.enterprise_id',true),'')::uuid) OR
        (current_user='f1_analysis_report_definer' AND session_user='f1_api'
          AND nullif(current_setting('f1.enterprise_id',true),'')::uuid IS NULL AND nullif(current_setting('f1.sub',true),'') IS NULL)
      ) WITH CHECK (
        revoked_at IS NULL OR (current_user='{ROLE}' AND session_user='f1_api'
          AND enterprise_id=nullif(current_setting('f1.enterprise_id',true),'')::uuid) OR
        (current_user='f1_analysis_report_definer' AND session_user='f1_api'
          AND nullif(current_setting('f1.enterprise_id',true),'')::uuid IS NULL AND nullif(current_setting('f1.sub',true),'') IS NULL)
      )""")
    _management()
    _policies()
    _result_fence()
    _historical_functions()


def _management() -> None:
    op.execute("""CREATE FUNCTION f1.require_membership_manager() RETURNS uuid
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE actor uuid;
      BEGIN
        IF session_user<>'f1_api' THEN RAISE EXCEPTION 'MEMBERSHIP_NOT_FOUND'; END IF;
        SELECT eu.user_id INTO actor FROM f1.enterprise_user eu
        JOIN f1.user_profile up ON up.id=eu.user_id
        JOIN f1.enterprise e ON e.id=eu.enterprise_id
        WHERE eu.enterprise_id=f1.current_enterprise_id() AND up.keycloak_sub=f1.current_sub()
          AND eu.revoked_at IS NULL AND eu.role='enterprise_admin'
          AND e.business_kind IN ('service_provider','client');
        IF actor IS NULL THEN RAISE EXCEPTION 'MEMBERSHIP_NOT_FOUND'; END IF;
        RETURN actor;
      END $$""")
    op.execute("REVOKE ALL ON FUNCTION f1.require_membership_manager() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION f1.require_membership_manager() TO {ROLE}")
    op.execute("""CREATE FUNCTION f1.read_memberships() RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE actor uuid; members jsonb;
      BEGIN
        actor:=f1.require_membership_manager();
        SELECT coalesce(jsonb_agg(jsonb_build_object('id',eu.id,'user_id',eu.user_id,
          'email',up.email,'role',eu.role,'status',CASE WHEN eu.revoked_at IS NULL
          THEN 'active' ELSE 'revoked' END) ORDER BY up.email,eu.id),'[]') INTO members
          FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id
          WHERE eu.enterprise_id=f1.current_enterprise_id();
        RETURN jsonb_build_object('enterprise_id',f1.current_enterprise_id(),
          'current_user_id',actor,'can_manage',true,'members',members);
      END $$""")
    op.execute("""CREATE FUNCTION f1.manage_membership(p_member uuid,p_request uuid,p_action text,p_role text)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=f1.current_enterprise_id(); actor uuid;
        member f1.enterprise_user; previous f1.audit_log; payload jsonb; after_role text; after_revoked timestamptz;
      BEGIN
        PERFORM f1.require_membership_manager();
        IF p_member IS NULL OR p_request IS NULL OR p_action IS NULL OR
          p_action NOT IN ('role','revoke','restore') THEN RAISE EXCEPTION 'MEMBERSHIP_ROLE_INVALID'; END IF;
        -- All member mutations serialize per tenant. Recheck after waiting;
        -- an HTTP identity snapshot is never sufficient for a database write.
        PERFORM pg_advisory_xact_lock(hashtextextended('membership:'||eid::text,0));
        actor:=f1.require_membership_manager();
        SELECT * INTO member FROM f1.enterprise_user WHERE enterprise_id=eid AND id=p_member FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'MEMBERSHIP_NOT_FOUND'; END IF;
        IF member.role='super_admin' THEN RAISE EXCEPTION 'MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED'; END IF;
        IF (p_action='role' AND (p_role IS NULL OR p_role NOT IN ('enterprise_admin','plant_admin','auditor','partner')))
          OR (p_action<>'role' AND p_role IS NOT NULL) THEN RAISE EXCEPTION 'MEMBERSHIP_ROLE_INVALID'; END IF;
        payload:=jsonb_build_object('role',p_role);
        SELECT * INTO previous FROM f1.audit_log WHERE id=p_request;
        IF FOUND THEN
          IF previous.action<>'membership.'||p_action OR previous.resource_id<>p_member::text
            OR previous.user_sub<>f1.current_sub() OR previous.result::jsonb->'request'<>payload
          THEN RAISE EXCEPTION 'MEMBERSHIP_REQUEST_CONFLICT'; END IF;
          RETURN f1.read_memberships();
        END IF;
        after_role:=CASE WHEN p_action='role' THEN p_role ELSE member.role END;
        after_revoked:=CASE WHEN p_action='restore' THEN NULL WHEN p_action='revoke'
          THEN coalesce(member.revoked_at,statement_timestamp()) ELSE member.revoked_at END;
        IF member.role='enterprise_admin' AND member.revoked_at IS NULL
          AND (after_role<>'enterprise_admin' OR after_revoked IS NOT NULL)
          AND NOT EXISTS(SELECT 1 FROM f1.enterprise_user WHERE enterprise_id=eid
            AND id<>p_member AND role='enterprise_admin' AND revoked_at IS NULL)
          THEN RAISE EXCEPTION 'MEMBERSHIP_LAST_ADMIN'; END IF;
        UPDATE f1.enterprise_user SET role=after_role,revoked_at=after_revoked WHERE id=p_member AND enterprise_id=eid;
        INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
          VALUES(p_request,eid,f1.current_sub(),'membership.'||p_action,'membership',p_member::text,
            jsonb_build_object('request',payload,'before',jsonb_build_object('role',member.role,'revoked_at',member.revoked_at),
              'after',jsonb_build_object('role',after_role,'revoked_at',after_revoked))::text);
        IF member.user_id=actor AND (after_role<>'enterprise_admin' OR after_revoked IS NOT NULL) THEN
          RETURN jsonb_build_object('enterprise_id',eid,'current_user_id',actor,'can_manage',false,'members','[]'::jsonb);
        END IF;
        RETURN f1.read_memberships();
      END $$""")
    for signature in ('read_memberships()','manage_membership(uuid,uuid,text,text)'):
        op.execute(f"REVOKE ALL ON FUNCTION f1.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION f1.{signature} TO f1_api, {ROLE}")


def _policies() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON f1.enterprise,f1.enterprise_user,f1.user_profile,f1.audit_log TO {ROLE}")
    op.execute(f"GRANT UPDATE(role,revoked_at) ON f1.enterprise_user TO {ROLE}")
    op.execute(f"GRANT INSERT ON f1.audit_log TO {ROLE}")
    own="session_user='f1_api' AND enterprise_id=f1.current_enterprise_id()"
    op.execute(f"CREATE POLICY member_management_read ON f1.enterprise_user FOR SELECT TO {ROLE} USING({own})")
    op.execute(f"CREATE POLICY member_management_update ON f1.enterprise_user FOR UPDATE TO {ROLE} USING({own}) WITH CHECK({own})")
    op.execute(f"CREATE POLICY member_management_enterprise ON f1.enterprise FOR SELECT TO {ROLE} USING(session_user='f1_api' AND id=f1.current_enterprise_id())")
    op.execute(f"CREATE POLICY member_management_profile ON f1.user_profile FOR SELECT TO {ROLE} USING(session_user='f1_api' AND EXISTS(SELECT 1 FROM f1.enterprise_user eu WHERE eu.enterprise_id=f1.current_enterprise_id() AND eu.user_id=user_profile.id))")
    audit=own+" AND action IN ('membership.role','membership.revoke','membership.restore')"
    op.execute(f"CREATE POLICY member_management_audit_read ON f1.audit_log FOR SELECT TO {ROLE} USING({audit})")
    op.execute(f"CREATE POLICY member_management_audit_insert ON f1.audit_log FOR INSERT TO {ROLE} WITH CHECK({audit} AND user_sub=f1.current_sub() AND resource_type='membership')")
    op.execute(f"CREATE POLICY member_management_audit_private ON f1.audit_log AS RESTRICTIVE FOR INSERT TO PUBLIC WITH CHECK(action NOT LIKE 'membership.%' OR current_user='{ROLE}')")
    op.execute("GRANT SELECT(revoked_at) ON f1.enterprise_user TO f1_analysis_report_definer")


def _historical_functions() -> None:
    # Exact versioned definitions below retain portal lock ordering and report
    # transition/lease guards. No historical migration is modified.
    op.execute("RESET ROLE")
    op.execute("DO $$ BEGIN IF session_user<>'f0d_bootstrap' THEN RAISE EXCEPTION 'MEMBERSHIP_BOOTSTRAP_REQUIRED'; END IF; END $$")
    op.execute(CONSUME_INVITE_SQL)
    op.execute(FAIL_REVOKED_SQL)
    op.execute(PORTAL_MANAGER_SQL)
    op.execute(CREATE_INVITE_SQL)
    op.execute("SET LOCAL ROLE f0d_migration")


def _result_fence() -> None:
    op.execute("""CREATE FUNCTION f1.lock_current_membership() RETURNS text
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE active_role text;
      BEGIN
        IF session_user<>'f1_api' THEN RETURN NULL; END IF;
        SELECT eu.role INTO active_role FROM f1.enterprise_user eu
        JOIN f1.user_profile up ON up.id=eu.user_id
        WHERE eu.enterprise_id=f1.current_enterprise_id() AND up.keycloak_sub=f1.current_sub()
          AND eu.revoked_at IS NULL FOR SHARE OF eu;
        RETURN active_role;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.lock_current_membership() FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.lock_current_membership() TO f1_api,f1_client_access_definer,f1_invite_create_definer')
    op.execute("""CREATE FUNCTION f1.guard_report_result_membership() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      BEGIN
        -- The unscoped, narrowly constrained revoked-actor reconciler retains
        -- its original lease/transition guards. Ordinary results lock the
        -- actor after job/version locks and retain that lock until commit.
        IF current_user='f1_api' AND OLD.status='generating'
          AND NEW.status IN ('draft','failed','queued') THEN
          IF NEW.enterprise_id IS DISTINCT FROM f1.current_enterprise_id()
            OR coalesce(f1.lock_current_membership(),'') NOT IN ('super_admin','enterprise_admin')
          THEN RAISE EXCEPTION 'REPORT_ACTOR_REVOKED'; END IF;
        END IF;
        RETURN NEW;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.guard_report_result_membership() FROM PUBLIC')
    op.execute('CREATE TRIGGER report_result_membership BEFORE UPDATE OF status ON f1.analysis_report_version FOR EACH ROW EXECUTE FUNCTION f1.guard_report_result_membership()')


def downgrade() -> None:
    raise RuntimeError('MEMBERSHIP_RESTORE_REQUIRED')


CONSUME_INVITE_SQL = """
        CREATE OR REPLACE FUNCTION f1.consume_invite(
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
          SELECT * INTO v_row FROM f1.invite_jti AS i WHERE i.jti=p_jti;
          IF NOT FOUND THEN RAISE EXCEPTION 'INVITE_NOT_FOUND'; END IF;
          IF v_row.portal_audience_id IS NOT NULL THEN
            PERFORM pg_advisory_xact_lock(hashtextextended('portal:'||v_row.portal_audience_id::text,0));
          END IF;
          SELECT * INTO v_row FROM f1.invite_jti AS i
           WHERE i.jti = p_jti FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'INVITE_NOT_FOUND'; END IF;
          IF v_row.consumed_at IS NOT NULL THEN RAISE EXCEPTION 'INVITE_ALREADY_USED'; END IF;
          IF v_row.revoked_at IS NOT NULL THEN RAISE EXCEPTION 'INVITE_REVOKED'; END IF;
          IF v_row.portal_audience_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM f1.analysis_report_client_audience a
            WHERE a.id=v_row.portal_audience_id AND a.audience_enterprise_id=v_row.enterprise_id AND a.status='active'
          ) THEN RAISE EXCEPTION 'INVITE_REVOKED'; END IF;
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
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_profile, v_row.role)
          ON CONFLICT (enterprise_id,user_id) DO NOTHING;
          IF NOT FOUND THEN RAISE EXCEPTION 'MEMBERSHIP_INACTIVE'; END IF;
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_sub,
                  'invite.consume', 'invite', p_jti, 'success');
          RETURN QUERY SELECT v_row.jti, v_row.enterprise_id, v_row.email, v_row.role;
        END $$
"""

FAIL_REVOKED_SQL = """
        CREATE OR REPLACE FUNCTION f1.fail_revoked_report_generation(
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
          v_actor_revoked_at timestamptz;
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

          SELECT membership.role,membership.revoked_at INTO v_actor_role,v_actor_revoked_at
          FROM f1.enterprise_user AS membership
          WHERE membership.enterprise_id=p_enterprise_id
            AND membership.user_id=v_actor_user_id
          FOR UPDATE OF membership;
          -- Report/version/audit actor foreign keys retain this membership;
          -- revocation retains the row and may use revoked_at or a role change.
          IF NOT FOUND THEN
            RETURN FALSE;
          END IF;
          IF v_actor_revoked_at IS NULL AND v_actor_role IN ('super_admin','enterprise_admin') THEN
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


PORTAL_MANAGER_SQL = """
      CREATE OR REPLACE FUNCTION f1.require_client_portal_manager() RETURNS uuid
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE actor uuid;
      BEGIN
        IF session_user <> 'f1_api' THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        IF f1.lock_current_membership() IS DISTINCT FROM 'enterprise_admin' THEN
          RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        SELECT eu.user_id INTO actor FROM f1.enterprise_user eu
        JOIN f1.user_profile up ON up.id=eu.user_id
        JOIN f1.enterprise e ON e.id=eu.enterprise_id
        WHERE eu.enterprise_id=f1.current_enterprise_id()
          AND up.keycloak_sub=f1.current_sub() AND eu.role='enterprise_admin'
          AND e.business_kind='service_provider';
        IF actor IS NULL THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        RETURN actor;
      END $$
"""


CREATE_INVITE_SQL = """
        CREATE OR REPLACE FUNCTION f1.create_invite_for_current_sub(
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
          v_actor_role := f1.lock_current_membership();
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
