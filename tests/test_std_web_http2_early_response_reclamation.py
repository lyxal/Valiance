"""RFC 9113 §8.1: early final responses must not retain abandoned uploads.

The server is permitted to terminate the upload with RST_STREAM(NO_ERROR)
*after* finishing its final response.  The response remains usable and both
request admission and per-stream application state must be reclaimed.
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch

import h2.errors
import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import _web_http2 as adapter
import tests.test_std_web_v22_http2 as helpers


class HTTP2EarlyResponseReclamationTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start
    client = helpers.HTTP2TLSTests.client
    headers = staticmethod(helpers.HTTP2TLSTests.headers)

    def _observe(self, sock, conn, sid, timeout=4):
        body = bytearray()
        ended = False
        reset = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not (ended and reset is not None):
            sock.settimeout(max(.05, deadline - time.monotonic()))
            try:
                recv = sock.recv(65536)
            except TimeoutError:
                continue
            if not recv:
                break
            for event in conn.receive_data(recv):
                if isinstance(event, h2.events.ConnectionTerminated):
                    self.fail(f'healthy connection terminated unexpectedly: {event.error_code}')
                if isinstance(event, h2.events.DataReceived):
                    if event.stream_id == sid:
                        body.extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == sid:
                    ended = True
                if isinstance(event, h2.events.StreamReset) and event.stream_id == sid:
                    reset = event.error_code
            pending = conn.data_to_send()
            if pending:
                sock.sendall(pending)
        self.assertTrue(ended, 'final response was not completed')
        self.assertEqual(reset, h2.errors.ErrorCodes.NO_ERROR,
                         'early final response should cancel the unnecessary upload safely')
        return bytes(body)

    def test_many_early_streamed_responses_release_upload_and_state(self):
        app = (core.App().route('POST', '/ignore', lambda req: 'done')
               .route('GET', '/healthy', lambda req: 'healthy')
               .with_body_mode('POST', '/ignore', 'Stream'))
        instances = []
        original_init = adapter.HTTP2Connection.__init__
        def trace(connection, *args, **kwargs):
            original_init(connection, *args, **kwargs)
            instances.append(connection)

        with patch.object(adapter.HTTP2Connection, '__init__', trace):
            server = self.start(app, http1.Limits(max_concurrent_requests=4))
            sock, conn = self.client(server)
            until = time.monotonic() + 2
            while not instances and time.monotonic() < until:
                time.sleep(.01)
        self.assertEqual(len(instances), 1)
        for sid in range(1, 302, 2):
            conn.send_headers(sid, self.headers('/ignore', 'POST'), end_stream=False)
            conn.send_data(sid, b'ignored payload')
            sock.sendall(conn.data_to_send())
            self.assertEqual(self._observe(sock, conn, sid), b'done')
        deadline = time.monotonic() + 2
        while instances[0].states and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(instances[0].states), 0,
                         'completed early-response streams retained per-stream state')
        self.assertEqual(server._server._active_requests, 0)
        conn.send_headers(303, self.headers('/healthy'), end_stream=True)
        sock.sendall(conn.data_to_send())
        deadline = time.monotonic() + 3
        body = bytearray()
        ended = False
        while time.monotonic() < deadline and not ended:
            sock.settimeout(.2)
            try:
                recv = sock.recv(65536)
            except TimeoutError:
                continue
            for event in conn.receive_data(recv):
                if isinstance(event, h2.events.DataReceived) and event.stream_id == 303:
                    body.extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == 303:
                    ended = True
        self.assertTrue(ended)
        self.assertEqual(bytes(body), b'healthy')

    def test_pre_admission_413_is_reclaimed_after_early_reply(self):
        app = core.App().route('POST', '/short', lambda req: 'should not run')
        instances = []
        original_init = adapter.HTTP2Connection.__init__
        def trace(connection, *args, **kwargs):
            original_init(connection, *args, **kwargs)
            instances.append(connection)
        with patch.object(adapter.HTTP2Connection, '__init__', trace):
            server = self.start(app, http1.Limits(max_buffered_body_bytes=8))
            sock, conn = self.client(server)
            until = time.monotonic() + 2
            while not instances and time.monotonic() < until:
                time.sleep(.01)
        self.assertEqual(len(instances), 1)
        for sid in (1, 3, 5, 7, 9):
            conn.send_headers(sid, self.headers('/short', 'POST', **{'content-length': '100'}),
                              end_stream=False)
            sock.sendall(conn.data_to_send())
            self.assertEqual(self._observe(sock, conn, sid), b'Content Too Large')
        deadline = time.monotonic() + 2
        while instances[0].states and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(instances[0].states), 0)
        self.assertEqual(server._server._active_requests, 0)


class HTTP2IdleConnectionShutdownTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start

    def test_idle_h2_preface_wait_does_not_hold_shutdown_past_grace(self):
        """A TLS h2 client that never sends the preface holds no request slots."""
        import socket
        import ssl
        import threading
        server = self.start(core.App().route('GET', '/', lambda _: 'ok'),
                            http1.Limits(header_timeout_seconds=8,
                                         graceful_shutdown_seconds=.1))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(['h2'])
        sock = context.wrap_socket(
            socket.create_connection(server.bound_address, timeout=3),
            server_hostname='localhost')
        self.addCleanup(sock.close)
        self.assertEqual(sock.selected_alpn_protocol(), 'h2')
        deadline = time.monotonic() + 2
        while not server._server._http2_connections and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(server._server._http2_connections)
        done = __import__('threading').Event()
        def shutdown():
            server.shutdown(.15)
            done.set()
        worker = threading.Thread(target=shutdown, daemon=True)
        began = time.monotonic()
        worker.start()
        fast = done.wait(.9)
        if not fast:
            # Avoid allowing a deliberately failing regression test to keep the
            # test runner alive for the long preface/header deadline.
            server._server.cancel_connections()
        worker.join(2)
        self.assertTrue(fast, 'idle/incomplete h2 preface held server shutdown past grace')
        self.assertLess(time.monotonic()-began, 1.25)
