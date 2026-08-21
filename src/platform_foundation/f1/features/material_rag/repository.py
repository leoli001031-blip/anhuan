"""PostgreSQL persistence for canonical units, scope bindings, and jobs."""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping

from sqlalchemy import LargeBinary, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import _worker_dsn, session_scope
from .contracts import (
    CanonicalUnit,
    JobAction,
    MaterialRagIntegrityError,
    MaterialRagJobClaim,
    MaterialRagLeaseLost,
    SensitiveText,
)
from .security import (
    dataset_ref_aad,
    decrypt_text,
    encrypt_text,
    unit_aad,
    unit_aad_for_identity,
)

if TYPE_CHECKING:
    from ...auth import Tenant


_BINDING_NAMESPACE = uuid.UUID("fdc520dc-ffca-4ba3-a875-6ca74754655e")
_JOB_ACTIONS = frozenset(("index", "rebuild", "delete"))
_DATASET_REF_RE = re.compile(r"^[0-9a-f]{32}$")


def _lease_lost(source: str) -> MaterialRagLeaseLost:
    error = MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
    error.source = source
    return error


@dataclass(frozen=True, slots=True, repr=False)
class DatasetBinding:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    dataset_ref: str
    status: str

    def __repr__(self) -> str:
        return (
            "DatasetBinding("
            f"id={self.id!r}, enterprise_id={self.enterprise_id!r}, "
            f"knowledge_scope_id={self.knowledge_scope_id!r}, "
            f"dataset_ref=<redacted>, status={self.status!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class DatasetBindingState:
    status: str
    binding: DatasetBinding | None


@contextmanager
def live_source_mutation_fence(
    claim: MaterialRagJobClaim, *, lease_seconds: int = 300
) -> Iterator[None]:
    """Hold the claimed job and live upload-task rows for one mutation.

    Renewal, lifecycle proof, and ``FOR SHARE`` on the job plus upload task
    happen in one worker transaction.  Version and record rows stay
    worker-select-only and are not locked here.
    """
    if claim.action not in {"index", "rebuild"}:
        raise MaterialRagIntegrityError("MATERIAL_RAG_RELEASE_FENCE_FORBIDDEN")
    if not 30 <= lease_seconds <= 900:
        raise ValueError("MATERIAL_RAG_LEASE_INVALID")

    import psycopg

    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection:
        renewed = bool(
            connection.execute(
                "SELECT f1.renew_material_rag_job_lease(%s,%s,%s)",
                (str(claim.id), str(claim.lease_token), lease_seconds),
            ).fetchone()[0]
        )
        if not renewed:
            raise _lease_lost("MUTATION_FENCE")
        connection.execute(
            "SELECT set_config('f1.material_rag_job_id',%s,true),"
            "set_config('f1.material_rag_lease_token',%s,true)",
            (str(claim.id), str(claim.lease_token)),
        )
        source = connection.execute(
            """
            SELECT active_job.id
            FROM f1.material_rag_job AS active_job
            JOIN f1.document_version AS version
              ON version.enterprise_id = active_job.enterprise_id
             AND version.id = active_job.document_version_id
             AND version.document_record_id = active_job.document_record_id
             AND version.upload_task_id = active_job.upload_task_id
            JOIN f1.document_record AS record
              ON record.enterprise_id = version.enterprise_id
             AND record.id = version.document_record_id
             AND record.knowledge_scope_id = active_job.knowledge_scope_id
            JOIN f1.upload_task AS task
              ON task.enterprise_id = version.enterprise_id
             AND task.id = version.upload_task_id
             AND task.content_sha256 = active_job.source_sha256
            WHERE active_job.id = %s
              AND active_job.lease_token = %s
              AND active_job.enterprise_id = %s
              AND active_job.knowledge_scope_id = %s
              AND active_job.document_record_id = %s
              AND active_job.document_version_id = %s
              AND active_job.source_sha256 = %s
              AND active_job.action IN ('index','rebuild')
              AND active_job.status = 'running'
              AND active_job.lease_until > clock_timestamp()
              AND task.pipeline_kind = 'controlled_ingestion'
              AND task.status = 'done'
              AND task.processing_stage = 'ready'
              AND task.object_state = 'ready'
              AND task.scan_verdict = 'clean'
              AND task.preview_status = 'ready'
              AND task.quarantine_status = 'released'
              AND task.released_at IS NOT NULL
            FOR SHARE OF active_job, task
            """,
            (
                str(claim.id),
                str(claim.lease_token),
                str(claim.enterprise_id),
                str(claim.knowledge_scope_id),
                str(claim.document_record_id),
                str(claim.document_version_id),
                claim.source_sha256,
            ),
        ).fetchone()
        if source is None:
            raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_INDEXABLE")
        yield


@contextmanager
def live_scope_job_lock(claim: MaterialRagJobClaim) -> Iterator[None]:
    """Serialize every index/rebuild/delete job for one material scope.

    The PostgreSQL session lock spans the complete remote mutation sequence,
    while the lease is renewed both before and after lock acquisition.  This
    closes the gap where a last-version delete could otherwise race a sibling
    index between the local empty-scope proof and remote dataset deletion.
    """
    if claim.action not in _JOB_ACTIONS:
        raise MaterialRagIntegrityError("MATERIAL_RAG_JOB_ACTION_INVALID")
    identity = (
        "material-rag-scope-v1\x00"
        f"{claim.enterprise_id}\x00{claim.knowledge_scope_id}"
    ).encode("utf-8")
    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    import psycopg

    with psycopg.connect(dsn) as connection:
        lock_key = int(
            connection.execute(
                "SELECT pg_catalog.hashbyteaextended(%s,0)", (identity,)
            ).fetchone()[0]
        )
        connection.commit()
        locked = False
        try:
            renewed = bool(
                connection.execute(
                    "SELECT f1.renew_material_rag_job_lease(%s,%s,%s)",
                    (str(claim.id), str(claim.lease_token), 300),
                ).fetchone()[0]
            )
            connection.commit()
            if not renewed:
                raise _lease_lost("SCOPE_LOCK")
            connection.execute(
                "SELECT pg_catalog.pg_advisory_lock(%s)", (lock_key,)
            ).fetchone()
            connection.commit()
            locked = True
            renewed = bool(
                connection.execute(
                    "SELECT f1.renew_material_rag_job_lease(%s,%s,%s)",
                    (str(claim.id), str(claim.lease_token), 300),
                ).fetchone()[0]
            )
            connection.commit()
            if not renewed:
                raise _lease_lost("SCOPE_LOCK")
            yield
        finally:
            if locked:
                connection.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(%s)", (lock_key,)
                ).fetchone()
                connection.commit()


