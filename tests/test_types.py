import unittest

from valiance.types import (
    C,
    Coll,
    Context,
    Fn,
    N,
    NoneType,
    Overload,
    Overloads,
    Specificity,
    U,
    V,
    _combine_all,
    _match_specificity,
    _solve,
    _substitute,
    apply_overload,
    apply_overload_to_stack,
    assignable,
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
        self.assertFalse(assignable(C(Coll.LIST_EXACT, Number), Number))
        self.assertTrue(compatible(C(Coll.LIST_EXACT, Number), Number))

    def test_nested_list_solves_reduce_t_as_list(self):
        constraints = _solve(C(Coll.LIST_EXACT, V("T")), C(Coll.LIST_EXACT, Number, 2))
        self.assertIsNotNone(constraints)
        t = _combine_all(constraints["T"])
        self.assertEqual(t, C(Coll.LIST_EXACT, Number))

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
        expected = Fn((C(Coll.LIST_EXACT, Number), C(Coll.LIST_EXACT, Number)), (C(Coll.LIST_EXACT, Number),))
        self.assertTrue(compatible(plus, expected))

    def test_reduce_signature_substitution(self):
        xs_type = C(Coll.LIST_EXACT, Number, 2)
        constraints = _solve(C(Coll.LIST_EXACT, V("T")), xs_type)
        subst = {"T": _combine_all(constraints["T"])}
        function_param = _substitute(Fn((V("T"), V("T")), (V("T"),)), subst)
        self.assertEqual(
            function_param,
            Fn((C(Coll.LIST_EXACT, Number), C(Coll.LIST_EXACT, Number)), (C(Coll.LIST_EXACT, Number),)),
        )

    def test_apply_overload_reports_substitution_and_actual_returns(self):
        reduce = Overload(
            (C(Coll.LIST_EXACT, V("T")), Fn((V("T"), V("T")), (V("T"),))),
            (V("T"),),
        )
        applied = apply_overload(
            reduce,
            (C(Coll.LIST_EXACT, Number, 2), Fn((Number, Number), (Number,))),
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied.substitution["T"], C(Coll.LIST_EXACT, Number))
        self.assertEqual(applied.params[0], C(Coll.LIST_EXACT, Number, 2))
        self.assertEqual(applied.returns, (C(Coll.LIST_EXACT, Number),))

    def test_apply_overload_to_stack_can_infer_missing_inputs(self):
        plus = Overload((Number, Number), (Number,))
        applied = apply_overload_to_stack(plus, (), infer_missing=True)
        self.assertIsNotNone(applied)
        self.assertEqual(applied.inputs, (Number, Number))
        self.assertEqual(applied.stack, (Number,))

    def test_apply_overload_to_stack_requires_inputs_without_inference(self):
        plus = Overload((Number, Number), (Number,))
        self.assertIsNone(apply_overload_to_stack(plus, (Number,), infer_missing=False))
        applied = apply_overload_to_stack(plus, (Number, Number), infer_missing=False)
        self.assertIsNotNone(applied)
        self.assertEqual(applied.stack, (Number,))

    def test_merge_types_and_stacks(self):
        self.assertEqual(merge_types(Number, String), U(Number, String))
        self.assertEqual(merge_stacks((Number,), ()), (optional(Number),))
        self.assertEqual(merge_stacks((Number,), (String,)), (U(Number, String),))

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
        self.assertEqual(_match_specificity(N("Circle"), N("Shape"), ctx), Specificity.TRAIT)


if __name__ == "__main__":
    unittest.main()
