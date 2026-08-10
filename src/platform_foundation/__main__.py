from __future__ import annotations

import argparse
import json
import os

from .database import DatabaseConfig
from .replay import replay_profile


def _config() -> DatabaseConfig:
    values = {
        "migration_dsn": os.environ.get("F0D_MIGRATION_DSN", ""),
        "runtime_dsn": os.environ.get("F0D_RUNTIME_DSN", ""),
        "worker_dsn": os.environ.get("F0D_WORKER_DSN", ""),
    }
    if not all(values.values()):
        raise SystemExit("F0D_DATABASE_DSN_REQUIRED")
    if "postgresql" not in values["migration_dsn"]:
        raise SystemExit("F0D_POSTGRESQL_REQUIRED")
    return DatabaseConfig(**values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixture-platform-foundation")
    subparsers = parser.add_subparsers(dest="command", required=True)
    replay = subparsers.add_parser("replay")
    replay.add_argument("--profile", required=True, choices=("smoke", "full"))
    args = parser.parse_args(argv)
    if args.command != "replay":
        return 2
    try:
        result = replay_profile(_config(), args.profile)
    except Exception:
        print(json.dumps({"status": "FAILED", "reason_code": "REPLAY_FAILED"}))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
