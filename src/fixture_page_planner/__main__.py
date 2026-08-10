from __future__ import annotations

import argparse
import sys

from fixture_gate import ENVIRONMENT_DEMO_V01
from fixture_router.router import (
    REGISTERED_CORE_MANIFEST,
    REGISTERED_NEGATIVE_MANIFEST,
    REGISTERED_SOURCE_ROOT,
)

from .planner import (
    REGISTERED_FULL_ROUTE_PLAN,
    REGISTERED_SMOKE_ROUTE_PLAN,
    PlannerFailure,
    build_page_plan,
    write_page_outputs,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixture_page_planner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="build a body-free native page plan")
    plan.add_argument("--profile", choices=("smoke", "full"), required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "plan":
        return 2
    route_plan_path = (
        REGISTERED_SMOKE_ROUTE_PLAN
        if args.profile == "smoke"
        else REGISTERED_FULL_ROUTE_PLAN
    )
    try:
        plan = build_page_plan(
            source_root=REGISTERED_SOURCE_ROOT,
            core_manifest=REGISTERED_CORE_MANIFEST,
            negative_manifest=REGISTERED_NEGATIVE_MANIFEST,
            route_plan_path=route_plan_path,
            profile=args.profile,
            expected_identity=ENVIRONMENT_DEMO_V01,
        )
        write_page_outputs(plan)
    except PlannerFailure as error:
        details = " ".join(
            f"{key}={value}" for key, value in error.public_record().items()
        )
        print(f"fixture_page_planner status=FAILED {details}", file=sys.stderr)
        return 2
    except Exception:
        print(
            "fixture_page_planner status=FAILED code=INTERNAL_FAILURE",
            file=sys.stderr,
        )
        return 2

    summary = plan["summary"]
    print(
        "fixture_page_planner status=PLANNED "
        f"profile={args.profile} documents={summary['documents']} "
        f"visual_units={summary['visual_units']} errors=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
