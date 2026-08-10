"""Fail-closed local object vault for registered fixture bytes.

This module deliberately implements only a local, fixture-only filesystem
adapter.  It does not provide production object-storage or WORM semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import secrets
import stat
from typing import AsyncIterable, Iterable, Iterator
import uuid


_PRIVATE_TMP = "/private/tmp"
_CHUNK_SIZE = 1024 * 1024
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
_FILE_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
_FILE_WRITE_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class VaultError(RuntimeError):
    """A path-free, stable-code vault failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class StagedObject:
    stage_id: str
    sha256: str
    size: int
    _vault_token: str = field(repr=False, compare=False)
    _device: int = field(repr=False, compare=False)
    _inode: int = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_id: str
    sha256: str
    size: int


class LocalFixtureVault:
    """A create-only fixture vault rooted below an explicit /private/tmp task.

    The instance keeps directory file descriptors open so operations stay
    anchored to the directories validated during construction.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not _NOFOLLOW or not getattr(os, "O_DIRECTORY", 0):
            raise VaultError("UNSUPPORTED_PLATFORM")

        root_text = _validated_root_text(root)
        self._token = secrets.token_hex(32)
        self._root_fd = -1
        self._staging_fd = -1
        self._final_fd = -1

        try:
            _ensure_directory_path(root_text)
            self._root_fd = _open_directory_path(root_text)
            _validate_directory_fd(self._root_fd)
            self._staging_fd = _open_or_create_child_directory(
                self._root_fd, "staging"
            )
            self._final_fd = _open_or_create_child_directory(self._root_fd, "final")
        except VaultError:
            self.close()
            raise
        except (OSError, TypeError, ValueError):
            self.close()
            raise VaultError("VAULT_INIT_FAILED") from None

    def __enter__(self) -> LocalFixtureVault:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        for attribute in ("_final_fd", "_staging_fd", "_root_fd"):
            descriptor = getattr(self, attribute, -1)
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                setattr(self, attribute, -1)

    def stage_bytes(self, data: bytes | bytearray | memoryview) -> StagedObject:
        try:
            view = memoryview(data)
        except (TypeError, ValueError):
            raise VaultError("INVALID_BYTES") from None
        return self.stage_chunks((view,))

    def stage_chunks(
        self,
        chunks: Iterable[bytes | bytearray | memoryview],
        *,
        stage_id: str | None = None,
    ) -> StagedObject:
        self._validate_operating_directories()
        name, descriptor, identity = self._create_staging_file(stage_id)
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                iterator = iter(chunks)
            except TypeError:
                raise VaultError("INVALID_CHUNKS") from None
            while True:
                try:
                    chunk = next(iterator)
                except StopIteration:
                    break
                except VaultError:
                    raise
                except Exception:
                    raise VaultError("SOURCE_READ_FAILED") from None

                try:
                    view = memoryview(chunk)
                    if not view.contiguous:
                        view = memoryview(bytes(view))
                    view = view.cast("B")
                except (TypeError, ValueError):
                    raise VaultError("INVALID_CHUNK") from None

                if not view:
                    continue
                _write_once(descriptor, view)
                digest.update(view)
                size += len(view)

            return self._complete_staging(name, descriptor, identity, digest, size)
        except VaultError:
            self._cleanup_staging_identity(name, identity)
            raise
        except (OSError, OverflowError):
            self._cleanup_staging_identity(name, identity)
            raise VaultError("STAGING_WRITE_FAILED") from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    async def stage_async_chunks(
        self,
        chunks: AsyncIterable[bytes | bytearray | memoryview],
        *,
        maximum_size: int,
        stage_id: str | None = None,
    ) -> StagedObject:
        if (
            isinstance(maximum_size, bool)
            or not isinstance(maximum_size, int)
            or maximum_size < 0
        ):
            raise VaultError("INVALID_SIZE_LIMIT")
        self._validate_operating_directories()
        name, descriptor, identity = self._create_staging_file(stage_id)
        digest = hashlib.sha256()
        size = 0
        try:
            try:
                iterator = chunks.__aiter__()
            except (AttributeError, TypeError):
                raise VaultError("INVALID_CHUNKS") from None
            while True:
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    break
                except VaultError:
                    raise
                except Exception:
                    raise VaultError("SOURCE_READ_FAILED") from None
                try:
                    view = memoryview(chunk)
                    if not view.contiguous:
                        view = memoryview(bytes(view))
                    view = view.cast("B")
                except (TypeError, ValueError):
                    raise VaultError("INVALID_CHUNK") from None
                if not view:
                    continue
                if size + len(view) > maximum_size:
                    raise VaultError("CONTENT_TOO_LARGE")
                _write_once(descriptor, view)
                digest.update(view)
                size += len(view)
            return self._complete_staging(name, descriptor, identity, digest, size)
        except VaultError:
            self._cleanup_staging_identity(name, identity)
            raise
        except (OSError, OverflowError):
            self._cleanup_staging_identity(name, identity)
            raise VaultError("STAGING_WRITE_FAILED") from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _complete_staging(
        self,
        name: str,
        descriptor: int,
        identity: tuple[int, int],
        digest: object,
        size: int,
    ) -> StagedObject:
        _sync_file(descriptor)
        current = _validate_file_fd(descriptor, expected_mode=0o600)
        if (current.st_dev, current.st_ino) != identity:
            raise VaultError("STAGING_IDENTITY_CHANGED")
        if current.st_size != size:
            raise VaultError("STAGING_SIZE_MISMATCH")
        _sync_directory(self._staging_fd)
        return StagedObject(
            stage_id=name,
            sha256=digest.hexdigest(),  # type: ignore[attr-defined]
            size=size,
            _vault_token=self._token,
            _device=identity[0],
            _inode=identity[1],
        )

    def stage_fd(self, source_fd: int, *, stage_id: str | None = None) -> StagedObject:
        before = _validate_read_only_source_fd(source_fd)

        def source_chunks() -> Iterator[bytes]:
            offset = 0
            while offset < before.st_size:
                wanted = min(_CHUNK_SIZE, before.st_size - offset)
                try:
                    chunk = os.pread(source_fd, wanted, offset)
                except OSError:
                    raise VaultError("SOURCE_READ_FAILED") from None
                if len(chunk) != wanted:
                    raise VaultError("SOURCE_SHORT_READ")
                yield chunk
                offset += len(chunk)

            after = _safe_fstat(source_fd, "SOURCE_STAT_FAILED")
            identity_before = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            identity_after = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if identity_after != identity_before:
                raise VaultError("SOURCE_CHANGED")

        return self.stage_chunks(source_chunks(), stage_id=stage_id)

    def recover_stage(
        self, stage_id: str, expected_sha256: str, expected_size: int
    ) -> StagedObject:
        self._validate_operating_directories()
        _validate_expected_metadata(expected_sha256, expected_size)
        if not _is_opaque_name(stage_id):
            raise VaultError("INVALID_STAGE_ID")

        descriptor = _open_named_file(self._staging_fd, stage_id)
        try:
            _lock_file(descriptor, exclusive=False)
            metadata = _validate_file_fd(descriptor, expected_mode=0o600)
            if metadata.st_size != expected_size:
                raise VaultError("STAGING_SIZE_MISMATCH")
            actual_hash, actual_size = _hash_fd(descriptor, expected_size)
            if actual_hash != expected_sha256 or actual_size != expected_size:
                raise VaultError("STAGING_HASH_MISMATCH")
            return StagedObject(
                stage_id=stage_id,
                sha256=expected_sha256,
                size=expected_size,
                _vault_token=self._token,
                _device=metadata.st_dev,
                _inode=metadata.st_ino,
            )
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def promote(self, staged: StagedObject) -> StoredObject:
        object_id = _opaque_name()
        if not _is_opaque_name(object_id):
            raise VaultError("UUID_GENERATION_FAILED")
        return self.promote_as(staged, object_id)

    def promote_as(self, staged: StagedObject, object_id: str) -> StoredObject:
        self._validate_staged_capability(staged)
        self._validate_operating_directories()
        if not _is_opaque_name(object_id):
            raise VaultError("INVALID_OBJECT_ID")

        try:
            existing = self._verify_final_if_present(
                object_id, staged.sha256, staged.size
            )
        except VaultError:
            raise VaultError("FINAL_CONFLICT") from None
        if existing is not None:
            self._cleanup_capability_stage(staged)
            return existing

        source_fd = _open_named_file(self._staging_fd, staged.stage_id)
        try:
            _lock_file(source_fd, exclusive=True)
            source_stat = _validate_file_fd(source_fd, expected_mode=0o600)
            if (source_stat.st_dev, source_stat.st_ino) != (
                staged._device,
                staged._inode,
            ):
                raise VaultError("STAGING_IDENTITY_MISMATCH")
            if source_stat.st_size != staged.size:
                raise VaultError("STAGING_SIZE_MISMATCH")

            source_hash, source_size = _hash_fd(source_fd, staged.size)
            if source_hash != staged.sha256 or source_size != staged.size:
                raise VaultError("STAGING_HASH_MISMATCH")

            try:
                os.link(
                    staged.stage_id,
                    object_id,
                    src_dir_fd=self._staging_fd,
                    dst_dir_fd=self._final_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                try:
                    existing = self._verify_final_if_present(
                        object_id, staged.sha256, staged.size
                    )
                except VaultError:
                    raise VaultError("FINAL_CONFLICT") from None
                if existing is None:
                    raise VaultError("FINAL_CREATE_RACE")
                self._cleanup_capability_stage(staged)
                return existing
            except OSError:
                raise VaultError("FINAL_CREATE_FAILED") from None

            _sync_directory(self._final_fd)
            self._cleanup_staging_identity(
                staged.stage_id,
                (staged._device, staged._inode),
                expected_nlink=2,
            )

            final_fd = _open_named_file(self._final_fd, object_id)
            try:
                final_stat = _validate_file_fd(final_fd, expected_mode=0o600)
                if final_stat.st_nlink != 1:
                    raise VaultError("FINAL_LINK_COUNT_INVALID")
                final_hash, final_size = _hash_fd(final_fd, staged.size)
                if final_hash != staged.sha256 or final_size != staged.size:
                    raise VaultError("FINAL_VERIFY_FAILED")
            finally:
                try:
                    os.close(final_fd)
                except OSError:
                    pass

            return StoredObject(
                object_id=object_id,
                sha256=staged.sha256,
                size=staged.size,
            )
        except VaultError:
            raise
        except (OSError, OverflowError):
            raise VaultError("PROMOTION_FAILED") from None
        finally:
            try:
                os.close(source_fd)
            except OSError:
                pass

    def verify(
        self, object_id: str, expected_sha256: str, expected_size: int
    ) -> StoredObject:
        self._validate_operating_directories()
        _validate_expected_metadata(expected_sha256, expected_size)
        if not _is_opaque_name(object_id):
            raise VaultError("INVALID_OBJECT_ID")
        result = self._verify_final_if_present(
            object_id, expected_sha256, expected_size
        )
        if result is None:
            raise VaultError("FINAL_NOT_FOUND")
        return result

    def final_count(self) -> int:
        self._validate_operating_directories()
        try:
            names = os.listdir(self._final_fd)
        except OSError:
            raise VaultError("FINAL_LIST_FAILED") from None
        count = 0
        for name in names:
            if not _is_opaque_name(name):
                continue
            descriptor = -1
            try:
                descriptor = os.open(
                    name,
                    _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
                    dir_fd=self._final_fd,
                )
                _validate_file_fd(descriptor, expected_mode=0o600)
            except (OSError, VaultError):
                continue
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            count += 1
        return count

    def discard(self, staged: StagedObject) -> bool:
        self._validate_staged_capability(staged)
        self._validate_operating_directories()
        return self._cleanup_staging_identity(
            staged.stage_id, (staged._device, staged._inode)
        )

    def discard_named_stage(self, stage_id: str) -> bool:
        self._validate_operating_directories()
        if not _is_opaque_name(stage_id):
            raise VaultError("INVALID_STAGE_ID")
        descriptor = _open_named_file(self._staging_fd, stage_id)
        try:
            _lock_file(descriptor, exclusive=True)
            metadata = _validate_file_fd(descriptor, expected_mode=0o600)
            identity = (metadata.st_dev, metadata.st_ino)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return self._cleanup_staging_identity(stage_id, identity)

    def store_bytes(self, data: bytes | bytearray | memoryview) -> StoredObject:
        staged = self.stage_bytes(data)
        return self._promote_or_discard(staged)

    def store_chunks(
        self, chunks: Iterable[bytes | bytearray | memoryview]
    ) -> StoredObject:
        staged = self.stage_chunks(chunks)
        return self._promote_or_discard(staged)

    def store_fd(self, source_fd: int) -> StoredObject:
        staged = self.stage_fd(source_fd)
        return self._promote_or_discard(staged)

    def _promote_or_discard(self, staged: StagedObject) -> StoredObject:
        try:
            return self.promote(staged)
        except VaultError:
            self._cleanup_staging_identity(
                staged.stage_id, (staged._device, staged._inode)
            )
            raise

    def _ensure_open(self) -> None:
        if min(self._root_fd, self._staging_fd, self._final_fd) < 0:
            raise VaultError("VAULT_CLOSED")

    def _validate_operating_directories(self) -> None:
        self._ensure_open()
        _validate_directory_fd(self._root_fd)
        _validate_directory_fd(self._staging_fd)
        _validate_directory_fd(self._final_fd)

    def _verify_final_if_present(
        self, object_id: str, expected_sha256: str, expected_size: int
    ) -> StoredObject | None:
        descriptor = -1
        try:
            descriptor = os.open(
                object_id,
                _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
                dir_fd=self._final_fd,
            )
        except FileNotFoundError:
            return None
        except OSError:
            raise VaultError("FINAL_OBJECT_INVALID") from None
        try:
            metadata = _safe_fstat(descriptor, "OBJECT_STAT_FAILED")
            if not stat.S_ISREG(metadata.st_mode):
                raise VaultError("OBJECT_TYPE_INVALID")
            if metadata.st_uid != os.geteuid():
                raise VaultError("OBJECT_OWNER_INVALID")
            if stat.S_IMODE(metadata.st_mode) != 0o600:
                raise VaultError("OBJECT_MODE_INVALID")
            if metadata.st_nlink == 2:
                if metadata.st_size != expected_size:
                    raise VaultError("FINAL_SIZE_MISMATCH")
                actual_hash, actual_size = _hash_fd(descriptor, expected_size)
                if actual_hash != expected_sha256 or actual_size != expected_size:
                    raise VaultError("FINAL_HASH_MISMATCH")
                self._recover_interrupted_promotion(descriptor, metadata)
                metadata = _validate_file_fd(descriptor, expected_mode=0o600)
            elif metadata.st_nlink != 1:
                raise VaultError("OBJECT_LINK_COUNT_INVALID")
            if metadata.st_size != expected_size:
                raise VaultError("FINAL_SIZE_MISMATCH")
            actual_hash, actual_size = _hash_fd(descriptor, expected_size)
            if actual_hash != expected_sha256 or actual_size != expected_size:
                raise VaultError("FINAL_HASH_MISMATCH")
            return StoredObject(
                object_id=object_id,
                sha256=expected_sha256,
                size=expected_size,
            )
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def _recover_interrupted_promotion(
        self, final_fd: int, final_metadata: os.stat_result
    ) -> None:
        identity = (final_metadata.st_dev, final_metadata.st_ino)
        try:
            names = os.listdir(self._staging_fd)
        except OSError:
            raise VaultError("OBJECT_LINK_COUNT_INVALID") from None

        matches: list[str] = []
        for name in names:
            if not _is_opaque_name(name):
                continue
            descriptor = -1
            try:
                descriptor = os.open(
                    name,
                    _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
                    dir_fd=self._staging_fd,
                )
                metadata = os.fstat(descriptor)
            except OSError:
                continue
            finally:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
            if (
                stat.S_ISREG(metadata.st_mode)
                and metadata.st_uid == os.geteuid()
                and stat.S_IMODE(metadata.st_mode) == 0o600
                and metadata.st_nlink == 2
                and (metadata.st_dev, metadata.st_ino) == identity
            ):
                matches.append(name)

        if len(matches) != 1:
            raise VaultError("OBJECT_LINK_COUNT_INVALID")
        _sync_directory(self._final_fd)
        self._cleanup_staging_identity(matches[0], identity, expected_nlink=2)
        current = _safe_fstat(final_fd, "OBJECT_STAT_FAILED")
        if (
            (current.st_dev, current.st_ino) != identity
            or current.st_nlink != 1
        ):
            raise VaultError("OBJECT_LINK_COUNT_INVALID")

    def _cleanup_capability_stage(self, staged: StagedObject) -> None:
        descriptor = -1
        try:
            descriptor = os.open(
                staged.stage_id,
                _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
                dir_fd=self._staging_fd,
            )
        except FileNotFoundError:
            return
        except OSError:
            raise VaultError("STAGING_CLEANUP_FAILED") from None
        try:
            metadata = _validate_file_fd(descriptor, expected_mode=0o600)
            if (metadata.st_dev, metadata.st_ino) != (
                staged._device,
                staged._inode,
            ):
                raise VaultError("STAGING_IDENTITY_MISMATCH")
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if not self._cleanup_staging_identity(
            staged.stage_id, (staged._device, staged._inode)
        ):
            raise VaultError("STAGING_CLEANUP_FAILED")

    def _create_staging_file(
        self, requested_name: str | None = None
    ) -> tuple[str, int, tuple[int, int]]:
        if requested_name is not None and not _is_opaque_name(requested_name):
            raise VaultError("INVALID_STAGE_ID")
        attempts = 1 if requested_name is not None else 8
        for _ in range(attempts):
            name = requested_name if requested_name is not None else _opaque_name()
            if not _is_opaque_name(name):
                raise VaultError("UUID_GENERATION_FAILED")
            try:
                descriptor = os.open(
                    name,
                    _FILE_WRITE_FLAGS | _NOFOLLOW | _CLOEXEC,
                    0o600,
                    dir_fd=self._staging_fd,
                )
            except FileExistsError:
                if requested_name is not None:
                    raise VaultError("STAGING_ALREADY_EXISTS") from None
                continue
            except OSError:
                raise VaultError("STAGING_CREATE_FAILED") from None
            try:
                os.fchmod(descriptor, 0o600)
                _lock_file(descriptor, exclusive=True)
                metadata = _validate_file_fd(descriptor, expected_mode=0o600)
                return name, descriptor, (metadata.st_dev, metadata.st_ino)
            except VaultError:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            except OSError:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise VaultError("STAGING_CREATE_FAILED") from None
        raise VaultError("UUID_COLLISION")

    def _cleanup_staging_identity(
        self,
        name: str,
        identity: tuple[int, int],
        *,
        expected_nlink: int = 1,
    ) -> bool:
        try:
            descriptor = os.open(
                name,
                _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
                dir_fd=self._staging_fd,
            )
        except FileNotFoundError:
            return False
        except OSError:
            return False
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                return False
            if (
                metadata.st_uid != os.geteuid()
                or metadata.st_nlink != expected_nlink
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                return False
            if (metadata.st_dev, metadata.st_ino) != identity:
                return False
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(name, dir_fd=self._staging_fd)
            _sync_directory(self._staging_fd)
            return True
        except OSError:
            return False

    def _validate_staged_capability(self, staged: StagedObject) -> None:
        if not isinstance(staged, StagedObject):
            raise VaultError("INVALID_STAGED_OBJECT")
        if not secrets.compare_digest(staged._vault_token, self._token):
            raise VaultError("FOREIGN_STAGED_OBJECT")
        if not _is_opaque_name(staged.stage_id):
            raise VaultError("INVALID_STAGE_ID")
        if (
            not _is_sha256(staged.sha256)
            or isinstance(staged.size, bool)
            or not isinstance(staged.size, int)
            or staged.size < 0
        ):
            raise VaultError("INVALID_STAGED_METADATA")

    def __del__(self) -> None:
        self.close()


def _validated_root_text(root: str | os.PathLike[str]) -> str:
    try:
        text = os.fspath(root)
    except TypeError:
        raise VaultError("INVALID_ROOT") from None
    if not isinstance(text, str) or not text or not os.path.isabs(text):
        raise VaultError("INVALID_ROOT")
    normalized = os.path.normpath(text)
    if text.rstrip(os.sep) != normalized:
        raise VaultError("INVALID_ROOT")
    try:
        resolved = str(Path(normalized).resolve(strict=False))
        common = os.path.commonpath((_PRIVATE_TMP, resolved))
    except (OSError, RuntimeError, ValueError):
        raise VaultError("INVALID_ROOT") from None
    if common != _PRIVATE_TMP or resolved == _PRIVATE_TMP or resolved != normalized:
        raise VaultError("ROOT_OUTSIDE_PRIVATE_TMP")
    return resolved


def _ensure_directory_path(path: str) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    except FileNotFoundError:
        raise VaultError("ROOT_PARENT_MISSING") from None
    except OSError:
        raise VaultError("ROOT_CREATE_FAILED") from None


def _open_directory_path(path: str) -> int:
    try:
        return os.open(path, _DIRECTORY_FLAGS | _NOFOLLOW | _CLOEXEC)
    except OSError:
        raise VaultError("ROOT_OPEN_FAILED") from None


def _open_or_create_child_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError:
        raise VaultError("VAULT_DIRECTORY_CREATE_FAILED") from None
    try:
        descriptor = os.open(
            name,
            _DIRECTORY_FLAGS | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError:
        raise VaultError("VAULT_DIRECTORY_OPEN_FAILED") from None
    try:
        _validate_directory_fd(descriptor)
    except VaultError:
        os.close(descriptor)
        raise
    return descriptor


def _validate_directory_fd(descriptor: int) -> os.stat_result:
    metadata = _safe_fstat(descriptor, "VAULT_DIRECTORY_STAT_FAILED")
    if not stat.S_ISDIR(metadata.st_mode):
        raise VaultError("VAULT_DIRECTORY_INVALID")
    if metadata.st_uid != os.geteuid():
        raise VaultError("VAULT_DIRECTORY_OWNER_INVALID")
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise VaultError("VAULT_DIRECTORY_MODE_INVALID")
    return metadata


def _validate_file_fd(descriptor: int, *, expected_mode: int) -> os.stat_result:
    metadata = _safe_fstat(descriptor, "OBJECT_STAT_FAILED")
    if not stat.S_ISREG(metadata.st_mode):
        raise VaultError("OBJECT_TYPE_INVALID")
    if metadata.st_uid != os.geteuid():
        raise VaultError("OBJECT_OWNER_INVALID")
    if metadata.st_nlink != 1:
        raise VaultError("OBJECT_LINK_COUNT_INVALID")
    if stat.S_IMODE(metadata.st_mode) != expected_mode:
        raise VaultError("OBJECT_MODE_INVALID")
    return metadata


def _validate_read_only_source_fd(descriptor: int) -> os.stat_result:
    if isinstance(descriptor, bool) or not isinstance(descriptor, int) or descriptor < 0:
        raise VaultError("INVALID_SOURCE_FD")
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    except OSError:
        raise VaultError("INVALID_SOURCE_FD") from None
    if flags & os.O_ACCMODE != os.O_RDONLY:
        raise VaultError("SOURCE_FD_NOT_READ_ONLY")
    metadata = _safe_fstat(descriptor, "SOURCE_STAT_FAILED")
    if not stat.S_ISREG(metadata.st_mode):
        raise VaultError("SOURCE_NOT_REGULAR")
    if metadata.st_nlink != 1:
        raise VaultError("SOURCE_LINK_COUNT_INVALID")
    if metadata.st_size < 0:
        raise VaultError("SOURCE_SIZE_INVALID")
    return metadata


def _safe_fstat(descriptor: int, code: str) -> os.stat_result:
    try:
        return os.fstat(descriptor)
    except OSError:
        raise VaultError(code) from None


def _lock_file(descriptor: int, *, exclusive: bool) -> None:
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
    except OSError:
        raise VaultError("STAGING_BUSY") from None


def _open_named_file(directory_fd: int, name: str) -> int:
    if not _is_opaque_name(name):
        raise VaultError("INVALID_OBJECT_ID")
    try:
        return os.open(
            name,
            _FILE_READ_FLAGS | _NOFOLLOW | _CLOEXEC,
            dir_fd=directory_fd,
        )
    except FileNotFoundError:
        raise VaultError("OBJECT_NOT_FOUND") from None
    except OSError:
        raise VaultError("OBJECT_OPEN_FAILED") from None


def _write_once(descriptor: int, data: memoryview) -> None:
    try:
        written = os.write(descriptor, data)
    except OSError:
        raise VaultError("WRITE_FAILED") from None
    if written != len(data):
        raise VaultError("SHORT_WRITE")


def _hash_fd(descriptor: int, expected_size: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    offset = 0
    while offset < expected_size:
        wanted = min(_CHUNK_SIZE, expected_size - offset)
        try:
            chunk = os.pread(descriptor, wanted, offset)
        except OSError:
            raise VaultError("OBJECT_READ_FAILED") from None
        if len(chunk) != wanted:
            raise VaultError("OBJECT_SHORT_READ")
        digest.update(chunk)
        offset += len(chunk)
    try:
        extra = os.pread(descriptor, 1, expected_size)
    except OSError:
        raise VaultError("OBJECT_READ_FAILED") from None
    if extra:
        raise VaultError("OBJECT_SIZE_MISMATCH")
    return digest.hexdigest(), offset


def _sync_file(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        raise VaultError("FILE_FSYNC_FAILED") from None


def _sync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno == errno.EINVAL:
            raise VaultError("DIRECTORY_FSYNC_UNSUPPORTED") from None
        raise VaultError("DIRECTORY_FSYNC_FAILED") from None


def _opaque_name() -> str:
    return uuid.uuid4().hex


def _is_opaque_name(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 32:
        return False
    try:
        parsed = uuid.UUID(hex=value)
        return (
            parsed.hex == value
            and parsed.version == 4
            and parsed.variant == uuid.RFC_4122
        )
    except (ValueError, AttributeError):
        return False


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def _validate_expected_metadata(expected_sha256: object, expected_size: object) -> None:
    if (
        not _is_sha256(expected_sha256)
        or isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise VaultError("INVALID_EXPECTED_METADATA")


__all__ = [
    "LocalFixtureVault",
    "StagedObject",
    "StoredObject",
    "VaultError",
]
