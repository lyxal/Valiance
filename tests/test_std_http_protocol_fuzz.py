"""Reproducible property checks around the independent raw-wire fuzzer."""
from __future__ import annotations

import unittest

from tools.fuzz_std_http_protocol import campaign, make_cases


class ProtocolFuzzRegression(unittest.TestCase):
    def test_corpus_is_deterministic_and_contains_each_attack_family(self):
        left = make_cases(20261120, 90)
        right = make_cases(20261120, 90)
        self.assertEqual(left, right)
        self.assertEqual(len({case.name for case in left}), 9)
        self.assertEqual(len(left), 90)
        self.assertTrue(any(case.name == 'valid_http_error'
                            and case.expected == 'success' for case in left))

    def test_independent_raw_peer_corpus_and_resource_reclamation(self):
        for seed in (20261120, 61743):
            with self.subTest(seed=seed):
                result = campaign(seed, 360)
                self.assertEqual(result['status'], 'passed', result['failures'])
                self.assertEqual(result['cases_completed'], 360)
                self.assertEqual(result['response_budget_remaining'], 0)
                self.assertEqual(result['upload_budget_remaining'], 0)


if __name__ == '__main__':
    unittest.main()
