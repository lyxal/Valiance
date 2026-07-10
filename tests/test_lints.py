"""Tests for the extensible lint registry."""

from __future__ import annotations

import unittest

from valiance.analysis import Analyser, LintRegistry, finding
from valiance.analysis.lints import NodeLintContext
from valiance.asts import NumberLiteralNode
from valiance.parsing import parse


class LintRegistryTests(unittest.TestCase):
    """Verify that lint rules can be installed without analyser changes."""

    def test_custom_node_rule_runs_from_a_supplied_registry(self) -> None:
        """A registry can add a node lint without editing analyser handlers."""
        registry = LintRegistry()

        def lint_number(context: NodeLintContext):
            """Flag every number literal for this isolated test registry."""
            return (
                finding(
                    "test-number-literal",
                    "number literal visited by custom lint",
                    context.node,
                ),
            )

        registry.register_node(NumberLiteralNode, lint_number)
        analyser = Analyser(lint_registry=registry)

        analyser.analyse(parse("1 as Integer"))

        self.assertEqual(
            analyser.lints,
            ["1:1: number literal visited by custom lint"],
        )
        self.assertEqual(analyser.lint_findings[0].code, "test-number-literal")

    def test_child_analysers_reuse_the_supplied_registry(self) -> None:
        """Rules remain active inside nested function analysis."""
        registry = LintRegistry()

        def lint_number(context: NodeLintContext):
            """Flag number literals analysed in nested scopes."""
            return (finding("nested-number", "nested number", context.node),)

        registry.register_node(NumberLiteralNode, lint_number)
        analyser = Analyser(lint_registry=registry)

        analyser.analyse(parse("fn => 1 end"))

        self.assertEqual(analyser.lints, ["1:7: nested number"])
        self.assertEqual(analyser.lint_findings[0].code, "nested-number")


if __name__ == "__main__":
    unittest.main()
