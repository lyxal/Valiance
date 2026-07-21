"""End-to-end regression tests for the Brainfuck interpreter example."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import RuntimeError, compile_program, dumps, loads, run

BRAINFUCK_INTERPRETER = r"""
define \TAPE_SIZE -> Integer => 30000
$tape = [0] overtake \TAPE_SIZE

tag #TapePointer as unit
define #TapePointer(:Integer) => inRange(0, \TAPE_SIZE)

define get(tape: Integer+, ind: #TapePointer Integer) => $tape[#-TapePointer $ind]
define apply(:#TapePointer Integer, fn: Function[Integer -> Integer]) =>
  #-TapePointer | #TapePointer $fn()
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
    _ => None
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


if __name__ == "__main__":
    unittest.main()
