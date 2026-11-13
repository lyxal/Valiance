"""Incoming request trailers: strict HTTP/1.1 parsing and post-EOF Valiance access."""
from __future__ import annotations

import socket
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, parts):
        self.parts = list(parts)
        self.closed = 0

    def read(self, n):
        if not self.parts:
            return None
        part = self.parts.pop(0)
        if len(part) > n:
            self.parts.insert(0, part[n:])
        return part[:n]

    def close(self):
        self.closed += 1


def raw_request(addr, body):
    with socket.create_connection(addr, timeout=4) as sock:
        sock.settimeout(4)
        sock.sendall(body)
        sock.shutdown(socket.SHUT_WR)
        result = b''
        while True:
            try:
                chunk = sock.recv(65536)
            except ConnectionResetError:
                break
            if not chunk:
                break
            result += chunk
        return result


class IncomingTrailerCoreTests(unittest.TestCase):
    def test_only_after_eof_and_separate_from_headers(self):
        source = Source([b'abc', b'def'])
        request = core.test_request_stream(
            'POST', '/', source,
            headers=core.Headers((('Content-Type', 'application/octet-stream'),)),
            trailers=core.Headers((('X-Checksum', 'first'), ('x-checksum', 'second'))))
        with self.assertRaises(core.WebError) as caught:
            core.trailers(request)
        self.assertEqual(caught.exception.name, 'BodyUnavailable')
        stream = core.body_stream(request)
        self.assertEqual(stream.read(3), b'abc')
        with self.assertRaises(core.WebError):
            core.trailers(request)
        stream.discard()
        self.assertEqual(core.trailers(request).values('X-Checksum'), ('first', 'second'))
        self.assertEqual(request.headers.values('X-Checksum'), ())
        self.assertEqual(source.closed, 1)

    def test_socket_free_dispatch_and_middleware_observe_validated_eof(self):
        before = []
        def middleware(req):
            # Even middleware cannot inspect fields before body consumption.
            with self.assertRaises(core.WebError) as result:
                core.trailers(req)
            before.append(result.exception.name)
            return req
        def handler(req):
            core.body_stream(req).discard()
            return 'checksum=' + core.trailers(req).one('X-Checksum')
        app = core.App().route('POST', '/upload', handler)
        app = app.with_body_mode('POST', '/upload', 'Stream').use(middleware)
        source = Source([b'abc'])
        request = core.test_request_stream(
            'POST', '/upload', source,
            trailers=core.Headers((('X-Checksum', 'synthetic'),)))
        response = core.dispatch(app, request)
        self.assertEqual(response.bytes, b'checksum=synthetic')
        self.assertEqual(before, ['BodyUnavailable'])
        self.assertEqual(source.closed, 1)

    def test_wrong_mode_and_early_close_are_errors(self):
        with self.assertRaises(core.WebError) as error:
            core.trailers(core.test_request('POST', '/'))
        self.assertEqual(error.exception.name, 'WrongMode')
        req = core.test_request_stream('PUT', '/', Source([b'abc']))
        core.body_stream(req).close()
        with self.assertRaises(core.WebError) as error:
            core.trailers(req)
        self.assertEqual(error.exception.name, 'BodyUnavailable')

    def test_invalid_synthetic_trailer_fields_and_limits(self):
        for name in ('Content-Length', 'Authorization', 'Host', 'Trailer', 'Cookie',
                     'Transfer-Encoding', 'Content-Type', 'Set-Cookie'):
            with self.subTest(name=name), self.assertRaises(core.WebError) as caught:
                core.test_request_stream('POST', '/', Source([]),
                                         trailers=core.Headers(((name, 'bad'),)))
            self.assertEqual(caught.exception.name, 'InvalidHeader')
        with self.assertRaises(core.WebError) as caught:
            core.test_request_stream('POST', '/', Source([]),
                                     trailers=core.Headers((('X-Note', 'z' * 9000),)))
        self.assertEqual(caught.exception.name, 'HeadersTooLarge')
        with self.assertRaises(core.WebError):
            core.test_request_stream('POST', '/', Source([]),
                                     trailers=core.Headers((('X-Note', '\u2603'),)))
        with self.assertRaises(core.WebError) as error:
            core.test_request_stream('POST', '/', Source([]),
                                     headers=core.Headers((('X-Note', 'initial'),)),
                                     trailers=core.Headers((('x-note', 'trailer'),)))
        self.assertEqual(error.exception.name, 'InvalidHeader')

    def test_empty_trailers_after_complete_body(self):
        req = core.test_request_stream('QUERY', '/', Source([]))
        reader = core.body_stream(req)
        self.assertIsNone(reader.read(12))
        self.assertEqual(core.trailers(req), core.Headers())


