"""Prevent false qualification from missing or fabricated partial evidence."""
from __future__ import annotations
import copy
import unittest

from tools.check_std_http_release import REQUIRED_SUITES, evaluate


def specimen():
    return {
        'qualification': {
            'all_suites_passed': True, 'local_gate_passed': True,
            'native_dependency_production_qualified': True, 'unsafe_old_libcurl_override': False,
            'suites': [{'suite': name, 'outcome': 'passed', 'tests_run': 5,
                        'skipped': 0, 'exit_code': 0} for name in sorted(REQUIRED_SUITES)],
        },
        'http1_fuzz': {'status': 'passed', 'cases_completed': 1800, 'failures': []},
        'http2_fuzz': {'status': 'passed', 'count_attempted': 200, 'problems': [],
                       'cases_by_type': {str(n): 10 for n in range(20)}},
        'http2_soak': {'status': 'passed', 'requests_submitted': 2000,
                        'completed_body_reads': 1600, 'http2_binary_uploads_submitted': 200,
                        'independent_peer_connections_observed': 1, 'problems': []},
        'native_asan': {'status': 'passed', 'sanitizer_requested': True, 'returncode': 0,
                        'native_results': {'failures': 0, 'successful': 100}},
        'native_dependency': {'outcome': 'passed', 'secure_libcurl_baseline_met': True, 'unsafe_old_libcurl_override': False, 'ssl_feature': True,
                              'http2_feature': True, 'resolved_binary_sha256': 'a' * 64},
        'packaging': {'status': 'passed', 'loaded_libcurl': {
            'sha256': 'a' * 64, 'secure_libcurl_baseline_met': True, 'ssl_enabled': True, 'http2_enabled': True}, 'issues': []},
        'python_coverage': {'totals': {'percent_covered': 86,
                         'percent_branches_covered': 78},
                         'files': {'src/valiance/std/_http_transport.py':
                                   {'summary': {'percent_covered': 88}}}},
        'resource_profile': {'status': 'passed', 'requests_after_warmup': 810, 'issues': []},
        'soak': {'status': 'passed', 'configured_duration_seconds': 90,
                 'completed_body_reads': 1200, 'peak_http_reactor_threads': 1, 'issues': []},
    }


class ReleaseEvidenceTests(unittest.TestCase):
    def test_complete_local_evidence_passes(self):
        self.assertEqual(evaluate(specimen())['status'], 'passed')

    def test_missing_suite_is_rejected(self):
        data = specimen()
        data['qualification']['suites'].pop()
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_skipped_tests_are_rejected(self):
        data = specimen()
        data['qualification']['suites'][0]['skipped'] = 1
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_missing_sanitizer_is_rejected(self):
        data = specimen()
        data['native_asan']['sanitizer_requested'] = False
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_missing_fuzz_families_and_short_soak_rejected(self):
        data = specimen()
        data['http2_fuzz']['cases_by_type'].clear()
        data['soak']['configured_duration_seconds'] = 12
        result = evaluate(data)
        self.assertEqual(result['status'], 'failed')
        self.assertGreaterEqual(len(result['problems']), 2)


class DependencyGateTests(unittest.TestCase):
    def test_bypassed_curl_is_never_qualified(self):
        data = specimen()
        data['qualification']['unsafe_old_libcurl_override'] = True
        self.assertEqual(evaluate(data)['status'], 'failed')

    def test_libcurl_version_security_baseline_is_mandatory(self):
        for record, key in (('native_dependency', 'secure_libcurl_baseline_met'),
                            ('packaging', 'secure_libcurl_baseline_met')):
            data = specimen()
            if record == 'packaging':
                data['packaging']['loaded_libcurl'][key] = False
            else:
                data[record][key] = False
            self.assertEqual(evaluate(data)['status'], 'failed')
