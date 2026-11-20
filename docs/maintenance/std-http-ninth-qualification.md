# Valiance `std.http` — Ninth implementation and bounded qualification report

**Status:** Release-hardening implementation; **not qualified for stable release**.
**Source:** Cumulative implementation through round eight, with ninth-round updates.
**Verification environment:** Linux-6.18.57-x86_64-with-glibc2.41; Python 3.13.5.
**Local result:** 267 completed tests passed across 6 suites, no skips, failures or test timeouts. Python 3.13 is used with the `--allow-unsupported-python` development override; the project's required runtime is Python >=3.14.

## Implemented in this round

1. **File-to-network streaming through the standard API.** Added `std.file.ReadFile.readChunk(count)`, a bounded (1..1,048,576 bytes) incremental read that returns an exclusive reader and `Some` nonempty tagged byte data, or `None` at EOF. An ordinary short final chunk is not an error. It refuses mixing decoded text with binary reads. Errors close the native file handle. No whole-file buffer is required.
2. **Tested multipart file upload using cooperative Valiance tasks.** A producer owns the nonduplicable `ReadFile`, reads in chunks, and writes into the existing bounded `http.UploadPipe` while another task sends `MultipartPart.StreamFile`. The end-to-end local server check runs in both regular and optimized/serialized execution and checks for leaked file handles.
3. **Bounded qualification runner.** Added `tools/qualify_std_http.py`, which executes each suite in a separate process group, enforces a strict per-suite deadline, kills timed-out descendants, produces JSON evidence, and refuses to qualify suites with no tests or skips.
4. **Regression for detached test descendants.** Fixed the case where a unittest parent had already exited but its descendant kept stdout open: termination now kills the entire POSIX process group rather than returning after a successful parent `poll()`. Added a reproducer to `tests/test_http_qualification.py`.
5. **Cross-platform qualification workflow.** Added `.github/workflows/std-http-qualification.yml` for Linux, macOS and Windows using Python 3.14. The workflow is present in the archive, but **these CI jobs have not been executed in this environment**. Updated maintenance and standard-library documentation.

## Completed bounded local tests

| Suite | Tests | Result | Elapsed |
|---|---:|---|---:|
| `tests.test_std_http` | 92 | passed | 13.25 s |
| `tests.test_std_http_http2` | 7 | passed | 1.326 s |
| `tests.test_std_file_integration` | 40 | passed | 9.434 s |
| `tests.test_concurrency_execution` | 35 | passed | 1.89 s |
| `tests.test_std_web` | 88 | passed | 79.544 s |
| `tests.test_http_qualification` | 5 | passed | 2.036 s |
| **Total** | **267** | **Passed** | |

The machine-readable results (including runtime, exit status, skip count, test tail and actual timeouts) are in `docs/maintenance/std-http-ninth-qualification.json` inside the archive and in the separately supplied `std-http-ninth-qualification.json` file. The local gate passed only under the explicitly recorded unsupported-interpreter override; this is **not** cross-platform release signoff.

## Remaining blockers to stable release

- Execute the new CI matrix successfully on Python 3.14 across the project's supported Linux, macOS and Windows targets, with reproducible libcurl and TLS dependency/features; verify actual artifacts from CI rather than assuming workflow success.
- Complete full native/VM memory accounting beyond existing upload and queued-response budgets, with instrumented lifetime/leak and fault-injection tests under cancellation and shutdown.
- Perform independent RFC-oriented HTTP/2 protocol and security conformance, proxy/cookie/DNS-rebinding and redirect/TLS adversarial testing, including full packet framing and stream reset cases.
- Verify timeouts under hostile DNS/TLS/network stalls and do large-file/channels I/O stress testing, plus high-concurrency performance baselines.
- Complete the original phase-by-phase conformance matrix, broader language regression suite, security review, package licensing, API-freeze review and supported-platform qualification.

**Conclusion:** The reported stuck subprocess is not left running. The file-backed streaming gap is closed for the tested path, and the qualification runner can recover from a stalled test and its descendants. This is substantial release-hardening progress, but it would be misleading to label `std.http` stable before the remaining mandatory gates pass.
