"""Offline contract for the body-free F1.1.1 PostgreSQL verifier.

These tests never open a database connection.  They pin the live verifier's
fail-closed surface so the separate acceptance run can exercise a fresh,
random scratch database without turning caller-supplied output into evidence.
"""
from __future__ import annotations

import ast
import inspect
import unittest
from unittest import mock

from infra.f1 import formal_acceptance
from tests import f111_repair_pg_verify as verifier


EXPECTED_LIVE_METRICS = {
    "scratch_preexisting_rows",
    "enterprise_control_failures",
    "resolver_scope_violations",
    "invite_escalation_acceptances",
    "invite_concurrency_failures",
    "invite_membership_mismatches",
    "invite_audit_mismatches",
    "upload_claim_failures",
    "upload_token_guard_failures",
    "outbox_claim_failures",
    "outbox_token_guard_failures",
    "qa_claim_state_failures",
    "qa_owner_guard_failures",
    "qa_completion_audit_failures",
    "fixture_cleanup_residuals",
}


class F111RepairPgLiveContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = inspect.getsource(verifier)
        cls.tree = ast.parse(cls.source)

    def test_live_metrics_are_explicit_and_fail_closed(self) -> None:
        self.assertTrue(EXPECTED_LIVE_METRICS.issubset(set(verifier.METRICS)))
        rendered = verifier._render({name: 0 for name in verifier.METRICS})
        self.assertEqual(len(rendered.split()), len(verifier.METRICS))
        self.assertNotIn("None", rendered)
        self.assertEqual(tuple(formal_acceptance.PG_METRICS), tuple(verifier.METRICS))

    def test_shared_database_is_rejected_before_any_connection(self) -> None:
        with mock.patch.object(verifier, "pg_database", return_value="shared_f1"):
            with mock.patch.object(verifier.psycopg, "connect") as connect:
                with self.assertRaisesRegex(RuntimeError, "SCRATCH_DATABASE_REQUIRED"):
                    verifier.verify()
        connect.assert_not_called()

    def test_only_exact_standalone_or_formal_scratch_names_are_accepted(self) -> None:
        accepted = (
            "anhuan_f111_repair_xdkaou",
            "f111_repair_0123456789abcdef0123456789abcdef",
        )
        rejected = (
            "anhuan_f111_repair_",
            "anhuan_f111_repair_XDKAOU",
            "f111_repair_short",
            "f111_repair_0123456789abcdef0123456789abcdef_extra",
            "prefix_f111_repair_0123456789abcdef0123456789abcdef",
        )
        for name in accepted:
            self.assertTrue(verifier._is_repair_scratch_database(name), name)
        for name in rejected:
            self.assertFalse(verifier._is_repair_scratch_database(name), name)

    def test_verifier_has_random_registry_and_exact_finally_cleanup(self) -> None:
        self.assertIn("class _Fixture", self.source)
        self.assertIn("uuid.uuid4", self.source)
        self.assertIn("def _cleanup_fixture", self.source)
        self.assertIn("finally:", self.source)
        self.assertIn("fixture_cleanup_residuals", self.source)
        self.assertIn("scratch_preexisting_rows", self.source)

    def test_invite_contract_is_two_connection_and_counted(self) -> None:
        for token in (
            "ThreadPoolExecutor(max_workers=2)",
            "threading.Barrier(2)",
            "f1.create_invite_for_current_sub",
            "f1.consume_invite",
            "invite_concurrency_failures",
            "invite_membership_mismatches",
            "invite_audit_mismatches",
        ):
            self.assertIn(token, self.source)

    def test_worker_and_outbox_token_contracts_are_live_calls(self) -> None:
        for token in (
            "f1.claim_upload_task",
            "f1.renew_upload_lease",
            "upload_token_guard_failures",
            "f1.claim_pending_dispatch",
            "f1.complete_dispatch",
            "outbox_token_guard_failures",
            "dispatch_lease_until",
        ):
            self.assertIn(token, self.source)

    def test_qa_contract_covers_claim_states_owner_and_atomic_audit(self) -> None:
        for token in (
            "f1.claim_qa_request",
            "CLAIMED",
            "IN_PROGRESS",
            "CONFLICT",
            "f1.complete_qa_request",
            "qa_owner_guard_failures",
            "qa_completion_audit_failures",
            "connection.rollback()",
        ):
            self.assertIn(token, self.source)

    def test_output_has_one_aggregate_print_and_no_exception_payload(self) -> None:
        print_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        self.assertEqual(len(print_calls), 1)
        self.assertEqual(ast.unparse(print_calls[0].args[0]), "_render(metrics)")
        self.assertNotIn("traceback", self.source.lower())
        self.assertNotIn("print(exc", self.source.lower())
        self.assertNotIn("print(error", self.source.lower())


class F111RepairPgOutcomeTests(unittest.TestCase):
    """Exercise the body-free counters used by the live scratch attacks."""

    def test_nonmember_boundary_requires_positive_bridge_and_four_denials(self) -> None:
        baseline = {
            "authorized_bridge_rows": 1,
            "document_rows": 0,
            "audit_rows": 0,
            "bridge_rows": 0,
            "document_insert_denied": True,
        }
        self.assertEqual(verifier._boundary_failure_counts(**baseline), (0, 0))
        for name, value in (
            ("authorized_bridge_rows", 0),
            ("document_rows", 1),
            ("audit_rows", 1),
            ("bridge_rows", 1),
            ("document_insert_denied", False),
        ):
            changed = dict(baseline)
            changed[name] = value
            self.assertNotEqual(verifier._boundary_failure_counts(**changed), (0, 0))

    def test_invite_rejection_counter_rejects_every_partial_side_effect(self) -> None:
        expected = verifier._InviteRejectionState(1, 0, 0, 0, 0)
        self.assertEqual(
            verifier._invite_rejection_failure_count(
                accepted=False,
                observed=expected,
                expected_profile_rows=0,
                expected_membership_rows=0,
                expected_role_rows=0,
            ),
            0,
        )
        for field_name in expected.__dataclass_fields__:
            values = {
                field: getattr(expected, field)
                for field in expected.__dataclass_fields__
            }
            values[field_name] += 1
            self.assertEqual(
                verifier._invite_rejection_failure_count(
                    accepted=False,
                    observed=verifier._InviteRejectionState(**values),
                    expected_profile_rows=0,
                    expected_membership_rows=0,
                    expected_role_rows=0,
                ),
                1,
            )
        self.assertEqual(
            verifier._invite_rejection_failure_count(
                accepted=True,
                observed=expected,
                expected_profile_rows=0,
                expected_membership_rows=0,
                expected_role_rows=0,
            ),
            1,
        )

    def test_existing_membership_rejection_requires_role_and_jti_unchanged(self) -> None:
        observed = verifier._InviteRejectionState(1, 1, 1, 1, 0)
        self.assertEqual(
            verifier._invite_rejection_failure_count(
                accepted=False,
                observed=observed,
                expected_profile_rows=1,
                expected_membership_rows=1,
                expected_role_rows=1,
            ),
            0,
        )
        changed = verifier._InviteRejectionState(0, 1, 1, 0, 1)
        self.assertEqual(
            verifier._invite_rejection_failure_count(
                accepted=False,
                observed=changed,
                expected_profile_rows=1,
                expected_membership_rows=1,
                expected_role_rows=1,
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
