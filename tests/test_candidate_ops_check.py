"""Offline boundaries for the one-shot candidate monitor; no live services."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import candidate_ops_check as ops
from platform_foundation.f1.maintenance.candidate_backup import HEAD, migration_identity
from platform_foundation.f1.maintenance.object_reconcile import digest


def config_for(directory):
    dsn = directory / 'dsn'
    dsn.write_text('host=127.0.0.1 port=15432 dbname=f1_arpg_0123456789ab user=f0d_bootstrap password=not-a-real-secret')
    dsn.chmod(0o600)
    return dict(schema_version=1, scope='local_candidate', candidate_id='synthetic-ops',
        compose_project='anhuan-ar-uat-0123456789ab', docker_host='unix:///synthetic/docker.sock',
        database=dict(dsn_file=str(dsn), name='f1_arpg_0123456789ab', cluster='123456', head=HEAD),
        migration_source_sha256=migration_identity(), thresholds=copy.deepcopy(ops.DEFAULT_THRESHOLDS),
        services={name: dict(container_id=f'{index:064x}', image_id='sha256:'+'a'*64)
                  for index, name in enumerate(sorted(ops.REQUIRED_SERVICES), 1)}, object_plan=None)


def observations(config):
    return [dict(id=item['container_id'], image_id=item['image_id'], running=True, state='running',
        health='healthy', restart_count=0, project=config['compose_project'], service=name,
        ports={'5432/tcp': [{'HostIp':'127.0.0.1','HostPort':'15432'}]} if name=='postgres' else
              {'80/tcp': [{'HostIp':'127.0.0.1','HostPort':'18080'}]} if name=='web' else {})
        for name,item in config['services'].items()]


class CandidateOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.config=config_for(self.root)

    def save(self, value=None):
        path=self.root/'config.json';path.write_bytes(ops.canonical(self.config if value is None else value));path.chmod(0o600)
        return path

    def plan(self, *, age=0, issues=None, **changes):
        now=datetime.now(timezone.utc)
        plan=dict(schema_version=1, target={'database':self.config['database']['name'],
            'cluster':self.config['database']['cluster'],'head':HEAD,'storage':'b'*64},
            created_at=(now-timedelta(seconds=age)).isoformat(), minimum_retention_seconds=86400,
            object_count=7, task_count=3, issues=issues or [], status='ISSUES_FOUND' if issues else 'HEALTHY')
        plan.update(changes);plan['plan_sha256']=digest(plan)
        path=self.root/'plan.json';raw=ops.canonical(plan);path.write_bytes(raw);path.chmod(0o600)
        self.config['object_plan']={'path':str(path),'sha256':ops.sha(raw),'storage_identity':'b'*64}
        return now

    def test_no_configuration_never_probes(self):
        probe=Mock(side_effect=AssertionError('must not call'))
        result=ops.run_ops(inspect=probe,get_readiness=probe,connect_factory=probe)
        self.assertEqual(result['status'],'NOT_TESTED');probe.assert_not_called()

    def test_incomplete_or_unsafe_configuration_stops_before_probe(self):
        cases=[({'compose_project':'anhuan-f1'},'FAILED'),({'scope':'production'},'FAILED'),
               ({'docker_host':'tcp://remote:2375'},'FAILED'),({'services':{}},'NOT_TESTED'),
               ({'thresholds':{}},'NOT_TESTED'),({'migration_source_sha256':'0'*64},'FAILED')]
        for changes,status in cases:
            with self.subTest(changes=changes):
                probe=Mock(side_effect=AssertionError('must not call'))
                result=ops.run_ops(self.save(self.config|changes),inspect=probe)
                self.assertEqual(result['status'],status);probe.assert_not_called()

    def test_private_config_and_duplicate_keys_fail_closed(self):
        path=self.save();path.chmod(0o644);probe=Mock()
        self.assertEqual(ops.run_ops(path,inspect=probe)['status'],'FAILED');probe.assert_not_called()
        path.chmod(0o600);path.write_text('{"scope":"local_candidate","scope":"production"}')
        result=ops.run_ops(path,inspect=probe)
        self.assertEqual(result['reason'],'CONFIG_DUPLICATE_KEY');probe.assert_not_called()

    def test_target_mismatch_never_connects_or_calls_http(self):
        for field,value in [('project','anhuan-f1'),('image_id','sha256:'+'f'*64),('service','unrelated')]:
            with self.subTest(field=field):
                actual=observations(self.config);actual[0][field]=value;probe=Mock()
                result=ops.run_ops(self.save(),inspect=lambda _:actual,get_readiness=probe,connect_factory=probe)
                self.assertEqual(result['reason'],'SERVICE_TARGET_IDENTITY_MISMATCH');probe.assert_not_called()

    def test_dsn_binding_rejects_remote_and_ambient_libpq_options(self):
        dsn=Path(self.config['database']['dsn_file']);original=dsn.read_text()
        for changed in [original.replace('127.0.0.1','localhost'),original.replace('15432','5432'),
            original.replace('f0d_bootstrap','f1_api'), original+' options=-cstatement_timeout=0', original+' service=production']:
            with self.subTest(dsn_changed=True):
                dsn.write_text(changed)
                with self.assertRaises(ops.OpsError):ops.readonly_dsn(self.config,15432)

    def test_service_health_restarts_and_unbound_ports(self):
        actual=observations(self.config)
        first=actual[0];first.update(health='unhealthy',restart_count=4)
        result,ports=ops.observe_services(self.config,actual)
        self.assertEqual(result['status'],'ALERT');self.assertEqual(ports,{'postgres':15432,'web':18080})
        self.assertEqual(result['services'][0]['alerts'],['SERVICE_NOT_READY','SERVICE_RESTART_THRESHOLD'])
        next(row for row in actual if row['service']=='web')['ports']['80/tcp'][0]['HostIp']='0.0.0.0'
        with self.assertRaisesRegex(ops.OpsError,'CANDIDATE_LOOPBACK_PORT_REQUIRED'):
            ops.observe_services(self.config,actual)

    def test_readiness_body_requires_all_five_actual_boolean_probes(self):
        body={'status':'ready','components':{name:True for name in ops.READINESS_COMPONENTS}}
        self.assertEqual(ops.observe_readiness(200,body)['status'],'PASSED')
        for changed in [body|{'status':'unavailable'},body|{'components':{}},
            body|{'components':body['components']|{'database':1}},body|{'extra':'private'}]:
            with self.assertRaises(ops.OpsError):ops.observe_readiness(200,changed)
        body['status']='unavailable';body['components']['redis']=False
        self.assertEqual(ops.observe_readiness(503,body)['status'],'ALERT')
        with self.assertRaises(ops.OpsError):ops.observe_readiness(200,body)

    def test_object_plan_is_explicit_dated_reference_without_private_issue_data(self):
        now=self.plan(issues=[{'code':'RELEASED_OBJECT_MISSING','action':'restore','id':'private-id',
            'identity':{'object_key':'private-object-key'}}])
        result=ops.observe_object_plan(self.config,now=now)
        self.assertEqual(result['status'],'ALERT');self.assertEqual(result['issue_count'],1)
        self.assertEqual(result['scope'],'REFERENCED_READ_ONLY_PLAN_NOT_RESCANNED')
        self.assertNotIn('private',json.dumps(result))

    def test_missing_plan_does_not_claim_all_checks_passed(self):
        with patch.object(ops,'database_snapshot',return_value={'status':'PASSED'}):
            result=ops.run_ops(self.save(),inspect=observations,
                get_readiness=lambda _: (200,{'status':'ready','components':dict.fromkeys(ops.READINESS_COMPONENTS,True)}))
        self.assertEqual(result['status'],'NOT_TESTED');self.assertEqual(result['alerts_delivered'],'NOT_TESTED')
        self.assertEqual(result['production_deployment'],'NOT_TESTED')

    def test_object_plan_wrong_target_stale_future_bad_digest_or_schema_rejected(self):
        for changes in [{'age':3601},{'age':-1},{'target':{}},{'schema_version':True}]:
            with self.subTest(changes=changes):
                now=self.plan(**changes)
                with self.assertRaises(ops.OpsError):ops.observe_object_plan(self.config,now=now)
        self.plan();self.config['object_plan']['sha256']='f'*64
        with self.assertRaisesRegex(ops.OpsError,'FINGERPRINT'):ops.observe_object_plan(self.config)
        self.plan();path=Path(self.config['object_plan']['path']);plan=json.loads(path.read_text());plan['task_count']=999
        raw=ops.canonical(plan);path.write_bytes(raw);self.config['object_plan']['sha256']=ops.sha(raw)
        with self.assertRaisesRegex(ops.OpsError,'CONTRACT'):ops.observe_object_plan(self.config)

    def test_probe_errors_are_sanitized_and_never_green(self):
        self.plan()
        with patch.object(ops,'database_snapshot',side_effect=RuntimeError('private-user:password@database')):
            result=ops.run_ops(self.save(),inspect=observations,get_readiness=Mock(side_effect=TimeoutError('private response')))
        self.assertEqual(result['status'],'FAILED');self.assertNotIn('password',json.dumps(result));self.assertNotIn('private response',json.dumps(result))

    def test_output_is_private_exclusive_and_preserves_existing_evidence(self):
        path=self.root/'result.json';ops.write_result(path,{'status':'NOT_TESTED'})
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        with self.assertRaises(FileExistsError):ops.write_result(path,{'status':'PASSED'})
        self.assertEqual(json.loads(path.read_text()),{'status':'NOT_TESTED'})


if __name__=='__main__':unittest.main()
