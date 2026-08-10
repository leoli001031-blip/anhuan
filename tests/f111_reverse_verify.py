"""F1.1.1 fail-closed reverse verifier for one ephemeral scratch stack.

The verifier prints exactly the twenty taskbook metrics and nothing else.  It
never reads ``.env`` files, never embeds a credential or fixture location, and
refuses a compose project that is not a random ``anhuan-f111-repair-*`` scope.
All business mutations use OIDC-authenticated HTTP.  PostgreSQL, MinIO, RQ and
RAGFlow access is limited to observation, adversarial CAS checks, and exact
cleanup of identifiers registered by this run.
"""
from __future__ import annotations

import ast
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
warnings.filterwarnings("error")

METRICS = (
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

SCRATCH_PROJECT_PREFIX = "anhuan-f111-repair-"
DEPENDENCY_UNREACHABLE = 90
REQUIRED_FIXTURES = 4
F1_TABLES = (
    "enterprise",
    "enterprise_user",
    "user_profile",
    "plant",
    "document",
    "upload_task",
    "outbox",
    "qa_request",
    "invite_jti",
)
IDENTITY_COLUMNS = (
    ("audit_log", "id"),
    ("outbox", "id"),
    ("qa_request", "request_id"),
    ("upload_task", "id"),
    ("document", "id"),
    ("invite_jti", "jti"),
    ("enterprise_user", "id"),
    ("user_profile", "id"),
)
UPSTREAM_TABLES = (
    "configuration",
    "run",
    "document_scope",
    "page",
    "block",
    "chunk",
    "chunk_block_link",
)


class ReverseFailure(RuntimeError):
    """Fixed-code verifier failure; its message is deliberately never printed."""


class DependencyFailure(ReverseFailure):
    pass


def _sha(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _opaque_json(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str))


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _is_remote_id(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(value or "")))


def _decode_claims(token: str) -> dict[str, Any]:
    try:
        segment = token.split(".")[1]
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
        value = json.loads(raw)
    except (IndexError, ValueError, json.JSONDecodeError):
        raise ReverseFailure("TOKEN_CLAIMS_INVALID") from None
    if not isinstance(value, dict):
        raise ReverseFailure("TOKEN_CLAIMS_INVALID")
    return value


def _tag_value(raw_tags: Any, key: str) -> str | None:
    tags = raw_tags if isinstance(raw_tags, list) else [raw_tags]
    for raw in tags:
        name, separator, value = str(raw or "").partition("=")
        if separator and name == key and value:
            return value
    return None


def _answer_reference_ids(answer: str) -> set[str]:
    references: set[str] = set()
    for raw in re.findall(
        r"\[chunk_id=([0-9a-fA-F-]{36}),\s*pages=\[[0-9,\s]+\]\]",
        answer,
    ):
        try:
            references.add(str(uuid.UUID(raw)))
        except ValueError:
            raise ReverseFailure("ANSWER_CITATION_INVALID") from None
    return references


def format_metric_line(values: Mapping[str, int]) -> str:
    """Return the only permitted stdout payload in the exact contract order."""
    normalized: list[str] = []
    for name in METRICS:
        value = values.get(name, 1)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            value = 1
        normalized.append(f"{name}={value}")
    return " ".join(normalized)


def invite_consume_probe_payload(token: str, project: str) -> dict[str, str]:
    """Build one compatibility-field spoof without exposing an identity."""

    suffix = project.removeprefix(SCRATCH_PROJECT_PREFIX)
    if not token or not re.fullmatch(r"[0-9a-f]{32}", suffix):
        raise ReverseFailure("INVITE_PROBE_IDENTITY_INVALID")
    return {
        "token": token,
        "keycloak_sub": "f111-forged-" + suffix,
        "email": suffix + "@fixture.invalid",
    }


def audit_gate_failure_count(
    *, auditor_status: int, enterprise_admin_status: int, observed_role: str
) -> int:
    """Both the positive and negative HTTP authorization outcomes are required."""

    return (
        int(auditor_status != 200)
        + int(enterprise_admin_status != 403)
        + int(observed_role != "enterprise_admin")
    )


class SecretFiles:
    """Read bounded 0600 regular files from one explicit 0700 directory."""

    def __init__(self, directory: Path) -> None:
        if not directory.is_absolute() or directory.name.startswith(".env"):
            raise ReverseFailure("SECRET_SCOPE_INVALID")
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ReverseFailure("SECRET_SCOPE_INVALID")
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise ReverseFailure("SECRET_SCOPE_PERMISSIONS")
        self._directory = directory

    def read(self, name: str, *, maximum: int = 65536) -> str:
        if not re.fullmatch(r"[a-z0-9_]{1,64}", name):
            raise ReverseFailure("SECRET_NAME_INVALID")
        path = self._directory / name
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_size < 1
                or info.st_size > maximum
            ):
                raise ReverseFailure("SECRET_FILE_INVALID")
            raw = os.read(descriptor, maximum + 1)
        finally:
            os.close(descriptor)
        try:
            value = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            raise ReverseFailure("SECRET_FILE_INVALID") from None
        if not value:
            raise ReverseFailure("SECRET_FILE_INVALID")
        return value


@dataclass(frozen=True, slots=True)
class Fixture:
    location: Path
    sha256: str
    content_type: str

    def body(self) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.location, flags)
        except OSError:
            raise ReverseFailure("FIXTURE_INVALID")
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size < 1
                or info.st_size > 100 * 1024 * 1024
            ):
                raise ReverseFailure("FIXTURE_INVALID")
            chunks: list[bytes] = []
            remaining = info.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise ReverseFailure("FIXTURE_INVALID")
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if _sha(raw) != self.sha256:
            raise ReverseFailure("FIXTURE_HASH_MISMATCH")
        return raw


@dataclass(frozen=True, slots=True)
class ReverseConfig:
    project: str
    api_base: str
    keycloak_base: str
    compose_files: tuple[Path, ...]
    secrets: SecretFiles
    fixtures: tuple[Fixture, ...]
    enterprise_a: uuid.UUID
    enterprise_b: uuid.UUID
    leak_canaries: tuple[bytes, ...]
    timeout_seconds: int

    @property
    def scratch_database_name(self) -> str:
        """Derive the only database this random verifier project may touch."""
        return "f111_repair_" + self.project.removeprefix(SCRATCH_PROJECT_PREFIX)

    @classmethod
    def from_environment(cls) -> "ReverseConfig":
        project = os.environ.get("F111_REVERSE_PROJECT", "")
        suffix = project.removeprefix(SCRATCH_PROJECT_PREFIX)
        if not project.startswith(SCRATCH_PROJECT_PREFIX) or not re.fullmatch(
            r"[0-9a-f]{32}", suffix
        ):
            raise ReverseFailure("UNSAFE_STACK_SCOPE")
        if uuid.UUID(hex=suffix).version != 4:
            raise ReverseFailure("UNSAFE_STACK_SCOPE")
        raw_secret_dir = os.environ.get("F111_REVERSE_SECRETS_DIR", "")
        if not raw_secret_dir:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        secrets = SecretFiles(Path(raw_secret_dir))
        api_base = os.environ.get("F111_REVERSE_API_BASE", "").rstrip("/")
        keycloak_base = os.environ.get("F111_REVERSE_KEYCLOAK_BASE", "").rstrip("/")
        if not api_base.startswith("http://127.0.0.1:"):
            raise ReverseFailure("UNSAFE_API_SCOPE")
        if not keycloak_base.startswith("http://127.0.0.1:"):
            raise ReverseFailure("UNSAFE_IDP_SCOPE")
        compose_file = ROOT / "infra/f1/docker-compose.yml"
        raw_override = os.environ.get("F111_REVERSE_COMPOSE_OVERRIDE", "")
        if not raw_override:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        override = Path(raw_override)
        allowed_root = (ROOT / "infra/f1").resolve()
        try:
            resolved_override = override.resolve(strict=True)
        except OSError:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        if (
            not override.is_absolute()
            or override.is_symlink()
            or resolved_override.parent != allowed_root
            or resolved_override.name != "docker-compose.repair.yml"
        ):
            raise ReverseFailure("UNSAFE_COMPOSE_OVERRIDE")
        if not compose_file.is_file():
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        try:
            manifest = json.loads(secrets.read("fixture_manifest", maximum=262144))
        except json.JSONDecodeError:
            raise ReverseFailure("FIXTURE_MANIFEST_INVALID") from None
        if not isinstance(manifest, list) or len(manifest) < REQUIRED_FIXTURES:
            raise ReverseFailure("FIXTURE_MANIFEST_INCOMPLETE")
        fixtures: list[Fixture] = []
        seen: set[str] = set()
        for raw in manifest:
            if not isinstance(raw, dict):
                raise ReverseFailure("FIXTURE_MANIFEST_INVALID")
            digest = str(raw.get("sha256", ""))
            media = str(raw.get("content_type", ""))
            location = Path(str(raw.get("path", "")))
            if (
                not re.fullmatch(r"[0-9a-f]{64}", digest)
                or digest in seen
                or media not in {"application/pdf", "image/jpeg"}
                or not location.is_absolute()
            ):
                raise ReverseFailure("FIXTURE_MANIFEST_INVALID")
            seen.add(digest)
            fixtures.append(Fixture(location, digest, media))
        try:
            raw_canaries = json.loads(
                secrets.read("leak_canaries", maximum=262144)
            )
        except json.JSONDecodeError:
            raise ReverseFailure("LEAK_CANARY_MANIFEST_INVALID") from None
        if not isinstance(raw_canaries, list) or not 1 <= len(raw_canaries) <= 64:
            raise ReverseFailure("LEAK_CANARY_MANIFEST_INVALID")
        leak_canaries: list[bytes] = []
        for raw in raw_canaries:
            if not isinstance(raw, str):
                raise ReverseFailure("LEAK_CANARY_MANIFEST_INVALID")
            encoded = raw.encode("utf-8")
            if len(encoded) < 8 or len(encoded) > 4096:
                raise ReverseFailure("LEAK_CANARY_MANIFEST_INVALID")
            leak_canaries.append(encoded)
        timeout = int(os.environ.get("F111_REVERSE_TIMEOUT_SECONDS", "420"))
        if timeout < 60 or timeout > 900:
            raise ReverseFailure("TIMEOUT_INVALID")
        return cls(
            project=project,
            api_base=api_base,
            keycloak_base=keycloak_base,
            compose_files=(compose_file, resolved_override),
            secrets=secrets,
            fixtures=tuple(fixtures),
            enterprise_a=uuid.UUID(secrets.read("enterprise_a_id")),
            enterprise_b=uuid.UUID(secrets.read("enterprise_b_id")),
            leak_canaries=tuple(leak_canaries),
            timeout_seconds=timeout,
        )


