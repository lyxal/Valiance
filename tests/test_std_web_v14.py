"""v14 conformance increments: typed JSON and cookie policy surface."""
from __future__ import annotations

import http.client
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core
from valiance.std import web as bridge


class V14TypedJsonTests(unittest.TestCase):
    def execute(self, source: str, *, optimized: bool = False, serialized: bool = False):
        analyser = Analyser()
        analysed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(analysed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)

    def test_all_content_methods_and_schema_errors(self):
        for method in ('POST', 'PUT', 'PATCH', 'QUERY'):
            src = f'''import {{std.web, std.web.App, std.file.#byte}}
$app = App
$app := {method.lower()}("/number"): fn(req: web.{method.title()}Request) -> web.Response | web.Error =>
  $number = web.bodyJson[Int]($req) ?
  web.Response(body = "valid")
end
$headers = web.withHeader(web.Headers, "Content-Type", "application/problem+json")
web.dispatch($app, web.testRequest("{method}", "/number", $headers, [52 #byte, 50 #byte]))
web.dispatch($app, web.testRequest("{method}", "/number", $headers, [34 #byte, 110 #byte, 111 #byte, 34 #byte]))
web.dispatch($app, web.testRequest("{method}", "/number", $headers, [123 #byte]))
web.dispatch($app, web.testRequest("{method}", "/number", web.withHeader(web.Headers, "Content-Type", "text/plain"), [0 #byte]))'''
            with self.subTest(method=method):
                result = self.execute(src, optimized=True, serialized=True)
                responses = [bridge._unwrap_response(item) for item in result]
                self.assertEqual([r.status for r in responses], [200, 400, 400, 415])
                self.assertEqual(responses[0].body, 'valid')
                self.assertTrue(all('no' not in r.bytes.decode() for r in responses[1:]))

    def test_nested_structures_follow_std_json_decoder(self):
        src = '''import {std.web, std.web.App, std.file.#byte}
$app = App
$app := post("/array"): fn(req: web.PostRequest) -> web.Response | web.Error =>
  $numbers = web.bodyJson[Int+]($req) ?
  web.jsonResponse($numbers) ?
end
$headers = web.withHeader(web.Headers, "Content-Type", "application/json; charset=utf-8")
web.dispatch($app, web.testRequest("POST", "/array", $headers, [91 #byte, 49 #byte, 44 #byte, 50 #byte, 93 #byte]))'''
        # jsonResponse propagates ValueError, which belongs to ordinary Result.
        src = src.replace('-> web.Response | web.Error', '-> web.Response | web.Error | ValueError')
        for optimized, serialized in ((False, False), (True, True)):
            result, = self.execute(src, optimized=optimized, serialized=serialized)
            value = bridge._unwrap_response(result)
            self.assertEqual(value.status, 200)
            self.assertEqual(value.bytes, b'[1,2]')

    def test_live_http1_invalid_schema_returns_400(self):
        src = '''import {std.web, std.web.App}
$app = App
$app := post("/int"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyJson[Int]($req) ?
  "valid"
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease, = self.execute(src, serialized=True)
        token = bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                host, port = bridge._SERVER_LEASES[token].bound_address
            conn = http.client.HTTPConnection(host, port, timeout=4)
            try:
                for data, expected in ((b'42', 200), (b'"not an integer"', 400),
                                       (b'{', 400), (b'null', 400)):
                    conn.request('POST', '/int', body=data, headers={'Content-Type': 'application/json'})
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    body = response.read()
                    if expected == 400:
                        self.assertNotIn(b'not an integer', body)
            finally:
                conn.close()
        finally:
            bridge._close_server_token(token)


class V14ResponseCookiesTests(unittest.TestCase):
    def execute(self, source: str, *, serialized: bool = False):
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        bytecode = compile_program(program, optimize=serialized)
        return run(loads(dumps(bytecode)) if serialized else bytecode)

    def test_cookie_defaults_and_explicit_policy_core(self):
        response = core.with_cookie(core.Response(), '__Host-sid', 'v')
        field, = response.headers.values('Set-Cookie')
        self.assertEqual(field, '__Host-sid=v; Path=/; Secure; HttpOnly; SameSite=Lax')
        response = core.with_cookie(response, 'other', 'ok', path='/auth', domain='example.test',
                                    same_site='Strict', max_age=120, expires=0)
        self.assertEqual(len(response.headers.values('Set-Cookie')), 2)
        self.assertIn('Max-Age=120', response.headers.values('Set-Cookie')[1])
        self.assertIn('Expires=Thu, 01 Jan 1970 00:00:00 GMT', response.headers.values('Set-Cookie')[1])
        deleted = core.delete_cookie(response, 'other', path='/auth', domain='example.test')
        self.assertEqual(len(deleted.headers.values('Set-Cookie')), 3)
        expired = deleted.headers.values('Set-Cookie')[-1]
        for component in ('Path=/auth', 'Domain=example.test', 'Max-Age=0',
                          'Expires=Thu, 01 Jan 1970 00:00:00 GMT'):
            self.assertIn(component, expired)

    def test_cookie_invalid_scope_and_prefixes_core(self):
        invalid = (
            dict(name='sid', value='bad;cookie'),
            dict(name='sid', value='v', path='/a\nInjected'),
            dict(name='sid', value='v', domain='-example.test'),
            dict(name='sid', value='v', domain='bad..test'),
            dict(name='sid', value='v', same_site='None', secure=False),
            dict(name='sid', value='v', partitioned=True, secure=False),
            dict(name='__Secure-sid', value='v', secure=False),
            dict(name='__Host-sid', value='v', path='/other'),
            dict(name='__Host-sid', value='v', domain='example.test'),
            dict(name='__Http-sid', value='v', http_only=False),
            dict(name='sid', value='v', max_age=1.5),
            dict(name='sid', value='v', expires='wrong'),
        )
        for case in invalid:
            with self.subTest(case=case):
                with self.assertRaises(core.WebError) as error:
                    core.with_cookie(core.Response(), **case)
                self.assertEqual(error.exception.name, 'InvalidCookie')

    def test_valiance_cookie_named_parameters_and_defaults(self):
        source = '''import {std.web}
