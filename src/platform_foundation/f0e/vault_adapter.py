"""Verified read-only descriptor hand-off for the existing local vault.

The adapter intentionally reuses the vault's validated directory descriptor and
its no-follow, owner, mode, link-count, and digest checks.  It never resolves or
returns a filesystem path.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from typing import Iterator

from ..vault import (
    LocalFixtureVault,
    VaultError,
    _hash_fd,
    _is_opaque_name,
    _open_named_file,
    _safe_fstat,
    _validate_expected_metadata,
    _validate_file_fd,
    _validate_read_only_source_fd,
)
from .contracts import F0EError


class VerifiedSourceFd:
    """Capability for one verified, regular, read-only object descriptor."""

    __slots__ = ("_fd", "_identity", "_sha256", "_size")

    def __init__(self, descriptor: int, sha256: str, size: int) -> None:
        try:
            metadata = _validate_read_only_source_fd(descriptor)
            _validate_file_fd(descriptor, expected_mode=0o600)
        except VaultError:
            raise F0EError("SOURCE_OBJECT_INVALID") from None
        self._fd = descriptor
        self._sha256 = sha256
        self._size = size
        self._identity = _identity(metadata)

    def __enter__(self) -> VerifiedSourceFd:
        self.reverify()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self._fd < 0 else "open"
        return f"VerifiedSourceFd(size={self._size}, state={state!r})"

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def size(self) -> int:
        return self._size

    def fileno(self) -> int:
        if self._fd < 0:
            raise F0EError("SOURCE_FD_CLOSED")
        return self._fd

    def reverify(self) -> None:
        descriptor = self.fileno()
        try:
            metadata = _validate_read_only_source_fd(descriptor)
            _validate_file_fd(descriptor, expected_mode=0o600)
            if _identity(metadata) != self._identity or metadata.st_size != self._size:
                raise F0EError("SOURCE_OBJECT_CHANGED")
            actual_sha256, actual_size = _hash_fd(descriptor, self._size)
            after = _safe_fstat(descriptor, "OBJECT_STAT_FAILED")
        except F0EError:
            raise
        except VaultError:
            raise F0EError("SOURCE_OBJECT_CHANGED") from None
        if (
            _identity(after) != self._identity
            or actual_sha256 != self._sha256
            or actual_size != self._size
        ):
            raise F0EError("SOURCE_OBJECT_CHANGED")

    def close(self) -> None:
        descriptor = self._fd
        self._fd = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


@contextmanager
def open_verified_source(
    vault: LocalFixtureVault,
    object_id: str,
    expected_sha256: str,
    expected_size: int,
) -> Iterator[VerifiedSourceFd]:
    """Open a registered vault object without exposing its path or body."""

    descriptor = -1
    source: VerifiedSourceFd | None = None
    try:
        if not isinstance(vault, LocalFixtureVault):
            raise F0EError("SOURCE_OBJECT_INVALID")
        _validate_expected_metadata(expected_sha256, expected_size)
        if not _is_opaque_name(object_id):
            raise F0EError("SOURCE_OBJECT_INVALID")
        vault.verify(object_id, expected_sha256, expected_size)
        vault._validate_operating_directories()
        descriptor = _open_named_file(vault._final_fd, object_id)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            raise F0EError("SOURCE_OBJECT_INVALID") from None
        metadata = _validate_file_fd(descriptor, expected_mode=0o600)
        actual_sha256, actual_size = _hash_fd(descriptor, expected_size)
        after = _safe_fstat(descriptor, "OBJECT_STAT_FAILED")
        if (
            _identity(after) != _identity(metadata)
            or actual_sha256 != expected_sha256
            or actual_size != expected_size
        ):
            raise F0EError("SOURCE_OBJECT_CHANGED")
        source = VerifiedSourceFd(descriptor, expected_sha256, expected_size)
        descriptor = -1
        yield source
        source.reverify()
    except F0EError:
        raise
    except VaultError:
        raise F0EError("SOURCE_OBJECT_INVALID") from None
    except (OSError, TypeError, ValueError):
        raise F0EError("SOURCE_OBJECT_INVALID") from None
    finally:
        if source is not None:
            source.close()
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = ("VerifiedSourceFd", "open_verified_source")
