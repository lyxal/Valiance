"""Compile/run a native ASan/UBSan libcurl multi probe and check ctypes ABI.

The probe is built with system headers, and its own C code is instrumented.
A system libcurl/OpenSSL distributed without sanitizers is *not* thereby
sanitized. No native memory-safety certification is claimed from this check.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

# Source-tree invocation also works without an editable project install.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for directory in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from valiance.std._http_transport import _CurlSockAddr, _curl, _libcurl_version_supported


class Peer(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b'valid' * 400
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, BrokenPipeError):
            pass


def run(rounds: int, *, sanitizer: bool = True) -> dict:
    compiler = shutil.which('gcc') or shutil.which('clang')
    record = {'platform': platform.platform(), 'python': platform.python_version(),
              'compiler': compiler, 'sanitizer_requested': sanitizer,
              'native_dependency_instrumented': False,
              'scope': 'native C multi probe and libcurl ABI; no coverage of Python ctypes callback code or uninstrumented shared libraries'}
    if not compiler:
        return dict(record, status='unavailable', reason='no system C compiler')
    src = Path(__file__).with_name('native_std_http_probe.c')
    with tempfile.TemporaryDirectory(prefix='valiance-native-http-') as directory:
        exe = Path(directory) / 'probe'
        options = [compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-g',
                   '-fno-omit-frame-pointer']
        if sanitizer:
            options += ['-fsanitize=address,undefined']
        options += [str(src), '-o', str(exe), '-lcurl']
        compiled = subprocess.run(options, capture_output=True, text=True, timeout=40)
        record['compile_command'] = options
        if compiled.returncode:
            return dict(record, status='failed', stage='compile',
                        output=(compiled.stdout + compiled.stderr)[-3000:])
        server = ThreadingHTTPServer(('127.0.0.1', 0), Peer)
        server.daemon_threads = True
        server.block_on_close = False
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            env = os.environ.copy()
            if sanitizer:
                env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1:abort_on_error=0'
                env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
            executed = subprocess.run([str(exe), f'http://127.0.0.1:{server.server_port}/',
                                       str(rounds)], env=env, capture_output=True,
                                      text=True, timeout=max(45, rounds * 2))
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        record['returncode'] = executed.returncode
        record['stderr_tail'] = executed.stderr[-4000:]
        try:
            result = json.loads(executed.stdout.strip())
        except json.JSONDecodeError:
            return dict(record, status='failed', stage='execution', stdout=executed.stdout[-2000:])
        record['native_results'] = result
        curl_info = _curl().curl_version_info(0).contents
        python_curl_version = curl_info.version.decode('utf-8', 'replace')
        record['secure_libcurl_baseline_met'] = _libcurl_version_supported(curl_info.version_num)
        # Distinguish instrumenting our tiny probe from instrumenting the
        # actual libcurl shared object. TLS/HTTP2 libraries are separate.
        libcurl_path = os.environ.get('VALIANCE_LIBCURL_PATH')
        native_ldd = ''
        if platform.system() == 'Linux' and libcurl_path and Path(libcurl_path).is_file():
            mapped = subprocess.run(['ldd', libcurl_path], capture_output=True,
                                    text=True, timeout=15, check=False)
            native_ldd = mapped.stdout + mapped.stderr
        record['native_dependency_instrumented'] = (
            ('libasan.' in native_ldd or 'libclang_rt.asan' in native_ldd)
            and ('libubsan.' in native_ldd or 'libclang_rt.ubsan' in native_ldd)
        )
        record['tls_dependency_instrumented'] = False

        abi = {
            'address_offset_python': _CurlSockAddr.addr.offset,
            'address_offset_c': result['curl_sockaddr_addr_offset'],
            'curl_off_t_size_c': result['curl_off_t_size'],
            'curl_off_t_size_python': ctypes.sizeof(ctypes.c_longlong),
            'curl_sockaddr_size_python': ctypes.sizeof(_CurlSockAddr),
            'curl_sockaddr_size_c': result['curl_sockaddr_size'],
            'runtime_libcurl_version_c': result.get('runtime_libcurl_version'),
            'runtime_libcurl_version_python': python_curl_version,
            'native_library_loaded': str(_curl()._name),
        }
        record['abi'] = abi
        valid = (executed.returncode == 0 and result['status'] == 'passed'
                 and abi['address_offset_python'] == abi['address_offset_c']
                 and abi['curl_off_t_size_c'] == abi['curl_off_t_size_python']
                 and abi['curl_sockaddr_size_c'] == abi['curl_sockaddr_size_python']
                 and abi['runtime_libcurl_version_c'].split(' ', 1)[0].removeprefix('libcurl/')
                 == abi['runtime_libcurl_version_python'])
        return dict(record, status='passed' if valid else 'failed')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=40)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--no-sanitizers', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.rounds <= 10000:
        parser.error('rounds must be between 1 and 10000')
    try:
        record = run(args.rounds, sanitizer=not args.no_sanitizers)
    except BaseException as exc:
        record = {'status': 'failed', 'exception': f'{type(exc).__name__}: {exc}'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2)[-4000:])
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
