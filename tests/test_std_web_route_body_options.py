"""Typed route body-mode and byte-cap admission across compiler, core, HTTP/1.1."""
from __future__ import annotations

import http.client
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, data: bytes):
        self.data = data
        self.closed = 0

    def read(self, maximum: int) -> bytes | None:
        if not self.data:
            return None
        chunk, self.data = self.data[:maximum], self.data[maximum:]
        return chunk

    def close(self):
        self.closed += 1


class RouteBodyOptionsCoreTests(unittest.TestCase):
    def test_route_limits_match_exact_method_at_most_specific_path(self):
        app = core.App().route('POST', '/files/{id}', lambda r: 'bounded',
                               body_mode='Stream', max_body_bytes=11)
        app = app.route('GET', '/files/special', lambda r: 'get')
        app = app.route('PUT', '/files/{id}', lambda r: 'put', max_body_bytes=7)
        self.assertEqual(core.select_body_policy(app, 'POST', '/files/a'), ('Stream', 11))
        self.assertEqual(core.select_body_policy(app, 'PUT', '/files/a'), ('Buffered', 7))
        self.assertEqual(core.select_body_policy(app, 'POST', '/files/special'), ('Buffered', None))
        self.assertEqual(core.select_body_policy(app, 'DELETE', '/files/a'), ('Buffered', None))
        self.assertEqual(core.select_body_policy(app, 'POST', '/elsewhere'), ('Buffered', None))
        self.assertEqual(core.select_body_mode(app, 'POST', '/files/a'), 'Stream')
        router = core.Router().route('QUERY', '/', lambda r: 'q',
                                     body_mode='Stream', max_body_bytes=8)
        mounted = core.App().group('/api', router)
        self.assertEqual(core.select_body_policy(mounted, 'QUERY', '/api'), ('Stream', 8))

    def test_reject_invalid_body_options_at_registration(self):
        for method, kwargs in [
            ('GET', {'body_mode': 'Stream'}),
            ('HEAD', {'max_body_bytes': 1}),
            ('DELETE', {'max_body_bytes': 1}),
            ('OPTIONS', {'body_mode': 'Stream'}),
            ('POST', {'body_mode': 'Unknown'}),
            ('POST', {'max_body_bytes': 0}),
            ('POST', {'max_body_bytes': -1}),
            ('POST', {'max_body_bytes': True}),
            ('POST', {'max_body_bytes': 1024 ** 3 + 1}),
        ]:
            with self.subTest(method=method, kwargs=kwargs):
                with self.assertRaises(core.ConfigError):
                    core.App().route(method, '/', lambda r: 'x', **kwargs)

    def test_synthetic_buffered_admission_and_stream_cap(self):
        hits = []
        app = core.App().route('POST', '/', lambda req: hits.append(True) or 'ok',
                               max_body_bytes=3)
        self.assertEqual(core.dispatch(app, core.test_request('POST', '/', body=b'abc')).status, 200)
        self.assertEqual(core.dispatch(app, core.test_request('POST', '/', body=b'abcd')).status, 413)
        self.assertEqual(hits, [True])
        stream_app = core.App().route('POST', '/', lambda req: core.body_stream(req).discard() or 'ok',
                                      body_mode='Stream', max_body_bytes=3)
        src = Source(b'abcd')
        request = core.test_request_stream('POST', '/', src, max_bytes=20)
        self.assertEqual(core.dispatch(stream_app, request).status, 413)
        self.assertEqual(src.closed, 1)
        src2 = Source(b'abc')
        self.assertEqual(core.dispatch(stream_app, core.test_request_stream(
            'POST', '/', src2, max_bytes=20)).status, 200)
        self.assertEqual(src2.closed, 1)


