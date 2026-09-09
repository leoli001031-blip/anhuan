"""Current-head backup and restore into an explicitly empty, independent target.

This package contains private database rows and decryption keys. The manifest
digest is a separate trust anchor, printed in the backup receipt. Restore never
drops an existing database, overwrites an object or repairs an incomplete target.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import tempfile
import time

from psycopg import sql

from .backup_objects import (BUCKETS, MAX_OBJECTS, MAX_TOTAL_BYTES, export_objects,
                             inventory, md5, restore_objects, sha, validate_object)
from ..secret_files import _read_secure
from .backup_crypto import verify_ciphertexts

SCHEMA = 'anhuan-analysis-report-backup-v1'
HEAD = 'f1_0044'
PROTECTED_RLS_TABLE_COUNT = 55
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
KEY_NAMES = ('f1_material_rag_key', 'f1_qa_key', 'f0i_key', 'invite_signing_key')
MANIFEST_KEY = 'f1_material_rag_manifest_key'
OPTIONAL_KEY = 'f0f_source_key'
SCHEMAS = ('f0d', 'f0e', 'f0f', 'f0f_crypto', 'f0g', 'f0i', 'f1', 'f1_native_crypto')
ROOT = Path(__file__).resolve().parents[4]


class BackupError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False, default=str).encode()


def private_root(path):
    path = Path(path).absolute()
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid() or path.resolve() != path):
        raise BackupError('BACKUP_PRIVATE_DIRECTORY_REQUIRED')
    return path


def write_private(root, name, raw):
    if '/' in name or name in {'', '.', '..'}:
        raise BackupError('BACKUP_MEMBER_NAME_INVALID')
    fd = os.open(root/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def secure_read(root, name, maximum=MAX_PACKAGE_BYTES):
    return _read_secure(root/name, unavailable_code='BACKUP_MEMBER_INVALID',
                        minimum_size=0, maximum_size=maximum)


def directory_sync(root):
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migration_identity():
    paths = sorted([*(ROOT/'infra/f1/alembic/versions').glob('*.py'),*(ROOT/'migrations/versions').glob('*.py')])
    if not paths:
        raise BackupError('BACKUP_MIGRATION_SOURCE_UNAVAILABLE')
    return sha(canonical({str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in paths}))


def database_identity(c, *, require_head=True):
    database, user, cluster, version = c.execute(
        "SELECT current_database(),session_user,system_identifier::text,current_setting('server_version_num')::int "
        'FROM pg_control_system()').fetchone()
    if user != 'f0d_bootstrap' or version//10000 != 18:
        raise BackupError('BACKUP_DATABASE_IDENTITY_INVALID')
    result = {'database': database, 'cluster': cluster, 'postgres_version': version}
    if require_head:
        head = c.execute('SELECT version_num FROM f1.alembic_version').fetchall()
        f0_head = c.execute('SELECT version_num FROM f0d.alembic_version').fetchall()
        if head != [(HEAD,)] or f0_head != [('f0d_0006',)]:
            raise BackupError('BACKUP_CURRENT_HEAD_REQUIRED')
        result['head'] = HEAD
    return result


def require_no_other_clients(c):
    if c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() "
                 "AND pid<>pg_backend_pid() AND backend_type='client backend'").fetchone()[0]:
        raise BackupError('BACKUP_STOP_DATABASE_CLIENTS_REQUIRED')


def table_names(c):
    return c.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=ANY(%s) AND c.relkind='r' ORDER BY 1,2", (list(SCHEMAS),)).fetchall()


def snapshot(c):
    """Independent row and security-catalog proof, with no OIDs or passwords."""
    c.execute("SET LOCAL TIME ZONE 'UTC'")
    c.execute("SET LOCAL DateStyle='ISO, YMD'")
    names = table_names(c)
    protected = set(runpy.run_path(str(ROOT/'infra/f1/analysis-reports/migrate.py'))['EXPECTED_RLS_TABLES'])
    protected.update({'material_review_revision','material_review_fragment'})
    observed = {r[0] for r in c.execute("SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='f1' AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity")}
    if len(protected)!=PROTECTED_RLS_TABLE_COUNT or not protected.issubset(observed):
        raise BackupError('BACKUP_PROTECTED_RLS_TABLE_SET_MISMATCH')
    if c.execute('SELECT count(*) FROM pg_largeobject_metadata').fetchone()[0]:
        raise BackupError('BACKUP_LARGE_OBJECTS_UNSUPPORTED')
    unknown = c.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' "
                        "AND nspname NOT IN ('information_schema','public','f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto')").fetchall()
    if unknown:
        raise BackupError('BACKUP_UNKNOWN_SCHEMA')
    rows, total = {}, 0
    for schema, name in names:
        digest, count = hashlib.sha256(), 0
        with c.cursor(name='backup_rows') as cursor:
            cursor.execute(sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text')
                           .format(sql.Identifier(schema,name)))
            for row in cursor:
                raw = row[0].encode()
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise BackupError('BACKUP_DATABASE_BUDGET_EXCEEDED')
                digest.update(len(raw).to_bytes(8,'big')); digest.update(raw); count += 1
        rows[schema+'.'+name] = {'count': count, 'sha256': digest.hexdigest()}
    catalog_queries = {
        'schemas': "SELECT nspname,pg_get_userbyid(nspowner),ARRAY(SELECT acl::text FROM unnest(COALESCE(nspacl,acldefault('n',nspowner))) acl ORDER BY acl::text) FROM pg_namespace WHERE nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto','public') ORDER BY 1",
        'relations': "SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner),c.relrowsecurity,c.relforcerowsecurity,ARRAY(SELECT acl::text FROM unnest(COALESCE(c.relacl,CASE WHEN c.relkind='S' THEN acldefault('S',c.relowner) ELSE acldefault('r',c.relowner) END)) acl ORDER BY acl::text),c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2",
        'columns': "SELECT n.nspname,c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,a.attacl::text,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2,3",
        'constraints': "SELECT n.nspname,c.relname,k.conname,pg_get_constraintdef(k.oid,true),k.convalidated FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2,3",
        'indexes': "SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes WHERE schemaname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2,3",
        'functions': "SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner),ARRAY(SELECT acl::text FROM unnest(COALESCE(p.proacl,acldefault('f',p.proowner))) acl ORDER BY acl::text),p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2,3",
        'triggers': "SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2,3",
        'policies': "SELECT schemaname,tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2,3",
        'sequences': "SELECT schemaname,sequencename,sequenceowner,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname IN ('f0d','f0e','f0f','f0f_crypto','f0g','f0i','f1','f1_native_crypto') ORDER BY 1,2",
        'extensions': "SELECT e.extname,e.extversion,n.nspname,pg_get_userbyid(e.extowner) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1",
        'roles': "SELECT rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolconnlimit,rolvaliduntil,rolbypassrls,rolconfig FROM pg_roles WHERE rolname LIKE 'f0d_%' OR rolname LIKE 'f1_%' ORDER BY 1",
        'memberships': "SELECT r.rolname,m.rolname,g.rolname,a.admin_option,a.inherit_option,a.set_option FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.grantor WHERE r.rolname LIKE 'f0d_%' OR r.rolname LIKE 'f1_%' OR m.rolname LIKE 'f0d_%' OR m.rolname LIKE 'f1_%' ORDER BY 1,2,3",
    }
    catalog = {name: c.execute(query).fetchall() for name,query in catalog_queries.items()}
    return json.loads(canonical({'rows': rows, 'catalog': catalog}))


class ContainerPostgres:
    """Use the selected server's own pg_dump/pg_restore without password argv."""
    def __init__(self, container, database, docker='docker'):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', container):
            raise BackupError('BACKUP_CONTAINER_INVALID')
        if not re.fullmatch(r'[a-zA-Z0-9_]{1,63}', database):
            raise BackupError('BACKUP_DATABASE_NAME_INVALID')
        self.container,self.database,self.docker = container,database,docker

    def command(self, executable, *args):
        return [self.docker,'exec','-i',self.container,executable,'-U','f0d_bootstrap','-d',self.database,*args]

    def bind(self, expected):
        result = subprocess.run(self.command('psql','-XAt','-c',
            "SELECT current_database()||'|'||system_identifier::text||'|'||current_setting('server_version_num') FROM pg_control_system()"),
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        actual = '|'.join(str(expected[k]) for k in ('database','cluster','postgres_version'))
        if result.returncode or result.stdout.decode().strip() != actual:
            raise BackupError('BACKUP_CONTAINER_DATABASE_MISMATCH')

    def dump(self, snapshot_id, root):
        if not re.fullmatch(r'[0-9A-Fa-f-]+',snapshot_id):
            raise BackupError('BACKUP_SNAPSHOT_INVALID')
        fd = os.open(root/'database.dump',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as out:
            result = subprocess.run(self.command('pg_dump','--format=custom','--snapshot='+snapshot_id,
                                    '--lock-wait-timeout=5000','--no-password'),
                stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.PIPE,timeout=300)
            out.flush();os.fsync(out.fileno())
        if result.returncode:
            raise BackupError('BACKUP_PG_DUMP_FAILED')

    def restore(self, root, extensions, record):
        # pg_restore cannot ALTER EXTENSION OWNER. Using session authorization
        # globally would instead deny private NOLOGIN definers CREATE on f1.
        # Render the three archive sections, create only pgcrypto as its recorded
        # owner, then apply all SQL together as bootstrap in one transaction.
        raw=secure_read(root,'database.dump')
        if record!={'size':len(raw),'sha256':sha(raw)}:
            raise BackupError('BACKUP_MEMBER_HASH_MISMATCH')
        with tempfile.TemporaryFile() as archive,tempfile.TemporaryFile() as script:
            archive.write(raw);del raw
            for section in ('pre-data','data','post-data'):
                archive.seek(0)
                command=[self.docker,'exec','-i',self.container,'pg_restore','--file=-','--section='+section]
                with tempfile.TemporaryFile() as rendered:
                    process=subprocess.run(command,stdin=archive,stdout=rendered,stderr=subprocess.PIPE,timeout=300)
                    if process.returncode or rendered.tell()>MAX_PACKAGE_BYTES:
                        raise BackupError('RESTORE_ARCHIVE_RENDER_FAILED')
                    rendered.seek(0)
                    if section=='pre-data':
                        content=rendered.read()
                        for name,version,schema,owner in extensions:
                            if name=='plpgsql':
                                continue
                            if (name!='pgcrypto' or schema not in {'f0f_crypto','f1_native_crypto'}
                                    or owner not in {'f0d_migration','f0d_bootstrap'}):
                                raise BackupError('RESTORE_EXTENSION_CONTRACT_INVALID')
                            line=f'CREATE EXTENSION IF NOT EXISTS {name} WITH SCHEMA {schema};'.encode()
                            if content.splitlines().count(line)!=1:
                                raise BackupError('RESTORE_EXTENSION_STATEMENT_MISMATCH')
                            replacement=f'SET LOCAL ROLE {owner};\n'.encode()+line+b'\nRESET ROLE;'
                            content=content.replace(b'\n'+line+b'\n',b'\n'+replacement+b'\n')
                        script.write(content)
                    else:
                        while part:=rendered.read(1024*1024):
                            script.write(part)
                    script.write(b'\nRESET ROLE;\nRESET SESSION AUTHORIZATION;\n')
                    if script.tell()>MAX_PACKAGE_BYTES:
                        raise BackupError('RESTORE_ARCHIVE_RENDER_BUDGET_EXCEEDED')
            script.seek(0)
            result=subprocess.run(self.command('psql','-Xq','-v','ON_ERROR_STOP=1','--single-transaction','--file=-'),
                stdin=script,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=300)
        if result.returncode:
            error = BackupError('RESTORE_PG_RESTORE_FAILED')
            error.stderr = result.stderr
            raise error


def backup(connect, client, pg, storage_identity, output, key_paths):
    if not set(KEY_NAMES).issubset(key_paths) or not set(key_paths).issubset({*KEY_NAMES,MANIFEST_KEY,OPTIONAL_KEY}):
        raise BackupError('BACKUP_ALL_KEYS_REQUIRED')
    keys = {name: _read_secure(Path(path), unavailable_code='BACKUP_KEY_INVALID',minimum_size=16,maximum_size=4096)
            for name,path in key_paths.items()}
    validate_keys(keys)
    output = Path(output).absolute()
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    root = private_root(output)
    with connect() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        c.execute("SET LOCAL lock_timeout='5s'")
        c.execute("SET LOCAL statement_timeout='120s'")
        identity = database_identity(c)
        require_no_other_clients(c)
        pg.bind(identity)
        names = table_names(c)
        c.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(
            sql.SQL(',').join(sql.Identifier(*name) for name in names)))
        proof = snapshot(c)
        if proof['rows'].get('f0f.body_configuration',{}).get('count',0):
            if OPTIONAL_KEY not in keys:
                raise BackupError('BACKUP_HISTORICAL_F0F_KEY_REQUIRED')
        try:
            decryption_counts=verify_ciphertexts(c,keys)
        except Exception:
            raise BackupError('BACKUP_KEY_OR_CIPHERTEXT_INVALID') from None
        snapshot_id = c.execute('SELECT pg_export_snapshot()').fetchone()[0]
        pg.dump(snapshot_id,root)
        objects = export_objects(client,lambda name,raw:write_private(root,name,raw))
        if snapshot(c) != proof:
            raise BackupError('BACKUP_DATABASE_CHANGED')
        for name,raw in keys.items():
            write_private(root,'key-'+name,raw)
        write_private(root,'database-proof.json',canonical(proof))
        files = {}
        total = 0
        for path in sorted(root.iterdir()):
            raw = secure_read(root,path.name)
            total += len(raw)
            if total > MAX_PACKAGE_BYTES:
                raise BackupError('BACKUP_PACKAGE_BUDGET_EXCEEDED')
            files[path.name] = {'size':len(raw),'sha256':sha(raw)}
        manifest = {'schema':SCHEMA,'head':HEAD,'protected_rls_table_count':PROTECTED_RLS_TABLE_COUNT,'source':identity,
                    'storage_identity':storage_identity,'migration_sha256':migration_identity(),
                    'decryption_counts':decryption_counts,
                    'created_at_unix':int(time.time()),'files':files,'objects':objects,
                    'keys':[name for name in (*KEY_NAMES,MANIFEST_KEY,OPTIONAL_KEY) if name in keys]}
        raw = canonical(manifest)
        write_private(root,'manifest.json',raw)
        directory_sync(root)
    digest = sha(raw)
    verify_package(root,digest)
    return {'status':'BACKUP_VERIFIED','manifest_sha256':digest,'head':HEAD,
            'objects':len(objects),'protected_rls_tables':PROTECTED_RLS_TABLE_COUNT,'database_tables':len(proof['rows']),'source':identity}


def validate_keys(keys):
    try:
        decoded = {}
        for name in ('f1_material_rag_key','f1_qa_key',*([MANIFEST_KEY] if MANIFEST_KEY in keys else [])):
            raw = keys[name].decode('ascii').strip()
            decoded[name] = bytes.fromhex(raw) if len(raw)==64 else raw.encode('ascii')
        if (len(decoded['f1_material_rag_key']) not in (16,24,32)
                or len(decoded['f1_qa_key']) not in (16,24,32)
                or MANIFEST_KEY in decoded and (len(decoded[MANIFEST_KEY])!=32 or decoded[MANIFEST_KEY]==decoded['f1_material_rag_key'])
                or len(keys['f0i_key'])!=32 or len(keys['invite_signing_key'].strip())<32
                or OPTIONAL_KEY in keys and len(keys[OPTIONAL_KEY])!=32):
            raise ValueError()
    except (KeyError,ValueError,UnicodeError):
        raise BackupError('BACKUP_KEY_FORMAT_INVALID') from None


def valid_key_names(names):
    return (isinstance(names,list) and set(KEY_NAMES).issubset(names)
            and names==[name for name in (*KEY_NAMES,MANIFEST_KEY,OPTIONAL_KEY) if name in names])


def verify_package(root, trusted_manifest_sha256):
    root = private_root(root)
    raw = secure_read(root,'manifest.json',16*1024*1024)
    if not re.fullmatch(r'[0-9a-f]{64}',trusted_manifest_sha256) or sha(raw) != trusted_manifest_sha256:
        raise BackupError('BACKUP_MANIFEST_TRUST_MISMATCH')
    manifest = json.loads(raw)
    if (canonical(manifest) != raw or set(manifest) != {'schema','head','protected_rls_table_count','source','decryption_counts',
            'storage_identity','migration_sha256','created_at_unix','files','objects','keys'}
            or manifest['schema'] != SCHEMA or manifest['head'] != HEAD
            or manifest['protected_rls_table_count'] != PROTECTED_RLS_TABLE_COUNT
            or not valid_key_names(manifest['keys'])
            or manifest['migration_sha256'] != migration_identity()):
        raise BackupError('BACKUP_MANIFEST_CONTRACT_MISMATCH')
    files = manifest['files']
    if not isinstance(files,dict) or set(p.name for p in root.iterdir()) != set(files)|{'manifest.json'}:
        raise BackupError('BACKUP_PACKAGE_MEMBERS_MISMATCH')
    required = {'database.dump','database-proof.json'} | {'key-'+name for name in manifest['keys']}
    total = 0
    for name,record in files.items():
        if (name not in required and not re.fullmatch(r'object-[0-9]{5}-part-[0-9]{4}\.bin',name)):
            raise BackupError('BACKUP_MEMBER_NAME_INVALID')
        if set(record) != {'size','sha256'} or type(record['size']) is not int or record['size'] < 0:
            raise BackupError('BACKUP_MEMBER_RECORD_INVALID')
        raw = secure_read(root,name)
        total += len(raw)
        if total > MAX_PACKAGE_BYTES or record != {'size':len(raw),'sha256':sha(raw)}:
            raise BackupError('BACKUP_MEMBER_HASH_MISMATCH')
    if not required.issubset(files) or not secure_read(root,'database.dump').startswith(b'PGDMP'):
        raise BackupError('BACKUP_DATABASE_ARCHIVE_INVALID')
    validate_keys({name:secure_read(root,'key-'+name,4096) for name in manifest['keys']})
    proof=json.loads(secure_read(root,'database-proof.json'))
    if proof['rows'].get('f0f.body_configuration',{}).get('count',0) and OPTIONAL_KEY not in manifest['keys']:
        raise BackupError('BACKUP_HISTORICAL_F0F_KEY_REQUIRED')
    seen,referenced = set(),set(required)
    objects = manifest['objects']
    if not isinstance(objects,list) or len(objects) > MAX_OBJECTS or sum(i['size'] for i in objects)>MAX_TOTAL_BYTES:
        raise BackupError('BACKUP_OBJECT_BUDGET_EXCEEDED')
    for item in objects:
        validate_object(item)
        identity = (item['bucket'],item['key'])
        if identity in seen:
            raise BackupError('BACKUP_DUPLICATE_OBJECT')
        seen.add(identity)
        full = hashlib.sha256()
        for part in item['parts']:
            if part['file'] in referenced or part['file'] not in files:
                raise BackupError('BACKUP_DUPLICATE_OR_MISSING_PART')
            referenced.add(part['file'])
            raw = secure_read(root,part['file'])
            if len(raw)!=part['size'] or sha(raw)!=part['sha256'] or md5(raw)!=part['md5']:
                raise BackupError('BACKUP_OBJECT_BYTES_MISMATCH')
            full.update(raw)
        if full.hexdigest()!=item['sha256']:
            raise BackupError('BACKUP_OBJECT_BYTES_MISMATCH')
    if referenced != set(files):
        raise BackupError('BACKUP_UNREFERENCED_MEMBER')
    return manifest


def empty_target(connect, client, pg, source, storage_identity):
    with connect() as c:
        identity = database_identity(c,require_head=False)
        require_no_other_clients(c)
        if identity['cluster']==source['cluster'] or storage_identity==source.get('storage_identity'):
            raise BackupError('RESTORE_INDEPENDENT_TARGET_REQUIRED')
        if identity['postgres_version']!=source['postgres_version']:
            raise BackupError('RESTORE_POSTGRES_VERSION_MISMATCH')
        relations = c.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                             "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()[0]
        functions = c.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                             "WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'").fetchone()[0]
        schemas = {row[0] for row in c.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema'")}
        if relations or functions or not schemas.issubset({'public','f0d'}):
            raise BackupError('RESTORE_EMPTY_DATABASE_REQUIRED')
        pg.bind(identity)
    if {b.name for b in client.list_buckets()} != set(BUCKETS) or inventory(client):
        raise BackupError('RESTORE_EMPTY_BUCKETS_REQUIRED')
    identity['storage_identity'] = storage_identity
    return identity


def runtime_check(connect, storage_identity, package, manifest_sha256, restore_result):
    """Read-only fence before reconnecting runtime; never falsifies job transitions."""
    root=private_root(package)
    manifest=verify_package(root,manifest_sha256)
    if (restore_result.get('status')!='RESTORE_VERIFIED'
            or restore_result.get('manifest_sha256')!=manifest_sha256
            or restore_result.get('target',{}).get('storage_identity')!=storage_identity):
        raise BackupError('RESTORE_RESULT_IDENTITY_INVALID')
    expected=restore_result['target']
    with connect() as c:
        identity=database_identity(c,require_head=False)
        if identity!={k:expected[k] for k in ('database','cluster','postgres_version')}:
            raise BackupError('RESTORE_RUNTIME_TARGET_CHANGED')
        require_no_other_clients(c)
        if snapshot(c)!=json.loads(secure_read(root,'database-proof.json')):
            raise BackupError('RESTORE_DATABASE_CHANGED_BEFORE_START')
        columns=c.execute("SELECT n.nspname,t.relname,a.attname FROM pg_attribute a "
            "JOIN pg_class t ON t.oid=a.attrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
            "WHERE t.relkind='r' AND n.nspname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped "
            "AND a.attname ~ '(^|_)lease_until$' ORDER BY 1,2,3",(list(SCHEMAS),)).fetchall()
        leases=[]
        for schema,table,column in columns:
            # This terminal report retains its completed-token receipt. Its
            # historical lease is not a resumable capability after status=draft.
            suffix=sql.SQL(" AND status='generating'") if (schema,table)==('f1','analysis_report_generation_job') else sql.SQL('')
            count,until=c.execute(sql.SQL('SELECT count(*),max({}) FROM {} WHERE {}>clock_timestamp(){}')
                .format(sql.Identifier(column),sql.Identifier(schema,table),sql.Identifier(column),suffix)).fetchone()
            if count:
                leases.append({'table':schema+'.'+table,'column':column,'count':count,'until':until.isoformat()})
        return {'status':'WAIT_LEASE_EXPIRY' if leases else 'RUNTIME_RESTART_READY',
                'manifest_sha256':manifest_sha256,'target':expected,'active_leases':leases,
                'runtime_started':False,'retrieval_mode':'LOCAL_INDEX_ONLY'}


def restore(connect, client, pg, storage_identity, package, manifest_sha256, output,
            provision_roles, expected_target):
    root = private_root(package)
    manifest = verify_package(root,manifest_sha256)
    source = {**manifest['source'],'storage_identity':manifest['storage_identity']}
    target = empty_target(connect,client,pg,source,storage_identity)
    if target != expected_target:
        raise BackupError('RESTORE_REVIEWED_TARGET_CHANGED')
    output = Path(output).absolute()
    output.mkdir(mode=0o700,parents=False,exist_ok=False)
    out = private_root(output)
    write_private(out,'target.json',canonical({'source':source,'target':target,'manifest_sha256':manifest_sha256}))
    journal = os.open(out/'journal.jsonl',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(journal,'wb') as stream:
        def event(value):
            stream.write(canonical(value)+b'\n');stream.flush();os.fsync(stream.fileno())
        event({'stage':'preflight_passed','target':target})
        try:
            # Nothing downstream overwrites source secrets or existing runtime
            # passwords. This new directory is the material to mount at cutover.
            keys = out/'restored-keys';keys.mkdir(mode=0o700)
            for name in manifest['keys']:
                write_private(keys,name,secure_read(root,'key-'+name,4096))
            directory_sync(keys);directory_sync(out)
            proof = json.loads(secure_read(root,'database-proof.json'))
            event({'stage':'database_preparation_started'})
            with connect() as c:
                provision_roles(c,target['database'])
                # This is only the baseline empty schema created by 00_roles.sql;
                # DROP has no CASCADE and preflight required no relations/functions.
                c.execute('DROP SCHEMA IF EXISTS f0d')
                for row in proof['catalog']['roles']:
                    for setting in row[-1] or []:
                        name,value=setting.split('=',1)
                        # Quoting a whole search_path value in ALTER ROLE would
                        # turn its comma-separated names into one schema name.
                        if name=='search_path':
                            c.execute('SELECT set_config(%s,%s,true)',(name,value))
                            c.execute(sql.SQL('ALTER ROLE {} SET {} FROM CURRENT').format(
                                sql.Identifier(row[0]),sql.Identifier(name)))
                        else:
                            c.execute(sql.SQL('ALTER ROLE {} SET {} TO {}').format(
                                sql.Identifier(row[0]),sql.Identifier(name),sql.Literal(value)))
            event({'stage':'database_restore_started'})
            # Recheck all bytes immediately before the first archive write.
            verify_package(root,manifest_sha256)
            pg.restore(root,proof['catalog']['extensions'],manifest['files']['database.dump'])
            with connect() as c:
                observed = snapshot(c)
                if database_identity(c)['head']!=HEAD or observed!=proof:
                    write_private(out,'database-proof-observed.json',canonical(observed))
                    event({'stage':'database_proof_mismatch',
                           'row_tables':[k for k in proof['rows'] if observed['rows'].get(k)!=proof['rows'][k]],
                           'catalog_sections':[k for k in proof['catalog'] if observed['catalog'].get(k)!=proof['catalog'][k]]})
                    raise BackupError('RESTORE_DATABASE_PROOF_MISMATCH')
                try:
                    restored_counts=verify_ciphertexts(c,{name:secure_read(root,'key-'+name,4096) for name in manifest['keys']})
                except Exception:
                    raise BackupError('RESTORE_KEY_OR_CIPHERTEXT_INVALID') from None
                if restored_counts!=manifest['decryption_counts']:
                    raise BackupError('RESTORE_DECRYPTION_COUNT_MISMATCH')
            event({'stage':'database_verified'})
            restore_objects(client,manifest['objects'],lambda name:secure_read(root,name),event)
            if len(inventory(client))!=len(manifest['objects']):
                raise BackupError('RESTORE_FINAL_OBJECT_COUNT_MISMATCH')
            event({'stage':'restore_verified'})
            result = {'status':'RESTORE_VERIFIED','head':HEAD,'source':source,'target':target,
                      'manifest_sha256':manifest_sha256,'protected_rls_tables':PROTECTED_RLS_TABLE_COUNT,
                      'database_tables':len(proof['rows']),
                      'objects':len(manifest['objects']),'old_leases':'PRESERVED_OFFLINE',
                      'runtime_start':'NOT_STARTED','keys_directory':str(keys)}
            write_private(out,'result.json',canonical(result));directory_sync(out)
            return result
        except BaseException:
            event({'stage':'restore_failed','runtime_start':'BLOCKED','retry':'NEW_EMPTY_TARGET_REQUIRED'})
            raise
