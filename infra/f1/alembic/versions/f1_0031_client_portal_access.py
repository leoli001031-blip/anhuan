"""Atomic client portal provisioning and revocable, bound invitations.

Provider, CRM account and customer enterprise remain distinct identities.
The private writer does not grant providers membership in customer tenants.
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = "f1_0031"
down_revision: str | None = "f1_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLE = "f1_client_access_definer"


def upgrade() -> None:
    op.execute("ALTER TABLE f1.analysis_report_client_audience ADD COLUMN create_request_id uuid")
    op.execute("CREATE UNIQUE INDEX client_portal_request_uq ON f1.analysis_report_client_audience(enterprise_id,create_request_id) WHERE create_request_id IS NOT NULL")
    op.execute("ALTER TABLE f1.invite_jti ADD COLUMN portal_audience_id uuid REFERENCES f1.analysis_report_client_audience(id), ADD COLUMN revoked_at timestamptz")
    _functions()
    _policies()
    _consume_contract()


def _functions() -> None:
    # Only the private role may invoke this helper. Caller identity is always
    # looked up in current membership; no role or target tenant comes from JSON.
    op.execute("""
      CREATE FUNCTION f1.require_client_portal_manager() RETURNS uuid
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE actor uuid;
      BEGIN
        IF session_user <> 'f1_api' THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        SELECT eu.user_id INTO actor FROM f1.enterprise_user eu
        JOIN f1.user_profile up ON up.id=eu.user_id
        JOIN f1.enterprise e ON e.id=eu.enterprise_id
        WHERE eu.enterprise_id=f1.current_enterprise_id()
          AND up.keycloak_sub=f1.current_sub() AND eu.role='enterprise_admin'
          AND e.business_kind='service_provider';
        IF actor IS NULL THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        RETURN actor;
      END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION f1.require_client_portal_manager() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION f1.require_client_portal_manager() TO {ROLE}")
    op.execute("""
      CREATE FUNCTION f1.manage_client_portal(p_client uuid,p_request uuid,p_action text,p_license text)
      RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=f1.current_enterprise_id(); target uuid; label text;
        binding f1.analysis_report_client_audience;
        previous f1.audit_log;
      BEGIN
        PERFORM f1.require_client_portal_manager();
        IF p_client IS NULL OR p_request IS NULL OR p_action IS NULL
          OR p_action NOT IN ('open','revoke','restore') THEN
          RAISE EXCEPTION 'CLIENT_PORTAL_INPUT_INVALID'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(eid::text||':client:'||p_client::text,0));
        SELECT display_name INTO label FROM f1.crm_account WHERE enterprise_id=eid AND id=p_client;
        IF NOT FOUND THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        IF EXISTS(SELECT 1 FROM f1.analysis_report_client_audience
          WHERE enterprise_id=eid AND create_request_id=p_request AND client_account_id<>p_client)
          THEN RAISE EXCEPTION 'CLIENT_PORTAL_REQUEST_CONFLICT'; END IF;
        SELECT * INTO binding FROM f1.analysis_report_client_audience
          WHERE enterprise_id=eid AND client_account_id=p_client;
        IF binding.id IS NOT NULL THEN
          PERFORM set_config('f1.client_access_target',binding.audience_enterprise_id::text,true);
        END IF;
        SELECT * INTO previous FROM f1.audit_log WHERE id=p_request;
        IF FOUND THEN
          IF previous.action<>'client.portal.'||p_action OR previous.resource_id<>p_client::text
            OR previous.user_sub<>f1.current_sub() THEN RAISE EXCEPTION 'CLIENT_PORTAL_REQUEST_CONFLICT'; END IF;
          IF binding.id IS NULL THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
          IF p_action='open' AND NOT EXISTS(SELECT 1 FROM f1.enterprise WHERE id=binding.audience_enterprise_id AND license_no=btrim(p_license))
            THEN RAISE EXCEPTION 'CLIENT_PORTAL_REQUEST_CONFLICT'; END IF;
          RETURN binding.audience_enterprise_id;
        END IF;
        IF binding.id IS NULL THEN
          IF p_action<>'open' THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
          IF p_license IS NULL OR length(btrim(p_license)) NOT BETWEEN 1 AND 64 THEN
            RAISE EXCEPTION 'CLIENT_PORTAL_LICENSE_REQUIRED'; END IF;
          target:=gen_random_uuid();
          PERFORM set_config('f1.client_access_target',target::text,true);
          INSERT INTO f1.enterprise(id,name,license_no,business_kind)
            VALUES(target,label,btrim(p_license),'client');
          INSERT INTO f1.analysis_report_client_audience
            (id,enterprise_id,client_account_id,audience_enterprise_id,status,create_request_id)
            VALUES(gen_random_uuid(),eid,p_client,target,'active',p_request) RETURNING * INTO binding;
          INSERT INTO f1.material_knowledge_scope(id,enterprise_id,scope_kind,client_account_id)
            VALUES(gen_random_uuid(),eid,'client',p_client) ON CONFLICT DO NOTHING;
        ELSE
          target:=binding.audience_enterprise_id;
          PERFORM set_config('f1.client_access_target',target::text,true);
          IF NOT EXISTS(SELECT 1 FROM f1.enterprise WHERE id=target AND business_kind='client')
            THEN RAISE EXCEPTION 'CLIENT_PORTAL_ORGANIZATION_UNCONFIGURED'; END IF;
          IF p_action='open' AND NOT EXISTS(SELECT 1 FROM f1.enterprise WHERE id=target AND license_no=btrim(p_license))
            THEN RAISE EXCEPTION 'CLIENT_PORTAL_REQUEST_CONFLICT'; END IF;
          -- Every issue/revoke/consume takes this binding lock before ledger
          -- row locks; a revoke cannot race a successful invitation consume.
          PERFORM pg_advisory_xact_lock(hashtextextended('portal:'||binding.id::text,0));
          IF p_action='open' AND binding.status='revoked' THEN
            RAISE EXCEPTION 'CLIENT_PORTAL_RESTORE_REQUIRED'; END IF;
          IF (p_action='revoke' AND binding.status='active') OR
             (p_action='restore' AND binding.status='revoked') THEN
            UPDATE f1.analysis_report_client_audience SET
              status=CASE WHEN p_action='revoke' THEN 'revoked' ELSE 'active' END,
              updated_at=statement_timestamp() WHERE id=binding.id;
            IF p_action='revoke' THEN
              UPDATE f1.invite_jti SET revoked_at=statement_timestamp()
                WHERE portal_audience_id=binding.id AND consumed_at IS NULL AND revoked_at IS NULL;
            END IF;
          END IF;
        END IF;
        -- One command receipt per request, including an already-satisfied
        -- state. Retrying an earlier revoke after restore must not revoke again.
        INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
          VALUES(p_request,eid,f1.current_sub(),'client.portal.'||p_action,
            'crm_account',p_client::text,'success');
        RETURN target;
      END $$
    """)
    op.execute("""
      CREATE FUNCTION f1.issue_client_portal_invite(p_client uuid,p_jti text,p_email text,p_exp timestamptz)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=f1.current_enterprise_id(); binding f1.analysis_report_client_audience;
        previous f1.invite_jti;
      BEGIN
        PERFORM f1.require_client_portal_manager();
        IF p_client IS NULL OR p_jti IS NULL OR length(p_jti) NOT BETWEEN 1 AND 128
          OR p_email IS NULL OR length(btrim(p_email)) NOT BETWEEN 3 AND 320
          OR p_exp IS NULL OR p_exp<=statement_timestamp()
          OR p_exp>statement_timestamp()+interval '24 hours' THEN
          RAISE EXCEPTION 'CLIENT_PORTAL_INPUT_INVALID'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(eid::text||':client:'||p_client::text,0));
        SELECT * INTO binding FROM f1.analysis_report_client_audience
          WHERE enterprise_id=eid AND client_account_id=p_client;
        IF NOT FOUND THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('portal:'||binding.id::text,0));
        IF binding.status<>'active' THEN RAISE EXCEPTION 'CLIENT_PORTAL_RESTORE_REQUIRED'; END IF;
        PERFORM set_config('f1.client_access_target',binding.audience_enterprise_id::text,true);
        IF NOT EXISTS(SELECT 1 FROM f1.enterprise WHERE id=binding.audience_enterprise_id AND business_kind='client')
          THEN RAISE EXCEPTION 'CLIENT_PORTAL_ORGANIZATION_UNCONFIGURED'; END IF;
        SELECT * INTO previous FROM f1.invite_jti WHERE jti=p_jti;
        IF FOUND THEN
          IF previous.portal_audience_id<>binding.id OR previous.email<>lower(btrim(p_email))
            THEN RAISE EXCEPTION 'CLIENT_PORTAL_REQUEST_CONFLICT'; END IF;
          IF previous.revoked_at IS NOT NULL OR previous.consumed_at IS NOT NULL
            OR previous.expires_at<=statement_timestamp() THEN RAISE EXCEPTION 'CLIENT_PORTAL_INVITE_FINISHED'; END IF;
          RETURN jsonb_build_object('enterprise_id',previous.enterprise_id,'email',previous.email,
            'role',previous.role,'jti',previous.jti,'exp',extract(epoch FROM previous.expires_at)::bigint);
        END IF;
        INSERT INTO f1.invite_jti(jti,enterprise_id,email,role,expires_at,portal_audience_id)
          VALUES(p_jti,binding.audience_enterprise_id,lower(btrim(p_email)),'enterprise_admin',p_exp,binding.id);
        INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
          VALUES(gen_random_uuid(),eid,f1.current_sub(),'client.portal.invite','invite',p_jti,'success');
        RETURN jsonb_build_object('enterprise_id',binding.audience_enterprise_id,'email',lower(btrim(p_email)),
          'role','enterprise_admin','jti',p_jti,'exp',extract(epoch FROM p_exp)::bigint);
      END $$
    """)
    op.execute("""
      CREATE FUNCTION f1.read_client_portal(p_client uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=f1.current_enterprise_id(); binding f1.analysis_report_client_audience;
        members jsonb; invitations jsonb;
      BEGIN
        PERFORM f1.require_client_portal_manager();
        IF NOT EXISTS(SELECT 1 FROM f1.crm_account WHERE enterprise_id=eid AND id=p_client)
          THEN RAISE EXCEPTION 'CLIENT_PORTAL_NOT_FOUND'; END IF;
        SELECT * INTO binding FROM f1.analysis_report_client_audience
          WHERE enterprise_id=eid AND client_account_id=p_client;
        IF NOT FOUND THEN RETURN jsonb_build_object('client_id',p_client,'status','not_open','members','[]'::jsonb,'invitations','[]'::jsonb); END IF;
        PERFORM set_config('f1.client_access_target',binding.audience_enterprise_id::text,true);
        SELECT coalesce(jsonb_agg(jsonb_build_object('id',eu.id,'email',up.email,'role',eu.role) ORDER BY eu.created_at),'[]'::jsonb)
          INTO members FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id
          WHERE eu.enterprise_id=binding.audience_enterprise_id;
        SELECT coalesce(jsonb_agg(jsonb_build_object('email',i.email,'expires_at',i.expires_at,
          'status',CASE WHEN i.consumed_at IS NOT NULL THEN 'accepted' WHEN i.revoked_at IS NOT NULL THEN 'revoked'
            WHEN i.expires_at<=statement_timestamp() THEN 'expired' ELSE 'pending' END)
          ORDER BY i.created_at DESC),'[]'::jsonb) INTO invitations FROM
          (SELECT * FROM f1.invite_jti WHERE portal_audience_id=binding.id ORDER BY created_at DESC LIMIT 50) i;
        RETURN jsonb_build_object('client_id',p_client,'status',binding.status,
          'customer_enterprise_id',binding.audience_enterprise_id,'members',members,'invitations',invitations);
      END $$
    """)
    for signature in ("manage_client_portal(uuid,uuid,text,text)","issue_client_portal_invite(uuid,text,text,timestamptz)","read_client_portal(uuid)"):
        op.execute(f"REVOKE ALL ON FUNCTION f1.{signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION f1.{signature} TO f1_api")


