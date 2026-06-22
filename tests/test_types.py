import unittest

from valiance.types import (
    C,
    Context,
    Fn,
    ListExactType,
    ListMinType,
    N,
    NoneType,
    Overload,
    Overloads,
    Specificity,
    TypeStack,
    U,
    V,
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

Number = N("Number")
String = N("String")


class TypeLibraryTests(unittest.TestCase):
    def test_assignment_does_not_vectorise(self):
        self.assertFalse(assignable(C(ListExactType, Number), Number))
        self.assertTrue(compatible(C(ListExactType, Number), Number))

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

    def test_cross_specificity_is_ambiguous(self):
        left = Overload((Number, U(Number, String)), (Number,))
        right = Overload((U(Number, String), Number), (Number,))
        self.assertIsNone(resolve_overload_result((left, right), (Number, Number)))

    def test_trait_specificity(self):
        ctx = Context(trait_impls={"Circle": {"Shape"}})
        self.assertTrue(compatible(N("Circle"), N("Shape"), ctx))
        self.assertEqual(
            _match_specificity(N("Circle"), N("Shape"), ctx),
            Specificity.TRAIT,
        )


if __name__ == "__main__":
    unittest.main()
