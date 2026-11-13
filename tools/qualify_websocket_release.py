#!/usr/bin/env python3
"""Fail-closed qualification runner for std.web WebSockets.

The targeted suite is evidence for specific H1/H2 behaviors, *not* release
sign-off. A zero exit additionally requires Python 3.14+, a dependency lock,
clean full-repository regressions, browser/proxy and independently implemented
H2 interoperability, repeatable stress and the external security fixture.
H3 is required only for ``--scope full``. Missing commands and omitted or
skipped mandatory tests always fail; no test that prints OK and hangs counts.

Example, for a partial evidence report without running tests:
  python tools/qualify_websocket_release.py --inspect --output evidence.json

For full qualification, supply independently reviewed executable test commands
via the flags in --help, on the supported Python 3.14 environment. Commands
run with shlex splitting, not through a shell. Do not use no-op commands.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import signal
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
SUITES = (
    'test_std_web_v28_websocket_h1',
    'test_std_web_v28_websocket_valiance',
    'test_std_web_v29_websocket_h2',
    'test_std_web_v30_websocket_admission',
    'test_std_web_v30_websocket_single_reader',
    'test_std_web_websocket_heartbeat',
    'test_std_web_websocket_request_context',
    'test_std_web_websocket_typed_context_memory',
    'test_std_web_websocket_ws01_ws07',
    'test_std_web_ws04_ws05_cancellation',
    'test_std_web_ws08_ws11_resource_gates',
    'test_std_web_websocket_ws11_deflate',
    'test_std_web_websocket_ws12_handshake_vectors',
    'test_std_web_websocket_ws13_frame_corpus',
    'test_std_web_websocket_interoperability',
    'test_std_web_http2_header_fragment_fuzz',
    'test_std_web_http2_completion_field_budgets',
    'test_std_web_v23_http2_rfc',
    'test_std_web_v24_http2_acceptance',
    'test_std_web_websocket_release_qualification',
)
# The independent work cannot be replaced by the local hyper-h2 test fixture.
EXTERNAL = {
    'browser_and_tls_reverse_proxy': 'browser-proxy-command',
    'independent_h2_websocket_client': 'h2-interop-command',
    'security_origin_and_auth': 'security-command',
    'full_repository_regressions': 'full-repo-command',
    'reproducible_fuzz_campaign': 'fuzz-command',
    'mixed_protocol_longevity_stress': 'stress-command',
}
FULL_ONLY = {'h3_websocket_interoperability': 'h3-interop-command'}


def _run(name: str, command: list[str], seconds: int, env: dict[str, str],
         repeat: int = 1) -> dict:
    attempts = []
    for index in range(repeat):
        started = time.monotonic()
        # Isolate each test command in its own POSIX process group. A timed-out
        # test may have spawned server/VM/client helpers; killing only the main
        # Python process leaks those descendants into subsequent release runs.
        # Windows has no portable process-group kill; the direct child is
        # killed there, and that platform remains subject to separate CI runs.
        process = subprocess.Popen(command, cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors='replace', start_new_session=os.name == 'posix')
        try:
            output, _ = process.communicate(timeout=seconds)
            code, timed_out = process.returncode, False
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            # Reap the main worker and collect output even if it already
            # printed unittest's OK before its teardown deadlocked.
            try:
                output, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
            code = process.returncode
        # unittest can print OK then hang. Only real zero exit passes.
        tests = re.findall(r'Ran (\d+) tests? in', output)
        skipped = bool(re.search(r'OK \([^)]*skipped\s*=\s*[1-9]', output))
        ok = code == 0 and not timed_out and not skipped
        evidence = None
        evidence_error = None
        if name.startswith('tests.'):
            ok = ok and len(tests) > 0 and int(tests[-1]) > 0 and 'OK' in output
        else:
            # A zero-exit no-op (e.g. `true`) must never sign off a release
            # gate. External independent clients/tools report tested cases
            # using one positive machine-readable evidence record on stdout.
            marker = 'VALIANCE_WS_EVIDENCE='
            for line in output.splitlines():
                if line.startswith(marker):
                    try:
                        evidence = json.loads(line[len(marker):])
                    except (ValueError, TypeError):
                        evidence_error = 'Invalid external qualification JSON'
                    break
            if not isinstance(evidence, dict):
                evidence_error = evidence_error or 'External qualification marker missing'
            elif (evidence.get('gate') != name or
                  type(evidence.get('tests_run')) is not int or
                  evidence['tests_run'] <= 0 or
                  type(evidence.get('skipped')) is not int or
                  evidence['skipped'] != 0 or
                  evidence.get('passed') is not True or
                  not isinstance(evidence.get('implementation'), str) or
                  not evidence['implementation'].strip()):
                evidence_error = 'External evidence missing positive cases, implementation identity, or gate match'
            if evidence_error:
                ok = False
        attempts.append({
            'repeat': index + 1, 'exit_code': code, 'timed_out': timed_out,
            'elapsed_seconds': round(time.monotonic() - started, 3),
            'tests_run': int(tests[-1]) if tests else None,
            'skip_detected': skipped, 'passed': ok,
            'external_evidence': evidence, 'external_evidence_error': evidence_error,
            'output_tail': output[-2500:],
        })
    return {'name': name, 'command': command, 'attempts': attempts,
            'passed': all(x['passed'] for x in attempts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=('h1h2', 'full'), default='h1h2')
    parser.add_argument('--inspect', action='store_true',
                        help='Report prerequisites without running tests; always fails closed')
    parser.add_argument('--output', type=Path, default=Path(
                        '.artifacts/websocket-release-evidence.json'))
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--resource-repetitions', type=int, default=3,
        help='Separate clean-exit repetitions for the intermittently sensitive resource suite')
    for field in (*EXTERNAL.values(), *FULL_ONLY.values()):
        parser.add_argument('--' + field, dest=field.replace('-', '_'),
            help='Executable external qualification command (not a status assertion)')
    args = parser.parse_args()
    if args.timeout_seconds < 1:
        parser.error('timeout must be positive')
    if args.resource_repetitions < 3:
        parser.error('resource qualification needs at least three clean-exit repetitions')
    lock = ROOT / 'uv.lock'
    version = sys.version_info
    deps = {}
    for package in ('h11', 'h2', 'hpack', 'hyperframe', 'websockets', 'websocket-client'):
        try:
            deps[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            deps[package] = None
    required_h3 = (ROOT / 'src/valiance/std/_web_http3.py').exists()
    required_external = dict(EXTERNAL)
    if args.scope == 'full':
        required_external.update(FULL_ONLY)
    report = {
        'scope': args.scope,
        'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'python': sys.version, 'python_executable': sys.executable,
        'python_3_14_required': version >= (3, 14),
        'lockfile_sha256': hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None,
        'dependencies': deps,
        'h3_source_present': required_h3,
        'required_targeted_suites': [f'tests.{name}' for name in SUITES],
        'targeted': [], 'external': {},
        'qualification_passed': False,
        'notes': 'Green targeted tests never substitute for browser/proxy/security, '
                 'independent H2, full regressions, Python 3.14 or H3 verification.',
    }
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + str(ROOT)
    # Preserve a Python/C stack on SIGSEGV rather than only an exit code -11.
    # The evidence captures subprocess output, including faulthandler traces.
    env['PYTHONFAULTHANDLER'] = '1'
    if not args.inspect:
        for suffix in SUITES:
            cmd = [sys.executable, '-m', 'unittest', f'tests.{suffix}', '-q']
            result = _run(f'tests.{suffix}', cmd, args.timeout_seconds, env,
                repeat=args.resource_repetitions if suffix == 'test_std_web_ws08_ws11_resource_gates' else 1)
            report['targeted'].append(result)
            print(f"{result['name']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
        for name, field in required_external.items():
            value = getattr(args, field.replace('-', '_'))
            if value:
                command = shlex.split(value)
                report['external'][name] = _run(name, command, args.timeout_seconds,
                    env, repeat=3 if name == 'mixed_protocol_longevity_stress' else 1)
            else:
                report['external'][name] = {
                    'name': name, 'passed': False, 'reason': 'No independent executable supplied'}
    else:
        report['external'] = {name: {'name': name, 'passed': False,
            'reason': 'Not executed (inspection only)'} for name in required_external}
    report['qualification_passed'] = bool(
        not args.inspect and version >= (3, 14) and lock.exists() and
        (args.scope != 'full' or required_h3) and
        len(report['targeted']) == len(SUITES) and
        all(item['passed'] for item in report['targeted']) and
        all(item['passed'] for item in report['external'].values()))
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(f"{args.scope} release qualification: "
          f"{'PASS' if report['qualification_passed'] else 'NOT QUALIFIED'}; "
          f"evidence: {output}", flush=True)
    return 0 if report['qualification_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
