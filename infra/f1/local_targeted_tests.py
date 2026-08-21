"""Aggregate-only targeted engineering checks for the local closeout image."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_MODULES = (
    "tests.test_engineering_closeout_atomicity",
    "tests.test_engineering_closeout_backup",
    "tests.test_engineering_closeout_browser_compose_supervisor",
    "tests.test_engineering_closeout_business_verify",
    "tests.test_engineering_closeout_frontend_api",
    "tests.test_engineering_closeout_health",
    "tests.test_engineering_closeout_ingestion_verify",
    "tests.test_engineering_closeout_keycloak",
    "tests.test_engineering_closeout_log_verify",
    "tests.test_engineering_closeout_migration",
    "tests.test_engineering_closeout_reverse",
    "tests.test_engineering_closeout_targeted_runner",
    "tests.test_engineering_closeout_verify",
    "tests.test_p2_wave1",
    "tests.test_p2_wave2",
    "tests.test_p2_wave3",
    "tests.test_p2_wave4",
    "tests.test_p3_controlled_ingestion",
    "tests.test_p4_views_reports_crm",
    "tests.test_p5_policy_workflow",
    "tests.test_p6_automated_quality",
    "tests.test_p7_local_rehearsal",
)
MINIMUM_TESTS = 137


def main() -> int:
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(
            captured
        ):
            suite = unittest.defaultTestLoader.loadTestsFromNames(TEST_MODULES)
            result = unittest.TextTestRunner(
                stream=captured, verbosity=0, failfast=False, buffer=True
            ).run(suite)
    except BaseException:  # noqa: BLE001 - stdout must stay aggregate-only
        print("LOCAL_TARGETED_TESTS_FAILED", file=sys.stderr)
        return 1

    metrics = {
        "errors": len(result.errors),
        "failures": len(result.failures),
        "skipped": len(result.skipped),
        "tests": result.testsRun,
    }
    if (
        not result.wasSuccessful()
        or metrics["tests"] < MINIMUM_TESTS
        or metrics["skipped"] != 0
    ):
        print("LOCAL_TARGETED_TESTS_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(metrics, sort_keys=True, separators=(",", ":")))
    print("LOCAL_TARGETED_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
