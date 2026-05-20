import unittest

from valiance.Types import ExactRankParameter, MinimumRankParameter, NamedType, OptionalTypeParameter, make_symbol


def bare_named(name: str) -> NamedType:
    return NamedType(make_symbol(name), set(), [])


class TypeSimplificationHarness(unittest.TestCase):
    def test_exact_ranks_accumulate_into_single_exact_rank(self) -> None:
        number = bare_named("Number")
        number.add_parameter(ExactRankParameter(3))
        number.add_parameter(ExactRankParameter(1))

        self.assertEqual(number.parameters, [ExactRankParameter(4)])

    def test_exact_then_minimum_rank_accumulates_into_single_minimum_rank(self) -> None:
        number = bare_named("Number")
        number.add_parameter(ExactRankParameter(3))
        number.add_parameter(MinimumRankParameter(2))

        self.assertEqual(number.parameters, [MinimumRankParameter(5)])

    def test_minimum_then_exact_rank_accumulates_into_single_minimum_rank(self) -> None:
        number = bare_named("Number")
        number.add_parameter(MinimumRankParameter(2))
        number.add_parameter(ExactRankParameter(3))

        self.assertEqual(number.parameters, [MinimumRankParameter(5)])

    def test_optional_ranks_collapse_to_maximum_optional_rank(self) -> None:
        number = bare_named("Number")
        number.add_parameter(OptionalTypeParameter(1))
        number.add_parameter(OptionalTypeParameter(3))

        self.assertEqual(number.parameters, [OptionalTypeParameter(4)])

    def test_different_parameter_families_remain_distinct(self) -> None:
        number = bare_named("Number")
        number.add_parameter(ExactRankParameter(3))
        number.add_parameter(OptionalTypeParameter(2))

        self.assertEqual(number.parameters, [ExactRankParameter(3), OptionalTypeParameter(2)])


if __name__ == "__main__":
    unittest.main()
