"""Black-box regression of honest acceptance counts, interruption and snapshots."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import acceptance_gate as gate


class AcceptanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_cases(self, source, *, timeout=15, names=None):
        (self.root / "sample.py").write_text(textwrap.dedent(source), encoding="utf-8")
        return gate.run_unittest(names or ["sample"], self.root / "evidence", python=sys.executable,
                                 timeout=timeout, root=self.root)

    def test_multiple_failures_errors_and_skips_have_real_ids(self):
        result = self.run_cases('''
            import unittest
            class Sample(unittest.TestCase):
                def test_a_pass(self): print("FULL_LOG_START" + "x" * 10000 + "FULL_LOG_END")
                def test_b_fail(self): self.fail("first")
                def test_c_fail(self): self.fail("second")
                def test_d_error(self): raise RuntimeError("actual error")
                @unittest.skip("explicit skip")
                def test_e_skip(self): pass
        ''')
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual({key: result["counts"][key] for key in ["collected", "run", "passed", "failed", "error", "skipped"]},
                         {"collected": 5, "run": 5, "passed": 1, "failed": 2, "error": 1, "skipped": 1})
        self.assertFalse(result["process_error"])
        self.assertEqual(result["process"]["exit_code"], 1)
        self.assertEqual([case["id"] for case in result["cases"] if case["outcome"] == "failed"],
                         ["sample.Sample.test_b_fail", "sample.Sample.test_c_fail"])
        log = Path(result["process"]["log"]).read_text()
        self.assertIn("FULL_LOG_START" + "x" * 10000 + "FULL_LOG_END", log)
        self.assertIn("RuntimeError: actual error", log)

    def test_skip_only_does_not_pass_acceptance(self):
        result = self.run_cases('''
            import unittest
            class Sample(unittest.TestCase):
                @unittest.skip("unavailable fixture")
                def test_skip(self): pass
        ''')
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["counts"]["passed"], 0)
        self.assertEqual(result["counts"]["skipped"], 1)
        self.assertEqual(result["process"]["exit_code"], 0)

    def test_import_failure_is_discovery_error_not_a_fake_case(self):
        result = self.run_cases("raise ImportError('required_dependency_missing')")
        self.assertEqual(result["status"], "NOT_TESTED")
        self.assertEqual(result["counts"]["collected"], 0)
        self.assertEqual(result["counts"]["run"], 0)
        self.assertEqual(result["counts"]["error"], 0)
        self.assertEqual(len(result["discovery_errors"]), 1)

    def test_fixture_failure_does_not_count_collected_tests_as_run(self):
        result = self.run_cases('''
            import unittest
            def setUpModule(): raise RuntimeError("database unreachable")
            class Sample(unittest.TestCase):
                def test_first(self): pass
                def test_second(self): pass
        ''')
        self.assertEqual(result["status"], "NOT_TESTED")
        self.assertEqual(result["counts"]["collected"], 2)
        self.assertEqual(result["counts"]["run"], 0)
        self.assertEqual(result["counts"]["error"], 0)
        self.assertEqual(result["counts"]["not_run"], 2)
        self.assertEqual(result["fixture_events"][0]["event"], "fixture_error")

    def test_two_failing_subtests_count_one_parent(self):
        result = self.run_cases('''
            import unittest
            class Sample(unittest.TestCase):
                def test_parent(self):
                    for n in range(2):
                        with self.subTest(n=n): self.fail(str(n))
        ''')
        self.assertEqual(result["counts"]["failed"], 1)
        self.assertEqual(result["counts"]["run"], 1)
        events = [json.loads(line) for line in Path(result["journal"]).read_text().splitlines()]
        self.assertEqual(len([event for event in events if event["event"] == "subtest"]), 2)

    def test_process_exit_preserves_partial_results_without_inventing_errors(self):
        result = self.run_cases('''
            import os, unittest
            class Sample(unittest.TestCase):
                def test_a_pass(self): pass
                def test_b_exit(self): os._exit(23)
                def test_c_never(self): pass
        ''')
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["process_error"])
        self.assertEqual(result["process"]["exit_code"], 23)
        self.assertEqual({key: result["counts"][key] for key in ["collected", "run", "passed", "error", "incomplete", "not_run"]},
                         {"collected": 3, "run": 2, "passed": 1, "error": 0, "incomplete": 1, "not_run": 1})

    def test_timeout_has_process_evidence_not_a_fake_case_error(self):
        result = self.run_cases('''
            import time, unittest
            class Sample(unittest.TestCase):
                def test_hang(self): time.sleep(60)
        ''', timeout=1)
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(result["process"]["timed_out"])
        self.assertEqual(result["counts"]["run"], 1)
        self.assertEqual(result["counts"]["incomplete"], 1)
        self.assertEqual(result["counts"]["error"], 0)

    def test_missing_interpreter_is_not_tested(self):
        result = gate.run_unittest(["sample"], self.root / "evidence", python=str(self.root / "missing-python"),
                                   timeout=1, root=self.root)
        self.assertEqual(result["status"], "NOT_TESTED")
        self.assertEqual(result["counts"]["run"], 0)
        self.assertEqual(result["counts"]["error"], 0)
        self.assertIsNone(result["process"]["exit_code"])
        self.assertTrue(result["process"]["launch_error"])

    def test_clean_execution_has_every_case_id(self):
        result = self.run_cases('''
            import unittest
            class Sample(unittest.TestCase):
                def test_real(self): self.assertEqual(2 + 2, 4)
        ''')
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["counts"]["passed"], 1)
        self.assertEqual(result["cases"][0]["id"], "sample.Sample.test_real")

    def test_node_events_preserve_multiple_failures_skip_and_full_names(self):
        (self.root / "sample.cjs").write_text('''
            const test = require('node:test');
            const assert = require('node:assert/strict');
            test('actual pass', () => assert.equal(1, 1));
            test('first failure', () => assert.equal(1, 2));
            test('second failure', () => { throw new Error('second'); });
            test.skip('explicit skip', () => {});
        ''')
        result = gate.run_node_tests(["sample.cjs"], self.root / "evidence", cwd=self.root, timeout=15)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual({key: result["counts"][key] for key in ["collected", "run", "passed", "failed", "skipped"]},
                         {"collected": 4, "run": 4, "passed": 1, "failed": 2, "skipped": 1})
        self.assertEqual([case["name"] for case in result["cases"]],
                         ["actual pass", "first failure", "second failure", "explicit skip"])
        self.assertFalse(result["process_error"])

    def test_node_import_failure_is_not_a_fake_case(self):
        (self.root / "sample.cjs").write_text("require('nonexistent-evidence-package');")
        result = gate.run_node_tests(["sample.cjs"], self.root / "evidence", cwd=self.root, timeout=15)
        self.assertEqual(result["status"], "NOT_TESTED")
        self.assertEqual(result["counts"]["collected"], 0)
        self.assertEqual(result["counts"]["failed"], 0)
        self.assertEqual(len(result["wrapper_errors"]), 1)

    def test_teardown_error_blocks_pass_but_keeps_case_success(self):
        result = self.run_cases('''
            import unittest
            def tearDownModule(): raise RuntimeError("cleanup failed")
            class Sample(unittest.TestCase):
                def test_real(self): pass
        ''')
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["counts"]["passed"], 1)
        self.assertEqual(result["counts"]["error"], 0)
        self.assertEqual(len(result["fixture_events"]), 1)

    def test_quality_missing_real_material_manifest_is_not_tested(self):
        with patch.object(gate,"source_fingerprint",return_value={
                "status":"CAPTURED","head":"abc","working_tree_sha256":"def"}):
            output=self.root/"quality"
            self.assertEqual(gate.run_gate("quality",output),2)
            report=json.loads(next(output.glob("*/report.json")).read_text())
            self.assertEqual(report["status"],"NOT_TESTED")

    def test_browser_missing_docker_never_starts_stack(self):
        with patch.object(gate,'source_fingerprint',return_value={
                'status':'CAPTURED','head':'abc','working_tree_sha256':'def'}), \
             patch.object(gate,'run_process',return_value={'exit_code':None,'timed_out':False,'launch_error':'unavailable'}), \
             patch.object(gate,'run_browser') as browser:
            self.assertEqual(gate.run_gate('browser',self.root/'browser'),2)
            browser.assert_not_called()
            report=json.loads(next((self.root/'browser').glob('*/report.json')).read_text())
            self.assertEqual(report['status'],'NOT_TESTED')

    def test_browser_claimed_pass_requires_complete_journal_and_cleanup(self):
        output=self.root/'browser-evidence';output.mkdir()
        result={'status':'PASSED','shared_unchanged':True,'owned_image_tags_removed':True,
                'runtime_recovery':{'state':'PASSED'},'cleanup':{'containers':0,'volumes':0,'networks':0,'control_removed':True}}
        (output/'browser-result.json').write_text(json.dumps(result))
        process={'exit_code':0,'timed_out':False,'launch_error':None}
        with patch.object(gate,'run_process',return_value=process):
            self.assertEqual(gate.run_browser(output,python=sys.executable,timeout=1)['status'],'ERROR')
            journal=output/'material-checks.jsonl'
            events=[{'event':'collected','checks':['upload','original']},{'event':'check','id':'upload','outcome':'passed'}]
            journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
            incomplete=gate.run_browser(output,python=sys.executable,timeout=1)
            self.assertEqual(incomplete['status'],'ERROR')
            self.assertEqual(incomplete['counts']['not_run'],1)
            events.append({'event':'check','id':'original','outcome':'passed'})
            journal.write_text(''.join(json.dumps(e)+'\n' for e in events))
            self.assertEqual(gate.run_browser(output,python=sys.executable,timeout=1)['status'],'PASSED')
            result['runtime_recovery']['state']='SERVICES_RESTARTED'
            (output/'browser-result.json').write_text(json.dumps(result))
            self.assertEqual(gate.run_browser(output,python=sys.executable,timeout=1)['status'],'ERROR')
            result['runtime_recovery']['state']='PASSED'
            result['cleanup']['volumes']=1
            (output/'browser-result.json').write_text(json.dumps(result))
            self.assertEqual(gate.run_browser(output,python=sys.executable,timeout=1)['status'],'ERROR')

    def test_browser_failure_retains_passed_failed_and_not_run_checks(self):
        output=self.root/'browser-failed';output.mkdir()
        (output/'browser-result.json').write_text(json.dumps({'status':'FAILED'}))
        events=[{'event':'collected','checks':['upload','original','report']},
                {'event':'check','id':'upload','outcome':'passed'},
                {'event':'check','id':'original','outcome':'failed'}]
        (output/'material-checks.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        with patch.object(gate,'run_process',return_value={'exit_code':1,'timed_out':False,'launch_error':None}):
            result=gate.run_browser(output,python=sys.executable,timeout=1)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['counts'],{'passed':1,'failed':1,'not_run':1,'collected':3,'run':2})

    def test_restore_without_docker_is_not_tested_and_never_runs_backend(self):
        with patch.object(gate,'source_fingerprint',return_value={
                'status':'CAPTURED','head':'abc','working_tree_sha256':'def'}), \
             patch.object(gate,'run_process',return_value={'exit_code':None,'timed_out':False,'launch_error':'unavailable'}), \
             patch.object(gate,'run_unittest') as backend:
            self.assertEqual(gate.run_gate('restore',self.root/'restore'),2)
            backend.assert_not_called()
            report=json.loads(next((self.root/'restore').glob('*/report.json')).read_text())
            self.assertEqual(report['status'],'NOT_TESTED')

    def test_fingerprint_covers_dirty_new_and_deleted_files_at_same_head(self):
        def git(*args):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        git("init")
        (self.root / "tracked.py").write_text("before")
        git("add", "tracked.py")
        git("-c", "user.name=Evidence Test", "-c", "user.email=evidence@example.invalid", "commit", "-m", "initial")
        excluded = self.root / "evidence"
        before = gate.source_fingerprint(self.root, excluded)
        (self.root / "tracked.py").write_text("after")
        dirty = gate.source_fingerprint(self.root, excluded)
        self.assertEqual(before["head"], dirty["head"])
        self.assertNotEqual(before["working_tree_sha256"], dirty["working_tree_sha256"])
        (self.root / "new.py").write_text("untracked source")
        added = gate.source_fingerprint(self.root, excluded)
        self.assertNotEqual(added["working_tree_sha256"], dirty["working_tree_sha256"])
        (self.root / "tracked.py").unlink()
        deleted = gate.source_fingerprint(self.root, excluded)
        self.assertNotEqual(deleted["working_tree_sha256"], added["working_tree_sha256"])
        excluded.mkdir()
        (excluded / "run.json").write_text("generated proof")
        self.assertEqual(gate.source_fingerprint(self.root, excluded)["working_tree_sha256"], deleted["working_tree_sha256"])

    def test_postgres_harness_uses_writable_linux_temporary_root(self):
        from infra.f1 import analysis_report_postgres_integration as harness
        with patch.object(harness.sys, "platform", "linux"), patch.object(
                harness.tempfile, "gettempdir", return_value=str(self.root)):
            stack = harness.PostgresIntegrationStack()
        self.assertEqual(stack.control_dir.parent, self.root)
        self.assertEqual(stack.control_dir.name, stack.project_name)
        with patch.object(harness.sys, "platform", "darwin"):
            mac_stack = harness.PostgresIntegrationStack()
        self.assertEqual(mac_stack.control_dir.parent, Path("/private/tmp"))


if __name__ == "__main__":
    unittest.main()
