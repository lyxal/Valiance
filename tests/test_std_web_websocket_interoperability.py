"""Independent wsproto RFC 6455 client over H1 and hyper-h2 extended CONNECT.

Unlike the raw-frame unit tests, all outbound/inbound WebSocket frames in this
suite are encoded and parsed by the third-party wsproto implementation. The
HTTP/2 transport remains hyper-h2 (not a browser or deployed reverse proxy).
"""
from __future__ import annotations

import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

import h2.config
import h2.connection
import h2.events
import h2.settings
from wsproto import WSConnection
from wsproto.connection import Connection as WSFrameConnection, ConnectionType
from wsproto.events import (AcceptConnection, BytesMessage, CloseConnection,
                            Ping, Pong, Request, TextMessage)

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_ws import WebSocketPolicy


class IndependentProtocolClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('openssl'):
            raise unittest.SkipTest('openssl needed for WSS/H2 certificate')
        cls.tmp = tempfile.TemporaryDirectory(prefix='wsproto-h2-')
        cls.cert = os.path.join(cls.tmp.name, 'cert.pem')
        cls.key = os.path.join(cls.tmp.name, 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '1',
                        '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, app):
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(server.shutdown, .6)
        return server

    def connect_tls(self, server, *, alpn):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols([alpn])
        sock = ctx.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                               server_hostname='localhost')
        sock.settimeout(3)
        self.assertEqual(sock.selected_alpn_protocol(), alpn)
        self.addCleanup(sock.close)
        return sock

    @staticmethod
    def echo(ws):
        while True:
            message = ws.receive()
            if message.kind == 'Closed':
                return
            if message.kind == 'Text':
                ws.send_text(message.value)
            elif message.kind == 'Binary':
                ws.send_bytes(message.value)

    def test_wsproto_independent_h1_tls_handshake_text_binary_ping_and_close(self):
        app = core.App().websocket('/ws', self.echo, WebSocketPolicy(
            subprotocols=('chat',), heartbeat_interval_seconds=0))
        server = self.start(app)
        sock = self.connect_tls(server, alpn='http/1.1')
        client = WSConnection(ConnectionType.CLIENT)
        sock.sendall(client.send(Request(host=f'localhost:{server.bound_address[1]}',
                                        target='/ws', subprotocols=['chat'],
                                        extra_headers=[(b'origin',
                                            f'https://localhost:{server.bound_address[1]}'.encode())])))
        accepted = []
        deadline = time.monotonic() + 4
        while not accepted and time.monotonic() < deadline:
            client.receive_data(sock.recv(4096))
            accepted.extend(e for e in client.events() if isinstance(e, AcceptConnection))
        self.assertEqual(accepted[0].subprotocol, 'chat')
        frames = (TextMessage('snowman \u2603'), BytesMessage(b'\x00\xfe\x01'), Ping(b'ok'))
        sock.sendall(b''.join(client.send(e) for e in frames))
        seen = []
        deadline = time.monotonic() + 4
        while len(seen) < 3 and time.monotonic() < deadline:
            client.receive_data(sock.recv(4096))
            seen += list(client.events())
        self.assertTrue(any(isinstance(e, TextMessage) and e.data == 'snowman \u2603' for e in seen), seen)
        self.assertTrue(any(isinstance(e, BytesMessage) and e.data == b'\x00\xfe\x01' for e in seen), seen)
        self.assertTrue(any(isinstance(e, Pong) and e.payload == b'ok' for e in seen), seen)
        sock.sendall(client.send(CloseConnection(code=1000, reason='done')))
        seen = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any(isinstance(e, CloseConnection) for e in seen):
            piece = sock.recv(4096)
            if not piece:
                break
            client.receive_data(piece)
            seen += list(client.events())
        self.assertTrue(any(isinstance(e, CloseConnection) and e.code == 1000 for e in seen), seen)

    def test_wsproto_over_h2_multiplexed_messages_and_concurrent_get(self):
        app = core.App().websocket('/ws', self.echo, WebSocketPolicy(
            heartbeat_interval_seconds=0, subprotocols=('chat',)))
        app = app.route('GET', '/ok', lambda r: 'independent')
        server = self.start(app)
        sock = self.connect_tls(server, alpn='h2')
        h2client = h2.connection.H2Connection(config=h2.config.H2Configuration(
            client_side=True, header_encoding='utf-8'))
        h2client.initiate_connection()
        sock.sendall(h2client.data_to_send())
        # A genuine peer must observe RFC 8441 opt-in before creating streams.
        deadline = time.monotonic() + 4
        while h2client.remote_settings[h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL] != 1:
            self.assertLess(time.monotonic(), deadline, 'server did not enable CONNECT')
            h2client.receive_data(sock.recv(65536))
            sock.sendall(h2client.data_to_send())
        wsclients = {sid: WSFrameConnection(ConnectionType.CLIENT) for sid in (1, 3, 5)}
        payloads = {1: 'unicode \u2603', 3: b'\xff\x00\x7f', 5: 'second'}
        for sid, frame_client in wsclients.items():
            h2client.send_headers(sid, [(':method','CONNECT'),(':protocol','websocket'),
                (':scheme','https'),(':authority','localhost'),(':path','/ws'),
                ('sec-websocket-version','13'), ('sec-websocket-protocol','chat')])
            value = payloads[sid]
            event = TextMessage(value) if isinstance(value, str) else BytesMessage(value)
            # Each independently encoded wsproto message includes client masking.
            h2client.send_data(sid, frame_client.send(event) + frame_client.send(Ping(b'p')))
        h2client.send_headers(7, [(':method','GET'),(':scheme','https'),
            (':authority','localhost'),(':path','/ok')], end_stream=True)
        sock.sendall(h2client.data_to_send())
        response_headers: dict[int, dict[str, str]] = {}
        ws_events: dict[int, list] = {sid: [] for sid in wsclients}
        http_body = bytearray()
        ended = set()
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            for event in h2client.receive_data(sock.recv(65536)):
                if isinstance(event, h2.events.ResponseReceived):
                    response_headers[event.stream_id] = dict(event.headers)
                if isinstance(event, h2.events.DataReceived):
                    if event.stream_id == 7:
                        http_body.extend(event.data)
                    else:
                        wsclients[event.stream_id].receive_data(event.data)
                        ws_events[event.stream_id].extend(wsclients[event.stream_id].events())
                    h2client.acknowledge_received_data(event.flow_controlled_length,
                                                       event.stream_id)
                if isinstance(event, h2.events.StreamEnded):
                    ended.add(event.stream_id)
            pending = h2client.data_to_send()
            if pending:
                sock.sendall(pending)
            if 7 in ended and all(any(isinstance(e, Pong) for e in ws_events[sid]) and
                                  any(isinstance(e, (TextMessage, BytesMessage)) for e in ws_events[sid])
                                  for sid in wsclients):
                break
        self.assertEqual(http_body, b'independent')
        self.assertEqual(response_headers[7][':status'], '200')
        for sid, expected in payloads.items():
            self.assertEqual(response_headers[sid][':status'], '200')
            self.assertEqual(response_headers[sid]['sec-websocket-protocol'], 'chat')
            data_events = [e.data for e in ws_events[sid] if isinstance(e, (TextMessage, BytesMessage))]
            self.assertEqual(data_events, [expected], ws_events[sid])
            self.assertEqual([e.payload for e in ws_events[sid] if isinstance(e, Pong)], [b'p'])
        # Finish the three WS streams.  The other HTTP/2 stream stays healthy.
        for sid, protocol in wsclients.items():
            h2client.send_data(sid, protocol.send(CloseConnection(code=1000)))
        sock.sendall(h2client.data_to_send())
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not set(wsclients) <= ended:
            for event in h2client.receive_data(sock.recv(65536)):
                if isinstance(event, h2.events.DataReceived):
                    h2client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.StreamEnded):
                    ended.add(event.stream_id)
            pending = h2client.data_to_send()
            if pending:
                sock.sendall(pending)
        self.assertTrue(set(wsclients) <= ended, ended)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = server.resource_snapshot()
            if snapshot['active_websockets'] == 0 and snapshot['reserved_bytes'] == 0:
                break
            time.sleep(.02)
        self.assertEqual(snapshot['active_websockets'], 0, snapshot)
        self.assertEqual(snapshot['reserved_bytes'], 0, snapshot)


if __name__ == '__main__':
    unittest.main()