class IncomingTrailerHTTPTests(unittest.TestCase):
    def test_chunked_trailers_and_keepalive(self):
        hits = []
        def handler(req):
            reader = core.body_stream(req)
            with self.assertRaises(core.WebError):
                core.trailers(req)
            chunks = []
            while (part := reader.read(2)) is not None:
                chunks.append(part)
            value = core.trailers(req).values('X-Checksum')
            hits.append((b''.join(chunks), value, req.headers.values('X-Checksum')))
            return 'verified:' + ','.join(value)
        app = core.App().route('POST', '/', handler).with_body_mode('POST', '/', 'Stream')
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .5)
        data = (b'POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n'
                b'Trailer: X-Checksum\r\nConnection: close\r\n\r\n'
                b'3\r\nabc\r\n3\r\ndef\r\n0\r\nX-Checksum: one\r\n'
                b'X-Checksum: two\r\n\r\n')
        response = raw_request(server.bound_address, data)
        self.assertIn(b'HTTP/1.1 200', response)
        self.assertIn(b'verified:one,two', response)
        self.assertEqual(hits, [(b'abcdef', ('one', 'two'), ())])

    def test_completed_trailers_allow_next_keepalive_request(self):
        seen = []
        def upload(req):
            reader = core.body_stream(req)
            while reader.read(64) is not None:
                pass
            seen.append(core.trailers(req).one('X-Hash'))
            return 'received'
        app = (core.App().route('POST', '/upload', upload)
               .with_body_mode('POST', '/upload', 'Stream')
               .route('GET', '/health', lambda req: 'healthy'))
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .5)
        pipelined = (
            b'POST /upload HTTP/1.1\r\nHost: localhost\r\n'
            b'Transfer-Encoding: chunked\r\nTrailer: X-Hash\r\n\r\n'
            b'2\r\nok\r\n0\r\nX-Hash: good\r\n\r\n'
            b'GET /health HTTP/1.1\r\nHost: localhost\r\n'
            b'Connection: close\r\n\r\n')
        result = raw_request(server.bound_address, pipelined)
        self.assertEqual(result.count(b'HTTP/1.1 200'), 2)
        self.assertIn(b'received', result)
        self.assertIn(b'healthy', result)
        self.assertEqual(seen, ['good'])

    def test_forbidden_and_oversize_trailers_are_rejected(self):
        called = []
        def handler(req):
            reader = core.body_stream(req)
            while reader.read(1024) is not None:
                pass
            called.append('ok')
            return 'should not succeed'
        app = core.App().route('POST', '/', handler).with_body_mode('POST', '/', 'Stream')
        server = http1.serve(app, port=0,
                              limits=http1.Limits(max_header_bytes=1024))
        self.addCleanup(server.shutdown, .5)
        prefix = (b'POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n'
                  b'Connection: close\r\n\r\n0\r\n')
        for suffix in (b'Content-Length: 2\r\n\r\n',
                       b'X-Too-Large: ' + b'z' * 2000 + b'\r\n\r\n'):
            with self.subTest(suffix=suffix[:20]):
                reply = raw_request(server.bound_address, prefix + suffix)
                self.assertTrue(b'HTTP/1.1 400' in reply or b'HTTP/1.1 431' in reply,
                                reply[:180])
        self.assertEqual(called, [])

    def test_buffered_request_rejects_untrusted_trailers_before_handler(self):
        called = []
        app = core.App().route('POST', '/', lambda req: called.append(1) or 'ok')
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .5)
        wire = (b'POST / HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n'
                b'Connection: close\r\n\r\n0\r\nAuthorization: invalid\r\n\r\n')
        result = raw_request(server.bound_address, wire)
        self.assertIn(b'HTTP/1.1 400', result)
        self.assertEqual(called, [])


class IncomingTrailerValianceTests(unittest.TestCase):
    SOURCE = r'''import {std.web, std.web.App}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> web.Response | web.Error | ValueError =>
  $stream = web.bodyStream($req) ?
  $data = web.readBodyStream($stream, 1024) ?
  $end = web.readBodyStream($stream, 1024) ?
  $tail = web.trailers($req) ?
  $single = web.header($tail, "X-Checksum") ?
  $values = web.headerValues($tail, "X-Checksum")
  web.jsonResponse($values)
end
$app = web.withBodyMode($app, web.Method.POST, "/upload", web.BodyMode.Stream) ?
web.serve($app, web.ServerConfig(port = 0)) ?'''

    def test_valiance_live_and_serialized_bytecode(self):
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                analyser = Analyser()
                program = analyser.analyse(parse(self.SOURCE))
                self.assertEqual([str(err) for err in analyser.diagnostics], [])
                code = compile_program(program, optimize=serialized)
                if serialized:
                    code = loads(dumps(code))
                handle, = run(code)
                token = bridge._lease_token(handle)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        address = bridge._SERVER_LEASES[token].bound_address
                    raw = raw_request(address, (
                        b'POST /upload HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n'
                        b'Connection: close\r\nTrailer: X-Checksum\r\n\r\n'
                        b'3\r\nabc\r\n0\r\nX-Checksum: from-vm\r\n\r\n'))
                    self.assertIn(b'HTTP/1.1 200', raw)
                    self.assertIn(b'["from-vm"]', raw)
                finally:
                    bridge._close_server_token(token, .5)


if __name__ == '__main__':
    unittest.main()