@dataclass(slots=True)
class ResourceRegistry:
    run_id: str
    db_ids: dict[str, set[str]] = field(default_factory=dict)
    object_etags: dict[str, str] = field(default_factory=dict)
    rq_job_ids: set[str] = field(default_factory=set)
    ragflow_dataset_ids: set[str] = field(default_factory=set)
    ragflow_document_ids: dict[str, set[str]] = field(default_factory=dict)
    ragflow_chunk_ids: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    invite_memberships: set[tuple[str, str]] = field(default_factory=set)

    def add_db(self, table: str, value: Any) -> None:
        if value is not None:
            self.db_ids.setdefault(table, set()).add(str(value))


@dataclass(frozen=True, slots=True)
class Snapshot:
    planes: tuple[tuple[str, str], ...]
    database_identities: tuple[tuple[str, tuple[str, ...]], ...]
    ragflow_inventory_state: Mapping[str, Any]
    legacy_object_orphans: int
    legacy_rq_orphans: int
    index_duplicate_count: int
    upstream_digest: str

    def plane(self, name: str) -> str:
        return dict(self.planes)[name]


def current_run_delta(before: int, after: int) -> int:
    """Legacy is reported separately; only a new positive delta is a failure."""
    return max(0, after - before)


class HttpBusinessClient:
    """OIDC + HTTP is the only path for business mutations."""

    def __init__(self, config: ReverseConfig, registry: ResourceRegistry) -> None:
        self.config = config
        self.registry = registry
        self._tokens: dict[str, str] = {}
        self._token_expiry: dict[str, int] = {}

    def token(self, identity: str, *, fresh: bool = False) -> str:
        if (
            identity in self._tokens
            and not fresh
            and self._token_expiry.get(identity, 0) > int(time.time()) + 20
        ):
            return self._tokens[identity]
        username = self.config.secrets.read(f"{identity}_username")
        password = self.config.secrets.read(f"{identity}_password")
        data = urllib.parse.urlencode(
            {
                "username": username,
                "password": password,
                "grant_type": "password",
                "client_id": "anhuan-web",
            }
        ).encode("ascii")
        request = urllib.request.Request(
            self.config.keycloak_base
            + "/realms/anhuan/protocol/openid-connect/token",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        self._tokens[identity] = token
        claims = _decode_claims(token)
        try:
            self._token_expiry[identity] = int(claims.get("exp", 0))
        except (TypeError, ValueError):
            self._token_expiry[identity] = 0
        return token

    def token_canaries(self) -> tuple[bytes, ...]:
        values: list[bytes] = []
        for token in self._tokens.values():
            encoded = token.encode("utf-8")
            values.extend((encoded, encoded[:32], encoded[-32:]))
        return tuple(values)

    def request(
        self,
        method: str,
        path: str,
        identity: str,
        *,
        enterprise_id: uuid.UUID | None = None,
        body: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.token(identity)}"}
        if enterprise_id is not None:
            headers["X-Enterprise-Id"] = str(enterprise_id)
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.config.api_base + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {}
            return error.code, payload
        except (OSError, urllib.error.URLError):
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def upload(self, fixture: Fixture, identity: str = "tenant_a") -> tuple[int, dict]:
        boundary = "----f111" + self.registry.run_id[-24:]
        opaque_name = self.registry.run_id[-32:] + (
            ".pdf" if fixture.content_type == "application/pdf" else ".jpg"
        )
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{opaque_name}"\r\n'
            f"Content-Type: {fixture.content_type}\r\n\r\n"
        ).encode("ascii") + fixture.body() + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            self.config.api_base + "/api/v1/documents/upload",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token(identity, fresh=True)}",
                "X-Enterprise-Id": str(self.config.enterprise_a),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.loads(response.read())
                return response.status, payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read())
            except json.JSONDecodeError:
                payload = {}
            return error.code, payload if isinstance(payload, dict) else {}
        except (OSError, urllib.error.URLError):
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def documents(self, identity: str = "tenant_a", *, enterprise: uuid.UUID | None = None) -> tuple[int, list]:
        status, payload = self.request(
            "GET",
            "/api/v1/documents",
            identity,
            enterprise_id=enterprise or self.config.enterprise_a,
        )
        return status, payload if isinstance(payload, list) else []

    def audit(
        self,
        identity: str = "auditor",
        *,
        enterprise: uuid.UUID | None = None,
    ) -> tuple[int, list]:
        status, payload = self.request(
            "GET",
            "/api/v1/audit",
            identity,
            enterprise_id=enterprise or self.config.enterprise_a,
        )
        return status, payload if isinstance(payload, list) else []

    def wait_document(self, document_id: str, expected: set[str]) -> bool:
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            status, rows = self.documents()
            if status == 200:
                row = next((item for item in rows if str(item.get("id")) == document_id), None)
                if row and row.get("status") in expected:
                    return True
            time.sleep(2)
        return False


