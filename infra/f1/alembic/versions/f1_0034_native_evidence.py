"""Durable native DOCX extraction, immutable v2 revisions and encrypted blocks."""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op

revision: str = 'f1_0034'
down_revision: str | None = 'f1_0033'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
ROLE = 'f1_material_evidence_definer'
TABLES = ('material_evidence_job', 'material_extraction_revision', 'material_evidence_fragment')
SIGNATURES = (
    'register_native_extraction_job(uuid,text,text)',
    'claim_native_extraction_jobs(integer,integer)',
    'read_native_extraction_claim(uuid,uuid)',
    'renew_native_extraction_lease(uuid,uuid,integer)',
    'finish_native_extraction_failure(uuid,uuid,text,integer)',
    'finalize_native_extraction(uuid,uuid,jsonb)',
)


def upgrade() -> None:
    op.execute(f"""DO $$ BEGIN
      IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}') THEN
        RAISE EXCEPTION 'NATIVE_DEFINER_REQUIRED'; END IF;
      IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{ROLE}' AND
        (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication OR rolbypassrls))
        OR EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid IN(m.roleid,m.member) WHERE r.rolname='{ROLE}')
        THEN RAISE EXCEPTION 'NATIVE_DEFINER_UNSAFE'; END IF;
    END $$""")
    _crypto()
    _tables()
    _helpers()
    _functions()
    _grants()


