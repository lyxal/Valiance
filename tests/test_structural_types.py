import unittest

from valiance.parsing import parse_type
from valiance.symbols import Symbol
from valiance.types import (
    AnonymousTrait,
    AnonymousTraitRequirement,
    Context,
    ExactList,
    Field,
    Fn,
    GenericConstraint,
    I,
    Integer,
    N,
    Number,
    Overload,
    Real,
    Row,
    String,
    U,
    V,
    Variance,
    _combine_all,
    _solve,
    _substitute,
    apply_overload,
    assignable,
    compatible,
    merge_types,
    optional,
    same,
    show,
    subtype,
)

FOO = Symbol("Foo")
BAR = Symbol("Bar")
VEHICLE = Symbol("Vehicle")
CAR = Symbol("Car")
BOX = Symbol("Box")
SINK = Symbol("Sink")
CELL = Symbol("Cell")
FIRST = Symbol("first")
SECOND = Symbol("second")
VALUE = Symbol("value")
NAME = Symbol("name")
AGE = Symbol("age")
READ = Symbol("read")
WRITE = Symbol("write")
MAP = Symbol("map")

Foo = N(FOO)
Bar = N(BAR)
Vehicle = N(VEHICLE)
Car = N(CAR)


def requirement(name: Symbol, params: tuple, returns: tuple):
    return AnonymousTraitRequirement(name, Overload(params, returns))


def unary_trait(
    subject: str = "T",
    result: str = "U",
    *,
    read_name: Symbol = READ,
):
    return AnonymousTrait(
        (Symbol(subject), Symbol(result)),
        (requirement(read_name, (V(subject),), (V(result),)),),
    )


