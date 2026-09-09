"""Real object-store recovery, stale-plan rejection and DB write serialization."""
from __future__ import annotations
import asyncio
import copy
import hashlib
import io
import json
import os
import unittest
import uuid
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

import psycopg
from minio import Minio
from platform_foundation.f1 import storage
from platform_foundation.f1.maintenance.object_reconcile import ObjectReconciler,ReconcileError,digest
from platform_foundation.f1.features.p3.preview import build_preview,content_addressed_preview
from tests import test_native_evidence_postgres as native
from tests import test_storage_service_runtime as objects


def setUpModule():
    native.setUpModule()
    try:objects.setUpModule()
    except BaseException:native.tearDownModule();raise


def tearDownModule():
    try:objects.tearDownModule()
    finally:native.tearDownModule()


class ObjectReconcileTests(unittest.TestCase):
    def setUp(self):
        self.h=native.NativePostgresTests();self.h.setUp()
        self.client=Minio(objects.ENDPOINT,access_key=objects.ROOT_USER,secret_key=objects.ROOT_PASSWORD,secure=False)
        self.now=datetime.now(timezone.utc)+timedelta(days=2)
        self.reconciler=ObjectReconciler(native.STACK._bootstrap,self.client,'isolated-'+objects.RUN,clock=lambda:self.now)

    def source(self,*,released=False):
        h=self.h;raw=h.raw;h.actual_size(raw)
        with native.STACK._bootstrap() as c:
            task,key=c.execute('SELECT t.id,t.object_key FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id WHERE v.id=%s',(h.version,)).fetchone()
            stored=self.client.put_object(storage.QUARANTINE_BUCKET,key,io.BytesIO(raw),len(raw))
            c.execute("UPDATE f1.upload_task SET source_etag=%s,quarantine_status=%s,released_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END WHERE id=%s",(stored.etag,'released' if released else 'held',released,task))
        if released:self.client.put_object(storage.BUCKET,key,io.BytesIO(raw),len(raw))
        result=content_addressed_preview(task,build_preview('docx',io.BytesIO(raw)))
        self.put_preview(task,result)
        with native.STACK._bootstrap() as c:c.execute('UPDATE f1.upload_task SET preview_sha256=%s,preview_unit_count=%s WHERE id=%s',(result.sha256,result.unit_count,task))
        return task,key,raw,result

    def put_preview(self,task,result):
        for unit in result.units:
            key=storage._preview_object_key(task,unit.id,unit.content_type)
            self.client.put_object(storage.PREVIEW_BUCKET,key,io.BytesIO(unit.content),len(unit.content),content_type=unit.content_type)
        manifest=json.dumps(result.payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        key=storage._preview_object_key(task,str(uuid.uuid5(task,'manifest:'+result.sha256)),'application/json')
        self.client.put_object(storage.PREVIEW_BUCKET,key,io.BytesIO(manifest),len(manifest),content_type='application/json')

    def issues(self,plan,task=None,action=None,key=None):
        return [i for i in plan['issues'] if (task is None or i.get('identity',{}).get('task_id')==str(task))
                and (action is None or i['action']==action) and (key is None or i.get('key')==key)]

    def apply(self,plan,issues,**kwargs):
        return self.reconciler.apply(plan,issue_ids=[i['id'] for i in issues],**kwargs)

    def statuses(self,result,issues):
        ids={i['id'] for i in issues}
        return [r for r in result['results'] if r['id'] in ids]

    def test_missing_released_quarantine_and_preview_restore_exact_bytes_and_replay(self):
        task,key,raw,preview=self.source(released=True)
        self.client.remove_object(storage.BUCKET,key)
        plan=self.reconciler.plan();issue=self.issues(plan,task,'restore_released');self.assertEqual(len(issue),1)
        self.assertIsNone(self.reconciler._stat(storage.BUCKET,key))  # Planning is read-only.
        self.assertEqual(self.statuses(self.apply(plan,issue),issue)[0]['status'],'REPAIRED')
        self.assertEqual(self.statuses(self.apply(plan,issue),issue)[0]['status'],'ALREADY_REPAIRED')
        self.client.remove_object(storage.QUARANTINE_BUCKET,key)
        plan=self.reconciler.plan();issue=self.issues(plan,task,'restore_quarantine');self.assertEqual(len(issue),1)
        self.assertEqual(self.statuses(self.apply(plan,issue),issue)[0]['status'],'REPAIRED')
        unit=preview.units[0];unit_key=storage._preview_object_key(task,unit.id,unit.content_type)
        self.client.remove_object(storage.PREVIEW_BUCKET,unit_key)
        plan=self.reconciler.plan();issue=self.issues(plan,task,'restore_preview');self.assertEqual(len(issue),1)
        self.assertEqual(self.statuses(self.apply(plan,issue),issue)[0]['status'],'REPAIRED')
        manifest_key=storage._preview_object_key(task,str(uuid.uuid5(task,'manifest:'+preview.sha256)),'application/json')
        self.client.remove_object(storage.PREVIEW_BUCKET,manifest_key)
        plan=self.reconciler.plan();issue=self.issues(plan,task,'restore_preview')
        self.assertEqual(self.statuses(self.apply(plan,issue),issue)[0]['status'],'REPAIRED')
        with native.STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT preview_sha256,quarantine_status FROM f1.upload_task WHERE id=%s',(task,)).fetchone(),(preview.sha256,'released'))
        self.assertEqual(self.reconciler._source(self._row(task),storage.QUARANTINE_BUCKET)[1],raw)

    def _row(self,task):
        with native.STACK._bootstrap() as c:return self.reconciler._tasks(c,task)[str(task)]

    def test_corruption_missing_both_sources_and_rebuild_change_are_never_overwritten(self):
        task,key,raw,preview=self.source(released=True)
        self.client.put_object(storage.BUCKET,key,io.BytesIO(b'x'*len(raw)),len(raw))
        plan=self.reconciler.plan();issues=self.issues(plan,task)
        self.assertTrue(any(i['code']=='RELEASED_SOURCE_CORRUPT' and i['action']=='inspect' for i in issues))
        self.assertEqual(self.reconciler._source(self._row(task),storage.BUCKET)[0],'corrupt')
        self.client.remove_object(storage.BUCKET,key);self.client.remove_object(storage.QUARANTINE_BUCKET,key)
        plan=self.reconciler.plan()
        self.assertTrue(all(i['action']=='inspect' for i in self.issues(plan,task) if i['code'].endswith('SOURCE_MISSING')))
        self.client.put_object(storage.QUARANTINE_BUCKET,key,io.BytesIO(raw),len(raw))
        with native.STACK._bootstrap() as c:c.execute("UPDATE f1.upload_task SET preview_sha256=%s WHERE id=%s",('a'*64,task))
        plan=self.reconciler.plan();issues=self.issues(plan,task,'restore_preview')
        result=self.apply(plan,issues)
        self.assertEqual(self.statuses(result,issues)[0]['code'],'RECONCILE_PREVIEW_REBUILD_MISMATCH')
        self.assertEqual(self._row(task)['task']['preview_sha256'],'a'*64)

    def test_orphan_cleanup_rechecks_new_references_content_retention_and_live_preview(self):
        task,key,raw,preview=self.source()
        orphan=uuid.uuid4().hex+'.docx';self.client.put_object(storage.QUARANTINE_BUCKET,orphan,io.BytesIO(b'orphan'),6)
        current=ObjectReconciler(native.STACK._bootstrap,self.client,'isolated-'+objects.RUN)
        recent=self.issues(current.plan(),key=orphan)
        self.assertEqual(recent[0]['action'],'retain')
        plan=self.reconciler.plan();issues=self.issues(plan,key=orphan,action='delete_orphan');self.assertEqual(len(issues),1)
        with native.STACK._bootstrap() as c:
            c.execute("INSERT INTO f1.document(id,enterprise_id,object_key,filename,size,content_type,status,knowledge_scope_id) VALUES(%s,%s,%s,'newly referenced',6,'application/pdf','pending',%s)",(uuid.uuid4(),self.h.eid,orphan,self.h.scope))
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['status'],'NOW_REFERENCED')
        changed=uuid.uuid4().hex+'.docx';self.client.put_object(storage.QUARANTINE_BUCKET,changed,io.BytesIO(b'before'),6)
        plan=self.reconciler.plan();issues=self.issues(plan,key=changed,action='delete_orphan')
        self.client.put_object(storage.QUARANTINE_BUCKET,changed,io.BytesIO(b'after!'),6)
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['code'],'RECONCILE_OBJECT_SNAPSHOT_CHANGED')
        stale_key=storage._preview_object_key(task,str(uuid.uuid4()),'application/json')
        self.client.put_object(storage.PREVIEW_BUCKET,stale_key,io.BytesIO(b'old preview'),11)
        plan=self.reconciler.plan();issues=self.issues(plan,task,'delete_orphan',stale_key);self.assertEqual(len(issues),1)
        with native.STACK._bootstrap() as c:c.execute("UPDATE f1.upload_task SET updated_at=clock_timestamp() WHERE id=%s",(task,))
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['code'],'RECONCILE_TASK_SNAPSHOT_CHANGED')
        plan=self.reconciler.plan();issues=self.issues(plan,task,'delete_orphan',stale_key)
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['status'],'REPAIRED')
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['status'],'ALREADY_REPAIRED')
        self.assertEqual(self.reconciler._preview(self._row(task))[1],'valid')

    def test_repairs_hold_source_locks_and_unknown_success_replays(self):
        task,key,raw,preview=self.source(released=True);self.client.remove_object(storage.BUCKET,key)
        plan=self.reconciler.plan();issues=self.issues(plan,task,'restore_released');original=self.client.put_object;checked=[]
        def put(*args,**kwargs):
            with native.STACK._bootstrap() as c:
                c.execute("SET LOCAL lock_timeout='60ms'")
                with self.assertRaises(psycopg.errors.LockNotAvailable):c.execute('UPDATE f1.upload_task SET source_etag=source_etag WHERE id=%s',(task,))
                c.rollback()
            original(*args,**kwargs);checked.append(True)
            raise RuntimeError('SIMULATED_LOST_WRITE_RESPONSE')
        with patch.object(self.client,'put_object',side_effect=put):
            self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['status'],'BLOCKED')
        self.assertEqual(checked,[True])
        self.assertEqual(self.statuses(self.apply(plan,issues),issues)[0]['status'],'ALREADY_REPAIRED')
        changed=copy.deepcopy(plan);changed['target']['storage']='other';changed['plan_sha256']=digest({k:v for k,v in changed.items() if k!='plan_sha256'})
        with self.assertRaisesRegex(ReconcileError,'RECONCILE_TARGET_CHANGED'):self.apply(changed,issues)

    def test_stranded_upload_finalizes_atomically_with_current_manager_and_one_delivery(self):
        task,key,raw,preview=self.source()
        with native.STACK._bootstrap() as c:
            c.execute("UPDATE f1.upload_task SET source_etag=NULL,object_state='reserved',status='pending',processing_stage='received',scan_verdict='queued',preview_status='blocked',preview_sha256=NULL,preview_unit_count=0 WHERE id=%s",(task,))
        plan=self.reconciler.plan();issues=self.issues(plan,task,'finalize_upload');self.assertEqual(len(issues),1)
        flags={'F1_LOCAL_ENGINEERING':'1','F1_MATERIAL_INGESTION_DURABLE_LOCAL':'1'}
        with patch.dict(os.environ,flags):
            self.assertEqual(self.statuses(self.apply(plan,issues,actor_sub='missing-manager'),issues)[0]['status'],'BLOCKED')
            from platform_foundation.f1.features.p3 import service
            original=service._register_ingestion_delivery_if_enabled
            async def fail_after_registration(*args,**kwargs):
                await original(*args,**kwargs)
                raise RuntimeError('RECONCILE_FINALIZATION_ROLLBACK_PROBE')
            with patch.object(service,'_register_ingestion_delivery_if_enabled',side_effect=fail_after_registration):
                self.assertEqual(self.statuses(self.apply(plan,issues,actor_sub=self.h.sub),issues)[0]['status'],'BLOCKED')
            with native.STACK._bootstrap() as c:
                self.assertEqual(c.execute('SELECT object_state FROM f1.upload_task WHERE id=%s',(task,)).fetchone()[0],'reserved')
                self.assertEqual(c.execute('SELECT count(*) FROM f1.material_ingestion_delivery WHERE document_version_id=%s',(self.h.version,)).fetchone()[0],0)
                self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='document.quarantine' AND resource_id=%s",(str(self.h.version),)).fetchone()[0],0)
                c.execute('UPDATE f1.enterprise_user SET revoked_at=clock_timestamp() WHERE enterprise_id=%s AND user_id=%s',(self.h.eid,self.h.actor))
            self.assertEqual(self.statuses(self.apply(plan,issues,actor_sub=self.h.sub),issues)[0]['code'],'RECONCILE_CURRENT_MANAGER_REQUIRED')
            with native.STACK._bootstrap() as c:c.execute('UPDATE f1.enterprise_user SET revoked_at=NULL WHERE enterprise_id=%s AND user_id=%s',(self.h.eid,self.h.actor))
            result=self.apply(plan,issues,actor_sub=self.h.sub)
            self.assertEqual(self.statuses(result,issues)[0]['status'],'REPAIRED',self.statuses(result,issues))
            self.assertEqual(self.statuses(self.apply(plan,issues,actor_sub=self.h.sub),issues)[0]['status'],'ALREADY_REPAIRED')
        with native.STACK._bootstrap() as c:
            self.assertEqual(c.execute('SELECT object_state,processing_stage FROM f1.upload_task WHERE id=%s',(task,)).fetchone(),('quarantined','received'))
            self.assertEqual(c.execute('SELECT count(*) FROM f1.material_ingestion_delivery WHERE document_version_id=%s',(self.h.version,)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT count(*) FROM f1.audit_log WHERE action='document.quarantine' AND resource_id=%s",(str(self.h.version),)).fetchone()[0],1)

    def test_cli_reads_real_maintenance_secret_files_and_writes_private_plan(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        task,key,raw,_=self.source(released=True);self.client.remove_object(storage.BUCKET,key)
        with tempfile.TemporaryDirectory(prefix='reconcile-cli-',dir=native.STACK.control_dir) as directory:
            folder=Path(directory)
            for name,value in [('minio_root_user',objects.ROOT_USER),('minio_root_password',objects.ROOT_PASSWORD)]:
                path=folder/name;path.write_text(value);path.chmod(0o600)
            path=folder/'f1_bootstrap_dsn';path.write_bytes((native.STACK.secrets_dir/path.name).read_bytes());path.chmod(0o600)
            env={k:v for k,v in native.STACK.runtime_env().items() if not k.endswith('_PASSWORD_FILE')}
            env.update(F1_SECRETS_DIR=directory,MINIO_ENDPOINT=objects.ENDPOINT,F1_LOCAL_ENGINEERING='1',F1_MINIO_ROOT_USER_FILE='',F1_MINIO_ROOT_PASSWORD_FILE='')
            receipt=folder/'plan.json'
            command=[sys.executable,'infra/f1/analysis_report_reconcile.py','plan','--output',str(receipt)]
            result=subprocess.run(command,env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(receipt.stat().st_mode&0o777,0o600)
            plan=json.loads(receipt.read_text());issues=self.issues(plan,task,'restore_released');self.assertEqual(len(issues),1)
            self.assertIsNone(self.reconciler._stat(storage.BUCKET,key))
            output=folder/'apply.json'
            result=subprocess.run([sys.executable,'infra/f1/analysis_report_reconcile.py','apply','--plan',str(receipt),'--issue-id',issues[0]['id'],'--output',str(output)],
                env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(self.statuses(json.loads(output.read_text()),issues)[0]['status'],'REPAIRED')
            self.assertEqual(self.reconciler._source(self._row(task),storage.BUCKET)[1],raw)
            journal=Path(str(output)+'.journal.jsonl')
            self.assertEqual(journal.stat().st_mode&0o777,0o600)
            events=[json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual(events[0]['event'],'run_started');self.assertEqual(events[-1]['event'],'run_finished')
            started=next(e for e in events if e['event']=='action_started')
            finished=next(e for e in events if e['event']=='action_finished' and e['id']==started['id'])
            self.assertEqual(finished['status'],'REPAIRED')
            # Kill a real CLI process after MinIO accepted the copy but before
            # the result is returned. The action identity must already be durable.
            self.client.remove_object(storage.BUCKET,key)
            crashed=folder/'crashed.json'
            code="""
import os,runpy,signal,sys
from minio import Minio
put=Minio.put_object
def crash_after_put(self,*args,**kwargs):
    result=put(self,*args,**kwargs)
    os.kill(os.getpid(),signal.SIGKILL)
Minio.put_object=crash_after_put
sys.argv=['analysis_report_reconcile.py','apply','--plan',sys.argv[1],'--issue-id',sys.argv[2],'--output',sys.argv[3]]
runpy.run_module('infra.f1.analysis_report_reconcile',run_name='__main__')
"""
            killed=subprocess.run([sys.executable,'-c',code,str(receipt),issues[0]['id'],str(crashed)],env=env,
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(killed.returncode,-9,killed.stdout+killed.stderr)
            interrupted=[json.loads(line) for line in Path(str(crashed)+'.journal.jsonl').read_text().splitlines()]
            self.assertTrue(any(e['event']=='action_started' and e['id']==issues[0]['id'] for e in interrupted))
            self.assertFalse(any(e['event']=='action_finished' and e['id']==issues[0]['id'] for e in interrupted))
            replay=folder/'replay.json'
            result=subprocess.run([sys.executable,'infra/f1/analysis_report_reconcile.py','apply','--plan',str(receipt),'--issue-id',issues[0]['id'],'--output',str(replay)],
                env=env,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(self.statuses(json.loads(replay.read_text()),issues)[0]['status'],'ALREADY_REPAIRED')
            print('RECONCILE_SIGKILL_AFTER_PUT=RECOVERED;JOURNAL_BEFORE_MUTATION=1',flush=True)

    def test_forged_cross_task_preview_plan_cannot_delete_a_current_unit(self):
        task,key,raw,preview=self.source()
        stale=storage._preview_object_key(task,str(uuid.uuid4()),'application/json')
        self.client.put_object(storage.PREVIEW_BUCKET,stale,io.BytesIO(b'stale'),5)
        plan=self.reconciler.plan();issue=self.issues(plan,task,'delete_orphan',stale)[0]
        foreign_task=uuid.uuid4();foreign=storage._preview_object_key(foreign_task,str(uuid.uuid4()),'application/json')
        self.client.put_object(storage.PREVIEW_BUCKET,foreign,io.BytesIO(b'foreign'),7)
        tampered=copy.deepcopy(plan)
        changed=next(i for i in tampered['issues'] if i['id']==issue['id']);changed['key']=foreign
        changed['object_snapshot']=self.reconciler._read(storage.PREVIEW_BUCKET,foreign)[0]
        tampered['plan_sha256']=digest({k:v for k,v in tampered.items() if k!='plan_sha256'})
        result=self.apply(tampered,[changed])
        self.assertEqual(self.statuses(result,[changed])[0]['code'],'RECONCILE_PREVIEW_PREFIX_MISMATCH')
        self.assertEqual(self.reconciler._read(storage.PREVIEW_BUCKET,foreign)[1],b'foreign')
