#!/usr/bin/env bash
# Fail-closed HTTP/2 release qualification. All mandatory checks must complete
# successfully on the supported Python runtime; unavailable tools are blockers.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src:.

python_cmd="${VALIANCE_PYTHON:-python3.14}"
command -v "$python_cmd" >/dev/null || {
  echo "BLOCKED: Python 3.14 executable unavailable: $python_cmd" >&2; exit 2;
}
"$python_cmd" -c 'import sys; assert sys.version_info >= (3, 14), sys.version'
command -v openssl >/dev/null || { echo 'BLOCKED: openssl unavailable' >&2; exit 2; }
command -v curl >/dev/null || { echo 'BLOCKED: curl unavailable' >&2; exit 2; }
command -v h2spec >/dev/null || { echo 'BLOCKED: h2spec unavailable' >&2; exit 2; }
command -v uv >/dev/null || { echo 'BLOCKED: uv unavailable' >&2; exit 2; }
"$python_cmd" -c 'import h2, hpack, h11, pytest, textual'

# The lockfile MUST include the complete declared dependency graph, including
# h2/hpack, before release. --check is read-only and cannot silently upgrade it.
uv lock --check
modules=(
  tests.test_std_web_v22_http2
  tests.test_std_web_v23_http2_rfc
  tests.test_std_web_v24_http2_acceptance
  tests.test_std_web_v25_http2_supervision
  tests.test_std_web_v26_http2_deadlines
  tests.test_std_web_http2_message_lifecycle
  tests.test_std_web_http2_completion_field_budgets
  tests.test_std_web_http2_early_response_reclamation
  tests.test_std_web_http2_multipart_equivalence
  tests.test_std_web_http2_header_fragment_fuzz
  tests.test_std_web_http2_outgoing_supervision
  tests.test_std_web_http2_stream_queue_budget
)
for module in "${modules[@]}"; do
  "$python_cmd" -m unittest -q "$module"
done
"$python_cmd" -m unittest -q "${modules[@]}"
"$python_cmd" -m pytest -q tests

scratch=$(mktemp -d)
pid=''
cleanup() {
  if [[ -n "$pid" ]]; then
    kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  rm -rf "$scratch"
}
trap cleanup EXIT
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$scratch/key.pem" -out "$scratch/cert.pem" -days 1 \
  -subj /CN=localhost >/dev/null 2>&1
"$python_cmd" scripts/http2_conformance_fixture.py \
  --cert "$scratch/cert.pem" --key "$scratch/key.pem" \
  --ready-file "$scratch/port" > "$scratch/server.log" 2>&1 &
pid=$!
for _ in $(seq 1 100); do
  [[ -s "$scratch/port" ]] && break
  kill -0 "$pid" 2>/dev/null || break
  sleep .05
done
[[ -s "$scratch/port" ]] || {
  cat "$scratch/server.log" >&2
  echo 'FAILED: HTTP/2 fixture did not start' >&2
  exit 1
}
port=$(< "$scratch/port")
# --http2 can negotiate down to HTTP/1.1: require actual HTTP/2 in the result.
http_version=$(curl -k --http2 --noproxy '*' -fsS --max-time 8 \
  -w '%{http_version}' -o "$scratch/get-body" "https://localhost:$port/")
[[ "$http_version" == '2' ]] || {
  echo "FAILED: ALPN selected HTTP/$http_version rather than HTTP/2" >&2
  exit 1
}
grep -Fxq 'valiance-h2-ok' "$scratch/get-body"
post_version=$(curl -k --http2 --noproxy '*' -fsS --max-time 8 \
  -X POST --data-binary 'h2-qualification-payload' \
  -w '%{http_version}' -o "$scratch/post-body" "https://localhost:$port/")
[[ "$post_version" == '2' ]] || {
  echo "FAILED: POST selected HTTP/$post_version rather than HTTP/2" >&2
  exit 1
}
grep -Fxq 'valiance-h2-ok' "$scratch/post-body"
# A separate Go net/http HTTP/2 implementation checks multiplexing and binary
# POST flow control against the same TLS fixture; no external Go modules used.
command -v go >/dev/null || { echo 'BLOCKED: Go independent client unavailable' >&2; exit 2; }
GOPROXY=off GOTOOLCHAIN=local go build -o "$scratch/go-interop" scripts/http2_go_interop.go
"$scratch/go-interop" "https://127.0.0.1:$port/"
# h2spec covers RFC 7540/7541 vectors, not every RFC 9113 requirement.
h2spec -t -k -S -h 127.0.0.1 -p "$port"
echo 'HTTP/2 release gate PASSED (including independent strict h2spec)'
