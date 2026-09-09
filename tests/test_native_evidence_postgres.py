"""Real PostgreSQL native evidence boundaries; only dedicated harness resources."""
from __future__ import annotations
import copy
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest
import uuid
from unittest.mock import patch
import psycopg
from psycopg.types.json import Jsonb
os.environ.setdefault('F1_KEYCLOAK_ISSUER_URL','http://material-rag.invalid/realms/anhuan')
from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.features.evidence.envelopes import build_docx_payload, decrypt_fragment
from platform_foundation.f1.features.evidence.docx_native import PARSER_VERSION, SUPPORT_PROFILE
from tests.test_native_evidence import package, p, extract
from platform_foundation.f1.auth import Tenant
from platform_foundation.f1.features.evidence.status import get_status
from platform_foundation.f1.features.evidence.repository import read_claim

STACK=WORLD=None

def setUpModule():
    global STACK,WORLD
    STACK=PostgresIntegrationStack();print('NATIVE_PROJECT='+STACK.project_name,flush=True)
    try: STACK.start();WORLD=STACK.seed_world()
    except BaseException:
        STACK.dispose_runtime();STACK.stop();raise

def tearDownModule():
    if STACK:
        STACK.dispose_runtime();STACK.stop()
        if STACK.cleanup_status!='CLEAN' or STACK.dedicated_after!=(0,0,0) or STACK.shared_match!=1:
            raise AssertionError('NATIVE_CLEANUP_FAILED')
        print('NATIVE_CLEANUP=CLEAN;SHARED_UNCHANGED=1',flush=True)

