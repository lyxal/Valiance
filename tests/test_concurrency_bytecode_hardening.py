"""Release-gate compatibility and optimizer tests for concurrency bytecode."""

from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
)
from valiance.runtime.optimizer import (
    DEFAULT_OPTIMIZATION_PIPELINE,
    FunctionOptimizationPass,
    OptimizationError,
    OptimizationPipeline,
)
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads
from valiance.vtypes import RuntimeTypePattern, UnionDispatchBranch

FIXTURES = Path(__file__).parent / "fixtures" / "bytecode"


def concurrency_leaf(name: str) -> FunctionCode:
    """Build one nested function containing every semantic barrier family."""
    return FunctionCode(
        (
            Instruction(OpCode.SCOPE_BEGIN, (0, 0, "1:1")),
            Instruction(OpCode.PUSH_CONST, 1),
            Instruction(OpCode.CHANNEL_NEW, (True, "2:1")),
            Instruction(OpCode.CHANNEL_CLOSE, "3:1"),
            Instruction(OpCode.CANCEL_POLL),
            Instruction(OpCode.SCOPE_END, (0, 0, "4:1")),
            Instruction(OpCode.RETURN),
        ),
        name=name,
        recursive=True,
    )


class _RemovePollPass(FunctionOptimizationPass):
    name = "illegal-remove-poll"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        return replace(
            function,
            instructions=tuple(
                item for item in function.instructions if item.op is not OpCode.CANCEL_POLL
            ),
        )


class ConcurrencyCompatibilityFixtureTests(unittest.TestCase):
    def test_checked_in_current_fixture_loads_and_round_trips(self):
        data = (FIXTURES / "concurrency-v30.vbc").read_bytes()
        program = loads(data)
        self.assertEqual(loads(dumps(program)), program)

    def test_checked_in_older_fixture_has_clear_version_failure(self):
        data = (FIXTURES / "concurrency-v29-unsupported.vbc").read_bytes()
        with self.assertRaisesRegex(
            BytecodeFormatError,
            "unsupported Valiance bytecode version 29; expected 30",
        ):
            loads(data)


class ConcurrencyOptimizerContractTests(unittest.TestCase):
    def test_function_set_union_dispatch_and_recursive_payload_round_trip(self):
        branch = UnionDispatchBranch((RuntimeTypePattern("any"),), 0)
        function_set = FunctionSetCode(
            (concurrency_leaf("generic-specialization"), concurrency_leaf("recursive")),
            dispatch_plan=(branch,),
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, function_set),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )
        optimized = DEFAULT_OPTIMIZATION_PIPELINE.optimize(program)
        self.assertEqual(loads(dumps(program)), program)
        self.assertEqual(loads(dumps(optimized)), optimized)
        nested = optimized.main.instructions[0].arg
        self.assertEqual(nested.dispatch_plan, function_set.dispatch_plan)
        for overload in nested.overloads:
            self.assertTrue(overload.recursive)
            self.assertEqual(
                tuple(item.op for item in overload.instructions if item.op in {
                    OpCode.SCOPE_BEGIN,
                    OpCode.CHANNEL_NEW,
                    OpCode.CHANNEL_CLOSE,
                    OpCode.CANCEL_POLL,
                    OpCode.SCOPE_END,
                }),
                (
                    OpCode.SCOPE_BEGIN,
                    OpCode.CHANNEL_NEW,
                    OpCode.CHANNEL_CLOSE,
                    OpCode.CANCEL_POLL,
                    OpCode.SCOPE_END,
                ),
            )

    def test_pipeline_rejects_pass_that_changes_nested_barriers(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, concurrency_leaf("nested")),
                    Instruction(OpCode.RETURN),
                )
            )
        )
        with self.assertRaisesRegex(OptimizationError, "changed concurrency barrier"):
            OptimizationPipeline((_RemovePollPass(),)).optimize(program)

    def test_inlining_candidate_with_concurrency_is_not_inlined(self):
        nested = concurrency_leaf("small-but-concurrent")
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, nested),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )
        optimized = DEFAULT_OPTIMIZATION_PIPELINE.optimize(program)
        self.assertIsInstance(optimized.main.instructions[0].arg, FunctionCode)
        self.assertIn(
            OpCode.CANCEL_POLL,
            tuple(item.op for item in optimized.main.instructions[0].arg.instructions),
        )


if __name__ == "__main__":
    unittest.main()
