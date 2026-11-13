# `std.web` implementation record

**Updated:** 13 November 2026  
**Current source baseline:** HTTP/1.1 and HTTP/2 WebSockets with the latest recorded qualification evidence  
**Status:** Incremental HTTP/1.1 and TLS ALPN HTTP/2 plus WebSockets over both transports; **not v1.0 conformant or production-ready**.

This is the single record for material produced during the implementation of
`std.web`: milestone notes, feature coverage, protocol completion gates,
qualification evidence, and remaining work. Keep the normative specification,
user guide, and samples separate; keep implementation-process material here.

- [Final normative v1.0 contract](../Valiance-std-web-final-specification-v1.0.md)

The v22 milestone adds HTTPS HTTP/2 with hyper-h2/HPACK, 32 multiplexed streams per connection, independent dispatch, incoming/outgoing flow control, stream resets, trailers, 103 hints, and shared app pipeline. TLS automatically offers `h2` and `http/1.1`; cleartext remains H1 only. The new H2 adapter is `_web_http2.py` and its focused suite is `test_std_web_v22_http2.py`. This is not an exhaustive RFC 9113 conformance or load test, and the existing `uv.lock` needs regeneration under Python 3.14.

The v21 milestone exposes six normative multipart quota knobs on `web.Limits`: `maxFormFields`, `maxMultipartParts`, `maxUploadFileBytes`, `maxMultipartFieldBytes`, `maxMultipartNameBytes`, and `maxMultipartFilenameBytes`. HTTP/1.1 attaches frozen quota snapshots to requests and both buffered/streaming parsers enforce them, including through Valiance handlers. `maxFormFields` remains a multipart-only configurable bound pending the full URL-encoded form policy; file-count/header quotas remain fixed. The v20 milestone delivers direct `bodyMode` / `maxBodyBytes` options on content-bearing route registration. It includes per-route admission checks for both buffered and streamed HTTP/1.1 requests, source cleanup, grouped Router preservation and socket-free behavior. The v19 milestone implemented incremental multipart/form-data field/file streaming for HTTP/1.1, with single-owner uploads and bounded parser memory. Earlier milestones include request/response streaming, trailers, SSE, compression and CORS/TLS 1.3 for HTTP/1.1. See the focused tests and qualification sections below for testing limits.

For multipart parser bounds and ownership, see [`_web_multipart_stream.py`](../../src/valiance/std/_web_multipart_stream.py) and the focused suite [`test_std_web_v21_multipart_limits.py`](../../tests/test_std_web_v21_multipart_limits.py). The shared code is in [`_web_core.py`](../../src/valiance/std/_web_core.py); the HTTP/1.1 admission adapter is [`_web_http1.py`](../../src/valiance/std/_web_http1.py); and the Valiance route modifier bridge and declarations are [`web.py`](../../src/valiance/std/web.py) and [`web.vlnc`](../../src/valiance/std/web.vlnc). Route modifier default binding passes through the general compiler call planner under `valiance.analysis.calls`. The focused direct body-option regression suite is [`test_std_web_route_body_options.py`](../../tests/test_std_web_route_body_options.py).

Do not infer complete RFC 9113, WebSocket, HTTP/3, comprehensive TLS or cross-platform security conformance from the targeted transport tests. Any per-route byte cap must not relax the applicable global limit; the listener validates that invariant before accepting clients.

### v23 — RFC 9113 protocol hardening (not full conformance)

HTTP/2 now reclaims unread upload flow-control credit after early handler return and stream reset; isolates invalid Content-Length at the DATA-frame boundary; prunes fully closed request state; validates HTTP/2 cookie crumbs/authority/trailers; and uses a two-phase GOAWAY with the correct final admitted stream ID. See `tests/test_std_web_v23_http2_rfc.py`. **Upgrading hyper-h2 requires retesting the private `_receive_data_frame` length-isolation hook.** The full RFC 9113 acceptance matrix and Python 3.14 lock verification remain outstanding.

### v24 — HTTP/2 bounded buffered requests and adversarial framing (not full conformance)

Buffered HTTP/2 request octets now share a connection-wide in-flight byte budget matching `Limits.max_buffered_body_bytes`, with 413 stream isolation and reclamation on completion/reset. HPACK decode errors emit `COMPRESSION_ERROR`, nonempty SETTINGS ACK emits `FRAME_SIZE_ERROR`, and oversized frame headers are rejected without waiting for their claimed payload. The RFC-specific integration relies on private hyper-h2 4.3 internals and **requires retesting on dependency updates**. See `tests/test_std_web_v24_http2_acceptance.py` for focused coverage and the HTTP/2 completion gate below for remaining acceptance gaps.

### v25 — server-wide HTTP/2 admission and buffered-body supervision (not full conformance)

The listener now caps active request tasks across HTTP/1.1 and HTTP/2 (`maxConcurrentRequests`, default 1024), buffers HTTP/2 request body octets against an **all-HTTP/2-connections** shared reserve (`maxTotalBufferedBytes`, default 256 MiB), and advertises/configures the per-connection stream cap (`maxConcurrentStreamsPerConnection`, default 100). An explicit per-connection buffered limit is available (`maxBufferedBytesPerConnection`, default 32 MiB), but v24's stricter `maxBufferedBodyBytes` aggregate cap remains in effect. HTTP/2 admission pressure refuses only the new stream with `REFUSED_STREAM`; HTTP/1.1 returns 503. Resetting a stream with a blocked handler **does not release its request-task lease** until that handler exits. The shared body budget does **not** cover HTTP/1.1 buffers, response bytes, HPACK memory, app copies or queues. Complete structured cancellation, memory supervision and exhaustive RFC 9113 checks remain outstanding. See `tests/test_std_web_v25_http2_supervision.py` for eight new live protocol regressions and 123 distinct passing targeted cases.


### v26 — HTTP/2 per-stream inactivity cancellation and bounded listener shutdown

Admitted HTTP/2 streams now enforce `bodyIdleTimeoutMilliseconds` on incomplete request bodies and `writeIdleTimeoutMilliseconds` on response dispatch/producer/output progress. Expired streams use `RST_STREAM(CANCEL)` without terminating their multiplexed peers. The connection idle timer does not interrupt an active admitted stream that is still within its stream-specific watchdog. H2 callbacks and producers now use a bounded daemon worker pool: forced listener shutdown no longer **joins** a non-cooperative callback, but **does not kill it**; admission is held until the actual worker returns, even if the TLS socket has already been unregistered. A queued but unstarted job is cancelled at teardown and queued producer cleanup is best effort. These are inactivity timeouts, not absolute request lifetimes or safe isolation of hostile application code. See `tests/test_std_web_v26_http2_deadlines.py` (9 new cases; 132 distinct passing targeted tests). Full RFC 9113, Python 3.14, lockfile, and full-suite verification are outstanding.

### v27 — malformed-message isolation, HEAD lifecycle and independent HTTP/2 client verification

HTTP/2 now keeps a malformed Content-Length or trailing HEADERS without END_STREAM stream-local (`PROTOCOL_ERROR`) rather than terminating the entire TLS connection. HTTP/2 HEAD responses defer exclusive `StreamBody.close()` onto bounded workers, emit `END_STREAM` without content, and preserve eager cleanup for HTTP/1.1/synthetic dispatch. Invalid HTTP/2 response fields are validated before a streamed producer is transferred and return a safe 500 after source cleanup. TLS ALPN prefers `h2` over `http/1.1` when both are offered; independent curl/nghttp2 GET, binary POST and HEAD requests pass over HTTP/2. See `tests/test_std_web_http2_message_lifecycle.py` (8 new cases; 140 completed targeted passes). **Full RFC 9113 certification, Python 3.14, and the full suite are not complete.**

### WebSocket automatic heartbeat and bounded receive supervision

`_web_ws.WebSocketPolicy` exposes `heartbeat_interval_seconds` (disabled by
0, the default) and `pong_timeout_seconds` (default 10). Valiance exposes
`WebSocketPolicy(heartbeatIntervalSeconds=..., pongTimeoutSeconds=...)`.
Enabling heartbeats starts a dedicated daemon reader for the *owned session*.
It sends an eight-byte unpredictable Ping after inactivity, accepts only the
matching Pong before the monotonic deadline, answers peer Pings, and can close
an unresponsive peer even while the handler is not calling `websocketReceive`.
HTTP/2 heartbeats use a per-stream virtual socket: a timed-out WebSocket does
not close the shared multiplexed TLS connection.

The heartbeat reader is the only RFC 6455 input parser. Application text,
binary and closed events enter a FIFO capped at 32 messages and at most the
policy's `maxMessageBytes` in queued payload bytes; overflow closes that session
with status 1009. The application's `websocketReceive` retains exclusive
single-consumer behavior, including with the background reader enabled.
Control frames do not compete with application readers. Close-frame sends are
serialized under the same send lock as other frames, so concurrent shutdown
and application close cannot emit duplicate closes. When heartbeats are off,
`websocketReceive` preserves its original direct, synchronous parsing path.

These quotas bound *per-session* receive copies, not all application memory and
not a listener-wide sum across WebSocket application queues. A handler that
blocks indefinitely in arbitrary application code is not forcibly terminated.
Focus tests live in [`test_std_web_websocket_heartbeat.py`](../../tests/test_std_web_websocket_heartbeat.py)
and the existing HTTP/1.1/HTTP/2/admission WebSocket regression suites.

### WebSocket upgrade requests and cancellation

Handshake middleware receives the dedicated `Request.WebSocketRequest` variant,
not an ordinary `GetRequest` or a synthetic GET for HTTP/2 CONNECT. Its
`originalMethod` is `GET` on HTTP/1.1 and `CONNECT` on HTTP/2. Route captures,
query, headers, scheme, authority, request ID and the current immutable
request-context mapping survive middleware; attempted request-identity changes
are rejected before handshake commitment. App and grouped middleware execute
once, and their response after-callbacks run before `101`/HTTP/2 `200` headers
are committed. A middleware response may reject the handshake without invoking
the WebSocket handler.

The owned `WebSocket` retains the approved handshake request. Native Valiance
code should call `web.websocketRequest(socket)` to obtain a copyable metadata
snapshot without duplicating the `@nodup` WebSocket owner. Its return type is
`web.WebSocketRequest`; middleware receives `web.Request.WebSocketRequest`.
Direct `$socket.request` field access currently attempts to load a second
occurrence of the owned socket in the VM and is **not** a safe alternative in
compiled Valiance. The native accessor keeps the single-owner invariant.

Aborting a WebSocket signals local cancellation and interrupts a blocked direct
receive. HTTP/1.1 interruption acts only on the upgraded socket's inbound
half; HTTP/2 interruption acts only on the extended CONNECT stream's read
condition. A peer RST_STREAM also presents a cancelled session. These
mechanisms cannot forcibly terminate arbitrary Python/Valiance application
callbacks or cancel their unrelated spawned tasks; complete structured child
scope cancellation remains separate work. Public typed `ContextKey[T]` APIs
and HTTP/3 extended CONNECT are also not yet delivered.

Focused acceptance lives in
[`test_std_web_websocket_request_context.py`](../../tests/test_std_web_websocket_request_context.py)
and the compiled Valiance middleware test in
[`test_std_web_v28_websocket_valiance.py`](../../tests/test_std_web_v28_websocket_valiance.py).


---

## Consolidated implementation-process material

The sections below preserve the feature notes, coverage records, completion gates, and qualification evidence that were previously spread across separate files.


## `std.web` original-plan practical coverage — H1/H2 preview

**Scope:** The frozen `docs/Valiance-std-web-final-specification-v1.0.md` remains the normative authority. This document is an engineering inventory, **not** the §15 acceptance sign-off. It tracks feature usability for local apps and controlled deployments without treating untested behavior as passing. HTTP/3/QUIC is not implemented; its absence remains a clear difference from the original all-transport plan.

**Evidence convention:** `DEMO` = compiled `samples/web/feature_demo.vlnc` tested with a live HTTP/1.1 client and WebSocket; `FOCUSED` = targeted unittest with clean exit; `LEGACY` = earlier test modules in the checkout, not all rerun at this checkpoint; `PARTIAL` = functional implementation but incomplete matrix/contract; `NOT DONE` = absent major transport or confirmed missing work. A named module is not automatically evidence of a current pass.

