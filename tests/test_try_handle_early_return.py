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

    def test_named_typed_fault_exposes_concrete_fields(self):
        """A typed named handler reads the caught object, not a wrapper."""
        self.check('''
define recover(n: Int) -> String =>
  try =>
    panic ValueFault("details")
  handle as problem: ValueFault =>
    $problem.message
  end
  "wrong"
end
recover 1
''', ["details"])

    def test_named_catch_all_binds_concrete_fault(self):
        """An untyped named handler observes the actual raised fault value."""
        source = '''
define recover(n: Int) -> Fault =>
  try =>
    panic ValueFault("any")
  handle as problem =>
    $problem
  end
  panic ValueFault("wrong")
end
recover 1
'''
        for code in self.variants(source):
            [fault] = run(code)
            self.assertIsInstance(fault, ObjectValue)
            self.assertEqual(fault.type_name, "ValueFault")
            self.assertEqual(fault.fields["message"], "any")

    def test_named_fault_from_callee_crosses_activation_frames(self):
        """Panic ownership survives unwinding a called function's frame."""
        source = '''
define throws(n: Int) -> Int =>
  panic ValueFault("inner")
end
define recover(n: Int) -> String =>
  try =>
    throws $n
  handle as problem: ValueFault =>
    $problem.message
  end
  "wrong"
end
recover 1
'''
        self.check(source, ["inner"])

    def test_named_handler_keeps_first_match_and_other_forms(self):
        """Named and unnamed handlers share type matching and source order."""
        self.check('''
define recover(n: Int) -> String =>
  try =>
    panic ValueFault("value")
  handle as ignored: KeyFault =>
    "wrong"
  handle as actual: ValueFault =>
    $actual.message
  handle =>
    "wrong catch-all"
  end
  "wrong fallthrough"
end
recover 1
''', ["value"])
        self.check('''
define recover(n: Int) -> String =>
  try =>
    panic ValueFault("value")
  handle KeyFault =>
    "wrong"
  handle as actual =>
    $actual to[String]
  end
  "wrong"
end
recover 1
''', ["ValueFault{message: 'value'}"])

    def test_named_handler_preserves_early_return_and_success_path(self):
        """A handled panic returns; successful try still falls through."""
        self.check('''
define recover(n: Int) -> String =>
  try =>
    if ($n < 0) => panic ValueFault("negative")
  handle as fault: ValueFault =>
    $fault.message
  end
  "continued"
end
[recover(-1), recover(1)]
''', [["negative", "continued"]])

    def test_named_handler_type_is_checked(self):
        """A named typed handler must still declare a concrete Fault type."""
        source = '''
try =>
  panic ValueFault("bad")
handle as problem: String =>
  1
end
'''
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(
            any("does not implement Fault" in diagnostic for diagnostic in analyser.diagnostics),
            analyser.diagnostics,
        )

    def test_owned_fault_released_once_in_named_and_unnamed_handlers(self):
        """Matched faults retain one occurrence across unwound call frames."""
        for handler in (
            "handle as fault: MyFault =>",
            "handle as fault =>",
            "handle =>",
        ):
            source = '''
@nodup("exclusive")
object MyFault =>
  define ~MyFault => println "released" end
end
object MyFault as Fault => end
define throws(n: Int) -> Int =>
  panic MyFault
end
define recover(n: Int) -> Int =>
  try =>
    throws $n
  ''' + handler + '''
    7
  end
  99
end
recover 1
'''
            for code in self.variants(source):
                with self.subTest(handler=handler):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(run(code), [RuntimeNumber(7)])
                    self.assertEqual(output.getvalue(), "released\n")

    def test_nested_named_handlers_preserve_shadowed_parameter(self):
        """Unwinding an inner handler restores its shadowed outer binding."""
        self.check('''
define recover(fault: String) -> String =>
  try =>
    try =>
      panic ValueFault("first")
    handle as fault: ValueFault =>
      panic RuntimeFault("second")
    end
    end
  handle RuntimeFault =>
    $fault
  end
  "wrong"
end
recover("original")
''', ["original"])

    def test_named_handler_can_rethrow_the_same_fault(self):
        """A borrowed caught value stays owned through a second panic."""
        self.check('''
define recover(n: Int) -> String =>
  try =>
    try =>
      panic ValueFault("first")
    handle as fault: ValueFault =>
      panic $fault
    end
    end
  handle as outer: ValueFault =>
    $outer.message
  end
  "wrong"
end
recover(1)
''', ["first"])

    def test_named_handler_can_be_caught_within_another_named_handler(self):
        """Inner recovery retains an enclosing handler's lexical binding."""
        self.check('''
define recover(n: Int) -> String =>
  try =>
    panic ValueFault("first")
  handle as fault: ValueFault =>
    try =>
      panic RuntimeFault("second")
    handle RuntimeFault =>
      $fault.message
    end
    "wrong"
  end
  "wrong"
end
recover(1)
''', ["first"])

    def test_named_handler_shadows_global_without_replacing_it(self):
        """A top-level local catch binding does not modify a global name."""
        source = '''
$fault = "global"
try =>
  panic ValueFault("caught")
handle as fault: ValueFault =>
  $fault.message
end
'''
        for code in self.variants(source):
            vm = VirtualMachine()
            self.assertEqual(vm.run(code), ["caught"])
            self.assertEqual(vm.globals["fault"], "global")

    def test_unhandled_fault_from_nested_call_releases_transport(self):
        """An uncaught fault does not strand the occurrence moved out of a callee."""
        source = '''
@nodup("exclusive")
object MyFault =>
  define ~MyFault => println "released" end
end
object MyFault as Fault => end
define throws(n: Int) -> Int =>
  panic MyFault
end
throws 1
'''
        for code in self.variants(source):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with self.assertRaisesRegex(Exception, "uncaught panic: MyFault"):
                    run(code)
            self.assertEqual(output.getvalue(), "released\n")

    def test_named_handler_preserves_multiple_result_stack_effect(self):
        """The bound panic is a local, never an extra stack result."""
        self.check('''
define recover(n: Int) -> Int, String =>
  try =>
    panic ValueFault("two values")
  handle as problem: ValueFault =>
    9 $problem.message
  end
  0 "wrong"
end
recover 1
''', [RuntimeNumber(9), "two values"])

    def test_named_handler_inside_state_modification(self):
        """Early return from a named handler releases protected State access."""
        self.check('''
import {std.concurrency.State}
$state = State(0)
$state modify: fn(current: Int) -> Int =>
  try =>
    panic ValueFault("caught")
  handle as problem: ValueFault =>
    5
  end
  999
end
get $state
$state modify: (1 +)
get $state
''', [RuntimeNumber(5), RuntimeNumber(6)])


if __name__ == "__main__":
    unittest.main()
