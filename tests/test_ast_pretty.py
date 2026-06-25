import unittest

from valiance.asts import (
    FieldAccessNode,
    FunctionNode,
    NumberLiteralNode,
    TypedNode,
    pretty_ast,
)
from valiance.symbols import Symbol
from valiance.types import Number

FOO = Symbol("foo")


class AstPrettyTests(unittest.TestCase):
    def test_pretty_ast_formats_program_over_multiple_lines(self):
        program = [
            FunctionNode(
                params=None,
                body=(FieldAccessNode(FOO), NumberLiteralNode("1")),
                returns=None,
            )
        ]

        rendered = pretty_ast(program)

        self.assertIn("FunctionNode(", rendered)
        self.assertIn("params=infer", rendered)
        self.assertIn("FieldAccessNode(name=foo)", rendered)
        self.assertGreater(rendered.count("\n"), 4)

    def test_pretty_ast_formats_typed_nodes(self):
        rendered = pretty_ast([TypedNode(NumberLiteralNode("1"), Number)])

        self.assertIn("TypedNode(type=Number", rendered)
        self.assertIn("NumberLiteralNode(value='1')", rendered)


if __name__ == "__main__":
    unittest.main()
