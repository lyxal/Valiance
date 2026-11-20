# `std.http` release conformance matrix

**Status: not released as stable.** This is an evidence ledger for the original
`Valiance-std-http-design-specification-v1.0.md`, not a claim that the full
nine engineering phases plus Phase 0 are complete. "Local" means the actual
Linux/Python environment recorded by the matching qualification JSON. Tests
run under the Python 3.13 development override do **not** satisfy the declared
Python >=3.14 release requirement.

| Plan requirement | Implemented evidence | Release gap / disposition |
|---|---|---|
| 0. API and ownership specification | `docs/Valiance-std-http-design-specification-v1.0.md`; declarations in `http.vlnc` and checked compiler examples | Public API compatibility/security freeze review still required |
| 1. Validated types, duplicate headers, ownership | `test_std_http.HTTPValueTests`, `test_std_http.HTTPValianceTests`, filesystem integration | VM-wide byte representation and full native heap accounting remain open |
| 2. Cooperative cancellable external I/O | `runtime/external_io.py`, VM external wakeups, concurrency tests; reactor-failure injection and cancel/shutdown race tests | Formal state-space/race analysis and broader scheduler regression needed |
| 3. HTTP/1.1 and HTTP/2 transport | libcurl multi reactor; independent `h2` peer exercises GOAWAY, reset, flow-control windows, trailers and multiplexing; raw H1 peer | Formal h2spec/full RFC corpus and other-platform native build verification remain open |
| 4. Request/response semantics | Early headers, 4xx/5xx ordinary responses, method and framing tests | More RFC-level edge cases, trailers, proxy and connection-reuse testing |
| 5. Bounded response/upload streaming | `UploadPipe`, lazy multipart, shared response budgets, file `readChunk`; iterative aggregate body reader; response finalization and cancelled-read cleanup; repeat-client RSS/heap/FD profiler | ASan/Valgrind native allocator safety and long-duration production soak still open |
| 6. TLS, redirects, cookies, timeouts and security | Default TLS verification, opt-in redirect/cookies, destination-IP selection restriction; expired and name-mismatch TLS certificate rejection; 48-case truncated-wire corpus | Cross-origin header/body isolation, proxy-environment and cookie mutation tests plus seeded raw-wire campaign pass locally; full DNS/OS and HTTP/2 fuzz/PSL review remain outstanding |
| 7. Verb helpers and dynamic/typed JSON | `http.vlnc`, `http.py`, `std.json` integration and Valiance examples | Additional encoding interoperability and API freeze review |
| 8. Comprehensive verification | Bounded suite runner, independent H1/H2 peers, 1,000-request concurrency test, repeated shutdown and process RSS/FD/heap growth report | Reproducible HTTP/1.1 mutation corpus and bounded pooled-client throughput baseline implemented; native sanitizers, long-duration production soak and full HTTP/2 fuzz remain open |
| 9. Docs, CI and packaging | Maintenance guide, native dependency inspector, packaging/license checklist, reference metadata and qualification CI | Absolute-path native dependency loading and on-Linux mapped-binary SHA-256 inspection implemented; OS packaging/license signoff and full reference execution remain open |

## Evidence quality requirements

- A test present in the repository is **not** a passing test. Record completed
  suite outcomes with a machine-readable JSON report, command, Python version,
  elapsed time, skips and timeouts. A skip, absent suite, or subprocess timeout
  does not qualify as a pass.
- The raw HTTP/1.1 peer independently tests incomplete `Content-Length`,
  truncated chunk framing, conflicting lengths, malformed status/field names,
  and 103 interim response handling. It is not a protocol fuzzer.
- Reactor-failure tests inject an exception while a response reader and send
  are pending and verify they resolve terminally, admission slots recover,
  buffers are released and subsequent sends fail as closed. A separate test
  races shutdown and cancellation 24 times, exercising the multi-handle
  wakeup/cleanup lifetime lock.
- Resource budgets cover queued upload and response ownership, not arbitrary
  Python/Valiance values, all libcurl allocations, system socket buffers or
  all TLS decompression memory. Do not describe them as global RSS caps.
- The test runner's POSIX process-group cleanup covers descendants of an
  exited parent. On Windows the tree-kill strategy is exercised while the
  parent is alive; robust post-parent-exit Windows containment needs separate
  implementation and testing before asserting that guarantee.

## Stable-release signoff

**Not yet satisfied:** exhaustive native allocator/sanitizer evidence, full
HTTP/2/HTTP/1 fuzz and RFC conformance, formal security audit (SSRF/proxy/TLS,
PSL/cookies), runtime dependency licensing and packaging signoff on target
systems, cross-platform execution, benchmark baselines, and full generated
reference/documentation execution. Python 3.14 validation is explicitly
deferred to the user, not assumed to have passed. No stable-release
label should be applied until all are independently verified.

## Additional security/verification evidence (next release-hardening campaign)

- A pair of independent HTTP servers verifies that all caller-authored
  headers are stripped across origins, including custom authentication fields;
  same-origin headers remain intact. Cross-origin nonempty-body replay on
  301/302/307/308 is rejected, whereas 303 / conventional POST rewrites drop
  the body. Literal controls in Location are rejected before URL resolution.
- Opt-in non-public-IP filtering is tested with HTTP_PROXY/HTTPS_PROXY/
  ALL_PROXY variables pointing to a local attacker peer, and both destination
  and proxy receive zero requests. Additional deterministic IPv6 transition
  and mapped-address, cookie header mutation, cookie scope/PSL tests pass.
- `tools/fuzz_std_http_protocol.py` runs independently against the actual
  native reactor and records nine raw-wire response families and seed in JSON.
- `tools/soak_std_http.py` records cancellation, early-close, parallel request
  throughput/percentiles and post-shutdown resource reservations. Its metrics
  are local platform baselines, not a cross-platform or sanitizer certificate.

- A separate TLS HTTP/2 peer sends uppercase names, forbidden connection
  headers, missing :status and corrupt HPACK headers as *raw* frames after
  the HTTP/2 handshake. All cases must fail as HTTP errors with no remaining
  registered streams. These are representative malicious frames, not h2spec.
