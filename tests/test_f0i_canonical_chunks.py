from __future__ import annotations

from dataclasses import replace
import contextlib
import hashlib
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
import uuid
from xml.etree import ElementTree

import psycopg

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN
from platform_foundation.database import DatabaseConfig, role_transaction
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.f0h.fixture_reader import (
    load_registered_plan,
    open_registered_source,
)
from platform_foundation.f0h.runtime_config import load_runtime_bundle
from platform_foundation.f0i.bootstrap import (
    drop_scratch_database,
    ensure_database,
)
from platform_foundation.f0i.chunking import (
    CHILD_MAX_CHARACTERS,
    CHILD_MIN_CHARACTERS,
    build_canonical_unit,
    build_leaf_blocks,
    build_parent_child_chunks,
    canonicalize_text,
    verify_reconstruction,
)
from platform_foundation.f0i.config import (
    database_config,
    validate_local_database_config,
)
from platform_foundation.f0i.contracts import (
    CANONICAL_TEXT_RULE,
    CHUNK_RULE,
    LEAF_RULE,
    F0IError,
    IdentityBinding,
    LeafInput,
    SensitiveCanonicalBody,
    Utf8Span,
    canonical_json_bytes,
    canonical_sha256,
    chain_sha256,
    stable_uuid4,
)
from platform_foundation.f0i.keyfile import create_keyfile, load_keyfile
from platform_foundation.f0i.locking import HostReplayLock
from platform_foundation.f0i.extractors import (
    _cell_text,
    extract_native_pdf_pages,
    observation_from_ocr_result,
)
from platform_foundation.f0i.artifacts import (
    _atomic_write_all_at,
    _replay_proof,
    _sbom,
    _status_html,
    _validate_public_payloads,
    _verify_sequence,
)
from platform_foundation.f0i.persistence import (
    DocumentRecord,
    RunInput,
    configuration_identity,
    database_summary,
    load_source_documents,
    persist_run,
    set_tenant_context,
    validate_persisted_records,
    validate_persisted_run,
)
from platform_foundation.f0i.replay import (
    _anticipated_summary,
    _bind_manifests,
    _build_documents,
    _canonical_unit,
    _input_manifest_sha256,
    _registered_entries,
    _render_evidence,
    _wipe_document_records,
)
from platform_foundation.f0i.structures import (
    docx_paragraph_location,
    docx_table_cell_location,
    native_geometry,
    ocr_bbox_to_ppm,
    page_geometry,
    pdf_table_status,
    structure_unit_sha256,
    xlsx_cell_location,
    xlsx_sheet_location,
)


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
_PRIVATE_TMP = (
    str(_FROZEN_F0_ISOLATION.tmp_dir)
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp"
)


def _f0i_private_path(label: str, suffix: str) -> str:
    if _FROZEN_F0_ISOLATION is None:
        return f"/private/tmp/anhuan-f0i-{label}{suffix}"
    return str(
        _FROZEN_F0_ISOLATION.tmp_dir
        / (
            f"anhuan-f0i-{_FROZEN_F0_ISOLATION.project_id.hex}-"
            f"{label}{suffix}"
        )
    )


_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64
_SHA_E = "e" * 64
_SHA_F = "f" * 64


def _binding(
    *,
    tenant_id: uuid.UUID | None = None,
    document_version_id: uuid.UUID | None = None,
    processing_unit_id: uuid.UUID | None = None,
    structure_sha256: str | None = None,
) -> IdentityBinding:
    visual = structure_sha256 is None
    return IdentityBinding(
        tenant_id=tenant_id or uuid.UUID("00000000-0000-4000-8000-000000000001"),
        document_version_id=document_version_id
        or uuid.UUID("00000000-0000-4000-8000-000000000002"),
        source_processing_unit_id=(
            processing_unit_id
            or uuid.UUID("00000000-0000-4000-8000-000000000003")
            if visual
            else None
        ),
        structure_unit_sha256=structure_sha256,
        source_version_sha256=_SHA_A,
        f0h_model_sha256=_SHA_B,
        f0h_configuration_sha256=_SHA_C,
        parsing_rule_sha256=_SHA_D,
        chunking_rule_sha256=_SHA_E,
    )


def _leaf(text: str, *, separator_after: str = "") -> LeafInput:
    return LeafInput(
        text=text,
        block_kind="NATIVE_PAGE_TEXT",
        locator_kind="NATIVE_PAGE",
        locator_sha256=hashlib.sha256(b"synthetic-locator").hexdigest(),
        separator_after=separator_after,
    )


def _unit(text: str):
    return build_canonical_unit(_binding(), (_leaf(text),))


def _artifact_replay(profile: str, *, rows: int, calls: int) -> dict[str, object]:
    smoke = profile == "smoke"
    summary: dict[str, object] = {
        "documents": 10 if smoke else 26,
        "document_scopes": 10 if smoke else 26,
        "visual_documents": 6 if smoke else 22,
        "structure_documents": 2,
        "deferred_documents": 2,
        "visual_units": 110 if smoke else 249,
        "native_visual_units": 105 if smoke else 225,
        "ocr_visual_units": 5 if smoke else 24,
        "structure_units": 4,
        "pages": 110 if smoke else 249,
        "blocks": 800 if smoke else 1600,
        "parent_chunks": 114 if smoke else 253,
        "child_chunks": 114 if smoke else 253,
        "child_block_links": 800 if smoke else 1600,
        "negative_scopes": 2,
        "negative_enabled_gates": 0,
        "reconstruction_failures": 0,
        "tenant_version_crosswires": 0,
        "orphan_blocks": 0,
        "orphan_chunks": 0,
        "plaintext_leaks": 0,
        "persisted_ocr_calls": 5 if smoke else 24,
        "persisted_runs": 1 if smoke else 2,
        "persisted_smoke_ocr_calls": 5,
        "persisted_full_ocr_calls": 0 if smoke else 19,
    }
    summary.update(
        {
            "docx_sections": 1,
            "docx_paragraphs": 60,
            "docx_tables": 1,
            "docx_rows": 5,
            "docx_table_cells": 58,
            "xlsx_sheets": 3,
            "xlsx_cells": 306,
            "xlsx_formula_cells": 0,
            "xlsx_formula_cached_values": 0,
            "xlsx_value_cells": 19,
        }
    )
    return {
        "schema": "f0i-replay-result-v1",
        "status": "LOCAL_CANONICAL_CHUNKS_READY",
        "accuracy_status": "ACCURACY_NOT_EVALUATED",
        "search_status": "SEARCH_NOT_READY",
        "production_status": "NOT_PRODUCTION",
        "profile": profile,
        "summary": summary,
        "delta": {"rows_inserted": rows, "ocr_calls": calls},
        "configuration_sha256": _SHA_A,
        "registered_plan_sha256": _SHA_B,
        "input_manifest_sha256": _SHA_C,
        "input_summary_sha256": _SHA_D,
        "replay_summary_sha256": _SHA_E,
        "external_calls": 0,
        "search_calls": 0,
        "errors": 0,
        "raw_text_persisted": False,
    }


def _database_name(name: str) -> str:
    if _FROZEN_F0_ISOLATION is None:
        return name
    aliases = {
        "f0i_test_0123456789abcdef": _FROZEN_F0_ISOLATION.database_name(
            "f0i-migration"
        ),
        "f0i_test_fedcba9876543210": _FROZEN_F0_ISOLATION.database_name(
            "f0i-persistence"
        ),
    }
    return aliases.get(name, name)


def _database_config(name: str) -> DatabaseConfig:
    name = _database_name(name)
    if _FROZEN_F0_ISOLATION is not None:
        return _FROZEN_F0_ISOLATION.database_config(name)
    base = "127.0.0.1:55432/" + name
    return DatabaseConfig(
        migration_dsn=(
            "postgresql://f0d_migration:f0d-migration-local-v01@" + base
        ),
        runtime_dsn="postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        worker_dsn="postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    )


def _lock_probe(path: str, connection: object) -> None:
    try:
        with HostReplayLock(path):
            connection.send("ACQUIRED")  # type: ignore[attr-defined]
    except F0IError as error:
        connection.send(error.code)  # type: ignore[attr-defined]
    finally:
        connection.close()  # type: ignore[attr-defined]


