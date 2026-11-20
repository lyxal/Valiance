"""Local std.http native-runtime packaging/ABI/trust/redistribution audit.

Captures exact libcurl shared-object provenance and Linux shared-library
resolution, checks source licenses, and enforces an accompanying copyright
license when a product explicitly bundles libcurl. No binaries are bundled by
this script and results are host-specific, not cross-platform certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import ssl
import subprocess
import sys

# Source-tree invocation also works without an editable project install.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
for directory in (PROJECT_ROOT, PROJECT_ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from tools.check_std_http_native import _resolved_loaded_library_path
from valiance.std._http_transport import _curl, _libcurl_version_supported, _UNSUPPORTED_CURL_OVERRIDE


def bundled_curl_license_valid(library: Path, license_file: Path | None) -> bool:
    """Never silently ship third-party native code without its license text."""
    if not library.is_absolute() or not library.is_file():
        return False
    if license_file is None or not license_file.is_file():
        return False
    try:
        contents = license_file.read_text(encoding='utf-8')
    except (UnicodeError, OSError):
        return False
    # Do not claim license correctness from its filename alone. The source
    # must still be reviewed against the exact libcurl and TLS dependency set.
    return len(contents) >= 200 and ('curl' in contents.lower() or
                                     'copyright' in contents.lower())


def audit(project_root: Path, *, bundled_library: Path | None = None,
          license_file: Path | None = None) -> dict:
    issues = []
    licenses = {name: (project_root / name).is_file()
                for name in ('LICENSE-APACHE', 'LICENSE-MIT')}
    if not all(licenses.values()):
        issues.append('Valiance project licenses are missing')
    lib = _curl()
    resolved = _resolved_loaded_library_path(str(lib._name))
    info = lib.curl_version_info(0).contents
    version = info.version.decode('utf-8', 'replace')
    loaded = {
        'requested_loader_name': str(lib._name),
        'resolved_path': str(resolved) if resolved else None,
        'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved else None,
        'bytes': resolved.stat().st_size if resolved else None,
        'version': version,
        'ssl_backend': (info.ssl_version or b'').decode('utf-8', 'replace'),
        'ssl_enabled': bool(info.features & (1 << 2)),
        'http2_enabled': bool(info.features & (1 << 16)),
        'secure_libcurl_baseline_met': _libcurl_version_supported(info.version_num),
    }
    if not loaded['secure_libcurl_baseline_met'] or os.environ.get(_UNSUPPORTED_CURL_OVERRIDE) == '1':
        issues.append('loaded libcurl does not meet the enforced 8.23.0 release baseline or development bypass is enabled')
    if not loaded['ssl_enabled'] or not loaded['http2_enabled']:
        issues.append('libcurl lacks mandatory TLS or HTTP/2 functionality')
    if not resolved:
        issues.append('could not establish exact loaded libcurl file')
    dependency_check = None
    if platform.system() == 'Linux' and resolved:
        try:
            process = subprocess.run(['ldd', str(resolved)], capture_output=True,
                                     text=True, timeout=15, check=False)
            dependency_check = {'command': ['ldd', str(resolved)],
                                'returncode': process.returncode,
                                'output': process.stdout[-16000:], 'stderr': process.stderr[-2000:]}
            if process.returncode or 'not found' in process.stdout:
                issues.append('libcurl has unresolved Linux shared dependencies')
        except (OSError, subprocess.TimeoutExpired) as exc:
            issues.append(f'Linux dynamic dependency inspection failed: {exc}')
    default_ca = ssl.get_default_verify_paths()
    cafile = default_ca.cafile
    ca_probe = {'python_default_cafile': cafile,
                'python_default_cafile_exists': bool(cafile and Path(cafile).is_file()),
                'note': 'Python CA discovery is diagnostic only; native libcurl trust store varies by build'}
    if bundled_library is not None:
        if not bundled_curl_license_valid(bundled_library, license_file):
            issues.append('explicitly bundled libcurl requires a matching copyright/license file')
        if resolved and bundled_library.resolve() != resolved.resolve():
            issues.append('packaged libcurl differs from the binary loaded at runtime')
    elif os.environ.get('VALIANCE_LIBCURL_PATH'):
        issues.append('explicitly packaged libcurl override requires --bundled-library and --license-file audit')
    return {
        'status': 'passed' if not issues else 'failed', 'platform': platform.platform(),
        'python': sys.version.split()[0], 'loaded_libcurl': loaded,
        'licenses_present': licenses, 'dynamic_dependencies': dependency_check,
        'trust_store_diagnostics': ca_probe,
        'bundled_library_requested': str(bundled_library) if bundled_library else None,
        'bundled_libcurl_license_checked': bundled_library is not None,
        'redistribution_scope': 'system-provided dependency' if bundled_library is None else 'explicitly bundled runtime',
        'issues': issues,
        'limitations': 'Does not inspect macOS or Windows package binaries, TLS backend redistribution terms, or certify CA policies on other machines.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--bundled-library', type=Path)
    parser.add_argument('--license-file', type=Path)
    args = parser.parse_args()
    try:
        record = audit(Path(__file__).resolve().parent.parent,
                       bundled_library=args.bundled_library,
                       license_file=args.license_file)
    except BaseException as exc:
        record = {'status': 'failed', 'issues': [f'{type(exc).__name__}: {exc}']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': record['status'], 'issues': record['issues'],
                      'libcurl': record.get('loaded_libcurl', {}).get('version')}, indent=2))
    return 0 if record['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
