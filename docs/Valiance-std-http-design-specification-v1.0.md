# Valiance `std.http` — design specification and implementation status

**Status: experimental implementation, not a stable-release specification.**

This document is the engineering design record for `std.http`. The original
ten-phase release qualification remains mandatory before promoting the module
to stable. The currently implemented subset is distinguished from design
requirements to prevent callers from relying on unqualified behavior.

## Public API implemented

| Operation/type | Valiance contract | Status |
|---|---|---|
| `http.Client` | Default shareable client, native reference identity; `http.close(client)` | Implemented, local tests |
| `http.ClientConfig` | Validated per-transfer, per-client and private process-wide queued-response/upload capacity budgets, in-flight admission, header ceilings, optional read/write-idle timeouts, opt-in cookies, origin/HTTPS/IP restrictions, redirects, proxy and CA options | Implemented subset, local tests |
| `http.openClient(config)` | `ClientConfig -> Result[Client, http.Error]` | Implemented, local tests |
| `http.Request` | Method, HTTP(S) URL, optional headers and optional body | Implemented, local tests |
| `http.RequestBody` | `Empty`, `Text`, `Bytes`, `Stream`, `Form`, `Multipart`, `MultipartStream` | Buffered form/file and streamed multipart files tested |
| `http.uploadPipe` | Single writer/source pair with bounded chunks and optional known length | Implemented with cooperative backpressure |
| `http.Headers` | Ordered, case-insensitive multimap; duplicate field names retained | Implemented, local tests |
| `http.Error` | `InvalidRequest`, `Network`, `Timeout`, `Limit`, `Closed`, `Unsupported`, `Decode` | Implemented subset; error taxonomy not final |
| `http.send(client, request)` | `Result[Response, Error]`; responds at final headers, even for 4xx/5xx | Implemented, local tests |
| `http.get(url)` / `http.head(url)` / `http.delete(url)` | Default-client requests, `Result[Response, Error]` | Implemented |
| `http.post(url, text)` / `http.put(url, text)` / `http.patch(url, text)` | UTF-8 text-body helpers through canonical transport | Implemented |
| `http.read(body)` | Exclusive body owner plus `Result[#byte+ Int+?, Error]`, EOF is `None` | Implemented, local tests |
| `http.readAll(body, limit)` | Exclusive owner plus bounded `Result[#byte+ Int+, Error]` | Implemented, local tests |
| `http.bytes(response)` / `http.text(response)` | Read and close response body with fixed 16 MiB complete-body limit | Implemented, local tests |
| `http.json(response)` | `Result[std.json.Json, Error]`, strict UTF-8 and the same parser as `std.json.load` | Implemented, local tests |
| `http.jsonAs[T]` | `Result[T, Error]`, uses `std.json.decode[T]` type checking and exact-decimal JSON | Implemented, local tests |
| `http.FormField`, `http.MultipartPart`, `RequestBody.Form/Multipart` | Ordered fields, binary files, validated MIME parts, auto-generated Content-Type | Implemented; buffered, max 16 MiB |
| `MultipartPart.StreamFile`, `RequestBody.MultipartStream` | Lazy MIME segments with one-use streaming `UploadSource` per file, optional declared length | Implemented; independent HTTP/1.1, HTTP/2 and Valiance tests; further stress qualification needed |
| Redirect policies | Opt-in 301/302/303/307/308, cross-origin credential stripping, max hops, loop/downgrade checks | HTTP/1.1 local tests pass |
| `ClientConfig.enableCookies` | Optional client-scoped RFC 6265 subset, host/path/expiry/Secure, public-suffix restrictions | Implemented, local tests; policy not independently qualified |
| Retries | No automatic retries | Explicit retry policy not implemented |
| `ClientConfig.allowedOrigins` / `requireHttps` | Optional exact scheme/host/port allowlist and HTTPS-only outbound mode | Implemented; does not inspect DNS/IP itself |
| `ClientConfig.denyNonPublicIps` | Optional socket-level public-IP restriction, including redirect re-resolution and disabling proxies | Implemented locally; needs adversarial and supported-OS review |
| `ClientConfig.writeIdleTimeoutMs` | Optional stalled-upload-progress deadline, excluding producer-side backpressure | Implemented, deterministic deadline and upload integration tests |
| `ClientConfig.connectionAcquireTimeoutMs` | Optional deadline to acquire a libcurl connection (queueing and connection setup) | Implemented, requires libcurl >= 8.2; local queue-contention tests |
| `http.withQuery(url, fields)` | Ordered query-field encoder, preserves repeated keys | Implemented, local tests |

