"""Explicit maintenance CLI: first plan, then revalidate and apply that exact plan."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from minio import Minio

from infra.f1.migrate_f1 import _bootstrap_dsn
from platform_foundation.f1.secret_files import read_f1_secret_text, _read_secure
from platform_foundation.f1.maintenance.object_reconcile import ObjectReconciler, ReconcileError



def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','apply'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--issue-id',action='append',help='Optional reviewed issue IDs; omission applies every repairable issue.')
    parser.add_argument('--actor-sub',help='Current manager for finalizing a stranded upload; never changes release approval.')
    args=parser.parse_args()
    if os.environ.get('F1_LOCAL_ENGINEERING')!='1':raise ReconcileError('RECONCILE_EXPLICIT_MAINTENANCE_REQUIRED')
    if args.mode=='apply' and args.plan is None:parser.error('--plan is required for apply')
    # The one-shot operator uses maintenance credentials; API/worker volumes do not gain them.
    endpoint=os.environ['MINIO_ENDPOINT']
    user=read_f1_secret_text('minio_root_user',file_env='F1_MINIO_ROOT_USER_FILE')
    password=read_f1_secret_text('minio_root_password',file_env='F1_MINIO_ROOT_PASSWORD_FILE')
    dsn=_bootstrap_dsn()
    client=Minio(endpoint,access_key=user,secret_key=password,secure=False)
    identity=hashlib.sha256((endpoint+'\0'+user).encode()).hexdigest()
    reconciler=ObjectReconciler(lambda:psycopg.connect(dsn,connect_timeout=5),client,identity)
    args.output=args.output.absolute()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists() or args.output.is_symlink():raise ReconcileError('RECONCILE_RECEIPT_EXISTS')
    # Reserve the final receipt before any mutations. The fsynced action journal
    # preserves in-flight identities if the process dies before the final receipt.
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as receipt:
        try:
            if args.mode=='plan':result=reconciler.plan()
            else:
                plan_raw=_read_secure(args.plan.absolute(),unavailable_code='RECONCILE_PLAN_FILE_INVALID',minimum_size=1,maximum_size=16*1024*1024)
                journal_path=Path(str(args.output)+'.journal.jsonl')
                descriptor=os.open(journal_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'w') as journal:
                    def event(record):
                        journal.write(json.dumps(record,ensure_ascii=False)+'\n');journal.flush();os.fsync(journal.fileno())
                    result=reconciler.apply(json.loads(plan_raw),actor_sub=args.actor_sub,issue_ids=args.issue_id,on_event=event)
                    event({'event':'run_finished','status':result['status']})
                result['journal']=str(journal_path)
            json.dump(result,receipt,ensure_ascii=False,indent=2);receipt.write('\n');receipt.flush();os.fsync(receipt.fileno())
        except Exception:
            receipt.seek(0);receipt.truncate();json.dump({'status':'FAILED','code':'OBJECT_RECONCILE_FAILED'},receipt)
            receipt.flush();os.fsync(receipt.fileno());raise
    print(json.dumps({'status':result['status'],'receipt':str(args.output.absolute()),
        'issues':len(result.get('issues',[])),'remaining_inspection':result.get('remaining_inspection',0)},ensure_ascii=False))
    return 1 if result['status']=='BLOCKED' else 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('OBJECT_RECONCILE_FAILED');raise SystemExit(1)