class F0IContractTests(unittest.TestCase):
    def test_frozen_contract_versions_are_explicit(self) -> None:
        self.assertEqual(CANONICAL_TEXT_RULE, "UTF8_NFC_LF_V1")
        self.assertEqual(CHUNK_RULE, "UNICODE_300_800_NO_OVERLAP_V1")
        self.assertEqual(LEAF_RULE, "ORDERED_UTF8_SPAN_COVERAGE_V1")

    def test_unknown_error_is_redacted(self) -> None:
        error = F0IError("PRIVATE_BODY_13900000000")
        self.assertEqual(str(error), "CANONICAL_CONTRACT_INVALID")
        self.assertNotIn("PRIVATE", repr(error.to_dict()))
        self.assertNotIn("13900000000", repr(error.to_dict()))

    def test_known_error_has_fixed_dictionary(self) -> None:
        self.assertEqual(
            F0IError("GEOMETRY_INVALID").to_dict(),
            {"error": "F0I_ERROR", "reason_code": "GEOMETRY_INVALID"},
        )

    def test_canonical_json_is_ascii_sorted_and_compact(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"z": "中", "a": [2, 1]}),
            b'{"a":[2,1],"z":"\\u4e2d"}',
        )

    def test_canonical_json_rejects_float(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_CONTRACT_INVALID"):
            canonical_json_bytes({"not_metadata": 0.5})

    def test_stable_uuid_is_deterministic_rfc4122_uuid4(self) -> None:
        first = stable_uuid4("f0i.test.v1", {"value": 1})
        second = stable_uuid4("f0i.test.v1", {"value": 1})
        self.assertEqual(first, second)
        self.assertEqual((first.version, first.variant), (4, uuid.RFC_4122))

    def test_stable_uuid_binds_namespace(self) -> None:
        self.assertNotEqual(
            stable_uuid4("f0i.test.one", "same"),
            stable_uuid4("f0i.test.two", "same"),
        )

    def test_chain_hash_binds_predecessor(self) -> None:
        root = chain_sha256("f0i.test.chain", None, {"ordinal": 1})
        linked = chain_sha256("f0i.test.chain", root, {"ordinal": 2})
        unlinked = chain_sha256("f0i.test.chain", None, {"ordinal": 2})
        self.assertNotEqual(linked, unlinked)

    def test_chain_hash_rejects_malformed_predecessor(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_CONTRACT_INVALID"):
            chain_sha256("f0i.test.chain", "not-a-sha", {})

    def test_visual_identity_has_exclusive_processing_unit(self) -> None:
        payload = _binding().identity_payload()
        self.assertEqual(payload["processing_unit_kind"], "UPSTREAM_VISUAL")
        self.assertIsNotNone(payload["source_processing_unit_id"])
        self.assertIsNone(payload["structure_unit_sha256"])

    def test_structure_identity_has_exclusive_structure_hash(self) -> None:
        payload = _binding(structure_sha256=_SHA_F).identity_payload()
        self.assertEqual(payload["processing_unit_kind"], "NATIVE_STRUCTURE")
        self.assertIsNone(payload["source_processing_unit_id"])
        self.assertEqual(payload["structure_unit_sha256"], _SHA_F)

    def test_identity_rejects_missing_processing_identity(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_CONTRACT_INVALID"):
            IdentityBinding(
                tenant_id=uuid.uuid4(),
                document_version_id=uuid.uuid4(),
                source_processing_unit_id=None,
                structure_unit_sha256=None,
                source_version_sha256=_SHA_A,
                f0h_model_sha256=_SHA_B,
                f0h_configuration_sha256=_SHA_C,
                parsing_rule_sha256=_SHA_D,
                chunking_rule_sha256=_SHA_E,
            )

    def test_identity_rejects_crosswired_processing_identities(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_CONTRACT_INVALID"):
            IdentityBinding(
                tenant_id=uuid.uuid4(),
                document_version_id=uuid.uuid4(),
                source_processing_unit_id=uuid.uuid4(),
                structure_unit_sha256=_SHA_F,
                source_version_sha256=_SHA_A,
                f0h_model_sha256=_SHA_B,
                f0h_configuration_sha256=_SHA_C,
                parsing_rule_sha256=_SHA_D,
                chunking_rule_sha256=_SHA_E,
            )

    def test_sensitive_body_counts_utf8_bytes_and_unicode_characters(self) -> None:
        body = SensitiveCanonicalBody("A中🙂\n".encode())
        try:
            self.assertEqual(body.byte_count, 9)
            self.assertEqual(body.character_count, 4)
            self.assertEqual(body.nonblank_character_count, 3)
            self.assertEqual(body.sha256, hashlib.sha256("A中🙂\n".encode()).hexdigest())
        finally:
            body.wipe()

    def test_sensitive_body_view_is_read_only(self) -> None:
        body = SensitiveCanonicalBody(b"body")
        try:
            self.assertTrue(body.view().readonly)
        finally:
            body.wipe()

    def test_sensitive_body_repr_redacts_body(self) -> None:
        body = SensitiveCanonicalBody(b"SYNTHETIC_BODY_CANARY")
        try:
            self.assertNotIn("CANARY", repr(body))
        finally:
            body.wipe()

    def test_sensitive_body_context_wipes_capability(self) -> None:
        body = SensitiveCanonicalBody(b"ephemeral")
        with body:
            self.assertEqual(body.byte_count, 9)
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_INVALID"):
            body.view()

    def test_sensitive_body_rejects_invalid_utf8(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_INVALID"):
            SensitiveCanonicalBody(b"\xff")

    def test_sensitive_body_rejects_carriage_return(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_INVALID"):
            SensitiveCanonicalBody(b"a\rb")

    def test_sensitive_body_rejects_non_nfc(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_INVALID"):
            SensitiveCanonicalBody("e\u0301".encode())

    def test_leaf_repr_redacts_text_and_separator(self) -> None:
        leaf = _leaf("SYNTHETIC_LEAF_CANARY", separator_after="SECRET_SEPARATOR")
        rendered = repr(leaf)
        self.assertNotIn("CANARY", rendered)
        self.assertNotIn("SECRET", rendered)

    def test_utf8_span_reports_exact_deltas(self) -> None:
        span = Utf8Span(2, 9, 1, 4)
        self.assertEqual((span.byte_count, span.character_count), (7, 3))


class F0IChunkingTests(unittest.TestCase):
    def _assert_child_lengths(self, length: int, expected: tuple[int, ...]) -> None:
        unit = _unit("x" * length)
        try:
            self.assertEqual(
                tuple(child.plaintext_characters for child in unit.children),
                expected,
            )
            self.assertTrue(unit.children[-1].is_tail)
            self.assertTrue(all(not child.is_tail for child in unit.children[:-1]))
        finally:
            unit.wipe()

    def test_canonicalize_normalizes_line_endings_and_nfc(self) -> None:
        self.assertEqual(canonicalize_text("e\u0301\r\nnext\rlast"), "é\nnext\nlast")

    def test_multibyte_leaf_spans_track_bytes_and_characters(self) -> None:
        unit = _unit("A中🙂")
        try:
            span = unit.blocks[0].span
            self.assertEqual(span.to_dict(), {
                "start_byte": 0,
                "end_byte": 8,
                "start_character": 0,
                "end_character": 3,
            })
            self.assertEqual(unit.children[0].span, span)
        finally:
            unit.wipe()

    def test_separator_is_an_explicit_leaf_block(self) -> None:
        unit = build_canonical_unit(
            _binding(),
            (_leaf("one", separator_after="\n"), _leaf("two")),
        )
        try:
            self.assertEqual(
                tuple(block.block_kind for block in unit.blocks),
                ("NATIVE_PAGE_TEXT", "CANONICAL_SEPARATOR", "NATIVE_PAGE_TEXT"),
            )
            self.assertEqual(unit.body.view().tobytes(), b"one\ntwo")
        finally:
            unit.wipe()

    def test_leaf_spans_cover_body_without_gap(self) -> None:
        unit = build_canonical_unit(
            _binding(),
            (_leaf("甲", separator_after="\n"), _leaf("乙")),
        )
        try:
            self.assertEqual(unit.blocks[0].span.start_byte, 0)
            for left, right in zip(unit.blocks, unit.blocks[1:]):
                self.assertEqual(left.span.end_byte, right.span.start_byte)
                self.assertEqual(
                    left.span.end_character, right.span.start_character
                )
            self.assertEqual(unit.blocks[-1].span.end_byte, unit.body.byte_count)
        finally:
            unit.wipe()

    def test_empty_body_has_one_zero_length_tail_child(self) -> None:
        self._assert_child_lengths(0, (0,))

    def test_299_characters_are_permitted_as_tail(self) -> None:
        self._assert_child_lengths(299, (299,))

    def test_300_characters_form_one_child(self) -> None:
        self._assert_child_lengths(300, (300,))

    def test_800_characters_form_one_child(self) -> None:
        self._assert_child_lengths(800, (800,))

    def test_801_characters_split_at_frozen_upper_bound(self) -> None:
        self._assert_child_lengths(801, (800, 1))

    def test_1099_characters_allow_short_tail(self) -> None:
        self._assert_child_lengths(1099, (800, 299))

    def test_1100_characters_end_with_300_character_tail(self) -> None:
        self._assert_child_lengths(1100, (800, 300))

    def test_1600_characters_form_two_full_children(self) -> None:
        self._assert_child_lengths(1600, (800, 800))

    def test_child_spans_have_zero_overlap(self) -> None:
        unit = _unit("x" * 1601)
        try:
            for left, right in zip(unit.children, unit.children[1:]):
                self.assertEqual(left.span.end_byte, right.span.start_byte)
                self.assertEqual(
                    left.span.end_character, right.span.start_character
                )
        finally:
            unit.wipe()

    def test_every_child_has_exactly_one_parent(self) -> None:
        unit = _unit("x" * 1601)
        try:
            self.assertTrue(
                all(
                    child.parent_chunk_id == unit.parent.chunk_id
                    for child in unit.children
                )
            )
            self.assertIsNone(unit.parent.parent_chunk_id)
        finally:
            unit.wipe()

    def test_chunk_block_links_reference_only_unit_members(self) -> None:
        unit = build_canonical_unit(
            _binding(),
            (_leaf("a" * 500, separator_after="\n"), _leaf("b" * 500)),
        )
        try:
            chunk_ids = {unit.parent.chunk_id, *(child.chunk_id for child in unit.children)}
            block_ids = {block.block_id for block in unit.blocks}
            self.assertTrue(all(link.chunk_id in chunk_ids for link in unit.links))
            self.assertTrue(all(link.block_id in block_ids for link in unit.links))
        finally:
            unit.wipe()

    def test_same_inputs_produce_same_ids_and_chains(self) -> None:
        first = _unit("stable" * 200)
        second = _unit("stable" * 200)
        try:
            self.assertEqual(first.blocks, second.blocks)
            self.assertEqual(first.parent, second.parent)
            self.assertEqual(first.children, second.children)
            self.assertEqual(first.unit_chain_sha256, second.unit_chain_sha256)
        finally:
            first.wipe()
            second.wipe()

    def test_source_processing_unit_changes_all_unit_identity(self) -> None:
        first = build_canonical_unit(
            _binding(processing_unit_id=uuid.UUID("00000000-0000-4000-8000-000000000003")),
            (_leaf("same body"),),
        )
        second = build_canonical_unit(
            _binding(processing_unit_id=uuid.UUID("00000000-0000-4000-8000-000000000004")),
            (_leaf("same body"),),
        )
        try:
            self.assertNotEqual(first.blocks[0].block_id, second.blocks[0].block_id)
            self.assertNotEqual(first.parent.chunk_id, second.parent.chunk_id)
            self.assertNotEqual(first.unit_chain_sha256, second.unit_chain_sha256)
        finally:
            first.wipe()
            second.wipe()

    def test_children_do_not_cross_processing_units(self) -> None:
        first = _unit("one" * 300)
        second = build_canonical_unit(
            _binding(document_version_id=uuid.UUID("00000000-0000-4000-8000-000000000005")),
            (_leaf("two" * 300),),
        )
        try:
            self.assertTrue(
                all(child.parent_chunk_id == first.parent.chunk_id for child in first.children)
            )
            self.assertTrue(
                all(child.parent_chunk_id == second.parent.chunk_id for child in second.children)
            )
            self.assertNotEqual(first.parent.chunk_id, second.parent.chunk_id)
        finally:
            first.wipe()
            second.wipe()

    def test_block_hash_tamper_breaks_reconstruction(self) -> None:
        unit = _unit("tamper evidence")
        try:
            damaged = replace(
                unit,
                blocks=(replace(unit.blocks[0], plaintext_sha256=_SHA_F),),
            )
            with self.assertRaisesRegex(F0IError, "CANONICAL_RECONSTRUCTION_FAILED"):
                verify_reconstruction(damaged)
        finally:
            unit.wipe()

    def test_child_chain_tamper_breaks_reconstruction(self) -> None:
        unit = _unit("x" * 801)
        try:
            damaged_children = (
                replace(unit.children[0], chain_sha256=_SHA_F),
                *unit.children[1:],
            )
            damaged = replace(unit, children=damaged_children)
            with self.assertRaisesRegex(F0IError, "CANONICAL_RECONSTRUCTION_FAILED"):
                verify_reconstruction(damaged)
        finally:
            unit.wipe()

    def test_unit_chain_tamper_breaks_reconstruction(self) -> None:
        unit = _unit("chain")
        try:
            with self.assertRaisesRegex(F0IError, "CANONICAL_RECONSTRUCTION_FAILED"):
                verify_reconstruction(replace(unit, unit_chain_sha256=_SHA_F))
        finally:
            unit.wipe()

    def test_empty_leaf_sequence_is_rejected(self) -> None:
        with self.assertRaisesRegex(F0IError, "RESOURCE_LIMIT_EXCEEDED"):
            build_canonical_unit(_binding(), ())

    def test_more_than_4096_leaves_are_rejected(self) -> None:
        leaf = _leaf("")
        with self.assertRaisesRegex(F0IError, "RESOURCE_LIMIT_EXCEEDED"):
            build_leaf_blocks(_binding(), (leaf,) * 4097)

    def test_body_byte_limit_is_enforced_before_persistence(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_LIMIT"):
            build_canonical_unit(
                _binding(), (_leaf("中"),), maximum_bytes=2
            )

    def test_body_character_limit_is_enforced_before_persistence(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_LIMIT"):
            build_canonical_unit(
                _binding(), (_leaf("abc"),), maximum_characters=2
            )

    def test_frozen_chunk_minimum_cannot_be_loosened(self) -> None:
        body, blocks = build_leaf_blocks(_binding(), (_leaf("x"),))
        try:
            with self.assertRaisesRegex(F0IError, "CHUNK_RULE_INVALID"):
                build_parent_child_chunks(
                    _binding(), body, blocks, child_min_characters=299
                )
        finally:
            body.wipe()

    def test_frozen_chunk_maximum_cannot_be_widened(self) -> None:
        body, blocks = build_leaf_blocks(_binding(), (_leaf("x"),))
        try:
            with self.assertRaisesRegex(F0IError, "CHUNK_RULE_INVALID"):
                build_parent_child_chunks(
                    _binding(), body, blocks, child_max_characters=801
                )
        finally:
            body.wipe()

    def test_unpaired_surrogate_is_rejected(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_BODY_INVALID"):
            canonicalize_text("\ud800")

    def test_frozen_chunk_constants_are_300_and_800(self) -> None:
        self.assertEqual((CHILD_MIN_CHARACTERS, CHILD_MAX_CHARACTERS), (300, 800))


class F0IStructureContractTests(unittest.TestCase):
    def test_ocr_bbox_normalizes_top_left_pixels_to_ppm(self) -> None:
        evidence = ocr_bbox_to_ppm(
            ((0, 0), (100, 0), (100, 50), (0, 50)),
            render_width_px=200,
            render_height_px=100,
            page_rotation=0,
        )
        self.assertEqual(
            evidence.bbox_ppm,
            ((0, 0), (500000, 0), (500000, 500000), (0, 500000)),
        )
        self.assertEqual(evidence.coordinate_space, "TOP_LEFT_PPM")

    def test_ocr_bbox_marks_reading_order_as_candidate_only(self) -> None:
        evidence = ocr_bbox_to_ppm(
            ((1, 1), (3, 1), (3, 2), (1, 2)),
            render_width_px=3,
            render_height_px=3,
            page_rotation=90,
        )
        self.assertEqual(evidence.reading_order_status, "READING_ORDER_CANDIDATE")
        self.assertEqual(evidence.page_rotation, 90)

    def test_ocr_bbox_hash_binds_rotation(self) -> None:
        arguments = {
            "bbox": ((0, 0), (2, 0), (2, 2), (0, 2)),
            "render_width_px": 2,
            "render_height_px": 2,
        }
        zero = ocr_bbox_to_ppm(page_rotation=0, **arguments)
        ninety = ocr_bbox_to_ppm(page_rotation=90, **arguments)
        self.assertNotEqual(zero.location_sha256, ninety.location_sha256)

    def test_ocr_bbox_rejects_boolean_coordinate(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            ocr_bbox_to_ppm(
                ((False, 0), (2, 0), (2, 2), (0, 2)),
                render_width_px=2,
                render_height_px=2,
                page_rotation=0,
            )

    def test_ocr_bbox_rejects_out_of_bounds_coordinate(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            ocr_bbox_to_ppm(
                ((0, 0), (3, 0), (3, 2), (0, 2)),
                render_width_px=2,
                render_height_px=2,
                page_rotation=0,
            )

    def test_ocr_bbox_rejects_degenerate_quadrilateral(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            ocr_bbox_to_ppm(
                ((0, 0), (2, 0), (2, 0), (0, 0)),
                render_width_px=2,
                render_height_px=2,
                page_rotation=0,
            )

    def test_ocr_bbox_rejects_unregistered_rotation(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            ocr_bbox_to_ppm(
                ((0, 0), (2, 0), (2, 2), (0, 2)),
                render_width_px=2,
                render_height_px=2,
                page_rotation=45,
            )

    def test_native_geometry_is_explicitly_unavailable(self) -> None:
        evidence = native_geometry(page_rotation=270)
        self.assertEqual(evidence.location_status, "UNAVAILABLE")
        self.assertEqual(evidence.location_reason_code, "NATIVE_LAYOUT_NOT_CAPTURED")
        self.assertIsNone(evidence.bbox_ppm)

    def test_page_geometry_preserves_observed_boxes_and_rotation(self) -> None:
        evidence = page_geometry(
            media_box={"left": "0.000", "bottom": "0.000", "right": "612.000", "top": "792.000"},
            crop_box={"left": "1.000", "bottom": "2.000", "right": "611.000", "top": "790.000"},
            rotation=180,
        )
        record = evidence.to_record()
        self.assertEqual(record["rotation"], 180)
        self.assertEqual(record["crop_box"]["left"], "1.000")  # type: ignore[index]

    def test_page_geometry_rejects_unfixed_decimal_format(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            page_geometry(
                media_box={"left": "0", "bottom": "0.000", "right": "1.000", "top": "1.000"},
                crop_box={"left": "0.000", "bottom": "0.000", "right": "1.000", "top": "1.000"},
                rotation=0,
            )

    def test_page_geometry_rejects_inverted_box(self) -> None:
        with self.assertRaisesRegex(F0IError, "GEOMETRY_INVALID"):
            page_geometry(
                media_box={"left": "2.000", "bottom": "0.000", "right": "1.000", "top": "1.000"},
                crop_box={"left": "0.000", "bottom": "0.000", "right": "1.000", "top": "1.000"},
                rotation=0,
            )

    def test_docx_paragraph_locator_uses_observed_ordinals(self) -> None:
        location = docx_paragraph_location(
            structure_ordinal=1, block_ordinal=4, paragraph_ordinal=3
        )
        self.assertEqual(location.location_kind, "DOCX_PARAGRAPH")
        self.assertEqual(location.docx_paragraph_ordinal, 3)
        self.assertIsNone(location.docx_table_ordinal)

    def test_docx_table_locator_uses_observed_cell_coordinates(self) -> None:
        location = docx_table_cell_location(
            structure_ordinal=1,
            block_ordinal=5,
            table_ordinal=2,
            row_ordinal=3,
            cell_ordinal=4,
        )
        self.assertEqual(
            (location.docx_table_ordinal, location.docx_row_ordinal, location.docx_cell_ordinal),
            (2, 3, 4),
        )

    def test_docx_locator_rejects_zero_ordinal(self) -> None:
        with self.assertRaisesRegex(F0IError, "CANONICAL_CONTRACT_INVALID"):
            docx_paragraph_location(
                structure_ordinal=1, block_ordinal=0, paragraph_ordinal=1
            )

    def test_xlsx_sheet_locator_has_no_estimated_cell(self) -> None:
        location = xlsx_sheet_location(structure_ordinal=2, sheet_ordinal=3)
        self.assertEqual(location.location_kind, "XLSX_SHEET")
        self.assertIsNone(location.xlsx_row_ordinal)
        self.assertIsNone(location.xlsx_column_ordinal)

    def test_xlsx_cell_locator_uses_observed_coordinates(self) -> None:
        location = xlsx_cell_location(
            structure_ordinal=2,
            sheet_ordinal=3,
            row_ordinal=9,
            column_ordinal=7,
        )
        self.assertEqual(
            (location.xlsx_sheet_ordinal, location.xlsx_row_ordinal, location.xlsx_column_ordinal),
            (3, 9, 7),
        )

    def test_xlsx_formula_is_preserved_as_literal_and_never_executed(self) -> None:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        cell = ElementTree.fromstring(
            f'<c xmlns="{namespace}"><f>SUM(A1:A2)</f><v>3</v></c>'
        )
        self.assertEqual(
            _cell_text(
                cell,
                "n",
                (),
                f"{{{namespace}}}f",
                f"{{{namespace}}}v",
            ),
            "=SUM(A1:A2)\n3",
        )

    def test_xlsx_formula_without_cache_remains_observed_literal(self) -> None:
        namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        cell = ElementTree.fromstring(
            f'<c xmlns="{namespace}"><f>A1*2</f></c>'
        )
        self.assertEqual(
            _cell_text(
                cell,
                "n",
                (),
                f"{{{namespace}}}f",
                f"{{{namespace}}}v",
            ),
            "=A1*2",
        )

    def test_structure_unit_hash_is_stable(self) -> None:
        values = {
            "source_version_sha256": _SHA_A,
            "source_plan_sha256": _SHA_B,
            "structure_anchor_sha256": _SHA_C,
            "unit_kind": "DOCX_SECTION",
            "unit_ordinal": 1,
        }
        self.assertEqual(structure_unit_sha256(**values), structure_unit_sha256(**values))

    def test_structure_unit_hash_binds_sheet_ordinal(self) -> None:
        values = {
            "source_version_sha256": _SHA_A,
            "source_plan_sha256": _SHA_B,
            "structure_anchor_sha256": _SHA_C,
            "unit_kind": "XLSX_SHEET",
        }
        self.assertNotEqual(
            structure_unit_sha256(unit_ordinal=1, **values),
            structure_unit_sha256(unit_ordinal=2, **values),
        )

    def test_pdf_table_status_is_honestly_unresolved(self) -> None:
        self.assertEqual(
            pdf_table_status(),
            {
                "table_status": "UNRESOLVED",
                "table_reason_code": "PDF_TABLE_MODEL_NOT_IN_SCOPE",
            },
        )

    def test_artifact_proof_accepts_only_registered_full_aggregates(self) -> None:
        full = _artifact_replay("full", rows=1, calls=19)
        proof = _replay_proof(full, "full")
        self.assertEqual(proof["documents"], 26)
        self.assertEqual(proof["ocr_visual_units"], 24)

    def test_artifact_sequence_requires_zero_second_full_delta(self) -> None:
        smoke = _artifact_replay("smoke", rows=1, calls=5)
        first = _artifact_replay("full", rows=1, calls=19)
        second = _artifact_replay("full", rows=1, calls=1)
        proof = _replay_proof(first, "full")
        with self.assertRaisesRegex(F0IError, "ARTIFACT_GENERATION_FAILED"):
            _verify_sequence(smoke, first, second, proof, proof)

    def test_artifact_sequence_accepts_idempotent_rebuild(self) -> None:
        smoke = _artifact_replay("smoke", rows=0, calls=0)
        first = _artifact_replay("full", rows=0, calls=0)
        second = _artifact_replay("full", rows=0, calls=0)
        proof = _replay_proof(first, "full")
        _verify_sequence(smoke, first, second, proof, proof)

    def test_artifact_proof_rejects_search_ready_promotion(self) -> None:
        full = _artifact_replay("full", rows=1, calls=19)
        full["search_status"] = "SEARCH_READY"
        with self.assertRaisesRegex(F0IError, "ARTIFACT_GENERATION_FAILED"):
            _replay_proof(full, "full")

    def test_status_html_has_no_remote_resource(self) -> None:
        smoke = _replay_proof(
            _artifact_replay("smoke", rows=1, calls=5), "smoke"
        )
        full = _replay_proof(
            _artifact_replay("full", rows=1, calls=19), "full"
        )
        html = _status_html(smoke, full)
        self.assertNotIn(b"http://", html)
        self.assertNotIn(b"https://", html)
        self.assertIn(b"SEARCH_NOT_READY", html)

    def test_actual_status_html_passes_public_payload_guard(self) -> None:
        smoke = _replay_proof(
            _artifact_replay("smoke", rows=0, calls=0), "smoke"
        )
        full = _replay_proof(
            _artifact_replay("full", rows=0, calls=0), "full"
        )
        _validate_public_payloads(
            {
                "acceptance.json": b"{}\n",
                "sbom.json": b"{}\n",
                "status.html": _status_html(smoke, full),
            }
        )

    def test_artifact_unsafe_late_target_keeps_earlier_files_unchanged(self) -> None:
        payloads = {
            "acceptance.json": b'{"status":"new"}\n',
            "sbom.json": b'{"status":"new"}\n',
            "status.html": b"<html>new</html>\n",
        }
        with tempfile.TemporaryDirectory(dir=_PRIVATE_TMP) as directory:
            root = Path(directory) / "outputs"
            root.mkdir(mode=0o700)
            acceptance = root / "acceptance.json"
            sbom = root / "sbom.json"
            status = root / "status.html"
            acceptance.write_bytes(b"old-acceptance\n")
            sbom.write_bytes(b"old-sbom\n")
            status.write_bytes(b"old-status\n")
            for path in (acceptance, sbom, status):
                path.chmod(0o600)
            before = (acceptance.read_bytes(), sbom.read_bytes())
            status.unlink()
            os.mkfifo(status, 0o600)
            with self.assertRaisesRegex(F0IError, "ARTIFACT_GENERATION_FAILED"):
                _atomic_write_all_at(root, payloads)
            self.assertEqual(
                (acceptance.read_bytes(), sbom.read_bytes()), before
            )
            self.assertTrue(stat.S_ISFIFO(os.lstat(status).st_mode))

    def test_artifact_phone_heuristic_ignores_exact_sha256_tokens(self) -> None:
        digest = b"13812345678" + b"a" * 53
        payloads = {
            "acceptance.json": b'{"sha256":"' + digest + b'"}\n',
            "sbom.json": b"{}\n",
            "status.html": b"<html></html>\n",
        }
        _validate_public_payloads(payloads)

    def test_artifact_phone_heuristic_rejects_non_hash_phone(self) -> None:
        payloads = {
            "acceptance.json": b'{"contact":"13812345678"}\n',
            "sbom.json": b"{}\n",
            "status.html": b"<html></html>\n",
        }
        with self.assertRaisesRegex(F0IError, "ARTIFACT_GENERATION_FAILED"):
            _validate_public_payloads(payloads)


class F0IKeyLockAndConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        token = uuid.uuid4().hex[:16]
        self.key_path = _f0i_private_path(token, ".key")
        self.other_key_path = _f0i_private_path(token + "-other", ".key")
        self.lock_path = _f0i_private_path(token, ".lock")
        self.other_lock_path = _f0i_private_path(token + "-other", ".lock")

    def tearDown(self) -> None:
        for value in (
            self.other_key_path,
            self.key_path,
            self.other_lock_path,
            self.lock_path,
        ):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(value)

    def test_created_key_is_owner_only_regular_32_bytes(self) -> None:
        create_keyfile(self.key_path)
        metadata = os.lstat(self.key_path)
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual((metadata.st_size, metadata.st_nlink), (32, 1))

    def test_loaded_key_fingerprint_matches_creation(self) -> None:
        fingerprint = create_keyfile(self.key_path)
        with load_keyfile(self.key_path) as key:
            self.assertEqual(key.fingerprint_sha256, fingerprint)
            self.assertTrue(key.view().readonly)

    def test_key_repr_never_exposes_material(self) -> None:
        create_keyfile(self.key_path)
        with load_keyfile(self.key_path) as key:
            rendered = repr(key)
            self.assertNotIn(key.view().tobytes().hex(), rendered)

    def test_key_creation_refuses_existing_target(self) -> None:
        create_keyfile(self.key_path)
        with self.assertRaisesRegex(F0IError, "KEYFILE_ALREADY_EXISTS"):
            create_keyfile(self.key_path)

    def test_key_loader_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(F0IError, "KEYFILE_INVALID"):
            load_keyfile("anhuan-f0i-relative.key")

    def test_key_loader_rejects_wrong_mode(self) -> None:
        create_keyfile(self.key_path)
        os.chmod(self.key_path, 0o640)
        with self.assertRaisesRegex(F0IError, "KEYFILE_INVALID"):
            load_keyfile(self.key_path)

    def test_key_loader_rejects_symlink(self) -> None:
        create_keyfile(self.key_path)
        os.symlink(self.key_path, self.other_key_path)
        with self.assertRaises(F0IError):
            load_keyfile(self.other_key_path)

    def test_key_loader_rejects_hardlink(self) -> None:
        create_keyfile(self.key_path)
        os.link(self.key_path, self.other_key_path)
        with self.assertRaisesRegex(F0IError, "KEYFILE_INVALID"):
            load_keyfile(self.key_path)

    def test_key_loader_rejects_fifo(self) -> None:
        os.mkfifo(self.key_path, 0o600)
        with self.assertRaisesRegex(F0IError, "KEYFILE_INVALID"):
            load_keyfile(self.key_path)

    def test_host_lock_acquires_and_releases(self) -> None:
        lock = HostReplayLock(self.lock_path)
        lock.acquire()
        self.assertTrue(lock.held)
        lock.release()
        self.assertFalse(lock.held)

    def test_host_lock_rejects_double_acquire(self) -> None:
        lock = HostReplayLock(self.lock_path)
        try:
            lock.acquire()
            with self.assertRaisesRegex(F0IError, "LOCK_INVALID"):
                lock.acquire()
        finally:
            lock.release()

    def test_host_lock_blocks_second_process(self) -> None:
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_lock_probe, args=(self.lock_path, send))
        with HostReplayLock(self.lock_path):
            process.start()
            send.close()
            self.assertTrue(receive.poll(10))
            self.assertEqual(receive.recv(), "LOCK_UNAVAILABLE")
            process.join(10)
        self.assertEqual(process.exitcode, 0)

    def test_host_lock_can_be_reacquired_by_new_process_after_release(self) -> None:
        with HostReplayLock(self.lock_path) as lock:
            self.assertTrue(lock.held)
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_lock_probe, args=(self.lock_path, send))
        process.start()
        send.close()
        self.assertTrue(receive.poll(10))
        self.assertEqual(receive.recv(), "ACQUIRED")
        process.join(10)
        self.assertEqual(process.exitcode, 0)

    def test_host_lock_rejects_symlink(self) -> None:
        Path(self.lock_path).touch(mode=0o600)
        os.symlink(self.lock_path, self.other_lock_path)
        with self.assertRaisesRegex(F0IError, "LOCK_INVALID"):
            HostReplayLock(self.other_lock_path).acquire()

    def test_host_lock_rejects_hardlink(self) -> None:
        Path(self.lock_path).touch(mode=0o600)
        os.link(self.lock_path, self.other_lock_path)
        with self.assertRaisesRegex(F0IError, "LOCK_INVALID"):
            HostReplayLock(self.lock_path).acquire()

    def test_host_lock_rejects_fifo(self) -> None:
        os.mkfifo(self.lock_path, 0o600)
        with self.assertRaisesRegex(F0IError, "LOCK_INVALID"):
            HostReplayLock(self.lock_path).acquire()

    def test_host_lock_rejects_non_fixed_directory(self) -> None:
        with self.assertRaisesRegex(F0IError, "LOCK_INVALID"):
            HostReplayLock("/tmp/not-the-fixed-lock.lock")

    def test_database_config_accepts_exact_scratch_identity(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        self.assertIs(validate_local_database_config(config), config)

    def test_database_config_rejects_wrong_role(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        broken = replace(
            config,
            worker_dsn=config.worker_dsn.replace("f0d_worker", "f0d_runtime", 1),
        )
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(broken)

    def test_database_config_rejects_remote_host(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        broken = replace(
            config,
            runtime_dsn=config.runtime_dsn.replace("127.0.0.1", "192.0.2.10"),
        )
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(broken)

    def test_database_config_rejects_wrong_port(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        port = (
            str(_FROZEN_F0_ISOLATION.postgres_port)
            if _FROZEN_F0_ISOLATION is not None
            else "55432"
        )
        broken = replace(
            config,
            migration_dsn=config.migration_dsn.replace(port, "5432"),
        )
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(broken)

    def test_database_config_rejects_query_parameters(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        broken = replace(config, runtime_dsn=config.runtime_dsn + "?sslmode=disable")
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(broken)

    def test_database_config_rejects_cross_database_roles(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        broken = replace(
            config,
            worker_dsn=config.worker_dsn.replace(
                _database_name("f0i_test_0123456789abcdef"),
                _database_name("f0i_test_fedcba9876543210"),
            ),
        )
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(broken)

    def test_database_config_constructor_rejects_unregistered_name(self) -> None:
        with self.assertRaisesRegex(F0IError, "DATABASE_CONFIGURATION_INVALID"):
            database_config("customer_production")

    def test_database_config_error_does_not_render_dsn(self) -> None:
        config = _database_config("f0i_test_0123456789abcdef")
        secret = "SYNTHETIC_DSN_SECRET"
        broken = replace(config, migration_dsn=config.migration_dsn + secret)
        with self.assertRaises(F0IError) as raised:
            validate_local_database_config(broken)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("postgresql", repr(raised.exception.to_dict()))


class F0IDatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0i-migration")
            if _FROZEN_F0_ISOLATION is not None
            else "f0i_test_" + uuid.uuid4().hex[:16]
        )
        try:
            cls.created = ensure_database(cls.database_name)
            cls.config = database_config(cls.database_name)
        except Exception:
            drop_scratch_database(cls.database_name)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        drop_scratch_database(cls.database_name)

    def test_scratch_database_is_fresh_clone(self) -> None:
        self.assertTrue(self.created)

    def test_migration_revision_is_linear_0006(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT version_num FROM f0d.alembic_version"
            ).fetchone()[0]
        self.assertEqual(value, "f0d_0006")

    def test_schema_has_exact_seven_canonical_tables(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='f0i' "
                "ORDER BY tablename"
            ).fetchall()
        self.assertEqual(
            [row[0] for row in rows],
            [
                "block",
                "chunk",
                "chunk_block_link",
                "configuration",
                "document_scope",
                "page",
                "run",
            ],
        )

    def test_all_canonical_tables_force_rls(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT relname,relrowsecurity,relforcerowsecurity "
                "FROM pg_class JOIN pg_namespace ON pg_namespace.oid=relnamespace "
                "WHERE nspname='f0i' AND relkind='r' ORDER BY relname"
            ).fetchall()
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row[1:] == (True, True) for row in rows))

    def test_every_table_has_update_delete_and_truncate_guards(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT c.relname,count(*) FROM pg_trigger t "
                "JOIN pg_class c ON c.oid=t.tgrelid "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='f0i' AND NOT t.tgisinternal "
                "AND t.tgname IN ('reject_immutable_row_mutation',"
                "'reject_immutable_truncate') GROUP BY c.relname ORDER BY c.relname"
            ).fetchall()
        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row[1] == 2 for row in rows))

    def test_public_has_no_schema_or_table_privileges(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            values = connection.execute(
                "SELECT has_schema_privilege('public','f0i','USAGE'),"
                "has_table_privilege('public','f0i.block','SELECT,INSERT,UPDATE,DELETE'),"
                "has_table_privilege('public','f0i.chunk','SELECT,INSERT,UPDATE,DELETE')"
            ).fetchone()
        self.assertEqual(values, (False, False, False))

    def test_runtime_has_no_direct_canonical_table_read_capability(self) -> None:
        with psycopg.connect(self.config.runtime_dsn) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute("SELECT count(*) FROM f0i.block").fetchone()

    def test_body_storage_columns_are_ciphertext_bytea_only(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT table_name,column_name,data_type FROM information_schema.columns "
                "WHERE table_schema='f0i' AND column_name LIKE '%body%' "
                "ORDER BY table_name,column_name"
            ).fetchall()
        body_payloads = [row for row in rows if row[1] in {"body_ciphertext"}]
        self.assertEqual(body_payloads, [("block", "body_ciphertext", "bytea"), ("chunk", "body_ciphertext", "bytea")])
        self.assertFalse(any(row[1] in {"body", "body_text", "raw_text", "plaintext"} for row in rows))

    def test_composite_foreign_keys_bind_tenant_and_version(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            definitions = "\n".join(
                row[0]
                for row in connection.execute(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE connamespace='f0i'::regnamespace AND contype='f'"
                ).fetchall()
            )
        self.assertIn("enterprise_id", definitions)
        self.assertIn("configuration_sha256", definitions)
        self.assertIn("document_version_id", definitions)
        self.assertIn("document_scope_id", definitions)

    def test_pgcrypto_roundtrip_preserves_multibyte_bytes(self) -> None:
        plaintext = "SYNTHETIC中🙂".encode()
        key = bytes(range(32)).hex()
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT f0f_crypto.pgp_sym_decrypt_bytea("
                "f0f_crypto.pgp_sym_encrypt_bytea(%s::bytea,%s,"
                "'cipher-algo=aes256,compress-algo=0'),%s,"
                "'cipher-algo=aes256,compress-algo=0')",
                (plaintext, key, key),
            ).fetchone()[0]
        self.assertEqual(bytes(value), plaintext)

    def test_pgcrypto_ciphertext_tamper_is_rejected(self) -> None:
        key = bytes(range(32)).hex()
        connection = psycopg.connect(self.config.migration_dsn)
        try:
            ciphertext = connection.execute(
                "SELECT f0f_crypto.pgp_sym_encrypt_bytea(%s::bytea,%s,"
                "'cipher-algo=aes256,compress-algo=0')",
                (b"tamper-probe", key),
            ).fetchone()[0]
            damaged = bytearray(ciphertext)
            damaged[0] = (damaged[0] + 1) % 256
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    "SELECT f0f_crypto.pgp_sym_decrypt_bytea(%s::bytea,%s,"
                    "'cipher-algo=aes256,compress-algo=0')",
                    (damaged, key),
                ).fetchone()
        finally:
            connection.close()

    def test_bbox_validator_accepts_only_numeric_ppm_quadrilateral(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            values = connection.execute(
                "SELECT f0i.valid_bbox_ppm(%s::jsonb),f0i.valid_bbox_ppm(%s::jsonb)",
                (
                    "[[0,0],[1000000,0],[1000000,1000000],[0,1000000]]",
                    '[[0,0],[1000001,0],[1,1],[0,1]]',
                ),
            ).fetchone()
        self.assertEqual(values, (True, False))

    def test_append_only_truncate_is_rejected_even_when_empty(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute("TRUNCATE f0i.chunk_block_link")

    def test_database_bootstrap_is_idempotent(self) -> None:
        self.assertFalse(ensure_database(self.database_name))

    def test_sbom_database_versions_match_live_components(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            server_version = str(
                connection.execute("SHOW server_version").fetchone()[0]
            ).split()[0]
            extension_version = str(
                connection.execute(
                    "SELECT extversion FROM pg_extension WHERE extname='pgcrypto'"
                ).fetchone()[0]
            )
        components = {
            str(item["name"]): str(item["version"])
            for item in _sbom(load_runtime_bundle(), {})["components"]
            if item.get("name") in {"PostgreSQL", "pgcrypto"}
        }
        self.assertEqual(components["PostgreSQL"], server_version)
        self.assertEqual(components["pgcrypto"], extension_version)


class _RollbackPersistenceProbe(Exception):
    """Sentinel used to force an integration transaction rollback."""


class F0IProductionPersistenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        token = uuid.uuid4().hex[:16]
        cls.database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0i-persistence")
            if _FROZEN_F0_ISOLATION is not None
            else "f0i_test_" + token
        )
        cls.key_path = _f0i_private_path("persist-" + token, ".key")
        cls.created = False
        try:
            cls.created = ensure_database(cls.database_name)
            cls.config = database_config(cls.database_name)
            create_keyfile(cls.key_path)
            cls.context = authenticate_local_session(
                cls.config, LOCAL_TENANT_A_TOKEN
            )
            cls.bundle = load_runtime_bundle()
            cls.plan = load_registered_plan("smoke")
            cls.full_plan = load_registered_plan("full")
            entries = cls.plan.payload.get("entries")
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                raise AssertionError("registered smoke entries invalid")
            cls.entries = tuple(entries)
            with role_transaction(cls.config, "f0d_migration") as connection:
                cls.sources = load_source_documents(
                    connection, cls.context, cls.entries
                )
                _bind_manifests(cls.plan, cls.sources)
            with load_keyfile(cls.key_path) as key:
                cls.identity = configuration_identity(
                    cls.context,
                    cls.bundle,
                    full_plan_sha256=cls.full_plan.page_plan_sha256,
                    key_fingerprint_sha256=key.fingerprint_sha256,
                )
        except Exception:
            drop_scratch_database(cls.database_name)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(cls.key_path)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        drop_scratch_database(cls.database_name)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(cls.key_path)

    @classmethod
    def _synthetic_ocr_result(
        cls, entry: dict[str, object], page: dict[str, object]
    ) -> dict[str, object]:
        document_type = str(entry["type"])
        page_no = int(page["page_no"])
        empty_page = document_type == "PDF" and page_no == 1
        text = "SYNTHETIC OCR BODY"
        if empty_page:
            blocks: list[dict[str, object]] = []
            body = ""
        elif document_type == "PDF" and page_no == 2:
            # The trailing observed zero-width leaf must remain a block but
            # must not become a positive child/block intersection.
            blocks = [
                {
                    "index": 0,
                    "text": text,
                    "bbox": [[100, 100], [900, 100], [900, 200], [100, 200]],
                    "confidence_ppm": 900000,
                },
                {
                    "index": 1,
                    "text": "",
                    "bbox": [[100, 300], [900, 300], [900, 400], [100, 400]],
                    "confidence_ppm": 800000,
                },
            ]
            body = text + "\n"
        else:
            blocks = [
                {
                    "index": 0,
                    "text": text,
                    "bbox": [[100, 100], [900, 100], [900, 200], [100, 200]],
                    "confidence_ppm": 900000,
                }
            ]
            body = text
        if document_type == "PDF":
            renderer = {
                "name": "pypdfium2",
                "version": "5.12.1",
                "pdfium_version": "152.0.7947.0",
            }
            origin = "PDFIUM_250_DPI"
            dpi: int | None = 250
        else:
            renderer = {"name": "opencv-imdecode", "version": "5.0.0.93"}
            origin = "JPEG_DECODED_SOURCE_PIXELS"
            dpi = None
        render_identity = (
            document_type + ":" + str(page["page_id"])
        ).encode("ascii")
        return {
            "document_type": document_type,
            "source_unit_id": page["page_id"],
            "render_width_px": 1000,
            "render_height_px": 1000,
            "render_origin": origin,
            "render_dpi": dpi,
            "renderer": renderer,
            "render_sha256": hashlib.sha256(render_identity).hexdigest(),
            "ocr_text_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "blocks": blocks,
        }

    @classmethod
    def _build_smoke_documents(cls) -> tuple[list[DocumentRecord], int]:
        non_visual = tuple(
            (index, entry, source)
            for index, (entry, source) in enumerate(
                zip(cls.entries, cls.sources, strict=True)
            )
            if source.document_type in {"DOC", "DOCX", "XLSX"}
        )
        documents, calls = _build_documents(
            cls.plan,
            non_visual,
            context=cls.context,
            bundle=cls.bundle,
        )
        if calls != 0:
            _wipe_document_records(documents)
            raise AssertionError("structure-only fixture invoked OCR")
        try:
            for index, (entry, source) in enumerate(
                zip(cls.entries, cls.sources, strict=True)
            ):
                if source.document_type not in {"PDF", "JPEG"}:
                    continue
                raw_pages = entry.get("pages")
                if not isinstance(raw_pages, list) or any(
                    not isinstance(page, dict) for page in raw_pages
                ):
                    raise AssertionError("registered visual pages invalid")
                pages = tuple(raw_pages)
                units = []
                with open_registered_source(cls.plan, index) as registered:
                    native_pages = tuple(
                        page
                        for page in pages
                        if page.get("decision") == "NATIVE_CANDIDATE"
                    )
                    native = (
                        extract_native_pdf_pages(registered, entry, native_pages)
                        if source.document_type == "PDF" and native_pages
                        else ()
                    )
                    native_by_ordinal = {
                        item.unit_ordinal: item for item in native
                    }
                    for page, source_page in zip(
                        pages, source.pages, strict=True
                    ):
                        if source_page.candidate_decision == "NATIVE_CANDIDATE":
                            observation = native_by_ordinal[source_page.page_no]
                            render = None
                        else:
                            result = cls._synthetic_ocr_result(entry, page)
                            observation = observation_from_ocr_result(
                                entry, page, result
                            )
                            render = _render_evidence(source.document_type, result)
                            calls += 1
                        units.append(
                            _canonical_unit(
                                cls.context,
                                source,
                                observation,
                                cls.bundle,
                                source_page,
                                None,
                                render=render,
                            )
                        )
                documents.append(
                    DocumentRecord(
                        source=source,
                        scope_kind="VISUAL",
                        units=tuple(units),
                    )
                )
            ordinal = {
                source.document_version_id: index
                for index, source in enumerate(cls.sources)
            }
            documents.sort(key=lambda item: ordinal[item.source.document_version_id])
            return documents, calls
        except Exception:
            _wipe_document_records(documents)
            raise

    def test_registered_smoke_persistence_validates_every_draft_and_rolls_back_tamper(
        self,
    ) -> None:
        self.assertTrue(self.created)
        documents, ocr_calls = self._build_smoke_documents()
        try:
            summary = _anticipated_summary(
                self.entries,
                self.sources,
                documents,
                existing_summary=None,
            )
            run_input = RunInput(
                profile="smoke",
                input_manifest_sha256=_input_manifest_sha256(self.plan),
                input_summary_sha256=canonical_sha256(
                    self.plan.payload.get("summary")
                ),
                requested_document_count=len(self.entries),
            )
            summary_sha256 = canonical_sha256(summary)
            with self.assertRaises(_RollbackPersistenceProbe):
                with load_keyfile(self.key_path) as key:
                    material = key.view()
                    try:
                        with role_transaction(
                            self.config, "f0d_migration"
                        ) as connection:
                            persisted = persist_run(
                                connection,
                                self.context,
                                self.identity,
                                self.bundle,
                                material,
                                run_input,
                                summary_sha256,
                                summary,
                                documents,
                                ocr_call_count=ocr_calls,
                            )
                            validate_persisted_records(
                                connection,
                                self.context,
                                self.identity,
                                documents,
                            )
                            actual = database_summary(
                                connection,
                                self.context,
                                self.identity,
                                self.sources,
                                material,
                            )
                            validate_persisted_run(
                                connection,
                                self.context,
                                self.identity,
                                run_input,
                                summary_sha256,
                                actual,
                                ocr_call_count=ocr_calls,
                            )
                            self.assertEqual(actual, summary)
                            self.assertGreater(persisted.rows_inserted, 0)
                            self.assertEqual(ocr_calls, 5)
                            evidence = connection.execute(
                                "SELECT "
                                "count(*) FILTER (WHERE evidence_method='PYPDF_NATIVE') AS native,"
                                "count(*) FILTER (WHERE block_kind='OCR_TEXT_BLOCK') AS ocr_text,"
                                "count(*) FILTER (WHERE block_kind='OCR_EMPTY_PAGE') AS ocr_empty,"
                                "count(*) FILTER (WHERE evidence_method='DOCX_XML') AS docx,"
                                "count(*) FILTER (WHERE evidence_method='XLSX_CELL_XML') AS xlsx "
                                "FROM f0i.block"
                            ).fetchone()
                            self.assertIsNotNone(evidence)
                            self.assertGreater(int(evidence["native"]), 0)
                            self.assertGreater(int(evidence["ocr_text"]), 0)
                            self.assertEqual(int(evidence["ocr_empty"]), 1)
                            self.assertGreater(int(evidence["docx"]), 0)
                            self.assertGreater(int(evidence["xlsx"]), 0)
                            jpeg = connection.execute(
                                "SELECT count(*) AS count FROM f0i.page p "
                                "JOIN f0i.document_scope s ON s.id=p.document_scope_id "
                                "AND s.enterprise_id=p.enterprise_id "
                                "WHERE s.document_type='JPEG' "
                                "AND p.selected_route='LOCAL_OCR'"
                            ).fetchone()
                            self.assertEqual(int(jpeg["count"]), 1)
                            parents = connection.execute(
                                "SELECT count(*) AS total,count(*) FILTER "
                                "(WHERE chunk_ordinal=0) AS zeroes FROM f0i.chunk "
                                "WHERE chunk_level='PARENT'"
                            ).fetchone()
                            self.assertEqual(parents["total"], parents["zeroes"])
                            zero_text_links = connection.execute(
                                "SELECT count(*) AS count FROM f0i.chunk_block_link l "
                                "JOIN f0i.block b ON b.id=l.block_id "
                                "AND b.enterprise_id=l.enterprise_id "
                                "WHERE b.block_kind='OCR_TEXT_BLOCK' "
                                "AND b.body_plaintext_size_bytes=0"
                            ).fetchone()
                            self.assertEqual(int(zero_text_links["count"]), 0)
                            adjacent = connection.execute(
                                "SELECT b.enterprise_id,b.configuration_id,"
                                "b.configuration_sha256,b.document_scope_id,"
                                "b.first_run_id,b.document_version_id,"
                                "b.processing_plan_id,b.container_id,"
                                "c.id AS chunk_id,b.id AS block_id,"
                                "c.unit_chain_sha256,"
                                "greatest(c.span_start_byte,b.span_start_byte) AS sb,"
                                "least(c.span_end_byte,b.span_end_byte) AS eb,"
                                "greatest(c.span_start_character,"
                                "b.span_start_character) AS sc,"
                                "least(c.span_end_character,"
                                "b.span_end_character) AS ec,"
                                "COALESCE((SELECT max(link_ordinal)+1 FROM "
                                "f0i.chunk_block_link l WHERE l.chunk_id=c.id),1) "
                                "AS ordinal FROM f0i.block b JOIN f0i.chunk c "
                                "ON c.enterprise_id=b.enterprise_id "
                                "AND c.document_scope_id=b.document_scope_id "
                                "AND c.container_id=b.container_id "
                                "WHERE b.block_kind='OCR_TEXT_BLOCK' "
                                "AND b.body_plaintext_size_bytes=0 "
                                "AND c.chunk_level='CHILD' "
                                "AND c.body_plaintext_size_bytes>0 "
                                "AND greatest(c.span_start_byte,b.span_start_byte)="
                                "least(c.span_end_byte,b.span_end_byte) LIMIT 1"
                            ).fetchone()
                            self.assertIsNotNone(adjacent)
                            with self.assertRaises(
                                psycopg.errors.CheckViolation
                            ) as rejected:
                                with connection.transaction():
                                    connection.execute(
                                        "INSERT INTO f0i.chunk_block_link("
                                        "id,enterprise_id,configuration_id,"
                                        "configuration_sha256,document_scope_id,"
                                        "first_run_id,document_version_id,"
                                        "processing_plan_id,container_id,chunk_id,"
                                        "block_id,link_ordinal,"
                                        "intersection_start_byte,"
                                        "intersection_end_byte,"
                                        "intersection_start_character,"
                                        "intersection_end_character,"
                                        "unit_chain_sha256) VALUES ("
                                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                                        "%s,%s,%s,%s,%s)",
                                        (
                                            uuid.uuid4(),
                                            adjacent["enterprise_id"],
                                            adjacent["configuration_id"],
                                            adjacent["configuration_sha256"],
                                            adjacent["document_scope_id"],
                                            adjacent["first_run_id"],
                                            adjacent["document_version_id"],
                                            adjacent["processing_plan_id"],
                                            adjacent["container_id"],
                                            adjacent["chunk_id"],
                                            adjacent["block_id"],
                                            adjacent["ordinal"],
                                            adjacent["sb"],
                                            adjacent["eb"],
                                            adjacent["sc"],
                                            adjacent["ec"],
                                            adjacent["unit_chain_sha256"],
                                        ),
                                    )
                            self.assertEqual(rejected.exception.sqlstate, "23514")
                            empty_page_links = connection.execute(
                                "SELECT count(*) AS count FROM f0i.chunk_block_link l "
                                "JOIN f0i.block b ON b.id=l.block_id "
                                "AND b.enterprise_id=l.enterprise_id "
                                "WHERE b.block_kind='OCR_EMPTY_PAGE'"
                            ).fetchone()
                            self.assertEqual(int(empty_page_links["count"]), 1)

                            raise _RollbackPersistenceProbe()
                    finally:
                        material.release()
            with role_transaction(self.config, "f0d_migration") as connection:
                set_tenant_context(connection, self.context)
                residual = connection.execute(
                    "SELECT (SELECT count(*) FROM f0i.configuration) "
                    "+ (SELECT count(*) FROM f0i.run) "
                    "+ (SELECT count(*) FROM f0i.document_scope) "
                    "+ (SELECT count(*) FROM f0i.page) "
                    "+ (SELECT count(*) FROM f0i.block) "
                    "+ (SELECT count(*) FROM f0i.chunk) "
                    "+ (SELECT count(*) FROM f0i.chunk_block_link) AS count"
                ).fetchone()
            self.assertEqual(int(residual["count"]), 0)

            original_document = next(
                item for item in documents if item.units
            )
            original_unit = original_document.units[0]
            original_block = original_unit.canonical.blocks[0]
            damaged_chain = (
                _SHA_F
                if original_block.chain_sha256 != _SHA_F
                else _SHA_E
            )
            damaged_canonical = replace(
                original_unit.canonical,
                blocks=(
                    replace(original_block, chain_sha256=damaged_chain),
                    *original_unit.canonical.blocks[1:],
                ),
            )
            damaged_unit = replace(
                original_unit, canonical=damaged_canonical
            )
            damaged_document = replace(
                original_document,
                units=(damaged_unit, *original_document.units[1:]),
            )
            damaged_documents = [
                damaged_document if item is original_document else item
                for item in documents
            ]
            with self.assertRaisesRegex(F0IError, "REPLAY_MISMATCH"):
                with load_keyfile(self.key_path) as key:
                    material = key.view()
                    try:
                        with role_transaction(
                            self.config, "f0d_migration"
                        ) as connection:
                            persist_run(
                                connection,
                                self.context,
                                self.identity,
                                self.bundle,
                                material,
                                run_input,
                                summary_sha256,
                                summary,
                                damaged_documents,
                                ocr_call_count=ocr_calls,
                            )
                            validate_persisted_records(
                                connection,
                                self.context,
                                self.identity,
                                documents,
                            )
                    finally:
                        material.release()
            with role_transaction(self.config, "f0d_migration") as connection:
                set_tenant_context(connection, self.context)
                after_tamper = connection.execute(
                    "SELECT count(*) AS count FROM f0i.configuration"
                ).fetchone()
            self.assertEqual(int(after_tamper["count"]), 0)
        finally:
            _wipe_document_records(documents)

    def test_registered_smoke_selection_is_subset_not_full_prefix(self) -> None:
        smoke_entries = _registered_entries(self.plan)
        full_entries = _registered_entries(self.full_plan)
        smoke_ids = {entry["document_id"] for entry in smoke_entries}
        full_ids = {entry["document_id"] for entry in full_entries}
        prefix_ids = {
            entry["document_id"] for entry in full_entries[: len(smoke_entries)]
        }
        self.assertEqual(len(smoke_entries), 10)
        self.assertEqual(len(smoke_ids), 10)
        self.assertTrue(smoke_ids < full_ids)
        self.assertNotEqual(smoke_ids, prefix_ids)


if __name__ == "__main__":
    unittest.main()
