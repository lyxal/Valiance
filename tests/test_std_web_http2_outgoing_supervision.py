"""Outgoing HTTP/2 buffered replies share finite listener/connection budgets.

The budget must remain charged while DATA is blocked by a peer-controlled
stream window, and it must be returned after the final TLS write or reset.
"""
from __future__ import annotations

import socket
import time
import unittest

import h2.errors
import h2.events
import h2.settings

from valiance.std import _web_core as core, _web_http1 as http1
import tests.test_std_web_v22_http2 as helpers


class HTTP2OutgoingBudgetTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start
    client = helpers.HTTP2TLSTests.client
    headers = staticmethod(helpers.HTTP2TLSTests.headers)

    def _receive(self, sock, conn, wanted, timeout=3):
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not wanted(seen):
            sock.settimeout(.2)
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            for event in conn.receive_data(data):
                seen.append(event)
                if isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length,
                                                   event.stream_id)
            outgoing = conn.data_to_send()
            if outgoing:
                sock.sendall(outgoing)
        self.assertTrue(wanted(seen), [type(e).__name__ for e in seen])
        return seen

    @staticmethod
    def _statuses(events):
        result = {}
        for event in events:
            if isinstance(event, h2.events.ResponseReceived):
                raw = dict(event.headers)
                value = raw.get(':status', raw.get(b':status'))
                result[event.stream_id] = int(value)
        return result

    def _pause_peer_window(self, sock, conn):
        conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 0})
        sock.sendall(conn.data_to_send())
        self._receive(sock, conn,
                      lambda events: any(isinstance(e, h2.events.SettingsAcknowledged)
                                         for e in events))

    def test_global_budget_honoured_until_blocked_data_drains(self):
        app = core.App().route('GET', '/large', lambda _: core.Response(200, b'Z' * 15000))
        server = self.start(app, http1.Limits(max_total_buffered_bytes=19000,
                        max_buffered_bytes_per_connection=30000))
        sock, conn = self.client(server)
        self._pause_peer_window(sock, conn)
        conn.send_headers(1, self.headers('/large'), end_stream=True)
        sock.sendall(conn.data_to_send())
        one = self._receive(sock, conn,
                lambda e: 1 in self._statuses(e))
        self.assertEqual(self._statuses(one)[1], 200)
        self.assertEqual(server._server._h2_buffered_bytes, 15000)
        conn.send_headers(3, self.headers('/large'), end_stream=True)
        sock.sendall(conn.data_to_send())
        three = self._receive(sock, conn,
                lambda e: any(isinstance(x, h2.events.StreamEnded) and x.stream_id == 3 for x in e))
        self.assertEqual(self._statuses(three)[3], 503)
        self.assertEqual(server._server._h2_buffered_bytes, 15000)
        conn.increment_flow_control_window(20000, stream_id=1)
        sock.sendall(conn.data_to_send())
        finished = self._receive(sock, conn,
                lambda e: any(isinstance(x, h2.events.StreamEnded) and x.stream_id == 1 for x in e))
        self.assertEqual(b''.join(x.data for x in finished
                                  if isinstance(x, h2.events.DataReceived)
                                  and x.stream_id == 1), b'Z' * 15000)
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        conn.send_headers(5, self.headers('/large'), end_stream=True)
        sock.sendall(conn.data_to_send())
        five = self._receive(sock, conn, lambda e: 5 in self._statuses(e))
        self.assertEqual(self._statuses(five)[5], 200)
        conn.reset_stream(5, error_code=h2.errors.ErrorCodes.CANCEL)
        sock.sendall(conn.data_to_send())
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._h2_buffered_bytes, 0)

    def test_global_outbound_budget_is_shared_across_tls_connections(self):
        app = core.App().route('GET', '/large',
                               lambda _: core.Response(200, b'R' * 15000))
        server = self.start(app, http1.Limits(max_total_buffered_bytes=19000,
                        max_buffered_bytes_per_connection=30000))
        one, a = self.client(server)
        two, b = self.client(server)
        self._pause_peer_window(one, a)
        self._pause_peer_window(two, b)
        a.send_headers(1, self.headers('/large'), end_stream=True)
        one.sendall(a.data_to_send())
        self.assertEqual(self._statuses(self._receive(one, a,
                lambda e: 1 in self._statuses(e)))[1], 200)
        self.assertEqual(server._server._h2_buffered_bytes, 15000)
        b.send_headers(1, self.headers('/large'), end_stream=True)
        two.sendall(b.data_to_send())
        self.assertEqual(self._statuses(self._receive(two, b,
                lambda e: 1 in self._statuses(e)))[1], 503)
        a.reset_stream(1, error_code=h2.errors.ErrorCodes.CANCEL)
        one.sendall(a.data_to_send())
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        b.send_headers(3, self.headers('/large'), end_stream=True)
        two.sendall(b.data_to_send())
        self.assertEqual(self._statuses(self._receive(two, b,
                lambda e: 3 in self._statuses(e)))[3], 200)
        b.reset_stream(3, error_code=h2.errors.ErrorCodes.CANCEL)
        two.sendall(b.data_to_send())

    def test_client_rst_releases_held_outbound_budget(self):
        app = core.App().route('GET', '/large', lambda _: core.Response(200, b'Q' * 12000))
        server = self.start(app, http1.Limits(max_total_buffered_bytes=14000,
                        max_buffered_bytes_per_connection=14000))
        sock, conn = self.client(server)
        self._pause_peer_window(sock, conn)
        conn.send_headers(1, self.headers('/large'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self._receive(sock, conn, lambda e: 1 in self._statuses(e))
        self.assertEqual(server._server._h2_buffered_bytes, 12000)
        conn.reset_stream(1, error_code=h2.errors.ErrorCodes.CANCEL)
        sock.sendall(conn.data_to_send())
        deadline = time.monotonic() + 2
        while server._server._h2_buffered_bytes and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server._server._h2_buffered_bytes, 0)
        conn.send_headers(3, self.headers('/large'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertEqual(self._statuses(self._receive(sock, conn,
                lambda e: 3 in self._statuses(e)))[3], 200)