async def persist_canonical_units(
    session: AsyncSession, units: Iterable[CanonicalUnit]
) -> int:
    """Persist filtered units encrypted at rest; exact retries are no-ops."""
    materialized = tuple(units)
    if not materialized:
        return 0
    enterprise_ids = {unit.enterprise_id for unit in materialized}
    version_ids = {unit.document_version_id for unit in materialized}
    scope_ids = {unit.knowledge_scope_id for unit in materialized}
    if len(enterprise_ids) != 1 or len(version_ids) != 1 or len(scope_ids) != 1:
        raise ValueError("MATERIAL_UNIT_BATCH_IDENTITY_INVALID")
    if len({unit.id for unit in materialized}) != len(materialized):
        raise ValueError("MATERIAL_UNIT_BATCH_DUPLICATE")

    inserted = 0
    for unit in materialized:
        aad = unit_aad(unit)
        ciphertext, aad_sha = encrypt_text(unit.body.reveal(), aad)
        created = (
            await session.execute(
                text(
                    "INSERT INTO f1.material_rag_unit ("
                    "id,enterprise_id,knowledge_scope_id,document_record_id,"
                    "document_version_id,source_sha256,page_number,ordinal,"
                    "parser_version,body_ciphertext,body_sha256,body_aad_sha256,"
                    "ocr_applied,table_candidate,two_column_candidate) VALUES ("
                    ":id,:enterprise_id,:scope_id,:record_id,:version_id,"
                    ":source_sha,:page_number,:ordinal,:parser_version,"
                    ":body_ciphertext,:body_sha,:aad_sha,:ocr_applied,:table_candidate,"
                    ":two_column_candidate) ON CONFLICT (enterprise_id,id) "
                    "DO NOTHING RETURNING id"
                ).bindparams(
                    bindparam("body_ciphertext", type_=LargeBinary()),
                ),
                {
                    "id": unit.id,
                    "enterprise_id": unit.enterprise_id,
                    "scope_id": unit.knowledge_scope_id,
                    "record_id": unit.document_record_id,
                    "version_id": unit.document_version_id,
                    "source_sha": unit.source_sha256,
                    "page_number": unit.page_number,
                    "ordinal": unit.ordinal,
                    "parser_version": unit.parser_version,
                    "body_ciphertext": ciphertext,
                    "body_sha": unit.body_sha256,
                    "aad_sha": aad_sha,
                    "ocr_applied": unit.ocr_applied,
                    "table_candidate": unit.table_candidate,
                    "two_column_candidate": unit.two_column_candidate,
                },
            )
        ).first()
        if created is not None:
            inserted += 1
            continue
        existing = (
            await session.execute(
                text(
                    "SELECT knowledge_scope_id,document_record_id,"
                    "document_version_id,source_sha256,page_number,ordinal,"
                    "parser_version,body_sha256,table_candidate,"
                    "two_column_candidate,ocr_applied FROM f1.material_rag_unit "
                    "WHERE enterprise_id=:enterprise_id AND id=:id"
                ),
                {"enterprise_id": unit.enterprise_id, "id": unit.id},
            )
        ).mappings().one_or_none()
        expected = {
            "knowledge_scope_id": unit.knowledge_scope_id,
            "document_record_id": unit.document_record_id,
            "document_version_id": unit.document_version_id,
            "source_sha256": unit.source_sha256,
            "page_number": unit.page_number,
            "ordinal": unit.ordinal,
            "parser_version": unit.parser_version,
            "body_sha256": unit.body_sha256,
            "ocr_applied": unit.ocr_applied,
            "table_candidate": unit.table_candidate,
            "two_column_candidate": unit.two_column_candidate,
        }
        if existing is None or any(existing[key] != value for key, value in expected.items()):
            raise MaterialRagIntegrityError("MATERIAL_UNIT_IDENTITY_CONFLICT")
    return inserted


