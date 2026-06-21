import unittest

from valiance.analysis import analyse, analyse_function
from valiance.asts import ElementNode, FunctionNode, FunctionParam, NumberLiteralNode
from valiance.types import (
    Environment,
    Fn,
    N,
    Never,
    NoMatchingOverload,
    Overload,
    Overloads,
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

    def test_no_matching_overload_applies_failed_stack_shape(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        result = env.apply("+", TypeStack((String, String)))

        self.assertIsInstance(result, NoMatchingOverload)
        self.assertEqual(result.stack, TypeStack((Never(),)))
        self.assertEqual(result.params, (Number, Number))
        self.assertEqual(result.actual_returns, (Never(),))

    def test_no_matching_overload_pops_expected_inputs_on_underflow(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        result = env.apply("+", TypeStack((Number,)))

        self.assertIsInstance(result, NoMatchingOverload)
        self.assertEqual(result.stack, TypeStack((Never(),)))

    def test_overload_sets_require_fixed_shape(self):
        env = Environment()
        env.define_overload("op", Overload((Number,), (Number,)))

        with self.assertRaises(ValueError):
            env.define_overload("op", Overload((Number, Number), (Number,)))

        with self.assertRaises(ValueError):
            env.define_overload("op", Overload((Number,), (Number, Number)))

    def test_function_infers_missing_inputs(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(body=(ElementNode("+"),)), env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_function_infers_overload_set_when_missing_inputs_are_ambiguous(self):
        typed = analyse([FunctionNode(body=(ElementNode("+"),))])

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Number, Number), (Number,)),
                Overload((String, String), (String,)),
            ),
        )

    def test_function_empty_params_do_not_infer_missing_inputs(self):
        env = Environment()
        env.define_overload("+", Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(params=(), body=(ElementNode("+"),)), env)

        self.assertEqual(typ, Fn((), (Never(),)))

    def test_function_empty_params_can_return_literal(self):
        typ = analyse_function(
            FunctionNode(params=(), body=(NumberLiteralNode("1"),)),
            Environment(),
        )

        self.assertEqual(typ, Fn((), (Number,)))

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
