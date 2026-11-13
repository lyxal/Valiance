"""Public typed request context and shared WebSocket heartbeat FIFO budgets.

These cases exercise compiled Valiance, HTTP/1.1 wire clients, and the RFC 8441
stream independently of the previous WebSocket acceptance fixtures.
"""
from __future__ import annotations

import socket
import threading
import time
import unittest

from websockets.sync.client import connect

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge
from valiance.std._web_ws import WebSocket, WebSocketPolicy
from tests.test_std_web_v28_websocket_h1 import WebSocketH1Tests


def compiled(source: str, *, serialize: bool = False):
    checked = Analyser()
    program = checked.analyse(parse(source))
    if checked.diagnostics:
        raise AssertionError([str(item) for item in checked.diagnostics])
    code = compile_program(program, optimize=serialize)
    return run(loads(dumps(code)) if serialize else code)


class TypedRequestContextTests(unittest.TestCase):
    def test_core_context_key_identity_and_immutable_snapshots(self):
        alpha, beta = core.ContextKey(), core.ContextKey()
        first = core.test_request('GET', '/')
        second = first.with_context(alpha, 'member')
        self.assertEqual(second.context[alpha], 'member')
        self.assertNotIn(alpha, first.context)
        self.assertNotIn(beta, second.context)
        self.assertEqual(core.context(second, alpha), 'member')
        with self.assertRaises(core.WebError) as exc:
            core.require_context(second, beta)
        self.assertEqual(exc.exception.name, 'MissingContext')

    def test_compiled_generic_key_isolation_and_optional_lookup(self):
        source = '''import {std.web}
$one = web.contextKey[String]
$two = web.contextKey[String]
$req = web.testRequest("GET", "/", web.Headers) ?
$with = web.withContext($req, $one, "allowed")
$present = web.requireContext($with, $one) ?
$missing = web.context($with, $two)
$missing'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                answer, = compiled(source, serialize=optimized)
                self.assertEqual(answer.type_name, 'None')

    def test_manually_constructed_key_is_rejected(self):
        # Private fields are unreadable to Valiance, but the nominal
        # constructor can still be called. An unauthenticated nonce must
        # never be usable to recover another request's context.
        from valiance.runtime.runtime_values import ObjectValue, RuntimeNumber
        forged = ObjectValue('web.ContextKey', {
            'token': RuntimeNumber(123),
            'authenticator': RuntimeNumber(123),
        })
        with self.assertRaises(core.WebError) as error:
            bridge._context_key_token(forged)
        self.assertEqual(error.exception.name, 'MissingContext')

    def test_context_values_survive_multiple_immutable_enrichments(self):
        source = '''import {std.web}
$one = web.contextKey[String]
$two = web.contextKey[Int]
$req = web.testRequest("GET", "/", web.Headers) ?
$first = web.withContext($req, $one, "retained")
$second = web.withContext($first, $two, 9)
web.requireContext($second, $one) ?'''
        self.assertEqual(compiled(source), ['retained'])

    def test_compiled_missing_key_returns_typed_error(self):
        source = '''import {std.web}
$one = web.contextKey[Int]
$req = web.testRequest("GET", "/", web.Headers) ?
web.requireContext($req, $one)'''
        error, = compiled(source)
        self.assertEqual(error.type_name, 'web.Error.MissingContext')

    def test_wrong_typed_value_rejected_during_analysis(self):
        source = '''import {std.web}
$key = web.contextKey[Int]
$req = web.testRequest("GET", "/", web.Headers) ?
web.withContext($req, $key, "wrong")'''
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(analyser.diagnostics)

    def test_core_middleware_snapshot_is_returned_to_socket(self):
        key = core.ContextKey()
        observations = []
        def handler(socket_owner):
            observations.append(core.require_context(socket_owner.request, key))
            socket_owner.send_text(core.require_context(socket_owner.request, key))
            socket_owner.close()
        app = core.App().use(lambda req: req.with_context(key, 'granted')).websocket('/ws', handler)
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .5)
        with connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                     open_timeout=3, close_timeout=1) as client:
            self.assertEqual(client.recv(), 'granted')
        self.assertEqual(observations, ['granted'])

    def test_compiled_websocket_middleware_typed_context_h1(self):
        source = r'''import {std.web, std.web.App, std.web.[middleware, websocket]}
$key = web.contextKey[String]
$app = App
$app := middleware: fn(req: web.Request) =>
  web.withContext($req, $key, "authorized")
end
$app := websocket("/chat"): fn(sock: web.WebSocket) =>
  $req = web.websocketRequest($sock)
  $value = web.requireContext($req, $key) ?
  web.websocketSendText($sock, $value) ?
  web.websocketClose($sock) ?
