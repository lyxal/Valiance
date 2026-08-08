"""End-to-end program tests for fundamental and realistic Valiance behaviour.

These tests exercise complete Valiance programs through parsing, analysis,
compilation, and execution. Program fixtures live here or under ``samples``;
documentation examples are intentionally not used as test sources.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import RuntimeError, compile_program, dumps, loads, run
from valiance.runtime.bytecode import FunctionCode, OpCode, ResolvedElementReference
from valiance.runtime.runtime_values import LazyList, RuntimeNumber


def execute(source: str, source_file: Path | None = None, *, optimize: bool = False):
    """Parse, analyse, compile, and execute one Valiance source string."""
    program = parse(source)
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=optimize))


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
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_map_double_modifier(self):
        result = execute("""
            [1, 2, 3, 4] map: * 2
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_map_double_inferred_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn => * 2
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_map_double_inferred_return_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) => * 2
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_map_double_fully_typed_lambda(self):
        result = execute("""
            [1, 2, 3, 4] map fn (:Integer) -> Integer => * 2
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_map_double_the_stack_way(self):
        result = execute("""
            dup [1, 2, 3, 4] | +
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("2"),
                    RuntimeNumber("4"),
                    RuntimeNumber("6"),
                    RuntimeNumber("8"),
                ]
            ],
        )

    def test_reduce_and_slash_use_first_item_as_seed(self):
        self.assertEqual(execute("[1, 2, 3, 4] reduce: +"), [RuntimeNumber("10")])
        self.assertEqual(execute("[1, 2, 3, 4] /: +"), [RuntimeNumber("10")])

    def test_reduce_rejects_empty_list(self):
        with self.assertRaisesRegex(Exception, "reduce requires a non-empty list"):
            execute("([] as[Integer+]) reduce: +")

    def test_fold_uses_explicit_seed_and_accepts_empty_list(self):
        self.assertEqual(execute("[1, 2, 3] 10 fold: +"), [RuntimeNumber("16")])
        self.assertEqual(execute("([] as[Integer+]) 10 fold: +"), [RuntimeNumber("10")])

    def test_fold_supports_distinct_accumulator_and_item_types(self):
        source = '[1, 2, 3] "" fold fn (text: String, n: Integer) -> String => "$text$n" end'
        self.assertEqual(execute(source), ["123"])

    def test_fold_widens_seed_from_higher_order_accumulator_evidence(self):
        source = """
$findFold = fn (haystack: Number+, needle: Number) =>
  fold($haystack, {0, false}): fn (
    state: {Number, #boolean Number},
    item: Number
  ) -> {Number, #boolean Number} =>
    if ($state[1] as![#boolean Number]) => $state
    else if ($item == $needle) => {$state[0], true}
    else => {$state[0] + 1, false}
    end
  end
end
$findFold([4, 7, 9], 7)
$findFold([4, 7, 9], 5)
"""
        self.assertEqual(
            execute(source),
            [
                (RuntimeNumber("1"), RuntimeNumber("1")),
                (RuntimeNumber("3"), RuntimeNumber("0")),
            ],
        )

    # The intersection of generics and rank polymorphism

    def test_reduce_sum_matrix_modifier(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            reduce: +           
        """)
        self.assertEqual(
            result, [[RuntimeNumber("12"), RuntimeNumber("15"), RuntimeNumber("18")]]
        )

    def test_reduce_sum_matrix_inferred_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            reduce fn => +           
        """)
        self.assertEqual(
            result, [[RuntimeNumber("12"), RuntimeNumber("15"), RuntimeNumber("18")]]
        )

    def test_reduce_sum_matrix_inferred_return_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            reduce fn (:Integer+, :Integer+) => +           
        """)
        self.assertEqual(
            result, [[RuntimeNumber("12"), RuntimeNumber("15"), RuntimeNumber("18")]]
        )

    def test_reduce_sum_matrix_fully_typed_lambda(self):
        result = execute("""
            [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
            reduce fn (:Integer+, :Integer+) -> Integer+ => +           
        """)
        self.assertEqual(
            result, [[RuntimeNumber("12"), RuntimeNumber("15"), RuntimeNumber("18")]]
        )

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
        self.assertEqual(result, [RuntimeNumber("120")])

    def test_factorial_recursive_as_a_lambda(self):
        result = execute("""
        $factorial = @recursive fn (n: Integer) -> Integer =>
            if ($n == 0) => return 1
            this($n - 1) * $n
        end

        $factorial(5)
        """)
        self.assertEqual(result, [RuntimeNumber("120")])

    def test_factorial_as_reduce(self):
        result = execute("""
        define factorial => range(1, _) reduce: *
        factorial 5
        """)
        self.assertEqual(result, [RuntimeNumber("120")])

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
        self.assertEqual(result, [RuntimeNumber("120")])

    def test_flatten_as_function(self):
        result = execute("""
        $flatten = @recursive fn[T] (list: T~) -> T+ =>
            $flattened: T+ = []
            $list foreach (item) =>
                $flattened := addAll($item match =>
                as lst: T+ => $lst
                as scl: T  => [$scl]
                        _  => this($item)
                end)
            end
            $flattened
        end

        $flatten([[1, 2, 3], [[4, 5], [6]], 7])
        """)
        self.assertEqual(
            result,
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                    RuntimeNumber("5"),
                    RuntimeNumber("6"),
                    RuntimeNumber("7"),
                ]
            ],
        )



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
  map: reduce: *
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
        reduce($stack[-1, -2])
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
    $[$n - 1]
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
        self.assertEqual(output.getvalue(), "5\n5\n7\n6\n7\n")

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
trait Logger => extend log(:String)<Eager, IO>
trait ErrorReporter as Logger =>
  define reportError(:String) => $self log
end

object ConsoleLogger => end
object ConsoleLogger as Logger =>
  define log(:String)<Eager, IO> => println
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



BRAINFUCK_INTERPRETER = r"""
define \TAPE_SIZE -> Integer => 30000
$tape = [0] overtake \TAPE_SIZE

tag #TapePointer as unit
define #TapePointer(:Integer) => inRange(0, \TAPE_SIZE)

define get(tape: Integer+, ind: #TapePointer Integer) => $tape[#-TapePointer $ind]
define apply(value: #TapePointer Integer, fn: Function[Integer -> Integer]) =>
  #-TapePointer $value | #TapePointer $fn()
end

$tp: #TapePointer Integer = 0

$program = input("Enter brainfuck program: ")

$instructions: record(.cmd: String, .jump: Integer)+ = $program map fn (ch) =>
  record{cmd => $ch, jump => -1}
end

$stack: Integer+ = []

$instructions foreach (instr, i) =>
  $instr.cmd match =>
    "[" =>
      $stack := append $i
    "]" =>
      if (length $stack == 0) => panic ValueFault("Unmatched ]")
      $open = $stack last
      $stack := dropLast
      $instructions[$open].jump = $i + 1
      $instructions[$i].jump = $open
    _ => \\None
  end
end

if (length $stack != 0) => panic ValueFault("Unmatched [")

$pc: Integer = 0

while ($pc < length $instructions) =>
  $instr = $instructions[$pc]
  $instr.cmd match =>
    "+" =>
      $tape[#-TapePointer $tp] := + 1 | % 256
      $pc := + 1
    "-" =>
      $tape[#-TapePointer $tp] := - 1 | % 256
      $pc := + 1
    ">" =>
      $tp := apply: fn => + 1 | % \TAPE_SIZE
      $pc := + 1
    "<" =>
      $tp := apply: fn => - 1 | % \TAPE_SIZE
      $pc := + 1
    "." =>
      print fromCharcode $tape[#-TapePointer $tp]
      $pc := + 1
    "[" =>
      if ($tape[#-TapePointer $tp] == 0) =>
        $pc = $instr.jump
      else =>
        $pc := inc
      end
    "]" =>
      if ($tape[#-TapePointer $tp] != 0) =>
        $pc = $instr.jump
      else =>
        $pc := + 1
      end
    _ => panic ValueFault("Unexpected command in program")
  end
end
"""


def compile_interpreter():
    """Parse, analyse, compile, and serialize the example program."""
    analyser = Analyser()
    typed = analyser.analyse(parse(BRAINFUCK_INTERPRETER))
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return loads(dumps(compile_program(typed, optimize=False)))


class BrainfuckExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = compile_interpreter()

    def execute(self, brainfuck: str) -> tuple[list[object], str]:
        output = io.StringIO()
        with (
            patch("builtins.input", return_value=brainfuck),
            contextlib.redirect_stdout(output),
        ):
            result = run(self.program)
        return result, output.getvalue()

    def test_executes_looped_program_and_prints_character(self):
        result, output = self.execute("++++++++[>++++++++<-]>+.")
        self.assertEqual(result, [])
        self.assertEqual(output, "A")

    def test_zero_cell_skips_loop_body(self):
        result, output = self.execute("[.]++.")
        self.assertEqual(result, [])
        self.assertEqual(output, "\x02")

    def test_rejects_unmatched_closing_bracket(self):
        with self.assertRaisesRegex(RuntimeError, r"ValueFault.*Unmatched \]"):
            self.execute("]")

    def test_rejects_unmatched_opening_bracket(self):
        with self.assertRaisesRegex(RuntimeError, r"ValueFault.*Unmatched \["):
            self.execute("[")



SAMPLE_DIRECTORY = Path(__file__).parents[1] / "samples" / "optimizations"


def _compile_sample(name: str):
    """Compile one checked-in workload with and without optimisation."""
    source = (SAMPLE_DIRECTORY / name).read_text(encoding="utf-8")
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(f"{name} did not analyse: {analyser.diagnostics}")
    return compile_program(typed, optimize=False), compile_program(typed)


class OptimizerProgramTests(unittest.TestCase):
    """Run non-trivial examples through every optimisation family."""

    def assert_equivalent(self, unoptimized, optimized, expected):
        """Require direct, optimised, and serialized execution to agree."""
        self.assertEqual(run(unoptimized), expected)
        self.assertEqual(run(optimized), expected)
        self.assertEqual(run(loads(dumps(optimized))), expected)

    def test_project_estimate_uses_constant_folding(self):
        unoptimized, optimized = _compile_sample("ProjectEstimate.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("825.00")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertIn(
            RuntimeNumber("510"),
            tuple(
                instruction.arg
                for instruction in optimized.main.instructions
                if instruction.op is OpCode.PUSH_CONST
            ),
        )

    def test_shipment_pricing_materializes_explicit_arguments(self):
        unoptimized, optimized = _compile_sample("ShipmentPricing.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("59.50")],
        )
        functions = [
            instruction.arg
            for instruction in optimized.main.instructions
            if instruction.op is OpCode.MAKE_FUNCTION
        ]
        self.assertEqual(len(functions), 2)
        for function in functions:
            self.assertIsInstance(function, FunctionCode)
            self.assertEqual(
                tuple(instruction.op for instruction in function.instructions[:2]),
                (OpCode.LOAD_VAR, OpCode.LOAD_VAR),
            )

    def test_subscription_forecast_inlines_small_constant_functions(self):
        unoptimized, optimized = _compile_sample("SubscriptionForecast.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("1797.00")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertFalse(
            any(
                instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and isinstance(instruction.arg, ResolvedElementReference)
                and instruction.arg.name in {"\\monthlyRate", "\\monthsPerYear"}
                for instruction in optimized.main.instructions
            )
        )

    def test_ledger_reorder_removes_inverse_stack_shuffles(self):
        unoptimized, optimized = _compile_sample("LedgerReorder.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("1540")],
        )
        self.assertTrue(
            any(
                instruction.op is OpCode.STACK_SHUFFLE
                for instruction in unoptimized.main.instructions
            )
        )
        self.assertFalse(
            any(
                instruction.op is OpCode.STACK_SHUFFLE
                for instruction in optimized.main.instructions
            )
        )

    def test_payroll_feature_flag_folds_branch_and_bytecode(self):
        unoptimized, optimized = _compile_sample("PayrollFeatureFlag.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("950")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertFalse(
            any(
                instruction.op
                in {OpCode.JUMP, OpCode.JUMP_IF_FALSE, OpCode.JUMP_IF_MATCH}
                for instruction in optimized.main.instructions
            )
        )



ROOT = Path(__file__).resolve().parents[1]


def _materialize(value):
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _execute(program):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        stack = run(program)
    return _materialize(stack), output.getvalue()


class SampleProgramRegressionTests(unittest.TestCase):
    def test_checked_in_samples_match_across_execution_modes(self):
        samples = sorted((ROOT / "samples").glob("*.vlnc"))
        self.assertTrue(samples)

        for path in samples:
            with self.subTest(sample=path.name):
                analyser = Analyser()
                typed = analyser.analyse(
                    parse(path.read_text(encoding="utf-8"))
                )
                self.assertEqual(analyser.diagnostics, [])

                executions = []
                for optimize in (False, True):
                    program = compile_program(typed, optimize=optimize)
                    direct = _execute(program)
                    restored = _execute(loads(dumps(program)))
                    self.assertEqual(direct, restored)
                    executions.append(direct)

                self.assertEqual(executions[0], executions[1])



if __name__ == "__main__":
    unittest.main()
