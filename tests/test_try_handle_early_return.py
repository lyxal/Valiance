"""Regression tests for source-level try/handle lexical-function returns."""

from __future__ import annotations

import contextlib
import io
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program, dumps, loads, run
from valiance.runtime.runtime_values import ObjectValue, RuntimeNumber


class TryHandleEarlyReturnTests(unittest.TestCase):
    """Check the analyser, optimiser, serializer and VM agree on exits."""

    def variants(self, source: str):
        """Compile one checked source in both bytecode modes, including round trips."""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [], source)
        for optimize in (False, True):
            code = compile_program(typed, optimize=optimize)
            for encoded in (False, True):
                yield loads(dumps(code)) if encoded else code

    def check(self, source: str, expected: list[object]) -> None:
        """Run all executable variants of an expected result."""
        for code in self.variants(source):
            self.assertEqual(run(code), expected)

    def test_handler_is_lexical_return_no_post_handler_side_effect(self):
        source = '''
define f(n: Int) -> String =>
  try =>
    panic ValueFault("bad")
  handle ValueFault =>
    "recovered"
  end
  println "SHOULD NOT EXECUTE"
  "wrong"
end
f(0)
'''
        for code in self.variants(source):
            text = io.StringIO()
            with contextlib.redirect_stdout(text):
                self.assertEqual(run(code), ["recovered"])
            self.assertEqual(text.getvalue(), "")

    def test_successful_try_continues_and_recovery_exits(self):
        self.check('''
define f(n: Int) -> String =>
  try =>
    if ($n < 0) => panic ValueFault("negative")
  handle ValueFault =>
    "recovered"
  end
  "continued"
end
[f(-1), f(1)]
''', [["recovered", "continued"]])

    def test_top_level_handler_finishes_program_only(self):
        self.check('''
try =>
  panic ValueFault("bad")
handle =>
  7
end
999
''', [RuntimeNumber(7)])

    def test_first_matching_handler_and_catch_all(self):
        self.check('''
define f(n: Int) -> String =>
  try =>
    panic ValueFault("bad")
  handle KeyFault =>
    "key"
  handle ValueFault =>
    "value"
  handle =>
    "catch-all"
  end
  "wrong"
end
f(1)
''', ["value"])
        self.check('''
define f(n: Int) -> String =>
  try =>
    panic ValueFault("bad")
  handle KeyFault => "wrong"
  handle => "catch-all"
  end
  "wrong"
end
f(1)
''', ["catch-all"])

    def test_nearest_inner_handler_and_outer_unmatched_handler(self):
        self.check('''
define f(n: Int) -> String =>
  try =>
    try =>
      panic ValueFault("bad")
    handle ValueFault =>
      "inner"
    end
    end
  handle => "outer"
  end
  "wrong"
end
f(1)
''', ["inner"])
        self.check('''
define f(n: Int) -> String =>
  try =>
    try =>
      panic ValueFault("bad")
    handle KeyFault =>
      "wrong"
    end
    end
  handle ValueFault =>
    "outer"
  end
  "wrong"
end
f(1)
''', ["outer"])

    def test_fault_in_handler_propagates_to_outer(self):
        self.check('''
define f(n: Int) -> String =>
  try =>
    try =>
      panic ValueFault("bad")
    handle ValueFault =>
      panic RuntimeFault("new")
    end
    end
  handle RuntimeFault =>
    "outer"
  end
  "wrong"
end
f(1)
''', ["outer"])

    def test_caller_catches_called_function_fault(self):
        self.check('''
define raises(n: Int) -> Int =>
  panic ValueFault("bad")
end
define f(n: Int) -> Int =>
  try =>
    raises $n
  handle ValueFault =>
    70
  end
  99
end
f(1)
''', [RuntimeNumber(70)])

    def test_nested_if_and_match(self):
        self.check('''
define f(n: Int) -> Int =>
  if ($n == 1) =>
    try =>
      panic ValueFault("bad")
    handle ValueFault =>
      77
    end
  else => 0
  end
  99
end
[f(0), f(1)]
''', [[RuntimeNumber(99), RuntimeNumber(77)]])
        self.check('''
define f(n: Int) -> Int =>
  $n match =>
    1 =>
      try =>
        panic ValueFault("bad")
      handle ValueFault =>
        77
      end
    _ => 0
  end
  99
end
[f(0), f(1)]
''', [[RuntimeNumber(99), RuntimeNumber(77)]])

    def test_implicit_state_carrying_while_helper_propagates_return(self):
        self.check('''
define f(n: Int) -> Int =>
  0 while (< 2) =>
    try =>
      if ($n == 1) => panic ValueFault("bad")
      else => 0 end
      pop
    handle ValueFault =>
      77
    end
    + 1
  end
  99
end
[f(0), f(1)]
''', [[RuntimeNumber(99), RuntimeNumber(77)]])

    def test_foreach_helper_propagates_return(self):
        self.check('''
define f(n: Int) -> Int =>
  [1, 2] foreach (x) =>
    try =>
      if ($n == 1) => panic ValueFault("bad")
      else => 0 end
      pop
    handle ValueFault =>
      77
    end
  end
  end
  99
end
[f(0), f(1)]
''', [[RuntimeNumber(99), RuntimeNumber(77)]])

    def test_explicit_return_in_handler(self):
        self.check('''
define f(n: Int) -> Int =>
  try =>
    panic ValueFault("bad")
  handle ValueFault =>
    return 17
  end
  99
end
f(1)
''', [RuntimeNumber(17)])

    def test_inferred_return_types_union(self):
        self.check('''
define f(n: Int) =>
  try =>
    if ($n < 0) => panic ValueFault("bad")
  handle ValueFault =>
    42
  end
  "ok"
end
[f(-1), f(1)]
''', [[RuntimeNumber(42), "ok"]])

    def test_multiple_outputs_and_zero_outputs(self):
        two = '''
define f(n: Int) -> Int, String =>
  try =>
    panic ValueFault("bad")
  handle ValueFault =>
    7 "answer"
  end
  99 "wrong"
end
'''
        for code in self.variants(two):
            vm = VirtualMachine()
            vm.run(code)
            self.assertEqual(vm.call(vm.globals["f"], [RuntimeNumber(0)]),
                             [RuntimeNumber(7), "answer"])
        zero = '''
define f(n: Int) -> =>
  try =>
    panic ValueFault("bad")
  handle ValueFault =>
    println "recovered"
  end
  println "wrong"
end
f(1)
'''
        for code in self.variants(zero):
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(run(code), [])
            self.assertEqual(printed.getvalue(), "recovered\n")

    def test_catch_and_continue_low_level_optional_conversion(self):
        self.check('''
"not a number" to?[Int]
123
''', [ObjectValue('None', {}), RuntimeNumber(123)])

    def test_closure_return_does_not_exit_outer_caller(self):
        self.check('''
define f(n: Int) -> Int =>
  $inner = fn -> Int =>
    try =>
      panic ValueFault("bad")
    handle ValueFault =>
      7
    end
    99
  end
  $inner() + 5
end
f(1)
''', [RuntimeNumber(12)])

    def test_declared_return_arity_and_type_are_checked(self):
        """A terminal handler may not return fewer results than declared."""
        invalid = (
            ('''
define f(n: Int) -> Int, String =>
  try => panic ValueFault("bad")
  handle ValueFault => 1
  end
  2 "ok"
end
f(0)
''', "fewer values"),
            ('''
define f(n: Int) -> Int =>
  try => panic ValueFault("bad")
  handle ValueFault => "unused" pop
  end
  2
end
f(0)
''', "fewer values"),
            ('''
define f(n: Int) -> String =>
  try => panic ValueFault("bad")
  handle ValueFault => 1
  end
  "ok"
end
f(0)
''', "return type mismatch"),
        )
        for source, diagnostic in invalid:
            with self.subTest(diagnostic=diagnostic):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(any(diagnostic in message for message in analyser.diagnostics),
                                analyser.diagnostics)

    def test_explicit_err_result_is_not_automatically_wrapped(self):
        """An Err object returned by a handler remains that exact object."""
        source = '''
object MyError => end
object MyError as Err => end
define f(n: Int) -> MyError =>
  try => panic ValueFault("bad")
  handle ValueFault => MyError
  end
  MyError
end
f(0)
'''
        for code in self.variants(source):
            returned, = run(code)
            self.assertEqual(returned.type_name, "MyError")

    def test_raii_cleanup_runs_once_on_implicit_return(self):
        """Leaving a function through a handler releases its owned locals."""
        source = '''
@nodup("exclusive")
object Token =>
  define ~Token => println "released" end
end
define f(n: Int) -> String =>
  $owned = Token
  try =>
    panic ValueFault("bad")
  handle ValueFault =>
    "ok"
  end
  "wrong"
end
f(0)
'''
        for code in self.variants(source):
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                self.assertEqual(run(code), ["ok"])
            self.assertEqual(printed.getvalue(), "released\n")

    def test_terminal_state_carrying_while_body_emits_loop(self):
        """A loop whose only body exit returns still executes the handler."""
        self.check('''
define f(n: Int) -> Int =>
  0 while (< 2) =>
    try =>
      panic ValueFault("bad")
    handle ValueFault =>
      77
    end
  end
  99
end
f(1)
''', [RuntimeNumber(77)])

    def test_state_modify_handler_returns_and_releases_protection(self):
        """A handler nested in a generated loop completes its state update."""
        self.check('''
import {std.concurrency.State}
$s = State(0)
$s modify: fn(x:Int)->Int =>
  0 while (< 2) =>
    try => panic ValueFault("bad")
    handle ValueFault => 5
    end
  end
  6
end
get $s
$s modify: (1 +)
get $s
''', [RuntimeNumber(5), RuntimeNumber(6)])


if __name__ == "__main__":
    unittest.main()
