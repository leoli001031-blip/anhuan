"""Dedicated analysis-report dual-identity browser UAT.

Does not impersonate business or material-rag UAT. Default compose is unchanged.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/f1/docker-compose.local.yml"
OVERLAY_FILE = ROOT / "infra/f1/docker-compose.analysis-report-uat.yml"
SCOPE = "analysis-report-uat"
CONTROL_SCHEMA = "anhuan-analysis-report-uat-control-v1"
WORKSPACE_SHA256 = hashlib.sha256(
    f"{CONTROL_SCHEMA}\0{ROOT}\0{os.geteuid()}".encode("utf-8")
).hexdigest()
PROBE = WORKSPACE_SHA256[:12]
PROJECT_RE = re.compile(r"^anhuan-ar-uat-[0-9a-f]{12}\Z")
OK_TAG = "LOCAL_ANALYSIS_REPORT_DUAL_IDENTITY_BROWSER_OK"
SUMMARY_KEYS = frozenset(
    {
        "ark_calls",
        "cdp_request_id_bound",
        "client_console_denied",
        "client_legacy_tree_denied",
        "client_portal",
        "client_session_enterprise_b",
        "mock_data",
        "provider_console",
        "provider_legacy_tree",
        "provider_portal_denied",
        "provider_session_enterprise_a",
        "provider_storage_revalidated",
        "stage",
    }
)


class UatError(RuntimeError):
    pass


def _localctl():
    loader = importlib.machinery.SourceFileLoader(
        "analysis_report_uat_localctl", str(ROOT / "scripts/localctl")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_LOCALCTL")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


def _load_fixture():
    path = ROOT / "infra/f1/analysis-reports/local_browser_fixture.py"
    loader = importlib.machinery.SourceFileLoader(
        "analysis_report_uat_fixture", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_FIXTURE")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


LC = _localctl()


def _identity() -> dict[str, object]:
    project_id = uuid.uuid5(
        uuid.NAMESPACE_URL, f"{CONTROL_SCHEMA}:{WORKSPACE_SHA256}"
    ).hex
    return {
        "schema": CONTROL_SCHEMA,
        "project_id": project_id,
        "compose_project": f"anhuan-ar-uat-{PROBE}",
        "pgint_project_name": f"anhuan-ar-pgint-{PROBE}",
        "database": f"f1_arpg_{PROBE}",
        "runtime_image": f"anhuan-ar-uat-runtime:{PROBE}",
        "web_image": f"anhuan-ar-uat-web:{PROBE}",
        "control_dir": f"/private/tmp/anhuan-ar-pgint-{PROBE}",
    }


def _control_paths(state: dict[str, object]) -> dict[str, Path]:
    control_dir = Path(str(state["control_dir"]))
    return {
        "control_dir": control_dir,
        "secrets": control_dir / "secrets",
        "home": control_dir / "home",
        "tmp": control_dir / "tmp",
        "env": control_dir / "compose.env",
        "state": control_dir / "state.json",
        "lock": control_dir / "command.lock",
        "receipt": control_dir / "identity.receipt",
    }


def _environment(paths: dict[str, Path]) -> dict[str, str]:
    return {
        "DOCKER_CONFIG": str(paths["home"] / ".docker"),
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "HOME": str(paths["home"]),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_PROXY": "127.0.0.1,localhost,postgres,keycloak,minio,redis,clamd,api,web",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(paths["tmp"]),
        "no_proxy": "127.0.0.1,localhost,postgres,keycloak,minio,redis,clamd,api,web",
    }


def _run(
    arguments: list[str],
    *,
    paths: dict[str, Path],
    timeout: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            cwd=ROOT,
            env=_environment(paths),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_COMMAND_TIMEOUT") from error
    if check and result.returncode != 0:
        raise UatError(_command_failure(arguments, result))
    return result


def _command_failure(
    arguments: list[str], result: subprocess.CompletedProcess[str]
) -> str:
    blob = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
    token = "OTHER"
    for needle, name in (
        ("local_seed_migration_required", "SEED_HEAD"),
        ("didn't complete successfully", "SERVICE_UNHEALTHY"),
        ("failed to solve", "BUILD_FAILED"),
        ("bind: address already in use", "PORT_IN_USE"),
        ("cannot connect to the docker daemon", "DOCKER_DAEMON"),
        ("no such image", "IMAGE_MISSING"),
        ("permission denied", "PERMISSION"),
        ("error while interpolating", "INTERPOLATE"),
        ("unknown flag", "COMPOSE_FLAG"),
    ):
        if needle in blob:
            token = name
            break
    verb = "cmd"
    if "compose" in arguments:
        for item in arguments[arguments.index("compose") + 1 :]:
            if item in {"run", "build", "up", "down"}:
                verb = item
                break
            if item == "seed":
                verb = "seed"
                break
            if item == "migrator":
                verb = "migrator"
                break
            if item == "keycloak-provisioner":
                verb = "provisioner"
                break
    return f"LOCAL_ANALYSIS_REPORT_UAT_COMMAND_FAILED:{verb}:{token}:{result.returncode}"


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
        "--profile",
        "ops",
        *arguments,
    ]
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
            raise UatError("LOCAL_ANALYSIS_REPORT_UAT_INVENTORY_FAILED")
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
            raise UatError("LOCAL_ANALYSIS_REPORT_UAT_INVENTORY_FAILED")
        return len([line for line in result.stdout.splitlines() if line])

    return (
        count(["ps", "-a", "--format", "{{.ID}}"]),
        count(["volume", "ls", "--format", "{{.Name}}"]),
        count(["network", "ls", "--format", "{{.ID}}"]),
    )


def _shared_fingerprint() -> bytes:
    from infra.f1.analysis_report_postgres_integration import (
        canonical_shared_fingerprint,
    )

    return canonical_shared_fingerprint()


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
            raise UatError("LOCAL_ANALYSIS_REPORT_UAT_STATE_INVALID")
        state = {**state, "web_port": int(existing["web_port"]), "pg_port": int(existing["pg_port"])}
        paths = _control_paths(state)
    else:
        LC._exclusive_write(paths["state"], payload)
    _write_docker_config(paths)
    _write_secrets(state, paths)
    LC._validate_secret_set(paths["secrets"])
    _write_env(state, paths)
    _write_receipt(state, paths)
    return state, paths


def _write_docker_config(paths: dict[str, Path]) -> None:
    docker_dir = paths["home"] / ".docker"
    if not docker_dir.exists():
        docker_dir.mkdir(mode=0o700)
    LC._directory(docker_dir)
    plugin_dir = LC._docker_plugin_directory()
    payload: dict[str, object] = {}
    if plugin_dir is not None:
        payload["cliPluginsExtraDirs"] = [plugin_dir]
    data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "ascii"
    )
    path = docker_dir / "config.json"
    if path.exists():
        LC._secure_file(path, maximum=4096)
    else:
        LC._exclusive_write(path, data)


def _write_secrets(state: dict[str, object], paths: dict[str, Path]) -> None:
    existing = {
        name for name in LC.ALL_SECRET_NAMES if (paths["secrets"] / name).exists()
    }
    if existing:
        if existing != set(LC.ALL_SECRET_NAMES):
            raise UatError("LOCAL_ANALYSIS_REPORT_UAT_SECRET_SET_INCOMPLETE")
        return
    import secrets as secrets_mod

    passwords = {
        name: LC._secret_value()
        for name in LC.TEXT_SECRET_NAMES
        if name
        not in {
            "f0d_migration_dsn",
            "f1_bootstrap_dsn",
            "f1_migration_dsn",
            "minio_root_user",
            "f1_qa_key",
        }
    }
    passwords["minio_root_user"] = "local" + secrets_mod.token_hex(12)
    passwords["f1_qa_key"] = secrets_mod.token_hex(32)
    database = str(state["database"])
    bootstrap_password = quote(passwords["f0d_bootstrap_password"], safe="")
    migration_password = quote(passwords["f0d_migration_password"], safe="")
    passwords["f0d_migration_dsn"] = (
        f"postgresql://f0d_migration:{migration_password}@postgres:5432/{database}"
    )
    passwords["f1_bootstrap_dsn"] = (
        f"postgresql://f0d_bootstrap:{bootstrap_password}@postgres:5432/{database}"
    )
    passwords["f1_migration_dsn"] = passwords["f0d_migration_dsn"]
    for name in LC.TEXT_SECRET_NAMES:
        path = paths["secrets"] / name
        if not path.exists():
            LC._exclusive_write(path, passwords[name].encode("ascii"))
    f0i_path = paths["secrets"] / "f0i_key"
    if not f0i_path.exists():
        LC._exclusive_write(f0i_path, secrets_mod.token_bytes(32))


def _write_env(state: dict[str, object], paths: dict[str, Path]) -> None:
    origin = f"http://127.0.0.1:{int(state['web_port'])}"
    values = {
        "LOCAL_DATABASE": state["database"],
        "LOCAL_GID": os.getegid(),
        "LOCAL_PG_PORT": state["pg_port"],
        "LOCAL_PROJECT_ID": state["project_id"],
        "LOCAL_RUNTIME_IMAGE": state["runtime_image"],
        "LOCAL_SECRETS_DIR": str(paths["secrets"]),
        "LOCAL_UID": os.geteuid(),
        "LOCAL_WEB_IMAGE": state["web_image"],
        "LOCAL_WEB_ORIGIN": origin,
        "LOCAL_WEB_PORT": state["web_port"],
    }
    data = "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode("utf-8")
    if paths["env"].exists():
        LC._atomic_replace(paths["env"], data)
    else:
        LC._exclusive_write(paths["env"], data)


def _write_receipt(state: dict[str, object], paths: dict[str, Path]) -> None:
    body = (
        "\n".join(
            (
                f"LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID={state['project_id']}",
                f"LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME={state['pgint_project_name']}",
                f"LOCAL_ANALYSIS_REPORT_PGINT_DATABASE={state['database']}",
            )
        )
        + "\n"
    )
    if paths["receipt"].exists():
        LC._atomic_replace(paths["receipt"], body.encode("ascii"))
    else:
        LC._exclusive_write(paths["receipt"], body.encode("ascii"))


def _rewrite_host_bootstrap_dsn(state: dict[str, object], paths: dict[str, Path]) -> None:
    password = (paths["secrets"] / "f0d_bootstrap_password").read_text(encoding="ascii").strip()
    database = str(state["database"])
    port = int(state["pg_port"])
    encoded = quote(password, safe="")
    dsn = f"postgresql://f0d_bootstrap:{encoded}@127.0.0.1:{port}/{database}\n"
    LC._atomic_replace(paths["secrets"] / "f1_bootstrap_dsn", dsn.encode("ascii"))


def _start(state: dict[str, object], paths: dict[str, Path]) -> None:
    _compose(state, paths, "run", "--rm", "--no-deps", "secret-init", timeout=180)
    _compose(state, paths, "build", "migrator", "web", timeout=1800)
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
        "api",
        "worker",
        "dispatcher",
        "web",
        timeout=240,
    )


def _pg_env(state: dict[str, object], paths: dict[str, Path]) -> dict[str, str]:
    return {
        "F1_LOCAL_ENGINEERING": "1",
        "F1_MATERIAL_ANALYSIS_REPORT_LOCAL": "1",
        "F1_PG_HOST": "127.0.0.1",
        "F1_PG_PORT": str(state["pg_port"]),
        "F1_PG_DATABASE": str(state["database"]),
        "F1_SECRETS_DIR": str(paths["secrets"]),
        "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID": str(state["project_id"]),
        "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME": str(state["pgint_project_name"]),
        "LOCAL_ANALYSIS_REPORT_PGINT_DATABASE": str(state["database"]),
        "LOCAL_ANALYSIS_REPORT_PGINT_CONTROL_DIR": str(paths["control_dir"]),
    }


def _seed_identities(state: dict[str, object], paths: dict[str, Path]) -> None:
    # local_seed.main() is frozen at f1_0014. This UAT migrator stops at
    # f1_0017, so the same ensure_* helpers run on the host with that head.
    from infra.f1 import local_seed
    from infra.f1.migrate_f1 import _bootstrap_dsn

    import psycopg

    _rewrite_host_bootstrap_dsn(state, paths)
    original = dict(os.environ)
    try:
        os.environ.update(_pg_env(state, paths))
        with psycopg.connect(_bootstrap_dsn(), autocommit=False, connect_timeout=5) as connection:
            head = connection.execute(
                "SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f1.alembic_version"
            ).fetchone()
            if head is None or head[0] != "f1_0017":
                raise UatError("LOCAL_ANALYSIS_REPORT_UAT_SEED_HEAD_MISMATCH")
            local_seed._ensure_enterprise(
                connection, local_seed.ENTERPRISE_A, "Local Enterprise A", "LOCAL-A"
            )
            local_seed._ensure_enterprise(
                connection, local_seed.ENTERPRISE_B, "Local Enterprise B", "LOCAL-B"
            )
            for binding in local_seed.BINDINGS:
                local_seed._ensure_binding(connection, binding)
            local_seed._ensure_durability_canary(connection)
            connection.commit()
    except UatError:
        raise
    except Exception as error:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_SEED_FAILED") from error
    finally:
        os.environ.clear()
        os.environ.update(original)


def _apply_fixture(state: dict[str, object], paths: dict[str, Path]) -> None:
    _rewrite_host_bootstrap_dsn(state, paths)
    original = dict(os.environ)
    try:
        os.environ.update(_pg_env(state, paths))
        _load_fixture().apply()
    except Exception as error:
        raise UatError(
            str(error) if str(error).startswith("LOCAL_") else "LOCAL_ANALYSIS_REPORT_UAT_FIXTURE_FAILED"
        ) from error
    finally:
        os.environ.clear()
        os.environ.update(original)


def _browser(state: dict[str, object], paths: dict[str, Path]) -> dict[str, object]:
    origin = f"http://127.0.0.1:{int(state['web_port'])}"
    command = [
        LC._node(),
        str(LC.PWA_BROWSER_RUNNER),
        origin,
        str(paths["secrets"]),
        "--stage",
        "analysis-report-uat",
    ]
    result = _run(command, paths=paths, timeout=420, check=False)
    if result.returncode == 0 and result.stderr and result.stderr.strip():
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_BROWSER_STDERR")
    lines = result.stdout.splitlines() if result.stdout else []
    if result.returncode != 0 or len(lines) < 2 or lines[-1] != OK_TAG:
        detail = "NO_TAG"
        for line in (result.stderr.splitlines() if result.stderr else []):
            if line.startswith("LOCAL_BROWSER_VERIFY_FAILED "):
                detail = line
        raise UatError(f"LOCAL_ANALYSIS_REPORT_UAT_BROWSER_FAILED {detail}")
    try:
        summary = json.loads(lines[-2])
    except json.JSONDecodeError as error:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_BROWSER_JSON_INVALID") from error
    if not isinstance(summary, dict) or set(summary) != SUMMARY_KEYS:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_BROWSER_SUMMARY_INVALID")
    expected = {
        "ark_calls": 0,
        "cdp_request_id_bound": 1,
        "client_console_denied": 1,
        "client_legacy_tree_denied": 1,
        "client_portal": 1,
        "client_session_enterprise_b": 1,
        "mock_data": 0,
        "provider_console": 1,
        "provider_legacy_tree": 1,
        "provider_portal_denied": 1,
        "provider_session_enterprise_a": 1,
        "provider_storage_revalidated": 1,
        "stage": "analysis-report-uat",
    }
    if summary != expected:
        raise UatError("LOCAL_ANALYSIS_REPORT_UAT_BROWSER_SUMMARY_INVALID")
    return summary


def _stop(state: dict[str, object] | None, paths: dict[str, Path] | None) -> None:
    project = f"anhuan-ar-uat-{PROBE}"
    if state is not None and paths is not None:
        project = str(state["compose_project"])
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
        scoped = _scope_counts(paths)
        if leftovers != (0, 0, 0) or scoped != (0, 0, 0):
            raise UatError(
                f"LOCAL_ANALYSIS_REPORT_UAT_RESIDUAL C={leftovers[0]} V={leftovers[1]} N={leftovers[2]}"
            )
        shutil.rmtree(paths["control_dir"])


def _live_once() -> dict[str, object]:
    shared = _shared_fingerprint()
    state = None
    paths = None
    try:
        state, paths = _initialize()
        _start(state, paths)
        _apply_fixture(state, paths)
        summary = _browser(state, paths)
        _stop(state, paths)
        paths = None
        if _shared_fingerprint() != shared:
            raise UatError("LOCAL_ANALYSIS_REPORT_UAT_SHARED_DRIFT")
        return summary
    except Exception as error:
        if state is not None and paths is not None:
            try:
                _stop(state, paths)
            except Exception:
                pass
        if isinstance(error, LC.LocalError):
            raise UatError(str(error) or "LOCAL_ANALYSIS_REPORT_UAT_LOCALCTL") from error
        raise


def run_check() -> dict[str, object]:
    last: BaseException | None = None
    for _ in (1, 2):
        try:
            return _live_once()
        except UatError as error:
            last = error
    assert last is not None
    raise last


WORKFLOW_OK_TAG = "LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_OK"
WORKFLOW_SUMMARY_KEYS = frozenset(
    {
        "approve",
        "ark_calls",
        "cdp_request_id_bound",
        "citation_count",
        "client_detail",
        "client_list",
        "create_idempotent",
        "dedicated_c",
        "dedicated_n",
        "dedicated_v",
        "generation_draft",
        "generation_idempotent",
        "hidden_after_withdraw",
        "mock_data",
        "provider_create",
        "publish",
        "section_count",
        "shared_match",
        "skipped",
        "submit",
        "unbound_visible",
        "withdraw",
    }
)
WORKFLOW_BROWSER_KEYS = WORKFLOW_SUMMARY_KEYS - {
    "dedicated_c",
    "dedicated_n",
    "dedicated_v",
    "shared_match",
}


def _workflow_browser(state: dict[str, object], paths: dict[str, Path]) -> dict[str, object]:
    origin = f"http://127.0.0.1:{int(state['web_port'])}"
    command = [
        LC._node(),
        str(LC.PWA_BROWSER_RUNNER),
        origin,
        str(paths["secrets"]),
        "--stage",
        "analysis-report-workflow",
    ]
    result = _run(command, paths=paths, timeout=900, check=False)
    if result.returncode == 0 and result.stderr and result.stderr.strip():
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_STDERR")
    lines = result.stdout.splitlines() if result.stdout else []
    if result.returncode != 0 or len(lines) < 2 or lines[-1] != WORKFLOW_OK_TAG:
        detail = "NO_TAG"
        for line in result.stderr.splitlines() if result.stderr else []:
            if line.startswith("LOCAL_BROWSER_VERIFY_FAILED "):
                detail = line
        raise UatError(f"LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_FAILED {detail}")
    try:
        summary = json.loads(lines[-2])
    except json.JSONDecodeError as error:
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_JSON_INVALID") from error
    if not isinstance(summary, dict) or set(summary) != WORKFLOW_BROWSER_KEYS:
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_SUMMARY_INVALID")
    return summary


def _workflow_supervisor(
    state: dict[str, object], paths: dict[str, Path], summary: dict[str, object]
) -> None:
    fixture = _load_fixture()
    _rewrite_host_bootstrap_dsn(state, paths)
    original = dict(os.environ)
    try:
        os.environ.update(_pg_env(state, paths))
        import psycopg

        from infra.f1.migrate_f1 import _bootstrap_dsn

        with psycopg.connect(_bootstrap_dsn(), autocommit=True, connect_timeout=5) as connection:
            reports = connection.execute(
                "SELECT count(*) FROM f1.analysis_report WHERE client_account_id=%s",
                (fixture.CRM_ACCOUNT_ID,),
            ).fetchone()
            versions = connection.execute(
                "SELECT count(*) FROM f1.analysis_report_version AS version "
                "JOIN f1.analysis_report AS report ON report.id = version.report_id "
                "WHERE report.client_account_id=%s",
                (fixture.CRM_ACCOUNT_ID,),
            ).fetchone()
        if reports is None or versions is None or int(reports[0]) != 1 or int(versions[0]) != 1:
            raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_SUPERVISOR_COUNT")
        summary["create_idempotent"] = 1
        summary["generation_idempotent"] = 1
    except UatError:
        raise
    except Exception as error:
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_SUPERVISOR_FAILED") from error
    finally:
        os.environ.clear()
        os.environ.update(original)


def _workflow_expected(summary: dict[str, object]) -> None:
    citation = summary.get("citation_count")
    if not isinstance(citation, int) or citation < 2:
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_SUMMARY_INVALID")
    expected = {
        "approve": 1,
        "ark_calls": 0,
        "cdp_request_id_bound": 1,
        "client_detail": 1,
        "client_list": 1,
        "create_idempotent": 1,
        "dedicated_c": 0,
        "dedicated_n": 0,
        "dedicated_v": 0,
        "generation_draft": 1,
        "generation_idempotent": 1,
        "hidden_after_withdraw": 1,
        "mock_data": 0,
        "provider_create": 1,
        "publish": 1,
        "section_count": 7,
        "shared_match": 1,
        "skipped": 0,
        "submit": 1,
        "unbound_visible": 0,
        "withdraw": 1,
        "citation_count": citation,
    }
    if summary != expected:
        raise UatError("LOCAL_ANALYSIS_REPORT_WORKFLOW_BROWSER_SUMMARY_INVALID")


def _workflow_live_once() -> dict[str, object]:
    shared = _shared_fingerprint()
    state = None
    paths = None
    try:
        state, paths = _initialize()
        _start(state, paths)
        _apply_fixture(state, paths)
        summary = _workflow_browser(state, paths)
        _workflow_supervisor(state, paths, summary)
        _stop(state, paths)
        paths = None
        shared_match = 1 if _shared_fingerprint() == shared else 0
        summary = {
            **summary,
            "dedicated_c": 0,
            "dedicated_v": 0,
            "dedicated_n": 0,
            "shared_match": shared_match,
        }
        _workflow_expected(summary)
        return summary
    except Exception as error:
        if state is not None and paths is not None:
            try:
                _stop(state, paths)
            except Exception:
                pass
        if isinstance(error, LC.LocalError):
            raise UatError(str(error) or "LOCAL_ANALYSIS_REPORT_WORKFLOW_LOCALCTL") from error
        raise


def run_workflow_check() -> dict[str, object]:
    last: BaseException | None = None
    for _ in (1, 2):
        try:
            return _workflow_live_once()
        except UatError as error:
            last = error
    assert last is not None
    raise last
