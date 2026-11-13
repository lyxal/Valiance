"""HTTP/1.1 resource budgets and draining lifecycle, including the public VM bridge.

This tests the implemented HTTP/1.1 subset, not multi-protocol transport
cancellation or cancellation of arbitrary non-cooperative handler code.
"""
from __future__ import annotations

import http.client
import socket
import threading
import time
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core
from valiance.std import _web_http1 as http1
from valiance.std import web as bridge


class ResourceLimitsTests(unittest.TestCase):
    def open_server(self, *, limits=None, handler=None):
        app = core.App().route('GET', '/', handler or (lambda req: 'ready'))
        app = app.route('POST', '/upload', lambda req: str(len(req.body)))
        server = http1.serve(app, port=0, limits=limits)
        self.addCleanup(server.shutdown)
        return server

    def test_reject_invalid_resource_limits_before_binding(self):
        for kwargs in ({'max_connections': 0}, {'max_buffered_body_bytes': -1},
                       {'header_timeout_seconds': 0}, {'max_header_fields': 3.5},
                       {'max_connections': True}, {'graceful_shutdown_seconds': -1},
                       {'body_idle_timeout_seconds': 100000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(core.ConfigError):
                http1.Limits(**kwargs)
        self.assertEqual(http1.Limits(graceful_shutdown_seconds=0).graceful_shutdown_seconds, 0)

    def test_live_bounded_binary_body_and_keep_alive_after_success(self):
        s = self.open_server(limits=http1.Limits(max_buffered_body_bytes=4))
        c = http.client.HTTPConnection(*s.bound_address, timeout=3)
        self.addCleanup(c.close)
        c.request('POST', '/upload', b'\x00\x80\xffx', {'Content-Type': 'application/octet-stream'})
        ok = c.getresponse()
        self.assertEqual((ok.status, ok.read()), (200, b'4'))
        c.request('POST', '/upload', b'12345', {'Content-Type': 'application/octet-stream'})
        too_big = c.getresponse()
        self.assertEqual((too_big.status, too_big.read()), (413, b'Content Too Large'))

    def test_header_target_and_header_field_budgets(self):
        for limits, target, headers, status in (
            (http1.Limits(max_request_target_bytes=4), '/long', {'Host': 'localhost'}, 414),
            (http1.Limits(max_header_fields=1), '/', {'Host': 'localhost', 'X-Other': '1'}, 431),
            (http1.Limits(max_header_bytes=96), '/', {'Host': 'localhost', 'X-Test': 'x'*100}, 431),
        ):
            with self.subTest(status=status):
                s = self.open_server(limits=limits)
                with socket.create_connection(s.bound_address, timeout=3) as sock:
                    sock.settimeout(3)
                    wire = (f'GET {target} HTTP/1.1\r\n' +
                            ''.join(f'{k}: {v}\r\n' for k, v in headers.items()) +
                            '\r\n').encode('ascii')
                    sock.sendall(wire)
                    data = sock.recv(4096)
                self.assertTrue(data.startswith(f'HTTP/1.1 {status}'.encode()), data)

    def test_header_timeout_and_maximum_connections(self):
        s = self.open_server(limits=http1.Limits(header_timeout_seconds=.15,
                                                  max_connections=1))
        hold = socket.create_connection(s.bound_address, timeout=3)
        hold.settimeout(3)
        self.addCleanup(hold.close)
        hold.sendall(b'GET / HTTP/1.1\r\nHost: local')  # unfinished headers
        # Exhausted admission closes the additional connection, never runs handler.
        with socket.create_connection(s.bound_address, timeout=3) as extra:
            extra.settimeout(1)
            self.assertEqual(extra.recv(1024), b'')
        # A slow client cannot hold the one connection indefinitely.
        self.assertEqual(hold.recv(1024), b'')

    def test_draining_closes_idle_keepalive_without_idle_timeout(self):
        s = self.open_server(limits=http1.Limits(connection_idle_timeout_seconds=30,
                                                  graceful_shutdown_seconds=1))
        with socket.create_connection(s.bound_address, timeout=3) as sock:
            sock.settimeout(3)
            sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
            buffer = b''
            while b'ready' not in buffer:
                buffer += sock.recv(4096)
            start = time.monotonic()
            s.shutdown()
            self.assertLess(time.monotonic()-start, .8)
            self.assertEqual(sock.recv(1024), b'')
            self.assertEqual(s.counts, (1, 1))
            self.assertEqual(s.cancelled, 0)
            s.shutdown()  # Python host ownership closes idempotently

    def test_graceful_drain_finishes_one_active_handler_and_rejects_new_requests(self):
        entered = threading.Event()
        release = threading.Event()
        def handler(req):
            entered.set()
            if not release.wait(3):
                return 'timed out'
            return 'finished'
        s = self.open_server(limits=http1.Limits(graceful_shutdown_seconds=2), handler=handler)
        received: list[bytes] = []
        def client():
            with socket.create_connection(s.bound_address, timeout=3) as sock:
                sock.settimeout(3)
                sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
                received.append(sock.recv(4096))
        caller = threading.Thread(target=client)
        caller.start()
        self.assertTrue(entered.wait(2))
        stopping = threading.Thread(target=s.shutdown)
        stopping.start()
        self.assertTrue(s._server.draining.wait(2))
        release.set()
        stopping.join(3)
        caller.join(3)
        self.assertFalse(stopping.is_alive())
        self.assertEqual(s.counts, (1, 1))
        self.assertEqual(s.cancelled, 0)
        self.assertIn(b'Connection: close', received[0])
        self.assertIn(b'finished', received[0])
        with self.assertRaises(OSError):
            socket.create_connection(s.bound_address, timeout=.25)

    def test_expired_grace_cancels_incomplete_body_and_reports_count(self):
        s = self.open_server(limits=http1.Limits(graceful_shutdown_seconds=.05))
        with socket.create_connection(s.bound_address, timeout=3) as sock:
            sock.settimeout(3)
            sock.sendall(b'POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\nabc')
            # The frame is admitted before its remaining bytes arrive.
            deadline = time.monotonic()+2
            while s.counts[0] < 1 and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertEqual(s.counts[0], 1)
            started = time.monotonic()
            s.shutdown()
            self.assertLess(time.monotonic()-started, 1)
            self.assertEqual(s.counts, (1, 0))
            self.assertEqual(s.cancelled, 1)
            self.assertEqual(sock.recv(1024), b'')


class ValianceServerLimitsTests(unittest.TestCase):
    @staticmethod
    def execute(source: str, *, optimized=False, serialized=False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)
        program = compile_program(typed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)

    def test_custom_limits_reach_real_listener_and_bytes_boundary(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) => "ok" end
web.serve($app, web.ServerConfig(port = 0, limits = web.Limits(maxBufferedBodyBytes = 4))) ?'''
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                owner, = self.execute(source, optimized=optimized, serialized=serialized)
                token = bridge._lease_token(owner)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        server = bridge._SERVER_LEASES[token]
                    self.assertEqual(server._server.web_limits.max_buffered_body_bytes, 4)
                    con = http.client.HTTPConnection(*server.bound_address, timeout=3)
                    try:
                        con.request('POST', '/upload', b'abcde')
                        response = con.getresponse()
                        self.assertEqual((response.status, response.read()), (413, b'Content Too Large'))
                    finally:
                        con.close()
                finally:
                    bridge._close_server_token(token)

    def test_shutdown_with_deadline_returns_real_request_counts(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) => "hello" end
$server = web.serve($app, web.ServerConfig(port = 0, limits = web.Limits(gracefulShutdownMilliseconds = 0))) ?
$addresses = web.boundAddresses($server)
$report = web.shutdownWithin($server, 0) ?
$diagnostic = $report.webSocketCallbacksTimedOut
$report'''
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                report, = self.execute(source, optimized=optimized, serialized=serialized)
                self.assertEqual(report.type_name, 'web.ServerStopReport')
                self.assertEqual(int(report.fields['accepted']), 0)
                self.assertEqual(int(report.fields['completed']), 0)
                self.assertEqual(int(report.fields['cancelled']), 0)
                self.assertGreaterEqual(float(report.fields['gracefulSeconds']), 0)
                for field in ('webSocketCancellationSignals', 'webSocketCallbacksTimedOut',
                              'webSocketOwnedTasksUnjoined', 'webSocketTransportSocketsOpen',
                              'webSocketTransportBytesRemaining'):
                    self.assertEqual(int(report.fields[field]), 0)

    def test_public_server_stats_are_borrowed_and_read_only(self):
        source = """import {std.web, std.web.App}
$app = App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
$server = web.serve($app, web.ServerConfig(port = 0)) ?
$stats = web.serverStats($server)
$read = $stats.activeConnections
$report = web.shutdownWithin($server, 0) ?
$stats"""
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                stats, = self.execute(source, optimized=optimized, serialized=serialized)
                self.assertEqual(stats.type_name, 'web.ServerStats')
                self.assertEqual(int(stats.fields['accepted']), 0)
                self.assertEqual(int(stats.fields['activeWebSockets']), 0)
                self.assertGreaterEqual(int(stats.fields['activeTransportWorkers']), 0)

    def test_invalid_limit_returns_server_error(self):
        source = '''import {std.web}
web.serve(web.App, web.ServerConfig(port = 0, limits = web.Limits(maxConnections = 0)))'''
        result, = self.execute(source)
        self.assertEqual(result.type_name, 'web.ServerError.Failed')
        self.assertIn('limits', result.fields['message'].lower())

    def test_tls_and_limits_construct_together_without_starting_a_listener(self):
        source = '''import {std.web}
web.ServerConfig(
  port = 0,
  tls = web.TLS(certificateFile = "example.pem", privateKeyFile = "example-key.pem"),
  limits = web.Limits(maxConnections = 7)
)'''
        config, = self.execute(source, serialized=True)
        self.assertEqual(config.type_name, 'web.ServerConfig')
        self.assertEqual(bridge._core_limits(config).max_connections, 7)
        tls = bridge._core_tls(config)
        self.assertEqual(tls.cert_file, 'example.pem')


if __name__ == '__main__':
    unittest.main()
