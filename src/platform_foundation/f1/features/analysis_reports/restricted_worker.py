"""Generation over an exclusive login with only delivery/lease capabilities."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from cryptography.exceptions import InvalidTag
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ...database import session_scope
from ..evidence.effective import decode_source
from .contracts import EligibleSource, EvidenceUnit, GenerationFailed


async def read_delivery(delivery_id: uuid.UUID, dispatch_token: uuid.UUID):
    async with session_scope(role='f1_report_worker') as session:
        row = (await session.execute(text('SELECT * FROM f1.read_report_worker_delivery(:id,:token)'),
            {'id': delivery_id, 'token': dispatch_token})).mappings().one_or_none()
        return dict(row) if row else None


async def claim_generation(delivery_id: uuid.UUID, dispatch_token: uuid.UUID):
    async with session_scope(role='f1_report_worker') as session:
        claim = (await session.execute(text('SELECT f1.claim_report_worker_generation(:id,:token)'),
            {'id': delivery_id, 'token': dispatch_token})).scalar_one()
        await session.commit()
        return claim


async def load_sources(job_id: uuid.UUID, lease_token: uuid.UUID):
    async with session_scope(role='f1_report_worker') as session:
        rows = (await session.execute(text('SELECT * FROM f1.read_report_worker_sources(:id,:token)'),
            {'id': job_id, 'token': lease_token})).scalars().all()
        # Ciphertext is returned under the source/job/member locks; decoding
        # happens after commit so model latency never retains those locks.
        await session.commit()
    result = []
    for row in rows:
        source = decode_source(row)
        if source.evidence_kind == 'revoked':
            continue
        if not source.fragments:
            raise GenerationFailed('REPORT_SOURCE_INDEX_OUTDATED')
        units = tuple(EvidenceUnit(page_number=getattr(f.locator, 'page_number', None), ordinal=f.ordinal,
            body_sha256=f.body_sha256, text=f.body, locator=f.locator.to_dict(),
            evidence_revision_id=f.evidence_revision_id, fragment_id=f.id) for f in source.fragments)
        result.append(EligibleSource(document_version_id=source.document_version_id, document_name=source.document_name,
            version_number=source.version_number, source_sha256=source.source_sha256, scope_kind=source.scope_kind,
            page_number=units[0].page_number, evidence_units=units))
    return result


async def finish_generation(job_id, lease_token, outcome, result):
    try:
        async with session_scope(role='f1_report_worker') as session:
            done = (await session.execute(text('SELECT f1.finish_report_worker_generation(:id,:token,:outcome,CAST(:result AS jsonb))'),
                {'id': job_id, 'token': lease_token, 'outcome': outcome, 'result': json.dumps(result, default=str)})).scalar_one()
            await session.commit()
            return bool(done)
    except DBAPIError as exc:
        reason = getattr(getattr(exc.orig, 'diag', None), 'message_primary', None)
        if reason in {'REPORT_SOURCE_FINGERPRINT_CHANGED','REPORT_SOURCE_INDEX_OUTDATED','REPORT_LEASE_STALE'}:
            raise GenerationFailed(reason) from None
        raise


async def finish_delivery(delivery_id, dispatch_token):
    row = await read_delivery(delivery_id, dispatch_token)
    if row is None:
        return
    status = row['job_status']
    outcome = 'done' if status == 'draft' else ('blocked' if status == 'failed' else 'retry')
    reason = None if outcome == 'done' else (row['error_reason'] if outcome == 'blocked' else 'REPORT_GENERATION_INCOMPLETE')
    async with session_scope(role='f1_report_worker') as session:
        await session.execute(text('SELECT f1.finish_report_worker_delivery(:id,:token,:outcome,:reason,:seconds)'),
            {'id': delivery_id, 'token': dispatch_token, 'outcome': outcome, 'reason': reason,
             'seconds': 5 if outcome == 'retry' else None})
        await session.commit()


async def process_delivery(delivery_id: uuid.UUID, dispatch_token: uuid.UUID):
    from . import worker
    claim = await claim_generation(delivery_id, dispatch_token)
    if claim is None:
        # Duplicate, revoked actor, terminal or temporarily owned by a live
        # generation lease. The DB delivery state drives eventual retry.
        await finish_delivery(delivery_id, dispatch_token)
        return
    job_id, token = uuid.UUID(claim['id']), uuid.UUID(claim['lease_token'])
    try:
        if not worker._generation_enabled():
            raise GenerationFailed('REPORT_WORKER_GENERATION_DISABLED')
        sources = await load_sources(job_id, token)
        frozen = worker._freeze_claimed(uuid.UUID(claim['enterprise_id']), uuid.UUID(claim['client_account_id']), sources)
        if frozen.fingerprint_sha256 != claim['source_fingerprint_sha256']:
            raise GenerationFailed('REPORT_SOURCE_FINGERPRINT_CHANGED')
        generator = worker.LlmReportGenerator() if worker.llm_generation_enabled() else worker.EvidenceDrivenReportGenerator()
        generated = generator.generate(frozen)
        if not await finish_generation(job_id, token, 'draft', asdict(generated)):
            raise GenerationFailed('REPORT_LEASE_STALE')
    except (GenerationFailed, ValueError, InvalidTag) as exc:
        reason = exc.reason if isinstance(exc, GenerationFailed) else 'REPORT_SOURCE_EVIDENCE_INVALID'
        if not await finish_generation(job_id, token, 'failed', {'reason': reason}):
            raise RuntimeError('REPORT_LEASE_STALE') from None
    except Exception:
        if worker._rq_retry_available():
            await finish_generation(job_id, token, 'retry', {})
        else:
            await finish_generation(job_id, token, 'failed', {'reason': 'REPORT_GENERATION_RETRIES_EXHAUSTED'})
            worker.mark_current_dispatch_failure('REPORT_GENERATION_RETRIES_EXHAUSTED')
        await finish_delivery(delivery_id, dispatch_token)
        raise
    await finish_delivery(delivery_id, dispatch_token)
