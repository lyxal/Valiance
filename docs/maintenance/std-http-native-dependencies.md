# Outbound HTTP native dependency and packaging contract

`std.http` dynamically loads the operating system's `libcurl` through
`ctypes.util.find_library("curl")`, or uses an explicitly selected native
library via `VALIANCE_LIBCURL_PATH=/absolute/path/to/libcurl` when the app
packages its own compatible binary. Relative overrides and missing files are
rejected before the dynamic loader is called. The public `std.http` API has no libcurl
handle values. `tools/check_std_http_native.py --report <path>` inspects the
**library this runtime actually loads**, not the `curl` executable. On Linux it
records the resolved mapped binary path, byte count and SHA-256 checksum so
packagers can pin a specific distribution artifact; other systems provide
that provenance when the selected filename is absolute. This matters since
those binaries can use different versions or TLS backends.

## Runtime requirements and enforcement

| Requirement | Enforcement | Implication |
|---|---|---|
| libcurl 7.68+ | `_curl()` startup version check | `curl_multi_wakeup`, multi poll and nonblocking request reactor |
| HTTP/2 feature | `CURL_VERSION_HTTP2` at startup | TLS ALPN and HTTP/2 support required by the design |
| TLS feature | `CURL_VERSION_SSL` at startup | Verifying HTTPS transports; `SSL_VERIFYPEER=1`, `SSL_VERIFYHOST=2` per transfer |
| Connection-ID introspection | libcurl 8.2+ checked when `connectionAcquireTimeoutMs` is configured | Accurate per-transfer connection-assignment deadline |
| Native callback ABI | ctypes ABI and callback pointer signatures | Needs platform-specific integration tests with the deployed library |
| Trust store and optional custom CA | libcurl's TLS backend/system configuration; optional `caBundle` | Package must provide and refresh certificates on every supported platform |
| Compression and HTTP/2 peer | libcurl's supported HTTP2 and content encodings | Decompressed response bytes remain subject to `maxBodyBytes` |

An unsupported native feature should result in a structured startup/setup
error rather than silently weakening TLS or falling back to a blocking HTTP
implementation. Disabled certificate verification is **not** a supported mode.

## Redistribution and maintenance review

This repository does **not** include or redistribute `libcurl` or its optional
TLS, IDN, HTTP/2, proxy and compression dependencies. Deployers using system
packages must ensure a compatible libcurl is available and that the system
trust store works in their runtime environment. If distributing libcurl or its
linked libraries with a product, review and comply with the licenses, notices
and update obligations for the **specific distributed build**. Do not treat
this project's Rust/Python licenses as automatically covering its native
libraries. Track upstream security advisories for the chosen TLS backend,
libcurl and HTTP/2 implementation; the dynamically loaded library is part of
the attack surface.

## Qualification procedure

1. Run the dependency inspector in the intended installation environment and
   save the JSON evidence with the tested package/version.
2. Run the independent HTTP/1.1 raw-peer and HTTP/2 TLS suites against that
   exact runtime environment. Validate certificate expiry, hostname mismatch,
   HTTP/2 resets/trailers/flow control/GOAWAY and checked resource cleanup.
3. Run `tools/profile_std_http_resources.py` and save its separate process-RSS,
   descriptor, Python traced-memory and budget evidence. Supplement it with
   native allocator/sanitizer profiling for memory-safety assurance.
4. Check the dependency's published license and security update policy when
   producing installers or containers; include applicable notices.
5. Do not claim validation on another OS or libcurl/TLS build solely because a
   different host passed these checks. Python 3.14/platform testing is tracked
   separately by the user.

The dependency inspector and growth profiler are **verification tools**, not
an exhaustive cross-platform packaging certificate or native-memory proof.

## Explicitly packaged binary (deployment workflow)

When a supported platform does not expose an appropriate libcurl through the
system loader, ship a compatible native library through the platform's own
package/install mechanism and set `VALIANCE_LIBCURL_PATH` to its **absolute**
file path before the first HTTP client starts. The override is intentionally
not a bare library filename (which could resolve from an attacker-controlled
working directory). The runtime still verifies libcurl >= 7.68, SSL and
HTTP/2 features and requires 8.2+ for connection-identity deadlines. It does
not silently downgrade network security or select a blocking HTTP backend.

Run the native inspector in the final installed environment and retain its
resolved binary hash, TLS backend, certificate store evidence and license
notices. Platform-specific redistribution and ABI validation remain separate
release gates; an absolute path alone does not constitute a packaged release.
