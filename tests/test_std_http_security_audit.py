"""Independent adversarial redirect, credential, DNS/socket and cookie tests.

The peers are local and intentionally adversarial. These tests do not connect to
public hosts and require no access to private DNS or a production proxy.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
import random
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from valiance.std._http_cookies import CookieJar
from valiance.std._http_core import ClientConfig, Headers, Request, validate_url
from valiance.std._http_errors import ErrorKind, HttpError, HttpValueError
from valiance.std._http_transport import Client, _is_public_destination, _next_redirect, _curl_library_name


class _RedirectPeer(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_: object) -> None:
        pass

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def do_PUT(self) -> None:
        self._serve()

    def _serve(self) -> None:
        length = int(self.headers.get('Content-Length', '0'))
        if length > 128 * 1024:
            self.send_error(413)
            return
        body = self.rfile.read(length) if length else b''
        with self.server.lock:
            self.server.requests.append((self.command, self.path, dict(self.headers.items()), body))
        if self.path.startswith('/redirect/'):
            status = int(self.path.rsplit('/', 1)[-1])
            target = self.server.target + '/inspect'
            self.send_response(status)
            self.send_header('Location', target)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        payload = json.dumps({
            'method': self.command, 'body': body.decode('latin-1'),
            'headers': {key.lower(): value for key, value in self.headers.items()},
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def peers():
    servers = []
    threads = []
    try:
        for _ in range(2):
            server = ThreadingHTTPServer(('127.0.0.1', 0), _RedirectPeer)
            server.daemon_threads = True
            server.lock = threading.Lock()
            server.requests = []
            servers.append(server)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
            thread.start()
            threads.append(thread)
        origins = [f'http://127.0.0.1:{server.server_port}' for server in servers]
        servers[0].target = origins[1]
        servers[1].target = origins[0]
        yield (*origins, *servers)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


class RedirectSecurityAudit(unittest.TestCase):
    def test_cross_origin_redirect_strips_all_caller_supplied_headers(self):
        # Custom secret headers are as credential-bearing as Authorization;
        # a name-based denylist is insufficient here.
        headers = Headers((
            ('Authorization', 'Bearer private'), ('X-Session-Token', 'private'),
            ('X-Signed-User', 'alice-secret'), ('Cookie', 'session=secret'),
            ('X-Note', 'non-secret'), ('Accept', 'application/json'),
        ))
        with peers() as (left, right, first, second):
            with Client(ClientConfig(follow_redirects=True)) as client:
                response = client.send(Request('GET', left + '/redirect/302', headers))
                payload = json.loads(response.body.bytes(8192))
                self.assertEqual(response.metadata.final_url, right + '/inspect')
                for name, _ in headers.pairs:
                    self.assertNotEqual(payload['headers'].get(name.lower()), headers.values(name)[0])
                # The same-origin redirect retains the caller's headers.
                first.target = left
                response = client.send(Request('GET', left + '/redirect/302', headers))
                same = json.loads(response.body.bytes(8192))
                for name, value in headers.pairs:
                    self.assertEqual(same['headers'].get(name.lower()), value)
            self.assertEqual(len(second.requests), 1)

    def test_cross_origin_307_308_301_302_never_replay_body(self):
        with peers() as (left, right, first, second):
            with Client(ClientConfig(follow_redirects=True)) as client:
                for code, method in ((307, 'POST'), (308, 'PUT'), (301, 'PUT'), (302, 'PUT')):
                    with self.subTest(status=code):
                        before = len(second.requests)
                        with self.assertRaises(HttpError) as raised:
                            client.send(Request(method, left + f'/redirect/{code}',
                                                body=b'secret-body'))
                        self.assertEqual(raised.exception.kind, ErrorKind.PROTOCOL)
                        self.assertEqual(raised.exception.stage, 'redirect')
                        self.assertEqual(len(second.requests), before)
                # 303 and the conventional 302 POST rewrite are safe: the
                # redirected request has neither body nor credentials.
                for code in (303, 302):
                    response = client.send(Request('POST', left + f'/redirect/{code}',
                                                   body=b'secret-body'))
                    payload = json.loads(response.body.bytes(8192))
                    self.assertEqual(payload['method'], 'GET')
                    self.assertEqual(payload['body'], '')

    def test_same_origin_307_preserves_method_and_body(self):
        with peers() as (left, right, first, second):
            first.target = left
            with Client(ClientConfig(follow_redirects=True)) as client:
                response = client.send(Request('PUT', left + '/redirect/307',
                                               Headers((('X-Auth-Token', 'origin-only'),)),
                                               b'\x00payload'))
                payload = json.loads(response.body.bytes(8192))
                self.assertEqual(payload['method'], 'PUT')
                self.assertEqual(payload['body'], '\x00payload')
                self.assertEqual(payload['headers']['x-auth-token'], 'origin-only')
            self.assertEqual(second.requests, [])

    def test_redirect_policy_checks_each_new_origin_before_network(self):
        with peers() as (left, right, first, second):
            with Client(ClientConfig(follow_redirects=True, allowed_origins=(left,))) as client:
                with self.assertRaises(HttpError) as raised:
                    client.send(Request('GET', left + '/redirect/302'))
                self.assertEqual(raised.exception.stage, 'policy')
                self.assertEqual(second.requests, [])

    def test_ip_filter_rejects_loopback_even_with_proxy_environment(self):
        with peers() as (left, right, first, second):
            proxy_env = {'HTTP_PROXY': right, 'HTTPS_PROXY': right,
                         'ALL_PROXY': right, 'http_proxy': right,
                         'https_proxy': right, 'all_proxy': right,
                         'NO_PROXY': '', 'no_proxy': ''}
            with patch.dict(os.environ, proxy_env):
                with Client(ClientConfig(deny_non_public_ips=True)) as client:
                    with self.assertRaises(HttpError) as raised:
                        client.send(Request('GET', left + '/inspect'))
                    self.assertEqual(raised.exception.stage, 'network-policy')
                    self.assertEqual(raised.exception.kind, ErrorKind.INVALID_REQUEST)
            self.assertEqual(first.requests, [])
            self.assertEqual(second.requests, [])

    def test_disallowed_ipv6_transition_and_mapped_private_addresses(self):
        unsafe = ('127.0.0.1', '10.10.2.3', '169.254.0.1', '192.168.1.5',
                  '::1', '::ffff:127.0.0.1', '2002:c0a8:101::1',
                  '64:ff9b::7f00:1', '2001::c0a8:0101', 'fe80::1')
        for address in unsafe:
            with self.subTest(address=address):
                self.assertFalse(_is_public_destination(ipaddress.ip_address(address)))
        for address in ('8.8.8.8', '1.1.1.1', '2606:4700:4700::1111'):
            self.assertTrue(_is_public_destination(ipaddress.ip_address(address)))

    def test_redirect_target_mutation_corpus_never_accepts_unsafe_schemes(self):
        request = Request('GET', 'https://example.com/safe')
        locations = ('file:///etc/passwd', 'javascript:alert(1)',
                     'http://user:password@example.com',
                     'https://user@example.com', 'https://example.com\\@evil.test',
                     '//evil.test/\r\nInjected', '\nhttps://evil.test',
                     'https://example.com/\tpath', 'http://[broken/', 'ftp://example.com/file')
        for location in locations:
            with self.subTest(location=location):
                with self.assertRaises(HttpError):
                    _next_redirect(request, 302, location, allow_downgrade=False)


class NativeLibrarySelectionAudit(unittest.TestCase):
    def test_override_requires_an_existing_absolute_file(self):
        with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': 'libcurl.so.4'}):
            with self.assertRaises(HttpError) as raised:
                _curl_library_name()
            self.assertEqual(raised.exception.stage, 'startup')
        with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': '/does/not/exist/curl.so'}):
            with self.assertRaises(HttpError):
                _curl_library_name()

    def test_explicit_packaged_absolute_library_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / 'curl.so'
            lib.write_bytes(b'test sentinel, not a loadable library')
            with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': str(lib)}):
                self.assertEqual(_curl_library_name(), str(lib))


class CookieMutationAudit(unittest.TestCase):
    def test_malformed_cookie_mutation_never_injects_transport_headers(self):
        randomizer = random.Random(0xC00C1E)
        jar = CookieJar()
        punctuation = 'abcXYZ019;=,\\\" \r\n\x00\x7f\t'
        for iteration in range(500):
            mutation = ''.join(randomizer.choice(punctuation)
                               for _ in range(randomizer.randrange(1, 180)))
            jar.receive('https://sub.example.com/path/leaf', f'cookie{iteration}={mutation}; Path=/')
            result = jar.outbound('https://sub.example.com/path/leaf') or ''
            self.assertLessEqual(len(result), 8192)
            self.assertNotIn('\r', result)
            self.assertNotIn('\n', result)
            self.assertNotIn('\x00', result)
            self.assertLessEqual(len(jar), 256)

    def test_cookie_does_not_escape_domain_path_or_https_boundary(self):
        jar = CookieJar()
        self.assertTrue(jar.receive('https://sub.example.com/private/login',
                                    'token=secret; Path=/private; Secure'))
        self.assertFalse(jar.receive('https://sub.example.com/',
                                     'supercookie=secret; Domain=com; Path=/'))
        self.assertIsNone(jar.outbound('http://sub.example.com/private'))
        self.assertIsNone(jar.outbound('https://sub.example.com/public'))
        self.assertIsNone(jar.outbound('https://different.example.com/private'))
        self.assertEqual(jar.outbound('https://sub.example.com/private'), 'token=secret')


if __name__ == '__main__':
    unittest.main()
