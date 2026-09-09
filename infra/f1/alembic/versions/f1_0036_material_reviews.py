"""Immutable human reviews, source-bound encrypted text and field corrections."""
from alembic import op
revision = 'f1_0036'
down_revision = 'f1_0035'
branch_labels = depends_on = None
ROLE = 'f1_material_evidence_definer'


def upgrade():
    _tables()
    _helpers()
    _reader()
    _writer()
    _grants()


def _tables():
    op.execute("""CREATE TABLE f1.material_review_revision (
      id uuid PRIMARY KEY, enterprise_id uuid NOT NULL, knowledge_scope_id uuid NOT NULL,
      document_record_id uuid NOT NULL, document_version_id uuid NOT NULL,
      source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
      source_format text NOT NULL CHECK(source_format IN('pdf','docx','xlsx','jpeg')),
      revision_no integer NOT NULL CHECK(revision_no>0), predecessor_id uuid,
      request_id uuid NOT NULL, request_sha256 text NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
      action text NOT NULL CHECK(action IN('confirm','revoke')),
      base_revision_id uuid, base_manifest_sha256 text,
      fragment_count integer NOT NULL CHECK(fragment_count BETWEEN 0 AND 20015),
      nonblank_fragment_count integer NOT NULL CHECK(nonblank_fragment_count BETWEEN 0 AND fragment_count),
      actor_sub text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(enterprise_id,id), UNIQUE(enterprise_id,request_id),
      UNIQUE(enterprise_id,document_version_id,revision_no),
      FOREIGN KEY(enterprise_id,predecessor_id) REFERENCES f1.material_review_revision(enterprise_id,id),
      FOREIGN KEY(enterprise_id,knowledge_scope_id) REFERENCES f1.material_knowledge_scope(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_record_id) REFERENCES f1.document_record(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_version_id) REFERENCES f1.document_version(enterprise_id,id),
      CHECK((action='confirm' AND base_revision_id IS NOT NULL AND base_manifest_sha256 ~ '^[0-9a-f]{64}$' AND fragment_count>0)
        OR (action='revoke' AND predecessor_id IS NOT NULL AND base_revision_id IS NULL AND base_manifest_sha256 IS NULL AND fragment_count=0))
    )""")
    op.execute("""CREATE TABLE f1.material_review_fragment (
      id uuid PRIMARY KEY, enterprise_id uuid NOT NULL, review_revision_id uuid NOT NULL,
      base_fragment_id uuid NOT NULL, entry_kind text NOT NULL CHECK(entry_kind IN('text','field')), field_name text,
      ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 20014), locator jsonb NOT NULL,
      locator_sha256 text NOT NULL CHECK(locator_sha256 ~ '^[0-9a-f]{64}$'),
      body_sha256 text NOT NULL CHECK(body_sha256 ~ '^[0-9a-f]{64}$'),
      character_count integer NOT NULL CHECK(character_count BETWEEN 0 AND 2000000),
      has_nonblank_text boolean NOT NULL CHECK(character_count>0 OR NOT has_nonblank_text),
      body_ciphertext bytea NOT NULL CHECK(octet_length(body_ciphertext) BETWEEN 33 AND 8000033),
      body_aad_sha256 text NOT NULL CHECK(body_aad_sha256 ~ '^[0-9a-f]{64}$'),
      UNIQUE(enterprise_id,review_revision_id,ordinal),
      UNIQUE(enterprise_id,review_revision_id,field_name),
      FOREIGN KEY(enterprise_id,review_revision_id) REFERENCES f1.material_review_revision(enterprise_id,id),
      CHECK((entry_kind='text' AND field_name IS NULL) OR (entry_kind='field' AND field_name IS NOT NULL))
    )""")
    for table in ('material_review_revision','material_review_fragment'):
        op.execute(f"CREATE TRIGGER review_immutable BEFORE UPDATE OR DELETE ON f1.{table} FOR EACH ROW EXECUTE FUNCTION f1.guard_native_immutable()")


