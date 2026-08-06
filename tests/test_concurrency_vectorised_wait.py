import unittest

from valiance.analysis import Analyser
from valiance.asts import TypedWaitNode
from valiance.parsing import parse
from valiance.runtime import compile_program, run
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.vtypes import ExactList, Integer, String, Task


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute(source: str):
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=False))


class VectorisedWaitAnalysisTests(unittest.TestCase):
    def test_task_list_lifts_each_native_output(self):
        analyser, typed = analyse(
            '[fn -> Integer, String => 1 "a" end | spawn] | wait'
        )
        self.assertEqual(analyser.diagnostics, [])
        wait = typed[-1]
        self.assertIsInstance(wait, TypedWaitNode)
        self.assertTrue(wait.vectorised)
        self.assertEqual(
            wait.output_types,
            (ExactList(Integer), ExactList(String)),
        )

    def test_non_task_collection_is_rejected(self):
        analyser, _ = analyse("[1, 2] | wait")
        self.assertTrue(any("task collection" in item for item in analyser.diagnostics))


class VectorisedWaitExecutionTests(unittest.TestCase):
    def test_source_group_wait_preserves_order(self):
        self.assertEqual(
            execute(
                """[
  fn -> Integer => 2 end | spawn,
  fn -> Integer => 1 end | spawn
] | wait"""
            ),
            [[RuntimeNumber(2), RuntimeNumber(1)]],
        )

    def test_source_group_wait_lifts_multiple_outputs(self):
        self.assertEqual(
            execute(
                """[
  fn -> Integer, String => 1 "one" end | spawn,
  fn -> Integer, String => 2 "two" end | spawn
] | wait"""
            ),
            [
                [RuntimeNumber(1), RuntimeNumber(2)],
                ["one", "two"],
            ],
        )

    def test_runtime_group_wait_preserves_nested_shape(self):
        from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
        from valiance.runtime.vm import VirtualMachine

        vm = VirtualMachine(output=lambda _value: None)
        scope = vm.scheduler.root_scope
        first = scope.spawn(lambda: (1,))
        second = scope.spawn(lambda: (2,))
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, [[second], [first, second]]),
                    Instruction(OpCode.WAIT_TASKS_VECTORISED, 1),
                    Instruction(OpCode.RETURN),
                ),
                name="<nested-group-wait>",
            )
        )
        self.assertEqual(vm.run(program), [[[2], [1, 2]]])

    def test_empty_typed_task_collection_returns_shaped_empty_output(self):
        self.assertEqual(
            execute("[] as[Task[Integer]+] | wait"),
            [[]],
        )

    def test_empty_multiple_output_task_collection_returns_one_empty_per_output(self):
        self.assertEqual(
            execute("[] as[Task[Integer, String]+] | wait"),
            [[], []],
        )


if __name__ == "__main__":
    unittest.main()
