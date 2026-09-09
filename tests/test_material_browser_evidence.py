"""Actual child termination must preserve partial evidence and stop owned work."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from scripts.material_browser_gate import BrowserInterrupted, bounded_command, ROOT


class BrowserProcessEvidence(unittest.TestCase):
    def test_timeout_preserves_output_and_reaps_child(self):
        result,error=bounded_command([sys.executable,'-c',
            'import os,time;print(os.getpid(),flush=True);print("before-timeout",flush=True);time.sleep(30)'],
            environment=os.environ.copy(),timeout=.3)
        self.assertIsInstance(error,subprocess.TimeoutExpired)
        self.assertIn('before-timeout',result.stdout)
        self.assertLess(result.returncode,0)
        with self.assertRaises(ProcessLookupError):os.kill(int(result.stdout.splitlines()[0]),0)

    def test_external_signal_retains_output_and_terminates_child(self):
        def stop(signum,frame):raise BrowserInterrupted('test interruption')
        previous=signal.signal(signal.SIGALRM,stop)
        try:
            signal.setitimer(signal.ITIMER_REAL,.3)
            result,error=bounded_command([sys.executable,'-c',
                'import os,time;print(os.getpid(),flush=True);print("before-signal",flush=True);time.sleep(30)'],
                environment=os.environ.copy(),timeout=10)
        finally:
            signal.setitimer(signal.ITIMER_REAL,0)
            signal.signal(signal.SIGALRM,previous)
        self.assertIsInstance(error,BrowserInterrupted)
        self.assertIn('before-signal',result.stdout)
        self.assertLess(result.returncode,0)
        with self.assertRaises(ProcessLookupError):os.kill(int(result.stdout.splitlines()[0]),0)

    def test_fixture_originals_are_in_acceptance_source_inventory(self):
        from scripts.acceptance_gate import source_fingerprint
        result=source_fingerprint(ROOT,ROOT/'.local/acceptance')
        included={item['path'] for item in result['files']}
        for path in ['tests/fixtures/docx/ordinary-generated.docx','tests/fixtures/xlsx/ordinary-generated.xlsx']:
            self.assertIn(path,included)
            self.assertTrue((ROOT/path).is_file())

    def test_failed_supervision_reaps_running_browser_process(self):
        def fail():raise RuntimeError('recovery observer failed')
        result,error=bounded_command([sys.executable,'-c',
            'import os,time;print(os.getpid(),flush=True);time.sleep(30)'],
            environment=os.environ.copy(),timeout=10,on_tick=fail)
        self.assertIsInstance(error,RuntimeError)
        self.assertEqual(str(error),'recovery observer failed')
        self.assertLess(result.returncode,0)
        with self.assertRaises(ProcessLookupError):os.kill(int(result.stdout.strip()),0)
