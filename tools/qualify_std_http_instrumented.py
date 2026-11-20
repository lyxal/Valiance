"""Test the actual instrumented libcurl shared object through Valiance ctypes.

Unlike tools/validate_std_http_native.py's C probe, this verifies that the
*loaded libcurl* depends on an ASan and UBSan runtime. Then it executes
bounded Python/VM protocol tests with that library selected. OpenSSL/nghttp2
are separate dependencies and must be independently instrumented/audited.
Linux-only; not a substitute for independent external security review.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

SUITES = ('tests.test_std_http', 'tests.test_std_http_http2',
          'tests.test_std_http_security_audit', 'tests.test_std_http_dependency_policy')


def inspect_instrumentation(library: Path) -> dict:
    if not library.is_absolute() or not library.is_file():
        return {'valid': False, 'problems': ['an absolute, existing libcurl shared object is required']}
    if sys.platform != 'linux':
        return {'valid': False, 'problems': ['instrumented dependency inspection currently supports Linux only']}
    result = subprocess.run(['ldd', str(library)], text=True, capture_output=True,
                            timeout=20, check=False)
    listing = result.stdout + result.stderr
    asan = 'libasan.' in listing or 'libclang_rt.asan' in listing
    ubsan = 'libubsan.' in listing or 'libclang_rt.ubsan' in listing
    problems = []
    if result.returncode or 'not found' in listing:
        problems.append('the target libcurl cannot be dynamically resolved')
    if not asan:
        problems.append('loaded libcurl is not linked with AddressSanitizer')
    if not ubsan:
        problems.append('loaded libcurl is not linked with UndefinedBehaviorSanitizer')
    return {'valid': not problems, 'problems': problems, 'asan_linked': asan,
            'ubsan_linked': ubsan, 'dependencies': listing[-16000:]}


def run(library: Path, timeout: int) -> dict:
    record = {'library': str(library), 'platform': sys.platform,
              'sanitizer_scope': 'actual instrumented libcurl plus Valiance ctypes; TLS/nghttp2 not automatically instrumented'}
    instrumentation = inspect_instrumentation(library)
    record['instrumentation'] = instrumentation
    if not instrumentation['valid']:
        return dict(record, status='failed', suites=[])
    compiler = shutil.which('gcc')
    if not compiler:
        return dict(record, status='failed', problems=['gcc/libasan runtime lookup unavailable'], suites=[])
    path = subprocess.run([compiler, '-print-file-name=libasan.so'],
                          capture_output=True, text=True, timeout=10, check=False).stdout.strip()
    if not path or not Path(path).is_file():
        return dict(record, status='failed', problems=['libasan runtime file unavailable'], suites=[])
    root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env.pop('VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL', None)
    env['VALIANCE_LIBCURL_PATH'] = str(library)
    env['LD_PRELOAD'] = path + (':' + env['LD_PRELOAD'] if env.get('LD_PRELOAD') else '')
    env['LD_LIBRARY_PATH'] = str(library.parent) + (':' + env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1:abort_on_error=1:strict_string_checks=1'
    env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    env['PYTHONPATH'] = os.pathsep.join((str(root/'src'), str(root)))
    suites = []
    for suite in SUITES:
        command = [sys.executable, '-m', 'unittest', '-q', suite]
        try:
            result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    text=True, timeout=timeout, check=False)
            suites.append({'suite':suite, 'status':'passed' if result.returncode == 0 else 'failed',
                           'exit_code':result.returncode, 'output_tail':(result.stdout+result.stderr)[-2500:]})
        except subprocess.TimeoutExpired:
            suites.append({'suite':suite, 'status':'timeout', 'exit_code':None})
    return dict(record, status='passed' if all(s['status']=='passed' for s in suites) else 'failed',
                suites=suites, loaded_native_instrumented=True, tls_native_instrumented=False)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--report', type=Path, required=True)
    args=parser.parse_args()
    if not 1 <= args.timeout_seconds <= 3600:
        parser.error('timeout must be 1..3600 seconds')
    try:
        doc=run(args.library, args.timeout_seconds)
    except (OSError, subprocess.TimeoutExpired) as exc:
        doc={'status':'failed','problems':[f'{type(exc).__name__}: {exc}']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(doc,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':doc['status'], 'suites':doc.get('suites', []),
                      'problems':doc.get('problems', doc.get('instrumentation',{}).get('problems',[]))},indent=2))
    return 0 if doc['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
