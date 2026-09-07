#!/usr/bin/env python3
"""Unified acceptance gate for the A-Eco closeout execution.

Runs the frozen set of test suites in selectable modes and produces a
structured report with source SHA, suite list, and execution counts.

Modes:
  offline     — Pure offline unit/contract tests (no DB, no browser, no model)
  integration — Tests requiring a real PostgreSQL instance
  browser     — Browser workflow checks (requires Chrome + running stack)
  restore     — Backup/restore/fault-injection validation (requires isolation)
  quality     — OCR/report quality sample evaluation (fixtures + evaluator)

Usage:
  python scripts/acceptance_gate.py --mode offline
  python scripts/acceptance_gate.py --mode offline --evidence-dir out/acceptance

Exit codes:
  0 = all selected suites passed
  1 = one or more suites failed
  2 = environment not available for the selected mode (NOT_TESTED)

The gate reuses the project's existing test runners (unittest, npm);
it does not duplicate verification logic.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- Frozen suite definitions (T0 baseline) ---

OFFLINE_SUITES = [
    "tests.test_material_cloud_ocr",
    "tests.test_analysis_report_llm_generator",
    "tests.test_analysis_report_pdf_artifact",
    "tests.test_analysis_reports",
    "tests.test_analysis_report_workflow_contract",
    "tests.test_analysis_report_stage_b_contract",
    "tests.test_analysis_report_authz_contract",
    "tests.test_analysis_report_wave9_contract",
    "tests.test_p3_controlled_ingestion",
    "tests.test_material_rag_local_extractive",
    "tests.test_aeco_wave7_backend_contracts",
    "tests.test_aeco_wave7_frontend_contracts",
]

INTEGRATION_SUITES = [
    "tests.test_analysis_report_postgres_integration",
]

FRONTEND_COMMANDS = [
    ("lint", ["npm", "run", "lint"], "src/web"),
    ("build", ["npm", "run", "build"], "src/web"),
    ("verify:material-automation", ["npm", "run", "verify:material-automation"], "src/web"),
]


def _resolve_python() -> str:
    """Prefer the project venv; fall back to current interpreter."""
    venv = Path("/Users/lichenhao/Desktop/安环项目/.venv/bin/python")
    if venv.is_file():
        return str(venv)
    return sys.executable


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def _run_unittest(suites: list[str]) -> dict:
    python = _resolve_python()
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")
    cmd = [python, "-B", "-m", "unittest"] + suites
    result = subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=600, env=env,
    )
    # unittest writes results to stderr; combine for parsing
    stdout = (result.stdout or "") + "\n" + (result.stderr or "")
    # Parse "Ran N tests in X" and "OK" / "FAILED (failures=N, errors=M, skipped=K)"
    ran = 0
    failures = 0
    errors = 0
    skipped = 0
    ok = False
    for line in stdout.splitlines():
        if line.startswith("Ran "):
            try:
                ran = int(line.split()[1])
            except (ValueError, IndexError):
                pass
        if line.strip() == "OK":
            ok = True  # provisional; final ok also requires exit_code == 0
        if line.startswith("FAILED"):
            ok = False
            # parse failure counts
            import re
            for m in re.finditer(r"failures=(\d+)", line):
                failures = int(m.group(1))
            for m in re.finditer(r"errors=(\d+)", line):
                errors = int(m.group(1))
            for m in re.finditer(r"skipped=(\d+)", line):
                skipped = int(m.group(1))
        if line.startswith("OK") and "skipped=" in line:
            import re
            for m in re.finditer(r"skipped=(\d+)", line):
                skipped = int(m.group(1))
    # A non-zero exit code means the test runner itself failed (e.g. import
    # error, signal, or post-test cleanup failure).  Text "OK" alone is not
    # sufficient — a process that prints OK then crashes still fails.
    if result.returncode != 0:
        ok = False
        if errors == 0:
            errors = 1  # ensure the caller sees a failure count
    return {
        "suites": suites,
        "command": " ".join(["python", "-B", "-m", "unittest"] + suites),
        "exit_code": result.returncode,
        "total": ran,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "ok": ok,
        "exit_code_detail": (
            "pass" if result.returncode == 0 else f"nonzero_exit_code={result.returncode}"
        ),
        "stdout_tail": stdout[-500:] if len(stdout) > 500 else stdout,
    }


def _run_frontend(group: str) -> dict:
    commands = FRONTEND_COMMANDS if group == "all" else []
    results = []
    for name, cmd, cwd in commands:
        npm = shutil.which("npm")
        if not npm:
            return {"status": "NOT_TESTED", "reason": "npm not found"}
        result = subprocess.run(
            [npm, "run", name] if cmd[0:2] == ["npm", "run"] else cmd,
            cwd=REPO_ROOT / cwd, capture_output=True, text=True, timeout=600,
        )
        results.append({
            "name": name,
            "exit_code": result.returncode,
            "ok": result.returncode == 0,
            "stdout_tail": (result.stdout or "")[-300:],
        })
    return {
        "status": "PASSED" if all(r["ok"] for r in results) else "FAILED",
        "commands": results,
    }


def _check_environment(mode: str) -> tuple[bool, str]:
    if mode == "offline":
        return True, ""
    if mode == "integration":
        # Check for docker / running postgres
        if shutil.which("docker") is None:
            return False, "docker not available"
        return True, ""
    if mode == "browser":
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if not chrome.is_file():
            return False, "Chrome not found at expected path"
        return True, ""
    if mode == "restore":
        if shutil.which("docker") is None:
            return False, "docker not available"
        return True, ""
    if mode == "quality":
        return True, ""
    return False, f"unknown mode: {mode}"


def run_gate(mode: str, evidence_dir: Path | None) -> int:
    sha = _git_sha()
    available, reason = _check_environment(mode)
    if not available:
        report = {
            "sha": sha,
            "mode": mode,
            "status": "NOT_TESTED",
            "reason": reason,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        print(json.dumps(report, indent=2))
        print(f"ACCEPTANCE_GATE mode={mode} status=NOT_TESTED reason={reason}")
        return 2

    results: dict = {
        "sha": sha,
        "mode": mode,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if mode == "offline":
        # Backend unittest suites
        backend = _run_unittest(OFFLINE_SUITES)
        results["backend"] = backend
        # Frontend lint/build/verify
        frontend = _run_frontend("all")
        results["frontend"] = frontend
        all_ok = backend["ok"] and frontend.get("status") == "PASSED"
    elif mode == "integration":
        backend = _run_unittest(INTEGRATION_SUITES)
        results["backend"] = backend
        all_ok = backend["ok"]
    else:
        # browser/restore/quality: placeholder until those phases implement their runners
        results["status"] = "NOT_IMPLEMENTED_YET"
        all_ok = False

    results["status"] = "PASSED" if all_ok else "FAILED"
    report_json = json.dumps(results, indent=2, ensure_ascii=False)

    # Write evidence file
    if evidence_dir:
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_file = evidence_dir / f"acceptance_{mode}_{sha[:8]}_{int(time.time())}.json"
        evidence_file.write_text(report_json, encoding="utf-8")
        results["evidence_file"] = str(evidence_file)

    print(report_json)
    print(f"ACCEPTANCE_GATE mode={mode} status={results['status']} sha={sha[:12]}")
    return 0 if all_ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        required=True,
        choices=["offline", "integration", "browser", "restore", "quality"],
        help="Which acceptance mode to run",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Directory to write the structured evidence JSON (default: no file)",
    )
    args = parser.parse_args()
    return run_gate(args.mode, args.evidence_dir)


if __name__ == "__main__":
    sys.exit(main())
