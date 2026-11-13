"""Completion-gate HTTP/2 compressed field/HPACK decoder resource limits.

These are real TLS/ALPN tests plus a frame-scanner test. Wire assertions
intentionally avoid relying solely on the hyper-h2 client for invalid frames.
"""
from __future__ import annotations

import socket
import ssl
import time
import unittest

import h2.errors
import h2.events
import h2.settings
import hpack
from hyperframe.frame import ContinuationFrame, GoAwayFrame, HeadersFrame, Frame

from valiance.std import _web_core as core, _web_http1 as http1, web as bridge
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std._web_http2 import (_CompressedHeaderBudget,
                                       _CompressedHeaderBudgetExceeded)
import tests.test_std_web_v22_http2 as helper


class HTTP2CompressedFieldBudgetTests(unittest.TestCase):
    setUpClass = classmethod(helper.HTTP2TLSTests.setUpClass.__func__)
    tearDownClass = classmethod(helper.HTTP2TLSTests.tearDownClass.__func__)
    client = helper.HTTP2TLSTests.client
    start = helper.HTTP2TLSTests.start
    exchange = helper.HTTP2TLSTests.exchange
    headers = staticmethod(helper.HTTP2TLSTests.headers)

    def _frames_until_goaway(self, sock, timeout=3):
        from hyperframe.frame import Frame
        data = bytearray()
        result = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(.05, deadline - time.monotonic()))
            try:
                packet = sock.recv(65536)
            except socket.timeout:
                break
            if not packet:
                break
            data.extend(packet)
            while len(data) >= 9:
                frame, size = Frame.parse_frame_header(memoryview(data[:9]))
                if len(data) < 9 + size:
                    break
                frame.parse_body(memoryview(data[9:9 + size]))
                del data[:9 + size]
                result.append(frame)
                if isinstance(frame, GoAwayFrame):
                    return result
        return result

    def test_scanner_counts_only_hpack_octets_ignoring_padding_and_priority(self):
        checker = _CompressedHeaderBudget(5)
        preface = b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'
        header = HeadersFrame(1, data=b'xyz')
        header.flags.add('PADDED')
        header.flags.add('PRIORITY')
        header.flags.add('END_HEADERS')
        header.pad_length = 30
        header.depends_on = 0
        raw = preface + header.serialize()
        for index in range(0, len(raw), 2):
            checker.feed(raw[index:index + 2])
        self.assertFalse(checker.block_open)
        self.assertEqual(checker.block_bytes, 0)

    def test_scanner_counts_across_continuations_and_recv_boundaries(self):
        checker = _CompressedHeaderBudget(7)
        first = HeadersFrame(1, data=b'1234')
        second = ContinuationFrame(1, data=b'5678')
        second.flags.add('END_HEADERS')
        wire = b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n' + first.serialize() + second.serialize()
        with self.assertRaises(_CompressedHeaderBudgetExceeded):
            for b in wire:
                checker.feed(bytes([b]))

    def test_compressed_field_block_over_quota_goaway_not_hpack_decoder(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app, http1.Limits(max_compressed_header_bytes=150))
        sock, _ = self.client(server)
        fields = [(b':method', b'GET'), (b':scheme', b'https'),
                  (b':authority', b'localhost'), (b':path', b'/ok'),
                  (b'x-uncompressible', b'0123456789abcdef' * 25)]
        compressed = hpack.Encoder().encode(fields, huffman=False)
        self.assertGreater(len(compressed), 150)
        first = HeadersFrame(1, data=compressed[:95])
        first.flags.add('END_STREAM')
        second = ContinuationFrame(1, data=compressed[95:])
        second.flags.add('END_HEADERS')
        # Fragment across TCP writes, including the frame header itself.
        wire = first.serialize() + second.serialize()
        sock.sendall(wire[:4])
        sock.sendall(wire[4:100])
        sock.sendall(wire[100:])
        frames = self._frames_until_goaway(sock)
        self.assertTrue(any(isinstance(f, GoAwayFrame) and
                            f.error_code == h2.errors.ErrorCodes.ENHANCE_YOUR_CALM
                            for f in frames), [(type(f).__name__, getattr(f, 'error_code', None))
                                               for f in frames])

    def test_valid_compressed_headers_continue_to_dispatch(self):
        app = core.App().route('GET', '/ok', lambda _: 'alive')
        server = self.start(app, http1.Limits(max_compressed_header_bytes=150))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ok'), end_stream=True)
        sock.sendall(conn.data_to_send())
        self.assertEqual(self.exchange(sock, conn, (1,))[0][1], b'alive')

    def test_advertised_dynamic_table_limit_and_decoder_enforcement(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app, http1.Limits(max_hpack_dynamic_table_bytes=1024))
        sock, conn = self.client(server)
        sock.settimeout(3)
        events = conn.receive_data(sock.recv(65536))
        settings = [e for e in events if isinstance(e, h2.events.RemoteSettingsChanged)]
        self.assertTrue(settings)
        change = settings[-1].changed_settings[h2.settings.SettingCodes.HEADER_TABLE_SIZE]
        self.assertEqual(change.new_value, 1024)
        # Decoder's own early cap must reject a larger HPACK table update.
        encoder = hpack.Encoder()
        encoder.header_table_size = 2048
        fields = [(b':method', b'GET'), (b':scheme', b'https'),
                  (b':authority', b'localhost'), (b':path', b'/ok')]
        payload = encoder.encode(fields)
        frame = HeadersFrame(1, data=payload)
        frame.flags.add('END_HEADERS')
        frame.flags.add('END_STREAM')
        sock.sendall(conn.data_to_send() + frame.serialize())
        frames = self._frames_until_goaway(sock)
        self.assertTrue(any(isinstance(f, GoAwayFrame) and
                            f.error_code == h2.errors.ErrorCodes.COMPRESSION_ERROR
                            for f in frames), [(type(f).__name__, getattr(f, 'error_code', None))
                                               for f in frames])

    def test_valiance_config_bridge_carries_all_three_quotas(self):
        source = """import {std.web}
web.ServerConfig(limits = web.Limits(
  maxDecodedHeaderBytes = 8192,
  maxCompressedHeaderBytes = 4096,
  maxHpackDynamicTableBytes = 1024))"""
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual([str(e) for e in analyser.diagnostics], [])
        bytecode = compile_program(program, optimize=True)
        for code in (bytecode, loads(dumps(bytecode))):
            config, = run(code)
            limits = bridge._core_limits(config)
            self.assertEqual((limits.max_decoded_header_bytes,
                              limits.max_compressed_header_bytes,
                              limits.max_hpack_dynamic_table_bytes),
                             (8192, 4096, 1024))

    def test_unfinished_continuation_times_out_with_goaway(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app, http1.Limits(header_timeout_seconds=.18))
        sock, _ = self.client(server)
        partial = HeadersFrame(1, data=b'\x82')
        partial.flags.add('END_STREAM')
        sock.sendall(partial.serialize())
        frames = self._frames_until_goaway(sock, timeout=2)
        self.assertTrue(any(isinstance(f, GoAwayFrame) and
                            f.error_code == h2.errors.ErrorCodes.ENHANCE_YOUR_CALM
                            for f in frames))

    def test_fragmented_headers_finish_inside_header_deadline(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app, http1.Limits(header_timeout_seconds=.6))
        sock, conn = self.client(server)
        conn.send_headers(1, self.headers('/ok'), end_stream=True)
        wire = conn.data_to_send()
        first, size = Frame.parse_frame_header(memoryview(wire[:9]))
        first.parse_body(memoryview(wire[9:9 + size]))
        self.assertIsInstance(first, HeadersFrame)
        split = max(1, len(first.data) // 2)
        half = HeadersFrame(1, data=first.data[:split])
        half.flags.add('END_STREAM')
        remainder = ContinuationFrame(1, data=first.data[split:])
        remainder.flags.add('END_HEADERS')
        sock.sendall(half.serialize())
        time.sleep(.07)
        sock.sendall(remainder.serialize())
        self.assertEqual(self.exchange(sock, conn, (1,))[0][1], b'ok')

    def test_missing_client_preface_times_out(self):
        app = core.App().route('GET', '/ok', lambda _: 'ok')
        server = self.start(app, http1.Limits(header_timeout_seconds=.15))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_alpn_protocols(['h2'])
        sock = ctx.wrap_socket(socket.create_connection(server.bound_address, timeout=3),
                               server_hostname='localhost')
        self.addCleanup(sock.close)
        # A TLS/ALPN peer that never emits the 24-byte h2 preface must not
        # be allowed to hold a connection slot indefinitely.
        frames = self._frames_until_goaway(sock, timeout=2)
        self.assertTrue(any(isinstance(f, GoAwayFrame) and
                            f.error_code == h2.errors.ErrorCodes.ENHANCE_YOUR_CALM
                            for f in frames))

    def test_invalid_new_public_quota_values_rejected(self):
        for field in ('max_decoded_header_bytes', 'max_compressed_header_bytes',
                      'max_hpack_dynamic_table_bytes'):
            with self.subTest(field=field), self.assertRaises(core.ConfigError):
                http1.Limits(**{field: 0})


if __name__ == '__main__':
    unittest.main()
