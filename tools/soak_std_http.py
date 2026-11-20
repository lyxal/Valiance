"""Bounded long-running connection-pool, cancellation and throughput profile.

Uses a controlled local HTTP/1.1 peer and the libcurl multi reactor directly.
There is one transport reactor thread for many simultaneous requests; this
measurement does not create a Python worker thread per outbound request.

The report is a reproducible LOCAL throughput and lifetime baseline, not a
claim of long-duration production, Windows/macOS or sanitizer qualification.
"""
from __future__ import annotations

import argparse
from concurrent.futures import CancelledError, Future
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import threading
from time import monotonic, sleep

from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET
from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_transport import Client, _PROCESS_BUFFER_BUDGET

try:
    import psutil
except ImportError:
    psutil = None


_SMALL = b'a' * 1024
_LARGE = b'b' * 65536


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_: object) -> None:
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # A cancelled transfer can reset its socket after it leaves a
            # keepalive request idle; this is an expected local-peer event.
            pass

    def do_GET(self) -> None:
        if self.path == '/slow':
            sleep(.02)
        content = _LARGE if self.path == '/large' else _SMALL
        self.send_response(200)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _snapshot() -> dict[str, int | None]:
    gc.collect()
    process = psutil.Process() if psutil is not None else None
    with _PROCESS_BUFFER_BUDGET._lock:
        queued = _PROCESS_BUFFER_BUDGET.used
    with _PROCESS_UPLOAD_BUDGET._lock:
        upload = _PROCESS_UPLOAD_BUDGET.used
    return {
        'rss_bytes': process.memory_info().rss if process is not None else None,
        'open_fds': process.num_fds() if process is not None and hasattr(process, 'num_fds') else None,
        'reactor_threads': sum(t.name == 'valiance-http-reactor' for t in threading.enumerate()),
        'response_reserved_bytes': queued,
        'upload_reserved_bytes': upload,
    }