def _policies() -> None:
    op.execute(f"GRANT USAGE ON SCHEMA f1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON f1.enterprise,f1.enterprise_user,f1.user_profile,f1.crm_account,f1.analysis_report_client_audience,f1.material_knowledge_scope,f1.invite_jti,f1.audit_log TO {ROLE}")
    op.execute(f"GRANT INSERT ON f1.enterprise,f1.analysis_report_client_audience,f1.material_knowledge_scope,f1.invite_jti,f1.audit_log TO {ROLE}")
    op.execute(f"GRANT UPDATE(status,updated_at) ON f1.analysis_report_client_audience TO {ROLE}")
    op.execute(f"GRANT UPDATE(revoked_at) ON f1.invite_jti TO {ROLE}")
    own = "session_user='f1_api' AND enterprise_id=f1.current_enterprise_id()"
    target = "nullif(current_setting('f1.client_access_target',true),'')::uuid"
    op.execute(f"CREATE POLICY client_access_enterprise_read ON f1.enterprise FOR SELECT TO {ROLE} USING(session_user='f1_api' AND (id=f1.current_enterprise_id() OR id={target}))")
    op.execute(f"CREATE POLICY client_access_enterprise_insert ON f1.enterprise FOR INSERT TO {ROLE} WITH CHECK(session_user='f1_api' AND id={target} AND business_kind='client')")
    op.execute(f"CREATE POLICY client_access_membership_read ON f1.enterprise_user FOR SELECT TO {ROLE} USING(session_user='f1_api' AND enterprise_id IN (f1.current_enterprise_id(),{target}))")
    op.execute(f"CREATE POLICY client_access_profile_read ON f1.user_profile FOR SELECT TO {ROLE} USING(session_user='f1_api' AND (keycloak_sub=f1.current_sub() OR EXISTS(SELECT 1 FROM f1.enterprise_user eu WHERE eu.user_id=user_profile.id AND eu.enterprise_id={target})))")
    op.execute(f"CREATE POLICY client_access_crm_read ON f1.crm_account FOR SELECT TO {ROLE} USING({own})")
    for table in ("analysis_report_client_audience","material_knowledge_scope"):
        op.execute(f"CREATE POLICY client_access_read ON f1.{table} FOR SELECT TO {ROLE} USING({own})")
        op.execute(f"CREATE POLICY client_access_insert ON f1.{table} FOR INSERT TO {ROLE} WITH CHECK({own})")
    op.execute(f"CREATE POLICY client_access_update ON f1.analysis_report_client_audience FOR UPDATE TO {ROLE} USING({own}) WITH CHECK({own})")
    invite = f"session_user='f1_api' AND enterprise_id={target} AND EXISTS(SELECT 1 FROM f1.analysis_report_client_audience a WHERE a.id=invite_jti.portal_audience_id AND a.enterprise_id=f1.current_enterprise_id() AND a.audience_enterprise_id=invite_jti.enterprise_id)"
    op.execute(f"CREATE POLICY client_access_invite_read ON f1.invite_jti FOR SELECT TO {ROLE} USING({invite})")
    op.execute(f"CREATE POLICY client_access_invite_insert ON f1.invite_jti FOR INSERT TO {ROLE} WITH CHECK({invite} AND role='enterprise_admin' AND consumed_at IS NULL AND revoked_at IS NULL)")
    op.execute(f"CREATE POLICY client_access_invite_revoke ON f1.invite_jti FOR UPDATE TO {ROLE} USING({invite}) WITH CHECK({invite})")
    op.execute(f"CREATE POLICY client_access_audit ON f1.audit_log FOR INSERT TO {ROLE} WITH CHECK({own} AND user_sub=f1.current_sub() AND action IN ('client.portal.open','client.portal.revoke','client.portal.restore','client.portal.invite') AND result='success')")
    op.execute(f"CREATE POLICY client_access_audit_read ON f1.audit_log FOR SELECT TO {ROLE} USING({own} AND action IN ('client.portal.open','client.portal.revoke','client.portal.restore','client.portal.invite'))")
    # The existing pre-membership consumer may inspect only the binding of
    # its exact signed JTI/tenant. It receives no cross-tenant write grant.
    op.execute("GRANT SELECT ON f1.analysis_report_client_audience TO f1_invite_consume_definer")
    op.execute("""CREATE POLICY client_access_consume_binding ON f1.analysis_report_client_audience
      FOR SELECT TO f1_invite_consume_definer USING(session_user='f1_api' AND EXISTS(
        SELECT 1 FROM f1.invite_jti i WHERE i.jti=current_setting('f1.invite_target_jti',true)
        AND i.enterprise_id::text=current_setting('f1.invite_target_enterprise',true)
        AND i.portal_audience_id=analysis_report_client_audience.id
        AND i.enterprise_id=analysis_report_client_audience.audience_enterprise_id))""")


def _consume_contract() -> None:
    # The earlier consumer may already be owned by its isolated NOLOGIN role.
    # Replace only this exact function through the validated bootstrap session.
    op.execute("RESET ROLE")
    op.execute("DO $$ BEGIN IF session_user <> 'f0d_bootstrap' THEN RAISE EXCEPTION 'CLIENT_PORTAL_BOOTSTRAP_REQUIRED'; END IF; END $$")
    op.execute("""
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
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_profile, v_row.role);
          INSERT INTO f1.audit_log
            (id, enterprise_id, user_sub, action, resource_type, resource_id, result)
          VALUES (gen_random_uuid(), v_row.enterprise_id, v_sub,
                  'invite.consume', 'invite', p_jti, 'success');
          RETURN QUERY SELECT v_row.jti, v_row.enterprise_id, v_row.email, v_row.role;
        END $$
        """)
    op.execute("SET LOCAL ROLE f0d_migration")


def downgrade() -> None:
    raise RuntimeError("CLIENT_PORTAL_RESTORE_REQUIRED")
