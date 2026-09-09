"""Actual HTTP commands, private writer RLS, invitations and rollback.

Only already verified OIDC claims are injected. Every database is disposable.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.auth import current_user, memberships_for_sub
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.api.routers.client_portal_access import router
from platform_foundation.f1.api.routers.invitation import router as invitations
from platform_foundation.f1.api.routers.analysis_reports import session_router

STACK = WORLD = KEY_ENV = None


def setUpModule():
    global STACK,WORLD,KEY_ENV
    STACK=PostgresIntegrationStack()
    print("CLIENT_PORTAL_PROJECT="+STACK.project_name,flush=True)
    try:
        STACK.start(); WORLD=STACK.seed_world()
        key=STACK.secrets_dir/'portal_signing_key';key.write_text(secrets.token_hex(32));key.chmod(0o600)
        KEY_ENV=patch.dict(os.environ,{"F1_INVITE_KEY_FILE":str(key)});KEY_ENV.start()
    except BaseException:
        STACK.dispose_runtime();STACK.stop();raise


def tearDownModule():
    if KEY_ENV: KEY_ENV.stop()
    if STACK:
        STACK.dispose_runtime();STACK.stop()
        if STACK.cleanup_status!='CLEAN' or STACK.dedicated_after!=(0,0,0) or STACK.shared_match!=1:
            raise AssertionError('CLIENT_PORTAL_CLEANUP_FAILED')
        print('CLIENT_PORTAL_CLEANUP=CLEAN;SHARED_UNCHANGED=1',flush=True)


def http(claims):
    app=FastAPI();app.include_router(router,prefix='/v1/clients')
    app.include_router(invitations,prefix='/v1/invitations');app.include_router(session_router,prefix='/v1')
    app.dependency_overrides[current_user]=lambda:claims
    return TestClient(app)


class ClientPortalPostgresTests(unittest.TestCase):
    def setUp(self):
        self.sub='portal-manager-'+uuid.uuid4().hex
        self.claims={'sub':self.sub,'roles':[],'email':self.sub+'@example.invalid','email_verified':True}
        self.client_id=uuid.uuid4();self.request_id=str(uuid.uuid4())
        with STACK._bootstrap() as connection:
            local_seed._ensure_binding(connection,local_seed.Binding(self.sub,self.sub,self.claims['email'],WORLD.enterprise_a,'enterprise_admin'))
            actor=connection.execute('SELECT id FROM f1.user_profile WHERE keycloak_sub=%s',(self.sub,)).fetchone()[0]
            connection.execute("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id) VALUES(%s,%s,'Portal test client','active',%s)",(self.client_id,WORLD.enterprise_a,actor))
        self.client=http(self.claims);self.headers={'X-Enterprise-Id':str(WORLD.enterprise_a)}
        self.base=f'/v1/clients/{self.client_id}'

    def tearDown(self): self.client.close()

    def open(self):
        return self.client.post(self.base+'/portal-access',headers=self.headers,json={'request_id':self.request_id,'license_no':'TEST-NONPRODUCTION-REGISTRATION'})

    def action(self,action):
        return self.client.post(self.base+'/portal-access/'+action,headers=self.headers,json={'request_id':str(uuid.uuid4())})

    def issue(self,email='owner@example.invalid',request=None):
        return self.client.post(self.base+'/portal-invitations',headers=self.headers,json={'request_id':request or str(uuid.uuid4()),'email':email})

    def test_open_creates_distinct_customer_and_scope_and_replays_once(self):
        first=self.open();self.assertEqual(first.status_code,200,first.text)
        target=uuid.UUID(first.json()['customer_enterprise_id']);self.assertNotIn(target,(WORLD.enterprise_a,self.client_id))
        self.assertEqual(self.open().json(),first.json())
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT business_kind FROM f1.enterprise WHERE id=%s',(target,)).fetchone()[0],'client')
            self.assertEqual(c.execute('SELECT count(*) FROM f1.enterprise_user WHERE enterprise_id=%s',(target,)).fetchone()[0],0)
            self.assertEqual(c.execute("SELECT count(*) FROM f1.material_knowledge_scope WHERE enterprise_id=%s AND client_account_id=%s AND scope_kind='client'",(WORLD.enterprise_a,self.client_id)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='client.portal.open' AND resource_id=%s",(str(self.client_id),)).fetchone()[0],1)
        self.assertEqual([m['enterprise_id'] for m in asyncio.run(memberships_for_sub(self.sub))],[str(WORLD.enterprise_a)])

    def test_parallel_open_retries_return_one_binding(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.open(),range(2)))
        self.assertEqual([r.status_code for r in results],[200,200],[r.text for r in results])
        self.assertEqual(results[0].json(),results[1].json())

    def test_bound_invitation_replays_and_joins_only_customer_as_owner(self):
        target=self.open().json()['customer_enterprise_id'];request=str(uuid.uuid4())
        first=self.issue(request=request);self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.issue(request=request).json(),first.json())
        conflict=self.issue(email='different@example.invalid',request=request)
        self.assertEqual(conflict.status_code,409)
        claims={'sub':'new-owner-'+uuid.uuid4().hex,'roles':['super_admin'],'email':'owner@example.invalid','email_verified':True}
        with http(claims) as client:
            result=client.post('/v1/invitations/consume',json={'token':first.json()['token']})
            self.assertEqual(result.status_code,200,result.text)
            session=client.get('/v1/session/access',headers={'X-Enterprise-Id':target})
            self.assertEqual(session.json()['product_role'],'client_user')
        memberships=asyncio.run(memberships_for_sub(claims['sub']))
        self.assertEqual([(m['enterprise_id'],m['role']) for m in memberships],[(target,'enterprise_admin')])
        status=self.client.get(self.base+'/portal-access',headers=self.headers).json()
        self.assertEqual(status['members'][0]['email'],'owner@example.invalid')
        self.assertEqual([i['status'] for i in status['invitations']],['accepted'])

    def test_revoke_blocks_pending_invitation_and_restore_does_not_resurrect_it(self):
        target=self.open().json()['customer_enterprise_id'];invite=self.issue()
        self.assertEqual(invite.status_code,200,invite.text)
        self.assertEqual(self.action('revoke').json()['status'],'revoked')
        # Replay of the original successful request reports current status
        # without restoring it; a new open must require explicit restoration.
        self.assertEqual(self.open().json()['status'],'revoked')
        self.request_id=str(uuid.uuid4())
        self.assertEqual(self.open().status_code,409)
        self.assertEqual(self.issue().status_code,409)
        claims={'sub':'revoked-owner-'+uuid.uuid4().hex,'roles':[],'email':'owner@example.invalid','email_verified':True}
        with http(claims) as client:
            for state in ('revoked','restored'):
                if state=='restored': self.assertEqual(self.action('restore').json()['customer_enterprise_id'],target)
                result=client.post('/v1/invitations/consume',json={'token':invite.json()['token']})
                self.assertEqual(result.status_code,409,result.text);self.assertEqual(result.json()['detail'],'INVITE_REVOKED')
            fresh=self.issue();self.assertEqual(fresh.status_code,200,fresh.text)
            self.assertEqual(client.post('/v1/invitations/consume',json={'token':fresh.json()['token']}).status_code,200)

    def test_other_clients_and_nonmanager_roles_are_not_found(self):
        foreign=self.client.get(f'/v1/clients/{WORLD.foreign_client_id}/portal-access',headers=self.headers)
        self.assertEqual(foreign.status_code,404)
        for role in ('super_admin','plant_admin','auditor','partner'):
            with STACK._bootstrap() as c:
                c.execute('UPDATE f1.enterprise_user SET role=%s WHERE enterprise_id=%s AND user_id=(SELECT id FROM f1.user_profile WHERE keycloak_sub=%s)',(role,WORLD.enterprise_a,self.sub))
            self.assertEqual(self.open().status_code,404,role)

    def test_request_cannot_supply_arbitrary_customer_tenant_or_role(self):
        body={'request_id':self.request_id,'license_no':'TEST','customer_enterprise_id':str(WORLD.enterprise_b)}
        self.assertEqual(self.client.post(self.base+'/portal-access',json=body,headers=self.headers).status_code,422)
        self.open()
        self.assertEqual(self.client.post(self.base+'/portal-invitations',headers=self.headers,json={'request_id':str(uuid.uuid4()),'email':'a@example.invalid','role':'super_admin'}).status_code,422)

    def test_old_revoke_request_does_not_undo_later_restore(self):
        self.open();request={'request_id':str(uuid.uuid4())}
        url=self.base+'/portal-access/revoke'
        self.assertEqual(self.client.post(url,headers=self.headers,json=request).json()['status'],'revoked')
        self.assertEqual(self.action('restore').json()['status'],'active')
        self.assertEqual(self.client.post(url,headers=self.headers,json=request).json()['status'],'active')
        changed={'request_id':self.request_id,'license_no':'DIFFERENT-REGISTRATION'}
        self.assertEqual(self.client.post(self.base+'/portal-access',headers=self.headers,json=changed).status_code,409)

    def test_revoke_and_consume_serialize_without_partial_membership_or_deadlock(self):
        self.open();invite=self.issue();self.assertEqual(invite.status_code,200,invite.text)
        claims={'sub':'racing-owner-'+uuid.uuid4().hex,'roles':[],'email':'owner@example.invalid','email_verified':True}
        from threading import Barrier
        barrier=Barrier(2)
        def consume():
            with http(claims) as client:
                barrier.wait(timeout=10)
                return client.post('/v1/invitations/consume',json={'token':invite.json()['token']})
        def revoke():
            barrier.wait(timeout=10)
            return self.action('revoke')
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(consume);revoking=pool.submit(revoke)
            accepted=pending.result(timeout=20);revoked=revoking.result(timeout=20)
        self.assertEqual(revoked.status_code,200,revoked.text)
        self.assertEqual(revoked.json()['status'],'revoked')
        self.assertIn(accepted.status_code,(200,409),accepted.text)
        joined=accepted.status_code==200
        self.assertEqual(len(asyncio.run(memberships_for_sub(claims['sub']))),int(joined))
        from platform_foundation.f1.invitation import validate_invite
        jti=validate_invite(invite.json()['token'])['jti']
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT consumed_at IS NOT NULL,revoked_at IS NOT NULL FROM f1.invite_jti WHERE jti=%s',(jti,)).fetchone(),(joined,not joined))
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='invite.consume' AND resource_id=%s",(jti,)).fetchone()[0],int(joined))

    def test_runtime_cannot_directly_create_customer_or_binding(self):
        async def probe():
            async with session_scope(role='f1_api',enterprise_id=WORLD.enterprise_a,sub=self.sub) as session:
                with self.assertRaises(DBAPIError) as raised:
                    await session.execute(text("INSERT INTO f1.enterprise(id,name,license_no,business_kind) VALUES(:id,'Bad','Bad','client')"),{'id':uuid.uuid4()})
                self.assertEqual(raised.exception.orig.sqlstate,'42501');await session.rollback()
        asyncio.run(probe())

    def test_audit_failure_rolls_back_enterprise_binding_and_scope(self):
        with STACK._bootstrap() as c:
            before=c.execute('SELECT count(*) FROM f1.enterprise').fetchone()[0]
            c.execute("CREATE FUNCTION f1.portal_test_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='client.portal.open' THEN RAISE EXCEPTION 'TEST_PORTAL_AUDIT_FAILED'; END IF; RETURN NEW; END $$")
            c.execute('CREATE TRIGGER portal_test_audit_failure BEFORE INSERT ON f1.audit_log FOR EACH ROW EXECUTE FUNCTION f1.portal_test_audit_failure()')
        try:
            self.assertEqual(self.open().status_code,503)
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM f1.enterprise').fetchone()[0],before)
                self.assertEqual(c.execute('SELECT count(*) FROM f1.analysis_report_client_audience WHERE client_account_id=%s',(self.client_id,)).fetchone()[0],0)
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_knowledge_scope WHERE client_account_id=%s',(self.client_id,)).fetchone()[0],0)
        finally:
            with STACK._bootstrap() as c:
                c.execute('DROP TRIGGER portal_test_audit_failure ON f1.audit_log');c.execute('DROP FUNCTION f1.portal_test_audit_failure()')
