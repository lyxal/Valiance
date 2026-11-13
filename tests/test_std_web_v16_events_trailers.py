"""v16 SSE and HTTP/1.1 trailer compliance regression coverage."""
import http.client
import socket
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Events:
    def __init__(self, values):
        self.values = list(values)
        self.closed = 0
        self.reads = 0

    def next(self):
        self.reads += 1
        return self.values.pop(0) if self.values else None

    def close(self):
        self.closed += 1


class Bytes:
    def __init__(self, parts):
        self.parts = list(parts)
        self.closed = 0

    def read(self, n):
        return self.parts.pop(0) if self.parts else None

    def close(self):
        self.closed += 1


class EventCoreTests(unittest.TestCase):
    def test_multiline_unicode_fields_and_blank_lines(self):
        source = Events([core.Event('hello\r\nsecond\rthird\n', event='notice', id='e:1', retry_ms=2500),
                         core.Event('🌍')])
        response = core.event_stream(source)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.one('Content-Type'), 'text/event-stream; charset=utf-8')
        self.assertEqual(response.headers.one('Cache-Control'), 'no-cache')
        self.assertEqual(response.headers.one('X-Accel-Buffering'), 'no')
        self.assertEqual(core.collect_body(response), (
            'event: notice\nid: e:1\nretry: 2500\ndata: hello\ndata: second\n'
            'data: third\ndata: \n\ndata: 🌍\n\n').encode())
        self.assertEqual(source.closed, 1)
        self.assertEqual(source.reads, 3)

    def test_sse_validation_and_bounded_event_size(self):
        for kw in ({'event':'ok\nretry: 1'}, {'id':'x\rdata: injected'},
                   {'id':'nul\x00'}, {'retry_ms':-1}, {'retry_ms':2**40}):
            with self.assertRaises(core.WebError):
                core.Event('hello', **kw)
        source = Events([core.Event('x' * 66000)])
        with self.assertRaises(core.WebError) as raised:
            core.collect_body(core.event_stream(source), 100000)
        self.assertEqual(raised.exception.name, 'BodyTooLarge')
        self.assertEqual(source.closed, 1)

    def test_head_middleware_and_replacement_close_event_source(self):
        sources = []
        def handler(_):
            source = Events([core.Event('hi')]); sources.append(source)
            return core.event_stream(source)
        app = core.App().route('GET', '/', handler)
        self.assertEqual(core.dispatch(app, core.test_request('HEAD', '/')).bytes, b'')
        self.assertEqual((sources[0].reads, sources[0].closed), (0, 1))
        app2 = app.use(lambda req: core.After(req, lambda _: core.Response(body='replaced')))
        self.assertEqual(core.dispatch(app2, core.test_request('GET', '/')).bytes, b'replaced')
        self.assertEqual((sources[1].reads, sources[1].closed), (0, 1))

    def test_trailer_validation_and_no_body(self):
        for trailers in (core.Headers((('Content-Length','7'),)),
                         core.Headers((('Set-Cookie','x=1'),)),
                         core.Headers((('X-Test','yes'), ('x-test','duplicate')))):
            if trailers.pairs[0][0] == 'X-Test':
                # Repeated non-singleton trailer fields are legal.
                continue
            source = Bytes([b'foo'])
            with self.assertRaises(core.WebError):
                core.with_trailers(core.Response(body=core.stream(source)), trailers)
            source.close()
        with self.assertRaises(core.WebError):
            core.with_trailers(core.Response(body='buffer'), core.Headers((('X-Digest','x'),)))
        with self.assertRaises(core.WebError):
            core.with_trailers(core.Response(body=core.stream(Bytes([]))), core.Headers())
        with self.assertRaises(core.WebError):
            core.with_trailers(core.Response(body=core.stream(Bytes([]))),
                               core.Headers((('X-Test', '🙃'),)))
        with self.assertRaises(core.WebError):
            core.with_trailers(core.Response(body=core.stream(Bytes([])),
                headers=core.Headers((('Content-Length','0'),))),core.Headers((('X-Digest','foo'),)))

    def test_trailers_are_preserved_through_metadata_and_suppressed_on_head(self):
        sources=[]
        def handler(_):
            source=Bytes([b'ab']); sources.append(source)
            return core.with_trailers(core.Response(body=core.stream(source)),
                                      core.Headers((('X-Checksum','abc'),)))
        app=core.App().route('GET','/',handler).use(lambda req: core.After(
            req,lambda res: res.with_header('X-Added','yes')))
        response=core.dispatch(app,core.test_request('GET','/'))
        self.assertEqual(response.trailers.one('X-Checksum'),'abc')
        self.assertEqual(response.headers.one('X-Added'),'yes')
        self.assertEqual(core.collect_body(response),b'ab')
        head=core.dispatch(app,core.test_request('HEAD','/'))
        self.assertEqual(head.trailers.pairs,())
        self.assertEqual((sources[1].closed),1)


