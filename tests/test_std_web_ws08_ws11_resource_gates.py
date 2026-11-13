"""WS-08/09 resource accounting and WS-11 extension policy acceptance cases.

These tests use an instrumented listener budget and real paired sockets; a
quota must be acquired before any untrusted frame body is read or serialized.
"""
from __future__ import annotations
import os
import socket
import struct
import threading
import time
import unittest

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_ws import WebSocket, WebSocketPolicy


class Budget:
    def __init__(self, *, total=1024 * 1024, queue=256):
        self.web_limits = http1.Limits(max_total_buffered_bytes=total,
                                      max_websocket_queued_send_bytes=queue)
        self.total = total
        self.used = 0
        self.peak = 0
        self.attempts = []
        self.lock = threading.Lock()

    def reserve_h2_buffer(self, size):
        with self.lock:
            self.attempts.append(size)
            if self.used + size > self.total:
                return False
            self.used += size
            self.peak = max(self.peak, self.used)
            return True

    def release_h2_buffer(self, size):
        with self.lock:
            assert 0 <= size <= self.used, (size, self.used)
            self.used -= size


class CaptureSocket:
    def __init__(self, *, fail=False):
        self.sent = []
        self.fail = fail
    def settimeout(self, seconds):
        pass
    def sendall(self, data):
        if self.fail:
            raise OSError('write failed')
        self.sent.append(data)
    def recv(self, maximum):
        return b''
    def interrupt_read(self):
        pass
    def interrupt_write(self):
        pass


def masked(opcode, payload, *, fin=True):
    key = b'1234'
    length = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode,
                  0x80 | length]) if length < 126 else (
        bytes([(0x80 if fin else 0) | opcode, 0xfe]) + struct.pack('!H', length))
    return head + key + bytes(c ^ key[i % 4] for i, c in enumerate(payload))


