"""Fail-closed runtime contract for isolated execution of the frozen F0 suites.

The legacy F0 defaults remain authoritative unless the one explicit
``F111_F0_ISOLATION_CONFIG`` environment variable is present.  The JSON file
contains identities and private paths only; PostgreSQL credentials stay in
four owner-only files and are read only at the connection boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping
from urllib.parse import quote
import uuid

from psycopg.conninfo import conninfo_to_dict

from .database import DatabaseConfig


SCHEMA = "f1.1.1-frozen-f0-isolation-v1"
ENVIRONMENT_VARIABLE = "F111_F0_ISOLATION_CONFIG"
REASON_CODE = "FROZEN_F0_ISOLATION_CONFIG_REJECTED"

_MAX_CONFIG_BYTES = 64 * 1024
_MAX_DSN_BYTES = 4096
_ROLES = (
    "f0d_bootstrap",
    "f0d_migration",
    "f0d_runtime",
    "f0d_worker",
)
_DATABASE_PURPOSES = (
    "f0d-test",
    "f0d-upgrade",
    "f0e-test",
    "f0f-test",
    "f0g-base",
    "f0g-case",
    "f0i-migration",
    "f0i-persistence",
)
_DOCKER_PHASES = ("f0e", "f0f", "f0h")
_CONFIG_KEYS = frozenset(
    {
        "schema",
        "project_id",
        "runtime_root",
        "tmp_dir",
        "fixture_source_root",
        "postgres",
        "paths",
        "projects",
    }
)
_POSTGRES_KEYS = frozenset(
    {
        "host",
        "port",
        "dsn_files",
        "f0g_template_database",
        "f0i_template_database",
    }
)
_PATH_KEYS = frozenset(
    {
        "f0e_runtime_root",
        "f0f_runtime_root",
        "f0f_key_file",
        "f0f_vault_root",
        "f0h_runtime_root",
    }
)
_PROJECT_KEYS = frozenset({"docker", "f0j0", "f0j1"})
_DSN_KEYS = frozenset({"user", "password", "host", "port", "dbname"})
_RUNTIME_SUFFIX = re.compile(r"[a-z0-9_]{8,32}")
_RUNTIME_ROOT_NAME = re.compile(
    r"anhuan-f111-repair-f0-[0-9a-f]{32}-[a-z0-9_]{8,32}"
)


class FrozenF0IsolationError(RuntimeError):
    """A single redacted failure for every malformed isolation input."""

    code = REASON_CODE

    def __init__(self, _detail: object = None) -> None:
        super().__init__(REASON_CODE)

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": REASON_CODE}


@dataclass(frozen=True, slots=True)
class FrozenF0Isolation:
    project_id: uuid.UUID
    runtime_root: Path
    tmp_dir: Path
    fixture_source_root: Path
    postgres_host: str
    postgres_port: int
    bootstrap_dsn_file: Path
    migration_dsn_file: Path
    runtime_dsn_file: Path
    worker_dsn_file: Path
    f0g_template_database: str
    f0i_template_database: str
    f0e_runtime_root: Path
    f0f_runtime_root: Path
    f0f_key_file: Path
    f0f_vault_root: Path
    f0h_runtime_root: Path
    docker_project_name: str
    f0j0_project_name: str
    f0j1_project_name: str

    @property
    def managed_database_names(self) -> tuple[str, ...]:
        return (
            self.f0g_template_database,
            self.f0i_template_database,
            *(self.database_name(purpose) for purpose in _DATABASE_PURPOSES),
        )

    @property
    def managed_project_names(self) -> tuple[str, ...]:
        return (
            self.docker_project_name,
            *(self.docker_project_for(phase) for phase in _DOCKER_PHASES),
            self.f0j0_project_name,
            self.f0j1_project_name,
        )

    @property
    def f0j0_retrieval_container_name(self) -> str:
        return self.f0j0_project_name + "-opensearch"

    @property
    def managed_container_names(self) -> tuple[str, ...]:
        """Exact non-Compose containers used by the frozen discover suite."""

        return (self.f0j0_retrieval_container_name,)

    @property
    def managed_paths(self) -> tuple[Path, ...]:
        return (
            self.runtime_root,
            self.tmp_dir,
            self.fixture_source_root,
            self.bootstrap_dsn_file,
            self.migration_dsn_file,
            self.runtime_dsn_file,
            self.worker_dsn_file,
            self.f0e_runtime_root,
            self.f0f_runtime_root,
            self.f0f_key_file,
            self.f0f_vault_root,
            self.f0h_runtime_root,
        )

    def database_name(self, purpose: str) -> str:
        if purpose not in _DATABASE_PURPOSES:
            raise FrozenF0IsolationError()
        token = self.project_id.hex
        names = {
            "f0d-test": f"f111_f0d_{token}",
            "f0d-upgrade": f"f111_f0d_upgrade_{token}",
            "f0e-test": f"f111_f0e_{token}",
            "f0f-test": f"f111_f0f_{token}",
            "f0g-base": f"f111_f0g_base_{token}",
            "f0g-case": f"f111_f0g_case_{token}",
            "f0i-migration": f"f111_f0i_migration_{token}",
            "f0i-persistence": f"f111_f0i_persist_{token}",
        }
        return names[purpose]

    def docker_project_for(self, phase: str) -> str:
        if phase not in _DOCKER_PHASES:
            raise FrozenF0IsolationError()
        return f"{self.docker_project_name}-{phase}"

    def dsn_for(self, role: str, database: str | None = None) -> str:
        if role not in _ROLES:
            raise FrozenF0IsolationError()
        expected_database = "postgres" if role == "f0d_bootstrap" else self.f0i_template_database
        values = _read_and_parse_dsn(self, role, expected_database)
        target = expected_database if database is None else database
        if not self.database_allowed(target, bootstrap=role == "f0d_bootstrap"):
            raise FrozenF0IsolationError()
        return _format_dsn(values, target)

    def database_allowed(self, database: object, *, bootstrap: bool = False) -> bool:
        if not isinstance(database, str):
            return False
        if bootstrap and database == "postgres":
            return True
        return database in self.managed_database_names

    def database_config(self, database: str) -> DatabaseConfig:
        if not self.database_allowed(database):
            raise FrozenF0IsolationError()
        return DatabaseConfig(
            migration_dsn=self.dsn_for("f0d_migration", database),
            runtime_dsn=self.dsn_for("f0d_runtime", database),
            worker_dsn=self.dsn_for("f0d_worker", database),
        )

    def validate_database_config(self, config: DatabaseConfig) -> DatabaseConfig:
        if not isinstance(config, DatabaseConfig):
            raise FrozenF0IsolationError()
        names: set[str] = set()
        for field_name, role in (
            ("migration_dsn", "f0d_migration"),
            ("runtime_dsn", "f0d_runtime"),
            ("worker_dsn", "f0d_worker"),
        ):
            values = _parse_dsn(getattr(config, field_name, None))
            baseline = _read_and_parse_dsn(self, role, self.f0i_template_database)
            if (
                values["user"] != role
                or values["password"] != baseline["password"]
                or values["host"] != self.postgres_host
                or values["port"] != str(self.postgres_port)
                or not self.database_allowed(values["dbname"])
            ):
                raise FrozenF0IsolationError()
            names.add(values["dbname"])
        if len(names) != 1:
            raise FrozenF0IsolationError()
        return config

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "project_id": str(self.project_id),
            "runtime_root": str(self.runtime_root),
            "tmp_dir": str(self.tmp_dir),
            "fixture_source_root": str(self.fixture_source_root),
            "postgres": {
                "host": self.postgres_host,
                "port": self.postgres_port,
                "dsn_files": {
                    "f0d_bootstrap": str(self.bootstrap_dsn_file),
                    "f0d_migration": str(self.migration_dsn_file),
                    "f0d_runtime": str(self.runtime_dsn_file),
                    "f0d_worker": str(self.worker_dsn_file),
                },
                "f0g_template_database": self.f0g_template_database,
                "f0i_template_database": self.f0i_template_database,
            },
            "paths": {
                "f0e_runtime_root": str(self.f0e_runtime_root),
                "f0f_runtime_root": str(self.f0f_runtime_root),
                "f0f_key_file": str(self.f0f_key_file),
                "f0f_vault_root": str(self.f0f_vault_root),
                "f0h_runtime_root": str(self.f0h_runtime_root),
            },
            "projects": {
                "docker": self.docker_project_name,
                "f0j0": self.f0j0_project_name,
                "f0j1": self.f0j1_project_name,
            },
        }


def build_frozen_f0_isolation(
    runtime_root: str | Path,
    project_id: uuid.UUID,
    postgres_port: int,
) -> FrozenF0Isolation:
    """Purely derive the one permitted layout; do not touch the filesystem."""

    try:
        if (
            not isinstance(project_id, uuid.UUID)
            or project_id.version != 4
            or project_id.variant != uuid.RFC_4122
        ):
            raise FrozenF0IsolationError()
        root = _lexical_absolute_path(runtime_root)
        if (
            isinstance(postgres_port, bool)
            or not isinstance(postgres_port, int)
            or not 1024 <= postgres_port <= 65535
            or postgres_port == 55432
        ):
            raise FrozenF0IsolationError()
        token = project_id.hex
        expected_prefix = f"anhuan-f111-repair-f0-{token}-"
        if (
            root.parent != Path("/private/tmp")
            or not root.name.startswith(expected_prefix)
            or _RUNTIME_SUFFIX.fullmatch(root.name[len(expected_prefix) :]) is None
        ):
            raise FrozenF0IsolationError()
        secrets = root / "secrets"
        return FrozenF0Isolation(
            project_id=project_id,
            runtime_root=root,
            tmp_dir=root / "tmp",
            fixture_source_root=root / "fixture-source",
            postgres_host="127.0.0.1",
            postgres_port=postgres_port,
            bootstrap_dsn_file=secrets / "f0d-bootstrap.dsn",
            migration_dsn_file=secrets / "f0d-migration.dsn",
            runtime_dsn_file=secrets / "f0d-runtime.dsn",
            worker_dsn_file=secrets / "f0d-worker.dsn",
            f0g_template_database=f"f111_f0g_template_{token}",
            f0i_template_database=f"f111_f0i_template_{token}",
            f0e_runtime_root=root / "f0e-runtime",
            f0f_runtime_root=root / "f0f-runtime",
            f0f_key_file=secrets / "f0f.key",
            f0f_vault_root=root / "f0f-vault",
            f0h_runtime_root=root / "f0h-runtime",
            docker_project_name=f"anhuan-f111-repair-f0-{token}",
            f0j0_project_name=f"anhuan-f111-repair-j0-{token}",
            f0j1_project_name=f"anhuan-f111-repair-j1-{token}",
        )
    except FrozenF0IsolationError:
        raise
    except (OSError, TypeError, ValueError):
        raise FrozenF0IsolationError() from None


def validate_frozen_f0_isolation(isolation: FrozenF0Isolation) -> FrozenF0Isolation:
    try:
        if not isinstance(isolation, FrozenF0Isolation):
            raise FrozenF0IsolationError()
        expected = build_frozen_f0_isolation(
            isolation.runtime_root,
            isolation.project_id,
            isolation.postgres_port,
        )
        if isolation != expected or isolation.postgres_host != "127.0.0.1":
            raise FrozenF0IsolationError()
        _validate_directory(isolation.runtime_root, isolation.runtime_root)
        for path in (
            isolation.tmp_dir,
            isolation.fixture_source_root,
            isolation.f0e_runtime_root,
            isolation.f0f_runtime_root,
            isolation.f0f_vault_root,
            isolation.f0h_runtime_root,
            isolation.bootstrap_dsn_file.parent,
        ):
            _validate_directory(path, isolation.runtime_root)
        for role in _ROLES:
            expected_database = "postgres" if role == "f0d_bootstrap" else isolation.f0i_template_database
            _read_and_parse_dsn(isolation, role, expected_database)
        _validate_private_file(
            isolation.f0f_key_file,
            isolation.runtime_root,
            maximum_bytes=64,
            exact_bytes=32,
        )
        if len(set(isolation.managed_database_names)) != len(isolation.managed_database_names):
            raise FrozenF0IsolationError()
        if len(set(isolation.managed_project_names)) != len(isolation.managed_project_names):
            raise FrozenF0IsolationError()
        if (
            len(set(isolation.managed_container_names))
            != len(isolation.managed_container_names)
            or set(isolation.managed_container_names)
            & set(isolation.managed_project_names)
        ):
            raise FrozenF0IsolationError()
        return isolation
    except FrozenF0IsolationError:
        raise
    except (OSError, TypeError, ValueError):
        raise FrozenF0IsolationError() from None


def load_frozen_f0_isolation(
    environ: Mapping[str, str] | None = None,
) -> FrozenF0Isolation | None:
    source = os.environ if environ is None else environ
    try:
        raw = source.get(ENVIRONMENT_VARIABLE)
        if raw is None:
            return None
        config_path = _lexical_absolute_path(raw)
        expected_root = _runtime_root_for_config(config_path)
        payload_bytes = _read_private_file(config_path, maximum_bytes=_MAX_CONFIG_BYTES)
        payload = json.loads(payload_bytes.decode("ascii", errors="strict"))
        if not isinstance(payload, dict) or frozenset(payload) != _CONFIG_KEYS:
            raise FrozenF0IsolationError()
        canonical = _canonical_json(payload)
        if payload_bytes != canonical:
            raise FrozenF0IsolationError()
        isolation = _from_payload(payload)
        if (
            isolation.runtime_root != expected_root
            or not _strict_descendant(config_path, isolation.runtime_root)
        ):
            raise FrozenF0IsolationError()
        return validate_frozen_f0_isolation(isolation)
    except FrozenF0IsolationError:
        raise
    except Exception:
        raise FrozenF0IsolationError() from None


def write_frozen_f0_isolation(
    path: str | Path,
    isolation: FrozenF0Isolation,
) -> Path:
    """Write only the canonical config after the orchestrator created assets."""

    descriptor = -1
    parent_descriptor = -1
    target: Path | None = None
    staging: Path | None = None
    target_created = False
    try:
        validate_frozen_f0_isolation(isolation)
        target = _lexical_absolute_path(path)
        if not _strict_descendant(target, isolation.runtime_root):
            raise FrozenF0IsolationError()
        _validate_directory(target.parent, isolation.runtime_root)
        if os.path.lexists(target) or any(target.parent.glob(".f0-isolation-*.tmp")):
            raise FrozenF0IsolationError()
        payload = _canonical_json(isolation.to_payload())
        staging = target.parent / f".f0-isolation-{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            staging,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _read_private_file(staging, maximum_bytes=_MAX_CONFIG_BYTES)
        parent_descriptor = os.open(
            target.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(parent_descriptor)
        if os.path.lexists(target):
            raise FrozenF0IsolationError()
        os.link(staging, target, follow_symlinks=False)
        target_created = True
        os.unlink(staging)
        staging = None
        os.fsync(parent_descriptor)
        _read_private_file(target, maximum_bytes=_MAX_CONFIG_BYTES)
        return target
    except FrozenF0IsolationError:
        _cleanup_publish_failure(staging, target if target_created else None)
        if parent_descriptor >= 0:
            try:
                os.fsync(parent_descriptor)
            except OSError:
                pass
        raise
    except Exception:
        _cleanup_publish_failure(staging, target if target_created else None)
        if parent_descriptor >= 0:
            try:
                os.fsync(parent_descriptor)
            except OSError:
                pass
        raise FrozenF0IsolationError() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise FrozenF0IsolationError()
        offset += written


def _cleanup_publish_failure(staging: Path | None, target: Path | None) -> None:
    for candidate in (staging, target):
        if candidate is None:
            continue
        try:
            os.unlink(candidate)
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _from_payload(payload: dict[str, object]) -> FrozenF0Isolation:
    if payload.get("schema") != SCHEMA:
        raise FrozenF0IsolationError()
    postgres = payload.get("postgres")
    paths = payload.get("paths")
    projects = payload.get("projects")
    if (
        not isinstance(postgres, dict)
        or frozenset(postgres) != _POSTGRES_KEYS
        or not isinstance(paths, dict)
        or frozenset(paths) != _PATH_KEYS
        or not isinstance(projects, dict)
        or frozenset(projects) != _PROJECT_KEYS
    ):
        raise FrozenF0IsolationError()
    dsn_files = postgres.get("dsn_files")
    if not isinstance(dsn_files, dict) or frozenset(dsn_files) != frozenset(_ROLES):
        raise FrozenF0IsolationError()
    try:
        raw_project_id = payload["project_id"]
        if not isinstance(raw_project_id, str):
            raise FrozenF0IsolationError()
        project_id = uuid.UUID(raw_project_id)
        if str(project_id) != raw_project_id:
            raise FrozenF0IsolationError()
    except (KeyError, ValueError):
        raise FrozenF0IsolationError() from None
    isolation = FrozenF0Isolation(
        project_id=project_id,
        runtime_root=_lexical_absolute_path(payload.get("runtime_root")),
        tmp_dir=_lexical_absolute_path(payload.get("tmp_dir")),
        fixture_source_root=_lexical_absolute_path(payload.get("fixture_source_root")),
        postgres_host=str(postgres.get("host")),
        postgres_port=postgres.get("port"),  # type: ignore[arg-type]
        bootstrap_dsn_file=_lexical_absolute_path(dsn_files.get("f0d_bootstrap")),
        migration_dsn_file=_lexical_absolute_path(dsn_files.get("f0d_migration")),
        runtime_dsn_file=_lexical_absolute_path(dsn_files.get("f0d_runtime")),
        worker_dsn_file=_lexical_absolute_path(dsn_files.get("f0d_worker")),
        f0g_template_database=str(postgres.get("f0g_template_database")),
        f0i_template_database=str(postgres.get("f0i_template_database")),
        f0e_runtime_root=_lexical_absolute_path(paths.get("f0e_runtime_root")),
        f0f_runtime_root=_lexical_absolute_path(paths.get("f0f_runtime_root")),
        f0f_key_file=_lexical_absolute_path(paths.get("f0f_key_file")),
        f0f_vault_root=_lexical_absolute_path(paths.get("f0f_vault_root")),
        f0h_runtime_root=_lexical_absolute_path(paths.get("f0h_runtime_root")),
        docker_project_name=str(projects.get("docker")),
        f0j0_project_name=str(projects.get("f0j0")),
        f0j1_project_name=str(projects.get("f0j1")),
    )
    return isolation


def _role_file(isolation: FrozenF0Isolation, role: str) -> Path:
    fields = {
        "f0d_bootstrap": isolation.bootstrap_dsn_file,
        "f0d_migration": isolation.migration_dsn_file,
        "f0d_runtime": isolation.runtime_dsn_file,
        "f0d_worker": isolation.worker_dsn_file,
    }
    try:
        return fields[role]
    except KeyError:
        raise FrozenF0IsolationError() from None


def _read_and_parse_dsn(
    isolation: FrozenF0Isolation,
    role: str,
    expected_database: str,
) -> dict[str, str]:
    payload = _read_private_file(
        _role_file(isolation, role),
        root=isolation.runtime_root,
        maximum_bytes=_MAX_DSN_BYTES,
    )
    try:
        dsn = payload.decode("ascii", errors="strict")
        values = _parse_dsn(dsn)
        if (
            values["user"] != role
            or values["host"] != isolation.postgres_host
            or values["port"] != str(isolation.postgres_port)
            or values["dbname"] != expected_database
            or dsn != _format_dsn(values, expected_database)
        ):
            raise FrozenF0IsolationError()
        return values
    finally:
        material = bytearray(payload)
        material[:] = b"\0" * len(material)


def _parse_dsn(dsn: object) -> dict[str, str]:
    if (
        not isinstance(dsn, str)
        or not dsn.startswith("postgresql://")
        or "?" in dsn
        or "#" in dsn
        or any(character.isspace() for character in dsn)
    ):
        raise FrozenF0IsolationError()
    try:
        parsed = conninfo_to_dict(dsn)
    except Exception:
        raise FrozenF0IsolationError() from None
    if frozenset(parsed) != _DSN_KEYS:
        raise FrozenF0IsolationError()
    values = {key: parsed.get(key) for key in _DSN_KEYS}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise FrozenF0IsolationError()
    return {key: str(value) for key, value in values.items()}


def _format_dsn(values: Mapping[str, str], database: str) -> str:
    return (
        "postgresql://"
        + quote(values["user"], safe="")
        + ":"
        + quote(values["password"], safe="")
        + "@"
        + values["host"]
        + ":"
        + values["port"]
        + "/"
        + quote(database, safe="")
    )


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _lexical_absolute_path(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise FrozenF0IsolationError()
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or "\0" in raw or not os.path.isabs(raw):
        raise FrozenF0IsolationError()
    if os.path.normpath(raw) != raw:
        raise FrozenF0IsolationError()
    return Path(raw)


def _runtime_root_for_config(config_path: Path) -> Path:
    try:
        relative = config_path.relative_to(Path("/private/tmp"))
    except ValueError:
        raise FrozenF0IsolationError() from None
    if len(relative.parts) < 2 or _RUNTIME_ROOT_NAME.fullmatch(relative.parts[0]) is None:
        raise FrozenF0IsolationError()
    return Path("/private/tmp") / relative.parts[0]


def _strict_descendant(path: Path, root: Path) -> bool:
    try:
        return path != root and path.is_relative_to(root)
    except (OSError, TypeError, ValueError):
        return False


def _validate_directory(path: Path, root: Path) -> None:
    if path != root and not _strict_descendant(path, root):
        raise FrozenF0IsolationError()
    try:
        listed = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise FrozenF0IsolationError() from None
    if (
        resolved != path
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_uid != os.getuid()
        or stat.S_IMODE(listed.st_mode) != 0o700
    ):
        raise FrozenF0IsolationError()


def _validate_private_file(
    path: Path,
    root: Path,
    *,
    maximum_bytes: int,
    exact_bytes: int | None = None,
) -> None:
    _read_private_file(
        path,
        root=root,
        maximum_bytes=maximum_bytes,
        exact_bytes=exact_bytes,
    )


def _read_private_file(
    path: Path,
    *,
    root: Path | None = None,
    maximum_bytes: int,
    exact_bytes: int | None = None,
) -> bytes:
    descriptor = -1
    try:
        if root is not None and not _strict_descendant(path, root):
            raise FrozenF0IsolationError()
        listed = path.lstat()
        if path.resolve(strict=True) != path or stat.S_ISLNK(listed.st_mode):
            raise FrozenF0IsolationError()
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not 0 < before.st_size <= maximum_bytes
            or (exact_bytes is not None and before.st_size != exact_bytes)
            or (listed.st_dev, listed.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise FrozenF0IsolationError()
        output = bytearray()
        while len(output) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(output))
            if not chunk:
                break
            output.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(output) != before.st_size
            or os.read(descriptor, 1)
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise FrozenF0IsolationError()
        return bytes(output)
    except FrozenF0IsolationError:
        raise
    except OSError:
        raise FrozenF0IsolationError() from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


__all__ = (
    "ENVIRONMENT_VARIABLE",
    "REASON_CODE",
    "SCHEMA",
    "FrozenF0Isolation",
    "FrozenF0IsolationError",
    "build_frozen_f0_isolation",
    "load_frozen_f0_isolation",
    "validate_frozen_f0_isolation",
    "write_frozen_f0_isolation",
)