end
web.serve($app, web.ServerConfig(port=0)) ?'''
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                lease, = compiled(source, serialize=optimized)
                token = bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        port = bridge._SERVER_LEASES[token].bound_address[1]
                    with connect(f'ws://127.0.0.1:{port}/chat',
                                 open_timeout=3, close_timeout=1) as client:
                        self.assertEqual(client.recv(), 'authorized')
                finally:
                    bridge._close_server_token(token, .5)


class _SharedBudget:
    def __init__(self, maximum: int):
        self.maximum = maximum
        self.current = 0
        self.lock = threading.Lock()

    def reserve_h2_buffer(self, n: int) -> bool:
        with self.lock:
            if self.current + n > self.maximum:
                return False
            self.current += n
            return True

    def release_h2_buffer(self, n: int) -> None:
        with self.lock:
            assert 0 <= n <= self.current, (n, self.current)
            self.current -= n


def _await(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(.01)
    return condition()


class WebSocketPendingBudgetTests(unittest.TestCase):
    def socket_owner(self, budget):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        policy = WebSocketPolicy(heartbeat_interval_seconds=1.0,
                                 pong_timeout_seconds=3.0,
                                 idle_timeout_seconds=4.0)
        owner = WebSocket(left, core.test_request('GET', '/ws'), policy,
                          buffer_budget=budget)
        self.addCleanup(owner.abort)
        return owner, right

    def test_queued_messages_across_sessions_share_budget(self):
        budget = _SharedBudget(19)  # 4x frame pre-read + queued peer (4 + 16 > 19)
        one, peer1 = self.socket_owner(budget)
        two, peer2 = self.socket_owner(budget)
        peer1.sendall(WebSocketH1Tests.masked(1, b'1234'))
        self.assertTrue(_await(lambda: budget.current == 4))
        peer2.sendall(WebSocketH1Tests.masked(2, b'abcd'))
        self.assertTrue(_await(lambda: two.cancelled))
        self.assertEqual(budget.current, 4)
        self.assertEqual(one.receive().value, '1234')
        self.assertTrue(_await(lambda: budget.current == 0))
        # WS-09 rejects the frame at the 4x pre-read reservation stage;
        # the protocol-side violation is surfaced as BadRequest / close 1009.
        self.assertEqual(two._incoming_error.name, 'BadRequest')

    def test_cancel_reclaims_stalled_messages_once(self):
        budget = _SharedBudget(64)
        one, peer = self.socket_owner(budget)
        peer.sendall(WebSocketH1Tests.masked(1, b'abcdefgh'))
        self.assertTrue(_await(lambda: budget.current == 8))
        one.abort()
        one.abort()
        self.assertEqual(budget.current, 0)

    def test_peer_disconnect_reclaims_queued_messages(self):
        budget = _SharedBudget(32)
        owner, peer = self.socket_owner(budget)
        peer.sendall(WebSocketH1Tests.masked(1, b'held'))
        self.assertTrue(_await(lambda: budget.current == 4))
        peer.close()
        self.assertTrue(_await(lambda: owner.cancelled))
        self.assertEqual(budget.current, 0)

    def test_pending_queue_releases_budget_after_receive(self):
        budget = _SharedBudget(12)  # Reserve 4x frame body before parsing
        one, peer = self.socket_owner(budget)
        peer.sendall(WebSocketH1Tests.masked(1, b'abc'))
        self.assertTrue(_await(lambda: budget.current == 3))
        self.assertEqual(one.receive().value, 'abc')
        self.assertEqual(budget.current, 0)
        peer.sendall(WebSocketH1Tests.masked(1, b'xyz'))
        self.assertTrue(_await(lambda: budget.current == 3))
        one.abort()
        self.assertEqual(budget.current, 0)


class WebSocketListenerBudgetH1Tests(unittest.TestCase):
    def test_live_queued_messages_share_listener_budget(self):
        gate = threading.Event()
        messages = []
        policy = WebSocketPolicy(heartbeat_interval_seconds=.5,
                                 pong_timeout_seconds=3.0)
        def handler(ws):
            gate.wait(2)
            try:
                messages.append(ws.receive().value)
            except core.WebError:
                pass
            ws.close()
        server = http1.serve(core.App().websocket('/ws', handler, policy),
                             port=0, limits=http1.Limits(max_total_buffered_bytes=32))
        self.addCleanup(server.shutdown, .4)
        self.addCleanup(gate.set)
        client = WebSocketH1Tests()
        one, response1 = client.raw(server)
        self.addCleanup(one.close)
        self.assertIn(b' 101 ', response1)
        two, response2 = client.raw(server)
        self.addCleanup(two.close)
        self.assertIn(b' 101 ', response2)
        one.sendall(client.masked(1, b'12345678'))
        self.assertTrue(_await(lambda: server._server._h2_buffered_bytes == 8))
        two.sendall(client.masked(1, b'12345678'))
        # A second live socket cannot bypass the global memory reservation.
        self.assertEqual(client.frame(two), (8, b'\x03\xf1'))  # 1009
        self.assertEqual(server._server._h2_buffered_bytes, 8)
        gate.set()
        self.assertTrue(_await(lambda: server._server._h2_buffered_bytes == 0))
        self.assertIn('12345678', messages)


class WebSocketListenerBudgetH2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        H2WebSocketWireTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        H2WebSocketWireTests.tearDownClass.__func__(cls)

    def test_live_h2_queue_releases_shared_budget_after_receive(self):
        import h2.events
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests, maskframe
        self.start = H2WebSocketWireTests.start.__get__(self)
        self.connect = H2WebSocketWireTests.connect.__get__(self)
        self.pump = H2WebSocketWireTests.pump.__get__(self)
        self.wsheaders = H2WebSocketWireTests.wsheaders
        gate = threading.Event()
        finished = threading.Event()
        seen = []
        def handler(ws):
            gate.wait(3)
            try:
                seen.append(ws.receive().value)
            finally:
                finished.set()
                ws.close()
        server = self.start(core.App().websocket('/ws', handler,
            WebSocketPolicy(heartbeat_interval_seconds=.5, pong_timeout_seconds=3)),
            limits=http1.Limits(max_total_buffered_bytes=128))
        self.addCleanup(gate.set)
        sock, conn = self.connect(server)
        self.addCleanup(sock.close)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
        conn.send_data(1, maskframe(1, b'hello!'))
        sock.sendall(conn.data_to_send())
        self.assertTrue(_await(lambda: server._server._h2_buffered_bytes == 6))
        gate.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(seen, ['hello!'])
        self.assertTrue(_await(lambda: server._server._h2_buffered_bytes == 0))


class TypedContextObjectPayloadTests(unittest.TestCase):
    def test_compiled_object_context_survives_middleware_boundary(self):
        source = r'''import {std.web, std.web.App, std.web.[middleware, websocket]}
