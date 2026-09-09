"""Native XLSX/JPEG revisions with typed locations and ASCII canonical identities."""
from alembic import op
revision = 'f1_0035'
down_revision = 'f1_0034'
branch_labels = depends_on = None
ROLE = 'f1_material_evidence_definer'


def upgrade():
    _helpers()
    for table in ('material_evidence_job', 'material_extraction_revision'):
        for column in ('source_format', 'parser_version', 'support_profile'):
            op.execute(f'ALTER TABLE f1.{table} DROP CONSTRAINT {table}_{column}_check')
        op.execute(f'ALTER TABLE f1.{table} ADD CONSTRAINT native_format_contract CHECK(f1.native_contract(source_format,parser_version,support_profile))')
    op.execute("ALTER TABLE f1.material_extraction_revision ADD COLUMN processing_identity jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE f1.material_extraction_revision ADD CONSTRAINT native_processing_contract CHECK(f1.native_processing_valid(source_format,processing_identity))")
    _functions()
    _reader()
    op.execute('GRANT SELECT(source_format,parser_version,support_profile) ON f1.material_evidence_job TO f1_api')
    op.execute('GRANT SELECT(debts,processing_identity) ON f1.material_extraction_revision TO f1_api')


def _helpers():
    op.execute(r"""CREATE FUNCTION f1.native_contract(fmt text,parser text,profile text) RETURNS boolean
LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
SELECT COALESCE((fmt,parser,profile) IN (
('docx','docx-native-1','transitional-main-body-simple-table-1'),
('xlsx','xlsx-native-1','transitional-visible-cells-no-formulas-1'),
('jpeg','jpeg-ocr-1','jpeg-oriented-rgb-whole-image-1')),false) $$""")
    op.execute(r"""CREATE FUNCTION f1.native_ascii_json(serialized text) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE result text:=''; c text; n integer; i integer;
BEGIN
FOR i IN 1..length(serialized) LOOP
c:=substr(serialized,i,1); n:=ascii(c);
IF n<127 THEN result:=result||c;
ELSIF n<=65535 THEN result:=result||chr(92)||'u'||lpad(to_hex(n),4,'0');
ELSE n:=n-65536; result:=result||chr(92)||'u'||lpad(to_hex(55296+n/1024),4,'0')||chr(92)||'u'||lpad(to_hex(56320+n%1024),4,'0'); END IF;
END LOOP;
RETURN result;
END $$""")
    op.execute(r"""CREATE OR REPLACE FUNCTION f1.native_canonical(j jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
BEGIN
IF jsonb_typeof(j)='object' THEN
RETURN (SELECT '{'||COALESCE(string_agg(f1.native_ascii_json(to_jsonb(key)::text)||':'||f1.native_canonical(value),',' ORDER BY key COLLATE "C"),'')||'}' FROM jsonb_each(j));
ELSIF jsonb_typeof(j)='array' THEN
RETURN (SELECT '['||COALESCE(string_agg(f1.native_canonical(value),',' ORDER BY ordinal),'')||']' FROM jsonb_array_elements(j) WITH ORDINALITY a(value,ordinal));
ELSE RETURN f1.native_ascii_json(j::text); END IF;
END $$""")
    op.execute(r"""CREATE FUNCTION f1.native_processing_valid(fmt text,p jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE k text;
BEGIN
IF p IS NULL OR jsonb_typeof(p)<>'object' THEN RETURN false; END IF;
IF fmt IN('docx','xlsx') THEN RETURN p='{}'::jsonb; END IF;
IF fmt<>'jpeg' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key) IS DISTINCT FROM ARRAY['backend','dialect','endpoint_sha256','image_sha256','model','pixel_sha256','prompt_sha256','provider','renderer']::text[] THEN RETURN false; END IF;
IF p->>'backend' IS DISTINCT FROM 'cloud-vision-image-1' OR p->>'renderer' IS DISTINCT FROM 'jpeg-orient-rgb-pillow-12.2.0-1'
OR p->>'provider' NOT IN ('ark_vision','glm_vision') OR p->>'dialect' NOT IN ('chat','anthropic')
OR p->>'model' !~ '^[A-Za-z0-9_.:/-]{1,200}$' THEN RETURN false; END IF;
FOREACH k IN ARRAY ARRAY['backend','dialect','model','provider','renderer','endpoint_sha256','image_sha256','pixel_sha256','prompt_sha256'] LOOP
IF jsonb_typeof(p->k)<>'string' THEN RETURN false; END IF;
END LOOP;
FOREACH k IN ARRAY ARRAY['endpoint_sha256','image_sha256','pixel_sha256','prompt_sha256'] LOOP
IF p->>k !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
END LOOP;
RETURN true;
END $$""")
    op.execute(r"""CREATE FUNCTION f1.native_cell(address text) RETURNS integer[]
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE a text[]; col integer:=0; rownum integer; c text;
BEGIN
a:=regexp_match(address,'^([A-Z]{1,3})([1-9][0-9]{0,6})$');
IF a IS NULL THEN RETURN NULL; END IF;
FOREACH c IN ARRAY regexp_split_to_array(a[1],'') LOOP col:=col*26+ascii(c)-64; END LOOP;
rownum:=a[2]::integer;
IF col>16384 OR rownum>1048576 THEN RETURN NULL; END IF;
RETURN ARRAY[rownum,col];
END $$""")
    op.execute(r"""CREATE FUNCTION f1.native_locator_valid(fmt text,l jsonb) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
DECLARE k text; addresses text[]; firstcell integer[]; lastcell integer[];
BEGIN
IF l IS NULL OR jsonb_typeof(l)<>'object' OR l->'schema_version' IS DISTINCT FROM '2'::jsonb OR l->>'schema_version' IS DISTINCT FROM '2' THEN RETURN false; END IF;
IF fmt='docx' THEN
          IF jsonb_typeof(l)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(l) key) IS DISTINCT FROM ARRAY[
            'body_index','cell_index','grid_column','grid_span','kind','paragraph_index','part','row_index','schema_version']::text[]
            OR l->>'kind' IS DISTINCT FROM 'docx_block' OR l->>'part' IS DISTINCT FROM 'word/document.xml'
            OR l->'schema_version' IS DISTINCT FROM '2'::jsonb OR l->>'schema_version' IS DISTINCT FROM '2' OR jsonb_typeof(l->'body_index')<>'number'
            OR l->>'body_index' !~ '^[1-9][0-9]{0,6}$' OR (l->>'body_index')::integer>1000000 THEN RETURN false; END IF;
          IF l->'row_index'='null'::jsonb THEN
            IF l->'cell_index'<>'null'::jsonb OR l->'grid_column'<>'null'::jsonb OR l->'grid_span'<>'null'::jsonb OR l->'paragraph_index'<>'null'::jsonb THEN RETURN false; END IF;
          ELSE
            FOREACH k IN ARRAY ARRAY['row_index','cell_index','grid_column','grid_span','paragraph_index'] LOOP
              IF jsonb_typeof(l->k)<>'number' OR l->>k !~ '^[1-9][0-9]{0,6}$' OR (l->>k)::integer>1000000 THEN RETURN false; END IF;
            END LOOP;
            IF (l->>'grid_column')::integer<(l->>'cell_index')::integer OR (l->>'grid_column')::integer+(l->>'grid_span')::integer>1000001 THEN RETURN false; END IF;
          END IF;

ELSIF fmt='xlsx' THEN
IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(l) key) IS DISTINCT FROM ARRAY['cell_range','kind','part','schema_version','sheet_id','sheet_name']::text[]
OR l->>'kind' IS DISTINCT FROM 'xlsx_cells'
OR jsonb_typeof(l->'sheet_id')<>'number' OR l->>'sheet_id' !~ '^[1-9][0-9]{0,9}$' OR (l->>'sheet_id')::bigint>4294967295
OR jsonb_typeof(l->'sheet_name')<>'string' OR length(l->>'sheet_name') NOT BETWEEN 1 AND 31
OR translate(l->>'sheet_name',chr(92)||'/?*:[]','')<>l->>'sheet_name'
OR left(l->>'sheet_name',1)=chr(39) OR right(l->>'sheet_name',1)=chr(39)
OR l->>'sheet_name' ~ '[\x01-\x1f]'
OR jsonb_typeof(l->'part')<>'string' OR l->>'part' !~ '^xl/worksheets/[A-Za-z0-9_-]+[.]xml$'
OR jsonb_typeof(l->'cell_range')<>'string' THEN RETURN false; END IF;
addresses:=string_to_array(l->>'cell_range',':');
IF cardinality(addresses) NOT IN(1,2) THEN RETURN false; END IF;
firstcell:=f1.native_cell(addresses[1]); lastcell:=f1.native_cell(addresses[cardinality(addresses)]);
IF firstcell IS NULL OR lastcell IS NULL OR firstcell[1]>lastcell[1] OR firstcell[2]>lastcell[2] OR (cardinality(addresses)=2 AND firstcell=lastcell) THEN RETURN false; END IF;
ELSIF fmt='jpeg' THEN
IF (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(l) key) IS DISTINCT FROM ARRAY['exif_orientation','kind','rendered_height','rendered_sha256','rendered_width','schema_version','source_height','source_width']::text[]
OR l->>'kind' IS DISTINCT FROM 'image' OR jsonb_typeof(l->'rendered_sha256')<>'string' OR l->>'rendered_sha256' !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
FOREACH k IN ARRAY ARRAY['source_width','source_height','rendered_width','rendered_height','exif_orientation'] LOOP
IF jsonb_typeof(l->k)<>'number' OR l->>k !~ '^[1-9][0-9]{0,4}$' OR (l->>k)::integer>10000 THEN RETURN false; END IF;
END LOOP;
IF (l->>'exif_orientation')::integer>8 OR (l->>'source_width')::bigint*(l->>'source_height')::bigint>40000000
OR (l->>'rendered_width')::bigint*(l->>'rendered_height')::bigint>40000000 THEN RETURN false; END IF;
ELSE RETURN false;
END IF;
RETURN true;
END $$""")
    for sig in ("native_contract(text,text,text)", "native_ascii_json(text)", "native_canonical(jsonb)", "native_processing_valid(text,jsonb)", "native_cell(text)", "native_locator_valid(text,jsonb)"):
        op.execute(f"REVOKE ALL ON FUNCTION f1.{sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION f1.{sig} TO {ROLE}")


