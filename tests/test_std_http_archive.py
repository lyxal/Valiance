"""Verify release ZIP audit refuses missing files and path traversal."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
import zipfile
from tools.verify_std_http_release_archive import check_archive

class ReleaseArchiveSafetyTests(unittest.TestCase):
    def test_invalid_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d)/'bad.zip'
            with zipfile.ZipFile(bad,'w') as z:
                z.writestr('project/../secrets.txt','abc')
            result = check_archive(bad)
            self.assertEqual(result['status'],'failed')
            self.assertTrue(any('unsafe' in issue for issue in result['issues']))

    def test_missing_files_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            bad=Path(d)/'incomplete.zip'
            with zipfile.ZipFile(bad,'w') as z:
                z.writestr('project/README.md','hello')
            result=check_archive(bad)
            self.assertEqual(result['status'],'failed')
            self.assertGreater(len(result['issues']),5)