`Response` currently exposes `status`, `finalUrl`, `protocol`, `headers`, and
`body`. Public request/response types deliberately expose no libcurl handles.
The `ClientConfig` and `ResponseBody` signatures should not be considered frozen. To pass a non-default `Headers` field to `Request`, import `std.http.Headers` along with `std.http`; to construct `RequestBody.Text`, import `std.http.RequestBody`. This follows Valiance's nominal import behavior.

### Resource limits and idle deadlines

The default `maxPending=1024` is a cap on **all accepted in-flight transfers**,
not merely the submissions still queued for the reactor. Requests above this
limit fail at admission with `http.Error.Limit`; cancelled, rejected and
completed transfers release their admission permits.

`bodyBufferBytes=262144` bounds each queued response separately. In addition,
`maxClientBufferBytes=2097152` bounds the aggregate queued response bytes
across one client. When the shared budget is full, native transport pauses
rather than allocating unbounded additional queued chunks; freeing capacity
or explicitly closing an unread body resumes the waiting streams. Completed
chunks held by readers, libcurl internal allocations, and buffered uploads
are **not** part of this particular shared budget. A fixed 64 MiB **process-wide queued response buffer cap** now additionally
covers all HTTP clients, using atomic per-client/global reservations and
cross-client wakeups. The limit is internal rather than a user-configurable
VM-wide budget. A minimum per-client budget of 16384 bytes prevents
starvation on an ordinary libcurl body callback.