| §15 | Requirement area | Practical current state and evidence | What remains before exact normative sign-off |
|---:|---|---|---|
| 1 | Language compatibility | PARTIAL: compiled HTTP/WS sample; public `ServerStats` and error hook compiled in normal and optimized/serialized modes (`FOCUSED`) | Full grammar and typed wrapper/signature matrix |
| 2 | Implicit results | PARTIAL: string, response, safe `html.Node` and error conversions exercised by demo, core and `test_std_html.py` | All unusual `Err`/union/implicit branches |
| 3 | Named faults | PARTIAL: app error-hook and fallback behavior tested | Fault provenance, integrity faults and full unwind matrix |
| 4 | Routing precedence | Implemented for common routing (`LEGACY` `test_std_web.py`) | Exhaustive precedence combinations |
| 5 | Route validity | Implemented checks (`LEGACY`) | Comprehensive source-span diagnostics matrix |
| 6 | Router composition | Functional group + staticRouter in `DEMO` | Imported niladic mount and every composition invariant |
| 7 | Paths | Functional strict path guards including traversal denial in `DEMO` | Full Unicode/encoding and proxy differential fuzz |
| 8 | Methods | Functional GET/HEAD/POST/OPTIONS in `DEMO`; other verbs implemented in core (`LEGACY`) | Eight nominal-method end-to-end matrix incl QUERY |
| 9 | HEAD | DEMO HEAD suppression | Explicit-HEAD and streaming length matrix |
| 10 | OPTIONS | DEMO OPTIONS/CORS | All Allow/override/security combinations |
| 11 | Errors | New public `withErrorHandler`, `defaultErrorResponse`; 404/405/Allow in focused Valiance tests | Exact frozen `ErrorContext`/stage/fault type parity and one-shot all-error matrix |
| 12 | Middleware | CORS/compression and error flow in DEMO | All nested before/after fault branches |
| 13 | Method identity | Implemented typed method paths (`LEGACY`) | Full compiled method-identity matrix |
| 14 | Params | Live query retrieval in DEMO; form helpers present | Full duplicate/empty/invalid param tests |
| 15 | JSON | DEMO JSON POST, type and content type rejection | Exhaustive typed and failure mapping acceptance |
| 16 | Cookies | DEMO Set-Cookie | Full cookie prefix/deletion/duplicate/CRLF matrix |
| 17 | Headers | HTTP/1.1 and H2 parser guards implemented (`LEGACY`) | Full smuggling and raw header-byte corpus |
| 18 | Multipart | FOCUSED: 7 `MultipartLimitsCoreTests` pass | Full live multipart/temp-file cleanup matrix |
| 19 | Streaming request | Implemented request body stream paths (`LEGACY`) | Clean-exit, backpressure and cancellation acceptance rerun |
| 20 | Responses | DEMO text/JSON/redirect/cookie/HEAD; generated `html.Node` body and direct handler rendering tested with live H1 | All status/method/stream interactions |
| 21 | Static files | DEMO gzip/static/ETag/path traversal; `LEGACY` v14 suite | Full range/symlink/Unicode matrix |
| 22 | Caching | DEMO ETag/conditional 304/public cache | Full strong/weak precedence and mutation conditions |
| 23 | Compression | DEMO gzip negotiation; library supports gzip/br (`LEGACY`) | Full content negotiation, streaming, and representation identity matrix |
| 24 | SSE | Event stream API implemented (`LEGACY` v16 tests) | Full live browser/intermediary/backpressure qualification |
| 25 | WebSockets | H1/H2 tested, including independent-client, compression and close paths (`LEGACY` + DEMO) | H3 missing; exhaustive RFC/browser/proxy and late-stage reliability sign-off |
| 26 | TLS | New atomic `reloadTLS`, live certificate swap and ALPN preserved (`FOCUSED`) | SNI/mTLS/complete TLS policy matrix |
| 27 | HTTP/1.1 | Live DEMO, routed middleware, WSS paths | Full malformed framing and persistent-stream matrix |
| 28 | HTTP/2 | Live H2 extended-CONNECT and regular H2 cases (`LEGACY`); public protocol selection and H2 host-isolation fixture (`FOCUSED`) | End-to-end H2 proxy/resource conformance |
| 29 | HTTP/3 | **NOT DONE**: QUIC/HTTP3 transport and H3 WebSockets not implemented; explicitly requested H3 now fails startup instead of downgrading | Separate transport project; defer only from *practical H1/H2 preview*, not from frozen v1.0 |
| 30 | Resource budgets | Shared listener/admission quotas, profile tests and snapshots (`LEGACY`) | Universal native/TLS allocator upper bound |
| 31 | Structured concurrency | Cooperative cancellation + bounded teardown (`LEGACY`), **not** forcible Python callback killing | Whole-process/task matrix across all modes |
| 32 | Proxy trust | **Practical H1/H2 implementation:** public host allowlist, validated CIDRs, explicit RFCForwarded/XForwarded modes, peer/client identity, pre-upgrade host validation, chain rejection and stream isolation (`FOCUSED` `test_std_web_network_policy`) | Production reverse-proxy/browser topology, URL generation from `publicOrigin`, Unix-peer handling and wider trust differentials remain unqualified |
| 33 | Lifecycle | `run`/`serve`/shutdown, `serverStats`, `reloadTLS`, and explicit H1/H2 protocol selection with fail-closed unsupported H3 (`FOCUSED`) | Multi-listener/Unix endpoints, rotating cert, signal/platform soak |
| 34 | Dispatch parity | Socket-free dispatcher shares policies and error hooks with live paths (`LEGACY` + FOCUSED) | Synthetic/live differential for all 3 required transports |
| 35 | Multimodule | Basic compiled application and module loader operational | Imported route-builder and ownership compiler matrix |
| 36 | Diagnostics | Safe route-template completion logger with peer/effective-client distinction, and `ServerStats` (`FOCUSED`) | Streaming byte totals and full typed failure-stage/event schema remain partial |
| 37 | Property/fuzz | Several WebSocket fixed-seed / hostile vectors and parser suites (`LEGACY`) | Broad cross-transport differential, fuzz and minimized regressions |
| 38 | Performance | Explicit resource metrics; sampled tests (`LEGACY`) | Deployment throughput/latency, TLS/HPACK allocation and load review |

### Newly implemented public functionality in this checkpoint

- `withErrorHandler(app, callback)` and `defaultErrorResponse(context)` provide a usable app-level response hook with safe fallback. The *current* `ErrorContext` covers request, request ID, stage, name, status, and allowedMethods; it is **not the final exact typed failure/context scheme from the frozen specification**.
- `withLogger(app, callback)` emits a redacted, route-template-based `LogEvent` through the shared dispatch adapter. A callback fault cannot recursively fail a request. Streamed response byte counts are not yet fully metered, and public callback signature compatibility still deserves compiler matrix coverage.
- `serverStats(handle)` exposes a snapshot of transport, connection, websocket and TLS-handshake counters through an owned `ServerHandle` and `ServerStats` value. It is monitoring, **not** a zero-overhead full allocator accounting proof.
- `reloadTLS(handle, config)` validates a replacement TLS config before an atomic swap for **future** handshakes, without modifying established TLS connections. Rejects non-TLS or closed handles.

### H1/H2 networking/security additions

The public `ServerConfig` additionally supports `allowedHosts`, CIDR
`trustedProxies`, `ProxyMode.Disabled`/`RFCForwarded`/`XForwarded`, validated
`publicOrigin`, and explicit `Protocol.HTTP1`/`HTTP2` selection. Unknown or
unavailable protocols (including `HTTP3`) fail startup. Both H1 and H2 check
host policy **before** dispatch and WebSocket handshake commitment; a denied
host yields HTTP 421 without disturbing unrelated H2 streams. Verified
`peer`/`clientAddress` fields are exposed to request handlers and structured
completion events. Core policy, public compiled listener and live TLS/H2 tests
are in `tests/test_std_web_network_policy.py`. This substantially improves
practical proxy security, but is **not** production proxy-topology sign-off.

### Reproduction and release boundaries

```sh
uv run vln run --file samples/web/feature_demo.vlnc
uv run --with websockets --with wsproto python tools/qualify_std_web_preview.py --api-matrix --output std-web-practical-api-evidence.json
```

The second command **fails closed** if any selected suite lacks a clean completed process exit. `--functional-only` is useful on Python 3.13 but does not establish support for the project's required Python 3.14+. See `docs/std-web-preview.md`. Do not market this release as an unaudited public-internet server or as full §15 / H3 conformance.

### `std.html` generation (independent companion module)

The `std.html` module is implemented with `html.Node`, escaped text and
attributes, common element builders, fragments, generic `element`, node
attributes, and secure `render`. Web handlers returning a node and
`web.Response(body = node)` produce HTML with UTF-8 media type.
`tests/test_std_html.py` covers compiled calls and a real H1 response;
see `docs/std-html.md`. This is a practical HTML generation API, not
a complete HTML content-model validator, sanitizer or client-side templater.

## `std.web` HTTP/1.1 resource limits and shutdown (v13)

This guide covers the **implemented HTTP/1.1 transport subset**, not the complete v1.0 multi-protocol server contract.

### Configure limits

```valiance
import {std.web, std.web.App}

$app = App
$app := get("/"): fn(req: web.GetRequest) => "ready" end

$server = web.serve($app, web.ServerConfig(
  port = 8080,
  limits = web.Limits(
    maxBufferedBodyBytes = 1048576,
    maxHeaderFields = 64,
    maxConnections = 32,
    headerTimeoutMilliseconds = 5000,
    bodyIdleTimeoutMilliseconds = 10000,
    connectionIdleTimeoutMilliseconds = 10000,
    gracefulShutdownMilliseconds = 3000
  )
)) ?

$report = web.shutdown $server
```

`Limits` is immutable. Its public fields and defaults are:

| Field | Default | Meaning |
|---|---:|---|
| `maxRequestTargetBytes` | 8192 | Raw request-target bytes |
| `maxHeaderBytes` | 32768 | Header section bytes |
| `maxHeaderFields` | 100 | Maximum number of fields |
| `maxBufferedBodyBytes` | 16777216 | Total buffered request body bytes, including chunked |
| `maxConnections` | 128 | Concurrent admitted transport connections (including TLS handshakes) |
| `headerTimeoutMilliseconds` | 10000 | Header receipt deadline / TLS handshake deadline |
| `bodyIdleTimeoutMilliseconds` | 30000 | Idle deadline while receiving request-body data |
| `connectionIdleTimeoutMilliseconds` | 60000 | Idle HTTP/1.1 keep-alive deadline |
| `writeIdleTimeoutMilliseconds` | 30000 | Send deadline for HTTP responses |
| `gracefulShutdownMilliseconds` | 30000 | Wait for admitted requests during shutdown; zero is allowed |

Invalid values fail startup with a `ServerError` from `web.serve`. Bounds are enforced before allocation or dispatch where possible. The server's default listener host remains `127.0.0.1`.

To use HTTPS and limits together, supply both `tls = web.TLS(...)` and `limits = web.Limits(...)` to `web.ServerConfig`.

### Shutdown

`web.shutdown(server)` consumes the unique `@nodup` handle and uses the configured grace interval. `web.shutdownWithin(server, graceSeconds)` consumes it and overrides that interval **in seconds**. The separately named override is a Valiance overload-resolution workaround; the normative v1.0 signature uses an optional argument on `shutdown` instead.

Shutdown stops admitting new requests, interrupts idle keep-alive connections, lets already-admitted requests finish, and interrupts unfinished socket I/O after grace expires. Responses completed after drain begins carry transport-owned `Connection: close` on HTTP/1.1. The report carries observed accepted, completed, and cancelled request counts and measured shutdown duration. Already-admitted requests for which a complete HTTP error response was sent count as completed, not cancelled.

The TLS handshake runs within the same connection admission budget and timeout as other connections; a stalled handshake cannot block the listener accept loop. Incomplete handshakes are interrupted during shutdown.

**Limits of this subset:** Python user code that never returns or ignores cancellation cannot be forcibly terminated by the threading adapter. The strict bounded cancellation of all application tasks, general WebSocket/SSE stream drain, HTTP/2/3 GOAWAY, cancellation of arbitrary Valiance closures, and the complete v1.0 `ServerStopReport` transport-failure accounting still require a structured-concurrency server runtime. This release makes no claim of those behaviors.

### Verification

The relevant test file is `tests/test_std_web_limits_shutdown.py`. Existing TLS tests additionally validate concurrent slow and healthy handshakes and immediate interruption of a pending handshake.

## `std.web`: opt-in buffered compression

Import the `App` constructor to make its object-friendly route and middleware
registrations available:

```valiance
import {std.web, std.web.App}

$app = App
$app := get("/"): fn(req: web.GetRequest) =>
  "Compressed only when the client permits it and the body is large enough"
end
$app := middleware web.compression(web.CompressionPolicy(minBytes = 32))
web.run($app, web.ServerConfig(port = 8080))
```

You can also register compression on a `Router`, then mount that router as a
group. No compression is performed unless this middleware is registered.

The policy's named fields are `minBytes` (default 1024), `gzipEnabled`
(default true), `brotliEnabled` (default true), and `maxBytes` (default
16777216). Valiance boolean flags use `true`/`false` or `1`/`0`. Invalid
flags, limits, or an unavailable requested Brotli implementation cause a
configuration failure. The build declares `brotli>=1.1,<2`.

The middleware negotiates `Accept-Encoding` with quality values and emits
either `Content-Encoding: br` or `gzip`, using a different strong ETag for the
compressed octets. The final `Vary` includes `Accept-Encoding`. `HEAD`
returns metadata but no body. Explicit byte-range requests stay uncompressed,
and safe conditional GET/HEAD validators compare against the selected coding.

It excludes binary/opaque, already encoded, no-transform, downloadable,
partial, streamed-event, multipart, tiny and oversized bodies. The new
middleware works through the same socket-free, HTTP/1.1 and HTTPS dispatcher.

**Scope:** This implements buffered compression only; owned streaming and
per-stream compression remain future architecture work. The native factory
currently returns an opaque middleware registration value rather than the
fully general callable specified for future `std.web`.

## Incremental HTTP/1.1 request bodies (v17)

Opt-in request streams avoid collecting the request into a single byte buffer.
Use `web.withBodyMode` after registering an ordinary `post`/`put`/`patch`/`query`
route. The default remains `Buffered`.

```valiance
import {std.web, std.web.App}

$app = App
$app := post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $input = web.bodyStream($req) ?
  $first = web.readBodyStream($input, 4096) ?
  web.discardBodyStream($input) ?
  "received"
end
$app = web.withBodyMode($app, web.Method.POST, "/upload", web.BodyMode.Stream) ?
web.run($app, web.ServerConfig(port = 8080,
  limits = web.Limits(maxStreamingBodyBytes = 10485760)))
```

`readBodyStream(stream, maxBytes)` accepts 1..65536, returning bytes, `None`
for EOF or `web.Error`. The stream is exclusive: `bodyStream(request)` may be
called once. `discardBodyStream` consumes the remainder subject to its total
resource limit. Abandoned/incomplete inputs are not replayed as another request.
After a partial read, the transport responds with `Connection: close`; only
streams read through the protocol EOF can be reused on HTTP/1.1 keep-alive.

This is currently an **HTTP/1.1 implementation slice**, not the full v1.0
body-mode registration signature or streaming multipart API. Refer to the v17
status report for normative differences and remaining limitations.

## `std.web` v14 — typed JSON request decoding and response cookies

This document records the v14 implementation increment relative to the normative
`Valiance-std-web-final-specification-v1.0.md`. It does not replace that contract.

### Typed JSON requests

```valiance
import {std.web, std.web.App}

$app = App
$app := post("/count"): fn(req: web.PostRequest) -> String | web.Error =>
  $count = web.bodyJson[Int]($req) ?
  "valid"
end
```

`web.bodyJson[T](request)` invokes the existing `std.json` codec's host decode
entry point with `typeInfo[T].name`; it does not implement a second JSON codec.
POST, PUT, PATCH, and QUERY are supported at runtime. Request media types
`application/json` and `application/*+json` are accepted with UTF-8.
Malformed syntax, invalid UTF-8, and schema mismatch produce typed
`web.Error.InvalidJson` (HTTP 400). Missing/wrong media type produces HTTP
415. The existing bounded request-body rules still apply. Decoding results
participate in normal Valiance `Result` and `?` propagation.

**Remaining constraint:** The compiler currently cannot resolve an
output-generic source wrapper across the four method-specific overloads.
Consequently the public wrapper is generic in its request type and **checks
method capability at runtime**, rather than rejecting GET/HEAD/DELETE/OPTIONS
at registration time. This is *not* full §5.5 static type conformance.

### Response cookie policy

```valiance
import {std.web}

$response = web.withCookie(
  web.Response(body = "ready"), "session", "opaque-value",
  path = "/account", httpOnly = true, secure = true,
  sameSite = web.SameSite.Strict, maxAge = 3600
) ?

$expired = web.deleteCookie($response, "session", path = "/account") ?
```

`web.withCookie(response,name,value,...)` supports the v1.0 named options:
`path` (`/`), `domain` (absent/host-only), `httpOnly` (`true`),
`secure` (`true`), `sameSite` (`web.SameSite.Lax`), `maxAge` (absent),
`expires` (absent Unix seconds), and `partitioned` (`false`). The `SameSite`
variants are `Strict`, `Lax`, and `None`. Independent cookies append separate
`Set-Cookie` field lines, never comma-joining them. Values are validated and
never silently encoded. `__Host-`, `__Secure-`, `__Http-`, partitioned-cookie,
and `SameSite=None` constraints are enforced. `deleteCookie` supports explicit
path, domain, and partition scope and issues both `Max-Age=0` and a past
`Expires` value; it cannot infer a previously issued cookie's scope.

