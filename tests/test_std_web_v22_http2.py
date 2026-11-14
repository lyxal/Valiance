"""v22: HTTP/2 TLS ALPN, HPACK, multiplexed dispatch and flow-control tests."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

import h2.config
import h2.connection
import h2.events
import h2.settings

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std._web_http2 import _request_headers


class HTTP2CoreTests(unittest.TestCase):
    def test_tls_required_for_http2_and_duplicates_invalid(self):
        app = core.App().route('GET', '/', lambda r: 'ok')
        for protocols in (('HTTP/2',), ('HTTP/1.1', 'HTTP/2')):
            with self.assertRaises(core.ConfigError):
                http1.serve(app, port=0, protocols=protocols)
        for protocols in ((), ('HTTP/1.1', 'HTTP/1.1'), ('HTTP/3',)):
            with self.assertRaises(core.ConfigError):
                http1.serve(app, port=0, protocols=protocols)

    def test_header_validation_and_decoded_limits(self):
        proper = [(b':method', b'GET'), (b':path', b'/'), (b':scheme', b'https'),
                  (b':authority', b'example.com'), (b'cookie', b'a=1')]
        method, path, fields, host = _request_headers(proper, http1.Limits())
        self.assertEqual((method, path, host), ('GET', '/', 'example.com'))
        for invalid in (
            proper + [(b'connection', b'keep-alive')],
            proper + [(b'host', b'elsewhere')],
            proper + [(b'X-Upper', b'x')],
            proper + [(b':method', b'POST')],
            proper + [(b'content-length', b'2'), (b'content-length', b'2')],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(core.WebError):
                _request_headers(invalid, http1.Limits())
        with self.assertRaises(core.WebError):
            _request_headers(proper + [(b'x-huge', b'x' * 80)],
                             http1.Limits(max_header_bytes=80))


@unittest.skipUnless(shutil.which('openssl'), 'openssl CLI required for TLS fixture')
class HTTP2TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix='valiance-h2-tls-')
        cls.cert = os.path.join(cls._tmp.name, 'cert.pem')
        cls.key = os.path.join(cls._tmp.name, 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '2',
                        '-subj', '/CN=localhost'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def start(self, app, limits=None):
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key),
                             limits=limits)
        self.addCleanup(server.shutdown, .5)
        return server

    def client(self, server, *, alpn='h2'):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols([alpn])
        s = ctx.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                            server_hostname='localhost')
        s.settimeout(4)
        self.assertEqual(s.selected_alpn_protocol(), alpn)
        h2 = h2_connection()
        h2.initiate_connection()
        s.sendall(h2.data_to_send())
        self.addCleanup(s.close)
        return s, h2

    def exchange(self, sock, conn, expected, *, timeout=5, auto_ack=True):
        bodies = {sid: bytearray() for sid in expected}
        events = []
        ended = set()
        deadline = time.monotonic() + timeout
        while ended != set(expected) and time.monotonic() < deadline:
            sock.settimeout(max(.05, deadline-time.monotonic()))
            recv = sock.recv(65536)
            if not recv:
                break
            for e in conn.receive_data(recv):
                events.append(e)
                if isinstance(e, h2.events.DataReceived):
                    bodies.setdefault(e.stream_id, bytearray()).extend(e.data)
                    if auto_ack:
                        conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                if isinstance(e, h2.events.StreamEnded):
                    ended.add(e.stream_id)
            outgoing = conn.data_to_send()
            if outgoing:
                sock.sendall(outgoing)
        self.assertEqual(ended, set(expected), [(type(e).__name__, getattr(e, 'stream_id', None)) for e in events])
        return {k: bytes(v) for k, v in bodies.items()}, events

    @staticmethod
    def headers(path='/', method='GET', **extra):
        return [(':method', method), (':scheme', 'https'), (':authority', 'localhost'),
                (':path', path)] + list(extra.items())

    def test_alpn_get_hpack_repeat_requests_and_h1_compatibility(self):
        app = core.App().route('GET', '/hi', lambda r: r.protocol + ':' + r.path)
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/hi'), end_stream=True)
        conn.send_headers(3, self.headers('/hi'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1, 3))
        self.assertEqual(bodies, {1: b'HTTP/2:/hi', 3: b'HTTP/2:/hi'})
        self.assertEqual(len([e for e in events if isinstance(e, h2.events.ResponseReceived)]), 2)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(['http/1.1'])
        with ctx.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                             server_hostname='localhost') as old:
            self.assertEqual(old.selected_alpn_protocol(), 'http/1.1')
            old.settimeout(4)
            old.sendall(b'GET /hi HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            result = old.recv(4096)
            self.assertIn(b'HTTP/1.1 200', result)

    def test_multiplexing_fast_stream_not_blocked(self):
        slow = threading.Event()
        def handler(r):
            if r.path == '/slow':
                slow.wait(1)
            return r.path
        app = core.App().route('GET', '/slow', handler).route('GET', '/fast', handler)
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/slow'), end_stream=True)
        conn.send_headers(3, self.headers('/fast'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1, 3))
        order = [e.stream_id for e in events if isinstance(e, h2.events.StreamEnded)]
        self.assertEqual(order[0], 3)
        self.assertEqual(bodies, {1: b'/slow', 3: b'/fast'})

    def test_large_streamed_request_flow_control(self):
        def handler(r):
            src = core.body_stream(r)
            count = 0
            while (piece := src.read(2048)) is not None:
                count += len(piece)
            return str(count)
        app = core.App().route('POST', '/upload', handler).with_body_mode('POST', '/upload', 'Stream')
        server = self.start(app)
        sock, conn = self.client(server)
        data = b'R' * 180000
        conn.send_headers(1, self.headers('/upload', 'POST', **{'content-length': str(len(data))}),
                          end_stream=False)
        sock.sendall(conn.data_to_send())
        sent = 0
        ended = False
        events = []
        deadline = time.monotonic() + 8
        while not ended and time.monotonic() < deadline:
            window = min(conn.local_flow_control_window(1), conn.max_outbound_frame_size)
            if sent < len(data) and window:
                n = min(window, len(data)-sent)
                conn.send_data(1, data[sent:sent+n], end_stream=sent+n==len(data))
                sent += n
                sock.sendall(conn.data_to_send())
            try:
                sock.settimeout(.15)
                received = sock.recv(65536)
            except socket.timeout:
                continue
            if not received:
                break
            for e in conn.receive_data(received):
                events.append(e)
                if isinstance(e, h2.events.DataReceived):
                    if e.data:
                        self.assertEqual(e.data, b'180000')
                    conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                if isinstance(e, h2.events.StreamEnded):
                    ended = True
            if conn.data_to_send():
                sock.sendall(conn.data_to_send())
        self.assertEqual(sent, len(data))
        self.assertTrue(ended, [type(e).__name__ for e in events])

    def test_valiance_tls_listener_normal_and_serialized_vm(self):
        source = f'''import {{std.web, std.web.App}}
$app = App
$app := get("/via-vm"): fn(req: web.GetRequest) -> String =>
  "valiance-h2"
end
web.serve($app, web.ServerConfig(port = 0,
  tls = web.TLS(certificateFile = "{Path(self.cert).as_posix()}",
                privateKeyFile = "{Path(self.key).as_posix()}"))) ?'''
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                bytecode = compile_program(program, optimize=serialized)
                handle, = run(loads(dumps(bytecode)) if serialized else bytecode)
                token = bridge._lease_token(handle)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        server = bridge._SERVER_LEASES[token]
                    sock, conn = self.client(server)
                    conn.send_headers(1, self.headers('/via-vm'), end_stream=True)
                    sock.sendall(conn.data_to_send())
                    bodies, events = self.exchange(sock, conn, (1,))
                    self.assertEqual(bodies[1], b'valiance-h2')
                finally:
                    bridge._close_server_token(token, .5)

    def test_buffered_request_limit_and_next_stream_isolation(self):
        app = core.App().route('POST', '/tiny', lambda r: str(len(r.body)))
        app = app.route('GET', '/ok', lambda r: 'survived')
        server = self.start(app, http1.Limits(max_buffered_body_bytes=8))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/tiny', 'POST', **{'content-length': '9'}),
                          end_stream=False)
        conn.send_data(1, b'123456789', end_stream=True)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1, 3))
        codes = {e.stream_id: dict(e.headers)[':status'] for e in events
                 if isinstance(e, h2.events.ResponseReceived)}
        self.assertEqual(codes[1], '413')
        self.assertEqual(codes[3], '200')
        self.assertEqual(bodies[3], b'survived')

    def test_streaming_response_early_hints_and_trailers(self):
        closed = []
        class Producer:
            def __init__(self):
                self.chunks = [b'A' * 16000, b'B' * 16000, b'C' * 15000]
            def read(self, maximum):
                if not self.chunks:
                    return None
                return self.chunks.pop(0)
            def close(self):
                closed.append(True)
        def handler(req):
            return core.Response(200,
                core.stream(Producer(), content_type='application/octet-stream',
                            length=47000),
                early_hints=('</style.css>; rel=preload',),
                trailers=core.Headers((('X-Total', '47000'),)))
        server = self.start(core.App().route('GET', '/stream', handler))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/stream'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bodies[1], b'A' * 16000 + b'B' * 16000 + b'C' * 15000)
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.InformationalResponseReceived)], ['103'])
        self.assertEqual([dict(e.headers)['x-total'] for e in events
                          if isinstance(e, h2.events.TrailersReceived)], ['47000'])
        self.assertEqual(closed, [True])

    def test_reset_cancels_only_its_stream(self):
        blocked = threading.Event()
        cancelled = threading.Event()
        def handler(req):
            blocked.set()
            try:
                core.body_stream(req).discard()
            except core.WebError:
                cancelled.set()
            return 'done'
        app = core.App().route('POST', '/wait', handler).with_body_mode('POST', '/wait', 'Stream')
        app = app.route('GET', '/ok', lambda r: 'still running')
        server = self.start(app)
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/wait', 'POST'), end_stream=False)
        sock.sendall(conn.data_to_send())
        self.assertTrue(blocked.wait(2))
        conn.reset_stream(1)
        conn.send_headers(3, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        bodies, _ = self.exchange(sock, conn, (3,))
        self.assertEqual(bodies[3], b'still running')
        self.assertTrue(cancelled.wait(2))

    def test_outgoing_flow_control_window_and_resume(self):
        payload = b'z' * 90000
        app = core.App().route('GET', '/big', lambda r: core.Response(200, payload))
        server = self.start(app)
        sock, conn = self.client(server)
        conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 1024})
        conn.send_headers(1, self.headers('/big'), end_stream=True)
        sock.sendall(conn.data_to_send())
        received = bytearray()
        until = time.monotonic() + .35
        while time.monotonic() < until:
            try:
                sock.settimeout(.05)
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            for e in conn.receive_data(chunk):
                if isinstance(e, h2.events.DataReceived):
                    received.extend(e.data)
        self.assertLessEqual(len(received), 1024)
        conn.increment_flow_control_window(200000, stream_id=1)
        sock.sendall(conn.data_to_send())
        bodies, events = self.exchange(sock, conn, (1,))
        self.assertEqual(bytes(received) + bodies[1], payload)


def h2_connection():
    return h2.connection.H2Connection(h2.config.H2Configuration(client_side=True, header_encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
