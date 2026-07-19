"""Regression tests for top-level function-literal closure boundaries."""
from __future__ import annotations

import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import LazyList, RuntimeNumber


def analyse(source: str):
    """Analyse one source snippet and return its analyser and typed program."""
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def materialize(value):
    """Materialize lazy runtime collections for stable assertions."""
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [materialize(item) for item in value]
    return value


class TopLevelFunctionCaptureTests(unittest.TestCase):
    """Keep importable definitions closed while allowing script-local closures."""

    def test_top_level_function_literal_captures_top_level_assignment(self):
        """An ordinary top-level closure may read a script-local assignment."""
        source = """
$target = 4
[1, 2, 3] map fn => + $target end
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        expected = [[RuntimeNumber("5"), RuntimeNumber("6"), RuntimeNumber("7")]]
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(materialize(run(program)), expected)
            self.assertEqual(materialize(run(loads(dumps(program)))), expected)

    def test_modifier_function_inherits_top_level_capture_permission(self):
        """Modifier sugar wraps a function that retains the enclosing permission."""
        source = """
5
$x = top
range(1, 3) map: ** $x
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        [result] = run(compile_program(typed, optimize=False))
        self.assertEqual(
            list(result),
            [RuntimeNumber("1"), RuntimeNumber("32"), RuntimeNumber("243")],
        )

    def test_nested_literal_inside_top_level_literal_inherits_permission(self):
        """Nested closures may transitively retain a top-level capture."""
        source = """
$x = 5
$outer = fn => fn => $x end end
call(call($outer))
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed, optimize=False)), [RuntimeNumber("5")])

    def test_define_and_nested_literal_inside_define_remain_closed(self):
        """A define cannot evade module closure rules through a nested literal."""
        for source in (
            "$x = 5\ndefine bad => $x end",
            "$x = 5\ndefine bad => fn => $x end end",
        ):
            with self.subTest(source=source):
                analyser, _typed = analyse(source)
                self.assertEqual(len(analyser.diagnostics), 1)
                self.assertIn("cannot capture top-level variable 'x'", analyser.diagnostics[0])

    def test_define_cannot_capture_top_level_constant(self):
        """A top-level constant is not importable closure state for a define."""
        for body in (
            '$x',
            '"value: $x"',
            '"value: ${$x 1 +}"',
            '"function: ${fn () => $x end}"',
        ):
            source = f"const $x = 5\ndefine bad -> String => {body} end"
            with self.subTest(body=body):
                analyser, _typed = analyse(source)
                self.assertEqual(len(analyser.diagnostics), 1)
                self.assertIn(
                    "cannot capture top-level variable 'x'",
                    analyser.diagnostics[0],
                )



if __name__ == "__main__":
    unittest.main()
