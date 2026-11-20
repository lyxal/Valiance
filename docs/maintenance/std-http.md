# Maintaining outbound `std.http`

**Release candidate — production signoff pending.** See the
[design and implementation-status specification](../Valiance-std-http-design-specification-v1.0.md)
for public contracts, state diagrams, threat model, and unimplemented release
requirements.

## Module boundaries

| File | Responsibility |
|---|---|
| `src/valiance/std/http.vlnc` | Public data declarations, ownership annotations, error variants and body methods |
| `src/valiance/std/http.py` | Native Valiance function registration, translation, VM-local client and body token registries |
| `src/valiance/std/_http_core.py` | HTTP transport-neutral validation, config, method, headers, request and metadata values |
| `src/valiance/std/_http_body.py` | Bounded upload pipes, process-wide upload reservations, lazy multipart segment composition, producer completion, EOF, cancellation and source ownership |
| `src/valiance/std/_http_cookies.py` | Opt-in per-client cookie store with domain/path/expiry/Secure scoping and PSL fallback |
| `src/valiance/std/_http_errors.py` | Structured private error kinds and safe diagnostics |
| `src/valiance/std/_http_transport.py` | ctypes libcurl multi reactor, in-flight admission, per-client/process-wide response budgets, buffered-upload leases, connection acquisition deadline, socket-level IP policy, idle deadlines and header limits |
| `src/valiance/runtime/external_io.py` | Reusable VM/native asynchronous operation contract |
| `src/valiance/runtime/vm.py` | Root adapter, cooperative task suspension, scheduler wake and cancel propagation |
| `tests/test_std_http.py` | Independent local HTTP peer and Valiance compilation/bytecode tests |
| `tests/test_std_http_http2.py` | Separate Python h2 TLS peer, stream multiplex/cancellation and certificate verification |
| `tests/test_std_http_http2_adversarial.py` | Independent TLS h2 peer sending malformed HPACK and forbidden response-header frames below hyper-h2's header validator |
| `tests/test_std_http_adversarial.py` | Raw HTTP/1.1 hostile peer: malformed status/headers, interim responses, conflicting framing, truncated bodies |
| `tests/test_std_http_security_audit.py` | Independent two-origin credential/body-redirect probes, proxy-environment isolation, public-IP restrictions, cookie mutation, native loader override validation |
| `tests/test_std_http_protocol_fuzz.py` | Reproducibility and automated black-box mutated HTTP response corpus |
| `tools/qualify_std_http.py` | Per-suite process-tree bounded qualification with machine-readable evidence |
| `tools/profile_std_http_resources.py` | Repeatable RSS, traced-heap, descriptor, thread and upload/download budget recovery measurement |
| `tools/check_std_http_native.py` | Inspect dynamically mapped native libcurl, TLS capabilities, resolved file SHA-256 and optional deployment override |
| `tools/fuzz_std_http_protocol.py` | Deterministic raw-wire mutator with a machine-readable corpus outcome and resource cleanup checks |
| `tools/soak_std_http.py` | Bounded multi-request connection-pool soak with cancellations, early closes, latency and resource accounting |

## Critical invariants

- The libcurl easy and multi handles are manipulated only on their owning
  reactor thread, except `curl_multi_wakeup` is used from submitting threads.
  The native wakeup is serialized against `curl_multi_cleanup` with `_lock`,
  including cancellation/shutdown races. Never invoke Valiance callbacks from
  a C callback or reactor thread.
- If the reactor exits unexpectedly, `_fail_closed` rejects all newly admitted
  requests, fails active response streams and not-yet-admitted futures, and
  releases native handles and admission slots before destroying the multi
  handle. An unexpected exception must never leave a scheduler activation
  blocked forever. Fault injection covers an active reader and queued send.
- An HTTP native call returns an `ExternalOperation` whose future is awaited
  cooperatively, not by blocking the bytecode executor. `ExternalOperation`
  materializes Valiance values only in the scheduler/root execution path.
- All admission attempts that acquire a client lease must release it exactly
  once, even when a queue entry is cancelled or discarded on reactor shutdown.
  `ResponseBody.close` is idempotent, empties buffered chunks and enqueues
  transfer cancellation.
