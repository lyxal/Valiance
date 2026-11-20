"""Deterministic black-box hostile HTTP/2 frame campaign (real TLS/ALPN peer).

Frames bypass hyper-h2's *outbound* checks; the target is the real libcurl
HTTP/2 parser behind std.http. This is a targeted corpus, not proof of complete
RFC 9113 conformance or a substitute for libFuzzer/h2spec.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import sys
import tempfile
from time import monotonic

# Test peer fixtures live in the source checkout, not the installed wheel.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for directory in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from tests.test_std_http_http2 import _write_test_certificate
from tests.test_std_http_http2_adversarial import MALFORMED_FRAMES, _HostileH2Server
from valiance.std._http_core import ClientConfig, Request
from valiance.std._http_errors import HttpError
from valiance.std._http_transport import Client, _PROCESS_BUFFER_BUDGET
from valiance.std._http_body import _PROCESS_UPLOAD_BUDGET


def selected_cases(seed: int, count: int) -> tuple[str, ...]:
    if count < len(MALFORMED_FRAMES):
        raise ValueError('count must cover every malformed frame family')
    rng = random.Random(seed)
    cases = list(MALFORMED_FRAMES)
    cases.extend(rng.choice(MALFORMED_FRAMES) for _ in range(count - len(cases)))
    rng.shuffle(cases)
    return tuple(cases)


def campaign(seed: int, count: int) -> dict:
    started = monotonic()
    outcomes: dict[str, int] = {name: 0 for name in MALFORMED_FRAMES}
    problems = []
    attempted = 0
    cases = selected_cases(seed, count)
    with tempfile.TemporaryDirectory(prefix='valiance-h2-fuzz-') as root:
        cert, key = _write_test_certificate(Path(root))
        with Client(ClientConfig(ca_bundle=str(cert), connect_timeout_ms=1300,
                                 total_timeout_ms=2500, body_buffer_bytes=8192,
                                 max_client_buffer_bytes=64 * 1024)) as client:
            for index, mutation in enumerate(cases):
                peer = _HostileH2Server(cert, key, mutation)
                attempted += 1
                outcome = 'rejected'
                try:
                    try:
                        response = client.send(Request('GET', peer.url))
                        response.body.bytes(8192)
                        outcome = 'accepted'
                    except HttpError:
                        outcomes[mutation] += 1
                    except BaseException as exc:
                        outcome = 'unexpected_exception'
                        if len(problems) < 30:
                            problems.append({'case': index, 'mutation': mutation,
                                             'exception': repr(exc)})
                finally:
                    peer.close()
                if outcome == 'accepted' and len(problems) < 30:
                    problems.append({'case': index, 'mutation': mutation,
                                     'error': 'malformed HTTP/2 frame was accepted'})
                if peer.failure and len(problems) < 30:
                    problems.append({'case': index, 'mutation': mutation,
                                     'peer_error': repr(peer.failure)})
            if client._reactor._buffer_budget.used or client._reactor._transfers:
                problems.append({'error': 'live transfer or client buffer reservation after corpus'})
    with _PROCESS_BUFFER_BUDGET._lock:
        response_remaining = _PROCESS_BUFFER_BUDGET.used
    with _PROCESS_UPLOAD_BUDGET._lock:
        upload_remaining = _PROCESS_UPLOAD_BUDGET.used
    if response_remaining or upload_remaining:
        problems.append({'error': 'process-wide budget leak',
                         'response_bytes': response_remaining, 'upload_bytes': upload_remaining})
    return {
        'seed': seed, 'count_requested': count, 'count_attempted': attempted,
        'cases_by_type': outcomes, 'duration_seconds': round(monotonic() - started, 3),
        'response_budget_remaining': response_remaining,
        'upload_budget_remaining': upload_remaining, 'problems': problems,
        'status': 'passed' if not problems and attempted == count else 'failed',
        'limitations': 'Black-box mutated independent TLS/ALPN peer; no native library sanitizer or exhaustive RFC fuzz coverage',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=20261120)
    parser.add_argument('--count', type=int, default=200)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if not len(MALFORMED_FRAMES) <= args.count <= 5000:
        parser.error('count must cover all mutation families, maximum 5000')
    try:
        report = campaign(args.seed, args.count)
    except BaseException as exc:
        report = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report.get(key) for key in
                      ('status', 'count_attempted', 'duration_seconds', 'problems', 'reason')}, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