class NativePostgresTests(unittest.TestCase):
    def report_delivery(self):
        from platform_foundation.f1.features.analysis_reports import service,delivery_repository
        self.eid,self.scope,client_scope,client_id=(uuid.uuid4() for _ in range(4))
        with STACK._bootstrap() as c:
            c.execute("INSERT INTO f1.enterprise(id,name,license_no,business_kind) VALUES(%s,'Worker capability','TEST','service_provider')",(self.eid,))
            audience=uuid.uuid4()
            c.execute("INSERT INTO f1.enterprise(id,name,license_no,business_kind) VALUES(%s,'Worker audience','TEST','client')",(audience,))
            local_seed._ensure_binding(c,local_seed.Binding(self.sub,self.sub,self.sub+'@example.invalid',self.eid,'enterprise_admin'))
            c.execute("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id) VALUES(%s,%s,'Worker client','active',%s)",(client_id,self.eid,self.actor))
            c.execute("INSERT INTO f1.material_knowledge_scope(id,enterprise_id,scope_kind,client_account_id) VALUES(%s,%s,'service_provider',NULL),(%s,%s,'client',%s)",(self.scope,self.eid,client_scope,self.eid,client_id))
            c.execute("INSERT INTO f1.analysis_report_client_audience(id,enterprise_id,client_account_id,audience_enterprise_id,status) VALUES(%s,%s,%s,%s,'active')",(uuid.uuid4(),self.eid,client_id,audience))
        self.version=self.source(self.result);self.complete_review_base()
        self.scope=client_scope;self.version=self.source(self.result);self.complete_review_base()
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def prepare():
            created=await service.create_report(tenant,client_id,uuid.uuid4())
            queued=await service.generate_report(tenant,client_id,uuid.UUID(created['report_id']),uuid.uuid4())
            claims=await delivery_repository.claim_due_deliveries()
            claim=next(c for c in claims if c.id==delivery_repository.delivery_id_for(uuid.UUID(queued['job_id'])))
            return queued,claim,tenant
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1'}):
            return asyncio.run(prepare())

    def generated_report_claim(self):
        from dataclasses import asdict
        from platform_foundation.f1.features.analysis_reports import restricted_worker as rw,worker
        queued,delivery,tenant=self.report_delivery()
        async def prepare():
            claim=await rw.claim_generation(delivery.id,delivery.dispatch_token)
            sources=await rw.load_sources(uuid.UUID(claim['id']),uuid.UUID(claim['lease_token']))
            frozen=worker._freeze_claimed(self.eid,uuid.UUID(claim['client_account_id']),sources)
            return claim,asdict(worker.EvidenceDrivenReportGenerator().generate(frozen))
        claim,result=asyncio.run(prepare())
        # Match the real JSON transport: UUIDs in the Python generator become strings.
        import json
        return claim,json.loads(json.dumps(result,default=str)),delivery

    def report_finish(self,claim,outcome='draft',result=None,c=None):
        if c:
            return c.execute('SELECT f1.finish_report_worker_generation(%s,%s,%s,%s)',
                (claim['id'],claim['lease_token'],outcome,Jsonb(result or {}))).fetchone()[0]
        with self.connection('f1_report_worker') as conn:
            return self.report_finish(claim,outcome,result,c=conn)

    def report_rows(self,claim):
        with STACK._bootstrap() as c:
            return c.execute('SELECT (SELECT count(*) FROM f1.analysis_report_section WHERE version_id=%s),'
                '(SELECT count(*) FROM f1.analysis_report_citation WHERE version_id=%s),'
                '(SELECT status FROM f1.analysis_report_generation_job WHERE id=%s)',
                (claim['version_id'],claim['version_id'],claim['id'])).fetchone()

    def test_report_worker_login_has_only_capabilities_and_forged_context_cannot_change_scope(self):
        from platform_foundation.f1.features.analysis_reports import restricted_worker as rw
        queued,delivery,tenant=self.report_delivery()
        commands=('SELECT * FROM f1.analysis_report','SELECT * FROM f1.material_evidence_fragment',
            'SELECT * FROM f1.user_profile','UPDATE f1.enterprise_user SET role=role',
            "UPDATE f1.analysis_report_version SET status='published'",'DELETE FROM f1.analysis_report_section',
            'SET ROLE f1_api','SET ROLE f1_worker','SET ROLE f1_report_generate_definer',
            'SELECT f1.claim_analysis_report_generation_deliveries(1,300)',
            'SELECT f1.read_effective_material_sources(ARRAY[]::uuid[])',
            "SELECT f1.report_worker_corpus('{}'::jsonb)",
            "SELECT f1.report_worker_context(NULL,NULL,true)",
            "SELECT f1.report_worker_fingerprint('{}'::jsonb)",
            "SELECT f1.report_worker_revoke(NULL,NULL,NULL)")
        for command in commands:
            with self.connection('f1_report_worker') as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute(command)
        for role in ('f1_api','f1_worker','f1_source_reader'):
            with self.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT f1.claim_report_worker_generation(%s,%s)',(delivery.id,delivery.dispatch_token))
        self.assertIsNone(asyncio.run(rw.claim_generation(delivery.id,uuid.uuid4())))
        with self.connection('f1_report_worker') as c:
            c.execute("SELECT set_config('f1.enterprise_id',%s,true),set_config('f1.sub',%s,true)",(str(WORLD.enterprise_b),'forged'))
            claim=c.execute('SELECT f1.claim_report_worker_generation(%s,%s)',(delivery.id,delivery.dispatch_token)).fetchone()[0]
            self.assertEqual(claim['enterprise_id'],str(self.eid));self.assertEqual(claim['actor_sub'],self.sub)
            self.assertEqual(c.execute('SELECT * FROM f1.read_report_worker_sources(%s,%s)',(uuid.uuid4(),claim['lease_token'])).fetchall(),[])
        self.assertIsNone(asyncio.run(rw.claim_generation(delivery.id,delivery.dispatch_token)))
        self.assertFalse(self.report_finish({**claim,'lease_token':str(uuid.uuid4())},'failed',{'reason':'REPORT_PROBE_FAILED'}))

    def test_report_worker_real_rq_without_api_secret_and_legacy_continuation_transfer(self):
        import json,subprocess,sys
        from pathlib import Path
        from redis import Redis
        from rq import Queue
        from platform_foundation.f1.features.analysis_reports import queue as report_queue
        from platform_foundation.f1.features.material_pipeline import queue as pipeline_queue
        from platform_foundation.f1.features.p3.delivery_queue import QUEUE_NAME as ingestion_queue
        queued,delivery,_=self.report_delivery()
        cid=None;client=None
        root=Path(__file__).resolve().parents[1]
        try:
            cid=subprocess.check_output(['docker','run','-d','--label','io.anhuan.scope=report-worker-probe',
                '--label','io.anhuan.report-probe='+uuid.uuid4().hex,'-p','127.0.0.1::6379',
                'redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2'],text=True).strip()
            port=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]['NetworkSettings']['Ports']['6379/tcp'][0]['HostPort']
            url='redis://127.0.0.1:'+port+'/0';client=Redis.from_url(url)
            deadline=time.monotonic()+10
            while True:
                try:client.ping();break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(.05)
            args={'enterprise_id':self.eid,'provider_sub':self.sub,'version_id':self.version}
            with patch.object(report_queue,'REDIS_URL',url),patch.object(pipeline_queue,'REDIS_URL',url),patch.dict(os.environ,{
                'F1_REPORT_WORKER_RESTRICTED':'0','F1_PIPELINE_CONTINUATIONS_ON_INGESTION':'0'}):
                # Real old queued messages coexist with the report generation.
                pipeline_queue.enqueue_report_stage(**args)
                pipeline_queue.enqueue_reconcile_stage(**args)
                pipeline_queue.enqueue_recovery_sweep(enterprise_id=self.eid,provider_sub=self.sub)
                pipeline_queue.enqueue_durable_delivery(delivery_id=uuid.uuid4(),dispatch_token=uuid.uuid4())
                report_queue.enqueue_generation(delivery.id,delivery.dispatch_token)
            q=Queue(report_queue.QUEUE_NAME,connection=client);old_ids=q.job_ids
            self.assertEqual(len(old_ids),5)
            isolated=STACK.control_dir/'report-runtime';isolated.mkdir(mode=0o700)
            for name in ('f1_report_worker_password','f1_material_rag_key'):
                pth=isolated/name;pth.write_bytes((STACK.secrets_dir/name).read_bytes());pth.chmod(0o600)
            env={k:v for k,v in STACK.runtime_env().items() if not k.endswith('_PASSWORD_FILE')}
            env.update(F1_SECRETS_DIR=str(isolated),F1_PROVIDER_SECRETS_DIR=str(isolated),REDIS_URL=url,
                F1_MATERIAL_RAG_KEY_FILE=str(isolated/'f1_material_rag_key'),F1_REPORT_WORKER_RESTRICTED='1',
                F1_PIPELINE_CONTINUATIONS_ON_INGESTION='1',F1_MATERIAL_ANALYSIS_REPORT_LOCAL='1',
                F1_LOCAL_ENGINEERING='1',F1_NATIVE_EVIDENCE_LOCAL='1',F1_MATERIAL_ANALYSIS_REPORT_LLM='0')
            code="""from platform_foundation.f1.database import _api_dsn
try: _api_dsn()
except Exception: pass
else: raise AssertionError('REPORT_WORKER_HAS_API_SECRET')
from redis import Redis
from rq import Queue,SimpleWorker
from platform_foundation.f1.features.analysis_reports.queue import QUEUE_NAME,REDIS_URL
c=Redis.from_url(REDIS_URL)
SimpleWorker([Queue(QUEUE_NAME,connection=c)],connection=c).work(burst=True,logging_level='CRITICAL')
"""
            completed=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,capture_output=True,text=True,timeout=40)
            self.assertEqual(completed.returncode,0,completed.stderr[-1800:])
            self.assertEqual(q.count,0)
            for jid in old_ids:
                self.assertEqual(str(q.fetch_job(jid).get_status()),'JobStatus.FINISHED')
            transferred=Queue(ingestion_queue,connection=client).jobs
            self.assertEqual(len(transferred),4)
            self.assertEqual({j.func_name.rsplit('.',1)[-1] for j in transferred},
                {'run_report_stage','run_reconcile_stage','run_recovery_sweep','run_durable_delivery'})
            self.assertTrue(all(j.id.endswith('-ingestion') for j in transferred))
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT status FROM f1.analysis_report_generation_job WHERE id=%s',(queued['job_id'],)).fetchone()[0],'draft')
                self.assertEqual(c.execute('SELECT state FROM f1.analysis_report_generation_delivery WHERE id=%s',(delivery.id,)).fetchone()[0],'done')
            print('REPORT_RQ_NO_API_SECRET=DRAFT;LEGACY_TRANSFER=4',flush=True)
        finally:
            if client:client.close()
            if cid:
                subprocess.run(['docker','rm','-f',cid],check=True,capture_output=True,timeout=15)

    def test_report_worker_draft_is_atomic_and_replay_or_cross_client_citation_is_rejected(self):
        claim,result,delivery=self.generated_report_claim()
        # A real source under another customer's scope in the same provider.
        with STACK._bootstrap() as c:
            other=uuid.uuid4();scope=uuid.uuid4()
            c.execute("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id) VALUES(%s,%s,'Other client','active',%s)",(other,self.eid,self.actor))
            c.execute("INSERT INTO f1.material_knowledge_scope(id,enterprise_id,scope_kind,client_account_id) VALUES(%s,%s,'client',%s)",(scope,self.eid,other))
        self.scope=scope;wrong=self.source(self.result)
        tampered=copy.deepcopy(result);tampered['citations'][0]['document_version_id']=str(wrong)
        with self.assertRaisesRegex(Exception,'REPORT_CITATION_SCOPE_INVALID'):self.report_finish(claim,result=tampered)
        self.assertEqual(self.report_rows(claim),(0,0,'generating'))
        untyped=copy.deepcopy(result)
        for citation in untyped['citations']:
            citation.update(locator=None,evidence_revision_id=None,fragment_id=None,evidence_body_sha256=None,page_number=1)
        with self.assertRaisesRegex(Exception,'REPORT_CITATION_IDENTITY_REQUIRED'):self.report_finish(claim,result=untyped)
        self.assertEqual(self.report_rows(claim),(0,0,'generating'))
        with self.assertRaisesRegex(Exception,'REPORT_RESULT_INVALID'):self.report_finish(claim,'published',result)
        self.assertTrue(self.report_finish(claim,result=result))
        state=self.report_rows(claim);self.assertEqual(state,(7,len(result['citations']),'draft'))
        self.assertFalse(self.report_finish(claim,result=result));self.assertEqual(self.report_rows(claim),state)
        from platform_foundation.f1.features.analysis_reports import restricted_worker as rw
        self.assertEqual(asyncio.run(rw.load_sources(uuid.UUID(claim['id']),uuid.UUID(claim['lease_token']))),[])
        asyncio.run(rw.finish_delivery(delivery.id,delivery.dispatch_token))
        self.assertIsNone(asyncio.run(rw.read_delivery(delivery.id,delivery.dispatch_token)))

    def test_report_worker_result_holds_membership_lock_and_denies_revoked_actor(self):
        claim,result,_=self.generated_report_claim()
        with self.connection('f1_report_worker') as c:
            self.assertTrue(self.report_finish(claim,result=result,c=c))
            with STACK._bootstrap() as competing:
                competing.execute("SET LOCAL lock_timeout='100ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):
                    competing.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
            c.rollback()
        with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertFalse(self.report_finish(claim,result=result));self.assertEqual(self.report_rows(claim),(0,0,'generating'))

    def test_report_worker_rechecks_effective_corpus_after_model_latency(self):
        from platform_foundation.f1.features.analysis_reports import restricted_worker as rw
        from platform_foundation.f1.features.analysis_reports.contracts import GenerationFailed
        claim,result,_=self.generated_report_claim()
        _,review=self.reviewed_payload();self.write_review_payload(review)
        async def finish():
            with self.assertRaisesRegex(GenerationFailed,'REPORT_SOURCE_FINGERPRINT_CHANGED'):
                await rw.finish_generation(uuid.UUID(claim['id']),uuid.UUID(claim['lease_token']),'draft',result)
        asyncio.run(finish());self.assertEqual(self.report_rows(claim),(0,0,'generating'))
        self.assertTrue(self.report_finish(claim,'failed',{'reason':'REPORT_SOURCE_FINGERPRINT_CHANGED'}))
        from platform_foundation.f1.features.analysis_reports import worker
        queued,delivery,_=self.report_delivery()
        original=worker.EvidenceDrivenReportGenerator.generate
        def edit_while_generating(generator,frozen):
            generated=original(generator,frozen)
            _,review=self.reviewed_payload();self.write_review_payload(review)
            return generated
        with patch.object(worker.EvidenceDrivenReportGenerator,'generate',edit_while_generating),patch.dict(os.environ,{
            'F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1','F1_LOCAL_ENGINEERING':'1','F1_MATERIAL_ANALYSIS_REPORT_LLM':'0'}):
            asyncio.run(rw.process_delivery(delivery.id,delivery.dispatch_token))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT status,error_reason FROM f1.analysis_report_generation_job WHERE id=%s',(queued['job_id'],)).fetchone(),('failed','REPORT_SOURCE_FINGERPRINT_CHANGED'))
            self.assertEqual(c.execute('SELECT state,reason_code FROM f1.analysis_report_generation_delivery WHERE id=%s',(delivery.id,)).fetchone(),('blocked','REPORT_SOURCE_FINGERPRINT_CHANGED'))
            self.assertEqual(c.execute('SELECT count(*) FROM f1.analysis_report_section WHERE version_id=%s',(queued['version_id'],)).fetchone()[0],0)

    def test_report_worker_expiry_during_report_lock_wait_writes_nothing(self):
        claim,result,_=self.generated_report_claim()
        with STACK._bootstrap() as c:
            c.execute("SELECT set_config('session_replication_role','replica',true)")
            c.execute("UPDATE f1.analysis_report_generation_job SET lease_until=clock_timestamp()+interval '250 milliseconds' WHERE id=%s",(claim['id'],))
        with STACK._bootstrap() as blocker,ThreadPoolExecutor(max_workers=1) as pool:
            blocker.execute('SELECT id FROM f1.analysis_report WHERE id=%s FOR UPDATE',(claim['report_id'],))
            future=pool.submit(self.report_finish,claim,'draft',result)
            time.sleep(.35);blocker.commit()
            self.assertFalse(future.result(timeout=5))
        self.assertEqual(self.report_rows(claim),(0,0,'generating'))

    def test_report_worker_revoked_unclaimed_actor_is_audited_and_retry_fences_old_lease(self):
        from platform_foundation.f1.features.analysis_reports import restricted_worker as rw
        queued,delivery,_=self.report_delivery()
        with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        asyncio.run(rw.process_delivery(delivery.id,delivery.dispatch_token))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT status,error_reason FROM f1.analysis_report_generation_job WHERE id=%s',(queued['job_id'],)).fetchone(),('failed','REPORT_ACTOR_REVOKED'))
            self.assertEqual(c.execute("SELECT count(*) FROM f1.analysis_report_audit_event WHERE version_id=%s AND action='actor_revoked'",(queued['version_id'],)).fetchone()[0],1)
        claim,result,delivery=self.generated_report_claim()
        self.assertTrue(self.report_finish(claim,'retry',{}))
        new=asyncio.run(rw.claim_generation(delivery.id,delivery.dispatch_token))
        self.assertNotEqual(new['lease_token'],claim['lease_token'])
        self.assertFalse(self.report_finish(claim,result=result))
        self.assertTrue(self.report_finish(new,'failed',{'reason':'REPORT_GENERATION_RETRIES_EXHAUSTED'}))

    def leased_source(self, job, *, kind='native', c=None, token=None, job_id=None):
        if c:
            return c.execute('SELECT f1.read_leased_task_source(%s,%s,%s)',(kind,job_id or job['job_id'],token or job['lease_token'])).fetchone()[0]
        with self.connection('f1_source_reader') as conn:return self.leased_source(job,kind=kind,c=conn,token=token,job_id=job_id)

    def test_task_source_reader_only_live_bound_source_and_no_table_or_write_power(self):
        self.register();job=self.claim()
        source=self.leased_source(job)
        self.assertEqual(source['document_version_id'],str(self.version));self.assertEqual(source['storage_area'],'released')
        self.assertIsNone(self.leased_source(job,token=uuid.uuid4()))
        self.assertIsNone(self.leased_source(job,job_id=uuid.uuid4()))
        self.assertIsNone(self.leased_source(job,kind='pdf-index'))
        for role in ('f1_api','f1_worker'):
            with self.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):self.leased_source(job,c=c)
        for command in ('SELECT * FROM f1.material_evidence_job','SELECT * FROM f1.document','SELECT * FROM f1.material_evidence_fragment',
                        'UPDATE f1.upload_task SET status=status',"SET ROLE f1_api","SET ROLE f1_worker",
                        "SELECT f1.claim_native_extraction_jobs(1,300)"):
            with self.connection('f1_source_reader') as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute(command)
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(job['job_id'],))
        self.assertIsNone(self.leased_source(job))
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '300 seconds' WHERE id=%s",(job['job_id'],))
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertIsNone(self.leased_source(job))

    def test_task_source_reader_pdf_index_identity_and_current_version(self):
        self.historical_pdf_base()
        from platform_foundation.f1.features.material_rag.repository import claim_job
        from platform_foundation.f1.features.material_rag.contracts import MaterialRagJobClaim
        jid=uuid.uuid4()
        with STACK._bootstrap() as c:
            record,task=c.execute('SELECT document_record_id,upload_task_id FROM f1.document_version WHERE id=%s',(self.version,)).fetchone()
            c.execute("INSERT INTO f1.material_rag_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,upload_task_id,source_sha256,action,idempotency_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,'rebuild',%s)",(jid,self.eid,self.scope,record,self.version,task,self.result.source_sha256,uuid.uuid4().hex*2))
        claim=asyncio.run(claim_job(jid,worker_id='source-probe',lease_seconds=300))
        self.assertIsInstance(claim,MaterialRagJobClaim)
        job={'job_id':jid,'lease_token':claim.lease_token}
        self.assertEqual(self.leased_source(job,kind='pdf-index')['storage_area'],'quarantine')
        self.assertIsNone(self.leased_source(job,kind='native'))
        self.assertIsNone(self.leased_source(job,kind='pdf-index',token=uuid.uuid4()))
        with STACK._bootstrap() as c:c.execute('UPDATE f1.document_record SET latest_version_no=2 WHERE id=%s',(record,))
        self.assertIsNone(self.leased_source(job,kind='pdf-index'))

    def test_task_source_gateway_real_minio_http_and_locks(self):
        import hashlib,io,socket
        import httpx,uvicorn
        from platform_foundation.f1 import source_gateway,storage
        from tests import test_storage_service_runtime as objects
        self.actual_size(self.raw);self.register();job=self.claim()
        source=self.leased_source(job);request={'kind':'native','job_id':job['job_id'],'lease_token':job['lease_token']}
        objects.setUpModule()
        server=None;thread=None;sock=None
        try:
            user,password=objects.IDENTITIES['worker']
            uploader=objects.ServiceStorageRuntimeTests().client('api')
            uploader.put_object(storage.BUCKET,source['object_key'],io.BytesIO(self.raw),len(self.raw))
            values={'minio_service_user':user,'minio_service_password':password}
            original_secret=storage.read_f1_secret_text
            def secret(name,**kw):return values[name] if name in values else original_secret(name,**kw)
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16)
            url='http://127.0.0.1:'+str(sock.getsockname()[1])
            server=uvicorn.Server(uvicorn.Config(source_gateway.app,log_level='critical',access_log=False))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
            with patch.object(storage,'MINIO_ENDPOINT',objects.ENDPOINT),patch.object(storage,'read_f1_secret_text',side_effect=secret),patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1'}):
                thread.start()
                deadline=time.monotonic()+5
                while not server.started and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(server.started)
                with httpx.Client(timeout=10,trust_env=False) as client:
                    response=client.post(url+'/source',json=request);self.assertEqual(response.status_code,200);self.assertEqual(response.content,self.raw)
                    self.assertEqual(response.headers['cache-control'],'no-store')
                    for change in ({'lease_token':str(uuid.uuid4())},{'job_id':str(uuid.uuid4())},{'object_key':source['object_key']},{'lease_token':'not-a-token'}):
                        response=client.post(url+'/source',json={**request,**change});self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                    # While the gateway reads MinIO, a concurrent source mutation
                    # must block at the database row lock.
                    original_read=storage.read_released_material_source
                    def locked_read(*args):
                        with STACK._bootstrap() as c:
                            c.execute("SET LOCAL lock_timeout='100ms'")
                            with self.assertRaises(psycopg.errors.LockNotAvailable):c.execute('UPDATE f1.upload_task SET source_etag=source_etag WHERE id=%s',(job['upload_task_id'],))
                            c.rollback()
                        return original_read(*args)
                    with patch.object(storage,'read_released_material_source',side_effect=locked_read):
                        response=client.post(url+'/source',json=request);self.assertEqual(response.status_code,200)
                    uploader.put_object(storage.BUCKET,source['object_key'],io.BytesIO(b'x'*len(self.raw)),len(self.raw))
                    response=client.post(url+'/source',json=request);self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                    uploader.put_object(storage.BUCKET,source['object_key'],io.BytesIO(self.raw),len(self.raw))
                    # The actual native worker follows its gateway branch over
                    # a real socket, then finalizes through its real DB lease.
                    outer=self
                    class LocalTransport(httpx.HTTPTransport):
                        def handle_request(self, req):
                            outer.assertEqual(str(req.url),'http://source-gateway:8080/source')
                            req.url=req.url.copy_with(host='127.0.0.1',port=sock.getsockname()[1])
                            return super().handle_request(req)
                    original_client=httpx.Client
                    def local_client(**kwargs):return original_client(**kwargs,transport=LocalTransport())
                    from platform_foundation.f1.features.evidence import worker
                    with patch.object(httpx,'Client',side_effect=local_client),patch.dict(os.environ,{'F1_TASK_SOURCE_GATEWAY':'1','F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):
                        self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(job['job_id']),uuid.UUID(job['lease_token']))),'DONE')
                    self.assertEqual(self.count(job),(1,3))
                    response=client.post(url+'/source',json=request);self.assertEqual(response.status_code,404)
        finally:
            if server:server.should_exit=True
            if thread:thread.join(5)
            if sock:sock.close()
            objects.tearDownModule()

    def test_review_original_base_survives_confirmation_revoke_but_not_source_or_actor_change(self):
        self.actual_size(self.raw);self.complete_review_base()
        base=self.review_source();fragment=base['base_fragments'][0]['id'];revision=base['base_revision_id']
        def read(c=None, identity=None):
            if c:return c.execute('SELECT f1.read_review_original(%s,%s,%s)',identity or (self.version,fragment,revision)).fetchone()[0]
            with self.connection('f1_api') as conn:return read(conn)
        self.assertEqual(read()['source_sha256'],self.result.source_sha256)
        _,payload=self.reviewed_payload();confirmed=self.write_review_payload(payload)
        self.assertEqual(read()['locator'],base['base_fragments'][0]['locator'])
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload
        request=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=confirmed['id'],action='revoke')
        self.write_review_payload(build_review_payload(self.review_source(),request))
        self.assertIsNotNone(read())
        from platform_foundation.f1.features.evidence.original import original_view
        from platform_foundation.f1 import storage
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}),patch.object(storage,'read_released_material_source',return_value=self.raw):
            rendered=asyncio.run(original_view(tenant,version_id=self.version,fragment_id=uuid.UUID(fragment),revision_id=uuid.UUID(revision),review_base=True))
            self.assertEqual(rendered['original_text'],'排口 COD 42 mg/L');self.assertNotIn('object_key',rendered)
        with self.connection('f1_api') as c:
            for identity in [(self.version,uuid.uuid4(),revision),(self.version,fragment,uuid.uuid4()),(uuid.uuid4(),fragment,revision)]:self.assertIsNone(read(c,identity))
            self.assertIsNone(c.execute('SELECT f1.read_citation_original(NULL,%s,%s,%s)',(self.version,fragment,revision)).fetchone()[0])
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:self.assertIsNone(read(c))
        with self.connection('f1_worker') as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):read(c)
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertIsNone(read())
        self.sub='review-original-admin-'+uuid.uuid4().hex
        with STACK._bootstrap() as c:local_seed._ensure_binding(c,local_seed.Binding(self.sub,self.sub,self.sub+'@example.invalid',self.eid,'enterprise_admin'))
        self.assertIsNotNone(read())
        with STACK._bootstrap() as c:c.execute('UPDATE f1.document_record SET latest_version_no=2 WHERE id=(SELECT document_record_id FROM f1.document_version WHERE id=%s)',(self.version,))
        self.assertIsNone(read())

    def recover_raw(self, request, expected=None, *, c=None, version=None):
        if c:return c.execute('SELECT f1.recover_native_extraction(%s,%s,%s,%s,%s)',
            (version or self.version,request,expected,PARSER_VERSION,SUPPORT_PROFILE)).fetchone()[0]
        with self.connection('f1_api') as c:return self.recover_raw(request,expected,c=c,version=version)

    def test_recovery_backfill_receipt_survives_running_done_and_unknown_result(self):
        request=uuid.uuid4();first=self.recover_raw(request)
        self.assertEqual(first['outcome'],'registered');self.assertEqual(first['state'],'pending')
        job=self.claim();again=self.recover_raw(request)
        self.assertTrue(again['replayed']);self.assertEqual(again['job_id'],job['job_id'])
        self.finalize(job,self.payload(job));third=self.recover_raw(request)
        self.assertTrue(third['replayed']);self.assertEqual(self.count(job),(1,3))
        noop=self.recover_raw(uuid.uuid4(),job['job_id']);self.assertEqual(noop['state'],'done');self.assertEqual(noop['outcome'],'unchanged')
        with self.connection('f1_api') as c,self.assertRaisesRegex(Exception,'NATIVE_RECOVERY_REQUEST_CONFLICT'):
            self.recover_raw(request,job['job_id'],c=c)
        with self.connection('f1_api') as c,self.assertRaisesRegex(Exception,'NATIVE_RECOVERY_JOB_CHANGED'):
            self.recover_raw(uuid.uuid4(),None,c=c)
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE enterprise_id=%s AND action='native.extraction.recovery_requested' AND resource_id=%s",(self.eid,str(request))).fetchone()[0],1)

    def test_recovery_rebinds_only_blocked_job_and_fences_old_token(self):
        self.register();old=self.claim();old_actor=self.actor;old_sub=self.sub
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,old_actor))
        self.assertIsNone(asyncio.run(read_claim(uuid.UUID(old['job_id']),uuid.UUID(old['lease_token']))))
        new_sub='recovery-admin-'+uuid.uuid4().hex
        with STACK._bootstrap() as c:local_seed._ensure_binding(c,local_seed.Binding(new_sub,new_sub,new_sub+'@example.invalid',self.eid,'enterprise_admin'))
        request=uuid.uuid4()
        with self.connection('f1_api',sub=new_sub) as c:receipt=self.recover_raw(request,old['job_id'],c=c)
        self.assertEqual(receipt['outcome'],'rearmed');self.assertEqual(receipt['job_id'],old['job_id'])
        new=self.claim();self.assertEqual(new['actor_sub'],new_sub);self.assertNotEqual(new['lease_token'],old['lease_token'])
        self.assertEqual(new['revision_id'],old['revision_id']);self.assertEqual(new['attempt'],1)
        self.assertIsNone(self.finalize(old,self.payload(old)));self.finalize(new,self.payload(new));self.assertEqual(self.count(new),(1,3))
        with self.connection('f1_api',sub=old_sub) as c,self.assertRaisesRegex(Exception,'NATIVE_SOURCE_UNAVAILABLE'):
            self.recover_raw(request,old['job_id'],c=c)
        with self.connection('f1_worker') as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.recover_raw(uuid.uuid4(),old['job_id'],c=c)

    def test_recovery_concurrent_commands_do_not_reset_active_attempt(self):
        self.register();job=self.claim()
        with self.connection() as c:c.execute('SELECT f1.finish_native_extraction_failure(%s,%s,%s,NULL)',(job['job_id'],job['lease_token'],'NATIVE_PARSE_REJECTED'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda request:self.recover_raw(request,job['job_id']),[uuid.uuid4(),uuid.uuid4()]))
        self.assertEqual(sorted(x['outcome'] for x in results),['rearmed','unchanged'])
        running=self.claim();request=uuid.uuid4();receipt=self.recover_raw(request,running['job_id'])
        self.assertEqual(receipt['state'],'running');self.assertEqual(receipt['outcome'],'unchanged')
        with self.connection() as c:c.execute('SELECT f1.finish_native_extraction_failure(%s,%s,%s,NULL)',(running['job_id'],running['lease_token'],'NATIVE_PARSE_REJECTED'))
        self.assertTrue(self.recover_raw(request,running['job_id'])['replayed'])
        with self.connection('f1_api') as c:self.assertEqual(c.execute('SELECT state FROM f1.material_evidence_job WHERE id=%s',(running['job_id'],)).fetchone()[0],'blocked')

    def test_recovery_api_transaction_rolls_back_handoff_and_discovers_missing(self):
        from platform_foundation.f1.features.evidence.recovery import recover,NativeRecoveryIn,missing_candidates
        from platform_foundation.f1.features.material_pipeline import repository as pipeline_repository
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        request=NativeRecoveryIn(request_id=uuid.uuid4(),expected_job_id=None)
        flags={'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1',
            'F1_MATERIAL_RAG_LOCAL_INDEX':'1','F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1','REDIS_URL':'redis://127.0.0.1:6379/0'}
        async def run():
            found=[];after=None
            while True:
                page=await missing_candidates(tenant,scope_kind='service_provider',after=after,limit=1)
                found.extend(page['version_ids']);after=uuid.UUID(page['next_after']) if page['next_after'] else None
                if after is None:break
            self.assertIn(str(self.version),found);self.assertEqual(len(set(found)),len(found))
            with patch.object(pipeline_repository,'register_delivery_in_session',side_effect=RuntimeError('RECOVERY_HANDOFF_PROBE')):
                with self.assertRaisesRegex(RuntimeError,'RECOVERY_HANDOFF_PROBE'):await recover(tenant,self.version,request)
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_evidence_job WHERE document_version_id=%s',(self.version,)).fetchone()[0],0)
                self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='native.extraction.recovery_requested' AND resource_id=%s",(str(request.request_id),)).fetchone()[0],0)
            receipt=await recover(tenant,self.version,request);self.assertEqual(receipt.outcome,'registered')
            self.assertTrue((await recover(tenant,self.version,request)).replayed)
            with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(self.version,)).fetchone()[0],'pending')
        with patch.dict(os.environ,flags):asyncio.run(run())

    def effective_state(self):
        with self.connection('f1_api') as c:
            return c.execute('SELECT f1.read_effective_material_state(%s)',(self.version,)).fetchone()[0]

    def test_effective_state_tracks_jobs_review_revoke_and_current_version(self):
        self.assertEqual(self.effective_state()['state'],'pending')
        self.register();job=self.claim()
        self.assertEqual(self.effective_state()['state'],'running')
        self.finalize(job,self.payload(job))
        state=self.effective_state();self.assertEqual(state['state'],'ready')
        self.assertEqual(state['fragment_count'],3);self.assertEqual(state['evidence_kind'],'extraction')
        _,payload=self.reviewed_payload();receipt=self.write_review_payload(payload)
        state=self.effective_state();self.assertEqual(state['evidence_kind'],'review');self.assertEqual(state['revision_id'],receipt['id'])
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload
        revoke=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=receipt['id'],action='revoke')
        self.write_review_payload(build_review_payload(self.review_source(),revoke))
        self.assertEqual(self.effective_state()['state'],'revoked')
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:
            self.assertIsNone(c.execute('SELECT f1.read_effective_material_state(%s)',(self.version,)).fetchone()[0])
        with self.connection('f1_worker') as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT f1.read_effective_material_state(%s)',(self.version,))
        with STACK._bootstrap() as c:c.execute('UPDATE f1.document_record SET latest_version_no=2 WHERE id=%s',(job['document_record_id'],))
        self.assertEqual(self.effective_state()['state'],'unavailable')

    def test_effective_pipeline_reads_native_state_without_pdf_index(self):
        self.register();job=self.claim();self.finalize(job,self.payload(job))
        from platform_foundation.f1.features.material_pipeline.coordinator import _load_context,_analysis_stage,_index_stage
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def run():
            context=await _load_context(tenant,self.version)
            self.assertIsNone(context.index_job_id)
            self.assertEqual(_analysis_stage(context).status,'ready');self.assertEqual(_index_stage(context).status,'ready')
            self.assertEqual(_index_stage(context).reason_code,'EFFECTIVE_EXTRACTION_READY')
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):asyncio.run(run())

    def test_original_current_fragment_identity_scope_and_storage_hash(self):
        self.actual_size(self.raw);self.register();job=self.claim();self.finalize(job,self.payload(job))
        source=next(s for s in self.effective_sources() if s['document_version_id']==str(self.version))
        fragment=source['fragments'][0]['id'];revision=source['evidence_revision_id']
        with self.connection('f1_api') as c:
            row=c.execute('SELECT f1.read_citation_original(NULL,%s,%s,%s)',(self.version,fragment,revision)).fetchone()[0]
            self.assertEqual(row['source_sha256'],self.result.source_sha256)
            self.assertEqual(row['locator'],source['fragments'][0]['locator'])
            for identity in [(self.version,uuid.uuid4(),revision),(self.version,fragment,uuid.uuid4()),(uuid.uuid4(),fragment,revision)]:
                self.assertIsNone(c.execute('SELECT f1.read_citation_original(NULL,%s,%s,%s)',identity).fetchone()[0])
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:
            self.assertIsNone(c.execute('SELECT f1.read_citation_original(NULL,%s,%s,%s)',(self.version,fragment,revision)).fetchone()[0])
        from platform_foundation.f1.features.evidence.original import original_view
        from platform_foundation.f1 import storage
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        identity=dict(version_id=self.version,fragment_id=uuid.UUID(fragment),revision_id=uuid.UUID(revision))
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):
            with patch.object(storage,'read_released_material_source',return_value=self.raw):
                result=asyncio.run(original_view(tenant,**identity));self.assertEqual(result['original_text'],'排口 COD 42 mg/L')
                self.assertNotIn('object_key',result)
            with patch.object(storage,'read_released_material_source',return_value=b'changed'):
                with self.assertRaises(Exception) as caught:asyncio.run(original_view(tenant,**identity))
                self.assertEqual(caught.exception.http_status,503)
        _,payload=self.reviewed_payload();self.write_review_payload(payload)
        with self.connection('f1_api') as c:
            self.assertIsNone(c.execute('SELECT f1.read_citation_original(NULL,%s,%s,%s)',(self.version,fragment,revision)).fetchone()[0])

    def setUp(self):
        self.sub='native-admin-'+uuid.uuid4().hex
        self.eid=WORLD.enterprise_a
        with STACK._bootstrap() as c:
            local_seed._ensure_binding(c,local_seed.Binding(self.sub,self.sub,self.sub+'@example.invalid',self.eid,'enterprise_admin'))
            self.actor=c.execute('SELECT id FROM f1.user_profile WHERE keycloak_sub=%s',(self.sub,)).fetchone()[0]
            self.scope=c.execute("SELECT id FROM f1.material_knowledge_scope WHERE enterprise_id=%s AND scope_kind='service_provider'",(self.eid,)).fetchone()[0]
        self.tail='末段 '+uuid.uuid4().hex
        self.raw=package(p('排口 COD 42 mg/L')+'<w:p/>'+p(self.tail))
        self.result=extract(self.raw)
        self.version=self.source(self.result)
    def connection(self,role='f1_worker',eid=None,sub=None):
        c=psycopg.connect(host='127.0.0.1',port=STACK.host_port,dbname=STACK.database,user=role,password=STACK.passwords[role])
        if role=='f1_api':
            c.execute("SELECT set_config('f1.enterprise_id',%s,true),set_config('f1.sub',%s,true)",(str(eid or self.eid),sub or self.sub))
        return c
    def source(self,result,source_format="docx"):
        mime,ext={"docx":("application/vnd.openxmlformats-officedocument.wordprocessingml.document",".docx"),"xlsx":("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",".xlsx"),"jpeg":("image/jpeg",".jpg")}[source_format]
        label='native-'+uuid.uuid4().hex
        document,record,task,version=(uuid.uuid4() for _ in range(4))
        key=uuid.uuid4().hex+ext
        with STACK._bootstrap() as c:
            c.execute("INSERT INTO f1.document(id,enterprise_id,object_key,filename,size,content_type,status,knowledge_scope_id) VALUES(%s,%s,%s,'synthetic'||%s,100,%s,'done',%s)",(document,self.eid,key,ext,mime,self.scope))
            c.execute("INSERT INTO f1.document_record(id,enterprise_id,title,status,latest_version_no,created_by_user_id,declared_material_kind,knowledge_scope_id,scope_selection_source,scope_selected_by_user_id,scope_selected_at) VALUES(%s,%s,%s,'active',1,%s,'unknown',%s,'upload_selection',%s,clock_timestamp())",(record,self.eid,label,self.actor,self.scope,self.actor))
            c.execute("INSERT INTO f1.upload_task(id,enterprise_id,document_id,object_key,content_sha256,status,object_state,pipeline_kind,processing_stage,quarantine_status,scan_verdict,preview_status,preview_kind,released_at,source_size,source_etag) VALUES(%s,%s,%s,%s,%s,'done','ready','controlled_ingestion','ready','released','clean','ready','page_text',clock_timestamp(),100,'synthetic-original-etag')",(task,self.eid,document,key,result.source_sha256))
            c.execute("INSERT INTO f1.document_version(id,enterprise_id,document_record_id,version_no,source_document_id,upload_task_id,display_filename,idempotency_key_sha256,created_by_user_id) VALUES(%s,%s,%s,1,%s,%s,'synthetic.docx',%s,%s)",(version,self.eid,record,document,task,uuid.uuid4().hex+uuid.uuid4().hex,self.actor))
        return version
    def register(self,version=None,c=None):
        if c:return c.execute('SELECT f1.register_native_extraction_job(%s,%s,%s)',(version or self.version,PARSER_VERSION,SUPPORT_PROFILE)).fetchone()[0]
        with self.connection('f1_api') as c:return self.register(version,c)
    def claim(self):
        with self.connection() as c:
            rows=[r[0] for r in c.execute('SELECT * FROM f1.claim_native_extraction_jobs(100,300)').fetchall()]
        return next(r for r in rows if r['document_version_id']==str(self.version))
    def payload(self,job,result=None):
        return build_docx_payload(result or self.result,**{key:uuid.UUID(job[key]) for key in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','revision_id')})
    def finalize(self,job,payload,c=None):
        if c:return c.execute('SELECT f1.finalize_native_extraction(%s,%s,%s)',(job['job_id'],job['lease_token'],Jsonb(payload))).fetchone()[0]
        with self.connection() as c:return self.finalize(job,payload,c)
    def count(self,job):
        with STACK._bootstrap() as c:
            return c.execute('SELECT (SELECT count(*) FROM f1.material_extraction_revision WHERE job_id=%s),(SELECT count(*) FROM f1.material_evidence_fragment WHERE extraction_revision_id=%s)',(job['job_id'],job['revision_id'])).fetchone()
    def review_source(self):
        with self.connection('f1_api') as c:
            return c.execute('SELECT f1.read_material_review(%s)',(self.version,)).fetchone()[0]
    def reviewed_payload(self, source=None, head=None, request_id=None):
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload
        source=source or self.review_source()
        request=ReviewWriteIn(request_id=request_id or uuid.uuid4(),expected_review_revision_id=head,
            action='confirm',base_revision_id=source['base_revision_id'],base_manifest_sha256=source['base_manifest_sha256'],
            checked_against_source=True,texts=[dict(base_fragment_id=x['id'],text='已核对修订 '+str(n)) for n,x in enumerate(source['base_fragments'])],
            fields=[dict(base_fragment_id=source['base_fragments'][0]['id'],field_name='report_title',text='人工报告标题')])
        return request,build_review_payload(source,request)
    def write_review_payload(self,payload,c=None):
        if c:return c.execute('SELECT f1.write_material_review(%s)',(Jsonb(payload),)).fetchone()[0]
        with self.connection('f1_api') as c:return self.write_review_payload(payload,c)
    def complete_review_base(self):
        self.register();job=self.claim();self.finalize(job,self.payload(job));return job
    def test_review_append_encrypted_roundtrip_replay_revoke_and_reconfirm(self):
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload,decrypt_review_fragment,request_digest
        j=self.complete_review_base();source=self.review_source();request,payload=self.reviewed_payload(source)
        self.assertEqual(payload['request_sha256'],request_digest(source,request))
        first=self.write_review_payload(payload);self.assertEqual(first['revision_no'],1)
        second=self.write_review_payload(build_review_payload(source,request));self.assertTrue(second['replayed']);self.assertEqual(first['id'],second['id'])
        row=self.review_source();self.assertEqual([decrypt_review_fragment(x) for x in row['review_fragments']],['已核对修订 0','已核对修订 1','已核对修订 2','人工报告标题'])
        self.assertEqual(self.count(j),(1,3))
        revoke=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=first['id'],action='revoke')
        receipt=self.write_review_payload(build_review_payload(source,revoke));self.assertEqual(receipt['revision_no'],2)
        self.assertEqual(self.review_source()['review_fragments'],[])
        _,p=self.reviewed_payload(head=receipt['id']);self.assertEqual(self.write_review_payload(p)['revision_no'],3)
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action LIKE 'native.review.%%' AND resource_id IN(SELECT id::text FROM f1.material_review_revision WHERE document_version_id=%s)",(self.version,)).fetchone()[0],3)
    def test_review_request_conflict_stale_head_and_lost_result_probe(self):
        from platform_foundation.f1.features.evidence.review import request_digest,build_review_payload
        self.complete_review_base();source=self.review_source();request,p=self.reviewed_payload(source)
        first=self.write_review_payload(p)
        _,stale=self.reviewed_payload(source)
        with self.assertRaisesRegex(psycopg.Error,'REVIEW_REVISION_CONFLICT'):self.write_review_payload(stale)
        changed=request.model_copy(update={'texts':[request.texts[0].model_copy(update={'text':'不同内容'}),*request.texts[1:]]})
        with self.assertRaisesRegex(psycopg.Error,'REVIEW_REQUEST_CONFLICT'):self.write_review_payload(build_review_payload(source,changed))
        with self.connection('f1_api') as c:
            receipt=c.execute('SELECT f1.probe_material_review_request(%s,%s,%s)',(self.version,request.request_id,request_digest(source,request))).fetchone()[0]
            self.assertEqual(receipt['id'],first['id']);self.assertTrue(receipt['replayed'])
    def test_review_source_actor_and_private_tables_are_fenced(self):
        self.complete_review_base();_,p=self.reviewed_payload();self.write_review_payload(p)
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:
            self.assertIsNone(c.execute('SELECT f1.read_material_review(%s)',(self.version,)).fetchone()[0])
            with self.assertRaisesRegex(psycopg.Error,'REVIEW_REQUEST_INVALID|REVIEW_SOURCE_UNAVAILABLE'):self.write_review_payload(p,c)
        for role in ('f1_api','f1_worker'):
            for statement in ('SELECT * FROM f1.material_review_fragment','INSERT INTO f1.material_review_revision DEFAULT VALUES','SELECT f1.review_source(NULL)'):
                with self.subTest(role=role,sql=statement),self.connection(role) as c:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute(statement)
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.enterprise_user SET role='auditor' WHERE enterprise_id=%s AND user_id=%s",(self.eid,self.actor))
        with self.connection('f1_api') as c:self.assertIsNone(c.execute('SELECT f1.read_material_review(%s)',(self.version,)).fetchone()[0])
        with self.assertRaisesRegex(psycopg.Error,'REVIEW_SOURCE_UNAVAILABLE'):self.write_review_payload(p)
    def test_review_payload_tamper_partial_base_and_immutability(self):
        from platform_foundation.f1.features.evidence.review import semantic_hash
        self.complete_review_base();_,good=self.reviewed_payload()
        mutations=[lambda x:x['fragments'][0]['locator'].update(body_index=999),
            lambda x:x['fragments'][0].update(base_fragment_id=str(uuid.uuid4())),
            lambda x:x.update(base_manifest_sha256='0'*64),lambda x:x['fragments'].pop(0),
            lambda x:x['fragments'][0].update(body_aad_sha256='0'*64),lambda x:x['fragments'][0].update(ordinal=True),
            lambda x:x['fragments'][-1].update(field_name='invented'),lambda x:x.update(checked_against_source=False),
            lambda x:x['fragments'][0].update(body_ciphertext_hex='00'),lambda x:x['fragments'][0].update(character_count=2000001)]
        for mutate in mutations:
            bad=copy.deepcopy(good);mutate(bad);bad['request_sha256']=semantic_hash(bad)
            with self.assertRaises(psycopg.Error):self.write_review_payload(bad)
        self.write_review_payload(good)
        for table in ('material_review_revision','material_review_fragment'):
            with STACK._bootstrap() as c:
                with self.assertRaisesRegex(psycopg.Error,'NATIVE_REVISION_IMMUTABLE'):c.execute('DELETE FROM f1.'+table)
        partial=extract(package(p('incomplete')+'<w:p><w:r><w:drawing/></w:r></w:p>'))
        self.version=self.source(partial);self.register();j=self.claim();self.finalize(j,self.payload(j,partial))
        row=self.review_source();self.assertNotIn('base_fragments',row)
    def test_review_concurrent_edit_one_winner_and_rollback(self):
        self.complete_review_base();_,p1=self.reviewed_payload();_,p2=self.reviewed_payload()
        with self.connection('f1_api') as c:self.write_review_payload(p1,c);c.rollback()
        with ThreadPoolExecutor(max_workers=2) as pool:
            def submit(payload):
                try:return self.write_review_payload(payload)
                except psycopg.Error as e:return str(e)
            results=list(pool.map(submit,[p1,p2]))
        self.assertEqual(sum(isinstance(x,dict) for x in results),1)
        self.assertIn('REVIEW_REVISION_CONFLICT',str(results))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT count(*) FROM f1.material_review_revision WHERE document_version_id=%s',(self.version,)).fetchone()[0],1)

    def test_review_api_projection_and_unknown_response_retry(self):
        from platform_foundation.f1.features.evidence.review_service import get_review,write_review
        self.complete_review_base();request,_=self.reviewed_payload()
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def run():
            original=await get_review(tenant,self.version);self.assertTrue(original.editable)
            self.assertEqual(original.base_items[0].text,'排口 COD 42 mg/L')
            receipt=await write_review(tenant,self.version,request)
            repeat=await write_review(tenant,self.version,request)
            self.assertEqual(receipt.id,repeat.id);self.assertTrue(repeat.replayed)
            result=await get_review(tenant,self.version)
            self.assertEqual(result.review_items[0].text,'已核对修订 0')
            serialized=result.model_dump_json()
            for forbidden in ('ciphertext','object_key','source_etag','actor_sub','lease_token'):
                self.assertNotIn(forbidden,serialized)
            _,stale=self.reviewed_payload()
            with self.assertRaises(Exception) as caught:await write_review(tenant,self.version,request.model_copy(update={'request_id':uuid.uuid4()}))
            self.assertEqual(caught.exception.http_status,409)
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):asyncio.run(run())
    def historical_pdf_base(self):
        from platform_foundation.f1.features.material_rag.security import canonical_page_units,encrypt_text,unit_aad
        jid=uuid.uuid4()
        with STACK._bootstrap() as c:
            record,task,document=c.execute('SELECT document_record_id,upload_task_id,source_document_id FROM f1.document_version WHERE id=%s',(self.version,)).fetchone()
            key=uuid.uuid4().hex+'.pdf'
            c.execute("UPDATE f1.document SET content_type='application/pdf',object_key=%s WHERE id=%s",(key,document))
            c.execute('UPDATE f1.upload_task SET object_key=%s WHERE id=%s',(key,task))
            units=canonical_page_units(enterprise_id=self.eid,knowledge_scope_id=self.scope,document_record_id=record,document_version_id=self.version,
                source_sha256=self.result.source_sha256,page_number=3,parser_version='pypdf-6.14.2-visual3',text='人工复核前 COD 42 mg/L')
            # Historical PDF fixture only; review reads/writes use real restricted roles.
            c.execute("SELECT set_config('session_replication_role','replica',true)")
            for u in units:
                cipher,aad=encrypt_text(u.body.reveal(),unit_aad(u))
                c.execute('INSERT INTO f1.material_rag_unit(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,source_sha256,page_number,ordinal,parser_version,body_ciphertext,body_sha256,body_aad_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (u.id,self.eid,self.scope,record,self.version,u.source_sha256,u.page_number,u.ordinal,u.parser_version,cipher,u.body_sha256,aad))
            c.execute("INSERT INTO f1.material_rag_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,upload_task_id,source_sha256,action,status,idempotency_sha256,result_manifest_sha256,indexed_unit_count) VALUES(%s,%s,%s,%s,%s,%s,%s,'index','done',%s,%s,%s)",
                (jid,self.eid,self.scope,record,self.version,task,self.result.source_sha256,uuid.uuid4().hex*2,'0'*64,len(units)))

    def test_review_pdf_base_preserves_v1_identity_and_rebuild_invalidates_base(self):
        from platform_foundation.f1.features.evidence.review_service import get_review,write_review
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn
        self.historical_pdf_base()
        with STACK._bootstrap() as c:
            record,task=c.execute('SELECT document_record_id,upload_task_id FROM f1.document_version WHERE id=%s',(self.version,)).fetchone()
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def run():
            first=await get_review(tenant,self.version);self.assertTrue(first.editable)
            self.assertEqual(first.base_items[0].location,'第 3 页');self.assertIn('42 mg/L',first.base_items[0].text)
            request=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=None,action='confirm',
                base_revision_id=first.base_revision_id,base_manifest_sha256=first.base_manifest_sha256,checked_against_source=True,
                texts=[dict(base_fragment_id=x.id,text='核对后 COD 24 mg/L') for x in first.base_items])
            receipt=await write_review(tenant,self.version,request)
            with STACK._bootstrap() as c:
                c.execute("INSERT INTO f1.material_rag_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,upload_task_id,source_sha256,action,idempotency_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,'rebuild',%s)",
                    (uuid.uuid4(),self.eid,self.scope,record,self.version,task,self.result.source_sha256,uuid.uuid4().hex*2))
            # Lost confirmation response still replays after a base becomes unavailable.
            repeated=await write_review(tenant,self.version,request);self.assertTrue(repeated.replayed);self.assertEqual(receipt.id,repeated.id)
            latest=await get_review(tenant,self.version);self.assertFalse(latest.editable)
            self.assertEqual(latest.review_items[0].text,'核对后 COD 24 mg/L')
            self.assertEqual(latest.review_items[0].location,'第 3 页')
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):asyncio.run(run())


    def effective_sources(self, scopes=None, eid=None, sub=None):
        with self.connection('f1_api', eid=eid, sub=sub) as c:
            return [x[0] for x in c.execute('SELECT * FROM f1.read_effective_material_sources(%s)',(scopes or [self.scope],)).fetchall()]

    def test_effective_review_overrides_and_revoke_never_falls_back(self):
        from platform_foundation.f1.features.evidence.effective import decode_source
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload
        self.complete_review_base()
        pick=lambda: next(x for x in self.effective_sources() if x['document_version_id']==str(self.version))
        original=decode_source(pick());self.assertIn('42 mg/L', original.fragments[0].body)
        self.assertIsNone(getattr(original.fragments[0].locator,'page_number',None))
        request,payload=self.reviewed_payload();receipt=self.write_review_payload(payload)
        reviewed=decode_source(pick());self.assertEqual(reviewed.evidence_kind,'review')
        self.assertEqual(str(reviewed.evidence_revision_id),receipt['id'])
        self.assertEqual(reviewed.fragments[0].body,'已核对修订 0')
        self.assertNotEqual(reviewed.fragments[0].id,original.fragments[0].id)
        revoke=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=receipt['id'],action='revoke')
        self.write_review_payload(build_review_payload(self.review_source(),revoke))
        revoked=decode_source(pick());self.assertEqual(revoked.evidence_kind,'revoked');self.assertEqual(revoked.fragments,())
        tampered=copy.deepcopy(pick());tampered['fragments']=[payload['fragments'][0]]
        with self.assertRaises(ValueError):decode_source(tampered)

    def test_effective_scope_member_audience_and_worker_boundaries(self):
        self.complete_review_base()
        with STACK._bootstrap() as c:
            scopes={str(kind):sid for sid,kind in c.execute("SELECT id,client_account_id FROM f1.material_knowledge_scope WHERE enterprise_id=%s AND scope_kind='client'",(self.eid,)).fetchall()}
        bound=scopes[str(WORLD.bound_client_id)];other=scopes[str(WORLD.race_client_id)]
        rows=self.effective_sources([self.scope,bound],eid=WORLD.enterprise_b,sub=WORLD.client_sub)
        self.assertIn(str(self.version),[r['document_version_id'] for r in rows])
        self.assertEqual(self.effective_sources([self.scope,other],eid=WORLD.enterprise_b,sub=WORLD.client_sub),[])
        self.assertEqual(self.effective_sources([self.scope,uuid.uuid4()]),[])
        with self.connection() as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
            c.execute('SELECT * FROM f1.read_effective_material_sources(%s)',([self.scope],)).fetchall()
        with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertEqual(self.effective_sources(),[])

    def test_effective_search_reaches_later_fragment_and_rejects_tampered_identity(self):
        from platform_foundation.f1.features.material_rag.service import _effective_local_records
        from platform_foundation.f1.features.material_rag.contracts import RetrievalContext
        from platform_foundation.f1.features.material_rag.local_extractive import rank_local_evidence
        from platform_foundation.f1.features.evidence.effective import decode_source
        self.raw=package(''.join(p('常规记录 '+str(i)) for i in range(300))+p('排口编号 ZX9988 COD 17 mg/L'))
        self.result=extract(self.raw);self.version=self.source(self.result);self.complete_review_base()
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        context=RetrievalContext(enterprise_id=self.eid,kind='service_provider',client_account_id=None,scope_ids=(self.scope,))
        records=asyncio.run(_effective_local_records(tenant,context))
        hits=rank_local_evidence('ZX9988',records,limit=1);self.assertEqual(len(hits),1)
        citation=hits[0].to_citation_dict();self.assertEqual(citation['page_number'],None)
        self.assertEqual(citation['locator']['body_index'],301);self.assertIn('17 mg/L',citation['snippet'])
        row=next(x for x in self.effective_sources() if x['document_version_id']==str(self.version))
        for key,value in [('document_version_id',str(uuid.uuid4())),('locator',{'schema_version':2,'kind':'docx_block','body_index':1})]:
            changed=copy.deepcopy(row);changed['fragments'][-1][key]=value
            with self.assertRaises(ValueError):decode_source(changed)


    def test_effective_report_freezes_review_and_preserves_it_after_revoke(self):
        from platform_foundation.f1.features.analysis_reports import service,repository,delivery_repository,worker
        from platform_foundation.f1.features.evidence.review import ReviewWriteIn,build_review_payload
        audience_sub='original-client-'+uuid.uuid4().hex
        from platform_foundation.f1.database import session_scope
        self.eid=uuid.uuid4();self.scope=uuid.uuid4();client_scope=uuid.uuid4();client_id=uuid.uuid4();audience_id=uuid.uuid4()
        with STACK._bootstrap() as c:
            c.execute("INSERT INTO f1.enterprise(id,name,license_no,business_kind) VALUES(%s,'Typed report','TEST','service_provider'),(%s,'Typed audience','TEST','client')",(self.eid,audience_id))
            local_seed._ensure_binding(c,local_seed.Binding(self.sub,self.sub,self.sub+'@example.invalid',self.eid,'enterprise_admin'))
            c.execute("INSERT INTO f1.crm_account(id,enterprise_id,display_name,stage,created_by_user_id) VALUES(%s,%s,'Typed customer','active',%s)",(client_id,self.eid,self.actor))
            c.execute("INSERT INTO f1.material_knowledge_scope(id,enterprise_id,scope_kind,client_account_id) VALUES(%s,%s,'service_provider',NULL),(%s,%s,'client',%s)",(self.scope,self.eid,client_scope,self.eid,client_id))
            c.execute("INSERT INTO f1.analysis_report_client_audience(id,enterprise_id,client_account_id,audience_enterprise_id,status) VALUES(%s,%s,%s,%s,'active')",(uuid.uuid4(),self.eid,client_id,audience_id))
            local_seed._ensure_binding(c,local_seed.Binding(audience_sub,audience_sub,'original-audience@example.invalid',audience_id,'enterprise_admin'))
        self.version=self.source(self.result);self.historical_pdf_base()
        self.scope=client_scope
        # Real parsers and restricted finalize; storage/OCR ports remain synthetic.
        from tests.test_xlsx_native_evidence import ORIGINAL,extract as extract_sheet
        from tests.test_jpeg_native_evidence import JpegNativeEvidence,jpeg
        from platform_foundation.f1.features.evidence.envelopes import build_native_payload
        image_case=JpegNativeEvidence();image_case.setUp()
        try:
            raw_image=jpeg(6);image_result,_=image_case.extract(raw_image)
        finally:image_case.doCleanups()
        for fmt,raw,result in [('xlsx',ORIGINAL.read_bytes(),extract_sheet(ORIGINAL.read_bytes())),('jpeg',raw_image,image_result)]:
            job=self.format_job(raw,fmt,result)
            payload=build_native_payload(result,source_format=fmt,**{key:uuid.UUID(job[key]) for key in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','revision_id')})
            self.finalize(job,payload)
        self.version=self.source(self.result);self.complete_review_base()
        request,payload=self.reviewed_payload();receipt=self.write_review_payload(payload)
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def run():
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as session:
                sources=await repository.load_eligible_sources(session,self.eid,client_id)
                before=repository.fingerprint_for(self.eid,client_id,sources)
            created=await service.create_report(tenant,client_id,uuid.uuid4());report_id=uuid.UUID(created['report_id'])
            queued=await service.generate_report(tenant,client_id,report_id,uuid.uuid4())
            did=delivery_repository.delivery_id_for(uuid.UUID(queued['job_id']))
            claims=await delivery_repository.claim_due_deliveries();claim=next(c for c in claims if c.id==did)
            await worker._process_generation_delivery(claim.id,claim.dispatch_token)
            status=await service.job_status(tenant,uuid.UUID(queued['job_id']));self.assertEqual(status['status'],'draft',status)
            version=uuid.UUID(queued['version_id']);frozen=await service.version_detail(tenant,version)
            self.assertEqual({c['locator']['kind'] for c in frozen['citations']},{'pdf_page','docx_block','xlsx_cells','image'})
            citations=[c for c in frozen['citations'] if c['document_version_id']==str(self.version)]
            self.assertTrue(citations);self.assertTrue(all(c['page_number'] is None for c in citations))
            self.assertTrue(all(c['evidence_revision_id']==receipt['id'] for c in citations))
            for citation in frozen['citations']:
                with self.connection('f1_api') as c:
                    original=c.execute('SELECT f1.read_citation_original(%s,NULL,NULL,NULL)',(citation['citation_id'],)).fetchone()[0]
                    self.assertEqual(original['locator'],citation['locator'])
                    self.assertEqual(original['document_version_id'],citation['document_version_id'])
                with self.connection('f1_api',eid=audience_id,sub=audience_sub) as c:
                    self.assertIsNone(c.execute('SELECT f1.read_citation_original(%s,NULL,NULL,NULL)',(citation['citation_id'],)).fetchone()[0])
            html=await service.version_artifact(tenant,version);self.assertIn('正文块'.encode(),html.body);self.assertNotIn(b'None',html.body)
            pdf=await service.version_artifact_pdf(tenant,version);self.assertTrue(pdf.body.startswith(b'%PDF-'))
            from platform_foundation.f1 import qa_service
            from platform_foundation.f1.features.material_rag.service import derive_retrieval_context
            context=await derive_retrieval_context(tenant,client_id);qid=uuid.uuid4()
            answer=await qa_service.ask_material_question('已核对修订',qid,tenant,context)
            self.assertTrue(answer.citations)
            self.assertEqual((await qa_service.ask_material_question('已核对修订',qid,tenant,context)).to_dict(),answer.to_dict())
            revoke=ReviewWriteIn(request_id=uuid.uuid4(),expected_review_revision_id=receipt['id'],action='revoke')
            self.write_review_payload(build_review_payload(self.review_source(),revoke))
            with self.assertRaises(qa_service.RequestIdConflict):
                await qa_service.ask_material_question('已核对修订',qid,tenant,context)
            same=await service.version_detail(tenant,version);self.assertEqual(frozen,same)
            # Frozen report citations retain access to their original even
            # after the current review is revoked. Client access is gated by
            # the actual publication/withdrawal transitions on this report.
            await service.apply_transition(tenant,version,'submit')
            await service.apply_transition(tenant,version,'approve',checklist={'citation_traceable':True,'risks_complete':True,'usage_boundary':True})
            await service.apply_transition(tenant,version,'publish')
            for citation in frozen['citations']:
                with self.connection('f1_api',eid=audience_id,sub=audience_sub) as c:
                    original=c.execute('SELECT f1.read_citation_original(%s,NULL,NULL,NULL)',(citation['citation_id'],)).fetchone()[0]
                    self.assertEqual(original['locator'],citation['locator'])
            await service.apply_transition(tenant,version,'withdraw')
            with self.connection('f1_api',eid=audience_id,sub=audience_sub) as c:
                self.assertIsNone(c.execute('SELECT f1.read_citation_original(%s,NULL,NULL,NULL)',(citations[0]['citation_id'],)).fetchone()[0])
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as session:
                sources=await repository.load_eligible_sources(session,self.eid,client_id)
                self.assertNotEqual(repository.fingerprint_for(self.eid,client_id,sources),before)
                self.assertNotIn(self.version,[x.document_version_id for x in sources])
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1','F1_REPORT_WORKER_RESTRICTED':'1'}):asyncio.run(run())

    def test_catalog_roles_and_registered_source_identity(self):
        j=self.register();again=self.register();self.assertEqual(j,again)
        self.assertEqual(j['source_sha256'],self.result.source_sha256)
        self.assertEqual(j['source_etag'],'synthetic-original-etag')
        self.assertEqual(j['state'],'pending')
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT version_num FROM f1.alembic_version').fetchone()[0],'f1_0044')
            self.assertEqual(c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='f1' AND c.relname IN('material_evidence_job','material_extraction_revision','material_evidence_fragment') AND c.relrowsecurity AND c.relforcerowsecurity").fetchone()[0],3)
            self.assertEqual(c.execute("SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname='f1_material_evidence_definer'").fetchone(),(False,False,False))
    def test_complete_encrypt_roundtrip_and_idempotent_lost_receipt(self):
        self.register();j=self.claim();payload=self.payload(j)
        self.assertEqual(self.finalize(j,payload),uuid.UUID(j['revision_id']))
        self.assertEqual(self.finalize(j,self.payload(j)),uuid.UUID(j['revision_id']))
        self.assertEqual(self.count(j),(1,3))
        with STACK._bootstrap() as c:
            c.row_factory=psycopg.rows.dict_row
            rows=c.execute('SELECT f.*,r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.source_sha256,r.parser_version,r.extraction_contract FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id WHERE r.id=%s ORDER BY f.ordinal',(j['revision_id'],)).fetchall()
            self.assertEqual([decrypt_fragment(r) for r in rows],['排口 COD 42 mg/L','',self.tail])
            self.assertEqual(rows[0]['body_ciphertext'].hex(),payload['fragments'][0]['body_ciphertext_hex'])
            r=c.execute('SELECT report_source_eligible,nonblank_fragment_count FROM f1.material_extraction_revision WHERE id=%s',(j['revision_id'],)).fetchone();self.assertEqual(r,{'report_source_eligible':True,'nonblank_fragment_count':2})
            self.assertEqual(c.execute("SELECT count(*) n FROM f1.audit_log WHERE action='native.extraction.completed' AND resource_id=%s",(j['revision_id'],)).fetchone()['n'],1)
    def test_partial_persists_actual_counts_and_debts_without_fragments(self):
        partial=extract(package(p(uuid.uuid4().hex)+'<w:p><w:r><w:drawing/></w:r></w:p>'))
        self.version=self.source(partial);self.register();j=self.claim()
        self.assertEqual(self.finalize(j,self.payload(j,partial)),uuid.UUID(j['revision_id']))
        self.assertEqual(self.count(j),(1,0))
        with STACK._bootstrap() as c:
            r=c.execute('SELECT coverage_state,expected_block_count,processed_block_count,report_source_eligible,debts FROM f1.material_extraction_revision WHERE id=%s',(j['revision_id'],)).fetchone()
            self.assertEqual(r[:4],('partial',2,2,False));self.assertEqual(len(r[4]),len(partial.debts))
    def test_registration_transaction_rollback_and_replay_rechecks_actor(self):
        with self.connection('f1_api') as c:j=self.register(c=c);c.rollback()
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT count(*) FROM f1.material_evidence_job WHERE id=%s',(j['job_id'],)).fetchone()[0],0)
        j=self.register()
        with STACK._bootstrap() as c:c.execute("UPDATE f1.enterprise_user SET role='auditor' WHERE enterprise_id=%s AND user_id=%s",(self.eid,self.actor))
        with self.assertRaises(psycopg.Error):self.register()
    def test_runtime_dml_ciphertext_and_helper_access_denied(self):
        self.register();j=self.claim();self.finalize(j,self.payload(j))
        for role in ('f1_api','f1_worker'):
            for statement in ["SELECT body_ciphertext FROM f1.material_evidence_fragment", "SELECT f1.native_source(NULL)","UPDATE f1.material_evidence_job SET state='pending'", "DELETE FROM f1.material_extraction_revision", "INSERT INTO f1.material_evidence_fragment DEFAULT VALUES"]:
                with self.subTest(role=role,sql=statement),self.connection(role) as c:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute(statement)
                    c.rollback()
        for table in ('material_extraction_revision','material_evidence_fragment'):
            with STACK._bootstrap() as c:
                with self.assertRaisesRegex(psycopg.Error,'NATIVE_REVISION_IMMUTABLE'):c.execute('DELETE FROM f1.'+table)
                c.rollback()
    def test_cross_tenant_registration_and_status_hidden(self):
        j=self.register()
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:
            with self.assertRaises(psycopg.Error):self.register(c=c)
            c.rollback()
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:self.assertEqual(c.execute('SELECT id FROM f1.material_evidence_job').fetchall(),[])
    def test_payload_tampering_rolls_back_all_rows(self):
        self.register();j=self.claim();good=self.payload(j)
        mutations=[('tenant',lambda x:x.update(enterprise_id=str(uuid.uuid4()))),('revision',lambda x:x.update(revision_id=str(uuid.uuid4()))),('source',lambda x:x.update(source_sha256='a'*64)),('count_bool',lambda x:x.update(expected_block_count=True)),('eligible',lambda x:x.update(report_source_eligible=False)),('contract_float',lambda x:x.update(extraction_contract=1.0)),('schema_float',lambda x:x['fragments'][0]['locator'].update(schema_version=2.0)),('count_float',lambda x:x.update(processed_block_count=3.0)),('ordinal',lambda x:x['fragments'][0].update(ordinal=1)),('hash',lambda x:x['fragments'][0].update(body_aad_sha256='0'*64)),('locator_bool',lambda x:x['fragments'][0]['locator'].update(body_index=True)),('locator_extra',lambda x:x['fragments'][0]['locator'].update(page_number=1)),('wrong_id',lambda x:x['fragments'][0].update(id=str(uuid.uuid5(uuid.NAMESPACE_DNS,'other')))),('cipher_format',lambda x:x['fragments'][0].update(body_ciphertext_hex='00')),('blank_lie',lambda x:x['fragments'][1].update(has_nonblank_text=True))]
        for name,mutate in mutations:
            bad=copy.deepcopy(good);mutate(bad)
            with self.subTest(name=name),self.assertRaises(psycopg.Error):self.finalize(j,bad)
            self.assertEqual(self.count(j),(0,0))
        self.assertIsNotNone(self.finalize(j,good))
    def test_two_claimers_lease_renew_expiry_and_old_token_fence(self):
        self.register()
        with ThreadPoolExecutor(max_workers=2) as pool:
            def claim(_):
                with self.connection() as c:return [r[0] for r in c.execute('SELECT * FROM f1.claim_native_extraction_jobs(100,300)').fetchall()]
            rows=sum(list(pool.map(claim,range(2))),[])
        jobs=[r for r in rows if r['document_version_id']==str(self.version)];self.assertEqual(len(jobs),1);old=jobs[0]
        with self.connection() as c:self.assertTrue(c.execute('SELECT f1.renew_native_extraction_lease(%s,%s,300)',(old['job_id'],old['lease_token'])).fetchone()[0])
        with STACK._bootstrap() as c:c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(old['job_id'],))
        self.assertIsNone(self.finalize(old,self.payload(old)))
        new=self.claim();self.assertNotEqual(old['lease_token'],new['lease_token']);self.assertEqual(old['revision_id'],new['revision_id']);self.assertEqual(new['attempt'],2)
        with self.connection() as c:self.assertFalse(c.execute('SELECT f1.finish_native_extraction_failure(%s,%s,%s,NULL)',(old['job_id'],old['lease_token'],'NATIVE_SOURCE_INVALID')).fetchone()[0])
        self.assertIsNotNone(self.finalize(new,self.payload(new)))
    def test_failure_retry_and_terminal_blocked(self):
        self.register();j=self.claim()
        with self.connection() as c:self.assertTrue(c.execute('SELECT f1.finish_native_extraction_failure(%s,%s,%s,1)',(j['job_id'],j['lease_token'],'NATIVE_SOURCE_UNAVAILABLE')).fetchone()[0])
        with STACK._bootstrap() as c:c.execute("UPDATE f1.material_evidence_job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=%s",(j['job_id'],))
        j=self.claim()
        with self.connection() as c:self.assertTrue(c.execute('SELECT f1.finish_native_extraction_failure(%s,%s,%s,NULL)',(j['job_id'],j['lease_token'],'NATIVE_PARSE_REJECTED')).fetchone()[0])
        self.assertEqual(self.count(j),(0,0));self.assertEqual(self.register()['state'],'blocked')
    def test_revocation_first_blocks_finalize_and_finalizer_first_holds_member(self):
        self.register();j=self.claim();payload=self.payload(j)
        with self.connection() as c:
            self.assertIsNotNone(self.finalize(j,payload,c))
            with STACK._bootstrap() as other:
                other.execute("SET LOCAL lock_timeout='150ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):other.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
                other.rollback()
            c.rollback()
        self.assertEqual(self.count(j),(0,0))
        with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertIsNone(self.finalize(j,payload));self.assertEqual(self.count(j),(0,0))
    def test_source_etag_changed_after_claim_blocks_result(self):
        self.register();j=self.claim()
        with STACK._bootstrap() as c:c.execute("UPDATE f1.upload_task SET source_etag='changed' WHERE id=%s",(j['upload_task_id'],))
        self.assertIsNone(self.finalize(j,self.payload(j)));self.assertEqual(self.count(j),(0,0))


    def test_read_claim_blocks_revoked_actor_without_repeated_dispatch(self):
        self.register();j=self.claim()
        with STACK._bootstrap() as c:c.execute("UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s",(self.eid,self.actor))
        self.assertIsNone(asyncio.run(read_claim(uuid.UUID(j['job_id']),uuid.UUID(j['lease_token']))))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT state,reason_code,lease_token FROM f1.material_evidence_job WHERE id=%s',(j['job_id'],)).fetchone(),('blocked','NATIVE_ACTOR_REVOKED',None))
        self.assertEqual(self.count(j),(0,0))

    def test_status_uses_real_metadata_grants_and_source_authorization(self):
        self.register();j=self.claim();self.finalize(j,self.payload(j))
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):
            status=asyncio.run(get_status(tenant,self.version)).model_dump(mode='json',by_alias=True)
            self.assertEqual((status['state'],status['coverage_state'],status['fragment_count'],status['report_source_eligible']),('done','complete',3,True))
            self.assertEqual((status['source_format'],status['parser_version'],status['debts'],status['processing_identity']),('docx',PARSER_VERSION,[],{}))
            for private in ('object_key','lease_token','actor_sub','body_ciphertext','source_etag','source_sha256'):
                self.assertNotIn(private,status)
            foreign=Tenant(enterprise_id=WORLD.enterprise_b,sub=self.sub,roles=(),role='enterprise_admin',business_kind='client')
            with self.assertRaises(Exception) as error:asyncio.run(get_status(foreign,self.version))
            self.assertEqual(getattr(error.exception,'http_status',None),404)
            auditor=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='auditor',business_kind='service_provider')
            with self.assertRaises(Exception) as error:asyncio.run(get_status(auditor,self.version))
            self.assertEqual(getattr(error.exception,'http_status',None),403)
            partial=extract(package(p(uuid.uuid4().hex)+'<w:p><w:r><w:drawing/></w:r></w:p>'))
            self.version=self.source(partial);self.register();j=self.claim();self.finalize(j,self.payload(j,partial))
            status=asyncio.run(get_status(tenant,self.version))
            self.assertEqual((status.state,status.coverage_state,status.processed_block_count,status.report_source_eligible),('done','partial',2,False))

    def test_empty_and_merged_table_locations_survive_db_contract(self):
        result=extract(package('<w:p><w:r><w:t xml:space="preserve">   </w:t></w:r></w:p><w:p/>'))
        self.version=self.source(result);self.register();j=self.claim();self.finalize(j,self.payload(j,result))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT fragment_count,nonblank_fragment_count,report_source_eligible FROM f1.material_extraction_revision WHERE id=%s',(j['revision_id'],)).fetchone(),(2,0,False))
        result=extract(package('<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/><w:gridCol/></w:tblGrid><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'+p(uuid.uuid4().hex)+'</w:tc><w:tc>'+p('第三格')+'</w:tc></w:tr></w:tbl>'))
        self.assertEqual(result.coverage_state,'complete')
        self.version=self.source(result);self.register();j=self.claim();self.finalize(j,self.payload(j,result))
        with STACK._bootstrap() as c:
            locator=c.execute('SELECT locator FROM f1.material_evidence_fragment WHERE extraction_revision_id=%s AND ordinal=1',(j['revision_id'],)).fetchone()[0]
            self.assertEqual((locator['cell_index'],locator['grid_column'],locator['grid_span']),(2,3,1))

    def test_failure_after_revision_insert_leaves_no_partial_commit(self):
        self.register();j=self.claim();payload=self.payload(j)
        with STACK._bootstrap() as c:
            c.execute("CREATE FUNCTION f1.native_test_fail_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'NATIVE_TEST_INSERT_FAILURE'; END $$")
            c.execute("CREATE TRIGGER native_test_fail_insert BEFORE INSERT ON f1.material_evidence_fragment FOR EACH ROW WHEN (NEW.ordinal=1) EXECUTE FUNCTION f1.native_test_fail_insert()")
        try:
            with self.assertRaisesRegex(psycopg.Error,'NATIVE_TEST_INSERT_FAILURE'):self.finalize(j,payload)
            self.assertEqual(self.count(j),(0,0))
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT state FROM f1.material_evidence_job WHERE id=%s',(j['job_id'],)).fetchone()[0],'running')
                self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='native.extraction.completed' AND resource_id=%s",(j['revision_id'],)).fetchone()[0],0)
        finally:
            with STACK._bootstrap() as c:
                c.execute('DROP TRIGGER native_test_fail_insert ON f1.material_evidence_fragment');c.execute('DROP FUNCTION f1.native_test_fail_insert()')
        self.assertIsNotNone(self.finalize(j,payload))

    def wait_for_lock(self,pid):
        deadline=time.monotonic()+5
        with STACK._bootstrap() as c:
            while time.monotonic()<deadline:
                if c.execute("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=%s",(pid,)).fetchone()[0]:return
                c.execute('SELECT pg_stat_clear_snapshot()');threading.Event().wait(.01)
        self.fail('worker did not reach real PostgreSQL lock wait')

    def test_register_task_lock_serializes_finalizer_without_member_job_inversion(self):
        self.register();j=self.claim();payload=self.payload(j)
        with self.connection('f1_api') as registration,self.connection() as worker,ThreadPoolExecutor(max_workers=1) as pool:
            self.register(c=registration)
            pid=worker.info.backend_pid
            final=pool.submit(self.finalize,j,payload,worker)
            try:self.wait_for_lock(pid)
            finally:registration.commit()
            self.assertEqual(final.result(timeout=5),uuid.UUID(j['revision_id']));worker.commit()
        self.assertEqual(self.count(j),(1,3))

    def test_revoke_commit_while_finalizer_waits_rechecks_current_membership(self):
        self.register();j=self.claim();payload=self.payload(j)
        with STACK._bootstrap() as revoker,self.connection() as worker,ThreadPoolExecutor(max_workers=1) as pool:
            revoker.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
            final=pool.submit(self.finalize,j,payload,worker)
            try:self.wait_for_lock(worker.info.backend_pid)
            finally:revoker.commit()
            self.assertIsNone(final.result(timeout=5));worker.commit()
        self.assertEqual(self.count(j),(0,0))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_evidence_job WHERE id=%s',(j['job_id'],)).fetchone(),('blocked','NATIVE_ACTOR_REVOKED'))

    def test_lease_expiry_after_source_lock_wait_uses_current_clock(self):
        self.register();j=self.claim();payload=self.payload(j)
        with STACK._bootstrap() as holder,self.connection() as worker,ThreadPoolExecutor(max_workers=1) as pool:
            holder.execute('SELECT id FROM f1.upload_task WHERE id=%s FOR UPDATE',(j['upload_task_id'],))
            with STACK._bootstrap() as c:c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '300 milliseconds' WHERE id=%s",(j['job_id'],))
            final=pool.submit(self.finalize,j,payload,worker)
            try:
                self.wait_for_lock(worker.info.backend_pid)
                holder.execute('SELECT pg_sleep(0.35)')
            finally:holder.commit()
            self.assertIsNone(final.result(timeout=5));worker.commit()
        self.assertEqual(self.count(j),(0,0))


    def actual_size(self,raw):
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.document d SET size=%s FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id',(len(raw),self.version))
            c.execute('UPDATE f1.upload_task t SET source_size=%s FROM f1.document_version v WHERE v.id=%s AND t.id=v.upload_task_id',(len(raw),self.version))

    def assert_release_atomicity(self,source_format="docx"):
        from platform_foundation.f1 import storage
        from platform_foundation.f1.features.p3 import service
        from platform_foundation.f1.features.evidence import repository
        self.actual_size(self.raw)
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.upload_task t SET quarantine_status='held',released_at=NULL FROM f1.document_version v WHERE v.id=%s AND t.id=v.upload_task_id",(self.version,))
        original=repository.register_in_session
        async def fail_after_registration(session,version,source_format="docx"):
            await original(session,version,source_format)
            raise RuntimeError('NATIVE_RELEASE_AFTER_REGISTER_PROBE')
        def released(**kwargs):
            self.assertEqual(kwargs['expected_sha256'],self.result.source_sha256)
            self.assertEqual(kwargs['expected_size'],len(self.raw))
            self.assertEqual(kwargs['expected_etag'],'synthetic-original-etag')
            return True
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1',
            'REDIS_URL':'redis://127.0.0.1:6379/0'}),patch.object(storage,'release_ingestion_object',side_effect=released) as release:
            with patch.object(repository,'register_in_session',side_effect=fail_after_registration):
                with self.assertRaisesRegex(RuntimeError,'NATIVE_RELEASE_AFTER_REGISTER_PROBE'):
                    asyncio.run(service.act_on_version(tenant,self.version,action='release'))
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT quarantine_status,released_at FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(self.version,)).fetchone(),('held',None))
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_evidence_job WHERE document_version_id=%s',(self.version,)).fetchone()[0],0)
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(self.version,)).fetchone()[0],0)
            asyncio.run(service.act_on_version(tenant,self.version,action='release'))
            from platform_foundation.f1.features.evidence.formats import CONTRACTS
            def registered():
                with self.connection('f1_api') as c:
                    return c.execute('SELECT f1.register_native_extraction_job(%s,%s,%s)',(self.version,*CONTRACTS[source_format])).fetchone()[0]
            job=registered()
            asyncio.run(service.act_on_version(tenant,self.version,action='release'))
            self.assertEqual(registered()['job_id'],job['job_id'])
            self.assertEqual(release.call_count,2)
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM f1.material_evidence_job WHERE document_version_id=%s',(self.version,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM f1.material_rag_job WHERE document_version_id=%s',(self.version,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(self.version,)).fetchone()[0],'pending')

    def ingestion_delivery(self, raw=None, fmt='docx'):
        import hashlib
        from types import SimpleNamespace
        from platform_foundation.f1.features.p3 import delivery_repository
        raw=raw or self.raw
        result=SimpleNamespace(source_sha256=hashlib.sha256(raw).hexdigest())
        version=self.source(result,'docx' if fmt=='pdf' else fmt)
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.upload_task t SET status='pending',object_state='quarantined',processing_stage='received',quarantine_status='held',scan_verdict='queued',preview_status='blocked',released_at=NULL,source_size=%s FROM f1.document_version v WHERE v.id=%s AND t.id=v.upload_task_id",(len(raw),version))
            c.execute("UPDATE f1.document d SET status='pending',size=%s FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(len(raw),version))
            if fmt=='pdf':c.execute("UPDATE f1.document d SET content_type='application/pdf' FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(version,))
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def register():
            d=await delivery_repository.register_delivery(tenant,version)
            return next(x for x in await delivery_repository.claim_due_deliveries() if x.id==d.id)
        return asyncio.run(register()),tenant,raw

    def ingestion_connection(self,delivery):
        c=self.connection('f1_ingestion_worker')
        c.execute("SELECT set_config('f1.ingestion_delivery_id',%s,true),set_config('f1.ingestion_dispatch_token',%s,true)",(str(delivery.id),str(delivery.dispatch_token)))
        return c

    def test_ingestion_gateway_four_formats_real_http_objects_and_immutable_preview(self):
        import base64,hashlib,io,json,socket
        import httpx,uvicorn
        from tests import test_storage_service_runtime as objects
        from tests.test_material_layout_regressions import _pdf
        from tests.test_xlsx_native_evidence import ORIGINAL
        from tests.test_jpeg_native_evidence import jpeg
        from platform_foundation.f1 import source_gateway,storage,database
        from platform_foundation.f1.features.p3 import delivery_worker,processor
        from platform_foundation.f1.features.p3.scanner import ScanResult
        from platform_foundation.f1.features.p3.preview import build_preview,content_addressed_preview
        from platform_foundation.f1.ingestion_context import ingestion_capability
        flags={'F1_INGESTION_WORKER_RESTRICTED':'1','F1_PIPELINE_COORDINATOR_GATEWAY':'1',
            'F1_TASK_SOURCE_GATEWAY':'1','F1_PREVIEW_CONTENT_ADDRESSED':'1','F1_STORAGE_SERVICE_CREDENTIALS':'1',
            'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_INGESTION_DURABLE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1',
            'REDIS_URL':'redis://127.0.0.1:6379/0'}
        objects.setUpModule();server=thread=sock=None
        try:
            uploader=objects.ServiceStorageRuntimeTests().client('api')
            values=dict(zip(('minio_service_user','minio_service_password'),objects.IDENTITIES['ingestion']))
            original_secret=storage.read_f1_secret_text
            def secret(name,**kw):
                if name in values:
                    self.assertNotEqual(threading.get_ident(),threading.main_thread().ident)
                    return values[name]
                return original_secret(name,**kw)
            sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16)
            url='http://127.0.0.1:'+str(sock.getsockname()[1])
            server=uvicorn.Server(uvicorn.Config(source_gateway.app,log_level='critical',access_log=False))
            thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
            with patch.object(storage,'MINIO_ENDPOINT',objects.ENDPOINT),patch.object(storage,'read_f1_secret_text',side_effect=secret),patch.dict(os.environ,flags):
                thread.start();deadline=time.monotonic()+5
                while not server.started and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(server.started)
                original_client=httpx.Client;outer=self;paths=[]
                class LocalTransport(httpx.HTTPTransport):
                    def handle_request(self,req):
                        outer.assertEqual(req.url.host,'source-gateway');outer.assertEqual(req.url.port,8080)
                        paths.append(req.url.path)
                        req.url=req.url.copy_with(host='127.0.0.1',port=sock.getsockname()[1])
                        return super().handle_request(req)
                def local_client(**kwargs):
                    self.assertFalse(kwargs['trust_env']);self.assertFalse(kwargs['follow_redirects'])
                    return original_client(**kwargs,transport=LocalTransport())
                for fmt,raw in [('docx',self.raw),('pdf',_pdf(pages=1,header=True,draw=False)),('xlsx',ORIGINAL.read_bytes()),('jpeg',jpeg())]:
                    with self.subTest(format=fmt):
                        # Registration is API work, before entering the worker capability.
                        with patch.dict(os.environ,{'F1_INGESTION_WORKER_RESTRICTED':'0'}):delivery,tenant,_=self.ingestion_delivery(raw,fmt)
                        with STACK._bootstrap() as c:
                            task,key=c.execute('SELECT t.id,t.object_key FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(delivery.document_version_id,)).fetchone()
                            obj=uploader.put_object(storage.QUARANTINE_BUCKET,key,io.BytesIO(raw),len(raw))
                            c.execute('UPDATE f1.upload_task SET source_etag=%s WHERE id=%s',(obj.etag,task))
                        with patch.object(httpx,'Client',side_effect=local_client),patch.object(processor,'scan_stream',return_value=ScanResult('clean',None,'clamav','1.4.3','123')):
                            asyncio.run(delivery_worker._run_durable_ingestion(delivery.id,delivery.dispatch_token))
                        with STACK._bootstrap() as c:
                            self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_ingestion_delivery WHERE id=%s',(delivery.id,)).fetchone(),('done',None))
                            manifest_sha=c.execute('SELECT preview_sha256 FROM f1.upload_task WHERE id=%s',(task,)).fetchone()[0]
                            self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(delivery.document_version_id,)).fetchone()[0],'pending')
                        manifest_key=storage._preview_object_key(task,str(uuid.uuid5(task,'manifest:'+manifest_sha)),'application/json')
                        response=uploader.get_object(storage.PREVIEW_BUCKET,manifest_key)
                        try:manifest=response.read()
                        finally:response.close();response.release_conn()
                        self.assertEqual(hashlib.sha256(manifest).hexdigest(),manifest_sha)
                        for meta in json.loads(manifest)['units']:
                            self.assertEqual(meta['id'],str(uuid.uuid5(task,f"{meta['ordinal']}:{meta['content_type']}:{meta['sha256']}")))
                        with original_client(timeout=5,trust_env=False) as client:
                            response=client.post(url+'/ingestion/source',json={'delivery_id':str(delivery.id),'dispatch_token':str(delivery.dispatch_token)})
                            self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                self.assertEqual(paths.count('/ingestion/preview'),4);self.assertEqual(paths.count('/ingestion/source'),5)
                # Independent worker process has only its DB key and material key.
                import subprocess,sys,tempfile
                from pathlib import Path
                with patch.dict(os.environ,{'F1_INGESTION_WORKER_RESTRICTED':'0'}):isolated_delivery,_,raw=self.ingestion_delivery()
                with STACK._bootstrap() as c:
                    task,key=c.execute('SELECT t.id,t.object_key FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(isolated_delivery.document_version_id,)).fetchone()
                    obj=uploader.put_object(storage.QUARANTINE_BUCKET,key,io.BytesIO(raw),len(raw))
                    c.execute('UPDATE f1.upload_task SET source_etag=%s WHERE id=%s',(obj.etag,task))
                with tempfile.TemporaryDirectory(prefix='ingestion-http-',dir=STACK.control_dir) as directory:
                    for name in ('f1_ingestion_worker_password','f1_material_rag_key'):
                        dest=Path(directory)/name;dest.write_bytes((STACK.secrets_dir/name).read_bytes());dest.chmod(0o600)
                    env={k:v for k,v in STACK.runtime_env().items() if not k.endswith('_PASSWORD_FILE')}
                    env.update(flags,F1_SECRETS_DIR=directory,F1_PROVIDER_SECRETS_DIR=directory,F1_MATERIAL_RAG_KEY_FILE=str(Path(directory)/'f1_material_rag_key'),F1_MATERIAL_OCR_ENABLED='0')
                    code="""
import asyncio,sys,uuid,httpx
from unittest.mock import patch
from platform_foundation.f1 import database,storage
from platform_foundation.f1.features.p3 import delivery_worker,processor
from platform_foundation.f1.features.p3.scanner import ScanResult
for read in (database._api_dsn,storage._client):
    try:read()
    except RuntimeError:pass
    else:raise AssertionError('FORBIDDEN_RUNTIME_SECRET_PRESENT')
class LocalTransport(httpx.HTTPTransport):
    def handle_request(self,request):
        assert request.url.host=='source-gateway' and request.url.port==8080
        request.url=request.url.copy_with(host='127.0.0.1',port=int(sys.argv[3]))
        return super().handle_request(request)
client=httpx.Client
def local_client(**kwargs):
    assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
    return client(**kwargs,transport=LocalTransport())
with patch.object(httpx,'Client',side_effect=local_client),patch.object(processor,'scan_stream',return_value=ScanResult('clean',None,'clamav','1.4.3','123')):
    asyncio.run(delivery_worker._run_durable_ingestion(uuid.UUID(sys.argv[1]),uuid.UUID(sys.argv[2])))
print('INGESTION_HTTP_NO_API_OR_STORAGE_SECRET=PROCESSED')
"""
                    completed=subprocess.run([sys.executable,'-c',code,str(isolated_delivery.id),str(isolated_delivery.dispatch_token),str(sock.getsockname()[1])],
                        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=40)
                    self.assertEqual(completed.returncode,0,completed.stderr[-2000:]);self.assertIn('INGESTION_HTTP_NO_API_OR_STORAGE_SECRET=PROCESSED',completed.stdout)
                with STACK._bootstrap() as c:
                    self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_ingestion_delivery WHERE id=%s',(isolated_delivery.id,)).fetchone(),('done',None))
                    self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(isolated_delivery.document_version_id,)).fetchone()[0],'pending')
                # A valid capability binds all writes to the DB task and current process token.
                with patch.dict(os.environ,{'F1_INGESTION_WORKER_RESTRICTED':'0'}):delivery,tenant,raw=self.ingestion_delivery()
                async def begin_preview():
                    with ingestion_capability(delivery.id,delivery.dispatch_token):
                        claim=await processor._claim_process(tenant,delivery.document_version_id)
                        await processor._advance_after_scan(tenant,claim,ScanResult('clean',None,'clamav','1.4.3','123'))
                        await processor._advance_to_previewing(tenant,claim)
                        return claim
                claim=asyncio.run(begin_preview())
                first=content_addressed_preview(claim.task_id,build_preview('docx',io.BytesIO(raw)))
                def bundle(result,token=claim.token):return {'delivery_id':str(delivery.id),'dispatch_token':str(delivery.dispatch_token),
                    'process_token':str(token),'manifest':result.payload,'artifacts':[base64.b64encode(u.content).decode() for u in result.units]}
                command=bundle(first)
                with original_client(timeout=10,trust_env=False) as client:
                    changed=copy.deepcopy(command);changed['manifest']['units'][0]['id']=str(uuid.uuid4())
                    badsha=copy.deepcopy(command);badsha['artifacts'][0]=base64.b64encode(b'other bytes').decode()
                    wrongkind=copy.deepcopy(command);wrongkind['manifest']['kind']='image'
                    invalid=[changed,badsha,wrongkind,{**command,'object_key':'another-task/manifest.json'},
                        {**command,'process_token':str(uuid.uuid4())},{**command,'process_token':None},
                        {**command,'dispatch_token':str(uuid.uuid4())},{**command,'artifacts':[]}]
                    for payload in invalid:
                        response=client.post(url+'/ingestion/preview',json=payload)
                        self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'');self.assertEqual(response.headers['cache-control'],'no-store')
                    response=client.post(url+'/ingestion/source',content=b'x'*257)
                    self.assertEqual(response.status_code,404)
                    store=storage.store_ingestion_preview_unit
                    checked=[]
                    def locked_store(**kwargs):
                        if not checked:
                            for sql,params in [('UPDATE f1.upload_task SET source_etag=source_etag WHERE id=%s',(claim.task_id,)),
                                ('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))]:
                                with STACK._bootstrap() as c:
                                    c.execute("SET LOCAL lock_timeout='60ms'")
                                    with self.assertRaises(psycopg.errors.LockNotAvailable):c.execute(sql,params)
                                    c.rollback()
                            checked.append(True)
                        return store(**kwargs)
                    with patch.object(storage,'store_ingestion_preview_unit',side_effect=locked_store):
                        self.assertEqual(client.post(url+'/ingestion/preview',json=command).status_code,204)
                    self.assertEqual(checked,[True])
                    with STACK._bootstrap() as c:c.execute("UPDATE f1.upload_task SET lease_until=clock_timestamp()+interval '150 milliseconds' WHERE id=%s",(claim.task_id,))
                    def late_store(**kwargs):
                        time.sleep(.2)
                        return store(**kwargs)
                    with patch.object(storage,'store_ingestion_preview_unit',side_effect=late_store):
                        response=client.post(url+'/ingestion/preview',json=command)
                        self.assertEqual(response.status_code,404)
                    with STACK._bootstrap() as c:
                        self.assertEqual(c.execute('SELECT processing_stage,preview_sha256 FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone(),('previewing',None))
                    replacement=asyncio.run(begin_preview());self.assertNotEqual(replacement.token,claim.token)
                    second=content_addressed_preview(claim.task_id,build_preview('docx',io.BytesIO(package(p('新预览内容，旧写入不得覆盖。')))))
                    self.assertNotEqual(second.units[0].id,first.units[0].id);self.assertNotEqual(second.sha256,first.sha256)
                    self.assertEqual(client.post(url+'/ingestion/preview',json=bundle(second,replacement.token)).status_code,204)
                    self.assertEqual(client.post(url+'/ingestion/preview',json=command).status_code,404)
                    # Simulate a timed-out remote PUT completing after the next attempt.
                    # Its address is immutable, so the newer manifest and unit remain exact.
                    for unit in first.units:
                        uploader.put_object(storage.PREVIEW_BUCKET,storage._preview_object_key(claim.task_id,unit.id,unit.content_type),io.BytesIO(unit.content),len(unit.content))
                    old_manifest=json.dumps(first.payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
                    uploader.put_object(storage.PREVIEW_BUCKET,storage._preview_object_key(claim.task_id,str(uuid.uuid5(claim.task_id,'manifest:'+first.sha256)),'application/json'),io.BytesIO(old_manifest),len(old_manifest))
                    with patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:values[n] if n in values else original_secret(n,**kw)):
                        current=storage.read_ingestion_preview_manifest(task_id=claim.task_id,expected_sha256=second.sha256)
                        self.assertEqual(json.loads(current),second.payload)
                        for unit in second.units:
                            self.assertEqual(storage.read_ingestion_preview_artifact(task_id=claim.task_id,unit_id=unit.id,content_type=unit.content_type,expected_sha256=unit.sha256,expected_size=len(unit.content)),unit.content)
                    with STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
                    self.assertEqual(client.post(url+'/ingestion/preview',json=bundle(second,replacement.token)).status_code,404)
                # Verify older manifests remain readable after enabling the new path.
                legacy_task=uuid.uuid4();legacy=b'{"kind":"page_text","units":[]}'
                legacy_sha=hashlib.sha256(legacy).hexdigest()
                uploader.put_object(storage.PREVIEW_BUCKET,storage._preview_object_key(legacy_task,'manifest','application/json'),io.BytesIO(legacy),len(legacy))
                with patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:values[n] if n in values else original_secret(n,**kw)):
                    self.assertEqual(storage.read_ingestion_preview_manifest(task_id=legacy_task,expected_sha256=legacy_sha),legacy)
                # A corrupt object at the current address must not fall back to legacy.
                uploader.put_object(storage.PREVIEW_BUCKET,storage._preview_object_key(legacy_task,str(uuid.uuid5(legacy_task,'manifest:'+legacy_sha)),'application/json'),io.BytesIO(b'corrupt'),7)
                with patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:values[n] if n in values else original_secret(n,**kw)):
                    with self.assertRaisesRegex(storage.StorageError,'PREVIEW_IDENTITY_MISMATCH'):
                        storage.read_ingestion_preview_manifest(task_id=legacy_task,expected_sha256=legacy_sha)
        finally:
            if server:server.should_exit=True
            if thread:thread.join(5)
            if sock:sock.close()
            objects.tearDownModule()

    def test_late_upload_failure_preserves_accepted_source_and_audit(self):
        from platform_foundation.f1.features.p3 import service
        from platform_foundation.f1.database import session_scope
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin')
        with STACK._bootstrap() as c:
            key=c.execute('SELECT idempotency_key_sha256 FROM f1.document_version WHERE id=%s',(self.version,)).fetchone()[0]
            c.execute("UPDATE f1.upload_task SET quarantine_status='held',released_at=NULL WHERE id=(SELECT upload_task_id FROM f1.document_version WHERE id=%s)",(self.version,))
        async def stale_failure():
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as session:
                reservation=await service._existing_reservation(session,tenant,idempotency_key_sha256=key)
            await service.mark_quarantine_failed(tenant,reservation)
            return reservation
        reservation=asyncio.run(stale_failure())
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT processing_stage,object_state,quarantine_status FROM f1.upload_task WHERE id=%s',(reservation.task_id,)).fetchone(),('ready','ready','held'))
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='document.quarantine' AND resource_id=%s",(str(self.version),)).fetchone()[0],0)

    def test_late_upload_success_preserves_live_processing_and_terminal_state(self):
        from platform_foundation.f1.features.p3 import service,processor
        from platform_foundation.f1.database import session_scope
        from platform_foundation.f1.ingestion_context import ingestion_capability
        delivery,tenant,raw=self.ingestion_delivery()
        with STACK._bootstrap() as c:key=c.execute('SELECT idempotency_key_sha256 FROM f1.document_version WHERE id=%s',(delivery.document_version_id,)).fetchone()[0]
        async def late_success():
            async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as session:
                reservation=await service._existing_reservation(session,tenant,idempotency_key_sha256=key)
            with ingestion_capability(delivery.id,delivery.dispatch_token):claim=await processor._claim_process(tenant,delivery.document_version_id)
            await service.finalize_quarantine(tenant,reservation,source_etag='synthetic-original-etag',source_size=len(raw))
            return claim,reservation
        claim,reservation=asyncio.run(late_success())
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT processing_stage,lease_token FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone(),('scanning',claim.token))
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='document.quarantine' AND resource_id=%s",(str(delivery.document_version_id),)).fetchone()[0],0)
            c.execute("UPDATE f1.upload_task SET status='done',object_state='ready',processing_stage='ready',scan_verdict='clean',preview_status='ready',lease_token=NULL,lease_owner=NULL,lease_acquired_at=NULL,lease_until=NULL WHERE id=%s",(claim.task_id,))
        asyncio.run(service.finalize_quarantine(tenant,reservation,source_etag='synthetic-original-etag',source_size=len(raw)))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT processing_stage FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone()[0],'ready')

    def test_ingestion_source_sql_binds_process_actor_and_forbids_other_logins(self):
        from platform_foundation.f1.features.p3 import processor
        from platform_foundation.f1.features.p3.scanner import ScanResult
        from platform_foundation.f1.ingestion_context import ingestion_capability
        delivery,tenant,_=self.ingestion_delivery()
        with ingestion_capability(delivery.id,delivery.dispatch_token):claim=asyncio.run(processor._claim_process(tenant,delivery.document_version_id))
        def read(token=None,process=None,preview=False):
            with self.connection('f1_source_reader') as c:
                c.execute("SELECT set_config('f1.enterprise_id',%s,true),set_config('f1.sub','forged',true)",(str(WORLD.enterprise_b),))
                return c.execute('SELECT f1.read_ingestion_task_source(%s,%s,%s,%s)',(delivery.id,token or delivery.dispatch_token,process,preview)).fetchone()[0]
        self.assertEqual(read(process=claim.token)['upload_task_id'],str(claim.task_id))
        self.assertIsNone(read());self.assertIsNone(read(process=uuid.uuid4()));self.assertIsNone(read(process=claim.token,token=uuid.uuid4()))
        self.assertIsNone(read(process=claim.token,preview=True))
        for role in ('f1_api','f1_worker','f1_ingestion_worker','f1_report_worker'):
            with self.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT f1.read_ingestion_task_source(%s,%s,%s,false)',(delivery.id,delivery.dispatch_token,claim.token))
        with self.connection('f1_source_reader') as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
            c.execute('SELECT f1.source_ingestion_context(%s,%s)',(delivery.id,delivery.dispatch_token))
        async def preview():
            with ingestion_capability(delivery.id,delivery.dispatch_token):
                await processor._advance_after_scan(tenant,claim,ScanResult('clean',None,'clamav','1.4.3','123'))
                await processor._advance_to_previewing(tenant,claim)
        asyncio.run(preview())
        self.assertIsNone(read(process=claim.token));self.assertIsNotNone(read(process=claim.token,preview=True))
        with STACK._bootstrap() as c:c.execute("UPDATE f1.upload_task SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(claim.task_id,))
        self.assertIsNone(read(process=claim.token,preview=True))
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.upload_task SET lease_until=clock_timestamp()+interval '10 minutes' WHERE id=%s",(claim.task_id,))
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        self.assertIsNone(read(process=claim.token,preview=True))

    def test_ingestion_login_scopes_direct_sql_and_denies_business_actions(self):
        delivery,_,_=self.ingestion_delivery()
        with self.connection('f1_ingestion_worker') as c:
            self.assertEqual(c.execute('SELECT * FROM f1.document').fetchall(),[])
            self.assertIsNone(c.execute('SELECT f1.ingestion_worker_context(false)').fetchone()[0])
        with self.ingestion_connection(delivery) as c:
            context=c.execute('SELECT f1.ingestion_worker_context(false)').fetchone()[0]
            self.assertEqual(context['document_version_id'],str(delivery.document_version_id))
            self.assertFalse(c.execute("SELECT f1.finish_ingestion_worker_delivery(%s,%s,'done',NULL,NULL)",(delivery.id,delivery.dispatch_token)).fetchone()[0])
            c.execute("SELECT set_config('f1.enterprise_id',%s,true),set_config('f1.sub','forged',true)",(str(WORLD.enterprise_b),))
            self.assertEqual(c.execute('SELECT id FROM f1.document').fetchall(),[(uuid.UUID(context['source_document_id']),)])
            self.assertEqual(c.execute('SELECT id FROM f1.document_version').fetchall(),[(delivery.document_version_id,)])
            self.assertEqual(c.execute('SELECT id FROM f1.user_profile').fetchall(),[(self.actor,)])
            self.assertEqual(c.execute("UPDATE f1.document SET status='failed' WHERE id<>%s RETURNING id",(context['source_document_id'],)).fetchall(),[])
            c.rollback()
        for sql in ('SET ROLE f1_api','SET ROLE f1_worker','SET ROLE f1_ingestion_process_definer',
                    'SELECT * FROM f1.analysis_report','SELECT * FROM f1.material_evidence_fragment',
                    "SELECT f1.ingestion_worker_ready('{}'::jsonb)",
                    'UPDATE f1.enterprise_user SET role=role','DELETE FROM f1.material_analysis',
                    "UPDATE f1.upload_task SET object_key='forged'","UPDATE f1.upload_task SET released_at=clock_timestamp()",
                    "UPDATE f1.material_analysis SET status='confirmed'",'SELECT f1.claim_material_ingestion_deliveries(1,300)'):
            with self.ingestion_connection(delivery) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute(sql)
        for sql in ("UPDATE f1.upload_task SET quarantine_status='released'",
                    "UPDATE f1.document SET status='done'",
                    "SELECT f1.register_ingestion_worker_pipeline()"):
            with self.ingestion_connection(delivery) as c,self.assertRaises(psycopg.Error):c.execute(sql)
        for role in ('f1_api','f1_worker','f1_report_worker','f1_source_reader'):
            with self.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute('SELECT f1.read_ingestion_worker_delivery(%s,%s)',(delivery.id,delivery.dispatch_token))

    def test_ingestion_runtime_docx_and_pdf_without_api_factory(self):
        import io
        from tests.test_material_layout_regressions import _pdf
        from platform_foundation.f1 import database,storage
        from platform_foundation.f1.features.p3 import delivery_worker,processor
        from platform_foundation.f1.features.p3.scanner import ScanResult
        flags={'F1_INGESTION_WORKER_RESTRICTED':'1','F1_PIPELINE_COORDINATOR_GATEWAY':'1',
            'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_INGESTION_DURABLE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1',
            'REDIS_URL':'redis://127.0.0.1:6379/0'}
        for fmt,raw in [('docx',self.raw),('pdf',_pdf(pages=1,header=True,draw=False))]:
            with self.subTest(format=fmt):
                delivery,tenant,raw=self.ingestion_delivery(raw,fmt)
                original=database._get_factory
                def restricted(role):
                    self.assertEqual(role,'f1_ingestion_worker')
                    return original(role)
                with patch.dict(os.environ,flags),patch.object(database,'_get_factory',side_effect=restricted),\
                     patch.object(storage,'open_quarantine_source',side_effect=lambda *a,**kw:io.BytesIO(raw)),\
                     patch.object(processor,'scan_stream',return_value=ScanResult('clean',None,'clamav','1.4.3','123')),\
                     patch.object(processor,'_store_preview'):
                    asyncio.run(delivery_worker._run_durable_ingestion(delivery.id,delivery.dispatch_token))
                with STACK._bootstrap() as c:
                    state=c.execute('SELECT state,reason_code FROM f1.material_ingestion_delivery WHERE id=%s',(delivery.id,)).fetchone()
                    self.assertEqual(state,('done',None))
                    self.assertEqual(c.execute('SELECT processing_stage,quarantine_status FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(delivery.document_version_id,)).fetchone(),('ready','held'))
                    self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(delivery.document_version_id,)).fetchone()[0],'pending')
                    self.assertEqual(c.execute('SELECT count(*) FROM f1.material_analysis WHERE document_version_id=%s',(delivery.document_version_id,)).fetchone()[0],1 if fmt=='pdf' else 0)
                if fmt=='pdf':
                    from platform_foundation.f1.features.p3 import delivery_repository
                    async def rearm():
                        d=await delivery_repository.register_delivery(tenant,delivery.document_version_id,rearm_terminal=True)
                        return next(x for x in await delivery_repository.claim_due_deliveries() if x.id==d.id)
                    resumed=asyncio.run(rearm())
                    with self.ingestion_connection(resumed) as c:
                        existing=c.execute('SELECT id FROM f1.material_analysis').fetchone()[0]
                        with self.assertRaises(psycopg.Error) as denied:
                            c.execute("INSERT INTO f1.material_page_classification(id,enterprise_id,analysis_id,page_number,primary_kind,ocr_required,table_candidate,two_column_candidate,text_character_count,text_confidence_ppm,scan_confidence_ppm,table_confidence_ppm,two_column_confidence_ppm,reason_codes) VALUES(%s,%s,%s,2,'text',false,false,false,50,1000000,0,0,0,'[]')",(uuid.uuid4(),self.eid,existing))
                        self.assertEqual(denied.exception.diag.message_primary,'INGESTION_WRITE_DENIED')
                    with self.ingestion_connection(resumed) as c:
                        with self.assertRaises(psycopg.Error) as denied:
                            c.execute("INSERT INTO f1.audit_log(id,enterprise_id,user_sub,action,resource_type,resource_id,result) VALUES(%s,%s,%s,'material.analysis.created','material_analysis',%s,'ready')",(uuid.uuid4(),self.eid,self.sub,str(existing)))
                        self.assertEqual(denied.exception.diag.message_primary,'INGESTION_WRITE_DENIED')

    def test_ingestion_publish_rolls_back_handoff_and_rejects_changed_source(self):
        from platform_foundation.f1.features.p3 import processor,delivery_worker
        from platform_foundation.f1.features.p3.preview import PreviewResult
        from platform_foundation.f1.features.p3.scanner import ScanResult
        from platform_foundation.f1.features.p3.contracts import IngestionError
        from platform_foundation.f1.features.material_intake import service as intake
        from platform_foundation.f1.ingestion_context import ingestion_capability
        delivery,tenant,_=self.ingestion_delivery()
        flags={'F1_INGESTION_WORKER_RESTRICTED':'1','F1_PIPELINE_COORDINATOR_GATEWAY':'1','F1_LOCAL_ENGINEERING':'1',
            'F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_INGESTION_DURABLE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1','REDIS_URL':'redis://127.0.0.1:6379/0'}
        async def run():
            with ingestion_capability(delivery.id,delivery.dispatch_token):
                claim=await processor._claim_process(tenant,delivery.document_version_id)
                await processor._advance_after_scan(tenant,claim,ScanResult('clean',None,'clamav','1.4.3','123'))
                await processor._advance_to_previewing(tenant,claim)
                preview=PreviewResult('page_text',{},'a'*64,1,())
                original=intake._register_auto_pipeline_delivery_if_enabled
                async def fail_after_handoff(*args,**kwargs):
                    await original(*args,**kwargs)
                    raise RuntimeError('RESTRICTED_HANDOFF_ROLLBACK_PROBE')
                with patch.object(intake,'_register_auto_pipeline_delivery_if_enabled',side_effect=fail_after_handoff):
                    with self.assertRaisesRegex(RuntimeError,'RESTRICTED_HANDOFF_ROLLBACK_PROBE'):
                        await processor._publish_ready(tenant,claim,preview)
                with STACK._bootstrap() as c:
                    self.assertEqual(c.execute('SELECT processing_stage,lease_token FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone(),('previewing',claim.token))
                    self.assertEqual(c.execute('SELECT count(*) FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(delivery.document_version_id,)).fetchone()[0],0)
                    self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='document.version.process' AND resource_id=%s AND result='ready'",(str(delivery.document_version_id),)).fetchone()[0],0)
                    c.execute("UPDATE f1.upload_task SET content_sha256=%s WHERE id=%s",('b'*64,claim.task_id))
                with self.assertRaises(IngestionError) as changed:
                    await processor._publish_ready(tenant,claim,preview)
                self.assertEqual(changed.exception.code,'P3_PROCESS_OWNERSHIP_LOST')
                with STACK._bootstrap() as c:
                    c.execute("UPDATE f1.upload_task SET content_sha256=%s,lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(claim.content_sha256,claim.task_id))
                with self.ingestion_connection(delivery) as c:
                    with self.assertRaises(psycopg.Error) as expired:
                        c.execute("UPDATE f1.upload_task SET error_reason='P3_SOURCE_READ_FAILED' WHERE id=%s",(claim.task_id,))
                    self.assertEqual(expired.exception.diag.message_primary,'INGESTION_WRITE_DENIED')
                # A new processing lease can recover the expired attempt.
                replacement=await processor._claim_process(tenant,delivery.document_version_id)
                self.assertNotEqual(replacement.token,claim.token)
                await processor._advance_after_scan(tenant,replacement,ScanResult('clean',None,'clamav','1.4.3','123'))
                await processor._advance_to_previewing(tenant,replacement)
                await processor._publish_ready(tenant,replacement,preview)
        with patch.dict(os.environ,flags):asyncio.run(run())
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        with patch.dict(os.environ,flags):asyncio.run(delivery_worker._run_durable_ingestion(delivery.id,delivery.dispatch_token))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_ingestion_delivery WHERE id=%s',(delivery.id,)).fetchone(),('blocked','MATERIAL_INGESTION_CAPABILITY_INVALID'))

    def test_ingestion_real_rq_process_has_no_api_database_secret(self):
        import json,subprocess,sys
        from pathlib import Path
        from redis import Redis
        from rq import Queue,Worker
        from platform_foundation.f1.features.p3 import delivery_worker,delivery_queue
        delivery,_,raw=self.ingestion_delivery()
        cid=None;client=None
        try:
            cid=subprocess.check_output(['docker','run','-d','--label','io.anhuan.scope=ingestion-worker-probe',
                '--label','io.anhuan.ingestion-probe='+uuid.uuid4().hex,'-p','127.0.0.1::6379',
                'redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2'],text=True).strip()
            port=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]['NetworkSettings']['Ports']['6379/tcp'][0]['HostPort']
            url='redis://127.0.0.1:'+port+'/0';client=Redis.from_url(url)
            deadline=time.monotonic()+10
            while True:
                try:client.ping();break
                except Exception:
                    if time.monotonic()>deadline:raise
                    time.sleep(.05)
            queue=Queue(delivery_queue.QUEUE_NAME,connection=client)
            # A SIGKILL leaves RQ's old birth record alive. A new process on the
            # same hostname must register without deleting or reusing that record.
            stale=Worker([queue],connection=client,name=delivery_worker._worker_name())
            stale.register_birth()
            replacement=Worker([queue],connection=client,name=delivery_worker._worker_name())
            replacement.register_birth()
            self.assertTrue(client.exists(stale.key))
            self.assertFalse(client.hexists(stale.key,'death'))
            replacement.register_death();stale.register_death()
            job=queue.enqueue(delivery_worker.run_durable_ingestion,str(delivery.id),str(delivery.dispatch_token))
            isolated=STACK.control_dir/'ingestion-runtime';isolated.mkdir(mode=0o700)
            for name in ('f1_ingestion_worker_password','f1_material_rag_key'):
                dest=isolated/name;dest.write_bytes((STACK.secrets_dir/name).read_bytes());dest.chmod(0o600)
            source_path=STACK.control_dir/'ingestion-source.docx';source_path.write_bytes(raw);source_path.chmod(0o600)
            env={k:v for k,v in STACK.runtime_env().items() if not k.endswith('_PASSWORD_FILE')}
            env.update(F1_SECRETS_DIR=str(isolated),F1_PROVIDER_SECRETS_DIR=str(isolated),REDIS_URL=url,
                F1_MATERIAL_RAG_KEY_FILE=str(isolated/'f1_material_rag_key'),F1_INGESTION_WORKER_RESTRICTED='1',
                F1_PIPELINE_COORDINATOR_GATEWAY='1',F1_PIPELINE_CONTINUATIONS_ON_INGESTION='1',
                F1_MATERIAL_ANALYSIS_REPORT_LOCAL='1',F1_MATERIAL_AUTO_PIPELINE_LOCAL='1',
                F1_MATERIAL_RAG_LOCAL_INDEX='1',F1_MATERIAL_RAG_ORCHESTRATION_LOCAL='0',
                F1_MATERIAL_INGESTION_DURABLE_LOCAL='1',F1_LOCAL_ENGINEERING='1',F1_NATIVE_EVIDENCE_LOCAL='1',
                F1_MATERIAL_OCR_ENABLED='0')
            code="""
import hashlib,io,sys
from pathlib import Path
from unittest.mock import patch
from platform_foundation.f1.database import _api_dsn
try: _api_dsn()
except RuntimeError: pass
else: raise AssertionError('INGESTION_API_SECRET_PRESENT')
from redis import Redis
from rq import Queue,SimpleWorker
from platform_foundation.f1 import storage
from platform_foundation.f1.features.p3 import processor
from platform_foundation.f1.features.p3.scanner import ScanResult
from platform_foundation.f1.features.p3.delivery_queue import QUEUE_NAME,REDIS_URL
raw=Path(sys.argv[1]).read_bytes()
def source(key,sha,size,etag):
    assert sha==hashlib.sha256(raw).hexdigest() and size==len(raw)
    return io.BytesIO(raw)
c=Redis.from_url(REDIS_URL)
with patch.object(storage,'open_quarantine_source',side_effect=source),patch.object(processor,'scan_stream',return_value=ScanResult('clean',None,'clamav','1.4.3','123')),patch.object(processor,'_store_preview'):
    SimpleWorker([Queue(QUEUE_NAME,connection=c)],connection=c).work(burst=True,logging_level='CRITICAL')
print('INGESTION_RQ_NO_API_SECRET=PROCESSED')
"""
            result=subprocess.run([sys.executable,'-c',code,str(source_path)],cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True,timeout=40)
            self.assertEqual(result.returncode,0,result.stderr[-2000:]);self.assertIn('INGESTION_RQ_NO_API_SECRET=PROCESSED',result.stdout)
            self.assertEqual(job.get_status(refresh=True).value,'finished')
            with STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_ingestion_delivery WHERE id=%s',(delivery.id,)).fetchone(),('done',None))
                self.assertEqual(c.execute('SELECT state FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(delivery.document_version_id,)).fetchone()[0],'pending')
            print('INGESTION_RQ_NO_API_SECRET=DONE;HANDOFF=PENDING',flush=True)
        finally:
            if client:client.close()
            if cid:subprocess.run(['docker','rm','-f',cid],check=True,capture_output=True,timeout=20)

    def test_ingestion_write_serializes_actor_and_rechecks_lease_after_lock_wait(self):
        delivery,_,_=self.ingestion_delivery()
        with self.ingestion_connection(delivery) as c:
            context=c.execute('SELECT f1.ingestion_worker_context(false)').fetchone()[0]
            c.execute("UPDATE f1.upload_task SET error_reason='P3_SOURCE_READ_FAILED' WHERE id=%s",(context['upload_task_id'],))
            with STACK._bootstrap() as revoker:
                revoker.execute("SET LOCAL lock_timeout='100ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):revoker.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
                revoker.rollback()
            c.rollback()
        with STACK._bootstrap() as holder,self.ingestion_connection(delivery) as worker,ThreadPoolExecutor(max_workers=1) as pool:
            holder.execute('SELECT id FROM f1.upload_task WHERE id=%s FOR UPDATE',(context['upload_task_id'],))
            with STACK._bootstrap() as c:c.execute("UPDATE f1.material_ingestion_delivery SET dispatch_lease_until=clock_timestamp()+interval '250 milliseconds' WHERE id=%s",(delivery.id,))
            future=pool.submit(worker.execute,"UPDATE f1.upload_task SET error_reason='P3_SOURCE_READ_FAILED' WHERE id=%s",(context['upload_task_id'],))
            try:
                self.wait_for_lock(worker.info.backend_pid)
                holder.execute('SELECT pg_sleep(0.35)')
            finally:holder.commit()
            with self.assertRaises(psycopg.Error):future.result(timeout=5)
            worker.rollback()
        with STACK._bootstrap() as c:
            self.assertIsNone(c.execute('SELECT error_reason FROM f1.upload_task WHERE id=%s',(context['upload_task_id'],)).fetchone()[0])
            c.execute("UPDATE f1.material_ingestion_delivery SET dispatch_lease_until=clock_timestamp()+interval '10 minutes' WHERE id=%s",(delivery.id,))
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        with self.ingestion_connection(delivery) as c:
            self.assertEqual(c.execute('SELECT * FROM f1.document').fetchall(),[])
            self.assertEqual(c.execute("UPDATE f1.upload_task SET error_reason='P3_SOURCE_READ_FAILED' RETURNING id").fetchall(),[])

    def test_pipeline_control_http_capability_and_worker_without_database_credentials(self):
        import json,socket,subprocess,sys,tempfile
        from pathlib import Path
        import httpx,uvicorn
        from fastapi import FastAPI
        from platform_foundation.f1.features.material_pipeline import control_gateway,repository,queue
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        async def prepare():
            registered=await repository.register_delivery(tenant,self.version)
            return next(d for d in await repository.claim_due_deliveries() if d.id==registered.id)
        delivery=asyncio.run(prepare())
        command={'delivery_id':str(delivery.id),'dispatch_token':str(delivery.dispatch_token)}
        app=FastAPI();app.include_router(control_gateway.router)
        self.assertNotIn(control_gateway.PATH,app.openapi()['paths'])
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(16)
        port=sock.getsockname()[1];url=f'http://127.0.0.1:{port}'+control_gateway.PATH
        server=uvicorn.Server(uvicorn.Config(app,log_level='critical',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
        flags={'F1_PIPELINE_COORDINATOR_GATEWAY':'1','F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1',
            'REDIS_URL':'redis://127.0.0.1:6379/0'}
        try:
            with patch.dict(os.environ,flags),patch.object(queue,'enqueue_reconcile_stage',return_value=None):
                thread.start()
                deadline=time.monotonic()+5
                while not server.started and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(server.started)
                with httpx.Client(timeout=15,trust_env=False) as client:
                    for change in ({'dispatch_token':str(uuid.uuid4())},{'delivery_id':str(uuid.uuid4())},
                                   {'tenant':str(WORLD.enterprise_b)},{'actor_sub':'forged'},
                                   {'version_id':str(self.version)},{'action':'publish'},
                                   {'dispatch_token':'invalid'}):
                        response=client.post(url,json={**command,**change})
                        self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                        self.assertEqual(response.headers['cache-control'],'no-store')
                    response=client.post(url,content=b'x'*257);self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                    with patch.dict(os.environ,{'F1_PIPELINE_COORDINATOR_GATEWAY':'0'}):
                        self.assertEqual(client.post(url,json=command).status_code,404)
                    # The consumer has no DB or object-store credential. Only
                    # DNS is mapped to this dedicated socket for the test.
                    code="""
import os,socket,sys,uuid
from platform_foundation.f1.database import _api_dsn
from platform_foundation.f1.features.material_pipeline import worker
try:
    _api_dsn()
except RuntimeError:
    pass
else:
    raise AssertionError('API_CREDENTIAL_PRESENT')
original=socket.getaddrinfo
port=int(sys.argv[1])
def resolve(host,service,*args,**kwargs):
    if host==b'api':host='api'
    if host=='api':
        assert int(service)==8001
        host,service='127.0.0.1',port
    return original(host,service,*args,**kwargs)
socket.getaddrinfo=resolve
worker.run_report_stage('legacy','legacy','legacy')
worker.run_reconcile_stage('legacy','legacy','legacy')
worker.run_recovery_sweep('legacy','legacy')
worker.run_durable_delivery(sys.argv[2],sys.argv[3])
print('PIPELINE_HTTP_NO_DB_SECRET=FORWARDED;LEGACY_NUDGES=DISCARDED')
"""
                    with tempfile.TemporaryDirectory(prefix='pipeline-no-secrets-',dir=STACK.control_dir) as empty:
                        env={k:v for k,v in os.environ.items() if not k.endswith('_PASSWORD_FILE')}
                        env['F1_SECRETS_DIR']=empty
                        result=subprocess.run([sys.executable,'-c',code,str(port),str(delivery.id),str(delivery.dispatch_token)],
                            cwd=Path(__file__).resolve().parents[1],env=env,text=True,capture_output=True,timeout=30)
                    self.assertEqual(result.returncode,0,result.stderr[-2000:]);self.assertIn('PIPELINE_HTTP_NO_DB_SECRET=FORWARDED',result.stdout)
                    print(result.stdout.strip(),flush=True)
                    with STACK._bootstrap() as c:
                        row=c.execute('SELECT state,reason_code FROM f1.material_pipeline_delivery WHERE id=%s',(delivery.id,)).fetchone()
                        self.assertEqual(row,('retry_wait','EFFECTIVE_EXTRACTION_PENDING'))
                    self.assertEqual(client.post(url,json=command).status_code,404)
                    with STACK._bootstrap() as c:
                        c.execute("UPDATE f1.material_pipeline_delivery SET state='dispatched',dispatch_token=%s,dispatch_lease_until=clock_timestamp()-interval '1 second',next_attempt_at=NULL,reason_code=NULL WHERE id=%s",(delivery.dispatch_token,delivery.id))
                    self.assertEqual(client.post(url,json=command).status_code,404)
        finally:
            server.should_exit=True;thread.join(5);sock.close()
            self.assertFalse(thread.is_alive())

    def test_ready_preview_native_handoff_is_atomic_and_pdf_waits_for_analysis(self):
        from platform_foundation.f1.features.p3 import processor
        from platform_foundation.f1.features.p3.preview import PreviewResult
        from platform_foundation.f1.features.p3.scanner import ScanResult
        from platform_foundation.f1.features.material_pipeline import repository
        from platform_foundation.f1.features.material_intake.service import _register_auto_pipeline_delivery_if_enabled
        from platform_foundation.f1.database import session_scope
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        flags={'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_RAG_ORCHESTRATION_LOCAL':'0','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1',
            'REDIS_URL':'redis://127.0.0.1:6379/0'}
        for fmt in ('pdf','docx','xlsx','jpeg'):
            with self.subTest(format=fmt):
                version=self.source(self.result,'docx' if fmt=='pdf' else fmt)
                with STACK._bootstrap() as c:
                    c.execute("UPDATE f1.upload_task t SET status='pending',object_state='quarantined',processing_stage='received',quarantine_status='held',scan_verdict='queued',preview_status='blocked',released_at=NULL FROM f1.document_version v WHERE v.id=%s AND t.id=v.upload_task_id",(version,))
                    c.execute("UPDATE f1.document d SET status='pending' FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(version,))
                    if fmt=='pdf':
                        c.execute("UPDATE f1.document d SET content_type='application/pdf' FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(version,))
                async def run():
                    claim=await processor._claim_process(tenant,version)
                    await processor._advance_after_scan(tenant,claim,ScanResult('clean',None,'clamav','1.4.3','123'))
                    await processor._advance_to_previewing(tenant,claim)
                    preview=PreviewResult(kind={'xlsx':'sheet_grid','jpeg':'image'}.get(fmt,'page_text'),payload={},sha256='a'*64,unit_count=1,units=())
                    if fmt=='pdf':
                        with patch.object(repository,'register_delivery_in_session',side_effect=AssertionError('PDF_ANALYSIS_NOT_YET_READY')):
                            await processor._publish_ready(tenant,claim,preview)
                        with STACK._bootstrap() as c:
                            self.assertEqual(c.execute('SELECT processing_stage FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone()[0],'ready')
                            self.assertEqual(c.execute('SELECT count(*) FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(version,)).fetchone()[0],0)
                        return
                    original=repository.register_delivery_in_session
                    async def fail_after_registration(*args,**kwargs):
                        await original(*args,**kwargs)
                        raise RuntimeError('READY_HANDOFF_COMMIT_GAP_PROBE')
                    with patch.object(repository,'register_delivery_in_session',side_effect=fail_after_registration):
                        with self.assertRaisesRegex(RuntimeError,'READY_HANDOFF_COMMIT_GAP_PROBE'):
                            await processor._publish_ready(tenant,claim,preview)
                    with STACK._bootstrap() as c:
                        self.assertEqual(c.execute('SELECT processing_stage,lease_token FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone(),('previewing',claim.token))
                        self.assertEqual(c.execute('SELECT count(*) FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(version,)).fetchone()[0],0)
                        self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE resource_id=%s AND action='document.version.process' AND result='ready'",(str(version),)).fetchone()[0],0)
                    await processor._publish_ready(tenant,claim,preview)
                    # No immediate coordinator/Redis nudge. The DB dispatcher
                    # alone can find exactly this ready version after a crash.
                    claims=await repository.claim_due_deliveries()
                    delivery=next(d for d in claims if d.document_version_id==version)
                    self.assertEqual(delivery.actor_sub,self.sub)
                    # A later PDF analysis hand-off cannot overwrite a live
                    # continuation token, reset attempts or register twice.
                    async with session_scope(role='f1_api',enterprise_id=self.eid,sub=self.sub) as session:
                        await _register_auto_pipeline_delivery_if_enabled(session,tenant,version)
                        await session.commit()
                    replay=await repository.read_delivery_claim(delivery.id,delivery.dispatch_token)
                    self.assertEqual(replay,delivery)
                    with STACK._bootstrap() as c:
                        self.assertEqual(c.execute('SELECT processing_stage,quarantine_status,lease_token FROM f1.upload_task WHERE id=%s',(claim.task_id,)).fetchone(),('ready','held',None))
                        self.assertEqual(c.execute('SELECT count(*) FROM f1.material_pipeline_delivery WHERE document_version_id=%s',(version,)).fetchone()[0],1)
                        self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE resource_id=%s AND action='document.version.process' AND result='ready'",(str(version),)).fetchone()[0],1)
                with patch.dict(os.environ,flags):asyncio.run(run())

    def test_real_release_register_atomicity_and_idempotent_no_change_route(self):
        self.assert_release_atomicity()

    def test_xlsx_jpeg_release_register_atomicity_and_idempotent_replay(self):
        import hashlib
        from types import SimpleNamespace
        from tests.test_xlsx_native_evidence import ORIGINAL
        from tests.test_jpeg_native_evidence import jpeg
        for fmt,raw in [('xlsx',ORIGINAL.read_bytes()),('jpeg',jpeg(6))]:
            with self.subTest(fmt=fmt):
                self.raw=raw;self.result=SimpleNamespace(source_sha256=hashlib.sha256(raw).hexdigest())
                self.version=self.source(self.result,fmt)
                self.assert_release_atomicity(fmt)

    def test_real_worker_parses_same_verified_bytes_and_persists_complete_revision(self):
        from platform_foundation.f1 import storage
        from platform_foundation.f1.features.evidence.worker import run_job
        self.actual_size(self.raw);self.register();j=self.claim()
        def read(key,sha,size):
            self.assertEqual((key,sha,size),(j['object_key'],self.result.source_sha256,len(self.raw)))
            return self.raw
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}),patch.object(storage,'read_released_native_source',side_effect=read) as reader:
            self.assertEqual(asyncio.run(run_job(uuid.UUID(j['job_id']),uuid.UUID(j['lease_token']))),'DONE')
            self.assertEqual(asyncio.run(run_job(uuid.UUID(j['job_id']),uuid.UUID(j['lease_token']))),'STALE')
            reader.assert_called_once()
        self.assertEqual(self.count(j),(1,3))
        with STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT source_sha256,manifest_sha256,coverage_state FROM f1.material_extraction_revision WHERE id=%s',(j['revision_id'],)).fetchone(),(self.result.source_sha256,self.result.manifest_sha256,'complete'))

    def test_ordinary_original_worker_roundtrip_and_effective_style_conflict(self):
        from tests.test_docx_package_compatibility import ORIGINAL, rewrite, child, style
        from platform_foundation.f1 import storage
        from platform_foundation.f1.features.evidence.worker import run_job
        normal = ORIGINAL.read_bytes()
        conflict = rewrite({'word/stylesWithEffects.xml': lambda root:
            child(child(style(root), 'rPr'), 'color', val='FFFFFF')})
        for raw, expected in ((normal, 'complete'), (conflict, 'partial')):
            with self.subTest(coverage=expected):
                result = extract(raw)
                self.version = self.source(result)
                self.actual_size(raw)
                self.register()
                job = self.claim()
                with patch.dict(os.environ, {'F1_LOCAL_ENGINEERING': '1', 'F1_NATIVE_EVIDENCE_LOCAL': '1'}), patch.object(storage, 'read_released_native_source', return_value=raw):
                    self.assertEqual(asyncio.run(run_job(uuid.UUID(job['job_id']), uuid.UUID(job['lease_token']))), 'DONE')
                with STACK._bootstrap() as c:
                    c.row_factory = psycopg.rows.dict_row
                    revision = c.execute('SELECT coverage_state,source_sha256,manifest_sha256,expected_block_count,processed_block_count,report_source_eligible,debts FROM f1.material_extraction_revision WHERE id=%s', (job['revision_id'],)).fetchone()
                    self.assertEqual(revision['coverage_state'], expected)
                    self.assertEqual((revision['expected_block_count'], revision['processed_block_count']), (5, 5))
                    self.assertEqual(revision['report_source_eligible'], expected == 'complete')
                    self.assertEqual(revision['source_sha256'], result.source_sha256)
                    self.assertEqual(revision['manifest_sha256'], result.manifest_sha256)
                    rows = c.execute('SELECT f.*,r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.source_sha256,r.parser_version,r.extraction_contract FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id WHERE r.id=%s ORDER BY f.ordinal', (job['revision_id'],)).fetchall()
                    if expected == 'complete':
                        self.assertEqual([decrypt_fragment(row) for row in rows], ['合成巡检记录', '项目', '结果', '标识', '清楚'])
                        self.assertEqual(rows[-1]['locator'], {'schema_version': 2, 'kind': 'docx_block', 'body_index': 2, 'row_index': 2, 'cell_index': 2, 'grid_column': 2, 'grid_span': 1, 'paragraph_index': 1, 'part': 'word/document.xml'})
                    else:
                        self.assertEqual(rows, [])
                        self.assertIn('DOCX_VISIBILITY_UNRESOLVED', {d['reason_code'] for d in revision['debts']})

    def test_real_worker_partial_and_changed_storage_bytes_fail_closed(self):
        from platform_foundation.f1 import storage
        from platform_foundation.f1.features.evidence.worker import run_job
        raw=package(p(uuid.uuid4().hex)+'<w:p><w:r><w:drawing/></w:r></w:p>')
        partial=extract(raw);self.version=self.source(partial);self.actual_size(raw);self.register();j=self.claim()
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}),patch.object(storage,'read_released_native_source',return_value=raw):
            self.assertEqual(asyncio.run(run_job(uuid.UUID(j['job_id']),uuid.UUID(j['lease_token']))),'DONE')
        self.assertEqual(self.count(j),(1,0))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT coverage_state,processed_block_count,report_source_eligible FROM f1.material_extraction_revision WHERE id=%s',(j['revision_id'],)).fetchone(),('partial',2,False))
        self.version=self.source(extract(package(p(uuid.uuid4().hex))))
        self.register();j=self.claim()
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}),patch.object(storage,'read_released_native_source',return_value=b'wrong bytes'):
            self.assertEqual(asyncio.run(run_job(uuid.UUID(j['job_id']),uuid.UUID(j['lease_token']))),'BLOCKED')
        self.assertEqual(self.count(j),(0,0))
        with STACK._bootstrap() as c:self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_evidence_job WHERE id=%s',(j['job_id'],)).fetchone(),('blocked','NATIVE_SOURCE_INVALID'))

    def test_lease_expiry_during_fragment_insert_rolls_back_revision(self):
        self.register();j=self.claim();payload=self.payload(j)
        with STACK._bootstrap() as c:
            c.execute("CREATE FUNCTION f1.native_test_delay_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.35); RETURN NEW; END $$")
            c.execute("CREATE TRIGGER native_test_delay_insert BEFORE INSERT ON f1.material_evidence_fragment FOR EACH ROW WHEN (NEW.ordinal=0) EXECUTE FUNCTION f1.native_test_delay_insert()")
            c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '200 milliseconds' WHERE id=%s",(j['job_id'],))
        try:
            with self.assertRaisesRegex(psycopg.Error,'NATIVE_LEASE_EXPIRED'):self.finalize(j,payload)
            self.assertEqual(self.count(j),(0,0))
        finally:
            with STACK._bootstrap() as c:
                c.execute('DROP TRIGGER native_test_delay_insert ON f1.material_evidence_fragment');c.execute('DROP FUNCTION f1.native_test_delay_insert()')
        j=self.claim();self.assertIsNotNone(self.finalize(j,self.payload(j)))


    def format_job(self, raw, source_format, result):
        from platform_foundation.f1.features.evidence.formats import CONTRACTS
        self.version=self.source(result,source_format); self.actual_size(raw)
        with self.connection('f1_api') as c:
            c.execute('SELECT f1.register_native_extraction_job(%s,%s,%s)',(self.version,*CONTRACTS[source_format]))
        return self.claim()

    def saved_fragments(self, job):
        with STACK._bootstrap() as c:
            c.row_factory=psycopg.rows.dict_row
            return c.execute('SELECT f.*,r.knowledge_scope_id,r.document_record_id,r.document_version_id,r.source_sha256,r.parser_version,r.extraction_contract FROM f1.material_evidence_fragment f JOIN f1.material_extraction_revision r ON r.id=f.extraction_revision_id WHERE r.id=%s ORDER BY f.ordinal',(job['revision_id'],)).fetchall()

    def test_unicode_canonical_matches_python_including_surrogate_pairs_and_escapes(self):
        from platform_foundation.f1.features.evidence.contracts import canonical_json
        samples=[{'sheet_name':'排口😀"\\\n\t\x7f','x':None,'nested':{'值':'é🦉'},'array':[1,True,None,'😃']}, {}, {'😀':1,'中文':2,'a':3}]
        with STACK._bootstrap() as c:
            for sample in samples:
                self.assertEqual(c.execute('SELECT f1.native_canonical(%s)',(Jsonb(sample),)).fetchone()[0].encode('ascii'),canonical_json(sample))
        for role in ('f1_api','f1_worker'):
            with self.connection(role) as c:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute("SELECT f1.native_ascii_json('秘密')")

    def test_xlsx_real_worker_unicode_locations_encrypted_roundtrip(self):
        from tests.test_xlsx_native_evidence import rewrite,extract,q
        from platform_foundation.f1.features.evidence import worker
        from platform_foundation.f1 import storage
        raw=rewrite({'xl/workbook.xml':lambda root:root.find(q('sheets'))[0].set('name','排口😀"台账')})
        result=extract(raw);self.assertEqual(result.coverage_state,'complete')
        job=self.format_job(raw,'xlsx',result)
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}),patch.object(storage,'read_released_native_source',return_value=raw):
            self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(job['job_id']),uuid.UUID(job['lease_token']))),'DONE')
        rows=self.saved_fragments(job)
        self.assertEqual([decrypt_fragment(row) for row in rows],[b.text for b in result.blocks])
        self.assertEqual(rows[10]['locator']['cell_range'],'A4:C4')
        self.assertEqual(rows[0]['locator']['sheet_name'],'排口😀"台账')
        changed=dict(rows[0]);changed['locator']={**changed['locator'],'sheet_name':'其他台账'}
        with self.assertRaisesRegex(ValueError,'NATIVE_FRAGMENT_IDENTITY_INVALID'):decrypt_fragment(changed)
        self.assertEqual(self.count(job),(1,15))
        _,review=self.reviewed_payload();self.write_review_payload(review)
        from platform_foundation.f1.features.evidence.review import decrypt_review_fragment
        reviewed=self.review_source()['review_fragments'];self.assertEqual(reviewed[0]['locator'],rows[0]['locator'])
        self.assertEqual(decrypt_review_fragment(reviewed[0]),'已核对修订 0')

    def test_xlsx_partial_formula_preserves_debt_and_no_fragments(self):
        from xml.etree import ElementTree as ET
        from tests.test_xlsx_native_evidence import rewrite,extract,q,cell
        from platform_foundation.f1.features.evidence.envelopes import build_native_payload
        raw=rewrite({'xl/worksheets/sheet1.xml':lambda root:ET.SubElement(cell(root,'B2'),q('f')).__setattr__('text','SUM(40,2.5)')})
        result=extract(raw);self.assertEqual(result.coverage_state,'partial')
        job=self.format_job(raw,'xlsx',result)
        payload=build_native_payload(result,source_format='xlsx',**{k:uuid.UUID(job[k]) for k in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','revision_id')})
        self.assertIsNotNone(self.finalize(job,payload));self.assertEqual(self.count(job),(1,0))
        with STACK._bootstrap() as c:
            row=c.execute('SELECT debts,report_source_eligible,processing_identity FROM f1.material_extraction_revision WHERE id=%s',(job['revision_id'],)).fetchone()
            self.assertEqual(row[0],payload['debts']);self.assertEqual(row[1:],(False,{}))

    def test_typed_locator_sql_rejects_forged_or_cross_format_shapes(self):
        from platform_foundation.f1.features.evidence.contracts import XlsxCellsLocator,ImageLocator,DocxBlockLocator
        valid={'docx':DocxBlockLocator(1).to_dict(),'xlsx':XlsxCellsLocator(4294967295,'台账😀','xl/worksheets/data.xml','A1:XFD1048576').to_dict(),'jpeg':ImageLocator(80,40,40,80,'a'*64,6).to_dict()}
        invalid=[('xlsx',{'sheet_id':True}),('xlsx',{'sheet_id':4294967296}),('xlsx',{'sheet_name':'bad/name'}),('xlsx',{'cell_range':'A2:A1'}),('xlsx',{'cell_range':'A1:A1'}),('xlsx',{'cell_range':'XFE1'}),('xlsx',{'sheet_name':None}),('xlsx',{'part':'../data.xml'}),('jpeg',{'exif_orientation':9}),('jpeg',{'source_width':10000,'source_height':10000}),('jpeg',{'source_width':False}),('docx',{'extra':1})]
        with STACK._bootstrap() as c:
            for fmt,loc in valid.items():
                self.assertTrue(c.execute('SELECT f1.native_locator_valid(%s,%s)',(fmt,Jsonb(loc))).fetchone()[0])
                for wrong in set(valid)-{fmt}:
                    self.assertFalse(c.execute('SELECT f1.native_locator_valid(%s,%s)',(wrong,Jsonb(loc))).fetchone()[0])
            for fmt,change in invalid:
                with self.subTest(fmt=fmt,change=change):self.assertFalse(c.execute('SELECT f1.native_locator_valid(%s,%s)',(fmt,Jsonb({**valid[fmt],**change}))).fetchone()[0])

    def test_format_registration_rejects_parser_and_mime_extension_mismatch(self):
        from tests.test_xlsx_native_evidence import ORIGINAL,extract
        from platform_foundation.f1.features.evidence.formats import CONTRACTS
        result=extract(ORIGINAL.read_bytes());self.version=self.source(result,'xlsx')
        with self.assertRaisesRegex(psycopg.Error,'NATIVE_REGISTER_INVALID'):self.register()
        with STACK._bootstrap() as c:
            c.execute("UPDATE f1.document d SET content_type='image/jpeg' FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(self.version,))
        with self.connection('f1_api') as c:
            with self.assertRaisesRegex(psycopg.Error,'NATIVE_SOURCE_UNAVAILABLE'):
                c.execute('SELECT f1.register_native_extraction_job(%s,%s,%s)',(self.version,*CONTRACTS['xlsx']))

    def test_jpeg_worker_saves_orientation_processing_identity_and_retries_transport(self):
        import hashlib
        from pathlib import Path
        import tempfile
        from tests.test_jpeg_native_evidence import jpeg,complete
        from platform_foundation.f1 import storage
        from platform_foundation.f1.features.evidence import worker,jpeg_native
        from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
        raw=jpeg(6)
        with tempfile.TemporaryDirectory() as directory:
            key=Path(directory)/'key';key.write_text('synthetic-key');key.chmod(0o600)
            config=CloudOcrConfig(provider='glm_vision',api_key_file=key,model='synthetic-model',base_url='https://example.invalid')
            with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_OCR_ENABLED':'0'}),patch.object(jpeg_native.CloudOcrConfig,'from_environment',return_value=config),patch.object(storage,'read_released_native_source',return_value=raw):
                result=jpeg_native.extract_jpeg(raw,expected_sha256=hashlib.sha256(raw).hexdigest(),transport=lambda *a:complete('COD 42 mg/L'))
                job=self.format_job(raw,'jpeg',result)
                with patch.object(jpeg_native,'_default_transport',side_effect=OSError('synthetic transport fail')):
                    self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(job['job_id']),uuid.UUID(job['lease_token']))),'RETRY')
                self.assertEqual(self.count(job),(0,0))
                with STACK._bootstrap() as c:
                    self.assertEqual(c.execute('SELECT state,reason_code FROM f1.material_evidence_job WHERE id=%s',(job['job_id'],)).fetchone(),('retry_wait','NATIVE_OCR_UNAVAILABLE'))
                    c.execute("UPDATE f1.material_evidence_job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=%s",(job['job_id'],))
                retry=self.claim();self.assertEqual(retry['revision_id'],job['revision_id'])
                self.assertNotEqual(retry['lease_token'],job['lease_token'])
                with patch.object(jpeg_native,'_default_transport',return_value=complete('COD 42 mg/L')):
                    self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(retry['job_id']),uuid.UUID(retry['lease_token']))),'DONE')
        rows=self.saved_fragments(job);self.assertEqual([decrypt_fragment(row) for row in rows],['COD 42 mg/L'])
        self.assertEqual((rows[0]['locator']['rendered_width'],rows[0]['locator']['rendered_height'],rows[0]['locator']['exif_orientation']),(40,80,6))
        _,review=self.reviewed_payload();self.write_review_payload(review)
        from platform_foundation.f1.features.evidence.review import decrypt_review_fragment
        reviewed=self.review_source()['review_fragments'];self.assertEqual(reviewed[0]['locator'],rows[0]['locator'])
        self.assertEqual(decrypt_review_fragment(reviewed[0]),'已核对修订 0')
        with STACK._bootstrap() as c:
            identity=c.execute('SELECT processing_identity FROM f1.material_extraction_revision WHERE id=%s',(job['revision_id'],)).fetchone()[0]
            self.assertEqual(identity,result.processing_identity)
            self.assertNotIn('api_key_file',identity)
            with self.assertRaisesRegex(psycopg.Error,'NATIVE_REVISION_IMMUTABLE'):
                c.execute("UPDATE f1.material_extraction_revision SET processing_identity='{}' WHERE id=%s",(job['revision_id'],))


    def test_fragment_api_pages_decrypt_and_reject_foreign_or_wrong_revision(self):
        from platform_foundation.f1.features.evidence.status import get_fragments
        self.register();job=self.claim();self.finalize(job,self.payload(job))
        tenant=Tenant(enterprise_id=self.eid,sub=self.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
        with patch.dict(os.environ,{'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1'}):
            first=asyncio.run(get_fragments(tenant,self.version,uuid.UUID(job['revision_id']),limit=2))
            self.assertEqual([item.text for item in first.items],['排口 COD 42 mg/L',''])
            self.assertEqual(first.next_after,1)
            final=asyncio.run(get_fragments(tenant,self.version,uuid.UUID(job['revision_id']),after=first.next_after,limit=2))
            self.assertEqual([item.text for item in final.items],[self.tail]);self.assertIsNone(final.next_after)
            self.assertEqual(first.items[0].location,'正文块 1 · 段落')
            self.assertNotIn('ciphertext',repr(first.model_dump()))
            for version,revision in [(self.version,uuid.uuid4()),(uuid.uuid4(),uuid.UUID(job['revision_id']))]:
                with self.assertRaises(Exception) as caught:asyncio.run(get_fragments(tenant,version,revision))
                self.assertEqual(caught.exception.http_status,404)
        with self.connection('f1_api',eid=WORLD.enterprise_b) as c:
            self.assertIsNone(c.execute('SELECT f1.read_native_extraction_fragments(%s,%s,-1,10)',(self.version,job['revision_id'])).fetchone()[0])
        with self.connection('f1_worker') as c:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT f1.read_native_extraction_fragments(%s,%s,-1,10)',(self.version,job['revision_id']))

    def test_fragment_reader_retains_old_version_and_revocation_stops_access(self):
        self.register();job=self.claim();self.finalize(job,self.payload(job))
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.document_record SET latest_version_no=2 WHERE id=%s',(job['document_record_id'],))
        with self.connection('f1_api') as c:
            self.assertEqual(len(c.execute('SELECT f1.read_native_extraction_fragments(%s,%s,-1,10)',(self.version,job['revision_id'])).fetchone()[0]['items']),3)
        with STACK._bootstrap() as c:
            c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.eid,self.actor))
        with self.connection('f1_api') as c:
            self.assertIsNone(c.execute('SELECT f1.read_native_extraction_fragments(%s,%s,-1,10)',(self.version,job['revision_id'])).fetchone()[0])

if __name__=='__main__':unittest.main()
