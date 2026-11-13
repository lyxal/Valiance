"""Deterministic RFC 6455 frame fragmentation and failure corpus (WS-13 partial)."""
from __future__ import annotations

import os
import random
import socket
import unittest

from valiance.std import _web_core as core
from valiance.std._web_ws import WebSocket, WebSocketPolicy
from tests.test_std_web_ws08_ws11_resource_gates import Budget, masked


def owner(left, budget=None):
    return WebSocket(left, core.test_request('GET','/ws'), WebSocketPolicy(
        max_frame_bytes=2048, max_message_bytes=8192,
        heartbeat_interval_seconds=0), buffer_budget=budget)


class FrameCorpus(unittest.TestCase):
    def test_outbound_text_and_close_reason_reject_invalid_unicode_without_writing(self):
        """RFC 6455 §8.1: neither a text message nor close reason may emit UTF-8 surrogates."""
        for reason in ('\ud800', '\udfff', 'x\ud800y'):
            with self.subTest(reason=ascii(reason)):
                left, right = socket.socketpair()
                try:
                    left.settimeout(.25); right.settimeout(.25)
                    ws = owner(left)
                    with self.assertRaises(core.WebError) as failure:
                        ws.close(1000, reason)
                    self.assertEqual(failure.exception.name, 'BadRequest')
                    with self.assertRaises(core.WebError) as failure:
                        ws.send_text(reason)
                    self.assertEqual(failure.exception.name, 'BadRequest')
                    with self.assertRaises(TimeoutError):
                        right.recv(1)
                    # Invalid inputs must not poison the send/close state.
                    ws.close(1000, 'ok')
                    self.assertEqual(right.recv(6), b'\x88\x04\x03\xe8ok')
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()

    def test_randomized_utf8_split_boundaries_and_budget_reclamation(self):
        """500 reproducible masked text/binary messages with UTF-8 split at arbitrary octets."""
        for seed in range(500):
            rng = random.Random(0x6455_7692 + seed)
            text = seed % 2 == 0
            if text:
                unicode_points = ('a', '\u00e9', '\u2603', '\U0001f680', '\U0001f4a1')
                payload = ''.join(rng.choice(unicode_points) for _ in range(rng.randint(0, 50))).encode()
            else:
                payload = rng.randbytes(rng.randint(0, 200))
            lengths = []
            remaining = len(payload)
            while remaining:
                length = rng.randint(1, min(13, remaining))
                lengths.append(length)
                remaining -= length
            # An empty data frame remains valid and must produce a message.
            if not lengths:
                lengths = [0]
            left, right = socket.socketpair()
            budget = Budget(total=128*1024)
            try:
                left.settimeout(2); right.settimeout(2)
                ws = owner(left, budget)
                offset, wire = 0, bytearray()
                for index, length in enumerate(lengths):
                    wire.extend(masked((1 if text else 2) if index == 0 else 0,
                        payload[offset:offset+length], fin=index == len(lengths)-1))
                    offset += length
                    if index % 3 == 1:
                        wire.extend(masked(9, b'!'))
                right.sendall(wire)
                result = ws.receive()
                self.assertEqual(result.kind, 'Text' if text else 'Binary', seed)
                self.assertEqual(result.value, payload.decode() if text else payload, seed)
                self.assertEqual(budget.used, 0, seed)
                ws.abort(fast=True)
            finally:
                left.close(); right.close()

    def test_iana_registered_close_codes_and_invalid_ranges(self):
        """RFC 6455 §7.4 / IANA: include post-RFC assigned 1012–1014."""
        import struct
        accepted = (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010,
                    1011, 1012, 1013, 1014, 3000, 3999, 4000, 4999)
        refused = (0, 999, 1004, 1005, 1006, 1015, 1016, 2000, 2999, 5000, 65535)
        for code in accepted:
            with self.subTest(peer_code=code):
                left, right = socket.socketpair()
                try:
                    left.settimeout(2); right.settimeout(2)
                    ws = owner(left)
                    wire = struct.pack('!H', code) + b'reason'
                    right.sendall(masked(8, wire))
                    result = ws.receive()
                    self.assertEqual((result.kind, result.code, result.reason),
                                     ('Closed', code, 'reason'))
                    self.assertEqual(right.recv(2 + len(wire)),
                                     bytes((0x88, len(wire))) + wire)
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()
            # 1010 is a valid *peer* code but is client-only under RFC 6455.
            if code == 1010:
                continue
            with self.subTest(application_code=code):
                left, right = socket.socketpair()
                try:
                    left.settimeout(2); right.settimeout(2)
                    ws = owner(left)
                    ws.close(code, 'reason')
                    wire = struct.pack('!H', code) + b'reason'
                    self.assertEqual(right.recv(2 + len(wire)),
                                     bytes((0x88, len(wire))) + wire)
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()
        left, right = socket.socketpair()
        try:
            left.settimeout(2); right.settimeout(2)
            ws = owner(left)
            with self.assertRaises(core.WebError):
                ws.close(1010, 'missing extension')
            ws.close(1000)
            self.assertEqual(right.recv(4), b'\x88\x02\x03\xe8')
            ws.abort(fast=True)
        finally:
            left.close(); right.close()
        for code in refused:
            with self.subTest(peer_invalid=code):
                left, right = socket.socketpair()
                try:
                    left.settimeout(2); right.settimeout(2)
                    ws = owner(left)
                    right.sendall(masked(8, struct.pack('!H', code)))
                    with self.assertRaises(core.WebError):
                        ws.receive()
                    self.assertEqual(right.recv(4), b'\x88\x02\x03\xea')
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()

    def test_fixed_seed_fragmentation_utf8_binary_and_pong(self):
        for seed in range(80):
            rng = random.Random(seed)
            left, right = socket.socketpair()
            try:
                left.settimeout(2); right.settimeout(2)
                budget = Budget(total=128 * 1024)
                ws = owner(left, budget)
                is_text = seed % 2 == 0
                payload = (('ab\N{SNOWMAN}\N{SNOWFLAKE}' * (seed % 70 + 1)).encode('utf-8')
                           if is_text else rng.randbytes(seed * 19 + 1))
                parts = []
                offset = 0
                while offset < len(payload):
                    count = rng.randint(1, min(311, len(payload) - offset))
                    parts.append(payload[offset:offset+count])
                    offset += count
                wire = bytearray()
                for i, part in enumerate(parts):
                    wire.extend(masked((1 if is_text else 2) if i == 0 else 0,
                                       part, fin=i == len(parts) - 1))
                    if i % 4 == 0:
                        wire.extend(masked(9, b'x'))
                right.sendall(wire)
                message = ws.receive()
                self.assertEqual(message.kind, 'Text' if is_text else 'Binary')
                self.assertEqual(message.value, payload.decode('utf-8') if is_text else payload)
                self.assertEqual(budget.used, 0)
                ws.abort(fast=True)
            finally:
                left.close(); right.close()

    def test_invalid_unmasked_rsv_and_control_fragment(self):
        for wire in (b'\x81\x01x',
                     b'\x91\x80\x01\x02\x03\x04',
                     b'\x09\x80\x01\x02\x03\x04',
                     b'\x83\x80\x01\x02\x03\x04'):
            with self.subTest(wire=wire):
                left, right = socket.socketpair()
                try:
                    left.settimeout(2)
                    ws = owner(left)
                    right.sendall(wire)
                    with self.assertRaises(core.WebError):
                        ws.receive()
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()

    def test_protocol_rejections_close_with_rfc_codes_and_reclaim_bytes(self):
        """RFC 6455 §§5.2–5.5, 7.4, 8.1 on actual masked wire frames."""
        import struct
        key = b'1234'
        def raw(first, second, extended=b'', body=b''):
            return bytes((first, second)) + extended + key + body
        vectors = (
            ('unmasked', b'\x81\x00', 1002),
            ('rsv1', raw(0xc1, 0x80), 1002),
            ('rsv2', raw(0xa1, 0x80), 1002),
            ('rsv3', raw(0x91, 0x80), 1002),
            ('opcode-3', raw(0x83, 0x80), 1002),
            ('opcode-11', raw(0x8b, 0x80), 1002),
            ('orphan-continuation', masked(0, b'o'), 1002),
            ('fragmented-ping', masked(9, b'p', fin=False), 1002),
            ('fragmented-close', masked(8, b'\x03\xe8', fin=False), 1002),
            ('control-large', raw(0x89, 0xfe, struct.pack('!H', 126)), 1002),
            ('nonminimal-16', raw(0x82, 0xfe, struct.pack('!H', 125)), 1002),
            ('nonminimal-64', raw(0x82, 0xff, struct.pack('!Q', 65535)), 1002),
            ('length-high-bit', raw(0x82, 0xff, b'\x80'+b'\x00'*7), 1002),
            ('invalid-single-close-byte', masked(8, b'\x03'), 1002),
            ('forbidden-close-1005', masked(8, struct.pack('!H', 1005)), 1002),
            ('forbidden-close-1006', masked(8, struct.pack('!H', 1006)), 1002),
            ('forbidden-close-1015', masked(8, struct.pack('!H', 1015)), 1002),
            ('invalid-close-reason', masked(8, struct.pack('!H', 1000)+b'\xff'), 1007),
            ('invalid-text-utf8', masked(1, b'\xc0\x80'), 1007),
            ('oversized-frame-header', raw(0x82, 0xfe, struct.pack('!H', 4096)), 1009),
            ('nested-fragment-start', masked(1, b'first', fin=False)+masked(2,b'next'), 1002),
        )
        for label, wire, close_code in vectors:
            with self.subTest(vector=label):
                left, right = socket.socketpair()
                budget = Budget(total=512*1024)
                left.settimeout(2); right.settimeout(2)
                try:
                    ws = owner(left, budget)
                    right.sendall(wire)
                    with self.assertRaises(core.WebError) as raised:
                        ws.receive()
                    self.assertEqual(raised.exception.name, 'BadRequest')
                    # The server responds with the correct RFC close code,
                    # even when rejecting from an incomplete length header.
                    response = right.recv(4)
                    self.assertEqual(response, b'\x88\x02'+struct.pack('!H', close_code))
                    self.assertEqual(budget.used, 0)
                    self.assertLessEqual(budget.peak, budget.total)
                    ws.abort(fast=True)
                finally:
                    left.close(); right.close()

    def test_valid_empty_close_and_fragmented_utf8_split_boundaries(self):
        import struct
        for fragments in ((b'\xf0', b'\x9f', b'\x8c', b'\x8d'),
                          (b'\xe2\x98', b'\x83')):
            left, right = socket.socketpair()
            try:
                left.settimeout(2); right.settimeout(2)
                ws = owner(left)
                right.sendall(b''.join(masked(1 if index == 0 else 0, part,
                    fin=index == len(fragments)-1) for index, part in enumerate(fragments)))
                result = ws.receive()
                self.assertEqual(result.value, b''.join(fragments).decode('utf-8'))
                right.sendall(masked(8, b''))
                closed = ws.receive()
                self.assertEqual((closed.kind, closed.code, closed.reason),
                                 ('Closed', 1005, ''))
                self.assertEqual(right.recv(2), b'\x88\x00')
                ws.abort(fast=True)
            finally:
                left.close(); right.close()

    def test_fragmented_invalid_utf8_must_fail_at_message_end(self):
        left, right = socket.socketpair()
        try:
            left.settimeout(2)
            ws = owner(left)
            right.sendall(masked(1, b'\xf0\x9f', fin=False) + masked(0, b'wrong'))
            with self.assertRaises(core.WebError) as error:
                ws.receive()
            self.assertEqual(error.exception.name, 'BadRequest')
            ws.abort(fast=True)
        finally:
            left.close(); right.close()


if __name__ == '__main__': unittest.main()
