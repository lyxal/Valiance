"""A local passing fuzz report is never equivalent to stable-release approval."""
from __future__ import annotations
import copy
import unittest
from tools.check_std_http_stable import evaluate


def complete():
    return {
        'native': {'libcurl_version_number': 0x081700, 'instrumented_shipping_libcurl': True,
                   'instrumented_shipping_tls': True, 'asan_ubsan_clean': True,
                   'no_native_leaks': True, 'shipping_binary_sha256': 'a'*64},
        'security_review': {'independent': True, 'approved': True, 'reviewer': 'external auditor',
                            'commit': 'abc123', 'critical_findings_open': 0,
                            'high_findings_open': 0,
                            'dns_ssrf_proxy_tls_credential_cookie_reviewed': True},
        'endurance': {'status': 'passed', 'configured_duration_seconds': 43200,
                      'elapsed_seconds': 43200, 'unexpected_errors': 0,
                      'zero_net_native_handles': True, 'bounded_memory_growth': True,
                      'http1_http2_uploads_cancellations': True},
        'platforms': {p: {'qualified': True, 'clean_install': True, 'tls_trust': True,
                          'packaging_licenses': True, 'libcurl_version_number': 0x081700}
                      for p in ('linux', 'macos', 'windows')},
        'release': {'api_frozen': True, 'rollback_tested': True, 'docs_compiled': True,
                    'local_gate_passed': True, 'source_commit': 'abc123',
                    'artifact_sha256': 'a'*64, 'unsafe_old_libcurl_override': False},
    }

class StableGateTests(unittest.TestCase):
    def test_complete_signoffs_satisfy_contract(self):
        self.assertEqual(evaluate(complete())['status'], 'passed')

    def test_unverified_signoffs_fail_closed(self):
        self.assertEqual(evaluate({})['status'], 'failed')

    def test_old_curl_cannot_pass_even_with_other_signoffs(self):
        data=complete(); data['native']['libcurl_version_number']=0x080e01
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_missing_platform_is_release_blocker(self):
        data=complete(); data['platforms'].pop('macos')
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_no_12h_soak_or_independent_review_is_release_blocker(self):
        data=complete(); data['endurance']['elapsed_seconds']=90
        data['security_review']['approved']=False
        self.assertEqual(evaluate(data)['status'], 'failed')
        self.assertGreaterEqual(len(evaluate(data)['problems']), 2)

    def test_development_override_always_fails(self):
        data=complete(); data['release']['unsafe_old_libcurl_override']=True
        self.assertEqual(evaluate(data)['status'], 'failed')
