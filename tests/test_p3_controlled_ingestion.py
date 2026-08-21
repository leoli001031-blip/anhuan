"""Lightweight, offline contracts for P3 controlled ingestion.

These tests intentionally use no database, container, network, shared object,
F1.1.1 formal verifier, or old upload worker.  They pin the one linear
migration, tenant-bound schema, fail-closed state transitions, narrow format
surface, deterministic safe previews, and the rule that P3 never creates an
outbox event or enters the legacy indexing pipeline.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import struct
import sys
import unittest
import zipfile
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pypdf import PdfWriter
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

from platform_foundation.f1 import models
from platform_foundation.f1.features.p3 import contracts, preview, processor, scanner


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "infra/f1/alembic/versions"
P3_MIGRATION = MIGRATIONS / "f1_0006_controlled_ingestion.py"
P3_FEATURES = ROOT / "src/platform_foundation/f1/features/p3"
P3_SERVICE = P3_FEATURES / "service.py"
P3_ROUTER = (
    ROOT / "src/platform_foundation/f1/api/routers/p3_controlled_ingestion.py"
)

FROZEN_MIGRATION_DIGESTS = {
    "f1_0001_platform_shell_baseline.py": (
        "18af367b01ff9d5cc8fe514aeba8ffc9e486ef1349d984473e4cfe41d49c5edd"
    ),
    "f1_0002_tenant_boundaries_and_workflow.py": (
        "710a2a88f76dadb16a890727f179faa6e44a5ddc27819bd3f6d8be8532b8ca3a"
    ),
    "f1_0003_security_boundaries.py": (
        "a8058d00719d26132b24671a4c802c4cea820d0b6ca1a3555a44fa58385d2da9"
    ),
    "f1_0004_repair_boundaries.py": (
        "b4befabca47939d7522bffbd8ed577717bead8f923e22120ed56ee138028d521"
    ),
    "f1_0005_business_workbench.py": (
        "e1f034cf731a08f2c17615a5f4570afe3bee3443734afac73e67c056d811feb6"
    ),
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(_source(path), filename=str(path))


def _definition(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"definition not found: {name}")


def _string_literals(path: Path, definition: str | None = None) -> tuple[str, ...]:
    target: ast.AST = _definition(path, definition) if definition else _tree(path)
    return tuple(
        node.value
        for node in ast.walk(target)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _called_names(path: Path, definition: str | None = None) -> set[str]:
    target: ast.AST = _definition(path, definition) if definition else _tree(path)
    names: set[str] = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            modules.update(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _router_paths(method: str) -> set[str]:
    paths: set[str] = set()
    for node in ast.walk(_tree(P3_ROUTER)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == method
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                paths.add(decorator.args[0].value)
    return paths


def _constraints(table, kind):
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, kind)
    }


def _fk_pairs(constraint: ForeignKeyConstraint) -> tuple[tuple[str, str], ...]:
    return tuple(
        (element.parent.name, element.target_fullname)
        for element in constraint.elements
    )


def _zip_bytes(
    entries: dict[str, bytes], *, compression: int = zipfile.ZIP_STORED
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return output.getvalue()


def _pdf_bytes() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def _pdf_with_aa_actions() -> bytes:
    from pypdf.generic import DictionaryObject, NameObject

    output = io.BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=72, height=72)
    additional = DictionaryObject()
    additional[NameObject("/O")] = DictionaryObject()
    page[NameObject("/AA")] = additional
    writer.write(output)
    return output.getvalue()


def _docx_bytes(text: str = "synthetic") -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    ).encode()
    return _zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "word/document.xml": document,
        }
    )


def _xlsx_bytes() -> bytes:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    workbook = f'<workbook xmlns="{namespace}"/>'.encode()
    sheet = (
        f'<worksheet xmlns="{namespace}"><sheetData><row r="1">'
        '<c r="A1" t="inlineStr"><is><t>synthetic</t></is></c>'
        '<c r="B1"><f>1+1</f><v>2</v></c>'
        "</row></sheetData></worksheet>"
    ).encode()
    return _zip_bytes(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": workbook,
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def _jpeg_bytes(*, width: int = 1, height: int = 1, metadata: bytes = b"") -> bytes:
    app1 = (
        b"\xff\xe1" + struct.pack(">H", len(metadata) + 2) + metadata
        if metadata
        else b""
    )
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 11)
        + b"\x08"
        + struct.pack(">HH", height, width)
        + b"\x01\x01\x11\x00"
    )
    sos = b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8" + app1 + sof0 + sos + b"\x00\xff\xd9"


class _SizedStream:
    """Seekable bounded-memory stream used to cross a real byte limit."""

    def __init__(self, prefix: bytes, size: int) -> None:
        self._prefix = prefix
        self._size = size
        self._position = 0

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence != 0 or offset < 0 or offset > self._size:
            raise OSError("P3_TEST_STREAM_SEEK_INVALID")
        self._position = offset
        return offset

    def read(self, size: int = -1) -> bytes:
        if self._position >= self._size:
            return b""
        count = self._size - self._position if size < 0 else min(
            size, self._size - self._position
        )
        start = self._position
        self._position += count
        body = bytearray(b"x" * count)
        overlap_start = max(start, 0)
        overlap_end = min(start + count, len(self._prefix))
        if overlap_end > overlap_start:
            body[overlap_start - start : overlap_end - start] = self._prefix[
                overlap_start:overlap_end
            ]
        return bytes(body)


class P3MigrationAndTenantContractTests(unittest.TestCase):
    def test_p3_stays_in_the_single_linear_f1_chain(self) -> None:
        script = ScriptDirectory.from_config(
            Config(str(ROOT / "infra/f1/alembic.ini"))
        )
        self.assertEqual(script.get_heads(), ["f1_0015"])
        self.assertEqual(script.get_revision("f1_0006").down_revision, "f1_0005")
        self.assertEqual(script.get_revision("f1_0015").down_revision, "f1_0014")
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from infra.f1.migrate_f1 import F1_DEFAULT_MIGRATE_TARGET

        self.assertEqual(F1_DEFAULT_MIGRATE_TARGET, "f1_0014")

    def test_p3_does_not_rewrite_frozen_f1_migrations(self) -> None:
        observed = {
            name: hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest()
            for name in FROZEN_MIGRATION_DIGESTS
        }
        self.assertEqual(observed, FROZEN_MIGRATION_DIGESTS)

    def test_p3_ddl_forces_rls_and_denies_public_writes(self) -> None:
        source = _source(P3_MIGRATION)
        self.assertIn(
            'for table in ("document_record", "document_version", "document_preview_unit"):',
            source,
        )
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)
        self.assertIn("FORCE ROW LEVEL SECURITY", source)
        self.assertIn("enterprise_id = f1.current_enterprise_id()", source)
        self.assertIn("f1.session_authorized", source)
        self.assertIn("REVOKE ALL ON f1.document_record", source)
        self.assertNotIn("ALTER TABLE f1.outbox", source)
        self.assertNotIn("CREATE TABLE f1.outbox", source)

    def test_version_and_preview_foreign_keys_are_tenant_composite(self) -> None:
        version = models.DocumentVersion.__table__
        version_fks = _constraints(version, ForeignKeyConstraint)
        self.assertEqual(
            _fk_pairs(version_fks["document_version_record_enterprise_fk"]),
            (
                ("enterprise_id", "f1.document_record.enterprise_id"),
                ("document_record_id", "f1.document_record.id"),
            ),
        )
        self.assertEqual(
            _fk_pairs(version_fks["document_version_source_enterprise_fk"]),
            (
                ("enterprise_id", "f1.document.enterprise_id"),
                ("source_document_id", "f1.document.id"),
            ),
        )
        self.assertEqual(
            _fk_pairs(version_fks["document_version_task_enterprise_fk"]),
            (
                ("enterprise_id", "f1.upload_task.enterprise_id"),
                ("upload_task_id", "f1.upload_task.id"),
            ),
        )
        preview_fks = _constraints(
            models.DocumentPreviewUnit.__table__, ForeignKeyConstraint
        )
        self.assertEqual(
            _fk_pairs(preview_fks["document_preview_unit_version_enterprise_fk"]),
            (
                ("enterprise_id", "f1.document_version.enterprise_id"),
                ("document_version_id", "f1.document_version.id"),
            ),
        )

    def test_version_identity_and_sequence_are_unique_per_tenant(self) -> None:
        uniques = _constraints(models.DocumentVersion.__table__, UniqueConstraint)
        self.assertEqual(
            tuple(
                column.name
                for column in uniques["document_version_record_version_uq"].columns
            ),
            ("enterprise_id", "document_record_id", "version_no"),
        )
        self.assertEqual(
            tuple(
                column.name
                for column in uniques["document_version_idempotency_uq"].columns
            ),
            ("enterprise_id", "idempotency_key_sha256"),
        )


class P3PipelineBoundaryTests(unittest.TestCase):
    def test_processor_forwards_a_bounded_real_scanner_endpoint(self) -> None:
        signature = inspect.signature(processor.process_controlled_ingestion)
        self.assertEqual(signature.parameters["scanner_host"].default, "clamd")
        self.assertEqual(signature.parameters["scanner_port"].default, 3310)
        source = inspect.getsource(processor.process_controlled_ingestion)
        self.assertIn("scan_stream,", source)
        self.assertIn("host=scanner_host", source)
        self.assertIn("port=scanner_port", source)
        self.assertNotIn("scan_stream =", source)

    def test_retry_post_resets_then_runs_the_real_processor(self) -> None:
        source = ast.get_source_segment(
            _source(P3_ROUTER),
            _definition(P3_ROUTER, "retry_version"),
        )
        self.assertIsNotNone(source)
        assert source is not None
        reset = source.index(
            'await act_on_version(tenant, version_id, action="retry")'
        )
        process = source.index(
            "await process_controlled_ingestion(tenant, version_id)"
        )
        result = source.index("return await get_version(tenant, version_id)")
        self.assertLess(reset, process)
        self.assertLess(process, result)

    def test_document_list_types_every_optional_postgres_bind(self) -> None:
        literals = " ".join(_string_literals(P3_SERVICE, "list_documents"))
        for marker in (
            "CAST(:content_type AS text)",
            "CAST(:status AS text)",
            "CAST(:cursor_updated_at AS timestamptz)",
            "CAST(:cursor_id AS uuid)",
        ):
            self.assertIn(marker, literals)

    def test_p3_sources_do_not_create_outbox_or_enter_legacy_indexing(self) -> None:
        paths = tuple(sorted(P3_FEATURES.glob("*.py"))) + (P3_ROUTER,)
        forbidden_calls = {
            "dispatch_pending_outbox",
            "enqueue_upload",
            "process_upload",
            "run_upload_pipeline",
        }
        forbidden_import_suffixes = (
            "indexing",
            "ragflow_provision",
            "worker_pipeline",
        )
        for path in paths:
            with self.subTest(path=path.name):
                literals = "\n".join(_string_literals(path))
                self.assertNotIn("f1.outbox", literals)
                self.assertNotIn("upload.dispatched", literals)
                self.assertNotIn("upload.indexing", literals)
                self.assertTrue(
                    forbidden_calls.isdisjoint(_called_names(path)),
                    f"legacy call in {path.name}",
                )
                self.assertFalse(
                    any(
                        module.endswith(forbidden_import_suffixes)
                        for module in _imported_modules(path)
                    ),
                    f"legacy import in {path.name}",
                )

    def test_upload_finalizes_held_before_router_starts_processing(self) -> None:
        finalize = " ".join(_string_literals(P3_SERVICE, "finalize_quarantine"))
        self.assertIn("object_state='quarantined'", finalize)
        self.assertIn("quarantine_status='held'", finalize)
        calls = _called_names(P3_SERVICE, "complete_upload")
        self.assertTrue(
            {
                "enqueue_scan",
                "enqueue_upload",
                "process_controlled_ingestion",
                "run_upload_pipeline",
            }.isdisjoint(calls)
        )
        router_source = _source(P3_ROUTER)
        for function_name in ("create_document", "append_version"):
            segment = ast.get_source_segment(
                router_source,
                _definition(P3_ROUTER, function_name),
            )
            self.assertIsNotNone(segment)
            assert segment is not None
            self.assertLess(
                segment.index("await complete_upload("),
                segment.index("await process_controlled_ingestion("),
            )

    def test_controlled_api_exposes_an_explicit_scan_preview_action(self) -> None:
        post_paths = _router_paths("post")
        controlled_paths = {
            path
            for path in post_paths
            if path.startswith("/versions/{version_id}/")
            and any(marker in path for marker in ("process", "scan", "preview"))
        }
        self.assertTrue(controlled_paths)

    def test_successful_processing_becomes_ready_but_remains_held(self) -> None:
        updates = [
            value
            for path in P3_FEATURES.glob("*.py")
            for value in _string_literals(path)
            if "UPDATE f1.upload_task" in value
        ]
        self.assertTrue(
            any(
                "object_state='ready'" in statement
                and "processing_stage='ready'" in statement
                and "scan_verdict='clean'" in statement
                and "preview_status='ready'" in statement
                and "quarantine_status='released'" not in statement
                for statement in updates
            )
        )

    def test_manual_release_is_a_ready_held_database_cas(self) -> None:
        statements = _string_literals(P3_SERVICE, "act_on_version")
        release_updates = [
            value
            for value in statements
            if "UPDATE f1.upload_task" in value
            and "quarantine_status='released'" in value
        ]
        self.assertEqual(len(release_updates), 1)
        release = release_updates[0]
        self.assertIn("released_at=statement_timestamp()", release)
        where_clause = release.partition("WHERE")[2]
        self.assertTrue(where_clause, "release must use one database CAS")
        for marker in (
            "object_state='ready'",
            "quarantine_status='held'",
            "scan_verdict='clean'",
            "preview_status='ready'",
        ):
            self.assertIn(marker, where_clause)
        self.assertNotIn("f1.outbox", "\n".join(statements))

    def test_scanner_unavailable_stays_held_in_retry_wait(self) -> None:
        source = "\n".join(_source(path) for path in P3_FEATURES.glob("*.py"))
        self.assertIn('stage = "retry_wait" if retryable', source)
        self.assertIn('quarantine_status = "held" if retryable', source)
        self.assertIn('scan_verdict="unavailable"', source)
        unavailable_handler = source.split("except ScanFailure as error:", 1)[1].split(
            "except PreviewFailure as error:", 1
        )[0]
        self.assertNotIn('scan_verdict="clean"', unavailable_handler)

    def test_retry_is_explicit_local_state_reset_only(self) -> None:
        statements = "\n".join(_string_literals(P3_SERVICE, "act_on_version"))
        for marker in (
            "processing_stage='received'",
            "scan_verdict='queued'",
            "preview_status='blocked'",
        ):
            self.assertIn(marker, statements)
        self.assertNotIn("f1.outbox", statements)
        self.assertNotIn("upload.dispatched", statements)

    def test_release_and_retry_actions_are_fail_closed(self) -> None:
        exact_ready = contracts.version_allowed_actions(
            "enterprise_admin",
            workflow_status="ready",
            scan_status="clean",
            preview_status="ready",
            quarantine_status="held",
            attempt=1,
            reason_code=None,
        )
        self.assertEqual(exact_ready, ["release", "reject"])

        scanner_unavailable = contracts.version_allowed_actions(
            "enterprise_admin",
            workflow_status="blocked",
            scan_status="unavailable",
            preview_status="blocked",
            quarantine_status="held",
            attempt=1,
            reason_code="SCAN_ENGINE_UNAVAILABLE",
        )
        self.assertEqual(scanner_unavailable, ["retry"])

        infected = contracts.version_allowed_actions(
            "enterprise_admin",
            workflow_status="blocked",
            scan_status="infected",
            preview_status="blocked",
            quarantine_status="blocked",
            attempt=1,
            reason_code="MALWARE_DETECTED",
        )
        self.assertEqual(infected, [])
        self.assertEqual(
            contracts.version_allowed_actions(
                "auditor",
                workflow_status="ready",
                scan_status="clean",
                preview_status="ready",
                quarantine_status="held",
                attempt=1,
                reason_code=None,
            ),
            [],
        )

    def test_public_models_do_not_expose_storage_or_scanner_internals(self) -> None:
        forbidden = {
            "object_key",
            "etag",
            "presigned_url",
            "absolute_path",
            "scanner_response",
            "scanner_signature",
        }
        for model in (
            contracts.VersionOut,
            contracts.DocumentSummaryOut,
            contracts.DocumentDetailOut,
            contracts.PreviewUnitOut,
            contracts.PreviewManifestOut,
        ):
            with self.subTest(model=model.__name__):
                self.assertTrue(forbidden.isdisjoint(model.model_fields))


class P3FormatScannerAndPreviewTests(unittest.TestCase):
    def assert_ingestion_error(self, code: str, callback) -> None:
        with self.assertRaises(contracts.IngestionError) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def assert_preview_error(self, code: str, callback) -> None:
        with self.assertRaises(preview.PreviewFailure) as context:
            callback()
        self.assertEqual(context.exception.code, code)

    def test_capabilities_publish_exactly_four_formats_and_frozen_limits(self) -> None:
        capability = contracts.capabilities(scanner_state="unavailable")
        self.assertEqual(
            {item.content_type for item in capability.allowed_types},
            {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "image/jpeg",
            },
        )
        self.assertEqual(capability.scanner.state, "unavailable")
        self.assertEqual(contracts.MAX_PDF_PAGES, 128)
        self.assertEqual(contracts.MAX_OOXML_ENTRIES, 2_048)
        self.assertEqual(contracts.MAX_OOXML_ENTRY_BYTES, 16 * 1024 * 1024)
        self.assertEqual(contracts.MAX_OOXML_EXPANDED_BYTES, 128 * 1024 * 1024)
        self.assertEqual(contracts.MAX_OOXML_COMPRESSION_RATIO, 100)
        self.assertEqual(contracts.MAX_JPEG_PIXELS, 40_000_000)
        self.assertEqual(contracts.MAX_JPEG_EDGE, 10_000)

    def test_preflight_accepts_the_four_exact_format_contracts(self) -> None:
        cases = (
            ("pdf", "synthetic.pdf", "application/pdf", _pdf_bytes()),
            (
                "docx",
                "synthetic.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                _docx_bytes(),
            ),
            (
                "xlsx",
                "synthetic.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                _xlsx_bytes(),
            ),
            ("jpeg", "synthetic.jpeg", "image/jpeg", _jpeg_bytes()),
        )
        for kind, filename, content_type, body in cases:
            with self.subTest(kind=kind):
                handle = io.BytesIO(body)
                observed = contracts.preflight_stream(
                    handle, filename=filename, content_type=content_type
                )
                self.assertEqual(observed.kind, kind)
                self.assertEqual(observed.size, len(body))
                self.assertEqual(
                    observed.content_sha256, hashlib.sha256(body).hexdigest()
                )
                self.assertEqual(handle.tell(), 0)

    def test_preflight_rejects_legacy_type_mismatch_and_real_oversize(self) -> None:
        self.assert_ingestion_error(
            "P3_FORMAT_NOT_ALLOWED",
            lambda: contracts.preflight_stream(
                io.BytesIO(b"\x89PNG\r\n\x1a\n"),
                filename="synthetic.png",
                content_type="image/png",
            ),
        )
        self.assert_ingestion_error(
            "P3_EXTENSION_MISMATCH",
            lambda: contracts.preflight_stream(
                io.BytesIO(_pdf_bytes()),
                filename="synthetic.docx",
                content_type="application/pdf",
            ),
        )
        self.assert_ingestion_error(
            "P3_CONTAINER_MISMATCH",
            lambda: contracts.preflight_stream(
                io.BytesIO(b"not-pdf"),
                filename="synthetic.pdf",
                content_type="application/pdf",
            ),
        )
        pdf_limit = contracts.ALLOWED_FORMATS["pdf"]
        self.assert_ingestion_error(
            "P3_FILE_TOO_LARGE",
            lambda: contracts.preflight_stream(
                _SizedStream(pdf_limit.magic, pdf_limit.max_bytes + 1),
                filename="synthetic.pdf",
                content_type=pdf_limit.content_type,
            ),
        )

    def test_pdf_preview_is_deterministic_and_rejects_active_content(self) -> None:
        body = _pdf_bytes()
        first = preview.build_preview("pdf", io.BytesIO(body))
        second = preview.build_preview("pdf", io.BytesIO(body))
        self.assertEqual(first.kind, "page_text")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.unit_count, 1)
        self.assertEqual(first.units[0].content_type, "application/json")
        self.assert_preview_error(
            "P3_PDF_ACTIVE_CONTENT",
            lambda: preview.build_preview(
                "pdf", io.BytesIO(body + b"\n/JavaScript")
            ),
        )
        preview.build_preview("pdf", io.BytesIO(body + b"\n/AA*\xd9binary"))
        self.assert_preview_error(
            "P3_PDF_ACTIVE_CONTENT",
            lambda: preview.build_preview("pdf", io.BytesIO(_pdf_with_aa_actions())),
        )

    def test_docx_preview_rejects_external_relationship_and_zip_bomb(self) -> None:
        result = preview.build_preview("docx", io.BytesIO(_docx_bytes()))
        self.assertEqual(result.kind, "page_text")
        self.assertEqual(result.unit_count, 1)

        unsafe = _zip_bytes(
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": (
                    b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                    b'wordprocessingml/2006/main"><w:body/></w:document>'
                ),
                "word/_rels/document.xml.rels": (
                    b'<Relationships><Relationship TargetMode="External" '
                    b'Target="https://invalid.example/"/></Relationships>'
                ),
            }
        )
        self.assert_preview_error(
            "P3_OOXML_EXTERNAL_RELATIONSHIP",
            lambda: preview.build_preview("docx", io.BytesIO(unsafe)),
        )

        compressed = _zip_bytes(
            {
                "[Content_Types].xml": b"<Types/>",
                "word/document.xml": b"<root>" + b"x" * 1_000_000 + b"</root>",
            },
            compression=zipfile.ZIP_DEFLATED,
        )
        self.assert_preview_error(
            "P3_OOXML_COMPRESSION_LIMIT",
            lambda: preview.build_preview("docx", io.BytesIO(compressed)),
        )

    def test_xlsx_preview_never_evaluates_formula(self) -> None:
        result = preview.build_preview("xlsx", io.BytesIO(_xlsx_bytes()))
        self.assertEqual(result.kind, "sheet_grid")
        self.assertEqual(result.unit_count, 1)
        self.assertEqual(result.units[0].grid, [["synthetic", "[FORMULA]"]])
        decoded = json.loads(result.units[0].content)
        self.assertEqual(decoded, [["synthetic", "[FORMULA]"]])

    def test_jpeg_preview_strips_metadata_and_enforces_pixel_limit(self) -> None:
        metadata = b"synthetic-metadata"
        body = _jpeg_bytes(metadata=metadata)
        result = preview.build_preview("jpeg", io.BytesIO(body))
        self.assertEqual(result.kind, "image")
        self.assertEqual(result.unit_count, 1)
        self.assertNotIn(metadata, result.units[0].content)
        self.assertEqual(result.units[0].width_px, 1)
        self.assertEqual(result.units[0].height_px, 1)
        self.assert_preview_error(
            "P3_JPEG_PIXEL_LIMIT",
            lambda: preview.build_preview(
                "jpeg",
                io.BytesIO(_jpeg_bytes(width=10_000, height=10_000)),
            ),
        )

    def test_clamd_wire_parser_is_body_free_and_fail_closed(self) -> None:
        version = scanner.parse_clamd_version(b"ClamAV 1.4.2/27123/ignored\x00")
        clean = scanner.parse_clamd_response(b"stream: OK\x00", version=version)
        infected = scanner.parse_clamd_response(
            b"stream: Synthetic.Signature FOUND\x00", version=version
        )
        self.assertEqual(clean.verdict, "clean")
        self.assertIsNone(clean.reason_code)
        self.assertEqual(infected.verdict, "infected")
        self.assertEqual(infected.reason_code, "P3_MALWARE_DETECTED")
        self.assertNotIn("Synthetic.Signature", repr(infected))
        with self.assertRaises(scanner.ScanFailure) as context:
            scanner.parse_clamd_response(b"stream: unavailable ERROR\x00")
        self.assertTrue(context.exception.retryable)
        self.assertEqual(context.exception.code, "P3_SCAN_ENGINE_ERROR")


if __name__ == "__main__":
    unittest.main()
