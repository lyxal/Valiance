"""Uninstrumented system native libraries must not pass memory-safety signoff."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from tools.qualify_std_http_instrumented import inspect_instrumentation

class InstrumentationPolicyTests(unittest.TestCase):
    def test_missing_library_fails_closed(self):
        result=inspect_instrumentation(Path('/definitely/missing/libcurl.so'))
        self.assertFalse(result['valid'])

    def test_invalid_relative_library_fails_closed(self):
        result=inspect_instrumentation(Path('libcurl.so'))
        self.assertFalse(result['valid'])
