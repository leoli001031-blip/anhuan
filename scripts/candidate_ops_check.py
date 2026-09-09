#!/usr/bin/env python3
"""One-shot, read-only local candidate operations checks. No schedules or notifications."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
REQUIRED_SERVICES = frozenset({'postgres', 'minio', 'redis', 'clamd', 'keycloak', 'api', 'web',
    'worker', 'ingestion-worker', 'report-worker', 'dispatcher', 'source-gateway'})
READINESS_COMPONENTS = frozenset({'database', 'minio', 'redis', 'clamd', 'oidc'})
# This is an operator-selected test threshold profile, not a production SLA.
THRESHOLD_LIMITS = {'window_seconds': (60, 604800), 'pending_age_seconds': (1, 86400),
    'retry_due_age_seconds': (0, 86400), 'expired_lease_grace_seconds': (0, 86400),
    'high_attempt_threshold': (1, 100), 'max_recent_terminal_failures': (0, 1000000),
    'max_retry_wait': (0, 1000000), 'max_high_attempt_active': (0, 1000000),
    'max_overdue_pending': (0, 1000000), 'max_overdue_retries': (0, 1000000),
    'max_expired_leases': (0, 1000000), 'max_service_restarts': (0, 1000000),
    'object_plan_max_age_seconds': (1, 604800), 'max_object_issues': (0, 50000)}
DEFAULT_THRESHOLDS = dict(window_seconds=3600, pending_age_seconds=300,
    retry_due_age_seconds=120, expired_lease_grace_seconds=120, high_attempt_threshold=3,
    max_recent_terminal_failures=0, max_retry_wait=20, max_high_attempt_active=0,
    max_overdue_pending=0, max_overdue_retries=0, max_expired_leases=0,
    max_service_restarts=3, object_plan_max_age_seconds=3600, max_object_issues=0)

# Closed vocabulary from current migration and worker contracts, no input-supplied SQL.
# table, state column, all states, pending, lease-active, retry, failed-terminal,
# lease column, attempt expression, next-attempt expression, row predicate.
DELIVERY_STATES = ('pending', 'dispatched', 'retry_wait', 'done', 'blocked')
QUEUES = [
    (name, 'state', DELIVERY_STATES, ('pending',), ('dispatched',), ('retry_wait',), ('blocked',),
     'dispatch_lease_until', 'attempt', 'next_attempt_at', 'TRUE')
    for name in ('material_ingestion_delivery', 'material_pipeline_delivery', 'analysis_report_generation_delivery')
] + [
    ('material_evidence_job', 'state', ('pending', 'running', 'retry_wait', 'done', 'blocked'),
     ('pending',), ('running',), ('retry_wait',), ('blocked',), 'lease_until', 'attempt', 'next_attempt_at', 'TRUE'),
    ('material_rag_job', 'status', ('queued', 'running', 'retry_wait', 'done', 'failed'),
     ('queued',), ('running',), ('retry_wait',), ('failed',), 'lease_until', 'attempt', 'next_attempt_at', 'TRUE'),
    ('analysis_report_generation_job', 'status', ('queued', 'generating', 'draft', 'failed'),
     ('queued',), ('generating',), (), ('failed',), 'lease_until', '0', 'NULL::timestamptz', 'TRUE'),
    ('upload_task', 'processing_stage', ('received', 'scanning', 'validating', 'previewing', 'ready', 'retry_wait', 'rejected', 'failed'),
     ('received',), ('scanning', 'validating', 'previewing'), ('retry_wait',), ('failed',),
     'lease_until', 'attempt', 'next_attempt_at', "pipeline_kind='controlled_ingestion'"),
]


class OpsError(ValueError):
    pass


class MissingInput(OpsError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OpsError('CONFIG_DUPLICATE_KEY')
        result[key] = value
    return result


def private_read(path, maximum):
    from platform_foundation.f1.secret_files import _read_secure
    path = Path(path).absolute()
    return _read_secure(path, unavailable_code='OPS_PRIVATE_INPUT_INVALID', minimum_size=1, maximum_size=maximum)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def validate_config(value):
    from platform_foundation.f1.maintenance.candidate_backup import HEAD, migration_identity
    keys = {'schema_version', 'scope', 'candidate_id', 'compose_project', 'docker_host', 'database',
            'services', 'thresholds', 'migration_source_sha256', 'object_plan'}
    if not isinstance(value, dict) or set(value) - keys:
        raise OpsError('CONFIG_INVALID')
    if (keys - {'object_plan'}) - set(value):
        raise MissingInput('CANDIDATE_CONFIG_INCOMPLETE')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['scope'] != 'local_candidate':
        raise OpsError('LOCAL_CANDIDATE_SCOPE_REQUIRED')
    if not isinstance(value['candidate_id'], str) or not re.fullmatch('[A-Za-z0-9_-]{1,80}', value['candidate_id']):
        raise OpsError('CANDIDATE_ID_INVALID')
    # A random dedicated namespace; the canonical shared project can never match.
    if not isinstance(value['compose_project'], str) or not re.fullmatch(r'anhuan-(?:ar-(?:uat|pgint)|ops-candidate)-[0-9a-f]{12}', value['compose_project']):
        raise OpsError('DEDICATED_PROJECT_REQUIRED')
    host = value['docker_host']
    if not isinstance(host, str) or not host.startswith('unix:///') or any(c in host for c in '\r\n\0'):
        raise OpsError('LOCAL_DOCKER_SOCKET_REQUIRED')
    if not _hash(value['migration_source_sha256']) or value['migration_source_sha256'] != migration_identity():
        raise OpsError('MIGRATION_SOURCE_IDENTITY_MISMATCH')
    database = value['database']
    if not isinstance(database, dict) or set(database) != {'dsn_file', 'name', 'cluster', 'head'}:
        raise MissingInput('DATABASE_IDENTITY_REQUIRED')
    if (not isinstance(database['dsn_file'], str) or not Path(database['dsn_file']).is_absolute()
        or not isinstance(database['name'], str) or not re.fullmatch(r'f1_arpg_[0-9a-f]{12}', database['name'])
        or not isinstance(database['cluster'], str) or not re.fullmatch('[0-9]{1,20}', database['cluster'])
        or database['head'] != HEAD):
        raise OpsError('DATABASE_IDENTITY_INVALID')
    services = value['services']
    if not isinstance(services, dict) or not REQUIRED_SERVICES.issubset(services) or len(services) > 24:
        raise MissingInput('CRITICAL_SERVICE_INVENTORY_REQUIRED')
    ids = set()
    for name, expected in services.items():
        if (not isinstance(name, str) or not re.fullmatch('[a-z][a-z0-9-]{0,39}', name)
            or not isinstance(expected, dict) or set(expected) != {'container_id', 'image_id'}
            or not _hash(expected['container_id']) or expected['container_id'] in ids
            or not isinstance(expected['image_id'], str) or not re.fullmatch('sha256:[0-9a-f]{64}', expected['image_id'])):
            raise OpsError('SERVICE_IDENTITY_INVALID')
        ids.add(expected['container_id'])
    thresholds = value['thresholds']
    if not isinstance(thresholds, dict) or set(thresholds) != set(THRESHOLD_LIMITS):
        raise MissingInput('EXPLICIT_THRESHOLDS_REQUIRED')
    for name, (minimum, maximum) in THRESHOLD_LIMITS.items():
        if type(thresholds[name]) is not int or not minimum <= thresholds[name] <= maximum:
            raise OpsError('THRESHOLD_INVALID')
    plan = value.get('object_plan')
    if plan is not None and (not isinstance(plan, dict) or set(plan) != {'path', 'sha256', 'storage_identity'}
        or not isinstance(plan['path'], str) or not Path(plan['path']).is_absolute()
        or not _hash(plan['sha256']) or not _hash(plan['storage_identity'])):
        raise OpsError('OBJECT_PLAN_REFERENCE_INVALID')
    return value


# Docker output deliberately excludes environment, health log bodies, mounts and secrets.
INSPECT_TEMPLATE = '''{"id":{{json .Id}},"image_id":{{json .Image}},"running":{{json .State.Running}},"state":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"missing"{{end}},"restart_count":{{json .RestartCount}},"project":{{json (index .Config.Labels "com.docker.compose.project")}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"ports":{{json .NetworkSettings.Ports}}}'''


def docker_inspect(config, *, timeout=5):
    command = [shutil.which('docker') or 'docker', '--host', config['docker_host'], 'inspect',
               '--type', 'container', '--format', INSPECT_TEMPLATE,
               *[item['container_id'] for item in config['services'].values()]]
    # Only explicit host and process-local temporary output; no shell/compose commands.
    with tempfile.TemporaryFile() as output:
        cp = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            timeout=timeout, check=False)
        if cp.returncode:
            raise OpsError('SERVICE_INSPECTION_FAILED')
        if output.tell() > 262144:
            raise OpsError('SERVICE_INSPECTION_BUDGET_EXCEEDED')
        output.seek(0)
        return [json.loads(line) for line in output.read().splitlines()]


def observe_services(config, observations):
    if not isinstance(observations, list) or len(observations) != len(config['services']):
        raise OpsError('SERVICE_OBSERVATION_INCOMPLETE')
    by_id = {item['id']: item for item in observations}
    if len(by_id) != len(observations):
        raise OpsError('SERVICE_OBSERVATION_DUPLICATE')
    services, ports = [], {}
    for name, expected in sorted(config['services'].items()):
        actual = by_id.get(expected['container_id'])
        if (not actual or actual.get('project') != config['compose_project'] or actual.get('service') != name
            or actual.get('image_id') != expected['image_id']):
            raise OpsError('SERVICE_TARGET_IDENTITY_MISMATCH')
        if (type(actual.get('restart_count')) is not int or actual['restart_count'] < 0
            or type(actual.get('running')) is not bool or actual.get('health') not in {'healthy', 'unhealthy', 'starting', 'missing'}
            or actual.get('state') not in {'created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead'}):
            raise OpsError('SERVICE_STATE_INVALID')
        alerts = []
        if not actual['running'] or actual['state'] != 'running' or actual['health'] != 'healthy':
            alerts.append('SERVICE_NOT_READY')
        if actual['restart_count'] > config['thresholds']['max_service_restarts']:
            alerts.append('SERVICE_RESTART_THRESHOLD')
        services.append({key: actual[key] for key in ('id', 'service', 'image_id', 'running', 'state', 'health', 'restart_count')} | {'alerts': alerts})
        if name in {'postgres', 'web'}:
            bindings = (actual.get('ports') or {}).get('5432/tcp' if name == 'postgres' else '80/tcp')
            if (not isinstance(bindings, list) or len(bindings) != 1 or bindings[0].get('HostIp') != '127.0.0.1'
                or not re.fullmatch('[0-9]{1,5}', str(bindings[0].get('HostPort', '')))
                or not 1024 <= int(bindings[0]['HostPort']) <= 65535):
                raise OpsError('CANDIDATE_LOOPBACK_PORT_REQUIRED')
            ports[name] = int(bindings[0]['HostPort'])
    return {'status': 'ALERT' if any(item['alerts'] for item in services) else 'PASSED',
            'scope': 'CONFIGURED_CONTAINERS_ONLY', 'services': services}, ports


def readonly_dsn(config, postgres_port):
    from psycopg.conninfo import conninfo_to_dict
    parsed = conninfo_to_dict(private_read(config['database']['dsn_file'], 16384).decode().strip())
    if (set(parsed) - {'host', 'port', 'dbname', 'user', 'password', 'sslmode'}
        or parsed.get('host') != '127.0.0.1' or parsed.get('port') != str(postgres_port)
        or parsed.get('dbname') != config['database']['name'] or parsed.get('user') != 'f0d_bootstrap'
        or not parsed.get('password') or parsed.get('sslmode', 'disable') not in {'disable', 'require', 'verify-full'}):
        raise OpsError('DATABASE_ENDPOINT_BINDING_MISMATCH')
    # Bind the socket address too: inherited PGHOSTADDR must not redirect a
    # libpq connection while its textual host still says 127.0.0.1.
    return parsed | {'hostaddr': '127.0.0.1', 'sslmode': parsed.get('sslmode', 'disable')}


@contextmanager
def readonly_connection(connect):
    with connect() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL statement_timeout='3000ms'")
        connection.execute("SET LOCAL lock_timeout='1000ms'")
        connection.execute("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        # Do not let a narrowed bootstrap silently observe a FORCE-RLS empty set.
        connection.execute('SET LOCAL row_security=off')
        if connection.execute('SHOW transaction_read_only').fetchone() != ('on',):
            raise OpsError('READ_ONLY_TRANSACTION_REQUIRED')
        yield connection
        # No commit receipt: this operator ends even its read-only snapshot by rollback.
        connection.rollback()


def _queue_snapshot(c, spec, thresholds):
    from psycopg import sql
    name, state_col, states, pending, running, retry, terminal, lease, attempt, due, predicate = spec
    active = pending + running + retry
    # All interpolated identifiers/expressions come from QUEUES, never from config.
    statement = sql.SQL('''SELECT count(*),
      count(*) FILTER(WHERE {state} = ANY(%s)),
      count(*) FILTER(WHERE {state} = ANY(%s) AND updated_at >= transaction_timestamp()-make_interval(secs=>%s)),
      count(*) FILTER(WHERE {state} = ANY(%s)),
      count(*) FILTER(WHERE {state} = ANY(%s) AND {due} <= transaction_timestamp()-make_interval(secs=>%s)),
      count(*) FILTER(WHERE {state} = ANY(%s) AND updated_at <= transaction_timestamp()-make_interval(secs=>%s)),
      count(*) FILTER(WHERE {state} = ANY(%s) AND {lease} <= transaction_timestamp()),
      count(*) FILTER(WHERE {state} = ANY(%s) AND {lease} <= transaction_timestamp()-make_interval(secs=>%s)),
      count(*) FILTER(WHERE {state} = ANY(%s) AND {attempt} >= %s),
      count(*) FILTER(WHERE NOT ({state} = ANY(%s))),
      COALESCE(max(EXTRACT(EPOCH FROM transaction_timestamp()-updated_at)) FILTER(WHERE {state} = ANY(%s)),0)
      FROM f1.{table} WHERE {predicate}''').format(state=sql.Identifier(state_col),
        due=sql.SQL(due), lease=sql.Identifier(lease), attempt=sql.SQL(attempt),
        table=sql.Identifier(name), predicate=sql.SQL(predicate))
    row = c.execute(statement, (list(terminal), list(terminal), thresholds['window_seconds'], list(retry),
        list(retry), thresholds['retry_due_age_seconds'], list(pending), thresholds['pending_age_seconds'],
        list(running), list(running), thresholds['expired_lease_grace_seconds'], list(active),
        thresholds['high_attempt_threshold'], list(states), list(pending))).fetchone()
    keys = ('total', 'terminal_failures_total', 'recent_terminal_failures', 'retry_wait', 'overdue_retries',
            'overdue_pending', 'expired_leases', 'expired_leases_past_grace', 'high_attempt_active', 'unknown_states')
    metrics = dict(zip(keys, (int(value) for value in row[:10]), strict=True))
    metrics['oldest_pending_age_seconds'] = max(0, round(float(row[10]), 3))
    state_counts = dict(c.execute(sql.SQL('SELECT {state},count(*) FROM f1.{table} WHERE {predicate} GROUP BY {state}').format(
        state=sql.Identifier(state_col), table=sql.Identifier(name), predicate=sql.SQL(predicate))).fetchall())
    if set(state_counts) - set(states) or metrics['unknown_states']:
        raise OpsError('QUEUE_STATE_CONTRACT_MISMATCH')
    metrics['states'] = {state: int(state_counts.get(state, 0)) for state in states}
    checks = [('recent_terminal_failures', 'max_recent_terminal_failures'), ('retry_wait', 'max_retry_wait'),
        ('overdue_retries', 'max_overdue_retries'), ('overdue_pending', 'max_overdue_pending'),
        ('expired_leases_past_grace', 'max_expired_leases'), ('high_attempt_active', 'max_high_attempt_active')]
    alerts = [{'metric': metric, 'observed': metrics[metric], 'threshold': thresholds[limit]}
              for metric, limit in checks if metrics[metric] > thresholds[limit]]
    return {'table': 'f1.' + name, 'status': 'ALERT' if alerts else 'PASSED', 'metrics': metrics, 'alerts': alerts}


def database_snapshot(connect, expected, thresholds):
    from platform_foundation.f1.maintenance import candidate_backup as authority
    with readonly_connection(connect) as c:
        role = c.execute("SELECT current_user,rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
        if role != ('f0d_bootstrap', True):
            raise OpsError('MAINTENANCE_VISIBILITY_REQUIRED')
        identity = authority.database_identity(c, require_head=False)
        if any(identity[key] != expected[key] for key in ('database', 'cluster')):
            raise OpsError('DATABASE_TARGET_IDENTITY_MISMATCH')
        heads = c.execute('SELECT version_num FROM f1.alembic_version').fetchall()
        foundation = c.execute('SELECT version_num FROM f0d.alembic_version').fetchall()
        if heads != [(authority.HEAD,)] or foundation != [('f0d_0006',)]:
            raise OpsError('CURRENT_MIGRATION_HEAD_REQUIRED')
        identity.update(head=authority.HEAD, foundation_head='f0d_0006')
        protected = set(runpy.run_path(str(ROOT / 'infra/f1/analysis-reports/migrate.py'))['EXPECTED_RLS_TABLES'])
        protected.update({'material_review_revision', 'material_review_fragment'})
        observed = {row[0] for row in c.execute("SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='f1' AND c.relkind='r' AND c.relrowsecurity AND c.relforcerowsecurity")}
        if len(protected) != authority.PROTECTED_RLS_TABLE_COUNT or not protected.issubset(observed):
            raise OpsError('PROTECTED_RLS_CONTRACT_MISMATCH')
        snapshot_at = c.execute('SELECT transaction_timestamp()').fetchone()[0].isoformat()
        queues = [_queue_snapshot(c, spec, thresholds) for spec in QUEUES]
        return {'status': 'ALERT' if any(q['alerts'] for q in queues) else 'PASSED',
            'identity': identity, 'snapshot_at': snapshot_at, 'transaction_read_only': True,
            'protected_rls_tables': len(protected), 'queues': queues,
            'scope': 'DATABASE_AGGREGATES_NO_ROW_IDS_OR_CONTENT', 'recent_window_seconds': thresholds['window_seconds']}


def readiness_get(web_port):
    connection = http.client.HTTPConnection('127.0.0.1', web_port, timeout=5)
    try:
        connection.request('GET', '/api/readyz', headers={'Accept': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(8193)
        if len(body) > 8192:
            raise OpsError('READINESS_RESPONSE_BUDGET_EXCEEDED')
        return response.status, json.loads(body)
    finally:
        connection.close()


def observe_readiness(status, body):
    if (status not in (200, 503) or not isinstance(body, dict) or set(body) != {'status', 'components'}
        or not isinstance(body['components'], dict) or set(body['components']) != READINESS_COMPONENTS
        or any(type(value) is not bool for value in body['components'].values())
        or body['status'] != ('ready' if all(body['components'].values()) else 'unavailable')
        or status != (200 if all(body['components'].values()) else 503)):
        raise OpsError('READINESS_CONTRACT_INVALID')
    return {'status': 'PASSED' if status == 200 else 'ALERT', 'components': body['components'],
            'http_status': status, 'scope': 'CURRENT_API_READINESS_CONTRACT'}


def observe_object_plan(config, *, now=None):
    from platform_foundation.f1.maintenance.object_reconcile import digest
    reference = config.get('object_plan')
    if reference is None:
        return {'status': 'NOT_TESTED', 'reason': 'OBJECT_RECONCILE_PLAN_REQUIRED', 'scope': 'REFERENCED_READ_ONLY_PLAN'}
    raw = private_read(reference['path'], 16 * 1024 * 1024)
    if sha(raw) != reference['sha256']:
        raise OpsError('OBJECT_PLAN_FILE_FINGERPRINT_MISMATCH')
    plan = json.loads(raw, object_pairs_hook=strict_object)
    if (not isinstance(plan, dict) or type(plan.get('schema_version')) is not int
        or plan['schema_version'] != 1 or plan.get('status') not in {'HEALTHY', 'ISSUES_FOUND'}
        or plan.get('plan_sha256') != digest({k: v for k, v in plan.items() if k != 'plan_sha256'})):
        raise OpsError('OBJECT_PLAN_CONTRACT_INVALID')
    target = {'database': config['database']['name'], 'cluster': config['database']['cluster'],
              'head': config['database']['head'], 'storage': reference['storage_identity']}
    if plan.get('target') != target:
        raise OpsError('OBJECT_PLAN_TARGET_MISMATCH')
    created = datetime.fromisoformat(plan['created_at'])
    if created.tzinfo is None:
        raise OpsError('OBJECT_PLAN_TIME_INVALID')
    age = ((now or datetime.now(timezone.utc)) - created).total_seconds()
    if not 0 <= age <= config['thresholds']['object_plan_max_age_seconds']:
        raise OpsError('OBJECT_PLAN_STALE_OR_FUTURE')
    issues = plan.get('issues')
    if not isinstance(issues, list) or len(issues) > 50000 or (plan['status'] == 'HEALTHY') != (not issues):
        raise OpsError('OBJECT_PLAN_ISSUES_INVALID')
    counts = Counter()
    for issue in issues:
        if not isinstance(issue, dict) or not re.fullmatch('[A-Z0-9_]{1,80}', str(issue.get('code', ''))):
            raise OpsError('OBJECT_PLAN_REASON_INVALID')
        counts[issue['code']] += 1
    for key in ('object_count', 'task_count'):
        if type(plan.get(key)) is not int or not 0 <= plan[key] <= 1000000:
            raise OpsError('OBJECT_PLAN_COUNT_INVALID')
    return {'status': 'ALERT' if len(issues) > config['thresholds']['max_object_issues'] else 'PASSED',
        'scope': 'REFERENCED_READ_ONLY_PLAN_NOT_RESCANNED', 'file_sha256': reference['sha256'],
        'plan_sha256': plan['plan_sha256'], 'created_at': created.isoformat(), 'age_seconds': round(age, 3),
        'object_count': plan['object_count'], 'task_count': plan['task_count'],
        'issue_count': len(issues), 'issue_codes': dict(sorted(counts.items())),
        'threshold': config['thresholds']['max_object_issues']}


def failure(exc):
    return {'status': 'NOT_TESTED' if isinstance(exc, (MissingInput, ModuleNotFoundError, FileNotFoundError)) else 'FAILED',
            'reason': str(exc) if isinstance(exc, OpsError) else 'OBSERVATION_FAILED', 'error_type': type(exc).__name__}


def run_ops(config_path=None, *, inspect=docker_inspect, get_readiness=readiness_get, connect_factory=None):
    started = time.monotonic()
    result = {'schema_version': 1, 'scope': 'LOCAL_CANDIDATE_READ_ONLY', 'status': 'NOT_TESTED',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': {}, 'alerts_delivered': 'NOT_TESTED',
        'production_deployment': 'NOT_TESTED', 'formal_sla': 'NOT_CONFIGURED',
        'limitations': ['One observation, not continuous uptime or a queue recovery action.',
            'Object evidence is a dated read-only plan reference, not a fresh object scan.',
            'Existing maintenance credentials are used in a read-only transaction; no new runtime grants.']}
    if config_path is None:
        return {**result, 'reason': 'EXPLICIT_CANDIDATE_CONFIG_REQUIRED'}
    try:
        raw = private_read(config_path, 131072)
        config = validate_config(json.loads(raw, object_pairs_hook=strict_object))
        result.update(config_sha256=sha(raw), candidate_id=config['candidate_id'], compose_project=config['compose_project'],
                      thresholds=config['thresholds'], migration_source_sha256=config['migration_source_sha256'])
        observed = inspect(config)
        result['checks']['services'], ports = observe_services(config, observed)
        dsn = readonly_dsn(config, ports['postgres'])
    except Exception as exc:
        result.update(failure(exc));return result
    try:
        import psycopg
        connect = connect_factory or (lambda: psycopg.connect(**dsn, connect_timeout=5,
            options='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000'))
        expected = {'database': config['database']['name'], 'cluster': config['database']['cluster']}
        result['checks']['database'] = database_snapshot(connect, expected, config['thresholds'])
    except Exception as exc:
        result['checks']['database'] = failure(exc)
    # No source/model calls: this reuses the API's five body-free dependency probes.
    try:
        result['checks']['readiness'] = observe_readiness(*get_readiness(ports['web']))
    except Exception as exc:
        result['checks']['readiness'] = failure(exc)
    try:
        result['checks']['objects'] = observe_object_plan(config)
    except Exception as exc:
        result['checks']['objects'] = failure(exc)
    statuses = [check['status'] for check in result['checks'].values()]
    result['status'] = next((state for state in ('FAILED', 'ALERT', 'NOT_TESTED') if state in statuses), 'PASSED')
    result['seconds'] = round(time.monotonic() - started, 3)
    return result


def write_result(path, result):
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(canonical(result) + b'\n');output.flush();os.fsync(output.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, help='Private JSON for one explicitly selected dedicated local candidate.')
    parser.add_argument('--output', type=Path, required=True, help='New private JSON evidence; existing files are not overwritten.')
    args = parser.parse_args(argv)
    result = run_ops(args.config)
    write_result(args.output, result)
    print(json.dumps({'status': result['status'], 'scope': result['scope'], 'output': str(args.output)}))
    return {'PASSED': 0, 'ALERT': 1, 'FAILED': 1, 'NOT_TESTED': 2}[result['status']]


if __name__ == '__main__':
    raise SystemExit(main())
