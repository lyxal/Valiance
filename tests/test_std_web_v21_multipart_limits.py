"""v21: public HTTP/1.1 multipart quota configuration through web.Limits."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import http.client
import os
import tempfile
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, octets: bytes, split: int = 17):
        self.octets = octets
        self.split = split
        self.closed = 0

    def read(self, count):
        if not self.octets:
            return None
        cut = min(count, self.split)
        part, self.octets = self.octets[:cut], self.octets[cut:]
        return part

    def close(self):
        self.closed += 1


def field(name: str, data: bytes) -> bytes:
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + data


def file(name: str, filename: str, data: bytes) -> bytes:
    return (f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + data


def form(*parts: bytes) -> bytes:
    return b''.join(b'--XYZ\r\n' + part + b'\r\n' for part in parts) + b'--XYZ--\r\n'


MIME = core.Headers((('Content-Type', 'multipart/form-data; boundary=XYZ'),))


def request(data: bytes, policy: core.MultipartLimits, *, streaming: bool = False):
    if streaming:
        src = Source(data, 3)
        return replace(core.test_request_stream('POST', '/upload', src, headers=MIME),
                       multipart_limits=policy), src
    return replace(core.test_request('POST', '/upload', headers=MIME, body=data),
                   multipart_limits=policy), None


class MultipartLimitsCoreTests(unittest.TestCase):
    def test_normative_defaults_are_finite_and_frozen(self):
        limits = http1.Limits()
        policy = limits.multipart_policy
        self.assertEqual((limits.max_form_fields, limits.max_multipart_parts,
                          limits.max_upload_file_bytes, limits.max_multipart_field_bytes,
                          limits.max_multipart_name_bytes, limits.max_multipart_filename_bytes),
                         (1000, 128, 256 * 1024 * 1024, 1024 * 1024, 1024, 4096))
        self.assertEqual((policy.max_files, policy.max_header_bytes), (100, 16384))
        with self.assertRaises(FrozenInstanceError):
            policy.max_parts = 10000
        self.assertNotEqual(policy, replace(policy, max_parts=1))

    def test_invalid_public_quotas_fail_configuration(self):
        for name in ('max_form_fields', 'max_multipart_parts', 'max_upload_file_bytes',
                     'max_multipart_field_bytes', 'max_multipart_name_bytes',
                     'max_multipart_filename_bytes'):
            for value in (-1, 0, True, 1024 ** 3 + 1):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(core.ConfigError):
                        http1.Limits(**{name: value})
        with self.assertRaises(core.ConfigError):
            core.MultipartLimits(max_file_bytes=0)

    def test_buffered_limits_cover_parts_fields_files_names_and_octets(self):
        cases = (
            (form(field('a', b'x'), field('b', b'y')), dict(max_parts=1)),
            (form(field('a', b'x'), field('b', b'y')), dict(max_fields=1)),
            (form(field('a', b'xy')), dict(max_field_bytes=1)),
            (form(file('a', 'f', b'abc')), dict(max_file_bytes=2)),
            (form(field('long', b'v')), dict(max_name_bytes=3)),
            (form(file('a', 'long.txt', b'a')), dict(max_filename_bytes=4)),
            (form(file('a', 'f', b'1'), file('b', 'g', b'2')), dict(max_files=1)),
        )
        for payload, options in cases:
            with self.subTest(options=options):
                req, _ = request(payload, core.MultipartLimits(**options))
                with self.assertRaises(core.WebError) as err:
                    core.multipart(req)
                self.assertEqual(err.exception.name, 'BodyTooLarge')
        req, _ = request(form(field('ok', b'first'), field('ok', b'second')),
                         core.MultipartLimits(max_fields=2, max_parts=2))
        self.assertEqual(core.multipart(req).fields.pairs, (('ok', 'first'), ('ok', 'second')))

    def test_default_name_and_filename_boundary_report_413(self):
        for streaming in (False, True):
            for kind, payload in (
                ('name', form(field('n' * 1025, b'v'))),
                ('filename', form(file('x', 'f' * 4097, b'v'))),
            ):
                with self.subTest(streaming=streaming, kind=kind):
                    req, src = request(payload, core.MultipartLimits(), streaming=streaming)
                    with self.assertRaises(core.WebError) as error:
                        if streaming:
                            stream = core.multipart_stream(req)
                            stream.next_part()
                        else:
                            core.multipart(req)
                    self.assertEqual(error.exception.name, 'BodyTooLarge')
                    if src is not None:
                        self.assertEqual(src.closed, 1)

    def test_stream_limits_cover_parts_fields_file_and_names_with_cleanup(self):
        cases = (
            (form(field('x', b'a'), field('y', b'b')), dict(max_parts=1)),
            (form(field('x', b'a'), field('y', b'b')), dict(max_fields=1)),
            (form(field('x', b'abc')), dict(max_field_bytes=2)),
            (form(file('a', 'b', b'abc')), dict(max_file_bytes=2)),
            (form(field('long', b'a')), dict(max_name_bytes=3)),
            (form(file('x', 'long', b'a')), dict(max_filename_bytes=3)),
            (form(file('x', 'a', b'a'), file('x', 'b', b'b')), dict(max_files=1)),
        )
        for payload, options in cases:
            with self.subTest(options=options):
                req, src = request(payload, core.MultipartLimits(**options), streaming=True)
                reader = core.multipart_stream(req)
                with self.assertRaises(core.WebError) as err:
                    while (part := reader.next_part()) is not None:
                        if part.is_file:
                            reader.discard_part()
                self.assertEqual(err.exception.name, 'BodyTooLarge')
                self.assertTrue(reader.closed)
                self.assertEqual(src.closed, 1)

    def test_multibyte_name_limits_measure_utf8_octets(self):
        for streaming in (False, True):
            req, src = request(form(field('é', b'x')),
                               core.MultipartLimits(max_name_bytes=1), streaming=streaming)
            with self.subTest(streaming=streaming):
                with self.assertRaises(core.WebError) as err:
                    if streaming:
                        core.multipart_stream(req).next_part()
                    else:
                        core.multipart(req)
                self.assertEqual(err.exception.name, 'BodyTooLarge')
                if src is not None:
                    self.assertEqual(src.closed, 1)

    def test_overrides_cannot_expand_snapshot_and_exclusive_save_cleans_up(self):
        policy = core.MultipartLimits(max_file_bytes=3)
        req, src = request(form(file('x', 'unsafe', b'abcdefgh')),
                           policy, streaming=True)
        stream = core.multipart_stream(req, max_file_bytes=10000)
        stream.next_part()
        with tempfile.TemporaryDirectory() as directory:
            name = os.path.join(directory, 'exclusive.bin')
            with self.assertRaises(core.WebError) as error:
                stream.save_part(name)
            self.assertEqual(error.exception.name, 'BodyTooLarge')
            self.assertFalse(os.path.exists(name))
        self.assertEqual(src.closed, 1)
        req, _ = request(form(field('x', b'ab')), core.MultipartLimits(max_field_bytes=1))
        with self.assertRaises(core.WebError) as error:
            core.multipart(req, max_field_bytes=1000)
        self.assertEqual(error.exception.name, 'BodyTooLarge')


class MultipartLimitsHTTPTests(unittest.TestCase):
    def server(self, app, limits):
        srv = http1.serve(app, port=0, limits=limits)
        self.addCleanup(srv.shutdown, .5)
        return srv

    def test_real_http_buffered_and_stream_413_and_keepalive(self):
        def buffered(req):
            return str(len(core.multipart(req).fields.pairs))
        def streaming(req):
            reader = core.multipart_stream(req)
            seen = 0
            while (part := reader.next_part()) is not None:
                if part.is_file:
                    reader.discard_part()
                seen += 1
            return str(seen)
        app = core.App().route('POST', '/buffered', buffered)
        app = app.route('POST', '/upload', streaming, body_mode='Stream')
        limits = http1.Limits(max_form_fields=1, max_multipart_parts=1,
                              max_multipart_name_bytes=3, max_upload_file_bytes=3)
        server = self.server(app, limits)
        for path in ('/buffered', '/upload'):
            with self.subTest(path=path):
                c = http.client.HTTPConnection(*server.bound_address, timeout=4)
                try:
                    for payload, expected in (
                        (form(field('a', b'ok')), 200),
                        (form(field('a', b'ok'), field('b', b'no')), 413),
                        (form(field('long', b'x')), 413),
                        (form(file('a', 'f', b'abcd')), 413),
                    ):
                        c.request('POST', path, payload,
                                  headers={'Content-Type': 'multipart/form-data; boundary=XYZ'})
                        r = c.getresponse()
                        self.assertEqual(r.status, expected)
                        r.read()
                finally:
                    c.close()

    def test_chunked_stream_file_quota_is_not_content_length_only(self):
        def handler(req):
            reader = core.multipart_stream(req)
            reader.next_part()
            reader.discard_part()
            return 'ok'
        app = core.App().route('POST', '/upload', handler, body_mode='Stream')
        app = app.route('GET', '/ping', lambda r: 'pong')
        server = self.server(app, http1.Limits(max_upload_file_bytes=2))
        c = http.client.HTTPConnection(*server.bound_address, timeout=4)
        self.addCleanup(c.close)
        payload = form(file('x', 'a', b'0123456'))
        c.request('POST', '/upload', body=(payload[i:i + 3] for i in range(0, len(payload), 3)),
                  encode_chunked=True,
                  headers={'Content-Type': 'multipart/form-data; boundary=XYZ',
                           'Transfer-Encoding': 'chunked'})
        r = c.getresponse()
        self.assertEqual(r.status, 413)
        connection_policy = r.getheader('Connection')
        r.read()
        # If the framed request was fully consumed, keepalive must remain safe.
        if connection_policy != 'close':
            c.request('GET', '/ping')
            response = c.getresponse()
            self.assertEqual((response.status, response.read()), (200, b'pong'))


class MultipartLimitsValianceTests(unittest.TestCase):
    def test_public_limits_pass_through_native_and_serialized_vm_to_live_http(self):
        source = '''import {std.web, std.web.App}
$app = App
$app := post("/upload", bodyMode = web.BodyMode.Stream): fn(req: web.PostRequest) -> String | web.Error =>
  $stream = web.multipartStream($req) ?
  $part1 = web.multipartNext($stream) ?
  $part2 = web.multipartNext($stream) ?
  "ok"
end
$app := post("/buffered"): fn(req: web.PostRequest) -> String | web.Error =>
  $parsed = web.multipart($req) ?
  "ok"
end
$app := post("/file", bodyMode = web.BodyMode.Stream): fn(req: web.PostRequest) -> String | web.Error =>
  $stream = web.multipartStream($req) ?
  $part = web.multipartNext($stream) ?
  web.multipartDiscard($stream) ?
  "ok"
end
web.serve($app, web.ServerConfig(port = 0,
  limits = web.Limits(maxFormFields = 1, maxMultipartParts = 1,
                      maxMultipartNameBytes = 3, maxUploadFileBytes = 3))) ?'''
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        for optimized, serialized in ((False, False), (True, False), (True, True)):
            with self.subTest(optimized=optimized, serialized=serialized):
                bytecode = compile_program(program, optimize=optimized)
                handle, = run(loads(dumps(bytecode)) if serialized else bytecode)
                token = bridge._lease_token(handle)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        address = bridge._SERVER_LEASES[token].bound_address
                    conn = http.client.HTTPConnection(*address, timeout=4)
                    try:
                        for path in ('/upload', '/buffered'):
                            cases = [(form(field('a', b'ok')), 200),
                                     (form(field('a', b'x'), field('b', b'y')), 413),
                                     (form(field('long', b'x')), 413)]
                            if path == '/buffered':
                                cases.append((form(file('x', 'f', b'abcd')), 413))
                            for payload, status in cases:
                                conn.request('POST', path, payload,
                                             headers={'Content-Type': 'multipart/form-data; boundary=XYZ'})
                                resp = conn.getresponse()
                                self.assertEqual(resp.status, status, (path, payload[:90]))
                                resp.read()
                        for payload, status in ((form(file('x', 'f', b'ab')), 200),
                                                (form(file('x', 'f', b'abcd')), 413)):
                            conn.request('POST', '/file', payload,
                                         headers={'Content-Type': 'multipart/form-data; boundary=XYZ'})
                            resp = conn.getresponse()
                            self.assertEqual(resp.status, status)
                            resp.read()
                    finally:
                        conn.close()
                finally:
                    bridge._close_server_token(token, .5)

    def test_valiance_public_invalid_limits_reject_serve(self):
        source = '''import {std.web, std.web.App}
web.serve(App, web.ServerConfig(port = 0,
    limits = web.Limits(maxMultipartParts = 0)))'''
        analyser = Analyser()
        program = analyser.analyse(parse(source))
        self.assertEqual([str(d) for d in analyser.diagnostics], [])
        handle_or_err, = run(compile_program(program))
        self.assertEqual(handle_or_err.type_name, 'web.ServerError.Failed')


if __name__ == '__main__':
    unittest.main()
