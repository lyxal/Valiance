"""Automatic RFC 6455 ping/pong supervision with bounded asynchronous input.

Live HTTP/1.1 and HTTP/2 tests ensure the reader handles Pong even when the
application handler is not calling receive(). No external heartbeat service is
required and the handler still owns the sole application message consumer.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

import h2.events

from valiance.std import _web_core as core
from valiance.std import _web_http1 as http1
from valiance.std._web_ws import WebSocket, WebSocketPolicy
import tests.test_std_web_v28_websocket_h1 as ws_h1
import tests.test_std_web_v29_websocket_h2 as ws_h2


class HeartbeatH1Tests(unittest.TestCase):
    raw = ws_h1.WebSocketH1Tests.raw
    masked = staticmethod(ws_h1.WebSocketH1Tests.masked)
    frame = staticmethod(ws_h1.WebSocketH1Tests.frame)

    def start(self, handler, policy=None):
        server = http1.serve(core.App().websocket('/ws', handler, policy or
            WebSocketPolicy(heartbeat_interval_seconds=.12, pong_timeout_seconds=.25)), port=0)
        self.addCleanup(server.shutdown, .3)
        return server

    def test_auto_ping_and_pong_without_application_receive(self):
        allow_receive = threading.Event()
        finished = threading.Event()
        received = []
        def handler(ws):
            allow_receive.wait(2)
            received.append(ws.receive())
            finished.set()
        server = self.start(handler)
        self.addCleanup(allow_receive.set)
        sock, response = self.raw(server)
        self.addCleanup(sock.close)
        self.assertIn(b' 101 ', response)
        opcode, ping = self.frame(sock)
        self.assertEqual(opcode, 9)
        self.assertEqual(len(ping), 8)
        sock.sendall(self.masked(10, ping))
        sock.sendall(self.masked(1, b'hello'))
        # The asynchronous reader must accept and queue Text before the app
        # starts consuming, and not miss a matched Pong in the meantime.
        time.sleep(.1)
        allow_receive.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual([(m.kind, m.value) for m in received], [('Text', 'hello')])

    def test_unanswered_ping_closes_session_and_reclaims_admission(self):
        handler_done = threading.Event()
        failures = []
        def handler(ws):
            try:
                ws.receive()
            except core.WebError as exc:
                failures.append(exc.name)
            finally:
                handler_done.set()
        server = self.start(handler, WebSocketPolicy(heartbeat_interval_seconds=.1,
                                                    pong_timeout_seconds=.16))
        sock, response = self.raw(server)
        self.addCleanup(sock.close)
        self.assertIn(b' 101 ', response)
        opcode, ping = self.frame(sock)
        self.assertEqual(opcode, 9)
        self.assertTrue(ping)
        opcode, payload = self.frame(sock)
        self.assertEqual((opcode, payload), (8, struct.pack('!H', 1001)))
        self.assertTrue(handler_done.wait(2))
        self.assertEqual(failures, ['BodyUnavailable'])
        self.assertEqual(server._server._active_websockets, 0)

    def test_incorrect_pong_does_not_satisfy_deadline(self):
        done = threading.Event()
        def handler(ws):
            try: ws.receive()
            except core.WebError: pass
            finally: done.set()
        server = self.start(handler, WebSocketPolicy(heartbeat_interval_seconds=.1,
                                                    pong_timeout_seconds=.16))
        sock, _ = self.raw(server)
        self.addCleanup(sock.close)
        opcode, token = self.frame(sock)
        self.assertEqual(opcode, 9)
        sock.sendall(self.masked(10, b'x' * len(token)))
        self.assertEqual(self.frame(sock), (8, struct.pack('!H', 1001)))
        self.assertTrue(done.wait(2))

    def test_pending_application_messages_are_bounded(self):
        allow_receive = threading.Event()
        done = threading.Event()
        def handler(ws):
            allow_receive.wait(2)
            try: ws.receive()
            except core.WebError: pass
            finally: done.set()
        policy = WebSocketPolicy(heartbeat_interval_seconds=.1,
                                 pong_timeout_seconds=.3, max_message_bytes=8,
                                 max_frame_bytes=8)
        server = self.start(handler, policy)
        self.addCleanup(allow_receive.set)
        sock, _ = self.raw(server)
        self.addCleanup(sock.close)
        sock.sendall(self.masked(1, b'12345678') + self.masked(1, b'a'))
        self.assertEqual(self.frame(sock), (8, struct.pack('!H', 1009)))
        allow_receive.set()
        self.assertTrue(done.wait(2))

    def test_server_shutdown_interrupts_supervised_reader(self):
        exited = threading.Event()
        def handler(ws):
            try:
                ws.receive()
            except core.WebError:
                pass
            finally:
                exited.set()
        server = self.start(handler, WebSocketPolicy(heartbeat_interval_seconds=.1,
                                                    pong_timeout_seconds=.5))
        sock, response = self.raw(server)
        self.addCleanup(sock.close)
        self.assertIn(b' 101 ', response)
        server.shutdown(.08)
        self.assertTrue(exited.wait(2))

    def test_disabled_heartbeat_retains_original_sync_reader(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        ws = WebSocket(left, core.test_request('GET', '/ws'), WebSocketPolicy(heartbeat_interval_seconds=0))
        self.assertIsNone(ws._reader)
        right.sendall(self.masked(1, b'ok'))
        self.assertEqual(ws.receive().value, 'ok')

    def test_valiance_policy_fields_in_optimized_serialized_bytecode(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run
        source = ("import {std.web}\n"
                  "web.WebSocketPolicy(heartbeatIntervalSeconds = 3, "
                  "pongTimeoutSeconds = 4)")
        analyser = Analyser()
        checked = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        compiled = compile_program(checked, optimize=True)
        for program in (compiled, loads(dumps(compiled))):
            policy, = run(program)
            self.assertEqual(int(policy.fields['heartbeatIntervalSeconds']), 3)
            self.assertEqual(int(policy.fields['pongTimeoutSeconds']), 4)
            from valiance.std import web as bridge
            native = bridge._core_ws_policy(policy)
            self.assertEqual(native.heartbeat_interval_seconds, 3)
            self.assertEqual(native.pong_timeout_seconds, 4)

    def test_close_frame_is_sent_once_when_shutdown_races_application(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        ws = WebSocket(left, core.test_request('GET', '/ws'), WebSocketPolicy(heartbeat_interval_seconds=0))
        threads = [threading.Thread(target=ws.close, daemon=True) for _ in range(8)]
        for worker in threads:
            worker.start()
        for worker in threads:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        right.settimeout(.2)
        self.assertEqual(right.recv(32), b'\x88\x02\x03\xe8')
        with self.assertRaises(socket.timeout):
            right.recv(1)

    def test_policy_rejects_invalid_deadlines(self):
        for value in (-1.0, float('nan'), float('inf'), 90000):
            with self.subTest(value=value):
                with self.assertRaises(core.ConfigError):
                    WebSocketPolicy(heartbeat_interval_seconds=value)
        with self.assertRaises(core.ConfigError):
            WebSocketPolicy(pong_timeout_seconds=0)


class HeartbeatH2Tests(ws_h2.H2WebSocketWireTests):
    """Inherit the tested H2 fixture; only the heartbeat case is a new test."""
    # unittest inherited methods below are disabled by replacing test names
    # in the actual test class instead of subclassing the test fixture.
    def test_heartbeat_without_application_reader(self):
        allow_receive = threading.Event()
        finished = threading.Event()
        result = []
        def handler(ws):
            allow_receive.wait(2)
            result.append(ws.receive())
            finished.set()
        server = self.start(core.App().websocket('/ws', handler, WebSocketPolicy(
            heartbeat_interval_seconds=.15, pong_timeout_seconds=.45)))
        self.addCleanup(allow_receive.set)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.DataReceived) and e.stream_id == 1 for e in es), timeout=3)
        data = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived) and e.stream_id == 1)
        frames = ws_h2.decodeframes(data)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], 9)
        conn.send_data(1, ws_h2.maskframe(10, frames[0][1]) + ws_h2.maskframe(1, b'over-h2'))
        sock.sendall(conn.data_to_send())
        time.sleep(.1)
        allow_receive.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual([(m.kind, m.value) for m in result], [('Text', 'over-h2')])
        # Co-multiplexed ordinary GET remains unaffected by heartbeat traffic.
        # The route isn't registered here; a 404 still demonstrates stream
        # independence rather than WebSocket-specific blocking.
        conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
                              (':path', '/missing'), (':authority', 'localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es), timeout=3)
        self.assertEqual([dict(e.headers)[':status'] for e in events
                          if isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3], ['404'])


    def test_peer_missing_pong_ends_h2_websocket_not_connection(self):
        exited = threading.Event()
        def handler(ws):
            try:
                ws.receive()
            except core.WebError:
                pass
            finally:
                exited.set()
        server = self.start(core.App().websocket('/ws', handler, WebSocketPolicy(
            heartbeat_interval_seconds=.1, pong_timeout_seconds=.2))
                            .route('GET', '/ok', lambda _: 'still alive'))
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 1 for e in es), timeout=4)
        frames = ws_h2.decodeframes(b''.join(e.data for e in events
            if isinstance(e, h2.events.DataReceived) and e.stream_id == 1))
        self.assertTrue(any(opcode == 9 for opcode, _ in frames))
        self.assertIn((8, struct.pack('!H', 1001)), frames)
        self.assertTrue(exited.wait(2))
        conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
                              (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es))
        self.assertEqual([dict(e.headers)[':status'] for e in events if
                          isinstance(e, h2.events.ResponseReceived) and e.stream_id == 3], ['200'])



# Keep the upstream fixture's inherited cases out of this new module's test
# collection: they are run separately in their native suite.
for _name in list(vars(ws_h2.H2WebSocketWireTests)):
    if _name.startswith('test_'):
        setattr(HeartbeatH2Tests, _name, None)
