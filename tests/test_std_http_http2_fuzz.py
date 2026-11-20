"""Bounded integration tests for independent malicious HTTP/2 corpus tool."""
from __future__ import annotations
import unittest

from tools.fuzz_std_http_http2 import campaign, selected_cases
from tests.test_std_http_http2_adversarial import MALFORMED_FRAMES


class HTTP2MutationCampaignTests(unittest.TestCase):
    def test_reproducible_and_complete_selection(self):
        left = selected_cases(77342, 100)
        self.assertEqual(left, selected_cases(77342, 100))
        self.assertNotEqual(left, selected_cases(77343, 100))
        self.assertEqual(set(left), set(MALFORMED_FRAMES))
        with self.assertRaises(ValueError):
            selected_cases(1, 1)

    def test_live_black_box_fuzz_corpus(self):
        result = campaign(772018, 60)
        self.assertEqual(result['status'], 'passed', result['problems'])
        self.assertEqual(result['count_attempted'], 60)
        self.assertTrue(all(result['cases_by_type'].values()))
        self.assertEqual(result['response_budget_remaining'], 0)
        self.assertEqual(result['upload_budget_remaining'], 0)
