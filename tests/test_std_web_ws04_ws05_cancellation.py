"""Cross-transport WebSocket cancellation boundary acceptance tests.

These tests distinguish cancellation-safe Valiance bytecode and cooperative
session children from arbitrary uninterruptible Python/native callbacks.
"""
import socket
import time
import h2.events
from websockets.sync.client import connect
from valiance.std import _web_http1 as http1
from tests import test_std_web_v29_websocket_h2 as h2_test
from tests import test_std_web_v28_websocket_h1 as h1_test
import threading
import unittest
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.vm import VirtualMachine, FunctionValue
from valiance.runtime.concurrency import CancelledFault, ScopeState, TaskScope, TaskState
from valiance.std import _web_core as core
from valiance.std._web_ws import WebSocket, WebSocketPolicy


def program_for(source):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError([str(x) for x in analyser.diagnostics])
    return compile_program(typed)


class StructuredCancellationAcceptance(unittest.TestCase):
    def test_transport_abort_exposes_owner_cancel_event(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        owner = WebSocket(left, core.test_request('GET', '/ws'),
                          WebSocketPolicy(heartbeat_interval_seconds=0))
        self.assertFalse(owner.cancellation_event.is_set())
        owner.abort()
        self.assertTrue(owner.cancellation_event.is_set())
        self.assertIs(owner.cancellation_event, owner.cancellation_event)

    def test_fast_abort_joins_owned_cooperative_python_child(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        owner = WebSocket(first, core.test_request('GET', '/ws'),
                          WebSocketPolicy(heartbeat_interval_seconds=0))
        entered = threading.Event()
        finished = threading.Event()

        def child(cancel):
            entered.set()
            cancel.wait(3)
            finished.set()

        child_thread = owner.spawn_child(child)
        self.assertTrue(entered.wait(1))
        owner.abort(fast=True)
        self.assertEqual(owner.join_owned(.5), 0)
        self.assertTrue(finished.is_set())
        self.assertFalse(child_thread.is_alive())
        self.assertEqual(owner.owned_threads_alive, 0)

    def test_uncooperative_session_child_is_reported_not_killed(self):
        first, second = socket.socketpair()
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        owner = WebSocket(first, core.test_request('GET', '/ws'),
                          WebSocketPolicy(heartbeat_interval_seconds=0))
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def ignores_cancel(cancel):
            entered.set()
            release.wait(3)

        child_thread = owner.spawn_child(ignores_cancel)
        self.assertTrue(entered.wait(1))
        owner.abort(fast=True)
        started = time.monotonic()
        self.assertEqual(owner.join_owned(.04), 1)
        self.assertLess(time.monotonic() - started, .25)
        self.assertTrue(child_thread.is_alive())
        release.set()
        self.assertEqual(owner.join_owned(1), 0)
        self.assertFalse(child_thread.is_alive())

    def test_resource_cancellation_interrupts_bytecode_callback_and_closes_scope(self):
        # Without an interpreter checkpoint, this bytecode-only infinite loop
        # cannot be stopped merely by aborting the transport owner.
        program = program_for('''$callback = fn =>
  $child = fn =>
    while (true) =>
      1 pop
    end
  end | spawn
  while (true) =>
    1 pop
  end
end
$callback''')
        entered = threading.Event()
        cancel = threading.Event()
        done = threading.Event()
        results = []
        children = []
        original = TaskScope.spawn

        def track(scope, *args, **kwargs):
            handle = original(scope, *args, **kwargs)
            children.append((scope, handle.control))
            entered.set()
            return handle

        def invoke():
            try:
                with VirtualMachine() as vm:
                    callback, = vm.call(FunctionValue(program.main, vm.globals), [])
                    vm.call_value_scoped(callback, [], cancel_event=cancel)
                results.append('returned unexpectedly')
            except CancelledFault:
                results.append('cancelled')
            except BaseException as exc:
                results.append(f'error: {type(exc).__name__}: {exc}')
            finally:
                done.set()

        with patch.object(TaskScope, 'spawn', track):
            thread = threading.Thread(target=invoke, daemon=True)
            thread.start()
            self.assertTrue(entered.wait(2), 'callback never created child')
            cancel.set()  # Peer reset/heartbeat/shutdown owner signal.
            self.assertTrue(done.wait(3), 'Valiance callback ignored cancellation')
            thread.join(.1)
        self.assertEqual(results, ['cancelled'])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0][0].state, ScopeState.CLOSED)
        self.assertEqual(children[0][1].state, TaskState.CANCELLED)
        self.assertFalse(thread.is_alive())

    def test_nested_valiance_children_are_cancelled_transitively(self):
        program = program_for('''$callback = fn =>
  $child = fn =>
    $grandchild = fn =>
      while (true) =>
        1 pop
      end
    end | spawn
    while (true) =>
      1 pop
    end
  end | spawn
  $child | wait
end
$callback''')
        cancel = threading.Event()
        seen = []
        original = TaskScope.spawn
        def track(scope, *args, **kwargs):
            handle = original(scope, *args, **kwargs)
            seen.append((scope, handle.control))
            if len(seen) == 2:
                cancel.set()
            return handle
        with patch.object(TaskScope, 'spawn', track), VirtualMachine() as vm:
            callback, = vm.call(FunctionValue(program.main, vm.globals), [])
            with self.assertRaises(CancelledFault):
                vm.call_value_scoped(callback, [], cancel_event=cancel)
        self.assertEqual(len(seen), 2)
        self.assertTrue(all(child.state is TaskState.CANCELLED and scope.state is ScopeState.CLOSED
                            for scope, child in seen))

    def test_pre_cancelled_scope_never_invokes_user_callback(self):
        event = threading.Event()
        event.set()
        callback = program_for('$callback = fn => 42 end\n$callback')
        with VirtualMachine() as vm:
            value, = vm.call(FunctionValue(callback.main, vm.globals), [])
            with self.assertRaises(CancelledFault):
                vm.call_value_scoped(value, [], cancel_event=event)
        self.assertFalse(vm._resource_scope_cancellations)

    def test_scope_registration_does_not_leak_after_success_or_panic(self):
        good = program_for('$callback = fn => 12 end\n$callback')
        bad = program_for('''$callback = fn =>
  ValueFault("panic") panic
end
$callback''')
        with VirtualMachine() as vm:
            value, = vm.call(FunctionValue(good.main, vm.globals), [])
            self.assertEqual(vm.call_value_scoped(value, [], cancel_event=threading.Event()), [12])
            self.assertFalse(vm._resource_scope_cancellations)
        with VirtualMachine() as vm:
            value, = vm.call(FunctionValue(bad.main, vm.globals), [])
            with self.assertRaises(Exception):
                vm.call_value_scoped(value, [], cancel_event=threading.Event())
            self.assertFalse(vm._resource_scope_cancellations)


