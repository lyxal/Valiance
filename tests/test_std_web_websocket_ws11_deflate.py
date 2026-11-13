"""Explicit RFC 7692 no-context-takeover WebSocket compression acceptance tests."""
from __future__ import annotations

import os
import socket
import struct
import unittest
import zlib

from websockets.sync.client import connect
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_ws import WebSocket, WebSocketPolicy, _DEFLATE_RESPONSE, _negotiated_deflate
from tests.test_std_web_ws08_ws11_resource_gates import Budget, masked


def compressed(data: bytes) -> bytes:
    obj = zlib.compressobj(wbits=-15)
    out = obj.compress(data) + obj.flush(zlib.Z_SYNC_FLUSH)
    assert out.endswith(b'\x00\x00\xff\xff')
    return out[:-4]


def frame(opcode: int, data: bytes, *, fin=True, rsv1=False) -> bytes:
    raw = bytearray(masked(opcode, data, fin=fin))
    if rsv1: raw[0] |= 0x40
    return bytes(raw)


def recv_frame(sock):
    raw = sock.recv(2)
    if len(raw) < 2: raise EOFError()
    a, b = raw
    if b & 127 == 126: n = struct.unpack('!H', sock.recv(2))[0]
    elif b & 127 == 127: n = struct.unpack('!Q', sock.recv(8))[0]
    else: n = b & 127
    data = b''
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk: raise EOFError()
        data += chunk
    return a, data


