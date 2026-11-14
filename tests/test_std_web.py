"""HTTP routing, target validation, and Valiance std.web integration tests."""
import unittest

from valiance.std import _web_core as web
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run


class WebPublicErrorHookTests(unittest.TestCase):
    """Public Valiance app policy remains effective after immutable routing edits."""

    def test_error_hook_default_mapping_and_success_is_not_an_error(self):
        from valiance.std import web as bridge
        source = r'''import {std.web, std.web.App}
$app = App
$app := web.withErrorHandler: fn (err: web.ErrorContext) -> web.Response =>
  web.withHeader(web.defaultErrorResponse($err), "X-App-Error", "yes")
end
$app := get("/ok"): fn(req: web.GetRequest) => "ok" end
web.dispatch($app, web.testRequest("GET", "/missing"))
web.dispatch($app, web.testRequest("POST", "/ok"))
web.dispatch($app, web.testRequest("GET", "/ok"))'''
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        for optimize, serialize in ((False, False), (True, True)):
            with self.subTest(optimize=optimize, serialize=serialize):
                program = compile_program(typed, optimize=optimize)
                values = run(loads(dumps(program)) if serialize else program)
                missing, disallowed, accepted = map(bridge._unwrap_response, values)
                self.assertEqual([r.status for r in (missing, disallowed, accepted)],
                                 [404, 405, 200])
                self.assertEqual(missing.headers.one('X-App-Error'), 'yes')
                self.assertEqual(disallowed.headers.one('Allow'), 'GET, HEAD, OPTIONS')
                self.assertEqual(disallowed.headers.one('X-App-Error'), 'yes')
                self.assertEqual(accepted.body, 'ok')
                self.assertEqual(accepted.headers.values('X-App-Error'), ())

    def test_faulting_logging_callback_does_not_replace_http_response(self):
        from valiance.std import web as bridge
        source = r'''import {std.web, std.web.App}
$app = App
$app := web.withLogger: fn (event: web.LogEvent) -> web.Error =>
  web.Error.Forbidden("private log failure")
end
$app := get("/ok"): fn(req: web.GetRequest) => "ok" end
web.dispatch($app, web.testRequest("GET", "/ok"))'''
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        result, = run(compile_program(typed))
        response = bridge._unwrap_response(result)
        self.assertEqual((response.status, response.body), (200, 'ok'))


