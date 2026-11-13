"""v19: streaming multipart fields/uploads with strict MIME boundaries and ownership."""
from __future__ import annotations

import http.client
import os
import tempfile
import socket
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.std import _web_core as core, _web_http1 as http1
from valiance.std import web as bridge


class Source:
    def __init__(self, data: bytes, size: int = 11):
        self.data = data
        self.size = size
        self.closed = 0
        self.reads = 0
        self.biggest = 0

    def read(self, maximum):
        self.reads += 1
        if not self.data:
            return None
        amount = min(maximum, self.size)
        self.biggest = max(self.biggest, amount)
        out, self.data = self.data[:amount], self.data[amount:]
        return out

    def close(self):
        self.closed += 1


def upload(name: str, data: bytes, filename: str = 'unknown.bin') -> bytes:
    return (f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            'Content-Type: application/octet-stream\r\n\r\n').encode() + data


def field(name: str, data: bytes) -> bytes:
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + data


def body(parts, boundary=b'XYZ'):
    return b''.join(b'--' + boundary + b'\r\n' + p + b'\r\n' for p in parts) + b'--' + boundary + b'--\r\n'


def request(payload, *, content_type='multipart/form-data; boundary=XYZ', size=5, max_bytes=1024 ** 3):
    source = Source(payload, size)
    req = core.test_request_stream('POST', '/', source, max_bytes=max_bytes,
        headers=core.Headers((('Content-Type', content_type),)))
    return req, source


