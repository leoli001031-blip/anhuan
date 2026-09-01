"""Dedicated localhost analysis-report demo supervisor.

Independent compose project, ports, and control directory. Does not publish
ports beyond 127.0.0.1, does not call Ark, and does not enable frontend mock.
Synthetic fixture materials are not real customer data.
REMOTE_STAGING_TARGET_NOT_AUTHORIZED. NOT_PRODUCTION.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from infra.f1 import analysis_report_uat as uat
from infra.f1.analysis_report_uat import (
    COMPOSE_FILE,
    LC,
    ROOT,
    UatError,
    _apply_fixture,
    _assert_ocr_runtime,
    _environment,
    _run,
    _seed_identities,
    _shared_fingerprint,
    _validate_analysis_report_secret_set,
    _write_docker_config,
    _write_env,
    _write_receipt,
    _write_secrets,
)


OVERLAY_FILE = ROOT / "infra/f1/docker-compose.analysis-report-demo.yml"
NO_OCR_OVERLAY_FILE = (
    ROOT / "infra/f1/docker-compose.analysis-report-no-ocr.yml"
)
SCOPE = "analysis-report-demo"
CONTROL_SCHEMA = "anhuan-analysis-report-demo-control-v1"
WORKSPACE_SHA256 = hashlib.sha256(
    f"{CONTROL_SCHEMA}\0{ROOT}\0{os.geteuid()}".encode("utf-8")
).hexdigest()
PROBE = WORKSPACE_SHA256[:12]
NO_OCR_WORKSPACE_SHA256 = hashlib.sha256(
    f"{CONTROL_SCHEMA}\0{ROOT}\0{os.geteuid()}\0ocr-disabled".encode("utf-8")
).hexdigest()
OCR_MODE_ENV = "A_ECO_ANALYSIS_REPORT_OCR_MODE"
PROJECT_RE = re.compile(r"^anhuan-ar-demo-[0-9a-f]{12}\Z")
STATUS_KEYS = frozenset(
    {
        "ark_calls",
        "client_login_ready",
        "f1_head",
        "generator",
        "mock_data",
        "provider_login_ready",
        "ready",
        "shared_match",
        "workflow_seeded",
    }
)


class DemoError(UatError):
    pass


def _ocr_disabled() -> bool:
    mode = os.environ.get(OCR_MODE_ENV, "required").strip().lower()
    if mode in {"", "required"}:
        return False
    if mode == "disabled":
        return True
    raise DemoError("LOCAL_ANALYSIS_REPORT_OCR_MODE_INVALID")


def _identity() -> dict[str, object]:
    workspace_sha256 = (
        NO_OCR_WORKSPACE_SHA256 if _ocr_disabled() else WORKSPACE_SHA256
    )
    probe = workspace_sha256[:12]
    project_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"{CONTROL_SCHEMA}:{workspace_sha256}"
    ).hex
    return {
        "schema": CONTROL_SCHEMA,
        "project_id": project_id,
        "compose_project": f"anhuan-ar-demo-{probe}",
        "pgint_project_name": f"anhuan-ar-pgint-{probe}",
        "database": f"f1_arpg_{probe}",
        "runtime_image": f"anhuan-ar-demo-runtime:{probe}",
        "web_image": f"anhuan-ar-demo-web:{probe}",
        "control_dir": f"/private/tmp/anhuan-ar-pgint-{probe}",
        "shared_before": "",
    }


def _control_paths(state: dict[str, object]) -> dict[str, Path]:
    return uat._control_paths(state)


def _compose(state: dict[str, object], paths: dict[str, Path], *arguments: str, timeout: int = 600) -> None:
    command = [
        LC._docker(),
        "compose",
        "--ansi",
        "never",
        "--project-name",
        str(state["compose_project"]),
        "--env-file",
        str(paths["env"]),
        "-f",
        str(COMPOSE_FILE),
        "-f",
        str(OVERLAY_FILE),
    ]
    if _ocr_disabled():
        command.extend(["-f", str(NO_OCR_OVERLAY_FILE)])
    command.extend(
        [
            "--profile",
            "ops",
            "--profile",
            "analysis-report",
            *arguments,
        ]
    )
    _run(command, paths=paths, timeout=timeout)


def _inventory(project: str, paths: dict[str, Path]) -> tuple[int, int, int]:
    def count(kind: str) -> int:
        if kind == "ps":
            command = [LC._docker(), "ps", "-a", "-q", "--no-trunc"]
        else:
            command = [LC._docker(), kind, "ls", "-q"]
        command.extend(["--filter", f"label=com.docker.compose.project={project}"])
        result = _run(command, paths=paths, timeout=60, check=False)
        if result.returncode != 0:
            raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_INVENTORY_FAILED")
        return len([line for line in result.stdout.splitlines() if line])

    return count("ps"), count("volume"), count("network")


def _scope_counts(paths: dict[str, Path]) -> tuple[int, int, int]:
    def count(arguments: list[str]) -> int:
        result = _run(
            [LC._docker(), *arguments, "--filter", f"label=io.anhuan.scope={SCOPE}"],
            paths=paths,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_INVENTORY_FAILED")
        return len([line for line in result.stdout.splitlines() if line])

    return (
        count(["ps", "-a", "--format", "{{.ID}}"]),
        count(["volume", "ls", "--format", "{{.Name}}"]),
        count(["network", "ls", "--format", "{{.ID}}"]),
    )


def _initialize() -> tuple[dict[str, object], dict[str, Path]]:
    identity = _identity()
    control_dir = Path(str(identity["control_dir"]))
    if not control_dir.exists():
        control_dir.mkdir(mode=0o700)
    LC._directory(control_dir)
    state = {
        **identity,
        "web_port": LC._free_port(),
        "pg_port": LC._free_port(),
        "shared_before": _shared_fingerprint().hex(),
    }
    paths = _control_paths(state)
    for directory in (paths["secrets"], paths["home"], paths["tmp"]):
        if not directory.exists():
            directory.mkdir(mode=0o700)
        LC._directory(directory)
    payload = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )
    if paths["state"].exists():
        LC._secure_file(paths["state"], maximum=16384)
        existing = json.loads(paths["state"].read_text(encoding="ascii"))
        if not isinstance(existing, dict) or str(existing.get("compose_project")) != str(
            state["compose_project"]
        ):
            raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_STATE_INVALID")
        state = {
            **state,
            "web_port": int(existing["web_port"]),
            "pg_port": int(existing["pg_port"]),
            "shared_before": str(existing.get("shared_before") or state["shared_before"]),
        }
        paths = _control_paths(state)
    else:
        LC._exclusive_write(paths["state"], payload)
    _write_docker_config(paths)
    _write_secrets(state, paths)
    _validate_analysis_report_secret_set(paths["secrets"])
    _write_env(state, paths)
    _write_receipt(state, paths)
    return state, paths


def _start_stack(state: dict[str, object], paths: dict[str, Path]) -> None:
    ocr_disabled = _ocr_disabled()
    if not ocr_disabled:
        _assert_ocr_runtime(paths)
    _compose(state, paths, "run", "--rm", "--no-deps", "secret-init", timeout=180)
    _compose(state, paths, "build", "migrator", "web", timeout=1800)
    runtime_services = [
        "api",
        "worker",
        "dispatcher",
        "ingestion-worker",
        "report-worker",
        "web",
    ]
    if not ocr_disabled:
        runtime_services.insert(0, "material-rag-ocr")
    _compose(
        state,
        paths,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "360",
        "postgres",
        "keycloak",
        "minio",
        "redis",
        "clamd",
        timeout=420,
    )
    _compose(state, paths, "run", "--rm", "migrator", timeout=600)
    _seed_identities(state, paths)
    _compose(state, paths, "run", "--rm", "keycloak-provisioner", timeout=180)
    _compose(
        state,
        paths,
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "180",
        *runtime_services,
        timeout=240,
    )


def _password_grant(origin: str, secrets: Path, username: str, secret_name: str) -> bool:
    password = (secrets / secret_name).read_text(encoding="ascii").strip()
    body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "anhuan-web",
            "username": username,
            "password": password,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"{origin}/realms/anhuan/protocol/openid-connect/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    token = payload.get("access_token")
    return isinstance(token, str) and len(token) > 20


def _readiness_ready(origin: str) -> bool:
    request = urllib.request.Request(f"{origin}/api/readyz", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                return False
            if response.headers.get("Cache-Control", "").lower() != "no-store":
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    components = payload.get("components") if isinstance(payload, dict) else None
    return bool(
        payload.get("status") == "ready"
        and isinstance(components, dict)
        and components
        and all(value is True for value in components.values())
    )


def _session_role(origin: str, secrets: Path, username: str, secret_name: str, enterprise: str) -> str | None:
    password = (secrets / secret_name).read_text(encoding="ascii").strip()
    body = urllib.parse.urlencode(
        {
            "grant_type": "password",
            "client_id": "anhuan-web",
            "username": username,
            "password": password,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        f"{origin}/realms/anhuan/protocol/openid-connect/token",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = payload.get("access_token")
        if not isinstance(token, str):
            return None
        access = urllib.request.Request(
            f"{origin}/api/v1/session/access",
            method="GET",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Enterprise-Id": enterprise,
            },
        )
        with urllib.request.urlopen(access, timeout=15) as response:
            session = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    role = session.get("product_role")
    return role if isinstance(role, str) else None


def _head_and_seeded(state: dict[str, object], paths: dict[str, Path]) -> tuple[str, int]:
    from infra.f1.migrate_f1 import _bootstrap_dsn
    import psycopg

    uat._rewrite_host_bootstrap_dsn(state, paths)
    original = dict(os.environ)
    try:
        os.environ.update(uat._pg_env(state, paths))
        fixture = uat._load_fixture()
        with psycopg.connect(_bootstrap_dsn(), autocommit=True, connect_timeout=5) as connection:
            head = connection.execute(
                "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM f1.alembic_version"
            ).fetchone()
            rows = connection.execute(
                "SELECT scope.scope_kind, count(*) "
                "FROM f1.document_version AS version "
                "JOIN f1.document_record AS record "
                "  ON record.enterprise_id = version.enterprise_id "
                " AND record.id = version.document_record_id "
                "JOIN f1.upload_task AS task "
                "  ON task.enterprise_id = version.enterprise_id "
                " AND task.id = version.upload_task_id "
                "JOIN f1.material_knowledge_scope AS scope "
                "  ON scope.enterprise_id = record.enterprise_id "
                " AND scope.id = record.knowledge_scope_id "
                "JOIN f1.material_rag_unit AS unit "
                "  ON unit.enterprise_id = version.enterprise_id "
                " AND unit.document_version_id = version.id "
                " AND unit.document_record_id = record.id "
                " AND unit.source_sha256 = task.content_sha256 "
                "WHERE version.enterprise_id = %s "
                "  AND record.status = 'active' "
                "  AND task.pipeline_kind = 'controlled_ingestion' "
                "  AND task.quarantine_status = 'released' "
                "  AND task.released_at IS NOT NULL "
                "  AND task.scan_verdict = 'clean' "
                "  AND task.preview_status = 'ready' "
                "  AND task.status = 'done' "
                "  AND ("
                "    (scope.scope_kind = 'service_provider' AND scope.client_account_id IS NULL) "
                "    OR (scope.scope_kind = 'client' AND scope.client_account_id = %s)"
                "  ) "
                "GROUP BY scope.scope_kind",
                (
                    __import__("infra.f1.local_seed", fromlist=["ENTERPRISE_A"]).ENTERPRISE_A,
                    fixture.CRM_ACCOUNT_ID,
                ),
            ).fetchall()
        counts = {str(kind): int(n) for kind, n in rows}
        seeded = 1 if counts.get("service_provider") == 1 and counts.get("client") == 1 else 0
        version = head[0] if head is not None else ""
        return str(version), seeded
    finally:
        os.environ.clear()
        os.environ.update(original)


def _api_generator_flags(state: dict[str, object], paths: dict[str, Path]) -> tuple[str, int]:
    result = _run(
        [
            LC._docker(),
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={state['compose_project']}",
            "--filter",
            "label=com.docker.compose.service=api",
            "--format",
            "{{.ID}}",
        ],
        paths=paths,
        timeout=30,
        check=False,
    )
    container = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or len(container) != 1:
        return "", 1
    inspect = _run(
        [LC._docker(), "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", container[0]],
        paths=paths,
        timeout=30,
        check=False,
    )
    blob = inspect.stdout or ""
    local = "F1_MATERIAL_ANALYSIS_REPORT_LOCAL=1" in blob.splitlines()
    engineering = "F1_LOCAL_ENGINEERING=1" in blob.splitlines()
    local_qa = "F1_MATERIAL_QA_LOCAL_EXTRACTIVE=1" in blob.splitlines()
    ark = any("ARK" in line and line.split("=", 1)[-1] not in {"", "0", "false", "False"} for line in blob.splitlines())
    if local and engineering and local_qa and not ark:
        return "evidence_local", 0
    return "", 1


def run_start() -> dict[str, str]:
    shared = _shared_fingerprint()
    state = None
    paths = None
    try:
        state, paths = _initialize()
        if not PROJECT_RE.fullmatch(str(state["compose_project"])):
            raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_PROJECT_INVALID")
        _start_stack(state, paths)
        _apply_fixture(state, paths)
        if _shared_fingerprint() != shared:
            raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_SHARED_DRIFT")
        origin = f"http://127.0.0.1:{int(state['web_port'])}"
        return {
            "url": origin,
            "provider_username": "tenant-a",
            "client_username": "invitee",
        }
    except Exception as error:
        if state is not None and paths is not None:
            try:
                run_stop()
            except Exception:
                pass
        if isinstance(error, LC.LocalError):
            raise DemoError(str(error) or "LOCAL_ANALYSIS_REPORT_DEMO_LOCALCTL") from error
        raise


def run_status() -> dict[str, object]:
    identity = _identity()
    paths = _control_paths(identity)
    if not paths["state"].exists():
        raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_NOT_STARTED")
    LC._secure_file(paths["state"], maximum=16384)
    state = json.loads(paths["state"].read_text(encoding="ascii"))
    origin = f"http://127.0.0.1:{int(state['web_port'])}"
    leftovers = _inventory(str(state["compose_project"]), paths)
    ready = 1 if leftovers[0] > 0 and _readiness_ready(origin) else 0
    head, seeded = _head_and_seeded(state, paths)
    generator, ark_calls = _api_generator_flags(state, paths)
    provider = _password_grant(origin, paths["secrets"], "tenant-a", "oidc_tenant_a")
    client = _password_grant(origin, paths["secrets"], "invitee", "oidc_invitee")
    shared_match = 1 if _shared_fingerprint().hex() == str(state.get("shared_before") or "") else 0
    payload = {
        "ark_calls": ark_calls,
        "client_login_ready": 1 if client else 0,
        "f1_head": head,
        "generator": generator,
        "mock_data": 0,
        "provider_login_ready": 1 if provider else 0,
        "ready": ready,
        "shared_match": shared_match,
        "workflow_seeded": seeded,
    }
    if set(payload) != STATUS_KEYS:
        raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_STATUS_INVALID")
    expected = {
        "ark_calls": 0,
        "client_login_ready": 1,
        "f1_head": "f1_0024",
        "generator": "evidence_local",
        "mock_data": 0,
        "provider_login_ready": 1,
        "ready": 1,
        "shared_match": 1,
        "workflow_seeded": 1,
    }
    if payload != expected:
        raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_STATUS_INVALID")
    return payload


def run_min_check() -> None:
    identity = _identity()
    paths = _control_paths(identity)
    LC._secure_file(paths["state"], maximum=16384)
    state = json.loads(paths["state"].read_text(encoding="ascii"))
    origin = f"http://127.0.0.1:{int(state['web_port'])}"
    from infra.f1 import local_seed

    provider_role = _session_role(
        origin, paths["secrets"], "tenant-a", "oidc_tenant_a", str(local_seed.ENTERPRISE_A)
    )
    client_role = _session_role(
        origin, paths["secrets"], "invitee", "oidc_invitee", str(local_seed.ENTERPRISE_B)
    )
    if provider_role != "provider_admin" or client_role != "client_user":
        raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_MIN_CHECK_FAILED")


def run_stop() -> dict[str, int]:
    identity = _identity()
    paths = _control_paths(identity)
    project = str(identity["compose_project"])
    state = None
    if paths["state"].exists():
        LC._secure_file(paths["state"], maximum=16384)
        state = json.loads(paths["state"].read_text(encoding="ascii"))
        project = str(state["compose_project"])
    if state is not None:
        _compose(
            state,
            paths,
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "30",
            timeout=180,
        )
        leftovers = _inventory(project, paths)
        # The amd64 no-OCR candidate is intentionally allowed to coexist with
        # a stopped historical demo as a rollback target. Its project-specific
        # inventory remains the cleanup authority; the default ARM64 path keeps
        # the original global scope exclusivity check.
        scoped = (0, 0, 0) if _ocr_disabled() else _scope_counts(paths)
        if leftovers != (0, 0, 0) or scoped != (0, 0, 0):
            raise DemoError(
                f"LOCAL_ANALYSIS_REPORT_DEMO_RESIDUAL C={leftovers[0]} V={leftovers[1]} N={leftovers[2]}"
            )
    if paths["control_dir"].exists():
        shutil.rmtree(paths["control_dir"])
    return {"dedicated_c": 0, "dedicated_v": 0, "dedicated_n": 0}


def run_rehearsal() -> dict[str, object]:
    last: BaseException | None = None
    for _ in (1, 2):
        shared = _shared_fingerprint()
        try:
            started = run_start()
            status = run_status()
            run_min_check()
            stopped = run_stop()
            if _shared_fingerprint() != shared:
                raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_SHARED_DRIFT")
            if Path(str(_identity()["control_dir"])).exists():
                raise DemoError("LOCAL_ANALYSIS_REPORT_DEMO_CONTROL_RESIDUAL")
            return {**status, **stopped, "url_present": 1 if started.get("url") else 0}
        except (DemoError, UatError) as error:
            last = error
            try:
                run_stop()
            except Exception:
                pass
    assert last is not None
    raise last
