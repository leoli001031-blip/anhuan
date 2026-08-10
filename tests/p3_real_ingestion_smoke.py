"""One-shot real PostgreSQL/MinIO/ClamAV smoke for P3 ingestion.

The runner owns one random PostgreSQL container (via the P2 scratch helper)
and two separately-labelled random sidecars.  PostgreSQL data and MinIO data
live only on tmpfs; ClamAV uses the immutable signature database shipped in
the pinned image and never runs FreshClam.  No shared Docker volume is
created, mounted, enumerated, or removed.

OIDC signature verification is replaced only at the ASGI identity dependency.
Tenant membership/RLS, PostgreSQL, MinIO object IO, the clamd wire verdict,
preview generation, and release copying all remain real.  The only processor
injection binds its existing real ``scan_stream`` implementation to the
random loopback clamd port; it does not replace or synthesize a verdict.

Stdout contains fixed aggregate status/reason metrics only.  It never prints
response bodies, source names, object keys, UUIDs, ports, DSNs, or secrets.
This is targeted local validation, not release verification or production use.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from functools import partial
import http.client
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time
from typing import Any
import uuid

import httpx
import psycopg
from pypdf import PdfWriter


ROOT = Path(__file__).resolve().parents[1]
for source_root in (ROOT, ROOT / "src"):
    source_text = str(source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)

from tests.p2_real_pg_api_smoke import ScratchPostgres, SmokeFailure  # noqa: E402


MINIO_IMAGE = (
    "minio/minio:RELEASE.2024-07-29T22-14-52Z@sha256:"
    "29110b4abbcc7c2a71f19f5e375d50c2771c94272efba59c9a0532c88403672d"
)
MINIO_REPO_DIGEST = (
    "minio/minio@sha256:"
    "29110b4abbcc7c2a71f19f5e375d50c2771c94272efba59c9a0532c88403672d"
)
CLAMAV_IMAGE = (
    "clamav/clamav:1.4.6-debian13-slim@sha256:"
    "aaf6efb85740dc60872e2c13e5b7778c2d57b05b960f854a2461eaf729250d18"
)
CLAMAV_REPO_DIGEST = (
    "clamav/clamav@sha256:"
    "aaf6efb85740dc60872e2c13e5b7778c2d57b05b960f854a2461eaf729250d18"
)
SCOPE_LABEL = "p3-real-ingestion-smoke"
NAME_RE = re.compile(r"anhuan-p3-(?:minio|clamd)-[0-9a-f]{32}\Z")
CONTAINER_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

METRICS = OrderedDict(
    (
        ("migration_head_mismatches", 0),
        ("sidecar_identity_failures", 0),
        ("minio_failures", 0),
        ("scanner_failures", 0),
        ("upload_failures", 0),
        ("process_failures", 0),
        ("preview_failures", 0),
        ("release_failures", 0),
        ("cross_tenant_api_leaks", 0),
        ("data_identity_failures", 0),
        ("cleanup_residuals", 0),
        ("unexpected_failures", 0),
    )
)


def _secure_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        raise SmokeFailure("SIDECAR_SECRET_RED")


def _json_object(response: httpx.Response, code: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        raise SmokeFailure(code) from None
    if not isinstance(payload, dict):
        raise SmokeFailure(code)
    return payload


def _uuid_field(payload: dict[str, Any], field: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[field]))
    except (KeyError, TypeError, ValueError):
        raise SmokeFailure(code) from None


def _blank_pdf() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


class ScratchSidecars:
    """Own exactly one MinIO and one clamd container for one smoke run."""

    def __init__(self, scratch: ScratchPostgres) -> None:
        self.scratch = scratch
        self.run_id = uuid.uuid4().hex
        self.minio_name = f"anhuan-p3-minio-{self.run_id}"
        self.clamd_name = f"anhuan-p3-clamd-{self.run_id}"
        if not NAME_RE.fullmatch(self.minio_name) or not NAME_RE.fullmatch(
            self.clamd_name
        ):
            raise SmokeFailure("SIDECAR_NAME_RED")
        self.container_ids: dict[str, str] = {}
        self.minio_port = 0
        self.clamd_port = 0
        self.minio_user_path = scratch.secrets_dir / "minio_root_user"
        self.minio_password_path = scratch.secrets_dir / "minio_root_password"
        self.minio_user = f"p3{secrets.token_hex(12)}"
        self.minio_password = secrets.token_hex(32)

    def _docker(self, *arguments: str, timeout: int = 90):
        return self.scratch._docker(*arguments, timeout=timeout)  # noqa: SLF001

    def _validate_image(self, image: str, expected_digest: str) -> None:
        result = self._docker("image", "inspect", image)
        if result.returncode != 0:
            raise SmokeFailure("SIDECAR_IMAGE_RED")
        try:
            payload = json.loads(result.stdout)[0]
            image_id = str(payload["Id"])
            repo_digests = {str(value) for value in payload["RepoDigests"]}
        except (IndexError, KeyError, TypeError, ValueError):
            raise SmokeFailure("SIDECAR_IMAGE_RED") from None
        if not IMAGE_ID_RE.fullmatch(image_id) or expected_digest not in repo_digests:
            raise SmokeFailure("SIDECAR_IMAGE_RED")

    def _assert_no_preexisting(self) -> None:
        result = self._docker(
            "ps",
            "-a",
            "--filter",
            f"label=com.anhuan.run={self.run_id}",
            "--format",
            "{{.ID}}",
        )
        if result.returncode != 0 or result.stdout.strip():
            raise SmokeFailure("SIDECAR_PREEXISTING_RED")

    def _start_minio(self) -> None:
        uid = os.geteuid()
        gid = os.getegid()
        result = self._docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            self.minio_name,
            "--label",
            f"com.anhuan.scope={SCOPE_LABEL}",
            "--label",
            f"com.anhuan.run={self.run_id}",
            "--label",
            "com.anhuan.component=minio",
            "--publish",
            "127.0.0.1::9000",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--user",
            f"{uid}:{gid}",
            "--tmpfs",
            f"/data:rw,nosuid,nodev,noexec,size=256m,mode=0700,uid={uid},gid={gid}",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,noexec,size=32m,mode=0700,uid={uid},gid={gid}",
            "--mount",
            "type=bind,src="
            f"{self.minio_user_path},dst=/run/secrets/minio_root_user,readonly",
            "--mount",
            "type=bind,src="
            f"{self.minio_password_path},dst=/run/secrets/minio_root_password,readonly",
            "--env",
            "HOME=/tmp",
            "--env",
            "MINIO_BROWSER=off",
            "--env",
            "MINIO_ROOT_USER_FILE=/run/secrets/minio_root_user",
            "--env",
            "MINIO_ROOT_PASSWORD_FILE=/run/secrets/minio_root_password",
            MINIO_IMAGE,
            "server",
            "--console-address",
            ":9001",
            "/data",
            timeout=120,
        )
        if result.returncode != 0:
            raise SmokeFailure("MINIO_START_RED")
        container_id = result.stdout.decode("ascii", "strict").strip()
        if not CONTAINER_ID_RE.fullmatch(container_id):
            raise SmokeFailure("MINIO_ID_RED")
        self.container_ids["minio"] = container_id
        self.minio_port = self._validate_container(
            component="minio",
            container_id=container_id,
            container_name=self.minio_name,
            internal_port="9000/tcp",
            required_tmpfs={"/data", "/tmp"},
            allowed_bind_targets={
                "/run/secrets/minio_root_user",
                "/run/secrets/minio_root_password",
            },
        )

    def _start_clamd(self) -> None:
        result = self._docker(
            "run",
            "--detach",
            "--pull=never",
            "--platform",
            "linux/arm64",
            "--name",
            self.clamd_name,
            "--label",
            f"com.anhuan.scope={SCOPE_LABEL}",
            "--label",
            f"com.anhuan.run={self.run_id}",
            "--label",
            "com.anhuan.component=clamd",
            "--publish",
            "127.0.0.1::3310",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=0777",
            "--tmpfs",
            "/run/clamav:rw,nosuid,nodev,noexec,size=16m,mode=0777",
            "--tmpfs",
            "/var/log/clamav:rw,nosuid,nodev,noexec,size=32m,mode=0777",
            "--env",
            "CLAMAV_NO_FRESHCLAMD=true",
            "--env",
            "CLAMD_STARTUP_TIMEOUT=300",
            "--env",
            "CLAMD_CONF_MaxFileSize=52428800",
            "--env",
            "CLAMD_CONF_MaxScanSize=52428800",
            "--env",
            "CLAMD_CONF_StreamMaxLength=52428800",
            CLAMAV_IMAGE,
            timeout=120,
        )
        if result.returncode != 0:
            raise SmokeFailure("CLAMD_START_RED")
        container_id = result.stdout.decode("ascii", "strict").strip()
        if not CONTAINER_ID_RE.fullmatch(container_id):
            raise SmokeFailure("CLAMD_ID_RED")
        self.container_ids["clamd"] = container_id
        self.clamd_port = self._validate_container(
            component="clamd",
            container_id=container_id,
            container_name=self.clamd_name,
            internal_port="3310/tcp",
            required_tmpfs={"/tmp", "/run/clamav", "/var/log/clamav"},
            allowed_bind_targets=set(),
        )

    def _validate_container(
        self,
        *,
        component: str,
        container_id: str,
        container_name: str,
        internal_port: str,
        required_tmpfs: set[str],
        allowed_bind_targets: set[str],
    ) -> int:
        result = self._docker("inspect", container_id)
        if result.returncode != 0:
            raise SmokeFailure("SIDECAR_INSPECT_RED")
        try:
            payload = json.loads(result.stdout)[0]
            labels = payload["Config"]["Labels"]
            binding = payload["NetworkSettings"]["Ports"][internal_port]
            host_ip = str(binding[0]["HostIp"])
            host_port = int(binding[0]["HostPort"])
            tmpfs = set(payload["HostConfig"]["Tmpfs"])
            mounts = payload.get("Mounts") or []
            observed_binds = {
                str(mount["Destination"])
                for mount in mounts
                if str(mount.get("Type")) == "bind"
            }
            forbidden_mount = any(
                str(mount.get("Type")) not in {"bind", "tmpfs"}
                for mount in mounts
            )
            writable_bind = any(
                str(mount.get("Type")) == "bind" and bool(mount.get("RW"))
                for mount in mounts
            )
        except (IndexError, KeyError, TypeError, ValueError):
            raise SmokeFailure("SIDECAR_INSPECT_RED") from None
        if (
            payload["Id"] != container_id
            or str(payload["Name"]).removeprefix("/") != container_name
            or labels.get("com.anhuan.scope") != SCOPE_LABEL
            or labels.get("com.anhuan.run") != self.run_id
            or labels.get("com.anhuan.component") != component
            or host_ip != "127.0.0.1"
            or not 1 <= host_port <= 65535
            or host_port in {9000, 3310}
            or not required_tmpfs.issubset(tmpfs)
            or observed_binds != allowed_bind_targets
            or forbidden_mount
            or writable_bind
        ):
            raise SmokeFailure("SIDECAR_IDENTITY_RED")
        return host_port

    def _wait_minio(self) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1", self.minio_port, timeout=2
                )
                connection.request("GET", "/minio/health/live")
                response = connection.getresponse()
                response.read(1024)
                connection.close()
                if response.status == 200:
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.5)
        raise SmokeFailure("MINIO_READY_RED")

    def _wait_clamd(self) -> None:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    ("127.0.0.1", self.clamd_port), timeout=2
                ) as connection:
                    connection.settimeout(2)
                    connection.sendall(b"zVERSION\x00")
                    response = bytearray()
                    while len(response) <= 4096:
                        piece = connection.recv(512)
                        if not piece:
                            break
                        response.extend(piece)
                        if b"\x00" in piece or piece.endswith(b"\n"):
                            break
                if re.match(rb"^ClamAV [0-9]", bytes(response)):
                    return
            except (OSError, TimeoutError):
                pass
            time.sleep(1)
        raise SmokeFailure("CLAMD_READY_RED")

    def start(self) -> None:
        self._validate_image(MINIO_IMAGE, MINIO_REPO_DIGEST)
        self._validate_image(CLAMAV_IMAGE, CLAMAV_REPO_DIGEST)
        self._assert_no_preexisting()
        _secure_write(self.minio_user_path, self.minio_user + "\n")
        _secure_write(self.minio_password_path, self.minio_password + "\n")
        self._start_minio()
        self._start_clamd()
        self._wait_minio()
        self._wait_clamd()

    def environment(self) -> dict[str, str]:
        if not self.minio_port or not self.clamd_port:
            raise SmokeFailure("SIDECAR_NOT_READY_RED")
        return {
            "MINIO_ENDPOINT": f"127.0.0.1:{self.minio_port}",
            "F1_MINIO_ROOT_USER_FILE": str(self.minio_user_path),
            "F1_MINIO_ROOT_PASSWORD_FILE": str(self.minio_password_path),
        }

    def cleanup(self) -> int:
        failures = 0
        targets = (
            ("clamd", self.clamd_name),
            ("minio", self.minio_name),
        )
        if not self.scratch.endpoint.startswith("unix://"):
            return 1
        for component, name in targets:
            container_id = self.container_ids.get(component)
            target = container_id or name
            inspect = self._docker("inspect", target)
            if inspect.returncode == 0:
                try:
                    payload = json.loads(inspect.stdout)[0]
                    labels = payload["Config"]["Labels"]
                    valid = (
                        CONTAINER_ID_RE.fullmatch(str(payload["Id"])) is not None
                        and (container_id is None or payload["Id"] == container_id)
                        and str(payload["Name"]).removeprefix("/") == name
                        and labels.get("com.anhuan.scope") == SCOPE_LABEL
                        and labels.get("com.anhuan.run") == self.run_id
                        and labels.get("com.anhuan.component") == component
                    )
                except (IndexError, KeyError, TypeError, ValueError):
                    valid = False
                if not valid:
                    failures += 1
                else:
                    removed = self._docker(
                        "rm", "--force", "--volumes", str(payload["Id"])
                    )
                    if removed.returncode != 0:
                        failures += 1
            elif b"No such object" not in inspect.stderr:
                failures += 1
        residual = self._docker(
            "ps",
            "-a",
            "--filter",
            f"label=com.anhuan.run={self.run_id}",
            "--format",
            "{{.ID}}",
        )
        if residual.returncode != 0 or residual.stdout.strip():
            failures += 1
        return failures


def _validate_migration_head(scratch: ScratchPostgres) -> None:
    with psycopg.connect(**scratch.bootstrap_kwargs()) as connection:
        heads = connection.execute(
            "SELECT "
            "(SELECT string_agg(version_num, ',' ORDER BY version_num) "
            "FROM f0d.alembic_version),"
            "(SELECT string_agg(version_num, ',' ORDER BY version_num) "
            "FROM f1.alembic_version)"
        ).fetchone()
    if heads != ("f0d_0006", "f1_0010"):
        raise SmokeFailure("MIGRATION_HEAD_RED")


async def _dispose_database_engines() -> None:
    try:
        from platform_foundation.f1 import database
    except ImportError:
        return
    for engine in tuple(database._engines.values()):  # noqa: SLF001
        await engine.dispose()  # type: ignore[union-attr]
    database._engines.clear()  # noqa: SLF001
    database._factories.clear()  # noqa: SLF001


async def _api_smoke(
    scratch: ScratchPostgres,
    sidecars: ScratchSidecars,
    actors: dict[str, dict[str, Any]],
) -> tuple[uuid.UUID, uuid.UUID]:
    environment = scratch.f1_environment()
    environment.update(sidecars.environment())
    for key, value in environment.items():
        os.environ[key] = value

    from fastapi import FastAPI, Header, HTTPException
    from platform_foundation.f1 import auth, storage
    from platform_foundation.f1.api.routers import p3_controlled_ingestion
    from platform_foundation.f1.features.p3 import processor, scanner

    # Test-only endpoint binding.  The implementation and clamd wire verdict
    # remain the production scanner implementation against the real sidecar.
    original_scan_stream = processor.scan_stream
    processor.scan_stream = partial(
        scanner.scan_stream,
        host="127.0.0.1",
        port=sidecars.clamd_port,
        timeout_seconds=60,
    )

    app = FastAPI()
    app.include_router(
        p3_controlled_ingestion.router, prefix="/api/v1/ingestion"
    )

    async def synthetic_user(
        x_p3_smoke_actor: str | None = Header(default=None),
    ) -> dict[str, Any]:
        actor = actors.get(x_p3_smoke_actor or "")
        if actor is None:
            raise HTTPException(status_code=401, detail="SMOKE_IDENTITY_REQUIRED")
        return {"sub": actor["sub"], "roles": ()}

    app.dependency_overrides[auth.current_user] = synthetic_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async def run_requests() -> tuple[uuid.UUID, uuid.UUID]:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://p3.invalid"
        ) as client:
            async def request(
                actor_name: str,
                method: str,
                path: str,
                expected: int,
                *,
                code: str,
                data: dict[str, str] | None = None,
                files: dict[str, tuple[str, bytes, str]] | None = None,
                extra_headers: dict[str, str] | None = None,
            ) -> httpx.Response:
                actor = actors[actor_name]
                headers = {
                    "X-P3-Smoke-Actor": actor_name,
                    "X-Enterprise-Id": str(actor["enterprise_id"]),
                }
                if extra_headers:
                    headers.update(extra_headers)
                try:
                    response = await client.request(
                        method,
                        path,
                        headers=headers,
                        data=data,
                        files=files,
                    )
                except Exception:
                    raise SmokeFailure(f"{code}_REQUEST_RED") from None
                if response.status_code != expected:
                    raise SmokeFailure(f"{code}_HTTP_{response.status_code}_RED")
                return response

            body = _blank_pdf()
            created_response = await request(
                "admin",
                "POST",
                "/api/v1/ingestion/documents",
                202,
                code="UPLOAD",
                data={"display_name": "SYNTHETIC_P3_DOCUMENT"},
                files={
                    "file": (
                        "synthetic.pdf",
                        body,
                        "application/pdf",
                    )
                },
                extra_headers={"Idempotency-Key": secrets.token_hex(24)},
            )
            created = _json_object(created_response, "UPLOAD_BODY_RED")
            document_id = _uuid_field(created, "id", "UPLOAD_BODY_RED")
            versions = created.get("versions")
            if not isinstance(versions, list) or len(versions) != 1:
                raise SmokeFailure("UPLOAD_BODY_RED")
            version = versions[0]
            if not isinstance(version, dict):
                raise SmokeFailure("UPLOAD_BODY_RED")
            version_id = _uuid_field(version, "id", "UPLOAD_BODY_RED")
            if (
                version.get("workflow_status") != "received"
                or version.get("quarantine_status") != "held"
                or version.get("scan_status") != "queued"
                or version.get("preview_status") != "blocked"
            ):
                raise SmokeFailure("UPLOAD_STATE_RED")

            await request(
                "tenant_b",
                "GET",
                f"/api/v1/ingestion/documents/{document_id}",
                404,
                code="CROSS_TENANT",
            )

            processed_response = await request(
                "admin",
                "POST",
                f"/api/v1/ingestion/versions/{version_id}/process",
                202,
                code="PROCESS",
            )
            processed = _json_object(processed_response, "PROCESS_BODY_RED")
            if (
                processed.get("workflow_status") != "ready"
                or processed.get("quarantine_status") != "held"
                or processed.get("scan_status") != "clean"
                or processed.get("preview_status") != "ready"
            ):
                raise SmokeFailure("PROCESS_STATE_RED")

            preview_response = await request(
                "admin",
                "GET",
                f"/api/v1/ingestion/versions/{version_id}/preview",
                200,
                code="PREVIEW",
            )
            preview = _json_object(preview_response, "PREVIEW_BODY_RED")
            units = preview.get("units")
            if (
                preview.get("status") != "ready"
                or preview.get("kind") != "page_text"
                or not isinstance(units, list)
                or len(units) != 1
                or not isinstance(units[0], dict)
                or units[0].get("kind") != "page_text"
            ):
                raise SmokeFailure("PREVIEW_STATE_RED")
            unit_id = str(units[0].get("id") or "")
            if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", unit_id) is None:
                raise SmokeFailure("PREVIEW_UNIT_RED")
            unit_response = await request(
                "admin",
                "GET",
                f"/api/v1/ingestion/versions/{version_id}/preview/units/"
                f"{unit_id}/content",
                200,
                code="PREVIEW_UNIT",
            )
            unit = _json_object(unit_response, "PREVIEW_UNIT_BODY_RED")
            if not isinstance(unit.get("lines"), list) or not isinstance(
                unit.get("truncated"), bool
            ):
                raise SmokeFailure("PREVIEW_UNIT_BODY_RED")

            released_response = await request(
                "admin",
                "POST",
                f"/api/v1/ingestion/versions/{version_id}/release",
                200,
                code="RELEASE",
            )
            released = _json_object(released_response, "RELEASE_BODY_RED")
            if (
                released.get("workflow_status") != "ready"
                or released.get("quarantine_status") != "released"
                or released.get("scan_status") != "clean"
                or released.get("preview_status") != "ready"
            ):
                raise SmokeFailure("RELEASE_STATE_RED")
            return document_id, version_id

    try:
        identifiers = await run_requests()
    finally:
        processor.scan_stream = original_scan_stream

    document_id, version_id = identifiers
    with psycopg.connect(**scratch.bootstrap_kwargs()) as connection:
        row = connection.execute(
            "SELECT task.object_key,task.content_sha256,task.source_size,"
            "task.source_etag,task.scan_verdict,task.scanner_engine,"
            "task.scanner_version,task.signature_version,task.preview_status,"
            "task.released_at "
            "FROM f1.document_version AS version "
            "JOIN f1.upload_task AS task ON task.enterprise_id=version.enterprise_id "
            "AND task.id=version.upload_task_id "
            "WHERE version.id=%s AND version.document_record_id=%s",
            (version_id, document_id),
        ).fetchone()
        audit = {
            str(item[0])
            for item in connection.execute(
                "SELECT action FROM f1.audit_log WHERE enterprise_id=%s "
                "AND resource_id=%s",
                (actors["admin"]["enterprise_id"], str(version_id)),
            ).fetchall()
        }
    if row is None:
        raise SmokeFailure("DATA_IDENTITY_RED")
    if (
        row[4] != "clean"
        or row[5] != "clamav"
        or not row[6]
        or not row[7]
        or row[8] != "ready"
        or row[9] is None
        or not {
            "document.version.create",
            "document.version.process",
            "document.version.release",
        }.issubset(audit)
    ):
        raise SmokeFailure("DATA_IDENTITY_RED")
    quarantined = storage.verify_quarantine_object(
        str(row[0]),
        expected_sha256=str(row[1]),
        expected_size=int(row[2]),
        expected_etag=str(row[3]),
    )
    released = storage.verify_stored_object(
        str(row[0]),
        expected_sha256=str(row[1]),
        expected_size=int(row[2]),
    )
    if (
        quarantined.sha256 != str(row[1])
        or released.sha256 != str(row[1])
        or quarantined.size != released.size
    ):
        raise SmokeFailure("MINIO_IDENTITY_RED")
    return document_id, version_id


def _metric_for(code: str) -> str:
    if code.startswith("MIGRATION") or code.startswith("ROOT_MIGRATION") or code.startswith(
        "F1_MIGRATION"
    ):
        return "migration_head_mismatches"
    if code.startswith("MINIO"):
        return "minio_failures"
    if code.startswith("CLAMD") or code.startswith("SCAN"):
        return "scanner_failures"
    if code.startswith("SIDECAR"):
        return "sidecar_identity_failures"
    if code.startswith("UPLOAD"):
        return "upload_failures"
    if code.startswith("PROCESS"):
        return "process_failures"
    if code.startswith("PREVIEW"):
        return "preview_failures"
    if code.startswith("RELEASE"):
        return "release_failures"
    if code.startswith("CROSS_TENANT"):
        return "cross_tenant_api_leaks"
    if code.startswith("DATA"):
        return "data_identity_failures"
    return "unexpected_failures"


def _render(status: str, reason: str | None = None) -> None:
    print(status)
    if reason is not None:
        print(f"reason={reason}")
    for name, value in METRICS.items():
        print(f"{name}={value}")


def main() -> int:
    scratch: ScratchPostgres | None = None
    sidecars: ScratchSidecars | None = None
    primary_reason: str | None = None
    success = False
    try:
        scratch = ScratchPostgres()
        scratch.start()
        scratch.migrate()
        _validate_migration_head(scratch)
        actors = scratch.seed()
        sidecars = ScratchSidecars(scratch)
        sidecars.start()
        asyncio.run(_api_smoke(scratch, sidecars, actors))
        success = True
    except SmokeFailure as error:
        primary_reason = error.code
        metric = _metric_for(error.code)
        if METRICS[metric] == 0:
            METRICS[metric] = 1
    except Exception:
        primary_reason = "UNEXPECTED_RED"
        METRICS["unexpected_failures"] = 1
    finally:
        try:
            asyncio.run(_dispose_database_engines())
        except Exception:
            METRICS["cleanup_residuals"] += 1
        if sidecars is not None:
            try:
                METRICS["cleanup_residuals"] += sidecars.cleanup()
            except Exception:
                METRICS["cleanup_residuals"] += 1
        if scratch is not None:
            try:
                METRICS["cleanup_residuals"] += scratch.cleanup()
            except Exception:
                METRICS["cleanup_residuals"] += 1

    if success and all(value == 0 for value in METRICS.values()):
        _render("P3_REAL_INGESTION_SMOKE_PASSED_NOT_RELEASE_VERIFIED")
        return 0
    _render(
        "P3_REAL_INGESTION_SMOKE_REJECTED",
        primary_reason or "CLEANUP_RED",
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
