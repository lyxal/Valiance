"""Owned pull-based HTTP/1.1 response streaming regression tests (v15)."""
from __future__ import annotations

from dataclasses import replace
import http.client
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, pieces):
        self.pieces = list(pieces)
        self.read_calls = 0
        self.closed = 0

    def read(self, max_bytes):
        self.read_calls += 1
        if not self.pieces:
            return None
        chunk = self.pieces.pop(0)
        if len(chunk) > max_bytes:
            self.pieces.insert(0, chunk[max_bytes:])
            return chunk[:max_bytes]
        return chunk

    def close(self):
        self.closed += 1


class StreamingCoreTests(unittest.TestCase):
    def test_bounded_collection_and_linear_consumption(self):
        producer = Source([b'A' * 40000, b'\x00\xff', b'B' * 40000])
        stream = core.stream(producer, 'application/octet-stream')
        response = core.Response(body=stream)
        self.assertEqual(response.headers.one('Content-Type'), 'application/octet-stream')
        with self.assertRaises(core.WebError):
            _ = response.bytes
        self.assertEqual(core.collect_body(response, 90000), b'A' * 40000 + b'\x00\xff' + b'B' * 40000)
        self.assertEqual(producer.closed, 1)
        with self.assertRaises(core.WebError):
            core.collect_body(response)

    def test_collect_limit_always_closes(self):
        producer = Source([b'abc', b'def'])
        with self.assertRaises(core.WebError) as err:
            core.collect_body(core.Response(body=core.stream(producer)), 4)
        self.assertEqual(err.exception.name, 'BodyTooLarge')
        self.assertEqual(producer.closed, 1)

    def test_head_does_not_consume_and_closes(self):
        producers = []
        def handler(_):
            producer = Source([b'payload'])
            producers.append(producer)
            return core.Response(body=core.stream(producer, length=7))
        app = core.App().route('GET', '/resource', handler)
        response = core.dispatch(app, core.test_request('HEAD', '/resource'))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.one('Content-Length'), '7')
        self.assertEqual(response.bytes, b'')
        self.assertEqual(producers[0].read_calls, 0)
        self.assertEqual(producers[0].closed, 1)
        producer = Source([b'data'])
        app = core.App().route('GET', '/', lambda _: core.Response(body=core.stream(producer)))
        head = core.dispatch(app, core.test_request('HEAD', '/'))
        self.assertIsNone(head.headers.one('Content-Length'))
        self.assertEqual(producer.closed, 1)

    def test_middleware_replacement_or_failure_closes_unused_stream(self):
        producer = Source([b'unused'])
        app = core.App().route('GET', '/', lambda _: core.Response(body=core.stream(producer)))
        app = app.use(lambda req: core.After(req, lambda res: core.Response(body='replaced')))
        result = core.dispatch(app, core.test_request('GET', '/'))
        self.assertEqual(result.bytes, b'replaced')
        self.assertEqual(producer.closed, 1)
        producer2 = Source([b'unused'])
        def fail(_):
            raise core.WebError('Forbidden', 'denied')
        app2 = core.App().route('GET', '/', lambda _: core.Response(body=core.stream(producer2)))
        app2 = app2.use(lambda req: core.After(req, fail))
        self.assertEqual(core.dispatch(app2, core.test_request('GET', '/')).status, 403)
        self.assertEqual(producer2.closed, 1)

    def test_conditional_304_and_compression_do_not_consume(self):
        producer = Source([b'unused'])
        def handler(request):
            return core.conditional(request, core.Response(body=core.stream(producer)).with_header('ETag', '"v1"'))
        app = core.App().route('GET', '/', handler)
        req = core.test_request('GET', '/', headers=core.Headers((('If-None-Match', '"v1"'),)))
        self.assertEqual(core.dispatch(app, req).status, 304)
        self.assertEqual(producer.closed, 1)
        fresh = Source([b'payload'])
        app2 = core.App().route('GET', '/', lambda _: core.Response(body=core.stream(fresh, 'text/plain')))
        app2 = app2.use(core.compression(core.CompressionPolicy(min_bytes=1)))
        resp = core.dispatch(app2, core.test_request('GET', '/', headers=core.Headers((('Accept-Encoding','gzip'),))))
        self.assertIsInstance(resp.body, core.StreamBody)
        self.assertIsNone(resp.headers.one('Content-Encoding'))
        self.assertEqual(core.collect_body(resp), b'payload')

    def test_reject_invalid_byte_source_and_nonprogress(self):
        for pieces in ([b'' for _ in range(18)], [b'good', 'bad']):
            source = Source(pieces)
            stream = core.stream(source)
            with self.assertRaises(core.WebError):
                core.collect_body(core.Response(body=stream), 100)
            self.assertEqual(source.closed, 1)

    def test_content_length_mismatch_before_or_during_collection(self):
        source = Source([b'abc'])
        response = core.Response(body=core.stream(source, length=2))
        with self.assertRaises(core.WebError):
            core.collect_body(response)
        self.assertEqual(source.closed, 1)
        other = core.Response(body=core.stream(Source([b'a']), length=1)).with_header('Content-Length','2')
        with self.assertRaises(core.WebError):
            core.validate_response(other)
        other.body.close()


