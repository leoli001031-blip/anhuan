"""Actual PG, HTTP and MinIO OCR reuse; model responses are synthetic and counted."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

import httpx
import psycopg
from psycopg.types.json import Jsonb
import uvicorn
from platform_foundation.f1 import ocr_cache, source_gateway, storage
from platform_foundation.f1.features.evidence import jpeg_native, worker
from platform_foundation.f1.features.material_intake import cloud_ocr
from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
from tests import test_native_evidence_postgres as native
from tests import test_storage_service_runtime as objects
from tests.test_jpeg_native_evidence import jpeg, complete
from tests.test_material_cloud_ocr import _scanned_pdf, _paint_resource_images, _jpeg, _LONG_TEXT


class Upgrade43Stack(native.PostgresIntegrationStack):
    def _migrate(self):
        from sqlalchemy import create_engine,text
        from sqlalchemy.engine import make_url
        from infra.f1 import local_migrate,migrate_f1
        engine=create_engine(make_url(migrate_f1._bootstrap_dsn()).set(drivername='postgresql+psycopg'))
        try:
            with engine.begin() as c:
                c.exec_driver_sql('SET LOCAL ROLE f0d_migration')
                try:local_migrate._upgrade_f0(c)
                finally:c.exec_driver_sql('RESET ROLE')
                migrate_f1.migrate_with_connection(c,target='f1_0043')
            with engine.begin() as c:
                assert c.execute(text('SELECT version_num FROM f1.alembic_version')).scalar_one()=='f1_0043'
                assert c.execute(text("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid='f1.read_leased_task_source(text,uuid,uuid)'::regprocedure")).scalar_one()=='f1_source_read_definer'
                migrate_f1.migrate_with_connection(c,target='f1_0044')
            with engine.begin() as c:
                migrate_f1.migrate_with_connection(c,target='f1_0044')
                assert c.execute(text("SELECT has_function_privilege('f1_ocr_cache_definer','f1.read_leased_task_source(text,uuid,uuid)','EXECUTE')")).scalar_one()
            print('OCR_CACHE_UPGRADE_0043_TO_0044_AND_REPLAY=PASSED',flush=True)
        finally:engine.dispose()


def setUpModule():
    with patch.object(native,'PostgresIntegrationStack',Upgrade43Stack):native.setUpModule()
    try: objects.setUpModule()
    except BaseException: native.tearDownModule(); raise


def tearDownModule():
    try: objects.tearDownModule()
    finally: native.tearDownModule()


class OcrCacheTests(unittest.TestCase):
    def setUp(self):
        self.h = native.NativePostgresTests(); self.h.setUp()
        temporary = tempfile.TemporaryDirectory(prefix='ocr-cache-test-'); self.addCleanup(temporary.cleanup)
        key = Path(temporary.name)/'key'; key.write_text('synthetic-only'); key.chmod(0o600)
        self.cfg = CloudOcrConfig(provider='glm_vision', api_key_file=key, model='synthetic-model', base_url='https://example.invalid')
        flags = {'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1','F1_MATERIAL_OCR_ENABLED':'0',
            'F1_TASK_SOURCE_GATEWAY':'1',
            'F1_MATERIAL_INGESTION_DURABLE_LOCAL':'1','F1_MATERIAL_RAG_LOCAL_INDEX':'1',
            'F1_MATERIAL_AUTO_PIPELINE_LOCAL':'1','F1_INGESTION_WORKER_RESTRICTED':'0'}
        p=patch.dict(os.environ,flags);p.start();self.addCleanup(p.stop)
        p=patch.object(CloudOcrConfig,'from_environment',return_value=self.cfg);p.start();self.addCleanup(p.stop)
        self.uploader=objects.ServiceStorageRuntimeTests().client('api')
        p=patch.object(storage,'MINIO_ENDPOINT',objects.ENDPOINT);p.start();self.addCleanup(p.stop)
        original_secret=storage.read_f1_secret_text
        values=dict(zip(('minio_service_user','minio_service_password'),objects.IDENTITIES['ingestion']))
        p=patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:values[n] if n in values else original_secret(n,**kw));p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1'});p.start();self.addCleanup(p.stop)
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen(16)
        self.server=uvicorn.Server(uvicorn.Config(source_gateway.app,log_level='critical',access_log=False))
        self.thread=threading.Thread(target=self.server.run,kwargs={'sockets':[self.sock]},daemon=True)
        self.thread.start();deadline=time.monotonic()+5
        while not self.server.started and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(self.server.started);self.addCleanup(self.stop_server)
        self.client_type=httpx.Client;outer=self
        class Transport(httpx.HTTPTransport):
            def handle_request(self,req):
                outer.assertEqual(req.url.host,'source-gateway');outer.assertEqual(req.url.port,8080)
                req.url=req.url.copy_with(host='127.0.0.1',port=outer.sock.getsockname()[1])
                return super().handle_request(req)
        def client(**kwargs):
            self.assertFalse(kwargs['trust_env']);self.assertFalse(kwargs['follow_redirects'])
            return self.client_type(**kwargs,transport=Transport())
        p=patch.object(httpx,'Client',side_effect=client);p.start();self.addCleanup(p.stop)

    def stop_server(self):
        self.server.should_exit=True;self.thread.join(5);self.sock.close()
        self.assertFalse(self.thread.is_alive())

    def jpeg_job(self,raw=None):
        raw=raw or jpeg()
        with patch.dict(os.environ,{'F1_OCR_RESULT_CACHE':'0'}):
            result=jpeg_native.extract_jpeg(raw,expected_sha256=hashlib.sha256(raw).hexdigest(),transport=lambda *a:complete())
        from platform_foundation.f1.features.evidence.formats import CONTRACTS
        self.h.version=self.h.source(result,'jpeg');self.h.actual_size(raw)
        self.put_source(self.h.version,raw)
        with self.h.connection('f1_api') as c:
            c.execute('SELECT f1.register_native_extraction_job(%s,%s,%s)',(self.h.version,*CONTRACTS['jpeg']))
        return self.h.claim(),raw

    def put_source(self,version,raw):
        with native.STACK._bootstrap() as c:
            task,key=c.execute('SELECT t.id,t.object_key FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(version,)).fetchone()
            stored=self.uploader.put_object(storage.QUARANTINE_BUCKET,key,io.BytesIO(raw),len(raw))
            self.uploader.put_object(storage.BUCKET,key,io.BytesIO(raw),len(raw))
            c.execute('UPDATE f1.upload_task SET source_etag=%s WHERE id=%s',(stored.etag,task))

    @contextmanager
    def scope(self,job,kind='native'):
        with patch.dict(os.environ,{'F1_OCR_RESULT_CACHE':'1'}),ocr_cache.task_scope(kind,
            uuid.UUID(str(job['job_id'])),uuid.UUID(str(job['lease_token'])),self.h.eid,self.h.version):
            yield

    def ticket(self,sha,identity='synthetic'):
        return ocr_cache.Ticket(sha,1,{'test_profile':identity})

    def cache_count(self):
        with native.STACK._bootstrap() as c:
            return c.execute('SELECT count(*) FROM f1.material_ocr_result_cache WHERE document_version_id=%s',(self.h.version,)).fetchone()[0]

    def test_sql_http_identity_immutable_encryption_tamper_and_role_denials(self):
        job,raw=self.jpeg_job();sha=hashlib.sha256(raw).hexdigest()
        with self.scope(job):
            ticket=self.ticket(sha);self.assertIsNone(ticket.load())
            self.assertEqual(ticket.save({'text':'已核对 42 mg/L'}),{'text':'已核对 42 mg/L'})
            self.assertEqual(ticket.save({'text':'different result'}),{'text':'已核对 42 mg/L'})
            self.assertEqual(self.cache_count(),1)
            with native.STACK._bootstrap() as c:
                envelope=c.execute('SELECT envelope FROM f1.material_ocr_result_cache WHERE document_version_id=%s',(self.h.version,)).fetchone()[0]
            self.assertNotIn('已核对',json.dumps(envelope,ensure_ascii=False));self.assertNotIn('42 mg/L',json.dumps(envelope))
            for bad in ({**job,'lease_token':str(uuid.uuid4())},{**job,'job_id':str(uuid.uuid4())}):
                with self.scope(bad),self.assertRaises(ocr_cache.OcrCacheError):self.ticket(sha).load()
            with self.assertRaises(ocr_cache.OcrCacheError):self.ticket('f'*64).save({'text':'forged'})
            with ocr_cache.task_scope('native',uuid.UUID(job['job_id']),uuid.UUID(job['lease_token']),native.WORLD.enterprise_b,self.h.version),self.assertRaises(ocr_cache.OcrCacheError):self.ticket(sha).load()
            for role in ('f1_api','f1_worker','f1_ingestion_worker','f1_report_worker','f1_source_reader'):
                with self.h.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):c.execute('SELECT * FROM f1.material_ocr_result_cache')
            for role in ('f1_api','f1_worker','f1_ingestion_worker','f1_report_worker'):
                with self.h.connection(role) as c,self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    c.execute('SELECT f1.leased_ocr_cache(%s,%s,%s,%s,%s,%s,%s,NULL)',('native',job['job_id'],job['lease_token'],self.h.version,sha,1,'a'*64))
            with native.STACK._bootstrap() as c:
                envelope['ciphertext_hex']=envelope['ciphertext_hex'][:-2]+('00' if envelope['ciphertext_hex'][-2:]!='00' else 'ff')
                c.execute('UPDATE f1.material_ocr_result_cache SET envelope=%s WHERE document_version_id=%s',(Jsonb(envelope),self.h.version))
            with self.assertRaisesRegex(ocr_cache.OcrCacheError,'OCR_CACHE_INVALID'):ticket.load()
        with self.client_type(trust_env=False) as client:
            url='http://127.0.0.1:'+str(self.sock.getsockname()[1])+'/ocr-cache'
            for payload in ({'object_key':'foreign'}, {'kind':'native','job_id':job['job_id'],'lease_token':job['lease_token'],'enterprise_id':str(self.h.eid)}):
                response=client.post(url,json=payload);self.assertEqual(response.status_code,404);self.assertEqual(response.content,b'')
                self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.post(url,content=b'x'*(ocr_cache.MAX_WIRE_BYTES+1)).status_code,404)

    def test_jpeg_worker_retry_after_ocr_reuses_result_and_preserves_complete_fragment(self):
        job,raw=self.jpeg_job();model=Mock(return_value=complete('COD 42 mg/L'))
        with patch.dict(os.environ,{'F1_OCR_RESULT_CACHE':'1'}),patch.object(jpeg_native,'_default_transport',model):
            with patch.object(worker.repository,'finalize',side_effect=RuntimeError('synthetic crash after cached OCR')):
                self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(job['job_id']),uuid.UUID(job['lease_token']))),'RETRY')
            self.assertEqual(model.call_count,1);self.assertEqual(self.cache_count(),1);self.assertEqual(self.h.count(job),(0,0))
            with native.STACK._bootstrap() as c:c.execute("UPDATE f1.material_evidence_job SET next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=%s",(job['job_id'],))
            retry=self.h.claim();self.assertNotEqual(retry['lease_token'],job['lease_token'])
            self.assertEqual(asyncio.run(worker.run_job(uuid.UUID(retry['job_id']),uuid.UUID(retry['lease_token']))),'DONE')
            self.assertEqual(model.call_count,1);self.assertEqual(self.h.count(job),(1,1))
        self.assertEqual(native.decrypt_fragment(self.h.saved_fragments(job)[0]),'COD 42 mg/L')
        print('JPEG_OCR_CALLS_FIRST_AND_RETRY=1;ENCRYPTED_FRAGMENT=1',flush=True)

    def test_model_prompt_renderer_configuration_and_epoch_changes_do_not_hit(self):
        job,raw=self.jpeg_job();sha=hashlib.sha256(raw).hexdigest();model=Mock(return_value=complete('42 mg/L'))
        def run(config=None):
            result=jpeg_native.extract_jpeg(raw,expected_sha256=sha,config=config or self.cfg,transport=model)
            self.assertTrue(result.report_source_eligible)
        with self.scope(job):
            run();run();self.assertEqual(model.call_count,1)
            run(replace(self.cfg,model='new-model'));self.assertEqual(model.call_count,2)
            with patch.object(cloud_ocr,'_CLOUD_OCR_PROMPT','新的完整转录提示词'):run()
            self.assertEqual(model.call_count,3)
            original=jpeg_native.render_jpeg
            def changed(*a,**kw):return replace(original(*a,**kw),renderer_version='renderer-revision-test')
            with patch.object(jpeg_native,'render_jpeg',side_effect=changed):run()
            self.assertEqual(model.call_count,4)
            run(replace(self.cfg,request_timeout_seconds=30));self.assertEqual(model.call_count,5)
            with patch.dict(os.environ,{'F1_OCR_CACHE_EPOCH':'2'}):run()
            self.assertEqual(model.call_count,6)
            run();self.assertEqual(model.call_count,6)
            with patch.object(ocr_cache,'code_identity',return_value=['parser-revision-test']):run()
            self.assertEqual(model.call_count,7)
        # Equal bytes in a different version are a fresh cache domain.
        second,_=self.jpeg_job(raw)
        with self.scope(second):run()
        self.assertEqual(model.call_count,8)

    def test_late_or_revoked_lease_cannot_save_and_failed_ocr_is_not_cached(self):
        job,raw=self.jpeg_job();sha=hashlib.sha256(raw).hexdigest()
        with self.scope(job):
            result=jpeg_native.extract_jpeg(raw,expected_sha256=sha,config=self.cfg,transport=lambda *a:complete(''))
            self.assertFalse(result.report_source_eligible);self.assertEqual(self.cache_count(),0)
            result=jpeg_native.extract_jpeg(raw,expected_sha256=sha,config=self.cfg,transport=lambda *a:b'{"choices":[{"message":{"content":"42"},"finish_reason":"length"}]}')
            self.assertFalse(result.report_source_eligible);self.assertEqual(self.cache_count(),0)
            def expired(*args):
                with native.STACK._bootstrap() as c:c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(job['job_id'],))
                return complete('42 mg/L')
            result=jpeg_native.extract_jpeg(raw,expected_sha256=sha,config=self.cfg,transport=expired)
            self.assertFalse(result.report_source_eligible);self.assertTrue(result.retryable);self.assertEqual(self.cache_count(),0)
            with native.STACK._bootstrap() as c:
                c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '300 seconds' WHERE id=%s",(job['job_id'],))
                c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.h.eid,self.h.actor))
            with self.assertRaises(ocr_cache.OcrCacheError):self.ticket(sha).save({'text':'42 mg/L'})
            self.assertEqual(self.cache_count(),0)

    def prepare_pdf(self):
        raw=_paint_resource_images(_scanned_pdf(_jpeg()))
        delivery,tenant,_=self.h.ingestion_delivery(raw,'pdf');self.h.version=delivery.document_version_id
        with native.STACK._bootstrap() as c:
            key=uuid.uuid4().hex+'.pdf'
            c.execute('UPDATE f1.upload_task t SET object_key=%s FROM f1.document_version v WHERE v.id=%s AND t.id=v.upload_task_id',(key,self.h.version))
            c.execute('UPDATE f1.document d SET object_key=%s FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id',(key,self.h.version))
        self.put_source(self.h.version,raw)
        with native.STACK._bootstrap() as c:
            record,task=c.execute('SELECT document_record_id,upload_task_id FROM f1.document_version WHERE id=%s',(self.h.version,)).fetchone()
            c.execute("UPDATE f1.upload_task SET status='done',object_state='ready',processing_stage='ready',scan_verdict='clean',preview_status='ready',preview_unit_count=1 WHERE id=%s",(task,))
        return raw,delivery,tenant,record,task

    def test_pdf_analysis_to_local_index_reuses_real_rendered_page(self):
        from platform_foundation.f1.features.p3 import processor
        from platform_foundation.f1.ingestion_context import ingestion_capability
        from platform_foundation.f1.features.material_pipeline import local_index
        raw,delivery,tenant,record,task=self.prepare_pdf()
        from platform_foundation.f1.features.material_intake.service import persist_ocr_checkpoint
        from platform_foundation.f1.features.material_intake.ocr import OcrPageResult
        old_text='旧提示词结果不得复用' * 8
        old=OcrPageResult(1,old_text,'applied','OCR_APPLIED',True,len(old_text),source_unit_id='a'*64,parser_backend=cloud_ocr.CLOUD_OCR_PARSER_BACKEND)
        asyncio.run(persist_ocr_checkpoint(tenant,document_version_id=self.h.version,source_sha256=hashlib.sha256(raw).hexdigest(),expected_page_count=1,result=old))
        model=Mock(return_value=complete(_LONG_TEXT))
        with patch.dict(os.environ,{'F1_OCR_RESULT_CACHE':'1','F1_INGESTION_WORKER_RESTRICTED':'1'}),patch.object(cloud_ocr,'_default_transport',model):
            async def analyze():
                with ingestion_capability(delivery.id,delivery.dispatch_token):
                    await processor.retry_ready_material_analysis(tenant,self.h.version)
            asyncio.run(analyze())
            self.assertEqual(model.call_count,1);self.assertEqual(self.cache_count(),1)
            with native.STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT status FROM f1.material_analysis WHERE document_version_id=%s',(self.h.version,)).fetchone()[0],'ready')
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_ocr_checkpoint WHERE document_version_id=%s',(self.h.version,)).fetchone()[0],0)
                c.execute("UPDATE f1.upload_task SET quarantine_status='released',released_at=clock_timestamp() WHERE id=%s",(task,))
                c.execute("UPDATE f1.document d SET status='done' FROM f1.document_version v WHERE v.id=%s AND d.id=v.source_document_id",(self.h.version,))
                jid=uuid.uuid4()
                c.execute("INSERT INTO f1.material_rag_job(id,enterprise_id,knowledge_scope_id,document_record_id,document_version_id,upload_task_id,source_sha256,action,idempotency_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,'index',%s)",(jid,self.h.eid,self.h.scope,record,self.h.version,task,hashlib.sha256(raw).hexdigest(),uuid.uuid4().hex*2))
            with patch.dict(os.environ,{'F1_INGESTION_WORKER_RESTRICTED':'0'}):
                result=asyncio.run(local_index.run_local_index_job(jid,worker_id='ocr-reuse-test'))
            with native.STACK._bootstrap() as c:
                state=c.execute('SELECT status,error_reason FROM f1.material_rag_job WHERE id=%s',(jid,)).fetchone()
            self.assertEqual(result.kind,'DONE',state);self.assertEqual(model.call_count,1)
            with native.STACK._bootstrap() as c:
                self.assertGreater(c.execute('SELECT count(*) FROM f1.material_rag_unit WHERE document_version_id=%s',(self.h.version,)).fetchone()[0],0)
        print('PDF_ANALYSIS_PLUS_INDEX_MODEL_CALLS=1;SOURCE_AND_RENDER_REAL=1',flush=True)

    def test_write_expiry_rolls_back_and_source_change_cannot_reuse(self):
        job,raw=self.jpeg_job();sha=hashlib.sha256(raw).hexdigest()
        with native.STACK._bootstrap() as c:
            c.execute("CREATE FUNCTION f1.ocr_cache_test_delay() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(.25); RETURN NEW; END $$")
            c.execute('CREATE TRIGGER ocr_cache_test_delay AFTER INSERT ON f1.material_ocr_result_cache FOR EACH ROW EXECUTE FUNCTION f1.ocr_cache_test_delay()')
            c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '180 milliseconds' WHERE id=%s",(job['job_id'],))
        try:
            with self.scope(job),self.assertRaises(ocr_cache.OcrCacheError):self.ticket(sha).save({'text':'42 mg/L'})
            self.assertEqual(self.cache_count(),0)
        finally:
            with native.STACK._bootstrap() as c:
                c.execute('DROP TRIGGER ocr_cache_test_delay ON f1.material_ocr_result_cache')
                c.execute('DROP FUNCTION f1.ocr_cache_test_delay()')
        with native.STACK._bootstrap() as c:
            c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '300 seconds' WHERE id=%s",(job['job_id'],))
        with self.scope(job):
            self.ticket(sha).save({'text':'42 mg/L'})
            with native.STACK._bootstrap() as c:c.execute('UPDATE f1.upload_task SET content_sha256=%s WHERE id=%s',('b'*64,job['upload_task_id']))
            with self.assertRaises(ocr_cache.OcrCacheError):self.ticket(sha).load()
        self.assertEqual(self.cache_count(),1)

    def test_fifo_pinned_bundle_reuse_and_profile_invalidation(self):
        from platform_foundation.f1.features.material_intake import ocr
        raw,delivery,tenant,record,task=self.prepare_pdf()
        root=self.cfg.api_key_file.parent
        request=root/'request';response=root/'response';ready=root/'ready'
        os.mkfifo(request,0o600);os.mkfifo(response,0o600);ready.write_bytes(b'ready');ready.chmod(0o600)
        config=ocr.LocalOcrConfig(enabled=True,request_fifo=request,response_fifo=response,ready_file=ready)
        def result(body,header,**kw):
            self.assertEqual(body,raw)
            return ocr.OcrPageResult(1,_LONG_TEXT,'applied','OCR_APPLIED',True,len(_LONG_TEXT),
                confidence_mean_ppm=950000,table_candidate=True,source_unit_id=header['source_unit_id'])
        request_page=Mock(side_effect=result)
        job={'job_id':delivery.id,'lease_token':delivery.dispatch_token}
        with self.scope(job,'pdf-analysis'),patch.object(ocr,'_request_page',request_page):
            first=ocr.ocr_pdf_pages(raw,page_numbers=[1],config=config)
            second=ocr.ocr_pdf_pages(raw,page_numbers=[1],config=config)
            self.assertTrue(first[0].ocr_applied);self.assertEqual(first,second);self.assertEqual(request_page.call_count,1)
            self.assertTrue(second[0].table_candidate);self.assertEqual(second[0].confidence_mean_ppm,950000)
            with patch.object(ocr._F0H_BUNDLE,'configuration_sha256','e'*64):
                changed=ocr.ocr_pdf_pages(raw,page_numbers=[1],config=config)
            self.assertTrue(changed[0].ocr_applied);self.assertEqual(request_page.call_count,2)
            with self.assertRaises(ocr.LocalOcrError):ocr.ocr_pdf_pages(raw,page_numbers=[1],config=config,expected_sha256='f'*64)
            self.assertEqual(request_page.call_count,2)
        self.assertEqual(self.cache_count(),2)
