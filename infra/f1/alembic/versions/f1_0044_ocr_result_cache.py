"""Immutable encrypted OCR results, accessible only through a live source lease."""
from alembic import op

revision = 'f1_0044'
down_revision = 'f1_0043'
branch_labels = depends_on = None
ROLE = 'f1_ocr_cache_definer'


def upgrade():
    op.execute(f'GRANT USAGE ON SCHEMA f1 TO {ROLE}')
    op.execute("""CREATE TABLE f1.material_ocr_result_cache (
      enterprise_id uuid NOT NULL REFERENCES f1.enterprise(id),
      document_version_id uuid NOT NULL REFERENCES f1.document_version(id),
      source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
      unit_no integer NOT NULL CHECK(unit_no BETWEEN 1 AND 128),
      input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
      envelope jsonb NOT NULL CHECK(octet_length(envelope::text)<=1000000),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(document_version_id,source_sha256,unit_no,input_sha256)
    )""")
    op.execute('ALTER TABLE f1.material_ocr_result_cache ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE f1.material_ocr_result_cache FORCE ROW LEVEL SECURITY')
    op.execute(f'GRANT SELECT,INSERT ON f1.material_ocr_result_cache TO {ROLE}')
    op.execute(f"CREATE POLICY cache_read ON f1.material_ocr_result_cache FOR SELECT TO {ROLE} USING(session_user='f1_source_reader')")
    op.execute(f"CREATE POLICY cache_insert ON f1.material_ocr_result_cache FOR INSERT TO {ROLE} WITH CHECK(session_user='f1_source_reader')")
    op.execute("""CREATE FUNCTION f1.leased_ocr_cache(p_kind text,p_job uuid,p_token uuid,
      p_version uuid,p_source text,p_unit integer,p_input text,p_envelope jsonb)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb; saved jsonb; count_profiles integer;
      BEGIN
        IF session_user<>'f1_source_reader' OR p_version IS NULL OR p_source IS NULL
          OR p_source !~ '^[0-9a-f]{64}$' OR p_input IS NULL OR p_input !~ '^[0-9a-f]{64}$'
          OR p_unit IS NULL OR p_unit NOT BETWEEN 1 AND 128 THEN RETURN NULL; END IF;
        IF p_kind='pdf-analysis' THEN
          ctx:=f1.read_ingestion_task_source(p_job,p_token,NULL,false);
        ELSIF p_kind IN ('native','pdf-index') THEN
          ctx:=f1.read_leased_task_source(p_kind,p_job,p_token);
        ELSE RETURN NULL; END IF;
        IF ctx IS NULL OR (ctx->>'document_version_id')::uuid IS DISTINCT FROM p_version
          OR ctx->>'source_sha256' IS DISTINCT FROM p_source
          OR (p_kind='native' AND (ctx->>'source_format'<>'jpeg' OR p_unit<>1))
          THEN RETURN NULL; END IF;
        -- Serialize competing first writers and the per-page profile budget.
        -- The source function already holds source/job/member locks.
        PERFORM pg_advisory_xact_lock(hashtextextended('ocr-cache:'||p_version::text,0));
        IF (ctx->>'lease_until')::timestamptz<=clock_timestamp() THEN RETURN NULL; END IF;
        SELECT envelope INTO saved FROM f1.material_ocr_result_cache
          WHERE document_version_id=p_version AND source_sha256=p_source
            AND unit_no=p_unit AND input_sha256=p_input;
        IF saved IS NULL AND p_envelope IS NOT NULL THEN
          IF jsonb_typeof(p_envelope)<>'object' OR octet_length(p_envelope::text)>1000000
            OR NOT(p_envelope ?& ARRAY['ciphertext_hex','aad_sha256','body_sha256'])
            OR p_envelope-ARRAY['ciphertext_hex','aad_sha256','body_sha256']<>'{}'::jsonb
            OR jsonb_typeof(p_envelope->'ciphertext_hex')<>'string'
            OR length(p_envelope->>'ciphertext_hex') NOT BETWEEN 64 AND 990000
            OR (p_envelope->>'ciphertext_hex') !~ '^([0-9a-f]{2})+$'
            OR jsonb_typeof(p_envelope->'aad_sha256')<>'string'
            OR jsonb_typeof(p_envelope->'body_sha256')<>'string'
            OR (p_envelope->>'aad_sha256') !~ '^[0-9a-f]{64}$'
            OR (p_envelope->>'body_sha256') !~ '^[0-9a-f]{64}$'
            THEN RETURN NULL; END IF;
          SELECT count(*) INTO count_profiles FROM f1.material_ocr_result_cache
            WHERE document_version_id=p_version AND source_sha256=p_source AND unit_no=p_unit;
          IF count_profiles>=8 THEN RETURN jsonb_build_object('status','capacity'); END IF;
          INSERT INTO f1.material_ocr_result_cache VALUES(
            (ctx->>'enterprise_id')::uuid,p_version,p_source,p_unit,p_input,p_envelope,clock_timestamp());
          saved:=p_envelope;
        END IF;
        IF (ctx->>'lease_until')::timestamptz<=clock_timestamp() THEN
          RAISE EXCEPTION 'OCR_CACHE_LEASE_EXPIRED';
        END IF;
        RETURN jsonb_build_object('status',CASE WHEN saved IS NULL THEN 'miss' ELSE 'hit' END,
          'enterprise_id',ctx->>'enterprise_id','document_version_id',p_version,
          'source_sha256',p_source,'envelope',saved);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.leased_ocr_cache(text,uuid,uuid,uuid,text,integer,text,jsonb) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.leased_ocr_cache(text,uuid,uuid,uuid,text,integer,text,jsonb) TO f1_source_reader')


def downgrade():
    raise RuntimeError('OCR_CACHE_RESTORE_REQUIRED')
