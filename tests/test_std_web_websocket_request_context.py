"""WebSocket upgrade request identity, middleware, and lifetime integration."""
from __future__ import annotations

from dataclasses import replace
import socket
import threading
import unittest

import h2.events

from valiance.std import _web_core as core
from tests import test_std_web_v28_websocket_h1 as ws_h1
from tests import test_std_web_v29_websocket_h2 as ws_h2
from tests.test_std_web_v29_websocket_h2 import decodeframes


class WebSocketRequestH1Tests(unittest.TestCase):
    # Reuse transport fixtures without inheriting their unrelated test cases.
    start = ws_h1.WebSocketH1Tests.start
    raw = ws_h1.WebSocketH1Tests.raw
    frame = staticmethod(ws_h1.WebSocketH1Tests.frame)
    def test_middleware_context_unwinds_before_h1_commit(self):
        order = []
        def before(req):
            order.append(('before', req.method, req.original_method))
            self.assertEqual(req.route_params, {'item': '42'})
            self.assertEqual(req.query.one('q'), 'ok')
            self.assertTrue(req.request_id)
            self.assertEqual(req.protocol, 'HTTP/1.1')
            return core.After(req.with_context('user', 'authorized'), lambda response: finish(response))
        def finish(response):
            order.append(('after', response.status))
            return response.with_header('X-Authorization', 'ok')
        def handler(ws):
            order.append(('handler', ws.request.context['user']))
            self.assertEqual(ws.request.method, 'WEBSOCKET')
            self.assertEqual(ws.request.original_method, 'GET')
            ws.send_text(ws.request.context['user'])
            ws.close()
        router = core.Router().websocket('/room/{item}', handler).use(before)
        server = self.start(core.App().group('/api', router))
        sock, response = self.raw(server, '/api/room/42?q=ok')
        self.assertIn(b'101 ', response)
        self.assertIn(b'X-Authorization: ok', response)
        self.assertEqual(self.frame(sock), (1, b'authorized'))
        self.assertEqual(self.frame(sock)[0], 8)
        self.assertEqual(order, [('before','WEBSOCKET','GET'), ('after',101),
                                  ('handler','authorized')])
        sock.close()

    def test_supervisor_abort_wakes_blocked_receive_without_heartbeat(self):
        from valiance.std._web_ws import WebSocket, WebSocketPolicy
        server_end, peer_end = socket.socketpair()
        server_end.settimeout(4)
        peer_end.settimeout(4)
        self.addCleanup(server_end.close)
        self.addCleanup(peer_end.close)
        request = replace(core.test_request('GET','/ws'), method='WEBSOCKET')
        owner = WebSocket(server_end, request, WebSocketPolicy())
        entered = threading.Event()
        finished = threading.Event()
        errors = []
        def receiver():
            entered.set()
            try:
                owner.receive()
            except core.WebError as exc:
                errors.append(exc.name)
            finally:
                finished.set()
        reader = threading.Thread(target=receiver, daemon=True)
        reader.start()
        self.assertTrue(entered.wait(1))
        owner.abort(1001)
        self.assertTrue(finished.wait(1), 'Cancellation did not wake blocked receiver')
        self.assertTrue(owner.cancelled)
        self.assertEqual(errors, ['BodyUnavailable'])
        reader.join(timeout=.5)

    def test_valiance_bridge_wraps_distinct_websocket_variant(self):
        from valiance.std import web as bridge
        request = replace(core.test_request('GET', '/ws/9?q=yes'),
                          method='WEBSOCKET', protocol='HTTP/1.1',
                          route_params={'item': '9'})
        wrapped = bridge._wrap_request(request)
        self.assertEqual(wrapped.type_name, 'web.Request.WebSocketRequest')
        restored = bridge._unwrap_request(wrapped)
        self.assertEqual(restored.method, 'WEBSOCKET')
        self.assertEqual(restored.original_method, 'GET')
        self.assertEqual(restored.route_params, {'item': '9'})
        self.assertEqual(restored.query.one('q'), 'yes')

    def test_identity_rewrite_rejected_before_h1_upgrade(self):
        invoked = []
        def corrupt(req):
            return replace(req, original_method='POST')
        server = self.start(core.App().websocket('/ws', lambda ws: invoked.append(1)).use(corrupt))
        sock, response = self.raw(server)
        self.assertIn(b'500 ', response)
        self.assertNotIn(b'101 ', response)
        self.assertEqual(invoked, [])
        sock.close()

    def test_after_rejection_blocks_h1_handler(self):
        invoked = []
        def auth(req):
            return core.After(req, lambda response: core.Response(403, 'denied'))
        server = self.start(core.App().websocket('/ws', lambda ws: invoked.append(1)).use(auth))
        sock, response = self.raw(server)
        self.assertIn(b'403 ', response)
        self.assertEqual(invoked, [])
        sock.close()


