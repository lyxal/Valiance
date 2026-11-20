"""Measure std.http native/process resource recovery after repeated client use.

This is bounded, local-peer instrumentation, not a substitute for a native
ASan/Valgrind build of libcurl or a long-duration production soak test.
Metrics explicitly distinguish native RSS from tracemalloc's Python heap.
"""
from __future__ import annotations

import argparse
from concurrent.futures import CancelledError
import gc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import sys
import threading
import tracemalloc
from time import monotonic, sleep

from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET
from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_transport import Client, _PROCESS_BUFFER_BUDGET

try:
    import psutil
except ImportError:
    psutil = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_: object) -> None:
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client cancellation intentionally resets its keepalive socket.

    def do_GET(self) -> None:
        body = b'v' * 32000
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def _snapshot() -> dict[str, int | None]:
    gc.collect()
    sleep(.15)
    gc.collect()
    process = psutil.Process() if psutil is not None else None
    with _PROCESS_BUFFER_BUDGET._lock:
        response_used = _PROCESS_BUFFER_BUDGET.used
    with _PROCESS_UPLOAD_BUDGET._lock:
        upload_used = _PROCESS_UPLOAD_BUDGET.used
    return {
        'rss_bytes': process.memory_info().rss if process is not None else None,
        'open_fds': process.num_fds() if process is not None and hasattr(process, 'num_fds') else None,
        'python_traced_bytes': tracemalloc.get_traced_memory()[0],
        'active_reactor_threads': sum(t.name == 'valiance-http-reactor'
                                      for t in threading.enumerate()),
        'response_budget_used': response_used,
        'upload_budget_used': upload_used,
    }


def _cycle(base: str, clients: int) -> None:
    config = ClientConfig(max_pending=16, max_connections=2,
                          max_connections_per_origin=2,
                          max_body_bytes=128 * 1024,
                          body_buffer_bytes=16 * 1024,
                          max_client_buffer_bytes=32 * 1024)
    for iteration in range(clients):
        with Client(config) as client:
            for operation in range(5):
                result = client.send_async(Request('GET', base + '/body')).result(timeout=5)
                if operation == 0:
                    result.body.close()
                else:
                    if result.body.collect_async(32768).result(timeout=5) != b'v' * 32000:
                        raise AssertionError('incorrect downloaded body')
            pending = client.send_async(Request('GET', base + '/body'))
            if iteration % 3 == 0:
                pending.cancel()
            try:
                response = pending.result(timeout=5)
            except CancelledError:
                pass
            else:
                response.body.close()
        if client._reactor._thread.is_alive():
            raise AssertionError('reactor remained running after client shutdown')
        if client._reactor._buffer_budget.used or client._reactor._transfers:
            raise AssertionError('client leaked live transfer or queue reservations')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clients-per-batch', type=int, default=80)
    parser.add_argument('--batches', type=int, default=3)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.clients_per_batch <= 1000 or not 2 <= args.batches <= 8:
        parser.error('expected 1..1000 clients per batch and 2..8 batches')
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    tracemalloc.start()
    started = monotonic()
    failure = None
    samples = []
    try:
        _cycle(base, 12)  # One-time DNS, libcurl and interpreter cache warmup.
        samples.append(_snapshot())
        for number in range(args.batches):
            _cycle(base, args.clients_per_batch)
            samples.append(_snapshot())
            print(f'Profile batch {number + 1}/{args.batches} finished', flush=True)
    except BaseException as exc:
        failure = f'{type(exc).__name__}: {exc}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    baseline = samples[0] if samples else {}
    final = samples[-1] if samples else {}
    deltas = {name: (final[name] - baseline[name] if baseline.get(name) is not None
                     and final.get(name) is not None else None)
              for name in baseline}
    # Intentionally spacious, explicit leak-detection thresholds. Native RSS
    # can contain allocator caching even after live objects are freed.
    limits = {'python_traced_bytes': 8 * 1024 * 1024,
              'rss_bytes': 24 * 1024 * 1024, 'open_fds': 8}
    issues = ([failure] if failure else [])
    for field, limit in limits.items():
        if deltas.get(field) is not None and deltas[field] > limit:
            issues.append(f'{field} increased by {deltas[field]} (budget {limit})')
    for field in ('active_reactor_threads', 'response_budget_used', 'upload_budget_used'):
        if final.get(field, 0) != baseline.get(field, 0) or final.get(field, 0) != 0:
            issues.append(f'{field} not released: {final.get(field)}')
    report = {
        'status': 'passed' if not issues and len(samples) == args.batches + 1 else 'failed',
        'python': sys.version, 'platform': platform.platform(),
        'psutil_available': psutil is not None,
        'clients_after_warmup': args.clients_per_batch * args.batches,
        'requests_after_warmup': args.clients_per_batch * args.batches * 6,
        'duration_seconds': round(monotonic() - started, 3),
        'samples': samples, 'final_minus_warmup': deltas,
        'thresholds': limits, 'issues': issues,
        'caveat': 'RSS/heap threshold is a bounded growth smoke test; not an ASan/Valgrind or permanent memory-safety proof',
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'duration_seconds', 'final_minus_warmup', 'issues')}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