def _helpers():
    op.execute(r"""CREATE FUNCTION f1.review_source(p_version uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE v f1.document_version; t f1.upload_task; d f1.document; r f1.document_record;
      BEGIN
        SELECT * INTO v FROM f1.document_version WHERE id=p_version;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT * INTO t FROM f1.upload_task WHERE id=v.upload_task_id AND enterprise_id=v.enterprise_id FOR SHARE;
        SELECT * INTO d FROM f1.document WHERE id=v.source_document_id AND enterprise_id=v.enterprise_id FOR SHARE;
        SELECT * INTO r FROM f1.document_record WHERE id=v.document_record_id AND enterprise_id=v.enterprise_id FOR SHARE;
        SELECT * INTO v FROM f1.document_version WHERE id=p_version FOR SHARE;
        IF v.id IS NULL OR t.id IS NULL OR d.id IS NULL OR r.id IS NULL
          OR t.document_id IS DISTINCT FROM d.id OR v.source_document_id IS DISTINCT FROM d.id
          OR v.document_record_id IS DISTINCT FROM r.id OR v.upload_task_id IS DISTINCT FROM t.id
          OR r.status<>'active' OR v.version_no<>r.latest_version_no OR r.knowledge_scope_id IS NULL
          OR d.knowledge_scope_id IS DISTINCT FROM r.knowledge_scope_id OR d.status<>'done'
          OR NOT ((d.content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document' AND t.object_key ~ '^[0-9a-f]{32}[.]docx$' AND t.source_size<=26214400)
            OR (d.content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' AND t.object_key ~ '^[0-9a-f]{32}[.]xlsx$' AND t.source_size<=26214400)
            OR (d.content_type='application/pdf' AND t.object_key ~ '^[0-9a-f]{32}[.]pdf$' AND t.source_size<=52428800)
            OR (d.content_type='image/jpeg' AND t.object_key ~ '^[0-9a-f]{32}[.]jpg$' AND t.source_size<=20971520))
          OR t.pipeline_kind<>'controlled_ingestion'
          OR t.status<>'done' OR t.object_state<>'ready' OR t.processing_stage<>'ready'
          OR t.preview_status<>'ready' OR t.scan_verdict<>'clean' OR t.quarantine_status<>'released'
          OR t.released_at IS NULL OR t.rejected_at IS NOT NULL OR t.source_size IS NULL
          OR t.source_size NOT BETWEEN 1 AND 52428800 OR t.source_size IS DISTINCT FROM d.size
          OR t.source_etag IS NULL OR length(t.source_etag) NOT BETWEEN 1 AND 255
          OR t.object_key IS DISTINCT FROM d.object_key THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('enterprise_id',v.enterprise_id,'knowledge_scope_id',r.knowledge_scope_id,
          'document_record_id',r.id,'document_version_id',v.id,'source_document_id',d.id,'upload_task_id',t.id,
          'source_format',CASE WHEN d.content_type='application/pdf' THEN 'pdf' WHEN d.content_type='image/jpeg' THEN 'jpeg' WHEN d.content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' THEN 'xlsx' ELSE 'docx' END,
          'source_sha256',t.content_sha256,'source_size',t.source_size,'source_etag',t.source_etag,'object_key',t.object_key);
      END $$""")
    op.execute(r"""CREATE FUNCTION f1.review_uuid(ns uuid, value bytea) RETURNS uuid
      LANGUAGE plpgsql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE h bytea;
      BEGIN
      h:=f1.native_sha1(uuid_send(ns)||value);
      h:=set_byte(set_byte(substring(h FROM 1 FOR 16),6,(get_byte(h,6)&15)|80),8,(get_byte(h,8)&63)|128);
      RETURN encode(h,'hex')::uuid;
      END $$""")
    op.execute("""CREATE FUNCTION f1.review_semantic(p jsonb) RETURNS text
      LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT encode(sha256(convert_to(f1.native_canonical((p-ARRAY['request_sha256','fragments'])||jsonb_build_object('fragments',
        COALESCE((SELECT jsonb_agg(jsonb_build_object('base_fragment_id',value->'base_fragment_id','entry_kind',value->'entry_kind','field_name',value->'field_name','ordinal',value->'ordinal','body_sha256',value->'body_sha256') ORDER BY n) FROM jsonb_array_elements(p->'fragments') WITH ORDINALITY a(value,n)),'[]'::jsonb))),'UTF8')),'hex') $$""")
    op.execute("""CREATE FUNCTION f1.review_base(s jsonb) RETURNS jsonb
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE rid uuid; manifest text; items jsonb; eid uuid:=(s->>'enterprise_id')::uuid; vid uuid:=(s->>'document_version_id')::uuid;
      BEGIN
      IF s IS NULL THEN RETURN NULL; END IF;
      IF s->>'source_format'='pdf' THEN
        -- A current successful whole-document index is the PDF review base.
        -- A queued/running rebuild is not an accepted base.
        SELECT id INTO rid FROM f1.material_rag_job WHERE enterprise_id=eid AND document_version_id=vid
          AND source_sha256=s->>'source_sha256' AND action IN('index','rebuild') ORDER BY created_at DESC,id DESC LIMIT 1;
        IF rid IS NULL OR NOT EXISTS(SELECT 1 FROM f1.material_rag_job j WHERE j.id=rid AND j.status='done') THEN RETURN NULL; END IF;
        SELECT jsonb_agg(to_jsonb(q) ORDER BY page_number,ordinal,id) INTO items FROM (
          SELECT u.id,u.enterprise_id,u.knowledge_scope_id,u.document_record_id,u.document_version_id,u.source_sha256,
            u.page_number,u.ordinal,u.parser_version,u.body_sha256,u.body_aad_sha256,encode(u.body_ciphertext,'hex') AS body_ciphertext_hex,
            jsonb_build_object('schema_version',2,'kind','pdf_page','page_number',u.page_number) AS locator
          FROM f1.material_rag_unit u WHERE u.enterprise_id=eid AND u.document_version_id=vid
            AND u.knowledge_scope_id=(s->>'knowledge_scope_id')::uuid AND u.document_record_id=(s->>'document_record_id')::uuid
            AND u.source_sha256=s->>'source_sha256' ORDER BY page_number,ordinal,id LIMIT 20001
        ) q;
        IF items IS NULL OR jsonb_array_length(items)>20000 OR EXISTS(SELECT 1 FROM jsonb_array_elements(items) f
          WHERE f->>'parser_version' NOT IN('pypdf-6.14.2-visual3','f0h-ppocrv6-3.9.2','cloud-vision-page-3'))
          OR jsonb_array_length(items) IS DISTINCT FROM (SELECT indexed_unit_count FROM f1.material_rag_job WHERE id=rid)
          THEN RETURN NULL; END IF;
        SELECT encode(sha256(convert_to(f1.native_canonical(jsonb_agg(jsonb_build_object('id',f->'id','body_sha256',f->'body_sha256',
          'parser_version',f->'parser_version','page_number',f->'page_number','ordinal',f->'ordinal') ORDER BY n)),'UTF8')),'hex')
          INTO manifest FROM jsonb_array_elements(items) WITH ORDINALITY a(f,n);
      ELSE
        SELECT id,manifest_sha256 INTO rid,manifest FROM f1.material_extraction_revision
          WHERE enterprise_id=eid AND document_version_id=vid AND source_sha256=s->>'source_sha256'
          AND source_format=s->>'source_format' AND report_source_eligible ORDER BY created_at DESC,id DESC LIMIT 1;
        IF rid IS NULL THEN RETURN NULL; END IF;
        SELECT jsonb_agg(to_jsonb(q) ORDER BY ordinal) INTO items FROM (
          SELECT f.id,f.enterprise_id,f.extraction_revision_id,f.ordinal,f.locator,f.locator_sha256,f.body_sha256,
            f.character_count,f.has_nonblank_text,f.body_aad_sha256,encode(f.body_ciphertext,'hex') AS body_ciphertext_hex,
            r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.source_sha256,r.parser_version,r.extraction_contract
          FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id AND r.enterprise_id=f.enterprise_id
          WHERE f.enterprise_id=eid AND f.extraction_revision_id=rid ORDER BY f.ordinal
        ) q;
      END IF;
      IF items IS NULL OR octet_length(items::text)>24000000 THEN RETURN NULL; END IF;
      RETURN jsonb_build_object('base_revision_id',rid,'base_manifest_sha256',manifest,'base_fragments',items);
      END $$""")


