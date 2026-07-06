"""
Similar to the runtime tests, except these are tests designed to exercise
key language features, rather than just aspects of the runtime.

These are higher level than the other tests, and represent expected
usage of the language, rather than testing specific features in isolation.

These also test very important/fundamental concepts that are crucial
to the language, such as rank polymorphism, type inference, and
tags.
"""

from pathlib import Path
import unittest

from valiance.analysis.analyser import Analyser
from valiance.parsing.parser import parse
from valiance.runtime.compiler import compile_program
from valiance.runtime.vm import run


def execute(source: str, source_file: Path | None = None):
    program = parse(source)
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed))


class ProgramTests(unittest.TestCase):
    # Tests that see if doubling a list of numbers works
    def test_map_double_vectorised(self):
        result = execute("""
            [1, 2, 3, 4] * 2
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    def test_map_double_modifier(self):
        result = execute("""
            [1, 2, 3, 4] map: * 2
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    def test_map_double_inferred_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn => * 2
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    def test_map_double_inferred_return_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) => * 2
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    def test_map_double_fully_typed_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) -> Integer => * 2
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    def test_map_double_the_stack_way(self):
        result = execute("""
            dup [1, 2, 3, 4] | +
        """)
        self.assertEqual(result, [2, 4, 6, 8])

    # The intersection of generics and rank polymorphism

    def test_reduce_sum_matrix_modifier(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold: +           
        """)
        self.assertEqual(result, [12, 15, 18])

    def test_reduce_sum_matrix_inferred_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn => +           
        """)
        self.assertEqual(result, [12, 15, 18])

    def test_reduce_sum_matrix_inferred_return_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn (:Integer+, :Integer+) => +           
        """)
        self.assertEqual(result, [12, 15, 18])

    def test_reduce_sum_matrix_fully_typed_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn (:Integer+, :Integer+) -> Integer+ => +           
        """)
        self.assertEqual(result, [12, 15, 18])
