"""Boundary tests for the call-planning subsystem."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.analysis.calls import CallAnalyser
from valiance.parsing import parse


class CallPlanningBoundaryTests(unittest.TestCase):
    """Verify call planning is owned outside the analyser façade."""

    def test_analyser_constructs_a_call_service(self) -> None:
        """The façade exposes the dedicated call-planning subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.calls, CallAnalyser)
        self.assertTrue(analyser.calls.provides("element_call_candidates"))
        self.assertTrue(analyser.calls.provides("_analyse_element_extension"))

    def test_selected_overload_is_committed_to_the_typed_call(self) -> None:
        """Analysis records its overload decision for downstream compilation."""
        analyser = Analyser()
        typed = analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsNotNone(typed[-1].overload)
        self.assertEqual(len(typed[-1].overload.params), 2)

    def test_vectorisation_plan_is_committed_during_analysis(self) -> None:
        """Vectorised calls carry selected depths rather than deferring intent."""
        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2] 10 +"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(typed[-1].overload.vectorised_depths, (1, 0))

    def test_call_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve call-service context delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])

    def test_legacy_analyser_call_modules_are_removed(self) -> None:
        """Phase 15C leaves no compatibility modules under old names."""
        root = Path(__file__).parents[1] / "src" / "valiance" / "analysis"
        self.assertFalse((root / "_analyser_calls.py").exists())
        self.assertFalse((root / "_analyser_functions.py").exists())


if __name__ == "__main__":
    unittest.main()
