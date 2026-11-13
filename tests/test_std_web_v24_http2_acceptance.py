"""v24: RFC 9113 HTTP/2 framing checks and bounded aggregate buffered uploads.

Real TLS sockets: deliberately malformed peers use raw hyperframe bytes rather
than relying on a cooperating h2 client to construct invalid frames.
"""
from __future__ import annotations

import socket
import time
import unittest

import h2.errors
import h2.events
from hyperframe.frame import (ContinuationFrame, Frame, GoAwayFrame, HeadersFrame,
                              PingFrame, SettingsFrame)

from valiance.std import _web_core as core, _web_http1 as http1
import tests.test_std_web_v22_http2 as h2_helpers


class HTTP2RFC9113AcceptanceTests(unittest.TestCase):
    setUpClass = classmethod(h2_helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(h2_helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = h2_helpers.HTTP2TLSTests.start
    client = h2_helpers.HTTP2TLSTests.client
    exchange = h2_helpers.HTTP2TLSTests.exchange
    headers = staticmethod(h2_helpers.HTTP2TLSTests.headers)

    def _raw_frames_until(self, sock, predicate, timeout=3):
        data = bytearray()
        frames = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(min(.3, max(.05, deadline-time.monotonic())))
            try:
                fragment = sock.recv(65536)
            except socket.timeout:
                continue
            if not fragment:
                break
            data.extend(fragment)
            while len(data) >= 9:
                frame, length = Frame.parse_frame_header(memoryview(data[:9]))
                if len(data) < 9 + length:
                    break
                frame.parse_body(memoryview(data[9:9 + length]))
                del data[:9 + length]
                frames.append(frame)
                if predicate(frame):
                    return frames
        return frames

    def _assert_goaway(self, payload, code):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app)
        sock, _ = self.client(server)
        sock.sendall(payload)
        frames = self._raw_frames_until(sock, lambda f: isinstance(f, GoAwayFrame))
        self.assertTrue(any(isinstance(f, GoAwayFrame) for f in frames),
                        [(type(f).__name__, getattr(f, 'error_code', None)) for f in frames])
        self.assertEqual(next(f.error_code for f in frames if isinstance(f, GoAwayFrame)), code)

    def test_settings_ack_with_payload_is_frame_size_error(self):
        frame = SettingsFrame(0, settings={1: 128})
        frame.flags.add('ACK')
        self._assert_goaway(frame.serialize(), h2.errors.ErrorCodes.FRAME_SIZE_ERROR)

    def test_settings_ack_error_is_preserved_across_fragmented_input(self):
        server = self.start(core.App().route('GET', '/ok', lambda _: 'ok'))
        sock, _ = self.client(server)
        frame = SettingsFrame(0, settings={1: 128})
        frame.flags.add('ACK')
        raw = frame.serialize()
        sock.sendall(raw[:8])
        sock.sendall(raw[8:11])
        sock.sendall(raw[11:])
        frames = self._raw_frames_until(sock, lambda f: isinstance(f, GoAwayFrame))
        self.assertTrue(any(isinstance(f, GoAwayFrame) and
                            f.error_code == h2.errors.ErrorCodes.FRAME_SIZE_ERROR
                            for f in frames))

    def test_oversize_settings_ack_length_fails_without_body(self):
        # A claimed 16 MiB ACK body must not be buffered waiting for bytes.
        raw_header_only = b'\xff\xff\xff\x04\x01\x00\x00\x00\x00'
        self._assert_goaway(raw_header_only, h2.errors.ErrorCodes.FRAME_SIZE_ERROR)

    def test_invalid_initial_window_setting_is_flow_control_error(self):
        self._assert_goaway(SettingsFrame(0, settings={4: 0x80000000}).serialize(),
                            h2.errors.ErrorCodes.FLOW_CONTROL_ERROR)

    def test_interleaved_ping_in_header_continuation_is_protocol_error(self):
        incomplete = HeadersFrame(1, data=b'\x82')  # indexed :method GET, no END_HEADERS
        interrupt = PingFrame(0, opaque_data=b'abcdefgh')
        self._assert_goaway(incomplete.serialize() + interrupt.serialize(),
                            h2.errors.ErrorCodes.PROTOCOL_ERROR)

    def test_continuation_precedence_over_malformed_settings_ack(self):
        # While receiving a field block, only CONTINUATION is permitted;
        # preserve PROTOCOL_ERROR even if the interleaved frame is invalid.
        headers = HeadersFrame(1, data=b'\x82')
        invalid_ack = SettingsFrame(0, settings={1: 128})
        invalid_ack.flags.add('ACK')
        self._assert_goaway(headers.serialize() + invalid_ack.serialize(),
                            h2.errors.ErrorCodes.PROTOCOL_ERROR)

    def test_invalid_hpack_index_is_compression_error(self):
        broken = HeadersFrame(1, data=b'\x80')  # illegal indexed field zero
        broken.flags.add('END_HEADERS')
        broken.flags.add('END_STREAM')
        self._assert_goaway(broken.serialize(), h2.errors.ErrorCodes.COMPRESSION_ERROR)

    def test_truncated_settings_parameter_is_frame_size_error(self):
        # SETTINGS entries are six octets, not five (RFC 9113 §6.5).
        raw = b'\x00\x00\x05\x04\x00\x00\x00\x00\x00' + b'12345'
        self._assert_goaway(raw, h2.errors.ErrorCodes.FRAME_SIZE_ERROR)

    def test_truncated_rst_stream_is_frame_size_error(self):
        # RST_STREAM requires a four-octet code (RFC 9113 §6.4).
        raw = b'\x00\x00\x03\x03\x00\x00\x00\x00\x01' + b'bad'
        self._assert_goaway(raw, h2.errors.ErrorCodes.FRAME_SIZE_ERROR)

    def test_invalid_window_update_increment_is_protocol_error(self):
        # Zero-window updates on stream zero are connection errors (§6.9).
        raw = b'\x00\x00\x04\x08\x00\x00\x00\x00\x00' + b'\x00' * 4
        self._assert_goaway(raw, h2.errors.ErrorCodes.PROTOCOL_ERROR)

    def test_hpack_dynamic_table_is_preserved_between_streams(self):
        server = self.start(core.App().route('GET', '/ok', lambda r: r.headers.one('x-dynamic') or 'missing'))
        sock, conn = self.client(server)
        # One encoder instance should use the dynamic table on stream 3.
        conn.send_headers(1, self.headers('/ok') + [('x-dynamic', 'reused-value')], end_stream=True)
        conn.send_headers(3, self.headers('/ok') + [('x-dynamic', 'reused-value')], end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, _ = self.exchange(sock, conn, (1, 3))
        self.assertEqual(bodies, {1: b'reused-value', 3: b'reused-value'})

    def test_unknown_setting_is_ignored_without_poisoning_next_request(self):
        server = self.start(core.App().route('GET', '/ok', lambda _: 'ok'))
        sock, conn = self.client(server)
        conn.update_settings({0xA0A0: 999})
        conn.send_headers(1, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bodies[1], b'ok')
        self.assertTrue(any(isinstance(e, h2.events.SettingsAcknowledged) for e in events))

    def test_fragmented_continuation_header_block_roundtrip(self):
        server = self.start(core.App().route('GET', '/ok', lambda _: 'continued'))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ok') + [('x-test', 'fragmented')],
                          end_stream=True)
        raw = conn.data_to_send()
        original, block_len = Frame.parse_frame_header(memoryview(raw[:9]))
        original.parse_body(memoryview(raw[9:9 + block_len]))
        self.assertIsInstance(original, HeadersFrame)
        self.assertIn('END_HEADERS', original.flags)
        split = max(1, len(original.data) // 2)
        first = HeadersFrame(1, data=original.data[:split])
        first.flags.add('END_STREAM')
        second = ContinuationFrame(1, data=original.data[split:])
        second.flags.add('END_HEADERS')
        sock.sendall(first.serialize() + second.serialize())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bodies[1], b'continued')
        self.assertTrue(any(isinstance(e, h2.events.ResponseReceived) for e in events))

    def test_valid_ping_ack_and_concurrent_request(self):
        server = self.start(core.App().route('GET', '/ok', lambda _: 'alive'))
        sock, conn = self.client(server)
        conn.ping(b'12345678')
        conn.send_headers(1, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        _, events = self.exchange(sock, conn, (1,))
        # Depending on arrival batching, PING_ACK can come after the reply.
        self.assertTrue(any(isinstance(e, h2.events.PingAckReceived) for e in events)
                        or self._ping_ack_after_reply(sock, conn))

    def _ping_ack_after_reply(self, sock, conn):
        sock.settimeout(.2)
        try:
            return any(isinstance(e, h2.events.PingAckReceived)
                       for e in conn.receive_data(sock.recv(65536)))
        except socket.timeout:
            return False

    def test_inflight_buffered_uploads_share_connection_budget_and_recover(self):
        app = core.App().route('POST', '/size', lambda r: str(len(r.body)))
        server = self.start(app, limits=http1.Limits(max_buffered_body_bytes=12000))
        sock, conn = self.client(server)
        # Keep stream 1's 8 KiB body incomplete while a second stream begins.
        conn.send_headers(1, self.headers('/size', 'POST'))
        conn.send_data(1, b'A' * 8000)
        conn.send_headers(3, self.headers('/size', 'POST'))
        conn.send_data(3, b'B' * 8000, end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (3,))
        statuses = {e.stream_id: dict(e.headers)[':status'] for e in events
                    if isinstance(e, h2.events.ResponseReceived)}
        self.assertEqual(statuses[3], '413')
        self.assertNotEqual(bodies[3], b'8000')
        conn.end_stream(1)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bodies[1], b'8000')
        conn.send_headers(5, self.headers('/size', 'POST'))
        conn.send_data(5, b'C' * 8000, end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (5,))
        self.assertEqual(bodies[5], b'8000')

    def test_buffered_budget_is_reclaimed_on_reset(self):
        app = core.App().route('POST', '/size', lambda r: str(len(r.body)))
        server = self.start(app, limits=http1.Limits(max_buffered_body_bytes=12000))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/size', 'POST'))
        conn.send_data(1, b'A' * 8000)
        conn.reset_stream(1, error_code=h2.errors.ErrorCodes.CANCEL)
        conn.send_headers(3, self.headers('/size', 'POST'))
        conn.send_data(3, b'B' * 8000, end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (3,))
        self.assertEqual(bodies[3], b'8000')