class ScratchServiceController:
    """Fault controls are permitted only after the random project gate passed."""

    def __init__(self, config: ReverseConfig) -> None:
        if not config.project.startswith(SCRATCH_PROJECT_PREFIX):
            raise ReverseFailure("UNSAFE_STACK_SCOPE")
        self.config = config
        self.database_port: int | None = None
        self.expected_services: set[str] = set()

    def _base_command(self) -> list[str]:
        command = [
            "docker", "compose", "--env-file", "/dev/null",
            "-p", self.config.project,
        ]
        for compose_file in self.config.compose_files:
            command.extend(("-f", str(compose_file)))
        return command

    def validate_isolation(self) -> None:
        unsafe_environment = (
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_ENV_FILES",
        )
        if any(os.environ.get(name) for name in unsafe_environment):
            raise ReverseFailure("UNSAFE_DOCKER_SCOPE")
        context = subprocess.run(
            [
                "docker", "context", "inspect", "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=False,
            timeout=30,
        )
        if context.returncode != 0:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        try:
            docker_host = json.loads(context.stdout)
        except json.JSONDecodeError:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        parsed_docker_host = urllib.parse.urlsplit(str(docker_host))
        if (
            parsed_docker_host.scheme != "unix"
            or parsed_docker_host.netloc
            or not Path(parsed_docker_host.path).is_absolute()
        ):
            raise ReverseFailure("UNSAFE_DOCKER_SCOPE")
        command = self._base_command() + ["config", "--format", "json"]
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        try:
            effective = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        if not isinstance(effective, dict) or effective.get("name") != self.config.project:
            raise ReverseFailure("UNSAFE_STACK_SCOPE")
        services = effective.get("services")
        if not isinstance(services, dict):
            raise ReverseFailure("COMPOSE_SCOPE_INVALID")
        self.expected_services = set(services)
        shared_ports = {
            80, 3000, 4317, 4318, 5173, 6379, 8001, 8080,
            9000, 9001, 9090, 9380, 16686,
        }
        for service in services.values():
            if not isinstance(service, dict) or service.get("container_name"):
                raise ReverseFailure("COMPOSE_SCOPE_INVALID")
            for port in service.get("ports") or []:
                if not isinstance(port, dict):
                    raise ReverseFailure("COMPOSE_SCOPE_INVALID")
                try:
                    published = int(port.get("published"))
                except (TypeError, ValueError):
                    raise ReverseFailure("COMPOSE_SCOPE_INVALID") from None
                if published in shared_ports:
                    raise ReverseFailure("UNSAFE_STACK_SCOPE")

        def published_port(service_name: str, target: int) -> int:
            service = services.get(service_name)
            if not isinstance(service, dict):
                raise ReverseFailure("COMPOSE_SCOPE_INVALID")
            matches: list[int] = []
            for port in service.get("ports") or []:
                if not isinstance(port, dict) or int(port.get("target", -1)) != target:
                    continue
                if str(port.get("host_ip", "")) != "127.0.0.1":
                    raise ReverseFailure("UNSAFE_STACK_SCOPE")
                try:
                    matches.append(int(port["published"]))
                except (KeyError, TypeError, ValueError):
                    raise ReverseFailure("COMPOSE_SCOPE_INVALID") from None
            if len(matches) != 1:
                raise ReverseFailure("COMPOSE_SCOPE_INVALID")
            return matches[0]

        def url_port(value: str, schemes: set[str]) -> int:
            parsed = urllib.parse.urlsplit(value)
            if (
                parsed.scheme not in schemes
                or parsed.hostname != "127.0.0.1"
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ReverseFailure("UNSAFE_DEPENDENCY_SCOPE")
            try:
                return int(parsed.port)
            except (TypeError, ValueError):
                raise ReverseFailure("UNSAFE_DEPENDENCY_SCOPE") from None

        minio_endpoint = self.config.secrets.read("minio_endpoint")
        endpoint_bindings = (
            (url_port(self.config.api_base, {"http"}), published_port("api", 8001)),
            (
                url_port(self.config.keycloak_base, {"http"}),
                published_port("keycloak", 8080),
            ),
            (
                url_port("http://" + minio_endpoint, {"http"}),
                published_port("minio", 9000),
            ),
            (
                url_port(self.config.secrets.read("redis_url"), {"redis"}),
                published_port("redis", 6379),
            ),
            (
                url_port(self.config.secrets.read("ragflow_base_url"), {"http"}),
                published_port("ragflow", 9380),
            ),
            (
                url_port(self.config.secrets.read("jaeger_base_url"), {"http"}),
                published_port("jaeger", 16686),
            ),
        )
        if any(observed != expected for observed, expected in endpoint_bindings):
            raise ReverseFailure("UNSAFE_DEPENDENCY_SCOPE")

        def service_environment(service_name: str) -> dict[str, str]:
            service = services.get(service_name)
            if not isinstance(service, dict):
                raise ReverseFailure("COMPOSE_SCOPE_INVALID")
            raw = service.get("environment")
            if isinstance(raw, dict):
                return {str(key): str(value) for key, value in raw.items()}
            if isinstance(raw, list):
                result: dict[str, str] = {}
                for item in raw:
                    key, separator, value = str(item).partition("=")
                    if not separator:
                        raise ReverseFailure("COMPOSE_SCOPE_INVALID")
                    result[key] = value
                return result
            raise ReverseFailure("COMPOSE_SCOPE_INVALID")

        expected_database = self.config.scratch_database_name
        database_ports: set[int] = set()
        for service_name in ("api", "worker", "dispatcher"):
            environment = service_environment(service_name)
            if (
                environment.get("F1_PG_DATABASE") != expected_database
                or environment.get("F1_PG_HOST") != "host.docker.internal"
                or environment.get("F1_SECRETS_DIR") != "/run/secrets/f1"
                or environment.get("REDIS_URL") != "redis://redis:6379/0"
            ):
                raise ReverseFailure("UNSAFE_RUNTIME_SCOPE")
            try:
                database_ports.add(int(environment["F1_PG_PORT"]))
            except (KeyError, TypeError, ValueError):
                raise ReverseFailure("UNSAFE_RUNTIME_SCOPE") from None
        for service_name in ("api", "worker"):
            environment = service_environment(service_name)
            if (
                environment.get("MINIO_ENDPOINT") != "minio:9000"
                or environment.get("RAGFLOW_BASE_URL") != "http://ragflow:80"
            ):
                raise ReverseFailure("UNSAFE_RUNTIME_SCOPE")
        api_environment = service_environment("api")
        if (
            api_environment.get("KEYCLOAK_URL") != "http://keycloak:8080"
            or api_environment.get("OTEL_EXPORTER_OTLP_ENDPOINT") != "jaeger:4317"
        ):
            raise ReverseFailure("UNSAFE_RUNTIME_SCOPE")
        if len(database_ports) != 1:
            raise ReverseFailure("UNSAFE_RUNTIME_SCOPE")
        self.database_port = database_ports.pop()

    def _run(self, *arguments: str, timeout: int = 180) -> None:
        command = self._base_command()
        command.extend(arguments)
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")

    def pause(self, service: str) -> None:
        if service not in {"redis", "worker", "ragflow"}:
            raise ReverseFailure("FAULT_TARGET_INVALID")
        self._run("pause", service)

    def unpause(self, service: str) -> None:
        if service not in {"redis", "worker", "ragflow"}:
            raise ReverseFailure("FAULT_TARGET_INVALID")
        self._run("unpause", service)

    def signal_worker(self) -> None:
        self._run("kill", "-s", "SIGKILL", "worker")

    def ensure_worker(self) -> None:
        self._run(
            "up", "-d", "--wait", "--wait-timeout", "180",
            "worker", "dispatcher", timeout=240,
        )

    def state_digest(self) -> str:
        command = self._base_command() + ["ps", "--all", "--format", "json"]
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        raw = result.stdout.strip()
        try:
            decoded = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            try:
                decoded = [json.loads(line) for line in raw.splitlines() if line]
            except json.JSONDecodeError:
                raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        rows = decoded if isinstance(decoded, list) else [decoded]
        normalized: dict[str, tuple[str, str, int]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ReverseFailure("COMPOSE_STATE_INVALID")
            service = str(row.get("Service", ""))
            if not service or service in normalized:
                raise ReverseFailure("COMPOSE_STATE_INVALID")
            try:
                exit_code = int(row.get("ExitCode", 0) or 0)
            except (TypeError, ValueError):
                raise ReverseFailure("COMPOSE_STATE_INVALID") from None
            normalized[service] = (
                str(row.get("State", "")),
                str(row.get("Health", "")),
                exit_code,
            )
        if set(normalized) != self.expected_services:
            raise ReverseFailure("COMPOSE_STATE_INVALID")
        return _opaque_json(normalized)

    def logs(self) -> bytes:
        command = self._base_command()
        command.extend(("logs", "--no-color"))
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE")
        return result.stdout + result.stderr

    def traces(self) -> bytes:
        base = self.config.secrets.read("jaeger_base_url").rstrip("/")
        if not base.startswith("http://127.0.0.1:"):
            raise ReverseFailure("UNSAFE_TRACE_SCOPE")
        query = urllib.parse.urlencode(
            {"service": "anhuan-f1-api", "limit": "200"}
        )
        try:
            with urllib.request.urlopen(base + "/api/traces?" + query, timeout=30) as response:
                return response.read()
        except (OSError, urllib.error.URLError):
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None


class ControlPlane:
    """Read-only evidence plus exact run-scoped fault and cleanup controls."""

    def __init__(self, config: ReverseConfig, registry: ResourceRegistry) -> None:
        self.config = config
        self.registry = registry
        self._control_dsn = config.secrets.read("control_dsn")
        self._worker_dsn = config.secrets.read("worker_dsn")
        self._baseline_identities: dict[str, set[str]] | None = None

    def connect(self):
        try:
            import psycopg

            return psycopg.connect(self._control_dsn)
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def validate_database_scope(self, expected_port: int | None) -> None:
        """Reject a DSN unless both roles land in this run's derived scratch DB."""
        try:
            import psycopg

            identities: list[tuple[str, bool, int]] = []
            for dsn in (self._control_dsn, self._worker_dsn):
                with psycopg.connect(dsn) as connection:
                    row = connection.execute(
                        "SELECT current_database(), "
                        "current_setting('is_superuser') = 'on', inet_server_port()"
                    ).fetchone()
                if row is None:
                    raise DependencyFailure("DEPENDENCY_UNREACHABLE")
                identities.append((str(row[0]), bool(row[1]), int(row[2])))
        except ReverseFailure:
            raise
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        expected = self.config.scratch_database_name
        if expected_port is None or identities != [
            (expected, True, expected_port),
            (expected, False, expected_port),
        ]:
            raise ReverseFailure("UNSAFE_DATABASE_SCOPE")

    def _row_digest(self, schema: str, table: str) -> str:
        if schema not in {"f1", "f0i"} or not re.fullmatch(r"[a-z_]+", table):
            raise ReverseFailure("SNAPSHOT_TABLE_INVALID")
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT row_to_json(t)::text FROM {schema}.{table} AS t ORDER BY 1"
            ).fetchall()
        accumulator = hashlib.sha256()
        for (raw,) in rows:
            accumulator.update(str(raw).encode("utf-8"))
            accumulator.update(b"\x00")
        return accumulator.hexdigest()

    def database_digest(self) -> str:
        return _opaque_json({table: self._row_digest("f1", table) for table in F1_TABLES})

    def database_identities(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        identities: list[tuple[str, tuple[str, ...]]] = []
        with self.connect() as connection:
            for table, column in IDENTITY_COLUMNS:
                rows = connection.execute(
                    f"SELECT {column}::text FROM f1.{table} ORDER BY 1"
                ).fetchall()
                identities.append((table, tuple(str(row[0]) for row in rows)))
        return tuple(identities)

    def bind_baseline_identities(self, snapshot_value: Snapshot) -> None:
        self._baseline_identities = {
            table: set(values) for table, values in snapshot_value.database_identities
        }

    def require_current_run_identity(self, table: str, value: Any) -> None:
        if self._baseline_identities is None or table not in dict(IDENTITY_COLUMNS):
            raise ReverseFailure("BASELINE_IDENTITIES_NOT_BOUND")
        if str(value) in self._baseline_identities.get(table, set()):
            raise ReverseFailure("PREEXISTING_RESOURCE_REUSE")

    def audit_digest(self) -> str:
        return self._row_digest("f1", "audit_log")

    def upstream_digest(self) -> str:
        return _opaque_json(
            {table: self._row_digest("f0i", table) for table in UPSTREAM_TABLES}
        )

    def minio_inventory(self) -> dict[str, tuple[str, int]]:
        try:
            from minio import Minio

            endpoint = self.config.secrets.read("minio_endpoint")
            client = Minio(
                endpoint,
                access_key=self.config.secrets.read("minio_access_key"),
                secret_key=self.config.secrets.read("minio_secret_key"),
                secure=self.config.secrets.read("minio_secure").lower() == "true",
            )
            bucket = self.config.secrets.read("minio_bucket")
            return {
                item.object_name: (str(item.etag or ""), int(item.size or 0))
                for item in client.list_objects(bucket, recursive=True)
            }
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def minio_digest(self) -> str:
        inventory = self.minio_inventory()
        return _opaque_json(
            sorted((_sha(key), etag, size) for key, (etag, size) in inventory.items())
        )

    def _rq_components(self):
        try:
            from redis import Redis
            from rq import Queue
            from rq.registry import (
                DeferredJobRegistry,
                FailedJobRegistry,
                FinishedJobRegistry,
                ScheduledJobRegistry,
                StartedJobRegistry,
            )

            redis = Redis.from_url(self.config.secrets.read("redis_url"))
            redis.ping()
            queue = Queue("anhuan-f1-uploads", connection=redis)
            registries = (
                StartedJobRegistry(queue=queue),
                FinishedJobRegistry(queue=queue),
                FailedJobRegistry(queue=queue),
                DeferredJobRegistry(queue=queue),
                ScheduledJobRegistry(queue=queue),
            )
            return redis, queue, registries
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def rq_inventory(self) -> set[str]:
        redis, queue, registries = self._rq_components()
        jobs = set(queue.get_job_ids())
        for registry in registries:
            jobs.update(registry.get_job_ids(cleanup=False))
        for raw_key in redis.scan_iter(match="rq:job:*", count=100):
            key = raw_key.decode("utf-8", errors="strict")
            prefix, separator, job_id = key.partition("rq:job:")
            if prefix or not separator or not job_id:
                raise ReverseFailure("RQ_KEY_INVALID")
            jobs.add(job_id)
        return jobs

    def rq_execution_inventory(self) -> set[str]:
        redis, _queue, _registries = self._rq_components()
        executions: set[str] = set()
        for pattern in ("rq:execution:*", "rq:executions:*"):
            for raw_key in redis.scan_iter(match=pattern, count=100):
                key = raw_key.decode("utf-8", errors="strict")
                executions.add(_sha(key))
        return executions

    def redis_aux_inventory(self) -> set[str]:
        redis, _queue, _registries = self._rq_components()
        return {
            _sha(raw_key.decode("utf-8", errors="strict"))
            for raw_key in redis.scan_iter(match="f1-*", count=100)
        }

    def rq_digest(self) -> str:
        return _opaque_json(
            {
                "jobs": sorted(_sha(job_id) for job_id in self.rq_inventory()),
                "executions": sorted(self.rq_execution_inventory()),
                "f1_keys": sorted(self.redis_aux_inventory()),
            }
        )

    def ragflow_inventory(self) -> dict[str, Any]:
        try:
            from platform_foundation.f0j1.ragflow_client import RagFlowClient

            client = RagFlowClient(base_url=self.config.secrets.read("ragflow_base_url"))
            token = self.config.secrets.read("ragflow_api_key")
            datasets: dict[str, Any] = {}
            for dataset in client.list_datasets(token):
                dataset_id = str(dataset.get("id", ""))
                if not _is_remote_id(dataset_id):
                    raise ReverseFailure("RAGFLOW_ID_INVALID")
                documents: dict[str, Any] = {}
                for document in client.list_documents(token, dataset_id):
                    document_id = str(document.get("id", ""))
                    if not _is_remote_id(document_id):
                        raise ReverseFailure("RAGFLOW_ID_INVALID")
                    chunks: dict[str, dict[str, str]] = {}
                    for chunk in client.list_chunks(token, dataset_id, document_id):
                        chunk_id = str(chunk.get("id") or chunk.get("chunk_id") or "")
                        if not _is_remote_id(chunk_id):
                            raise ReverseFailure("RAGFLOW_ID_INVALID")
                        safe = dict(chunk)
                        content = safe.pop("content", None)
                        if isinstance(content, str):
                            safe["content_sha256"] = _sha(content)
                        canonical_id = _tag_value(chunk.get("tag_kwd"), "chunk_id")
                        chunks[chunk_id] = {
                            "meta": _opaque_json(safe),
                            "canonical_id_digest": _sha(canonical_id) if canonical_id else "",
                        }
                    safe_document = dict(document)
                    safe_document.pop("content", None)
                    documents[document_id] = {
                        "meta": _opaque_json(safe_document),
                        "name_digest": _sha(str(document.get("name", ""))),
                        "chunks": chunks,
                    }
                safe_dataset = dict(dataset)
                safe_dataset.pop("description", None)
                datasets[dataset_id] = {
                    "meta": _opaque_json(safe_dataset),
                    "name_digest": _sha(str(dataset.get("name", ""))),
                    "documents": documents,
                }
            return datasets
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def ragflow_digest(self) -> str:
        return _opaque_json(self.ragflow_inventory())

    def legacy_object_orphans(self) -> int:
        inventory = self.minio_inventory()
        with self.connect() as connection:
            referenced = {
                str(row[0])
                for row in connection.execute("SELECT object_key FROM f1.document").fetchall()
            }
        return len(set(inventory) - referenced)

    def legacy_rq_orphans(self) -> int:
        jobs = self.rq_inventory()
        with self.connect() as connection:
            represented = {
                str(row[0])
                for row in connection.execute(
                    "SELECT rq_job_id FROM f1.outbox WHERE rq_job_id IS NOT NULL"
                ).fetchall()
            }
        return len(jobs - represented)

    def index_duplicate_count(self, inventory: Mapping[str, Any] | None = None) -> int:
        if inventory is None:
            inventory = self.ragflow_inventory()
        duplicates = 0
        for dataset in inventory.values():
            names: list[str] = []
            for document in dataset["documents"].values():
                names.append(document["name_digest"])
                canonical_ids = [
                    chunk["canonical_id_digest"]
                    for chunk in document["chunks"].values()
                    if chunk["canonical_id_digest"]
                ]
                duplicates += len(document["chunks"]) - len(canonical_ids)
                duplicates += len(canonical_ids) - len(set(canonical_ids))
            duplicates += len(names) - len(set(names))
        return duplicates

    def register_document(self, document_id: str) -> dict[str, Any]:
        if not _is_uuid(document_id):
            raise ReverseFailure("DOCUMENT_ID_INVALID")
        deadline = time.monotonic() + 60
        row = None
        while time.monotonic() < deadline and row is None:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT d.id, d.object_key, t.id, t.source_etag, o.id, o.rq_job_id, "
                    "t.lease_token, t.enterprise_id FROM f1.document d "
                    "JOIN f1.upload_task t ON t.document_id=d.id "
                    "JOIN f1.outbox o ON o.task_id=t.id AND o.event_type='upload.dispatched' "
                    "WHERE d.id=%s",
                    (document_id,),
                ).fetchone()
            if row is None:
                time.sleep(1)
        if row is None:
            raise ReverseFailure("DOCUMENT_RESERVATION_MISSING")
        task_uuid = uuid.UUID(str(row[2]))
        object_key = str(row[1] or "")
        source_etag = str(row[3] or "")
        job_id = str(row[5] or "")
        if (
            not re.fullmatch(r"[0-9a-f]{32}\.(?:pdf|jpg)", object_key)
            or not object_key.startswith(task_uuid.hex)
            or not re.fullmatch(r"[0-9a-f]{32}(?:-[1-9][0-9]*)?", source_etag)
            or job_id != f"f1-upload-{task_uuid}"
        ):
            raise ReverseFailure("RUN_RESOURCE_IDENTITY_INVALID")
        for table, value in (("document", row[0]), ("upload_task", row[2]), ("outbox", row[4])):
            self.require_current_run_identity(table, value)
        self.registry.add_db("document", row[0])
        self.registry.add_db("upload_task", row[2])
        self.registry.add_db("outbox", row[4])
        self.registry.object_etags[object_key] = source_etag
        self.registry.rq_job_ids.add(job_id)
        return {
            "document_id": str(row[0]),
            "object_key": str(row[1]),
            "task_id": str(row[2]),
            "etag": source_etag,
            "outbox_id": str(row[4]),
            "job_id": job_id,
            "lease_token": str(row[6]) if row[6] else None,
            "enterprise_id": str(row[7]),
        }

    def document_effects(self, enterprise_id: uuid.UUID, sha256: str) -> tuple[int, int, int, int]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT count(DISTINCT d.id), count(DISTINCT t.id), "
                "count(DISTINCT o.id), count(DISTINCT d.object_key) "
                "FROM f1.document d JOIN f1.upload_task t ON t.document_id=d.id "
                "JOIN f1.outbox o ON o.task_id=t.id "
                "WHERE d.enterprise_id=%s AND t.content_sha256=%s",
                (str(enterprise_id), sha256),
            ).fetchone()
        return tuple(int(value) for value in row)

    def membership_count(self, enterprise_id: uuid.UUID, sub: str) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT count(*) FROM f1.enterprise_user "
                    "AS eu JOIN f1.user_profile AS up ON up.id=eu.user_id "
                    "WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s",
                    (str(enterprise_id), sub),
                ).fetchone()[0]
            )

    def membership_role(self, enterprise_id: uuid.UUID, sub: str) -> str:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT eu.role FROM f1.enterprise_user AS eu "
                "JOIN f1.user_profile AS up ON up.id=eu.user_id "
                "WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s",
                (str(enterprise_id), sub),
            ).fetchall()
        if len(rows) != 1:
            return ""
        return str(rows[0][0])

    def wait_for_lease(self, task_id: str) -> str:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT lease_token FROM f1.upload_task WHERE id=%s",
                    (task_id,),
                ).fetchone()
            if row and row[0]:
                return str(row[0])
            time.sleep(1)
        raise ReverseFailure("LEASE_NOT_OBSERVED")

    def wait_task_reason(self, task_id: str, reason: str, *, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT error_reason FROM f1.upload_task WHERE id=%s",
                    (task_id,),
                ).fetchone()
            if row and row[0] == reason:
                return True
            time.sleep(1)
        return False

    def wait_outbox_pending(self, outbox_id: str, *, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.connect() as connection:
                row = connection.execute(
                    "SELECT state, dispatched_at FROM f1.outbox WHERE id=%s",
                    (outbox_id,),
                ).fetchone()
            if row is not None and row[0] == "pending" and row[1] is None:
                return True
            time.sleep(1)
        return False

    def dispatch_effects(self, task_id: str) -> tuple[int, int, int]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT count(*), count(DISTINCT rq_job_id), "
                "coalesce(max(dispatch_attempt), 0) FROM f1.outbox "
                "WHERE task_id=%s AND event_type='upload.dispatched'",
                (task_id,),
            ).fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def run_dispatch_duplicate_count(self) -> int:
        task_ids = sorted(self.registry.db_ids.get("upload_task", set()))
        if not task_ids:
            return 1
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT task_id::text, count(*), count(DISTINCT rq_job_id) "
                "FROM f1.outbox WHERE task_id=ANY(%s::uuid[]) "
                "AND event_type='upload.dispatched' GROUP BY task_id",
                (task_ids,),
            ).fetchall()
        observed = {str(row[0]): (int(row[1]), int(row[2])) for row in rows}
        return sum(observed.get(task_id) != (1, 1) for task_id in task_ids)

    def rq_payload_is_task_only(self, job_id: str, task_id: str) -> bool:
        try:
            from rq.job import Job

            redis, _queue, _registries = self._rq_components()
            job = Job.fetch(job_id, connection=redis)
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        return (
            str(job.func_name)
            == "platform_foundation.f1.upload_task.run_upload_pipeline"
            and tuple(job.args) == (task_id,)
            and dict(job.kwargs) == {}
        )

    def expire_lease(self, task_id: str, token: str) -> None:
        with self.connect() as connection:
            changed = connection.execute(
                "UPDATE f1.upload_task SET lease_until=statement_timestamp()-interval '1 second' "
                "WHERE id=%s AND lease_token=%s",
                (task_id, token),
            ).rowcount
            connection.commit()
        if changed != 1:
            raise ReverseFailure("LEASE_FAULT_INJECTION_FAILED")

    def stale_lease_rejected(self, task: Mapping[str, Any], stale_token: str) -> bool:
        try:
            import psycopg

            with psycopg.connect(self._worker_dsn) as connection:
                connection.execute(
                    "SELECT set_config('f1.enterprise_id', %s, true)",
                    (task["enterprise_id"],),
                )
                connection.execute(
                    "SELECT set_config('f1.task_id', %s, true)", (task["task_id"],)
                )
                connection.execute(
                    "SELECT set_config('f1.lease_token', %s, true)", (stale_token,)
                )
                renewed = bool(
                    connection.execute(
                        "SELECT f1.renew_upload_lease(%s, %s, %s)",
                        (task["task_id"], stale_token, 30),
                    ).fetchone()[0]
                )
                changed = connection.execute(
                    "UPDATE f1.upload_task SET status='done' "
                    "WHERE id=%s AND lease_token=%s",
                    (task["task_id"], stale_token),
                ).rowcount
                connection.rollback()
            return not renewed and changed == 0
        except (psycopg.OperationalError, psycopg.InterfaceError):
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None
        except psycopg.Error:
            raise ReverseFailure("STALE_LEASE_PROBE_SQL_ERROR") from None

    def register_invite(self, token: str, enterprise_id: uuid.UUID, invitee_sub: str) -> str:
        claims = _decode_claims(token)
        jti = str(claims.get("jti", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", jti):
            raise ReverseFailure("INVITE_JTI_INVALID")
        self.require_current_run_identity("invite_jti", jti)
        self.registry.add_db("invite_jti", jti)
        self.registry.invite_memberships.add((str(enterprise_id), invitee_sub))
        return jti

    def register_invite_effects(self, enterprise_id: uuid.UUID, invitee_sub: str) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT eu.id, up.id FROM f1.enterprise_user AS eu "
                "JOIN f1.user_profile AS up ON up.id=eu.user_id "
                "WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s",
                (str(enterprise_id), invitee_sub),
            ).fetchone()
        if row is None:
            raise ReverseFailure("INVITE_MEMBERSHIP_MISSING")
        self.require_current_run_identity("enterprise_user", row[0])
        self.registry.add_db("enterprise_user", row[0])

    def profile_id(self, sub: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM f1.user_profile WHERE keycloak_sub=%s", (sub,)
            ).fetchone()
        return str(row[0]) if row else None

    def register_new_profile(self, sub: str) -> None:
        profile_id = self.profile_id(sub)
        if profile_id is None:
            raise ReverseFailure("INVITE_PROFILE_MISSING")
        self.require_current_run_identity("user_profile", profile_id)
        self.registry.add_db("user_profile", profile_id)

    def refresh_run_relations(self) -> None:
        task_ids = sorted(self.registry.db_ids.get("upload_task", set()))
        if task_ids:
            with self.connect() as connection:
                rows = connection.execute(
                    "SELECT id, task_id, rq_job_id FROM f1.outbox "
                    "WHERE task_id=ANY(%s::uuid[])",
                    (task_ids,),
                ).fetchall()
            for identifier, task_id, job_id in rows:
                self.require_current_run_identity("outbox", identifier)
                self.registry.add_db("outbox", identifier)
                if job_id:
                    expected = {
                        f"f1-upload-{task_id}",
                        f"f1-indexing-{task_id}",
                        f"f1-failed-{task_id}",
                    }
                    if str(job_id) not in expected:
                        raise ReverseFailure("RUN_RESOURCE_IDENTITY_INVALID")
                    self.registry.rq_job_ids.add(str(job_id))
        self.register_run_audits()

    def register_run_audits(self) -> set[str]:
        resources: set[str] = set()
        for table in ("document", "upload_task", "qa_request", "invite_jti"):
            resources.update(self.registry.db_ids.get(table, set()))
        if not resources:
            return set()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM f1.audit_log WHERE resource_id=ANY(%s::text[])",
                (list(resources),),
            ).fetchall()
        for (identifier,) in rows:
            self.require_current_run_identity("audit_log", identifier)
            self.registry.add_db("audit_log", identifier)
        return {str(row[0]) for row in rows}

    def audit_gap_count(self) -> int:
        expected: list[tuple[str, str]] = []
        expected.extend(("document.upload", value) for value in self.registry.db_ids.get("document", set()))
        expected.extend(("document.index", value) for value in self.registry.db_ids.get("upload_task", set()))
        with self.connect() as connection:
            for value in self.registry.db_ids.get("qa_request", set()):
                exists = int(
                    connection.execute(
                        "SELECT count(*) FROM f1.qa_request WHERE request_id=%s",
                        (value,),
                    ).fetchone()[0]
                )
                if exists:
                    expected.append(("qa.complete", value))
        for value in self.registry.db_ids.get("invite_jti", set()):
            expected.extend((("invite.create", value), ("invite.consume", value)))
        gaps = 0
        with self.connect() as connection:
            for action, resource_id in expected:
                count = int(
                    connection.execute(
                        "SELECT count(*) FROM f1.audit_log WHERE action=%s AND resource_id=%s",
                        (action, resource_id),
                    ).fetchone()[0]
                )
                if count < 1:
                    gaps += 1
        return gaps

    def citation_crosswire_count(
        self,
        citations: Sequence[Mapping[str, Any]],
        fixture_sha256: str,
    ) -> int:
        if not citations:
            return 1
        with self.connect() as connection:
            scope_rows = connection.execute(
                "SELECT id, enterprise_id FROM f0i.document_scope "
                "WHERE source_object_sha256=%s AND terminal_status='CANONICAL_SCOPE_INCLUDED'",
                (fixture_sha256,),
            ).fetchall()
            if len(scope_rows) != 1:
                return 1
            scope_id, tenant_id = str(scope_rows[0][0]), str(scope_rows[0][1])
            bad = 0
            seen: set[str] = set()
            for citation in citations:
                chunk_id = str(citation.get("chunk_id", ""))
                if not _is_uuid(chunk_id) or chunk_id in seen:
                    bad += 1
                    continue
                seen.add(chunk_id)
                row = connection.execute(
                    "SELECT c.document_scope_id, c.enterprise_id, "
                    "ARRAY(SELECT DISTINCT b.page_no FROM f0i.chunk_block_link AS l "
                    "JOIN f0i.block AS b ON b.enterprise_id=l.enterprise_id AND b.id=l.block_id "
                    "WHERE l.enterprise_id=c.enterprise_id AND l.chunk_id=c.id "
                    "AND b.page_no IS NOT NULL ORDER BY 1), c.body_plaintext_sha256 "
                    "FROM f0i.chunk AS c WHERE c.id=%s",
                    (chunk_id,),
                ).fetchone()
                cited_pages = citation.get("pages")
                if (
                    row is None
                    or str(row[0]) != scope_id
                    or str(row[1]) != tenant_id
                    or str(citation.get("document_id", "")) != scope_id
                    or str(citation.get("tenant_id", "")) != tenant_id
                    or str(citation.get("body_sha256", "")) != str(row[3])
                    or not isinstance(cited_pages, list)
                    or not cited_pages
                    or not set(cited_pages).issubset(set(row[2] or []))
                ):
                    bad += 1
            return bad

    def tenant_crosswire_count(self) -> int:
        with self.connect() as connection:
            checks = (
                "SELECT count(*) FROM f1.document d JOIN f1.upload_task t ON t.document_id=d.id "
                "WHERE d.enterprise_id<>t.enterprise_id",
                "SELECT count(*) FROM f1.upload_task t JOIN f1.outbox o ON o.task_id=t.id "
                "WHERE t.enterprise_id<>o.enterprise_id",
                "SELECT count(*) FROM f1.document d JOIN f1.plant p ON p.id=d.plant_id "
                "WHERE d.plant_id IS NOT NULL AND d.enterprise_id<>p.enterprise_id",
            )
            return sum(int(connection.execute(sql).fetchone()[0]) for sql in checks)

    def plaintext_leaks(self, canaries: Iterable[bytes], logs: bytes) -> int:
        needles: list[bytes] = []
        for item in canaries:
            if len(item) < 8:
                continue
            needles.append(item)
            try:
                decoded = item.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            escaped = json.dumps(decoded, ensure_ascii=True)[1:-1].encode("ascii")
            encoded = urllib.parse.quote(decoded, safe="").encode("ascii")
            needles.extend(value for value in (escaped, encoded) if len(value) >= 8)
        if any(needle in logs for needle in needles):
            return 1
        with self.connect() as connection:
            qa_rows = connection.execute(
                "SELECT row_to_json(q)::text FROM f1.qa_request q "
                "WHERE request_id=ANY(%s::uuid[])",
                (list(self.registry.db_ids.get("qa_request", set())),),
            ).fetchall()
            audit_rows = connection.execute(
                "SELECT row_to_json(a)::text FROM f1.audit_log a "
                "WHERE id=ANY(%s::uuid[])",
                (list(self.registry.db_ids.get("audit_log", set())),),
            ).fetchall()
        raw = b"\x00".join(
            str(row[0]).encode("utf-8") for row in (*qa_rows, *audit_rows)
        )
        if any(needle in raw for needle in needles):
            return 1
        roots = (
            ROOT / "artifacts/f1-platform-shell/v0.3",
            ROOT / "infra/f1/ragflow/logs",
            ROOT / "infra/f1/otel/logs",
        )
        for root in roots:
            if not root.exists():
                continue
            for candidate in root.rglob("*"):
                if (
                    not candidate.is_file()
                    or candidate.is_symlink()
                    or candidate.name.startswith(".env")
                ):
                    continue
                if candidate.stat().st_size > 16 * 1024 * 1024:
                    return 1
                observed = candidate.read_bytes()
                if any(needle in observed for needle in needles):
                    return 1
        return 0

    def run_object_orphan_delta(self, baseline: int) -> int:
        return current_run_delta(baseline, self.legacy_object_orphans())

    def run_rq_orphan_delta(self, baseline: int) -> int:
        return current_run_delta(baseline, self.legacy_rq_orphans())

    def run_index_duplicate_delta(self, baseline: int) -> int:
        return current_run_delta(baseline, self.index_duplicate_count())

    def discover_remote_additions(self, baseline: Mapping[str, Any]) -> None:
        current = self.ragflow_inventory()
        for dataset_id, dataset in current.items():
            if dataset_id not in baseline:
                self.registry.ragflow_dataset_ids.add(dataset_id)
                continue
            old_documents = baseline.get(dataset_id, {}).get("documents", {})
            for document_id, document in dataset["documents"].items():
                if document_id not in old_documents:
                    self.registry.ragflow_document_ids.setdefault(dataset_id, set()).add(document_id)
                    continue
                old_chunks = old_documents[document_id].get("chunks", {})
                additions = set(document["chunks"]) - set(old_chunks)
                if additions:
                    self.registry.ragflow_chunk_ids.setdefault(
                        (dataset_id, document_id), set()
                    ).update(additions)

    def remove_ragflow_resources(self) -> None:
        try:
            from platform_foundation.f0j1.ragflow_client import RagFlowClient

            client = RagFlowClient(base_url=self.config.secrets.read("ragflow_base_url"))
            token = self.config.secrets.read("ragflow_api_key")
            for (dataset_id, document_id), identifiers in self.registry.ragflow_chunk_ids.items():
                if identifiers:
                    accepted = client.delete_chunks(
                        token, dataset_id, document_id, sorted(identifiers)
                    )
                    remaining = {
                        str(chunk.get("id") or chunk.get("chunk_id") or "")
                        for chunk in client.list_chunks(token, dataset_id, document_id)
                    }
                    if not accepted or remaining.intersection(identifiers):
                        raise ReverseFailure("RAGFLOW_CLEANUP_MISMATCH")
            for dataset_id, identifiers in self.registry.ragflow_document_ids.items():
                if identifiers:
                    deleted = client.delete_documents(token, dataset_id, sorted(identifiers))
                    if deleted != len(identifiers):
                        raise ReverseFailure("RAGFLOW_CLEANUP_MISMATCH")
            if self.registry.ragflow_dataset_ids:
                deleted = client.delete_datasets(
                    token, sorted(self.registry.ragflow_dataset_ids)
                )
                if deleted != len(self.registry.ragflow_dataset_ids):
                    raise ReverseFailure("RAGFLOW_CLEANUP_MISMATCH")
        except ReverseFailure:
            raise
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def remove_rq_resources(self) -> None:
        _redis, queue, registries = self._rq_components()
        for job_id in sorted(self.registry.rq_job_ids):
            for registry in registries:
                try:
                    registry.remove(job_id, delete_job=False)
                except Exception:
                    raise ReverseFailure("RQ_CLEANUP_MISMATCH") from None
            try:
                queue.remove(job_id, delete_job=True)
            except Exception:
                try:
                    from rq.job import Job

                    Job.fetch(job_id, connection=queue.connection).delete()
                except Exception:
                    if job_id in self.rq_inventory():
                        raise ReverseFailure("RQ_CLEANUP_MISMATCH") from None

    def remove_minio_resources(self) -> None:
        try:
            from minio import Minio

            client = Minio(
                self.config.secrets.read("minio_endpoint"),
                access_key=self.config.secrets.read("minio_access_key"),
                secret_key=self.config.secrets.read("minio_secret_key"),
                secure=self.config.secrets.read("minio_secure").lower() == "true",
            )
            bucket = self.config.secrets.read("minio_bucket")
            for object_key, expected_etag in self.registry.object_etags.items():
                observed = client.stat_object(bucket, object_key)
                if str(observed.etag or "") != expected_etag:
                    raise ReverseFailure("OBJECT_ETAG_MISMATCH")
                client.remove_object(bucket, object_key)
        except ReverseFailure:
            raise
        except Exception:
            raise DependencyFailure("DEPENDENCY_UNREACHABLE") from None

    def remove_database_resources(self) -> None:
        identifiers = self.registry.db_ids
        memberships = self.registry.invite_memberships
        with self.connect() as connection:
            is_superuser = connection.execute("SELECT current_setting('is_superuser')").fetchone()[0]
            if str(is_superuser).lower() != "on":
                raise ReverseFailure("SCRATCH_CONTROL_ROLE_INSUFFICIENT")
            connection.execute("SET LOCAL session_replication_role = 'replica'")
            for table in ("audit_log", "outbox", "qa_request", "upload_task", "document", "invite_jti"):
                values = sorted(identifiers.get(table, set()))
                if not values:
                    continue
                key = "request_id" if table == "qa_request" else "id"
                if table == "invite_jti":
                    key = "jti"
                array_type = "text" if table == "invite_jti" else "uuid"
                connection.execute(
                    f"DELETE FROM f1.{table} WHERE {key}=ANY(%s::{array_type}[])",
                    (values,),
                )
            for enterprise_id, sub in sorted(memberships):
                connection.execute(
                    "DELETE FROM f1.enterprise_user AS eu USING f1.user_profile AS up "
                    "WHERE eu.user_id=up.id AND eu.enterprise_id=%s AND up.keycloak_sub=%s",
                    (enterprise_id, sub),
                )
            for profile_id in sorted(identifiers.get("user_profile", set())):
                connection.execute(
                    "DELETE FROM f1.user_profile WHERE id=%s AND NOT EXISTS "
                    "(SELECT 1 FROM f1.enterprise_user WHERE user_id=%s)",
                    (profile_id, profile_id),
                )
            connection.commit()


