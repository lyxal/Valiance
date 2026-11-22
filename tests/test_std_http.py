"""Independent local-peer tests for the experimental outbound HTTP client.

The tests intentionally use Python's HTTP server, not Valiance's std.web,
so malformed framing and wire interpretation cannot pass symmetrically.
"""

from __future__ import annotations

from concurrent.futures import CancelledError, ThreadPoolExecutor
import gc
from hashlib import sha256
import json
import os
import tempfile
import ipaddress
import socket
from types import SimpleNamespace
import gzip
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Barrier, BrokenBarrierError, Event, Thread, enumerate as active_threads
from time import monotonic, sleep
import unittest
import weakref

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.vm import VirtualMachine
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.std import _http_core as core
from valiance.std._http_body import UploadPipe, MultipartUpload, _PROCESS_UPLOAD_BUDGET
from valiance.std._http_cookies import CookieJar
from valiance.std._http_errors import ErrorKind, HttpError, HttpValueError
from valiance.std import _http_transport as transport

_MODULES = ModuleLoader()


class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            # An aborted HTTP request legitimately resets a keepalive socket.
            pass

    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def _respond(self, status, content, *, delay=0, headers=()):
        self.send_response(status)
        for key, value in headers:
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        if delay:
            sleep(delay)
        if self.command != 'HEAD':
            try:
                self.wfile.write(content)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _redirect(self, status, location):
        self._respond(status, b'', headers=(('Location', location),))

    def do_GET(self):
        if self.path == '/cookie-set':
            return self._respond(200, b'set', headers=(('Set-Cookie', 'session=abc; Path=/; HttpOnly'),))
        if self.path == '/cookie-set-path':
            return self._respond(200, b'set', headers=(('Set-Cookie', 'scoped=yes; Path=/inside'),))
        if self.path == '/cookie-remove':
            return self._respond(200, b'deleted', headers=(('Set-Cookie', 'session=gone; Path=/; Max-Age=0'),))
        if self.path == '/redirect-with-cookie':
            return self._respond(302, b'', headers=(('Location', '/inspect'), ('Set-Cookie', 'hop=value; Path=/')))
        if self.path in ('/inside/inspect', '/outside/inspect', '/inspect-cookie'):
            return self._respond(200, self.headers.get('Cookie', '').encode('ascii'))
        if self.path == '/redirect-one':
            return self._redirect(302, '/redirect-two')
        if self.path == '/redirect-two':
            return self._redirect(301, '/')
        if self.path == '/redirect-loop-a':
            return self._redirect(301, '/redirect-loop-b')
        if self.path == '/redirect-loop-b':
            return self._redirect(302, '/redirect-loop-a')
        if self.path == '/redirect-invalid':
            return self._redirect(302, 'file:///etc/passwd')
        if self.path == '/redirect-cross':
            return self._redirect(302, self.server.cross_base + '/inspect')
        if self.path == '/redirect-same':
            return self._redirect(302, '/inspect')
        if self.path == '/inspect':
            content = '|'.join((
                self.headers.get('Authorization', ''),
                self.headers.get('Cookie', ''),
                self.headers.get('X-Api-Key', ''), self.command,
            ))
            return self._respond(200, content.encode('utf-8'))
        if self.path == '/slow':
            return self._respond(200, b'slow-body', delay=0.35)
        if self.path == '/cooperative-barrier':
            # Both tasks must reach this independent server BEFORE either
            # receives its response headers. This is a stronger concurrency
            # oracle than a fragile global elapsed-time assertion.
            try:
                self.server.concurrent_barrier.wait(timeout=2)
            except BrokenBarrierError:
                return self._respond(503, b'non-cooperative requests')
            return self._respond(200, b'overlap confirmed')
        if self.path == '/many-headers':
            return self._respond(200, b'headers', headers=tuple((f'X-Field-{n}', 'y') for n in range(24)))
        if self.path == '/large-headers':
            return self._respond(200, b'headers', headers=(('X-Large', 'x' * 8000),))
        if self.path == '/gzip-small':
            return self._respond(200, gzip.compress(b'decoded data'),
                                 headers=(('Content-Encoding', 'gzip'),))
        if self.path == '/gzip-bomb':
            return self._respond(200, gzip.compress(b'A' * 200_000),
                                 headers=(('Content-Encoding', 'gzip'),))
        if self.path == '/huge':
            return self._respond(200, b'a' * 120_000)
        for name, data in (
            ('/json-null', b'null'), ('/json-bool', b'true'),
            ('/json-number', b'12345678901234567890.123456789'),
            ('/json-text', b'"hello"'), ('/json-array', b'[1, null]'),
            ('/json-object', b'{"ok":true}'),
        ):
            if self.path == name:
                return self._respond(200, data)
        if self.path == '/json':
            return self._respond(200, b'{"ok":true,"count":2,"items":[null,"yes"]}')
        if self.path == '/number':
            return self._respond(200, b'42')
        if self.path == '/badjson':
            return self._respond(200, b'{"a":1,"a":2}')
        if self.path == '/utf8':
            return self._respond(200, 'naïve'.encode('utf-8'))
        if self.path == '/binary':
            return self._respond(200, bytes(range(256)))
        if self.path == '/headers':
            return self._respond(200, b'headers', headers=(('Set-Cookie', 'a=1'), ('Set-Cookie', 'b=2')))
        if self.path == '/404':
            return self._respond(404, b'missing')
        return self._respond(200, b'hello')

    def do_HEAD(self):
        return self._respond(200, b'hello')

    def do_POST(self):
        if self.path == '/delayed-upload':
            # Leave admitted buffered requests live long enough to measure
            # simultaneous transport leases, not merely aggregate throughput.
            sleep(0.3)
        if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
            chunks = []
            while True:
                head = self.rfile.readline().split(b';', 1)[0].strip()
                size = int(head, 16)
                if not size:
                    while self.rfile.readline().strip():
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            content = b''.join(chunks)
        else:
            length = int(self.headers.get('Content-Length', 0))
            content = self.rfile.read(length)
        if self.path.split('?', 1)[0] == '/inspect-upload':
            header = self.headers.get('Content-Type', '').encode('latin-1')
            return self._respond(201, header + b'\n' + content)
        if self.path == '/sha256':
            return self._respond(201, sha256(content).hexdigest().encode('ascii'))
        if self.path == '/redirect-303':
            return self._redirect(303, '/inspect')
        if self.path == '/redirect-302-post':
            return self._redirect(302, '/inspect')
        if self.path == '/redirect-307':
            return self._redirect(307, '/upload')
        if self.path == '/redirect-308':
            return self._redirect(308, '/upload')
        return self._respond(201, content)

    do_PUT = do_POST
    do_PATCH = do_POST

    def do_PURGE(self):
        return self._respond(200, b'purged')


def evaluate(source: str, *, optimized=False, serialized=False):
    analyser = Analyser(module_loader=_MODULES)
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=optimized)
    return run(loads(dumps(program)) if serialized else program)


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.other = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.other_thread = Thread(target=cls.other.serve_forever, daemon=True)
        cls.other_thread.start()
        cls.other_base = f'http://127.0.0.1:{cls.other.server_port}'
        cls.server.cross_base = cls.other_base

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.other.shutdown()
        cls.other.server_close()
        cls.other_thread.join(timeout=2)


