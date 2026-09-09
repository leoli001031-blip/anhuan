"""Real HTTP creation, replay, DB role fence and immutable creation history."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import psycopg
import unittest
import uuid
from unittest.mock import patch
os.environ.setdefault('F1_KEYCLOAK_ISSUER_URL','http://material-rag.invalid/realms/anhuan')
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.auth import current_user, Tenant
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.api.routers.p4_views_reports import router
from platform_foundation.f1.features.p4 import crm

STACK=WORLD=None

def setUpModule():
    global STACK,WORLD
    STACK=PostgresIntegrationStack()
    print('CRM_CREATION_PROJECT='+STACK.project_name,flush=True)
    try:
        STACK.start();WORLD=STACK.seed_world()
    except BaseException:
        STACK.dispose_runtime();STACK.stop();raise

def tearDownModule():
    if STACK:
        STACK.dispose_runtime();STACK.stop()
        if STACK.cleanup_status!='CLEAN' or STACK.dedicated_after!=(0,0,0) or STACK.shared_match!=1:
            raise AssertionError('CRM_CREATION_CLEANUP_FAILED')
        print('CRM_CREATION_CLEANUP=CLEAN;SHARED_UNCHANGED=1',flush=True)

class CrmCreationPostgresTests(unittest.TestCase):
    def setUp(self):
        self.sub='crm-create-'+uuid.uuid4().hex
        self.actor=self.bind(self.sub,WORLD.enterprise_a,'enterprise_admin')
        app=FastAPI();app.include_router(router,prefix='/v1/views-reports')
        app.dependency_overrides[current_user]=lambda:{'sub':self.sub,'roles':['super_admin']}
        self.client=TestClient(app);self.headers={'X-Enterprise-Id':str(WORLD.enterprise_a)}
        self.body={'request_id':str(uuid.uuid4()),'display_name':'  Creation test  ','stage':'lead'}
    def tearDown(self):self.client.close()
    def bind(self,sub,enterprise,role):
        with STACK._bootstrap() as c:
            local_seed._ensure_binding(c,local_seed.Binding(name=sub,sub=sub,email=sub+'@example.invalid',enterprise_id=enterprise,role=role))
            return c.execute('SELECT id FROM f1.user_profile WHERE keycloak_sub=%s',(sub,)).fetchone()[0]
    def post(self,body=None):
        return self.client.post('/v1/views-reports/crm/accounts',headers=self.headers,json=body if body is not None else self.body)
    def count(self):
        with STACK._bootstrap() as c:
            return c.execute('SELECT count(*) FROM f1.crm_account WHERE enterprise_id=%s AND create_request_id=%s',(WORLD.enterprise_a,self.body['request_id'])).fetchone()[0]
    def test_create_and_parallel_retry_leave_one_client_and_audit(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies=list(pool.map(lambda _:self.post(),range(2)))
        self.assertEqual([r.status_code for r in replies],[201,201],[r.text for r in replies])
        self.assertEqual(replies[0].json(),replies[1].json());row=replies[0].json()
        self.assertEqual(row['display_name'],'Creation test');self.assertEqual(row['enterprise_id'],str(WORLD.enterprise_a))
        self.assertNotIn('create_request_payload',row);self.assertEqual(self.count(),1)
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='crm.account.created' AND resource_id=%s",(row['id'],)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM f1.analysis_report_client_audience WHERE client_account_id=%s',(row['id'],)).fetchone()[0],0)
    def test_changed_payload_and_actor_cannot_reuse_request(self):
        self.assertEqual(self.post().status_code,201)
        for key,value in [('display_name','Different'),('stage','active'),('industry_note','Changed')]:
            response=self.post({**self.body,key:value});self.assertEqual(response.status_code,409,response.text)
            self.assertEqual(response.json()['detail'],'CRM_ACCOUNT_REQUEST_CONFLICT')
        self.sub='other-manager-'+uuid.uuid4().hex;self.bind(self.sub,WORLD.enterprise_a,'enterprise_admin')
        self.assertEqual(self.post().status_code,409);self.assertEqual(self.count(),1)
    def test_replay_after_edit_returns_current_customer_without_restoring_old_values(self):
        created=self.post().json()
        edited=self.client.patch('/v1/views-reports/crm/accounts/'+created['id'],headers=self.headers,json={'display_name':'Edited customer','stage':'active'})
        self.assertEqual(edited.status_code,200,edited.text)
        replay=self.post();self.assertEqual(replay.status_code,201,replay.text)
        self.assertEqual(replay.json()['display_name'],'Edited customer');self.assertEqual(replay.json()['stage'],'active')
        with STACK._bootstrap() as c:
            stored=c.execute('SELECT create_request_payload FROM f1.crm_account WHERE id=%s',(created['id'],)).fetchone()[0]
            self.assertEqual(stored['display_name'],'Creation test');self.assertEqual(stored['stage'],'lead')
    def test_missing_request_blank_name_and_other_roles_fail_without_writes(self):
        self.assertEqual(self.post({'display_name':'No key'}).status_code,422)
        self.assertEqual(self.post({**self.body,'display_name':'   '}).status_code,422)
        for role in ('super_admin','plant_admin','auditor','partner'):
            with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET role=%s WHERE user_id=%s AND enterprise_id=%s',(role,self.actor,WORLD.enterprise_a))
            response=self.post();self.assertEqual(response.status_code,403,response.text)
        self.assertEqual(self.count(),0)
    def test_client_admin_and_unconfigured_organization_cannot_create(self):
        for kind in ('client','unconfigured'):
            with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise SET business_kind=%s WHERE id=%s',(kind,WORLD.enterprise_a))
            try:self.assertEqual(self.post().status_code,403)
            finally:
                with STACK._bootstrap() as c:c.execute("UPDATE f1.enterprise SET business_kind='service_provider' WHERE id=%s",(WORLD.enterprise_a,))
        self.assertEqual(self.count(),0)
    def test_stale_tenant_and_replay_after_revocation_recheck_authority(self):
        self.assertEqual(self.post().status_code,201)
        tenant=Tenant(enterprise_id=WORLD.enterprise_a,sub=self.sub,roles=('super_admin',),role='enterprise_admin',business_kind='service_provider')
        with STACK._bootstrap() as c:c.execute("UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE user_id=%s AND enterprise_id=%s",(self.actor,WORLD.enterprise_a))
        async def replay():
            return await crm.create_account(tenant,request_id=uuid.UUID(self.body['request_id']),display_name=self.body['display_name'],stage='lead',owner_user_id=None,industry_note=None,region_note=None,next_follow_up_at=None)
        with self.assertRaises(Exception) as raised:asyncio.run(replay())
        self.assertEqual(getattr(raised.exception,'status_code',None),403)
        self.assertEqual(self.count(),1)
    def test_low_level_insert_rejects_technical_admin_and_request_history_cannot_change(self):
        created=self.post().json()
        async def change_history():
            async with session_scope(role='f1_api',enterprise_id=WORLD.enterprise_a,sub=self.sub) as session:
                await session.execute(text('UPDATE f1.crm_account SET create_request_id=NULL,create_request_payload=NULL WHERE id=:id'),{'id':uuid.UUID(created['id'])})
                await session.commit()
        with self.assertRaises(DBAPIError) as raised:asyncio.run(change_history())
        self.assertIn('CRM_CREATION_REQUEST_IMMUTABLE',str(raised.exception))
        with STACK._bootstrap() as c:c.execute("UPDATE f1.enterprise_user SET role='super_admin' WHERE user_id=%s AND enterprise_id=%s",(self.actor,WORLD.enterprise_a))
        async def direct_insert():
            async with session_scope(role='f1_api',enterprise_id=WORLD.enterprise_a,sub=self.sub) as session:
                await session.execute(text("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id,create_request_id,create_request_payload) VALUES(:id,:eid,'Bypass','lead',:actor,:request,'{}')"),{'id':uuid.uuid4(),'eid':WORLD.enterprise_a,'actor':self.actor,'request':uuid.uuid4()})
                await session.commit()
        with self.assertRaises(DBAPIError) as raised:asyncio.run(direct_insert())
        self.assertIn('CRM_MANAGER_REQUIRED',str(raised.exception))
    def test_audit_failure_rolls_back_customer_and_retry_key(self):
        async def failed(*args,**kwargs):raise RuntimeError('CRM_AUDIT_FAILURE_PROBE')
        with patch.object(crm,'add_event',failed):
            with self.assertRaisesRegex(RuntimeError,'CRM_AUDIT_FAILURE_PROBE'):self.post()
        self.assertEqual(self.count(),0);self.assertEqual(self.post().status_code,201);self.assertEqual(self.count(),1)

    def test_creation_holds_membership_lock_until_audit_and_commit(self):
        reached=threading.Event();release=threading.Event();original=crm.add_event
        async def held(*args,**kwargs):
            await original(*args,**kwargs);reached.set()
            if not release.wait(10):raise AssertionError('CRM_CREATE_BARRIER_TIMEOUT')
        with patch.object(crm,'add_event',held), ThreadPoolExecutor(max_workers=1) as pool:
            creation=pool.submit(self.post)
            try:
                self.assertTrue(reached.wait(10))
                with STACK._bootstrap() as c:
                    c.execute("SET LOCAL lock_timeout='150ms'")
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        c.execute("UPDATE f1.enterprise_user SET role='auditor' WHERE enterprise_id=%s AND user_id=%s",(WORLD.enterprise_a,self.actor))
                    c.rollback()
            finally:release.set()
            self.assertEqual(creation.result(timeout=10).status_code,201)
        self.assertEqual(self.count(),1)

    def test_same_request_uuid_is_scoped_to_enterprise(self):
        first=self.post();self.assertEqual(first.status_code,201,first.text)
        self.bind(self.sub,WORLD.enterprise_b,'enterprise_admin')
        with STACK._bootstrap() as c:
            original=c.execute('SELECT business_kind FROM f1.enterprise WHERE id=%s',(WORLD.enterprise_b,)).fetchone()[0]
            c.execute("UPDATE f1.enterprise SET business_kind='service_provider' WHERE id=%s",(WORLD.enterprise_b,))
        try:
            self.headers={'X-Enterprise-Id':str(WORLD.enterprise_b)}
            second=self.post();self.assertEqual(second.status_code,201,second.text)
            self.assertEqual(second.json()['enterprise_id'],str(WORLD.enterprise_b))
            self.assertNotEqual(first.json()['id'],second.json()['id'])
            self.assertEqual(self.post().json()['id'],second.json()['id'])
            hidden=self.client.get('/v1/views-reports/crm/accounts/'+first.json()['id'],headers=self.headers)
            self.assertEqual(hidden.status_code,404,hidden.text)
        finally:
            with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise SET business_kind=%s WHERE id=%s',(original,WORLD.enterprise_b))

    def test_datetime_offsets_normalize_to_same_instant_and_naive_is_rejected(self):
        first=self.post({**self.body,'next_follow_up_at':'2026-10-01T10:00:00+08:00'})
        self.assertEqual(first.status_code,201,first.text)
        replay=self.post({**self.body,'next_follow_up_at':'2026-10-01T02:00:00Z'})
        self.assertEqual(replay.status_code,201,replay.text);self.assertEqual(first.json(),replay.json())
        invalid=self.post({**self.body,'next_follow_up_at':'2026-10-01T02:00:00'})
        self.assertEqual(invalid.status_code,422,invalid.text)
        self.assertEqual(invalid.json()['detail'],'CRM_ACCOUNT_DATETIME_INVALID')
