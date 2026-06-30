import contextlib
import io
import unittest
from builtins import RuntimeError as PythonRuntimeError
from decimal import Decimal
from itertools import count, islice
from pathlib import Path
from tempfile import TemporaryDirectory

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
from valiance.runtime_values import LazyList


def execute(source: str, source_file: Path | None = None):
    program = parse(source)
    analyser = Analyser(source_file=source_file)
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

    def test_vectorises_scalar_overloads_over_lazy_lists(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, count(Decimal("1"))),
                    Instruction(OpCode.PUSH_CONST, Decimal("10")),
                    Instruction(OpCode.LOAD_ELEMENT, "+"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        stack = run(program)

        self.assertEqual(len(stack), 1)
        self.assertIsInstance(stack[0], LazyList)
        self.assertEqual(
            list(islice(stack[0], 5)),
            [Decimal("11"), Decimal("12"), Decimal("13"), Decimal("14"), Decimal("15")],
        )

    def test_runtime_list_builtins_accept_lazy_lists_without_forcing_length(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, count(1)),
                    Instruction(OpCode.LOAD_ELEMENT, "head"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        self.assertEqual(run(program), [1])

    def test_runtime_length_rejects_lazy_lists(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, count(1)),
                    Instruction(OpCode.LOAD_ELEMENT, "length"),
                    Instruction(OpCode.CALL),
                ),
                name="<main>",
            )
        )

        with self.assertRaises(PythonRuntimeError) as error:
            run(program)

        self.assertIn("length requires a finite list", str(error.exception))

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

    def test_executes_imported_component_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute("import { math.[add_one] }\n41 add_one", main)

        self.assertEqual(stack, [Decimal("42")])

    def test_executes_imported_namespace_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            stack = execute("import { math }\n41 math.add_one", main)

        self.assertEqual(stack, [Decimal("42")])

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

    def test_executes_object_default_constructor_and_field_access(self):
        self.assertEqual(
            execute(
                """
object Person =>
  $name: String
  $age: Number
end
Person("Ada", 36) $.name
"""
            ),
            ["Ada"],
        )

    def test_executes_object_field_access_over_lists(self):
        self.assertEqual(
            execute(
                """
object Person =>
  $name: String
end
[Person("Ada"), Person("Grace")] $.name
"""
            ),
            [["Ada", "Grace"]],
        )

    def test_executes_public_object_field_write_as_reconstruction(self):
        self.assertEqual(
            execute(
                """
object Person =>
  public $name: String
end
Person("Ada")
$.name = "Grace"
$.name
"""
            ),
            ["Grace"],
        )

    def test_executes_enum_member_value_access(self):
        self.assertEqual(
            execute(
                """
enum[String] TokenType =>
  NUMBER = "Number"
end
TokenType.NUMBER.value
"""
            ),
            ["Number"],
        )

    def test_executes_match_on_enum_and_variant_members(self):
        self.assertEqual(
            execute(
                """
enum Colour => RED GREEN end
Colour.GREEN
match =>
  as :RED => "red"
  as :GREEN => "green"
end
"""
            ),
            ["green"],
        )
        self.assertEqual(
            execute(
                """
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(1)
match =>
  as :Some => "some"
  as :None => 0
end
"""
            ),
            ["some"],
        )

    def test_executes_match_literal_guard_and_wildcard_patterns(self):
        self.assertEqual(
            execute(
                """
10
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""
            ),
            ["The number was 10"],
        )
        self.assertEqual(
            execute(
                """
7
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""
            ),
            ["The number is bigger than 5"],
        )
        self.assertEqual(
            execute(
                """
2
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
"""
            ),
            ["Too small"],
        )

    def test_executes_match_list_patterns_with_bindings_and_rests(self):
        self.assertEqual(
            execute(
                """
[1, 99, 3]
match =>
  [1, _, 3] => "shape"
  _ => "no"
end
"""
            ),
            ["shape"],
        )
        self.assertEqual(
            execute(
                """
[1, 99, 3]
match =>
  [1, $x = _, 3] => $x
  _ => 0
end
"""
            ),
            [Decimal("99")],
        )
        self.assertEqual(
            execute(
                """
[1, 2, 3, 4, 6]
match =>
  [1, ..., 3, $y = ..., 6] => $y length
  _ => 0
end
"""
            ),
            [Decimal("1")],
        )

    def test_executes_match_type_guards_destructure_and_stack_patterns(self):
        self.assertEqual(
            execute(
                """
6
match =>
  as :Number if > 5 => "Type match with guard"
  as y => $y
end
"""
            ),
            ["Type match with guard"],
        )
        self.assertEqual(
            execute(
                """
object Pair =>
  $left: Number
  $right: Number
end
Pair(5, 5)
match =>
  as :Pair(param, param) => $param
  _ => 0
end
"""
            ),
            [Decimal("5")],
        )
        self.assertEqual(
            execute(
                """
2 1
match =>
  1, 2 => "Top of stack was 1 and then 2"
  _, _ => "default case"
end
"""
            ),
            ["Top of stack was 1 and then 2"],
        )
        self.assertEqual(
            execute(
                """
[1, 2, 3] 3
match =>
  if > 10 || if < 4, [1, 2, 3] => "mixed"
  _, _ => "default"
end
"""
            ),
            ["mixed"],
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
