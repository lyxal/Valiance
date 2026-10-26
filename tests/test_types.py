import unittest

import valiance.vtypes as T
from itertools import permutations, product

from valiance.vtypes.symbols import Symbol
from valiance.vtypes import (
    AnonymousTrait,
    AnonymousTraitRequirement,
    Exact,
    AtLeastList,
    C,
    Context,
    DataTag,
    DataTagDefinition,
    ElementTag,
    ElementTagDefinition,
    ElementTagKind,
    Environment,
    NoVec,
    ExactList,
    Field,
    Fn,
    GenericConstraint,
    I,
    ListExactType,
    ListMinType,
    ListRuggedType,
    M,
    MetaVarId,
    MetaVarType,
    N,
    Never,
    NoneType,
    OKType,
    Overload,
    OverloadMismatchReason,
    Overloads,
    Row,
    Specificity,
    Tagged,
    TagKind,
    Tup,
    TupleTypeItem,
    TupVariadic,
    TypeStack,
    TypeVariable,
    UnionType,
    TypeVarId,
    TypeVarScope,
    U,
    V,
    Variance,
    WithoutTag,
    _combine_all,
    _match_specificity,
    _solve,
    _substitute,
    apply_overload,
    apply_overload_to_stack,
    apply_overloads_to_stack,
    assignable,
    collection_item_type,
    compatible,
    merge_stacks,
    merge_types,
    normalize,
    optional,
    resolve_overload_result,
    same,
    subtype,
    try_apply_overload,
)

NUMBER = Symbol("Number")
REAL = Symbol("Real")
INT = Symbol("Int")
STRING = Symbol("String")
CIRCLE = Symbol("Circle")
SHAPE = Symbol("Shape")
FOO = Symbol("Foo")
BAR = Symbol("bar")
BAZ = Symbol("baz")
CAR = Symbol("Car")
VEHICLE = Symbol("Vehicle")
PARSE_ERROR = Symbol("ParseError")

Number = N(NUMBER)
Real = N(REAL)
Int = N(INT)
String = N(STRING)
Foo = N(FOO)
Car = N(CAR)
Vehicle = N(VEHICLE)
ParseError = N(PARSE_ERROR)


