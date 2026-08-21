"""Fresh-process probe after dedicated PG+MinIO restart.

Reconnects from environment secrets, re-reads MinIO, and runs production
repository/service retrieval.  Parent-process memory is not evidence.
Stdout is a closed count JSON; keys, bodies, DSN, and IDs are never printed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCUMENTS_BUCKET = "anhuan-f1-documents"
QUARANTINE_BUCKET = "anhuan-f1-quarantine"
PREVIEW_BUCKET = "anhuan-f1-previews"
FIXTURE_NS = uuid.UUID("6c2f8d1e-4a0b-4f33-9c7a-12b9e0d4a8f1")


class ProbeError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProbeTransport:
    def __init__(self) -> None:
        self.candidates: tuple[object, ...] = ()

    async def retrieve_candidates(self, query, datasets, limit):
        del query, datasets, limit
        return self.candidates


def _secret(name: str) -> str:
    path = Path(os.environ["F1_SECRETS_DIR"]) / name
    return path.read_text(encoding="ascii").strip()


def _tenants() -> tuple[object, object, uuid.UUID, uuid.UUID]:
    from infra.f1 import local_seed
    from platform_foundation.f1.auth import Tenant

    tenant_a = Tenant(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub="db906685-6906-4bc4-9d3a-9011975fd132",
        roles=("enterprise_admin",),
        role="enterprise_admin",
    )
    tenant_b = Tenant(
        enterprise_id=local_seed.ENTERPRISE_B,
        sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
        roles=("enterprise_admin",),
        role="enterprise_admin",
    )
    return (
        tenant_a,
        tenant_b,
        uuid.uuid5(FIXTURE_NS, "br-client-a"),
        uuid.uuid5(FIXTURE_NS, "br-client-b"),
    )


def _reread_minio() -> int:
    from minio import Minio

    client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=_secret("minio_root_user"),
        secret_key=_secret("minio_root_password"),
        secure=False,
    )
    count = 0
    digest = hashlib.sha256(b"BR_POST_RESTART_MINIO_V1\x00")
    for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
        if not client.bucket_exists(bucket):
            continue
        for item in client.list_objects(bucket, recursive=True):
            object_key = str(item.object_name or "")
            if not object_key or object_key.endswith("/"):
                continue
            response = client.get_object(bucket, object_key)
            try:
                body = response.read()
            finally:
                response.close()
                response.release_conn()
            count += 1
            digest.update(len(body).to_bytes(8, "big"))
            digest.update(hashlib.sha256(body).digest())
            del body
            del object_key
    if count <= 0:
        raise ProbeError("MINIO_RECONNECT_EMPTY")
    return count


def _load_units() -> list[dict[str, Any]]:
    import psycopg

    with psycopg.connect(_secret("f1_bootstrap_dsn")) as connection:
        rows = connection.execute(
            "SELECT unit.id, unit.enterprise_id, unit.knowledge_scope_id, "
            "unit.document_record_id, unit.document_version_id, "
            "unit.source_sha256, unit.page_number, unit.body_sha256, "
            "scope.scope_kind FROM f1.material_rag_unit AS unit "
            "JOIN f1.material_knowledge_scope AS scope "
            "ON scope.enterprise_id=unit.enterprise_id "
            "AND scope.id=unit.knowledge_scope_id"
        ).fetchall()
    records = []
    for row in rows:
        records.append(
            {
                "canonical_unit_id": row[0],
                "enterprise_id": row[1],
                "knowledge_scope_id": row[2],
                "document_record_id": row[3],
                "document_version_id": row[4],
                "source_sha256": str(row[5]),
                "page_number": int(row[6]),
                "body_sha256": str(row[7]),
                "scope_kind": str(row[8]),
            }
        )
    if not records:
        raise ProbeError("UNITS_MISSING")
    return records


def _candidates(units: list[dict[str, Any]]):
    from platform_foundation.f1.features.material_rag.ragflow_adapter import (
        RemoteCandidate,
    )

    return tuple(
        RemoteCandidate(
            canonical_unit_id=item["canonical_unit_id"],
            knowledge_scope_id=item["knowledge_scope_id"],
            document_record_id=item["document_record_id"],
            document_version_id=item["document_version_id"],
            source_sha256=item["source_sha256"],
            page_number=item["page_number"],
            body_sha256=item["body_sha256"],
        )
        for item in units
    )


def _visibility(
    result,
    *,
    tenant,
    context,
    units: list[dict[str, Any]],
) -> tuple[int, int]:
    allowed = set(context._scope_ids)
    by_id = {item["canonical_unit_id"]: item for item in units}
    cross_tenant = 0
    cross_scope = 0
    for evidence in result.evidence:
        item = by_id.get(evidence.canonical_unit_id)
        if item is None:
            cross_scope += 1
            continue
        if item["enterprise_id"] != tenant.enterprise_id:
            cross_tenant += 1
        if item["knowledge_scope_id"] not in allowed:
            cross_scope += 1
        elif context.kind == "service_provider" and item["scope_kind"] != "service_provider":
            cross_scope += 1
    return cross_tenant, cross_scope


async def _retrieve(units: list[dict[str, Any]]) -> dict[str, int]:
    from platform_foundation.f1.features.material_rag.security import (
        CLIENT_B_RETRIEVAL_QUERY_TEXT,
        PROVIDER_RETRIEVAL_QUERY_TEXT,
    )
    from platform_foundation.f1.features.material_rag.service import (
        MaterialRetrievalService,
        PostgresMaterialRagRepository,
    )

    tenant_a, tenant_b, _client_a_id, client_b_id = _tenants()
    transport = ProbeTransport()
    transport.candidates = _candidates(units)
    service = MaterialRetrievalService(PostgresMaterialRagRepository(), transport)
    context_a = await service.derive_retrieval_context(tenant_a, None)
    result_a = await service.retrieve_registered(
        PROVIDER_RETRIEVAL_QUERY_TEXT, tenant_a, context_a
    )
    context_b = await service.derive_retrieval_context(tenant_b, client_b_id)
    result_b = await service.retrieve_registered(
        CLIENT_B_RETRIEVAL_QUERY_TEXT, tenant_b, context_b
    )
    cross_tenant_a, cross_scope_a = _visibility(
        result_a, tenant=tenant_a, context=context_a, units=units
    )
    cross_tenant_b, cross_scope_b = _visibility(
        result_b, tenant=tenant_b, context=context_b, units=units
    )
    if not result_a.evidence or not result_b.evidence:
        raise ProbeError("POST_RESTART_RETRIEVAL_EMPTY")
    return {
        "cross_scope_visible": cross_scope_a + cross_scope_b,
        "cross_tenant_visible": cross_tenant_a + cross_tenant_b,
        "retrieval_a_ok": 1,
        "retrieval_b_ok": 1,
    }


def main() -> int:
    try:
        if not os.environ.get("F1_SECRETS_DIR") or not os.environ.get("MINIO_ENDPOINT"):
            raise ProbeError("PROBE_ENV_MISSING")
        minio_count = _reread_minio()
        units = _load_units()
        retrieved = asyncio.run(_retrieve(units))
        payload = {
            "cross_scope_visible": retrieved["cross_scope_visible"],
            "cross_tenant_visible": retrieved["cross_tenant_visible"],
            "fresh_process": 1,
            "minio_object_count": minio_count,
            "minio_reconnect_ok": 1,
            "retrieval_a_ok": retrieved["retrieval_a_ok"],
            "retrieval_b_ok": retrieved["retrieval_b_ok"],
        }
        if (
            payload["cross_tenant_visible"] != 0
            or payload["cross_scope_visible"] != 0
            or payload["retrieval_a_ok"] != 1
            or payload["retrieval_b_ok"] != 1
            or payload["minio_reconnect_ok"] != 1
        ):
            raise ProbeError("POST_RESTART_PROBE_INVALID")
    except ProbeError as error:
        sys.stderr.write(error.code + "\n")
        return 2
    sys.stdout.write(
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
