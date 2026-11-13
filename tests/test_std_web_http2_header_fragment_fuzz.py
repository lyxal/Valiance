"""Deterministic fragmentation corpus for RFC 9113 HPACK byte budgets.

This is a regression corpus, not a substitute for independent h2spec or
coverage-guided fuzzing. It exercises wire segmentation invariance, padding,
priority metadata, CONTINUATION boundaries and the exact compressed quota.
"""
from __future__ import annotations

import random
import unittest

from hpack import Encoder

from valiance.std._web_http2 import (
    _CompressedHeaderBudget, _CompressedHeaderBudgetExceeded,
)

PREFACE = b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'


def frame(frame_type: int, flags: int, stream_id: int, payload: bytes) -> bytes:
    return (len(payload).to_bytes(3, 'big') + bytes((frame_type, flags)) +
            stream_id.to_bytes(4, 'big') + payload)


class HTTP2HeaderFragmentFuzzTests(unittest.TestCase):

    def test_header_quota_rejects_midframe_with_constant_metadata_storage(self):
        """Attacker cannot use a 16-KiB field frame as a quota-scanner copy."""
        scanner = _CompressedHeaderBudget(128)
        # No HPACK decoding is performed here; this is a partial wire input.
        prefix = PREFACE + frame(4, 0, 0, b'')
        scanner.feed(prefix)
        # The HEADERS frame announces 16 KiB. Only its initial 129 octets
        # arrive; its remaining bytes must not be needed to reject the quota.
        head = (16384).to_bytes(3, 'big') + b'\x01\x04' + (1).to_bytes(4, 'big')
        with self.assertRaises(_CompressedHeaderBudgetExceeded):
            scanner.feed(head + b'x' * 129)
        self.assertGreater(scanner.frame_remaining, 0)
        self.assertLessEqual(scanner.frame_metadata_peak, 6)
        self.assertLessEqual(len(scanner.frame_prefix), 6)
        self.assertFalse(hasattr(scanner, 'frame_body'))

    def test_padded_priority_field_is_not_overcounted_during_fragmentation(self):
        """Padding and priority are excluded even before END_HEADERS arrives."""
        field = b'abc'
        priority = b'\x00\x00\x00\x00\x10'
        padded = bytes((120,)) + priority + field + b'\x00' * 120
        wire = PREFACE + frame(4, 0, 0, b'') + frame(1, 0x08 | 0x20 | 0x04, 1, padded)
        scanner = _CompressedHeaderBudget(len(field))
        for octet in wire:
            scanner.feed(bytes((octet,)))
        self.assertFalse(scanner.block_open)
        self.assertLessEqual(scanner.frame_metadata_peak, 6)
        self.assertEqual(scanner.block_bytes, 0)
        with self.assertRaises(_CompressedHeaderBudgetExceeded):
            too_small = _CompressedHeaderBudget(len(field) - 1)
            too_small.feed(wire)

    def test_randomized_valid_and_over_limit_compressed_field_fragments(self):
        seed = random.Random(0x9113_2026)
        for case in range(175):
            count = seed.randrange(1, 15)
            fields = [(':method', 'GET'), (':scheme', 'https'),
                      (':authority', 'localhost'), (':path', '/')]
            for i in range(count):
                fields.append((f'x-{i}', ''.join(seed.choices('abcdef0123456789',
                                                             k=seed.randrange(2, 110)))))
            encoded = Encoder().encode(fields)
            self.assertTrue(encoded)
            cut = seed.randrange(0, len(encoded)+1)
            padding = seed.randrange(0, 12)
            with_priority = bool(seed.getrandbits(1))
            headers_flags = 0x08 | (0x20 if with_priority else 0)
            first = (bytes((padding,)) +
                     (b'\x00\x00\x00\x00\x10' if with_priority else b'') +
                     encoded[:cut] + (b'\x00' * padding))
            data = (PREFACE + frame(4, 0, 0, b'') +
                    frame(1, headers_flags, 1, first) +
                    frame(9, 0x04, 1, encoded[cut:]))
            for limit, expected_error in ((len(encoded), False),
                                          (max(1, len(encoded)-1), len(encoded) > 1)):
                inspector = _CompressedHeaderBudget(limit)
                error = None
                pos = 0
                while pos < len(data):
                    step = seed.randrange(1, 29)
                    try:
                        inspector.feed(data[pos:pos+step])
                    except _CompressedHeaderBudgetExceeded as exc:
                        error = exc
                        break
                    pos += step
                with self.subTest(case=case, limit=limit):
                    self.assertEqual(error is not None, expected_error)
                    if not expected_error:
                        self.assertEqual(inspector.preface_remaining, 0)
                        self.assertFalse(inspector.block_open)
                        self.assertIsNone(inspector.header_section_started_at)
                        self.assertEqual(inspector.block_bytes, 0)
