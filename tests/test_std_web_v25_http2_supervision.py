"""v25: bounded HTTP/2 admission and buffered-body accounting across connections.

Tests use independent TLS/ALPN h2 sessions and the actual listener. They
verify retry-safe refusal and recovery without assuming complete RFC coverage.
"""
from __future__ import annotations

import http.client
import threading
import time
import unittest

import h2.errors
import h2.events
import h2.settings

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
import tests.test_std_web_v22_http2 as helpers


class HTTP2SupervisionTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start
    client = helpers.HTTP2TLSTests.client
    exchange = helpers.HTTP2TLSTests.exchange
    headers = staticmethod(helpers.HTTP2TLSTests.headers)

    def _events_until(self, sock, conn, predicate, timeout=4):
        events = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(.05, deadline - time.monotonic()))
            data = sock.recv(65536)
            if not data:
                break
            events.extend(conn.receive_data(data))
            wire = conn.data_to_send()
            if wire:
                sock.sendall(wire)
            if any(predicate(e) for e in events):
                break
        return events

    def test_global_admission_refuses_then_recovers_across_tls_connections(self):
        entered = threading.Event()
        release = threading.Event()
        def handler(req):
            if req.path == '/slow':
                entered.set()
                release.wait(4)
            return req.path
        app = core.App().route('GET', '/slow', handler).route('GET', '/fast', handler)
        server = self.start(app, http1.Limits(max_concurrent_requests=1))
        self.addCleanup(release.set)
        first, conn1 = self.client(server)
        second, conn2 = self.client(server)
        conn1.send_headers(1, self.headers('/slow'), end_stream=True)
        first.sendall(conn1.data_to_send())
        self.assertTrue(entered.wait(2))
        conn2.send_headers(1, self.headers('/fast'), end_stream=True)
        second.sendall(conn2.data_to_send())
        events = self._events_until(second, conn2,
            lambda e: isinstance(e, h2.events.StreamReset) and e.stream_id == 1)
        resets = [e for e in events if isinstance(e, h2.events.StreamReset)]
        self.assertEqual([(e.stream_id, e.error_code) for e in resets],
                         [(1, h2.errors.ErrorCodes.REFUSED_STREAM)])
        self.assertEqual(server._server._active_requests, 1)
        release.set()
        bodies, _ = self.exchange(first, conn1, (1,))
        self.assertEqual(bodies[1], b'/slow')
        conn2.send_headers(3, self.headers('/fast'), end_stream=True)
        second.sendall(conn2.data_to_send())
        bodies, _ = self.exchange(second, conn2, (3,))
        self.assertEqual(bodies[3], b'/fast')
        # Completion is now accounted after TLS sendall returns, rather than
        # when the response is merely queued. The peer can observe END_STREAM
        # just before the writer thread releases its lease.
        deadline = time.monotonic() + 2
        while server._server._active_requests and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_requests, 0)
        self.assertEqual(server.counts[0], 2)

    def test_reset_cannot_free_admission_until_uncooperative_handler_exits(self):
        entered = threading.Event()
        release = threading.Event()
        def handler(req):
            if req.path == '/slow':
                entered.set()
                release.wait(4)
            return 'ok'
        app = core.App().route('GET', '/slow', handler).route('GET', '/fast', handler)
        server = self.start(app, http1.Limits(max_concurrent_requests=1))
        self.addCleanup(release.set)
        first, c1 = self.client(server)
        second, c2 = self.client(server)
        c1.send_headers(1, self.headers('/slow'), end_stream=True)
        first.sendall(c1.data_to_send())
        self.assertTrue(entered.wait(2))
        c1.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        first.sendall(c1.data_to_send())
        # Wait until the reset is received; admission must remain occupied
        # even though stream 1 has become protocol-inactive.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if server._server.cancelled_requests:
                break
            time.sleep(.02)
        self.assertEqual(server._server._active_requests, 1)
        c2.send_headers(1, self.headers('/fast'), end_stream=True)
        second.sendall(c2.data_to_send())
        events = self._events_until(second, c2,
            lambda e: isinstance(e, h2.events.StreamReset) and e.stream_id == 1)
        self.assertEqual([e.error_code for e in events
                          if isinstance(e, h2.events.StreamReset)],
                         [h2.errors.ErrorCodes.REFUSED_STREAM])
        release.set()
        deadline = time.monotonic() + 3
        while server._server._active_requests and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_requests, 0)
        c2.send_headers(3, self.headers('/fast'), end_stream=True)
        second.sendall(c2.data_to_send())
        self.assertEqual(self.exchange(second, c2, (3,))[0][3], b'ok')

    def test_http11_and_http2_share_request_admission(self):
        entered = threading.Event()
        release = threading.Event()
        def handler(req):
            if req.path == '/slow':
                entered.set()
                release.wait(4)
            return 'ready'
        app = core.App().route('GET', '/slow', handler).route('GET', '/fast', handler)
        server = self.start(app, http1.Limits(max_concurrent_requests=1))
        self.addCleanup(release.set)
        first, conn = self.client(server)
        conn.send_headers(1, self.headers('/slow'), end_stream=True)
        first.sendall(conn.data_to_send())
        self.assertTrue(entered.wait(2))
        # Direct HTTP/1.1, independent listener connection: 503, not silent close.
        client = http.client.HTTPSConnection(*server.bound_address, timeout=3,
                                             context=self._h11_context())
        self.addCleanup(client.close)
        client.request('GET', '/fast', headers={'Host': 'localhost'})
        rejected = client.getresponse()
        self.assertEqual(rejected.status, 503)
        rejected.read()
        release.set()
        self.exchange(first, conn, (1,))
        client.close()
        client = http.client.HTTPSConnection(*server.bound_address, timeout=3,
                                             context=self._h11_context())
        self.addCleanup(client.close)
        client.request('GET', '/fast', headers={'Host': 'localhost'})
        ok = client.getresponse()
        self.assertEqual((ok.status, ok.read()), (200, b'ready'))

    @staticmethod
    def _h11_context():
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(['http/1.1'])
        return ctx

    def test_total_buffered_upload_bytes_shared_and_reclaimed(self):
        app = core.App().route('POST', '/size', lambda req: str(len(req.body)))
        server = self.start(app, http1.Limits(max_total_buffered_bytes=12000))
        first, c1 = self.client(server)
        second, c2 = self.client(server)
        c1.send_headers(1, self.headers('/size', 'POST'))
        c1.send_data(1, b'A' * 8000)
        first.sendall(c1.data_to_send())
        # The first stream must reserve its body before we send the second.
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes != 8000 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, 8000)
        c2.send_headers(1, self.headers('/size', 'POST'))
        c2.send_data(1, b'B' * 8000, end_stream=True)
        second.sendall(c2.data_to_send())
        bodies, events = self.exchange(second, c2, (1,))
        code = [dict(e.headers)[':status'] for e in events
                if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1]
        self.assertEqual(code, ['413'])
        self.assertNotEqual(bodies[1], b'8000')
        # The rejected stream's 413 response now owns its own output byte
        # lease until TLS flush completes, so briefly counted bytes may be
        # 8000 (upload) + 17 (413 response). Check eventual exact release.
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes != 8000 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, 8000)
        c1.end_stream(1)
        first.sendall(c1.data_to_send())
        self.assertEqual(self.exchange(first, c1, (1,))[0][1], b'8000')
        c2.send_headers(3, self.headers('/size', 'POST'))
        c2.send_data(3, b'C' * 8000, end_stream=True)
        second.sendall(c2.data_to_send())
        self.assertEqual(self.exchange(second, c2, (3,))[0][3], b'8000')
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, 0)

    def test_reset_reclaims_global_buffer_for_other_connection(self):
        app = core.App().route('POST', '/size', lambda req: str(len(req.body)))
        server = self.start(app, http1.Limits(max_total_buffered_bytes=9000))
        first, c1 = self.client(server)
        second, c2 = self.client(server)
        c1.send_headers(1, self.headers('/size', 'POST'))
        c1.send_data(1, b'A' * 8000)
        first.sendall(c1.data_to_send())
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes != 8000 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, 8000)
        c1.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        first.sendall(c1.data_to_send())
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        c2.send_headers(1, self.headers('/size', 'POST'))
        c2.send_data(1, b'B' * 8000, end_stream=True)
        second.sendall(c2.data_to_send())
        self.assertEqual(self.exchange(second, c2, (1,))[0][1], b'8000')

    def test_advertised_stream_budget_matches_config(self):
        server = self.start(core.App().route('GET', '/', lambda req: 'ok'),
                            http1.Limits(max_concurrent_streams_per_connection=4))
        sock, conn = self.client(server)
        events = self._events_until(sock, conn,
            lambda e: (isinstance(e, h2.events.RemoteSettingsChanged)
                       and h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS in e.changed_settings))
        advertised = [e.changed_settings[h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS].new_value
                      for e in events if isinstance(e, h2.events.RemoteSettingsChanged)
                      and h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS in e.changed_settings]
        self.assertEqual(advertised[-1], 4)

    def test_new_limits_reject_invalid_values(self):
        for name in ('max_concurrent_requests', 'max_total_buffered_bytes',
                     'max_buffered_bytes_per_connection',
                     'max_concurrent_streams_per_connection'):
            for value in (0, -1, True, 1.5):
                with self.subTest(name=name, value=value), self.assertRaises(core.ConfigError):
                    http1.Limits(**{name: value})

    def test_public_valiance_constructor_exposes_new_budgets(self):
        source = '''import {std.web}
web.ServerConfig(limits = web.Limits(
  maxConcurrentRequests = 3,
  maxTotalBufferedBytes = 8192,
  maxBufferedBytesPerConnection = 4096,
  maxConcurrentStreamsPerConnection = 2))'''
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual([str(x) for x in analyser.diagnostics], [])
        code = compile_program(program, optimize=True)
        for bytecode in (code, loads(dumps(code))):
            config, = run(bytecode)
            parsed = bridge._core_limits(config)
            self.assertEqual((parsed.max_concurrent_requests,
                              parsed.max_total_buffered_bytes,
                              parsed.max_buffered_bytes_per_connection,
                              parsed.max_concurrent_streams_per_connection),
                             (3, 8192, 4096, 2))


if __name__ == '__main__':
    unittest.main()