async def load_dataset_binding(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    allow_deleting: bool = False,
) -> DatasetBinding | None:
    statuses = "('ready','deleting')" if allow_deleting else "('ready')"
    row = (
        await session.execute(
            text(
                "SELECT id,dataset_ref_ciphertext,dataset_ref_sha256,"
                "dataset_ref_aad_sha256,status "
                "FROM f1.material_rag_scope_binding "
                "WHERE enterprise_id=:enterprise_id "
                "AND knowledge_scope_id=:scope_id AND backend='ragflow' "
                f"AND status IN {statuses}"
            ),
            {"enterprise_id": enterprise_id, "scope_id": knowledge_scope_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    aad = dataset_ref_aad(
        enterprise_id=enterprise_id,
        knowledge_scope_id=knowledge_scope_id,
        binding_id=row["id"],
    )
    dataset_ref = decrypt_text(
        bytes(row["dataset_ref_ciphertext"]), aad, str(row["dataset_ref_aad_sha256"])
    )
    actual_ref_sha = hashlib.sha256(dataset_ref.encode("utf-8")).hexdigest()
    if not _DATASET_REF_RE.fullmatch(dataset_ref) or not hmac.compare_digest(
        actual_ref_sha, str(row["dataset_ref_sha256"])
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")
    return DatasetBinding(
        row["id"],
        enterprise_id,
        knowledge_scope_id,
        dataset_ref,
        str(row["status"]),
    )


async def load_dataset_binding_state(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
) -> DatasetBindingState | None:
    """Return a scope binding lifecycle without exposing absent ciphertext."""
    row = (
        await session.execute(
            text(
                "SELECT status FROM f1.material_rag_scope_binding "
                "WHERE enterprise_id=:enterprise_id "
                "AND knowledge_scope_id=:scope_id AND backend='ragflow'"
            ),
            {"enterprise_id": enterprise_id, "scope_id": knowledge_scope_id},
        )
    ).mappings().one_or_none()
    if row is None:
        return None
    status = str(row["status"])
    binding = None
    if status in {"ready", "deleting"}:
        binding = await load_dataset_binding(
            session,
            enterprise_id=enterprise_id,
            knowledge_scope_id=knowledge_scope_id,
            allow_deleting=True,
        )
        if binding is None or binding.status != status:
            raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")
    return DatasetBindingState(status, binding)


async def persist_dataset_binding(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    dataset_ref: str,
) -> DatasetBinding:
    """Bind exactly one encrypted physical reference to a product scope."""
    if not isinstance(dataset_ref, str) or not _DATASET_REF_RE.fullmatch(dataset_ref):
        raise ValueError("MATERIAL_RAG_DATASET_REF_INVALID")
    binding_id = uuid.uuid5(
        _BINDING_NAMESPACE, f"{enterprise_id}\x00{knowledge_scope_id}\x00ragflow"
    )
    aad = dataset_ref_aad(
        enterprise_id=enterprise_id,
        knowledge_scope_id=knowledge_scope_id,
        binding_id=binding_id,
    )
    ciphertext, aad_sha = encrypt_text(dataset_ref, aad)
    ref_sha = hashlib.sha256(dataset_ref.encode("utf-8")).hexdigest()
    await session.execute(
        text(
            "INSERT INTO f1.material_rag_scope_binding ("
            "id,enterprise_id,knowledge_scope_id,backend,status) "
            "VALUES (:id,:enterprise_id,:scope_id,'ragflow','provisioning') "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "id": binding_id,
            "enterprise_id": enterprise_id,
            "scope_id": knowledge_scope_id,
        },
    )
    existing = (
        await session.execute(
            text(
                "SELECT id,backend,status,dataset_ref_sha256 "
                "FROM f1.material_rag_scope_binding "
                "WHERE enterprise_id=:enterprise_id AND knowledge_scope_id=:scope_id "
                "FOR UPDATE"
            ),
            {"enterprise_id": enterprise_id, "scope_id": knowledge_scope_id},
        )
    ).mappings().one_or_none()
    if existing is None or existing["id"] != binding_id or existing["backend"] != "ragflow":
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_CONFLICT")
    if existing["status"] == "deleting":
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_DELETING")
    if existing["status"] == "ready" and existing["dataset_ref_sha256"] != ref_sha:
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_CONFLICT")
    if existing["status"] != "ready":
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE f1.material_rag_scope_binding SET "
                        "dataset_ref_ciphertext=:ciphertext,"
                        "dataset_ref_sha256=:ref_sha,"
                        "dataset_ref_aad_sha256=:aad_sha,status='ready',"
                        "error_reason=NULL "
                        "WHERE enterprise_id=:enterprise_id AND id=:id"
                    ),
                    {
                        "ciphertext": ciphertext,
                        "ref_sha": ref_sha,
                        "aad_sha": aad_sha,
                        "enterprise_id": enterprise_id,
                        "id": binding_id,
                    },
                )
        except IntegrityError:
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_DATASET_BINDING_CONFLICT"
            ) from None
    binding = await load_dataset_binding(
        session,
        enterprise_id=enterprise_id,
        knowledge_scope_id=knowledge_scope_id,
    )
    if binding is None or binding.dataset_ref != dataset_ref:
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_CONFLICT")
    return binding


async def ensure_dataset_binding_intent(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
) -> str:
    """Durably create one scope binding intent before remote provisioning.

    The deterministic binding id is returned as a body-free provisioning
    token.  A crash before the remote dataset exists leaves a recoverable
    tombstone instead of an untracked remote side effect.
    """
    binding_id = uuid.uuid5(
        _BINDING_NAMESPACE, f"{enterprise_id}\x00{knowledge_scope_id}\x00ragflow"
    )
    await session.execute(
        text(
            "INSERT INTO f1.material_rag_scope_binding ("
            "id,enterprise_id,knowledge_scope_id,backend,status) "
            "VALUES (:id,:enterprise_id,:scope_id,'ragflow','provisioning') "
            "ON CONFLICT DO NOTHING"
        ),
        {
            "id": binding_id,
            "enterprise_id": enterprise_id,
            "scope_id": knowledge_scope_id,
        },
    )
    row = (
        await session.execute(
            text(
                "SELECT id,backend,status FROM f1.material_rag_scope_binding "
                "WHERE enterprise_id=:enterprise_id "
                "AND knowledge_scope_id=:scope_id FOR UPDATE"
            ),
            {"enterprise_id": enterprise_id, "scope_id": knowledge_scope_id},
        )
    ).mappings().one_or_none()
    if (
        row is not None
        and row["id"] == binding_id
        and row["backend"] == "ragflow"
        and row["status"] == "deleted"
    ):
        await session.execute(
            text(
                "UPDATE f1.material_rag_scope_binding SET "
                "status='provisioning',updated_at=statement_timestamp() "
                "WHERE enterprise_id=:enterprise_id AND id=:id "
                "AND status='deleted' AND dataset_ref_ciphertext IS NULL "
                "AND dataset_ref_sha256 IS NULL "
                "AND dataset_ref_aad_sha256 IS NULL"
            ),
            {"enterprise_id": enterprise_id, "id": binding_id},
        )
        row = (
            await session.execute(
                text(
                    "SELECT id,backend,status "
                    "FROM f1.material_rag_scope_binding "
                    "WHERE enterprise_id=:enterprise_id AND id=:id FOR UPDATE"
                ),
                {"enterprise_id": enterprise_id, "id": binding_id},
            )
        ).mappings().one_or_none()
    if (
        row is None
        or row["id"] != binding_id
        or row["backend"] != "ragflow"
        or row["status"] not in {"provisioning", "ready"}
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_CONFLICT")
    return str(row["status"])


async def prepare_empty_scope_dataset_delete(
    session: AsyncSession,
    *,
    claim: MaterialRagJobClaim,
    dataset_ref_sha256: str,
) -> bool:
    """Atomically prove an empty live-delete scope and fence its binding."""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT f1.prepare_empty_material_rag_scope("
                    ":job_id,:lease_token,:dataset_ref_sha256)"
                ),
                {
                    "job_id": claim.id,
                    "lease_token": claim.lease_token,
                    "dataset_ref_sha256": dataset_ref_sha256,
                },
            )
        ).scalar()
    )


