"""v26: bounded HTTP/2 shutdown and per-stream idle/deadline supervision.

All timing tests use live TLS/ALPN sockets and short configured watchdogs. A
blocked application callback is intentionally *not* claimed to be killable.
"""
from __future__ import annotations

import socket
import threading
import time
import unittest

import h2.errors
import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
import tests.test_std_web_v22_http2 as helpers


class HTTP2DeadlineTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start
    client = helpers.HTTP2TLSTests.client
    exchange = helpers.HTTP2TLSTests.exchange
    headers = staticmethod(helpers.HTTP2TLSTests.headers)

    def events_until(self, sock, conn, accept, timeout=3):
        received = []
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                sock.settimeout(max(.05, end - time.monotonic()))
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            for event in conn.receive_data(data):
                received.append(event)
                if isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            wire = conn.data_to_send()
            if wire:
                sock.sendall(wire)
            if accept(received):
                break
        return received

    @staticmethod
    def reset_for(events, sid):
        return [e for e in events if isinstance(e, h2.events.StreamReset)
                and e.stream_id == sid]

    def test_stalled_inbound_body_resets_only_its_stream(self):
        app = core.App().route('POST', '/body', lambda r: str(len(r.body)))
        app = app.route('GET', '/ok', lambda r: 'works')
        limits = http1.Limits(body_idle_timeout_seconds=.18,
                              write_idle_timeout_seconds=3)
        server = self.start(app, limits)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/body', 'POST'))
        conn.send_data(1, b'stalled')
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.events_until(sock, conn,
            lambda e: bool(self.reset_for(e, 1)) and
            any(isinstance(x, h2.events.StreamEnded) and x.stream_id == 3 for x in e))
        self.assertEqual([e.error_code for e in self.reset_for(events, 1)],
                         [h2.errors.ErrorCodes.CANCEL])
        self.assertTrue(any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 3
                            for e in events))
        conn.send_headers(5, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertEqual(self.exchange(sock, conn, (5,))[0][5], b'works')

    def test_body_progress_refreshes_idle_watchdog(self):
        limits = http1.Limits(body_idle_timeout_seconds=.28)
        server = self.start(core.App().route('POST', '/len', lambda r: str(len(r.body))), limits)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/len', 'POST'))
        sock.sendall(conn.data_to_send())
        for index in range(3):
            time.sleep(.09)
            conn.send_data(1, b'ab', end_stream=index == 2)
            sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bodies[1], b'6')
        self.assertFalse(self.reset_for(events, 1))

    def test_stalled_handler_cancelled_without_killing_sibling(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def handler(req):
            if req.path == '/slow':
                entered.set()
                release.wait(5)
            return req.path
        app = core.App().route('GET', '/slow', handler).route('GET', '/fast', handler)
        server = self.start(app, http1.Limits(write_idle_timeout_seconds=.24,
                                              max_concurrent_requests=2))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/slow'), end_stream=True)
        conn.send_headers(3, self.headers('/fast'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertTrue(entered.wait(2))
        events = self.events_until(sock, conn,
            lambda e: bool(self.reset_for(e, 1)) and
            any(isinstance(x, h2.events.StreamEnded) and x.stream_id == 3 for x in e))
        self.assertEqual([e.error_code for e in self.reset_for(events, 1)],
                         [h2.errors.ErrorCodes.CANCEL])
        self.assertTrue(any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 3
                            for e in events))
        # The cancelled callback is still running and owns its admission slot.
        self.assertEqual(server._server._active_requests, 1)
        release.set()
        until = time.monotonic() + 2
        while server._server._active_requests and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual(server._server._active_requests, 0)

    def test_blocked_stream_producer_is_reset_but_other_stream_survives(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        class Producer:
            def read(self, maximum):
                entered.set()
                release.wait(5)
                return None
            def close(self):
                closed.set()
        def handle(req):
            if req.path == '/blocking':
                return core.Response(200, core.stream(Producer()))
            return 'fine'
        app = core.App().route('GET', '/blocking', handle).route('GET', '/ok', handle)
        server = self.start(app, http1.Limits(write_idle_timeout_seconds=.23))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/blocking'), end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertTrue(entered.wait(2))
        events = self.events_until(sock, conn,
            lambda e: bool(self.reset_for(e, 1)) and
            any(isinstance(x, h2.events.StreamEnded) and x.stream_id == 3 for x in e))
        self.assertEqual([e.error_code for e in self.reset_for(events, 1)],
                         [h2.errors.ErrorCodes.CANCEL])
        self.assertTrue(any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 3
                            for e in events))
        release.set()
        self.assertTrue(closed.wait(2))

    def test_forced_shutdown_does_not_join_uncooperative_callback(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def handler(req):
            entered.set()
            release.wait(5)
            return 'eventually'
        server = self.start(core.App().route('GET', '/slow', handler),
                            http1.Limits(write_idle_timeout_seconds=10))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/slow'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertTrue(entered.wait(2))
        start = time.monotonic()
        server.shutdown(.12)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.6, 'forced listener shutdown must not join a blocked app callback')
        self.assertEqual(server._server._active_requests, 1,
                         'running callback must retain admission until it exits')
        release.set()
        until = time.monotonic() + 2
        while server._server._active_requests and time.monotonic() < until:
            time.sleep(.01)
        self.assertEqual(server._server._active_requests, 0,
                         'late callback completion must release lease after socket unregistration')

    def test_streaming_body_reader_is_woken_on_idle_reset(self):
        entered, woke = threading.Event(), threading.Event()
        failures = []
        def handler(req):
            incoming = core.body_stream(req)
            entered.set()
            try:
                incoming.read(8)
            except core.WebError as error:
                failures.append(error.name)
            finally:
                woke.set()
            return 'not delivered'
        app = core.App().route('POST', '/upload', handler).with_body_mode('POST', '/upload', 'Stream')
        app = app.route('GET', '/ok', lambda req: 'ready')
        server = self.start(app, http1.Limits(body_idle_timeout_seconds=.18))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/upload', 'POST'))
        sock.sendall(conn.data_to_send())
        self.assertTrue(entered.wait(2))
        events = self.events_until(sock, conn, lambda e: bool(self.reset_for(e, 1)))
        self.assertEqual([e.error_code for e in self.reset_for(events, 1)],
                         [h2.errors.ErrorCodes.CANCEL])
        self.assertTrue(woke.wait(2))
        self.assertEqual(failures, ['BodyUnavailable'])
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertEqual(self.exchange(sock, conn, (3,))[0][3], b'ready')

    def test_pending_callback_is_cancelled_without_running_after_shutdown(self):
        first_entered, first_release, queued_entered = (threading.Event(),
                                                         threading.Event(), threading.Event())
        self.addCleanup(first_release.set)
        # Exercise cancellation of a *pending* callback directly; the live
        # shutdown test above exercises an already-running callback.
        from valiance.std._web_http2 import _DaemonTaskPool
        pool = _DaemonTaskPool(1, 2)
        pool.submit(lambda _: (first_entered.set(), first_release.wait(5)), object())
        self.assertTrue(first_entered.wait(2))
        pool.submit(lambda _: queued_entered.set(), object())
        abandoned = pool.shutdown()
        self.assertEqual(len(abandoned), 1)
        self.assertFalse(queued_entered.is_set())
        first_release.set()

    def test_connection_idle_does_not_drop_active_response(self):
        def delayed(req):
            time.sleep(.24)
            return 'complete'
        server = self.start(core.App().route('GET', '/delayed', delayed),
                            http1.Limits(connection_idle_timeout_seconds=.10,
                                         write_idle_timeout_seconds=.5))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/delayed'), end_stream=True)
        sock.sendall(conn.data_to_send())
        body, _ = self.exchange(sock, conn, (1,))
        self.assertEqual(body[1], b'complete')

    def test_idle_timer_config_validation(self):
        for name in ('body_idle_timeout_seconds', 'write_idle_timeout_seconds'):
            for val in (0, -1, True, 90000):
                with self.subTest(name=name, val=val), self.assertRaises(core.ConfigError):
                    http1.Limits(**{name: val})


if __name__ == '__main__':
    unittest.main()
