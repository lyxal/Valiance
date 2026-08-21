"""Boundary tests for branch-producing control-flow analysis."""

from __future__ import annotations

import copy
import unittest

from valiance.analysis import Analyser
from valiance.analysis.control_flow import ControlFlowAnalyser
from valiance.asts import TypedForNode, TypedMatchNode, TypedTryNode, TypedWhileNode
from valiance.parsing import parse


class ControlFlowBoundaryTests(unittest.TestCase):
    """Verify control-flow semantics are owned outside the analyser façade."""

    def test_analyser_constructs_a_control_flow_service(self) -> None:
        """The façade exposes the dedicated control-flow subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.control_flow, ControlFlowAnalyser)
        self.assertTrue(analyser.control_flow.provides("_match"))
        self.assertTrue(analyser.control_flow.provides("_match_is_exhaustive"))
        self.assertTrue(analyser.control_flow.provides("_try"))

    def test_match_analysis_commits_a_typed_match(self) -> None:
        """Pattern refinement and coverage produce the existing typed AST node."""
        analyser = Analyser()
        typed = analyser.analyse(parse("""1 match =>
  1 => "one"
  _ => "other"
end"""))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedMatchNode)

    def test_try_analysis_commits_a_typed_try(self) -> None:
        """Try branches and handlers retain their explicit typed representation."""
        analyser = Analyser()
        typed = analyser.analyse(parse("""try =>
  1
handle =>
  2
end"""))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedTryNode)

    def test_loop_handlers_live_under_control_flow(self) -> None:
        """While and foreach handlers still emit their established typed nodes."""
        analyser = Analyser()
        typed = analyser.analyse(parse("$n: Int = 0\nwhile ($n < 1) =>\n$n := + 1\nend"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedWhileNode)

        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2] foreach (n) => $n end"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedForNode)

    def test_implicit_while_input_is_reused_by_condition_and_body(self) -> None:
        """The condition observes loop state without consuming it from the body."""
        analyser = Analyser()
        typed = analyser.analyse(parse("""0 while (< 10) =>
  println("Count is ${top}")
  + 1
end"""))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedWhileNode)
        self.assertEqual(typed[-1].input_count, 1)

    def test_control_flow_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve control-flow context delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("1 match => _ => 2 end"))
        self.assertEqual(analyser.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
