import contextlib
import io
import unittest
from unittest.mock import patch
from builtins import RuntimeError as PythonRuntimeError
from itertools import count, islice
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.elements.builtins import BUILTIN_ERROR_TYPES, BUILTIN_FAULT_TYPES
from valiance.parsing import parse
from valiance.runtime import (
    AssertionFailure,
    CompileError,
    RuntimeError,
    VirtualMachine,
    compile_program,
    dumps,
    loads,
    run,
)
from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
    ResolvedElementReference,
)
from valiance.runtime.vm import (
    FunctionValue,
    _borrow_destructor_receiver,
    _mark_mustcall_method,
    _release_value,
    _retain_value,
    _run_object_cleanup,
    _set_field,
)
from valiance.runtime.runtime_values import (
    DictValue,
    LazyList,
    ListValue,
    ObjectRuntimeType,
    ObjectValue,
    LazyPipelineStage,
    PlannedLazyList,
    PipelineTerminal,
    RuntimeNumber,
    RecordValue,
    format_runtime_value,
)


def execute(source: str, source_file: Path | None = None):
    program = parse(source)
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=False))


def execute_with_static_mustcall_backstop(source: str):
    """Execute code whose deliberate static violation exercises runtime fallback."""
    program = parse(source)
    analyser = Analyser()
    typed = analyser.analyse(program)
    unexpected = [
        message
        for message in analyser.diagnostics
        if "reaches destruction without calling" not in message
    ]
    if unexpected:
        raise AssertionError(unexpected)
    if not any(
        "reaches destruction without calling" in message
        for message in analyser.diagnostics
    ):
        raise AssertionError("expected a static @mustcall violation")
    return run(compile_program(typed, optimize=False))


def _materialize_lists(value):
    """Recursively materialize lazy Valiance list results for assertions."""
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [_materialize_lists(item) for item in value]
    return value


CONWAY_ASSIGNMENT_VARIANT = r"""
import {
  std.grids.allNeighbors,
  std.random.randbit
}

define step(board: Number++) -> Number++ =>
  $neighbors = $board allNeighbors(wrapping = true)
  $neighbors map fn (cells) =>
    [$cells[4], sum removeAt($cells, 4)] match =>
      [_, 3]  => 1
      [1, 2]  => 1
      _ => 0
    end
  end
end

const $BOARD_WIDTH = 10
const $BOARD_HEIGHT = 10

$board = range(1, $BOARD_WIDTH * $BOARD_HEIGHT) | map: randbit | reshape {$BOARD_WIDTH, $BOARD_HEIGHT}
$board := step
$board
"""


CONWAY_PIPELINE_VARIANT = r"""
import {std.grids.allNeighbors, std.random.randbit}

define step(board: Number++) -> Number++ =>
  $board allNeighbors(wrapping = true) | map fn (cells) =>
    [$cells[4], sum removeAt($cells, 4)] match =>
      [_, 3]  => 1
      [1, 2]  => 1
      _ => 0
    end
  end
end

const $(BOARD_WIDTH, BOARD_HEIGHT) = 10 | 10
$board = range(1, $BOARD_WIDTH * $BOARD_HEIGHT) | map: randbit | reshape {$BOARD_WIDTH, $BOARD_HEIGHT}
$board := step
$board
"""


class RuggedFlattenRegressionTests(unittest.TestCase):
    def test_t_double_rugged_match_flattens_recursively(self):
        source = """
        define[T] flatten(xs: T~) -> T+ =>
          $res: T+ = []
          $xs foreach (x) =>
            $res := addAll($x match =>
              as :T~~ => flatten
              _ => ^+
            end)
          end
          $res
        end
        flatten [[1,2,3],4,[[5],[[6]],[[[7,8]]]]]
        """
        self.assertEqual(
            execute(source),
            [[RuntimeNumber(str(value)) for value in range(1, 9)]],
        )

    def test_higher_order_flat_map_over_rugged_input(self):
        source = """
        define[T, U] flatMap(
          xs: T~,
          f: Function[T -> U]
        ) -> U+ =>
          $res: U+ = []
          $xs foreach (item) =>
            $res := addAll($item match =>
              as ls: T~~ => flatMap($ls, $f)
              as x => $f(^+ $x)
            end)
          end
          $res
        end
        flatMap(
          [[1,2],3,[[4],[[5,6]]]],
          fn (x: Integer) -> String => "v${$x}" end
        )
        """
        self.assertEqual(execute(source), [["v1", "v2", "v3", "v4", "v5", "v6"]])



    def test_minimum_rank_match_subtraction_expands_exact_interval(self):
        source = """
        define[T] accept(xs: T+2 | T+3 | T+4) -> Integer => 1 end
        define[T] classify(xs: T*2) -> Integer =>
          match =>
            as :T*5 => 0
            _ => accept
          end
        end
        classify [[1]]
        classify [[[1]]]
        classify [[[[1]]]]
        classify [[[[[1]]]]]
        """
        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("0"),
            ],
        )

    def test_rugged_rank_match_subtraction_expands_exact_interval(self):
        source = """
        define[T] accept(xs: T+2 | T+3 | T+4) -> Integer => 1 end
        define[T] classify(xs: T~2) -> Integer =>
          match =>
            as :T*5 => 0
            _ => accept
          end
        end
        classify [[1]]
        classify [[[1]]]
        classify [[[[1]]]]
        classify [[[[[1]]]]]
        """
        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("0"),
            ],
        )


class RuntimeTests(unittest.TestCase):
    def test_if_else_materializes_missing_branch_values_as_none(self):
        """Pad a shorter conditional branch to its analysed stack shape."""
        source = """99
if (false) =>
  1 2
else =>
end
"""
        self.assertEqual(execute(source), [RuntimeNumber("99"), None, None])

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed)))
        self.assertEqual(
            VirtualMachine().run(restored),
            [RuntimeNumber("99"), None, None],
        )

    def test_if_else_does_not_pad_the_longer_branch(self):
        """Leave the selected branch values intact when it has full arity."""
        self.assertEqual(
            execute("if (true) => 1 2 else => end"),
            [RuntimeNumber("1"), RuntimeNumber("2")],
        )

    def test_assert_else_returns_assert_error(self):
        [value] = execute('assert => false else => "wrong value" end')
        self.assertIsInstance(value, ObjectValue)
        self.assertEqual(value.type_name, "AssertError")
        self.assertEqual(value.fields["value"], "wrong value")

    def test_assert_peeks_condition_inputs(self):
        self.assertEqual(
            execute("41 assert => + 1 == 42 end"),
            [RuntimeNumber("41")],
        )

    def test_top_level_assert_else_returns_result_on_both_paths(self):
        original, success = execute(
            '41 assert => + 1 == 42 else => "wrong value" end'
        )
        self.assertEqual(original, RuntimeNumber("41"))
        self.assertIsNone(success)

        [failure] = execute('assert => false else => "wrong value" end')
        self.assertEqual(failure.type_name, "AssertError")

    def test_std_testing_assertions(self):
        self.assertEqual(
            execute(
                "import { std.testing }\n"
                "testing.assertEqual(20 + 22, 42)\n"
                "testing.assertNotEqual(42, 43)\n"
                'testing.assertPanics: fn => RuntimeFault("boom") panic end'
            ),
            [],
        )
        with self.assertRaisesRegex(
            AssertionFailure,
            "expected: 43\nactual:   42",
        ):
            execute("import { std.testing }\n" "testing.assertEqual(20 + 22, 43)")

    def test_scalar_list_updates_preserve_fast_ownership_metadata(self):
        """Keep large scalar lists cheap across closures and indexed updates."""
        source = """
$tape = [0] overtake 30000
$i: Integer = 0
define identity(n: Integer) => $n
while ($i < 2) =>
  $tape[0] := + 1
  $i := + 1
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)

        self.assertEqual(vm.run(compile_program(typed, optimize=False)), [])
        tape = vm.globals["tape"]
        self.assertIsInstance(tape, ListValue)
        self.assertEqual(tape.runtime_rank, 1)
        self.assertIs(tape._ownership_trivial, True)
        self.assertEqual(tape[0], RuntimeNumber("2"))
        self.assertEqual(vm.globals["identity"].owned_names, frozenset())

    def test_scalar_record_updates_preserve_fast_ownership_metadata(self):
        """Keep scalar records out of recursive retain and release walks."""
        source = """
$point = record{x => 0, y => 1}
$i: Integer = 0
while ($i < 2) =>
  $point.x := + 1
  $i := + 1
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)

        self.assertEqual(vm.run(compile_program(typed, optimize=False)), [])
        point = vm.globals["point"]
        self.assertIsInstance(point, DictValue)
        self.assertIs(point._ownership_trivial, True)
        self.assertEqual(point["x"], RuntimeNumber("2"))

    def test_container_updates_use_borrowed_copy_on_write_receivers(self):
        """Mutate unique containers in place without leaking through aliases."""
        source = """
$list = [1, 2]
$list_alias = $list
$list[0] = 9
$record = record{x => 1, y => 2}
$record_alias = $record
$record.x = 9
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = loads(dumps(compile_program(typed, optimize=False)))
        borrowed = [
            instruction
            for instruction in program.main.instructions
            if instruction.op is OpCode.LOAD_VAR_BORROW
        ]
        self.assertEqual(
            [instruction.arg for instruction in borrowed],
            ["list", "record"],
        )

        vm = VirtualMachine(output=lambda _value: None)
        self.assertEqual(vm.run(program), [])
        self.assertEqual(vm.globals["list"], [RuntimeNumber("9"), RuntimeNumber("2")])
        self.assertEqual(
            vm.globals["list_alias"],
            [RuntimeNumber("1"), RuntimeNumber("2")],
        )
        self.assertIsNot(vm.globals["list"], vm.globals["list_alias"])
        self.assertEqual(
            vm.globals["record"],
            {"x": RuntimeNumber("9"), "y": RuntimeNumber("2")},
        )
        self.assertEqual(
            vm.globals["record_alias"],
            {"x": RuntimeNumber("1"), "y": RuntimeNumber("2")},
        )
        self.assertIsNot(vm.globals["record"], vm.globals["record_alias"])

    def test_deep_recursion_uses_iterative_vm_frames(self):
        """Run recursive Valiance calls beyond Python's recursion limit."""
        self.assertEqual(
            execute("""
$down = @recursive fn (n: Integer) -> Integer =>
  if ($n == 0) => return 0
  this($n - 1)
end
$down(5000)
"""),
            [RuntimeNumber("0")],
        )

    def test_deep_recursive_panic_unwinds_activation_stack(self):
        """Propagate panics through deep iterative activations into handlers."""
        self.assertEqual(
            execute("""
$down = @recursive fn (n: Integer) -> Integer =>
  if ($n == 0) => ValueFault("done") panic
  this($n - 1)
end
try =>
  $down(3000)
handle ValueFault =>
  42
end
"""),
            [RuntimeNumber("42")],
        )

    def test_executes_stack_arithmetic(self):
        self.assertEqual(execute("*(+(1, 2), 3)"), [RuntimeNumber("9")])
        self.assertEqual(execute("(1 + 2) * (3 + 4)"), [RuntimeNumber("21")])
        self.assertEqual(execute("5 -(2, _)"), [RuntimeNumber("-3")])

    def test_real_valued_scientific_exponent_executes_as_one_literal(self):
        result = execute("1.3e5.2")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], RuntimeNumber("1.3e5.2"))
        self.assertTrue(str(result[0]).startswith("206036.115019944753"))

    def test_zero_imaginary_component_preserves_real_value(self):
        self.assertEqual(execute("1i0"), [RuntimeNumber("1")])
        self.assertEqual(execute("1.5i0"), [RuntimeNumber("1.5")])

    def test_power_uses_the_principal_complex_branch(self):
        self.assertEqual(execute("-1 ** 0.5"), [RuntimeNumber("0i1")])

        result = RuntimeNumber("-2") ** RuntimeNumber("0.5i1")
        self.assertTrue(result.is_complex())

    def test_power_keeps_real_results_real(self):
        self.assertEqual(
            RuntimeNumber("0i1") ** RuntimeNumber("2"), RuntimeNumber("-1")
        )
        self.assertEqual(execute("9 ** 0.5"), [RuntimeNumber("3")])
        self.assertEqual(execute("-8 ** (1 / 3)"), [RuntimeNumber("-2")])
        self.assertEqual(execute("-8 ** (2 / 3)"), [RuntimeNumber("4")])
        self.assertEqual(execute("-8 ** (-1 / 3)"), [RuntimeNumber("-0.5")])

    def test_arithmetic_preserves_arbitrarily_large_integer_precision(self):
        value = "12345678901234567890123456789"

        self.assertEqual(
            execute(f"{value} + 2"),
            [RuntimeNumber("12345678901234567890123456791")],
        )
        self.assertEqual(
            execute(f"{value} - 2"),
            [RuntimeNumber("12345678901234567890123456787")],
        )
        self.assertEqual(
            execute(f"{value} * 3"),
            [RuntimeNumber("37037036703703703670370370367")],
        )
        self.assertEqual(execute(f"{value} / 1"), [RuntimeNumber(value)])

    def test_tag_validator_runs_at_runtime(self):
        source = """
tag #checked as computed
define #checked(:Number) -> #boolean Number => true end
1 #checked
"""
        self.assertEqual(execute(source), [RuntimeNumber("1")])

        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)
        bytecode = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(bytecode), [RuntimeNumber("1")])

    def test_declared_return_tag_selects_tagged_overload(self):
        output = io.StringIO()
        source = """
tag #sorted as computed
#sorted: + =>
  (#sorted Number, Number) -> #sorted Number
end

define sort(:Number+) -> #sorted Number+ => top
define #sorted min(:#sorted Number+) => println "Cheap min"
define min(:Number+) => println "Expensive min"

$ns = [1, 2, 3, 4, 5]
$sortedNs = sort $ns

min $ns
min $sortedNs
"""

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        bytecode = loads(dumps(compile_program(typed, optimize=False)))

        with contextlib.redirect_stdout(output):
            stack = run(bytecode)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Expensive min\nCheap min\n")

    def test_direct_tag_application_sources_explicit_parameter(self):
        output = io.StringIO()
        source = """
tag #sorted as computed
define sort(:Number+) -> #sorted Number+ => #sorted
define #sorted min(:#sorted Number+) => println "Cheap min"
$sorted = sort [1, 2, 3]
min $sorted
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Cheap min\n")

    def test_tagged_values_remain_operational_through_stack_shuffles(self):
        source = """
range(1, 100) filter: fn (n) =>
  [3, 5] | $n | swap | % | 0 | swap | in
end sum
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        direct = compile_program(typed, optimize=False)
        optimized = compile_program(typed)
        restored = loads(dumps(optimized))
        expected = [RuntimeNumber(2418)]

        self.assertEqual(run(direct), expected)
        self.assertEqual(run(optimized), expected)
        self.assertEqual(run(restored), expected)

    def test_tag_validator_failure_panics(self):
        with self.assertRaises(RuntimeError):
            execute("""
tag #checked as computed
define #checked(value: Number) -> #boolean Number => $value 2 == end
1 #checked
""")

    def test_executes_stack_shuffle_copy_and_move(self):
        self.assertEqual(
            execute("1 2 3 4\ncopy(a, b -> a, b, b)"),
            [
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("3"),
                RuntimeNumber("4"),
                RuntimeNumber("3"),
                RuntimeNumber("4"),
                RuntimeNumber("4"),
            ],
        )
        self.assertEqual(
            execute("1 2 3 4\nmove(a, _, b -> a, a, b)"),
            [
                RuntimeNumber("1"),
                RuntimeNumber("3"),
                RuntimeNumber("2"),
                RuntimeNumber("2"),
                RuntimeNumber("4"),
            ],
        )

    def test_optional_arguments_use_ecs_overrides_at_runtime(self):
        self.assertEqual(
            execute("""
define pick(a: Number, b: Number = 2) -> Number => $a $b +
3 pick(b = 4)
3 pick(_, 5)
"""),
            [RuntimeNumber("7"), RuntimeNumber("8")],
        )

    def test_drop_is_a_stateful_fused_pipeline_stage(self):
        planned = execute("range(1, 10) map: fn (n) => $n * 2 end | drop 3")[0]

        self.assertIsInstance(planned, PlannedLazyList)
        self.assertEqual(
            tuple(stage.name for stage in planned.stages),
            ("map", "drop"),
        )
        self.assertEqual(
            list(planned),
            [
                RuntimeNumber(8),
                RuntimeNumber(10),
                RuntimeNumber(12),
                RuntimeNumber(14),
                RuntimeNumber(16),
                RuntimeNumber(18),
                RuntimeNumber(20),
            ],
        )

    def test_first_terminates_a_planned_pipeline_after_one_output(self):
        consumed: list[int] = []

        def source():
            for value in range(1, 10):
                consumed.append(value)
                yield RuntimeNumber(value)

        planned = PlannedLazyList(
            source(),
            (LazyPipelineStage.filtering(lambda value: value > 3),),
        )
        terminal = PipelineTerminal.first("missing")

        self.assertEqual(planned.run_terminal(terminal), RuntimeNumber(4))
        self.assertEqual(consumed, [1, 2, 3, 4])

    def test_vector_membership_preparation_is_operand_order_invariant(self):
        sources = (
            "range(1, 100) filter fn (n) => " "$n % [3, 5] in swap 0 end sum",
            "range(1, 100) filter fn (n) => " "[3, 5] | $n | swap | % | 0 | swap | in end sum",
            "range(1, 100) filter fn (n) => "
            "[3, 5] | $n | swap | % | 0 | swap | in end sum",
        )
        results = [execute(source) for source in sources]
        self.assertEqual(
            results,
            [[RuntimeNumber(2418)], [RuntimeNumber(2418)], [RuntimeNumber(2418)]],
        )

    def test_symbolic_match_prepares_index_reduction_and_aggregate_patterns(self):
        source = """
fn (values: Integer+) -> Integer =>
  [$values[1], sum removeAt($values, 1)] match =>
    [_, 4] => 9
    [2, 2] => 8
    _ => 0
  end
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed, optimize=False))[0]
        prepared = vm.prepare_call(function, 1, 1)
        self.assertEqual(prepared.strategy, "symbolic-match")
        self.assertEqual(
            prepared.invoke1([RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)]),
            (RuntimeNumber(9),),
        )
        self.assertEqual(
            prepared.invoke1([RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(1)]),
            (RuntimeNumber(8),),
        )

    def test_guarded_match_uses_prepared_dispatch_plan(self):
        source = """
fn (n: Integer) -> String =>
  match =>
    if % 15 == 0 => "FizzBuzz"
    if % 5 == 0 => "Buzz"
    if % 3 == 0 => "Fizz"
    _ => "${top}"
  end
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(
            output=lambda _value: None,
            collect_optimization_stats=True,
        )
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(prepared.strategy, "match-dispatch")
        self.assertEqual(prepared.invoke1(RuntimeNumber(15)), ("FizzBuzz",))
        self.assertEqual(prepared.invoke1(RuntimeNumber(10)), ("Buzz",))
        self.assertEqual(prepared.invoke1(RuntimeNumber(9)), ("Fizz",))
        self.assertEqual(prepared.invoke1(RuntimeNumber(7)), ("7",))
        assert vm.optimization_stats is not None
        self.assertEqual(
            vm.optimization_stats.snapshot()["prepared.strategy.match-dispatch"],
            1,
        )

    def test_guard_bytecode_retains_statically_selected_overloads(self):
        """Do not discard analysed element slots while lowering match guards."""
        source = """
fn (n: Integer) -> String =>
  match =>
    if % 3 == 0 => "multiple"
    _ => "other"
  end
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        function = next(
            instruction.arg
            for instruction in program.main.instructions
            if isinstance(instruction.arg, FunctionCode)
        )
        jump = next(
            instruction
            for instruction in function.instructions
            if instruction.op is OpCode.JUMP_IF_MATCH
        )
        guard = jump.arg[0][0][1]
        guard_ops = tuple(instruction.op for instruction in guard.instructions)

        self.assertEqual(guard_ops.count(OpCode.CALL_RESOLVED_ELEMENT), 2)
        self.assertNotIn(OpCode.LOAD_ELEMENT, guard_ops)
        self.assertNotIn(OpCode.CALL, guard_ops)

    def test_guarded_match_cache_separates_integer_and_real_runtime_shapes(self):
        """Keep adaptive guard overload caching correct across numeric categories."""
        source = """