class EventHTTPTests(unittest.TestCase):
    def test_live_sse_and_disconnect_cleanup(self):
        sources=[]
        def handler(_):
            source=Events([core.Event('hello'),core.Event('world',id='2')]);sources.append(source)
            return core.event_stream(source)
        server=http1.serve(core.App().route('GET','/events',handler),port=0)
        try:
            conn=http.client.HTTPConnection(*server.bound_address,timeout=4)
            try:
                conn.request('GET','/events',headers={'Last-Event-ID':'1'})
                resp=conn.getresponse()
                self.assertEqual(resp.status,200)
                self.assertEqual(resp.getheader('Transfer-Encoding'),'chunked')
                self.assertEqual(resp.getheader('Content-Type'),'text/event-stream; charset=utf-8')
                self.assertEqual(resp.read(),b'data: hello\n\nid: 2\ndata: world\n\n')
            finally:conn.close()
            self.assertEqual(sources[0].closed,1)
        finally:server.shutdown(0.5)

    def test_live_stream_trailers_and_chunk_terminator(self):
        producer=Bytes([b'abc',b'def'])
        app=core.App().route('GET','/',lambda _: core.with_trailers(
            core.Response(body=core.stream(producer,length=6)),
            core.Headers((('X-Checksum','xyz'),('X-Count','2')))))
        server=http1.serve(app,port=0)
        try:
            with socket.create_connection(server.bound_address,timeout=4) as sock:
                sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                sock.settimeout(4)
                data=b''
                while True:
                    piece=sock.recv(4096)
                    if not piece:break
                    data+=piece
            self.assertIn(b'Transfer-Encoding: chunked',data)
            self.assertIn(b'Trailer: x-checksum, x-count',data)
            self.assertNotIn(b'Content-Length:',data)
            self.assertIn(b'3\r\nabc\r\n3\r\ndef\r\n0\r\n',data)
            self.assertIn(b'X-Checksum: xyz\r\n',data)
            self.assertIn(b'X-Count: 2\r\n',data)
            self.assertEqual(producer.closed,1)
        finally:server.shutdown(0.5)

    def test_fail_after_commit_sends_no_success_trailers(self):
        class Fails(Bytes):
            def read(self,n):
                if self.parts:return self.parts.pop(0)
                raise core.WebError('Internal','private')
        producer=Fails([b'prefix'])
        app=core.App().route('GET','/',lambda _: core.with_trailers(
            core.Response(body=core.stream(producer)),core.Headers((('X-Done','yes'),))))
        server=http1.serve(app,port=0)
        try:
            with socket.create_connection(server.bound_address,timeout=4) as sock:
                sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                sock.settimeout(4)
                data=b''
                while True:
                    piece=sock.recv(4096)
                    if not piece:break
                    data+=piece
            self.assertIn(b'prefix',data)
            self.assertIn(b'Trailer: x-done',data)
            self.assertNotIn(b'X-Done: yes',data)
            self.assertNotIn(b'0\r\n',data)
            self.assertEqual(producer.closed,1)
        finally:server.shutdown(0.5)


class EventValianceTests(unittest.TestCase):
    def execute(self, source, serialized=False):
        analyzer=Analyser();analysed=analyzer.analyse(parse(source))
        self.assertEqual([str(x) for x in analyzer.diagnostics],[])
        program=compile_program(analysed,optimize=serialized)
        return run(loads(dumps(program)) if serialized else program)

    def test_valiance_event_source_serialized_and_normal(self):
        source=r'''import {std.web, std.concurrency}
$counter = concurrency.State(0)
$events = web.EventSource(
  next = fn() -> web.Event | None | web.Error =>
    if concurrency.get($counter) == 0 =>
      concurrency.set($counter, 1)
      web.Event(data = "hello\nworld", id = "1")
    else => \None end
  end,
  close = fn() -> None | web.Error => \None end
)
web.responseText(web.eventStream($events)) ?'''
        for serial in (False,True):
            result,=self.execute(source,serialized=serial)
            self.assertEqual(result,'id: 1\ndata: hello\ndata: world\n\n')

    def test_valiance_live_sse_and_last_event_id(self):
        source=r'''import {std.web, std.web.App, std.concurrency}
public define \makeEvents =>
  $counter = concurrency.State(0)
  web.EventSource(
    next = fn() -> web.Event | None | web.Error =>
      if concurrency.get($counter) == 0 =>
        concurrency.set($counter, 1)
        web.Event(data = "streamed", event = "update", id = "2")
      else => \None end
    end,
    close = fn() -> None | web.Error => \None end
  )
end
$app = App
$app := get("/updates"): fn(req: web.GetRequest) -> web.Response =>
  web.eventStream(\makeEvents)
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease,=self.execute(source,serialized=True)
        token=bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                bound=bridge._SERVER_LEASES[token].bound_address
            conn=http.client.HTTPConnection(*bound,timeout=4)
            try:
                conn.request('GET','/updates',headers={'Last-Event-ID':'1'})
                res=conn.getresponse()
                self.assertEqual(res.status,200)
                self.assertEqual(res.getheader('Content-Type'),'text/event-stream; charset=utf-8')
                self.assertEqual(res.read(),b'event: update\nid: 2\ndata: streamed\n\n')
            finally:conn.close()
        finally:bridge._close_server_token(token,0.5)

    def test_valiance_trailers_and_live_listener(self):
        source=r'''import {std.web, std.web.App, std.file.#byte, std.concurrency}
public define \makeSource =>
  $counter = concurrency.State(0)
  web.ByteSource(
    read = fn(max: Int) -> #byte+ Int+ | None | web.Error =>
      if concurrency.get($counter) == 0 =>
        concurrency.set($counter, 1)
        [97 #byte, 98 #byte]
      else => \None end
    end,
    close = fn() -> None | web.Error => \None end
  )
end
$app = App
$app := get("/"): fn(req: web.GetRequest) -> web.Response | web.Error =>
  $fields = web.withHeader(web.Headers, "X-Checksum", "ok")
  web.withTrailers(web.streamResponse(\makeSource), $fields)
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease,=self.execute(source,serialized=True)
        token=bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                bound=bridge._SERVER_LEASES[token].bound_address
            with socket.create_connection(bound,timeout=4) as sock:
                sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                sock.settimeout(4);data=b''
                while True:
                    part=sock.recv(4096)
                    if not part: break
                    data+=part
            self.assertIn(b'2\r\nab\r\n0\r\n',data)
            self.assertIn(b'X-Checksum: ok',data)
        finally:bridge._close_server_token(token,0.5)


if __name__=='__main__':unittest.main()
