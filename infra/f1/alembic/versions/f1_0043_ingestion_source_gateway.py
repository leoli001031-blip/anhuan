"""Read-only DB capability for ingestion source and preview object gateway."""
from alembic import op
from sqlalchemy import text
revision = 'f1_0043'
down_revision = 'f1_0042'
branch_labels = depends_on = None
ROLE = 'f1_source_read_definer'


def upgrade():
    for table in ('material_ingestion_delivery','material_knowledge_scope','crm_account'):
        op.execute(f'GRANT SELECT,UPDATE(id) ON f1.{table} TO {ROLE}')
        op.execute(f"CREATE POLICY ingestion_source_read ON f1.{table} FOR SELECT TO {ROLE} USING(session_user='f1_source_reader')")
        op.execute(f"CREATE POLICY ingestion_source_lock ON f1.{table} FOR UPDATE TO {ROLE} USING(session_user='f1_source_reader') WITH CHECK(false)")
    definition=op.get_bind().execute(text("SELECT pg_get_functiondef('f1.ingestion_worker_context(boolean)'::regprocedure)")).scalar_one()
    replacements=(
        ('ingestion_worker_context(p_lock boolean)', 'source_ingestion_context(cap uuid, token uuid)'),
        ('SECURITY DEFINER','SECURITY INVOKER'),
        ('member f1.enterprise_user; actor uuid; cap uuid; token uuid;', 'member f1.enterprise_user; actor uuid; p_lock boolean:=true;'),
        ("session_user<>'f1_ingestion_worker'", "session_user<>'f1_source_reader'"),
    )
    for old,new in replacements:
        if definition.count(old)!=1:raise RuntimeError('INGESTION_SOURCE_DEFINITION_MISMATCH')
        definition=definition.replace(old,new)
    start=definition.index('        BEGIN\n          cap:=nullif(')
    end=definition.index('        IF cap IS NULL',start)
    definition=definition[:start]+definition[end:]
    op.execute(definition)
    op.execute('REVOKE ALL ON FUNCTION f1.source_ingestion_context(uuid,uuid) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION f1.source_ingestion_context(uuid,uuid) TO {ROLE}')
    op.execute("""CREATE FUNCTION f1.read_ingestion_task_source(p_delivery uuid,p_token uuid,p_process uuid,p_preview boolean)
      RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DECLARE ctx jsonb; task f1.upload_task; deadline timestamptz;
      BEGIN
        IF session_user<>'f1_source_reader' OR p_delivery IS NULL OR p_token IS NULL
          OR p_preview IS NULL THEN RETURN NULL; END IF;
        ctx:=f1.source_ingestion_context(p_delivery,p_token);
        IF ctx IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO task FROM f1.upload_task WHERE id=(ctx->>'upload_task_id')::uuid;
        deadline:=(ctx->>'lease_until')::timestamptz;
        IF p_preview THEN
          IF p_process IS NULL OR task.processing_stage<>'previewing'
            OR task.object_state<>'quarantined' OR task.quarantine_status<>'held'
            OR task.scan_verdict<>'clean' OR task.preview_status<>'generating'
            OR task.lease_token IS DISTINCT FROM p_process OR task.lease_until IS NULL
            THEN RETURN NULL; END IF;
          deadline:=least(deadline,task.lease_until);
        ELSIF p_process IS NOT NULL THEN
          IF task.processing_stage<>'scanning' OR task.scan_verdict<>'scanning'
            OR task.object_state<>'quarantined' OR task.quarantine_status<>'held'
            OR task.lease_token IS DISTINCT FROM p_process OR task.lease_until IS NULL
            THEN RETURN NULL; END IF;
          deadline:=least(deadline,task.lease_until);
        ELSE
          IF ctx->>'content_type'<>'application/pdf' OR task.processing_stage<>'ready'
            OR task.object_state<>'ready' OR task.quarantine_status NOT IN ('held','released')
            OR task.scan_verdict<>'clean' OR task.preview_status<>'ready'
            THEN RETURN NULL; END IF;
        END IF;
        IF deadline<=clock_timestamp() THEN RETURN NULL; END IF;
        RETURN ctx||jsonb_build_object('storage_area','quarantine','lease_until',deadline);
      END $$""")
    op.execute('REVOKE ALL ON FUNCTION f1.read_ingestion_task_source(uuid,uuid,uuid,boolean) FROM PUBLIC')
    op.execute('GRANT EXECUTE ON FUNCTION f1.read_ingestion_task_source(uuid,uuid,uuid,boolean) TO f1_source_reader')


def downgrade():
    raise RuntimeError('INGESTION_SOURCE_RESTORE_REQUIRED')