- Cancelling a pending single-chunk body read closes the body and aborts
  the transport, never silently discards the next received chunk. Complete-body
  async consumption uses an iterative pump, not recursive completion callbacks,
  and cancelling an aggregate future also closes its body. Completed bodies
  release unread buffered reservations if the final Python owner is collected;
  actively transferring bodies still require cancellation, close or deadline.
- A body has one logical owner; methods use `@returnAll` to keep the exclusive
  owner on the stack alongside a result. Do not silently clone or share
  `ResponseBody` or introduce a second reader.
- `Client.close` stops new requests. Existing transfer leases keep the reactor
  around until network transfers finish. VM closure forcibly cleans up all
  tracked clients and bodies.
- `Headers` retains duplicates and ordering. `Host`, transfer encoding and
  content length cannot be arbitrarily written over libcurl's framing.
- Decode limits are checked **on decoded output**, not just on compressed wire
  sizes. Futures distinguish EOF from terminal transfer failure.
- Root and spawned tasks must always use the same reactor/transport path.
- Root execution drives runnable tasks while an external future is pending;
  blocking on the future would deadlock if a spawned task provides upload bytes.
- A streaming upload's source is claimed once on admission. The single writer
  can move to a producer task, with at most one pending writer and bounded
  accepted chunks. libcurl pauses on an empty source and resumes via a reactor
  command, never by invoking Valiance on its native thread. `MultipartStream`
  composes several independent sources with generated MIME framing; each
  `StreamFile` source is claimed exactly once and cleaned up on completion,
  cancellation, or failed admission. A failed or incomplete source aborts the
  entire multipart transfer; streaming sources cannot be replayed on redirects.
  Only static framing and buffered parts count against
  `maxBufferedUploadBytes`, not the bytes supplied by streaming writers.
  A separate private 64 MiB process upload reservation additionally charges
  twice each UploadPipe capacity (queue plus one pending writer), generated
  multipart fragments on request admission, and ordinary buffered request
  bytes until the transfer terminates. This aggregate reservation is
  conservative and fail-fast; it does not suspend new source construction.
  `shutdown_vm` must call `release_transfer` for outstanding pipes so VM
  teardown returns their leases deterministically.
- Redirects are disabled by default. With opt-in policy, 301/302 rewrite POST,
  303 rewrites non-HEAD, and 307/308 preserve method/body. Buffered bodies can
  be replayed; streamed bodies cannot. Cross-origin hops strip sensitive
  authentication headers and HTTPS-to-HTTP is forbidden unless opted in.
- An entire redirect chain shares its original monotonic deadline. The final
  response reports its final URL, and only the final body's bytes are exposed.
- `maxPending` counts **all accepted in-flight transfers**, including requests
  already dequeued from the command queue. Each rejected/failed/cancelled
  admission must release exactly one semaphore permit. Reusing an UploadPipe
  must not dispose of the previously reserved transfer or leak its permit.
- Each response has a bounded queue (`bodyBufferBytes`). A client-wide budget
  (`maxClientBufferBytes`) additionally caps the sum of all queued response
  bytes. A private process-wide queued-response cap of 64 MiB is shared by
  *all* HTTP clients; both caps must have room for a chunk before it is
  retained, with atomic reservations and coordinated cross-client wakeups.
  The process cap is a fixed private safety limit for this experimental release,
  not yet a configurable VM-wide heap/native-memory budget. Callback
  chunks delivered directly to an awaiting reader, libcurl's internal buffers,
  user-owned completed chunks, and buffered uploads are not covered by the
  *response* cap. Upload capacity is accounted for separately as described
  above. The reactor pauses streams when the shared
  budget is full and resumes them as readers or closures release capacity.
  Wakeups are coalesced to avoid an unbounded control-command backlog.
- `maxResponseHeaderBytes` and `maxResponseHeaderFields` cap received header
  traffic, including intermediate response and redirect headers. Headers over
  either limit fail with `Limit` before a response is returned. The optional
  `readIdleTimeoutMs` applies after final response headers, reset by received
  body chunks, and does not elapse during transport pause for backpressure.
  Optional `writeIdleTimeoutMs` monitors libcurl-reported upload progress after
  at least one payload byte has advanced; it does not run while a producer's
  empty UploadPipe has intentionally paused libcurl, after upload completion,
  or after final response headers. This is not an ACK-level TCP deadline.
  The existing total deadline still applies to the entire transfer.
