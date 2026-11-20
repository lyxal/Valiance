"""Fail-closed stable release signoff: never confuse local CI with production approval.

The final release gate intentionally needs independent attestations and tested
shipping artifacts. An operator should supply the completed evidence directory,
not substitute a local fuzz/pass count for a third-party security assessment.
Python 3.14 platform qualification is expressly deferred to the Valiance owner.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone

REQUIRED_PLATFORMS = ('linux', 'macos', 'windows')
MIN_DURATION_SECONDS = 12 * 3600


def evaluate(doc: dict) -> dict:
    problems = []
    def check(condition, message):
        if not condition:
            problems.append(message)
    native = doc.get('native', {})
    check(native.get('libcurl_version_number', 0) >= 0x081700,
          'shipping runtime must use libcurl 8.23.0 or later')
    check(native.get('instrumented_shipping_libcurl') is True,
          'shipping libcurl itself needs sanitizer evidence')
    check(native.get('instrumented_shipping_tls') is True,
          'shipping TLS stack needs sanitizer or equivalent memory-safety evidence')
    check(native.get('asan_ubsan_clean') is True,
          'native sanitizer runs did not pass')
    check(native.get('no_native_leaks') is True,
          'native leak/lifetime verification is missing')
    check(native.get('shipping_binary_sha256') and len(native.get('shipping_binary_sha256', '')) == 64,
          'shipping binary fingerprint is missing')

    security = doc.get('security_review', {})
    check(security.get('independent') is True and security.get('approved') is True,
          'independent security review must approve the final code and binaries')
    check(bool(security.get('reviewer') and security.get('commit')),
          'security review must name a reviewer and exact source commit')
    check(security.get('critical_findings_open') == 0,
          'no critical security findings can remain open')
    check(security.get('high_findings_open') == 0,
          'no high security findings can remain open')
    check(security.get('dns_ssrf_proxy_tls_credential_cookie_reviewed') is True,
          'SSRF/DNS/proxy/TLS/redirect/credential/cookie review is incomplete')

    soak = doc.get('endurance', {})
    check(soak.get('status') == 'passed' and
          soak.get('configured_duration_seconds', 0) >= MIN_DURATION_SECONDS and
          soak.get('elapsed_seconds', 0) >= MIN_DURATION_SECONDS and
          soak.get('unexpected_errors', 1) == 0,
          '12-hour or longer measured endurance campaign has not passed')
    check(soak.get('zero_net_native_handles') is True and
          soak.get('bounded_memory_growth') is True,
          'endurance test must establish memory/handle stability')
    check(soak.get('http1_http2_uploads_cancellations') is True,
          'endurance test must include both protocols, uploads, cancellations and shutdown')

    platforms = doc.get('platforms', {})
    for name in REQUIRED_PLATFORMS:
        item = platforms.get(name, {})
        check(item.get('qualified') is True and item.get('clean_install') is True and
              item.get('tls_trust') is True and item.get('packaging_licenses') is True and
              item.get('libcurl_version_number', 0) >= 0x081700,
              f'{name}: platform installation, TLS, licenses or libcurl qualification missing')
    release = doc.get('release', {})
    check(release.get('api_frozen') is True, 'public API compatibility signoff missing')
    check(release.get('rollback_tested') is True, 'rollback has not been tested')
    check(release.get('docs_compiled') is True, 'documentation/example qualification missing')
    check(release.get('local_gate_passed') is True, 'final local gate must pass with the shipping libcurl')
    check(bool(release.get('source_commit') and release.get('artifact_sha256')),
          'final source commit and artifact digest must be recorded')
    check(not release.get('unsafe_old_libcurl_override', True),
          'development-only libcurl override is forbidden in release')
    return {'status': 'passed' if not problems else 'failed', 'problems': problems,
            'scope': 'stable production go-live evidence gate, including external attestations'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.evidence.read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        result = {'status': 'failed', 'problems': [f'unusable evidence: {exc}']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