async def finalize_empty_scope_dataset_delete(
    session: AsyncSession,
    *,
    claim: MaterialRagJobClaim,
    dataset_ref_sha256: str,
) -> bool:
    """Clear the exact deleted binding only under the same live delete lease."""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT f1.finalize_empty_material_rag_scope("
                    ":job_id,:lease_token,:dataset_ref_sha256)"
                ),
                {
                    "job_id": claim.id,
                    "lease_token": claim.lease_token,
                    "dataset_ref_sha256": dataset_ref_sha256,
                },
            )
        ).scalar()
    )


async def load_units_for_version(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    document_version_id: uuid.UUID,
) -> tuple[CanonicalUnit, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT id,document_record_id,source_sha256,page_number,ordinal,"
                "parser_version,body_ciphertext,body_sha256,body_aad_sha256,"
                "ocr_applied,table_candidate,two_column_candidate "
                "FROM f1.material_rag_unit WHERE enterprise_id=:enterprise_id "
                "AND knowledge_scope_id=:scope_id AND document_version_id=:version_id "
                "ORDER BY page_number,ordinal,id"
            ),
            {
                "enterprise_id": enterprise_id,
                "scope_id": knowledge_scope_id,
                "version_id": document_version_id,
            },
        )
    ).mappings().all()
    units: list[CanonicalUnit] = []
    for row in rows:
        aad = unit_aad_for_identity(
            enterprise_id=enterprise_id,
            knowledge_scope_id=knowledge_scope_id,
            unit_id=row["id"],
            document_record_id=row["document_record_id"],
            document_version_id=document_version_id,
            source_sha256=str(row["source_sha256"]),
            page_number=int(row["page_number"]),
            ordinal=int(row["ordinal"]),
            parser_version=str(row["parser_version"]),
            body_sha256=str(row["body_sha256"]),
        )
        body = decrypt_text(
            bytes(row["body_ciphertext"]), aad, str(row["body_aad_sha256"])
        )
        units.append(
            CanonicalUnit(
                id=row["id"],
                enterprise_id=enterprise_id,
                knowledge_scope_id=knowledge_scope_id,
                document_record_id=row["document_record_id"],
                document_version_id=document_version_id,
                source_sha256=str(row["source_sha256"]),
                page_number=int(row["page_number"]),
                ordinal=int(row["ordinal"]),
                parser_version=str(row["parser_version"]),
                body=SensitiveText(body),
                body_sha256=str(row["body_sha256"]),
                ocr_applied=bool(row["ocr_applied"]),
                table_candidate=bool(row["table_candidate"]),
                two_column_candidate=bool(row["two_column_candidate"]),
            )
        )
    return tuple(units)


