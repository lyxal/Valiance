#!/usr/bin/env bash
# Repeatable HTTP/2 engineering verification on the available Python runtime.
# Unlike qualify_http2_release.sh, this intentionally does NOT certify full
# RFC 9113 conformance, the Python 3.14 deployment matrix, or the entire repo.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src:.
python_cmd="${VALIANCE_PYTHON:-python}"
command -v "$python_cmd" >/dev/null || { echo "Missing Python: $python_cmd" >&2; exit 2; }
"$python_cmd" -c 'import h2, hpack, h11'
command -v openssl >/dev/null || { echo 'Missing openssl' >&2; exit 2; }
command -v curl >/dev/null || { echo 'Missing curl' >&2; exit 2; }
command -v go >/dev/null || { echo 'Missing independent Go HTTP/2 client compiler' >&2; exit 2; }

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
"$python_cmd" -m unittest -q "${modules[@]}"

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
  -subj '/CN=localhost' >/dev/null 2>&1
"$python_cmd" scripts/http2_conformance_fixture.py \
  --cert "$scratch/cert.pem" --key "$scratch/key.pem" \
  --ready-file "$scratch/port" > "$scratch/server.log" 2>&1 &
pid=$!
for _ in $(seq 1 100); do
  [[ -s "$scratch/port" ]] && break
  kill -0 "$pid" 2>/dev/null || break
  sleep .05
done
[[ -s "$scratch/port" ]] || { cat "$scratch/server.log" >&2; exit 1; }
port=$(< "$scratch/port")
url="https://127.0.0.1:$port/"
version=$(curl -k --http2 --noproxy '*' --max-time 10 -fsS \
  -w '%{http_version}' -o "$scratch/body" "$url")
[[ "$version" == '2' ]] && grep -Fxq 'valiance-h2-ok' "$scratch/body" || {
  echo 'curl/nghttp2 failed HTTP/2 GET' >&2; exit 1;
}
version=$(curl -k --http2 --noproxy '*' --max-time 10 -fsS \
  --data-binary 'h2 transport test' -w '%{http_version}' \
  -o "$scratch/body" "$url")
[[ "$version" == '2' ]] && grep -Fxq 'valiance-h2-ok' "$scratch/body" || {
  echo 'curl/nghttp2 failed HTTP/2 POST' >&2; exit 1;
}
# Go net/http uses an independent implementation from nghttp2 and hyper-h2.
GOPROXY=off GOTOOLCHAIN=local go build -o "$scratch/go-interop" scripts/http2_go_interop.go
"$scratch/go-interop" "$url"
if command -v h2spec >/dev/null; then
  h2spec -t -k -S -h 127.0.0.1 -p "$port"
  echo 'Independent h2spec acceptance PASSED (this remains an engineering check).'
else
  echo 'DEFERRED: h2spec not installed; RFC 7540/7541 conformance NOT certified.' >&2
fi
echo 'HTTP/2 LOCAL ENGINEERING CHECK PASSED — not an RFC 9113 completion certificate.'
