"""HTTP membership lifecycle against real PostgreSQL, including stale actors."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
from threading import Barrier
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault('F1_KEYCLOAK_ISSUER_URL', 'http://material-rag.invalid/realms/anhuan')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.auth import current_user, memberships_for_sub
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.api.routers.memberships import router
from platform_foundation.f1.api.routers.invitation import router as invites
from platform_foundation.f1.api.routers.analysis_reports import session_router
from platform_foundation.f1.api.routers import client_portal_access

STACK = WORLD = KEY_ENV = None


def setUpModule():
    global STACK,WORLD,KEY_ENV
    STACK=PostgresIntegrationStack()
    print('MEMBERSHIP_PROJECT='+STACK.project_name,flush=True)
    try:
        STACK.start(); WORLD=STACK.seed_world()
        key=STACK.secrets_dir/'member_signing_key';key.write_text(secrets.token_hex(32));key.chmod(0o600)
        KEY_ENV=patch.dict(os.environ,{'F1_INVITE_KEY_FILE':str(key)});KEY_ENV.start()
    except BaseException:
        STACK.dispose_runtime();STACK.stop();raise


def tearDownModule():
    if KEY_ENV: KEY_ENV.stop()
    if STACK:
        STACK.dispose_runtime();STACK.stop()
        if STACK.cleanup_status!='CLEAN' or STACK.dedicated_after!=(0,0,0) or STACK.shared_match!=1:
            raise AssertionError('MEMBERSHIP_CLEANUP_FAILED')
        print('MEMBERSHIP_CLEANUP=CLEAN;SHARED_UNCHANGED=1',flush=True)


def http(sub):
    app=FastAPI();app.include_router(router,prefix='/v1/memberships')
    app.include_router(invites,prefix='/v1/invitations');app.include_router(session_router,prefix='/v1')
    app.include_router(client_portal_access.router,prefix='/v1/clients')
    app.dependency_overrides[current_user]=lambda:{'sub':sub,'roles':['super_admin'],
        'email':sub+'@example.invalid','email_verified':True}
    return TestClient(app)


class MembershipPostgresTests(unittest.TestCase):
    def setUp(self):
        self.eid=uuid.uuid4();self.sub='member-admin-'+uuid.uuid4().hex
        with STACK._bootstrap() as c:
            c.execute("INSERT INTO f1.enterprise(id,name,license_no,business_kind) VALUES(%s,'Member test','TEST','service_provider')",(self.eid,))
        self.admin=self.member(self.sub,'enterprise_admin')
        self.target_sub='member-target-'+uuid.uuid4().hex
        self.target=self.member(self.target_sub,'plant_admin')
        self.client=http(self.sub);self.headers={'X-Enterprise-Id':str(self.eid)}

    def tearDown(self): self.client.close()

    def member(self,sub,role,eid=None):
        eid=eid or self.eid
        with STACK._bootstrap() as c:
            local_seed._ensure_binding(c,local_seed.Binding(sub,sub,sub+'@example.invalid',eid,role))
            return str(c.execute('SELECT eu.id FROM f1.enterprise_user eu JOIN f1.user_profile up ON up.id=eu.user_id WHERE eu.enterprise_id=%s AND up.keycloak_sub=%s',(eid,sub)).fetchone()[0])

    def action(self,action,member=None,request=None,client=None,**body):
        return (client or self.client).post('/v1/memberships/'+(member or self.target)+'/'+action,
            headers=self.headers,json={'request_id':request or str(uuid.uuid4()),**body})

    def read(self): return self.client.get('/v1/memberships',headers=self.headers)

    def status(self):
        response=self.read();self.assertEqual(response.status_code,200,response.text)
        return {m['id']:m for m in response.json()['members']}

    def test_role_revoke_restore_preserve_identity_and_replay_never_reverts_later_state(self):
        original=self.status()[self.target]
        self.assertEqual(self.action('role',role='auditor').status_code,200)
        request=str(uuid.uuid4())
        self.assertEqual(self.action('revoke',request=request).status_code,200)
        self.assertEqual(self.status()[self.target]['status'],'revoked')
        self.assertEqual(asyncio.run(memberships_for_sub(self.target_sub)),[])
        self.assertEqual(self.action('restore').status_code,200)
        self.assertEqual(self.action('revoke',request=request).status_code,200)
        current=self.status()[self.target]
        self.assertEqual((current['user_id'],current['status'],current['role']),(original['user_id'],'active','auditor'))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM f1.audit_log WHERE id=%s',(uuid.UUID(request),)).fetchone()[0],1)

    def test_revoked_A_active_B_same_oidc_identity_and_old_session_has_no_A_rows(self):
        self.member(self.target_sub,'plant_admin',WORLD.enterprise_b)
        self.assertEqual(self.action('revoke').status_code,200)
        with http(self.target_sub) as client:
            self.assertEqual(client.get('/v1/session/access',headers=self.headers).status_code,404)
            self.assertEqual(client.get('/v1/session/access',headers={'X-Enterprise-Id':str(WORLD.enterprise_b)}).status_code,200)
        async def probe():
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.target_sub) as s:
                self.assertFalse((await s.execute(text('SELECT f1.session_authorized(:eid)'),{'eid':self.eid})).scalar_one())
                self.assertEqual((await s.execute(text('SELECT count(*) FROM f1.enterprise_user'))).scalar_one(),0)
        asyncio.run(probe())

    def test_last_admin_revoke_and_demote_fail_and_two_self_revocations_leave_one_admin(self):
        for action,body in [('revoke',{}),('role',{'role':'plant_admin'})]:
            result=self.action(action,self.admin,**body)
            self.assertEqual((result.status_code,result.json()['detail']),(409,'MEMBERSHIP_LAST_ADMIN'))
        self.assertEqual(self.action('role',role='enterprise_admin').status_code,200)
        barrier=Barrier(2)
        def revoke(sub,member):
            with http(sub) as c:
                barrier.wait(timeout=10);return self.action('revoke',member,client=c)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(revoke,self.sub,self.admin);b=pool.submit(revoke,self.target_sub,self.target)
            results=[a.result(timeout=20),b.result(timeout=20)]
        self.assertEqual(sorted(r.status_code for r in results),[200,409],[r.text for r in results])
        self.assertFalse(next(r for r in results if r.status_code==200).json()['can_manage'])
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM f1.enterprise_user WHERE enterprise_id=%s AND role='enterprise_admin' AND revoked_at IS NULL",(self.eid,)).fetchone()[0],1)

    def test_cross_tenant_nonmanagers_and_technical_roles_cannot_administer(self):
        foreign=self.member('foreign-'+uuid.uuid4().hex,'enterprise_admin',WORLD.enterprise_b)
        self.assertEqual(self.action('revoke',foreign).status_code,404)
        tech=self.member('technical-'+uuid.uuid4().hex,'super_admin')
        self.assertEqual(self.action('revoke',tech).json()['detail'],'MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED')
        self.assertEqual(self.action('role',role='super_admin').json()['detail'],'MEMBERSHIP_ROLE_INVALID')
        with http(self.target_sub) as c:
            self.assertEqual(self.action('revoke',self.admin,client=c).status_code,404)
        self.assertEqual(self.action('restore',enterprise_id=str(WORLD.enterprise_b)).status_code,422)

    def test_customer_admin_can_manage_own_members(self):
        with STACK._bootstrap() as c: c.execute("UPDATE f1.enterprise SET business_kind='client' WHERE id=%s",(self.eid,))
        self.assertEqual(self.action('revoke').status_code,200)
        response=self.client.get('/v1/session/access',headers=self.headers)
        self.assertEqual(response.json()['product_role'],'client_user')
        self.assertEqual(response.json()['membership_role'],'enterprise_admin')

    def test_same_request_changed_payload_conflicts(self):
        request=str(uuid.uuid4())
        self.assertEqual(self.action('role',request=request,role='auditor').status_code,200)
        result=self.action('role',request=request,role='enterprise_admin')
        self.assertEqual((result.status_code,result.json()['detail']),(409,'MEMBERSHIP_REQUEST_CONFLICT'))

    def test_invitation_cannot_restore_revoked_membership_or_consume_ledger(self):
        created=self.client.post('/v1/invitations',headers=self.headers,json={'email':self.target_sub+'@example.invalid','role':'auditor'})
        self.assertEqual(created.status_code,201,created.text)
        token=created.json()['token'];self.assertEqual(self.action('revoke').status_code,200)
        with http(self.target_sub) as client:
            response=client.post('/v1/invitations/consume',json={'token':token})
        self.assertEqual((response.status_code,response.json()['detail']),(409,'MEMBERSHIP_INACTIVE'),response.text)
        from platform_foundation.f1.invitation import validate_invite
        jti=validate_invite(token)['jti']
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT consumed_at FROM f1.invite_jti WHERE jti=%s',(jti,)).fetchone()[0],None)
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='invite.consume' AND resource_id=%s",(jti,)).fetchone()[0],0)

    def test_private_writer_role_and_fake_audit_cannot_be_accessed_by_runtime(self):
        async def probe():
            for query in ("SET ROLE f1_membership_definer", "UPDATE f1.enterprise_user SET revoked_at=NULL",
              "INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result) VALUES(gen_random_uuid(),f1.current_enterprise_id(),f1.current_sub(),'membership.revoke','membership','fake','{}')"):
                async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as s:
                    with self.assertRaises(DBAPIError) as error: await s.execute(text(query))
                    self.assertEqual(error.exception.orig.sqlstate,'42501');await s.rollback()
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.target_sub) as s:
                await s.execute(text("SELECT set_config('f1.client_access_target',:eid,true)"),{'eid':str(self.eid)})
                with self.assertRaises(DBAPIError) as error: await s.execute(text('SELECT f1.read_memberships()'))
                self.assertIn('MEMBERSHIP_NOT_FOUND',str(error.exception.orig));await s.rollback()
        asyncio.run(probe())

    def test_audit_failure_rolls_back_membership(self):
        with STACK._bootstrap() as c:
            c.execute("CREATE FUNCTION f1.membership_test_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='membership.revoke' THEN RAISE EXCEPTION 'TEST_MEMBER_AUDIT_FAILED'; END IF; RETURN NEW; END $$")
            c.execute('CREATE TRIGGER membership_test_fail BEFORE INSERT ON f1.audit_log FOR EACH ROW EXECUTE FUNCTION f1.membership_test_fail()')
        try:
            self.assertEqual(self.action('revoke').status_code,503)
            self.assertEqual(self.status()[self.target]['status'],'active')
        finally:
            with STACK._bootstrap() as c:
                c.execute('DROP TRIGGER membership_test_fail ON f1.audit_log');c.execute('DROP FUNCTION f1.membership_test_fail()')

    def report_actor(self):
        from platform_foundation.f1.auth import Tenant
        self.member(self.sub,'enterprise_admin',WORLD.enterprise_a)
        self.target=self.member(self.target_sub,'enterprise_admin',WORLD.enterprise_a)
        self.headers={'X-Enterprise-Id':str(WORLD.enterprise_a)}
        return Tenant(WORLD.enterprise_a,self.target_sub,(),role='enterprise_admin',business_kind='service_provider')

    async def queue_report(self,actor):
        from platform_foundation.f1.features.analysis_reports import service
        made=await service.create_report(actor,WORLD.bound_client_id,uuid.uuid4())
        queued=await service.generate_report(actor,WORLD.bound_client_id,uuid.UUID(made['report_id']),uuid.uuid4())
        return uuid.UUID(made['report_id']),uuid.UUID(queued['job_id']),uuid.UUID(queued['version_id'])

    async def reconcile(self,actor,job):
        from platform_foundation.f1.features.analysis_reports import repository
        async with session_scope(role='f1_api') as s:
            result=await repository.fail_revoked_actor_generation(s,enterprise_id=actor.enterprise_id,job_id=job,provider_sub=actor.sub)
            await s.commit();return result

    def test_queued_revoked_actor_closes_once_preserving_foreign_keys_and_restore_does_not_restart(self):
        actor=self.report_actor();rid,jid,vid=asyncio.run(self.queue_report(actor))
        self.assertEqual(self.action('revoke').status_code,200)
        self.assertTrue(asyncio.run(self.reconcile(actor,jid)))
        self.assertFalse(asyncio.run(self.reconcile(actor,jid)))
        self.assertEqual(self.action('restore').status_code,200)
        self.assertFalse(asyncio.run(self.reconcile(actor,jid)))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT status,error_reason FROM f1.analysis_report_generation_job WHERE id=%s',(jid,)).fetchone(),('failed','REPORT_ACTOR_REVOKED'))
            self.assertEqual(c.execute("SELECT count(*) FROM f1.analysis_report_audit_event WHERE report_id=%s AND action='actor_revoked'",(rid,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM f1.enterprise_user WHERE id=%s',(uuid.UUID(self.target),)).fetchone()[0],1)

    def test_valid_generation_lease_preserved_until_expiry_and_old_actor_cannot_finish(self):
        from platform_foundation.f1.features.analysis_reports import delivery_repository,worker
        actor=self.report_actor();rid,jid,vid=asyncio.run(self.queue_report(actor));lease=uuid.uuid4()
        async def claim():
            claims=await delivery_repository.claim_due_deliveries()
            dispatch=next(c for c in claims if c.id==delivery_repository.delivery_id_for(jid))
            return await worker._claim(jid,actor.enterprise_id,actor.sub,vid,dispatch.id,dispatch.dispatch_token,lease)
        with patch.object(worker,'LEASE_SECONDS',30):
            self.assertIsNotNone(asyncio.run(claim()))
        self.assertEqual(self.action('revoke').status_code,200)
        self.assertFalse(asyncio.run(self.reconcile(actor,jid)))
        self.assertFalse(asyncio.run(worker._fail_claim(job_id=jid,enterprise_id=actor.enterprise_id,
            provider_sub=actor.sub,version_id=vid,lease_token=lease,reason='REPORT_SOURCE_EVIDENCE_INVALID')))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT status,lease_token FROM f1.analysis_report_generation_job WHERE id=%s',(jid,)).fetchone(),('generating',lease))
        import time
        time.sleep(30.1)  # Real expiry: no trigger bypass or fabricated job state.
        self.assertTrue(asyncio.run(self.reconcile(actor,jid)))
        self.assertFalse(asyncio.run(self.reconcile(actor,jid)))

    def test_revoke_waits_for_generated_result_transaction_to_commit(self):
        from concurrent.futures import TimeoutError
        from threading import Event
        from platform_foundation.f1.features.analysis_reports import delivery_repository,repository,worker
        actor=self.report_actor();rid,jid,vid=asyncio.run(self.queue_report(actor))
        reached=Event();release=Event();original=repository.persist_generated
        async def pending_commit(*args,**kwargs):
            result=await original(*args,**kwargs)
            self.assertTrue(result);reached.set()
            if not await asyncio.to_thread(release.wait,10): raise AssertionError('COMMIT_BARRIER_TIMEOUT')
            return result
        async def generate():
            claims=await delivery_repository.claim_due_deliveries()
            dispatch=next(c for c in claims if c.id==delivery_repository.delivery_id_for(jid))
            await worker._process_generation_job(jid,actor.enterprise_id,actor.sub,vid,dispatch.id,dispatch.dispatch_token)
        with patch.object(repository,'persist_generated',side_effect=pending_commit), patch.dict(os.environ,{'F1_MATERIAL_ANALYSIS_REPORT_LLM':'0'}):
            with ThreadPoolExecutor(max_workers=2) as pool:
                producing=pool.submit(lambda:asyncio.run(generate()))
                revoking=None;blocked=False
                try:
                    self.assertTrue(reached.wait(10),'GENERATION_BARRIER_NOT_REACHED')
                    revoking=pool.submit(lambda:self.action('revoke'))
                    try: revoking.result(timeout=0.5)
                    except TimeoutError: blocked=True
                finally: release.set()
                producing.result(timeout=15)
                response=revoking.result(timeout=15)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(blocked,'Revoke returned before the old actor committed a generated draft')
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT status FROM f1.analysis_report_version WHERE id=%s',(vid,)).fetchone()[0],'draft')

    def test_revoke_waits_for_portal_command_commit(self):
        from contextlib import asynccontextmanager
        from concurrent.futures import TimeoutError
        from threading import Event
        actor=self.report_actor();client_id=uuid.uuid4()
        with STACK._bootstrap() as c:
            profile=c.execute('SELECT id FROM f1.user_profile WHERE keycloak_sub=%s',(actor.sub,)).fetchone()[0]
            c.execute("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id) VALUES(%s,%s,'Portal member race','active',%s)",(client_id,actor.enterprise_id,profile))
        reached=Event();release=Event();original_scope=client_portal_access.session_scope
        @asynccontextmanager
        async def pending_commit(**kwargs):
            async with original_scope(**kwargs) as s:
                original_commit=s.commit
                async def commit():
                    reached.set()
                    if not await asyncio.to_thread(release.wait,10): raise AssertionError('PORTAL_BARRIER_TIMEOUT')
                    await original_commit()
                s.commit=commit
                yield s
        def opening():
            with http(actor.sub) as client:
                return client.post(f'/v1/clients/{client_id}/portal-access',headers=self.headers,
                    json={'request_id':str(uuid.uuid4()),'license_no':'TEST'})
        with patch.object(client_portal_access,'session_scope',pending_commit):
            with ThreadPoolExecutor(max_workers=2) as pool:
                opened=pool.submit(opening);revoking=None;blocked=False
                try:
                    self.assertTrue(reached.wait(10),'PORTAL_BARRIER_NOT_REACHED')
                    revoking=pool.submit(lambda:self.action('revoke'))
                    try: revoking.result(timeout=.5)
                    except TimeoutError: blocked=True
                finally: release.set()
                response=opened.result(timeout=15);revoked=revoking.result(timeout=15)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(revoked.status_code,200,revoked.text)
        self.assertTrue(blocked,'Revoke returned before an old administrator committed portal provisioning')

    def test_revoke_during_final_update_statement_rolls_back_all_generated_rows(self):
        import time
        from platform_foundation.f1.features.analysis_reports import delivery_repository,worker
        actor=self.report_actor();rid,jid,vid=asyncio.run(self.queue_report(actor))
        key=secrets.randbelow(2_000_000_000)+1
        async def generate():
            claims=await delivery_repository.claim_due_deliveries()
            dispatch=next(c for c in claims if c.id==delivery_repository.delivery_id_for(jid))
            await worker._process_generation_job(jid,actor.enterprise_id,actor.sub,vid,dispatch.id,dispatch.dispatch_token)
        with STACK._bootstrap() as c:
            # Barrier inside the actual final UPDATE, after its RLS statement
            # snapshot but before the new member fence. No fabricated outputs.
            c.execute(f"CREATE FUNCTION f1.membership_result_barrier() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.id='{vid}'::uuid AND OLD.status='generating' AND NEW.status='draft' THEN PERFORM pg_advisory_xact_lock({key}); END IF; RETURN NEW; END $$")
            c.execute('CREATE TRIGGER a_membership_result_barrier BEFORE UPDATE OF status ON f1.analysis_report_version FOR EACH ROW EXECUTE FUNCTION f1.membership_result_barrier()')
            c.commit();c.execute('SELECT pg_advisory_lock(%s)',(key,))
            try:
                with patch.dict(os.environ,{'F1_MATERIAL_ANALYSIS_REPORT_LLM':'0'}), ThreadPoolExecutor(max_workers=1) as pool:
                    producing=pool.submit(lambda:asyncio.run(generate()))
                    try:
                        deadline=time.monotonic()+10
                        while not c.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=%s AND NOT granted)",(key,)).fetchone()[0]:
                            if producing.done(): producing.result()
                            self.assertLess(time.monotonic(),deadline,'RESULT_BARRIER_NOT_REACHED');time.sleep(.02)
                        revoked=self.action('revoke');self.assertEqual(revoked.status_code,200,revoked.text)
                    finally: c.execute('SELECT pg_advisory_unlock(%s)',(key,))
                    with self.assertRaisesRegex(RuntimeError,'REPORT_LEASE_STALE'): producing.result(timeout=15)
                self.assertEqual(c.execute('SELECT status FROM f1.analysis_report_version WHERE id=%s',(vid,)).fetchone()[0],'generating')
                self.assertEqual(c.execute('SELECT status FROM f1.analysis_report_generation_job WHERE id=%s',(jid,)).fetchone()[0],'generating')
                for table in ('analysis_report_section','analysis_report_citation'):
                    self.assertEqual(c.execute(f'SELECT count(*) FROM f1.{table} WHERE version_id=%s',(vid,)).fetchone()[0],0)
            finally:
                c.execute('SELECT pg_advisory_unlock(%s)',(key,))
                c.execute('DROP TRIGGER a_membership_result_barrier ON f1.analysis_report_version');c.execute('DROP FUNCTION f1.membership_result_barrier()')

    def test_submit_audit_foreign_key_already_serializes_with_revocation(self):
        from concurrent.futures import TimeoutError
        from threading import Event
        from platform_foundation.f1.features.analysis_reports import delivery_repository,repository,service,worker
        actor=self.report_actor();rid,jid,vid=asyncio.run(self.queue_report(actor))
        async def draft():
            claims=await delivery_repository.claim_due_deliveries()
            dispatch=next(c for c in claims if c.id==delivery_repository.delivery_id_for(jid))
            await worker._process_generation_job(jid,actor.enterprise_id,actor.sub,vid,dispatch.id,dispatch.dispatch_token)
        with patch.dict(os.environ,{'F1_MATERIAL_ANALYSIS_REPORT_LLM':'0'}): asyncio.run(draft())
        reached=Event();release=Event();original=repository.transition_version
        async def pending_commit(*args,**kwargs):
            result=await original(*args,**kwargs);self.assertTrue(result);reached.set()
            if not await asyncio.to_thread(release.wait,10): raise AssertionError('SUBMIT_BARRIER_TIMEOUT')
            return result
        with patch.object(repository,'transition_version',side_effect=pending_commit),ThreadPoolExecutor(max_workers=2) as pool:
            submitted=pool.submit(lambda:asyncio.run(service.apply_transition(actor,vid,'submit')))
            revoked=None;blocked=False
            try:
                self.assertTrue(reached.wait(10),'SUBMIT_BARRIER_NOT_REACHED')
                revoked=pool.submit(lambda:self.action('revoke'))
                try: revoked.result(timeout=.5)
                except TimeoutError: blocked=True
            finally: release.set()
            submitted.result(timeout=15);self.assertEqual(revoked.result(timeout=15).status_code,200)
        self.assertTrue(blocked,'Revoke crossed a real transition and audit actor FK lock')