def _reader():
    op.execute("""CREATE FUNCTION f1.read_material_review(p_version uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid; actor text; s jsonb; b jsonb; h f1.material_review_revision; items jsonb;
      BEGIN
      eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
      actor:=nullif(current_setting('f1.sub',true),'');
      IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL THEN RETURN NULL; END IF;
      s:=f1.review_source(p_version);
      IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid OR NOT f1.native_actor(eid,actor) THEN RETURN NULL; END IF;
      b:=f1.review_base(s);
      SELECT * INTO h FROM f1.material_review_revision WHERE enterprise_id=eid AND document_version_id=p_version ORDER BY revision_no DESC LIMIT 1;
      SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY ordinal),'[]'::jsonb) INTO items FROM (
        SELECT f.id,f.enterprise_id,f.review_revision_id,f.base_fragment_id,f.entry_kind,f.field_name,f.ordinal,f.locator,f.locator_sha256,
          f.body_sha256,f.character_count,f.has_nonblank_text,f.body_aad_sha256,encode(f.body_ciphertext,'hex') AS body_ciphertext_hex,
          h.knowledge_scope_id AS knowledge_scope_id,h.document_record_id AS document_record_id,h.document_version_id AS document_version_id,
          h.source_sha256 AS source_sha256,h.base_revision_id AS base_revision_id,h.base_manifest_sha256 AS base_manifest_sha256
        FROM f1.material_review_fragment f WHERE f.enterprise_id=eid AND f.review_revision_id=h.id ORDER BY f.ordinal
      ) q;
      RETURN (s-ARRAY['object_key','source_etag','source_size','source_document_id','upload_task_id'])||COALESCE(b,'{}'::jsonb)||
        jsonb_build_object('review_head',CASE WHEN h.id IS NULL THEN NULL ELSE jsonb_build_object('id',h.id,'action',h.action,
          'revision_no',h.revision_no,'base_revision_id',h.base_revision_id,'base_manifest_sha256',h.base_manifest_sha256) END,'review_fragments',items);
      END $$""")


