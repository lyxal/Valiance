import unittest

from valiance.asts import (
    BreakNode,
    DefineNode,
    ElementNode,
    ForNode,
    FunctionNode,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    NumberLiteralNode,
    SetVariableNode,
    SourceLocation,
    StringLiteralNode,
    Symbol,
    TagApplicationNode,
)
from valiance.parsing import LexError, ParseError, lex, parse, parse_type
from valiance.types import (
    C,
    DataTag,
    Fn,
    ListExactType,
    N,
    Number,
    String,
    Tagged,
    same,
)


class LexerTests(unittest.TestCase):
    def test_lexes_comments_strings_and_numbers(self):
        tokens = lex(
            '#? ignore\n#/ nested #/ deeper /# done /#\n"hi \\"there\\"" -1e-2 3i4'
        )
        values = [token.value for token in tokens]

        self.assertIn('hi "there"', values)
        self.assertIn("-1e-2", values)
        self.assertIn("3i4", values)

    def test_lexes_backslash_prefixed_element_name_as_one_token(self):
        tokens = lex("\\foo")

        self.assertEqual(tokens[0].value, "\\foo")

    def test_lexes_data_tags_as_one_token(self):
        tokens = lex("#sorted #!infinite #infinite++")

        self.assertEqual(
            [token.value for token in tokens[:-1]],
            ["#sorted", "#!infinite", "#infinite++"],
        )

    def test_unterminated_string_is_error(self):
        with self.assertRaises(LexError):
            lex('"missing')


class ParserTests(unittest.TestCase):
    def test_parses_basic_stack_chain_and_element_call_syntax(self):
        program = parse('println("hello")\n$answer = +(40, 2)')

        self.assertEqual(
            program,
            [
                StringLiteralNode("hello"),
                ElementNode(Symbol("println")),
                NumberLiteralNode("40"),
                NumberLiteralNode("2"),
                ElementNode(Symbol("+")),
                SetVariableNode(Symbol("answer")),
            ],
        )

    def test_lowers_infix_chain_to_stack_order(self):
        self.assertEqual(
            parse("1 + 2"),
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(Symbol("+")),
            ],
        )

    def test_lowers_multiple_chain_segments_left_to_right(self):
        self.assertEqual(
            parse("3 + 4 * 7"),
            [
                NumberLiteralNode("3"),
                NumberLiteralNode("4"),
                ElementNode(Symbol("+")),
                NumberLiteralNode("7"),
                ElementNode(Symbol("*")),
            ],
        )

    def test_preserves_stack_order_when_chain_is_already_stacky(self):
        self.assertEqual(
            parse("1 2 +"),
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(Symbol("+")),
            ],
        )

    def test_lowers_element_only_chain_right_to_left(self):
        program = parse("[1, 2, 3, 4, 5]\nprintln length")

        self.assertIsInstance(program[0], ListLiteralNode)
        self.assertEqual(
            program[1:],
            [
                ElementNode(Symbol("length")),
                ElementNode(Symbol("println")),
            ],
        )

    def test_parses_function_definition_with_params_and_returns(self):
        [node] = parse("define double(n: Number) -> Number => $n 2 *")

        self.assertIsInstance(node, DefineNode)
        self.assertEqual(node.name, Symbol("double"))
        self.assertEqual(node.function.params[0].name, Symbol("n"))
        self.assertTrue(same(node.function.params[0].typ, Number))
        self.assertEqual(node.function.returns, (Number,))
        self.assertEqual(node.function.body[-1], ElementNode(Symbol("*")))

    def test_parses_niladic_define_with_backslash_name(self):
        [node] = parse('define \\foo => println("Hello, World!")')

        self.assertIsInstance(node, DefineNode)
        self.assertEqual(node.name, Symbol("\\foo"))
        self.assertEqual(
            node.function.body,
            (
                StringLiteralNode("Hello, World!"),
                ElementNode(Symbol("println")),
            ),
        )

    def test_empty_define_params_are_syntax_error(self):
        with self.assertRaises(ParseError):
            parse("define foo() => 1")

    def test_empty_element_call_args_are_syntax_error(self):
        with self.assertRaises(ParseError):
            parse("foo()")

    def test_parses_value_level_tag_application(self):
        program = parse("[1, 2, 3] | #sorted")

        self.assertIsInstance(program[-1], TagApplicationNode)
        self.assertEqual(program[-1].tag, DataTag("sorted"))

    def test_inline_function_body_consumes_trailing_end(self):
        [node] = parse("fn => + | double end")

        self.assertIsInstance(node, FunctionNode)
        self.assertEqual(
            node.body,
            (
                ElementNode(Symbol("+")),
                ElementNode(Symbol("double")),
            ),
        )

    def test_parser_attaches_source_locations_to_ast_nodes(self):
        program = parse("\n  1 + 2")

        self.assertEqual(program[0].location, SourceLocation(2, 3, 3))
        self.assertEqual(program[1].location, SourceLocation(2, 7, 7))
        self.assertEqual(program[2].location, SourceLocation(2, 5, 5))

    def test_parses_multiline_if_else(self):
        [node] = parse(
            """
if ($n == 0) =>
  1
else =>
  $n 1 -
end
"""
        )

        self.assertIsInstance(node, IfNode)
        self.assertEqual(node.condition[0], GetVariableNode(Symbol("n")))
        self.assertEqual(node.then_branch, (NumberLiteralNode("1"),))
        self.assertEqual(node.else_branch[-1], ElementNode(Symbol("-")))

    def test_parses_function_literal_and_foreach_break(self):
        program = parse(
            """
fn (:Number) => +
$xs foreach (x, i) =>
  if ($x == 3) => break ($x, $i)
end
"""
        )

        self.assertIsInstance(program[0], FunctionNode)
        self.assertIsInstance(program[2], ForNode)
        loop = program[2]
        self.assertEqual(loop.variable, Symbol("x"))
        self.assertEqual(loop.index_variable, Symbol("i"))
        self.assertIsInstance(loop.body[0], IfNode)
        self.assertIsInstance(loop.body[0].then_branch[0], BreakNode)

    def test_parses_list_literal_as_item_expressions(self):
        [node] = parse("[1, +(2, 3), \"x\"]")

        self.assertIsInstance(node, ListLiteralNode)
        self.assertEqual(node.items[0], (NumberLiteralNode("1"),))
        self.assertEqual(node.items[1][-1], ElementNode(Symbol("+")))
        self.assertEqual(node.items[2], (StringLiteralNode("x"),))

    def test_parses_type_syntax(self):
        self.assertTrue(same(parse_type("Number+"), C(ListExactType, Number, 1)))
        self.assertTrue(
            same(
                parse_type("Function[Number, String -> Number]"),
                Fn((Number, String), (Number,)),
            )
        )
        self.assertTrue(
            same(
                parse_type("Result[Number, String]"),
                N(Symbol("Result"), Number, String),
            )
        )
        self.assertTrue(
            same(
                parse_type("#sorted Number+"),
                Tagged(C(ListExactType, Number), "sorted"),
            )
        )
        self.assertTrue(
            same(
                parse_type("#!infinite Number+"),
                Tagged(C(ListExactType, Number), DataTag("infinite", absent=True)),
            )
        )


if __name__ == "__main__":
    unittest.main()