class WebCoreTests(unittest.TestCase):
    def test_safe_completion_logger_keeps_route_template_and_is_fault_isolated(self):
        from valiance.std import _web_core as core
        events = []
        def observe(event):
            events.append(event)
            # A faulty user logger never overwrites an already-built response.
            raise RuntimeError('secret application token')
        app = core.App().route('GET', '/profile/{id}', lambda r: 'ok')
        app = app.with_logger(observe)
        request = core.test_request('GET', '/profile/alice?credential=secret',
                                    headers=core.Headers((('Authorization', 'Bearer secret'),)))
        self.assertEqual(core.dispatch(app, request).status, 200)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual((event.route_template, event.method, event.status),
                         ('/profile/{id}', 'GET', 200))
        self.assertEqual(event.response_bytes, 2)
        self.assertNotIn('secret', repr(event))
        self.assertNotIn('alice', repr(event))
        nested = []
        def recurse(evt):
            nested.append(evt.status)
            core.dispatch(app2, core.test_request('GET', '/'))
        app2 = core.App().route('GET', '/', lambda _: 'yes').with_logger(recurse)
        self.assertEqual(core.dispatch(app2, core.test_request('GET', '/')).status, 200)
        self.assertEqual(nested, [200])

    def test_target_decoding_once_and_repeated_query_pairs(self):
        path, params = web.validate_target('/users/%C3%A9?x=1&x=2&name=a+b')
        self.assertEqual(path, '/users/é')
        self.assertEqual(params.values('x'), ('1', '2'))
        self.assertEqual(params.one('name'), 'a+b')
        self.assertRaises(web.WebError, params.one, 'x')
        self.assertRaises(web.WebError, params.require, 'absent')
        self.assertEqual(web.parse_params('name=a+b', form=True).one('name'), 'a b')

    def test_invalid_targets_rejected_before_dispatch(self):
        for target in ('/%', '/%F0%80%80%80', '/%00', '/%2f', '/%5C',
                       '/a/..', '/%2e', '/x/%2E%2e', '/x\\y', '//other',
                       '/%252f', '/x#fragment', '/?x=%ZZ', '/?x=%FF'):
            with self.subTest(target=target):
                with self.assertRaises(web.DispatchError):
                    web.test_request('GET', target)

    def test_literal_path_takes_priority_over_dynamic_path_before_method(self):
        app = web.App().route('GET', '/users/new', lambda req: 'literal')
        app = app.route('POST', '/users/{id}', lambda req: req.capture('id'))
        self.assertEqual(web.dispatch(app, web.test_request('GET', '/users/new')).body, 'literal')
        response = web.dispatch(app, web.test_request('POST', '/users/new'))
        self.assertEqual(response.status, 405)
        self.assertEqual(response.headers.one('Allow'), 'GET, HEAD, OPTIONS')
        self.assertEqual(web.dispatch(app, web.test_request('POST', '/users/22')).body, '22')

    def test_ambiguous_or_duplicate_routes_rejected(self):
        for left, right in (('/users/{id}', '/users/{name}'),
                            ('/files/{*path}', '/files/{*rest}'),
                            ('/literal', '/literal')):
            app = web.App().route('GET', left, lambda _: 'a').route('GET', right, lambda _: 'b')
            with self.subTest(left=left, right=right):
                self.assertRaises(web.ConfigError, web.prepare, app)

    def test_invalid_templates_rejected(self):
        for path in ('', 'users', '/a//b', '/a/{*rest}/b', '/{1bad}', '/{x}/{x}', '/a?b'):
            with self.subTest(path=path):
                self.assertRaises(web.ConfigError, web.App().route, 'GET', path, lambda _: 'a')

    def test_immutability_and_reusable_group(self):
        child = web.Router().route('GET', '/', lambda _: 'root')
        child = child.route('POST', '/{id}', lambda r: r.capture('id'))
        initial = web.App()
        app = initial.group('/api', child)
        self.assertEqual(len(initial.entries), 0)
        self.assertEqual(web.dispatch(app, web.test_request('GET', '/api')).body, 'root')
        self.assertEqual(web.dispatch(app, web.test_request('POST', '/api/7')).body, '7')
        self.assertEqual(web.dispatch(initial, web.test_request('GET', '/api')).status, 404)

    def test_head_and_options(self):
        actual_methods = []
        app = web.App().route('GET', '/item', lambda req: actual_methods.append(
            (req.method, req.original_method)) or 'hello')
        head = web.dispatch(app, web.test_request('HEAD', '/item'))
        self.assertEqual(head.body, None)
        self.assertEqual(head.headers.one('Content-Length'), '5')
        self.assertEqual(actual_methods, [('GET', 'HEAD')])
        options = web.dispatch(app, web.test_request('OPTIONS', '/item'))
        self.assertEqual(options.status, 204)
        self.assertEqual(options.headers.one('Allow'), 'GET, HEAD, OPTIONS')

    def test_explicit_head_wins(self):
        app = web.App().route('GET', '/x', lambda _: 'long')
        app = app.route('HEAD', '/x', lambda _: web.Response(headers=web.Headers((('X-Head', 'yes'),))))
        head = web.dispatch(app, web.test_request('HEAD', '/x'))
        self.assertEqual(head.headers.one('X-Head'), 'yes')

    def test_app_middleware_runs_on_not_found_and_short_circuit(self):
        events = []
        def before(req):
            events.append('before')
            return web.After(req, lambda resp: (events.append('after') or resp.with_header('X-Trace', 'set')))
        app = web.App().use(before)
        missing = web.dispatch(app, web.test_request('GET', '/nope'))
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.headers.one('X-Trace'), 'set')
        self.assertEqual(events, ['before', 'after'])
        events.clear()
        app = app.use(lambda _: web.Response(403, 'blocked'))
        blocked = web.dispatch(app, web.test_request('GET', '/nope'))
        self.assertEqual(blocked.status, 403)
        self.assertEqual(events, ['before', 'after'])

    def test_middlewares_unwind_after_failure(self):
        events = []
        def outer(req):
            events.append('outer')
            return web.After(req, lambda resp: (events.append('outer-after') or resp))
        def inner(req):
            events.append('inner')
            return web.After(req, lambda resp: (events.append('inner-after') or resp))
        app = web.App().use(outer).use(inner).route('GET', '/', lambda req: 3)
        self.assertEqual(web.dispatch(app, web.test_request('GET', '/')).status, 500)
        self.assertEqual(events, ['outer', 'inner', 'inner-after', 'outer-after'])

    def test_fault_in_after_callback_continues_outer_unwind(self):
        def invalid(_):
            raise ValueError('do not expose this fault')
        app = web.App().use(lambda req: web.After(req, lambda resp: resp.with_header('X-Outer', 'yes')))
        app = app.use(lambda req: web.After(req, invalid)).route('GET', '/', lambda req: 'ok')
        response = web.dispatch(app, web.test_request('GET', '/'))
        self.assertEqual(response.status, 500)
        self.assertNotIn('do not expose', response.body)
        self.assertEqual(response.headers.one('X-Outer'), 'yes')

    def test_middleware_cannot_rewrite_method_or_path(self):
        from dataclasses import replace
        for change in ({'path': '/b'}, {'method': 'POST'}, {'raw_target': '/b'}):
            app = web.App().use(lambda req, change=change: replace(req, **change))
            app = app.route('GET', '/a', lambda _: 'bad')
            with self.subTest(change=change):
                self.assertEqual(web.dispatch(app, web.test_request('GET', '/a')).status, 500)

    def test_custom_error_callback_failure_falls_back(self):
        from dataclasses import replace
        app = replace(web.App(), on_error=lambda *a: web.Response(418, 'custom'))
        self.assertEqual(web.dispatch(app, web.test_request('GET', '/missing')).status, 418)
        broken = replace(app, on_error=lambda *a: 7)
        self.assertEqual(web.dispatch(broken, web.test_request('GET', '/missing')).status, 500)

    def test_reverse_routing_no_injection(self):
        app = web.App().route('GET', '/items/{id}', lambda req: 'ok')
        uri = web.route_url(app, 'GET', '/items/{id}', {'id': 'one two'},
                            web.Params((('q', 'a&b'), ('q', 'z'))))
        self.assertEqual(uri, '/items/one%20two?q=a%26b&q=z')
        for item in ('.', '..', ''):
            with self.subTest(item=item):
                self.assertRaises(web.ConfigError, web.route_url, app, 'GET', '/items/{id}', {'id': item})

    def test_header_validation_and_response_helpers(self):
        self.assertRaises(web.WebError, web.Headers, (('X', 'line\r\nbreak'),))
        self.assertRaises(web.WebError, web.Headers, (('bad name', 'x'),))
        self.assertEqual(web.json_response({'x': 1}).bytes, b'{"x":1}')
        self.assertEqual(web.redirect('/safe').status, 303)
        self.assertRaises(web.WebError, web.redirect, '//evil.com')
        self.assertEqual(web.no_store(web.Response()).headers.one('Cache-Control'), 'no-store')
        self.assertEqual(web.with_etag(web.Response(), 'abc').headers.one('ETag'), '"abc"')

    def test_body_media_types_and_repeated_form_values(self):
        json_req = web.test_request('POST', '/upload',
            headers=web.Headers((('Content-Type', 'application/problem+json'),)),
            body=b'{"value":true}')
        self.assertEqual(web.body_json(json_req), {'value': True})
        form = web.test_request('QUERY', '/search',
            headers=web.Headers((('Content-Type', 'application/x-www-form-urlencoded'),)),
            body=b'a=one+two&a=three')
        self.assertEqual(web.form_params(form).values('a'), ('one two', 'three'))
        self.assertEqual(web.body_text(form), 'a=one+two&a=three')
        wrong = web.test_request('POST', '/', body=b'[]')
        self.assertEqual(web.body_bytes(wrong), b'[]')
        with self.assertRaises(web.WebError) as caught:
            web.body_json(wrong)
        self.assertEqual(caught.exception.status, 415)
        malformed = web.test_request('POST', '/',
            headers=web.Headers((('Content-Type', 'application/json'),)), body=b'{broken')
        self.assertEqual(web.dispatch(web.App().route('POST', '/', web.body_json), malformed).status, 400)
        self.assertRaises(web.WebError, web.body_text, web.test_request('PUT', '/', body=b'\xff'))
        self.assertRaises(web.WebError, web.body_bytes, web.test_request('GET', '/', body=b'abc'))

    def test_mutation_preconditions_and_strong_weak_entity_tags(self):
        from valiance.std._web_core import Validators, preconditions
        state = Validators(etag='"v2"', last_modified=1000)
        def check(method, fields, validators=state):
            return preconditions(web.test_request(method, '/item', headers=web.Headers(tuple(fields))), validators)
        self.assertIsNone(check('PUT', (('If-Match', '"v2"'),)))
        self.assertIsNone(check('PATCH', (('If-Match', '*'),)))
        self.assertIsNone(check('PUT', (('If-None-Match', '"v1"'),)))
        self.assertIsNone(check('PUT', (('If-Unmodified-Since', 'Thu, 01 Jan 1970 00:17:00 GMT'),)))
        self.assertIsNone(check('POST', (('If-Unmodified-Since', 'invalid-date'),)))
        for fields in (
            (('If-Match', '"v1"'),),
            (('If-Match', 'W/"v2"'),),
            (('If-None-Match', '*'),),
            (('If-None-Match', 'W/"v2"'),),
            (('If-Unmodified-Since', 'Thu, 01 Jan 1970 00:15:00 GMT'),),
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(web.WebError) as caught:
                    check('PUT', fields)
                self.assertEqual(caught.exception.status, 412)
        self.assertIsNone(check('PUT', (('If-None-Match', '*'),), Validators(exists=False)))
        with self.assertRaises(web.WebError) as caught:
            check('PUT', (('If-Match', '*'),), Validators(exists=False))
        self.assertEqual(caught.exception.status, 412)
        for malformed in ('"v2",', 'unquoted', 'W/"unterminated'):
            with self.subTest(malformed=malformed):
                with self.assertRaises(web.WebError) as caught:
                    check('PUT', (('If-Match', malformed),))
                self.assertEqual(caught.exception.status, 400)

    def test_conditional_selection_respects_strong_preconditions(self):
        res = web.with_last_modified(web.with_etag(web.Response(body='v2'), 'v2'), 1000)
        accepted = web.test_request('GET', '/', headers=web.Headers((
            ('If-Match', '"v2"'), ('If-None-Match', 'W/"v2"'))))
        self.assertEqual(web.conditional(accepted, res).status, 304)
        rejected = web.test_request('HEAD', '/', headers=web.Headers((
            ('If-Match', 'W/"v2"'), ('If-None-Match', '"v2"'))))
        with self.assertRaises(web.WebError) as caught:
            web.conditional(rejected, res)
        self.assertEqual(caught.exception.status, 412)

    def test_cookie_validation_and_response_cookie_policy(self):
        req = web.test_request('GET', '/', headers=web.Headers((
            ('Cookie', 'sid=one; theme=dark'), ('Cookie', 'sid=two'))))
        self.assertEqual(web.cookies(req).values('sid'), ('one', 'two'))
        with self.assertRaises(web.WebError) as caught:
            web.cookie(req, 'sid')
        self.assertEqual(caught.exception.name, 'AmbiguousCookie')
        response = web.with_cookie(web.Response(), '__Host-session', 'secure')
        self.assertIn('HttpOnly', response.headers.values('Set-Cookie')[0])
        self.assertIn('Secure', response.headers.values('Set-Cookie')[0])
        response = web.delete_cookie(response, 'session')
        self.assertEqual(len(response.headers.values('Set-Cookie')), 2)
        self.assertRaises(web.WebError, web.with_cookie, web.Response(), '__Host-a', 'v', domain='example.com')
        self.assertRaises(web.WebError, web.with_cookie, web.Response(), 'x', 'bad;value')

    def test_conditionals_and_invalid_response_framing(self):
        res = web.with_etag(web.Response(body='version'), 'v1')
        request = web.test_request('GET', '/', headers=web.Headers((('If-None-Match', '"v1"'),)))
        self.assertEqual(web.conditional(request, res).status, 304)
        different = web.test_request('GET', '/', headers=web.Headers((('If-None-Match', '"v2"'),)))
        self.assertIs(web.conditional(different, res), res)
        app = web.App().route('GET', '/', lambda _: web.Response(body='abc').with_header('Content-Length', '1'))
        self.assertEqual(web.dispatch(app, web.test_request('GET', '/')).status, 500)
        self.assertEqual(web.dispatch(app, web.test_request('HEAD', '/')).status, 200)
        self.assertEqual(web.dispatch(web.App(), web.test_request('BREW', '/')).status, 501)


    def test_binary_responses_are_bytes_and_collectors_are_bounded(self):
        raw = b'\x00\x80\xff\x0a'
        response = web.Response(body=raw)
        self.assertEqual(response.bytes, raw)
        self.assertEqual(response.headers.one('Content-Type'), 'application/octet-stream')
        self.assertEqual(web.collect_body(response), raw)
        self.assertEqual(web.collect_body(web.Response(body='\u00e9')), b'\xc3\xa9')
        self.assertEqual(web.response_text(web.Response(body='\u00e9')), '\u00e9')
        self.assertRaises(web.WebError, web.response_text, response)
        with self.assertRaises(web.WebError) as oversized:
            web.collect_body(response, 3)
        self.assertEqual(oversized.exception.name, 'BodyTooLarge')
        with self.assertRaises(web.WebError) as bad_limit:
            web.collect_body(response, -1)
        self.assertEqual(bad_limit.exception.name, 'InvalidResponse')
        self.assertEqual(web.Response(body=b'', headers=web.Headers((('Content-Type', 'image/png'),))).bytes, b'')


class WebHTTP1IntegrationTests(unittest.TestCase):
    """Real loopback socket checks against the h11 application adapter."""

    def setUp(self):
        from valiance.std import _web_http1 as http1
        self.http1 = http1

    def _start(self, app, limits=None):
        handle = self.http1.serve(app, port=0, limits=limits)
        self.addCleanup(handle.shutdown)
        return handle.bound_address[1]

    def _raw(self, port, data):
        import socket
        with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
            client.settimeout(3)
            client.sendall(data)
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    return b''.join(chunks)
                chunks.append(chunk)

    def test_http1_get_head_options_keepalive_and_path_precedence(self):
        import http.client
        app = web.App().route('GET', '/resource', lambda _: 'hello')
        app = app.route('POST', '/resource/{id}', lambda r: r.capture('id'))
        port = self._start(app)
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        self.addCleanup(conn.close)
        conn.request('GET', '/resource?x=1')
        response = conn.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'hello'))
        conn.request('HEAD', '/resource')
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('Content-Length'), '5')
        self.assertEqual(response.read(), b'')
        conn.request('OPTIONS', '/resource')
        response = conn.getresponse()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.getheader('Allow'), 'GET, HEAD, OPTIONS')
        self.assertEqual(response.read(), b'')
        conn.request('POST', '/resource')
        response = conn.getresponse()
        self.assertEqual(response.status, 405)
        self.assertEqual(response.getheader('Allow'), 'GET, HEAD, OPTIONS')
        response.read()

    def test_http1_preconditions_and_cache_validation(self):
        import http.client
        state = web.Validators(etag='"current"', last_modified=1000)
        def replace_item(request):
            web.preconditions(request, state)
            return 'replaced'
        def read_item(request):
            return web.conditional(request, web.with_etag(web.Response(body='current'), 'current'))
        app = web.App().route('PUT', '/item', replace_item).route('GET', '/item', read_item)
        port = self._start(app)
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        self.addCleanup(conn.close)
        for method, fields, expected in (
            ('PUT', {'If-Match': '"old"'}, 412),
            ('PUT', {'If-Match': '"current"'}, 200),
            ('PUT', {'If-None-Match': '*'}, 412),
            ('GET', {'If-None-Match': 'W/"current"'}, 304),
            ('GET', {'If-Match': 'W/"current"'}, 412),
        ):
            with self.subTest(method=method, headers=fields):
                conn.request(method, '/item', headers=fields)
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                body = response.read()
                if expected == 304:
                    self.assertEqual(body, b'')
                elif method == 'PUT' and expected == 200:
                    self.assertEqual(body, b'replaced')

    def test_http1_body_json_form_and_chunked_input(self):
        import http.client
        def responder(request):
            if request.headers.one('Content-Type') == 'application/json':
                return web.json_response(web.body_json(request))
            return web.Response(body=web.form_params(request).one('q'))
        app = web.App().route('POST', '/echo', responder)
        port = self._start(app)
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        self.addCleanup(conn.close)
        conn.request('POST', '/echo', b'{"ok":true}', {'Content-Type': 'application/json'})
        response = conn.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'{"ok":true}'))
        conn.request('POST', '/echo', b'q=a+b&q=z', {'Content-Type': 'application/x-www-form-urlencoded'})
        response = conn.getresponse()
        self.assertEqual((response.status, response.read()), (400, b'Parameter appears more than once'))  # repeated q
        conn.request('POST', '/echo', b'q=a+b', {'Content-Type': 'application/x-www-form-urlencoded'}, encode_chunked=True)
        response = conn.getresponse()
        self.assertEqual((response.status, response.read()), (200, b'a b'))
        conn.request('POST', '/echo', b'{bad', {'Content-Type': 'application/json'})
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()

    def test_http1_rejects_smuggling_and_target_attacks_before_handlers(self):
        count = []
        app = web.App().route('POST', '/', lambda _: count.append(1) or 'ok')
        port = self._start(app)
        payloads = (
            b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\nContent-Length: 3\r\n\r\nabc',
            b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 3\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n',
            b'POST /%2f HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n',
            b'POST / HTTP/1.1\r\nHost: a\r\nHost: b\r\nContent-Length: 0\r\n\r\n',
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                reply = self._raw(port, payload)
                self.assertTrue(reply.startswith(b'HTTP/1.1 400'), reply)
        self.assertEqual(count, [])

    def test_valiance_run_serves_http_and_stops_on_sigterm(self):
        import http.client
        import os
        import signal
        import socket
        import subprocess
        import sys
        import time
        if os.name != 'posix':
            self.skipTest('POSIX signal-based listener shutdown test')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        source = f'''import {{std.web, std.web.[get, post]}}
$app = web.App
$app := get("/ping"): fn (req: web.GetRequest) => "native" end
$app := post("/text"): fn (req: web.PostRequest) -> String | web.Error => web.bodyText($req) end
$app := post("/bytes"): fn (req: web.PostRequest) -> String | web.Error =>
  $octets = web.bodyBytes($req) ?
  "binary accepted"
end
$app := post("/form"): fn (req: web.PostRequest) -> String | web.Error =>
  $params = web.formParams($req) ?
  $values = web.paramValues($params, "q")
  "form parsed"
end
web.run($app, web.ServerConfig(port = {port}))'''
        script = ("from valiance.analysis import Analyser\n"
                  "from valiance.parsing import parse\n"
                  "from valiance.runtime import compile_program, run\n"
                  f"source = {source!r}\n"
                  "analyser = Analyser()\n"
                  "nodes = analyser.analyse(parse(source))\n"
                  "assert not analyser.diagnostics, analyser.diagnostics\n"
                  "run(compile_program(nodes))\n")
        child = subprocess.Popen([sys.executable, '-u', '-c', script],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        try:
            succeeded = False
            # Cold Valiance compilation can exceed seven seconds on a loaded
            # developer machine. Wait on a monotonic deadline rather than
            # assuming 70 refused connections take one second each.
            startup_deadline = time.monotonic() + 25
            while time.monotonic() < startup_deadline:
                if child.poll() is not None:
                    break
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
                    conn.request('GET', '/ping')
                    response = conn.getresponse()
                    succeeded = (response.status, response.read()) == (200, b'native')
                    conn.close()
                    if succeeded:
                        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
                        conn.request('POST', '/text', b'hello world', {'Content-Type': 'text/plain'})
                        text_res = conn.getresponse()
                        self.assertEqual((text_res.status, text_res.read()), (200, b'hello world'))
                        conn.request('POST', '/text', b'\xff', {'Content-Type': 'text/plain'})
                        invalid_res = conn.getresponse()
                        self.assertEqual(invalid_res.status, 400)
                        invalid_res.read()
                        conn.request('POST', '/bytes', b'\xff\x00\x80', {'Content-Type': 'application/octet-stream'})
                        bytes_res = conn.getresponse()
                        self.assertEqual((bytes_res.status, bytes_res.read()), (200, b'binary accepted'))
                        conn.request('POST', '/form', b'q=one&q=two',
                                     {'Content-Type': 'application/x-www-form-urlencoded'})
                        form_res = conn.getresponse()
                        self.assertEqual((form_res.status, form_res.read()), (200, b'form parsed'))
                        conn.close()
                        break
                except (OSError, TimeoutError):
                    time.sleep(.1)
            self.assertTrue(succeeded, 'Native run did not accept a Valiance request')
        finally:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
            try:
                out, err = child.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                out, err = child.communicate()
            self.assertEqual(child.returncode, 0, (out, err))

    def test_pipelined_duplicate_content_length_is_rejected_before_h11_coalesces(self):
        port = self._start(web.App().route('GET', '/', lambda _: 'ok'))
        payload = (b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n'
                   b'POST / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Content-Length: 0\r\nContent-Length: 0\r\n\r\n')
        response = self._raw(port, payload)
        self.assertEqual(response.count(b'HTTP/1.1 200'), 1)
        self.assertEqual(response.count(b'HTTP/1.1 400'), 1)
        self.assertEqual(response.count(b'HTTP/1.1'), 2)

    def test_http1_finite_admission_limits(self):
        limits = self.http1.Limits(max_request_target_bytes=16, max_buffered_body_bytes=4)
        port = self._start(web.App().route('POST', '/', lambda _: 'ok'), limits)
        cases = (
            (b'GET /xxxxxxxxxxxxxxxx HTTP/1.1\r\nHost: localhost\r\n\r\n', 414),
            (b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nabcde', 413),
        )
        for payload, status in cases:
            with self.subTest(status=status):
                self.assertTrue(self._raw(port, payload).startswith(f'HTTP/1.1 {status}'.encode()))



class WebLanguageIntegrationTests(unittest.TestCase):
    def _run(self, source: str, *, optimize: bool = False, serialized: bool = False):
        analyzer = Analyser()
        nodes = analyzer.analyse(parse(source))
        self.assertEqual(analyzer.diagnostics, [])
        program = compile_program(nodes, optimize=optimize)
        return run(loads(dumps(program)) if serialized else program)

    def test_canonical_get_route_executes_and_serializes(self):
        source = '''import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "Hello, world!" end
web.dispatch($app, web.testRequest("GET", "/"))'''
        for optimized in (False, True):
            for serialized in (False, True):
                with self.subTest(optimized=optimized, serialized=serialized):
                    response = self._run(source, optimize=optimized, serialized=serialized)[0]
                    self.assertEqual(response.fields['status'], 200)
                    self.assertEqual(response.fields['body'], 'Hello, world!')

    def test_route_specificity_and_head_from_valiance(self):
        source = '''import {std.web, std.web.[get, post]}
$app = web.App
$app := get("/users/new"): fn (req: web.GetRequest) => "new" end
$app := post("/users/{id}"): fn (req: web.PostRequest) => $req.path end
web.dispatch($app, web.testRequest("POST", "/users/new"))
web.dispatch($app, web.testRequest("POST", "/users/22"))
web.dispatch($app, web.testRequest("HEAD", "/users/new"))'''
        results = self._run(source)
        self.assertEqual([r.fields['status'] for r in results], [405, 200, 200])
        self.assertEqual([r.fields['body'] for r in results], ['Method Not Allowed', '/users/22', ''])


    def test_group_value_mount_in_language_and_router_scoped_middleware(self):
        source = '''import {std.web, std.web.[get, group, middleware]}
$child = web.Router
$child := get("/items/{id}"): fn (req: web.GetRequest) => $req.path end
$child := middleware: fn (req: web.Request) => $req end
$app = web.App
$app := group("/v1", $child)
web.dispatch($app, web.testRequest("GET", "/v1/items/123"))
web.dispatch($app, web.testRequest("GET", "/items/123"))'''
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                results = self._run(source, serialized=serialized)
                self.assertEqual([r.fields['status'] for r in results], [200, 404])
                self.assertEqual(results[0].fields['body'], '/v1/items/123')

    def test_group_builder_runs_once_and_composes_root(self):
        source = '''import {std.web, std.web.[get, group]}
$app = web.App
$app := group("/api"): fn (router: web.Router) =>
  $r = $router
  $r := get("/"): fn (req: web.GetRequest) => "root" end
  $r
end
web.dispatch($app, web.testRequest("GET", "/api"))'''
        self.assertEqual(self._run(source)[0].fields['body'], 'root')

    def test_prepared_app_and_route_descriptors_from_valiance(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/users/{id}"): fn(req: web.GetRequest) => "one" end
$descriptors = web.routes($app) ?
$prepared = web.prepare($app) ?
web.dispatch($prepared, web.testRequest("GET", "/users/21"))'''
        for optimized in (False, True):
            for serialized in (False, True):
                with self.subTest(optimized=optimized, serialized=serialized):
                    response = self._run(source, optimize=optimized, serialized=serialized)[0]
                    self.assertEqual(response.fields['status'], 200)
                    self.assertEqual(response.fields['body'], 'one')
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/p"): fn(req: web.GetRequest) => "ok" end
web.routes($app)'''
        descriptors = self._run(source)[0]
        self.assertEqual([(item.fields['method'].type_name, item.fields['automatic'])
                          for item in descriptors],
                         [('web.Method.GET', 0), ('web.Method.HEAD', 1),
                          ('web.Method.OPTIONS', 1)])
        for item in descriptors:
            self.assertEqual(item.fields['template'], '/p')
            self.assertEqual(item.fields['path'], '/p')
            self.assertEqual(item.fields['source'].type_name, 'None')

    def test_preconditions_from_valiance_are_usable_with_try_operator(self):
        source = r'''import {std.web, std.web.App}
$app = App
$app := put("/item"): fn(req: web.PutRequest) -> String | web.Error =>
  web.preconditions($req, web.Validators(etag = "\"v2\"")) ?
  "updated"
end
web.dispatch($app, web.testRequest("PUT", "/item"))
web.dispatch($app, web.testRequest("PUT", "/item", web.withHeader(web.Headers, "If-Match", "\"v1\"")))'''
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                responses = self._run(source, serialized=serialized)
                self.assertEqual([r.fields['status'] for r in responses], [200, 412])

    def test_prepare_rejects_ambiguous_routes_before_dispatch(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/{first}"): fn(req: web.GetRequest) => "one" end
$app := get("/{second}"): fn(req: web.GetRequest) => "two" end
web.prepare($app)
web.routes($app)'''
        outputs = self._run(source)
        self.assertEqual([x.type_name for x in outputs],
                         ['web.ConfigError.InvalidRoute', 'web.ConfigError.InvalidRoute'])

    def test_native_errors_propagate_through_try_operator(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/search"): fn(req: web.GetRequest) -> String | web.Error =>
  web.requireParam(web.queryParams($req), "term") ?
end
web.dispatch($app, web.testRequest("GET", "/search?term=first&term=second"))'''
        for optimized in (False, True):
            for serialized in (False, True):
                with self.subTest(optimized=optimized, serialized=serialized):
                    res = self._run(source, optimize=optimized, serialized=serialized)[0]
                    self.assertEqual(res.fields['status'], 400)
        # ConfigError from a native route utility also needs to short-circuit.
        source = r'''import {std.web, std.web.App}
define \build -> web.PreparedApp | web.ConfigError =>
  $app = App
  $app := get("/{id}"): fn(req: web.GetRequest) => "one" end
  $app := get("/{name}"): fn(req: web.GetRequest) => "two" end
  web.prepare($app) ?
end
\build'''
        result = self._run(source)[0]
        self.assertEqual(result.type_name, 'web.ConfigError.InvalidRoute')

    def test_query_helpers_and_response_headers_via_language(self):
        source = '''import {std.web, std.web.[get]}
$app = web.App
$app := get("/go"): fn (req: web.GetRequest) =>
  $qs = web.queryParams($req)
  $vals = web.paramValues($qs, "x")
  $res = web.Response(body = "ok")
  $res = web.withHeader($res, "X-Test", "yes")
  $res = web.cachePrivate($res, 30)
  $res
end
web.dispatch($app, web.testRequest("GET", "/go?x=one&x=two"))'''
        response = self._run(source)[0]
        self.assertEqual(response.fields['status'], 200)
        self.assertEqual(response.fields['body'], 'ok')
        from valiance.std.web import _unwrap_response
        headers = _unwrap_response(response).headers
        self.assertEqual(headers.one('X-Test'), 'yes')
        self.assertEqual(headers.one('Cache-Control'), 'private, max-age=30')


    def test_returned_typed_error_and_middleware_http_errors(self):
        source = '''import {std.web, std.web.[get, middleware]}
$app = web.App
$app := get("/bad"): fn (req: web.GetRequest) -> web.Error => web.Error.BadRequest("bad item") end
$app := middleware: fn (req: web.Request) -> Result[web.Request, web.Error] =>
  if $req.path == "/blocked" => web.Error.Forbidden("blocked")
  else => $req
  end
end
web.dispatch($app, web.testRequest("GET", "/bad"))
web.dispatch($app, web.testRequest("GET", "/blocked"))'''
        results = self._run(source, serialized=True)
        self.assertEqual([r.fields['status'] for r in results], [400, 403])
        self.assertEqual([r.fields['body'] for r in results], ['bad item', 'blocked'])

    def test_status_variant_cases_and_response_status_helper(self):
        source = '''import {std.web}
web.withStatus(web.Response(body = "created"), web.Status.Created)
web.Error.BadRequest("bad")
web.Status.BadRequest'''
        response, error, status = self._run(source, serialized=True)
        self.assertEqual(response.fields['status'], 201)
        self.assertEqual(error.type_name, 'web.Error.BadRequest')
        self.assertEqual(status.type_name, 'web.Status.BadRequest')

    def test_parameter_header_and_cookie_helpers_in_language(self):
        source = '''import {std.web}
$p = web.queryParams(web.testRequest("GET", "/?q=one&q=two"))
web.paramOne($p, "q")
web.requireParam($p, "absent")
web.header(web.Response(body = "ok"), "Content-Type")
web.withCookie(web.Response, "sid", "value")
web.deleteCookie(web.Response, "sid")'''
        results = self._run(source, serialized=True)
        self.assertEqual(results[0].type_name, 'web.Error.AmbiguousParam')
        self.assertEqual(results[1].type_name, 'web.Error.MissingParam')
        self.assertEqual(results[2].type_name, 'Some')
        self.assertEqual(results[2].fields['value'], 'text/plain; charset=utf-8')
        from valiance.std.web import _unwrap_response
        added = _unwrap_response(results[3]).headers.values('Set-Cookie')
        removed = _unwrap_response(results[4]).headers.values('Set-Cookie')
        self.assertEqual(len(added), 1)
        self.assertIn('Secure', added[0])
        self.assertIn('Max-Age=0', removed[0])

    def test_invalid_handler_return_type_diagnostic(self):
        source = '''import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => 42 end'''
        analyzer = Analyser()
        analyzer.analyse(parse(source))
        self.assertTrue(analyzer.diagnostics)
        self.assertTrue(any('no overloads' in d for d in analyzer.diagnostics), analyzer.diagnostics)

    def test_niladic_app_is_independent_per_constructor_call(self):
        source = '''import {std.web}
$a = web.App
$b = web.App
$a
$b'''
        a, b = self._run(source, serialized=True)
        self.assertIsNot(a, b)
        self.assertEqual(a.fields['routes'].type_name, 'None')
        self.assertEqual(b.fields['routes'].type_name, 'None')
        self.assertIsNot(a.fields['routes'], b.fields['routes'])



    def test_binary_response_constructor_and_bounded_readers(self):
        from valiance.std.web import _unwrap_response
        source = '''import {std.web, std.file.#byte}
web.Response(body = [0 #byte, 128 #byte, 255 #byte])
web.Response(status = web.Status.Accepted, body = [42 #byte])
web.collectBody(web.Response(body = [0 #byte, 128 #byte, 255 #byte]))
web.responseText(web.Response(body = [255 #byte]))
web.responseText(web.Response(body = "ok"), 1)
web.responseText(web.Response(body = "ok"), 2)
web.collectBody(web.Response(body = [0 #byte, 128 #byte]), 1)'''
        for optimized, serialized in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                response, accepted, collected, bad_utf8, too_small, text, oversized = self._run(
                    source, optimize=optimized, serialized=serialized)
                self.assertEqual(_unwrap_response(response).bytes, b'\x00\x80\xff')
                self.assertEqual((_unwrap_response(accepted).status, _unwrap_response(accepted).bytes),
                                 (202, b'*'))
                self.assertEqual(_unwrap_response(response).headers.one('Content-Type'),
                                 'application/octet-stream')
                self.assertEqual(bytes(map(int, collected)), b'\x00\x80\xff')
                self.assertEqual(bad_utf8.type_name, 'web.Error.BadText')
                self.assertEqual(too_small.type_name, 'web.Error.BodyTooLarge')
                self.assertEqual(text, 'ok')
                self.assertEqual(oversized.type_name, 'web.Error.BodyTooLarge')

    def test_binary_post_echo_survives_middleware_and_bytecode(self):
        from valiance.std.web import _unwrap_response
        source = '''import {std.web, std.web.App, std.file.#byte}
$app = App
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) =>
    web.withHeader($res, "X-After", "seen")
  end}
end
$app := post("/echo"): fn(req: web.PostRequest) -> web.Response | web.Error =>
  $binary = web.bodyBytes($req) ?
  web.Response(body = $binary)
end
web.dispatch($app, web.testRequest("POST", "/echo", web.Headers, [0 #byte, 128 #byte, 255 #byte]))'''
        for optimized, serialized in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result, = self._run(source, optimize=optimized, serialized=serialized)
                response = _unwrap_response(result)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.bytes, b'\x00\x80\xff')
                self.assertEqual(response.headers.one('X-After'), 'seen')
                self.assertEqual(response.headers.one('Content-Type'), 'application/octet-stream')


class WebObjectFriendlyAndRequestsTests(unittest.TestCase):
    """Public imports and request helpers must work through compiled Valiance."""

    def execute(self, source, *, optimize=False, serialized=False):
        analyzer = Analyser()
        typed = analyzer.analyse(parse(source))
        self.assertEqual(analyzer.diagnostics, [])
        program = compile_program(typed, optimize=optimize)
        return run(loads(dumps(program)) if serialized else program)

    def test_importing_app_automatically_imports_all_route_verbs(self):
        source = '''import {std.web.[App, GetRequest, PostRequest, dispatch, testRequest]}
$app = App
$app := get("/a"): fn(req: GetRequest) => "get" end
$app := post("/b"): fn(req: PostRequest) => "post" end
dispatch($app, testRequest("GET", "/a"))
dispatch($app, testRequest("POST", "/b"))'''
        for optimize, serialize in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(optimize=optimize, serialize=serialize):
                res = self.execute(source, optimize=optimize, serialized=serialize)
                self.assertEqual([r.fields['body'] for r in res], ['get', 'post'])

    def test_importing_router_exposes_route_methods_and_group_automatically(self):
        source = '''import {std.web, std.web.[App, Router]}
$router = Router
$router := get("/"): fn(req: web.GetRequest) => "nested" end
$app = App
$app := group("/users", $router)
web.dispatch($app, web.testRequest("GET", "/users"))'''
        result = self.execute(source, serialized=True)[0]
        self.assertEqual((result.fields['status'], result.fields['body']), (200, 'nested'))

    def test_selected_app_alias_works_with_auto_registered_methods(self):
        source = '''import {std.web, std.web.[App as HttpApp]}
$app = HttpApp
$app := get("/"): fn(req: web.GetRequest) => "alias" end
web.dispatch($app, web.testRequest("GET", "/"))'''
        self.assertEqual(self.execute(source)[0].fields['body'], 'alias')

    def test_cookie_parser_uses_distinct_ordered_pairs_and_ambiguity_errors(self):
        source = '''import {std.web}
$headers = web.withHeader(web.Headers, "Cookie", "sid=first; sid=second; theme=dark")
$req = web.testRequest("GET", "/", $headers)
web.cookies($req)
web.cookie($req, "sid")
web.cookie($req, "theme")'''
        pairs, repeated, unique = self.execute(source, serialized=True)
        self.assertEqual(pairs.type_name, 'web.CookiePairs')
        self.assertEqual(list(pairs.fields['names'].fields['value']), ['sid', 'sid', 'theme'])
        self.assertEqual(list(pairs.fields['values'].fields['value']), ['first', 'second', 'dark'])
        self.assertEqual(repeated.type_name, 'web.Error.AmbiguousCookie')
        self.assertEqual(unique.fields['value'], 'dark')

    def test_conditional_get_and_last_modified_through_app(self):
        source = '''import {std.web}
$app = web.App
$app := web.get("/doc"): fn(req: web.GetRequest) =>
  web.conditional($req, web.withETag(web.Response(body = "content"), "v2"))
end
$h = web.withHeader(web.Headers, "If-None-Match", "W/\\"v2\\"")
web.dispatch($app, web.testRequest("GET", "/doc", $h))
web.dispatch($app, web.testRequest("GET", "/doc"))
web.withLastModified(web.Response(body = "ok"), 0)'''
        import valiance.std.web as native
        matched, unmatched, modified = self.execute(source, serialized=True)
        self.assertEqual((matched.fields['status'], matched.fields['body']), (304, ''))
        self.assertEqual((unmatched.fields['status'], unmatched.fields['body']), (200, 'content'))
        self.assertEqual(native._unwrap_response(modified).headers.one('Last-Modified'),
                         'Thu, 01 Jan 1970 00:00:00 GMT')

    def test_reverse_url_generation_returns_typed_config_errors(self):
        source = '''import {std.web}
$app = web.App
$app := web.get("/items/{id}"): fn(req: web.GetRequest) => "ok" end
web.routeUrl($app, web.Method.GET, "/items/{id}", dict{"id" => "a b"})
web.routeUrl($app, web.Method.GET, "/items/{id}", dict{"other" => "bad"})'''
        good, bad = self.execute(source, serialized=True)
        self.assertEqual(good, '/items/a%20b')
        self.assertEqual(bad.type_name, 'web.ConfigError.InvalidRoute')


class WebMiddlewareNegotiationAndHintsTests(unittest.TestCase):
    """New v4 API and actual HTTP/1.1 informational-response coverage."""

    def evaluate(self, source, *, optimize=False, serialized=False):
        analyser = Analyser()
        nodes = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(nodes, optimize=optimize)
        return run(loads(dumps(program)) if serialized else program)

    def test_after_middleware_runs_on_error_and_in_reverse_order(self):
        from valiance.std.web import _unwrap_response
        source = '''import {std.web, std.web.App}
$app = App
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) =>
    web.withHeader($res, "X-Outer", "yes")
  end}
end
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) =>
    web.withHeader($res, "X-Inner", "yes")
  end}
end
$app := get("/ok"): fn(req: web.GetRequest) => "ok" end
web.dispatch($app, web.testRequest("GET", "/ok"))
web.dispatch($app, web.testRequest("GET", "/missing"))'''
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                ok, error = self.evaluate(source, optimize=optimized, serialized=serialized)
                self.assertEqual((ok.fields['status'], error.fields['status']), (200, 404))
                for response in (ok, error):
                    headers = _unwrap_response(response).headers
                    self.assertEqual(headers.one('X-Outer'), 'yes')
                    self.assertEqual(headers.one('X-Inner'), 'yes')

    def test_after_middleware_returns_error_and_outer_still_runs(self):
        from valiance.std.web import _unwrap_response
        source = '''import {std.web, std.web.App}
$app = App
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) =>
    web.withHeader($res, "X-Outer", "done")
  end}
end
$app := middleware: fn(req: web.Request) =>
  record{request => $req, after => fn(res: web.Response) -> Result[web.Response, web.Error] =>
    web.Error.Forbidden("blocked after")
  end}
end
$app := get("/"): fn(req: web.GetRequest) => "original" end
web.dispatch($app, web.testRequest("GET", "/"))'''
        response = _unwrap_response(self.evaluate(source, serialized=True)[0])
        self.assertEqual(response.status, 403)
        self.assertEqual(response.headers.one('X-Outer'), 'done')

    def test_accept_specificity_quality_and_invalid_header(self):
        from valiance.std import _web_core as c
        headers = c.Headers((('Accept', 'text/html;q=0, */*;q=1'),))
        req = c.test_request('GET', '/', headers=headers)
        self.assertFalse(c.accepts(req, 'text/html'))
        self.assertTrue(c.accepts(req, 'text/plain'))
        req = c.test_request('GET', '/', headers=c.Headers((('Accept', 'application/json;q=0.5, application/*;q=0'),)))
        self.assertTrue(c.accepts(req, 'application/json'))
        self.assertFalse(c.accepts(req, 'application/xml'))
        self.assertTrue(c.accepts(c.test_request('GET', '/'), 'application/json'))
        for malformed in ('text/html;q=1.1234', 'text/html;q=no', '*/json', 'text/html,', 'text/html;q=2'):
            req = c.test_request('GET', '/', headers=c.Headers((('Accept', malformed),)))
            with self.subTest(malformed=malformed):
                with self.assertRaises(c.WebError) as caught:
                    c.accepts(req, 'text/html')
                self.assertEqual(caught.exception.name, 'InvalidHeader')

    def test_valiance_accepts_typed_result(self):
        source = '''import {std.web}
$h = web.withHeader(web.Headers, "Accept", "text/html;q=0, */*;q=1")
$r = web.testRequest("GET", "/", $h)
web.accepts($r, "text/html")
web.accepts($r, "text/plain")'''
        self.assertEqual(self.evaluate(source, serialized=True), [0, 1])

    def test_early_hints_metadata_and_invalid_link(self):
        from valiance.std import _web_core as c
        from valiance.std.web import _unwrap_response
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/"): fn(req: web.GetRequest) =>
 web.withEarlyHints(web.Response(body = "ok"), ["</style.css>; rel=preload; as=style"])
end
web.dispatch($app, web.testRequest("GET", "/"))'''
        for optimized, serialized in ((False, False), (True, True)):
            result = _unwrap_response(self.evaluate(source, optimize=optimized, serialized=serialized)[0])
            self.assertEqual(result.early_hints, ('</style.css>; rel=preload; as=style',))
        for link in ('</evil>\r\nX-Smuggle: yes; rel=preload',
                     '<javascript:alert(1)>; rel=preload',
                     '<//evil.test/x>; rel=preload',
                     '</x>; rel=next',
                     '<é>; rel=preload'):
            with self.subTest(link=link):
                with self.assertRaises(c.WebError):
                    c.with_early_hints(c.Response(body='ok'), [link])

    def test_http1_103_precedes_final_response(self):
        from valiance.std import _web_core as c, _web_http1 as h
        import socket
        app = c.App().route('GET', '/', lambda _: c.with_early_hints(
            c.Response(body='ok'), ['</style.css>; rel=preload; as=style']))
        server = h.serve(app, port=0)
        try:
            with socket.create_connection(('127.0.0.1', server.bound_address[1]), timeout=3) as conn:
                conn.settimeout(3)
                conn.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                chunks = []
                while chunk := conn.recv(65536):
                    chunks.append(chunk)
            raw = b''.join(chunks)
            self.assertEqual(raw.count(b'HTTP/1.1 103 '), 1)
            self.assertTrue(raw.startswith(b'HTTP/1.1 103 '))
            self.assertIn(b'Link: </style.css>; rel=preload; as=style', raw)
            self.assertIn(b'HTTP/1.1 200 ', raw)
            self.assertTrue(raw.endswith(b'ok'))
        finally:
            server.shutdown()


class WebJsonResponseIncrementTests(unittest.TestCase):
    """v8 JSON uses std.json encoding and strict HTTP media validation."""

    def execute(self, source: str, *, optimized=False, serialized=False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)

    def test_json_response_record_status_and_default(self):
        source = '''import {std.web}
web.jsonResponse(record{name => "Ada", count => 2}, web.Status.Accepted, web.Headers)
web.jsonResponse(42)'''
        from valiance.std import web as bridge
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result, default = self.execute(source, optimized=optimized, serialized=serialized)
                response = bridge._unwrap_response(result)
                self.assertEqual(response.status, 202)
                self.assertEqual(response.body, b'{"name":"Ada","count":2}')
                self.assertEqual(response.headers.one('Content-Type'), 'application/json')
                self.assertEqual(bridge._unwrap_response(default).body, b'42')

    def test_json_response_in_object_friendly_route(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/json"): fn(req: web.GetRequest) -> web.Response | ValueError =>
  web.jsonResponse(record{answer => 42}) ?
end
web.dispatch($app, web.testRequest("GET", "/json"))'''
        from valiance.std import web as bridge
        result, = self.execute(source, serialized=True)
        response = bridge._unwrap_response(result)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b'{"answer":42}')

    def test_json_body_http_media_validation_and_codec(self):
        source = '''import {std.web, std.json, std.web.App, std.file.#byte}
$app = App
$app := post("/json"): fn(req: web.PostRequest) -> web.Response | web.Error | ValueError =>
  $text = web.bodyJsonText($req) ?
  $number = json.decode[Int]($text) ?
  web.jsonResponse($number) ?
end
$headers = web.withHeader(web.Headers, "Content-Type", "application/problem+json")
web.dispatch($app, web.testRequest("POST", "/json", $headers, [52 #byte, 50 #byte]))
web.dispatch($app, web.testRequest("POST", "/json", web.Headers, [52 #byte, 50 #byte]))
web.dispatch($app, web.testRequest("POST", "/json", $headers, [255 #byte]))'''
        from valiance.std import web as bridge
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                valid, bad_media, bad_utf8 = self.execute(source, optimized=optimized,
                                                         serialized=serialized)
                responses = [bridge._unwrap_response(item) for item in (valid, bad_media, bad_utf8)]
                self.assertEqual([r.status for r in responses], [200, 415, 400])
                self.assertEqual(responses[0].body, b'42')

    def test_json_schema_failure_is_safe_500_pending_typed_body_api(self):
        source = '''import {std.web, std.json, std.web.App, std.file.#byte}
$app = App
$app := post("/"): fn(req: web.PostRequest) -> web.Response | web.Error | ValueError =>
  $text = web.bodyJsonText($req) ?
  $number = json.decode[Int]($text) ?
  web.jsonResponse($number) ?
end
$headers = web.withHeader(web.Headers, "Content-Type", "application/json")
web.dispatch($app, web.testRequest("POST", "/", $headers, [123 #byte, 98 #byte, 97 #byte, 100 #byte]))'''
        from valiance.std import web as bridge
        result, = self.execute(source)
        response = bridge._unwrap_response(result)
        self.assertEqual(response.status, 500)
        self.assertNotIn('bad', response.body)

    def test_real_http1_json_roundtrip(self):
        import http.client
        from valiance.std import web as bridge
        source = '''import {std.web, std.json, std.web.App}
$app = App
$app := post("/number"): fn(req: web.PostRequest) -> web.Response | web.Error | ValueError =>
  $text = web.bodyJsonText($req) ?
  $number = json.decode[Int]($text) ?
  web.jsonResponse(record{value => $number}) ?
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        owner, = self.execute(source, serialized=True)
        token = bridge._lease_token(owner)
        try:
            with bridge._SERVER_LEASES_LOCK:
                host, port = bridge._SERVER_LEASES[token].bound_address
            connection = http.client.HTTPConnection(host, port, timeout=4)
            try:
                connection.request('POST', '/number', b'42',
                                   {'Content-Type': 'application/json'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'application/json')
                self.assertEqual(response.read(), b'{"value":42}')
                connection.request('POST', '/number', b'42',
                                   {'Content-Type': 'text/plain'})
                response = connection.getresponse()
                self.assertEqual(response.status, 415)
                self.assertEqual(response.read(), b'Unsupported Media Type')
            finally:
                connection.close()
        finally:
            bridge._close_server_token(token)
        self.assertFalse(bridge._SERVER_LEASES)

    def test_redirect_and_created_optional_arguments(self):
        source = '''import {std.web}
web.redirect("/moved", web.Status.MovedPermanently)
web.created("/created", web.Response(body = "saved"))
web.redirect("//external.test")'''
        from valiance.std import web as bridge
        moved, created, rejected = self.execute(source)
        self.assertEqual(bridge._unwrap_response(moved).status, 301)
        self.assertEqual(bridge._unwrap_response(moved).headers.one('Location'), '/moved')
        self.assertEqual(bridge._unwrap_response(created).status, 201)
        self.assertEqual(bridge._unwrap_response(created).body, 'saved')
        self.assertEqual(rejected.type_name, 'web.Error.InvalidLocation')


class WebOwnedHTTP1LifecycleTests(unittest.TestCase):
    """Exercise Valiance-owned HTTP/1.1 listener values, not only Python handles."""

    def execute(self, source: str, *, optimized: bool = False, serialized: bool = False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=optimized)
        return run(loads(dumps(program)) if serialized else program)


    def test_valiance_binary_response_on_live_owned_http1_listener(self):
        import http.client
        from valiance.std import web as bridge
        source = '''import {std.web, std.web.App, std.file.#byte}
$app = App
$app := get("/bytes"): fn(req: web.GetRequest) =>
  web.Response(body = [0 #byte, 128 #byte, 255 #byte])
end
$app := post("/echo"): fn(req: web.PostRequest) -> web.Response | web.Error =>
  web.Response(body = web.bodyBytes($req) ?)
end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        owner, = self.execute(source, serialized=True)
        token = bridge._lease_token(owner)
        try:
            with bridge._SERVER_LEASES_LOCK:
                host, port = bridge._SERVER_LEASES[token].bound_address
            connection = http.client.HTTPConnection(host, port, timeout=4)
            try:
                connection.request('GET', '/bytes')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'application/octet-stream')
                self.assertEqual(response.getheader('Content-Length'), '3')
                self.assertEqual(response.read(), b'\x00\x80\xff')
                connection.request('HEAD', '/bytes')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Length'), '3')
                self.assertEqual(response.read(), b'')
                connection.request('POST', '/echo', b'\x00\x80\xff',
                                   {'Content-Type': 'application/octet-stream'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'application/octet-stream')
                self.assertEqual(response.read(), b'\x00\x80\xff')
            finally:
                connection.close()
        finally:
            bridge._close_server_token(token)
        self.assertFalse(bridge._SERVER_LEASES)

    def test_serve_bind_shutdown_and_error_values(self):
        from valiance.std import web as bridge
        source = '''import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
$addresses = web.boundAddresses $server
web.shutdown $server
$addresses'''
        for optimized, serialized in ((False, False), (True, False), (False, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                report, endpoints = self.execute(source, optimized=optimized,
                                                 serialized=serialized)
                self.assertEqual(report.type_name, 'web.ServerStopReport')
                self.assertEqual(report.fields['reason'].type_name,
                                 'web.ServerStopReason.Requested')
                self.assertEqual((report.fields['accepted'], report.fields['completed']),
                                 (0, 0))
                self.assertEqual(len(endpoints), 1)
                self.assertEqual(endpoints[0].fields['protocol'], 'HTTP/1.1')
                self.assertGreater(int(endpoints[0].fields['port']), 0)
                self.assertFalse(bridge._SERVER_LEASES)

        bad = self.execute('''import {std.web}
web.serve(web.App, web.ServerConfig(port = 99999))''')[0]
        self.assertEqual(bad.type_name, 'web.ServerError.Failed')
        self.assertFalse(bridge._SERVER_LEASES)

    def test_owned_handle_scope_exit_runs_destructor(self):
        from valiance.std import web as bridge
        source = '''import {std.web}
define \\probe =>
  $server = web.serve(web.App, web.ServerConfig(port = 0)) ?
  web.boundAddresses $server
end
\\probe'''
        for serialized in (False, True):
            endpoints, = self.execute(source, serialized=serialized)
            self.assertEqual(len(endpoints), 1)
            self.assertFalse(bridge._SERVER_LEASES)

    def test_real_http_request_during_owned_lifetime(self):
        from valiance.std import web as bridge
        import socket
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/live"): fn(req: web.GetRequest) => "owned server" end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        owned, = self.execute(source)
        token = bridge._lease_token(owned)
        try:
            with bridge._SERVER_LEASES_LOCK:
                host, port = bridge._SERVER_LEASES[token].bound_address
            with socket.create_connection((host, port),
                                          timeout=3) as client:
                client.settimeout(3)
                client.sendall(b'GET /live HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                received = bytearray()
                while True:
                    data = client.recv(32768)
                    if not data:
                        break
                    received.extend(data)
            self.assertIn(b'HTTP/1.1 200', received)
            self.assertTrue(received.endswith(b'owned server'))
            with bridge._SERVER_LEASES_LOCK:
                active = bridge._SERVER_LEASES[token]
                self.assertEqual(active.counts, (1, 1))
        finally:
            bridge._close_server_token(token)
        self.assertFalse(bridge._SERVER_LEASES)

    def test_owned_handle_cannot_be_duplicated(self):
        # A handle must not be copied into two independent owners.
        source = '''import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
$left = $server
$right = $server
web.shutdown $left'''
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(analyser.diagnostics)

    def test_wait_observes_external_stop_and_consumes_handle(self):
        # The development adapter has no signal integration for explicit serve.
        # Stop its listener externally, then let wait consume the owned handle.
        from valiance.std import web as bridge
        from threading import Thread
        from unittest.mock import patch
        from valiance.std import _web_http1 as http1

        serve = http1.serve
        stoppers = []

        def serve_and_stop(*args, **kwargs):
            owner = serve(*args, **kwargs)
            stopper = Thread(target=owner.shutdown, daemon=True)
            stopper.start()
            stoppers.append(stopper)
            return owner

        source = '''import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
web.wait $server'''
        with patch.object(http1, 'serve', side_effect=serve_and_stop):
            report, = self.execute(source)
        for stopper in stoppers:
            stopper.join(timeout=3)
        self.assertEqual(report.type_name, 'web.ServerStopReport')
        self.assertFalse(bridge._SERVER_LEASES)


if __name__ == '__main__':
    unittest.main()


class WebStaticFileTests(unittest.TestCase):
    """Bounded fixed files and confined static routes through Valiance."""

    def execute(self, source: str, *, optimized: bool = False, serialized: bool = False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        bytecode = compile_program(typed, optimize=optimized)
        return run(loads(dumps(bytecode)) if serialized else bytecode)

    def test_fixed_file_binary_page_download_and_missing_error(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import web as bridge
        with TemporaryDirectory() as folder:
            binary = Path(folder) / 'source.bin'
            binary.write_bytes(b'\x00\x80\xff')
            page = Path(folder) / 'page.bin'
            page.write_text('<b>Hi</b>', encoding='utf-8')
            source = f'''import {{std.web}}
web.file("{binary.as_posix()}")
web.page("{page.as_posix()}")
web.download("{binary.as_posix()}", "parent/é report.txt")
web.file("{(Path(folder) / 'absent.txt').as_posix()}")'''
            for optimized, serialized in ((False, False), (True, True)):
                with self.subTest(optimized=optimized, serialized=serialized):
                    values = self.execute(source, optimized=optimized, serialized=serialized)
                    one, html, attachment = [bridge._unwrap_response(v) for v in values[:3]]
                    self.assertEqual(one.bytes, b'\x00\x80\xff')
                    self.assertEqual(one.headers.one('Content-Type'), 'application/octet-stream')
                    self.assertEqual(one.headers.one('Cache-Control'), 'no-store')
                    self.assertEqual(one.headers.one('X-Content-Type-Options'), 'nosniff')
                    self.assertEqual(html.headers.one('Content-Type'), 'text/html; charset=utf-8')
                    self.assertEqual(attachment.bytes, one.bytes)
                    self.assertIn('filename*=UTF-8\'\'%C3%A9%20report.txt',
                                  attachment.headers.one('Content-Disposition'))
                    self.assertEqual(values[3].type_name, 'web.Error.NotFound')

    def test_fixed_file_invalid_filename_and_response_limit(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from valiance.std import _web_core as core
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'file.txt'
            path.write_text('abcdefghij')
            with self.assertRaises(core.WebError) as raised:
                core.download(str(path), 'bad\r\nHeader: injected')
            self.assertEqual(raised.exception.name, 'InvalidHeader')
            with patch.object(core, '_FILE_RESPONSE_LIMIT', 3):
                # Explicit path reads use the shared bounded reader.
                with self.assertRaises(core.WebError) as raised:
                    core.file(str(path))
                self.assertEqual(raised.exception.name, 'Internal')

    def test_static_mount_route_precedence_and_object_friendly_imports(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import web as bridge
        if os.name != 'posix':
            self.skipTest('staticRouter is deliberately POSIX-only until no-follow Windows support')
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'index.html').write_text('Home')
            (root / 'data.bin').write_bytes(b'\x00\x80\xff')
            source = f'''import {{std.web, std.web.App}}
$app = App
$app := group("/assets", web.staticRouter("{root}") ?)
$app := get("/assets/health"): fn(req: web.GetRequest) => "healthy" end
web.dispatch($app, web.testRequest("GET", "/assets/data.bin"))
web.dispatch($app, web.testRequest("GET", "/assets"))
web.dispatch($app, web.testRequest("GET", "/assets/health"))
web.dispatch($app, web.testRequest("POST", "/assets/health"))'''
            for optimized, serialized in ((False, False), (True, True)):
                with self.subTest(optimized=optimized, serialized=serialized):
                    results = self.execute(source, optimized=optimized, serialized=serialized)
                    responses = [bridge._unwrap_response(result) for result in results]
                    self.assertEqual([r.status for r in responses], [200, 200, 200, 405])
                    self.assertEqual([r.bytes for r in responses[:3]],
                                     [b'\x00\x80\xff', b'Home', b'healthy'])

    def test_static_path_confinement_and_symlink_rejection(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import _web_core as core
        if os.name != 'posix':
            self.skipTest('requires secure dirfd no-follow traversal')
        with TemporaryDirectory() as folder, TemporaryDirectory() as outside:
            root = Path(folder)
            (root / '.secrets').write_text('hidden')
            (root / 'index.html').write_text('Home')
            (root / 'dir').mkdir()
            (root / 'dir' / 'index.html').write_text('Nested')
            secret = Path(outside) / 'secret.txt'
            secret.write_text('private')
            (root / 'link').symlink_to(secret)
            (root / 'linked-dir').symlink_to(outside, target_is_directory=True)
            app = core.App().group('/assets', core.Router().route(
                'GET', '/{*path}', lambda request: core.static_file(folder, request)))
            for path in ('/assets/.secrets', '/assets/link', '/assets/linked-dir/secret.txt',
                         '/assets/dir/.secret', '/assets/absent'):
                self.assertEqual(core.dispatch(app, core.test_request('GET', path)).status, 404)
            for malformed in ('/assets/../secret.txt', '/assets/%2e%2e/secret.txt',
                              '/assets/%2fetc', '/assets/%252fetc'):
                with self.subTest(path=malformed):
                    with self.assertRaises(core.DispatchError):
                        core.test_request('GET', malformed)
            self.assertEqual(core.dispatch(app, core.test_request('GET', '/assets/dir')).bytes,
                             b'Nested')
            with self.assertRaises(core.ConfigError):
                core.static_root(str(root / 'linked-dir'))

    def test_static_ranges_head_validators_and_416_size(self):
        import os
        from dataclasses import replace
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import _web_core as core
        if os.name != 'posix':
            self.skipTest('Secure static routes require POSIX no-follow directory descriptors')
        with TemporaryDirectory() as folder:
            (Path(folder) / 'payload.bin').write_bytes(b'0123456789')
            app = core.App().route('GET', '/{*path}', lambda req: core.static_file(folder, req))
            full = core.dispatch(app, core.test_request('GET', '/payload.bin'))
            self.assertEqual(full.status, 200)
            self.assertEqual(full.headers.one('Accept-Ranges'), 'bytes')
            etag = full.headers.one('ETag')
            modified = full.headers.one('Last-Modified')
            self.assertIsNotNone(modified)
            requests = (
                (('If-None-Match', etag), 304, b''),
                (('Range', 'bytes=2-4'), 206, b'234'),
                (('Range', 'bytes=-2'), 206, b'89'),
                (('Range', 'bytes=200-300'), 416, b''),
                (('Range', 'items=0-1'), 200, b'0123456789'),
                (('If-Range', '"other"'), 200, b'0123456789'),
            )
            for (name, value), status, body in requests:
                with self.subTest(name=name, value=value):
                    fields = [('Range', 'bytes=0-0')] if name == 'If-Range' else []
                    fields.append((name, value))
                    response = core.dispatch(app, core.test_request(
                        'GET', '/payload.bin', headers=core.Headers(tuple(fields))))
                    self.assertEqual(response.status, status)
                    self.assertEqual(response.bytes, body)
                    if status == 416:
                        self.assertEqual(response.headers.one('Content-Range'), 'bytes */10')
            head = core.dispatch(app, core.test_request(
                'HEAD', '/payload.bin', headers=core.Headers((('Range', 'bytes=0-0'),))))
            self.assertIsNone(head.body)
            self.assertEqual(head.headers.one('Content-Length'), '10')
            multiple = core.dispatch(app, core.test_request(
                'GET', '/payload.bin', headers=core.Headers((('Range', 'bytes=0-1,8-9'),))))
            self.assertEqual(multiple.status, 206)
            self.assertIn('multipart/byteranges', multiple.headers.one('Content-Type'))
            self.assertIn(b'Content-Range: bytes 0-1/10', multiple.bytes)
            self.assertIn(b'Content-Range: bytes 8-9/10', multiple.bytes)

    def test_live_http1_static_binary_range_and_head(self):
        import http.client
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import web as bridge
        if os.name != 'posix':
            self.skipTest('staticRouter is deliberately POSIX-only')
        with TemporaryDirectory() as folder:
            (Path(folder) / 'file.bin').write_bytes(b'\x00\x80\xffAB')
            source = f'''import {{std.web, std.web.App}}
$app = App
$app := group("/static", web.staticRouter("{folder}") ?)
web.serve($app, web.ServerConfig(port = 0)) ?'''
            owner, = self.execute(source, serialized=True)
            token = bridge._lease_token(owner)
            try:
                with bridge._SERVER_LEASES_LOCK:
                    host, port = bridge._SERVER_LEASES[token].bound_address
                connection = http.client.HTTPConnection(host, port, timeout=5)
                try:
                    connection.request('GET', '/static/file.bin')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), b'\x00\x80\xffAB')
                    etag = response.getheader('ETag')
                    connection.request('GET', '/static/file.bin', headers={'Range': 'bytes=1-2'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), b'\x80\xff')
                    connection.request('HEAD', '/static/file.bin')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.getheader('Content-Length'), '5')
                    self.assertEqual(response.read(), b'')
                    connection.request('GET', '/static/file.bin', headers={'If-None-Match': etag})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 304)
                    self.assertEqual(response.read(), b'')
                finally:
                    connection.close()
            finally:
                bridge._close_server_token(token)

    def test_std_path_inputs_and_invalid_static_root(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import web as bridge
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'file.bin').write_bytes(b'\x00\xff')
            source = f'''import {{std.web, std.path.Path, std.web.App}}
web.file(Path("{(root / 'file.bin').as_posix()}"))'''
            result = self.execute(source, serialized=True)
            self.assertEqual(bridge._unwrap_response(result[0]).bytes, b'\x00\xff')

            if os.name == 'posix':
                source += f'''
$app = App
$app := group("/files", web.staticRouter(Path("{root.as_posix()}")) ?)
web.dispatch($app, web.testRequest("GET", "/files/file.bin"))
web.staticRouter("{(root / 'does-not-exist').as_posix()}")'''
                result = self.execute(source, serialized=True)
                self.assertEqual(bridge._unwrap_response(result[1]).bytes, b'\x00\xff')
                self.assertEqual(result[2].type_name, 'web.ConfigError.InvalidRoute')


class WebMultipartBufferedTests(unittest.TestCase):
    """Buffered multipart parser and Valiance bridge; no streaming/leased files."""

    @staticmethod
    def payload(*parts, boundary='MULTI'):
        result = bytearray()
        for headers, data in parts:
            result.extend(b'--' + boundary.encode() + b'\r\n')
            for name, value in headers:
                result.extend(name.encode() + b': ' + value.encode('utf-8') + b'\r\n')
            result.extend(b'\r\n' + data + b'\r\n')
        result.extend(b'--' + boundary.encode() + b'--\r\n')
        return bytes(result)

    @staticmethod
    def part(name, value, filename=None, media=None):
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        headers = [('Content-Disposition', disposition)]
        if media is not None:
            headers.append(('Content-Type', media))
        return headers, value

    def req(self, raw, *, method='POST', media='multipart/form-data; boundary=MULTI'):
        return web.test_request(method, '/upload',
            headers=web.Headers((('Content-Type', media),)), body=raw)

    def execute(self, source, *, optimized=False, serialized=False):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        bytecode = compile_program(typed, optimize=optimized)
        return run(loads(dumps(bytecode)) if serialized else bytecode)

    def test_core_repeated_fields_and_exact_upload_octets(self):
        body = self.payload(
            self.part('tag', b'a'),
            self.part('upload', b'\x00\xff\r\n--MULTIX\r\n\x80', filename='../../untrusted.bin',
                      media='application/octet-stream'),
            self.part('tag', b'b'),
            self.part('upload', b'\x01', filename='second.bin'))
        form = web.multipart(self.req(body))
        self.assertEqual(form.fields.values('tag'), ('a', 'b'))
        self.assertEqual([item.filename for item in form.uploads('upload')],
                         ['../../untrusted.bin', 'second.bin'])
        self.assertEqual(form.uploads('upload')[0].data, b'\x00\xff\r\n--MULTIX\r\n\x80')
        self.assertEqual(form.uploads('upload')[0].size, 15)
        self.assertIsNone(form.upload_one('missing'))
        self.assertEqual(form.uploads('unknown'), ())
        with self.assertRaises(web.WebError) as caught:
            form.upload_one('upload')
        self.assertEqual(caught.exception.name, 'AmbiguousParam')

    def test_core_media_boundary_and_malformed_parts(self):
        valid = self.payload(self.part('name', b'Ada'))
        bad = (
            (valid, 'text/plain', 'UnsupportedMediaType'),
            (valid, 'multipart/form-data', 'InvalidMultipart'),
            (valid, 'multipart/form-data; boundary=other', 'InvalidMultipart'),
            (valid[:-4], 'multipart/form-data; boundary=MULTI', 'InvalidMultipart'),
            (valid + b'junk', 'multipart/form-data; boundary=MULTI', 'InvalidMultipart'),
            (valid.replace(b'\r\n', b'\n'), 'multipart/form-data; boundary=MULTI', 'InvalidMultipart'),
            (self.payload(self.part('name', b'\xff')), 'multipart/form-data; boundary=MULTI', 'InvalidMultipart'),
            (valid, 'multipart/form-data; boundary="MUL TI"', 'InvalidMultipart'),
        )
        for body, media, expected in bad:
            with self.subTest(media=media, size=len(body)):
                with self.assertRaises(web.WebError) as caught:
                    web.multipart(self.req(body, media=media))
                self.assertEqual(caught.exception.name, expected)
        with self.assertRaises(web.WebError) as caught:
            web.multipart(self.req(valid, method='GET'))
        self.assertEqual(caught.exception.name, 'BodyUnavailable')

    def test_core_rejects_duplicate_or_folded_headers_and_unsupported_part_encodings(self):
        raw = self.payload(self.part('item', b'hello'))
        variants = (
            raw.replace(b'Content-Disposition:', b'Content-Disposition: form-data; name="extra"\r\nContent-Disposition:'),
            raw.replace(b'Content-Disposition:', b' Content-Disposition:'),
            raw.replace(b'Content-Disposition:', b'Content-Transfer-Encoding: base64\r\nContent-Disposition:'),
            raw.replace(b'Content-Disposition:', b'X-Unsafe: test\r\nContent-Disposition:'),
            raw.replace(b'name="item"', b'name="item"; name="duplicate"'),
            raw.replace(b'name="item"', b'filename="file.txt"'),
        )
        for body in variants:
            with self.subTest(body=body[:110]):
                with self.assertRaises(web.WebError) as caught:
                    web.multipart(self.req(body))
                self.assertEqual(caught.exception.name, 'InvalidMultipart')

    def test_core_quota_enforcement(self):
        with self.assertRaises(web.WebError) as caught:
            # Explicitly test the prior 64 KiB tightened policy; v21's
            # normative default maxMultipartFieldBytes is now 1 MiB.
            web.multipart(self.req(self.payload(self.part('field', b'A' * 65537))),
                          max_field_bytes=65536)
        self.assertEqual(caught.exception.name, 'BodyTooLarge')
        with self.assertRaises(web.WebError) as caught:
            web.multipart(self.req(self.payload(*[self.part('f', b'') for _ in range(1001)])))
        self.assertEqual(caught.exception.name, 'BodyTooLarge')
        with self.assertRaises(web.WebError) as caught:
            web.multipart(self.req(self.payload(*[self.part('f', b'', filename='x') for _ in range(101)])))
        self.assertEqual(caught.exception.name, 'BodyTooLarge')
        with self.assertRaises(web.WebError) as caught:
            web.multipart(self.req(self.payload(self.part('big', b'A' * (16 * 1024 * 1024)))))
        self.assertEqual(caught.exception.name, 'BodyTooLarge')

    def test_valiance_text_fields_and_upload_bytes_in_optimized_serialized_execution(self):
        payload = self.payload(self.part('field', b'Ada'),
                               self.part('blob', b'\x00\x80\xff', filename='photo.bin'))
        data = ', '.join(f'{byte} #byte' for byte in payload)
        source = f'''import {{std.web, std.web.App, std.file.#byte}}
$app = App
$app := post("/text"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "field") ?
end
$app := post("/bytes"): fn(req: web.PostRequest) -> web.Response | web.Error =>
  $form = web.multipart($req) ?
  $uploads = web.uploads($form, "blob")
  $upload = $uploads[0]
  web.Response(body = web.readUpload($upload) ?)
end
$headers = web.withHeader(web.Headers, "Content-Type", "multipart/form-data; boundary=MULTI")
web.dispatch($app, web.testRequest("POST", "/text", $headers, [{data}]))
web.dispatch($app, web.testRequest("POST", "/bytes", $headers, [{data}]))'''
        from valiance.std import web as bridge
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result = self.execute(source, optimized=optimized, serialized=serialized)
                text, binary = (bridge._unwrap_response(item) for item in result)
                self.assertEqual(text.body, 'Ada')
                self.assertEqual(binary.bytes, b'\x00\x80\xff')

    def test_valiance_reports_malformed_multipart_as_http_400(self):
        payload = b'--MULTI\r\nContent-Disposition: form-data; name="x"\r\n\r\nabc'
        data = ', '.join(f'{byte} #byte' for byte in payload)
        source = f'''import {{std.web, std.web.App, std.file.#byte}}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  "ok"
end
$headers = web.withHeader(web.Headers, "Content-Type", "multipart/form-data; boundary=MULTI")
web.dispatch($app, web.testRequest("POST", "/upload", $headers, [{data}]))'''
        from valiance.std import web as bridge
        result, = self.execute(source, serialized=True)
        response = bridge._unwrap_response(result)
        self.assertEqual(response.status, 400)
        self.assertNotIn('Content-Disposition', response.bytes.decode('utf-8'))

    def test_http1_live_multipart_upload_binary_roundtrip(self):
        import http.client
        from valiance.std import _web_http1 as http1
        payload = self.payload(self.part('blob', b'\x00\xff\x80', filename='photo.bin'))
        def echo(req):
            form = web.multipart(req)
            return web.Response(body=form.upload_one('blob').data)
        server = http1.serve(web.App().route('POST', '/upload', echo), port=0)
        try:
            host, port = server.bound_address
            conn = http.client.HTTPConnection(host, port, timeout=3)
            try:
                conn.request('POST', '/upload', payload,
                             {'Content-Type': 'multipart/form-data; boundary=MULTI'})
                reply = conn.getresponse()
                self.assertEqual(reply.status, 200)
                self.assertEqual(reply.read(), b'\x00\xff\x80')
                conn.request('POST', '/upload', payload[:-6],
                             {'Content-Type': 'multipart/form-data; boundary=MULTI'})
                reply = conn.getresponse()
                self.assertEqual(reply.status, 400)
                reply.read()
            finally:
                conn.close()
        finally:
            server.shutdown()

    def test_valiance_save_upload_exclusive_create_and_no_filename_trust(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.std import web as bridge
        data = self.payload(self.part('blob', b'\x00\x80\xff',
                                      filename='../../attacker-selected.txt'))
        byte_values = ', '.join(f'{octet} #byte' for octet in data)
        with TemporaryDirectory() as folder:
            destination = Path(folder) / 'output.bin'
            source = f'''import {{std.web, std.web.App, std.path.Path, std.file.#byte}}
$app = App
$app := post("/save"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  $files = web.uploads($form, "blob")
  web.saveUpload($files[0], Path("{destination.as_posix()}")) ?
  "saved"
end
$h = web.withHeader(web.Headers, "Content-Type", "multipart/form-data; boundary=MULTI")
web.dispatch($app, web.testRequest("POST", "/save", $h, [{byte_values}]))
web.dispatch($app, web.testRequest("POST", "/save", $h, [{byte_values}]))'''
            first, second = self.execute(source, serialized=True)
            self.assertEqual(bridge._unwrap_response(first).status, 200)
            self.assertEqual(bridge._unwrap_response(second).status, 409)
            self.assertEqual(destination.read_bytes(), b'\x00\x80\xff')
            self.assertFalse((Path(folder) / 'attacker-selected.txt').exists())
            if __import__('os').name == 'posix':
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_valiance_ambiguous_upload_one_propagates_typed_client_error(self):
        from valiance.std import web as bridge
        payload = self.payload(self.part('blob', b'a', filename='a.txt'),
                               self.part('blob', b'b', filename='b.txt'))
        byte_values = ', '.join(f'{octet} #byte' for octet in payload)
        source = f'''import {{std.web, std.web.App, std.file.#byte}}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  $file = web.uploadOne($form, "blob") ?
  "ok"
end
$h = web.withHeader(web.Headers, "Content-Type", "multipart/form-data; boundary=MULTI")
web.dispatch($app, web.testRequest("POST", "/upload", $h, [{byte_values}]))'''
        result, = self.execute(source)
        self.assertEqual(bridge._unwrap_response(result).status, 400)
