# `std.http` release validation — instrumented local and CI campaigns

This validation program targets the user-supplied `std.http` implementation
plan. The **local evidence does not equal a global production certificate**:
Python 3.14 and other platforms are explicitly deferred to the maintainer,
while independent security review and instrumented builds of the exact shipped
native dependencies require access to those distributions and external auditors.

## New executable checks

| Area | Test/entry point | Required evidence | What it genuinely checks |
|---|---|---|---|
| Core regression | `tools/qualify_std_http.py` | `qualification.json` | Separate process groups, per-suite timeouts, no skipped/empty suites, cancellation and HTTP1/HTTP2/VM/web regressions |
| HTTP/1.1 mutated wire | `tools/fuzz_std_http_protocol.py` | `http1-fuzz.json` | >=1,800 seeded independent raw-wire peer cases with explicit expected success/failure and budget cleanup |
| Hostile HTTP/2 frames | `tools/fuzz_std_http_http2.py` | `http2-fuzz.json` | >=240 mutations in 20 malformed RFC 9113/HPACK frame families sent by an independently implemented TLS peer |
| HTTP/2 multiplex stress | `tools/soak_std_http_http2.py` | `http2-soak.json` | >=2,000 multiplexed requests to independent hyper-h2 TLS server, cancellations/early closes, connection reuse and cleanup |
| Native ABI + sanitizers | `tools/validate_std_http_native.py` | `native-asan.json` | GCC/Clang ASan+UBSan C libcurl multi harness, repeated native cancellations, C-versus-ctypes socket ABI layout |
| Process resource retention | `tools/profile_std_http_resources.py` | `resource-profile.json` | >=800 repeated requests, RSS, traced Python heap, FD count, native reactor thread count, global buffer reservations |
| Longer local soak | `tools/soak_std_http.py` | `long-soak.json` | >=90s/64 in-flight HTTP/1.1 attempts, cancellations, early closes and resource recovery |
| Native provenance | `tools/check_std_http_native.py` | `native-dependency.json` | Actual loaded libcurl absolute path + SHA-256, TLS/HTTP2 capabilities and version |
| Packaging audit | `tools/audit_std_http_packaging.py` | `packaging.json` | Linux shared dependency resolution, source license files, optional bundled binary/third-party copyright enforcement |
| Compiler-checked reference | `tests/test_std_http_reference_examples.py` | `qualification.json` | All 17 published `std.http` entries provide Valiance-analyser-checked examples |
| Python branch coverage | `tools/coverage_std_http.py` | `python-coverage.json` | 8 focused suites exercise the HTTP modules; minimum 80% combined lines/branches and 70% branches for local evidence |
| Explicit evidence gate | `tools/check_std_http_release.py` | `local-release-gate.json` | Refuses missing/incomplete suite results, truncated corpus, failed sanitizer, weak soak or incomplete packaging record |

The new `tests/test_std_http_http2_adversarial.py` probes 20 malformed
frame/header families (including invalid stream IDs, PING/SETTINGS frame
sizes, response pseudo-header order and HPACK errors). The
`tests/test_std_http_http2_fuzz.py` and `tests/test_std_http_http2_soak.py`
prove these campaign entry points work. Packaging and evidence-gate tests
include failure-path cases so a missing license or skipped suite cannot
silently satisfy the local release gate.

## Reproduction

From the repository root, install the project dependencies and make the
standard-library imports available with `PYTHONPATH=src:tests:.`. On Linux,
ensure a C compiler, the matching libcurl development headers, and ASan/UBSan
runtimes are installed. The following commands are **sequential** to avoid
spurious wall-clock failures from competing local stress workloads:

```bash
mkdir -p std-http-evidence
python tools/qualify_std_http.py --allow-unsupported-python --timeout-seconds 200 --report std-http-evidence/qualification.json
python tools/fuzz_std_http_protocol.py --seed 20261120 --count 1800 --report std-http-evidence/http1-fuzz.json
python tools/fuzz_std_http_http2.py --seed 20261120 --count 240 --report std-http-evidence/http2-fuzz.json
python tools/soak_std_http_http2.py --requests 3000 --concurrency 48 --report std-http-evidence/http2-soak.json
python tools/validate_std_http_native.py --rounds 80 --report std-http-evidence/native-asan.json
python tools/profile_std_http_resources.py --clients-per-batch 45 --batches 3 --report std-http-evidence/resource-profile.json
python tools/soak_std_http.py --duration-seconds 90 --concurrency 64 --report std-http-evidence/long-soak.json
python tools/coverage_std_http.py --report std-http-evidence/python-coverage.json
python tools/check_std_http_native.py --report std-http-evidence/native-dependency.json
python tools/audit_std_http_packaging.py --report std-http-evidence/packaging.json
python tools/check_std_http_release.py --evidence-dir std-http-evidence --report std-http-evidence/local-release-gate.json
```

The `--allow-unsupported-python` flag documents the current environment's
Python 3.13 development override. It does **not** pass Python 3.14 validation.
The CI workflow includes a dedicated Linux native/security job and a separate
Linux/macOS/Windows matrix. Neither unexecuted CI job constitutes local
platform evidence. Bundling libcurl explicitly requires passing
`audit_std_http_packaging.py --bundled-library /absolute/path --license-file
/absolute/path`, plus checking the native TLS backend's own license terms.

## Sanitizer limitations

The C probe is compiled using `-fsanitize=address,undefined` with leak
checking enabled. The C harness itself is instrumented; the host's prebuilt
libcurl, nghttp2, OpenSSL and Python interpreter are **not** retroactively
instrumented. This detects mistakes in the harness and some allocator-visible
boundary faults, verifies `curl_sockaddr` layout, and exercises native handle
cleanup, but does not certify the `ctypes` callbacks or the shipped TLS build.
Native release qualification still needs the *exact* shipping native libraries
compiled with compatible sanitizers and a full adapter stress run under those
libraries (or equivalent Valgrind instrumentation).

## What remains external

- Python 3.14, macOS and Windows results, deliberately left for maintainer CI.
- ASan/LSan builds of the exact redistributed native libraries; this runtime
  only has precompiled, unsanitized libcurl/OpenSSL.
- Independent offensive security audit for SSRF/private-network DNS/socket
  routing and proxy behavior across target operating systems, redirect/
  credential/cookie handling, and untrusted destinations. The IP filter is
  optional, not a guaranteed OS-wide sandbox.
- External HTTP/2 peer conformance/fuzzing beyond the deterministic 20-family
  corpus. `h2spec` primarily tests **servers**; it is not valid certification
  of Valiance's outbound HTTP/2 *client*.
- Production-network multi-hour soak, leak review, licensing/CA/trust-store
  verification and platform-native packaging validation.

Do not mark the original ten-phase stable-release exit gate completed merely
because a one-platform local test campaign passes. Evidence should be
associated with an exact commit/artifact hash before deployment.
