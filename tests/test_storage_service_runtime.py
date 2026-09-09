"""Real isolated MinIO policy denials; no shared objects, volumes or secrets."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import unittest
from urllib.request import urlopen
from unittest.mock import patch
import uuid
from minio import Minio
from minio.credentials import StaticProvider
from minio.error import S3Error, MinioAdminException
from minio.minioadmin import MinioAdmin
from infra.f1.analysis_report_storage import provision, STORAGE_ROLES, BUCKETS
from infra.f1.analysis_report_postgres_integration import canonical_shared_fingerprint

IMAGE='minio/minio:RELEASE.2024-07-29T22-14-52Z@sha256:29110b4abbcc7c2a71f19f5e375d50c2771c94272efba59c9a0532c88403672d'
SCOPE='material-service-storage'
RUN=uuid.uuid4().hex
CID=NETWORK=TEMP=None
ENDPOINT=''
IDENTITIES={role:('svc'+secrets.token_hex(12),secrets.token_hex(32)) for role in STORAGE_ROLES}
ROOT_USER='root'+secrets.token_hex(12)
ROOT_PASSWORD=secrets.token_hex(32)
BEFORE=None


def docker(*args):
    return subprocess.check_output(['docker',*args],text=True,stderr=subprocess.PIPE,timeout=60).strip()


def cleanup():
    if CID:
        labels=json.loads(docker('inspect','--format','{{json .Config.Labels}}',CID))
        if labels.get('io.anhuan.scope')!=SCOPE or labels.get('io.anhuan.storage-run')!=RUN:raise AssertionError('STORAGE_CLEANUP_IDENTITY_MISMATCH')
        docker('rm','-f',CID)
    if NETWORK:
        labels=json.loads(docker('network','inspect','--format','{{json .Labels}}',NETWORK))
        if labels.get('io.anhuan.storage-run')!=RUN:raise AssertionError('STORAGE_NETWORK_IDENTITY_MISMATCH')
        docker('network','rm',NETWORK)
    if TEMP:TEMP.cleanup()


def setUpModule():
    global CID,NETWORK,TEMP,ENDPOINT,BEFORE
    BEFORE=canonical_shared_fingerprint();TEMP=tempfile.TemporaryDirectory(prefix='anhuan-storage-')
    env=Path(TEMP.name)/'minio.env';env.write_text(f'MINIO_ROOT_USER={ROOT_USER}\nMINIO_ROOT_PASSWORD={ROOT_PASSWORD}\n');env.chmod(0o600)
    try:
        NETWORK=docker('network','create','--label',f'io.anhuan.scope={SCOPE}','--label',f'io.anhuan.storage-run={RUN}','anhuan-storage-'+RUN[:12])
        CID=docker('run','-d','--name','anhuan-storage-'+RUN[:12],'--label',f'io.anhuan.scope={SCOPE}','--label',f'io.anhuan.storage-run={RUN}',
                   '--network',NETWORK,'--publish','127.0.0.1::9000','--tmpfs','/data:rw,size=128m','--env-file',str(env),IMAGE,'server','/data')
        ENDPOINT=docker('port',CID,'9000/tcp')
        if not ENDPOINT.startswith('127.0.0.1:'):raise AssertionError('STORAGE_LOOPBACK_REQUIRED')
        deadline=time.monotonic()+45
        while True:
            try:
                with urlopen('http://'+ENDPOINT+'/minio/health/ready',timeout=2) as response:
                    if response.status==200:break
            except Exception:
                if time.monotonic()>deadline:raise RuntimeError('STORAGE_READINESS_TIMEOUT') from None
                time.sleep(.2)
        provision(ENDPOINT,ROOT_USER,ROOT_PASSWORD,IDENTITIES)
        # Provisioning replays against the same server; no policy accumulation.
        provision(ENDPOINT,ROOT_USER,ROOT_PASSWORD,IDENTITIES)
        print('STORAGE_RUNTIME_READY='+RUN,flush=True)
    except BaseException:
        cleanup();raise


def tearDownModule():
    cleanup()
    if canonical_shared_fingerprint()!=BEFORE:raise AssertionError('STORAGE_SHARED_CHANGED')
    if docker('ps','-aq','--filter',f'label=io.anhuan.storage-run={RUN}') or docker('network','ls','-q','--filter',f'label=io.anhuan.storage-run={RUN}'):
        raise AssertionError('STORAGE_RESOURCES_REMAIN')
    print('STORAGE_CLEANUP=CLEAN;SHARED_UNCHANGED=1',flush=True)


class ServiceStorageRuntimeTests(unittest.TestCase):
    def test_candidate_secret_init_removes_runtime_root_and_preserves_replayed_identities(self):
        from infra.f1 import analysis_report_uat as uat
        directory=Path(TEMP.name)/'candidate-secrets';directory.mkdir(mode=0o700)
        uat._write_secrets({'database':'f1_storage_probe'},{'secrets':directory})
        uat._validate_analysis_report_secret_set(directory)
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
        uat._write_secrets({'database':'f1_storage_probe'},{'secrets':directory})
        self.assertEqual(hashes,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()})
        mounts=[]
        destinations={}
        for name in ('api','worker','ingestion-worker','storage-provisioner','source-gateway','migrator','report-worker'):
            target=Path(TEMP.name)/name;target.mkdir();destinations[name]=target
            if name in ('api','worker','ingestion-worker'):
                for key in ('minio_root_user','minio_root_password'):(target/key).write_bytes((directory/key).read_bytes())
            if name=='report-worker':(target/'f1_api_password').write_bytes((directory/'f1_api_password').read_bytes())
            if name=='ingestion-worker':
                for key in ('f1_api_password','f1_worker_password'):(target/key).write_bytes((directory/key).read_bytes())
            mounts.extend(['--volume',str(target)+':/'+name])
        script=Path(__file__).resolve().parents[1]/'infra/f1/analysis-reports/storage_secret_init.sh'
        docker('run','--rm','--label',f'io.anhuan.scope={SCOPE}','--label',f'io.anhuan.storage-run={RUN}',
               '--network','none','--read-only','--volume',str(script)+':/init.sh:ro','--volume',str(directory)+':/source:ro',*mounts,
               'redis:7-alpine@sha256:e7723ff73d963f5cc6d9c4643ea3d989527a402a319239054e9472a7fb9219a2','/bin/sh','/init.sh')
        for role, destination in (('api','api'),('ingestion','source-gateway')):
            target=destinations[destination]
            self.assertEqual({p.name for p in target.iterdir()},{'minio_service_user','minio_service_password'} | ({'f1_source_reader_password'} if role=='ingestion' else set()))
            for kind in ('user','password'):
                self.assertEqual((target/f'minio_service_{kind}').read_bytes(),(directory/f'minio_{role}_{kind}').read_bytes())
                self.assertEqual((target/f'minio_service_{kind}').stat().st_mode&0o777,0o600)
        self.assertEqual({p.name for p in destinations['ingestion-worker'].iterdir()},{'f1_ingestion_worker_password'})
        self.assertEqual(list(destinations['worker'].iterdir()),[])
        self.assertEqual({p.name for p in destinations['migrator'].iterdir()},{'f1_source_reader_password','f1_report_worker_password','f1_ingestion_worker_password'})
        self.assertEqual({p.name for p in destinations['report-worker'].iterdir()},{'f1_report_worker_password'})
        self.assertEqual((destinations['report-worker']/'f1_report_worker_password').read_bytes(),(directory/'f1_report_worker_password').read_bytes())
        # Existing candidates may need new service identities. Adding them is
        # resumable without changing existing keys or accepting unknown files.
        (directory/'minio_worker_user').unlink()
        uat._write_secrets({'database':'f1_storage_probe'},{'secrets':directory})
        uat._validate_analysis_report_secret_set(directory)
        self.assertEqual(hashlib.sha256((directory/'f1_material_rag_key').read_bytes()).hexdigest(),hashes['f1_material_rag_key'])

    def test_candidate_compose_enables_service_credentials_only_in_specialized_overlays(self):
        root=Path(__file__).resolve().parents[1]
        values={'LOCAL_DATABASE':'f1_storage_probe','LOCAL_GID':str(os.getegid()),'LOCAL_UID':str(os.geteuid()),
                'LOCAL_PG_PORT':'54321','LOCAL_PROJECT_ID':RUN,'LOCAL_RUNTIME_IMAGE':'anhuan-storage-runtime:probe',
                'LOCAL_SECRETS_DIR':TEMP.name,'LOCAL_WEB_IMAGE':'anhuan-storage-web:probe','LOCAL_WEB_ORIGIN':'http://127.0.0.1:54322','LOCAL_WEB_PORT':'54322'}
        for mode in ('demo','uat'):
            raw=subprocess.check_output(['docker','compose','-f',str(root/'infra/f1/docker-compose.local.yml'),'-f',str(root/f'infra/f1/docker-compose.analysis-report-{mode}.yml'),
                                         '--profile','analysis-report','--profile','ops','config','--format','json'],env={**os.environ,**values},stderr=subprocess.PIPE,text=True,timeout=30)
            services=json.loads(raw)['services']
            for role in ('api','worker','ingestion-worker','dispatcher','report-worker'):
                self.assertEqual(services[role]['environment']['F1_PIPELINE_CONTINUATIONS_ON_INGESTION'],'1')
            for role in ('api','ingestion-worker'):
                self.assertEqual(services[role]['environment']['F1_PIPELINE_COORDINATOR_GATEWAY'],'1')
            for role in ('api','worker','ingestion-worker'):
                self.assertEqual(services[role]['environment']['F1_STORAGE_SERVICE_CREDENTIALS'],'1')
                self.assertFalse(any(v['source']=='minio_secrets' for v in services[role]['volumes']))
            for role in ('worker','ingestion-worker'):
                self.assertEqual(services[role]['environment']['F1_TASK_SOURCE_GATEWAY'],'1')
            self.assertEqual(services['ingestion-worker']['environment']['F1_INGESTION_WORKER_RESTRICTED'],'1')
            self.assertEqual(services['report-worker']['environment']['F1_REPORT_WORKER_RESTRICTED'],'1')
            self.assertEqual({v['source'] for v in services['report-worker']['volumes']},{'report_worker_secrets'})
            self.assertNotIn('ports',services['source-gateway'])
            self.assertEqual({v['source'] for v in services['source-gateway']['volumes']},{'source_gateway_secrets'})
            self.assertEqual(services['storage-secret-init']['network_mode'],'none')
            self.assertEqual(services['storage-provisioner']['command'][-1],'/app/infra/f1/analysis_report_storage.py')
        self.assertNotIn('F1_STORAGE_SERVICE_CREDENTIALS',(root/'infra/f1/docker-compose.local.yml').read_text())

    def client(self,role):
        user,password=IDENTITIES[role]
        return Minio(ENDPOINT,access_key=user,secret_key=password,secure=False)

    def denied(self,operation):
        with self.assertRaises(S3Error) as caught:operation()
        self.assertEqual(caught.exception.code,'AccessDenied')

    def test_services_normal_flow_and_worker_write_denial(self):
        from platform_foundation.f1 import storage
        raw=b'actual authorized source bytes';sha=hashlib.sha256(raw).hexdigest();key=uuid.uuid4().hex+'.docx'
        with patch.object(storage,'MINIO_ENDPOINT',ENDPOINT),patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1'}):
            for role in ('api',):
                values=dict(zip(('minio_service_user','minio_service_password'),IDENTITIES[role]))
                with patch.object(storage,'read_f1_secret_text',side_effect=lambda name,**kw:values[name]):
                    stored=storage.store_quarantine_stream(io.BytesIO(raw),content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',length=len(raw),object_key=key)
                    storage.release_ingestion_object(task_id=uuid.UUID(hex=key.split('.')[0]),object_key=key,expected_sha256=sha,expected_size=len(raw),expected_etag=stored.etag)
            values=dict(zip(('minio_service_user','minio_service_password'),IDENTITIES['worker']))
            with patch.object(storage,'read_f1_secret_text',side_effect=lambda name,**kw:values[name]):
                self.assertEqual(storage.read_released_native_source(key,sha,len(raw)),raw)
                self.assertEqual(storage.open_quarantine_source(key,sha,len(raw),stored.etag).read(),raw)
            worker=self.client('worker')
            for bucket in BUCKETS:
                self.denied(lambda:worker.put_object(bucket,uuid.uuid4().hex,io.BytesIO(b'x'),1))
                self.denied(lambda:worker.remove_object(bucket,key))
            self.denied(lambda:list(worker.list_objects(BUCKETS[0])))

    def test_real_secret_files_initialize_and_use_service_client(self):
        from infra.f1 import analysis_report_storage as provisioner
        from platform_foundation.f1 import storage
        with tempfile.TemporaryDirectory(prefix='real-secret-',dir=TEMP.name) as directory:
            folder=Path(directory);root=folder/'root';root.mkdir()
            for name,value in [('minio_root_user',ROOT_USER),('minio_root_password',ROOT_PASSWORD)]:
                path=root/name;path.write_text(value);path.chmod(0o600)
            for role in STORAGE_ROLES:
                for kind,value in zip(('user','password'),IDENTITIES[role]):
                    path=folder/f'minio_{role}_{kind}';path.write_text(value);path.chmod(0o600)
            env={'F1_LOCAL_ENGINEERING':'1','F1_SECRETS_DIR':directory,'MINIO_ENDPOINT':ENDPOINT,'F1_STORAGE_SERVICE_CREDENTIALS':'1'}
            for role in STORAGE_ROLES:
                for kind in ('USER','PASSWORD'):env[f'F1_MINIO_{role.upper()}_{kind}_FILE']=''
            env.update(F1_MINIO_SERVICE_USER_FILE='',F1_MINIO_SERVICE_PASSWORD_FILE='')
            with patch.dict(os.environ,env),patch.object(provisioner,'Path',side_effect=lambda path:root if path=='/run/minio-root' else Path(path)):
                self.assertEqual(provisioner.main(),0)
                for kind,value in zip(('user','password'),IDENTITIES['ingestion']):
                    path=folder/f'minio_service_{kind}';path.write_text(value);path.chmod(0o600)
                with patch.object(storage,'MINIO_ENDPOINT',ENDPOINT):
                    client=storage._client()
                    self.denied(lambda:list(client.list_objects(BUCKETS[0])))
                    key=uuid.uuid4().hex+'/secret-read.json'
                    client.put_object(BUCKETS[2],key,io.BytesIO(b'{}'),2)
                (folder/'minio_service_user').unlink()
                with self.assertRaisesRegex(RuntimeError,'F1_RUNTIME_SECRET_UNAVAILABLE'):storage._client()

    def test_ingestion_identity_only_writes_previews_including_multipart(self):
        from platform_foundation.f1 import storage
        client=self.client('ingestion')
        for bucket in BUCKETS:
            self.denied(lambda:list(client.list_objects(bucket)))
            self.denied(lambda:client.remove_object(bucket,'forbidden'))
        for bucket in BUCKETS[:2]:
            self.denied(lambda:client.put_object(bucket,'forbidden',io.BytesIO(b'x'),1))
        values=dict(zip(('minio_service_user','minio_service_password'),IDENTITIES['ingestion']))
        raw=b'x'*(20*1024*1024);sha=hashlib.sha256(raw).hexdigest();task=uuid.uuid4()
        with patch.object(storage,'MINIO_ENDPOINT',ENDPOINT),patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1'}),patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:values[n]):
            stored=storage.store_ingestion_preview_unit(task_id=task,unit_id=str(uuid.uuid5(task,'multipart:'+sha)),content=raw,content_type='image/jpeg')
            self.assertEqual(stored.size,len(raw))
            self.assertEqual(storage.read_ingestion_preview_unit(stored.object_key,sha,len(raw)),raw)
        readonly=dict(zip(('minio_service_user','minio_service_password'),IDENTITIES['worker']))
        with patch.object(storage,'MINIO_ENDPOINT',ENDPOINT),patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1','F1_PREVIEW_CONTENT_ADDRESSED':'1'}),patch.object(storage,'read_f1_secret_text',side_effect=lambda n,**kw:readonly[n]):
            with patch.object(storage,'_read_bucket_bytes',wraps=storage._read_bucket_bytes) as read:
                with self.assertRaisesRegex(storage.StorageError,'SOURCE_OBJECT_STAT_FAILED'):
                    storage.read_ingestion_preview_manifest(task_id=task,expected_sha256=sha)
                self.assertEqual(read.call_count,1)  # Access denied cannot trigger legacy fallback.

    def test_each_service_cannot_administer_buckets_users_or_unrelated_sources(self):
        root=Minio(ENDPOINT,access_key=ROOT_USER,secret_key=ROOT_PASSWORD,secure=False)
        other='unrelated-'+uuid.uuid4().hex;root.make_bucket(other);root.put_object(other,'secret',io.BytesIO(b'unrelated'),9)
        for role in STORAGE_ROLES:
            with self.subTest(role=role):
                client=self.client(role)
                self.denied(lambda:client.get_object(other,'secret'))
                self.denied(lambda:client.make_bucket('forbidden-'+uuid.uuid4().hex))
                self.denied(lambda:client.set_bucket_policy(BUCKETS[0],json.dumps({'Version':'2012-10-17','Statement':[]})))
                admin=MinioAdmin(endpoint=ENDPOINT,credentials=StaticProvider(*IDENTITIES[role]),secure=False)
                with self.assertRaises(MinioAdminException) as caught:admin.user_add('forbidden'+uuid.uuid4().hex,secrets.token_hex(32))
                self.assertIn('403',str(caught.exception._code))

    def test_missing_service_secret_cannot_fall_back_to_present_root(self):
        from platform_foundation.f1 import storage
        called=[]
        def read(name,**kwargs):
            called.append(name)
            if name.startswith('minio_root'):return ROOT_USER
            raise RuntimeError('SERVICE_SECRET_MISSING')
        with patch.dict(os.environ,{'F1_STORAGE_SERVICE_CREDENTIALS':'1'}),patch.object(storage,'read_f1_secret_text',side_effect=read):
            with self.assertRaisesRegex(RuntimeError,'SERVICE_SECRET_MISSING'):storage._client()
        self.assertEqual(called,['minio_service_user'])
