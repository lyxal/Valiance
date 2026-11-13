"""Frozen WS-01..WS-07 acceptance work: typed keys, borrows and cancellation.

These tests exercise both transports, compiled Valiance, cooperative child
lifetimes and cancellation races; they deliberately do not certify WS-08..23.
"""
from __future__ import annotations

import socket
import random
import threading
import time
import unittest
from unittest.mock import patch

import h2.events
from websockets.sync.client import connect

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.vm import VirtualMachine, FunctionValue
from valiance.runtime.concurrency import TaskScope, TaskState, ScopeState
from valiance.std import _web_core as core, _web_http1 as http1, web as bridge
from valiance.std._web_ws import WebSocket, WebSocketPolicy
from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests, decodeframes


def analyse(source: str):
    checker = Analyser()
    typed = checker.analyse(parse(source))
    return checker, typed


def compiled(source: str, *, serialize: bool = False):
    checker, typed = analyse(source)
    if checker.diagnostics:
        raise AssertionError([str(x) for x in checker.diagnostics])
    code = compile_program(typed, optimize=serialize)
    return run(loads(dumps(code)) if serialize else code)


class PublicWSAPIAndBorrowTests(unittest.TestCase):
    def test_ws01_canonical_generic_mints_two_distinct_keys(self):
        source = '''import {std.web}
$first = web.ContextKey[String]
$other = web.ContextKey[String]
$req = web.testRequest("GET", "/", web.Headers) ?
$allowed = web.withContext($req, $first, "member")
$unused = web.context($allowed, $other)
web.requireContext($allowed, $first) ?'''
        for serialized in (False, True):
            self.assertEqual(compiled(source, serialize=serialized), ['member'])
        # Same-type distinct keys cannot recover one another's entries.
        self.assertEqual(compiled(source.replace('web.requireContext($allowed, $first) ?',
                                                 'web.context($allowed, $other)'))[0].type_name, 'None')

    def test_ws01_forged_constructor_arguments_are_rejected(self):
        checker, _ = analyse('import {std.web}\nweb.ContextKey[Int](1, 2)')
        self.assertTrue(checker.diagnostics)

    def test_ws01_wrong_key_value_type_is_a_compile_failure(self):
        source = '''import {std.web}
$key = web.ContextKey[Int]
$req = web.testRequest("GET", "/", web.Headers) ?
web.withContext($req, $key, "not an Int")'''
        checker, _ = analyse(source)
        self.assertTrue(checker.diagnostics)

    def test_ws02_ordinary_nodup_owner_can_be_observed_but_not_copied(self):
        source = '''@nodup("exclusive") object Owner =>
  public $label: String
end
$owner = Owner("member")
$first = $owner.label
$second = $owner.label
$first'''
        self.assertEqual(compiled(source), ['member'])
        checker, _ = analyse(source.replace('$second = $owner.label', '$second = $owner'))
        self.assertTrue(any('additional occurrence' in str(d) or 'cannot store' in str(d)
                            for d in checker.diagnostics))

    def test_ws02_public_socket_cannot_be_copied_statically(self):
        source = '''import {std.web, std.web.App, std.web.websocket}
$app = App
$app := websocket("/ws"): fn(sock: web.WebSocket) =>
  $copy = $sock
  web.websocketClose($sock) ?
end
$app'''
        checker, _ = analyse(source)
        self.assertTrue(any('cannot store' in str(d) for d in checker.diagnostics))

    def test_ws03_public_defaults_and_invalid_types(self):
        policy, = compiled('import {std.web}\nweb.WebSocketPolicy')
        self.assertEqual(int(policy.fields['maxMessageBytes']), 8 * 1024 * 1024)
        self.assertEqual(int(policy.fields['heartbeatIntervalSeconds']), 30)
        self.assertEqual(policy.fields['extensionPolicy'], 'Disabled')
        limits, = compiled('import {std.web}\nweb.Limits')
        self.assertEqual(int(limits.fields['maxWebSocketFrameBytes']), 1024 * 1024)
        self.assertEqual(int(limits.fields['maxWebSocketMessagesBytes']), 8 * 1024 * 1024)
        self.assertEqual(int(limits.fields['webSocketPingIntervalSeconds']), 30)
        self.assertEqual(int(limits.fields['webSocketPongTimeoutSeconds']), 10)
        self.assertEqual(WebSocketPolicy(extension_policy='PermessageDeflate').extension_policy,
                         'PermessageDeflate')
        for expression in ('web.WebSocketPolicy(maxMessageBytes="huge")',
                           'web.WebSocketPolicy(heartbeatIntervalSeconds="often")',
                           'web.Limits(maxWebSocketMessagesBytes="huge")'):
            checker, _ = analyse('import {std.web}\n' + expression)
            self.assertTrue(checker.diagnostics, expression)

    def test_ws03_route_uses_stricter_listener_message_cap(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        class Budget:
            web_limits = http1.Limits(max_websocket_messages_bytes=128,
                                       max_websocket_frame_bytes=64)
        ws = WebSocket(left, core.test_request('GET', '/ws'),
                       WebSocketPolicy(max_message_bytes=1024,
                                       max_frame_bytes=512, heartbeat_interval_seconds=0),
                       buffer_budget=Budget())
        self.addCleanup(ws.abort)
        self.assertEqual(ws._policy.max_message_bytes, 128)
        self.assertEqual(ws._policy.max_frame_bytes, 64)
        with self.assertRaises(core.WebError):
            ws.send_bytes(b'x' * 129)

    def test_ws02_live_valiance_direct_request_member(self):
        source = '''import {std.web, std.web.App, std.web.websocket}
$app = App
$app := websocket("/ws"): fn(sock: web.WebSocket) =>
  web.websocketSendText($sock, $sock.request.originalMethod) ?
  web.websocketClose($sock) ?
end
web.serve($app, web.ServerConfig(port=0)) ?'''
        for serialized in (False, True):
            lease, = compiled(source, serialize=serialized)
            token = bridge._lease_token(lease)
            try:
                with bridge._SERVER_LEASES_LOCK:
                    port = bridge._SERVER_LEASES[token].bound_address[1]
                with connect(f'ws://127.0.0.1:{port}/ws', open_timeout=3,
                             close_timeout=1) as client:
                    self.assertEqual(client.recv(), 'GET')
            finally:
                bridge._close_server_token(token, .5)


class WSSessionCancellationTests(unittest.TestCase):
    def make_owner(self):
        one, two = socket.socketpair()
        self.addCleanup(one.close)
        self.addCleanup(two.close)
        owner = WebSocket(one, core.test_request('GET', '/ws'),
                          WebSocketPolicy(heartbeat_interval_seconds=0))
        self.addCleanup(owner.abort)
        return owner, two

    def test_ws04_native_cooperative_children_cancel_and_join(self):
        owner, _ = self.make_owner()
        entered = threading.Event()
        finished = threading.Event()
        def child(cancelled):
            entered.set()
            cancelled.wait(4)
            finished.set()
        worker = owner.spawn_child(child)
        self.assertTrue(entered.wait(1))
        owner.abort()
        self.assertTrue(finished.wait(1))
        worker.join(.2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(owner._children)
        with self.assertRaises(core.WebError):
            owner.spawn_child(child)

    def test_ws04_valiance_spawned_task_is_cancelled_at_callback_scope_exit(self):
        source = '''$callback = fn =>
  $child = fn =>
    1 pop
    2 pop
  end | spawn
end
$callback'''
        checker, typed = analyse(source)
        self.assertEqual(checker.diagnostics, [])
        program = compile_program(typed)
        seen = []
        original = TaskScope.spawn
        def track(scope, *args, **kwargs):
            handle = original(scope, *args, **kwargs)
            seen.append((scope, handle.control))
            return handle
        with patch.object(TaskScope, 'spawn', track), VirtualMachine() as vm:
            callback, = vm.call(FunctionValue(program.main, vm.globals), [])
            self.assertEqual(vm.call_value_scoped(callback, []), [])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1].state, TaskState.CANCELLED)
        self.assertEqual(seen[0][0].state, ScopeState.CLOSED)

    def test_ws04_scoped_valiance_child_cancelled_when_callback_panics(self):
        source = """$callback = fn =>
  $child = fn =>
    1 pop
    2 pop
  end | spawn
  ValueFault("panic inside session") panic
end
$callback"""
        checker, typed = analyse(source)
        self.assertFalse(checker.diagnostics)
        program = compile_program(typed)
        children = []
        original = TaskScope.spawn
        def track(scope, *args, **kwargs):
            handle = original(scope, *args, **kwargs)
            children.append((scope, handle.control))
            return handle
        with patch.object(TaskScope, 'spawn', track), VirtualMachine() as vm:
            callback, = vm.call(FunctionValue(program.main, vm.globals), [])
            with self.assertRaises(Exception):
                vm.call_value_scoped(callback, [])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0][1].state, TaskState.CANCELLED)
        self.assertEqual(children[0][0].state, ScopeState.CLOSED)

    def test_ws05_listener_shutdown_interrupts_live_blocked_reader(self):
        entered = threading.Event()
        finished = threading.Event()
        observed = []
        def handler(ws):
            entered.set()
            try:
                ws.receive()
            except core.WebError as err:
                observed.append(err.name)
            finally:
                finished.set()
        server = http1.serve(core.App().websocket('/ws', handler), port=0)
        self.addCleanup(server.shutdown, 0)
        client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                         open_timeout=3, close_timeout=.2)
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(len(server._server._websocket_owners), 1)
            server.shutdown(.05)
            self.assertTrue(finished.wait(1))
            self.assertIn('BodyUnavailable', observed)
            self.assertFalse(server._server._websocket_owners)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_ws06_after_handshake_error_never_sends_http_500(self):
        from tests.test_std_web_v28_websocket_h1 import WebSocketH1Tests
        def handler(ws):
            raise ValueError('confidential error string')
        server = http1.serve(core.App().websocket('/ws', handler), port=0)
        self.addCleanup(server.shutdown, .3)
        helper = WebSocketH1Tests()
        with self.assertLogs('valiance.std._web_http1', level='ERROR') as observed:
            sock, response = helper.raw(server)
            self.addCleanup(sock.close)
            self.assertIn(b' 101 ', response)
            kind, payload = helper.frame(sock)
            self.assertEqual((kind, payload), (8, b'\x03\xf3'))  # 1011
            self.assertNotIn(b'HTTP/1.1 500', response)
        self.assertNotIn('confidential error string', ' '.join(observed.output))
        self.assertIn('ValueError', ' '.join(observed.output))

    def test_ws07_abort_is_idempotent_while_writer_is_blocked(self):
        blocked = threading.Event()
        released = threading.Event()
        class Backpressured:
            def settimeout(self, value):
                pass
            def sendall(self, payload):
                if payload[0] == 0x81:
                    blocked.set()
                    if not released.wait(3):
                        raise OSError('writer timed out')
            def recv(self, n):
                raise OSError('cancelled')
            def interrupt_write(self):
                released.set()
            def interrupt_read(self):
                released.set()
        ws = WebSocket(Backpressured(), core.test_request('GET', '/ws'),
                       WebSocketPolicy(heartbeat_interval_seconds=0))
        failures = []
        def send():
            try:
                ws.send_text('blocked')
            except OSError:
                failures.append('interrupted')
        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        self.assertTrue(blocked.wait(1))
        before = time.monotonic()
        callbacks = [threading.Thread(target=ws.abort, daemon=True) for _ in range(8)]
        for thread in callbacks: thread.start()
        for thread in callbacks: thread.join(1)
        writer.join(1)
        self.assertTrue(all(not t.is_alive() for t in callbacks))
        self.assertFalse(writer.is_alive())
        self.assertLess(time.monotonic() - before, 1.5)
        self.assertEqual(failures, [])  # Interrupted transport may complete sendall.
        self.assertTrue(ws.cancelled)


    def test_ws07_seeded_h1_send_close_abort_schedule(self):
        rng = random.Random(1707)
        for _ in range(40):
            left, right = socket.socketpair()
            try:
                ws = WebSocket(left, core.test_request('GET', '/ws'),
                               WebSocketPolicy(heartbeat_interval_seconds=0))
                barrier = threading.Barrier(3)
                failures = []
                def worker(operation):
                    barrier.wait(timeout=1)
                    time.sleep(rng.random() * .001)
                    try:
                        operation()
                    except (core.WebError, OSError):
                        pass
                    except Exception as exc:
                        failures.append(type(exc).__name__)
                ops = [lambda: ws.send_text('small'), lambda: ws.close(), lambda: ws.abort()]
                threads = [threading.Thread(target=worker, args=(op,), daemon=True) for op in ops]
                for t in threads: t.start()
                for t in threads: t.join(1)
                self.assertTrue(all(not t.is_alive() for t in threads))
                self.assertFalse(failures, failures)
                self.assertTrue(ws.cancelled)
            finally:
                left.close()
                right.close()

    def test_ws07_waiting_sender_cannot_start_after_abort(self):
        class Transport:
            def settimeout(self, value): pass
            def sendall(self, data):
                raise AssertionError('Cancelled sender must not write')
            def interrupt_write(self): pass
            def interrupt_read(self): pass
        owner = WebSocket(Transport(), core.test_request('GET', '/ws'),
                          WebSocketPolicy(heartbeat_interval_seconds=0))
        errors = []
        owner._send_lock.acquire()
        try:
            sender = threading.Thread(target=lambda: self._try_send(owner, errors), daemon=True)
            sender.start()
            # The writer is waiting on the exclusive lock; shutdown must not
            # wait for it or allow it to write after releasing that lock.
            owner.abort(1001)
        finally:
            owner._send_lock.release()
        sender.join(2)
        self.assertFalse(sender.is_alive())
        self.assertEqual(errors, ['BodyUnavailable'])

    @staticmethod
    def _try_send(owner, errors):
        try:
            owner.send_text('late message')
        except core.WebError as err:
            errors.append(err.name)