class RowPolymorphismTests(unittest.TestCase):
    def test_row_width_and_depth_subtyping_compose(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        source = Row(Foo, Field(NAME, Car), Field(AGE, Number))
        target = Row(Foo, Field(NAME, Vehicle))

        self.assertTrue(subtype(source, target, ctx))
        self.assertTrue(assignable(source, target, ctx))
        self.assertTrue(compatible(source, target, ctx))
        self.assertFalse(assignable(target, source, ctx))

    def test_row_field_order_is_canonical(self):
        left = Row(Foo, Field(NAME, String), Field(AGE, Number))
        right = Row(Foo, Field(AGE, Number), Field(NAME, String))

        self.assertTrue(same(left, right))
        self.assertEqual(left, right)

    def test_duplicate_row_fields_merge_without_order_dependence(self):
        left = Row(Foo, Field(NAME, String), Field(NAME, Number))
        right = Row(Foo, Field(NAME, U(Number, String)))

        self.assertTrue(same(left, right))

    def test_row_generic_compatibility_is_strictly_broader_than_assignment(self):
        pattern = Row(V("@1"), Field(NAME, V("@2")))
        actual = Row(Foo, Field(NAME, String), Field(AGE, Number))

        self.assertFalse(assignable(actual, pattern))
        self.assertTrue(compatible(actual, pattern))

    def test_named_and_anonymous_row_generics_solve_independently(self):
        pattern = Row(V("@1"), Field(NAME, V("T")))
        actual = Row(Foo, Field(NAME, String))

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["@1"]), Foo)
        self.assertEqual(_combine_all(constraints["T"]), String)
        self.assertTrue(
            same(
                _substitute(pattern, {"@1": Foo, "T": String}),
                actual,
            )
        )

    def test_shared_row_generic_combines_evidence_from_multiple_fields(self):
        pattern = Row(
            V("@1"),
            Field(FIRST, V("@2")),
            Field(SECOND, V("@2")),
        )
        actual = Row(
            Foo,
            Field(FIRST, Integer),
            Field(SECOND, Number),
        )

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        solution = _combine_all(constraints["@2"])
        self.assertEqual(solution, Number)
        self.assertTrue(assignable(Integer, solution))
        self.assertTrue(assignable(Number, solution))

    def test_generic_row_solving_accepts_concrete_depth_subtypes(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        pattern = Row(V("T"), Field(VALUE, Number))
        actual = Row(Car, Field(VALUE, Integer))

        constraints = _solve(pattern, actual, ctx)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["T"]), Car)

    def test_conflicting_shared_row_generic_rejects_overload_application(self):
        pattern = Row(
            V("@1"),
            Field(FIRST, V("@2")),
            Field(SECOND, V("@2")),
        )
        actual = Row(
            Foo,
            Field(FIRST, String),
            Field(SECOND, Number),
        )
        overload = Overload((pattern,), (V("@2"),))

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertIsNone(_combine_all(constraints["@2"]))
        self.assertIsNone(apply_overload(overload, (actual,)))

    def test_row_fields_support_union_optional_and_intersection_targets(self):
        source = Row(Foo, Field(VALUE, Integer))

        self.assertTrue(
            assignable(source, Row(Foo, Field(VALUE, U(Number, String))))
        )
        self.assertTrue(
            assignable(source, Row(Foo, Field(VALUE, optional(Number))))
        )
        self.assertTrue(
            assignable(source, Row(Foo, Field(VALUE, I(Real, Number))))
        )

    def test_row_branch_merging_uses_common_structural_supertype(self):
        narrow = Row(Foo, Field(NAME, String), Field(AGE, Integer))
        broad = Row(Foo, Field(NAME, String))
        incompatible = Row(Foo, Field(NAME, Number))

        self.assertTrue(same(merge_types(narrow, broad), broad))
        merged = merge_types(broad, incompatible)
        self.assertTrue(assignable(broad, merged))
        self.assertTrue(assignable(incompatible, merged))

    def test_row_pattern_alpha_renaming_preserves_solutions(self):
        left = Row(V("@1"), Field(NAME, V("@2")))
        right = Row(V("@10"), Field(NAME, V("@11")))
        actual = Row(Foo, Field(NAME, String), Field(AGE, Number))

        left_solution = _solve(left, actual)
        right_solution = _solve(right, actual)

        self.assertIsNotNone(left_solution)
        self.assertIsNotNone(right_solution)
        self.assertEqual(_combine_all(left_solution["@1"]), Foo)
        self.assertEqual(_combine_all(right_solution["@10"]), Foo)
        self.assertEqual(_combine_all(left_solution["@2"]), String)
        self.assertEqual(_combine_all(right_solution["@11"]), String)

    def test_rows_compose_with_covariant_nominal_generics(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        ctx.set_generic_variance(BOX, (Variance.COVARIANT,))
        source = Row(Foo, Field(NAME, Car), Field(AGE, Number))
        target = Row(Foo, Field(NAME, Vehicle))

        self.assertTrue(assignable(N(BOX, source), N(BOX, target), ctx))
        self.assertFalse(assignable(N(BOX, target), N(BOX, source), ctx))

    def test_rows_follow_contravariant_and_invariant_generic_positions(self):
        ctx = Context()
        ctx.set_generic_variance(SINK, (Variance.CONTRAVARIANT,))
        ctx.set_generic_variance(CELL, (Variance.INVARIANT,))
        source = Row(Foo, Field(NAME, String), Field(AGE, Number))
        target = Row(Foo, Field(NAME, String))

        self.assertTrue(assignable(N(SINK, target), N(SINK, source), ctx))
        self.assertTrue(compatible(N(SINK, target), N(SINK, source), ctx))
        self.assertFalse(assignable(N(SINK, source), N(SINK, target), ctx))
        self.assertFalse(assignable(N(CELL, source), N(CELL, target), ctx))
        self.assertFalse(assignable(N(CELL, target), N(CELL, source), ctx))

    def test_rows_compose_with_collection_covariance(self):
        source = Row(Foo, Field(NAME, String), Field(AGE, Number))
        target = Row(Foo, Field(NAME, String))

        self.assertTrue(assignable(ExactList(source), ExactList(target)))
        self.assertFalse(assignable(ExactList(target), ExactList(source)))

    def test_row_fields_can_require_anonymous_traits(self):
        trait = unary_trait()
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))
        source = Row(Bar, Field(VALUE, Foo))
        target = Row(Bar, Field(VALUE, trait))

        self.assertTrue(assignable(source, target, ctx))
        self.assertTrue(compatible(source, target, ctx))

    def test_row_fields_can_contain_generic_function_types(self):
        pattern = Row(
            Foo,
            Field(VALUE, Fn((V("@1"),), (V("@2"),))),
        )
        actual = Row(
            Foo,
            Field(VALUE, Fn((String,), (Number,))),
        )

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["@1"]), String)
        self.assertEqual(_combine_all(constraints["@2"]), Number)