web.withCookie(web.Response, "sid", "default") ?
web.withCookie(web.Response, "sid", "v", path = "/auth", domain = "example.test", httpOnly = false, secure = false, sameSite = web.SameSite.Lax, maxAge = 5, expires = 0) ?
web.deleteCookie(web.Response, "sid", path = "/auth", domain = "example.test") ?
web.withCookie(web.Response, "sid", "v", secure = false, sameSite = web.SameSite.None)
web.withCookie(web.Response, "__Host-sid", "v", partitioned = true, sameSite = web.SameSite.None) ?'''
        for serialized in (False, True):
            result = self.execute(source, serialized=serialized)
            self.assertEqual(len(result), 5)
            normal = bridge._unwrap_response(result[0]).headers.values('Set-Cookie')
            self.assertIn('Secure', normal[0])
            custom = bridge._unwrap_response(result[1]).headers.values('Set-Cookie')[0]
            self.assertIn('Path=/auth', custom)
            self.assertIn('Max-Age=5', custom)
            self.assertNotIn('; Secure', custom)
            self.assertNotIn('; HttpOnly', custom)
            delete = bridge._unwrap_response(result[2]).headers.values('Set-Cookie')[0]
            self.assertIn('Max-Age=0', delete)
            self.assertEqual(result[3].type_name, 'web.Error.InvalidCookie')
            partitioned = bridge._unwrap_response(result[4]).headers.values('Set-Cookie')[0]
            self.assertIn('SameSite=None', partitioned)
            self.assertIn('Partitioned', partitioned)
            self.assertIn('Secure', partitioned)

    def test_live_http1_preserves_multiple_set_cookie_fields(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) -> web.Response | web.Error =>
  $res = web.withCookie(web.Response(body = "ready"), "sid", "abc") ?
  web.withCookie($res, "theme", "dark", path = "/prefs") ?
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease, = self.execute(source, serialized=True)
        token = bridge._lease_token(lease)
        try:
            with bridge._SERVER_LEASES_LOCK:
                host, port = bridge._SERVER_LEASES[token].bound_address
            conn = http.client.HTTPConnection(host, port, timeout=4)
            try:
                conn.request('GET', '/')
                result = conn.getresponse()
                self.assertEqual(result.status, 200)
                self.assertEqual(result.read(), b'ready')
                fields = [v for k, v in result.getheaders() if k.lower() == 'set-cookie']
                self.assertEqual(len(fields), 2)
                self.assertTrue(fields[0].startswith('sid=abc;'))
                self.assertTrue(fields[1].startswith('theme=dark; Path=/prefs;'))
            finally:
                conn.close()
        finally:
            bridge._close_server_token(token)

    def test_invalid_response_cookie_policy_maps_to_safe_500(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) -> web.Response | web.Error =>
  web.withCookie(web.Response, "sid", "bad;value") ?
end
web.dispatch($app, web.testRequest("GET", "/"))'''
        for serialized in (False, True):
            response, = self.execute(source, serialized=serialized)
            normalized = bridge._unwrap_response(response)
            self.assertEqual(normalized.status, 500)
            self.assertEqual(normalized.body, 'Internal Server Error')
