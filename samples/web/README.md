# Run a small `std.web` application

From the repository root, with Python **3.14+** and the project's dependencies
installed:

```shell
uv run vln run --file samples/web/hello_http_websocket.vlnc
```

The server listens on **127.0.0.1:8765** only. In another terminal:

```shell
curl --fail http://127.0.0.1:8765/health
```

Expected response: `ok`. To try the WebSocket route, use a standard WebSocket
client connecting to `ws://127.0.0.1:8765/ws`, send any text message, and
receive `hello from Valiance` followed by a normal close. For example, with
the independent Python `websockets` client installed:

```python
from websockets.sync.client import connect
with connect('ws://127.0.0.1:8765/ws', open_timeout=5) as ws:
    ws.send('hi')
    print(ws.recv())
```

Stop the server with Ctrl+C. The example is **not** a production authentication
or Origin policy: it deliberately exposes no data and binds only to loopback.
For network deployment, use TLS or a properly configured reverse proxy,
authentication middleware and explicit WebSocket Origin rules.

Run the focused, reproducible practical-preview checks from the repository root:

```shell
uv run --with websockets --with wsproto python tools/qualify_std_web_preview.py
```

The command checks live HTTP/WebSocket behavior and selected H1/H2 regressions
with clean subprocess exits. It does **not** sign off HTTP/3, complete RFC
conformance, untrusted internet deployments, or all language/compiler tests.
See [the preview guide](../../docs/std-web-preview.md) for support boundaries.

## Expanded practical API demo

```shell
uv run vln run --file samples/web/feature_demo.vlnc
```

This second loopback-only example serves `GET /health`, `GET /greet?name=Ada`,
`POST /number` (JSON integer), `/cache` (ETag/304), `/redirect` (303),
`/cookie`, `/assets/readme.txt` (static files), and `/ws` (text WebSocket).
It includes gzip compression, an explicit CORS origin policy for
`https://preview.example`, and `withErrorHandler` to customize safe errors.
Example requests:

```shell
curl -i 'http://127.0.0.1:8765/greet?name=Ada'
curl -i -X POST -H 'Content-Type: application/json' --data '7' http://127.0.0.1:8765/number
curl -i -H 'Accept-Encoding: gzip' http://127.0.0.1:8765/assets/readme.txt
curl -i -H 'If-None-Match: "preview-v1"' http://127.0.0.1:8765/cache
```

This is an **illustration**, not a login/authentication implementation.
`tests/test_std_web_preview_smoke.py` starts the compiled example and checks
real HTTP/WebSocket traffic and rejection behavior. For a broader original-plan
feature inventory, see
[`docs/maintenance/web.md`](../../docs/maintenance/web.md).

## Generate HTML with `std.html`

```shell
uv run vln run --file samples/web/html_generation.vlnc
curl -i http://127.0.0.1:8766/
```

The response has `Content-Type: text/html; charset=utf-8` and escaped text
(e.g. `<script>` becomes `&lt;script&gt;`). See
[`docs/std-html.md`](../../docs/std-html.md) for builders, attributes,
composition and security restrictions. Plain string handlers remain text/plain.
