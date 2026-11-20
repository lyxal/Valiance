# Valiance `std.http` — release candidate 2 hardening report

**Deliverable:** cumulative repository based on `Valiance-std-http-release-hardening.zip`, with modified transport, independent security and protocol tests, a fuzzing campaign, a load/resource profiler, CI checks and reproducible JSON evidence. This is a **release candidate**, not a claim of fully certified stable release. Python 3.14 was expressly deferred to the user and was not tested here.

## Correctness and security fixes

1. **Prevented arbitrary custom-header credential leakage on cross-origin redirects.** The previous redirect code removed only a short header-name denylist. Any caller-defined header can contain a secret, so cross-origin redirects now drop *all* caller-provided headers. libcurl may regenerate ordinary transport-managed headers for the target. Same-origin redirects keep the caller's headers.
2. **Prevented cross-origin buffered body exfiltration on body-preserving redirects.** A 307, 308 or method-preserving 301/302 from a nonempty request body to a new origin now returns a structured `Protocol` error instead of uploading the original request body to a peer-chosen host. Same-origin body-preserving redirects still work. POST 301/302 and 303 rewrites still drop their body as expected.
3. **Reject malformed redirect locations before URL normalization.** Python URL joining strips some literal control bytes silently. The transport now rejects them *before* `urljoin`, and converts malformed IPv6 target parsing into a structured redirect error. Valid percent-encoded path octets remain permitted.
4. **Made native library packaging explicit and auditable.** `VALIANCE_LIBCURL_PATH` may point to an existing **absolute** library file to support product-specific native packaging. Relative paths, missing files and non-loadable libraries fail closed. This does not weaken SSL/HTTP2/version checks and must be set before the first HTTP client. The native dependency inspector now records the actual mapped libcurl path, byte size and SHA-256 where available.

### Compatibility consideration

The cross-origin 307/308 body-replay restriction is deliberately stricter than transparent HTTP redirection. Applications that intentionally wish to POST/PUT to another origin must make a new explicit request after approving that destination. This is an intentional safety contract and deserves an API-review signoff before a stable public release.

## Independent security and protocol verification added

- `tests/test_std_http_security_audit.py`: 11 tests covering two-origin redirect capture, arbitrary custom credential headers, body-replay denial, same-origin preservation, redirect origin allowlists, proxy environment isolation for the non-public-IP policy, mapped/transition IPv6 addresses, malformed redirect targets, cookie mutation/scope, and native loader path validation.
- `tests/test_std_http_http2_adversarial.py`: one focused test with four independent TLS HTTP/2 malformed-frame subcases (uppercase header name, forbidden `connection` field, missing `:status`, corrupt HPACK), all rejected without residual transfers.
- `tests/test_std_http_protocol_fuzz.py`: two tests verifying reproducible seeds and a 720-case live black-box regression campaign.
- `tools/fuzz_std_http_protocol.py`: configurable seed/count independent local raw-wire peer, with nine mutation families. The recorded expanded run covered **1,800 cases**, finished in **4.273 seconds**, and passed without unexpected error/success behavior or outstanding response/upload reservations.
- `tools/soak_std_http.py`: concurrent non-worker-per-request operation submission with intentional cancellations and early closes. A completed **15-second / 32-concurrency** local run submitted **13,938** requests, completed **12,428** full-body reads, cancelled **820** pending requests, intentionally closed **690** bodies, and recorded **814.38 completed reads/second**, p95 **52.02 ms**, p99 **75.87 ms** on this host. It reported **one peak HTTP reactor thread**, post-shutdown zero reactor threads and zero response/upload reservations, open file descriptor change -1, and RSS change +14,581,760 bytes. These are host-specific local baseline metrics, not production guarantees.

## Resource lifetime verification

The existing repeat-client process profiler was rerun with 135 clients and 810 requests after warmup. It **passed** with +5,771,264 bytes RSS, +54,351 bytes traced Python heap, zero net open file descriptors, zero retained HTTP reactor threads and zero upload/response reservations. This is a process growth test, **not** proof of native allocator correctness or ASAN/Valgrind coverage.

The actual local transport library was identified as libcurl **8.14.1** with OpenSSL **3.5.5**, SSL/HTTP2 feature bits and connection-ID support. The inspector recorded the loaded Linux file and its SHA-256 in `native-dependencies.json`. This data is build provenance, not a cross-platform or license review.

## Bounded regression qualification

**356 distinct tests across 12 suites passed** on Linux/Python 3.13.5 with the explicit development override; zero skips or timeouts in the accepted results. The evidence was combined from **two completed runs**, not misrepresented as one uninterrupted 12-suite campaign:

- Initial full bounded campaign: 355 tests across 11 suites, including all 88 `std.web` tests. All passed; the slow `std.web` suite completed in 143.468 seconds under its 165-second individual limit.
- A subsequent bounded rerun after final security fixes: the 100-test HTTP suite, the 11-test security suite, and the new one-test HTTP/2 adversarial suite all passed. The merged record chooses the latest result for each suite and records campaign provenance.

The source also passed targeted Python syntax compilation and a final direct run of the security/fuzz/adversarial HTTP2 tests (14 tests). The `tools/` tree contains an **unrelated pre-existing** `tools/terminal_editor/worker.py` Python syntax error, so no assertion of tree-wide `compileall` is made; modified Python modules compile successfully.

## Automated evidence in the repository

`docs/qualification/std-http/` contains:

- `local-regression.json`: merged per-suite results, durations, skipped count, campaign provenance and scope restrictions
- `protocol-fuzz.json`: complete seeded black-box mutation campaign results
- `local-soak.json`: request throughput/latency, cancellation and cleanup measurements
- `resource-profile.json`: repeated client RSS/heap/fd/thread reservation results
- `native-dependencies.json`: libcurl/TLS binary provenance

`tools/qualify_std_http.py` now includes the new security, protocol-fuzz and malicious HTTP/2 suites. `.github/workflows/std-http-qualification.yml` also executes the standalone seeded mutation campaign and bounded soak; these CI jobs have **not** been run in this environment on other operating systems.

## Remaining external/formal release signoffs

1. **Instrumented native allocator analysis** against the exact shipped libcurl/TLS builds with ASAN/LSan/Valgrind, including cancellation races. Neither a successful process RSS profile nor normal Python `ctypes` execution substitutes for this.
2. **Broader protocol conformance**, particularly full HTTP/2 malformed-frame/h2spec and sustained peer interoperability, not just the independent fixtures and deterministic targeted corpus in this project.
3. **Independent network security audit**, including OS-specific DNS/socket routing, proxies, complete cookie/PSL behavior and egress controls. The optional IP policy is not a full SSRF sandbox.
4. **Long-duration production-like soak** and network-latency/throughput comparison. The measured 15-second local concurrency profile is a useful baseline, not a multi-hour qualification.
5. **Native packaging/ABI, licensing and trust-store signoff** for target platforms. Absolute-path selection and mapped-binary hashing improve the audit trail but do not supply redistributable binaries.
6. **Python 3.14 testing** is expressly deferred to the user, as requested. No assertion that it passed is made.

**Assessment:** substantial real security fixes and independent tests; suitable to continue toward API freeze as a release candidate. Do not promote to an unqualified stable production release until the external signoffs are completed.
