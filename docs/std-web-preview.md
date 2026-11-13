# `std.web` practical preview: what works and what is not certified

`std.web` is the Valiance server-side HTTP/WebSocket framework. The practical
preview is designed for **local applications, prototypes, integration tests,
and controlled deployments** that can tolerate a developing server runtime.
This is **not the normative v1.0 conformance sign-off** and it is **not yet
recommended as an unaudited, directly exposed public-internet server**.

## Start here

Use **Python 3.14 or newer**, the version required in `pyproject.toml`, and
install the declared dependencies. From this source checkout, the documented
project workflow is:

```sh
uv sync
uv run vln run --file samples/web/hello_http_websocket.vlnc
```

Read [the examples](../samples/web/README.md) for HTTP and WebSocket client
commands. The example listens on `127.0.0.1:8765`, responds `ok` on
`GET /health`, and sends one message on `/ws` before closing. It deliberately
does not implement authorization. Stop it with Ctrl+C. Change the port in the
example if it is already occupied.

A dedicated `.github/workflows/std-web-preview.yml` now requests Python
3.14, a locked install, wheel build and the broad `--api-matrix` gate; it has
**not run in this local environment**. The source archive includes `uv.lock`,
but **a fresh Python 3.14 lock/sync was not verified** in the qualification environment. If lock resolution fails,
resolve it on the target Python 3.14 machine and check the package versions;
do not silently replace supported interpreter requirements.

## Broad original-API practical example

For ordinary web-app workflows, run the expanded compiled demo:

```sh
uv run vln run --file samples/web/feature_demo.vlnc
```

It covers JSON POSTs, typed query parameter extraction, redirects, secure cookie
response helpers, conditional ETag caching, static file routing, gzip
compression, CORS preflight/denial, a safe custom app error hook, and WebSocket
traffic. The live test in `tests/test_std_web_preview_smoke.py` exercises both
success and rejection paths. Its presence **does not establish full
implementation of all 38 normative acceptance rows**. See the
[consolidated implementation record](maintenance/web.md)
for explicit partial and missing areas, including HTTP/3, full proxy-trust
qualification and the final typed error/event schema.

New public lifecycle functionality includes `web.serverStats(handle)` and
`web.reloadTLS(handle, tlsConfig)`; TLS credentials are validated before a
context swap for new connections. `web.withLogger` emits redacted route-based
completion events; logger faults do not change the completed HTTP response.
`web.withErrorHandler` and `web.defaultErrorResponse` allow safe custom error
responses on both live and synthetic dispatch paths.

## Deploying H1/H2 with an explicit host/proxy boundary

The public `ServerConfig` now supports `allowedHosts`, `trustedProxies`,
`proxyMode`, `publicOrigin`, and `protocols`. All are validated before the
listener binds. For a TLS-terminating proxy which talks **HTTP/1.1 over
loopback** to the app:

```valiance
import {std.web}
$config = web.ServerConfig(
  host = "127.0.0.1", port = 8080,
  protocols = [web.Protocol.HTTP1],
  allowedHosts = ["example.com"],
  trustedProxies = ["127.0.0.0/8"],
  proxyMode = web.ProxyMode.RFCForwarded,
  publicOrigin = "https://example.com"
)
```

The proxy must **overwrite** incoming `Forwarded` fields with a validated
chain, not append values supplied by Internet clients. Without the explicit
trusted-peer CIDR and selected mode, forwarding headers are ignored even if
they look authoritative. Trusted modes validate hop counts, address syntax,
scheme and authority, and reject mixed `Forwarded`/`X-Forwarded-*` dialects.
An allowlist mismatch returns HTTP 421 before handlers or WebSocket upgrades;
H2 rejects only the affected stream. `request.peer` is the TCP peer, while
`request.clientAddress` is the validated forwarded address if configured. The
same distinction is available in redacted structured completion logs.

`publicOrigin` is validated configuration metadata; this checkpoint does
**not** claim that every URL-building helper automatically uses it. TLS
listeners can select `HTTP1` and `HTTP2`; requesting `HTTP3`, a duplicate
protocol, or cleartext `HTTP2` fails startup rather than downgrading. An
unsupported proxy topology should not be represented as a trusted setup.

## Current useful feature envelope

