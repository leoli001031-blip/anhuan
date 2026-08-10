from __future__ import annotations

import argparse
import sys

from .router import (
    REGISTERED_CORE_MANIFEST,
    REGISTERED_NEGATIVE_MANIFEST,
    REGISTERED_SOURCE_ROOT,
    RouteFailure,
    build_route_plan,
    write_route_outputs,
)
from fixture_gate import ENVIRONMENT_DEMO_V01


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixture_router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="build a local Fixture route plan")
    plan.add_argument("--profile", choices=("smoke", "full"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "plan":
        return 2

    try:
        route_plan = build_route_plan(
            source_root=REGISTERED_SOURCE_ROOT,
            core_manifest=REGISTERED_CORE_MANIFEST,
            negative_manifest=REGISTERED_NEGATIVE_MANIFEST,
            profile=args.profile,
            expected_identity=ENVIRONMENT_DEMO_V01,
        )
        write_route_outputs(route_plan)
    except RouteFailure as error:
        details = " ".join(
            f"{key}={value}" for key, value in error.public_record().items()
        )
        print(f"fixture_router status=FAILED {details}", file=sys.stderr)
        return 2

    print(
        "fixture_router status=ROUTED "
        f"profile={args.profile} routed={route_plan['summary']['total']} failed=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
