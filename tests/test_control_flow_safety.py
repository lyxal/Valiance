"""Adversarial regression tests for Valiance control-flow structures."""
from __future__ import annotations

import contextlib
import io
import unittest

from valiance.analysis import Analyser
from valiance.parsing import ParseError, parse
from valiance.runtime import RuntimeError, compile_program, dumps, loads, run
from valiance.runtime.runtime_values import LazyList, ObjectValue, RuntimeNumber


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute(source: str, *, round_trip: bool = False):
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=False)
    if round_trip:
        program = loads(dumps(program))
    return run(program)


class ControlFlowSafetyTests(unittest.TestCase):
    def test_if_rejects_non_boolean_and_multi_value_conditions(self):
        for source in (
            'if (1) => "then" else => "else" end',
            'if ("not boolean") => "then" else => "else" end',
        ):
            with self.subTest(source=source):
                analyser, _typed = analyse(source)
                self.assertTrue(analyser.diagnostics)

    def test_if_merges_empty_and_multi_value_branches_without_corrupting_stack(self):
        stack = execute("""
99
if false =>
  1 2
else =>
end
""")
        self.assertEqual(stack, [RuntimeNumber("99"), None, None])

    def test_if_unselected_branch_does_not_run_or_leak_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("""
if true =>
  "selected"
else =>
  "unselected" println
  RuntimeFault("must not execute") panic
end
""")
        self.assertEqual(stack, ["selected"])
        self.assertEqual(output.getvalue(), "")

    def test_while_rejects_non_boolean_or_multi_value_condition(self):
        for source in (
            'while (1) => break end',
            'while ("not boolean") => break end',
        ):
            with self.subTest(source=source):
                analyser, _typed = analyse(source)
                self.assertTrue(analyser.diagnostics)

    def test_parameterised_while_rejects_body_that_does_not_rebuild_state(self):
        analyser, _typed = analyse(
            '0 while (< 3) -> (n: Integer) => pop end'
        )
        self.assertTrue(analyser.diagnostics)

    def test_nested_break_only_exits_innermost_loop(self):
        stack = execute("""
$total: Integer = 0
[1, 2, 3] foreach (outer) =>
  [10, 20, 30] foreach (inner) =>
    $total := + 1
    break
  end
end
$total
""")
        self.assertEqual(stack, [RuntimeNumber("3")])

    def test_empty_foreach_returns_none_for_each_break_result(self):
        stack = execute("""
[] as Integer+ foreach (n) =>
  break (1, 2, 3)
end
""")
        self.assertEqual(stack, [ObjectValue("None", {})] * 3)

    def test_lazy_foreach_can_break_without_forcing_remaining_items(self):
        stack = execute("""
range(1, 1000000) foreach (n) =>
  break ($n)
end
""")
        self.assertEqual(stack, [RuntimeNumber("1"), RuntimeNumber("1")])

    def test_break_releases_loop_local_owned_values_exactly_once(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("""
object Temp =>
  $name: String
  define ~Temp => $self.name println
end
[1] foreach (n) =>
  $temp = Temp("released")
  break
end
""")
        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "released\n")

    def test_foreach_index_and_value_bindings_do_not_escape_scope(self):
        analyser, _typed = analyse("""
[10] foreach (item, index) =>
  $item $index pop pop
end
$item
""")
        self.assertTrue(analyser.diagnostics)

    def test_break_values_from_nested_if_have_stable_arity_after_round_trip(self):
        source = """
[1, 2, 3] foreach (n) =>
  if ($n == 2) =>
    break ($n, $n * 10)
  end
end
"""
        expected = [RuntimeNumber("2"), RuntimeNumber("20")]
        self.assertEqual(execute(source), expected)
        self.assertEqual(execute(source, round_trip=True), expected)

    def test_at_implicit_and_explicit_forms_are_equivalent_after_round_trip(self):
        implicit = """
[[1, 2], [3, 4]]
[10, 20]
at (row+, item) => append
"""
        explicit = """
[[1, 2], [3, 4]]
[10, 20]
at (row+, item) => $row append $item
"""
        expected = [[
            [RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("10")],
            [RuntimeNumber("3"), RuntimeNumber("4"), RuntimeNumber("20")],
        ]]
        self.assertEqual(execute(implicit), expected)
        self.assertEqual(execute(explicit), expected)
        self.assertEqual(execute(implicit, round_trip=True), expected)
        self.assertEqual(execute(explicit, round_trip=True), expected)

    def test_at_scalar_broadcast_matches_manually_repeated_values(self):
        broadcast = execute("""
[[1], [2], [3]]
9
at (row+, item) => append
""")
        repeated = execute("""
[[1], [2], [3]]
[9, 9, 9]
at (row+, item) => append
""")
        self.assertEqual(broadcast, repeated)

    def test_at_rejects_impossible_stop_rank_before_runtime(self):
        analyser, _typed = analyse('[1, 2] at (items++) => top')
        self.assertTrue(analyser.diagnostics)
        self.assertIn("requires rank 2", "\n".join(analyser.diagnostics))

    def test_unfold_rejects_invalid_state_arity(self):
        for source in (
            '1 unfold -> (n: Integer) => $n dup dup end',
            '1 unfold -> (n: Integer) => 1 2 3 end',
        ):
            with self.subTest(source=source):
                analyser, _typed = analyse(source)
                self.assertTrue(analyser.diagnostics)

    def test_unfold_panic_is_propagated_with_runtime_context(self):
        with self.assertRaises(RuntimeError) as caught:
            execute("""
1 unfold (true) -> (n: Integer) =>
  RuntimeFault("transition failed") panic
end | #-infinite | first
""")
        self.assertIn("transition failed", str(caught.exception))

    def test_unfold_is_lazy_until_a_consumer_requests_an_item(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("""
1 unfold (true) -> (n: Integer) =>
  "advanced" println
  $n + 1
end
""")
        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(output.getvalue(), "")

    def test_malformed_nested_control_flow_fails_with_parse_error(self):
        malformed = """
while (true) =>
  if (true) =>
    [1, 2] foreach (n) =>
      at (item) => [1, 2
"""
        with self.assertRaises(ParseError):
            parse(malformed)


if __name__ == "__main__":
    unittest.main()
