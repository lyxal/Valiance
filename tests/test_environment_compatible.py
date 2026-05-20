import unittest

from valiance.Types import (
    ExactRankParameter,
    ListRankParameter,
    MinimumRankParameter,
    OptionalTypeParameter,
    UnionType,
)

from tests.support import build_environment, named, tag


class EnvironmentCompatibleHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.env = build_environment()

    def assert_compatible(self, source, target, expected: bool) -> None:
        self.assertEqual(
            self.env.compatible(source, target),
            expected,
            f"expected {source} compat {target} to be {expected}",
        )

    def test_compatible_requires_base_assignability(self) -> None:
        cases = [
            ("same type", named("Number"), named("Number"), True),
            ("subtype is compatible with supertype", named("Integer"), named("Number"), True),
            ("unrelated types are incompatible", named("String"), named("Number"), False),
            ("named type is compatible with union target", named("Integer"), UnionType([named("Number"), named("String")]), True),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_compatible(source, target, expected)

    def test_compatible_requires_target_tag_superset(self) -> None:
        serializable = tag("Serializable")
        immutable = tag("Immutable")

        cases = [
            (
                "target may add tags",
                named("Number", tags={serializable}),
                named("Number", tags={serializable, immutable}),
                True,
            ),
            (
                "target missing source tag",
                named("Number", tags={serializable, immutable}),
                named("Number", tags={serializable}),
                False,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_compatible(source, target, expected)

    def test_compatible_accepts_assignable_parameter_shapes(self) -> None:
        cases = [
            (
                "assignable parameter remains compatible",
                named("Number", parameters=[ExactRankParameter(3)]),
                named("Number", parameters=[MinimumRankParameter(2)]),
                True,
            ),
            (
                "trailing optional parameter remains compatible",
                named("Number", parameters=[ExactRankParameter(1)]),
                named("Number", parameters=[ExactRankParameter(1), OptionalTypeParameter(1)]),
                True,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_compatible(source, target, expected)

    def test_compatible_vectorization_parameter_rules(self) -> None:
        cases = [
            (
                "exact can vectorise into smaller exact",
                named("Number", parameters=[ExactRankParameter(3)]),
                named("Number", parameters=[ExactRankParameter(2)]),
                True,
            ),
            (
                "exact cannot vectorise into larger exact",
                named("Number", parameters=[ExactRankParameter(2)]),
                named("Number", parameters=[ExactRankParameter(3)]),
                False,
            ),
            (
                "minimum can potentially vectorise into exact",
                named("Number", parameters=[MinimumRankParameter(3)]),
                named("Number", parameters=[ExactRankParameter(2)]),
                True,
            ),
            (
                "list rank can vectorise into exact",
                named("Number", parameters=[ListRankParameter(3)]),
                named("Number", parameters=[ExactRankParameter(2)]),
                True,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_compatible(source, target, expected)

    def test_compatible_checks_all_parameters(self) -> None:
        source = named(
            "Number",
            parameters=[ExactRankParameter(3), OptionalTypeParameter(2)],
        )
        target = named(
            "Number",
            parameters=[ExactRankParameter(2), OptionalTypeParameter(1)],
        )

        self.assert_compatible(source, target, False)


if __name__ == "__main__":
    unittest.main()
