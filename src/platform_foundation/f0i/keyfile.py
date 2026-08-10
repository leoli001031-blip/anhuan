"""Owner-only F0-I canonical-body key capability."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0IError


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
ACCEPTANCE_KEY_FILE = (
    str(
        _FROZEN_F0_ISOLATION.tmp_dir
        / f"anhuan-f0i-{_FROZEN_F0_ISOLATION.project_id.hex}-acceptance.key"
    )
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp/anhuan-f0i-acceptance-v01.key"
)
_KEY_BYTES = 32
_PREFIX = "anhuan-f0i-"
_DOMAIN = b"F0I_LOCAL_CANONICAL_KEY_V1\0"


class LocalCanonicalKey:
    __slots__ = ("_buffer", "_fingerprint")

    def __init__(self, value: bytes | bytearray | memoryview) -> None:
        try:
            view = memoryview(value).cast("B")
        except (TypeError, ValueError):
            raise F0IError("KEYFILE_INVALID") from None
        if len(view) != _KEY_BYTES:
            raise F0IError("KEYFILE_INVALID")
        self._buffer = bytearray(view)
        self._fingerprint = hashlib.sha256(_DOMAIN + self._buffer).hexdigest()

    def __enter__(self) -> LocalCanonicalKey:
        if len(self._buffer) != _KEY_BYTES:
            raise F0IError("KEYFILE_INVALID")
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()

    def __repr__(self) -> str:
        return "LocalCanonicalKey(state=%r)" % (
            "loaded" if self._buffer else "wiped"
        )

    __str__ = __repr__

    @property
    def fingerprint_sha256(self) -> str:
        return self._fingerprint

    def view(self) -> memoryview:
        if len(self._buffer) != _KEY_BYTES:
            raise F0IError("KEYFILE_INVALID")
        return memoryview(self._buffer).toreadonly()

    def wipe(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._buffer.clear()


def create_keyfile(path: str) -> str:
    directory, name = _validated_target(path)
    descriptor = -1
    dirfd = -1
    material = bytearray(os.urandom(_KEY_BYTES))
    try:
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=dirfd,
        )
        _validate_fd(descriptor)
        written = 0
        while written < len(material):
            count = os.write(descriptor, memoryview(material)[written:])
            if count <= 0:
                raise F0IError("KEYFILE_NOT_AVAILABLE")
            written += count
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != _KEY_BYTES:
            raise F0IError("KEYFILE_INVALID")
        os.fsync(dirfd)
        return hashlib.sha256(_DOMAIN + material).hexdigest()
    except FileExistsError:
        raise F0IError("KEYFILE_ALREADY_EXISTS") from None
    except F0IError:
        raise
    except OSError:
        raise F0IError("KEYFILE_NOT_AVAILABLE") from None
    finally:
        material[:] = b"\0" * len(material)
        material.clear()
        if descriptor >= 0:
            os.close(descriptor)
        if dirfd >= 0:
            os.close(dirfd)


def load_keyfile(path: str) -> LocalCanonicalKey:
    directory, name = _validated_target(path)
    descriptor = -1
    dirfd = -1
    material = bytearray()
    try:
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=dirfd,
        )
        before = _validate_fd(descriptor)
        if before.st_size != _KEY_BYTES:
            raise F0IError("KEYFILE_INVALID")
        while len(material) < _KEY_BYTES:
            chunk = os.read(descriptor, _KEY_BYTES - len(material))
            if not chunk:
                break
            material.extend(chunk)
        if len(material) != _KEY_BYTES or os.read(descriptor, 1):
            raise F0IError("KEYFILE_INVALID")
        after = _validate_fd(descriptor)
        if _identity(before) != _identity(after):
            raise F0IError("KEYFILE_INVALID")
        return LocalCanonicalKey(material)
    except F0IError:
        raise
    except OSError:
        raise F0IError("KEYFILE_NOT_AVAILABLE") from None
    finally:
        material[:] = b"\0" * len(material)
        material.clear()
        if descriptor >= 0:
            os.close(descriptor)
        if dirfd >= 0:
            os.close(dirfd)


def _validated_target(path: object) -> tuple[str, str]:
    if not isinstance(path, str) or "\0" in path or not os.path.isabs(path):
        raise F0IError("KEYFILE_INVALID")
    normalized = os.path.normpath(path)
    directory, name = os.path.split(normalized)
    if _FROZEN_F0_ISOLATION is not None:
        prefix = f"anhuan-f0i-{_FROZEN_F0_ISOLATION.project_id.hex}-"
        if (
            normalized != path
            or directory != str(_FROZEN_F0_ISOLATION.tmp_dir)
            or not name.startswith(prefix)
            or not name.endswith(".key")
            or len(name) > 96
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-."
                for character in name
            )
        ):
            raise F0IError("KEYFILE_INVALID")
        try:
            parent = os.lstat(directory)
            if (
                Path(directory).resolve(strict=True) != Path(directory)
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) != 0o700
            ):
                raise F0IError("KEYFILE_INVALID")
        except F0IError:
            raise
        except OSError:
            raise F0IError("KEYFILE_NOT_AVAILABLE") from None
        return directory, name
    if (
        directory != "/private/tmp"
        or normalized != path
        or not name.startswith(_PREFIX)
        or not name.endswith(".key")
        or len(name) > 96
        or any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789-."
            for character in name
        )
    ):
        raise F0IError("KEYFILE_INVALID")
    try:
        parent = os.lstat(directory)
    except OSError:
        raise F0IError("KEYFILE_NOT_AVAILABLE") from None
    if not stat.S_ISDIR(parent.st_mode):
        raise F0IError("KEYFILE_INVALID")
    return directory, name


def _validate_fd(descriptor: int) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise F0IError("KEYFILE_INVALID")
    return metadata


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = (
    "ACCEPTANCE_KEY_FILE",
    "LocalCanonicalKey",
    "create_keyfile",
    "load_keyfile",
)
