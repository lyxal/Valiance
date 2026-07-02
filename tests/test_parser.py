import unittest

from valiance.asts import (
    AssertNode,
    AtNode,
    BindingPatternNode,
    BreakNode,
    CastNode,
    DefineNode,
    ElementNode,
    EnumMemberNode,
    ForNode,
    FunctionNode,
    GetVariableNode,
    GuardPatternNode,
    IfNode,
    ImportComponent,
    ImportNode,
    ImportPath,
    ImportSpec,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    ListPatternNode,
    MatchNode,
    NumberLiteralNode,
    ObjectFieldNode,
    OrPatternNode,
    RestPatternNode,
    SetVariableNode,
    SourceLocation,
    StringInterpolationNode,
    StringLiteralNode,
    Symbol,
    TagApplicationNode,
    TraitRequirementNode,
    TryNode,
    TupleLiteralNode,
    TypePatternNode,
    UnfoldNode,
    VariantMemberNode,
    WhileNode,
    WildcardPatternNode,
)
from valiance.parsing import LexError, ParseError, lex, parse, parse_type
from valiance.types import (
    Atomic,
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

    def test_parses_string_identifier_interpolation(self):
        self.assertEqual(
            parse('"Hello, $name"'),
            [
                StringInterpolationNode(
                    ("Hello, ", (GetVariableNode(Symbol("name")),)),
                ),
            ],
        )

    def test_parses_string_expression_interpolation(self):
        self.assertEqual(
            parse('"Count is ${1 + 2}"'),
            [
                StringInterpolationNode(
                    (
                        "Count is ",
                        (
                            NumberLiteralNode("1"),
                            NumberLiteralNode("2"),
                            ElementNode(Symbol("+")),
                        ),
                    ),
                ),
            ],
        )
        self.assertEqual(
            parse('"Hello, ${name}"'),
            [
                StringInterpolationNode(
                    ("Hello, ", (GetVariableNode(Symbol("name")),)),
                ),
            ],
        )

    def test_escaped_dollar_stays_literal_in_string(self):
        self.assertEqual(
            parse('"Cost: \\$5"'),
            [StringLiteralNode("Cost: $5")],
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

    def test_parses_safe_and_checked_casts(self):
        self.assertEqual(
            parse("1 as Number"),
            [
                NumberLiteralNode("1"),
                CastNode(Number),
            ],
        )
        self.assertEqual(
            parse("value as! String"),
            [
                ElementNode(Symbol("value")),
                CastNode(String, checked=True),
            ],
        )

    def test_parses_empty_list_cast_as_literal_annotation(self):
        [node] = parse("[] as Number+")

        self.assertEqual(node, ListLiteralNode((), C(ListExactType, Number)))

    def test_parses_parentheses_as_grouping(self):
        self.assertEqual(
            parse("(1 + 2) * (3 + 4)"),
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(Symbol("+")),
                NumberLiteralNode("3"),
                NumberLiteralNode("4"),
                ElementNode(Symbol("+")),
                ElementNode(Symbol("*")),
            ],
        )

    def test_parses_braced_tuple_literal(self):
        [node] = parse("{1, 2, 3, 4}")

        self.assertEqual(
            node,
            TupleLiteralNode(
                (
                    (NumberLiteralNode("1"),),
                    (NumberLiteralNode("2"),),
                    (NumberLiteralNode("3"),),
                    (NumberLiteralNode("4"),),
                )
            ),
        )

    def test_parses_indexing_forms(self):
        program = parse("$data[2, 4, 1]\n[1, 2, 3] $[1]\n...$[3, 4]")

        self.assertEqual(program[0], GetVariableNode(Symbol("data")))
        self.assertEqual(program[4], IndexAccessNode(program[4].selectors))
        self.assertEqual(len(program[4].selectors), 3)
        self.assertIsInstance(program[5], ListLiteralNode)
        self.assertEqual(program[7], IndexAccessNode(program[7].selectors))
        self.assertTrue(program[-1].spread)

    def test_parses_index_augmented_assignment_as_copy_update(self):
        program = parse("$data[1] := + 3")

        self.assertEqual(program[0], GetVariableNode(Symbol("data")))
        self.assertIsInstance(program[2], IndexAccessNode)
        self.assertEqual(program[3], NumberLiteralNode("3"))
        self.assertEqual(program[4], ElementNode(Symbol("+")))
        self.assertEqual(program[5], GetVariableNode(Symbol("data")))
        self.assertIsInstance(program[7], IndexSetNode)
        self.assertEqual(program[8], SetVariableNode(Symbol("data")))

    def test_parses_colon_modifier_as_function_argument(self):
        program = parse("[1, 2, 3, 4] map: double")

        self.assertIsInstance(program[0], ListLiteralNode)
        self.assertEqual(program[1].name, Symbol("map"))
        self.assertEqual(
            program[1].modifier_args,
            (FunctionNode(body=(ElementNode(Symbol("double")),)),),
        )

    def test_parses_element_disambiguation_before_call_and_modifier_syntax(self):
        program = parse("+[Number+, _]([[1, 2]], [10, 20])\nmap[Number]: double")

        self.assertEqual(
            program[:3],
            [
                ListLiteralNode(
                    (
                        (
                            ListLiteralNode(
                                ((NumberLiteralNode("1"),), (NumberLiteralNode("2"),))
                            ),
                        ),
                    )
                ),
                ListLiteralNode(
                    ((NumberLiteralNode("10"),), (NumberLiteralNode("20"),))
                ),
                ElementNode(
                    Symbol("+"),
                    disambiguation=(C(ListExactType, Number), None),
                ),
            ],
        )
        self.assertEqual(
            program[3],
            ElementNode(
                Symbol("map"),
                (FunctionNode(body=(ElementNode(Symbol("double")),)),),
                (Number,),
            ),
        )

    def test_parses_parenthesized_colon_modifier_arguments(self):
        program = parse("fork: (sum, length) /")

        self.assertEqual(program[0].name, Symbol("fork"))
        self.assertEqual(
            program[0].modifier_args,
            (
                FunctionNode(body=(ElementNode(Symbol("sum")),)),
                FunctionNode(body=(ElementNode(Symbol("length")),)),
            ),
        )
        self.assertEqual(
            program[1:],
            [ElementNode(Symbol("/"))],
        )

    def test_parses_function_definition_with_params_and_returns(self):
        [node] = parse("define double(n: Number) -> Number => $n 2 *")

        self.assertIsInstance(node, DefineNode)
        self.assertEqual(node.name, Symbol("double"))
        self.assertEqual(node.function.params[0].name, Symbol("n"))
        self.assertTrue(same(node.function.params[0].typ, Number))
        self.assertEqual(node.function.returns, (Number,))
        self.assertEqual(node.function.body[-1], ElementNode(Symbol("*")))

    def test_parses_generic_function_definition_constraints(self):
        [node] = parse("define[T: Vehicle] keep(value: T) -> T => $value")

        self.assertIsInstance(node, DefineNode)
        self.assertEqual(node.generics, (Symbol("T"),))
        self.assertEqual(node.generic_variances, (None,))
        self.assertEqual(node.generic_constraints, (N(Symbol("Vehicle")),))
        self.assertEqual(node.function.params[0].typ, N(Symbol("T")))
        self.assertEqual(node.function.returns, (N(Symbol("T")),))

    def test_parses_atomic_generic_type_marker(self):
        [node] = parse("define find(needle: T atomic, haystack: T+) => $needle")

        self.assertEqual(node.function.params[0].typ, Atomic(N(Symbol("T"))))
        self.assertEqual(node.function.params[1].typ, C(ListExactType, N(Symbol("T"))))

    def test_parses_imports_with_namespace_alias_and_components(self):
        program = parse(
            """
public import {
  utils as u,
  math.[double, triple as t]
}
"""
        )

        self.assertEqual(
            program,
            [
                ImportNode(
                    (
                        ImportSpec(
                            ImportPath(("utils",)),
                            Symbol("u"),
                        ),
                        ImportSpec(
                            ImportPath(("math",)),
                            None,
                            (
                                ImportComponent(Symbol("double")),
                                ImportComponent(Symbol("triple"), Symbol("t")),
                            ),
                        ),
                    ),
                    True,
                )
            ],
        )

    def test_parses_namespace_qualified_element_names(self):
        self.assertEqual(
            parse("utils.double 4"),
            [
                NumberLiteralNode("4"),
                ElementNode(Symbol("utils.double")),
            ],
        )

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

    def test_parses_object_trait_variant_and_enum_declarations(self):
        [person] = parse(
            """
object Person =>
  $name: String
  public $age: Number = 0
  define label => $self.name
end
"""
        )

        self.assertEqual(person.name, Symbol("Person"))
        self.assertEqual(
            person.fields[:2],
            (
                ObjectFieldNode(Symbol("name"), String),
                ObjectFieldNode(
                    Symbol("age"),
                    Number,
                    (NumberLiteralNode("0"),),
                    Symbol("public"),
                ),
            ),
        )
        self.assertEqual(person.definitions[0].name, Symbol("label"))

        [box] = parse("object[T: any Vehicle] Box => $value: T end")
        self.assertEqual(box.generics, (Symbol("T"),))
        self.assertEqual(box.generic_variances, (Symbol("covariant"),))

        [shape] = parse("trait Shape => extend area -> Number end")
        self.assertEqual(
            shape.requirements,
            (TraitRequirementNode(Symbol("area"), returns=(Number,)),),
        )

        [option] = parse(
            """
variant Option =>
  Some =>
    $value: Number
  end
  None => end
end
"""
        )
        self.assertEqual(
            option.variants,
            (
                VariantMemberNode(
                    Symbol("Some"),
                    (ObjectFieldNode(Symbol("value"), Number),),
                ),
                VariantMemberNode(Symbol("None")),
            ),
        )

        [colour] = parse("enum Colour => RED GREEN BLUE end")
        self.assertEqual(
            colour.enum_members,
            (
                EnumMemberNode(Symbol("RED")),
                EnumMemberNode(Symbol("GREEN")),
                EnumMemberNode(Symbol("BLUE")),
            ),
        )

    def test_parses_object_friendly_qualified_element_name(self):
        self.assertEqual(parse("Foo::bar"), [ElementNode(Symbol("Foo::bar"))])

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

    def test_parses_missing_control_flow_structures(self):
        [node] = parse(
            """
if ($n == 0) => "zero"
else if ($n == 1) => "one"
else => "many"
end
"""
        )
        self.assertIsInstance(node, IfNode)
        self.assertIsInstance(node.else_branch[0], IfNode)

        [assert_node] = parse(
            """
assert =>
  true
else =>
  "nope"
end
"""
        )
        self.assertIsInstance(assert_node, AssertNode)
        self.assertEqual(assert_node.else_branch, (StringLiteralNode("nope"),))

        [while_node] = parse("while (> 0) -> (count: Number) => $count 1 - end")
        self.assertIsInstance(while_node, WhileNode)
        self.assertEqual(while_node.params[0].name, Symbol("count"))

        [unfold_node] = parse("unfold (< 5) -> (n: Number) => $n 1 + end")
        self.assertIsInstance(unfold_node, UnfoldNode)
        self.assertEqual(unfold_node.params[0].name, Symbol("n"))

        [at_node] = parse("at (list+, item) => + end")
        self.assertIsInstance(at_node, AtNode)
        self.assertEqual(at_node.levels[0].depth, 1)

        [try_node] = parse(
            """
try =>
  "boom" panic
handle String =>
  "typed"
handle =>
  "default"
end
"""
        )
        self.assertIsInstance(try_node, TryNode)
        self.assertEqual(len(try_node.handlers), 2)
        self.assertEqual(try_node.handlers[0].typ, N(Symbol("String")))
        self.assertIsNone(try_node.handlers[1].typ)

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

    def test_parses_match_type_and_default_cases(self):
        [node] = parse(
            """
match =>
  as :Colour.RED => "red"
  default => "other"
end
"""
        )

        self.assertIsInstance(node, MatchNode)
        self.assertEqual(node.cases[0].pattern_type, N(Symbol("Colour.RED")))
        self.assertFalse(node.cases[0].is_default)
        self.assertTrue(node.cases[1].is_default)
        self.assertEqual(node.cases[1].body, (StringLiteralNode("other"),))

    def test_parses_match_pattern_examples(self):
        [node] = parse(
            """
match =>
  10 => "ten"
  if > 5 => "big"
  _ => "small"
end
"""
        )
        self.assertEqual(len(node.cases), 3)
        self.assertIsInstance(node.cases[1].patterns[0], GuardPatternNode)
        self.assertIsInstance(node.cases[2].patterns[0], WildcardPatternNode)

        [node] = parse(
            """
match =>
  [1, _, 3] => "a"
  [1, $x = _, 3] => "b"
  [1, ..., 3] => "c"
  [1, ..., 3, $y = ..., 6] => "d"
end
"""
        )
        self.assertIsInstance(node.cases[0].patterns[0], ListPatternNode)
        self.assertIsInstance(node.cases[1].patterns[0].items[1], BindingPatternNode)
        self.assertIsInstance(node.cases[2].patterns[0].items[1], RestPatternNode)
        self.assertIsInstance(node.cases[3].patterns[0].items[3], BindingPatternNode)

        [node] = parse(
            """
match =>
  as x: OtherType => "named"
  as :Number if > 5 => "guarded"
  as :Obj(param, param) => "obj"
  as y => "default"
end
"""
        )
        self.assertIsInstance(node.cases[0].patterns[0], TypePatternNode)
        self.assertEqual(node.cases[0].patterns[0].name, Symbol("x"))
        self.assertTrue(node.cases[1].patterns[0].guard)
        self.assertEqual(len(node.cases[2].patterns[0].fields), 2)

        [node] = parse(
            """
match =>
  1, 2 => "stack"
  3 || 4, 5 || 6 => "alts"
  if > 10 || if < 4, [1, 2, 3] => "mixed"
  _, _ => "default"
end
"""
        )
        self.assertEqual(len(node.cases[0].patterns), 2)
        self.assertIsInstance(node.cases[1].patterns[0], OrPatternNode)
        self.assertIsInstance(node.cases[2].patterns[0], OrPatternNode)

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
