"""F0-I registered-fixture replay into encrypted canonical evidence.

The public entry points always acquire the fixed host flock before touching a
database, key, or OCR runtime.  ``replay_sequence`` intentionally holds that
same lock over smoke -> full -> full acceptance orchestration, closing the
cross-process gap that a sequence of independent CLI calls would leave.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import os
import uuid

from psycopg.conninfo import conninfo_to_dict

from ..auth import SessionContext, authenticate_local_session
from ..bootstrap import LOCAL_TENANT_A_TOKEN
from ..database import DatabaseConfig, role_transaction
from ..f0h.contracts import F0HError
from ..f0h.fixture_reader import (
    RegisteredPlan,
    load_registered_plan,
    open_registered_source,
)
from ..f0h.replay import _envelope
from ..f0h.runtime_config import RuntimeBundle, load_runtime_bundle, runtime_paths
from ..f0h.supervisor import FixedArgvPpocrV6Supervisor, docker_argv
from .bootstrap import ensure_database
from .chunking import build_canonical_unit
from .config import (
    ACCEPTANCE_DATABASE,
    database_config,
    validate_local_database_config,
)
from .contracts import F0IError, IdentityBinding, canonical_sha256
from .extractors import (
    UnitObservation,
    extract_docx_section,
    extract_native_pdf_pages,
    extract_xlsx_sheets,
    observation_from_ocr_result,
)
from .keyfile import (
    ACCEPTANCE_KEY_FILE,
    create_keyfile,
    load_keyfile,
)
from .locking import DEFAULT_HOST_LOCK_PATH, host_replay_lock
from .persistence import (
    CanonicalUnitRecord,
    ConfigurationIdentity,
    DocumentRecord,
    OcrRenderEvidence,
    RunInput,
    SourceDocument,
    SourcePage,
    chunk_rule_sha256,
    configuration_identity,
    database_summary,
    existing_scope_versions,
    find_run,
    inspect_configuration,
    load_source_documents,
    parser_rule_sha256,
    persisted_child_links,
    persist_run,
    set_tenant_context,
    validate_persisted_records,
    validate_persisted_run,
)


_PROFILES = frozenset({"smoke", "full"})
_EXPECTED = {
    "smoke": {
        "documents": 10,
        "visual_units": 110,
        "native_visual_units": 105,
        "ocr_visual_units": 5,
        "deferred_documents": 2,
    },
    "full": {
        "documents": 26,
        "visual_units": 249,
        "native_visual_units": 225,
        "ocr_visual_units": 24,
        "deferred_documents": 2,
    },
}


def replay_profile(
    profile: str,
    *,
    database_name: str = ACCEPTANCE_DATABASE,
    key_path: str = ACCEPTANCE_KEY_FILE,
    lock_path: str = DEFAULT_HOST_LOCK_PATH,
    config: DatabaseConfig | None = None,
) -> dict[str, object]:
    """Replay one profile under the fixed host process lock."""

    return replay_sequence(
        (profile,),
        database_name=database_name,
        key_path=key_path,
        lock_path=lock_path,
        config=config,
    )[0]


def replay_sequence(
    profiles: Sequence[str],
    *,
    database_name: str = ACCEPTANCE_DATABASE,
    key_path: str = ACCEPTANCE_KEY_FILE,
    lock_path: str = DEFAULT_HOST_LOCK_PATH,
    config: DatabaseConfig | None = None,
) -> tuple[dict[str, object], ...]:
    """Replay a non-empty sequence while one host flock covers every member."""

    if (
        not isinstance(profiles, Sequence)
        or isinstance(profiles, (str, bytes, bytearray, memoryview))
        or not profiles
        or any(profile not in _PROFILES for profile in profiles)
    ):
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    try:
        with host_replay_lock(lock_path):
            actual_config = _prepare_database(database_name, config)
            context = authenticate_local_session(actual_config, LOCAL_TENANT_A_TOKEN)
            _ensure_key_is_recoverable(actual_config, context, key_path)
            with load_keyfile(key_path) as key:
                bundle = load_runtime_bundle()
                full_plan = load_registered_plan("full")
                results: list[dict[str, object]] = []
                for profile in profiles:
                    key_material = key.view()
                    try:
                        results.append(
                            _replay_locked(
                                profile,
                                config=actual_config,
                                context=context,
                                key_material=key_material,
                                key_fingerprint_sha256=key.fingerprint_sha256,
                                bundle=bundle,
                                full_plan=full_plan,
                            )
                        )
                    finally:
                        key_material.release()
            return tuple(results)
    except F0IError:
        raise
    except F0HError as error:
        if error.code in {"SOURCE_OBJECT_CHANGED", "SOURCE_OBJECT_INVALID"}:
            raise F0IError("SOURCE_OBJECT_CHANGED") from None
        raise F0IError("REPLAY_MISMATCH") from None
    except Exception:
        raise F0IError("PERSISTENCE_FAILED") from None


def _replay_locked(
    profile: str,
    *,
    config: DatabaseConfig,
    context: SessionContext,
    key_material: memoryview,
    key_fingerprint_sha256: str,
    bundle: RuntimeBundle,
    full_plan: RegisteredPlan,
) -> dict[str, object]:
    plan = full_plan if profile == "full" else load_registered_plan(profile)
    entries = _registered_entries(plan)
    run_input = RunInput(
        profile=profile,
        input_manifest_sha256=_input_manifest_sha256(plan),
        input_summary_sha256=canonical_sha256(plan.payload.get("summary")),
        requested_document_count=len(entries),
    )
    identity = configuration_identity(
        context,
        bundle,
        full_plan_sha256=full_plan.page_plan_sha256,
        key_fingerprint_sha256=key_fingerprint_sha256,
    )
    with role_transaction(config, "f0d_migration") as connection:
        configured = inspect_configuration(
            connection, context, identity, bundle, key_material
        )
        sources = load_source_documents(connection, context, entries)
        _bind_manifests(plan, sources)
        prior_run = find_run(connection, context, identity, run_input) if configured else None
        smoke_sources: tuple[SourceDocument, ...] = ()
        if profile == "full":
            smoke_plan = load_registered_plan("smoke")
            smoke_entries = _registered_entries(smoke_plan)
            smoke_sources = load_source_documents(
                connection, context, smoke_entries
            )
            _bind_manifests(smoke_plan, smoke_sources)
            smoke_versions = frozenset(
                source.document_version_id for source in smoke_sources
            )
            full_versions = frozenset(
                source.document_version_id for source in sources
            )
            if (
                len(smoke_sources) != 10
                or len(smoke_versions) != 10
                or not smoke_versions < full_versions
            ):
                raise F0IError("REPLAY_MISMATCH")
            smoke_run_input = RunInput(
                profile="smoke",
                input_manifest_sha256=_input_manifest_sha256(smoke_plan),
                input_summary_sha256=canonical_sha256(
                    smoke_plan.payload.get("summary")
                ),
                requested_document_count=len(smoke_entries),
            )
            smoke_run = (
                find_run(connection, context, identity, smoke_run_input)
                if configured
                else None
            )
            if smoke_run is None or int(smoke_run["ocr_call_count"]) != 5:
                # A full replay is intentionally not an alternate fresh entry
                # point: the only accepted history is smoke(5) then full(+19).
                raise F0IError("REPLAY_MISMATCH")
        present_versions = (
            existing_scope_versions(
                connection,
                context,
                identity,
                tuple(source.document_version_id for source in sources),
            )
            if configured
            else frozenset()
        )
        existing_sources = tuple(
            source for source in sources if source.document_version_id in present_versions
        )
        existing_summary = (
            database_summary(connection, context, identity, existing_sources, key_material)
            if existing_sources
            else None
        )
        if profile == "full" and prior_run is None:
            if (
                present_versions != smoke_versions
                or existing_summary is None
                or smoke_run is None
            ):
                raise F0IError("REPLAY_MISMATCH")
            smoke_summary = database_summary(
                connection, context, identity, smoke_sources, key_material
            )
            _validate_expected_summary("smoke", smoke_summary)
            if canonical_sha256(smoke_summary) != str(
                smoke_run["replay_summary_sha256"]
            ):
                raise F0IError("REPLAY_MISMATCH")
        if prior_run is not None:
            if len(present_versions) != len(sources):
                raise F0IError("REPLAY_MISMATCH")
            summary = database_summary(
                connection, context, identity, sources, key_material
            )
            summary_sha256 = canonical_sha256(summary)
            if str(prior_run["replay_summary_sha256"]) != summary_sha256:
                raise F0IError("REPLAY_MISMATCH")
            validate_persisted_run(
                connection,
                context,
                identity,
                run_input,
                summary_sha256,
                summary,
                ocr_call_count=int(prior_run["ocr_call_count"]),
            )
            return _result(
                profile,
                plan,
                bundle,
                identity,
                run_input,
                summary,
                summary_sha256,
                rows_inserted=0,
                ocr_calls=0,
            )

    missing = tuple(
        (index, entry, source)
        for index, (entry, source) in enumerate(zip(entries, sources, strict=True))
        if source.document_version_id not in present_versions
    )
    drafts: list[DocumentRecord] = []
    ocr_calls = 0
    try:
        drafts, ocr_calls = _build_documents(
            plan,
            missing,
            context=context,
            bundle=bundle,
        )
        anticipated = _anticipated_summary(
            entries,
            sources,
            drafts,
            existing_summary=existing_summary,
        )
        _validate_expected_summary(profile, anticipated)
        summary_sha256 = canonical_sha256(anticipated)
        with role_transaction(config, "f0d_migration") as connection:
            # Recheck after expensive parsing/OCR while still under the same host
            # flock.  A mismatch indicates an out-of-contract DB writer.
            current = existing_scope_versions(
                connection,
                context,
                identity,
                tuple(source.document_version_id for source in sources),
            ) if configured else frozenset()
            if current != present_versions or find_run(
                connection, context, identity, run_input
            ) is not None:
                raise F0IError("REPLAY_MISMATCH")
            persisted = persist_run(
                connection,
                context,
                identity,
                bundle,
                key_material,
                run_input,
                summary_sha256,
                anticipated,
                drafts,
                ocr_call_count=ocr_calls,
            )
            validate_persisted_records(
                connection,
                context,
                identity,
                drafts,
            )
            # Reverse validation is part of the same write transaction.  A
            # span, decrypt, orphan, crosswire, count, or run-hash mismatch
            # therefore rolls back every F0-I row instead of stranding an
            # immutable partial acceptance target.
            summary = database_summary(
                connection, context, identity, sources, key_material
            )
            run = find_run(connection, context, identity, run_input)
            if (
                run is None
                or summary != anticipated
                or canonical_sha256(summary) != summary_sha256
                or str(run["replay_summary_sha256"]) != summary_sha256
                or int(run["ocr_call_count"]) != ocr_calls
            ):
                raise F0IError("REPLAY_MISMATCH")
            validate_persisted_run(
                connection,
                context,
                identity,
                run_input,
                summary_sha256,
                summary,
                ocr_call_count=ocr_calls,
            )
        return _result(
            profile,
            plan,
            bundle,
            identity,
            run_input,
            summary,
            summary_sha256,
            rows_inserted=persisted.rows_inserted,
            ocr_calls=ocr_calls,
        )
    finally:
        _wipe_document_records(drafts)


def _registered_entries(
    plan: RegisteredPlan,
) -> tuple[Mapping[str, object], ...]:
    entries_raw = plan.payload.get("entries")
    if not isinstance(entries_raw, list) or any(
        not isinstance(entry, dict) for entry in entries_raw
    ):
        raise F0IError("REPLAY_MISMATCH")
    return tuple(entries_raw)


def _build_documents(
    plan: RegisteredPlan,
    missing: Sequence[tuple[int, Mapping[str, object], SourceDocument]],
    *,
    context: SessionContext,
    bundle: RuntimeBundle,
) -> tuple[list[DocumentRecord], int]:
    documents: list[DocumentRecord] = []
    supervisor: FixedArgvPpocrV6Supervisor | None = None
    ocr_calls = 0
    try:
        for index, entry, source in missing:
            document_type = source.document_type
            if document_type == "DOC":
                if entry.get("parse_status") != "DEFERRED_CONVERSION_REQUIRED":
                    raise F0IError("REPLAY_MISMATCH")
                documents.append(
                    DocumentRecord(
                        source=source,
                        scope_kind="DEFERRED",
                        deferred_reason_code="DEFERRED_CONVERSION_REQUIRED",
                    )
                )
                continue
            if document_type in {"PDF", "JPEG"}:
                if supervisor is None and any(
                    page.candidate_decision == "FULL_PAGE_OCR_REQUIRED"
                    for page in source.pages
                ):
                    docker, seccomp = runtime_paths()
                    supervisor = FixedArgvPpocrV6Supervisor(
                        docker_argv(docker, seccomp, bundle.container_image_id), bundle
                    )
                record, calls = _build_visual_document(
                    plan,
                    index,
                    entry,
                    source,
                    context=context,
                    bundle=bundle,
                    supervisor=supervisor,
                )
                documents.append(record)
                ocr_calls += calls
                continue
            with open_registered_source(plan, index) as registered:
                if registered.sha256 != source.source_object_sha256 or registered.size != source.source_object_size_bytes:
                    raise F0IError("SOURCE_OBJECT_CHANGED")
                if document_type == "DOCX":
                    observation = extract_docx_section(
                        registered,
                        entry,
                        source_version_sha256=source.source_object_sha256,
                        source_plan_sha256=source.source_plan_sha256,
                    )
                    anchor_sha = canonical_sha256(
                        {
                            "anchors": entry.get("structure_anchors"),
                            "summary": entry.get("structure_summary"),
                        }
                    )
                    unit = _canonical_unit(
                        context, source, observation, bundle, None, anchor_sha
                    )
                    summary = _integer_summary(entry)
                    documents.append(
                        DocumentRecord(
                            source=source,
                            scope_kind="STRUCTURE",
                            units=(unit,),
                            structure_summary_sha256=canonical_sha256(
                                {
                                    "anchors": entry.get("structure_anchors"),
                                    "summary": entry.get("structure_summary"),
                                }
                            ),
                            docx_paragraph_count=summary["paragraphs"],
                            docx_table_count=summary["tables"],
                            docx_row_count=summary["rows"],
                            docx_cell_count=summary["cells"],
                        )
                    )
                    continue
                if document_type == "XLSX":
                    observations = extract_xlsx_sheets(
                        registered,
                        entry,
                        source_version_sha256=source.source_object_sha256,
                        source_plan_sha256=source.source_plan_sha256,
                    )
                    anchors = entry.get("structure_anchors")
                    if not isinstance(anchors, list) or len(anchors) != len(observations):
                        raise F0IError("REPLAY_MISMATCH")
                    units = tuple(
                        _canonical_unit(
                            context,
                            source,
                            observation,
                            bundle,
                            None,
                            canonical_sha256(anchor),
                        )
                        for observation, anchor in zip(observations, anchors, strict=True)
                    )
                    summary = _integer_summary(entry)
                    documents.append(
                        DocumentRecord(
                            source=source,
                            scope_kind="STRUCTURE",
                            units=units,
                            structure_summary_sha256=canonical_sha256(
                                {
                                    "anchors": entry.get("structure_anchors"),
                                    "summary": entry.get("structure_summary"),
                                }
                            ),
                            xlsx_sheet_count=summary["sheets"],
                            xlsx_cell_count=summary["cells"],
                            xlsx_value_cell_count=summary["value_cells"],
                            xlsx_formula_count=summary["formulas"],
                            xlsx_formula_cached_value_count=summary[
                                "formula_cached_values"
                            ],
                        )
                    )
                    continue
            raise F0IError("REPLAY_MISMATCH")
        return documents, ocr_calls
    except Exception:
        _wipe_document_records(documents)
        raise


def _build_visual_document(
    plan: RegisteredPlan,
    index: int,
    entry: Mapping[str, object],
    source: SourceDocument,
    *,
    context: SessionContext,
    bundle: RuntimeBundle,
    supervisor: FixedArgvPpocrV6Supervisor | None,
) -> tuple[DocumentRecord, int]:
    pages = entry.get("pages")
    if not isinstance(pages, list) or len(pages) != len(source.pages):
        raise F0IError("REPLAY_MISMATCH")
    units: list[CanonicalUnitRecord] = []
    calls = 0
    try:
        with open_registered_source(plan, index) as registered:
            if registered.sha256 != source.source_object_sha256 or registered.size != source.source_object_size_bytes:
                raise F0IError("SOURCE_OBJECT_CHANGED")
            native_pages = tuple(
                page
                for page in pages
                if isinstance(page, dict) and page.get("decision") == "NATIVE_CANDIDATE"
            )
            native_observations = (
                extract_native_pdf_pages(registered, entry, native_pages)
                if source.document_type == "PDF" and native_pages
                else ()
            )
            native_by_ordinal = {
                observation.unit_ordinal: observation
                for observation in native_observations
            }
            for planned, source_page in zip(pages, source.pages, strict=True):
                if not isinstance(planned, dict):
                    raise F0IError("REPLAY_MISMATCH")
                render: OcrRenderEvidence | None = None
                if source_page.candidate_decision == "NATIVE_CANDIDATE":
                    observation = native_by_ordinal.get(source_page.page_no)
                    if observation is None:
                        raise F0IError("REPLAY_MISMATCH")
                else:
                    if supervisor is None:
                        raise F0IError("REPLAY_MISMATCH")
                    envelope = _envelope(registered, entry, planned)
                    result: dict[str, object] | None = None
                    try:
                        result = supervisor.execute_envelope(
                            envelope,
                            expected={
                                "document_type": source.document_type,
                                "expected_total_pages": int(entry.get("page_count", 1)),
                                "page_no": source_page.page_no,
                                "source_sha256": source.source_object_sha256,
                                "source_unit_id": source_page.source_unit_id,
                            },
                        )
                        observation = observation_from_ocr_result(entry, planned, result)
                        render = _render_evidence(source.document_type, result)
                        calls += 1
                    finally:
                        envelope[:] = b"\0" * len(envelope)
                        envelope.clear()
                        if result is not None:
                            blocks = result.get("blocks")
                            if isinstance(blocks, list):
                                blocks.clear()
                            result.clear()
                units.append(
                    _canonical_unit(
                        context,
                        source,
                        observation,
                        bundle,
                        source_page,
                        None,
                        render=render,
                    )
                )
        return DocumentRecord(source=source, scope_kind="VISUAL", units=tuple(units)), calls
    except Exception:
        for unit in units:
            unit.canonical.wipe()
        units.clear()
        raise


def _canonical_unit(
    context: SessionContext,
    source: SourceDocument,
    observation: UnitObservation,
    bundle: RuntimeBundle,
    source_page: SourcePage | None,
    structure_anchor_sha256: str | None,
    *,
    render: OcrRenderEvidence | None = None,
) -> CanonicalUnitRecord:
    binding = IdentityBinding(
        tenant_id=context.enterprise_id,
        document_version_id=source.document_version_id,
        source_processing_unit_id=(
            source_page.processing_unit_id if source_page is not None else None
        ),
        structure_unit_sha256=(
            observation.structure_unit_sha256 if source_page is None else None
        ),
        source_version_sha256=source.source_object_sha256,
        f0h_model_sha256=bundle.model_bundle_sha256,
        f0h_configuration_sha256=bundle.configuration_sha256,
        parsing_rule_sha256=parser_rule_sha256(),
        chunking_rule_sha256=chunk_rule_sha256(),
    )
    canonical = build_canonical_unit(
        binding, tuple(item.leaf for item in observation.leaves)
    )
    return CanonicalUnitRecord(
        observation=observation,
        canonical=canonical,
        source_page=source_page,
        structure_anchor_sha256=structure_anchor_sha256,
        ocr_render=render,
    )


def _render_evidence(
    document_type: str, result: Mapping[str, object]
) -> OcrRenderEvidence:
    renderer = result.get("renderer")
    if not isinstance(renderer, dict):
        raise F0IError("REPLAY_MISMATCH")
    if document_type == "PDF":
        renderer_id = "pypdfium2"
        renderer_version = "5.12.1+pdfium.152.0.7947.0"
    else:
        renderer_id = "opencv-imdecode"
        renderer_version = "5.0.0.93"
    return OcrRenderEvidence(
        width_px=_positive(result.get("render_width_px")),
        height_px=_positive(result.get("render_height_px")),
        dpi=(250 if document_type == "PDF" else None),
        origin=str(result.get("render_origin")),
        renderer_id=renderer_id,
        renderer_version=renderer_version,
        render_sha256=_sha256(result.get("render_sha256")),
    )


def _anticipated_summary(
    entries: Sequence[Mapping[str, object]],
    sources: Sequence[SourceDocument],
    drafts: Sequence[DocumentRecord],
    *,
    existing_summary: Mapping[str, int] | None,
) -> dict[str, int]:
    visual_sources = tuple(source for source in sources if source.document_type in {"PDF", "JPEG"})
    structure_sources = tuple(source for source in sources if source.document_type in {"DOCX", "XLSX"})
    deferred_sources = tuple(source for source in sources if source.document_type == "DOC")
    native = sum(
        page.candidate_decision == "NATIVE_CANDIDATE"
        for source in visual_sources
        for page in source.pages
    )
    ocr = sum(
        page.candidate_decision == "FULL_PAGE_OCR_REQUIRED"
        for source in visual_sources
        for page in source.pages
    )
    structure_units = sum(1 if source.document_type == "DOCX" else 3 for source in structure_sources)
    existing_blocks = int(existing_summary.get("blocks", 0)) if existing_summary else 0
    existing_parents = int(existing_summary.get("parent_chunks", 0)) if existing_summary else 0
    existing_children = int(existing_summary.get("child_chunks", 0)) if existing_summary else 0
    existing_links = int(existing_summary.get("child_block_links", 0)) if existing_summary else 0
    new_units = tuple(unit for document in drafts for unit in document.units)
    docx_summary = next(
        (_integer_summary(entry) for entry in entries if entry.get("type") == "DOCX"),
        {},
    )
    xlsx_summary = next(
        (_integer_summary(entry) for entry in entries if entry.get("type") == "XLSX"),
        {},
    )
    return {
        "documents": len(sources),
        "document_scopes": len(sources),
        "visual_documents": len(visual_sources),
        "structure_documents": len(structure_sources),
        "deferred_documents": len(deferred_sources),
        "visual_units": sum(len(source.pages) for source in visual_sources),
        "native_visual_units": native,
        "ocr_visual_units": ocr,
        "persisted_ocr_calls": ocr,
        "persisted_runs": 1 if len(sources) == 10 else 2,
        "persisted_smoke_ocr_calls": 5,
        "persisted_full_ocr_calls": 0 if len(sources) == 10 else 19,
        "structure_units": structure_units,
        "pages": sum(len(source.pages) for source in visual_sources),
        "blocks": existing_blocks + sum(len(unit.canonical.blocks) for unit in new_units),
        "parent_chunks": existing_parents + len(new_units),
        "child_chunks": existing_children + sum(len(unit.canonical.children) for unit in new_units),
        "child_block_links": existing_links
        + sum(len(persisted_child_links(unit.canonical)) for unit in new_units),
        "docx_sections": 1 if docx_summary else 0,
        "docx_paragraphs": int(docx_summary.get("paragraphs", 0)),
        "docx_tables": int(docx_summary.get("tables", 0)),
        "docx_rows": int(docx_summary.get("rows", 0)),
        "docx_table_cells": int(docx_summary.get("cells", 0)),
        "xlsx_sheets": int(xlsx_summary.get("sheets", 0)),
        "xlsx_cells": int(xlsx_summary.get("cells", 0)),
        "xlsx_formula_cells": int(xlsx_summary.get("formulas", 0)),
        "xlsx_formula_cached_values": int(
            xlsx_summary.get("formula_cached_values", 0)
        ),
        "xlsx_value_cells": int(xlsx_summary.get("value_cells", 0)),
        "negative_scopes": sum(source.source_group == "negative" for source in sources),
        "negative_enabled_gates": 0,
        "reconstruction_failures": 0,
        "tenant_version_crosswires": 0,
        "orphan_blocks": 0,
        "orphan_chunks": 0,
        "plaintext_leaks": 0,
    }


def _validate_expected_summary(profile: str, summary: Mapping[str, int]) -> None:
    expected = _EXPECTED[profile]
    if any(summary.get(key) != value for key, value in expected.items()) or (
        summary.get("document_scopes") != expected["documents"]
        or summary.get("pages") != expected["visual_units"]
        or summary.get("structure_units") != 4
        or summary.get("docx_sections") != 1
        or summary.get("docx_paragraphs") != 60
        or summary.get("docx_tables") != 1
        or summary.get("docx_rows") != 5
        or summary.get("docx_table_cells") != 58
        or summary.get("xlsx_sheets") != 3
        or summary.get("xlsx_cells") != 306
        or summary.get("xlsx_formula_cells") != 0
        or summary.get("xlsx_formula_cached_values") != 0
        or summary.get("xlsx_value_cells") != 19
        or summary.get("negative_scopes") != 2
        or summary.get("negative_enabled_gates") != 0
        or summary.get("persisted_runs") != (1 if profile == "smoke" else 2)
        or summary.get("persisted_smoke_ocr_calls") != 5
        or summary.get("persisted_full_ocr_calls") != (0 if profile == "smoke" else 19)
    ):
        raise F0IError("REPLAY_MISMATCH")


def _result(
    profile: str,
    plan: RegisteredPlan,
    bundle: RuntimeBundle,
    identity: ConfigurationIdentity,
    run_input: RunInput,
    summary: Mapping[str, int],
    replay_summary_sha256: str,
    *,
    rows_inserted: int,
    ocr_calls: int,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "f0i-replay-result-v1",
        "status": "LOCAL_CANONICAL_CHUNKS_READY",
        "accuracy_status": "ACCURACY_NOT_EVALUATED",
        "search_status": "SEARCH_NOT_READY",
        "production_status": "NOT_PRODUCTION",
        "profile": profile,
        "summary": dict(summary),
        "delta": {"rows_inserted": rows_inserted, "ocr_calls": ocr_calls},
        "configuration_sha256": identity.configuration_sha256,
        "runtime": {
            "model_bundle_sha256": bundle.model_bundle_sha256,
            "configuration_sha256": bundle.configuration_sha256,
            "execution_profile_sha256": bundle.execution_profile_sha256,
            "lock_sha256": bundle.lock_sha256,
            "image_id": bundle.container_image_id,
        },
        "registered_plan_sha256": plan.page_plan_sha256,
        "input_manifest_sha256": run_input.input_manifest_sha256,
        "input_summary_sha256": run_input.input_summary_sha256,
        "replay_summary_sha256": replay_summary_sha256,
        "raw_text_persisted": False,
        "external_calls": 0,
        "search_calls": 0,
        "errors": 0,
    }
    encoded = repr(value)
    if any(
        marker in encoded
        for marker in ("/Users/", "environment-demo", "http://", "https://", "@")
    ):
        raise F0IError("REPLAY_MISMATCH")
    return value


def _prepare_database(
    database_name: str, config: DatabaseConfig | None
) -> DatabaseConfig:
    actual = (
        database_config(database_name)
        if config is None
        else validate_local_database_config(config)
    )
    try:
        names = {
            str(conninfo_to_dict(getattr(actual, field))["dbname"])
            for field in ("migration_dsn", "runtime_dsn", "worker_dsn")
        }
    except Exception:
        raise F0IError("DATABASE_CONFIGURATION_INVALID") from None
    if names != {database_name}:
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    ensure_database(database_name)
    return actual


def _ensure_key_is_recoverable(
    config: DatabaseConfig, context: SessionContext, key_path: str
) -> None:
    if os.path.lexists(key_path):
        return
    with role_transaction(config, "f0d_migration") as connection:
        set_tenant_context(connection, context)
        row = connection.execute(
            "SELECT count(*) AS count FROM f0i.configuration"
        ).fetchone()
        if row is None or int(row["count"]) != 0:
            raise F0IError("KEYFILE_NOT_AVAILABLE")
    create_keyfile(key_path)


def _bind_manifests(
    plan: RegisteredPlan, sources: Sequence[SourceDocument]
) -> None:
    if len(plan.manifests) != len(sources):
        raise F0IError("REPLAY_MISMATCH")
    for manifest, source in zip(plan.manifests, sources, strict=True):
        if (
            manifest.group != source.source_group
            or manifest.expected_sha256 != source.source_object_sha256
        ):
            raise F0IError("REPLAY_MISMATCH")


def _input_manifest_sha256(plan: RegisteredPlan) -> str:
    return canonical_sha256(
        [
            {
                "expected_sha256": item.expected_sha256,
                "group": item.group,
                "line": item.line,
            }
            for item in plan.manifests
        ]
    )


def _integer_summary(entry: Mapping[str, object]) -> dict[str, int]:
    value = entry.get("structure_summary")
    if not isinstance(value, dict) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 0
        for item in value.values()
    ):
        raise F0IError("REPLAY_MISMATCH")
    return {str(key): int(item) for key, item in value.items()}


def _positive(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise F0IError("REPLAY_MISMATCH")
    return value


def _sha256(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise F0IError("REPLAY_MISMATCH")
    return value


def _wipe_document_records(documents: Sequence[DocumentRecord]) -> None:
    for document in documents:
        for unit in document.units:
            unit.canonical.wipe()
    if isinstance(documents, list):
        documents.clear()


__all__ = ("replay_profile", "replay_sequence")