class TypeLibraryTests(unittest.TestCase):
    def test_nominal_generic_inference_uses_a_refined_rows_base(self):
        """Structural evidence does not erase nominal generic arguments."""
        variable = T.V("Value", T.TypeVarId(98765, 0))
        overload = T.Overload((T.N(Symbol("Box"), variable),), (variable,))
        refined = T.Row(T.N(Symbol("Box"), T.Int), T.Field(Symbol("name"), T.String))
        applied = T.try_apply_overload(overload, (refined,), T.Context()).applied
        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (T.Int,))

    def test_ffi_nominal_namespace_is_distinct_from_valiance_namespace(self):
        ffi_int = T.FFI(Symbol("int"))
        valiance_int = N(Symbol("int"))
        self.assertFalse(same(ffi_int, valiance_int))
        self.assertFalse(subtype(ffi_int, valiance_int))
        self.assertFalse(assignable(ffi_int, valiance_int))
        self.assertFalse(compatible(ffi_int, valiance_int))

    def test_ffi_types_support_generics_and_normalization(self):
        typ = T.FFI(Symbol("array"), V("T"))
        self.assertIsInstance(normalize(typ), T.FFINamedType)
        result = _substitute(typ, {"T": T.FFI(Symbol("int"))})
        self.assertIsInstance(result, T.FFINamedType)
        self.assertEqual(str(result), "&array[&int]")

    def test_atomic_marker_normalization_is_idempotent(self):
        self.assertEqual(normalize(Exact(Exact(Int))), Exact(Int))

    def test_exact_list_union_preserves_different_ranks(self):
        self.assertEqual(
            U(ExactList(Int, 2), ExactList(Int)),
            UnionType(frozenset((ExactList(Int, 2), ExactList(Int)))),
        )

    def test_exact_list_union_does_not_factor_shared_outer_rank(self):
        self.assertEqual(
            U(ExactList(Int, 4), ExactList(Int, 2)),
            UnionType(frozenset((ExactList(Int, 4), ExactList(Int, 2)))),
        )

    def test_exact_list_union_factors_different_bases(self):
        self.assertEqual(
            U(ExactList(Int, 2), ExactList(String, 2)),
            ExactList(U(Int, String), 2),
        )

    def test_exact_list_union_preserves_different_rank_collection_subset(self):
        self.assertEqual(
            U(String, ExactList(Int, 2), ExactList(Int)),
            UnionType(frozenset((String, ExactList(Int, 2), ExactList(Int)))),
        )

    def test_different_rank_exact_list_union_is_idempotent(self):
        union = U(ExactList(Int, 4), ExactList(Int, 2))
        self.assertEqual(normalize(union), union)

    def test_intersection_with_never_normalizes_to_bottom(self):
        self.assertEqual(I(Int, Never()), Never())
        self.assertEqual(I(Never(), String), Never())

    def test_intersection_and_row_views_retain_union_constituent_subtyping(self):
        a, b, c = (N(Symbol(name)) for name in ('A', 'B', 'C'))
        union = U(a, b)
        view = I(union, c)
        for relation in (subtype, assignable, compatible):
            self.assertTrue(relation(view, union))
            self.assertTrue(relation(T.Row(view, Field(Symbol('value'), Int)), union))
            self.assertTrue(relation(I(a, b), U(I(a, b), c)))
        self.assertFalse(subtype(view, a))

    def test_optional_never_normalizes_to_none(self):
        self.assertEqual(optional(Never()), NoneType())

    def test_optional_types_are_covariant(self):
        self.assertTrue(subtype(optional(Int), optional(Number)))
        self.assertTrue(assignable(optional(Int), optional(Number)))
        self.assertTrue(compatible(optional(Int), optional(Number)))
        self.assertFalse(assignable(optional(Number), optional(Int)))

    def test_some_covariance_preserves_optional_subtype_transitivity(self):
        some_integer = N(Symbol("Some"), Int)

        self.assertTrue(subtype(some_integer, optional(Int)))
        self.assertTrue(subtype(optional(Int), optional(Number)))
        self.assertTrue(subtype(some_integer, optional(Number)))
        self.assertTrue(subtype(some_integer, N(Symbol("Some"), Number)))

    def test_merge_none_with_explicit_some_does_not_double_wrap(self):
        some_integer = N(Symbol("Some"), Int)

        self.assertEqual(merge_types(NoneType(), some_integer), optional(Int))
        self.assertEqual(merge_types(some_integer, NoneType()), optional(Int))
        self.assertEqual(
            merge_types(optional(Number), some_integer),
            optional(Number),
        )

    def test_tagged_unions_and_intersections_decompose_before_tag_checks(self):
        tagged_integer = Tagged(Int, "x")
        tagged_number = Tagged(Number, "x")
        tagged_real = Tagged(Real, "x")
        source_intersection = I(tagged_integer, Tagged(Number, "y"))
        source_union = U(tagged_integer, tagged_real)

        self.assertTrue(subtype(source_intersection, tagged_integer))
        self.assertTrue(subtype(source_union, tagged_number))

    def test_unit_tags_cannot_be_laundered_through_absent_requirements(self):
        ctx = Context()
        ctx.define_tag("km", TagKind.UNIT)
        ctx.define_tag("sec", TagKind.UNIT)
        seconds = Tagged(Int, "sec")
        not_kilometres = Tagged(Int, DataTag("km", absent=True))

        self.assertFalse(subtype(seconds, not_kilometres, ctx))
        self.assertFalse(assignable(seconds, not_kilometres, ctx))
        self.assertFalse(subtype(seconds, Int, ctx))
        self.assertTrue(
            subtype(seconds, Tagged(Number, DataTag("sec")), ctx)
        )

    def test_contextual_branch_merges_preserve_unit_tags(self):
        ctx = Context()
        ctx.define_tag("km", TagKind.UNIT)
        kilometres = Tagged(Int, "km")

        merged = merge_types(Int, kilometres, ctx)
        merged_stack = merge_stacks(
            TypeStack((Int,)),
            TypeStack((kilometres,)),
            ctx,
        )

        self.assertEqual(merged, U(Int, kilometres))
        self.assertEqual(merged_stack, TypeStack((U(Int, kilometres),)))
        self.assertTrue(assignable(Int, merged, ctx))
        self.assertTrue(assignable(kilometres, merged, ctx))

    def test_constructed_tag_and_plain_shape_remain_union_members(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        plain = ExactList(Int)
        infinite = Tagged(plain, "infinite")
        expected = U(infinite, plain)

        self.assertEqual(merge_types(infinite, plain, ctx), expected)
        self.assertEqual(merge_types(plain, infinite, ctx), expected)
        self.assertEqual(
            merge_stacks(TypeStack((infinite,)), TypeStack((plain,)), ctx),
            TypeStack((expected,)),
        )

    def test_tagged_plain_union_survives_repeated_branch_merges(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        plain = ExactList(Int)
        infinite = Tagged(plain, "infinite")
        expected = U(infinite, plain)

        self.assertEqual(merge_types(expected, plain, ctx), expected)
        self.assertEqual(merge_types(expected, infinite, ctx), expected)
        self.assertEqual(merge_types(plain, expected, ctx), expected)
        self.assertEqual(merge_types(infinite, expected, ctx), expected)

    def test_tagged_plain_branch_join_is_permutation_invariant(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        plain = ExactList(Int)
        infinite = Tagged(plain, "infinite")
        values = (infinite, plain, plain)
        merged = {
            merge_types(merge_types(first, second, ctx), third, ctx)
            for first, second, third in permutations(values)
        }

        self.assertEqual(merged, {U(infinite, plain)})

    def test_multiple_tagged_alternatives_remain_distinct_from_plain(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        ctx.define_tag("cached", TagKind.CONSTRUCTED)
        plain = ExactList(Int)
        infinite = Tagged(plain, "infinite")
        cached = Tagged(plain, "cached")
        expected = U(infinite, cached, plain)

        merged = merge_types(merge_types(infinite, cached, ctx), plain, ctx)

        self.assertEqual(merged, expected)

    def test_absent_tag_and_plain_shape_remain_union_members(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        plain = ExactList(Int)
        finite = Tagged(plain, DataTag("infinite", absent=True))

        self.assertEqual(merge_types(finite, plain, ctx), U(finite, plain))
        self.assertEqual(merge_types(plain, finite, ctx), U(finite, plain))

    def test_computed_tag_and_plain_shape_remain_union_members(self):
        ctx = Context()
        ctx.define_tag("sorted", TagKind.COMPUTED)
        plain = ExactList(Int)
        sorted_list = Tagged(plain, "sorted")

        self.assertEqual(
            merge_types(sorted_list, plain, ctx),
            U(sorted_list, plain),
        )

    def test_numeric_intersections_remove_redundant_supertypes(self):
        self.assertEqual(I(Int, Real), Int)
        self.assertEqual(I(Int, Number), Int)
        self.assertEqual(I(Real, Number), Real)

    def test_bottom_intersections_preserve_subtype_transitivity(self):
        source = I(Never(), Int)
        target = Tagged(String, DataTag("required"))

        self.assertTrue(subtype(source, Never()))
        self.assertTrue(subtype(Never(), target))
        self.assertTrue(subtype(source, target))

    def test_merge_is_canonical_for_mutually_assignable_refinements(self):
        plain = Tup(Int, Int)
        refined = Tup(WithoutTag(Int, "x"), WithoutTag(Int, "x"))

        self.assertTrue(assignable(plain, refined))
        self.assertTrue(assignable(refined, plain))
        self.assertEqual(merge_types(plain, refined), plain)
        self.assertEqual(merge_types(refined, plain), plain)

    def test_branch_type_merging_is_commutative_and_associative(self):
        values = (Int, String, NoneType())
        merged = {
            normalize(merge_types(merge_types(first, second), third))
            for first, second, third in permutations(values)
        }

        self.assertEqual(merged, {optional(U(Int, String))})

    def test_branch_stack_merging_is_independent_of_branch_order(self):
        branches = (
            TypeStack((Int,)),
            TypeStack((String,)),
            TypeStack(()),
        )
        merged = {
            merge_stacks(merge_stacks(first, second), third)
            for first, second, third in permutations(branches)
        }

        self.assertEqual(merged, {TypeStack((optional(U(Int, String)),))})

    def test_generic_evidence_combination_is_permutation_invariant(self):
        evidence = (NoneType(), Int, optional(Int))
        overload = Overload((V("T"), V("T"), V("T")), (V("T"),))

        results = []
        for arguments in permutations(evidence):
            applied = apply_overload(overload, arguments)
            self.assertIsNotNone(applied)
            results.append(applied.substitution["T"])

        self.assertTrue(
            all(normalize(result) == optional(Int) for result in results)
        )

    def test_symbols_have_value_equality_and_hashing(self):
        left = Symbol("Number")
        right = Symbol("Number")

        self.assertEqual(left, right)
        self.assertEqual({left: Number}[right], Number)

    def test_assignment_does_not_vectorise(self):
        self.assertFalse(assignable(C(ListExactType, Number), Number))
        self.assertTrue(compatible(C(ListExactType, Number), Number))

    def test_public_subtype_api_checks_nominal_widening(self):
        self.assertTrue(subtype(Int, Number))
        self.assertFalse(subtype(Number, Int))

    def test_minimum_rank_is_parameter_compatible_with_exact_rank(self):
        argument = C(ListMinType, Number)
        parameter = C(ListExactType, Number)

        self.assertFalse(assignable(argument, parameter))
        applied = apply_overload(Overload((parameter,), (Int,)), (argument,))

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.vectorised_depths, (0,))
        self.assertEqual(applied.vectorised_target_ranks, (1,))
        self.assertEqual(
            applied.actual_returns,
            (U(Int, C(ListMinType, Int)),),
        )

    def test_minimum_rank_argument_vectorises_dynamically_to_atomic_parameter(self):
        argument = C(ListMinType, Int)

        applied = apply_overload(Overload((Int,), (String,)), (argument,))

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.vectorised_depths, (1,))
        self.assertEqual(applied.vectorised_target_ranks, (0,))
        self.assertEqual(applied.actual_returns, (C(ListMinType, String),))

    def test_minimum_rank_exact_generic_keeps_unknown_rank_in_call_adaptation(self):
        item = TypeVariable('Item')
        mapped = TypeVariable('Mapped')
        overload = Overload(
            (ExactList(item), Fn((item,), (mapped,))),
            (ExactList(mapped),),
        )
        applied = apply_overload(
            overload,
            (AtLeastList(String), Fn((String,), (Int,))),
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution['Item'], String)
        self.assertEqual(applied.params[0], ExactList(String))
        self.assertEqual(applied.scores[0], Specificity.RANK_ADAPTED)
        self.assertEqual(applied.vectorised_depths, (0, 0))
        self.assertEqual(applied.vectorised_target_ranks, (1, None))
        self.assertEqual(applied.actual_returns, (AtLeastList(Int),))
        # An isolated item remains uncertain; only call adaptation owns the
        # excess rank when matching an exact collection parameter.
        self.assertEqual(collection_item_type(AtLeastList(String)),
                         U(String, AtLeastList(String)))
        self.assertIsNone(apply_overload(
            overload, (AtLeastList(String), Fn((Int,), (Int,))),
        ))

    def test_exact_generic_minimum_rank_retains_guaranteed_item_ranks(self):
        item = TypeVariable('Item')
        solved = _solve(ExactList(item), AtLeastList(String, 3), adapt_min_rank=True)
        self.assertEqual(solved, {'Item': [ExactList(String, 2)]})

    def test_union_vectorisation_records_runtime_target_rank(self):
        scalar_or_list = U(Int, C(ListMinType, Int))

        applied = apply_overload(Overload((Int,), (String,)), (scalar_or_list,))

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.vectorised_depths, (0,))
        self.assertEqual(applied.vectorised_target_ranks, (0,))
        self.assertEqual(applied.actual_returns, (U(String, C(ListMinType, String)),))

    def test_matching_union_parameter_does_not_trigger_runtime_vectorisation(self):
        scalar_or_list = U(Int, C(ListMinType, Int))

        applied = apply_overload(
            Overload((scalar_or_list,), (String,)),
            (scalar_or_list,),
        )

        self.assertIsNotNone(applied)
        self.assertFalse(applied.vectorised)
        self.assertEqual(applied.vectorised_depths, ())
        self.assertEqual(applied.vectorised_target_ranks, ())
        self.assertEqual(applied.actual_returns, (String,))

    def test_higher_minimum_rank_preserves_minimum_vectorised_result_rank(self):
        applied = apply_overload(
            Overload((C(ListExactType, Number),), (Int,)),
            (C(ListMinType, Number, 3),),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.vectorised_depths, (2,))
        self.assertEqual(applied.vectorised_target_ranks, (1,))
        self.assertEqual(applied.actual_returns, (C(ListMinType, Int, 2),))

    def test_numeric_nominal_hierarchy_is_integer_real_number(self):
        self.assertTrue(assignable(Int, Real))
        self.assertTrue(assignable(Int, Number))
        self.assertTrue(assignable(Real, Number))
        self.assertFalse(assignable(Real, Int))
        self.assertFalse(assignable(Number, Real))

    def test_nested_collection_normalization_is_idempotent(self):
        nested = ExactList(
            ExactList(
                AtLeastList(ExactList(String, 2), 4),
                2,
            ),
            1,
        )

        once = normalize(nested)

        self.assertEqual(normalize(once), once)
        self.assertEqual(once, AtLeastList(String, 9))




    def test_collection_item_types_are_covariant(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})

        self.assertTrue(
            assignable(C(ListExactType, Car), C(ListExactType, Vehicle), ctx)
        )
        self.assertTrue(
            assignable(C(ListExactType, Car, 2), C(ListExactType, Vehicle, 2), ctx)
        )
        self.assertFalse(
            assignable(C(ListExactType, Car, 2), C(ListExactType, Vehicle), ctx)
        )
        self.assertFalse(
            assignable(C(ListExactType, Vehicle), C(ListExactType, Car), ctx)
        )

    def test_nominal_generic_arguments_remain_invariant(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        box = Symbol("Box")

        self.assertFalse(assignable(N(box, Car), N(box, Vehicle), ctx))

    def test_variant_members_preserve_parent_arguments_and_declared_variance(self):
        parent, member = Symbol('Variant'), Symbol('Variant.Member')
        ctx = Context(variant_members={member: parent})
        self.assertTrue(subtype(N(member, Int), N(parent, Int), ctx))
        self.assertFalse(subtype(N(member, String), N(parent, Int), ctx))
        self.assertFalse(subtype(N(member), N(parent, Int), ctx))
        ctx.set_generic_variance(parent, (Variance.COVARIANT,))
        self.assertTrue(subtype(N(member, Int), N(parent, Number), ctx))
        self.assertFalse(subtype(N(member, String), N(parent, Number), ctx))
        ctx.set_generic_variance(parent, (Variance.CONTRAVARIANT,))
        self.assertTrue(subtype(N(member, Number), N(parent, Int), ctx))

    def test_nominal_generic_arguments_follow_declared_covariance(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        box = Symbol("Box")
        ctx.set_generic_variance(box, (Variance.COVARIANT,))

        self.assertTrue(assignable(N(box, Car), N(box, Vehicle), ctx))
        self.assertFalse(assignable(N(box, Vehicle), N(box, Car), ctx))

    def test_nominal_generic_arguments_follow_declared_contravariance(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        consumer = Symbol("Consumer")
        ctx.set_generic_variance(consumer, (Variance.CONTRAVARIANT,))

        self.assertTrue(assignable(N(consumer, Vehicle), N(consumer, Car), ctx))
        self.assertFalse(assignable(N(consumer, Car), N(consumer, Vehicle), ctx))

    def test_nominal_generic_variance_defaults_to_invariant_on_arity_mismatch(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        pair = Symbol("Pair")
        ctx.set_generic_variance(pair, (Variance.COVARIANT,))

        self.assertFalse(
            assignable(N(pair, Car, String), N(pair, Vehicle, String), ctx)
        )

    def test_nominal_generic_arguments_support_mixed_variance(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        transformer = Symbol("Transformer")
        ctx.set_generic_variance(
            transformer,
            (Variance.CONTRAVARIANT, Variance.COVARIANT),
        )

        self.assertTrue(
            assignable(
                N(transformer, Vehicle, Car),
                N(transformer, Car, Vehicle),
                ctx,
            )
        )
        self.assertFalse(
            assignable(
                N(transformer, Car, Vehicle),
                N(transformer, Vehicle, Car),
                ctx,
            )
        )

    def test_nominal_generic_variance_uses_nested_collection_assignability(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        box = Symbol("Box")
        ctx.set_generic_variance(box, (Variance.COVARIANT,))

        self.assertTrue(
            assignable(
                N(box, C(ListExactType, Car)),
                N(box, C(ListExactType, Vehicle)),
                ctx,
            )
        )

    def test_generic_constraints_are_checked_after_solving(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        overload = Overload(
            (V("T"),),
            (V("T"),),
            (GenericConstraint("T", Vehicle),),
        )

        accepted = apply_overload(overload, (Car,), ctx)
        rejected = apply_overload(overload, (String,), ctx)

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.substitution, {"T": Car})
        self.assertIsNone(rejected)

    def test_generic_upper_constraints_accept_subtypes(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        overload = Overload(
            (V("T"),),
            (V("T"),),
            (GenericConstraint("T", Car, Variance.COVARIANT),),
        )

        accepted = apply_overload(overload, (Car,), ctx)
        rejected = apply_overload(overload, (Vehicle,), ctx)

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.substitution, {"T": Car})
        self.assertIsNone(rejected)

    def test_generic_lower_constraints_accept_supertypes(self):
        ctx = Context(trait_impls={CAR: {VEHICLE}})
        overload = Overload(
            (V("T"),),
            (V("T"),),
            (GenericConstraint("T", Car, Variance.CONTRAVARIANT),),
        )

        accepted = apply_overload(overload, (Vehicle,), ctx)
        rejected = apply_overload(overload, (String,), ctx)

        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.substitution, {"T": Vehicle})
        self.assertIsNone(rejected)

    def test_result_union_simplifies_success_and_error_members(self):
        self.assertEqual(U(Number, ParseError), N(Symbol("Result"), Number, ParseError))
        self.assertEqual(
            U(OKType(Number), OKType(String), ParseError),
            N(Symbol("Result"), U(Number, String), ParseError),
        )

    def test_ok_and_error_values_assign_to_result(self):
        result = N(Symbol("Result"), Number, ParseError)

        self.assertTrue(assignable(OKType(Number), result))
        self.assertTrue(assignable(ParseError, result))
        self.assertFalse(assignable(String, result))

    def test_nested_list_solves_reduce_t_as_list(self):
        constraints = _solve(C(ListExactType, V("T")), C(ListExactType, Number, 2))
        self.assertIsNotNone(constraints)
        t = _combine_all(constraints["T"])
        self.assertEqual(t, C(ListExactType, Number))

    def test_rugged_generic_solves_from_scalar_leaves(self):
        actual = ExactList(
            U(
                Int,
                ExactList(Int),
                ExactList(ExactList(Int)),
            )
        )
        constraints = _solve(C(ListRuggedType, V("T")), actual)
        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["T"]), Int)

    def test_collection_item_type_peels_one_rank(self):
        self.assertEqual(collection_item_type(C(ListExactType, Number)), Number)
        self.assertEqual(
            collection_item_type(C(ListExactType, Number, 2)),
            C(ListExactType, Number),
        )
        self.assertEqual(
            collection_item_type(C(ListMinType, Number)),
            U(Number, C(ListMinType, Number)),
        )
        self.assertIsNone(collection_item_type(Number))

    def test_optional_union_displays_with_question_mark_syntax(self):
        self.assertEqual(str(optional(Int)), "Int?")
        self.assertEqual(str(optional(U(Int, String))), "(Int | String)?")
        self.assertEqual(
            str(Fn((optional(Int),), (optional(String),))),
            "Function[Int? -> String?]",
        )

    def test_collection_display_parenthesizes_union_base(self):
        self.assertEqual(
            str(C(ListExactType, U(Number, C(ListExactType, Number)))),
            "(Number | Number+)+",
        )

    def test_tagged_type_displays_tag_depth(self):
        self.assertEqual(
            str(Tagged(C(ListExactType, Number, 2), DataTag("infinite", depth=2))),
            "#infinite++ Number+2",
        )

    def test_environment_stores_tag_declarations_by_symbol_and_kind(self):
        env = Environment()

        env.add_constructed_tag("infinite")
        env.add_computed_tag(Symbol("sorted"))
        env.add_unit_tag("km")

        self.assertEqual(
            env.lookup_tag(Symbol("infinite")),
            DataTagDefinition(Symbol("infinite"), TagKind.CONSTRUCTED),
        )
        self.assertEqual(
            env.lookup_tag("sorted"),
            DataTagDefinition(Symbol("sorted"), TagKind.COMPUTED),
        )
        self.assertEqual(
            env.lookup_tag("km"),
            DataTagDefinition(Symbol("km"), TagKind.UNIT),
        )

    def test_child_environment_reads_parent_tag_declarations(self):
        env = Environment()
        env.add_constructed_tag("infinite")

        self.assertEqual(
            env.child_scope().lookup_tag("infinite"),
            DataTagDefinition(Symbol("infinite"), TagKind.CONSTRUCTED),
        )

    def test_environment_stores_element_tag_declarations(self):
        env = Environment()

        env.add_property_element_tag("IO")
        env.add_companion_element_tag(Symbol("Eager"))

        self.assertEqual(
            env.lookup_element_tag("IO"),
            ElementTagDefinition(Symbol("IO"), ElementTagKind.PROPERTY),
        )
        self.assertEqual(
            env.child_scope().lookup_element_tag("Eager"),
            ElementTagDefinition(Symbol("Eager"), ElementTagKind.COMPANION),
        )

    def test_function_element_tag_requirements_affect_compatibility(self):
        eager = ElementTag(Symbol("Eager"))
        not_eager = ElementTag(Symbol("Eager"), absent=True)

        tagged = Fn((Number,), (), (eager,))

        self.assertTrue(compatible(tagged, Fn((Number,), (), (eager,))))
        self.assertFalse(compatible(tagged, Fn((Number,), (), (not_eager,))))
        self.assertFalse(compatible(Fn((Number,), ()), Fn((Number,), (), (eager,))))

    def test_element_tag_requirements_match_parameterized_effects(self):
        panic_string = ElementTag(Symbol("Panic"), (String,))
        any_panic = ElementTag(Symbol("Panic"))
        no_panic = ElementTag(Symbol("Panic"), absent=True)
        no_string_panic = ElementTag(Symbol("Panic"), (String,), absent=True)
        no_number_panic = ElementTag(Symbol("Panic"), (Number,), absent=True)
        actual = Fn((Number,), (), (panic_string,))
        broad_numeric_panic = Fn(
            (Number,),
            (),
            (ElementTag(Symbol("Panic"), (Number,)),),
        )

        self.assertTrue(compatible(actual, Fn((Number,), (), (any_panic,))))
        self.assertFalse(compatible(actual, Fn((Number,), (), (no_panic,))))
        self.assertFalse(
            compatible(actual, Fn((Number,), (), (no_string_panic,)))
        )
        self.assertTrue(
            compatible(actual, Fn((Number,), (), (no_number_panic,)))
        )
        self.assertFalse(
            compatible(
                broad_numeric_panic,
                Fn(
                    (Number,),
                    (),
                    (ElementTag(Symbol("Panic"), (Int,), absent=True),),
                ),
            )
        )

    def test_show_sorts_parameterized_element_tags_without_type_ordering(self):
        rendered = T.show(
            Fn(
                (),
                (),
                (
                    ElementTag(Symbol("Panic"), (Number,)),
                    ElementTag(Symbol("Panic"), (String,)),
                ),
            )
        )

        self.assertEqual(
            rendered,
            "Function[ -> ]<Panic[Number], Panic[String]>",
        )

    def test_element_tag_arguments_participate_in_generic_solving(self):
        pattern = Fn(
            (Number,),
            (),
            (ElementTag(Symbol("Panic"), (V("F"),)),),
        )
        actual = Fn(
            (Number,),
            (),
            (ElementTag(Symbol("Panic"), (String,)),),
        )

        solved = _solve(pattern, actual)

        self.assertIsNotNone(solved)
        self.assertEqual(solved["F"], [String])

    def test_only_unit_tags_block_erasure_to_untagged_types(self):
        ctx = Context()
        ctx.define_tag("infinite", TagKind.CONSTRUCTED)
        ctx.define_tag("sorted", TagKind.COMPUTED)
        ctx.define_tag("km", TagKind.UNIT)

        self.assertTrue(assignable(Tagged(Number, "infinite"), Number, ctx))
        self.assertTrue(assignable(Tagged(Number, "sorted"), Number, ctx))
        self.assertFalse(assignable(Tagged(Number, "km"), Number, ctx))

    def test_readable_type_builders_match_core_constructors(self):
        self.assertEqual(TypeVariable("Item"), V("Item"))
        self.assertEqual(ExactList(Number), C(ListExactType, Number))
        self.assertEqual(
            WithoutTag(ExactList(TypeVariable("Item")), "infinite"),
            Tagged(C(ListExactType, V("Item")), DataTag("infinite", absent=True)),
        )

    def test_type_variable_identity_is_distinct_from_display_name(self):
        left = V("T", TypeVarId(10, 0))
        right = V("T", TypeVarId(11, 0))

        self.assertNotEqual(left, right)
        self.assertEqual(str(left), "T")
        self.assertEqual(str(right), "T")
        self.assertEqual(len({left, right}), 2)

    def test_type_variable_scope_allocates_stable_binder_positions(self):
        outer = TypeVarScope(20, ("T", "U"))
        inner = TypeVarScope(21, ("T",))

        self.assertEqual(outer.variable("T"), outer.variable("T"))
        self.assertEqual(outer.variable("T").identity, TypeVarId(20, 0))
        self.assertEqual(outer.variable("U").identity, TypeVarId(20, 1))
        self.assertNotEqual(outer.variable("T"), inner.variable("T"))
        self.assertEqual(outer.bindings(), {
            "T": outer.variable("T"),
            "U": outer.variable("U"),
        })
        with self.assertRaises(KeyError):
            outer.variable("Missing")

    def test_solver_keys_scoped_variables_by_identity(self):
        outer = V("T", TypeVarId(40, 0))
        inner = V("T", TypeVarId(41, 0))
        pattern = Tup(outer, inner)

        solved = _solve(pattern, Tup(Int, String))

        self.assertEqual(solved, {
            TypeVarId(40, 0): [Int],
            TypeVarId(41, 0): [String],
        })

    def test_substitution_targets_identity_not_display_name(self):
        outer = V("T", TypeVarId(50, 0))
        inner = V("T", TypeVarId(51, 0))

        substituted = _substitute(
            Tup(outer, inner),
            {TypeVarId(50, 0): Int},
        )

        self.assertEqual(substituted, Tup(Int, inner))

    def test_metavariable_has_distinct_refinable_identity(self):
        first = M("@1", MetaVarId(70, 1))
        second = M("@1", MetaVarId(71, 1))

        self.assertIsInstance(first, MetaVarType)
        self.assertNotEqual(first, second)
        self.assertEqual(str(first), "@1")
        self.assertEqual(
            _substitute(first, {MetaVarId(70, 1): Int}),
            Int,
        )

    def test_rigid_and_meta_variables_with_same_label_do_not_alias(self):
        rigid = V("T", TypeVarId(80, 0))
        inferred = M("T", MetaVarId(80, 0))

        self.assertNotEqual(rigid, inferred)
        self.assertEqual(
            _substitute(
                Tup(rigid, inferred),
                {MetaVarId(80, 0): String},
            ),
            Tup(rigid, String),
        )

    def test_type_variable_scope_rejects_duplicate_binder_names(self):
        with self.assertRaisesRegex(ValueError, "must be unique"):
            TypeVarScope(30, ("T", "T"))

    def test_absent_tagged_collection_parameter_does_not_vectorise_return(self):
        overload = Overload(
            (Tagged(C(ListExactType, V("T")), DataTag("infinite", absent=True)),),
            (Number,),
        )

        applied = apply_overload(overload, (C(ListExactType, Number),))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (Number,))
        self.assertFalse(applied.vectorised)

    def test_generic_parameter_solves_across_every_actual_union_branch(self):
        overload = Overload(
            (WithoutTag(ExactList(V("Item")), "infinite"),),
            (Int,),
        )
        actual = U(ExactList(Int), ExactList(Number))

        applied = apply_overload(overload, (actual,))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["Item"], Number)
        self.assertEqual(applied.actual_returns, (Int,))

    def test_constructed_tags_are_transparent_to_generic_overload_solving(self):
        overload = Overload(
            (C(ListExactType, V("Item")), Int),
            (C(ListExactType, V("Item")),),
        )
        source = Tagged(C(ListExactType, Int), DataTag("infinite"))

        applied = apply_overload(overload, (source, Int))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["Item"], Int)
        self.assertEqual(applied.params, (C(ListExactType, Int), Int))
        self.assertEqual(applied.actual_returns, (C(ListExactType, Int),))

    def test_vectorised_return_lifts_data_tags_to_collection_depth(self):
        boolean_integer = Tagged(Int, DataTag("boolean"))
        applied = apply_overload(
            Overload((Int, Int), (boolean_integer,)),
            (C(ListExactType, Int), Int),
        )
        self.assertIsNotNone(applied)
        self.assertEqual(
            applied.actual_returns,
            (
                Tagged(
                    C(ListExactType, Int),
                    DataTag("boolean", depth=1),
                ),
            ),
        )

    def test_union_argument_joins_scalar_and_vectorised_returns(self):
        scalar_or_list = U(Int, ExactList(Int))

        applied = apply_overload(
            Overload((Int, Int), (Int,)),
            (scalar_or_list, Int),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (scalar_or_list,))

    def test_union_vectorisation_joins_every_rank_combination(self):
        scalar_or_vector = U(
            Int,
            ExactList(Int),
            ExactList(Int, 2),
        )
        applied = apply_overload(
            Overload((Int, Int), (String,)),
            (scalar_or_vector, scalar_or_vector),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(
            applied.actual_returns,
            (U(String, ExactList(String), ExactList(String, 2)),),
        )


    def test_nested_union_vectorisation_substitutes_every_scalar_leaf(self):
        shape = ExactList(
            U(
                Int,
                ExactList(Int),
                ExactList(U(Int, ExactList(Int))),
            )
        )
        expected = ExactList(
            U(
                String,
                ExactList(String),
                ExactList(U(String, ExactList(String))),
            )
        )
        applied = apply_overload(Overload((Int,), (String,)), (shape,))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (expected,))

    def test_vectorised_return_preserves_heterogeneous_collection_shape(self):
        rugged_shape = ExactList(U(Int, ExactList(Int)))

        applied = apply_overload(
            Overload((Int, Int), (Int,)),
            (rugged_shape, rugged_shape),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (rugged_shape,))

    def test_apply_overload_marks_vectorisation(self):
        overload = Overload((Number, Number), (Number,))

        applied = apply_overload(
            overload,
            (C(ListExactType, Number), C(ListExactType, Number)),
        )

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.actual_returns, (C(ListExactType, Number),))

    def test_rugged_collection_only_vectorises_to_atomic_parameters(self):
        argument = C(ListRuggedType, Number, 2)

        atomic = apply_overload(Overload((Number,), (Number,)), (argument,))

        self.assertIsNotNone(atomic)
        self.assertTrue(atomic.vectorised)
        self.assertEqual(atomic.vectorised_depths, (2,))
        self.assertEqual(
            atomic.actual_returns,
            (C(ListRuggedType, Number, 2),),
        )

        for parameter in (
            C(ListExactType, Number),
            C(ListMinType, Number),
        ):
            with self.subTest(parameter=parameter):
                self.assertFalse(compatible(argument, parameter))
                self.assertIsNone(
                    apply_overload(Overload((parameter,), (Number,)), (argument,))
                )

    def test_higher_rugged_rank_satisfies_lower_rugged_rank(self):
        for source_rank in (1, 2, 3):
            source = C(ListRuggedType, Int, source_rank)
            for target_rank in range(1, source_rank + 1):
                target = C(ListRuggedType, Number, target_rank)
                with self.subTest(
                    source_rank=source_rank,
                    target_rank=target_rank,
                ):
                    self.assertTrue(subtype(source, target))
                    self.assertTrue(assignable(source, target))
                    self.assertTrue(compatible(source, target))
                    applied = apply_overload(
                        Overload((target,), (Number,)),
                        (source,),
                    )
                    self.assertIsNotNone(applied)
                    self.assertFalse(applied.vectorised)

    def test_generic_rugged_call_keeps_leaf_type_across_rank_widening(self):
        caller_item = V("T", TypeVarId(100, 0))
        callee_item = V("T", TypeVarId(200, 0))
        overload = Overload(
            (C(ListRuggedType, callee_item),),
            (C(ListExactType, callee_item),),
            generic_params=("T",),
        )

        applied = apply_overload(
            overload,
            (C(ListRuggedType, caller_item, 3),),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution, {callee_item.identity: caller_item})
        self.assertEqual(
            applied.actual_returns,
            (C(ListExactType, caller_item),),
        )
        self.assertFalse(applied.vectorised)

    def test_lower_rugged_rank_does_not_satisfy_higher_rugged_rank(self):
        for source_rank in (1, 2):
            source = C(ListRuggedType, Number, source_rank)
            target = C(ListRuggedType, Number, source_rank + 1)
            with self.subTest(source_rank=source_rank):
                self.assertFalse(subtype(source, target))
                self.assertFalse(assignable(source, target))
                self.assertFalse(compatible(source, target))

    def test_mixed_rugged_vectorisation_keeps_the_weakest_shape(self):
        rugged = C(ListRuggedType, Number, 2)
        uniform = C(ListExactType, Number, 2)

        applied = apply_overload(
            Overload((Number, Number), (Number,)),
            (rugged, uniform),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(
            applied.actual_returns,
            (C(ListRuggedType, Number, 2),),
        )

    def test_collection_ranks_must_be_positive(self):
        for collection in (
            ListExactType,
            ListMinType,
            ListRuggedType,
        ):
            for rank in (0, -1):
                with self.subTest(collection=collection, rank=rank):
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        C(collection, Number, rank)

    def test_rugged_sources_never_subsume_uniform_collection_targets(self):
        for source_rank in (1, 2, 3):
            source = C(ListRuggedType, Number, source_rank)
            for target in (
                C(ListExactType, Number),
                C(ListMinType, Number),
            ):
                with self.subTest(source=source, target=target):
                    self.assertFalse(subtype(source, target))
                    self.assertFalse(assignable(source, target))
                    self.assertFalse(compatible(source, target))

    def test_uniform_collection_can_still_vectorise_to_collection_parameter(self):
        parameter = C(ListExactType, Number)

        for argument in (
            C(ListExactType, Number, 2),
            C(ListMinType, Number, 2),
        ):
            with self.subTest(argument=argument):
                applied = apply_overload(
                    Overload((parameter,), (Number,)),
                    (argument,),
                )

                self.assertIsNotNone(applied)
                self.assertTrue(applied.vectorised)
                self.assertEqual(applied.vectorised_depths, (1,))

    def test_exact_parameter_disables_vectorisation(self):
        overload = Overload((NoVec(Number),), (Number,))

        scalar = apply_overload(overload, (Int,))
        vector = apply_overload(overload, (C(ListExactType, Int),))

        self.assertIsNotNone(scalar)
        self.assertFalse(scalar.vectorised)
        self.assertIsNone(vector)

    def test_exact_collection_requires_the_declared_rank(self):
        overload = Overload((NoVec(C(ListExactType, Number)),), (Number,))

        matching = apply_overload(overload, (C(ListExactType, Int),))
        higher_rank = apply_overload(overload, (C(ListExactType, Int, 2),))

        self.assertIsNotNone(matching)
        self.assertFalse(matching.vectorised)
        self.assertEqual(matching.actual_returns, (Number,))
        self.assertIsNone(higher_rank)

    def test_generic_exact_parameter_treats_collection_as_one_value(self):
        overload = Overload((NoVec(V("T")),), (V("T"),))
        argument = C(ListExactType, Int)

        applied = apply_overload(overload, (argument,))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], argument)
        self.assertEqual(applied.params, (NoVec(argument),))
        self.assertEqual(applied.actual_returns, (argument,))
        self.assertFalse(applied.vectorised)

    def test_exact_argument_broadcasts_when_another_argument_vectorises(self):
        overload = Overload(
            (NoVec(C(ListExactType, Number)), Number),
            (Number,),
        )

        applied = apply_overload(
            overload,
            (
                C(ListExactType, Int),
                C(ListExactType, Int),
            ),
        )

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.vectorised_depths, (0, 1))
        self.assertEqual(applied.actual_returns, (C(ListExactType, Number),))

    def test_atomic_marker_can_supply_the_only_generic_evidence(self):
        applied = apply_overload(
            Overload((Exact(V("T")),), (V("T"),)),
            (Int,),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], Int)
        self.assertEqual(applied.actual_returns, (Int,))
        self.assertIsNone(
            apply_overload(
                Overload((Exact(V("T")),), (V("T"),)),
                (C(ListExactType, Int),),
            )
        )

    def test_atomic_collection_base_requires_scalar_items_and_declared_rank(self):
        overload = Overload(
            (C(ListExactType, Exact(V("T"))),),
            (C(ListExactType, V("T")),),
        )

        matching = apply_overload(overload, (C(ListExactType, Int),))

        self.assertIsNotNone(matching)
        self.assertEqual(matching.substitution["T"], Int)
        self.assertEqual(
            matching.actual_returns,
            (C(ListExactType, Int),),
        )
        self.assertIsNone(
            apply_overload(overload, (C(ListExactType, Int, 2),))
        )
        self.assertIsNone(
            apply_overload(
                overload,
                (C(ListExactType, C(ListExactType, Int)),),
            )
        )


    def test_atomic_evidence_validates_without_overriding_regular_evidence(self):
        overload = Overload(
            (C(ListExactType, V("T")), Exact(V("T"))),
            (V("T"),),
        )

        applied = apply_overload(
            overload,
            (C(ListExactType, Int), Int),
        )

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], Int)
        self.assertIsNone(
            apply_overload(
                overload,
                (C(ListExactType, Int), String),
            )
        )
        self.assertIsNone(
            apply_overload(
                overload,
                (C(ListExactType, Int, 2), Int),
            )
        )

    def test_collection_item_type_looks_through_tags(self):
        self.assertEqual(
            collection_item_type(Tagged(C(ListExactType, Number), "infinite")),
            Number,
        )

    def test_collection_item_type_projects_nested_tag_facts(self):
        collection = Tagged(ExactList(Int, 2), DataTag("boolean", 2),
                            DataTag("checked", 1, absent=True), DataTag("container", 0))
        item = collection_item_type(collection)
        self.assertEqual(item, Tagged(ExactList(Int), DataTag("boolean", 1),
                                      DataTag("checked", 0, absent=True)))
        self.assertEqual(collection_item_type(item), Tagged(Int, DataTag("boolean", 0)))

    def test_row_type_displays_required_fields(self):
        self.assertEqual(
            str(Row(V("@1"), Field(BAZ, V("@2")))),
            "@1(.baz: @2)",
        )

    def test_row_type_assignability_requires_fields(self):
        source = Row(Foo, Field(BAR, Number), Field(BAZ, String))
        target = Row(Foo, Field(BAR, Number))

        self.assertTrue(assignable(source, target))
        self.assertFalse(assignable(Foo, target))
        self.assertFalse(assignable(source, Row(Foo, Field(BAR, String))))

    def test_row_type_solves_base_and_field_generics(self):
        constraints = _solve(
            Row(V("T"), Field(BAR, V("U"))),
            Row(Foo, Field(BAR, Number)),
        )

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["T"]), Foo)
        self.assertEqual(_combine_all(constraints["U"]), Number)

    def test_row_type_overload_application_substitutes_fields(self):
        overload = Overload(
            (Row(V("T"), Field(BAR, V("U"))),),
            (V("U"),),
        )

        applied = apply_overload(overload, (Row(Foo, Field(BAR, Number)),))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], Foo)
        self.assertEqual(applied.substitution["U"], Number)
        self.assertEqual(applied.params, (Row(Foo, Field(BAR, Number)),))
        self.assertEqual(applied.returns, (Number,))


    def test_row_width_depth_and_compatibility_laws(self):
        source = Row(Foo, Field(BAR, Int), Field(BAZ, String))
        wider = Row(Foo, Field(BAR, Number))
        wrong_depth = Row(Foo, Field(BAR, String))

        self.assertTrue(subtype(source, wider))
        self.assertTrue(assignable(source, wider))
        self.assertTrue(compatible(source, wider))
        self.assertFalse(assignable(wider, source))
        self.assertFalse(compatible(source, wrong_depth))

    def test_row_generic_requires_one_coherent_solution_across_fields(self):
        pattern = Row(V("Base"), Field(BAR, V("T")), Field(BAZ, V("T")))
        coherent = Row(Foo, Field(BAR, Int), Field(BAZ, Int))
        widenable = Row(Foo, Field(BAR, Int), Field(BAZ, Real))
        conflicting = Row(Foo, Field(BAR, String), Field(BAZ, Int))

        self.assertIsNotNone(_solve(pattern, coherent))
        solved = _solve(pattern, widenable)
        self.assertIsNotNone(solved)
        self.assertEqual(_combine_all(solved["T"]), Real)
        self.assertTrue(
            same(
                _combine_all(_solve(pattern, conflicting)["T"]),
                U(Int, String),
            )
        )
        self.assertTrue(compatible(conflicting, pattern))

    def test_row_nested_in_covariant_generic_preserves_field_rules(self):
        ctx = Context()
        box = Symbol("Box")
        ctx.set_generic_variance(box, (Variance.COVARIANT,))
        source = N(box, Row(Foo, Field(BAR, Int), Field(BAZ, String)))
        target = N(box, Row(Foo, Field(BAR, Number)))

        self.assertTrue(assignable(source, target, ctx))
        self.assertTrue(compatible(source, target, ctx))
        self.assertFalse(assignable(target, source, ctx))

    def test_anonymous_trait_requirements_share_one_generic_solution(self):
        ctx = Context()
        plus = Symbol("plus")
        times = Symbol("times")
        ctx.define_structural_overload(plus, Overload((Int, Int), (Int,)))
        ctx.define_structural_overload(times, Overload((Int, Int), (String,)))
        trait = AnonymousTrait(
            (Symbol("T"),),
            (
                AnonymousTraitRequirement(plus, Overload((V("T"), V("T")), (V("T"),))),
                AnonymousTraitRequirement(times, Overload((V("T"), V("T")), (V("T"),))),
            ),
        )

        self.assertFalse(assignable(Int, trait, ctx))
        self.assertFalse(compatible(Int, trait, ctx))

    def test_anonymous_trait_alpha_renaming_is_semantics_preserving(self):
        ctx = Context()
        combine = Symbol("combine")
        ctx.define_structural_overload(combine, Overload((Int, Int), (Int,)))
        left = AnonymousTrait(
            (Symbol("T"),),
            (AnonymousTraitRequirement(combine, Overload((V("T"), V("T")), (V("T"),))),),
        )
        right = AnonymousTrait(
            (Symbol("@17"),),
            (AnonymousTraitRequirement(combine, Overload((V("@17"), V("@17")), (V("@17"),))),),
        )

        self.assertEqual(assignable(Int, left, ctx), assignable(Int, right, ctx))
        self.assertEqual(compatible(Int, left, ctx), compatible(Int, right, ctx))

    def test_anonymous_generic_scopes_do_not_capture_named_generic(self):
        pattern = Tup(Row(V("T"), Field(BAR, V("@1"))), V("T"))
        actual = Tup(Row(Foo, Field(BAR, Int)), Foo)
        solved = _solve(pattern, actual)

        self.assertIsNotNone(solved)
        self.assertEqual(_combine_all(solved["T"]), Foo)
        self.assertEqual(_combine_all(solved["@1"]), Int)

    def test_optional_none_does_not_solve_type_var(self):
        constraints = _solve(optional(V("T")), NoneType())
        self.assertEqual(constraints, {})

    def test_optional_default_can_solve_from_other_parameter(self):
        constraints = {}
        for pattern, actual in [(optional(V("T")), NoneType()), (V("T"), Number)]:
            result = _solve(pattern, actual)
            self.assertIsNotNone(result)
            for key, values in result.items():
                constraints.setdefault(key, []).extend(values)
        self.assertEqual(_combine_all(constraints["T"]), Number)

    def test_fixed_tuple_assigns_to_arbitrary_length_tuple_pattern(self):
        numbers_then_string = TupVariadic(
            TupleTypeItem(Number, repeated=True),
            TupleTypeItem(String),
        )
        numbers_then_strings = TupVariadic(
            TupleTypeItem(Number, repeated=True),
            TupleTypeItem(String, repeated=True),
        )

        self.assertTrue(assignable(Tup(String), numbers_then_string))
        self.assertTrue(assignable(Tup(Number, Number, String), numbers_then_string))
        self.assertTrue(assignable(Tup(Number, String, String), numbers_then_strings))
        self.assertFalse(assignable(Tup(String, Number), numbers_then_string))

    def test_union_of_fixed_tuples_assigns_to_variadic_pattern(self):
        actual = U(
            Tup(Int, Int, Int),
            Tup(Int, Int, Int, Int),
        )
        self.assertTrue(assignable(actual, TupVariadic(TupleTypeItem(Int, True))))

    def test_union_tuple_rejects_when_one_branch_misses_variadic_pattern(self):
        actual = U(Tup(Int, Int), Tup(Int, String))
        self.assertFalse(assignable(actual, TupVariadic(TupleTypeItem(Int, True))))

    def test_arbitrary_length_tuple_pattern_solves_generics(self):
        constraints = _solve(
            TupVariadic(TupleTypeItem(V("T"), repeated=True), TupleTypeItem(String)),
            Tup(Number, Number, String),
        )

        self.assertIsNotNone(constraints)
        self.assertEqual(_combine_all(constraints["T"]), Number)

    def test_reduce_accepts_plus_via_vectorised_function_compatibility(self):
        plus = Overloads(Overload((Number, Number), (Number,)))
        expected = Fn(
            (C(ListExactType, Number), C(ListExactType, Number)),
            (C(ListExactType, Number),),
        )
        self.assertTrue(compatible(plus, expected))

    def test_reduce_signature_substitution(self):
        xs_type = C(ListExactType, Number, 2)
        constraints = _solve(C(ListExactType, V("T")), xs_type)
        subst = {"T": _combine_all(constraints["T"])}
        function_param = _substitute(Fn((V("T"), V("T")), (V("T"),)), subst)
        self.assertEqual(
            function_param,
            Fn(
                (C(ListExactType, Number), C(ListExactType, Number)),
                (C(ListExactType, Number),),
            ),
        )

    def test_apply_overload_reports_substitution_and_actual_returns(self):
        reduce = Overload(
            (C(ListExactType, V("T")), Fn((V("T"), V("T")), (V("T"),))),
            (V("T"),),
        )
        applied = apply_overload(
            reduce,
            (C(ListExactType, Number, 2), Fn((Number, Number), (Number,))),
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], C(ListExactType, Number))
        self.assertEqual(applied.params[0], C(ListExactType, Number, 2))
        self.assertEqual(applied.returns, (C(ListExactType, Number),))
        self.assertFalse(applied.vectorised)

    def test_try_apply_overload_reports_structured_mismatch(self):
        overload = Overload((Number,), (Number,))

        accepted = try_apply_overload(overload, (Int,))
        self.assertIsNotNone(accepted.applied)
        self.assertIsNone(accepted.mismatch)

        rejected = try_apply_overload(overload, (String,))
        self.assertIsNone(rejected.applied)
        self.assertIsNotNone(rejected.mismatch)
        self.assertEqual(
            rejected.mismatch.reason,
            OverloadMismatchReason.ARGUMENT_TYPE,
        )
        self.assertEqual(rejected.mismatch.argument_index, 0)
        self.assertEqual(rejected.mismatch.expected, Number)
        self.assertEqual(rejected.mismatch.actual, String)

    def test_apply_overload_to_stack_can_infer_missing_inputs(self):
        plus = Overload((Number, Number), (Number,))
        applied = apply_overload_to_stack(plus, TypeStack(), infer_missing=True)
        self.assertIsNotNone(applied)
        self.assertEqual(applied.inputs, (Number, Number))
        self.assertEqual(applied.stack, TypeStack((Number,)))

    def test_apply_overload_to_stack_requires_inputs_without_inference(self):
        plus = Overload((Number, Number), (Number,))
        self.assertIsNone(
            apply_overload_to_stack(
                plus,
                TypeStack((Number,)),
                infer_missing=False,
            )
        )
        applied = apply_overload_to_stack(
            plus,
            TypeStack((Number, Number)),
            infer_missing=False,
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied.stack, TypeStack((Number,)))

    def test_apply_overloads_to_stack_chooses_and_updates_stack(self):
        plus_number = Overload((Number, Number), (Number,))
        plus_string = Overload((String, String), (String,))
        applied = apply_overloads_to_stack(
            (plus_number, plus_string),
            TypeStack((String, String)),
        )
        self.assertIsNotNone(applied)
        self.assertIs(applied.overload, plus_string)
        self.assertEqual(applied.stack, TypeStack((String,)))

    def test_apply_overloads_to_stack_reports_ambiguity(self):
        left = Overload((Number, U(Number, String)), (Number,))
        right = Overload((U(Number, String), Number), (Number,))
        self.assertIsNone(
            apply_overloads_to_stack(
                (left, right),
                TypeStack((Number, Number)),
            )
        )

    def test_merge_types_and_stacks(self):
        self.assertEqual(merge_types(Number, String), U(Number, String))
        self.assertEqual(
            merge_stacks(TypeStack((Number,)), TypeStack()),
            TypeStack((optional(Number),)),
        )
        self.assertEqual(
            merge_stacks(TypeStack((Number,)), TypeStack((String,))),
            TypeStack((U(Number, String),)),
        )

    def test_type_stack_methods(self):
        plus = Overload((Number, Number), (Number,))
        stack = TypeStack().push(Number).push(Number)
        applied = stack.apply_one(plus)
        self.assertIsNotNone(applied)
        self.assertEqual(applied.stack, TypeStack((Number,)))
        self.assertEqual(
            stack.merge(TypeStack()),
            TypeStack((optional(Number), optional(Number))),
        )

    def test_callable_generic_collection_input_preserves_scalar_returns(self):
        expected = Fn((ExactList(String, 2),), (TypeVariable("Mapped"),))
        callback = Fn((ExactList(TypeVariable("Item")),), (Int,))

        self.assertEqual(_solve(expected, callback), {"Mapped": [Int]})

    def test_overload_set_can_cover_each_union_function_input_branch(self):
        overloaded = Overloads(
            Overload((Int,), (Int,)),
            Overload((String,), (String,)),
        )
        expected = Fn((U(Int, String),), (TypeVariable("Mapped"),))

        solved = _solve(expected, overloaded)

        self.assertEqual(solved, {"Mapped": [U(Int, String)]})
        self.assertTrue(
            compatible(
                overloaded,
                Fn((U(Int, String),), (U(Int, String),)),
            )
        )

    def test_union_function_coverage_requires_every_branch(self):
        overloaded = Overloads(Overload((Int,), (Int,)))

        self.assertFalse(
            compatible(
                overloaded,
                Fn((U(Int, String),), (U(Int, String),)),
            )
        )

    def test_union_function_coverage_requires_unambiguous_branches(self):
        overloaded = Overloads(
            Overload((Int,), (Int,)),
            Overload((Int,), (String,)),
            Overload((String,), (String,)),
        )

        self.assertFalse(
            compatible(
                overloaded,
                Fn((U(Int, String),), (U(Int, String),)),
            )
        )

    def test_union_function_coverage_rejects_overlapping_tag_dispatch(self):
        left = Tagged(Int, DataTag(Symbol("left")))
        right = Tagged(Int, DataTag(Symbol("right")))
        overloaded = Overloads(
            Overload((left,), (Int,)),
            Overload((right,), (String,)),
        )

        self.assertFalse(
            compatible(
                overloaded,
                Fn((U(left, right),), (U(Int, String),)),
            )
        )

    def test_union_function_coverage_accepts_disjoint_reified_tags(self):
        ctx = Context()
        ctx.define_tag("left", TagKind.COMPUTED)
        ctx.define_tag("right", TagKind.COMPUTED)
        ctx.add_disjoint_tags("left", "right")
        left = Tagged(Int, DataTag("left"))
        right = Tagged(Int, DataTag("right"))
        overloaded = Overloads(
            Overload((left,), (Int,)),
            Overload((right,), (String,)),
        )

        self.assertTrue(
            compatible(
                overloaded,
                Fn((U(left, right),), (U(Int, String),)),
                ctx,
            )
        )

    def test_union_function_coverage_accepts_broad_non_union_inputs(self):
        overloaded = Overloads(
            Overload((Int, Number), (Number,)),
            Overload((String, Number), (Number,)),
        )

        self.assertTrue(
            compatible(
                overloaded,
                Fn((U(Int, String), Number), (Number,)),
            )
        )

    def test_union_function_coverage_accepts_nominal_supertypes(self):
        overloaded = Overloads(
            Overload((Number,), (Number,)),
            Overload((String,), (String,)),
        )

        self.assertTrue(
            compatible(
                overloaded,
                Fn((U(Int, String),), (U(Number, String),)),
            )
        )

    def test_concrete_overload_beats_union_overload(self):
        concrete = Overload((Number,), (Number,))
        unioned = Overload((U(Number, String),), (Number,))
        result = resolve_overload_result((concrete, unioned), (Number,))
        self.assertIsNotNone(result)
        self.assertIs(result.overload, concrete)

    def test_concrete_exact_overload_beats_exact_generic_equivalent(self):
        concrete = Overload((Int,), (String,))
        generic = Overload((V("T"),), (Number,))

        concrete_applied = apply_overload(concrete, (Int,))
        generic_applied = apply_overload(generic, (Int,))
        result = resolve_overload_result((generic, concrete), (Int,))

        self.assertEqual(concrete_applied.scores, (Specificity.EXACT,))
        self.assertEqual(
            generic_applied.scores,
            (Specificity.EXACT_GENERIC,),
        )
        self.assertIsNotNone(result)
        self.assertIs(result.overload, concrete)

    def test_direct_generic_beats_implicit_result_injection(self):
        direct = Overload((V("E"),), (V("E"),))
        result_error = Overload(
            (N(Symbol("Result"), Never(), V("E")),),
            (V("E"),),
        )

        direct_applied = apply_overload(direct, (ParseError,))
        injected_applied = apply_overload(result_error, (ParseError,))
        result = resolve_overload_result(
            (result_error, direct),
            (ParseError,),
        )

        self.assertEqual(
            direct_applied.scores,
            (Specificity.EXACT_GENERIC,),
        )
        self.assertEqual(injected_applied.scores, (Specificity.UNION,))
        self.assertIsNotNone(result)
        self.assertIs(result.overload, direct)

    def test_numeric_tower_specificity_selects_narrowest_overload(self):
        integer = Overload((Int, Int), (Int,))
        real = Overload((Real, Real), (Real,))
        number = Overload((Number, Number), (Number,))

        result = resolve_overload_result((number, real, integer), (Int, Int))

        self.assertIsNotNone(result)
        self.assertIs(result.overload, integer)

    def test_vectorised_numeric_specificity_selects_narrowest_overload(self):
        integer = Overload((Int, Int), (Int,))
        real = Overload((Real, Real), (Real,))
        number = Overload((Number, Number), (Number,))

        applied = apply_overloads_to_stack(
            (number, real, integer),
            TypeStack((C(ListExactType, Int), C(ListExactType, Int))),
        )

        self.assertIsNotNone(applied)
        self.assertIs(applied.overload, integer)
        self.assertEqual(applied.stack, TypeStack((C(ListExactType, Int),)))

    def test_cross_specificity_is_ambiguous(self):
        left = Overload((Number, U(Number, String)), (Number,))
        right = Overload((U(Number, String), Number), (Number,))
        self.assertIsNone(resolve_overload_result((left, right), (Number, Number)))

    def test_trait_specificity(self):
        ctx = Context(trait_impls={CIRCLE: {SHAPE}})
        self.assertTrue(compatible(N(CIRCLE), N(SHAPE), ctx))
        self.assertEqual(
            _match_specificity(N(CIRCLE), N(SHAPE), ctx),
            Specificity.TRAIT,
        )

    def test_upper_only_callable_evidence_chooses_upper_bound(self):
        overload = Overload(
            (Fn((V("T"),), (String,)),),
            (V("T"),),
        )
        applied = apply_overload(
            overload,
            (Fn((Number,), (String,)),),
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], Number)
        self.assertEqual(applied.returns, (Number,))

    def test_multiple_upper_bounds_infer_intersection(self):
        overload = Overload(
            (
                Fn((V("T"),), (String,)),
                Fn((V("T"),), (String,)),
            ),
            (V("T"),),
        )
        applied = apply_overload(
            overload,
            (
                Fn((Number,), (String,)),
                Fn((String,), (String,)),
            ),
        )
        self.assertIsNotNone(applied)
        self.assertTrue(
            same(applied.substitution["T"], I(Number, String))
        )


    def test_generic_lower_bounds_form_reduced_union_when_unrelated(self):
        choose = Overload((V("T"), V("T")), (V("T"),))
        applied = apply_overload(choose, (Int, String))
        self.assertIsNotNone(applied)
        self.assertTrue(same(applied.substitution["T"], U(Int, String)))
        self.assertTrue(same(applied.returns[0], U(Int, String)))

    def test_generic_numeric_lower_bounds_choose_real_for_integer_and_real(self):
        choose = Overload((V("T"), V("T")), (V("T"),))
        applied = apply_overload(choose, (Int, Real))
        self.assertIsNotNone(applied)
        self.assertTrue(same(applied.substitution["T"], Real))
        self.assertEqual(applied.returns, (Real,))

    def test_generic_lower_bound_union_is_order_independent(self):
        choose = Overload((V("T"), V("T")), (V("T"),))
        forward = apply_overload(choose, (Int, String))
        reverse = apply_overload(choose, (String, Int))
        self.assertIsNotNone(forward)
        self.assertIsNotNone(reverse)
        self.assertTrue(same(forward.substitution["T"], reverse.substitution["T"]))




