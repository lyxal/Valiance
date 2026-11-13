"""HTTP/2 byte-stream / multipart equivalence against the HTTP/1.1 parser.

True flow-controlled HTTP/2 DATA frames must be a contiguous request body to
std.web. The multipart parser must see exact binary octets and shared limits.
"""
from __future__ import annotations

import hashlib
import time
import unittest

import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
from tests.test_std_web_v19_multipart_stream import body, field, upload
import tests.test_std_web_v22_http2 as helpers


class HTTP2MultipartEquivalenceTests(unittest.TestCase):
    setUpClass = classmethod(helpers.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helpers.HTTP2TLSTests.tearDownClass.__func__)
    start = helpers.HTTP2TLSTests.start
    client = helpers.HTTP2TLSTests.client
    headers = staticmethod(helpers.HTTP2TLSTests.headers)

    def _post(self, sock, conn, sid, payload, timeout=7):
        conn.send_headers(sid, self.headers('/form', 'POST',
                          **{'content-type': 'multipart/form-data; boundary=XYZ',
                             'content-length': str(len(payload))}))
        body_sent = 0
        response = bytearray()
        status = None
        ended = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not ended:
            remaining = len(payload)-body_sent
            if remaining > 0:
                window = min(conn.local_flow_control_window(sid),
                             conn.max_outbound_frame_size, remaining, 8191)
                if window:
                    conn.send_data(sid, payload[body_sent:body_sent+window],
                                   end_stream=body_sent+window == len(payload))
                    body_sent += window
            outbound = conn.data_to_send()
            if outbound:
                sock.sendall(outbound)
            sock.settimeout(.25)
            try:
                raw = sock.recv(65536)
            except TimeoutError:
                continue
            if not raw:
                break
            for e in conn.receive_data(raw):
                if isinstance(e, h2.events.ResponseReceived) and e.stream_id == sid:
                    value = dict(e.headers).get(':status', dict(e.headers).get(b':status', b''))
                    status = value.decode('ascii') if isinstance(value, bytes) else value
                if isinstance(e, h2.events.DataReceived):
                    if e.stream_id == sid:
                        response.extend(e.data)
                    conn.acknowledge_received_data(e.flow_controlled_length, e.stream_id)
                if isinstance(e, h2.events.StreamEnded) and e.stream_id == sid:
                    ended = True
        self.assertEqual(body_sent, len(payload))
        self.assertTrue(ended, 'HTTP/2 multipart upload stalled')
        return status, bytes(response)

    def test_large_binary_multipart_exact_octets_and_field_order(self):
        data = (b'\x00\x80\xff\r\n--XYZQ-not-a-boundary' * 7500)
        expected = hashlib.sha256(data).hexdigest()
        def handler(req):
            parser = core.multipart_stream(req)
            labels = []
            total = 0
            digest = hashlib.sha256()
            while (part := parser.next_part()) is not None:
                if part.is_file:
                    while (chunk := parser.read_part(4096)) is not None:
                        digest.update(chunk)
                        total += len(chunk)
                else:
                    labels.append(part.value)
            return f'{labels}:{total}:{digest.hexdigest()}'
        app = (core.App().route('POST', '/form', handler)
               .with_body_mode('POST', '/form', 'Stream'))
        server = self.start(app)
        sock, conn = self.client(server)
        payload = body([field('label', b'one'), upload('data', data), field('label', b'two')])
        status, received = self._post(sock, conn, 1, payload)
        self.assertEqual(status, '200')
        self.assertEqual(received, f"['one', 'two']:{len(data)}:{expected}".encode())

    def test_configured_file_quota_returns_413_but_next_stream_survives(self):
        def handler(req):
            stream = core.multipart_stream(req)
            part = stream.next_part()
            while stream.read_part(512) is not None:
                pass
            stream.next_part()
            return part.filename
        app = (core.App().route('POST', '/form', handler)
               .route('GET', '/healthy', lambda req: 'healthy')
               .with_body_mode('POST', '/form', 'Stream'))
        server = self.start(app, http1.Limits(max_upload_file_bytes=1024))
        sock, conn = self.client(server)
        payload = body([upload('data', b'A' * 4096)])
        status, received = self._post(sock, conn, 1, payload)
        self.assertEqual(status, '413')
        conn.send_headers(3, self.headers('/healthy'), end_stream=True)
        sock.sendall(conn.data_to_send())
        reply = bytearray()
        ended = False
        deadline = time.monotonic() + 3
        while not ended and time.monotonic() < deadline:
            sock.settimeout(.25)
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            for event in conn.receive_data(data):
                if isinstance(event, h2.events.DataReceived):
                    if event.stream_id == 3:
                        reply.extend(event.data)
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                if isinstance(event, h2.events.StreamEnded) and event.stream_id == 3:
                    ended = True
        self.assertTrue(ended)
        self.assertEqual(bytes(reply), b'healthy')
