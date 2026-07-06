"""
Similar to the runtime tests, except these are tests designed to exercise
key language features, rather than just aspects of the runtime.

These are higher level than the other tests, and represent expected
usage of the language, rather than testing specific features in isolation.

These also test very important/fundamental concepts that are crucial
to the language, such as rank polymorphism, type inference, and
tags.
"""

from decimal import Decimal
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


FIZZBUZZ_TO_100 = [
    "1",
    "2",
    "Fizz",
    "4",
    "Buzz",
    "Fizz",
    "7",
    "8",
    "Fizz",
    "Buzz",
    "11",
    "Fizz",
    "13",
    "14",
    "FizzBuzz",
    "16",
    "17",
    "Fizz",
    "19",
    "Buzz",
    "Fizz",
    "22",
    "23",
    "Fizz",
    "Buzz",
    "26",
    "Fizz",
    "28",
    "29",
    "FizzBuzz",
    "31",
    "32",
    "Fizz",
    "34",
    "Buzz",
    "Fizz",
    "37",
    "38",
    "Fizz",
    "Buzz",
    "41",
    "Fizz",
    "43",
    "44",
    "FizzBuzz",
    "46",
    "47",
    "Fizz",
    "49",
    "Buzz",
    "Fizz",
    "52",
    "53",
    "Fizz",
    "Buzz",
    "56",
    "Fizz",
    "58",
    "59",
    "FizzBuzz",
    "61",
    "62",
    "Fizz",
    "64",
    "Buzz",
    "Fizz",
    "67",
    "68",
    "Fizz",
    "Buzz",
    "71",
    "Fizz",
    "73",
    "74",
    "FizzBuzz",
    "76",
    "77",
    "Fizz",
    "79",
    "Buzz",
    "Fizz",
    "82",
    "83",
    "Fizz",
    "Buzz",
    "86",
    "Fizz",
    "88",
    "89",
    "FizzBuzz",
    "91",
    "92",
    "Fizz",
    "94",
    "Buzz",
    "Fizz",
    "97",
    "98",
    "Fizz",
    "Buzz",
]


class ProgramTests(unittest.TestCase):
    # Tests that see if doubling a list of numbers works
    def test_map_double_vectorised(self):
        result = execute("""
            [1, 2, 3, 4] * 2
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    def test_map_double_modifier(self):
        result = execute("""
            [1, 2, 3, 4] map: * 2
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    def test_map_double_inferred_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn => * 2
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    def test_map_double_inferred_return_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) => * 2
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    def test_map_double_fully_typed_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) -> Integer => * 2
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    def test_map_double_the_stack_way(self):
        result = execute("""
            dup [1, 2, 3, 4] | +
        """)
        self.assertEqual(
            result, [[Decimal("2"), Decimal("4"), Decimal("6"), Decimal("8")]]
        )

    # The intersection of generics and rank polymorphism

    def test_reduce_sum_matrix_modifier(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold: +           
        """)
        self.assertEqual(result, [[Decimal("12"), Decimal("15"), Decimal("18")]])

    def test_reduce_sum_matrix_inferred_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn => +           
        """)
        self.assertEqual(result, [[Decimal("12"), Decimal("15"), Decimal("18")]])

    def test_reduce_sum_matrix_inferred_return_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn (:Integer+, :Integer+) => +           
        """)
        self.assertEqual(result, [[Decimal("12"), Decimal("15"), Decimal("18")]])

    def test_reduce_sum_matrix_fully_typed_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            fold fn (:Integer+, :Integer+) -> Integer+ => +           
        """)
        self.assertEqual(result, [[Decimal("12"), Decimal("15"), Decimal("18")]])

    # Similar to the mapping example, but a little more complex
    def test_even_odd_check_mapped_over_range(self):
        result = execute("""
            range(1, 10)
            map fn =>
                if (% 2 == 0) => "Even"
                else => "Odd"
            end     
        """)
        self.assertEqual(
            result,
            [
                [
                    "Odd",
                    "Even",
                    "Odd",
                    "Even",
                    "Odd",
                    "Even",
                    "Odd",
                    "Even",
                    "Odd",
                    "Even",
                ]
            ],
        )

    # Fizzbuzz examples
    def test_fizzbuzz_smart_style(self):
        result = execute("""
        range(1, 100) map fn (n) =>
            % [3, 5] == 0 * ["Fizz", "Buzz"]
            join "" or toString $n
        end
        """)
        self.assertEqual(result, [FIZZBUZZ_TO_100])

    def test_fizzbuzz_imperative_style(self):
        result = execute("""
        range(1, 100) map: match =>
            if % 15 == 0 => "FizzBuzz"
            if %  5 == 0 =>     "Buzz"
            if %  3 == 0 =>     "Fizz"
                        _ => toString
        end 
        """)
        self.assertEqual(result, [FIZZBUZZ_TO_100])

    def test_fizzbuzz_with_variables_style(self):
        result = execute("""
        $output: String+ = []
        range(1, 100) foreach (n) =>
            $res = ""
            if ($n % 3 == 0) => $res := + "Fizz"
            if ($n % 5 == 0) => $res := + "Buzz"
            $output := append($res or "$n")
        end
        $output                     
        """)
        self.assertEqual(result, [FIZZBUZZ_TO_100])

    # Factorials

    def test_factorial_recursive_as_a_define(self):
        result = execute("""
        define factorial(:Integer) -> Integer =>
            match =>
                0 => 1
                _ => factorial(- 1) *
            end
        end
        factorial 5
        """)
        self.assertEqual(result, [Decimal("120")])

    def test_factorial_recursive_as_a_lambda(self):
        result = execute("""
        $factorial = @recursive fn (n: Integer) -> Integer =>
            if ($n == 0) => return 1
            this($n - 1) * $n
        end

        $factorial(5)
        """)
        self.assertEqual(result, [Decimal("120")])

    def test_factorial_as_fold(self):
        result = execute("""
        define factorial => range(1, _) fold: *
        factorial 5
        """)
        self.assertEqual(result, [Decimal("120")])

    def test_factorial_as_imperative_loop(self):
        result = execute("""
        define factorial(n: Number) =>
            $output = 1
            $count = 0
            while ($count < $n) =>
                $count := + 1
                $output := * $count
            end
            $output
        end

        factorial 5
        """)
        self.assertEqual(result, [Decimal("120")])
