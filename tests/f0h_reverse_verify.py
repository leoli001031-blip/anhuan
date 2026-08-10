from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Mapping, Sequence


_ROOT = Path(__file__).resolve().parents[1]
_INFRA = _ROOT / "infra/f0h"
_ORDER = (
    "valid_exit",
    "missing_model_exit",
    "tampered_exit",
    "restored_exit",
    "protocol_mismatches",
    "body_leaks",
    "external_calls",
    "runtime_downloads",
    "old_runtime_mutations",
    "container_residuals",
)
_EXPECTED = (0, 2, 2, 0, 0, 0, 0, 0, 0, 0)
_OLD_IMAGE_IDS = (
    "sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a",
    "sha256:7316755e9776033453420b11292ed481b253196dc9db4bbe596a149dcd1a0a64",
)
_OLD_RUNTIME_TARGETS = (
    "infra/f0e",
    "infra/f0f",
    "src/platform_foundation/f0e",
    "src/platform_foundation/f0f",
    "tests/test_f0e_local_ocr.py",
    "tests/f0e_reverse_verify.py",
    "tests/test_f0f_controlled_body_gold.py",
    "tests/f0f_reverse_verify.py",
    "artifacts/f0e-local-ocr/v0.1",
    "artifacts/f0f-controlled-body/v0.1",
)
_F0H_PERSISTENCE_TARGETS = (
    "infra/f0h",
    "src/platform_foundation/f0h",
    "artifacts/f0h-ppocrv6-runtime/v0.1",
    "tests/test_f0h_ppocrv6_runtime.py",
    "tests/f0h_reverse_verify.py",
    "PROGRESS.md",
    "BLOCKED.md",
)
_FORBIDDEN_PROVIDER_MODULES = frozenset(
    {
        "anthropic",
        "boto3",
        "httpx",
        "openai",
        "requests",
        "socket",
        "urllib",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _empty_metrics() -> dict[str, int]:
    return {
        "valid_exit": 2,
        "missing_model_exit": 0,
        "tampered_exit": 0,
        "restored_exit": 2,
        "protocol_mismatches": 1,
        "body_leaks": 1,
        "external_calls": 1,
        "runtime_downloads": 1,
        "old_runtime_mutations": 1,
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


def _iter_target_files(targets: Sequence[str]) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative in targets:
        target = _ROOT / relative
        if target.is_file() or target.is_symlink():
            files.append(target)
            continue
        if not target.is_dir():
            files.append(target)
            continue
        files.extend(
            path
            for path in target.rglob("*")
            if "__pycache__" not in path.parts
            and (path.is_file() or path.is_symlink())
        )
    return tuple(sorted(set(files), key=lambda path: str(path)))


def _tree_fingerprint(targets: Sequence[str]) -> str:
    material: list[dict[str, object]] = []
    for path in _iter_target_files(targets):
        try:
            listed = os.lstat(path)
            relative = str(path.relative_to(_ROOT))
            if not stat.S_ISREG(listed.st_mode) or stat.S_ISLNK(listed.st_mode):
                material.append(
                    {
                        "kind": stat.S_IFMT(listed.st_mode),
                        "path": relative,
                    }
                )
                continue
            digest = hashlib.sha256()
            with path.open("rb", buffering=0) as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            material.append(
                {
                    "mode": stat.S_IMODE(listed.st_mode),
                    "nlink": listed.st_nlink,
                    "path": relative,
                    "sha256": digest.hexdigest(),
                    "size": listed.st_size,
                }
            )
        except OSError:
            material.append({"missing": True, "path": str(path.relative_to(_ROOT))})
    return hashlib.sha256(_canonical(material)).hexdigest()


def _inspect_old_images(docker: str) -> tuple[str, ...] | None:
    try:
        completed = subprocess.run(
            (
                docker,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                *_OLD_IMAGE_IDS,
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
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or completed.stderr:
        return None
    try:
        observed = tuple(
            line.decode("ascii", errors="strict")
            for line in completed.stdout.splitlines()
            if line
        )
    except UnicodeError:
        return None
    if observed != _OLD_IMAGE_IDS:
        return None
    return observed


def _configuration_rejection(operation: object) -> tuple[int, object | None]:
    from platform_foundation.f0h.contracts import F0HError

    try:
        value = operation()  # type: ignore[operator]
    except F0HError as error:
        return (2 if error.code == "RUNNER_CONFIGURATION_INVALID" else 1), None
    except BaseException:
        return 1, None
    return 0, value


def _runtime_result(
    envelope: bytes | bytearray,
    runtime_root: Path,
    *,
    mode: str = "body",
) -> tuple[int, dict[str, object] | None]:
    from platform_foundation.f0h.contracts import F0HError
    from platform_foundation.f0h.supervisor import run_envelope_for_test

    try:
        result = run_envelope_for_test(envelope, mode=mode, root=runtime_root)
    except F0HError:
        return 2, None
    except BaseException:
        return 1, None
    return (0, result) if isinstance(result, dict) else (1, None)


def _protocol_violations(
    result: Mapping[str, object] | None,
    bundle: object,
    *,
    evidence: bool,
) -> int:
    if not isinstance(result, Mapping):
        return 1
    engine = getattr(bundle, "engine_identity", None)
    expected = {
        "accuracy_claimed": False,
        "benchmark_tier": "NONE",
        "document_type": "JPEG",
        "expected_total_pages": 1,
        "external_calls": 0,
        "external_processing": "DENY",
        "fixture_label": "FIXTURE_ONLY",
        "ocr_engine": engine,
        "ocr_executed": True,
        "page_no": 1,
        "profile_sha256": getattr(bundle, "execution_profile_sha256", None),
        "raw_text_emitted": not evidence,
        "raw_text_persisted": False,
        "schema": "f0e-result-v1" if evidence else "f0f-body-result-v1",
        "status": "SUCCESS",
        "temp_residuals": 0,
    }
    violations = sum(int(result.get(key) != value) for key, value in expected.items())
    for key in ("source_sha256", "source_unit_id", "render_sha256"):
        violations += int(_SHA256_RE.fullmatch(str(result.get(key, ""))) is None)
    nonblank = result.get("ocr_nonblank_char_count")
    violations += int(
        isinstance(nonblank, bool) or not isinstance(nonblank, int) or nonblank <= 0
    )
    if evidence:
        violations += int("blocks" in result)
        violations += sum(int(key in result) for key in ("body", "raw_text", "text"))
    else:
        blocks = result.get("blocks")
        violations += int(not isinstance(blocks, list) or not blocks)
        if isinstance(blocks, list):
            violations += int(
                any(
                    not isinstance(block, dict)
                    or block.get("index") != index
                    or not isinstance(block.get("text"), str)
                    for index, block in enumerate(blocks)
                )
            )
    return violations


def _body_needles(result: Mapping[str, object] | None) -> tuple[bytes, ...]:
    if not isinstance(result, Mapping) or not isinstance(result.get("blocks"), list):
        return ()
    texts = [
        block.get("text")
        for block in result["blocks"]  # type: ignore[index]
        if isinstance(block, dict)
        and isinstance(block.get("text"), str)
        and str(block.get("text")).strip()
    ]
    joined = "\x1f".join(str(text) for text in texts).encode("utf-8")
    needles = [joined] if len(joined) >= 8 else []
    needles.extend(
        encoded
        for encoded in (str(text).encode("utf-8") for text in texts)
        if len(encoded) >= 8 and encoded != joined
    )
    return tuple(dict.fromkeys(needles))


def _contains_any(path: Path, needles: tuple[bytes, ...]) -> int:
    if not needles:
        return 1
    overlap = max(len(needle) for needle in needles) - 1
    tail = b""
    try:
        with path.open("rb", buffering=0) as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return 0
                window = tail + chunk
                if any(needle in window for needle in needles):
                    return 1
                tail = window[-overlap:] if overlap else b""
    except OSError:
        return 1


def _persistent_body_leaks(
    needles: tuple[bytes, ...], temporary_root: Path
) -> int:
    files = [
        path
        for path in _iter_target_files(_F0H_PERSISTENCE_TARGETS)
        if path.is_file() and not path.is_symlink()
    ]
    files.extend(
        path
        for path in temporary_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    return sum(_contains_any(path, needles) for path in set(files))


def _provider_import_violations() -> int:
    violations = 0
    files = tuple((_ROOT / "src/platform_foundation/f0h").glob("*.py")) + (
        _INFRA / "runner.py",
    )
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(source)
        except (OSError, UnicodeError, SyntaxError):
            violations += 1
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = (node.module,)
            else:
                continue
            violations += sum(
                name.split(".", 1)[0] in _FORBIDDEN_PROVIDER_MODULES
                for name in names
            )
    return violations


def _reported_external_call_violations(
    results: Sequence[Mapping[str, object] | None],
) -> int:
    violations = 0
    for result in results:
        if not isinstance(result, Mapping):
            continue
        external_calls = result.get("external_calls")
        violations += int(
            isinstance(external_calls, bool)
            or not isinstance(external_calls, int)
            or external_calls != 0
            or result.get("external_processing") != "DENY"
        )
    return violations


def _offline_policy_violations(
    bundle: object,
    runtime_lock: Mapping[str, object],
    component_lock: Mapping[str, object],
    argv: tuple[str, ...],
) -> tuple[int, int]:
    external_calls = _provider_import_violations()
    runtime_downloads = 0
    try:
        external_calls += int(argv[argv.index("--network") + 1] != "none")
        external_calls += int("--mount" in argv or "--volume" in argv)
        external_calls += int(argv[argv.index("--pull") + 1] != "never")
    except (IndexError, ValueError):
        external_calls += 1
        runtime_downloads += 1
    try:
        seccomp = json.loads((_INFRA / "seccomp.json").read_bytes())
        denied = {
            name
            for rule in seccomp["syscalls"]
            if rule["action"] != "SCMP_ACT_ALLOW"
            for name in rule["names"]
        }
        external_calls += int(
            not {"socket", "connect", "accept", "bind"}.issubset(denied)
        )
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        external_calls += 1

    runtime_policy = runtime_lock.get("runtime_policy")
    component_policy = component_lock.get("runtime_policy")
    profile = component_lock.get("profile")
    runtime_downloads += int(getattr(bundle, "runtime_downloads", None) is not False)
    runtime_downloads += int(
        not isinstance(runtime_policy, Mapping)
        or runtime_policy.get("runtime_downloads") is not False
        or runtime_policy.get("network") != "NONE"
    )
    runtime_downloads += int(
        not isinstance(component_policy, Mapping)
        or component_policy.get("runtime_downloads") is not False
        or component_policy.get("network") != "NONE"
    )
    runtime_downloads += int(
        not isinstance(profile, Mapping)
        or profile.get("runtime_downloads") is not False
        or profile.get("network_mode") != "none"
        or profile.get("pull_policy") != "never"
    )
    try:
        dockerfile = (_INFRA / "Dockerfile").read_text(
            encoding="utf-8", errors="strict"
        )
    except (OSError, UnicodeError):
        runtime_downloads += 1
    else:
        lowered = dockerfile.lower()
        runtime_downloads += int("--no-index" not in lowered)
        runtime_downloads += int("--no-deps" not in lowered)
        runtime_downloads += int(
            any(token in lowered for token in ("http://", "https://", "curl", "wget"))
        )
    return external_calls, runtime_downloads


def _container_residuals(docker: str) -> int:
    try:
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
    except (OSError, ValueError, subprocess.SubprocessError):
        return 1
    if completed.returncode != 0 or completed.stderr:
        return 1
    return len(completed.stdout.splitlines())


def _discard_private_result(result: dict[str, object] | None) -> None:
    if not isinstance(result, dict):
        return
    blocks = result.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict):
                block.clear()
        blocks.clear()
    result.clear()


def _evaluate() -> tuple[dict[str, int], tuple[bytes, ...]]:
    from platform_foundation.f0h.runtime_config import (
        load_runtime_bundle,
        runtime_paths,
    )
    from platform_foundation.f0h.supervisor import docker_argv, run_envelope_for_test

    metrics = _empty_metrics()
    old_files_before = _tree_fingerprint(_OLD_RUNTIME_TARGETS)
    bundle = load_runtime_bundle()
    docker, seccomp = runtime_paths()
    old_images_before = _inspect_old_images(docker)
    f0h_before = _tree_fingerprint(_F0H_PERSISTENCE_TARGETS)
    runtime_lock = json.loads((_INFRA / "runtime-lock.json").read_bytes())
    component_lock = json.loads((_INFRA / "component-lock.json").read_bytes())
    argv = docker_argv(docker, seccomp, bundle.container_image_id)
    metrics["external_calls"], metrics["runtime_downloads"] = (
        _offline_policy_violations(bundle, runtime_lock, component_lock, argv)
    )

    probe = runpy.run_path(str(_INFRA / "synthetic_probe.py"))
    envelope = bytearray(probe["_envelope"]("JPEG", False))
    initial: dict[str, object] | None = None
    evidence: dict[str, object] | None = None
    restored: dict[str, object] | None = None
    unexpected_missing: object | None = None
    unexpected_tampered: object | None = None
    needles: tuple[bytes, ...] = ()
    try:
        with tempfile.TemporaryDirectory(
            prefix="f0h-reverse-", dir="/private/tmp"
        ) as temporary:
            temporary_root = Path(temporary) / "f0h"
            shutil.copytree(_INFRA, temporary_root)

            metrics["valid_exit"], initial = _runtime_result(
                envelope, temporary_root
            )
            evidence_exit, evidence = _runtime_result(
                envelope, temporary_root, mode="evidence"
            )
            metrics["valid_exit"] = max(metrics["valid_exit"], evidence_exit)
            metrics["protocol_mismatches"] = _protocol_violations(
                initial, bundle, evidence=False
            ) + _protocol_violations(evidence, bundle, evidence=True)
            needles = _body_needles(initial)
            metrics["protocol_mismatches"] += int(not needles)
            metrics["body_leaks"] = int(
                not isinstance(evidence, dict)
                or "blocks" in evidence
                or evidence.get("raw_text_emitted") is not False
            )

            detector = temporary_root / "models/PP-OCRv6_det_small.onnx"
            detector_backup = temporary_root / "models/.reverse-detector-backup"
            os.replace(detector, detector_backup)
            try:
                (
                    metrics["missing_model_exit"],
                    unexpected_missing,
                ) = _configuration_rejection(
                    lambda: run_envelope_for_test(envelope, root=temporary_root)
                )
            finally:
                os.replace(detector_backup, detector)

            lock_path = temporary_root / "runtime-lock.json"
            original_lock = lock_path.read_bytes()
            tampered_lock = json.loads(original_lock.decode("ascii", errors="strict"))
            tampered_lock["container_image_id"] = "sha256:" + "0" * 64
            tampered_bytes = _canonical(tampered_lock)
            if (
                json.loads(tampered_bytes) != tampered_lock
                or tampered_lock.get("schema") != "f0h-runtime-lock-v1"
                or _IMAGE_RE.fullmatch(str(tampered_lock.get("container_image_id", "")))
                is None
            ):
                metrics["protocol_mismatches"] += 1
            lock_path.write_bytes(tampered_bytes)
            try:
                metrics["tampered_exit"], unexpected_tampered = (
                    _configuration_rejection(
                        lambda: run_envelope_for_test(envelope, root=temporary_root)
                    )
                )
            finally:
                lock_path.write_bytes(original_lock)

            metrics["restored_exit"], restored = _runtime_result(
                envelope, temporary_root
            )
            metrics["protocol_mismatches"] += _protocol_violations(
                restored, bundle, evidence=False
            )
            metrics["external_calls"] += _reported_external_call_violations(
                (initial, evidence, restored)
            )
            if isinstance(initial, dict) and isinstance(restored, dict):
                initial_digest = hashlib.sha256(_canonical(initial)).hexdigest()
                restored_digest = hashlib.sha256(_canonical(restored)).hexdigest()
                metrics["protocol_mismatches"] += int(
                    initial_digest != restored_digest
                )
            else:
                metrics["protocol_mismatches"] += 1

            metrics["body_leaks"] += _persistent_body_leaks(
                needles, temporary_root
            )
            metrics["protocol_mismatches"] += int(
                _tree_fingerprint(_F0H_PERSISTENCE_TARGETS) != f0h_before
            )
    finally:
        envelope[:] = b"\0" * len(envelope)
        envelope.clear()
        _discard_private_result(initial)
        _discard_private_result(evidence)
        _discard_private_result(restored)
        if isinstance(unexpected_missing, dict):
            _discard_private_result(unexpected_missing)
        if isinstance(unexpected_tampered, dict):
            _discard_private_result(unexpected_tampered)

    old_files_after = _tree_fingerprint(_OLD_RUNTIME_TARGETS)
    old_images_after = _inspect_old_images(docker)
    metrics["old_runtime_mutations"] = int(
        old_files_before != old_files_after
        or old_images_before is None
        or old_images_after is None
        or old_images_before != old_images_after
    )
    metrics["container_residuals"] = _container_residuals(docker)
    return metrics, needles


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
        metrics["body_leaks"] += sum(int(needle in captured) for needle in needles)
    else:
        metrics["body_leaks"] += 1
    for name in _ORDER:
        print(f"{name}={metrics[name]}")
    observed = tuple(metrics[name] for name in _ORDER)
    return 0 if observed == _EXPECTED else 2


if __name__ == "__main__":
    raise SystemExit(main())
