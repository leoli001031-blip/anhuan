"""Scoped effective evidence: confirmed reviews supersede extraction; revoked heads exclude it."""
from alembic import op
revision = 'f1_0037'
down_revision = 'f1_0036'
branch_labels = depends_on = None
ROLE = 'f1_material_evidence_definer'


def upgrade():
    _base()
    _reader()
    _state()
    _citations()
    _original_reader()


def _base():
    op.execute("""CREATE OR REPLACE FUNCTION f1.review_base(s jsonb) RETURNS jsonb
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
          AND source_format=s->>'source_format' ORDER BY created_at DESC,id DESC LIMIT 1;
        IF NOT EXISTS(SELECT 1 FROM f1.material_extraction_revision WHERE id=rid AND report_source_eligible) THEN RETURN NULL; END IF;
        IF NOT EXISTS(SELECT 1 FROM f1.material_evidence_job WHERE id=(SELECT id FROM f1.material_evidence_job WHERE enterprise_id=eid AND document_version_id=vid ORDER BY created_at DESC,id DESC LIMIT 1) AND state='done' AND revision_id=rid) THEN RETURN NULL; END IF;
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
    # Reuse the audience resolver, which requires a current member and exactly
    # one active binding. The native definer still has no login or role members.
    # On a forward upgrade the audience helper already belongs to its isolated
    # definer. Bootstrap grants this one seam without runtime role membership.
    op.execute('RESET ROLE')
    op.execute("DO $$ BEGIN IF session_user<>'f0d_bootstrap' THEN RAISE EXCEPTION 'EFFECTIVE_BOOTSTRAP_REQUIRED'; END IF; END $$")
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.aeco_client_material_context() TO {ROLE}')
    op.execute('SET LOCAL ROLE f0d_migration')
    op.execute("""CREATE FUNCTION f1.read_effective_material_sources(p_scopes uuid[]) RETURNS SETOF jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor text:=nullif(current_setting('f1.sub',true),''); owner_id uuid; audience record;
        q record; s jsonb; b jsonb; h f1.material_review_revision; items jsonb;
        n integer:=0; total_fragments integer:=0; total_bytes bigint:=0; payload jsonb;
      BEGIN
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL THEN RETURN; END IF;
        IF p_scopes IS NULL OR cardinality(p_scopes) NOT BETWEEN 1 AND 2
          OR cardinality(p_scopes)<>(SELECT count(DISTINCT x) FROM unnest(p_scopes) x)
          THEN RAISE EXCEPTION 'EFFECTIVE_SCOPE_INVALID'; END IF;
        IF f1.native_actor(eid,actor) THEN
          owner_id:=eid;
          IF (SELECT count(*) FROM f1.material_knowledge_scope WHERE enterprise_id=eid AND id=ANY(p_scopes)
            AND ((scope_kind='service_provider' AND client_account_id IS NULL) OR scope_kind='client'))<>cardinality(p_scopes)
            OR (SELECT count(*) FROM f1.material_knowledge_scope WHERE id=ANY(p_scopes) AND scope_kind='service_provider')<>1
            THEN RETURN; END IF;
        ELSE
          SELECT * INTO audience FROM f1.aeco_client_material_context();
          IF audience.provider_enterprise_id IS NULL OR cardinality(p_scopes)<>2
            OR NOT (p_scopes @> ARRAY[audience.provider_scope_id,audience.client_scope_id]) THEN RETURN; END IF;
          owner_id:=audience.provider_enterprise_id;
        END IF;
        FOR q IN SELECT v.id,r.title,v.version_no,sc.scope_kind FROM f1.document_version v
          JOIN f1.document_record r ON r.id=v.document_record_id AND r.enterprise_id=v.enterprise_id
          JOIN f1.material_knowledge_scope sc ON sc.id=r.knowledge_scope_id AND sc.enterprise_id=r.enterprise_id
          WHERE v.enterprise_id=owner_id AND sc.id=ANY(p_scopes) AND r.status='active' AND v.version_no=r.latest_version_no
          ORDER BY v.id LIMIT 1001
        LOOP
          n:=n+1; IF n>1000 THEN RAISE EXCEPTION 'EFFECTIVE_CORPUS_LIMIT'; END IF;
          s:=f1.review_source(q.id);
          IF s IS NULL THEN CONTINUE; END IF;
          IF (s->>'enterprise_id')::uuid IS DISTINCT FROM owner_id OR NOT ((s->>'knowledge_scope_id')::uuid=ANY(p_scopes)) THEN RAISE EXCEPTION 'EFFECTIVE_SCOPE_INVALID'; END IF;
          SELECT * INTO h FROM f1.material_review_revision WHERE enterprise_id=owner_id AND document_version_id=q.id ORDER BY revision_no DESC LIMIT 1;
          items:='[]'::jsonb; b:=NULL;
          IF h.id IS NOT NULL THEN
            IF h.source_sha256 IS DISTINCT FROM s->>'source_sha256' OR h.source_format IS DISTINCT FROM s->>'source_format'
              OR h.knowledge_scope_id IS DISTINCT FROM (s->>'knowledge_scope_id')::uuid THEN RAISE EXCEPTION 'EFFECTIVE_IDENTITY_INVALID'; END IF;
            IF h.action='confirm' THEN
              SELECT COALESCE(jsonb_agg(to_jsonb(fq) ORDER BY ordinal),'[]'::jsonb) INTO items FROM (
                SELECT f.id,f.enterprise_id,f.review_revision_id,f.base_fragment_id,f.entry_kind,f.field_name,f.ordinal,f.locator,f.locator_sha256,
                  f.body_sha256,f.character_count,f.has_nonblank_text,f.body_aad_sha256,encode(f.body_ciphertext,'hex') AS body_ciphertext_hex,
                  h.knowledge_scope_id AS knowledge_scope_id,h.document_record_id AS document_record_id,h.document_version_id AS document_version_id,
                  h.source_sha256 AS source_sha256,h.base_revision_id AS base_revision_id,h.base_manifest_sha256 AS base_manifest_sha256
                FROM f1.material_review_fragment f WHERE f.enterprise_id=owner_id AND f.review_revision_id=h.id
              ) fq;
              IF jsonb_array_length(items)<>h.fragment_count THEN RAISE EXCEPTION 'EFFECTIVE_IDENTITY_INVALID'; END IF;
            END IF;
          ELSE
            b:=f1.review_base(s); items:=COALESCE(b->'base_fragments','[]'::jsonb);
          END IF;
          payload:=(s-ARRAY['object_key','source_etag','source_size','source_document_id','upload_task_id'])||
            jsonb_build_object('document_name',q.title,'version_number',q.version_no,'scope_kind',q.scope_kind,
              'evidence_kind',CASE WHEN h.action='confirm' THEN 'review' WHEN h.action='revoke' THEN 'revoked' ELSE 'extraction' END,
              'evidence_revision_id',COALESCE(h.id,(b->>'base_revision_id')::uuid),
              'manifest_sha256',COALESCE(h.base_manifest_sha256,b->>'base_manifest_sha256'),
              'available',jsonb_array_length(items)>0,'fragments',items);
          total_fragments:=total_fragments+jsonb_array_length(items); total_bytes:=total_bytes+octet_length(payload::text);
          IF total_fragments>20000 OR total_bytes>24000000 THEN RAISE EXCEPTION 'EFFECTIVE_CORPUS_LIMIT'; END IF;
          RETURN NEXT payload;
        END LOOP;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_effective_material_sources(uuid[]) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_effective_material_sources(uuid[]) TO f1_api')


def downgrade():
    raise RuntimeError('EFFECTIVE_MATERIAL_RESTORE_REQUIRED')


def _state():
    op.execute("""CREATE FUNCTION f1.read_effective_material_state(p_version uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor text:=nullif(current_setting('f1.sub',true),''); s jsonb; b jsonb;
        h f1.material_review_revision; j f1.material_evidence_job; r f1.material_extraction_revision;
      BEGIN
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL OR NOT f1.native_actor(eid,actor)
          OR NOT EXISTS(SELECT 1 FROM f1.document_version WHERE id=p_version AND enterprise_id=eid) THEN RETURN NULL; END IF;
        s:=f1.review_source(p_version);
        IF s IS NULL THEN RETURN jsonb_build_object('state','unavailable','reason_code','EFFECTIVE_SOURCE_UNAVAILABLE'); END IF;
        IF (s->>'enterprise_id')::uuid IS DISTINCT FROM eid THEN RETURN NULL; END IF;
        SELECT * INTO h FROM f1.material_review_revision WHERE enterprise_id=eid AND document_version_id=p_version ORDER BY revision_no DESC LIMIT 1;
        IF h.id IS NOT NULL THEN
          IF h.source_sha256 IS DISTINCT FROM s->>'source_sha256' OR h.source_format IS DISTINCT FROM s->>'source_format'
            OR h.knowledge_scope_id IS DISTINCT FROM (s->>'knowledge_scope_id')::uuid THEN RAISE EXCEPTION 'EFFECTIVE_IDENTITY_INVALID'; END IF;
          RETURN jsonb_build_object('state',CASE WHEN h.action='confirm' THEN 'ready' ELSE 'revoked' END,
            'evidence_kind','review','revision_id',h.id,'fragment_count',CASE WHEN h.action='confirm' THEN h.fragment_count ELSE 0 END,
            'reason_code',CASE WHEN h.action='confirm' THEN 'EFFECTIVE_REVIEW_READY' ELSE 'EFFECTIVE_REVIEW_REVOKED' END);
        END IF;
        b:=f1.review_base(s);
        IF b IS NOT NULL THEN RETURN jsonb_build_object('state','ready','evidence_kind','extraction',
          'revision_id',b->'base_revision_id','fragment_count',jsonb_array_length(b->'base_fragments'),'reason_code','EFFECTIVE_EXTRACTION_READY'); END IF;
        SELECT * INTO j FROM f1.material_evidence_job WHERE enterprise_id=eid AND document_version_id=p_version ORDER BY created_at DESC,id DESC LIMIT 1;
        SELECT * INTO r FROM f1.material_extraction_revision WHERE enterprise_id=eid AND id=j.revision_id;
        RETURN jsonb_build_object('state',CASE WHEN j.id IS NULL THEN 'pending' WHEN j.state<>'done' THEN j.state
            WHEN r.coverage_state='partial' THEN 'partial' ELSE 'empty' END,
          'evidence_kind','extraction','revision_id',r.id,'fragment_count',0,
          'reason_code',CASE WHEN j.state='blocked' THEN coalesce(j.reason_code,'NATIVE_EXTRACTION_FAILED')
            WHEN r.coverage_state='partial' THEN 'EFFECTIVE_EXTRACTION_PARTIAL' WHEN j.state='done' THEN 'EFFECTIVE_EXTRACTION_EMPTY' ELSE 'EFFECTIVE_EXTRACTION_PENDING' END);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_effective_material_state(uuid) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_effective_material_state(uuid) TO f1_api')


def _citations():
    op.execute("""ALTER TABLE f1.analysis_report_citation
      ALTER COLUMN page_number DROP NOT NULL,
      ADD COLUMN locator jsonb,
      ADD COLUMN evidence_revision_id uuid,
      ADD COLUMN fragment_id uuid,
      ADD COLUMN evidence_body_sha256 text,
      ADD CONSTRAINT citation_identity_shape CHECK(
        (locator IS NULL AND evidence_revision_id IS NULL AND fragment_id IS NULL AND evidence_body_sha256 IS NULL AND page_number IS NOT NULL)
        OR (locator IS NOT NULL AND evidence_revision_id IS NOT NULL AND fragment_id IS NOT NULL
          AND evidence_body_sha256 IS NOT NULL AND evidence_body_sha256 ~ '^[0-9a-f]{64}$'))""")
    op.execute('GRANT INSERT(locator,evidence_revision_id,fragment_id,evidence_body_sha256) ON f1.analysis_report_citation TO f1_api')
    op.execute("""CREATE FUNCTION f1.guard_effective_citation() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE fmt text; valid boolean:=false;
      BEGIN
        IF NEW.locator IS NULL THEN RETURN NEW; END IF;
        fmt:=CASE NEW.locator->>'kind' WHEN 'pdf_page' THEN 'pdf' WHEN 'docx_block' THEN 'docx' WHEN 'xlsx_cells' THEN 'xlsx' WHEN 'image' THEN 'jpeg' END;
        IF fmt IS NULL OR (fmt<>'pdf' AND NOT f1.native_locator_valid(fmt,NEW.locator))
          OR (fmt='pdf' AND NEW.locator IS DISTINCT FROM jsonb_build_object('schema_version',2,'kind','pdf_page','page_number',NEW.page_number))
          OR (fmt='pdf' AND NEW.page_number IS DISTINCT FROM (NEW.locator->>'page_number')::integer)
          OR (fmt<>'pdf' AND NEW.page_number IS NOT NULL) THEN RAISE EXCEPTION 'EFFECTIVE_CITATION_INVALID'; END IF;
        SELECT EXISTS(SELECT 1 FROM f1.material_review_fragment f JOIN f1.material_review_revision r ON r.id=f.review_revision_id AND r.enterprise_id=f.enterprise_id
          WHERE f.enterprise_id=NEW.enterprise_id AND f.id=NEW.fragment_id AND r.id=NEW.evidence_revision_id AND r.action='confirm'
            AND r.document_version_id=NEW.document_version_id AND f.locator=NEW.locator AND f.body_sha256=NEW.evidence_body_sha256) INTO valid;
        IF NOT valid AND fmt<>'pdf' THEN
          SELECT EXISTS(SELECT 1 FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id AND r.enterprise_id=f.enterprise_id
            WHERE f.enterprise_id=NEW.enterprise_id AND f.id=NEW.fragment_id AND r.id=NEW.evidence_revision_id AND r.report_source_eligible
              AND r.document_version_id=NEW.document_version_id AND f.locator=NEW.locator AND f.body_sha256=NEW.evidence_body_sha256) INTO valid;
        ELSIF NOT valid THEN
          SELECT EXISTS(SELECT 1 FROM f1.material_rag_unit f JOIN f1.material_rag_job j ON j.document_version_id=f.document_version_id AND j.enterprise_id=f.enterprise_id
            WHERE f.enterprise_id=NEW.enterprise_id AND f.id=NEW.fragment_id AND j.id=NEW.evidence_revision_id AND j.status='done'
              AND f.document_version_id=NEW.document_version_id AND f.page_number=NEW.page_number AND f.body_sha256=NEW.evidence_body_sha256
              AND f.source_sha256=j.source_sha256) INTO valid;
        END IF;
        IF NOT valid THEN RAISE EXCEPTION 'EFFECTIVE_CITATION_INVALID'; END IF;
        RETURN NEW;
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.guard_effective_citation() FROM PUBLIC')
    op.execute('CREATE TRIGGER effective_citation_identity BEFORE INSERT ON f1.analysis_report_citation FOR EACH ROW EXECUTE FUNCTION f1.guard_effective_citation()')


def _original_reader():
    for table in ('analysis_report','analysis_report_version','analysis_report_citation'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY citation_original_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='f1_api')")
    op.execute("""CREATE FUNCTION f1.read_citation_original(p_citation uuid,p_version uuid,p_fragment uuid,p_revision uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor text:=nullif(current_setting('f1.sub',true),''); provider boolean; audience record; c record;
        v f1.document_version; t f1.upload_task; d f1.document; r f1.document_record;
        h f1.material_review_revision; sc f1.material_knowledge_scope; s jsonb; b jsonb; loc jsonb;
        owner_id uuid; source_hash text; fmt text;
      BEGIN
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL THEN RETURN NULL; END IF;
        provider:=f1.native_actor(eid,actor);
        SELECT * INTO audience FROM f1.aeco_client_material_context();
        IF NOT provider THEN
          IF audience.provider_enterprise_id IS NULL THEN RETURN NULL; END IF;
          owner_id:=audience.provider_enterprise_id;
        ELSE owner_id:=eid; END IF;
        IF p_citation IS NOT NULL THEN
          IF p_version IS NOT NULL OR p_fragment IS NOT NULL OR p_revision IS NOT NULL THEN RETURN NULL; END IF;
          SELECT cit.*,ver.status,ver.artifact_ready,rep.archived_at,rep.client_visible,rep.client_account_id INTO c
            FROM f1.analysis_report_citation cit JOIN f1.analysis_report_version ver ON ver.id=cit.version_id AND ver.enterprise_id=cit.enterprise_id
            JOIN f1.analysis_report rep ON rep.id=ver.report_id AND rep.enterprise_id=ver.enterprise_id
            WHERE cit.id=p_citation AND cit.enterprise_id=owner_id;
          IF c.id IS NULL THEN RETURN NULL; END IF;
          IF NOT provider AND (c.client_account_id IS DISTINCT FROM audience.client_account_id OR c.status<>'published'
            OR NOT c.artifact_ready OR NOT c.client_visible OR c.archived_at IS NOT NULL) THEN RETURN NULL; END IF;
          p_version:=c.document_version_id;
          loc:=coalesce(c.locator,jsonb_build_object('schema_version',2,'kind','pdf_page','page_number',c.page_number));
          IF c.locator IS NOT NULL THEN
            SELECT rv.source_sha256 INTO source_hash FROM f1.material_review_revision rv JOIN f1.material_review_fragment rf
              ON rf.review_revision_id=rv.id AND rf.enterprise_id=rv.enterprise_id WHERE rv.enterprise_id=owner_id
              AND rv.id=c.evidence_revision_id AND rf.id=c.fragment_id AND rv.document_version_id=p_version
              AND rf.locator=loc AND rf.body_sha256=c.evidence_body_sha256;
            IF source_hash IS NULL THEN
              SELECT er.source_sha256 INTO source_hash FROM f1.material_extraction_revision er JOIN f1.material_evidence_fragment ef
                ON ef.extraction_revision_id=er.id AND ef.enterprise_id=er.enterprise_id WHERE er.enterprise_id=owner_id
                AND er.id=c.evidence_revision_id AND ef.id=c.fragment_id AND er.document_version_id=p_version
                AND ef.locator=loc AND ef.body_sha256=c.evidence_body_sha256;
            END IF;
            IF source_hash IS NULL AND loc->>'kind'='pdf_page' THEN
              SELECT j.source_sha256 INTO source_hash FROM f1.material_rag_job j WHERE j.enterprise_id=owner_id
                AND j.id=c.evidence_revision_id AND j.document_version_id=p_version AND j.status='done';
            END IF;
            IF source_hash IS NULL THEN RETURN NULL; END IF;
          END IF;
        ELSE
          IF p_version IS NULL OR p_fragment IS NULL OR p_revision IS NULL THEN RETURN NULL; END IF;
          s:=f1.review_source(p_version);
          IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM owner_id THEN RETURN NULL; END IF;
          IF NOT provider AND NOT ((s->>'knowledge_scope_id')::uuid=ANY(ARRAY[audience.provider_scope_id,audience.client_scope_id])) THEN RETURN NULL; END IF;
          SELECT * INTO h FROM f1.material_review_revision WHERE enterprise_id=owner_id AND document_version_id=p_version ORDER BY revision_no DESC LIMIT 1;
          IF h.id IS NOT NULL THEN
            IF h.id<>p_revision OR h.action<>'confirm' OR h.source_sha256 IS DISTINCT FROM s->>'source_sha256' THEN RETURN NULL; END IF;
            SELECT locator INTO loc FROM f1.material_review_fragment WHERE enterprise_id=owner_id AND review_revision_id=h.id AND id=p_fragment;
          ELSE
            b:=f1.review_base(s);
            IF (b->>'base_revision_id')::uuid IS DISTINCT FROM p_revision THEN RETURN NULL; END IF;
            SELECT f->'locator' INTO loc FROM jsonb_array_elements(b->'base_fragments') f WHERE (f->>'id')::uuid=p_fragment;
          END IF;
          source_hash:=s->>'source_sha256';
        END IF;
        IF loc IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO v FROM f1.document_version WHERE id=p_version AND enterprise_id=owner_id;
        SELECT * INTO t FROM f1.upload_task WHERE id=v.upload_task_id AND enterprise_id=owner_id FOR SHARE;
        SELECT * INTO d FROM f1.document WHERE id=v.source_document_id AND enterprise_id=owner_id FOR SHARE;
        SELECT * INTO r FROM f1.document_record WHERE id=v.document_record_id AND enterprise_id=owner_id FOR SHARE;
        SELECT * INTO sc FROM f1.material_knowledge_scope WHERE id=r.knowledge_scope_id AND enterprise_id=owner_id;
        IF v.id IS NULL OR t.id IS NULL OR d.id IS NULL OR r.id IS NULL OR sc.id IS NULL
          OR d.knowledge_scope_id IS DISTINCT FROM sc.id OR t.document_id IS DISTINCT FROM d.id
          OR d.status<>'done' OR t.pipeline_kind<>'controlled_ingestion' OR t.status<>'done'
          OR t.processing_stage<>'ready' OR t.object_state<>'ready' OR t.scan_verdict<>'clean' OR t.preview_status<>'ready'
          OR t.quarantine_status<>'released' OR t.released_at IS NULL OR t.rejected_at IS NOT NULL
          OR t.object_key IS DISTINCT FROM d.object_key OR t.source_size IS NULL OR t.source_size IS DISTINCT FROM d.size
          OR t.content_sha256 IS NULL OR (source_hash IS NOT NULL AND source_hash IS DISTINCT FROM t.content_sha256) THEN RETURN NULL; END IF;
        IF p_citation IS NOT NULL THEN
          IF sc.scope_kind='client' AND sc.client_account_id IS DISTINCT FROM c.client_account_id THEN RETURN NULL; END IF;
        END IF;
        IF NOT provider AND NOT(sc.id=ANY(ARRAY[audience.provider_scope_id,audience.client_scope_id])) THEN RETURN NULL; END IF;
        fmt:=CASE d.content_type WHEN 'application/pdf' THEN 'pdf' WHEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' THEN 'docx'
          WHEN 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' THEN 'xlsx' WHEN 'image/jpeg' THEN 'jpeg' END;
        IF fmt IS NULL THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('document_version_id',v.id,'source_format',fmt,'source_sha256',t.content_sha256,
          'source_size',t.source_size,'object_key',t.object_key,'locator',loc);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_citation_original(uuid,uuid,uuid,uuid) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_citation_original(uuid,uuid,uuid,uuid) TO f1_api')
