"""Deterministic independent HTTP/2 multiplex and shutdown stress."""
from __future__ import annotations
import unittest

from tools.soak_std_http_http2 import campaign


class HTTP2SoakTests(unittest.TestCase):
    def test_multiplex_cancel_reuse_and_cleanup(self):
        result = campaign(120, 16)
        self.assertEqual(result['status'], 'passed', result['problems'])
        self.assertGreaterEqual(result['completed_body_reads'], 90)
        self.assertLessEqual(result['independent_peer_connections_observed'], 4)
        self.assertEqual(result['remaining_reactor_threads'], 0)
        self.assertGreater(result['http2_binary_uploads_submitted'], 0)
