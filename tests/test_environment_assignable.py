import unittest

from valiance.Environment import Environment
from valiance.Types import (
    ExactRankParameter,
    IntersectionType,
    ListRankParameter,
    MinimumRankParameter,
    NamedType,
    OptionalTypeParameter,
    Tag,
    UnionType,
    make_symbol,
)


def symbol(name: str):
    return make_symbol(name)


def tag(name: str) -> Tag:
    return Tag(symbol(name))


def named(
    name: str,
    *,
    generics: list | None = None,
    parameters: list | None = None,
    tags: set | None = None,
) -> NamedType:
    return NamedType(
        symbol(name),
        tags or set(),
        generics or [],
        parameters or [],
    )


class EnvironmentAssignableHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.env = Environment()
        self.env.add_trait(symbol("Number"), [symbol("Comparable"), symbol("Printable")])
        self.env.add_trait(symbol("Integer"), [symbol("Number"), symbol("Comparable"), symbol("Printable")])
        self.env.add_trait(symbol("String"), [symbol("Comparable"), symbol("Printable")])
        self.env.add_trait(symbol("Box"), [])
        self.env.add_trait(symbol("ReadonlyBox"), [])

    def assert_assignable(self, source, target, expected: bool) -> None:
        self.assertEqual(
            self.env.assignable(source, target),
            expected,
            f"expected {source} -> {target} to be {expected}",
        )

    def test_named_type_assignability_cases(self) -> None:
        cases = [
            ("exact same type", named("Number"), named("Number"), True),
            ("implemented trait", named("Integer"), named("Number"), True),
            ("transitive traits are not inferred", named("Integer"), named("Printable"), True),
            ("unrelated named type", named("String"), named("Number"), False),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)

    def test_tags_must_be_superset_of_target_tags(self) -> None:
        serializable = tag("Serializable")
        immutable = tag("Immutable")

        cases = [
            (
                "all target tags present on source",
                named("Number", tags={serializable, immutable}),
                named("Number", tags={serializable}),
                True,
            ),
            (
                "source missing target tag",
                named("Number", tags={serializable}),
                named("Number", tags={serializable, immutable}),
                False,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)

    def test_generic_named_types_use_recursive_assignability(self) -> None:
        cases = [
            (
                "same generic type",
                named("Box", generics=[named("Integer")]),
                named("Box", generics=[named("Number")]),
                True,
            ),
            (
                "different generic family",
                named("Box", generics=[named("Integer")]),
                named("ReadonlyBox", generics=[named("Number")]),
                False,
            ),
            (
                "generic argument must be assignable",
                named("Box", generics=[named("String")]),
                named("Box", generics=[named("Number")]),
                False,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)

    def test_parameter_assignability_cases(self) -> None:
        cases = [
            (
                "exact rank to smaller minimum rank",
                named("Number", parameters=[ExactRankParameter(3)]),
                named("Number", parameters=[MinimumRankParameter(2)]),
                True,
            ),
            (
                "minimum rank too small",
                named("Number", parameters=[MinimumRankParameter(1)]),
                named("Number", parameters=[MinimumRankParameter(2)]),
                False,
            ),
            (
                "list rank to list rank",
                named("Number", parameters=[ListRankParameter(4)]),
                named("Number", parameters=[ListRankParameter(2)]),
                True,
            ),
            (
                "optional trailing parameter may be omitted",
                named("Number", parameters=[ExactRankParameter(1)]),
                named("Number", parameters=[ExactRankParameter(1), OptionalTypeParameter(1)]),
                True,
            ),
            (
                "optional parameter is not assignable to a stricter optional parameter",
                named("Number", parameters=[OptionalTypeParameter(2)]),
                named("Number", parameters=[OptionalTypeParameter(1)]),
                False,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)

    def test_named_type_against_union_and_intersection(self) -> None:
        number_or_string = UnionType([named("Number"), named("String")])
        comparable_and_printable = IntersectionType([named("Comparable"), named("Printable")])

        cases = [
            ("named into union", named("Integer"), number_or_string, True),
            ("named into intersection", named("Integer"), comparable_and_printable, True),
            ("named fails intersection", named("Number"), IntersectionType([named("Comparable"), named("Missing")]), False),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)

    def test_union_and_intersection_cases(self) -> None:
        cases = [
            (
                "intersection assignable to one of its members",
                IntersectionType([named("Number"), named("Printable")]),
                named("Printable"),
                True,
            ),
            (
                "intersection assignable to compatible intersection",
                IntersectionType([named("Number"), named("Printable")]),
                IntersectionType([named("Printable")]),
                True,
            ),
            (
                "union members all fit wider union",
                UnionType([named("Integer"), named("String")]),
                UnionType([named("Number"), named("String")]),
                True,
            ),
            (
                "union members may map to different target branches",
                UnionType([named("Integer"), named("String")]),
                UnionType([named("Number"), named("Printable")]),
                True,
            ),
        ]

        for label, source, target, expected in cases:
            with self.subTest(label):
                self.assert_assignable(source, target, expected)


if __name__ == "__main__":
    unittest.main()
