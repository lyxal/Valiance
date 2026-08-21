import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import RuntimeNumber


def execute(source: str, *, optimize: bool = False, round_trip: bool = False):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=optimize)
    if round_trip:
        program = loads(dumps(program))
    return run(program)


class ConcurrencyExecutionTests(unittest.TestCase):
    def test_spawn_wait_executes_once(self):
        self.assertEqual(
            execute("fn -> Int => 42 end | spawn | wait"),
            [RuntimeNumber(42)],
        )

    def test_spawn_captures_explicit_argument_at_spawn(self):
        self.assertEqual(
            execute(
                "10 fn (value: Int) -> Int => $value 2 * end | spawn | wait"
            ),
            [RuntimeNumber(20)],
        )

    def test_wait_restores_multiple_outputs(self):
        self.assertEqual(
            execute('fn -> Int, String => 1 "one" end | spawn | wait'),
            [RuntimeNumber(1), "one"],
        )

    def test_concurrent_scope_preserves_body_outputs(self):
        self.assertEqual(
            execute("concurrent -> Int => 7 end"),
            [RuntimeNumber(7)],
        )

    def test_discarded_child_is_joined_at_scope_end(self):
        self.assertEqual(
            execute(
                """concurrent -> Int =>
  $ignored = fn -> Int => 3 end | spawn
  9
end"""
            ),
            [RuntimeNumber(9)],
        )

    def test_repeatable_wait_observes_one_execution(self):
        self.assertEqual(
            execute(
                """$task = fn -> Int => 6 end | spawn
$task wait
$task wait"""
            ),
            [RuntimeNumber(6), RuntimeNumber(6)],
        )

    def test_root_scope_joins_unobserved_child(self):
        self.assertEqual(
            execute("$task = fn -> Int => 8 end | spawn\n1"),
            [RuntimeNumber(1)],
        )

    def test_concurrency_bytecode_executes_after_roundtrip(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn -> Int => 5 end | spawn | wait"))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(loads(dumps(program))), [RuntimeNumber(5)])

    def test_two_tasks_mutate_shared_closure_capture_with_copy_on_write(self):
        source = """$base = [1, 2, 3]
$operation = fn -> Int+ =>
  $local = $base
  $local[0] := 99
  $local
end
$first = $operation spawn
$second = $operation spawn
$first wait
$second wait
$base"""
        expected = [
            [RuntimeNumber(99), RuntimeNumber(2), RuntimeNumber(3)],
            [RuntimeNumber(99), RuntimeNumber(2), RuntimeNumber(3)],
            [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)],
        ]
        self.assertEqual(execute(source), expected)
        self.assertEqual(execute(source, optimize=True, round_trip=True), expected)

    def test_parent_write_after_spawn_does_not_change_child_argument(self):
        source = """$base = [1, 2, 3]
$operation = fn (items: Int+) -> Int+ => $items end
$task = $base $operation spawn
$base[0] := 9
$task wait
$base"""
        result = execute(source)
        self.assertEqual(
            result[-2:],
            [
                [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)],
                [RuntimeNumber(9), RuntimeNumber(2), RuntimeNumber(3)],
            ],
        )

    def test_child_write_does_not_change_parent_argument(self):
        source = """$base = [1, 2, 3]
$operation = fn (items: Int+) -> Int+ =>
  $local = $items
  $local[1] := 8
  $local
end
$task = $base $operation spawn
$task wait
$base"""
        self.assertEqual(
            execute(source),
            [
                [RuntimeNumber(1), RuntimeNumber(8), RuntimeNumber(3)],
                [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)],
            ],
        )

    def test_lazy_range_crosses_task_boundary_without_materialization(self):
        source = """$values = range(1, 1000000)
$operation = fn (items: Int+) -> Int => $items first end
$task = $values $operation spawn
$task wait"""
        self.assertEqual(execute(source), [RuntimeNumber(1)])
        self.assertEqual(
            execute(source, optimize=True, round_trip=True),
            [RuntimeNumber(1)],
        )

    def test_discarded_failed_child_is_not_ignored(self):
        source = """concurrent -> Int =>
  $ignored = fn -> Int => RuntimeFault("child failed") panic end | spawn
  9
end"""
        for optimize, round_trip in ((False, False), (True, True)):
            with self.subTest(optimize=optimize, round_trip=round_trip):
                with self.assertRaisesRegex(Exception, "child failed"):
                    execute(source, optimize=optimize, round_trip=round_trip)

    def test_vectorised_wait_failure_is_selected_by_input_order(self):
        source = """$tasks = [
  fn -> Int => RuntimeFault("first failure") panic end | spawn,
  fn -> Int => RuntimeFault("second failure") panic end | spawn
]
$tasks wait"""
        with self.assertRaisesRegex(Exception, "first failure") as caught:
            execute(source, optimize=True, round_trip=True)
        self.assertNotIn("second failure", str(caught.exception))

    def test_body_panic_cancels_and_joins_spawned_child(self):
        source = """concurrent -> Int =>
  $pending = fn -> Int => 1 end | spawn
  RuntimeFault("body failed") panic
end"""
        with self.assertRaisesRegex(Exception, "body failed"):
            execute(source, optimize=True, round_trip=True)

    def test_handle_returned_from_closed_scope_is_terminal_and_waitable(self):
        source = """$task = concurrent -> Task[Int] =>
  fn -> Int => 12 end | spawn
end
$task wait
$task wait"""
        expected = [RuntimeNumber(12), RuntimeNumber(12)]
        self.assertEqual(execute(source), expected)
        self.assertEqual(execute(source, optimize=True, round_trip=True), expected)

    def test_zero_output_task_waits_without_stack_placeholder(self):
        source = """$task = fn -> => end | spawn
$task wait
7"""
        self.assertEqual(execute(source), [RuntimeNumber(7)])
        self.assertEqual(
            execute(source, optimize=True, round_trip=True),
            [RuntimeNumber(7)],
        )

    def test_nested_scope_joins_only_its_own_child(self):
        source = """concurrent -> Int =>
  $outer = fn -> Int =>
    concurrent -> Int =>
      $inner = fn -> Int => 5 end | spawn
      $inner wait
    end
  end | spawn
  $outer wait
end"""
        self.assertEqual(
            execute(source, optimize=True, round_trip=True),
            [RuntimeNumber(5)],
        )


if __name__ == "__main__":
    unittest.main()