class LiveWebSocketScopeCancellation(unittest.TestCase):
    _busy_source = """$callback = fn =>
  $child = fn =>
    while (true) =>
      1 pop
    end
  end | spawn
  while (true) =>
    1 pop
  end
end
$callback"""

    def setUp(self):
        self.program = program_for(self._busy_source)
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.children = []
        self.results = []
        self.original_spawn = TaskScope.spawn

    def handler(self, owner):
        try:
            with VirtualMachine() as vm:
                callback, = vm.call(FunctionValue(self.program.main, vm.globals), [])
                vm.call_value_scoped(callback, [], cancel_event=owner.cancellation_event)
            self.results.append('returned unexpectedly')
        except CancelledFault:
            self.results.append('cancelled')
        except BaseException as exc:
            self.results.append(f'error: {type(exc).__name__}: {exc}')
        finally:
            self.finished.set()

    def track(self, scope, *args, **kwargs):
        handle = self.original_spawn(scope, *args, **kwargs)
        self.children.append((scope, handle.control))
        self.entered.set()
        return handle

    def assert_reclaimed(self):
        self.assertTrue(self.finished.wait(3), 'application task scope did not stop')
        self.assertEqual(self.results, ['cancelled'])
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.children[0][0].state, ScopeState.CLOSED)
        self.assertEqual(self.children[0][1].state, TaskState.CANCELLED)

    def test_h1_peer_close_propagates_to_callback_and_cooperative_child(self):
        app = core.App().websocket('/ws', self.handler)
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .1)
        with patch.object(TaskScope, 'spawn', lambda scope, *a, **kw: self.track(scope, *a, **kw)):
            client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                             open_timeout=3, close_timeout=1)
            try:
                self.assertTrue(self.entered.wait(2), f'handler state: {self.results}')
                client.close()
                self.assert_reclaimed()
            finally:
                client.close()

    def test_h1_ungraceful_tcp_disconnect_cancels_scope(self):
        app = core.App().websocket('/ws', self.handler)
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .1)
        with patch.object(TaskScope, 'spawn', lambda scope, *a, **kw: self.track(scope, *a, **kw)):
            raw, result = h1_test.WebSocketH1Tests().raw(server)
            self.assertIn(b' 101 ', result)
            self.assertTrue(self.entered.wait(2), f'handler state: {self.results}')
            raw.shutdown(socket.SHUT_RDWR)
            raw.close()
            self.assert_reclaimed()

    def test_h1_heartbeat_timeout_cancels_callback_and_children(self):
        # Raw client never sends Pong, including after receiving the server
        # Ping. The heartbeat reader must notify the owner scope itself.
        policy = WebSocketPolicy(heartbeat_interval_seconds=.05,
                                 pong_timeout_seconds=.05)
        app = core.App().websocket('/ws', self.handler, policy=policy)
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .1)
        with patch.object(TaskScope, 'spawn', lambda scope, *a, **kw: self.track(scope, *a, **kw)):
            raw, result = h1_test.WebSocketH1Tests().raw(server)
            self.addCleanup(raw.close)
            self.assertIn(b' 101 ', result)
            self.assertTrue(self.entered.wait(2), f'handler state: {self.results}')
            self.assert_reclaimed()

    def test_h1_forced_shutdown_joins_cooperative_native_children(self):
        entered, finished = threading.Event(), threading.Event()
        seen = []

        def handler(owner):
            def child(cancel):
                entered.set()
                cancel.wait(3)
                finished.set()
            seen.append(owner.spawn_child(child))
            owner.cancellation_event.wait(3)

        server = http1.serve(core.App().websocket('/ws', handler), port=0)
        self.addCleanup(server.shutdown, 0)
        client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                         open_timeout=3, close_timeout=.2)
        try:
            self.assertTrue(entered.wait(2))
            server.shutdown(0)
            self.assertTrue(finished.wait(1))
            seen[0].join(.2)
            self.assertFalse(seen[0].is_alive())
            self.assertEqual(server.stop_diagnostics['unjoined_owned_tasks'], 0)
            self.assertEqual(server.stop_diagnostics['transport_sockets_open'], 0)
            self.assertEqual(server.stop_diagnostics['transport_buffered_bytes'], 0)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_h1_finished_handler_reports_noncooperative_owned_child(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        children = []

        def handler(owner):
            def child(cancel):
                entered.set()
                release.wait(3)
            children.append(owner.spawn_child(child))

        server = http1.serve(core.App().websocket('/ws', handler), port=0)
        self.addCleanup(server.shutdown, 0)
        client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                         open_timeout=3, close_timeout=.2)
        try:
            self.assertTrue(entered.wait(2))
            deadline = time.monotonic() + 2
            while server._server._active_websockets and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(server._server._active_websockets, 0)
            server.shutdown(.05)
            # The child is still runnable, but is diagnosed rather than
            # accidentally forgotten when its handler unregisters the owner.
            self.assertTrue(children[0].is_alive())
            self.assertEqual(server.stop_diagnostics['unjoined_owned_tasks'], 1)
            self.assertEqual(server.stop_diagnostics['timed_out'], 0)
            release.set()
            children[0].join(1)
            self.assertFalse(children[0].is_alive())
        finally:
            try:
                client.close()
            except Exception:
                pass

    def test_h1_listener_shutdown_propagates_to_bytecode_scope(self):
        app = core.App().websocket('/ws', self.handler)
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .1)
        with patch.object(TaskScope, 'spawn', lambda scope, *a, **kw: self.track(scope, *a, **kw)):
            client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                             open_timeout=3, close_timeout=.2)
            try:
                self.assertTrue(self.entered.wait(2), f'handler state: {self.results}')
                server.shutdown(0)
                self.assert_reclaimed()
            finally:
                try:
                    client.close()
                except Exception:
                    pass