fn (n: Number) -> String =>
  match =>
    if % 2 == 0 => "even"
    _ => "other"
  end
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(prepared.invoke1(RuntimeNumber(4)), ("even",))
        self.assertEqual(prepared.invoke1(RuntimeNumber("4.5")), ("other",))
        self.assertEqual(prepared.invoke1(RuntimeNumber(6)), ("even",))

    def test_prepared_stats_report_structural_rejection_reasons(self):
        """Expose attempted prepared strategies only when statistics are enabled."""
        analyser = Analyser()
        typed = analyser.analyse(parse("fn (n: Integer) => $n + 1 end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(
            output=lambda _value: None,
            collect_optimization_stats=True,
        )
        function = vm.run(compile_program(typed))[0]
        vm.prepare_call(function, 1, 1)
        assert vm.optimization_stats is not None
        snapshot = vm.optimization_stats.snapshot()

        self.assertGreaterEqual(snapshot["prepared.rejected.constant"], 1)
        self.assertIn("prepared.strategy.resolved-builtin", snapshot)

    def test_guarded_match_map_preserves_source_order(self):
        result = execute("""
range(1, 16) map fn (n: Integer) =>
  match =>
    if % 15 == 0 => "FizzBuzz"
    if % 5 == 0 => "Buzz"
    if % 3 == 0 => "Fizz"
    _ => "${top}"
  end
end
""")
        self.assertEqual(
            list(result[0]),
            [
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
            ],
        )

    def test_optimization_stats_are_disabled_by_default(self):
        self.assertIsNone(VirtualMachine(output=lambda _value: None).optimization_stats)

    def test_optimization_stats_report_prepared_plan_reuse(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn (value: Integer) -> Integer => $value end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(
            output=lambda _value: None,
            collect_optimization_stats=True,
        )
        function = vm.run(compile_program(typed))[0]

        vm.prepare_call(function, 1, 1)
        vm.prepare_call(function, 1, 1)

        assert vm.optimization_stats is not None
        self.assertEqual(
            vm.optimization_stats.snapshot(),
            {
                "prepared.created": 1,
                "prepared.rejected.constant": 1,
                "prepared.strategy.identity": 2,
                "prepared.reused": 1,
            },
        )

    def test_prepared_leaf_treats_exact_rank_collection_as_scalar(self):
        source = """
fn (cells: Integer+) -> Integer => $cells[1] end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed, optimize=False))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(
            prepared.invoke1([RuntimeNumber(10), RuntimeNumber(20), RuntimeNumber(30)]),
            (RuntimeNumber(20),),
        )

    def test_vectorisation_failure_is_not_retried_as_scalar(self):
        """A fixed vectorisation policy must propagate its real execution failure."""
        vm = VirtualMachine(output=lambda _value: None)
        function = FunctionValue(
            FunctionCode((Instruction(OpCode.RETURN),), params=("value",)),
            {},
        )
        failure = RuntimeError("vector execution failed")

        with (
            patch(
                "valiance.runtime.vm._vectorize_function",
                side_effect=failure,
            ) as vectorize,
            patch.object(vm, "call") as scalar_call,
        ):
            with self.assertRaisesRegex(RuntimeError, "vector execution failed"):
                vm.call_value(function, [[RuntimeNumber(1)]])

        vectorize.assert_called_once()
        scalar_call.assert_not_called()

    def test_prepared_leaf_still_vectorises_above_parameter_rank(self):
        source = """
fn (cells: Integer+) -> Integer => $cells[0] end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed, optimize=False))[0]
        prepared = vm.prepare_call(function, 1, 1)

        result = prepared.invoke1(
            [
                [RuntimeNumber(1), RuntimeNumber(2)],
                [RuntimeNumber(3), RuntimeNumber(4)],
            ]
        )
        self.assertEqual(result, ([RuntimeNumber(1), RuntimeNumber(3)],))

    def test_prepared_call_exposes_arity_specialised_invocation(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse("fn (a: Integer, b: Integer) -> Integer => $a $b + end")
        )
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 2, 1)

        self.assertEqual(
            prepared.invoke2(RuntimeNumber(20), RuntimeNumber(22)),
            (RuntimeNumber(42),),
        )

    def test_take_is_a_stateful_fused_pipeline_stage(self):
        consumed: list[int] = []

        def source():
            for value in range(1, 10):
                consumed.append(value)
                yield RuntimeNumber(value)

        planned = PlannedLazyList(
            source(),
            (
                LazyPipelineStage.mapping(lambda value: value * 2),
                LazyPipelineStage.limiting(3),
            ),
        )

        self.assertEqual(
            list(planned),
            [RuntimeNumber(2), RuntimeNumber(4), RuntimeNumber(6)],
        )
        self.assertEqual(consumed, [1, 2, 3])

    def test_zero_take_does_not_pull_the_pipeline_source(self):
        consumed: list[int] = []

        def source():
            consumed.append(1)
            yield RuntimeNumber(1)

        planned = PlannedLazyList(
            source(),
            (LazyPipelineStage.limiting(0),),
        )

        self.assertEqual(list(planned), [])
        self.assertEqual(consumed, [])

    def test_user_function_vectorisation_reuses_prepared_scalar_plan(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse("fn (n: Integer) -> Integer => ($n * 2) + 1 end")
        )
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed, optimize=False))[0]

        result = vm.call_value(
            function,
            [[RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)]],
        )

        self.assertEqual(
            result,
            [[RuntimeNumber(3), RuntimeNumber(5), RuntimeNumber(7)]],
        )
        prepared = function.prepared_calls[(1, 1)]
        self.assertEqual(prepared.strategy, "straight-line")

    def test_lazy_list_traversals_replay_cached_values(self):
        consumed: list[int] = []

        def source():
            for value in range(1, 4):
                consumed.append(value)
                yield RuntimeNumber(value)

        values = LazyList(source())

        self.assertEqual(
            list(values),
            [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)],
        )
        self.assertEqual(
            list(values),
            [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)],
        )
        self.assertEqual(consumed, [1, 2, 3])

    def test_lazy_preview_preserves_the_complete_later_traversal(self):
        values = LazyList(RuntimeNumber(value) for value in range(1, 6))

        self.assertEqual(
            format_runtime_value(values, lazy_preview_limit=2),
            "[1, 2, ...]",
        )
        self.assertEqual(
            list(values),
            [
                RuntimeNumber(1),
                RuntimeNumber(2),
                RuntimeNumber(3),
                RuntimeNumber(4),
                RuntimeNumber(5),
            ],
        )

    def test_dup_prints_the_same_lazy_range_twice(self):
        output: list[str] = []
        program = parse("range(1, 3) dup | println | println")
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertFalse(analyser.diagnostics)

        run(compile_program(typed, optimize=False), output=output.append)

        self.assertEqual(output, ["[1, 2, 3]\n", "[1, 2, 3]\n"])

    def test_length_preserves_a_duplicated_lazy_range(self):
        stack = execute("range(1, 10) dup | length")

        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(stack[1], RuntimeNumber(10))
        self.assertEqual(
            list(stack[0]),
            [RuntimeNumber(value) for value in range(1, 11)],
        )

    def test_planned_lazy_pipeline_replays_without_reexecuting_operations(self):
        mapped: list[int] = []
        source = LazyList(RuntimeNumber(value) for value in range(1, 4))

        def track(value):
            mapped.append(int(value))
            return value * 2

        values = PlannedLazyList(
            source,
            (LazyPipelineStage.mapping(track),),
        )

        self.assertEqual(
            list(values),
            [RuntimeNumber(2), RuntimeNumber(4), RuntimeNumber(6)],
        )
        self.assertEqual(
            list(values),
            [RuntimeNumber(2), RuntimeNumber(4), RuntimeNumber(6)],
        )
        self.assertEqual(mapped, [1, 2, 3])

    def test_length_counts_a_terminating_unfold_without_python_size(self):
        result = execute(
            "1 unfold (< 5) -> (n: Integer) => $n 1 + end | #-infinite | length"
        )

        self.assertEqual(result, [RuntimeNumber(4)])

    def test_length_fuses_a_finite_planned_pipeline(self):
        result = execute("range(1, 20) filter: fn (n) => $n % 2 == 0 end | length")
        self.assertEqual(result, [RuntimeNumber(10)])

    def test_prepared_predicate_uses_general_plan_for_new_shapes(self):
        result = execute("range(1, 10) filter: fn (n) => $n > 7 end | sum")
        self.assertEqual(result, [RuntimeNumber(27)])

    def test_map_builds_a_fusible_lazy_pipeline(self):
        result = execute("range(1, 5) map: fn (n) => $n % 3 end")[0]

        self.assertIsInstance(result, PlannedLazyList)
        self.assertEqual(tuple(stage.name for stage in result.stages), ("map",))
        self.assertEqual(
            list(result),
            [
                RuntimeNumber(1),
                RuntimeNumber(2),
                RuntimeNumber(0),
                RuntimeNumber(1),
                RuntimeNumber(2),
            ],
        )

    def test_map_filter_sum_executes_one_fused_pipeline(self):
        result = execute(
            "range(1, 20) "
            "map: fn (n) => $n % 7 end | "
            "filter: fn (n) => $n > 2 end | "
            "sum"
        )

        self.assertEqual(result, [RuntimeNumber(54)])

    def test_straight_line_plan_handles_multiple_resolved_calls(self):
        source = "fn (n: Integer) -> Integer => ($n * 2) + 1 end"
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed, optimize=False))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(prepared.strategy, "straight-line")
        self.assertEqual(prepared(RuntimeNumber(5)), (RuntimeNumber(11),))

    def test_prepared_niladic_scalar_uses_constant_strategy(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn () -> Integer => 7 end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 0, 1)

        self.assertEqual(prepared.strategy, "constant")
        self.assertEqual(prepared(), (RuntimeNumber(7),))
        self.assertIs(prepared, vm.prepare_call(function, 0, 1))

    def test_prepared_unary_identity_uses_identity_strategy(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn (value: Integer) -> Integer => $value end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(prepared.strategy, "identity")
        self.assertEqual(prepared(RuntimeNumber(9)), (RuntimeNumber(9),))

    def test_prepared_unary_wrapper_uses_resolved_builtin_strategy(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn (n: Integer) -> Integer => $n % 7 end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]

        first = vm.prepare_call(function, 1, 1)
        second = vm.prepare_call(function, 1, 1)

        self.assertIs(first, second)
        self.assertEqual(first.strategy, "resolved-builtin")
        self.assertEqual(first(RuntimeNumber(10)), (RuntimeNumber(3),))

    def test_prepared_binary_wrapper_uses_resolved_builtin_strategy(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse("fn (a: Integer, b: Integer) -> Integer => " "$a $b + end")
        )
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 2, 1)

        self.assertEqual(prepared.strategy, "resolved-builtin")
        self.assertEqual(
            prepared(RuntimeNumber(6), RuntimeNumber(7)),
            (RuntimeNumber(13),),
        )

    def test_prepared_wrapper_falls_back_for_collection_vectorisation(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("fn (n: Integer) -> Integer => $n % 7 end"))
        self.assertEqual(analyser.diagnostics, [])
        vm = VirtualMachine(output=lambda _value: None)
        function = vm.run(compile_program(typed))[0]
        prepared = vm.prepare_call(function, 1, 1)

        self.assertEqual(
            list(prepared([RuntimeNumber(8), RuntimeNumber(9)])[0]),
            [RuntimeNumber(1), RuntimeNumber(2)],
        )

    def test_vectorises_scalar_overloads_over_lists(self):
        self.assertEqual(
            execute("[1, 2, 3] + [5, 6, 7]"),
            [[RuntimeNumber("6"), RuntimeNumber("8"), RuntimeNumber("10")]],
        )
        self.assertEqual(
            execute("[1, 2, 3] + 10"),
            [[RuntimeNumber("11"), RuntimeNumber("12"), RuntimeNumber("13")]],
        )

    def test_exact_parameter_executes_as_ordinary_scalar_value(self):
        self.assertEqual(
            execute("""
$myfun = fn (:Number novec) => double
$myfun(10)
"""),
            [RuntimeNumber("20")],
        )

    def test_exact_collection_broadcasts_while_other_parameter_vectorises(self):
        self.assertEqual(
            execute("""
define keep(xs: Number+ novec, x: Number) -> Number+ => $xs end
[10, 20, 30] [1, 2] keep
"""),
            [
                [
                    [RuntimeNumber("10"), RuntimeNumber("20"), RuntimeNumber("30")],
                    [RuntimeNumber("10"), RuntimeNumber("20"), RuntimeNumber("30")],
                ]
            ],
        )

    def test_extend_default_substitutes_missing_values_and_runs_once(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            result = execute('[1, 2, 3] [4] + extend(0 | "default evaluated" println)')

        self.assertEqual(
            result,
            [[RuntimeNumber("5"), RuntimeNumber("2"), RuntimeNumber("3")]],
        )
        self.assertEqual(output.getvalue(), "default evaluated\n")

    def test_extend_patterns_select_by_missing_argument_positions(self):
        self.assertEqual(
            execute("""
[1, 2, 3] [4, 5] + extend =>
  (lhs, _) => $lhs end
  (_, rhs) => $rhs end
end
[1, 2] [4, 5, 6] + extend =>
  (lhs, _) => $lhs end
  (_, rhs) => $rhs end
end
"""),
            [
                [RuntimeNumber("5"), RuntimeNumber("7"), RuntimeNumber("6")],
                [RuntimeNumber("5"), RuntimeNumber("7"), RuntimeNumber("12")],
            ],
        )

    def test_extend_selector_receives_optionals(self):
        self.assertEqual(
            execute("[1, 2, 3] [4, 5] + extend: or"),
            [[RuntimeNumber("5"), RuntimeNumber("7"), RuntimeNumber("6")]],
        )

    def test_extend_applies_to_vectorised_user_functions(self):
        source = """
define add(a: Integer, b: Integer) -> Integer => $a $b + end
[1, 2, 3] [4, 5] add extend(0)
"""
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertEqual(analyser.diagnostics, [])
        bytecode = loads(dumps(compile_program(typed, optimize=False)))

        self.assertEqual(
            run(bytecode),
            [[RuntimeNumber("5"), RuntimeNumber("7"), RuntimeNumber("3")]],
        )

    def test_extend_preserves_lazy_vectorisation(self):
        [result] = execute("range(1, 3) [10, 20] + extend(0)")

        self.assertIsInstance(result, LazyList)
        self.assertEqual(
            list(result),
            [RuntimeNumber("11"), RuntimeNumber("22"), RuntimeNumber("3")],
        )

    def test_repeats_strings_with_number_on_either_side(self):
        self.assertEqual(execute('3 "ha" *'), ["hahaha"])
        self.assertEqual(execute('"ha" 3 *'), ["hahaha"])

    def test_structural_trait_element_calls_dispatch_to_runtime_shape(self):
        self.assertEqual(
            execute("""
define[T, U] dotProd(
  left: trait =>
    extend +(:T, :T) -> T
    extend *(:T, :U) -> T
  end +,
  right: U+
) =>
  * | reduce: +
end

["Fizz", "Buzz"] dotProd [0, 1]
"""),
            ["Buzz"],
        )

    def test_vectorises_scalar_overloads_over_lazy_lists(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, count(RuntimeNumber("1"))),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("10")),
                    Instruction(OpCode.LOAD_ELEMENT, "+"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        stack = run(program)

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(
            list(islice(stack[0], 5)),
            [
                RuntimeNumber("11"),
                RuntimeNumber("12"),
                RuntimeNumber("13"),
                RuntimeNumber("14"),
                RuntimeNumber("15"),
            ],
        )

    def test_generic_atomic_scalar_search_uses_equality_constraint(self):
        source = """
define[T: trait => extend ==(:T, :T) -> #boolean Number end] findScalar(
  xs: T+,
  x: T exact
) -> Integer? =>
  $xs foreach (item, pos) =>
    if ($item == $x) => return $pos
  end
  \\None
end

[1, 2, 3, 4, 5] findScalar 3
"""

        self.assertEqual(execute(source), [RuntimeNumber("2")])

    def test_generic_atomic_scalar_search_rejects_non_scalar_shapes(self):
        definition = """
define[T: trait => extend ==(:T, :T) -> #boolean Number end] findScalar(
  xs: T+,
  x: T exact
) -> Integer? =>
  $xs foreach (item, pos) =>
    if ($item == $x) => return $pos
  end
  \\None
end
"""

        for call in (
            "[[1, 2, 3, 4, 5]] findScalar 3",
            "[1, 2, 3, 4, 5] findScalar [3]",
        ):
            analyser = Analyser()
            analyser.analyse(parse(definition + call))
            self.assertTrue(analyser.diagnostics)

    def test_generic_atomic_rank_one_list_executes_with_unmarked_body_type(self):
        self.assertEqual(
            execute("""
define[T] rankOne(xs: T+ exact) -> T+ => $xs end
[1, 2, 3] rankOne
"""),
            [[RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("3")]],
        )

    def test_add_all_appends_items_to_target(self):
        self.assertEqual(
            execute("[1, 2] [3, 4] addAll"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                ]
            ],
        )

    def test_add_all_explicit_items_follow_target(self):
        self.assertEqual(
            execute("[1, 2] addAll([3, 4])"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                ]
            ],
        )

    def test_recursive_flatten_handles_rugged_lists(self):
        self.assertEqual(
            execute("""
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
"""),
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

    def test_result_ok_constructor_and_question_unwrap(self):
        self.assertEqual(execute("OK(1) ?"), [RuntimeNumber("1")])
        self.assertEqual(execute("OK(1) ?!"), [RuntimeNumber("1")])

    def test_builtin_qualified_element_bypasses_user_shadowing(self):
        self.assertEqual(
            execute("""
variant Maybe =>
  Some => $value: Number end
end
*::Some(1)
?
"""),
            [RuntimeNumber("1")],
        )

    def test_question_short_circuits_error_from_current_function(self):
        stack = execute("""
object ParseError => end
object ParseError as Err => end
define maybe_double(x: Result[Number, ParseError]) -> Number =>
  $x ?
  double
end
ParseError
maybe_double
""")

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], ObjectValue)
        self.assertEqual(stack[0].type_name, "ParseError")

    def test_question_bang_panics_on_result_error(self):
        with self.assertRaises(RuntimeError) as error:
            execute("""
object ParseError => end
object ParseError as Err => end
ParseError
?!
""")

        self.assertIn("UnwrappedResultFault", str(error.exception))

    def test_result_and_then_maps_ok_and_preserves_error(self):
        ok_stack = execute("OK(2) &: double")

        self.assertEqual(len(ok_stack), 1)
        self.assertIsInstance(ok_stack[0], ObjectValue)
        self.assertEqual(ok_stack[0].type_name, "OK")
        self.assertEqual(ok_stack[0].fields["value"], RuntimeNumber("4"))

        err_stack = execute("""
object ParseError => end
object ParseError as Err => end
ParseError
&: double
""")

        self.assertEqual(len(err_stack), 1)
        self.assertIsInstance(err_stack[0], ObjectValue)
        self.assertEqual(err_stack[0].type_name, "ParseError")

    def test_runtime_length_rejects_lazy_lists(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, count(1)),
                    Instruction(OpCode.LOAD_ELEMENT, "length"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        with self.assertRaises(PythonRuntimeError) as error:
            run(program)

        message = str(error.exception)
        self.assertIn("cannot call element 'length'", message)
        self.assertIn("attempted input shapes:", message)
        self.assertIn("(#-infinite Item+)", message)
        self.assertIn("stack: [[1, 2, 3, 4, 5", message)
        self.assertIn("100, ...]]", message)
        self.assertIn("stack types: [Unknown+]", message)
        self.assertIn("<main> ip 2: call", message)

    def test_randbit_supports_niladic_and_mapping_calls(self):
        with patch(
            "valiance.std.random.random.getrandbits",
            return_value=1,
        ):
            self.assertEqual(
                execute("import {std.random.randbit}\nrandbit"),
                [RuntimeNumber("1")],
            )

        with patch(
            "valiance.std.random.random.getrandbits",
            side_effect=(0, 1, 0),
        ):
            [mapped] = execute(
                "import {std.random.randbit}\n" "range(1, 3) map: randbit"
            )
            self.assertEqual(
                list(mapped),
                [RuntimeNumber("0"), RuntimeNumber("1"), RuntimeNumber("0")],
            )

    def test_all_neighbors_preserves_documented_order_and_edge_behavior(self):
        [without_wrapping] = execute(
            "import {std.grids.allNeighbors}\n"
            "[[1, 2, 3], [4, 5, 6], [7, 8, 9]] "
            "allNeighbors(wrapping = false)"
        )
        [with_wrapping] = execute(
            "import {std.grids.allNeighbors}\n"
            "[[1, 2, 3], [4, 5, 6], [7, 8, 9]] "
            "allNeighbors(wrapping = true)"
        )

        self.assertEqual(
            without_wrapping[0][0],
            [
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("4"),
                RuntimeNumber("5"),
            ],
        )
        self.assertEqual(
            without_wrapping[1][1],
            [RuntimeNumber(str(value)) for value in range(1, 10)],
        )
        self.assertEqual(
            with_wrapping[0][0],
            [
                RuntimeNumber("9"),
                RuntimeNumber("7"),
                RuntimeNumber("8"),
                RuntimeNumber("3"),
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("6"),
                RuntimeNumber("4"),
                RuntimeNumber("5"),
            ],
        )
        self.assertTrue(
            all(len(neighborhood) == 9 for row in with_wrapping for neighborhood in row)
        )

    def test_conway_assignment_variant_executes_one_generation(self):
        self._assert_conway_blinker(CONWAY_ASSIGNMENT_VARIANT)

    def test_conway_pipeline_variant_executes_one_generation(self):
        self._assert_conway_blinker(CONWAY_PIPELINE_VARIANT)

    def _assert_conway_blinker(self, source: str):
        bits = [
            int(row == 4 and column in {3, 4, 5})
            for row in range(10)
            for column in range(10)
        ]
        with patch(
            "valiance.std.random.random.getrandbits",
            side_effect=bits,
        ):
            [board] = execute(source)
            board = _materialize_lists(board)

        live_cells = {
            (row, column)
            for row, values in enumerate(board)
            for column, value in enumerate(values)
            if value == RuntimeNumber("1")
        }
        self.assertEqual(live_cells, {(3, 4), (4, 4), (5, 4)})
        self.assertEqual(len(board), 10)
        self.assertTrue(all(len(row) == 10 for row in board))

    def test_executes_element_with_colon_function_argument(self):
        stack = execute("[1, 2, 3] map: double")

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(
            list(stack[0]),
            [RuntimeNumber("2"), RuntimeNumber("4"), RuntimeNumber("6")],
        )

    def test_colon_context_sets_implicit_modifier_arity(self):
        stack = execute("[1, 2, 3] map: +")

        self.assertEqual(
            list(stack[0]),
            [RuntimeNumber("2"), RuntimeNumber("4"), RuntimeNumber("6")],
        )

    def test_colon_context_sets_explicit_inferred_function_arity(self):
        stack = execute(
            """
3
range(1, _)
map: fn => [top] overtake swap | /: *
reverse | /: **
"""
        )

        self.assertEqual(stack, [RuntimeNumber("531441")])

    def test_ecs_call_accepts_explicit_function_modifier(self):
        ecs_stack = execute("map([1, 2, 3]): fn => * 2 end")
        postfix_stack = execute("[1, 2, 3] map: * 2")

        self.assertEqual(list(ecs_stack[0]), list(postfix_stack[0]))

    def test_map_prefers_exact_rank_for_inferred_function(self):
        self.assertEqual(
            execute(
                '[["this", "is", "a"], ["matrix", "of"], ["strings"]] '
                'map fn (s) => first | length end'
            ),
            [[RuntimeNumber("4"), RuntimeNumber("6"), RuntimeNumber("7")]],
        )

    def test_map_dispatches_overloaded_functions_across_union_items(self):
        output = io.StringIO()
        source = """
$lst = [1, 2, "A", "B"]

define foo => * 2
println($lst map: * 2)
println($lst map fn => * 2)
println($lst map: foo)
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(
            output.getvalue(),
            "[2, 4, AA, BB]\n[2, 4, AA, BB]\n[2, 4, AA, BB]\n",
        )

    def test_union_dispatch_accepts_broad_numeric_overload(self):
        output = io.StringIO()
        source = """
$lst = [1, "A"]
define classify(n: Number) -> String => "number"
define classify(s: String) -> String => "string"
$lst map: classify | println
"""

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = loads(dumps(compile_program(typed, optimize=False)))
        with contextlib.redirect_stdout(output):
            stack = run(program)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[number, string]\n")

    def test_union_dispatch_uses_statically_selected_broad_branch(self):
        output = io.StringIO()
        source = """
define widen(n: Number) -> Number => $n
define choose(n: Number) -> String => "number"
define choose(n: Integer) -> String => "integer"
define choose(s: String) -> String => "string"
[1 | widen, "A"] map: choose | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[number, string]\n")

    def test_union_dispatch_accepts_trait_and_variant_branches(self):
        output = io.StringIO()
        source = """
trait Shape => end
object Circle => end
object Circle as Shape => end
variant Maybe =>
  Some => $value: Number end
  None => end
end
define describe(x: Shape) -> String => "shape"
define describe(x: Maybe) -> String => "maybe"
define describe(x: String) -> String => $x
[Circle, Some(1), "text"] map: describe | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[shape, maybe, text]\n")

    def test_variant_extend_dispatches_to_member_element(self):
        output = io.StringIO()
        source = """
variant Shape =>
  extend getArea -> Number

  Circle =>
    $radius: Number
    define getArea => squared $self.radius * 3.14
  end
  Rectangle =>
    $width: Number
    $height: Number
    define getArea => $self.width * $self.height
  end
end

define asShape(value: Shape) -> Shape => $value
Circle(5) | asShape | getArea | println
Rectangle(4, 6) | asShape | getArea | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "78.5\n24\n")

    def test_external_element_overrides_variant_member_defaults(self):
        source = """
variant Shape =>
  extend getArea -> Number
  Circle =>
    $radius: Number
    define getArea => squared $self.radius
  end
  Rectangle =>
    $width: Number
    $height: Number
    define getArea => $self.width * $self.height
  end
end
define getArea(:Shape) -> Number => 99
Circle(5) | getArea
Rectangle(4, 6) | getArea
"""

        self.assertEqual(execute(source), [RuntimeNumber("99"), RuntimeNumber("99")])

    def test_union_dispatch_preserves_generic_arguments_in_literals(self):
        output = io.StringIO()
        source = """
trait Vehicle => end
object Car => end
object Car as Vehicle => end
object[T] Box => $value: T end
define describe(x: Box[Vehicle]) -> String => "vehicle box"
define describe(x: String) -> String => "string"
[Car | Box, "x"] map: describe | println
"""

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = loads(dumps(compile_program(typed, optimize=False)))
        with contextlib.redirect_stdout(output):
            stack = run(program)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[vehicle box, string]\n")

    def test_union_dispatch_uses_reified_disjoint_data_tags(self):
        output = io.StringIO()
        source = """
tag #left as computed
tag #right as computed
tag #left disjoint #right
define label(x: #left Integer) -> String => "left"
define label(x: #right Integer) -> String => "right"
[1 #left, 2 #right] map: label | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[left, right]\n")

    def test_eager_map_with_println_executes_immediately(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            stack = execute("[1, 2, 3] map: println")

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "1\n2\n3\n")

    def test_pipe_after_map_modifier_prints_mapped_list_once(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            stack = execute("[1, 2, 3, 4] map: * 2 | println")

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "[2, 4, 6, 8]\n")

    def test_executes_call_site_checked_builtins(self):
        self.assertEqual(
            execute("1 2 peek: +"),
            [RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("3")],
        )
        self.assertEqual(
            execute("1 2 3 dip: +"), [RuntimeNumber("3"), RuntimeNumber("3")]
        )
        self.assertEqual(
            execute("2 fork: (double, double)"),
            [RuntimeNumber("4"), RuntimeNumber("4")],
        )
        self.assertEqual(
            execute("6 7 (fn (:Number, :Number) => + end) call"),
            [RuntimeNumber("13")],
        )
        self.assertEqual(
            execute("call(fn (:Number, :Number) => + end, 6, 7)"),
            [RuntimeNumber("13")],
        )
        self.assertEqual(
            execute("fn => + end | call(6, 7)"),
            [RuntimeNumber("13")],
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(execute("fn => + end | call(6, 7) | println"), [])
        self.assertEqual(output.getvalue(), "13\n")
        self.assertEqual(
            execute("""