class HTTPValueTests(unittest.TestCase):
    def test_async_collection_handles_thousands_of_ready_chunks(self):
        # A completed Future executes its callbacks inline. Recursively
        # collecting small already-available chunks must never hit recursion
        # depth or accidentally retain process-wide buffer reservations.
        shared = transport._BufferBudget(16 * 1024, lambda: None)
        commands = []
        reactor = SimpleNamespace(_command=commands.append)
        body = transport.ResponseBody(reactor, 1, 16 * 1024, shared)
        for _ in range(5000):
            self.assertTrue(body._push(b'x'))
        body._finish()
        self.assertEqual(body.collect_async(5000).result(timeout=2), b'x' * 5000)
        self.assertEqual(shared.used, 0)
        self.assertTrue(body._closed)
        self.assertEqual(len(commands), 1)

    def test_cancel_pending_chunk_read_aborts_instead_of_dropping_data(self):
        shared = transport._BufferBudget(16 * 1024, lambda: None)
        commands = []
        body = transport.ResponseBody(SimpleNamespace(_command=commands.append),
                                      7, 16 * 1024, shared)
        read = body.read_async()
        self.assertTrue(read.cancel())
        self.assertTrue(body._closed)
        self.assertIsNone(body._pending)
        self.assertEqual(commands, [('cancel', 7, None)])
        self.assertTrue(body._push(b'later'))
        self.assertEqual(shared.used, 0)

    def test_cancel_complete_body_collection_releases_pending_reader(self):
        shared = transport._BufferBudget(16 * 1024, lambda: None)
        commands = []
        reactor = SimpleNamespace(_command=commands.append)
        body = transport.ResponseBody(reactor, 3, 16 * 1024, shared)
        collection = body.collect_async(4096)
        self.assertIsNotNone(body._pending)
        self.assertTrue(collection.cancel())
        self.assertTrue(body._closed)
        self.assertIsNone(body._pending)
        self.assertEqual(commands, [('cancel', 3, None)])

    def test_unread_completed_body_releases_budget_on_finalization(self):
        shared = transport._BufferBudget(16 * 1024, lambda: None)
        reactor = SimpleNamespace(_command=lambda action: None)
        body = transport.ResponseBody(reactor, 2, 16 * 1024, shared)
        self.assertTrue(body._push(b'unread' * 500))
        body._finish()
        self.assertEqual(shared.used, 3000)
        reference = weakref.ref(body)
        del body
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(shared.used, 0)

    def test_headers_are_ordered_and_case_insensitive(self):
        headers = core.Headers((('Set-Cookie', 'a'), ('set-cookie', 'b'), ('X-Trace', 'c')))
        self.assertEqual(headers.values('SET-cookie'), ('a', 'b'))
        self.assertEqual(headers.pairs[1], ('set-cookie', 'b'))
        with self.assertRaises(HttpValueError):
            headers.one('Set-Cookie')
        self.assertEqual(headers.replace('sEt-CoOkIe', 'z').values('set-cookie'), ('z',))

    def test_wire_injection_rejected_before_request(self):
        for name, value in (('Host\r\nX-Injected', 'yes'), ('X-Test', 'a\nb'), ('Bad Header', 'x')):
            with self.subTest(name=name):
                with self.assertRaises(HttpValueError):
                    core.Headers(((name, value),))
        for url in ('file:///etc/passwd', 'http://user:pass@example.test/x',
                    'http://example.test/%GG', 'http://example.test/#fragment',
                    'http://example.test/a\\b', 'http://127.0.0.1:99999'):
            with self.subTest(url=url):
                with self.assertRaises(HttpValueError):
                    core.Request('GET', url)
        for method in ('GET\nX:bad', 'not a method', ''):
            with self.assertRaises(HttpValueError):
                core.Request(method, 'http://example.com/')
        with self.assertRaises(HttpValueError):
            core.Request('POST', 'http://example.test/', core.Headers((('Content-Length', '999'),)), b'abc')

    def test_url_normalization_and_config_guards(self):
        self.assertEqual(core.validate_url('HTTPS://EXAMPLE.COM'), 'https://example.com/')
        self.assertEqual(core.validate_url('http://[::1]:8888/a'), 'http://[::1]:8888/a')
        self.assertEqual(core.encode_query((('x', 'a b'), ('x', '+'))), 'x=a%20b&x=%2B')
        for kwargs in ({'max_pending': 0}, {'body_buffer_bytes': 100, 'max_body_bytes': 1},
                       {'max_connections_per_origin': 1000},
                       {'max_client_buffer_bytes': 8192},
                       {'max_response_header_bytes': 0},
                       {'max_response_header_fields': -2},
                       {'read_idle_timeout_ms': 0},
                       {'read_idle_timeout_ms': True},
                       {'write_idle_timeout_ms': 0},
                       {'write_idle_timeout_ms': True},
                       {'deny_non_public_ips': 1},
                       {'deny_non_public_ips': True, 'proxy': 'http://127.0.0.1:8080'}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HttpValueError):
                    core.ClientConfig(**kwargs)

    def test_form_encoding_and_query_repeats(self):
        self.assertEqual(core.encode_form((('key', 'two words'), ('key', 'a+b&c'),
                                           ('unicode', 'café'))),
                         b'key=two+words&key=a%2Bb%26c&unicode=caf%C3%A9')
        self.assertEqual(core.url_with_query('http://example.test/?old=1',
                                              (('old', '+'), ('n', 'a b'))),
                         'http://example.test/?old=1&old=%2B&n=a%20b')
        with self.assertRaises(HttpValueError):
            core.encode_form((('x', 'x' * 100),), limit=10)

    def test_multipart_encoding_and_injection_defenses(self):
        blob, ctype = core.encode_multipart((
            core.MultipartPart('title', 'café'.encode('utf-8'), content_type='text/plain; charset=utf-8'),
            core.MultipartPart('upload', b'\x00\xff\r\n', filename='a";injected.bin', content_type='application/octet-stream'),
        ), boundary='unit_test_boundary')
        parsed = BytesParser(policy=policy.default).parsebytes(
            b'MIME-Version: 1.0\r\nContent-Type: ' + ctype.encode() + b'\r\n\r\n' + blob)
        pieces = parsed.get_payload()
        self.assertEqual(len(pieces), 2)
        self.assertEqual(pieces[0].get_payload(decode=True), 'café'.encode('utf-8'))
        self.assertEqual(pieces[1].get_payload(decode=True), b'\x00\xff\r\n')
        self.assertNotIn(b'filename="a";injected.bin"', blob)
        for malformed in (core.MultipartPart('x\r\nBad: x', b'hi'),
                          core.MultipartPart('x', b'hi', filename='bad\nname'),
                          core.MultipartPart('x', b'hi', content_type='text/plain\r\nX: evil')):
            with self.subTest(malformed=malformed):
                with self.assertRaises(HttpValueError):
                    core.encode_multipart((malformed,))
        with self.assertRaises(HttpValueError):
            core.encode_multipart((core.MultipartPart('x', b'x' * 4096),), limit=256)

    def test_secure_cookie_cannot_be_overwritten_by_insecure_origin(self):
        jar = CookieJar()
        # A non-secure Set-Cookie from HTTP must not overwrite secure-only
        # credentials that already exist in the same domain/path scope.
        self.assertTrue(jar.receive('https://example.com/', 'auth=secure; Secure; Path=/'))
        self.assertFalse(jar.receive('http://example.com/', 'auth=attacker; Path=/'))
        self.assertEqual(jar.outbound('https://example.com/'), 'auth=secure')

    def test_cookie_jar_host_path_expiry_and_prefix_rules(self):
        jar = CookieJar()
        origin = 'http://shop.example.com/cart/add'
        self.assertTrue(jar.receive(origin, 'sid=abc; HttpOnly'))
        self.assertEqual(jar.outbound('http://shop.example.com/cart/checkout'), 'sid=abc')
        self.assertIsNone(jar.outbound('http://shop.example.com/other'))
        self.assertIsNone(jar.outbound('http://sub.shop.example.com/cart/checkout'))
        self.assertFalse(jar.receive(origin, 'super=1; Domain=com'))
        self.assertFalse(jar.receive(origin, 'other=1; Domain=badexample.com'))
        self.assertFalse(jar.receive(origin, 'bad=1; Domain=co.uk'))
        self.assertFalse(jar.receive(origin, 'x=bad\r\nInjected: yes'))
        self.assertFalse(jar.receive(origin, '__Secure-sid=x; Path=/'))
        self.assertFalse(jar.receive(origin, 'bad=x; Secure'))
        self.assertTrue(jar.receive('https://shop.example.com/', '__Host-ok=yes; Path=/; Secure'))
        self.assertTrue(jar.receive('https://shop.example.com/', '__Secure-ok=yes; Secure'))
        self.assertIn('__Host-ok=yes', jar.outbound('https://shop.example.com/'))
        self.assertNotIn('__Host-ok=yes', jar.outbound('http://shop.example.com/') or '')
        self.assertFalse(jar.receive('https://shop.example.com/', '__Host-bad=1; Domain=example.com; Secure; Path=/'))
        self.assertTrue(jar.receive(origin, 'sid=deleted; Max-Age=0'))
        self.assertIsNone(jar.outbound(origin))

    def test_domain_cookies_require_public_suffix_validation(self):
        jar = CookieJar()
        if jar._is_public_suffix is None:
            self.assertFalse(jar.receive('https://a.example.com/', 'domain=1; Domain=example.com'))
        else:
            self.assertTrue(jar.receive('https://a.example.com/', 'domain=1; Domain=example.com'))
            self.assertEqual(jar.outbound('https://b.example.com/'), 'domain=1')
            self.assertFalse(jar.receive('https://a.foo.co.uk/', 'bad=1; Domain=co.uk'))
        self.assertFalse(jar.receive('http://127.0.0.1/', 'ip=1; Domain=127.0.0.1'))

    def test_explicit_origin_allowlist_config_validation(self):
        allowed = core.ClientConfig(allowed_origins=('https://example.com', 'https://example.com:443'))
        self.assertEqual(allowed.allowed_origins, ('https://example.com/', 'https://example.com:443/'))
        for bad in ((), ('https://example.com/admin',), ('https://example.com/?x=1',),
                    ('file:///etc/hosts',), ('http://example.com/#hash',)):
            with self.subTest(bad=bad):
                with self.assertRaises(HttpValueError):
                    core.ClientConfig(allowed_origins=bad)
        with self.assertRaises(HttpValueError):
            core.ClientConfig(require_https=1)

    def test_network_address_selection_and_public_policy(self):
        ipv4 = b'\x02\x00\x00\x50' + socket.inet_aton('192.168.1.5') + b'\x00' * 8
        ipv6 = (b'\x0a\x00\x00\x50' + b'\x00' * 4 +
                socket.inet_pton(socket.AF_INET6, '2001:db8::5') + b'\x00' * 4)
        self.assertEqual(transport._destination_ip(socket.AF_INET, ipv4),
                         ipaddress.ip_address('192.168.1.5'))
        self.assertEqual(transport._destination_ip(socket.AF_INET6, ipv6),
                         ipaddress.ip_address('2001:db8::5'))
        with self.assertRaises(HttpError):
            transport._destination_ip(socket.AF_UNIX, ipv4)
        with self.assertRaises(HttpError):
            transport._destination_ip(socket.AF_INET6, ipv6[:16])
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254',
                        '::1', 'fc00::1', '::ffff:127.0.0.1', '2001:db8::5'):
            self.assertFalse(ipaddress.ip_address(address).is_global, address)
        self.assertTrue(transport._is_public_destination(ipaddress.ip_address('1.1.1.1')))
        self.assertTrue(transport._is_public_destination(ipaddress.ip_address('2606:4700:4700::1111')))
        for candidate in ('2002:7f00:1::1', '2001:0::1',
                          '64:ff9b::7f00:1', '64:ff9b:1::7f00:1'):
            self.assertFalse(transport._is_public_destination(ipaddress.ip_address(candidate)))

    def test_upload_pipe_limits_and_terminal_state(self):
        pipe = UploadPipe(5, expected_bytes=6)
        self.assertEqual(pipe.write_async(b'hello').result(), 5)
        waiting = pipe.write_async(b'!')
        self.assertFalse(waiting.done())
        self.assertEqual(pipe.read_native(3), b'hel')
        self.assertEqual(waiting.result(timeout=1), 1)
        with self.assertRaises(HttpError):
            pipe.write_async(b'overflow').result()
        pipe.finish()
        self.assertEqual(pipe.read_native(10), b'lo')
        self.assertEqual(pipe.read_native(10), b'!')
        self.assertEqual(pipe.read_native(10), b'')
        with self.assertRaises(HttpError):
            pipe.write_async(b'bad').result()
        self.assertEqual(UploadPipe(1).write_async(b'').result(), 0)

    def test_process_upload_budget_covers_pending_writers_and_release(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            self.assertEqual(budget.used, 0)
            old_limit, old_peak = budget.maximum, budget.peak
            budget.maximum, budget.peak = 80, 0
        try:
            first, second = UploadPipe(20), UploadPipe(20)
            self.assertEqual(budget.used, 80)
            first.write_async(b'a' * 20).result(timeout=1)
            pending = first.write_async(b'b' * 20)
            self.assertFalse(pending.done())
            with self.assertRaises(HttpError) as limited:
                UploadPipe(1)
            self.assertEqual(limited.exception.kind, ErrorKind.RESOURCE_LIMIT)
            self.assertEqual(first.read_native(20), b'a' * 20)
            self.assertEqual(pending.result(timeout=1), 20)
            self.assertEqual(first.read_native(20), b'b' * 20)
            self.assertEqual(budget.peak, 80)
            # Once a transfer owns the pipe, its release reclaims capacity
            # even if the application still holds a reference to the pipe.
            first.reserve()
            first.release_transfer()
            self.assertEqual(budget.used, 40)
            replacement = UploadPipe(20)
            replacement.release_writer()
            replacement.release_source()
            second.release_writer()
            second.release_source()
            self.assertEqual(budget.used, 0)
        finally:
            with budget._lock:
                self.assertEqual(budget.used, 0)
                budget.maximum = old_limit
                budget.peak = max(old_peak, budget.peak)

    def test_multipart_fragments_reserve_the_same_process_upload_budget(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            self.assertEqual(budget.used, 0)
            original = budget.maximum
        source = UploadPipe(16)
        body, _ = core.encode_multipart_stream((
            core.MultipartPart('meta', b'Z' * 70),
            core.MultipartPart('file', source, filename='file.bin'),
        ), boundary='multipart-budget')
        with budget._lock:
            budget.maximum = 32 + body.buffered_bytes - 1
        try:
            with self.assertRaises(HttpError) as failed:
                body.reserve()
            self.assertEqual(failed.exception.kind, ErrorKind.RESOURCE_LIMIT)
            self.assertEqual(budget.used, 32)
            # A failed multipart admission must not strand a pipe lease.
            body.release_transfer()
            self.assertEqual(budget.used, 0)
            with budget._lock:
                budget.maximum = 32 + body.buffered_bytes
            second = UploadPipe(16)
            usable, _ = core.encode_multipart_stream((
                core.MultipartPart('meta', b'Z' * 70),
                core.MultipartPart('file', second, filename='file.bin'),
            ), boundary='multipart-budget')
            self.assertEqual(body.buffered_bytes, usable.buffered_bytes)
            usable.reserve()
            self.assertEqual(budget.used, 32 + usable.buffered_bytes)
            usable.release_transfer()
            self.assertEqual(budget.used, 0)
            usable.release_transfer()  # idempotent
            self.assertEqual(budget.used, 0)
        finally:
            body.release_transfer()
            with budget._lock:
                self.assertEqual(budget.used, 0)
                budget.maximum = original

    def test_upload_reservations_remain_bounded_under_thread_contention(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            self.assertEqual(budget.used, 0)
            maximum, previous_peak = budget.maximum, budget.peak
            budget.maximum, budget.peak = 8192, 0
        try:
            def worker(_):
                for _ in range(50):
                    pipe = UploadPipe(256)
                    pipe.reserve()
                    self.assertEqual(pipe.write_async(b'X' * 200).result(timeout=1), 200)
                    self.assertEqual(pipe.read_native(256), b'X' * 200)
                    pipe.release_transfer()
                    pipe.release_transfer()

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(worker, range(8)))
            with budget._lock:
                self.assertEqual(budget.used, 0)
                self.assertLessEqual(budget.peak, budget.maximum)
        finally:
            with budget._lock:
                self.assertEqual(budget.used, 0)
                budget.maximum = maximum
                budget.peak = max(previous_peak, budget.peak)

    def test_abandoned_python_upload_pipe_releases_capacity_on_gc(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            initial = budget.used
        pipe = UploadPipe(17)
        self.assertEqual(budget.used, initial + 34)
        del pipe
        gc.collect()
        self.assertEqual(budget.used, initial)

    def test_upload_pipe_aborts_pending_writer(self):
        pipe = UploadPipe(4)
        pipe.write_async(b'1234').result()
        waiting = pipe.write_async(b'5')
        pipe.abort()
        with self.assertRaises(HttpError):
            waiting.result(timeout=1)
        with self.assertRaises(HttpError):
            pipe.read_native(100)


class HTTPTransportTests(ServerTestCase):
    def test_repeated_client_shutdown_reclaims_native_transfers_and_reactor_threads(self):
        # Exercise reuse, early body abandonment, and cancellation against
        # repeated construction/destruction of independent libcurl multi pools.
        original_threads = {id(t) for t in active_threads()
                            if t.name == 'valiance-http-reactor'}
        response_budget = transport._PROCESS_BUFFER_BUDGET
        with response_budget._lock:
            initial_response = response_budget.used
        with _PROCESS_UPLOAD_BUDGET._lock:
            initial_upload = _PROCESS_UPLOAD_BUDGET.used
        for index in range(36):
            client = transport.Client(core.ClientConfig(max_connections=2,
                                          max_connections_per_origin=2,
                                          total_timeout_ms=1800))
            try:
                response = client.send(core.Request('GET', self.base + '/'))
                if index % 3:
                    self.assertEqual(response.body.bytes(1024), b'hello')
                else:
                    response.body.close()
                pending = client.send_async(core.Request('GET', self.base + '/slow'))
                pending.cancel()
            finally:
                client.close()
            self.assertFalse(client._reactor._thread.is_alive())
            self.assertFalse(client._reactor._transfers)
            self.assertEqual(client._reactor._buffer_budget.used, 0)
            self.assertEqual(client._reactor._admission_slots._value,
                             client._reactor.config.max_pending)
            if pending.done() and not pending.cancelled():
                try:
                    pending.result().body.close()
                except HttpError:
                    pass
        gc.collect()
        self.assertEqual({id(t) for t in active_threads()
                          if t.name == 'valiance-http-reactor'}, original_threads)
        with response_budget._lock:
            self.assertEqual(response_budget.used, initial_response)
        with _PROCESS_UPLOAD_BUDGET._lock:
            self.assertEqual(_PROCESS_UPLOAD_BUDGET.used, initial_upload)

    def test_closed_client_releases_rejected_transfer_lease(self):
        called = []
        client = transport.Client()
        client.close()
        future = client.send_async(core.Request('POST', self.base + '/sha256',
                                                body=b'abc'), on_finish=lambda: called.append(1))
        with self.assertRaises(HttpError) as closed:
            future.result(timeout=1)
        self.assertEqual(closed.exception.kind, ErrorKind.CLOSED)
        self.assertEqual(called, [1])

    def test_known_length_native_completion_before_writer_finish_is_safe(self):
        """libcurl can complete after the last byte but before producer EOF."""
        from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET
        with _PROCESS_UPLOAD_BUDGET._lock:
            before = _PROCESS_UPLOAD_BUDGET.used
        pipe = UploadPipe(4, expected_bytes=3)
        finalized = []
        pipe.set_finalizer(lambda: finalized.append(True))
        pipe.reserve()
        self.assertEqual(pipe.write_async(b'abc').result(), 3)
        self.assertEqual(pipe.read_native(100), b'abc')
        pipe.release_transfer()  # Native completion wins the race.
        self.assertFalse(finalized)  # The producer still owns a live writer.
        pipe.finish()            # Must not report Error.Closed.
        pipe.finish()            # Idempotent.
        self.assertEqual(finalized, [True])
        self.assertEqual(_PROCESS_UPLOAD_BUDGET.used, before)

    def test_process_upload_budget_recovers_on_completed_and_cancelled_transfers(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            before = budget.used
        pipe = UploadPipe(32, 3)
        self.assertEqual(pipe.write_async(b'abc').result(), 3)
        pipe.finish()
        with transport.Client() as client:
            response = client.send(core.Request('POST', self.base + '/sha256', body=pipe))
            self.assertEqual(response.body.bytes(100), sha256(b'abc').hexdigest().encode())
        self.assertTrue(pipe.releasable)
        self.assertEqual(budget.used, before)

        pipe2 = UploadPipe(8)
        with transport.Client() as client:
            pending = client.send_async(core.Request('POST', self.base + '/sha256', body=pipe2))
            self.assertTrue(pending.cancel())
            limit = monotonic() + 5
            while monotonic() < limit and not pipe2.releasable:
                sleep(0.01)
        self.assertTrue(pipe2.releasable)
        self.assertEqual(budget.used, before)

    def test_process_budget_admits_buffered_uploads_atomically(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            self.assertEqual(budget.used, 0)
            original = budget.maximum
            budget.maximum = 100
        try:
            with transport.Client() as client:
                first = client.send_async(core.Request('POST', self.base + '/delayed-upload',
                                                       body=b'x' * 70))
                self.assertEqual(budget.used, 70)
                with self.assertRaises(HttpError) as excess:
                    client.send_async(core.Request('POST', self.base + '/delayed-upload',
                                                   body=b'y' * 70))
                self.assertEqual(excess.exception.kind, ErrorKind.RESOURCE_LIMIT)
                self.assertEqual(budget.used, 70)
                self.assertEqual(first.result(timeout=5).body.bytes(256), b'x' * 70)
                self.assertEqual(budget.used, 0)
                # Rejected acquisition must not consume an in-flight permit.
                success = client.send_async(core.Request('POST', self.base + '/delayed-upload',
                                                         body=b'z' * 70))
                self.assertEqual(success.result(timeout=5).body.bytes(256), b'z' * 70)
                self.assertEqual(budget.used, 0)
        finally:
            with budget._lock:
                self.assertEqual(budget.used, 0)
                budget.maximum = original

    def test_cancelled_buffered_upload_releases_global_reservation(self):
        budget = _PROCESS_UPLOAD_BUDGET
        with budget._lock:
            self.assertEqual(budget.used, 0)
            original = budget.maximum
            budget.maximum = 256
        try:
            with transport.Client() as client:
                pending = client.send_async(core.Request('POST', self.base + '/delayed-upload',
                                                         body=b'x' * 128))
                self.assertEqual(budget.used, 128)
                self.assertTrue(pending.cancel())
                deadline = monotonic() + 5
                while monotonic() < deadline and budget.used:
                    sleep(.01)
                self.assertEqual(budget.used, 0)
                again = client.send_async(core.Request('POST', self.base + '/delayed-upload',
                                                       body=b'y' * 128))
                self.assertEqual(again.result(timeout=5).body.bytes(256), b'y' * 128)
                self.assertEqual(budget.used, 0)
        finally:
            with budget._lock:
                self.assertEqual(budget.used, 0)
                budget.maximum = original

    def test_streamed_upload_known_and_unknown_length(self):
        payload = bytes(range(256)) * 120
        for declared in (len(payload), None):
            with self.subTest(declared=declared):
                pipe = UploadPipe(1024, expected_bytes=declared)
                producer_completed = Event()
                producer_errors = []

                def produce():
                    try:
                        for start in range(0, len(payload), 333):
                            pipe.write_async(payload[start:start + 333]).result(timeout=3)
                        pipe.finish()
                    except BaseException as exc:
                        producer_errors.append(exc)
                    finally:
                        producer_completed.set()

                with transport.Client() as client:
                    pending = client.send_async(core.Request('POST', self.base + '/sha256',
                                                             body=pipe))
                    thread = Thread(target=produce, daemon=True)
                    thread.start()
                    response = pending.result(timeout=5)
                    self.assertEqual(response.status, 201)
                    self.assertEqual(response.body.bytes(100), sha256(payload).hexdigest().encode())
                    self.assertTrue(producer_completed.wait(timeout=2))
                    thread.join(timeout=2)
                    self.assertFalse(producer_errors, producer_errors)

    def test_streaming_multipart_upload_multiple_parts_and_lengths(self):
        payload = bytes(range(256)) * 128
        for expected in (len(payload), None):
            with self.subTest(expected=expected):
                pipe = UploadPipe(997, expected_bytes=expected)
                body, content_type = core.encode_multipart_stream((
                    core.MultipartPart("description", b"a small note", content_type="text/plain"),
                    core.MultipartPart("file", pipe, filename="large.bin",
                                       content_type="application/octet-stream"),
                    core.MultipartPart("after", b"last", content_type="text/plain"),
                ))
                self.assertIsInstance(body, MultipartUpload)
                self.assertEqual(body.expected_bytes is None, expected is None)
                self.assertLess(body.buffered_bytes, 1024)
                errors = []

                def produce():
                    try:
                        for at in range(0, len(payload), 709):
                            pipe.write_async(payload[at:at + 709]).result(timeout=5)
                        pipe.finish()
                    except BaseException as exc:
                        errors.append(exc)

                with transport.Client(core.ClientConfig(max_buffered_upload_bytes=2048)) as client:
                    future = client.send_async(core.Request(
                        "POST", self.base + "/inspect-upload",
                        core.Headers((("Content-Type", content_type),)), body))
                    thread = Thread(target=produce, daemon=True)
                    thread.start()
                    response = future.result(timeout=10)
                    result = response.body.bytes(len(payload) + 4096)
                    thread.join(timeout=4)
                    self.assertFalse(thread.is_alive())
                    self.assertFalse(errors, errors)
                ctype, wire = result.split(b'\n', 1)
                parsed = BytesParser(policy=policy.default).parsebytes(
                    b'MIME-Version: 1.0\r\nContent-Type: ' + ctype + b'\r\n\r\n' + wire)
                self.assertEqual([p.get_payload(decode=True) for p in parsed.get_payload()],
                                 [b'a small note', payload, b'last'])
                self.assertTrue(body._released)
                self.assertTrue(pipe.releasable)

    def test_multipart_stream_validation_and_nonreplayable_redirect(self):
        pipe = UploadPipe(32, expected_bytes=3)
        with self.assertRaises(HttpError):
            core.encode_multipart_stream((core.MultipartPart('a', pipe),
                                          core.MultipartPart('b', pipe)))
        for bad in (core.MultipartPart('bad\nname', UploadPipe(8)),
                    core.MultipartPart('file', UploadPipe(8), filename='evil\rname')):
            with self.assertRaises(HttpValueError):
                core.encode_multipart_stream((bad,))
        stream = UploadPipe(4, expected_bytes=3)
        stream.write_async(b'abc').result(timeout=1)
        stream.finish()
        multipart, ctype = core.encode_multipart_stream((core.MultipartPart('file', stream),))
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            with self.assertRaises(HttpError) as failure:
                client.send(core.Request('POST', self.base + '/redirect-307',
                                         core.Headers((('Content-Type', ctype),)), multipart))
            self.assertEqual(failure.exception.stage, 'redirect')
        self.assertTrue(stream.releasable)

    def test_rejected_reuse_of_multipart_stream_preserves_active_request(self):
        pipe = UploadPipe(8, expected_bytes=3)
        body, ctype = core.encode_multipart_stream((core.MultipartPart('file', pipe),))
        headers = core.Headers((('Content-Type', ctype),))
        request = core.Request('POST', self.base + '/upload', headers, body)
        with transport.Client(core.ClientConfig(max_pending=1)) as client:
            first = client.send_async(request)
            with self.assertRaises(HttpError) as rejected:
                client.send_async(request)
            self.assertEqual(rejected.exception.kind, ErrorKind.RESOURCE_LIMIT)
            pipe.write_async(b'abc').result(timeout=2)
            pipe.finish()
            self.assertIn(b'abc', first.result(timeout=5).body.bytes(2048))
            next_request = client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(next_request.body.bytes(16), b'hello')
        self.assertTrue(pipe.releasable)
        self.assertTrue(body._released)

    def test_multipart_stream_buffered_parts_obey_client_limit(self):
        stream = UploadPipe(16)
        multipart, ctype = core.encode_multipart_stream((
            core.MultipartPart('name', b'x' * 150),
            core.MultipartPart('file', stream, filename='test.bin'),
        ))
        with transport.Client(core.ClientConfig(max_buffered_upload_bytes=100)) as client:
            with self.assertRaises(HttpError) as caught:
                client.send(core.Request('POST', self.base + '/inspect-upload',
                                         core.Headers((('Content-Type', ctype),)), multipart))
            self.assertEqual(caught.exception.kind, ErrorKind.RESOURCE_LIMIT)
        self.assertTrue(multipart._released)

    def test_multipart_multiple_file_streams_and_content_length(self):
        first, second = UploadPipe(4, 3), UploadPipe(4, 2)
        first.write_async(b'abc').result()
        second.write_async(b'\x00\xff').result()
        first.finish()
        second.finish()
        body, content_type = core.encode_multipart_stream((
            core.MultipartPart('a', first, filename='a.txt', content_type='text/plain'),
            core.MultipartPart('b', second, filename='b.bin', content_type='application/octet-stream'),
        ), boundary='two-files')
        with self.assertRaises(HttpValueError):
            core.Request('POST', self.base + '/upload',
                         core.Headers((('Content-Length', '7'),)), body)
        with transport.Client() as client:
            response = client.send(core.Request('POST', self.base + '/inspect-upload',
                                                core.Headers((('Content-Type', content_type),
                                                              ('Content-Length', str(body.expected_bytes)))), body))
            ctype, wire = response.body.bytes(4096).split(b'\n', 1)
        parsed = BytesParser(policy=policy.default).parsebytes(
            b'MIME-Version: 1.0\r\nContent-Type: ' + ctype + b'\r\n\r\n' + wire)
        self.assertEqual([p.get_payload(decode=True) for p in parsed.get_payload()],
                         [b'abc', b'\x00\xff'])
        self.assertTrue(first.releasable and second.releasable)

    def test_multipart_multiple_stream_sources_abort_and_cleanup(self):
        first = UploadPipe(8, expected_bytes=3)
        second = UploadPipe(1, expected_bytes=2)
        first.write_async(b'abc').result()
        first.finish()
        multipart, ctype = core.encode_multipart_stream((
            core.MultipartPart('first', first), core.MultipartPart('second', second)))
        with transport.Client() as client:
            pending = client.send_async(core.Request('POST', self.base + '/inspect-upload',
                                                     core.Headers((('Content-Type', ctype),)), multipart))
            second.write_async(b'x').result()
            blocked = second.write_async(b'y')
            self.assertFalse(blocked.done())
            self.assertTrue(pending.cancel())
            with self.assertRaises((HttpError,)):
                blocked.result(timeout=5)
            limit = monotonic() + 5
            while monotonic() < limit and not (first.releasable and second.releasable):
                sleep(.01)
        self.assertTrue(first.releasable)
        self.assertTrue(second.releasable)
        self.assertTrue(multipart._released)

    def test_streamed_upload_cannot_be_redirect_replayed(self):
        pipe = UploadPipe(1024, expected_bytes=3)
        pipe.write_async(b'abc').result()
        pipe.finish()
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            with self.assertRaises(HttpError) as caught:
                client.send(core.Request('POST', self.base + '/redirect-307', body=pipe))
            self.assertEqual(caught.exception.stage, 'redirect')

    def test_headers_before_slow_body_and_eof(self):
        with transport.Client() as client:
            start = monotonic()
            response = client.send(core.Request('GET', self.base + '/slow'))
            self.assertLess(monotonic() - start, 0.32)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body.bytes(20), b'slow-body')
            with self.assertRaises(HttpError):
                response.body.read()

    def test_incremental_chunk_read_and_eof(self):
        with transport.Client() as client:
            response = client.send(core.Request('GET', self.base + '/binary'))
            pieces = []
            while (chunk := response.body.read()) is not None:
                pieces.append(chunk)
            self.assertEqual(b''.join(pieces), bytes(range(256)))
            self.assertIsNone(response.body.read())
            response.body.close()
            with self.assertRaises(HttpError):
                response.body.read()

    def test_status_and_header_multiplicity(self):
        with transport.Client() as client:
            response = client.send(core.Request('GET', self.base + '/404'))
            self.assertEqual(response.status, 404)
            self.assertEqual(response.body.bytes(100), b'missing')
            response = client.send(core.Request('GET', self.base + '/headers'))
            self.assertEqual(response.metadata.headers.values('set-cookie'), ('a=1', 'b=2'))
            self.assertEqual(response.body.bytes(100), b'headers')

    def test_extension_methods_use_canonical_transport(self):
        with transport.Client() as client:
            response = client.send(core.Request('PURGE', self.base + '/purge'))
            self.assertEqual(response.status, 200)
            self.assertEqual(response.body.bytes(20), b'purged')

    def test_binary_and_buffered_upload(self):
        with transport.Client() as client:
            response = client.send(core.Request('GET', self.base + '/binary'))
            self.assertEqual(response.body.bytes(300), bytes(range(256)))
            response = client.send(core.Request('POST', self.base + '/upload', body=b'\x00a\xff'))
            self.assertEqual(response.status, 201)
            self.assertEqual(response.body.bytes(20), b'\x00a\xff')

    def test_body_limit_and_backpressure(self):
        config = core.ClientConfig(body_buffer_bytes=4096, max_body_bytes=150_000)
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/huge'))
            self.assertEqual(response.body.bytes(120_000), b'a' * 120_000)
            response = client.send(core.Request('GET', self.base + '/huge'))
            with self.assertRaises(HttpError) as caught:
                response.body.bytes(20_000)
            self.assertEqual(caught.exception.kind, ErrorKind.RESOURCE_LIMIT)

    def test_shared_client_buffer_budget_and_resume(self):
        # Two large responses contend for a one-callback shared memory budget.
        # Both consumers must finish even when one transfer is paused waiting
        # for the other to release its queued body chunks.
        config = core.ClientConfig(body_buffer_bytes=16384,
                                   max_client_buffer_bytes=16384,
                                   max_body_bytes=150_000)
        with transport.Client(config) as client:
            responses = [client.send_async(core.Request('GET', self.base + '/huge'))
                         for _ in range(2)]
            bodies = [response.result(timeout=5).body for response in responses]
            with ThreadPoolExecutor(max_workers=2) as executor:
                tasks = [executor.submit(body.bytes, 150_000) for body in bodies]
                self.assertEqual([task.result(timeout=8) for task in tasks],
                                 [b'a' * 120_000] * 2)
            budget = client._reactor._buffer_budget
            self.assertLessEqual(budget.peak, config.max_client_buffer_bytes)
            self.assertEqual(budget.used, 0)

    def test_process_wide_buffer_budget_across_independent_clients(self):
        # Each client has its own 32 KiB queue allowance, but both must obey
        # the same 16 KiB process-wide queue ceiling and cross-client wakes.
        # Tightening the private test budget is safe only with no live queues.
        global_budget = transport._PROCESS_BUFFER_BUDGET
        with global_budget._lock:
            self.assertEqual(global_budget.used, 0)
            original_limit, original_peak = global_budget.maximum, global_budget.peak
            global_budget.maximum = 16 * 1024
            global_budget.peak = 0
        try:
            config = core.ClientConfig(body_buffer_bytes=16384,
                                       max_client_buffer_bytes=32768,
                                       max_body_bytes=150_000)
            with transport.Client(config) as first, transport.Client(config) as second:
                bodies = [first.send(core.Request('GET', self.base + '/huge')).body,
                          second.send(core.Request('GET', self.base + '/huge')).body]
                with ThreadPoolExecutor(max_workers=2) as executor:
                    reads = [executor.submit(body.bytes, 150_000) for body in bodies]
                    self.assertEqual([read.result(timeout=8) for read in reads],
                                     [b'a' * 120_000] * 2)
                self.assertEqual(first._reactor._buffer_budget.used, 0)
                self.assertEqual(second._reactor._buffer_budget.used, 0)
                with global_budget._lock:
                    self.assertLessEqual(global_budget.peak, global_budget.maximum)
                    self.assertEqual(global_budget.used, 0)
        finally:
            with global_budget._lock:
                self.assertEqual(global_budget.used, 0)
                global_budget.maximum = original_limit
                global_budget.peak = max(original_peak, global_budget.peak)

    def test_process_budget_released_when_another_client_abandons_body(self):
        global_budget = transport._PROCESS_BUFFER_BUDGET
        with global_budget._lock:
            self.assertEqual(global_budget.used, 0)
            old_limit = global_budget.maximum
            global_budget.maximum = 16 * 1024
        try:
            config = core.ClientConfig(body_buffer_bytes=16384,
                                       max_client_buffer_bytes=16384,
                                       max_body_bytes=150_000)
            with transport.Client(config) as first, transport.Client(config) as second:
                abandoned = first.send(core.Request('GET', self.base + '/huge')).body
                waiting = second.send(core.Request('GET', self.base + '/huge')).body
                abandoned.close()
                self.assertEqual(waiting.bytes(150_000), b'a' * 120_000)
                self.assertEqual(global_budget.used, 0)
        finally:
            with global_budget._lock:
                self.assertEqual(global_budget.used, 0)
                global_budget.maximum = old_limit

    def test_shared_budget_reclaimed_on_unread_body_close(self):
        config = core.ClientConfig(body_buffer_bytes=16384,
                                   max_client_buffer_bytes=16384,
                                   max_body_bytes=150_000)
        with transport.Client(config) as client:
            first = client.send(core.Request('GET', self.base + '/huge'))
            second = client.send(core.Request('GET', self.base + '/huge'))
            first.body.close()  # Releases queued chunks held by an abandoned response.
            self.assertEqual(second.body.bytes(150_000), b'a' * 120_000)
            self.assertEqual(client._reactor._buffer_budget.used, 0)

    def test_response_header_limits_before_body_delivery(self):
        for endpoint, config in (
            ('/large-headers', core.ClientConfig(max_response_header_bytes=4096)),
            ('/many-headers', core.ClientConfig(max_response_header_fields=8)),
        ):
            with self.subTest(endpoint=endpoint), transport.Client(config) as client:
                with self.assertRaises(HttpError) as caught:
                    client.send(core.Request('GET', self.base + endpoint))
                self.assertEqual(caught.exception.kind, ErrorKind.RESOURCE_LIMIT)
                self.assertEqual(caught.exception.stage, 'headers')
        with transport.Client(core.ClientConfig(max_response_header_fields=50)) as client:
            result = client.send(core.Request('GET', self.base + '/many-headers'))
            self.assertEqual(result.body.bytes(128), b'headers')

    def test_restricted_ip_policy_rejects_loopback_after_resolution(self):
        config = core.ClientConfig(deny_non_public_ips=True, max_pending=1,
                                   allowed_origins=(self.base,))
        with transport.Client(config) as client:
            for host in (self.base, 'http://localhost:' + str(self.server.server_port)):
                # The localhost hostname case exercises the libcurl-selected
                # address after DNS resolution rather than string filtering.
                for _ in range(2):
                    pending = client.send_async(core.Request('GET', host + '/'))
                    with self.assertRaises(HttpError) as caught:
                        pending.result(timeout=3)
                    self.assertEqual(caught.exception.stage,
                                     'network-policy' if host == self.base else 'policy')
            self.assertEqual(len(client._reactor._transfers), 0)
        with transport.Client(core.ClientConfig(deny_non_public_ips=True)) as client:
            for target in (self.base, 'http://localhost:' + str(self.server.server_port),
                           'http://[::1]:1/', 'http://2130706433:1/',
                           'http://[::ffff:127.0.0.1]:1/'):

                with self.subTest(target=target), self.assertRaises(HttpError) as caught:
                    client.send(core.Request('GET', target + '/'))
                self.assertEqual(caught.exception.kind, ErrorKind.INVALID_REQUEST)
                self.assertEqual(caught.exception.stage, 'network-policy')

    def test_write_idle_progress_and_producer_pause_contract(self):
        config = core.ClientConfig(write_idle_timeout_ms=150)
        stream = SimpleNamespace(last_network_write=10.0,
                                 upload_progress_started=True,
                                 upload_complete=False, upload_waiting=False,
                                 response_headers_received=False)
        self.assertFalse(transport._write_idle_expired(config, stream, 10.149))
        self.assertTrue(transport._write_idle_expired(config, stream, 10.1501))
        stream.upload_waiting = True
        self.assertFalse(transport._write_idle_expired(config, stream, 20.0))
        stream.upload_waiting = False
        stream.upload_progress_started = False
        self.assertFalse(transport._write_idle_expired(config, stream, 20.0))
        stream.upload_progress_started = True
        stream.upload_complete = True
        self.assertFalse(transport._write_idle_expired(config, stream, 20.0))
        stream.upload_complete = False
        stream.response_headers_received = True
        self.assertFalse(transport._write_idle_expired(config, stream, 20.0))
        with transport.Client(core.ClientConfig(write_idle_timeout_ms=1000)) as client:
            payload = b'bytes' * 20000
            response = client.send(core.Request('POST', self.base + '/sha256',
                                                body=payload))
            self.assertEqual(response.body.bytes(100),
                             sha256(payload).hexdigest().encode('ascii'))

    def test_many_concurrent_downloads_share_one_reactor_and_buffer_budget(self):
        # Each response is larger than the shared queue budget. Completion
        # requires cooperative pause/resume and budget reclamation, without
        # allocating a native host thread for every in-flight transfer.
        config = core.ClientConfig(max_pending=128, max_connections=16,
                                   max_connections_per_origin=8,
                                   body_buffer_bytes=16384,
                                   max_client_buffer_bytes=32768)
        with transport.Client(config) as client:
            def download(_index):
                response = client.send(core.Request('GET', self.base + '/huge'))
                return len(response.body.bytes(130000))

            with ThreadPoolExecutor(max_workers=24) as pool:
                self.assertEqual(list(pool.map(download, range(96))), [120000] * 96)
            budget = client._reactor._buffer_budget
            self.assertLessEqual(budget.peak, config.max_client_buffer_bytes)
            self.assertEqual(budget.used, 0)
            self.assertFalse(client._reactor._transfers)
            self.assertTrue(client._reactor._thread.is_alive())

    def test_optional_connection_acquisition_deadline_and_recovery(self):
        if transport._curl().curl_version_info(0).contents.version_num < 0x080200:
            self.skipTest('connection identity requires libcurl >= 8.2')
        # A slow response monopolizes the only connection. The second transfer
        # is waiting in libcurl's connection pool, not for response headers.
        config = core.ClientConfig(max_connections=1,
                                   max_connections_per_origin=1,
                                   connection_acquire_timeout_ms=120,
                                   total_timeout_ms=2000)
        with transport.Client(config) as client:
            first = client.send(core.Request('GET', self.base + '/slow'))
            pending = client.send_async(core.Request('GET', self.base + '/'))
            with self.assertRaises(HttpError) as caught:
                pending.result(timeout=3)
            self.assertEqual(caught.exception.kind, ErrorKind.TIMEOUT)
            self.assertEqual(caught.exception.stage, 'connection-acquisition')
            self.assertEqual(first.body.bytes(100), b'slow-body')
            recovered = client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(recovered.body.bytes(100), b'hello')
        # A request that has acquired a connection may wait for a slow body
        # without exceeding the connection acquisition deadline.
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/slow'))
            self.assertEqual(response.body.bytes(100), b'slow-body')

    def test_connection_acquisition_configuration_rejects_invalid_values(self):
        for bad in (0, -1, True, 3.25, '200'):
            with self.subTest(value=bad), self.assertRaises(HttpValueError):
                core.ClientConfig(connection_acquire_timeout_ms=bad)
        self.assertIsNone(core.ClientConfig().connection_acquire_timeout_ms)

    def test_thousand_in_flight_requests_keep_host_thread_count_bounded(self):
        # A 1,000-request mostly-queued scenario. Only two TCP connections
        # are allowed and the reactor must not start a host thread per easy.
        from threading import active_count
        config = core.ClientConfig(max_pending=1024, max_connections=2,
                                   max_connections_per_origin=2,
                                   total_timeout_ms=5000)
        with transport.Client(config) as client:
            before = active_count()
            futures = [client.send_async(core.Request('GET', self.base + '/slow'))
                       for _ in range(1000)]
            deadline = monotonic() + 10
            while len(client._reactor._transfers) < 1000 and monotonic() < deadline:
                sleep(.02)
            self.assertEqual(len(client._reactor._transfers), 1000)
            self.assertLessEqual(active_count() - before, 8)
            for future in futures:
                if not future.cancel():
                    # send() may already have delivered final headers. Those
                    # response bodies remain exclusively caller-owned.
                    try:
                        future.result(timeout=1).body.close()
                    except HttpError:
                        pass
            deadline = monotonic() + 10
            while (client._reactor._transfers or
                   client._reactor._admission_slots._value != config.max_pending):
                if monotonic() > deadline:
                    self.fail('cancelled queue did not release all transfer registrations')
                sleep(.02)
            self.assertEqual(client._reactor._buffer_budget.used, 0)

    def test_optional_read_idle_deadline(self):
        config = core.ClientConfig(read_idle_timeout_ms=75,
                                   total_timeout_ms=2000)
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/slow'))
            with self.assertRaises(HttpError) as caught:
                response.body.bytes(1024)
            self.assertEqual(caught.exception.kind, ErrorKind.TIMEOUT)
            self.assertEqual(caught.exception.stage, 'read-idle')

    def test_backpressure_does_not_count_as_network_read_idle(self):
        config = core.ClientConfig(read_idle_timeout_ms=80,
                                   body_buffer_bytes=16384,
                                   max_client_buffer_bytes=16384,
                                   max_body_bytes=150_000,
                                   total_timeout_ms=3000)
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/huge'))
            sleep(0.25)  # More than the read-idle interval while curl is paused.
            self.assertEqual(response.body.bytes(150_000), b'a' * 120_000)

    def test_transfer_decompressed_limit_is_enforced(self):
        config = core.ClientConfig(body_buffer_bytes=1024, max_body_bytes=4096)
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/huge'))
            with self.assertRaises(HttpError) as caught:
                response.body.bytes(10_000)
            self.assertEqual(caught.exception.kind, ErrorKind.RESOURCE_LIMIT)

    def test_cancel_send_or_close_body_cancels_transfer(self):
        with transport.Client() as client:
            request = core.Request('GET', self.base + '/slow')
            future = client.send_async(request)
            self.assertTrue(future.cancel())
            with self.assertRaises(CancelledError):
                future.result(timeout=2)
            response = client.send(request)
            response.body.close()
            with self.assertRaises(HttpError):
                response.body.read()

    def test_rejected_upload_source_does_not_leak_admission_slot(self):
        source = UploadPipe(1024, expected_bytes=3)
        source.write_async(b'abc').result()
        source.finish()
        with transport.Client(core.ClientConfig(max_pending=2)) as client:
            first = client.send_async(core.Request('POST', self.base + '/sha256', body=source))
            with self.assertRaises(HttpError) as caught:
                client.send_async(core.Request('POST', self.base + '/sha256', body=source))
            self.assertEqual(caught.exception.kind, ErrorKind.CLOSED)
            self.assertEqual(first.result(timeout=3).body.bytes(128),
                             sha256(b'abc').hexdigest().encode('ascii'))
            later = client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(later.body.bytes(1024), b'hello')

    def test_total_in_flight_admission_limit_and_slot_recovery(self):
        # The command queue alone is insufficient: the reactor can dequeue
        # thousands of slow transfers while their network work remains active.
        config = core.ClientConfig(max_pending=2)
        with transport.Client(config) as client:
            first = client.send_async(core.Request('GET', self.base + '/slow'))
            second = client.send_async(core.Request('GET', self.base + '/slow'))
            with self.assertRaises(HttpError) as caught:
                client.send_async(core.Request('GET', self.base + '/'))
            self.assertEqual(caught.exception.kind, ErrorKind.RESOURCE_LIMIT)
            self.assertEqual(caught.exception.stage, 'admission')
            self.assertEqual(first.result(timeout=3).body.bytes(1024), b'slow-body')
            self.assertEqual(second.result(timeout=3).body.bytes(1024), b'slow-body')
            again = client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(again.body.bytes(1024), b'hello')

    def test_cancelled_queued_admission_releases_slot(self):
        config = core.ClientConfig(max_pending=1)
        with transport.Client(config) as client:
            reactor_paused = Event()
            resume_reactor = Event()
            admission_finished = Event()

            def pause_reactor():
                reactor_paused.set()
                resume_reactor.wait()

            request = core.Request('GET', self.base + '/')
            # Hold the reactor before submission so cancellation happens in the queue.
            client._reactor._command(('step', 0, pause_reactor))
            try:
                self.assertTrue(reactor_paused.wait(timeout=5), 'reactor did not pause')
                future = client.send_async(request, on_finish=admission_finished.set)
                self.assertTrue(future.cancel())
            finally:
                resume_reactor.set()

            self.assertTrue(admission_finished.wait(timeout=5), 'cancelled admission did not finish')
            replacement = client.send_async(request)
            self.assertEqual(replacement.result(timeout=3).body.bytes(1024), b'hello')

    def test_close_while_admissions_pending_finishes_all_futures(self):
        client = transport.Client(core.ClientConfig(max_connections=1, max_connections_per_origin=1))
        futures = [client.send_async(core.Request('GET', self.base + '/slow')) for _ in range(10)]
        client.close()
        for future in futures:
            try:
                response = future.result(timeout=2)
            except (HttpError, CancelledError):
                continue
            response.body.close()

    def test_unexpected_reactor_failure_completes_queued_requests_and_readers(self):
        # A native reactor exception must not strand tasks, upload leases, or
        # response readers even if a send is still queued for admission.
        config = core.ClientConfig(max_pending=4)
        with transport.Client(config) as client:
            response = client.send(core.Request('GET', self.base + '/slow'))
            reading = response.body.read_async()
            queued = []

            def inject_fault():
                queued.append(client.send_async(core.Request('GET', self.base + '/')))
                raise RuntimeError('injected failure on reactor thread')

            client._reactor._command(('step', 0, inject_fault))
            with self.assertRaises(HttpError) as caught:
                reading.result(timeout=3)
            self.assertEqual(caught.exception.kind, ErrorKind.TRANSFER)
            self.assertEqual(caught.exception.stage, 'reactor')
            self.assertEqual(len(queued), 1)
            with self.assertRaises(HttpError) as caught:
                queued[0].result(timeout=3)
            self.assertEqual(caught.exception.stage, 'reactor')
            client._reactor._thread.join(timeout=3)
            self.assertFalse(client._reactor._thread.is_alive())
            self.assertFalse(client._reactor._transfers)
            self.assertEqual(client._reactor._admission_slots._value, config.max_pending)
            self.assertEqual(client._reactor._buffer_budget.used, 0)
            with self.assertRaises(HttpError) as caught:
                client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(caught.exception.kind, ErrorKind.CLOSED)

    def test_cancel_and_shutdown_race_never_wakes_freed_multi_handle(self):
        # Repeatedly race a foreign task's cancellation against reactor
        # shutdown. The command sender and cleanup must synchronize the
        # native multi-handle lifetime, not just check a closed Boolean.
        for iteration in range(24):
            client = transport.Client()
            future = client.send_async(core.Request('GET', self.base + '/slow'))
            start = Barrier(3)
            errors = []

            def cancel():
                try:
                    start.wait(timeout=2)
                    future.cancel()
                except BaseException as exc:
                    errors.append(exc)

            def shutdown():
                try:
                    start.wait(timeout=2)
                    client.close()
                except BaseException as exc:
                    errors.append(exc)

            workers = (Thread(target=cancel), Thread(target=shutdown))
            for worker in workers:
                worker.start()
            start.wait(timeout=2)
            for worker in workers:
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive(), f'shutdown race {iteration} deadlocked')
            self.assertFalse(errors, f'shutdown race {iteration}: {errors}')
            self.assertFalse(client._reactor._thread.is_alive())
            try:
                response = future.result(timeout=2)
            except (HttpError, CancelledError):
                pass
            else:
                response.body.close()

    def test_redirect_defaults_to_no_follow(self):
        with transport.Client() as client:
            response = client.send(core.Request('GET', self.base + '/redirect-one'))
            self.assertEqual(response.status, 302)
            self.assertEqual(response.metadata.final_url, self.base + '/redirect-one')
            self.assertEqual(response.body.bytes(10), b'')

    def test_redirect_chain_and_limits(self):
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            response = client.send(core.Request('GET', self.base + '/redirect-one'))
            self.assertEqual(response.status, 200)
            self.assertEqual(response.metadata.final_url, self.base + '/')
            self.assertEqual(response.body.bytes(20), b'hello')
        with transport.Client(core.ClientConfig(follow_redirects=True, max_redirects=1)) as client:
            with self.assertRaises(HttpError) as caught:
                client.send(core.Request('GET', self.base + '/redirect-one'))
            self.assertEqual(caught.exception.kind, ErrorKind.PROTOCOL)
            self.assertEqual(caught.exception.stage, 'redirect')

    def test_redirect_loop_and_unsupported_url(self):
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            for path in ('redirect-loop-a', 'redirect-invalid'):
                with self.subTest(path=path), self.assertRaises(HttpError) as caught:
                    client.send(core.Request('GET', self.base + '/' + path))
                self.assertEqual(caught.exception.kind, ErrorKind.PROTOCOL)

    def test_redirect_credential_isolation(self):
        headers = core.Headers((
            ('Authorization', 'Bearer private'), ('Cookie', 'session=private'),
            ('X-Api-Key', 'private-key'), ('X-Note', 'public'),
        ))
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            same = client.send(core.Request('GET', self.base + '/redirect-same', headers))
            self.assertEqual(same.body.bytes(100), b'Bearer private|session=private|private-key|GET')
            cross = client.send(core.Request('GET', self.base + '/redirect-cross', headers))
            self.assertEqual(cross.metadata.final_url, self.other_base + '/inspect')
            self.assertEqual(cross.body.bytes(100), b'|||GET')

    def test_redirect_method_rewriting_and_preservation(self):
        with transport.Client(core.ClientConfig(follow_redirects=True)) as client:
            for path in ('redirect-303', 'redirect-302-post'):
                with self.subTest(path=path):
                    resp = client.send(core.Request('POST', self.base + '/' + path,
                                                    body=b'no-replay-needed'))
                    self.assertEqual(resp.body.bytes(100), b'|||GET')
            for path in ('redirect-307', 'redirect-308'):
                with self.subTest(path=path):
                    resp = client.send(core.Request('POST', self.base + '/' + path,
                                                    body=b'preserved\x00payload'))
                    self.assertEqual(resp.status, 201)
                    self.assertEqual(resp.body.bytes(100), b'preserved\x00payload')

    def test_redirect_policy_validates_downgrade_and_origin(self):
        request = core.Request('GET', 'https://example.com/start',
                               core.Headers((('Authorization', 'secret'),)))
        with self.assertRaises(HttpError) as caught:
            transport._next_redirect(request, 302, 'http://example.com/',
                                     allow_downgrade=False)
        self.assertEqual(caught.exception.stage, 'redirect')
        allowed = transport._next_redirect(request, 302, 'http://example.com/',
                                           allow_downgrade=True)
        self.assertEqual(allowed.headers.values('authorization'), ())
        with self.assertRaises(HttpValueError):
            core.ClientConfig(follow_redirects=True, max_redirects=0)


class HTTPValianceTests(ServerTestCase):
    def test_every_published_http_reference_example_compiles(self):
        from valiance.elements.reference_docs import collect_stdlib_references

        references = [item for item in collect_stdlib_references()
                      if item.qualified_name.startswith('std.http.')]
        self.assertEqual(len(references), 17)
        for reference in references:
            for overload in reference.overload_documentation:
                self.assertTrue(overload.examples, reference.qualified_name)
                for example in overload.examples:
                    with self.subTest(operation=reference.qualified_name,
                                      source=example.source):
                        analyser = Analyser(module_loader=_MODULES)
                        analyser.analyse(parse(example.source))
                        self.assertEqual(analyser.diagnostics, [])

    def test_import_compiles_and_client_is_constructible(self):
        self.assertEqual(len(evaluate('import {std.http}\nhttp.Client')), 1)
        self.assertEqual(len(evaluate('import {std.http}\nhttp.emptyHeaders')), 1)

    def test_get_and_read_all(self):
        source = (f'import {{std.http, std.http.ResponseBody}}\n'
                  f'$r = http.get("{self.base}/") ?\n'
                  '$r.status\n'
                  'http.readAll($r.body, 20)')
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result = evaluate(source, optimized=optimized, serialized=serialized)
                self.assertEqual(result[0], RuntimeNumber(200))
                self.assertEqual(list(result[-1]), list(b'hello'))

    def test_vlnc_send_explicit_client_and_header_values(self):
        source = (f'import {{std.http, std.http.Headers}}\n'
                  '$headers = http.withHeader(http.emptyHeaders, "X-Test", "one") ?\n'
                  '$headers := http.withHeader($headers, "x-test", "two") ?\n'
                  '$values = http.headerValues($headers, "X-TEST")\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("GET", "{self.base}/404", $headers)) ?\n'
                  '$values\n$response.status')
        result = evaluate(source)
        self.assertEqual(result[-2], ['one', 'two'])
        self.assertEqual(result[-1], RuntimeNumber(404))

    def test_valiance_invalid_values_are_errors(self):
        invalid_url = evaluate('import {std.http}\nhttp.get("file:///etc/passwd")')
        self.assertEqual(invalid_url[0].type_name, 'http.Error.InvalidRequest')
        bad_config = evaluate('import {std.http}\n'
                              'http.openClient(http.ClientConfig(maxConnections=1))')
        self.assertEqual(bad_config[0].type_name, 'http.Error.InvalidRequest')

    def test_vm_managed_client_config(self):
        source = ('import {std.http}\n'
                  '$config = http.ClientConfig(maxConnections=2, maxConnectionsPerOrigin=2)\n'
                  '$client = http.openClient($config) ?\n'
                  'http.close($client)')
        self.assertEqual(evaluate(source), [])

    def test_valiance_connection_acquisition_configuration(self):
        if transport._curl().curl_version_info(0).contents.version_num < 0x080200:
            self.skipTest('connection identity requires libcurl >= 8.2')
        source = ('import {std.http}\n'
                  '$config = http.ClientConfig(connectionAcquireTimeoutMs=Some(250))\n'
                  '$client = http.openClient($config) ?\n'
                  'http.close($client)')
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized):
                self.assertEqual(evaluate(source, optimized=optimized,
                                          serialized=serialized), [])

    def test_valiance_restricted_client_refuses_private_socket(self):
        source = ('import {std.http}\n'
                  '$client = http.openClient(http.ClientConfig('
                  'denyNonPublicIps=Some(true))) ?\n'
                  f'$result = http.send($client, http.Request("GET", "{self.base}/"))\n'
                  'http.close($client)\n'
                  '$result')
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result = evaluate(source, optimized=optimized, serialized=serialized)
                self.assertEqual(result[-1].type_name, 'http.Error.InvalidRequest')

    def test_valiance_network_and_write_idle_configuration(self):
        source = ('import {std.http}\n'
                  '$config = http.ClientConfig(denyNonPublicIps=Some(true), '
                  'writeIdleTimeoutMs=Some(500))\n'
                  '$client = http.openClient($config) ?\n'
                  'http.close($client)')
        self.assertEqual(evaluate(source), [])

    def test_valiance_shared_budget_and_idle_configuration(self):
        source = ('import {std.http}\n'
                  '$config = http.ClientConfig(maxClientBufferBytes=32768, '
                  'readIdleTimeoutMs=Some(150), bodyBufferBytes=16384, '
                  'maxResponseHeaderBytes=65536, maxResponseHeaderFields=64)\n'
                  '$client = http.openClient($config) ?\n'
                  'http.close($client)')
        self.assertEqual(evaluate(source), [])

    def test_vlnc_redirect_configuration(self):
        source = (f'import {{std.http}}\n'
                  '$client = http.openClient(http.ClientConfig('
                  'followRedirects=Some(true), maxRedirects=4)) ?\n'
                  f'$response = http.send($client, http.Request("GET", "{self.base}/redirect-one")) ?\n'
                  '$response.status\n$response.finalUrl\n'
                  'http.text($response) ?')
        self.assertEqual(evaluate(source), [RuntimeNumber(200), self.base + '/', 'hello'])

    def test_vm_text_bytes_and_dynamic_json(self):
        text = evaluate(f'import {{std.http}}\nhttp.text(http.get("{self.base}/utf8") ?) ?')
        self.assertEqual(text, ['naïve'])
        raw = evaluate(f'import {{std.http}}\nhttp.bytes(http.get("{self.base}/binary") ?) ?')
        self.assertEqual(list(raw[0]), list(bytes(range(256))))
        json_value = evaluate(f'import {{std.http}}\nhttp.json(http.get("{self.base}/json") ?) ?')
        self.assertEqual(json_value[0].type_name, 'json.Json.Object')
        self.assertEqual(json_value[0].fields['members']['count'].type_name, 'json.Json.Number')
        invalid = evaluate(f'import {{std.http}}\nhttp.json(http.get("{self.base}/badjson") ?)')
        self.assertEqual(invalid[0].type_name, 'http.Error.Decode')

    def test_explicit_text_request_body(self):
        source = (f'import {{std.http, std.http.RequestBody}}\n'
                  '$client = http.Client\n'
                  f'$request = http.Request("POST", "{self.base}/upload", \\None, '
                  'Some(http.RequestBody.Text("payload")))\n'
                  '$response = http.send($client, $request) ?\n'
                  'http.text($response) ?')
        self.assertEqual(evaluate(source), ['payload'])

    def test_typed_json_reuses_checked_decoder(self):
        correct = evaluate(f'import {{std.http}}\nhttp.jsonAs[Int](http.get("{self.base}/number") ?) ?')
        self.assertEqual(correct, [RuntimeNumber(42)])
        wrong_type = evaluate(f'import {{std.http}}\nhttp.jsonAs[String](http.get("{self.base}/number") ?)')
        self.assertEqual(wrong_type[0].type_name, 'http.Error.Decode')
        invalid = evaluate(f'import {{std.http}}\nhttp.jsonAs[Int](http.get("{self.base}/badjson") ?)')
        self.assertEqual(invalid[0].type_name, 'http.Error.Decode')

    def test_vm_http_post(self):
        source = (f'import {{std.http}}\n'
                  f'$response = http.post("{self.base}/", "message") ?\n'
                  '$response.status\n'
                  'http.text($response) ?')
        result = evaluate(source)
        self.assertEqual(result, [RuntimeNumber(201), 'message'])

    def test_valiance_urlencoded_form_and_query_helper(self):
        source = (f'import {{std.http, std.http.RequestBody}}\n'
                  '$url = http.withQuery("' + self.base + '/inspect-upload?existing=1", '
                  '[http.FormField("a", "a b"), http.FormField("a", "+")]) ?\n'
                  '$fields = [http.FormField("search", "x & y"), '
                  'http.FormField("search", "café")]\n'
                  '$client = http.Client\n'
                  '$response = http.send($client, '
                  'http.Request("POST", $url, \\None, '
                  'Some(http.RequestBody.Form($fields)))) ?\n'
                  'http.text($response) ?')
        self.assertEqual(evaluate(source), [
            'application/x-www-form-urlencoded; charset=utf-8\n'
            'search=x+%26+y&search=caf%C3%A9'])

    def test_valiance_multipart_form_and_file(self):
        source = (f'import {{std.http, std.http.RequestBody, '
                  'std.http.MultipartPart, std.file.#byte}\n'
                  '$parts = [http.MultipartPart.Text("note", "hello"), '
                  'http.MultipartPart.File("file", "bin.dat", '
                  '"application/octet-stream", [0 #byte, 255 #byte, 65 #byte])]\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("POST", "{self.base}/inspect-upload", '
                  '\\None, Some(http.RequestBody.Multipart($parts)))) ?\n'
                  'http.bytes($response) ?')
        raw = bytes(int(n) for n in evaluate(source)[0])
        ctype, blob = raw.split(b'\n', 1)
        self.assertTrue(ctype.startswith(b'multipart/form-data; boundary='))
        parsed = BytesParser(policy=policy.default).parsebytes(
            b'MIME-Version: 1.0\r\nContent-Type: ' + ctype + b'\r\n\r\n' + blob)
        parts = parsed.get_payload()
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_payload(decode=True), b'hello')
        self.assertEqual(parts[1].get_payload(decode=True), b'\x00\xffA')

    def test_valiance_file_backed_multipart_upload(self):
        from valiance.std import file as file_backend

        with tempfile.TemporaryDirectory() as directory:
            filename = os.path.join(directory, 'payload.bin')
            payload = b'\x00\xffABC\x00Z'
            with open(filename, 'wb') as stream:
                stream.write(payload)
            source = (
                'import {std.http, std.file, std.path, '
                'std.http.UploadWriter, std.http.RequestBody, '
                'std.http.MultipartPart}\n'
                '$pipe = http.uploadPipe(Some(7), 2) ?\n'
                '$writer = $pipe.writer\n$source = $pipe.source\n'
                '$writer\n'
                '$task = spawn: fn(input: UploadWriter) -> Int =>\n'
                f'  $file = file.ReadFile(path.Path({json.dumps(filename)}))\n'
                '  $out = $input move(value -> value)\n'
                '  $total = 0\n'
                '  while (true) =>\n'
                '    $(file, chunk) = file.readChunk($file, 2)\n'
                '    $chunk match =>\n'
                '      as :None => break\n'
                '      _ =>\n'
                '        $data = $chunk ?!\n'
                '        $(out, count) = http.write($out, $data) ?\n'
                '        $total = $total + $count\n'
                '    end\n'
                '  end\n'
                '  $(lastWriter, finished) = http.finish($out) ?\n'
                '  file.close($file)\n'
                '  $total\n'
                'end\n'
                '$parts = [http.MultipartPart.StreamFile("file", "test.bin", '
                '"application/octet-stream", $source)]\n'
                '$client = http.Client\n'
                f'$response = http.send($client, http.Request("POST", "{self.base}/inspect-upload", '
                '\\None, Some(http.RequestBody.MultipartStream($parts)))) ?\n'
                '$task wait\nhttp.bytes($response) ?'
            )
            for optimized, serialized in ((False, False), (True, True)):
                with self.subTest(optimized=optimized, serialized=serialized):
                    values = evaluate(source, optimized=optimized, serialized=serialized)
                    self.assertEqual(values[0], RuntimeNumber(len(payload)))
                    header, body = bytes(int(n) for n in values[1]).split(b'\n', 1)
                    parsed = BytesParser(policy=policy.default).parsebytes(
                        b'MIME-Version: 1.0\r\nContent-Type: ' + header + b'\r\n\r\n' + body)
                    self.assertEqual(parsed.get_payload()[0].get_payload(decode=True), payload)
                    self.assertFalse(file_backend._handles)

    def test_valiance_streamed_multipart_file_with_spawned_producer(self):
        source = (f'import {{std.http, std.http.UploadWriter, '
                  f'std.http.RequestBody, std.http.MultipartPart, std.file.#byte}}\n'
                  '$pipe = http.uploadPipe(Some(3), 1) ?\n'
                  '$writer = $pipe.writer\n$source = $pipe.source\n'
                  '$writer\n'
                  '$task = spawn: fn (writer: UploadWriter) -> Int =>\n'
                  '  $(w2, n1) = http.write($writer, [0 #byte]) ?\n'
                  '  $(w3, n2) = http.write($w2, [255 #byte]) ?\n'
                  '  $(w4, n3) = http.write($w3, [65 #byte]) ?\n'
                  '  $(w5, finished) = http.finish($w4) ?\n'
                  '  $n1 $n2 + $n3 +\n'
                  'end\n'
                  '$parts = [http.MultipartPart.Text("note", "hello"), '
                  'http.MultipartPart.StreamFile("file", "data.bin", '
                  '"application/octet-stream", $source)]\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("POST", "{self.base}/inspect-upload", '
                  '\\None, Some(http.RequestBody.MultipartStream($parts)))) ?\n'
                  '$task wait\nhttp.bytes($response) ?')
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                values = evaluate(source, optimized=optimized, serialized=serialized)
                self.assertEqual(values[0], RuntimeNumber(3))
                ctype, wire = bytes(int(n) for n in values[1]).split(b'\n', 1)
                parsed = BytesParser(policy=policy.default).parsebytes(
                    b'MIME-Version: 1.0\r\nContent-Type: ' + ctype + b'\r\n\r\n' + wire)
                self.assertEqual([p.get_payload(decode=True) for p in parsed.get_payload()],
                                 [b'hello', b'\x00\xffA'])

    def test_form_content_type_override_and_upload_budget_rejected(self):
        source = (f'import {{std.http, std.http.RequestBody, std.http.Headers}}\n'
                  '$fields = [http.FormField("x", "1")]\n'
                  '$headers = http.withHeader(http.emptyHeaders, "Content-Type", "evil") ?\n'
                  '$client = http.Client\n'
                  f'http.send($client, http.Request("POST", "{self.base}/inspect-upload", '
                  'Some($headers), Some(http.RequestBody.Form($fields))))')
        self.assertEqual(evaluate(source)[0].type_name, 'http.Error.InvalidRequest')
        with transport.Client(core.ClientConfig(max_buffered_upload_bytes=4)) as client:
            with self.assertRaises(HttpError) as failed:
                client.send(core.Request('POST', self.base + '/inspect-upload', body=b'12345'))
            self.assertEqual(failed.exception.kind, ErrorKind.RESOURCE_LIMIT)

    def test_cookie_jar_is_opt_in_per_client_and_expires(self):
        with transport.Client() as client:
            self.assertEqual(client.send(core.Request('GET', self.base + '/cookie-set')).status, 200)
            result = client.send(core.Request('GET', self.base + '/inspect-cookie'))
            self.assertEqual(result.body.bytes(16384), b'')
        config = core.ClientConfig(enable_cookies=True)
        with transport.Client(config) as one, transport.Client(config) as two:
            self.assertEqual(one.send(core.Request('GET', self.base + '/cookie-set')).body.bytes(16384), b'set')
            self.assertEqual(one.send(core.Request('GET', self.base + '/inspect-cookie')).body.bytes(16384), b'session=abc')
            self.assertEqual(two.send(core.Request('GET', self.base + '/inspect-cookie')).body.bytes(16384), b'')
            self.assertEqual(one.send(core.Request('GET', self.base + '/cookie-set-path')).body.bytes(16384), b'set')
            inside = one.send(core.Request('GET', self.base + '/inside/inspect')).body.bytes(16384)
            outside = one.send(core.Request('GET', self.base + '/outside/inspect')).body.bytes(16384)
            self.assertIn(b'scoped=yes', inside)
            self.assertNotIn(b'scoped=yes', outside)
            self.assertEqual(one.send(core.Request('GET', self.base + '/cookie-remove')).body.bytes(16384), b'deleted')
            self.assertEqual(one.send(core.Request('GET', self.base + '/inspect-cookie')).body.bytes(16384), b'')

    def test_cookie_jar_redirect_and_explicit_header_precedence(self):
        config = core.ClientConfig(enable_cookies=True, follow_redirects=True)
        with transport.Client(config) as client:
            result = client.send(core.Request('GET', self.base + '/redirect-with-cookie'))
            self.assertEqual(result.body.bytes(16384), b'|hop=value||GET')
            manual = core.Headers((('Cookie', 'manual=1'),))
            result = client.send(core.Request('GET', self.base + '/inspect', manual))
            self.assertEqual(result.body.bytes(16384), b'|manual=1||GET')

    def test_valiance_client_can_enable_cookie_jar(self):
        source = ('import {std.http}\n'
                  '$client = http.openClient(http.ClientConfig(enableCookies=Some(true))) ?\n'
                  f'$first = http.send($client, http.Request("GET", "{self.base}/cookie-set")) ?\n'
                  'http.text($first) ?\n'
                  f'$second = http.send($client, http.Request("GET", "{self.base}/inspect-cookie")) ?\n'
                  'http.text($second) ?')
        self.assertEqual(evaluate(source), ['set', 'session=abc'])

    def test_origin_allowlist_enforced_before_connect_and_after_redirect(self):
        with transport.Client(core.ClientConfig(allowed_origins=(self.base,))) as client:
            accepted = client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(accepted.body.bytes(1024), b'hello')
            with self.assertRaises(HttpError) as denial:
                client.send(core.Request('GET', self.other_base + '/'))
            self.assertEqual(denial.exception.kind, ErrorKind.INVALID_REQUEST)
        restricted = core.ClientConfig(allowed_origins=(self.base,), follow_redirects=True)
        with transport.Client(restricted) as client:
            with self.assertRaises(HttpError) as denial:
                client.send(core.Request('GET', self.base + '/redirect-cross'))
            self.assertEqual(denial.exception.kind, ErrorKind.INVALID_REQUEST)
        with transport.Client(core.ClientConfig(require_https=True)) as client:
            with self.assertRaises(HttpError) as denial:
                client.send(core.Request('GET', self.base + '/'))
            self.assertEqual(denial.exception.kind, ErrorKind.INVALID_REQUEST)

    def test_valiance_origin_allowlist_configuration(self):
        source = (f'import {{std.http}}\n'
                  '$config = http.ClientConfig(allowedOrigins=Some(["' + self.base + '"]))\n'
                  '$client = http.openClient($config) ?\n'
                  f'$ok = http.send($client, http.Request("GET", "{self.base}/")) ?\n'
                  'http.text($ok) ?\n'
                  f'http.send($client, http.Request("GET", "{self.other_base}/"))')
        result = evaluate(source)
        self.assertEqual(result[0], 'hello')
        self.assertEqual(result[1].type_name, 'http.Error.InvalidRequest')

    def test_gzip_response_decoded_and_bomb_is_bounded(self):
        with transport.Client(core.ClientConfig(max_body_bytes=1024, body_buffer_bytes=1024)) as client:
            result = client.send(core.Request('GET', self.base + '/gzip-small'))
            self.assertEqual(result.body.bytes(1024), b'decoded data')
            huge = client.send(core.Request('GET', self.base + '/gzip-bomb'))
            with self.assertRaises(HttpError) as failure:
                huge.body.bytes(1024)
            self.assertEqual(failure.exception.kind, ErrorKind.RESOURCE_LIMIT)

    def test_valiance_streamed_upload_with_spawned_producer(self):
        source = (f'import {{std.http, std.http.UploadWriter, '
                  f'std.http.RequestBody, std.file.#byte}}\n'
                  '$pipe = http.uploadPipe(Some(3), 1) ?\n'
                  '$writer = $pipe.writer\n$source = $pipe.source\n'
                  '$writer\n'
                  '$task = spawn: fn (writer: UploadWriter) -> Int =>\n'
                  '  $(w2, n1) = http.write($writer, [97 #byte]) ?\n'
                  '  $(w3, n2) = http.write($w2, [98 #byte]) ?\n'
                  '  $(w4, n3) = http.write($w3, [99 #byte]) ?\n'
                  '  $(w5, complete) = http.finish($w4) ?\n'
                  '  $n1 $n2 + $n3 +\n'
                  'end\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("POST", "{self.base}/upload", '
                  '\\None, Some(http.RequestBody.Stream($source)))) ?\n'
                  '$task wait\n$response.status\nhttp.text($response) ?')
        for optimized, serialized in ((False, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                result = evaluate(source, optimized=optimized, serialized=serialized)
                self.assertEqual(result, [RuntimeNumber(3), RuntimeNumber(201), 'abc'])

    def test_valiance_streamed_upload_unbounded_length(self):
        source = (f'import {{std.http, std.http.UploadWriter, '
                  f'std.http.RequestBody, std.file.#byte}}\n'
                  '$pipe = http.uploadPipe(2) ?\n'
                  '$writer = $pipe.writer\n$source = $pipe.source\n'
                  '$(w2, count) = http.write($writer, [0 #byte, 255 #byte]) ?\n'
                  '$(w3, complete) = http.finish($w2) ?\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("POST", "{self.base}/upload", '
                  '\\None, Some(http.RequestBody.Stream($source)))) ?\n'
                  'http.bytes($response) ?')
        result = evaluate(source)
        self.assertEqual(list(result[0]), [0, 255])

    def test_valiance_upload_pipe_validation_and_bounded_writes(self):
        invalid = evaluate('import {std.http}\nhttp.uploadPipe(0)')
        self.assertEqual(invalid[0].type_name, 'http.Error.InvalidRequest')
        source = ('import {std.http, std.http.UploadWriter, std.file.#byte}\n'
                  '$pipe = http.uploadPipe(1) ?\n'
                  '$writer = $pipe.writer\n'
                  '$(next, result) = http.write($writer, [1 #byte, 2 #byte])\n'
                  '$result')
        invalid_chunk = evaluate(source)
        self.assertEqual(invalid_chunk[0].type_name, 'http.Error.Limit')

    def test_http_registries_release_consumed_bodies_and_uploads(self):
        from valiance.std import http as bridge

        source = (f'import {{std.http, std.http.UploadWriter, '
                  f'std.http.RequestBody, std.file.#byte}}\n'
                  '$pipe = http.uploadPipe(expectedBytes=Some(3), capacity=3) ?\n'
                  '$writer = $pipe.writer\n$source = $pipe.source\n'
                  '$(w2, n) = http.write($writer, [97 #byte, 98 #byte, 99 #byte]) ?\n'
                  '$(w3, finished) = http.finish($w2) ?\n'
                  '$client = http.Client\n'
                  f'$response = http.send($client, http.Request("POST", "{self.base}/upload", '
                  '\\None, Some(http.RequestBody.Stream($source)))) ?\n'
                  'http.text($response) ?')
        analyser = Analyser(module_loader=_MODULES)
        typed = analyser.analyse(parse(source))
        self.assertFalse(analyser.diagnostics)
        program = compile_program(typed)
        with VirtualMachine() as vm:
            self.assertEqual(vm.run(program), ['abc'])
            self.assertFalse(getattr(vm, '_http_body_tokens', set()))
            self.assertFalse(getattr(vm, '_http_upload_tokens', set()))
            self.assertFalse(bridge._BODIES)
            self.assertFalse(bridge._UPLOADS)

    def test_share_one_client_between_spawned_requests(self):
        source = (f'import {{std.http}}\n'
                  '$client = http.Client\n'
                  f'$a = fn => http.send($client, http.Request("GET", "{self.base}/slow")) ? end | spawn\n'
                  f'$b = fn => http.send($client, http.Request("GET", "{self.base}/slow")) ? end | spawn\n'
                  '$first = $a wait\n$second = $b wait\n'
                  '$first.status\n$second.status')
        result = evaluate(source)
        self.assertEqual(result[-2:], [RuntimeNumber(200), RuntimeNumber(200)])

    def test_dynamic_json_all_six_roots(self):
        expected = ('Null', 'Boolean', 'Number', 'Text', 'Array', 'Object')
        paths = ('null', 'bool', 'number', 'text', 'array', 'object')
        for name, root in zip(paths, expected, strict=True):
            with self.subTest(name=name):
                result = evaluate(f'import {{std.http}}\n'
                                  f'http.json(http.get("{self.base}/json-{name}") ?) ?')
                self.assertEqual(result[0].type_name, 'json.Json.' + root)

    def test_vm_task_cancelled_while_network_pending(self):
        source = (f'import {{std.http}}\n'
                  f'$task = fn => http.get("{self.base}/slow") ? end | spawn\n'
                  '$task cancel\n'
                  f'$next = http.get("{self.base}/") ?\n'
                  '$next.status')
        self.assertEqual(evaluate(source), [RuntimeNumber(200)])

    def test_cooperative_concurrent_tasks(self):
        self.server.concurrent_barrier = Barrier(2)
        source = (f'import {{std.http}}\n'
                  f'$first = fn => http.get("{self.base}/cooperative-barrier") ? end | spawn\n'
                  f'$second = fn => http.get("{self.base}/cooperative-barrier") ? end | spawn\n'
                  '$a = $first wait\n$b = $second wait\n'
                  '$a.status\n$b.status')
        values = evaluate(source)
        self.assertEqual(values[-2:], [RuntimeNumber(200), RuntimeNumber(200)])
        self.assertEqual(self.server.concurrent_barrier.n_waiting, 0)
        self.assertFalse(self.server.concurrent_barrier.broken)


if __name__ == '__main__':
    unittest.main()
