"""RFC 6455 HTTP/1.1 handshake attack corpus (WS-12 partial acceptance)."""
from __future__ import annotations

import base64
from dataclasses import replace
import unittest

from valiance.std import _web_core as core
from valiance.std._web_ws import WebSocketPolicy, validate_handshake, upgrade_requested

KEY = base64.b64encode(b'0123456789abcdef').decode('ascii')
BASE = (('Host', 'localhost'), ('Upgrade', 'websocket'), ('Connection', 'Upgrade'),
        ('Sec-WebSocket-Key', KEY), ('Sec-WebSocket-Version', '13'))


def candidate(*, omit=(), add=(), change=(), method='GET'):
    headers = tuple((k, v) for k, v in BASE if k.lower() not in omit)
    headers += tuple(add)
    for name, value in change:
        headers = tuple((k, v) for k, v in headers if k.lower() != name.lower())
        headers += ((name, value),)
    # Construct directly so adversarial Host/authority mismatches are tested
    # in handshake validation rather than synthetic request initialization.
    return replace(core.test_request(method, '/ws', authority='localhost', headers=core.Headers(headers)), protocol='HTTP/1.1')


class HandshakeCorpus(unittest.TestCase):
    def test_valid_handshake_accept_hash_and_extension_defaults(self):
        req = candidate(add=(('Origin', 'http://localhost'),))
        accept, proto, deflate = validate_handshake(req, WebSocketPolicy())
        self.assertEqual(accept, 'BACScCJPNqyz+UBoqMH89VmURoA=')
        self.assertIsNone(proto)
        self.assertFalse(deflate)
        self.assertTrue(upgrade_requested(req.headers))
        req = candidate(add=(('Sec-WebSocket-Extensions', 'permessage-deflate'),))
        self.assertFalse(validate_handshake(req, WebSocketPolicy())[2])
        self.assertTrue(validate_handshake(req, WebSocketPolicy(
            extension_policy='PermessageDeflate'))[2])

    def test_origin_port_zero_and_empty_explicit_ports_do_not_alias_default(self):
        # RFC 6454 serialized origins carry an explicit port when present.
        # :0 and a dangling colon must never be treated as an omitted port.
        self.assertEqual(validate_handshake(candidate(add=(('Origin',
            'http://localhost'),)), WebSocketPolicy())[1], None)
        for value in ('http://localhost:0', 'http://localhost:',
                      'https://localhost:0', 'http://localhost:65536'):
            with self.subTest(origin=value), self.assertRaises(core.WebError):
                validate_handshake(candidate(add=(('Origin', value),)),
                                   WebSocketPolicy())
        for value in ('http://localhost:0', 'http://localhost:'):
            with self.subTest(h2_origin=value), self.assertRaises(core.WebError):
                from valiance.std._web_ws import validate_h2_handshake
                request = replace(core.test_request('CONNECT', '/ws',
                    authority='localhost', headers=core.Headers((
                        ('Sec-WebSocket-Version','13'), ('Origin', value)))),
                    protocol='HTTP/2', scheme='http')
                validate_h2_handshake(request, WebSocketPolicy())

    def test_origin_controls_cannot_be_normalized_into_trusted_origins(self):
        """URL parsing must not erase C0 whitespace from a security boundary."""
        for origin in (' http://localhost', 'http://localhost ',
                       '\thttp://localhost', 'http://local\thost',
                       'http://localhost\r', 'http://localhost\n',
                       'http://localhost\x00', 'http://localhost\x7f',
                       'http://localhost/\t', 'http://localhost#\n'):
            with self.subTest(origin=repr(origin)):
                with self.assertRaises(core.WebError):
                    validate_handshake(candidate(add=(('Origin', origin),)),
                                       WebSocketPolicy())
                with self.assertRaises(core.ConfigError):
                    WebSocketPolicy(allowed_origins=(origin,))
                from valiance.std._web_ws import validate_h2_handshake
                with self.assertRaises(core.WebError):
                    # Header construction may reject controls before the H2
                    # WebSocket validator; either layer must reject them.
                    h2_request = replace(core.test_request('CONNECT', '/ws',
                        authority='localhost', headers=core.Headers((
                            ('Sec-WebSocket-Version', '13'), ('Origin', origin)))),
                        protocol='HTTP/2', scheme='http')
                    validate_h2_handshake(h2_request, WebSocketPolicy())
        with self.assertRaises(core.ConfigError):
            WebSocketPolicy(allowed_origins=('*',))
        self.assertEqual(validate_handshake(candidate(add=(('Origin',
            'http://localhost'),)), WebSocketPolicy())[1], None)

    def test_attack_matrix_rejected_before_upgrade(self):
        bad = (
            {'omit': ('upgrade',)},
            {'omit': ('connection',)},
            {'omit': ('sec-websocket-key',)},
            {'omit': ('sec-websocket-version',)},
            {'omit': ('host',)},
            {'change': (('Upgrade', 'something-else'),)},
            {'change': (('Connection', 'keep-alive'),)},
            {'change': (('Sec-WebSocket-Key', 'abc'),)},
            {'change': (('Sec-WebSocket-Key', base64.b64encode(b'too-short').decode()),)},
            {'change': (('Sec-WebSocket-Version', '12'),)},
            {'add': (('Sec-WebSocket-Key', KEY),)},
            {'add': (('Sec-WebSocket-Version', '13'),)},
            {'add': (('Origin', 'http://attacker.invalid'),)},
            {'add': (('Origin', 'null'),)},
            {'add': (('Origin', 'http://localhost/x'),)},
            {'add': (('Origin', 'http://localhost'), ('Origin', 'http://localhost'))},
            {'add': (('Content-Length', '1'),)},
            {'add': (('Transfer-Encoding', 'chunked'),)},
            {'add': (('Sec-WebSocket-Protocol', 'foo,,bar'),)},
            {'add': (('Sec-WebSocket-Protocol', 'foo,foo'),)},
            {'method': 'POST'},
        )
        policy = WebSocketPolicy()
        for vector in bad:
            with self.subTest(vector=vector):
                try:
                    request = candidate(**vector)
                except core.DispatchError:
                    # Some invalid fixtures are rejected even before handshake.
                    continue
                with self.assertRaises(core.WebError):
                    validate_handshake(request, policy)

    def test_extended_h1_header_and_origin_matrix(self):
        """RFC 6455 §§4.2.1/4.2.2, 9.1 and origin rejection branches."""
        bad = (
            ('duplicate-host', {'add': (('hOsT', 'localhost'),)}),
            ('duplicate-subprotocol', {'add': (('Sec-WebSocket-Protocol', 'chat'),
                                            ('Sec-WebSocket-Protocol', 'chat'),)}),
            ('empty-upgrade', {'change': (('Upgrade', ''),)}),
            ('empty-connection', {'change': (('Connection', ''),)}),
            ('invalid-upgrade-token', {'change': (('Upgrade', 'websocket,,h2c'),)}),
            ('invalid-connection-token', {'change': (('Connection', 'keep alive, Upgrade'),)}),
            ('partial-key', {'change': (('Sec-WebSocket-Key', KEY[:-1]),)}),
            ('key-whitespace', {'change': (('Sec-WebSocket-Key', ' '+KEY),)}),
            ('key-not-base64', {'change': (('Sec-WebSocket-Key', '!' * 24),)}),
            ('duplicate-origin', {'add': (('Origin', 'http://localhost'),
                                          ('Origin', 'http://localhost'),)}),
            ('origin-auth', {'add': (('Origin', 'http://user@localhost'),)}),
            ('origin-query', {'add': (('Origin', 'http://localhost?q=1'),)}),
            ('origin-fragment', {'add': (('Origin', 'http://localhost#frag'),)}),
            ('origin-invalid-port', {'add': (('Origin', 'http://localhost:bad'),)}),
            ('origin-wrong-port', {'add': (('Origin', 'http://localhost:81'),)}),
            ('origin-wrong-scheme', {'add': (('Origin', 'https://localhost'),)}),
            ('origin-wrong-host', {'add': (('Origin', 'http://other.invalid'),)}),
            ('body-negative-length', {'add': (('Content-Length', '-1'),)}),
            ('body-positive-length', {'add': (('Content-Length', '2'),)}),
            ('body-with-transfer-coding', {'add': (('Transfer-Encoding', 'chunked'),)}),
            ('protocol-invalid-character', {'add': (('Sec-WebSocket-Protocol', 'bad token'),)}),
            ('protocol-empty-trailing', {'add': (('Sec-WebSocket-Protocol', 'chat,'),)}),
            ('protocol-empty-leading', {'add': (('Sec-WebSocket-Protocol', ',chat'),)}),
            ('http10', {'method': 'GET'}),
        )
        for label, params in bad:
            if label == 'http10':
                request = replace(candidate(), protocol='HTTP/1.0')
            else:
                try:
                    request = candidate(**params)
                except core.DispatchError:
                    continue  # parser rejection is also pre-commit rejection
            with self.subTest(label=label), self.assertRaises(core.WebError):
                validate_handshake(request, WebSocketPolicy())

    def test_h1_valid_case_tokens_subprotocol_and_allowed_origin(self):
        req = candidate(change=(('Upgrade', 'h2c, WebSocket'),
                                ('Connection', 'keep-alive, uPgRaDe')),
                        add=(('Sec-WebSocket-Protocol', 'alpha, chat'),
                             ('Origin', 'https://allowed.example:443')))
        accept, selected, compressed = validate_handshake(req, WebSocketPolicy(
            allowed_origins=('https://allowed.example',),
            subprotocols=('chat', 'alpha'), require_subprotocol=True))
        self.assertEqual(accept, 'BACScCJPNqyz+UBoqMH89VmURoA=')
        self.assertEqual(selected, 'chat')
        self.assertFalse(compressed)
        repeated = candidate(add=(('UPGRADE', 'websocket'), ('CONNECTION', 'Upgrade')))
        self.assertEqual(validate_handshake(repeated, WebSocketPolicy())[0], accept)
        with self.assertRaises(core.WebError):
            validate_handshake(candidate(), WebSocketPolicy(
                require_subprotocol=True, subprotocols=('chat',)))

    def test_malformed_upgrade_never_falls_back_to_plain_get(self):
        for fields in (
            core.Headers((('Sec-WebSocket-Key', KEY),)),
            core.Headers((('Upgrade', 'websocket'),)),
            core.Headers((('Connection', 'Upgrade'),)),
            core.Headers((('Sec-WebSocket-Extensions', 'unknown-extension'),)),
        ):
            with self.subTest(headers=fields):
                self.assertTrue(upgrade_requested(fields))


if __name__ == '__main__': unittest.main()
