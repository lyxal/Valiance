"""Boundary tests for declaration subsystem ownership."""

from __future__ import annotations

import copy
import unittest

from valiance.analysis import Analyser
from valiance.analysis.declarations import DeclarationAnalyser
from valiance.parsing import parse


class DeclarationBoundaryTests(unittest.TestCase):
    """Verify declaration services remain coordinated by the façade."""

    def test_analyser_constructs_a_declaration_service(self) -> None:
        """The analyser exposes a dedicated declaration subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.declarations, DeclarationAnalyser)
        self.assertTrue(analyser.declarations.provides("_object_definition"))
        self.assertTrue(analyser.declarations.provides("_load_import_definitions"))

    def test_declaration_service_survives_session_copy(self) -> None:
        """REPL preview copies retain the service-to-context relationship."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("object Point => public $x: Number end\nPoint(1)"))
        self.assertEqual(analyser.diagnostics, [])

    def test_function_registration_still_uses_the_facade_handler(self) -> None:
        """Node dispatch delegates definitions without changing typed output."""
        analyser = Analyser()
        analyser.analyse(parse("define identity(x: Number) -> Number => $x\n1 identity"))
        self.assertEqual(analyser.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
