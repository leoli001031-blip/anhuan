"""Soft-archive closeout contracts: guard wiring, list filtering, UI lifecycle.

Regression guards for the fifth review round (PR #18 findings):

1.  Unarchive must not fall through into the f1_0023 business branches
    (ANALYSIS_REPORT_UPDATE_INVALID regression on restore).
2.  apply_transition must reject submit/return/approve/publish for archived
    reports under the report row lock; withdraw keeps its own contract.
3.  generate_report must gate BOTH dispatch paths (fresh request and
    exact-request resume/requeue) on the archived state.
4.  Provider lists hide archived reports by default behind an explicit
    include_archived opt-in; client-facing published surfaces filter archived
    rows as defense in depth.
5.  The finding detail page must not let a stale submission clear the next
    client's form/loading state (onSuccess after epoch check, action-scoped
    loading slot, explicit context-switch form reset).

Static contract tests: they pin the wiring the runtime behaviour depends on.
The live database replay lives in the integration gate evidence; these tests
keep the orchestration seams from silently losing that wiring.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0025_report_soft_archive.py"
MIGRATION_AUDIT = ROOT / "infra/f1/alembic/versions/f1_0026_report_management_audit.py"
SERVICE = ROOT / "src/platform_foundation/f1/features/analysis_reports/service.py"
REPOSITORY = ROOT / "src/platform_foundation/f1/features/analysis_reports/repository.py"
ROUTER = ROOT / "src/platform_foundation/f1/api/routers/analysis_reports.py"
FINDING_DETAIL = ROOT / "src/web/src/pages/console/ClientFindingDetailPage.tsx"
CLIENT_REPORTS_PAGE = ROOT / "src/web/src/pages/console/ClientReportsPage.tsx"
HTTP_API = ROOT / "src/web/src/adapters/HttpAnalysisReportApi.ts"
MOCK_API = ROOT / "src/web/src/adapters/MockAnalysisReportApi.ts"
WIRE = ROOT / "src/web/src/adapters/wire.ts"
TYPES = ROOT / "src/web/src/adapters/types.ts"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


class GuardMigrationContracts(unittest.TestCase):
    """The database guard is the last line of defence for archived reports."""

    def setUp(self) -> None:
        self.source = _source(MIGRATION)

    def test_archived_branch_precedes_all_business_branches(self) -> None:
        archived = self.source.index("IF NEW.archived_at IS NOT NULL THEN")
        immutable = self.source.index("ANALYSIS_REPORT_ARCHIVED_IMMUTABLE")
        business = self.source.index(
            "IF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN"
        )
        self.assertLess(archived, immutable)
        self.assertLess(immutable, business)

    def test_active_version_check_includes_queued(self) -> None:
        active = _between(
            self.source,
            "SELECT 1 FROM f1.analysis_report_version AS version",
            "RAISE EXCEPTION 'ANALYSIS_REPORT_ARCHIVE_ACTIVE'",
        )
        self.assertIn("'queued'", active)
        self.assertIn("'review_pending'", active)
        self.assertIn("'published'", active)

    def test_unarchive_branch_restores_without_business_mixing(self) -> None:
        # The unarchive branch must sit between the archived branch and the
        # business branches, and must reject mixed business-field updates.
        archived_return = self.source.index(
            "ANALYSIS_REPORT_ARCHIVE_ACTIVE"
        )
        unarchive = self.source.index("IF OLD.archived_at IS NOT NULL THEN")
        business = self.source.index(
            "IF NEW.current_version_id IS DISTINCT FROM OLD.current_version_id THEN"
        )
        self.assertLess(archived_return, unarchive)
        self.assertLess(unarchive, business)
        branch = _between(
            self.source,
            "IF OLD.archived_at IS NOT NULL THEN",
            "-- Report is NOT archived",
        )
        self.assertIn("ANALYSIS_REPORT_UNARCHIVE_MIXED", branch)
        self.assertIn("RETURN NEW;", branch)

    def test_downgrade_restores_pre_0025_guard_and_revokes_grants(self) -> None:
        downgrade = self.source.split("def downgrade() -> None:", 1)[1]
        self.assertIn(
            "CREATE OR REPLACE FUNCTION f1.guard_analysis_report_write()",
            downgrade,
        )
        self.assertIn("ANALYSIS_REPORT_UPDATE_INVALID", downgrade)
        # The restored guard must no longer reference archive columns.
        restored = _between(
            downgrade,
            "CREATE OR REPLACE FUNCTION f1.guard_analysis_report_write()",
            "$$\n        \"\"\"\n    )",
        )
        self.assertNotIn("archived_at", restored)
        self.assertNotIn("ANALYSIS_REPORT_ARCHIVED_IMMUTABLE", restored)
        self.assertIn(
            "REVOKE UPDATE (archived_at, archived_by_user_id, archived_reason) ",
            downgrade,
        )
        self.assertIn("ON f1.analysis_report FROM f1_api", downgrade)


class ServiceContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(SERVICE)

    def test_apply_transition_rejects_archived_under_row_lock(self) -> None:
        body = _between(
            self.source,
            "async def apply_transition(",
            "async def _store_health_snapshot",
        )
        lock = body.index("lock_report_for_generation")
        fence = body.index(
            'locked_report.get("current_version_id") != version_id'
        )
        archived = body.index('locked_report.get("archived_at") is not None')
        transition = body.index("transition_version")
        self.assertLess(lock, fence)
        self.assertLess(fence, archived)
        self.assertLess(archived, transition)
        # Withdraw keeps its separate contract (archiving already requires
        # no active versions, so published-then-archived cannot occur).
        self.assertRegex(
            body,
            r"action != \"withdraw\"\s*\n?\s*and locked_report is not None",
        )

    def test_generate_gates_both_dispatch_paths_on_archived(self) -> None:
        body = _between(
            self.source,
            "async def generate_report(",
            "async def _resume_generation",
        )
        # Exact-request resume path: lock + reject BEFORE the audited requeue.
        resume = _between(body, "if existing_job is not None:", "else:")
        self.assertIn("lock_report_for_generation", resume)
        self.assertLess(
            resume.index("lock_report_for_generation"),
            resume.index("_resume_generation"),
        )
        self.assertIn('resume_lock.get("archived_at") is not None', resume)
        # Fresh dispatch path keeps its under-lock rejection.
        fresh = _between(body, "else:", "if (\n                    concurrent_job")
        self.assertIn('locked_report.get("archived_at") is not None', fresh)

    def test_dead_unlocked_archived_helper_is_not_reintroduced(self) -> None:
        # The unlocked helper invited call sites that race the archive mark;
        # every archived decision must happen under the report row lock.
        self.assertNotIn("async def _check_not_archived(", self.source)

    def test_provider_list_hides_archived_behind_explicit_opt_in(self) -> None:
        body = _between(
            self.source,
            "async def list_client_reports(",
            "async def create_report(",
        )
        self.assertIn("include_archived: bool = False", body)
        self.assertIn("include_archived=include_archived", body)
        summary = _between(self.source, "def _summary(", "def _require_provider(")
        self.assertIn('"archived_at"', summary)


class RepositoryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(REPOSITORY)

    def test_provider_list_filters_archived_by_default(self) -> None:
        body = _between(
            self.source,
            "async def list_provider_reports(",
            "async def list_published_for_client(",
        )
        self.assertIn(
            'archived_filter = "" if include_archived else '
            '"AND report.archived_at IS NULL "',
            body,
        )
        self.assertIn("report.archived_at, version.status AS current_status", body)

    def test_client_facing_published_surfaces_exclude_archived(self) -> None:
        published_list = _between(
            self.source,
            "async def list_published_for_client(",
            "async def get_published_detail(",
        )
        self.assertIn("AND report.archived_at IS NULL", published_list)
        published_detail = _between(
            self.source,
            "async def get_published_detail(",
            "async def attach_sections(",
        )
        self.assertIn("AND report.archived_at IS NULL", published_detail)


class RouterContracts(unittest.TestCase):
    def test_provider_list_endpoint_exposes_include_archived_opt_in(self) -> None:
        source = _source(ROUTER)
        endpoint = _between(
            source,
            '@router.get("/clients/{client_account_id}/reports")',
            '@router.post("/clients/{client_account_id}/reports")',
        )
        self.assertIn("include_archived: bool = False", endpoint)
        self.assertIn("include_archived=include_archived", endpoint)


class ManagementAuditMigrationContracts(unittest.TestCase):
    """f1_0026: immutable archive/unarchive audit stream."""

    def setUp(self) -> None:
        self.source = _source(MIGRATION_AUDIT)

    def test_insert_only_event_table_with_guard(self) -> None:
        self.assertIn(
            "CREATE TABLE f1.analysis_report_management_event",
            self.source,
        )
        # Only SELECT + column-scoped INSERT are granted; no UPDATE/DELETE
        # grant exists anywhere, making the trail immutable for f1_api.
        self.assertIn(
            "GRANT INSERT (id, enterprise_id, report_id, actor_user_id, ",
            self.source,
        )
        self.assertIn('"ON f1.analysis_report_management_event TO f1_api"', self.source)
        grants = _between(self.source, "GRANT SELECT ON", "CREATE FUNCTION")
        self.assertNotIn("GRANT UPDATE", grants)
        self.assertNotIn("GRANT DELETE", grants)
        self.assertIn(
            "CREATE TRIGGER analysis_report_management_insert_guard",
            self.source,
        )

    def test_guard_binds_event_to_transaction_identity_not_timestamps(self) -> None:
        guard = _between(
            self.source,
            "CREATE FUNCTION f1.guard_analysis_report_management_insert()",
            "def downgrade()",
        )
        # Actor re-authentication + session enterprise binding.
        self.assertIn("REPORT_MANAGEMENT_EVENT_ACTOR_INVALID", guard)
        self.assertIn("current_setting('f1.enterprise_id',true)", guard)
        # Transaction identity: the report row must have been WRITTEN by
        # this transaction (xmin equality).  Timestamp ordering is NOT a
        # same-transaction proof and must not appear at all.
        self.assertIn("report.xmin::text::bigint", guard)
        self.assertIn("pg_current_xact_id()::text::bigint & 4294967295", guard)
        self.assertIn("REPORT_MANAGEMENT_EVENT_TX_MISMATCH", guard)
        self.assertNotIn("transaction_timestamp()", guard)
        self.assertNotIn("updated_at>=", guard)
        # One transition, one event: duplicate consumption inside the same
        # transaction (different event ids) is rejected.
        self.assertIn("REPORT_MANAGEMENT_EVENT_DUPLICATE", guard)
        self.assertIn("event.xmin::text::bigint=v_self_xid", guard)
        # report_archived must match the archived_by/reason actually written.
        self.assertIn("report.archived_reason IS NOT DISTINCT FROM NEW.reason", guard)
        # report_unarchived carries no reason and requires the mark cleared.
        self.assertIn("NEW.reason IS NOT NULL", guard)
        # Actions are a closed set.
        self.assertIn("'report_archived'", self.source)
        self.assertIn("'report_unarchived'", self.source)

    def test_rls_forced_with_provider_admin_policies(self) -> None:
        self.assertIn(
            '"ALTER TABLE f1.analysis_report_management_event "',
            self.source,
        )
        self.assertIn('"ENABLE ROW LEVEL SECURITY"', self.source)
        self.assertIn('"FORCE ROW LEVEL SECURITY"', self.source)
        self.assertIn(
            "CREATE POLICY analysis_report_management_event_insert",
            self.source,
        )

    def test_migration_is_linear_head_after_0025(self) -> None:
        self.assertIn('revision: str = "f1_0026"', self.source)
        self.assertIn('down_revision: str | None = "f1_0025"', self.source)


class ArchiveAuditServiceContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.source = _source(SERVICE)

    def test_archive_writes_audit_event_in_same_transaction(self) -> None:
        body = _between(
            self.source, "async def archive_report(", "async def unarchive_report("
        )
        update = body.index("UPDATE f1.analysis_report SET")
        event = body.index("INSERT INTO f1.analysis_report_management_event")
        commit = body.index("await session.commit()")
        self.assertLess(update, event)
        self.assertLess(event, commit)
        self.assertIn("'report_archived'", body)
        # The idempotent early return must stay BEFORE the update/event pair,
        # so a repeated archive never duplicates the audit event.
        early = body.index("already_archived")
        self.assertLess(early, update)

    def test_unarchive_writes_audit_event_in_same_transaction(self) -> None:
        body = self.source.split("async def unarchive_report(", 1)[1]
        update = body.index("UPDATE f1.analysis_report SET")
        event = body.index("INSERT INTO f1.analysis_report_management_event")
        commit = body.index("await session.commit()")
        self.assertLess(update, event)
        self.assertLess(event, commit)
        self.assertIn("'report_unarchived'", body)
        early = body.index("already_unarchived")
        self.assertLess(early, update)



class FindingDetailLifecycleContracts(unittest.TestCase):
    """Stale submissions must not clear the next client's form or loading."""

    def setUp(self) -> None:
        self.source = _source(FINDING_DETAIL)

    def test_run_action_applies_on_success_only_after_epoch_check(self) -> None:
        body = _between(
            self.source,
            "const runAction = async (",
            "const allowed = finding?",
        )
        self.assertIn("onSuccess?: () => void,", body)
        epoch_check = body.index("await operation();")
        stale_drop = body.index(
            "if (refreshEpoch.current !== epoch) return;", epoch_check
        )
        success = body.index("onSuccess?.();")
        self.assertLess(epoch_check, stale_drop)
        self.assertLess(stale_drop, success)

    def test_operations_are_pure_api_calls(self) -> None:
        # The old pattern ran setState INSIDE the awaited operation, before
        # the epoch check — a late stale submission cleared the next client's
        # draft. Operations must only call the API; setters move to onSuccess.
        correction = _between(
            self.source, "const submitCorrection = async () => {", "const submitReview = async () => {"
        )
        self.assertIn("() =>\n        submitCorrectiveAction(", correction)
        self.assertNotIn("const result = await submitCorrectiveAction", correction)
        review = _between(
            self.source,
            "const submitReview = async () => {",
            "if (loading) {",
        )
        self.assertIn("() =>\n        reviewFinding(", review)
        self.assertNotIn("const result = await reviewFinding", review)
        edit = _between(self.source, "onFinish={async (values) => {", "</Form>")
        self.assertNotIn("const result = await updateFinding", edit)

    def test_loading_slot_is_action_scoped(self) -> None:
        body = _between(
            self.source,
            "const runAction = async (",
            "const allowed = finding?",
        )
        self.assertIn("const actionSeq = useRef(0);", self.source)
        self.assertIn("const seq = ++actionSeq.current;", body)
        self.assertIn(
            "if (actionSeq.current === seq) setActionLoading(null);", body
        )

    def test_context_switch_resets_forms_explicitly(self) -> None:
        reset = _between(
            self.source,
            "// 上下文切换时显式清空表单/弹窗",
            "}, [clientId, findingId]);",
        )
        for setter in (
            "setCorrectionOpen(false)",
            'setCorrectionText("")',
            "setReviewDecision(null)",
            'setReviewComment("")',
            "setEditOpen(false)",
        ):
            self.assertIn(setter, reset)