class OverloadResolutionMatrixTests(unittest.TestCase):
    """Exercise selection laws across candidate order, ranks, and type evidence."""

    def test_numeric_winner_is_independent_of_order_and_vectorisation(self):
        numeric = (T.Int, T.Real, T.Number)
        overloads = tuple(T.Overload((typ, typ), (typ,)) for typ in numeric)
        for left, right, rank in product(range(3), range(3), range(3)):
            args = tuple(
                T.ExactList(numeric[index], rank) if rank else numeric[index]
                for index in (left, right)
            )
            expected = numeric[max(left, right)]
            result_type = T.ExactList(expected, rank) if rank else expected
            for order in permutations(overloads):
                with self.subTest(left=left, right=right, rank=rank, order=order):
                    resolved = T.resolve_overload_result(order, args)
                    applied = T.apply_overloads_to_stack(
                        order, T.TypeStack((T.String,) + args)
                    )
                    self.assertIsNotNone(resolved)
                    self.assertIsNotNone(applied)
                    self.assertIs(resolved.overload, overloads[max(left, right)])
                    self.assertIs(applied.overload, resolved.overload)
                    self.assertEqual(applied.stack, T.TypeStack((T.String, result_type)))
                    self.assertEqual(applied.vectorised, rank > 0)

    def test_dominated_and_inapplicable_candidates_do_not_change_winner(self):
        winner = T.Overload((T.Int,), (T.String,))
        candidates = (
            winner,
            T.Overload((T.Number,), (T.Number,)),
            T.Overload((T.U(T.Int, T.String),), (T.Boolean,)),
            T.Overload((T.String,), (T.Int,)),
        )
        for order in permutations(candidates):
            with self.subTest(order=order):
                result = T.resolve_overload_result(order, (T.Int,))
                self.assertIsNotNone(result)
                self.assertIs(result.overload, winner)
                self.assertEqual(result.returns, (T.String,))

    def test_cross_specificity_cannot_be_resolved_by_order_or_return_type(self):
        broad = T.U(T.Int, T.String)
        # One exact match cannot compensate for two weaker matches elsewhere.
        for returns in ((T.String, T.String), (T.Int, T.String)):
            candidates = (
                T.Overload((T.Int, broad, broad), (returns[0],)),
                T.Overload((broad, T.Int, T.Int), (returns[1],)),
                T.Overload((broad, broad, broad), (T.Number,)),
            )
            for order in permutations(candidates):
                with self.subTest(returns=returns, order=order):
                    self.assertIsNone(T.resolve_overload_result(order, (T.Int,) * 3))
                    self.assertIsNone(T.apply_overloads_to_stack(
                        order, T.TypeStack((T.Int,) * 3)
                    ))

    def test_generic_solutions_do_not_leak_between_candidates_or_calls(self):
        generic = T.Overload((T.V("T"),), (T.V("T"),))
        constrained = T.Overload(
            (T.V("T"),), (T.Boolean,),
            generic_constraints=(T.GenericConstraint("T", T.Number),),
        )
        concrete = T.Overload((T.Int,), (T.String,))
        for order in permutations((generic, constrained, concrete)):
            for actual, expected, returns in (
                (T.String, generic, (T.String,)),
                (T.Int, concrete, (T.String,)),
                (T.ExactList(T.String), generic, (T.ExactList(T.String),)),
                (T.String, generic, (T.String,)),
            ):
                with self.subTest(order=order, actual=actual):
                    result = T.resolve_overload_result(order, (actual,))
                    self.assertIsNotNone(result)
                    self.assertIs(result.overload, expected)
                    self.assertEqual(result.returns, returns)
                    if expected is generic:
                        self.assertEqual(result.substitution, {"T": actual})
        self.assertEqual(generic.params, (T.V("T"),))
        self.assertEqual(generic.returns, (T.V("T"),))

    def test_collection_match_beats_scalar_vectorisation_at_each_rank(self):
        scalar = T.Overload((T.Int,), (T.Boolean,))
        for rank in (1, 2, 3):
            collection = T.ExactList(T.Int, rank)
            direct = T.Overload((collection,), (T.String,))
            for order in permutations((scalar, direct)):
                with self.subTest(rank=rank, order=order):
                    applied = T.apply_overloads_to_stack(order, T.TypeStack((collection,)))
                    self.assertIsNotNone(applied)
                    self.assertIs(applied.overload, direct)
                    self.assertFalse(applied.vectorised)
                    self.assertEqual(applied.actual_returns, (T.String,))

    def test_exact_candidate_rejection_does_not_disable_another_candidates_vectorisation(self):
        exact = T.Overload((T.Exact(T.Int),), (T.String,))
        scalar = T.Overload((T.Int,), (T.Boolean,))
        for order in permutations((exact, scalar)):
            with self.subTest(order=order):
                applied = T.apply_overloads_to_stack(
                    order, T.TypeStack((T.ExactList(T.Int, 2),))
                )
                self.assertIsNotNone(applied)
                self.assertIs(applied.overload, scalar)
                self.assertTrue(applied.vectorised)
                self.assertEqual(applied.actual_returns, (
                    T.Tagged(T.ExactList(T.Int, 2), T.DataTag("boolean", depth=2)),
                ))

    def test_union_argument_requires_complete_coverage_by_selected_overload(self):
        partial = T.Overload((T.Int,), (T.Boolean,))
        complete = T.Overload((T.U(T.Number, T.String),), (T.String,))
        for members in permutations((T.Int, T.String)):
            args = (T.U(*members),)
            self.assertIsNone(T.resolve_overload_result((partial,), args))
            for order in permutations((partial, complete)):
                with self.subTest(members=members, order=order):
                    result = T.resolve_overload_result(order, args)
                    self.assertIsNotNone(result)
                    self.assertIs(result.overload, complete)

    def test_wrong_arity_and_types_do_not_consume_or_search_lower_stack_values(self):
        overload = T.Overload((T.Int, T.String), (T.Boolean,))
        for items in ((), (T.String,), (T.String, T.Int), (T.Int, T.String, T.Boolean)):
            with self.subTest(items=items):
                stack = T.TypeStack(items)
                self.assertIsNone(T.apply_overloads_to_stack((overload,), stack))
                self.assertEqual(stack.items, items)

    def test_trait_resolution_uses_current_context_without_retaining_old_evidence(self):
        shape = T.N(Symbol("Shape"))
        printable = T.N(Symbol("Printable"))
        circle = T.N(Symbol("Circle"))
        shape_overload = T.Overload((shape,), (T.String,))
        printable_overload = T.Overload((printable,), (T.Int,))
        for order in permutations((shape_overload, printable_overload)):
            for traits, expected in (
                ({shape.name}, shape_overload),
                ({printable.name}, printable_overload),
                ({shape.name, printable.name}, None),
                (set(), None),
                ({shape.name}, shape_overload),
            ):
                with self.subTest(order=order, traits=traits):
                    ctx = T.Context(trait_impls={circle.name: traits})
                    result = T.resolve_overload_result(order, (circle,), ctx)
                    if expected is None:
                        self.assertIsNone(result)
                    else:
                        self.assertIsNotNone(result)
                        self.assertIs(result.overload, expected)

    def test_union_callable_coverage_requires_every_argument_combination(self):
        branches = tuple(product((T.Int, T.String), repeat=2))
        overloads = tuple(T.Overload(params, (T.String,)) for params in branches)
        expected = T.Fn((T.U(T.Int, T.String),) * 2, (T.String,))
        for order in permutations(overloads):
            with self.subTest(order=order):
                self.assertTrue(T.compatible(T.Overloads(*order), expected))
        for omitted in range(len(overloads)):
            with self.subTest(omitted=branches[omitted]):
                partial = overloads[:omitted] + overloads[omitted + 1:]
                self.assertFalse(T.compatible(T.Overloads(*partial), expected))

    def test_union_callable_checks_each_selected_return_and_branch_ambiguity(self):
        expected = T.Fn((T.U(T.Int, T.String),), (T.String,))
        integer = T.Overload((T.Int,), (T.String,))
        wrong_return = T.Overload((T.String,), (T.Int,))
        for order in permutations((integer, wrong_return)):
            with self.subTest(order=order):
                self.assertFalse(T.compatible(T.Overloads(*order), expected))
        ambiguous = (
            T.Overload((T.Int,), (T.String,)),
            T.Overload((T.Int,), (T.Int,)),
            T.Overload((T.String,), (T.String,)),
        )
        expected = T.Fn((T.U(T.Int, T.String),), (T.U(T.Int, T.String),))
        for order in permutations(ambiguous):
            with self.subTest(order=order):
                self.assertFalse(T.compatible(T.Overloads(*order), expected))


