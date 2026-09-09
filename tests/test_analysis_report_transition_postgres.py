"""Real PostgreSQL regression gate for report transition evidence and lock order."""
from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.features.analysis_reports import repository, service, worker, delivery_repository

CHECKLIST = {"citation_traceable": True, "risks_complete": True, "usage_boundary": True}


class ReportTransitionPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["F1_MATERIAL_ANALYSIS_REPORT_LLM"] = "0"
        cls.stack = PostgresIntegrationStack()
        print("TRANSITION_PROJECT=" + cls.stack.project_name, flush=True)
        try:
            cls.stack.start()
            cls.world = cls.stack.seed_world()
        except BaseException:
            cls.stack.dispose_runtime()
            cls.stack.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.stack.dispose_runtime()
        cls.stack.stop()
        assert cls.stack.cleanup_status == "CLEAN", cls.stack.cleanup_status
        assert cls.stack.dedicated_after == (0, 0, 0)
        assert cls.stack.shared_match == 1
        print("TRANSITION_CLEANUP=CLEAN", flush=True)

    def scope(self):
        t = self.world.provider_a
        return session_scope(role="f1_api", enterprise_id=t.enterprise_id, sub=t.sub)

    async def new_report(self):
        made = await service.create_report(self.world.provider_a, self.world.bound_client_id, uuid.uuid4())
        return uuid.UUID(made["report_id"])

    async def draft(self, report_id, *, next_version=False):
        t = self.world.provider_a
        if next_version:
            async with self.scope() as s:
                report = await repository.lock_report_for_generation(s, t.enterprise_id, report_id)
                sources = await repository.load_eligible_sources(s, t.enterprise_id, self.world.bound_client_id)
                frozen = service._freeze(t.enterprise_id, self.world.bound_client_id, sources)
                generated = await repository.begin_generation(s, report=report, actor_id=self.world.actor_a,
                    actor_sub=t.sub, request_id=uuid.uuid4(), frozen=frozen)
                await s.commit()
        else:
            generated = await service.generate_report(t, self.world.bound_client_id, report_id, uuid.uuid4())
        jid, vid = uuid.UUID(str(generated["job_id"])), uuid.UUID(str(generated["version_id"]))
        claims = await delivery_repository.claim_due_deliveries()
        claim = next(c for c in claims if c.id == delivery_repository.delivery_id_for(jid))
        await worker._process_generation_job(jid, t.enterprise_id, t.sub, vid, claim.id, claim.dispatch_token)
        status = await service.job_status(t, jid)
        self.assertEqual(status["status"], "draft", status)
        return vid

    async def approve(self, vid):
        await service.apply_transition(self.world.provider_a, vid, "submit")
        await service.apply_transition(self.world.provider_a, vid, "approve", checklist=CHECKLIST)

    async def event_count(self, rid, table, action):
        async with self.scope() as s:
            return (await s.execute(text(f"SELECT count(*) FROM f1.{table} WHERE report_id=:r AND action=:a"),
                                    {"r": rid, "a": action})).scalar_one()

    def test_archive_restore_concurrent_idempotency(self):
        async def run():
            rid = await self.new_report()
            archived = await asyncio.gather(*[service.archive_report(self.world.provider_a, rid, reason="same") for _ in range(2)])
            self.assertEqual(sorted(x["already_archived"] for x in archived), [False, True])
            self.assertEqual(await self.event_count(rid, "analysis_report_management_event", "report_archived"), 1)
            restored = await asyncio.gather(*[service.unarchive_report(self.world.provider_a, rid) for _ in range(2)])
            self.assertEqual(sorted(x["already_unarchived"] for x in restored), [False, True])
            self.assertEqual(await self.event_count(rid, "analysis_report_management_event", "report_unarchived"), 1)
            await service.archive_report(self.world.provider_a, rid, reason="again")
            self.assertEqual(await self.event_count(rid, "analysis_report_management_event", "report_archived"), 2)
        asyncio.run(run())

    async def fake_management(self, s, rid, action="report_archived", reason=None):
        await s.execute(text("INSERT INTO f1.analysis_report_management_event "
            "(id,enterprise_id,report_id,actor_user_id,action,reason) VALUES (:i,:e,:r,:u,:a,:reason)"),
            {"i": uuid.uuid4(), "e": self.world.enterprise_a, "r": rid, "u": self.world.actor_a, "a": action, "reason": reason})

    def test_noop_touch_cannot_forge_archive_event(self):
        async def run():
            rid = await self.new_report()
            await service.archive_report(self.world.provider_a, rid, reason="original")
            async with self.scope() as s:
                await s.execute(text("UPDATE f1.analysis_report SET updated_at=statement_timestamp() WHERE id=:r"), {"r": rid})
                with self.assertRaisesRegex(Exception, "REPORT_TRANSITION_EVIDENCE_REQUIRED"):
                    await self.fake_management(s, rid, reason="original")
                await s.rollback()
            self.assertEqual(await self.event_count(rid, "analysis_report_management_event", "report_archived"), 1)
        asyncio.run(run())

    def test_archive_without_event_cannot_commit(self):
        async def run():
            rid = await self.new_report()
            async with self.scope() as s:
                await s.execute(text("UPDATE f1.analysis_report SET archived_at=statement_timestamp(), "
                    "archived_by_user_id=:u,updated_at=statement_timestamp() WHERE id=:r"),
                    {"u": self.world.actor_a, "r": rid})
                with self.assertRaisesRegex(Exception, "REPORT_TRANSITION_EVENT_REQUIRED"):
                    await s.commit()
                await s.rollback()
            async with self.scope() as s:
                self.assertIsNone((await s.execute(text("SELECT archived_at FROM f1.analysis_report WHERE id=:r"), {"r": rid})).scalar())
        asyncio.run(run())

    def test_earlier_transaction_cannot_reuse_later_submit(self):
        async def run():
            rid = await self.new_report()
            vid = await self.draft(rid)
            for table in ("audit", "review"):
                async with self.scope() as early:
                    await early.execute(text("SELECT pg_current_xact_id()"))
                    if table == "audit":
                        await service.apply_transition(self.world.provider_a, vid, "submit")
                    with self.assertRaisesRegex(Exception, "REPORT_TRANSITION_EVIDENCE_REQUIRED"):
                        if table == "audit":
                            await repository.add_audit(early, enterprise_id=self.world.enterprise_a, report_id=rid,
                                version_id=vid, actor_id=self.world.actor_a, action="submit", from_status="draft", to_status="review_pending")
                        else:
                            await repository.insert_review_event(early, enterprise_id=self.world.enterprise_a, report_id=rid,
                                version_id=vid, actor_id=self.world.actor_a, action="submit", checklist={}, comment=None)
                    await early.rollback()
            for table in ("analysis_report_audit_event", "analysis_report_review_event"):
                self.assertEqual(await self.event_count(rid, table, "submit"), 1)
        asyncio.run(run())

    def test_submit_requires_audit_and_review_before_commit(self):
        async def run():
            rid = await self.new_report()
            vid = await self.draft(rid)
            for include_audit in (False, True):
                async with self.scope() as s:
                    if include_audit:
                        await repository.transition_version(s, enterprise_id=self.world.enterprise_a, version_id=vid,
                            from_status="draft", to_status="review_pending", actor_id=self.world.actor_a, action="submit")
                    else:
                        await s.execute(text("UPDATE f1.analysis_report_version SET status='review_pending',updated_at=statement_timestamp() WHERE id=:v"), {"v": vid})
                    with self.assertRaisesRegex(Exception, "REPORT_TRANSITION_REVIEW_REQUIRED" if include_audit else "REPORT_TRANSITION_EVENT_REQUIRED"):
                        await s.commit()
                    await s.rollback()
                async with self.scope() as s:
                    self.assertEqual((await repository.get_version(s, self.world.enterprise_a, vid))["status"], "draft")
        asyncio.run(run())

    def test_same_transition_rejects_duplicate_audit_and_review(self):
        async def run():
            rid = await self.new_report()
            vid = await self.draft(rid)
            for duplicate in ("audit", "review"):
                async with self.scope() as s:
                    await repository.transition_version(s, enterprise_id=self.world.enterprise_a, version_id=vid,
                        from_status="draft", to_status="review_pending", actor_id=self.world.actor_a, action="submit")
                    await repository.insert_review_event(s, enterprise_id=self.world.enterprise_a, report_id=rid,
                        version_id=vid, actor_id=self.world.actor_a, action="submit", checklist={}, comment=None)
                    with self.assertRaisesRegex(Exception, "transition_uq"):
                        if duplicate == "audit":
                            await repository.add_audit(s, enterprise_id=self.world.enterprise_a, report_id=rid,
                                version_id=vid, actor_id=self.world.actor_a, action="submit", from_status="draft", to_status="review_pending")
                        else:
                            await repository.insert_review_event(s, enterprise_id=self.world.enterprise_a, report_id=rid,
                                version_id=vid, actor_id=self.world.actor_a, action="submit", checklist={}, comment=None)
                    await s.rollback()
        asyncio.run(run())

    def test_withdraw_and_replacement_publish_share_report_lock(self):
        async def run():
            rid = await self.new_report()
            v1 = await self.draft(rid)
            await self.approve(v1)
            await service.apply_transition(self.world.provider_a, v1, "publish")
            v2 = await self.draft(rid, next_version=True)
            await self.approve(v2)
            held, attempting = asyncio.Event(), asyncio.Event()
            original = AsyncSession.execute
            trace = []
            async def execute(s, statement, params=None, *args, **kwargs):
                sql = " ".join(str(statement).split())
                name = asyncio.current_task().get_name()
                is_lock = "FROM f1.analysis_report " in sql and "FOR UPDATE" in sql
                if name == "replacement" and is_lock:
                    trace.append("replacement attempts report lock")
                    attempting.set()
                result = await original(s, statement, params, *args, **kwargs)
                if name == "withdrawal" and is_lock:
                    trace.append("withdrawal holds report lock")
                    held.set()
                    await asyncio.wait_for(attempting.wait(), 10)
                return result
            async def replace():
                await asyncio.wait_for(held.wait(), 10)
                return await service.apply_transition(self.world.provider_a, v2, "publish")
            with patch.object(AsyncSession, "execute", execute):
                results = await asyncio.wait_for(asyncio.gather(
                    asyncio.create_task(service.apply_transition(self.world.provider_a, v1, "withdraw"), name="withdrawal"),
                    asyncio.create_task(replace(), name="replacement")), 20)
            self.assertEqual(len(results), 2)
            self.assertEqual(len(trace), 2)
            async with self.scope() as s:
                rows = (await s.execute(text("SELECT version_number,status FROM f1.analysis_report_version WHERE report_id=:r ORDER BY version_number"), {"r": rid})).all()
                self.assertEqual([tuple(row) for row in rows], [(1, "withdrawn"), (2, "published")])
                self.assertTrue((await s.execute(text("SELECT client_visible FROM f1.analysis_report WHERE id=:r"), {"r": rid})).scalar())
        asyncio.run(run())

    def test_archived_draft_cannot_submit_with_valid_audit_and_review(self):
        async def run():
            rid = await self.new_report()
            vid = await self.draft(rid)
            await service.archive_report(self.world.provider_a, rid)
            async with self.scope() as s:
                with self.assertRaisesRegex(Exception, "ANALYSIS_REPORT_ARCHIVED_VERSION_INVALID"):
                    # This was a validly audited SQL bypass before the new
                    # database archive fence. The status write must fail first.
                    await repository.transition_version(s, enterprise_id=self.world.enterprise_a,
                        version_id=vid, from_status="draft", to_status="review_pending",
                        actor_id=self.world.actor_a, action="submit")
                    await repository.insert_review_event(s, enterprise_id=self.world.enterprise_a,
                        report_id=rid, version_id=vid, actor_id=self.world.actor_a,
                        action="submit", checklist={}, comment=None)
                    await s.commit()
                await s.rollback()
            async with self.scope() as s:
                self.assertEqual((await repository.get_version(s, self.world.enterprise_a, vid))["status"], "draft")
            self.assertEqual(await self.event_count(rid, "analysis_report_audit_event", "submit"), 0)
            self.assertEqual(await self.event_count(rid, "analysis_report_review_event", "submit"), 0)
            await service.unarchive_report(self.world.provider_a, rid)
            await self.approve(vid)
            await service.apply_transition(self.world.provider_a, vid, "publish")
            await service.apply_transition(self.world.provider_a, vid, "withdraw")
            async with self.scope() as s:
                self.assertEqual((await repository.get_version(s, self.world.enterprise_a, vid))["status"], "withdrawn")
        asyncio.run(run())

    def test_archived_report_cannot_insert_queued_version(self):
        async def run():
            rid = await self.new_report()
            await service.archive_report(self.world.provider_a, rid)
            async with self.scope() as s:
                report = await repository.lock_report_for_generation(s, self.world.enterprise_a, rid)
                sources = await repository.load_eligible_sources(s, self.world.enterprise_a, self.world.bound_client_id)
                frozen = service._freeze(self.world.enterprise_a, self.world.bound_client_id, sources)
                with self.assertRaisesRegex(Exception, "ANALYSIS_REPORT_ARCHIVED_VERSION_INVALID"):
                    await repository.begin_generation(s, report=report, actor_id=self.world.actor_a,
                        actor_sub=self.world.provider_a.sub, request_id=uuid.uuid4(), frozen=frozen)
                    await s.commit()
                await s.rollback()
            async with self.scope() as s:
                self.assertEqual((await s.execute(text("SELECT count(*) FROM f1.analysis_report_version WHERE report_id=:r"), {"r": rid})).scalar_one(), 0)
        asyncio.run(run())

    def test_archived_failed_version_cannot_redispatch(self):
        async def run():
            rid = await self.new_report()
            generated = await service.generate_report(self.world.provider_a, self.world.bound_client_id, rid, uuid.uuid4())
            jid, vid = uuid.UUID(generated["job_id"]), uuid.UUID(generated["version_id"])
            async with self.scope() as s:
                await repository.fail_queued_generation(s, enterprise_id=self.world.enterprise_a,
                    job_id=jid, version_id=vid, reason="REPORT_QUEUE_DISPATCH_FAILED")
                await s.commit()
            await service.archive_report(self.world.provider_a, rid)
            async with self.scope() as s:
                with self.assertRaisesRegex(Exception, "ANALYSIS_REPORT_ARCHIVED_VERSION_INVALID"):
                    await repository.requeue_failed_generation(s, enterprise_id=self.world.enterprise_a,
                        report_id=rid, job_id=jid, version_id=vid, actor_id=self.world.actor_a,
                        actor_sub=self.world.provider_a.sub, reason="REPORT_QUEUE_DISPATCH_FAILED",
                        audit_action="redispatch")
                    await s.commit()
                await s.rollback()
            async with self.scope() as s:
                self.assertEqual((await repository.get_version(s, self.world.enterprise_a, vid))["status"], "failed")
                self.assertEqual((await repository.get_job(s, self.world.enterprise_a, jid))["status"], "failed")
            self.assertEqual(await self.event_count(rid, "analysis_report_audit_event", "redispatch"), 0)
            await service.unarchive_report(self.world.provider_a, rid)
            async with self.scope() as s:
                self.assertTrue(await repository.requeue_failed_generation(s, enterprise_id=self.world.enterprise_a,
                    report_id=rid, job_id=jid, version_id=vid, actor_id=self.world.actor_a,
                    actor_sub=self.world.provider_a.sub, reason="REPORT_QUEUE_DISPATCH_FAILED",
                    audit_action="redispatch"))
                await s.commit()
            self.assertEqual(await self.event_count(rid, "analysis_report_audit_event", "redispatch"), 1)
        asyncio.run(run())

    def test_raw_submit_racing_archive_cannot_commit_archived_review(self):
        async def run():
            rid = await self.new_report()
            vid = await self.draft(rid)
            held, raw_attempted = asyncio.Event(), asyncio.Event()
            original = AsyncSession.execute
            async def execute(s, statement, params=None, *args, **kwargs):
                sql = " ".join(str(statement).split())
                name = asyncio.current_task().get_name()
                if name == "raw-submit" and sql.startswith("UPDATE f1.analysis_report_version SET"):
                    raw_attempted.set()
                result = await original(s, statement, params, *args, **kwargs)
                if name == "archive-race" and "FROM f1.analysis_report " in sql and "FOR UPDATE" in sql:
                    held.set()
                    await asyncio.wait_for(raw_attempted.wait(), 10)
                return result
            async def raw_submit():
                await asyncio.wait_for(held.wait(), 10)
                async with self.scope() as s:
                    with self.assertRaisesRegex(Exception, "ANALYSIS_REPORT_ARCHIVED_VERSION_INVALID"):
                        await repository.transition_version(s, enterprise_id=self.world.enterprise_a,
                            version_id=vid, from_status="draft", to_status="review_pending",
                            actor_id=self.world.actor_a, action="submit")
                        await repository.insert_review_event(s, enterprise_id=self.world.enterprise_a,
                            report_id=rid, version_id=vid, actor_id=self.world.actor_a,
                            action="submit", checklist={}, comment=None)
                        await s.commit()
                    await s.rollback()
            with patch.object(AsyncSession, "execute", execute):
                await asyncio.wait_for(asyncio.gather(
                    asyncio.create_task(service.archive_report(self.world.provider_a, rid), name="archive-race"),
                    asyncio.create_task(raw_submit(), name="raw-submit")), 20)
            async with self.scope() as s:
                self.assertTrue((await s.execute(text("SELECT archived_at IS NOT NULL FROM f1.analysis_report WHERE id=:r"), {"r": rid})).scalar_one())
                self.assertEqual((await repository.get_version(s, self.world.enterprise_a, vid))["status"], "draft")
            self.assertEqual(await self.event_count(rid, "analysis_report_audit_event", "submit"), 0)
            self.assertEqual(await self.event_count(rid, "analysis_report_review_event", "submit"), 0)
        asyncio.run(run())

    def test_journal_private_and_functions_narrow(self):
        with self.stack._bootstrap() as c:
            for role in ("f1_api", "f1_worker"):
                self.assertFalse(c.execute("SELECT has_table_privilege(%s,'f1.analysis_report_transition','SELECT,INSERT,UPDATE,DELETE')", (role,)).fetchone()[0])
            owners = c.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE pronamespace='f1'::regnamespace AND proname IN ('capture_analysis_report_transition','bind_analysis_report_transition_event','require_analysis_report_transition_event')").fetchall()
            self.assertEqual(owners, [("f1_report_transition_definer",)] * 3)
            self.assertEqual(c.execute("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='f1_report_transition_definer') OR roleid=(SELECT oid FROM pg_roles WHERE rolname='f1_report_transition_definer')").fetchone()[0], 0)
