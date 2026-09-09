"""Current local-index candidate: backup, verify, plan and restore to empty services."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from minio import Minio

from infra.f1 import migrate_f1
from platform_foundation.f1.secret_files import read_f1_secret_text, _read_secure
from platform_foundation.f1.maintenance import candidate_backup as operation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('backup','verify','plan','restore','check-runtime'))
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--manifest-sha256',help='Digest from the separately retained original backup receipt.')
    parser.add_argument('--postgres-container',help='Exact PostgreSQL container for the selected database.')
    parser.add_argument('--output',type=Path,help='New private backup receipt/plan file, or restore result directory.')
    parser.add_argument('--plan',type=Path,help='Previously inspected plan for this exact empty destination.')
    parser.add_argument('--restore-result',type=Path,help='Verified restore result.json for the restart fence.')
    parser.add_argument('--minio-secure',action='store_true')
    parser.add_argument('--writers-stopped',action='store_true',help='Maintenance operator confirms API, dispatcher and all workers are stopped.')
    args=parser.parse_args()
    if args.mode!='backup' and not args.manifest_sha256:
        parser.error('--manifest-sha256 is required')
    if args.mode=='verify':
        manifest=operation.verify_package(args.package,args.manifest_sha256)
        print(json.dumps({'status':'BACKUP_VERIFIED','head':manifest['head'],'objects':len(manifest['objects'])}))
        return 0
    if os.environ.get('F1_LOCAL_ENGINEERING')!='1' or not args.writers_stopped:
        raise operation.BackupError('BACKUP_EXPLICIT_STOPPED_WRITERS_REQUIRED')
    if os.environ.get('F1_MATERIAL_RAG_LOCAL_INDEX')!='1':
        raise operation.BackupError('BACKUP_LOCAL_INDEX_CANDIDATE_REQUIRED')
    if not args.postgres_container or not args.output:
        parser.error('--postgres-container and --output are required')
    if args.mode=='restore' and not args.plan:
        parser.error('--plan is required for restore')
    dsn=migrate_f1._bootstrap_dsn()
    connect=lambda:psycopg.connect(dsn,connect_timeout=5)
    with connect() as c:
        identity=operation.database_identity(c,require_head=args.mode=='backup')
    pg=operation.ContainerPostgres(args.postgres_container,identity['database'])
    endpoint=os.environ['MINIO_ENDPOINT']
    user=read_f1_secret_text('minio_root_user',file_env='F1_MINIO_ROOT_USER_FILE')
    password=read_f1_secret_text('minio_root_password',file_env='F1_MINIO_ROOT_PASSWORD_FILE')
    client=Minio(endpoint,access_key=user,secret_key=password,secure=args.minio_secure)
    storage_identity=hashlib.sha256((str(args.minio_secure)+'\0'+endpoint+'\0'+user).encode()).hexdigest()
    output=args.output.absolute()
    if output.exists() or output.is_symlink():
        raise operation.BackupError('BACKUP_OUTPUT_ALREADY_EXISTS')
    if args.mode=='backup':
        names={'f1_material_rag_key':'F1_MATERIAL_RAG_KEY_FILE','f1_qa_key':'F1_QA_KEY_FILE',
               'f0i_key':'F1_F0I_KEY_FILE','invite_signing_key':'F1_INVITE_KEY_FILE'}
        keys={name:Path(os.environ[env]) if os.environ.get(env) else Path(os.environ['F1_SECRETS_DIR'])/name
              for name,env in names.items()}
        historical=Path(os.environ.get('F1_F0F_SOURCE_KEY_FILE') or str(Path(os.environ['F1_SECRETS_DIR'])/'f0f_source_key'))
        if historical.exists() or os.environ.get('F1_F0F_SOURCE_KEY_FILE'):
            keys['f0f_source_key']=historical
        proof_key=Path(os.environ.get('F1_MATERIAL_RAG_MANIFEST_KEY_FILE') or str(Path(os.environ['F1_SECRETS_DIR'])/'f1_material_rag_manifest_key'))
        if proof_key.exists() or os.environ.get('F1_MATERIAL_RAG_MANIFEST_KEY_FILE'):
            keys['f1_material_rag_manifest_key']=proof_key
        result=operation.backup(connect,client,pg,storage_identity,args.package,keys)
        operation.write_private(output.parent,output.name,operation.canonical(result))
    elif args.mode=='plan':
        manifest=operation.verify_package(args.package,args.manifest_sha256)
        origin={**manifest['source'],'storage_identity':manifest['storage_identity']}
        target=operation.empty_target(connect,client,pg,origin,storage_identity)
        result={'status':'RESTORE_PLAN_READY','manifest_sha256':args.manifest_sha256,'target':target,'source':origin}
        operation.write_private(output.parent,output.name,operation.canonical(result))
    elif args.mode=='restore':
        plan=json.loads(_read_secure(args.plan.absolute(),unavailable_code='RESTORE_PLAN_INVALID',
                        minimum_size=1,maximum_size=16384))
        if (set(plan)!={'status','manifest_sha256','target','source'} or plan['status']!='RESTORE_PLAN_READY'
                or plan['manifest_sha256']!=args.manifest_sha256):
            raise operation.BackupError('RESTORE_PLAN_INVALID')
        result=operation.restore(connect,client,pg,storage_identity,args.package,args.manifest_sha256,output,
            lambda c,database:migrate_f1._provision_roles(c,database,target=operation.HEAD),plan['target'])
    else:
        if not args.restore_result:
            parser.error('--restore-result is required for check-runtime')
        receipt=json.loads(_read_secure(args.restore_result.absolute(),unavailable_code='RESTORE_RESULT_INVALID',
                           minimum_size=1,maximum_size=16384))
        result=operation.runtime_check(connect,storage_identity,args.package,args.manifest_sha256,receipt)
        operation.write_private(output.parent,output.name,operation.canonical(result))
    print(json.dumps({'status':result['status'],'output':str(output),
                      'manifest_sha256':result['manifest_sha256']},ensure_ascii=False))
    return 2 if result['status']=='WAIT_LEASE_EXPIRY' else 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # No DSN, object metadata, SQL contents, credentials or private key bytes.
        code=str(error) if isinstance(error,operation.BackupError) else 'ANALYSIS_REPORT_BACKUP_FAILED'
        print(json.dumps({'status':'FAILED','code':code}))
        raise SystemExit(1)