def _writer():
    op.execute("""CREATE FUNCTION f1.probe_material_review_request(p_version uuid,p_request uuid,p_sha text) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid; actor text; s jsonb; r f1.material_review_revision;
      BEGIN
      eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
      actor:=nullif(current_setting('f1.sub',true),'');
      IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL THEN RAISE EXCEPTION 'REVIEW_SOURCE_UNAVAILABLE'; END IF;
      s:=f1.review_source(p_version);
      IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid OR NOT f1.native_actor(eid,actor) THEN RAISE EXCEPTION 'REVIEW_SOURCE_UNAVAILABLE'; END IF;
      SELECT * INTO r FROM f1.material_review_revision WHERE enterprise_id=eid AND request_id=p_request;
      IF r.id IS NULL THEN RETURN NULL; END IF;
      IF r.document_version_id IS DISTINCT FROM p_version OR r.request_sha256 IS DISTINCT FROM p_sha THEN RAISE EXCEPTION 'REVIEW_REQUEST_CONFLICT'; END IF;
      RETURN jsonb_build_object('request_id',r.request_id,'id',r.id,'revision_no',r.revision_no,'action',r.action,'replayed',true);
      END $$""")
    op.execute(r"""CREATE FUNCTION f1.write_material_review(p jsonb) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid; actor text; s jsonb; b jsonb; h f1.material_review_revision; old f1.material_review_revision;
        vid uuid; rid uuid; req uuid; f jsonb; bf jsonb; l jsonb; identity jsonb; aad bytea; k text;
        pos integer:=0; n integer; textn integer; chars bigint:=0; nonblank integer:=0; fields text[]:='{}'; previousfield text:='';
      BEGIN
      eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
      actor:=nullif(current_setting('f1.sub',true),'');
      IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL OR p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>24000000
        OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key) IS DISTINCT FROM ARRAY[
          'action','base_manifest_sha256','base_revision_id','checked_against_source','document_version_id','expected_review_revision_id',
          'fragments','request_id','request_sha256','revision_id','source_sha256']::text[]
        OR jsonb_typeof(p->'fragments')<>'array' OR jsonb_typeof(p->'checked_against_source')<>'boolean'
        OR p->>'action' NOT IN('confirm','revoke') THEN RAISE EXCEPTION 'REVIEW_REQUEST_INVALID'; END IF;
      vid:=(p->>'document_version_id')::uuid; req:=(p->>'request_id')::uuid;
      rid:=f1.review_uuid('55b7ce1a-7b14-5ba8-84f0-fcc0d495ba66',convert_to(eid::text,'UTF8')||decode('00','hex')||convert_to(req::text,'UTF8'));
      IF p->>'revision_id' IS DISTINCT FROM rid::text OR p->>'request_sha256' IS DISTINCT FROM f1.review_semantic(p)
        OR p->>'request_id' IS DISTINCT FROM req::text OR p->>'document_version_id' IS DISTINCT FROM vid::text THEN RAISE EXCEPTION 'REVIEW_REQUEST_INVALID'; END IF;
      -- The source upload lock serializes reviews, release and native finalization.
      PERFORM t.id FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id AND v.enterprise_id=t.enterprise_id
        WHERE v.id=vid FOR UPDATE OF t;
      s:=f1.review_source(vid);
      IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid OR NOT f1.native_actor(eid,actor) THEN RAISE EXCEPTION 'REVIEW_SOURCE_UNAVAILABLE'; END IF;
      IF p->>'source_sha256' IS DISTINCT FROM s->>'source_sha256' THEN RAISE EXCEPTION 'REVIEW_SOURCE_CHANGED'; END IF;
      SELECT * INTO old FROM f1.material_review_revision WHERE enterprise_id=eid AND request_id=req;
      IF old.id IS NOT NULL THEN
        IF old.request_sha256 IS DISTINCT FROM p->>'request_sha256' THEN RAISE EXCEPTION 'REVIEW_REQUEST_CONFLICT'; END IF;
        RETURN jsonb_build_object('request_id',old.request_id,'id',old.id,'revision_no',old.revision_no,'action',old.action,'replayed',true);
      END IF;
      SELECT * INTO h FROM f1.material_review_revision WHERE enterprise_id=eid AND document_version_id=vid ORDER BY revision_no DESC LIMIT 1;
      IF p->>'expected_review_revision_id' IS DISTINCT FROM h.id::text THEN RAISE EXCEPTION 'REVIEW_REVISION_CONFLICT'; END IF;
      n:=jsonb_array_length(p->'fragments');
      IF n>20015 THEN RAISE EXCEPTION 'REVIEW_COVERAGE_INVALID'; END IF;
      IF p->>'action'='revoke' THEN
        IF h.id IS NULL OR h.action<>'confirm' OR n<>0 OR p->'checked_against_source'<>'false'::jsonb
          OR p->'base_revision_id'<>'null'::jsonb OR p->'base_manifest_sha256'<>'null'::jsonb THEN RAISE EXCEPTION 'REVIEW_REVOKE_INVALID'; END IF;
      ELSE
        b:=f1.review_base(s);
        IF b IS NULL OR p->>'base_revision_id' IS DISTINCT FROM b->>'base_revision_id'
          OR p->>'base_manifest_sha256' IS DISTINCT FROM b->>'base_manifest_sha256' THEN RAISE EXCEPTION 'REVIEW_BASE_CHANGED'; END IF;
        textn:=jsonb_array_length(b->'base_fragments');
        IF p->'checked_against_source'<>'true'::jsonb OR n<textn OR n>textn+15 THEN RAISE EXCEPTION 'REVIEW_COVERAGE_INVALID'; END IF;
      END IF;
      FOR f IN SELECT value FROM jsonb_array_elements(p->'fragments') LOOP
        IF jsonb_typeof(f)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(f) key) IS DISTINCT FROM ARRAY[
          'base_fragment_id','body_aad_sha256','body_ciphertext_hex','body_sha256','character_count','entry_kind','field_name',
          'has_nonblank_text','id','locator','locator_sha256','ordinal']::text[]
          OR jsonb_typeof(f->'ordinal')<>'number' OR f->>'ordinal' IS DISTINCT FROM pos::text
          OR jsonb_typeof(f->'character_count')<>'number' OR f->>'character_count' !~ '^(0|[1-9][0-9]{0,6})$'
          OR jsonb_typeof(f->'has_nonblank_text')<>'boolean'
          OR jsonb_typeof(f->'body_ciphertext_hex')<>'string' OR f->>'body_ciphertext_hex' !~ '^[0-9a-f]+$'
          OR left(f->>'body_ciphertext_hex',10)<>'46314d5231' OR length(f->>'body_ciphertext_hex') NOT BETWEEN 66 AND 16000066
          OR length(f->>'body_ciphertext_hex')%2<>0 THEN RAISE EXCEPTION 'REVIEW_FRAGMENT_INVALID'; END IF;
        FOREACH k IN ARRAY ARRAY['body_sha256','body_aad_sha256','locator_sha256'] LOOP
          IF jsonb_typeof(f->k)<>'string' OR f->>k !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'REVIEW_FRAGMENT_INVALID'; END IF;
        END LOOP;
        IF pos<textn THEN
          bf:=b->'base_fragments'->pos;
          IF f->>'entry_kind' IS DISTINCT FROM 'text' OR f->'field_name'<>'null'::jsonb THEN RAISE EXCEPTION 'REVIEW_COVERAGE_INVALID'; END IF;
        ELSE
          SELECT value INTO bf FROM jsonb_array_elements(b->'base_fragments') WHERE value->>'id'=f->>'base_fragment_id';
          IF f->>'entry_kind' IS DISTINCT FROM 'field' OR jsonb_typeof(f->'field_name')<>'string'
            OR f->>'field_name' NOT IN('source_title','publisher','source_type','jurisdiction','source_reference','version_title','domain',
              'effect_status','issued_on','effective_from','effective_to','summary','report_title','report_date','report_summary')
            OR f->>'field_name'=ANY(fields) OR (f->>'field_name') COLLATE "C" <= previousfield COLLATE "C"
            OR (f->>'character_count')::integer>4096 THEN RAISE EXCEPTION 'REVIEW_FIELD_INVALID'; END IF;
          fields:=array_append(fields,f->>'field_name'); previousfield:=f->>'field_name';
        END IF;
        l:=f->'locator';
        IF bf IS NULL OR f->>'base_fragment_id' IS DISTINCT FROM bf->>'id' OR l IS DISTINCT FROM bf->'locator'
          OR f->>'locator_sha256' IS DISTINCT FROM encode(sha256(convert_to(f1.native_canonical(l),'UTF8')),'hex') THEN RAISE EXCEPTION 'REVIEW_LOCATION_INVALID'; END IF;
        chars:=chars+(f->>'character_count')::bigint;
        IF chars>2000000 OR ((f->>'character_count')::integer=0 AND (f->>'has_nonblank_text')::boolean) THEN RAISE EXCEPTION 'REVIEW_CONTENT_LIMIT'; END IF;
        IF (f->>'has_nonblank_text')::boolean THEN nonblank:=nonblank+1; END IF;
        identity:=jsonb_build_object('schema_version',1,'enterprise_id',eid,'knowledge_scope_id',s->'knowledge_scope_id',
          'document_record_id',s->'document_record_id','document_version_id',vid,'review_revision_id',rid,
          'base_revision_id',p->'base_revision_id','base_fragment_id',f->'base_fragment_id','base_manifest_sha256',p->'base_manifest_sha256',
          'source_sha256',s->'source_sha256','source_format',s->'source_format','entry_kind',f->'entry_kind','field_name',f->'field_name',
          'ordinal',pos,'locator',l,'locator_sha256',f->'locator_sha256','body_sha256',f->'body_sha256');
        aad:=convert_to('anhuan.material.review.v1','UTF8')||decode('00','hex')||convert_to(f1.native_canonical(identity),'UTF8');
        IF f->>'body_aad_sha256' IS DISTINCT FROM encode(sha256(aad),'hex') OR f->>'id' IS DISTINCT FROM
          f1.review_uuid('d5949baf-21ee-543c-822e-ea9a3d92b233',aad)::text THEN RAISE EXCEPTION 'REVIEW_IDENTITY_INVALID'; END IF;
        pos:=pos+1;
      END LOOP;
      INSERT INTO f1.material_review_revision(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,source_sha256,source_format,
        revision_no,predecessor_id,request_id,request_sha256,action,base_revision_id,base_manifest_sha256,fragment_count,nonblank_fragment_count,actor_sub)
      VALUES(rid,eid,(s->>'knowledge_scope_id')::uuid,(s->>'document_record_id')::uuid,vid,s->>'source_sha256',s->>'source_format',
        COALESCE(h.revision_no,0)+1,h.id,req,p->>'request_sha256',p->>'action',(p->>'base_revision_id')::uuid,p->>'base_manifest_sha256',n,nonblank,actor);
      INSERT INTO f1.material_review_fragment(id,enterprise_id,review_revision_id,base_fragment_id,entry_kind,field_name,ordinal,locator,locator_sha256,
        body_sha256,character_count,has_nonblank_text,body_ciphertext,body_aad_sha256)
      SELECT (x->>'id')::uuid,eid,rid,(x->>'base_fragment_id')::uuid,x->>'entry_kind',x->>'field_name',(x->>'ordinal')::integer,x->'locator',x->>'locator_sha256',
        x->>'body_sha256',(x->>'character_count')::integer,(x->>'has_nonblank_text')::boolean,decode(x->>'body_ciphertext_hex','hex'),x->>'body_aad_sha256'
        FROM jsonb_array_elements(p->'fragments') x;
      INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
      VALUES(gen_random_uuid(),eid,actor,'native.review.'||CASE WHEN p->>'action'='confirm' THEN 'confirmed' ELSE 'revoked' END,
        'material_review_revision',rid::text,jsonb_build_object('version_id',vid,'revision_no',COALESCE(h.revision_no,0)+1,'predecessor_id',h.id)::text);
      RETURN jsonb_build_object('request_id',req,'id',rid,'revision_no',COALESCE(h.revision_no,0)+1,'action',p->>'action','replayed',false);
      END $$""")


