"""Standalone real-TLS HTTP/2 target for independent conformance tools.

Example: python3.14 scripts/http2_conformance_fixture.py --cert cert.pem
         --key key.pem --port 0 --ready-file /tmp/valiance-h2-port
Then: h2spec -t -k -S -h 127.0.0.1 -p "$(cat /tmp/valiance-h2-port)"

This fixture is intentionally independent of unittest and uses the product's
real listener, routing and response implementation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
from threading import Event

from valiance.std import _web_core as core, _web_http1 as http1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cert', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--ready-file', type=Path, required=True)
    args = parser.parse_args()

    # The independent h2spec client requires both GET and POST / to produce
    # successful, nonempty HTTP/2 responses. A body is irrelevant here; this
    # deliberately exercises the same core dispatch as normal applications.
    app = (core.App().route('GET', '/', lambda _req: 'valiance-h2-ok')
           .route('POST', '/', lambda _req: 'valiance-h2-ok'))
    done = Event()
    def terminate(_sig, _frame):
        done.set()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    server = http1.serve(
        app, host=args.host, port=args.port,
        tls=http1.TLSConfig(str(args.cert), str(args.key)),
        protocols=('HTTP/2',))
    try:
        args.ready_file.write_text(str(server.bound_address[1]) + '\n', encoding='ascii')
        print(f'HTTP/2 fixture ready on {args.host}:{server.bound_address[1]}', flush=True)
        done.wait()
    finally:
        args.ready_file.unlink(missing_ok=True)
        server.shutdown(3)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
