"""Collect bounded line/branch coverage for the five private/public HTTP modules.

Coverage is measured using the independent tests on a real libcurl transport;
this does not inspect native libcurl/TLS implementation or constitute a
security audit. The JSON output uses coverage.py's standard schema.
"""
from __future__ import annotations

import argparse
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = ','.join(('valiance.std._http_core', 'valiance.std._http_body',
                   'valiance.std._http_cookies', 'valiance.std._http_transport',
                   'valiance.std.http'))
SUITES = (
    'tests.test_std_http', 'tests.test_std_http_http2',
    'tests.test_std_http_http2_adversarial', 'tests.test_std_http_http2_fuzz',
    'tests.test_std_http_http2_soak', 'tests.test_std_http_adversarial',
    'tests.test_std_http_security_audit', 'tests.test_std_http_protocol_fuzz',
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=int, default=180)
    args = parser.parse_args()
    if not 30 <= args.timeout_seconds <= 1200:
        parser.error('timeout-seconds must be 30..1200')
    project = Path(__file__).resolve().parent.parent
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='std-http-coverage-') as path:
        datafile = str(Path(path) / '.coverage')
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join((str(project / 'src'), str(project),
                                            env.get('PYTHONPATH', '')))
        run = [sys.executable, '-m', 'coverage', 'run', '--branch',
               f'--data-file={datafile}', f'--source={SOURCE}', '-m',
               'unittest', '-q', *SUITES]
        print('Collecting HTTP source/branch coverage from 8 focused suites', flush=True)
        kwargs = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                  if os.name == 'nt' else {'start_new_session': True})
        process = subprocess.Popen(run, cwd=project, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, **kwargs)
        try:
            output, _ = process.communicate(timeout=args.timeout_seconds)
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                try:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   capture_output=True, timeout=10, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill() if process.poll() is None else None
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            print('Coverage process tree exceeded deadline (failed)', file=sys.stderr)
            return 1
        if process.returncode:
            print(output[-4000:], file=sys.stderr)
            return 1
        export = [sys.executable, '-m', 'coverage', 'json',
                  f'--data-file={datafile}', '-o', str(report), '--pretty-print']
        return subprocess.run(export, cwd=project, env=env, timeout=30).returncode


if __name__ == '__main__':
    sys.exit(main())
