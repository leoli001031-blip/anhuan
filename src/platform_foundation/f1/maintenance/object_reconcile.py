"""Reviewable object repair plans, bound to one database and object-store target."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from minio.error import S3Error
from psycopg.rows import dict_row

from .. import storage
from ..features.p3.preview import build_preview, content_addressed_preview

BUCKETS = (storage.QUARANTINE_BUCKET, storage.BUCKET, storage.PREVIEW_BUCKET)
SOURCE_KEY = re.compile(r'^[0-9a-f]{32}\.(?:pdf|docx|xlsx|jpg|doc|xls|ppt|pptx|png)$')
PREVIEW_KEY = re.compile(r'^[0-9a-f]{32}/[0-9a-f]{32}\.(?:json|jpg)$')
MAX_OBJECTS = 10000
MAX_READ_BYTES = 1024 * 1024 * 1024
MIN_RETENTION_SECONDS = 86400
REPAIR_ACTIONS = frozenset({'restore_quarantine','restore_released','restore_preview','finalize_upload','delete_orphan'})
TASK_SQL = """SELECT to_jsonb(t) AS task,v.id AS version_id,v.source_document_id,
  v.document_record_id,v.idempotency_key_sha256,d.content_type,d.object_key AS document_key,
  d.size AS document_size,r.status AS record_status,
  EXISTS(SELECT 1 FROM f1.material_ingestion_delivery q WHERE q.document_version_id=v.id
    AND q.state IN ('pending','dispatched','retry_wait')) AS delivery_busy
  FROM f1.upload_task t JOIN f1.document_version v ON v.upload_task_id=t.id AND v.enterprise_id=t.enterprise_id
  JOIN f1.document d ON d.id=v.source_document_id AND d.enterprise_id=t.enterprise_id
  JOIN f1.document_record r ON r.id=v.document_record_id AND r.enterprise_id=t.enterprise_id
  WHERE t.pipeline_kind='controlled_ingestion'"""


class ReconcileError(RuntimeError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _utc(value):
    return datetime.fromisoformat(str(value)) if not isinstance(value,datetime) else value


class ObjectReconciler:
    """The operator supplies maintenance credentials; runtime roles gain no grants."""
    def __init__(self, connect: Callable, client, storage_identity: str, *, clock=None):
        self.connect,self.client,self.storage_identity=connect,client,storage_identity
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.read_bytes=0

    def _target(self,c):
        row=c.execute("SELECT current_database(),session_user,(SELECT system_identifier::text FROM pg_control_system()),(SELECT version_num FROM f1.alembic_version)").fetchone()
        from infra.f1.migrate_f1 import F1_ANALYSIS_REPORT_MIGRATE_TARGET
        if row[1]!='f0d_bootstrap' or row[3]!=F1_ANALYSIS_REPORT_MIGRATE_TARGET:
            raise ReconcileError('RECONCILE_DATABASE_IDENTITY_INVALID')
        return {'database':row[0],'cluster':row[2],'head':row[3],'storage':self.storage_identity}

    def _tasks(self,c,task_id=None):
        sql=TASK_SQL+(' AND t.id=%s' if task_id else '')+' ORDER BY v.id'
        with c.cursor(row_factory=dict_row) as cursor:
            rows=cursor.execute(sql,(task_id,) if task_id else ()).fetchall()
        unique={}
        for row in rows:
            identity=row['task']['id']
            if identity in unique:raise ReconcileError('RECONCILE_AMBIGUOUS_TASK_VERSION')
            unique[identity]=row
        return unique

    def _stat(self,bucket,key):
        try:obj=self.client.stat_object(bucket,key)
        except S3Error as error:
            if error.code in {'NoSuchKey','NoSuchObject'}:return None
            raise ReconcileError('RECONCILE_STORAGE_STAT_FAILED') from None
        return {'etag':str(obj.etag),'size':int(obj.size),'modified':obj.last_modified.isoformat()}

    def _read(self,bucket,key,maximum=50*1024*1024):
        meta=self._stat(bucket,key)
        if meta is None:return None,None
        if not 0 < meta['size'] <= maximum or self.read_bytes+meta['size']>MAX_READ_BYTES:
            raise ReconcileError('RECONCILE_READ_BUDGET_EXCEEDED')
        response=self.client.get_object(bucket,key)
        try:raw=response.read(meta['size']+1)
        finally:response.close();response.release_conn()
        self.read_bytes+=len(raw)
        if len(raw)!=meta['size'] or self._stat(bucket,key)!=meta:
            raise ReconcileError('RECONCILE_OBJECT_CHANGED_DURING_READ')
        return {**meta,'sha256':hashlib.sha256(raw).hexdigest()},raw

    def _inventory(self):
        objects={}
        for bucket in BUCKETS:
            for obj in self.client.list_objects(bucket,recursive=True):
                if len(objects)>=MAX_OBJECTS:raise ReconcileError('RECONCILE_OBJECT_BUDGET_EXCEEDED')
                objects[(bucket,obj.object_name)]={'etag':str(obj.etag),'size':int(obj.size),'modified':obj.last_modified.isoformat()}
        return objects

    def _identity(self,row):
        t=row['task']
        return {'task_id':t['id'],'version_id':str(row['version_id']),'enterprise_id':t['enterprise_id'],
            'key':t['object_key'],'sha256':t['content_sha256'],'size':t['source_size'],'content_type':row['content_type']}

    def _issue(self,code,action='inspect',row=None,**details):
        issue={'code':code,'action':action,**details}
        if row:
            issue.update(identity=self._identity(row),task_snapshot=digest(row))
        issue['id']=digest(issue)
        return issue

    def _source(self,row,bucket):
        t=row['task'];meta,raw=self._read(bucket,t['object_key'])
        if meta is None:return 'missing',None
        if meta['sha256']!=t['content_sha256'] or meta['size']!=t['source_size']:
            return 'corrupt',None
        if bucket==storage.QUARANTINE_BUCKET and t['source_etag'] is not None and meta['etag']!=t['source_etag']:
            return 'etag_mismatch',None
        return 'valid',raw

    def _preview(self,row):
        """Return protected keys, missing/corrupt state; unknown manifests protect the prefix."""
        t=row['task'];task=uuid.UUID(t['id']);expected=t['preview_sha256']
        if not expected:
            return (None,'missing') if t['processing_stage']=='ready' else (set(),'none')
        key=storage._preview_object_key(task,str(uuid.uuid5(task,'manifest:'+expected)),'application/json')
        meta,raw=self._read(storage.PREVIEW_BUCKET,key,256*1024)
        if meta is None:
            key=storage._preview_object_key(task,'manifest','application/json')
            meta,raw=self._read(storage.PREVIEW_BUCKET,key,256*1024)
        if meta is None:return None,'missing'
        if meta['sha256']!=expected:return None,'corrupt'
        try:
            manifest=json.loads(raw);units=manifest['units']
            if not isinstance(units,list) or not 1<=len(units)<=128:raise ValueError()
            refs={key};state='valid'
            for unit in units:
                unit_key=storage._preview_object_key(task,unit['id'],unit['content_type']);refs.add(unit_key)
                umeta,_=self._read(storage.PREVIEW_BUCKET,unit_key,20*1024*1024 if unit['content_type']=='image/jpeg' else 256*1024)
                if umeta is None:state='missing' if state!='corrupt' else state
                elif umeta['sha256']!=unit['sha256'] or umeta['size']!=unit['size_bytes']:state='corrupt'
            return refs,state
        except (KeyError,TypeError,ValueError,storage.StorageError):return None,'corrupt'

    @staticmethod
    def _busy(row):
        return row['delivery_busy'] or row['task']['processing_stage'] in {'received','scanning','validating','previewing','retry_wait'}

    def plan(self):
        self.read_bytes=0;issues=[];now=self.clock()
        with self.connect() as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            target=self._target(c);tasks=self._tasks(c)
            protected={r[0] for r in c.execute('SELECT object_key FROM f1.document UNION SELECT object_key FROM f1.upload_task')}
            all_task_ids={str(r[0]) for r in c.execute('SELECT id FROM f1.upload_task')}
            inventory=self._inventory();preview_refs={}
            for row in tasks.values():
                t=row['task'];q,qraw=self._source(row,storage.QUARANTINE_BUCKET);r,rraw=self._source(row,storage.BUCKET)
                if t['object_key']!=row['document_key'] or t['source_size']!=row['document_size']:
                    issues.append(self._issue('SOURCE_DATABASE_IDENTITY_MISMATCH',row=row));continue
                if q=='missing':
                    recent=t['object_state']=='reserved' and (now-_utc(t['created_at'])).total_seconds()<600
                    issues.append(self._issue('UPLOAD_IN_PROGRESS' if recent else 'QUARANTINE_SOURCE_MISSING','retain' if recent else 'restore_quarantine' if r=='valid' else 'inspect',row))
                elif q!='valid':issues.append(self._issue('QUARANTINE_SOURCE_'+q.upper(),row=row))
                elif t['object_state'] in {'reserved','write_failed'}:
                    issues.append(self._issue('UPLOAD_OBJECT_NOT_FINALIZED','finalize_upload',row))
                if t['quarantine_status']=='released':
                    if r=='missing':issues.append(self._issue('RELEASED_SOURCE_MISSING','restore_released' if q=='valid' else 'inspect',row))
                    elif r!='valid':issues.append(self._issue('RELEASED_SOURCE_'+r.upper(),row=row))
                elif r!='missing':issues.append(self._issue('UNCOMMITTED_RELEASE_COPY','retain',row))
                refs,state=self._preview(row);preview_refs[t['id']]=refs
                if t['preview_status']=='ready' and state in {'missing','corrupt'}:
                    action='restore_preview' if state=='missing' and (q=='valid' or r=='valid') and t['scan_verdict']=='clean' else 'inspect'
                    issues.append(self._issue('PREVIEW_'+state.upper(),action,row))
            for (bucket,key),listed in inventory.items():
                row=None
                if bucket!=storage.PREVIEW_BUCKET:
                    if key in protected:continue
                    if not SOURCE_KEY.fullmatch(key):
                        issues.append(self._issue('UNRECOGNIZED_OBJECT','retain',bucket=bucket,key=key));continue
                else:
                    if not PREVIEW_KEY.fullmatch(key):
                        issues.append(self._issue('UNRECOGNIZED_OBJECT','retain',bucket=bucket,key=key));continue
                    task_id=str(uuid.UUID(hex=key.split('/')[0]));row=tasks.get(task_id)
                    if task_id in all_task_ids:
                        if row is None or self._busy(row) or preview_refs.get(task_id) is None or key in preview_refs[task_id]:continue
                age=(now-_utc(listed['modified'])).total_seconds()
                if age<MIN_RETENTION_SECONDS:
                    issues.append(self._issue('RECENT_UNREFERENCED_OBJECT','retain',row,bucket=bucket,key=key));continue
                meta,_=self._read(bucket,key)
                if meta is None:raise ReconcileError('RECONCILE_OBJECT_CHANGED_DURING_READ')
                issues.append(self._issue('UNREFERENCED_OBJECT','delete_orphan',row,bucket=bucket,key=key,object_snapshot=meta))
        result={'schema_version':1,'target':target,'created_at':now.isoformat(),'minimum_retention_seconds':MIN_RETENTION_SECONDS,
            'object_count':len(inventory),'task_count':len(tasks),'issues':issues,'status':'ISSUES_FOUND' if issues else 'HEALTHY'}
        result['plan_sha256']=digest(result)
        return result

    def _put_exact(self,bucket,key,raw,content_type,*,expected_etag=None):
        existing,body=self._read(bucket,key)
        if existing is not None:
            if body!=raw:raise ReconcileError('RECONCILE_EXISTING_OBJECT_CONFLICT')
        else:self.client.put_object(bucket,key,io.BytesIO(raw),len(raw),content_type=content_type)
        verified,body=self._read(bucket,key)
        if body!=raw:raise ReconcileError('RECONCILE_WRITE_VERIFICATION_FAILED')
        if expected_etag and verified['etag']!=expected_etag:raise ReconcileError('RECONCILE_RESTORED_ETAG_MISMATCH')

    def _restore_preview(self,row):
        t=row['task'];task=uuid.UUID(t['id']);refs,state=self._preview(row)
        if state=='valid':return 'ALREADY_REPAIRED'
        if state=='corrupt' or t['scan_verdict']!='clean' or t['preview_status']!='ready':raise ReconcileError('RECONCILE_PREVIEW_NOT_REPAIRABLE')
        source_state,raw=self._source(row,storage.QUARANTINE_BUCKET)
        if source_state=='missing':source_state,raw=self._source(row,storage.BUCKET)
        if source_state!='valid':raise ReconcileError('RECONCILE_SOURCE_NOT_VALID')
        fmt={'application/pdf':'pdf','application/vnd.openxmlformats-officedocument.wordprocessingml.document':'docx',
             'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':'xlsx','image/jpeg':'jpeg'}[row['content_type']]
        original=build_preview(fmt,io.BytesIO(raw));addressed=content_addressed_preview(task,original)
        if addressed.sha256==t['preview_sha256']:
            result=addressed;manifest_id=str(uuid.uuid5(task,'manifest:'+addressed.sha256))
        elif original.sha256==t['preview_sha256']:
            result=original;manifest_id='manifest'
        else:raise ReconcileError('RECONCILE_PREVIEW_REBUILD_MISMATCH')
        for unit in result.units:
            self._put_exact(storage.PREVIEW_BUCKET,storage._preview_object_key(task,unit.id,unit.content_type),unit.content,unit.content_type)
        self._put_exact(storage.PREVIEW_BUCKET,storage._preview_object_key(task,manifest_id,'application/json'),canonical(result.payload),'application/json')
        if self._preview(row)[1]!='valid':raise ReconcileError('RECONCILE_PREVIEW_VERIFICATION_FAILED')
        return 'REPAIRED'

    def _delete_orphan(self,c,issue,row):
        bucket,key=issue['bucket'],issue['key']
        if bucket not in BUCKETS:raise ReconcileError('RECONCILE_BUCKET_INVALID')
        if bucket!=storage.PREVIEW_BUCKET:
            if not SOURCE_KEY.fullmatch(key):raise ReconcileError('RECONCILE_KEY_INVALID')
            c.execute('LOCK TABLE f1.document,f1.upload_task IN SHARE MODE')
            if c.execute('SELECT 1 FROM f1.document WHERE object_key=%s UNION ALL SELECT 1 FROM f1.upload_task WHERE object_key=%s',(key,key)).fetchone():
                return 'NOW_REFERENCED'
        else:
            if not PREVIEW_KEY.fullmatch(key):raise ReconcileError('RECONCILE_KEY_INVALID')
            task_id=uuid.UUID(hex=key.split('/')[0])
            if row is None:
                c.execute('LOCK TABLE f1.upload_task IN SHARE MODE')
                if c.execute('SELECT 1 FROM f1.upload_task WHERE id=%s',(task_id,)).fetchone():return 'NOW_REFERENCED'
            else:
                if row['task']['id']!=str(task_id):raise ReconcileError('RECONCILE_PREVIEW_PREFIX_MISMATCH')
                refs,_=self._preview(row)
                if self._busy(row) or refs is None or key in refs:return 'NOW_REFERENCED'
        meta,_=self._read(bucket,key)
        if meta is None:return 'ALREADY_REPAIRED'
        if meta!=issue['object_snapshot']:raise ReconcileError('RECONCILE_OBJECT_SNAPSHOT_CHANGED')
        if (self.clock()-_utc(meta['modified'])).total_seconds()<MIN_RETENTION_SECONDS:raise ReconcileError('RECONCILE_RETENTION_NOT_REACHED')
        self.client.remove_object(bucket,key)
        if self._stat(bucket,key) is not None:raise ReconcileError('RECONCILE_DELETE_VERIFICATION_FAILED')
        return 'REPAIRED'

    async def _finalize(self,row,actor_sub):
        from ..auth import Tenant,memberships_for_sub
        from ..database import session_scope
        from ..features.p3 import service
        memberships=await memberships_for_sub(actor_sub)
        membership=next((m for m in memberships if str(m['enterprise_id'])==row['task']['enterprise_id'] and m['role'] in service.MANAGER_ROLES),None)
        if membership is None:raise ReconcileError('RECONCILE_CURRENT_MANAGER_REQUIRED')
        tenant=Tenant(enterprise_id=uuid.UUID(row['task']['enterprise_id']),sub=actor_sub,roles=(),role=membership['role'])
        async with session_scope(role='f1_api',enterprise_id=tenant.enterprise_id,sub=tenant.sub) as session:
            reservation=await service._existing_reservation(session,tenant,idempotency_key_sha256=row['idempotency_key_sha256'])
        if reservation is None or str(reservation.task_id)!=row['task']['id']:raise ReconcileError('RECONCILE_SCOPE_DENIED')
        if reservation.object_key!=row['task']['object_key'] or reservation.content_sha256!=row['task']['content_sha256'] or reservation.size!=row['task']['source_size'] or reservation.source_document_id!=row['source_document_id']:
            raise ReconcileError('RECONCILE_SOURCE_IDENTITY_CHANGED')
        state,_=self._source(row,storage.QUARANTINE_BUCKET)
        if state!='valid':raise ReconcileError('RECONCILE_SOURCE_NOT_VALID')
        meta=self._stat(storage.QUARANTINE_BUCKET,row['task']['object_key'])
        await service.finalize_quarantine(tenant,reservation,source_etag=meta['etag'],source_size=meta['size'])

    def apply(self,plan,*,actor_sub=None,issue_ids=None,on_event=None):
        self.read_bytes=0
        if plan.get('schema_version')!=1 or plan.get('plan_sha256')!=digest({k:v for k,v in plan.items() if k!='plan_sha256'}):
            raise ReconcileError('RECONCILE_PLAN_INTEGRITY_INVALID')
        age=(self.clock()-_utc(plan['created_at'])).total_seconds()
        if not 0<=age<=86400:raise ReconcileError('RECONCILE_PLAN_EXPIRED')
        with self.connect() as c:
            if self._target(c)!=plan['target']:raise ReconcileError('RECONCILE_TARGET_CHANGED')
        if issue_ids is not None and not set(issue_ids)<={i['id'] for i in plan['issues']}:raise ReconcileError('RECONCILE_ISSUE_UNKNOWN')
        results=[]
        def emit(event):
            if on_event is not None:on_event(event)
        def record(result):
            results.append(result)
            emit({'event':'action_finished',**result})
        emit({'event':'run_started','plan_sha256':plan['plan_sha256'],'target':plan['target']})
        for issue in plan['issues']:
            if issue_ids is not None and issue['id'] not in issue_ids:
                record({'id':issue['id'],'status':'NOT_SELECTED'});continue
            action=issue['action']
            if action not in REPAIR_ACTIONS:
                record({'id':issue['id'],'status':'RETAINED','code':issue['code']});continue
            emit({'event':'action_started','id':issue['id'],'action':action})
            try:
                finalize=None
                with self.connect() as c:
                    c.execute("SET LOCAL lock_timeout='3s'");c.execute("SET LOCAL statement_timeout='10s'")
                    if self._target(c)!=plan['target']:raise ReconcileError('RECONCILE_TARGET_CHANGED')
                    row=None
                    if 'identity' in issue:
                        task_id=uuid.UUID(issue['identity']['task_id'])
                        c.execute('SELECT id FROM f1.upload_task WHERE id=%s FOR UPDATE',(task_id,))
                        row=self._tasks(c,task_id).get(str(task_id))
                        if row is not None:
                            c.execute('SELECT id FROM f1.document WHERE id=%s FOR SHARE',(row['source_document_id'],))
                            c.execute('SELECT id FROM f1.document_record WHERE id=%s FOR SHARE',(row['document_record_id'],))
                            c.execute('SELECT id FROM f1.document_version WHERE id=%s FOR SHARE',(row['version_id'],))
                            row=self._tasks(c,task_id).get(str(task_id))
                        if row is None or self._identity(row)!=issue['identity']:raise ReconcileError('RECONCILE_SOURCE_IDENTITY_CHANGED')
                        if digest(row)!=issue['task_snapshot']:
                            # Replaying a successful finalization is harmless and must not rearm it.
                            if action=='finalize_upload' and row['task']['object_state'] in {'quarantined','ready'}:
                                status='ALREADY_REPAIRED';record({'id':issue['id'],'status':status});continue
                            raise ReconcileError('RECONCILE_TASK_SNAPSHOT_CHANGED')
                    elif action!='delete_orphan':raise ReconcileError('RECONCILE_TASK_REQUIRED')
                    if action=='delete_orphan':status=self._delete_orphan(c,issue,row)
                    elif action=='restore_preview':status=self._restore_preview(row)
                    elif action=='finalize_upload':
                        if not actor_sub:raise ReconcileError('RECONCILE_CURRENT_MANAGER_REQUIRED')
                        finalize=row;status='REPAIRED'
                    else:
                        destination=storage.QUARANTINE_BUCKET if action=='restore_quarantine' else storage.BUCKET
                        counterpart=storage.BUCKET if action=='restore_quarantine' else storage.QUARANTINE_BUCKET
                        if action=='restore_released' and row['task']['quarantine_status']!='released':raise ReconcileError('RECONCILE_RELEASE_NOT_COMMITTED')
                        current,_=self._source(row,destination)
                        if current=='valid':status='ALREADY_REPAIRED'
                        elif current!='missing':raise ReconcileError('RECONCILE_EXISTING_OBJECT_CONFLICT')
                        else:
                            state,raw=self._source(row,counterpart)
                            if state!='valid':raise ReconcileError('RECONCILE_SOURCE_NOT_VALID')
                            self._put_exact(destination,row['task']['object_key'],raw,row['content_type'],expected_etag=row['task']['source_etag'] if action=='restore_quarantine' else None)
                            status='REPAIRED'
                if finalize is not None:asyncio.run(self._finalize(finalize,actor_sub))
                record({'id':issue['id'],'status':status})
            except ReconcileError as error:record({'id':issue['id'],'status':'BLOCKED','code':str(error)})
            except Exception:record({'id':issue['id'],'status':'BLOCKED','code':'RECONCILE_OPERATION_FAILED'})
        return {'schema_version':1,'plan_sha256':plan['plan_sha256'],'target':plan['target'],'results':results,
            'status':'BLOCKED' if any(r['status']=='BLOCKED' for r in results) else 'APPLIED_WITH_REMAINDERS' if any(i['action']=='inspect' for i in plan['issues']) or any(r['status']=='NOT_SELECTED' for r in results) else 'APPLIED',
            'remaining_inspection':sum(i['action']=='inspect' for i in plan['issues']),
            'not_selected':sum(r['status']=='NOT_SELECTED' for r in results)}
