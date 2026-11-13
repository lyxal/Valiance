#!/usr/bin/env python3
"""Reproducible H1/H2 WebSocket memory and reclamation profiling.

Each fixture runs in a fresh Python process, so tracemalloc peaks and RSS
measurements are not contaminated by previous scenarios. Live server tests
assert listener/stream reservation and admission cleanup separately; this tool
adds sampled process RSS, CPython heap high-water marks, and thread deltas.

Usage: PYTHONPATH=src:. python tools/qualify_websocket_resources.py \\
           --repeat 3 --output .artifacts/websocket-resource-profile.json

This is a qualification guard, NOT a proof that all TLS/HPACK buffers fit in
maxTotalBufferedBytes. Resident-memory ceilings are explicit *test-environment*
thresholds and cannot replace path-by-path budget accounting.
"""
from __future__ import annotations

import argparse
import gc
import importlib.metadata
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest


SCENARIOS = {
    'h1_fragmentation': [
        'tests.test_std_web_ws08_ws11_resource_gates.ResourceGates.test_incremental_octet_input_releases_inbound_reservations',
        'tests.test_std_web_ws08_ws11_resource_gates.ResourceGates.test_many_partially_delivered_frames_share_one_pre_read_budget',
        'tests.test_std_web_ws08_ws11_resource_gates.ResourceGates.test_heartbeat_fifo_reserves_then_releases_pending_messages',
        'tests.test_std_web_websocket_ws13_frame_corpus.FrameCorpus.test_fixed_seed_fragmentation_utf8_binary_and_pong',
        'tests.test_std_web_websocket_ws13_frame_corpus.FrameCorpus.test_protocol_rejections_close_with_rfc_codes_and_reclaim_bytes',
    ],
    'h1_churn': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_h1_repeated_websocket_churn_reclaims_session_and_byte_counters',
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_h1_outbound_fragmentation_with_small_public_queue',
    ],
    'h2_resets': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_h2_repeated_websocket_resets_preserve_http_and_reclaim_bytes',
        'tests.test_std_web_v29_websocket_h2.H2WebSocketWireTests.test_rfc6455_invalid_frames_are_independent_h2_stream_failures',
        'tests.test_std_web_v29_websocket_h2.H2WebSocketWireTests.test_fragmented_binary_ping_parallel_h2_streams_with_http_get',
    ],
    'h2_header_staging': [
        'tests.test_std_web_http2_header_fragment_fuzz.HTTP2HeaderFragmentFuzzTests.test_header_quota_rejects_midframe_with_constant_metadata_storage',
        'tests.test_std_web_http2_header_fragment_fuzz.HTTP2HeaderFragmentFuzzTests.test_padded_priority_field_is_not_overcounted_during_fragmentation',
        'tests.test_std_web_http2_header_fragment_fuzz.HTTP2HeaderFragmentFuzzTests.test_randomized_valid_and_over_limit_compressed_field_fragments',
        'tests.test_std_web_v29_websocket_h2.H2WebSocketWireTests.test_h2_wire_and_hpack_high_water_metrics_reclaim_admission',
    ],
    'h2_backpressure': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_h2_closed_windows_on_two_clients_release_global_queue_after_reset',
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_h2_outbound_flow_with_small_public_queue',
    ],
    'mixed_tls_h1_h2_churn': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_mixed_tls_h1_h2_churn_preserves_listener_wide_admission',
    ],
    'tls_handshake_stalls': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_stalled_tls_clienthello_does_not_block_wss_and_reclaims_slot',
    ],
    'tls_connection_admission': [
        'tests.test_std_web_ws08_ws11_resource_gates.LiveTransportBudgetCases.test_tls_h1_h2_connection_cap_and_worker_reclamation',
    ],
    'h2_mixed_wire_fuzz': [
        'tests.test_std_web_v29_websocket_h2.H2WebSocketWireTests.test_mixed_seeded_rfc6455_errors_and_fragmentation_stay_stream_local',
    ],
}


