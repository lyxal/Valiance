"""Live RFC 6455 HTTP/1.1 upgrade + bounded frame acceptance tests."""
from __future__ import annotations

import base64
import os
import socket
import struct
import unittest
import ssl
import shutil
import tempfile
import subprocess

from websockets.sync.client import connect

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std._web_ws import WebSocketPolicy


class WebSocketH1Tests(unittest.TestCase):
    def start(self, app):
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .4)
        return server

    def url(self, server, path='/ws'):
        return f'ws://127.0.0.1:{server.bound_address[1]}{path}'

    def raw(self, server, path='/ws', *, origin=None, extra=b'', key=None):
        s = socket.create_connection(server.bound_address, timeout=3)
        s.settimeout(3)
        key = key if key is not None else base64.b64encode(os.urandom(16))
        raw = (b'GET ' + path.encode() + b' HTTP/1.1\r\nHost: 127.0.0.1:' +
               str(server.bound_address[1]).encode() + b'\r\nUpgrade: websocket\r\n'
               b'Connection: keep-alive, Upgrade\r\nSec-WebSocket-Version: 13\r\n'
               b'Sec-WebSocket-Key: ' + key + b'\r\n' +
               (b'Origin: ' + origin.encode() + b'\r\n' if origin else b'') + extra + b'\r\n')
        s.sendall(raw)
        response = bytearray()
        while b'\r\n\r\n' not in response:
            response.extend(s.recv(1))
        return s, bytes(response)

    @staticmethod
    def masked(opcode, payload=b'', fin=True):
        mask = b'\x01\x02\x03\x04'
        flag = 0x80 if fin else 0
        length = len(payload)
        extra = bytes([length | 0x80]) if length < 126 else (
            bytes([126 | 0x80]) + struct.pack('!H', length))
        return bytes([flag | opcode]) + extra + mask + bytes(
            value ^ mask[i % 4] for i, value in enumerate(payload))

    @staticmethod
    def frame(sock):
        a, b = sock.recv(2)
        if b == 126:
            length = struct.unpack('!H', sock.recv(2))[0]
        elif b == 127:
            length = struct.unpack('!Q', sock.recv(8))[0]
        else:
            length = b
        data = b''
        while len(data) < length:
            data += sock.recv(length - len(data))
        return a & 15, data

    def test_websockets_independent_client_text_binary_and_subprotocol(self):
        def echo(ws):
            while True:
                message = ws.receive()
                if message.kind == 'Closed':
                    return
                if message.kind == 'Text':
                    ws.send_text(message.value)
                else:
                    ws.send_bytes(message.value)
        app = core.App().websocket('/ws', echo, WebSocketPolicy(subprotocols=('chat',)))
        server = self.start(app)
        with connect(self.url(server), subprotocols=['chat'], open_timeout=3,
                     close_timeout=1) as sock:
            self.assertEqual(sock.subprotocol, 'chat')
            sock.send('hello \N{SNOWMAN}')
            self.assertEqual(sock.recv(), 'hello \N{SNOWMAN}')
            sock.send(b'\x00\xff\x00\x80')
            self.assertEqual(sock.recv(), b'\x00\xff\x00\x80')

    def test_routes_remain_distinct_group_mount_and_head(self):
        router = core.Router().websocket('/ws', lambda sock: sock.close())
        app = core.App().route('GET', '/ws', lambda req: 'normal').group('/v1', router)
        app = app.websocket('/ws', lambda sock: sock.close())
        server = self.start(app)
        import urllib.request
        for path in ('/ws', '/v1/ws'):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.bound_address[1]}{path}',timeout=3) as resp:
                    code, body = resp.status, resp.read()
            except Exception as exc:
                code = getattr(exc, 'code', None)
                body = b''
            if path == '/ws':
                self.assertEqual(code, 200)
                self.assertEqual(body, b'normal')
            else:
                self.assertEqual(code, 426)
            sock, raw = self.raw(server, path)
            self.assertIn(b'101 ', raw)
            self.assertEqual(self.frame(sock)[0], 8)
            sock.close()

    @unittest.skipUnless(shutil.which('openssl'), 'openssl needed for WSS fixture')
    def test_tls_wss_upgrade_and_same_origin(self):
        with tempfile.TemporaryDirectory(prefix='websocket-tls-') as directory:
            cert, key = os.path.join(directory, 'cert.pem'), os.path.join(directory, 'key.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', key, '-out', cert, '-days', '2',
                            '-subj', '/CN=localhost'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
            server = http1.serve(core.App().websocket('/ws',
                lambda ws: (ws.send_text(ws.receive().value), ws.close())),
                port=0, tls=http1.TLSConfig(cert, key))
            self.addCleanup(server.shutdown, .4)
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            url = f'wss://localhost:{server.bound_address[1]}/ws'
            with connect(url, ssl=ssl_ctx, open_timeout=3, close_timeout=1,
                         origin=f'https://localhost:{server.bound_address[1]}') as client:
                client.send('secure')
                self.assertEqual(client.recv(), 'secure')

    @unittest.skipUnless(shutil.which('chromium'), 'headless Chromium unavailable')
    def test_real_chromium_browser_opaque_origin_cannot_hijack_websocket(self):
        """An opaque-origin browser's Origin:null cannot reach an H1 handler."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('Playwright package unavailable')
        from threading import Event
        handler_entered = Event()
        def handler(ws):
            handler_entered.set()
            ws.close()
        server = self.start(core.App().websocket('/ws', handler))
        before = server._server.accepted_requests
        with sync_playwright() as browser_driver:
            browser = browser_driver.chromium.launch(headless=True,
                executable_path=shutil.which('chromium'),
                args=['--no-sandbox', '--no-proxy-server'])
            try:
                page = browser.new_page()
                page.goto('about:blank')
                outcome = page.evaluate("""async (port) => {
                    return await new Promise(resolve => {
                        const ws = new WebSocket('ws://127.0.0.1:' + port + '/ws');
                        ws.onopen = () => resolve('unexpected_open');
                        ws.onerror = () => resolve('rejected');
                        setTimeout(() => resolve('timeout'), 2500);
                    });
                }""", server.bound_address[1])
                self.assertEqual(outcome, 'rejected')
            finally:
                browser.close()
        # Require the browser attempt to reach the real H1 request parser;
        # a local browser networking policy failure alone is not sufficient.
        self.assertGreater(server._server.accepted_requests, before)
        self.assertFalse(handler_entered.is_set())

    @unittest.skipUnless(shutil.which('openssl'), 'openssl needed for TLS reverse-proxy fixture')
    def test_tls_terminating_reverse_proxy_origin_allowlist_and_forwarded_headers(self):
        """Untrusted Forwarded/X-Forwarded-Proto never broadens Origin policy."""
        import socketserver
        import threading

        def echo(ws):
            ws.close()
        app = core.App().websocket('/allowed', echo, WebSocketPolicy(
            allowed_origins=('https://front.example',)))
        app = app.websocket('/default', echo)
        backend = self.start(app)
        with tempfile.TemporaryDirectory(prefix='websocket-proxy-') as directory:
            cert, key = os.path.join(directory, 'cert.pem'), os.path.join(directory, 'key.pem')
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', key, '-out', cert, '-days', '1',
                            '-subj', '/CN=front.example'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
            frontend_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            frontend_ctx.load_cert_chain(cert, key)

            class Forwarder(socketserver.BaseRequestHandler):
                def handle(self):
                    try:
                        with self.server.tls_ctx.wrap_socket(self.request, server_side=True) as front:
                            with socket.create_connection(self.server.backend, timeout=3) as origin:
                                front.settimeout(3)
                                origin.settimeout(3)
                                def send_front_to_origin():
                                    try:
                                        while True:
                                            data = front.recv(4096)
                                            if not data:
                                                return
                                            origin.sendall(data)
                                    except (OSError, ssl.SSLError):
                                        pass
                                    finally:
                                        try: origin.shutdown(socket.SHUT_WR)
                                        except OSError: pass
                                sender = threading.Thread(target=send_front_to_origin, daemon=True)
                                sender.start()
                                try:
                                    while True:
                                        data = origin.recv(4096)
                                        if not data:
                                            break
                                        front.sendall(data)
                                except (OSError, ssl.SSLError):
                                    pass
                                sender.join(timeout=.1)
                    except (OSError, ssl.SSLError):
                        pass

            class Proxy(socketserver.ThreadingMixIn, socketserver.TCPServer):
                daemon_threads = True
                allow_reuse_address = True

            with Proxy(('127.0.0.1', 0), Forwarder) as proxy:
                proxy.tls_ctx = frontend_ctx
                proxy.backend = backend.bound_address
                thread = threading.Thread(target=proxy.serve_forever, kwargs={
                    'poll_interval': .01}, daemon=True)
                thread.start()
                try:
                    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    client_ctx.check_hostname = False
                    client_ctx.verify_mode = ssl.CERT_NONE
                    for path, origin, status in (
                        ('/allowed', 'https://front.example', 101),
                        ('/allowed', 'https://attacker.invalid', 403),
                        ('/default', 'https://front.example', 403),
                    ):
                        with self.subTest(path=path, origin=origin):
                            raw = socket.create_connection(proxy.server_address, timeout=3)
                            with client_ctx.wrap_socket(raw, server_hostname='front.example') as client:
                                client.settimeout(3)
                                nonce = base64.b64encode(os.urandom(16))
                                client.sendall(b'GET ' + path.encode() + b' HTTP/1.1\r\n'
                                    b'Host: front.example\r\n'
                                    b'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                                    b'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: ' +
                                    nonce + b'\r\nOrigin: ' + origin.encode() +
                                    b'\r\nForwarded: proto=https;host=front.example\r\n'
                                    b'X-Forwarded-Proto: https\r\n\r\n')
                                reply = bytearray()
                                while b'\r\n\r\n' not in reply:
                                    chunk = client.recv(1024)
                                    self.assertTrue(chunk, 'proxy closed before HTTP response')
                                    reply.extend(chunk)
                                self.assertIn(f' {status} '.encode(), bytes(reply).split(b'\r\n', 1)[0])
                finally:
                    proxy.shutdown()
                    thread.join(timeout=2)

    def test_origin_guard_and_explicit_allowlist(self):
        app = core.App().websocket('/ws', lambda ws: ws.close())
        server = self.start(app)
        sock, response = self.raw(server, origin='https://wrong.invalid')
        self.assertIn(b'403 ', response)
        sock.close()
        sock, response = self.raw(server, origin='http://127.0.0.1:' + str(server.bound_address[1]))
        self.assertIn(b'101 ', response)
        sock.close()
        app2 = core.App().websocket('/ws', lambda ws: ws.close(),
                                    WebSocketPolicy(allowed_origins=('https://allowed.example',)))
        server2 = self.start(app2)
        sock, response = self.raw(server2, origin='https://allowed.example')
        self.assertIn(b'101 ', response)
        sock.close()

    def test_explicit_zero_and_empty_origin_ports_fail_over_live_wire(self):
        server = self.start(core.App().websocket('/ws', lambda ws: ws.close()))
        for origin in ('http://127.0.0.1:0', 'http://127.0.0.1:'):
            with self.subTest(origin=origin):
                sock, response = self.raw(server, origin=origin)
                try:
                    self.assertIn(b'403 ', response)
                    self.assertNotIn(b'101 ', response)
                finally:
                    sock.close()
        sock, response = self.raw(server, origin=f'http://127.0.0.1:{server.bound_address[1]}')
        try:
            self.assertIn(b'101 ', response)
        finally:
            sock.close()

    def test_malformed_handshake_rejected_without_get_fallback(self):
        app = core.App().route('GET','/ws',lambda _: 'normal').websocket('/ws',lambda _: None)
        server = self.start(app)
        for extra, key in ((b'Sec-WebSocket-Key: xxx\r\n', None),
                           (b'Sec-WebSocket-Version: 12\r\n', None),
                           (b'', b'\x00-invalid')):
            with self.subTest(extra=extra, key=key):
                sock, resp = self.raw(server, extra=extra, key=key)
                self.assertNotIn(b'101 ', resp)
                self.assertNotIn(b'normal', resp)
                sock.close()

    def test_wire_handshake_negative_matrix_never_dispatches_get(self):
        """Malformed RFC 6455 upgrade must not reach an ordinary GET route."""
        app = core.App().route('GET', '/ws', lambda _: 'normal GET content')
        app = app.websocket('/ws', lambda ws: ws.close())
        server = self.start(app)
        vectors = (
            b'Sec-WebSocket-Key: duplicate\r\n',
            b'Sec-WebSocket-Version: 12\r\n',
            b'Origin: null\r\n',
            b'Origin: https://attacker.invalid\r\n',
            b'Origin: http://127.0.0.1/path\r\n',
            b'Origin: http://127.0.0.1\r\nOrigin: http://127.0.0.1\r\n',
            b'Sec-WebSocket-Protocol: chat,,alpha\r\n',
            b'Sec-WebSocket-Protocol: chat,chat\r\n',
            b'Content-Length: 1\r\n',
            b'Transfer-Encoding: chunked\r\n',
            b'Host: conflict.invalid\r\n',
        )
        for extra in vectors:
            with self.subTest(extra=extra):
                sock, response = self.raw(server, extra=extra)
                try:
                    self.assertNotIn(b' 101 ', response)
                    self.assertNotIn(b' 200 ', response)
                    self.assertNotIn(b'normal GET content', response)
                finally:
                    sock.close()

    def test_fragmented_masked_message_ping_and_close(self):
        def echo(ws):
            msg = ws.receive()
            ws.send_text(msg.value)
            ws.receive()
        server = self.start(core.App().websocket('/ws', echo))
        sock, raw = self.raw(server)
        self.assertIn(b'101 ', raw)
        sock.sendall(self.masked(1, b'hel', fin=False) + self.masked(9, b'?', fin=True) +
                     self.masked(0, b'lo', fin=True))
        self.assertEqual(self.frame(sock), (10, b'?'))
        self.assertEqual(self.frame(sock), (1, b'hello'))
        sock.sendall(self.masked(8, struct.pack('!H',1000)))
        self.assertEqual(self.frame(sock), (8, struct.pack('!H',1000)))
        sock.close()

    def test_unmasked_and_invalid_utf8_close_codes(self):
        for bad, code in ((b'\x81\x01x', 1002), (self.masked(1,b'\xff'), 1007)):
            def reader(ws):
                try: ws.receive()
                except core.WebError: pass
            server = self.start(core.App().websocket('/ws', reader))
            sock, raw = self.raw(server)
            self.assertIn(b'101 ', raw)
            sock.sendall(bad)
            opcode, body = self.frame(sock)
            self.assertEqual((opcode, struct.unpack('!H',body[:2])[0]),(8,code))
            sock.close()

    def test_independent_websocket_client_library(self):
        import websocket
        def mirror(sock):
            message = sock.receive()
            if message.kind == 'Binary':
                sock.send_bytes(message.value)
            else:
                sock.send_text(message.value)
        server = self.start(core.App().websocket('/ws', mirror))
        client = websocket.create_connection(self.url(server), timeout=3,
                                             http_proxy_host=None)
        try:
            client.send_binary(b'\x00\x80\xff')
            self.assertEqual(client.recv(), b'\x00\x80\xff')
        finally:
            client.close()

    def test_reject_oversized_frame_before_waiting_for_payload(self):
        def reader(sock):
            try:
                sock.receive()
            except core.WebError:
                pass
        server = self.start(core.App().websocket('/ws', reader,
            WebSocketPolicy(max_frame_bytes=8, max_message_bytes=32)))
        sock, headers = self.raw(server)
        self.assertIn(b'101 ', headers)
        # A 126-byte frame announces its length but sends no payload.
        # It must be rejected from the length prefix alone.
        sock.sendall(b'\x82\xfe\x00\x7e')
        opcode, body = self.frame(sock)
        self.assertEqual((opcode, struct.unpack('!H', body[:2])[0]), (8, 1009))
        sock.close()

    def test_fragmented_message_quota(self):
        def reader(sock):
            try:
                sock.receive()
            except core.WebError:
                pass
        server = self.start(core.App().websocket('/ws', reader,
            WebSocketPolicy(max_frame_bytes=5, max_message_bytes=8)))
        sock, headers = self.raw(server)
        self.assertIn(b'101 ', headers)
        sock.sendall(self.masked(1, b'hello', fin=False) + self.masked(0, b'world'))
        opcode, body = self.frame(sock)
        self.assertEqual((opcode, struct.unpack('!H', body[:2])[0]), (8, 1009))
        sock.close()

    def test_outgoing_large_message_is_fragmented_and_reassembled(self):
        payload = bytes(range(50))
        server = self.start(core.App().websocket('/ws', lambda sock: sock.send_bytes(payload),
            WebSocketPolicy(max_frame_bytes=8, max_message_bytes=64)))
        with connect(self.url(server), open_timeout=3, close_timeout=1) as client:
            self.assertEqual(client.recv(), payload)

    def test_early_masked_data_stays_in_upgraded_protocol(self):
        # A peer that pipelines first WebSocket bytes into the same TCP write
        # must never have them parsed as another HTTP request.
        def handler(ws):
            message = ws.receive()
            ws.send_bytes(message.value)
            ws.close()
        server = self.start(core.App().websocket('/ws', handler))
        s = socket.create_connection(server.bound_address, timeout=3)
        s.settimeout(3)
        key = base64.b64encode(os.urandom(16))
        data = (b'GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
                b'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                b'Sec-WebSocket-Key: ' + key + b'\r\n\r\n' +
                self.masked(2, b'\x00\xff\x80'))
        s.sendall(data)
        headers = bytearray()
        while not headers.endswith(b'\r\n\r\n'):
            headers.extend(s.recv(1))
        self.assertIn(b'101 ', headers)
        self.assertEqual(self.frame(s), (2, b'\x00\xff\x80'))
        s.close()

    def test_graceful_shutdown_cancels_idle_websocket(self):
        import time
        def reader(ws):
            try:
                ws.receive()
            except core.WebError:
                pass
        server = http1.serve(core.App().websocket('/ws', reader), port=0)
        s, response = self.raw(server)
        self.assertIn(b'101 ', response)
        start = time.monotonic()
        server.shutdown(.15)
        self.assertLess(time.monotonic() - start, 3)
        s.settimeout(2)
        # Supervised shutdown sends a best-effort Going Away close frame
        # before transport EOF when the writer is not blocked.
        self.assertEqual(self.frame(s), (8, b'\x03\xe9'))
        self.assertEqual(s.recv(1), b'')
        s.close()

    def test_browser_cookies_do_not_bypass_explicit_websocket_authentication(self):
        """WS-15: Origin filtering and cookie delivery are not authentication."""
        from threading import Event

        entered = []
        handler_entered = Event()

        def handler(ws):
            entered.append(1)
            handler_entered.set()
            ws.close()

        def auth(req):
            if req.method == 'WEBSOCKET' and req.headers.one('Authorization') != 'Bearer allow':
                return core.Response(401, 'authorization required')
            return req
        app = core.App().websocket('/ws', handler).use(auth)
        server = self.start(app)
        for extra, wanted in (
            (b'Cookie: session=trusted\r\n', b'401 '),
            (b'Cookie: session=trusted\r\nAuthorization: Bearer wrong\r\n', b'401 '),
            (b'Authorization: Bearer allow\r\n', b'101 '),
        ):
            sock, response = self.raw(server, extra=extra)
            try:
                self.assertIn(wanted, response)
                if wanted == b'401 ':
                    self.assertNotIn(b'101 ', response)
                else:
                    self.assertTrue(handler_entered.wait(timeout=3))
            finally:
                sock.close()
        self.assertEqual(entered, [1])

    def test_middleware_rejects_before_upgrade(self):
        calls=[]
        def auth(req):
            calls.append(req.path)
            return core.Response(401,'no')
        app=core.App().websocket('/ws',lambda ws: ws.close()).use(auth)
        server=self.start(app)
        sock,response=self.raw(server)
        self.assertIn(b'401 ',response)
        self.assertEqual(calls,['/ws'])
        sock.close()


if __name__ == '__main__':
    unittest.main()
