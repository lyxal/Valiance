"""RFC 8441 WebSocket streams: TLS/ALPN, subprotocols, framing and isolation."""
from __future__ import annotations

import os
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import shutil

import h2.config
import h2.connection
import h2.errors
import h2.events
import h2.settings

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_ws import WebSocketPolicy
from valiance.std._web_http2 import _request_headers


def maskframe(kind: int, data: bytes = b'', *, fin: bool = True) -> bytes:
    key = b'\xaa\xbb\xcc\xdd'
    header = bytes([(0x80 if fin else 0) | kind])
    if len(data) < 126:
        header += bytes([0x80 | len(data)])
    elif len(data) < 65536:
        header += bytes([0xfe]) + struct.pack('!H', len(data))
    else:
        header += bytes([0xff]) + struct.pack('!Q', len(data))
    return header + key + bytes(value ^ key[index & 3] for index, value in enumerate(data))


def decodeframes(data: bytes) -> list[tuple[int, bytes]]:
    result = []
    offset = 0
    while offset + 2 <= len(data):
        b0,b1 = data[offset:offset + 2]
        assert not b1 & 0x80, 'Server-to-client WebSocket frames must be unmasked'
        length = b1 & 0x7f
        consumed = 2
        if length == 126:
            if len(data) - offset < 4: break
            length = struct.unpack('!H', data[offset + 2:offset + 4])[0]
            consumed = 4
        elif length == 127:
            if len(data) - offset < 10: break
            length = struct.unpack('!Q', data[offset + 2:offset + 10])[0]
            consumed = 10
        if len(data) - offset < consumed + length: break
        result.append((b0 & 15, data[offset + consumed:offset + consumed + length]))
        offset += consumed + length
    return result


