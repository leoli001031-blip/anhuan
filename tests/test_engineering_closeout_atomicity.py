"""Database-free contracts for the real migration atomicity one-shot."""
from __future__ import annotations

import inspect
import json
import unittest

from infra.f1.local_migration_atomicity import (
    AtomicityCounts,
    AtomicityError,
    RollbackObservation,
    render_success,
    rewrite_dsn_database,
    scratch_database_name,
    verify_normal_heads,
    verify_rollback_observation,
)
import infra.f1.local_migration_atomicity as atomicity


class EngineeringCloseoutAtomicityTests(unittest.TestCase):
    def test_scratch_name_is_random_and_bound_to_the_local_source(self) -> None:
        source = "anhuan_closeout_0123456789abcdef01234567"
        first = scratch_database_name(source, "a" * 32)
        second = scratch_database_name(source, "b" * 32)
        self.assertEqual(first, "anhuan_atomicity_0123456789ab_aaaaaaaaaaaaaaaa")
        self.assertNotEqual(first, second)
        for invalid in (
            "postgres",
            "anhuan_closeout_0123",
            "shared_0123456789abcdef01234567",
        ):
            with self.subTest(source=invalid):
                with self.assertRaisesRegex(
                    AtomicityError, "^LOCAL_ATOMICITY_SOURCE_INVALID$"
                ):
                    scratch_database_name(invalid, "a" * 32)

    def test_dsn_rewrite_changes_only_the_database(self) -> None:
        source = (
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            "anhuan_closeout_0123456789abcdef01234567"
        )
        scratch = "anhuan_atomicity_0123456789ab_aaaaaaaaaaaaaaaa"
        rewritten = rewrite_dsn_database(
            source,
            database=scratch,
            expected_user="f0d_bootstrap",
        )
        self.assertEqual(
            rewritten,
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            + scratch,
        )
        self.assertNotIn("?", rewritten)

    def test_failure_rollback_requires_every_observed_count_to_be_zero(self) -> None:
        verify_rollback_observation(RollbackObservation(0, 0, 0, 0, 0))
        fields = (
            "f0_schema_count",
            "f1_schema_count",
            "version_table_count",
            "business_relation_count",
            "business_routine_count",
        )
        for field in fields:
            values = {name: 0 for name in fields}
            values[field] = 1
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    AtomicityError, "^LOCAL_ATOMICITY_ROLLBACK_MISMATCH$"
                ):
                    verify_rollback_observation(RollbackObservation(**values))

    def test_normal_migration_requires_one_exact_head_per_schema(self) -> None:
        verify_normal_heads(("f0d_0006",), ("f1_0014",))
        for f0_heads, f1_heads in (
            (("f0d_0006", "extra"), ("f1_0014",)),
            (("f0d_0005",), ("f1_0014",)),
            (("f0d_0006",), ()),
        ):
            with self.subTest(f0=f0_heads, f1=f1_heads):
                with self.assertRaisesRegex(
                    AtomicityError, "^LOCAL_ATOMICITY_HEAD_MISMATCH$"
                ):
                    verify_normal_heads(f0_heads, f1_heads)

    def test_runner_uses_the_real_inprocess_failpoint_and_exact_cleanup(self) -> None:
        source = inspect.getsource(atomicity)
        probe = inspect.getsource(atomicity._execute_probe)
        cleanup = inspect.getsource(atomicity._drop_scratch_database)
        self.assertIn(
            "local_migrate.migrate(after_f1_upgrade=_raise_injected_failure)",
            probe,
        )
        self.assertEqual(probe.count("local_migrate.migrate("), 2)
        self.assertIn("_scratch_f0_schema_bootstrap(local_migrate)", probe)
        bootstrap = inspect.getsource(atomicity._scratch_f0_schema_bootstrap)
        self.assertIn("CREATE SCHEMA f0d AUTHORIZATION f0d_migration", bootstrap)
        self.assertNotIn("IF NOT EXISTS", bootstrap)
        self.assertIn("verify_rollback_observation", probe)
        self.assertIn("verify_normal_heads", probe)
        self.assertIn("CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0", source)
        self.assertIn('sql.SQL("DROP DATABASE {} WITH (FORCE)")', cleanup)
        self.assertIn("sql.Identifier(scratch_database)", cleanup)
        self.assertNotIn("DROP DATABASE IF EXISTS", cleanup)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("docker", source.lower())

    def test_output_is_body_free_fixed_metrics(self) -> None:
        metrics, tag = render_success(AtomicityCounts())
        decoded = json.loads(metrics)
        self.assertEqual(tag, "LOCAL_MIGRATION_ATOMICITY_OK")
        self.assertTrue(all(type(value) is int for value in decoded.values()))
        self.assertEqual(decoded["scratch_database_residual_count"], 0)
        for forbidden in (
            "anhuan_closeout_",
            "anhuan_atomicity_",
            "postgresql://",
            "password",
            "fixture.invalid",
        ):
            self.assertNotIn(forbidden, metrics)


if __name__ == "__main__":
    unittest.main()
