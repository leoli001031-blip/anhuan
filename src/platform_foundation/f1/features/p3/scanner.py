"""Fail-closed local ClamAV INSTREAM adapter for P3.

No response body, source name, object key, path, or scanner signature is
returned to callers.  The only allowed hosts are the internal sidecar name
and loopback, preventing a configuration typo from uploading a source to an
external scanner.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import struct
from dataclasses import dataclass
from typing import BinaryIO

from .contracts import SCAN_TIMEOUT_SECONDS


MAX_SCAN_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
ALLOWED_SCANNER_HOSTS = frozenset(("clamd", "localhost", "127.0.0.1", "::1"))


class ScanFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ScanResult:
    verdict: str
    reason_code: str | None
    engine: str = "clamav"
    engine_version: str | None = None
    signature_version: str | None = None


@dataclass(frozen=True, slots=True)
class ScannerVersion:
    engine_version: str
    signature_version: str


_VERSION_RE = re.compile(
    rb"^ClamAV ([0-9][0-9A-Za-z._-]{0,31})/([0-9]{1,16})(?:/[^\x00\r\n]{1,128})?$"
)


def parse_clamd_response(
    payload: bytes, *, version: ScannerVersion | None = None
) -> ScanResult:
    """Map the clamd wire response to fixed, body-free outcomes."""
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise ScanFailure("P3_SCAN_PROTOCOL_ERROR", retryable=True)
    normalized = payload.rstrip(b"\x00\r\n")
    if normalized == b"stream: OK":
        return ScanResult(
            verdict="clean",
            reason_code=None,
            engine_version=version.engine_version if version else None,
            signature_version=version.signature_version if version else None,
        )
    if normalized.startswith(b"stream: ") and normalized.endswith(b" FOUND"):
        return ScanResult(
            verdict="infected",
            reason_code="P3_MALWARE_DETECTED",
            engine_version=version.engine_version if version else None,
            signature_version=version.signature_version if version else None,
        )
    if normalized.startswith(b"stream: ") and normalized.endswith(b" ERROR"):
        raise ScanFailure("P3_SCAN_ENGINE_ERROR", retryable=True)
    raise ScanFailure("P3_SCAN_PROTOCOL_ERROR", retryable=True)


def parse_clamd_version(payload: bytes) -> ScannerVersion:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise ScanFailure("P3_SCAN_PROTOCOL_ERROR", retryable=True)
    match = _VERSION_RE.fullmatch(payload.rstrip(b"\x00\r\n"))
    if match is None:
        raise ScanFailure("P3_SCAN_PROTOCOL_ERROR", retryable=True)
    return ScannerVersion(
        engine_version=match.group(1).decode("ascii"),
        signature_version=match.group(2).decode("ascii"),
    )


def scanner_version(
    *,
    host: str = "clamd",
    port: int = 3310,
    timeout_seconds: int = SCAN_TIMEOUT_SECONDS,
) -> ScannerVersion:
    address = _resolve_target(host, port, timeout_seconds)
    return _scanner_version_at(address, port, timeout_seconds)


def _scanner_version_at(
    address: str, port: int, timeout_seconds: int
) -> ScannerVersion:
    try:
        with socket.create_connection(
            (address, port), timeout=timeout_seconds
        ) as connection:
            connection.settimeout(timeout_seconds)
            connection.sendall(b"zVERSION\x00")
            response = _receive_response(connection)
    except (TimeoutError, socket.timeout) as error:
        raise ScanFailure("P3_SCANNER_TIMEOUT", retryable=True) from error
    except OSError as error:
        raise ScanFailure("P3_SCANNER_UNAVAILABLE", retryable=True) from error
    return parse_clamd_version(response)


def scan_stream(
    file_obj: BinaryIO,
    *,
    expected_size: int,
    expected_sha256: str,
    host: str = "clamd",
    port: int = 3310,
    timeout_seconds: int = SCAN_TIMEOUT_SECONDS,
) -> ScanResult:
    """Scan an exact source identity using clamd's local INSTREAM protocol."""
    address = _resolve_target(host, port, timeout_seconds)
    if not 0 < expected_size <= MAX_SCAN_BYTES:
        raise ScanFailure("P3_SCAN_SIZE_INVALID", retryable=False)

    digest = hashlib.sha256()
    observed = 0
    version = _scanner_version_at(address, port, timeout_seconds)
    try:
        file_obj.seek(0)
        with socket.create_connection(
            (address, port), timeout=timeout_seconds
        ) as connection:
            connection.settimeout(timeout_seconds)
            connection.sendall(b"zINSTREAM\x00")
            while True:
                chunk = file_obj.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise ScanFailure("P3_SOURCE_READ_FAILED", retryable=True)
                observed += len(chunk)
                if observed > expected_size or observed > MAX_SCAN_BYTES:
                    raise ScanFailure("P3_SOURCE_IDENTITY_MISMATCH", retryable=False)
                digest.update(chunk)
                connection.sendall(struct.pack("!I", len(chunk)))
                connection.sendall(chunk)
            connection.sendall(struct.pack("!I", 0))
            response = _receive_response(connection)
    except ScanFailure:
        raise
    except (TimeoutError, socket.timeout) as error:
        raise ScanFailure("P3_SCANNER_TIMEOUT", retryable=True) from error
    except OSError as error:
        raise ScanFailure("P3_SCANNER_UNAVAILABLE", retryable=True) from error
    except Exception as error:
        raise ScanFailure("P3_SOURCE_READ_FAILED", retryable=True) from error
    try:
        file_obj.seek(0)
    except Exception as error:
        raise ScanFailure("P3_SOURCE_READ_FAILED", retryable=True) from error

    if observed != expected_size or digest.hexdigest() != expected_sha256:
        raise ScanFailure("P3_SOURCE_IDENTITY_MISMATCH", retryable=False)
    return parse_clamd_response(bytes(response), version=version)


def _resolve_target(host: str, port: int, timeout_seconds: int) -> str:
    if host not in ALLOWED_SCANNER_HOSTS or not 1 <= port <= 65535:
        raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    if timeout_seconds < 1 or timeout_seconds > SCAN_TIMEOUT_SECONDS:
        raise ScanFailure("P3_SCAN_TIMEOUT_INVALID", retryable=False)
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        }
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except (OSError, ValueError) as error:
        raise ScanFailure("P3_SCANNER_UNAVAILABLE", retryable=True) from error
    if not parsed or any(
        not (address.is_private or address.is_loopback) for address in parsed
    ):
        raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    return str(sorted(parsed, key=lambda item: (item.version, int(item)))[0])


def _receive_response(connection: socket.socket) -> bytes:
    response = bytearray()
    while len(response) <= MAX_RESPONSE_BYTES:
        piece = connection.recv(min(512, MAX_RESPONSE_BYTES + 1 - len(response)))
        if not piece:
            break
        response.extend(piece)
        if b"\x00" in piece or piece.endswith(b"\n"):
            break
    if len(response) > MAX_RESPONSE_BYTES:
        raise ScanFailure("P3_SCAN_PROTOCOL_ERROR", retryable=True)
    return bytes(response)


__all__ = (
    "ALLOWED_SCANNER_HOSTS",
    "MAX_SCAN_BYTES",
    "ScanFailure",
    "ScanResult",
    "ScannerVersion",
    "parse_clamd_response",
    "parse_clamd_version",
    "scan_stream",
    "scanner_version",
)
