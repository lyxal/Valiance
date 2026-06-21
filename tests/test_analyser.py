import unittest

from valiance.analysis import analyse, analyse_function, default_environment
from valiance.asts import (
    ElementNode,
    FunctionNode,
    FunctionParam,
    NumberLiteralNode,
    TypedFunctionNode,
)
from valiance.types import (
    AppliedElement,
    C,
    Environment,
    Fn,
    FunctionType,
    ListExactType,
    N,
    Never,
    NoMatchingOverload,
    ObjectAttribute,
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

    def test_default_environment_includes_generic_reduce_and_map(self):
        env = default_environment()
        reduce_result = env.apply(
            "/",
            TypeStack(
                (
                    C(ListExactType, Number),
                    Fn((Number, Number), (Number,)),
                )
            ),
        )
        self.assertIsInstance(reduce_result, AppliedElement)
        self.assertEqual(reduce_result.application.stack, TypeStack((Number,)))

        map_result = env.apply(
            "map",
            TypeStack(
                (
                    C(ListExactType, Number),
                    Fn((Number,), (String,)),
                )
            ),
        )
        self.assertIsInstance(map_result, AppliedElement)
        self.assertEqual(
            map_result.application.stack,
            TypeStack((C(ListExactType, String),)),
        )

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

    def test_environment_tracks_object_attributes(self):
        env = Environment()

        env.define_object(
            "Foo",
            (
                ObjectAttribute("bar", N("Bax")),
                ObjectAttribute("name", String),
            ),
        )

        self.assertTrue(env.object_exists("Foo"))
        self.assertFalse(env.object_exists("Missing"))
        self.assertEqual(env.lookup_attribute("Foo", "bar"), N("Bax"))
        self.assertTrue(env.has_attribute("Foo", "name"))
        self.assertFalse(env.has_attribute("Foo", "missing"))
        self.assertIsNone(env.lookup_attribute("Missing", "bar"))

    def test_object_attributes_cannot_be_declared_twice(self):
        env = Environment()

        with self.assertRaises(ValueError):
            env.define_object(
                "Foo",
                (
                    ObjectAttribute("bar", Number),
                    ObjectAttribute("bar", String),
                ),
            )

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

    def test_overloaded_function_node_keeps_typed_body_per_overload(self):
        typed = analyse([FunctionNode(body=(ElementNode("+"),))])
        function = typed[0]

        self.assertIsInstance(function, TypedFunctionNode)
        self.assertEqual(
            [overload.typ for overload in function.overloads],
            [
                Fn((Number, Number), (Number,)),
                Fn((String, String), (String,)),
            ],
        )
        self.assertEqual(
            [
                [body_node.typ for body_node in overload.body]
                for overload in function.overloads
            ],
            [[Number], [String]],
        )

    def test_overloaded_function_node_drops_never_returning_overloads(self):
        typed = analyse([FunctionNode(body=(ElementNode("+"), ElementNode("/")))])

        match typed[0].typ:
            case FunctionType(returns=returns):
                self.assertNotIn(Never(), returns)
            case overload_set:
                self.assertTrue(
                    all(
                        not any(ret == Never() for ret in overload.returns)
                        for overload in overload_set.overloads
                    )
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
