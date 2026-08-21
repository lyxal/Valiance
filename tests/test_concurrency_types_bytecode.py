import unittest

from valiance.runtime import dumps, loads
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.vtypes import (
    Int, Number, String, Task, TaskType, V, _solve, _substitute,
    ElementTag, assignable, show,
)


class TaskTypeTests(unittest.TestCase):
    def test_task_preserves_native_output_row(self):
        task = Task(Int, String)
        self.assertIsInstance(task, TaskType)
        self.assertEqual(task.outputs, (Int, String))
        self.assertEqual(show(task), "Task[Int, String]")

    def test_empty_output_task_has_unambiguous_display(self):
        self.assertEqual(show(Task()), "Task[->]")

    def test_task_output_generics_substitute_positionally(self):
        task = Task(V("T"), String)
        self.assertEqual(_substitute(task, {"T": Int}), Task(Int, String))

    def test_task_rows_are_invariant(self):
        self.assertTrue(assignable(Task(Int), Task(Int)))
        self.assertFalse(assignable(Task(Int), Task(Number)))
        self.assertFalse(assignable(Task(Int), Task(Int, String)))

    def test_task_row_participates_in_generic_solving(self):
        self.assertEqual(_solve(Task(V("T")), Task(Int)), {"T": [Int]})

    def test_task_effects_are_preserved_and_invariant(self):
        from valiance.vtypes.symbols import Symbol
        io = ElementTag(Symbol("io"))
        task = Task(Int, effects=(io,))
        self.assertEqual(task.effects, frozenset({io}))
        self.assertFalse(assignable(task, Task(Int)))
        self.assertEqual(_substitute(task, {}), task)

    def test_channel_nominal_argument_is_invariant(self):
        from valiance.vtypes import Context, N
        from valiance.vtypes.symbols import Symbol

        context = Context()
        self.assertFalse(assignable(N(Symbol("Channel"), Int), N(Symbol("Channel"), Number), context))


class ConcurrencyBytecodeTests(unittest.TestCase):
    def test_all_concurrency_opcodes_round_trip(self):
        instructions = (
            Instruction(OpCode.SCOPE_BEGIN),
            Instruction(OpCode.SPAWN_CALL, (0, 1, 0)),
            Instruction(OpCode.WAIT_TASK, 1),
            Instruction(OpCode.WAIT_TASKS_VECTORISED, 1),
            Instruction(OpCode.CHANNEL_NEW, False),
            Instruction(OpCode.CHANNEL_SEND),
            Instruction(OpCode.CHANNEL_RECEIVE),
            Instruction(OpCode.CHANNEL_CLOSE),
            Instruction(OpCode.CANCEL_POLL),
            Instruction(OpCode.SCOPE_END),
            Instruction(OpCode.RETURN),
        )
        program = Program(
            FunctionCode(
                instructions,
                name="<concurrency-roundtrip>",
            )
        )
        self.assertEqual(loads(dumps(program)), program)

    def test_surface_task_annotation_normalizes_to_task_output_row(self):
        from valiance.parsing import parse
        from valiance.vtypes import TaskType, normalize

        definition = parse(
            "define f -> Task[Int, String] => "
            "fn -> Int, String => 1 \"one\" end | spawn end"
        )[0]
        declared = definition.function.returns[0]
        normalized = normalize(declared)
        self.assertIsInstance(normalized, TaskType)
        self.assertEqual(normalized.outputs, (Int, String))


if __name__ == "__main__":
    unittest.main()
