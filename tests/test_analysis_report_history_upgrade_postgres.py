"""Real business history on f1_0026 survives the official upgrade to f1_0044.

Only the initial test stack target and identity seed differ from the current
harness. Report/workflow/history writes all use the normal API role and real
services. Production harness and migrator configuration are not overridden.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from infra.f1 import local_migrate, local_seed, migrate_f1
from infra.f1.analysis_report_postgres_integration import (
    CLIENT_SUB, DUAL_SUB, ENTERPRISE_C, STRANGER_SUB, PostgresIntegrationStack,
)
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.features.analysis_reports import (
    delivery_repository, health, repository, service, worker,
)
from tests.test_analysis_report_postgres_integration import EXPECTED_RLS_TABLES, _ExplicitSnapshotScorer

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = {"citation_traceable": True, "risks_complete": True, "usage_boundary": True}
EVENT_TABLES = (
    "analysis_report_audit_event", "analysis_report_review_event",
    "analysis_report_management_event", "analysis_report_health_snapshot",
)
HISTORY_TABLES = (
    "analysis_report_client_audience", "analysis_report", "analysis_report_version",
    "analysis_report_section", "analysis_report_citation",
    "analysis_report_generation_job", "analysis_report_generation_delivery",
    *EVENT_TABLES,
)


class HistoryUpgradeStack(PostgresIntegrationStack):
    def _migrate(self) -> None:
        """Use the real closed internal migration target for the old fixture."""
        local_migrate._root_migration_url()
        migrate_f1._migration_dsn()
        engine = create_engine(make_url(migrate_f1._bootstrap_dsn()).set(drivername="postgresql+psycopg"))
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE f0d_migration")
                try:
                    local_migrate._upgrade_f0(connection)
                finally:
                    connection.exec_driver_sql("RESET ROLE")
                migrate_f1.migrate_with_connection(connection, target="f1_0026")
                head = connection.execute(text("SELECT version_num FROM f1.alembic_version")).scalar_one()
                if head != "f1_0026":
                    raise AssertionError("HISTORY_INITIAL_HEAD_INVALID")
        finally:
            engine.dispose()

    def _seed_identities(self) -> None:
        """Minimal test identities for seed_world, gated to the historical head."""
        with self._bootstrap() as connection:
            if connection.execute("SELECT version_num FROM f1.alembic_version").fetchone() != ("f1_0026",):
                raise AssertionError("HISTORY_SEED_HEAD_INVALID")
            for enterprise, label, registration in (
                (local_seed.ENTERPRISE_A, "Local Enterprise A", "LOCAL-A"),
                (local_seed.ENTERPRISE_B, "Local Enterprise B", "LOCAL-B"),
                (ENTERPRISE_C, "Local Enterprise C", "LOCAL-C"),
            ):
                local_seed._ensure_enterprise(connection, enterprise, label, registration)
            for binding in (
                *local_seed.BINDINGS,
                local_seed.Binding("dual-a", DUAL_SUB, "dual@fixture.invalid", local_seed.ENTERPRISE_A, "super_admin"),
                local_seed.Binding("dual-b", DUAL_SUB, "dual@fixture.invalid", local_seed.ENTERPRISE_B, "partner"),
                local_seed.Binding("client-b", CLIENT_SUB, "client@fixture.invalid", local_seed.ENTERPRISE_B, "partner"),
                local_seed.Binding("stranger-c", STRANGER_SUB, "stranger@fixture.invalid", ENTERPRISE_C, "partner"),
            ):
                local_seed._ensure_binding(connection, binding)
            local_seed._ensure_durability_canary(connection)
            connection.commit()

    def upgrade_current(self) -> None:
        """Execute the unmodified dedicated migrator in its normal subprocess."""
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "infra/f1/analysis-reports/migrate.py")],
            cwd=ROOT, env=self.runtime_env(), capture_output=True, timeout=120,
        )
        if result.returncode or result.stdout.decode().strip() != "LOCAL_ANALYSIS_REPORT_MIGRATE_OK":
            # Migration diagnostics contain fixture SQL only, never print the
            # process environment or secret DSNs.
            raise AssertionError("HISTORY_UPGRADE_FAILED:" + result.stderr.decode(errors="replace")[-3500:])


class ReportHistoryUpgradePostgresTests(unittest.TestCase):
    def snapshot(self, stack, *, with_journal=False):
        result = {}
        with stack._bootstrap() as connection:
            for table in (*HISTORY_TABLES, *(("analysis_report_transition",) if with_journal else ())):
                result[table] = [row[0] for row in connection.execute(
                    f"SELECT to_jsonb(record) FROM f1.{table} AS record ORDER BY id"
                ).fetchall()]
        return result

    def digest(self, rows):
        return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def scope(self):
        return session_scope(role="f1_api", enterprise_id=self.world.enterprise_a, sub=self.world.provider_a.sub)

    async def new_report(self):
        result = await service.create_report(self.world.provider_a, self.world.bound_client_id, uuid.uuid4())
        return uuid.UUID(result["report_id"])

    async def draft(self, rid):
        generated = await service.generate_report(self.world.provider_a, self.world.bound_client_id, rid, uuid.uuid4())
        jid, vid = uuid.UUID(generated["job_id"]), uuid.UUID(generated["version_id"])
        claims = await delivery_repository.claim_due_deliveries()
        claim = next(c for c in claims if c.id == delivery_repository.delivery_id_for(jid))
        await worker._process_generation_job(jid, self.world.enterprise_a, self.world.provider_a.sub, vid, claim.id, claim.dispatch_token)
        self.assertEqual((await service.job_status(self.world.provider_a, jid))["status"], "draft")
        return vid

    async def seed_history(self):
        published_rid = await self.new_report()
        published_vid = await self.draft(published_rid)
        await service.apply_transition(self.world.provider_a, published_vid, "submit")
        await service.apply_transition(self.world.provider_a, published_vid, "approve", checklist=CHECKLIST)
        health.set_local_scorer(_ExplicitSnapshotScorer())
        try:
            await service.apply_transition(self.world.provider_a, published_vid, "publish")
        finally:
            health.set_local_scorer(None)
        review_rid = await self.new_report()
        review_vid = await self.draft(review_rid)
        await service.apply_transition(self.world.provider_a, review_vid, "submit")
        returned_rid = await self.new_report()
        returned_vid = await self.draft(returned_rid)
        await service.apply_transition(self.world.provider_a, returned_vid, "submit")
        await service.apply_transition(self.world.provider_a, returned_vid, "return", comment="补齐真实材料后重新生成")
        archived_rid = await self.new_report()
        await service.archive_report(self.world.provider_a, archived_rid, reason="升级前归档")
        await service.unarchive_report(self.world.provider_a, archived_rid)
        await service.archive_report(self.world.provider_a, archived_rid, reason="升级前再次归档")
        return {"published_rid": published_rid, "published_vid": published_vid,
                "review_vid": review_vid, "archived_rid": archived_rid}

    async def exercise_new_contract(self, historical):
        # Existing client reads and historical health remain usable before new
        # changes are added; the old event stream remains append-only.
        published = await service.get_published(self.world.client_b, historical["published_rid"])
        self.assertIsInstance(published, dict)
        await service.unarchive_report(self.world.provider_a, historical["archived_rid"])
        await service.apply_transition(self.world.provider_a, historical["review_vid"], "approve", checklist=CHECKLIST)
        health.set_local_scorer(_ExplicitSnapshotScorer())
        try:
            await service.apply_transition(self.world.provider_a, historical["review_vid"], "publish")
        finally:
            health.set_local_scorer(None)
        rid = await self.new_report()
        async with self.scope() as session:
            await session.execute(text("UPDATE f1.analysis_report SET archived_at=statement_timestamp(), "
                "archived_by_user_id=:u,updated_at=statement_timestamp() WHERE id=:r"),
                {"u": self.world.actor_a, "r": rid})
            with self.assertRaises(DBAPIError) as raised:
                await session.commit()
            self.assertEqual(raised.exception.orig.sqlstate, "P0001")
            self.assertIn("REPORT_TRANSITION_EVENT_REQUIRED", str(raised.exception.orig))
            await session.rollback()
        async with self.scope() as session:
            self.assertIsNone((await session.execute(text("SELECT archived_at FROM f1.analysis_report WHERE id=:r"), {"r": rid})).scalar_one())
            with self.assertRaises(DBAPIError) as raised:
                await repository.add_audit(session, enterprise_id=self.world.enterprise_a,
                    report_id=historical["published_rid"], version_id=historical["published_vid"],
                    actor_id=self.world.actor_a, action="publish", from_status="approved", to_status="published")
            self.assertEqual(raised.exception.orig.sqlstate, "P0001")
            self.assertIn("REPORT_TRANSITION_EVIDENCE_REQUIRED", str(raised.exception.orig))
            await session.rollback()

    def test_historical_rows_preserved_and_new_contract_enforced(self):
        os.environ["F1_MATERIAL_ANALYSIS_REPORT_LLM"] = "0"
        stack = HistoryUpgradeStack()
        print("HISTORY_UPGRADE_PROJECT=" + stack.project_name, flush=True)
        try:
            stack.start()
            self.world = stack.seed_world()
            historical = asyncio.run(self.seed_history())
            before = self.snapshot(stack)
            self.assertEqual(len(before["analysis_report"]), 4)
            self.assertEqual(len(before["analysis_report_health_snapshot"]), 1)
            self.assertEqual(len(before["analysis_report_management_event"]), 3)
            self.assertTrue(all(before[table] for table in HISTORY_TABLES))
            print("HISTORY_0026_COUNTS=" + json.dumps({k: len(v) for k, v in before.items()}, sort_keys=True), flush=True)
            print("HISTORY_0026_SHA256=" + self.digest(before), flush=True)
            stack.upgrade_current()
            upgraded = self.snapshot(stack, with_journal=True)
            self.assertEqual(upgraded.pop("analysis_report_transition"), [])
            for table in EVENT_TABLES:
                for row in upgraded[table]:
                    self.assertIsNone(row.pop("transition_id"))
            for row in upgraded["analysis_report_client_audience"]:
                self.assertIsNone(row.pop("create_request_id"))
            for row in upgraded['analysis_report_citation']:
                for field in ('locator','evidence_revision_id','fragment_id','evidence_body_sha256'):
                    self.assertIsNone(row.pop(field))
            self.assertEqual(upgraded, before)
            print("HISTORY_ALL_ROWS_PRESERVED=PASSED;LEGACY_TRANSITIONS_NULL=PASSED", flush=True)
            with stack._bootstrap() as connection:
                self.assertEqual(connection.execute("SELECT version_num FROM f1.alembic_version").fetchone(), ("f1_0044",))
                # Existing CRM owner A is a provider. B both owns CRM and is a
                # client audience, so keep its ambiguous identity unconfigured.
                # C is only an audience, and becomes a client.
                kinds = dict(connection.execute("SELECT id,business_kind FROM f1.enterprise WHERE id=ANY(%s)", ([self.world.enterprise_a,self.world.enterprise_b,self.world.enterprise_c],)).fetchall())
                self.assertEqual(kinds, {self.world.enterprise_a: "service_provider", self.world.enterprise_b: "unconfigured", self.world.enterprise_c: "client"})
                self.assertEqual(connection.execute("SELECT count(*) FROM pg_class WHERE relnamespace='f1'::regnamespace AND relkind='r' AND relrowsecurity AND relforcerowsecurity AND relname=ANY(%s)", (list(EXPECTED_RLS_TABLES),)).fetchone()[0], len(EXPECTED_RLS_TABLES))
                owners = connection.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace='f1'::regnamespace AND proname IN ('capture_analysis_report_transition','bind_analysis_report_transition_event','require_analysis_report_transition_event')").fetchall()
                self.assertEqual(owners, [("f1_report_transition_definer",)] * 3)
            asyncio.run(self.exercise_new_contract(historical))
            after_new = self.snapshot(stack, with_journal=True)
            self.assertEqual(len(after_new["analysis_report_transition"]), 4)
            for table in EVENT_TABLES:
                old_ids = {row["id"] for row in before[table]}
                old_rows = []
                for row in after_new[table]:
                    if row["id"] in old_ids:
                        preserved = dict(row)
                        self.assertIsNone(preserved.pop("transition_id"))
                        old_rows.append(preserved)
                    else:
                        self.assertIsNotNone(row["transition_id"])
                self.assertEqual(old_rows, before[table])
            print("HISTORY_NEW_EVENT_BINDING_AND_COMMIT_GUARD=PASSED", flush=True)
            stack.upgrade_current()
            self.assertEqual(self.snapshot(stack, with_journal=True), after_new)
            print("HISTORY_OFFICIAL_MIGRATOR_REPLAY_IDEMPOTENT=PASSED", flush=True)
        finally:
            health.set_local_scorer(None)
            stack.dispose_runtime()
            stack.stop()
            self.assertEqual(stack.cleanup_status, "CLEAN")
            self.assertEqual(stack.dedicated_after, (0, 0, 0))
            self.assertEqual(stack.shared_match, 1)
            self.assertFalse(stack.control_dir.exists())
            print("HISTORY_UPGRADE_CLEANUP=CLEAN;DEDICATED_REMAINING=0,0,0;SHARED_UNCHANGED=1", flush=True)
