"""Opt-in incremental HTTP/1.1 request bodies, core ownership and Valiance bridge."""
from __future__ import annotations

import http.client
import socket
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core
from valiance.std import _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = 0
        self.read_calls = 0

    def read(self, maximum):
        self.read_calls += 1
        if not self.chunks:
            return None
        part = self.chunks.pop(0)
        self.chunks[0:0] = [part[maximum:]] if len(part) > maximum else []
        return part[:maximum]

    def close(self):
        self.closed += 1


class RequestStreamCoreTests(unittest.TestCase):
    def test_claim_once_and_discard_closes_source(self):
        src = Source([b'abc', b'\x00\xff', b'xyz'])
        req = core.test_request_stream('POST', '/upload', src)
        stream = core.body_stream(req)
        self.assertEqual(stream.read(3), b'abc')
        with self.assertRaises(core.WebError) as err:
            core.body_stream(req)
        self.assertEqual(err.exception.name, 'BodyUnavailable')
        stream.discard()
        self.assertTrue(stream.complete)
        self.assertEqual(src.closed, 1)
        stream.close()
        self.assertEqual(src.closed, 1)

    def test_reject_wrong_mode_and_body_helpers(self):
        with self.assertRaises(core.WebError):
            core.body_stream(core.test_request('POST', '/'))
        for method in ('GET','HEAD','DELETE','OPTIONS'):
            with self.assertRaises(core.DispatchError):
                core.test_request_stream(method, '/', Source([]))
        req = core.test_request_stream('QUERY', '/', Source([b'hello']),
                                       headers=core.Headers((('Content-Type','text/plain'),)))
        with self.assertRaises(core.WebError):
            core.body_bytes(req)
        core.body_stream(req).close()

    def test_size_bound_nonprogress_and_cleanup(self):
        stream = core.body_stream(core.test_request_stream(
            'PUT','/',Source([b'abc', b'def']),max_bytes=5))
        self.assertEqual(stream.read(3), b'abc')
        with self.assertRaises(core.WebError) as err:
            stream.discard()
        self.assertEqual(err.exception.name, 'BodyTooLarge')
        self.assertTrue(stream.closed)
        src = Source([b''] * 18)
        stream = core.body_stream(core.test_request_stream('PATCH','/',src))
        with self.assertRaises(core.WebError):
            for _ in range(18): stream.read(1)
        self.assertEqual(src.closed, 1)

    def test_route_precedence_and_scope_selection(self):
        app = core.App().route('POST','/r/{id}',lambda req:'buffered')
        app = app.route('GET','/r/new',lambda req:'static')
        app = app.with_body_mode('POST','/r/{id}','Stream')
        # Path specificity wins over method, so POST /r/new is not routed to /r/{id}.
        self.assertEqual(core.select_body_mode(app,'POST','/r/new'),'Buffered')
        self.assertEqual(core.select_body_mode(app,'POST','/r/x'),'Stream')
        self.assertEqual(core.select_body_mode(app,'POST','/missing'),'Buffered')
        with self.assertRaises(core.ConfigError):
            app.with_body_mode('GET','/r/new','Stream')
        with self.assertRaises(core.ConfigError):
            app.with_body_mode('POST','/missing','Stream')
        router = core.Router().route('PUT','/',lambda req:'ok').with_body_mode('PUT','/','Stream')
        app2 = core.App().group('/prefix',router)
        self.assertEqual(core.select_body_mode(app2,'PUT','/prefix'),'Stream')

    def test_synthetic_mode_mismatch_is_dispatch_error_and_closes_source(self):
        stream_app = core.App().route('POST','/',lambda req:'ok').with_body_mode('POST','/','Stream')
        with self.assertRaises(core.DispatchError):
            core.dispatch(stream_app,core.test_request('POST','/'))
        source=Source([b'ignored'])
        buffered_app=core.App().route('POST','/',lambda req:'ok')
        with self.assertRaises(core.DispatchError):
            core.dispatch(buffered_app,core.test_request_stream('POST','/',source))
        self.assertEqual(source.closed,1)

    def test_socket_free_dispatch_closes_after_partial_read(self):
        src = Source([b'abcdef'])
        app = core.App().route('POST','/', lambda req: core.body_stream(req).read(2).hex())
        app = app.with_body_mode('POST','/','Stream')
        response = core.dispatch(app, core.test_request_stream('POST','/',src))
        self.assertEqual(response.bytes, b'6162')
        self.assertEqual(src.closed, 1)


