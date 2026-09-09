"""Controlled ingestion login, bounded to a live delivery and its source."""
from alembic import op
from sqlalchemy import text

revision = 'f1_0042'
down_revision = 'f1_0041'
branch_labels = depends_on = None
ROLE = 'f1_ingestion_process_definer'
LOGIN = 'f1_ingestion_worker'
SIGNATURES = (
    'read_ingestion_worker_delivery(uuid,uuid)',
    'finish_ingestion_worker_delivery(uuid,uuid,text,text,integer)',
    'ingestion_worker_context(boolean)',
    'register_ingestion_worker_pipeline()',
)


def upgrade():
    op.execute(f'GRANT USAGE ON SCHEMA f1 TO {ROLE},{LOGIN}')
    for table in ('enterprise','enterprise_user','user_profile','document','document_record','document_version',
                  'upload_task','material_knowledge_scope','crm_account','material_ingestion_delivery'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY ingestion_cap_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='{LOGIN}')")
        if table != 'user_profile':
            op.execute(f'GRANT UPDATE(id) ON f1.{table} TO {ROLE}')
            op.execute(f"CREATE POLICY ingestion_cap_lock ON f1.{table} FOR UPDATE TO {ROLE} USING(session_user='{LOGIN}') WITH CHECK(false)")
    op.execute(f'GRANT UPDATE(state,dispatch_token,dispatch_lease_until,next_attempt_at,reason_code,completed_at,updated_at) ON f1.material_ingestion_delivery TO {ROLE}')
    op.execute(f"CREATE POLICY ingestion_cap_finish ON f1.material_ingestion_delivery FOR UPDATE TO {ROLE} USING(session_user='{LOGIN}') WITH CHECK(session_user='{LOGIN}')")
    op.execute(f'GRANT SELECT,INSERT ON f1.material_pipeline_delivery TO {ROLE}')
    op.execute(f"CREATE POLICY ingestion_cap_handoff_read ON f1.material_pipeline_delivery FOR SELECT TO {ROLE} USING(session_user='{LOGIN}')")
    op.execute(f"CREATE POLICY ingestion_cap_handoff_insert ON f1.material_pipeline_delivery FOR INSERT TO {ROLE} WITH CHECK(session_user='{LOGIN}')")
    for signature in ('current_sub()','current_enterprise_id()'):
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{signature} TO {ROLE},{LOGIN}')
    _context()
    _ready()
    _delivery()
    _handoff()
    _runtime_permissions()
    _guard()
    for signature in SIGNATURES:
        op.execute(f'REVOKE ALL ON FUNCTION f1.{signature} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{signature} TO {LOGIN}')