def snapshot(control: ControlPlane, services: ScratchServiceController) -> Snapshot:
    """Capture exact digests for DB, MinIO, RQ, RAGFlow and audit."""
    ragflow_inventory = control.ragflow_inventory()
    planes = {
        "database": control.database_digest(),
        "minio": control.minio_digest(),
        "rq": control.rq_digest(),
        "ragflow": _opaque_json(ragflow_inventory),
        "audit": control.audit_digest(),
        "services": services.state_digest(),
    }
    return Snapshot(
        planes=tuple(sorted(planes.items())),
        database_identities=control.database_identities(),
        ragflow_inventory_state=ragflow_inventory,
        legacy_object_orphans=control.legacy_object_orphans(),
        legacy_rq_orphans=control.legacy_rq_orphans(),
        index_duplicate_count=control.index_duplicate_count(ragflow_inventory),
        upstream_digest=control.upstream_digest(),
    )


def cleanup(verifier: "Verifier", registry: ResourceRegistry) -> None:
    """Remove only this random run and require identity/etag guarded deletes."""
    if not registry.run_id.startswith(SCRATCH_PROJECT_PREFIX):
        raise ReverseFailure("UNSAFE_CLEANUP_SCOPE")
    verifier.control.refresh_run_relations()
    if registry.db_ids.get("document") or registry.db_ids.get("upload_task"):
        verifier.control.discover_remote_additions(verifier.baseline_ragflow_inventory)
    verifier.control.remove_ragflow_resources()
    verifier.control.remove_rq_resources()
    for expected_etag in registry.object_etags.values():
        if not expected_etag:
            raise ReverseFailure("OBJECT_ETAG_MISSING")
    verifier.control.remove_database_resources()
    verifier.control.remove_minio_resources()


