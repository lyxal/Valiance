"""v23: RFC 9113 stream isolation, headers, flow-credit and GOAWAY wire regressions.

These are interoperability-style tests over real TLS sockets, not a complete
RFC 9113 conformance or adversarial HPACK test suite.
"""
from __future__ import annotations

import socket
import threading
import time
import unittest

import h2.errors
import h2.events
from hyperframe.frame import Frame, GoAwayFrame

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_http2 import _request_headers, _request_trailers
import tests.test_std_web_v22_http2 as _h2_test_helpers


class HTTP2RFCHeaderTests(unittest.TestCase):
    def test_cookie_crumbs_are_combined_without_reordering_other_fields(self):
        raw = [(b':method', b'GET'), (b':scheme', b'https'),
               (b':path', b'/'), (b':authority', b'example.org'),
               (b'x-before', b'a'), (b'cookie', b'first=1'),
               (b'x-middle', b'b'), (b'cookie', b'second=2'),
               (b'x-after', b'c')]
        _, _, headers, _ = _request_headers(raw, http1.Limits())
        self.assertEqual(headers.pairs, (
            ('x-before', 'a'), ('cookie', 'first=1; second=2'),
            ('x-middle', 'b'), ('x-after', 'c')))

    def test_authority_uses_origin_normalization_and_host_fallback(self):
        base = [(b':method', b'GET'), (b':scheme', b'https'), (b':path', b'/')]
        self.assertEqual(_request_headers(base + [(b'host', b'example.org')],
                                          http1.Limits())[3], 'example.org')
        self.assertEqual(_request_headers(base + [(b':authority', b'EXAMPLE.ORG:443'),
                                                   (b'host', b'example.org')],
                                          http1.Limits())[3], 'EXAMPLE.ORG:443')
        for other in ('example.org:444', 'admin@example.org', 'example.org:invalid', 'example.org:0'):
            with self.subTest(other=other), self.assertRaises(core.WebError):
                _request_headers(base + [(b':authority', other.encode()),
                                         (b'host', b'example.org')], http1.Limits())

    def test_strict_trailer_validation(self):
        initial = core.Headers((('x-before', 'value'),))
        limits = http1.Limits()
        self.assertEqual(_request_trailers([(b'x-checksum', b'ok')], initial, limits).pairs,
                         (('x-checksum', 'ok'),))
        for invalid in ([(b':method', b'POST')], [(b'X-Upper', b'a')],
                        [(b'content-length', b'1')], [(b'x-before', b'other')],
                        [(b'x-name', b' value')], [(b'x-name', b'bad\r\nvalue')]):
            with self.subTest(invalid=invalid), self.assertRaises(core.WebError):
                _request_trailers(invalid, initial, limits)


