"""Fixed local CLI for F0-I replay and aggregate artifacts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .contracts import F0IError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform_foundation.f0i")
    subcommands = parser.add_subparsers(dest="command", required=True)
    replay = subcommands.add_parser("replay")
    replay.add_argument("--profile", choices=("smoke", "full"), required=True)
    subcommands.add_parser("artifacts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "replay":
            from .replay import replay_profile

            result = replay_profile(arguments.profile)
        elif arguments.command == "artifacts":
            from .artifacts import generate_artifacts

            result = generate_artifacts()
        else:
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        print(
            json.dumps(
                result,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
        return 0
    except F0IError as error:
        print(
            json.dumps(
                error.to_dict(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except Exception:
        print(
            '{"error":"F0I_ERROR","reason_code":"PERSISTENCE_FAILED"}'
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main",)