class ClientReportsPageContracts(unittest.TestCase):
    def test_page_uses_explicit_archived_filter_and_restore_entry(self) -> None:
        source = _source(CLIENT_REPORTS_PAGE)
        self.assertIn(
            "listClientReports(clientId, { includeArchived: showArchived })",
            source,
        )
        self.assertIn("api.unarchiveReport(row.report_id)", source)
        self.assertIn("api.archiveReport(target.report_id", source)
        self.assertIn("maxLength={500}", source)
        # Archived rows never link into the workbench.
        self.assertIn("{r.archived_at ? (", source)

    def test_page_binds_rows_targets_and_actions_to_client_context(self) -> None:
        # Sixth-review finding: a stale archive modal / stale rows must not
        # operate on the previous client's reports after a route switch.
        source = _source(CLIENT_REPORTS_PAGE)
        reset = _between(
            source,
            "// 切客户立即清空客户绑定的瞬时状态",
            "}, [clientId]);",
        )
        for setter in (
            "setRows(null)",
            "setArchiveTarget(null)",
            'setArchiveReason("")',
            "setActionReportId(null)",
            "createRequestId.current = null",
        ):
            self.assertIn(setter, reset)
        # Context epoch increments on switch/unmount only.
        self.assertIn("const contextEpoch = useRef(0);", source)
        self.assertIn("++contextEpoch.current;", source)
        # Pre-submit ownership check: no write request may leave a dead target.
        submit = _between(source, "const submitArchive = async () => {", "const restore = async")
        self.assertIn(
            "if (contextEpoch.current !== targetEpoch.current) {", submit
        )
        precheck = submit.index("contextEpoch.current !== targetEpoch.current")
        api_call = submit.index("api.archiveReport(")
        self.assertLess(precheck, api_call)
        # List responses only land on the context that fetched them.
        listing = _between(
            source, "useEffect(() => {", "}, [api, clientId, nonce, showArchived];"
        )
        self.assertIn("contextEpoch.current === epoch", listing)


