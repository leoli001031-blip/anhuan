from __future__ import annotations

import ast
from pathlib import Path
import unittest

from infra.f1 import local_targeted_tests


ROOT = Path(__file__).resolve().parents[1]


class EngineeringCloseoutTargetedRunnerTests(unittest.TestCase):
    def test_runner_uses_frozen_modules_without_discovery_or_skips(self) -> None:
        self.assertEqual(len(local_targeted_tests.TEST_MODULES), 22)
        self.assertEqual(len(set(local_targeted_tests.TEST_MODULES)), 22)
        self.assertIn(
            "tests.test_engineering_closeout_browser_compose_supervisor",
            local_targeted_tests.TEST_MODULES,
        )
        self.assertIn(
            "tests.test_engineering_closeout_targeted_runner",
            local_targeted_tests.TEST_MODULES,
        )
        source = (ROOT / "infra/f1/local_targeted_tests.py").read_text(
            encoding="utf-8"
        )
        ast.parse(source)
        self.assertNotIn("discover(", source)
        self.assertIn('metrics["skipped"] != 0', source)
        self.assertIn("metrics[\"tests\"] < MINIMUM_TESTS", source)
        self.assertEqual(local_targeted_tests.MINIMUM_TESTS, 137)
        self.assertNotIn(
            "tests.test_engineering_closeout_browser_runner",
            local_targeted_tests.TEST_MODULES,
        )
        self.assertNotIn(
            "tests.test_engineering_closeout_pwa_build_id",
            local_targeted_tests.TEST_MODULES,
        )
        self.assertNotIn(
            "tests.test_p8_internal_pwa", local_targeted_tests.TEST_MODULES
        )

    def test_localctl_exposes_one_aggregate_targeted_test_command(self) -> None:
        source = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        dockerfile = (ROOT / "infra/f1/local.Dockerfile").read_text(
            encoding="utf-8"
        )
        targeted_dockerfile = (
            ROOT / "infra/f1/local-targeted.Dockerfile"
        ).read_text(encoding="utf-8")
        compose = (ROOT / "infra/f1/docker-compose.local.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('subparsers.add_parser("test")', source)
        self.assertIn('arguments.command == "test"', source)
        self.assertIn('"targeted-tests"', source)
        self.assertIn('"verifier", "web"', source)
        self.assertIn('"build", "targeted-tests"', source)
        self.assertIn('metrics["web_builds"] = 1', source)
        self.assertIn("COPY scripts/localctl /app/scripts/localctl", dockerfile)
        self.assertIn("/app/src/web/node_modules", targeted_dockerfile)
        self.assertIn("/opt/anhuan-node/ld-musl.so.1", targeted_dockerfile)
        self.assertIn("  targeted-tests:\n", compose)
        block = compose.split("  targeted-tests:\n", 1)[1].split(
            "  backup-db:\n", 1
        )[0]
        self.assertIn("../../tests:/app/tests:ro", block)
        self.assertIn("local-targeted.Dockerfile", block)
        self.assertNotIn("_secrets:", block)


if __name__ == "__main__":
    unittest.main()
