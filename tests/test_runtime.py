import contextlib
import io
import unittest
from decimal import Decimal

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import CompileError, RuntimeError, compile_program, run
from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
)


def execute(source: str):
    program = parse(source)
    analyser = Analyser()
    typed = analyser.analyse(program)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed))


class RuntimeTests(unittest.TestCase):
    def test_executes_stack_arithmetic(self):
        self.assertEqual(execute("*(+(1, 2), 3)"), [Decimal("9")])

    def test_vectorises_scalar_overloads_over_lists(self):
        self.assertEqual(
            execute("[1, 2, 3] + [5, 6, 7]"),
            [[Decimal("6"), Decimal("8"), Decimal("10")]],
        )
        self.assertEqual(
            execute("[1, 2, 3] + 10"),
            [[Decimal("11"), Decimal("12"), Decimal("13")]],
        )

    def test_executes_element_with_colon_function_argument(self):
        self.assertEqual(
            execute("[1, 2, 3] map: double"),
            [[Decimal("2"), Decimal("4"), Decimal("6")]],
        )

    def test_compiler_emits_resolved_builtin_element_calls(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, ops)
        self.assertEqual(run(program), [Decimal("3")])

    def test_compiler_emits_resolved_user_defined_element_calls(self):
        source = """
define add_one(n: Number) -> Number => $n 1 +
41 add_one
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed)
        ops = tuple(instruction.op for instruction in program.main.instructions)

        self.assertIn(OpCode.CALL_RESOLVED_ELEMENT, ops)
        self.assertNotIn(OpCode.LOAD_ELEMENT, ops)
        self.assertEqual(run(program), [Decimal("42")])

    def test_compiler_emits_every_user_defined_overload_body(self):
        source = """
define same(x, y) => $x $y +
1 2 same
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])

        program = compile_program(typed)
        maker = program.main.instructions[0]
        self.assertEqual(maker.op, OpCode.MAKE_FUNCTION)
        self.assertIsInstance(maker.arg, FunctionSetCode)
        self.assertEqual(len(maker.arg.overloads), 2)
        self.assertEqual(run(program), [Decimal("3")])

    def test_repeated_defines_merge_user_defined_overloads(self):
        source = """
define triple(n: Number) -> Number => $n * 3
define triple(s: String) -> String => $s + $s + $s
triple 15
"""
        self.assertEqual(execute(source), [Decimal("45")])

        source = """
define triple(n: Number) -> Number => $n * 3
define triple(s: String) -> String => $s + $s + $s
triple "H"
"""
        self.assertEqual(execute(source), ["HHH"])

    def test_compiler_requires_typed_nodes(self):
        with self.assertRaises(CompileError):
            compile_program(parse("1"))

    def test_executes_variables_and_named_definitions(self):
        self.assertEqual(
            execute(
                """
define add_one(n: Number) -> Number => $n 1 +
$value = 41
$value add_one
"""
            ),
            [Decimal("42")],
        )

    def test_executes_list_tuple_record_and_dict_literals(self):
        self.assertEqual(execute("[1, 2, 3] length"), [Decimal("3")])
        self.assertEqual(execute('(1, "two")'), [(Decimal("1"), "two")])
        self.assertEqual(execute("record{x: 5}.x"), [Decimal("5")])
        self.assertEqual(execute('dict{"x": 7}'), [{"x": Decimal("7")}])

    def test_executes_conditionals_and_loops(self):
        self.assertEqual(execute("if (true) => 2 else => 3 end"), [Decimal("2")])
        self.assertEqual(
            execute(
                """
$n = 3
while ($n 0 >) =>
  $n = $n 1 -
end
$n
"""
            ),
            [Decimal("0")],
        )

    def test_println_writes_output_and_consumes_value(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            stack = execute('"hello" println')

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "hello\n")

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
            stack = run(compile_program(typed))

        self.assertEqual(stack, [])
        self.assertEqual(output.getvalue(), "15\n[3, 6, 9, 12, 15]\n")

    def test_runtime_element_errors_show_stack_and_attempted_inputs(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, "x"),
                    Instruction(OpCode.PUSH_CONST, Decimal("1")),
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
        self.assertIn("stack types: [String, Number]", message)
        self.assertIn("attempted input shapes:", message)
        self.assertIn("(Number, Number)", message)


if __name__ == "__main__":
    unittest.main()
