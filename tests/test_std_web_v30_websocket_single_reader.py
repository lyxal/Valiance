"""Single-owner WebSocket input: a second reader never corrupts frame parsing."""
from __future__ import annotations

import socket
import struct
import threading
import time
import unittest

from valiance.std import _web_core as core
from valiance.std._web_ws import WebSocket, WebSocketPolicy


class WebSocketReaderOwnershipTests(unittest.TestCase):
    def test_concurrent_receive_rejected_without_consuming_any_frame(self):
        server, peer = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(peer.close)
        request = core.test_request('GET', '/ws')
        ws = WebSocket(server, request, WebSocketPolicy(idle_timeout_seconds=3, heartbeat_interval_seconds=0))
        complete = threading.Event()
        outputs = []
        def consumer():
            try:
                outputs.append(ws.receive())
            finally:
                complete.set()
        worker = threading.Thread(target=consumer, daemon=True)
        worker.start()
        self.assertTrue(self._await(lambda: ws._receive_lock.locked()))
        with self.assertRaises(core.WebError) as error:
            ws.receive()
        self.assertEqual(error.exception.name, 'BodyUnavailable')
        self.assertFalse(complete.is_set())
        # One masked text frame reaches only the original reader.
        peer.sendall(b'\x81\x82\x01\x02\x03\x04' + bytes([ord('O') ^ 1, ord('K') ^ 2]))
        self.assertTrue(complete.wait(2))
        worker.join(1)
        self.assertEqual([(m.kind, m.value) for m in outputs], [('Text', 'OK')])
        self.assertFalse(ws._receive_lock.locked())

    def test_lock_is_released_after_disconnect(self):
        server, peer = socket.socketpair()
        self.addCleanup(server.close)
        request = core.test_request('GET', '/ws')
        ws = WebSocket(server, request, WebSocketPolicy(idle_timeout_seconds=.1, heartbeat_interval_seconds=0))
        peer.close()
        with self.assertRaises(core.WebError):
            ws.receive()
        self.assertFalse(ws._receive_lock.locked())
        self.assertEqual(ws.receive().kind, 'Closed')

    @staticmethod
    def _await(predicate, duration=2):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if predicate(): return True
            time.sleep(.005)
        return predicate()
