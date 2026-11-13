"""HTTP/2 streaming-response producer queues have explicit byte leases.

These tests use actual TLS sockets and stop the peer's HTTP/2 DATA window.
They prevent a bounded *number* of chunks from escaping the server's overall
byte budget, and assert that RST_STREAM and disconnect return those leases.
"""
from __future__ import annotations

import time
import unittest

import h2.errors
import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
import tests.test_std_web_http2_outgoing_supervision as prior


class _FiniteSource:
    def __init__(self, size):
        self.remaining = size
        self.closed = False

    def read(self, maximum):
        if not self.remaining:
            return None
        amount = min(maximum, self.remaining)
        self.remaining -= amount
        return b'S' * amount

    def close(self):
        self.closed = True


class HTTP2StreamQueueBudgetTests(unittest.TestCase):
    """Reuse only the TLS fixture and HTTP/2 event handling utilities."""

    setUpClass = classmethod(prior.HTTP2OutgoingBudgetTests.setUpClass.__func__)
    tearDownClass = classmethod(prior.HTTP2OutgoingBudgetTests.tearDownClass.__func__)
    start = prior.HTTP2OutgoingBudgetTests.start
    client = prior.HTTP2OutgoingBudgetTests.client
    headers = staticmethod(prior.HTTP2OutgoingBudgetTests.headers)
    _receive = prior.HTTP2OutgoingBudgetTests._receive
    _pause_peer_window = prior.HTTP2OutgoingBudgetTests._pause_peer_window
    _statuses = staticmethod(prior.HTTP2OutgoingBudgetTests._statuses)

    def _server(self, *, total=9000, per_connection=9000):
        def stream_response(req):
            return core.Response(200, core.stream(_FiniteSource(16000), length=16000))
        app = core.App().route('GET', '/stream', stream_response)
        return self.start(app, http1.Limits(
            max_total_buffered_bytes=total,
            max_buffered_bytes_per_connection=per_connection,
            write_idle_timeout_seconds=3,
        ))

    def _wait_budget(self, server, size, deadline=2):
        until = time.monotonic() + deadline
        while time.monotonic() < until:
            if server._server._h2_buffered_bytes == size:
                return
            time.sleep(.005)
        self.assertEqual(server._server._h2_buffered_bytes, size)

    def test_stalled_producer_cannot_exceed_shared_budget_and_reset_releases_it(self):
        server = self._server()
        sock, conn = self.client(server)
        self._pause_peer_window(sock, conn)
        conn.send_headers(1, self.headers('/stream'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self._receive(sock, conn, lambda ev: 1 in self._statuses(ev))
        self._wait_budget(server, 9000)
        conn.send_headers(3, self.headers('/stream'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self._receive(sock, conn, lambda ev: 3 in self._statuses(ev))
        # The second producer is waiting on its byte reservation, not adding
        # an unaccounted 16-KiB body to the paused connection.
        self.assertEqual(server._server._h2_buffered_bytes, 9000)
        conn.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        sock.sendall(conn.data_to_send())
        # The second producer claims the freed budget while both streams and
        # the underlying TCP connection remain independent.
        self._wait_budget(server, 9000)
        conn.increment_flow_control_window(20000, stream_id=3)
        sock.sendall(conn.data_to_send())
        events = self._receive(sock, conn,
            lambda es: any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 3 for e in es),
            timeout=4)
        self.assertEqual(b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                                  and e.stream_id == 3), b'S' * 16000)
        self._wait_budget(server, 0)

    def test_listener_budget_blocks_a_second_connection_until_the_first_resets(self):
        server = self._server(total=8000, per_connection=20000)
        first, c1 = self.client(server)
        second, c2 = self.client(server)
        self._pause_peer_window(first, c1)
        self._pause_peer_window(second, c2)
        c1.send_headers(1, self.headers('/stream'), end_stream=True)
        first.sendall(c1.data_to_send())
        self._receive(first, c1, lambda ev: 1 in self._statuses(ev))
        self._wait_budget(server, 8000)
        c2.send_headers(1, self.headers('/stream'), end_stream=True)
        second.sendall(c2.data_to_send())
        self._receive(second, c2, lambda ev: 1 in self._statuses(ev))
        self.assertEqual(server._server._h2_buffered_bytes, 8000)
        c1.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        first.sendall(c1.data_to_send())
        # A second physical TLS connection can use the released reservation.
        c2.increment_flow_control_window(20000, stream_id=1)
        second.sendall(c2.data_to_send())
        events = self._receive(second, c2,
            lambda es: any(isinstance(e, h2.events.StreamEnded) and e.stream_id == 1 for e in es),
            timeout=4)
        self.assertEqual(b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)),
                         b'S' * 16000)
        self._wait_budget(server, 0)

    def test_cancelled_and_disconnected_streams_return_shared_queue_budget(self):
        server = self._server(total=7000, per_connection=7000)
        sock, conn = self.client(server)
        self._pause_peer_window(sock, conn)
        conn.send_headers(1, self.headers('/stream'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self._receive(sock, conn, lambda ev: 1 in self._statuses(ev))
        self._wait_budget(server, 7000)
        sock.close()
        self._wait_budget(server, 0, deadline=3)
        fresh, fresh_conn = self.client(server)
        self._pause_peer_window(fresh, fresh_conn)
        fresh_conn.send_headers(1, self.headers('/stream'), end_stream=True)
        fresh.sendall(fresh_conn.data_to_send())
        self._receive(fresh, fresh_conn, lambda ev: 1 in self._statuses(ev))
        self._wait_budget(server, 7000)
        fresh_conn.reset_stream(1, h2.errors.ErrorCodes.CANCEL)
        fresh.sendall(fresh_conn.data_to_send())
        self._wait_budget(server, 0)
