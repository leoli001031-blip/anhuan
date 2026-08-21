"""Fresh RAGFlow account, API token and Ark embedding-only provisioner.

Runs inside the exact pinned RAGFlow image.  Account and token bootstrap use
the v0.26.4 public APIs.  Provider configuration uses that same release's
official Peewee models under one connection, tenant-scoped database lock and
atomic transaction.  This deliberately avoids the public instance endpoint:
that endpoint sends a synthetic connectivity sentence which is outside the
four-Demo authorization.  The Ark credential is held only by this one-shot
process and is never printed or copied into the control volume.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.cookiejar
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA


BASE_URL = os.environ.get("RAGFLOW_BASE_URL", "").rstrip("/")
EMAIL = os.environ.get("F1_MATERIAL_RAG_BOOTSTRAP_EMAIL", "")
PASSWORD_FILE = Path(
    os.environ.get("F1_MATERIAL_RAG_BOOTSTRAP_PASSWORD_FILE", "")
)
ARK_KEY_FILE = Path(os.environ.get("F1_ARK_API_KEY_FILE", ""))
TOKEN_FILE = Path(os.environ.get("F1_RAGFLOW_API_KEY_FILE", ""))
ATTESTATION_FILE = Path(
    os.environ.get("F1_MATERIAL_RAG_PROVIDER_ATTESTATION_FILE", "")
)
CONTROL_DIRECTORY = Path(os.environ.get("F1_MATERIAL_RAG_CONTROL_DIR", ""))
PUBLIC_KEY_FILE = Path("/ragflow/conf/public.pem")
RAGFLOW_ROOT = Path("/ragflow")
RUNTIME_CONFIG_TEMPLATE = Path("/ragflow/conf/service_conf.yaml.template")
RUNTIME_CONFIG_FILE = Path("/ragflow/conf/service_conf.yaml")
VERSION_FILE = Path("/ragflow/VERSION")
NICKNAME = "material-rag-verifier"
PROVIDER = "VolcEngine"
INSTANCE = "material-rag-ark"
MODEL = "doubao-embedding-vision"
MODEL_TYPE = "embedding"
MODEL_MAX_TOKENS = 8192
EXPECTED_RAGFLOW_VERSION = "v0.26.4"
EXPECTED_RUNTIME_CONFIG_SHA256 = (
    "90e70113f9ff19291486853543c29c90875aabcf716eba958063726ff9b5c7c7"
)
EXPECTED_RUNTIME_TEMPLATE_SHA256 = (
    "6ba72aaefe9296b1c0707a676a9e28b9909629b6ad3da4bf242a355e04221115"
)
ARK_RELAY_BASE_URL = "http://material-rag-egress-proxy:8080/api/plan/v3"
ARK_UPSTREAM_AUTHORITY = "ark.cn-beijing.volces.com:443"
ARK_UPSTREAM_EMBEDDING_PATH = "/api/plan/v3/embeddings/multimodal"
EXPECTED_RAGFLOW_BASE_URL = "http://material-rag-ragflow:80"
EXPECTED_RAGFLOW_LOOPBACK_URL = "http://127.0.0.1:80"
MAX_RESPONSE_BYTES = 1024 * 1024
TOKEN_RE = re.compile(r"ragflow-[A-Za-z0-9_-]{16,256}\Z")
EMAIL_RE = re.compile(r"[a-z][a-z0-9-]{2,31}@[a-z][a-z0-9.-]{2,63}\Z")
IDENTIFIER_RE = re.compile(r"[0-9a-f]{32}\Z")
INTERNAL_PASSWORD_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
CONFIG_VARIABLE_RE = re.compile(
    r"\$\{([A-Z][A-Z0-9_]*)(?::-(.*?))?\}"
)


class ProvisionError(RuntimeError):
    def __init__(self, reason: str, *, evidence: dict[str, object] | None = None) -> None:
        super().__init__(reason)
        self.evidence = evidence if isinstance(evidence, dict) else None


_COOKIE_JAR: http.cookiejar.CookieJar | None = None
_OPENER: urllib.request.OpenerDirector | None = None


def _reset_http_client() -> None:
    global _COOKIE_JAR, _OPENER
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)
    _COOKIE_JAR = http.cookiejar.CookieJar()
    _OPENER = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(_COOKIE_JAR),
    )


def _http_opener() -> urllib.request.OpenerDirector:
    if _OPENER is None:
        _reset_http_client()
    assert _OPENER is not None
    return _OPENER


PROVIDER_PREFLIGHT_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_PREFLIGHT_FAILED"
PROVIDER_IDENTITY_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_IDENTITY_FAILED"
PROVIDER_READY_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_READY_FAILED"
PROVIDER_UNAVAILABLE_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_UNAVAILABLE_FAILED"
PROVIDER_REQUEST_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED"
PROVIDER_RESPONSE_INVALID = "LOCAL_MATERIAL_RAG_PROVIDER_RESPONSE_INVALID"
PROVIDER_REGISTER_DISABLED = "LOCAL_MATERIAL_RAG_PROVIDER_REGISTER_DISABLED"
READY_ERROR_REASONS = {
    "MATERIAL_RAG_RAGFLOW_UNAVAILABLE": PROVIDER_UNAVAILABLE_FAILED,
    "MATERIAL_RAG_RAGFLOW_REQUEST_FAILED": PROVIDER_REQUEST_FAILED,
    "MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID": PROVIDER_RESPONSE_INVALID,
}
PROVIDER_REGISTER_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_REGISTER_FAILED"
PROVIDER_LOGIN_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_LOGIN_FAILED"
PROVIDER_TOKEN_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_TOKEN_FAILED"
PROVIDER_RUNTIME_CONFIG_FAILED = (
    "LOCAL_MATERIAL_RAG_PROVIDER_RUNTIME_CONFIG_FAILED"
)
PROVIDER_STATE_FAILED = "LOCAL_MATERIAL_RAG_PROVIDER_STATE_FAILED"
PROVIDER_CONTROL_WRITE_FAILED = (
    "LOCAL_MATERIAL_RAG_PROVIDER_CONTROL_WRITE_FAILED"
)
PROVIDER_INTERNAL_ERROR = "LOCAL_MATERIAL_RAG_PROVIDER_INTERNAL_ERROR"
PROVIDER_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE "
_CURL_EXIT_CLASS = {
    0: "OK",
    6: "RESOLVE",
    7: "CONNECT",
    22: "HTTP",
    28: "TIMEOUT",
    52: "EMPTY",
    56: "RECV",
}
_RESPONSE_SIZE_CLASSES = (
    (0, "EMPTY"),
    (1024, "SMALL"),
    (65536, "MEDIUM"),
    (MAX_RESPONSE_BYTES, "LARGE"),
)


def _curl_exit_class(
    code: int | None, *, spawn_failed: bool = False, timed_out: bool = False
) -> str:
    if spawn_failed:
        return "SPAWN"
    if timed_out:
        return "TIMEOUT"
    if not isinstance(code, int) or not 0 <= code <= 255:
        return "NONE" if code is None else "OTHER"
    return _CURL_EXIT_CLASS.get(code, "OTHER")


def _response_size_class(size: int) -> str:
    if not isinstance(size, int) or size < 0:
        return "UNKNOWN"
    for limit, name in _RESPONSE_SIZE_CLASSES:
        if size <= limit:
            return name
    return "OVERSIZE"


def _elapsed_class(seconds: float) -> str:
    if seconds < 5:
        return "LT5S"
    if seconds < 30:
        return "S5_30"
    if seconds < 60:
        return "S30_60"
    if seconds <= 300:
        return "S60_300"
    return "OVER300"


def _ready_evidence(
    *,
    attempt_count: int,
    curl_code: int | None,
    curl_exit_class: str,
    response_size: int,
    elapsed_s: float,
) -> dict[str, object]:
    attempts = attempt_count if isinstance(attempt_count, int) and 1 <= attempt_count <= 300 else 300
    payload: dict[str, object] = {
        "attempt_count": attempts,
        "curl_exit_class": curl_exit_class,
        "elapsed_class": _elapsed_class(elapsed_s),
        "endpoint": "SYSTEM_CONFIG",
        "phase": "READY",
        "response_size_class": _response_size_class(response_size),
    }
    if isinstance(curl_code, int) and 0 <= curl_code <= 255:
        payload["curl_code"] = curl_code
    return payload


def _print_ready_evidence(evidence: dict[str, object] | None) -> None:
    if not isinstance(evidence, dict):
        return
    print(
        PROVIDER_EVIDENCE_PREFIX
        + json.dumps(
            evidence, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ),
        file=sys.stderr,
        flush=True,
    )


def _read_private(path: Path, *, minimum: int, maximum: int) -> bytes:
    if not path.is_absolute():
        raise ProvisionError("MATERIAL_RAG_SECRET_PATH_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ProvisionError("MATERIAL_RAG_SECRET_INVALID") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not minimum <= before.st_size <= maximum
        ):
            raise ProvisionError("MATERIAL_RAG_SECRET_INVALID")
        body = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
    )
    if len(body) != before.st_size or identity(before) != identity(after):
        raise ProvisionError("MATERIAL_RAG_SECRET_CHANGED")
    return body


def _public_key() -> bytes:
    try:
        body = _read_private(PUBLIC_KEY_FILE, minimum=256, maximum=16384)
        key = RSA.import_key(body, passphrase="Welcome")
    except (ProvisionError, ValueError, IndexError, TypeError):
        raise ProvisionError("MATERIAL_RAG_PUBLIC_KEY_INVALID") from None
    if key.has_private() or key.size_in_bits() < 2048:
        raise ProvisionError("MATERIAL_RAG_PUBLIC_KEY_INVALID")
    return body


def _encrypt_password(password: bytes) -> str:
    try:
        key = RSA.import_key(_public_key(), passphrase="Welcome")
        encoded = base64.b64encode(password)
        encrypted = PKCS1_v1_5.new(key).encrypt(encoded)
        return base64.b64encode(encrypted).decode("ascii")
    except (ValueError, IndexError, TypeError):
        raise ProvisionError("MATERIAL_RAG_PASSWORD_ENCRYPT_FAILED") from None


def _verify_runtime_version() -> None:
    try:
        value = _read_private(VERSION_FILE, minimum=1, maximum=128).decode(
            "ascii", "strict"
        )
    except (ProvisionError, UnicodeDecodeError):
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_VERSION_INVALID") from None
    if value.strip() != EXPECTED_RAGFLOW_VERSION:
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_VERSION_INVALID")


def _atomic_runtime_config_replace(body: bytes, mode: int) -> None:
    temporary = RUNTIME_CONFIG_FILE.with_name(".material-rag-service-conf")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, mode)
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, RUNTIME_CONFIG_FILE)
    except FileExistsError:
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_ALREADY_EXISTS") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _write_runtime_config() -> bytes:
    fixed_environment = {
        "DB_TYPE": "mysql",
        "DOC_ENGINE": "elasticsearch",
        "ES_HOST": "material-rag-es",
        "MINIO_HOST": "material-rag-objectstore",
        "MYSQL_DBNAME": "rag_flow",
        "MYSQL_HOST": "material-rag-mysql",
        "MYSQL_PORT": "3306",
        "MYSQL_USER": "root",
        "REDIS_HOST": "material-rag-cache",
        "REDIS_PORT": "6379",
    }
    for name, expected in fixed_environment.items():
        if os.environ.get(name) != expected:
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")
    internal_passwords = []
    for name in (
        "ELASTIC_PASSWORD",
        "MINIO_PASSWORD",
        "MYSQL_PASSWORD",
        "REDIS_PASSWORD",
    ):
        value = os.environ.get(name, "")
        if not INTERNAL_PASSWORD_RE.fullmatch(value):
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")
        internal_passwords.append(value)
    if len(set(internal_passwords)) != 1:
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")

    try:
        original = _read_private(
            RUNTIME_CONFIG_FILE, minimum=1024, maximum=131072
        )
        template = _read_private(
            RUNTIME_CONFIG_TEMPLATE, minimum=1024, maximum=131072
        ).decode("utf-8", "strict")
    except (ProvisionError, UnicodeDecodeError):
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID") from None
    original_info = RUNTIME_CONFIG_FILE.lstat()
    if (
        original_info.st_uid != os.geteuid()
        or stat.S_IMODE(original_info.st_mode) != 0o644
        or hashlib.sha256(original).hexdigest() != EXPECTED_RUNTIME_CONFIG_SHA256
        or hashlib.sha256(template.encode("utf-8")).hexdigest()
        != EXPECTED_RUNTIME_TEMPLATE_SHA256
    ):
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")

    def replacement(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = os.environ.get(name)
        if not value:
            value = default or ""
        if any(character in value for character in ("\0", "\r", "\n")):
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")
        return value

    rendered = CONFIG_VARIABLE_RE.sub(replacement, template)
    if "${" in rendered:
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_INVALID")
    body = rendered.encode("utf-8")
    replaced = False
    try:
        _atomic_runtime_config_replace(body, 0o600)
        replaced = True
        info = RUNTIME_CONFIG_FILE.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(body)
        ):
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_WRITE_FAILED")
    except BaseException:
        if replaced:
            try:
                _atomic_runtime_config_replace(original, 0o644)
            except BaseException:
                raise ProvisionError(
                    "MATERIAL_RAG_RUNTIME_CONFIG_REMOVE_FAILED"
                ) from None
        raise
    return original


def _restore_runtime_config(original: bytes) -> None:
    try:
        info = RUNTIME_CONFIG_FILE.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_REMOVE_FAILED")
        _atomic_runtime_config_replace(original, 0o644)
        restored = RUNTIME_CONFIG_FILE.lstat()
        if (
            not stat.S_ISREG(restored.st_mode)
            or restored.st_nlink != 1
            or restored.st_uid != os.geteuid()
            or stat.S_IMODE(restored.st_mode) != 0o644
            or hashlib.sha256(
                _read_private(RUNTIME_CONFIG_FILE, minimum=1024, maximum=131072)
            ).hexdigest()
            != EXPECTED_RUNTIME_CONFIG_SHA256
        ):
            raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_REMOVE_FAILED")
    except (FileNotFoundError, OSError, ProvisionError):
        raise ProvisionError("MATERIAL_RAG_RUNTIME_CONFIG_REMOVE_FAILED") from None


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    authorization: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    if BASE_URL not in {EXPECTED_RAGFLOW_BASE_URL, EXPECTED_RAGFLOW_LOOPBACK_URL}:
        raise ProvisionError("MATERIAL_RAG_BASE_URL_INVALID")
    data = None
    headers: dict[str, str] = {
        "User-Agent": "curl/8.5.0",
        "Accept": "*/*",
    }
    if payload is not None:
        data = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        headers["Content-Type"] = "application/json"
    if authorization:
        headers["Authorization"] = authorization
    request = urllib.request.Request(
        BASE_URL + path, data=data, method=method, headers=headers
    )
    try:
        with _http_opener().open(request, timeout=30) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            status_code = int(response.status)
            response_authorization = response.headers.get("Authorization")
    except urllib.error.HTTPError as error:
        body = error.read(MAX_RESPONSE_BYTES + 1)
        status_code = int(error.code)
        response_authorization = error.headers.get("Authorization")
    except (OSError, urllib.error.URLError):
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_UNAVAILABLE") from None
    if status_code != 200 or not 1 <= len(body) <= MAX_RESPONSE_BYTES:
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_REQUEST_FAILED")
    try:
        document = json.loads(body.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID") from None
    if (
        not isinstance(document, dict)
        or document.get("code") not in (0, "0")
        or not isinstance(document.get("data"), (dict, list, str, type(None)))
    ):
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID")
    return document, response_authorization


_READY_ATTEMPT_STATE = {"attempt_count": 1, "elapsed_s": 0.0}


def _system_config() -> dict[str, Any]:
    document, evidence, error = _system_config_attempt(
        attempt_count=int(_READY_ATTEMPT_STATE.get("attempt_count") or 1),
        elapsed_s=float(_READY_ATTEMPT_STATE.get("elapsed_s") or 0.0),
    )
    if error:
        raise ProvisionError(error, evidence=evidence)
    if document is None:
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_REQUEST_FAILED", evidence=evidence)
    return document


def _system_config_attempt(
    *, attempt_count: int, elapsed_s: float
) -> tuple[dict[str, Any] | None, dict[str, object], str | None]:
    if BASE_URL not in {EXPECTED_RAGFLOW_BASE_URL, EXPECTED_RAGFLOW_LOOPBACK_URL}:
        evidence = _ready_evidence(
            attempt_count=attempt_count,
            curl_code=None,
            curl_exit_class="NONE",
            response_size=0,
            elapsed_s=elapsed_s,
        )
        return None, evidence, "MATERIAL_RAG_BASE_URL_INVALID"
    try:
        completed = subprocess.run(
            [
                "curl",
                "-sS",
                "-f",
                "--max-time",
                "20",
                "--path-as-is",
                BASE_URL + "/api/v1/system/config",
            ],
            capture_output=True,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired:
        evidence = _ready_evidence(
            attempt_count=attempt_count,
            curl_code=None,
            curl_exit_class="TIMEOUT",
            response_size=0,
            elapsed_s=elapsed_s,
        )
        return None, evidence, "MATERIAL_RAG_RAGFLOW_UNAVAILABLE"
    except OSError:
        evidence = _ready_evidence(
            attempt_count=attempt_count,
            curl_code=None,
            curl_exit_class="SPAWN",
            response_size=0,
            elapsed_s=elapsed_s,
        )
        return None, evidence, "MATERIAL_RAG_RAGFLOW_UNAVAILABLE"
    curl_code = int(completed.returncode)
    response_size = len(completed.stdout)
    exit_class = _curl_exit_class(curl_code)
    evidence = _ready_evidence(
        attempt_count=attempt_count,
        curl_code=curl_code,
        curl_exit_class=exit_class,
        response_size=response_size,
        elapsed_s=elapsed_s,
    )
    if curl_code in {6, 7, 28}:
        return None, evidence, "MATERIAL_RAG_RAGFLOW_UNAVAILABLE"
    if curl_code != 0 or not 1 <= response_size <= MAX_RESPONSE_BYTES:
        return None, evidence, "MATERIAL_RAG_RAGFLOW_REQUEST_FAILED"
    try:
        document = json.loads(completed.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, evidence, "MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID"
    if (
        not isinstance(document, dict)
        or document.get("code") not in (0, "0")
        or not isinstance(document.get("data"), (dict, list, str, type(None)))
    ):
        return None, evidence, "MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID"
    return document, evidence, None


def _wait_for_ragflow_api() -> None:
    started = time.monotonic()
    last_error = "MATERIAL_RAG_RAGFLOW_UNAVAILABLE"
    last_evidence: dict[str, object] | None = None
    attempt_count = 0
    while True:
        now = time.monotonic()
        if now - started >= 300:
            break
        attempt_count += 1
        _READY_ATTEMPT_STATE["attempt_count"] = attempt_count
        _READY_ATTEMPT_STATE["elapsed_s"] = now - started
        try:
            document = _system_config()
            last_evidence = None
            data = document.get("data")
            if isinstance(data, dict) and data.get("registerEnabled") in (
                1,
                True,
                "1",
            ):
                return
            if isinstance(data, dict):
                raise ProvisionError("MATERIAL_RAG_REGISTER_DISABLED")
            last_error = "MATERIAL_RAG_RAGFLOW_RESPONSE_INVALID"
        except ProvisionError as error:
            if error.args and error.args[0] == "MATERIAL_RAG_REGISTER_DISABLED":
                raise
            if error.args:
                last_error = error.args[0]
            if error.evidence is not None:
                last_evidence = error.evidence
        time.sleep(1)
    raise ProvisionError(last_error, evidence=last_evidence)


def _register_user(encrypted_password: str) -> None:
    _request(
        "POST",
        "/api/v1/users",
        payload={
            "nickname": NICKNAME,
            "email": EMAIL,
            "password": encrypted_password,
        },
    )


def _login_identity(encrypted_password: str) -> tuple[str, str]:
    document, header = _request(
        "POST",
        "/api/v1/auth/login",
        payload={"email": EMAIL, "password": encrypted_password},
    )
    data = document.get("data")
    user_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(user_id, str) or not IDENTIFIER_RE.fullmatch(user_id):
        raise ProvisionError("MATERIAL_RAG_LOGIN_IDENTITY_INVALID")
    candidates = [header]
    if isinstance(data, dict):
        candidates.extend(data.get(name) for name in ("access_token", "token"))
    for value in candidates:
        if isinstance(value, str) and 16 <= len(value) <= 4096:
            return value, user_id
    if _COOKIE_JAR is not None and any(_COOKIE_JAR):
        return "", user_id
    raise ProvisionError("MATERIAL_RAG_LOGIN_TOKEN_INVALID")


def _api_identity(login_token: str, user_id: str) -> tuple[str, str]:
    listed, _header = _request(
        "GET",
        "/api/v1/system/tokens",
        authorization=login_token or None,
    )
    data = listed.get("data")
    if not isinstance(data, list) or len(data) > 1:
        raise ProvisionError("MATERIAL_RAG_TOKEN_LIST_INVALID")
    if data:
        identity = data[0]
    else:
        created, _header = _request(
            "POST",
            "/api/v1/system/tokens",
            authorization=login_token or None,
        )
        identity = created.get("data")
    if not isinstance(identity, dict):
        raise ProvisionError("MATERIAL_RAG_API_TOKEN_INVALID")
    token = identity.get("token")
    tenant_id = identity.get("tenant_id")
    if (
        not isinstance(token, str)
        or not TOKEN_RE.fullmatch(token)
        or not isinstance(tenant_id, str)
        or not IDENTIFIER_RE.fullmatch(tenant_id)
        or tenant_id != user_id
        or identity.get("dialog_id") is not None
        or identity.get("source") is not None
    ):
        raise ProvisionError("MATERIAL_RAG_API_TOKEN_INVALID")
    return token, tenant_id


def _configure_provider(tenant_id: str, ark_key: str) -> None:
    if len(ark_key) > 512:
        raise ProvisionError("MATERIAL_RAG_ARK_KEY_INVALID")

    try:
        root_info = RAGFLOW_ROOT.lstat()
    except OSError:
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_ROOT_INVALID") from None
    if (
        not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or root_info.st_uid != 0
        or root_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise ProvisionError("MATERIAL_RAG_RAGFLOW_ROOT_INVALID")
    # ``python -I`` intentionally ignores the image's PYTHONPATH.  Add only
    # the verified, root-owned release root so its official modules can load.
    sys.path.insert(0, str(RAGFLOW_ROOT))

    # Imports intentionally happen only after the private runtime config has
    # been created.  Do not replace these model operations with CommonService
    # calls: their nested connection_context decorators can close the single
    # connection that owns the MySQL named lock.  Official modules emit
    # startup logs; keep the localctl stdout/stderr contract to one line.
    with open(os.devnull, "w", encoding="utf-8") as discarded:
        with contextlib.redirect_stdout(discarded), contextlib.redirect_stderr(
            discarded
        ):
            _configure_provider_locked(tenant_id, ark_key)


def _configure_provider_locked(tenant_id: str, ark_key: str) -> None:
    from common import settings

    settings.init_settings()
    from api.db.db_models import (
        DB,
        TenantModel,
        TenantModelInstance,
        TenantModelProvider,
    )
    from common.misc_utils import get_uuid

    instance_extra = json.dumps(
        {"base_url": ARK_RELAY_BASE_URL},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    model_extra = json.dumps(
        {"max_tokens": MODEL_MAX_TOKENS},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    lock_name = f"material-rag-provider:{tenant_id}"

    with DB.connection_context():
        with DB.lock(lock_name, 60):
            with DB.atomic():
                providers = list(
                    TenantModelProvider.select().where(
                        TenantModelProvider.tenant_id == tenant_id
                    )
                )
                if not providers:
                    TenantModelProvider.create(
                        id=get_uuid(), tenant_id=tenant_id, provider_name=PROVIDER
                    )
                # Re-read every layer after a possible insert.  Both the fresh
                # and reuse paths therefore pass the same exact validator
                # before a child record can be created.
                providers = list(
                    TenantModelProvider.select().where(
                        TenantModelProvider.tenant_id == tenant_id
                    )
                )
                if len(providers) != 1:
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")
                provider = providers[0]
                if (
                    not IDENTIFIER_RE.fullmatch(str(provider.id))
                    or provider.provider_name != PROVIDER
                ):
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")

                instances = list(
                    TenantModelInstance.select().where(
                        TenantModelInstance.provider_id == provider.id
                    )
                )
                if not instances:
                    TenantModelInstance.create(
                        id=get_uuid(),
                        provider_id=provider.id,
                        instance_name=INSTANCE,
                        api_key=ark_key,
                        status="active",
                        extra=instance_extra,
                    )
                instances = list(
                    TenantModelInstance.select().where(
                        TenantModelInstance.provider_id == provider.id
                    )
                )
                if len(instances) != 1:
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")
                instance = instances[0]
                if (
                    not IDENTIFIER_RE.fullmatch(str(instance.id))
                    or instance.instance_name != INSTANCE
                    or instance.status != "active"
                    or instance.extra != instance_extra
                    or not hmac.compare_digest(str(instance.api_key), ark_key)
                ):
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")

                models = list(
                    TenantModel.select().where(
                        TenantModel.provider_id == provider.id
                    )
                )
                if not models:
                    TenantModel.create(
                        id=get_uuid(),
                        provider_id=provider.id,
                        instance_id=instance.id,
                        model_name=MODEL,
                        model_type=MODEL_TYPE,
                        status="active",
                        extra=model_extra,
                    )
                models = list(
                    TenantModel.select().where(
                        TenantModel.provider_id == provider.id
                    )
                )
                if len(models) != 1:
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")
                model = models[0]
                if (
                    not IDENTIFIER_RE.fullmatch(str(model.id))
                    or model.instance_id != instance.id
                    or model.model_name != MODEL
                    or model.model_type != MODEL_TYPE
                    or model.status != "active"
                    or model.extra != model_extra
                ):
                    raise ProvisionError("MATERIAL_RAG_PROVIDER_STATE_INVALID")


def _write_token(token: str) -> None:
    if not CONTROL_DIRECTORY.is_absolute() or TOKEN_FILE.parent != CONTROL_DIRECTORY:
        raise ProvisionError("MATERIAL_RAG_CONTROL_PATH_INVALID")
    info = CONTROL_DIRECTORY.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ProvisionError("MATERIAL_RAG_CONTROL_DIRECTORY_INVALID")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(TOKEN_FILE, flags, 0o600)
        try:
            body = token.encode("ascii")
            if os.write(descriptor, body) != len(body):
                raise ProvisionError("MATERIAL_RAG_TOKEN_WRITE_FAILED")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        raise ProvisionError("MATERIAL_RAG_TOKEN_ALREADY_EXISTS") from None
    info = TOKEN_FILE.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size != len(token)
    ):
        raise ProvisionError("MATERIAL_RAG_TOKEN_WRITE_FAILED")


def _write_embedding_only_attestation() -> None:
    if (
        not CONTROL_DIRECTORY.is_absolute()
        or ATTESTATION_FILE.parent != CONTROL_DIRECTORY
        or ATTESTATION_FILE.name != "provider-attestation.json"
    ):
        raise ProvisionError("MATERIAL_RAG_CONTROL_PATH_INVALID")
    payload = {
        "configured_base_url": ARK_RELAY_BASE_URL,
        "external_llm_call_count": 0,
        "external_llm_enabled": False,
        "instance": INSTANCE,
        "model": MODEL,
        "model_types": ["embedding"],
        "provider": PROVIDER,
        "schema": "anhuan-material-rag-provider-attestation-v2",
        "upstream_authority": ARK_UPSTREAM_AUTHORITY,
        "upstream_embedding_path": ARK_UPSTREAM_EMBEDDING_PATH,
    }
    body = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(ATTESTATION_FILE, flags, 0o600)
        try:
            if os.write(descriptor, body) != len(body):
                raise ProvisionError("MATERIAL_RAG_ATTESTATION_WRITE_FAILED")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        raise ProvisionError("MATERIAL_RAG_ATTESTATION_ALREADY_EXISTS") from None
    info = ATTESTATION_FILE.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_size != len(body)
    ):
        raise ProvisionError("MATERIAL_RAG_ATTESTATION_WRITE_FAILED")


def main() -> int:
    failure_reason = PROVIDER_PREFLIGHT_FAILED
    try:
        if not EMAIL_RE.fullmatch(EMAIL):
            raise ProvisionError("MATERIAL_RAG_EMAIL_INVALID")
        password = bytearray(_read_private(PASSWORD_FILE, minimum=32, maximum=256))
        ark_raw = bytearray(_read_private(ARK_KEY_FILE, minimum=16, maximum=16384))
        try:
            encrypted = _encrypt_password(bytes(password))
            try:
                ark_key = bytes(ark_raw).decode("ascii", "strict")
            except UnicodeDecodeError:
                raise ProvisionError("MATERIAL_RAG_ARK_KEY_INVALID") from None
            if not ark_key or ark_key != ark_key.strip():
                raise ProvisionError("MATERIAL_RAG_ARK_KEY_INVALID")
            _verify_runtime_version()
            failure_reason = PROVIDER_READY_FAILED
            _wait_for_ragflow_api()
            failure_reason = PROVIDER_REGISTER_FAILED
            _register_user(encrypted)
            failure_reason = PROVIDER_LOGIN_FAILED
            login_token, user_id = _login_identity(encrypted)
            failure_reason = PROVIDER_TOKEN_FAILED
            token, tenant_id = _api_identity(login_token, user_id)
            failure_reason = PROVIDER_RUNTIME_CONFIG_FAILED
            original_runtime_config = _write_runtime_config()
            try:
                failure_reason = PROVIDER_STATE_FAILED
                _configure_provider(tenant_id, ark_key)
            finally:
                try:
                    _restore_runtime_config(original_runtime_config)
                except Exception:
                    failure_reason = PROVIDER_RUNTIME_CONFIG_FAILED
                    raise
            # RAGFlow can reach Ark only through the endpoint-aware relay.
            # The first authorized Demo body, not a synthetic provider probe,
            # is the only subsequent real connectivity validation.
            failure_reason = PROVIDER_CONTROL_WRITE_FAILED
            _write_embedding_only_attestation()
            _write_token(token)
        finally:
            password[:] = b"\0" * len(password)
            ark_raw[:] = b"\0" * len(ark_raw)
        print("LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_OK")
        return 0
    except ProvisionError as error:
        reason = error.args[0] if error.args else ""
        if failure_reason == PROVIDER_READY_FAILED:
            _print_ready_evidence(error.evidence)
        if reason == "MATERIAL_RAG_REGISTER_DISABLED":
            print(PROVIDER_REGISTER_DISABLED, file=os.sys.stderr)
        elif failure_reason == PROVIDER_READY_FAILED and reason in READY_ERROR_REASONS:
            print(READY_ERROR_REASONS[reason], file=os.sys.stderr)
        else:
            print(failure_reason, file=os.sys.stderr)
        return 2
    except Exception:
        print(PROVIDER_INTERNAL_ERROR, file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
