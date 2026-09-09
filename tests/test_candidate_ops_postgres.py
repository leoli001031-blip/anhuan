"""Real PostgreSQL 18 queue observations in a new disposable dedicated stack."""
from __future__ import annotations
import copy
from datetime import datetime, timedelta, timezone
import os
import unittest
import uuid
import psycopg
os.environ.setdefault('F1_KEYCLOAK_ISSUER_URL','http://material-rag.invalid/realms/anhuan')
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from scripts import candidate_ops_check as ops

STACK=None


def setUpModule():
    global STACK
    STACK=PostgresIntegrationStack();print('OPS_PROJECT='+STACK.project_name,flush=True)
    try:STACK.start();STACK.seed_world()
    except BaseException:
        STACK.dispose_runtime();STACK.stop();raise


def tearDownModule():
    if STACK:
        STACK.dispose_runtime();STACK.stop()
        if STACK.cleanup_status!='CLEAN' or STACK.dedicated_after!=(0,0,0) or STACK.shared_match!=1:
            raise AssertionError('OPS_CLEANUP_FAILED')
        print('OPS_CLEANUP=CLEAN;DEDICATED_AFTER=0,0,0;SHARED_UNCHANGED=1',flush=True)


class CandidateOpsPostgresTests(unittest.TestCase):
    def setUp(self):
        self.thresholds=copy.deepcopy(ops.DEFAULT_THRESHOLDS)
        with STACK._bootstrap() as c:
            self.expected=dict(database=STACK.database,cluster=c.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0])
            # Only this disposable stack receives synthetic failure fixtures.
            c.execute("SET LOCAL session_replication_role='replica'")
            c.execute('DELETE FROM f1.material_ingestion_delivery')
            self.versions=c.execute('SELECT enterprise_id,id FROM f1.document_version ORDER BY id LIMIT 5').fetchall()
        self.assertGreaterEqual(len(self.versions),3)

    def seed_delivery(self,index,state,*,age=0,attempt=0,lease_age=None,retry_age=None):
        now=datetime.now(timezone.utc);updated=now-timedelta(seconds=age)
        token=uuid.uuid4() if state=='dispatched' else None
        lease=now-timedelta(seconds=lease_age) if lease_age is not None else None
        due=now-timedelta(seconds=retry_age) if retry_age is not None else None
        reason='SYNTHETIC_OPS_TEST' if state in {'blocked','retry_wait'} else None
        completed=updated if state in {'blocked','done'} else None
        enterprise,version=self.versions[index]
        with STACK._bootstrap() as c:
            c.execute("SET LOCAL session_replication_role='replica'")
            c.execute('INSERT INTO f1.material_ingestion_delivery(id,enterprise_id,document_version_id,actor_sub,state,attempt,'
                'dispatch_token,dispatch_lease_until,next_attempt_at,reason_code,created_at,updated_at,completed_at)'
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (uuid.uuid4(),enterprise,version,'synthetic-ops-fixture',state,attempt,token,lease,due,reason,updated,updated,completed))

    def snapshot(self):return ops.database_snapshot(STACK._bootstrap,self.expected,self.thresholds)

    def ingestion(self,snapshot):return next(q for q in snapshot['queues'] if q['table']=='f1.material_ingestion_delivery')

    def fingerprint(self):
        with STACK._bootstrap() as c:
            return c.execute("SELECT (SELECT md5(coalesce(jsonb_agg(to_jsonb(d) ORDER BY id)::text,'')) FROM f1.material_ingestion_delivery d),"
                "(SELECT count(*) FROM f1.audit_log),(SELECT count(*) FROM f1.analysis_report_audit_event)").fetchone()

    def test_known_retry_blocked_pending_and_expired_lease_are_visible_without_mutation(self):
        self.seed_delivery(0,'retry_wait',age=900,attempt=3,retry_age=300)
        self.seed_delivery(1,'blocked',age=30,attempt=4)
        self.seed_delivery(2,'dispatched',age=900,attempt=2,lease_age=300)
        before=self.fingerprint();snapshot=self.snapshot();after=self.fingerprint()
        self.assertEqual(before,after);self.assertTrue(snapshot['transaction_read_only']);self.assertEqual(snapshot['protected_rls_tables'],55)
        self.assertEqual(len(snapshot['queues']),7)
        queue=self.ingestion(snapshot);metrics=queue['metrics']
        self.assertEqual(metrics['states'],{'pending':0,'dispatched':1,'retry_wait':1,'done':0,'blocked':1})
        self.assertEqual({k:metrics[k] for k in ('terminal_failures_total','recent_terminal_failures','overdue_retries',
            'overdue_pending','expired_leases','expired_leases_past_grace','high_attempt_active')},
            dict(terminal_failures_total=1,recent_terminal_failures=1,overdue_retries=1,overdue_pending=0,
                expired_leases=1,expired_leases_past_grace=1,high_attempt_active=1))
        self.assertEqual(queue['status'],'ALERT')
        self.assertEqual({a['metric'] for a in queue['alerts']},{'recent_terminal_failures','overdue_retries',
            'expired_leases_past_grace','high_attempt_active'})
        with STACK._bootstrap() as c:
            c.execute("SET LOCAL session_replication_role='replica'")
            c.execute('DELETE FROM f1.material_ingestion_delivery WHERE document_version_id=%s',(self.versions[2][1],))
        self.seed_delivery(2,'pending',age=900)
        before=self.fingerprint();pending=self.ingestion(self.snapshot());self.assertEqual(before,self.fingerprint())
        self.assertEqual(pending['metrics']['overdue_pending'],1)
        self.assertIn('overdue_pending',{a['metric'] for a in pending['alerts']})
        print('OPS_REAL_QUEUE=retry1,blocked1,recent_blocked1,expired1;PENDING_PROBE=1;ROWS_AND_AUDIT_UNCHANGED=1',flush=True)

    def test_old_terminal_records_and_lease_grace_do_not_create_false_alarms(self):
        self.seed_delivery(0,'blocked',age=7200,attempt=99)
        self.seed_delivery(1,'done',age=30,attempt=99)
        self.seed_delivery(2,'dispatched',age=10,attempt=1,lease_age=10)
        queue=self.ingestion(self.snapshot());metrics=queue['metrics']
        self.assertEqual(metrics['terminal_failures_total'],1);self.assertEqual(metrics['recent_terminal_failures'],0)
        self.assertEqual(metrics['high_attempt_active'],0);self.assertEqual(metrics['expired_leases'],1)
        self.assertEqual(metrics['expired_leases_past_grace'],0);self.assertEqual(queue['status'],'PASSED')

    def test_readonly_transaction_rejects_even_noop_business_write(self):
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with ops.readonly_connection(STACK._bootstrap) as c:
                c.execute('UPDATE f1.material_ingestion_delivery SET attempt=attempt WHERE false')

    def test_wrong_cluster_and_stale_head_cannot_pass(self):
        with self.assertRaisesRegex(ops.OpsError,'DATABASE_TARGET_IDENTITY_MISMATCH'):
            ops.database_snapshot(STACK._bootstrap,self.expected|{'cluster':'1'},self.thresholds)
        with STACK._bootstrap() as c:c.execute("UPDATE f1.alembic_version SET version_num='f1_0043'")
        try:
            with self.assertRaisesRegex(ops.OpsError,'CURRENT_MIGRATION_HEAD_REQUIRED'):self.snapshot()
        finally:
            with STACK._bootstrap() as c:c.execute("UPDATE f1.alembic_version SET version_num='f1_0044'")

    def test_narrowed_bootstrap_role_cannot_hide_force_rls_rows_as_zero(self):
        self.seed_delivery(0,'blocked',age=30)
        def narrowed():
            c=STACK._bootstrap();c.execute('SET ROLE f1_api');c.commit();return c
        with self.assertRaisesRegex(ops.OpsError,'MAINTENANCE_VISIBILITY_REQUIRED'):
            ops.database_snapshot(narrowed,self.expected,self.thresholds)
        self.assertEqual(self.ingestion(self.snapshot())['metrics']['recent_terminal_failures'],1)


if __name__=='__main__':unittest.main()
