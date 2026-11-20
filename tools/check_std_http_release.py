"""Fail-closed evidence gate for *local* std.http validation.

Passes only if every required suite and all independent native, HTTP/1,
HTTP/2, resource, security, and soak checks produced verified JSON evidence.
A passed local gate does NOT certify third-party audits, Windows/macOS or a
native-library instrumented ASan build. Python 3.14 is user-deferred.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys

REQUIRED_SUITES = frozenset((
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
))
REQUIRED_FILES = {
    'qualification': 'qualification.json',
    'http1_fuzz': 'http1-fuzz.json',
    'http2_fuzz': 'http2-fuzz.json',
    'http2_soak': 'http2-soak.json',
    'native_asan': 'native-asan.json',
    'native_dependency': 'native-dependency.json',
    'packaging': 'packaging.json',
    'python_coverage': 'python-coverage.json',
    'resource_profile': 'resource-profile.json',
    'soak': 'long-soak.json',
}


def evaluate(reports: dict[str, dict], *, min_soak_seconds: float = 60,
             min_http1_cases: int = 1800, min_http2_cases: int = 200) -> dict:
    problems: list[str] = []

    def require(condition: object, reason: str) -> None:
        if not condition:
            problems.append(reason)

    qualification = reports.get('qualification', {})
    suites = qualification.get('suites', [])
    if not isinstance(suites, list):
        suites = []
    suite_names = {s.get('suite') for s in suites if isinstance(s, dict)}
    require(REQUIRED_SUITES <= suite_names,
            'missing required suites: ' + ', '.join(sorted(REQUIRED_SUITES - suite_names)))
    require(bool(suites) and qualification.get('all_suites_passed') is True,
            'qualification all_suites_passed is not true')
    for suite in suites:
        if isinstance(suite, dict):
            require(suite.get('outcome') == 'passed' and suite.get('tests_run', 0) > 0
                    and suite.get('skipped') == 0 and suite.get('exit_code') == 0,
                    f"incomplete or failed suite: {suite.get('suite')}")
    require(qualification.get('native_dependency_production_qualified') is True,
            'native dependency qualification cannot use an older libcurl build')
    require(not qualification.get('unsafe_old_libcurl_override', False),
            'insecure legacy libcurl bypass is forbidden in release evidence')
    require(qualification.get('local_gate_passed') is True,
            'qualification local gate not passed (Python version override required if <3.14)')

    f1 = reports.get('http1_fuzz', {})
    require(f1.get('status') == 'passed' and
            f1.get('cases_completed', 0) >= min_http1_cases and not f1.get('failures'),
            f'HTTP/1 fuzz must pass >= {min_http1_cases} completed cases')
    f2 = reports.get('http2_fuzz', {})
    require(f2.get('status') == 'passed' and
            f2.get('count_attempted', 0) >= min_http2_cases and not f2.get('problems')
            and len(f2.get('cases_by_type', {})) >= 20
            and all(v > 0 for v in f2.get('cases_by_type', {}).values()),
            f'HTTP/2 fuzz must pass >= {min_http2_cases} cases across >=20 mutation families')

    h2soak = reports.get('http2_soak', {})
    require(h2soak.get('status') == 'passed' and
            h2soak.get('requests_submitted', 0) >= 2000 and
            h2soak.get('completed_body_reads', 0) >= 1500 and
            h2soak.get('http2_binary_uploads_submitted', 0) >= 100 and
            h2soak.get('independent_peer_connections_observed', 999) <= 4 and
            not h2soak.get('problems'),
            'HTTP/2 multiplex/cancellation soak must pass >=2000 submitted requests on <=4 connections')

    native = reports.get('native_asan', {})
    require(native.get('status') == 'passed' and native.get('sanitizer_requested') is True
            and native.get('returncode') == 0 and
            native.get('native_results', {}).get('failures') == 0 and
            native.get('native_results', {}).get('successful', 0) >= 100,
            'ASan/UBSan native ABI/multi harness failed, missing or insufficient')
    dep = reports.get('native_dependency', {})
    require(dep.get('outcome') == 'passed' and dep.get('secure_libcurl_baseline_met') is True
            and dep.get('unsafe_old_libcurl_override') is False and dep.get('ssl_feature') and
            dep.get('http2_feature') and dep.get('resolved_binary_sha256'),
            'libcurl native dependency provenance/features not verified')
    packaging = reports.get('packaging', {})
    require(packaging.get('status') == 'passed' and
            packaging.get('loaded_libcurl', {}).get('secure_libcurl_baseline_met') is True and
            packaging.get('loaded_libcurl', {}).get('sha256') and
            packaging.get('loaded_libcurl', {}).get('ssl_enabled') and
            packaging.get('loaded_libcurl', {}).get('http2_enabled') and
            not packaging.get('issues'),
            'native distribution/runtime dependency audit failed or incomplete')
    coverage = reports.get('python_coverage', {})
    totals = coverage.get('totals', {})
    modules = coverage.get('files', {})
    transport = next((file.get('summary', {}) for name, file in modules.items()
                      if name.endswith('_http_transport.py')), {})
    require(totals.get('percent_covered', 0) >= 80 and
            totals.get('percent_branches_covered', 0) >= 70 and
            transport.get('percent_covered', 0) >= 80,
            'Python HTTP coverage must exceed 80% overall, 70% branches, 80% transport')

    profile = reports.get('resource_profile', {})
    require(profile.get('status') == 'passed' and
            profile.get('requests_after_warmup', 0) >= 800 and
            not profile.get('issues'),
            'resource recovery profile failed or covers <800 requests')
    soak = reports.get('soak', {})
    require(soak.get('status') == 'passed' and
            soak.get('configured_duration_seconds', 0) >= min_soak_seconds and
            soak.get('completed_body_reads', 0) >= 1000 and
            soak.get('peak_http_reactor_threads', 999) <= 2 and
            not soak.get('issues'),
            f'soak must pass >= {min_soak_seconds}s and >=1000 reads with bounded reactors')

    return {
        'schema': 1, 'status': 'passed' if not problems else 'failed',
        'qualification_level': 'single-platform instrumented local gate (NOT independent security or cross-platform certification)',
        'python_314_validation': 'deferred to user',
        'sanitizer_scope': 'probe C harness instrumented; distributed libcurl/TLS and ctypes callbacks not instrumented',
        'platform': platform.platform(), 'python': platform.python_version(),
        'required_suite_count': len(REQUIRED_SUITES),
        'observed_qualified_suites': len(suites),
        'observed_tests': sum(s.get('tests_run', 0) for s in suites if isinstance(s, dict)),
        'evidence': {name: name in reports for name in REQUIRED_FILES},
        'remaining_independent_signoffs': [
            'instrumented build of the exact shipped libcurl/OpenSSL and ctypes adapter',
            'external RFC 9113/h2spec interoperability and fuzz audit',
            'third-party DNS/proxy/SSRF and credential-security review',
            'production-network long-duration soak and platform-specific package/license/trust-store signoff',
            'Python 3.14 validation (user-deferred)',
        ],
        'problems': problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--min-soak-seconds', type=float, default=60)
    parser.add_argument('--min-http1-cases', type=int, default=1800)
    parser.add_argument('--min-http2-cases', type=int, default=200)
    args = parser.parse_args()
    evidence = {}
    missing = []
    for name, filename in REQUIRED_FILES.items():
        try:
            evidence[name] = json.loads((args.evidence_dir / filename).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            missing.append(f'{filename}: {type(exc).__name__}: {exc}')
    record = evaluate(evidence, min_soak_seconds=args.min_soak_seconds,
                      min_http1_cases=args.min_http1_cases,
                      min_http2_cases=args.min_http2_cases)
    record['problems'].extend(missing)
    if missing:
        record['status'] = 'failed'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': record['status'], 'observed_tests': record['observed_tests'],
                      'problems': record['problems']}, indent=2))
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
