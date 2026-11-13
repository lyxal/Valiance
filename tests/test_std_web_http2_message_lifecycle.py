"""RFC 9113 malformed-message isolation and stream ownership across multiplexed requests."""
from __future__ import annotations

import shutil
import subprocess
import socket
import threading
import time
import unittest

import h2.errors
import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_http2 import _request_headers
import tests.test_std_web_v22_http2 as h2_helpers


class HTTP2MessageValidationTests(unittest.TestCase):
    def test_invalid_content_length_is_malformed(self):
        base = [(b':method', b'POST'), (b':scheme', b'https'),
                (b':authority', b'localhost'), (b':path', b'/ok')]
        for length in (b'-1', b'+1', b'1.0', b'1, 1', b'one', b' 1'):
            with self.subTest(length=length[:20]), self.assertRaises(core.WebError):
                _request_headers(base + [(b'content-length', length)], http1.Limits())


    def test_socket_free_head_remains_eagerly_closed_by_default(self):
        closed = []

        class Source:
            def read(self, maximum):
                raise AssertionError('HEAD source must not be consumed')

            def close(self):
                closed.append(True)

        app = core.App().route('HEAD', '/head',
                               lambda r: core.Response(body=core.StreamBody(Source(), length=9)))
        result = core.dispatch(app, core.test_request('HEAD', '/head'))
        self.assertEqual(result.headers.one('content-length'), '9')
        self.assertIsNone(result.body)
        self.assertEqual(closed, [True])
        deferred = core.dispatch(app, core.test_request('HEAD', '/head'),
                                 defer_head_stream_close=True)
        self.assertIsInstance(deferred.body, core.StreamBody)
        self.assertEqual(closed, [True])
        deferred.body.close()
        self.assertEqual(closed, [True, True])