def rss_bytes(pid: int) -> int | None:
    """Current resident set on Linux; never confuse VmHWM with live RSS."""
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def worker(scenario: str, target: Path) -> int:
    # Import the test modules before defining the baseline, so import caches
    # are not incorrectly called leaked per-session allocations.
    suite = unittest.defaultTestLoader.loadTestsFromNames(SCENARIOS[scenario])
    gc.collect()
    baseline_rss = rss_bytes(os.getpid())
    baseline_threads = len(threading.enumerate())
    baseline_non_daemon = sum(not t.daemon for t in threading.enumerate())
    tracemalloc.start(5)
    before_heap, _ = tracemalloc.get_traced_memory()
    started = time.monotonic()
    uncaught_workers: list[str] = []
    previous_hook = threading.excepthook
    def track_worker_failure(args: threading.ExceptHookArgs) -> None:
        uncaught_workers.append(f'{args.thread.name}: '
            f'{args.exc_type.__name__}: {args.exc_value}')
        previous_hook(args)
    threading.excepthook = track_worker_failure
    try:
        with open(os.devnull, 'w', encoding='utf-8') as quiet:
            result = unittest.TextTestRunner(stream=quiet, verbosity=0).run(suite)
        # HTTP/2 pool workers have a bounded idle tick before exit.
        time.sleep(.18)
    finally:
        threading.excepthook = previous_hook
    gc.collect()
    after_heap, peak_heap = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    sample = {
        'scenario': scenario,
        'tests': result.testsRun,
        'failures': [f'{case}: {details[:800]}' for case, details in result.failures],
        'errors': [f'{case}: {details[:800]}' for case, details in result.errors],
        'skips': [(str(case), reason) for case, reason in result.skipped],
        'duration_seconds': round(time.monotonic() - started, 3),
        'traced_heap_before_bytes': before_heap,
        'traced_heap_after_bytes': after_heap,
        'traced_heap_peak_bytes': peak_heap,
        'traced_heap_retained_delta_bytes': after_heap - before_heap,
        'rss_before_bytes': baseline_rss,
        'rss_after_bytes': rss_bytes(os.getpid()),
        'uncaught_worker_exceptions': uncaught_workers,
        'non_daemon_thread_delta': (sum(not t.daemon for t in threading.enumerate()) -
                                    baseline_non_daemon),
        'thread_count_before': baseline_threads,
        'thread_count_after': len(threading.enumerate()),
        'passed': result.wasSuccessful() and not result.skipped and
                  not uncaught_workers and result.testsRun == len(SCENARIOS[scenario]),
    }
    target.write_text(json.dumps(sample, indent=2) + '\n')
    return 0 if sample['passed'] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', type=Path, default=Path(
        '.artifacts/websocket-resource-profile.json'))
    parser.add_argument('--rss-ceiling-mib', type=int, default=768,
                        help='Test-host absolute per-worker resident set ceiling')
    parser.add_argument('--rss-drift-mib', type=int, default=128,
                        help='Maximum after-minus-before RSS for one fixture')
    parser.add_argument('--heap-peak-mib', type=int, default=384,
                        help='Maximum traced CPython peak for one fixture')
    parser.add_argument('--heap-retained-mib', type=int, default=64,
                        help='Maximum remaining traced CPython allocations after a fixture')
    parser.add_argument('--timeout-seconds', type=int, default=120)
    parser.add_argument('--worker', choices=SCENARIOS, help=argparse.SUPPRESS)
    parser.add_argument('--worker-result', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.worker_result is None:
            parser.error('--worker-result is required in worker mode')
        return worker(args.worker, args.worker_result)
    if args.repeat < 1 or args.timeout_seconds < 1:
        parser.error('repeat and timeout must be positive')

    report = {
        'python': sys.version.split()[0],
        'executable': sys.executable,
        'platform': sys.platform,
        'versions': {name: importlib.metadata.version(name)
                     for name in ('h2', 'hpack', 'hyperframe', 'websockets')},
        'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'repeat': args.repeat,
        'scenarios': SCENARIOS,
        'limits': {'rss_ceiling_bytes': args.rss_ceiling_mib*1048576,
                   'rss_drift_bytes': args.rss_drift_mib*1048576,
                   'traced_heap_peak_bytes': args.heap_peak_mib*1048576,
                   'traced_heap_retained_bytes': args.heap_retained_mib*1048576},
        'samples': [],
        'qualification_passed': True,
        'scope_note': 'Measured process memory + exact shared-reservation assertions; '
                      'not a proof of full TLS/HPACK/native allocation budgeting.',
    }
    with tempfile.TemporaryDirectory(prefix='valiance-websocket-qual-') as tmp:
        for scenario in SCENARIOS:
            for index in range(args.repeat):
                result_path = Path(tmp) / 'result.json'
                log_path = Path(tmp) / 'worker.log'
                cmd = [sys.executable, str(Path(__file__).resolve()),
                       '--worker', scenario, '--worker-result', str(result_path)]
                peak_rss = 0
                started = time.monotonic()
                with log_path.open('w') as log:
                    # The worker may launch TLS/server helpers. Give it a
                    # process group so a qualification timeout does not leave
                    # those helpers alive or contaminate later samples.
                    process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=os.name == 'posix',
                        env={**os.environ, 'PYTHONFAULTHANDLER': '1'})
                    while process.poll() is None:
                        peak_rss = max(peak_rss, rss_bytes(process.pid) or 0)
                        if time.monotonic()-started > args.timeout_seconds:
                            try:
                                if os.name == 'posix':
                                    os.killpg(process.pid, signal.SIGKILL)
                                else:
                                    process.kill()
                            except ProcessLookupError:
                                pass
                            break
                        time.sleep(.012)
                    code = process.wait()
                if result_path.exists():
                    sample = json.loads(result_path.read_text())
                    result_path.unlink()
                else:
                    sample = {'scenario': scenario, 'passed': False,
                              'errors': ['Worker failed to produce metrics']}
                sample.update(repeat=index+1, exit_code=code,
                              sampled_peak_rss_bytes=peak_rss,
                              duration_wall_seconds=round(time.monotonic()-started, 3))
                before = sample.get('rss_before_bytes')
                after = sample.get('rss_after_bytes')
                if (before is None or after is None or peak_rss == 0):
                    sample['passed'] = False
                    sample.setdefault('errors', []).append('RSS measurement unavailable')
                else:
                    sample['rss_retained_delta_bytes'] = after - before
                    if (peak_rss > report['limits']['rss_ceiling_bytes'] or
                            after-before > report['limits']['rss_drift_bytes'] or
                            sample.get('traced_heap_peak_bytes', 10**20) >
                            report['limits']['traced_heap_peak_bytes'] or
                            sample.get('traced_heap_retained_delta_bytes', 10**20) >
                            report['limits']['traced_heap_retained_bytes'] or
                            sample.get('thread_count_after', 10**20) >
                            sample.get('thread_count_before', 0) or
                            sample.get('non_daemon_thread_delta', 10**20) > 0):
                        sample['passed'] = False
                        sample.setdefault('errors', []).append('Memory qualification guard exceeded')
                sample['passed'] = bool(sample.get('passed') and code == 0)
                if not sample['passed']:
                    report['qualification_passed'] = False
                    sample['worker_log_tail'] = log_path.read_text(errors='replace')[-4000:]
                report['samples'].append(sample)
                print(f"{scenario} repeat {index+1}: "
                      f"{'PASS' if sample['passed'] else 'FAIL'}; "
                      f"RSS peak {peak_rss / 1048576:.1f} MiB; "
                      f"tracemalloc peak {sample.get('traced_heap_peak_bytes', 0) / 1048576:.1f} MiB",
                      flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(f'Profile written: {args.output}', flush=True)
    return 0 if report['qualification_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