class DeflateCore(unittest.TestCase):
    def setUp(self):
        self.left, self.right = socket.socketpair()
        self.addCleanup(self.left.close)
        self.addCleanup(self.right.close)
        self.left.settimeout(2)
        self.right.settimeout(2)

    def owner(self, **kwargs):
        policy = WebSocketPolicy(extension_policy='PermessageDeflate',
                                 max_frame_bytes=32768, max_message_bytes=131072,
                                 heartbeat_interval_seconds=0, **kwargs)
        return WebSocket(self.left, core.test_request('GET', '/ws'), policy,
                         permessage_deflate=True)

    def test_raw_compressed_fragmented_text_and_control_interleave(self):
        ws = self.owner()
        data = compressed(('snowman \N{SNOWMAN}' * 1000).encode())
        half = len(data) // 2
        self.right.sendall(frame(1, data[:half], fin=False, rsv1=True) +
                           frame(9, b'?') + frame(0, data[half:]))
        msg = ws.receive()
        self.assertEqual(msg.kind, 'Text')
        self.assertEqual(msg.value, 'snowman \N{SNOWMAN}' * 1000)
        self.assertEqual(recv_frame(self.right), (0x8a, b'?'))
        ws.abort()

    def test_server_compression_rsv_only_on_first_frame(self):
        ws = self.owner()
        text = 'a' * 4000
        ws.send_text(text)
        a, payload = recv_frame(self.right)
        self.assertEqual(a, 0xc1)
        inflate = zlib.decompressobj(-15)
        self.assertEqual(inflate.decompress(payload + b'\x00\x00\xff\xff').decode(), text)
        ws.abort()

    def test_decompression_bomb_rejected_and_budget_released(self):
        budget = Budget(total=1024 * 1024)
        ws = WebSocket(self.left, core.test_request('GET','/ws'), WebSocketPolicy(
            extension_policy='PermessageDeflate', max_frame_bytes=4096, max_message_bytes=8192,
            heartbeat_interval_seconds=0), buffer_budget=budget,
            permessage_deflate=True)
        self.right.sendall(frame(2, compressed(b'Z' * 200000), rsv1=True))
        with self.assertRaises(core.WebError):
            ws.receive()
        self.assertEqual(budget.used, 0)
        ws.abort()

    def test_invalid_compression_and_rsv_on_continuation_rejected(self):
        ws = self.owner()
        self.right.sendall(frame(1, b'not deflate', rsv1=True))
        with self.assertRaises(core.WebError): ws.receive()
        ws.abort()
        # Fresh connection for invalid RSV continuation.
        left, right = socket.socketpair()
        self.addCleanup(left.close); self.addCleanup(right.close)
        policy = WebSocketPolicy(extension_policy='PermessageDeflate',heartbeat_interval_seconds=0)
        ws2 = WebSocket(left, core.test_request('GET','/ws'), policy, permessage_deflate=True)
        right.sendall(frame(1,b'a',fin=False) + frame(0,b'b',rsv1=True))
        with self.assertRaises(core.WebError): ws2.receive()
        ws2.abort()

    def test_extension_offer_policy_strict_and_disabled_default(self):
        policy = WebSocketPolicy(extension_policy='PermessageDeflate')
        headers = core.Headers((('Sec-WebSocket-Extensions', 'permessage-deflate; client_max_window_bits'),))
        self.assertTrue(_negotiated_deflate(headers, policy))
        self.assertFalse(_negotiated_deflate(headers, WebSocketPolicy()))
        self.assertFalse(_negotiated_deflate(core.Headers((('Sec-WebSocket-Extensions', 'permessage-deflate; server_max_window_bits=8'),)),policy))
        self.assertFalse(_negotiated_deflate(core.Headers((('Sec-WebSocket-Extensions', 'permessage-deflate; client_no_context_takeover; client_no_context_takeover'),)),policy))
        with self.assertRaises(core.ConfigError):
            WebSocketPolicy(extension_policy='ArbitraryExtension')
        self.assertIn('server_no_context_takeover', _DEFLATE_RESPONSE)

    def test_deflate_cap_reservation_denies_before_inflation(self):
        budget = Budget(total=128)
        ws = WebSocket(self.left, core.test_request('GET','/ws'), WebSocketPolicy(
            extension_policy='PermessageDeflate', max_frame_bytes=4096, max_message_bytes=8192,
            heartbeat_interval_seconds=0), buffer_budget=budget,
            permessage_deflate=True)
        self.right.sendall(frame(2, compressed(b'hello'), rsv1=True))
        with self.assertRaises(core.WebError): ws.receive()
        self.assertEqual(budget.used, 0)
        ws.abort()


    def test_upgrade_staging_bytes_reserved_and_released(self):
        initial = frame(2, b'early-binary')
        budget = Budget(total=4096)
        ws = WebSocket(self.left, core.test_request('GET','/ws'), WebSocketPolicy(
            heartbeat_interval_seconds=0), initial_bytes=initial, buffer_budget=budget)
        self.assertEqual(budget.used, len(initial))
        self.assertEqual(ws.receive().value, b'early-binary')
        self.assertEqual(budget.used, 0)
        ws.abort()
        self.assertEqual(budget.used, 0)

    def test_unread_upgrade_staging_reclaimed_after_abort(self):
        initial = frame(2, b'early-binary')
        budget = Budget(total=4096)
        ws = WebSocket(self.left, core.test_request('GET','/ws'), WebSocketPolicy(
            heartbeat_interval_seconds=0), initial_bytes=initial, buffer_budget=budget)
        self.assertEqual(budget.used, len(initial))
        ws.abort(fast=True)
        self.assertEqual(budget.used, 0)

    def test_upgrade_staging_admission_rejected_before_reader(self):
        initial = frame(2, b'x' * 100)
        budget = Budget(total=10)
        with self.assertRaises(core.WebError):
            WebSocket(self.left, core.test_request('GET','/ws'), WebSocketPolicy(
                heartbeat_interval_seconds=0), initial_bytes=initial, buffer_budget=budget)
        self.assertEqual(budget.used, 0)

    def test_compressed_invalid_utf8_rejected_with_close_1007(self):
        ws = self.owner()
        self.right.sendall(frame(1, compressed(b'\xff'), rsv1=True))
        with self.assertRaises(core.WebError):
            ws.receive()
        opcode, data = recv_frame(self.right)
        self.assertEqual(opcode & 15, 8)
        self.assertEqual(struct.unpack('!H', data[:2])[0], 1007)
        ws.abort()



class DeflateLive(unittest.TestCase):
    def test_independent_websockets_deflate_client_roundtrips(self):
        def echo(ws):
            for _ in range(3):
                msg = ws.receive()
                if msg.kind == 'Text': ws.send_text(msg.value)
                else: ws.send_bytes(msg.value)
            ws.close()
        app = core.App().websocket('/ws',echo,WebSocketPolicy(
            extension_policy='PermessageDeflate', heartbeat_interval_seconds=0))
        server = http1.serve(app,port=0)
        self.addCleanup(server.shutdown,.5)
        url=f'ws://127.0.0.1:{server.bound_address[1]}/ws'
        with connect(url, open_timeout=3, close_timeout=2, compression='deflate') as client:
            self.assertEqual(len(client.protocol.extensions),1)
            for data in ('snowman \N{SNOWMAN}' * 1000, bytes(range(256)) * 100, 'final' * 1000):
                client.send(data)
                self.assertEqual(client.recv(), data)


if __name__=='__main__': unittest.main()
