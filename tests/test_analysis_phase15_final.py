"""Final architectural acceptance tests for the Phase 15 decomposition."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.analysis.calls import CallAnalyser
from valiance.analysis.contracts import ContractAnalyser
from valiance.analysis.control_flow import ControlFlowAnalyser
from valiance.analysis.declarations import DeclarationAnalyser
from valiance.analysis.expressions import ExpressionAnalyser
from valiance.parsing import parse


class Phase15ArchitectureTests(unittest.TestCase):
    """Lock in the completed analyser package boundaries."""

    def test_facade_exposes_all_semantic_services(self) -> None:
        """The façade coordinates five explicit semantic subsystems."""
        analyser = Analyser()
        self.assertIsInstance(analyser.declarations, DeclarationAnalyser)
        self.assertIsInstance(analyser.calls, CallAnalyser)
        self.assertIsInstance(analyser.control_flow, ControlFlowAnalyser)
        self.assertIsInstance(analyser.contracts, ContractAnalyser)
        self.assertIsInstance(analyser.expressions, ExpressionAnalyser)

    def test_facade_stays_within_target_size(self) -> None:
        """The completed façade remains below the 1,200-line target."""
        source = Path(__file__).parents[1] / "src/valiance/analysis/analyser.py"
        self.assertLessEqual(len(source.read_text().splitlines()), 1_200)

    def test_legacy_private_modules_are_removed(self) -> None:
        """No old monolithic analyser implementation modules remain."""
        root = Path(__file__).parents[1] / "src/valiance/analysis"
        for name in (
            "_analyser_calls.py",
            "_analyser_functions.py",
            "_analyser_patterns.py",
            "_analyser_handlers.py",
            "_analyser_utils.py",
            "where_clause.py",
            "annotations.py",
        ):
            self.assertFalse((root / name).exists(), name)

    def test_copied_session_preserves_every_service(self) -> None:
        """REPL-style deep copies retain all service-to-context links."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("define twice(x: Number) -> Number => $x 2 *\n3 twice"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIs(analyser.declarations._context, analyser)
        self.assertIs(analyser.calls._context, analyser)
        self.assertIs(analyser.control_flow._context, analyser)
        self.assertIs(analyser.contracts._context, analyser)
        self.assertIs(analyser.expressions._context, analyser)

    def test_element_diagnostics_are_owned_by_calls(self) -> None:
        """Smart element suggestions are exposed by the call subsystem."""
        analyser = Analyser()
        self.assertTrue(analyser.calls.provides("_unknown_element_message"))
        analyser.analyse(parse("1 prntln"))
        self.assertTrue(analyser.diagnostics)
        self.assertIn("did you mean", analyser.diagnostics[0])


if __name__ == "__main__":
    unittest.main()
