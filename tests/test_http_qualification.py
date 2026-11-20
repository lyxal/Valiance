"""Fail-closed and process-timeout invariants for std.http qualification."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import qualify_std_http as qualify


class HTTPQualificationTests(unittest.TestCase):
    def test_skipped_or_empty_suites_cannot_qualify(self):
        self.assertEqual(qualify._suite_result(0, 'Ran 0 tests in 0.0s\n\nOK', False)[0], 'failed')
        self.assertEqual(qualify._suite_result(0, 'Ran 1 test in 0.0s\n\nOK (skipped=1)', False)[0], 'failed')
        self.assertEqual(qualify._suite_result(0, 'Ran 10 tests in 1.0s\n\nOK', False),
                         ('passed', 10, 0))
        self.assertEqual(qualify._suite_result(0, 'Ran 10 tests in 1.0s\n\nOK', True)[0], 'timeout')

    def test_timeout_is_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            failure = {
                'suite': 'tests.test_std_http', 'outcome': 'timeout',
                'exit_code': -9, 'duration_seconds': 0.2,
                'timeout_seconds': 1, 'command': [], 'output_tail': '',
            }
            with patch.object(qualify, '_run_suite', return_value=failure):
                status = qualify.main([
                    '--suite', 'tests.test_std_http', '--report', str(report),
                    '--allow-unsupported-python',
                ])
            evidence = json.loads(report.read_text())
            self.assertEqual(status, 1)
            self.assertFalse(evidence['all_suites_passed'])
            self.assertFalse(evidence['local_gate_passed'])
            self.assertFalse(evidence['cross_platform_qualified'])
            self.assertEqual(evidence['suites'][0]['outcome'], 'timeout')

    def test_supported_python_gate_is_not_inferred_from_passing_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            passing = {
                'suite': 'tests.test_std_http', 'outcome': 'passed',
                'exit_code': 0, 'duration_seconds': 0.1,
                'timeout_seconds': 1, 'command': [], 'output_tail': 'OK',
            }
            with patch.object(qualify, '_run_suite', return_value=passing):
                status = qualify.main(['--suite', 'tests.test_std_http',
                                       '--report', str(report)])
            evidence = json.loads(report.read_text())
            self.assertEqual(status, 0 if sys.version_info >= (3, 14) else 1)
            self.assertTrue(evidence['all_suites_passed'])
            self.assertEqual(evidence['local_gate_passed'], sys.version_info >= (3, 14))

    def test_terminated_group_includes_descendant(self):
        # POSIX can kill an orphaned descendant via its original process group.
        # On Windows taskkill /T needs the parent to still be running. Exercise
        # its supported live-parent tree-termination path without a skip: a
        # skipped test would make the cross-platform qualification gate fail.
        parent_code = (
            'import subprocess,sys,time; '
            'subprocess.Popen([sys.executable,"-c",'
            '"import time;time.sleep(30)"]); '
            'print("parent started",flush=True); '
            + ('time.sleep(30)' if os.name == 'nt' else '')
        )
        kwargs = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                  if os.name == 'nt' else {'start_new_session': True})
        parent = subprocess.Popen(
            [sys.executable, '-c', parent_code],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs,
        )
        try:
            if os.name != 'nt':
                parent.wait(timeout=5)
            with self.assertRaises(subprocess.TimeoutExpired):
                parent.communicate(timeout=0.35)
            qualify._terminate_tree(parent)
            output, _ = parent.communicate(timeout=5)
            self.assertIn(b'parent started', output)
        finally:
            qualify._terminate_tree(parent)
            if parent.poll() is None:
                parent.kill()
            parent.communicate(timeout=5)

    def test_process_group_termination_reaps_stalled_child(self):
        kwargs = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                  if os.name == 'nt' else {'start_new_session': True})
        child = subprocess.Popen(
            [sys.executable, '-c', 'import time; time.sleep(60)'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
        )
        try:
            qualify._terminate_tree(child)
            child.communicate(timeout=5)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