class RouteBodyOptionsHTTPTests(unittest.TestCase):
    def server(self, app, **kwargs):
        srv = http1.serve(app, port=0, **kwargs)
        self.addCleanup(srv.shutdown, .5)
        return srv

    def test_global_cap_cannot_be_bypassed_by_route(self):
        app = core.App().route('POST', '/', lambda r: 'ok',
                               body_mode='Stream', max_body_bytes=12)
        with self.assertRaises(core.ConfigError):
            http1.serve(app, port=0, limits=http1.Limits(max_streaming_body_bytes=11))
        buffered = core.App().route('PUT', '/', lambda r: 'ok', max_body_bytes=12)
        with self.assertRaises(core.ConfigError):
            http1.serve(buffered, port=0, limits=http1.Limits(max_buffered_body_bytes=11))
        self.server(app, limits=http1.Limits(max_streaming_body_bytes=12))

    def test_content_length_and_chunked_enforce_stream_route_limit(self):
        seen = []
        def handle(req):
            reader = core.body_stream(req)
            reader.discard()
            seen.append(reader.total)
            return str(reader.total)
        app = core.App().route('POST', '/stream', handle,
                               body_mode='Stream', max_body_bytes=5)
        app = app.route('GET', '/ping', lambda req: 'pong')
        server = self.server(app, limits=http1.Limits(max_streaming_body_bytes=20))
        client = http.client.HTTPConnection(*server.bound_address, timeout=4)
        self.addCleanup(client.close)
        client.request('POST', '/stream', b'123456')
        res = client.getresponse()
        self.assertEqual((res.status, res.read()), (413, b'Content Too Large'))
        client.request('POST', '/stream', b'12345')
        res = client.getresponse()
        self.assertEqual((res.status, res.read()), (200, b'5'))
        client.request('POST', '/stream', body=[b'123', b'456'], encode_chunked=True,
                       headers={'Transfer-Encoding': 'chunked'})
        res = client.getresponse()
        self.assertEqual(res.status, 413)
        self.assertEqual(res.getheader('Connection'), 'close')
        res.read()
        self.assertEqual(seen, [5])

    def test_buffered_limit_and_other_method_isolation(self):
        hits = []
        app = core.App().route('PUT', '/r/{id}', lambda req: hits.append(req.body) or 'put',
                               max_body_bytes=3)
        app = app.route('GET', '/r/special', lambda req: 'get')
        server = self.server(app, limits=http1.Limits(max_buffered_body_bytes=50))
        client = http.client.HTTPConnection(*server.bound_address, timeout=4)
        self.addCleanup(client.close)
        client.request('PUT', '/r/abc', b'1234')
        r = client.getresponse()
        self.assertEqual(r.status, 413)
        r.read()
        client.request('PUT', '/r/abc', body=[b'12', b'34'], encode_chunked=True,
                       headers={'Transfer-Encoding': 'chunked'})
        r = client.getresponse()
        self.assertEqual(r.status, 413)
        r.read()
        client.request('PUT', '/r/abc', b'123')
        r = client.getresponse()
        self.assertEqual((r.status, r.read()), (200, b'put'))
        client.request('PUT', '/r/special', b'1234')
        r = client.getresponse()
        self.assertEqual(r.status, 405)
        r.read()
        self.assertEqual(hits, [b'123'])


class RouteBodyOptionsValianceTests(unittest.TestCase):
    @staticmethod
    def compile(source):
        analyser = Analyser()
        analysed = analyser.analyse(parse(source))
        return [str(d) for d in analyser.diagnostics], analysed

    def test_direct_registration_and_router_group_end_to_end(self):
        source = r'''import {std.web, std.web.App, std.web.Router}
$router = Router
$router := post("/upload", bodyMode = web.BodyMode.Stream, maxBodyBytes = 8): fn(req: web.PostRequest) -> String | web.Error =>
  $reader = web.bodyStream($req) ?
  web.discardBodyStream($reader) ?
  "saved"
end
$app = App
$app := group("/v1", $router)
web.serve($app, web.ServerConfig(port = 0,
  limits = web.Limits(maxBufferedBodyBytes = 2, maxStreamingBodyBytes = 20))) ?'''
        diagnostics, analysed = self.compile(source)
        self.assertEqual(diagnostics, [])
        for optimized, serialized in ((False, False), (True, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                compiled = compile_program(analysed, optimize=optimized)
                lease, = run(loads(dumps(compiled)) if serialized else compiled)
                token = bridge._lease_token(lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        addr = bridge._SERVER_LEASES[token].bound_address
                    conn = http.client.HTTPConnection(*addr, timeout=4)
                    try:
                        for data, expected in ((b'abcd', 200), (b'12345678', 200),
                                               (b'123456789', 413)):
                            conn.request('POST', '/v1/upload', data)
                            resp = conn.getresponse()
                            self.assertEqual(resp.status, expected)
                            resp.read()
                    finally:
                        conn.close()
                finally:
                    bridge._close_server_token(token, .5)

    def test_keywords_are_typed_and_invalid_limits_fail(self):
        # Both route options coexist in the ordinary higher-order PUT overload.
        source = ("import {std.web, std.web.App}\n$app = App\n"
                  '$app := put("/x", bodyMode = web.BodyMode.Stream, maxBodyBytes = 5): '
                  'fn(req: web.PutRequest) -> String => "ok" end\n')
        self.assertEqual(self.compile(source)[0], [])
        wrong_method = ("import {std.web, std.web.App}\n$app = App\n"
                        '$app := get("/x", bodyMode = web.BodyMode.Stream): '
                        'fn(req: web.GetRequest) -> String => "ok" end\n')
        self.assertTrue(self.compile(wrong_method)[0])
        invalid_limit = ("import {std.web, std.web.App}\n$app = App\n"
                         '$app := post("/x", maxBodyBytes = 0): '
                         'fn(req: web.PostRequest) -> String => "ok" end\n')
        diagnostics, analysed = self.compile(invalid_limit)
        self.assertEqual(diagnostics, [])
        with self.assertRaises(Exception):
            run(compile_program(analysed))



if __name__ == '__main__':
    unittest.main()
