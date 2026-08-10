"""Body-free diagnostic recorder for F1.1.1 acceptance gates.

Each invocation runs exactly one caller-selected, taskbook-authorized command.
Only its exit code, a normalized-output SHA-256, and explicitly parsed numeric
metrics are persisted.  Command text, stdout/stderr, paths, timings and run IDs
never enter the evidence bundle.

Example::

    python -B infra/f1/repro_verify.py gate \
      --evidence /private/tmp/f111-evidence/evidence.json \
      --name targeted_tests -- python -B -m unittest tests.test_f111_repair_security

Run every name listed by ``list`` to diagnose a development run.  Serialized
output from this caller-selected interface is never formal acceptance
authority: :mod:`infra.f1.artifacts_v03` always publishes it as rejected, even
when every recorded value is zero.  Only the fixed formal orchestrator may
eventually promote a completed run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from infra.f1.artifacts_v03 import (
        EVIDENCE_SCHEMA,
        REQUIRED_GATES,
        REVERSE_METRICS,
        inventory_digest,
    )
except ModuleNotFoundError:  # direct ``python infra/f1/repro_verify.py``
    from artifacts_v03 import (  # type: ignore[no-redef]
        EVIDENCE_SCHEMA,
        REQUIRED_GATES,
        REVERSE_METRICS,
        inventory_digest,
    )


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_ISO_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b")
_DURATION = re.compile(r"\b(?:elapsed[=: ]*)?\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b", re.I)
_TMP_PATH = re.compile(r"/(?:private/)?(?:tmp|var/folders)/[^\s'\"]+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReverseEvidenceError(ValueError):
    pass


def normalize_output(output: str, root: Path) -> str:
    """Remove nondeterministic/path material before hashing, never persisting it."""
    normalized = output.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _ANSI.sub("", normalized)
    root_text = str(root.resolve())
    if root_text:
        normalized = normalized.replace(root_text, "<ROOT>")
    normalized = _TMP_PATH.sub("<SCRATCH>", normalized)
    normalized = _UUID.sub("<UUID>", normalized)
    normalized = _ISO_TIME.sub("<TIME>", normalized)
    normalized = _DURATION.sub("<DURATION>", normalized)
    # Whitespace emitted by progress renderers is not evidence.
    lines = [" ".join(line.split()) for line in normalized.splitlines() if line.strip()]
    return "\n".join(lines) + ("\n" if lines else "")


def normalized_digest(output: str, root: Path) -> str:
    return hashlib.sha256(normalize_output(output, root).encode("utf-8")).hexdigest()


def parse_reverse_metrics(output: str) -> dict[str, int]:
    """Accept exactly one complete 20-metric line and reject extras/duplicates."""
    candidates: list[dict[str, int]] = []
    pair_pattern = re.compile(r"\b([a-z][a-z0-9_]*)=(-?\d+)\b")
    for line in output.splitlines():
        pairs = pair_pattern.findall(line)
        if not pairs:
            continue
        keys = [key for key, _value in pairs]
        if set(keys) & set(REVERSE_METRICS):
            if len(keys) != len(set(keys)):
                raise ReverseEvidenceError("REVERSE_METRIC_DUPLICATE")
            parsed = {key: int(value) for key, value in pairs}
            if set(parsed) != set(REVERSE_METRICS):
                raise ReverseEvidenceError("REVERSE_METRIC_SET_MISMATCH")
            candidates.append(parsed)
    if len(candidates) != 1:
        raise ReverseEvidenceError("REVERSE_METRIC_LINE_COUNT")
    return candidates[0]


def _machine_result_digest(output: str, fallback: str) -> str:
    matches = re.findall(r"\bCLEAN_REBUILD_RESULT_SHA256=([0-9a-f]{64})\b", output)
    if len(matches) > 1 or (matches and not _HEX64.fullmatch(matches[0])):
        return "INVALID"
    return matches[0] if matches else fallback


def _read_bundle(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": EVIDENCE_SCHEMA, "gates": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("EVIDENCE_BUNDLE_INVALID") from error
    if not isinstance(data, dict) or data.get("schema") != EVIDENCE_SCHEMA:
        raise ValueError("EVIDENCE_BUNDLE_SCHEMA")
    gates = data.get("gates")
    if not isinstance(gates, dict) or set(gates) - set(REQUIRED_GATES):
        raise ValueError("EVIDENCE_BUNDLE_GATES")
    # Copy only the schema's body-free fields.  An edited bundle containing a
    # raw-output key is rejected rather than carried forward.
    allowed = {
        "exit", "normalized_output_sha256", "metrics", "result_sha256",
        "inventory_sha256",
    }
    clean: dict[str, Any] = {"schema": EVIDENCE_SCHEMA, "gates": {}}
    for name, gate in gates.items():
        if not isinstance(gate, dict) or set(gate) - allowed:
            raise ValueError("EVIDENCE_BUNDLE_FORBIDDEN_FIELD")
        clean["gates"][name] = dict(gate)
    return clean


def _write_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    content = (json.dumps(bundle, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    temporary = path.parent / f".evidence-{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def record_gate(
    *,
    evidence_path: Path,
    name: str,
    command: Sequence[str],
    root: Path,
) -> dict[str, Any]:
    """Run one gate and atomically add its body-free record to the bundle."""
    if name not in REQUIRED_GATES:
        raise ValueError("GATE_NAME_UNDECLARED")
    if name != "sbom_reconcile" and not command:
        raise ValueError("GATE_COMMAND_MISSING")
    if command:
        completed = subprocess.run(
            list(command),
            cwd=str(root),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        combined = completed.stdout + completed.stderr
        exit_code = completed.returncode
    else:
        combined = "SBOM_RECONCILE_LOCAL_INVENTORY\n"
        exit_code = 0
    digest = normalized_digest(combined, root)
    gate: dict[str, Any] = {
        "exit": int(exit_code),
        "normalized_output_sha256": digest,
    }
    if name == "reverse":
        try:
            gate["metrics"] = parse_reverse_metrics(combined)
        except ReverseEvidenceError:
            gate["metrics"] = {}
            gate["exit"] = 2 if exit_code == 0 else int(exit_code)
    if name.startswith("clean_rebuild_"):
        gate["result_sha256"] = _machine_result_digest(combined, digest)
    if name == "sbom_reconcile":
        try:
            gate["inventory_sha256"] = inventory_digest(root)
        except (OSError, ValueError, json.JSONDecodeError):
            gate["inventory_sha256"] = "INVALID"
            gate["exit"] = 2 if exit_code == 0 else int(exit_code)
    bundle = _read_bundle(evidence_path)
    bundle["gates"][name] = gate
    _write_bundle(evidence_path, bundle)
    return gate


def _gate_cli(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    gate = record_gate(
        evidence_path=args.evidence.resolve(),
        name=args.name,
        command=command,
        root=args.root.resolve(),
    )
    print(
        json.dumps(
            {
                "gate": args.name,
                "exit": gate["exit"],
                "normalized_output_sha256": gate["normalized_output_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if gate["exit"] == 0 else 2


def _check_cli(args: argparse.Namespace) -> int:
    bundle = _read_bundle(args.evidence.resolve())
    missing = sorted(set(REQUIRED_GATES) - set(bundle["gates"]))
    nonzero = sorted(
        name for name, gate in bundle["gates"].items()
        if not isinstance(gate.get("exit"), int) or gate["exit"] != 0
    )
    result = {"gate_count": len(bundle["gates"]), "missing_count": len(missing), "nonzero_count": len(nonzero)}
    print(json.dumps(result, sort_keys=True))
    return 0 if not missing and not nonzero else 2


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record body-free F1.1.1 gate evidence")
    subparsers = parser.add_subparsers(dest="action", required=True)

    listing = subparsers.add_parser("list")
    listing.set_defaults(handler=lambda _args: (print("\n".join(REQUIRED_GATES)) or 0))

    gate = subparsers.add_parser("gate")
    gate.add_argument("--evidence", required=True, type=Path)
    gate.add_argument("--root", type=Path, default=Path.cwd())
    gate.add_argument("--name", required=True, choices=REQUIRED_GATES)
    gate.add_argument("command", nargs=argparse.REMAINDER)
    gate.set_defaults(handler=_gate_cli)

    check = subparsers.add_parser("check")
    check.add_argument("--evidence", required=True, type=Path)
    check.set_defaults(handler=_check_cli)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
