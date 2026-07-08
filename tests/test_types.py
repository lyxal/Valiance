import unittest

from valiance.symbols import Symbol
from valiance.types import (
    C,
    Context,
    DataTag,
    DataTagDefinition,
    ElementTag,
    ElementTagDefinition,
    ElementTagKind,
    Environment,
    ExactList,
    Field,
    Fn,
    GenericConstraint,
    ListExactType,
    ListMinType,
    N,
    NoneType,
    OKType,
    Overload,
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
    optional,
    resolve_overload_result,
)

NUMBER = Symbol("Number")
REAL = Symbol("Real")
INTEGER = Symbol("Integer")
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
Integer = N(INTEGER)
String = N(STRING)
Foo = N(FOO)
Car = N(CAR)
Vehicle = N(VEHICLE)
ParseError = N(PARSE_ERROR)


class TypeLibraryTests(unittest.TestCase):
    def test_symbols_have_value_equality_and_hashing(self):
        left = Symbol("Number")
        right = Symbol("Number")

        self.assertEqual(left, right)
        self.assertEqual({left: Number}[right], Number)

    def test_assignment_does_not_vectorise(self):
        self.assertFalse(assignable(C(ListExactType, Number), Number))
        self.assertTrue(compatible(C(ListExactType, Number), Number))

    def test_numeric_nominal_hierarchy_is_integer_real_number(self):
        self.assertTrue(assignable(Integer, Real))
        self.assertTrue(assignable(Integer, Number))
        self.assertTrue(assignable(Real, Number))
        self.assertFalse(assignable(Real, Integer))
        self.assertFalse(assignable(Number, Real))

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

    def test_absent_tagged_collection_parameter_does_not_vectorise_return(self):
        overload = Overload(
            (Tagged(C(ListExactType, V("T")), DataTag("infinite", absent=True)),),
            (Number,),
        )

        applied = apply_overload(overload, (C(ListExactType, Number),))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.actual_returns, (Number,))
        self.assertFalse(applied.vectorised)

    def test_constructed_tags_are_transparent_to_generic_overload_solving(self):
        overload = Overload(
            (C(ListExactType, V("Item")), Integer),
            (C(ListExactType, V("Item")),),
        )
        source = Tagged(C(ListExactType, Integer), DataTag("infinite"))

        applied = apply_overload(overload, (source, Integer))

        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["Item"], Integer)
        self.assertEqual(applied.params, (C(ListExactType, Integer), Integer))
        self.assertEqual(applied.actual_returns, (C(ListExactType, Integer),))

    def test_apply_overload_marks_vectorisation(self):
        overload = Overload((Number, Number), (Number,))

        applied = apply_overload(
            overload,
            (C(ListExactType, Number), C(ListExactType, Number)),
        )

        self.assertIsNotNone(applied)
        self.assertTrue(applied.vectorised)
        self.assertEqual(applied.actual_returns, (C(ListExactType, Number),))

    def test_collection_item_type_looks_through_tags(self):
        self.assertEqual(
            collection_item_type(Tagged(C(ListExactType, Number), "infinite")),
            Number,
        )

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

    def test_concrete_overload_beats_union_overload(self):
        concrete = Overload((Number,), (Number,))
        unioned = Overload((U(Number, String),), (Number,))
        result = resolve_overload_result((concrete, unioned), (Number,))
        self.assertIsNotNone(result)
        self.assertIs(result.overload, concrete)

    def test_numeric_tower_specificity_selects_narrowest_overload(self):
        integer = Overload((Integer, Integer), (Integer,))
        real = Overload((Real, Real), (Real,))
        number = Overload((Number, Number), (Number,))

        result = resolve_overload_result((number, real, integer), (Integer, Integer))

        self.assertIsNotNone(result)
        self.assertIs(result.overload, integer)

    def test_vectorised_numeric_specificity_selects_narrowest_overload(self):
        integer = Overload((Integer, Integer), (Integer,))
        real = Overload((Real, Real), (Real,))
        number = Overload((Number, Number), (Number,))

        applied = apply_overloads_to_stack(
            (number, real, integer),
            TypeStack((C(ListExactType, Integer), C(ListExactType, Integer))),
        )

        self.assertIsNotNone(applied)
        self.assertIs(applied.overload, integer)
        self.assertEqual(applied.stack, TypeStack((C(ListExactType, Integer),)))

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


if __name__ == "__main__":
    unittest.main()
