"""Real isolated PostgreSQL runs must survive another run's cleanup."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from infra.f1.analysis_report_postgres_integration import (
    PostgresIntegrationStack,
    dedicated_counts,
)


class AnalysisReportHarnessParallelTests(unittest.TestCase):
    def test_stopping_one_run_keeps_the_other_database_and_resources(self) -> None:
        first = PostgresIntegrationStack()
        second = PostgresIntegrationStack()
        print(f"HARNESS_PARALLEL projects={first.project_name},{second.project_name}", flush=True)
        try:
            with patch.dict(os.environ):
                first.start()
                second.start()
                second_counts = dedicated_counts(
                    project_name=second.project_name, project_id=second.project_id
                )
                self.assertTrue(all(count > 0 for count in second_counts))
                with second._bootstrap() as connection:
                    before = connection.execute(
                        "SELECT current_database(), version_num FROM f1.alembic_version"
                    ).fetchone()
                first.stop()
                self.assertEqual(first.cleanup_status, "CLEAN")
                self.assertEqual(
                    dedicated_counts(project_name=second.project_name, project_id=second.project_id),
                    second_counts,
                )
                with second._bootstrap() as connection:
                    after = connection.execute(
                        "SELECT current_database(), version_num FROM f1.alembic_version"
                    ).fetchone()
                self.assertEqual(after, before)
                self.assertEqual(after[0], second.database)
                print(f"HARNESS_PARALLEL surviving_head={after[1]}", flush=True)
        finally:
            # Each harness only selects its own three-label resource set, even
            # after a failed start.  Attempt both cleanups if the first fails.
            try:
                if first.cleanup_status != "CLEAN":
                    first.stop()
            finally:
                second.stop()
        self.assertEqual(first.cleanup_status, "CLEAN")
        self.assertEqual(second.cleanup_status, "CLEAN")
        self.assertEqual(first.dedicated_after, (0, 0, 0))
        self.assertEqual(second.dedicated_after, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