object Credentials =>
  $label: String
end
$key = web.contextKey[Credentials]
$app = App
$app := middleware: fn(req: web.Request) =>
  web.withContext($req, $key, Credentials("member"))
end
$app := websocket("/ws"): fn(sock: web.WebSocket) =>
  $request = web.websocketRequest($sock)
  $cred = web.requireContext($request, $key) ?
  web.websocketSendText($sock, $cred.label) ?
  web.websocketClose($sock) ?
end
web.serve($app, web.ServerConfig(port=0)) ?'''
        lease, = compiled(source)
        token = bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                port = bridge._SERVER_LEASES[token].bound_address[1]
            with connect(f'ws://127.0.0.1:{port}/ws',
                         open_timeout=3, close_timeout=1) as client:
                self.assertEqual(client.recv(), 'member')
        finally:
            bridge._close_server_token(token, .5)


class TypedContextHTTP2ValianceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        H2WebSocketWireTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        H2WebSocketWireTests.tearDownClass.__func__(cls)

    def test_compiled_valiance_middleware_context_over_extended_connect(self):
        import h2.events
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests, decodeframes
        source = r'''import {std.web, std.web.App, std.web.[middleware, websocket]}
$key = web.contextKey[String]
$app = App
$app := middleware: fn(req: web.Request) =>
  web.withContext($req, $key, "h2-authorized")
end
$app := websocket("/ws"): fn(sock: web.WebSocket) =>
  $request = web.websocketRequest($sock)
  $value = web.requireContext($request, $key) ?
  web.websocketSendText($sock, $value) ?
  web.websocketClose($sock) ?
end
web.serve($app, web.ServerConfig(port=0)) ?'''
        lease, = compiled(source)
        token = bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                app = bridge._SERVER_LEASES[token]._server.web_app.app
            self.start = H2WebSocketWireTests.start.__get__(self)
            self.connect = H2WebSocketWireTests.connect.__get__(self)
            self.pump = H2WebSocketWireTests.pump.__get__(self)
            self.wsheaders = H2WebSocketWireTests.wsheaders
            server = self.start(app)
            sock, conn = self.connect(server)
            conn.send_headers(1, self.wsheaders('/ws'))
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.StreamEnded) and e.stream_id == 1 for e in es))
            body = b''.join(e.data for e in events if
                            isinstance(e, h2.events.DataReceived) and e.stream_id == 1)
            self.assertEqual(decodeframes(body), [(1, b'h2-authorized'),
                                                   (8, b'\x03\xe8')])
        finally:
            bridge._close_server_token(token, .5)


if __name__ == '__main__':
    unittest.main()
