"""Fail-closed release evidence behavior, separate from RFC wire conformance."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/qualify_websocket_release.py'


class ReleaseQualificationTests(unittest.TestCase):
    def test_inspection_never_claims_to_qualify_unrun_release_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'report.json'
            process = subprocess.run((sys.executable, str(SCRIPT), '--inspect',
                                      '--output', str(report_path)), cwd=ROOT,
                capture_output=True, text=True, timeout=8)
            self.assertNotEqual(process.returncode, 0)
            report = json.loads(report_path.read_text())
            self.assertFalse(report['qualification_passed'])
            self.assertTrue(report['required_targeted_suites'])
            self.assertFalse(report['targeted'])
            self.assertTrue(report['lockfile_sha256'])
            self.assertTrue(all(not value['passed'] for value in report['external'].values()))

    def test_intermittent_resource_gate_requires_three_clean_processes(self):
        """A single lucky pass cannot silence an intermittently failing suite."""
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            attempt = subprocess.run((sys.executable, str(SCRIPT), '--inspect',
                '--resource-repetitions', '2', '--output', str(report)),
                cwd=ROOT, capture_output=True, text=True, timeout=8)
            self.assertNotEqual(attempt.returncode, 0)
            self.assertIn('at least three', attempt.stderr)
            self.assertFalse(report.exists())

    def test_zero_exit_external_noop_cannot_sign_off_release_gate(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('qualifier', SCRIPT)
        qualifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualifier)
        result = qualifier._run('browser_and_tls_reverse_proxy',
            [sys.executable, '-c', 'print("all tests passed")'],
            5, dict(__import__('os').environ))
        self.assertFalse(result['passed'])
        self.assertEqual(result['attempts'][0]['exit_code'], 0)
        self.assertEqual(result['attempts'][0]['external_evidence_error'],
                         'External qualification marker missing')

    @unittest.skipUnless(sys.platform.startswith('linux'),
                         'POSIX process-group orphan accounting tested on Linux')
    def test_timeout_kills_nested_test_processes_not_only_parent(self):
        """WS-20: a hanging suite cannot leave its launched test server alive."""
        import importlib.util
        import os
        import time
        spec = importlib.util.spec_from_file_location('qualifier', SCRIPT)
        qualifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualifier)
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'child.pid'
            script = ("import subprocess,sys,time; "
                      "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
                      "open(sys.argv[1],'w').write(str(p.pid)); "
                      "print('Ran 1 test in 0.1s\\nOK',flush=True); time.sleep(30)")
            result = qualifier._run('tests.subprocess_timeout_fixture',
                [sys.executable, '-c', script, str(marker)],
                2, dict(os.environ))
            self.assertFalse(result['passed'])
            self.assertTrue(result['attempts'][0]['timed_out'])
            self.assertTrue(marker.exists())
            pid = int(marker.read_text())
            deadline = time.monotonic()+2
            while time.monotonic()<deadline:
                try:
                    state = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1][0]
                    if state == 'Z':
                        break  # Defunct is not runnable, pending reaping by init.
                except FileNotFoundError:
                    break
                time.sleep(.02)
            else:
                self.fail(f'child test process {pid} is still runnable after timeout')

    def test_full_scope_cannot_pass_without_h3_implementation_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'report.json'
            process = subprocess.run((sys.executable, str(SCRIPT), '--inspect',
                                      '--scope', 'full', '--output', str(report_path)),
                cwd=ROOT, capture_output=True, text=True, timeout=8)
            self.assertNotEqual(process.returncode, 0)
            report = json.loads(report_path.read_text())
            self.assertEqual(report['scope'], 'full')
            self.assertFalse(report['qualification_passed'])
            self.assertIn('h3_websocket_interoperability', report['external'])
            self.assertFalse(report['external']['h3_websocket_interoperability']['passed'])


if __name__ == '__main__':
    unittest.main()