@unittest.skipUnless(shutil.which('openssl'), 'openssl required')
class H2WebSocketWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='valiance-ws-h2-')
        cls.cert = os.path.join(cls.tmp.name, 'cert.pem')
        cls.key = os.path.join(cls.tmp.name, 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '1',
                        '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, app, limits=None):
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key),
                             limits=limits)
        self.addCleanup(server.shutdown, .5)
        return server

    def connect(self, server):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(['h2'])
        sock = ctx.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                               server_hostname='localhost')
        sock.settimeout(4)
        self.assertEqual(sock.selected_alpn_protocol(), 'h2')
        self.addCleanup(sock.close)
        conn = h2.connection.H2Connection(config=h2.config.H2Configuration(
            client_side=True, header_encoding='utf-8',
            validate_outbound_headers=False))
        conn.initiate_connection()
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: any(
            isinstance(e, h2.events.RemoteSettingsChanged) for e in es))
        settings = conn.remote_settings
        self.assertEqual(settings[h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL], 1)
        return sock, conn

    def pump(self, sock, conn, *, until, timeout=5, ack=True):
        gathered = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(min(1.0, max(.05, deadline-time.monotonic())))
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            if not data:
                break
            events = conn.receive_data(data)
            for event in events:
                if ack and isinstance(event, h2.events.DataReceived):
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            gathered.extend(events)
            outgoing = conn.data_to_send()
            if outgoing: sock.sendall(outgoing)
            if until(gathered): break
        return gathered

    @staticmethod
    def wsheaders(path='/ws', origin=None, **overrides):
        result = [(':method','CONNECT'),(':protocol','websocket'),(':scheme','https'),
                  (':path',path),(':authority','localhost'),('sec-websocket-version','13')]
        if origin: result.append(('origin',origin))
        result.extend(overrides.items())
        return result

    def test_rfc8441_settings_subprotocol_and_concurrent_http2_get(self):
        def echo(ws):
            msg = ws.receive()
            ws.send_text(msg.value)
            ws.close()
        app = core.App().websocket('/ws', echo, WebSocketPolicy(subprotocols=('chat',)))
        app = app.route('GET', '/plain', lambda r: r.protocol)
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders(**{'sec-websocket-protocol':'chat'}))
        conn.send_headers(3, [(':method','GET'),(':scheme','https'),(':path','/plain'),
                              (':authority','localhost')], end_stream=True)
        conn.send_data(1, maskframe(1,b'Hello over HTTP/2'))
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: {1,3}.issubset({e.stream_id
            for e in es if isinstance(e,h2.events.StreamEnded)}))
        headers = {e.stream_id: dict(e.headers) for e in events if isinstance(e,h2.events.ResponseReceived)}
        bodies = {sid: b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                               and e.stream_id==sid) for sid in (1,3)}
        self.assertEqual(headers[1][':status'], '200')
        self.assertEqual(headers[1].get('sec-websocket-protocol'), 'chat')
        self.assertNotIn('sec-websocket-accept',headers[1])
        self.assertEqual(headers[3][':status'],'200')
        self.assertEqual(bodies[3],b'HTTP/2')
        self.assertEqual(decodeframes(bodies[1])[:2],[(1,b'Hello over HTTP/2'),(8,b'\x03\xe8')])

    def test_cookie_only_websocket_cannot_bypass_h2_auth_middleware(self):
        """WS-15: a cookie-only CONNECT is rejected without bearer credentials."""
        entered = []
        def auth(req):
            if req.method == 'WEBSOCKET' and req.headers.one('Authorization') != 'Bearer allow':
                return core.Response(401, 'authorization required')
            return req
        app = (core.App().websocket('/ws', lambda ws: (entered.append(1), ws.close()),
                                    WebSocketPolicy(heartbeat_interval_seconds=0))
               .route('GET', '/ok', lambda req: 'healthy')
               .use(auth))
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders(**{'cookie': 'session=trusted'}))
        conn.send_headers(3, self.wsheaders(**{'cookie': 'session=trusted',
                                             'authorization': 'Bearer wrong'}))
        conn.send_headers(5, self.wsheaders(**{'authorization': 'Bearer allow'}))
        conn.send_headers(7, [(':method', 'GET'), (':scheme', 'https'),
                              (':authority', 'localhost'), (':path', '/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=5, until=lambda es: {1,3,5,7} <= {
            e.stream_id for e in es if isinstance(e, h2.events.StreamEnded)})
        replies = {e.stream_id: dict(e.headers)[':status'] for e in events
                   if isinstance(e, h2.events.ResponseReceived)}
        self.assertEqual(replies, {1:'401', 3:'401', 5:'200', 7:'200'})
        self.assertEqual(entered, [1])
        self.assertEqual(b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                                  and e.stream_id == 7), b'healthy')

    def test_origin_and_version_rejection_are_stream_local(self):
        app = core.App().websocket('/ws', lambda ws: ws.close())
        app = app.route('GET','/ok',lambda r:'alive')
        server = self.start(app)
        sock,conn = self.connect(server)
        conn.send_headers(1,self.wsheaders(origin='https://outside.invalid'))
        conn.send_headers(3,self.wsheaders(**{'sec-websocket-version':'12'}))
        conn.send_headers(5,[(':method','GET'),(':scheme','https'),(':path','/ok'),
                             (':authority','localhost')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock,conn,until=lambda es: {1,3,5}.issubset({e.stream_id
            for e in es if isinstance(e,h2.events.StreamEnded)}))
        headers={e.stream_id:dict(e.headers) for e in events if isinstance(e,h2.events.ResponseReceived)}
        self.assertEqual(headers[1][':status'],'403')
        self.assertEqual(headers[3][':status'],'400')
        self.assertEqual(headers[5][':status'],'200')
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id == 5),b'alive')

    def test_graceful_goaway_during_active_websocket_is_bounded(self):
        """RFC 9113 §6.8: draining emits GOAWAY without waiting on a WS read."""
        entered = threading.Event()
        def blocking(ws):
            entered.set()
            try:
                ws.receive()
            except core.WebError:
                pass
        server = self.start(core.App().websocket('/ws', blocking,
            WebSocketPolicy(heartbeat_interval_seconds=0)))
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=4, until=lambda es: any(
            isinstance(e, h2.events.ResponseReceived) and e.stream_id == 1 for e in es))
        self.assertEqual(dict(next(e.headers for e in events if isinstance(
            e, h2.events.ResponseReceived) and e.stream_id == 1))[':status'], '200')
        self.assertTrue(entered.wait(2))
        stopper = threading.Thread(target=server.shutdown, args=(.6,), daemon=True)
        stopper.start()
        events = self.pump(sock, conn, timeout=3, until=lambda es: any(
            isinstance(e, h2.events.ConnectionTerminated) for e in es))
        messages = [e for e in events if isinstance(e, h2.events.ConnectionTerminated)]
        self.assertTrue(messages, events)
        self.assertEqual(messages[0].error_code, 0)
        self.assertEqual(messages[0].last_stream_id, 0x7fffffff)
        stopper.join(3)
        self.assertFalse(stopper.is_alive(), 'H2 shutdown exceeded bounded deadline')
        report = server.stop_diagnostics
        self.assertEqual(report['transport_sockets_open'], 0, report)
        self.assertEqual(report['transport_buffered_bytes'], 0, report)

    def test_masked_fragmentation_and_ping_pong(self):
        def echo(ws):
            msg = ws.receive()
            ws.send_bytes(msg.value)
            ws.close()
        server=self.start(core.App().websocket('/ws',echo))
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders())
        raw = maskframe(2,b'\x00\xfe',fin=False) + maskframe(9,b'?') + maskframe(0,b'\xff')
        conn.send_data(1,raw)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.StreamEnded)
            and e.stream_id==1 for e in es))
        body=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived))
        self.assertEqual(decodeframes(body),[(10,b'?'),(2,b'\x00\xfe\xff'),(8,b'\x03\xe8')])

    def test_reset_one_websocket_does_not_close_other_stream(self):
        entered=threading.Event()
        def blocking(ws):
            entered.set()
            try: ws.receive()
            except core.WebError: pass
        server=self.start(core.App().websocket('/ws',blocking).route('GET','/ok',lambda r:'yes'))
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders())
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.ResponseReceived)
                                                       and e.stream_id==1 for e in es))
        self.assertTrue(entered.wait(2))
        conn.reset_stream(1,error_code=h2.errors.ErrorCodes.CANCEL)
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),(':path','/ok'),
                            (':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.StreamEnded)
                                                        and e.stream_id==3 for e in es))
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==3),b'yes')

    def test_h2_wire_and_hpack_high_water_metrics_reclaim_admission(self):
        """WS-10: transport staging measurements are not quota reservations."""
        app=(core.App().websocket('/ws', lambda ws: ws.close(),
            WebSocketPolicy(heartbeat_interval_seconds=0))
            .route('GET','/ok',lambda _: 'healthy'))
        server=self.start(app)
        sock,conn=self.connect(server)
        conn.send_headers(1, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok'),
            ('x-hpack-test','dynamic-header-'*15)], end_stream=True)
        conn.send_headers(3, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok'),
            ('x-hpack-test','dynamic-header-'*15)], end_stream=True)
        conn.send_headers(5, self.wsheaders())
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,timeout=6,until=lambda es:
            {1,3,5} <= {e.stream_id for e in es if isinstance(e,h2.events.StreamEnded)})
        self.assertEqual({1,3,5}, {e.stream_id for e in events
            if isinstance(e,h2.events.StreamEnded)})
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            snapshot=server.resource_snapshot()
            if snapshot['active_requests']==0 and snapshot['reserved_bytes']==0:
                break
            time.sleep(.02)
        self.assertEqual(snapshot['active_requests'],0,snapshot)
        self.assertEqual(snapshot['reserved_bytes'],0,snapshot)
        self.assertGreater(snapshot['h2_tls_read_chunk_peak'],0)
        self.assertLessEqual(snapshot['h2_tls_read_chunk_peak'],16384)
        self.assertGreater(snapshot['h2_tls_write_batch_peak'],0)
        self.assertGreater(snapshot['h2_unparsed_frame_peak'],0)
        # HPACK static-only initial traffic is valid too; the repeated custom
        # field should cause the decoder to retain a dynamic table entry.
        self.assertGreater(snapshot['h2_hpack_dynamic_peak'],0,snapshot)
        self.assertLessEqual(snapshot['h2_hpack_dynamic_peak'],4096)

    def test_websocket_trailers_reset_only_own_h2_stream(self):
        """RFC 8441: a WebSocket DATA tunnel does not accept H2 trailers.

        The client is allowed to send trailing HEADERS at the H2 frame layer,
        but the WebSocket application must treat them as a protocol violation
        scoped to this stream; a multiplexed GET must still succeed.
        """
        def blocked(ws):
            try:
                ws.receive()
            except core.WebError:
                pass
        app = (core.App().websocket('/ws', blocked, WebSocketPolicy(
            heartbeat_interval_seconds=0)).route('GET','/ok', lambda _: 'untouched'))
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        accepted = self.pump(sock, conn, until=lambda es: any(
            isinstance(e,h2.events.ResponseReceived) and e.stream_id==1 for e in es))
        self.assertEqual({e.stream_id:dict(e.headers).get(':status') for e in accepted
            if isinstance(e,h2.events.ResponseReceived)}.get(1), '200')
        conn.send_headers(1, [('x-trailer', 'should-not-appear')], end_stream=True)
        conn.send_headers(3, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=6, until=lambda es:
            any(isinstance(e,h2.events.StreamReset) and e.stream_id==1 for e in es)
            and any(isinstance(e,h2.events.StreamEnded) and e.stream_id==3 for e in es))
        self.assertTrue(any(isinstance(e,h2.events.StreamReset) and e.stream_id==1
            for e in events), [(type(e).__name__,getattr(e,'stream_id',None)) for e in events])
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
            and e.stream_id==3), b'untouched')

    def test_h2_split_websocket_frame_header_64bit_oversize_and_http_get(self):
        """RFC 6455 announced oversized 64-bit frame closes 1009 pre-body."""
        def blocked(ws):
            try:
                ws.receive()
            except core.WebError:
                pass
        server = self.start(core.App().websocket('/ws', blocked, WebSocketPolicy(
            heartbeat_interval_seconds=0, max_frame_bytes=256))
            .route('GET','/ok',lambda _: 'alive'))
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        frame = b'\x82\xff' + struct.pack('!Q', 65536) + b'\xaa\xbb\xcc\xdd'
        # Each frame-header octet arrives as a separate HTTP/2 DATA frame.
        for byte in frame:
            conn.send_data(1, bytes((byte,)))
        conn.send_headers(3, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,timeout=6,until=lambda es:
            {1,3} <= {e.stream_id for e in es if isinstance(e,h2.events.StreamEnded)})
        body=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                      and e.stream_id==1)
        self.assertEqual(decodeframes(body), [(8,b'\x03\xf1')])
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
            and e.stream_id==3), b'alive')

    def test_large_bidirectional_binary_transfer_flow_control(self):
        payload = bytes(range(256)) * 700  # 179,200 octets, well over H2's default window
        def echo(ws):
            message = ws.receive()
            ws.send_bytes(message.value)
            ws.close()
        server = self.start(core.App().websocket('/ws', echo))
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders())
        sock.sendall(conn.data_to_send())
        frame = maskframe(2, payload)
        offset = 0
        deadline = time.monotonic() + 8
        while offset < len(frame) and time.monotonic() < deadline:
            window = conn.local_flow_control_window(1)
            size = min(window, conn.max_outbound_frame_size, 8192, len(frame)-offset)
            if size:
                conn.send_data(1, frame[offset:offset+size])
                offset += size
                sock.sendall(conn.data_to_send())
            else:
                self.pump(sock, conn, timeout=.4,
                          until=lambda events: conn.local_flow_control_window(1) > 0)
        self.assertEqual(offset,len(frame),'HTTP/2 client upload window never resumed')
        events = self.pump(sock, conn, timeout=8, until=lambda es:any(
            isinstance(e,h2.events.StreamEnded) and e.stream_id==1 for e in es))
        body = b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived))
        self.assertEqual(decodeframes(body), [(2,payload),(8,b'\x03\xe8')])

    def test_invalid_rfc6455_payload_isolated_to_one_h2_stream(self):
        def handler(ws):
            try: ws.receive()
            except core.WebError: pass
        app = core.App().websocket('/ws',handler).route('GET','/ok',lambda req:'healthy')
        server=self.start(app)
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders())
        conn.send_data(1,b'\x81\x01X') # unmasked client frame => protocol close 1002
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),(':authority','localhost'),
                             (':path','/ok')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:{1,3}.issubset({e.stream_id for e in es
                                               if isinstance(e,h2.events.StreamEnded)}))
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==3),b'healthy')
        body=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                      and e.stream_id==1)
        self.assertEqual(decodeframes(body),[(8,b'\x03\xea')])

    def test_extended_connect_header_parser_adversarial_matrix(self):
        """RFC 8441 §4 and RFC 9113 §8.3 pseudoheader/malformed-message rules."""
        base = [(b':method', b'CONNECT'), (b':protocol', b'websocket'),
                (b':scheme', b'https'), (b':authority', b'localhost'),
                (b':path', b'/ws'), (b'sec-websocket-version', b'13')]
        from valiance.std._web_http2 import _request_headers
        def replace_field(name, value):
            return [(k, value if k == name else v) for k, v in base]
        vectors = (
            ('missing-method', [x for x in base if x[0] != b':method']),
            ('missing-protocol', [x for x in base if x[0] != b':protocol']),
            ('missing-scheme', [x for x in base if x[0] != b':scheme']),
            ('missing-path', [x for x in base if x[0] != b':path']),
            ('missing-authority', [x for x in base if x[0] != b':authority']),
            ('empty-authority', replace_field(b':authority', b'')),
            ('wrong-scheme', replace_field(b':scheme', b'http')),
            ('wrong-method', replace_field(b':method', b'GET')),
            ('invalid-target', replace_field(b':path', b'/\x00')),
            ('extra-pseudo', base + [(b':other', b'bad')]),
            ('duplicate-pseudo', base + [(b':path', b'/ws')]),
            ('pseudo-after-regular', base + [(b':path', b'/ws')]),
            ('authority-host-conflict', base + [(b'host', b'evil.invalid')]),
            ('duplicate-content-length', base + [(b'content-length', b'0'),
                                                  (b'content-length', b'0')]),
            ('forbidden-hop-by-hop', base + [(b'connection', b'Upgrade')]),
            ('invalid-te', base + [(b'te', b'gzip')]),
            ('uppercase-field', base + [(b'Origin', b'https://localhost')]),
            ('leading-whitespace', base + [(b'origin', b' https://localhost')]),
            ('invalid-protocol-byte', replace_field(b':protocol', b'web socket')),
        )
        for label, fields in vectors:
            with self.subTest(label=label), self.assertRaises(core.WebError):
                _request_headers(fields, http1.Limits())
        valid = base + [(b'cookie', b'a=1'), (b'cookie', b'b=2'),
                        (b'te', b'trailers'), (b'host', b'localhost:443')]
        method, path, headers, authority = _request_headers(valid, http1.Limits())
        self.assertEqual((method, path, authority), ('CONNECT', '/ws', 'localhost'))
        self.assertEqual(headers.one('cookie'), 'a=1; b=2')

    def test_extended_connect_rejected_version_origin_and_subprotocol_are_stream_local(self):
        """Application-policy rejections return status, not TLS GOAWAY."""
        app = core.App().websocket('/ws', lambda ws: ws.close(),
            WebSocketPolicy(require_subprotocol=True, subprotocols=('chat',),
                            heartbeat_interval_seconds=0))
        app = app.route('GET', '/ok', lambda req: 'healthy')
        server = self.start(app)
        sock, conn = self.connect(server)
        vectors = (
            (1, {'sec-websocket-version': '12'}, '400'),
            (3, {'origin': 'https://attacker.invalid'}, '403'),
            (5, {'sec-websocket-protocol': 'chat,chat'}, '400'),
            (7, {'sec-websocket-protocol': 'bad token'}, '400'),
            (9, {}, '400'),
        )
        for sid, overrides, _ in vectors:
            conn.send_headers(sid, self.wsheaders(**overrides), end_stream=True)
        conn.send_headers(11, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=8, until=lambda es:
            {1,3,5,7,9,11}.issubset({e.stream_id for e in es
                if isinstance(e, h2.events.StreamEnded)}))
        statuses = {e.stream_id: dict(e.headers)[':status'] for e in events
                    if isinstance(e, h2.events.ResponseReceived)}
        for sid, _, expected in vectors:
            with self.subTest(stream=sid):
                self.assertEqual(statuses.get(sid), expected)
        self.assertEqual(statuses.get(11), '200')
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==11), b'healthy')

    def test_fragmented_binary_ping_parallel_h2_streams_with_http_get(self):
        """Deterministic cross-stream RFC 6455 fragmentation and Pong isolation."""
        import random
        def echo(ws):
            msg = ws.receive()
            ws.send_bytes(msg.value)
            ws.close()
        server = self.start(core.App().websocket('/ws', echo,
            WebSocketPolicy(heartbeat_interval_seconds=0))
            .route('GET','/ok',lambda _: 'healthy'))
        sock, conn = self.connect(server)
        expected = {}
        for index in range(16):
            sid = 2*index+1
            rng = random.Random(0x6455 + index)
            payload = rng.randbytes(40 + index*9)
            split = 1 + rng.randrange(len(payload)-1)
            expected[sid] = payload
            conn.send_headers(sid, self.wsheaders())
            conn.send_data(sid, maskframe(2,payload[:split],fin=False)
                +maskframe(9,b'!')+maskframe(0,payload[split:]))
        conn.send_headers(33,[(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock,conn,timeout=10,until=lambda es:
            set(expected)|{33} <= {e.stream_id for e in es
                if isinstance(e, h2.events.StreamEnded)})
        statuses={e.stream_id:dict(e.headers)[':status'] for e in events
                  if isinstance(e,h2.events.ResponseReceived)}
        for sid, payload in expected.items():
            with self.subTest(stream=sid):
                self.assertEqual(statuses.get(sid),'200')
                wire=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                              and e.stream_id==sid)
                self.assertEqual(decodeframes(wire),[(10,b'!'),(2,payload),(8,b'\x03\xe8')])
        self.assertEqual(statuses.get(33),'200')
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==33),b'healthy')

    def test_rfc6455_invalid_frames_are_independent_h2_stream_failures(self):
        """Malformed framing closes only its extended CONNECT stream."""
        def reader(ws):
            try: ws.receive()
            except core.WebError: pass
        server = self.start(core.App().websocket('/ws', reader,
            WebSocketPolicy(heartbeat_interval_seconds=0))
            .route('GET', '/ok', lambda _: 'healthy'))
        sock, conn = self.connect(server)
        vectors = ((1, b'\x81\x01X', 1002),
                   (3, b'\xc1\x80\x00\x00\x00\x00', 1002),
                   (5, maskframe(1, b'\xff'), 1007),
                   (7, maskframe(8, b'\x03'), 1002),
                   (9, b'\x82\xfe\x00\x7d', 1002))
        for sid, wire, _ in vectors:
            conn.send_headers(sid, self.wsheaders())
            conn.send_data(sid, wire)
        conn.send_headers(11, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=8, until=lambda es:
            {1,3,5,7,9,11}.issubset({e.stream_id for e in es
                if isinstance(e, h2.events.StreamEnded)}))
        headers = {e.stream_id: dict(e.headers) for e in events
                   if isinstance(e, h2.events.ResponseReceived)}
        for sid, _, code in vectors:
            with self.subTest(stream=sid):
                self.assertEqual(headers[sid][':status'], '200')
                wire = b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                and e.stream_id==sid)
                self.assertEqual(decodeframes(wire), [(8, struct.pack('!H', code))])
        self.assertEqual(headers[11][':status'], '200')
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==11), b'healthy')

    def test_mixed_seeded_rfc6455_errors_and_fragmentation_stay_stream_local(self):
        """RFC 6455/8441: mixed valid and invalid streams cannot corrupt peers."""
        import random
        def handler(ws):
            try:
                msg = ws.receive()
                if msg.kind == 'Binary':
                    ws.send_bytes(msg.value)
                ws.close()
            except core.WebError:
                pass
        app = core.App().websocket('/ws', handler, WebSocketPolicy(
            max_frame_bytes=256, max_message_bytes=512,
            heartbeat_interval_seconds=0)).route('GET', '/ok', lambda _: 'still healthy')
        server = self.start(app)
        sock, conn = self.connect(server)
        invalid = (
            (b'\x81\x01X', 1002),  # missing client masking
            (b'\xc1\x80\x00\x00\x00\x00', 1002),  # RSV1
            (maskframe(1, b'\xff'), 1007),
            (maskframe(1, b'\xe2', fin=False) + maskframe(0, b'x'), 1007),
            (maskframe(9, b'x', fin=False), 1002),
            (maskframe(8, b'\x03\xed'), 1002),  # prohibited 1005 code
            (b'\x82\xfe\x01\x01', 1009),  # oversize header, no body
            (b'\x82\xff\x80\x00\x00\x00\x00\x00\x00\x00', 1002),
        )
        expected = {}
        for index in range(16):
            sid = 2*index + 1
            conn.send_headers(sid, self.wsheaders())
            if index % 2 == 0:
                rng = random.Random(0x8441 + index)
                payload = rng.randbytes(37 + index*9)
                split = rng.randrange(1, len(payload))
                conn.send_data(sid, maskframe(2, payload[:split], fin=False) +
                               maskframe(9, b'!') + maskframe(0, payload[split:]))
                expected[sid] = [(10, b'!'), (2, payload), (8, b'\x03\xe8')]
            else:
                wire, code = invalid[index // 2]
                conn.send_data(sid, wire)
                expected[sid] = [(8, struct.pack('!H', code))]
        conn.send_headers(33, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, timeout=12, until=lambda es:
            set(expected) | {33} <= {e.stream_id for e in es
                if isinstance(e, h2.events.StreamEnded)})
        statuses = {e.stream_id: dict(e.headers)[':status'] for e in events
                    if isinstance(e, h2.events.ResponseReceived)}
        for sid, frames in expected.items():
            with self.subTest(stream=sid):
                self.assertEqual(statuses.get(sid), '200')
                wire = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                                and e.stream_id == sid)
                self.assertEqual(decodeframes(wire), frames)
        self.assertEqual(statuses.get(33), '200')
        self.assertEqual(b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                                  and e.stream_id == 33), b'still healthy')
        # H2 DATA can be acknowledged by the peer before the connection
        # worker has released its queued TLS-flush reservation. Qualify
        # eventual reclamation, not a racy instantaneous snapshot.
        deadline = time.monotonic() + 2
        while server.resource_snapshot()['reserved_bytes'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(server.resource_snapshot()['reserved_bytes'], 0)

    def test_compiled_valiance_websocket_runs_on_http2(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run
        from valiance.std import web as bridge
        from tests.test_std_web_v28_websocket_valiance import WebSocketValianceTests
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                analyser=Analyser()
                checked=analyser.analyse(parse(WebSocketValianceTests.SOURCE))
                self.assertEqual([str(x) for x in analyser.diagnostics],[])
                program=compile_program(checked,optimize=serialized)
                lease,=run(loads(dumps(program)) if serialized else program)
                token=bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        app=bridge._SERVER_LEASES[token]._server.web_app.app
                    server=self.start(app)
                    sock,conn=self.connect(server)
                    conn.send_headers(1,self.wsheaders('/echo'))
                    conn.send_data(1,maskframe(1,b'hello'))
                    sock.sendall(conn.data_to_send())
                    events=self.pump(sock,conn,until=lambda es:any(
                        isinstance(e,h2.events.StreamEnded) and e.stream_id==1 for e in es))
                    self.assertEqual(decodeframes(b''.join(e.data for e in events
                        if isinstance(e,h2.events.DataReceived) and e.stream_id==1)),
                        [(1,b'valiance'),(2,b'\x00\xff\x11'),(8,b'\x03\xe8')])
                    with bridge._WS_LOCK:
                        self.assertFalse(bridge._WS_LEASES)
                finally:
                    bridge._close_server_token(token, .5)

    def test_stalled_websocket_output_does_not_block_normal_http2(self):
        payload = b'Z' * 90000
        server = self.start(core.App().websocket('/ws',lambda ws:(ws.send_bytes(payload), ws.close()))
                            .route('GET','/ok',lambda req:'fast'))
        sock,conn=self.connect(server)
        conn.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 1024})
        conn.send_headers(1,self.wsheaders())
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),(':path','/ok'),
                             (':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,ack=False,until=lambda es:any(
            isinstance(e,h2.events.StreamEnded) and e.stream_id==3 for e in es))
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                   and e.stream_id==3),b'fast')
        early=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                       and e.stream_id==1)
        self.assertLessEqual(len(early),1024)
        conn.increment_flow_control_window(120000,stream_id=1)
        conn.increment_flow_control_window(120000)
        sock.sendall(conn.data_to_send())
        tail=self.pump(sock,conn,timeout=8,until=lambda es:any(
            isinstance(e,h2.events.StreamEnded) and e.stream_id==1 for e in es))
        body=early+b''.join(e.data for e in tail if isinstance(e,h2.events.DataReceived)
                            and e.stream_id==1)
        self.assertEqual(decodeframes(body),[(2,payload),(8,b'\x03\xe8')])

    def test_rejected_tunnel_variants_and_healthy_peer(self):
        called=[]
        server=self.start(core.App().websocket('/ws',lambda ws:called.append(1))
                          .route('GET','/ok',lambda req:'ok'))
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders(**{'content-length':'0'}))
        # Standard CONNECT remains unsupported, not an accidental websocket.
        conn.send_headers(3,[(':method','CONNECT'),(':authority','localhost')])
        conn.send_headers(5,[(':method','GET'),(':scheme','https'),(':authority','localhost'),
                            (':path','/ok')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:(
          {3,5}.issubset({e.stream_id for e in es if isinstance(e,h2.events.StreamEnded)})
          and any(isinstance(e,h2.events.StreamReset) and e.stream_id==1 for e in es)))
        headers={e.stream_id:dict(e.headers) for e in events
                 if isinstance(e,h2.events.ResponseReceived)}
        self.assertTrue(any(isinstance(e,h2.events.StreamReset) and e.stream_id==1
                            and e.error_code==h2.errors.ErrorCodes.PROTOCOL_ERROR for e in events))
        self.assertEqual(headers[3][':status'],'501')
        self.assertEqual(headers[5][':status'],'200')
        self.assertFalse(called)

    def test_malformed_pseudoheaders_are_stream_errors(self):
        app=core.App().websocket('/ws',lambda ws:ws.close())
        server=self.start(app)
        sock,conn=self.connect(server)
        invalid=[[(':method','GET'),(':protocol','websocket'),(':scheme','https'),
                  (':authority','localhost'),(':path','/ws')],
                 [(':method','CONNECT'),(':protocol','websocket'),
                  (':authority','localhost'),(':path','/ws')]]
        for i,headers in enumerate(invalid):
            conn.send_headers(i*2+1,headers)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es: len([e for e in es
              if isinstance(e,h2.events.StreamReset)])>=2)
        codes={e.stream_id:e.error_code for e in events if isinstance(e,h2.events.StreamReset)}
        self.assertEqual(codes,{1:h2.errors.ErrorCodes.PROTOCOL_ERROR,
                                3:h2.errors.ErrorCodes.PROTOCOL_ERROR})

    def test_opt_in_permessage_deflate_extended_connect_and_stream_isolation(self):
        import zlib
        def compressed(data):
            compressor = zlib.compressobj(wbits=-15)
            return (compressor.compress(data) +
                    compressor.flush(zlib.Z_SYNC_FLUSH))[:-4]
        def echo(ws):
            msg = ws.receive()
            ws.send_bytes(msg.value)
            ws.close()
        app = core.App().websocket('/ws', echo, WebSocketPolicy(
            extension_policy='PermessageDeflate', heartbeat_interval_seconds=0))
        app = app.route('GET', '/ok', lambda r: 'alive')
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders(**{
            'sec-websocket-extensions': 'permessage-deflate; client_max_window_bits'}))
        payload = bytes(range(256)) * 30
        frame = bytearray(maskframe(2, compressed(payload)))
        frame[0] |= 0x40
        conn.send_data(1, bytes(frame))
        conn.send_headers(3, [(':method','GET'),(':scheme','https'),
                              (':path','/ok'),(':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events = self.pump(sock, conn, until=lambda es: {1,3}.issubset({e.stream_id
            for e in es if isinstance(e, h2.events.StreamEnded)}))
        headers = {e.stream_id: dict(e.headers) for e in events
                   if isinstance(e, h2.events.ResponseReceived)}
        self.assertEqual(headers[1][':status'], '200')
        self.assertIn('client_no_context_takeover',
                      headers[1]['sec-websocket-extensions'])
        self.assertEqual(headers[3][':status'], '200')
        body = b''.join(e.data for e in events if isinstance(e, h2.events.DataReceived)
                        and e.stream_id == 1)
        self.assertTrue(body[0] & 0x40)
        frames = decodeframes(body)
        inflation = zlib.decompressobj(-15)
        self.assertEqual(inflation.decompress(frames[0][1] + b'\x00\x00\xff\xff'), payload)
        self.assertEqual(frames[-1], (8, b'\x03\xe8'))

    def test_deflate_binary_crosses_flow_windows_with_independent_get(self):
        import zlib
        payload = os.urandom(150000)
        compressor = zlib.compressobj(wbits=-15)
        compressed = (compressor.compress(payload) + compressor.flush(zlib.Z_SYNC_FLUSH))[:-4]
        def echo(ws):
            message = ws.receive()
            ws.send_bytes(message.value)
            ws.close()
        app = core.App().websocket('/ws', echo, WebSocketPolicy(
            extension_policy='PermessageDeflate', heartbeat_interval_seconds=0))
        app = app.route('GET','/ok',lambda req:'healthy')
        server = self.start(app)
        sock, conn = self.connect(server)
        conn.send_headers(1, self.wsheaders(**{
            'sec-websocket-extensions':'permessage-deflate; client_max_window_bits'}))
        frame = bytearray(maskframe(2, compressed))
        frame[0] |= 0x40
        sent = 0
        deadline = time.monotonic() + 10
        while sent < len(frame) and time.monotonic() < deadline:
            window = conn.local_flow_control_window(1)
            size = min(window, conn.max_outbound_frame_size, 8192, len(frame)-sent)
            if size:
                conn.send_data(1, frame[sent:sent+size])
                sent += size
                sock.sendall(conn.data_to_send())
            else:
                self.pump(sock, conn, timeout=.4,
                          until=lambda events: conn.local_flow_control_window(1) > 0)
        self.assertEqual(sent, len(frame))
        events = self.pump(sock, conn, timeout=10, until=lambda es:any(
            isinstance(e, h2.events.StreamEnded) and e.stream_id==1 for e in es))
        body = b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                        and e.stream_id==1)
        self.assertTrue(body and body[0] & 0x40)
        frames = decodeframes(body)
        data = b''.join(part for kind,part in frames if kind in (0,2))
        inflater = zlib.decompressobj(-15)
        self.assertEqual(inflater.decompress(data + b'\x00\x00\xff\xff'), payload)
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),
            (':path','/ok'),(':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        followup = self.pump(sock,conn,until=lambda es:any(
            isinstance(e,h2.events.StreamEnded) and e.stream_id==3 for e in es))
        self.assertEqual(b''.join(e.data for e in followup if isinstance(
            e,h2.events.DataReceived) and e.stream_id==3), b'healthy')

if __name__=='__main__':unittest.main()
