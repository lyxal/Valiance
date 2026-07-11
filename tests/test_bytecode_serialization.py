import os
import subprocess
import sys
import textwrap
import unittest
from decimal import Decimal

from valiance.runtime import BytecodeFormatError, RuntimeError, dumps, loads, run
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)


class BytecodeSerializationTests(unittest.TestCase):
    def test_boolean_constants_preserve_boolean_type(self):
        for value in (False, True):
            with self.subTest(value=value):
                program = Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, value),
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )

                decoded = loads(dumps(program))
                decoded_value = decoded.main.instructions[0].arg

                self.assertEqual(decoded_value, value)
                self.assertIs(type(decoded_value), bool)
                self.assertEqual(run(decoded), [value])

    def test_nested_boolean_instruction_arguments_preserve_boolean_type(self):
        argument = (("ascending", 0, False), ("descending", 1, True))
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, argument),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        decoded = loads(dumps(program))
        decoded_argument = decoded.main.instructions[0].arg

        self.assertEqual(decoded_argument, argument)
        self.assertIs(type(decoded_argument[0][2]), bool)
        self.assertIs(type(decoded_argument[1][2]), bool)

    def test_serializes_variant_parent_metadata(self):
        program = Program(
            FunctionCode((Instruction(OpCode.RETURN),), name="<main>"),
            (("ascending", "sorted"), ("descending", "sorted")),
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_rejects_malformed_variant_parent_metadata(self):
        malformed = (
            (("ascending", "sorted"), ("ascending", "ordered")),
            (("ascending", "ascending"),),
            (("ascending", "descending"), ("descending", "sorted")),
        )
        for tag_parents in malformed:
            with self.subTest(tag_parents=tag_parents):
                program = Program(
                    FunctionCode((Instruction(OpCode.RETURN),), name="<main>"),
                    tag_parents,
                )
                with self.assertRaises(BytecodeFormatError):
                    dumps(program)
                with self.assertRaises(RuntimeError):
                    run(program)

    def test_invalid_decimal_payload_raises_bytecode_format_error(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.PUSH_CONST, Decimal("42")),),
                name="<main>",
            )
        )
        data = dumps(program)
        corrupted = data.replace(b"42", b"x2", 1)

        with self.assertRaises(BytecodeFormatError):
            loads(corrupted)

    def test_every_truncation_is_reported_as_a_format_error(self):
        nested = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, Decimal("123.45")),
                Instruction(
                    OpCode.MAKE_FUNCTION,
                    FunctionCode(
                        (Instruction(OpCode.LOAD_VAR, "value"),),
                        params=("value",),
                        name="identity",
                    ),
                ),
                Instruction(OpCode.RETURN),
            ),
            name="<main>",
        )
        data = dumps(Program(nested))

        for end in range(len(data)):
            with self.subTest(end=end):
                with self.assertRaises(BytecodeFormatError):
                    loads(data[:end])

    def test_trailing_bytes_are_rejected(self):
        data = dumps(Program(FunctionCode((Instruction(OpCode.RETURN),))))

        with self.assertRaises(BytecodeFormatError):
            loads(data + b"\x00")

    def test_deeply_nested_values_fail_through_bytecode_format_error(self):
        value = None
        for _ in range(2_000):
            value = (value,)
        program = Program(
            FunctionCode((Instruction(OpCode.PUSH_CONST, value),), name="<main>")
        )

        with self.assertRaises(BytecodeFormatError):
            dumps(program)

    def test_deeply_nested_payloads_fail_through_bytecode_format_error(self):
        from tools.fuzzing import _nested_tuple_bytecode

        with self.assertRaises(BytecodeFormatError):
            loads(_nested_tuple_bytecode(2_000))

    def test_invalid_jump_targets_are_rejected_without_hanging(self):
        root = os.path.dirname(os.path.dirname(__file__))
        script = textwrap.dedent(
            """
            from valiance.runtime import RuntimeError, run
            from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program

            for target in (-1, 3):
                program = Program(
                    FunctionCode((Instruction(OpCode.JUMP, target),), name="<main>")
                )
                try:
                    run(program)
                except RuntimeError as exc:
                    if "invalid jump target" not in str(exc):
                        raise
                else:
                    raise AssertionError(f"jump target {target} was accepted")
            """
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(root, "src")

        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=2,
            env=env,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_malformed_decoded_instructions_raise_language_runtime_errors(self):
        instructions = (
            Instruction(OpCode.BUILD_LIST, "not-a-count"),
            Instruction(OpCode.MAKE_FUNCTION, "not-function-code"),
            Instruction(OpCode.CALL_RESOLVED_ELEMENT, None),
            Instruction(OpCode.SOURCE_ARGS, "not-an-arity"),
        )

        for instruction in instructions:
            with self.subTest(instruction=instruction):
                program = Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, 1),
                            instruction,
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )
                decoded = loads(dumps(program))

                with self.assertRaises(RuntimeError):
                    run(decoded)

    def test_serializes_byte_oriented_format_without_op_names(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, Decimal("42")),
                    Instruction(OpCode.PUSH_CONST, "answer"),
                    Instruction(OpCode.BUILD_STRING, ("value=", None)),
                    Instruction(OpCode.BUILD_TUPLE, 2),
                    Instruction(
                        OpCode.CALL_RESOLVED_ELEMENT,
                        ResolvedElementReference("+", 0),
                    ),
                    Instruction(OpCode.TRY_UNWRAP),
                    Instruction(OpCode.STACK_SHUFFLE, ("copy", ("x",), ("x",))),
                    Instruction(OpCode.SOURCE_ARGS, 1),
                    Instruction(OpCode.VALIDATE_TAG, ("#checked", 0)),
                    Instruction(OpCode.WRAP_ASSERT_ERROR),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        data = dumps(program)
        decoded = loads(data)

        self.assertTrue(data.startswith(b"VLNCBC\x14"))
        self.assertNotIn(b"push_const", data)
        self.assertNotIn(b"valiance-bytecode", data)
        self.assertEqual(decoded, program)

    def test_serializes_arbitrarily_large_decimal_constants(self):
        value = Decimal("99999999999999999999999999999")
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, value),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_nested_function_code(self):
        inner = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "x"),
                Instruction(OpCode.RETURN),
            ),
            params=("x",),
            name="id",
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, inner),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)


    def test_serializes_function_parameter_collection_ranks(self):
        function = FunctionCode(
            (Instruction(OpCode.RETURN),),
            params=("cells", "count"),
            param_collection_ranks=(1, 0),
        )

        self.assertEqual(loads(dumps(Program(function))), Program(function))

    def test_serializes_recursive_function_flag(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.RETURN),),
                name="loop",
                recursive=True,
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_function_set_code(self):
        overloads = FunctionSetCode(
            (
                FunctionCode((Instruction(OpCode.RETURN),), params=("x",)),
                FunctionCode((Instruction(OpCode.RETURN),), params=("x", "y")),
            )
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, overloads),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_vector_extension_references(self):
        identity = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "value"),
                Instruction(OpCode.RETURN),
            ),
            params=("value",),
        )
        extension = VectorExtensionReference(
            rules=(ExtensionRuleReference((True, False), identity),),
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(
                        OpCode.CALL_RESOLVED_ELEMENT,
                        ResolvedElementReference(
                            "+",
                            0,
                            vectorised=True,
                            vectorised_depths=(0, 1),
                            vectorised_target_ranks=(1, None),
                            extension=extension,
                        ),
                    ),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)


if __name__ == "__main__":
    unittest.main()