class LiveHTTP2WebSocketScopeCancellation(unittest.TestCase):
    _busy_source = LiveWebSocketScopeCancellation._busy_source
    setUp = LiveWebSocketScopeCancellation.setUp
    handler = LiveWebSocketScopeCancellation.handler
    track = LiveWebSocketScopeCancellation.track
    assert_reclaimed = LiveWebSocketScopeCancellation.assert_reclaimed
    @classmethod
    def setUpClass(cls):
        h2_test.H2WebSocketWireTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        h2_test.H2WebSocketWireTests.tearDownClass.__func__(cls)

    start = h2_test.H2WebSocketWireTests.start
    connect = h2_test.H2WebSocketWireTests.connect
    pump = h2_test.H2WebSocketWireTests.pump
    wsheaders = staticmethod(h2_test.H2WebSocketWireTests.wsheaders)

    def test_h2_stream_reset_joins_cooperative_native_child(self):
        entered, finished = threading.Event(), threading.Event()
        children = []

        def handler(owner):
            def child(cancel):
                entered.set()
                cancel.wait(3)
                finished.set()
            children.append(owner.spawn_child(child))
            owner.cancellation_event.wait(3)

        app = core.App().websocket('/ws', handler).route('GET', '/ok', lambda req: 'healthy')
        server = self.start(app)
        sock, conn = self.connect(server)
        self.addCleanup(sock.close)
        conn.send_headers(1, self.wsheaders('/ws'))
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
        self.assertTrue(any(isinstance(e, h2.events.ResponseReceived)
                            and e.stream_id == 1 for e in events))
        self.assertTrue(entered.wait(2))
        conn.reset_stream(1)
        conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
             (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es))
        self.assertIn(b'healthy', b''.join(e.data for e in events
            if isinstance(e, h2.events.DataReceived) and e.stream_id == 3))
        self.assertTrue(finished.wait(2))
        children[0].join(.25)
        self.assertFalse(children[0].is_alive())
        deadline = time.monotonic() + 2
        while server._server._active_websockets and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_websockets, 0)

    def test_h2_forced_shutdown_closes_transport_with_blocked_native_callback(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def handler(owner):
            entered.set()
            release.wait(3)  # Ignores the WebSocket cancellation event.

        server = self.start(core.App().websocket('/ws', handler))
        sock, conn = self.connect(server)
        self.addCleanup(sock.close)
        conn.send_headers(1, self.wsheaders('/ws'))
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
        self.assertTrue(any(isinstance(e, h2.events.ResponseReceived)
                            and e.stream_id == 1 for e in events))
        self.assertTrue(entered.wait(2))
        before = time.monotonic()
        server.shutdown(.05)
        self.assertLess(time.monotonic() - before, .8)
        report = server.stop_diagnostics
        self.assertEqual(report['timed_out'], 1)
        self.assertEqual(report['isolated'], 0)
        self.assertEqual(report['unjoined_owned_tasks'], 0)
        self.assertEqual(report['transport_sockets_open'], 0)
        self.assertEqual(report['transport_buffered_bytes'], 0)
        self.assertEqual(server._server._active_websockets, 1)
        release.set()
        deadline = time.monotonic() + 2
        while server._server._active_websockets and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_websockets, 0)

    def test_h2_reset_cancels_vm_scope_but_preserves_peer_request(self):
        app = core.App().websocket('/ws', self.handler).route('GET', '/ok', lambda req: 'healthy')
        with patch.object(TaskScope, 'spawn', lambda scope, *a, **kw: self.track(scope, *a, **kw)):
            server = self.start(app)
            sock, conn = self.connect(server)
            conn.send_headers(1, self.wsheaders('/ws'))
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
            self.assertTrue(any(isinstance(e, h2.events.ResponseReceived)
                                and e.stream_id == 1 for e in events))
            self.assertTrue(self.entered.wait(2), f'handler state: {self.results}')
            conn.reset_stream(1)
            conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
                  (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es))
            self.assertIn(b'healthy', b''.join(e.data for e in events
                if isinstance(e, h2.events.DataReceived) and e.stream_id == 3))
            self.assert_reclaimed()




if __name__ == '__main__':
    unittest.main()