class RequestStreamHTTP1Tests(unittest.TestCase):
    def server(self, app, limits=None):
        server = http1.serve(app,port=0,limits=limits)
        self.addCleanup(server.shutdown, .5)
        return server

    def test_large_incremental_upload_and_buffered_admission(self):
        counts = []
        def consume(req):
            reader = core.body_stream(req)
            total = 0
            while (part := reader.read(16384)) is not None:
                self.assertLessEqual(len(part), 16384)
                total += len(part)
            counts.append(total)
            return str(total)
        app = core.App().route('POST','/stream',consume).with_body_mode('POST','/stream','Stream')
        app = app.route('POST','/buffer',lambda req: str(len(req.body)))
        server=self.server(app,http1.Limits(max_buffered_body_bytes=4,
                                            max_streaming_body_bytes=300000))
        client=http.client.HTTPConnection(*server.bound_address,timeout=4)
        self.addCleanup(client.close)
        data=b'\x00\x80\xff' * 80000
        client.request('POST','/stream',data)
        result=client.getresponse()
        self.assertEqual((result.status,result.read()),(200,str(len(data)).encode()))
        self.assertEqual(counts,[len(data)])
        # Same connection may be reused after reading through h11.EndOfMessage.
        client.request('POST','/buffer',b'12345')
        denied=client.getresponse()
        self.assertEqual(denied.status,413)
        denied.read()

    def test_chunked_upload_and_limit_error(self):
        def consume(req):
            reader=core.body_stream(req)
            parts=[]
            while (part:=reader.read(2)) is not None: parts.append(part)
            return core.Response(body=b''.join(parts))
        app=core.App().route('PATCH','/chunk',consume).with_body_mode('PATCH','/chunk','Stream')
        server=self.server(app,http1.Limits(max_streaming_body_bytes=7))
        client=http.client.HTTPConnection(*server.bound_address,timeout=4)
        self.addCleanup(client.close)
        client.request('PATCH','/chunk',body=[b'ab',b'cde',b'fg'],encode_chunked=True,
                       headers={'Transfer-Encoding':'chunked'})
        response=client.getresponse()
        self.assertEqual((response.status,response.read()),(200,b'abcdefg'))
        client.request('PATCH','/chunk',body=[b'abcdefgh'],encode_chunked=True,
                       headers={'Transfer-Encoding':'chunked'})
        result=client.getresponse()
        self.assertEqual(result.status,413)
        self.assertEqual(result.getheader('Connection'),'close')
        result.read()

    def test_early_return_closes_connection_and_never_processes_next_request(self):
        hits=[]
        def partial(req):
            hits.append('stream')
            return core.body_stream(req).read(2).decode()
        app=core.App().route('POST','/upload',partial).with_body_mode('POST','/upload','Stream')
        app=app.route('GET','/other',lambda _:hits.append('other') or 'oops')
        server=self.server(app)
        with socket.create_connection(server.bound_address,timeout=4) as sock:
            sock.settimeout(4)
            sock.sendall(b'POST /upload HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n'
                         b'abcdefghijGET /other HTTP/1.1\r\nHost: localhost\r\n\r\n')
            data=b''
            while (piece:=sock.recv(4096)):data+=piece
        self.assertIn(b'HTTP/1.1 200',data)
        self.assertIn(b'Connection: close',data)
        self.assertEqual(hits,['stream'])

    def test_declared_excess_rejected_before_handler(self):
        hits=[]
        app=core.App().route('POST','/',lambda req:hits.append(True) or 'ok')
        app=app.with_body_mode('POST','/','Stream')
        server=self.server(app,http1.Limits(max_streaming_body_bytes=3))
        conn=http.client.HTTPConnection(*server.bound_address,timeout=3)
        self.addCleanup(conn.close)
        conn.request('POST','/',body=b'abcdef')
        resp=conn.getresponse();self.assertEqual(resp.status,413);resp.read()
        self.assertEqual(hits,[])


class RequestStreamValianceTests(unittest.TestCase):
    SOURCE=r'''import {std.web, std.web.App}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $reader = web.bodyStream($req) ?
  $chunk = web.readBodyStream($reader, 3) ?
  web.discardBodyStream($reader) ?
  "accepted"
end
$app = web.withBodyMode($app, web.Method.POST, "/upload", web.BodyMode.Stream) ?
web.serve($app, web.ServerConfig(port = 0,
  limits = web.Limits(maxBufferedBodyBytes = 2, maxStreamingBodyBytes = 128))) ?'''

    def test_valiance_mounted_router_preserves_stream_mode(self):
        source=r'''import {std.web, std.web.App, std.web.Router}
$router = Router
$router := put("/"): fn(req: web.PutRequest) -> String | web.Error =>
  $reader = web.bodyStream($req) ?
  web.discardBodyStream($reader) ?
  "mounted"
end
$router = web.withBodyMode($router, web.Method.PUT, "/", web.BodyMode.Stream) ?
$app = App
$app := group("/api", $router)
web.serve($app, web.ServerConfig(port = 0,
  limits = web.Limits(maxBufferedBodyBytes = 1))) ?'''
        analyser=Analyser(); analysed=analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics],[])
        lease,=run(compile_program(analysed))
        token=bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                addr=bridge._SERVER_LEASES[token].bound_address
            client=http.client.HTTPConnection(*addr,timeout=4)
            try:
                client.request('PUT','/api',b'abcd')
                result=client.getresponse()
                self.assertEqual((result.status,result.read()),(200,b'mounted'))
            finally:client.close()
        finally:bridge._close_server_token(token,.5)

    def test_valiance_live_and_serialized_bytecode(self):
        for serialized in (False,True):
            with self.subTest(serialized=serialized):
                analyser=Analyser();analysed=analyser.analyse(parse(self.SOURCE))
                self.assertEqual([str(d) for d in analyser.diagnostics],[])
                program=compile_program(analysed,optimize=serialized)
                lease,=run(loads(dumps(program)) if serialized else program)
                token=bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        addr=bridge._SERVER_LEASES[token].bound_address
                    conn=http.client.HTTPConnection(*addr,timeout=4)
                    try:
                        conn.request('POST','/upload',b'\x00\xff' * 12)
                        response=conn.getresponse()
                        self.assertEqual((response.status,response.read()),(200,b'accepted'))
                    finally:conn.close()
                finally:bridge._close_server_token(token,.5)
        self.assertEqual(bridge._REQUEST_STREAM_LEASES,{})


if __name__=='__main__':
    unittest.main()
