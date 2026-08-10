"""Fail-closed F0-H runtime identity loader; no discovery or downloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0HError, canonical_sha256


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_INFRA = _PROJECT_ROOT / "infra/f0h"
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_RUNTIME_LOCK_SHA256 = (
    "8f15cdecec2612639f909faba120fab1c8915be3a00f3e1cab601ea42195d77b"
)


@dataclass(frozen=True, slots=True)
class RuntimeBundle:
    lock_sha256: str
    container_image_id: str
    execution_profile_sha256: str
    configuration_sha256: str
    model_bundle_sha256: str
    rapidocr_version: str
    ocr_family: str
    detector_model: str
    detector_model_sha256: str
    recognizer_model: str
    recognizer_model_sha256: str
    classifier_model: str
    classifier_model_sha256: str
    rapidocr_wheel_sha256: str
    onnxruntime_version: str
    network_mode: str
    runtime_downloads: bool
    production_allowed: bool
    external_processing: str
    benchmark_tier: str
    timeout_seconds: int
    maximum_private_output_bytes: int
    maximum_source_bytes: int
    maximum_pixels: int
    base_image_id: str
    base_profile_sha256: str
    dictionary_sha256: str

    @property
    def engine_identity(self) -> dict[str, object]:
        return {
            "config_sha256": self.configuration_sha256,
            "model_bundle_sha256": self.model_bundle_sha256,
            "model_type": "small",
            "name": "rapidocr",
            "ocr_version": self.ocr_family,
            "onnxruntime_version": self.onnxruntime_version,
            "provider": "ppocrv6-small",
            "runtime_profile_sha256": self.execution_profile_sha256,
            "version": self.rapidocr_version,
        }


def load_runtime_bundle(root: Path | None = None) -> RuntimeBundle:
    infra = _infra_root(root)
    try:
        lock_bytes = _read_owned_regular(infra / "runtime-lock.json", 2 * 1024 * 1024)
        component_bytes = _read_owned_regular(
            infra / "component-lock.json", 2 * 1024 * 1024
        )
        lock = json.loads(lock_bytes.decode("ascii", errors="strict"))
        component = json.loads(component_bytes.decode("ascii", errors="strict"))
        if (
            hashlib.sha256(lock_bytes).hexdigest() != _EXPECTED_RUNTIME_LOCK_SHA256
            or not isinstance(lock, dict)
            or not isinstance(component, dict)
            or lock.get("schema") != "f0h-runtime-lock-v1"
            or component.get("schema") != "f0h-component-lock-v1"
            or lock.get("candidate_status") != "LOCAL_PPOCRV6_RUNTIME_READY"
            or component.get("candidate_status") != "LOCAL_PPOCRV6_RUNTIME_ONLY"
            or lock.get("container_image_reference_kind")
            != "LOCAL_DOCKER_CONTENT_ID"
            or _IMAGE_RE.fullmatch(str(lock.get("container_image_id", ""))) is None
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        if (
            lock.get("profile_sha256") != component.get("profile_sha256")
            or canonical_sha256(component.get("profile"))
            != component.get("profile_sha256")
            or lock.get("configuration_sha256")
            != component.get("configuration_sha256")
            or lock.get("model_bundle_sha256")
            != component.get("model_bundle_sha256")
            or lock.get("provider") != component.get("provider")
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")

        integrity = lock.get("integrity")
        if not isinstance(integrity, dict):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        if integrity.get("component-lock.json") != hashlib.sha256(
            component_bytes
        ).hexdigest():
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        for relative, expected in integrity.items():
            if relative == "component-lock.json":
                continue
            if (
                not isinstance(relative, str)
                or relative.startswith("/")
                or ".." in Path(relative).parts
                or _HASH_RE.fullmatch(str(expected)) is None
            ):
                raise F0HError("RUNNER_CONFIGURATION_INVALID")
            actual = hashlib.sha256(
                _read_owned_regular(infra / relative, 80 * 1024 * 1024)
            ).hexdigest()
            if actual != expected:
                raise F0HError("RUNNER_CONFIGURATION_INVALID")

        models = component.get("models")
        if not isinstance(models, list) or len(models) != 3:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        model_by_role = {
            str(item.get("role")): item for item in models if isinstance(item, dict)
        }
        if set(model_by_role) != {
            "detector",
            "recognizer",
            "orientation_classifier",
        }:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        for item in model_by_role.values():
            filename = str(item.get("filename", ""))
            expected = str(item.get("sha256", ""))
            if (
                Path(filename).name != filename
                or _HASH_RE.fullmatch(expected) is None
                or hashlib.sha256(
                    _read_owned_regular(infra / "models" / filename, 40 * 1024 * 1024)
                ).hexdigest()
                != expected
            ):
                raise F0HError("RUNNER_CONFIGURATION_INVALID")

        runtime_dependencies = component.get("dependencies", {}).get("runtime")
        if not isinstance(runtime_dependencies, list) or len(runtime_dependencies) != 9:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        rapidocr_wheel_sha256 = ""
        for item in runtime_dependencies:
            if not isinstance(item, dict):
                raise F0HError("RUNNER_CONFIGURATION_INVALID")
            filename = str(item.get("filename", ""))
            expected = str(item.get("sha256", ""))
            if (
                Path(filename).name != filename
                or _HASH_RE.fullmatch(expected) is None
                or hashlib.sha256(
                    _read_owned_regular(infra / "wheels" / filename, 40 * 1024 * 1024)
                ).hexdigest()
                != expected
            ):
                raise F0HError("RUNNER_CONFIGURATION_INVALID")
            if item.get("name") == "rapidocr" and item.get("version") == "3.9.2":
                rapidocr_wheel_sha256 = expected
        if not rapidocr_wheel_sha256:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")

        provider = component["provider"]
        policy = component["runtime_policy"]
        profile = component["profile"]
        dictionary = component["dictionary"]
        if (
            provider
            != {
                "provider_id": "ppocrv6-small",
                "engine_distribution": "rapidocr",
                "engine_version": "3.9.2",
                "engine": "onnxruntime",
                "ocr_family": "PP-OCRv6",
                "detector_model_type": "small",
                "recognizer_model_type": "small",
                "orientation_classifier_family": "PP-OCRv2-compatible-mobile",
                "orientation_classifier_is_v6": False,
            }
            or policy.get("accuracy_status") != "NOT_EVALUATED"
            or policy.get("benchmark_tier") != "NONE"
            or policy.get("external_processing") != "DENY"
            or policy.get("network") != "NONE"
            or policy.get("runtime_downloads") is not False
            or policy.get("production_allowed") is not False
            or profile.get("network_mode") != "none"
            or profile.get("runtime_downloads") is not False
            or profile.get("units_per_execution") != 1
            or profile.get("concurrency") != 1
            or profile.get("timeout_seconds") != 120
            or profile.get("max_private_output_bytes") != 8 * 1024 * 1024
            or profile.get("max_source_bytes") != 64 * 1024 * 1024
            or profile.get("max_pixels_per_page") != 16_000_000
            or dictionary.get("kind") != "EMBEDDED_ONNX_METADATA"
            or dictionary.get("character_lines") != 18708
            or _HASH_RE.fullmatch(str(dictionary.get("utf8_sha256", ""))) is None
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        if lock.get("runtime_policy") != policy:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        detector = model_by_role["detector"]
        recognizer = model_by_role["recognizer"]
        classifier = model_by_role["orientation_classifier"]
        return RuntimeBundle(
            lock_sha256=hashlib.sha256(lock_bytes).hexdigest(),
            container_image_id=str(lock["container_image_id"]),
            execution_profile_sha256=str(component["profile_sha256"]),
            configuration_sha256=str(component["configuration_sha256"]),
            model_bundle_sha256=str(component["model_bundle_sha256"]),
            rapidocr_version="3.9.2",
            ocr_family="PP-OCRv6",
            detector_model=str(detector["filename"]),
            detector_model_sha256=str(detector["sha256"]),
            recognizer_model=str(recognizer["filename"]),
            recognizer_model_sha256=str(recognizer["sha256"]),
            classifier_model=str(classifier["filename"]),
            classifier_model_sha256=str(classifier["sha256"]),
            rapidocr_wheel_sha256=rapidocr_wheel_sha256,
            onnxruntime_version=str(component["base"]["onnxruntime_version"]),
            network_mode="none",
            runtime_downloads=False,
            production_allowed=False,
            external_processing="DENY",
            benchmark_tier="NONE",
            timeout_seconds=int(profile["timeout_seconds"]),
            maximum_private_output_bytes=int(profile["max_private_output_bytes"]),
            maximum_source_bytes=int(profile["max_source_bytes"]),
            maximum_pixels=int(profile["max_pixels_per_page"]),
            base_image_id=str(component["base"]["image_id"]),
            base_profile_sha256=str(component["base"]["profile_sha256"]),
            dictionary_sha256=str(dictionary["utf8_sha256"]),
        )
    except F0HError:
        raise
    except (KeyError, OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        raise F0HError("RUNNER_CONFIGURATION_INVALID") from None


def runtime_paths(root: Path | None = None) -> tuple[str, str]:
    infra = _infra_root(root)
    docker = "/usr/local/bin/docker"
    seccomp_path = infra / "seccomp.json"
    try:
        _read_owned_regular(seccomp_path, 256 * 1024)
        resolved = seccomp_path.resolve(strict=True)
        if (
            not Path(docker).is_file()
            or seccomp_path.is_symlink()
            or resolved != seccomp_path.absolute()
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        return docker, str(resolved)
    except F0HError:
        raise
    except (OSError, RuntimeError):
        raise F0HError("RUNNER_CONFIGURATION_INVALID") from None


def _infra_root(root: Path | None) -> Path:
    try:
        isolation = load_frozen_f0_isolation()
        expected = _DEFAULT_INFRA if isolation is None else isolation.f0h_runtime_root
        value = expected if root is None else Path(root)
        if isolation is not None and value != expected:
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        if value.is_symlink() or not value.is_dir():
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        listed = value.lstat()
        resolved = value.resolve(strict=True)
        if (
            resolved != value.absolute()
            or isolation is not None
            and (
                listed.st_uid != os.getuid()
                or stat.S_IMODE(listed.st_mode) != 0o700
            )
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        return resolved
    except F0HError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise F0HError("RUNNER_CONFIGURATION_INVALID") from None


def _read_owned_regular(path: Path, limit: int) -> bytes:
    descriptor = -1
    output = bytearray()
    try:
        listed = os.lstat(path)
        if stat.S_ISLNK(listed.st_mode):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
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
            or stat.S_IMODE(before.st_mode) not in {0o600, 0o644, 0o755}
            or not 0 <= before.st_size <= limit
            or (listed.st_dev, listed.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        while len(output) < before.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, before.st_size - len(output)))
            if not chunk:
                break
            output.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(output) != before.st_size
            or os.read(descriptor, 1)
            or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        return bytes(output)
    except F0HError:
        raise
    except OSError:
        raise F0HError("RUNNER_CONFIGURATION_INVALID") from None
    finally:
        output[:] = b"\0" * len(output)
        output.clear()
        if descriptor >= 0:
            os.close(descriptor)


__all__ = ("RuntimeBundle", "load_runtime_bundle", "runtime_paths")
