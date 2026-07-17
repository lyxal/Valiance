"""Boundary tests for the call-planning subsystem."""

from __future__ import annotations

import copy
import unittest

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
