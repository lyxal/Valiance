"""Regression tests for the tentative example programs in the language guide."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, run
from valiance.runtime.runtime_values import LazyList, RuntimeNumber


def execute(source: str):
    """Parse, analyse, compile, and execute one Valiance source string."""
    program = parse(source)
    analyser = Analyser()
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=False))


def materialize(value):
    """Recursively materialize lazy list values for stable assertions."""
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [materialize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(materialize(item) for item in value)
    return value


class TentativeExampleTests(unittest.TestCase):
    def test_number_guessing_game_accepts_the_target(self):
        source = r"""
import {std.random}
const $TARGET = random.between(1, 100)
while (true) =>
  input("Enter your guess! ")
  parseInt match =>
    as guess: Number =>
      if ($guess < $TARGET) => println("Too small!")
      else if ($guess > $TARGET) => println("Too big!")
      else =>
        println("Just right!")
        break
      end
    _ => println("Input must be a number")
  end
end
"""
        output = io.StringIO()
        with (
            patch("valiance.std.random.random.randint", return_value=42) as randint,
            patch("builtins.input", return_value="42"),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(execute(source), [])

        randint.assert_called_once_with(1, 100)
        self.assertEqual(output.getvalue(), "Just right!\n")

    def test_caesar_cipher(self):
        source = r"""
define cipher(plaintext: String, shiftAmount: Integer) =>
  import {string}
  string.\Alphabet peek: rotate $shiftAmount
  string.transliterate($plaintext, _, _)
end
cipher("ABC XYZ", 3)
"""
        self.assertEqual(execute(source), ["DEF ABC"])

    def test_run_length_encode_and_decode(self):
        source = r"""
define encode(:String) -> {Integer, String}+ =>
  groupConsecutive
  map: ({length, first})
end

define decode(:{Integer, String}+) -> String =>
  map: fold: *
  join ""
end
"aaabbc" encode
[{3, "a"}, {2, "b"}, {1, "c"}] decode
"aaabbc" encode | decode
"""
        encoded, decoded, round_trip = execute(source)
        self.assertEqual(
            materialize(encoded),
            [
                (RuntimeNumber("3"), "a"),
                (RuntimeNumber("2"), "b"),
                (RuntimeNumber("1"), "c"),
            ],
        )
        self.assertEqual(decoded, "aaabbc")
        self.assertEqual(round_trip, "aaabbc")

    def test_stack_style_calculator(self):
        source = r"""
define evaluate(expr: String)<Panic[UnwrappedNoneFault]>
-> Result[Number, ValueError] =>
  $stack: Number+ = []
  $expr split(" ") foreach (token) =>
    $token match =>
      if in "+-*/" =>
        $token match =>
          "+" => fn => +
          "-" => fn => -
          "*" => fn => *
          "/" => fn => /
        fold($stack[-1, -2])
        $stack := drop(2) | append
      if numeric? => $stack := append (parseInt $token ?!)
      _ => return ValueError("Unexpected Token")
    end
  end
  last $stack
end
evaluate("3 4 +")
"""
        self.assertEqual(execute(source), [RuntimeNumber("7")])

    def test_sum_of_squares_styles(self):
        source = r"""
define sumOfSquares(:Number+) -> Number => sum square
define otherSquares(:Number+) -> Number => sum ** 2
[1, 2, 3] sumOfSquares
[1, 2, 3] otherSquares
"""
        self.assertEqual(execute(source), [RuntimeNumber("14"), RuntimeNumber("14")])

    def test_trapezoidal_rule(self):
        source = r"""
define trapezoidal(
  fn: Function[Number -> Number],
  a: Number,
  b: Number,
  n: Number)
-> Number =>
  $h = ($b - $a) / $n
  $fsum = sum $fn(range(1, $n - 1) * $h + $a)
  $h * sum [0.5 * $fn($a), 0.5 * $fn($b), $fsum]
end
trapezoidal(fn (x: Number) => $x * $x end, 0, 1, 2)
"""
        self.assertEqual(execute(source), [RuntimeNumber("0.375")])

    def test_fibonacci_in_three_styles(self):
        source = r"""
define fibonacci1(n: Integer) =>
  if ($n == 0) => 0
  else if ($n == 1) => 1
  else =>
    0 1 unfold => peek: +
    $[$n - 2]
  end
end

@recursive
define fibonacci2(n: Number) -> Number =>
  if ($n == 0) => 0
  else if ($n == 1) => 1
  else => sum this [$n - 1, $n - 2]
end

define fibonacci3(n: Number) =>
  $(prev, next) = 0, 1
  $iterations = 0
  while ($iterations < $n) =>
    $(prev, next) = $next, $prev + $next
    $iterations := + 1
  end
  $prev
end
fibonacci1(8)
fibonacci2(8)
fibonacci3(8)
"""
        self.assertEqual(
            execute(source),
            [RuntimeNumber("21"), RuntimeNumber("21"), RuntimeNumber("21")],
        )

    def test_records(self):
        source = r"""
$store = record{a => 1, b => 2, c => 3}
$store.a
$store.c
record{x => 3} record.extend{y => 4}
record{x => 3} record.merge record{y => 4}
"""
        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("1"),
                RuntimeNumber("3"),
                {"x": RuntimeNumber("3"), "y": RuntimeNumber("4")},
                {"x": RuntimeNumber("3"), "y": RuntimeNumber("4")},
            ],
        )

    def test_argument_cycling(self):
        source = r"""
$singleArg = fn (:Number) => println | println
$singleArg(5)
$doubleArg = fn (:Number, :Number) => println | println | println
$doubleArg(6, 7)
"""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(execute(source), [])
        self.assertEqual(output.getvalue(), "5\n5\n6\n7\n6\n")

    def test_user_defined_dip(self):
        source = r"""
$dip = fn (function: Function) =>
  $temp = top
  $function()
  $temp
end
1 2 3 $dip(fn => +)
"""
        self.assertEqual(execute(source), [RuntimeNumber("3"), RuntimeNumber("3")])

    def test_trait_can_implement_another_trait_without_repeating_defaults(self):
        source = r"""
trait Logger => extend log(:String)
trait ErrorReporter as Logger =>
  define reportError(:String) => $self log
end

object ConsoleLogger => end
object ConsoleLogger as Logger =>
  define log(:String) => println
end
object ConsoleLogger as ErrorReporter => end
"""
        self.assertEqual(execute(source), [])

    def test_generic_find(self):
        source = r"""
define[T: trait => extend ===(T, T) -> #boolean Number] find(
  haystack: T+,
  needle: T
) =>
  $haystack foreach (item, ind) =>
    if ($item === $needle) => return $ind
  end
end

[1, 2, 3, 4, 5] find 3
[[1, 2, 3], [1, 3, 4], [2, 3, 4]] find [1, 3, 4]
"""
        self.assertEqual(execute(source), [RuntimeNumber("2"), RuntimeNumber("1")])


if __name__ == "__main__":
    unittest.main()
