"""Verify a released source ZIP by extraction and isolated import/smoke tests.

This checks the *archive*, not merely the source tree. Native qualification is
separate: use --require-native when the shipping 8.23.0+ libcurl is available.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import zipfile

REQUIRED = (
    'pyproject.toml', 'LICENSE-MIT', 'LICENSE-APACHE',
    'src/valiance/std/http.vlnc', 'src/valiance/std/_http_transport.py',
    'src/valiance/std/_http_body.py', 'src/valiance/std/_http_core.py',
    'tests/test_std_http_dependency_policy.py', 'tests/test_std_http_stable_gate.py',
    'tools/check_std_http_stable.py', 'tools/build_std_http_libcurl.py',
    'docs/maintenance/std-http.md',
)


def check_archive(path: Path, *, native: bool = False) -> dict:
    findings = []
    if not path.is_file():
        return {'status': 'failed', 'issues': ['ZIP archive not found']}
    with zipfile.ZipFile(path) as archive:
        bad = archive.testzip()
        if bad:
            findings.append(f'ZIP CRC failure: {bad}')
        files = archive.namelist()
        if len(files) != len(set(files)):
            findings.append('duplicate ZIP entries')
        if not files:
            findings.append('empty archive')
        tops = {PurePosixPath(item).parts[0] for item in files if item}
        if len(tops) != 1:
            findings.append('archive needs exactly one top-level project directory')
        prefix = next(iter(tops), '')
        for member in files:
            parts = PurePosixPath(member).parts
            if member.startswith('/') or '..' in parts or '\\' in member:
                findings.append(f'unsafe ZIP member: {member}')
            if member.endswith(('.pyc', '.pyo')) or '__pycache__/' in member:
                findings.append('archive contains generated Python bytecode')
        available = set(files)
        for requirement in REQUIRED:
            if f'{prefix}/{requirement}' not in available:
                findings.append(f'missing project artifact: {requirement}')
        if findings:
            return {'status': 'failed', 'issues': findings}
        with tempfile.TemporaryDirectory(prefix='valiance-release-unpack-') as directory:
            archive.extractall(directory)
            root = Path(directory) / prefix
            env = os.environ.copy()
            env['PYTHONPATH'] = os.pathsep.join((str(root / 'src'), str(root)))
            cmd = [sys.executable, '-m', 'unittest', '-q',
                   'tests.test_std_http_dependency_policy', 'tests.test_std_http_stable_gate']
            try:
                result = subprocess.run(cmd, cwd=root, env=env, text=True,
                                        capture_output=True, timeout=60, check=False)
            except subprocess.TimeoutExpired:
                findings.append('extracted-source test timeout')
            else:
                if result.returncode:
                    findings.append('extracted-source tests failed: ' + (result.stderr + result.stdout)[-2000:])
            if native:
                native_cmd = [sys.executable, '-c',
                              'from valiance.std._http_transport import _curl; _curl()']
                try:
                    process = subprocess.run(native_cmd, cwd=root, env=env, text=True,
                                             capture_output=True, timeout=30, check=False)
                    if process.returncode:
                        findings.append('shipping native library load failed: ' + process.stderr[-1800:])
                except subprocess.TimeoutExpired:
                    findings.append('shipping native library load timeout')
    return {'status': 'failed' if findings else 'passed', 'issues': findings,
            'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'archive_size_bytes': path.stat().st_size,
            'native_check_required': native, 'isolated_source_tests': 'passed' if not findings else 'failed'}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--require-native', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args=parser.parse_args()
    try:
        doc=check_archive(args.archive, native=args.require_native)
    except (ValueError, zipfile.BadZipFile, OSError) as exc:
        doc={'status':'failed', 'issues':[f'{type(exc).__name__}: {exc}']}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(doc,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(doc,indent=2))
    return 0 if doc['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
