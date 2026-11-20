"""Independent raw-wire HTTP/1.1 failure tests for std.http.

The peer deliberately does not use a correct HTTP server implementation:
malformed protocol frames must terminate as errors instead of being silently
accepted, hanging a body reader, or retaining a transfer's resources.
"""
from __future__ import annotations

import socketserver
import random
import threading
import unittest

from valiance.std import _http_core as core, _http_transport as transport
from valiance.std._http_errors import ErrorKind, HttpError


class _RawPeer(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(2)
        received = b''
        try:
            while b'\r\n\r\n' not in received and len(received) < 65536:
                data = self.request.recv(4096)
                if not data:
                    return
                received += data
            self.request.sendall(self.server.payload)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass


class _PeerServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class HTTPAdversarialTests(unittest.TestCase):
    def _exchange(self, wire: bytes) -> bytes:
        with _PeerServer(('127.0.0.1', 0), _RawPeer) as peer:
            peer.payload = wire
            thread = threading.Thread(target=lambda: peer.serve_forever(poll_interval=.02), daemon=True)
            thread.start()
            try:
                config = core.ClientConfig(connect_timeout_ms=1000, total_timeout_ms=2500)
                with transport.Client(config) as client:
                    response = client.send(core.Request('GET', f'http://127.0.0.1:{peer.server_address[1]}/'))
                    return response.body.bytes(4096)
            finally:
                peer.shutdown()
                thread.join(timeout=2)

    def test_seeded_truncated_body_corpus_never_becomes_successful_eof(self):
        # Deterministic mutation corpus against an independent raw-wire peer.
        # An accepted 200 with fewer than its advertised bytes is ALWAYS a
        # transfer failure, regardless of whether the peer closes between
        # header framing boundaries or at a short body length.
        randomizer = random.Random(20261120)
        for case in range(48):
            announced = randomizer.randint(1, 2048)
            actual = randomizer.randrange(announced)
            body = bytes(randomizer.getrandbits(8) for _ in range(actual))
            response = (b'HTTP/1.1 200 OK\r\nContent-Length: ' +
                        str(announced).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            with self.subTest(case=case, announced=announced, actual=actual):
                with self.assertRaises(HttpError) as caught:
                    self._exchange(response)
                self.assertIn(caught.exception.kind,
                              (ErrorKind.TRANSFER, ErrorKind.PROTOCOL))

    def test_truncated_content_length_is_not_successful_eof(self):
        wire = (b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n'
                b'\r\nshort')
        with self.assertRaises(HttpError) as caught:
            self._exchange(wire)
        self.assertIn(caught.exception.kind, (ErrorKind.TRANSFER, ErrorKind.PROTOCOL))

    def test_invalid_status_line_is_rejected(self):
        wire = b'BADPROTO 200 OK\r\nContent-Length: 2\r\n\r\nok'
        with self.assertRaises(HttpError):
            self._exchange(wire)

    def test_malformed_header_name_is_rejected(self):
        wire = b'HTTP/1.1 200 OK\r\nBad Name: value\r\nContent-Length: 2\r\n\r\nok'
        with self.assertRaises(HttpError):
            self._exchange(wire)

    def test_conflicting_content_lengths_fail_before_successful_body(self):
        wire = (b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 10\r\n'
                b'Connection: close\r\n\r\nok')
        with self.assertRaises(HttpError):
            self._exchange(wire)

    def test_interim_response_is_not_mistaken_for_final_headers(self):
        wire = (b'HTTP/1.1 103 Early Hints\r\nLink: </css>; rel=preload\r\n\r\n'
                b'HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\n'
                b'hello')
        self.assertEqual(self._exchange(wire), b'hello')

    def test_interim_response_without_final_headers_is_a_protocol_failure(self):
        wire = b'HTTP/1.1 103 Early Hints\r\nLink: </css>; rel=preload\r\n\r\n'
        with self.assertRaises(HttpError):
            self._exchange(wire)

    def test_truncated_chunked_transfer_never_reports_complete_body(self):
        wire = (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
                b'Connection: close\r\n\r\n5\r\nhello\r\n3\r\nx')
        with self.assertRaises(HttpError):
            self._exchange(wire)


if __name__ == '__main__':
    unittest.main()
