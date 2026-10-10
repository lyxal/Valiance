"""Boundary tests for declaration subsystem ownership."""

from __future__ import annotations

import copy
import re
import unittest

from valiance.analysis import Analyser
from valiance.analysis.declarations import DeclarationAnalyser
from valiance.parsing import parse


class DeclarationBoundaryTests(unittest.TestCase):
    """Verify declaration services remain coordinated by the façade."""

    def test_analyser_constructs_a_declaration_service(self) -> None:
        """The analyser exposes a dedicated declaration subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.declarations, DeclarationAnalyser)
        self.assertTrue(analyser.declarations.provides("_object_definition"))
        self.assertTrue(analyser.declarations.provides("_load_import_definitions"))

    def test_declaration_service_survives_session_copy(self) -> None:
        """REPL preview copies retain the service-to-context relationship."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("object Point => public $x: Number end\nPoint(1)"))
        self.assertEqual(analyser.diagnostics, [])

    def test_function_registration_still_uses_the_facade_handler(self) -> None:
        """Node dispatch delegates definitions without changing typed output."""
        analyser = Analyser()
        analyser.analyse(parse("define identity(x: Number) -> Number => $x\n1 identity"))
        self.assertEqual(analyser.diagnostics, [])

    def test_undefined_function_signature_types_are_diagnosed(self) -> None:
        """Signature typos report their cause even when overload selection fails."""
        for annotation in ("r", "r+", "Function[Number -> r]", "Number | r"):
            with self.subTest(annotation=annotation):
                analyser = Analyser()
                analyser.analyse(parse(
                    f"define factorial(n: {annotation}) -> Number =>\n"
                    "  $n match =>\n"
                    "    0 => 1\n"
                    "    _ => factorial($n - 1) * $n\n"
                    "  end\nend\nprintln factorial 5"
                ))
                self.assertTrue(any(
                    "undefined type 'r'" in diagnostic
                    for diagnostic in analyser.diagnostics
                ))
                column = len("define factorial(n: ") + annotation.rfind("r") + 1
                self.assertIn(f"1:{column}: undefined type 'r'", analyser.diagnostics)

    def test_signature_generics_and_forward_declared_types_are_valid(self) -> None:
        """Name validation preserves generic binders and module setup ordering."""
        analyser = Analyser()
        analyser.analyse(parse(
            "define[T] identity(x: T) -> T => $x end\n"
            "define point(x: Point) -> Point => $x end\n"
            "object Point => public $x: Number end"
        ))
        self.assertEqual(analyser.diagnostics, [])

    def test_undefined_return_and_literal_types_are_diagnosed(self) -> None:
        """Definitions and function values validate input and output annotations."""
        for source in (
            "define missing(x: Number) -> r => $x end",
            "fn(x: r) -> Number => 1 end",
            "fn(x: Number) -> r => $x end",
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(any(
                    "undefined type 'r'" in diagnostic
                    for diagnostic in analyser.diagnostics
                ))

    def test_undefined_type_diagnostics_point_to_each_source_name(self) -> None:
        """Retain exact positions across genericization and overload expansion."""
        for source in (
            "define missing(x: Number) -> r => $x end",
            "fn(x: r) -> Number => 1 end",
            "fn(x: Number) -> r => $x end",
            "define[T: r] missing(x: T) -> T => $x end",
            "define missing(\n  x: r,\n  y: r\n) -> Number => 1 end",
            "overload (r -> Number)\ndefine missing(x) => 1 end",
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                for match in re.finditer(r"\br\b", source):
                    prefix = source[:match.start()]
                    line = prefix.count("\n") + 1
                    column = len(prefix.rsplit("\n", 1)[-1]) + 1
                    self.assertIn(
                        f"{line}:{column}: undefined type 'r'", analyser.diagnostics,
                    )


if __name__ == "__main__":
    unittest.main()
