"""Differential tests for realistic optimisation workloads."""

from __future__ import annotations

import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import FunctionCode, OpCode, ResolvedElementReference
from valiance.runtime_values import RuntimeNumber

SAMPLE_DIRECTORY = Path(__file__).parents[1] / "samples" / "optimizations"


def _compile_sample(name: str):
    """Compile one checked-in workload with and without optimisation."""
    source = (SAMPLE_DIRECTORY / name).read_text(encoding="utf-8")
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(f"{name} did not analyse: {analyser.diagnostics}")
    return compile_program(typed, optimize=False), compile_program(typed)


class OptimizerProgramTests(unittest.TestCase):
    """Run non-trivial examples through every optimisation family."""

    def assert_equivalent(self, unoptimized, optimized, expected):
        """Require direct, optimised, and serialized execution to agree."""
        self.assertEqual(run(unoptimized), expected)
        self.assertEqual(run(optimized), expected)
        self.assertEqual(run(loads(dumps(optimized))), expected)

    def test_project_estimate_uses_constant_folding(self):
        unoptimized, optimized = _compile_sample("ProjectEstimate.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("825.00")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertIn(
            RuntimeNumber("510"),
            tuple(
                instruction.arg
                for instruction in optimized.main.instructions
                if instruction.op is OpCode.PUSH_CONST
            ),
        )

    def test_shipment_pricing_materializes_explicit_arguments(self):
        unoptimized, optimized = _compile_sample("ShipmentPricing.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("59.50")],
        )
        functions = [
            instruction.arg
            for instruction in optimized.main.instructions
            if instruction.op is OpCode.MAKE_FUNCTION
        ]
        self.assertEqual(len(functions), 2)
        for function in functions:
            self.assertIsInstance(function, FunctionCode)
            self.assertEqual(
                tuple(instruction.op for instruction in function.instructions[:2]),
                (OpCode.LOAD_VAR, OpCode.LOAD_VAR),
            )

    def test_subscription_forecast_inlines_small_constant_functions(self):
        unoptimized, optimized = _compile_sample("SubscriptionForecast.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("1797.00")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertFalse(
            any(
                instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and isinstance(instruction.arg, ResolvedElementReference)
                and instruction.arg.name in {"\\monthlyRate", "\\monthsPerYear"}
                for instruction in optimized.main.instructions
            )
        )

    def test_ledger_reorder_removes_inverse_stack_shuffles(self):
        unoptimized, optimized = _compile_sample("LedgerReorder.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("1540")],
        )
        self.assertTrue(
            any(
                instruction.op is OpCode.STACK_SHUFFLE
                for instruction in unoptimized.main.instructions
            )
        )
        self.assertFalse(
            any(
                instruction.op is OpCode.STACK_SHUFFLE
                for instruction in optimized.main.instructions
            )
        )

    def test_payroll_feature_flag_folds_branch_and_bytecode(self):
        unoptimized, optimized = _compile_sample("PayrollFeatureFlag.vlnc")

        self.assert_equivalent(
            unoptimized,
            optimized,
            [RuntimeNumber("950")],
        )
        self.assertLess(
            len(optimized.main.instructions),
            len(unoptimized.main.instructions),
        )
        self.assertFalse(
            any(
                instruction.op
                in {OpCode.JUMP, OpCode.JUMP_IF_FALSE, OpCode.JUMP_IF_MATCH}
                for instruction in optimized.main.instructions
            )
        )


if __name__ == "__main__":
    unittest.main()
