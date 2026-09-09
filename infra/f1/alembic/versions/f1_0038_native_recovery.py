"""Explicit, idempotent administrator recovery of native extraction deliveries."""
from alembic import op
revision = 'f1_0038'
down_revision = 'f1_0037'
branch_labels = depends_on = None
ROLE = 'f1_material_evidence_definer'
ACTION = 'native.extraction.recovery_requested'


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION f1.guard_native_immutable() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$ BEGIN
      IF TG_TABLE_NAME='material_evidence_job' AND TG_OP='UPDATE' THEN
        IF OLD.state='blocked' AND NEW.state='pending' AND session_user='f1_api' AND current_user='f1_material_evidence_definer'
          AND OLD.enterprise_id=nullif(current_setting('f1.enterprise_id',true),'')::uuid
          AND NEW.actor_sub=nullif(current_setting('f1.sub',true),'') AND f1.native_actor(OLD.enterprise_id,NEW.actor_sub)
          AND NEW.attempt=0 AND NEW.lease_token IS NULL AND NEW.lease_until IS NULL AND NEW.next_attempt_at IS NULL
          AND NEW.reason_code IS NULL AND NEW.completed_at IS NULL AND OLD.completed_token IS NULL AND NEW.completed_token IS NULL
          AND (to_jsonb(NEW)-ARRAY['actor_sub','state','attempt','lease_token','lease_until','next_attempt_at','reason_code','updated_at','completed_at'])
            = (to_jsonb(OLD)-ARRAY['actor_sub','state','attempt','lease_token','lease_until','next_attempt_at','reason_code','updated_at','completed_at'])
          THEN RETURN NEW; END IF;
        IF (to_jsonb(NEW)-ARRAY['state','attempt','lease_token','lease_until','completed_token','next_attempt_at','reason_code','updated_at','completed_at'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','attempt','lease_token','lease_until','completed_token','next_attempt_at','reason_code','updated_at','completed_at'])
          THEN RAISE EXCEPTION 'NATIVE_JOB_IDENTITY_IMMUTABLE'; END IF;
        IF OLD.state IN('done','blocked') THEN RAISE EXCEPTION 'NATIVE_JOB_TERMINAL'; END IF;
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'NATIVE_REVISION_IMMUTABLE'; END $$""")
    op.execute(f"CREATE UNIQUE INDEX native_recovery_request_uq ON f1.audit_log(enterprise_id,resource_id) WHERE action='{ACTION}'")
    op.execute(f'GRANT SELECT ON f1.audit_log TO {ROLE}')
    op.execute(f"CREATE POLICY native_recovery_read ON f1.audit_log FOR SELECT TO {ROLE} USING(session_user='f1_api' AND action='{ACTION}')")
    op.execute(f"CREATE POLICY native_recovery_write ON f1.audit_log FOR INSERT TO {ROLE} WITH CHECK(session_user='f1_api' AND action='{ACTION}' AND resource_type='material_evidence_job')")
    op.execute("""CREATE FUNCTION f1.recover_native_extraction(p_version uuid,p_request uuid,p_expected_job uuid,p_parser text,p_profile text) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor text:=nullif(current_setting('f1.sub',true),''); s jsonb; prior f1.audit_log;
        j f1.material_evidence_job; latest_id uuid; result jsonb; registered jsonb; outcome text;
      BEGIN
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL OR p_version IS NULL OR p_request IS NULL
          THEN RAISE EXCEPTION 'NATIVE_RECOVERY_INVALID'; END IF;
        -- Same source-first order as registration. Exclusive task lock also
        -- serializes a concurrent finalizer before any actor/job transition.
        PERFORM t.id FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id AND v.enterprise_id=t.enterprise_id
          WHERE v.id=p_version AND v.enterprise_id=eid FOR UPDATE OF t;
        s:=f1.native_source(p_version);
        IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid OR NOT f1.native_actor(eid,actor)
          THEN RAISE EXCEPTION 'NATIVE_SOURCE_UNAVAILABLE'; END IF;
        IF NOT f1.native_contract(s->>'source_format',p_parser,p_profile) THEN RAISE EXCEPTION 'NATIVE_RECOVERY_INVALID'; END IF;
        SELECT * INTO prior FROM f1.audit_log WHERE enterprise_id=eid AND action='native.extraction.recovery_requested' AND resource_id=p_request::text;
        IF prior.id IS NOT NULL THEN
          IF prior.user_sub IS DISTINCT FROM actor OR (prior.result::jsonb)->>'version_id' IS DISTINCT FROM p_version::text
            OR ((prior.result::jsonb)->>'expected_job_id')::uuid IS DISTINCT FROM p_expected_job
            OR prior.result::jsonb->>'parser_version' IS DISTINCT FROM p_parser OR prior.result::jsonb->>'support_profile' IS DISTINCT FROM p_profile
            THEN RAISE EXCEPTION 'NATIVE_RECOVERY_REQUEST_CONFLICT'; END IF;
          RETURN (prior.result::jsonb->'receipt')||jsonb_build_object('replayed',true);
        END IF;
        SELECT id INTO latest_id FROM f1.material_evidence_job WHERE enterprise_id=eid AND document_version_id=p_version ORDER BY created_at DESC,id DESC LIMIT 1;
        IF latest_id IS DISTINCT FROM p_expected_job THEN RAISE EXCEPTION 'NATIVE_RECOVERY_JOB_CHANGED'; END IF;
        registered:=f1.register_native_extraction_job(p_version,p_parser,p_profile);
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=(registered->>'job_id')::uuid FOR UPDATE;
        IF j.id IS NULL OR NOT(to_jsonb(j) @> s) THEN RAISE EXCEPTION 'NATIVE_SOURCE_UNAVAILABLE'; END IF;
        outcome:=CASE WHEN latest_id IS DISTINCT FROM j.id THEN 'registered' ELSE 'unchanged' END;
        IF j.state='blocked' THEN
          UPDATE f1.material_evidence_job SET state='pending',attempt=0,actor_sub=actor,
            lease_token=NULL,lease_until=NULL,next_attempt_at=NULL,reason_code=NULL,completed_at=NULL,updated_at=clock_timestamp()
            WHERE id=j.id RETURNING * INTO j;
          outcome:='rearmed';
        END IF;
        result:=jsonb_build_object('request_id',p_request,'version_id',p_version,'job_id',j.id,'state',j.state,'outcome',outcome,'replayed',false);
        INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
          VALUES(gen_random_uuid(),eid,actor,'native.extraction.recovery_requested','material_evidence_job',p_request::text,
            jsonb_build_object('version_id',p_version,'expected_job_id',p_expected_job,'parser_version',p_parser,'support_profile',p_profile,
              'previous_job_id',latest_id,'receipt',result)::text);
        RETURN result;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.recover_native_extraction(uuid,uuid,uuid,text,text) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.recover_native_extraction(uuid,uuid,uuid,text,text) TO f1_api')


def downgrade():
    raise RuntimeError('NATIVE_RECOVERY_RESTORE_REQUIRED')