An additional fixed **64 MiB process-wide upload capacity reservation** is
shared by all clients and all upload sources. Each `UploadPipe` reserves twice
its capacity at creation (its bounded queue plus one pending writer's chunk),
releasing the lease on transfer completion, resource closure, or finalization.
`MultipartStream` charges generated MIME fragments and buffered part data on
transfer admission; ordinary buffered `bytes` request bodies are charged from
admission until transport completion. If a reservation cannot be acquired,
submission fails with `http.Error.Limit` without accepting another transfer.
The reservation is deliberately conservative: it limits aggregate *potential*
HTTP-retained upload data, not measured resident heap. It does not account for
caller-held Valiance `#byte+ Int+` values or their copies, Python interpreter
and libcurl allocations, or network kernel buffers; full VM/native memory
qualification remains open.

`maxResponseHeaderBytes=131072` and `maxResponseHeaderFields=256` enforce
limits on response headers, including interim messages and redirects. These
limits fail an in-progress request before oversized metadata is returned.
`readIdleTimeoutMs` is optional (`None` by default); when configured, it
limits inactivity *after final headers*, but the clock is reset on resumption
from local response-buffer backpressure. `totalTimeoutMs` still bounds the
whole transaction, including redirects and body transfer. The optional
`writeIdleTimeoutMs` monitors upload progress reported by libcurl after the
first upload bytes progress, except when the application producer is paused
waiting for data; it is not a TCP ACK-level measurement. A separate
connection acquisition deadline `connectionAcquireTimeoutMs` is opt-in and
applies from admission to libcurl connection assignment (including pool waits,
DNS, and connecting). It requires libcurl >= 8.2, reports
`connection-acquisition` timeouts, and is observed at the reactor's 100 ms
polling cadence. It is **not** a pure pool-queue deadline or a hard realtime
interrupt; total and connect deadlines remain independently in force.

```valiance
import {std.http}
$config = http.ClientConfig(
  maxClientBufferBytes=32768, bodyBufferBytes=16384,
  maxResponseHeaderBytes=65536, maxResponseHeaderFields=64,
  readIdleTimeoutMs=Some(250), writeIdleTimeoutMs=Some(2000),
  connectionAcquireTimeoutMs=Some(5000))
$client = http.openClient($config) ?
http.close($client)
```

### Compile-checked usage

```valiance
import {std.http}

$response = http.get("https://example.org/") ?
$response.status
$text = http.text($response) ?
$text
```

```valiance
import {std.http}

$config = http.ClientConfig(maxConnections=4, maxConnectionsPerOrigin=2)
$client = http.openClient($config) ?
$response = http.send($client, http.Request("GET", "https://example.org/")) ?
http.close($client)
http.bytes($response) ?
```

```valiance
import {std.http, std.http.ResponseBody}

$response = http.get("https://example.org/") ?
#? readAll returns the exclusive body owner and the result.
http.readAll($response.body, 1048576)
```

These are examples of syntax, not evidence that a public Internet call was run.
Local integration tests compile and execute equivalent operations with an
independent HTTP server, including optimized/serialized bytecode.

### Structured form, multipart and policy examples

```valiance
import {std.http, std.http.RequestBody}
$fields = [http.FormField("query", "café & tea"), http.FormField("query", "water")]
$client = http.Client
$response = http.send($client,
  http.Request("POST", "https://example.org/form", \None,
    Some(http.RequestBody.Form($fields)))) ?
http.text($response) ?
```

The form encoder preserves repeated keys and uses UTF-8 URL-encoding. The
`Multipart` constructor below is buffered (absolute limit 16 MiB), produces a
random boundary, sets `Content-Type` automatically and rejects an
application-supplied `Content-Type` for structured bodies:

```valiance
import {std.http, std.http.RequestBody, std.http.MultipartPart, std.file.#byte}
$parts = [
  http.MultipartPart.Text("message", "hello"),
  http.MultipartPart.File("upload", "data.bin", "application/octet-stream",
                          [0 #byte, 255 #byte])
]
$client = http.Client
$response = http.send($client,
  http.Request("POST", "https://example.org/upload", \None,
    Some(http.RequestBody.Multipart($parts)))) ?
http.bytes($response) ?
```

For large multipart files, use `RequestBody.MultipartStream` instead. It
assembles MIME headers, buffered text fields and exclusive streamed file parts
incrementally. The sender can suspend while a different task feeds an
`UploadWriter`; no complete multipart request is allocated. For example, with
an independent streaming producer:

```valiance
import {std.http, std.http.RequestBody, std.http.MultipartPart, std.http.UploadWriter, std.file.#byte}
$pipe = http.uploadPipe(expectedBytes=Some(3), capacity=1) ?
$writer = $pipe.writer
$source = $pipe.source
$writer
$task = spawn: fn (writer: UploadWriter) -> Int =>
  $(w2, a) = http.write($writer, [0 #byte]) ?
  $(w3, b) = http.write($w2, [255 #byte]) ?
  $(w4, c) = http.write($w3, [65 #byte]) ?
  $(w5, done) = http.finish($w4) ?
  $a $b + $c +
end
$parts = [
  http.MultipartPart.Text("note", "hello"),
  http.MultipartPart.StreamFile("file", "data.bin", "application/octet-stream", $source)
]
$client = http.Client
$response = http.send($client,
  http.Request("POST", "https://example.org/upload", \None,
    Some(http.RequestBody.MultipartStream($parts)))) ?
$task wait
http.bytes($response) ?
```

Static MIME framing and buffered parts remain limited to 16 MiB and the
client's smaller `maxBufferedUploadBytes`, if configured. Streamed file chunks
are subject to each upload pipe's bounded capacity and the process-wide upload
reservation (two capacities per live pipe). Reusing a source within a
multipart body is rejected; cancellation and transport cleanup abort all its
sources. A known length is used for Content-Length if *all* sources declare
one; otherwise HTTP/1.1 uses streaming transfer framing. The multipart body
cannot be replayed on 307/308 redirects. Buffered requests with a nonempty body also cannot be replayed on cross-origin redirects. This is not a native file-path reader:
the application must feed chunks into the pipe itself.

The following policies are *optional*. Cookie state is private to one
client, with no global jar. An origin allowlist matches the URL's exact
scheme/hostname/effective port, and is checked again on every redirect.
The exact-origin allowlist alone **does not block DNS rebinding or protect
against malicious proxies**. To restrict untrusted destination URLs, enable
`denyNonPublicIps`, which inspects libcurl's selected remote IP before creating
a socket and prevents configured and environment-derived proxies from bypassing
that check. It blocks non-global IPs as well as common IPv6 transition ranges.
This opt-in safeguard is not yet a complete, cross-platform-validated SSRF
sandbox, and should be combined with trusted egress-network controls.

```valiance
import {std.http}
$config = http.ClientConfig(
  enableCookies=Some(true),
  allowedOrigins=Some(["https://example.org"]),
  requireHttps=Some(true),
  denyNonPublicIps=Some(true))
$client = http.openClient($config) ?
$response = http.send($client, http.Request("GET", "https://example.org/")) ?
http.text($response) ?
```

### Redirect and streamed-upload examples

Redirect following is disabled by default. To enable it for a client, set an
optional Boolean and a hop limit:

```valiance
import {std.http}
$config = http.ClientConfig(followRedirects=Some(true), maxRedirects=4)
$client = http.openClient($config) ?
$response = http.send($client, http.Request("GET", "https://example.org/")) ?
http.text($response) ?
```

An upload pipe separates its exclusive writer and source owners. The writer
can be moved to a producer task while the caller sends the source. Each write
chunk must fit in the capacity. A known length is optional; when omitted,
HTTP/1.1 uses chunked framing. The following pattern is tested against a local
independent HTTP/1.1 server (not the public example URL):

```valiance
import {std.http, std.http.UploadWriter, std.http.RequestBody, std.file.#byte}
$pipe = http.uploadPipe(expectedBytes=Some(3), capacity=1) ?
$writer = $pipe.writer
$source = $pipe.source
$writer
$task = spawn: fn (writer: UploadWriter) -> Int =>
  $(w2, n1) = http.write($writer, [97 #byte]) ?
  $(w3, n2) = http.write($w2, [98 #byte]) ?
  $(w4, n3) = http.write($w3, [99 #byte]) ?
  $(w5, done) = http.finish($w4) ?
  $n1 $n2 + $n3 +
end
$client = http.Client
$response = http.send($client,
  http.Request("POST", "https://example.org/upload", \None,
    Some(http.RequestBody.Stream($source)))) ?
$task wait
http.bytes($response) ?
```

Because Valiance binds a single positional argument to the rightmost optional
parameter, the `uploadPipe` declaration puts `capacity` last. Prefer named
arguments when supplying both settings. `307` and `308` redirects do not
replay streamed uploads; they return a protocol error.

## Ownership and lifecycle decisions

```text
VM
 ├─ default client (lazy, VM lifetime)
 ├─ explicitly opened clients (native token registry)
 ├─ response body token registry
 └─ upload endpoints (one writer and one source per pipe)

Client.OPEN --accept request--> Transfer.PENDING
        |                      |         |
        | close()              | headers | transport error/cancel
        v                      v         v
 Client.CLOSING         Body.READABLE   Transfer.TERMINAL
        |                      |
        | no leases            | EOF / close / cancellation
        v                      v
 Client.CLOSED           Body.TERMINAL
```

- `Client` identity is an entry in a native registry, not a copyable raw
  native pointer. The entry retains an active-transfer lease. Closing a client
  stops new admissions, but previously accepted transfers retain the transport
  until they finish. Closing a body asks the reactor to abort that transfer.
- `ResponseBody` is annotated `@nodup`: only one reader owns the body. A
  pending read rejects concurrent access. The `read` and `readAll` methods
  return the owner in Valiance's `@returnAll` style; an EOF, failure or
  explicit close is terminal.
- VM closure attempts deterministic cleanup: close registered response bodies,
  stop explicit clients, stop the lazy default client. Native close is
  idempotent. Completion and cancellation race through futures and registered
  scheduler wakes rather than calling Valiance from a transport thread.
- Buffered request bodies can be reused, but retries are disabled. Redirects are
  opt-in and observe method-rewrite rules. Upload pipes are single-use resources:
  the source is claimed on send, the writer may run in a separate Valiance task,
  and dropping an unfinished writer aborts the upload. Native streaming does not
  execute Valiance code from a curl read callback.

## Native and runtime architecture

The Python bridge `src/valiance/std/http.py` accepts Valiance values, validates
and translates them to private Python value types (`_http_core.py`), registers
client/body identities, and returns `ExternalOperation` values. The bridge
only constructs Valiance runtime objects after the future is resolved on the
VM's execution thread.

`runtime/external_io.py` represents completion, failure and abort hooks.
`runtime/vm.py` detects external operations at native call boundaries,
registers a scheduler wake, suspends cooperative tasks, and schedules result
publication on the scheduler thread. Cancellation invalidates the wake and
calls the transport abort hook. Calls at root execution use the same operation
and reactor. Root waits drive runnable tasks because an upload's spawned
producer may be needed to complete the request. Existing task scheduling, FFI
and structured-scope behavior should be checked against all modes.

`_http_transport.py` is an event-driven libcurl multi reactor with exactly one
reactor thread per client (not per request). A bounded command queue accepts
requests; separate lifecycle controls prevent cancellation from starving when
admission is full. Connection caps are delegated to libcurl multi options.
Response callbacks enqueue chunks into a bounded per-transfer queue; on
pressure they pause the easy transfer and resume when the reader frees space.
An individual oversized curl callback can temporarily exceed the configured
buffer budget. Response-header completion occurs at the final status-line
header block, before body consumption. HTTP/2 is requested for TLS through
libcurl feature negotiation, and independently tested against a separate Python `h2` TLS peer: ALPN,
parallel streams on one connection, stream cancellation isolation, binary
POSTs and CA validation. This is **partial HTTP/2 evidence**, not an RFC-level
`h2spec` conformance or a production certificate/proxy matrix.

### Timeout, cancellation and error decisions

- libcurl monotonic network timers implement total and connect timeouts in
  milliseconds. One real deadline covers redirect hops and response-body
  consumption. Root and scheduled requests share that transport; the root
  adapter drives runnable tasks to avoid deadlock with spawned upload producers.
- Cancelling a pending send cancels its future and queues transfer abortion.
  A task's cancellation callback invalidates its external wake before abort.
- Cancelled tasks preserve the VM task-cancellation mechanism; the aborted
  transport's errors are not injected into the cancelled task. Explicit body
  close surfaces a `Closed` error on further reads.
- 4xx/5xx statuses are ordinary `Response` values. Header/protocol and
  transfer failures become `http.Error` cases. libcurl codes are private.
- Text, dynamic JSON and typed JSON use strict UTF-8. Dynamic JSON shares the
  `std.json` strict parser and variant builder (exact decimals, duplicate-key
  rejection, all six root JSON types). Typed JSON shares that parser and
  `std.json.decode[T]` checked constructor/variant/type validation. All three
  read limits are fixed at 16 MiB in this implementation.

## Security and resource threat model

| Threat | Current mitigation | Remaining gap |
|---|---|---|
| Certificate spoofing | Peer/hostname verification default; temporary independent TLS h2 peer tests untrusted CA rejection and configured CA trust | Full trust-store/mTLS/proxy matrix |
| Header or URL injection | ASCII token names, Latin-1 values, control-character rejection, HTTP(S) URLs | Full HTTP request-smuggling suite |
| Credential leakage on redirects | Disabled by default; cross-origin redirects discard **all caller-supplied headers**, forbid replay of nonempty request bodies, and reject literal control characters in Location before URL joining | Remaining custom transport/proxy behavior needs formal audit |
| SSRF / DNS rebinding | Validated HTTP(S) URL; opt-in origin/HTTPS policy and libcurl-socket address filter for non-public IPs and transition ranges, checked on every new connection; restricted clients disable proxy use | Adversarial DNS-rebinding, unusual tunnel/egress routes, OS compatibility and network policy audit outstanding |
| Response memory exhaustion | Per-response and per-client queued-body budgets with pause/resume; in-flight admission permits, decoded-body ceiling and header byte/field limits | VM-wide/full native-allocation memory budget and benchmark evidence missing |
| Slow peers | Real connect/total and optional response read-idle and upload-progress write-idle deadlines | Pool-acquisition limit, comprehensive real-network upload stall qualification and timeout race matrix missing |
| Large uploads | Bounded streaming pipe; lazy multipart stream composition with known/unknown lengths; buffered form/multipart limit (16 MiB hard cap and configurable lower max) | VM-wide memory/large-transfer stress and file/channel producer adapters missing |
| Cookie theft / request replay | Cookies disabled by default; opt-in per-client jar enforces domain/path/expiry/Secure, prefixes and public-suffix policy; no retries | Full RFC 6265bis validation, cross-platform public suffix feature check and replay-aware retries missing |
| Native resource leaks | Exactly-once transfer removal, client leases and VM cleanup; local regression tests | Extensive cancellation/race/fuzzer/leak qualification missing |
| Platform compatibility | System libcurl >= 7.68 with SSL and HTTP/2 feature bits discovered dynamically; startup error if incompatible | Minimum version checks and Linux/Windows/macOS packaging and licensing assessment missing |

## Binary values

The current public API uses the existing Valiance `#byte+ Int+` representation,
and native decoding/encoding creates tagged integer-list values. This retains
compatibility but is **not an efficiency qualification**. A benchmark-backed
first-class contiguous byte representation (or an accepted reason not to add
one) is required before the stable release. Buffered uploads use libcurl
copy-postfields; streamed uploads instead use a bounded single-consumer source
driven by libcurl's read callback, with pause/resume.

## Verification / release-gate inventory

| Source plan phase | Evidence present | Outstanding gate |
|---|---|---|
| 0 — Architecture | This decision record, API examples, local tests | Approved signature contract, benchmarks, full threat/conformance review |
| 1 — Types/ownership | Header, request, config, resource registries, exclusive body | Task-channel move tests, finalizer stress, compact-byte decision |
| 2 — External I/O | Scheduler wake and future abort interface, root adapter | Deterministic race harness, task cancellation across all boundaries |
| 3 — Transport | libcurl multi, HTTP/1.1 local peer; independent Python h2 TLS test, multiplex/cancel/TLS coverage | h2spec/protocol fuzz, proxy/OS packaging matrix |
| 4 — HTTP semantics | `send`, metadata, status, header-before-body tests | Complete RFC framing/trailer/interim/error cases |
| 5 — Streaming | Bounded response and upload pipes, per-client and process-wide queued-body accounting, pause/resume, known/unknown lengths, independent local peer | Full VM/native+upload memory accounting, file/channel integration, extended stress qualification |
| 6 — Policies | Connect/total/read/write-idle deadlines, response-header limits, TLS defaults, redirects, opt-in cookie jar, origin/HTTPS restrictions, opt-in non-public IP socket policy, gzip decoded-size cap | Pure pool-queue deadline, adversarial IP/rebinding tests, retries, proxy suite, full cookie/spec review |
| 7 — High-level/JSON | Verbs, `bytes`/`text`, JSON, streaming and bounded buffered/streamed multipart bodies, query helper | Configurable text decoding, file/channel producer adapters, stable API review |
| 8 — Qualification | Independent HTTP/1.1 and h2 peers, security/cookie/form tests, 96 concurrent bounded downloads, selected VM regressions | h2spec, fuzz, ASAN/leaks, full repository CI/regressions, supported OSes |
| 9 — Release | Source and maintenance guide | Dependency packaging, reference/tooling, stable review and CI |

**No stable-release exit gate is claimed.** The original implementation plan's
full acceptance matrix is still the release definition of done. Do not ship
this as production-ready or expose its experimental behavior as a guaranteed
long-term public API.
