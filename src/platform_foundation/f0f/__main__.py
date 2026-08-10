from __future__ import annotations

import argparse
import json
import os

from ..database import DatabaseConfig
from .acceptance import replay_profile
from .artifacts import generate_artifacts


def _config() -> DatabaseConfig:
    values = {
        "migration_dsn": os.environ.get("F0D_MIGRATION_DSN", ""),
        "runtime_dsn": os.environ.get("F0D_RUNTIME_DSN", ""),
        "worker_dsn": os.environ.get("F0D_WORKER_DSN", ""),
    }
    if not all(values.values()) or any(
        not value.startswith("postgresql://") for value in values.values()
    ):
        raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
    return DatabaseConfig(**values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="f0f-controlled-body")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay")
    replay.add_argument("--profile", required=True, choices=("smoke", "full"))
    commands.add_parser("artifacts")
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            result: dict[str, object] = replay_profile(_config(), args.profile)
        elif args.command == "artifacts":
            result = {
                "schema": "f0f-artifact-result-v1",
                "status": "LOCAL_FIXTURE_CONTROLLED_BODY_ACCEPTED",
                **generate_artifacts(_config()),
            }
        else:
            return 2
    except Exception:
        print(
            json.dumps(
                {"status": "FAILED", "reason_code": "F0F_OPERATION_FAILED"},
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
