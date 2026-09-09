#!/usr/bin/env python3
"""Acceptance evidence: real case outcomes, process results, full logs and source.

Exit 0: PASSED. Exit 1: FAILED/ERROR/INCOMPLETE/SOURCE_CHANGED.
Exit 2: NOT_TESTED/NOT_IMPLEMENTED. Evidence is always saved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
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
    "tests.test_engineering_closeout_migration",
    "tests.test_p2_wave1",
    "tests.test_analysis_report_soft_archive",
    "tests.test_material_layout_regressions",
    "tests.test_material_pdf_renderer",
    "tests.test_material_native_visibility",
    "tests.test_native_evidence",
    "tests.test_docx_package_compatibility",
    "tests.test_xlsx_native_evidence",
    "tests.test_jpeg_native_evidence",
    "tests.test_native_evidence_envelopes",
    "tests.test_native_evidence_worker",
    "tests.test_material_reviews",
    "tests.test_effective_evidence",
    "tests.test_analysis_report_harness_isolation",
    "tests.test_acceptance_evidence",
    "tests.test_material_quality_gate",
    "tests.test_material_browser_evidence",
    "tests.test_candidate_ops_check",
]
INTEGRATION_SUITES = [
    "tests.test_storage_service_runtime",
    "tests.test_object_reconcile_postgres",
    "tests.test_ocr_cache_postgres",
    "tests.test_candidate_ops_postgres",
    "tests.test_analysis_report_harness_parallel",
    "tests.test_analysis_report_postgres_integration",
    "tests.test_analysis_report_transition_postgres",
    "tests.test_material_layout_postgres",
    "tests.test_analysis_report_history_upgrade_postgres",
    "tests.test_invitation_join_postgres",
    "tests.test_business_identity_postgres",
    "tests.test_client_portal_access_postgres",
    "tests.test_membership_management_postgres",
    "tests.test_crm_creation_postgres",
    "tests.test_native_evidence_postgres",
]
RESTORE_SUITES = ["tests.test_analysis_report_restore_postgres"]
FRONTEND_COMMANDS = ["lint", "build", "verify:material-automation", "verify:frontend-context"]


def _git(*args: str, root: Path) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE, timeout=30)


def source_fingerprint(root: Path, excluded: Path) -> dict:
    """Hash tracked and nonignored new files, including dirty/deleted source.

    The evidence tree is excluded explicitly to prevent self-modifying proof.
    Ignored dependencies/build outputs follow the checkout's gitignore contract.
    """
    try:
        head = _git("rev-parse", "HEAD", root=root).decode().strip()
        paths = set(_git("ls-files", "--cached", "--others", "--exclude-standard", "-z", root=root).split(b"\0"))
        entries = []
        for raw in sorted(paths - {b""}):
            relative = os.fsdecode(raw)
            path = root / relative
            if path.absolute().is_relative_to(excluded.absolute()):
                continue
            if path.is_symlink():
                content, kind = os.fsencode(os.readlink(path)), "symlink"
            elif path.is_file():
                content, kind = path.read_bytes(), "file"
            elif not path.exists():
                content, kind = b"", "missing"
            else:
                # Git submodule contents require their own snapshot contract.
                raise ValueError(f"unsupported source entry: {relative}")
            entries.append({"path": relative, "kind": kind,
                            "sha256": hashlib.sha256(content).hexdigest()})
        serialized = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        return {"status": "CAPTURED", "head": head,
                "working_tree_sha256": hashlib.sha256(serialized).hexdigest(),
                "git_status": _git("status", "--porcelain=v1", root=root).decode(),
                "files": entries, "excluded_evidence_dir": str(excluded)}
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return {"status": "ERROR", "reason": str(exc)}


def run_process(command: list[str], cwd: Path, log_path: Path, *, timeout: float,
                env: dict | None = None, termination_grace: float = 10) -> dict:
    started = time.monotonic()
    result = {"command": command, "cwd": str(cwd), "log": str(log_path),
              "exit_code": None, "timed_out": False, "launch_error": None}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        try:
            process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=os.name == "posix")
        except OSError as exc:
            result["launch_error"] = str(exc)
            log.write((str(exc) + "\n").encode())
        else:
            try:
                result["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=termination_grace)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                result["exit_code"] = process.returncode
    result["seconds"] = round(time.monotonic() - started, 3)
    result["log_sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
    return result


def summarize_journal(path: Path, process: dict) -> dict:
    events, journal_errors = [], []
    if path.exists():
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                events.append(json.loads(line))
            except (ValueError, TypeError) as exc:
                journal_errors.append({"line": line_number, "error": str(exc)})
    cases = {}
    for event in events:
        if event["event"] == "collected":
            cases = {item["ordinal"]: {**item, "started": False, "outcome": "not_run"}
                     for item in event["cases"]}
        elif event["event"] in {"case_start", "case_finish"} and event["ordinal"] in cases:
            case = cases[event["ordinal"]]
            if event["event"] == "case_start":
                case.update(started=True, outcome="incomplete")
            else:
                case["outcome"] = event["outcome"]
    outcomes = ["passed", "failed", "error", "skipped", "expected_failure",
                "unexpected_success", "incomplete", "not_run"]
    counts = {name: sum(c["outcome"] == name for c in cases.values()) for name in outcomes}
    counts.update(collected=len(cases), run=sum(c["started"] for c in cases.values()))
    discovery = [e for e in events if e["event"] == "discovery_error"]
    fixture = [e for e in events if e["event"].startswith("fixture_")]
    runner_errors = [e for e in events if e["event"] == "runner_error"]
    complete = any(e["event"] == "runner_complete" for e in events)
    # unittest's normal exit 1 is explained by genuine test/discovery/fixture
    # outcomes. Signals, missing completion and other exits are process errors.
    expected_nonzero = bool(counts["failed"] or counts["error"] or counts["unexpected_success"]
                            or discovery or any(e["event"] == "fixture_error" for e in fixture))
    process_error = bool(process["launch_error"] or process["timed_out"] or not complete
                         or process["exit_code"] not in (0, 1)
                         or (process["exit_code"] == 1 and not expected_nonzero))
    if process_error or journal_errors or discovery or runner_errors or any(e["event"] == "fixture_error" for e in fixture):
        status = "NOT_TESTED" if not counts["run"] else "ERROR"
    elif counts["failed"] or counts["error"] or counts["unexpected_success"]:
        status = "FAILED"
    elif not counts["collected"] or not counts["run"]:
        status = "NOT_TESTED"
    elif any(counts[key] for key in ("skipped", "expected_failure", "incomplete", "not_run")) or fixture:
        status = "INCOMPLETE"
    else:
        status = "PASSED"
    return {"status": status, "counts": counts, "cases": list(cases.values()),
            "discovery_errors": discovery, "fixture_events": fixture, "runner_errors": runner_errors,
            "journal_errors": journal_errors, "runner_completed": complete,
            "process_error": process_error, "process": process, "journal": str(path)}


def run_unittest(suites: list[str], evidence: Path, *, python: str, timeout: float,
                 root: Path = REPO_ROOT, extra_env: dict | None = None) -> dict:
    journal = evidence / "unittest-events.jsonl"
    env = {**os.environ, **(extra_env or {})}
    env["PYTHONPATH"] = os.pathsep.join([str(root), str(root / "src"), env.get("PYTHONPATH", "")])
    env.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")
    command = [python, "-B", str(REPO_ROOT / "scripts/unittest_evidence_runner.py"),
               "--journal", str(journal), *suites]
    process = run_process(command, root, evidence / "unittest.log", timeout=timeout, env=env)
    return {"suites": suites, **summarize_journal(journal, process)}


def run_frontend(evidence: Path, timeout: float) -> dict:
    npm = shutil.which("npm")
    results = []
    for name in FRONTEND_COMMANDS:
        if name == "verify:frontend-context":
            # The package script is the single declared suite inventory. A
            # second hard-coded list silently omitted newly added UI tests.
            package = json.loads((REPO_ROOT / "src/web/package.json").read_text())
            command = package["scripts"][name].split()
            if command[:2] != ["node", "--test"] or len(command) < 3 or any(
                not path.startswith("scripts/") or not path.endswith(".test.cjs") or ".." in path
                for path in command[2:]
            ):
                results.append({"name": name, "status": "FAILED", "reason": "FRONTEND_SUITE_INVENTORY_INVALID"})
                continue
            result = run_node_tests(command[2:], evidence,
                                    cwd=REPO_ROOT / "src/web", timeout=timeout)
            results.append({"name": name, **result})
            continue
        process = run_process([npm or "npm", "run", name], REPO_ROOT / "src/web",
                              evidence / (name.replace(":", "-") + ".log"), timeout=timeout)
        status = "NOT_TESTED" if process["launch_error"] else (
            "PASSED" if process["exit_code"] == 0 and not process["timed_out"] else "FAILED")
        results.append({"name": name, "status": status, "process": process})
    return {"status": combine_statuses([r["status"] for r in results]), "commands": results}


def run_node_tests(files: list[str], evidence: Path, *, cwd: Path, timeout: float) -> dict:
    journal = evidence / "node-events.jsonl"
    command = [shutil.which("node") or "node", "--test", "--test-reporter=spec",
               "--test-reporter-destination=stdout",
               "--test-reporter=" + str(REPO_ROOT / "scripts/node_evidence_reporter.cjs"),
               "--test-reporter-destination=" + str(journal), *files]
    process = run_process(command, cwd, evidence / "verify-frontend-context.log", timeout=timeout)
    events, journal_errors, cases, wrapper_errors = [], [], {}, []
    if journal.exists():
        for number, line in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
            try:
                events.append(json.loads(line))
            except ValueError as exc:
                journal_errors.append({"line": number, "error": str(exc)})
    wrappers = set(files) | {str((cwd / file).resolve()) for file in files}
    for event in events:
        data = event.get("data", {})
        if event["type"] not in {"test:enqueue", "test:dequeue", "test:pass", "test:fail"}:
            continue
        if data.get("name") in wrappers or data.get("type") == "suite" or data.get("details", {}).get("type") == "suite":
            if event["type"] == "test:fail":
                wrapper_errors.append(event)
            continue
        identity = f'{data.get("file", "")}:{data.get("line", "")}:{data.get("column", "")}:{data.get("name", "")}'
        case = cases.setdefault(identity, {"id": identity, "name": data.get("name"), "started": False, "outcome": "not_run"})
        if event["type"] == "test:dequeue":
            case.update(started=True, outcome="incomplete")
        elif event["type"] in {"test:pass", "test:fail"}:
            outcome = "skipped" if data.get("skip") else ("todo" if data.get("todo") else (
                "passed" if event["type"] == "test:pass" else "failed"))
            case.update(started=True, outcome=outcome)
    counts = {name: sum(c["outcome"] == name for c in cases.values())
              for name in ["passed", "failed", "skipped", "todo", "incomplete", "not_run"]}
    counts.update(collected=len(cases), run=sum(c["started"] for c in cases.values()))
    complete = any(e["type"] == "evidence:complete" for e in events)
    process_error = bool(process["launch_error"] or process["timed_out"] or not complete
                         or process["exit_code"] not in (0, 1)
                         or (process["exit_code"] == 1 and not (counts["failed"] or wrapper_errors)))
    if process_error or journal_errors or wrapper_errors:
        status = "ERROR" if counts["run"] else "NOT_TESTED"
    elif counts["failed"]:
        status = "FAILED"
    elif not counts["run"]:
        status = "NOT_TESTED"
    elif any(counts[name] for name in ["skipped", "todo", "incomplete", "not_run"]):
        status = "INCOMPLETE"
    else:
        status = "PASSED"
    return {"status": status, "counts": counts, "cases": list(cases.values()),
            "journal": str(journal), "journal_errors": journal_errors,
            "wrapper_errors": wrapper_errors, "runner_completed": complete,
            "process_error": process_error, "process": process}


def combine_statuses(statuses: list[str]) -> str:
    if not statuses:
        return "NOT_TESTED"
    for status in ("ERROR", "FAILED", "NOT_IMPLEMENTED", "NOT_TESTED", "INCOMPLETE"):
        if status in statuses:
            return status
    return "PASSED" if all(s == "PASSED" for s in statuses) else "ERROR"


def run_quality_process(evidence: Path, *, python: str, timeout: float,
                        manifest: Path | None, live_ocr: bool) -> dict:
    output=evidence/'quality-result.json'
    command=[python,'-B',str(REPO_ROOT/'scripts/material_quality_gate.py'),'--output',str(output)]
    if manifest is not None:command.extend(['--manifest',str(manifest.resolve())])
    if live_ocr:command.append('--live-ocr')
    process=run_process(command,REPO_ROOT,evidence/'quality.log',timeout=timeout)
    try:
        result=json.loads(output.read_text())
        status=result['status'];counts=result['counts']
        if status not in {'PASSED','FAILED','NOT_TESTED'} or result['scope']!='real':raise ValueError
        if status=='PASSED' and (not counts['total'] or counts['total']!=counts['passed']
            or counts['failed'] or counts['not_tested'] or counts['expected_rejections']
            or len(result['samples'])!=counts['total']
            or {x['format'] for x in result['samples']}!={'pdf','docx','xlsx','jpeg'}
            or any(x['outcome']!='PASS' for x in result['samples'])):raise ValueError
        if process['exit_code']!={'PASSED':0,'FAILED':1,'NOT_TESTED':2}[status]:raise ValueError
    except (OSError,ValueError,KeyError,TypeError):result={'status':'ERROR'}
    if process['launch_error']:result['status']='NOT_TESTED'
    elif process['timed_out']:result['status']='ERROR'
    return {**result,'process':process,'result_file':str(output)}


def run_browser(evidence: Path, *, python: str, timeout: float) -> dict:
    process=run_process([python, "-B", str(REPO_ROOT/"scripts/material_browser_gate.py"),
                         "--recovery-fault","--evidence-dir",str(evidence)],REPO_ROOT,evidence/"browser.log",
                        timeout=timeout,termination_grace=360)
    result_file=evidence/"browser-result.json"
    cases={};journal_errors=[];collected=False;reported=set()
    journal=evidence/"material-checks.jsonl"
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                event=json.loads(line)
                if event['event']=='collected':
                    if collected or not event['checks'] or len(set(event['checks']))!=len(event['checks']):raise ValueError('invalid collection')
                    cases={identity:{'id':identity,'outcome':'not_run'} for identity in event['checks']};collected=True
                elif event['event']=='check':
                    if event['id'] not in cases or event['outcome'] not in {'passed','failed','not_run'} or event['id'] in reported:raise ValueError('invalid outcome')
                    reported.add(event['id'])
                    cases[event['id']]=event
                elif event['event']=='interaction':
                    if event.get('phase') not in {'before_click','after_click'}:raise ValueError('invalid interaction')
                else:raise ValueError('unknown event')
            except (ValueError,KeyError,TypeError):journal_errors.append('BROWSER_JOURNAL_INVALID')
    checks=list(cases.values())
    counts={outcome:sum(x['outcome']==outcome for x in checks) for outcome in ['passed','failed','not_run']}
    counts.update(collected=len(checks),run=len(checks)-counts['not_run'])
    try:
        result=json.loads(result_file.read_text());status=result['status']
        if status not in {'PASSED','FAILED','NOT_TESTED'}:raise ValueError
        if status=='PASSED' and (not checks or counts['passed']!=len(checks)
            or result.get('runtime_recovery',{}).get('state')!='PASSED'
            or result.get('owned_image_tags_removed') is not True
            or not result.get('shared_unchanged') or result.get('cleanup')!={'containers':0,'volumes':0,'networks':0,'control_removed':True}):status='INCOMPLETE'
    except (OSError,ValueError,KeyError,TypeError):status='ERROR'
    if process['launch_error']:status='NOT_TESTED'
    elif process['timed_out'] or process['exit_code'] not in (0,1) or (process['exit_code']==0 and status!='PASSED') or (process['exit_code']==1 and status=='PASSED') or journal_errors:status='ERROR'
    return {'status':status,'scope':'SYNTHETIC_BROWSER_ENGINEERING','counts':counts,
        'cases':checks,'journal':str(journal),'journal_errors':journal_errors,
        'process':process,'result_file':str(result_file)}


def run_gate(mode: str, evidence_dir: Path | None, *, python: str = sys.executable,
             timeout: float = 1200, quality_manifest: Path | None = None, quality_live_ocr: bool = False) -> int:
    evidence_base = (evidence_dir or REPO_ROOT / ".local/acceptance").resolve()
    evidence = evidence_base / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + mode + "-" + uuid.uuid4().hex[:8])
    evidence.mkdir(parents=True)
    before = source_fingerprint(REPO_ROOT, evidence_base)
    report = {"schema_version": 2, "mode": mode,
              "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "source_before": before, "evidence_file": str(evidence / "report.json")}
    if mode == "quality":
        report["quality"] = run_quality_process(evidence,python=python,timeout=timeout,manifest=quality_manifest,live_ocr=quality_live_ocr)
        report["status"] = report["quality"]["status"]
    else:
        available = True
        if mode in {"integration", "restore", "browser"}:
            environment = run_process([shutil.which("docker") or "docker", "info", "--format", "{{.ServerVersion}}"],
                                      REPO_ROOT, evidence / "docker-info.log", timeout=30)
            report["environment"] = environment
            available = environment["exit_code"] == 0 and not environment["timed_out"]
        if not available:
            report.update(status="NOT_TESTED", reason="Docker engine is not available.")
        elif mode == "browser":
            report["browser"] = run_browser(evidence,python=python,timeout=timeout)
            report["status"] = report["browser"]["status"]
        else:
            suites={"offline":OFFLINE_SUITES,"integration":INTEGRATION_SUITES,"restore":RESTORE_SUITES}[mode]
            report["backend"] = run_unittest(suites,
                                              evidence, python=python, timeout=timeout)
            statuses = [report["backend"]["status"]]
            if mode == "offline":
                report["frontend"] = run_frontend(evidence, timeout)
                statuses.append(report["frontend"]["status"])
            report["status"] = combine_statuses(statuses)
    after = source_fingerprint(REPO_ROOT, evidence_base)
    report["source_after"] = after
    report["source_stable"] = (before.get("status") == after.get("status") == "CAPTURED"
                               and before["head"] == after["head"]
                               and before["working_tree_sha256"] == after["working_tree_sha256"])
    if report["status"] == "PASSED" and not report["source_stable"]:
        report["status"] = "SOURCE_CHANGED"
    (evidence / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Keep console readable; complete source manifest, case IDs and errors are in report.json.
    print(json.dumps({"mode": mode, "status": report["status"], "source_stable": report["source_stable"],
                      "counts": report.get("backend", report.get("browser", report.get("quality", {}))).get("counts"), "evidence_file": report["evidence_file"]}, ensure_ascii=False))
    return 0 if report["status"] == "PASSED" else (2 if report["status"] in {"NOT_TESTED", "NOT_IMPLEMENTED"} else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["offline", "integration", "browser", "restore", "quality"])
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--python", default=sys.executable, help="Interpreter for unittest (default: current interpreter).")
    parser.add_argument("--timeout", type=float, default=1200, help="Seconds per child process.")
    parser.add_argument("--quality-manifest", type=Path, help="Reviewed real-material gold manifest; absent input remains NOT_TESTED.")
    parser.add_argument("--quality-live-ocr", action="store_true", help="Use configured OCR for explicitly supplied reviewed real materials.")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return run_gate(args.mode, args.evidence_dir, python=args.python, timeout=args.timeout,quality_manifest=args.quality_manifest,quality_live_ocr=args.quality_live_ocr)


if __name__ == "__main__":
    sys.exit(main())
