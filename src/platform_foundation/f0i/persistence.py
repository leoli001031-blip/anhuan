"""Tenant-scoped encrypted persistence for F0-I canonical evidence.

This module is deliberately lower level than :mod:`platform_foundation.f0i.replay`.
It accepts already verified, in-memory canonical units, encrypts every body slice
inside PostgreSQL, and returns body-free aggregate evidence.  No API in this
module renders a DSN, key, source name, path, or plaintext body.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import uuid
from collections.abc import Mapping, Sequence

from psycopg import Connection

from ..auth import SessionContext
from ..f0h.runtime_config import RuntimeBundle
from .contracts import (
    CHUNK_RULE,
    LEAF_RULE,
    CanonicalUnitDraft,
    ChunkBlockLinkDraft,
    F0IError,
    canonical_sha256,
    stable_uuid4,
)
from .chunking import verify_reconstruction
from .extractors import LeafObservation, UnitObservation


_CIPHER_OPTIONS = "cipher-algo=aes256,compress-algo=0"
_PARSER_RULE_VERSION = "F0I_NATIVE_OCR_OOXML_V1"
_LOCATION_RULE_VERSION = "OBSERVED_LOCATION_V1"
_PARSER_RULE_SHA256 = canonical_sha256(
    {
        "native": "PYPDF_6.14.2_STRICT_PLAIN",
        "ocr": "F0H_PPOCRV6_PRIVATE_BLOCKS",
        "ooxml": "STRICT_XML_STRUCTURE_ONLY",
        "version": _PARSER_RULE_VERSION,
    }
)
_CHUNK_RULE_SHA256 = canonical_sha256(
    {
        "chunk_rule": CHUNK_RULE,
        "leaf_rule": LEAF_RULE,
        "maximum": 800,
        "minimum": 300,
        "overlap": 0,
    }
)
_OCR_OUTPUT_CONTRACT_SHA256 = canonical_sha256(
    {
        "bbox": "RENDERED_PIXEL_TOP_LEFT_V1",
        "body_protocol": "f0f-body-result-v1",
        "model": "PP-OCRv6-small",
        "normalization": "ocr-text-nfc-lf-v1",
    }
)


@dataclass(frozen=True, slots=True)
class ConfigurationIdentity:
    configuration_id: uuid.UUID
    configuration_sha256: str
    key_fingerprint_sha256: str
    full_plan_sha256: str


@dataclass(frozen=True, slots=True)
class SourcePage:
    processing_unit_id: uuid.UUID
    source_unit_id: str
    unit_ordinal: int
    unit_kind: str
    page_no: int
    candidate_decision: str
    evidence_sha256: str
    native_text_identity_sha256: str
    native_characters: int


@dataclass(frozen=True, slots=True)
class SourceDocument:
    enterprise_id: uuid.UUID
    document_version_id: uuid.UUID
    object_blob_id: uuid.UUID
    source_object_sha256: str
    source_object_size_bytes: int
    processing_plan_id: uuid.UUID
    source_document_id: str
    source_plan_sha256: str
    source_schema_version: str
    source_rule_version: str
    document_type: str
    source_group: str
    corpus_role: str
    enterprise_fact_allowed: bool
    current_regulation_allowed: bool
    search_publish_allowed: bool
    visual_unit_count: int
    pages: tuple[SourcePage, ...]


@dataclass(frozen=True, slots=True)
class OcrRenderEvidence:
    width_px: int
    height_px: int
    dpi: int | None
    origin: str
    renderer_id: str
    renderer_version: str
    render_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalUnitRecord:
    observation: UnitObservation
    canonical: CanonicalUnitDraft
    source_page: SourcePage | None
    structure_anchor_sha256: str | None
    ocr_render: OcrRenderEvidence | None = None


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    source: SourceDocument
    scope_kind: str
    units: tuple[CanonicalUnitRecord, ...] = ()
    structure_summary_sha256: str | None = None
    docx_paragraph_count: int | None = None
    docx_table_count: int | None = None
    docx_row_count: int | None = None
    docx_cell_count: int | None = None
    xlsx_sheet_count: int | None = None
    xlsx_cell_count: int | None = None
    xlsx_value_cell_count: int | None = None
    xlsx_formula_count: int | None = None
    xlsx_formula_cached_value_count: int | None = None
    deferred_reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class RunInput:
    profile: str
    input_manifest_sha256: str
    input_summary_sha256: str
    requested_document_count: int


@dataclass(frozen=True, slots=True)
class PersistResult:
    run_id: uuid.UUID
    run_identity_sha256: str
    rows_inserted: int
    configuration_inserted: bool


def parser_rule_sha256() -> str:
    return _PARSER_RULE_SHA256


def chunk_rule_sha256() -> str:
    return _CHUNK_RULE_SHA256


def set_tenant_context(
    connection: Connection[Mapping[str, object]], context: SessionContext
) -> None:
    """Set and verify the transaction-local tenant capability."""

    if not isinstance(context, SessionContext):
        raise F0IError("DATABASE_OPERATION_FAILED")
    row = connection.execute(
        "SELECT set_config('f0d.enterprise_id',%s,true) AS enterprise_id,"
        "set_config('f0d.actor_id',%s,true) AS actor_id,"
        "set_config('f0d.session_token_sha256',%s,true) AS token_sha256",
        (
            str(context.enterprise_id),
            str(context.actor_id),
            context.session_token_sha256,
        ),
    ).fetchone()
    if (
        row is None
        or str(row["enterprise_id"]) != str(context.enterprise_id)
        or str(row["actor_id"]) != str(context.actor_id)
        or str(row["token_sha256"]) != context.session_token_sha256
    ):
        raise F0IError("DATABASE_OPERATION_FAILED")
    allowed = connection.execute(
        "SELECT f0d.context_session_authorized(%s) AS authorized",
        (context.enterprise_id,),
    ).fetchone()
    if allowed is None or allowed.get("authorized") is not True:
        raise F0IError("DATABASE_OPERATION_FAILED")


def configuration_identity(
    context: SessionContext,
    bundle: RuntimeBundle,
    *,
    full_plan_sha256: str,
    key_fingerprint_sha256: str,
) -> ConfigurationIdentity:
    """Derive the exact ID/hash generated by ``f0i.configuration``."""

    if not isinstance(context, SessionContext) or not isinstance(bundle, RuntimeBundle):
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    values = _configuration_values(
        bundle,
        full_plan_sha256=full_plan_sha256,
        key_fingerprint_sha256=key_fingerprint_sha256,
        verifier_plaintext_sha256="0" * 64,
    )
    configuration_id = stable_uuid4(
        "f0i.configuration.v1",
        context.enterprise_id,
        context.actor_id,
        {key: value for key, value in values.items() if key != "verifier_plaintext_sha256"},
    )
    verifier_sha256 = hashlib.sha256(configuration_id.bytes).hexdigest()
    values["verifier_plaintext_sha256"] = verifier_sha256
    return ConfigurationIdentity(
        configuration_id=configuration_id,
        configuration_sha256=_sql_chain(_configuration_hash_values(values)),
        key_fingerprint_sha256=key_fingerprint_sha256,
        full_plan_sha256=full_plan_sha256,
    )


def inspect_configuration(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    bundle: RuntimeBundle,
    key: memoryview,
) -> bool:
    """Return whether the expected configuration exists and validates."""

    set_tenant_context(connection, context)
    row = connection.execute(
        "SELECT id,actor_id,configuration_sha256,key_fingerprint_sha256,"
        "registered_full_plan_sha256,ocr_configuration_sha256,"
        "ocr_model_bundle_sha256,ocr_execution_profile_sha256,"
        "ocr_runtime_image_id,ocr_runtime_lock_sha256,"
        "ocr_output_contract_sha256,key_verifier_plaintext_sha256,"
        "f0f_crypto.pgp_sym_decrypt_bytea(key_verifier_ciphertext,"
        "encode(%s::bytea,'hex'),%s) AS verifier FROM f0i.configuration "
        "WHERE enterprise_id=%s AND id=%s",
        (key, _CIPHER_OPTIONS, context.enterprise_id, identity.configuration_id),
    ).fetchone()
    if row is None:
        count = connection.execute(
            "SELECT count(*) AS count FROM f0i.configuration"
        ).fetchone()
        if count is None or int(count["count"]) != 0:
            raise F0IError("REPLAY_MISMATCH")
        return False
    verifier = bytearray(row["verifier"])  # type: ignore[arg-type]
    try:
        expected = {
            "id": identity.configuration_id,
            "actor_id": context.actor_id,
            "configuration_sha256": identity.configuration_sha256,
            "key_fingerprint_sha256": identity.key_fingerprint_sha256,
            "registered_full_plan_sha256": identity.full_plan_sha256,
            "ocr_configuration_sha256": bundle.configuration_sha256,
            "ocr_model_bundle_sha256": bundle.model_bundle_sha256,
            "ocr_execution_profile_sha256": bundle.execution_profile_sha256,
            "ocr_runtime_image_id": bundle.container_image_id,
            "ocr_runtime_lock_sha256": bundle.lock_sha256,
            "ocr_output_contract_sha256": _OCR_OUTPUT_CONTRACT_SHA256,
            "key_verifier_plaintext_sha256": hashlib.sha256(
                identity.configuration_id.bytes
            ).hexdigest(),
        }
        if any(str(row[key]) != str(value) for key, value in expected.items()) or bytes(
            verifier
        ) != identity.configuration_id.bytes:
            raise F0IError("REPLAY_MISMATCH")
        return True
    finally:
        _wipe(verifier)


def load_source_documents(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    entries: Sequence[Mapping[str, object]],
) -> tuple[SourceDocument, ...]:
    """Resolve only registered group/line identities into frozen DB provenance."""

    set_tenant_context(connection, context)
    output: list[SourceDocument] = []
    for entry in entries:
        group = entry.get("group")
        line = entry.get("line")
        if group not in {"core", "negative"} or isinstance(line, bool) or not isinstance(
            line, int
        ):
            raise F0IError("REPLAY_MISMATCH")
        row = connection.execute(
            "SELECT registry.enterprise_id,registry.source_document_id,"
            "registry.expected_sha256,registry.expected_size_bytes,"
            "registry.document_type,registry.source_group,registry.corpus_role,"
            "registry.enterprise_fact_allowed,registry.current_regulation_allowed,"
            "registry.search_publish_allowed,version.id AS document_version_id,"
            "version.object_blob_id,blob.sha256 AS object_sha256,"
            "blob.size_bytes AS object_size_bytes,plan.id AS processing_plan_id,"
            "plan.source_plan_sha256,plan.source_schema_version,"
            "plan.source_rule_version,plan.visual_unit_count "
            "FROM f0d.fixture_source_registry AS registry "
            "JOIN f0d.document_version AS version ON "
            "version.enterprise_id=registry.enterprise_id "
            "AND version.source_document_id=registry.source_document_id "
            "JOIN f0d.object_blob AS blob ON blob.enterprise_id=version.enterprise_id "
            "AND blob.id=version.object_blob_id "
            "JOIN f0d.document_processing_plan AS plan ON "
            "plan.enterprise_id=version.enterprise_id "
            "AND plan.document_version_id=version.id "
            "WHERE registry.enterprise_id=%s AND registry.source_group=%s "
            "AND registry.source_line=%s",
            (context.enterprise_id, group, line),
        ).fetchall()
        if len(row) != 1:
            raise F0IError("REPLAY_MISMATCH")
        record = row[0]
        if (
            str(record["source_document_id"]) != str(entry.get("document_id"))
            or str(record["document_type"]) != str(entry.get("type"))
            or str(record["source_group"]) != str(group)
            or str(record["corpus_role"]) != str(entry.get("corpus_role"))
            or bool(record["enterprise_fact_allowed"])
            is not bool(entry.get("enterprise_fact_allowed"))
            or bool(record["current_regulation_allowed"])
            is not bool(entry.get("current_regulation_allowed"))
            or bool(record["search_publish_allowed"])
            is not bool(entry.get("search_publish_allowed"))
            or str(record["expected_sha256"]) != str(record["object_sha256"])
            or int(record["expected_size_bytes"]) != int(record["object_size_bytes"])
        ):
            raise F0IError("REPLAY_MISMATCH")
        units = connection.execute(
            "SELECT id,source_unit_id,unit_ordinal,unit_kind,page_no,"
            "candidate_decision,evidence_sha256,native_text_identity_sha256,"
            "native_characters FROM f0d.document_processing_unit "
            "WHERE enterprise_id=%s AND processing_plan_id=%s "
            "AND unit_kind IN ('PAGE','IMAGE') ORDER BY unit_ordinal",
            (context.enterprise_id, record["processing_plan_id"]),
        ).fetchall()
        pages = tuple(
            SourcePage(
                processing_unit_id=item["id"],  # type: ignore[arg-type]
                source_unit_id=str(item["source_unit_id"]),
                unit_ordinal=int(item["unit_ordinal"]),
                unit_kind=str(item["unit_kind"]),
                page_no=int(item["page_no"]),
                candidate_decision=str(item["candidate_decision"]),
                evidence_sha256=str(item["evidence_sha256"]),
                native_text_identity_sha256=str(item["native_text_identity_sha256"]),
                native_characters=int(item["native_characters"]),
            )
            for item in units
        )
        _validate_source_pages(entry, pages, int(record["visual_unit_count"]))
        output.append(
            SourceDocument(
                enterprise_id=record["enterprise_id"],  # type: ignore[arg-type]
                document_version_id=record["document_version_id"],  # type: ignore[arg-type]
                object_blob_id=record["object_blob_id"],  # type: ignore[arg-type]
                source_object_sha256=str(record["object_sha256"]),
                source_object_size_bytes=int(record["object_size_bytes"]),
                processing_plan_id=record["processing_plan_id"],  # type: ignore[arg-type]
                source_document_id=str(record["source_document_id"]),
                source_plan_sha256=str(record["source_plan_sha256"]),
                source_schema_version=str(record["source_schema_version"]),
                source_rule_version=str(record["source_rule_version"]),
                document_type=str(record["document_type"]),
                source_group=str(record["source_group"]),
                corpus_role=str(record["corpus_role"]),
                enterprise_fact_allowed=bool(record["enterprise_fact_allowed"]),
                current_regulation_allowed=bool(record["current_regulation_allowed"]),
                search_publish_allowed=bool(record["search_publish_allowed"]),
                visual_unit_count=int(record["visual_unit_count"]),
                pages=pages,
            )
        )
    return tuple(output)


def existing_scope_versions(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    versions: Sequence[uuid.UUID],
) -> frozenset[uuid.UUID]:
    set_tenant_context(connection, context)
    rows = connection.execute(
        "SELECT document_version_id FROM f0i.document_scope "
        "WHERE enterprise_id=%s AND configuration_id=%s "
        "AND configuration_sha256=%s AND document_version_id=ANY(%s)",
        (
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            list(versions),
        ),
    ).fetchall()
    return frozenset(row["document_version_id"] for row in rows)  # type: ignore[misc]


def find_run(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    run_input: RunInput,
) -> Mapping[str, object] | None:
    set_tenant_context(connection, context)
    return connection.execute(
        "SELECT * FROM f0i.run WHERE enterprise_id=%s AND configuration_id=%s "
        "AND configuration_sha256=%s AND profile=%s "
        "AND input_manifest_sha256=%s AND input_summary_sha256=%s",
        (
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            run_input.profile,
            run_input.input_manifest_sha256,
            run_input.input_summary_sha256,
        ),
    ).fetchone()


def validate_persisted_run(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    run_input: RunInput,
    replay_summary_sha256: str,
    summary: Mapping[str, int],
    *,
    ocr_call_count: int,
) -> None:
    """Bind the complete immutable run row to its input and verified summary."""

    set_tenant_context(connection, context)
    row = find_run(connection, context, identity, run_input)
    run_id = stable_uuid4(
        "f0i.run.v1",
        context.enterprise_id,
        identity.configuration_id,
        run_input.profile,
        run_input.input_manifest_sha256,
        run_input.input_summary_sha256,
    )
    run_identity = _sql_chain(
        (
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            run_input.profile,
            run_input.input_manifest_sha256,
            run_input.input_summary_sha256,
        )
    )
    terminal = "LOCAL_CANONICAL_CHUNKS_READY"
    accuracy = "ACCURACY_NOT_EVALUATED"
    search = "SEARCH_NOT_READY"
    production = "NOT_PRODUCTION"
    benchmark = "NONE"
    external = "DENY"
    run_chain_values: tuple[object, ...] = (
        context.enterprise_id,
        context.actor_id,
        identity.configuration_id,
        identity.configuration_sha256,
        run_input.profile,
        run_input.input_manifest_sha256,
        run_input.input_summary_sha256,
        replay_summary_sha256,
        run_input.requested_document_count,
        run_input.requested_document_count,
        summary["visual_documents"],
        summary["structure_documents"],
        summary["deferred_documents"],
        summary["visual_units"],
        summary["native_visual_units"],
        summary["ocr_visual_units"],
        summary["structure_units"],
        summary["parent_chunks"],
        summary["child_chunks"],
        summary["blocks"],
        ocr_call_count,
        0,
        terminal,
        accuracy,
        search,
        production,
        benchmark,
        external,
    )
    expected = (
        run_id,
        context.enterprise_id,
        context.actor_id,
        identity.configuration_id,
        identity.configuration_sha256,
        run_input.profile,
        run_input.input_manifest_sha256,
        run_input.input_summary_sha256,
        replay_summary_sha256,
        run_input.requested_document_count,
        run_input.requested_document_count,
        summary["visual_documents"],
        summary["structure_documents"],
        summary["deferred_documents"],
        summary["visual_units"],
        summary["native_visual_units"],
        summary["ocr_visual_units"],
        summary["structure_units"],
        summary["parent_chunks"],
        summary["child_chunks"],
        summary["blocks"],
        ocr_call_count,
        0,
        terminal,
        accuracy,
        search,
        production,
        benchmark,
        external,
        run_identity,
        _sql_chain(run_chain_values),
    )
    fields = (
        "id", "enterprise_id", "actor_id", "configuration_id",
        "configuration_sha256", "profile", "input_manifest_sha256",
        "input_summary_sha256", "replay_summary_sha256",
        "requested_document_count", "resolved_document_count",
        "visual_document_count", "structure_document_count",
        "deferred_document_count", "visual_unit_count", "native_visual_count",
        "ocr_visual_count", "structure_unit_count", "parent_chunk_count",
        "child_chunk_count", "block_count", "ocr_call_count", "error_count",
        "terminal_status", "accuracy_status", "search_status",
        "production_status", "benchmark_tier", "external_processing_policy",
        "run_identity_sha256", "run_chain_sha256",
    )
    if row is None or tuple(
        _project_value(field_name, row[field_name]) for field_name in fields
    ) != expected:
        raise F0IError("REPLAY_MISMATCH")


def persist_run(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    bundle: RuntimeBundle,
    key: memoryview,
    run_input: RunInput,
    replay_summary_sha256: str,
    summary: Mapping[str, int],
    documents: Sequence[DocumentRecord],
    *,
    ocr_call_count: int,
) -> PersistResult:
    """Atomically insert configuration, one run, and every new scope/unit."""

    set_tenant_context(connection, context)
    inserted_configuration = _ensure_configuration(
        connection, context, identity, bundle, key
    )
    run_id = stable_uuid4(
        "f0i.run.v1",
        context.enterprise_id,
        identity.configuration_id,
        run_input.profile,
        run_input.input_manifest_sha256,
        run_input.input_summary_sha256,
    )
    run_row = connection.execute(
        "INSERT INTO f0i.run(id,enterprise_id,actor_id,configuration_id,"
        "configuration_sha256,profile,input_manifest_sha256,input_summary_sha256,"
        "replay_summary_sha256,requested_document_count,resolved_document_count,"
        "visual_document_count,structure_document_count,deferred_document_count,"
        "visual_unit_count,native_visual_count,ocr_visual_count,"
        "structure_unit_count,parent_chunk_count,child_chunk_count,block_count,"
        "ocr_call_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_identity_sha256",
        (
            run_id,
            context.enterprise_id,
            context.actor_id,
            identity.configuration_id,
            identity.configuration_sha256,
            run_input.profile,
            run_input.input_manifest_sha256,
            run_input.input_summary_sha256,
            replay_summary_sha256,
            run_input.requested_document_count,
            run_input.requested_document_count,
            summary["visual_documents"],
            summary["structure_documents"],
            summary["deferred_documents"],
            summary["visual_units"],
            summary["native_visual_units"],
            summary["ocr_visual_units"],
            summary["structure_units"],
            summary["parent_chunks"],
            summary["child_chunks"],
            summary["blocks"],
            ocr_call_count,
        ),
    ).fetchone()
    if run_row is None:
        raise F0IError("PERSISTENCE_FAILED")
    run_identity = str(run_row["run_identity_sha256"])
    rows_inserted = 1 + int(inserted_configuration)
    for document in documents:
        rows_inserted += _insert_document(
            connection,
            context,
            identity,
            key,
            run_id,
            run_identity,
            document,
        )
    return PersistResult(
        run_id=run_id,
        run_identity_sha256=run_identity,
        rows_inserted=rows_inserted,
        configuration_inserted=inserted_configuration,
    )


def database_summary(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    sources: Sequence[SourceDocument],
    key: memoryview,
) -> dict[str, int]:
    """Reverse-validate selected encrypted evidence and return stable counts."""

    set_tenant_context(connection, context)
    versions = [source.document_version_id for source in sources]
    if not versions:
        raise F0IError("REPLAY_MISMATCH")
    projection = "smoke" if len(sources) == 10 else "full" if len(sources) == 26 else None
    scope_rows = connection.execute(
        "SELECT * FROM f0i.document_scope WHERE enterprise_id=%s "
        "AND configuration_id=%s AND configuration_sha256=%s "
        "AND document_version_id=ANY(%s)",
        (
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            versions,
        ),
    ).fetchall()
    if len(scope_rows) != len(sources):
        raise F0IError("REPLAY_MISMATCH")
    scope_ids = [row["id"] for row in scope_rows]
    counts = connection.execute(
        "SELECT "
        "count(*) AS documents,"
        "count(*) FILTER (WHERE scope_kind='VISUAL') AS visual_documents,"
        "count(*) FILTER (WHERE scope_kind='STRUCTURE') AS structure_documents,"
        "count(*) FILTER (WHERE scope_kind='DEFERRED') AS deferred_documents,"
        "sum(visual_unit_count) AS visual_units,"
        "sum(structure_unit_count) AS structure_units,"
        "sum(COALESCE(docx_paragraph_count,0)) AS docx_paragraphs,"
        "sum(COALESCE(docx_table_count,0)) AS docx_tables,"
        "sum(COALESCE(docx_row_count,0)) AS docx_rows,"
        "sum(COALESCE(docx_cell_count,0)) AS docx_table_cells,"
        "sum(COALESCE(xlsx_sheet_count,0)) AS xlsx_sheets,"
        "sum(COALESCE(xlsx_cell_count,0)) AS xlsx_cells,"
        "sum(COALESCE(xlsx_formula_count,0)) AS xlsx_formula_cells,"
        "sum(COALESCE(xlsx_formula_cached_value_count,0)) "
        "AS xlsx_formula_cached_values,"
        "sum(COALESCE(xlsx_value_cell_count,0)) AS xlsx_value_cells,"
        "count(*) FILTER (WHERE source_group='negative') AS negative_scopes,"
        "sum((enterprise_fact_allowed::int)+(current_regulation_allowed::int)+"
        "(search_publish_allowed::int)) FILTER (WHERE source_group='negative') "
        "AS negative_enabled_gates FROM f0i.document_scope "
        "WHERE enterprise_id=%s AND id=ANY(%s)",
        (context.enterprise_id, scope_ids),
    ).fetchone()
    visual = connection.execute(
        "SELECT count(*) AS pages,"
        "count(*) FILTER (WHERE selected_route='NATIVE_REFERENCE') AS native,"
        "count(*) FILTER (WHERE selected_route='LOCAL_OCR') AS ocr "
        "FROM f0i.page WHERE enterprise_id=%s AND document_scope_id=ANY(%s)",
        (context.enterprise_id, scope_ids),
    ).fetchone()
    tree = connection.execute(
        "SELECT (SELECT count(*) FROM f0i.block WHERE enterprise_id=%s "
        "AND document_scope_id=ANY(%s)) AS blocks,"
        "(SELECT count(*) FROM f0i.chunk WHERE enterprise_id=%s "
        "AND document_scope_id=ANY(%s) AND chunk_level='PARENT') AS parents,"
        "(SELECT count(*) FROM f0i.chunk WHERE enterprise_id=%s "
        "AND document_scope_id=ANY(%s) AND chunk_level='CHILD') AS children,"
        "(SELECT count(*) FROM f0i.chunk_block_link WHERE enterprise_id=%s "
        "AND document_scope_id=ANY(%s)) AS links",
        (
            context.enterprise_id,
            scope_ids,
            context.enterprise_id,
            scope_ids,
            context.enterprise_id,
            scope_ids,
            context.enterprise_id,
            scope_ids,
        ),
    ).fetchone()
    if counts is None or visual is None or tree is None:
        raise F0IError("REPLAY_MISMATCH")
    if projection is None:
        run_counts = {
            "persisted_runs": 0,
            "persisted_smoke_ocr_calls": 0,
            "persisted_full_ocr_calls": 0,
        }
    else:
        selected_profiles = ["smoke"] if projection == "smoke" else ["smoke", "full"]
        run_row = connection.execute(
            "SELECT count(*) AS runs,"
            "COALESCE(sum(ocr_call_count) FILTER (WHERE profile='smoke'),0) "
            "AS smoke_ocr,"
            "COALESCE(sum(ocr_call_count) FILTER (WHERE profile='full'),0) "
            "AS full_ocr FROM f0i.run WHERE enterprise_id=%s "
            "AND configuration_id=%s AND configuration_sha256=%s "
            "AND profile=ANY(%s)",
            (
                context.enterprise_id,
                identity.configuration_id,
                identity.configuration_sha256,
                selected_profiles,
            ),
        ).fetchone()
        if run_row is None:
            raise F0IError("REPLAY_MISMATCH")
        run_counts = {
            "persisted_runs": int(run_row["runs"]),
            "persisted_smoke_ocr_calls": int(run_row["smoke_ocr"]),
            "persisted_full_ocr_calls": int(run_row["full_ocr"]),
        }
    _validate_encrypted_trees(connection, context, scope_ids, key)
    orphan_blocks, orphan_chunks, crosswires = _orphan_counts(
        connection, context, scope_ids
    )
    result = {
        "documents": int(counts["documents"]),
        "document_scopes": int(counts["documents"]),
        "visual_documents": int(counts["visual_documents"]),
        "structure_documents": int(counts["structure_documents"]),
        "deferred_documents": int(counts["deferred_documents"]),
        "visual_units": int(counts["visual_units"] or 0),
        "native_visual_units": int(visual["native"]),
        "ocr_visual_units": int(visual["ocr"]),
        "persisted_ocr_calls": int(visual["ocr"]),
        **run_counts,
        "structure_units": int(counts["structure_units"] or 0),
        "pages": int(visual["pages"]),
        "blocks": int(tree["blocks"]),
        "parent_chunks": int(tree["parents"]),
        "child_chunks": int(tree["children"]),
        "child_block_links": int(tree["links"]),
        "docx_sections": sum(
            1
            for row in scope_rows
            if str(row["document_type"]) == "DOCX"
        ),
        "docx_paragraphs": int(counts["docx_paragraphs"] or 0),
        "docx_tables": int(counts["docx_tables"] or 0),
        "docx_rows": int(counts["docx_rows"] or 0),
        "docx_table_cells": int(counts["docx_table_cells"] or 0),
        "xlsx_sheets": int(counts["xlsx_sheets"] or 0),
        "xlsx_cells": int(counts["xlsx_cells"] or 0),
        "xlsx_formula_cells": int(counts["xlsx_formula_cells"] or 0),
        "xlsx_formula_cached_values": int(
            counts["xlsx_formula_cached_values"] or 0
        ),
        "xlsx_value_cells": int(counts["xlsx_value_cells"] or 0),
        "negative_scopes": int(counts["negative_scopes"]),
        "negative_enabled_gates": int(counts["negative_enabled_gates"] or 0),
        "reconstruction_failures": 0,
        "tenant_version_crosswires": crosswires,
        "orphan_blocks": orphan_blocks,
        "orphan_chunks": orphan_chunks,
        "plaintext_leaks": _plaintext_column_leaks(connection),
    }
    if (
        result["pages"] != result["visual_units"]
        or result["native_visual_units"] + result["ocr_visual_units"]
        != result["visual_units"]
        or result["parent_chunks"]
        != result["visual_units"] + result["structure_units"]
        or any(
            result[key] != 0
            for key in (
                "negative_enabled_gates",
                "reconstruction_failures",
                "tenant_version_crosswires",
                "orphan_blocks",
                "orphan_chunks",
                "plaintext_leaks",
            )
        )
    ):
        raise F0IError("REPLAY_MISMATCH")
    return result


def validate_persisted_records(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    documents: Sequence[DocumentRecord],
) -> None:
    """Compare every stored provenance field with its verified in-memory draft.

    Decryption/reconstruction proves that ciphertext spans form the canonical
    body.  This second, independent check proves that the stored IDs, chains,
    ordinals, locations, hashes, and source bindings are exactly the records
    produced by the verified draft.  Callers run it in the same transaction as
    ``persist_run`` so any mismatch rolls back the immutable row set.
    """

    set_tenant_context(connection, context)
    seen_scopes: set[uuid.UUID] = set()
    for document in documents:
        source = document.source
        scope_id = _scope_id(context, identity, source)
        if scope_id in seen_scopes:
            raise F0IError("REPLAY_MISMATCH")
        seen_scopes.add(scope_id)
        scope = connection.execute(
            "SELECT id,enterprise_id,configuration_id,configuration_sha256,"
            "document_version_id,object_blob_id,source_object_sha256,"
            "source_object_size_bytes,processing_plan_id,source_document_id,"
            "source_plan_sha256,source_schema_version,source_rule_version,"
            "document_type,source_group,corpus_role,scope_kind,visual_unit_count,"
            "structure_unit_count,structure_summary_sha256,docx_paragraph_count,"
            "docx_table_count,docx_row_count,docx_cell_count,xlsx_sheet_count,"
            "xlsx_cell_count,xlsx_value_cell_count,xlsx_formula_count,"
            "xlsx_formula_cached_value_count,deferred_reason_code,"
            "enterprise_fact_allowed,current_regulation_allowed,"
            "search_publish_allowed,terminal_status FROM f0i.document_scope "
            "WHERE enterprise_id=%s AND configuration_id=%s "
            "AND configuration_sha256=%s AND id=%s",
            (
                context.enterprise_id,
                identity.configuration_id,
                identity.configuration_sha256,
                scope_id,
            ),
        ).fetchone()
        if scope is None or _scope_projection(scope) != _expected_scope_projection(
            context, identity, document, scope_id
        ):
            raise F0IError("REPLAY_MISMATCH")

        expected_pages = 0
        expected_blocks = 0
        expected_chunks = 0
        expected_links = 0
        seen_containers: set[uuid.UUID] = set()
        for unit in document.units:
            verify_reconstruction(unit.canonical)
            container_id, container_kind, page_id = _unit_container(
                context, identity, source, unit
            )
            if container_id in seen_containers:
                raise F0IError("REPLAY_MISMATCH")
            seen_containers.add(container_id)
            if page_id is not None:
                _validate_persisted_page(
                    connection,
                    context,
                    identity,
                    scope_id,
                    source,
                    unit,
                    page_id,
                )
                expected_pages += 1
            _validate_persisted_blocks(
                connection,
                context,
                identity,
                scope_id,
                source,
                unit,
                container_id,
                container_kind,
                page_id,
            )
            _validate_persisted_chunks(
                connection,
                context,
                identity,
                scope_id,
                source,
                unit,
                container_id,
                container_kind,
                page_id,
            )
            _validate_persisted_links(
                connection,
                context,
                identity,
                scope_id,
                source,
                unit,
                container_id,
            )
            expected_blocks += len(unit.canonical.blocks)
            expected_chunks += 1 + len(unit.canonical.children)
            expected_links += len(persisted_child_links(unit.canonical))

        totals = connection.execute(
            "SELECT "
            "(SELECT count(*) FROM f0i.page WHERE enterprise_id=%s "
            "AND document_scope_id=%s) AS pages,"
            "(SELECT count(*) FROM f0i.block WHERE enterprise_id=%s "
            "AND document_scope_id=%s) AS blocks,"
            "(SELECT count(*) FROM f0i.chunk WHERE enterprise_id=%s "
            "AND document_scope_id=%s) AS chunks,"
            "(SELECT count(*) FROM f0i.chunk_block_link WHERE enterprise_id=%s "
            "AND document_scope_id=%s) AS links",
            (
                context.enterprise_id,
                scope_id,
                context.enterprise_id,
                scope_id,
                context.enterprise_id,
                scope_id,
                context.enterprise_id,
                scope_id,
            ),
        ).fetchone()
        if totals is None or tuple(int(totals[key]) for key in (
            "pages", "blocks", "chunks", "links"
        )) != (expected_pages, expected_blocks, expected_chunks, expected_links):
            raise F0IError("REPLAY_MISMATCH")


def _scope_id(
    context: SessionContext,
    identity: ConfigurationIdentity,
    source: SourceDocument,
) -> uuid.UUID:
    return stable_uuid4(
        "f0i.document-scope.v1",
        context.enterprise_id,
        identity.configuration_id,
        source.document_version_id,
        source.processing_plan_id,
        source.source_plan_sha256,
    )


def _scope_projection(row: Mapping[str, object]) -> tuple[object, ...]:
    return (
        row["id"],
        row["enterprise_id"],
        row["configuration_id"],
        str(row["configuration_sha256"]),
        row["document_version_id"],
        row["object_blob_id"],
        str(row["source_object_sha256"]),
        int(row["source_object_size_bytes"]),
        row["processing_plan_id"],
        str(row["source_document_id"]),
        str(row["source_plan_sha256"]),
        str(row["source_schema_version"]),
        str(row["source_rule_version"]),
        str(row["document_type"]),
        str(row["source_group"]),
        str(row["corpus_role"]),
        str(row["scope_kind"]),
        int(row["visual_unit_count"]),
        int(row["structure_unit_count"]),
        _optional_text(row["structure_summary_sha256"]),
        _optional_int(row["docx_paragraph_count"]),
        _optional_int(row["docx_table_count"]),
        _optional_int(row["docx_row_count"]),
        _optional_int(row["docx_cell_count"]),
        _optional_int(row["xlsx_sheet_count"]),
        _optional_int(row["xlsx_cell_count"]),
        _optional_int(row["xlsx_value_cell_count"]),
        _optional_int(row["xlsx_formula_count"]),
        _optional_int(row["xlsx_formula_cached_value_count"]),
        _optional_text(row["deferred_reason_code"]),
        bool(row["enterprise_fact_allowed"]),
        bool(row["current_regulation_allowed"]),
        bool(row["search_publish_allowed"]),
        str(row["terminal_status"]),
    )


def _expected_scope_projection(
    context: SessionContext,
    identity: ConfigurationIdentity,
    document: DocumentRecord,
    scope_id: uuid.UUID,
) -> tuple[object, ...]:
    source = document.source
    return (
        scope_id,
        context.enterprise_id,
        identity.configuration_id,
        identity.configuration_sha256,
        source.document_version_id,
        source.object_blob_id,
        source.source_object_sha256,
        source.source_object_size_bytes,
        source.processing_plan_id,
        source.source_document_id,
        source.source_plan_sha256,
        source.source_schema_version,
        source.source_rule_version,
        source.document_type,
        source.source_group,
        source.corpus_role,
        document.scope_kind,
        source.visual_unit_count if document.scope_kind == "VISUAL" else 0,
        len(document.units) if document.scope_kind == "STRUCTURE" else 0,
        document.structure_summary_sha256,
        document.docx_paragraph_count,
        document.docx_table_count,
        document.docx_row_count,
        document.docx_cell_count,
        document.xlsx_sheet_count,
        document.xlsx_cell_count,
        document.xlsx_value_cell_count,
        document.xlsx_formula_count,
        document.xlsx_formula_cached_value_count,
        document.deferred_reason_code,
        source.enterprise_fact_allowed,
        source.current_regulation_allowed,
        source.search_publish_allowed,
        (
            "DEFERRED_CONVERSION_REQUIRED"
            if document.scope_kind == "DEFERRED"
            else "CANONICAL_SCOPE_INCLUDED"
        ),
    )


def _unit_container(
    context: SessionContext,
    identity: ConfigurationIdentity,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
) -> tuple[uuid.UUID, str, uuid.UUID | None]:
    page = unit.source_page
    if page is not None:
        page_id = stable_uuid4(
            "f0i.page.v1",
            context.enterprise_id,
            identity.configuration_id,
            page.processing_unit_id,
            page.source_unit_id,
            page.evidence_sha256,
        )
        return page_id, "PAGE", page_id
    if (
        unit.observation.structure_unit_sha256 is None
        or unit.structure_anchor_sha256 is None
    ):
        raise F0IError("REPLAY_MISMATCH")
    return (
        stable_uuid4(
            "f0i.structure-container.v1",
            context.enterprise_id,
            identity.configuration_id,
            source.document_version_id,
            unit.observation.structure_unit_sha256,
        ),
        unit.observation.unit_kind,
        None,
    )


def _validate_persisted_page(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    page_id: uuid.UUID,
) -> None:
    page = unit.source_page
    if page is None:
        raise F0IError("REPLAY_MISMATCH")
    row = connection.execute(
        "SELECT id,enterprise_id,configuration_id,configuration_sha256,"
        "document_scope_id,document_version_id,processing_plan_id,"
        "source_document_id,source_plan_sha256,source_processing_unit_id,"
        "source_unit_id,source_unit_ordinal,source_unit_kind,page_no,"
        "candidate_decision,selected_route,source_unit_evidence_sha256,"
        "native_text_identity_sha256,native_characters,"
        "source_page_output_sha256,source_page_evidence_sha256,rotation,"
        "media_left,media_bottom,media_right,media_top,crop_left,crop_bottom,"
        "crop_right,crop_top,width_px,height_px,ocr_render_width_px,"
        "ocr_render_height_px,ocr_render_dpi,ocr_render_origin,ocr_renderer_id,"
        "ocr_renderer_version,ocr_render_sha256,geometry_sha256 FROM f0i.page "
        "WHERE enterprise_id=%s AND document_scope_id=%s AND id=%s",
        (context.enterprise_id, scope_id, page_id),
    ).fetchone()
    geometry = unit.observation.page_geometry
    render = unit.ocr_render
    if geometry is None:
        media: tuple[object, ...] = (None, None, None, None)
        crop: tuple[object, ...] = (None, None, None, None)
        rotation = None
        geometry_sha256 = canonical_sha256(
            {
                "height_px": unit.observation.image_height_px,
                "source_unit_id": page.source_unit_id,
                "width_px": unit.observation.image_width_px,
            }
        )
    else:
        media = geometry.media_box
        crop = geometry.crop_box
        rotation = geometry.rotation
        geometry_sha256 = geometry.geometry_sha256
    expected = (
        page_id,
        context.enterprise_id,
        identity.configuration_id,
        identity.configuration_sha256,
        scope_id,
        source.document_version_id,
        source.processing_plan_id,
        source.source_document_id,
        source.source_plan_sha256,
        page.processing_unit_id,
        page.source_unit_id,
        page.unit_ordinal,
        page.unit_kind,
        page.page_no,
        page.candidate_decision,
        (
            "NATIVE_REFERENCE"
            if page.candidate_decision == "NATIVE_CANDIDATE"
            else "LOCAL_OCR"
        ),
        page.evidence_sha256,
        page.native_text_identity_sha256,
        page.native_characters,
        unit.observation.source_output_sha256,
        unit.observation.source_evidence_sha256,
        rotation,
        *media,
        *crop,
        unit.observation.image_width_px,
        unit.observation.image_height_px,
        render.width_px if render is not None else None,
        render.height_px if render is not None else None,
        render.dpi if render is not None else None,
        render.origin if render is not None else None,
        render.renderer_id if render is not None else None,
        render.renderer_version if render is not None else None,
        render.render_sha256 if render is not None else None,
        geometry_sha256,
    )
    if row is None or _page_projection(row) != expected:
        raise F0IError("REPLAY_MISMATCH")


def _page_projection(row: Mapping[str, object]) -> tuple[object, ...]:
    text_fields = {
        "configuration_sha256",
        "source_document_id",
        "source_plan_sha256",
        "source_unit_id",
        "source_unit_kind",
        "candidate_decision",
        "selected_route",
        "source_unit_evidence_sha256",
        "native_text_identity_sha256",
        "source_page_output_sha256",
        "source_page_evidence_sha256",
        "ocr_render_origin",
        "ocr_renderer_id",
        "ocr_renderer_version",
        "ocr_render_sha256",
        "geometry_sha256",
    }
    integer_fields = {
        "source_unit_ordinal",
        "page_no",
        "native_characters",
        "rotation",
        "width_px",
        "height_px",
        "ocr_render_width_px",
        "ocr_render_height_px",
        "ocr_render_dpi",
    }
    numeric_text_fields = {
        "media_left", "media_bottom", "media_right", "media_top",
        "crop_left", "crop_bottom", "crop_right", "crop_top",
    }
    fields = (
        "id", "enterprise_id", "configuration_id", "configuration_sha256",
        "document_scope_id", "document_version_id", "processing_plan_id",
        "source_document_id", "source_plan_sha256", "source_processing_unit_id",
        "source_unit_id", "source_unit_ordinal", "source_unit_kind", "page_no",
        "candidate_decision", "selected_route", "source_unit_evidence_sha256",
        "native_text_identity_sha256", "native_characters",
        "source_page_output_sha256", "source_page_evidence_sha256", "rotation",
        "media_left", "media_bottom", "media_right", "media_top", "crop_left",
        "crop_bottom", "crop_right", "crop_top", "width_px", "height_px",
        "ocr_render_width_px", "ocr_render_height_px", "ocr_render_dpi",
        "ocr_render_origin", "ocr_renderer_id", "ocr_renderer_version",
        "ocr_render_sha256", "geometry_sha256",
    )
    output: list[object] = []
    for field_name in fields:
        value = row[field_name]
        if value is None:
            output.append(None)
        elif field_name in text_fields:
            output.append(str(value))
        elif field_name in integer_fields:
            output.append(int(value))
        elif field_name in numeric_text_fields:
            output.append(str(value))
        else:
            output.append(value)
    return tuple(output)


def _validate_persisted_blocks(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
) -> None:
    rows = connection.execute(
        "SELECT id,enterprise_id,configuration_id,configuration_sha256,"
        "document_scope_id,document_version_id,processing_plan_id,"
        "source_document_id,source_plan_sha256,container_id,container_kind,"
        "page_id,source_processing_unit_id,source_unit_id,source_unit_ordinal,"
        "page_no,structure_unit_sha256,structure_anchor_sha256,block_ordinal,"
        "block_kind,evidence_method,source_route,location_kind,location_status,"
        "location_reason_code,location_sha256,bbox_ppm,coordinate_space,"
        "reading_order_status,confidence_ppm,structure_ordinal,"
        "docx_block_ordinal,docx_paragraph_ordinal,docx_table_ordinal,"
        "docx_row_ordinal,docx_cell_ordinal,xlsx_sheet_ordinal,xlsx_row_ordinal,"
        "xlsx_column_ordinal,table_evidence_status,"
        "canonical_body_plaintext_sha256,canonical_body_plaintext_size_bytes,"
        "canonical_body_plaintext_character_count,body_plaintext_sha256,"
        "body_plaintext_size_bytes,body_plaintext_character_count,"
        "previous_source_chain_sha256,source_chain_sha256,span_start_byte,"
        "span_end_byte,span_start_character,span_end_character FROM f0i.block "
        "WHERE enterprise_id=%s AND document_scope_id=%s AND container_id=%s "
        "ORDER BY block_ordinal",
        (context.enterprise_id, scope_id, container_id),
    ).fetchall()
    metadata = _block_metadata(unit.observation, unit.canonical)
    expected = tuple(
        _expected_block_projection(
            context,
            identity,
            scope_id,
            source,
            unit,
            container_id,
            container_kind,
            page_id,
            block,
            block_metadata,
        )
        for block, block_metadata in zip(
            unit.canonical.blocks, metadata, strict=True
        )
    )
    actual = tuple(_block_projection(row) for row in rows)
    if actual != expected:
        raise F0IError("REPLAY_MISMATCH")


def _expected_block_projection(
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
    block: object,
    metadata: Mapping[str, object],
) -> tuple[object, ...]:
    source_page = unit.source_page
    location = metadata["location"]
    if not isinstance(location, Mapping):
        raise F0IError("REPLAY_MISMATCH")
    span = block.span  # type: ignore[attr-defined]
    return (
        block.block_id,  # type: ignore[attr-defined]
        context.enterprise_id,
        identity.configuration_id,
        identity.configuration_sha256,
        scope_id,
        source.document_version_id,
        source.processing_plan_id,
        source.source_document_id,
        source.source_plan_sha256,
        container_id,
        container_kind,
        page_id,
        source_page.processing_unit_id if source_page is not None else None,
        source_page.source_unit_id if source_page is not None else None,
        source_page.unit_ordinal if source_page is not None else None,
        source_page.page_no if source_page is not None else None,
        unit.observation.structure_unit_sha256,
        unit.structure_anchor_sha256,
        block.ordinal,  # type: ignore[attr-defined]
        block.block_kind,  # type: ignore[attr-defined]
        metadata["evidence_method"],
        metadata["source_route"],
        location["location_kind"],
        location["location_status"],
        location["location_reason_code"],
        location["location_sha256"],
        _freeze_json(location.get("bbox_ppm")),
        location["coordinate_space"],
        location["reading_order_status"],
        metadata["confidence_ppm"],
        metadata["structure_ordinal"],
        metadata["docx_block_ordinal"],
        metadata["docx_paragraph_ordinal"],
        metadata["docx_table_ordinal"],
        metadata["docx_row_ordinal"],
        metadata["docx_cell_ordinal"],
        metadata["xlsx_sheet_ordinal"],
        metadata["xlsx_row_ordinal"],
        metadata["xlsx_column_ordinal"],
        metadata["table_evidence_status"],
        unit.canonical.body.sha256,
        unit.canonical.body.byte_count,
        unit.canonical.body.character_count,
        block.plaintext_sha256,  # type: ignore[attr-defined]
        block.plaintext_bytes,  # type: ignore[attr-defined]
        block.plaintext_characters,  # type: ignore[attr-defined]
        block.previous_chain_sha256,  # type: ignore[attr-defined]
        block.chain_sha256,  # type: ignore[attr-defined]
        span.start_byte,
        span.end_byte,
        span.start_character,
        span.end_character,
    )


def _block_projection(row: Mapping[str, object]) -> tuple[object, ...]:
    fields = (
        "id", "enterprise_id", "configuration_id", "configuration_sha256",
        "document_scope_id", "document_version_id", "processing_plan_id",
        "source_document_id", "source_plan_sha256", "container_id",
        "container_kind", "page_id", "source_processing_unit_id",
        "source_unit_id", "source_unit_ordinal", "page_no",
        "structure_unit_sha256", "structure_anchor_sha256", "block_ordinal",
        "block_kind", "evidence_method", "source_route", "location_kind",
        "location_status", "location_reason_code", "location_sha256", "bbox_ppm",
        "coordinate_space", "reading_order_status", "confidence_ppm",
        "structure_ordinal", "docx_block_ordinal", "docx_paragraph_ordinal",
        "docx_table_ordinal", "docx_row_ordinal", "docx_cell_ordinal",
        "xlsx_sheet_ordinal", "xlsx_row_ordinal", "xlsx_column_ordinal",
        "table_evidence_status", "canonical_body_plaintext_sha256",
        "canonical_body_plaintext_size_bytes",
        "canonical_body_plaintext_character_count", "body_plaintext_sha256",
        "body_plaintext_size_bytes", "body_plaintext_character_count",
        "previous_source_chain_sha256", "source_chain_sha256", "span_start_byte",
        "span_end_byte", "span_start_character", "span_end_character",
    )
    return tuple(_project_value(field_name, row[field_name]) for field_name in fields)


def _validate_persisted_chunks(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
) -> None:
    rows = connection.execute(
        "SELECT id,enterprise_id,configuration_id,configuration_sha256,"
        "document_scope_id,document_version_id,processing_plan_id,"
        "source_document_id,source_plan_sha256,container_id,container_kind,"
        "page_id,source_processing_unit_id,source_unit_id,source_unit_ordinal,"
        "page_no,structure_unit_sha256,structure_anchor_sha256,chunk_level,"
        "parent_chunk_id,chunk_ordinal,is_tail,overlap_characters,"
        "canonical_body_plaintext_sha256,canonical_body_plaintext_size_bytes,"
        "canonical_body_plaintext_character_count,body_plaintext_sha256,"
        "body_plaintext_size_bytes,body_plaintext_character_count,"
        "span_start_byte,span_end_byte,span_start_character,span_end_character,"
        "previous_source_chain_sha256,source_chain_sha256,unit_chain_sha256 "
        "FROM f0i.chunk WHERE enterprise_id=%s AND document_scope_id=%s "
        "AND container_id=%s ORDER BY CASE chunk_level WHEN 'PARENT' THEN 0 "
        "ELSE 1 END,chunk_ordinal",
        (context.enterprise_id, scope_id, container_id),
    ).fetchall()
    expected = tuple(
        _expected_chunk_projection(
            context,
            identity,
            scope_id,
            source,
            unit,
            container_id,
            container_kind,
            page_id,
            chunk,
        )
        for chunk in (unit.canonical.parent, *unit.canonical.children)
    )
    actual = tuple(_chunk_projection(row) for row in rows)
    if actual != expected:
        raise F0IError("REPLAY_MISMATCH")


def _expected_chunk_projection(
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
    chunk: object,
) -> tuple[object, ...]:
    source_page = unit.source_page
    span = chunk.span  # type: ignore[attr-defined]
    return (
        chunk.chunk_id,  # type: ignore[attr-defined]
        context.enterprise_id,
        identity.configuration_id,
        identity.configuration_sha256,
        scope_id,
        source.document_version_id,
        source.processing_plan_id,
        source.source_document_id,
        source.source_plan_sha256,
        container_id,
        container_kind,
        page_id,
        source_page.processing_unit_id if source_page is not None else None,
        source_page.source_unit_id if source_page is not None else None,
        source_page.unit_ordinal if source_page is not None else None,
        source_page.page_no if source_page is not None else None,
        unit.observation.structure_unit_sha256,
        unit.structure_anchor_sha256,
        chunk.chunk_level,  # type: ignore[attr-defined]
        chunk.parent_chunk_id,  # type: ignore[attr-defined]
        0 if chunk.chunk_level == "PARENT" else chunk.ordinal,  # type: ignore[attr-defined]
        chunk.is_tail,  # type: ignore[attr-defined]
        0,
        unit.canonical.body.sha256,
        unit.canonical.body.byte_count,
        unit.canonical.body.character_count,
        chunk.plaintext_sha256,  # type: ignore[attr-defined]
        chunk.plaintext_bytes,  # type: ignore[attr-defined]
        chunk.plaintext_characters,  # type: ignore[attr-defined]
        span.start_byte,
        span.end_byte,
        span.start_character,
        span.end_character,
        chunk.previous_chain_sha256,  # type: ignore[attr-defined]
        chunk.chain_sha256,  # type: ignore[attr-defined]
        unit.canonical.unit_chain_sha256,
    )


def _chunk_projection(row: Mapping[str, object]) -> tuple[object, ...]:
    fields = (
        "id", "enterprise_id", "configuration_id", "configuration_sha256",
        "document_scope_id", "document_version_id", "processing_plan_id",
        "source_document_id", "source_plan_sha256", "container_id",
        "container_kind", "page_id", "source_processing_unit_id",
        "source_unit_id", "source_unit_ordinal", "page_no",
        "structure_unit_sha256", "structure_anchor_sha256", "chunk_level",
        "parent_chunk_id", "chunk_ordinal", "is_tail", "overlap_characters",
        "canonical_body_plaintext_sha256", "canonical_body_plaintext_size_bytes",
        "canonical_body_plaintext_character_count", "body_plaintext_sha256",
        "body_plaintext_size_bytes", "body_plaintext_character_count",
        "span_start_byte", "span_end_byte", "span_start_character",
        "span_end_character", "previous_source_chain_sha256",
        "source_chain_sha256", "unit_chain_sha256",
    )
    return tuple(_project_value(field_name, row[field_name]) for field_name in fields)


def _validate_persisted_links(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
) -> None:
    rows = connection.execute(
        "SELECT id,enterprise_id,configuration_id,configuration_sha256,"
        "document_scope_id,document_version_id,processing_plan_id,container_id,"
        "chunk_id,block_id,link_ordinal,intersection_start_byte,"
        "intersection_end_byte,intersection_start_character,"
        "intersection_end_character,unit_chain_sha256 "
        "FROM f0i.chunk_block_link WHERE enterprise_id=%s "
        "AND document_scope_id=%s AND container_id=%s "
        "ORDER BY chunk_id,link_ordinal",
        (context.enterprise_id, scope_id, container_id),
    ).fetchall()
    links = sorted(
        persisted_child_links(unit.canonical),
        key=lambda item: (str(item.chunk_id), item.link_ordinal),
    )
    expected = tuple(
        (
            link.link_id,
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            scope_id,
            source.document_version_id,
            source.processing_plan_id,
            container_id,
            link.chunk_id,
            link.block_id,
            link.link_ordinal,
            link.intersection_span.start_byte,
            link.intersection_span.end_byte,
            link.intersection_span.start_character,
            link.intersection_span.end_character,
            unit.canonical.unit_chain_sha256,
        )
        for link in links
    )
    fields = (
        "id", "enterprise_id", "configuration_id", "configuration_sha256",
        "document_scope_id", "document_version_id", "processing_plan_id",
        "container_id", "chunk_id", "block_id", "link_ordinal",
        "intersection_start_byte", "intersection_end_byte",
        "intersection_start_character", "intersection_end_character",
        "unit_chain_sha256",
    )
    actual = tuple(
        tuple(_project_value(field_name, row[field_name]) for field_name in fields)
        for row in rows
    )
    if actual != expected:
        raise F0IError("REPLAY_MISMATCH")


def _project_value(field_name: str, value: object) -> object:
    if value is None:
        return None
    if field_name == "bbox_ppm":
        return _freeze_json(value)
    if isinstance(value, (uuid.UUID, bool)):
        return value
    if isinstance(value, int):
        return int(value)
    return str(value)


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_json(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _ensure_configuration(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    bundle: RuntimeBundle,
    key: memoryview,
) -> bool:
    if inspect_configuration(connection, context, identity, bundle, key):
        return False
    verifier = bytearray(identity.configuration_id.bytes)
    try:
        verifier_sha = hashlib.sha256(verifier).hexdigest()
        row = connection.execute(
            "WITH encrypted AS (SELECT f0f_crypto.pgp_sym_encrypt_bytea("
            "%s::bytea,encode(%s::bytea,'hex'),%s) AS value) "
            "INSERT INTO f0i.configuration(id,enterprise_id,actor_id,"
            "parser_rule_version,chunk_rule_version,location_rule_version,"
            "ocr_configuration_sha256,ocr_model_bundle_sha256,"
            "ocr_execution_profile_sha256,ocr_runtime_image_id,"
            "ocr_runtime_lock_sha256,ocr_output_contract_sha256,"
            "registered_full_plan_sha256,key_fingerprint_sha256,"
            "key_verifier_plaintext_sha256,key_verifier_ciphertext,"
            "key_verifier_ciphertext_sha256) SELECT %s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s,%s,encrypted.value,"
            "encode(f0f_crypto.digest(encrypted.value,'sha256'),'hex') FROM encrypted "
            "RETURNING configuration_sha256",
            (
                verifier,
                key,
                _CIPHER_OPTIONS,
                identity.configuration_id,
                context.enterprise_id,
                context.actor_id,
                _PARSER_RULE_VERSION,
                CHUNK_RULE,
                _LOCATION_RULE_VERSION,
                bundle.configuration_sha256,
                bundle.model_bundle_sha256,
                bundle.execution_profile_sha256,
                bundle.container_image_id,
                bundle.lock_sha256,
                _OCR_OUTPUT_CONTRACT_SHA256,
                identity.full_plan_sha256,
                identity.key_fingerprint_sha256,
                verifier_sha,
            ),
        ).fetchone()
        if row is None or str(row["configuration_sha256"]) != identity.configuration_sha256:
            raise F0IError("PERSISTENCE_FAILED")
        return True
    finally:
        _wipe(verifier)


def _insert_document(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    key: memoryview,
    run_id: uuid.UUID,
    run_identity: str,
    document: DocumentRecord,
) -> int:
    source = document.source
    scope_id = _scope_id(context, identity, source)
    terminal = (
        "DEFERRED_CONVERSION_REQUIRED"
        if document.scope_kind == "DEFERRED"
        else "CANONICAL_SCOPE_INCLUDED"
    )
    connection.execute(
        "INSERT INTO f0i.document_scope(id,enterprise_id,configuration_id,"
        "configuration_sha256,first_run_id,first_run_identity_sha256,"
        "document_version_id,object_blob_id,source_object_sha256,"
        "source_object_size_bytes,processing_plan_id,source_document_id,"
        "source_plan_sha256,source_schema_version,source_rule_version,"
        "document_type,source_group,corpus_role,scope_kind,visual_unit_count,"
        "structure_unit_count,structure_summary_sha256,docx_paragraph_count,"
        "docx_table_count,docx_row_count,docx_cell_count,xlsx_sheet_count,"
        "xlsx_cell_count,xlsx_value_cell_count,xlsx_formula_count,"
        "xlsx_formula_cached_value_count,deferred_reason_code,"
        "enterprise_fact_allowed,current_regulation_allowed,"
        "search_publish_allowed,terminal_status) VALUES ("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            scope_id,
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            run_id,
            run_identity,
            source.document_version_id,
            source.object_blob_id,
            source.source_object_sha256,
            source.source_object_size_bytes,
            source.processing_plan_id,
            source.source_document_id,
            source.source_plan_sha256,
            source.source_schema_version,
            source.source_rule_version,
            source.document_type,
            source.source_group,
            source.corpus_role,
            document.scope_kind,
            source.visual_unit_count if document.scope_kind == "VISUAL" else 0,
            len(document.units) if document.scope_kind == "STRUCTURE" else 0,
            document.structure_summary_sha256,
            document.docx_paragraph_count,
            document.docx_table_count,
            document.docx_row_count,
            document.docx_cell_count,
            document.xlsx_sheet_count,
            document.xlsx_cell_count,
            document.xlsx_value_cell_count,
            document.xlsx_formula_count,
            document.xlsx_formula_cached_value_count,
            document.deferred_reason_code,
            source.enterprise_fact_allowed,
            source.current_regulation_allowed,
            source.search_publish_allowed,
            terminal,
        ),
    )
    inserted = 1
    for unit in document.units:
        inserted += _insert_unit(
            connection,
            context,
            identity,
            key,
            run_id,
            scope_id,
            document,
            unit,
        )
    return inserted


def _insert_unit(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    key: memoryview,
    run_id: uuid.UUID,
    scope_id: uuid.UUID,
    document: DocumentRecord,
    unit: CanonicalUnitRecord,
) -> int:
    source = document.source
    observation = unit.observation
    canonical = unit.canonical
    visual = unit.source_page is not None
    if visual:
        page = unit.source_page
        if page is None:
            raise F0IError("PERSISTENCE_FAILED")
        page_id = stable_uuid4(
            "f0i.page.v1",
            context.enterprise_id,
            identity.configuration_id,
            page.processing_unit_id,
            page.source_unit_id,
            page.evidence_sha256,
        )
        _insert_page(
            connection,
            context,
            identity,
            run_id,
            scope_id,
            source,
            page,
            page_id,
            observation,
            unit.ocr_render,
        )
        container_id = page_id
        container_kind = "PAGE"
    else:
        if observation.structure_unit_sha256 is None or unit.structure_anchor_sha256 is None:
            raise F0IError("PERSISTENCE_FAILED")
        page_id = None
        container_id = stable_uuid4(
            "f0i.structure-container.v1",
            context.enterprise_id,
            identity.configuration_id,
            source.document_version_id,
            observation.structure_unit_sha256,
        )
        container_kind = observation.unit_kind

    metadata = _block_metadata(observation, canonical)
    for block, block_meta in zip(canonical.blocks, metadata, strict=True):
        location = block_meta["location"]
        material = canonical.body.slice(block.span.start_byte, block.span.end_byte)
        try:
            _insert_block(
                connection,
                context,
                identity,
                key,
                run_id,
                scope_id,
                source,
                unit,
                container_id,
                container_kind,
                page_id,
                block,
                location,
                block_meta,
                material,
            )
        finally:
            material.release()
    chunks = (canonical.parent, *canonical.children)
    for chunk in chunks:
        material = canonical.body.slice(chunk.span.start_byte, chunk.span.end_byte)
        try:
            _insert_chunk(
                connection,
                context,
                identity,
                key,
                run_id,
                scope_id,
                source,
                unit,
                container_id,
                container_kind,
                page_id,
                chunk,
                canonical,
                material,
            )
        finally:
            material.release()
    child_links = persisted_child_links(canonical)
    for link in child_links:
        connection.execute(
            "INSERT INTO f0i.chunk_block_link(id,enterprise_id,configuration_id,"
            "configuration_sha256,document_scope_id,first_run_id,"
            "document_version_id,processing_plan_id,container_id,chunk_id,"
            "block_id,link_ordinal,intersection_start_byte,intersection_end_byte,"
            "intersection_start_character,intersection_end_character,"
            "unit_chain_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,%s,%s,%s,%s,%s)",
            (
                link.link_id,
                context.enterprise_id,
                identity.configuration_id,
                identity.configuration_sha256,
                scope_id,
                run_id,
                source.document_version_id,
                source.processing_plan_id,
                container_id,
                link.chunk_id,
                link.block_id,
                link.link_ordinal,
                link.intersection_span.start_byte,
                link.intersection_span.end_byte,
                link.intersection_span.start_character,
                link.intersection_span.end_character,
                canonical.unit_chain_sha256,
            ),
        )
    return 1 + len(canonical.blocks) + len(chunks) + len(child_links) if visual else len(
        canonical.blocks
    ) + len(chunks) + len(child_links)


def persisted_child_links(
    canonical: CanonicalUnitDraft,
) -> tuple[ChunkBlockLinkDraft, ...]:
    """Project only schema-valid child links from the richer core graph.

    The in-memory graph records a point intersection for every zero-width leaf
    contained by a child.  F0-I's database contract deliberately persists such
    a zero intersection only when *both* the child and block bodies are empty;
    otherwise the leaf remains reconstructable by its ordered span but is not
    misrepresented as contributing body bytes to that child.
    """

    child_by_id = {child.chunk_id: child for child in canonical.children}
    block_by_id = {block.block_id: block for block in canonical.blocks}
    output: list[ChunkBlockLinkDraft] = []
    for link in canonical.links:
        child = child_by_id.get(link.chunk_id)
        block = block_by_id.get(link.block_id)
        if child is None or block is None:
            continue
        intersection = link.intersection_span
        positive = (
            intersection.byte_count > 0 and intersection.character_count > 0
        )
        both_empty = (
            child.plaintext_bytes == 0
            and child.plaintext_characters == 0
            and block.plaintext_bytes == 0
            and block.plaintext_characters == 0
        )
        if positive or both_empty:
            output.append(link)
    return tuple(output)


def _insert_page(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    run_id: uuid.UUID,
    scope_id: uuid.UUID,
    source: SourceDocument,
    page: SourcePage,
    page_id: uuid.UUID,
    observation: UnitObservation,
    render: OcrRenderEvidence | None,
) -> None:
    pdf = observation.page_geometry
    geometry_sha = (
        pdf.geometry_sha256
        if pdf is not None
        else canonical_sha256(
            {
                "height_px": observation.image_height_px,
                "source_unit_id": page.source_unit_id,
                "width_px": observation.image_width_px,
            }
        )
    )
    media = pdf.media_box if pdf is not None else (None, None, None, None)
    crop = pdf.crop_box if pdf is not None else (None, None, None, None)
    selected_route = (
        "NATIVE_REFERENCE"
        if page.candidate_decision == "NATIVE_CANDIDATE"
        else "LOCAL_OCR"
    )
    if (selected_route == "LOCAL_OCR") != (render is not None):
        raise F0IError("PERSISTENCE_FAILED")
    connection.execute(
        "INSERT INTO f0i.page(id,enterprise_id,configuration_id,"
        "configuration_sha256,document_scope_id,first_run_id,document_version_id,"
        "processing_plan_id,source_document_id,source_plan_sha256,"
        "source_processing_unit_id,source_unit_id,source_unit_ordinal,"
        "source_unit_kind,page_no,candidate_decision,selected_route,"
        "source_unit_evidence_sha256,native_text_identity_sha256,"
        "native_characters,source_page_output_sha256,"
        "source_page_evidence_sha256,rotation,media_left,media_bottom,media_right,"
        "media_top,crop_left,crop_bottom,crop_right,crop_top,width_px,height_px,"
        "ocr_render_width_px,ocr_render_height_px,ocr_render_dpi,ocr_render_origin,"
        "ocr_renderer_id,ocr_renderer_version,ocr_render_sha256,geometry_sha256) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            page_id,
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            scope_id,
            run_id,
            source.document_version_id,
            source.processing_plan_id,
            source.source_document_id,
            source.source_plan_sha256,
            page.processing_unit_id,
            page.source_unit_id,
            page.unit_ordinal,
            page.unit_kind,
            page.page_no,
            page.candidate_decision,
            selected_route,
            page.evidence_sha256,
            page.native_text_identity_sha256,
            page.native_characters,
            observation.source_output_sha256,
            observation.source_evidence_sha256,
            pdf.rotation if pdf is not None else None,
            *media,
            *crop,
            observation.image_width_px,
            observation.image_height_px,
            render.width_px if render is not None else None,
            render.height_px if render is not None else None,
            render.dpi if render is not None else None,
            render.origin if render is not None else None,
            render.renderer_id if render is not None else None,
            render.renderer_version if render is not None else None,
            render.render_sha256 if render is not None else None,
            geometry_sha,
        ),
    )


def _insert_block(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    key: memoryview,
    run_id: uuid.UUID,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
    block: object,
    location: Mapping[str, object],
    metadata: Mapping[str, object],
    material: memoryview,
) -> None:
    canonical = unit.canonical
    observation = unit.observation
    source_page = unit.source_page
    span = block.span  # type: ignore[attr-defined]
    connection.execute(
        "WITH encrypted AS (SELECT f0f_crypto.pgp_sym_encrypt_bytea("
        "%s::bytea,encode(%s::bytea,'hex'),%s) AS value) "
        "INSERT INTO f0i.block(id,enterprise_id,configuration_id,"
        "configuration_sha256,document_scope_id,first_run_id,document_version_id,"
        "processing_plan_id,source_document_id,source_plan_sha256,"
        "source_document_type,container_id,container_kind,page_id,"
        "source_processing_unit_id,source_unit_id,source_unit_ordinal,page_no,"
        "structure_unit_sha256,structure_anchor_sha256,block_ordinal,block_kind,"
        "evidence_method,source_route,location_kind,location_status,"
        "location_reason_code,location_sha256,bbox_ppm,coordinate_space,"
        "reading_order_status,confidence_ppm,structure_ordinal,docx_block_ordinal,"
        "docx_paragraph_ordinal,docx_table_ordinal,docx_row_ordinal,"
        "docx_cell_ordinal,xlsx_sheet_ordinal,xlsx_row_ordinal,"
        "xlsx_column_ordinal,table_evidence_status,"
        "canonical_body_plaintext_sha256,canonical_body_plaintext_size_bytes,"
        "canonical_body_plaintext_character_count,body_plaintext_sha256,"
        "body_plaintext_size_bytes,body_plaintext_character_count,body_ciphertext,"
        "body_ciphertext_sha256,previous_source_chain_sha256,source_chain_sha256,"
        "span_start_byte,span_end_byte,span_start_character,span_end_character) "
        "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,encrypted.value,"
        "encode(f0f_crypto.digest(encrypted.value,'sha256'),'hex'),%s,%s,%s,%s,%s,%s "
        "FROM encrypted",
        (
            material,
            key,
            _CIPHER_OPTIONS,
            block.block_id,  # type: ignore[attr-defined]
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            scope_id,
            run_id,
            source.document_version_id,
            source.processing_plan_id,
            source.source_document_id,
            source.source_plan_sha256,
            source.document_type,
            container_id,
            container_kind,
            page_id,
            source_page.processing_unit_id if source_page is not None else None,
            source_page.source_unit_id if source_page is not None else None,
            source_page.unit_ordinal if source_page is not None else None,
            source_page.page_no if source_page is not None else None,
            observation.structure_unit_sha256,
            unit.structure_anchor_sha256,
            block.ordinal,  # type: ignore[attr-defined]
            block.block_kind,  # type: ignore[attr-defined]
            metadata["evidence_method"],
            metadata["source_route"],
            location["location_kind"],
            location["location_status"],
            location["location_reason_code"],
            location["location_sha256"],
            metadata["bbox_json"],
            location["coordinate_space"],
            location["reading_order_status"],
            metadata["confidence_ppm"],
            metadata["structure_ordinal"],
            metadata["docx_block_ordinal"],
            metadata["docx_paragraph_ordinal"],
            metadata["docx_table_ordinal"],
            metadata["docx_row_ordinal"],
            metadata["docx_cell_ordinal"],
            metadata["xlsx_sheet_ordinal"],
            metadata["xlsx_row_ordinal"],
            metadata["xlsx_column_ordinal"],
            metadata["table_evidence_status"],
            canonical.body.sha256,
            canonical.body.byte_count,
            canonical.body.character_count,
            block.plaintext_sha256,  # type: ignore[attr-defined]
            block.plaintext_bytes,  # type: ignore[attr-defined]
            block.plaintext_characters,  # type: ignore[attr-defined]
            block.previous_chain_sha256,  # type: ignore[attr-defined]
            block.chain_sha256,  # type: ignore[attr-defined]
            span.start_byte,
            span.end_byte,
            span.start_character,
            span.end_character,
        ),
    )


def _insert_chunk(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    identity: ConfigurationIdentity,
    key: memoryview,
    run_id: uuid.UUID,
    scope_id: uuid.UUID,
    source: SourceDocument,
    unit: CanonicalUnitRecord,
    container_id: uuid.UUID,
    container_kind: str,
    page_id: uuid.UUID | None,
    chunk: object,
    canonical: CanonicalUnitDraft,
    material: memoryview,
) -> None:
    source_page = unit.source_page
    span = chunk.span  # type: ignore[attr-defined]
    connection.execute(
        "WITH encrypted AS (SELECT f0f_crypto.pgp_sym_encrypt_bytea("
        "%s::bytea,encode(%s::bytea,'hex'),%s) AS value) "
        "INSERT INTO f0i.chunk(id,enterprise_id,configuration_id,"
        "configuration_sha256,document_scope_id,first_run_id,document_version_id,"
        "processing_plan_id,source_document_id,source_plan_sha256,container_id,"
        "container_kind,page_id,source_processing_unit_id,source_unit_id,"
        "source_unit_ordinal,page_no,structure_unit_sha256,structure_anchor_sha256,"
        "chunk_level,parent_chunk_id,chunk_ordinal,is_tail,overlap_characters,"
        "canonical_body_plaintext_sha256,canonical_body_plaintext_size_bytes,"
        "canonical_body_plaintext_character_count,body_plaintext_sha256,"
        "body_plaintext_size_bytes,body_plaintext_character_count,body_ciphertext,"
        "body_ciphertext_sha256,span_start_byte,span_end_byte,span_start_character,"
        "span_end_character,previous_source_chain_sha256,source_chain_sha256,"
        "unit_chain_sha256) SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s,encrypted.value,"
        "encode(f0f_crypto.digest(encrypted.value,'sha256'),'hex'),%s,%s,%s,%s,%s,"
        "%s,%s FROM encrypted",
        (
            material,
            key,
            _CIPHER_OPTIONS,
            chunk.chunk_id,  # type: ignore[attr-defined]
            context.enterprise_id,
            identity.configuration_id,
            identity.configuration_sha256,
            scope_id,
            run_id,
            source.document_version_id,
            source.processing_plan_id,
            source.source_document_id,
            source.source_plan_sha256,
            container_id,
            container_kind,
            page_id,
            source_page.processing_unit_id if source_page is not None else None,
            source_page.source_unit_id if source_page is not None else None,
            source_page.unit_ordinal if source_page is not None else None,
            source_page.page_no if source_page is not None else None,
            unit.observation.structure_unit_sha256,
            unit.structure_anchor_sha256,
            chunk.chunk_level,  # type: ignore[attr-defined]
            chunk.parent_chunk_id,  # type: ignore[attr-defined]
            # The database contract reserves ordinal zero for the one parent.
            # Keep the core parent ID/chain untouched; normalize only the
            # stored ordinal at this boundary so a future draft default cannot
            # silently violate the schema.
            0 if chunk.chunk_level == "PARENT" else chunk.ordinal,  # type: ignore[attr-defined]
            chunk.is_tail,  # type: ignore[attr-defined]
            canonical.body.sha256,
            canonical.body.byte_count,
            canonical.body.character_count,
            chunk.plaintext_sha256,  # type: ignore[attr-defined]
            chunk.plaintext_bytes,  # type: ignore[attr-defined]
            chunk.plaintext_characters,  # type: ignore[attr-defined]
            span.start_byte,
            span.end_byte,
            span.start_character,
            span.end_character,
            chunk.previous_chain_sha256,  # type: ignore[attr-defined]
            chunk.chain_sha256,  # type: ignore[attr-defined]
            canonical.unit_chain_sha256,
        ),
    )


def _block_metadata(
    observation: UnitObservation, canonical: CanonicalUnitDraft
) -> tuple[dict[str, object], ...]:
    observed = iter(observation.leaves)
    output: list[dict[str, object]] = []
    for block in canonical.blocks:
        if block.block_kind == "CANONICAL_SEPARATOR":
            output.append(
                {
                    "location": {
                        "location_kind": "SYNTHETIC_SEPARATOR",
                        "location_status": "SYNTHETIC",
                        "location_reason_code": "CANONICAL_JOIN_SEPARATOR",
                        "location_sha256": block.location_sha256,
                        "coordinate_space": "SYNTHETIC",
                        "reading_order_status": "SYNTHETIC",
                    },
                    "bbox_json": None,
                    "confidence_ppm": None,
                    "evidence_method": "CANONICAL_JOIN",
                    "source_route": _source_route(observation),
                    "structure_ordinal": None,
                    "docx_block_ordinal": None,
                    "docx_paragraph_ordinal": None,
                    "docx_table_ordinal": None,
                    "docx_row_ordinal": None,
                    "docx_cell_ordinal": None,
                    "xlsx_sheet_ordinal": None,
                    "xlsx_row_ordinal": None,
                    "xlsx_column_ordinal": None,
                    "table_evidence_status": _table_status(observation, block.block_kind),
                }
            )
            continue
        try:
            leaf = next(observed)
        except StopIteration:
            raise F0IError("CANONICAL_RECONSTRUCTION_FAILED") from None
        location = leaf.location_record
        if leaf.docx_location is not None or leaf.xlsx_location is not None:
            location = {
                **location,
                "bbox_ppm": None,
                "coordinate_space": "SOURCE_STRUCTURE",
                "reading_order_status": "SOURCE_STRUCTURE_ORDER",
            }
        output.append(
            {
                "location": location,
                "bbox_json": (
                    canonical_json_bbox(location.get("bbox_ppm"))
                    if location.get("bbox_ppm") is not None
                    else None
                ),
                "confidence_ppm": leaf.confidence_ppm,
                "evidence_method": _evidence_method(observation),
                "source_route": _source_route(observation),
                "structure_ordinal": location.get("structure_ordinal"),
                "docx_block_ordinal": location.get("docx_block_ordinal"),
                "docx_paragraph_ordinal": location.get("docx_paragraph_ordinal"),
                "docx_table_ordinal": location.get("docx_table_ordinal"),
                "docx_row_ordinal": location.get("docx_row_ordinal"),
                "docx_cell_ordinal": location.get("docx_cell_ordinal"),
                "xlsx_sheet_ordinal": location.get("xlsx_sheet_ordinal"),
                "xlsx_row_ordinal": location.get("xlsx_row_ordinal"),
                "xlsx_column_ordinal": location.get("xlsx_column_ordinal"),
                "table_evidence_status": _table_status(observation, block.block_kind),
            }
        )
    try:
        next(observed)
    except StopIteration:
        return tuple(output)
    raise F0IError("CANONICAL_RECONSTRUCTION_FAILED")


def canonical_json_bbox(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _evidence_method(observation: UnitObservation) -> str:
    return {
        "PDF_PAGE": (
            "PYPDF_NATIVE"
            if observation.leaves[0].leaf.block_kind == "NATIVE_PAGE_TEXT"
            else "PP_OCRV6_SMALL"
        ),
        "JPEG_IMAGE": "PP_OCRV6_SMALL",
        "DOCX_SECTION": "DOCX_XML",
        "XLSX_SHEET": "XLSX_CELL_XML",
    }[observation.unit_kind]


def _source_route(observation: UnitObservation) -> str:
    return {
        "PDF_PAGE": (
            "NATIVE_REFERENCE"
            if observation.leaves[0].leaf.block_kind == "NATIVE_PAGE_TEXT"
            else "LOCAL_OCR"
        ),
        "JPEG_IMAGE": "LOCAL_OCR",
        "DOCX_SECTION": "DOCX_XML",
        "XLSX_SHEET": "XLSX_CELL_XML",
    }[observation.unit_kind]


def _table_status(observation: UnitObservation, block_kind: str) -> str:
    if observation.unit_kind == "PDF_PAGE":
        return "UNRESOLVED"
    if observation.unit_kind == "JPEG_IMAGE":
        return "NOT_APPLICABLE"
    if observation.unit_kind == "DOCX_SECTION":
        return "OBSERVED_DOCX_XML" if block_kind == "DOCX_TABLE_CELL" else "NOT_APPLICABLE"
    return (
        "NOT_APPLICABLE"
        if block_kind == "CANONICAL_SEPARATOR"
        else "OBSERVED_XLSX_CELL_XML"
    )


def _validate_source_pages(
    entry: Mapping[str, object], pages: tuple[SourcePage, ...], expected_count: int
) -> None:
    raw = entry.get("pages")
    document_type = entry.get("type")
    if document_type not in {"PDF", "JPEG"}:
        if pages or expected_count != 0:
            raise F0IError("REPLAY_MISMATCH")
        return
    if not isinstance(raw, list) or len(raw) != expected_count or len(pages) != len(raw):
        raise F0IError("REPLAY_MISMATCH")
    for source, planned in zip(pages, raw, strict=True):
        if not isinstance(planned, dict):
            raise F0IError("REPLAY_MISMATCH")
        native_hash = planned.get("native_text_sha256") or "0" * 64
        native_characters = planned.get("native_characters")
        if document_type == "JPEG" and native_characters is None:
            native_characters = 0
        if (
            source.source_unit_id != str(planned.get("page_id"))
            or source.page_no != planned.get("page_no")
            or source.candidate_decision != planned.get("decision")
            or source.native_characters != native_characters
            or source.native_text_identity_sha256 != native_hash
            or source.unit_kind != ("IMAGE" if document_type == "JPEG" else "PAGE")
        ):
            raise F0IError("REPLAY_MISMATCH")


def _configuration_values(
    bundle: RuntimeBundle,
    *,
    full_plan_sha256: str,
    key_fingerprint_sha256: str,
    verifier_plaintext_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": "f0i-canonical-chunks-v0.1",
        "normalization_rule": "UTF8_NFC_LF_V1",
        "parser_rule_version": _PARSER_RULE_VERSION,
        "chunk_rule_version": CHUNK_RULE,
        "location_rule_version": _LOCATION_RULE_VERSION,
        "pypdf_version": "6.14.2",
        "pypdf_license_expression": "BSD-3-Clause",
        "ocr_model_id": "PP-OCRv6-small",
        "ocr_family": "PP-OCRv6",
        "rapidocr_version": "3.9.2",
        "ocr_model_status": "NOT_EVALUATED",
        "ocr_configuration_sha256": bundle.configuration_sha256,
        "ocr_model_bundle_sha256": bundle.model_bundle_sha256,
        "ocr_execution_profile_sha256": bundle.execution_profile_sha256,
        "ocr_runtime_image_id": bundle.container_image_id,
        "ocr_runtime_lock_sha256": bundle.lock_sha256,
        "ocr_output_contract_sha256": _OCR_OUTPUT_CONTRACT_SHA256,
        "registered_full_plan_sha256": full_plan_sha256,
        "cipher_profile": "PGP_SYM_AES256_V1",
        "key_source": "LOCAL_FIXTURE_FILE_0600",
        "key_fingerprint_sha256": key_fingerprint_sha256,
        "verifier_plaintext_sha256": verifier_plaintext_sha256,
        "child_min_characters": 300,
        "child_max_characters": 800,
        "child_overlap_characters": 0,
        "maximum_body_bytes": 4 * 1024 * 1024,
        "benchmark_tier": "NONE",
        "external_processing_policy": "DENY",
        "search_status": "SEARCH_NOT_READY",
        "production_allowed": False,
        "raw_plaintext_columns_allowed": False,
    }


def _configuration_hash_values(values: Mapping[str, object]) -> tuple[object, ...]:
    return tuple(
        values[key]
        for key in (
            "schema_version",
            "normalization_rule",
            "parser_rule_version",
            "chunk_rule_version",
            "location_rule_version",
            "pypdf_version",
            "pypdf_license_expression",
            "ocr_model_id",
            "ocr_family",
            "rapidocr_version",
            "ocr_model_status",
            "ocr_configuration_sha256",
            "ocr_model_bundle_sha256",
            "ocr_execution_profile_sha256",
            "ocr_runtime_image_id",
            "ocr_runtime_lock_sha256",
            "ocr_output_contract_sha256",
            "registered_full_plan_sha256",
            "cipher_profile",
            "key_source",
            "key_fingerprint_sha256",
            "verifier_plaintext_sha256",
            "child_min_characters",
            "child_max_characters",
            "child_overlap_characters",
            "maximum_body_bytes",
            "benchmark_tier",
            "external_processing_policy",
            "search_status",
            "production_allowed",
            "raw_plaintext_columns_allowed",
        )
    )


def _sql_chain(values: Sequence[object | None]) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            parts.append("<NULL>")
        elif isinstance(value, bool):
            parts.append("true" if value else "false")
        else:
            parts.append(str(value))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _validate_encrypted_trees(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    scope_ids: Sequence[uuid.UUID],
    key: memoryview,
) -> None:
    containers = connection.execute(
        "SELECT container_id FROM f0i.chunk WHERE enterprise_id=%s "
        "AND document_scope_id=ANY(%s) AND chunk_level='PARENT' "
        "ORDER BY container_id",
        (context.enterprise_id, list(scope_ids)),
    ).fetchall()
    for container in containers:
        container_id = container["container_id"]
        blocks = connection.execute(
            "SELECT block_ordinal,body_plaintext_sha256,body_plaintext_size_bytes,"
            "body_plaintext_character_count,span_start_byte,span_end_byte,"
            "span_start_character,span_end_character,"
            "canonical_body_plaintext_sha256,"
            "f0f_crypto.pgp_sym_decrypt_bytea(body_ciphertext,"
            "encode(%s::bytea,'hex'),%s) AS body FROM f0i.block "
            "WHERE enterprise_id=%s AND container_id=%s ORDER BY block_ordinal",
            (key, _CIPHER_OPTIONS, context.enterprise_id, container_id),
        ).fetchall()
        chunks = connection.execute(
            "SELECT chunk_level,chunk_ordinal,body_plaintext_sha256,"
            "body_plaintext_size_bytes,body_plaintext_character_count,"
            "span_start_byte,span_end_byte,span_start_character,span_end_character,"
            "canonical_body_plaintext_sha256,"
            "f0f_crypto.pgp_sym_decrypt_bytea(body_ciphertext,"
            "encode(%s::bytea,'hex'),%s) AS body FROM f0i.chunk "
            "WHERE enterprise_id=%s AND container_id=%s "
            "ORDER BY CASE chunk_level WHEN 'PARENT' THEN 0 ELSE 1 END,chunk_ordinal",
            (key, _CIPHER_OPTIONS, context.enterprise_id, container_id),
        ).fetchall()
        if not blocks or not chunks or str(chunks[0]["chunk_level"]) != "PARENT":
            raise F0IError("REPLAY_MISMATCH")
        leaf_body = _reconstruct_rows(blocks, require_zero_start=True)
        child_body = _reconstruct_rows(chunks[1:], require_zero_start=True)
        parent = bytearray(chunks[0]["body"])  # type: ignore[arg-type]
        try:
            expected_sha = str(chunks[0]["canonical_body_plaintext_sha256"])
            if (
                bytes(leaf_body) != bytes(parent)
                or bytes(child_body) != bytes(parent)
                or hashlib.sha256(parent).hexdigest() != expected_sha
            ):
                raise F0IError("REPLAY_MISMATCH")
        finally:
            _wipe(leaf_body)
            _wipe(child_body)
            _wipe(parent)


def _reconstruct_rows(
    rows: Sequence[Mapping[str, object]], *, require_zero_start: bool
) -> bytearray:
    output = bytearray()
    expected_byte = 0
    expected_character = 0
    try:
        for row in rows:
            body = bytearray(row["body"])  # type: ignore[arg-type]
            try:
                decoded = body.decode("utf-8", errors="strict")
                if (
                    int(row["span_start_byte"]) != expected_byte
                    or int(row["span_start_character"]) != expected_character
                    or len(body) != int(row["body_plaintext_size_bytes"])
                    or len(decoded) != int(row["body_plaintext_character_count"])
                    or hashlib.sha256(body).hexdigest()
                    != str(row["body_plaintext_sha256"])
                ):
                    raise F0IError("REPLAY_MISMATCH")
                output.extend(body)
                expected_byte = int(row["span_end_byte"])
                expected_character = int(row["span_end_character"])
            finally:
                _wipe(body)
        if require_zero_start and rows and (
            int(rows[0]["span_start_byte"]) != 0
            or int(rows[0]["span_start_character"]) != 0
        ):
            raise F0IError("REPLAY_MISMATCH")
        return output
    except Exception:
        _wipe(output)
        raise


def _orphan_counts(
    connection: Connection[Mapping[str, object]],
    context: SessionContext,
    scope_ids: Sequence[uuid.UUID],
) -> tuple[int, int, int]:
    row = connection.execute(
        "SELECT "
        "(SELECT count(*) FROM f0i.block AS block LEFT JOIN f0i.document_scope AS scope "
        "ON scope.enterprise_id=block.enterprise_id AND scope.id=block.document_scope_id "
        "WHERE block.enterprise_id=%s AND block.document_scope_id=ANY(%s) "
        "AND scope.id IS NULL) AS orphan_blocks,"
        "(SELECT count(*) FROM f0i.chunk AS child LEFT JOIN f0i.chunk AS parent "
        "ON parent.enterprise_id=child.enterprise_id AND parent.id=child.parent_chunk_id "
        "WHERE child.enterprise_id=%s AND child.document_scope_id=ANY(%s) "
        "AND child.chunk_level='CHILD' AND parent.id IS NULL) AS orphan_chunks,"
        "(SELECT count(*) FROM f0i.block AS block JOIN f0i.document_scope AS scope "
        "ON scope.id=block.document_scope_id WHERE block.enterprise_id=%s "
        "AND block.document_scope_id=ANY(%s) AND (scope.enterprise_id<>block.enterprise_id "
        "OR scope.document_version_id<>block.document_version_id "
        "OR scope.configuration_id<>block.configuration_id)) AS crosswires",
        (
            context.enterprise_id,
            list(scope_ids),
            context.enterprise_id,
            list(scope_ids),
            context.enterprise_id,
            list(scope_ids),
        ),
    ).fetchone()
    if row is None:
        raise F0IError("REPLAY_MISMATCH")
    return int(row["orphan_blocks"]), int(row["orphan_chunks"]), int(row["crosswires"])


def _plaintext_column_leaks(connection: Connection[Mapping[str, object]]) -> int:
    row = connection.execute(
        "SELECT count(*) AS count FROM information_schema.columns "
        "WHERE table_schema='f0i' AND data_type IN ('text','character varying') "
        "AND column_name ~ '(body|raw_text|plaintext)$'"
    ).fetchone()
    return int(row["count"]) if row is not None else 1


def _wipe(value: bytearray) -> None:
    value[:] = b"\0" * len(value)
    value.clear()


__all__ = (
    "CanonicalUnitRecord",
    "ConfigurationIdentity",
    "DocumentRecord",
    "OcrRenderEvidence",
    "PersistResult",
    "RunInput",
    "SourceDocument",
    "SourcePage",
    "chunk_rule_sha256",
    "configuration_identity",
    "database_summary",
    "existing_scope_versions",
    "find_run",
    "inspect_configuration",
    "load_source_documents",
    "parser_rule_sha256",
    "persisted_child_links",
    "persist_run",
    "set_tenant_context",
    "validate_persisted_records",
    "validate_persisted_run",
)
