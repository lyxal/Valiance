"""Runtime dependency safety contract and pinned build's offline checks."""
from __future__ import annotations
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from valiance.std._http_errors import ErrorKind, HttpError
from valiance.std._http_transport import (
    _libcurl_version_supported, _validate_libcurl_version,
    _UNSUPPORTED_CURL_OVERRIDE, _curl_library_name,
)
from tools.build_std_http_libcurl import CURL_SOURCE_SHA256, verified_tarball


class CurlDependencyPolicyTests(unittest.TestCase):
    def test_release_baseline_and_known_unpatched_versions(self):
        for version in (0x070000, 0x080a01, 0x080e01, 0x081600):
            self.assertFalse(_libcurl_version_supported(version))
        for version in (0x081700, 0x081701, 0x081800):
            self.assertTrue(_libcurl_version_supported(version))
        self.assertFalse(_libcurl_version_supported('8.23.0'))

    def test_old_libcurl_fails_closed_without_unsafe_override(self):
        with patch.dict(os.environ, {_UNSUPPORTED_CURL_OVERRIDE: '0'}):
            with self.assertRaises(HttpError) as raised:
                _validate_libcurl_version(0x080e01)
        self.assertEqual(raised.exception.kind, ErrorKind.UNSUPPORTED)
        self.assertEqual(raised.exception.stage, 'startup')
        self.assertIn('8.23.0', str(raised.exception))

    def test_bypass_requires_exact_explicit_value(self):
        for value in ('true', 'yes', '2', ''):
            with self.subTest(value=value), patch.dict(os.environ, {_UNSUPPORTED_CURL_OVERRIDE: value}):
                with self.assertRaises(HttpError):
                    _validate_libcurl_version(0x080e01)
        with patch.dict(os.environ, {_UNSUPPORTED_CURL_OVERRIDE: '1'}):
            _validate_libcurl_version(0x080e01)

    def test_pinned_version_accepted_without_override(self):
        with patch.dict(os.environ, {_UNSUPPORTED_CURL_OVERRIDE: '0'}):
            _validate_libcurl_version(0x081700)

    def test_explicit_path_must_exist_and_be_absolute(self):
        with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': 'libcurl.so.4'}):
            with self.assertRaises(HttpError):
                _curl_library_name()
        with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': '/missing/no-libcurl.so'}):
            with self.assertRaises(HttpError):
                _curl_library_name()
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'libcurl.so'
            path.write_bytes(b'test only')
            with patch.dict(os.environ, {'VALIANCE_LIBCURL_PATH': str(path)}):
                self.assertEqual(_curl_library_name(), str(path))

    def test_source_hash_must_match_before_extraction(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / 'curl-8.23.0.tar.xz'
            fake.write_bytes(b'untrusted')
            with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                verified_tarball(fake)
            self.assertEqual(len(CURL_SOURCE_SHA256), 64)


if __name__ == '__main__':
    unittest.main()
