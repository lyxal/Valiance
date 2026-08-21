"""Boundary tests for immutable branch-local analysis state."""

from __future__ import annotations

import unittest

from valiance.analysis import AnalysisBranch, BranchSet, BranchVariables, InputMode
from valiance.analysis.state import VariableWrite
from valiance.vtypes import Int, Number, String
from valiance.vtypes.symbols import Symbol


class AnalysisStateBoundaryTests(unittest.TestCase):
    """Exercise the public transformations without constructing an analyser."""

    def test_branch_stack_transformations_are_immutable(self) -> None:
        """Pushing and popping return new branches without changing the input."""
        branch = AnalysisBranch()
        pushed = branch.push(Int, String)
        self.assertEqual(tuple(branch.stack), ())
        self.assertEqual(tuple(pushed.stack), (Int, String))
        self.assertEqual(tuple(pushed.pop().stack), (Int,))

    def test_variable_refinement_is_independent_of_analyser(self) -> None:
        """Variable frames refine matching facts as pure values."""
        name = Symbol("value")
        written = BranchVariables().write(name, Int)
        self.assertIsInstance(written, VariableWrite)
        self.assertIsNotNone(written.variables)
        refined = written.variables.refine_type(Int, Number)
        self.assertEqual(refined.read(name), Number)
        self.assertIsNone(BranchVariables().read(name))

    def test_branch_set_collect_deduplicates_equal_states(self) -> None:
        """Collection preserves order while removing duplicate branches."""
        branch = AnalysisBranch(input_mode=InputMode.NILADIC)
        self.assertEqual(BranchSet.collect((branch, branch)).branches, (branch,))


if __name__ == "__main__":
    unittest.main()