class HTTP2MessageLifecycleTests(unittest.TestCase):
    setUpClass = classmethod(h2_helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(h2_helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = h2_helpers.HTTP2TLSTests.start
    client = h2_helpers.HTTP2TLSTests.client
    exchange = h2_helpers.HTTP2TLSTests.exchange
    headers = staticmethod(h2_helpers.HTTP2TLSTests.headers)

    def _observe(self, sock, conn, *, valid_id=3, invalid_id=1, timeout=5):
        reset = None
        good = bytearray()
        ended = False
        term = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and (reset is None or not ended):
            sock.settimeout(min(.3, max(.05, deadline-time.monotonic())))
            try:
                received = sock.recv(65536)
            except socket.timeout:
                continue
            if not received:
                break
            for event in conn.receive_data(received):
                if isinstance(event, h2.events.ConnectionTerminated):
                    term = event.error_code
                if isinstance(event, h2.events.StreamReset) and event.stream_id == invalid_id:
                    reset = event.error_code
                if isinstance(event, h2.events.DataReceived):
                    if event.stream_id == valid_id:
                        good.extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == valid_id:
                    ended = True
            outgoing = conn.data_to_send()
            if outgoing:
                sock.sendall(outgoing)
        self.assertIsNone(term, f'malformed request killed connection: {term}')
        self.assertEqual(reset, h2.errors.ErrorCodes.PROTOCOL_ERROR)
        self.assertTrue(ended, 'unrelated HTTP/2 request did not complete')
        self.assertEqual(bytes(good), b'ok')

    def test_invalid_content_length_headers_do_not_terminate_connection(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        for invalid in ('nonsense', '+' + '1', '9' * 4500):
            with self.subTest(invalid=invalid[:15]):
                server = self.start(app)
                sock, conn = self.client(server)
                conn.config.validate_outbound_headers = False
                conn.config.normalize_outbound_headers = False
                conn.send_headers(1, self.headers('/ok', 'POST') +
                                  [('content-length', invalid)], end_stream=True)
                conn.send_headers(3, self.headers('/ok'), end_stream=True)
                sock.sendall(conn.data_to_send())
                self._observe(sock, conn)

    def test_trailers_without_end_stream_reset_only_the_bad_stream(self):
        app = core.App().route('POST', '/ok', lambda _: 'ok').route('GET', '/ok', lambda _: 'ok')
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ok', 'POST'), end_stream=False)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        from hpack import Encoder
        from hyperframe.frame import HeadersFrame
        invalid = HeadersFrame(1, data=Encoder().encode([('x-trailer', 'value')]))
        invalid.flags.add('END_HEADERS')
        sock.sendall(conn.data_to_send() + invalid.serialize())
        self._observe(sock, conn)

    def test_streaming_handler_failure_releases_connection_flow_credit(self):
        def broken(req):
            time.sleep(.08)  # queue the upload before failing the handler
            raise RuntimeError('application error')

        def count(req):
            source = core.body_stream(req)
            total = 0
            while (data := source.read(4096)) is not None:
                total += len(data)
            return str(total)

        app = (core.App().route('POST', '/broken', broken)
               .route('POST', '/count', count)
               .with_body_mode('POST', '/broken', 'Stream')
               .with_body_mode('POST', '/count', 'Stream'))
        server = self.start(app, limits=http1.Limits(body_idle_timeout_seconds=15))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/broken', 'POST'))
        for _ in range(4):
            conn.send_data(1, b'A' * 15000)
        sock.sendall(conn.data_to_send())
        time.sleep(.15)
        # The failed callback must release 60 KiB of unread input before this
        # independent request attempts to consume more than one H2 window.
        conn.send_headers(3, self.headers('/count', 'POST'))
        sock.sendall(conn.data_to_send())
        data = b'B' * 180000
        sent = 0
        ended = False
        response = bytearray()
        deadline = time.monotonic() + 2.5
        while not ended and time.monotonic() < deadline:
            available = min(conn.local_flow_control_window(3), conn.max_outbound_frame_size,
                            len(data) - sent)
            if available > 0:
                conn.send_data(3, data[sent:sent + available],
                               end_stream=sent + available == len(data))
                sent += available
            outbound = conn.data_to_send()
            if outbound:
                sock.sendall(outbound)
            sock.settimeout(.15)
            try:
                incoming = sock.recv(65536)
            except socket.timeout:
                continue
            if not incoming:
                break
            for event in conn.receive_data(incoming):
                if isinstance(event, h2.events.DataReceived):
                    if event.stream_id == 3:
                        response.extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == 3:
                    ended = True
        self.assertEqual(sent, len(data), 'unread body leaked connection flow credit')
        self.assertTrue(ended)
        self.assertEqual(bytes(response), b'180000')

    @unittest.skipUnless(shutil.which('curl'), 'curl CLI is required for independent HTTP/2 interop')
    def test_nghttp2_curl_tls_interoperability_get_post_and_head(self):
        if 'HTTP2' not in subprocess.run(['curl', '--version'], capture_output=True,
                                          text=True, check=True).stdout:
            self.skipTest('curl lacks nghttp2 support')
        app = (core.App().route('GET', '/ok', lambda _: 'curl-h2')
               .route('HEAD', '/head', lambda _: core.Response(body=b'seven!!'))
               .route('POST', '/echo', lambda r: r.body.hex()))
        server = self.start(app)
        origin = f'https://localhost:{server.bound_address[1]}'
        for method, target, expected in (
            ('GET', '/ok', b'curl-h2'),
            ('POST', '/echo', b'41424300'),
            ('HEAD', '/head', b''),
        ):
            command = ['curl', '--http2', '--insecure', '--noproxy', '*',
                       '--silent', '--show-error', '--max-time', '6',
                       '--write-out', '\n%{http_version}', origin + target]
            if method == 'HEAD':
                command += ['--head', '--output', '/dev/null']
            else:
                command += ['--request', method]
            if method == 'POST':
                command += ['--data-binary', '@-']
            result = subprocess.run(command, input=b'ABC\x00' if method == 'POST' else b'',
                                    capture_output=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            body, version = result.stdout.rsplit(b'\n', 1)
            self.assertEqual(version, b'2', (method, result.stdout))
            self.assertEqual(body, expected, method)

    def test_invalid_streamed_response_closes_source_and_returns_safe_500(self):
        closed = threading.Event()
        reads = []

        class Source:
            def read(self, maximum):
                reads.append(maximum)
                return b'should not be sent'

            def close(self):
                closed.set()

        def invalid(req):
            # Trailer is a forbidden HTTP/2 response field even though it is
            # a syntactically valid HTTP/1.1 header. Do not transfer ownership
            # of the StreamBody if H2 response normalization will reject it.
            return core.Response(body=core.StreamBody(Source()),
                                 headers=core.Headers((('trailer', 'x-checksum'),)))

        app = (core.App().route('GET', '/invalid', invalid)
               .route('GET', '/ok', lambda _: 'ok'))
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/invalid'), end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1, 3), timeout=3)
        statuses = {event.stream_id: dict(event.headers)[':status']
                    for event in events if isinstance(event, h2.events.ResponseReceived)}
        self.assertEqual(statuses, {1: '500', 3: '200'})
        self.assertEqual(bodies[1], b'Internal Server Error')
        self.assertEqual(bodies[3], b'ok')
        self.assertTrue(closed.wait(1))
        self.assertFalse(reads)

    def test_blocked_head_source_close_does_not_block_other_streams(self):
        release = threading.Event()
        closing = threading.Event()

        class Source:
            def read(self, maximum):
                raise AssertionError('HEAD must not read response content')

            def close(self):
                closing.set()
                release.wait(3)

        app = (core.App().route('HEAD', '/head', lambda _: core.Response(body=core.StreamBody(Source(), length=7)))
               .route('GET', '/ok', lambda _: 'ok'))
        server = self.start(app, limits=http1.Limits(write_idle_timeout_seconds=2))
        sock, conn = self.client(server)
        try:
            conn.send_headers(1, self.headers('/head', 'HEAD'), end_stream=True)
            sock.sendall(conn.data_to_send())
            self.assertTrue(closing.wait(2))
            conn.send_headers(3, self.headers('/ok'), end_stream=True)
            sock.sendall(conn.data_to_send())
            observed = []
            bodies = {1: bytearray(), 3: bytearray()}
            ended = set()
            deadline = time.monotonic() + 1.7
            while ended != {1, 3} and time.monotonic() < deadline:
                sock.settimeout(.15)
                try:
                    wire = sock.recv(65536)
                except socket.timeout:
                    continue
                if not wire:
                    break
                for evt in conn.receive_data(wire):
                    observed.append((type(evt).__name__, getattr(evt, 'stream_id', None)))
                    if isinstance(evt, h2.events.DataReceived):
                        bodies[evt.stream_id].extend(evt.data)
                        conn.acknowledge_received_data(evt.flow_controlled_length, evt.stream_id)
                    elif isinstance(evt, h2.events.StreamEnded):
                        ended.add(evt.stream_id)
                outgoing = conn.data_to_send()
                if outgoing:
                    sock.sendall(outgoing)
            self.assertEqual(ended, {1, 3}, observed)
            self.assertEqual(bytes(bodies[1]), b'')
            self.assertEqual(bytes(bodies[3]), b'ok')
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
