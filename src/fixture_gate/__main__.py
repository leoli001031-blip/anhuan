from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .validator import (
    AuditWriteFailure,
    ENVIRONMENT_DEMO_V01,
    ValidationFailure,
    failure_audit,
    verify_fixture_set,
    write_audit,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fixture_gate")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="verify a local Fixture set")
    verify.add_argument("--source-root", required=True, type=Path)
    verify.add_argument("--core-manifest", required=True, type=Path)
    verify.add_argument("--negative-manifest", required=True, type=Path)
    verify.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "verify":
        return 2

    allowed_output_root = Path.cwd() / "artifacts/fixture-audit/v0.1"
    try:
        audit = verify_fixture_set(
            source_root=args.source_root,
            core_manifest=args.core_manifest,
            negative_manifest=args.negative_manifest,
            expected_identity=ENVIRONMENT_DEMO_V01,
        )
    except ValidationFailure as error:
        try:
            write_audit(
                failure_audit(error), args.output, allowed_root=allowed_output_root
            )
        except AuditWriteFailure:
            print(
                "fixture_gate status=FAILED code=AUDIT_WRITE_FAILED",
                file=sys.stderr,
            )
            return 2
        fields = error.public_record()
        details = " ".join(f"{key}={value}" for key, value in fields.items())
        print(f"fixture_gate status=FAILED {details}", file=sys.stderr)
        return 2

    try:
        write_audit(audit, args.output, allowed_root=allowed_output_root)
    except AuditWriteFailure:
        print("fixture_gate status=FAILED code=AUDIT_WRITE_FAILED", file=sys.stderr)
        return 2
    summary = audit["summary"]
    print(
        "fixture_gate status=VERIFIED "
        f"verified={summary['verified']} failed={summary['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