class WSH2CancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        H2WebSocketWireTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        H2WebSocketWireTests.tearDownClass.__func__(cls)

    start = H2WebSocketWireTests.start
    connect = H2WebSocketWireTests.connect
    pump = H2WebSocketWireTests.pump
    wsheaders = staticmethod(H2WebSocketWireTests.wsheaders)

    def test_ws07_h2_reset_vs_blocked_write_seeded_recovery(self):
        finished = []
        def handler(ws):
            try:
                ws.send_bytes(b'\xaa' * 600000)
            except (core.WebError, OSError, TimeoutError):
                pass
            finally:
                finished.append(True)
        app = core.App().websocket('/ws', handler).route('GET', '/ok', lambda req: 'healthy')
        server = self.start(app)
        sock, conn = self.connect(server)
        rng = random.Random(31007)
        for i in range(6):
            websocket_sid = i * 4 + 1
            http_sid = websocket_sid + 2
            conn.send_headers(websocket_sid, self.wsheaders('/ws'))
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.ResponseReceived) and
                e.stream_id == websocket_sid for e in es), ack=False)
            self.assertTrue(any(isinstance(e, h2.events.ResponseReceived) and
                                e.stream_id == websocket_sid for e in events))
            time.sleep(rng.random() * .004)
            conn.reset_stream(websocket_sid)
            conn.send_headers(http_sid, [(':method', 'GET'), (':scheme', 'https'),
                     (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.StreamEnded) and e.stream_id == http_sid for e in es))
            self.assertIn(b'healthy', b''.join(e.data for e in events
                if isinstance(e, h2.events.DataReceived) and e.stream_id == http_sid))
        deadline = time.monotonic() + 3
        while len(finished) < 6 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(finished), 6)

    def test_ws05_h2_reset_cancels_only_websocket_owner(self):
        entered = threading.Event()
        finished = threading.Event()
        def handler(ws):
            entered.set()
            try:
                ws.receive()
            except core.WebError:
                pass
            finally:
                finished.set()
        app = core.App().websocket('/ws', handler).route('GET', '/ok', lambda req: 'still alive')
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders('/ws'))
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
        self.assertTrue(any(getattr(e, "stream_id", None) == 1 for e in events))
        self.assertTrue(entered.wait(2))
        conn.reset_stream(1)
        conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
                              (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es))
        self.assertTrue(finished.wait(2))
        self.assertIn(b'still alive', b''.join(e.data for e in events
                          if isinstance(e, h2.events.DataReceived) and e.stream_id == 3))

    def test_ws01_ws02_live_h2_valiance_canonical_key_and_borrow(self):
        source = '''import {std.web, std.web.App, std.web.[middleware, websocket]}
$key = web.ContextKey[String]
$app = App
$app := middleware: fn(req: web.Request) =>
  web.withContext($req, $key, "h2-authorized")
end
$app := websocket("/ws"): fn(sock: web.WebSocket) =>
  $request = web.websocketRequest($sock)
  $value = web.requireContext($request, $key) ?
  web.websocketSendText($sock, $sock.request.originalMethod) ?
  web.websocketSendText($sock, $value) ?
  web.websocketClose($sock) ?
end
web.serve($app, web.ServerConfig(port=0)) ?'''
        lease, = compiled(source)
        token = bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                app = bridge._SERVER_LEASES[token]._server.web_app.app
            server = self.start(app)
            sock, conn = self.connect(server)
            conn.send_headers(1, self.wsheaders('/ws'))
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.StreamEnded) and e.stream_id == 1 for e in es))
            body = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                            and e.stream_id == 1)
            self.assertEqual(decodeframes(body), [(1, b'CONNECT'),
                                                   (1, b'h2-authorized'),
                                                   (8, b'\x03\xe8')])
        finally:
            bridge._close_server_token(token, .5)
