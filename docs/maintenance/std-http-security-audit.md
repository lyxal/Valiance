# std.http outbound transport security review — controlled local evidence

This document is an audit trail of **tested local invariants**, not an independent
penetration-test certificate. It should be reviewed against the approved design
before public API freeze. All URLs exercised by the attack peers are local.

## Threat boundaries

- **Destination control:** Requests to untrusted URLs can cause SSRF. The
  default transport does not implement a sandbox; applications must enable
  `denyNonPublicIps`, `allowedOrigins`, or their OS egress policy explicitly.
  The IP option checks libcurl's selected socket address rather than resolving
  the hostname in a separate, vulnerable time-of-check path. It rejects private,
  loopback and selected transition-encoded addresses and disables proxies,
  including inherited environment settings. It cannot block all DNS metadata,
  unusual network routes, tunnels, platform-specific IP classifications or
  destinations reachable through a separate application-level relay.
- **Redirect authority:** HTTP servers can redirect to a different origin and
  cannot be trusted to keep credentials private. Redirects are disabled unless
  explicitly enabled. Across origins, all caller-supplied headers are removed,
  rather than trying to recognize a finite list of credential header names.
  A body-preserving redirect with a nonempty buffered body is rejected, not
  replayed to an attacker destination. 303 and conventional POST 301/302
  rewrites drop the body; same-origin 307/308 retain method and buffered body.
  Literal controls in Location are rejected before `urljoin` normalization.
- **Cookies:** Cookie storage is opt-in and per client. Scope is domain/path,
  expiry and Secure; `__Secure-` / `__Host-` rules are enforced. Domain cookies
  require a public-suffix provider or are rejected conservatively. The jar is
  deliberately a restrictive RFC 6265 subset, not a complete browser jar.
- **Hostile peers:** Response header count/size, decoded body size, response
  queue, upload queue, total deadline and active transfers have explicit
  bounds. A response stream reports truncation, reset and decompression limit
  errors rather than presenting partial data as successful EOF.
- **Native loading:** A packaged libcurl may be selected only by an existing
  absolute file path (`VALIANCE_LIBCURL_PATH`); it still must satisfy SSL,
  HTTP/2 and minimum version gates. The libcurl library is a native trust
  boundary and remains subject to its upstream security patch policy.

## Reproducible adversarial checks

1. `python -m unittest -q tests.test_std_http_security_audit` — two-origin
   redirect capture, credential/body isolation, proxy-environment regression,
   IP address classification, cookie mutation and loader override validation.
2. `python tools/fuzz_std_http_protocol.py --seed 20261120 --count 1800
   --report fuzz.json` — raw-wire black-box malformed HTTP/1.1 corpus, including
   truncated fixed/chunked bodies, invalid status/header fields, oversized
   headers, gzip expansion, valid error statuses and interim responses.
3. `python tools/soak_std_http.py --duration-seconds 15 --concurrency 32
   --report soak.json` — bounded concurrency, cancellation, intentional
   early close, full-body correctness, latency, reactor-thread and queue-budget
   recovery, RSS and file-descriptor deltas.
4. `python -m unittest -q tests.test_std_http_http2_adversarial` —
   independently crafted invalid HTTP/2 HPACK/header frames after TLS ALPN,
   rejected with resource cleanup.
5. `python tools/check_std_http_native.py --report native.json` — actual
   loaded libcurl version/features/TLS and, where accessible, a binary hash.
6. `python tools/qualify_std_http.py --timeout-seconds 165
   --allow-unsupported-python --report qualification.json` — local developer
   override only, one timed subprocess per suite with explicit pass/fail data.

## Signoff still requiring independent verification

- An independent network egress/security review across target OSes, DNS and
  proxies, including routing behavior and names with unusual canonical forms.
- Native ASan/Valgrind/LSan testing with instrumented libcurl/TLS builds.
  Process RSS profiling does not detect all use-after-free or native leaks.
- Full HTTP/2 malformed-frame/h2spec, HTTP/1.1 interoperability and broad
  structured protocol fuzzing beyond the targeted deterministic cases.
- Long-duration production traffic and multi-host throughput/soak comparison.
- Shipping/distributing native libcurl/TLS dependencies with their ABI,
  trust-store, versioning, license, security-update and platform checks.

Python 3.14 verification is separately deferred to the user and is not
represented as passing here.