class ProbeSuite:
    def __init__(self, verifier: "Verifier") -> None:
        self.verifier = verifier
        self.config = verifier.config
        self.registry = verifier.registry
        self.control = verifier.control
        self.http = verifier.http
        self.services = verifier.services
        self.question = self.config.secrets.read("question_primary")
        self.alternate_question = self.config.secrets.read("question_alternate")
        if self.question == self.alternate_question:
            raise ReverseFailure("QUESTION_PAIR_INVALID")
        self.primary_document_id: str | None = None
        self.primary_citations: list[Mapping[str, Any]] = []

    @staticmethod
    def _zero(condition: bool) -> int:
        return 0 if condition else 1

    def _upload_and_register(self, fixture: Fixture) -> tuple[int, dict, dict[str, Any]]:
        status, payload = self.http.upload(fixture)
        document_id = str(payload.get("id", ""))
        if status not in {200, 201} or not _is_uuid(document_id):
            raise ReverseFailure("HTTP_UPLOAD_FAILED")
        task = self.control.register_document(document_id)
        return status, payload, task

    def membership_mint(self) -> int:
        token_b = self.http.token("tenant_b")
        sub_b = str(_decode_claims(token_b).get("sub", ""))
        if not sub_b or self.control.membership_count(self.config.enterprise_a, sub_b) != 0:
            return 1
        status_read, _ = self.http.request(
            "GET",
            f"/api/v1/enterprises/{self.config.enterprise_a}",
            "tenant_b",
            enterprise_id=self.config.enterprise_a,
        )
        status_write, payload = self.http.request(
            "POST",
            "/api/v1/invitations",
            "tenant_b",
            enterprise_id=self.config.enterprise_a,
            body={
                "email": self.config.secrets.read("invitee_email"),
                "role": "partner",
            },
        )
        if status_write == 201 and isinstance(payload, dict) and payload.get("token"):
            invitee_sub = str(_decode_claims(self.http.token("invitee")).get("sub", ""))
            self.control.register_invite(str(payload["token"]), self.config.enterprise_a, invitee_sub)
        return self._zero(
            status_read == 404
            and status_write == 404
            and self.control.membership_count(self.config.enterprise_a, sub_b) == 0
        )

    def invite_double_consume(self) -> int:
        invitee_token = self.http.token("invitee", fresh=True)
        invitee_sub = str(_decode_claims(invitee_token).get("sub", ""))
        if not invitee_sub or self.control.membership_count(self.config.enterprise_a, invitee_sub) != 0:
            return 1
        profile_before = self.control.profile_id(invitee_sub)
        status, created = self.http.request(
            "POST",
            "/api/v1/invitations",
            "tenant_a",
            enterprise_id=self.config.enterprise_a,
            body={
                "email": self.config.secrets.read("invitee_email"),
                "role": "partner",
            },
        )
        token = str(created.get("token", "")) if isinstance(created, dict) else ""
        if status != 201 or not token:
            return 1
        jti = self.control.register_invite(
            token, self.config.enterprise_a, invitee_sub
        )

        def consume() -> tuple[int, Any]:
            return self.http.request(
                "POST",
                "/api/v1/invitations/consume",
                "invitee",
                body=invite_consume_probe_payload(token, self.registry.run_id),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result() for future in (executor.submit(consume), executor.submit(consume))]
        statuses = sorted(status for status, _payload in results)
        membership = self.control.membership_count(self.config.enterprise_a, invitee_sub)
        with self.control.connect() as connection:
            ledger = connection.execute(
                "SELECT role, consumed_at IS NOT NULL, consumed_by_sub "
                "FROM f1.invite_jti WHERE jti=%s",
                (jti,),
            ).fetchone()
        if membership == 1:
            self.control.register_invite_effects(self.config.enterprise_a, invitee_sub)
            if profile_before is None:
                self.control.register_new_profile(invitee_sub)
        return self._zero(
            statuses == [200, 409]
            and membership == 1
            and ledger is not None
            and ledger[0] == "partner"
            and bool(ledger[1])
            and str(ledger[2]) == invitee_sub
        )

    def ragflow_recovery_and_valid_http(self) -> tuple[int, int]:
        fixture = self.config.fixtures[0]
        self.services.pause("ragflow")
        try:
            _status, _payload, task = self._upload_and_register(fixture)
            outage_observed = self.control.wait_task_reason(
                task["task_id"], "RAGFLOW_UNAVAILABLE", timeout=180
            )
        finally:
            self.services.unpause("ragflow")
        recovered = self.http.wait_document(task["document_id"], {"done"})
        self.primary_document_id = task["document_id"]
        request_id = uuid.uuid4()
        self.control.require_current_run_identity("qa_request", request_id)
        self.registry.add_db("qa_request", request_id)
        status, response = self.http.request(
            "POST",
            "/api/v1/qa",
            "tenant_a",
            enterprise_id=self.config.enterprise_a,
            body={
                "question": self.question,
                "enterprise_id": str(self.config.enterprise_a),
                "request_id": str(request_id),
            },
            timeout=self.config.timeout_seconds,
        )
        citations = response.get("citations") if isinstance(response, dict) else None
        self.primary_citations = citations if isinstance(citations, list) else []
        answer = response.get("answer") if isinstance(response, dict) else None
        citation_ids = {
            str(citation.get("chunk_id", ""))
            for citation in self.primary_citations
            if isinstance(citation, dict)
        }
        valid = (
            recovered
            and status == 200
            and isinstance(response, dict)
            and isinstance(answer, str)
            and bool(answer)
            and bool(self.primary_citations)
            and citation_ids == _answer_reference_ids(answer)
        )
        return self._zero(valid), self._zero(outage_observed and recovered)

    def enqueue_recovery(self) -> int:
        fixture = self.config.fixtures[1]
        self.services.pause("redis")
        try:
            _status, _payload, task = self._upload_and_register(fixture)
            outage_observed = self.control.wait_outbox_pending(
                task["outbox_id"], timeout=60
            )
        finally:
            self.services.unpause("redis")
        recovered = self.http.wait_document(task["document_id"], {"done"})
        return self._zero(outage_observed and recovered)

    def worker_restart_and_stale_lease(self) -> tuple[int, int]:
        fixture = self.config.fixtures[2]
        worker_maybe_paused = True
        ragflow_maybe_paused = False
        stale_rejected = False
        try:
            self.services.pause("worker")
            ragflow_maybe_paused = True
            self.services.pause("ragflow")
            _status, _payload, task = self._upload_and_register(fixture)
            self.services.unpause("worker")
            worker_maybe_paused = False
            stale_token = self.control.wait_for_lease(task["task_id"])
            self.services.signal_worker()
            self.control.expire_lease(task["task_id"], stale_token)
            stale_rejected = self.control.stale_lease_rejected(task, stale_token)
        finally:
            if worker_maybe_paused:
                self.services.unpause("worker")
            if ragflow_maybe_paused:
                self.services.unpause("ragflow")
            self.services.ensure_worker()
        recovered = self.http.wait_document(task["document_id"], {"done"})
        with self.control.connect() as connection:
            attempts = int(
                connection.execute(
                    "SELECT attempt FROM f1.upload_task WHERE id=%s",
                    (task["task_id"],),
                ).fetchone()[0]
            )
        return self._zero(recovered and attempts >= 2), self._zero(stale_rejected)

    def upload_replay_and_dispatch(self) -> tuple[int, int]:
        fixture = self.config.fixtures[3]
        self.services.pause("worker")
        try:
            first_status, first_payload, task = self._upload_and_register(fixture)
            before = self.control.document_effects(self.config.enterprise_a, fixture.sha256)
            second_status, second_payload = self.http.upload(fixture)
            after = self.control.document_effects(self.config.enterprise_a, fixture.sha256)
        finally:
            self.services.unpause("worker")
        same_document = str(first_payload.get("id")) == str(second_payload.get("id"))
        dispatch_count, distinct_jobs, dispatch_attempts = self.control.dispatch_effects(
            task["task_id"]
        )
        terminal = self.http.wait_document(task["document_id"], {"done"})
        replay = (
            first_status in {200, 201}
            and second_status in {200, 201}
            and same_document
            and before == after
            and before == (1, 1, 1, 1)
            and terminal
        )
        dispatch = (
            dispatch_count == 1
            and distinct_jobs == 1
            and dispatch_attempts == 1
            and self.control.rq_payload_is_task_only(
                task["job_id"], task["task_id"]
            )
        )
        return self._zero(replay), self._zero(dispatch)

    def qa_request_races(self) -> int:
        request_id = uuid.uuid4()
        self.control.require_current_run_identity("qa_request", request_id)
        self.registry.add_db("qa_request", request_id)
        body = {
            "question": self.question,
            "enterprise_id": str(self.config.enterprise_a),
            "request_id": str(request_id),
        }

        def ask(payload: Mapping[str, Any]) -> tuple[int, Any]:
            return self.http.request(
                "POST",
                "/api/v1/qa",
                "tenant_a",
                enterprise_id=self.config.enterprise_a,
                body=dict(payload),
                timeout=self.config.timeout_seconds,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result() for future in (executor.submit(ask, body), executor.submit(ask, body))]
        if any(status not in {200, 202} for status, _payload in results):
            return 1
        replay_status, replay = ask(body)
        conflict_body = dict(body)
        conflict_body["question"] = self.alternate_question
        conflict_status, _ = ask(conflict_body)
        terminal = [payload for status, payload in results if status == 200]
        terminal.append(replay if replay_status == 200 else {})
        digests = {
            _opaque_json(
                {
                    "answer": payload.get("answer"),
                    "citations": payload.get("citations"),
                    "refusal_reason": payload.get("refusal_reason"),
                }
            )
            for payload in terminal
            if isinstance(payload, dict) and payload
        }
        with self.control.connect() as connection:
            row = connection.execute(
                "SELECT status, attempt FROM f1.qa_request WHERE request_id=%s",
                (str(request_id),),
            ).fetchone()
        return self._zero(
            replay_status == 200
            and conflict_status == 409
            and len(digests) == 1
            and row is not None
            and row[0] in {"done", "refused"}
            and int(row[1]) == 1
        )

    def citation_and_tenant_crosswires(self) -> tuple[int, int]:
        citation_bad = self.control.citation_crosswire_count(
            self.primary_citations, self.config.fixtures[0].sha256
        )
        cross_request_id = uuid.uuid4()
        self.control.require_current_run_identity("qa_request", cross_request_id)
        self.registry.add_db("qa_request", cross_request_id)
        cross_status, _ = self.http.request(
            "POST",
            "/api/v1/qa",
            "tenant_b",
            enterprise_id=self.config.enterprise_a,
            body={
                "question": self.question,
                "enterprise_id": str(self.config.enterprise_a),
                "request_id": str(cross_request_id),
            },
        )
        docs_status, docs = self.http.documents(
            "tenant_b", enterprise=self.config.enterprise_a
        )
        tenant_bad = self.control.tenant_crosswire_count()
        return (
            self._zero(citation_bad == 0 and cross_status == 404),
            self._zero(tenant_bad == 0 and docs_status == 404 and docs == []),
        )

    def run(self) -> dict[str, int]:
        results = dict.fromkeys(METRICS, 1)
        results["membership_mint"] = self.membership_mint()
        results["invite_double_consume"] = self.invite_double_consume()
        valid, ragflow = self.ragflow_recovery_and_valid_http()
        results["valid_http_e2e"] = valid
        results["ragflow_recovery"] = ragflow
        results["enqueue_recovery"] = self.enqueue_recovery()
        worker, stale = self.worker_restart_and_stale_lease()
        results["worker_restart"] = worker
        results["stale_lease_commit"] = stale
        replay, dispatch = self.upload_replay_and_dispatch()
        results["upload_replay_effects"] = replay
        results["duplicate_dispatch"] = dispatch
        results["qa_request_races"] = self.qa_request_races()
        citation, tenant = self.citation_and_tenant_crosswires()
        results["citation_crosswires"] = citation
        results["tenant_crosswires"] = tenant
        self.control.refresh_run_relations()
        results["duplicate_dispatch"] += self.control.run_dispatch_duplicate_count()
        audit_status, audit_rows = self.http.audit()
        tenant_b_sub = str(
            _decode_claims(self.http.token("tenant_b")).get("sub", "")
        )
        tenant_b_role = self.control.membership_role(
            self.config.enterprise_b, tenant_b_sub
        )
        enterprise_admin_audit_status, _ = self.http.audit(
            "tenant_b", enterprise=self.config.enterprise_b
        )
        observed_audit_ids = {
            str(row.get("id")) for row in audit_rows if isinstance(row, dict)
        }
        expected_audit_ids = self.registry.db_ids.get("audit_log", set())
        http_audit_gaps = len(expected_audit_ids - observed_audit_ids)
        results["audit_gaps"] = (
            self.control.audit_gap_count()
            + audit_gate_failure_count(
                auditor_status=audit_status,
                enterprise_admin_status=enterprise_admin_audit_status,
                observed_role=tenant_b_role,
            )
            + http_audit_gaps
        )
        results["object_orphans_delta"] = self.control.run_object_orphan_delta(
            self.verifier.before.legacy_object_orphans
        )
        results["rq_orphans_delta"] = self.control.run_rq_orphan_delta(
            self.verifier.before.legacy_rq_orphans
        )
        results["index_duplicates"] = self.control.run_index_duplicate_delta(
            self.verifier.before.index_duplicate_count
        )
        fixture_canaries: list[bytes] = []
        for fixture in self.config.fixtures:
            body = fixture.body()
            if len(body) >= 64:
                start = max(0, len(body) // 2 - 32)
                fixture_canaries.append(body[start : start + 64])
        canaries = (
            self.question.encode("utf-8"),
            self.alternate_question.encode("utf-8"),
            *fixture_canaries,
            *self.config.leak_canaries,
            *self.http.token_canaries(),
            str(ROOT).encode("utf-8"),
            ("----f111" + self.registry.run_id[-24:]).encode("ascii"),
            (self.registry.run_id[-32:] + ".pdf").encode("ascii"),
            (self.registry.run_id[-32:] + ".jpg").encode("ascii"),
            *(str(fixture.location).encode("utf-8") for fixture in self.config.fixtures),
            *(fixture.location.name.encode("utf-8") for fixture in self.config.fixtures),
        )
        results["new_plaintext_leaks"] = self.control.plaintext_leaks(
            canaries, self.services.logs() + self.services.traces()
        )
        results["upstream_mutations"] = self._zero(
            self.control.upstream_digest() == self.verifier.before.upstream_digest
        )
        return results


class Verifier:
    def __init__(self, config: ReverseConfig, registry: ResourceRegistry) -> None:
        self.config = config
        self.registry = registry
        self.control = ControlPlane(config, registry)
        self.http = HttpBusinessClient(config, registry)
        self.services = ScratchServiceController(config)
        self.services.validate_isolation()
        self.control.validate_database_scope(self.services.database_port)
        self.before: Snapshot | None = None
        self.baseline_ragflow_inventory: dict[str, Any] = {}
        self.cleanup_authorized = False

    def snapshot(self) -> Snapshot:
        return snapshot(self.control, self.services)

    def bind_baseline(self, baseline: Snapshot) -> None:
        self.before = baseline
        self.control.bind_baseline_identities(baseline)
        self.baseline_ragflow_inventory = dict(baseline.ragflow_inventory_state)
        self.cleanup_authorized = True

    def preclean_guard(self, baseline: Snapshot) -> int:
        """Prove no cleanup/mutation occurred before the first evidence point."""
        immediate = self.snapshot()
        if immediate != baseline:
            return 1
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        main_node = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main"
            ),
            None,
        )
        if main_node is None:
            return 1
        segment = ast.get_source_segment(source, main_node) or ""
        marker = "before = verifier.snapshot("
        if marker not in segment:
            return 1
        prefix = segment[: segment.index(marker)]
        if re.search(r"\b(?:DELETE|TRUNCATE|reset|cleanup)\b", prefix, re.IGNORECASE):
            return 1
        return 0

    def run_probes(self) -> dict[str, int]:
        if self.before is None:
            raise ReverseFailure("BASELINE_NOT_BOUND")
        return ProbeSuite(self).run()

    def cleanup(self) -> None:
        if not self.cleanup_authorized or self.before is None:
            raise ReverseFailure("CLEANUP_BEFORE_BASELINE_FORBIDDEN")
        cleanup(self, self.registry)