A malformed application-generated response cookie returns a typed
`web.Error.InvalidCookie`; when propagated to the HTTP dispatcher it yields a
safe **500** rather than misidentifying it as malformed client cookie data
(400). Request-cookie parsing and ambiguity behavior remain unchanged.

### Verification and boundaries

New tests live in `tests/test_std_web_v14.py`: typed JSON on four request
methods, typed list decoding, wrong schema and syntax, wrong media type,
HTTP/1.1 400, cookie options and secure defaults, prefix and validation
failures, distinct `Set-Cookie` headers, deletion scope, and correct 500
response-error mapping. Normal and optimized/serialized Valiance execution are
covered. The broader v1.0 transport and ownership requirements are not met by
this release; see this implementation record.

## HTTP/2 completion gate (post-v27 baseline)

**Normative sources:** RFC 9113 (HTTP/2), RFC 7541 (HPACK), and `docs/Valiance-std-web-final-specification-v1.0.md`. **This is a release gate, not a completion certificate.** Keep this single document current rather than declaring each batch of tests a new "finished" milestone.

### Exit criteria

A row is *closed* only when its required implementation, negative-wire acceptance checks, normal-mode tests, and reproducibility evidence all exist. An implemented row with incomplete testing remains **OPEN**. Any **OPEN** mandatory row blocks the claim "RFC 9113 complete, nothing outstanding".

| Gate | Observable acceptance requirement | Current status / evidence | Closure blocker |
|---|---|---|---|
| 1 — TLS & preface | ALPN h2, valid preface/SETTINGS, bad/missing preface, TLS policy | **PARTIAL:** h2/HTTP1 ALPN and new missing-preface timeout wire test pass | TLS matrix, Python 3.14, independent handshake/state fuzz |
| 2 — Frames & SETTINGS | Frame format, continuation, size, error-code matrix, SETTINGS ACK/races, unknown frames, PING | **PARTIAL:** v24 malicious framing tests and post-candidate randomized HEADERS/CONTINUATION vectors pass | Exhaustive frame-state, SETTINGS race and h2spec vectors |
| 3 — HPACK & field budgets | Encoded/decoded limits, dynamic table, invalid HPACK, fragmentation, padding | **PARTIAL:** encoded/decoded/HPACK limits remain enforced; 10 candidate tests plus 175 seeded randomized header-fragment variants pass | Independent HPACK corpus, table-eviction tests, adversarial fuzz |
| 4 — Request message rules | Pseudoheaders, authority, Content-Length, forbidden fields, trailers, CONNECT rejection | **PARTIAL:** v22–v27 message tests plus early-response and multipart HTTP/2 equivalence tests pass | Exhaustive field-character/authority/CONNECT rejection matrix |
| 5 — Multiplexing & flow control | Bidirectional windows, resets, fairness, exhaustion/recovery | **PARTIAL:** v22–v26 flow-control/admission tests plus repeatable large-upload, early-response and stalled-outbound tests pass | Soak, randomized stream resets, protocol-level priority/fairness checks |
| 6 — Response framing | Bodyless HEAD/204/304, informational responses, trailers, writer cancellation | **PARTIAL:** targeted v22/v27 tests and post-candidate buffered response-budget tests pass | Exhaustive status/header/trailer/length matrix and failure injection |
| 7 — GOAWAY, cancellation & shutdown | Correct retry-safe stream accounting, graceful drain, bounded resource release | **OPEN:** post-candidate early-response state leak and flushed-frame accounting fixed; three combined 81-test HTTP/2 runs exited cleanly | Uncooperative application code cannot be forcibly stopped; comprehensive race testing absent |
| 8 — Security and resource supervision | Per-connection/server budgets, compressed field and HPACK caps, timeouts | **PARTIAL:** covers inbound buffers, buffered outbound responses, compressed HPACK, absolute headers/preface timeout, cancellation reclamation | StreamBody producer queues now counted, but a single local in-progress producer read and application/VM allocations are not globally accounted, concurrent attacker soak missing |
| 9 — Cross-version semantics & full regression | Consistent HTTP/1.1 + HTTP/2 behavior, all repository tests | **OPEN:** all 81 distinct HTTP/2 tests passed three combined runs with clean exits; 78 selected HTTP/1.1 regressions passed | Full repository suite fails outside HTTP/2 and timed out; process-teardown flakiness was not reproduced in three clean runs but is not proved absent |
| 10 — Reproducible release qualification | Python >=3.14, regenerated lockfile, multiple independent clients, h2spec/fuzz | **OPEN:** Python 3.13.5, hyper-h2 4.3.0, independent curl/nghttp2 GET/POST verified | Python 3.14 unavailable, lockfile not regenerated, h2spec absent, Go net/http 120-request concurrent independent TLS matrix now passes; h2spec and broader RFC 9113 negative-wire matrix remain missing |

**Scope boundary:** RFC 9113 does not itself mandate implementation of WebSockets (RFC 8441), HTTP/3, or optional extensions. However, `std.web` v1.0 requires the extended-CONNECT/WebSocket API separately. Do not confuse HTTP/2 protocol compliance with full `std.web` compliance.

### New candidate controls

- `web.Limits(maxCompressedHeaderBytes = 32768)` sets an inbound HPACK-encoded field-section byte ceiling. It is measured across HEADERS and CONTINUATION frames and excludes HEADERS padding and priority metadata. Over-limit sections terminate the connection with `ENHANCE_YOUR_CALM` *before* HPACK decompression.
- `web.Limits(maxDecodedHeaderBytes = 65536)` caps HPACK decoded header-list bytes. The existing `maxHeaderBytes = 32768` remains a separate, stricter default; **both** apply.
- `web.Limits(maxHpackDynamicTableBytes = 16384)` is a hard cap; the server negotiates `min(4096, configured cap)` by default, so it does not inflate the normal 4 KiB decoder table without necessity. A stricter configured cap is enforced before SETTINGS acknowledgment.
- `headerTimeoutMilliseconds`/`header_timeout_seconds` also bounds total time to complete the H2 connection preface and each HPACK field section. A trickle of bytes does not refresh this deadline.

### Finish-line engineering evidence (12 November 2026)

- **Fixed:** a server reply before request-upload EOF now completes the final response, uses the permitted `RST_STREAM(NO_ERROR)` to stop unused bytes, and prunes state on both application and pre-admission rejection paths. Live tests exercised 151 sequential early-response streams, rejected uploads, keep-alive reuse, and idle-preface shutdown.
- **Fixed:** buffered outgoing HTTP/2 response bytes now reserve the shared connection/listener memory budget, remain accounted while the client holds the DATA flow-control window closed, and release after the final TLS send or cancellation. An over-budget response is replaced by a 503 before headers are committed. This is not total memory accounting for arbitrary application code or streaming producer buffers.
- **Verified:** live binary multi-part uploads, repeated fields, quota rejection with subsequent stream survival, plus **175 seeded HPACK field-block/fragmentation variations**.
- **Verified:** 81 HTTP/2 tests completed together in **three independent runs**, all exit 0; 78 distinct targeted HTTP/1.1 tests each completed cleanly. Independent curl/nghttp2 negotiated actual HTTP/2 for GET/POST against a new real TLS fixture.
- **Not verified:** RFC 9113 full matrix, h2spec, required Python >=3.14, full repository suite, sustained hostile-concurrency memory/termination guarantees. The repository smoke test reached 38% with unrelated compiler failures and eventually timed out. An isolated unrelated compiler test failed (`tests/test_analysis_calls.py::CallPlanningBoundaryTests::test_repeated_higher_order_overloads_share_contextual_analysis`, expected 1 shared computation vs 8).
- **Reproducibility:** `scripts/qualify_http2_release.sh` is a **fail-closed qualification entrypoint**. It requires Python 3.14, validates lockfile, executes tests and strict independent-client acceptance. It currently exits blocked on missing Python 3.14; it does not certify this release candidate.

### Validation rules

1. Run all eleven HTTP/2 test modules as **separate completed commands** and record their exit codes; also run a combined command and investigate post-summary hangs.
2. Run `curl --http2` (nghttp2), h2spec, a second implementation, and both ordinary and adversarial clients against the exact candidate. Record executable versions.
3. Run the full repository suite under the declared Python 3.14 target and regenerate/verify the dependency lockfile. Do not accept Python 3.13 successes as proof.
4. Use sustained fuzzing/concurrent cancellation tests to verify memory ceilings, idle/preface deadlines, no deadlocks, and process termination.
5. Only replace every **OPEN/PARTIAL** mandatory row with **CLOSED** on an evidence-backed release; publish the final compliance claim *then*, not before.

### Post-finish-line unblocking checkpoint (12 November 2026)

- **Streaming response producer queues now have listener-wide and per-connection byte leases.** Each StreamBody read is bounded by the configured budgets. Reserved queue bytes remain charged while HTTP/2 DATA is blocked by the peer and until TLS `sendall` has completed. Reset, cancellation, and TLS disconnect return reservations. Three live TLS tests cover connection-level and cross-connection contention plus recovery; all three fail against the unmodified finish-line baseline but pass after the fix. An in-progress producer read remains bounded by 16 KiB per worker; arbitrary application/VM memory is not included.
- **Repeatable local engineering check:** `bash scripts/verify_http2_transport.sh` passes 84 HTTP/2 tests with clean exit; curl/nghttp2 GET/POST negotiated actual HTTP/2; an independent Go `net/http` client completed 120 parallel GET/POST operations with binary uploads. This script intentionally declares itself *not* an RFC 9113 certification. `scripts/qualify_http2_release.sh` remains fail-closed and now includes the new test module and Go client.
- **HTTP/1.1 compatibility:** all 78 v15–v21 targeted cases passed in seven separately exited test processes. Combined with the 84 HTTP/2 cases, 162 distinct transport regressions pass.
- **Compiler/documentation regressions addressed:** a call-local contextual-callback cache avoids eight redundant overload analyses; 42 call-planning tests pass. Standard-library reference extraction retains variant-specific overload documentation, and `web.bodyJson` gains a documented public example. The two previously failing focused documentation checks pass.
- **Remaining external/conformance blockers:** h2spec cannot be installed in this network-restricted environment and therefore its strict suite has NOT run; full RFC 9113 negative-wire qualification remains open. The required Python 3.14 / dependency-lock checks are explicitly deferred for the user's environment.
- **Full-repository status:** test collection reached 4,175 cases with one missing `textual` import in `test_terminal_editor.py`. Excluding that unavailable dependency, the latest first-failure run reached 1,150 passes before a separate production-docstring quality test failed on hundreds of pre-existing undocumented Python functions. This is not evidence of an HTTP/2 protocol failure; it is an unresolved package-wide release gate.

The HTTP/2 engineering suite is green, but **formal HTTP/2 conformance certification remains OPEN**. Neither the availability of Go/curl nor these specific resource fixes is a substitute for strict h2spec and complete release qualification.

## WebSocket session shutdown and callback lifetime

### Ownership boundary

A WebSocket session owns its frame parser, heartbeat reader, bounded incoming and outgoing queues, shared budget reservations and explicit Python `WebSocket.spawn_child` threads. A native Valiance callback runs in the VM's structured task scope, which receives the session's cancellation event and checks at scheduler/bytecode checkpoints. Descendant Valiance tasks are cancelled and joined by their parent scope.

CPython does **not** provide safe forced thread termination for arbitrary code. Foreign native calls or user callbacks that never observe cancellation may remain alive after a server stop. A WebSocket owner must not report that such code was terminated or that it was moved into process isolation. The callback retains its request/WebSocket admission lease until it actually exits; premature lease release would misstate active work. A separate OS worker-process model could provide a stronger kill boundary, but is **not** required by the current WebSocket H1/H2 cooperative contract.

### Shutdown and accounting

1. Mark the listener draining, reject new admissions and wait for live sessions up to the configured grace deadline.
2. On deadline expiry, signal session cancellation, use nonblocking socket abort, `shutdown()` and `close()` the accepted transport descriptors. This is independent of Python callback termination.
3. Join the heartbeat reader and explicitly registered Python child threads with **one shared bounded deadline**, avoiding joins from the current child thread or while holding WebSocket transport locks. H2 connection cleanup releases buffered TLS/flow-control byte reservations after the socket is interrupted.
4. Preserve a weak registry of previously unregistered owners whose explicitly owned Python children have not yet exited, so shutdown can reap or report them. Do not retain completed session objects indefinitely.
5. Report live callbacks independently of admission leases: H2 may keep a WebSocket admission lease while outgoing DATA is awaiting flush after the callback has returned.

`ServerHandle.stop_diagnostics` uses `cancelled` for active owners sent cancellation (not successful kills), `timed_out` for callback workers executing at the grace deadline, `isolated=0`, `unjoined_owned_tasks` for surviving explicit session-owned workers, `transport_sockets_open` for still-open transport FDs and `transport_buffered_bytes` for outstanding listener shared-byte reservations. The consumed Valiance `ServerStopReport` exposes the corresponding `webSocketCancellationSignals`, `webSocketCallbacksTimedOut`, `webSocketOwnedTasksUnjoined`, `webSocketTransportSocketsOpen` and `webSocketTransportBytesRemaining` integer fields. These are snapshot diagnostics; a blocked callback may subsequently return and release its admission lease.

This contract does not guarantee that a deliberately noncooperative child thread is joined or killed. It guarantees an explicit cancellation signal, a bounded join attempt, and truthful reporting of failure to cooperate. Application allocations outside server-owned transport budgets are not covered.

### Qualification evidence

`tests/test_std_web_ws04_ws05_cancellation.py` exercises H1/H2 shutdown and peer reset with cooperative native children, bytecode cancellation and an intentionally blocked native callback on HTTP/2. `tests/test_std_web_ws08_ws11_resource_gates.py` tests bounded H1 teardown and transport byte/FD reclamation with an intentionally blocked Python callback. `tests/test_std_web_limits_shutdown.py` exercises compiled Valiance `ServerStopReport` construction and fields. The checkpoint status file has the completed commands, exit codes and Python environment.

#### Qualification boundary note

Independent release acceptance does not replace cooperative session supervision.
WS-05 remains defined by bounded socket/transport teardown, signalling and joining
cooperative session-owned workers, and truthful remaining-callback counters.
Neither the WebSocket RFC acceptance suite nor the release qualification runner
may infer arbitrary Python/native callback termination from a returned shutdown.

