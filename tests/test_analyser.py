import unittest

from valiance.analysis import (
    Analyser,
    AnalysisBranch,
    BranchSet,
    BranchVariables,
    InputMode,
    analyse,
    analyse_function,
    default_environment,
)
from valiance.analysis.builtins import BUILTIN_ELEMENTS
from valiance.asts import (
    BreakNode,
    ElementNode,
    FieldAccessNode,
    ForNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    NumberLiteralNode,
    StringLiteralNode,
    TypedFunctionNode,
)
from valiance.parsing import parse
from valiance.symbols import Symbol
from valiance.types import (
    AppliedElement,
    C,
    Environment,
    Field,
    Fn,
    FunctionType,
    ListExactType,
    N,
    Never,
    NoMatchingOverload,
    NoneType,
    ObjectAttribute,
    Overload,
    Overloads,
    Row,
    TypeStack,
    U,
    UnknownElement,
    V,
    optional,
)
from valiance.types.default_types import Boolean

NUMBER = Symbol("Number")
STRING = Symbol("String")
BOOL = Symbol("Bool")
BAX = Symbol("Bax")
INTEGER = Symbol("Integer")

Number = N(NUMBER)
String = N(STRING)
Bool = N(BOOL)
PLUS = Symbol("+")
SLASH = Symbol("/")
AMB = Symbol("amb")
BAR = Symbol("bar")
FOO_FIELD = Symbol("foo")
COND = Symbol("cond")
FOO = Symbol("Foo")
DOUBLE = Symbol("double")
EQUALS = Symbol("==")
IS_POSITIVE = Symbol("positive?")
LENGTH = Symbol("length")
ITEM = Symbol("item")
MISSING = Symbol("missing")
NAME = Symbol("name")
OP = Symbol("op")
X = Symbol("x")
Y = Symbol("y")


