from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


_ROOT = Path(__file__).resolve().parents[1]
_ORDER = (
    "valid_exit",
    "config_tamper_exit",
    "span_tamper_exit",
    "restored_exit",
    "tenant_version_crosswires",
    "orphan_blocks",
    "orphan_chunks",
    "plaintext_leaks",
    "external_calls",
    "search_calls",
    "concurrent_ocr_runs",
    "upstream_mutations",
    "container_residuals",
)
_EXPECTED = (0, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
_TABLES = (
    "configuration",
    "run",
    "document_scope",
    "page",
    "block",
    "chunk",
    "chunk_block_link",
)
_UPSTREAM_SCHEMAS = ("f0d", "f0e", "f0f", "f0g")
_PERSISTENCE_TARGETS = (
    "migrations/versions/f0d_0006_canonical_chunks.py",
    "src/platform_foundation/f0i",
    "tests/test_f0i_canonical_chunks.py",
    "tests/f0i_reverse_verify.py",
    "artifacts/f0i-canonical-chunks/v0.1",
    "PROGRESS.md",
    "BLOCKED.md",
)
_EXTERNAL_IMPORTS = frozenset(
    {"anthropic", "boto3", "httpx", "openai", "requests", "socket", "urllib"}
)
_SEARCH_IMPORTS = frozenset(
    {
        "chromadb",
        "elasticsearch",
        "faiss",
        "opensearch",
        "opensearchpy",
        "pinecone",
        "qdrant_client",
        "weaviate",
    }
)
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


def _empty_metrics() -> dict[str, int]:
    return {
        "valid_exit": 2,
        "config_tamper_exit": 0,
        "span_tamper_exit": 0,
        "restored_exit": 2,
        "tenant_version_crosswires": 1,
        "orphan_blocks": 1,
        "orphan_chunks": 1,
        "plaintext_leaks": 1,
        "external_calls": 1,
        "search_calls": 1,
        "concurrent_ocr_runs": 1,
        "upstream_mutations": 1,
        "container_residuals": 1,
    }


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _database_dsn(database_name: str) -> str:
    from platform_foundation.f0i.bootstrap import _bootstrap_dsn

    return make_conninfo(_bootstrap_dsn(), dbname=database_name)


def _upstream_fingerprint() -> str:
    """Hash source rows server-side and return only opaque table digests."""

    from platform_foundation.f0i.config import SOURCE_DATABASE

    material: list[tuple[str, str, int, str]] = []
    with psycopg.connect(
        _database_dsn(SOURCE_DATABASE), row_factory=dict_row
    ) as connection:
        rows = connection.execute(
            "SELECT n.nspname AS schema_name,c.relname AS table_name "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname=ANY(%s) "
            "AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname",
            (list(_UPSTREAM_SCHEMAS),),
        ).fetchall()
        for row in rows:
            statement = sql.SQL(
                "SELECT count(*) AS rows,md5(COALESCE(string_agg("
                "md5(to_jsonb(t)::text),'' ORDER BY md5(to_jsonb(t)::text)),'')) "
                "AS digest FROM {}.{} AS t"
            ).format(
                sql.Identifier(str(row["schema_name"])),
                sql.Identifier(str(row["table_name"])),
            )
            observed = connection.execute(statement).fetchone()
            if observed is None:
                raise RuntimeError("UPSTREAM_FINGERPRINT_FAILED")
            material.append(
                (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                    int(observed["rows"]),
                    str(observed["digest"]),
                )
            )
    return hashlib.sha256(_canonical(material)).hexdigest()


def _set_context(connection: psycopg.Connection[dict[str, object]], context: object) -> None:
    connection.execute(
        "SELECT set_config('f0d.enterprise_id',%s,true),"
        "set_config('f0d.actor_id',%s,true),"
        "set_config('f0d.session_token_sha256',%s,true)",
        (
            str(getattr(context, "enterprise_id")),
            str(getattr(context, "actor_id")),
            str(getattr(context, "session_token_sha256")),
        ),
    )


@contextmanager
def _scoped_connection(config: object, context: object) -> Iterator[psycopg.Connection[dict[str, object]]]:
    with psycopg.connect(
        str(getattr(config, "migration_dsn")), row_factory=dict_row
    ) as connection:
        _set_context(connection, context)
        yield connection


def _encrypt(
    connection: psycopg.Connection[dict[str, object]],
    plaintext: bytes | bytearray | memoryview,
    key_material: bytearray,
) -> bytes:
    row = connection.execute(
        "SELECT f0f_crypto.pgp_sym_encrypt_bytea("
        "%s::bytea,encode(%s::bytea,'hex'),"
        "'cipher-algo=aes256,compress-algo=0') AS ciphertext",
        (plaintext, key_material),
    ).fetchone()
    if row is None or not isinstance(row["ciphertext"], (bytes, bytearray, memoryview)):
        raise RuntimeError("ENCRYPTION_FAILED")
    return bytes(row["ciphertext"])


def _decrypt(
    connection: psycopg.Connection[dict[str, object]],
    ciphertext: bytes | bytearray | memoryview,
    key_material: bytearray,
) -> bytearray:
    row = connection.execute(
        "SELECT f0f_crypto.pgp_sym_decrypt_bytea("
        "%s::bytea,encode(%s::bytea,'hex'),"
        "'cipher-algo=aes256,compress-algo=0') AS plaintext",
        (ciphertext, key_material),
    ).fetchone()
    if row is None or not isinstance(row["plaintext"], (bytes, bytearray, memoryview)):
        raise RuntimeError("DECRYPTION_FAILED")
    return bytearray(row["plaintext"])


def _source_row(
    connection: psycopg.Connection[dict[str, object]],
) -> dict[str, object]:
    row = connection.execute(
        "SELECT r.enterprise_id,r.source_document_id,r.source_group,"
        "r.document_type,r.corpus_role,r.enterprise_fact_allowed,"
        "r.current_regulation_allowed,r.search_publish_allowed,"
        "r.expected_sha256,r.expected_size_bytes,v.id AS document_version_id,"
        "v.object_blob_id,p.id AS processing_plan_id,p.source_plan_sha256,"
        "p.source_schema_version,p.source_rule_version,u.id AS unit_id,"
        "u.source_unit_id,u.unit_ordinal,u.unit_kind,u.page_no,"
        "u.candidate_decision,u.native_characters,"
        "u.native_text_sha256,u.native_text_identity_sha256,"
        "u.rotation,u.media_left,u.media_bottom,u.media_right,u.media_top,"
        "u.crop_left,u.crop_bottom,u.crop_right,u.crop_top,u.width_px,"
        "u.height_px,u.evidence_sha256 FROM f0d.fixture_source_registry r "
        "JOIN f0d.document_processing_plan p ON p.enterprise_id=r.enterprise_id "
        "AND p.source_document_id=r.source_document_id "
        "JOIN f0d.document_version v ON v.enterprise_id=p.enterprise_id "
        "AND v.id=p.document_version_id "
        "JOIN f0d.object_blob o ON o.enterprise_id=v.enterprise_id "
        "AND o.id=v.object_blob_id AND o.sha256=r.expected_sha256 "
        "AND o.size_bytes=r.expected_size_bytes "
        "JOIN f0d.document_processing_unit u ON u.enterprise_id=p.enterprise_id "
        "AND u.processing_plan_id=p.id WHERE r.source_group='core' "
        "AND r.document_type='PDF' AND u.unit_kind='PAGE' "
        "AND u.candidate_decision='NATIVE_CANDIDATE' "
        "ORDER BY r.source_line,u.unit_ordinal LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("SOURCE_SAMPLE_MISSING")
    return row


def _insert_configuration(
    connection: psycopg.Connection[dict[str, object]],
    context: object,
    key_fingerprint: str,
    key_material: bytearray,
) -> tuple[uuid.UUID, str]:
    configuration_id = uuid.uuid4()
    verifier = bytearray(os.urandom(32))
    try:
        verifier_hash = hashlib.sha256(verifier).hexdigest()
        ciphertext = _encrypt(connection, verifier, key_material)
        row = connection.execute(
            "INSERT INTO f0i.configuration("
            "id,enterprise_id,actor_id,parser_rule_version,chunk_rule_version,"
            "location_rule_version,ocr_configuration_sha256,"
            "ocr_model_bundle_sha256,ocr_execution_profile_sha256,"
            "ocr_runtime_image_id,ocr_runtime_lock_sha256,"
            "ocr_output_contract_sha256,registered_full_plan_sha256,"
            "key_fingerprint_sha256,key_verifier_plaintext_sha256,"
            "key_verifier_ciphertext,key_verifier_ciphertext_sha256) VALUES("
            "%s,%s,%s,'F0I_REVERSE_NATIVE_V1','UNICODE_300_800_NO_OVERLAP_V1',"
            "'OBSERVED_LOCATION_V1',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING configuration_sha256",
            (
                configuration_id,
                getattr(context, "enterprise_id"),
                getattr(context, "actor_id"),
                _SHA_A,
                _SHA_B,
                _SHA_C,
                "sha256:" + _SHA_D,
                _SHA_A,
                _SHA_B,
                _SHA_C,
                key_fingerprint,
                verifier_hash,
                ciphertext,
                hashlib.sha256(ciphertext).hexdigest(),
            ),
        ).fetchone()
        if row is None:
            raise RuntimeError("CONFIGURATION_INSERT_FAILED")
        return configuration_id, str(row["configuration_sha256"])
    finally:
        verifier[:] = b"\0" * len(verifier)
        verifier.clear()


def _insert_run(
    connection: psycopg.Connection[dict[str, object]],
    context: object,
    configuration_id: uuid.UUID,
    configuration_sha256: str,
    *,
    block_count: int,
    child_count: int,
) -> tuple[uuid.UUID, str]:
    run_id = uuid.uuid4()
    row = connection.execute(
        "INSERT INTO f0i.run("
        "id,enterprise_id,actor_id,configuration_id,configuration_sha256,"
        "profile,input_manifest_sha256,input_summary_sha256,"
        "replay_summary_sha256,requested_document_count,"
        "resolved_document_count,visual_document_count,"
        "structure_document_count,deferred_document_count,visual_unit_count,"
        "native_visual_count,ocr_visual_count,structure_unit_count,"
        "parent_chunk_count,child_chunk_count,block_count,ocr_call_count) "
        "VALUES(%s,%s,%s,%s,%s,'smoke',%s,%s,%s,10,10,10,0,0,1,1,0,0,1,%s,%s,0) "
        "RETURNING run_identity_sha256",
        (
            run_id,
            getattr(context, "enterprise_id"),
            getattr(context, "actor_id"),
            configuration_id,
            configuration_sha256,
            _SHA_A,
            _SHA_B,
            _SHA_C,
            child_count,
            block_count,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("RUN_INSERT_FAILED")
    return run_id, str(row["run_identity_sha256"])


def _insert_scope_and_page(
    connection: psycopg.Connection[dict[str, object]],
    source: Mapping[str, object],
    configuration_id: uuid.UUID,
    configuration_sha256: str,
    run_id: uuid.UUID,
    run_identity_sha256: str,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    from platform_foundation.f0i.contracts import canonical_sha256
    from platform_foundation.f0i.structures import page_geometry

    scope_id = uuid.uuid4()
    page_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO f0i.document_scope("
        "id,enterprise_id,configuration_id,configuration_sha256,first_run_id,"
        "first_run_identity_sha256,document_version_id,object_blob_id,"
        "source_object_sha256,source_object_size_bytes,processing_plan_id,"
        "source_document_id,source_plan_sha256,source_schema_version,"
        "source_rule_version,document_type,source_group,corpus_role,scope_kind,"
        "visual_unit_count,structure_unit_count,enterprise_fact_allowed,"
        "current_regulation_allowed,search_publish_allowed,terminal_status) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "'VISUAL',1,0,%s,%s,%s,'CANONICAL_SCOPE_INCLUDED')",
        (
            scope_id,
            source["enterprise_id"],
            configuration_id,
            configuration_sha256,
            run_id,
            run_identity_sha256,
            source["document_version_id"],
            source["object_blob_id"],
            source["expected_sha256"],
            source["expected_size_bytes"],
            source["processing_plan_id"],
            source["source_document_id"],
            source["source_plan_sha256"],
            source["source_schema_version"],
            source["source_rule_version"],
            source["document_type"],
            source["source_group"],
            source["corpus_role"],
            source["enterprise_fact_allowed"],
            source["current_regulation_allowed"],
            source["search_publish_allowed"],
        ),
    )
    media = {
        "left": f"{source['media_left']:.3f}",
        "bottom": f"{source['media_bottom']:.3f}",
        "right": f"{source['media_right']:.3f}",
        "top": f"{source['media_top']:.3f}",
    }
    crop = {
        "left": f"{source['crop_left']:.3f}",
        "bottom": f"{source['crop_bottom']:.3f}",
        "right": f"{source['crop_right']:.3f}",
        "top": f"{source['crop_top']:.3f}",
    }
    geometry = page_geometry(
        media_box=media, crop_box=crop, rotation=int(source["rotation"])
    )
    source_output_sha256 = str(source["native_text_sha256"])
    source_evidence_sha256 = canonical_sha256(
        {
            "candidate_decision": source["candidate_decision"],
            "source_output_sha256": source_output_sha256,
            "source_unit_evidence_sha256": source["evidence_sha256"],
        }
    )
    connection.execute(
        "INSERT INTO f0i.page("
        "id,enterprise_id,configuration_id,configuration_sha256,"
        "document_scope_id,first_run_id,document_version_id,processing_plan_id,"
        "source_document_id,source_plan_sha256,source_processing_unit_id,"
        "source_unit_id,source_unit_ordinal,source_unit_kind,page_no,"
        "candidate_decision,selected_route,source_unit_evidence_sha256,"
        "native_text_identity_sha256,native_characters,"
        "source_page_output_sha256,source_page_evidence_sha256,rotation,"
        "media_left,media_bottom,media_right,media_top,crop_left,crop_bottom,"
        "crop_right,crop_top,width_px,height_px,geometry_sha256) VALUES("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "'NATIVE_REFERENCE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s,%s)",
        (
            page_id,
            source["enterprise_id"],
            configuration_id,
            configuration_sha256,
            scope_id,
            run_id,
            source["document_version_id"],
            source["processing_plan_id"],
            source["source_document_id"],
            source["source_plan_sha256"],
            source["unit_id"],
            source["source_unit_id"],
            source["unit_ordinal"],
            source["unit_kind"],
            source["page_no"],
            source["candidate_decision"],
            source["evidence_sha256"],
            source["native_text_identity_sha256"],
            source["native_characters"],
            source_output_sha256,
            source_evidence_sha256,
            source["rotation"],
            source["media_left"],
            source["media_bottom"],
            source["media_right"],
            source["media_top"],
            source["crop_left"],
            source["crop_bottom"],
            source["crop_right"],
            source["crop_top"],
            source["width_px"],
            source["height_px"],
            geometry.geometry_sha256,
        ),
    )
    return scope_id, page_id, geometry.geometry_sha256


def _insert_blocks_chunks_links(
    connection: psycopg.Connection[dict[str, object]],
    source: Mapping[str, object],
    configuration_id: uuid.UUID,
    configuration_sha256: str,
    run_id: uuid.UUID,
    scope_id: uuid.UUID,
    page_id: uuid.UUID,
    key_material: bytearray,
    token: str,
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    from platform_foundation.f0i.chunking import build_canonical_unit
    from platform_foundation.f0i.contracts import IdentityBinding, LeafInput
    from platform_foundation.f0i.structures import native_geometry

    observed = native_geometry(page_rotation=int(source["rotation"]))
    binding = IdentityBinding(
        tenant_id=source["enterprise_id"],  # type: ignore[arg-type]
        document_version_id=source["document_version_id"],  # type: ignore[arg-type]
        source_processing_unit_id=source["unit_id"],  # type: ignore[arg-type]
        structure_unit_sha256=None,
        source_version_sha256=str(source["expected_sha256"]),
        f0h_model_sha256=_SHA_B,
        f0h_configuration_sha256=_SHA_A,
        parsing_rule_sha256=_SHA_C,
        chunking_rule_sha256=_SHA_D,
    )
    unit = build_canonical_unit(
        binding,
        (
            LeafInput(
                text=token,
                block_kind="NATIVE_PAGE_TEXT",
                locator_kind="NATIVE_TEXT",
                locator_sha256=observed.location_sha256,
                separator_after="\n",
            ),
            LeafInput(
                text="Ω" * 320,
                block_kind="NATIVE_PAGE_TEXT",
                locator_kind="NATIVE_TEXT",
                locator_sha256=observed.location_sha256,
            ),
        ),
    )
    common = {
        "enterprise_id": source["enterprise_id"],
        "configuration_id": configuration_id,
        "configuration_sha256": configuration_sha256,
        "document_scope_id": scope_id,
        "first_run_id": run_id,
        "document_version_id": source["document_version_id"],
        "processing_plan_id": source["processing_plan_id"],
        "source_document_id": source["source_document_id"],
        "source_plan_sha256": source["source_plan_sha256"],
        "container_id": page_id,
        "container_kind": "PAGE",
        "page_id": page_id,
        "source_processing_unit_id": source["unit_id"],
        "source_unit_id": source["source_unit_id"],
        "source_unit_ordinal": source["unit_ordinal"],
        "page_no": source["page_no"],
    }
    try:
        for block in unit.blocks:
            separator = block.block_kind == "CANONICAL_SEPARATOR"
            ciphertext = _encrypt(
                connection,
                unit.body.slice(block.span.start_byte, block.span.end_byte),
                key_material,
            )
            values = {
                **common,
                "id": block.block_id,
                "block_ordinal": block.ordinal,
                "block_kind": block.block_kind,
                "evidence_method": "CANONICAL_JOIN" if separator else "PYPDF_NATIVE",
                "source_route": "NATIVE_REFERENCE",
                "location_kind": block.locator_kind,
                "location_status": "SYNTHETIC" if separator else "UNAVAILABLE",
                "location_reason_code": (
                    "CANONICAL_JOIN_SEPARATOR"
                    if separator
                    else "NATIVE_LAYOUT_NOT_CAPTURED"
                ),
                "location_sha256": block.locator_sha256,
                "coordinate_space": "SYNTHETIC" if separator else "UNAVAILABLE",
                "reading_order_status": "SYNTHETIC" if separator else "UNAVAILABLE",
                "canonical_sha256": unit.body.sha256,
                "canonical_bytes": unit.body.byte_count,
                "canonical_characters": unit.body.character_count,
                "body_sha256": block.plaintext_sha256,
                "body_bytes": block.plaintext_bytes,
                "body_characters": block.plaintext_characters,
                "ciphertext": ciphertext,
                "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                "previous_chain": block.previous_chain_sha256,
                "source_chain": block.chain_sha256,
                "start_byte": block.span.start_byte,
                "end_byte": block.span.end_byte,
                "start_character": block.span.start_character,
                "end_character": block.span.end_character,
            }
            connection.execute(
                "INSERT INTO f0i.block("
                "id,enterprise_id,configuration_id,configuration_sha256,"
                "document_scope_id,first_run_id,document_version_id,"
                "processing_plan_id,source_document_id,source_plan_sha256,"
                "source_document_type,container_id,container_kind,page_id,"
                "source_processing_unit_id,source_unit_id,source_unit_ordinal,"
                "page_no,block_ordinal,block_kind,evidence_method,source_route,"
                "location_kind,location_status,location_reason_code,"
                "location_sha256,coordinate_space,reading_order_status,"
                "table_evidence_status,canonical_body_plaintext_sha256,"
                "canonical_body_plaintext_size_bytes,"
                "canonical_body_plaintext_character_count,"
                "body_plaintext_sha256,body_plaintext_size_bytes,"
                "body_plaintext_character_count,body_ciphertext,"
                "body_ciphertext_sha256,previous_source_chain_sha256,"
                "source_chain_sha256,span_start_byte,span_end_byte,"
                "span_start_character,span_end_character) VALUES("
                "%(id)s,%(enterprise_id)s,%(configuration_id)s,"
                "%(configuration_sha256)s,%(document_scope_id)s,"
                "%(first_run_id)s,%(document_version_id)s,"
                "%(processing_plan_id)s,%(source_document_id)s,"
                "%(source_plan_sha256)s,'PDF',%(container_id)s,"
                "%(container_kind)s,%(page_id)s,%(source_processing_unit_id)s,"
                "%(source_unit_id)s,%(source_unit_ordinal)s,%(page_no)s,"
                "%(block_ordinal)s,%(block_kind)s,%(evidence_method)s,"
                "%(source_route)s,%(location_kind)s,%(location_status)s,"
                "%(location_reason_code)s,%(location_sha256)s,"
                "%(coordinate_space)s,%(reading_order_status)s,'UNRESOLVED',"
                "%(canonical_sha256)s,%(canonical_bytes)s,"
                "%(canonical_characters)s,%(body_sha256)s,%(body_bytes)s,"
                "%(body_characters)s,%(ciphertext)s,%(ciphertext_sha256)s,"
                "%(previous_chain)s,%(source_chain)s,%(start_byte)s,"
                "%(end_byte)s,%(start_character)s,%(end_character)s)",
                values,
            )

        for chunk in (unit.parent, *unit.children):
            ciphertext = _encrypt(
                connection,
                unit.body.slice(chunk.span.start_byte, chunk.span.end_byte),
                key_material,
            )
            values = {
                **common,
                "id": chunk.chunk_id,
                "chunk_level": chunk.chunk_level,
                "parent_chunk_id": chunk.parent_chunk_id,
                "chunk_ordinal": chunk.ordinal,
                "is_tail": chunk.is_tail,
                "canonical_sha256": unit.body.sha256,
                "canonical_bytes": unit.body.byte_count,
                "canonical_characters": unit.body.character_count,
                "body_sha256": chunk.plaintext_sha256,
                "body_bytes": chunk.plaintext_bytes,
                "body_characters": chunk.plaintext_characters,
                "ciphertext": ciphertext,
                "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
                "start_byte": chunk.span.start_byte,
                "end_byte": chunk.span.end_byte,
                "start_character": chunk.span.start_character,
                "end_character": chunk.span.end_character,
                "previous_chain": chunk.previous_chain_sha256,
                "source_chain": chunk.chain_sha256,
                "unit_chain": unit.unit_chain_sha256,
            }
            connection.execute(
                "INSERT INTO f0i.chunk("
                "id,enterprise_id,configuration_id,configuration_sha256,"
                "document_scope_id,first_run_id,document_version_id,"
                "processing_plan_id,source_document_id,source_plan_sha256,"
                "container_id,container_kind,page_id,source_processing_unit_id,"
                "source_unit_id,source_unit_ordinal,page_no,chunk_level,"
                "parent_chunk_id,chunk_ordinal,is_tail,canonical_body_plaintext_sha256,"
                "canonical_body_plaintext_size_bytes,"
                "canonical_body_plaintext_character_count,body_plaintext_sha256,"
                "body_plaintext_size_bytes,body_plaintext_character_count,"
                "body_ciphertext,body_ciphertext_sha256,span_start_byte,"
                "span_end_byte,span_start_character,span_end_character,"
                "previous_source_chain_sha256,source_chain_sha256,"
                "unit_chain_sha256) VALUES("
                "%(id)s,%(enterprise_id)s,%(configuration_id)s,"
                "%(configuration_sha256)s,%(document_scope_id)s,"
                "%(first_run_id)s,%(document_version_id)s,"
                "%(processing_plan_id)s,%(source_document_id)s,"
                "%(source_plan_sha256)s,%(container_id)s,%(container_kind)s,"
                "%(page_id)s,%(source_processing_unit_id)s,%(source_unit_id)s,"
                "%(source_unit_ordinal)s,%(page_no)s,%(chunk_level)s,"
                "%(parent_chunk_id)s,%(chunk_ordinal)s,%(is_tail)s,"
                "%(canonical_sha256)s,%(canonical_bytes)s,"
                "%(canonical_characters)s,%(body_sha256)s,%(body_bytes)s,"
                "%(body_characters)s,%(ciphertext)s,%(ciphertext_sha256)s,"
                "%(start_byte)s,%(end_byte)s,%(start_character)s,"
                "%(end_character)s,%(previous_chain)s,%(source_chain)s,"
                "%(unit_chain)s)",
                values,
            )

        child_ids = {child.chunk_id for child in unit.children}
        for link in unit.links:
            if link.chunk_id not in child_ids:
                continue
            connection.execute(
                "INSERT INTO f0i.chunk_block_link("
                "id,enterprise_id,configuration_id,configuration_sha256,"
                "document_scope_id,first_run_id,document_version_id,"
                "processing_plan_id,container_id,chunk_id,linked_chunk_level,"
                "block_id,link_ordinal,intersection_start_byte,"
                "intersection_end_byte,intersection_start_character,"
                "intersection_end_character,unit_chain_sha256) VALUES("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CHILD',%s,%s,%s,%s,%s,%s,%s)",
                (
                    link.link_id,
                    source["enterprise_id"],
                    configuration_id,
                    configuration_sha256,
                    scope_id,
                    run_id,
                    source["document_version_id"],
                    source["processing_plan_id"],
                    page_id,
                    link.chunk_id,
                    link.block_id,
                    link.link_ordinal,
                    link.intersection_span.start_byte,
                    link.intersection_span.end_byte,
                    link.intersection_span.start_character,
                    link.intersection_span.end_character,
                    unit.unit_chain_sha256,
                ),
            )
        return unit.blocks[0].block_id, tuple(child_ids)
    finally:
        unit.wipe()


def _insert_sample(
    connection: psycopg.Connection[dict[str, object]],
    context: object,
    key_fingerprint: str,
    key_material: bytearray,
    token: str,
) -> dict[str, object]:
    source = _source_row(connection)
    configuration_id, configuration_sha256 = _insert_configuration(
        connection, context, key_fingerprint, key_material
    )
    run_id, run_identity_sha256 = _insert_run(
        connection,
        context,
        configuration_id,
        configuration_sha256,
        block_count=3,
        child_count=1,
    )
    scope_id, page_id, _ = _insert_scope_and_page(
        connection,
        source,
        configuration_id,
        configuration_sha256,
        run_id,
        run_identity_sha256,
    )
    first_block_id, child_ids = _insert_blocks_chunks_links(
        connection,
        source,
        configuration_id,
        configuration_sha256,
        run_id,
        scope_id,
        page_id,
        key_material,
        token,
    )
    return {
        "configuration_id": configuration_id,
        "first_block_id": first_block_id,
        "child_ids": child_ids,
    }


def _decoded_characters(value: bytes | bytearray) -> int:
    return len(value.decode("utf-8", errors="strict"))


def _validate(
    connection: psycopg.Connection[dict[str, object]],
    key_material: bytearray,
    state: Mapping[str, object],
) -> int:
    block_plaintexts: list[bytearray] = []
    chunk_plaintexts: list[bytearray] = []
    canonical_bodies: dict[uuid.UUID, bytearray] = {}
    try:
        configuration = connection.execute(
            "SELECT key_verifier_plaintext_sha256,key_verifier_ciphertext,"
            "key_verifier_ciphertext_sha256,configuration_sha256 "
            "FROM f0i.configuration WHERE id=%s",
            (state["configuration_id"],),
        ).fetchone()
        if configuration is None:
            return 2
        ciphertext = bytes(configuration["key_verifier_ciphertext"])
        if hashlib.sha256(ciphertext).hexdigest() != str(
            configuration["key_verifier_ciphertext_sha256"]
        ):
            return 2
        verifier = _decrypt(connection, ciphertext, key_material)
        try:
            if hashlib.sha256(verifier).hexdigest() != str(
                configuration["key_verifier_plaintext_sha256"]
            ):
                return 2
        finally:
            verifier[:] = b"\0" * len(verifier)
            verifier.clear()

        blocks = connection.execute(
            "SELECT id,container_id,block_ordinal,body_ciphertext,"
            "body_ciphertext_sha256,body_plaintext_sha256,"
            "body_plaintext_size_bytes,body_plaintext_character_count,"
            "canonical_body_plaintext_sha256,"
            "canonical_body_plaintext_size_bytes,"
            "canonical_body_plaintext_character_count,span_start_byte,"
            "span_end_byte,span_start_character,span_end_character "
            "FROM f0i.block ORDER BY container_id,block_ordinal"
        ).fetchall()
        if not blocks:
            return 2
        block_groups: dict[uuid.UUID, list[tuple[dict[str, object], bytearray]]] = defaultdict(list)
        for row in blocks:
            encrypted = bytes(row["body_ciphertext"])
            if hashlib.sha256(encrypted).hexdigest() != str(
                row["body_ciphertext_sha256"]
            ):
                return 2
            plaintext = _decrypt(connection, encrypted, key_material)
            block_plaintexts.append(plaintext)
            if (
                hashlib.sha256(plaintext).hexdigest() != row["body_plaintext_sha256"]
                or len(plaintext) != int(row["body_plaintext_size_bytes"])
                or _decoded_characters(plaintext)
                != int(row["body_plaintext_character_count"])
                or int(row["span_end_byte"]) - int(row["span_start_byte"])
                != len(plaintext)
                or int(row["span_end_character"])
                - int(row["span_start_character"])
                != _decoded_characters(plaintext)
            ):
                return 2
            block_groups[row["container_id"]].append((row, plaintext))  # type: ignore[index]

        for container_id, material in block_groups.items():
            expected_byte = 0
            expected_character = 0
            body = bytearray()
            for ordinal, (row, plaintext) in enumerate(material, start=1):
                if (
                    int(row["block_ordinal"]) != ordinal
                    or int(row["span_start_byte"]) != expected_byte
                    or int(row["span_start_character"]) != expected_character
                ):
                    return 2
                body.extend(plaintext)
                expected_byte = int(row["span_end_byte"])
                expected_character = int(row["span_end_character"])
            first = material[0][0]
            if (
                expected_byte != len(body)
                or expected_character != _decoded_characters(body)
                or hashlib.sha256(body).hexdigest()
                != first["canonical_body_plaintext_sha256"]
                or len(body) != int(first["canonical_body_plaintext_size_bytes"])
                or _decoded_characters(body)
                != int(first["canonical_body_plaintext_character_count"])
                or any(
                    row["canonical_body_plaintext_sha256"]
                    != first["canonical_body_plaintext_sha256"]
                    for row, _ in material
                )
            ):
                return 2
            canonical_bodies[container_id] = body

        chunks = connection.execute(
            "SELECT id,container_id,chunk_level,parent_chunk_id,chunk_ordinal,"
            "is_tail,body_ciphertext,body_ciphertext_sha256,"
            "body_plaintext_sha256,body_plaintext_size_bytes,"
            "body_plaintext_character_count,canonical_body_plaintext_sha256,"
            "canonical_body_plaintext_size_bytes,"
            "canonical_body_plaintext_character_count,span_start_byte,"
            "span_end_byte,span_start_character,span_end_character,"
            "unit_chain_sha256 FROM f0i.chunk "
            "ORDER BY container_id,chunk_level DESC,chunk_ordinal"
        ).fetchall()
        chunk_groups: dict[uuid.UUID, list[tuple[dict[str, object], bytearray]]] = defaultdict(list)
        for row in chunks:
            encrypted = bytes(row["body_ciphertext"])
            if hashlib.sha256(encrypted).hexdigest() != str(
                row["body_ciphertext_sha256"]
            ):
                return 2
            plaintext = _decrypt(connection, encrypted, key_material)
            chunk_plaintexts.append(plaintext)
            if (
                hashlib.sha256(plaintext).hexdigest() != row["body_plaintext_sha256"]
                or len(plaintext) != int(row["body_plaintext_size_bytes"])
                or _decoded_characters(plaintext)
                != int(row["body_plaintext_character_count"])
            ):
                return 2
            chunk_groups[row["container_id"]].append((row, plaintext))  # type: ignore[index]

        if set(chunk_groups) != set(canonical_bodies):
            return 2
        block_spans = {
            row["id"]: (
                int(row["span_start_byte"]),
                int(row["span_end_byte"]),
                int(row["span_start_character"]),
                int(row["span_end_character"]),
                row["container_id"],
            )
            for row in blocks
        }
        child_spans: dict[uuid.UUID, tuple[int, int, int, int, uuid.UUID]] = {}
        for container_id, material in chunk_groups.items():
            body = canonical_bodies[container_id]
            parents = [item for item in material if item[0]["chunk_level"] == "PARENT"]
            children = sorted(
                (item for item in material if item[0]["chunk_level"] == "CHILD"),
                key=lambda item: int(item[0]["chunk_ordinal"]),
            )
            if len(parents) != 1 or not children:
                return 2
            parent, parent_body = parents[0]
            if (
                bytes(parent_body) != bytes(body)
                or parent["parent_chunk_id"] is not None
                or int(parent["chunk_ordinal"]) != 0
                or bool(parent["is_tail"])
                or int(parent["span_start_byte"]) != 0
                or int(parent["span_end_byte"]) != len(body)
            ):
                return 2
            expected_byte = 0
            expected_character = 0
            reconstructed = bytearray()
            try:
                for ordinal, (child, plaintext) in enumerate(children, start=1):
                    start_byte = int(child["span_start_byte"])
                    end_byte = int(child["span_end_byte"])
                    start_character = int(child["span_start_character"])
                    end_character = int(child["span_end_character"])
                    if (
                        int(child["chunk_ordinal"]) != ordinal
                        or child["parent_chunk_id"] != parent["id"]
                        or bool(child["is_tail"]) is not (ordinal == len(children))
                        or start_byte != expected_byte
                        or start_character != expected_character
                        or bytes(plaintext) != bytes(body[start_byte:end_byte])
                        or _decoded_characters(body[:start_byte]) != start_character
                        or _decoded_characters(body[:end_byte]) != end_character
                    ):
                        return 2
                    reconstructed.extend(plaintext)
                    expected_byte = end_byte
                    expected_character = end_character
                    child_spans[child["id"]] = (
                        start_byte,
                        end_byte,
                        start_character,
                        end_character,
                        container_id,
                    )
                if bytes(reconstructed) != bytes(body):
                    return 2
            finally:
                reconstructed[:] = b"\0" * len(reconstructed)
                reconstructed.clear()

        links = connection.execute(
            "SELECT chunk_id,block_id,link_ordinal,intersection_start_byte,"
            "intersection_end_byte,intersection_start_character,"
            "intersection_end_character,container_id FROM f0i.chunk_block_link "
            "ORDER BY chunk_id,link_ordinal"
        ).fetchall()
        linked: set[tuple[uuid.UUID, uuid.UUID]] = set()
        ordinals: dict[uuid.UUID, list[int]] = defaultdict(list)
        for link in links:
            chunk_id = link["chunk_id"]
            block_id = link["block_id"]
            if chunk_id not in child_spans or block_id not in block_spans:
                return 2
            child = child_spans[chunk_id]  # type: ignore[index]
            block = block_spans[block_id]  # type: ignore[index]
            expected = (
                max(child[0], block[0]),
                min(child[1], block[1]),
                max(child[2], block[2]),
                min(child[3], block[3]),
            )
            observed = (
                int(link["intersection_start_byte"]),
                int(link["intersection_end_byte"]),
                int(link["intersection_start_character"]),
                int(link["intersection_end_character"]),
            )
            if (
                child[4] != block[4]
                or link["container_id"] != child[4]
                or expected != observed
                or expected[0] > expected[1]
                or expected[2] > expected[3]
            ):
                return 2
            linked.add((chunk_id, block_id))  # type: ignore[arg-type]
            ordinals[chunk_id].append(int(link["link_ordinal"]))  # type: ignore[index]
        if set(ordinals) != set(child_spans) or any(
            values != list(range(1, len(values) + 1))
            for values in ordinals.values()
        ):
            return 2
        expected_links = {
            (chunk_id, block_id)
            for chunk_id, child in child_spans.items()
            for block_id, block in block_spans.items()
            if child[4] == block[4]
            and max(child[0], block[0]) < min(child[1], block[1])
        }
        if linked != expected_links:
            return 2
        return 0
    except BaseException:
        return 2
    finally:
        for plaintext in (*block_plaintexts, *chunk_plaintexts):
            plaintext[:] = b"\0" * len(plaintext)
            plaintext.clear()
        for body in canonical_bodies.values():
            body[:] = b"\0" * len(body)
            body.clear()


def _tamper_configuration(
    database_name: str,
    key_material: bytearray,
    state: Mapping[str, object],
) -> int:
    connection = psycopg.connect(
        _database_dsn(database_name), row_factory=dict_row
    )
    wrong = bytearray(os.urandom(31))
    try:
        connection.execute("SET LOCAL session_replication_role='replica'")
        ciphertext = _encrypt(connection, wrong, key_material)
        changed = connection.execute(
            "UPDATE f0i.configuration SET key_verifier_ciphertext=%s,"
            "key_verifier_ciphertext_sha256=%s WHERE id=%s RETURNING id",
            (
                ciphertext,
                hashlib.sha256(ciphertext).hexdigest(),
                state["configuration_id"],
            ),
        ).fetchone()
        result = _validate(connection, key_material, state)
        connection.rollback()
        return 2 if changed is not None and result == 2 else 1
    except BaseException:
        connection.rollback()
        return 1
    finally:
        wrong[:] = b"\0" * len(wrong)
        wrong.clear()
        connection.close()


def _tamper_span(
    database_name: str,
    key_material: bytearray,
    state: Mapping[str, object],
) -> int:
    connection = psycopg.connect(
        _database_dsn(database_name), row_factory=dict_row
    )
    try:
        connection.execute("SET LOCAL session_replication_role='replica'")
        changed = connection.execute(
            "UPDATE f0i.block SET span_start_byte=span_start_byte+1,"
            "span_end_byte=span_end_byte+1,"
            "span_start_character=span_start_character+1,"
            "span_end_character=span_end_character+1 WHERE id=%s RETURNING id",
            (state["first_block_id"],),
        ).fetchone()
        result = _validate(connection, key_material, state)
        connection.rollback()
        return 2 if changed is not None and result == 2 else 1
    except BaseException:
        connection.rollback()
        return 1
    finally:
        connection.close()


def _f0i_state_fingerprint(
    config: object, context: object
) -> tuple[int, str]:
    """Return only aggregate row counts and server-side row digests."""

    material: list[tuple[str, int, str]] = []
    with _scoped_connection(config, context) as connection:
        for table in _TABLES:
            statement = sql.SQL(
                "SELECT count(*) AS rows,md5(COALESCE(string_agg("
                "md5(to_jsonb(t)::text),'' ORDER BY md5(to_jsonb(t)::text)),'')) "
                "AS digest FROM f0i.{} AS t"
            ).format(sql.Identifier(table))
            row = connection.execute(statement).fetchone()
            if row is None:
                raise RuntimeError("F0I_STATE_FINGERPRINT_FAILED")
            material.append((table, int(row["rows"]), str(row["digest"])))
    return sum(item[1] for item in material), hashlib.sha256(
        _canonical(material)
    ).hexdigest()


def _active_version_crosswire_probe(config: object, context: object) -> int:
    """Attempt a real cross-version insert and unconditionally roll it back."""

    before = _f0i_state_fingerprint(config, context)
    connection = psycopg.connect(
        str(getattr(config, "migration_dsn")), row_factory=dict_row
    )
    rejected_by_composite_fk = False
    rollback_ok = True
    try:
        _set_context(connection, context)
        source = connection.execute(
            "SELECT id,document_version_id FROM f0i.block "
            "WHERE block_ordinal>1 ORDER BY block_ordinal LIMIT 1"
        ).fetchone()
        if source is None:
            raise RuntimeError("CROSSWIRE_SOURCE_MISSING")
        wrong = connection.execute(
            "SELECT id FROM f0d.document_version WHERE enterprise_id=%s "
            "AND id<>%s ORDER BY id LIMIT 1",
            (getattr(context, "enterprise_id"), source["document_version_id"]),
        ).fetchone()
        if wrong is None:
            raise RuntimeError("CROSSWIRE_TARGET_MISSING")
        columns = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='f0i' AND table_name='block' "
            "AND is_generated='NEVER' ORDER BY ordinal_position"
        ).fetchall()
        names: list[sql.Identifier] = []
        expressions: list[sql.Composable] = []
        parameters: list[object] = []
        for row in columns:
            name = str(row["column_name"])
            names.append(sql.Identifier(name))
            if name == "id":
                expressions.append(sql.Placeholder())
                parameters.append(uuid.uuid4())
            elif name == "document_version_id":
                expressions.append(sql.Placeholder())
                parameters.append(wrong["id"])
            elif name == "block_ordinal":
                expressions.append(sql.SQL("source.block_ordinal + 1000"))
            else:
                expressions.append(sql.Identifier("source", name))
        if not columns or len(names) != len(expressions):
            raise RuntimeError("CROSSWIRE_COLUMNS_INVALID")
        statement = sql.SQL(
            "INSERT INTO f0i.block ({}) SELECT {} FROM f0i.block AS source "
            "WHERE source.id={}"
        ).format(
            sql.SQL(",").join(names),
            sql.SQL(",").join(expressions),
            sql.Placeholder(),
        )
        parameters.append(source["id"])
        try:
            connection.execute(statement, parameters)
        except psycopg.Error as error:
            rejected_by_composite_fk = (
                error.sqlstate == "23503"
                and error.diag.constraint_name
                in {"block_document_scope_fk", "block_page_fk"}
            )
    finally:
        try:
            connection.rollback()
        except psycopg.Error:
            rollback_ok = False
        connection.close()
    after = _f0i_state_fingerprint(config, context)
    return int(
        not rejected_by_composite_fk or not rollback_ok or before != after
    )


def _integrity_counts(
    config: object, context_a: object, context_b: object
) -> tuple[int, int, int]:
    with _scoped_connection(config, context_a) as connection:
        crosswires = int(
            connection.execute(
                "SELECT ("
                "SELECT count(*) FROM f0i.page p JOIN f0i.document_scope s "
                "ON s.id=p.document_scope_id WHERE p.enterprise_id<>s.enterprise_id "
                "OR p.document_version_id<>s.document_version_id "
                "OR p.configuration_id<>s.configuration_id) + ("
                "SELECT count(*) FROM f0i.block b JOIN f0i.document_scope s "
                "ON s.id=b.document_scope_id WHERE b.enterprise_id<>s.enterprise_id "
                "OR b.document_version_id<>s.document_version_id "
                "OR b.configuration_id<>s.configuration_id) + ("
                "SELECT count(*) FROM f0i.chunk c JOIN f0i.document_scope s "
                "ON s.id=c.document_scope_id WHERE c.enterprise_id<>s.enterprise_id "
                "OR c.document_version_id<>s.document_version_id "
                "OR c.configuration_id<>s.configuration_id) AS count"
            ).fetchone()["count"]
        )
        orphan_blocks = int(
            connection.execute(
                "SELECT count(*) AS count FROM f0i.block b "
                "LEFT JOIN f0i.document_scope s ON s.enterprise_id=b.enterprise_id "
                "AND s.id=b.document_scope_id LEFT JOIN f0i.page p "
                "ON p.enterprise_id=b.enterprise_id AND p.id=b.page_id "
                "WHERE s.id IS NULL OR (b.page_id IS NOT NULL AND p.id IS NULL)"
            ).fetchone()["count"]
        )
        orphan_chunks = int(
            connection.execute(
                "SELECT (SELECT count(*) FROM f0i.chunk c "
                "LEFT JOIN f0i.document_scope s ON s.enterprise_id=c.enterprise_id "
                "AND s.id=c.document_scope_id LEFT JOIN f0i.page p "
                "ON p.enterprise_id=c.enterprise_id AND p.id=c.page_id "
                "LEFT JOIN f0i.chunk parent ON parent.enterprise_id=c.enterprise_id "
                "AND parent.id=c.parent_chunk_id WHERE s.id IS NULL "
                "OR (c.page_id IS NOT NULL AND p.id IS NULL) "
                "OR (c.chunk_level='CHILD' AND parent.id IS NULL)) + "
                "(SELECT count(*) FROM f0i.chunk_block_link l "
                "LEFT JOIN f0i.chunk c ON c.enterprise_id=l.enterprise_id "
                "AND c.id=l.chunk_id LEFT JOIN f0i.block b "
                "ON b.enterprise_id=l.enterprise_id AND b.id=l.block_id "
                "WHERE c.id IS NULL OR b.id IS NULL) AS count"
            ).fetchone()["count"]
        )
    with _scoped_connection(config, context_b) as connection:
        visible = 0
        for table in _TABLES:
            row = connection.execute(
                sql.SQL("SELECT count(*) AS count FROM f0i.{}").format(
                    sql.Identifier(table)
                )
            ).fetchone()
            visible += int(row["count"])
    return crosswires + visible, orphan_blocks, orphan_chunks


def _iter_persistence_files() -> tuple[Path, ...]:
    files: list[Path] = []
    for relative in _PERSISTENCE_TARGETS:
        target = _ROOT / relative
        if target.is_file() or target.is_symlink():
            files.append(target)
        elif target.is_dir():
            files.extend(
                path
                for path in target.rglob("*")
                if "__pycache__" not in path.parts
                and (path.is_file() or path.is_symlink())
            )
    return tuple(sorted(set(files), key=str))


def _file_contains(path: Path, needle: bytes) -> int:
    if len(needle) < 16:
        return 1
    overlap = len(needle) - 1
    tail = b""
    try:
        listed = os.lstat(path)
        if not stat.S_ISREG(listed.st_mode) or stat.S_ISLNK(listed.st_mode):
            return 0
        with path.open("rb", buffering=0) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return 0
                window = tail + chunk
                if needle in window:
                    return 1
                tail = window[-overlap:]
    except OSError:
        return 1


def _plaintext_leaks(
    config: object, context: object, needle: bytes
) -> int:
    leaks = sum(_file_contains(path, needle) for path in _iter_persistence_files())
    with _scoped_connection(config, context) as connection:
        forbidden = connection.execute(
            "SELECT count(*) AS count FROM information_schema.columns "
            "WHERE table_schema='f0i' AND column_name=ANY(%s)",
            (["body", "body_text", "content", "plaintext", "raw_text", "text"],),
        ).fetchone()
        leaks += int(forbidden["count"])
        textual = connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema='f0i' AND data_type IN "
            "('text','character','character varying') "
            "ORDER BY table_name,column_name"
        ).fetchall()
        token = needle.decode("ascii", errors="strict")
        for row in textual:
            statement = sql.SQL(
                "SELECT count(*) AS count FROM f0i.{} WHERE position(%s in {}::text)>0"
            ).format(
                sql.Identifier(str(row["table_name"])),
                sql.Identifier(str(row["column_name"])),
            )
            leaks += int(connection.execute(statement, (token,)).fetchone()["count"])
        bytea_columns = connection.execute(
            "SELECT table_name,column_name FROM information_schema.columns "
            "WHERE table_schema='f0i' AND data_type='bytea' "
            "ORDER BY table_name,column_name"
        ).fetchall()
        for row in bytea_columns:
            statement = sql.SQL(
                "SELECT count(*) AS count FROM f0i.{} "
                "WHERE position(%s::bytea in {})>0"
            ).format(
                sql.Identifier(str(row["table_name"])),
                sql.Identifier(str(row["column_name"])),
            )
            leaks += int(connection.execute(statement, (needle,)).fetchone()["count"])
    return leaks


def _import_call_metrics() -> tuple[int, int]:
    external = 0
    search = 0
    files = tuple((_ROOT / "src/platform_foundation/f0i").glob("*.py")) + (
        _ROOT / "tests/f0i_reverse_verify.py",
    )
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, SyntaxError):
            external += 1
            search += 1
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = (node.module,)
            else:
                continue
            roots = {name.split(".", 1)[0] for name in names}
            external += len(roots & _EXTERNAL_IMPORTS)
            search += len(roots & _SEARCH_IMPORTS)
    return external, search


def _container_residuals() -> int:
    try:
        from platform_foundation.f0h.runtime_config import runtime_paths

        docker, _ = runtime_paths()
        completed = subprocess.run(
            (
                docker,
                "ps",
                "-a",
                "--filter",
                "name=^/anhuan-f0h-",
                "--format",
                "{{.ID}}",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            cwd="/private/tmp",
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"},
            timeout=20,
            check=False,
        )
    except BaseException:
        return 1
    if completed.returncode != 0 or completed.stderr:
        return 1
    return len(completed.stdout.splitlines())


def _database_exists(database_name: str) -> bool:
    with psycopg.connect(_database_dsn("postgres"), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s) AS present",
            (database_name,),
        ).fetchone()
    return bool(row["present"])


def _evaluate() -> tuple[dict[str, int], tuple[bytes, ...]]:
    from platform_foundation.auth import authenticate_local_session
    from platform_foundation.bootstrap import (
        LOCAL_TENANT_A_TOKEN,
        LOCAL_TENANT_B_TOKEN,
    )
    from platform_foundation.f0i.bootstrap import (
        drop_scratch_database,
        ensure_database,
    )
    from platform_foundation.f0i.config import database_config
    from platform_foundation.f0i.keyfile import create_keyfile, load_keyfile
    from platform_foundation.f0i.locking import host_replay_lock

    metrics = _empty_metrics()
    metrics["external_calls"], metrics["search_calls"] = _import_call_metrics()
    upstream_before = _upstream_fingerprint()
    containers_before = _container_residuals()
    database_name = "f0i_verify_" + uuid.uuid4().hex[:16]
    key_path = "/private/tmp/anhuan-f0i-" + uuid.uuid4().hex[:16] + ".key"
    needle = b""
    scratch_created = False
    key_created = False
    try:
        with host_replay_lock():
            if containers_before != 0:
                raise RuntimeError("PREEXISTING_CONTAINER")
            scratch_created = ensure_database(database_name)
            if not scratch_created:
                raise RuntimeError("SCRATCH_DATABASE_NOT_FRESH")
            create_keyfile(key_path)
            key_created = True
            config = database_config(database_name)
            context_a = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
            context_b = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
            token = secrets.token_hex(24)
            needle = token.encode("ascii", errors="strict")
            with load_keyfile(key_path) as key:
                key_material = bytearray(key.view())
                try:
                    with _scoped_connection(config, context_a) as connection:
                        state = _insert_sample(
                            connection,
                            context_a,
                            key.fingerprint_sha256,
                            key_material,
                            token,
                        )
                    with _scoped_connection(config, context_a) as connection:
                        metrics["valid_exit"] = _validate(
                            connection, key_material, state
                        )
                    metrics["config_tamper_exit"] = _tamper_configuration(
                        database_name, key_material, state
                    )
                    metrics["span_tamper_exit"] = _tamper_span(
                        database_name, key_material, state
                    )
                    with _scoped_connection(config, context_a) as connection:
                        metrics["restored_exit"] = _validate(
                            connection, key_material, state
                        )
                    active_crosswire = _active_version_crosswire_probe(
                        config, context_a
                    )
                    (
                        metrics["tenant_version_crosswires"],
                        metrics["orphan_blocks"],
                        metrics["orphan_chunks"],
                    ) = _integrity_counts(config, context_a, context_b)
                    metrics["tenant_version_crosswires"] += active_crosswire
                    metrics["plaintext_leaks"] = _plaintext_leaks(
                        config, context_a, needle
                    )
                finally:
                    key_material[:] = b"\0" * len(key_material)
                    key_material.clear()
    finally:
        if key_created:
            try:
                os.unlink(key_path)
            except FileNotFoundError:
                key_created = False
        if scratch_created or _database_exists(database_name):
            drop_scratch_database(database_name)

    containers_after = _container_residuals()
    metrics["concurrent_ocr_runs"] = max(containers_before, containers_after)
    metrics["container_residuals"] = containers_after + int(
        os.path.lexists(key_path) or _database_exists(database_name)
    )
    metrics["upstream_mutations"] = int(
        upstream_before != _upstream_fingerprint()
    )
    return metrics, (needle,) if needle else ()


def main() -> int:
    metrics = _empty_metrics()
    needles: tuple[bytes, ...] = ()
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    try:
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            metrics, needles = _evaluate()
    except BaseException:
        metrics = _empty_metrics()
    captured = (stdout_buffer.getvalue() + stderr_buffer.getvalue()).encode(
        "utf-8", errors="replace"
    )
    if needles:
        metrics["plaintext_leaks"] += sum(
            int(needle in captured) for needle in needles
        )
    else:
        metrics["plaintext_leaks"] += 1
    for name in _ORDER:
        print(f"{name}={metrics[name]}")
    observed = tuple(metrics[name] for name in _ORDER)
    return 0 if observed == _EXPECTED else 2


if __name__ == "__main__":
    raise SystemExit(main())
