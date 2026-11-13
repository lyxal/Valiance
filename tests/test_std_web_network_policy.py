"""ServerConfig host and reverse-proxy trust on H1/H2 before application dispatch.

A direct peer address (never a forwarding header) determines whether a proxy
may contribute client identity and effective origin. Host controls apply to
HTTP requests and WebSocket handshakes before their handlers are invoked.
"""
from __future__ import annotations

import http.client
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import unittest

import h2.config
import h2.connection
import h2.events

from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge
from tests.test_std_web_limits_shutdown import ValianceServerLimitsTests


class NetworkPolicyTests(unittest.TestCase):
    def test_configuration_validation_and_authority_identity(self):
        cases = (
            dict(allowed_hosts=('*',)),
            dict(allowed_hosts=('example.test/path',)),
            dict(allowed_hosts=('example.test:0',)),
            dict(allowed_hosts=('example.test:99999',)),
            dict(allowed_hosts=('x..test',)),
            dict(trusted_proxies=('127.0.0.1',), proxy_mode='RFCForwarded'),
            dict(proxy_mode='XForwarded'),
            dict(proxy_mode='Unrecognized'),
            dict(public_origin='https://example.test/path'),
            dict(public_origin='https://example.test\n'),
        )
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(core.ConfigError):
                core.NetworkPolicy(**fields)
        self.assertEqual(core._authority_parts('[2001:db8::1]:8443'),
                         ('2001:db8::1', 8443))
        self.assertEqual(core._authority_parts('ExAmPlE.Test.'), ('example.test', None))

    def test_default_rejects_spoofing_by_ignoring_forwarding_fields(self):
        policy = core.NetworkPolicy(allowed_hosts=('service.test',))
        forged = core.Headers((('Forwarded', 'for=198.51.100.1;host=service.test;proto=https'),
                               ('X-Forwarded-Host', 'service.test'),
                               ('X-Forwarded-Proto', 'https')))
        with self.assertRaises(core.WebError) as rejected:
            policy.verify(forged, 'untrusted.test', 'http', '192.0.2.7')
        self.assertEqual((rejected.exception.name, rejected.exception.status),
                         ('MisdirectedRequest', 421))
        self.assertEqual(policy.verify(forged, 'service.test', 'http', '192.0.2.7'),
                         ('http', 'service.test', '192.0.2.7'))

    def test_forwarded_cidr_chain_and_conflict(self):
        policy = core.NetworkPolicy(allowed_hosts=('service.test',),
            trusted_proxies=('127.0.0.0/8', '10.0.0.0/8'),
            proxy_mode='RFCForwarded')
        fields = core.Headers((('Forwarded',
            'for=198.51.100.5;proto=https;host=service.test, '
            'for=10.0.0.2;proto=http;host=internal.test'),))
        self.assertEqual(policy.verify(fields, 'backend.test', 'http', '127.0.0.1'),
                         ('https', 'service.test', '198.51.100.5'))
        # The very same forged header from a public source has zero authority.
        with self.assertRaises(core.WebError) as rejected:
            policy.verify(fields, 'backend.test', 'http', '203.0.113.5')
        self.assertEqual(rejected.exception.status, 421)
        malicious = (
            'for=unknown;host=service.test', 'for=198.51.100.5;proto=ftp',
            'for=198.51.100.5;host=service.test;host=evil.test',
            'for="198.51.100.5', 'for=198.51.100.5;host="service.test/path"',
            'for=198.51.100.5;proto=https;host=service.test;foo="x\\y"',
        )
        for entry in malicious:
            with self.subTest(entry=entry), self.assertRaises(core.WebError):
                policy.verify(core.Headers((('Forwarded', entry),)),
                              'backend.test', 'http', '127.0.0.1')
        with self.assertRaises(core.WebError):
            policy.verify(core.Headers((('Forwarded', 'for=198.51.100.5'),
                                         ('X-Forwarded-For', '198.51.100.5'))),
                          'backend.test', 'http', '127.0.0.1')
        with self.assertRaises(core.WebError):
            policy.verify(core.Headers((('Forwarded',
                'for=198.51.100.5;proto=http;host=service.test'),)),
                'backend.test', 'https', '127.0.0.1')

    def test_x_forwarded_hop_validation(self):
        policy = core.NetworkPolicy(allowed_hosts=('service.test',),
            trusted_proxies=('127.0.0.0/8', '10.0.0.0/8'), proxy_mode='XForwarded')
        headers = core.Headers((('X-Forwarded-For', '198.51.100.5, 10.0.0.2'),
                                ('X-Forwarded-Host', 'service.test'),
                                ('X-Forwarded-Proto', 'https')))
        self.assertEqual(policy.verify(headers, 'backend.test', 'http', '127.0.0.1'),
                         ('https', 'service.test', '198.51.100.5'))
        for fields in (
            (('X-Forwarded-Host', 'service.test'),),
            (('X-Forwarded-For', 'unknown'),),
            (('X-Forwarded-For', '192.0.2.1'), ('X-Forwarded-Proto', 'ftp')),
            (('X-Forwarded-For', '192.0.2.1'), ('X-Forwarded-Host', 'bad/path')),
        ):
            with self.subTest(fields=fields), self.assertRaises(core.WebError):
                policy.verify(core.Headers(fields), 'backend.test', 'http', '127.0.0.1')

    def test_live_http1_allowlist_and_websocket_upgrade_isolation(self):
        seen = []
        policy = core.NetworkPolicy(allowed_hosts=('service.test',),
            trusted_proxies=('127.0.0.0/8',), proxy_mode='RFCForwarded')
        app = core.App().route('GET', '/test', lambda r: seen.append(r) or
            f'{r.scheme}|{r.authority}|{r.client_address}')
        app = app.websocket('/ws', lambda ws: seen.append('websocket'))
        server = http1.serve(app, port=0, network_policy=policy)
        self.addCleanup(server.shutdown)

        def request(path, host, extra=None):
            c = http.client.HTTPConnection(*server.bound_address, timeout=3)
            try:
                c.request('GET', path, headers={'Host': host, **(extra or {})})
                response = c.getresponse()
                return response.status, response.read()
            finally:
                c.close()

        self.assertEqual(request('/test', 'bad.test')[0], 421)
        self.assertEqual(len(seen), 0)
        self.assertEqual(request('/test', 'backend.test', {
            'Forwarded': 'for=198.51.100.7;proto=https;host=service.test',
        }), (200, b'https|service.test|198.51.100.7'))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].peer, '127.0.0.1')
        websocket_headers = (b'GET /ws HTTP/1.1\r\nHost: bad.test\r\n'
            b'Upgrade: websocket\r\nConnection: Upgrade\r\n'
            b'Sec-WebSocket-Version: 13\r\n'
            b'Sec-WebSocket-Key: MDEyMzQ1Njc4OWFiY2RlZg==\r\n\r\n')
        with socket.create_connection(server.bound_address, timeout=3) as tcp:
            tcp.settimeout(3)
            tcp.sendall(websocket_headers)
            response = tcp.recv(4096)
        self.assertIn(b'421 ', response)
        self.assertEqual(len(seen), 1)

    def test_explicit_protocol_selection_and_unsupported_h3_fail_closed(self):
        selected, = ValianceServerLimitsTests.execute('''import {std.web}
web.ServerConfig(port = 0, protocols = [web.Protocol.HTTP1])''')
        self.assertEqual(bridge._core_protocols(selected), ('HTTP/1.1',))
        denied, = ValianceServerLimitsTests.execute('''import {std.web}
web.serve(web.App, web.ServerConfig(port = 0,
  protocols = [web.Protocol.HTTP1, web.Protocol.HTTP3]))''')
        self.assertEqual(denied.type_name, 'web.ServerError.Failed')
        self.assertIn('protocol', denied.fields['message'].lower())
        cleartext_h2, = ValianceServerLimitsTests.execute('''import {std.web}
web.serve(web.App, web.ServerConfig(port = 0,
  protocols = [web.Protocol.HTTP2]))''')
        self.assertEqual(cleartext_h2.type_name, 'web.ServerError.Failed')

    def test_redacted_completion_event_distinguishes_peer_and_client(self):
        from dataclasses import replace
        observed = []
        app = core.App().route('GET', '/user/{id}', lambda r: 'ok')
        app = app.with_logger(observed.append)
        request = replace(core.test_request('GET', '/user/secret?token=private'),
                          peer='127.0.0.1', client_address='198.51.100.20')
        self.assertEqual(core.dispatch(app, request).status, 200)
        self.assertEqual(len(observed), 1)
        event = observed[0]
        self.assertEqual((event.peer, event.client_address),
                         ('127.0.0.1', '198.51.100.20'))
        self.assertEqual(event.route_template, '/user/{id}')
        self.assertNotIn('secret', repr(event))
        self.assertNotIn('private', repr(event))

    def test_public_valiance_security_configuration(self):
        source = '''import {std.web}
web.ServerConfig(port = 0, allowedHosts = ["service.test"],
  trustedProxies = ["127.0.0.0/8"],
  proxyMode = web.ProxyMode.RFCForwarded, publicOrigin = "https://service.test")'''
        config, = ValianceServerLimitsTests.execute(source, serialized=True)
        result = bridge._core_network_policy(config)
        self.assertEqual(result.allowed_hosts, ('service.test',))
        self.assertEqual(result.proxy_mode, 'RFCForwarded')
        self.assertEqual(result.trusted_proxies, ('127.0.0.0/8',))
        self.assertEqual(result.public_origin, 'https://service.test')
        default, = ValianceServerLimitsTests.execute(
            'import {std.web}\nweb.ServerConfig(port = 0)')
        self.assertEqual(bridge._core_network_policy(default), core.NetworkPolicy())


    def test_limits_and_deployment_policy_can_be_combined_without_breaking_legacy_config(self):
        source = '''import {std.web}
web.ServerConfig(port = 0,
  limits = web.Limits(maxConnections = 7),
  allowedHosts = ["service.test"], trustedProxies = ["127.0.0.0/8"],
  proxyMode = web.ProxyMode.RFCForwarded, protocols = [web.Protocol.HTTP1])'''
        config, = ValianceServerLimitsTests.execute(source)
        self.assertEqual(bridge._core_limits(config).max_connections, 7)
        self.assertEqual(bridge._core_network_policy(config).allowed_hosts,
                         ('service.test',))
        self.assertEqual(bridge._core_network_policy(config).proxy_mode,
                         'RFCForwarded')
        self.assertEqual(bridge._core_protocols(config), ('HTTP/1.1',))
        tls_source = '''import {std.web}
web.ServerConfig(port = 0, tls = web.TLS(
  certificateFile = "example.pem", privateKeyFile = "example-key.pem"),
  limits = web.Limits(maxConnections = 4),
  allowedHosts = ["service.test"])'''
        tls_config, = ValianceServerLimitsTests.execute(tls_source)
        self.assertEqual(bridge._core_limits(tls_config).max_connections, 4)
        self.assertEqual(bridge._core_tls(tls_config).cert_file, 'example.pem')
        self.assertEqual(bridge._core_network_policy(tls_config).allowed_hosts,
                         ('service.test',))

    def test_compiled_request_client_identity_on_live_listener(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := get("/identity"): fn(req: web.GetRequest) => $req.clientAddress end
web.serve($app, web.ServerConfig(port = 0,
  allowedHosts = ["service.test"], trustedProxies = ["127.0.0.0/8"],
  proxyMode = web.ProxyMode.RFCForwarded)) ?'''
        owner, = ValianceServerLimitsTests.execute(source, serialized=True)
        token = bridge._lease_token(owner)
        try:
            with bridge._SERVER_LEASES_LOCK:
                address = bridge._SERVER_LEASES[token].bound_address
            con = http.client.HTTPConnection(*address, timeout=3)
            try:
                con.request('GET', '/identity', headers={
                    'Host': 'backend.test',
                    'Forwarded': 'for=198.51.100.11;proto=https;host=service.test',
                })
                response = con.getresponse()
                self.assertEqual((response.status, response.read()),
                                 (200, b'198.51.100.11'))
            finally:
                con.close()
        finally:
            bridge._close_server_token(token)


@unittest.skipUnless(shutil.which('openssl'), 'openssl required for H2 TLS fixture')
class NetworkPolicyHTTP2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='valiance-network-h2-')
        cls.cert = os.path.join(cls.tmp.name, 'cert.pem')
        cls.key = os.path.join(cls.tmp.name, 'key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', cls.key, '-out', cls.cert, '-days', '2',
                        '-subj', '/CN=localhost'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_tls_http1_only_alpn_selection(self):
        app = core.App().route('GET', '/', lambda r: 'h1')
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key),
                             protocols=('HTTP/1.1',))
        self.addCleanup(server.shutdown, .5)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(['h2', 'http/1.1'])
        with context.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                                 server_hostname='localhost') as sock:
            sock.settimeout(3)
            self.assertEqual(sock.selected_alpn_protocol(), 'http/1.1')
            sock.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
            self.assertIn(b'HTTP/1.1 200', sock.recv(4096))

    def test_allowed_host_isolation_without_rejecting_entire_h2_connection(self):
        app = core.App().route('GET', '/', lambda r: r.authority)
        policy = core.NetworkPolicy(allowed_hosts=('service.test',))
        server = http1.serve(app, port=0, tls=http1.TLSConfig(self.cert, self.key),
                             network_policy=policy)
        self.addCleanup(server.shutdown, .5)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.set_alpn_protocols(['h2'])
        with context.wrap_socket(socket.create_connection(server.bound_address, timeout=4),
                                 server_hostname='localhost') as sock:
            sock.settimeout(4)
            self.assertEqual(sock.selected_alpn_protocol(), 'h2')
            conn = h2.connection.H2Connection(config=h2.config.H2Configuration(
                client_side=True, header_encoding='utf-8'))
            conn.initiate_connection()
            for sid, authority in ((1, 'untrusted.test'), (3, 'service.test')):
                conn.send_headers(sid, [(':method', 'GET'), (':scheme', 'https'),
                                        (':authority', authority), (':path', '/')], end_stream=True)
            sock.sendall(conn.data_to_send())
            statuses: dict[int, str] = {}
            bodies: dict[int, bytearray] = {1: bytearray(), 3: bytearray()}
            ended: set[int] = set()
            deadline = time.monotonic() + 7
            while ended != {1, 3} and time.monotonic() < deadline:
                for event in conn.receive_data(sock.recv(65536)):
                    if isinstance(event, h2.events.ResponseReceived):
                        statuses[event.stream_id] = dict(event.headers)[':status']
                    elif isinstance(event, h2.events.DataReceived):
                        bodies[event.stream_id].extend(event.data)
                        conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    elif isinstance(event, h2.events.StreamEnded):
                        ended.add(event.stream_id)
                outgoing = conn.data_to_send()
                if outgoing:
                    sock.sendall(outgoing)
            self.assertEqual(ended, {1, 3})
            self.assertEqual(statuses, {1: '421', 3: '200'})
            self.assertEqual(bytes(bodies[3]), b'service.test')


if __name__ == '__main__':
    unittest.main()
