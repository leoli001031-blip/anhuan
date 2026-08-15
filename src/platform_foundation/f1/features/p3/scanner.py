"""Fail-closed local ClamAV INSTREAM adapter for P3.

No response body, source name, object key, path, or scanner signature is
returned to callers.  The only allowed hosts are the internal sidecar name
and loopback, preventing a configuration typo from uploading a source to an
external scanner.
"""
from __future__ import annotations

import errno
import hashlib
import ipaddress
import os
import re
import socket
import struct
from dataclasses import dataclass
from typing import BinaryIO

from .contracts import SCAN_TIMEOUT_SECONDS


MAX_SCAN_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024
STREAM_CHUNK_BYTES = 64 * 1024
ALLOWED_SCANNER_HOSTS = frozenset(
    ("clamd", "material-rag-clamd", "localhost", "127.0.0.1", "::1")
)
ALLOWED_SCANNER_SOCKET = "/run/material-rag-clamd/clamd.sock"


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


def _configured_scanner_host(host: str) -> str:
    if host != "clamd":
        return host
    configured = os.environ.get("F1_CLAMD_HOST")
    if configured:
        return configured
    return host


def scanner_version(
    *,
    host: str = "clamd",
    port: int = 3310,
    timeout_seconds: int = SCAN_TIMEOUT_SECONDS,
) -> ScannerVersion:
    return _scanner_version_at(host, port, timeout_seconds)


_SCANNER_OS_PHASES = frozenset({"connect", "version", "stream"})
_SCANNER_ERRNO_NAMES = {
    errno.ECONNREFUSED: "ECONNREFUSED",
    errno.ECONNRESET: "ECONNRESET",
    errno.EPIPE: "EPIPE",
    errno.ETIMEDOUT: "ETIMEDOUT",
    errno.ENOENT: "ENOENT",
}
_SCANNER_ERRNO_SUFFIX = {
    errno.ECONNREFUSED: "REFUSED",
    errno.ECONNRESET: "RESET",
    errno.EPIPE: "PIPE",
}
SCANNER_PHASE_FAILURE_CODES = frozenset(
    {
        "P3_SCANNER_CONNECT_REFUSED",
        "P3_SCANNER_CONNECT_RESET",
        "P3_SCANNER_CONNECT_PIPE",
        "P3_SCANNER_VERSION_REFUSED",
        "P3_SCANNER_VERSION_RESET",
        "P3_SCANNER_VERSION_PIPE",
        "P3_SCANNER_STREAM_REFUSED",
        "P3_SCANNER_STREAM_RESET",
        "P3_SCANNER_STREAM_PIPE",
    }
)


def classify_scanner_os_error(error: OSError, *, phase: str) -> str:
    if phase not in _SCANNER_OS_PHASES:
        return "P3_SCANNER_UNAVAILABLE"
    if isinstance(error, (TimeoutError, socket.timeout)) or error.errno in {
        errno.ETIMEDOUT,
    }:
        return "P3_SCANNER_TIMEOUT"
    if isinstance(error, socket.gaierror):
        return "P3_SCANNER_DNS_FAILED"
    suffix = _SCANNER_ERRNO_SUFFIX.get(error.errno)
    if suffix is None:
        return "P3_SCANNER_UNAVAILABLE"
    code = f"P3_SCANNER_{phase.upper()}_{suffix}"
    if code not in SCANNER_PHASE_FAILURE_CODES:
        return "P3_SCANNER_UNAVAILABLE"
    return code


def _scanner_errno_token(error: OSError | None) -> str:
    if error is None:
        return "NONE"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "ETIMEDOUT"
    return _SCANNER_ERRNO_NAMES.get(error.errno, "OTHER")