- Optional `connectionAcquireTimeoutMs` (disabled by default) limits elapsed
  time from request admission/redirect hop until libcurl associates a connection
  with the transfer. This includes time waiting for a pooled connection *and*
  DNS/connect setup. It is different from read-idle or total transfer timeouts,
  and does **not** isolate pure pool queueing time. The reactor uses the
  `CURLINFO_CONN_ID` connection identity (also correct for HTTP/2 multiplexing),
  so clients requesting this option require libcurl >= 8.2; older libcurl
  versions fail client creation with `Unsupported`, not silently ignoring it.
  Deadline observation is bounded by the reactor's polling cadence (100 ms).
  Completion/cleanup and cancellation release the admission lease normally.

## File-backed streaming uploads

`file.ReadFile.readChunk(count)` returns the same exclusive reader plus `Some`
nonempty `#byte+ Int+`, or `None` at EOF. It reads at most `count` bytes, not
exactly `count`, and accepts sizes from 1 to 1,048,576. Unlike `readBytes`, an
ordinary short final chunk and EOF are not errors. Mixing prior text decoding
with raw byte reads remains an error. Failed chunk reads close the native file
handle, even if task unwinding precedes the Valiance resource destructor.

A file reader belongs **inside its producer task** and each returned `@nodup`
owner must be rebound to its original local (not copied into another local).
The upload pipe's capacity must accommodate each chunk. The following is the
working ownership pattern; connect the resulting `source` to a
`RequestBody.Stream` or `MultipartPart.StreamFile` and concurrently call
`http.send` from the parent task:

```valiance
import {std.http, std.file, std.path, std.http.UploadWriter}
$pipe = http.uploadPipe(capacity=65536) ?
$writer = $pipe.writer
$source = $pipe.source
$writer
$producer = spawn: fn(input: UploadWriter) -> Int =>
  $reader = file.ReadFile(path.Path("payload.bin"))
  $out = $input move(value -> value)
  $total = 0
  while (true) =>
    $(reader, chunk) = file.readChunk($reader, 65536)
    $chunk match =>
      as :None => break
      _ =>
        $(out, written) = http.write($out, $chunk ?!) ?
        $total = $total + $written
    end
  end
  $(out, finished) = http.finish($out) ?
  file.close($reader)
  $total
end
```

The file source is never loaded in full. File I/O errors are filesystem faults,
not HTTP transport errors. A producer that fails before `finish` must abort
its writer and the transfer; callers must observe both the producer task and
HTTP result. This implementation does not provide an all-or-nothing file upload
transaction.

## Bounded qualification and supported-interpreter gate

Run `python tools/qualify_std_http.py --report std-http-qualification.json`
under Python 3.14 or later. Each unittest suite runs in a separate process
group with a hard timeout. A timeout is a failure, and its process tree is
terminated and reaped rather than left running. Local development on older
Python may use `--allow-unsupported-python`, but that flag explicitly records
the override and **does not** constitute stable-release signoff.
The `std-http-qualification.yml` CI workflow runs the same qualification on
Linux, macOS and Windows Python 3.14 runners and uploads per-platform evidence.
Cross-platform qualification is only established after those jobs have actually
passed. This runner does not replace security fuzzing, leak instrumentation,
or full protocol conformance testing.

## Structured body and client-policy contracts

- `RequestBody.Form` accepts an ordered list of `http.FormField(name, value)`
  and generates UTF-8 `application/x-www-form-urlencoded` bytes. `withQuery`
  preserves duplicate keys. `RequestBody.Multipart` accepts `MultipartPart.Text`
  and `MultipartPart.File` and generates a fresh boundary and MIME encoding.
  These are **buffered** types: hard limit 16 MiB, with the configurable
  `maxBufferedUploadBytes` providing a lower client budget. Structured bodies
  reject conflicting caller-provided Content-Type headers. For large files,
  use `MultipartPart.StreamFile` inside `RequestBody.MultipartStream`, with a
  single-use `UploadSource` produced by an independent bounded `UploadWriter`.
  The multipart encoder emits MIME framing incrementally without buffering
  streamed file data. The source and its pending producer are aborted on
  transport cancellation, and a known Content-Length is used only if all
  streamed parts declare a length.