define choose(n: Number) -> Number => $n 1 +
define choose(i: Integer) -> String => "int"
fn => choose end | call{Number}(6)
"""),
            [RuntimeNumber("7")],
        )

    def test_explicit_call_treats_optional_collection_union_as_one_argument(self):
        source = """
fn (:(Number | String+)?) =>
  match =>
    ["hello", "world"] => "kind greeting"
    _ => "Nothing"
  end
end
call(["hello", "world"])
"""
        self.assertEqual(execute(source), ["kind greeting"])

    def test_executes_both_for_zero_through_three_input_callables(self):
        self.assertEqual(
            execute("both: fn => 7 end"),
            [RuntimeNumber("7"), RuntimeNumber("7")],
        )
        self.assertEqual(
            execute("1 2 both: double"),
            [RuntimeNumber("2"), RuntimeNumber("4")],
        )
        self.assertEqual(
            execute("1 2 3 4 both: +"),
            [RuntimeNumber("3"), RuntimeNumber("7")],
        )
        self.assertEqual(
            execute("1 2 3 4 5 6 " "both: fn (:Number, :Number, :Number) => + + end"),
            [RuntimeNumber("6"), RuntimeNumber("15")],
        )

    def test_executes_sequence_with_distinct_callable_arities(self):
        self.assertEqual(
            execute("1 2 sequence: (double, squared)"),
            [RuntimeNumber("2"), RuntimeNumber("4")],
        )
        self.assertEqual(
            execute("1 2 3 sequence: (double, +)"),
            [RuntimeNumber("2"), RuntimeNumber("5")],
        )
        self.assertEqual(
            execute(
                "1 2 3 4 5 sequence: "
                "(+, fn (:Number, :Number, :Number) => + + end)"
            ),
            [RuntimeNumber("3"), RuntimeNumber("12")],
        )
        self.assertEqual(
            execute("1 sequence: (fn => 9 end, double)"),
            [RuntimeNumber("9"), RuntimeNumber("2")],
        )

    def test_both_and_sequence_can_infer_enclosing_function_inputs(self):
        self.assertEqual(
            execute("""
$f = fn => both: + end
1 2 3 4 $f()
"""),
            [RuntimeNumber("7")],
        )
        self.assertEqual(
            execute("""
$f = fn => sequence: (double, +) end
1 2 3 $f()
"""),
            [RuntimeNumber("5")],
        )

    def test_sequence_serializes_its_call_site_arity_metadata(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("1 2 3 sequence: (double, +)"))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        references = tuple(
            instruction.arg
            for instruction in program.main.instructions
            if instruction.op is OpCode.CALL_RESOLVED_ELEMENT
            and isinstance(instruction.arg, ResolvedElementReference)
            and instruction.arg.name == "sequence"
        )

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].static_values, (1, 2))
        self.assertEqual(
            run(loads(dumps(program))),
            [RuntimeNumber("2"), RuntimeNumber("5")],
        )

    def test_executes_reduce_slash_overload(self):
        self.assertEqual(execute("[1, 2, 3, 4] /: +"), [RuntimeNumber("10")])

    def test_fork_runtime_passes_suffix_to_shorter_modifier(self):
        self.assertEqual(
            execute("""
define keep_name(name: String, n: Number) -> String => $name
"tag" 2 fork: (keep_name, double)
"""),
            ["tag", RuntimeNumber("4")],
        )

    def test_compiler_emits_resolved_builtin_element_calls(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, ops)
        self.assertEqual(run(program), [RuntimeNumber("3")])

    def test_checked_cast_emits_runtime_check(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('if true => 1 else => "x" end as![String]'))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertIn(OpCode.CHECK_CAST, ops)
        with self.assertRaises(RuntimeError) as error:
            run(program)
        self.assertIn("checked cast failed", str(error.exception))

    def test_optional_cast_returns_some_on_success_and_none_on_failure(self):
        success = execute('if false => 1 else => "x" end as?[String]')
        failure = execute('if true => 1 else => "x" end as?[String]')

        [some] = success
        [none] = failure
        self.assertEqual(some.type_name, "Some")
        self.assertEqual(some.fields["value"], "x")
        self.assertEqual(none.type_name, "None")

    def test_optional_cast_bytecode_round_trip(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('if false => 1 else => "x" end as?[String]'))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed, optimize=False)))

        self.assertIn(
            OpCode.TRY_CAST,
            tuple(instruction.op for instruction in restored.main.instructions),
        )
        [value] = run(restored)
        self.assertEqual(value.type_name, "Some")
        self.assertEqual(value.fields["value"], "x")

    def test_statically_safe_checked_cast_is_lowered_as_an_unchecked_upcast(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('ValueError("x") as![Err]'))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertNotIn(OpCode.CHECK_CAST, ops)
        [value] = run(program)
        self.assertIsInstance(value, ObjectValue)
        self.assertEqual(value.type_name, "ValueError")

    def test_niladic_none_element_pushes_none_value(self):
        [value] = execute("\\None")
        self.assertEqual(value, ObjectValue("None", {}))

        analyser = Analyser()
        analyser.analyse(parse("None"))
        self.assertTrue(any("unknown element 'None'" in item for item in analyser.diagnostics))

    def test_none_type_patterns_match_the_runtime_none_value(self):
        self.assertEqual(
            execute("""
\\None
match =>
  as :None => "none"
  _ => "other"
end
"""),
            ["none"],
        )

    def test_tagged_type_patterns_require_the_runtime_tag(self):
        source = """
tag #km as unit
{value}
match =>
  as :#km Number => "tagged"
  _ => "plain"
end
"""
        self.assertEqual(execute(source.format(value="1")), ["plain"])
        self.assertEqual(execute(source.format(value="1 #km")), ["tagged"])

    def test_generic_type_patterns_check_reified_type_arguments(self):
        source = """
object[T] Box =>
  public $value: T
end
Box("s")
match =>
  as :Box[Number] => "number"
  _ => "other"
end
"""
        self.assertEqual(execute(source), ["other"])

    def test_empty_list_cast_executes_as_empty_list(self):
        self.assertEqual(execute("[] as[Number+]"), [[]])

    def test_element_disambiguation_controls_runtime_vectorisation_depth(self):
        self.assertEqual(
            execute("[[1, 2], [3, 4]] +{Number+, _} [10, 20]"),
            [
                [
                    [RuntimeNumber("11"), RuntimeNumber("22")],
                    [RuntimeNumber("13"), RuntimeNumber("24")],
                ]
            ],
        )

    def test_minimum_rank_argument_vectorises_to_exact_parameter_at_runtime(self):
        self.assertEqual(
            execute("""
define exactIn(:Number+) => 1
define \\rank1 -> Number* => [1, 2]
define \\rank2 -> Number* => [[1, 2], [3, 4]]
define \\rank3 -> Number* => [[[1], [2]], [[3], [4]]]
exactIn \\rank1
exactIn \\rank2
exactIn \\rank3
"""),
            [
                RuntimeNumber("1"),
                [RuntimeNumber("1"), RuntimeNumber("1")],
                [
                    [RuntimeNumber("1"), RuntimeNumber("1")],
                    [RuntimeNumber("1"), RuntimeNumber("1")],
                ],
            ],
        )

    def test_implicit_function_input_is_refined_by_later_collection_consumer(self):
        source = """
$mag = fn => ** 2 | reduce: + | sqrt
$mag([3, 4])
$mag([[3, 5], [4, 12]])
"""

        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("5"),
                [
                    RuntimeNumber("34") ** RuntimeNumber("0.5"),
                    RuntimeNumber("160") ** RuntimeNumber("0.5"),
                ],
            ],
        )

    def test_minimum_rank_pipeline_vectorises_scalar_stages_at_runtime(self):
        source = """
define Mag(:Real*) => ** 2 | reduce: + | sqrt
Mag [3, 4]
Mag [[3, 5], [4, 12]]
"""

        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("5"),
                [RuntimeNumber("5"), RuntimeNumber("13")],
            ],
        )

    def test_generic_println_consumes_minimum_rank_pipeline_result_whole(self):
        source = """
define Mag(:Real*) => ** 2 | reduce: + | sqrt
println Mag [3, 4]
println Mag [[3, 5], [4, 12]]
"""

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            execute(source)

        self.assertEqual(output.getvalue(), "5\n[5, 13]\n")

    def test_empty_list_return_inference_executes_for_all_rank_modes(self):
        self.assertEqual(
            execute("""
define exactIn(:Number+) => 1
define minIn(:Number*) => 1
define ruggedIn(:Number~) => 1
define[T] exactGen(:T+) => 1
define[T] minGen(:T*) => 1
define[T] rugGen(:T~) => 1
define \\exact -> Number+ => []
define \\min -> Number* => []
define \\rugged -> Number~ => []
exactIn \\exact
exactIn \\min
minIn \\exact
minIn \\min
ruggedIn \\exact
ruggedIn \\min
ruggedIn \\rugged
exactGen \\exact
exactGen \\min
minGen \\exact
minGen \\min
rugGen \\exact
rugGen \\min
rugGen \\rugged
"""),
            [RuntimeNumber("1")] * 14,
        )

    def test_compiler_emits_resolved_user_defined_element_calls(self):
        source = """
define add_one(n: Number) -> Number => $n 1 +
41 add_one
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, ops)
        self.assertEqual(run(program), [RuntimeNumber("42")])

    def test_control_flow_bodies_keep_resolved_element_calls(self):
        source = """
define fibonacci(n: Integer) -> Integer =>
  $n match =>
    0 => 0
    1 => 1
    _ => fibonacci($n - 1) + fibonacci($n - 2)
  end
end
$total = 0
range(1, 5) foreach (n) =>
  $total := + fibonacci($n)
end
$total
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        function_code = program.main.instructions[0].arg
        self.assertIsInstance(function_code, FunctionCode)
        function_ops = tuple(
            instruction.op for instruction in function_code.instructions
        )
        foreach_instruction = next(
            instruction
            for instruction in program.main.instructions
            if instruction.op is OpCode.FOREACH
        )
        foreach_code = foreach_instruction.arg[0]
        foreach_ops = tuple(instruction.op for instruction in foreach_code.instructions)

        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, function_ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, function_ops)
        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, foreach_ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, foreach_ops)
        self.assertEqual(run(program), [RuntimeNumber("12")])

    def test_inline_control_flow_keeps_resolved_element_calls(self):
        """Do not redo overload search inside while, assert, or try bodies."""
        source = """
define add1(n: Integer) => $n + 1
$i: Integer = 0
while ($i < 1) =>
  $i := add1 $i