def soak(seconds: float, concurrency: int) -> dict:
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.daemon_threads = True
    server.block_on_close = False
    server_worker = threading.Thread(target=server.serve_forever,
                                     kwargs={'poll_interval': .02}, daemon=True)
    server_worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    baseline = _snapshot()
    started = monotonic()
    duration_end = started + seconds
    config = ClientConfig(max_pending=concurrency + 8,
                          max_connections=min(32, concurrency),
                          max_connections_per_origin=min(32, concurrency),
                          max_body_bytes=128 * 1024,
                          body_buffer_bytes=32 * 1024,
                          max_client_buffer_bytes=512 * 1024,
                          total_timeout_ms=12_000)
    submitted = 0
    completed = 0
    cancelled = 0
    intentionally_closed = 0
    unexpected = []
    latencies = []
    observed_max_reactors = 0
    # Pending headers or bodies are polled; no thread pool or blocking Future
    # is required for each request.
    pending_headers: list[tuple[Future, float, str]] = []
    pending_bodies: list[tuple[Future, float, int]] = []
    try:
        with Client(config) as client:
            while monotonic() < duration_end or pending_headers or pending_bodies:
                now = monotonic()
                while now < duration_end and len(pending_headers) + len(pending_bodies) < concurrency:
                    sequence = submitted
                    mode = 'cancel' if sequence % 17 == 0 else (
                        'abandon' if sequence % 19 == 0 else
                        'large' if sequence % 4 == 0 else 'small')
                    path = '/slow' if mode == 'cancel' else ('/large' if mode == 'large' else '/small')
                    future = client.send_async(Request('GET', base + path))
                    pending_headers.append((future, now, mode))
                    submitted += 1
                    if mode == 'cancel':
                        if future.cancel():
                            cancelled += 1
                    now = monotonic()
                next_headers = []
                for future, at, mode in pending_headers:
                    if not future.done():
                        next_headers.append((future, at, mode))
                        continue
                    try:
                        response = future.result()
                        if mode == 'abandon' or mode == 'cancel':
                            response.body.close()
                            intentionally_closed += 1
                        else:
                            expected = len(_LARGE) if mode == 'large' else len(_SMALL)
                            pending_bodies.append((response.body.collect_async(expected), at, expected))
                    except CancelledError:
                        pass
                    except BaseException as exc:
                        if len(unexpected) < 20:
                            unexpected.append(f'send: {type(exc).__name__}: {exc}')
                pending_headers = next_headers
                next_bodies = []
                for future, at, expected in pending_bodies:
                    if not future.done():
                        next_bodies.append((future, at, expected))
                        continue
                    try:
                        data = future.result()
                        if len(data) != expected:
                            raise AssertionError(f'expected {expected} bytes, read {len(data)}')
                        completed += 1
                        latencies.append((monotonic() - at) * 1000)
                    except BaseException as exc:
                        if len(unexpected) < 20:
                            unexpected.append(f'body: {type(exc).__name__}: {exc}')
                pending_bodies = next_bodies
                observed_max_reactors = max(observed_max_reactors,
                    sum(t.name == 'valiance-http-reactor' for t in threading.enumerate()))
                if pending_headers or pending_bodies:
                    sleep(.001)
            # Closing a client synchronizes native handle cleanup.
    finally:
        server.shutdown()
        server.server_close()
        server_worker.join(timeout=2)
    sleep(.15)
    final = _snapshot()
    elapsed = monotonic() - started
    growth = {
        key: final[key] - baseline[key] if baseline[key] is not None and final[key] is not None else None
        for key in ('rss_bytes', 'open_fds', 'reactor_threads', 'response_reserved_bytes', 'upload_reserved_bytes')
    }
    latencies.sort()
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * .95))] if latencies else None
    p99 = latencies[min(len(latencies) - 1, int(len(latencies) * .99))] if latencies else None
    failures = list(unexpected)
    if completed < 100:
        failures.append(f'insufficient completed operations: {completed}')
    if final['reactor_threads'] or final['response_reserved_bytes'] or final['upload_reserved_bytes']:
        failures.append('native reactor or buffer reservations remain after shutdown')
    if growth['open_fds'] is not None and growth['open_fds'] > 8:
        failures.append(f'file descriptor growth exceeded threshold: {growth["open_fds"]}')
    if growth['rss_bytes'] is not None and growth['rss_bytes'] > 64 * 1024 * 1024:
        failures.append(f'RSS growth exceeded 64 MiB: {growth["rss_bytes"]}')
    return {
        'status': 'passed' if not failures else 'failed',
        'platform': platform.platform(), 'python': sys.version.split()[0],
        'configured_duration_seconds': seconds, 'elapsed_seconds': round(elapsed, 3),
        'concurrency': concurrency, 'submitted': submitted,
        'completed_body_reads': completed, 'cancelled_before_headers': cancelled,
        'intentionally_closed': intentionally_closed,
        'successful_reads_per_second': round(completed / elapsed, 2),
        'p50_read_completion_ms': round(statistics.median(latencies), 2) if latencies else None,
        'p95_read_completion_ms': round(p95, 2) if p95 is not None else None,
        'p99_read_completion_ms': round(p99, 2) if p99 is not None else None,
        'peak_http_reactor_threads': observed_max_reactors,
        'baseline': baseline, 'final': final, 'final_minus_baseline': growth,
        'issues': failures,
        'limitations': 'Local HTTP/1.1 peer; not HTTP/2 soak or cross-platform/native sanitizer qualification.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration-seconds', type=float, default=20)
    parser.add_argument('--concurrency', type=int, default=32)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.duration_seconds <= 86400 or not 1 <= args.concurrency <= 512:
        parser.error('duration must be 1..86400 seconds and concurrency 1..512')
    report = soak(args.duration_seconds, args.concurrency)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'submitted', 'completed_body_reads',
            'successful_reads_per_second', 'p95_read_completion_ms', 'issues')}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