- Redirects are opt-in. On a **cross-origin redirect**, ALL caller-provided
  headers are dropped (not just a fixed list of known credential names), since
  custom headers may contain authentication tokens. libcurl may regenerate
  transport-managed headers such as Host and Accept. A cross-origin 307/308 or
  preserving 301/302 with a nonempty buffered body fails with a structured
  redirect protocol error instead of replaying potentially sensitive content
  to the peer-selected origin. A 303 or conventional POST 301/302 rewrite can
  redirect safely as GET without that body. Same-origin preserving redirects
  retain their method and buffered body. Before resolving a Location, reject
  literal controls/backslashes so URL normalization cannot silently strip
  malicious bytes; percent-encoded bytes within a valid URL remain permitted.
  The peer-chosen first destination always receives the original request.
- `enableCookies` is false by default. When true, a private reactor-owned jar
  receives response Set-Cookie fields before redirect processing and sends
  scoped cookies on later requests; explicit Cookie headers take precedence.
  It limits stored cookie count, individual size and outbound header length.
  Domain cookies require OS `libpsl` public-suffix checks; when unavailable,
  domain-attribute cookies are rejected (host-only still supported). This is
  a conservative RFC 6265 subset, not browser-grade cookie semantics.
- `allowedOrigins` accepts a nonempty list of bare URL origins; matching
  compares normalized scheme/host/effective port. `requireHttps` rejects HTTP.
  Both policies are validated at request setup on **every redirect**.
- Optional `denyNonPublicIps` (off by default for backward compatibility)
  checks **each libcurl-selected remote socket address** using the socket-open
  callback, immediately before a socket is created. It rejects loopback,
  private, link-local, reserved, and other non-global IPv4/IPv6 addresses,
  including IPv4-mapped IPv6. It also excludes known IPv6 transition ranges
  that can encapsulate IPv4 destinations. This avoids a separate DNS-lookup
  check and therefore defends against resolver/address time-of-check races and
  redirect re-resolution. Any explicit proxy is rejected and inherited
  HTTP(S)/ALL_PROXY settings are disabled for restricted clients, since
  otherwise a remote proxy could reach forbidden destinations. Rejected
  address candidates return `InvalidRequest` with the `network-policy` stage.
  **It is an opt-in destination-address control, not a complete SSRF sandbox.**
  DNS metadata leakage, unusual routing, platform behavior, redirect policy,
  IP-range classification, and OS egress controls still need security review.
  Automatic retries remain disabled.
- Independent HTTP/2 tests also cover peer stream reset after partial data,
  trailer completion, multiple stream/connection flow-control windows, GOAWAY
  connection rotation and cancellation of a backpressured stream. An independent
  raw-wire HTTP/1.1 corpus deterministically mutates 48 Content-Length/body
  boundaries. TLS tests reject both expired and hostname-mismatched certificates
  even when the server certificate is explicitly trusted.
- `test_std_http_http2.py` provides a separate Python h2 TLS peer and covers
  negotiated HTTP/2, multiplexing, stream cancellation, and TLS trust.
  It does not replace `h2spec` or security/load protocol qualification. Independent security fixtures cover cross-origin header/body replay, proxy environment isolation and cookie/PSL behavior, and a seeded raw-wire corpus exercises malformed peers.

## Resource and transport audit tools

Run `python tools/profile_std_http_resources.py --clients-per-batch 45
--batches 3 --report resource-profile.json` for a bounded repeated-client
process measurement. The profiler uses a separate, local Python HTTP peer and
records tracemalloc, process RSS (when `psutil` is installed), open descriptors,
reactor threads, and global upload/response reservations after garbage
collection. It also fails on unreturned reactor threads or budgets, and
records its explicit RSS/heap growth thresholds. This is a **heap-growth
smoke test**, not proof that every native allocation is tracked or that a
memory sanitizer found no unsafe accesses.

