"""Independent h2-library TLS peer verifies the outbound libcurl HTTP/2 client.

This peer is NOT Valiance std.web. All requests stay on loopback; a temporary
self-signed server certificate is trusted explicitly by the test client.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import socket
import ssl
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from time import monotonic, sleep
from valiance.std._http_errors import ErrorKind, HttpError
import unittest

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import h2.config
    import h2.connection
    import h2.events
except ImportError as exc:
    raise unittest.SkipTest(f"independent HTTP/2 fixture dependencies missing: {exc}") from exc

from valiance.std._http_core import ClientConfig, Headers, Request, MultipartPart, encode_multipart_stream
from valiance.std._http_transport import Client
from valiance.std._http_body import UploadPipe


def _write_test_certificate(folder: Path, *, include_ip: bool = True,
                            expired: bool = False) -> tuple[Path, Path]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    not_before = now - timedelta(days=3) if expired else now - timedelta(minutes=2)
    not_after = now - timedelta(days=1) if expired else now + timedelta(days=1)
    san = [x509.DNSName('localhost')]
    if include_ip:
        san.append(x509.IPAddress(ipaddress.ip_address('127.0.0.1')))
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(private_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(not_before).not_valid_after(not_after)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(private_key, hashes.SHA256()))
    cert_path, key_path = folder / 'cert.pem', folder / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return cert_path, key_path


class H2Peer:
    """Small independent TLS h2 responder, with concurrent stream counters."""

    def __init__(self, cert_path: Path, key_path: Path) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(32)
        self.listener.settimeout(.15)
        self.base = f'https://127.0.0.1:{self.listener.getsockname()[1]}'
        self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._tls.load_cert_chain(cert_path, key_path)
        self._tls.set_alpn_protocols(['h2'])
        self.stop = Event()
        self.lock = Lock()
        self.connections = 0
        self.stream_ids: list[tuple[int, int]] = []
        self.errors: list[Exception] = []
        self.reset_ids: list[tuple[int, int]] = []
        self.workers: list[Thread] = []
        self.thread = Thread(target=self._accept, daemon=True)
        self.thread.start()

    def _accept(self) -> None:
        while not self.stop.is_set():
            try:
                raw, addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                self.connections += 1
                conn_id = self.connections
            worker = Thread(target=self._serve, args=(raw, conn_id), daemon=True)
            self.workers.append(worker)
            worker.start()

    def _serve(self, raw: socket.socket, conn_id: int) -> None:
        try:
            with self._tls.wrap_socket(raw, server_side=True) as stream:
                if stream.selected_alpn_protocol() != 'h2':
                    raise AssertionError('did not negotiate HTTP/2 via ALPN')
                stream.settimeout(3)
                conn = h2.connection.H2Connection(config=h2.config.H2Configuration(
                    client_side=False, header_encoding='utf-8'))
                conn.initiate_connection()
                stream.sendall(conn.data_to_send())
                metadata: dict[int, tuple[str, str]] = {}
                uploads: dict[int, bytearray] = {}
                barrier: list[tuple[int, bytes]] = []
                outbound: dict[int, bytes] = {}
                while not self.stop.is_set():
                    terminate_connection = False
                    try:
                        buffer = stream.recv(32768)
                    except socket.timeout:
                        continue
                    if not buffer:
                        return
                    events = conn.receive_data(buffer)
                    for event in events:
                        if isinstance(event, h2.events.RequestReceived):
                            head = dict(event.headers)
                            metadata[event.stream_id] = (head.get(':method', ''), head.get(':path', ''))
                            uploads[event.stream_id] = bytearray()
                            with self.lock:
                                self.stream_ids.append((conn_id, event.stream_id))
                        elif isinstance(event, h2.events.DataReceived):
                            uploads[event.stream_id].extend(event.data)
                            conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        elif isinstance(event, h2.events.StreamReset):
                            outbound.pop(event.stream_id, None)
                            with self.lock:
                                self.reset_ids.append((conn_id, event.stream_id))
                        elif isinstance(event, h2.events.StreamEnded):
                            method, path = metadata[event.stream_id]
                            body = bytes(uploads[event.stream_id])
                            payload = body if path == '/echo' else f'{method} {path}'.encode()
                            if path == '/goaway':
                                conn.send_headers(event.stream_id, [(':status', '200'),
                                    ('content-length', '3')])
                                conn.send_data(event.stream_id, b'bye', end_stream=True)
                                conn.close_connection(error_code=0,
                                                      last_stream_id=event.stream_id)
                                terminate_connection = True
                                continue
                            if path == '/reset-body':
                                # Emit ordinary final headers followed by a
                                # partial body and RST_STREAM. Another h2
                                # stream on this connection must stay healthy.
                                conn.send_headers(event.stream_id, [(':status', '200'),
                                    ('content-length', '100')])
                                conn.send_data(event.stream_id, b'partial')
                                conn.reset_stream(event.stream_id, error_code=0x2)
                                continue
                            if path == '/trailers':
                                conn.send_headers(event.stream_id, [(':status', '200'),
                                    ('content-length', '3')])
                                conn.send_data(event.stream_id, b'yes')
                                conn.send_headers(event.stream_id,
                                                  [('x-checksum', 'ok')], end_stream=True)
                                continue
                            if path == '/flow-controlled':
                                size = 320 * 1024
                                conn.send_headers(event.stream_id, [(':status', '200'),
                                    ('content-length', str(size))])
                                outbound[event.stream_id] = b'z' * size
                                continue
                            if path == '/hold':
                                conn.send_headers(event.stream_id, [(':status', '200'),
                                    ('content-length', '100')])
                                continue
                            if path.startswith('/barrier/'):
                                barrier.append((event.stream_id, payload))
                                if len(barrier) < 2:
                                    continue
                                for stream_id, response_body in barrier:
                                    conn.send_headers(stream_id, [(':status', '200'),
                                        ('content-length', str(len(response_body)))])
                                    conn.send_data(stream_id, response_body, end_stream=True)
                                barrier.clear()
                                continue
                            conn.send_headers(event.stream_id, [
                                (':status', '201' if method == 'POST' else '200'),
                                ('content-type', 'application/octet-stream'),
                                ('content-length', str(len(payload))),
                            ])
                            conn.send_data(event.stream_id, payload, end_stream=True)
                    # Drain only the available HTTP/2 stream + connection
                    # window, using peer-advertised frame sizes. WindowUpdate
                    # events then unlock the remaining response, rather than
                    # bypassing RFC 9113 flow control with one huge frame.
                    for stream_id, remaining in tuple(outbound.items()):
                        while remaining:
                            available = conn.local_flow_control_window(stream_id)
                            if available <= 0:
                                break
                            count = min(len(remaining), available,
                                        conn.max_outbound_frame_size)
                            conn.send_data(stream_id, remaining[:count],
                                           end_stream=count == len(remaining))
                            remaining = remaining[count:]
                        if remaining:
                            outbound[stream_id] = remaining
                        else:
                            del outbound[stream_id]
                    outgoing = conn.data_to_send()
                    if outgoing:
                        stream.sendall(outgoing)
                    if terminate_connection:
                        return
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, ssl.SSLError, OSError):
            pass  # Peer disconnect/cancellation is expected.
        except Exception as exc:
            with self.lock:
                self.errors.append(exc)

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)
        for worker in self.workers:
            worker.join(timeout=2)


class HTTP2IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cert, key = _write_test_certificate(Path(cls.temp.name))
        cls.cert_path = cert
        cls.peer = H2Peer(cert, key)

    @classmethod
    def tearDownClass(cls):
        cls.peer.close()
        cls.temp.cleanup()

    def test_automatic_http2_negotiation_and_binary_post(self):
        with Client(ClientConfig(ca_bundle=str(self.cert_path))) as client:
            response = client.send(Request('GET', self.peer.base + '/verify'))
            self.assertTrue(response.metadata.protocol.startswith('HTTP/2'))
            self.assertEqual(response.body.bytes(4096), b'GET /verify')
            binary = b'\x00\xff\x08body'
            reply = client.send(Request('POST', self.peer.base + '/echo',
                                        Headers((('Content-Type', 'application/octet-stream'),)),
                                        binary))
            self.assertEqual(reply.status, 201)
            self.assertEqual(reply.body.bytes(4096), binary)
            self.assertTrue(reply.metadata.protocol.startswith('HTTP/2'))
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_streamed_multipart_upload_over_http2(self):
        stream = UploadPipe(8, expected_bytes=7)
        body, ctype = encode_multipart_stream((
            MultipartPart('name', b'valiance', content_type='text/plain'),
            MultipartPart('file', stream, filename='data.bin', content_type='application/octet-stream'),
        ), boundary='test-boundary')
        with Client(ClientConfig(ca_bundle=str(self.cert_path), max_buffered_upload_bytes=1024)) as client:
            pending = client.send_async(Request('POST', self.peer.base + '/echo',
                                                Headers((('Content-Type', ctype),)), body))
            errors = []

            def producer():
                try:
                    for part in (b'\x00\xff', b'abc', b'\x0d\x0a'):
                        stream.write_async(part).result(timeout=5)
                    stream.finish()
                except BaseException as exc:
                    errors.append(exc)

            worker = Thread(target=producer, daemon=True)
            worker.start()
            response = pending.result(timeout=8)
            self.assertEqual(response.status, 201)
            self.assertTrue(response.metadata.protocol.startswith('HTTP/2'))
            wire = response.body.bytes(2048)
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors, errors)
            self.assertIn(b'name="name"\r\nContent-Type: text/plain\r\n\r\nvaliance', wire)
            self.assertIn(b'filename="data.bin"\r\nContent-Type: application/octet-stream\r\n\r\n\x00\xffabc\r\n', wire)
            self.assertTrue(wire.endswith(b'--test-boundary--\r\n'))
        self.assertTrue(stream.releasable)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_two_live_streams_multiplex_over_one_connection(self):
        config = ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                              max_connections_per_origin=1)
        with Client(config) as client:
            with ThreadPoolExecutor(max_workers=2) as pool:
                left = pool.submit(client.send, Request('GET', self.peer.base + '/barrier/a'))
                right = pool.submit(client.send, Request('GET', self.peer.base + '/barrier/b'))
                response_a = left.result(timeout=8)
                response_b = right.result(timeout=8)
            self.assertEqual(response_a.body.bytes(4096), b'GET /barrier/a')
            self.assertEqual(response_b.body.bytes(4096), b'GET /barrier/b')
        with self.peer.lock:
            barrier_connections = {conn for conn, stream in self.peer.stream_ids if stream >= 1}
            self.assertTrue(barrier_connections)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_connection_acquisition_deadline_accepts_multiplexed_http2_streams(self):
        # The connection identity is shared by both streams. A per-connection
        # deadline must not incorrectly treat the sibling as a new TCP socket.
        from valiance.std._http_transport import _curl
        if _curl().curl_version_info(0).contents.version_num < 0x080200:
            self.skipTest('connection identity requires libcurl >= 8.2')
        config = ClientConfig(ca_bundle=str(self.cert_path),
                              max_connections=1, max_connections_per_origin=1,
                              connection_acquire_timeout_ms=1500)
        with Client(config) as client:
            with ThreadPoolExecutor(max_workers=2) as pool:
                left = pool.submit(client.send, Request('GET', self.peer.base + '/barrier/acq-a'))
                right = pool.submit(client.send, Request('GET', self.peer.base + '/barrier/acq-b'))
                a, b = left.result(timeout=8), right.result(timeout=8)
            self.assertTrue(a.metadata.protocol.startswith('HTTP/2'))
            self.assertEqual(a.body.bytes(4096), b'GET /barrier/acq-a')
            self.assertEqual(b.body.bytes(4096), b'GET /barrier/acq-b')
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_cancel_one_h2_stream_without_breaking_a_sibling(self):
        config = ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                              max_connections_per_origin=1)
        with Client(config) as client:
            with self.peer.lock:
                before = len(self.peer.reset_ids)
            pending = client.send(Request('GET', self.peer.base + '/hold'))
            self.assertTrue(pending.metadata.protocol.startswith('HTTP/2'))
            pending.body.close()
            healthy = client.send(Request('GET', self.peer.base + '/healthy'))
            self.assertEqual(healthy.body.bytes(1024), b'GET /healthy')
            end = monotonic() + 2
            while monotonic() < end:
                with self.peer.lock:
                    if len(self.peer.reset_ids) > before:
                        break
                sleep(.02)
            with self.peer.lock:
                self.assertGreater(len(self.peer.reset_ids), before)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_http2_goaway_allows_new_connection_for_later_request(self):
        with self.peer.lock:
            before = self.peer.connections
        with Client(ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                                 max_connections_per_origin=1)) as client:
            first = client.send(Request('GET', self.peer.base + '/goaway'))
            self.assertEqual(first.body.bytes(100), b'bye')
            second = client.send(Request('GET', self.peer.base + '/after-goaway'))
            self.assertEqual(second.body.bytes(100), b'GET /after-goaway')
        with self.peer.lock:
            self.assertGreaterEqual(self.peer.connections - before, 2)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_remote_stream_reset_is_a_body_error_not_eof(self):
        with Client(ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                                 max_connections_per_origin=1)) as client:
            response = client.send(Request('GET', self.peer.base + '/reset-body'))
            self.assertEqual(response.status, 200)
            with self.assertRaises(HttpError) as caught:
                response.body.bytes(1024)
            self.assertIn(caught.exception.kind, (ErrorKind.TRANSFER, ErrorKind.PROTOCOL))
            healthy = client.send(Request('GET', self.peer.base + '/after-reset'))
            self.assertEqual(healthy.body.bytes(1024), b'GET /after-reset')
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_trailers_do_not_corrupt_body_completion(self):
        with Client(ClientConfig(ca_bundle=str(self.cert_path))) as client:
            response = client.send(Request('GET', self.peer.base + '/trailers'))
            self.assertEqual(response.body.bytes(1024), b'yes')
            self.assertEqual(response.metadata.status, 200)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_flow_controlled_response_resumes_across_many_windows(self):
        with Client(ClientConfig(ca_bundle=str(self.cert_path),
                                 body_buffer_bytes=16 * 1024,
                                 max_client_buffer_bytes=32 * 1024,
                                 max_body_bytes=512 * 1024)) as client:
            response = client.send(Request('GET', self.peer.base + '/flow-controlled'))
            self.assertEqual(response.metadata.protocol, 'HTTP/2')
            self.assertEqual(response.body.collect_async(400 * 1024).result(timeout=12),
                             b'z' * (320 * 1024))
            self.assertEqual(client._reactor._buffer_budget.used, 0)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_tls_unknown_ca_rejected_by_default(self):
        with Client(ClientConfig(connect_timeout_ms=1500, total_timeout_ms=3000)) as client:
            with self.assertRaises(HttpError) as failure:
                client.send(Request('GET', self.peer.base + '/'))
            self.assertEqual(failure.exception.kind, ErrorKind.TLS)

    def test_cancel_backpressured_http2_stream_without_poisoning_connection(self):
        config = ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                              max_connections_per_origin=1,
                              body_buffer_bytes=16 * 1024,
                              max_client_buffer_bytes=16 * 1024,
                              max_body_bytes=512 * 1024)
        with Client(config) as client:
            response = client.send(Request('GET', self.peer.base + '/flow-controlled'))
            self.assertEqual(response.status, 200)
            response.body.close()
            healthy = client.send(Request('GET', self.peer.base + '/after-backpressure'))
            self.assertEqual(healthy.body.bytes(1024), b'GET /after-backpressure')
            self.assertEqual(client._reactor._buffer_budget.used, 0)
        self.assertFalse(self.peer.errors, self.peer.errors)

    def test_tls_rejects_trusted_certificate_with_wrong_hostname(self):
        # Trusting a CA/cert must never disable independent hostname checks.
        with TemporaryDirectory() as folder:
            cert, key = _write_test_certificate(Path(folder), include_ip=False)
            peer = H2Peer(cert, key)
            try:
                with Client(ClientConfig(ca_bundle=str(cert),
                                         connect_timeout_ms=1500)) as client:
                    with self.assertRaises(HttpError) as caught:
                        client.send(Request('GET', peer.base + '/wrong-host'))
                    self.assertEqual(caught.exception.kind, ErrorKind.TLS)
            finally:
                peer.close()

    def test_tls_rejects_expired_certificate_even_when_trusted(self):
        with TemporaryDirectory() as folder:
            cert, key = _write_test_certificate(Path(folder), expired=True)
            peer = H2Peer(cert, key)
            try:
                with Client(ClientConfig(ca_bundle=str(cert),
                                         connect_timeout_ms=1500)) as client:
                    with self.assertRaises(HttpError) as caught:
                        client.send(Request('GET', peer.base + '/expired'))
                    self.assertEqual(caught.exception.kind, ErrorKind.TLS)
            finally:
                peer.close()

    def test_http2_connection_pool_and_independent_requests(self):
        with self.peer.lock:
            before_connections = self.peer.connections
        config = ClientConfig(ca_bundle=str(self.cert_path), max_connections=1,
                              max_connections_per_origin=1)
        with Client(config) as client:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(client.send, Request('GET',
                           self.peer.base + '/parallel-' + str(index))) for index in range(12)]
                responses = [future.result(timeout=15) for future in futures]
                self.assertEqual([response.body.bytes(4096) for response in responses],
                                 [f'GET /parallel-{i}'.encode() for i in range(12)])
                self.assertTrue(all(response.metadata.protocol.startswith('HTTP/2')
                                    for response in responses))
        self.assertFalse(self.peer.errors, self.peer.errors)
        with self.peer.lock:
            self.assertTrue(self.peer.stream_ids)
            # Count only connections made by THIS test, not preceding tests
            # that share the independently implemented peer fixture.
            self.assertLessEqual(self.peer.connections - before_connections, 2)


if __name__ == '__main__':
    unittest.main()
