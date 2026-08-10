"""Strict command-line entry point for the local F0-H fixture runtime."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .artifacts import generate_artifacts
from .contracts import F0HError
from .replay import replay_profile


_FAILURE = {
    "reason_code": "F0H_OPERATION_FAILED",
    "status": "FAILED",
}


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        self.exit(2, json.dumps(_FAILURE, ensure_ascii=True, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeArgumentParser(prog="f0h-local-ppocrv6")
    commands = parser.add_subparsers(dest="command", required=True)
    replay = commands.add_parser("replay", help="replay a registered fixture profile")
    replay.add_argument("--profile", required=True)
    commands.add_parser("artifacts", help="run acceptance replays and write artifacts")
    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            if args.profile not in {"smoke", "full"}:
                return _print_failure()
            result: dict[str, object] = replay_profile(args.profile)
        elif args.command == "artifacts":
            result = {
                "schema": "f0h-artifact-result-v1",
                "status": "LOCAL_PPOCRV6_RUNTIME_READY",
                "accuracy_status": "ACCURACY_NOT_EVALUATED",
                "search_status": "SEARCH_NOT_READY",
                "production_status": "NOT_PRODUCTION",
                **generate_artifacts(),
            }
        else:
            return _print_failure()
    except F0HError:
        return _print_failure()
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


def _print_failure() -> int:
    print(json.dumps(_FAILURE, ensure_ascii=True, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
