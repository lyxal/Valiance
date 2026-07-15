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

class BuiltinLintRuleTests(unittest.TestCase):
    """Exercise the conservative built-in lint patterns and suppression checks."""

    def _codes(self, source: str) -> list[str]:
        """Analyse source and return stable lint codes."""
        analyser = Analyser()
        analyser.analyse(parse(source))
        return [item.code for item in analyser.lint_findings]

    def test_unused_foreach_index(self) -> None:
        """An unread index binding is removable."""
        self.assertIn(
            "unused-loop-index",
            self._codes("[1, 2] foreach (n, index) => $n 1 + end"),
        )

    def test_mutable_binding_written_once_can_be_const(self) -> None:
        """A binding with one write and a later read can be constant."""
        self.assertIn("constant-never-reassigned", self._codes("$x = 1\n$x 2 +"))

    def test_captured_write_warns_that_state_is_not_persistent(self) -> None:
        """Closure-local writes to captures have surprising persistence semantics."""
        self.assertIn(
            "captured-write-not-persistent",
            self._codes("fn =>\n  $x = 1\n  fn => $x := + 1 end\nend"),
        )

    def test_additive_accumulator_prefers_sum_over_fold(self) -> None:
        """The sum-specific lint wins over generic fold guidance."""
        codes = self._codes(
            "$total = 0\n[1, 2, 3] foreach (n) => $total := + $n end"
        )
        self.assertIn("prefer-sum", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_vectorising_map_prefers_direct_vectorisation(self) -> None:
        """A map containing a pervasive arithmetic call can vectorise directly."""
        self.assertIn(
            "explicit-map-can-vectorise",
            self._codes("[1, 2, 3] map: + 1"),
        )

    def test_repeated_literal_equality_chain_prefers_match(self) -> None:
        """An else-if chain over one subject is match-shaped."""
        self.assertIn(
            "prefer-match",
            self._codes(
                "$x = 1\n"
                "if ($x 1 ==) => 1 "
                "else if ($x 2 ==) => 2 else => 3 end"
            ),
        )

    def test_unknown_suppression_code_is_reported(self) -> None:
        """Misspelled lint codes do not silently suppress nothing."""
        self.assertIn(
            "unknown-lint-code",
            self._codes(
                '@lintOff("prefer-fodl")\n[1, 2] foreach (n) => $n 1 + end'
            ),
        )

    def test_unused_specific_suppression_is_reported(self) -> None:
        """Stale specific suppressions remain visible."""
        self.assertIn(
            "unused-lint-suppression",
            self._codes(
                '@lintOff("prefer-fold")\n[1, 2] foreach (n) => $n 1 + end'
            ),
        )

    def test_prefer_filter_rule_matches_conditional_collection_shape(self) -> None:
        """The filter lint recognises only the narrow unchanged-item pattern."""
        from valiance.analysis import AnalysisBranch, BranchSet
        from valiance.analysis.lints.rules.idioms import prefer_filter
        from valiance.asts import ElementNode, ForNode, GetVariableNode, IfNode
        from valiance.vtypes.symbols import Symbol

        loop = ForNode(
            Symbol("n"),
            body=(
                IfNode(
                    condition=(GetVariableNode(Symbol("n")),),
                    then_branch=(
                        GetVariableNode(Symbol("n")),
                        ElementNode(Symbol("append")),
                    ),
                ),
            ),
        )
        context = NodeLintContext(
            node=loop,
            branch=AnalysisBranch(),
            outputs=BranchSet((AnalysisBranch(),)),
            env=Analyser().env,
        )
        self.assertEqual(prefer_filter(context)[0].code, "prefer-filter")

    def test_while_traversal_rule_matches_index_pattern(self) -> None:
        """The foreach suggestion requires length, indexing, and increment shapes."""
        from valiance.analysis import AnalysisBranch, BranchSet
        from valiance.analysis.lints.rules.idioms import while_can_be_foreach
        from valiance.asts import ElementNode, GetVariableNode, IndexAccessNode, WhileNode
        from valiance.vtypes.symbols import Symbol

        loop = WhileNode(
            condition=(
                GetVariableNode(Symbol("i")),
                GetVariableNode(Symbol("xs")),
                ElementNode(Symbol("length")),
            ),
            body=(IndexAccessNode(), ElementNode(Symbol("inc"))),
        )
        context = NodeLintContext(
            node=loop,
            branch=AnalysisBranch(),
            outputs=BranchSet((AnalysisBranch(),)),
            env=Analyser().env,
        )
        self.assertEqual(while_can_be_foreach(context)[0].code, "while-can-be-foreach")


class ProjectLintConfigurationTests(unittest.TestCase):
    """Verify project-wide lint policy loaded from valiance.toml."""

    def _project(self, lint_table: str, source: str):
        """Create and analyse a temporary project with one source file."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        (root / "valiance.toml").write_text(
            "[project]\nname = \"lint-test\"\nversion = \"0.1.0\"\n\n"
            "[entries]\nmain = \"src/main.vlnc\"\n\n"
            f"[lints]\n{lint_table}\n\n"
            "[dependencies]\n",
            encoding="utf-8",
        )
        source_file = root / "src" / "main.vlnc"
        source_file.parent.mkdir()
        source_file.write_text(source, encoding="utf-8")
        analyser = Analyser(source_file=source_file)
        analyser.analyse(parse(source))
        return temporary, analyser

    def test_project_can_disable_specific_lints(self) -> None:
        """The disable array suppresses only named lint codes project-wide."""
        temporary, analyser = self._project(
            'enabled = true\ndisable = ["prefer-sum"]',
            "$total = 0\n[1, 2] foreach (n) => $total := + $n end\n1 as Integer",
        )
        self.addCleanup(temporary.cleanup)
        codes = [item.code for item in analyser.lint_findings]
        self.assertNotIn("prefer-sum", codes)
        self.assertIn("redundant-cast", codes)

    def test_project_can_disable_all_lints(self) -> None:
        """Setting enabled=false suppresses all project lint findings."""
        temporary, analyser = self._project(
            "enabled = false\ndisable = []",
            "$total = 0\n[1, 2] foreach (n) => $total := + $n end\n1 as Integer",
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(analyser.lint_findings, [])

    def test_source_suppression_layers_on_project_policy(self) -> None:
        """Node suppression can disable another lint after project configuration."""
        temporary, analyser = self._project(
            'enabled = true\ndisable = ["prefer-sum"]',
            '@lintOff("redundant-cast")\n1 as Integer',
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(analyser.lint_findings, [])

    def test_manifest_rejects_unknown_lint_codes(self) -> None:
        """Project policy reports misspelled lint codes while loading the manifest."""
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from valiance.modules_system.packages import PackageError, load_manifest

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valiance.toml").write_text(
                "[project]\nname = \"x\"\n\n[entries]\n\n"
                "[lints]\ndisable = [\"prefer-fodl\"]\n\n"
                "[dependencies]\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(PackageError, "unknown lint code"):
                load_manifest(root)
