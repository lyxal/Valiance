import os
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import FFIScalarValue, RuntimeNumber
from valiance.runtime.concurrency import Scheduler


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

    def test_scheduler_external_completion_is_thread_safe(self):
        import threading
        scheduler = Scheduler()
        observed = []
        registration = scheduler.register_external(lambda: observed.append("woke"), waitable=True)

        worker = threading.Thread(
            target=lambda: scheduler.enqueue_external_completion(registration.fire)
        )
        worker.start()
        scheduler.run_until(lambda: bool(observed))
        worker.join()
        self.assertEqual(observed, ["woke"])
        self.assertFalse(registration.active)


    @unittest.skipIf(os.name == "nt", "requires a POSIX shared-library compiler")
    def test_native_calls_suspend_tasks_and_overlap_on_workers(self):
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "blocking.c")
            library_path = os.path.join(directory, "libblocking.so")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <unistd.h>\n"
                    "static int active = 0;\n"
                    "static int maximum = 0;\n"
                    "int slow(void) {\n"
                    "  int current = __sync_add_and_fetch(&active, 1);\n"
                    "  int seen;\n"
                    "  do { seen = maximum; if (seen >= current) break; }\n"
                    "  while (!__sync_bool_compare_and_swap(&maximum, seen, current));\n"
                    "  usleep(100000);\n"
                    "  __sync_sub_and_fetch(&active, 1);\n"
                    "  return current;\n"
                    "}\n"
                    "int max_active(void) { return maximum; }\n"
                )
            subprocess.run(
                ["cc", "-shared", "-fPIC", source_path, "-o", library_path],
                check=True,
            )
            source = f"""import {{ffi(\"{library_path}\") as native}}
link native.slow() -> &int as slow
link native.max_active() -> &int as maxActive
$first = fn => slow end | spawn
$second = fn => slow end | spawn
$first wait | pop
$second wait | pop
maxActive
"""
            self.assertEqual(execute(source), [FFIScalarValue("&int", 2)])
            self.assertEqual(
                execute(source, optimize=True, round_trip=True),
                [FFIScalarValue("&int", 2)],
            )

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

class FFIForeignThreadCallbackTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "requires a POSIX shared-library compiler")
    def test_foreign_thread_callback_hands_off_to_scheduler(self):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            source_path = os.path.join(directory, "callback.c")
            library_path = os.path.join(directory, "libcallback.so")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <pthread.h>\n"
                    "typedef int (*Callback)(int);\n"
                    "typedef struct { Callback cb; int result; } Args;\n"
                    "static void* run_cb(void* raw){Args* a=(Args*)raw;"
                    "a->result=a->cb(a->cb(9));return 0;}\n"
                    "int apply_foreign(Callback cb){Args a={cb,0};pthread_t t;"
                    "pthread_create(&t,0,run_cb,&a);pthread_join(t,0);"
                    "return a.result;}\n"
                )
            subprocess.run(
                ["cc", "-shared", "-fPIC", "-pthread", source_path, "-o", library_path],
                check=True,
            )
            source = f'''import {{ffi("{library_path}") as cb}}
link cb.apply_foreign(:Function[int -> int]) -> &int as applyForeign
$task = fn -> int =>
  fn (value: int) -> int => $value end
  applyForeign
end | spawn
$task wait
'''
            expected = [FFIScalarValue("&int", 9)]
            self.assertEqual(execute(source), expected)
            self.assertEqual(execute(source, optimize=True, round_trip=True), expected)

class PublicTaskControlTests(unittest.TestCase):
    """Exercise public cancellation and logical-time timeout operations."""

    def test_cancel_consumes_task_and_requests_cancellation(self):
        self.assertEqual(execute("$task = fn -> Int => 42 end | spawn\n$task cancel"), [])

    def test_timeout_returns_task_result_before_deadline(self):
        source = "$task = fn -> Int => 42 end | spawn\n$task 10 timeout"
        self.assertEqual(execute(source), [RuntimeNumber(42)])
        self.assertEqual(execute(source, optimize=True, round_trip=True), [RuntimeNumber(42)])

    def test_zero_timeout_cancels_blocked_task(self):
        source = """$channel = Channel[Int]
$task = fn -> Receive[Int] => $channel receive end | spawn
$task 0 timeout
"""
        with self.assertRaisesRegex(RuntimeError, "cancelled task"):
            execute(source)
        with self.assertRaisesRegex(RuntimeError, "cancelled task"):
            execute(source, optimize=True, round_trip=True)

    def test_timeout_requires_non_negative_runtime_delay(self):
        source = "$task = fn -> Int => 42 end | spawn\n$task -1 timeout"
        with self.assertRaisesRegex(RuntimeError, "non-negative Int"):
            execute(source)
