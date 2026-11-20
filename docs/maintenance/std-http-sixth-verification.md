# `std.http` sixth implementation increment — verification

**Release status:** experimental; **not** qualified as stable or production-safe.

This increment builds on `Valiance-std-http-fifth-continuation.zip` and retains the earlier transport, cooperative scheduler integration, request/response ownership, redirects, uploads, cookies, security controls, and HTTP/2 support.

## Changes

1. **Process-wide response queue budget.** In addition to `bodyBufferBytes` (per stream) and `maxClientBufferBytes` (per client), private `_PROCESS_BUFFER_BUDGET` limits the total retained response chunks of *all clients* to 64 MiB by default. Reservation is atomic across the client and process counters. When a client frees queue space, reactors waiting on the process budget are woken and can resume paused libcurl transfers. Client registration uses weak references. This is a **queued-body-byte** bound, not a total native heap/RSS limit: libcurl buffers, chunks already handed to callers, request uploads, Python object overhead, and other VM state are excluded. This experimental limit is internal and not exposed as a public configuration knob.
2. **Optional connection-acquisition deadline.** `ClientConfig.connectionAcquireTimeoutMs` maps to `connection_acquire_timeout_ms` in the private Python configuration. It limits elapsed monotonic time from request admission/redirect hop until libcurl associates a connection with a transfer. This includes queued pool waiting **and** DNS/socket setup; it does not separately measure pure pool queueing. The feature relies on `CURLINFO_CONN_ID` and therefore requires libcurl >= 8.2 if enabled. Older versions fail client creation with a structured `Unsupported` startup error instead of silently ignoring the option. Its deadline is observed at the reactor's 100 ms polling cadence, not a hard real-time interrupt. It does not change the existing connect/total/read-idle/write-idle limits.
3. **Resource and HTTP/2 regression coverage.** Tests verify cross-client budget contention, abandonment/release wakeups, queued connection deadline expiration and reuse recovery, Valiance configuration compilation in interpreted and serialized/optimized execution, and connection-acquisition checks with multiplexed HTTP/2 streams. A 1,000-simultaneous-request stress case checks that host thread count stays bounded and that cancelled queued requests release admission permits and response resources. The HTTP/2 fixture's pooled-connection assertion now measures connections created by its own test rather than counting connections created by earlier tests in the class.

## Files changed

- `src/valiance/std/_http_core.py`
- `src/valiance/std/_http_transport.py`
- `src/valiance/std/http.py`
- `src/valiance/std/http.vlnc`
- `tests/test_std_http.py`
- `tests/test_std_http_http2.py`
- `docs/Valiance-std-http-design-specification-v1.0.md`
- `docs/maintenance/std-http.md`
- This verification note.

## Verification performed

From the repository root with `PYTHONPATH=src:tests:.`:

| Command or scope | Result |
|---|---|
| `python -m unittest tests.test_std_http tests.test_std_http_http2 -q` | **82 passed**, including independent h2 TLS peer and 1,000-transfer stress case |
| `python -m unittest tests.test_concurrency_execution tests.test_concurrency_vm_suspension tests.test_programs tests.test_reference_docs.ReferenceDocumentationTests.test_json_reference_retains_overload_docs_and_hides_native_hooks tests.test_reference_docs.ReferenceDocumentationTests.test_every_reference_overload_has_an_example -q` | **89 passed** |
| `python -m unittest tests.test_std_web -v` | **88 passed**, approximately 83 seconds, recorded in `std-web-verbose.log` in local build workspace |
| `python -m compileall -q` of changed Python files | Passed |
| **Distinct completed tests** | **259 passed** |

An earlier combined regression command used an incorrect reference-test class name and produced a test-loader error; the corrected selection passed. A preliminary 1,000-transfer stress assertion found that two already-delivered response bodies still held unread chunks; those bodies are caller-owned and must be explicitly closed. The finalized stress test closes them and verifies no queue bytes remain. A preliminary HTTP/2 test run exposed a fixture-order-dependent connection counter; the finalized test measures only the current test and all 82 HTTP tests passed. These intermediate failures are **not** recorded as passing runs.

**Test environment:** Linux, Python 3.13.5, system libcurl 8.14.1. The repository declares a Python >=3.14 requirement; testing here does not replace target-runtime qualification. The earlier full reference-example analysis, full repository-wide suite, Windows/macOS runs, sanitizer/fuzzer runs, and resource/RSS measurement were not completed in this increment.

## Remaining release gates

- End-to-end VM/native allocation accounting, including active upload queues, caller-held data, libcurl allocations, and multi-client lifecycle stress; hard RSS/memory ceilings and deterministic cleanup under abnormal VM exit.
- Strictly *queue-only* connection acquisition timing, if required as a separate policy; connect-timeout versus pool-wait semantics and high-resolution deadlines on all supported libcurl versions.
- Streaming multipart file uploads and comprehensive protocol, TLS/proxy and cookie policy conformance/fuzz suites.
- Adversarial egress/DNS rebinding test matrix for the opt-in socket policy; HTTP/2 h2spec/fuzz qualification and supported-platform (Linux/macOS/Windows) testing.
- Python 3.14 packaging, libcurl distribution/feature/license review, full reference-example execution, complete repository CI, API freeze and Phase 9 release approval.

**Conclusion:** Sixth experimental increment implemented and verified locally. Do not mark the original ten-phase specification complete.