class MultipartStreamCoreTests(unittest.TestCase):
    def test_order_repeated_fields_binary_file_and_chunk_boundaries(self):
        octets = b'\x00\x80\xff' + b'abc\r\n--XYZQnot-a-boundary' + b'\x00'
        parts = [field('label', b'first'), upload('files', octets), field('label', b'second')]
        for size in (1, 2, 7, 8192):
            with self.subTest(size=size):
                req, src = request(body(parts), size=size)
                stream = core.multipart_stream(req)
                one = stream.next_part()
                self.assertEqual((one.name, one.value, one.filename), ('label', 'first', None))
                two = stream.next_part()
                self.assertEqual((two.name, two.filename, two.value), ('files', 'unknown.bin', None))
                with self.assertRaises(core.WebError) as error:
                    stream.next_part()
                self.assertEqual(error.exception.name, 'BodyUnavailable')
                self.assertEqual(b''.join(iter(lambda: stream.read_part(3), None)), octets)
                three = stream.next_part()
                self.assertEqual((three.name, three.value), ('label', 'second'))
                self.assertIsNone(stream.next_part())
                self.assertTrue(req.body_stream.complete)
                self.assertEqual(src.closed, 1)
                self.assertLessEqual(src.biggest, 8192)

    def test_large_upload_has_bounded_working_window(self):
        payload = b'\xff' * (2 * 1024 * 1024 + 17)
        req, src = request(body([upload('blob', payload)]), size=11234, max_bytes=4 * 1024 ** 2)
        stream = core.multipart_stream(req)
        self.assertEqual(stream.next_part().filename, 'unknown.bin')
        total = 0
        while (chunk := stream.read_part(32000)) is not None:
            self.assertLessEqual(len(chunk), 32000)
            total += len(chunk)
            self.assertLess(len(stream._buffer), 65536)
        self.assertEqual(total, len(payload))
        self.assertIsNone(stream.next_part())
        self.assertGreater(src.reads, 100)
        self.assertEqual(src.closed, 1)

    def test_partial_discard_and_unclaimed_early_close(self):
        req, src = request(body([upload('one', b'A' * 120000), upload('two', b'B')]), size=2048)
        stream = core.multipart_stream(req)
        stream.next_part()
        self.assertEqual(stream.read_part(3), b'AAA')
        stream.discard_part()
        self.assertEqual(stream.next_part().name, 'two')
        stream.discard_part()
        self.assertIsNone(stream.next_part())
        self.assertEqual(src.closed, 1)
        req2, src2 = request(body([upload('one', b'A' * 120000)]))
        stream2 = core.multipart_stream(req2)
        stream2.next_part()
        stream2.close()
        self.assertEqual(src2.closed, 1)
        self.assertFalse(req2.body_stream.complete)
        stream2.close()
        self.assertEqual(src2.closed, 1)

    def test_stream_save_exclusive_no_clobber_and_error_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, 'upload.bin')
            octets = b'\x00\xff' * 40000
            req, src = request(body([upload('data', octets, '../../untrusted.txt')]), size=6000)
            stream = core.multipart_stream(req)
            self.assertEqual(stream.next_part().filename, '../../untrusted.txt')
            self.assertEqual(stream.save_part(destination), len(octets))
            with open(destination, 'rb') as f:
                self.assertEqual(f.read(), octets)
            self.assertIsNone(stream.next_part())
            self.assertEqual(src.closed, 1)
            req2, _ = request(body([upload('data', b'X')]))
            stream2 = core.multipart_stream(req2)
            stream2.next_part()
            with self.assertRaises(core.WebError) as error:
                stream2.save_part(destination)
            self.assertEqual(error.exception.name, 'Conflict')
            with open(destination, 'rb') as f:
                self.assertEqual(f.read(), octets)
            stream2.close()
            invalid = (b'--XYZ\r\n' + upload('data', b'partial') + b'\r\n--wrong--\r\n')
            req3, _ = request(invalid)
            stream3 = core.multipart_stream(req3)
            stream3.next_part()
            other_path = os.path.join(directory, 'incomplete')
            with self.assertRaises(core.WebError):
                stream3.save_part(other_path)
            self.assertFalse(os.path.exists(other_path))

    def test_empty_body_and_filename_empty(self):
        req, src = request(b'--XYZ--\r\n')
        self.assertIsNone(core.multipart_stream(req).next_part())
        self.assertEqual(src.closed, 1)
        req2, _ = request(body([upload('u', b'ab', filename='')]))
        parser = core.multipart_stream(req2)
        self.assertTrue(parser.next_part().is_file)
        parser.discard_part()
        self.assertIsNone(parser.next_part())

    def test_content_type_wrong_mode_and_single_owner(self):
        req, _ = request(body([field('ok', b'a')]))
        stream = core.multipart_stream(req)
        with self.assertRaises(core.WebError) as error:
            core.multipart_stream(req)
        self.assertEqual(error.exception.name, 'BodyUnavailable')
        stream.close()
        req, _ = request(body([]), content_type='application/json')
        with self.assertRaises(core.WebError) as error:
            core.multipart_stream(req)
        self.assertEqual(error.exception.name, 'UnsupportedMediaType')
        with self.assertRaises(core.WebError) as error:
            core.multipart_stream(core.test_request('POST', '/'))
        self.assertEqual(error.exception.name, 'WrongMode')

    def test_reject_malformed_header_and_missing_closing_boundary(self):
        bad = [
            b'--XYZ\r\nContent-Disposition: form-data; name="x"\r\n\r\nmissing final',
            b'--XYZ\r\nContent-Disposition: form-data; name="x"\r\nX-Test: a\r\nX-Test: b\r\n\r\nx\r\n--XYZ--\r\n',
            b'--XYZ\r\nContent-Disposition: form-data; name="x"\r\nX-Test: a\r\n\r\nx\r\n--XYZ--\r\n',
            b'--XYZ\r\nContent-Disposition: form-data; name="x"; filename*=utf-8\r\n\r\nx\r\n--XYZ--\r\n',
            b'--XYZ\r\nContent-Disposition: form-data; name="x"\r\n\r\nx\r\n--XYZ--\r\nextra',
        ]
        for data in bad:
            with self.subTest(data=data[:45]):
                req, src = request(data, size=2)
                stream = core.multipart_stream(req)
                with self.assertRaises(core.WebError) as error:
                    while (part := stream.next_part()) is not None:
                        if part.is_file:
                            stream.discard_part()
                self.assertIn(error.exception.name, ('InvalidMultipart', 'BodyTooLarge'))
                self.assertEqual(src.closed, 1)
        req, src = request(body([field('a', b'\xff')]))
        with self.assertRaises(core.WebError) as error:
            core.multipart_stream(req).next_part()
        self.assertEqual(error.exception.name, 'InvalidMultipart')
        self.assertEqual(src.closed, 1)

    def test_field_file_part_and_total_limits(self):
        cases = (
            (body([field('f', b'abcde')]), {'max_field_bytes': 4}),
            (body([upload('f', b'abcde')]), {'max_file_bytes': 4}),
            (body([field('f', b'a'), field('f', b'b')]), {'max_fields': 1}),
            (body([upload('f', b'a'), upload('f', b'b')]), {'max_files': 1}),
            (body([field('f', b'a'), field('f', b'b')]), {'max_parts': 1}),
            (body([field('f', b'a')]), {'max_header_bytes': 12}),
        )
        for data, limits in cases:
            with self.subTest(limits=limits):
                req, src = request(data)
                parser = core.multipart_stream(req, **limits)
                with self.assertRaises(core.WebError) as caught:
                    while (part := parser.next_part()) is not None:
                        if part.is_file:
                            parser.discard_part()
                self.assertEqual(caught.exception.name, 'BodyTooLarge')
                self.assertEqual(src.closed, 1)
        req, src = request(body([upload('f', b'123456789')]), max_bytes=45)
        parser = core.multipart_stream(req)
        with self.assertRaises(core.WebError) as error:
            while (part := parser.next_part()) is not None:
                parser.discard_part()
        self.assertEqual(error.exception.name, 'BodyTooLarge')
        self.assertEqual(src.closed, 1)