| Area | Practical-preview behavior | Boundary |
|---|---|---|
| HTTP/1.1 | Routed methods, middleware, request/response bodies, HTTP streaming, uploads and responses | Not all RFC/caching/edge cases independently certified |
| HTTPS / HTTP/2 | TLS 1.3 with ALPN H2, concurrent request streams, flow control and stream-local errors | No cleartext h2c; H2 vendor/proxy and resource acceptance still incomplete |
| WebSockets | H1 Upgrade and H2 extended CONNECT, UTF-8/binary, Ping/Pong, close, subprotocols and opt-in deflate | H3 WebSockets unavailable; interoperability edge cases remain |
| Control/resource policy | Finite listener, message, buffering and shutdown limits | Native TLS/Python allocator allocations not governed by one provable aggregate cap |
| Application safety | Origin validation, explicit middleware hook points, H1/H2 host allowlist and trusted CIDR forwarding configuration | No implicit authentication, CSRF protection, or session service; real production proxy topology still needs its own security review |
| Lifecycle | Cooperative cancellation, bounded transport closure and session-owned task joining | A noncooperative Python/native callback cannot be safely killed in-process |

## Practical ship criteria (separate from the RFC checklist)

A **functional local preview** and a **releasable community preview** are
separate claims. This checkpoint provides the former, not an unconditional
claim of the latter:

| Practical check | Current evidence | Decision |
|---|---|---|
| Run a real Valiance app with a public HTTP route and WebSocket | CLI subprocess test passes with `GET`, `HEAD`, message roundtrip, rejected Origin and graceful stop | Pass on tested Python 3.13 environment |
| Core HTTP routing and H1 adapter | 21 core plus 7 integration tests exit cleanly | Pass in focused matrix |
| Basic TLS/H2 and independent WebSocket interoperability | H2 concurrent GET/WebSocket and two independent-client test cases exit cleanly | Pass in focused matrix |
| Protocol rejection smoke | Seven handshake and eight frame acceptance methods exit cleanly | Pass in focused matrix |
| Supported Python version and dependency installation | Python 3.14+ is required, but unavailable in this test environment; `textual` is also missing here | **Open** |
| Cross-platform installer/wheel and fresh install | Not run as part of this checkpoint | **Open** |
| Unexplained crash and third-party synchronous WSS anomaly | Historical intermittent SIGSEGV and WSS no-header timeout unresolved | **Open** |
| Production browser/proxy/security/load matrix | Not independently qualified for this checkpoint | **Open** |

A community preview can be considered for broader use only after the open
practical checks that apply to its advertised deployment platforms are
verified. Do not downgrade them to success merely because comprehensive RFC
certification is deferred.

## Operational guidance

Keep the default **loopback bind** for development. For an externally
reachable service, put it behind a reviewed TLS-terminating proxy or use a
reviewed TLS deployment, and define allowed hosts/proxy trust explicitly.
Authenticate the user before granting access to private HTTP or WebSocket
routes; **cookies alone are not authorization**. Configure WebSocket Origins
for real browser callers and test both accepted and rejected origins with your
proxy. Explicitly set quotas/timeouts for your workload; keep per-route limits
within the global bounds. Never run untrusted arbitrary Python callbacks in
the server process; if forcible termination is essential, isolate them in
supervised processes outside `std.web`.

Do not promise HTTP/3, complete RFC 9113/6455 conformance, browser/proxy
portability, general zero-leak guarantees or clean full-repository regressions
without separate evidence. The development backlog is tracked by
[the WebSocket acceptance section](maintenance/web.md#h1h2-websocket-rfc-acceptance-and-resource-measurement-matrix)
and [the strict acceptance tool](../tools/qualify_websocket_release.py).

## Two distinct checks

**Practical preview check:**

```sh
uv run --with websockets --with wsproto python tools/qualify_std_web_preview.py
```

The `--with` arguments install independent client libraries used by the test
fixtures; they are not needed to serve ordinary HTTP/WebSockets.

It runs the example as a separate **actual Valiance CLI process**, sends HTTP
and WebSocket traffic, and runs a selected H1/H2 regression matrix. Add
`--api-matrix` for the broader original-plan feature suites and `--extended`
for more transport, resource and security cases. All mandatory
suites must complete with exit code zero and no skips. Test commands that
print `OK` and then hang fail. The JSON distinguishes `functional_passed`
from `preview_ready` (the latter also requires Python 3.14+ and installed
declared runtime dependencies). Developers testing on older Python can use
`--functional-only`; this will **never** change `preview_ready` to true.

**Full WebSocket v1.0 qualification:**

```sh
uv run python tools/qualify_websocket_release.py --inspect
```

The strict qualification requirements stay in force independently, including
external interoperability, whole-repo regression, toolchain and HTTP/3 gates.
Practical-preview success must not be interpreted as that stricter sign-off.

## Programmatic HTML generation

The separate [`std.html` guide](std-html.md) describes typed `html.Node`
construction, nested HTML composition, automatic escaping, and direct route
handler/`web.Response(body = node)` integration. The runnable
`samples/web/html_generation.vlnc` example serves generated HTML on loopback.
This functionality is independent of unfinished HTTP/3 conformance.
