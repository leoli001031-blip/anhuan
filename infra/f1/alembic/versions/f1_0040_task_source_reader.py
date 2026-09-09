"""Lease-only source gateway; its login cannot read tables or mutate jobs."""
from alembic import op
revision = 'f1_0040'
down_revision = 'f1_0039'
branch_labels = depends_on = None
ROLE = 'f1_source_read_definer'


def upgrade():
    op.execute(f'GRANT USAGE ON SCHEMA f1 TO {ROLE},f1_source_reader')
    for table in ('enterprise','enterprise_user','user_profile','document','document_record','document_version','upload_task','material_evidence_job','material_rag_job'):
        op.execute(f'GRANT SELECT ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY leased_source_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='f1_source_reader')")
        if table != 'user_profile':
            op.execute(f'GRANT UPDATE(id) ON f1.{table} TO {ROLE}')
            op.execute(f"CREATE POLICY leased_source_lock ON f1.{table} FOR UPDATE TO {ROLE} USING(session_user='f1_source_reader') WITH CHECK(false)")
    for signature in ('review_source(uuid)','native_actor(uuid,text)'):
        op.execute(f'GRANT EXECUTE ON FUNCTION f1.{signature} TO {ROLE}')
    op.execute("""CREATE FUNCTION f1.read_leased_task_source(p_kind text,p_job uuid,p_token uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE n f1.material_evidence_job; j f1.material_rag_job; s jsonb; deadline timestamptz;
      BEGIN
        IF session_user<>'f1_source_reader' OR p_job IS NULL OR p_token IS NULL THEN RETURN NULL; END IF;
        IF p_kind='native' THEN
          SELECT * INTO n FROM f1.material_evidence_job WHERE id=p_job;
          IF n.id IS NULL OR n.state<>'running' OR n.lease_token IS DISTINCT FROM p_token
            OR n.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
          s:=f1.review_source(n.document_version_id);
          SELECT * INTO n FROM f1.material_evidence_job WHERE id=p_job FOR SHARE;
          IF s IS NULL OR NOT(to_jsonb(n) @> s) OR n.state<>'running'
            OR n.lease_token IS DISTINCT FROM p_token OR NOT f1.native_actor(n.enterprise_id,n.actor_sub)
            THEN RETURN NULL; END IF;
          deadline:=n.lease_until;
          s:=s||jsonb_build_object('storage_area','released');
        ELSIF p_kind='pdf-index' THEN
          SELECT * INTO j FROM f1.material_rag_job WHERE id=p_job;
          IF j.id IS NULL OR j.status<>'running' OR j.action NOT IN ('index','rebuild')
            OR j.lease_token IS DISTINCT FROM p_token OR j.lease_until<=clock_timestamp() THEN RETURN NULL; END IF;
          s:=f1.review_source(j.document_version_id);
          SELECT * INTO j FROM f1.material_rag_job WHERE id=p_job FOR SHARE;
          IF s IS NULL OR s->>'source_format'<>'pdf' OR j.status<>'running' OR j.action NOT IN ('index','rebuild')
            OR j.lease_token IS DISTINCT FROM p_token OR NOT(to_jsonb(j) @> (s-ARRAY['source_document_id','source_size','source_etag','object_key','source_format']))
            THEN RETURN NULL; END IF;
          deadline:=j.lease_until;
          s:=s||jsonb_build_object('storage_area','quarantine');
        ELSE RETURN NULL;
        END IF;
        IF deadline IS NULL OR deadline<=clock_timestamp() THEN RETURN NULL; END IF;
        RETURN s||jsonb_build_object('lease_until',deadline);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_leased_task_source(text,uuid,uuid) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_leased_task_source(text,uuid,uuid) TO f1_source_reader')


def downgrade():
    raise RuntimeError('TASK_SOURCE_RESTORE_REQUIRED')