class AnonymousTraitTests(unittest.TestCase):
    def test_requirement_parameters_are_contravariant_and_returns_covariant(self):
        trait = AnonymousTrait(
            (Symbol("T"),),
            (requirement(MAP, (V("T"),), (Number,)),),
        )
        ctx = Context()
        ctx.define_structural_overload(MAP, Overload((Number,), (Integer,)))

        self.assertTrue(assignable(Integer, trait, ctx))
        self.assertTrue(subtype(Integer, trait, ctx))
        self.assertTrue(compatible(Integer, trait, ctx))

    def test_requirement_rejects_narrow_parameter_or_broad_return(self):
        trait = AnonymousTrait(
            (Symbol("T"),),
            (requirement(MAP, (V("T"),), (Integer,)),),
        )
        narrow_param = Context()
        narrow_param.define_structural_overload(MAP, Overload((Integer,), (Integer,)))
        broad_return = Context()
        broad_return.define_structural_overload(MAP, Overload((Number,), (Number,)))

        self.assertFalse(assignable(Number, trait, narrow_param))
        self.assertFalse(assignable(Integer, trait, broad_return))

    def test_shared_generic_requirements_use_one_coherent_substitution(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("T"),), (V("U"),)),
            ),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (Number,)))
        ctx.define_structural_overload(WRITE, Overload((Foo,), (Number,)))

        self.assertTrue(assignable(Foo, trait, ctx))

    def test_shared_generic_requirements_reject_inconsistent_evidence(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("T"),), (V("U"),)),
            ),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))
        ctx.define_structural_overload(WRITE, Overload((Foo,), (Number,)))

        self.assertFalse(assignable(Foo, trait, ctx))

    def test_trait_solver_backtracks_across_requirement_overloads(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("T"),), (V("U"),)),
            ),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))
        ctx.define_structural_overload(READ, Overload((Foo,), (Number,)))
        ctx.define_structural_overload(WRITE, Overload((Foo,), (Number,)))

        self.assertTrue(assignable(Foo, trait, ctx))

    def test_incompatible_complete_trait_solutions_are_rejected(self):
        trait = unary_trait()
        overload = Overload((trait,), (V("U"),))

        for returns in ((String, Number), (Number, String)):
            ctx = Context()
            for result in returns:
                ctx.define_structural_overload(READ, Overload((Foo,), (result,)))
            self.assertFalse(assignable(Foo, trait, ctx))
            self.assertIsNone(apply_overload(overload, (Foo,), ctx))

    def test_compatible_trait_solutions_combine_to_common_supertype(self):
        trait = unary_trait()
        overload = Overload((trait,), (V("U"),))
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (Integer,)))
        ctx.define_structural_overload(READ, Overload((Foo,), (Number,)))

        applied = apply_overload(overload, (Foo,), ctx)

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["U"], Number)
        self.assertEqual(applied.returns, (Number,))

    def test_trait_solutions_use_contextual_nominal_subtyping(self):
        trait = unary_trait()
        overload = Overload((trait,), (V("U"),))

        for returns in ((Car, Vehicle), (Vehicle, Car)):
            ctx = Context(trait_impls={CAR: {VEHICLE}})
            for result in returns:
                ctx.define_structural_overload(READ, Overload((Foo,), (result,)))
            applied = apply_overload(overload, (Foo,), ctx)

            self.assertTrue(assignable(Foo, trait, ctx))
            self.assertIsNotNone(applied)
            self.assertEqual(applied.substitution["U"], Vehicle)
            self.assertEqual(applied.returns, (Vehicle,))

    def test_trait_satisfaction_is_independent_of_candidate_order(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("T"),), (V("U"),)),
            ),
        )
        outcomes = []
        for returns in ((String, Number), (Number, String)):
            ctx = Context()
            for result in returns:
                ctx.define_structural_overload(READ, Overload((Foo,), (result,)))
            ctx.define_structural_overload(WRITE, Overload((Foo,), (Number,)))
            outcomes.append(assignable(Foo, trait, ctx))

        self.assertEqual(outcomes, [True, True])

    def test_trait_satisfaction_is_independent_of_requirement_order(self):
        requirements = (
            requirement(READ, (V("T"),), (V("U"),)),
            requirement(WRITE, (V("T"),), (V("U"),)),
        )
        left = AnonymousTrait((Symbol("T"), Symbol("U")), requirements)
        right = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            tuple(reversed(requirements)),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (Number,)))
        ctx.define_structural_overload(WRITE, Overload((Foo,), (Number,)))

        self.assertEqual(assignable(Foo, left, ctx), assignable(Foo, right, ctx))

    def test_alpha_renamed_bound_generics_are_equivalent(self):
        original = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("U"),), (V("T"),)),
            ),
        )
        renamed = AnonymousTrait(
            (Symbol("Subject"), Symbol("Result")),
            (
                requirement(READ, (V("Subject"),), (V("Result"),)),
                requirement(WRITE, (V("Result"),), (V("Subject"),)),
            ),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))
        ctx.define_structural_overload(WRITE, Overload((String,), (Foo,)))

        self.assertTrue(same(original, renamed))
        self.assertEqual(assignable(Foo, original, ctx), assignable(Foo, renamed, ctx))

    def test_substitution_does_not_capture_trait_local_generics(self):
        trait = AnonymousTrait(
            (Symbol("T"),),
            (requirement(READ, (V("T"),), (V("Outer"),)),),
        )
        substituted = _substitute(trait, {"T": String, "Outer": Number})
        expected = AnonymousTrait(
            (Symbol("T"),),
            (requirement(READ, (V("T"),), (Number,)),),
        )

        self.assertTrue(same(substituted, expected))

    def test_nested_trait_bindings_shadow_outer_substitutions(self):
        inner = AnonymousTrait(
            (Symbol("T"),),
            (requirement(READ, (V("T"),), (V("T"),)),),
        )
        outer = AnonymousTrait(
            (Symbol("T"),),
            (requirement(MAP, (V("T"),), (inner, V("Outer"))),),
        )
        substituted = _substitute(outer, {"T": String, "Outer": Number})
        expected = AnonymousTrait(
            (Symbol("T"),),
            (requirement(MAP, (V("T"),), (inner, Number)),),
        )

        self.assertTrue(same(substituted, expected))

    def test_requirement_local_generic_is_scoped_during_substitution(self):
        trait = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    READ,
                    Overload(
                        (V("Local"),),
                        (V("Outer"),),
                        (GenericConstraint("Local", V("Outer")),),
                    ),
                ),
            ),
        )
        substituted = _substitute(
            trait,
            {"Local": String, "Outer": Number},
        )
        expected = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    READ,
                    Overload(
                        (V("Local"),),
                        (Number,),
                        (GenericConstraint("Local", Number),),
                    ),
                ),
            ),
        )

        self.assertTrue(same(substituted, expected))

    def test_requirement_local_generics_are_alpha_equivalent(self):
        left = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    READ,
                    Overload(
                        (V("X"),),
                        (V("X"),),
                        (GenericConstraint("X", Number),),
                    ),
                ),
            ),
        )
        right = AnonymousTrait(
            (),
            (
                AnonymousTraitRequirement(
                    READ,
                    Overload(
                        (V("Y"),),
                        (V("Y"),),
                        (GenericConstraint("Y", Number),),
                    ),
                ),
            ),
        )

        self.assertTrue(same(left, right))

    def test_generic_structural_candidate_honours_its_bound(self):
        trait = AnonymousTrait(
            (Symbol("T"),),
            (requirement(MAP, (V("T"),), (V("T"),)),),
        )
        candidate = Overload(
            (V("X"),),
            (V("X"),),
            (GenericConstraint("X", Vehicle),),
        )
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        ctx.define_structural_overload(MAP, candidate)

        self.assertTrue(assignable(Car, trait, ctx))
        self.assertFalse(assignable(String, trait, ctx))

    def test_trait_can_be_used_as_generic_constraint(self):
        trait = unary_trait()
        overload = Overload(
            (V("X"),),
            (V("X"),),
            (GenericConstraint("X", trait),),
        )
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))

        self.assertIsNotNone(apply_overload(overload, (Foo,), ctx))
        self.assertIsNone(apply_overload(overload, (Bar,), ctx))

    def test_traits_compose_with_covariant_nominal_generics(self):
        trait = unary_trait()
        ctx = Context()
        ctx.set_generic_variance(BOX, (Variance.COVARIANT,))
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))

        self.assertTrue(assignable(N(BOX, Foo), N(BOX, trait), ctx))
        self.assertFalse(assignable(N(BOX, trait), N(BOX, Foo), ctx))

    def test_traits_follow_contravariant_and_invariant_generic_positions(self):
        trait = unary_trait()
        ctx = Context()
        ctx.set_generic_variance(SINK, (Variance.CONTRAVARIANT,))
        ctx.set_generic_variance(CELL, (Variance.INVARIANT,))
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))

        self.assertTrue(assignable(N(SINK, trait), N(SINK, Foo), ctx))
        self.assertTrue(compatible(N(SINK, trait), N(SINK, Foo), ctx))
        self.assertFalse(assignable(N(SINK, Foo), N(SINK, trait), ctx))
        self.assertFalse(assignable(N(CELL, Foo), N(CELL, trait), ctx))
        self.assertFalse(assignable(N(CELL, trait), N(CELL, Foo), ctx))

    def test_trait_requirements_can_contain_rows_and_nominal_generics(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(
                    READ,
                    (V("T"),),
                    (N(BOX, Row(V("U"), Field(NAME, String))),),
                ),
            ),
        )
        ctx = Context()
        ctx.set_generic_variance(BOX, (Variance.COVARIANT,))
        ctx.define_structural_overload(
            READ,
            Overload(
                (Foo,),
                (N(BOX, Row(Bar, Field(NAME, String), Field(AGE, Number))),),
            ),
        )

        self.assertTrue(assignable(Foo, trait, ctx))

    def test_traits_compose_through_lists_unions_optionals_and_functions(self):
        trait = unary_trait()
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))

        self.assertTrue(assignable(ExactList(Foo), ExactList(trait), ctx))
        self.assertTrue(assignable(Foo, U(trait, String), ctx))
        self.assertTrue(assignable(Foo, optional(trait), ctx))
        self.assertTrue(compatible(Fn((trait,), (Foo,)), Fn((Foo,), (trait,)), ctx))

    def test_anonymous_trait_show_parse_round_trip_preserves_bindings(self):
        trait = AnonymousTrait(
            (Symbol("T"), Symbol("U")),
            (
                requirement(READ, (V("T"),), (V("U"),)),
                requirement(WRITE, (V("U"),), (V("T"),)),
            ),
        )

        reparsed = parse_type(show(trait))

        self.assertTrue(same(trait, reparsed))

    def test_anonymous_trait_relation_apis_agree_for_values(self):
        trait = unary_trait()
        ctx = Context()
        ctx.define_structural_overload(READ, Overload((Foo,), (String,)))

        self.assertTrue(assignable(Foo, trait, ctx))
        self.assertTrue(compatible(Foo, trait, ctx))
        self.assertTrue(subtype(Foo, trait, ctx))


