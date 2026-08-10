"""Load and persist the frozen local OCR runtime identity."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..auth import SessionContext
from ..database import DatabaseConfig, tenant_transaction
from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0EError, ResourceLimits, SandboxProfile
from .hashing import canonical_sha256, stable_uuid4
from .service import LocalOcrConfigurationRecord


_ROOT = Path(__file__).resolve().parents[3]
_INFRA = _ROOT / "infra/f0e"
_LOCK = _INFRA / "runtime-lock.json"
_HASHED_FILES = {
    "component_lock_sha256": _INFRA / "component-lock.json",
    "compose_sha256": _INFRA / "compose.yaml",
    "dockerfile_sha256": _INFRA / "Dockerfile",
    "requirements_lock_sha256": _INFRA / "requirements.lock",
    "runner_sha256": _INFRA / "runner.py",
    "sbom_sha256": _INFRA / "sbom.spdx.json",
    "seccomp_sha256": _INFRA / "seccomp.json",
    "third_party_notices_sha256": _INFRA / "THIRD_PARTY_NOTICES.md",
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    lock_sha256: str
    container_image_id: str
    renderer_binary_sha256: str
    ocr_engine_binary_sha256: str
    language_pack_bundle_sha256: str
    normalization_profile_sha256: str
    execution_profile_sha256: str
    timeout_seconds: int

    @property
    def sandbox_profile(self) -> SandboxProfile:
        return SandboxProfile(
            renderer_sha256=self.renderer_binary_sha256,
            ocr_engine_sha256=self.ocr_engine_binary_sha256,
            language_pack_sha256=self.language_pack_bundle_sha256,
            execution_profile_sha256=self.execution_profile_sha256,
            container_image_id=self.container_image_id,
        )

    @property
    def resource_limits(self) -> ResourceLimits:
        return ResourceLimits(timeout_ms=self.timeout_seconds * 1000)


def load_runtime_bundle(root: Path | None = None) -> RuntimeBundle:
    infra = _infra_root(root)
    private_runtime = infra != _INFRA
    try:
        payload = _read_owned_regular(
            infra / "runtime-lock.json", private=private_runtime
        )
        lock = json.loads(payload.decode("ascii", errors="strict"))
        if (
            not isinstance(lock, dict)
            or lock.get("schema") != "f0e-runtime-lock-v1"
            or lock.get("candidate_status") != "LOCAL_FIXTURE_BASELINE_ONLY"
            or lock.get("container_image_reference_kind")
            != "LOCAL_DOCKER_CONTENT_ID"
            or _IMAGE_ID.fullmatch(str(lock.get("container_image_id", "")))
            is None
            or lock.get("runtime_policy")
            != {
                "external_processing": "DENY",
                "network": "NONE",
                "raw_text_emitted": False,
                "raw_text_persisted": False,
                "runtime_downloads": False,
            }
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        platform = lock["platform"]
        renderer = lock["renderer"]
        ocr = lock["ocr"]
        profile = lock["profile"]
        integrity = lock["integrity"]
        if (
            platform.get("architecture") != "arm64"
            or platform.get("operating_system") != "linux"
            or platform.get("python_version") != "3.11.9"
            or renderer.get("component") != "pypdfium2"
            or renderer.get("pypdfium2_version") != "5.12.1"
            or ocr.get("component") != "rapidocr-onnxruntime"
            or ocr.get("rapidocr_version") != "1.4.4"
            or ocr.get("onnxruntime_version") != "1.28.0"
            or ocr.get("normalization_rule") != "ocr-text-nfc-lf-v1"
            or profile.get("render_dpi") != 250
            or profile.get("max_pdf_pages") != 128
            or profile.get("max_pages_per_document_job") != 16
            or profile.get("max_pixels_per_page") != 16_000_000
            or profile.get("manual_review_confidence_floor_ppm") != 0
            or profile.get("concurrency") != 1
            or profile.get("units_per_execution") != 1
            or profile.get("network_mode") != "none"
            or canonical_sha256(profile) != lock.get("profile_sha256")
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        normalization_hash = hashlib.sha256(
            b"ocr-text-nfc-lf-v1"
        ).hexdigest()
        if normalization_hash != ocr.get("normalization_rule_sha256"):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        for key, path in _HASHED_FILES.items():
            path = infra / path.relative_to(_INFRA)
            if (
                hashlib.sha256(
                    _read_owned_regular(path, private=private_runtime)
                ).hexdigest()
                != integrity.get(key)
            ):
                raise F0EError("RUNNER_CONFIGURATION_INVALID")
        return RuntimeBundle(
            lock_sha256=hashlib.sha256(payload).hexdigest(),
            container_image_id=str(lock["container_image_id"]),
            renderer_binary_sha256=str(renderer["libpdfium_sha256"]),
            ocr_engine_binary_sha256=str(ocr["wheel_sha256"]),
            language_pack_bundle_sha256=str(ocr["model_bundle_sha256"]),
            normalization_profile_sha256=normalization_hash,
            execution_profile_sha256=str(lock["profile_sha256"]),
            timeout_seconds=int(profile["timeout_seconds"]),
        )
    except F0EError:
        raise
    except (KeyError, OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise F0EError("RUNNER_CONFIGURATION_INVALID") from None


def register_runtime_configuration(
    config: DatabaseConfig,
    context: SessionContext,
    bundle: RuntimeBundle,
) -> LocalOcrConfigurationRecord:
    if not isinstance(bundle, RuntimeBundle):
        raise F0EError("RUNNER_CONFIGURATION_INVALID")
    configuration_id = stable_uuid4(
        "runtime-configuration", context.enterprise_id, bundle.lock_sha256
    )
    try:
        with tenant_transaction(config, "f0d_worker", context) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO f0e.local_ocr_configuration("
                    "id,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version) VALUES ("
                    "%s,%s,%s,'pypdfium2','5.12.1',%s,"
                    "'rapidocr-onnxruntime','1.4.4',%s,'ch-det,ch-rec,ch-cls',"
                    "%s,%s,%s,%s,%s,%s,'RENDERED_PIXEL_TOP_LEFT_V1') "
                    "ON CONFLICT (id) DO NOTHING",
                    (
                        configuration_id,
                        context.enterprise_id,
                        context.actor_id,
                        bundle.renderer_binary_sha256,
                        bundle.ocr_engine_binary_sha256,
                        bundle.language_pack_bundle_sha256,
                        bundle.normalization_profile_sha256,
                        bundle.execution_profile_sha256,
                        bundle.container_image_id,
                        bundle.lock_sha256,
                        bundle.timeout_seconds,
                    ),
                )
                cursor.execute(
                    "SELECT * FROM f0e.local_ocr_configuration "
                    "WHERE enterprise_id=%s AND id=%s",
                    (context.enterprise_id, configuration_id),
                )
                row = cursor.fetchone()
                if row is None or row["actor_id"] != context.actor_id:
                    raise F0EError("RUNNER_CONFIGURATION_INVALID")
        record = LocalOcrConfigurationRecord(
            configuration_id=row["id"],
            configuration_sha256=str(row["configuration_sha256"]),
            renderer_id=str(row["renderer_id"]),
            renderer_version=str(row["renderer_version"]),
            renderer_binary_sha256=str(row["renderer_binary_sha256"]),
            ocr_engine_id=str(row["ocr_engine_id"]),
            ocr_engine_version=str(row["ocr_engine_version"]),
            ocr_engine_binary_sha256=str(row["ocr_engine_binary_sha256"]),
            language_pack_ids=str(row["language_pack_ids"]),
            language_pack_bundle_sha256=str(row["language_pack_bundle_sha256"]),
            normalization_profile_sha256=str(row["normalization_profile_sha256"]),
            execution_profile_sha256=str(row["execution_profile_sha256"]),
            container_image_id=str(row["container_image_id"]),
            lock_sha256=str(row["lock_sha256"]),
            dpi=int(row["dpi"]),
            max_pdf_pages=int(row["max_pdf_pages"]),
            max_selected_pages_per_run=int(row["max_selected_pages_per_run"]),
            max_pixels_per_page=int(row["max_pixels_per_page"]),
            manual_review_confidence_floor_ppm=int(
                row["manual_review_confidence_floor_ppm"]
            ),
            timeout_seconds=int(row["timeout_seconds"]),
            coordinate_space_version=str(row["coordinate_space_version"]),
        )
        expected_configuration_sha256 = hashlib.sha256(
            "\x1f".join(
                (
                    "pypdfium2",
                    "5.12.1",
                    bundle.renderer_binary_sha256,
                    "rapidocr-onnxruntime",
                    "1.4.4",
                    bundle.ocr_engine_binary_sha256,
                    "ch-det,ch-rec,ch-cls",
                    bundle.language_pack_bundle_sha256,
                    bundle.normalization_profile_sha256,
                    bundle.execution_profile_sha256,
                    bundle.container_image_id,
                    bundle.lock_sha256,
                    "250",
                    "128",
                    "16",
                    "16000000",
                    "0",
                    str(bundle.timeout_seconds),
                    "RENDERED_PIXEL_TOP_LEFT_V1",
                    "DENY",
                    "DENY",
                    "NONE",
                    "false",
                    "false",
                )
            ).encode("utf-8")
        ).hexdigest()
        if (
            record.configuration_id != configuration_id
            or record.configuration_sha256 != expected_configuration_sha256
            or record.renderer_binary_sha256 != bundle.renderer_binary_sha256
            or record.ocr_engine_binary_sha256 != bundle.ocr_engine_binary_sha256
            or record.language_pack_bundle_sha256
            != bundle.language_pack_bundle_sha256
            or record.normalization_profile_sha256
            != bundle.normalization_profile_sha256
            or record.execution_profile_sha256
            != bundle.execution_profile_sha256
            or record.container_image_id != bundle.container_image_id
            or record.lock_sha256 != bundle.lock_sha256
            or record.timeout_seconds != bundle.timeout_seconds
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        return record
    except F0EError:
        raise
    except Exception:
        raise F0EError("DATABASE_OPERATION_FAILED") from None


def runtime_paths(root: Path | None = None) -> tuple[str, str]:
    """Return validated executable inputs for the fixed supervisor only."""

    docker = "/usr/local/bin/docker"
    infra = _infra_root(root)
    seccomp_path = infra / "seccomp.json"
    _read_owned_regular(seccomp_path, private=infra != _INFRA)
    seccomp = str(seccomp_path.resolve(strict=True))
    if (
        not Path(docker).is_file()
        or seccomp_path.is_symlink()
        or seccomp_path.absolute() != Path(seccomp)
    ):
        raise F0EError("RUNNER_CONFIGURATION_INVALID")
    return docker, seccomp


def _infra_root(root: Path | None = None) -> Path:
    try:
        isolation = load_frozen_f0_isolation()
        if isolation is None:
            if root is not None and Path(root) != _INFRA:
                raise F0EError("RUNNER_CONFIGURATION_INVALID")
            return _INFRA
        expected = isolation.f0e_runtime_root
        value = expected if root is None else Path(root)
        if value != expected:
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        listed = value.lstat()
        resolved = value.resolve(strict=True)
        if (
            resolved != value.absolute()
            or stat.S_ISLNK(listed.st_mode)
            or not stat.S_ISDIR(listed.st_mode)
            or listed.st_uid != os.getuid()
            or stat.S_IMODE(listed.st_mode) != 0o700
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        return resolved
    except F0EError:
        raise
    except Exception:
        raise F0EError("RUNNER_CONFIGURATION_INVALID") from None


def _read_owned_regular(path: Path, *, private: bool) -> bytes:
    descriptor = -1
    output = bytearray()
    try:
        listed = os.lstat(path)
        if stat.S_ISLNK(listed.st_mode):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        allowed_modes = {0o600} if private else {0o600, 0o644, 0o755}
        before_identity = (
            int(before.st_dev),
            int(before.st_ino),
            int(before.st_mode),
            int(before.st_uid),
            int(before.st_nlink),
            int(before.st_size),
            int(before.st_mtime_ns),
            int(before.st_ctime_ns),
        )
        listed_identity = (
            int(listed.st_dev),
            int(listed.st_ino),
            int(listed.st_mode),
            int(listed.st_uid),
            int(listed.st_nlink),
            int(listed.st_size),
            int(listed.st_mtime_ns),
            int(listed.st_ctime_ns),
        )
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or not 0 < before.st_size <= 2 * 1024 * 1024
            or listed_identity != before_identity
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        while len(output) < before.st_size:
            chunk = os.read(
                descriptor, min(1024 * 1024, before.st_size - len(output))
            )
            if not chunk:
                break
            output.extend(chunk)
        after = os.fstat(descriptor)
        after_identity = (
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_mode),
            int(after.st_uid),
            int(after.st_nlink),
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_ctime_ns),
        )
        if (
            len(output) != before.st_size
            or os.read(descriptor, 1)
            or after_identity != before_identity
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        return bytes(output)
    except F0EError:
        raise
    except OSError:
        raise F0EError("RUNNER_CONFIGURATION_INVALID") from None
    finally:
        output[:] = b"\0" * len(output)
        output.clear()
        if descriptor >= 0:
            os.close(descriptor)


__all__ = (
    "RuntimeBundle",
    "load_runtime_bundle",
    "register_runtime_configuration",
    "runtime_paths",
)
