"""Provider review can inspect the unchanged base source after confirmation/revoke."""
from alembic import op
revision = 'f1_0039'
down_revision = 'f1_0038'
branch_labels = depends_on = None


def upgrade():
    op.execute("""CREATE FUNCTION f1.read_review_original(p_version uuid,p_fragment uuid,p_revision uuid) RETURNS jsonb
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE eid uuid:=nullif(current_setting('f1.enterprise_id',true),'')::uuid;
        actor text:=nullif(current_setting('f1.sub',true),''); s jsonb; b jsonb; loc jsonb;
      BEGIN
        IF session_user<>'f1_api' OR eid IS NULL OR actor IS NULL OR p_version IS NULL
          OR p_fragment IS NULL OR p_revision IS NULL THEN RETURN NULL; END IF;
        s:=f1.review_source(p_version);
        IF s IS NULL OR (s->>'enterprise_id')::uuid IS DISTINCT FROM eid OR NOT f1.native_actor(eid,actor)
          THEN RETURN NULL; END IF;
        b:=f1.review_base(s);
        IF (b->>'base_revision_id')::uuid IS DISTINCT FROM p_revision THEN RETURN NULL; END IF;
        SELECT f->'locator' INTO loc FROM jsonb_array_elements(b->'base_fragments') f WHERE (f->>'id')::uuid=p_fragment;
        IF loc IS NULL THEN RETURN NULL; END IF;
        RETURN jsonb_build_object('document_version_id',p_version,'source_format',s->>'source_format',
          'source_sha256',s->>'source_sha256','source_size',(s->>'source_size')::bigint,'object_key',s->>'object_key','locator',loc);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_review_original(uuid,uuid,uuid) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_review_original(uuid,uuid,uuid) TO f1_api')


def downgrade():
    raise RuntimeError('REVIEW_ORIGINAL_RESTORE_REQUIRED')