class AnalyserTests(unittest.TestCase):
    def test_default_environment_includes_builtin_plus(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(PLUS),
            ],
        )

        self.assertEqual([node.typ for node in typed], [Number, Number, Number])

    def test_builtin_elements_are_declared_before_installation(self):
        names = {element.name for element in BUILTIN_ELEMENTS}

        self.assertIn(PLUS, names)
        self.assertIn(SLASH, names)

    def test_default_environment_includes_generic_reduce_and_map(self):
        env = default_environment()
        reduce_result = env.apply(
            SLASH,
            TypeStack(
                (
                    C(ListExactType, Number),
                    Fn((Number, Number), (Number,)),
                )
            ),
        )
        self.assertIsInstance(reduce_result, AppliedElement)
        self.assertEqual(reduce_result.application.stack, TypeStack((Number,)))

        map_result = env.apply(
            Symbol("map"),
            TypeStack(
                (
                    C(ListExactType, Number),
                    Fn((Number,), (String,)),
                )
            ),
        )
        self.assertIsInstance(map_result, AppliedElement)
        self.assertEqual(
            map_result.application.stack,
            TypeStack((C(ListExactType, String),)),
        )

    def test_element_uses_environment_overloads_and_updates_stack(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(PLUS),
            ],
            env,
        )

        self.assertEqual([node.typ for node in typed], [Number, Number, Number])

    def test_non_inference_element_rejects_ambiguous_overload(self):
        env = Environment()
        env.define_overload(AMB, Overload((Number,), (Number,)))
        env.define_overload(AMB, Overload((Number,), (String,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Number,)))),
            (ElementNode(AMB),),
        )

        self.assertEqual(len(branches), 0)
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "ambiguous overloads for element 'amb' with stack [Number]",
            analyser.diagnostics[0],
        )

    def test_unknown_element_is_untyped(self):
        typed = analyse([ElementNode(MISSING)], Environment())
        self.assertIsNone(typed[0].typ)

    def test_diagnostics_include_source_location_when_available(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("missing"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: unknown element 'missing'"],
        )

    def test_environment_distinguishes_unknown_from_no_matching_overload(self):
        env = Environment()
        self.assertIsInstance(env.apply(MISSING, TypeStack()), UnknownElement)

        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        self.assertIsInstance(
            env.apply(PLUS, TypeStack((Number,))),
            NoMatchingOverload,
        )

    def test_no_matching_overload_applies_failed_stack_shape(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        result = env.apply(PLUS, TypeStack((String, String)))

        self.assertIsInstance(result, NoMatchingOverload)
        self.assertEqual(result.stack, TypeStack((Never(),)))
        self.assertEqual(result.params, (Number, Number))
        self.assertEqual(result.actual_returns, (Never(),))

    def test_no_matching_overload_pops_expected_inputs_on_underflow(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        result = env.apply(PLUS, TypeStack((Number,)))

        self.assertIsInstance(result, NoMatchingOverload)
        self.assertEqual(result.stack, TypeStack((Never(),)))

    def test_overload_sets_require_fixed_shape(self):
        env = Environment()
        env.define_overload(OP, Overload((Number,), (Number,)))

        with self.assertRaises(ValueError):
            env.define_overload(OP, Overload((Number, Number), (Number,)))

        with self.assertRaises(ValueError):
            env.define_overload(OP, Overload((Number,), (Number, Number)))

    def test_environment_tracks_object_attributes(self):
        env = Environment()

        env.define_object(
            FOO,
            (
                ObjectAttribute(BAR, N(BAX)),
                ObjectAttribute(NAME, String),
            ),
        )

        self.assertTrue(env.object_exists(FOO))
        self.assertFalse(env.object_exists(MISSING))
        self.assertEqual(env.lookup_attribute(FOO, BAR), N(BAX))
        self.assertTrue(env.has_attribute(FOO, NAME))
        self.assertFalse(env.has_attribute(FOO, MISSING))
        self.assertIsNone(env.lookup_attribute(MISSING, BAR))

    def test_child_scope_reads_outer_overloads_and_objects(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        env.define_object(FOO, (ObjectAttribute(BAR, String),))

        child = env.child_scope()

        self.assertEqual(child.overloads_for(PLUS), env.overloads_for(PLUS))
        self.assertEqual(child.lookup_attribute(FOO, BAR), String)

    def test_analyser_can_analyse_one_branch_block(self):
        analyser = Analyser(Environment())
        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch()),
            (NumberLiteralNode("1"),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_branch_set_condition_validation_pops_control_value(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch()),
            (NumberLiteralNode("1"),),
        )
        branches = branches.require_stack_top_assignable(Number, analyser.env.context)
        branches = branches.pop_stack_top()

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack())
        self.assertEqual([node.typ for node in branch.typed_body], [Number])

    def test_branch_set_condition_validation_rejects_any_non_bool_path(self):
        env = Environment()
        env.define_overload(COND, Overload((), (Boolean,)))
        env.define_overload(COND, Overload((), (Number,)))
        analyser = Analyser(env)
        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(input_mode=InputMode.INFER_INPUTS)),
            (ElementNode(COND),),
        )
        branches = branches.require_stack_top_assignable(Bool, env.context)

        self.assertEqual(len(branches), 0)

    def test_object_attributes_cannot_be_declared_twice(self):
        env = Environment()

        with self.assertRaises(ValueError):
            env.define_object(
                FOO,
                (
                    ObjectAttribute(BAR, Number),
                    ObjectAttribute(BAR, String),
                ),
            )

    def test_function_infers_missing_inputs(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(body=(ElementNode(PLUS),)), env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_function_infers_overload_set_when_missing_inputs_are_ambiguous(self):
        typed = analyse([FunctionNode(body=(ElementNode(PLUS),))])

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Number, Number), (Number,)),
                Overload((String, String), (String,)),
            ),
        )

    def test_unannotated_named_parameter_specializes_from_overload_use(self):
        typed = analyse(
            [
                FunctionNode(
                    params=(FunctionParam(X, None),),
                    body=(
                        ElementNode(PLUS),
                    ),
                )
            ]
        )

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Number,), (Number,)),
                Overload((String,), (String,)),
            ),
        )

    def test_repeated_unannotated_named_parameter_specializes_from_overload_use(self):
        typed = analyse(
            [
                FunctionNode(
                    params=(FunctionParam(X, None),),
                    body=(
                        GetVariableNode(X),
                        GetVariableNode(X),
                        ElementNode(PLUS),
                    ),
                )
            ]
        )
        function = typed[0]

        self.assertEqual(
            function.typ,
            Overloads(
                Overload((Number,), (Number,)),
                Overload((String,), (String,)),
            ),
        )
        self.assertIsInstance(function, TypedFunctionNode)
        self.assertEqual(
            [
                [body_node.typ for body_node in overload.body]
                for overload in function.overloads
            ],
            [[Number, Number, Number], [String, String, String]],
        )

    def test_overloaded_function_node_keeps_typed_body_per_overload(self):
        typed = analyse([FunctionNode(body=(ElementNode(PLUS),))])
        function = typed[0]

        self.assertIsInstance(function, TypedFunctionNode)
        self.assertEqual(
            [overload.typ for overload in function.overloads],
            [
                Fn((Number, Number), (Number,)),
                Fn((String, String), (String,)),
            ],
        )
        self.assertEqual(
            [
                [body_node.typ for body_node in overload.body]
                for overload in function.overloads
            ],
            [[Number], [String]],
        )

    def test_overloaded_function_node_drops_never_returning_overloads(self):
        typed = analyse([FunctionNode(body=(ElementNode(PLUS), ElementNode(SLASH)))])

        match typed[0].typ:
            case FunctionType(returns=returns):
                self.assertNotIn(Never(), returns)
            case overload_set:
                self.assertTrue(
                    all(
                        not any(ret == Never() for ret in overload.returns)
                        for overload in overload_set.overloads
                    )
                )

    def test_function_inference_suppresses_trimmed_branch_diagnostics(self):
        analyser = Analyser(default_environment())

        typ = analyser.analyse_function(
            FunctionNode(body=(ElementNode(PLUS), ElementNode(DOUBLE)))
        )

        self.assertEqual(typ, Fn((Number, Number), (Number,)))
        self.assertEqual(analyser.diagnostics, [])

    def test_function_empty_params_do_not_infer_missing_inputs(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(params=(), body=(ElementNode(PLUS),)), env)

        self.assertIsNone(typ)

    def test_function_empty_params_can_return_literal(self):
        typ = analyse_function(
            FunctionNode(params=(), body=(NumberLiteralNode("1"),)),
            Environment(),
        )

        self.assertEqual(typ, Fn((), (Number,)))

    def test_omitted_returns_keep_only_top_stack_value(self):
        env = Environment()
        node = FunctionNode(
            params=(),
            body=(NumberLiteralNode("1"), NumberLiteralNode("2")),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((), (Number,)))

    def test_explicit_empty_returns_return_no_values(self):
        env = Environment()
        node = FunctionNode(
            params=(),
            body=(NumberLiteralNode("1"),),
            returns=(),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((), ()))

    def test_function_uses_explicit_params(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        node = FunctionNode(
            params=(
                FunctionParam(X, Number),
                FunctionParam(Y, Number),
            ),
            body=(ElementNode(PLUS),),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_function_infers_row_constraint_from_field_access(self):
        typ = analyse_function(
            FunctionNode(body=(FieldAccessNode(BAR),)),
            Environment(),
        )

        self.assertEqual(typ, Fn((Row(V("@1"), Field(BAR, V("@2"))),), (V("@2"),)))

    def test_chained_field_access_refines_nested_row_constraint(self):
        typ = analyse_function(
            FunctionNode(body=(FieldAccessNode(BAR), FieldAccessNode(NAME))),
            Environment(),
        )

        self.assertEqual(
            typ,
            Fn(
                (Row(V("@1"), Field(BAR, Row(V("@2"), Field(NAME, V("@3"))))),),
                (V("@3"),),
            ),
        )

    def test_function_uses_explicit_row_parameter_for_field_access(self):
        node = FunctionNode(
            params=(FunctionParam(X, Row(N(FOO), Field(BAR, String))),),
            body=(FieldAccessNode(BAR),),
        )

        typ = analyse_function(node, Environment())

        self.assertEqual(typ, Fn((Row(N(FOO), Field(BAR, String)),), (String,)))

    def test_if_condition_refines_row_field_from_later_numeric_use(self):
        node = FunctionNode(
            params=(FunctionParam(X),),
            body=(
                GetVariableNode(X),
                FieldAccessNode(FOO_FIELD),
                ElementNode(Symbol("dup")),
                IfNode(
                    condition=(ElementNode(IS_POSITIVE),),
                    then_branch=(ElementNode(DOUBLE),),
                    else_branch=(NumberLiteralNode("0"),),
                ),
            ),
        )

        typ = analyse_function(node, default_environment())

        self.assertEqual(typ, Fn((Row(V("x"), Field(FOO_FIELD, Number)),), (Number,)))

    def test_if_branches_refine_row_field_collection_element_type(self):
        node = FunctionNode(
            params=(FunctionParam(X),),
            body=(
                GetVariableNode(X),
                FieldAccessNode(FOO_FIELD),
                ElementNode(Symbol("dup")),
                IfNode(
                    condition=(
                        ElementNode(LENGTH),
                        NumberLiteralNode("2"),
                        ElementNode(EQUALS),
                    ),
                    then_branch=(ElementNode(DOUBLE),),
                    else_branch=(
                        NumberLiteralNode("0"),
                        ElementNode(PLUS),
                    ),
                ),
            ),
        )

        typ = analyse_function(node, default_environment())

        self.assertEqual(
            typ,
            Fn(
                (Row(V("x"), Field(FOO_FIELD, C(ListExactType, Number))),),
                (C(ListExactType, Number),),
            ),
        )

    def test_field_access_uses_environment_object_attributes(self):
        env = Environment()
        env.define_object(FOO, (ObjectAttribute(BAR, String),))

        branches = Analyser(env).analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((N(FOO),)))),
            (FieldAccessNode(BAR),),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack((String,)))
        self.assertEqual([node.typ for node in branch.typed_body], [String])

    def test_explicit_non_niladic_function_cycles_params_on_underflow(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        node = FunctionNode(
            params=(
                FunctionParam(X, Number),
                FunctionParam(Y, Number),
            ),
            body=(ElementNode(PLUS), ElementNode(PLUS)),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_branch_variables_are_branch_local(self):
        number_vars = BranchVariables()
        string_vars = BranchVariables()

        number_vars, number_error = number_vars.write(X, Number)
        string_vars, string_error = string_vars.write(X, String)

        self.assertIsNone(number_error)
        self.assertIsNone(string_error)
        self.assertEqual(number_vars.read(X), Number)
        self.assertEqual(string_vars.read(X), String)

    def test_branch_variables_reject_incompatible_reassignment(self):
        variables = BranchVariables(function_locals=((X, Number),))

        updated, diagnostic = variables.write(X, String)

        self.assertIsNone(updated)
        self.assertEqual(
            diagnostic,
            "cannot assign String to variable 'x' of type Number",
        )

    def test_branch_variables_allow_assignable_reassignment(self):
        ctx = Environment().context
        ctx.trait_impls.setdefault(INTEGER, set()).add(NUMBER)
        variables = BranchVariables(function_locals=((X, Number),))

        updated, diagnostic = variables.write(X, N(INTEGER), ctx=ctx)

        self.assertIsNone(diagnostic)
        self.assertEqual(updated.read(X), Number)

    def test_branch_variables_check_existing_block_local_assignment(self):
        variables = BranchVariables(block_locals=((ITEM, Number),))

        updated, diagnostic = variables.write(ITEM, String)

        self.assertIsNone(updated)
        self.assertEqual(
            diagnostic,
            "cannot assign String to variable 'item' of type Number",
        )

    def test_branch_variables_reject_parameter_writes(self):
        variables = BranchVariables(parameters=((X, Number),))

        updated, diagnostic = variables.write(X, String)

        self.assertIsNone(updated)
        self.assertEqual(diagnostic, "cannot assign to read-only parameter 'x'")

    def test_branch_variables_shadow_captures_on_write(self):
        variables = BranchVariables(captures=((X, Number),))

        updated, diagnostic = variables.write(X, String)

        self.assertIsNone(diagnostic)
        self.assertEqual(updated.read(X), String)
        self.assertEqual(updated.captures, ((X, Number),))

    def test_branch_variables_drop_block_locals(self):
        variables = BranchVariables().with_block_local(ITEM, Number)

        self.assertEqual(variables.read(ITEM), Number)
        self.assertIsNone(variables.drop_block_locals().read(ITEM))

    def test_function_return_annotation_must_match(self):
        env = Environment()
        node = FunctionNode(body=(NumberLiteralNode("1"),), returns=(String,))

        self.assertIsNone(analyse_function(node, env))

    def test_top_level_function_node_is_typed_and_pushed(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        node = FunctionNode(body=(ElementNode(PLUS),))

        typed = analyse([node], env)

        self.assertEqual(typed[0].typ, Fn((Number, Number), (Number,)))

    def test_for_loop_without_break_returns_none(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((C(ListExactType, Number),)))),
            (
                ForNode(
                    variable=ITEM,
                    body=(GetVariableNode(ITEM),),
                ),
            ),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack((NoneType(),)))
        self.assertIsNone(branch.variables.read(ITEM))

    def test_for_loop_break_returns_optional_break_type(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((C(ListExactType, Number),)))),
            (
                ForNode(
                    variable=ITEM,
                    body=(BreakNode(values=(GetVariableNode(ITEM),)),),
                ),
            ),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack((optional(Number),)))
        self.assertIsNone(branch.break_type)

    def test_for_loop_collects_break_types_from_if_branches(self):
        env = Environment()
        env.define_overload(COND, Overload((), (Boolean,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((C(ListExactType, Number),)))),
            (
                ForNode(
                    variable=ITEM,
                    body=(
                        IfNode(
                            condition=(ElementNode(COND),),
                            then_branch=(BreakNode(values=(NumberLiteralNode("1"),)),),
                            else_branch=(BreakNode(values=(StringLiteralNode("x"),)),),
                        ),
                    ),
                ),
            ),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack((optional(U(Number, String)),)))

    def test_list_literal_infers_union_item_type(self):
        typed = analyse(
            [
                ListLiteralNode(
                    (
                        (NumberLiteralNode("1"),),
                        (StringLiteralNode("x"),),
                    )
                )
            ],
            Environment(),
        )

        self.assertEqual(typed[0].typ, C(ListExactType, U(Number, String)))

    def test_empty_list_literal_requires_annotation_or_cast(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch()),
            (ListLiteralNode(),),
        )

        self.assertEqual(len(branches), 0)
        self.assertEqual(
            analyser.diagnostics,
            ["empty list literal requires a type annotation or cast"],
        )

    def test_list_literal_forks_stack_and_pops_max_consumed_inputs(self):
        analyser = Analyser(default_environment())

        branches = analyser.analyse_block(
            BranchSet.one(
                AnalysisBranch(stack=TypeStack((String, Number, Number)))
            ),
            (
                ListLiteralNode(
                    (
                        (ElementNode(PLUS),),
                        (ElementNode(DOUBLE),),
                    )
                ),
            ),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(
            branch.stack,
            TypeStack((String, C(ListExactType, Number))),
        )

    def test_list_literal_items_contribute_to_function_input_inference(self):
        node = FunctionNode(
            body=(
                ListLiteralNode(
                    (
                        (ElementNode(DOUBLE),),
                        (NumberLiteralNode("1"),),
                    )
                ),
            ),
        )

        typ = analyse_function(node, default_environment())

        self.assertEqual(typ, Fn((Number,), (C(ListExactType, Number),)))


if __name__ == "__main__":
    unittest.main()