def raw_wss_binary_echo(server, context, payload: bytes, nonce: bytes) -> bytes:
    """Minimal deterministic TLS/RFC 6455 client for resource reclamation.

    These fixtures deliberately send the upgrade immediately after negotiating
    TLS, rather than relying on a third-party sync client's background reader.
    Independent-client interoperability is tested separately.
    """
    import base64
    authority = f'localhost:{server.bound_address[1]}'
    raw = socket.create_connection(server.bound_address, timeout=3)
    try:
        with context.wrap_socket(raw, server_hostname='localhost') as client:
            client.settimeout(3)
            key = base64.b64encode(nonce)
            client.sendall(b'GET /ws HTTP/1.1\r\nHost: ' +
                authority.encode('ascii') + b'\r\nUpgrade: websocket\r\n'
                b'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                b'Sec-WebSocket-Key: ' + key + b'\r\n\r\n')
            response = bytearray()
            while b'\r\n\r\n' not in response and len(response) < 4096:
                part = client.recv(1)
                if not part:
                    raise AssertionError(f'EOF before upgrade: {response!r}')
                response.extend(part)
            if b'HTTP/1.1 101' not in response:
                raise AssertionError(f'WebSocket upgrade rejected: {response!r}')
            client.sendall(masked(2, payload))
            target = 2 + len(payload)
            wire = bytearray()
            while len(wire) < target:
                part = client.recv(target - len(wire))
                if not part:
                    raise AssertionError(f'EOF before echo: {wire!r}')
                wire.extend(part)
            return bytes(wire)
    finally:
        raw.close()


def policy():
    return WebSocketPolicy(max_frame_bytes=1024, max_message_bytes=8192,
                           heartbeat_interval_seconds=0)


class ResourceGates(unittest.TestCase):
    def request(self):
        return core.test_request('GET', '/ws')

    def test_normative_queued_send_default_and_validation(self):
        self.assertEqual(http1.Limits().max_websocket_queued_send_bytes, 4194304)
        with self.assertRaises(core.ConfigError):
            http1.Limits(max_websocket_queued_send_bytes=0)
        with self.assertRaises(core.ConfigError):
            http1.Limits(max_websocket_queued_send_bytes=-1)

    def test_public_valiance_limits_constructor_reaches_native_adapter(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, run
        from valiance.std import web as bridge
        source = ('import {std.web}\n'
                  'web.ServerConfig(limits = web.Limits(maxWebSocketQueuedSendBytes = 768))')
        analyser = Analyser()
        checked = analyser.analyse(parse(source))
        self.assertFalse(analyser.diagnostics, [str(d) for d in analyser.diagnostics])
        config, = run(compile_program(checked))
        self.assertEqual(bridge._core_limits(config).max_websocket_queued_send_bytes, 768)

    def test_outbound_fragmentation_bounded_by_queue_and_shared_budget(self):
        budget = Budget(total=600, queue=256)
        stream = CaptureSocket()
        ws = WebSocket(stream, self.request(), policy(), buffer_budget=budget)
        ws.send_bytes(os.urandom(4096))
        self.assertGreater(len(stream.sent), 10)
        self.assertTrue(all(len(item) <= 256 for item in stream.sent))
        self.assertLessEqual(budget.peak, 256)
        self.assertEqual(budget.used, 0)
        self.assertEqual(ws._outgoing_bytes, 0)
        ws.abort()

    def test_outbound_rejected_before_write_if_listener_budget_exhausted(self):
        budget = Budget(total=50, queue=256)
        stream = CaptureSocket()
        ws = WebSocket(stream, self.request(), policy(), buffer_budget=budget)
        with self.assertRaises(core.WebError) as error:
            ws.send_bytes(b'!' * 150)
        self.assertEqual(error.exception.name, 'BodyTooLarge')
        self.assertEqual(stream.sent, [])
        self.assertEqual(budget.used, 0)
        ws.abort()

    def test_outbound_reservation_released_after_write_failure(self):
        budget = Budget(total=512)
        ws = WebSocket(CaptureSocket(fail=True), self.request(), policy(), buffer_budget=budget)
        with self.assertRaises(OSError):
            ws.send_text('broken connection')
        self.assertEqual(budget.used, 0)
        self.assertEqual(ws._outgoing_bytes, 0)
        ws.abort()

    def test_h1_slow_reader_expires_write_idle_and_reclaims_budget(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        budget = Budget(total=2048, queue=512)
        ws = WebSocket(left, self.request(), WebSocketPolicy(
            max_frame_bytes=1024, max_message_bytes=1024 * 1024,
            heartbeat_interval_seconds=0, idle_timeout_seconds=5,
            write_timeout_seconds=.12), buffer_budget=budget)
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            ws.send_bytes(b'Q' * (1024 * 1024))
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(budget.used, 0)
        ws.abort(fast=True)

    def test_inbound_fragmented_payload_budget_released_without_heartbeat(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        budget = Budget(total=1800)
        ws = WebSocket(left, self.request(), policy(), buffer_budget=budget)
        right.sendall(masked(2, b'1' * 100, fin=False) + masked(0, b'2' * 200))
        result = ws.receive()
        self.assertEqual(result.kind, 'Binary')
        self.assertEqual(result.value, b'1' * 100 + b'2' * 200)
        self.assertGreaterEqual(budget.peak, 1200)
        self.assertEqual(budget.used, 0)
        ws.abort()

    def test_inbound_frame_reservation_precedes_untrusted_body_arrival(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        budget = Budget(total=2048)
        ws = WebSocket(left, self.request(), policy(), buffer_budget=budget)
        results = []
        worker = threading.Thread(target=lambda: self._receive_worker(ws, results), daemon=True)
        worker.start()
        right.sendall(masked(2, b'x' * 100)[:6])  # header + mask; body withheld
        deadline = time.monotonic() + 2
        while budget.used != 400 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(budget.used, 400)
        ws.abort()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(budget.used, 0)

    @staticmethod
    def _receive_worker(ws, results):
        try:
            results.append(ws.receive())
        except Exception as exc:
            results.append(type(exc).__name__)

    def test_inbound_rejects_over_shared_budget_before_body_read(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        budget = Budget(total=256)
        ws = WebSocket(left, self.request(), policy(), buffer_budget=budget)
        right.sendall(masked(2, b'x' * 100)[:6])
        with self.assertRaises(core.WebError) as error:
            ws.receive()
        self.assertEqual(error.exception.name, 'BadRequest')
        self.assertEqual(budget.used, 0)
        ws.abort()

    def test_many_partially_delivered_frames_share_one_pre_read_budget(self):
        """A held frame header reserves memory before any body arrives."""
        budget = Budget(total=5000)
        peers, owners, jobs, results = [], [], [], []
        try:
            for index in range(5):
                left, right = socket.socketpair()
                left.settimeout(1); right.settimeout(1)
                peers.extend((left, right))
                ws = WebSocket(left, self.request(), policy(), buffer_budget=budget)
                owners.append(ws)
                job = threading.Thread(target=self._receive_worker, args=(ws, results), daemon=True)
                job.start(); jobs.append(job)
                # Valid 1000-byte frame length and mask, with body withheld.
                right.sendall(b'\x82\xfe\x03\xe8' + b'1234')
                deadline = time.monotonic() + 1
                while len(results) < index and time.monotonic() < deadline:
                    time.sleep(.002)
            self.assertLessEqual(budget.peak, 5000)
            self.assertGreaterEqual(budget.peak, 4000)
            # Only one reader can retain a 4000-byte reservation. All other
            # connections must be rejected rather than bypass the shared cap.
            self.assertEqual(budget.used, 4000)
        finally:
            for ws in owners: ws.abort(fast=True)
            for job in jobs: job.join(2)
            for peer in peers: peer.close()
        self.assertTrue(all(not job.is_alive() for job in jobs))
        self.assertEqual(budget.used, 0)

    def test_incremental_octet_input_releases_inbound_reservations(self):
        """Octet-by-octet transport input does not grow unreserved parsing."""
        left, right = socket.socketpair()
        left.settimeout(2); right.settimeout(2)
        budget = Budget(total=12000)
        ws = WebSocket(left, self.request(), policy(), buffer_budget=budget)
        result = []
        job = threading.Thread(target=self._receive_worker, args=(ws, result), daemon=True)
        try:
            job.start()
            wire = (masked(2, b'k'*256, fin=False) +
                    masked(0, b'm'*256, fin=False) + masked(0, b'n'*128))
            for byte in wire:
                right.sendall(bytes((byte,)))
            job.join(2)
            self.assertFalse(job.is_alive())
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].kind, 'Binary')
            self.assertEqual(result[0].value, b'k'*256+b'm'*256+b'n'*128)
            self.assertEqual(budget.used, 0)
            self.assertGreaterEqual(budget.peak, 4*640)
            self.assertLessEqual(budget.peak, budget.total)
        finally:
            ws.abort(fast=True)
            job.join(2)
            left.close(); right.close()

    def test_heartbeat_fifo_reserves_then_releases_pending_messages(self):
        """Automatic background receives must not hide retained FIFO bytes."""
        left, right = socket.socketpair()
        left.settimeout(2); right.settimeout(2)
        budget = Budget(total=4096)
        ws = WebSocket(left, self.request(), WebSocketPolicy(
            max_frame_bytes=256, max_message_bytes=512,
            heartbeat_interval_seconds=.7, pong_timeout_seconds=.7),
            buffer_budget=budget)
        try:
            right.sendall(b''.join(masked(2, bytes([i])*90) for i in range(4)))
            deadline = time.monotonic()+2
            while ws._incoming_bytes != 360 and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertEqual(ws._incoming_bytes, 360)
            self.assertEqual(budget.used, 360)
            self.assertEqual(ws.receive().value, bytes([0])*90)
            self.assertEqual(budget.used, 270)
            ws.abort(fast=True)
            self.assertEqual(budget.used, 0)
            self.assertEqual(ws.join_owned(1), 0)
        finally:
            ws.abort(fast=True)
            left.close(); right.close()

    def test_extension_rejected_and_reserved_bits_not_accepted(self):
        with self.assertRaises(core.ConfigError):
            WebSocketPolicy(extension_policy='permessage-deflate')
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        ws = WebSocket(left, self.request(), policy())
        frame = bytearray(masked(1, b'hello'))
        frame[0] |= 0x40  # RSV1 without negotiated extension
        right.sendall(frame)
        with self.assertRaises(core.WebError) as error:
            ws.receive()
        self.assertEqual(error.exception.name, 'BadRequest')
        ws.abort()



class LiveTransportBudgetCases(unittest.TestCase):
    """Quota-constrained outbound binary delivery on real H1 and RFC 8441 H2."""
    from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests as _fixture
    pump = _fixture.pump
    @classmethod
    def setUpClass(cls):
        import shutil
        if not shutil.which('openssl'):
            raise unittest.SkipTest('openssl is required for the H2 fixture')
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        H2WebSocketWireTests.setUpClass()
        cls.h2fixture = H2WebSocketWireTests

    @classmethod
    def tearDownClass(cls):
        cls.h2fixture.tearDownClass()

    def test_h1_outbound_fragmentation_with_small_public_queue(self):
        from websockets.sync.client import connect
        payload = os.urandom(4000)
        def handler(ws):
            ws.send_bytes(payload)
            ws.close()
        limits = http1.Limits(max_websocket_queued_send_bytes=256,
                              max_total_buffered_bytes=2048)
        server = http1.serve(core.App().websocket('/ws', handler), port=0,
                             limits=limits)
        self.addCleanup(server.shutdown, .25)
        with connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                     open_timeout=4, close_timeout=1) as client:
            self.assertEqual(client.recv(timeout=4), payload)
        self.assertEqual(server._server._h2_buffered_bytes, 0)

    def test_h2_closed_windows_on_two_clients_release_global_queue_after_reset(self):
        import h2.events
        import h2.settings
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        from valiance.std._web_ws import WebSocketPolicy
        def handler(ws):
            ws.send_bytes(b'z' * 10000)
            ws.close()
        limits = http1.Limits(max_websocket_queued_send_bytes=1024,
                              max_total_buffered_bytes=8192,
                              max_buffered_bytes_per_connection=8192)
        app = core.App().websocket('/ws', handler, WebSocketPolicy(
            max_frame_bytes=512, max_message_bytes=16000, heartbeat_interval_seconds=0))
        app = app.route('GET', '/ok', lambda req: 'healthy')
        server = http1.serve(app, port=0, limits=limits,
            tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .25)
        pairs = []
        for _ in range(2):
            sock, conn = H2WebSocketWireTests.connect(self, server)
            conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 0})
            conn.send_headers(1, H2WebSocketWireTests.wsheaders())
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, timeout=5, until=lambda es: any(
                isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1
                for e in es))
            self.assertIn('200', [dict(e.headers)[':status'] for e in events
                                   if isinstance(e, h2.events.ResponseReceived)])
            pairs.append((sock, conn))
        deadline = time.monotonic() + 3
        while server._server._h2_buffered_bytes == 0 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertGreater(server._server._h2_buffered_bytes, 0)
        self.assertLessEqual(server._server._h2_buffered_bytes, 8192)
        self.assertGreater(server.resource_snapshot()['reserved_bytes_peak'], 0)
        self.assertLessEqual(server.resource_snapshot()['reserved_bytes_peak'], 8192)
        for sock, conn in pairs:
            conn.reset_stream(1)
            conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 65535})
            conn.send_headers(3, [(':method', 'GET'), (':scheme', 'https'),
                 (':authority', 'localhost'), (':path', '/ok')], end_stream=True)
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es))
            self.assertEqual(b''.join(e.data for e in events
                if isinstance(e, h2.events.DataReceived) and e.stream_id == 3), b'healthy')
        deadline = time.monotonic() + 3
        while (server._server._h2_buffered_bytes or server._server._active_websockets) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        self.assertEqual(server._server._active_websockets, 0)

    def test_native_callback_shutdown_is_bounded_and_reports_timeout(self):
        """Bounded transport teardown never claims to kill native callbacks."""
        from websockets.sync.client import connect
        entered, unblock = threading.Event(), threading.Event()
        def blocked(ws):
            entered.set()
            unblock.wait(4)  # Simulates an uninterruptible external function.
        server = http1.serve(core.App().websocket('/ws', blocked), port=0)
        self.addCleanup(unblock.set)
        client = connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                         open_timeout=3, close_timeout=1)
        self.addCleanup(client.close)
        self.assertTrue(entered.wait(2))
        started = time.monotonic()
        server.shutdown(.07)
        self.assertLess(time.monotonic() - started, .7)
        self.assertEqual(server.stop_diagnostics, {
            'cancelled': 1, 'timed_out': 1, 'isolated': 0,
            'unjoined_owned_tasks': 0,
            'transport_sockets_open': 0,
            'transport_buffered_bytes': 0,
        })
        from valiance.std import web as bridge
        public = bridge._stop_report(0, 0, 0, .07, server.stop_diagnostics)
        self.assertEqual(int(public.fields['webSocketCancellationSignals']), 1)
        self.assertEqual(int(public.fields['webSocketCallbacksTimedOut']), 1)
        self.assertEqual(int(public.fields['webSocketOwnedTasksUnjoined']), 0)
        self.assertEqual(int(public.fields['webSocketTransportSocketsOpen']), 0)
        self.assertEqual(int(public.fields['webSocketTransportBytesRemaining']), 0)
        # The callback is still running, but its underlying server-owned FD
        # and transport budget are *already* reclaimed. The callback's lease
        # remains accurately accounted for until the callback itself exits.
        self.assertEqual(server._server._active_websockets, 1)
        self.assertEqual(server.resource_snapshot()['active_connection_workers'], 1)
        self.assertTrue(all(sock.fileno() < 0
                            for sock in server._server._connections))
        unblock.set()
        deadline = time.monotonic() + 2
        while (server._server._active_websockets or
               server.resource_snapshot()['active_connection_workers']) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_websockets, 0)
        self.assertEqual(server.resource_snapshot()['active_connection_workers'], 0)

    def test_h1_repeated_websocket_churn_reclaims_session_and_byte_counters(self):
        from websockets.sync.client import connect
        from valiance.std._web_ws import WebSocketPolicy

        def handler(owner):
            while owner.receive().kind != 'Closed':
                pass

        policy = WebSocketPolicy(heartbeat_interval_seconds=0)
        server = http1.serve(core.App().websocket('/ws', handler, policy), port=0)
        self.addCleanup(server.shutdown, .3)
        for i in range(12):
            with connect(f'ws://127.0.0.1:{server.bound_address[1]}/ws',
                         open_timeout=3, close_timeout=.5) as client:
                client.send(f'cycle-{i}')
        deadline = time.monotonic() + 2
        while (server._server._active_websockets or server._server._websocket_owners
               or server._server._h2_buffered_bytes) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_websockets, 0)
        self.assertEqual(server._server._active_websocket_callbacks, 0)
        snapshot = server.resource_snapshot()
        self.assertEqual(snapshot['reserved_bytes'], 0)
        self.assertEqual(snapshot['active_websocket_owners'], 0)
        self.assertGreaterEqual(snapshot['websocket_sessions_peak'], 1)
        self.assertFalse(server._server._websocket_owners)
        self.assertEqual(server._server._h2_buffered_bytes, 0)

    def test_h2_repeated_websocket_resets_preserve_http_and_reclaim_bytes(self):
        import h2.events
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        from valiance.std._web_ws import WebSocketPolicy

        app = core.App().websocket('/ws', lambda owner: owner.receive(),
                    WebSocketPolicy(heartbeat_interval_seconds=0))
        app = app.route('GET', '/ok', lambda req: 'healthy')
        server = http1.serve(app, port=0,
                    tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .3)
        sock, conn = H2WebSocketWireTests.connect(self, server)
        self.addCleanup(sock.close)
        for sid in range(1, 16, 2):
            conn.send_headers(sid, H2WebSocketWireTests.wsheaders('/ws'))
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, until=lambda es: any(
                isinstance(e, h2.events.ResponseReceived) and e.stream_id == sid for e in es))
            self.assertTrue(any(isinstance(e, h2.events.ResponseReceived)
                                and e.stream_id == sid for e in events))
            conn.reset_stream(sid)
            sock.sendall(conn.data_to_send())
        conn.send_headers(17, [(':method', 'GET'), (':scheme', 'https'),
             (':path', '/ok'), (':authority', 'localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id == 17 for e in es))
        self.assertIn(b'healthy', b''.join(e.data for e in events
                      if isinstance(e, h2.events.DataReceived) and e.stream_id == 17))
        deadline = time.monotonic() + 3
        while (server._server._active_websockets or server._server._h2_buffered_bytes
               or server._server._websocket_owners) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._active_websockets, 0)
        self.assertEqual(server._server._active_websocket_callbacks, 0)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        self.assertFalse(server._server._websocket_owners)

    def test_tls_h1_h2_connection_cap_and_worker_reclamation(self):
        """WS-10: TLS/H2 staging has a finite listener-wide connection multiplier."""
        import ssl
        import h2.connection
        import h2.config
        from valiance.std._web_ws import WebSocketPolicy
        limits = http1.Limits(max_connections=4,
            max_total_buffered_bytes=8192,
            max_buffered_bytes_per_connection=4096)
        app = core.App().websocket('/ws', lambda ws: ws.receive(),
            WebSocketPolicy(heartbeat_interval_seconds=0))
        server = http1.serve(app, port=0, limits=limits,
            tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .2)
        held = []
        try:
            for index in range(4):
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                tls.check_hostname = False
                tls.verify_mode = ssl.CERT_NONE
                tls.set_alpn_protocols(['h2' if index % 2 else 'http/1.1'])
                client = tls.wrap_socket(socket.create_connection(server.bound_address,
                    timeout=3), server_hostname='localhost')
                client.settimeout(3)
                held.append(client)
                if index % 2:
                    h2peer = h2.connection.H2Connection(config=h2.config.H2Configuration(
                        client_side=True))
                    h2peer.initiate_connection()
                    client.sendall(h2peer.data_to_send())
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                snapshot = server.resource_snapshot()
                if snapshot['active_connections'] == 4 and snapshot['active_h2_connections'] == 2:
                    break
                time.sleep(.01)
            snapshot = server.resource_snapshot()
            self.assertEqual(snapshot['active_connections'], 4, snapshot)
            self.assertEqual(snapshot['active_h2_connections'], 2, snapshot)
            self.assertLessEqual(snapshot['h2_tls_read_chunk_peak'], 16384)
            self.assertLessEqual(snapshot['h2_hpack_dynamic_peak'], 4096)
            self.assertLessEqual(snapshot['reserved_bytes'], 8192)
            # A fifth TCP connection cannot acquire a server-owned session
            # slot. It is closed before any TLS/HTTP handler is dispatched.
            rejected = socket.create_connection(server.bound_address, timeout=3)
            rejected.settimeout(2)
            try:
                self.assertEqual(rejected.recv(1), b'')
            finally:
                rejected.close()
            self.assertEqual(server.resource_snapshot()['active_connections'], 4)
        finally:
            for sock in held:
                sock.close()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = server.resource_snapshot()
            if snapshot['active_connections'] == snapshot['active_connection_workers'] == 0:
                break
            time.sleep(.02)
        self.assertEqual(snapshot['active_connections'], 0, snapshot)
        self.assertEqual(snapshot['active_connection_workers'], 0, snapshot)
        self.assertEqual(snapshot['active_h2_connections'], 0, snapshot)
        self.assertEqual(snapshot['reserved_bytes'], 0, snapshot)

    def test_stalled_tls_clienthello_does_not_block_wss_and_reclaims_slot(self):
        """Slow TLS negotiation uses a worker, not the listener accept loop.

        Handshake counters distinguish pre-HTTP stalls from stalled WebSocket
        handlers; the timeout must release both the connection and worker.
        """
        import ssl
        import base64

        def echo(ws):
            ws.send_text('ready')
            ws.close()

        limits = http1.Limits(max_connections=2, header_timeout_seconds=.8,
                              connection_idle_timeout_seconds=2)
        server = http1.serve(core.App().websocket('/ws', echo), port=0,
            limits=limits, tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .4)
        stalled = socket.create_connection(server.bound_address, timeout=2)
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                snapshot = server.resource_snapshot()
                if snapshot['tls_handshakes_active'] == 1:
                    break
                time.sleep(.005)
            self.assertEqual(snapshot['tls_handshakes_active'], 1, snapshot)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls.check_hostname = False
            tls.verify_mode = ssl.CERT_NONE
            tls.set_alpn_protocols(['http/1.1'])
            with tls.wrap_socket(socket.create_connection(server.bound_address,
                    timeout=3), server_hostname='localhost') as client:
                client.settimeout(3)
                client.sendall(b'GET /ws HTTP/1.1\r\nHost: localhost\r\n'
                    b'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                    b'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' +
                    base64.b64encode(b'0123456789abcdef') + b'\r\n\r\n')
                response = bytearray()
                while b'\r\n\r\n' not in response:
                    chunk = client.recv(1)
                    self.assertTrue(chunk)
                    response.extend(chunk)
                self.assertIn(b'HTTP/1.1 101', response)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                snapshot = server.resource_snapshot()
                if (snapshot['tls_handshakes_failed'] >= 1 and
                        snapshot['active_connections'] == 0 and
                        snapshot['active_connection_workers'] == 0):
                    break
                time.sleep(.01)
            self.assertGreaterEqual(snapshot['tls_handshakes_completed'], 1, snapshot)
            self.assertGreaterEqual(snapshot['tls_handshakes_failed'], 1, snapshot)
            self.assertGreaterEqual(snapshot['tls_handshakes_peak'], 1, snapshot)
            self.assertEqual(snapshot['tls_handshakes_active'], 0, snapshot)
            self.assertEqual(snapshot['active_connections'], 0, snapshot)
            self.assertEqual(snapshot['active_connection_workers'], 0, snapshot)
            self.assertEqual(snapshot['reserved_bytes'], 0, snapshot)
        finally:
            stalled.close()

    def test_reused_tls_context_wss_wire_churn_no_missing_upgrades(self):
        """TLS 1.3 session reuse must not drop H1 Upgrade headers or leases."""
        import base64
        import ssl

        def echo(ws):
            msg = ws.receive()
            ws.send_bytes(msg.value)
            ws.close()

        limits = http1.Limits(max_connections=4, max_active_websockets=2,
                              header_timeout_seconds=3,
                              max_total_buffered_bytes=16384)
        server = http1.serve(core.App().websocket('/ws', echo,
            WebSocketPolicy(heartbeat_interval_seconds=0)), port=0,
            limits=limits, tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .3)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(['http/1.1'])
        for index in range(24):
            payload = bytes((index, 255))
            self.assertEqual(raw_wss_binary_echo(server, context, payload,
                bytes((index,)) * 16), b'\x82\x02' + payload)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                metrics = server.resource_snapshot()
                if metrics['active_connections'] == metrics['active_connection_workers'] == 0:
                    break
                time.sleep(.01)
            self.assertEqual(metrics['active_connections'], 0, (index, metrics))
            self.assertEqual(metrics['active_connection_workers'], 0, (index, metrics))
        metrics = server.resource_snapshot()
        self.assertEqual(metrics['tls_handshakes_completed'], 24, metrics)
        self.assertEqual(server.counts, (24, 24))
        self.assertEqual(metrics['reserved_bytes'], 0)

    def test_mixed_tls_h1_h2_churn_preserves_listener_wide_admission(self):
        """Repeated WSS and RFC 8441 sessions reclaim the *same* ledger."""
        import h2.events
        import ssl
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests, maskframe, decodeframes
        from valiance.std._web_ws import WebSocketPolicy

        def echo(ws):
            try:
                msg = ws.receive()
                if msg.kind == 'Binary':
                    ws.send_bytes(msg.value)
                ws.close()
            except core.WebError:
                pass
        limits = http1.Limits(max_active_websockets=4,
                              max_total_buffered_bytes=16384,
                              max_buffered_bytes_per_connection=8192,
                              max_websocket_queued_send_bytes=1024)
        app = core.App().websocket('/ws', echo, WebSocketPolicy(
            heartbeat_interval_seconds=0, max_frame_bytes=256,
            max_message_bytes=512)).route('GET', '/ok', lambda _: 'healthy')
        server = http1.serve(app, port=0, limits=limits,
            tls=http1.TLSConfig(self.h2fixture.cert, self.h2fixture.key))
        self.addCleanup(server.shutdown, .3)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE
        tls.set_alpn_protocols(['http/1.1'])
        for index in range(6):
            payload = bytes((index, 0, 255))
            self.assertEqual(raw_wss_binary_echo(server, tls, payload,
                bytes((index,)) * 16), b'\x82\x03' + payload)
            sock, conn = H2WebSocketWireTests.connect(self, server)
            sid = 1
            conn.send_headers(sid, H2WebSocketWireTests.wsheaders())
            conn.send_data(sid, maskframe(2, bytes((255, index))))
            conn.send_headers(3, [(':method','GET'),(':scheme','https'),
                (':authority','localhost'),(':path','/ok')], end_stream=True)
            sock.sendall(conn.data_to_send())
            events = self.pump(sock, conn, timeout=6, until=lambda es:
                {1, 3} <= {e.stream_id for e in es if isinstance(e, h2.events.StreamEnded)})
            data = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                            and e.stream_id == 1)
            self.assertEqual(decodeframes(data), [(2, bytes((255, index))), (8, b'\x03\xe8')])
            self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                      and e.stream_id == 3), b'healthy')
            sock.close()
            # The peer's WebSocket close and HTTP/2 END_STREAM can precede
            # transport-worker teardown. Wait for the previous iteration's
            # sessions and sockets to be reclaimed before opening more TLS
            # connections; otherwise a slow CI host can confuse teardown
            # latency with a new handshake timeout.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                snapshot = server.resource_snapshot()
                if not any(snapshot[name] for name in (
                        'active_websockets', 'active_websocket_owners',
                        'active_websocket_callbacks', 'reserved_bytes',
                        'active_connections')):
                    break
                time.sleep(.02)
            self.assertFalse(any(snapshot[name] for name in (
                'active_websockets', 'active_websocket_owners',
                'active_websocket_callbacks', 'reserved_bytes',
                'active_connections')), snapshot)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = server.resource_snapshot()
            if not any(snapshot[name] for name in (
                    'active_websockets', 'active_websocket_owners',
                    'active_websocket_callbacks', 'reserved_bytes', 'active_requests')):
                break
            time.sleep(.01)
        snapshot = server.resource_snapshot()
        for name in ('active_websockets', 'active_websocket_owners',
                     'active_websocket_callbacks', 'reserved_bytes', 'active_requests'):
            self.assertEqual(snapshot[name], 0, (name, snapshot))
        self.assertLessEqual(snapshot['websocket_sessions_peak'], 4)
        self.assertLessEqual(snapshot['reserved_bytes_peak'], 16384)
        # Listener shutdown joins even its daemonized *cooperative* socket
        # workers. Only an uncooperative foreign callback can remain counted.
        server.shutdown(.3)
        self.assertEqual(server.resource_snapshot()['active_connection_workers'], 0)
        self.assertEqual(server.resource_snapshot()['active_connections'], 0)

    def test_h2_outbound_flow_with_small_public_queue(self):
        import h2.events
        from tests.test_std_web_v29_websocket_h2 import decodeframes
        payload = os.urandom(4500)
        def handler(ws):
            ws.send_bytes(payload)
            ws.close()
        limits = http1.Limits(max_websocket_queued_send_bytes=256,
                              max_total_buffered_bytes=4096)
        cert, key = self.h2fixture.cert, self.h2fixture.key
        server = http1.serve(core.App().websocket('/ws', handler), port=0,
                             limits=limits, tls=http1.TLSConfig(cert, key))
        self.addCleanup(server.shutdown, .25)
        from tests.test_std_web_v29_websocket_h2 import H2WebSocketWireTests
        sock, conn = H2WebSocketWireTests.connect(self, server)
        conn.send_headers(1, H2WebSocketWireTests.wsheaders())
        sock.sendall(conn.data_to_send())
        events = H2WebSocketWireTests.pump(self, sock, conn, timeout=8,
                  until=lambda es: any(isinstance(e, h2.events.StreamEnded)
                                       and e.stream_id == 1 for e in es))
        wire = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                        and e.stream_id == 1)
        frames = decodeframes(wire)
        self.assertTrue(frames and frames[0][0] == 2)
        self.assertEqual(b''.join(data for opcode, data in frames if opcode in (2, 0)), payload)
        self.assertEqual(frames[-1], (8, b'\x03\xe8'))
        # A peer may read END_STREAM before the connection worker has
        # cleared its last TLS-flush reservation. Require bounded recovery.
        deadline = time.monotonic() + 2
        while server.resource_snapshot()['reserved_bytes'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server.resource_snapshot()['reserved_bytes'], 0)


if __name__ == '__main__':
    unittest.main()
