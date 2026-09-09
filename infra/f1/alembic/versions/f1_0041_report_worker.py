"""Report generation login: delivery/lease commands, with no table privileges."""
from alembic import op
from sqlalchemy import text

revision = 'f1_0041'
down_revision = 'f1_0040'
branch_labels = depends_on = None
ROLE = 'f1_report_generate_definer'
LOGIN = 'f1_report_worker'
SIGNATURES = (
    'read_report_worker_delivery(uuid,uuid)',
    'finish_report_worker_delivery(uuid,uuid,text,text,integer)',
    'claim_report_worker_generation(uuid,uuid)',
    'read_report_worker_sources(uuid,uuid)',
    'finish_report_worker_generation(uuid,uuid,text,jsonb)',
)


def _definition(signature):
    return op.get_bind().execute(text('SELECT pg_get_functiondef(to_regprocedure(:sig))'), {'sig': 'f1.' + signature}).scalar_one()


def _replace(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError('REPORT_WORKER_MIGRATION_DEFINITION_MISMATCH')
    return sql.replace(old, new)


def upgrade():
    op.execute(f'GRANT USAGE ON SCHEMA f1 TO {ROLE},{LOGIN}')
    tables = ('enterprise','enterprise_user','user_profile','document','document_record','document_version',
        'upload_task','material_knowledge_scope','material_evidence_job','material_extraction_revision',
        'material_evidence_fragment','material_review_revision','material_review_fragment','material_rag_job',
        'material_rag_unit','analysis_report','analysis_report_version','analysis_report_generation_job',
        'analysis_report_generation_delivery','analysis_report_section','analysis_report_citation')
    for table in tables:
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY report_worker_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='{LOGIN}')")
    for table in ('enterprise','enterprise_user','document','document_record','document_version','upload_task','analysis_report'):
        op.execute(f'GRANT UPDATE(id) ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY report_worker_lock ON f1.{table} FOR UPDATE TO {ROLE} USING(session_user='{LOGIN}') WITH CHECK(false)")
    for table, columns in (
        ('analysis_report_generation_job','status,error_reason,lease_token,lease_until,lease_owner,updated_at'),
        ('analysis_report_version','status,updated_at'),
        ('analysis_report_generation_delivery','state,dispatch_token,dispatch_lease_until,next_attempt_at,reason_code,completed_at,updated_at'),
    ):
        op.execute(f'GRANT UPDATE({columns}) ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY report_worker_update ON f1.{table} FOR UPDATE TO {ROLE} USING(session_user='{LOGIN}') WITH CHECK(session_user='{LOGIN}')")
    for table, columns in (
        ('analysis_report_section','id,enterprise_id,version_id,section_key,title,body,ordinal'),
        ('analysis_report_citation','id,enterprise_id,version_id,document_version_id,document_name,version_number,page_number,excerpt,ordinal,locator,evidence_revision_id,fragment_id,evidence_body_sha256'),
        ('analysis_report_audit_event','id,enterprise_id,report_id,version_id,actor_user_id,action,from_status,to_status'),
    ):
        op.execute(f'GRANT INSERT({columns}) ON f1.{table} TO {ROLE}')
        extra = " AND action='actor_revoked'" if table == 'analysis_report_audit_event' else ''
        op.execute(f"CREATE POLICY report_worker_insert ON f1.{table} FOR INSERT TO {ROLE} WITH CHECK(session_user='{LOGIN}'{extra})")
    for signature in ('review_source(uuid)','review_base(jsonb)','native_actor(uuid,text)','native_canonical(jsonb)',
                      'native_ascii_json(text)','current_sub()','current_enterprise_id()'):
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{signature} TO {ROLE}')
    # The existing immutable citation trigger runs under its own definer. Only
    # its SELECT seam is extended; the login gains no evidence function/table access.
    for table in ('material_evidence_fragment','material_extraction_revision','material_review_fragment',
                  'material_review_revision','material_rag_unit','material_rag_job'):
        op.execute(f"CREATE POLICY report_worker_citation_check ON f1.{table} FOR SELECT TO f1_material_evidence_definer USING(session_user='{LOGIN}')")
    _delivery()
    _revocation()
    _context()
    _claim()
    _sources()
    _fingerprint()
    _finish()
    for signature in SIGNATURES:
        op.execute(f'REVOKE ALL ON FUNCTION f1.{signature} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{signature} TO {LOGIN}')


def _delivery():
    # Freeze the already-versioned delivery SQL with a new name and exclusive
    # login/owner. Original API/dispatcher functions and grants remain intact.
    for old, new in (
        ('read_analysis_report_generation_delivery_claim(uuid,uuid)', SIGNATURES[0]),
        ('finish_analysis_report_generation_delivery(uuid,uuid,text,text,integer)', SIGNATURES[1]),
    ):
        definition = _replace(_definition(old), old.split('(')[0]+'(', new.split('(')[0]+'(')
        definition = _replace(definition, "session_user NOT IN ('f1_api','f1_worker')", f"session_user<>'{LOGIN}'")
        op.execute(definition)


def _revocation():
    policy = op.get_bind().execute(text("SELECT pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid) FROM pg_policy WHERE polrelid='f1.enterprise_user'::regclass AND polname='membership_active'")).one()
    exception = f"current_user='{ROLE}' AND session_user='{LOGIN}'"
    op.execute(f'ALTER POLICY membership_active ON f1.enterprise_user USING (({policy[0]}) OR ({exception})) WITH CHECK (({policy[1]}) OR ({exception}))')
    # Private reconciler: called only after the public claim verifies the
    # current dispatch token. Keep old expired-lease and audit requirements.
    definition = _replace(_definition('fail_revoked_report_generation(uuid,uuid,text)'),
        'fail_revoked_report_generation(', 'report_worker_revoke(')
    definition = _replace(definition, "session_user <> 'f1_api'", f"session_user <> '{LOGIN}'")
    definition = _replace(definition, 'SECURITY DEFINER', 'SECURITY INVOKER')
    op.execute(definition)
    op.execute('REVOKE ALL ON FUNCTION f1.report_worker_revoke(uuid,uuid,text) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.report_worker_revoke(uuid,uuid,text) TO {ROLE}')
    # Only revoked-actor terminal writes enter the historic reconciler branch.
    for signature, predicate in (
        ('guard_analysis_report_job_write()', "NEW.error_reason='REPORT_ACTOR_REVOKED'"),
        ('guard_analysis_report_version_write()', "NEW.status='failed' AND EXISTS(SELECT 1 FROM f1.analysis_report_generation_job j WHERE j.version_id=NEW.id AND j.enterprise_id=NEW.enterprise_id AND j.error_reason='REPORT_ACTOR_REVOKED')"),
        ('guard_analysis_report_audit_insert()', "NEW.action='actor_revoked'"),
    ):
        definition = _replace(_definition(signature), "current_user='f1_analysis_report_definer'",
            f"(current_user='f1_analysis_report_definer' OR (current_user='{ROLE}' AND {predicate}))")
        op.execute(definition)


def _context():
    op.execute(f"""CREATE FUNCTION f1.report_worker_context(p_job uuid,p_token uuid,p_lock boolean) RETURNS jsonb
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE j f1.analysis_report_generation_job; d f1.analysis_report_generation_delivery;
        r f1.analysis_report; v f1.analysis_report_version; scopes uuid[];
      BEGIN
        IF session_user<>'{LOGIN}' OR p_job IS NULL OR p_token IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO j FROM f1.analysis_report_generation_job WHERE id=p_job;
        IF j.id IS NULL THEN RETURN NULL; END IF;
        IF p_lock THEN
          SELECT * INTO r FROM f1.analysis_report WHERE id=j.report_id AND enterprise_id=j.enterprise_id FOR UPDATE;
          SELECT * INTO j FROM f1.analysis_report_generation_job WHERE id=p_job FOR UPDATE;
          SELECT * INTO v FROM f1.analysis_report_version WHERE id=j.version_id AND enterprise_id=j.enterprise_id FOR UPDATE;
        ELSE
          SELECT * INTO r FROM f1.analysis_report WHERE id=j.report_id AND enterprise_id=j.enterprise_id;
          SELECT * INTO v FROM f1.analysis_report_version WHERE id=j.version_id AND enterprise_id=j.enterprise_id;
        END IF;
        SELECT * INTO d FROM f1.analysis_report_generation_delivery WHERE job_id=j.id AND enterprise_id=j.enterprise_id;
        IF j.status<>'generating' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp()
          OR r.id IS NULL OR r.current_version_id IS DISTINCT FROM j.version_id OR r.archived_at IS NOT NULL
          OR v.status<>'generating' OR v.report_id IS DISTINCT FROM r.id
          OR d.id IS NULL OR d.report_id IS DISTINCT FROM r.id OR d.version_id IS DISTINCT FROM v.id THEN RETURN NULL; END IF;
        IF p_lock THEN
          IF NOT f1.native_actor(j.enterprise_id,d.actor_sub) THEN RETURN NULL; END IF;
        ELSIF NOT EXISTS(SELECT 1 FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id
          JOIN f1.enterprise e ON e.id=eu.enterprise_id WHERE e.id=j.enterprise_id AND e.business_kind='service_provider'
            AND eu.revoked_at IS NULL AND eu.role='enterprise_admin' AND up.keycloak_sub=d.actor_sub) THEN RETURN NULL;
        END IF;
        SELECT array_agg(id ORDER BY scope_kind DESC) INTO scopes FROM f1.material_knowledge_scope
          WHERE enterprise_id=j.enterprise_id AND ((scope_kind='service_provider' AND client_account_id IS NULL)
            OR (scope_kind='client' AND client_account_id=r.client_account_id));
        IF cardinality(scopes) IS DISTINCT FROM 2 THEN RETURN NULL; END IF;
        RETURN to_jsonb(j)||jsonb_build_object('scopes',scopes,'client_account_id',r.client_account_id,'actor_sub',d.actor_sub);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.report_worker_context(uuid,uuid,boolean) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.report_worker_context(uuid,uuid,boolean) TO {ROLE}')


def _claim():
    op.execute(f"""CREATE FUNCTION f1.claim_report_worker_generation(p_delivery uuid,p_dispatch uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE d f1.analysis_report_generation_delivery; j f1.analysis_report_generation_job;
        r f1.analysis_report; v f1.analysis_report_version; token uuid;
      BEGIN
        IF session_user<>'{LOGIN}' OR p_delivery IS NULL OR p_dispatch IS NULL THEN RETURN NULL; END IF;
        -- Ignore caller-provided tenant/actor settings, including pooled or forged values.
        PERFORM set_config('f1.enterprise_id','',true); PERFORM set_config('f1.sub','',true);
        SELECT * INTO d FROM f1.analysis_report_generation_delivery WHERE id=p_delivery;
        IF d.id IS NULL OR d.state<>'dispatched' OR d.dispatch_token IS DISTINCT FROM p_dispatch
          OR d.dispatch_lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        SELECT * INTO r FROM f1.analysis_report WHERE id=d.report_id AND enterprise_id=d.enterprise_id FOR UPDATE;
        SELECT * INTO j FROM f1.analysis_report_generation_job WHERE id=d.job_id AND enterprise_id=d.enterprise_id FOR UPDATE;
        SELECT * INTO v FROM f1.analysis_report_version WHERE id=d.version_id AND enterprise_id=d.enterprise_id FOR UPDATE;
        SELECT * INTO d FROM f1.analysis_report_generation_delivery WHERE id=p_delivery FOR UPDATE;
        IF r.id IS NULL OR j.id IS NULL OR v.id IS NULL OR r.current_version_id IS DISTINCT FROM v.id OR r.archived_at IS NOT NULL
          OR j.report_id IS DISTINCT FROM r.id OR j.version_id IS DISTINCT FROM v.id OR v.report_id IS DISTINCT FROM r.id
          OR d.state<>'dispatched' OR d.dispatch_token IS DISTINCT FROM p_dispatch OR d.dispatch_lease_until<=clock_timestamp()
          OR j.status NOT IN('queued','generating') OR v.status IS DISTINCT FROM j.status THEN RETURN NULL; END IF;
        IF j.status='generating' AND j.lease_until>clock_timestamp() THEN RETURN NULL; END IF;
        IF NOT f1.native_actor(d.enterprise_id,d.actor_sub) THEN
          PERFORM f1.report_worker_revoke(d.enterprise_id,j.id,d.actor_sub);
          RETURN NULL;
        END IF;
        token:=gen_random_uuid();
        UPDATE f1.analysis_report_generation_job SET status='generating',lease_token=token,
          lease_owner='analysis-report.restricted',lease_until=clock_timestamp()+interval '300 seconds',updated_at=statement_timestamp()
          WHERE id=j.id;
        UPDATE f1.analysis_report_version SET status='generating',updated_at=statement_timestamp() WHERE id=v.id;
        RETURN f1.report_worker_context(j.id,token,false);
      END $$""")


def _sources():
    # Reuse the exact 0037 corpus construction/limits, behind private INVOKER
    # code. Its only scope input is the validated report job context.
    definition = _definition('read_effective_material_sources(uuid[])')
    definition = _replace(definition, 'read_effective_material_sources(p_scopes uuid[])', 'report_worker_corpus(ctx jsonb)')
    definition = _replace(definition, 'SECURITY DEFINER', 'SECURITY INVOKER')
    start = definition.index('DECLARE eid uuid:=')
    body = definition.index('        FOR q IN SELECT', start)
    definition = definition[:start] + """DECLARE owner_id uuid:=(ctx->>'enterprise_id')::uuid;
        p_scopes uuid[]:=ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(ctx->'scopes') x);
        q record; s jsonb; b jsonb; h f1.material_review_revision; items jsonb;
        n integer:=0; total_fragments integer:=0; total_bytes bigint:=0; payload jsonb;
      BEGIN
""" + definition[body:]
    op.execute(definition)
    op.execute('REVOKE ALL ON FUNCTION f1.report_worker_corpus(jsonb) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.report_worker_corpus(jsonb) TO {ROLE}')
    op.execute("""CREATE FUNCTION f1.read_report_worker_sources(p_job uuid,p_token uuid) RETURNS SETOF jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb;
      BEGIN
        ctx:=f1.report_worker_context(p_job,p_token,false); IF ctx IS NULL THEN RETURN; END IF;
        RETURN QUERY SELECT * FROM f1.report_worker_corpus(ctx);
        IF f1.report_worker_context(p_job,p_token,true) IS NULL THEN RAISE EXCEPTION 'REPORT_LEASE_STALE'; END IF;
      END $$""")


def _fingerprint():
    op.execute("""CREATE FUNCTION f1.report_worker_fingerprint(ctx jsonb) RETURNS text
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE src jsonb; sources jsonb:='[]'; identities jsonb; hashes jsonb;
      BEGIN
        FOR src IN SELECT * FROM f1.report_worker_corpus(ctx) LOOP
          IF src->>'evidence_kind'='revoked' THEN CONTINUE; END IF;
          SELECT jsonb_agg(jsonb_build_object('fragment_id',f->'id','revision_id',src->'evidence_revision_id',
              'locator',f->'locator','body_sha256',f->'body_sha256','ordinal',n) ORDER BY n),
            jsonb_agg(f->'body_sha256' ORDER BY coalesce((f->'locator'->>'page_number')::integer,0),n)
            INTO identities,hashes FROM jsonb_array_elements(src->'fragments') WITH ORDINALITY a(f,n)
            WHERE coalesce((f->>'has_nonblank_text')::boolean,true);
          IF identities IS NULL THEN RAISE EXCEPTION 'REPORT_SOURCE_INDEX_OUTDATED'; END IF;
          sources:=sources||jsonb_build_array(jsonb_build_object('document_version_id',src->'document_version_id',
            'source_sha256',src->'source_sha256','version_number',src->'version_number',
            'evidence_identity',identities,'evidence_body_sha256',hashes));
        END LOOP;
        SELECT coalesce(jsonb_agg(value ORDER BY value->>'document_version_id'),'[]') INTO sources FROM jsonb_array_elements(sources);
        RETURN encode(sha256(convert_to(f1.native_canonical(jsonb_build_object('tenant',ctx->'enterprise_id',
          'client',ctx->'client_account_id','template','enterprise-ehs-material-analysis-v1','sources',sources)),'UTF8')),'hex');
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.report_worker_fingerprint(jsonb) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.report_worker_fingerprint(jsonb) TO {ROLE}')


def _finish():
    op.execute("""CREATE FUNCTION f1.finish_report_worker_generation(p_job uuid,p_token uuid,p_outcome text,p_result jsonb) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb; eid uuid; vid uuid; item jsonb; n integer; reason text; target text; fingerprint text;
      BEGIN
        IF p_outcome IS NULL OR p_outcome NOT IN('draft','retry','failed') OR p_result IS NULL
          OR jsonb_typeof(p_result)<>'object' OR octet_length(p_result::text)>24000000 THEN RAISE EXCEPTION 'REPORT_RESULT_INVALID'; END IF;
        ctx:=f1.report_worker_context(p_job,p_token,false); IF ctx IS NULL THEN RETURN false; END IF;
        -- Read current evidence under source locks before taking report/job
        -- locks. Keep those locks to commit; edits/revocations during model
        -- latency cannot quietly become a newly accepted frozen report.
        IF p_outcome='draft' THEN fingerprint:=f1.report_worker_fingerprint(ctx); END IF;
        ctx:=f1.report_worker_context(p_job,p_token,true); IF ctx IS NULL THEN RETURN false; END IF;
        IF p_outcome='draft' AND fingerprint IS DISTINCT FROM ctx->>'source_fingerprint_sha256'
          THEN RAISE EXCEPTION 'REPORT_SOURCE_FINGERPRINT_CHANGED'; END IF;
        eid:=(ctx->>'enterprise_id')::uuid; vid:=(ctx->>'version_id')::uuid;
        IF p_outcome='draft' THEN
          IF jsonb_typeof(p_result->'sections') IS DISTINCT FROM 'array' OR jsonb_array_length(p_result->'sections')<>7
            OR jsonb_typeof(p_result->'citations') IS DISTINCT FROM 'array'
            OR jsonb_array_length(p_result->'citations') NOT BETWEEN 1 AND 20000 THEN RAISE EXCEPTION 'REPORT_RESULT_INVALID'; END IF;
          FOR item,n IN SELECT value,ordinality FROM jsonb_array_elements(p_result->'sections') WITH ORDINALITY LOOP
            INSERT INTO f1.analysis_report_section(id,enterprise_id,version_id,section_key,title,body,ordinal)
              VALUES(gen_random_uuid(),eid,vid,item->>'key',item->>'title',item->>'body',n);
          END LOOP;
          FOR item,n IN SELECT value,ordinality FROM jsonb_array_elements(p_result->'citations') WITH ORDINALITY LOOP
            IF jsonb_typeof(item->'locator') IS DISTINCT FROM 'object' OR item->>'evidence_revision_id' IS NULL
              OR item->>'fragment_id' IS NULL OR item->>'evidence_body_sha256' IS NULL
              THEN RAISE EXCEPTION 'REPORT_CITATION_IDENTITY_REQUIRED'; END IF;
            IF NOT EXISTS(SELECT 1 FROM f1.document_version v JOIN f1.document_record r ON r.id=v.document_record_id AND r.enterprise_id=v.enterprise_id
              WHERE v.enterprise_id=eid AND v.id=(item->>'document_version_id')::uuid
                AND r.knowledge_scope_id=ANY(ARRAY(SELECT x::uuid FROM jsonb_array_elements_text(ctx->'scopes') x))) THEN RAISE EXCEPTION 'REPORT_CITATION_SCOPE_INVALID'; END IF;
            INSERT INTO f1.analysis_report_citation(id,enterprise_id,version_id,document_version_id,document_name,version_number,page_number,
              excerpt,ordinal,locator,evidence_revision_id,fragment_id,evidence_body_sha256)
              VALUES(gen_random_uuid(),eid,vid,(item->>'document_version_id')::uuid,item->>'document_name',(item->>'version_number')::integer,
                (item->>'page_number')::integer,item->>'excerpt',n,NULLIF(item->'locator','null'::jsonb),
                (item->>'evidence_revision_id')::uuid,(item->>'fragment_id')::uuid,item->>'evidence_body_sha256');
          END LOOP;
          UPDATE f1.analysis_report_generation_job SET status='draft',error_reason=NULL,updated_at=statement_timestamp() WHERE id=p_job;
          target:='draft';
        ELSE
          reason:=p_result->>'reason';
          IF (p_outcome='failed' AND (reason IS NULL OR reason !~ '^REPORT_[A-Z0-9_]{1,73}$' OR reason='REPORT_ACTOR_REVOKED'))
            OR (p_outcome='retry' AND reason IS NOT NULL) THEN RAISE EXCEPTION 'REPORT_RESULT_INVALID'; END IF;
          target:=CASE WHEN p_outcome='retry' THEN 'queued' ELSE 'failed' END;
          UPDATE f1.analysis_report_generation_job SET status=target,error_reason=reason,lease_token=NULL,lease_until=NULL,
            lease_owner=NULL,updated_at=statement_timestamp() WHERE id=p_job;
        END IF;
        UPDATE f1.analysis_report_version SET status=target,updated_at=statement_timestamp() WHERE id=vid;
        -- Wall-clock check after content/triggers/locks; statement_timestamp is
        -- insufficient for a transaction that waits past the lease deadline.
        IF (ctx->>'lease_until')::timestamptz<=clock_timestamp() THEN RAISE EXCEPTION 'REPORT_LEASE_STALE'; END IF;
        RETURN true;
      END $$""")


def downgrade():
    raise RuntimeError('REPORT_WORKER_RESTORE_REQUIRED')