class FrontendAdapterContracts(unittest.TestCase):
    def test_http_adapter_wires_archive_endpoints_and_list_opt_in(self) -> None:
        source = _source(HTTP_API)
        archive = _between(
            source,
            "async archiveReport(",
            "async unarchiveReport(",
        )
        self.assertIn("/archive", archive)
        self.assertIn("{ reason }", archive)
        unarchive = _between(
            source, "async unarchiveReport(", "async createReport("
        )
        self.assertIn("/unarchive", unarchive)
        listing = _between(
            source, "async listClientReports(", "async archiveReport("
        )
        self.assertIn("includeArchived", listing)
        self.assertIn("?include_archived=true", listing)

    def test_wire_parser_carries_archived_at_and_archive_result(self) -> None:
        source = _source(WIRE)
        summary = _between(
            source, "export function parseProviderSummary(", "export function parseProviderList("
        )
        self.assertIn("archived_at", summary)
        # absent / null both mean "not archived" — older payloads stay valid
        self.assertIn("archivedRaw !== undefined", summary)
        result = source.split("export function parseArchiveResult(", 1)[1]
        self.assertIn('reqBoolean(row, "archived")', result)

    def test_mock_adapter_mirrors_archive_contract(self) -> None:
        source = _source(MOCK_API)
        self.assertIn("archivedAt: string | null", source)
        self.assertIn("options.includeArchived || r.archivedAt === null", source)
        self.assertIn("async unarchiveReport(", source)

    def test_summary_type_declares_archived_at(self) -> None:
        interface = _between(
            _source(TYPES),
            "export interface ProviderReportSummaryV1 {",
            "}",
        )
        self.assertRegex(interface, r"archived_at: string \| null;")


if __name__ == "__main__":
    unittest.main()
