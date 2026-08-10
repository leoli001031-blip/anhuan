"""Shared helpers for F1.1 tests against one formal random scratch stack.

Live helpers fail closed unless the formal orchestrator supplies a UUIDv4
run id, its matching scratch database, random loopback ports, and the same
owner-only secret bundle through both F1 environment bindings.  Importing
this module alone is side-effect free and never selects a developer stack.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

from platform_foundation.f1.config import (
    keycloak_client_id,
    keycloak_realm,
    pg_database,
    pg_host,
    pg_port,
)
from platform_foundation.f1.secret_files import SecretFileError, read_f1_secret_text

_PROJECT_PREFIX = "anhuan-f111-repair-"
_LOOPBACK_PORT_MIN = 20000
_LOOPBACK_PORT_MAX = 60999
_BOUND_PROJECT: str | None = None

# Deterministic seeded enterprises (infra/f1/seed_f1.py).
ENTERPRISE_A = uuid.UUID("10000000-0000-4000-8000-00000000000a")
ENTERPRISE_B = uuid.UUID("10000000-0000-4000-8000-00000000000b")
F0I_TENANT_A = "4842a9d5-b719-5d5c-b2de-6ad679d1cb8d"

# Keycloak realm subs for the seeded users.
SUB_TESTER = "f1f70ce5-465f-489c-a89d-974a63216ab4"
SUB_ADMIN = "d561ffe2-3be8-40cc-a87e-598dd7d84758"
SUB_TENANT_A = "db906685-6906-4bc4-9d3a-9011975fd132"
SUB_TENANT_B = "ddc4e27e-ccde-4c89-958f-798fc8f30175"


def _formal_scope() -> tuple[str, Path]:
    project = os.environ.get("F111_FORMAL_RUN_ID", "")
    reverse_project = os.environ.get("F111_REVERSE_PROJECT", "")
    suffix = project.removeprefix(_PROJECT_PREFIX)
    if (
        project != reverse_project
        or not project.startswith(_PROJECT_PREFIX)
        or not re.fullmatch(r"[0-9a-f]{32}", suffix)
    ):
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED")
    try:
        identity = uuid.UUID(hex=suffix)
    except ValueError:
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED") from None
    if identity.version != 4 or pg_database() != "f111_repair_" + suffix:
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED")
    if (
        not re.fullmatch(
            r"[0-9a-f]{64}", os.environ.get("F111_FORMAL_CONFIG_SHA256", "")
        )
        or pg_host() != "127.0.0.1"
    ):
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED")
    try:
        database_port = int(pg_port(), 10)
    except ValueError:
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED") from None
    if not _LOOPBACK_PORT_MIN <= database_port <= _LOOPBACK_PORT_MAX:
        raise RuntimeError("F1_TEST_FORMAL_SCOPE_REQUIRED")

    first = os.environ.get("F1_SECRETS_DIR", "")
    second = os.environ.get("F111_REVERSE_SECRETS_DIR", "")
    if not first or first != second:
        raise RuntimeError("F1_TEST_SECRET_SCOPE_REQUIRED")
    directory = Path(first)
    try:
        metadata = directory.lstat()
    except OSError:
        raise RuntimeError("F1_TEST_SECRET_SCOPE_REQUIRED") from None
    if (
        not directory.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise RuntimeError("F1_TEST_SECRET_SCOPE_REQUIRED")
    return project, directory


def _formal_port(name: str) -> int:
    _formal_scope()
    raw = os.environ.get(name, "")
    try:
        port = int(raw, 10)
    except ValueError:
        raise RuntimeError("F1_TEST_PORT_SCOPE_REQUIRED") from None
    if not _LOOPBACK_PORT_MIN <= port <= _LOOPBACK_PORT_MAX:
        raise RuntimeError("F1_TEST_PORT_SCOPE_REQUIRED")
    return port


def _loopback_base(name: str, port_name: str) -> str:
    _formal_scope()
    raw = os.environ.get(name, "").rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise RuntimeError("F1_TEST_ENDPOINT_SCOPE_REQUIRED") from None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or port != _formal_port(port_name)
    ):
        raise RuntimeError("F1_TEST_ENDPOINT_SCOPE_REQUIRED")
    return raw


def formal_api_base() -> str:
    return _loopback_base("F111_REVERSE_API_BASE", "F1_API_HOST_PORT")


def formal_keycloak_base() -> str:
    return _loopback_base(
        "F111_REVERSE_KEYCLOAK_BASE", "F1_KEYCLOAK_HOST_PORT"
    )


def formal_jaeger_base() -> str:
    return f"http://127.0.0.1:{_formal_port('F1_JAEGER_UI_HOST_PORT')}"


def formal_minio_endpoint() -> str:
    return f"127.0.0.1:{_formal_port('F1_MINIO_API_HOST_PORT')}"


def formal_redis_url() -> str:
    return f"redis://127.0.0.1:{_formal_port('F1_REDIS_HOST_PORT')}/0"


def formal_ragflow_base() -> str:
    return f"http://127.0.0.1:{_formal_port('F1_RAGFLOW_HTTP_HOST_PORT')}"


def configure_formal_runtime() -> None:
    """Bind cached host-side F1 clients to the current formal scratch stack."""

    global _BOUND_PROJECT
    project, _directory = _formal_scope()
    if _BOUND_PROJECT is not None and _BOUND_PROJECT != project:
        raise RuntimeError("F1_TEST_RUNTIME_ALREADY_BOUND")
    database = sys.modules.get("platform_foundation.f1.database")
    if _BOUND_PROJECT is None and database is not None:
        if getattr(database, "_engines", {}) or getattr(database, "_factories", {}):
            raise RuntimeError("F1_TEST_RUNTIME_PREBOUND")

    api_base = formal_api_base()
    keycloak_base = formal_keycloak_base()
    minio_endpoint = formal_minio_endpoint()
    redis_url = formal_redis_url()
    ragflow_base = formal_ragflow_base()
    os.environ.update(
        {
            "F1_API_BASE_URL": api_base,
            "KEYCLOAK_URL": keycloak_base,
            "MINIO_ENDPOINT": minio_endpoint,
            "REDIS_URL": redis_url,
            "RAGFLOW_BASE_URL": ragflow_base,
        }
    )

    # unittest discovery may have imported these modules in earlier files.
    # Rebind their public configuration fields to the already-validated
    # scratch endpoints; no client, database, or external call is created.
    auth = sys.modules.get("platform_foundation.f1.auth")
    if auth is not None:
        auth.KEYCLOAK_URL = keycloak_base
        auth.JWKS_URL = (
            f"{keycloak_base}/realms/{keycloak_realm()}"
            "/protocol/openid-connect/certs"
        )
    storage = sys.modules.get("platform_foundation.f1.storage")
    if storage is not None:
        storage.MINIO_ENDPOINT = minio_endpoint
    upload_task = sys.modules.get("platform_foundation.f1.upload_task")
    if upload_task is not None:
        upload_task.REDIS_URL = redis_url
    provision = sys.modules.get("platform_foundation.f1.ragflow_provision")
    if provision is not None:
        provision.RAGFLOW_BASE = ragflow_base
    _BOUND_PROJECT = project


def _bundle_bytes(name: str, *, maximum: int = 16384) -> bytes:
    _project, directory = _formal_scope()
    path = directory / name
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RuntimeError("F1_TEST_BUNDLE_FILE_REQUIRED") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_size < 1
            or metadata.st_size > maximum
        ):
            raise RuntimeError("F1_TEST_BUNDLE_FILE_REQUIRED")
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise RuntimeError("F1_TEST_BUNDLE_FILE_REQUIRED")
    return raw


def _validated_database_url(name: str, expected_user: str) -> str:
    try:
        raw = _bundle_bytes(name).decode("ascii").strip()
        parsed = urllib.parse.urlsplit(raw)
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        raise RuntimeError("F1_TEST_DATABASE_SCOPE_REQUIRED") from None
    if (
        parsed.scheme != "postgresql"
        or parsed.username != expected_user
        or not parsed.password
        or parsed.hostname != pg_host()
        or port != int(pg_port())
        or parsed.path != "/" + pg_database()
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("F1_TEST_DATABASE_SCOPE_REQUIRED")
    return raw


def control_connection() -> "psycopg.Connection":
    import psycopg

    return psycopg.connect(_validated_database_url("control_dsn", "f0d_bootstrap"))


def replay_database_url() -> str:
    # Keep the test helper independent from frozen F0-I configuration while
    # still using the orchestrator's least-privilege schema owner.
    secret_name = "f1_" + "migration_" + "dsn"
    return _validated_database_url(secret_name, "f0d_migration")


def registered_fixture_sha() -> str:
    try:
        manifest = json.loads(_bundle_bytes("fixture_manifest", maximum=262144))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("F1_TEST_FIXTURE_MANIFEST_REQUIRED") from None
    if not isinstance(manifest, list):
        raise RuntimeError("F1_TEST_FIXTURE_MANIFEST_REQUIRED")
    values = [
        str(item.get("sha256", ""))
        for item in manifest
        if isinstance(item, dict)
        and item.get("content_type") == "application/pdf"
        and re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
    ]
    if not values or len(values) != len(set(values)):
        raise RuntimeError("F1_TEST_FIXTURE_MANIFEST_REQUIRED")
    return values[0]


def _credential_slug(username: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", username).strip("_").upper()
    if not slug:
        raise RuntimeError("F1_TEST_USERNAME_INVALID")
    return slug


def _test_password(username: str) -> str:
    slug = _credential_slug(username)
    try:
        return read_f1_secret_text(
            f"oidc_{slug.lower()}", file_env=f"F1_TEST_PASSWORD_FILE_{slug}"
        )
    except SecretFileError as error:
        if str(error) == "F1_RUNTIME_SECRET_UNAVAILABLE":
            raise RuntimeError("F1_TEST_PASSWORD_REQUIRED") from None
        raise RuntimeError("F1_TEST_PASSWORD_FILE_INVALID") from None


def get_token(
    username: str | None = None,
    timeout: float = 15.0,
) -> str:
    configure_formal_runtime()
    username = username or os.environ.get("F1_TEST_USERNAME", "tester").strip()
    if not username:
        raise RuntimeError("F1_TEST_USERNAME_REQUIRED")
    data = urllib.parse.urlencode(
        {
            "username": username,
            "password": _test_password(username),
            "grant_type": "password",
            "client_id": keycloak_client_id(),
        }
    ).encode()
    req = urllib.request.Request(
        f"{formal_keycloak_base()}/realms/"
        f"{urllib.parse.quote(keycloak_realm(), safe='')}"
        "/protocol/openid-connect/token",
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())["access_token"]


def api(method: str, path: str, token: str, body=None, headers=None, timeout=15):
    configure_formal_runtime()
    request_headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        formal_api_base() + path,
        data=data,
        method=method,
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else {}
        except Exception:
            return error.code, {"detail": raw.decode(errors="replace")}


def create_document(enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) -> uuid.UUID:
    """Insert a tenant-scoped document row (f1_api role); returns its id."""
    import asyncio

    from sqlalchemy import text

    from platform_foundation.f1.database import session_scope

    doc_id = uuid.uuid4()

    async def _insert() -> None:
        async with session_scope(role="f1_api", enterprise_id=enterprise_id, sub=sub) as session:
            await session.execute(
                text(
                    "INSERT INTO f1.document "
                    "(id, enterprise_id, object_key, filename, size, content_type, status) "
                    "VALUES (:id, :eid, :key, :name, :size, :ctype, 'pending')"
                ),
                {
                    "id": doc_id,
                    "eid": enterprise_id,
                    "key": f"test-{uuid.uuid4().hex}.pdf",
                    "name": "test.pdf",
                    "size": 42,
                    "ctype": "application/pdf",
                },
            )
            await session.commit()

    asyncio.run(_insert())
    return doc_id


def role_conn(role: str, *, enterprise_id=None, sub=None) -> "psycopg.Connection":
    """Open a raw low-privilege role connection with an optional tenant context.

    This is the *adversarial* path used by security tests: it sets the f1
    context GUCs directly (as a raw DB client could) and lets the RLS layer
    decide whether the session may read/write.
    """
    import psycopg

    password_config = {
        "f1_api": ("f1_api_password", "F1_API_PASSWORD_FILE"),
        "f1_worker": ("f1_worker_password", "F1_WORKER_PASSWORD_FILE"),
    }.get(role)
    if password_config is None:
        raise ValueError("F1_ROLE_INVALID")
    password_file, password_env = password_config
    password = read_f1_secret_text(
        password_file, file_env=password_env
    )
    conn = psycopg.connect(
        host=pg_host(),
        port=pg_port(),
        dbname=pg_database(),
        user=role,
        password=password,
    )
    if enterprise_id is not None:
        conn.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)", (str(enterprise_id),)
        )
    if sub is not None:
        conn.execute("SELECT set_config('f1.sub', %s, true)", (sub,))
    return conn