def _scanner_addr_class(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> str:
    if address.is_loopback:
        return "LOOPBACK"
    if address.is_link_local:
        return "LINK_LOCAL"
    if address.is_private:
        return f"PRIVATE_IPV{address.version}"
    return "OTHER"


def _raise_scanner_os_error(error: OSError, *, phase: str) -> None:
    raise ScanFailure(
        classify_scanner_os_error(error, phase=phase), retryable=True
    ) from error


def _open_scanner_connection(
    host: str, port: int, timeout_seconds: int
) -> socket.socket:
    socket_path = os.environ.get("F1_CLAMD_SOCKET")
    if socket_path:
        if socket_path != ALLOWED_SCANNER_SOCKET:
            raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(timeout_seconds)
        try:
            connection.connect(socket_path)
        except OSError as error:
            connection.close()
            _raise_scanner_os_error(error, phase="connect")
            raise
        return connection
    last_error: OSError | None = None
    for address in _resolve_targets(
        _configured_scanner_host(host), port, timeout_seconds
    ):
        try:
            connection = socket.create_connection(
                (address, port), timeout=timeout_seconds
            )
        except OSError as error:
            last_error = error
            continue
        connection.settimeout(timeout_seconds)
        return connection
    if last_error is not None:
        _raise_scanner_os_error(last_error, phase="connect")
        raise
    raise ScanFailure("P3_SCANNER_UNAVAILABLE", retryable=True)


def _scanner_version_at(
    host: str, port: int, timeout_seconds: int
) -> ScannerVersion:
    connection = _open_scanner_connection(host, port, timeout_seconds)
    try:
        connection.sendall(b"zVERSION\x00")
        response = _receive_response(connection)
    except OSError as error:
        _raise_scanner_os_error(error, phase="version")
        raise
    finally:
        connection.close()
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
    if not 0 < expected_size <= MAX_SCAN_BYTES:
        raise ScanFailure("P3_SCAN_SIZE_INVALID", retryable=False)

    digest = hashlib.sha256()
    observed = 0
    version = _scanner_version_at(host, port, timeout_seconds)
    try:
        file_obj.seek(0)
        connection = _open_scanner_connection(host, port, timeout_seconds)
        try:
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
        finally:
            connection.close()
    except ScanFailure:
        raise
    except OSError as error:
        _raise_scanner_os_error(error, phase="stream")
        raise
    except Exception as error:
        raise ScanFailure("P3_SOURCE_READ_FAILED", retryable=True) from error
    try:
        file_obj.seek(0)
    except Exception as error:
        raise ScanFailure("P3_SOURCE_READ_FAILED", retryable=True) from error

    if observed != expected_size or digest.hexdigest() != expected_sha256:
        raise ScanFailure("P3_SOURCE_IDENTITY_MISMATCH", retryable=False)
    return parse_clamd_response(bytes(response), version=version)


LOOPBACK_SCANNER_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))


def _allowed_scanner_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(address.is_private or address.is_loopback)


