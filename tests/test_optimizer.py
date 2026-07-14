"""Tests for the extensible bytecode optimisation pipeline."""

from __future__ import annotations

import contextlib
import io
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.main import main
from valiance.parsing import parse
from valiance.runtime import (
    BytecodePeepholeOptimizationPass,
    ConstantFoldingOptimizationPass,
    ExplicitArgumentOptimizationPass,
    FunctionOptimizationPass,
    OptimizationPipeline,
    SmallFunctionInliningPass,
    StackShuffleOptimizationPass,
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
from valiance.runtime.runtime_values import RuntimeNumber


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
        self.assertEqual(run(optimized), [RuntimeNumber("1")])
        self.assertEqual(run(unoptimized), [RuntimeNumber("1")])
        self.assertEqual(run(loads(dumps(optimized))), [RuntimeNumber("1")])

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
        program = Program(FunctionCode((Instruction(OpCode.RETURN),), name="main"))

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

    def test_explicit_argument_pass_materializes_scalar_cycle_inputs(self):
        source = """
define add(left: Number, right: Number) -> Number => + end
3 4 add
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        unoptimized = compile_program(typed, optimize=False)
        optimized = compile_program(
            typed,
            optimization_pipeline=OptimizationPipeline(
                (ExplicitArgumentOptimizationPass(),)
            ),
        )
        original = unoptimized.main.instructions[0].arg
        rewritten = optimized.main.instructions[0].arg

        self.assertIsInstance(original, FunctionCode)
        self.assertIsInstance(rewritten, FunctionCode)
        self.assertEqual(original.instructions[0].op, OpCode.CALL_RESOLVED_ELEMENT)
        self.assertEqual(
            rewritten.instructions[:3],
            (
                Instruction(OpCode.LOAD_VAR, "left"),
                Instruction(OpCode.LOAD_VAR, "right"),
                original.instructions[0],
            ),
        )
        self.assertEqual(run(optimized), run(unoptimized))
        self.assertEqual(run(loads(dumps(optimized))), [RuntimeNumber("7")])

    def test_explicit_argument_pass_leaves_nested_cycle_scopes_implicit(self):
        reference = ResolvedElementReference("+", 1)
        function = FunctionCode(
            (
                Instruction(OpCode.CYCLE_BEGIN, (None, 0)),
                Instruction(OpCode.CALL_RESOLVED_ELEMENT, reference),
                Instruction(OpCode.CYCLE_END),
                Instruction(OpCode.RETURN),
            ),
            params=("value",),
            cycle_params=True,
            dispatch_types=("Number",),
            param_collection_ranks=(0,),
        )

        optimized = OptimizationPipeline(
            (ExplicitArgumentOptimizationPass(),)
        ).optimize(Program(function))

        self.assertEqual(optimized.main, function)

    def test_explicit_argument_pass_leaves_non_scalar_parameters_implicit(self):
        reference = ResolvedElementReference("==", 0)
        function = FunctionCode(
            (
                Instruction(OpCode.CALL_RESOLVED_ELEMENT, reference),
                Instruction(OpCode.RETURN),
            ),
            params=("left", "right"),
            cycle_params=True,
            dispatch_types=("List[Number]", "List[Number]"),
            param_collection_ranks=(1, 1),
        )

        optimized = OptimizationPipeline(
            (ExplicitArgumentOptimizationPass(),)
        ).optimize(Program(function))

        self.assertEqual(optimized.main, function)

    def test_constant_folding_reduces_pure_resolved_calls(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("2 3 + 4 *"))
        self.assertEqual(analyser.diagnostics, [])

        optimized = compile_program(
            typed,
            optimization_pipeline=OptimizationPipeline(
                (ConstantFoldingOptimizationPass(),)
            ),
        )

        self.assertEqual(
            optimized.main.instructions,
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber("14")),
                Instruction(OpCode.RETURN),
            ),
        )
        self.assertEqual(run(loads(dumps(optimized))), [RuntimeNumber("14")])

    def test_constant_folding_collapses_literal_tuple_and_string_builders(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("1")),
                    Instruction(OpCode.PUSH_CONST, "two"),
                    Instruction(OpCode.BUILD_TUPLE, 2),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("3")),
                    Instruction(OpCode.BUILD_STRING, ("total=", None)),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )

        optimized = OptimizationPipeline((ConstantFoldingOptimizationPass(),)).optimize(
            program
        )

        self.assertEqual(
            optimized.main.instructions,
            (
                Instruction(
                    OpCode.PUSH_CONST,
                    (RuntimeNumber("1"), "two"),
                ),
                Instruction(OpCode.PUSH_CONST, "total=3"),
                Instruction(OpCode.RETURN),
            ),
        )
        self.assertEqual(run(optimized), run(program))
        self.assertEqual(run(loads(dumps(optimized))), run(program))

    def test_small_function_inlining_replaces_constant_nilad_call(self):
        source = r"""
define \rate -> Number => 0.2 end
100 \rate *
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        unoptimized = compile_program(typed, optimize=False)
        optimized = compile_program(
            typed,
            optimization_pipeline=OptimizationPipeline(
                (SmallFunctionInliningPass(max_bytecode_size=4),)
            ),
        )

        self.assertTrue(
            any(
                instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and instruction.arg.name == "\\rate"
                for instruction in unoptimized.main.instructions
            )
        )
        self.assertFalse(
            any(
                instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and instruction.arg.name == "\\rate"
                for instruction in optimized.main.instructions
            )
        )
        self.assertIn(
            Instruction(OpCode.PUSH_CONST, RuntimeNumber("0.2")),
            optimized.main.instructions,
        )
        self.assertEqual(run(optimized), run(unoptimized))
        self.assertEqual(run(loads(dumps(optimized))), [RuntimeNumber("20.0")])

    def test_small_function_inlining_obeys_bytecode_size_threshold(self):
        source = r"""
define \rate -> Number => 0.2 end
100 \rate *
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        unoptimized = compile_program(typed, optimize=False)

        optimized = compile_program(
            typed,
            optimization_pipeline=OptimizationPipeline(
                (SmallFunctionInliningPass(max_bytecode_size=1),)
            ),
        )

        self.assertTrue(
            any(
                instruction.op is OpCode.CALL_RESOLVED_ELEMENT
                and instruction.arg.name == "\\rate"
                for instruction in optimized.main.instructions
            )
        )
        self.assertEqual(run(optimized), run(unoptimized))

    def test_bytecode_peepholes_remove_dead_push_and_fold_constant_branch(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("99")),
                    Instruction(OpCode.POP),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("0")),
                    Instruction(OpCode.JUMP_IF_FALSE, 6),
                    Instruction(OpCode.PUSH_CONST, "wrong"),
                    Instruction(OpCode.JUMP, 7),
                    Instruction(OpCode.PUSH_CONST, "right"),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )

        optimized = OptimizationPipeline(
            (BytecodePeepholeOptimizationPass(),)
        ).optimize(program)

        self.assertEqual(optimized.main.instructions[0], Instruction(OpCode.JUMP, 3))
        self.assertNotIn(
            Instruction(OpCode.PUSH_CONST, RuntimeNumber("99")),
            optimized.main.instructions,
        )
        self.assertEqual(run(optimized), ["right"])
        self.assertEqual(run(loads(dumps(optimized))), ["right"])

    def test_default_pipeline_folds_tagged_constant_condition(self):
        source = """
if (1 == 1) =>
  10
else =>
  20
end
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        unoptimized = compile_program(typed, optimize=False)
        optimized = compile_program(typed)

        self.assertEqual(
            optimized.main.instructions,
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber("10")),
                Instruction(OpCode.RETURN),
            ),
        )
        self.assertEqual(run(optimized), run(unoptimized))
        self.assertEqual(run(loads(dumps(optimized))), [RuntimeNumber("10")])

    def test_constant_folding_respects_rebound_builtin_names(self):
        reference = ResolvedElementReference("+", 0)
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("5")),
                    Instruction(OpCode.STORE_VAR, "+"),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("2")),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("3")),
                    Instruction(OpCode.CALL_RESOLVED_ELEMENT, reference),
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )

        optimized = OptimizationPipeline((ConstantFoldingOptimizationPass(),)).optimize(
            program
        )

        self.assertEqual(optimized, program)

    def test_stack_shuffle_pass_composes_inverse_permutations(self):
        swap = Instruction(
            OpCode.STACK_SHUFFLE,
            ("move", ("lower", "upper"), ("upper", "lower")),
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("10")),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("20")),
                    swap,
                    swap,
                    Instruction(OpCode.RETURN),
                ),
                name="main",
            )
        )

        optimized = OptimizationPipeline((StackShuffleOptimizationPass(),)).optimize(
            program
        )

        self.assertFalse(
            any(
                instruction.op is OpCode.STACK_SHUFFLE
                for instruction in optimized.main.instructions
            )
        )
        self.assertEqual(run(optimized), [RuntimeNumber("10"), RuntimeNumber("20")])
        self.assertEqual(run(loads(dumps(optimized))), run(program))

    def test_stack_shuffle_does_not_remove_cycle_backed_identity(self):
        identity = Instruction(
            OpCode.STACK_SHUFFLE,
            ("move", ("input",), ("input",)),
        )
        function = FunctionCode(
            (identity, Instruction(OpCode.RETURN)),
            params=("input",),
            cycle_params=True,
        )

        optimized = OptimizationPipeline((StackShuffleOptimizationPass(),)).optimize(
            Program(function)
        )

        self.assertEqual(optimized.main.instructions[0].op, OpCode.STACK_SHUFFLE)
        self.assertEqual(
            optimized.main.instructions[0].arg,
            ("move", ("0",), ("0",)),
        )

    def test_stack_shuffle_pass_canonicalizes_remaining_labels(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("10")),
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("20")),
                    Instruction(
                        OpCode.STACK_SHUFFLE,
                        (
                            "move",
                            ("very_long_lower", "very_long_upper"),
                            ("very_long_upper", "very_long_lower"),
                        ),
                    ),
                    Instruction(OpCode.RETURN),
                )
            )
        )

        optimized = OptimizationPipeline((StackShuffleOptimizationPass(),)).optimize(
            program
        )

        self.assertEqual(
            optimized.main.instructions[2],
            Instruction(OpCode.STACK_SHUFFLE, ("move", ("0", "1"), ("1", "0"))),
        )
        self.assertEqual(run(optimized), run(program))

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
