"""Tests for the extensible bytecode optimisation pipeline."""

from __future__ import annotations

import contextlib
import io
import unittest
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.main import main
from valiance.parsing import parse
from valiance.runtime import (
    FunctionOptimizationPass,
    OptimizationPipeline,
    compile_program,
    dumps,
    loads,
    optimize_program,
    run,
)
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    Instruction,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)


@dataclass
class _RecordingPass(FunctionOptimizationPass):
    seen: list[str | None]
    name: str = "recording"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        self.seen.append(function.name)
        return function


@dataclass
class _RenameProgramPass:
    name: str = "rename-main"

    def optimize(self, program: Program) -> Program:
        return Program(replace(program.main, name="optimized-main"))


class OptimizerTests(unittest.TestCase):
    def test_compile_optimizes_by_default_and_supports_opt_out(self):
        source = """
$value = fn -> Number =>
  return 1
  2
end
$value()
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        optimized = compile_program(typed)
        unoptimized = compile_program(typed, optimize=False)
        seen: list[str | None] = []
        custom = compile_program(
            typed,
            optimization_pipeline=OptimizationPipeline((_RecordingPass(seen),)),
        )
        optimized_function = optimized.main.instructions[0].arg
        unoptimized_function = unoptimized.main.instructions[0].arg

        self.assertIsInstance(optimized_function, FunctionCode)
        self.assertIsInstance(unoptimized_function, FunctionCode)
        self.assertEqual(len(optimized_function.instructions), 2)
        self.assertEqual(len(unoptimized_function.instructions), 4)
        custom_function = custom.main.instructions[0].arg
        self.assertIsInstance(custom_function, FunctionCode)
        self.assertEqual(len(custom_function.instructions), 4)
        self.assertIn("<main>", seen)
        self.assertEqual(run(optimized), [Decimal("1")])
        self.assertEqual(run(unoptimized), [Decimal("1")])
        self.assertEqual(run(loads(dumps(optimized))), [Decimal("1")])

    def test_control_flow_targets_are_rewritten_after_removal(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.TRY_BEGIN, ((None, 5),)),
                    Instruction(OpCode.PUSH_CONST, "boom"),
                    Instruction(OpCode.PANIC),
                    Instruction(OpCode.PUSH_CONST, "unreachable"),
                    Instruction(OpCode.JUMP, 5),
                    Instruction(OpCode.PUSH_CONST, "handled"),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )

        optimized = optimize_program(program)

        self.assertEqual(
            optimized.main.instructions,
            (
                Instruction(OpCode.TRY_BEGIN, ((None, 3),)),
                Instruction(OpCode.PUSH_CONST, "boom"),
                Instruction(OpCode.PANIC),
                Instruction(OpCode.PUSH_CONST, "handled"),
                Instruction(OpCode.RETURN),
            ),
        )
        self.assertEqual(run(optimized), ["handled"])


    def test_pipeline_accepts_whole_program_passes(self):
        program = Program(
            FunctionCode((Instruction(OpCode.RETURN),), name="main")
        )

        optimized = OptimizationPipeline((_RenameProgramPass(),)).optimize(program)

        self.assertEqual(optimized.main.name, "optimized-main")

    def test_custom_passes_visit_all_nested_function_payloads(self):
        leaf = lambda name: FunctionCode(  # noqa: E731
            (Instruction(OpCode.RETURN),),
            name=name,
        )
        extension = VectorExtensionReference(
            default=leaf("extension.default"),
            rules=(
                ExtensionRuleReference(
                    (True,),
                    leaf("extension.rule"),
                ),
            ),
            selector=leaf("extension.selector"),
        )
        constructor = ObjectConstructorReference(
            "Example",
            (),
            (),
            (),
            initializer=leaf("constructor"),
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(
                        OpCode.MAKE_FUNCTION,
                        FunctionSetCode((leaf("overload"),)),
                    ),
                    Instruction(OpCode.MAKE_OBJECT_CONSTRUCTOR, constructor),
                    Instruction(
                        OpCode.CALL_RESOLVED_ELEMENT,
                        ResolvedElementReference("example", 0, extension=extension),
                    ),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )
        seen: list[str | None] = []

        OptimizationPipeline((_RecordingPass(seen),)).optimize(program)

        self.assertEqual(
            seen,
            [
                "overload",
                "constructor",
                "extension.default",
                "extension.rule",
                "extension.selector",
                "main",
            ],
        )

    def test_cli_no_optimize_emits_unoptimized_bytecode(self):
        source = """
$value = fn -> Number =>
  return 1
  2
end
$value()
"""
        with TemporaryDirectory() as directory:
            optimized_path = Path(directory) / "optimized.vbc"
            unoptimized_path = Path(directory) / "unoptimized.vbc"
            output = io.StringIO()
            errors = io.StringIO()
            with (
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                optimized_exit = main(
                    [
                        "compile",
                        "--code",
                        source,
                        "--output",
                        str(optimized_path),
                    ]
                )
                unoptimized_exit = main(
                    [
                        "compile",
                        "--code",
                        source,
                        "--output",
                        str(unoptimized_path),
                        "--no-optimize",
                    ]
                )

            optimized = loads(optimized_path.read_bytes())
            unoptimized = loads(unoptimized_path.read_bytes())
            optimized_function = optimized.main.instructions[0].arg
            unoptimized_function = unoptimized.main.instructions[0].arg

        self.assertEqual(optimized_exit, 0)
        self.assertEqual(unoptimized_exit, 0)
        self.assertIsInstance(optimized_function, FunctionCode)
        self.assertIsInstance(unoptimized_function, FunctionCode)
        self.assertEqual(len(optimized_function.instructions), 2)
        self.assertEqual(len(unoptimized_function.instructions), 4)


if __name__ == "__main__":
    unittest.main()
