"""Current-manager recovery, with durable request receipts and safe replay."""
import uuid
from typing import Literal
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from ...auth import Tenant
from ...database import session_scope
from ..p3.contracts import IngestionError
from ..p3.service import get_version, require_manager
from .formats import CONTRACTS
from .repository import native_extraction_enabled


class NativeRecoveryIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID
    expected_job_id: uuid.UUID | None


class NativeRecoveryOut(BaseModel):
    request_id: uuid.UUID
    version_id: uuid.UUID
    job_id: uuid.UUID
    state: Literal['pending','running','retry_wait','done','blocked']
    outcome: Literal['registered','rearmed','unchanged']
    replayed: bool


async def recover(tenant: Tenant, version_id: uuid.UUID, request: NativeRecoveryIn) -> NativeRecoveryOut:
    version = await get_version(tenant, version_id)
    if not native_extraction_enabled():
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    source_format = {'application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'xlsx','image/jpeg':'jpeg'}.get(version.content_type)
    if source_format is None:
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    parser, profile = CONTRACTS[source_format]
    try:
        async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            receipt = (await session.execute(text('SELECT f1.recover_native_extraction(:version,:request,:expected,:parser,:profile)'),
                {'version':version_id,'request':request.request_id,'expected':request.expected_job_id,'parser':parser,'profile':profile})).scalar_one()
            from ..material_pipeline.coordinator import auto_pipeline_enabled
            if auto_pipeline_enabled() and not receipt['replayed']:
                from ..material_pipeline.repository import register_delivery_in_session
                await register_delivery_in_session(session, tenant, version_id, rearm_terminal=True)
            await session.commit()
    except DBAPIError as error:
        message = str(error)
        if 'NATIVE_SOURCE_UNAVAILABLE' in message:
            raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404) from None
        for reason in ('NATIVE_RECOVERY_REQUEST_CONFLICT','NATIVE_RECOVERY_JOB_CHANGED'):
            if reason in message:
                raise IngestionError(reason, http_status=409) from None
        if 'native_recovery_request_uq' in message:
            raise IngestionError('NATIVE_RECOVERY_REQUEST_CONFLICT', http_status=409) from None
        raise IngestionError('NATIVE_RECOVERY_UNAVAILABLE', http_status=503) from None
    return NativeRecoveryOut(**receipt)


async def missing_candidates(tenant: Tenant, *, scope_kind: str, client_account_id: uuid.UUID | None = None,
                             after: uuid.UUID | None = None, limit: int = 50) -> dict:
    require_manager(tenant)
    if not native_extraction_enabled() or tenant.role != 'enterprise_admin':
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    if scope_kind not in {'service_provider','client'} or (scope_kind=='client') != (client_account_id is not None) or not 1<=limit<=100:
        raise IngestionError('P3_REQUEST_INVALID', http_status=422)
    async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
        # Discovery uses ordinary tenant RLS. Recovery separately locks and
        # rechecks each source, actor and expected latest job before mutation.
        rows = (await session.execute(text("""SELECT v.id FROM f1.document_version v
          JOIN f1.document_record r ON r.id=v.document_record_id AND r.enterprise_id=v.enterprise_id
          JOIN f1.material_knowledge_scope sc ON sc.id=r.knowledge_scope_id AND sc.enterprise_id=r.enterprise_id
          JOIN f1.document d ON d.id=v.source_document_id AND d.enterprise_id=v.enterprise_id
          JOIN f1.upload_task t ON t.id=v.upload_task_id AND t.enterprise_id=v.enterprise_id
          WHERE v.enterprise_id=:enterprise AND sc.scope_kind=:kind AND sc.client_account_id IS NOT DISTINCT FROM CAST(:client AS uuid)
          AND r.status='active' AND v.version_no=r.latest_version_no
          AND d.content_type IN ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','image/jpeg')
          AND t.pipeline_kind='controlled_ingestion' AND t.processing_stage='ready' AND t.status='done' AND t.object_state='ready'
          AND t.scan_verdict='clean' AND t.preview_status='ready' AND t.quarantine_status='released' AND t.released_at IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM f1.material_evidence_job j WHERE j.enterprise_id=v.enterprise_id AND j.document_version_id=v.id)
          AND (CAST(:after AS uuid) IS NULL OR v.id>CAST(:after AS uuid)) ORDER BY v.id LIMIT :limit"""),
            {'enterprise':tenant.enterprise_id,'kind':scope_kind,'client':client_account_id,'after':after,'limit':limit+1})).scalars().all()
    return {'version_ids':[str(v) for v in rows[:limit]],'next_after':str(rows[limit-1]) if len(rows)>limit else None}