end
assert => $i == 1 else => add1 $i end
try =>
  add1 $i
handle =>
  add1 $i
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertGreaterEqual(ops.count(OpCode.CALL_RESOLVED_ELEMENT), 6)
        self.assertNotIn(OpCode.LOAD_ELEMENT, ops)
        result = run(program)
        self.assertEqual(result[0], RuntimeNumber("0"))
        self.assertIsNone(result[1])
        self.assertEqual(result[2], RuntimeNumber("2"))


    def test_compiler_emits_every_user_defined_overload_body(self):
        source = """
define same(x, y) => $x $y +
1 2 same
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed, optimize=False)
        maker = program.main.instructions[0]
        self.assertEqual(maker.op, OpCode.MAKE_FUNCTION)
        self.assertIsInstance(maker.arg, FunctionSetCode)
        self.assertEqual(len(maker.arg.overloads), 6)
        self.assertEqual(run(program), [RuntimeNumber("3")])

    def test_repeated_defines_merge_user_defined_overloads(self):
        source = """
define triple(n: Number) -> Number => $n * 3
define triple(s: String) -> String => $s + $s + $s
triple 15
"""
        self.assertEqual(execute(source), [RuntimeNumber("45")])

        source = """
define triple(n: Number) -> Number => $n * 3
define triple(s: String) -> String => $s + $s + $s
triple "H"
        """
        self.assertEqual(execute(source), ["HHH"])

    def test_multimethod_dispatches_collision_by_runtime_object_types(self):
        source = """
trait Collidable => end
object Spaceship => end
object Spaceship as Collidable => end
object Asteroid => end
object Asteroid as Collidable => end

define asCollidable(value: Collidable) -> Collidable => $value
define collide(left: Collidable, right: Collidable) -> String => "Default collision"
multi define collide(left: Asteroid, right: Spaceship) -> String => "a/s"
multi define collide(left: Spaceship, right: Asteroid) -> String => "s/a"
multi define collide(left: Spaceship, right: Spaceship) -> String => "s/s"
multi define collide(left: Asteroid, right: Asteroid) -> String => "a/a"

Asteroid | asCollidable
Spaceship | asCollidable
collide
Spaceship | asCollidable
Asteroid | asCollidable
collide
"""
        self.assertEqual(execute(source), ["a/s", "s/a"])
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertEqual(analyser.diagnostics, [])
        bytecode = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(bytecode), ["a/s", "s/a"])

    def test_multimethod_dispatches_hutton_razor_extension(self):
        source = """
trait Expr => end
object Val => $n: Number
object Val as Expr => end
object Add =>
  $left: Expr
  $right: Expr
end
object Add as Expr => end

define asExpr(value: Expr) -> Expr => $value
define eval(value: Expr) -> Number => 0
multi define eval(value: Val) -> Number => $value.n
multi define eval(value: Add) -> Number =>
  $value.left eval
  $value.right eval
  +
end

object Mul =>
  $left: Expr
  $right: Expr
end
object Mul as Expr => end

multi define eval(value: Mul) -> Number =>
  $value.left eval
  $value.right eval
  *
end

Mul(Add(Val(2), Val(3)), Val(4)) | asExpr | eval
"""
        self.assertEqual(execute(source), [RuntimeNumber("20")])

    def test_where_rank_variable_is_available_in_function_body(self):
        source = """
define rank_of(xs: Number+$n) -> Number => $n
[[1], [2]] rank_of
"""
        self.assertEqual(execute(source), [RuntimeNumber("2")])

    def test_where_rank_variable_in_return_type_executes(self):
        source = """
define id_rank(xs: Number+$n) -> Number+$n => $xs
[[1], [2]] id_rank
"""
        self.assertEqual(
            _materialize_lists(execute(source)),
            [[[RuntimeNumber("1")], [RuntimeNumber("2")]]],
        )

    def test_where_computed_variable_is_available_at_runtime(self):
        source = """
define static_values(x: Number) -> Number, Number
where ($a = 1.5, $b = and(1, not(0))) => $a $b
1 static_values
"""
        self.assertEqual(execute(source), [RuntimeNumber("1.5"), RuntimeNumber("1")])

    def test_where_rank_function_executes_through_postfix_call(self):
        source = """
[[1], [2]] (fn (xs: Number+$n) -> Number => $n end) call
"""
        self.assertEqual(execute(source), [RuntimeNumber("2")])

    def test_where_function_executes_through_explicit_call(self):
        source = """
call(fn (shape: {Number...}) -> Number where ($n = length $shape) => $n end, {1, 2, 3})
"""
        self.assertEqual(execute(source), [RuntimeNumber("3")])

    def test_where_function_vectorises_through_explicit_call(self):
        source = """
call(fn (x: Number) -> Number where ($offset = 1) => $x $offset + end, [1, 2])
"""
        self.assertEqual(
            _materialize_lists(execute(source)),
            [[RuntimeNumber("2"), RuntimeNumber("3")]],
        )

    def test_where_call_site_checked_function_receives_static_values(self):
        source = """
define shape_len(shape: {Number...}) -> Number
where ($n = length $shape) => $n
{1, 2, 3} shape_len
"""
        self.assertEqual(execute(source), [RuntimeNumber("3")])

    def test_where_introspects_bare_function_signatures_at_call_site(self):
        source = """
define signature(f: Function) -> Number, Number, Number, Number
where (
  $a = $f.arity,
  $i = length $f.inputs,
  $m = $f.multiplicity,
  $o = length $f.outputs
) => $a $i $m $o
fn (x: Number, y: String) -> Number, String => 1 "x" end signature
"""
        self.assertEqual(
            execute(source),
            [
                RuntimeNumber("2"),
                RuntimeNumber("2"),
                RuntimeNumber("2"),
                RuntimeNumber("2"),
            ],
        )

    def test_where_variadic_tuple_rank_binding_backtracks_at_runtime(self):
        source = """
define ranks(xs: {Number+$n..., String+...}) -> Number => $n
{[1], ["a"]} ranks
"""
        self.assertEqual(execute(source), [RuntimeNumber("1")])

    def test_where_rank_variable_resolves_in_checked_cast(self):
        source = """
define cast_same(xs: Number+$n) -> Number+$n => $xs as![Number+$n]
[[1], [2]] cast_same
"""
        self.assertEqual(
            _materialize_lists(execute(source)),
            [[[RuntimeNumber("1")], [RuntimeNumber("2")]]],
        )

    def test_where_output_rank_resolves_in_call_site_checked_cast(self):
        source = """
define make_shape(shape: {Number...}) -> Number+$n
where ($n = length $shape) => [[1]] as![Number+$n]
{1, 1} make_shape
"""
        self.assertEqual(
            _materialize_lists(execute(source)),
            [[[RuntimeNumber("1")]]],
        )

    def test_executes_string_interpolation(self):
        source = """