class MultipartStreamHTTPTests(unittest.TestCase):
    def server(self, app):
        server = http1.serve(app, port=0)
        self.addCleanup(server.shutdown, .5)
        return server

    def test_live_large_and_chunked_with_keepalive_and_trailers(self):
        seen = []
        def handle(req):
            stream = core.multipart_stream(req)
            result = []
            while (part := stream.next_part()) is not None:
                if part.is_file:
                    count = 0
                    while (data := stream.read_part(32768)) is not None:
                        count += len(data)
                    result.append((part.name, count))
                else:
                    result.append((part.name, part.value))
            seen.append((result, core.trailers(req).one('X-Checksum')))
            return 'received'
        app = core.App().route('POST', '/upload', handle).with_body_mode('POST', '/upload', 'Stream')
        app = app.route('GET', '/health', lambda req: 'healthy')
        server = self.server(app)
        content = body([field('name', b'Ada'), upload('bin', b'\xff' * 250000)])
        with socket.create_connection(server.bound_address, timeout=5) as sock:
            sock.settimeout(5)
            pieces = [content[i:i + 117] for i in range(0, len(content), 117)]
            chunks = b''.join(f'{len(p):X}\r\n'.encode() + p + b'\r\n' for p in pieces)
            wire = (b'POST /upload HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n'
                    b'Trailer: X-Checksum\r\nContent-Type: multipart/form-data; boundary=XYZ\r\n\r\n' + chunks + b'0\r\nX-Checksum: good\r\n\r\n'
                    b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
            sock.sendall(wire)
            response = b''
            while (block := sock.recv(65536)):
                response += block
        self.assertEqual(response.count(b'HTTP/1.1 200'), 2)
        self.assertIn(b'received', response)
        self.assertIn(b'healthy', response)
        self.assertEqual(seen, [([('name', 'Ada'), ('bin', 250000)], 'good')])

    def test_unread_file_closes_connection_without_processing_next_request(self):
        seen = []
        def early(req):
            stream = core.multipart_stream(req)
            self.assertEqual(stream.next_part().name, 'file')
            seen.append('upload')
            return 'early'
        app = core.App().route('POST', '/upload', early).with_body_mode('POST', '/upload', 'Stream')
        app = app.route('GET', '/next', lambda req: seen.append('next') or 'next')
        server = self.server(app)
        payload = body([upload('file', b'\x80' * 100000)])
        with socket.create_connection(server.bound_address, timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall((b'POST /upload HTTP/1.1\r\nHost: localhost\r\n'
                          b'Content-Type: multipart/form-data; boundary=XYZ\r\n'
                          + f'Content-Length: {len(payload)}\r\n\r\n'.encode()
                          + payload + b'GET /next HTTP/1.1\r\nHost: localhost\r\n\r\n'))
            response = b''
            while (piece := sock.recv(65536)):
                response += piece
        self.assertIn(b'HTTP/1.1 200', response)
        self.assertIn(b'Connection: close', response)
        self.assertNotIn(b'next', response)
        self.assertEqual(seen, ['upload'])

    def test_live_rejection_of_invalid_text_field(self):
        def invalid(req):
            parser = core.multipart_stream(req)
            parser.next_part()
            return 'unreachable'
        app = core.App().route('POST','/',invalid).with_body_mode('POST','/','Stream')
        server = self.server(app)
        conn = http.client.HTTPConnection(*server.bound_address, timeout=4)
        self.addCleanup(conn.close)
        conn.request('POST','/',body([field('name', b'\xff')]),
                     headers={'Content-Type':'multipart/form-data; boundary=XYZ'})
        reply = conn.getresponse()
        self.assertEqual(reply.status,400)
        # The reader has reached HTTP EOF; a complete, rejected form does not
        # require a connection-close when its framing was safe.
        reply.read()


class MultipartStreamValianceTests(unittest.TestCase):
    SOURCE = r'''import {std.web, std.web.App}
$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $reader = web.multipartStream($req) ?
  $first = web.multipartNext($reader) ?
  $someBytes = web.multipartRead($reader, 2) ?
  web.multipartDiscard($reader) ?
  $last = web.multipartNext($reader) ?
  "accepted"
end
$app = web.withBodyMode($app, web.Method.POST, "/upload", web.BodyMode.Stream) ?
web.serve($app, web.ServerConfig(port = 0)) ?'''

    def test_valiance_saves_binary_file_with_explicit_path(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, 'saved.dat')
            source = r'''import {std.web, std.web.App, std.path.Path}
$app = App
$app := post("/save"): fn(req: web.PostRequest) -> String | web.Error =>
  $reader = web.multipartStream($req) ?
  $file = web.multipartNext($reader) ?
  $size = web.saveMultipartPart($reader, Path(__DEST__)) ?
  $end = web.multipartNext($reader) ?
  "stored"
end
$app = web.withBodyMode($app, web.Method.POST, "/save", web.BodyMode.Stream) ?
web.serve($app, web.ServerConfig(port = 0)) ?'''.replace('__DEST__', json.dumps(destination))
            analyser = Analyser()
            analysed = analyser.analyse(parse(source))
            self.assertEqual([str(d) for d in analyser.diagnostics], [])
            lease, = run(compile_program(analysed))
            token = bridge._lease_token(lease)
            try:
                with bridge._SERVER_LEASES_LOCK:
                    address = bridge._SERVER_LEASES[token].bound_address
                client = http.client.HTTPConnection(*address, timeout=5)
                try:
                    file_bytes = b'\x00\x80\xff' * 10000
                    client.request('POST', '/save', body([upload('blob', file_bytes, 'evil/../x')]),
                                   headers={'Content-Type': 'multipart/form-data; boundary=XYZ'})
                    reply = client.getresponse()
                    self.assertEqual((reply.status, reply.read()), (200, b'stored'))
                    with open(destination, 'rb') as f:
                        self.assertEqual(f.read(), file_bytes)
                finally:
                    client.close()
            finally:
                bridge._close_server_token(token, .5)

    def test_valiance_live_normal_and_serialized(self):
        for serialize in (False, True):
            with self.subTest(serialized=serialize):
                analyser = Analyser()
                analysed = analyser.analyse(parse(self.SOURCE))
                self.assertEqual([str(d) for d in analyser.diagnostics], [])
                program = compile_program(analysed, optimize=serialize)
                server_lease, = run(loads(dumps(program)) if serialize else program)
                token = bridge._lease_token(server_lease)
                try:
                    with bridge._SERVER_LEASES_LOCK:
                        address = bridge._SERVER_LEASES[token].bound_address
                    client = http.client.HTTPConnection(*address, timeout=5)
                    try:
                        client.request('POST','/upload',body([upload('blob', b'\x00\x80\xff')]),
                                       headers={'Content-Type':'multipart/form-data; boundary=XYZ'})
                        res = client.getresponse()
                        self.assertEqual((res.status,res.read()), (200,b'accepted'))
                        with bridge._MULTIPART_STREAM_LOCK:
                            self.assertEqual(len(bridge._MULTIPART_STREAM_LEASES), 0)
                    finally:
                        client.close()
                finally:
                    bridge._close_server_token(token, .5)


if __name__ == '__main__':
    unittest.main()
