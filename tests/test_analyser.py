import unittest

from valiance.analysis import analyse
from valiance.asts import ElementNode, NumberLiteralNode
from valiance.types import (
    Environment,
    NoMatchingOverload,
    N,
    Overload,
    TypeStack,
    UnknownElement,
)


Number = N("Number")


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


if __name__ == "__main__":
    unittest.main()
