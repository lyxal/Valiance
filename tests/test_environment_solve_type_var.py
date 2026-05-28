import unittest

from valiance.Types import (
    ExactRankParameter,
    ListRankParameter,
    OptionalTypeParameter,
    TypeVar,
    make_symbol,
)

from tests.support import build_environment, named


def type_var(name: str) -> TypeVar:
    return TypeVar(make_symbol(name))


class EnvironmentSolveTypeVarHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.env = build_environment()

    def assert_solved(self, parameter_type, input_type, expected) -> None:
        self.assertEqual(
            self.env.solve_type_var(parameter_type, input_type),
            expected,
            f"expected {parameter_type} to solve against {input_type} as {expected}",
        )

    def test_solves_a_simple_type_variable(self) -> None:
        self.assert_solved(
            type_var("T"),
            named("Number"),
            {make_symbol("T"): named("Number")},
        )

    def test_solves_type_variables_recursively_inside_generics(self) -> None:
        parameter_type = named(
            "Box",
            generics=[named("Pair", generics=[type_var("T"), type_var("U")])],
        )
        input_type = named(
            "Box",
            generics=[named("Pair", generics=[named("String"), named("Integer")])],
        )

        self.assert_solved(
            parameter_type,
            input_type,
            {
                make_symbol("T"): named("String"),
                make_symbol("U"): named("Integer"),
            },
        )

    def test_solves_parameter_deltas_without_reusing_input_parameters(self) -> None:
        parameter_type = type_var("T")
        parameter_type.add_parameter(ExactRankParameter(1))

        input_type = named("Number", parameters=[ExactRankParameter(4)])

        result = self.env.solve_type_var(parameter_type, input_type)

        self.assertIn(make_symbol("T"), result)
        solved = result[make_symbol("T")]
        self.assertEqual(solved.name, make_symbol("Number"))
        self.assertEqual(solved.parameters, [ExactRankParameter(3)])

    def test_solve_parameters_handles_rank_families(self) -> None:
        cases = [
            (
                "exact ranks",
                [ExactRankParameter(1)],
                [ExactRankParameter(4)],
                [ExactRankParameter(3)],
            ),
            (
                "list ranks",
                [ListRankParameter(2)],
                [ListRankParameter(5)],
                [ListRankParameter(3)],
            ),
            (
                "optional ranks",
                [OptionalTypeParameter(1)],
                [OptionalTypeParameter(4)],
                [OptionalTypeParameter(3)],
            ),
        ]

        for label, parameter_ranks, input_ranks, expected in cases:
            with self.subTest(label):
                self.assertEqual(
                    self.env.solve_parameters(parameter_ranks, input_ranks),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