def _crypto() -> None:
    # UUIDv5 uses SHA-1 as an identity digest, not as an encryption primitive.
    # Reuse an existing extension in place; never relocate shared extensions.
    op.execute('RESET ROLE')
    connection = op.get_bind()
    namespace = connection.exec_driver_sql("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'").scalar_one_or_none()
    if namespace is None:
        op.execute('CREATE SCHEMA f1_native_crypto AUTHORIZATION f0d_migration')
        op.execute('REVOKE ALL ON SCHEMA f1_native_crypto FROM PUBLIC')
        op.execute('CREATE EXTENSION pgcrypto WITH SCHEMA f1_native_crypto')
        op.execute('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA f1_native_crypto FROM PUBLIC')
        namespace = 'f1_native_crypto'
    quoted = connection.dialect.identifier_preparer.quote_identifier(namespace)
    op.execute(f'GRANT USAGE ON SCHEMA {quoted} TO {ROLE},f0d_migration')
    op.execute(f'GRANT EXECUTE ON FUNCTION {quoted}.digest(bytea,text) TO {ROLE},f0d_migration')
    op.execute('SET LOCAL ROLE f0d_migration')
    op.execute(f"""CREATE FUNCTION f1.native_sha1(value bytea) RETURNS bytea
      LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
      SELECT {quoted}.digest(value,'sha1') $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.native_sha1(bytea) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.native_sha1(bytea) TO {ROLE}')


def _tables() -> None:
    op.execute("""CREATE TABLE f1.material_evidence_job (
      id uuid PRIMARY KEY, enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
      knowledge_scope_id uuid NOT NULL, document_record_id uuid NOT NULL,
      document_version_id uuid NOT NULL, source_document_id uuid NOT NULL, upload_task_id uuid NOT NULL,
      source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
      source_size bigint NOT NULL CHECK(source_size BETWEEN 1 AND 52428800),
      source_etag text NOT NULL CHECK(length(source_etag) BETWEEN 1 AND 255),
      object_key text NOT NULL CHECK(length(object_key) BETWEEN 1 AND 1024),
      source_format text NOT NULL DEFAULT 'docx' CHECK(source_format='docx'),
      parser_version text NOT NULL CHECK(parser_version='docx-native-1'),
      extraction_contract integer NOT NULL CHECK(extraction_contract=1),
      support_profile text NOT NULL CHECK(support_profile='transitional-main-body-simple-table-1'),
      revision_id uuid NOT NULL, actor_sub text NOT NULL CHECK(length(actor_sub) BETWEEN 1 AND 255 AND actor_sub=btrim(actor_sub) AND actor_sub !~ '[[:cntrl:]]'),
      state text NOT NULL DEFAULT 'pending', attempt integer NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 100),
      lease_token uuid, lease_until timestamptz, completed_token uuid, next_attempt_at timestamptz,
      reason_code text CHECK(reason_code ~ '^[A-Z0-9_]{1,80}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(), completed_at timestamptz,
      UNIQUE(enterprise_id,id), UNIQUE(enterprise_id,id,revision_id),
      UNIQUE(enterprise_id,document_version_id,source_sha256,parser_version,extraction_contract,support_profile),
      FOREIGN KEY(enterprise_id,knowledge_scope_id) REFERENCES f1.material_knowledge_scope(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_record_id) REFERENCES f1.document_record(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_version_id) REFERENCES f1.document_version(enterprise_id,id),
      FOREIGN KEY(enterprise_id,upload_task_id,source_document_id) REFERENCES f1.upload_task(enterprise_id,id,document_id),
      CHECK((state='pending' AND attempt=0 AND lease_token IS NULL AND lease_until IS NULL AND next_attempt_at IS NULL AND reason_code IS NULL AND completed_at IS NULL AND completed_token IS NULL)
       OR (state='running' AND attempt>0 AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND next_attempt_at IS NULL AND reason_code IS NULL AND completed_at IS NULL AND completed_token IS NULL)
       OR (state='retry_wait' AND attempt>0 AND lease_token IS NULL AND lease_until IS NULL AND next_attempt_at IS NOT NULL AND reason_code IS NOT NULL AND completed_at IS NULL AND completed_token IS NULL)
       OR (state='done' AND attempt>0 AND lease_token IS NULL AND lease_until IS NULL AND next_attempt_at IS NULL AND reason_code IS NULL AND completed_at IS NOT NULL AND completed_token IS NOT NULL)
       OR (state='blocked' AND lease_token IS NULL AND lease_until IS NULL AND next_attempt_at IS NULL AND reason_code IS NOT NULL AND completed_at IS NOT NULL AND completed_token IS NULL))
    )""")
    op.execute("CREATE INDEX native_job_due ON f1.material_evidence_job(state,next_attempt_at,lease_until,id)")
    op.execute("""CREATE TABLE f1.material_extraction_revision (
      id uuid PRIMARY KEY, enterprise_id uuid NOT NULL, job_id uuid NOT NULL,
      knowledge_scope_id uuid NOT NULL, document_record_id uuid NOT NULL, document_version_id uuid NOT NULL,
      source_document_id uuid NOT NULL, upload_task_id uuid NOT NULL,
      source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'), source_format text NOT NULL CHECK(source_format='docx'),
      parser_version text NOT NULL CHECK(parser_version='docx-native-1'), extraction_contract integer NOT NULL CHECK(extraction_contract=1),
      support_profile text NOT NULL CHECK(support_profile='transitional-main-body-simple-table-1'),
      manifest_sha256 text NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
      coverage_state text NOT NULL, debts jsonb NOT NULL CHECK(jsonb_typeof(debts)='array' AND jsonb_array_length(debts)<=256),
      expected_block_count integer NOT NULL CHECK(expected_block_count BETWEEN 0 AND 400000),
      processed_block_count integer NOT NULL CHECK(processed_block_count BETWEEN 0 AND 20000 AND processed_block_count<=expected_block_count),
      fragment_count integer NOT NULL CHECK(fragment_count BETWEEN 0 AND 20000),
      nonblank_fragment_count integer NOT NULL CHECK(nonblank_fragment_count BETWEEN 0 AND fragment_count),
      report_source_eligible boolean GENERATED ALWAYS AS(coverage_state='complete' AND nonblank_fragment_count>0) STORED,
      actor_sub text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(enterprise_id,id), UNIQUE(enterprise_id,job_id),
      FOREIGN KEY(enterprise_id,job_id,id) REFERENCES f1.material_evidence_job(enterprise_id,id,revision_id),
      FOREIGN KEY(enterprise_id,knowledge_scope_id) REFERENCES f1.material_knowledge_scope(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_record_id) REFERENCES f1.document_record(enterprise_id,id),
      FOREIGN KEY(enterprise_id,document_version_id) REFERENCES f1.document_version(enterprise_id,id),
      FOREIGN KEY(enterprise_id,upload_task_id,source_document_id) REFERENCES f1.upload_task(enterprise_id,id,document_id),
      CHECK((coverage_state='partial' AND jsonb_array_length(debts)>0 AND fragment_count=0 AND nonblank_fragment_count=0)
        OR (coverage_state='complete' AND jsonb_array_length(debts)=0 AND expected_block_count=processed_block_count AND fragment_count=processed_block_count))
    )""")
    op.execute("""CREATE TABLE f1.material_evidence_fragment (
      id uuid PRIMARY KEY, enterprise_id uuid NOT NULL, extraction_revision_id uuid NOT NULL,
      ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 19999), locator jsonb NOT NULL,
      locator_sha256 text NOT NULL CHECK(locator_sha256 ~ '^[0-9a-f]{64}$'),
      token_sha256 text NOT NULL CHECK(token_sha256 ~ '^[0-9a-f]{64}$'),
      body_sha256 text NOT NULL CHECK(body_sha256 ~ '^[0-9a-f]{64}$'),
      character_count integer NOT NULL CHECK(character_count BETWEEN 0 AND 2000000),
      has_nonblank_text boolean NOT NULL CHECK(character_count>0 OR NOT has_nonblank_text),
      body_ciphertext bytea NOT NULL CHECK(octet_length(body_ciphertext) BETWEEN 33 AND 8000033),
      body_aad_sha256 text NOT NULL CHECK(body_aad_sha256 ~ '^[0-9a-f]{64}$'),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(enterprise_id,extraction_revision_id,ordinal),
      FOREIGN KEY(enterprise_id,extraction_revision_id) REFERENCES f1.material_extraction_revision(enterprise_id,id)
    )""")
    op.execute("""CREATE FUNCTION f1.guard_native_immutable() RETURNS trigger
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$ BEGIN
      IF TG_TABLE_NAME='material_evidence_job' AND TG_OP='UPDATE' THEN
        IF (to_jsonb(NEW)-ARRAY['state','attempt','lease_token','lease_until','completed_token','next_attempt_at','reason_code','updated_at','completed_at'])
          IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','attempt','lease_token','lease_until','completed_token','next_attempt_at','reason_code','updated_at','completed_at'])
          THEN RAISE EXCEPTION 'NATIVE_JOB_IDENTITY_IMMUTABLE'; END IF;
        IF OLD.state IN('done','blocked') THEN RAISE EXCEPTION 'NATIVE_JOB_TERMINAL'; END IF;
        RETURN NEW;
      END IF;
      RAISE EXCEPTION 'NATIVE_REVISION_IMMUTABLE'; END $$""")
    for table in TABLES:
        op.execute(f"CREATE TRIGGER native_immutable BEFORE UPDATE OR DELETE ON f1.{table} FOR EACH ROW EXECUTE FUNCTION f1.guard_native_immutable()")
    op.execute("REVOKE ALL ON FUNCTION f1.guard_native_immutable() FROM PUBLIC")


def _helpers() -> None:
    # Invoker-only helpers are not granted to runtime roles. They run only
    # inside the six closed definer entrypoints and do not bypass FORCE RLS.
    op.execute("""CREATE FUNCTION f1.native_source(p_version uuid) RETURNS jsonb
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
          OR d.content_type<>'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
          OR t.pipeline_kind<>'controlled_ingestion' OR t.object_key !~ '^[0-9a-f]{32}[.]docx$'
          OR t.status<>'done' OR t.object_state<>'ready' OR t.processing_stage<>'ready'
          OR t.preview_status<>'ready' OR t.scan_verdict<>'clean' OR t.quarantine_status<>'released'
          OR t.released_at IS NULL OR t.rejected_at IS NOT NULL OR t.source_size IS NULL
          OR t.source_size NOT BETWEEN 1 AND 52428800 OR t.source_size IS DISTINCT FROM d.size
          OR t.source_etag IS NULL OR length(t.source_etag) NOT BETWEEN 1 AND 255
          OR t.object_key IS DISTINCT FROM d.object_key THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('enterprise_id',v.enterprise_id,'knowledge_scope_id',r.knowledge_scope_id,
          'document_record_id',r.id,'document_version_id',v.id,'source_document_id',d.id,'upload_task_id',t.id,
          'source_sha256',t.content_sha256,'source_size',t.source_size,'source_etag',t.source_etag,'object_key',t.object_key);
      END $$""")
    op.execute("""CREATE FUNCTION f1.native_actor(p_eid uuid,p_sub text) RETURNS boolean
      LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $$
      DECLARE m f1.enterprise_user; kind text;
      BEGIN
        SELECT business_kind INTO kind FROM f1.enterprise WHERE id=p_eid FOR SHARE;
        SELECT eu.* INTO m FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id
          WHERE eu.enterprise_id=p_eid AND up.keycloak_sub=p_sub FOR SHARE OF eu;
        RETURN COALESCE(kind='service_provider' AND m.role='enterprise_admin' AND m.revoked_at IS NULL,false);
      END $$""")
    op.execute("""CREATE FUNCTION f1.native_job_json(j f1.material_evidence_job) RETURNS jsonb
      LANGUAGE sql SECURITY INVOKER SET search_path=pg_catalog AS $$
        SELECT (to_jsonb(j)-ARRAY['id','completed_token']) || jsonb_build_object('job_id',j.id)
      $$""")
    # Canonical object strings below contain only validated ASCII identifiers,
    # fixed labels and integers. JSONB storage ordering is not Python sort_keys.
    op.execute("""CREATE FUNCTION f1.native_canonical(j jsonb) RETURNS text
      LANGUAGE sql IMMUTABLE SECURITY INVOKER SET search_path=pg_catalog AS $$
        SELECT '{'||COALESCE(string_agg(to_jsonb(key)::text||':'||
          CASE WHEN jsonb_typeof(value)='object' THEN f1.native_canonical(value) ELSE value::text END,',' ORDER BY key COLLATE "C"),'')||'}'
        FROM jsonb_each(j)
      $$""")
    for sig in ('native_source(uuid)','native_actor(uuid,text)','native_job_json(f1.material_evidence_job)','native_canonical(jsonb)'):
        op.execute(f'REVOKE ALL ON FUNCTION f1.{sig} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{sig} TO {ROLE}')


def _functions() -> None:
    op.execute("""CREATE FUNCTION f1.register_native_extraction_job(p_version uuid,p_parser text,p_profile text) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE s jsonb; j f1.material_evidence_job; eid uuid; actor text;
      BEGIN
        eid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor:=nullif(current_setting('f1.sub',true),'');
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL
          OR p_parser IS DISTINCT FROM 'docx-native-1' OR p_profile IS DISTINCT FROM 'transitional-main-body-simple-table-1'
          THEN RAISE EXCEPTION 'NATIVE_REGISTER_INVALID'; END IF;
        PERFORM t.id FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id AND v.enterprise_id=t.enterprise_id
          WHERE v.id=p_version FOR UPDATE OF t;
        s:=f1.native_source(p_version);
        IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid
          OR NOT f1.native_actor(eid,actor) THEN RAISE EXCEPTION 'NATIVE_SOURCE_UNAVAILABLE'; END IF;
        INSERT INTO f1.material_evidence_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,
          source_document_id,upload_task_id,source_sha256,source_size,source_etag,object_key,
          parser_version,extraction_contract,support_profile,revision_id,actor_sub)
        VALUES(gen_random_uuid(),eid,(s->>'knowledge_scope_id')::uuid,(s->>'document_record_id')::uuid,p_version,
          (s->>'source_document_id')::uuid,(s->>'upload_task_id')::uuid,s->>'source_sha256',(s->>'source_size')::bigint,
          s->>'source_etag',s->>'object_key',p_parser,1,p_profile,gen_random_uuid(),actor)
        ON CONFLICT(enterprise_id,document_version_id,source_sha256,parser_version,extraction_contract,support_profile) DO NOTHING
        RETURNING * INTO j;
        IF j.id IS NULL THEN SELECT * INTO j FROM f1.material_evidence_job WHERE enterprise_id=eid AND document_version_id=p_version
          AND source_sha256=s->>'source_sha256' AND parser_version=p_parser AND support_profile=p_profile AND extraction_contract=1; END IF;
        RETURN f1.native_job_json(j);
      END $$""")
    op.execute("""CREATE FUNCTION f1.claim_native_extraction_jobs(p_limit integer,p_lease_seconds integer) RETURNS SETOF jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j f1.material_evidence_job;
      BEGIN
        IF session_user<>'f1_worker' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
          OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 30 AND 900 THEN RAISE EXCEPTION 'NATIVE_CLAIM_INVALID'; END IF;
        FOR j IN SELECT * FROM f1.material_evidence_job WHERE state='pending'
          OR (state='retry_wait' AND next_attempt_at<=clock_timestamp())
          OR (state='running' AND lease_until<=clock_timestamp()) ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT p_limit
        LOOP
          IF j.attempt>=100 THEN
            UPDATE f1.material_evidence_job SET state='blocked',lease_token=NULL,lease_until=NULL,next_attempt_at=NULL,
              reason_code='NATIVE_ATTEMPTS_EXHAUSTED',completed_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=j.id;
            CONTINUE;
          END IF;
          UPDATE f1.material_evidence_job SET state='running',attempt=attempt+1,lease_token=gen_random_uuid(),
            lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds),next_attempt_at=NULL,reason_code=NULL,
            updated_at=clock_timestamp() WHERE id=j.id RETURNING * INTO j;
          RETURN NEXT f1.native_job_json(j);
        END LOOP;
      END $$""")
    op.execute("""CREATE FUNCTION f1.read_native_extraction_claim(p_job uuid,p_token uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE j f1.material_evidence_job; s jsonb;
      BEGIN
        IF session_user<>'f1_worker' THEN RAISE EXCEPTION 'NATIVE_WORKER_REQUIRED'; END IF;
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=p_job;
        IF j.id IS NULL OR j.state<>'running' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        s:=f1.native_source(j.document_version_id);
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=p_job FOR UPDATE;
        IF j.state<>'running' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        IF s IS NULL OR NOT(to_jsonb(j) @> s) THEN
          PERFORM f1.finish_native_extraction_failure(p_job,p_token,'NATIVE_SOURCE_INVALID',NULL); RETURN NULL;
        END IF;
        IF NOT f1.native_actor(j.enterprise_id,j.actor_sub) THEN
          PERFORM f1.finish_native_extraction_failure(p_job,p_token,'NATIVE_ACTOR_REVOKED',NULL); RETURN NULL;
        END IF;
        SELECT * INTO j FROM f1.material_evidence_job WHERE id=p_job;
        IF j.state<>'running' OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
        RETURN f1.native_job_json(j);
      END $$""")
    op.execute("""CREATE FUNCTION f1.renew_native_extraction_lease(p_job uuid,p_token uuid,p_seconds integer) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF session_user<>'f1_worker' OR p_seconds IS NULL OR p_seconds NOT BETWEEN 30 AND 900 THEN RAISE EXCEPTION 'NATIVE_RENEW_INVALID'; END IF;
      UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+make_interval(secs=>p_seconds),updated_at=clock_timestamp()
        WHERE id=p_job AND state='running' AND lease_token=p_token AND lease_until>clock_timestamp();
      RETURN FOUND; END $$""")
    op.execute("""CREATE FUNCTION f1.finish_native_extraction_failure(p_job uuid,p_token uuid,p_reason text,p_retry_seconds integer DEFAULT NULL) RETURNS boolean
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$ BEGIN
      IF session_user<>'f1_worker' OR p_reason IS NULL OR p_reason NOT IN('NATIVE_QUEUE_UNAVAILABLE','NATIVE_SOURCE_UNAVAILABLE','NATIVE_SOURCE_INVALID','NATIVE_PARSE_REJECTED','NATIVE_PARSER_UNSUPPORTED','NATIVE_ACTOR_REVOKED','NATIVE_EXTRACTION_FAILED')
        OR (p_retry_seconds IS NOT NULL AND p_retry_seconds NOT BETWEEN 1 AND 86400) THEN RAISE EXCEPTION 'NATIVE_FAILURE_INVALID'; END IF;
      UPDATE f1.material_evidence_job SET state=CASE WHEN p_retry_seconds IS NULL OR attempt>=100 THEN 'blocked' ELSE 'retry_wait' END,
        next_attempt_at=CASE WHEN p_retry_seconds IS NOT NULL AND attempt<100 THEN clock_timestamp()+make_interval(secs=>p_retry_seconds) END,
        completed_at=CASE WHEN p_retry_seconds IS NULL OR attempt>=100 THEN clock_timestamp() END,
        lease_token=NULL,lease_until=NULL,reason_code=p_reason,updated_at=clock_timestamp()
        WHERE id=p_job AND state='running' AND lease_token=p_token AND lease_until>clock_timestamp();
      RETURN FOUND; END $$""")
    _finalize()
    for sig in SIGNATURES:
        op.execute(f'REVOKE ALL ON FUNCTION f1.{sig} FROM PUBLIC')
        who='f1_api' if sig.startswith('register_') else 'f1_worker'
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{sig} TO {who}')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.finish_native_extraction_failure(uuid,uuid,text,integer) TO {ROLE}')


def _finalize() -> None:
    op.execute("""CREATE FUNCTION f1.finalize_native_extraction(p_job uuid,p_token uuid,p jsonb) RETURNS uuid
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
        IF p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>24000000
          OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(p) key) IS DISTINCT FROM ARRAY[
            'coverage_state','debts','expected_block_count','extraction_contract','fragments','manifest_sha256','parser_version',
            'processed_block_count','report_source_eligible','revision_id','source_format','source_sha256','support_profile']::text[]
          OR p->>'revision_id' IS DISTINCT FROM j.revision_id::text OR p->>'source_sha256' IS DISTINCT FROM j.source_sha256
          OR p->>'source_format' IS DISTINCT FROM 'docx' OR p->>'parser_version' IS DISTINCT FROM j.parser_version
          OR p->>'support_profile' IS DISTINCT FROM j.support_profile OR p->'extraction_contract' IS DISTINCT FROM '1'::jsonb OR p->>'extraction_contract' IS DISTINCT FROM '1'
          OR jsonb_typeof(p->'manifest_sha256')<>'string' OR p->>'manifest_sha256' !~ '^[0-9a-f]{64}$'
          OR jsonb_typeof(p->'expected_block_count')<>'number' OR p->>'expected_block_count' !~ '^(0|[1-9][0-9]{0,5})$'
          OR jsonb_typeof(p->'processed_block_count')<>'number' OR p->>'processed_block_count' !~ '^(0|[1-9][0-9]{0,5})$'
          OR jsonb_typeof(p->'report_source_eligible')<>'boolean' OR jsonb_typeof(p->'debts')<>'array'
          OR jsonb_typeof(p->'fragments')<>'array' THEN RAISE EXCEPTION 'NATIVE_PAYLOAD_INVALID'; END IF;
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
          IF jsonb_typeof(l)<>'object' OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(l) key) IS DISTINCT FROM ARRAY[
            'body_index','cell_index','grid_column','grid_span','kind','paragraph_index','part','row_index','schema_version']::text[]
            OR l->>'kind' IS DISTINCT FROM 'docx_block' OR l->>'part' IS DISTINCT FROM 'word/document.xml'
            OR l->'schema_version' IS DISTINCT FROM '2'::jsonb OR l->>'schema_version' IS DISTINCT FROM '2' OR jsonb_typeof(l->'body_index')<>'number'
            OR l->>'body_index' !~ '^[1-9][0-9]{0,6}$' OR (l->>'body_index')::integer>1000000 THEN RAISE EXCEPTION 'NATIVE_LOCATOR_INVALID'; END IF;
          IF l->'row_index'='null'::jsonb THEN
            IF l->'cell_index'<>'null'::jsonb OR l->'grid_column'<>'null'::jsonb OR l->'grid_span'<>'null'::jsonb OR l->'paragraph_index'<>'null'::jsonb THEN RAISE EXCEPTION 'NATIVE_LOCATOR_INVALID'; END IF;
          ELSE
            FOREACH k IN ARRAY ARRAY['row_index','cell_index','grid_column','grid_span','paragraph_index'] LOOP
              IF jsonb_typeof(l->k)<>'number' OR l->>k !~ '^[1-9][0-9]{0,6}$' OR (l->>k)::integer>1000000 THEN RAISE EXCEPTION 'NATIVE_LOCATOR_INVALID'; END IF;
            END LOOP;
            IF (l->>'grid_column')::integer<(l->>'cell_index')::integer OR (l->>'grid_column')::integer+(l->>'grid_span')::integer>1000001 THEN RAISE EXCEPTION 'NATIVE_LOCATOR_INVALID'; END IF;
          END IF;
          IF encode(sha256(convert_to(f1.native_canonical(l),'UTF8')),'hex')<>f->>'locator_sha256' THEN RAISE EXCEPTION 'NATIVE_LOCATOR_HASH_INVALID'; END IF;
          identity:=jsonb_build_object('schema_version',2,'enterprise_id',j.enterprise_id,'knowledge_scope_id',j.knowledge_scope_id,
            'document_record_id',j.document_record_id,'document_version_id',j.document_version_id,'extraction_revision_id',j.revision_id,
            'source_sha256',j.source_sha256,'source_format','docx','parser_version',j.parser_version,'extraction_contract',1,
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
          manifest_sha256,coverage_state,debts,expected_block_count,processed_block_count,fragment_count,nonblank_fragment_count,actor_sub)
        VALUES(j.revision_id,j.enterprise_id,j.id,j.knowledge_scope_id,j.document_record_id,j.document_version_id,j.source_document_id,j.upload_task_id,
          j.source_sha256,j.source_format,j.parser_version,j.extraction_contract,j.support_profile,p->>'manifest_sha256',p->>'coverage_state',p->'debts',expected,processed,n,nonblank,j.actor_sub);
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