class InlineTraitElementTagConformanceTests(unittest.TestCase):
    def test_structural_trait_requires_exact_element_tag_set(self):
        name = Symbol("read")
        panic = ElementTag(Symbol("Panic"), (ParseError,))
        trait = AnonymousTrait(
            (Symbol("T"),),
            (AnonymousTraitRequirement(name, Overload((V("T"),), (Number,))),),
        )
        ctx = Context()
        ctx.define_structural_overload(
            name,
            Overload((Int,), (Number,), element_tags=frozenset({panic})),
        )
        self.assertFalse(assignable(Int, trait, ctx))

        exact = AnonymousTrait(
            (Symbol("T"),),
            (
                AnonymousTraitRequirement(
                    name,
                    Overload(
                        (V("T"),),
                        (Number,),
                        element_tags=frozenset({panic}),
                    ),
                ),
            ),
        )
        self.assertTrue(assignable(Int, exact, ctx))


class ReflectionMetadataTests(unittest.TestCase):
    def test_intersection_layouts_filter_finite_owners_without_fabricating_type_storage(self):
        from valiance.vtypes.reflection import fields_for_value, value_fields_need_specialization

        env = Environment()
        a, b, view = (N(Symbol(name)) for name in ('A', 'B', 'View'))
        env.define_object(a.name, (T.ObjectAttribute(Symbol('number'), Int),))
        env.define_object(b.name, (T.ObjectAttribute(Symbol('text'), String),), duplication_error='protected')
        env.define_trait(view.name)
        env.add_trait_impl(a.name, view.name)
        owner = I(U(a, b), view)
        metadata = fields_for_value(owner, env)
        self.assertEqual(tuple(layout.owner.typ for layout in metadata.alternatives), (a,))
        self.assertEqual(metadata.value_type, Int)
        self.assertFalse(value_fields_need_specialization(owner, env))
        self.assertFalse(env.reflection_requires_unsafe(owner))
        for nested in (U(owner, String), T.Row(owner, Field(Symbol('number'), Int)),
                       ExactList(owner), T.Tup(owner, String)):
            self.assertFalse(env.reflection_requires_unsafe(nested))
        env.set_object_destructor_effects(b.name, frozenset((ElementTag(Symbol('IO')),)))
        self.assertEqual(env.destructor_effects(owner), frozenset())
        self.assertEqual(env.field_metadata(owner).fields, ())
        self.assertTrue(value_fields_need_specialization(I(view, N(Symbol('Other'))), env))
        env.add_trait_impl(b.name, view.name)
        self.assertTrue(env.reflection_requires_unsafe(owner))
        self.assertEqual({layout.owner.typ for layout in fields_for_value(owner, env).alternatives}, {a, b})
        wrapped = fields_for_value(Tagged(owner, 'marked'), env)
        self.assertEqual({layout.owner.typ for layout in wrapped.alternatives},
                         {Tagged(a, 'marked'), Tagged(b, 'marked')})

    def test_intersection_ownership_proof_retains_generic_and_row_evidence(self):
        env = Environment()
        a, resource, view = (N(Symbol(name)) for name in ('A', 'Resource', 'View'))
        env.define_object(a.name)
        env.define_object(resource.name, duplication_error='protected')
        env.define_trait(view.name, generics=(Symbol('T'),))
        env.add_trait_impl(a.name, view.name)
        self.assertFalse(env.reflection_requires_unsafe(I(a, N(view.name, Int))))
        self.assertTrue(env.reflection_requires_unsafe(I(a, N(view.name, resource))))
        self.assertTrue(env.reflection_requires_unsafe(I(a, T.Row(a, Field(Symbol('hidden'), resource)))))

    def test_intersection_and_nominal_row_release_effects_follow_the_storage_owner(self):
        env = Environment()
        owner, other = N(Symbol('Owner')), N(Symbol('Other'))
        effects = frozenset((ElementTag(Symbol('IO')),))
        env.define_object(owner.name)
        env.define_object(other.name)
        env.set_object_destructor_effects(owner.name, effects)
        self.assertEqual(env.destructor_effects(I(owner, other)), effects)
        self.assertEqual(env.destructor_effects(T.Row(owner, Field(Symbol('number'), Int))), effects)

    def test_nominal_row_views_preserve_physical_storage_and_canonical_owners(self):
        from valiance.vtypes.reflection import fields_for_value, value_fields_need_specialization

        env = Environment()
        owner = N(Symbol('Owner'))
        env.define_object(owner.name, (
            T.ObjectAttribute(Symbol('number'), Int),
            T.ObjectAttribute(Symbol('hidden'), String, access=Symbol('private')),
        ))
        view = T.Row(owner, T.Field(Symbol('number'), Int))
        metadata = fields_for_value(view, env)
        self.assertEqual(metadata, env.field_metadata(owner))
        self.assertEqual(fields_for_value(Tagged(view, 'marked'), env),
                         env.field_metadata(Tagged(owner, 'marked')))
        self.assertFalse(value_fields_need_specialization(view, env))
        self.assertTrue(value_fields_need_specialization(T.Row(V('T'), *view.fields), env))
        self.assertTrue(value_fields_need_specialization(T.Row(N(Symbol('record')), *view.fields), env))
        self.assertEqual(env.field_metadata(view).fields, ())

    def test_variant_value_layouts_flatten_and_preserve_generic_storage(self):
        from valiance.vtypes.reflection import fields_for_value

        env = Environment()
        parent, present, absent = map(Symbol, ('Maybe', 'Maybe.Present', 'Maybe.Absent'))
        env.define_object(present, (T.ObjectAttribute(Symbol('value'), V('T')),), generics=(Symbol('T'),))
        env.define_object(absent, generics=(Symbol('T'),))
        env.define_variant(parent, (present, absent), generics=(Symbol('T'),))
        owner = N(parent, Int)
        metadata = fields_for_value(U(owner, N(present, Int)), env)
        self.assertEqual(tuple(layout.owner.typ for layout in metadata.alternatives),
                         (N(present, Int), N(absent, Int)))
        self.assertEqual(metadata.value_type, Int)
        self.assertEqual(env.field_metadata(owner).fields, ())
        self.assertFalse(env.reflection_requires_unsafe(owner))
        self.assertTrue(env.reflection_requires_unsafe(N(parent, V('U'))))

    def test_variant_permission_checks_recursive_members_and_protected_storage(self):
        env = Environment()
        parent, node, leaf = map(Symbol, ('Tree', 'Tree.Node', 'Tree.Leaf'))
        env.define_object(node, (T.ObjectAttribute(Symbol('child'), N(parent)),))
        env.define_object(leaf)
        env.define_variant(parent, (node, leaf))
        self.assertFalse(env.reflection_requires_unsafe(N(parent)))
        env.define_object(leaf, duplication_error='protected')
        self.assertTrue(env.reflection_requires_unsafe(N(parent)))

    def test_value_union_layouts_preserve_owners_without_fabricating_static_storage(self):
        from valiance.vtypes.reflection import fields_for_value, field_type_evidence, fields_type_owners

        env = Environment()
        a, b = N(Symbol('A')), N(Symbol('B'))
        env.define_object(a.name, (T.ObjectAttribute(Symbol('number'), Int),))
        env.define_object(b.name, (T.ObjectAttribute(Symbol('label'), String),))
        owner = U(a, b)
        self.assertEqual(env.field_metadata(owner).fields, ())
        metadata = fields_for_value(owner, env)
        self.assertEqual({layout.owner.typ for layout in metadata.alternatives}, {a, b})
        self.assertEqual(metadata.value_type, U(Int, String))
        self.assertEqual({field.owner.typ for field in metadata.fields}, {a, b})
        self.assertEqual(field_type_evidence(U(N(Symbol('Field'), a), N(Symbol('Field'), b))), (owner, None))
        self.assertEqual(set(fields_type_owners(U(N(Symbol('Fields'), a), N(Symbol('Fields'), b)))), {a, b})

    def test_partial_storage_release_effects_exclude_owner_destructor(self):
        env = Environment()
        child = N(Symbol("Child"))
        owner = N(Symbol("Owner"))
        child_effects = frozenset((ElementTag(Symbol("IO")),))
        owner_effects = frozenset((ElementTag(Symbol("Random")),))
        env.define_object(child.name)
        env.set_object_destructor_effects(child.name, child_effects)
        env.define_object(owner.name, (T.ObjectAttribute(Symbol("child"), child),))
        env.set_object_destructor_effects(owner.name, owner_effects)
        self.assertEqual(env.destructor_effects(N(Symbol("Uninit"), owner)), child_effects)
        self.assertEqual(env.destructor_effects(owner), owner_effects)
        env.define_object(Symbol("Recursive"), (
            T.ObjectAttribute(Symbol("next"), N(Symbol("Uninit"), N(Symbol("Recursive")))),
        ))
        self.assertEqual(env.destructor_effects(N(Symbol("Uninit"), N(Symbol("Recursive")))), frozenset())

    def test_reflection_authorization_follows_private_nested_storage(self):
        env = Environment()
        secret = N(Symbol("Secret"))
        env.define_object(secret.name, duplication_error="protected")
        env.define_object(Symbol("Envelope"), (
            T.ObjectAttribute(Symbol("secret"), ExactList(secret), Symbol("private")),
        ))
        self.assertTrue(env.reflection_requires_unsafe(N(Symbol("Envelope"))))
        self.assertTrue(env.reflection_requires_unsafe(secret))
        self.assertFalse(env.reflection_requires_unsafe(Int))
        self.assertFalse(env.reflection_requires_unsafe(Tagged(Int, "boolean")))
        self.assertFalse(env.reflection_requires_unsafe(T.Never()))

    def test_reflection_authorization_checks_generic_arguments_and_unknown_types(self):
        env = Environment()
        env.define_object(Symbol("Box"), (
            T.ObjectAttribute(Symbol("value"), V("T")),
        ), generics=(Symbol("T"),))
        self.assertFalse(env.reflection_requires_unsafe(N(Symbol("Box"), Int)))
        self.assertTrue(env.reflection_requires_unsafe(N(Symbol("Box"), V("U"))))
        env.define_object(Symbol("Phantom"), generics=(Symbol("T"),))
        self.assertTrue(env.reflection_requires_unsafe(N(Symbol("Phantom"), V("U"))))
        self.assertTrue(env.reflection_requires_unsafe(V("T")))
        self.assertTrue(env.reflection_requires_unsafe(N(Symbol("UnknownTrait"))))
        self.assertTrue(env.reflection_requires_unsafe(T.Number))
        self.assertTrue(env.reflection_requires_unsafe(T.FFI(Symbol("handle"))))

    def test_reflection_authorization_traverses_structural_types(self):
        env = Environment()
        resource = N(Symbol("Resource"))
        env.define_object(resource.name, duplication_error="protected")
        for typ in (Tup(Int, resource), U(Int, resource), ExactList(resource),
                    Row(Int, Field(Symbol("value"), resource)),
                    Exact(resource), Tagged(resource, "marked")):
            with self.subTest(typ=typ):
                self.assertTrue(env.reflection_requires_unsafe(typ))
        for typ in (Tup(Int, String), U(Int, String), ExactList(Int)):
            with self.subTest(typ=typ):
                self.assertFalse(env.reflection_requires_unsafe(typ))
        self.assertTrue(env.reflection_requires_unsafe(T.Fn((Int,), (Int,))))
        self.assertTrue(env.reflection_requires_unsafe(T.Task((Int,))))

    def test_recursive_reflection_authorization_terminates_with_expanding_generics(self):
        env = Environment()
        node = Symbol("Node")
        env.define_object(node, (
            T.ObjectAttribute(Symbol("next"), N(node, ExactList(V("T")))),
            T.ObjectAttribute(Symbol("value"), V("T")),
        ), generics=(Symbol("T"),))
        self.assertFalse(env.reflection_requires_unsafe(N(node, Int)))
        env.define_object(Symbol("Secret"), duplication_error="protected")
        self.assertTrue(env.reflection_requires_unsafe(N(node, N(Symbol("Secret")))))

    def test_recursive_reflection_authorization_checks_arguments_before_cycle_cut(self):
        env = Environment()
        node = Symbol("Node")
        secret = N(Symbol("Secret"))
        env.define_object(secret.name, duplication_error="protected")
        env.define_object(node, (
            T.ObjectAttribute(Symbol("next"), N(node, secret)),
        ), generics=(Symbol("T"),))
        self.assertTrue(env.reflection_requires_unsafe(N(node, Int)))

    def test_mutually_recursive_reflection_authorization_visits_all_fields(self):
        env = Environment()
        left, right = Symbol("Left"), Symbol("Right")
        env.define_object(left, (T.ObjectAttribute(Symbol("next"), N(right)),))
        env.define_object(right, (T.ObjectAttribute(Symbol("next"), N(left)),))
        self.assertFalse(env.reflection_requires_unsafe(N(left)))
        env.define_object(Symbol("Secret"), duplication_error="protected")
        env.define_object(right, (
            T.ObjectAttribute(Symbol("next"), N(left)),
            T.ObjectAttribute(Symbol("secret"), N(Symbol("Secret"))),
        ))
        self.assertTrue(env.reflection_requires_unsafe(N(left)))

    def test_fields_preserve_order_visibility_and_distinct_identity(self):
        from valiance.vtypes.reflection import Visibility

        env = Environment()
        point = N(Symbol("Point"))
        env.define_object(point.name, (
            T.ObjectAttribute(Symbol("x"), Int, Symbol("public")),
            T.ObjectAttribute(Symbol("y"), Int, Symbol("private")),
            T.ObjectAttribute(Symbol("label"), String),
        ))
        metadata = env.field_metadata(point)
        self.assertEqual([field.name for field in metadata.fields], ["x", "y", "label"])
        self.assertEqual([field.index for field in metadata.fields], [0, 1, 2])
        self.assertEqual([field.visibility for field in metadata.fields], [
            Visibility.PUBLIC, Visibility.PRIVATE, Visibility.READABLE,
        ])
        self.assertNotEqual(metadata.fields[0], metadata.fields[1])
        self.assertTrue(all(field.owner == metadata.owner for field in metadata.fields))
        self.assertEqual(metadata.value_type, U(Int, String))
        self.assertEqual(metadata, env.field_metadata(point))

    def test_generic_owner_and_nested_field_types_are_specialized(self):
        env = Environment()
        scope = TypeVarScope(42, ("T",))
        variable = scope.variable("T")
        box = Symbol("Box")
        env.define_object(box, (
            T.ObjectAttribute(Symbol("value"), variable),
            T.ObjectAttribute(Symbol("nested"), ExactList(N(box, variable))),
        ), generics=(Symbol("T"),))
        integers = env.field_metadata(N(box, Int))
        strings = env.field_metadata(N(box, String))
        self.assertEqual(integers.owner.typ, N(box, Int))
        self.assertEqual(integers.fields[0].type.typ, Int)
        self.assertEqual(integers.fields[1].type.typ, ExactList(N(box, Int)))
        self.assertEqual(strings.fields[0].type.typ, String)
        self.assertNotEqual(integers.fields[0], strings.fields[0])
        self.assertEqual(env.lookup_attribute(box, Symbol("value")), variable)

    def test_unscoped_generic_metadata_and_unknown_fields_keep_type_evidence(self):
        env = Environment()
        box = Symbol("Box")
        env.define_object(box, (T.ObjectAttribute(Symbol("value"), V("T")),),
                          generics=(Symbol("T"),))
        self.assertEqual(env.field_metadata(N(box, Int)).value_type, Int)
        self.assertEqual(env.field_metadata(N(box, V("U"))).value_type, V("U"))

    def test_types_without_managed_storage_have_no_fabricated_fields(self):
        env = Environment()
        env.define_object(Symbol("Empty"))
        # An FFI name must never resolve through a same-spelled managed object.
        env.define_object(Symbol("Int"), (T.ObjectAttribute(Symbol("x"), Int),))
        for typ in (String, N(Symbol("Empty")), T.FFI(Symbol("Int")),
                    ExactList(Int), U(Int, String), T.Never(), T.NoneType(),
                    Tup(Int, String), T.Fn((Int,), (String,))):
            with self.subTest(typ=typ):
                metadata = env.field_metadata(typ)
                self.assertEqual(metadata.fields, ())
                self.assertEqual(metadata.value_type, T.Never())
                self.assertIsNone(metadata.find("missing"))

    def test_primitive_type_has_empty_metadata(self):
        self.assertEqual(Environment().field_metadata(Int).fields, ())

    def test_name_lookup_retains_all_possible_value_types(self):
        env = Environment()
        owner = N(Symbol("Person"))
        env.define_object(owner.name, (
            T.ObjectAttribute(Symbol("name"), String),
            T.ObjectAttribute(Symbol("age"), Int),
        ))
        metadata = env.field_metadata(owner)
        self.assertEqual(metadata.find("age"), metadata.fields[1])
        self.assertIsNone(metadata.find("missing"))
        self.assertEqual(metadata.value_type, U(String, Int))

    def test_qualified_owner_names_and_wrappers_preserve_identity(self):
        env = Environment()
        left = N(Symbol("Box", ("left",)))
        right = N(Symbol("Box", ("right",)))
        for owner in (left, right):
            env.define_object(owner.name, (T.ObjectAttribute(Symbol("value"), Int),))
        self.assertNotEqual(env.field_metadata(left).fields[0],
                            env.field_metadata(right).fields[0])
        tagged = Tagged(left, DataTag(Symbol("validated")))
        metadata = env.field_metadata(tagged)
        self.assertEqual(metadata.owner.typ, tagged)
        self.assertEqual(metadata.fields[0].owner.typ, tagged)
        self.assertEqual(metadata.fields[0].type.typ, Int)
        self.assertEqual(env.field_metadata(Exact(left)).owner.typ, Exact(left))

    def test_descriptors_are_immutable_and_normalized(self):
        from dataclasses import FrozenInstanceError
        from valiance.vtypes.reflection import TypeDescriptor

        self.assertEqual(TypeDescriptor(U(Int, Int)), TypeDescriptor(Int))
        env = Environment()
        owner = N(Symbol("Box"))
        env.define_object(owner.name, (T.ObjectAttribute(Symbol("value"), Int),))
        metadata = env.field_metadata(owner)
        with self.assertRaises(FrozenInstanceError):
            metadata.fields[0].name = "changed"
        self.assertEqual(hash(metadata), hash(env.field_metadata(owner)))


if __name__ == "__main__":
    unittest.main()