class AnonymousGenericIntegrationTests(unittest.TestCase):
    def test_anonymous_and_named_generics_compose_in_function_types(self):
        pattern = Fn(
            (Row(V("@1"), Field(NAME, V("T"))),),
            (V("T"),),
        )
        actual = Fn(
            (Row(Foo, Field(NAME, String)),),
            (String,),
        )

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["@1"]), Foo)
        self.assertEqual(_combine_all(constraints["T"]), String)

    def test_anonymous_generics_survive_nested_collection_and_generic_solving(self):
        pattern = N(
            BOX,
            ExactList(Row(V("@1"), Field(VALUE, V("@2")))),
        )
        actual = N(
            BOX,
            ExactList(Row(Foo, Field(VALUE, String), Field(NAME, String))),
        )

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["@1"]), Foo)
        self.assertEqual(_combine_all(constraints["@2"]), String)

    def test_anonymous_generic_row_applies_inside_nominal_generic(self):
        pattern = N(BOX, Row(V("@1"), Field(NAME, V("@2"))))
        actual_row = Row(Foo, Field(NAME, String), Field(AGE, Number))
        overload = Overload((pattern,), (V("@2"),))
        ctx = Context()
        ctx.set_generic_variance(BOX, (Variance.COVARIANT,))

        applied = apply_overload(overload, (N(BOX, actual_row),), ctx)

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["@1"], Foo)
        self.assertEqual(applied.substitution["@2"], String)
        self.assertEqual(applied.returns, (String,))

    def test_anonymous_generic_solver_calls_do_not_share_state(self):
        pattern = Fn((V("@1"),), (V("@1"),))

        first = _solve(pattern, Fn((Foo,), (Foo,)))
        second = _solve(pattern, Fn((String,), (String,)))

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(_combine_all(first["@1"]), Foo)
        self.assertEqual(_combine_all(second["@1"]), String)

    def test_nested_function_anonymous_generics_preserve_linkage(self):
        pattern = Fn(
            (V("@1"), Fn((V("@2"),), (V("@1"),))),
            (V("@2"),),
        )
        actual = Fn(
            (Foo, Fn((String,), (Foo,))),
            (String,),
        )
        inconsistent = Fn(
            (Foo, Fn((String,), (Bar,))),
            (String,),
        )

        solved = _solve(pattern, actual)

        self.assertIsNotNone(solved)
        self.assertEqual(_combine_all(solved["@1"]), Foo)
        self.assertEqual(_combine_all(solved["@2"]), String)
        self.assertIsNone(_solve(pattern, inconsistent))

    def test_named_and_anonymous_generic_variables_remain_independent(self):
        pattern = Row(
            V("@1"),
            Field(FIRST, V("T")),
            Field(SECOND, V("@2")),
        )
        actual = Row(
            Foo,
            Field(FIRST, String),
            Field(SECOND, Number),
        )

        constraints = _solve(pattern, actual)

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["@1"]), Foo)
        self.assertEqual(_combine_all(constraints["T"]), String)
        self.assertEqual(_combine_all(constraints["@2"]), Number)

    def test_shared_anonymous_generic_enforces_one_solution(self):
        overload = Overload((V("@1"), V("@1")), (V("@1"),))

        accepted = apply_overload(overload, (Integer, Number))
        rejected = apply_overload(overload, (String, Number))

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.substitution["@1"], Number)
        self.assertIsNone(rejected)


if __name__ == "__main__":
    unittest.main()
