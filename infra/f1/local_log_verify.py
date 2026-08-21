"""Body-free runtime log boundary checks for the local closeout stack."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from collections.abc import Iterable, Mapping


CORE_SERVICES = frozenset(
    {
        "api",
        "clamd",
        "dispatcher",
        "keycloak",
        "minio",
        "postgres",
        "redis",
        "web",
        "worker",
    }
)
MAX_SERVICE_LOG_BYTES = 4 * 1024 * 1024
MAX_TOTAL_LOG_BYTES = 16 * 1024 * 1024

_DSN_RE = re.compile(rb"postgres(?:ql)?(?:\+[a-z0-9_]+)?://", re.IGNORECASE)
_AUTH_RE = re.compile(
    rb"authorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+",
    re.IGNORECASE,
)
_JWT_RE = re.compile(
    rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_EMAIL_RE = re.compile(
    rb"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE
)
_PHONE_RE = re.compile(rb"(?<![0-9])1[3-9][0-9]{9}(?![0-9])")
_OIDC_QUERY_RE = re.compile(
    rb"(?:[?&]|\b)(?:code|state|session_state|nonce)=[^\s&]+",
    re.IGNORECASE,
)
_QUERY_STRING_RE = re.compile(
    rb"(?:\?|&)[A-Za-z0-9_%.-]{1,64}=[^\s&]+",
)
_FILE_RE = re.compile(
    rb"\b[^\s/\\]{1,128}\.(?:pdf|docx|xlsx|jpg|jpeg|png)\b", re.IGNORECASE
)
_BODY_MARKERS = (
    b"LOCAL_INGESTION_SYNTHETIC",
    b"local-ingestion.pdf",
)


class LogVerificationError(RuntimeError):
    """Fixed-code error that never contains log or secret content."""


@dataclass(frozen=True, slots=True)
class LogVerificationCounts:
    backup_scanned_file_count: int
    backup_secret_leak_count: int
    core_service_log_count: int
    log_body_marker_leak_count: int
    log_byte_limit_exceeded_count: int
    log_dsn_leak_count: int
    log_email_leak_count: int
    log_filename_leak_count: int
    log_oidc_query_leak_count: int
    log_phone_leak_count: int
    log_query_string_leak_count: int
    log_secret_leak_count: int
    log_token_leak_count: int
    repository_scanned_file_count: int
    repository_secret_leak_count: int


def _matches(pattern: re.Pattern[bytes], logs: Iterable[bytes]) -> int:
    return sum(1 for body in logs if pattern.search(body) is not None)


def verify_runtime_logs(
    service_logs: Mapping[str, bytes],
    *,
    secret_values: Iterable[bytes],
    repository_scanned_file_count: int,
    repository_secret_leak_count: int,
    backup_scanned_file_count: int,
    backup_secret_leak_count: int,
    extra_body_markers: Iterable[bytes] = (),
) -> LogVerificationCounts:
    """Return aggregate-only counts for the exact nine-service log snapshot."""
    if set(service_logs) != CORE_SERVICES:
        raise LogVerificationError("LOCAL_LOG_SERVICE_SET_INVALID")
    if any(type(body) is not bytes for body in service_logs.values()):
        raise LogVerificationError("LOCAL_LOG_PAYLOAD_INVALID")
    evidence_counts = (
        repository_scanned_file_count,
        repository_secret_leak_count,
        backup_scanned_file_count,
        backup_secret_leak_count,
    )
    if any(type(value) is not int or value < 0 for value in evidence_counts):
        raise LogVerificationError("LOCAL_LOG_EVIDENCE_COUNT_INVALID")

    logs = tuple(service_logs[name] for name in sorted(CORE_SERVICES))
    byte_limit_exceeded = sum(
        1 for body in logs if len(body) > MAX_SERVICE_LOG_BYTES
    )
    if sum(map(len, logs)) > MAX_TOTAL_LOG_BYTES:
        byte_limit_exceeded += 1

    secrets = tuple(
        value for value in secret_values if type(value) is bytes and len(value) >= 8
    )
    markers = tuple(
        marker
        for marker in (*_BODY_MARKERS, *extra_body_markers)
        if type(marker) is bytes and len(marker) >= 8
    )
    secret_leaks = sum(
        1 for secret in secrets for body in logs if secret in body
    )
    marker_leaks = sum(
        1 for marker in markers for body in logs if marker in body
    )
    token_leaks = _matches(_AUTH_RE, logs) + _matches(_JWT_RE, logs)

    return LogVerificationCounts(
        backup_scanned_file_count=backup_scanned_file_count,
        backup_secret_leak_count=backup_secret_leak_count,
        core_service_log_count=len(logs),
        log_body_marker_leak_count=marker_leaks,
        log_byte_limit_exceeded_count=byte_limit_exceeded,
        log_dsn_leak_count=_matches(_DSN_RE, logs),
        log_email_leak_count=_matches(_EMAIL_RE, logs),
        log_filename_leak_count=_matches(_FILE_RE, logs),
        log_oidc_query_leak_count=_matches(_OIDC_QUERY_RE, logs),
        log_phone_leak_count=_matches(_PHONE_RE, logs),
        log_query_string_leak_count=_matches(_QUERY_STRING_RE, logs),
        log_secret_leak_count=secret_leaks,
        log_token_leak_count=token_leaks,
        repository_scanned_file_count=repository_scanned_file_count,
        repository_secret_leak_count=repository_secret_leak_count,
    )


def render_success(counts: LogVerificationCounts) -> tuple[str, str]:
    payload = asdict(counts)
    if (
        payload["core_service_log_count"] != len(CORE_SERVICES)
        or payload["repository_scanned_file_count"] < 1
        or payload["backup_scanned_file_count"] < 0
    ):
        raise LogVerificationError("LOCAL_LOG_EVIDENCE_COUNT_INVALID")
    failure_reasons = (
        ("backup_secret_leak_count", "LOCAL_LOG_BACKUP_SECRET_LEAK"),
        ("repository_secret_leak_count", "LOCAL_LOG_REPOSITORY_SECRET_LEAK"),
        ("log_byte_limit_exceeded_count", "LOCAL_LOG_SIZE_LIMIT"),
        ("log_secret_leak_count", "LOCAL_LOG_RUNTIME_SECRET_LEAK"),
        ("log_dsn_leak_count", "LOCAL_LOG_DSN_LEAK"),
        ("log_token_leak_count", "LOCAL_LOG_TOKEN_LEAK"),
        ("log_query_string_leak_count", "LOCAL_LOG_QUERY_STRING_LEAK"),
        ("log_oidc_query_leak_count", "LOCAL_LOG_OIDC_QUERY_LEAK"),
        ("log_email_leak_count", "LOCAL_LOG_EMAIL_LEAK"),
        ("log_phone_leak_count", "LOCAL_LOG_PHONE_LEAK"),
        ("log_filename_leak_count", "LOCAL_LOG_FILENAME_LEAK"),
        ("log_body_marker_leak_count", "LOCAL_LOG_BODY_MARKER_LEAK"),
    )
    for key, reason in failure_reasons:
        if payload[key] != 0:
            raise LogVerificationError(reason)
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        "LOCAL_LOG_VERIFY_OK",
    )
