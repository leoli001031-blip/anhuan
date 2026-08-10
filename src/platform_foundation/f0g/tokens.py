"""Owner-only random local Fixture session token bundle.

The file is a fixed-width 96-byte capability below ``/private/tmp``.  It is
never copied into the workspace, environment, request payload or artifact.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0GError


_ROLE_BYTES = 32
_BUNDLE_BYTES = _ROLE_BYTES * 3
_PREFIX = "anhuan-f0g-"
_ROLES = ("ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR")
_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
ACCEPTANCE_TOKEN_BUNDLE = (
    str(
        _FROZEN_F0_ISOLATION.tmp_dir
        / f"anhuan-f0g-{_FROZEN_F0_ISOLATION.project_id.hex}-acceptance.tokens"
    )
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp/anhuan-f0g-acceptance-v01.tokens"
)


class FixtureTokenBundle:
    __slots__ = ("_buffer", "_wiped")

    def __init__(self, value: bytes | bytearray | memoryview) -> None:
        try:
            view = memoryview(value).cast("B")
        except (TypeError, ValueError):
            raise F0GError("ANNOTATION_PREPARE_FAILED") from None
        if len(view) != _BUNDLE_BYTES:
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        self._buffer = bytearray(view)
        self._wiped = False

    def __enter__(self) -> FixtureTokenBundle:
        if self._wiped:
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()

    def __repr__(self) -> str:
        return f"FixtureTokenBundle(state={'wiped' if self._wiped else 'loaded'!r})"

    __str__ = __repr__

    def token(self, role: str) -> str:
        if self._wiped or role not in _ROLES:
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        offset = _ROLES.index(role) * _ROLE_BYTES
        return "f0g_" + self._buffer[offset : offset + _ROLE_BYTES].hex()

    def wipe(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._buffer.clear()
        self._wiped = True


def create_token_bundle(path: str) -> None:
    directory, name = _validated_target(path)
    material = bytearray(os.urandom(_BUNDLE_BYTES))
    descriptor = -1
    dirfd = -1
    created = False
    complete = False
    try:
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dirfd,
        )
        created = True
        _validate_fd(descriptor, write=True)
        offset = 0
        while offset < len(material):
            written = os.write(descriptor, memoryview(material)[offset:])
            if written <= 0:
                raise F0GError("ANNOTATION_PREPARE_FAILED")
            offset += written
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != _BUNDLE_BYTES:
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        os.fsync(dirfd)
        complete = True
    except FileExistsError:
        raise F0GError("ANNOTATION_STATE_INVALID") from None
    except F0GError:
        raise
    except OSError:
        raise F0GError("ANNOTATION_PREPARE_FAILED") from None
    finally:
        material[:] = b"\0" * len(material)
        material.clear()
        if descriptor >= 0:
            os.close(descriptor)
        if created and not complete and dirfd >= 0:
            _unlink_if_present(name, dirfd)
        if dirfd >= 0:
            os.close(dirfd)


def load_token_bundle(path: str) -> FixtureTokenBundle:
    directory, name = _validated_target(path)
    material = bytearray()
    descriptor = -1
    dirfd = -1
    try:
        dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=dirfd,
        )
        before = _validate_fd(descriptor, write=False)
        while len(material) < _BUNDLE_BYTES:
            chunk = os.read(descriptor, _BUNDLE_BYTES - len(material))
            if not chunk:
                break
            material.extend(chunk)
        if len(material) != _BUNDLE_BYTES or os.read(descriptor, 1):
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        after = _validate_fd(descriptor, write=False)
        if _identity(before) != _identity(after):
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        return FixtureTokenBundle(material)
    except F0GError:
        raise
    except OSError:
        raise F0GError("ANNOTATION_PREPARE_FAILED") from None
    finally:
        material[:] = b"\0" * len(material)
        material.clear()
        if descriptor >= 0:
            os.close(descriptor)
        if dirfd >= 0:
            os.close(dirfd)


def _unlink_if_present(name: str, dirfd: int) -> None:
    try:
        os.unlink(name, dir_fd=dirfd)
    except FileNotFoundError:
        return
    except OSError:
        raise F0GError("ANNOTATION_PREPARE_FAILED") from None


def _validated_target(path: object) -> tuple[str, str]:
    if not isinstance(path, str) or "\0" in path or not os.path.isabs(path):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    normalized = os.path.normpath(path)
    directory, name = os.path.split(normalized)
    if _FROZEN_F0_ISOLATION is not None:
        prefix = f"anhuan-f0g-{_FROZEN_F0_ISOLATION.project_id.hex}-"
        if (
            normalized != path
            or directory != str(_FROZEN_F0_ISOLATION.tmp_dir)
            or not name.startswith(prefix)
            or not name.endswith(".tokens")
            or len(name) > 96
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-."
                for character in name
            )
        ):
            raise F0GError("ANNOTATION_PREPARE_FAILED")
        try:
            parent = os.lstat(directory)
            if (
                Path(directory).resolve(strict=True) != Path(directory)
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) != 0o700
            ):
                raise F0GError("ANNOTATION_PREPARE_FAILED")
        except F0GError:
            raise
        except OSError:
            raise F0GError("ANNOTATION_PREPARE_FAILED") from None
        return directory, name
    if (
        normalized != path
        or directory != "/private/tmp"
        or not name.startswith(_PREFIX)
        or not name.endswith(".tokens")
        or len(name) > 96
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-." for character in name)
    ):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    try:
        parent = os.lstat(directory)
    except OSError:
        raise F0GError("ANNOTATION_PREPARE_FAILED") from None
    if not stat.S_ISDIR(parent.st_mode):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
    return directory, name


def _validate_fd(descriptor: int, *, write: bool) -> os.stat_result:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or (not write and metadata.st_size != _BUNDLE_BYTES)
    ):
        raise F0GError("ANNOTATION_PREPARE_FAILED")
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
    "ACCEPTANCE_TOKEN_BUNDLE",
    "FixtureTokenBundle",
    "create_token_bundle",
    "load_token_bundle",
)