def _context():
    op.execute(f"""CREATE FUNCTION f1.ingestion_worker_context(p_lock boolean) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE d f1.material_ingestion_delivery; t f1.upload_task; v f1.document_version;
        src f1.document; rec f1.document_record; scope f1.material_knowledge_scope;
        member f1.enterprise_user; actor uuid; cap uuid; token uuid;
      BEGIN
        IF session_user<>'{LOGIN}' OR p_lock IS NULL THEN RETURN NULL; END IF;
        BEGIN
          cap:=nullif(current_setting('f1.ingestion_delivery_id',true),'')::uuid;
          token:=nullif(current_setting('f1.ingestion_dispatch_token',true),'')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN RETURN NULL;
        END;
        IF cap IS NULL OR token IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO d FROM f1.material_ingestion_delivery WHERE id=cap;
        IF d.id IS NULL OR d.state<>'dispatched' OR d.dispatch_token IS DISTINCT FROM token
          OR d.dispatch_lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        SELECT * INTO v FROM f1.document_version WHERE id=d.document_version_id AND enterprise_id=d.enterprise_id;
        IF v.id IS NULL THEN RETURN NULL; END IF;
        IF p_lock THEN
          PERFORM id FROM f1.upload_task WHERE id=v.upload_task_id FOR UPDATE;
          PERFORM id FROM f1.document WHERE id=v.source_document_id FOR SHARE;
          PERFORM id FROM f1.document_record WHERE id=v.document_record_id FOR SHARE;
          SELECT * INTO v FROM f1.document_version WHERE id=v.id FOR SHARE;
          SELECT * INTO d FROM f1.material_ingestion_delivery WHERE id=cap FOR SHARE;
        END IF;
        SELECT * INTO t FROM f1.upload_task WHERE id=v.upload_task_id AND enterprise_id=d.enterprise_id;
        SELECT * INTO src FROM f1.document WHERE id=v.source_document_id AND enterprise_id=d.enterprise_id;
        SELECT * INTO rec FROM f1.document_record WHERE id=v.document_record_id AND enterprise_id=d.enterprise_id;
        SELECT * INTO scope FROM f1.material_knowledge_scope WHERE id=rec.knowledge_scope_id AND enterprise_id=d.enterprise_id;
        IF t.id IS NULL OR src.id IS NULL OR rec.id IS NULL OR scope.id IS NULL
          OR rec.status<>'active' OR t.pipeline_kind<>'controlled_ingestion'
          OR t.document_id IS DISTINCT FROM src.id OR t.object_key IS DISTINCT FROM src.object_key
          OR src.knowledge_scope_id IS DISTINCT FROM scope.id
          OR t.source_size IS DISTINCT FROM src.size OR t.source_size IS NULL OR t.source_size<1
          OR t.source_etag IS NULL OR t.content_sha256 !~ '^[0-9a-f]{{64}}$'
          THEN RETURN NULL; END IF;
        SELECT id INTO actor FROM f1.user_profile WHERE keycloak_sub=d.actor_sub;
        IF p_lock THEN
          PERFORM id FROM f1.enterprise WHERE id=d.enterprise_id FOR SHARE;
          SELECT * INTO member FROM f1.enterprise_user WHERE enterprise_id=d.enterprise_id AND user_id=actor FOR SHARE;
          IF scope.client_account_id IS NOT NULL THEN
            PERFORM id FROM f1.crm_account WHERE id=scope.client_account_id AND enterprise_id=d.enterprise_id FOR SHARE;
          END IF;
        ELSE
          SELECT * INTO member FROM f1.enterprise_user WHERE enterprise_id=d.enterprise_id AND user_id=actor;
        END IF;
        IF member.id IS NULL OR member.revoked_at IS NOT NULL
          OR member.role NOT IN ('super_admin','enterprise_admin','plant_admin')
          OR (scope.scope_kind='client' AND member.role='plant_admin' AND NOT EXISTS(
            SELECT 1 FROM f1.crm_account WHERE id=scope.client_account_id
              AND enterprise_id=d.enterprise_id AND owner_user_id=actor))
          OR d.state<>'dispatched' OR d.dispatch_token IS DISTINCT FROM token
          OR d.dispatch_lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('delivery_id',d.id,'enterprise_id',d.enterprise_id,
          'document_version_id',v.id,'document_record_id',rec.id,'source_document_id',src.id,
          'upload_task_id',t.id,'knowledge_scope_id',scope.id,'client_account_id',scope.client_account_id,
          'actor_sub',d.actor_sub,'actor_id',actor,'member_id',member.id,'role',member.role,
          'source_sha256',t.content_sha256,'source_size',t.source_size,'source_etag',t.source_etag,
          'content_type',src.content_type,'object_key',t.object_key,'lease_until',d.dispatch_lease_until);
      END $$""")