def _select_scanner_address(
    host: str, usable: list[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return _order_scanner_addresses(host, usable)[0]


def _order_scanner_addresses(
    host: str, usable: list[ipaddress.IPv4Address | ipaddress.IPv6Address]
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    loopback = [address for address in usable if address.is_loopback]
    remote = [address for address in usable if not address.is_loopback]
    if host in LOOPBACK_SCANNER_HOSTS:
        candidates = loopback + remote
    else:
        candidates = remote + loopback
    preferred = [address for address in candidates if address.version == 4]
    fallback = [address for address in candidates if address.version != 4]
    return sorted(preferred, key=int) + sorted(fallback, key=int)


def _scanner_addrinfo(host: str, port: int) -> list[tuple]:
    last_error: OSError | None = None
    for family in (socket.AF_INET, socket.AF_UNSPEC):
        try:
            items = socket.getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as error:
            last_error = error
            continue
        if items:
            return items
    if last_error is not None:
        raise last_error
    raise socket.gaierror(socket.EAI_NONAME, "scanner host has no addresses")


def _resolve_targets(host: str, port: int, timeout_seconds: int) -> list[str]:
    if host not in ALLOWED_SCANNER_HOSTS or not 1 <= port <= 65535:
        raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    if timeout_seconds < 1 or timeout_seconds > SCAN_TIMEOUT_SECONDS:
        raise ScanFailure("P3_SCAN_TIMEOUT_INVALID", retryable=False)
    try:
        addresses = {
            item[4][0]
            for item in _scanner_addrinfo(host, port)
        }
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as error:
        raise ScanFailure("P3_SCANNER_UNAVAILABLE", retryable=True) from error
    except OSError as error:
        _raise_scanner_os_error(error, phase="connect")
        raise
    if any(
        not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
        )
        for address in parsed
    ):
        raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    usable = [address for address in parsed if _allowed_scanner_address(address)]
    if not usable:
        raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    ordered = _order_scanner_addresses(host, usable)
    if host not in LOOPBACK_SCANNER_HOSTS:
        ordered = [address for address in ordered if not address.is_loopback]
        if not ordered:
            raise ScanFailure("P3_SCANNER_TARGET_INVALID", retryable=False)
    return [str(address) for address in ordered]


def _resolve_target(host: str, port: int, timeout_seconds: int) -> str:
    return _resolve_targets(host, port, timeout_seconds)[0]


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


def diagnose_scanner_preflight(
    *,
    host: str = "clamd",
    port: int = 3310,
    timeout_seconds: int = SCAN_TIMEOUT_SECONDS,
) -> dict[str, str]:
    """Return fixed-token ClamAV preflight facts. Never includes addresses."""
    report = {
        "PHASE": "RESOLVE",
        "ADDR_CLASS": "NONE",
        "ADDR_COUNT": "0",
        "CONNECT_ERRNO": "NONE",
        "SEND_ERRNO": "NONE",
        "RECV_ERRNO": "NONE",
        "SCAN_CODE": "P3_SCANNER_UNAVAILABLE",
        "VERSION_OK": "NO",
    }
    try:
        targets = _resolve_targets(
            _configured_scanner_host(host), port, timeout_seconds
        )
    except ScanFailure as error:
        report["SCAN_CODE"] = error.code
        return report
    classes = {_scanner_addr_class(ipaddress.ip_address(target)) for target in targets}
    report["ADDR_COUNT"] = str(len(targets)) if len(targets) <= 16 else "MANY"
    report["ADDR_CLASS"] = next(iter(classes)) if len(classes) == 1 else "MIXED"
    last_connect: OSError | None = None
    connection: socket.socket | None = None
    for address in targets:
        try:
            connection = socket.create_connection(
                (address, port), timeout=timeout_seconds
            )
            report["CONNECT_ERRNO"] = "NONE"
            report["PHASE"] = "CONNECT"
            break
        except OSError as error:
            last_connect = error
            continue
    if connection is None:
        report["PHASE"] = "CONNECT"
        report["CONNECT_ERRNO"] = _scanner_errno_token(last_connect)
        if last_connect is None:
            return report
        report["SCAN_CODE"] = classify_scanner_os_error(
            last_connect, phase="connect"
        )
        return report
    connection.settimeout(timeout_seconds)
    try:
        report["PHASE"] = "VERSION"
        try:
            connection.sendall(b"zVERSION\x00")
        except OSError as error:
            report["SEND_ERRNO"] = _scanner_errno_token(error)
            report["SCAN_CODE"] = classify_scanner_os_error(error, phase="version")
            return report
        report["SEND_ERRNO"] = "NONE"
        try:
            response = _receive_response(connection)
        except OSError as error:
            report["RECV_ERRNO"] = _scanner_errno_token(error)
            report["SCAN_CODE"] = classify_scanner_os_error(error, phase="version")
            return report
        report["RECV_ERRNO"] = "NONE"
        parse_clamd_version(response)
    except ScanFailure as error:
        report["SCAN_CODE"] = error.code
        return report
    finally:
        connection.close()
    report["VERSION_OK"] = "YES"
    report["SCAN_CODE"] = "P3_SCANNER_VERSION_OK"
    report["PHASE"] = "OK"
    return report


__all__ = (
    "ALLOWED_SCANNER_HOSTS",
    "MAX_SCAN_BYTES",
    "ScanFailure",
    "ScanResult",
    "ScannerVersion",
    "classify_scanner_os_error",
    "diagnose_scanner_preflight",
    "parse_clamd_response",
    "parse_clamd_version",
    "scan_stream",
    "scanner_version",
)
