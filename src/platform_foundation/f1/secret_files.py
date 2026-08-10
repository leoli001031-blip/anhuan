"""Fail-closed access to F1 runtime secret files.

Runtime code receives only absolute file or directory locations from the
environment.  Secret values are never embedded in source, returned in error
messages, or inferred from developer-machine paths.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class SecretFileError(RuntimeError):
    """A secret location or file failed the local trust contract."""


def _configured_path(
    *,
    file_env: str,
    directory_env: str | None,
    filename: str | None,
    unavailable_code: str,
) -> Path:
    raw = os.environ.get(file_env, "").strip()
    if not raw and directory_env is not None and filename is not None:
        directory = os.environ.get(directory_env, "").strip()
        if directory:
            raw = str(Path(directory) / filename)
    if not raw:
        raise SecretFileError(unavailable_code)
    path = Path(raw)
    if not path.is_absolute():
        raise SecretFileError(f"{unavailable_code}_PATH_INVALID")
    return path


def _read_secure(
    path: Path,
    *,
    unavailable_code: str,
    minimum_size: int,
    maximum_size: int,
) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise SecretFileError(unavailable_code) from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_size < minimum_size
            or info.st_size > maximum_size
        ):
            raise SecretFileError(f"{unavailable_code}_PERMISSIONS_INVALID")
        value = os.read(descriptor, maximum_size + 1)
        if len(value) != info.st_size:
            raise SecretFileError(f"{unavailable_code}_SIZE_CHANGED")
        return value
    finally:
        os.close(descriptor)


def read_f1_secret_text(name: str, *, file_env: str) -> str:
    path = _configured_path(
        file_env=file_env,
        directory_env="F1_SECRETS_DIR",
        filename=name,
        unavailable_code="F1_RUNTIME_SECRET_UNAVAILABLE",
    )
    raw = _read_secure(
        path,
        unavailable_code="F1_RUNTIME_SECRET_UNAVAILABLE",
        minimum_size=1,
        maximum_size=4096,
    )
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise SecretFileError("F1_RUNTIME_SECRET_ENCODING_INVALID") from None
    if not value:
        raise SecretFileError("F1_RUNTIME_SECRET_EMPTY")
    return value


def read_provider_secret_text(name: str, *, file_env: str) -> str:
    path = _configured_path(
        file_env=file_env,
        directory_env="F1_PROVIDER_SECRETS_DIR",
        filename=name,
        unavailable_code="F1_PROVIDER_SECRET_UNAVAILABLE",
    )
    raw = _read_secure(
        path,
        unavailable_code="F1_PROVIDER_SECRET_UNAVAILABLE",
        minimum_size=1,
        maximum_size=4096,
    )
    try:
        value = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise SecretFileError("F1_PROVIDER_SECRET_ENCODING_INVALID") from None
    if not value:
        raise SecretFileError("F1_PROVIDER_SECRET_EMPTY")
    return value


def read_f0i_key() -> bytes:
    path = _configured_path(
        file_env="F1_F0I_KEY_FILE",
        directory_env=None,
        filename=None,
        unavailable_code="F1_F0I_KEY_UNAVAILABLE",
    )
    return _read_secure(
        path,
        unavailable_code="F1_F0I_KEY_UNAVAILABLE",
        minimum_size=32,
        maximum_size=32,
    )


__all__ = (
    "SecretFileError",
    "read_f0i_key",
    "read_f1_secret_text",
    "read_provider_secret_text",
)