The H1/H2 listener also tracks the `ThreadingMixIn` transport workers and
joins cooperative workers with **one aggregate 200 ms deadline** after listener
close. A blocked callback still cannot be killed; its transport thread remains
counted in `resource_snapshot()['active_connection_workers']` until it exits,
while its socket and budget can already have been torn down. A separate
`unjoined_owned_tasks` count continues to describe the WebSocket owner's own
registered children rather than transport threads. This distinction avoids
claiming that a timeout has terminated application code.

## Valiance `std.web` — definitive WebSocket completion checklist



**Frozen checklist:** 13 November 2026  
**Source baseline:** `Valiance-std-web-websocket-typed-context-memory-milestone.zip`  
**Normative authority:** `docs/Valiance-std-web-final-specification-v1.0.md`, especially §§5.1, 5.4, 6, 7.5, 11, 12.4, 12.7, 13, 15, 17.1–17.3.  
**Protocol authorities:** [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html), [RFC 8441](https://www.rfc-editor.org/rfc/rfc8441.html), [RFC 9220](https://www.rfc-editor.org/rfc/rfc9220.html).  
**WS-01–WS-07 updated status (13 November 2026):** WS-01, WS-02, WS-03, WS-04, WS-06, and WS-07 are checked with implementation and clean-exit targeted test evidence. **WS-05 CLOSED for H1/H2 under the explicitly amended cooperative-cancellation contract** (bounded transport teardown, joining cooperative session-owned work and truthful noncooperative callback diagnostics). Process isolation is optional and not required for this gate; see the amendment log below. WebSocket-over-HTTP/1.1 and HTTP/2 is implemented and has targeted passing tests. **Neither transport's full WebSocket acceptance gate nor the complete H1/H2/H3 WebSocket v1.0 contract has been signed off.**  

### Status and finish-line rules

- `[x]` means implementation/testing is reported as completed in the **baseline**; it is **not** a fresh rerun for this checklist.
- `[ ]` means the release gate is open. **Unknown/unverified is not passed.**
- `BLOCKED — H3` means the item requires the separate QUIC/HTTP/3 transport. It is **not** a reason to continually defer H1/H2 completion.
- The full WebSocket section is **DONE only if WS-01–WS-23 are checked, including H3,** and the final sign-off is recorded. The useful earlier milestone **“WebSocket H1/H2 complete”** may be declared once WS-01–WS-20 pass, with H3 explicitly excluded.
- Do not add new work simply because a version number advances. A new checklist item requires a **specific violated paragraph** of the frozen normative spec/RFC, a reproduced issue, or a changed upstream requirement. Record justification and evidence in the change log at the bottom.
- No checkbox may be closed based solely on source review, previous test counts, or a test command that printed `OK` but failed to exit. Record a reproducible successful test, its exit status, and affected transports.

### Baseline functionality — implemented, retain regressions

- [x] **B-01 — HTTP/1.1 `ws://`/`wss://` upgrade.** Authenticated handshake path, Origin policy, optional subprotocols, route coexistence, middleware rejection, and compiled Valiance handler. Evidence: `tests/test_std_web_v28_websocket_h1.py`, `tests/test_std_web_v28_websocket_valiance.py`.
- [x] **B-02 — HTTP/2 extended CONNECT.** SETTINGS negotiation, per-stream WebSocket framing, HTTP/2 flow control, stream-local cancellation, and coexistence with HTTP requests. Evidence: `tests/test_std_web_v29_websocket_h2.py`.
- [x] **B-03 — Core RFC 6455 message primitives.** Text/binary, masked input, fragmentation, Ping/Pong, close, UTF-8 validation, oversize rejection, one active reader. Evidence: H1/H2 and `tests/test_std_web_v30_websocket_single_reader.py`.
- [x] **B-04 — Session supervision.** Cross-protocol `maxActiveWebSockets` admission, heartbeat/Pong deadline, bounded pending heartbeat-reader FIFO, idempotent close, reader wake-up. Evidence: `tests/test_std_web_v30_websocket_admission.py`, `tests/test_std_web_websocket_heartbeat.py`, `tests/test_std_web_websocket_request_context.py`.
- [x] **B-05 — Handshake request metadata and typed context.** `WebSocketRequest` variant, middleware snapshot, `web.websocketRequest(socket)` accessor, `web.contextKey[T]`/context/requireContext, keyed identity and type checking, shared heartbeat queue accounting. Evidence: `tests/test_std_web_websocket_request_context.py`, `tests/test_std_web_websocket_typed_context_memory.py`.

Baseline report records **67 distinct targeted passing cases** across selected suites. This number is historical evidence, **not** full RFC conformance. An earlier combined runner sometimes stalled after printing success; clean-exit qualification remains open.

### Resolved API/default gaps in WS-01–WS-07 implementation

- **WS-01 closed:** `web.ContextKey[T]` is the canonical authenticated unique-key constructor; `web.contextKey[T]` remains a legacy alias. Same-type keys stay distinct, invalid values are rejected during analysis and attempts to supply forged constructor parameters fail compilation.
- **WS-02 closed:** direct `socket.request.originalMethod` and ordinary immutable `@nodup` member reads work without duplicating the owner. Explicit owner copying is rejected at compile time. Compiler projection provenance is distinct from an actual copied owner.
- **WS-03 closed (public surface and supported modes):** `WebSocketPolicy.maxMessageBytes` defaults to 8 MiB, heartbeat defaults to 30 seconds, global frame/message and heartbeat timing limits are exposed through `web.Limits`, and the stricter applicable limits are enforced. `extensionPolicy` defaults to `Disabled`; explicit unsupported extension settings fail with `ConfigError` instead of silently enabling compression. **Extension implementation is tracked under WS-11 (now closed for H1/H2)**, not part of WS-03's signature gate.
- **WS-08 closed in the resource checkpoint:** the normative `maxWebSocketQueuedSendBytes` 4 MiB setting is implemented and independently enforced in H1/H2; inbound/parser and remaining memory supervision is tracked under WS-09–WS-10.

### Work package A — exact public API and owned access (do first)

- [x] **WS-01 — Canonical `web.ContextKey[T]` factory.** Fix the general Valiance generic constructor/overload binding so the **specified spelling** mints a unique opaque key, with identical type safety/identity to the current `web.contextKey[T]`. **Pass:** compile and execute exact normative examples, including two keys of one type, wrong-type compile failure, a forged token failure, and middleware-to-WebSocket lookup on both transports. Keep legacy alias only if compatible.
- [x] **WS-02 — Borrow-aware `WebSocket.request` member access.** Reading immutable metadata must not copy/consume a `@nodup` socket. Correct the compiler/runtime's **general borrow rule**, not just WebSocket-specific ownership. **Pass:** compiled expressions such as `socket.request.originalMethod` work, an attempted socket copy fails statically, and `web.websocketRequest(socket)` remains safe. Add ordinary non-WebSocket `@nodup` regression cases.
- [x] **WS-03 — Public WebSocket signature and policy parity.** Diff each public declaration in `src/valiance/std/web.vlnc` and native bridge against §§11.1–11.3, 17.1–17.3: `websocket(App|Router, path, handler, policy)`, `WebSocketRequest`, `WebSocketMessage`, `WebSocketPolicy` (including required/optional settings), send/receive/ping/close return errors, and numeric defaults. **Pass:** an executable symbol/signature matrix and compile-failure tests for unsupported argument types; no undocumented mismatches or silent aliases.

### Work package B — structured lifetime and cancellation

- [x] **WS-04 — Parent-owned child-task supervision.** All WebSocket-spawned tasks/readers/producers must be registered under the session parent, not detached. **Pass:** normal return, handler `Err`, catchable panic, TCP disconnect, H2 `RST_STREAM`, heartbeat timeout and server shutdown each cancel and join **cooperative** child tasks, with zero surviving tracked tasks and exactly-once cleanup.
- [x] **WS-05 — Cooperative cancellation and bounded transport shutdown (amended for H1/H2).** Signal Valiance bytecode scopes, heartbeat readers and explicitly session-owned Python children on session termination, and join cooperative tasks with a bounded deadline. On grace expiry, close server-owned TCP/TLS sockets and reclaim shared transport reservations, even if an unrelated Python/native callback refuses to return. **Pass:** H1 and H2 cancellation and shutdown fixtures show cooperative child termination/join, no live transport descriptors or shared buffered-byte reservations after forced shutdown, and public `ServerStopReport` fields truthfully distinguish cancellation signals, callbacks active at the grace deadline, and unjoined session-owned threads. A callback that ignores cancellation may remain runnable and continues to own its admission lease until it exits. **No arbitrary Python thread termination or process isolation is promised.** This is a recorded user-approved change to the prior kill/isolation requirement; see the dated evidence entry below.
- [x] **WS-06 — Handler fault and ownership semantics.** After a handshake commits, handler `Err`/panic/throw may close/reset the WebSocket but never issue an HTTP error response. Precommit middleware and after-callback failures must reject safely and run exactly once. **Pass:** fault-injection matrix for both H1/H2, single close/reset, redacted logs, no handshake double-commit, no resource leak.
- [x] **WS-07 — Concurrent close/send/receive race safety.** Guarantee one active receiver, serialized sends, a single close transition and bounded unblock of blocked send/receive after peer close, cancellation, and shutdown. **Pass:** deterministic barriers plus randomized stress at send-vs-close, heartbeat-vs-close, reset-vs-write and shutdown-vs-handler boundaries on H1/H2, with clean process exit.

#### WS-04 / WS-05 cooperative lifetime boundary

- **WS-04 CLOSED (cooperative boundary):** the WebSocket owner termination event now reaches the running Valiance callback scope through bounded VM bytecode cancellation checkpoints. H1 peer close, H1 TCP disconnect, H1 listener shutdown, heartbeat timeout, and H2 `RST_STREAM` were exercised with an intentionally non-returning bytecode handler, and direct/nested child tasks were cancelled and joined. Normal callback return and panic remain covered. A cooperative Python `spawn_child` additionally observes the session cancellation event and joins. Arbitrary Python/native callback termination is outside WS-05's amended contract. Evidence: `tests/test_std_web_ws04_ws05_cancellation.py`.
- **WS-05 CLOSED for H1/H2 under the amended contract:** after the grace deadline, forced teardown requests WebSocket cancellation, interrupts and closes accepted TCP/TLS socket descriptors, joins cooperative session-owned heartbeat/Python tasks within an aggregate deadline and observes HTTP/2 buffer cleanup. Both Python `ServerHandle.stop_diagnostics` and public Valiance `ServerStopReport` expose outstanding native callback and unjoined owned-task counts. The intentionally uncooperative callback remains runnable and retains its truthful admission lease until it exits; it cannot retain an open server-owned socket. A noncooperative explicit Python child is reported as unjoined rather than falsely terminated. Stronger isolation is a separate execution boundary, not a prerequisite.

### Work package C — bounded memory, flow control and extension contract

- [x] **WS-08 — Outbound queue budget.** Enforce `maxWebSocketQueuedSendBytes` (normative 4 MiB default) and shared/global constraints before outbound frame serialization or queue insertion; apply H1/H2 backpressure and write-idle deadlines. **Pass:** slow-reader and closed-window tests across multiple clients prove finite allocations and that reservations are released on flush, reset, abort and disconnect.
- [ ] **WS-09 — Inbound budgets for every mode.** Account for in-flight fragmented messages, frame staging, and pending messages with **heartbeat on and off**. Enforce global, per-session and `maxWebSocketMessagesBytes` limits before unbounded accumulation. **Pass:** byte-by-byte frames, large fragmented messages and many idle clients cannot exceed measured caps; consumption, close and error release reservations exactly once.
- [ ] **WS-10 — Transport and task resource ownership.** Bound server-owned TLS/HTTP/2 buffering, admission slots and background task queues that WebSockets can influence; distinguish externally owned application allocations from enforceable server budgets. **Pass:** peak-memory/queue instrumentation and leak baselines over repeated connect/send/reset/close cycles (H1 and H2), including backpressured peers.
- [x] **WS-11 — Extension policy and compression (H1/H2).** Document and enforce the exact normative `WebSocketPolicy` extension behavior. Unknown/unapproved extensions must never be negotiated; RSV bits must be rejected unless a negotiated extension defines them. **If explicit permessage-deflate support is required by the frozen contract, implement its negotiation and bounded decompression/ratio controls; otherwise record a precise normative justification for a ConfigError on unsupported enablement.** **Pass:** hostile extension/parameter and compression-bomb vectors with bounded memory.

### Work package D — RFC protocol acceptance, security and parity

- [ ] **WS-12 — RFC 6455 handshake exhaustive matrix (H1).** Versions/keys, duplicate fields, token/case rules, invalid Host/Origin, proxy-effective host, request bodies, extension/subprotocol offers, 400/403/426 behavior, and no malformed-upgrade fallback to a GET handler. **Pass:** wire fixtures for all relevant RFC/spec validation branches; no response commitment before approval.
- [ ] **WS-13 — RFC 6455 frame/close exhaustive matrix (H1 + H2).** Mask bit, lengths (including nonminimal/64-bit invalid values), reserved bits/opcodes, control frame fragmentation/size, interleaved continuations, incremental UTF-8, Ping/Pong, close-code legality/reason, peer-close response and TCP/stream termination. **Pass:** positive, negative, split-boundary and randomized vectors on both transports with correct close code and no cross-stream failure.
- [ ] **WS-14 — RFC 8441 HTTP/2 stream-specific acceptance.** SETTINGS_ENABLE_CONNECT_PROTOCOL gating, `:protocol`/`:method`/`:scheme`/`:authority`/`:path`, no 101/Upgrade behavior, header/trailer rejection, stream reset, flow control, GOAWAY, and coexistence with ordinary H2 work. **Pass:** malformed variants are stream- or connection-scoped only as required by RFC 8441/9113; accepted requests return the correct success status and keep other streams healthy.
- [ ] **WS-15 — Browser and deployment security.** Browser same-origin defaults; explicit cross-origin allowlists; missing/`null`/opaque Origin behavior; cookies are not authentication; middleware auth; forwarded headers only via trusted proxy policy; Host and scheme integrity. **Pass:** real-browser and actual reverse-proxy TLS tests for cross-site WebSocket hijacking and accepted/rejected origins, including H1 and H2 as supported by clients/proxies.
- [ ] **WS-16 — Fuzz/property and fault injection.** Run reproducible seed corpora against handshake parsing, frame fragmentation, invalid UTF-8, arbitrary close races, partial writes, transport disconnects and cancellation. **Pass:** no hangs, runaway memory, uncaught worker exceptions, thread leaks, accidental HTTP downgrade or non-deterministic protocol corruption. Save the minimized failing vector with each fix.

### Work package E — independent qualification and clean release gate

- [ ] **WS-17 — Independent implementations and interop.** Use at least **two independently implemented** WebSocket clients per available transport where practical, including real browser H1, and reverse-proxy/tunnel fixtures. **Pass:** automated text/binary, multi-frame, heartbeat, close, Origin, subprotocol and reset cases with independent implementations (not merely two Python wrappers over the same engine).
- [ ] **WS-18 — Longevity/load/memory/shutdown matrix.** Concurrent mixed H1/H2 WebSockets + HTTP requests, connection churn, slow readers, peer loss, server shutdown while handlers are stalled, and quota recovery. **Pass:** documented deterministic stress profile (including randomized schedules) exits cleanly without growing session/task/byte counters; repeat at least three times.
- [ ] **WS-19 — Regression closure.** Re-run all existing WebSocket test modules, relevant HTTP/1.1/H2 flows, public compiler/`@nodup` tests, middleware/auth tests and whole-repository suites affected by changes. **Pass:** every required command exits code 0, no timed-out suite counted green, known unrelated failures filed distinctly rather than hidden.
- [ ] **WS-20 — Supported toolchain, reproducibility and evidence.** Under required Python 3.14+ and pinned dependency lock, run required builds and tests; capture client versions, environment, commands, exit codes and logs. **Pass:** a single automated `qualify_websocket_release` script exits nonzero for missing/skipped mandatory tests and publishes a checked requirements-to-tests matrix. **Python 3.14 is explicitly delegated to the user for now: this item remains OPEN, not waived.**

### Work package F — HTTP/3 dependency (blocked on QUIC/HTTP/3)

- [ ] **WS-21 — HTTP/3 WebSocket handshake.** **BLOCKED — H3.** Once the QUIC/H3 transport is implemented, add RFC 9220 extended CONNECT with the required HTTP/3 setting/negotiation, proper `:protocol` semantics, policy/middleware parity, stream ownership, and no H1/H2 upgrade hack. **Pass:** accepted and rejected H3 handshake fixture suite, including malformed SETTINGS/headers and proper status/error handling.
- [ ] **WS-22 — H3 WebSocket lifecycle and security parity.** **BLOCKED — H3.** Reuse the same Valiance WebSocket handler/policy and enforce the same message, admission, Origin, subprotocol, buffering, cancellation and shutdown guarantees under QUIC flow control. **Pass:** live H3 interoperability, reset/cancel, loss/idle timeout, graceful shutdown, and cross-protocol equivalence tests.

### Final sign-off

- [ ] **WS-23 — WebSocket v1.0 sign-off.** **Pass only when WS-01 through WS-22 are all checked,** the normative public surface is exact, all required RFC acceptance vectors and independent-client tests pass, and the evidence bundle (versions, commands, exit codes, stress metrics, known limitations) is reproducible. Missing transport or unsupported mandatory feature = not complete. Document any deliberate contract amendment explicitly rather than silently changing this checklist.

### Practical completion checkpoints

| Checkpoint | Required items | Allowed outcome |
|---|---|---|
| **H1/H2 feature completion** | WS-01–WS-11 | All public API/lifetime/budget gaps closed; protocol qualification still pending |
| **H1/H2 WebSocket release candidate** | WS-01–WS-19 | Wire/security/interop/regressions green on existing transports; toolchain gate still open |
| **H1/H2 WebSocket qualified** | WS-01–WS-20 | Can call **H1/H2 WebSockets complete** while clearly excluding RFC 9220/H3 |
| **Full `std.web` WebSocket v1.0** | WS-01–WS-23 | H1 + H2 + H3; no open normative WebSocket gates |

### Focused starting paths

- `src/valiance/std/web.vlnc` — public declarations, generic typed-context factory, owned fields.
- `src/valiance/std/web.py` — Valiance/native bridge.
- `src/valiance/std/_web_ws.py` — shared RFC 6455 parser, policy, heartbeat and cancellation owner.
- `src/valiance/std/_web_http1.py` — HTTP/1.1 upgrade lifecycle and transport.
- `src/valiance/std/_web_http2.py` — RFC 8441/flow control, stream cancellation.
- `src/valiance/std/_web_core.py` — routing/middleware, request context, shared budgets.
- `tests/test_std_web_v28_websocket_h1.py`, `tests/test_std_web_v28_websocket_valiance.py`, `tests/test_std_web_v29_websocket_h2.py`, `tests/test_std_web_v30_websocket_admission.py`, `tests/test_std_web_v30_websocket_single_reader.py`, `tests/test_std_web_websocket_heartbeat.py`, `tests/test_std_web_websocket_request_context.py`, `tests/test_std_web_websocket_typed_context_memory.py` — existing test baseline.

### Evidence log / changes to the frozen checklist

Record each completion as:

`ID | commit/archive | implementation files | test files/cases | command + exit code | environment | reviewer/date`

**WS-01 | WS-01–07 source archive |** `web.vlnc`, `web.py` | `test_std_web_websocket_ws01_ws07.py` (canonical keys, forgery, typed rejection, H2 middleware) plus `test_std_web_websocket_typed_context_memory.py` | separate completed unittest runs, exit 0 | Python 3.13.5, 2026-11-13.

**WS-02 | WS-01–07 source archive |** `runtime/compiler.py` | new Valiance H1 normal/serialized and H2 tests, ordinary `@nodup` static cases | exit 0 | Python 3.13.5, 2026-11-13.

**WS-03 | WS-01–07 source archive |** `web.vlnc`, `web.py`, `_web_ws.py`, `_web_http1.py` | default/signature/type tests, public compilation and H1/H2 regressions | exit 0 | Python 3.13.5, 2026-11-13. Extension enablement remains unsupported (WS-11).

**WS-06 | WS-01–07 source archive |** `_web_http1.py`, `_web_http2.py` | post-commit 1011/no 500/redacted error, H1/H2 precommit middleware and context suites | exit 0 | Python 3.13.5, 2026-11-13.

**WS-07 | WS-01–07 source archive |** `_web_ws.py`, `_web_http2.py`, `_web_http1.py` | deterministic blocked-writer, waiting sender, 40-seed H1 write/close/abort, six H2 reset-vs-blocked-write cycles, single-reader and heartbeat suites | exit 0 | Python 3.13.5, 2026-11-13.

**WS-04 historical partial / WS-05 open (previous baseline) |** `runtime/vm.py`, `elements/builtins.py`, `_web_ws.py`, HTTP transports | VM callback scope return/panic; TCP shutdown reader and H2 RST stream isolation; cooperative children signalled and joined | targeted tests exit 0 but **not the complete frozen WS-04/WS-05 pass matrix** | Python 3.13.5, 2026-11-13.

**WS-04 closure | structured-cancellation checkpoint |** `runtime/vm.py`, `web.py`, `_web_ws.py`, H1/H2 adapters | `test_std_web_ws04_ws05_cancellation.py` 10 cases, H1 14, H2 9 wire + 1 compiled, heartbeat 11, prior lifecycle 11 | all completed runs exit 0 | Python 3.13.5, 2026-11-13. WS-05 remains unverified/unimplemented for arbitrary native callback termination; see separate status.

**WS-08 closure | resource-budget checkpoint |** `_web_http1.py`, `_web_http2.py`, `_web_ws.py`, `web.vlnc`, `web.py` | `test_std_web_ws08_ws11_resource_gates.py` (normative public 4 MiB default/Valiance bridge, H1/H2 queue bound, H1 slow-reader deadline, two H2 clients with windows closed and reset, H1/H2 flush, shared reservations and write failure) | 14-case new test suite exited 0; H1 14, H2 10 and targeted supporting regressions exited 0 | Python 3.13.5, 2026-11-13. Server-owned outbound frames are reserved *before* serialization, the H2 DATA queue retains its own reservation through TLS flush, and the per-session cap is enforced even with two queued H2 chunks. This closes the frozen WS-08 gate for H1/H2; broader memory supervision remains WS-09/10.

**WS-09 partial | same checkpoint |** `_web_ws.py` reserves conservative 4× untrusted frame-body lengths before reading even in manual heartbeat-disabled mode; releases in finally after completion, EOF/error/abort. The previously implemented heartbeat FIFO reservation remains. Full initial-upgrade byte staging and all allocation peaks still need acceptance evidence. Keep OPEN.

**WS-10 partial / WS-11 historically open | earlier checkpoint |** live H2 two-connection shared-budget reset test, plus strict extension opt-in `ConfigError` and RSV rejection. No full resource leak/peak matrix or explicitly enabled permessage-deflate implementation; WS-10 and WS-11 remain OPEN.

**WS-05 partial | same checkpoint |** H1 native callback times out under a bounded shutdown and a truthful `isolated=0` diagnostic; test explicitly observes that the runnable callback remains. Not CLOSED.

**Regression record:** H1 suite 14, H2 10, heartbeat 11, admission 6, single-reader 2 (after updating the test's explicit manual-reader policy), request-context 8, typed-context 30, new WS-01–07 suite 19: **100 distinct targeted cases** individually covered by completed passing runs or completed successful subsets. The original single-reader result failed before an explicit non-heartbeat test-policy correction; subsequent two-test rerun exited 0. A few compiler-heavy combined invocations timed out and are **not** counted. Full repo, Python 3.14 and exhaustive conformance remain unverified.

Any proposed new requirement must list the source paragraph/RFC obligation or reproducible bug, why it was not already covered by WS-01–WS-23, and whether it blocks H1/H2 or only H3. This prevents indefinite expansion by vague milestones.

**WS-11 closure (H1/H2) | opt-in-deflate checkpoint, 2026-11-13 |**
`_web_ws.py`, `_web_http1.py`, `_web_http2.py`, `web.vlnc` |
`test_std_web_websocket_ws11_deflate.py` (11), `test_std_web_v29_websocket_h2.py` (+2),
independent `websockets` deflate client, Valiance `extensionPolicy="PermessageDeflate"` constructor test |
completed separate test invocations exit 0 | Python 3.13.5. Disabled is still default.
Only successfully validated offers negotiate `permessage-deflate` with
`server_no_context_takeover` and `client_no_context_takeover`; unsupported
window offers are declined, never reflected. RSV1 is accepted only on initial
data frames when compression is negotiated. Incoming decompression has bounded
output, compressed wire-size and fixed 1024:1 expansion bounds, zlib staging
budget and full reservation cleanup. The compressed H2 stream (including 150 KiB of binary data across flow-control windows) and ordinary GET
share one TLS connection. **This gate closes only for H1/H2.** H3 WebSockets
remain WS-21/22 and exhaustive protocol qualification WS-12–20 remains open.

**WS-09 further partial (not closed) | same checkpoint |**
H1 bytes received past Upgrade are now charged before constructing the parser,
released upon consumption or cancellation. Three new tests cover read,
abort and budget rejection. Complete allocator peak profiling is still open.

**WS-12 and WS-13 partial acceptance (both OPEN) | same checkpoint |**
`test_std_web_websocket_ws12_handshake_vectors.py` adds 21 negative handshake
vectors plus valid negotiation/hash and HTTP downgrade prevention;
`test_std_web_websocket_ws13_frame_corpus.py` adds 80 fixed-seed masked
fragmentation/text/binary/control cases, invalid frame vectors and UTF-8
rejection. Both suites exit 0 but do not satisfy the exhaustive wire/proxy,
independent-client and concurrency matrices in their frozen gate criteria.

**WS-05 historical status (superseded by cooperative contract amendment) | same checkpoint |**
Arbitrary uncooperative native/Python callback code is still in-process.
No safe kill boundary, process isolation proxy, or zero-runnable-orphan
acceptance proof exists. Existing timeout reporting does not change this.


**WS-10 additional partial validation (H1/H2, OPEN) | 2026-11-13 |**
`test_std_web_ws08_ws11_resource_gates.py` adds twelve H1 connect/send/close cycles and eight consecutive H2 extended-CONNECT/RST_STREAM cycles on a shared TLS connection, followed by a successful ordinary H2 GET. Both assert eventual zero WebSocket admission/callback counts and shared-buffer reservations. This is repeated lifecycle/ledger evidence, **not** the full required process peak-allocation, TLS/HPACK queue instrumentation or long-duration memory/leak qualification; WS-10 stays OPEN.

**WS-05 revised acceptance and closure (H1/H2) | 2026-11-13 |**
Explicit contract change: the former requirement to terminate arbitrary uncooperative native/Python callbacks is removed as technically unachievable in-process. The narrower mandatory guarantees are cooperative cancellation, bounded socket/TLS teardown, joining cooperative session-owned work, reclamation of transport-owned bytes, and truthful reporting of any still-running callback or unjoined child. No alteration to the frozen normative v1.0 specification is claimed; this clarifies its practical Python execution boundary (§12.7). **Implementation:** `_web_ws.py`, `_web_http1.py`, `_web_http2.py`, `web.py`, `web.vlnc`. **Evidence:** `test_std_web_ws04_ws05_cancellation.py` (H1/H2 cooperative child cancellation, ignored-cancel callback, unjoined child), `test_std_web_ws08_ws11_resource_gates.py` (forced H1 budget/FD reclaim), `test_std_web_limits_shutdown.py` (public stop report fields). Exact new checkpoint commands, counts, exit status and environment are in the accompanying continuation status report. H1/H2 feature/acceptance is **not** complete: WS-09/10 and WS-12–20 remain open.


### WS-09/10 and RFC matrix continuation — 13 November 2026

**Source checkpoint:** `Valiance-std-web-websocket-ws09-ws14-qualification-checkpoint.zip`.
**Normative spec is unchanged.** This evidence supplements, and does not weaken,
the frozen checklist. Explicit WS-05 cooperative cancellation amendment remains.

| Gate | State after this checkpoint | Evidence and remaining condition |
|---|---|---|
| WS-09 | `[ ] OPEN`, measured partial | Shared pre-body reservations and many partial clients; byte-by-byte fragmentation; heartbeat FIFO accounting; Linux RSS/traced heap samples. Global H2/TLS/HPACK allocator peak proof incomplete. |
| WS-10 | `[ ] OPEN`, measured partial | Listener `resource_snapshot` high-water/current counters, H1/H2 churn, backpressure, H2 mixed streams; 12 isolated process profiles passed. Full ownership-site audit and load/longevity baselines incomplete. |
| WS-12 | `[ ] OPEN`, extended | 24 new negative pure-handshake vectors, 11 live H1 malformed upgrades, list-valued header and subprotocol positives. Proxy wire matrix incomplete. |
| WS-13 | `[ ] OPEN`, extended | 21 exact-close-code negative frames, valid zero-payload close and split UTF-8, five invalid H2 streams and 16 valid concurrent fragmented H2 streams. Exhaustive cross-transport randomized matrix incomplete. |
| WS-14 | `[ ] OPEN`, extended | 19 negative pseudoheader parser vectors plus five live stream-local policy rejections and mixed H2 streams. SETTINGS/GOAWAY/trailer wire matrix incomplete. |
| WS-15–20 | `[ ] OPEN` | Browser/proxy, external H2 interop, full stress/regression, Python 3.14+ remain. |
| WS-21–23 | `[ ] BLOCKED H3 / OPEN sign-off` | Unchanged. |

Reproducible profile evidence was generated by
`tools/qualify_websocket_resources.py --repeat 3`; its results are summarized
here rather than retained as generated JSON.
In Python 3.13.5, 12/12 profiling subprocesses exited 0 with maximum
sampled RSS **104.41 MiB** (host-specific, not normative). The independently
completed targeted modules contain **114 distinct passing tests**. Those
numbers exclude combined/incomplete runners and overlapping repetitions.
Complete commands, package versions, limits, scope notes, and remaining gaps:
the WebSocket RFC acceptance section below.


### WS-09/10 and RFC acceptance follow-up — 13 November 2026

**Source checkpoint:** `Valiance-std-web-websocket-ws09-ws14-followup-checkpoint.zip`. The frozen normative specification and WS-05 cooperative shutdown amendment are unchanged. This is evidence added to open gates, not a relaxed pass criterion.

| Gate | Current status | New evidence / remainder |
|---|---|---|
| WS-09 | `[ ] OPEN` | HPACK quota scanner now retains ≤6 metadata octets and rejects during incomplete frames; H2 read chunk max 16 KiB. Native/TLS + aggregate HPACK allocation budgeting is not established. |
| WS-10 | `[ ] OPEN` | Observable listener read/write/parser/HPACK peaks; all 15 isolated profile runs passed, tests assert reclaim; aggregate memory cap over every ownership site and longevity baseline still absent. |
| WS-12 | `[ ] OPEN` | Existing handshake corpus retained; browser/proxy wire matrices absent. |
| WS-13 | `[ ] OPEN` | Registered 1012/1013/1014 close codes corrected; new legal/illegal peer/application close corpus; H2 split 64-bit announced frame-size rejection 1009. Cross-transport exhaustive fuzz pending. |
| WS-14 | `[ ] OPEN` | TLS/ALPN extended CONNECT rejects trailer stream-local; HTTP GET on same connection succeeds. Legacy H2 SETTINGS/GOAWAY/HPACK suites retested. Full RFC matrix pending. |
| WS-15–20 | `[ ] OPEN` | Interoperability, browser/proxy security, shutdown runner stall, Python 3.14, full-regression qualification not verified. |
| WS-21–23 | `[ ] H3 BLOCKED / sign-off OPEN` | Unchanged. |

**New evidence record:** `_web_http2.py`, `_web_http1.py`, `_web_ws.py`; scanner fuzz (3), H2 compressed field (10), RFC H2 v23 (11), v24 (16), H2 WS (19), H1 WS (15), WS-11 deflate (11), handshake (5), frame corpus (6), resource suite (19), WS-05 cancellation (16), admission (6), heartbeat (11): **148 distinct counted cases**, separate completed process runs exit 0 on Python 3.13.5. Five-profile-scenario × three-run subprocess matrix **15/15 pass**, maximum sampled RSS about 104.6 MiB on this Linux test host; not a universal memory bound. One additional H2 quiet runner stalled and is *not counted*, despite a later complete verbose rerun passing. Detailed conclusions are retained in the WebSocket RFC acceptance section below.


### 13 November 2026 — additional evidence without premature closure

| Item | Executed evidence | Acceptance decision |
|---|---|---|
| WS-09/10 | 7 x 3 isolated process profiles (21/21 clean), peak sampled RSS 105.78 MiB; aggregate cooperative transport worker joining and 0 lingering non-daemon workers in profile fixtures | **OPEN**: native TLS/HPACK/global byte cap audit unfinished |
| WS-12 | Explicit-port Origin parsing fix, H1 live rejection and H1/H2 policy regressions | **OPEN**: exhaustive wire/proxy matrix incomplete |
| WS-13/14/16 | Mixed 16 H2 stream valid/invalid RFC 6455 corpus, exact close codes and coexisting H2 GET | **OPEN**: broader error/fault/interop matrix incomplete |
| WS-15/17 | Real Chromium opaque Origin rejection and real local TLS-terminating reverse proxy with accepted/rejected Origins | **OPEN**: positive browser Origin and independent H2 vendor client missing |
| WS-18 | 21 clean process resource samples; 3 x mixed WSS/H2 churn | **OPEN**: long-load deployment matrix not completed |
| WS-19 | 160 distinct passing targeted methods across 15 modules, corrected resource suite green in 3 separate reruns; earlier H1 SIGSEGV -11 and two compiler-heavy timeouts preserved | **OPEN**: entire repository, stable clean-exit and root cause not verified |
| WS-20 | New `tools/qualify_websocket_release.py` and two fail-closed unit tests; `--inspect` exits nonzero on unsupported prerequisites | **OPEN**: Python 3.14, external tests, full release run unavailable |
| WS-21/22/23 | No H3 implementation/evidence | **BLOCKED / OPEN** |

No added checkbox has been closed on source review or test counts alone. The frozen normative specification is unchanged.


### 13 November 2026 — accelerated completion follow-up (this checkpoint)

**Source:** `Valiance-std-web-websocket-fast-track-checkpoint.zip`. **Frozen spec and amended WS-05 criteria unchanged.** New module `tests.test_std_web_websocket_interoperability` and new RFC, security, admission and shutdown tests provide specific completed cases but **do not close WS-09/10 or WS-12–20**. The toolchain/release gate remains intentionally fail-closed; H3 remains blocked.

| Gate | Passing evidence from this run | Decision |
|---|---|---|
| WS-09/10 | 24/24 eight-scenario isolated process profiles (3 repetitions), measured RSS max 105.85 MiB; four-connection H1/H2 TLS cap; complete 21-case resource and 35-case typed-context memory suite rechecked with updated conservative reservation fixtures | **OPEN**: native TLS/HPACK sum and hard global cap not proven |
| WS-13/16 | 500 seeded text/binary/fragment/control cases; malformed outgoing Unicode raises `WebError`; reject server-initiated 1010 while permitting peer 1010 | **OPEN**: hostile/full randomized RFC acceptance matrix pending |
| WS-14 | Active WebSocket H2 GOAWAY and simultaneous `wsproto`/hyper-h2 three streams plus ordinary GET | **OPEN**: independent H2 vendor implementation pending |
| WS-15/17 | H1/H2 cookie-only/bad-bearer rejected; valid bearer accepted; independent wsproto H1/H2 frame engine exercised | **OPEN**: positive real-browser Origin/client and H2 vendor test missing |
| WS-18 | H1/H2 TLS connection saturation and reclamation, eight profiles × three runs | **OPEN**: long-load deployment matrix pending |
| WS-19 | 255 distinct targeted tests green in completed individual processes across 20 modules after repairing four outdated memory-fixture assumptions; earlier resource-handshake timeout and previous SIGSEGV retained | **OPEN**: repository-wide concurrent failures and full regression incomplete |
| WS-20 | Test runner kills and reaps its own process group on POSIX timeout, tested with child; H1/H2 `--inspect` returns 1 | **OPEN**: Python 3.14+, complete external qualification unavailable |
| WS-21/22/23 | No QUIC/H3 WebSocket implementation or full release sign-off | **BLOCKED/OPEN** |

The generated reports were removed after their conclusions were incorporated here. Earlier failed invocations are not erased or counted as green. Process-group termination applies to the **test runner**, not to arbitrary in-process application callbacks. The full library remains unfinished.


#### Post-package stability check (same source bytes)

After the first complete 21-test resource-suite exit 0, two additional separate clean-process runs were attempted: **one failed** (`TimeoutError: timed out while waiting for WSS handshake response` inside mixed H1/H2 churn) and **one passed** (21 tests, exit 0). Three further diagnostic-instrumented resource-suite runs also exited 0; they do **not** explain or eliminate the failing run. The experimental instrumentation was reverted, leaving the source archive unchanged. This repeatability problem is an **unresolved WS-19 reliability defect**, regardless of passing isolated profiling and resource suite invocations. Do not describe the entire resource suite as reliably green.

### TLS/Origin security and stability follow-up — 13 November 2026

All original acceptance checks and the user-amended cooperative WS-05 boundary
are preserved. No outstanding frozen release checkbox is closed on the basis of
this focused continuation alone.

| Gate | Added executable evidence | Current decision |
|---|---|---|
| WS-09/10 | TLS phase/header observation, stall timeout/worker reclamation, WSS raw-wire TLS context reuse, mixed H1/H2 resource churn, extended isolated RSS/thread profile | **OPEN** — aggregate native/TLS/HPACK resource cap and whole matrix remain unproved |
| WS-12/15 | Reject Origin control/whitespace before URL parsing and reject invalid allowed origin policies; H1/H2 vectors | **OPEN** — full proxy/browser accepted-origin matrix still absent |
| WS-17 | Existing independent `wsproto` and `websockets` cases retained; synchronous WSS client anomaly isolated from resource test with real wire observations | **OPEN** — cross-vendor H2 and browser positive interop still absent |
| WS-18 | New slow ClientHello, 24 repeated WSS TLS context reuse, mixed H1/H2 quota churn; 9-scenario memory profile | **OPEN** — long deployment/load duration and full 3-repeat release matrix still pending |
| WS-19 | Expanded clean-exit resource repeats and observed third-party-client no-header stall diagnosis; previous `SIGSEGV` still unrooted | **OPEN** — full repo and crash-free all-client run not established |
| WS-20 | Qualification runner enforces at least three independent resource-suite invocations, captures fatal Python traces; profile kills worker process groups on timeout | **OPEN** — Python 3.14+ unavailable and external evidence not supplied |
| WS-21–23 | HTTP/3 remains absent | **BLOCKED/OPEN** |

This table is an evidence append, not a normative contract amendment.

## H1/H2 WebSocket RFC acceptance and resource-measurement matrix

**Scope:** RFC 6455, RFC 7692, RFC 8441, and HTTP/2 malformed-message rules from RFC 9113. The frozen Valiance WebSocket completion gates remain WS-09–WS-20. This matrix inventories both evidence and missing evidence; a populated row is **not** a release gate pass. HTTP/3 is tracked separately under WS-21/22.

**How to run:** `PYTHONPATH=src:. python -m unittest tests.test_std_web_websocket_ws12_handshake_vectors tests.test_std_web_websocket_ws13_frame_corpus tests.test_std_web_v28_websocket_h1 tests.test_std_web_v29_websocket_h2 -q`. Run the modules as separate processes when combined runners fail to exit. For independently measured RSS/CPython heap and resource churn: `PYTHONPATH=src:. python tools/qualify_websocket_resources.py --repeat 3`. This profiler writes its generated report under `.artifacts/`; test-host ceilings are not network-protocol normative limits.

### Handshake and extensions

| Requirement | Frozen gate / RFC | Evidence in repository | Verdict / remaining acceptance |
|---|---|---|---|
| Valid H1 GET / `101`, key SHA-1, mandatory version and Host | WS-12; RFC 6455 §4.2 | `HandshakeCorpus.test_valid_handshake_accept_hash_and_extension_defaults`; `WebSocketH1Tests.test_fragmented_masked_message_ping_and_close` | Verified targeted |
| Invalid/duplicate mandatory H1 fields, invalid body/framing, method and HTTP version | WS-12; RFC 6455 §4.2.1 | `HandshakeCorpus.test_attack_matrix_rejected_before_upgrade`, `.test_extended_h1_header_and_origin_matrix` (21 original + 24 new negative or pre-parser cases); H1 `.test_wire_handshake_negative_matrix_never_dispatches_get` (11 real-wire inputs) | Partial; wire permutations across proxies are missing |
| Token/case normalisation, repeated *list-valued* Upgrade/Connection, negotiated subprotocol | WS-12; RFC 6455 §§4.2.1/4.2.2 | `HandshakeCorpus.test_h1_valid_case_tokens_subprotocol_and_allowed_origin`; H1/H2 existing subprotocol live tests | Verified targeted; not exhaustive |
| Malformed upgrade cannot become ordinary GET | WS-12; RFC 6455 §4.2 | `HandshakeCorpus.test_malformed_upgrade_never_falls_back_to_plain_get`; H1 `.test_wire_handshake_negative_matrix_never_dispatches_get` | Verified targeted; not trusted-proxy exhaustive |
| Same-origin rejection and explicit allowed Origin | WS-15; RFC 6455 §10.2 | `HandshakeCorpus.test_extended_h1_header_and_origin_matrix`; H1 `.test_origin_guard_and_explicit_allowlist`; H2 `.test_extended_connect_rejected_version_origin_and_subprotocol_are_stream_local` | Targeted policy; browser/cookie/proxy tests **OPEN** |
| permessage-deflate, invalid offers, RSV1, bounded expansion | WS-11; RFC 7692 | `tests.test_std_web_websocket_ws11_deflate` and H2 deflate tests | WS-11 retained closed on H1/H2; exhaustive malformed parameter interop under WS-12–16 still **OPEN** |

### Frames, close and cancellation

| Requirement | Frozen gate / RFC | Evidence in repository | Verdict / remaining acceptance |
|---|---|---|---|
| Mask bit and forbidden RSV/opcodes | WS-13; RFC 6455 §§5.2/5.3 | `FrameCorpus.test_protocol_rejections_close_with_rfc_codes_and_reclaim_bytes` (21 negative close-code assertions) and H1 `.test_unmasked_and_invalid_utf8_close_codes`; H2 `.test_rfc6455_invalid_frames_are_independent_h2_stream_failures` | Verified tested variants; generated opcode combinations missing |
| Minimal 16/64-bit lengths, high-bit 64-bit field, announced oversize before payload | WS-13; RFC 6455 §5.2 | `FrameCorpus.test_protocol_rejections_close_with_rfc_codes_and_reclaim_bytes` | Verified targeted; H2 64-bit split-header oversize pre-body test verified; full cross-transport 64-bit wire matrix **OPEN** |
| Control ≤125 bytes, unfragmented control, Ping/Pong interleaving | WS-13; RFC 6455 §5.5 | `FrameCorpus` invalid control and fixed 80-seed interleaving; H1/H2 existing live ping/pong and heartbeat suites | Verified targeted; randomized H2 scheduling **OPEN** |
| UTF-8 across continuation boundaries, malformed text/close reasons | WS-13; RFC 6455 §8.1 | `FrameCorpus.test_valid_empty_close_and_fragmented_utf8_split_boundaries`, `.test_fragmented_invalid_utf8_must_fail_at_message_end`, `.test_protocol_rejections_close_with_rfc_codes_and_reclaim_bytes`; H2 malformed UTF-8 | Verified targeted; exhaustive incremental UTF-8 corpus **OPEN** |
| Valid empty Close and illegal close codes; echo close and clean EOF | WS-13; RFC 6455 §§5.5.1, 7.4 | `FrameCorpus.test_valid_empty_close_and_fragmented_utf8_split_boundaries`, `.test_iana_registered_close_codes_and_invalid_ranges` (including 1012–1014, app/private-use, forbidden codes); H1 close handshake test | Verified targeted; all legal close-code/reason sizes **OPEN** |
| Concurrent close/send/abort, H2 stream reset and shutdown | WS-07/13/16; RFC 6455 §§7.1–7.4 | `test_std_web_ws04_ws05_cancellation`; previous 40-seed close/write and six H2 reset/write cycles | WS-07 baseline closed; larger randomized protocol fuzz **OPEN** |

### Extended CONNECT (HTTP/2)

| Requirement | Frozen gate / RFC | Evidence in repository | Verdict / remaining acceptance |
|---|---|---|---|
| ENABLE_CONNECT_PROTOCOL server setting and extended CONNECT success `200`, no H1 `101` | WS-14; RFC 8441 §§3–5 | `H2WebSocketWireTests.test_rfc8441_settings_subprotocol_and_concurrent_http2_get` | Verified targeted; SETTINGS disagreement/policy cases **OPEN** |
| Required and prohibited pseudoheader layouts, case, Host/authority, Content-Length | WS-14; RFC 8441 §4; RFC 9113 §8.3 | `H2WebSocketWireTests.test_extended_connect_header_parser_adversarial_matrix` (19 negative parser vectors + positive crumbs/TE); `.test_malformed_pseudoheaders_are_stream_errors` | Verified targeted; malformed HPACK/pseudoheaders over raw TLS framing **OPEN** |
| Origin/version/subprotocol rejects as H2 stream-local HTTP responses, unrelated GET survives | WS-14/15; RFC 8441 §5 | `H2WebSocketWireTests.test_extended_connect_rejected_version_origin_and_subprotocol_are_stream_local` (five rejected streams + healthy GET) | Verified targeted; cross-browser/proxy case **OPEN** |
| Malformed WebSocket data closes only its own H2 stream and unrelated GET survives | WS-13/14; RFC 8441 §§5–6 | `H2WebSocketWireTests.test_rfc6455_invalid_frames_are_independent_h2_stream_failures` (five independent invalid streams + healthy GET); `H2WebSocketWireTests.test_fragmented_binary_ping_parallel_h2_streams_with_http_get` (16 fragmented binary streams + interleaved Ping and healthy GET) | Verified targeted; live trailer rejection and split 64-bit frame close-code assertions verified; reset/GOAWAY/window exhaustive matrix **OPEN** |
| Multiple active streams, flow control, reset, GOAWAY and trailers | WS-14; RFC 8441 §5; RFC 9113 §§5–8 | Existing `test_std_web_v29_websocket_h2` and `test_std_web_ws08_ws11_resource_gates` | Partial: live GOAWAY/trailer/error classification grid **OPEN** |

### Memory, resources and qualification boundaries

| Requirement | Frozen gate | Evidence | Verdict / remaining acceptance |
|---|---|---|---|
| Inbound reservation before body arrival, shared cap across idle clients | WS-09 | `ResourceGates.test_inbound_frame_reservation_precedes_untrusted_body_arrival`, `.test_many_partially_delivered_frames_share_one_pre_read_budget` | Tested; exhaustive TLS/socket parser peak allocations **OPEN** |
| Byte-at-a-time fragmentation; heartbeat FIFO both on/off | WS-09 | `.test_incremental_octet_input_releases_inbound_reservations`, `.test_heartbeat_fifo_reserves_then_releases_pending_messages`, earlier no-heartbeat suite | Tested; all modes and TLS/H2 allocator peak qualification **OPEN** |
| Shared memory high-water and zero after repeated H1/H2 teardown | WS-09/10 | `_ThreadedTCPServer.resource_snapshot()`; H1 12 connect/close cycles and H2 eight resets; paired H2 closed-window clients | Tested; not all native buffers accounted |
| Measured memory and task lifecycle across repeated isolated test processes | WS-10/18 | `tools/qualify_websocket_resources.py` | 4 scenarios × 3 repetitions; test-host RSS/heap/threads measured, **not full live-load acceptance** |
| Independent clients and real browser/proxy TLS deployments | WS-15/17 | Existing `websockets` and `websocket-client` independent H1 libraries; H2 hyper-h2 fixture | Real browser, proxy/tunnel implementation and cross-vendor H2 clients **OPEN** |
| Reproducible property/fuzz and no runaway memory | WS-16/18 | Fixed 80 seeded fragmentation cases; 21 illegal frame cases; 12 memory-profile runs | Larger randomized cross-transport fuzz and multi-hour/long-lived concurrency **OPEN** |
| Required toolchain and whole repository | WS-19/20 | Targeted Python 3.13.5 runs, no mandatory 3.14 run | **OPEN**; do not count suites that print OK but fail to exit |

### Definition of done not changed

The immutable completion checklist keeps WS-09, WS-10, WS-12 through WS-20 **OPEN** pending the outstanding cases above. This matrix does not lower the frozen acceptance bar or add a new release requirement. WS-05 remains closed under the explicitly amended cooperative (not arbitrary thread killing) contract. H3/QUIC remains blocked and is not included here.

### Continuation: compressed-header-copy removal, measured H2 staging and registered close codes

**Checkpoint date:** 13 November 2026. **Gates WS-09/10/12–20 remain open** under the unchanged completion criteria. The isolated profiling result is summarized below.

| Specific behavior / evidence | Requirement | Qualification verdict |
|---|---|---|
| `_CompressedHeaderBudget` retains only ≤6 header prefix metadata bytes, even across 175 deterministic padded/priority/CONTINUATION cases; rejects after 129 received HPACK bytes against a 128-byte quota while a 16,384-byte HEADERS frame is still incomplete | WS-09/10, compressed HTTP/2 field limits | Targeted constant-storage scanner and early pre-decode rejection **PASS**; hyper-h2 and TLS allocations remain separate |
| H2 TCP/TLS read staging reduced from 65,536 to 16,384 bytes per `recv`; server snapshots track H2 read peak, write-batch peak, parser-incomplete-frame peak and HPACK dynamic table high-water separately from reserved byte ledgers | WS-09/10, H2 buffer supervision | Observability **PASS** in live H2 test with reclaimed admission/byte leases; these are per-operation/connection high-water observations, not a cap or sum over TLS/native memory |
| H2 WebSocket trailers provoke a stream-local RST while a concurrent ordinary H2 GET completes normally | WS-14; RFC 8441 tunnel behavior | New real-TLS wire fixture **PASS**; all malformed/trailer combinations not exhausted |
| Each octet of a masked 64-bit WebSocket length header delivered via separate H2 DATA frame; announced 65,536-octet frame is rejected with WebSocket 1009 without waiting for body; concurrent HTTP GET survives | WS-13/14; RFC 6455 §5.2 | New real-TLS fixture **PASS**; more 64-bit framing combinations remain |
| IANA-assigned close codes 1012 (Service Restart), 1013 (Try Again Later), 1014 (gateway) accepted and echoed in peer/application paths; disallowed/unassigned codes rejected | WS-13; RFC 6455 §7.4 and current IANA WebSocket Close Code registry | Code mismatch fixed with new inbound/outbound legal/invalid corpus **PASS** |
| 5 profile scenarios × 3 independent subprocesses, host RSS+tracemalloc+thread deltas | WS-09/10/18 | **15/15 PASS** under recorded host guards, not proof of universal memory ceilings |
| Legacy H2 RFC suites `test_std_web_v23_http2_rfc` (11) and `test_std_web_v24_http2_acceptance` (16), plus HTTP/2 compressed-field budget and fragmentation suites (13) | WS-14/19 | Independent module processes exit 0, adding qualified SETTINGS/GOAWAY/HPACK coverage; full browser/proxy/3.14 release matrix **OPEN** |

**Still not qualified:** native TLS allocation and HPACK scratch/reallocation under low custom limits, sum of all connection-stage peaks versus global cap, browser/proxy-Origin security, independent vendor H2 WebSocket implementation, randomized failure-injection and longest-running churn, full repository and Python 3.14+. An intermittent separate H2 `-q` invocation did not cleanly exit; a subsequent complete verbose 19-test rerun exited 0. This remains a WS-19 reliability signal rather than a passing rerun or a gate closure.

### Qualification continuation: Origin authority, browser/proxy, mixed H2 and machine-readable release gates

The following additional *passing, targeted* fixtures expand the frozen WS-12–18
matrix without claiming exhaustive RFC, multi-vendor, memory or longevity acceptance.

| Behavior and authority | New executable evidence | Remaining qualification |
|---|---|---|
| RFC 6454 serialized Origin authority: explicit port zero must not alias an omitted default port; trailing empty port is invalid | `HandshakeCorpus.test_origin_port_zero_and_empty_explicit_ports_do_not_alias_default` (H1 and H2), `WebSocketH1Tests.test_explicit_zero_and_empty_origin_ports_fail_over_live_wire` | Full browser positive cases and additional authorities/proxy combinations (WS-12, WS-15) |
| Real Chromium browser emits an opaque Origin; no WebSocket handler entry after server rejects that origin; H1 parser admission checked so a blocked network attempt cannot count | `WebSocketH1Tests.test_real_chromium_browser_opaque_origin_cannot_hijack_websocket` | Browser network navigation restrictions in this environment prevented an accepted same-origin browser test; real browser full matrix remains open (WS-15/17) |
| TLS-terminating local reverse proxy with `Forwarded` and `X-Forwarded-Proto` demonstrates explicit allowlist success, hostile-origin rejection, and default-policy rejection | `WebSocketH1Tests.test_tls_terminating_reverse_proxy_origin_allowlist_and_forwarded_headers` | Independent deployed proxies, authentication/cookies and trusted-proxy configuration matrix remain open (WS-15/17) |
| 16 concurrent extended-CONNECT streams: eight seeded binary fragmented/Ping cases, eight invalid frame types with expected `1002`/`1007`/`1009`, and healthy GET on same TLS connection | `H2WebSocketWireTests.test_mixed_seeded_rfc6455_errors_and_fragmentation_stay_stream_local` | Independent H2 implementations, error classification beyond these vectors and large random fault campaigns remain open (WS-13/14/16) |
| Six repeated WSS sessions and six H2 sessions via the same TLS-enabled listener, with independent ordinary H2 GETs and listener-wide reclaim/admission peak assertions | `LiveTransportBudgetCases.test_mixed_tls_h1_h2_churn_preserves_listener_wide_admission` | Aggregate TLS/OpenSSL/HPACK heap is not charged under the shared listener byte ledger; longer-load/many-connection matrix remains open (WS-09/10/18) |
| Seven scenarios × three isolated processes with RSS, traced-heap and non-daemon thread-delta guards | `tools/qualify_websocket_resources.py --repeat 3` | Empirical fixture-specific peaks do not establish a universal aggregate native allocation cap (WS-09/10) |
| A checked gate-to-command report rejects a run without Python 3.14+, executable external evidence, clean full-repository tests, and actual H3 when requested | `tools/qualify_websocket_release.py`; `tests.test_std_web_websocket_release_qualification` | Script availability is not WS-20 closure; qualifying environment and required external execution still missing |

**Fail-closed release command:** `PYTHONPATH=src:. python tools/qualify_websocket_release.py --scope h1h2`. It writes its generated report under `.artifacts/` and is intentionally nonzero on this Python 3.13-only test host, even when every *targeted* module passes. External integration commands must perform actual tests and are recorded verbatim with exit status; a local mock of an external test is not qualifying evidence. `--scope full` also requires independently executed H3 interoperability (WS-21/22), not just a source filename.

External release commands must emit exactly one line with prefix
`VALIANCE_WS_EVIDENCE=` followed by a compact JSON object. Required fields are
`gate` (one of the exact gate labels returned by `--help`), `tests_run` (positive
integer), `skipped` (zero), `passed` (`true`), and a nonempty `implementation`
name/version. A command that merely exits zero, prints `OK`, or uses the wrong
gate label is **not accepted** as external release evidence. Review of actual
browser/proxy/H2 implementation independence remains required in addition to
this machine-checkable provenance; the format is not an anti-forgery mechanism.


### Accelerated follow-up — independent wsproto, 500 RFC frames, H1/H2 auth and GOAWAY

**13 November 2026; no WS-09/10 or WS-12–20 full-gate closure.**

| Specific acceptance proof | Executable evidence | Unresolved requirement |
|---|---|---|
| `wsproto` H1 TLS handshake/subprotocol and frame exchange; `wsproto` framing over hyper-h2 for three H2 extended-CONNECT streams and a concurrent GET | `tests.test_std_web_websocket_interoperability` (2, exit 0) | Independent vendor H2 and real positive browser interop (WS-17) |
| Cookie-only and invalid Bearer rejects under auth middleware, valid Bearer succeeds under both H1 and H2; ordinary H2 GET healthy | H1/H2 wire modules, exit 0 | Live browser/proxy matrix (WS-15) |
| H2 GOAWAY with active WebSocket, bounded shutdown, admission/byte recovery | `test_graceful_goaway_during_active_websocket_is_bounded` (exit 0) | Exhaustive GOAWAY/error-classification (WS-14) |
| 500 fixed-seed masked text/binary fragment/UTF-8 cases and malformed outgoing Unicode path; server-initiated 1010 prohibited | `tests.test_std_web_websocket_ws13_frame_corpus` (8, exit 0) | Broader fault/race fuzz (WS-13/16) |
| Four simultaneous TLS H1/H2 sockets under listener `max_connections=4`, fifth rejected, all transport workers and quota leases reclaimed | `test_tls_h1_h2_connection_cap_and_worker_reclamation` (exit 0) | Aggregate TLS-native/HPACK cap (WS-09/10) |
| Conservative 4x inbound pre-read reservations are reflected in older typed-context budget test fixtures; both full relevant modules exit 0 | `test_std_web_websocket_typed_context_memory` (35) and `test_std_web_ws08_ws11_resource_gates` (21) | H1 WSS handshake intermittently timed out in earlier combined run; no WS-19 closure |
| 8 measured scenarios x3 independent processes: 24/24 exit 0 with no remaining non-daemon workers or uncaught worker exceptions | `tools/qualify_websocket_resources.py --repeat 3` | Not proof of universal native cap or multihour load (WS-09/10/18) |

`tools/qualify_websocket_release.py` now kills spawned test-runner process groups on timeout and is regression tested. It still returns nonzero without Python 3.14+, complete full repository and external/browser/H2 vendor/H3 evidence. **This is deliberate.**


#### Post-package stability check (same source bytes)

After the first complete 21-test resource-suite exit 0, two additional separate clean-process runs were attempted: **one failed** (`TimeoutError: timed out while waiting for WSS handshake response` inside mixed H1/H2 churn) and **one passed** (21 tests, exit 0). Three further diagnostic-instrumented resource-suite runs also exited 0; they do **not** explain or eliminate the failing run. The experimental instrumentation was reverted, leaving the source archive unchanged. This repeatability problem is an **unresolved WS-19 reliability defect**, regardless of passing isolated profiling and resource suite invocations. Do not describe the entire resource suite as reliably green.

### TLS handshake and origin-parser hardening — November 2026

The HTTP/1.1 listener now separately reports `tls_handshakes_active`,
`tls_handshakes_peak`, `tls_handshakes_completed`, `tls_handshakes_failed`,
`h1_header_octets`, and `h1_header_read_eof` in its internal resource snapshot.
These are **diagnostics**, not reservations under `maxTotalBufferedBytes`.
`LiveTransportBudgetCases.test_stalled_tls_clienthello_does_not_block_wss_and_reclaims_slot`
proves a stalled ClientHello cannot block a second TLS/H1 WebSocket and its
connection slot and worker are subsequently reclaimed.

`WebSocketPolicy.allowed_origins` is parsed and validated when constructed.
The server rejects Origin values containing C0 controls, spaces, and DEL *before*
`urllib.parse.urlsplit` can discard characters. `HandshakeCorpus` exercises the
same control-character vectors for H1 and H2 and rejects malformed policy
allowlists. This is a targeted WS-12/15 security fix; full reverse-proxy/browser
security qualification still remains open.

A repeated third-party `websockets.sync` TLS/H1 churn fixture sometimes timed
out without sending any application data after a successful TLS handshake:
listener counters showed **zero new HTTP header octets**, unchanged admitted
request counts, and an EOF after the client timeout. Other retries completed.
The server's wire-level WSS churn cases now send a full RFC 6455 GET upgrade
immediately after TLS negotiation and check the actual echoed frame; 24
reuses of one TLS client context are a separate test. The independent-client
suites remain mandatory, and the sync-client anomaly remains an open WS-17/19
interoperability/reliability investigation. Do not claim a server-side fix,
client-library defect, or release closure solely from the raw-wire successes.

The memory profiler adds `tls_handshake_stalls` to its existing isolated H1/H2
matrix, records memory/thread recovery, and kills a timed-out worker's process
group rather than only its main process. Release qualification requires three
separate clean-exit runs of the resource suite and sets
`PYTHONFAULTHANDLER=1` to capture Python/native crash stacks if SIGSEGV recurs.
A previous SIGSEGV was observed during concurrent qualification; it has **not**
been root-caused or waived. Python 3.14+, full-repository acceptance,
positive real-browser Origin coverage, and HTTP/3 remain unqualified.


## Consolidated maintainer notes

### Native `Err` values and `Result[None, Err]` in Valiance stdlib adapters

A native function that constructs `ObjectValue('some.Error.Case', ...)` is not
necessarily equivalent at runtime to using the corresponding Valiance variant
constructor. The VM's `?` instruction checks runtime trait evidence, not only
static type names. For `std.web`, Python-created `web.Error` / `web.ConfigError`
values must carry `ObjectRuntimeType.accepted_names` including `Err`, as in
`_web_error_value` / `_config_error_value`; otherwise a handler may wrongly
continue past a failed check. Check both optimized and serialized `?` tests.

Also, a successful native `Result[None, Err]` should return `OK(None)` rather
than a bare `None`: the latter can trigger optional early-return handling from
`?`. This is particularly important for pre-mutation HTTP precondition checks.
The convention does not change the public union type and requires no new
language-level Result syntax.

### `std.web` binary bodies across native element calls

`std.file.#byte` lists are represented as `TaggedValue(ListValue(...))` when
stored in a Valiance object field, but native element argument conversion may
pass the same statically checked list as an untagged `ListValue`. Native HTTP
helpers therefore validate each item as an exact integer in 0..255 rather than
relying on runtime tag evidence or decoding bytes as UTF-8. In particular,
`std.web`'s `Response.body` accepts a union of text and byte-tagged lists;
handlers may build the body from `web.bodyBytes(req) ?`. The response bridge
must preserve octets both when returning the response to Valiance and when
unwrapping a compiled handler's result. Check optimized and serialized
programs, HEAD suppression, and after-middleware whenever changing this path.

### Bounded static web files and route ownership

`std.web.staticRouter` builds an ordinary immutable Router containing a native-only
`web._StaticFileHandler` marker in the catch-all GET route. When converting App
values, `valiance.std.web._to_core_config` recognizes this marker and installs the
transport-independent `core.static_file` handler without attempting a Valiance
`ctx.call` on a host callback. Router groups, app middleware, path precedence,
HEAD fallback and socket-free dispatch therefore share the existing pipeline.
Preserve marker duplication and mounted-root semantics when changing routing.

The current static implementation is a **bounded POSIX subset**: it checks
`O_NOFOLLOW` and descriptor-relative `open` support at construction, forbids
symlinks/dotfiles beneath the root, and buffers at most 16 MiB per response.
Do not silently fall back to pathname concatenation on platforms without
secure dirfd traversal, or imply that the buffered response is an owned file
lease. See `WebStaticFileTests` for path confinement, byte range, validator,
serialized bytecode, and HTTP/1.1 checks. `std.path.Path` native overloads
preserve raw POSIX filename bytes through `os.fsdecode`/surrogateescape.

### Defaulted higher-order `std.web` route modifiers

Modifier-style native elements may declare a function parameter before trailing
optional parameters. The modifier callback is nonetheless passed **last** to
the native implementation at runtime, after the ordinary arguments. For route
registration, unpack `target, path, bodyMode, maxBodyBytes, handler` even though
`handler` appears third in the declared function signature. This is important
when adding options to existing modifier-based APIs.

The analyser must also plan defaults for a modifier call without named call
arguments (for example `post("/upload"): fn(...)`). When a required ECS stack
argument precedes an explicit argument, a defaulted suffix must not shift that
explicit argument into the first required position: `post("/upload")` obtains
its App from the surrounding stack, and its path from the explicit argument.
The general call planner (`analysis/calls/arguments.py` and `candidates.py`)
implements the matching rule; do not special-case web route spelling in the
parser or VM. Run `test_std_web_route_body_options.py` and the v17 streaming
suite when changing this binding behavior.


### std.web v21 multipart limit policy propagation

For HTTP/1.1, `web.Limits` maps into `_web_http1.Limits` and the adapter copies
its `multipart_policy` into each `core.Request`. Both `core.multipart` and
`core.multipart_stream` draw from that immutable request snapshot and clamp
core-only explicit overrides to its values. The native Valiance bridge must
round-trip that snapshot in the private `multipartPolicy: Dict[String, Int]`
field for **all** eight `web.Request` variant cases and the eight corresponding
method-specific `web.*Request` objects; adding a field to only the variant
breaks Valiance object constructor overload arity. Validate public defaults
for `web.Limits` in both `web.vlnc` and `nativeDefaultLimits` in `web.py`.

The MIME parser's 16 KiB part-header buffer is independent of the UTF-8
name/filename quotas; `BodyTooLarge` (413) should win when a valid
Content-Disposition value exceeds a configured name/filename quota, including
for the normative 1,024/4,096-byte defaults. Streaming multipart owns and
closes the request stream; file parts cannot be advanced until consumed or
discarded. The definitive end-to-end examples are in
`tests/test_std_web_v21_multipart_limits.py`.


### std.web v22 HTTP/2 transport integration

TLS 1.3 listeners automatically advertise both `http/1.1` and `h2` through ALPN; plaintext remains HTTP/1.1. `src/valiance/std/_web_http2.py` owns a per-connection hyper-h2 state machine. Only the network/connection worker may call hyper-h2 or access the socket. Bounded application tasks own their RequestBodyStream reads and StreamBody generation; incoming credit is posted through the thread-safe `ack` queue, which MUST retain list identity when drained because each incoming stream retains a reference to it. A streaming input's credit is returned **on consumption**, not on receive. The stream data producer queue is bounded to two 16-KiB chunks and never eagerly collects an unknown-length response.

`_web_http1._ThreadedTCPServer` tracks the **count** of in-flight requests per connection; don't revert it to a boolean, or H2 graceful drain and metrics break. The ALPN-selected H2 socket is registered separately to avoid shutting down idle H2 sockets before they can send GOAWAY. Incoming DATA over the route/global cap must send a 413 if response headers are not yet committed, and reset if already committed; do not suppress the error by accidentally treating this as an ordinary cancelled stream. On RST_STREAM cancel only the matching `_State`; preserve other stream windows and request tasks.

This is a functional H2 milestone, not an RFC 9113 conformance statement: test SETTINGS/GOAWAY/HPACK edge cases, shared budgets and scheduler starvation, noncooperative producer cancellation, and final API `ServerConfig(protocols=...)` before production. Current `uv.lock` is stale, and the 3.13 test environment cannot regenerate it for the project-required Python 3.14. Run `tests/test_std_web_v22_http2.py` and the v15–v21 modules when touching the transport, response normalization, or native bridge.

##### v23 continuation checkpoint

The HTTP/2 implementation improves RFC 9113 error/flow-control/GOAWAY behavior and has 99 individually confirmed targeted passing tests; it does **not** close full HTTP/2 RFC conformance. Run `PYTHONPATH=src python -m unittest tests.test_std_web_v23_http2_rfc` after any HTTP/2 edit. In particular, hyper-h2's `InvalidBodyLengthError` must stay stream-local; check the private hook after any hyper-h2 update. Do not regenerate the lock on Python 3.13: the project baseline requires Python >=3.14.

##### `std.web` v24 checkpoint (12 November 2026)

The v23 archive was extended with a connection-wide buffered HTTP/2 upload cap, per-stream 413 isolation/byte-credit cleanup, and wire-level RFC 9113 checks. The TLS adapter maps malformed HPACK to COMPRESSION_ERROR, nonempty SETTINGS ACK to FRAME_SIZE_ERROR, and rejects oversized frames at the nine-byte header. Run `PYTHONPATH=src:. python -m unittest tests.test_std_web_v24_http2_acceptance tests.test_std_web_v23_http2_rfc tests.test_std_web_v22_http2 -q` for focused H2 regression; passed 37 cases here. **Do not claim full RFC 9113 conformance**; private hyper-h2 hooks, cross-connection resource limits and exhaustive wire acceptances remain. See `docs/maintenance/web.md` before continuing.

### `std.web` v25 HTTP/2 supervision (verified subset)

The `_ThreadedTCPServer` listener owns a shared admission counter across HTTP/1.1 and HTTP/2 (`maxConcurrentRequests`). For HTTP/2 buffered request payloads only, it also owns a lock-serialized byte budget (`maxTotalBufferedBytes`), reserved before enqueuing DATA and released exactly once after the buffered request completes, is rejected, resets, or the connection tears down. Admission failures send H1 503 or H2 `RST_STREAM(REFUSED_STREAM)`. H2 `_account` deliberately defers releasing a reset stream's request-task lease while its handler or started response producer still runs; its worker `finally` completes accounting. When editing this lifecycle, keep `test_std_web_v25_http2_supervision.py` (cross-connection contention and reset-while-handler-blocked) passing. These limits do not cover all response, HPACK or H1 allocations. The existing hyper-h2 integration uses private hooks; no full RFC 9113 compliance is claimed.


### `std.web` v26 HTTP/2 stream watchdog and non-joining shutdown

The H2 worker checks monotonic *per-stream* body/response progress: an idle incomplete upload is reset using `CANCEL`; after input completion, no handler/producer/outbound DATA progress until `write_idle_timeout_seconds` also resets that stream. Do not turn this into connection-wide timeout cancellation: multiplexed peers must survive. H2 runs callbacks in a bounded daemon pool instead of a `ThreadPoolExecutor` context manager so network shutdown cannot wait forever for `source.read()` or arbitrary handler code; **this cannot stop already-running callbacks**. A live handler/producer must keep its listener-wide request admission slot until its own `finally` path runs, including after `_connections` removes its TLS socket. An abandoned, queued producer is closed on a separate daemon thread; never call potentially blocking application `close()` in the connection worker. Per-stream timers are *inactivity* timers, not absolute request deadlines, and the normative spec remains unchanged. Run `tests/test_std_web_v26_http2_deadlines.py` and the v22–v25 HTTP/2 tests before changing these lifecycles. Full RFC 9113 and Python 3.14 verification remain outstanding.

### `std.web` HTTP/2 malformed-message isolation and HEAD ownership

The hyper-h2 4.3.0 receive path converts some malformed HTTP **message** conditions (invalid Content-Length during HEADERS, trailing HEADERS lacking END_STREAM) into connection-fatal errors even though RFC 9113 §8.1.1 requires a stream error. The narrow overrides in `_web_http2.py` translate only these known conditions to `RST_STREAM(PROTOCOL_ERROR)`; framing/HPACK failures remain connection errors. Always retest this private integration when upgrading hyper-h2; `tests/test_std_web_http2_message_lifecycle.py` exercises independent streams. For TLS ALPN offer both `h2` and `http/1.1`, and prefer `h2` on the **server**; offering h2 in the server list but putting HTTP/1.1 first negotiates H1 for clients like curl that offer both.

`StreamBody.close()` and handler callbacks are arbitrary application code and can block. The core dispatcher normally eagerly strips/closes HEAD bodies; HTTP/2 opts into `defer_head_stream_close=True`, then an H2 worker retains ownership and admission while the connection worker emits HEAD `END_STREAM` promptly. Never invoke untrusted source close from the multiplexed frame thread. Check H2-specific response fields **before** transferring an exclusive producer, or invalid response headers can leave a never-started producer and stall the stream. Existing socket-free and H1 dispatch must keep their normal eager HEAD cleanup.

### WebSocket request variant and owned-socket metadata

The WebSocket upgrade has a distinct `Request.WebSocketRequest` variant with
`originalMethod=GET` (H1) or `CONNECT` (H2). Middleware snapshots are separate
from the nominal `web.WebSocketRequest` in an owned `web.WebSocket`. When
adding Valiance field declarations, **qualify** the latter type on the
`WebSocket.request` field as `web.WebSocketRequest`: a bare local nominal can
resolve without its object field metadata in an imported module.

For compiled Valiance handlers, reading `$socket.request.field` currently
materializes an extra occurrence of the `@nodup` socket and raises a
`DuplicationFault`. Use the native `web.websocketRequest($socket)` accessor
instead; it returns an immutable metadata snapshot without copying or
consuming the transport owner. Do not relax the VM duplication guard to work
around this; a future compiler change should prove safe field borrowing.

#### TLS WebSocket handshake measurement under concurrent qualification

When a WSS test times out, distinguish TLS negotiation, H1 header arrival,
request admission, and the WebSocket callback. `ServerHandle.resource_snapshot()`
exposes separate counters for those stages. In repeated mixed H1/H2 churn on
Python 3.13.5, a synchronous third-party WSS client occasionally completed TLS
but sent **no HTTP Upgrade bytes** before timing out; server request counters
were unchanged and its header reader saw EOF. Do not attribute that trace to a
stuck WebSocket handler or mask it with blind retries. Deterministic resource
fixtures use direct RFC 6455 frames over TLS, with independently implemented
clients checked in separate interoperability suites. Keep the intermittent
third-party-client behavior and unexplained SIGSEGV as explicit release risks
until independently diagnosed and reliably reproduced or qualified.