$name = "Valiance"
"Hello, $name: ${1 + 2}"
"""
        self.assertEqual(execute(source), ["Hello, Valiance: 3"])

    def test_string_interpolation_formats_values(self):
        self.assertEqual(
            execute('"Values: ${[1, 2]}, ${"text"}"'),
            ["Values: [1, 2], text"],
        )

    def test_executes_imported_component_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute("import { math.[add_one] }\n41 add_one", main)

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_executes_component_imported_inside_define(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
define apply(n: Number) -> Number =>
  import { helper.[bump] }
  $n bump
end
41 apply
""",
                main,
            )

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_executes_component_imported_inside_function_literal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
41 (fn (:Number) =>
  import { helper.[bump] }
  bump
end) call
""",
                main,
            )

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_executes_object_imported_inside_define(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
define make(name: String) -> Person =>
  import { person.Person }
  Person($name)
end
"Ada" make
""",
                main,
            )

        self.assertEqual(stack, [ObjectValue("Person", {"name": "Ada"})])

    def test_executes_tag_validator_imported_inside_define(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                """
public tag #checked as computed
public define #checked(value: Number) -> #boolean Number => $value 2 == end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
define check(value: Number) -> #checked Number =>
  import { tags.#checked }
  $value #checked
end
2 check
""",
                main,
            )

        [value] = stack
        self.assertEqual(value.value, RuntimeNumber("2"))
        self.assertEqual({tag.name for tag in value.tags}, {"checked"})

    def test_imported_definition_carries_block_import_runtime_prelude(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            (root / "wrapper.vlnc").write_text(
                """
public define apply(n: Number) -> Number =>
  import { helper.[bump] }
  $n bump
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute("import { wrapper.[apply] }\n41 apply", main)

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_foreach_block_import_is_initialized_outside_loop_body(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = """
[1, 2, 3] foreach (n) =>
  import { helper.[bump] }
  $n bump
end
"""
            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse(source))
            program = compile_program(typed, optimize=False)

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            sum(
                instruction.op is OpCode.MAKE_FUNCTION
                for instruction in program.main.instructions
            ),
            1,
        )
        foreach = next(
            instruction
            for instruction in program.main.instructions
            if instruction.op is OpCode.FOREACH
        )
        body, _indexed, _completion_count = foreach.arg
        self.assertNotIn(
            OpCode.MAKE_FUNCTION,
            tuple(instruction.op for instruction in body.instructions),
        )
        self.assertEqual(run(program), [ObjectValue("None", {})])

    def test_sibling_blocks_can_import_different_elements_under_same_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "small.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            (root / "large.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 10 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
if true =>
  import { small.[bump] }
  1 bump
else => 0
end
if true =>
  import { large.[bump] }
  2 bump
else => 0
end
""",
                main,
            )

        self.assertEqual(stack, [RuntimeNumber("2"), RuntimeNumber("12")])

    def test_executes_imported_namespace_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute("import { math }\n41 math.add_one", main)

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_executes_imported_namespace_object_constructor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  $age: Number
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                'import { person }\nperson.Person("Joe", 67) $.name',
                main,
            )

        self.assertEqual(stack, ["Joe"])

    def test_executes_imported_namespace_explicit_object_constructor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  private $age: Number = 0
  define Person(name: String) => $self.name = $name
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                'import { person }\nperson.Person("Joe") $.name',
                main,
            )

        self.assertEqual(stack, ["Joe"])

    def test_executes_aliased_explicit_object_constructor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  define Person(name: String) => $self.name = $name
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                'import { person.[Person as Human] }\nHuman("Joe") $.name',
                main,
            )

        self.assertEqual(stack, ["Joe"])

    def test_executes_direct_imported_object_friendly_element(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  $age: Number
  define label -> String => $self.name
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                'import { person.Person }\nPerson("Joe", 67) label',
                main,
            )

        self.assertEqual(stack, ["Joe"])

    def test_external_element_overrides_imported_object_friendly_element(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "foo.vlnc").write_text(
                """
public object Foo =>
  $x: Number
  define get => $self.x
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                """
import { foo.Foo }
define get(:Foo) => $.x + 5
Foo(10) get
Foo(10) Foo::get
""",
                main,
            )

        self.assertEqual(stack, [RuntimeNumber("15"), RuntimeNumber("10")])

    def test_executes_direct_imported_trait_impl_friendly_element(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shape.vlnc").write_text(
                """
public trait Shape =>
  extend getArea -> Number
end
""",
                encoding="utf-8",
            )
            (root / "rectangle.vlnc").write_text(
                """
import {shape.Shape}

public object Rectangle =>
  $shortSide: Number
  $longSide: Number
end

object Rectangle as Shape =>
  define getArea => $self.shortSide * $self.longSide
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute(
                "import {rectangle.Rectangle}\n"
                "$myShape = Rectangle(6, 7)\n"
                "getArea $myShape",
                main,
            )

        self.assertEqual(stack, [RuntimeNumber("42")])

    def test_executes_python_backed_standard_library_regex_helpers(self):
        stack = execute("""
import { std.regex }
"a+" "aaa" regex.matches
"[0-9]+" "abc123" regex.first
"[,-]" "a,b-c" regex.split
""")

        self.assertEqual(len(stack), 3)
        self.assertEqual(stack[0], RuntimeNumber("1"))
        self.assertIsInstance(stack[1], ObjectValue)
        self.assertEqual(stack[1].type_name, "Some")
        self.assertEqual(stack[1].fields["value"], "123")
        self.assertEqual(stack[2], ["a", "b", "c"])

    def test_executes_python_backed_standard_library_trig_helpers(self):
        stack = execute("""
import { std.trig }
0 trig.sin
0 trig.cos
trig.pi
""")

        self.assertEqual(stack[0], RuntimeNumber("0.0"))
        self.assertEqual(stack[1], RuntimeNumber("1.0"))
        self.assertGreater(stack[2], RuntimeNumber("3.14"))

    def test_executes_valiance_only_standard_library_module(self):
        stack = execute("""
import { std.arithmetic }
5 arithmetic.square
3 arithmetic.cube
""")

        self.assertEqual(stack, [RuntimeNumber("25"), RuntimeNumber("27")])

    def test_executes_mixed_python_and_valiance_standard_library_module(self):
        stack = execute("""
import { std.text }
"  hi  " text.trim
"  hi  " text.exclaim
""")

        self.assertEqual(stack, ["hi", "hi!"])

    def test_compiler_requires_typed_nodes(self):
        with self.assertRaises(CompileError):
            compile_program(parse("1"), optimize=False)

    def test_executes_variables_and_named_definitions(self):
        self.assertEqual(
            execute("""
define add_one(n: Number) -> Number => $n 1 +
$value = 41
$value add_one
"""),
            [RuntimeNumber("42")],
        )

    def test_executes_explicitly_typed_variables(self):
        self.assertEqual(
            execute("""
$value: Number = 41
$value 1 +
"""),
            [RuntimeNumber("42")],
        )

    def test_recursive_function_code_binds_this_at_runtime(self):
        inner = FunctionCode(
            (
                Instruction(OpCode.LOAD_ELEMENT, "this"),
                Instruction(OpCode.RETURN),
            ),
            recursive=True,
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, inner),
                    Instruction(OpCode.CALL),
                    Instruction(OpCode.RETURN),
                )
            )
        )

        [value] = run(program)

        self.assertIs(value.globals["this"], value)

    def test_tupled_annotation_wraps_element_returns_at_runtime(self):
        self.assertEqual(
            execute("""
define \\pair -> Number, Number => 1 2
@@tupled \\pair
"""),
            [(RuntimeNumber("1"), RuntimeNumber("2"))],
        )

    def test_commutative_annotation_generates_runtime_wrapper(self):
        self.assertEqual(
            execute("""
@commutative define choose(left: Number, right: String) -> String => $right
"ok" 1 choose
"""),
            ["ok"],
        )

    def test_self_annotation_returns_object_friendly_receiver(self):
        stack = execute("""
object Box =>
  $value: Number
  @self define touch => end
end
Box(7)
touch
$.value
""")

        self.assertEqual(stack, [RuntimeNumber("7")])

    def test_nested_function_closure_keeps_captured_outer_value(self):
        self.assertEqual(
            execute("""
define makeMultiplier(factor: Number) =>
  fn (:Number) => * $factor
end

$double = makeMultiplier(2)
$triple = makeMultiplier(3)
$double($triple(4))
"""),
            [RuntimeNumber("24")],
        )

    def test_closure_assignment_does_not_persist_between_calls(self):
        output = io.StringIO()
        source = """
define foo(x: Integer) =>
  fn () =>
    $x := 1 +
    println $x
  end
end

$c = foo(5)
$c()
$c()
"""
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        if analyser.diagnostics:
            raise AssertionError(analyser.diagnostics)

        stack = run(compile_program(typed, optimize=False), output=output.write)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "6\n6\n")

    def test_err_type_annotation_synthesizes_runtime_message_element(self):
        self.assertEqual(
            execute("""
@errType object DivisionByZeroError => end
DivisionByZeroError("division by zero")
message
"""),
            ["division by zero"],
        )

    def test_builtin_error_types_construct_and_expose_messages(self):
        constructors = "\n".join(
            f'{error_type.text}("{error_type.text}")'
            for error_type in BUILTIN_ERROR_TYPES
        )
        values = execute(constructors)

        self.assertEqual(len(values), len(BUILTIN_ERROR_TYPES))
        for error_type, value in zip(BUILTIN_ERROR_TYPES, values, strict=True):
            with self.subTest(error_type=error_type.text):
                self.assertIsInstance(value, ObjectValue)
                self.assertEqual(value.type_name, error_type.text)
                self.assertEqual(value.fields, {"message": error_type.text})

        messages = "\n".join(
            f'{error_type.text}("{error_type.text}") message'
            for error_type in BUILTIN_ERROR_TYPES
        )
        self.assertEqual(
            execute(messages),
            [error_type.text for error_type in BUILTIN_ERROR_TYPES],
        )

    def test_builtin_fault_types_construct_and_expose_messages(self):
        constructible_fault_types = tuple(
            fault_type
            for fault_type in BUILTIN_FAULT_TYPES
            if fault_type.text != "VectorisationFault"
        )
        constructors = "\n".join(
            f'{fault_type.text}("{fault_type.text}")'
            for fault_type in constructible_fault_types
        )
        values = execute(constructors)

        self.assertEqual(len(values), len(constructible_fault_types))
        for fault_type, value in zip(constructible_fault_types, values, strict=True):
            with self.subTest(fault_type=fault_type.text):
                self.assertIsInstance(value, ObjectValue)
                self.assertEqual(value.type_name, fault_type.text)
                self.assertEqual(value.fields, {"message": fault_type.text})

        messages = "\n".join(
            f'{fault_type.text}("{fault_type.text}") message'
            for fault_type in constructible_fault_types
        )
        self.assertEqual(
            execute(messages),
            [fault_type.text for fault_type in constructible_fault_types],
        )
        self.assertEqual(
            execute('IndexFault("bad index") getMessage'),
            ["bad index"],
        )
        self.assertEqual(
            execute('IndexFault("bad index") $.message'),
            ["bad index"],
        )

    def test_user_defined_fault_can_be_panicked_and_handled(self):
        self.assertEqual(
            execute("""
object CustomProblem =>
  $message: String
end
object CustomProblem as Fault => end
try =>
  CustomProblem("boom") panic
handle CustomProblem =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_builtin_value_error_forms_result_in_safe_division(self):
        source = """
define safediv(x: Number, y: Number) =>
  if ($y 0 ==) => ValueError("y cannot be 0")
  else => $x / $y
  end
end
safediv(3, 0)
"""

        [value] = execute(source)

        self.assertIsInstance(value, ObjectValue)
        self.assertEqual(value.type_name, "ValueError")
        self.assertEqual(value.fields["message"], "y cannot be 0")

    def test_executes_object_default_constructor_and_field_access(self):
        self.assertEqual(
            execute("""
object Person =>
  $name: String
  $age: Number
end
Person("Ada", 36) $.name
"""),
            ["Ada"],
        )

    def test_default_constructor_supports_named_members_and_overrides_defaults(self):
        stack = execute("""
object Point =>
  $x: Real = 10
  $y: Real
end
Point(y = 20)
Point(20)
Point(y = 20, x = 30)
""")

        self.assertEqual(
            [value.fields for value in stack],
            [
                {"x": RuntimeNumber("10"), "y": RuntimeNumber("20")},
                {"x": RuntimeNumber("10"), "y": RuntimeNumber("20")},
                {"x": RuntimeNumber("30"), "y": RuntimeNumber("20")},
            ],
        )

    def test_executes_explicit_object_constructor(self):
        self.assertEqual(
            execute("""
object Counter =>
  $value: Number = 0
  private $timesIncremented = 0
  define Counter(initialValue: Number) => $self.value = $initialValue
end
Counter(7) $.value
"""),
            [RuntimeNumber("7")],
        )

    def test_explicit_constructor_accumulates_multiple_field_assignments(self):
        stack = execute("""
object Person =>
  $name: String
  $age: Number
  define Person(name: String, age: Number) =>
    $self.name = $name
    $self.age = $age
  end
end
Person("Ada", 36)
""")

        [person] = stack
        self.assertIsInstance(person, ObjectValue)
        self.assertEqual(person.fields, {"name": "Ada", "age": RuntimeNumber("36")})

    def test_self_methods_rebind_augmented_field_assignments(self):
        output = io.StringIO()
        source = """
object Counter =>
  $value: Integer
  private $timesIncremented = 0

  define Counter(initialValue: Integer) => $self.value = $initialValue

  @self define increment =>
    $self.value := + 1
    $self.timesIncremented := + 1
  end

  @self define +(:Integer) =>
    $self.value := +
    $self.timesIncremented := + 1
  end

  define incCount => $self.timesIncremented
end

Counter(0)
increment increment increment
+ 5
dup | $.value | println
incCount | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "8\n4\n")


    def test_account_methods_keep_receiver_out_of_body_stack_and_runtime_cycle(self):
        output = io.StringIO()
        source = """
object Account =>
  $balance: Real = 0
  $name: String
  @self define deposit(:Real) => $self.balance := +
  @self define withdraw(:Real) =>
    if (> $self.balance) => panic ValueFault("Insufficient funds")
    else => $self.balance := -
  end
  define show(title: String) =>
    println("$title ${$self.name} : ${$self.balance}")
  end
end

$a = Account("Demo")
$a show("After Creation")
$a := deposit 1000.00
$a show("After deposit")
$a := withdraw 100.00
$a show("After withdraw")
"""
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertFalse(analyser.diagnostics)

        bytecode = loads(dumps(compile_program(typed)))
        stack = run(bytecode, output=output.write)

        self.assertEqual(stack, [])
        self.assertEqual(
            output.getvalue(),
            "After Creation Demo : 0\n"
            "After deposit Demo : 1000\n"
            "After withdraw Demo : -900\n",
        )

    def test_self_receiver_is_not_used_as_an_implicit_operand(self):
        source = """
object Account =>
  $balance: Real = 0
  $name: String

  @self define deposit(:Real) => $self.balance := +
  @self define withdraw(v: Real) =>
    if (> $self.balance) =>
      panic ValueFault("Insufficient funds")
    else =>
      $self.balance := - $v
    end
  end

end

$a = Account("Demo")
$a := deposit 1000.00
$a := withdraw 100.00
$a | $.balance
"""

        stack = execute(source)

        self.assertEqual(stack, [RuntimeNumber("900")])

    def test_parameter_cycle_runs_right_to_left_and_wraps(self):
        source = """
define showCycle(a: String, b: String, c: String) -> =>
  println
  println
  println
  println
end
showCycle("a", "b", "c")
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                stack = run(program)
            self.assertEqual(stack, [])
            self.assertEqual(output.getvalue(), "c\nb\na\nc\n")

    def test_inferred_function_returns_only_the_analysed_top_value(self):
        source = """
$f = fn (a: Number, b: Number, c: Number) => dip: -
$f(10, 20, 30)
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            self.assertEqual(run(program), [RuntimeNumber("30")])

    def test_inferred_empty_function_returns_no_values(self):
        source = """
$f = fn () => println "done" end
$f()
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            self.assertEqual(run(program), [])

    def test_return_all_preserves_the_analysed_multiple_return_count(self):
        source = """
@returnAll define pair(a: Number, b: Number) => $a $b
pair(10, 20)
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            self.assertEqual(
                run(program),
                [RuntimeNumber("10"), RuntimeNumber("20")],
            )

    def test_explicit_return_arguments_return_multiple_values(self):
        source = """
$f = fn (a: Number, b: Number, c: Number) -> Number, Number =>
  return ($a, $b)
end
$f(10, 20, 30)
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            self.assertEqual(
                run(program),
                [RuntimeNumber("10"), RuntimeNumber("20")],
            )

    def test_equal_explicit_return_branch_multiplicity_executes(self):
        source = """
$f = fn (a: String, b: Integer) -> String, String =>
  if (0 == 0) => return ($a, $a)
  else => return ("unused", "unused")
end
$f("yes", 1)
"""
        self.assertEqual(execute(source), ["yes", "yes"])

    def test_return_all_allows_inferred_multiple_values(self):
        source = """
$f = @returnAll fn (a: Number, b: Number) =>
  return ($a, $b)
end
$f(10, 20)
"""
        self.assertEqual(
            execute(source),
            [RuntimeNumber("10"), RuntimeNumber("20")],
        )

    def test_return_chain_selects_its_top_value(self):
        source = """
$f = fn (a: Number, b: Number) =>
  return $a $b +
end
$f(10, 20)
"""
        self.assertEqual(execute(source), [RuntimeNumber("30")])

    def test_explicit_empty_and_bare_return_follow_distinct_policies(self):
        self.assertEqual(
            execute("$f = fn () => 10 return () end\n$f()"),
            [],
        )
        self.assertEqual(
            execute("$f = fn (a: Number, b: Number) => $a $b return end\n$f(10, 20)"),
            [RuntimeNumber("20")],
        )

    def test_return_values_propagate_out_of_foreach_body(self):
        source = """
$f = fn (a: Number, b: Number) -> Number, Number =>
  [1] foreach (item) =>
    return ($a, $b)
  end
end
$f(10, 20)
"""
        self.assertEqual(
            execute(source),
            [RuntimeNumber("10"), RuntimeNumber("20")],
        )

    def test_dip_sources_cycled_parameters_beneath_held_value(self):
        source = """
@returnAll define subtractUnder(
  a: Number,
  b: Number,
  c: Number
) =>
  dip: -
end
subtractUnder(10, 3, 99)
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        for program in (
            compile_program(typed, optimize=False),
            compile_program(typed),
            loads(dumps(compile_program(typed))),
        ):
            self.assertEqual(
                run(program),
                [RuntimeNumber("7"), RuntimeNumber("99")],
            )

    def test_parameter_cycle_starts_at_top_of_conceptual_input_stack(self):
        self.assertEqual(
            execute(
                "define addSecond(first: Integer, second: Integer) "
                "-> Integer => + 1\n"
                "addSecond(10, 20)"
            ),
            [RuntimeNumber("21")],
        )

    def test_explicit_constructor_preserves_generic_type_arguments(self):
        [box] = execute("""
object[T] Box =>
  $value: T
  define Box(value: T) => $self.value = $value
end
Box(1)
""")

        self.assertIsInstance(box, ObjectValue)
        self.assertEqual(box.fields, {"value": RuntimeNumber("1")})
        self.assertEqual(box.type_args, ("Integer",))

    def test_overloaded_explicit_constructor_uses_resolved_initializer(self):
        self.assertEqual(
            execute("""
object Value =>
  $number: Number = 0
  $text: String = ""
  define Value(value: Number) => $self.number = $value
  define Value(value: String) => $self.text = $value
end
Value(1) $.number
Value("x") $.text
"""),
            [RuntimeNumber("1"), "x"],
        )

    def test_external_element_overrides_object_friendly_element(self):
        output = io.StringIO()
        source = """
object Foo =>
  $x: Number
  define get => $self.x
end

define get(:Foo) => $.x + 5

Foo(10) | get | println
Foo(10) | Foo::get | println
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "15\n10\n")

    def test_named_external_element_overrides_object_friendly_element(self):
        self.assertEqual(
            execute("""
object Foo =>
  $x: Number
  define get => $self.x
end

define get(f: Foo) => $f.x + 5

Foo(10) get
Foo(10) Foo::get
"""),
            [RuntimeNumber("15"), RuntimeNumber("10")],
        )

    def test_executes_row_inferred_element_on_nominal_object(self):
        output = io.StringIO()
        source = """
object Person =>
  $name: String
  $age: Number
end

define getName -> String => $.name

$joe = Person("Joe", 67)
println getName $joe
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Joe\n")

    def test_field_access_cycles_explicit_named_parameter(self):
        output = io.StringIO()
        source = """
object Person =>
  $name: String
  $age: Number
end

define getName(person) -> String => $.name

$joe = Person("Joe", 67)
println getName $joe
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Joe\n")

    def test_field_access_cycles_explicit_nominal_parameter(self):
        output = io.StringIO()
        source = """
object Person =>
  $name: String
  $age: Number
end

define getName(person: Person) -> String => $.name

$joe = Person("Joe", 67)
println getName $joe
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Joe\n")

    def test_executes_object_field_access_over_lists(self):
        self.assertEqual(
            execute("""
object Person =>
  $name: String
end
[Person("Ada"), Person("Grace")] $.name
"""),
            [["Ada", "Grace"]],
        )

    def test_executes_public_object_field_write_as_reconstruction(self):
        self.assertEqual(
            execute("""
object Person =>
  public $name: String
end
Person("Ada")
$.name = "Grace"
$.name
"""),
            ["Grace"],
        )

    def test_object_fields_destroy_in_reverse_declaration_order(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            execute("""
object Tracer =>
  $name: String
  define ~Tracer => $self.name println
end

object Owner =>
  $first: Tracer
  $second: Tracer
  $third: Tracer
end

define \\releaseOwner -> =>
  Owner(Tracer("first"), Tracer("second"), Tracer("third"))
end
\\releaseOwner
""")

        self.assertEqual(output.getvalue(), "third\nsecond\nfirst\n")

    def test_reverse_field_destruction_survives_bytecode_round_trip(self):
        output = io.StringIO()
        source = """
object Tracer =>
  $name: String
  define ~Tracer => $self.name println
end

object Owner =>
  $first: Tracer
  $second: Tracer
  $third: Tracer
end

define \\releaseOwner -> =>
  Owner(Tracer("first"), Tracer("second"), Tracer("third"))
end
\\releaseOwner
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        restored = loads(dumps(program))
        with contextlib.redirect_stdout(output):
            run(restored)

        self.assertEqual(output.getvalue(), "third\nsecond\nfirst\n")

    def test_object_destructor_runs_when_last_reference_leaves_scope(self):
        output = io.StringIO()
        source = """
object Temp =>
  $name: String
  define ~Temp => $self.name println
end

define \\makeTemp =>
  $value = Temp("released")
end

\\makeTemp
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "released\n")

    def test_destructor_panic_is_catchable_after_structural_teardown(self):
        output = io.StringIO()
        source = """
object Child =>
  define ~Child => "child released" println
end

object Parent =>
  private $child: Child
  define ~Parent => RuntimeFault("parent cleanup failed") panic
end

define \\makeParent =>
  $parent = Parent(Child)
end

try =>
  \\makeParent
handle RuntimeFault =>
  "caught" println
end
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "child released\ncaught\n")

    def test_destructor_receiver_is_a_non_owning_borrow(self):
        value = ObjectValue("Resource", {})
        value.refcount = 0
        value.cleaning_up = True

        borrowed = _borrow_destructor_receiver(value)

        self.assertIs(borrowed, value)
        self.assertEqual(value.refcount, 0)
        self.assertTrue(value.destructor_borrowed)
        with self.assertRaises(RuntimeError) as caught:
            _retain_value(value)
        self.assertIn(
            "cannot retain borrowed destructor receiver Resource",
            str(caught.exception),
        )
        _release_value(value, VirtualMachine())
        self.assertEqual(value.refcount, 0)

    def test_release_integrity_rejects_underflow_and_post_destruction(self):
        guarded_runtime = ObjectRuntimeType(destructor_name="Underflow::~Underflow")
        underflow = ObjectValue("Underflow", {}, runtime_type=guarded_runtime)
        underflow.refcount = 0
        with self.assertRaises(RuntimeError) as caught:
            _release_value(underflow, VirtualMachine())
        self.assertIn("reference count underflow for Underflow", str(caught.exception))

        destroyed_runtime = ObjectRuntimeType(destructor_name="Destroyed::~Destroyed")
        destroyed = ObjectValue("Destroyed", {}, runtime_type=destroyed_runtime)
        destroyed.destroyed = True
        destroyed.refcount = 0
        with self.assertRaises(RuntimeError) as caught:
            _release_value(destroyed, VirtualMachine())
        self.assertIn("release after destruction of Destroyed", str(caught.exception))

    def test_destroyed_object_cannot_be_resurrected_by_retained_occurrence(self):
        value = ObjectValue("Resource", {})
        value.refcount = 0
        _run_object_cleanup(value, VirtualMachine())

        self.assertTrue(value.destroyed)
        self.assertEqual(value.refcount, 0)
        with self.assertRaises(RuntimeError) as caught:
            _retain_value(value)
        self.assertIn("use after destruction of Resource", str(caught.exception))

    def test_reconstructed_object_wrappers_share_mustcall_state(self):
        runtime_type = ObjectRuntimeType(
            mustcall_mode="any",
            mustcall_methods=("finish",),
        )
        original = ObjectValue(
            "Work",
            {"value": RuntimeNumber("1")},
            runtime_type=runtime_type,
        )

        reconstructed = _set_field(
            original,
            "value",
            RuntimeNumber("2"),
        )

        self.assertIsInstance(reconstructed, ObjectValue)
        self.assertIs(original.lifecycle_state, reconstructed.lifecycle_state)
        reconstructed.mustcall_called = frozenset({"finish"})
        self.assertEqual(original.mustcall_called, frozenset({"finish"}))

    def test_mustcall_result_adopts_receiver_lifecycle_state(self):
        runtime_type = ObjectRuntimeType(
            mustcall_mode="any",
            mustcall_methods=("finish",),
        )
        receiver = ObjectValue("Work", {}, runtime_type=runtime_type)
        independently_built_result = ObjectValue("Work", {}, runtime_type=runtime_type)
        callee = FunctionValue(
            FunctionCode(
                instructions=(),
                params=("self",),
                name="Work::finish",
            ),
            {},
        )

        _mark_mustcall_method(
            (receiver,),
            [independently_built_result],
            callee,
        )

        self.assertIs(
            independently_built_result.lifecycle_state,
            receiver.lifecycle_state,
        )
        self.assertEqual(receiver.mustcall_called, frozenset({"finish"}))
        self.assertEqual(
            independently_built_result.mustcall_called,
            frozenset({"finish"}),
        )

    def test_mustcall_all_requires_every_method(self):
        source = """
@mustcall(all = ["flush", "close"])
object Stream =>
  define flush => end
  define close => end
  define ~Stream => end
end

define \\partial =>
  $stream = Stream
  $stream flush
end

\\partial
"""

        with self.assertRaises(RuntimeError) as caught:
            execute_with_static_mustcall_backstop(source)

        self.assertIn("uncaught panic: MustCallFault", str(caught.exception))
        self.assertIn("Stream requires all of: flush, close", str(caught.exception))

    def test_panicking_required_method_does_not_satisfy_mustcall(self):
        source = """
@mustcall(any = ["finish"])
object Work =>
  define finish => RuntimeFault("finish failed") panic
  define ~Work => end
end

define \\attempt -> =>
  $work = Work
  $work finish
end

\\attempt
"""

        with self.assertRaises(RuntimeError) as caught:
            execute(source)

        self.assertIn("uncaught panic: RuntimeFault", str(caught.exception))
        self.assertIn("finish failed", str(caught.exception))
        secondary = tuple(getattr(caught.exception, "secondary_faults", ()))
        self.assertEqual(len(secondary), 1)
        self.assertEqual(secondary[0].value.type_name, "MustCallFault")

    def test_normally_returned_error_value_satisfies_mustcall(self):
        self.assertEqual(
            execute("""
@mustcall(any = ["finish"])
object Work =>
  define finish => ValueError("operation failed")
  define ~Work => end
end

define \\attempt =>
  $work = Work
  $result = $work finish
end

\\attempt
"""),
            [],
        )

    def test_destructor_call_does_not_satisfy_mustcall_contract(self):
        output = io.StringIO()
        source = """
@mustcall(any = ["finish"])
object Work =>
  define finish => "finish called" println
  define ~Work => $self finish
end

define \\abandon =>
  $work = Work
end

\\abandon
"""

        with (
            self.assertRaises(RuntimeError) as caught,
            contextlib.redirect_stdout(output),
        ):
            execute_with_static_mustcall_backstop(source)

        self.assertEqual(output.getvalue(), "finish called\n")
        self.assertIn("uncaught panic: MustCallFault", str(caught.exception))
        self.assertIn("Work requires one of: finish", str(caught.exception))

    def test_mustcall_violation_is_secondary_during_panic_unwind(self):
        source = """
@mustcall(any = ["finish"])
object Work =>
  define finish => end
  define ~Work => end
end

define \\fail =>
  $work = Work
  RuntimeFault("body failed") panic
end

\\fail
"""

        with self.assertRaises(RuntimeError) as caught:
            execute_with_static_mustcall_backstop(source)

        self.assertIn("uncaught panic: RuntimeFault", str(caught.exception))
        self.assertIn("body failed", str(caught.exception))
        secondary = tuple(getattr(caught.exception, "secondary_faults", ()))
        self.assertEqual(len(secondary), 1)
        self.assertEqual(secondary[0].value.type_name, "MustCallFault")
        self.assertEqual(
            secondary[0].value.fields["message"],
            "Work requires one of: finish",
        )

    def test_destructor_panic_during_unwind_aborts_with_both_panics(self):
        source = """
object Bomb =>
  define ~Bomb => IOFault("cleanup exploded") panic
end

define \\fail =>
  $bomb = Bomb
  RuntimeFault("body exploded") panic
end

\\fail
"""

        with self.assertRaises(RuntimeError) as caught:
            execute(source)

        message = str(caught.exception)
        self.assertIn("fatal: destructor panicked during panic unwinding", message)
        self.assertIn("original panic: RuntimeFault", message)
        self.assertIn("body exploded", message)
        self.assertIn("destructor panic: IOFault", message)
        self.assertIn("cleanup exploded", message)
        self.assertIn("while destroying: Bomb", message)

    def test_second_destructor_panic_in_teardown_cascade_aborts(self):
        source = """
object InnerBomb =>
  define ~InnerBomb => IOFault("inner cleanup exploded") panic
end

object OuterBomb =>
  private $inner: InnerBomb
  define ~OuterBomb => RuntimeFault("outer cleanup exploded") panic
end

define \\release =>
  $bomb = OuterBomb(InnerBomb)
end

\\release
"""

        with self.assertRaises(RuntimeError) as caught:
            execute(source)

        message = str(caught.exception)
        self.assertIn("fatal: destructor panicked during panic unwinding", message)
        self.assertIn("original panic: RuntimeFault", message)
        self.assertIn("outer cleanup exploded", message)
        self.assertIn("destructor panic: IOFault", message)
        self.assertIn("inner cleanup exploded", message)
        self.assertIn("while destroying: OuterBomb", message)

    def test_secondary_mustcall_fault_is_rendered_with_cleanup_context(self):
        source = """
@mustcall(any = ["finish"])
object Work =>
  define finish => end
  define ~Work => RuntimeFault("cleanup failed") panic
end

define \\makeWork -> => Work
\\makeWork
"""
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertEqual(analyser.diagnostics, [])
        with self.assertRaises(RuntimeError) as caught:
            run(compile_program(typed, optimize=False))

        message = str(caught.exception)
        self.assertIn("uncaught panic: RuntimeFault", message)
        self.assertIn("while cleaning up:", message)
        self.assertIn("destroying Work", message)
        self.assertIn("secondary lifecycle diagnostic 1:", message)
        self.assertIn("MustCallFault:", message)

    def test_nested_field_cleanup_context_is_rendered(self):
        source = """
object Inner =>
  define ~Inner => RuntimeFault("inner cleanup failed") panic
end

object Outer =>
  $inner: Inner
end

define \\makeInner -> Inner => Inner

define \\makeOuter -> => \\makeInner Outer
\\makeOuter
"""
        with self.assertRaises(RuntimeError) as caught:
            execute(source)

        message = str(caught.exception)
        self.assertIn("uncaught panic: RuntimeFault", message)
        self.assertIn("while cleaning up:", message)
        self.assertIn("destroying Inner", message)
        self.assertIn("destroying Outer", message)

    def test_destructor_panic_remains_primary_over_mustcall_fault(self):
        source = """
@mustcall(any = ["finish"])
object Work =>
  define finish => end
  define ~Work => RuntimeFault("destructor failed") panic
end

define \\makeWork =>
  $work = Work
end

\\makeWork
"""

        with self.assertRaises(RuntimeError) as caught:
            execute_with_static_mustcall_backstop(source)

        self.assertIn("uncaught panic: RuntimeFault", str(caught.exception))
        self.assertIn("destructor failed", str(caught.exception))
        self.assertNotIn("uncaught panic: MustCallFault", str(caught.exception))

    def test_object_friendly_pop_is_not_a_lifecycle_hook(self):
        output = io.StringIO()
        source = """
object Temp =>
  define pop => "custom pop" println
  define ~Temp => "destructor" println
end

define \\makeTemp =>
  $value = Temp
end

\\makeTemp
"""

        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "destructor\n")

    def test_unduplicatable_object_raises_duplication_fault(self):
        with self.assertRaises(RuntimeError) as caught:
            execute("""
object WriteFile =>
  @error("Writeable files cannot be duplicated")
  define dup => end
end

$file = WriteFile
$file
$file
""")

        self.assertIn("uncaught panic: DuplicationFault", str(caught.exception))
        self.assertIn("Writeable files cannot be duplicated", str(caught.exception))

    def test_mustcall_fault_still_runs_destructor(self):
        output = io.StringIO()
        source = """
@mustcall(any = ["commit"])
object Tx =>
  define commit => $self
  define ~Tx => "released" println
end

define \\leak =>
  $tx = Tx
end

\\leak
"""

        with (
            self.assertRaises(RuntimeError) as caught,
            contextlib.redirect_stdout(output),
        ):
            execute_with_static_mustcall_backstop(source)

        self.assertIn("uncaught panic: MustCallFault", str(caught.exception))
        self.assertEqual(output.getvalue(), "released\n")

    def test_generic_object_runtime_values_keep_type_arguments(self):
        stack = execute("""
object[T] Box =>
  public $value: T
end
1
Box
$.value = 2
""")

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], ObjectValue)
        self.assertEqual(stack[0].type_name, "Box")
        self.assertEqual(stack[0].type_args, ("Integer",))
        self.assertEqual(stack[0].fields["value"], RuntimeNumber("2"))

    def test_nested_generic_constructor_inference_executes(self):
        source = """
object[T] Box => $v: T
define[T, U] Map(
  b: Box[T],
  f: Function[T -> U]
) -> Box[U] => Box($f($b.v))

$b = Box[Integer](21)
$doubled = $b Map: * 2
$label = $doubled Map: "value = ${top}"
println $label.v
"""

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute(source)

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "value = 42\n")

    def test_generic_object_type_arguments_survive_bytecode_round_trip(self):
        source = """
object[T] Box =>
  $value: T
end
1
Box
"""
        program = compile_program(Analyser().analyse(parse(source)), optimize=False)
        stack = run(loads(dumps(program)))

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], ObjectValue)
        self.assertEqual(stack[0].type_args, ("Integer",))

    def test_explicit_object_constructor_survives_bytecode_round_trip(self):
        source = """
object Person =>
  $name: String
  $age: Number = 0
  define Person(name: String) => $self.name = $name
end
Person("Ada")
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        [person] = run(loads(dumps(compile_program(typed, optimize=False))))

        self.assertIsInstance(person, ObjectValue)
        self.assertEqual(person.fields, {"name": "Ada", "age": RuntimeNumber("0")})

    def test_function_element_tags_survive_bytecode_round_trip(self):
        source = "eager define log(value: Number) -> => $value println"
        program = compile_program(Analyser().analyse(parse(source)), optimize=False)
        restored = loads(dumps(program))
        maker = restored.main.instructions[0]

        self.assertEqual(maker.op, OpCode.MAKE_FUNCTION)
        self.assertIn("Eager", maker.arg.element_tags)

    def test_executes_enum_member_value_access(self):
        self.assertEqual(
            execute("""
enum[String] TokenType =>
  NUMBER = "Number"
end
TokenType.NUMBER.value
"""),
            ["Number"],
        )

    def test_executes_match_on_enum_and_variant_members(self):
        self.assertEqual(
            execute("""
enum Colour => RED GREEN end
Colour.GREEN
match =>
  as :RED => "red"
  as :GREEN => "green"
end
"""),
            ["green"],
        )
        self.assertEqual(
            execute("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(1)
match =>
  as :Some => "some"
  as :None => 0
end
"""),
            ["some"],
        )

    def test_generic_variant_runtime_values_keep_type_arguments(self):
        stack = execute("""
variant[T] Maybe =>
  Some => $value: T end
  None => end
end
1
Some
""")

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], ObjectValue)
        self.assertEqual(stack[0].type_name, "Maybe.Some")
        self.assertEqual(stack[0].type_args, ("Integer",))
        self.assertEqual(stack[0].fields["value"], RuntimeNumber("1"))

    def test_executes_match_literal_guard_and_wildcard_patterns(self):
        self.assertEqual(
            execute("""
10
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""),
            ["The number was 10"],
        )
        self.assertEqual(
            execute("""
7
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""),
            ["The number is bigger than 5"],
        )
        self.assertEqual(
            execute("""
2
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""),
            ["Too small"],
        )

    def test_executes_match_list_patterns_with_bindings_and_rests(self):
        self.assertEqual(
            execute("""
[1, 99, 3]
match =>
  [1, _, 3] => "shape"
  _ => "no"
end
"""),
            ["shape"],
        )
        self.assertEqual(
            execute("""
[1, 99, 3]
match =>
  [1, $x = _, 3] => "3 items, the middle is ${$x}"
  _ => "no"
end
"""),
            ["3 items, the middle is 99"],
        )
        self.assertEqual(
            execute("""
[1, 2, 3, 4, 6]
match =>
  [1, ..., 3, $y = ..., 6] => "Captured ${$y length} item"
  _ => "no"
end
"""),
            ["Captured 1 item"],
        )

    def test_match_branch_isolates_outer_stack_and_preserves_all_results(self):
        self.assertEqual(
            execute("""
10
1 match =>
  1 => 20 30
  _ => 40
end
2 match =>
  2 => 50 60
  _ => 70
end
"""),
            [
                RuntimeNumber("10"),
                RuntimeNumber("20"),
                RuntimeNumber("30"),
                RuntimeNumber("50"),
                RuntimeNumber("60"),
            ],
        )

    def test_guard_match_can_consume_two_inputs_like_two_value_patterns(self):
        self.assertEqual(
            execute("""
2 3
match =>
  if + | == 5 => "sum"
  10, 20 => "literal"
  _, _ => "other"
end
"""),
            ["sum"],
        )

    def test_value_match_body_cannot_pop_below_matched_subjects(self):
        self.assertEqual(
            execute("""
99 10
match =>
  10 => +
  _ => 0
end
"""),
            [RuntimeNumber("99"), RuntimeNumber("20")],
        )

    def test_recursive_rugged_fold_with_implicit_modifier_and_input_cycling(self):
        self.assertEqual(
            execute("""
define[T] flatten(:T~) -> T+ =>
  fold([] as[T+]): addAll(match =>
    as :T~ => flatten
    as :T => ^+
  )
end
[1, [2, [3]], 4] flatten
"""),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                ]
            ],
        )

    def test_match_branch_cycles_only_over_retained_coordinates(self):
        self.assertEqual(
            execute("""
99
2 1 match =>
  1, 2 => +
  _, _ => 0
end
"""),
            [RuntimeNumber("99"), RuntimeNumber("3")],
        )

    def test_executes_match_type_guards_destructure_and_stack_patterns(self):
        self.assertEqual(
            execute("""
6
match =>
  as :Number if > 5 => "Type match with guard"
  as y => "Default named type match: ${$y}"
end
"""),
            ["Type match with guard"],
        )
        self.assertEqual(
            execute("""
object Pair =>
  $left: Number
  $right: Number
end
Pair(5, 5)
match =>
  as :Pair(param, param) => "Destructured object with ${$param}"
  _ => "no"
end
"""),
            ["Destructured object with 5"],
        )
        self.assertEqual(
            execute("""
2 1
match =>
  1, 2 => "Top of stack was 1 and then 2"
  _, _ => "catch-all case"
end
"""),
            ["Top of stack was 1 and then 2"],
        )
        self.assertEqual(
            execute("""
[1, 2, 3] 3
match =>
  if > 10 || if < 4, [1, 2, 3] => "mixed"
  _, _ => "default"
end
"""),
            ["mixed"],
        )
        self.assertEqual(
            execute("""
define classify =>
  match =>
    1, "x" => "hit"
    _, _ => "miss"
  end
end
classify("x", 1)
"""),
            ["hit"],
        )

    def test_fizzbuzz_match_maps_inferred_and_explicit_functions(self):
        source = """
range(1, 15) map {function}
  match =>
    if % 15 == 0 => "FizzBuzz"
    if %  5 == 0 => "Buzz"
    if %  3 == 0 => "Fizz"
         _ => "${{top}}"
  end
end

println
"""
        expected = (
            "[1, 2, Fizz, 4, Buzz, Fizz, 7, 8, Fizz, Buzz, 11, Fizz, 13, 14, "
            "FizzBuzz]\n"
        )
        for function in ("fn =>", "fn (n: Integer) =>"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(execute(source.format(function=function)), [])
            self.assertEqual(output.getvalue(), expected)

    def test_executes_list_tuple_record_and_dict_literals(self):
        self.assertEqual(execute("[1, 2, 3] length"), [RuntimeNumber("3")])
        self.assertEqual(execute('{1, "two"}'), [(RuntimeNumber("1"), "two")])
        self.assertEqual(execute("record{x => 5}.x"), [RuntimeNumber("5")])
        self.assertEqual(execute('dict{"x" => 7}'), [{"x": RuntimeNumber("7")}])

    def test_dict_and_record_print_with_fat_arrows(self):
        dictionary = DictValue({"label": RuntimeNumber("1")})
        record = RecordValue({"label": RuntimeNumber("1")})

        self.assertEqual(format_runtime_value(dictionary), '{"label" => 1}')
        self.assertEqual(format_runtime_value(record), "record{label => 1}")

    def test_record_and_dict_expressions_cannot_read_outer_stack(self):
        for source in (
            "3 5 record{foo => +}",
            "3 5 dict{top => top}",
            '3 5 dict{"key" => +}',
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics)

    def test_dictionary_key_and_value_are_isolated_from_each_other(self):
        analyser = Analyser()
        analyser.analyse(parse("dict{3 => top}"))
        self.assertTrue(analyser.diagnostics)
        self.assertEqual(
            execute("dict{3 => 4 5 +}"), [{RuntimeNumber("3"): RuntimeNumber("9")}]
        )

    def test_record_and_dict_preserve_the_outer_stack(self):
        self.assertEqual(
            execute("3 5 record{foo => 7}"),
            [RuntimeNumber("3"), RuntimeNumber("5"), {"foo": RuntimeNumber("7")}],
        )
        self.assertEqual(
            execute('3 5 dict{"key" => 7}'),
            [RuntimeNumber("3"), RuntimeNumber("5"), {"key": RuntimeNumber("7")}],
        )

    def test_dictionary_keys_support_numbers_and_tuples(self):
        self.assertEqual(execute("dict{3 => 5} $[3]"), [RuntimeNumber("5")])
        self.assertEqual(execute("dict{{1, 2} => 7} $[{1, 2}]"), [RuntimeNumber("7")])

    def test_executes_conditionals_and_loops(self):
        self.assertEqual(execute("if (true) => 2 else => 3 end"), [RuntimeNumber("2")])
        self.assertEqual(
            execute("""
$n = 3
while ($n 0 >) =>
  $n = $n 1 -
end
$n
"""),
            [RuntimeNumber("0")],
        )

    def test_implicit_while_cycles_condition_input_through_body(self):
        output = []
        source = """0 while (< 10) =>
  println("Count is ${top}")
  + 1
end"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed)
        result = VirtualMachine(output=output.append).run(program)
        self.assertEqual(result, [RuntimeNumber("10")])
        self.assertEqual(output, [f"Count is {index}\n" for index in range(10)])

    def test_runtime_loop_forms_cycle_explicit_inputs(self):
        self.assertEqual(
            run(
                Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, RuntimeNumber("2")),
                            Instruction(OpCode.CYCLE_BEGIN, (None, 0)),
                            Instruction(
                                OpCode.CALL_RESOLVED_ELEMENT,
                                ResolvedElementReference("+", 1),
                            ),
                            Instruction(OpCode.CYCLE_END),
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )
            ),
            [RuntimeNumber("4")],
        )
        self.assertEqual(
            execute("""
define first_generated(n: Number) -> Number =>
  unfold (< 3) -> (x: Number) => 1 + end | #-infinite | first
end
1 first_generated
"""),
            [RuntimeNumber("1")],
        )
        self.assertEqual(
            execute("[1, 2] foreach (n) => if ($n 10 >) => break ($n) end end"),
            [ObjectValue("None", {})],
        )

    def test_at_vectorises_to_explicit_stop_ranks(self):
        implicit = """
[[1, 2], [3, 4]]
[5, 6]
at (list+, item) => append
"""
        explicit = """
[[1, 2], [3, 4]]
[5, 6]
at (list+, item) => $list append $item
"""
        expected = [
            [
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("5"),
            ],
            [
                RuntimeNumber("3"),
                RuntimeNumber("4"),
                RuntimeNumber("6"),
            ],
        ]

        self.assertEqual(execute(implicit), [expected])
        self.assertEqual(execute(explicit), [expected])

    def test_at_broadcasts_scalar_levels_and_survives_bytecode_round_trip(self):
        source = """
[[1, 2], [3, 4]]
5
at (list+, item) => append
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        bytecode = loads(dumps(compile_program(typed, optimize=False)))

        self.assertEqual(
            run(bytecode),
            [
                [
                    [
                        RuntimeNumber("1"),
                        RuntimeNumber("2"),
                        RuntimeNumber("5"),
                    ],
                    [
                        RuntimeNumber("3"),
                        RuntimeNumber("4"),
                        RuntimeNumber("5"),
                    ],
                ]
            ],
        )

    def test_foreach_and_while_break_return_values(self):
        self.assertEqual(
            execute("""
[1, 2, 3] foreach (n) =>
  if ($n 2 ==) =>
    break ($n, $n double)
  end
end
"""),
            [RuntimeNumber("2"), RuntimeNumber("4")],
        )
        self.assertEqual(
            execute("""
[1] foreach (n) =>
  if (false) =>
    break ($n, $n)
  end
end
"""),
            [ObjectValue("None", {}), ObjectValue("None", {})],
        )
        self.assertEqual(
            execute("""
$n = 0
while ($n < 10) =>
  if ($n 3 ==) =>
    break ($n)
  else =>
    $n := + 1
  end
end
"""),
            [RuntimeNumber("3")],
        )

    def test_named_recursion_inside_explicit_arguments_executes(self):
        self.assertEqual(
            execute(
                """
                define countdown(n: Integer) -> Integer =>
                  if ($n == 0) => 0
                  else => +(countdown($n - 1), 1)
                  end
                end
                countdown 5
                """
            ),
            [RuntimeNumber("5")],
        )

    def test_typed_recursive_definitions_call_themselves_at_runtime(self):
        self.assertEqual(
            run(
                Program(
                    FunctionCode(
                        (
                            Instruction(
                                OpCode.MAKE_FUNCTION,
                                FunctionCode(
                                    (
                                        Instruction(OpCode.LOAD_VAR, "n"),
                                        Instruction(
                                            OpCode.PUSH_CONST, RuntimeNumber("0")
                                        ),
                                        Instruction(
                                            OpCode.CALL_RESOLVED_ELEMENT,
                                            ResolvedElementReference(">", 0),
                                        ),
                                        Instruction(OpCode.JUMP_IF_FALSE, 11),
                                        Instruction(OpCode.LOAD_VAR, "n"),
                                        Instruction(
                                            OpCode.PUSH_CONST, RuntimeNumber("1")
                                        ),
                                        Instruction(OpCode.LOAD_ELEMENT, "-"),
                                        Instruction(OpCode.CALL),
                                        Instruction(OpCode.LOAD_ELEMENT, "countdown"),
                                        Instruction(OpCode.CALL),
                                        Instruction(OpCode.JUMP, 12),
                                        Instruction(
                                            OpCode.PUSH_CONST, RuntimeNumber("0")
                                        ),
                                        Instruction(OpCode.RETURN),
                                    ),
                                    params=("n",),
                                    name="countdown",
                                    cycle_params=True,
                                ),
                            ),
                            Instruction(OpCode.STORE_VAR, "countdown"),
                            Instruction(OpCode.PUSH_CONST, RuntimeNumber("3")),
                            Instruction(
                                OpCode.CALL_RESOLVED_ELEMENT,
                                ResolvedElementReference("countdown", 0),
                            ),
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )
            ),
            [RuntimeNumber("0")],
        )

    def test_executes_assert_and_unfold(self):
        self.assertEqual(execute("assert => true end 5"), [RuntimeNumber("5")])
        stack = execute(
            "1 unfold (< 4) -> (n: Number) => $n 1 + end | #-infinite | first"
        )
        self.assertEqual(stack, [RuntimeNumber("1")])

    def test_unfold_cycles_state_and_supports_separate_emission(self):
        explicit = execute("""
0 1 unfold (true) -> (prev: Integer, next: Integer) =>
  +
end | #-infinite | 7 take
""")
        explicit_prefix = list(explicit[0])
        self.assertEqual(
            explicit_prefix,
            [
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
                RuntimeNumber("8"),
                RuntimeNumber("13"),
            ],
        )

        inferred = execute("""
0 1 unfold =>
  +
end | #-infinite | 7 take
""")
        self.assertEqual(list(inferred[0]), explicit_prefix)

        tagged = execute("""
0 1 unfold =>
  +
end | 7 take
""")
        self.assertEqual(list(tagged[0]), explicit_prefix)

        separate = execute("""
1 unfold (< 10) -> (n: Integer) =>
  $n + 1
  if ($n % 2 == 0) => \\None
  else => $n Some
  end
end | #-infinite | 4 take
""")
        self.assertEqual(
            list(separate[0]),
            [
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
            ],
        )

    def test_unfold_condition_gatekeeps_initial_value(self):
        stack = execute(
            "5 unfold (< 5) -> (n: Integer) => $n 1 + end | #-infinite | 1 take"
        )
        self.assertEqual(list(stack[0]), [])

    def test_unfold_condition_gatekeeps_each_state_emission(self):
        stack = execute("""
0 1 unfold (< 100) -> (prev: Integer, next: Integer) =>
  $prev + $next
end | #-infinite | 20 take
""")
        self.assertEqual(
            list(stack[0]),
            [
                RuntimeNumber("1"),
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
                RuntimeNumber("8"),
                RuntimeNumber("13"),
                RuntimeNumber("21"),
                RuntimeNumber("34"),
                RuntimeNumber("55"),
                RuntimeNumber("89"),
            ],
        )

    def test_take_accepts_lazy_lists(self):
        stack = execute("1 5 range | 3 take")
        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(
            list(stack[0]), [RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("3")]
        )

    def test_executes_try_handle_for_panics(self):
        self.assertEqual(
            execute("""
try =>
  RuntimeFault("boom") panic
handle RuntimeFault =>
  "handled"
handle =>
  "default"
end
"""),
            ["handled"],
        )

    def test_vectorisation_length_mismatch_raises_catchable_fault(self):
        self.assertEqual(
            execute("""
try =>
  [1, 2, 3] + [4, 5]
handle VectorisationFault =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_uncaught_vectorisation_fault_reports_fault_type(self):
        with self.assertRaises(RuntimeError) as error:
            execute("[1, 2, 3] + [4, 5]")
        self.assertIn("uncaught panic: VectorisationFault", str(error.exception))
        self.assertIn(
            "cannot vectorise lists with different lengths",
            str(error.exception),
        )

    def test_nested_vectorisation_length_mismatch_is_catchable(self):
        self.assertEqual(
            execute("""
try =>
  [[1, 2], [3, 4, 5]] + [[6, 7], [8, 9]]
handle VectorisationFault =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_try_handle_uses_first_matching_handler(self):
        self.assertEqual(
            execute("""
try =>
  ValueFault("boom") panic
handle KeyFault =>
  "key"
handle =>
  "default"
end
"""),
            ["default"],
        )

    def test_out_of_bounds_indexing_raises_catchable_index_fault(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("""
try =>
  [1, 2, 3] $[5]
handle IndexFault =>
  println "Caught IndexFault"
end
""")

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "Caught IndexFault\n")

    def test_missing_dictionary_key_raises_catchable_key_fault(self):
        self.assertEqual(
            execute("""
try =>
  dict{"present" => 1} $["missing"]
handle KeyFault =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_uncaught_panic_is_runtime_error(self):
        with self.assertRaises(RuntimeError) as error:
            execute('RuntimeFault("boom") panic')
        self.assertIn(
            "uncaught panic: RuntimeFault{message: 'boom'}",
            str(error.exception),
        )

    def test_uncaught_index_fault_is_runtime_error(self):
        with self.assertRaises(RuntimeError) as error:
            execute("[1, 2, 3] $[5]")
        self.assertIn("uncaught panic: IndexFault", str(error.exception))

    def test_println_writes_output_and_consumes_value(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute('"hello" println')

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "hello\n")

    def test_println_formats_finite_lazy_range_as_full_list(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("println range(1, 100)")

        expected = "[" + ", ".join(str(index) for index in range(1, 101)) + "]\n"
        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), expected)

    def test_explicit_function_params_cycle_on_runtime_underflow(self):
        output = io.StringIO()
        source = """
define triple(:Number) => * 3
println triple 5
println(triple([1, 2, 3, 4, 5]))
"""
        program = parse(source)
        analyser = Analyser()
        typed = analyser.analyse(program)
        self.assertEqual(analyser.diagnostics, [])

        with contextlib.redirect_stdout(output):
            stack = run(compile_program(typed, optimize=False))

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "15\n[3, 6, 9, 12, 15]\n")

    def test_dictionary_index_assignment_inserts_missing_key(self):
        self.assertEqual(
            execute("""
                dict{"name" => "Jeff", "age" => 20}
                $["favColour"] = "Magenta"
                """),
            [
                {
                    "name": "Jeff",
                    "age": RuntimeNumber("20"),
                    "favColour": "Magenta",
                }
            ],
        )

    def test_dictionary_printing_uses_fat_arrows_and_quotes_string_keys(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute("""
                dict{"name" => "Jeff", "age" => 20}
                $["favColour"] = "Magenta"
                println
                """)

        self.assertEqual(stack, [])
        self.assertEqual(
            output.getvalue(),
            '{"name" => Jeff, "age" => 20, "favColour" => Magenta}\n',
        )

    def test_indexing_lists_slices_dicts_and_spread(self):
        self.assertEqual(execute("[1, 2, 3] $[1]"), [RuntimeNumber("2")])
        self.assertEqual(
            execute("$data = [5, 1, 6, 2, 7]\n$data[2, 4, 1]"),
            [[RuntimeNumber("6"), RuntimeNumber("7"), RuntimeNumber("1")]],
        )
        self.assertEqual(
            execute("$data = [5, 1, 6, 2, 7]\n$data[1:3]"),
            [[RuntimeNumber("1"), RuntimeNumber("6"), RuntimeNumber("2")]],
        )
        self.assertEqual(
            execute("[[9, 2, 5], [1, 4, 2]] $[[0, 0]:[1, 1]]"),
            [
                [
                    [RuntimeNumber("9"), RuntimeNumber("2")],
                    [RuntimeNumber("1"), RuntimeNumber("4")],
                ]
            ],
        )
        self.assertEqual(execute('dict{"name" => "Jeff"} $["name"]'), ["Jeff"])
        self.assertEqual(
            execute("[5, 1, 6, 2, 7] $...[3, 4]"),
            [RuntimeNumber("2"), RuntimeNumber("7")],
        )
        self.assertEqual(
            execute("$xs = [6, 9, 5, 7, 4, 2, 3]\n$xs...[1, 2, 4]"),
            [RuntimeNumber("9"), RuntimeNumber("5"), RuntimeNumber("4")],
        )

    def test_invalid_multidimensional_slice_raises_catchable_slice_fault(self):
        self.assertEqual(
            execute("""
try =>
  "abc" $[[0, 0]:[1, 1]]
handle SliceFault =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_rugged_multidimensional_slice_faults_at_non_list_dimension(self):
        self.assertEqual(
            execute("""
try =>
  [[1, 2], 3] $[[0, 0]:[1, 1]]
handle SliceFault =>
  "handled"
end
"""),
            ["handled"],
        )

    def test_uncaught_slice_fault_reports_fault_type(self):
        with self.assertRaises(RuntimeError) as error:
            execute('"abc" $[[0, 0]:[1, 1]]')
        self.assertIn("uncaught panic: SliceFault", str(error.exception))
        self.assertIn(
            "multidimensional slicing requires a list at every dimension",
            str(error.exception),
        )

    def test_multi_index_assignment_broadcasts_to_lists_and_dictionaries(self):
        self.assertEqual(
            execute("$list = [1, 2, 3, 4]\n$list[0, 3] = 8\n$list"),
            [
                [
                    RuntimeNumber("8"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("8"),
                ]
            ],
        )
        self.assertEqual(
            execute(
                '$dict = dict{"a" => 1, "b" => 2, "c" => 3}\n'
                '$dict["a", "c"] = 8\n$dict'
            ),
            [
                {
                    "a": RuntimeNumber("8"),
                    "b": RuntimeNumber("2"),
                    "c": RuntimeNumber("8"),
                }
            ],
        )

    def test_indexing_lazy_lists_uses_absolute_indices(self):
        self.assertEqual(
            execute("range(1, 100) $[0, 1, 2, 3, 4, 5]"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("2"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                    RuntimeNumber("5"),
                    RuntimeNumber("6"),
                ]
            ],
        )

    def test_slicing_lazy_lists(self):
        [result] = execute("range(1, 100) $[::2]")

        self.assertIsInstance(result, LazyList)
        self.assertEqual(
            list(islice(result, 6)),
            [
                RuntimeNumber("1"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
                RuntimeNumber("7"),
                RuntimeNumber("9"),
                RuntimeNumber("11"),
            ],
        )

    def test_slice_assignment_replaces_each_selected_item(self):
        self.assertEqual(
            execute("[1, 2, 3, 4, 5]\n$[1:3] = 4"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("4"),
                    RuntimeNumber("4"),
                    RuntimeNumber("4"),
                    RuntimeNumber("5"),
                ]
            ],
        )

    def test_augmented_slice_assignment_updates_each_selected_item(self):
        self.assertEqual(
            execute("[1, 2, 3, 4, 5]\n$[1:3] := + 1"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("3"),
                    RuntimeNumber("4"),
                    RuntimeNumber("5"),
                    RuntimeNumber("5"),
                ]
            ],
        )

    def test_lazy_slice_assignment_can_build_fizzbuzz_positions(self):
        [result] = execute(
            "range(1, 100) map: toString\n"
            '$[2::3] = "Fizz"\n'
            '$[4::5] = "Buzz"\n'
            '$[14::15] = "FizzBuzz"'
        )

        self.assertIsInstance(result, LazyList)
        self.assertEqual(
            list(islice(result, 15)),
            [
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
            ],
        )

    def test_stack_index_augmented_assignment_uses_ambient_stack(self):
        self.assertEqual(
            execute("10 [1, 2, 3, 4] $[2] := *"),
            [[
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("30"),
                RuntimeNumber("4"),
            ]],
        )

    def test_stack_index_augmented_assignment_preserves_operand_order(self):
        self.assertEqual(
            execute("10 [1, 2, 3, 4] $[2] := -"),
            [[
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("7"),
                RuntimeNumber("4"),
            ]],
        )

    def test_index_augmented_assignment_rebuilds_and_assigns_receiver(self):
        self.assertEqual(
            execute("$data = [1, 2, 3]\n$data[1] := + 3\n$data"),
            [[RuntimeNumber("1"), RuntimeNumber("5"), RuntimeNumber("3")]],
        )

    def test_multiple_index_augmented_assignment_transforms_selection_once(self):
        source = "$data = [0, 1, 2, 3, 4, 5]\n" "$data[2, 3, 5] := 1 rotate\n" "$data"
        expected = [
            [
                RuntimeNumber("0"),
                RuntimeNumber("1"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
                RuntimeNumber("4"),
                RuntimeNumber("2"),
            ]
        ]
        self.assertEqual(execute(source), expected)

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(restored), expected)

    def test_augmented_selection_does_not_refill_short_results(self):
        cases = (
            "[3, 5, 4, 2]\n$[0:1] := /: *",
            "[3, 5, 4, 2]\n$[0, 1] := /: *",
            "[3, 5, 4, 2]\n$[range(0, 1)] := /: *",
        )
        expected = [[
            RuntimeNumber("15"),
            RuntimeNumber("4"),
            RuntimeNumber("2"),
        ]]
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(execute(source), expected)

    def test_augmented_sparse_selection_removes_unmatched_positions(self):
        self.assertEqual(
            execute("[3, 5, 4, 2]\n$[0, 2] := /: *"),
            [[RuntimeNumber("12"), RuntimeNumber("5"), RuntimeNumber("2")]],
        )

    def test_augmented_selection_discards_excess_results(self):
        self.assertEqual(
            execute("[3, 5, 4, 2]\n$[0, 2] := 99 append"),
            [[RuntimeNumber("3"), RuntimeNumber("5"), RuntimeNumber("4"), RuntimeNumber("2")]],
        )

    def test_augmented_selection_gathers_in_selector_order(self):
        self.assertEqual(
            execute("[10, 3, 2]\n$[2, 0] := /: -"),
            [[RuntimeNumber("-8"), RuntimeNumber("3")]],
        )
        self.assertEqual(
            execute("[10, 3, 2]\n$[0, 2] := /: -"),
            [[RuntimeNumber("8"), RuntimeNumber("3")]],
        )

    def test_multiple_index_string_augmented_assignment_scatters_characters(self):
        self.assertEqual(
            execute('$text = "abcdef"\n' "$text[1, 3, 5] := 1 rotate\n" "$text"),
            ["adcfeb"],
        )

    def test_multiple_index_augmented_assignment_rejects_duplicate_positions(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "whole-selection augmented assignment contains duplicate indices",
        ):
            execute("$data = [0, 1, 2]\n" "$data[1, 1] := 1 rotate\n" "$data")

    def test_list_valued_selector_gathers_indices_in_declared_order(self):
        source = "$x = [1, 2, 3, 4, 5]\n" "$y = [1, 2, 4, 3, 0]\n" "$x[$y]"
        expected = [
            [
                RuntimeNumber("2"),
                RuntimeNumber("3"),
                RuntimeNumber("5"),
                RuntimeNumber("4"),
                RuntimeNumber("1"),
            ]
        ]
        self.assertEqual(execute(source), expected)

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(restored), expected)

    def test_list_valued_selector_supports_strings_and_dictionaries(self):
        self.assertEqual(
            execute('$text = "abcde"\n$indices = [4, 0, 2]\n$text[$indices]'),
            ["eac"],
        )
        self.assertEqual(
            execute(
                '$data = dict{"a" => 1, "b" => 2}\n'
                '$keys = ["b", "a"]\n'
                "$data[$keys]"
            ),
            [{"b": RuntimeNumber("2"), "a": RuntimeNumber("1")}],
        )

    def test_list_valued_selector_supports_assignment_and_augmentation(self):
        self.assertEqual(
            execute(
                "$data = [1, 2, 3, 4]\n"
                "$indices = [0, 2]\n"
                "$data[$indices] = 9\n"
                "$data"
            ),
            [
                [
                    RuntimeNumber("9"),
                    RuntimeNumber("2"),
                    RuntimeNumber("9"),
                    RuntimeNumber("4"),
                ]
            ],
        )
        self.assertEqual(
            execute(
                "$data = [1, 2, 3, 4]\n"
                "$indices = [0, 2]\n"
                "$data[$indices] := 1 rotate\n"
                "$data"
            ),
            [
                [
                    RuntimeNumber("3"),
                    RuntimeNumber("2"),
                    RuntimeNumber("1"),
                    RuntimeNumber("4"),
                ]
            ],
        )

    def test_list_valued_augmented_selector_rejects_duplicate_indices(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "whole-selection augmented assignment contains duplicate indices",
        ):
            execute(
                "$data = [1, 2, 3]\n"
                "$indices = [1, 1]\n"
                "$data[$indices] := 1 rotate"
            )

    def test_integer_rank_two_selector_gathers_multidimensional_paths(self):
        source = (
            "$matrix = [[1, 2], [3, 4]]\n"
            "$paths = [[0, 1], [1, 0]]\n"
            "$matrix[$paths]"
        )
        expected = [[RuntimeNumber("2"), RuntimeNumber("3")]]
        self.assertEqual(execute(source), expected)

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(restored), expected)

    def test_integer_rank_two_selector_supports_assignment_and_augmentation(self):
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4]]\n"
                "$matrix[[[0, 1], [1, 0]]] = 9\n"
                "$matrix"
            ),
            [
                [
                    [RuntimeNumber("1"), RuntimeNumber("9")],
                    [RuntimeNumber("9"), RuntimeNumber("4")],
                ]
            ],
        )
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4]]\n"
                "$matrix[[[0, 1], [1, 0]]] := 1 rotate\n"
                "$matrix"
            ),
            [
                [
                    [RuntimeNumber("1"), RuntimeNumber("3")],
                    [RuntimeNumber("2"), RuntimeNumber("4")],
                ]
            ],
        )

    def test_integer_rank_two_augmented_selector_rejects_duplicate_paths(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "whole-selection augmented assignment contains duplicate indices",
        ):
            execute(
                "$matrix = [[1, 2], [3, 4]]\n" "$matrix[[[0, 1], [0, 1]]] := 1 rotate"
            )

    def test_none_path_component_gathers_strict_wildcard_column(self):
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4], [5, 6]]\n"
                "$matrix[[\\None, 0]]"
            ),
            [[RuntimeNumber("1"), RuntimeNumber("3"), RuntimeNumber("5")]],
        )

    def test_index_underscore_is_none_path_shorthand(self):
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4], [5, 6]]\n"
                "$matrix[[_, 1]]"
            ),
            [[RuntimeNumber("2"), RuntimeNumber("4"), RuntimeNumber("6")]],
        )

    def test_none_path_component_supports_assignment_and_augmentation(self):
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4], [5, 6]]\n"
                "$matrix[[\\None, 0]] = 9\n$matrix"
            ),
            [[[RuntimeNumber("9"), RuntimeNumber("2")],
              [RuntimeNumber("9"), RuntimeNumber("4")],
              [RuntimeNumber("9"), RuntimeNumber("6")]]],
        )
        self.assertEqual(
            execute(
                "$matrix = [[1, 2], [3, 4], [5, 6]]\n"
                "$matrix[[_, 0]] := 1 rotate\n$matrix"
            ),
            [[[RuntimeNumber("3"), RuntimeNumber("2")],
              [RuntimeNumber("5"), RuntimeNumber("4")],
              [RuntimeNumber("1"), RuntimeNumber("6")]]],
        )

    def test_none_path_component_works_with_update_and_update_by(self):
        self.assertEqual(
            execute("update([[1, 2], [3, 4]], [\\None, 0], 8)"),
            [[[RuntimeNumber("8"), RuntimeNumber("2")],
              [RuntimeNumber("8"), RuntimeNumber("4")]]],
        )
        self.assertEqual(
            execute(
                "updateBy([[1, 2], [3, 4]], [\\None, 0], "
                "fn (xs) => $xs 1 rotate end)"
            ),
            [[[RuntimeNumber("3"), RuntimeNumber("2")],
              [RuntimeNumber("1"), RuntimeNumber("4")]]],
        )

    def test_none_scalar_index_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "None is not a valid scalar index"):
            execute("$xs = [1, 2]\n$xs[\\None]")

    def test_none_path_expansion_is_strict_for_ragged_lists(self):
        with self.assertRaisesRegex(RuntimeError, "index 1 is out of range"):
            execute(
                "$xs = [[1, 2], [3], [4, 5]]\n"
                "$xs[[\\None, 1]]"
            )

    def test_vectorised_comparison_result_is_a_boolean_mask(self):
        source = (
            "$values = [5, 1, 2, 7, 8, 2, 4, 6, 7, 1, 4, 7, 8]\n" "$values[$values < 5]"
        )
        expected = [
            [
                RuntimeNumber("1"),
                RuntimeNumber("2"),
                RuntimeNumber("2"),
                RuntimeNumber("4"),
                RuntimeNumber("1"),
                RuntimeNumber("4"),
            ]
        ]
        self.assertEqual(execute(source), expected)

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads(dumps(compile_program(typed, optimize=False)))
        self.assertEqual(run(restored), expected)

    def test_boolean_mask_indexes_lists_and_strings(self):
        self.assertEqual(
            execute("[1, 2, 3, 4] $[[true, false, true]]"),
            [[RuntimeNumber("1"), RuntimeNumber("3")]],
        )
        self.assertEqual(
            execute('"abcd" $[[true, false, true]]'),
            ["ac"],
        )

    def test_boolean_mask_may_be_shorter_but_not_longer_than_receiver(self):
        self.assertEqual(
            execute("[1, 2, 3, 4] $[[false, true]]"),
            [[RuntimeNumber("2")]],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "Boolean mask is longer than the indexed value",
        ):
            execute("[1, 2] $[[true, false, true]]")

    def test_selector_functions_index_lists_strings_and_dictionaries(self):
        self.assertEqual(
            execute(
                "[1, 2, 3, 4] " "$[fn (x: Number) -> #boolean Number => $x > 2 end]"
            ),
            [[RuntimeNumber("3"), RuntimeNumber("4")]],
        )
        self.assertEqual(
            execute('"abcd" ' '$[fn (c: String) -> #boolean Number => $c in "bd" end]'),
            ["bd"],
        )
        self.assertEqual(
            execute(
                '$data = dict{"a" => 1, "b" => 2, "c" => 3}\n'
                "$data[fn (key: String, value: Number) -> #boolean Number => "
                "$value > 1 end]"
            ),
            [{"b": RuntimeNumber("2"), "c": RuntimeNumber("3")}],
        )

    def test_mask_and_function_selectors_support_normal_assignment(self):
        self.assertEqual(
            execute(
                "$data = [1, 2, 3, 4]\n" "$data[[true, false, true]] = 9\n" "$data"
            ),
            [
                [
                    RuntimeNumber("9"),
                    RuntimeNumber("2"),
                    RuntimeNumber("9"),
                    RuntimeNumber("4"),
                ]
            ],
        )
        self.assertEqual(
            execute(
                '$data = dict{"a" => 1, "b" => 2}\n'
                "$data[fn (key: String, value: Number) -> #boolean Number => "
                "true end] = 7\n"
                "$data"
            ),
            [{"a": RuntimeNumber("7"), "b": RuntimeNumber("7")}],
        )

    def test_mask_and_function_selectors_preserve_grouped_augmented_assignment(self):
        self.assertEqual(
            execute(
                "$data = [1, 2, 3, 4]\n"
                "$data[[true, false, true]] := 1 rotate\n"
                "$data"
            ),
            [
                [
                    RuntimeNumber("3"),
                    RuntimeNumber("2"),
                    RuntimeNumber("1"),
                    RuntimeNumber("4"),
                ]
            ],
        )
        self.assertEqual(
            execute(
                '$text = "abcd"\n'
                "$text[fn (c: String) -> #boolean Number => "
                '$c in "bd" end] := 1 rotate\n'
                "$text"
            ),
            ["adcb"],
        )

    def test_dictionary_function_selector_supports_augmented_assignment(self):
        source = (
            "define keep(value: Dict[String, Integer]) -> "
            "Dict[String, Integer] => $value end\n"
            '$data = dict{"a" => 1, "b" => 2}\n'
            "$data[fn (key: String, value: Integer) -> #boolean Number => "
            "$value > 1 end] := keep\n"
            "$data"
        )
        self.assertEqual(
            execute(source),
            [{"a": RuntimeNumber("1"), "b": RuntimeNumber("2")}],
        )

    def test_normal_slice_assignment_remains_replacement(self):
        self.assertEqual(
            execute("[1, 2, 3, 4]\n$[1:2] = 9"),
            [
                [
                    RuntimeNumber("1"),
                    RuntimeNumber("9"),
                    RuntimeNumber("9"),
                    RuntimeNumber("4"),
                ]
            ],
        )

    def test_multiple_assignment_stores_corresponding_values(self):
        self.assertEqual(
            execute("$(a, b, c) = 1 2 3\n$a $b $c"),
            [RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("3")],
        )

    def test_multiple_assignment_fills_missing_values_from_existing_stack(self):
        self.assertEqual(
            execute("1\n$(a, b, c) = 2 3\n$a $b $c"),
            [RuntimeNumber("1"), RuntimeNumber("2"), RuntimeNumber("3")],
        )

    def test_indexing_cycles_explicit_parameter_receiver(self):
        self.assertEqual(
            execute("define second(:Number+) -> Number => $[1]\nsecond([4, 9])"),
            [RuntimeNumber("9")],
        )

    def test_runtime_element_errors_show_stack_and_attempted_inputs(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, "x"),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("1")),
                    Instruction(OpCode.LOAD_ELEMENT, "-"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        with self.assertRaises(RuntimeError) as error:
            run(program)

        message = str(error.exception)
        self.assertIn("cannot call element '-'", message)
        self.assertIn("stack: ['x', 1]", message)
        self.assertIn("stack types: [String, Integer]", message)
        self.assertIn("attempted input shapes:", message)
        self.assertIn("(Number, Number)", message)
        self.assertIn("runtime context:", message)
        self.assertIn("<main> ip 3: call", message)

    def test_runtime_diagnostics_format_functions_compactly(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            execute("fn => + end | println")

        self.assertEqual(
            output.getvalue(),
            "<overloaded function [2, 2, 2, 2, 2, 2]>\n",
        )

        program = Program(
            FunctionCode(
                (
                    Instruction(
                        OpCode.MAKE_FUNCTION,
                        FunctionCode((), params=("x",), name="held"),
                    ),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("1")),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )
        with self.assertRaises(RuntimeError) as error:
            run(program)

        message = str(error.exception)
        self.assertIn("cannot call value 1", message)
        self.assertIn("stack: [<held/1>]", message)
        self.assertNotIn("globals=", message)
        self.assertNotIn("FunctionCode(", message)

    def test_runtime_errors_show_nested_function_context(self):
        inner = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "value"),
                Instruction(OpCode.CHECK_CAST, ("nominal", "String")),
            ),
            params=("value",),
            name="bad_cast",
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("1")),
                    Instruction(OpCode.MAKE_FUNCTION, inner),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        with self.assertRaises(RuntimeError) as error:
            run(program)

        message = str(error.exception)
        self.assertIn("checked cast failed: 1 is Integer", message)
        self.assertIn("target: function 'bad_cast'", message)
        self.assertIn("arguments: [1]", message)
        self.assertIn("bad_cast ip 1: check_cast", message)
        self.assertIn("<main> ip 2: call", message)

    def test_match_binding_shadows_outer_variable_at_runtime(self):
        self.assertEqual(
            execute(
                '$x = 1\n"abc"\nmatch =>\n'
                "  as x: String => $x length\n"
                "  _ => 0\nend"
            ),
            [3],
        )

    def test_irrefutable_variant_destructure_is_exhaustive(self):
        self.assertEqual(
            execute("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(2)
match =>
  as :Some(_) => "some"
  as :None => "none"
end
"""),
            ["some"],
        )

    def test_irrefutable_type_pattern_narrows_a_later_default_branch(self):
        self.assertEqual(
            execute(
                '$x = (if 0 1 == => 1 else => "s" end)\n'
                "$x\nmatch =>\n"
                "  as :Number => 0\n"
                "  _ => $x length\nend"
            ),
            [1],
        )

    def test_wildcard_coordinate_preserves_safe_multi_subject_narrowing(self):
        self.assertEqual(
            execute(
                '$x = (if 0 1 == => 1 else => "x" end)\n'
                '$y = (if 1 1 == => 1 else => "y" end)\n'
                "$x $y\nmatch =>\n"
                "  _, as :Number => 0\n"
                "  _, _ => $x length\nend"
            ),
            [1],
        )

    def test_match_preserves_source_order_before_a_wildcard_case(self):
        self.assertEqual(
            execute("1\nmatch =>\n" '  _ => "first"\n' '  1 => "second"\nend'),
            ["first"],
        )

    def test_guarded_untyped_pattern_is_not_reordered_or_dropped(self):
        self.assertEqual(
            execute(
                "1\nmatch =>\n"
                '  as x if > 0 => "positive"\n'
                '  1 => "one"\n'
                '  _ => "other"\nend'
            ),
            ["positive"],
        )

    def test_repeated_match_binding_requires_equal_values(self):
        self.assertEqual(
            execute(
                "1 2\nmatch =>\n"
                '  $x = _, $x = _ => "same"\n'
                '  _, _ => "different"\nend'
            ),
            ["different"],
        )
        self.assertEqual(
            execute(
                "1 1\nmatch =>\n"
                '  $x = _, $x = _ => "same"\n'
                '  _, _ => "different"\nend'
            ),
            ["same"],
        )


if __name__ == "__main__":
    unittest.main()


class ImportedOverloadRuntimeTests(unittest.TestCase):
    def test_imported_recursive_definition_calls_hidden_runtime_binding(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "src"
            source_dir.mkdir()
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            (source_dir / "fib.vlnc").write_text(
                "public define fibonacci(n: Integer) -> Integer =>\n"
                "  $n match =>\n"
                "    0 => 0\n"
                "    1 => 1\n"
                "    _ => fibonacci($n - 1) + fibonacci($n - 2)\n"
                "  end\n"
                "end\n",
                encoding="utf-8",
            )
            main = source_dir / "main.vlnc"
            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse("import {fib.fibonacci}\nfibonacci(10)"))
            self.assertFalse(analyser.diagnostics)

            direct = run(compile_program(typed, optimize=False))
            restored = run(loads(dumps(compile_program(typed, optimize=False))))

        self.assertEqual(direct, [RuntimeNumber(55)])
        self.assertEqual(restored, direct)

    def test_component_import_wins_when_same_dotted_path_is_also_a_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moduleA").mkdir()
            (root / "moduleA.vlnc").write_text(
                'public define fileA(value: String) -> String => "moduleA.fileA"\n',
                encoding="utf-8",
            )
            (root / "moduleA" / "fileA.vlnc").write_text(
                'public define fileA(value: String) -> String => "moduleA/fileA"\n',
                encoding="utf-8",
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            typed = analyser.analyse(parse('import {moduleA.fileA}\nfileA("hello")'))
            self.assertEqual(analyser.diagnostics, [])
            result = run(compile_program(typed, optimize=False))

        self.assertEqual(result, ["moduleA.fileA"])

    def test_imported_overloads_call_the_runtime_body_selected_by_analysis(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "src"
            source_dir.mkdir()
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            (source_dir / "app.vlnc").write_text(
                'public define greeting(name: String) -> String => "Hello, $name!"\n'
                "public define greeting(x: Integer) -> Integer => $x + 1\n",
                encoding="utf-8",
            )
            main = source_dir / "main.vlnc"
            source = "import {app.greeting}\n" 'greeting("Valiance")\n' "greeting(5)"
            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse(source))
            self.assertFalse(analyser.diagnostics)

            direct = run(compile_program(typed, optimize=False))
            restored = run(loads(dumps(compile_program(typed, optimize=False))))

        self.assertEqual(direct, ["Hello, Valiance!", RuntimeNumber(6)])
        self.assertEqual(restored, direct)


    def test_variable_call_placeholders_fill_right_to_left_before_leftovers(self):
        source = r'''
$f = fn (a: String, b: String, c: String, d: String, e: String) -> String+ =>
  [$a, $b, $c, $d, $e]
end
"last"
"first"
"third"
$f(
  _,
  "second",
  _,
  "fourth"
)
'''
        self.assertEqual(
            execute(source),
            [["first", "second", "third", "fourth", "last"]],
        )


    def test_partial_element_call_binds_explicit_argument_on_the_right(self):
        self.assertEqual(execute("5 -(3)"), [RuntimeNumber(2)])

    def test_split_uses_the_same_argument_order_in_stack_and_ecs_syntax(self):
        stack_result = execute('"hello world" " " split')
        ecs_result = execute('"hello world" split(" ")')

        self.assertEqual(stack_result, [["hello", "world"]])
        self.assertEqual(ecs_result, stack_result)

    def test_fold_seed_can_be_supplied_by_partial_element_call(self):
        stack_result = execute("[1, 2, 3, 4, 5] 0 fold: +")
        ecs_result = execute("[1, 2, 3, 4, 5] fold(0): +")

        self.assertEqual(stack_result, [RuntimeNumber(15)])
        self.assertEqual(ecs_result, stack_result)


class StackValueUpdateTests(unittest.TestCase):
    """Exercise value-producing indexed updates through the public elements."""

    def test_update_replaces_a_scalar_index_without_mutating_input(self):
        self.assertEqual(
            execute("$xs = [1, 2, 3]\nupdate($xs, 1, 9)\n$xs"),
            [[RuntimeNumber(1), RuntimeNumber(9), RuntimeNumber(3)],
             [RuntimeNumber(1), RuntimeNumber(2), RuntimeNumber(3)]],
        )

    def test_update_accepts_integer_list_from_range(self):
        self.assertEqual(
            execute("update([1, 2, 3, 4], range(1, 2), 9)"),
            [[RuntimeNumber(1), RuntimeNumber(9), RuntimeNumber(9), RuntimeNumber(4)]],
        )

    def test_update_by_applies_function_to_scalar_index(self):
        self.assertEqual(
            execute("updateBy([1, 2, 3], 1, fn (x) => $x + 10 end)"),
            [[RuntimeNumber(1), RuntimeNumber(12), RuntimeNumber(3)]],
        )

    def test_update_by_applies_function_once_to_whole_selection(self):
        self.assertEqual(
            execute(
                "updateBy([1, 2, 3, 4], range(1, 2), "
                "fn (xs) => $xs + 10 end)"
            ),
            [[
                RuntimeNumber(1),
                RuntimeNumber(12),
                RuntimeNumber(13),
                RuntimeNumber(4),
            ]],
        )

    def test_update_by_round_trips_through_bytecode(self):
        source = "updateBy([1, 2, 3], 1, fn (x) => $x * 2 end)"
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertFalse(analyser.diagnostics)
        program = compile_program(typed, optimize=False)
        self.assertEqual(
            run(loads(dumps(program))),
            [[RuntimeNumber(1), RuntimeNumber(4), RuntimeNumber(3)]],
        )


class MinimumRankAssuranceTests(unittest.TestCase):
    def infer_type(self, source: str) -> str:
        import valiance.vtypes as T

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return T.show(typed[-1].typ)

    def test_default_rank_is_equivalent_to_explicit_one(self):
        self.assertEqual(execute("1 ^+"), execute("1 ^+1"))
        self.assertEqual(self.infer_type("1 ^+"), "Integer+")
        self.assertEqual(self.infer_type("1 ^+1"), "Integer+")

    def test_wraps_atomic_and_lower_rank_values(self):
        self.assertEqual(execute("1 ^+2"), [[[RuntimeNumber(1)]]])
        self.assertEqual(
            execute("[1, 2] ^+2"),
            [[[RuntimeNumber(1), RuntimeNumber(2)]]],
        )

    def test_wraps_repeatedly_to_a_higher_requested_rank(self):
        self.assertEqual(execute("1 ^+4"), [[[[[RuntimeNumber(1)]]]]])
        self.assertEqual(self.infer_type("1 ^+4"), "Integer+4")

    def test_wraps_ragged_runtime_value_by_its_shallowest_branch(self):
        self.assertEqual(
            execute("[1, [2, 3]] ^+2"),
            [[[RuntimeNumber(1), [RuntimeNumber(2), RuntimeNumber(3)]]]],
        )

    def test_only_consumes_the_top_stack_value(self):
        self.assertEqual(
            execute("1 2 ^+2"),
            [RuntimeNumber(1), [[RuntimeNumber(2)]]],
        )

    def test_chain_membership_applies_assurance_before_element(self):
        self.assertEqual(execute("5 length ^+"), [RuntimeNumber(1)])

    def test_does_not_wrap_values_already_at_the_minimum(self):
        self.assertEqual(
            execute("[[1, 2], [3, 4]] ^+2"),
            [[[RuntimeNumber(1), RuntimeNumber(2)], [RuntimeNumber(3), RuntimeNumber(4)]]],
        )

    def test_union_branches_below_target_resimplify_to_one_type(self):
        import valiance.vtypes as T

        analyser = Analyser()
        typed = analyser.analyse(parse("fn (:Number|Number+) => ^+2"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(T.show(typed[0].typ), "Function[Number | Number+ -> Number+2]")

    def test_union_keeps_branches_that_already_exceed_target(self):
        import valiance.vtypes as T

        analyser = Analyser()
        typed = analyser.analyse(parse("fn (:Number|Number+3) => ^+2"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            T.show(typed[0].typ),
            "Function[Number | Number+3 -> Number+2 | Number+3]",
        )

    def test_union_preserves_each_collection_rank_mode(self):
        import valiance.vtypes as T

        analyser = Analyser()
        typed = analyser.analyse(parse("fn (:String|String+|String*|String~) => ^+2"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            T.show(typed[0].typ),
            "Function[String | String* | String+ | String~ -> String*2 | String+2 | String~2]",
        )

    def test_exact_rank_is_raised_or_retained(self):
        self.assertEqual(
            self.infer_type("fn (:Number+) => ^+3"),
            "Function[Number+ -> Number+3]",
        )
        self.assertEqual(
            self.infer_type("fn (:Number+4) => ^+3"),
            "Function[Number+4 -> Number+4]",
        )

    def test_minimum_and_rugged_rank_are_raised_or_retained(self):
        cases = {
            "fn (:Number*) => ^+3": "Function[Number* -> Number*3]",
            "fn (:Number*4) => ^+3": "Function[Number*4 -> Number*4]",
            "fn (:Number~) => ^+3": "Function[Number~ -> Number~3]",
            "fn (:Number~4) => ^+3": "Function[Number~4 -> Number~4]",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(self.infer_type(source), expected)

    def test_type_inference_preserves_rank_mode_and_maps_unions(self):
        import valiance.vtypes as T

        cases = {
            "fn (:Number*) => ^+2": "Function[Number* -> Number*2]",
            "fn (:Number|Number+) => ^+": "Function[Number | Number+ -> Number+]",
            "fn (:String+|String*) => ^+2": "Function[String* | String+ -> String*2 | String+2]",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                analyser = Analyser()
                typed = analyser.analyse(parse(source))
                self.assertEqual(analyser.diagnostics, [])
                self.assertEqual(T.show(typed[0].typ), expected)

    def test_empty_inferred_function_stack_is_a_compile_error(self):
        analyser = Analyser()
        analyser.analyse(parse("fn => ^+"))
        self.assertTrue(analyser.diagnostics)
        self.assertIn("empty stack when assuring minimum rank", str(analyser.diagnostics[0]))

    def test_round_trips_through_bytecode_in_all_execution_modes(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2] ^+3"))
        self.assertEqual(analyser.diagnostics, [])
        expected = [[[[RuntimeNumber(1), RuntimeNumber(2)]]]]
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                program = compile_program(typed, optimize=optimize)
                self.assertEqual(run(program), expected)
                self.assertEqual(run(loads(dumps(program))), expected)


class FirstClassOverloadSetCallRuntimeTests(unittest.TestCase):
    def test_empty_variable_call_executes_generic_list_overload_at_higher_rank(self):
        self.assertEqual(
            execute("""
$f = fn (xs) => $xs 1 rotate end
[[1, 2], [3, 4], [5, 6]] $f()
"""),
            [[[RuntimeNumber(3), RuntimeNumber(4)],
              [RuntimeNumber(5), RuntimeNumber(6)],
              [RuntimeNumber(1), RuntimeNumber(2)]]],
        )

    def test_extracting_match_capture_is_not_an_implicit_branch_result(self):
        self.assertEqual(
            execute("""
[1, 9, 3] match =>
  extract [1, _, 3] => "matched"
  _ => "other"
end
"""),
            ["matched"],
        )

    def test_extract_does_not_leak_across_or_patterns_or_later_cases(self):
        self.assertEqual(
            execute("""
[2, 7]
match =>
  extract [1, _] || [2, _] => * 2
  [3, _] => "normal"
  _ => "fallback"
end
"""),
            [[RuntimeNumber(4), RuntimeNumber(14)]],
        )
        self.assertEqual(
            execute("""
[3, 7]
match =>
  extract [1, _] || [2, _] => * 2
  [3, _] => "normal"
  _ => "fallback"
end
"""),
            ["normal"],
        )

    def test_extract_does_not_leak_across_commas_or_later_cases(self):
        self.assertEqual(
            execute("""
[2, 5] [1, 4]
match =>
  extract [1, _], [2, _] => +
  [3, _], [4, _] => "normal"
  _, _ => "fallback"
end
"""),
            [[RuntimeNumber(6), RuntimeNumber(9)]],
        )
        self.assertEqual(
            execute("""
[4, 8] [3, 7]
match =>
  extract [1, _], [2, _] => +
  [3, _], [4, _] => "normal"
  _, _ => "fallback"
end
"""),
            ["normal"],
        )

    def test_extract_handles_or_before_comma_without_affecting_next_case(self):
        self.assertEqual(
            execute("""
[3, 5] [2, 4]
match =>
  extract [1, _] || [2, _], [3, _] => +
  [4, _], [5, _] => "normal"
  _, _ => "fallback"
end
"""),
            [[RuntimeNumber(5), RuntimeNumber(9)]],
        )
        self.assertEqual(
            execute("""
[5, 8] [4, 7]
match =>
  extract [1, _] || [2, _], [3, _] => +
  [4, _], [5, _] => "normal"
  _, _ => "fallback"
end
"""),
            ["normal"],
        )

    def test_extracting_match_patterns_project_structural_and_regex_captures(self):
        self.assertEqual(
            execute("""
[1, 9, 3] match =>
  extract [1, _, 3] => * 2
  _ => 0
end
"""),
            [RuntimeNumber(18)],
        )
        self.assertEqual(
            execute("""
[4, 5, 6, 7] match =>
  extract [4, ..., 7] => sum
  _ => 0
end
"""),
            [RuntimeNumber(11)],
        )
        self.assertEqual(
            execute('''"item:42" match =>
  extract "(?<kind>[a-z]+):([0-9]+)" => $kind swap
  _ => "no"
end'''),
            ["item", "42"],
        )
        self.assertEqual(
            execute("""
[1, 8, 3] match =>
  extract [1, $n = _, 3] => $n
  _ => 0
end
"""),
            [RuntimeNumber(8)],
        )
        analyser = Analyser()
        analyser.analyse(parse("1 match => extract _ => 2 end"))
        self.assertTrue(
            any("extract requires at least one" in str(item) for item in analyser.diagnostics)
        )
