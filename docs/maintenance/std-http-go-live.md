# `std.http` go-live release gates (20 November 2026)

**Status: NOT approved for a stable production release.** Python 3.14 and cross-platform validation are intentionally owner-deferred. Independent review, shipping-library sanitizer testing and a real 12-hour campaign also remain unperformed in this environment. A passing local test suite is not a signoff.

## 1. Native libcurl security baseline — enforced

`std.http` now fails at transport startup unless the **actual loaded libcurl is 8.23.0 or newer** and provides TLS and HTTP/2. This deliberately does not try to evaluate every distro-specific backport. CVE-2026-92392 affects upstream 8.12.0–8.22.0, with some patched maintenance branches; the strict baseline is intentionally easier to audit. Source: https://curl.se/docs/CVE-2026-92392.html.

For a verified source build on Linux with CMake, OpenSSL and nghttp2 development packages:

```shell
python tools/build_std_http_libcurl.py --prefix /opt/valiance-curl-8.23 --report curl-build.json
export VALIANCE_LIBCURL_PATH=/opt/valiance-curl-8.23/lib/libcurl.so
python tools/check_std_http_native.py --report native-dependency.json
python tools/audit_std_http_packaging.py --bundled-library "$VALIANCE_LIBCURL_PATH" \
  --license-file /opt/valiance-curl-8.23/licenses/libcurl-COPYING.txt --report packaging.json
```

The source archive is downloaded over HTTPS and **verified against a pinned SHA-256** (`8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5`) before extraction. It is not necessary to use the environment override if an appropriate system version is already installed. Tests MUST confirm that the loaded `.so`/`.dylib`/`.dll` is the intended build; installing the `curl` CLI alone does not update `libcurl` loaded by Python.

`VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL=1` is only for local development with *untrusted* dummy peers. It deliberately bypasses the startup guard, and **both local release-evidence validation and stable production gates reject results produced with the bypass**. Never set it for applications or public services. The `VALIANCE_LIBCURL_PATH` override must be an existing absolute library file; no relative dynamic-library search path override is accepted.

## 2. Mandatory qualification

1. Run the **exact release archive**, not just source-tree tests. Run `tools/verify_std_http_release_archive.py` against the ZIP, with `--require-native` on a host that has the shipping libcurl configured.
2. Run `tools/qualify_std_http.py` with no insecure libcurl override, then the HTTP/1 and HTTP/2 fuzz corpora and the independent malformed-peer tests.
3. Run actual **instrumented shipping libcurl and TLS dependencies** with the Valiance Python ctypes binding, cancellations, reactor crashes, uploads, and client shutdown. The C probe's ASan/UBSan result alone instruments only its own code. Capture native symbol/build provenance and sanitizer logs; instrumented binaries must not be redistributed as production release binaries. Run `tools/qualify_std_http_instrumented.py --library /path/to/sanitized/libcurl.so --report native-binding.json`, or the `linux-instrumented-libcurl-bindings` CI job. Its report explicitly states when TLS/nghttp2 remains uninstrumented.
4. Run a **12–24-hour mixed-load endurance campaign**, explicitly observing HTTP/1.1 and HTTP/2, streaming uploads, downloads, client pooling, cancellation, task scope exit, and VM shutdown. Capture intermediate RSS, native allocation tracking, file descriptors, thread counts, buffer reservations, and error/latency trends. The existing `tools/soak_std_http.py --duration-seconds 43200 ...` is an HTTP/1.1 portion of this requirement and is *not* a substitute for the mixed-protocol portion.
5. Obtain **independent security approval** on the exact source revision and native binaries, including DNS rebinding/SSRF, proxy bypass, custom credentials on redirects, TLS failures, cookie scopes, compression bombs, malformed HTTP/2 and native callbacks. Record open findings and disposition. The optional network IP policy is not a security sandbox: combine it with an OS/container egress firewall for untrusted destinations.
6. Validate clean installation and trust stores, native dependency ownership, licenses, update/incident procedures, rollback and crash reporting on **each supported target**. Python 3.14 and the Linux/macOS/Windows matrix are owner-deferred.
7. Complete `tools/check_std_http_stable.py --evidence release-evidence.json --report stable-decision.json`. Missing independent attestations, uninstrumented libraries, incomplete platform data or short soaks cause **failure**, by design. Do not mark all fields true based on automated test completion.

## 3. API freeze and release mechanics

The `http.Client`, `ClientConfig`, `RequestBody`, `ResponseBody`, `http.send/read`, and `json/jsonAs[T]` public contracts are in `http.vlnc`. Freeze names, stack effects, ownership, default redirect behavior, cancellation, deadlines, error kinds, and streaming limits before stable labeling. Re-run all documentation compiler examples against the final tag. A release candidate must not advertise missing features as complete.

Publish release source ZIP and reproducible build report with SHA-256, SBOM/native dependency hashes, license files for every redistributed library, CA store source, and minimum feature requirements. Version the `std.http` module with the Valiance release; pin the upstream curl source version+hash and monitor upstream security advisories.

## 4. Preview, rollback, incident procedures

Before a stable release, use a small opt-in deployment that reports crash traces, transport errors, cancellation failures, excessive header/body limits, unexpected redirects and connection budget exhaustion. Collect no raw credentials or response bodies in diagnostics.

Rollback rehearsal: deploy the signed previous application build behind a feature flag, disable HTTP requests or route them to an independently maintained client if native security is suspect, verify all active transfers shut down and native handles are released, and confirm telemetry recovers. **Never roll back to a known-vulnerable libcurl** merely to restore availability. Archive the failing native binary hash and incident evidence, and publish an advisory when applicable.

## 5. Evidence interpretation

- Passing local Python 3.13 tests with the development override demonstrates code compatibility only. It does **not** authorize a production release.
- A C ASan/UBSan probe or Python tracemalloc is useful but does **not** instrument binary-only libcurl/OpenSSL/nghttp2 code.
- The actual shipping libcurl 8.23.0 library was not obtainable in the local offline environment for this development session. The pinned build script is prepared for a networked runner; the compiled artifact has not been produced or verified here.
- Independent security approval is a human/outside-team requirement. Generating an audit checklist or writing local adversarial tests cannot self-issue that approval.
