"""Repo-relative entrypoint for the dedicated A-Eco local candidate.

Lifecycle ownership stays in ``scripts/localctl``.  The analysis-report
migrator remains ``infra/f1/analysis-reports/migrate.py`` and must finish at
``f1_0023``.  This wrapper adds the missing HTTP readiness gate: a running
container inventory is not accepted as readiness without the exact
``/api/readyz`` response.

This module does not deploy or contact a remote target.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALCTL = REPO_ROOT / "scripts/localctl"
MIGRATOR = REPO_ROOT / "infra/f1/analysis-reports/migrate.py"
EXPECTED_COMPONENTS = {
    "clamd": True,
    "database": True,
    "minio": True,
    "oidc": True,
    "redis": True,
}
EXPECTED_STATUS = {
    "ark_calls": 0,
    "client_login_ready": 1,
    "f1_head": "f1_0023",
    "generator": "evidence_local",
    "mock_data": 0,
    "provider_login_ready": 1,
    "ready": 1,
    "shared_match": 1,
    "workflow_seeded": 1,
}


class CandidateError(RuntimeError):
    pass


def _runtime_python() -> str:
    value = os.environ.get("A_ECO_PYTHON", "").strip()
    return value or sys.executable


def _runtime_environment() -> dict[str, str]:
    environment = dict(os.environ)
    repo_pythonpath = os.pathsep.join((str(REPO_ROOT / "src"), str(REPO_ROOT)))
    existing = environment.get("PYTHONPATH", "").strip()
    environment["PYTHONPATH"] = (
        os.pathsep.join((repo_pythonpath, existing)) if existing else repo_pythonpath
    )
    return environment


def _run_localctl(action: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [
            _runtime_python(),
            "-B",
            str(LOCALCTL),
            f"analysis-report-demo-{action}",
        ],
        cwd=REPO_ROOT,
        env=_runtime_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=1800 if action == "start" else 180,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        token = detail[-1] if detail else "LOCAL_ANALYSIS_REPORT_LOCALCTL_FAILED"
        raise CandidateError(token)
    if result.stderr:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_LOCALCTL_STDERR")
    return result


def _parse_start(stdout: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in rows:
            raise CandidateError("LOCAL_ANALYSIS_REPORT_START_OUTPUT_INVALID")
        rows[key] = value
    if set(rows) != {"url", "provider_username", "client_username"}:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_START_OUTPUT_INVALID")
    if rows["provider_username"] != "tenant-a" or rows["client_username"] != "invitee":
        raise CandidateError("LOCAL_ANALYSIS_REPORT_START_OUTPUT_INVALID")
    _ready_url(rows["url"])
    return rows


def _parse_status(stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_STATUS_OUTPUT_INVALID") from error
    integer_keys = {
        "ark_calls",
        "client_login_ready",
        "mock_data",
        "provider_login_ready",
        "ready",
        "shared_match",
        "workflow_seeded",
    }
    if (
        payload != EXPECTED_STATUS
        or not isinstance(payload, dict)
        or any(type(payload.get(key)) is not int for key in integer_keys)
        or type(payload.get("f1_head")) is not str
        or type(payload.get("generator")) is not str
    ):
        raise CandidateError("LOCAL_ANALYSIS_REPORT_STATUS_OUTPUT_INVALID")
    return payload


def _ready_url(origin: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise CandidateError("LOCAL_ANALYSIS_REPORT_ORIGIN_INVALID")
    try:
        port = parsed.port
    except ValueError as error:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_ORIGIN_INVALID") from error
    if port is None or not 1 <= port <= 65535:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_ORIGIN_INVALID")
    return f"http://127.0.0.1:{port}/api/readyz"


def _probe_readiness(origin: str) -> None:
    request = urllib.request.Request(_ready_url(origin), method="GET")
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except (OSError, urllib.error.URLError) as error:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_API_NOT_READY") from error
    try:
        body = response.read(4097)
        status = int(response.status)
        cache_control = response.headers.get("Cache-Control", "")
    finally:
        response.close()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise CandidateError("LOCAL_ANALYSIS_REPORT_API_READINESS_INVALID") from error
    if (
        status != 200
        or len(body) > 4096
        or "no-store" not in cache_control.lower()
        or payload != {"status": "ready", "components": EXPECTED_COMPONENTS}
    ):
        raise CandidateError("LOCAL_ANALYSIS_REPORT_API_READINESS_INVALID")


def _start() -> None:
    started = False
    try:
        start_result = _run_localctl("start")
        started = True
        start_payload = _parse_start(start_result.stdout)
        status_payload = _parse_status(_run_localctl("status").stdout)
        _probe_readiness(start_payload["url"])
    except Exception:
        if started:
            try:
                _run_localctl("stop")
            except (CandidateError, OSError, subprocess.SubprocessError):
                pass
        raise
    for key in ("url", "provider_username", "client_username"):
        print(f"{key}={start_payload[key]}")
    print(json.dumps(status_payload, sort_keys=True, separators=(",", ":")))
    print("LOCAL_ANALYSIS_REPORT_CANDIDATE_READY")


def _check(origin: str) -> None:
    payload = _parse_status(_run_localctl("status").stdout)
    _probe_readiness(origin)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print("LOCAL_ANALYSIS_REPORT_CANDIDATE_READY")


def _stop() -> None:
    _run_localctl("stop")
    print("LOCAL_ANALYSIS_REPORT_CANDIDATE_STOPPED")


def _migrate() -> None:
    result = subprocess.run(
        [_runtime_python(), "-B", str(MIGRATOR)],
        cwd=REPO_ROOT,
        env=_runtime_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        token = detail[-1] if detail else "LOCAL_ANALYSIS_REPORT_MIGRATION_FAILED"
        raise CandidateError(token)
    if result.stderr or result.stdout.strip() != "LOCAL_ANALYSIS_REPORT_MIGRATE_OK":
        raise CandidateError("LOCAL_ANALYSIS_REPORT_MIGRATION_OUTPUT_INVALID")
    print(result.stdout.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Operate the repo-relative, non-production A-Eco local candidate."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start")
    check = commands.add_parser("check")
    check.add_argument("--origin", required=True)
    commands.add_parser("stop")
    commands.add_parser("migrate")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "start":
            _start()
        elif arguments.command == "check":
            _check(arguments.origin)
        elif arguments.command == "stop":
            _stop()
        else:
            _migrate()
    except (CandidateError, OSError, subprocess.SubprocessError) as error:
        print(str(error) or "LOCAL_ANALYSIS_REPORT_CANDIDATE_FAILED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
