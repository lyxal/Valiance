import unittest
from decimal import Decimal

from valiance.runtime import dumps, loads
from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
)


class BytecodeSerializationTests(unittest.TestCase):
    def test_serializes_byte_oriented_format_without_op_names(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, Decimal("42")),
                    Instruction(OpCode.PUSH_CONST, "answer"),
                    Instruction(OpCode.BUILD_STRING, ("value=", None)),
                    Instruction(OpCode.BUILD_TUPLE, 2),
                    Instruction(OpCode.CALL_RESOLVED_ELEMENT, ("+", 0, 0)),
                    Instruction(OpCode.TRY_UNWRAP),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        data = dumps(program)
        decoded = loads(data)

        self.assertTrue(data.startswith(b"VLNCBC\x06"))
        self.assertNotIn(b"push_const", data)
        self.assertNotIn(b"valiance-bytecode", data)
        self.assertEqual(decoded, program)

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


if __name__ == "__main__":
    unittest.main()
