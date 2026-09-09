"""Fresh PG/MinIO restore of the current candidate; private synthetic fixtures."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from minio import Minio
from minio.datatypes import Part
from psycopg.types.json import Jsonb

from infra.f1 import migrate_f1
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1.maintenance import candidate_backup as backup
from platform_foundation.f1.maintenance.backup_objects import BUCKETS, md5
from platform_foundation.f1.features.evidence.effective import decode_source
from tests import test_native_evidence_postgres as native


def object_fixture(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name('test_storage_service_runtime.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def client(fixture):
    return Minio(fixture.ENDPOINT,access_key=fixture.ROOT_USER,secret_key=fixture.ROOT_PASSWORD,secure=False)


def postgres(stack):
    cid=subprocess.check_output(['docker','ps','-q','--filter',
        'label=com.docker.compose.project='+stack.project_name,'--filter',
        'label=com.docker.compose.service=postgres'],text=True).strip()
    return backup.ContainerPostgres(cid,stack.database)


def storage_identity(fixture):
    return backup.sha(('False\0'+fixture.ENDPOINT+'\0'+fixture.ROOT_USER).encode())


def operator_environment(stack,fixture):
    env=stack.runtime_env()
    for filename,value,variable in [('operator_minio_root_user',fixture.ROOT_USER,'F1_MINIO_ROOT_USER_FILE'),
                                   ('operator_minio_root_password',fixture.ROOT_PASSWORD,'F1_MINIO_ROOT_PASSWORD_FILE')]:
        path=stack.secrets_dir/filename
        if not path.exists():backup.write_private(stack.secrets_dir,filename,value.encode())
        env[variable]=str(path)
    env.update(F1_LOCAL_ENGINEERING='1',F1_MATERIAL_RAG_LOCAL_INDEX='1',MINIO_ENDPOINT=fixture.ENDPOINT)
    return env


class EmptyStack(PostgresIntegrationStack):
    def _migrate(self):
        pass
    def _seed_identities(self):
        pass


class CurrentRestoreTests(unittest.TestCase):
    def test_current_database_private_roles_fragments_review_report_and_multipart_restore(self):
        source_objects=object_fixture('restore_source_objects')
        target_objects=object_fixture('restore_target_objects')
        source=target=None
        source_objects_started=target_objects_started=False
        with tempfile.TemporaryDirectory(prefix='anhuan-current-restore-') as temp:
            directory=Path(temp).resolve();directory.chmod(0o700)
            try:
                native.setUpModule();source=native.STACK
                source_objects.setUpModule();source_objects_started=True
                storage=client(source_objects)
                helper=native.NativePostgresTests();helper.setUp()
                helper.actual_size(helper.raw)
                def put_known_sources():
                    with source._bootstrap() as c:
                        rows=c.execute('SELECT id,object_key FROM f1.upload_task WHERE content_sha256=%s',
                                       (backup.sha(helper.raw),)).fetchall()
                        for task,key in rows:
                            result=storage.put_object(BUCKETS[0],key,io.BytesIO(helper.raw),len(helper.raw))
                            storage.put_object(BUCKETS[1],key,io.BytesIO(helper.raw),len(helper.raw))
                            # Set identity before immutable native jobs are registered.
                            c.execute('UPDATE f1.upload_task SET source_etag=%s,source_size=%s WHERE id=%s',
                                      (result.etag,len(helper.raw),task))
                put_known_sources()
                helper.complete_review_base()
                _,payload=helper.reviewed_payload();review=helper.write_review_payload(payload)
                review_version=helper.version
                reviewed=decode_source(next(r for r in helper.effective_sources() if r['document_version_id']==str(review_version)))
                self.assertEqual(reviewed.fragments[0].body,'已核对修订 0')
                known={backup.sha(helper.raw):helper.raw}
                original_source=helper.source
                original_docx=helper.result
                def actual_source(extraction,source_format='docx'):
                    version=original_source(extraction,source_format)
                    raw=known[extraction.source_sha256]
                    with source._bootstrap() as c:
                        task,document,key=c.execute('SELECT t.id,d.id,t.object_key FROM f1.document_version v JOIN f1.upload_task t ON t.id=v.upload_task_id JOIN f1.document d ON d.id=v.source_document_id WHERE v.id=%s',(version,)).fetchone()
                        result=storage.put_object(BUCKETS[0],key,io.BytesIO(raw),len(raw))
                        storage.put_object(BUCKETS[1],key,io.BytesIO(raw),len(raw))
                        c.execute('UPDATE f1.upload_task SET source_etag=%s,source_size=%s WHERE id=%s',(result.etag,len(raw),task))
                        c.execute('UPDATE f1.document SET size=%s WHERE id=%s',(len(raw),document))
                    return version
                helper.source=actual_source
                from platform_foundation.f1.features.analysis_reports import service as reports,delivery_repository
                original_generate=reports.generate_report
                from tests.test_xlsx_native_evidence import ORIGINAL,extract as extract_sheet
                from tests.test_jpeg_native_evidence import JpegNativeEvidence,jpeg
                from platform_foundation.f1.features.evidence.envelopes import build_native_payload
                from fpdf import FPDF
                from platform_foundation.f1.auth import Tenant
                sheet=ORIGINAL.read_bytes();sheet_result=extract_sheet(sheet);known[backup.sha(sheet)]=sheet
                image_case=JpegNativeEvidence();image_case.setUp()
                try:
                    image_raw=jpeg(6);image_result,_=image_case.extract(image_raw)
                finally:image_case.doCleanups()
                known[backup.sha(image_raw)]=image_raw
                pdf_writer=FPDF();pdf_writer.set_font('Helvetica',size=12)
                for page in range(1,4):
                    pdf_writer.add_page();pdf_writer.cell(0,10,text='Historical synthetic source page '+str(page)+' COD 42 mg/L')
                pdf_raw=bytes(pdf_writer.output());known[backup.sha(pdf_raw)]=pdf_raw
                async def populate_then_generate(*args,**kwargs):
                    for fmt,raw,extraction in [('xlsx',sheet,sheet_result),('jpeg',image_raw,image_result)]:
                        job=helper.format_job(raw,fmt,extraction)
                        payload=build_native_payload(extraction,source_format=fmt,
                            **{key:uuid.UUID(job[key]) for key in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','revision_id')})
                        self.assertTrue(helper.finalize(job,payload))
                    helper.result=replace(original_docx,source_sha256=backup.sha(pdf_raw))
                    helper.version=helper.source(helper.result)
                    # Historical PDF unit contract is deliberately seeded here;
                    # this is restore proof, not a new PDF ingestion/OCR claim.
                    helper.historical_pdf_base()
                    with source._bootstrap() as c:
                        key=c.execute('SELECT t.object_key FROM f1.document_version v JOIN f1.upload_task t ON t.id=v.upload_task_id WHERE v.id=%s',(helper.version,)).fetchone()[0]
                        storage.put_object(BUCKETS[0],key,io.BytesIO(pdf_raw),len(pdf_raw))
                        storage.put_object(BUCKETS[1],key,io.BytesIO(pdf_raw),len(pdf_raw))
                    helper.result=original_docx
                    return await original_generate(*args,**kwargs)
                # Frozen report uses the actual restricted worker SQL/generator.
                with patch.object(reports,'generate_report',side_effect=populate_then_generate):
                    claim,result,delivery=helper.generated_report_claim()
                self.assertTrue(helper.report_finish(claim,result=result))
                self.assertTrue(asyncio.run(delivery_repository.finish_delivery(delivery.id,delivery.dispatch_token,outcome='done')))
                report_counts=helper.report_rows(claim)
                self.assertGreater(report_counts[0],0);self.assertGreater(report_counts[1],0)
                report_tenant=Tenant(enterprise_id=helper.eid,sub=helper.sub,roles=(),role='enterprise_admin',business_kind='service_provider')
                frozen=asyncio.run(reports.version_detail(report_tenant,uuid.UUID(claim['version_id'])))
                self.assertEqual({c['locator']['kind'] for c in frozen['citations']},{'docx_block','xlsx_cells','image','pdf_page'})
                from platform_foundation.f1.features.analysis_reports.artifact import render_html_artifact
                from platform_foundation.f1.features.analysis_reports.pdf_artifact import render_pdf_artifact
                html_hash=render_html_artifact(frozen).sha256;pdf_hash=render_pdf_artifact(frozen).sha256
                with source._bootstrap() as c:
                    report_scopes=[r[0] for r in c.execute('SELECT id FROM f1.material_knowledge_scope WHERE enterprise_id=%s ORDER BY scope_kind',(helper.eid,))]
                formats_before={s['document_version_id']:decode_source(s) for s in helper.effective_sources(report_scopes) if s['available']}
                self.assertEqual({s.source_format for s in formats_before.values()},{'docx','xlsx','jpeg','pdf'})
                # A real nonuniform multipart object cannot be replayed through a
                # single PUT or a uniform guessed part_size without changing ETag.
                upload=storage._create_multipart_upload(BUCKETS[2],'recovery-multipart',
                    {'Content-Type':'application/octet-stream','x-amz-meta-purpose':'restore-proof'})
                parts=[]
                for number,size in enumerate((5,7,3),1):
                    etag=storage._upload_part(BUCKETS[2],'recovery-multipart',bytes([number])*size*1024*1024,None,upload,number)
                    parts.append(Part(number,etag))
                original_etag=storage._complete_multipart_upload(BUCKETS[2],'recovery-multipart',upload,parts).etag
                for name in backup.KEY_NAMES:
                    path=source.secrets_dir/name
                    if not path.exists():
                        backup.write_private(source.secrets_dir,name,secrets.token_bytes(32) if name=='f0i_key' else secrets.token_hex(32).encode())
                key_paths={name:source.secrets_dir/name for name in backup.KEY_NAMES}
                from platform_foundation.f1 import qa_service,invitation
                from platform_foundation.f1.features.material_rag.contracts import RetrievalContext
                business_flags={'F1_LOCAL_ENGINEERING':'1','F1_NATIVE_EVIDENCE_LOCAL':'1',
                                'F1_MATERIAL_QA_LOCAL_EXTRACTIVE':'1','F1_MATERIAL_ANALYSIS_REPORT_LOCAL':'1'}
                context=RetrievalContext(enterprise_id=helper.eid,kind='client',client_account_id=uuid.UUID(claim['client_account_id']),scope_ids=tuple(report_scopes))
                question='COD 42 mg/L';qa_request=uuid.uuid4()
                with patch.dict(os.environ,business_flags):
                    answer=asyncio.run(qa_service.ask_material_question(question,qa_request,report_tenant,context))
                    self.assertIsNone(answer.refusal_reason);self.assertTrue(answer.citations)
                    invite=asyncio.run(invitation.create_invite(helper.eid,'restore-only@example.invalid','partner',user_sub=helper.sub))
                readback_request={'enterprise_id':str(helper.eid),'sub':helper.sub,'scopes':list(map(str,report_scopes)),
                    'client_id':claim['client_account_id'],'report_version':claim['version_id'],
                    'formats_sha256':backup.sha(backup.canonical({k:asdict(v) for k,v in formats_before.items()})),
                    'frozen_sha256':backup.sha(backup.canonical(frozen)),'html_sha256':html_hash,'pdf_sha256':pdf_hash,
                    'question':question,'qa_request':str(qa_request),'qa_sha256':backup.sha(backup.canonical(answer.to_dict())),
                    'invite_token':invite.token,'invite_jti':invite.jti}
                from platform_foundation.f1 import ocr_cache
                from platform_foundation.f1.features.material_rag.security import encrypt_text,decrypt_text
                cache_job=helper.format_job(image_raw,'jpeg',image_result)
                cache_version=helper.version
                with patch.dict(os.environ,{'F1_OCR_RESULT_CACHE':'1'}),ocr_cache.task_scope('native',
                    uuid.UUID(cache_job['job_id']),uuid.UUID(cache_job['lease_token']),helper.eid,cache_version):
                    ticket=ocr_cache.Ticket(backup.sha(image_raw),1,{'restore_synthetic_profile':1})
                    body=ocr_cache.canonical({'text':'42 mg/L'});body_sha=backup.sha(body)
                    cache_aad=ticket._aad(body_sha);cipher,aad_sha=encrypt_text(body.decode(),cache_aad)
                    envelope={'ciphertext_hex':cipher.hex(),'aad_sha256':aad_sha,'body_sha256':body_sha}
                    with helper.connection('f1_source_reader') as c:
                        cached=c.execute('SELECT f1.leased_ocr_cache(%s,%s,%s,%s,%s,1,%s,%s)',
                            ('native',cache_job['job_id'],cache_job['lease_token'],cache_version,
                             backup.sha(image_raw),ticket.input_sha256,Jsonb(envelope))).fetchone()[0]
                    self.assertEqual(cached['status'],'hit')
                with source._bootstrap() as c:
                    c.execute("UPDATE f1.material_evidence_job SET lease_until=clock_timestamp()+interval '45 seconds' WHERE id=%s",(cache_job['job_id'],))
                source.dispose_runtime()
                with source._bootstrap() as c:
                    print('RESTORE_SOURCE_CATALOG='+json.dumps(c.execute("SELECT n.nspname,count(c.oid) FROM pg_namespace n LEFT JOIN pg_class c ON c.relnamespace=n.oid WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' GROUP BY 1 ORDER BY 1").fetchall()),flush=True)
                source_identity=storage_identity(source_objects)
                package=directory/'package'
                with self.assertRaisesRegex(backup.BackupError,'BACKUP_ALL_KEYS_REQUIRED'):
                    backup.backup(source._bootstrap,storage,postgres(source),source_identity,directory/'missing-key',{})
                self.assertFalse((directory/'missing-key').exists())
                wrong_key=directory/'wrong-material-key';backup.write_private(directory,wrong_key.name,b'7f'*32)
                with self.assertRaisesRegex(backup.BackupError,'BACKUP_KEY_OR_CIPHERTEXT_INVALID'):
                    backup.backup(source._bootstrap,storage,postgres(source),source_identity,directory/'wrong-key-backup',
                                  {**key_paths,'f1_material_rag_key':wrong_key})
                self.assertFalse((directory/'wrong-key-backup/manifest.json').exists())
                command=[sys.executable,'-m','infra.f1.analysis_report_backup','backup','--package',str(package),
                    '--postgres-container',postgres(source).container,'--output',str(directory/'backup-receipt.json'),'--writers-stopped']
                process=subprocess.run(command,env=operator_environment(source,source_objects),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
                self.assertEqual(process.returncode,0,process.stdout.decode()+process.stderr.decode())
                receipt=json.loads((directory/'backup-receipt.json').read_bytes())
                manifest_hash=receipt['manifest_sha256']
                manifest=backup.verify_package(package,manifest_hash)
                self.assertEqual(receipt['protected_rls_tables'],55)
                self.assertGreater(receipt['database_tables'],55)
                self.assertEqual(manifest['head'],'f1_0044')
                proof=json.loads((package/'database-proof.json').read_bytes())
                self.assertIn('f1.material_ocr_result_cache',proof['rows'])
                # All negative preflights happen before any destination write.
                with source._bootstrap() as c:
                    source_target=backup.database_identity(c,require_head=True)
                source_target['storage_identity']=source_identity
                with self.assertRaisesRegex(backup.BackupError,'RESTORE_INDEPENDENT_TARGET_REQUIRED'):
                    backup.empty_target(source._bootstrap,storage,postgres(source),source_target,source_identity)
                source.dispose_runtime();native.tearDownModule();source=None
                source_objects.tearDownModule();source_objects_started=False
                # Original processes and tmpfs data are gone before new resources start.
                target=EmptyStack();target.start();native.STACK=target
                target_objects.setUpModule();target_objects_started=True
                destination=client(target_objects);destination_identity=storage_identity(target_objects)
                target_pg=postgres(target)
                origin={**manifest['source'],'storage_identity':manifest['storage_identity']}
                expected=backup.empty_target(target._bootstrap,destination,target_pg,origin,destination_identity)
                self.assertNotEqual(expected['cluster'],origin['cluster'])
                command=[sys.executable,'-m','infra.f1.analysis_report_backup','plan','--package',str(package),
                    '--manifest-sha256',manifest_hash,'--postgres-container',target_pg.container,
                    '--output',str(directory/'restore-plan.json'),'--writers-stopped']
                process=subprocess.run(command,env=operator_environment(target,target_objects),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
                self.assertEqual(process.returncode,0,process.stdout.decode()+process.stderr.decode())
                self.assertEqual(json.loads((directory/'restore-plan.json').read_bytes())['target'],expected)
                def provision(c,database):
                    migrate_f1._provision_roles(c,database,target='f1_0044')
                member=package/'key-f1_material_rag_key';original=member.read_bytes()
                member.write_bytes(b'00'*32)
                with self.assertRaisesRegex(backup.BackupError,'BACKUP_MEMBER_HASH_MISMATCH'):
                    backup.restore(target._bootstrap,destination,target_pg,destination_identity,package,manifest_hash,
                                   directory/'corrupt-run',provision,expected)
                self.assertFalse((directory/'corrupt-run').exists())
                member.write_bytes(original)
                with self.assertRaisesRegex(backup.BackupError,'BACKUP_MANIFEST_TRUST_MISMATCH'):
                    backup.verify_package(package,'f'*64)
                member.unlink()
                with self.assertRaisesRegex(backup.BackupError,'BACKUP_PACKAGE_MEMBERS_MISMATCH'):
                    backup.verify_package(package,manifest_hash)
                backup.write_private(package,member.name,original)
                self.assertEqual(backup.empty_target(target._bootstrap,destination,target_pg,origin,destination_identity),expected)
                failed_target=EmptyStack()
                try:
                    failed_target.start();failed_pg=postgres(failed_target)
                    failed_identity=backup.empty_target(failed_target._bootstrap,destination,failed_pg,origin,destination_identity)
                    def missing_extension_permission(c,database):
                        provision(c,database)
                        from psycopg import sql
                        c.execute(sql.SQL('REVOKE CREATE ON DATABASE {} FROM f0d_migration').format(sql.Identifier(database)))
                    with self.assertRaisesRegex(backup.BackupError,'RESTORE_PG_RESTORE_FAILED'):
                        backup.restore(failed_target._bootstrap,destination,failed_pg,destination_identity,package,manifest_hash,
                                       directory/'failed-sql-restore',missing_extension_permission,failed_identity)
                    self.assertFalse((directory/'failed-sql-restore/result.json').exists())
                    with failed_target._bootstrap() as c:
                        self.assertEqual(c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()[0],0)
                        self.assertIsNone(c.execute("SELECT to_regnamespace('f1')").fetchone()[0])
                    self.assertEqual(backup.inventory(destination),[])
                    events=[json.loads(line) for line in (directory/'failed-sql-restore/journal.jsonl').read_text().splitlines()]
                    self.assertEqual(events[-1]['stage'],'restore_failed')
                    self.assertEqual(events[-1]['runtime_start'],'BLOCKED')
                    backup.verify_package(package,manifest_hash)
                    print('RESTORE_REAL_SQL_FAILURE_ROLLBACK_ZERO_TABLES_ZERO_OBJECTS=PASSED',flush=True)
                finally:
                    failed_target.dispose_runtime();failed_target.stop()
                    self.assertEqual((failed_target.cleanup_status,failed_target.dedicated_after,failed_target.shared_match),('CLEAN',(0,0,0),1))
                    target.apply_env()
                try:
                    command=[sys.executable,'-m','infra.f1.analysis_report_backup','restore','--package',str(package),
                        '--manifest-sha256',manifest_hash,'--postgres-container',target_pg.container,
                        '--output',str(directory/'restored'),'--plan',str(directory/'restore-plan.json'),'--writers-stopped']
                    process=subprocess.run(command,env=operator_environment(target,target_objects),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
                    self.assertEqual(process.returncode,0,process.stdout.decode()+process.stderr.decode())
                    result=json.loads((directory/'restored/result.json').read_bytes())
                except backup.BackupError as error:
                    if hasattr(error,'stderr'):
                        print('RESTORE_SYNTHETIC_DIAGNOSTIC='+error.stderr.decode()[:1600],flush=True)
                    observed_path=directory/'restored/database-proof-observed.json'
                    if observed_path.exists():
                        observed=json.loads(observed_path.read_bytes())
                        for section in proof['catalog']:
                            if observed['catalog'][section]!=proof['catalog'][section]:
                                before=proof['catalog'][section];after=observed['catalog'][section]
                                pairs=[(a,b) for a,b in zip(before,after) if a!=b]
                                print('CATALOG_DIFF='+section+' '+str((len(before),len(after)))+' '+str(pairs[:1])[:1600],flush=True)
                    raise
                self.assertEqual(result['status'],'RESTORE_VERIFIED')
                with self.assertRaisesRegex(backup.BackupError,'RESTORE_EMPTY_DATABASE_REQUIRED'):
                    backup.empty_target(target._bootstrap,destination,target_pg,origin,destination_identity)
                self.assertEqual(destination.stat_object(BUCKETS[2],'recovery-multipart').etag,original_etag)
                restored_keys=Path(result['keys_directory'])
                with target._bootstrap() as c:
                    self.assertEqual(backup.snapshot(c),proof)
                restart=backup.runtime_check(target._bootstrap,destination_identity,package,manifest_hash,result)
                self.assertEqual(restart['status'],'WAIT_LEASE_EXPIRY',restart)
                self.assertTrue(any(x['table']=='f1.material_evidence_job' for x in restart['active_leases']))
                # Use the real database clock, without rewriting job states or tokens.
                from datetime import datetime
                until=max(datetime.fromisoformat(x['until']).timestamp() for x in restart['active_leases'])
                self.assertLess(until-time.time(),50)
                time.sleep(max(0,until-time.time())+.1)
                self.assertEqual(backup.runtime_check(target._bootstrap,destination_identity,package,manifest_hash,result)['status'],'RUNTIME_RESTART_READY')
                runtime=directory/'api-runtime';runtime.mkdir(mode=0o700)
                for name in backup.KEY_NAMES:
                    backup.write_private(runtime,name,(restored_keys/name).read_bytes())
                backup.write_private(runtime,'f1_api_password',(target.secrets_dir/'f1_api_password').read_bytes())
                for name,value in zip(('minio_service_user','minio_service_password'),target_objects.IDENTITIES['api']):
                    backup.write_private(runtime,name,value.encode())
                request_path=directory/'readback.json';backup.write_private(directory,request_path.name,backup.canonical(readback_request))
                child_env={key:value for key,value in target.runtime_env().items() if key in {
                    'PATH','HOME','LANG','LC_ALL','PYTHONPATH','F1_PG_HOST','F1_PG_PORT','F1_PG_DATABASE',
                    'F1_KEYCLOAK_ISSUER_URL','F1_KEYCLOAK_REALM','KEYCLOAK_URL'}}
                child_env.update(business_flags)
                child_env.update(F1_SECRETS_DIR=str(runtime),F1_PROVIDER_SECRETS_DIR=str(runtime),
                                 MINIO_ENDPOINT=target_objects.ENDPOINT)
                process=subprocess.run([sys.executable,'-m','tests.restore_readback_worker',str(request_path)],
                    env=child_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
                self.assertEqual(process.returncode,0,process.stderr.decode())
                print(process.stdout.decode().strip(),flush=True)
                flags={'F1_MATERIAL_RAG_KEY_FILE':str(restored_keys/'f1_material_rag_key')}
                with patch.dict(os.environ,flags):
                    formats_after={s['document_version_id']:decode_source(s) for s in helper.effective_sources(report_scopes) if s['available']}
                    self.assertEqual(formats_after,formats_before)
                    frozen_after=asyncio.run(reports.version_detail(report_tenant,uuid.UUID(claim['version_id'])))
                    self.assertEqual(frozen_after,frozen)
                    self.assertEqual(render_html_artifact(frozen_after).sha256,html_hash)
                    self.assertEqual(render_pdf_artifact(frozen_after).sha256,pdf_hash)
                    # Runtime RLS read and AEAD decrypt under freshly generated DB passwords.
                    helper.eid=reviewed.enterprise_id;helper.scope=reviewed.knowledge_scope_id
                    rows=helper.effective_sources()
                    after=decode_source(next(r for r in rows if r['document_version_id']==str(review_version)))
                    self.assertEqual(after,reviewed)
                    helper.version=review_version
                    self.assertEqual(helper.review_source()['review_head']['id'],review['id'])
                self.assertEqual(helper.report_rows(claim),report_counts)
                with helper.connection('f1_report_worker') as c:
                    import psycopg
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        c.execute('SELECT * FROM f1.material_evidence_fragment')
                with target._bootstrap() as c:
                    self.assertEqual(c.execute("SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid='f1.leased_ocr_cache(text,uuid,uuid,uuid,text,integer,text,jsonb)'::regprocedure").fetchone()[0], 'f1_ocr_cache_definer')
                    self.assertEqual(c.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'").fetchone()[0],'f0f_crypto')
                helper.eid=uuid.UUID(cache_job['enterprise_id']);helper.scope=uuid.UUID(cache_job['knowledge_scope_id']);helper.version=cache_version
                with helper.connection('f1_source_reader') as c:
                    self.assertIsNone(c.execute('SELECT f1.leased_ocr_cache(%s,%s,%s,%s,%s,1,%s,NULL)',
                        ('native',cache_job['job_id'],cache_job['lease_token'],cache_version,backup.sha(image_raw),ticket.input_sha256)).fetchone()[0])
                renewed=helper.claim();self.assertEqual(renewed['job_id'],cache_job['job_id'])
                self.assertNotEqual(renewed['lease_token'],cache_job['lease_token'])
                with helper.connection('f1_source_reader') as c:
                    cached=c.execute('SELECT f1.leased_ocr_cache(%s,%s,%s,%s,%s,1,%s,NULL)',
                        ('native',renewed['job_id'],renewed['lease_token'],cache_version,backup.sha(image_raw),ticket.input_sha256)).fetchone()[0]
                with patch.dict(os.environ,flags):
                    restored=cached['envelope']
                    self.assertEqual(decrypt_text(bytes.fromhex(restored['ciphertext_hex']),cache_aad,restored['aad_sha256']),body.decode())
                    payload=build_native_payload(image_result,source_format='jpeg',
                        **{key:uuid.UUID(renewed[key]) for key in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id','revision_id')})
                    self.assertTrue(helper.finalize(renewed,payload))
                    self.assertEqual(helper.count(renewed),(1,1))
                    self.assertEqual(asyncio.run(reports.version_detail(report_tenant,uuid.UUID(claim['version_id']))),frozen)
                print('RESTORE_OLD_LEASE_REJECTED_NEW_LEASE_CACHE_READ_AND_NATIVE_FINISH=PASSED',flush=True)
                print('CURRENT_HEAD_RESTORE_ROWS_CATALOG_KEYS_MULTIPART=PASSED',flush=True)
            finally:
                if source:
                    source.dispose_runtime();native.STACK=source;native.tearDownModule()
                if source_objects_started:source_objects.tearDownModule()
                if target:
                    target.dispose_runtime();target.stop()
                    self.assertEqual((target.cleanup_status,target.dedicated_after,target.shared_match),('CLEAN',(0,0,0),1))
                if target_objects_started:target_objects.tearDownModule()


if __name__=='__main__':
    unittest.main()
