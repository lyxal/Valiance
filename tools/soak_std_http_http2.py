"""Sustained multiplexed HTTP/2 interoperability and cancellation regression.

Uses an independent hyper-h2 TLS responder (not Valiance std.web), a single
libcurl client pool, and concurrent request/body futures without worker-per-
request blocking. Measures connection reuse, stream integrity, cancellation,
and post-shutdown cleanup. A local peer is not a production network benchmark.
"""
from __future__ import annotations

import argparse
from concurrent.futures import CancelledError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
import threading
import sys

# Test peer fixtures live in the source checkout, not the installed wheel.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for directory in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from tests.test_std_http_http2 import _write_test_certificate, H2Peer
from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_transport import Client, _PROCESS_BUFFER_BUDGET
from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET


def campaign(requests: int, concurrency: int) -> dict:
    problems = []
    completed = cancelled = submitted = early_close = uploads_submitted = 0
    started = monotonic()
    with TemporaryDirectory(prefix='valiance-h2-soak-') as root:
        cert, key = _write_test_certificate(Path(root))
        peer = H2Peer(cert, key)
        config = ClientConfig(ca_bundle=str(cert), max_pending=concurrency + 8,
                              max_connections=1, max_connections_per_origin=1,
                              body_buffer_bytes=4096, max_client_buffer_bytes=64 * 1024,
                              max_body_bytes=128 * 1024, total_timeout_ms=10_000)
        try:
            with Client(config) as client:
                headers = []
                bodies = []
                deadline = monotonic() + max(45, requests / 5)
                while submitted < requests or headers or bodies:
                    if monotonic() > deadline:
                        problems.append('HTTP/2 campaign exceeded its bounded operation deadline')
                        break
                    while submitted < requests and len(headers) + len(bodies) < concurrency:
                        index = submitted
                        # Exercise outbound HTTP/2 DATA framing and simultaneous
                        # upload/download streams, not just request headers.
                        upload = index % 7 == 0
                        request = (Request('POST', peer.base + '/echo',
                                           body=(f'binary-h2-{index}:'.encode() + bytes(range(64))))
                                   if upload else Request('GET', peer.base + f'/id/{index}'))
                        if upload:
                            uploads_submitted += 1
                        future = client.send_async(request)
                        headers.append((index, future))
                        submitted += 1
                        if index % 23 == 0 and future.cancel():
                            cancelled += 1
                    pending_headers = []
                    for index, future in headers:
                        if not future.done():
                            pending_headers.append((index, future))
                            continue
                        try:
                            response = future.result()
                            if response.metadata.protocol != 'HTTP/2':
                                raise AssertionError('expected HTTP/2 negotiation')
                            if index % 29 == 0:
                                response.body.close()
                                early_close += 1
                            else:
                                bodies.append((index, response.body.collect_async(4096)))
                        except CancelledError:
                            pass
                        except BaseException as exc:
                            if len(problems) < 20:
                                problems.append(f'headers {index}: {type(exc).__name__}: {exc}')
                    headers = pending_headers
                    pending_bodies = []
                    for index, future in bodies:
                        if not future.done():
                            pending_bodies.append((index, future))
                            continue
                        try:
                            body = future.result()
                            expected = (f'binary-h2-{index}:'.encode() + bytes(range(64))
                                        if index % 7 == 0 else f'GET /id/{index}'.encode('ascii'))
                            if body != expected:
                                raise AssertionError(f'wrong body for stream {index}: {body[:100]!r}')
                            completed += 1
                        except BaseException as exc:
                            if len(problems) < 20:
                                problems.append(f'body {index}: {type(exc).__name__}: {exc}')
                    bodies = pending_bodies
                    if headers or bodies:
                        sleep(.001)
                if client._reactor._buffer_budget.used:
                    problems.append('per-client response bytes not released')
            if client._reactor._thread.is_alive() or client._reactor._transfers:
                problems.append('reactor thread or native transfer remained alive')
        finally:
            peer.close()
        connections = peer.connections
        received_streams = len(peer.stream_ids)
        if peer.errors:
            problems.append('server errors: ' + repr(peer.errors[:3]))
    with _PROCESS_BUFFER_BUDGET._lock:
        response_remaining = _PROCESS_BUFFER_BUDGET.used
    with _PROCESS_UPLOAD_BUDGET._lock:
        upload_remaining = _PROCESS_UPLOAD_BUDGET.used
    threads = sum(t.name == 'valiance-http-reactor' for t in threading.enumerate())
    if response_remaining or upload_remaining or threads:
        problems.append('global reservations/reactor not released')
    if completed < requests * .75:
        problems.append('too few completed HTTP/2 responses')
    if not (1 <= connections <= max(4, requests // 50)):
        problems.append('unexpected HTTP/2 connection churn: ' + str(connections))
    return {
        'status': 'passed' if not problems else 'failed', 'requests_requested': requests,
        'requests_submitted': submitted, 'completed_body_reads': completed,
        'cancelled_before_headers': cancelled, 'early_close': early_close,
        'http2_binary_uploads_submitted': uploads_submitted,
        'independent_peer_streams_observed': received_streams,
        'independent_peer_connections_observed': connections,
        'duration_seconds': round(monotonic() - started, 3),
        'remaining_reactor_threads': threads,
        'remaining_response_bytes': response_remaining,
        'remaining_upload_bytes': upload_remaining,
        'problems': problems,
        'limitations': 'Local hyper-h2 peer only, not a native memory sanitizer or RFC 9113 exhaustive test.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=2000)
    parser.add_argument('--concurrency', type=int, default=32)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not 32 <= args.requests <= 25000 or not 1 <= args.concurrency <= 128:
        parser.error('requests must be 32..25000 and concurrency 1..128')
    try:
        result = campaign(args.requests, args.concurrency)
    except BaseException as exc:
        result = {'status': 'failed', 'problems': [f'{type(exc).__name__}: {exc}']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
