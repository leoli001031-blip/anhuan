"""Fixed-token ClamAV preflight probe. Stdlib only; prints KEY=VALUE."""
from __future__ import annotations

import errno
import ipaddress
import os
import socket
import sys


ALLOWED_HOSTS = frozenset(
    ("clamd", "material-rag-clamd", "localhost", "127.0.0.1", "::1")
)
LOOPBACK_HOSTS = frozenset(("localhost", "127.0.0.1", "::1"))
ALLOWED_KEYS = frozenset(
    {
        "ADDR_CLASS",
        "ADDR_COUNT",
        "CONNECT_ERRNO",
        "PHASE",
        "RECV_ERRNO",
        "SCAN_CODE",
        "SEND_ERRNO",
        "VERSION_OK",
    }
)
VALUE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
ERRNO_NAMES = {
    errno.ECONNREFUSED: "ECONNREFUSED",
    errno.ECONNRESET: "ECONNRESET",
    errno.EPIPE: "EPIPE",
    errno.ETIMEDOUT: "ETIMEDOUT",
    errno.ENOENT: "ENOENT",
}
ERRNO_SUFFIX = {
    errno.ECONNREFUSED: "REFUSED",
    errno.ECONNRESET: "RESET",
    errno.EPIPE: "PIPE",
}


def _errno_token(error: OSError | None) -> str:
    if error is None:
        return "NONE"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "ETIMEDOUT"
    return ERRNO_NAMES.get(error.errno, "OTHER")


def _classify(error: OSError, *, phase: str) -> str:
    if phase not in {"connect", "version", "stream"}:
        return "P3_SCANNER_UNAVAILABLE"
    if isinstance(error, (TimeoutError, socket.timeout)) or error.errno in {
        errno.ETIMEDOUT,
    }:
        return "P3_SCANNER_TIMEOUT"
    if isinstance(error, socket.gaierror):
        return "P3_SCANNER_DNS_FAILED"
    suffix = ERRNO_SUFFIX.get(error.errno)
    if suffix is None:
        return "P3_SCANNER_UNAVAILABLE"
    return f"P3_SCANNER_{phase.upper()}_{suffix}"


def _addr_class(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if address.is_loopback:
        return "LOOPBACK"
    if address.is_link_local:
        return "LINK_LOCAL"
    if address.is_private:
        return f"PRIVATE_IPV{address.version}"
    return "OTHER"


def _configured_host() -> str:
    host = os.environ.get("F1_CLAMD_HOST") or "material-rag-clamd"
    if host not in ALLOWED_HOSTS:
        return ""
    return host


def _resolve(host: str, port: int) -> list[str] | str:
    if host not in ALLOWED_HOSTS or not 1 <= port <= 65535:
        return "P3_SCANNER_TARGET_INVALID"
    last_error: OSError | None = None
    items: list[tuple] = []
    for family in (socket.AF_INET, socket.AF_UNSPEC):
        try:
            found = socket.getaddrinfo(
                host,
                port,
                family=family,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except OSError as error:
            last_error = error
            continue
        if found:
            items = found
            break
    if not items:
        if last_error is not None:
            return _classify(last_error, phase="connect")
        return "P3_SCANNER_DNS_FAILED"
    parsed = [ipaddress.ip_address(item[4][0]) for item in items]
    if any(
        not (address.is_private or address.is_loopback or address.is_link_local)
        for address in parsed
    ):
        return "P3_SCANNER_TARGET_INVALID"
    usable = [
        address
        for address in parsed
        if address.is_private or address.is_loopback
    ]
    if not usable:
        return "P3_SCANNER_TARGET_INVALID"
    loopback = [address for address in usable if address.is_loopback]
    remote = [address for address in usable if not address.is_loopback]
    if host in LOOPBACK_HOSTS:
        candidates = loopback + remote
    else:
        candidates = remote + loopback
        candidates = [address for address in candidates if not address.is_loopback]
        if not candidates:
            return "P3_SCANNER_TARGET_INVALID"
    preferred = [address for address in candidates if address.version == 4]
    fallback = [address for address in candidates if address.version != 4]
    ordered = sorted(preferred, key=int) + sorted(fallback, key=int)
    return [str(address) for address in ordered]


def diagnose() -> dict[str, str]:
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
    host = _configured_host()
    if not host:
        report["SCAN_CODE"] = "P3_SCANNER_TARGET_INVALID"
        return report
    resolved = _resolve(host, 3310)
    if isinstance(resolved, str):
        report["SCAN_CODE"] = resolved
        return report
    classes = {_addr_class(ipaddress.ip_address(target)) for target in resolved}
    report["ADDR_COUNT"] = str(len(resolved)) if len(resolved) <= 16 else "MANY"
    report["ADDR_CLASS"] = next(iter(classes)) if len(classes) == 1 else "MIXED"
    last_connect: OSError | None = None
    connection: socket.socket | None = None
    for address in resolved:
        try:
            connection = socket.create_connection((address, 3310), timeout=10)
            report["CONNECT_ERRNO"] = "NONE"
            report["PHASE"] = "CONNECT"
            break
        except OSError as error:
            last_connect = error
            continue
    if connection is None:
        report["PHASE"] = "CONNECT"
        report["CONNECT_ERRNO"] = _errno_token(last_connect)
        if last_connect is None:
            return report
        report["SCAN_CODE"] = _classify(last_connect, phase="connect")
        return report
    connection.settimeout(10)
    try:
        report["PHASE"] = "VERSION"
        try:
            connection.sendall(b"zVERSION\x00")
        except OSError as error:
            report["SEND_ERRNO"] = _errno_token(error)
            report["SCAN_CODE"] = _classify(error, phase="version")
            return report
        report["SEND_ERRNO"] = "NONE"
        try:
            response = bytearray()
            while len(response) <= 4096:
                piece = connection.recv(min(512, 4097 - len(response)))
                if not piece:
                    break
                response.extend(piece)
                if b"\x00" in piece or piece.endswith(b"\n"):
                    break
        except OSError as error:
            report["RECV_ERRNO"] = _errno_token(error)
            report["SCAN_CODE"] = _classify(error, phase="version")
            return report
        report["RECV_ERRNO"] = "NONE"
        normalized = bytes(response).rstrip(b"\x00\r\n")
        if not normalized.startswith(b"ClamAV "):
            report["SCAN_CODE"] = "P3_SCAN_PROTOCOL_ERROR"
            return report
    finally:
        connection.close()
    report["VERSION_OK"] = "YES"
    report["SCAN_CODE"] = "P3_SCANNER_VERSION_OK"
    report["PHASE"] = "OK"
    return report


def main() -> int:
    report = diagnose()
    for key in sorted(ALLOWED_KEYS):
        value = report.get(key, "UNAVAILABLE")
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 64
            or any(character not in VALUE_CHARS for character in value)
        ):
            value = "UNAVAILABLE"
        sys.stdout.write(f"{key}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
