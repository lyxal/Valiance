"""CORS policy and HTTPS transport conformance checks for std.web.

These exercise the same route pipeline through Python, compiled Valiance,
and live HTTP/1.1 sockets. They deliberately do not assert HTTP/2/3 support.
"""
from __future__ import annotations

import http.client
import shutil
import socket
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as web
from valiance.std import _web_http1 as http1
from valiance.std import web as bridge


class CorsCoreTests(unittest.TestCase):
    def setUp(self):
        self.called = []
        self.app = web.App().route('GET', '/thing', lambda r: self.called.append(r.method) or 'ok')
        self.app = self.app.route('POST', '/thing', lambda r: 'posted')
        self.policy = web.CorsPolicy(
            ('https://example.test',), allow_credentials=True,
            allowed_methods=('GET', 'POST'), allowed_headers=('X-Auth', 'Content-Type'),
            exposed_headers=('X-Request-ID',), max_age=300,
        )

    def request(self, method='GET', target='/thing', **headers):
        return web.test_request(method, target, headers=web.Headers(tuple(
            (key.replace('_', '-'), value) for key, value in headers.items()
        )))

    def test_default_denies_cross_origin_grants(self):
        response = web.dispatch(self.app, self.request(Origin='https://example.test'))
        self.assertEqual(response.status, 200)
        self.assertFalse(response.headers.values('Access-Control-Allow-Origin'))

    def test_allow_origin_normal_and_route_error(self):
        app = self.app.use(web.cors(self.policy))
        for path, status in (('/thing', 200), ('/missing', 404)):
            res = web.dispatch(app, self.request(target=path, Origin='https://EXAMPLE.test:443'))
            self.assertEqual(res.status, status)
            self.assertEqual(res.headers.one('Access-Control-Allow-Origin'), 'https://EXAMPLE.test:443')
            self.assertEqual(res.headers.one('Access-Control-Allow-Credentials'), 'true')
            self.assertEqual(res.headers.one('Access-Control-Expose-Headers'), 'X-Request-ID')
            self.assertIn('Origin', res.headers.one('Vary'))
        rejected = web.dispatch(app, self.request(Origin='https://example.test.evil'))
        self.assertFalse(rejected.headers.values('Access-Control-Allow-Origin'))
        self.assertIn('Origin', rejected.headers.one('Vary'))

    def test_denied_origin_strips_handler_supplied_cors_permissions(self):
        app = web.App().route('GET', '/x', lambda _: web.Response(
            body='x', headers=web.Headers((
                ('Access-Control-Allow-Origin', '*'),
                ('Access-Control-Allow-Credentials', 'true'),
            )))).use(web.cors(self.policy))
        r = web.dispatch(app, self.request(target='/x', Origin='https://evil.test'))
        self.assertEqual(r.status, 200)
        self.assertFalse(r.headers.values('Access-Control-Allow-Origin'))
        self.assertFalse(r.headers.values('Access-Control-Allow-Credentials'))
        self.assertIn('Origin', r.headers.one('Vary'))

    def test_valid_preflight_skips_business_handler_and_is_cache_safe(self):
        app = self.app.use(web.cors(self.policy))
        h = web.Headers((('Origin', 'https://example.test'),
                         ('Access-Control-Request-Method', 'GET'),
                         ('Access-Control-Request-Headers', 'x-auth, Content-Type')))
        r = web.dispatch(app, web.test_request('OPTIONS', '/thing', headers=h))
        self.assertEqual(r.status, 204)
        self.assertEqual(self.called, [])
        self.assertEqual(r.headers.one('Access-Control-Allow-Methods'), 'GET')
        self.assertEqual(r.headers.one('Access-Control-Allow-Headers'), 'x-auth, Content-Type')
        self.assertEqual(r.headers.one('Access-Control-Max-Age'), '300')
        self.assertEqual(r.headers.one('Access-Control-Allow-Credentials'), 'true')
        vary = r.headers.one('Vary')
        for part in ('Origin', 'Access-Control-Request-Method', 'Access-Control-Request-Headers'):
            self.assertIn(part, vary)

    def test_preflight_rejects_route_method_even_when_policy_permits(self):
        app = web.App().route('GET', '/literal', lambda r: 'ok')
        app = app.route('POST', '/{id}', lambda r: 'dynamic')
        app = app.use(web.cors(self.policy))
        h = web.Headers((('Origin', 'https://example.test'),
                         ('Access-Control-Request-Method', 'POST')))
        result = web.dispatch(app, web.test_request('OPTIONS', '/literal', headers=h))
        self.assertEqual(result.status, 403)
        self.assertEqual(result.headers.values('Access-Control-Allow-Origin'), ())

    def test_preflight_denials_and_duplicated_origins(self):
        app = self.app.use(web.cors(self.policy))
        base = [('Origin', 'https://example.test'), ('Access-Control-Request-Method', 'GET')]
        cases = (
            [('Origin', 'https://evil.test'), ('Access-Control-Request-Method', 'GET')],
            base + [('Origin', 'https://example.test')],
            base + [('Access-Control-Request-Headers', 'X-Unknown')],
            base + [('Access-Control-Request-Headers', 'X-Auth\nInjected')],
            base + [('Access-Control-Request-Private-Network', 'true')],
            [('Origin', 'https://example.test'), ('Access-Control-Request-Headers', 'X-Auth')],
            [('Access-Control-Request-Method', 'GET')],
            base + [('Access-Control-Request-Method', 'POST')],
        )
        for pairs in cases:
            with self.subTest(pairs=pairs):
                if any('\n' in value for _, value in pairs):
                    with self.assertRaises(web.WebError):
                        web.Headers(tuple(pairs))
                    continue
                result = web.dispatch(app, web.test_request('OPTIONS', '/thing',
                                                            headers=web.Headers(tuple(pairs))))
                if pairs == base + [('Access-Control-Request-Method', 'POST')]:
                    self.assertEqual(result.status, 403)
                else:
                    self.assertEqual(result.status, 403)
                self.assertFalse(result.headers.values('Access-Control-Allow-Origin'))

    def test_preflight_rejects_unknown_method_or_path_and_private_network_controls(self):
        policy = web.CorsPolicy(('*',), allowed_methods=('GET',), allow_private_network=True)
        app = self.app.use(web.cors(policy))
        def probe(path, method):
            h = web.Headers((('Origin', 'https://any.example'),
                             ('Access-Control-Request-Method', method),
                             ('Access-Control-Request-Private-Network', 'true')))
            return web.dispatch(app, web.test_request('OPTIONS', path, headers=h))
        self.assertEqual(probe('/thing', 'GET').status, 204)
        self.assertEqual(probe('/missing', 'GET').status, 403)
        self.assertEqual(probe('/thing', 'PATCH').status, 403)

    def test_wildcard_credentials_and_malformed_policy_are_rejected(self):
        cases = (
            dict(allowed_origins=('*',), allow_credentials=True),
            dict(allowed_origins=('https://x.test', '*')),
            dict(allowed_origins=('https://foo.test/a',)),
            dict(allowed_origins=('https://foo.test:0',)),
            dict(allowed_origins=('https://foo.test:65536',)),
            dict(allowed_origins=('https://foo.test@evil.test',)),
            dict(allowed_origins=('https://foo.test',), allowed_methods=('INVALID',)),
            dict(allowed_origins=('https://foo.test',), allowed_headers=('X\nHeader',)),
            dict(allowed_origins=('https://foo.test',), max_age=-1),
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(web.ConfigError):
                    web.CorsPolicy(**kwargs)

    def test_router_group_scope_and_middleware_order(self):
        child = web.Router().route('GET', '/ok', lambda r: 'hello')
        child = child.use(web.cors(self.policy))
        app = web.App().group('/api', child).route('GET', '/plain', lambda r: 'plain')
        with_origin = self.request(target='/api/ok', Origin='https://example.test')
        self.assertEqual(web.dispatch(app, with_origin).headers.one('Access-Control-Allow-Origin'),
                         'https://example.test')
        no_grant = web.dispatch(app, self.request(target='/plain', Origin='https://example.test'))
        self.assertEqual(no_grant.headers.values('Access-Control-Allow-Origin'), ())


class CorsValianceTests(unittest.TestCase):
    @staticmethod
    def execute(source, *, optimized=False, serialized=False):
        a = Analyser()
        typed = a.analyse(parse(source))
        if a.diagnostics:
            raise AssertionError(a.diagnostics)
        program = compile_program(typed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)

    def test_policy_named_fields_and_object_friendly_registration(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/x"): fn(req: web.GetRequest) => "ok" end
$app := middleware web.cors(web.CorsPolicy(
  allowedOrigins = ["https://example.test"],
  allowedMethods = [web.Method.GET],
  allowedHeaders = ["X-Test"],
  allowCredentials = true,
  maxAge = 500,
  allowPrivateNetwork = true
))
$h = web.withHeader(web.Headers, "Origin", "https://example.test")
$h := web.withHeader($h, "Access-Control-Request-Method", "GET")
$h := web.withHeader($h, "Access-Control-Request-Headers", "X-Test")
$h := web.withHeader($h, "Access-Control-Request-Private-Network", "true")
web.dispatch($app, web.testRequest("OPTIONS", "/x", $h))'''
        for optimized, serialized in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result = self.execute(source, optimized=optimized, serialized=serialized)[-1]
                from valiance.std.web import _unwrap_response
                core_response = _unwrap_response(result)
                self.assertEqual(core_response.status, 204)
                self.assertEqual(core_response.headers.one('Access-Control-Max-Age'), '500')
                self.assertEqual(core_response.headers.one('Access-Control-Allow-Credentials'), 'true')
                self.assertEqual(core_response.headers.one('Access-Control-Allow-Private-Network'), 'true')

    def test_router_scoped_cors_and_normal_middleware(self):
        source = '''import {std.web, std.web.[App, Router]}
$router = Router
$router := get("/"): fn(req: web.GetRequest) => "api" end
$router := middleware web.cors(web.CorsPolicy(
  allowedOrigins = ["https://example.test"],
  allowedMethods = [web.Method.GET]))
$app = App
$app := group("/api", $router)
$app := middleware: fn(req: web.Request) => $req end
$h = web.withHeader(web.Headers, "Origin", "https://example.test")
web.dispatch($app, web.testRequest("GET", "/api", $h))
web.dispatch($app, web.testRequest("GET", "/absent", $h))'''
        result = self.execute(source, serialized=True)
        from valiance.std.web import _unwrap_response
        selected, missing = map(_unwrap_response, result[-2:])
        self.assertEqual(selected.headers.one('Access-Control-Allow-Origin'), 'https://example.test')
        self.assertFalse(missing.headers.values('Access-Control-Allow-Origin'))


@unittest.skipUnless(shutil.which('openssl'), 'OpenSSL executable is required for TLS fixture')
class HTTPSTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = str(Path(cls.temp.name) / 'cert.pem')
        cls.key = str(Path(cls.temp.name) / 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_tls_13_socket_and_https_scheme(self):
        app = web.App().route('GET', '/', lambda req: req.scheme)
        owner = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)
        self.assertTrue(owner.tls_enabled)
        context = ssl.create_default_context(cafile=self.cert)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(['http/1.1'])
        con = http.client.HTTPSConnection('localhost', owner.bound_address[1],
                                          context=context, timeout=4)
        self.addCleanup(con.close)
        con.request('GET', '/')
        response = con.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'https'))

    def test_slow_tls_handshake_cannot_block_other_connections(self):
        """The accept loop must not perform a potentially slow handshake."""
        from time import monotonic, sleep
        owner = http1.serve(web.App().route('GET', '/', lambda r: 'ok'), port=0,
                            limits=http1.Limits(max_connections=2,
                                                header_timeout_seconds=1.5),
                            tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)
        raw = socket.create_connection(owner.bound_address, timeout=3)
        self.addCleanup(raw.close)
        # Wait until the listener admits the slow, non-TLS socket.
        deadline = monotonic() + 1
        while monotonic() < deadline:
            with owner._server._metric_lock:
                if owner._server._connections:
                    break
            sleep(.01)
        context = ssl.create_default_context(cafile=self.cert)
        c = http.client.HTTPSConnection('localhost', owner.bound_address[1],
                                        context=context, timeout=.6)
        self.addCleanup(c.close)
        start = monotonic()
        c.request('GET', '/')
        response = c.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'ok'))
        self.assertLess(monotonic() - start, .9)

    def test_shutdown_interrupts_pending_tls_handshake(self):
        """A configured ten-second TLS timeout must not prolong zero-grace stop."""
        from time import monotonic, sleep
        owner = http1.serve(web.App(), port=0, limits=http1.Limits(
            header_timeout_seconds=10, graceful_shutdown_seconds=0),
            tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)
        with socket.create_connection(owner.bound_address, timeout=3) as slow:
            slow.settimeout(3)
            deadline = monotonic() + 2
            while monotonic() < deadline:
                with owner._server._metric_lock:
                    if owner._server._connections:
                        break
                sleep(.01)
            start = monotonic()
            owner.shutdown()
            self.assertLess(monotonic() - start, .8)
            self.assertEqual(owner.counts, (0, 0))

    def test_tls_credential_reload_atomic_and_alpn_preserved(self):
        # A rejected replacement must leave the old certificate usable, and
        # a valid replacement must be seen by new TLS handshakes only.
        new_cert = str(Path(self.temp.name) / 'replacement.pem')
        new_key = str(Path(self.temp.name) / 'replacement-key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', new_key, '-out', new_cert, '-days', '1',
                        '-subj', '/CN=replaced.example'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        owner = http1.serve(web.App().route('GET', '/', lambda r: 'ok'),
                            port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)

        def observed_common_name():
            context = ssl._create_unverified_context()
            context.set_alpn_protocols(['h2', 'http/1.1'])
            with socket.create_connection(owner.bound_address, timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname='localhost') as secured:
                    cert = secured.getpeercert(binary_form=True)
                    self.assertEqual(secured.selected_alpn_protocol(), 'h2')
                    import hashlib
                    return hashlib.sha256(cert).digest()

        original = observed_common_name()
        with self.assertRaises(web.ConfigError):
            owner.reload_tls(http1.TLSConfig(new_cert, self.key))
        self.assertEqual(observed_common_name(), original)
        owner.reload_tls(http1.TLSConfig(new_cert, new_key))
        self.assertNotEqual(observed_common_name(), original)
        plain = http1.serve(web.App(), port=0)
        self.addCleanup(plain.shutdown)
        with self.assertRaises(web.ConfigError):
            plain.reload_tls(http1.TLSConfig(new_cert, new_key))

    def test_tls_12_plaintext_and_untrusted_certificate_rejected(self):
        owner = http1.serve(web.App(), port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(owner.shutdown)
        port = owner.bound_address[1]
        client12 = ssl.create_default_context(cafile=self.cert)
        client12.maximum_version = ssl.TLSVersion.TLSv1_2
        with self.assertRaises((ssl.SSLError, OSError)):
            client12.wrap_socket(socket.create_connection(('127.0.0.1', port), timeout=4),
                                 server_hostname='localhost')
        with self.assertRaises((ssl.SSLError, OSError)):
            ssl.create_default_context().wrap_socket(
                socket.create_connection(('127.0.0.1', port), timeout=4),
                server_hostname='localhost')
        with socket.create_connection(('127.0.0.1', port), timeout=4) as sock:
            sock.settimeout(4)
            sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
            try:
                payload = sock.recv(128)
                self.assertNotIn(b'HTTP/1.1 200', payload)
            except (ConnectionResetError, ssl.SSLError):
                pass

    def test_invalid_certificate_rejected_before_binding(self):
        with self.assertRaises(web.ConfigError):
            http1.serve(web.App(), port=0,
                        tls=http1.TLSConfig(self.cert, self.cert))
        with self.assertRaises(web.ConfigError):
            http1.serve(web.App(), port=0,
                        tls=http1.TLSConfig(self.cert, '/no/such/key.pem'))

    def test_valiance_https_serve_with_tls_constructor_and_bound_addresses(self):
        source = f'''import {{std.web, std.web.App}}
$app = App
$app := get("/secure"): fn(req: web.GetRequest) => $req.scheme end
web.serve($app, web.ServerConfig(port = 0, tls = web.TLS(certificateFile = "{self.cert}", privateKeyFile = "{self.key}"))) ?'''
        owner, = CorsValianceTests.execute(source, serialized=True)
        token = bridge._lease_token(owner)
        try:
            with bridge._SERVER_LEASES_LOCK:
                handle = bridge._SERVER_LEASES[token]
            self.assertTrue(handle.tls_enabled)
            context = ssl.create_default_context(cafile=self.cert)
            con = http.client.HTTPSConnection('localhost', handle.bound_address[1],
                                               context=context, timeout=4)
            try:
                con.request('GET', '/secure')
                response = con.getresponse()
                self.assertEqual((response.status, response.read()), (200, b'https'))
            finally:
                con.close()
        finally:
            bridge._close_server_token(token)

    def test_live_cors_https_preflight(self):
        app = web.App().route('GET', '/ok', lambda req: 'ok')
        app = app.use(web.cors(web.CorsPolicy(('https://example.test',),
                                              allowed_methods=('GET',),
                                              allowed_headers=('X-Auth',))))
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key))
        self.addCleanup(server.shutdown)
        context = ssl.create_default_context(cafile=self.cert)
        con = http.client.HTTPSConnection('localhost', server.bound_address[1],
                                           context=context, timeout=4)
        self.addCleanup(con.close)
        con.request('OPTIONS', '/ok', headers={'Origin': 'https://example.test',
                     'Access-Control-Request-Method': 'GET',
                     'Access-Control-Request-Headers': 'X-Auth'})
        response = con.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader('Access-Control-Allow-Origin'), 'https://example.test')
        self.assertEqual(response.read(), b'')
