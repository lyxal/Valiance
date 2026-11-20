# Valiance `std.http` — native, protocol, security and release-validation report

**Source:** cumulative Release Candidate 2 implementation plus the validation and CI hardening in this delivery.  
**Local platform:** Linux, Python 3.13.5 (explicit development override; **Python 3.14 deferred to maintainer**).  
**Local evidence gate:** **PASSED**. This is *not* a universal stable-release certificate.

## Implemented validation infrastructure

1. Expanded independent HTTP/2 hostile-wire tests from 4 to **20** malformed framing/HPACK/header families, including invalid stream IDs, interleaving/control-frame errors, pseudo-header ordering and padding.
2. Added seeded HTTP/2 black-box fuzzing against real TLS/ALPN peers (`tools/fuzz_std_http_http2.py`); independent multiplexing/cancellation stress (`tools/soak_std_http_http2.py`).
3. Added an AddressSanitizer/UndefinedBehaviorSanitizer **native C libcurl multi-handle probe**, including real request cancellation, pool reuse, and compiler-header versus Python `ctypes` ABI checks.
4. Added native packaging inspection (`tools/audit_std_http_packaging.py`) checking loaded library provenance, Linux dynamic dependencies, project licenses, and requiring explicit third-party license evidence for an explicitly bundled libcurl.
5. Added Python HTTP line/branch coverage, fail-closed release evidence gate (`tools/check_std_http_release.py`), machine-readable campaign merge with provenance, unit tests for both positive and rejected evidence, and 17-entry Valiance reference-example compiler checks.
6. Integrated dedicated Linux sanitizer/fuzz/security/coverage/soak CI gate alongside the existing 3-platform qualification matrix. These new CI jobs are configured, **not claimed to have run** here.

## Completed local verification

| Check | Result |
|---|---|
| Bounded regression qualification | **366 tests passed across 17 distinct suites**, 2 completed campaigns, no skips/failures/timeouts; all 88 `std.web` tests passed |
| HTTP/1.1 black-box protocol mutations | **1,800 / 1,800 passed** (seed 20261120) |
| HTTP/2 malformed-frame mutations | **240 / 240 rejected safely**, all 20 families covered |
| Independent HTTP/2 multiplex/stream lifetime soak | **3,000 requests submitted**, 2,770 reads completed, 131 cancellations, 99 early closes, 1 underlying peer connection |
| Longer HTTP/1.1 load soak | **90 seconds**, 91,863 submitted, 81,909 completed, 1 peak reactor thread, no unexpected errors |
| Native C sanitizer/ABI probe | **613 requests completed, 27 cancelled**, ASan+UBSan C probe passed with 0 reported errors |
| Client resource recovery | **810 requests**, RSS growth 4.27 MiB, net FD change 0, no remaining reactor threads or upload/download reservations |
| Native loader and local package audit | **passed**, libcurl 8.14.1, OpenSSL backend detected, library SHA-256 recorded; system-provided library (not vendored in archive) |
| Python HTTP module line/branch coverage | **86.65%** combined; **79.07%** branch; transport module 89.70% |
| Evidence gate | **passed**, 0 incomplete-evidence findings |

The regression count **does not** add fuzz cases or soak requests to unittest totals. The qualification evidence names both completed campaigns; it does not pretend the second reference-example check was part of the first run. The 90-second soak is a controlled local peer performance/lifetime test, not a multi-hour production-network test.

## What this validates—and what it cannot

**Verified here:** the full available HTTP/runtime/program/web regression selection; expanded independent TLS HTTP/2 failure cases, protocol mutation suites, request/response lifetime checks, direct file uploads, native C ABI compatibility, bounded load, local memory/FD/thread recovery, compilation of published HTTP examples, and reproducible Linux dependency provenance.

**Still requiring target-specific or independent signoff:**

- **Exact-shipping-libcurl/TLS native sanitizer build.** GCC ASan/UBSan instruments the C probe *only*, not the installed precompiled libcurl/nghttp2/OpenSSL libraries or Python `ctypes` callbacks. Use the real shipped native libraries built with sanitizers, or an equivalent external instrumented setup, before claiming allocator-safety certification.
- **Independent offensive network-security review.** The optional destination-IP restriction cannot be called an OS-wide SSRF sandbox. DNS/socket, proxy, redirects, cross-origin credentials, cookies and PSL behavior need review for the actual deployment network and platform.
- **Exhaustive RFC 9113/h2 client conformance.** The 20-frame-family corpus and independent multiplex peer are targeted, not exhaustive fuzzing. Server-oriented `h2spec` alone cannot certify an outbound HTTP client.
- **Multi-hour production-network stress** and platform-specific trust-store/ABI/binary-license certification. The local 90-second soak and Linux packaging inspection are not substitutes.
- **Python 3.14 and Windows/macOS qualification:** explicitly assigned to the user; configured CI is available but results were not generated here.

## Reproduction and files

All raw evidence is in `docs/qualification/std-http/current-validation/`, including `qualification-primary.json`, `qualification-reference.json`, merged `qualification.json`, coverage data, both fuzz corpora, both soaks, sanitizer C probe, resource profile, packaging/native manifests and `local-release-gate.json`.

Detailed commands, requirements and limitations: `docs/qualification/std-http/RELEASE_VALIDATION.md`. The Linux native/security GitHub Actions job fails closed if any required evidence is missing or below threshold; it is not a shortcut around the independent signoffs.

**Assessment:** local implementation and validation campaign passed. This is a significantly stronger release candidate; **full globally stable production qualification is not established** by one environment or by local automated tests alone.