def _functions():
    op.execute(r"""CREATE OR REPLACE FUNCTION f1.native_source(p_version uuid) RETURNS jsonb
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
          'source_format',CASE WHEN d.content_type='image/jpeg' THEN 'jpeg' WHEN d.content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' THEN 'xlsx' ELSE 'docx' END,
          'source_sha256',t.content_sha256,'source_size',t.source_size,'source_etag',t.source_etag,'object_key',t.object_key);
      END $$""")
    op.execute(r"""CREATE OR REPLACE FUNCTION f1.register_native_extraction_job(p_version uuid,p_parser text,p_profile text) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE s jsonb; j f1.material_evidence_job; eid uuid; actor text;
      BEGIN
        eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor:=nullif(current_setting('f1.sub',true),'');
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL
          THEN RAISE EXCEPTION 'NATIVE_REGISTER_INVALID'; END IF;
        PERFORM t.id FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id AND v.enterprise_id=t.enterprise_id
          WHERE v.id=p_version FOR UPDATE OF t;
        s:=f1.native_source(p_version);
        IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid
          OR NOT f1.native_actor(eid,actor) THEN RAISE EXCEPTION 'NATIVE_SOURCE_UNAVAILABLE'; END IF;
        IF NOT f1.native_contract(s->>'source_format',p_parser,p_profile) THEN RAISE EXCEPTION 'NATIVE_REGISTER_INVALID'; END IF;
        INSERT INTO f1.material_evidence_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,
          source_document_id,upload_task_id,source_sha256,source_size,source_etag,object_key,source_format,
          parser_version,extraction_contract,support_profile,revision_id,actor_sub)
        VALUES(gen_random_uuid(),eid,(s->>'knowledge_scope_id')::uuid,(s->>'document_record_id')::uuid,p_version,
          (s->>'source_document_id')::uuid,(s->>'upload_task_id')::uuid,s->>'source_sha256',(s->>'source_size')::bigint,
          s->>'source_etag',s->>'object_key',s->>'source_format',p_parser,1,p_profile,gen_random_uuid(),actor)
        ON CONFLICT(enterprise_id,document_version_id,source_sha256,parser_version,extraction_contract,support_profile) DO NOTHING
        RETURNING * INTO j;
        IF j.id IS NULL THEN SELECT * INTO j FROM f1.material_evidence_job WHERE enterprise_id=eid AND document_version_id=p_version
          AND source_sha256=s->>'source_sha256' AND parser_version=p_parser AND support_profile=p_profile AND extraction_contract=1; END IF;
        RETURN f1.native_job_json(j);
      END $$""")
    op.execute(r"""CREATE OR REPLACE FUNCTION f1.finalize_native_extraction(p_job uuid,p_token uuid,p jsonb) RETURNS uuid
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j f1.material_evidence_job; s jsonb; f jsonb; l jsonb; debt jsonb; k text;
        expected integer; processed integer; n integer; nonblank integer:=0; chars bigint:=0; pos integer:=0;
        aad bytea; identity jsonb; idhash bytea; calculated_id uuid;
      BEGIN
        IF session_user<>'f1_worker' THEN RAISE EXCEPTION 'NATIVE_WORKER_REQUIRED'; END IF;
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=p_job;
        IF j.id IS NULL THEN RETURN NULL; END IF;
        -- All source locks precede the job lock; release takes this same order.
        s:=f1.native_source(j.document_version_id);
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=p_job FOR UPDATE;
        IF s IS NULL OR NOT(to_jsonb(j) @> s) THEN
          PERFORM f1.finish_native_extraction_failure(p_job,p_token,'NATIVE_SOURCE_INVALID',NULL); RETURN NULL;
        END IF;
        IF NOT f1.native_actor(j.enterprise_id,j.actor_sub) THEN
          PERFORM f1.finish_native_extraction_failure(p_job,p_token,'NATIVE_ACTOR_REVOKED',NULL); RETURN NULL;
        END IF;
        IF j.state='done' THEN
          IF j.completed_token=p_token AND p->>'revision_id'=j.revision_id::text THEN RETURN j.revision_id; END IF;
          RETURN NULL;
        END IF;
        IF j.state<>'running' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        IF NOT (p ? 'processing_identity') THEN p:=p||jsonb_build_object('processing_identity','{}'::jsonb); END IF;
        IF p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>24000000
          OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key) IS DISTINCT FROM ARRAY[
            'coverage_state','debts','expected_block_count','extraction_contract','fragments','manifest_sha256','parser_version',
            'processed_block_count','processing_identity','report_source_eligible','revision_id','source_format','source_sha256','support_profile']::text[]
          OR p->>'revision_id' IS DISTINCT FROM j.revision_id::text OR p->>'source_sha256' IS DISTINCT FROM j.source_sha256
          OR p->>'source_format' IS DISTINCT FROM j.source_format OR p->>'parser_version' IS DISTINCT FROM j.parser_version
          OR p->>'support_profile' IS DISTINCT FROM j.support_profile OR p->'extraction_contract' IS DISTINCT FROM '1'::jsonb OR p->>'extraction_contract' IS DISTINCT FROM '1'
          OR jsonb_typeof(p->'manifest_sha256')<>'string' OR p->>'manifest_sha256' !~ '^[0-9a-f]{64}$'
          OR jsonb_typeof(p->'expected_block_count')<>'number' OR p->>'expected_block_count' !~ '^(0|[1-9][0-9]{0,5})$'
          OR jsonb_typeof(p->'processed_block_count')<>'number' OR p->>'processed_block_count' !~ '^(0|[1-9][0-9]{0,5})$'
          OR jsonb_typeof(p->'report_source_eligible')<>'boolean' OR jsonb_typeof(p->'debts')<>'array'
          OR jsonb_typeof(p->'fragments')<>'array' THEN RAISE EXCEPTION 'NATIVE_PAYLOAD_INVALID'; END IF;
        IF NOT f1.native_processing_valid(j.source_format,p->'processing_identity') THEN RAISE EXCEPTION 'NATIVE_PROCESSING_INVALID'; END IF;
        expected:=(p->>'expected_block_count')::integer; processed:=(p->>'processed_block_count')::integer;
        n:=jsonb_array_length(p->'fragments');
        IF expected>400000 OR processed>20000 OR processed>expected OR n>20000 OR jsonb_array_length(p->'debts')>256
          OR NOT((p->>'coverage_state'='complete' AND jsonb_array_length(p->'debts')=0 AND expected=processed AND processed=n)
            OR (p->>'coverage_state'='partial' AND jsonb_array_length(p->'debts')>0 AND n=0)) THEN RAISE EXCEPTION 'NATIVE_COVERAGE_INVALID'; END IF;
        FOR debt IN SELECT value FROM jsonb_array_elements(p->'debts') LOOP
          IF jsonb_typeof(debt)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(debt) key) IS DISTINCT FROM ARRAY['part','path','reason_code']::text[]
            OR jsonb_typeof(debt->'reason_code')<>'string' OR debt->>'reason_code' !~ '^[A-Z0-9_]{1,80}$'
            OR jsonb_typeof(debt->'part')<>'string' OR length(debt->>'part')>1024
            OR jsonb_typeof(debt->'path')<>'string' OR length(debt->>'path')>2048 THEN RAISE EXCEPTION 'NATIVE_DEBT_INVALID'; END IF;
        END LOOP;
        FOR f IN SELECT value FROM jsonb_array_elements(p->'fragments') LOOP
          IF jsonb_typeof(f)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(f) key) IS DISTINCT FROM ARRAY[
            'body_aad_sha256','body_ciphertext_hex','body_sha256','character_count','has_nonblank_text','id','locator','locator_sha256','ordinal','token_sha256']::text[]
            OR jsonb_typeof(f->'ordinal')<>'number' OR f->>'ordinal' IS DISTINCT FROM pos::text
            OR jsonb_typeof(f->'character_count')<>'number' OR f->>'character_count' !~ '^(0|[1-9][0-9]{0,6})$'
            OR jsonb_typeof(f->'has_nonblank_text')<>'boolean'
            OR jsonb_typeof(f->'id')<>'string' OR f->>'id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            OR jsonb_typeof(f->'body_ciphertext_hex')<>'string' OR f->>'body_ciphertext_hex' !~ '^[0-9a-f]+$'
            OR left(f->>'body_ciphertext_hex',10)<>'46314d5231'
            OR length(f->>'body_ciphertext_hex') NOT BETWEEN 66 AND 16000066 OR length(f->>'body_ciphertext_hex')%2<>0
            THEN RAISE EXCEPTION 'NATIVE_FRAGMENT_INVALID'; END IF;
          FOREACH k IN ARRAY ARRAY['body_aad_sha256','body_sha256','locator_sha256','token_sha256'] LOOP
            IF jsonb_typeof(f->k)<>'string' OR f->>k !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'NATIVE_FRAGMENT_HASH_INVALID'; END IF;
          END LOOP;
          chars:=chars+(f->>'character_count')::bigint;
          IF chars>2000000 OR ((f->>'character_count')::integer=0 AND (f->>'has_nonblank_text')::boolean) THEN RAISE EXCEPTION 'NATIVE_CHARACTER_COUNT_INVALID'; END IF;
          IF (f->>'has_nonblank_text')::boolean THEN nonblank:=nonblank+1; END IF;
          l:=f->'locator';
          IF NOT f1.native_locator_valid(j.source_format,l) THEN RAISE EXCEPTION 'NATIVE_LOCATOR_INVALID'; END IF;
          IF j.source_format='jpeg' AND l->>'rendered_sha256' IS DISTINCT FROM p->'processing_identity'->>'image_sha256' THEN RAISE EXCEPTION 'NATIVE_PROCESSING_INVALID'; END IF;
          IF encode(sha256(convert_to(f1.native_canonical(l),'UTF8')),'hex')<>f->>'locator_sha256' THEN RAISE EXCEPTION 'NATIVE_LOCATOR_HASH_INVALID'; END IF;
          identity:=jsonb_build_object('schema_version',2,'enterprise_id',j.enterprise_id,'knowledge_scope_id',j.knowledge_scope_id,
            'document_record_id',j.document_record_id,'document_version_id',j.document_version_id,'extraction_revision_id',j.revision_id,
            'source_sha256',j.source_sha256,'source_format',j.source_format,'parser_version',j.parser_version,'extraction_contract',1,
            'locator',l,'locator_sha256',f->>'locator_sha256','ordinal',pos,'body_sha256',f->>'body_sha256');
          aad:=convert_to('anhuan.material.evidence.v2','UTF8')||decode('00','hex')||convert_to(f1.native_canonical(identity),'UTF8');
          IF encode(sha256(aad),'hex')<>f->>'body_aad_sha256' THEN RAISE EXCEPTION 'NATIVE_AAD_INVALID'; END IF;
          idhash:=f1.native_sha1(decode('ca6db2f515145576b72fbad8971ee7a4','hex')||aad);
          idhash:=set_byte(set_byte(substring(idhash FROM 1 FOR 16),6,(get_byte(idhash,6)&15)|80),8,(get_byte(idhash,8)&63)|128);
          calculated_id:=encode(idhash,'hex')::uuid;
          IF calculated_id::text<>f->>'id' THEN RAISE EXCEPTION 'NATIVE_ID_INVALID'; END IF;
          pos:=pos+1;
        END LOOP;
        IF (p->>'report_source_eligible')::boolean IS DISTINCT FROM (p->>'coverage_state'='complete' AND nonblank>0) THEN RAISE EXCEPTION 'NATIVE_ELIGIBILITY_INVALID'; END IF;
        INSERT INTO f1.material_extraction_revision(id,enterprise_id,job_id,knowledge_scope_id,document_record_id,document_version_id,
          source_document_id,upload_task_id,source_sha256,source_format,parser_version,extraction_contract,support_profile,
          manifest_sha256,coverage_state,debts,expected_block_count,processed_block_count,fragment_count,nonblank_fragment_count,actor_sub,processing_identity)
        VALUES(j.revision_id,j.enterprise_id,j.id,j.knowledge_scope_id,j.document_record_id,j.document_version_id,j.source_document_id,j.upload_task_id,
          j.source_sha256,j.source_format,j.parser_version,j.extraction_contract,j.support_profile,p->>'manifest_sha256',p->>'coverage_state',p->'debts',expected,processed,n,nonblank,j.actor_sub,p->'processing_identity');
        INSERT INTO f1.material_evidence_fragment(id,enterprise_id,extraction_revision_id,ordinal,locator,locator_sha256,token_sha256,body_sha256,
          character_count,has_nonblank_text,body_ciphertext,body_aad_sha256)
        SELECT (x->>'id')::uuid,j.enterprise_id,j.revision_id,(x->>'ordinal')::integer,x->'locator',x->>'locator_sha256',x->>'token_sha256',x->>'body_sha256',
          (x->>'character_count')::integer,(x->>'has_nonblank_text')::boolean,decode(x->>'body_ciphertext_hex','hex'),x->>'body_aad_sha256'
        FROM jsonb_array_elements(p->'fragments') x;
        INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result)
        VALUES(gen_random_uuid(),j.enterprise_id,j.actor_sub,'native.extraction.completed','material_extraction_revision',j.revision_id::text,
          jsonb_build_object('job_id',j.id,'coverage_state',p->>'coverage_state','fragment_count',n)::text);
        UPDATE f1.material_evidence_job SET state='done',completed_token=p_token,lease_token=NULL,lease_until=NULL,
          completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=j.id AND lease_token=p_token AND lease_until>clock_timestamp();
        IF NOT FOUND THEN RAISE EXCEPTION 'NATIVE_LEASE_EXPIRED'; END IF;
        RETURN j.revision_id;
      END $$""")
    op.execute(r"""CREATE OR REPLACE FUNCTION f1.finish_native_extraction_failure(p_job uuid,p_token uuid,p_reason text,p_retry_seconds integer DEFAULT NULL) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF session_user<>'f1_worker' OR p_reason IS NULL OR p_reason NOT IN('NATIVE_QUEUE_UNAVAILABLE','NATIVE_SOURCE_UNAVAILABLE','NATIVE_SOURCE_INVALID','NATIVE_PARSE_REJECTED','NATIVE_PARSER_UNSUPPORTED','NATIVE_ACTOR_REVOKED','NATIVE_EXTRACTION_FAILED','NATIVE_OCR_UNAVAILABLE')
        OR (p_retry_seconds IS NOT NULL AND p_retry_seconds NOT BETWEEN 1 AND 86400) THEN RAISE EXCEPTION 'NATIVE_FAILURE_INVALID'; END IF;
      UPDATE f1.material_evidence_job SET state=CASE WHEN p_retry_seconds IS NULL OR attempt>=100 THEN 'blocked' ELSE 'retry_wait' END,
        next_attempt_at=CASE WHEN p_retry_seconds IS NOT NULL AND attempt<100 THEN clock_timestamp()+make_interval(secs=>p_retry_seconds) END,
        completed_at=CASE WHEN p_retry_seconds IS NULL OR attempt>=100 THEN clock_timestamp() END,
        lease_token=NULL,lease_until=NULL,reason_code=p_reason,updated_at=clock_timestamp()
        WHERE id=p_job AND state='running' AND lease_token=p_token AND lease_until>clock_timestamp();
      RETURN FOUND; END $$""")


