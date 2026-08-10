"""Deterministic, fail-closed F1.1.1 v0.3 diagnostic publisher.

The caller-driven publisher never executes acceptance work, so it is
permanently non-completable: serialized evidence can produce only a rejected
diagnostic batch.  A later fixed formal orchestrator must run every gate itself
in one trusted flow before it may use the private promotion path.  This keeps a
hand-written JSON file from becoming an acceptance authority.

Raw command output, command lines, working directories and absolute paths are
not accepted into evidence and therefore cannot reach an artifact.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts/f1-platform-shell/v0.3"

EVIDENCE_SCHEMA = "f1.1.1-machine-evidence-v1"
ACCEPTANCE_SCHEMA = "f1.1.1-acceptance-v2"
READY_CONCLUSION = "F1_1_1_ACCEPTED_FIXTURE_ONLY"
REJECTED_CONCLUSION = "F1_1_1_REJECTED"

REQUIRED_GATES = (
    "migration_replay",
    "targeted_tests",
    "full_repository_tests",
    "npm_ci",
    "npm_lint",
    "npm_build",
    "reverse",
    "clean_rebuild_1",
    "clean_rebuild_2",
    "log_canary",
    "sbom_reconcile",
)

REVERSE_METRICS = (
    "valid_http_e2e",
    "membership_mint",
    "invite_double_consume",
    "stale_lease_commit",
    "duplicate_dispatch",
    "upload_replay_effects",
    "enqueue_recovery",
    "worker_restart",
    "ragflow_recovery",
    "qa_request_races",
    "citation_crosswires",
    "tenant_crosswires",
    "audit_gaps",
    "object_orphans_delta",
    "rq_orphans_delta",
    "index_duplicates",
    "preclean_mutations",
    "new_plaintext_leaks",
    "upstream_mutations",
    "scratch_residuals",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_EVIDENCE_KEYS = {
    "stdout", "stderr", "output", "full", "tail", "command", "cmd",
    "cwd", "path", "paths", "dsn", "token", "key", "secret",
}


class EvidenceError(ValueError):
    """Evidence is malformed or contains material forbidden from artifacts."""


class ImmutableBatchError(RuntimeError):
    """An existing content-addressed batch does not match its manifest."""


@dataclass(frozen=True)
class PublishResult:
    exit_code: int
    batch_id: str
    current_path: Path
    conclusion: str


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _pretty_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha256(path.read_bytes())


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _forbidden_material(value: Any, parent_key: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in _FORBIDDEN_EVIDENCE_KEYS:
                return True
            if _forbidden_material(nested, lowered):
                return True
        return False
    if isinstance(value, list):
        return any(_forbidden_material(item, parent_key) for item in value)
    if isinstance(value, str):
        # Evidence is body-free.  Its permitted strings are schemas, gate IDs,
        # reason codes and hex digests; absolute paths have no legitimate use.
        return value.startswith(("/", "~/")) or "\\Users\\" in value
    return False


def _compose_components(root: Path) -> list[dict[str, Any]]:
    compose = root / "infra/f1/docker-compose.yml"
    text = compose.read_text(encoding="utf-8")
    components: list[dict[str, Any]] = []
    in_services = False
    service: str | None = None
    for line in text.splitlines():
        if line == "services:":
            in_services = True
            continue
        if in_services and line and not line.startswith((" ", "#")):
            break
        if not in_services:
            continue
        service_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if service_match:
            service = service_match.group(1)
            continue
        image_match = re.match(r"^\s{4}image:\s*([^\s#]+)", line)
        if image_match and service:
            image = image_match.group(1)
            properties = [{"name": "oci:reference", "value": image}]
            digest_match = re.search(r"@sha256:([0-9a-f]{64})$", image)
            component: dict[str, Any] = {
                "type": "container",
                "name": service,
                "bom-ref": f"compose:{service}",
                "properties": properties,
            }
            if digest_match:
                component["hashes"] = [
                    {"alg": "SHA-256", "content": digest_match.group(1)}
                ]
            components.append(component)
    if not components:
        raise EvidenceError("COMPOSE_INVENTORY_EMPTY")
    return components


def _dockerfile_components(root: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for label, relative in (
        ("api", "infra/f1/Dockerfile"),
        ("web", "infra/f1/web.Dockerfile"),
    ):
        path = root / relative
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^FROM\s+([^\s]+)", line.strip(), re.I)
            if not match:
                continue
            image = match.group(1)
            display = image.split("@", 1)[0]
            digest_match = re.search(r"@sha256:([0-9a-f]{64})$", image)
            component: dict[str, Any] = {
                "type": "container",
                "name": display,
                "bom-ref": f"dockerfile:{label}:{display}",
                "properties": [{"name": "oci:reference", "value": image}],
            }
            if digest_match:
                component["hashes"] = [
                    {"alg": "SHA-256", "content": digest_match.group(1)}
                ]
            components.append(component)
    if not components:
        raise EvidenceError("DOCKERFILE_INVENTORY_EMPTY")
    return components


def _python_components(root: Path) -> list[dict[str, Any]]:
    lock = root / "requirements/requirements-f1.lock"
    components: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^([A-Za-z0-9_.-]+)==([^\s]+).*--hash=sha256:([0-9a-f]{64})(?:\s|$)"
    )
    meaningful = [
        line.strip()
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for line in meaningful:
        match = pattern.match(line)
        if not match:
            raise EvidenceError("PYTHON_LOCK_ENTRY_INVALID")
        name, version, digest = match.groups()
        normalized = name.lower().replace("_", "-")
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{quote(normalized, safe='-._~')}@{quote(version, safe='-._~')}",
                "bom-ref": f"pkg:pypi/{quote(normalized, safe='-._~')}@{quote(version, safe='-._~')}",
                "hashes": [{"alg": "SHA-256", "content": digest}],
            }
        )
    if len(components) != len(meaningful) or not components:
        raise EvidenceError("PYTHON_LOCK_COVERAGE_MISMATCH")
    return components


def _integrity_hash(integrity: str) -> list[dict[str, str]]:
    if "-" not in integrity:
        return []
    algorithm, encoded = integrity.split("-", 1)
    names = {"sha256": "SHA-256", "sha384": "SHA-384", "sha512": "SHA-512"}
    if algorithm not in names:
        return []
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=True)
    except ValueError:
        return []
    return [{"alg": names[algorithm], "content": raw.hex()}]


def _npm_components(root: Path) -> list[dict[str, Any]]:
    lock_path = root / "src/web/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise EvidenceError("NPM_LOCK_PACKAGES_MISSING")
    components: list[dict[str, Any]] = []
    versioned = 0
    for package_path, metadata in packages.items():
        if not package_path or not isinstance(metadata, dict):
            continue
        version = metadata.get("version")
        if not isinstance(version, str) or not version:
            continue
        versioned += 1
        marker = "node_modules/"
        if marker not in package_path:
            raise EvidenceError("NPM_LOCK_PATH_INVALID")
        name = package_path.rsplit(marker, 1)[1]
        encoded_name = quote(name, safe="/")
        ref = f"pkg:npm/{encoded_name}@{quote(version, safe='-._~')}"
        component: dict[str, Any] = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": ref,
            "bom-ref": ref,
        }
        integrity = metadata.get("integrity")
        if isinstance(integrity, str):
            hashes = _integrity_hash(integrity)
            if hashes:
                component["hashes"] = hashes
        components.append(component)
    if len(components) != versioned or not components:
        raise EvidenceError("NPM_LOCK_COVERAGE_MISMATCH")
    return components


def build_inventory(root: Path) -> list[dict[str, Any]]:
    """Return deterministic CycloneDX components from actual tracked inputs."""
    components = (
        _compose_components(root)
        + _dockerfile_components(root)
        + _python_components(root)
        + _npm_components(root)
    )
    refs = [str(component["bom-ref"]) for component in components]
    if len(refs) != len(set(refs)):
        raise EvidenceError("SBOM_BOM_REF_DUPLICATE")
    return sorted(components, key=lambda component: str(component["bom-ref"]))


def inventory_digest(root: Path) -> str:
    return _sha256(_canonical_bytes(build_inventory(root)))


def _evidence_summary(evidence: Any) -> tuple[dict[str, dict[str, Any]], list[str]]:
    blockers: list[str] = []
    summary: dict[str, dict[str, Any]] = {}
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        blockers.append("EVIDENCE_SCHEMA_INVALID")
        evidence = {}
    if _forbidden_material(evidence):
        blockers.append("EVIDENCE_FORBIDDEN_MATERIAL")
    gates = evidence.get("gates", {}) if isinstance(evidence, dict) else {}
    if not isinstance(gates, dict):
        blockers.append("EVIDENCE_GATES_INVALID")
        gates = {}
    extras = sorted(set(gates) - set(REQUIRED_GATES))
    if extras:
        blockers.append("EVIDENCE_GATE_UNDECLARED")
    for name in REQUIRED_GATES:
        gate = gates.get(name)
        if not isinstance(gate, dict):
            blockers.append(f"GATE_MISSING_{name.upper()}")
            summary[name] = {"exit": -1, "normalized_output_sha256": "INVALID"}
            continue
        exit_code = gate.get("exit")
        digest = gate.get("normalized_output_sha256")
        clean: dict[str, Any] = {
            "exit": exit_code if isinstance(exit_code, int) else -1,
            "normalized_output_sha256": digest if _is_hex64(digest) else "INVALID",
        }
        if not isinstance(exit_code, int) or exit_code != 0:
            blockers.append(f"GATE_EXIT_{name.upper()}")
        if not _is_hex64(digest):
            blockers.append(f"GATE_DIGEST_{name.upper()}")
        if name == "reverse":
            metrics = gate.get("metrics")
            if not isinstance(metrics, dict) or set(metrics) != set(REVERSE_METRICS):
                blockers.append("REVERSE_METRICS_INCOMPLETE")
                clean["metric_count"] = len(metrics) if isinstance(metrics, dict) else 0
                clean["metric_nonzero"] = -1
            else:
                nonzero = sum(
                    1 for metric in REVERSE_METRICS
                    if not isinstance(metrics.get(metric), int) or metrics[metric] != 0
                )
                clean["metric_count"] = len(metrics)
                clean["metric_nonzero"] = nonzero
                if nonzero:
                    blockers.append("REVERSE_METRIC_NONZERO")
        if name.startswith("clean_rebuild_"):
            result_sha = gate.get("result_sha256")
            clean["result_sha256"] = result_sha if _is_hex64(result_sha) else "INVALID"
            if not _is_hex64(result_sha):
                blockers.append(f"CLEAN_REBUILD_DIGEST_{name[-1]}")
        if name == "sbom_reconcile":
            value = gate.get("inventory_sha256")
            clean["inventory_sha256"] = value if _is_hex64(value) else "INVALID"
            if not _is_hex64(value):
                blockers.append("SBOM_EVIDENCE_DIGEST_INVALID")
        summary[name] = clean
    first = summary.get("clean_rebuild_1", {}).get("result_sha256")
    second = summary.get("clean_rebuild_2", {}).get("result_sha256")
    if first == "INVALID" or second == "INVALID" or first != second:
        blockers.append("CLEAN_REBUILD_NONDETERMINISTIC")
    return summary, sorted(set(blockers))


def _acceptance_payload(
    evidence: Any,
    root: Path,
    *,
    formal_orchestrator_executed: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    summary, blockers = _evidence_summary(evidence)
    if not formal_orchestrator_executed:
        blockers.append("FORMAL_ORCHESTRATOR_REQUIRED")
    try:
        components = build_inventory(root)
        actual_inventory = _sha256(_canonical_bytes(components))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        components = []
        actual_inventory = "INVALID"
        blockers.append(type(error).__name__.upper() + "_INVENTORY")
    supplied = summary.get("sbom_reconcile", {}).get("inventory_sha256")
    if supplied != actual_inventory:
        blockers.append("SBOM_INVENTORY_MISMATCH")
    accepted = not blockers
    conclusion = READY_CONCLUSION if accepted else REJECTED_CONCLUSION
    normalized_evidence = {
        "schema": EVIDENCE_SCHEMA,
        "gates": summary,
    }
    payload = {
        "schema": ACCEPTANCE_SCHEMA,
        "conclusion": conclusion,
        "accepted": accepted,
        "blockers": sorted(set(blockers)),
        "evidence_sha256": _sha256(_canonical_bytes(normalized_evidence)),
        "gates": summary,
        "inventory_sha256": actual_inventory,
        "production": False,
        "accuracy_evaluated": False,
        "professional_judgment_required": True,
        "arbitrary_upload_closed": True,
        "malware_scan_closed": True,
    }
    return payload, components, accepted


def _status_html(payload: Mapping[str, Any]) -> bytes:
    blockers = payload.get("blockers", [])
    items = "".join(f"<li>{html.escape(str(item))}</li>" for item in blockers)
    body = (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<title>F1.1.1 acceptance</title></head><body>"
        f"<h1>{html.escape(str(payload['conclusion']))}</h1>"
        f"<p>accepted={str(bool(payload['accepted'])).lower()}</p>"
        f"<p>evidence_sha256={payload['evidence_sha256']}</p>"
        f"<ul>{items}</ul>"
        "<p>FIXTURE_ONLY / NOT_PRODUCTION / ACCURACY_NOT_EVALUATED / "
        "PROFESSIONAL_JUDGMENT_REQUIRED</p>"
        "</body></html>"
    )
    return body.encode("utf-8")


def _sbom(components: list[dict[str, Any]], inventory_sha: str) -> dict[str, Any]:
    document: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "anhuan-f1-fixture",
                "version": "1.1.1",
                "bom-ref": "application:anhuan-f1-fixture:1.1.1",
            },
            "properties": [{"name": "inventory:sha256", "value": inventory_sha}],
        },
        "components": components,
    }
    if _is_hex64(inventory_sha):
        document["serialNumber"] = f"urn:uuid:{uuid.UUID(inventory_sha[:32])}"
    return document


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


def _atomic_current(output_dir: Path, data: bytes) -> Path:
    temporary = output_dir / f".current-{uuid.uuid4().hex}.tmp"
    _write_private(temporary, data)
    current = output_dir / "current.json"
    os.replace(temporary, current)
    current.chmod(0o600)
    return current


def _remove_legacy_top_level(output_dir: Path) -> None:
    """Remove only superseded non-atomic v0.3 snapshots after current commits.

    Older generators wrote three mutable files directly under v0.3.  Leaving
    an old success snapshot beside a rejected current batch would be
    ambiguous.  The exact three known names are retired; batch files are never
    removed here.
    """
    for name in ("acceptance.json", "status.html", "sbom.json"):
        target = output_dir / name
        if target.is_symlink() or target.is_file():
            target.unlink()


def _verify_existing_batch(batch: Path, expected: Mapping[str, str]) -> None:
    if batch.is_symlink() or not batch.is_dir():
        raise ImmutableBatchError("IMMUTABLE_BATCH_TYPE_MISMATCH")
    actual_names = {path.name for path in batch.iterdir() if path.is_file()}
    if actual_names != set(expected):
        raise ImmutableBatchError("IMMUTABLE_BATCH_FILE_SET_MISMATCH")
    for name, digest in expected.items():
        target = batch / name
        if target.is_symlink() or _file_sha(target) != digest:
            raise ImmutableBatchError("IMMUTABLE_BATCH_DIGEST_MISMATCH")


def publish(*, root: Path, evidence_path: Path, output_dir: Path) -> PublishResult:
    """Publish a rejected diagnostic batch from untrusted serialized evidence.

    This public function deliberately has no switch that can grant formal
    authority.  Even a syntactically perfect, all-zero evidence document is
    non-completable.
    """
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        evidence = {"schema": "INVALID", "gates": {}}
    payload, components, accepted = _acceptance_payload(
        evidence,
        root,
        formal_orchestrator_executed=False,
    )
    # A rejected batch must not accidentally transport a success marker from
    # dependency metadata.  Inventory is still bound by digest in acceptance.
    sbom_components = components if accepted else []
    contents: dict[str, bytes] = {
        "acceptance.json": _pretty_bytes(payload),
        "status.html": _status_html(payload),
        "sbom.json": _pretty_bytes(_sbom(sbom_components, payload["inventory_sha256"])),
    }
    if not accepted and any(b"READY" in data for data in contents.values()):
        raise EvidenceError("REJECTED_ARTIFACT_SUCCESS_TOKEN")
    artifact_hashes = {name: _sha256(data) for name, data in sorted(contents.items())}
    manifest = {
        "schema": "f1.1.1-immutable-batch-v1",
        "files": artifact_hashes,
    }
    manifest_bytes = _pretty_bytes(manifest)
    contents["manifest.json"] = manifest_bytes
    batch_id = _sha256(manifest_bytes)
    all_hashes = {name: _sha256(data) for name, data in sorted(contents.items())}

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    staging_root = output_dir / ".staging"
    batches_root = output_dir / "batches"
    staging_root.mkdir(mode=0o700, exist_ok=True)
    batches_root.mkdir(mode=0o700, exist_ok=True)
    staging_root.chmod(0o700)
    batches_root.chmod(0o700)
    batch = batches_root / batch_id
    if batch.exists():
        _verify_existing_batch(batch, all_hashes)
    else:
        stage = Path(tempfile.mkdtemp(prefix="batch-", dir=staging_root))
        stage.chmod(0o700)
        try:
            for name, data in contents.items():
                _write_private(stage / name, data)
            try:
                os.replace(stage, batch)
                batch.chmod(0o700)
            except OSError:
                # A concurrent publisher may have installed this exact
                # content-addressed batch after our existence check.  Never
                # replace it; verify it byte-for-byte and reuse it.
                if not batch.exists():
                    raise
                _verify_existing_batch(batch, all_hashes)
        finally:
            if stage.exists():
                for child in stage.iterdir():
                    if child.is_file():
                        child.unlink()
                stage.rmdir()
    current_payload = {
        "schema": "f1.1.1-current-batch-v1",
        "batch_id": batch_id,
        "conclusion": payload["conclusion"],
        "files": all_hashes,
    }
    current_bytes = _pretty_bytes(current_payload)
    if not accepted and b"READY" in current_bytes:
        raise EvidenceError("REJECTED_CURRENT_SUCCESS_TOKEN")
    current_path = _atomic_current(output_dir, current_bytes)
    _remove_legacy_top_level(output_dir)
    return PublishResult(
        exit_code=0 if accepted else 2,
        batch_id=batch_id,
        current_path=current_path,
        conclusion=str(payload["conclusion"]),
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish F1.1.1 v0.3 machine evidence")
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = publish(
        root=args.root.resolve(),
        evidence_path=args.evidence.resolve(),
        output_dir=args.output.resolve(),
    )
    # The CLI emits only an opaque content ID and conclusion; no command output.
    print(json.dumps({"batch_id": result.batch_id, "conclusion": result.conclusion}, sort_keys=True))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
