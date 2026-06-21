import unittest

from valiance.analysis import analyse, analyse_function
from valiance.asts import ElementNode, FunctionNode, FunctionParam, NumberLiteralNode
from valiance.types import (
    Environment,
    Fn,
    NoMatchingOverload,
    N,
    Overload,
    TypeStack,
    UnknownElement,
)


Number = N("Number")
String = N("String")


class AnalyserTests(unittest.TestCase):
    def test_default_environment_includes_builtin_plus(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode("+"),
            ],
        )

        self.assertEqual([node.typ for node in typed], [Number, Number, Number])

    def test_element_uses_environment_overloads_and_updates_stack(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode("+"),
            ],
            env,
        )

        self.assertEqual([node.typ for node in typed], [Number, Number, Number])

    def test_unknown_element_is_untyped(self):
        typed = analyse([ElementNode("missing")], Environment())
        self.assertIsNone(typed[0].typ)

    def test_environment_distinguishes_unknown_from_no_matching_overload(self):
        env = Environment()
        self.assertIsInstance(env.apply("missing", TypeStack()), UnknownElement)

        env.define_overload("+", Overload((Number, Number), (Number,)))
        self.assertIsInstance(env.apply("+", TypeStack((Number,))), NoMatchingOverload)

    def test_function_infers_missing_inputs(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(body=(ElementNode("+"),)), env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_function_uses_explicit_params(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))
        node = FunctionNode(
            params=(
                FunctionParam("x", Number),
                FunctionParam("y", Number),
            ),
            body=(ElementNode("+"),),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_function_return_annotation_must_match(self):
        env = Environment()
        node = FunctionNode(body=(NumberLiteralNode("1"),), returns=(String,))

        self.assertIsNone(analyse_function(node, env))

    def test_top_level_function_node_is_typed_and_pushed(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))
        node = FunctionNode(body=(ElementNode("+"),))

        typed = analyse([node], env)

        self.assertEqual(typed[0].typ, Fn((Number, Number), (Number,)))


if __name__ == "__main__":
    unittest.main()
