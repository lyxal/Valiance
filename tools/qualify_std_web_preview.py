#!/usr/bin/env python3
"""Fail-closed *practical preview* checks for std.web on HTTP/1.1 and HTTP/2.

This deliberately has a narrower scope than qualify_websocket_release.py:
ordinary HTTP routing, a real compiled Valiance app, WebSocket upgrades,
TLS/H2 multiplexing, and basic ingress/security behavior. No RFC certificate,
HTTP/3, full-repo language qualification or untrusted deployment approval.

Use --functional-only for local development under unsupported interpreters. It
may exit zero when its tests pass, but never changes preview_ready to true.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
QUICK = (
    "test_std_html.HtmlNodeTests.test_live_http_response_is_html",
    "test_std_html.HtmlNodeTests.test_html_node_survives_optimized_serialized_bytecode",
    "test_std_web_preview_smoke",
    "test_std_web.WebCoreTests",
    "test_std_web.WebHTTP1IntegrationTests",
    "test_std_web_v29_websocket_h2.H2WebSocketWireTests.test_rfc8441_settings_subprotocol_and_concurrent_http2_get",
    "test_std_web_websocket_interoperability",
    "test_std_web_websocket_ws12_handshake_vectors",
    "test_std_web_websocket_ws13_frame_corpus.FrameCorpus.test_invalid_unmasked_rsv_and_control_fragment",
    "test_std_web_websocket_ws13_frame_corpus.FrameCorpus.test_valid_empty_close_and_fragmented_utf8_split_boundaries",
)
EXTENDED = (
    "test_std_web_v29_websocket_h2",
    "test_std_web_v28_websocket_valiance",
    "test_std_web_v21_multipart_limits",
    "test_std_web_v22_http2",
    "test_std_web_v23_http2_rfc",
    "test_std_web_v24_http2_acceptance",
    "test_std_web_cors_tls",
    "test_std_web_compression",
    "test_std_web_websocket_heartbeat",
    "test_std_web_websocket_ws11_deflate",
    "test_std_web_ws04_ws05_cancellation",
    "test_std_web_ws08_ws11_resource_gates",
)
# Executable, practical coverage for the major non-WebSocket surfaces of the
# original specification. This is feature-smoke evidence, not RFC certification.
# Each selector runs in its own process: successful assertion output without a
# clean process exit is not a passing feature check.
API_SURFACE = {
    "typed_generated_html": (
        "test_std_html.HtmlNodeTests.test_live_http_response_is_html",
        "test_std_html.HtmlNodeTests.test_escaped_nested_elements_and_fragment",
        "test_std_html.HtmlNodeTests.test_bad_attributes_and_urls_are_rejected",),
    "application_error_hook": (
        "test_std_web.WebPublicErrorHookTests",),
    "json_and_typed_params": (
        "test_std_web_preview_smoke.FeatureDemoSmokeTests",),
    "route_body_policy": (
        "test_std_web_route_body_options.RouteBodyOptionsCoreTests",),
    "multipart_upload_quotas": (
        "test_std_web_v21_multipart_limits.MultipartLimitsCoreTests",),
    "streaming_and_sse_core": (
        "test_std_web_v16_events_trailers.EventCoreTests",),
    "static_ranges_and_confinement": (
        "test_std_web.WebStaticFileTests.test_static_ranges_head_validators_and_416_size",
        "test_std_web.WebStaticFileTests.test_static_path_confinement_and_symlink_rejection"),
    "compression_core": (
        "test_std_web_compression.CompressionCoreTests",),
    "cors_core": (
        "test_std_web_cors_tls.CorsCoreTests",),
    "tls_credential_rotation": (
        "test_std_web_cors_tls.HTTPSTransportTests.test_tls_credential_reload_atomic_and_alpn_preserved",),
    "server_observability": (
        "test_std_web_limits_shutdown.ValianceServerLimitsTests.test_public_server_stats_are_borrowed_and_read_only",),
    "h2_http_request_policy": (
        "test_std_web_v22_http2.HTTP2CoreTests",),
    "host_allowlist_and_proxy_validation": (
        "test_std_web_network_policy.NetworkPolicyTests.test_configuration_validation_and_authority_identity",
        "test_std_web_network_policy.NetworkPolicyTests.test_default_rejects_spoofing_by_ignoring_forwarding_fields",
        "test_std_web_network_policy.NetworkPolicyTests.test_forwarded_cidr_chain_and_conflict",
        "test_std_web_network_policy.NetworkPolicyTests.test_x_forwarded_hop_validation",),
    "live_h1_h2_authority_isolation": (
        "test_std_web_network_policy.NetworkPolicyTests.test_live_http1_allowlist_and_websocket_upgrade_isolation",
        "test_std_web_network_policy.NetworkPolicyHTTP2Tests.test_allowed_host_isolation_without_rejecting_entire_h2_connection",),
    "public_host_proxy_config_and_peer_identity": (
        "test_std_web_network_policy.NetworkPolicyTests.test_public_valiance_security_configuration",
        "test_std_web_network_policy.NetworkPolicyTests.test_compiled_request_client_identity_on_live_listener",),
    "explicit_protocol_selection": (
        "test_std_web_network_policy.NetworkPolicyTests.test_explicit_protocol_selection_and_unsupported_h3_fail_closed",
        "test_std_web_network_policy.NetworkPolicyHTTP2Tests.test_tls_http1_only_alpn_selection",),
    "existing_limits_and_policy_constructor_compatibility": (
        "test_std_web_network_policy.NetworkPolicyTests.test_limits_and_deployment_policy_can_be_combined_without_breaking_legacy_config",
        "test_std_web_limits_shutdown.ValianceServerLimitsTests.test_custom_limits_reach_real_listener_and_bytes_boundary",
        "test_std_web_limits_shutdown.ValianceServerLimitsTests.test_tls_and_limits_construct_together_without_starting_a_listener",),
}


# Use the *declared* runtime dependencies, not the narrower imports needed by
# a chosen fixture. A smoke check cannot qualify an incomplete installation.
DEPENDENCIES = ("h11", "h2", "brotli", "prompt-toolkit", "regex", "textual")


def _run_suite(name: str, timeout: int) -> dict:
    command = [sys.executable, "-m", "unittest", f"tests.{name}", "-q"]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT),
                                         env.get("PYTHONPATH", "")))
    env["PYTHONFAULTHANDLER"] = "1"
    started = time.monotonic()
    timed_out = False
    # A leaked grandchild may keep an inherited pipe open after the test
    # process is reaped. Use a temporary file instead of communicate() on a
    # pipe so a hung test cannot stall this release checker indefinitely.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=os.name == "posix")
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        log.flush()
        log.seek(0)
        output = log.read()
    matches = re.findall(r"Ran (\d+) tests? in", output)
    count = int(matches[-1]) if matches else 0
    skip = re.search(r"OK \([^)]*skipped\s*=\s*(\d+)", output)
    skips = int(skip.group(1)) if skip else 0
    succeeded = (process.returncode == 0 and not timed_out
                 and count > 0 and "OK" in output and skips == 0)
    return {
        "module": f"tests.{name}", "command": command,
        "exit_code": process.returncode, "timed_out": timed_out,
        "test_count": count, "skipped_count": skips,
        "passed": succeeded, "duration_seconds": round(time.monotonic() - started, 3),
        "output_tail": output[-4500:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path(".artifacts/std-web-preview-evidence.json"))
    parser.add_argument("--timeout-seconds", type=int, default=100)
    parser.add_argument("--extended", action="store_true",
                        help="Additional HTTP/TLS/WS/resource suites")
    parser.add_argument("--api-matrix", action="store_true",
                        help="Run broad practical original std.web API feature coverage")
    parser.add_argument("--api-only", action="store_true",
                        help="Run only the focused original-API feature matrix")
    parser.add_argument("--functional-only", action="store_true",
                        help="Exit based on tests only; never claim supported preview runtime")
    args = parser.parse_args(argv)
    if args.timeout_seconds < 5:
        parser.error("--timeout-seconds must be at least 5")
    deps = {}
    for name in DEPENDENCIES:
        try:
            deps[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            deps[name] = None
    version = tuple(sys.version_info[:3])
    supported_runtime = version >= (3, 14) and all(deps.values())
    api_check = args.api_matrix or args.api_only
    api_modules = tuple(dict.fromkeys(name for names in API_SURFACE.values()
                                      for name in names)) if api_check else ()
    suites = (api_modules if args.api_only else
              tuple(dict.fromkeys(QUICK + (EXTENDED if args.extended else ()) + api_modules)))
    report = {
        "scope": "std.web HTTP/1.1 + TLS/HTTP/2 practical preview; no HTTP/3",
        "python": sys.version,
        "python_supported": version >= (3, 14),
        "dependency_versions": deps,
        "runtime_supported": supported_runtime,
        "extended": args.extended,
        "api_matrix": api_check,
        "api_only": args.api_only,
        "functional_only": args.functional_only,
        "suites": [],
        "limitations": [
            "Not exhaustive RFC 9112/9113/6455 conformance",
            "No HTTP/3/QUIC support",
            "Not whole-repository language regression qualification",
            "Not a public-internet security certification",
            "Uncooperative in-process native callbacks cannot be forcibly terminated",
        ],
    }
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve partial results even if the outer invocation is interrupted.
    # An incomplete report is never a qualified release.
    report["suites_planned"] = list(suites)
    report["functional_passed"] = False
    report["preview_ready"] = False
    for name in suites:
        result = _run_suite(name, args.timeout_seconds)
        report["suites"].append(result)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"{name}: {'PASS' if result['passed'] else 'FAIL'} "
              f"({result['test_count']} tests, exit {result['exit_code']})", flush=True)
    checked = {item['module'].removeprefix('tests.'): item['passed']
               for item in report['suites']}
    report['practical_api_features'] = {
        feature: {
            'selectors': list(names),
            'passed': all(checked.get(name, False) for name in names),
        }
        for feature, names in API_SURFACE.items()
    } if api_check else {}
    # HTTP/3 and strict cross-platform conformance are deliberately not
    # inferred from basic smoke successes. Keep these facts machine-readable.
    report['not_implemented_or_not_qualified'] = [
        'HTTP/3 QUIC and HTTP/3 WebSockets',
        'Full public trusted-proxy, allowed-host, SNI and mutual-TLS matrix',
        'Exhaustive RFC 9112/9113/6455/8441 acceptance',
        'Cross-platform/native-TLS allocation and untrusted-deployment audit',
    ]
    report["functional_passed"] = len(report["suites"]) == len(suites) and all(
        s["passed"] for s in report["suites"])
    report["preview_ready"] = report["functional_passed"] and supported_runtime
    report["total_distinct_tests"] = sum(s["test_count"] for s in report["suites"])
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    decision = "PREVIEW CHECK PASSED" if report["preview_ready"] else (
        "FUNCTIONAL ONLY (unsupported toolchain / missing deps)" if report["functional_passed"]
        else "PREVIEW CHECK FAILED")
    print(f"{decision}; evidence: {output}", flush=True)
    if args.functional_only:
        return 0 if report["functional_passed"] else 1
    return 0 if report["preview_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
