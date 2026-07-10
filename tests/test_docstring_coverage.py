"""Enforce maintenance documentation for production Python code."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


class DocstringCoverageTests(unittest.TestCase):
    """Verify that production modules and callables remain documented."""

    def test_production_modules_and_functions_have_docstrings(self) -> None:
        """Report every undocumented module, function, method, or nested helper."""
        source_root = Path(__file__).parents[1] / "src" / "valiance"
        missing: list[str] = []

        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(source_root.parent)
            if ast.get_docstring(tree, clean=False) is None:
                missing.append(f"{relative}: module")
            self._collect_missing_callables(tree, relative, missing)

        self.assertEqual(
            missing,
            [],
            "Production docstrings are required:\n" + "\n".join(missing),
        )

    def _collect_missing_callables(
        self,
        tree: ast.AST,
        path: Path,
        missing: list[str],
    ) -> None:
        """Append qualified names for undocumented functions found in one tree."""
        visitor = _FunctionDocstringVisitor(path)
        visitor.visit(tree)
        missing.extend(visitor.missing)


class _FunctionDocstringVisitor(ast.NodeVisitor):
    """Walk one module while retaining class and nested-function qualification."""

    def __init__(self, path: Path) -> None:
        """Initialize a visitor for one source path."""
        self.path = path
        self.scope: list[str] = []
        self.missing: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Visit methods under their class-qualified name."""
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check a synchronous function and recurse into nested definitions."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check an asynchronous function and recurse into nested definitions."""
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """Record a missing docstring and visit the callable's nested scopes."""
        qualified = ".".join((*self.scope, node.name))
        if ast.get_docstring(node, clean=False) is None:
            self.missing.append(f"{self.path}:{node.lineno}: {qualified}")
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


if __name__ == "__main__":
    unittest.main()
