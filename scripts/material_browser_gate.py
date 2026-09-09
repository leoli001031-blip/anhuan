#!/usr/bin/env python3
"""One isolated Linux Compose + Chrome material journey. Synthetic OCR, real I/O.

No production data or cloud calls. Every invocation owns a random project;
failures retain logs and clean only that exact project. Run via acceptance_gate.
"""
from __future__ import annotations
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import subprocess
import stat
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]


class BrowserInterrupted(RuntimeError):
    pass

def bounded_command(args, *, environment, timeout, on_tick=None):
    """Retain output and terminate the owned process group on timeout/signal."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        caught=None;process=None
        try:
            process=subprocess.Popen(args,cwd=ROOT,env=environment,stdin=subprocess.DEVNULL,
                stdout=out,stderr=err,start_new_session=os.name=='posix')
            if on_tick is None:
                process.wait(timeout=timeout)
            else:
                deadline=time.monotonic()+timeout
                while process.poll() is None:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
                    try:process.wait(timeout=min(.5,remaining))
                    except subprocess.TimeoutExpired:on_tick()
        except BaseException as exc:
            caught=exc
            if process is not None and process.poll() is None:
                if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
                else:process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
                    else:process.kill()
                    process.wait(timeout=5)
        finally:
            out.seek(0);stdout=out.read().decode('utf-8','replace')
            err.seek(0);stderr=err.read().decode('utf-8','replace')
    return subprocess.CompletedProcess(args,process.returncode if process else None,stdout,stderr),caught


def bounded_shared_fingerprint():
    code="from infra.f1 import analysis_report_uat as u;import sys;sys.stdout.buffer.write(u._shared_fingerprint())"
    return subprocess.check_output([sys.executable,'-B','-c',code],cwd=ROOT,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'src')])},timeout=30)


def fixtures(directory):
    from PIL import Image, ImageDraw
    from fpdf import FPDF
    from xml.etree import ElementTree as ET
    from tests.test_docx_package_compatibility import rewrite as docx_rewrite
    from tests.test_xlsx_native_evidence import rewrite as xlsx_rewrite, q, cell
    from platform_foundation.f1.features.evidence.docx_native import W
    from platform_foundation.f1.features.material_intake.jpeg_renderer import render_jpeg
    from platform_foundation.f1.features.material_intake.pdf_renderer import render_pdf_page
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import certifi
    directory.mkdir(mode=0o755)
    pdf=FPDF();pdf.add_page();pdf.set_font('Helvetica',size=14)
    pdf.multi_cell(180,8,'ZXPDF 42 mg/L\nSynthetic wastewater inspection record. Sample number ZXPDF.\nDischarge concentration 42 mg/L. Original evidence for engineering verification.')
    from pypdf import PdfReader, PdfWriter
    writer=PdfWriter(clone_from=PdfReader(io.BytesIO(bytes(pdf.output()))))
    writer.root_object.pop('/OpenAction',None)  # FPDF's default navigation is intentionally rejected by P3.
    pdf_stream=io.BytesIO();writer.write(pdf_stream);raw_pdf=pdf_stream.getvalue()
    def replace_document(root,text):
        body=root.find('{'+W+'}body')
        for child in list(body): body.remove(child)
        p=ET.SubElement(body,'{'+W+'}p');r=ET.SubElement(p,'{'+W+'}r');ET.SubElement(r,'{'+W+'}t').text=text
    raw_doc=docx_rewrite({'word/document.xml':lambda root:replace_document(root,'ZXDOCX 43 mg/L')})
    shared_text='Synthetic review method: compare every value, unit and original location before approval.'
    raw_shared=docx_rewrite({'word/document.xml':lambda root:replace_document(root,shared_text)})
    def sheet(root):
        target=cell(root,'A1')
        for child in list(target):target.remove(child)
        target.set('t','inlineStr');ET.SubElement(ET.SubElement(target,q('is')),q('t')).text='ZXXLSX 44 mg/L'
    raw_xlsx=xlsx_rewrite({'xl/worksheets/sheet1.xml':sheet})
    img=Image.new('RGB',(1000,180),'white');ImageDraw.Draw(img).text((30,60),'ZXJPEG 45 mg/L',fill='black',font_size=44)
    stream=io.BytesIO();img.save(stream,'JPEG',quality=95);raw_jpeg=stream.getvalue()
    entries=[]
    for identity,filename,mime,raw,locator,expected in [
        ('pdf','chain.pdf','application/pdf',raw_pdf,'pdf_page','ZXPDF 42 mg/L'),
        ('docx','chain.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document',raw_doc,'docx_block','ZXDOCX 43 mg/L'),
        ('xlsx','chain.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',raw_xlsx,'xlsx_cells','ZXXLSX 44 mg/L'),
        ('jpeg','chain.jpg','image/jpeg',raw_jpeg,'image','ZXJPEG 45 mg/L')]:
        path=directory/filename;path.write_bytes(raw);path.chmod(0o644)
        entries.append(dict(id=identity,filename=filename,mime=mime,path=str(path),sha256=hashlib.sha256(raw).hexdigest(),expected_text=expected,locator_kind=locator))
    shared_path=directory/'shared-method.docx';shared_path.write_bytes(raw_shared);shared_path.chmod(0o644)
    shared_file=dict(id='shared_docx',filename=shared_path.name,mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        path=str(shared_path),sha256=hashlib.sha256(raw_shared).hexdigest(),expected_text=shared_text,locator_kind='docx_block')
    raw_new=docx_rewrite({'word/document.xml':lambda root:replace_document(root,'ZXNEW 46 mg/L')})
    resume_text='Synthetic resumable upload identity remains unchanged after a lost receipt.'
    raw_resume=docx_rewrite({'word/document.xml':lambda root:replace_document(root,resume_text)})
    def unresolved_formula(root):
        target=cell(root,'A1')
        for child in list(target):target.remove(child)
        target.attrib.pop('t',None)
        ET.SubElement(target,q('f')).text='42+4'
        ET.SubElement(target,q('v')).text='46'
    raw_partial=xlsx_rewrite({'xl/worksheets/sheet1.xml':unresolved_formula})
    extras={}
    for identity,filename,raw,mime,expected,locator in [
        ('new_docx','chain-new.docx',raw_new,shared_file['mime'],'ZXNEW 46 mg/L','docx_block'),
        ('partial_xlsx','formula-unresolved.xlsx',raw_partial,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','XLSX_FORMULA_UNRESOLVED','xlsx_cells'),
        ('resume_docx','resume-note.docx',raw_resume,shared_file['mime'],resume_text,'docx_block'),
        ('invalid_file','unsupported.bin',b'Synthetic unsupported format.','application/octet-stream','UNSUPPORTED_EXTENSION',None)]:
        extra_path=directory/filename;extra_path.write_bytes(raw);extra_path.chmod(0o644)
        extras[identity]=dict(id=identity,filename=filename,mime=mime,path=str(extra_path),sha256=hashlib.sha256(raw).hexdigest(),expected_text=expected,locator_kind=locator)
    (directory/'manifest.json').write_text(json.dumps({'scope':'SYNTHETIC_ENGINEERING','files':entries,'shared_file':shared_file,'extra_files':extras}))
    rendered=render_jpeg(raw_jpeg,expected_sha256=hashlib.sha256(raw_jpeg).hexdigest())
    pdf_render=render_pdf_page(raw_pdf,1)
    (directory/'ocr_responses.json').write_text(json.dumps({
        hashlib.sha256(rendered.image).hexdigest():{'format':'jpeg','text':'ZXJPEG 45 mg/L'},
        hashlib.sha256(pdf_render).hexdigest():{'format':'pdf','text':'ZXPDF 42 mg/L\nSynthetic wastewater inspection record. Sample number ZXPDF.\nDischarge concentration 42 mg/L. Original evidence for engineering verification.'}}))
    (directory/'ocr_key').write_text(secrets.token_hex(32));(directory/'ocr_key').chmod(0o600)
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'synthetic-ocr')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
        .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('synthetic-ocr')]),critical=False).sign(key,hashes.SHA256()))
    pem=cert.public_bytes(serialization.Encoding.PEM)
    (directory/'tls.pem').write_bytes(pem)
    (directory/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (directory/'tls.key').chmod(0o600)
    (directory/'ca.pem').write_bytes(Path(certifi.where()).read_bytes()+b'\n'+pem)
    return entries


def run(evidence:Path, *, recovery_fault=False):
    from infra.f1 import analysis_report_uat as U
    run_id=uuid.uuid4().hex
    U.WORKSPACE_SHA256=hashlib.sha256(('material-chain:'+run_id).encode()).hexdigest();U.PROBE=U.WORKSPACE_SHA256[:12]
    identity=U._identity();identity['control_dir']=str((Path('/private/tmp') if sys.platform=='darwin' else Path(tempfile.gettempdir()))/('anhuan-ar-pgint-'+U.PROBE))
    project=identity['compose_project'];control=Path(identity['control_dir'])
    browser_control=Path(tempfile.gettempdir())/('pwa-update-'+secrets.token_hex(12))
    report={'scope':'SYNTHETIC_BROWSER_ENGINEERING','ocr':'HASH_BOUND_SYNTHETIC_PDF_JPEG_TLS_RESPONSE','project':project,'status':'NOT_TESTED','commands':[],'checks':[]}
    evidence.mkdir(parents=True,exist_ok=True)
    def persist(): (evidence/'browser-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    original_run=U._run
    state=paths=None;shared=None;owns_control=False;started=time.monotonic()
    def command(args,*,paths,timeout,check=True,on_tick=None):
        if 'compose' in args:
            assert args[args.index('--project-name')+1]==project
        number=len(report['commands'])+1;begin=time.monotonic()
        entry={'ordinal':number,'command':args,'exit_code':None,'state':'RUNNING','timeout_seconds':timeout}
        report['commands'].append(entry);persist()
        cp,caught=bounded_command(args,environment=U._environment(paths),timeout=timeout,on_tick=on_tick)
        entry['exit_code']=cp.returncode
        if caught is None:entry.update(state='FINISHED')
        elif isinstance(caught,subprocess.TimeoutExpired):entry.update(state='TIMED_OUT')
        else:entry.update(state='INTERRUPTED' if isinstance(caught,(KeyboardInterrupt,BrowserInterrupted)) else 'ERROR',error_type=type(caught).__name__)
        try:
            stdout,stderr=cp.stdout,cp.stderr
            text=stdout+'\nSTDERR\n'+stderr
            for path in paths['secrets'].iterdir():
                try:value=path.read_text().strip()
                except (UnicodeError,OSError):continue
                if len(value)>=8:text=text.replace(value,'[SYNTHETIC_SECRET_REDACTED]')
            log=evidence/f'command-{number:02d}.log';log.write_text(text)
            entry.update(seconds=round(time.monotonic()-begin,3),log=str(log),log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
            persist();print(f'material-browser command {number}: state={entry["state"]} exit={entry["exit_code"]}',flush=True)
        finally:
            persist()
        if caught is not None:raise caught
        if check and cp.returncode:raise U.UatError(U._command_failure(args,cp))
        return cp
    def inventory():
        counts=[]
        for args in [['ps','-a','-q','--no-trunc'],['volume','ls','-q'],['network','ls','-q']]:
            cp=command([U.LC._docker(),*args,'--filter','label=com.docker.compose.project='+project],paths=paths,timeout=15)
            counts.append(len(cp.stdout.splitlines()))
        return tuple(counts)
    def compose(*args,timeout=600):
        return command([U.LC._docker(),'compose','--ansi','never','--project-name',project,'-f',str(control/'resolved.json'),'--profile','ops','--profile','analysis-report',*args],paths=paths,timeout=timeout)
    def interrupted(signum,frame): raise BrowserInterrupted('BROWSER_RUN_INTERRUPTED')
    previous_handler=signal.signal(signal.SIGTERM,interrupted)
    clean_env={k:v for k,v in os.environ.items() if not k.startswith(('A_ECO_CLOUD_','F1_MATERIAL_CLOUD_','F1_MATERIAL_ANALYSIS_REPORT_LLM')) and k!='A_ECO_REPORT_LLM'}
    clean_env.update(F1_KEYCLOAK_ISSUER_URL='http://material-rag.invalid/realms/anhuan')
    try:
        shared=bounded_shared_fingerprint()
        assert not control.exists() and not browser_control.exists()
        owns_control=True
        with patch.dict(os.environ,clean_env,clear=True),patch.object(U,'_identity',return_value=identity),patch.object(U,'_run',command):
            state,paths=U._initialize()
            assert inventory()==(0,0,0)
            browser_control=paths['tmp']/('pwa-update-'+secrets.token_hex(12))
            browser_control.mkdir(mode=0o700);materials=browser_control/'material-fixtures'
            report['inputs']=fixtures(materials)
            cp=command([U.LC._docker(),'compose','--project-name',project,'--env-file',str(paths['env']),'-f',str(U.COMPOSE_FILE),'-f',str(U.OVERLAY_FILE),'--profile','ops','--profile','analysis-report','config','--format','json'],paths=paths,timeout=60)
            cfg=json.loads(cp.stdout)
            for name in ['material-rag-ocr','material-rag-ocr-init']:cfg['services'].pop(name,None)
            for service in cfg['services'].values():
                service.get('depends_on',{}).pop('material-rag-ocr',None)
                service['volumes']=[v for v in service.get('volumes',[]) if v.get('source')!='material_ocr_socket']
                env=service.get('environment',{})
                if 'F1_MATERIAL_OCR_ENABLED' in env:env['F1_MATERIAL_OCR_ENABLED']='0'
                if 'F1_MATERIAL_ANALYSIS_REPORT_LLM' in env:env['F1_MATERIAL_ANALYSIS_REPORT_LLM']='0'
            for name in ['api','worker','ingestion-worker']:
                svc=cfg['services'][name]
                svc['environment'].update(F1_MATERIAL_CLOUD_OCR_PROVIDER='glm_vision',F1_MATERIAL_CLOUD_OCR_MODEL='synthetic-hash-bound',F1_MATERIAL_CLOUD_OCR_DIALECT='chat',F1_MATERIAL_CLOUD_OCR_BASE_URL='https://synthetic-ocr:8443',F1_MATERIAL_CLOUD_OCR_API_KEY_FILE='/synthetic/ocr_key')
                svc['volumes'] += [{'type':'volume','source':'synthetic_ocr_auth','target':'/synthetic','read_only':True},{'type':'bind','source':str(materials/'ca.pem'),'target':'/usr/local/lib/python3.11/site-packages/certifi/cacert.pem','read_only':True}]
            labels={'io.anhuan.scope':'analysis-report-uat','io.anhuan.project-id':state['project_id']}
            for name in ['synthetic_ocr_inputs','synthetic_ocr_auth']:
                cfg['volumes'][name]={'labels':labels}
            # Linux bind mounts retain the host UID. Keep host secrets at 0600,
            # and copy only the generated test inputs into owned runtime volumes.
            # API/worker mounts contain the auth key, never the server TLS key.
            cfg['services']['synthetic-input-init']={
                'image':cfg['services']['secret-init']['image'],'user':'0:0',
                'command':['/bin/sh','-ec',
                    'umask 077; cp /source/tls.pem /source/tls.key /source/ocr_responses.json /inputs/; '
                    'cp /source/ocr_key /auth/ocr_key; '
                    'chown -R 65532:65532 /inputs /auth; '
                    'chmod 0700 /inputs /auth; chmod 0400 /inputs/*; chmod 0600 /auth/ocr_key'],
                'network_mode':'none','read_only':True,'restart':'no','labels':labels,
                'volumes':[{'type':'bind','source':str(materials),'target':'/source','read_only':True},
                    {'type':'volume','source':'synthetic_ocr_inputs','target':'/inputs'},
                    {'type':'volume','source':'synthetic_ocr_auth','target':'/auth'}]}
            cfg['services']['synthetic-ocr']={'image':state['runtime_image'],'user':'65532:65532','command':['python','-B','/runner/synthetic_ocr_server.py'],'volumes':[{'type':'bind','source':str(ROOT/'scripts/synthetic_ocr_server.py'),'target':'/runner/synthetic_ocr_server.py','read_only':True},{'type':'volume','source':'synthetic_ocr_inputs','target':'/synthetic','read_only':True},{'type':'volume','source':'synthetic_ocr_auth','target':'/synthetic-auth','read_only':True}],'networks':['localnet'],'labels':labels,'read_only':True,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true'],'healthcheck':{'test':['CMD','python','-c',"import socket;socket.create_connection(('127.0.0.1',8443),2).close()"],'interval':'3s','timeout':'3s','retries':20}}
            pdf_input=next(item for item in report['inputs'] if item['id']=='pdf')
            cfg['services']['render-probe']={'image':state['runtime_image'],'network_mode':'none',
                'command':['python','-B','-c',"from pathlib import Path;import hashlib;from platform_foundation.f1.features.material_intake.pdf_renderer import render_pdf_page;data=Path('/input.pdf').read_bytes();assert hashlib.sha256(data).hexdigest()=="+repr(pdf_input['sha256'])+";print(hashlib.sha256(render_pdf_page(data,1)).hexdigest())"],
                'volumes':[{'type':'bind','source':pdf_input['path'],'target':'/input.pdf','read_only':True}],
                'labels':{'io.anhuan.scope':'analysis-report-uat','io.anhuan.project-id':state['project_id']}}
            (control/'resolved.json').write_text(json.dumps(cfg));(control/'resolved.json').chmod(0o600)
            compose('run','--rm','--no-deps','secret-init',timeout=180)
            compose('run','--rm','--no-deps','storage-secret-init',timeout=180)
            compose('build','migrator','web',timeout=1800)
            rendered=compose('run','--rm','--no-deps','render-probe',timeout=30).stdout.strip()
            assert len(rendered)==64 and all(c in '0123456789abcdef' for c in rendered)
            mappings=json.loads((materials/'ocr_responses.json').read_text())
            old=next(k for k,v in mappings.items() if v['format']=='pdf')
            mappings[rendered]=mappings.pop(old)
            (materials/'ocr_responses.json').write_text(json.dumps(mappings))
            report['test_ocr_pdf_render']={'host_sha256':old,'candidate_linux_sha256':rendered,'source_sha256':pdf_input['sha256']};persist()
            compose('run','--rm','--no-deps','synthetic-input-init',timeout=60)
            permission_probe=compose('run','--rm','--no-deps','synthetic-ocr','python','-B','-c',
                "from pathlib import Path;import os,ssl,stat;assert os.geteuid()==65532;"
                "files=[Path('/synthetic/tls.key'),Path('/synthetic/tls.pem'),Path('/synthetic/ocr_responses.json'),Path('/synthetic-auth/ocr_key')];"
                "assert all(p.stat().st_uid==65532 and stat.S_IMODE(p.stat().st_mode)==(0o600 if p.name=='ocr_key' else 0o400) and p.read_bytes() for p in files);"
                "assert sorted(p.name for p in Path('/synthetic-auth').iterdir())==['ocr_key'];"
                "ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain('/synthetic/tls.pem','/synthetic/tls.key');print('SYNTHETIC_INPUT_PERMISSIONS_PASSED')",timeout=30)
            assert permission_probe.stdout.strip()=='SYNTHETIC_INPUT_PERMISSIONS_PASSED'
            assert all(stat.S_IMODE((materials/name).stat().st_mode)==0o600 for name in ['tls.key','ocr_key'])
            report['synthetic_input_permissions']={'status':'PASSED','runtime_uid':65532,'tls_key_mode':'0400','auth_key_mode':'0600','host_secret_mode':'0600','auth_volume_contains_tls_key':False};persist()
            compose('up','-d','--wait','--wait-timeout','360','postgres','keycloak','minio','redis','clamd',timeout=420)
            compose('run','--rm','--no-deps','storage-provisioner',timeout=180)
            compose('run','--rm','migrator',timeout=600)
            U._seed_identities(state,paths)
            compose('run','--rm','keycloak-provisioner',timeout=180)
            compose('up','-d','--wait','--wait-timeout','180','synthetic-ocr','api','worker','dispatcher','ingestion-worker','report-worker','source-gateway','web',timeout=240)
            U._rewrite_host_bootstrap_dsn(state,paths)
            fixture=U._load_fixture()
            def empty_materials(connection,creator):
                fixture._provider_scope_id(connection);fixture._ensure_client_scope(connection)
            with patch.dict(os.environ,U._pg_env(state,paths)),patch.object(fixture,'_ensure_synthetic_materials',empty_materials):
                fixture.apply()
            origin=f"http://127.0.0.1:{state['web_port']}";report['origin']=origin;persist()
            ops_config=control/'ops-config.json'
            def observe_ops(label,expected_statuses):
                output=evidence/f'ops-{label}.json'
                observed=command([sys.executable,'-B',str(ROOT/'scripts/candidate_ops_check.py'),
                    '--config',str(ops_config),'--output',str(output)],paths=paths,timeout=60,check=False)
                result=json.loads(output.read_text())
                assert observed.returncode=={'PASSED':0,'ALERT':1,'FAILED':1,'NOT_TESTED':2}[result['status']]
                for check_name,status in expected_statuses.items():
                    assert result['checks'][check_name]['status']==status, f'OPS_{label}_{check_name}_MISMATCH'
                report.setdefault('ops_runtime_observations',{})[label]={'status':result['status'],'checks':{key:value['status'] for key,value in result['checks'].items()},'file':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()};persist()
            def ingestion_snapshot():
                import psycopg
                from infra.f1.migrate_f1 import _bootstrap_dsn
                with patch.dict(os.environ,U._pg_env(state,paths)):
                    dsn=_bootstrap_dsn()
                with psycopg.connect(dsn,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=5000') as connection:
                    assert connection.execute('SELECT current_database()').fetchone()[0]==state['database']
                    return [dict(zip(('id','version_id','state','attempt','reason'),row)) for row in connection.execute(
                        'SELECT id::text,document_version_id::text,state,attempt,reason_code FROM f1.material_ingestion_delivery ORDER BY id').fetchall()]
            recovered=False
            if recovery_fault:
                import psycopg
                from scripts import candidate_ops_check as ops
                from platform_foundation.f1.maintenance.candidate_backup import migration_identity,HEAD
                from infra.f1.migrate_f1 import _bootstrap_dsn
                ids=compose('ps','-q',*sorted(ops.REQUIRED_SERVICES),timeout=15).stdout.splitlines()
                observed=command([U.LC._docker(),'inspect','--type','container','--format',ops.INSPECT_TEMPLATE,*ids],paths=paths,timeout=15)
                services={item['service']:{'container_id':item['id'],'image_id':item['image_id']} for item in (json.loads(line) for line in observed.stdout.splitlines())}
                with patch.dict(os.environ,U._pg_env(state,paths)):dsn=_bootstrap_dsn()
                with psycopg.connect(dsn,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=5000') as connection:
                    cluster=str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
                config={'schema_version':1,'scope':'local_candidate','candidate_id':project,'compose_project':project,
                    'docker_host':U._environment(paths)['DOCKER_HOST'],'database':{'dsn_file':str(paths['secrets']/'f1_bootstrap_dsn'),'name':state['database'],'cluster':cluster,'head':HEAD},
                    'services':services,'thresholds':{**ops.DEFAULT_THRESHOLDS,'max_retry_wait':0},'migration_source_sha256':migration_identity()}
                ops_config.write_text(json.dumps(config));ops_config.chmod(0o600)
                observe_ops('baseline',{'services':'PASSED','database':'PASSED','readiness':'PASSED','objects':'NOT_TESTED'})
                # Faults affect this random empty stack only. Uploads still have
                # to receive durable HTTP receipts while the queue is absent.
                compose('stop','--timeout','10','redis',timeout=30)
                report['runtime_recovery']={'state':'QUEUE_UNAVAILABLE','scope':'QUEUED_UPLOADS_AND_WORKER_RESTART'};persist()
            def supervise_recovery():
                nonlocal recovered
                if not recovery_fault or recovered:return
                journal=browser_control/'material-checks.jsonl'
                if not journal.exists():return
                rows=[]
                for line in journal.read_text().splitlines():
                    try:rows.append(json.loads(line))
                    except json.JSONDecodeError:continue  # a concurrent append may be incomplete
                if not any(row.get('event')=='check' and row.get('id')=='batch_upload_four_formats' and row.get('outcome')=='passed' for row in rows):return
                before=ingestion_snapshot()
                if len(before)!=4 or any(row['state']!='retry_wait' or row['reason']!='MATERIAL_INGESTION_QUEUE_UNAVAILABLE' for row in before):return
                report['runtime_recovery'].update(state='RETRY_WAIT_OBSERVED',before=before);persist()
                observe_ops('queue-outage',{'services':'ALERT','database':'ALERT','readiness':'ALERT','objects':'NOT_TESTED'})
                compose('kill','--signal','SIGKILL','worker','ingestion-worker','report-worker',timeout=30)
                compose('up','-d','--wait','--wait-timeout','90','redis',timeout=120)
                compose('up','-d','--wait','--wait-timeout','90','worker','ingestion-worker','report-worker',timeout=120)
                recovered=True
                report['runtime_recovery']['state']='SERVICES_RESTARTED';persist()
            node=Path(shutil.which('node') or '/missing-node').resolve(strict=True)
            assert stat.S_ISREG(node.stat().st_mode) and not node.stat().st_mode & (stat.S_IWGRP|stat.S_IWOTH) and os.access(node,os.X_OK)
            cp=command([str(node),str(U.LC.PWA_BROWSER_RUNNER),origin,str(paths['secrets']),'--stage','material-chain','--pwa-update-control',str(browser_control)],paths=paths,timeout=1200,check=False,on_tick=supervise_recovery if recovery_fault else None)
            if cp.returncode:raise RuntimeError('MATERIAL_BROWSER_STAGE_FAILED')
            lines=cp.stdout.strip().splitlines();assert lines[-1]=='LOCAL_MATERIAL_CHAIN_BROWSER_OK'
            report['journey']=json.loads(lines[-2])
            if recovery_fault:
                before=report['runtime_recovery'].get('before',[])
                after=[row for row in ingestion_snapshot() if row['id'] in {r['id'] for r in before}]
                assert recovered and len(after)==4 and {r['id'] for r in before}=={r['id'] for r in after}
                attempts={r['id']:r['attempt'] for r in before}
                assert all(row['state']=='done' and row['attempt']>attempts[row['id']] for row in after)
                report['runtime_recovery'].update(state='PASSED',after=after,same_delivery_ids=True)
            runtime={}
            for service,required in [('worker','f1_worker_password'),('ingestion-worker','f1_ingestion_worker_password'),('report-worker','f1_report_worker_password')]:
                files=compose('exec','-T',service,'python','-c',"import os,json;print(json.dumps(sorted(os.listdir('/run/secrets/f1'))))",timeout=15)
                names=json.loads(files.stdout)
                assert required in names and 'f1_api_password' not in names and not any('minio' in name or name.startswith('f1_storage_') for name in names)
                runtime[service]={'required_login':required,'api_password_present':False,'storage_credentials_present':False}
            report['restricted_runtime_credentials']=runtime
            ocr=compose('logs','--no-color','synthetic-ocr',timeout=15)
            counts={'pdf':0,'jpeg':0};events=[]
            for line in ocr.stdout.splitlines():
                if 'synthetic_ocr_response' in line:
                    event=json.loads(line[line.index('{'):]);counts[event['format']]+=1;events.append(event)
            report['ocr_http_observations']={'counts':counts,'events':events,'scope':'SYNTHETIC_RESPONSE_ACTUAL_HTTP'}
            assert counts=={'pdf':1,'jpeg':1}, 'OCR_EXPECTED_ONE_HTTP_CALL_PER_INPUT'
            report['execution_state']='CHECKS_PASSED_CLEANUP_PENDING'
    except BaseException as exc:
        report['status']='FAILED';report['error']={'type':type(exc).__name__,'reason':str(exc)[:500]}
    finally:
        journal=browser_control/'material-checks.jsonl'
        if journal.exists():shutil.copyfile(journal,evidence/'material-checks.jsonl')
        for screenshot in browser_control.glob('*-failure.png'):
            shutil.copyfile(screenshot,evidence/screenshot.name)
        if state is not None and paths is not None:
            try:
                if (control/'resolved.json').exists():
                    try:
                        compose('logs','--no-color','--tail','100',timeout=20)
                    except BaseException as exc:
                        report['log_capture_error']=type(exc).__name__;report['status']='FAILED'
                    compose('down','--volumes','--timeout','30',timeout=120)
                counts=inventory();report['cleanup']={'containers':counts[0],'volumes':counts[1],'networks':counts[2]}
                assert counts==(0,0,0)
                remaining_images=[]
                for name in ['runtime_image','web_image']:
                    original_run([U.LC._docker(),'image','rm',state[name]],paths=paths,timeout=20,check=False)
                    inspection=original_run([U.LC._docker(),'image','inspect','--format','{{.Id}}',state[name]],paths=paths,timeout=10,check=False)
                    if inspection.returncode==0:remaining_images.append(state[name])
                    elif inspection.returncode!=1 or not any(marker in inspection.stderr.lower() for marker in ['no such image','no such object']):
                        raise RuntimeError('BROWSER_OWN_IMAGE_INVENTORY_FAILED')
                report['owned_image_tags_removed']=not remaining_images
                assert not remaining_images
                shutil.rmtree(control);report['cleanup']['control_removed']=True
            except BaseException as exc:report['cleanup_error']=str(exc)[:200];report['status']='FAILED'
        elif owns_control and control.exists():
            # Initialization creates files only, before any Compose operation.
            try:
                assert control.is_dir() and not control.is_symlink()
                shutil.rmtree(control);report['partial_initialization_control_removed']=True
            except BaseException as exc:report['cleanup_error']=type(exc).__name__;report['status']='FAILED'
        if browser_control.exists():
            journal=browser_control/'material-checks.jsonl'
            if journal.exists():shutil.copyfile(journal,evidence/'material-checks.jsonl')
            shutil.rmtree(browser_control)
        signal.signal(signal.SIGTERM,previous_handler)
        try:
            report['shared_unchanged']=shared is not None and bounded_shared_fingerprint()==shared
        except BaseException as exc:
            report['shared_unchanged']=False;report['shared_check_error']=type(exc).__name__
        if not report['shared_unchanged']:report['status']='FAILED'
        if report['status']!='FAILED' and report.get('execution_state')=='CHECKS_PASSED_CLEANUP_PENDING':
            report['status']='PASSED';report['execution_state']='COMPLETE'
        report['seconds']=round(time.monotonic()-started,3);persist()
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence-dir',type=Path,required=True)
    p.add_argument('--recovery-fault',action='store_true',help='Stop this isolated queue during upload and restart workers before completion')
    a=p.parse_args()
    report=run(a.evidence_dir.resolve(),recovery_fault=a.recovery_fault);print(json.dumps({'status':report['status'],'evidence':str(a.evidence_dir/'browser-result.json')}));return 0 if report['status']=='PASSED' else 1

if __name__=='__main__':raise SystemExit(main())
