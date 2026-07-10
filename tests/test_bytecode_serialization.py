import unittest
from decimal import Decimal

from valiance.runtime import BytecodeFormatError, dumps, loads
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

        self.assertTrue(data.startswith(b"VLNCBC\x10"))
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
