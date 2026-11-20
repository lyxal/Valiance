"""Reproducibly build a private, version-pinned libcurl with HTTP/2 support.

The curl-8.23.0.tar.xz SHA-256 is independently published in FreeBSD ports
and pkgsrc and pinned here; an invalid tarball is rejected before extraction.
This script is for CI and release builders, not the std.http runtime.

Usage:
  python tools/build_std_http_libcurl.py --prefix /tmp/valiance-curl
  python tools/build_std_http_libcurl.py --source-tarball ./curl-8.23.0.tar.xz --prefix /tmp/valiance-curl

Requires CMake, a C compiler, OpenSSL development headers, and nghttp2
headers. `--sanitizers` builds an *additional testing* library, never the
redistributable production artifact. A production artifact additionally needs
its TLS/nghttp2 transitive licenses and a platform-specific audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

CURL_VERSION = '8.23.0'
CURL_SOURCE_SHA256 = '8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5'
CURL_SOURCE_URL = f'https://curl.se/download/curl-{CURL_VERSION}.tar.xz'


def verified_tarball(tarball: Path) -> dict:
    if not tarball.is_file():
        raise ValueError(f'curl source archive not found: {tarball}')
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    if digest != CURL_SOURCE_SHA256:
        raise ValueError(f'libcurl source SHA-256 mismatch: {digest}; expected {CURL_SOURCE_SHA256}')
    return {'source': str(tarball), 'source_sha256': digest, 'version': CURL_VERSION}


def _run(cmd: list[str], *, timeout: int = 1200) -> str:
    done = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if done.returncode:
        raise RuntimeError(f'{cmd[0]} exited {done.returncode}: {(done.stdout + done.stderr)[-5000:]}')
    return (done.stdout + done.stderr)[-3000:]


def build(*, prefix: Path, tarball: Path | None, sanitizers: bool) -> dict:
    if shutil.which('cmake') is None:
        raise RuntimeError('CMake is required to build libcurl')
    prefix = prefix.resolve()
    if prefix.exists() and any(prefix.iterdir()):
        raise ValueError(f'install prefix must be empty: {prefix}')
    with tempfile.TemporaryDirectory(prefix='valiance-libcurl-build-') as dirname:
        work = Path(dirname)
        archive = tarball if tarball is not None else work / f'curl-{CURL_VERSION}.tar.xz'
        if tarball is None:
            with urllib.request.urlopen(CURL_SOURCE_URL, timeout=60) as stream:
                with archive.open('wb') as output:
                    shutil.copyfileobj(stream, output)
        source = verified_tarball(archive)
        with tarfile.open(archive, mode='r:xz') as tf:
            tf.extractall(work, filter='data')
        directory = work / f'curl-{CURL_VERSION}'
        copying = directory / 'COPYING'
        if not copying.is_file() or copying.stat().st_size < 500:
            raise ValueError('source archive lacks the libcurl COPYING license')
        builddir = work / 'build'
        cmake = ['cmake', '-S', str(directory), '-B', str(builddir),
                 '-DCMAKE_BUILD_TYPE=RelWithDebInfo', '-DBUILD_SHARED_LIBS=ON',
                 '-DBUILD_CURL_EXE=OFF', '-DBUILD_TESTING=OFF',
                 '-DCURL_USE_OPENSSL=ON', '-DUSE_NGHTTP2=ON', '-DHTTP_ONLY=ON',
                 '-DCMAKE_INSTALL_PREFIX=' + str(prefix)]
        if sanitizers:
            flags = '-fsanitize=address,undefined -fno-omit-frame-pointer'
            cmake.extend(['-DCMAKE_C_FLAGS=' + flags,
                          '-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=address,undefined'])
        _run(cmake, timeout=180)
        cache = (builddir / 'CMakeCache.txt').read_text(encoding='utf-8', errors='replace')
        # Any auto-disabled HTTP/2 is a release-stopping error, not a warning.
        if 'USE_NGHTTP2:BOOL=ON' not in cache:
            raise RuntimeError('curl build cannot enable HTTP/2: nghttp2 development dependency missing')
        _run(['cmake', '--build', str(builddir), '--parallel', str(min(os.cpu_count() or 2, 8))])
        _run(['cmake', '--install', str(builddir)], timeout=180)
        license_file = prefix / 'licenses' / 'libcurl-COPYING.txt'
        license_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copying, license_file)
        # Resolve the real shared object, but return a linker-visible alias
        # (which is a better ctypes entrypoint than its versioned target).
        extensions = ('libcurl.so', 'libcurl.dylib', 'libcurl.dll')
        candidates = [p for p in prefix.rglob('*') if p.name in extensions and p.is_file()]
        if not candidates:
            raise RuntimeError('built libcurl shared library not found')
        library = candidates[0].absolute()
        report = dict(source, library=str(library), library_sha256=hashlib.sha256(library.read_bytes()).hexdigest(),
                      license_file=str(license_file), sanitizers=sanitizers,
                      status='built_not_qualified',
                      runtime_instructions=f'VALIANCE_LIBCURL_PATH={library} (then run native and HTTP qualification)')
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--source-tarball', type=Path)
    parser.add_argument('--sanitizers', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    try:
        report = build(prefix=args.prefix, tarball=args.source_tarball, sanitizers=args.sanitizers)
    except (OSError, ValueError, RuntimeError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        report = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                  'version': CURL_VERSION, 'source_sha256_expected': CURL_SOURCE_SHA256}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'built_not_qualified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
