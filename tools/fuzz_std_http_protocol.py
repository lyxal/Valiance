"""Deterministic, offline, black-box HTTP/1.1 response-mutation campaign.

Run this against the real std.http/libcurl reactor, not a reimplementation of
HTTP parsing. Mutations include valid status bodies, truncated fixed/chunked
bodies, invalid status/headers, header bombs, compressed expansion, and
interim responses. This is a reproducible targeted campaign, NOT an exhaustive
RFC fuzzer or a memory sanitizer.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import json
from pathlib import Path
import random
import socketserver
import sys
import threading
from time import monotonic

from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_errors import ErrorKind, HttpError
from valiance.std._http_transport import Client, _PROCESS_BUFFER_BUDGET
from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET


@dataclass(frozen=True)
class Case:
    name: str
    wire: bytes
    expected: str
    body: bytes = b''


def make_cases(seed: int, count: int) -> list[Case]:
    rng = random.Random(seed)
    cases = []
    names = ('fixed_truncated', 'chunked_truncated', 'header_bomb',
             'bad_status', 'bad_field', 'gzip_expansion',
             'valid_success', 'valid_http_error', 'interim_103')
    for index in range(count):
        name = names[index % len(names)]
        payload = rng.randbytes(rng.randrange(0, 1536))
        if name == 'fixed_truncated':
            wire = (f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload) + rng.randint(1, 1000)}\r\n'
                    'Connection: close\r\n\r\n').encode() + payload
            expected = 'error'
        elif name == 'chunked_truncated':
            # Announce strictly more data than provided; omit terminating CRLF
            # and the zero-size chunk. EOF must not masquerade as success.
            wire = (b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
                    b'Connection: close\r\n\r\n' +
                    f'{len(payload) + rng.randint(1, 1000):x}\r\n'.encode() + payload)
            expected = 'error'
        elif name == 'header_bomb':
            wire = (b'HTTP/1.1 200 OK\r\nX-Fill: ' + b'A' * rng.randint(3000, 4500) +
                    b'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            expected = 'error'
        elif name == 'bad_status':
            wire = b'NOTHTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n'
            expected = 'error'
        elif name == 'bad_field':
            wire = b'HTTP/1.1 200 OK\r\nBad Header: x\r\nContent-Length: 0\r\n\r\n'
            expected = 'error'
        elif name == 'gzip_expansion':
            compressed = gzip.compress(b'A' * rng.randint(9000, 60000), mtime=0)
            wire = (f'HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\n'
                    f'Content-Length: {len(compressed)}\r\nConnection: close\r\n\r\n').encode() + compressed
            expected = 'error'
        elif name == 'valid_success':
            wire = (f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n'
                    'Connection: close\r\n\r\n').encode() + payload
            expected = 'success'
        elif name == 'valid_http_error':
            code = rng.choice((404, 408, 429, 500, 503))
            wire = (f'HTTP/1.1 {code} Error\r\nContent-Length: {len(payload)}\r\n'
                    'Connection: close\r\n\r\n').encode() + payload
            expected = 'success'
        else:
            wire = (b'HTTP/1.1 103 Early Hints\r\nLink: </x>; rel=preload\r\n\r\n' +
                    (f'HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n'
                     'Connection: close\r\n\r\n').encode() + payload)
            expected = 'success'
        cases.append(Case(name, wire, expected, payload))
    return cases


class _RawPeer(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(2)
        line = b''
        try:
            while b'\r\n\r\n' not in line and len(line) < 16 * 1024:
                received = self.request.recv(4096)
                if not received:
                    return
                line += received
            index = int(line.split(b' ', 2)[1].split(b'/')[-1])
            self.request.sendall(self.server.cases[index].wire)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass


class _RawServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def campaign(seed: int, count: int) -> dict:
    cases = make_cases(seed, count)
    server = _RawServer(('127.0.0.1', 0), _RawPeer)
    server.cases = cases
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={'poll_interval': .02}, daemon=True)
    started = monotonic()
    failures = []
    attempted = 0
    try:
        thread.start()
        config = ClientConfig(connect_timeout_ms=1000, total_timeout_ms=2500,
                              max_response_header_bytes=2048,
                              max_body_bytes=4096, body_buffer_bytes=2048,
                              max_client_buffer_bytes=16 * 1024)
        with Client(config) as client:
            for index, case in enumerate(cases):
                attempted += 1
                observed = 'success'
                result = None
                try:
                    response = client.send(Request('GET',
                        f'http://127.0.0.1:{server.server_address[1]}/{index}'))
                    result = response.body.bytes(4096)
                except HttpError as exc:
                    observed = 'error'
                except BaseException as exc:
                    observed = 'exception'
                    if len(failures) < 30:
                        failures.append({'index': index, 'case': case.name,
                                         'exception': type(exc).__name__, 'detail': str(exc)})
                if observed != case.expected or (observed == 'success' and result != case.body):
                    if len(failures) < 30:
                        failures.append({'index': index, 'case': case.name,
                                         'expected': case.expected, 'observed': observed,
                                         'result_length': len(result) if result is not None else None})
    finally:
        server.shutdown()
        server.server_close()
        if thread.is_alive():
            thread.join(timeout=3)
    with _PROCESS_BUFFER_BUDGET._lock:
        response_remaining = _PROCESS_BUFFER_BUDGET.used
    with _PROCESS_UPLOAD_BUDGET._lock:
        upload_remaining = _PROCESS_UPLOAD_BUDGET.used
    if response_remaining or upload_remaining:
        failures.append({'response_budget': response_remaining,
                         'upload_budget': upload_remaining})
    return {'seed': seed, 'cases_requested': count, 'cases_completed': attempted,
            'cases_by_type': {name: sum(case.name == name for case in cases)
                              for name in sorted({case.name for case in cases})},
            'duration_seconds': round(monotonic() - started, 3),
            'response_budget_remaining': response_remaining,
            'upload_budget_remaining': upload_remaining,
            'failures': failures, 'status': 'passed' if not failures and attempted == count else 'failed',
            'limitations': 'Targeted, deterministic raw-wire corpus; no HTTP/2 fuzzing, no allocator sanitizer.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261120)
    parser.add_argument('--count', type=int, default=360)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not 9 <= args.count <= 10000:
        parser.error('count must be 9..10000')
    report = campaign(args.seed, args.count)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in
                      ('status', 'cases_completed', 'duration_seconds', 'failures')}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
