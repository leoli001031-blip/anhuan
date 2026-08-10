"""A fixed host-level flock guarding F0-I replay and every OCR invocation."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import re
import stat

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import F0IError


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
DEFAULT_HOST_LOCK_PATH = (
    str(
        _FROZEN_F0_ISOLATION.tmp_dir
        / f"anhuan-f0i-{_FROZEN_F0_ISOLATION.project_id.hex}-replay.lock"
    )
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp/anhuan-f0i-replay.lock"
)
_LOCK_NAME = re.compile(r"^anhuan-f0i-[a-z0-9][a-z0-9-]{0,63}\.lock$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)


class HostReplayLock:
    """Owner-only nonblocking process mutex; display never exposes its path."""

    __slots__ = ("_descriptor", "_path")

    def __init__(self, path: str = DEFAULT_HOST_LOCK_PATH) -> None:
        self._path = _validated_path(path)
        self._descriptor = -1

    def __enter__(self) -> HostReplayLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def __repr__(self) -> str:
        state = "held" if self._descriptor >= 0 else "released"
        return f"HostReplayLock(state={state!r})"

    __str__ = __repr__

    @property
    def held(self) -> bool:
        return self._descriptor >= 0

    def acquire(self) -> None:
        if self._descriptor >= 0:
            raise F0IError("LOCK_INVALID")
        descriptor = -1
        try:
            descriptor = os.open(
                self._path,
                os.O_RDWR | os.O_CREAT | _NOFOLLOW | _CLOEXEC,
                0o600,
            )
            before = _validate_descriptor(descriptor)
            path_metadata = os.lstat(self._path)
            if _identity(before) != _identity(path_metadata):
                raise F0IError("LOCK_INVALID")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                    raise F0IError("LOCK_UNAVAILABLE") from None
                raise F0IError("LOCK_INVALID") from None
            after = _validate_descriptor(descriptor)
            if _identity(before) != _identity(after):
                raise F0IError("LOCK_INVALID")
            self._descriptor = descriptor
            descriptor = -1
        except F0IError:
            raise
        except (OSError, TypeError, ValueError):
            raise F0IError("LOCK_INVALID") from None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    raise F0IError("LOCK_INVALID") from None

    def release(self) -> None:
        descriptor = self._descriptor
        self._descriptor = -1
        if descriptor < 0:
            return
        failed = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            failed = True
        try:
            os.close(descriptor)
        except OSError:
            failed = True
        if failed:
            raise F0IError("LOCK_INVALID")


def host_replay_lock(path: str = DEFAULT_HOST_LOCK_PATH) -> HostReplayLock:
    """Return the sole lock capability used by replay and OCR orchestration."""

    return HostReplayLock(path)


def _validated_path(value: object) -> str:
    if not isinstance(value, str) or "\0" in value or not os.path.isabs(value):
        raise F0IError("LOCK_INVALID")
    normalized = os.path.normpath(value)
    directory, name = os.path.split(normalized)
    if _FROZEN_F0_ISOLATION is not None:
        prefix = f"anhuan-f0i-{_FROZEN_F0_ISOLATION.project_id.hex}-"
        if (
            value != normalized
            or directory != str(_FROZEN_F0_ISOLATION.tmp_dir)
            or not name.startswith(prefix)
            or _LOCK_NAME.fullmatch(name) is None
        ):
            raise F0IError("LOCK_INVALID")
        try:
            parent = os.lstat(directory)
            if (
                Path(directory).resolve(strict=True) != Path(directory)
                or not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.getuid()
                or stat.S_IMODE(parent.st_mode) != 0o700
            ):
                raise F0IError("LOCK_INVALID")
        except F0IError:
            raise
        except OSError:
            raise F0IError("LOCK_INVALID") from None
        return normalized
    if (
        value != normalized
        or directory != "/private/tmp"
        or _LOCK_NAME.fullmatch(name) is None
    ):
        raise F0IError("LOCK_INVALID")
    try:
        parent = os.lstat(directory)
    except OSError:
        raise F0IError("LOCK_INVALID") from None
    if not stat.S_ISDIR(parent.st_mode):
        raise F0IError("LOCK_INVALID")
    return normalized


def _validate_descriptor(descriptor: int) -> os.stat_result:
    metadata = os.fstat(descriptor)
    flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
        or metadata.st_size != 0
        or flags & os.O_ACCMODE != os.O_RDWR
        or flags & getattr(os, "O_APPEND", 0)
    ):
        raise F0IError("LOCK_INVALID")
    return metadata


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
    )


__all__ = (
    "DEFAULT_HOST_LOCK_PATH",
    "HostReplayLock",
    "host_replay_lock",
)
