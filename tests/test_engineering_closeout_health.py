from __future__ import annotations

import contextlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


DATABASE = "anhuan_closeout_123456abcdef123456abcdef"
ROOT = Path(__file__).resolve().parents[1]
HEALTH = ROOT / "src/platform_foundation/f1/health.py"


def _module(name: str, **values: object) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


def _load_health() -> types.ModuleType:
    redis = _module("redis", Redis=object)
    sqlalchemy = _module("sqlalchemy", text=lambda statement: statement)
    config = _module(
        "platform_foundation.f1.config",
        keycloak_url=lambda: "http://keycloak.invalid",
        pg_database=lambda: DATABASE,
        redis_url=lambda: "redis://redis.invalid/0",
    )
    database = _module(
        "platform_foundation.f1.database",
        session_scope=lambda **_kwargs: None,
    )
    scanner = _module(
        "platform_foundation.f1.features.p3.scanner",
        scanner_version=lambda **_kwargs: None,
    )
    storage = _module(
        "platform_foundation.f1.storage",
        _client=lambda: None,
    )
    packages = {
        "platform_foundation": _module("platform_foundation"),
        "platform_foundation.f1": _module("platform_foundation.f1"),
        "platform_foundation.f1.features": _module(
            "platform_foundation.f1.features"
        ),
        "platform_foundation.f1.features.p3": _module(
            "platform_foundation.f1.features.p3"
        ),
    }
    for package in packages.values():
        package.__path__ = []  # type: ignore[attr-defined]
    module_name = "platform_foundation.f1.health_under_test"
    spec = importlib.util.spec_from_file_location(module_name, HEALTH)
    if spec is None or spec.loader is None:
        raise AssertionError("health module spec unavailable")
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(
        sys.modules,
        {
            **packages,
            "redis": redis,
            "sqlalchemy": sqlalchemy,
            "platform_foundation.f1.config": config,
            "platform_foundation.f1.database": database,
            "platform_foundation.f1.features.p3.scanner": scanner,
            "platform_foundation.f1.storage": storage,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


health = _load_health()


class _Result:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def one(self) -> tuple[object, ...]:
        return self._row


class _Session:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row
        self.statements: list[str] = []

    async def execute(self, statement: object) -> _Result:
        self.statements.append(str(statement))
        return _Result(self._row)


class EngineeringCloseoutHealthTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, row: tuple[object, ...]) -> tuple[bool, _Session]:
        session = _Session(row)

        @contextlib.asynccontextmanager
        async def session_scope(*, role: str):
            self.assertEqual(role, "f1_api")
            yield session

        with (
            mock.patch.object(health, "session_scope", session_scope),
            mock.patch.object(health, "pg_database", return_value=DATABASE),
        ):
            ready = await health._database_ready()
        return ready, session

    async def test_database_ready_requires_identity_database_and_every_core_table(
        self,
    ) -> None:
        ready_row = (
            "f1_api",
            DATABASE,
            *(True for _table in health._CORE_DATABASE_TABLES),
        )
        ready, session = await self._check(ready_row)

        self.assertTrue(ready)
        self.assertEqual(len(session.statements), 1)
        statement = session.statements[0]
        for table in health._CORE_DATABASE_TABLES:
            self.assertIn(f"to_regclass('{table}') IS NOT NULL", statement)

        for index, replacement in (
            (0, "f1_worker"),
            (1, "another_database"),
            *(
                (index, False)
                for index in range(2, len(ready_row))
            ),
        ):
            with self.subTest(index=index):
                changed = list(ready_row)
                changed[index] = replacement
                observed, _session = await self._check(tuple(changed))
                self.assertFalse(observed)

    async def test_database_ready_hides_dependency_failures(self) -> None:
        @contextlib.asynccontextmanager
        async def failing_scope(*, role: str):
            self.assertEqual(role, "f1_api")
            raise RuntimeError("private database detail")
            yield  # pragma: no cover

        with mock.patch.object(health, "session_scope", failing_scope):
            self.assertFalse(await health._database_ready())


if __name__ == "__main__":
    unittest.main()
