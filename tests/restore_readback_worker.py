"""Fresh runtime-process proof; only aggregate results leave private fixtures."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import uuid

from minio import Minio
from sqlalchemy import text

from platform_foundation.f1.auth import Tenant
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.features.evidence.effective import load_effective_sources
from platform_foundation.f1.features.material_rag.contracts import RetrievalContext
from platform_foundation.f1.features.material_rag.service import _effective_local_records
from platform_foundation.f1.features.material_rag.local_extractive import rank_local_evidence
from platform_foundation.f1.features.analysis_reports.service import version_detail
from platform_foundation.f1.features.analysis_reports.artifact import render_html_artifact
from platform_foundation.f1.features.analysis_reports.pdf_artifact import render_pdf_artifact
from platform_foundation.f1 import qa_service,invitation
from platform_foundation.f1.secret_files import _read_secure,read_f1_secret_text
from platform_foundation.f1.maintenance.candidate_backup import canonical,sha
from platform_foundation.f1.maintenance.backup_objects import BUCKETS


async def main():
    request=json.loads(_read_secure(Path(sys.argv[1]),unavailable_code='RESTORE_READBACK_REQUEST_INVALID',
                                   minimum_size=1,maximum_size=1024*1024))
    tenant=Tenant(enterprise_id=uuid.UUID(request['enterprise_id']),sub=request['sub'],roles=(),
                  role='enterprise_admin',business_kind='service_provider')
    scopes=tuple(uuid.UUID(s) for s in request['scopes'])
    async with session_scope(role='f1_api',enterprise_id=tenant.enterprise_id,sub=tenant.sub) as session:
        sources=await load_effective_sources(session,scopes)
        current={str(s.document_version_id):asdict(s) for s in sources if s.fragments}
        assert sha(canonical(current))==request['formats_sha256']
        assert {s.source_format for s in sources if s.fragments}=={'docx','xlsx','jpeg','pdf'}
        objects=(await session.execute(text('SELECT t.object_key,t.content_sha256,t.source_size FROM f1.upload_task t '
            'JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=ANY(CAST(:versions AS uuid[]))'),
            {'versions':list(current)})).all()
        assert len(objects)==len(current)
    context=RetrievalContext(enterprise_id=tenant.enterprise_id,kind='client',
        client_account_id=uuid.UUID(request['client_id']),scope_ids=scopes)
    records=await _effective_local_records(tenant,context)
    hits=rank_local_evidence('COD 42 mg/L',records,limit=20)
    assert {hit.to_citation_dict()['locator']['kind'] for hit in hits}=={'docx_block','xlsx_cells','image','pdf_page'}
    frozen=await version_detail(tenant,uuid.UUID(request['report_version']))
    assert sha(canonical(frozen))==request['frozen_sha256']
    assert render_html_artifact(frozen).sha256==request['html_sha256']
    assert render_pdf_artifact(frozen).sha256==request['pdf_sha256']
    answer=await qa_service.ask_material_question(request['question'],uuid.UUID(request['qa_request']),tenant,context)
    assert sha(canonical(answer.to_dict()))==request['qa_sha256']
    invite=invitation.validate_invite(request['invite_token'])
    assert invite['jti']==request['invite_jti'] and invite['enterprise_id']==request['enterprise_id']
    client=Minio(os.environ['MINIO_ENDPOINT'],
        access_key=read_f1_secret_text('minio_service_user',file_env='F1_MINIO_SERVICE_USER_FILE'),
        secret_key=read_f1_secret_text('minio_service_password',file_env='F1_MINIO_SERVICE_PASSWORD_FILE'),secure=False)
    for key,digest,size in objects:
        for bucket in BUCKETS[:2]:
            response=client.get_object(bucket,key)
            try:raw=response.read(size+1)
            finally:response.close();response.release_conn()
            assert len(raw)==size and sha(raw)==digest
    print(json.dumps({'status':'FRESH_PROCESS_READBACK_PASSED','source_formats':4,'source_objects_read':len(objects)*2,
        'frozen_citation_formats':4,'qa_replay':True,'invite_signature':True,'report_html_pdf_identical':True,
        'new_credentials':True,'live_model_calls':0}),flush=True)


if __name__=='__main__':
    asyncio.run(main())