def _grants() -> None:
    op.execute(f'GRANT USAGE ON SCHEMA f1 TO {ROLE}')
    allowed="session_user IN ('f1_api','f1_worker')"
    for table in TABLES:
        op.execute(f'ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON f1.{table} FROM PUBLIC,f1_api,f1_worker')
        op.execute(f'CREATE POLICY native_private ON f1.{table} FOR ALL TO {ROLE} USING({allowed}) WITH CHECK({allowed})')
        op.execute(f'GRANT SELECT,INSERT ON f1.{table} TO {ROLE}')
    op.execute(f'GRANT UPDATE ON f1.material_evidence_job TO {ROLE}')
    # Expose status metadata only to the current provider administrator. No
    # plaintext/ciphertext table read is granted to API or worker.
    for table in TABLES[:2]:
        op.execute(f"""CREATE POLICY native_status ON f1.{table} FOR SELECT TO f1_api USING(
          enterprise_id=f1.current_enterprise_id() AND f1.session_authorized(enterprise_id)
          AND EXISTS(SELECT 1 FROM f1.enterprise e WHERE e.id={table}.enterprise_id AND e.business_kind='service_provider')
          AND EXISTS(SELECT 1 FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id
            WHERE eu.enterprise_id={table}.enterprise_id AND up.keycloak_sub=f1.current_sub() AND eu.role='enterprise_admin' AND eu.revoked_at IS NULL))""")
    op.execute('GRANT SELECT(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,revision_id,state,attempt,reason_code,created_at,updated_at,completed_at) ON f1.material_evidence_job TO f1_api')
    op.execute('GRANT SELECT(id,enterprise_id,job_id,knowledge_scope_id,document_record_id,document_version_id,coverage_state,expected_block_count,processed_block_count,fragment_count,nonblank_fragment_count,report_source_eligible,created_at) ON f1.material_extraction_revision TO f1_api')
    for table in ('enterprise','enterprise_user','user_profile','document','document_record','document_version','upload_task','material_knowledge_scope'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f'CREATE POLICY native_source_read ON f1.{table} FOR SELECT TO {ROLE} USING({allowed})')
    for table in ('enterprise','enterprise_user','document','document_record','document_version','upload_task'):
        op.execute(f'GRANT UPDATE(id) ON f1.{table} TO {ROLE}')
        op.execute(f'CREATE POLICY native_source_lock ON f1.{table} FOR UPDATE TO {ROLE} USING({allowed}) WITH CHECK(false)')
    op.execute(f'GRANT INSERT ON f1.audit_log TO {ROLE}')
    op.execute(f"CREATE POLICY native_audit ON f1.audit_log FOR INSERT TO {ROLE} WITH CHECK({allowed} AND action='native.extraction.completed' AND resource_type='material_extraction_revision')")
    op.execute(f"CREATE POLICY native_audit_private ON f1.audit_log AS RESTRICTIVE FOR INSERT TO PUBLIC WITH CHECK(action NOT LIKE 'native.%' OR current_user='{ROLE}')")


def downgrade() -> None:
    raise RuntimeError('NATIVE_EVIDENCE_RESTORE_REQUIRED')
