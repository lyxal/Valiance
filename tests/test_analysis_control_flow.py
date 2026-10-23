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

    def test_reflective_foreach_rejects_an_invalid_possible_specialization(self):
        analyser = Analyser()
        analyser.analyse(parse('''
object Person =>
  public $age: Int
  public $name: String
end
define onlyInt(evidence: Type[Int]) -> Int => 1 end
fields[Person] foreach (field) => onlyInt($field.type) end
'''))
        self.assertTrue(any("no overloads" in message and "onlyInt" in message
                            for message in analyser.diagnostics), analyser.diagnostics)

    def test_reflective_escaped_transforms_keep_all_possible_result_types(self):
        import valiance.vtypes as T

        analyser = Analyser()
        branches = analyser.analyse_program_branches(parse('''
object Person =>
  public $name: String
  public $age: Int
end
define convert(evidence: Type[Int]) -> String => "integer" end
define convert(evidence: Type[String]) -> Int => 1 end
fields[Person] foreach (field) =>
  if $field.name == "age" => break convert($field.type) end
end
'''))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(all(T.same(branch.top, T.optional(T.U(T.Int, T.String)))
                            for branch in branches))

    def test_reflective_foreach_requires_lexical_authority_at_the_iteration_site(self):
        analyser = Analyser()
        analyser.analyse(parse('''
@nodup("Resource cannot be duplicated")
object Resource => public $value: Int end
unsafe => fields[Resource] end
foreach (field) => print $field.name end
'''))
        self.assertTrue(any("requires lexical unsafe" in message
                            for message in analyser.diagnostics), analyser.diagnostics)

    def test_opened_descriptor_remains_read_only(self):
        analyser = Analyser()
        analyser.analyse(parse('''
object Point => public $x: Int end
fields[Point] foreach (field) => $field.name = "changed" end
'''))
        self.assertTrue(analyser.diagnostics)

    def test_unsafe_authority_is_lexical_and_does_not_enter_helpers(self):
        """Nested unsafe scopes restore their parent; function bodies start safe."""
        from unittest.mock import patch
        from valiance.asts import ElementNode
        from valiance.analysis.state import BranchSet

        observations = []
        helper_observations = []
        original = Analyser._analyse_node_from_branch

        def observe(analyser, branch, node):
            if isinstance(node, ElementNode) and node.name.text == "observeUnsafe":
                observations.append(analyser.reflection_unsafe)
                return BranchSet((branch,))
            if isinstance(node, ElementNode) and node.name.text == "observeHelper":
                helper_observations.append(analyser.reflection_unsafe)
                return BranchSet((branch,))
            return original(analyser, branch, node)

        analyser = Analyser()
        with patch.object(Analyser, "_analyse_node_from_branch", observe):
            analyser.analyse(parse("""
observeUnsafe
unsafe =>
  observeUnsafe
  unsafe => observeUnsafe end
  observeUnsafe
end
observeUnsafe
define \\helper => observeHelper end
unsafe => \\helper end
"""))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(observations, [False, True, True, True, False])
        self.assertTrue(helper_observations)
        self.assertFalse(any(helper_observations))
        self.assertFalse(analyser.reflection_unsafe)

    def test_unsafe_restores_authority_after_failed_analysis(self):
        analyser = Analyser()
        analyser.analyse(parse("unsafe => missingElement end"))
        self.assertTrue(analyser.diagnostics)
        self.assertFalse(analyser.reflection_unsafe)

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
