from __future__ import annotations

import dataclasses
import hashlib
import os
from pathlib import Path
import runpy
import stat
import subprocess
import tempfile
import unittest
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from psycopg.types.json import Jsonb

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
    seed_local_foundation,
)
from platform_foundation.database import (
    DatabaseConfig,
    DatabaseError,
    tenant_transaction,
)
from platform_foundation.f0_isolation import load_frozen_f0_isolation


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
_PRIVATE_TMP = (
    str(_FROZEN_F0_ISOLATION.tmp_dir)
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp"
)
BOOTSTRAP_DSN = (
    _FROZEN_F0_ISOLATION.dsn_for("f0d_bootstrap", "postgres")
    if _FROZEN_F0_ISOLATION is not None
    else "postgresql://f0d_bootstrap:f0d-bootstrap-local-v01@127.0.0.1:55432/postgres"
)


def _database_config(database_name: str) -> DatabaseConfig:
    if _FROZEN_F0_ISOLATION is not None:
        if database_name != _FROZEN_F0_ISOLATION.database_name("f0f-test"):
            raise AssertionError("unsafe isolated F0F database name")
        return _FROZEN_F0_ISOLATION.database_config(database_name)
    base = "127.0.0.1:55432/" + database_name
    return DatabaseConfig(
        migration_dsn="postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        runtime_dsn="postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        worker_dsn="postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    )


def _f0f_container_prefix() -> str:
    from platform_foundation.f0e.supervisor import _container_prefix

    return _container_prefix("f0f")


def _f0f_residual_filter() -> str:
    return "name=^/" + _f0f_container_prefix()


class F0FContractTests(unittest.TestCase):
    def test_public_contract_is_available(self) -> None:
        from platform_foundation.f0f import (
            AnnotationCandidate,
            BodyConfiguration,
            BoundPageBody,
            CanonicalBody,
            F0FError,
            LocalFixtureKey,
            OcrBlock,
            OcrBodyResult,
            PageBodyMetadata,
            RuntimeBundle,
            bind_native_body,
            bind_ocr_body,
            create_keyfile,
            extract_native_page,
            load_keyfile,
            load_runtime_bundle,
            select_annotation_candidates,
        )

        exported = (
            AnnotationCandidate,
            BodyConfiguration,
            BoundPageBody,
            CanonicalBody,
            F0FError,
            LocalFixtureKey,
            OcrBlock,
            OcrBodyResult,
            PageBodyMetadata,
            RuntimeBundle,
            bind_native_body,
            bind_ocr_body,
            create_keyfile,
            extract_native_page,
            load_keyfile,
            load_runtime_bundle,
            select_annotation_candidates,
        )
        self.assertEqual(len(exported), 17)

    def test_unknown_error_is_redacted(self) -> None:
        from platform_foundation.f0f import F0FError

        error = F0FError("SYNTHETIC_PRIVATE_VALUE")
        self.assertEqual(str(error), "BODY_CONTRACT_INVALID")
        self.assertNotIn("PRIVATE", repr(error.to_dict()))

    def test_canonical_body_repr_redacts_bytes(self) -> None:
        from platform_foundation.f0f.contracts import native_body

        body = native_body("SYNTHETIC_ALPHA")
        try:
            self.assertNotIn("ALPHA", repr(body))
            self.assertEqual(body.sha256, hashlib.sha256(b"SYNTHETIC_ALPHA").hexdigest())
        finally:
            body.wipe()

    def test_canonical_body_context_wipes_buffer(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.contracts import native_body

        with native_body("SYNTHETIC_BETA") as body:
            self.assertEqual(bytes(body.view()), b"SYNTHETIC_BETA")
        with self.assertRaisesRegex(F0FError, "BODY_CONTRACT_INVALID"):
            body.view()

    def test_empty_ocr_body_is_well_formed(self) -> None:
        from platform_foundation.f0f.contracts import ocr_body

        with ocr_body(()) as body:
            self.assertEqual((body.byte_count, body.character_count), (0, 0))
            self.assertEqual(body.sha256, hashlib.sha256(b"").hexdigest())

    def test_canonical_body_rejects_byte_limit(self) -> None:
        from platform_foundation.f0f import CanonicalBody, F0FError

        with self.assertRaisesRegex(F0FError, "BODY_LIMIT_EXCEEDED"):
            CanonicalBody(
                b"12345",
                characters=5,
                nonblank_characters=5,
                normalization_rule="synthetic-v1",
                maximum_bytes=4,
            )

    def test_native_body_rejects_unpaired_surrogate(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.contracts import native_body

        with self.assertRaisesRegex(F0FError, "BODY_NORMALIZATION_FAILED"):
            native_body("\ud800")

    def test_ocr_body_normalizes_nfc_and_line_endings(self) -> None:
        from platform_foundation.f0f.contracts import ocr_body

        with ocr_body(("e\u0301\r\nX", "Y\rZ")) as body:
            self.assertEqual(bytes(body.view()).decode("utf-8"), "é\nX\nY\nZ")

    def test_ocr_body_preserves_block_order(self) -> None:
        from platform_foundation.f0f.contracts import ocr_body

        with ocr_body(("THIRD", "FIRST", "SECOND")) as body:
            self.assertEqual(bytes(body.view()), b"THIRD\nFIRST\nSECOND")

    def test_ocr_block_repr_redacts_text(self) -> None:
        from platform_foundation.f0f import OcrBlock

        block = OcrBlock(
            index=0,
            text="SYNTHETIC_GAMMA",
            bbox=((0, 0), (1, 0), (1, 1), (0, 1)),
            confidence_ppm=900_000,
        )
        self.assertNotIn("GAMMA", repr(block))

    def test_ocr_block_rejects_boolean_coordinate(self) -> None:
        from platform_foundation.f0f import F0FError, OcrBlock

        with self.assertRaisesRegex(F0FError, "RUNNER_OUTPUT_INVALID"):
            OcrBlock(
                index=0,
                text="X",
                bbox=((False, 0), (1, 0), (1, 1), (0, 1)),
                confidence_ppm=1,
            )

    def test_body_configuration_rejects_production(self) -> None:
        from platform_foundation.f0f import BodyConfiguration, F0FError

        with self.assertRaisesRegex(F0FError, "BODY_CONFIGURATION_INVALID"):
            BodyConfiguration(
                configuration_id=uuid.uuid4(),
                configuration_sha256="1" * 64,
                key_fingerprint_sha256="2" * 64,
                f0e_execution_profile_sha256="3" * 64,
                runner_image_id="sha256:" + "4" * 64,
                normalization_profile_sha256="5" * 64,
                production_allowed=True,
            )

    def test_annotation_candidate_cannot_claim_gold(self) -> None:
        from platform_foundation.f0f import AnnotationCandidate, F0FError

        with self.assertRaisesRegex(F0FError, "BODY_CONTRACT_INVALID"):
            AnnotationCandidate(
                queue_id=uuid.uuid4(),
                processing_unit_id=uuid.uuid4(),
                selected_route="LOCAL_OCR",
                queue_ordinal=1,
                status="GOLD",
            )

    def test_bound_native_body_rejects_wrong_page_output_hash(self) -> None:
        from platform_foundation.f0f import BoundPageBody, F0FError
        from platform_foundation.f0f.contracts import native_body

        body = native_body("SYNTHETIC_BOUND_NATIVE")
        try:
            with self.assertRaisesRegex(F0FError, "BODY_EVIDENCE_MISMATCH"):
                BoundPageBody(
                    page_evidence_id=uuid.uuid4(),
                    selected_route="NATIVE_REFERENCE",
                    source_output_sha256="0" * 64,
                    source_page_evidence_sha256="1" * 64,
                    body=body,
                )
        finally:
            body.wipe()

    def test_bound_ocr_body_recomputes_f0e_sequence_from_block_lengths(self) -> None:
        from platform_foundation.f0f import BoundPageBody
        from platform_foundation.f0f.contracts import ocr_body

        texts = ("A", "BC")
        digest = hashlib.sha256(b"F0E_TEXT_SEQUENCE_V1\0ocr-text-nfc-lf-v1\0")
        for index, text in enumerate(texts):
            encoded = text.encode("utf-8")
            digest.update(index.to_bytes(4, "big"))
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        body = ocr_body(texts)
        bound = BoundPageBody(
            page_evidence_id=uuid.uuid4(),
            selected_route="LOCAL_OCR",
            source_output_sha256=digest.hexdigest(),
            source_page_evidence_sha256="2" * 64,
            body=body,
            ocr_block_byte_lengths=(1, 2),
        )
        try:
            self.assertNotIn("BC", repr(bound))
        finally:
            bound.wipe()


class F0FKeyfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = (
            str(_FROZEN_F0_ISOLATION.f0f_key_file)
            if _FROZEN_F0_ISOLATION is not None
            else "/private/tmp/anhuan-f0f-test-" + uuid.uuid4().hex + ".key"
        )
        self.original_key: bytearray | None = None
        if _FROZEN_F0_ISOLATION is not None:
            self.original_key = bytearray(Path(self.path).read_bytes())
            os.unlink(self.path)
        self.created: list[str] = []

    def tearDown(self) -> None:
        for path in reversed(self.created):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        if self.original_key is not None:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            try:
                if os.write(descriptor, self.original_key) != len(self.original_key):
                    raise AssertionError("short test key restore")
            finally:
                os.close(descriptor)
                self.original_key[:] = b"\0" * len(self.original_key)
                self.original_key.clear()

    def _create(self, path: str | None = None) -> str:
        from platform_foundation.f0f import create_keyfile

        target = self.path if path is None else path
        self.created.append(target)
        return create_keyfile(target)

    def test_create_keyfile_has_exact_mode_size_and_link_count(self) -> None:
        fingerprint = self._create()
        metadata = os.lstat(self.path)
        self.assertEqual(
            (stat.S_IMODE(metadata.st_mode), metadata.st_size, metadata.st_nlink),
            (0o600, 32, 1),
        )
        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")

    def test_create_keyfile_rejects_non_private_tmp_path(self) -> None:
        from platform_foundation.f0f import F0FError, create_keyfile

        with tempfile.TemporaryDirectory(
            dir=_PRIVATE_TMP if _FROZEN_F0_ISOLATION is not None else None
        ) as root, self.assertRaisesRegex(
            F0FError, "KEYFILE_INVALID"
        ):
            create_keyfile(str(Path(root) / "anhuan-f0f-test.key"))

    def test_create_keyfile_never_overwrites(self) -> None:
        from platform_foundation.f0f import F0FError, create_keyfile

        self._create()
        with self.assertRaisesRegex(F0FError, "KEYFILE_ALREADY_EXISTS"):
            create_keyfile(self.path)

    def test_loaded_key_repr_is_redacted(self) -> None:
        from platform_foundation.f0f import load_keyfile

        fingerprint = self._create()
        with load_keyfile(self.path) as key:
            self.assertNotIn(bytes(key.view()).hex(), repr(key))
            self.assertEqual(key.fingerprint_sha256, fingerprint)

    def test_loaded_key_is_unavailable_after_context(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        self._create()
        with load_keyfile(self.path) as key:
            self.assertEqual(len(key.view()), 32)
        with self.assertRaisesRegex(F0FError, "KEYFILE_INVALID"):
            key.view()

    def test_load_rejects_world_readable_mode(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        self._create()
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(F0FError, "KEYFILE_INVALID"):
            load_keyfile(self.path)

    def test_load_rejects_hardlink(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        self._create()
        linked = self.path.removesuffix(".key") + "-linked.key"
        os.link(self.path, linked)
        self.created.append(linked)
        with self.assertRaisesRegex(F0FError, "KEYFILE_INVALID"):
            load_keyfile(self.path)

    def test_load_rejects_symlink(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        real = self.path.removesuffix(".key") + "-real.key"
        link = self.path.removesuffix(".key") + "-link.key"
        if _FROZEN_F0_ISOLATION is not None:
            self._create()
            real = self.path
        else:
            self._create(real)
        os.symlink(real, link)
        self.created.append(link)
        expected = (
            "KEYFILE_INVALID"
            if _FROZEN_F0_ISOLATION is not None
            else "KEYFILE_NOT_AVAILABLE"
        )
        with self.assertRaisesRegex(F0FError, expected):
            load_keyfile(link)

    def test_load_rejects_fifo_without_blocking(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        os.mkfifo(self.path, 0o600)
        self.created.append(self.path)
        with self.assertRaisesRegex(F0FError, "KEYFILE_INVALID"):
            load_keyfile(self.path)

    def test_load_rejects_oversized_key(self) -> None:
        from platform_foundation.f0f import F0FError, load_keyfile

        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.created.append(self.path)
        try:
            os.write(descriptor, b"X" * 33)
        finally:
            os.close(descriptor)
        with self.assertRaisesRegex(F0FError, "KEYFILE_INVALID"):
            load_keyfile(self.path)


def _selection_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for document in range(7):
        for page in range(2):
            rows.append(
                {
                    "source_group": "core",
                    "source_document_id": f"{document + 1:064x}",
                    "source_unit_id": f"{100 + document * 2 + page:064x}",
                    "processing_unit_id": uuid.UUID(
                        f"10000000-0000-4000-8000-{document * 2 + page + 1:012x}"
                    ),
                    "selected_route": "LOCAL_OCR",
                }
            )
    for document in range(5):
        rows.append(
            {
                "source_group": "core",
                "source_document_id": f"{100 + document:064x}",
                "source_unit_id": f"{200 + document:064x}",
                "processing_unit_id": uuid.UUID(
                    f"20000000-0000-4000-8000-{document + 1:012x}"
                ),
                "selected_route": "NATIVE_REFERENCE",
            }
        )
    return rows


class F0FSelectionTests(unittest.TestCase):
    def test_selection_has_exact_strata_and_ordinals(self) -> None:
        from platform_foundation.f0f import select_annotation_candidates

        selected = select_annotation_candidates(_selection_rows())
        self.assertEqual(len(selected), 15)
        self.assertEqual(
            [item.selected_route for item in selected].count("LOCAL_OCR"), 10
        )
        self.assertEqual(
            [item.selected_route for item in selected].count("NATIVE_REFERENCE"), 5
        )
        self.assertEqual([item.queue_ordinal for item in selected], list(range(1, 16)))

    def test_selection_is_deterministic_under_input_reversal(self) -> None:
        from platform_foundation.f0f import select_annotation_candidates

        rows = _selection_rows()
        self.assertEqual(
            select_annotation_candidates(rows),
            select_annotation_candidates(reversed(rows)),
        )

    def test_selection_covers_all_seven_ocr_documents_first(self) -> None:
        from platform_foundation.f0f import select_annotation_candidates

        rows = _selection_rows()
        document_by_unit = {
            row["processing_unit_id"]: row["source_document_id"] for row in rows
        }
        selected = select_annotation_candidates(rows)
        covered = {
            document_by_unit[item.processing_unit_id]
            for item in selected
            if item.selected_route == "LOCAL_OCR"
        }
        self.assertEqual(len(covered), 7)

    def test_negative_rows_are_never_selected(self) -> None:
        from platform_foundation.f0f import select_annotation_candidates

        rows = _selection_rows()
        negative_id = uuid.UUID("30000000-0000-4000-8000-000000000001")
        rows.append(
            {
                "source_group": "negative",
                "source_document_id": "f" * 64,
                "source_unit_id": "e" * 64,
                "processing_unit_id": negative_id,
                "selected_route": "LOCAL_OCR",
            }
        )
        self.assertNotIn(
            negative_id,
            {
                item.processing_unit_id
                for item in select_annotation_candidates(rows)
            },
        )

    def test_duplicate_processing_unit_is_rejected(self) -> None:
        from platform_foundation.f0f import F0FError, select_annotation_candidates

        rows = _selection_rows()
        rows.append(dict(rows[0]))
        with self.assertRaisesRegex(F0FError, "BODY_EVIDENCE_MISMATCH"):
            select_annotation_candidates(rows)

    def test_insufficient_ocr_candidates_is_rejected(self) -> None:
        from platform_foundation.f0f import F0FError, select_annotation_candidates

        rows = [
            row
            for row in _selection_rows()
            if row["selected_route"] == "NATIVE_REFERENCE"
        ]
        with self.assertRaisesRegex(F0FError, "BODY_REPLAY_MISMATCH"):
            select_annotation_candidates(rows)


class F0FRuntimeIdentityTests(unittest.TestCase):
    def test_runtime_bundle_matches_frozen_identity(self) -> None:
        from platform_foundation.f0f import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertEqual(
            bundle.container_image_id,
            "sha256:7316755e9776033453420b11292ed481b253196dc9db4bbe596a149dcd1a0a64",
        )
        self.assertEqual(
            bundle.base_container_image_id,
            "sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a",
        )

    def test_runtime_limits_keep_private_output_separate_from_body(self) -> None:
        from platform_foundation.f0f import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertEqual(bundle.maximum_private_output_bytes, 8 * 1024 * 1024)
        self.assertEqual(bundle.maximum_body_bytes, 4 * 1024 * 1024)

    def test_body_docker_argv_denies_network_and_mounts(self) -> None:
        from platform_foundation.f0f.runtime_config import runtime_paths
        from platform_foundation.f0f.supervisor import body_docker_argv
        from platform_foundation.f0f import load_runtime_bundle

        bundle = load_runtime_bundle()
        argv = body_docker_argv(*runtime_paths(), bundle.container_image_id)
        self.assertIn("none", argv[argv.index("--network") + 1 : argv.index("--network") + 2])
        self.assertNotIn("-v", argv)
        self.assertNotIn("--mount", argv)
        self.assertEqual(argv[-1], bundle.container_image_id)

    def test_runtime_lock_hash_matches_loaded_bundle(self) -> None:
        from platform_foundation.f0f import load_runtime_bundle

        root = Path(__file__).resolve().parents[1]
        expected = hashlib.sha256(
            (root / "infra/f0f/runtime-lock.json").read_bytes()
        ).hexdigest()
        self.assertEqual(load_runtime_bundle().lock_sha256, expected)

    def test_locked_file_reader_accepts_owned_regular_file(self) -> None:
        from platform_foundation.f0f.runtime_config import _read_owned_regular

        with tempfile.NamedTemporaryFile(dir=_PRIVATE_TMP, delete=False) as handle:
            path = Path(handle.name)
            handle.write(b"SYNTHETIC_LOCK")
        try:
            os.chmod(path, 0o600)
            self.assertEqual(_read_owned_regular(path), b"SYNTHETIC_LOCK")
        finally:
            path.unlink(missing_ok=True)

    def test_locked_file_reader_rejects_hardlink(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.runtime_config import _read_owned_regular

        with tempfile.NamedTemporaryFile(dir=_PRIVATE_TMP, delete=False) as handle:
            path = Path(handle.name)
            handle.write(b"SYNTHETIC_LOCK")
        linked = path.with_name(path.name + "-linked")
        try:
            os.chmod(path, 0o600)
            os.link(path, linked)
            with self.assertRaisesRegex(F0FError, "RUNNER_CONFIGURATION_INVALID"):
                _read_owned_regular(path)
        finally:
            linked.unlink(missing_ok=True)
            path.unlink(missing_ok=True)

    def test_locked_file_reader_rejects_symlink(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.runtime_config import _read_owned_regular

        with tempfile.NamedTemporaryFile(dir=_PRIVATE_TMP, delete=False) as handle:
            path = Path(handle.name)
            handle.write(b"SYNTHETIC_LOCK")
        linked = path.with_name(path.name + "-linked")
        try:
            os.chmod(path, 0o600)
            os.symlink(path, linked)
            with self.assertRaisesRegex(F0FError, "RUNNER_CONFIGURATION_INVALID"):
                _read_owned_regular(linked)
        finally:
            linked.unlink(missing_ok=True)
            path.unlink(missing_ok=True)

    def test_locked_file_reader_rejects_fifo_without_blocking(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.runtime_config import _read_owned_regular

        path = Path(_PRIVATE_TMP) / ("f0f-runtime-fifo-" + uuid.uuid4().hex)
        try:
            os.mkfifo(path, 0o600)
            with self.assertRaisesRegex(F0FError, "RUNNER_CONFIGURATION_INVALID"):
                _read_owned_regular(path)
        finally:
            path.unlink(missing_ok=True)

    def test_live_seccomp_denies_namespace_and_new_mount_syscalls(self) -> None:
        from platform_foundation.f0f import load_runtime_bundle
        from platform_foundation.f0f.runtime_config import runtime_paths
        from platform_foundation.f0f.supervisor import body_docker_argv

        bundle = load_runtime_bundle()
        argv = list(
            body_docker_argv(*runtime_paths(), bundle.container_image_id)
        )
        image = argv.pop()
        argv.extend(("--entrypoint", "python3", image, "-I", "-B", "-c"))
        argv.append(
            "import ctypes;L=ctypes.CDLL(None,use_errno=True);"
            "S=[('clone3',435,(0,0)),"
            "('clone_newuser',220,(268435473,0,0,0,0)),"
            "('open_tree',428,(-100,0,0)),"
            "('move_mount',429,(-1,0,-1,0,0)),"
            "('fsopen',430,(0,0)),('fsconfig',431,(-1,0,0,0,0)),"
            "('fsmount',432,(-1,0,0)),('fspick',433,(-100,0,0)),"
            "('mount_setattr',442,(-100,0,0,0,0))];R=[];"
            "[(ctypes.set_errno(0),R.append((n,L.syscall(q,*a),"
            "ctypes.get_errno()))) for n,q,a in S];"
            "print(' '.join(n+'='+str(e) for n,r,e in R))"
        )
        result = subprocess.run(
            argv, check=False, capture_output=True, timeout=30
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            result.stdout.strip().split(),
            [
                b"clone3=1",
                b"clone_newuser=1",
                b"open_tree=1",
                b"move_mount=1",
                b"fsopen=1",
                b"fsconfig=1",
                b"fsmount=1",
                b"fspick=1",
                b"mount_setattr=1",
            ],
        )


class F0FNativeParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        probe = runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "infra/f0f/synthetic_probe.py")
        )
        cls.pdf = probe["_minimal_pdf"]()
        cls.text = "F0F SYNTHETIC 123"

    def _route(self, **changes: object) -> object:
        from platform_foundation.f0e.contracts import PageRoute

        values: dict[str, object] = {
            "processing_unit_id": uuid.uuid4(),
            "processing_plan_id": uuid.uuid4(),
            "source_unit_id": "1" * 64,
            "unit_ordinal": 1,
            "unit_kind": "PAGE",
            "page_no": 1,
            "candidate_decision": "NATIVE_CANDIDATE",
            "evidence_method": "NATIVE_REFERENCE",
            "reason_codes": ("NATIVE_TEXT_THRESHOLD_MET",),
            "source_evidence_sha256": "2" * 64,
            "route_sha256": "3" * 64,
            "native_text_sha256": hashlib.sha256(self.text.encode()).hexdigest(),
            "native_characters": 15,
            "rotation": 0,
            "media_box": ("0.000", "0.000", "612.000", "792.000"),
            "crop_box": ("0.000", "0.000", "612.000", "792.000"),
            "expected_total_pages": 1,
        }
        values.update(changes)
        return PageRoute(**values)

    def _extract(self, route: object, source: bytes | None = None) -> object:
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.f0f import extract_native_page
        from platform_foundation.vault import LocalFixtureVault

        with tempfile.TemporaryDirectory(
            prefix="f0f-native-", dir=_PRIVATE_TMP
        ) as root, LocalFixtureVault(root) as vault:
            stored = vault.store_bytes(self.pdf if source is None else source)
            with open_verified_source(
                vault, stored.object_id, stored.sha256, stored.size
            ) as verified:
                return extract_native_page(verified, route)

    def test_native_parser_matches_f0c_hash_and_count(self) -> None:
        body = self._extract(self._route())
        try:
            self.assertEqual(bytes(body.view()), self.text.encode())
            self.assertEqual(body.sha256, hashlib.sha256(self.text.encode()).hexdigest())
        finally:
            body.wipe()

    def test_native_parser_rejects_wrong_f0c_hash(self) -> None:
        from platform_foundation.f0f import F0FError

        with self.assertRaisesRegex(F0FError, "NATIVE_TEXT_MISMATCH"):
            self._extract(self._route(native_text_sha256="0" * 64))

    def test_native_parser_rejects_wrong_f0c_count(self) -> None:
        from platform_foundation.f0f import F0FError

        with self.assertRaisesRegex(F0FError, "NATIVE_TEXT_MISMATCH"):
            self._extract(self._route(native_characters=14))

    def test_native_parser_rejects_ocr_route(self) -> None:
        from platform_foundation.f0f import F0FError

        with self.assertRaisesRegex(F0FError, "NATIVE_PARSE_FAILED"):
            self._extract(
                self._route(
                    candidate_decision="FULL_PAGE_OCR_REQUIRED",
                    evidence_method="LOCAL_OCR",
                    native_text_sha256=None,
                    native_characters=0,
                )
            )

    def test_native_parser_rejects_corrupt_pdf(self) -> None:
        from platform_foundation.f0f import F0FError

        with self.assertRaisesRegex(F0FError, "NATIVE_PARSE_FAILED"):
            self._extract(self._route(), source=b"%PDF-1.4\nBROKEN")


class F0FDatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0f-test")
            if _FROZEN_F0_ISOLATION is not None
            else f"f0f_test_{uuid.uuid4().hex[:16]}"
        )
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0").format(
                    sql.Identifier(cls.database_name)
                )
            )
        finally:
            admin.close()
        base = BOOTSTRAP_DSN.rsplit("/", 1)[0]
        cls.database_admin_dsn = base + "/" + cls.database_name
        admin = psycopg.connect(cls.database_admin_dsn, autocommit=True)
        try:
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            admin.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(cls.database_name)
                )
            )
            admin.execute(
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO f0d_runtime, f0d_worker"
                ).format(sql.Identifier(cls.database_name))
            )
            admin.execute("CREATE SCHEMA f0d AUTHORIZATION f0d_migration")
            admin.execute("REVOKE ALL ON SCHEMA f0d FROM PUBLIC")
        finally:
            admin.close()
        cls.config = _database_config(cls.database_name)
        previous = os.environ.get("F0D_MIGRATION_DSN")
        os.environ["F0D_MIGRATION_DSN"] = cls.config.migration_dsn.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        try:
            command.upgrade(Config("alembic.ini"), "f0d_0004")
        finally:
            if previous is None:
                os.environ.pop("F0D_MIGRATION_DSN", None)
            else:
                os.environ["F0D_MIGRATION_DSN"] = previous
        seed_local_foundation(cls.config)
        cls.context_a = authenticate_local_session(cls.config, LOCAL_TENANT_A_TOKEN)
        cls.context_b = authenticate_local_session(cls.config, LOCAL_TENANT_B_TOKEN)
        cls.key_path = (
            str(_FROZEN_F0_ISOLATION.f0f_key_file)
            if _FROZEN_F0_ISOLATION is not None
            else "/private/tmp/anhuan-f0f-dbtest-" + uuid.uuid4().hex + ".key"
        )
        from platform_foundation.f0f import create_keyfile, load_keyfile
        from platform_foundation.f0f.runtime_config import load_runtime_bundle
        from platform_foundation.f0f.service import ControlledBodyService

        if _FROZEN_F0_ISOLATION is None:
            create_keyfile(cls.key_path)
        cls.runtime = load_runtime_bundle()
        cls.service = ControlledBodyService(cls.config)
        with load_keyfile(cls.key_path) as key:
            cls.configuration = cls.service.register_configuration(
                cls.context_a, cls.runtime, key
            )
        if _FROZEN_F0_ISOLATION is not None:
            cls.vault_temp = None
            cls.vault_root = str(_FROZEN_F0_ISOLATION.f0f_vault_root)
        else:
            cls.vault_temp = tempfile.TemporaryDirectory(
                prefix="anhuan-f0f-dbtest-vault-", dir="/private/tmp"
            )
            cls.vault_root = cls.vault_temp.name
        cls._prepare_real_smoke_chain()

    @classmethod
    def tearDownClass(cls) -> None:
        if _FROZEN_F0_ISOLATION is None:
            try:
                os.unlink(cls.key_path)
            except FileNotFoundError:
                pass
        if cls.vault_temp is not None:
            cls.vault_temp.cleanup()
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(cls.database_name)
                )
            )
        finally:
            admin.close()

    @classmethod
    def _set_migration_context(cls, connection: object) -> None:
        connection.execute(  # type: ignore[attr-defined]
            "SELECT set_config('f0d.enterprise_id',%s,true),"
            "set_config('f0d.actor_id',%s,true),"
            "set_config('f0d.session_token_sha256',%s,true)",
            (
                str(cls.context_a.enterprise_id),
                str(cls.context_a.actor_id),
                cls.context_a.session_token_sha256,
            ),
        )

    @classmethod
    def _prepare_real_smoke_chain(cls) -> None:
        from platform_foundation.f0e.replay import assemble_run_envelope
        from platform_foundation.f0e.hashing import stable_uuid4
        from platform_foundation.f0e.runtime_config import (
            load_runtime_bundle as load_f0e_runtime_bundle,
            register_runtime_configuration,
            runtime_paths as f0e_runtime_paths,
        )
        from platform_foundation.f0e.service import LocalOcrService
        from platform_foundation.f0e.supervisor import (
            FixedArgvSandboxSupervisor,
            docker_argv as f0e_docker_argv,
        )
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.f0f.native import extract_native_page
        from platform_foundation.f0f.runtime_config import runtime_paths
        from platform_foundation.f0f.supervisor import (
            ControlledBodySupervisor,
            body_docker_argv,
        )
        from platform_foundation.f0f.service import bind_native_body, bind_ocr_body
        from platform_foundation.replay import replay_profile
        from platform_foundation.vault import LocalFixtureVault

        foundation = replay_profile(cls.config, "smoke", vault_root=cls.vault_root)
        if int(foundation["selected_documents"]) != 10:
            raise AssertionError("unexpected synthetic acceptance fixture count")

        f0e_bundle = load_f0e_runtime_bundle()
        f0e_configuration = register_runtime_configuration(
            cls.config, cls.context_a, f0e_bundle
        )
        f0e_service = LocalOcrService(cls.config)
        with tenant_transaction(
            cls.config, "f0d_runtime", cls.context_a
        ) as connection:
            plans = connection.execute(
                "SELECT p.id FROM f0d.document_processing_plan p JOIN "
                "f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                "AND r.source_document_id=p.source_document_id WHERE "
                "r.document_type IN ('PDF','JPEG','DOC') "
                "ORDER BY p.source_document_id"
            ).fetchall()
        for plan in plans:
            f0e_service.enqueue(
                cls.context_a, plan["id"], f0e_configuration.configuration_id
            )
        f0e_supervisor = FixedArgvSandboxSupervisor(
            f0e_docker_argv(
                *f0e_runtime_paths(), f0e_bundle.container_image_id
            ),
            f0e_bundle.sandbox_profile,
            f0e_bundle.resource_limits,
        )
        f0e_ocr_calls = 0
        with LocalFixtureVault(cls.vault_root) as vault:
            while True:
                lease = f0e_service.claim(cls.context_a, "f0f-test-base-worker")
                if lease is None:
                    break
                execution = f0e_service.load_execution(cls.context_a, lease)
                evidence = []
                if execution.deferred_document is None:
                    with open_verified_source(
                        vault,
                        execution.vault_object_id,
                        execution.input_object_sha256,
                        execution.input_object_size,
                    ) as source:
                        done = len(execution.native_evidence)
                        for route in execution.ocr_routes:
                            evidence.append(f0e_supervisor.execute_page(source, route))
                            f0e_ocr_calls += 1
                            done += 1
                            f0e_service.heartbeat(
                                cls.context_a, lease, done, len(execution.routes)
                            )
                f0e_service.finalize(
                    cls.context_a,
                    lease,
                    assemble_run_envelope(execution, tuple(evidence)),
                )
        cls.f0e_ocr_calls = f0e_ocr_calls

        first_jobs = cls.service.enqueue_all(cls.context_a, cls.configuration)
        second_jobs = cls.service.enqueue_all(cls.context_a, cls.configuration)
        cls.duplicate_enqueue_stable = first_jobs == second_jobs
        first_lease = cls.service.claim(cls.context_a, "f0f-test-body-worker")
        if first_lease is None:
            raise AssertionError("controlled body job not claimable")
        cls.stale_execution = cls.service.load_execution(cls.context_a, first_lease)
        with psycopg.connect(cls.config.migration_dsn) as connection:
            cls._set_migration_context(connection)
            connection.execute(
                "UPDATE f0d.job SET lease_until=statement_timestamp()-interval '1 second' "
                "WHERE enterprise_id=%s AND id=%s",
                (cls.context_a.enterprise_id, first_lease.job_id),
            )
        recovered = cls.service.claim(cls.context_a, "f0f-test-body-worker")
        if recovered is None or recovered.job_id != first_lease.job_id:
            raise AssertionError("controlled body job not recoverable")
        cls.recovered_generation = recovered.generation
        cls.original_generation = first_lease.generation

        f0f_supervisor = ControlledBodySupervisor(
            body_docker_argv(
                *runtime_paths(), cls.runtime.container_image_id
            ),
            cls.runtime.container_image_id,
            cls.runtime.execution_profile_sha256,
            cls.runtime.base_sandbox_profile,
            cls.runtime.resource_limits,
        )
        cls.completed_executions = []
        cls.ocr_execution = None
        cls.f0f_native_calls = 0
        cls.f0f_ocr_calls = 0
        cls.initial_crosswire_rejected = None
        cls.initial_crosswire_state_stable = None

        class _CrosswireUnexpectedlyAccepted(RuntimeError):
            pass

        def body_state() -> tuple[int, int, str, int]:
            with tenant_transaction(
                cls.config, "f0d_runtime", cls.context_a
            ) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM f0f.page_body_evidence) AS bodies,"
                    "(SELECT count(*) FROM f0d.audit_event WHERE "
                    "event_code='CONTROLLED_BODY_FINALIZED') AS audits,"
                    "status,progress_done FROM f0d.job WHERE id=%s",
                    (active_execution.lease.job_id,),
                ).fetchone()
            return (
                int(row["bodies"]),
                int(row["audits"]),
                str(row["status"]),
                int(row["progress_done"]),
            )

        def probe_initial_crosswire(active_execution: object, bodies: dict) -> None:
            if cls.initial_crosswire_rejected is not None or len(active_execution.pages) < 2:
                return
            before = body_state()
            swapped = list(active_execution.pages)
            swapped[0], swapped[1] = swapped[1], swapped[0]
            raw_values = []
            metadata = []
            for index, (target_page, source_page) in enumerate(
                zip(active_execution.pages, swapped), start=1
            ):
                source_bound = bodies[source_page.page_evidence_id]
                raw = bytearray(source_bound.body.view())
                raw_values.append(raw)
                digest = hashlib.sha256(raw).hexdigest()
                metadata.append(
                    {
                        "body_evidence_id": str(
                            stable_uuid4(
                                "f0f-page-body",
                                target_page.page_evidence_id,
                                digest,
                                active_execution.configuration.configuration_sha256,
                            )
                        ),
                        "page_evidence_id": str(target_page.page_evidence_id),
                        "body_index": index,
                        "plaintext_sha256": digest,
                        "plaintext_size_bytes": len(raw),
                        "ocr_block_byte_lengths": (
                            None
                            if source_bound.ocr_block_byte_lengths is None
                            else list(source_bound.ocr_block_byte_lengths)
                        ),
                    }
                )
            with cls._key() as key:
                key_material = bytearray(key.view())
            try:
                try:
                    with tenant_transaction(
                        cls.config, "f0d_worker", cls.context_a
                    ) as connection:
                        connection.execute(
                            "SELECT f0f.finalize_controlled_body_capture("
                            "%s,%s,%s,%s,%s,%s,%s)",
                            (
                                active_execution.lease.job_id,
                                active_execution.lease.generation,
                                active_execution.lease.token,
                                stable_uuid4(
                                    "f0f-crosswire-probe",
                                    active_execution.lease.job_id,
                                ),
                                Jsonb(metadata),
                                raw_values,
                                key_material,
                            ),
                        )
                        raise _CrosswireUnexpectedlyAccepted()
                except DatabaseError:
                    cls.initial_crosswire_rejected = True
                except _CrosswireUnexpectedlyAccepted:
                    cls.initial_crosswire_rejected = False
            finally:
                key_material[:] = b"\0" * len(key_material)
                key_material.clear()
                for raw in raw_values:
                    raw[:] = b"\0" * len(raw)
                    raw.clear()
            cls.initial_crosswire_state_stable = before == body_state()

        def process(lease: object) -> None:
            nonlocal active_execution
            execution = cls.service.load_execution(cls.context_a, lease)
            active_execution = execution
            bodies = {}
            try:
                with LocalFixtureVault(cls.vault_root) as vault:
                    with open_verified_source(
                        vault,
                        execution.vault_object_id,
                        execution.input_object_sha256,
                        execution.input_object_size,
                    ) as source:
                        for done, page in enumerate(execution.pages, start=1):
                            if page.route.evidence_method == "NATIVE_REFERENCE":
                                body = extract_native_page(source, page.route)
                                bound = bind_native_body(page, body)
                                cls.f0f_native_calls += 1
                            else:
                                result = f0f_supervisor.execute_page(
                                    source, page.route, page.expected_evidence
                                )
                                bound = bind_ocr_body(page, result)
                                cls.f0f_ocr_calls += 1
                                if cls.ocr_execution is None:
                                    cls.ocr_execution = execution
                            bodies[page.page_evidence_id] = bound
                            cls.service.heartbeat(
                                cls.context_a, lease, done, len(execution.pages)
                            )
                probe_initial_crosswire(execution, bodies)
                with cls._key() as key:
                    cls.service.finalize(cls.context_a, execution, bodies, key)
            finally:
                for body in bodies.values():
                    body.wipe()
            cls.completed_executions.append(execution)

        active_execution = cls.stale_execution
        process(recovered)
        while True:
            lease = cls.service.claim(cls.context_a, "f0f-test-body-worker")
            if lease is None:
                break
            process(lease)
        cls.multi_page_execution = next(
            execution
            for execution in cls.completed_executions
            if len(execution.pages) >= 2
        )

    @classmethod
    def _key(cls) -> object:
        from platform_foundation.f0f import load_keyfile

        return load_keyfile(cls.key_path)

    def _body_count(self) -> int:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            return int(
                connection.execute(
                    "SELECT count(*) AS count FROM f0f.page_body_evidence"
                ).fetchone()["count"]
            )

    def _bodies_for(self, execution: object) -> dict[uuid.UUID, object]:
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.f0f.native import extract_native_page
        from platform_foundation.f0f.runtime_config import runtime_paths
        from platform_foundation.f0f.service import bind_native_body, bind_ocr_body
        from platform_foundation.f0f.supervisor import (
            ControlledBodySupervisor,
            body_docker_argv,
        )
        from platform_foundation.vault import LocalFixtureVault

        supervisor = ControlledBodySupervisor(
            body_docker_argv(*runtime_paths(), self.runtime.container_image_id),
            self.runtime.container_image_id,
            self.runtime.execution_profile_sha256,
            self.runtime.base_sandbox_profile,
            self.runtime.resource_limits,
        )
        bodies = {}
        try:
            with LocalFixtureVault(self.vault_root) as vault:
                with open_verified_source(
                    vault,
                    execution.vault_object_id,
                    execution.input_object_sha256,
                    execution.input_object_size,
                ) as source:
                    for page in execution.pages:
                        if page.route.evidence_method == "NATIVE_REFERENCE":
                            body = extract_native_page(source, page.route)
                            bound = bind_native_body(page, body)
                        else:
                            result = supervisor.execute_page(
                                source, page.route, page.expected_evidence
                            )
                            bound = bind_ocr_body(page, result)
                        bodies[page.page_evidence_id] = bound
            return bodies
        except Exception:
            for body in bodies.values():
                body.wipe()
            raise

    def test_migration_revision_is_f0d_0004(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
        self.assertEqual(revision, "f0d_0004")

    def test_pgcrypto_is_isolated_from_public_schema(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            row = connection.execute(
                "SELECT n.nspname,has_schema_privilege('public',n.nspname,'USAGE') "
                "FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace "
                "WHERE e.extname='pgcrypto'"
            ).fetchone()
        self.assertEqual(row, ("f0f_crypto", False))

    def test_all_five_f0f_tables_force_rls(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            count = connection.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE n.nspname='f0f' AND c.relkind='r' "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            ).fetchone()[0]
        self.assertEqual(count, 5)

    def test_public_has_no_f0f_or_crypto_privileges(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT has_schema_privilege('public','f0f','USAGE')::int + "
                "has_schema_privilege('public','f0f_crypto','USAGE')::int + "
                "has_table_privilege('public','f0f.page_body_evidence','SELECT')::int + "
                "has_function_privilege('public','f0f.decrypt_verified_body(uuid,bytea)',"
                "'EXECUTE')::int"
            ).fetchone()[0]
        self.assertEqual(value, 0)

    def test_runtime_without_session_reads_zero_rows(self) -> None:
        with psycopg.connect(self.config.runtime_dsn) as connection:
            count = connection.execute(
                "SELECT count(*) FROM f0f.body_configuration"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_tenant_b_cannot_read_tenant_a_configuration(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            own = connection.execute(
                "SELECT count(*) AS count FROM f0f.body_configuration"
            ).fetchone()["count"]
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_b
        ) as connection:
            foreign = connection.execute(
                "SELECT count(*) AS count FROM f0f.body_configuration"
            ).fetchone()["count"]
        self.assertEqual((own, foreign), (1, 0))

    def test_configuration_is_immutable(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute(
                    "UPDATE f0f.body_configuration SET timeout_seconds=121 WHERE id=%s",
                    (self.configuration.configuration_id,),
                )

    def test_worker_cannot_delete_configuration(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute(
                    "DELETE FROM f0f.body_configuration WHERE id=%s",
                    (self.configuration.configuration_id,),
                )

    def test_worker_cannot_truncate_body_table(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute("TRUNCATE f0f.page_body_evidence")

    def test_roles_have_no_direct_f0f_insert_privilege(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT has_table_privilege('f0d_worker','f0f.page_body_evidence',"
                "'INSERT')::int + has_table_privilege('f0d_runtime',"
                "'f0f.gold_label_evidence','INSERT')::int"
            ).fetchone()[0]
        self.assertEqual(value, 0)

    def test_runtime_and_worker_cannot_execute_gold_mutation_functions(self) -> None:
        signatures = (
            "f0f.record_gold_label(uuid,uuid,bytea,bytea,text,bigint)",
            "f0f.adjudicate_gold_labels(uuid,uuid,uuid,uuid,text,uuid)",
        )
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT has_function_privilege('f0d_runtime',%s,'EXECUTE')::int + "
                "has_function_privilege('f0d_runtime',%s,'EXECUTE')::int + "
                "has_function_privilege('f0d_worker',%s,'EXECUTE')::int + "
                "has_function_privilege('f0d_worker',%s,'EXECUTE')::int",
                signatures * 2,
            ).fetchone()[0]
        self.assertEqual(value, 0)
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_runtime", self.context_a
            ) as connection:
                connection.execute(
                    "SELECT f0f.record_gold_label(%s,%s,%s,%s,%s,%s)",
                    (
                        uuid.uuid4(),
                        uuid.uuid4(),
                        b"X" * 32,
                        b"Y",
                        "UTF8_NFC_LF_V1",
                        1,
                    ),
                )

    def test_first_finalize_cross_page_probe_is_atomic_and_rejected(self) -> None:
        self.assertTrue(self.initial_crosswire_rejected)
        self.assertTrue(self.initial_crosswire_state_stable)

    def test_persistence_has_no_plain_body_key_path_or_image_column(self) -> None:
        forbidden = {"body", "content", "key", "page_image", "path", "raw_text"}
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='f0f'"
            ).fetchall()
        self.assertTrue(forbidden.isdisjoint({row[0] for row in rows}))

    def test_raw_key_bytes_are_not_stored_in_configuration(self) -> None:
        from platform_foundation.f0f import load_keyfile

        with load_keyfile(self.key_path) as key:
            material = bytearray(key.view())
        try:
            with tenant_transaction(
                self.config, "f0d_runtime", self.context_a
            ) as connection:
                count = connection.execute(
                    "SELECT count(*) FROM f0f.body_configuration WHERE "
                    "position(%s::bytea in key_verifier_ciphertext)>0",
                    (material,),
                ).fetchone()["count"]
            self.assertEqual(count, 0)
        finally:
            material[:] = b"\0" * len(material)
            material.clear()

    def test_wrong_key_cannot_reopen_existing_configuration(self) -> None:
        from platform_foundation.f0e.hashing import stable_uuid4
        from platform_foundation.f0f import LocalFixtureKey, create_keyfile, load_keyfile

        wrong_path = "/private/tmp/anhuan-f0f-wrong-" + uuid.uuid4().hex + ".key"
        wrong_key = None
        if _FROZEN_F0_ISOLATION is not None:
            wrong_key = LocalFixtureKey(os.urandom(32))
        else:
            create_keyfile(wrong_path)
        try:
            with (
                wrong_key
                if wrong_key is not None
                else load_keyfile(wrong_path)
            ) as wrong:
                material = bytearray(wrong.view())
            try:
                verifier_id = stable_uuid4(
                    "f0f-key-verifier", self.configuration.configuration_id
                )
                with self.assertRaises(DatabaseError):
                    with tenant_transaction(
                        self.config, "f0d_worker", self.context_a
                    ) as connection:
                        connection.execute(
                            "SELECT f0f.register_body_configuration("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                self.configuration.configuration_id,
                                verifier_id,
                                self.runtime.container_image_id,
                                self.runtime.lock_sha256,
                                self.runtime.execution_profile_sha256,
                                self.runtime.base_container_image_id,
                                self.runtime.base_execution_profile_sha256,
                                "f0f-body-result-v1",
                                material,
                            ),
                        )
            finally:
                material[:] = b"\0" * len(material)
                material.clear()
        finally:
            if _FROZEN_F0_ISOLATION is None:
                os.unlink(wrong_path)
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            count = connection.execute(
                "SELECT count(*) AS count FROM f0f.body_configuration"
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_real_smoke_body_routes_match_f0e_evidence(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0e.page_evidence_selection) AS visual_units,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='NATIVE_REFERENCE') AS native_units,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='LOCAL_OCR') AS ocr_units,"
                "(SELECT count(*) FROM f0f.page_body_evidence) AS bodies,"
                "(SELECT count(*) FROM f0f.page_body_evidence WHERE "
                "selected_route='NATIVE_REFERENCE') AS native_bodies,"
                "(SELECT count(*) FROM f0f.page_body_evidence WHERE "
                "selected_route='LOCAL_OCR') AS ocr_bodies"
            ).fetchone()
        self.assertGreater(int(row["visual_units"]), 0)
        self.assertGreater(int(row["ocr_units"]), 0)
        self.assertEqual(
            (row["visual_units"], row["native_units"], row["ocr_units"]),
            (row["bodies"], row["native_bodies"], row["ocr_bodies"]),
        )
        self.assertEqual(self.f0e_ocr_calls, self.f0f_ocr_calls)

    def test_real_smoke_has_one_body_per_visual_unit(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            row = connection.execute(
                "SELECT count(*) AS bodies,count(DISTINCT processing_unit_id) AS units,"
                "count(DISTINCT page_evidence_id) AS pages "
                "FROM f0f.page_body_evidence"
            ).fetchone()
        self.assertEqual((row["bodies"], row["units"]), (row["units"], row["pages"]))

    def test_duplicate_enqueue_is_idempotent(self) -> None:
        self.assertTrue(self.duplicate_enqueue_stable)
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            row = connection.execute(
                "SELECT count(*) AS jobs,count(DISTINCT processing_plan_id) AS plans "
                "FROM f0d.job WHERE kind='CAPTURE_CONTROLLED_BODY'"
            ).fetchone()
        self.assertEqual(row["jobs"], row["plans"])

    def test_expired_claim_is_recovered_with_new_generation(self) -> None:
        self.assertEqual(self.recovered_generation, self.original_generation + 1)

    def test_completed_queue_has_no_extra_claim(self) -> None:
        self.assertIsNone(self.service.claim(self.context_a, "f0f-test-empty-worker"))

    def test_correct_key_decrypts_and_rechecks_stored_hash(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            row = connection.execute(
                "SELECT id,plaintext_sha256,plaintext_size_bytes "
                "FROM f0f.page_body_evidence ORDER BY processing_unit_id LIMIT 1"
            ).fetchone()
        with self._key() as key:
            body = self.service.decrypt_verified(self.context_a, row["id"], key)
        try:
            self.assertEqual(body.sha256, str(row["plaintext_sha256"]).strip())
            self.assertEqual(body.byte_count, int(row["plaintext_size_bytes"]))
        finally:
            body.wipe()

    def test_wrong_key_decrypts_zero_rows_and_writes_nothing(self) -> None:
        from platform_foundation.f0f import (
            F0FError,
            LocalFixtureKey,
            create_keyfile,
            load_keyfile,
        )

        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            body_id = connection.execute(
                "SELECT id FROM f0f.page_body_evidence "
                "ORDER BY processing_unit_id LIMIT 1"
            ).fetchone()["id"]
        before = self._body_count()
        wrong_path = "/private/tmp/anhuan-f0f-wrong-" + uuid.uuid4().hex + ".key"
        wrong_key = None
        if _FROZEN_F0_ISOLATION is not None:
            wrong_key = LocalFixtureKey(os.urandom(32))
        else:
            create_keyfile(wrong_path)
        try:
            with (
                wrong_key
                if wrong_key is not None
                else load_keyfile(wrong_path)
            ) as wrong, self.assertRaisesRegex(
                F0FError, "BODY_DECRYPTION_FAILED"
            ):
                self.service.decrypt_verified(self.context_a, body_id, wrong)
        finally:
            if _FROZEN_F0_ISOLATION is None:
                os.unlink(wrong_path)
        self.assertEqual(self._body_count(), before)

    def test_tenant_b_cannot_decrypt_tenant_a_body(self) -> None:
        from platform_foundation.f0f import F0FError

        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            body_id = connection.execute(
                "SELECT id FROM f0f.page_body_evidence "
                "ORDER BY processing_unit_id LIMIT 1"
            ).fetchone()["id"]
        with self._key() as key, self.assertRaisesRegex(
            F0FError, "BODY_DECRYPTION_FAILED"
        ):
            self.service.decrypt_verified(self.context_b, body_id, key)

    def test_terminal_finalize_is_exactly_idempotent(self) -> None:
        execution = self.multi_page_execution
        bodies = self._bodies_for(execution)
        with self._key() as key:
            count = self.service.finalize(self.context_a, execution, bodies, key)
        self.assertEqual(count, len(execution.pages))

    def test_stale_lease_finalize_has_zero_writes(self) -> None:
        from platform_foundation.f0f import F0FError

        before = self._body_count()
        bodies = self._bodies_for(self.stale_execution)
        with self._key() as key, self.assertRaisesRegex(
            F0FError, "DATABASE_OPERATION_FAILED"
        ):
            self.service.finalize(
                self.context_a, self.stale_execution, bodies, key
            )
        self.assertEqual(self._body_count(), before)

    def test_cross_page_body_swap_is_rejected_with_zero_writes(self) -> None:
        from platform_foundation.f0f import F0FError

        execution = self.multi_page_execution
        before = self._body_count()
        bodies = self._bodies_for(execution)
        first = execution.pages[0].page_evidence_id
        second = execution.pages[1].page_evidence_id
        bodies[first], bodies[second] = bodies[second], bodies[first]
        try:
            with self._key() as key, self.assertRaisesRegex(
                F0FError, "BODY_EVIDENCE_MISMATCH"
            ):
                self.service.finalize(self.context_a, execution, bodies, key)
        finally:
            for body in bodies.values():
                body.wipe()
        self.assertEqual(self._body_count(), before)

    def test_missing_page_body_is_rejected_before_database_write(self) -> None:
        from platform_foundation.f0f import F0FError

        execution = self.multi_page_execution
        before = self._body_count()
        bodies = self._bodies_for(execution)
        removed = bodies.pop(execution.pages[0].page_evidence_id)
        try:
            with self._key() as key, self.assertRaisesRegex(
                F0FError, "BODY_CONTRACT_INVALID"
            ):
                self.service.finalize(self.context_a, execution, bodies, key)
        finally:
            removed.wipe()
            for body in bodies.values():
                body.wipe()
        self.assertEqual(self._body_count(), before)

    def test_ciphertext_tamper_fails_and_transaction_restores(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            body_id = connection.execute(
                "SELECT id FROM f0f.page_body_evidence "
                "ORDER BY processing_unit_id LIMIT 1"
            ).fetchone()["id"]
        with self._key() as key:
            material = bytearray(key.view())
        failed = False
        connection = psycopg.connect(self.config.migration_dsn)
        try:
            try:
                with connection.transaction():
                    self._set_migration_context(connection)
                    connection.execute(
                        "ALTER TABLE f0f.page_body_evidence DISABLE ROW LEVEL SECURITY"
                    )
                    connection.execute(
                        "ALTER TABLE f0f.page_body_evidence DISABLE TRIGGER "
                        "reject_immutable_row_mutation"
                    )
                    connection.execute(
                        "UPDATE f0f.page_body_evidence SET "
                        "ciphertext=set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%256),"
                        "ciphertext_sha256=encode(f0f_crypto.digest("
                        "set_byte(ciphertext,0,(get_byte(ciphertext,0)+1)%256),"
                        "'sha256'),'hex')::char(64) WHERE id=%s",
                        (body_id,),
                    )
                    connection.execute(
                        "SELECT f0f.decrypt_verified_body(%s,%s)",
                        (body_id, material),
                    )
            except psycopg.Error:
                failed = True
        finally:
            connection.close()
            material[:] = b"\0" * len(material)
            material.clear()
        self.assertTrue(failed)
        with self._key() as key:
            restored = self.service.decrypt_verified(self.context_a, body_id, key)
        restored.wipe()

    def test_body_rows_are_immutable_even_to_migration_owner(self) -> None:
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            body_id = connection.execute(
                "SELECT id FROM f0f.page_body_evidence "
                "ORDER BY processing_unit_id LIMIT 1"
            ).fetchone()["id"]
        with self.assertRaises(psycopg.Error):
            with psycopg.connect(self.config.migration_dsn) as connection:
                self._set_migration_context(connection)
                connection.execute(
                    "UPDATE f0f.page_body_evidence SET terminal_status=terminal_status "
                    "WHERE id=%s",
                    (body_id,),
                )

    def test_gold_interfaces_cannot_false_promote_fixture(self) -> None:
        from platform_foundation.f0f import F0FError

        with self.assertRaisesRegex(F0FError, "GOLD_OPERATION_DENIED"):
            self.service.record_gold_label()
        with self.assertRaisesRegex(F0FError, "GOLD_OPERATION_DENIED"):
            self.service.adjudicate_gold_labels()
        with tenant_transaction(
            self.config, "f0d_runtime", self.context_a
        ) as connection:
            row = connection.execute(
                "SELECT (SELECT count(*) FROM f0f.gold_label_evidence) AS labels,"
                "(SELECT count(*) FROM f0f.gold_adjudication) AS adjudications"
            ).fetchone()
        self.assertEqual((row["labels"], row["adjudications"]), (0, 0))

    def test_changed_vault_object_is_rejected_without_body_output(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.vault import LocalFixtureVault

        with tempfile.TemporaryDirectory(
            prefix="anhuan-f0f-source-change-", dir=_PRIVATE_TMP
        ) as root, LocalFixtureVault(root) as vault:
            stored = vault.store_bytes(b"SYNTHETIC_SOURCE_OBJECT")
            final = Path(root) / "final" / stored.object_id
            final.write_bytes(b"X" * stored.size)
            os.chmod(final, 0o600)
            with self.assertRaisesRegex(F0EError, "SOURCE_OBJECT_INVALID"):
                with open_verified_source(
                    vault, stored.object_id, stored.sha256, stored.size
                ):
                    pass

    def test_real_f0f_timeout_leaves_no_container(self) -> None:
        from platform_foundation.f0f import F0FError
        from platform_foundation.f0f.runtime_config import runtime_paths
        from platform_foundation.f0f.supervisor import (
            ControlledBodySupervisor,
            body_docker_argv,
        )
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.vault import LocalFixtureVault

        execution = self.ocr_execution
        self.assertIsNotNone(execution)
        page = next(
            item
            for item in execution.pages
            if item.route.evidence_method == "LOCAL_OCR"
        )
        limits = dataclasses.replace(self.runtime.resource_limits, timeout_ms=1)
        supervisor = ControlledBodySupervisor(
            body_docker_argv(
                *runtime_paths(), self.runtime.container_image_id
            ),
            self.runtime.container_image_id,
            self.runtime.execution_profile_sha256,
            self.runtime.base_sandbox_profile,
            limits,
        )
        with LocalFixtureVault(self.vault_root) as vault:
            with open_verified_source(
                vault,
                execution.vault_object_id,
                execution.input_object_sha256,
                execution.input_object_size,
            ) as source, self.assertRaisesRegex(
                F0FError, "RUNNER_TIMEOUT|RUNNER_FAILED"
            ):
                supervisor.execute_page(source, page.route, page.expected_evidence)
        result = subprocess.run(
            (
                "/usr/local/bin/docker",
                "ps",
                "-a",
                "--filter",
                _f0f_residual_filter(),
                "--format",
                "{{.ID}}",
            ),
            check=False,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b""))


if __name__ == "__main__":
    unittest.main()
