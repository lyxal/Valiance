# Valiance `std.http` — Tenth continuation and local verification

**Status:** Substantial release hardening completed; **not qualified for stable release**.
**Source:** Cumulative implementation from the ninth repository archive, with tenth-round updates.
**Verification:** Linux, CPython 3.13.5 (development override; Valiance declares Python >=3.14).

## Implemented and reviewed

1. **Fail-closed reactor lifecycle.** An unexpected native-reactor exception now stops new admission, delivers terminal errors to active response readers and pending request futures, releases transfer leases and admission slots, and cleans up easy/multi handles. libcurl multi poll/perform errors are checked rather than ignored. An injected failure with an in-progress body and a queued send verifies terminal completion and capacity recovery.
2. **Native multi-handle race protection.** `curl_multi_wakeup` on cancellation, body close, resume and client shutdown is now synchronized with `curl_multi_cleanup`. Before this change the closed check and wakeup could race destruction of the native multi handle. A 24-iteration cancellation/shutdown stress test checks the lifecycle.
3. **Independent malformed-wire HTTP/1.1 tests.** Added a raw socket peer to test truncated `Content-Length` bodies, incomplete chunk framing, invalid status lines, invalid header field names, contradictory content lengths, 103 interim headers followed by a valid final response, and interim-only premature EOF. Failures must not become successful EOF or hang readers.
4. **Expanded qualification.** The bounded suite runner includes the new adversarial peer suite plus VM-suspension and general program regressions. The Python 3.14 GitHub Actions workflow is configured to trigger on the additional runtime/program tests.
5. **Portable qualification tests.** Eliminated the unconditional Windows skip in the process-tree test; it now tests live-parent tree termination on Windows and the parent-exited descendant scenario on POSIX. This **does not prove** post-parent-exit Windows containment; that gap is documented.
6. **Maintenance/conformance evidence.** Updated `docs/maintenance/std-http.md` and added `docs/maintenance/std-http-conformance-matrix.md` mapping implementation and evidence to every phase of the original plan.

No public `std.http` signature was changed in this round.

## Completed verification

**Nine bounded suites passed (328 tests, no failures, no skips, no timeouts)** in two separately recorded campaigns:

| Suite | Passing tests |
|---|---:|
| `tests.test_std_http` | 94 |
| `tests.test_std_http_http2` | 7 |
| `tests.test_std_http_adversarial` | 7 |
| `tests.test_std_file_integration` | 40 |
| `tests.test_concurrency_execution` | 35 |
| `tests.test_concurrency_vm_suspension` | 6 |
| `tests.test_programs` | 46 |
| `tests.test_std_web` | 88 |
| `tests.test_http_qualification` | 5 |
| **Total** | **328** |

Three additional targeted reference-documentation checks passed, for **331 passing tests/checks in distinct completed invocations**. Python syntax compilation of modified files passed. The raw JSON evidence is in `docs/maintenance/std-http-tenth-qualification.json` (a combined index containing both completed batches) and the standalone download. The original 7-suite and additional 2-suite source reports are also downloadable separately. This is local evidence only, not cross-platform release signoff.

## Still blocking stable release

- Execute and inspect the Python 3.14 Linux/macOS/Windows matrix in supported packaging environments; the workflow exists but its success has not been verified here.
- Complete instrumented native/VM heap accounting and leak checks (not just response/upload queue reservations); run ASAN/Valgrind where applicable and prove shutdown/cancellation races under fault injection.
- Expand independent HTTP/2 protocol/RFC and fuzz conformance, HTTP/1.1 malformed-response corpus, DNS/TLS/proxy/redirect adversarial security tests, and cross-platform native socket policy verification.
- Verify Windows process-tree containment if a parent dies before cleanup, long-running network stall behavior, large transfer performance baselines and native libcurl/TLS distribution and license requirements.
- Finish full repository and generated-reference qualification, API compatibility/security review and remaining original phase exit gates.

**Release judgment:** These changes close real failure and native-lifetime risks, and the completed local regressions pass. They do not establish the unverified system/security/platform claims needed to remove the experimental label honestly.