Run `python tools/check_std_http_native.py --report dependencies.json` to
record the version, TLS backend, SSL/HTTP2 feature bits and loaded libcurl.
On Linux, the inspector also finds the *mapped* library path and records its
SHA-256 and file size, so the binary can be tied to its packaging manifest.
For an explicitly packaged native library, set `VALIANCE_LIBCURL_PATH` to its
absolute file path **before** creating any HTTP client. This is a controlled
deployment override, not a public Valiance configuration option.

Run `python tools/fuzz_std_http_protocol.py --seed 20261120 --count 1800
--report protocol-fuzz.json` to repeat the independent response-wire mutation
campaign. Run `python tools/soak_std_http.py --duration-seconds 15
--concurrency 32 --report soak.json` to measure bounded local HTTP/1.1
multi-client throughput, cancellation and cleanup. Both tools record failures
as data; neither replaces exhaustive RFC/HTTP2 fuzzing, system egress rules,
ASan/Valgrind or production-scale/long-duration testing. See the
[native dependencies maintenance guide](std-http-native-dependencies.md)
for certificate, platform and license packaging obligations.

## Supported development environment

The runtime loads a compatible **system libcurl** through `ctypes.util.find_library`, unless deployment sets `VALIANCE_LIBCURL_PATH` to an existing absolute native library file. Bare relative loader overrides are rejected to prevent search-path hijacking. The selected library is cached process-wide after its first use.
libcurl multi, `curl_multi_poll`, `curl_multi_wakeup`, HTTP/2 negotiation and
certificate verification support are required by the implementation. The runtime checks libcurl >= 7.68 and SSL/HTTP2 feature bits on loading; opting into connection acquisition timeouts additionally requires libcurl >= 8.2.
There is no bundled libcurl and no validated cross-platform compatibility
matrix. Absence of a compatible library produces an `Unsupported` startup error.
The supported project Python requirement remains `>=3.14`; local development
can exercise the tests on Python 3.13 but that is not release validation.

## Local tests

From the repository root with the project's dependencies available:

```sh
PYTHONPATH=src pytest -q tests/test_std_http.py tests/test_std_http_http2.py tests/test_std_http_adversarial.py
PYTHONPATH=src pytest -q tests/test_concurrency_execution.py tests/test_concurrency_vm_suspension.py
PYTHONPATH=src pytest -q tests/test_programs.py
```

Run bounded regression qualification with explicit local platform evidence:

```sh
PYTHONPATH=src python tools/qualify_std_http.py --timeout-seconds 165 --report std-http-qualification.json
```

On unsupported Python 3.13, use `--allow-unsupported-python` **only for
local development**. The GitHub Actions matrix requests Python 3.14 on Linux,
macOS and Windows; its platform results cannot be inferred from local runs.
The Windows process-tree test covers termination while the parent is live;
the escaped-descendant-after-parent-exit case requires additional Windows
containment testing. See [conformance matrix](std-http-conformance-matrix.md).

Before a stable release, run the whole repository test suite, real HTTP/2
and TLS peer tests, concurrency cancellation stress, DNS/TLS interruptions,
ASAN/Valgrind lifetime checks, protocol fuzz cases, and Windows/macOS/Linux
package checks. Record pass/fail evidence rather than treating test presence
as proof.

## Troubleshooting

- A task that never wakes: inspect `_suspend_external_result`, the native
  future's done callback, scheduler external completion queue and waitable
  registration. Callbacks may run immediately if the future is already done.
- A reactor that remains alive: inspect `_ClientEntry.active` leases,
  `_Reactor._remove`, the admission/shutdown queue-drain and VM body tokens.
- Upload admission failing with `Limit`: inspect `_PROCESS_UPLOAD_BUDGET`
  in `_http_body.py`. Pipe reservations charge twice the capacity even if
  empty; multipart framing and buffered request bytes consume additional
  reservations. Resource shutdown/finalization must release leases once. This
  intentionally does not account for all VM or libcurl heap allocations.
- Memory growth on slow downloads: inspect `ResponseBody._push`, `_paused`,
  `_BufferBudget`, `curl_easy_pause`, and `ClientConfig.max_client_buffer_bytes`.
  The aggregate queued-body budget is strict, but host-owned and libcurl
  buffers are additional bounded sources of memory. A response's local queue
  can hold one complete callback chunk even if it exceeds `bodyBufferBytes`.
