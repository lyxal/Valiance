"""Boundary tests for value-level expression analysis."""

from __future__ import annotations

import copy
import unittest

from valiance.analysis import Analyser
from valiance.analysis.expressions import ExpressionAnalyser
from valiance.asts import TypedLiteralNode, TypedNode
from valiance.asts.nodes import FieldAccessNode, FieldSetNode, ListLiteralNode, RecordLiteralNode
from valiance.parsing import parse


class ExpressionBoundaryTests(unittest.TestCase):
    """Verify value expressions are owned outside the analyser façade."""

    def test_analyser_constructs_an_expression_service(self) -> None:
        """The façade exposes the dedicated expression subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.expressions, ExpressionAnalyser)
        self.assertTrue(analyser.expressions.provides("_field_type"))
        self.assertTrue(analyser.expressions.provides("_can_access_attribute"))

    def test_collection_literal_handler_emits_typed_node(self) -> None:
        """Collection literal semantics remain committed in the typed AST."""
        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2, 3]"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedLiteralNode)
        self.assertIsInstance(typed[-1].node, ListLiteralNode)

    def test_record_literal_handler_emits_typed_node(self) -> None:
        """Record literal field typing remains available downstream."""
        analyser = Analyser()
        typed = analyser.analyse(parse("record{name => \"Ada\", score => 10}"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedLiteralNode)
        self.assertIsInstance(typed[-1].node, RecordLiteralNode)

    def test_field_handlers_use_expression_service(self) -> None:
        """Field reads and writes retain explicit typed operations."""
        source = """object Counter =>
  public $value: Number
end
1
Counter
dup
$.value
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(any(isinstance(node, TypedNode) and isinstance(node.node, FieldAccessNode) for node in typed))

    def test_expression_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve expression-service delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("[1, 2]"))
        self.assertEqual(analyser.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
