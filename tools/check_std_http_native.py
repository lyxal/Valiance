"""Inspect the actual system libcurl selected by std.http, not the curl CLI.

The output is machine-readable build/dependency evidence. Native features and
licenses still require platform-specific packaging review when redistributing
libcurl, nghttp2, OpenSSL or other linked libraries.
"""
from __future__ import annotations

import argparse
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import sys

from valiance.std._http_transport import _curl, _libcurl_version_supported, _UNSUPPORTED_CURL_OVERRIDE


def _resolved_loaded_library_path(loaded_name: str) -> Path | None:
    """Locate the mapped library to identify the binary, not just its SONAME.

    On Linux /proc/self/maps gives the exact dynamically mapped file after
    aliases, symlinks and loader search paths were resolved. Other systems
    return a verified absolute loaded path when available and otherwise
    explicitly leave provenance unknown rather than inventing a match.
    """
    candidate = Path(loaded_name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()
    maps = Path('/proc/self/maps')
    if maps.is_file():
        for line in maps.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.split(maxsplit=5)
            if len(parts) == 6 and 'libcurl' in Path(parts[5]).name:
                matched = Path(parts[5].removesuffix(' (deleted)'))
                if matched.is_file():
                    return matched.resolve()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    record: dict[str, object] = {
        'python': sys.version, 'platform': platform.platform(),
        'python_trust_default': str(ssl.get_default_verify_paths()),
        'library_lookup': ctypes.util.find_library('curl'),
        'explicit_library_path': os.environ.get('VALIANCE_LIBCURL_PATH'),
        'unsafe_old_libcurl_override': os.environ.get(_UNSUPPORTED_CURL_OVERRIDE) == '1',
    }
    try:
        lib = _curl()  # Executes the same version/SSL/HTTP2 gates as real clients.
        info = lib.curl_version_info(0).contents
        raw = info.version_num
        resolved = _resolved_loaded_library_path(str(lib._name))
        record.update({
            'outcome': 'passed', 'library_loaded': str(lib._name),
            'resolved_loaded_library_path': str(resolved) if resolved else None,
            'resolved_binary_size_bytes': resolved.stat().st_size if resolved else None,
            'resolved_binary_sha256': hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved else None,
            'libcurl_version': info.version.decode('utf-8', 'replace'),
            'version_number': f'{raw >> 16}.{(raw >> 8) & 255}.{raw & 255}',
            'tls_backend': (info.ssl_version or b'').decode('utf-8', 'replace'),
            'zlib_version': (info.libz_version or b'').decode('utf-8', 'replace'),
            'ssl_feature': bool(info.features & (1 << 2)),
            'http2_feature': bool(info.features & (1 << 16)),
            'connection_identity_supported': raw >= 0x080200,
            'secure_libcurl_baseline_met': _libcurl_version_supported(raw),
        })
    except BaseException as exc:
        record.update({'outcome': 'failed',
                       'error': f'{type(exc).__name__}: {exc}'})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record, indent=2))
    return 0 if record['outcome'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
