"""Native packaging audit denies incomplete redistribution metadata."""
from __future__ import annotations
import tempfile
from pathlib import Path
import unittest

from tools.audit_std_http_packaging import bundled_curl_license_valid


class PackagingSafetyTests(unittest.TestCase):
    def test_explicit_bundle_requires_actual_license_text(self):
        with tempfile.TemporaryDirectory() as d:
            library = Path(d) / 'libcurl.so'
            library.write_bytes(b'placeholder')
            license_file = Path(d) / 'LICENSE-curl.txt'
            self.assertFalse(bundled_curl_license_valid(library, None))
            license_file.write_text('x' * 250)
            self.assertFalse(bundled_curl_license_valid(library, license_file))
            license_file.write_text('Copyright (c) contributors to curl.\n' + 'terms\n' * 55)
            self.assertTrue(bundled_curl_license_valid(library, license_file))
            self.assertFalse(bundled_curl_license_valid(Path(d) / 'missing.so', license_file))