def main() -> int:
    metrics = dict.fromkeys(METRICS, 1)
    verifier: Verifier | None = None
    before: Snapshot | None = None
    fatal = False
    try:
        config = ReverseConfig.from_environment()
        registry = ResourceRegistry(run_id=config.project)
        verifier = Verifier(config, registry)
        before = verifier.snapshot()
        metrics["preclean_mutations"] = verifier.preclean_guard(before)
        if metrics["preclean_mutations"] != 0:
            raise ReverseFailure("PRECLEAN_MUTATION_DETECTED")
        verifier.bind_baseline(before)
        metrics.update(verifier.run_probes())
        metrics["preclean_mutations"] = 0
    except Exception:
        fatal = True
    finally:
        if (
            verifier is not None
            and before is not None
            and verifier.cleanup_authorized
        ):
            try:
                verifier.cleanup()
                after_cleanup = verifier.snapshot()
                if after_cleanup != before:
                    metrics["scratch_residuals"] = 1
                else:
                    metrics["scratch_residuals"] = 0
            except Exception:
                metrics["scratch_residuals"] = 1
                fatal = True
    print(format_metric_line(metrics))
    if fatal:
        return 2
    return 0 if all(metrics[name] == 0 for name in METRICS) else 2


if __name__ == "__main__":
    raise SystemExit(main())
