"""Body-free native extraction status for the existing material manager UI."""
from typing import Literal
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from ..p3.service import get_version
from ..p3.contracts import IngestionError
from .envelopes import decrypt_fragment
from .contracts import format_location, parse_locator
from .repository import native_extraction_enabled


class CoverageGap(BaseModel):
    reason_code: str
    part: str
    path: str


class EffectiveMaterialState(BaseModel):
    state: Literal['unavailable', 'pending', 'running', 'retry_wait', 'blocked', 'partial', 'empty', 'ready', 'revoked']
    evidence_kind: Literal['extraction', 'review'] | None = None
    revision_id: uuid.UUID | None = None
    fragment_count: int = 0
    reason_code: str | None = None


async def read_effective_state(session, version_id: uuid.UUID) -> dict | None:
    return (await session.execute(text('SELECT f1.read_effective_material_state(:version)'),
        {'version': version_id})).scalar_one()


class NativeExtractionStatus(BaseModel):
    schema_name: Literal["anhuan-native-extraction-v1"] = Field(
        default="anhuan-native-extraction-v1", alias="schema")
    version_id: uuid.UUID
    state: Literal["disabled", "not_registered", "pending", "running", "retry_wait", "done", "blocked"]
    job_id: uuid.UUID | None = None
    revision_id: uuid.UUID | None = None
    attempt: int = 0
    reason_code: str | None = None
    coverage_state: Literal["complete", "partial"] | None = None
    expected_block_count: int | None = None
    processed_block_count: int | None = None
    fragment_count: int | None = None
    report_source_eligible: bool = False
    source_format: Literal["docx", "xlsx", "jpeg"] | None = None
    parser_version: str | None = None
    support_profile: str | None = None
    debts: list[CoverageGap] = Field(default_factory=list)
    processing_identity: dict[str, str] = Field(default_factory=dict)
    effective: EffectiveMaterialState | None = None


async def get_status(tenant: Tenant, version_id: uuid.UUID) -> NativeExtractionStatus:
    # Keep the existing source authorization ahead of feature/schema checks.
    await get_version(tenant, version_id)
    if not native_extraction_enabled():
        return NativeExtractionStatus(version_id=version_id, state="disabled")
    async with session_scope(role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
        effective = await read_effective_state(session, version_id)
        row = (await session.execute(text(
            "SELECT j.id AS job_id,j.state,j.attempt,j.reason_code,r.id AS revision_id,"
            "j.source_format,j.parser_version,j.support_profile,"
            "coalesce(r.debts,'[]'::jsonb) AS debts,coalesce(r.processing_identity,'{}'::jsonb) AS processing_identity,"
            "r.coverage_state,r.expected_block_count,r.processed_block_count,"
            "r.fragment_count,coalesce(r.report_source_eligible,false) AS report_source_eligible "
            "FROM f1.material_evidence_job j LEFT JOIN f1.material_extraction_revision r "
            "ON r.enterprise_id=j.enterprise_id AND r.job_id=j.id "
            "WHERE j.enterprise_id=:enterprise AND j.document_version_id=:version "
            "ORDER BY j.created_at DESC,j.id DESC LIMIT 1"
        ), {"enterprise": tenant.enterprise_id, "version": version_id})).mappings().one_or_none()
    if row is None:
        return NativeExtractionStatus(version_id=version_id, state="not_registered", effective=effective)
    return NativeExtractionStatus(version_id=version_id, effective=effective, **dict(row))


class NativeFragmentOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    locator: dict
    location: str
    text: str
    body_sha256: str


class NativeFragmentsOut(BaseModel):
    version_id: uuid.UUID
    revision_id: uuid.UUID
    items: list[NativeFragmentOut]
    next_after: int | None = None


async def get_fragments(tenant: Tenant, version_id: uuid.UUID, revision_id: uuid.UUID,
                        *, after: int = -1, limit: int = 50) -> NativeFragmentsOut:
    await get_version(tenant, version_id)
    if not native_extraction_enabled():
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    if type(after) is not int or not -1 <= after <= 19999 or type(limit) is not int or not 1 <= limit <= 100:
        raise IngestionError("P3_REQUEST_INVALID", http_status=422)
    async with session_scope(role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
        result = (await session.execute(text(
            "SELECT f1.read_native_extraction_fragments(:version,:revision,:after,:limit)"
        ), {"version": version_id, "revision": revision_id, "after": after, "limit": limit})).scalar_one()
    if result is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    rows = result["items"]
    items = []
    for row in rows[:limit]:
        row["body_ciphertext"] = bytes.fromhex(row.pop("body_ciphertext_hex"))
        body = decrypt_fragment(row)
        items.append(NativeFragmentOut(id=row["id"], ordinal=row["ordinal"],
            locator=row["locator"], location=format_location(parse_locator(row["locator"])),
            text=body, body_sha256=row["body_sha256"]))
    return NativeFragmentsOut(version_id=version_id, revision_id=revision_id,
        items=items, next_after=items[-1].ordinal if len(rows)>limit else None)
