"""Bounded, fail-closed outbound HTTP qualification on the current platform.

A separate interpreter runs each suite, so a stalled local test server or VM
cannot hold the entire campaign open indefinitely. Every timeout kills the
process group (including child processes on supported hosts). The machine-
readable report records *completed* evidence, never assumes a timeout passed.

This is one-platform evidence, NOT an assertion of cross-platform qualification.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys
from time import monotonic

SUITES = (
    'tests.test_std_http',
    'tests.test_std_http_http2',
    'tests.test_std_http_http2_adversarial',
    'tests.test_std_http_http2_fuzz',
    'tests.test_std_http_http2_soak',
    'tests.test_std_http_adversarial',
    'tests.test_std_http_security_audit',
    'tests.test_std_http_protocol_fuzz',
    'tests.test_std_file_integration',
    'tests.test_concurrency_execution',
    'tests.test_concurrency_vm_suspension',
    'tests.test_programs',
    'tests.test_std_web',
    'tests.test_http_qualification',
    'tests.test_std_http_validation_gate',
    'tests.test_std_http_packaging',
    'tests.test_std_http_dependency_policy',
    'tests.test_std_http_stable_gate',
    'tests.test_std_http_archive',
    'tests.test_std_http_instrumented_policy',
    'tests.test_std_http_reference_examples',
)


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    """Stop a timed-out test subprocess and descendants, on all supported hosts."""
    # The group must be signalled even if its leader has already exited:
    # descendants can inherit the captured output pipe and leave communicate()
    # waiting forever for EOF after the parent has terminated.
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/PID', str(process.pid), '/T', '/F'],
                capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def _suite_result(returncode: int, output: str, timed_out: bool) -> tuple[str, int, int]:
    """Accept a suite only when unittest actually executed all its tests."""
    match = re.search(r'Ran (\d+) tests? in ', output)
    tests = int(match.group(1)) if match else 0
    skipped = sum(int(n) for n in re.findall(r'\bskipped=(\d+)', output))
    if timed_out:
        return 'timeout', tests, skipped
    if returncode or tests == 0 or skipped:
        return 'failed', tests, skipped
    return 'passed', tests, skipped


def _run_suite(name: str, timeout: float, project: Path) -> dict[str, object]:
    command = [sys.executable, '-m', 'unittest', '-q', name]
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(
        (str(project / 'src'), str(project / 'tests'), str(project),
         env.get('PYTHONPATH', ''))
    )
    kwargs = (
        {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == 'nt' else {'start_new_session': True}
    )
    started = monotonic()
    process = subprocess.Popen(
        command, cwd=project, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors='replace', **kwargs,
    )
    timed_out = False
    output = ''
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_tree(process)
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Fail closed. Even a host-specific failure to reap a descendant
            # must not silently count as a passing suite.
            output = 'Unable to drain captured output after terminating test group.'
    outcome, tests, skipped = _suite_result(process.returncode, output, timed_out)
    return {
        'suite': name,
        'outcome': outcome,
        'tests_run': tests,
        'skipped': skipped,
        'exit_code': process.returncode,
        'duration_seconds': round(monotonic() - started, 3),
        'timeout_seconds': timeout,
        'command': command,
        'output_tail': output[-12000:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', action='append', choices=SUITES,
                        help='Run selected suites; default is every qualified suite')
    parser.add_argument('--timeout-seconds', type=float, default=240,
                        help='Per-suite hard timeout (default 240)')
    parser.add_argument('--report', type=Path, default=Path('std-http-qualification.json'))
    parser.add_argument('--allow-unsupported-python', action='store_true',
                        help='Development only: do not fail solely for Python <3.14')
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 3600:
        parser.error('--timeout-seconds must be between 1 and 3600')

    project = Path(__file__).resolve().parent.parent
    supported_python = sys.version_info >= (3, 14)
    suites = tuple(dict.fromkeys(args.suite or SUITES))
    results = []
    for suite in suites:
        print(f'[std.http] {suite}', flush=True)
        result = _run_suite(suite, args.timeout_seconds, project)
        results.append(result)
        print(f"  {result['outcome']} ({result['duration_seconds']}s)", flush=True)

    all_pass = all(item['outcome'] == 'passed' for item in results)
    local_gate = all_pass and (supported_python or args.allow_unsupported_python)
    env_override = os.environ.get('VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL') == '1'
    evidence = {
        'schema': 1,
        'qualification_scope': 'current platform only; not stable-release signoff',
        'platform': platform.platform(),
        'python': platform.python_version(),
        'python_314_or_later': supported_python,
        'unsupported_python_override': args.allow_unsupported_python,
        'unsafe_old_libcurl_override': env_override,
        'native_dependency_production_qualified': not env_override,
        'all_suites_passed': all_pass,
        'local_gate_passed': local_gate,
        'cross_platform_qualified': False,
        'suites': results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(f'[std.http] report: {args.report} (local gate: {local_gate})', flush=True)
    return 0 if local_gate else 1


if __name__ == '__main__':
    raise SystemExit(main())
