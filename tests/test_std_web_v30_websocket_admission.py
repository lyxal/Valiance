"""Server-wide WebSocket session admission across H1 upgrades and H2 CONNECT.

These tests use real TCP/TLS sockets and verify that a reset stream cannot
free a WebSocket slot before its still-running callback actually exits.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import shutil

import h2.events
import h2.settings

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
import tests.test_std_web_v29_websocket_h2 as ws_h2


class WebSocketAdmissionTests(unittest.TestCase):
    pump = ws_h2.H2WebSocketWireTests.pump
    connect = ws_h2.H2WebSocketWireTests.connect
    wsheaders = staticmethod(ws_h2.H2WebSocketWireTests.wsheaders)

    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            return
        cls.certdir = tempfile.TemporaryDirectory(prefix='valiance-ws-admit-')
        cls.cert = os.path.join(cls.certdir.name, 'cert.pem')
        cls.key = os.path.join(cls.certdir.name, 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '1',
                        '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'certdir'):
            cls.certdir.cleanup()

    def start(self, app, *, tls=False):
        if tls and not shutil.which('openssl'):
            self.skipTest('openssl required')
        server = http1.serve(app, port=0, limits=http1.Limits(max_active_websockets=1),
                             tls=http1.TLSConfig(self.cert, self.key) if tls else None)
        self.addCleanup(server.shutdown, .2)
        return server

    @staticmethod
    def wait_for(predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.01)
        return predicate()

    def h1_upgrade(self, server, *, tls=False, valid=True):
        tcp = socket.create_connection(server.bound_address, timeout=3)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.set_alpn_protocols(['http/1.1'])
            tcp = ctx.wrap_socket(tcp, server_hostname='localhost')
        tcp.settimeout(3)
        self.addCleanup(tcp.close)
        key = base64.b64encode(os.urandom(16))
        request = (b'GET /ws HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: Upgrade\r\nUpgrade: websocket\r\n'
                   b'Sec-WebSocket-Version: ' + (b'13' if valid else b'12') + b'\r\n'
                   b'Sec-WebSocket-Key: ' + key + b'\r\n\r\n')
        tcp.sendall(request)
        answer = bytearray()
        while b'\r\n\r\n' not in answer:
            chunk = tcp.recv(4096)
            if not chunk:
                break
            answer.extend(chunk)
        return tcp, bytes(answer)

    def test_h1_session_cap_before_upgrade_and_releases_after_close(self):
        arrived = threading.Event()
        release = threading.Event()
        def hold(ws):
            arrived.set()
            release.wait(3)
        server = self.start(core.App().websocket('/ws', hold))
        self.addCleanup(release.set)
        first, response = self.h1_upgrade(server)
        self.assertIn(b' 101 ', response)
        self.assertTrue(arrived.wait(2))
        self.assertEqual(server._server._active_websockets, 1)
        rejected, response = self.h1_upgrade(server)
        self.assertIn(b' 503 ', response)
        self.assertEqual(server._server._active_websockets, 1)
        # An invalid handshake must not consume a session slot either.
        _, response = self.h1_upgrade(server, valid=False)
        self.assertIn(b' 400 ', response)
        self.assertEqual(server._server._active_websockets, 1)
        release.set()
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))
        _, response = self.h1_upgrade(server)
        self.assertIn(b' 101 ', response)
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_h2_and_h1_share_same_session_cap(self):
        entered = threading.Event()
        release = threading.Event()
        def hold(ws):
            entered.set()
            release.wait(3)
        server = self.start(core.App().websocket('/ws', hold)
                            .route('GET', '/ok', lambda req: 'healthy'), tls=True)
        self.addCleanup(release.set)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(event, h2.events.ResponseReceived) and event.stream_id == 1
            for event in events))
        headers = [dict(e.headers) for e in events if isinstance(e, h2.events.ResponseReceived)]
        self.assertEqual(headers[0][':status'], '200')
        self.assertTrue(entered.wait(2))
        # A different protocol on a different TLS connection must respect the
        # *same* admission gate.
        _, answer = self.h1_upgrade(server, tls=True)
        self.assertIn(b' 503 ', answer)
        # A second H2 stream is rejected without terminating its TLS connection.
        conn.send_headers(3, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(event, h2.events.ResponseReceived) and event.stream_id == 3
            for event in events))
        statuses = [dict(e.headers)[':status'] for e in events
                    if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3]
        self.assertEqual(statuses, ['503'])
        # Ordinary HTTP/2 work remains admissible while WS capacity is full.
        conn.send_headers(5, [(':method', 'GET'), (':scheme', 'https'),
                              (':path', '/ok'), (':authority', 'localhost')],
                          end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 5
            for e in events))
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 5],
                         ['200'])
        self.assertEqual(b''.join(e.data for e in events if
                                  isinstance(e, h2.events.DataReceived) and e.stream_id == 5),
                         b'healthy')
        release.set()
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))
        _, answer = self.h1_upgrade(server, tls=True)
        self.assertIn(b' 101 ', answer)

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_h2_reset_does_not_free_slot_held_by_running_callback(self):
        arrived = threading.Event()
        release = threading.Event()
        def stubborn(ws):
            arrived.set()
            release.wait(3)
        server = self.start(core.App().websocket('/ws', stubborn), tls=True)
        self.addCleanup(release.set)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1
            for e in events))
        self.assertTrue(arrived.wait(2))
        conn.reset_stream(1)
        conn.send_headers(3, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3
            for e in events))
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3],
                         ['503'])
        self.assertEqual(server._server._active_websockets, 1)
        release.set()
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))
        conn.send_headers(5, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 5
            for e in events))
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 5],
                         ['200'])
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required')
    def test_h2_session_slot_held_until_outgoing_frames_flush(self):
        def reply(ws):
            ws.send_text('queued until the client grants flow credit')
            ws.close()
        server = self.start(core.App().websocket('/ws', reply), tls=True)
        sock, conn = self.connect(server)
        # Zero stream send window. A fast handler can return, but the session
        # remains open until queued WS frames reach the remote peer.
        conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 0})
        sock.sendall(conn.data_to_send())
        self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.SettingsAcknowledged) for e in events))
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1
            for e in events))
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 1))
        conn.send_headers(3, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3
            for e in events))
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3],
                         ['503'])
        conn.increment_flow_control_window(65535, stream_id=1)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 1
            for e in events))
        self.assertTrue(any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 1
                            for e in events))
        self.assertTrue(self.wait_for(lambda: server._server._active_websockets == 0))
        conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 65535})
        sock.sendall(conn.data_to_send())
        self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.SettingsAcknowledged) for e in events))
        conn.send_headers(5, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda events: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 5
            for e in events))
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 5],
                         ['200'])

    def test_public_valiance_limit_works_in_serialized_bytecode(self):
        source = """import {std.web}
web.ServerConfig(limits = web.Limits(maxActiveWebSockets = 7))"""
        analyser = Analyser()
        tree = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        program = compile_program(tree, optimize=True)
        for bytecode in (program, loads(dumps(program))):
            config, = run(bytecode)
            self.assertEqual(bridge._core_limits(config).max_active_websockets, 7)

    def test_limit_validation(self):
        with self.assertRaises(core.ConfigError):
            http1.Limits(max_active_websockets=0)
        with self.assertRaises(core.ConfigError):
            http1.Limits(max_active_websockets=-1)
        self.assertEqual(http1.Limits().max_active_websockets, 1024)


if __name__ == '__main__':
    unittest.main()