def _ready():
    for table in ('material_analysis','material_page_classification'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY ingestion_cap_ready_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='{LOGIN}')")
    op.execute("""CREATE FUNCTION f1.ingestion_worker_ready(ctx jsonb) RETURNS boolean
      LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
        SELECT ctx IS NOT NULL AND EXISTS(SELECT 1 FROM f1.upload_task
          WHERE id=(ctx->>'upload_task_id')::uuid AND enterprise_id=(ctx->>'enterprise_id')::uuid
          AND status='done' AND object_state='ready' AND processing_stage='ready'
          AND quarantine_status IN ('held','released') AND scan_verdict='clean' AND preview_status='ready')
        AND (ctx->>'content_type'<>'application/pdf' OR EXISTS(
          SELECT 1 FROM (SELECT * FROM f1.material_analysis
            WHERE document_version_id=(ctx->>'document_version_id')::uuid
              AND enterprise_id=(ctx->>'enterprise_id')::uuid AND analysis_version='material-v1'
            ORDER BY analysis_revision DESC,id DESC LIMIT 1) a
          WHERE a.status IN ('ready','confirmed') AND a.source_sha256=ctx->>'source_sha256'
            AND a.extraction_contract=3
            AND a.page_count=(SELECT count(*) FROM f1.material_page_classification p WHERE p.analysis_id=a.id)
            AND NOT EXISTS(SELECT 1 FROM f1.material_page_classification p WHERE p.analysis_id=a.id AND p.ocr_required)))
      $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.ingestion_worker_ready(jsonb) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.ingestion_worker_ready(jsonb) TO {ROLE}')


def _delivery():
    for old,new in (
        ('read_material_ingestion_delivery_claim(uuid,uuid)',SIGNATURES[0]),
        ('finish_material_ingestion_delivery(uuid,uuid,text,text,integer)',SIGNATURES[1]),
    ):
        definition=op.get_bind().execute(text('SELECT pg_get_functiondef(to_regprocedure(:sig))'),{'sig':'f1.'+old}).scalar_one()
        for before,after in ((old.split('(')[0]+'(',new.split('(')[0]+'('),
                             ("session_user NOT IN ('f1_api','f1_worker')",f"session_user<>'{LOGIN}'")):
            if definition.count(before)!=1:raise RuntimeError('INGESTION_DEFINITION_MISMATCH')
            definition=definition.replace(before,after)
        if old.startswith('finish_'):
            definition=definition.replace('DECLARE v_count integer;', 'DECLARE v_count integer; ctx jsonb;')
            anchor='          UPDATE f1.material_ingestion_delivery AS delivery'
            if definition.count(anchor)!=1:raise RuntimeError('INGESTION_FINISH_DEFINITION_MISMATCH')
            definition=definition.replace(anchor,"""          IF p_outcome='done' THEN
            PERFORM set_config('f1.ingestion_delivery_id',p_delivery_id::text,true);
            PERFORM set_config('f1.ingestion_dispatch_token',p_dispatch_token::text,true);
            ctx:=f1.ingestion_worker_context(true);
            IF NOT f1.ingestion_worker_ready(ctx) THEN RETURN false; END IF;
          END IF;
"""+anchor)
        op.execute(definition.replace('statement_timestamp()','clock_timestamp()'))


def _handoff():
    op.execute("""CREATE FUNCTION f1.register_ingestion_worker_pipeline() RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb; stable uuid;
      BEGIN
        ctx:=f1.ingestion_worker_context(true);
        IF ctx IS NULL THEN RAISE EXCEPTION 'INGESTION_CAPABILITY_INVALID'; END IF;
        IF ctx->>'role' NOT IN ('super_admin','enterprise_admin') THEN RETURN NULL; END IF;
        IF NOT f1.ingestion_worker_ready(ctx) THEN RAISE EXCEPTION 'INGESTION_HANDOFF_NOT_READY'; END IF;
        stable:=md5('material-pipeline:advance:'||(ctx->>'enterprise_id')||':'||(ctx->>'document_version_id'))::uuid;
        INSERT INTO f1.material_pipeline_delivery(id,enterprise_id,document_version_id,delivery_kind,actor_sub,state)
          VALUES(stable,(ctx->>'enterprise_id')::uuid,(ctx->>'document_version_id')::uuid,'advance',ctx->>'actor_sub','pending')
          ON CONFLICT ON CONSTRAINT material_pipeline_delivery_identity_uq DO NOTHING;
        IF NOT EXISTS(SELECT 1 FROM f1.material_pipeline_delivery WHERE id=stable
          AND enterprise_id=(ctx->>'enterprise_id')::uuid AND document_version_id=(ctx->>'document_version_id')::uuid)
          THEN RAISE EXCEPTION 'INGESTION_HANDOFF_IDENTITY_CONFLICT'; END IF;
        IF f1.ingestion_worker_context(false) IS NULL THEN RAISE EXCEPTION 'INGESTION_CAPABILITY_EXPIRED'; END IF;
        RETURN stable;
      END $$""")
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.ingestion_worker_context(boolean) TO {ROLE}')


def _runtime_permissions():
    identifiers={'enterprise':'enterprise_id','enterprise_user':'member_id','user_profile':'actor_id',
        'document':'source_document_id','document_record':'document_record_id','document_version':'document_version_id',
        'upload_task':'upload_task_id','material_knowledge_scope':'knowledge_scope_id','crm_account':'client_account_id',
        'material_ingestion_delivery':'delivery_id'}
    for table,key in identifiers.items():
        predicate=f"id=(f1.ingestion_worker_context(false)->>'{key}')::uuid"
        op.execute(f'GRANT SELECT ON f1.{table} TO {LOGIN}')
        op.execute(f'CREATE POLICY ingestion_runtime_read ON f1.{table} FOR SELECT TO {LOGIN} USING({predicate})')
    for table in ('material_analysis','material_ocr_checkpoint'):
        predicate="document_version_id=(f1.ingestion_worker_context(false)->>'document_version_id')::uuid"
        op.execute(f'GRANT SELECT ON f1.{table} TO {LOGIN}')
        op.execute(f'CREATE POLICY ingestion_runtime_read ON f1.{table} FOR SELECT TO {LOGIN} USING({predicate})')
    for table in ('material_page_classification','material_field_candidate'):
        predicate=f'EXISTS(SELECT 1 FROM f1.material_analysis a WHERE a.id={table}.analysis_id AND a.enterprise_id={table}.enterprise_id)'
        op.execute(f'GRANT SELECT ON f1.{table} TO {LOGIN}')
        op.execute(f'CREATE POLICY ingestion_runtime_read ON f1.{table} FOR SELECT TO {LOGIN} USING({predicate})')
    updates={
        'upload_task':'status,object_state,processing_stage,quarantine_status,scan_verdict,scanner_engine,scanner_version,signature_version,preview_status,preview_kind,preview_sha256,preview_unit_count,error_reason,next_attempt_at,attempt,lease_token,lease_owner,lease_acquired_at,lease_until,updated_at',
        'document':'status',
        'material_analysis':'id',
    }
    for table,columns in updates.items():
        predicate=("document_version_id=(f1.ingestion_worker_context(false)->>'document_version_id')::uuid"
                   if table=='material_analysis' else f"id=(f1.ingestion_worker_context(false)->>'{identifiers[table]}')::uuid")
        op.execute(f'GRANT UPDATE({columns}) ON f1.{table} TO {LOGIN}')
        check='false' if table=='material_analysis' else predicate
        op.execute(f'CREATE POLICY ingestion_runtime_update ON f1.{table} FOR UPDATE TO {LOGIN} USING({predicate}) WITH CHECK({check})')
    inserts={
        'material_analysis':'id,enterprise_id,document_version_id,source_sha256,analysis_version,analysis_revision,supersedes_analysis_id,extraction_contract,parser_backend,status,document_profile,shadow_status,reason_code,suggested_kind,suggested_kind_confidence_ppm,resolved_kind,classification_source,classification_by_user_id,classification_at,page_count,candidate_count',
        'material_page_classification':'id,enterprise_id,analysis_id,page_number,primary_kind,ocr_required,table_candidate,two_column_candidate,text_character_count,text_confidence_ppm,scan_confidence_ppm,table_confidence_ppm,two_column_confidence_ppm,reason_codes',
        'material_field_candidate':'id,enterprise_id,analysis_id,field_name,candidate_value,page_number,evidence_snippet,confidence_ppm,confidence_basis,calibrated,producer',
        'material_ocr_checkpoint':'id,enterprise_id,document_version_id,source_sha256,expected_page_count,page_number,parser_backend,source_unit_id,body_ciphertext,body_sha256,body_aad_sha256,character_count,confidence_mean_ppm,table_candidate,two_column_candidate,completed_at,expires_at',
        'audit_log':'id,enterprise_id,user_sub,action,resource_type,resource_id,result',
    }
    for table,columns in inserts.items():
        op.execute(f'GRANT INSERT({columns}) ON f1.{table} TO {LOGIN}')
        op.execute(f"CREATE POLICY ingestion_runtime_insert ON f1.{table} FOR INSERT TO {LOGIN} WITH CHECK(enterprise_id=(f1.ingestion_worker_context(false)->>'enterprise_id')::uuid)")
    op.execute(f'GRANT DELETE ON f1.material_ocr_checkpoint TO {LOGIN}')
    op.execute(f"CREATE POLICY ingestion_runtime_delete ON f1.material_ocr_checkpoint FOR DELETE TO {LOGIN} USING(document_version_id=(f1.ingestion_worker_context(false)->>'document_version_id')::uuid)")


def _guard():
    op.execute(f"""CREATE FUNCTION f1.guard_ingestion_worker_write() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb; row_data jsonb; valid boolean:=false;
      BEGIN
        IF session_user<>'{LOGIN}' THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
        ctx:=f1.ingestion_worker_context(true);
        IF ctx IS NULL THEN RAISE EXCEPTION 'INGESTION_CAPABILITY_INVALID'; END IF;
        row_data:=CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
        IF row_data->>'enterprise_id' IS DISTINCT FROM ctx->>'enterprise_id'
          THEN RAISE EXCEPTION 'INGESTION_SCOPE_INVALID'; END IF;
        CASE TG_TABLE_NAME
          WHEN 'upload_task' THEN
            valid:=row_data->>'id'=ctx->>'upload_task_id'
              AND row_data->>'object_state' IN ('quarantined','ready')
              AND (row_data->>'quarantine_status' IN ('held','blocked') OR
                (TG_OP='UPDATE' AND OLD.quarantine_status='released' AND NEW.quarantine_status='released'));
            IF TG_OP='UPDATE' AND OLD.processing_stage IN ('scanning','validating','previewing')
              AND OLD.lease_until<=clock_timestamp()
              AND NOT (NEW.processing_stage='scanning' AND NEW.lease_token IS NOT NULL
                AND NEW.lease_token IS DISTINCT FROM OLD.lease_token)
              THEN valid:=false; END IF;
          WHEN 'document' THEN
            valid:=row_data->>'id'=ctx->>'source_document_id' AND row_data->>'status' IN ('pending','scanning','failed');
          WHEN 'material_analysis' THEN
            valid:=TG_OP='INSERT' AND row_data->>'document_version_id'=ctx->>'document_version_id'
              AND row_data->>'source_sha256'=ctx->>'source_sha256' AND row_data->>'status' IN ('ready','failed')
              AND ctx->>'content_type'='application/pdf' AND row_data->>'analysis_version'='material-v1'
              AND row_data->>'parser_backend'='pypdf_heuristic' AND row_data->>'extraction_contract'='3'
              AND EXISTS(SELECT 1 FROM f1.upload_task WHERE id=(ctx->>'upload_task_id')::uuid
                AND status='done' AND object_state='ready' AND processing_stage='ready'
                AND scan_verdict='clean' AND preview_status='ready'
                AND preview_unit_count=(row_data->>'page_count')::integer);
          WHEN 'material_ocr_checkpoint' THEN
            valid:=row_data->>'document_version_id'=ctx->>'document_version_id'
              AND (TG_OP='DELETE' OR row_data->>'source_sha256'=ctx->>'source_sha256');
          WHEN 'material_page_classification','material_field_candidate' THEN
            valid:=TG_OP='INSERT' AND EXISTS(SELECT 1 FROM f1.material_analysis
              WHERE id=(row_data->>'analysis_id')::uuid AND document_version_id=(ctx->>'document_version_id')::uuid
              AND enterprise_id=(ctx->>'enterprise_id')::uuid AND status='ready'
              AND xmin::text::bigint=(pg_current_xact_id()::text::numeric % 4294967296));
          WHEN 'audit_log' THEN
            valid:=TG_OP='INSERT' AND row_data->>'user_sub'=ctx->>'actor_sub' AND (
              (row_data->>'resource_type'='document_version' AND row_data->>'resource_id'=ctx->>'document_version_id'
                AND row_data->>'action' IN ('document.version.process','material.analysis.persist','material.analysis.deferred')) OR
              (row_data->>'resource_type'='material_analysis' AND row_data->>'action' IN ('material.analysis.created','material.analysis.retried')
                AND EXISTS(SELECT 1 FROM f1.material_analysis WHERE id::text=row_data->>'resource_id'
                  AND document_version_id=(ctx->>'document_version_id')::uuid
                  AND xmin::text::bigint=(pg_current_xact_id()::text::numeric % 4294967296))));
          ELSE valid:=false;
        END CASE;
        IF valid IS DISTINCT FROM true THEN RAISE EXCEPTION 'INGESTION_WRITE_DENIED'; END IF;
        RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.guard_ingestion_worker_write() FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.guard_ingestion_worker_write() TO {LOGIN}')
    for table in ('upload_task','document','material_analysis','material_page_classification',
                  'material_field_candidate','material_ocr_checkpoint','audit_log'):
        for timing,name in (('BEFORE','a_ingestion_capability'),('AFTER','zz_ingestion_capability')):
            op.execute(f'CREATE TRIGGER {name} {timing} INSERT OR UPDATE OR DELETE ON f1.{table} FOR EACH ROW EXECUTE FUNCTION f1.guard_ingestion_worker_write()')


def downgrade():
    raise RuntimeError('INGESTION_WORKER_RESTORE_REQUIRED')
