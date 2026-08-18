"""Endpoint-aware Ark embedding relay for the material-RAG verifier.

RAGFlow has no external route.  It can send plaintext HTTP only to this
service over the private ``material_rag_proxy`` network.  The relay accepts
one exact Ark embedding wire shape, proves every text item against an
authorizer-written SHA-256 allowlist, then opens one certificate-verified TLS
connection to the fixed Ark authority.  The allowlist may cover only locally
filtered Demo canonical units and deterministic verification canaries/queries;
it contains hashes, never text or identity metadata.  No original PDF, image,
filename, object key, dataset identifier, unregistered free-form question, LLM
or OCR request has an upstream code path.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ALLOWED_HOST = "ark.cn-beijing.volces.com"
ALLOWED_PORT = 443
ALLOWED_AUTHORITY = "ark.cn-beijing.volces.com:443"
ALLOWED_METHOD = "POST"
ALLOWED_PATH = "/api/plan/v3/embeddings/multimodal"
ALLOWED_MODEL = "doubao-embedding-vision"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8080
MAX_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_TEXT_ITEMS = 16
MAX_TEXT_CHARACTERS = 1_600
SOCKET_TIMEOUT_SECONDS = 60.0
AUDIT_PATH = Path("/run/material-rag-egress/audit.json")
AUTHORIZATION_PATH = Path(
    "/run/material-rag-authorization/body-sha256.json"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BENCHMARK_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


_COUNTER_KEYS = (
    "process_start_count",
    "authorized_embedding_request_count",
    "forwarded_embedding_request_count",
    "forwarded_non_embedding_request_count",
    "external_llm_call_count",
    "external_ocr_call_count",
    "input_text_count",
    "upstream_request_byte_count",
    "upstream_response_byte_count",
    "upstream_2xx_count",
    "upstream_4xx_count",
    "upstream_5xx_count",
    "rejected_request_count",
    "rejected_method_count",
    "rejected_path_count",
    "rejected_content_type_count",
    "rejected_json_count",
    "rejected_model_count",
    "rejected_non_text_input_count",
    "rejected_unauthorized_text_count",
    "inflight_embedding_request_count",
    "aborted_embedding_request_count",
)


class RelayError(RuntimeError):
    """Body-free relay failure with an optional rejection counter."""

    def __init__(self, reason: str, counter: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.counter = counter


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
    )


def _read_private_json(path: Path, *, maximum: int) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RelayError("ARK_AUTHORIZATION_UNAVAILABLE") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 1 <= before.st_size <= maximum
        ):
            raise RelayError("ARK_AUTHORIZATION_INVALID")
        body = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(body) != before.st_size or _identity(before) != _identity(after):
        raise RelayError("ARK_AUTHORIZATION_CHANGED")
    try:
        value = json.loads(body.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RelayError("ARK_AUTHORIZATION_INVALID") from error
    if not isinstance(value, dict):
        raise RelayError("ARK_AUTHORIZATION_INVALID")
    return value


def _authorized_body_sha256() -> frozenset[str]:
    value = _read_private_json(AUTHORIZATION_PATH, maximum=8 * 1024 * 1024)
    hashes = value.get("body_sha256")
    if (
        set(value) != {"schema", "body_sha256"}
        or value.get("schema") != "anhuan-material-rag-body-authorization-v1"
        or not isinstance(hashes, list)
        or not hashes
        or len(hashes) > 100_000
        or any(not isinstance(item, str) or not SHA256_RE.fullmatch(item) for item in hashes)
        or hashes != sorted(hashes)
        or len(set(hashes)) != len(hashes)
    ):
        raise RelayError("ARK_AUTHORIZATION_INVALID")
    return frozenset(hashes)


def _audit_template() -> dict[str, object]:
    return {
        "schema": "anhuan-material-rag-ark-relay-audit-v2",
        "allowed_upstream_authority": ALLOWED_AUTHORITY,
        "allowed_method": ALLOWED_METHOD,
        "allowed_path": ALLOWED_PATH,
        "allowed_model": ALLOWED_MODEL,
        **{key: 0 for key in _COUNTER_KEYS},
    }


class _Counters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values = self._load_or_empty()
        abandoned = int(self._values["inflight_embedding_request_count"])
        self._values["aborted_embedding_request_count"] = (
            int(self._values["aborted_embedding_request_count"]) + abandoned
        )
        self._values["inflight_embedding_request_count"] = 0
        self._values["process_start_count"] = int(
            self._values["process_start_count"]
        ) + 1
        self._persist()

    @staticmethod
    def _load_or_empty() -> dict[str, object]:
        try:
            value = _read_private_json(AUDIT_PATH, maximum=16 * 1024)
        except RelayError as error:
            if error.reason == "ARK_AUTHORIZATION_UNAVAILABLE":
                return _audit_template()
            raise
        expected = _audit_template()
        fixed = {
            "schema",
            "allowed_upstream_authority",
            "allowed_method",
            "allowed_path",
            "allowed_model",
        }
        if (
            set(value) != set(expected)
            or any(value.get(key) != expected[key] for key in fixed)
            or any(
                type(value.get(key)) is not int or int(value[key]) < 0
                for key in _COUNTER_KEYS
            )
            or int(value["forwarded_non_embedding_request_count"]) != 0
            or int(value["external_llm_call_count"]) != 0
            or int(value["external_ocr_call_count"]) != 0
        ):
            raise RelayError("ARK_AUDIT_INVALID")
        return value

    def _persist(self) -> None:
        AUDIT_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=AUDIT_PATH.parent, prefix="audit.", suffix=".tmp"
        )
        try:
            os.fchmod(descriptor, 0o600)
            body = _canonical_json(self._values) + b"\n"
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, AUDIT_PATH)
            directory = os.open(
                AUDIT_PATH.parent,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def reject(self, counter: str) -> None:
        if counter not in _COUNTER_KEYS or not counter.startswith("rejected_"):
            counter = "rejected_request_count"
        with self._lock:
            self._values["rejected_request_count"] = int(
                self._values["rejected_request_count"]
            ) + 1
            if counter != "rejected_request_count":
                self._values[counter] = int(self._values[counter]) + 1
            self._persist()

    def begin_forward(self, *, input_count: int, request_bytes: int) -> None:
        with self._lock:
            for key, amount in (
                ("authorized_embedding_request_count", 1),
                ("forwarded_embedding_request_count", 1),
                ("input_text_count", input_count),
                ("upstream_request_byte_count", request_bytes),
                ("inflight_embedding_request_count", 1),
            ):
                self._values[key] = int(self._values[key]) + amount
            self._persist()

    def finish(self, *, status: int | None, response_bytes: int) -> None:
        with self._lock:
            inflight = int(self._values["inflight_embedding_request_count"])
            if inflight < 1:
                raise RelayError("ARK_AUDIT_INVALID")
            self._values["inflight_embedding_request_count"] = inflight - 1
            self._values["upstream_response_byte_count"] = int(
                self._values["upstream_response_byte_count"]
            ) + response_bytes
            if status is None:
                self._values["aborted_embedding_request_count"] = int(
                    self._values["aborted_embedding_request_count"]
                ) + 1
            elif 200 <= status < 300:
                self._values["upstream_2xx_count"] = int(
                    self._values["upstream_2xx_count"]
                ) + 1
            elif 400 <= status < 500:
                self._values["upstream_4xx_count"] = int(
                    self._values["upstream_4xx_count"]
                ) + 1
            elif 500 <= status < 600:
                self._values["upstream_5xx_count"] = int(
                    self._values["upstream_5xx_count"]
                ) + 1
            else:
                self._values["aborted_embedding_request_count"] = int(
                    self._values["aborted_embedding_request_count"]
                ) + 1
            self._persist()


COUNTERS = _Counters()


def _allowed_upstream_ip(value: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if value.is_global:
        return True
    return (
        isinstance(value, ipaddress.IPv4Address)
        and value in _BENCHMARK_FAKE_IP_NETWORK
    )


def _public_addresses() -> list[tuple[int, tuple[object, ...]]]:
    global_addresses: list[tuple[int, tuple[object, ...]]] = []
    fake_ip_addresses: list[tuple[int, tuple[object, ...]]] = []
    seen: set[tuple[int, str]] = set()
    for family, _socktype, _protocol, _canonname, address in socket.getaddrinfo(
        ALLOWED_HOST,
        ALLOWED_PORT,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    ):
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        value = ipaddress.ip_address(str(address[0]))
        if not _allowed_upstream_ip(value):
            continue
        identity = (family, value.compressed)
        if identity in seen:
            continue
        seen.add(identity)
        bucket = global_addresses if value.is_global else fake_ip_addresses
        bucket.append((family, address))
    result = global_addresses + fake_ip_addresses
    if not result:
        raise OSError("ARK_EGRESS_DNS_REJECTED")
    return result


def _connect_public() -> socket.socket:
    last_error: OSError | None = None
    for family, address in _public_addresses():
        stream = socket.socket(family, socket.SOCK_STREAM)
        stream.settimeout(SOCKET_TIMEOUT_SECONDS)
        try:
            stream.connect(address)
            return stream
        except OSError as error:
            last_error = error
            stream.close()
    raise last_error or OSError("ARK_EGRESS_CONNECT_FAILED")


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def connect(self) -> None:
        raw = _connect_public()
        try:
            self.sock = self._context.wrap_socket(
                raw, server_hostname=ALLOWED_HOST
            )
        except BaseException:
            raw.close()
            raise


def _unique_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("ARK_JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def _validated_payload(raw: bytes) -> tuple[bytes, int]:
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=_unique_json_pairs
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RelayError("ARK_JSON_INVALID", "rejected_json_count") from error
    if not isinstance(value, dict) or set(value) not in (
        {"model", "input"},
        {"model", "input", "encoding_format"},
    ):
        raise RelayError("ARK_JSON_INVALID", "rejected_json_count")
    if value.get("model") != ALLOWED_MODEL:
        raise RelayError("ARK_MODEL_REJECTED", "rejected_model_count")
    if "encoding_format" in value and value["encoding_format"] != "float":
        raise RelayError("ARK_JSON_INVALID", "rejected_json_count")
    inputs = value.get("input")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_TEXT_ITEMS:
        raise RelayError("ARK_INPUT_REJECTED", "rejected_non_text_input_count")
    allowed = _authorized_body_sha256()
    normalized: list[dict[str, str]] = []
    for item in inputs:
        if (
            not isinstance(item, dict)
            or set(item) != {"type", "text"}
            or item.get("type") != "text"
            or not isinstance(item.get("text"), str)
            or not item["text"]
            or len(item["text"]) > MAX_TEXT_CHARACTERS
        ):
            raise RelayError(
                "ARK_INPUT_REJECTED", "rejected_non_text_input_count"
            )
        try:
            digest = hashlib.sha256(item["text"].encode("utf-8")).hexdigest()
        except UnicodeEncodeError:
            raise RelayError(
                "ARK_INPUT_REJECTED", "rejected_non_text_input_count"
            ) from None
        if digest not in allowed:
            raise RelayError(
                "ARK_TEXT_NOT_AUTHORIZED", "rejected_unauthorized_text_count"
            )
        normalized.append({"type": "text", "text": item["text"]})
    result: dict[str, object] = {"model": ALLOWED_MODEL, "input": normalized}
    if "encoding_format" in value:
        result["encoding_format"] = "float"
    return _canonical_json(result), len(normalized)


def _authorization_header(handler: BaseHTTPRequestHandler) -> str:
    values = handler.headers.get_all("Authorization", failobj=[])
    if (
        len(values) != 1
        or not isinstance(values[0], str)
        or not values[0].startswith("Bearer ")
        or not 16 <= len(values[0][7:]) <= 16_384
        or values[0] != values[0].strip()
        or any(
            ord(character) < 33 or ord(character) > 126
            for character in values[0][7:]
        )
    ):
        raise RelayError("ARK_AUTHORIZATION_INVALID", "rejected_json_count")
    return values[0]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Make parser/unknown-method failures body-free and auditable."""
        del message, explain
        COUNTERS.reject(
            "rejected_method_count" if code == 501 else "rejected_request_count"
        )
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _reject(self, counter: str) -> None:
        COUNTERS.reject(counter)
        self.send_response(403)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _reject_method(self) -> None:
        self._reject("rejected_method_count")

    do_GET = _reject_method
    do_PUT = _reject_method
    do_DELETE = _reject_method
    do_PATCH = _reject_method
    do_OPTIONS = _reject_method
    do_CONNECT = _reject_method
    do_HEAD = _reject_method

    def do_POST(self) -> None:
        status: int | None = None
        response_body = b""
        started = False
        try:
            if self.path != ALLOWED_PATH or "?" in self.path or "#" in self.path:
                raise RelayError("ARK_PATH_REJECTED", "rejected_path_count")
            content_types = self.headers.get_all("Content-Type", failobj=[])
            content_lengths = self.headers.get_all("Content-Length", failobj=[])
            transfer_encodings = self.headers.get_all(
                "Transfer-Encoding", failobj=[]
            )
            if content_types != ["application/json"]:
                raise RelayError(
                    "ARK_CONTENT_TYPE_REJECTED", "rejected_content_type_count"
                )
            if transfer_encodings or len(content_lengths) != 1:
                raise RelayError("ARK_JSON_INVALID", "rejected_json_count")
            try:
                length = int(content_lengths[0], 10)
            except (TypeError, ValueError):
                raise RelayError("ARK_JSON_INVALID", "rejected_json_count") from None
            if not 1 <= length <= MAX_REQUEST_BYTES:
                raise RelayError("ARK_JSON_INVALID", "rejected_json_count")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise RelayError("ARK_JSON_INVALID", "rejected_json_count")
            body, input_count = _validated_payload(raw)
            authorization = _authorization_header(self)
            COUNTERS.begin_forward(input_count=input_count, request_bytes=len(body))
            started = True
            connection = _PinnedHttpsConnection(
                ALLOWED_HOST,
                ALLOWED_PORT,
                timeout=SOCKET_TIMEOUT_SECONDS,
                context=ssl.create_default_context(),
            )
            try:
                connection.request(
                    ALLOWED_METHOD,
                    ALLOWED_PATH,
                    body=body,
                    headers={
                        "Accept": "application/json",
                        "Authorization": authorization,
                        "Content-Type": "application/json",
                        "Host": ALLOWED_HOST,
                    },
                )
                upstream = connection.getresponse()
                status = int(upstream.status)
                response_body = upstream.read(MAX_RESPONSE_BYTES + 1)
            finally:
                connection.close()
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise RelayError("ARK_RESPONSE_TOO_LARGE")
            content_type = upstream.getheader("Content-Type", "")
            if not content_type.lower().startswith("application/json"):
                raise RelayError("ARK_RESPONSE_INVALID")
            COUNTERS.finish(status=status, response_bytes=len(response_body))
            started = False
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response_body)
            self.close_connection = True
        except RelayError as error:
            if started:
                COUNTERS.finish(status=None, response_bytes=len(response_body))
            self._reject(error.counter or "rejected_request_count")
        except (OSError, ssl.SSLError, http.client.HTTPException):
            if started:
                COUNTERS.finish(status=None, response_bytes=len(response_body))
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        return


def main() -> int:
    with _Server((LISTEN_HOST, LISTEN_PORT), _Handler) as server:
        server.serve_forever(poll_interval=0.25)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
