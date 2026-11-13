"""Opt-in buffered compression: core, Valiance, HTTP/1.1, HTTPS.

No streamed-body conformance is claimed by these tests.
"""
from __future__ import annotations

from dataclasses import replace
import gzip
import http.client
import ssl
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import brotli

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as web
from valiance.std import _web_http1 as http1
from valiance.std import web as bridge


BODY = 'Valiance response compression test ' * 200


class CompressionCoreTests(unittest.TestCase):
    def app(self, policy=None, handler=None):
        return web.App().route('GET', '/', handler or (lambda _: BODY)).use(
            web.compression(policy or web.CompressionPolicy(min_bytes=32)))

    def request(self, *lines, method='GET'):
        return web.test_request(method, '/', headers=web.Headers(tuple(
            ('Accept-Encoding', line) for line in lines)))

    def test_disabled_by_default_and_default_vary(self):
        original = web.App().route('GET', '/', lambda _: BODY)
        r = web.dispatch(original, self.request('gzip'))
        self.assertEqual(r.bytes, BODY.encode())
        self.assertIsNone(r.headers.one('Content-Encoding'))
        enabled = web.dispatch(self.app(), self.request())
        self.assertEqual(enabled.bytes, BODY.encode())
        self.assertEqual(enabled.headers.one('Vary'), 'Accept-Encoding')

    def test_gzip_and_brotli_exact_roundtrip_and_etag_variants(self):
        original = web.with_etag(web.Response(body=BODY), 'version1')
        app = self.app(handler=lambda _: original)
        gzip_result = web.dispatch(app, self.request('gzip'))
        br_result = web.dispatch(app, self.request('br'))
        self.assertEqual(gzip_result.headers.one('Content-Encoding'), 'gzip')
        self.assertEqual(br_result.headers.one('Content-Encoding'), 'br')
        self.assertEqual(gzip.decompress(gzip_result.bytes).decode(), BODY)
        self.assertEqual(brotli.decompress(br_result.bytes).decode(), BODY)
        self.assertEqual(gzip_result.bytes, web.dispatch(app, self.request('gzip')).bytes)
        self.assertEqual(gzip_result.headers.one('ETag').startswith('"sha256-'), True)
        self.assertNotEqual(gzip_result.headers.one('ETag'), br_result.headers.one('ETag'))
        self.assertNotEqual(gzip_result.headers.one('ETag'), original.headers.one('ETag'))
        self.assertFalse(gzip_result.headers.values('Content-Length'))

    def test_preferences_quality_specificity_identity_and_wildcards(self):
        app = self.app()
        cases = (
            ('gzip, br;q=0.1', 'gzip'),
            ('gzip;q=0.1, br;q=0.8', 'br'),
            ('gzip;q=0, br;q=0', None),
            ('gzip;q=0, *;q=0.8', 'br'),
            ('gzip;q=0.6, br;q=0.6', 'br'),
            ('gzip;q=0.6, identity;q=0.9', None),
            ('identity', None),
        )
        for value, coding in cases:
            with self.subTest(value=value):
                r = web.dispatch(app, self.request(value))
                self.assertEqual(r.headers.one('Content-Encoding'), coding)
                self.assertEqual(r.status, 200)
        for value in ('identity;q=0, br;q=0, gzip;q=0', '*;q=0'):
            with self.subTest(no_representation=value):
                # *;q=0 excludes identity too under RFC 9110.
                r = web.dispatch(app, self.request(value))
                self.assertEqual(r.status, 406)

    def test_header_validation_is_safe_error_not_uncaught_fault(self):
        for bad in ('gzip;q=1.1', 'gzip;q=-1', 'gzip;q=banana',
                    'gzip, gzip;q=0', 'gzip;level=1', 'gzip;;q=1',
                    'gzip;q=0.1234', 'not/a-token'):
            with self.subTest(bad=bad):
                r = web.dispatch(self.app(), self.request(bad))
                self.assertEqual(r.status, 400)
        self.assertEqual(web.dispatch(self.app(), self.request('')).status, 200)

    def test_small_no_transform_content_range_already_encoded_and_stream_types(self):
        policy = web.CompressionPolicy(min_bytes=120, gzip_enabled=True, brotli_enabled=False)
        fixtures = (
            web.Response(body='tiny'),
            web.Response(body=BODY, headers=web.Headers((('Cache-Control', 'private, no-transform'),))),
            web.Response(206, body=BODY, headers=web.Headers((('Content-Range', 'bytes 0-3/100'),))),
            web.Response(body=BODY, headers=web.Headers((('Content-Encoding', 'gzip'),))),
            web.Response(body=BODY, headers=web.Headers((('Content-Type', 'text/event-stream'),))),
            web.Response(body=b'\x00' * 2000),
            web.Response(body=BODY, headers=web.Headers((('Content-Type', 'multipart/mixed; boundary=x'),))),
        )
        for response in fixtures:
            with self.subTest(status=response.status, headers=response.headers.pairs, size=len(response.bytes)):
                r = web.dispatch(self.app(policy, handler=lambda _, resp=response: resp), self.request('gzip'))
                self.assertEqual(r.status, response.status)
                self.assertEqual(r.bytes, response.bytes)
                self.assertEqual(r.headers.one('Content-Encoding'), response.headers.one('Content-Encoding'))

    def test_vary_and_invalid_policy(self):
        response = web.Response(body=BODY, headers=web.Headers((('Vary', 'Origin'), ('Vary', 'Accept-Language'))))
        app = self.app(handler=lambda _: response)
        r = web.dispatch(app, self.request('br'))
        self.assertEqual(r.headers.values('Vary'), ('Origin, Accept-Language, Accept-Encoding',))
        for kwargs in ({'min_bytes': -1}, {'max_bytes': 0}, {'min_bytes': 20, 'max_bytes': 10},
                       {'gzip_enabled': False, 'brotli_enabled': False},
                       {'gzip_enabled': 1}, {'brotli_enabled': 1}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(web.ConfigError):
                    web.CompressionPolicy(**kwargs)

    def test_middleware_scoping_and_reverse_order_error(self):
        child = web.Router().route('GET', '/', lambda _: BODY)
        child = child.use(web.compression(web.CompressionPolicy(min_bytes=1)))
        app = web.App().group('/api', child).route('GET', '/other', lambda _: BODY)
        selected = web.dispatch(app, web.test_request('GET', '/api', headers=web.Headers((('Accept-Encoding', 'gzip'),))))
        other = web.dispatch(app, web.test_request('GET', '/other', headers=web.Headers((('Accept-Encoding', 'gzip'),))))
        self.assertEqual(selected.headers.one('Content-Encoding'), 'gzip')
        self.assertIsNone(other.headers.one('Content-Encoding'))
        app = web.App().use(web.compression(web.CompressionPolicy(min_bytes=5)))
        app = app.route('GET', '/', lambda _: web.WebError('Conflict', 'something rejected'))
        result = web.dispatch(app, self.request('gzip'))
        self.assertEqual(result.status, 409)
        self.assertEqual(gzip.decompress(result.bytes), b'something rejected')

    def test_static_validators_are_selected_per_content_coding(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'example.txt').write_text(BODY)
            app = web.App().route('GET', '/files/{*path}', lambda req: web.static_file(root, req))
            app = app.use(web.compression(web.CompressionPolicy(min_bytes=10)))
            def request(coding, etag=None, method='GET'):
                pairs = [('Accept-Encoding', coding)]
                if etag is not None:
                    pairs.append(('If-None-Match', etag))
                return web.test_request(method, '/files/example.txt', headers=web.Headers(tuple(pairs)))
            identity = web.dispatch(app, request('identity'))
            gz = web.dispatch(app, request('gzip'))
            br = web.dispatch(app, request('br'))
            self.assertEqual(identity.status, 200)
            self.assertEqual(gzip.decompress(gz.bytes).decode(), BODY)
            self.assertEqual(brotli.decompress(br.bytes).decode(), BODY)
            identity_tag = identity.headers.one('ETag')
            gzip_tag = gz.headers.one('ETag')
            br_tag = br.headers.one('ETag')
            self.assertEqual(len({identity_tag, gzip_tag, br_tag}), 3)
            # A validator for the unencoded bytes does not match the selected
            # gzip or Brotli variant. Nor does gzip match Brotli.
            self.assertEqual(web.dispatch(app, request('gzip', identity_tag)).status, 200)
            self.assertEqual(web.dispatch(app, request('br', gzip_tag)).status, 200)
            for coding, tag in (('gzip', gzip_tag), ('br', br_tag), ('identity', identity_tag)):
                result = web.dispatch(app, request(coding, tag))
                self.assertEqual(result.status, 304)
                self.assertEqual(result.headers.one('ETag'), tag)
                self.assertEqual(result.bytes, b'')
                self.assertIn('Accept-Encoding', result.headers.one('Vary'))
            head = web.dispatch(app, request('br', br_tag, method='HEAD'))
            self.assertEqual(head.status, 304)
            self.assertEqual(head.bytes, b'')
            # Ranges are explicitly uncompressed; their validators continue
            # to describe original octets and If-Range is not reinterpreted.
            ranged = web.dispatch(app, web.test_request('GET', '/files/example.txt',
                headers=web.Headers((('Accept-Encoding', 'gzip'), ('Range', 'bytes=0-9')))))
            self.assertEqual(ranged.status, 206)
            self.assertEqual(ranged.bytes, BODY.encode()[:10])
            self.assertFalse(ranged.headers.values('Content-Encoding'))

    def test_head_suppression_preserves_encoded_representation_length(self):
        app = self.app()
        get_res = web.dispatch(app, self.request('gzip'))
        head_res = web.dispatch(app, self.request('gzip', method='HEAD'))
        self.assertEqual(head_res.bytes, b'')
        self.assertEqual(head_res.headers.one('Content-Encoding'), 'gzip')
        self.assertEqual(head_res.headers.one('Content-Length'), str(len(get_res.bytes)))


class CompressionValianceTests(unittest.TestCase):
    @staticmethod
    def execute(source, *, optimized=False, serialized=False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)
        program = compile_program(typed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)

    def test_app_object_import_full_compression_pipeline(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) => "abcdefghijabcdefghijabcdefghij" end
$app := middleware web.compression(web.CompressionPolicy(minBytes = 1))
$h = web.withHeader(web.Headers, "Accept-Encoding", "gzip")
web.dispatch($app, web.testRequest("GET", "/", $h))'''
        for flags in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(flags=flags):
                outputs = self.execute(source, optimized=flags[0], serialized=flags[1])
                result = bridge._unwrap_response(outputs[-1])
                self.assertEqual(result.headers.one('Content-Encoding'), 'gzip')
                self.assertEqual(gzip.decompress(result.bytes), b'abcdefghij' * 3)

    def test_router_scoped_brotli_and_no_transform(self):
        source = '''import {std.web, std.web.[App, Router]}
$router = Router
$router := get("/"): fn(req: web.GetRequest) => "abcdefghijabcdefghijabcdefghij" end
$router := middleware web.compression(web.CompressionPolicy(minBytes = 1))
$app = App
$app := group("/api", $router)
$app := get("/other"): fn(req: web.GetRequest) => "abcdefghijabcdefghijabcdefghij" end
$h = web.withHeader(web.Headers, "Accept-Encoding", "br")
web.dispatch($app, web.testRequest("GET", "/api", $h))
web.dispatch($app, web.testRequest("GET", "/other", $h))'''
        out = self.execute(source, serialized=True)
        routed, other = map(bridge._unwrap_response, out[-2:])
        self.assertEqual(brotli.decompress(routed.bytes), b'abcdefghij' * 3)
        self.assertEqual(other.bytes, b'abcdefghij' * 3)
        self.assertIsNone(other.headers.one('Content-Encoding'))


class CompressionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.app = web.App().route('GET', '/', lambda _: BODY)
        self.app = self.app.use(web.compression(web.CompressionPolicy(min_bytes=10)))
        self.owner = http1.serve(self.app, port=0)
        self.addCleanup(self.owner.shutdown)

    def test_live_http_compressed_and_head(self):
        con = http.client.HTTPConnection('127.0.0.1', self.owner.bound_address[1], timeout=3)
        self.addCleanup(con.close)
        con.request('GET', '/', headers={'Accept-Encoding': 'br'})
        res = con.getresponse()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader('Content-Encoding'), 'br')
        self.assertEqual(brotli.decompress(res.read()).decode(), BODY)
        con.request('GET', '/', headers={'Accept-Encoding': 'gzip'})
        res = con.getresponse()
        data = res.read()
        self.assertEqual(gzip.decompress(data).decode(), BODY)
        con.request('HEAD', '/', headers={'Accept-Encoding': 'gzip'})
        res = con.getresponse()
        self.assertEqual(res.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(res.getheader('Content-Length'), str(len(data)))
        self.assertEqual(res.read(), b'')

    def test_live_http_does_not_compress_unsupported_response_type(self):
        app = web.App().route('GET', '/', lambda _: web.Response(body=b'\x00\xff' * 400))
        app = app.use(web.compression(web.CompressionPolicy(min_bytes=1)))
        owner = http1.serve(app, port=0)
        self.addCleanup(owner.shutdown)
        con = http.client.HTTPConnection('127.0.0.1', owner.bound_address[1], timeout=3)
        self.addCleanup(con.close)
        con.request('GET', '/', headers={'Accept-Encoding': 'br'})
        res = con.getresponse()
        self.assertEqual(res.getheader('Content-Encoding'), None)
        self.assertEqual(res.read(), b'\x00\xff' * 400)


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL executable is required')
class CompressionHTTPSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = str(Path(cls.temp.name) / 'cert.pem')
        cls.key = str(Path(cls.temp.name) / 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_live_https_compression_pipeline(self):
        app = web.App().route('GET', '/', lambda _: BODY)
        app = app.use(web.compression(web.CompressionPolicy(min_bytes=10)))
        owner = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)
        context = ssl.create_default_context(cafile=self.cert)
        con = http.client.HTTPSConnection('localhost', owner.bound_address[1], timeout=3, context=context)
        self.addCleanup(con.close)
        con.request('GET', '/', headers={'Accept-Encoding': 'gzip'})
        response = con.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Encoding'), 'gzip')
        self.assertEqual(gzip.decompress(response.read()).decode(), BODY)