class StreamingHTTP1Tests(unittest.TestCase):
    def test_live_chunked_http1_and_head(self):
        streams = []
        def handler(req):
            source = Source([b'hello', b'\x00\xff', b'world'])
            streams.append(source)
            return core.Response(body=core.stream(source, 'application/octet-stream'))
        server = http1.serve(core.App().route('GET','/chunked',handler), port=0)
        try:
            conn = http.client.HTTPConnection(*server.bound_address, timeout=3)
            try:
                conn.request('GET','/chunked')
                response = conn.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(response.getheader('Transfer-Encoding'),'chunked')
                self.assertEqual(response.read(),b'hello\x00\xffworld')
                conn.request('HEAD','/chunked')
                head=conn.getresponse()
                self.assertEqual(head.status,200)
                self.assertEqual(head.read(),b'')
            finally:
                conn.close()
            self.assertEqual([p.closed for p in streams], [1,1])
            self.assertEqual(streams[1].read_calls,0)
        finally:
            server.shutdown(0.5)

    def test_stream_error_after_headers_aborts_without_fake_500(self):
        class FailingSource(Source):
            def read(self, max_bytes):
                if self.read_calls == 0:
                    self.read_calls += 1
                    return b'prefix'
                raise core.WebError('Internal', 'private source failure')
        producer = FailingSource([])
        app = core.App().route('GET','/',lambda _: core.Response(body=core.stream(producer)))
        server = http1.serve(app,port=0)
        try:
            conn=http.client.HTTPConnection(*server.bound_address,timeout=4)
            try:
                conn.request('GET','/')
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                with self.assertRaises(http.client.IncompleteRead) as error:
                    response.read()
                self.assertIn(b'prefix',error.exception.partial)
                self.assertNotIn(b'500',error.exception.partial)
            finally:conn.close()
            self.assertEqual(producer.closed,1)
        finally:server.shutdown(0.5)

    def test_live_fixed_length_stream(self):
        app=core.App().route('GET','/',lambda _: core.Response(
            body=core.stream(Source([b'a',b'b']),length=2)))
        server=http1.serve(app,port=0)
        try:
            conn=http.client.HTTPConnection(*server.bound_address,timeout=3)
            try:
                conn.request('GET','/')
                response=conn.getresponse()
                self.assertEqual(response.getheader('Content-Length'),'2')
                self.assertEqual(response.read(),b'ab')
            finally:conn.close()
        finally:server.shutdown(0.5)


class StreamingValianceTests(unittest.TestCase):
    SOURCE=r'''import {std.web, std.file.#byte, std.concurrency}
public define \makeSource =>
  $counter = concurrency.State(0)
  web.ByteSource(read = fn(max: Int) -> #byte+ Int+ | None | web.Error =>
    if concurrency.get($counter) == 0 =>
      concurrency.set($counter, 1)
      [65 #byte, 66 #byte]
    else => \None end
  end, close = fn() -> None | web.Error => \None end)
end
'''

    def execute(self, src, *, serialized=False):
        analyzer=Analyser(); analysed=analyzer.analyse(parse(src))
        self.assertEqual([str(d) for d in analyzer.diagnostics],[])
        prog=compile_program(analysed,optimize=serialized)
        return run(loads(dumps(prog)) if serialized else prog)

    def test_native_collection_bytecode(self):
        for serial in (False,True):
            result=self.execute(self.SOURCE + '''web.collectBody(web.streamResponse(\\makeSource, "text/plain"), 16) ?''',serialized=serial)
            self.assertEqual([int(x) for x in result[0]],[65,66])

    def test_native_read_error_and_abandoned_stream_cleanup(self):
        source=r'''import {std.web, std.file.#byte}
$source = web.ByteSource(
  read = fn(max: Int) -> #byte+ Int+ | None | web.Error =>
    web.Error.BadRequest("stop")
  end,
  close = fn() -> None | web.Error => \None end
)
web.collectBody(web.streamResponse($source), 16)'''
        result,=self.execute(source)
        self.assertEqual(result.type_name, 'web.Error.BadRequest')
        before=len(bridge._STREAM_LEASES)
        abandoned=r'''import {std.web, std.file.#byte}
$source = web.ByteSource(
  read = fn(max: Int) -> #byte+ Int+ | None | web.Error => \None end,
  close = fn() -> None | web.Error => \None end
)
web.streamResponse($source) pop'''
        self.assertEqual(self.execute(abandoned),[])
        self.assertEqual(len(bridge._STREAM_LEASES), before)

    def test_native_live_server_and_middleware(self):
        src=self.SOURCE + r'''import {std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) -> web.Response =>
  web.streamResponse(\makeSource, "text/plain")
end
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) =>
    web.withHeader($res, "X-Test", "1")
  end}
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease,=self.execute(src,serialized=True)
        token=bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                bound=bridge._SERVER_LEASES[token].bound_address
            conn=http.client.HTTPConnection(*bound,timeout=4)
            try:
                conn.request('GET','/')
                response=conn.getresponse()
                self.assertEqual(response.status,200)
                self.assertEqual(response.getheader('X-Test'),'1')
                self.assertEqual(response.read(),b'AB')
            finally:conn.close()
        finally:bridge._close_server_token(token,0.5)
