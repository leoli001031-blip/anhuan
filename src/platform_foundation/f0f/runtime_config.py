"""Strict F0-F runtime identity loader; no runtime discovery or downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..f0e.contracts import ResourceLimits, SandboxProfile
from ..f0e.hashing import canonical_sha256
from ..f0e.runtime_config import load_runtime_bundle as load_f0e_runtime_bundle
from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0FError


_ROOT = Path(__file__).resolve().parents[3]
_INFRA = _ROOT / "infra/f0f"
_LOCK = _INFRA / "runtime-lock.json"
_COMPONENT_LOCK = _INFRA / "component-lock.json"
_FILES = {
    "component_lock_sha256": _COMPONENT_LOCK,
    "compose_sha256": _INFRA / "compose.yaml",
    "dockerfile_sha256": _INFRA / "Dockerfile",
    "readme_sha256": _INFRA / "README.md",
    "runner_sha256": _INFRA / "runner.py",
    "sbom_sha256": _INFRA / "sbom.spdx.json",
    "seccomp_sha256": _INFRA / "seccomp.json",
    "synthetic_probe_sha256": _INFRA / "synthetic_probe.py",
    "third_party_notices_sha256": _INFRA / "THIRD_PARTY_NOTICES.md",
}
_IMAGE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    lock_sha256: str
    container_image_id: str
    execution_profile_sha256: str
    base_container_image_id: str
    base_runtime_lock_sha256: str
    base_execution_profile_sha256: str
    renderer_binary_sha256: str
    ocr_engine_binary_sha256: str
    language_pack_bundle_sha256: str
    normalization_profile_sha256: str
    timeout_seconds: int = 120
    maximum_private_output_bytes: int = 8 * 1024 * 1024
    maximum_body_bytes: int = 4 * 1024 * 1024

    @property
    def sandbox_profile(self) -> SandboxProfile:
        return SandboxProfile(
            renderer_sha256=self.renderer_binary_sha256,
            ocr_engine_sha256=self.ocr_engine_binary_sha256,
            language_pack_sha256=self.language_pack_bundle_sha256,
            execution_profile_sha256=self.execution_profile_sha256,
            normalization_rule_sha256=self.normalization_profile_sha256,
            container_image_id=self.container_image_id,
        )

    @property
    def base_sandbox_profile(self) -> SandboxProfile:
        return SandboxProfile(
            renderer_sha256=self.renderer_binary_sha256,
            ocr_engine_sha256=self.ocr_engine_binary_sha256,
            language_pack_sha256=self.language_pack_bundle_sha256,
            execution_profile_sha256=self.base_execution_profile_sha256,
            normalization_rule_sha256=self.normalization_profile_sha256,
            container_image_id=self.base_container_image_id,
        )

    @property
    def resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            timeout_ms=self.timeout_seconds * 1000,
            maximum_stdout_bytes=self.maximum_private_output_bytes,
        )


def load_runtime_bundle(
    root: Path | None = None, *, f0e_root: Path | None = None
) -> RuntimeBundle:
    infra, f0e_infra = _infra_roots(root, f0e_root)
    try:
        lock_bytes = _read_owned_regular(infra / "runtime-lock.json")
        lock = json.loads(lock_bytes.decode("ascii", errors="strict"))
        component_bytes = _read_owned_regular(infra / "component-lock.json")
        component = json.loads(component_bytes.decode("ascii", errors="strict"))
        if (
            not isinstance(lock, dict)
            or not isinstance(component, dict)
            or lock.get("schema") != "f0f-runtime-lock-v1"
            or component.get("schema") != "f0f-component-lock-v1"
            or lock.get("candidate_status") != "LOCAL_FIXTURE_CONTROLLED_BODY_ONLY"
            or lock.get("container_image_reference_kind") != "LOCAL_DOCKER_CONTENT_ID"
            or _IMAGE.fullmatch(str(lock.get("container_image_id", ""))) is None
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        platform = lock["platform"]
        protocol = lock["protocol"]
        policy = lock["runtime_policy"]
        base = lock["base"]
        integrity = lock["integrity"]
        profile = component["profile"]
        if (
            platform
            != {
                "architecture": "arm64",
                "operating_system": "linux",
                "python_version": "3.11.9",
            }
            or protocol
            != {
                "input_schema": "f0e-envelope-v1",
                "output_schema": "f0f-body-result-v1",
                "private_ipc_only": True,
                "success_additional_keys": ["blocks"],
                "block_keys": ["bbox", "confidence_ppm", "index", "text"],
                "normalization_rule": "ocr-text-nfc-lf-v1",
                "normalization_rule_sha256": (
                    "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
                ),
            }
            or policy
            != {
                "benchmark_tier": "NONE",
                "external_processing": "DENY",
                "network": "NONE",
                "private_ipc_body": True,
                "production_allowed": False,
                "raw_text_persisted": False,
                "runtime_downloads": False,
                "user_visible_raw_text": False,
            }
            or canonical_sha256(profile) != component.get("profile_sha256")
            or component.get("profile_sha256") != lock.get("profile_sha256")
            or profile.get("max_private_output_bytes") != 8 * 1024 * 1024
            or profile.get("timeout_seconds") != 120
            or profile.get("network_mode") != "none"
            or profile.get("units_per_execution") != 1
            or profile.get("concurrency") != 1
            or profile.get("output_schema") != "f0f-body-result-v1"
            or profile.get("input_envelope_schema") != "f0e-envelope-v1"
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        for key, path in _FILES.items():
            path = infra / path.relative_to(_INFRA)
            if hashlib.sha256(_read_owned_regular(path)).hexdigest() != integrity.get(key):
                raise F0FError("RUNNER_CONFIGURATION_INVALID")

        f0e = load_f0e_runtime_bundle(f0e_infra)
        base_sbom = hashlib.sha256(
            _read_owned_regular(f0e_infra / "sbom.spdx.json")
        ).hexdigest()
        if (
            base.get("container_image_id") != f0e.container_image_id
            or base.get("runtime_lock_sha256") != f0e.lock_sha256
            or base.get("profile_sha256") != f0e.execution_profile_sha256
            or base.get("sbom_sha256") != base_sbom
            or component["base"].get("image_id") != f0e.container_image_id
            or component["base"].get("profile_sha256")
            != f0e.execution_profile_sha256
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        return RuntimeBundle(
            lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
            container_image_id=str(lock["container_image_id"]),
            execution_profile_sha256=str(lock["profile_sha256"]),
            base_container_image_id=f0e.container_image_id,
            base_runtime_lock_sha256=f0e.lock_sha256,
            base_execution_profile_sha256=f0e.execution_profile_sha256,
            renderer_binary_sha256=f0e.renderer_binary_sha256,
            ocr_engine_binary_sha256=f0e.ocr_engine_binary_sha256,
            language_pack_bundle_sha256=f0e.language_pack_bundle_sha256,
            normalization_profile_sha256=f0e.normalization_profile_sha256,
        )
    except F0FError:
        raise
    except Exception:
        raise F0FError("RUNNER_CONFIGURATION_INVALID") from None


def runtime_paths(root: Path | None = None) -> tuple[str, str]:
    infra, _f0e_infra = _infra_roots(root, None)
    docker = "/usr/local/bin/docker"
    raw_seccomp = infra / "seccomp.json"
    _read_owned_regular(raw_seccomp)
    seccomp = str(raw_seccomp.resolve(strict=True))
    if not Path(docker).is_file() or seccomp != str(raw_seccomp):
        raise F0FError("RUNNER_CONFIGURATION_INVALID")
    return docker, seccomp


def _infra_roots(
    root: Path | None, f0e_root: Path | None
) -> tuple[Path, Path]:
    try:
        isolation = load_frozen_f0_isolation()
        if isolation is None:
            expected_f0e = _ROOT / "infra/f0e"
            if (
                (root is not None and Path(root) != _INFRA)
                or (f0e_root is not None and Path(f0e_root) != expected_f0e)
            ):
                raise F0FError("RUNNER_CONFIGURATION_INVALID")
            return _INFRA, expected_f0e
        expected = isolation.f0f_runtime_root
        expected_f0e = isolation.f0e_runtime_root
        value = expected if root is None else Path(root)
        f0e_value = expected_f0e if f0e_root is None else Path(f0e_root)
        if value != expected or f0e_value != expected_f0e:
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        resolved: list[Path] = []
        for candidate in (value, f0e_value):
            listed = candidate.lstat()
            canonical = candidate.resolve(strict=True)
            if (
                canonical != candidate.absolute()
                or stat.S_ISLNK(listed.st_mode)
                or not stat.S_ISDIR(listed.st_mode)
                or listed.st_uid != os.getuid()
                or stat.S_IMODE(listed.st_mode) != 0o700
            ):
                raise F0FError("RUNNER_CONFIGURATION_INVALID")
            resolved.append(canonical)
        return resolved[0], resolved[1]
    except F0FError:
        raise
    except Exception:
        raise F0FError("RUNNER_CONFIGURATION_INVALID") from None


def _read_owned_regular(path: Path) -> bytes:
    descriptor = -1
    output = bytearray()
    try:
        listed = os.lstat(path)
        if stat.S_ISLNK(listed.st_mode):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) not in {0o600, 0o644}
            or before.st_size > 2 * 1024 * 1024
            or (listed.st_dev, listed.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        while len(output) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(output))
            if not chunk:
                break
            output.extend(chunk)
        if len(output) != before.st_size or os.read(descriptor, 1):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        return bytes(output)
    except F0FError:
        raise
    except OSError:
        raise F0FError("RUNNER_CONFIGURATION_INVALID") from None
    finally:
        output[:] = b"\0" * len(output)
        output.clear()
        if descriptor >= 0:
            os.close(descriptor)


__all__ = ("RuntimeBundle", "load_runtime_bundle", "runtime_paths")
