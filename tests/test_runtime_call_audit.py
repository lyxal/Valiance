"""Tests for recursive static and dynamic runtime-call auditing."""

from __future__ import annotations

import unittest

from tools.audit_runtime_calls import audit_program, compile_source


class RuntimeCallAuditTests(unittest.TestCase):
    """Protect call metadata visibility across nested match guards."""

    def test_guard_elements_are_reported_as_resolved(self):
        source = """
1
match =>
  if % 2 == 0 => "even"
  _ => "odd"
end
"""
        report = audit_program(compile_source(source))

        self.assertGreaterEqual(report["counts"]["resolved-element"], 2)
        self.assertNotIn("dynamic-loaded-element", report["counts"])


if __name__ == "__main__":
    unittest.main()