def downgrade():
    raise RuntimeError("NATIVE_EVIDENCE_RESTORE_REQUIRED")


def _reader():
    op.execute("""CREATE FUNCTION f1.read_native_extraction_fragments(p_version uuid,p_revision uuid,p_after integer,p_limit integer) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid; actor text; revision f1.material_extraction_revision; result jsonb;
      BEGIN
      eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
      actor:=nullif(current_setting('f1.sub',true),'');
      IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL OR p_version IS NULL OR p_revision IS NULL
        OR p_after IS NULL OR p_after NOT BETWEEN -1 AND 19999 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
        THEN RAISE EXCEPTION 'NATIVE_READ_INVALID'; END IF;
      IF NOT f1.native_actor(eid,actor) THEN RETURN NULL; END IF;
      -- Old versions remain readable. Unlike worker submission this does not
      -- require latest_version_no; it still binds the exact released source.
      SELECT r.* INTO revision FROM f1.material_extraction_revision r
      JOIN f1.document_version v ON v.enterprise_id=r.enterprise_id AND v.id=r.document_version_id
        AND v.document_record_id=r.document_record_id AND v.source_document_id=r.source_document_id AND v.upload_task_id=r.upload_task_id
      JOIN f1.document_record d ON d.enterprise_id=r.enterprise_id AND d.id=r.document_record_id AND d.knowledge_scope_id=r.knowledge_scope_id
      JOIN f1.upload_task t ON t.enterprise_id=r.enterprise_id AND t.id=r.upload_task_id AND t.document_id=r.source_document_id AND t.content_sha256=r.source_sha256
      WHERE r.enterprise_id=eid AND r.id=p_revision AND v.id=p_version
        AND t.pipeline_kind='controlled_ingestion' AND t.status='done' AND t.object_state='ready'
        AND t.quarantine_status='released' AND t.released_at IS NOT NULL AND t.rejected_at IS NULL
        AND t.scan_verdict='clean';
      IF revision.id IS NULL THEN RETURN NULL; END IF;
      SELECT COALESCE(jsonb_agg(to_jsonb(q) ORDER BY ordinal),'[]'::jsonb) INTO result FROM (
        SELECT f.id,f.enterprise_id,f.extraction_revision_id,f.ordinal,f.locator,f.locator_sha256,f.body_sha256,f.token_sha256,
          f.character_count,f.has_nonblank_text,encode(f.body_ciphertext,'hex') AS body_ciphertext_hex,f.body_aad_sha256,
          revision.knowledge_scope_id AS knowledge_scope_id,revision.document_record_id AS document_record_id,
          revision.document_version_id AS document_version_id,revision.source_sha256 AS source_sha256,
          revision.parser_version AS parser_version,revision.extraction_contract AS extraction_contract
        FROM f1.material_evidence_fragment f WHERE f.enterprise_id=eid AND f.extraction_revision_id=revision.id AND f.ordinal>p_after
        ORDER BY f.ordinal LIMIT p_limit+1
      ) q;
      RETURN jsonb_build_object('revision_id',revision.id,'items',result);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_native_extraction_fragments(uuid,uuid,integer,integer) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_native_extraction_fragments(uuid,uuid,integer,integer) TO f1_api')