- A local test reports `Unknown element 'http.readAll'`: import
  `std.http.ResponseBody` alongside `std.http` to resolve the public instance
  method; this follows Valiance's member import conventions.
- An `std.http` native import disappears: ensure the `stdlib_element`
  registry and on-disk `.vlnc` signatures agree and do not overload a native
  name at different input arities (the environment rejects such overloads).
- An upload stalls: inspect `_http_body.UploadPipe._paused`, `_waiting`,
  `_http_transport._READ_CALLBACK`, and the `resume_upload` reactor command.
  `uploadPipe` arguments are ordered `expectedBytes`, then `capacity` to match
  Valiance's rightmost optional-parameter convention; use named arguments.

## Future engineering work

The file-backed multipart producer path is implemented; add large-file stress,
filesystem failure injection and broader channel interoperation qualification. Complete **VM-global** heap/native memory
accounting (beyond the implemented queued-response and upload-capacity limits),
including Valiance tagged byte values and libcurl allocations; configurable text
decoding and a wider write-idle test matrix,
full cookie/RFC and PSL review, replay-safe retry policy, adversarial DNS
rebinding and unusual-route testing of the opt-in IP policy, independent
redirect/TLS/proxy testing and full HTTP/2 framing/h2spec suite.
The upload/redirect path now has independent adversarial and soak evidence,
but the overall implementation remains a **release candidate**, not a signed-off
stable release. OS-specific native memory analysis, broad protocol fuzzing,
full network audit and production deployment qualification remain open.

## Instrumented local validation and release gate

`docs/qualification/std-http/RELEASE_VALIDATION.md` documents the separate
regression, HTTP/1.1 mutated-wire, HTTP/2 malformed-frame and multiplex soak,
ASan/UBSan C native ABI, RSS/FD reservation, packaging, and evidence-gate
checks. The source repository provides executable tools and Linux CI steps,
not an assertion of certified native-library or cross-platform results.
Python 3.14 verification is explicitly deferred to the maintainer.

## Stable-release gates and upgraded libcurl policy

See [std-http-go-live.md](std-http-go-live.md) for the enforced minimum 8.23.0 native dependency, checksum-verified source build, controlled rollout, rollback, and fail-closed independent attestation requirements. The previous 7.68 minimum for the underlying libcurl API is not the supported security baseline for a production std.http release. Local tests using `VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL=1` never qualify as release evidence.

## Commit-time CI versus release qualification

The production runtime **rejects libcurl older than 8.23.0**. Some hosted CI
images ship older versions; their version is not evidence of a compatible
shipping binary. Do not weaken `_validate_libcurl_version` to make those
hosted-image tests pass.

- `.github/workflows/tests.yml` uses
  `VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL=1` **only** in its ordinary
  language/standard-library regression-test job. This is never production
  qualification and must not be copied into a shipped executable.
- `.github/workflows/std-http-qualification.yml` runs bounded portable
  API/compile-policy regressions on Linux, macOS and Windows, plus local
  network/HTTP/2 tests on Linux using the same explicit test-only override.
  Those reports record that the native dependency is **not** production
  qualified. Platform-specific *native* qualification remains separate.
- The same workflow's pinned-libcurl security, fuzzing, sanitizer and load
  jobs run only on `workflow_dispatch`. They use a verified libcurl build and
  **must not** set `VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL`.
- `.github/workflows/std-http-overnight.yml` has only `workflow_dispatch`:
  **there is no push, PR or cron trigger for the 12-hour endurance run**.
  A release maintainer must start it deliberately on a configured self-hosted
  runner. No 12/24-hour soak is part of a normal commit's test pipeline.
- The independent stable-release evidence gate keeps its native provenance,
  security-review, extended-endurance and platform requirements. Passing
  ordinary commit tests is not a substitute for those signoffs.

`tests/test_std_http_ci_workflows.py` checks these trigger and override
boundaries to protect against accidental CI regressions. To reproduce the
local development-test environment with an older libcurl, run the bounded
qualification with the explicit override and record that its native
qualification status is false. To certify a release, select and verify a
shipping libcurl >=8.23.0 instead; never use the override.