async def enqueue_job_in_session(
    session,
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    action: JobAction,
    idempotency_key: str,
) -> uuid.UUID:
    if action not in _JOB_ACTIONS:
        raise ValueError("MATERIAL_RAG_JOB_ACTION_INVALID")
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
        raise ValueError("MATERIAL_RAG_IDEMPOTENCY_KEY_INVALID")
    source = (
        await session.execute(
            text(
                "SELECT record.id AS document_record_id,"
                "version.upload_task_id,version.version_no,"
                "record.latest_version_no,"
                "(version.version_no = record.latest_version_no) AS is_current,"
                "record.knowledge_scope_id,task.content_sha256,"
                "task.object_state,task.scan_verdict,task.preview_status,"
                "task.processing_stage,task.quarantine_status,task.released_at "
                "FROM f1.document_version AS version "
                "JOIN f1.document_record AS record ON "
                "record.enterprise_id=version.enterprise_id "
                "AND record.id=version.document_record_id "
                "JOIN f1.upload_task AS task ON "
                "task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                "WHERE version.enterprise_id=:enterprise_id "
                "AND version.id=:version_id "
                "AND task.pipeline_kind='controlled_ingestion'"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "version_id": document_version_id,
            },
        )
    ).mappings().one_or_none()
    if source is None:
        raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_FOUND")
    if not bool(source["is_current"]):
        raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_CURRENT")
    if action != "delete" and not (
        source["object_state"] == "ready"
        and source["scan_verdict"] == "clean"
        and source["preview_status"] == "ready"
        and source["processing_stage"] == "ready"
        and source["quarantine_status"] == "released"
        and source["released_at"] is not None
    ):
        raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_INDEXABLE")
    digest = hashlib.sha256(
        (
            "material-rag-job-v1\x00"
            f"{tenant.enterprise_id}\x00{source['knowledge_scope_id']}\x00"
            f"{document_version_id}\x00{action}\x00{idempotency_key}"
        ).encode("utf-8")
    ).hexdigest()
    job_id = uuid.uuid4()
    row = (
        await session.execute(
            text(
                "INSERT INTO f1.material_rag_job ("
                "id,enterprise_id,knowledge_scope_id,document_version_id,"
                "document_record_id,upload_task_id,source_sha256,action,status,"
                "idempotency_sha256) VALUES ("
                ":id,:enterprise_id,:scope_id,:version_id,:record_id,:upload_task_id,:source_sha,"
                ":action,'queued',:digest) "
                "ON CONFLICT (enterprise_id,idempotency_sha256) DO NOTHING "
                "RETURNING id"
            ),
            {
                "id": job_id,
                "enterprise_id": tenant.enterprise_id,
                "scope_id": source["knowledge_scope_id"],
                "version_id": document_version_id,
                "record_id": source["document_record_id"],
                "upload_task_id": source["upload_task_id"],
                "source_sha": source["content_sha256"],
                "action": action,
                "digest": digest,
            },
        )
    ).one_or_none()
    if row is None:
        existing = (
            await session.execute(
                text(
                    "SELECT id,knowledge_scope_id,document_record_id,"
                    "document_version_id,upload_task_id,source_sha256,action "
                    "FROM f1.material_rag_job WHERE enterprise_id=:enterprise_id "
                    "AND idempotency_sha256=:digest"
                ),
                {"enterprise_id": tenant.enterprise_id, "digest": digest},
            )
        ).mappings().one_or_none()
        expected = {
            "knowledge_scope_id": source["knowledge_scope_id"],
            "document_record_id": source["document_record_id"],
            "document_version_id": document_version_id,
            "upload_task_id": source["upload_task_id"],
            "source_sha256": source["content_sha256"],
            "action": action,
        }
        if existing is None or any(
            existing[key] != value for key, value in expected.items()
        ):
            raise MaterialRagIntegrityError("MATERIAL_RAG_IDEMPOTENCY_CONFLICT")
        return existing["id"]
    return row[0]