class HTTP2RFCWireTests(unittest.TestCase):
    setUpClass = classmethod(_h2_test_helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(_h2_test_helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = _h2_test_helpers.HTTP2TLSTests.start
    client = _h2_test_helpers.HTTP2TLSTests.client
    exchange = _h2_test_helpers.HTTP2TLSTests.exchange
    headers = staticmethod(_h2_test_helpers.HTTP2TLSTests.headers)

    def _send_upload_with_windows(self, sock, conn, sid, payload, *, timeout=6):
        sent = 0
        response = bytearray()
        ended = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not ended:
            if sent < len(payload):
                available = min(conn.local_flow_control_window(sid),
                                conn.max_outbound_frame_size, len(payload) - sent)
                if available:
                    conn.send_data(sid, payload[sent:sent + available],
                                   end_stream=sent + available == len(payload))
                    sent += available
            data = conn.data_to_send()
            if data:
                sock.sendall(data)
            try:
                sock.settimeout(.15)
                raw = sock.recv(65536)
            except socket.timeout:
                continue
            if not raw:
                break
            for e in conn.receive_data(raw):
                if isinstance(e, h2.events.DataReceived):
                    if e.stream_id == sid:
                        response.extend(e.data)
                    conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                if isinstance(e, h2.events.StreamEnded) and e.stream_id == sid:
                    ended = True
        self.assertTrue(ended, f'upload stalled at {sent}/{len(payload)} bytes')
        self.assertEqual(sent, len(payload))
        return bytes(response)

    def test_unread_upload_returns_connection_credit_to_other_streams(self):
        def ignore(r):
            time.sleep(.12)  # allow 60 KiB to queue before handler abandons body
            return 'ignored'
        def count(r):
            source = core.body_stream(r)
            total = 0
            while (chunk := source.read(4096)) is not None:
                total += len(chunk)
            return str(total)
        app = (core.App().route('POST', '/ignore', ignore)
               .route('POST', '/count', count)
               .with_body_mode('POST', '/ignore', 'Stream')
               .with_body_mode('POST', '/count', 'Stream'))
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ignore', 'POST'))
        for index in range(0, 60000, 16000):
            conn.send_data(1, b'a' * min(16000, 60000 - index),
                           end_stream=index + 16000 >= 60000)
        conn.send_headers(3, self.headers('/count', 'POST'))
        sock.sendall(conn.data_to_send())
        self.assertEqual(self._send_upload_with_windows(sock, conn, 3, b'b' * 180000),
                         b'180000')

    def test_reset_upload_returns_connection_credit_to_other_streams(self):
        started = threading.Event()
        def wait(r):
            started.set()
            time.sleep(.25)
            return 'unused'
        def count(r):
            src = core.body_stream(r)
            n = 0
            while (chunk := src.read(4096)) is not None:
                n += len(chunk)
            return str(n)
        app = (core.App().route('POST', '/wait', wait)
               .route('POST', '/count', count)
               .with_body_mode('POST', '/wait', 'Stream')
               .with_body_mode('POST', '/count', 'Stream'))
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/wait', 'POST'))
        for offset in range(0, 60000, 15000):
            conn.send_data(1, b'x' * 15000)
        sock.sendall(conn.data_to_send())
        self.assertTrue(started.wait(2))
        time.sleep(.08)
        conn.reset_stream(1, error_code=h2.errors.ErrorCodes.CANCEL)
        conn.send_headers(3, self.headers('/count', 'POST'))
        sock.sendall(conn.data_to_send())
        self.assertEqual(self._send_upload_with_windows(sock, conn, 3, b'y' * 170000),
                         b'170000')

    def test_malformed_header_resets_only_its_stream(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app)
        sock, conn = self.client(server)
        conn.config.validate_outbound_headers = False
        conn.config.normalize_outbound_headers = False
        conn.send_headers(1, self.headers('/ok') + [('connection', 'close')], end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies = {}
        resets = []
        ended = set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (3 not in ended or not resets):
            sock.settimeout(.5)
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            for e in conn.receive_data(data):
                if isinstance(e, h2.events.StreamReset):
                    resets.append((e.stream_id, e.error_code))
                if isinstance(e, h2.events.DataReceived):
                    bodies[e.stream_id] = bodies.get(e.stream_id, b'') + e.data
                    conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                if isinstance(e, h2.events.StreamEnded):
                    ended.add(e.stream_id)
        self.assertIn((1, h2.errors.ErrorCodes.PROTOCOL_ERROR), resets)
        self.assertEqual(bodies.get(3), b'ok')
        self.assertIn(3, ended)

    def test_connect_and_asterisk_options_are_valid_but_not_supported(self):
        app = core.App().route('GET', '/ok', lambda _: 'works')
        server = self.start(app)
        sock, conn = self.client(server)
        conn.config.validate_outbound_headers = False
        conn.config.normalize_outbound_headers = False
        conn.send_headers(1, [(':method', 'CONNECT'), (':authority', 'example.org:443')],
                          end_stream=True)
        conn.send_headers(3, self.headers('*', 'OPTIONS'), end_stream=True)
        conn.send_headers(5, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1, 3, 5))
        statuses = {e.stream_id: dict(e.headers)[':status'] for e in events
                    if isinstance(e, h2.events.ResponseReceived)}
        self.assertEqual(statuses, {1: '501', 3: '501', 5: '200'})
        self.assertEqual(bodies[5], b'works')

    def test_mismatched_content_length_is_isolated_stream_error(self):
        app = (core.App().route('POST', '/write', lambda r: str(len(r.body)))
               .route('GET', '/ok', lambda r: 'still works'))
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/write', 'POST', **{'content-length': '5'}))
        conn.send_data(1, b'four', end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        resets = []
        result = bytearray()
        ended = False
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and (not ended or not resets):
            sock.settimeout(.2)
            try:
                raw = sock.recv(65536)
            except socket.timeout:
                continue
            if not raw:
                break
            for e in conn.receive_data(raw):
                if isinstance(e, h2.events.StreamReset):
                    resets.append((e.stream_id, e.error_code))
                elif isinstance(e, h2.events.DataReceived):
                    if e.stream_id == 3:
                        result.extend(e.data)
                    conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                elif isinstance(e, h2.events.StreamEnded) and e.stream_id == 3:
                    ended = True
        self.assertIn((1, h2.errors.ErrorCodes.PROTOCOL_ERROR), resets)
        self.assertTrue(ended)
        self.assertEqual(bytes(result), b'still works')

    def test_invalid_ping_length_sends_frame_size_error_goaway(self):
        app = core.App().route('GET', '/ok', lambda r: 'ready')
        server = self.start(app)
        sock, conn = self.client(server)
        # PING requires exactly eight bytes of opaque data, RFC 9113 §6.7.
        sock.sendall(b'\x00\x00\x07\x06\x00\x00\x00\x00\x00' + b'1234567')
        data = bytearray()
        frames = []
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                sock.settimeout(.25)
                part = sock.recv(65536)
            except socket.timeout:
                continue
            if not part:
                break
            data.extend(part)
            while len(data) >= 9:
                frame, length = Frame.parse_frame_header(memoryview(data[:9]))
                if len(data) < length + 9:
                    break
                frame.parse_body(memoryview(data[9:9 + length]))
                frames.append(frame)
                del data[:9 + length]
            if any(isinstance(f, GoAwayFrame) for f in frames):
                break
        goaway = [f for f in frames if isinstance(f, GoAwayFrame)]
        self.assertTrue(goaway, [type(f).__name__ for f in frames])
        self.assertEqual(goaway[-1].error_code, h2.errors.ErrorCodes.FRAME_SIZE_ERROR)

    def test_graceful_drain_finishes_already_admitted_stream(self):
        started = threading.Event()
        release = threading.Event()
        def handler(_):
            started.set()
            release.wait(2)
            return 'completed-during-drain'
        server = self.start(core.App().route('GET', '/slow', handler))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/slow'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertTrue(started.wait(2))
        thread = threading.Thread(target=server.shutdown, args=(3,), daemon=True)
        thread.start()
        time.sleep(.15)  # first GOAWAY should precede finishing the handler
        release.set()
        data = bytearray()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                sock.settimeout(.2)
                chunk = sock.recv(65536)
            except (OSError, socket.timeout):
                continue
            if not chunk:
                break
            data.extend(chunk)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        frames = []
        while len(data) >= 9:
            frame, length = Frame.parse_frame_header(memoryview(data[:9]))
            if len(data) < 9 + length:
                break
            frame.parse_body(memoryview(data[9:9 + length]))
            frames.append(frame)
            del data[:9 + length]
        goaway = [f.last_stream_id for f in frames if isinstance(f, GoAwayFrame)]
        self.assertEqual(goaway, [0x7fffffff, 1])
        self.assertTrue(any(getattr(frame, 'data', None) == b'completed-during-drain'
                            for frame in frames))

    def test_graceful_goaway_reports_exact_last_admitted_stream(self):
        app = core.App().route('GET', '/ok', lambda _: 'good')
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ok'), end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertEqual(self.exchange(sock, conn, (1, 3))[0], {1: b'good', 3: b'good'})
        thread = threading.Thread(target=server.shutdown, args=(3,), daemon=True)
        thread.start()
        data = bytearray()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                sock.settimeout(.2)
                chunk = sock.recv(65536)
            except (socket.timeout, OSError):
                continue
            if not chunk:
                break
            data.extend(chunk)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        frames = []
        while len(data) >= 9:
            frame, length = Frame.parse_frame_header(memoryview(data[:9]))
            if len(data) < 9 + length:
                break
            frame.parse_body(memoryview(data[9:9 + length]))
            frames.append(frame)
            del data[:9 + length]
        goaway = [frame for frame in frames if isinstance(frame, GoAwayFrame)]
        self.assertGreaterEqual(len(goaway), 2, [type(frame).__name__ for frame in frames])
        self.assertEqual(goaway[0].last_stream_id, 0x7fffffff)
        self.assertEqual(goaway[-1].last_stream_id, 3)
        self.assertTrue(all(frame.error_code == 0 for frame in goaway))


if __name__ == '__main__':
    unittest.main()
