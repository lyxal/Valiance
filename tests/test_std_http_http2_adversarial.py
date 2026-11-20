"""Hostile independent HTTP/2 TLS peer: invalid HPACK and response framing.

Frames below are intentionally emitted BELOW hyper-h2's high-level outbound
validation. The client must treat invalid HTTP/2 headers as transfer/protocol
errors rather than successful complete responses or indefinite body reads.
"""
from __future__ import annotations

from pathlib import Path
import socket
import ssl
import tempfile
from threading import Event, Thread
import unittest

import h2.config
import h2.connection
import h2.events
from hpack import Encoder

from tests.test_std_http_http2 import _write_test_certificate
from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_errors import HttpError
from valiance.std._http_transport import Client


def _frame(payload: bytes, flags: int, stream_id: int, frame_type: int = 1) -> bytes:
    return (len(payload).to_bytes(3, 'big') + bytes((frame_type, flags)) +
            stream_id.to_bytes(4, 'big') + payload)


# Each mutation is sent by an independent TLS/ALPN peer *after* the client has
# completed its request. The peer deliberately bypasses hyper-h2's protective
# outbound validation so libcurl parses genuinely hostile wire frames.
MALFORMED_FRAMES = (
    'uppercase_name', 'connection_field', 'missing_status', 'corrupt_hpack',
    'duplicate_status', 'pseudo_after_regular', 'transfer_encoding',
    'bad_status', 'response_method', 'data_before_headers',
    'data_stream_zero', 'headers_stream_zero', 'headers_even_stream',
    'settings_on_stream', 'settings_ack_payload', 'ping_wrong_length',
    'continuation_unexpected', 'zero_window_update', 'rst_stream_zero',
    'invalid_padding',
)


class _HostileH2Server:
    def __init__(self, certificate: Path, private_key: Path, mutation: str):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(1)
        self.socket.settimeout(3)
        self.url = f'https://127.0.0.1:{self.socket.getsockname()[1]}/'
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(certificate, private_key)
        self.context.set_alpn_protocols(['h2'])
        self.mutation = mutation
        self.failure = None
        self.done = Event()
        self.worker = Thread(target=self._run, daemon=True)
        self.worker.start()

    def _invalid_response(self, stream_id: int) -> bytes:
        if self.mutation == 'uppercase_name':
            block = Encoder().encode([(b':status', b'200'), (b'X-Credential', b'injected')], huffman=False)
        elif self.mutation == 'connection_field':
            block = Encoder().encode([(b':status', b'200'), (b'connection', b'keep-alive')], huffman=False)
        elif self.mutation == 'missing_status':
            block = Encoder().encode([(b'x-accepted', b'yes')], huffman=False)
        elif self.mutation == 'corrupt_hpack':
            block = b'\xff\xff\xff\xff'
        elif self.mutation == 'duplicate_status':
            block = Encoder().encode([(b':status', b'200'), (b':status', b'204')], huffman=False)
        elif self.mutation == 'pseudo_after_regular':
            block = Encoder().encode([(b'x-first', b'yes'), (b':status', b'200')], huffman=False)
        elif self.mutation == 'transfer_encoding':
            block = Encoder().encode([(b':status', b'200'), (b'transfer-encoding', b'chunked')], huffman=False)
        elif self.mutation == 'bad_status':
            block = Encoder().encode([(b':status', b'xx0')], huffman=False)
        elif self.mutation == 'response_method':
            block = Encoder().encode([(b':status', b'200'), (b':method', b'GET')], huffman=False)
        elif self.mutation == 'data_before_headers':
            return _frame(b'oops', 0x1, stream_id, 0)
        elif self.mutation == 'data_stream_zero':
            return _frame(b'oops', 0x1, 0, 0)
        elif self.mutation == 'headers_stream_zero':
            return _frame(b'\x88', 0x5, 0)
        elif self.mutation == 'headers_even_stream':
            return _frame(b'\x88', 0x5, stream_id + 1)
        elif self.mutation == 'settings_on_stream':
            return _frame(b'\x00\x02\x00\x00\x00\x00', 0, stream_id, 4)
        elif self.mutation == 'settings_ack_payload':
            return _frame(b'\x00\x02\x00\x00\x00\x00', 1, 0, 4)
        elif self.mutation == 'ping_wrong_length':
            return _frame(b'abc', 0, 0, 6)
        elif self.mutation == 'continuation_unexpected':
            return _frame(b'\x88', 0x4, stream_id, 9)
        elif self.mutation == 'zero_window_update':
            return _frame(b'\x00'*4, 0, 0, 8)
        elif self.mutation == 'rst_stream_zero':
            return _frame(b'\x00'*4, 0, 0, 3)
        elif self.mutation == 'invalid_padding':
            return _frame(b'\xff\x88', 0x0d, stream_id)
        else:
            raise AssertionError(self.mutation)
        return _frame(block, 0x5, stream_id)  # END_HEADERS | END_STREAM

    def _run(self) -> None:
        try:
            raw, _ = self.socket.accept()
            with raw, self.context.wrap_socket(raw, server_side=True) as tls:
                tls.settimeout(2)
                if tls.selected_alpn_protocol() != 'h2':
                    raise AssertionError('client did not negotiate HTTP/2')
                h2server = h2.connection.H2Connection(config=h2.config.H2Configuration(client_side=False))
                h2server.initiate_connection()
                tls.sendall(h2server.data_to_send())
                while True:
                    received = tls.recv(65536)
                    if not received:
                        break
                    events = h2server.receive_data(received)
                    response = None
                    for event in events:
                        if isinstance(event, h2.events.StreamEnded):
                            response = self._invalid_response(event.stream_id)
                    outgoing = h2server.data_to_send()
                    if outgoing or response:
                        tls.sendall(outgoing + (response or b''))
                    if response is not None:
                        # The frame must reach the HTTP/2 parser before EOF.
                        self.done.wait(.05)
                        break
        except BaseException as exc:
            self.failure = exc
        finally:
            self.done.set()
            self.socket.close()

    def close(self) -> None:
        self.done.set()
        self.worker.join(timeout=4)
        self.socket.close()


class InvalidH2PeerTests(unittest.TestCase):
    def test_invalid_response_header_frames_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _write_test_certificate(Path(directory))
            for mutation in MALFORMED_FRAMES:
                with self.subTest(mutation=mutation):
                    peer = _HostileH2Server(cert, key, mutation)
                    try:
                        with Client(ClientConfig(ca_bundle=str(cert),
                                                 connect_timeout_ms=1200,
                                                 total_timeout_ms=2500)) as client:
                            with self.assertRaises(HttpError):
                                response = client.send(Request('GET', peer.url))
                                response.body.bytes(4096)
                            self.assertEqual(client._reactor._buffer_budget.used, 0)
                        self.assertEqual(client._reactor._transfers, {})
                    finally:
                        peer.close()
                    self.assertIsNone(peer.failure, f'{mutation}: {peer.failure}')


if __name__ == '__main__':
    unittest.main()
