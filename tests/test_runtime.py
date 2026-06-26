import contextlib
import io
import unittest
from decimal import Decimal

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import CompileError, compile_program, run


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


if __name__ == "__main__":
    unittest.main()