def _grants():
    allowed="session_user='f1_api'"
    for table in ('material_review_revision','material_review_fragment'):
        op.execute(f'ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON f1.{table} FROM PUBLIC,f1_api,f1_worker')
        op.execute(f'CREATE POLICY review_private ON f1.{table} FOR ALL TO {ROLE} USING({allowed}) WITH CHECK({allowed})')
        op.execute(f'GRANT SELECT,INSERT ON f1.{table} TO {ROLE}')
    for table in ('material_rag_unit','material_rag_job'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f'CREATE POLICY review_base_read ON f1.{table} FOR SELECT TO {ROLE} USING({allowed})')
    op.execute(f"CREATE POLICY review_audit ON f1.audit_log FOR INSERT TO {ROLE} WITH CHECK({allowed} AND action IN('native.review.confirmed','native.review.revoked') AND resource_type='material_review_revision')")
    for sig in ('review_source(uuid)','review_uuid(uuid,bytea)','review_semantic(jsonb)','review_base(jsonb)','read_material_review(uuid)','write_material_review(jsonb)','probe_material_review_request(uuid,uuid,text)'):
        op.execute(f'REVOKE ALL ON FUNCTION f1.{sig} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{sig} TO {ROLE}')
    for sig in ('read_material_review(uuid)','write_material_review(jsonb)','probe_material_review_request(uuid,uuid,text)'):
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{sig} TO f1_api')


def downgrade():
    raise RuntimeError('MATERIAL_REVIEW_RESTORE_REQUIRED')