async def enqueue_job(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    action: JobAction,
    idempotency_key: str,
) -> uuid.UUID:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        result_id = await enqueue_job_in_session(
            session,
            tenant,
            document_version_id=document_version_id,
            action=action,
            idempotency_key=idempotency_key,
        )
        await session.commit()
        return result_id


async def claim_next_job(
    *, worker_id: str, lease_seconds: int = 300
) -> MaterialRagJobClaim | None:
    if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 120:
        raise ValueError("MATERIAL_RAG_WORKER_ID_INVALID")
    if not 1 <= lease_seconds <= 900:
        raise ValueError("MATERIAL_RAG_LEASE_INVALID")
    async with session_scope(role="f1_worker") as session:
        row = (
            await session.execute(
                text(
                    "SELECT job_id,enterprise_id,knowledge_scope_id,"
                    "document_record_id,document_version_id,upload_task_id,source_sha256,"
                    "action,lease_token,attempt "
                    "FROM f1.claim_next_material_rag_job(:worker_id,:lease_seconds)"
                ),
                {"worker_id": worker_id, "lease_seconds": lease_seconds},
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        await session.commit()
        return MaterialRagJobClaim(
            id=row["job_id"],
            enterprise_id=row["enterprise_id"],
            knowledge_scope_id=row["knowledge_scope_id"],
            document_record_id=row["document_record_id"],
            document_version_id=row["document_version_id"],
            upload_task_id=row["upload_task_id"],
            source_sha256=str(row["source_sha256"]),
            action=str(row["action"]),  # type: ignore[arg-type]
            lease_token=row["lease_token"],
            attempt=int(row["attempt"]),
        )


async def claim_job(
    job_id: uuid.UUID, *, worker_id: str, lease_seconds: int = 300
) -> MaterialRagJobClaim | None:
    if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 120:
        raise ValueError("MATERIAL_RAG_WORKER_ID_INVALID")
    if not 30 <= lease_seconds <= 900:
        raise ValueError("MATERIAL_RAG_LEASE_INVALID")
    async with session_scope(role="f1_worker") as session:
        row = (
            await session.execute(
                text(
                    "SELECT job_id,enterprise_id,knowledge_scope_id,"
                    "document_record_id,document_version_id,upload_task_id,source_sha256,"
                    "action,lease_token,attempt "
                    "FROM f1.claim_material_rag_job(:job_id,:worker_id,:lease_seconds)"
                ),
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        await session.commit()
        return MaterialRagJobClaim(
            id=row["job_id"],
            enterprise_id=row["enterprise_id"],
            knowledge_scope_id=row["knowledge_scope_id"],
            document_record_id=row["document_record_id"],
            document_version_id=row["document_version_id"],
            upload_task_id=row["upload_task_id"],
            source_sha256=str(row["source_sha256"]),
            action=str(row["action"]),  # type: ignore[arg-type]
            lease_token=row["lease_token"],
            attempt=int(row["attempt"]),
        )


async def renew_job_lease(
    claim: MaterialRagJobClaim, *, lease_seconds: int = 300
) -> bool:
    async with session_scope(role="f1_worker") as session:
        renewed = bool(
            (
                await session.execute(
                    text("SELECT f1.renew_material_rag_job_lease(:id,:token,:seconds)"),
                    {"id": claim.id, "token": claim.lease_token, "seconds": lease_seconds},
                )
            ).scalar()
        )
        await session.commit()
        return renewed


async def finish_job(
    claim: MaterialRagJobClaim,
    *,
    status: str,
    result_manifest_sha256: str | None = None,
    indexed_unit_count: int | None = None,
    reason: str | None = None,
    retry_seconds: int = 0,
) -> bool:
    if status not in {"done", "retry_wait", "failed"}:
        raise ValueError("MATERIAL_RAG_JOB_STATUS_INVALID")
    if reason is not None and (
        len(reason) > 80 or not reason.replace("_", "").isalnum() or reason.upper() != reason
    ):
        raise ValueError("MATERIAL_RAG_JOB_REASON_INVALID")
    if status == "done":
        if (
            result_manifest_sha256 is None
            or len(result_manifest_sha256) != 64
            or indexed_unit_count is None
            or indexed_unit_count < 0
            or reason is not None
            or retry_seconds != 0
        ):
            raise ValueError("MATERIAL_RAG_JOB_OUTCOME_INVALID")
    elif status == "retry_wait":
        if reason is None or not 1 <= retry_seconds <= 86_400:
            raise ValueError("MATERIAL_RAG_JOB_OUTCOME_INVALID")
    elif reason is None or retry_seconds != 0:
        raise ValueError("MATERIAL_RAG_JOB_OUTCOME_INVALID")
    async with session_scope(role="f1_worker") as session:
        completed = bool(
            (
                await session.execute(
                    text(
                        "SELECT f1.finish_material_rag_job("
                        ":id,:token,:status,:manifest,:count,:reason,:retry_seconds)"
                    ),
                    {
                        "id": claim.id,
                        "token": claim.lease_token,
                        "status": status,
                        "manifest": result_manifest_sha256,
                        "count": indexed_unit_count,
                        "reason": reason,
                        "retry_seconds": retry_seconds,
                    },
                )
            ).scalar()
        )
        await session.commit()
        return completed


__all__ = (
    "DatasetBinding",
    "DatasetBindingState",
    "claim_job",
    "claim_next_job",
    "enqueue_job",
    "enqueue_job_in_session",
    "finish_job",
    "ensure_dataset_binding_intent",
    "finalize_empty_scope_dataset_delete",
    "load_dataset_binding",
    "load_dataset_binding_state",
    "load_units_for_version",
    "live_source_mutation_fence",
    "live_scope_job_lock",
    "prepare_empty_scope_dataset_delete",
    "persist_canonical_units",
    "persist_dataset_binding",
    "renew_job_lease",
)