class WebSocketRequestH2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ws_h2.H2WebSocketWireTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        ws_h2.H2WebSocketWireTests.tearDownClass.__func__(cls)

    start = ws_h2.H2WebSocketWireTests.start
    connect = ws_h2.H2WebSocketWireTests.connect
    pump = ws_h2.H2WebSocketWireTests.pump
    wsheaders = staticmethod(ws_h2.H2WebSocketWireTests.wsheaders)
    def test_h2_upgrade_request_identity_context_and_after_header(self):
        order=[]
        def before(req):
            order.append(('before',req.method,req.original_method))
            self.assertEqual(req.protocol, 'HTTP/2')
            self.assertEqual(req.route_params, {'name':'lila'})
            return core.After(req.with_context('principal','member'),
                              lambda resp: resp.with_header('X-Context','ready'))
        def handler(ws):
            order.append(('handler', ws.request.original_method, ws.request.context['principal']))
            ws.send_text(ws.request.context['principal'])
            ws.close()
        app=core.App().group('/api', core.Router().websocket('/chat/{name}',handler).use(before))
        server=self.start(app)
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders(path='/api/chat/lila?c=1'))
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.StreamEnded)
                                                       and e.stream_id==1 for e in es))
        headers={k:v for e in events if isinstance(e,h2.events.ResponseReceived)
                 for k,v in e.headers}
        payload=b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived))
        self.assertEqual(headers[':status'], '200')
        self.assertEqual(headers['x-context'], 'ready')
        self.assertEqual(decodeframes(payload), [(1,b'member'), (8,b'\x03\xe8')])
        self.assertEqual(order, [('before','WEBSOCKET','CONNECT'),
                                 ('handler','CONNECT','member')])

    def test_h2_peer_reset_cancels_only_the_websocket_reader(self):
        import h2.errors
        entered=threading.Event()
        exited=threading.Event()
        observed=[]
        def handler(ws):
            entered.set()
            try:
                ws.receive()
            except core.WebError:
                observed.append(ws.cancelled)
            finally:
                exited.set()
        app=core.App().websocket('/ws',handler).route('GET','/ok',lambda req:'still here')
        server=self.start(app)
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders())
        sock.sendall(conn.data_to_send())
        self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.ResponseReceived)
                                                 and e.stream_id==1 for e in es))
        self.assertTrue(entered.wait(2))
        conn.reset_stream(1,error_code=h2.errors.ErrorCodes.CANCEL)
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),(':path','/ok'),
                             (':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:any(isinstance(e,h2.events.StreamEnded)
                                                       and e.stream_id==3 for e in es))
        self.assertEqual(b''.join(e.data for e in events if isinstance(e,h2.events.DataReceived)
                                  and e.stream_id==3), b'still here')
        self.assertTrue(exited.wait(2), 'Reset stream did not wake receiver')
        self.assertEqual(observed,[True])

    def test_h2_middleware_denial_is_stream_local(self):
        seen=[]
        def auth(req):
            seen.append(req.method)
            return core.Response(403,'denied') if req.method == 'WEBSOCKET' else req
        app=(core.App().websocket('/ws',lambda ws:seen.append('handler'))
             .route('GET','/ok',lambda req:'alive').use(auth))
        server=self.start(app)
        sock,conn=self.connect(server)
        conn.send_headers(1,self.wsheaders())
        conn.send_headers(3,[(':method','GET'),(':scheme','https'),(':path','/ok'),
                              (':authority','localhost')],end_stream=True)
        sock.sendall(conn.data_to_send())
        events=self.pump(sock,conn,until=lambda es:{1,3}.issubset({e.stream_id for e in es
                 if isinstance(e,h2.events.StreamEnded)}))
        headers={e.stream_id:dict(e.headers) for e in events if isinstance(e,h2.events.ResponseReceived)}
        self.assertEqual(headers[1][':status'],'403')
        self.assertEqual(headers[3][':status'],'200')
        self.assertCountEqual(seen,['WEBSOCKET','GET'])



if __name__=='__main__':
    unittest.main()
