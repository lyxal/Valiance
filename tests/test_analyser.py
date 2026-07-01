import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import (
    Analyser,
    AnalysisBranch,
    BranchSet,
    BranchVariables,
    InputMode,
    analyse,
    analyse_function,
    analyse_function_details,
    default_environment,
)
from valiance.analysis.builtins import BUILTIN_ELEMENTS
from valiance.asts import (
    BreakNode,
    CallNode,
    ElementNode,
    FieldAccessNode,
    ForNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    NumberLiteralNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
)
from valiance.modules import ModuleLoader
from valiance.parsing import parse
from valiance.symbols import Symbol
from valiance.types import (
    AppliedElement,
    C,
    DataTag,
    Environment,
    ExactList,
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
    Tagged,
    TypeStack,
    U,
    UnknownElement,
    V,
    WithTag,
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

    def test_modifier_arguments_bind_to_function_parameters(self):
        env = default_environment()
        env.define_overload(
            OP,
            Overload(
                (
                    Fn((Number,), (Number,)),
                    C(ListExactType, Number),
                    Fn((Number,), (Number,)),
                ),
                (String,),
            ),
        )

        typed = analyse(parse("[1, 2] op: (double, double)"), env)

        self.assertEqual(typed[-1].typ, String)

    def test_colon_function_argument_is_attached_as_typed_function(self):
        typed = analyse(parse("[1, 2, 3] map: double"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, C(ListExactType, Number))
        self.assertEqual(len(typed[-1].modifier_args), 1)
        modifier = typed[-1].modifier_args[0]
        self.assertIsInstance(modifier, TypedFunctionNode)
        self.assertEqual(modifier.typ, Fn((Number,), (Number,)))

    def test_colon_function_arguments_must_match_function_parameter_count(self):
        analyser = Analyser()

        analyser.analyse(parse("[1, 2, 3] map: (double, double)"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:11: element 'map' expects 1 ':' function argument(s), got 2"],
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
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].overload_index, 0)
        self.assertEqual(
            typed[-1].overload.overload,
            Overload((Number, Number), (Number,)),
        )
        self.assertFalse(typed[-1].overload.vectorised)

    def test_element_records_vectorised_overload_application(self):
        typed = analyse(parse("[1, 2, 3] + [4, 5, 6]"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(
            typed[-1].overload.actual_returns,
            (C(ListExactType, Number),),
        )

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

    def test_object_declaration_registers_constructor_fields_and_friendly_element(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
object Person =>
  $name: String
  $age: Number
  define label -> String => $self.name
end
Person("Ada", 36) $.name
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.env.lookup_attribute(Symbol("Person"), NAME), String)
        self.assertEqual(
            analyser.env.overloads_for(Symbol("Person"))[0],
            Overload((String, Number), (N(Symbol("Person")),)),
        )
        self.assertTrue(analyser.env.overloads_for(Symbol("Person::label")))

    def test_object_member_access_levels_are_enforced(self):
        private_read = Analyser(Environment())
        private_read.analyse(
            parse(
                """
object Secret =>
  private $code: String
end
Secret("x") $.code
"""
            )
        )
        self.assertEqual(
            private_read.diagnostics,
            ["5:13: type Secret has no known field 'code'"],
        )

        readable_write = Analyser(Environment())
        readable_write.analyse(
            parse(
                """
object Person =>
  $name: String
end
Person("Ada") | $.name = "Grace"
"""
            )
        )
        self.assertEqual(
            readable_write.diagnostics,
            ["5:17: type Person has no writable field 'name'"],
        )

        public_write = Analyser(Environment())
        typed = public_write.analyse(
            parse(
                """
object Person =>
  public $name: String
end
Person("Ada") | $.name = "Grace"
"""
            )
        )
        self.assertEqual(public_write.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Person")))

    def test_object_friendly_elements_can_read_private_and_write_readable_fields(
        self,
    ):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
object Secret =>
  private $code: String
  $label: String
  define reveal -> String => $self.code
  define relabel(label: String) -> Secret => $self.label = $label
end
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(analyser.env.overloads_for(Symbol("Secret::reveal")))
        self.assertTrue(analyser.env.overloads_for(Symbol("Secret::relabel")))

    def test_trait_and_variant_declarations_register_relationships(self):
        env = Environment()
        analyser = Analyser(env)

        analyser.analyse(
            parse(
                """
trait Shape => extend area -> Number end
object Circle =>
  $radius: Number
end
object Circle as Shape => end
variant Maybe =>
  Some => $value: Number end
  None => end
end
"""
            )
        )

        self.assertTrue(env.context.implements(Symbol("Circle"), Symbol("Shape")))
        self.assertEqual(
            env.context.variant_members[Symbol("Maybe.Some")],
            Symbol("Maybe"),
        )
        self.assertEqual(
            env.lookup_variant(Symbol("Maybe")).members[0],
            Symbol("Maybe.Some"),
        )

    def test_enum_declaration_registers_niladic_members(self):
        env = Environment()
        analyser = Analyser(env)

        typed = analyser.analyse(parse("enum Colour => RED GREEN end\nColour.RED"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Colour")))
        self.assertTrue(env.overloads_for(Symbol("Colour.RED")))

    def test_match_on_enum_requires_all_members_without_default(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
enum Colour => RED GREEN BLUE end
Colour.RED
match =>
  as :RED => "red"
  as :GREEN => "green"
end
"""
            )
        )

        self.assertEqual(
            analyser.diagnostics,
            ["4:1: non-exhaustive match for Colour; missing cases: Colour.BLUE"],
        )

    def test_match_on_variant_is_exhaustive_by_member_cases(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(
            parse(
                """
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(1)
match =>
  as :Some => "some"
  as :None => "none"
end
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

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

    def test_declared_return_refines_inferred_row_field_type(self):
        details = analyse_function_details(
            FunctionNode(body=(FieldAccessNode(NAME),), returns=(String,)),
            Environment(),
        )

        self.assertIsNotNone(details)
        self.assertEqual(
            details.typ,
            Fn((Row(V("@1"), Field(NAME, String)),), (String,)),
        )
        typed_field = details.overloads[0].body[-1]
        self.assertEqual(typed_field.typ, String)

    def test_nominal_object_satisfies_inferred_row_element_parameter(self):
        source = """
object Person =>
  $name: String
  $age: Number
end

define getName -> String => $.name

$joe = Person("Joe", 67)
getName $joe
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        definition = typed[1]
        self.assertIsInstance(definition, TypedFunctionNode)
        self.assertEqual(
            definition.typ,
            Fn((Row(V("@1"), Field(NAME, String)),), (String,)),
        )
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, String)

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

    def test_call_node_calls_function_from_stack_with_explicit_arguments(self):
        typed = analyse(
            [
                FunctionNode(body=(ElementNode(PLUS),)),
                CallNode(args=(NumberLiteralNode("1"), NumberLiteralNode("2"))),
            ],
        )

        self.assertEqual(typed[-1].typ, Number)
        self.assertIsInstance(typed[-1], TypedCallNode)
        self.assertEqual(typed[-1].overload.params, (Number, Number))
        self.assertEqual(typed[-1].overload.actual_returns, (Number,))
        self.assertFalse(typed[-1].overload.vectorised)

    def test_call_node_falls_back_to_stack_values_for_missing_arguments(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                FunctionNode(body=(ElementNode(PLUS),)),
                CallNode(args=(NumberLiteralNode("2"),)),
            ],
        )

        self.assertEqual(typed[0].typ, Number)
        self.assertIsInstance(typed[1], TypedFunctionNode)
        self.assertEqual(typed[2].typ, Number)
        self.assertEqual(typed[3].typ, Number)

    def test_call_node_resolves_overloaded_function_type(self):
        typed = analyse(
            [
                FunctionNode(body=(ElementNode(PLUS),)),
                CallNode(args=(StringLiteralNode("a"), StringLiteralNode("b"))),
            ],
        )

        self.assertEqual(typed[-1].typ, String)
        self.assertIsInstance(typed[-1], TypedCallNode)
        self.assertEqual(typed[-1].overload.params, (String, String))
        self.assertEqual(typed[-1].overload.actual_returns, (String,))

    def test_string_interpolation_has_string_type(self):
        typed = analyse(
            [
                StringInterpolationNode(
                    (
                        "x=",
                        (NumberLiteralNode("1"),),
                        ", doubled=",
                        (NumberLiteralNode("2"), ElementNode(DOUBLE)),
                    )
                ),
            ],
        )

        self.assertEqual(typed[-1].typ, String)

    def test_call_node_reports_non_function_values(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Number,)))),
            (CallNode(),),
        )

        self.assertEqual(len(branches), 0)
        self.assertEqual(
            analyser.diagnostics,
            ["cannot call non-function value of type Number"],
        )

    def test_computed_tags_are_stripped_unless_explicitly_returned(self):
        env = Environment()
        env.add_computed_tag("sorted")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Tagged(Number, "sorted"),)))),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_explicit_computed_return_tag_is_kept(self):
        env = Environment()
        env.add_computed_tag("sorted")
        env.define_overload(
            OP,
            Overload((Tagged(Number, "sorted"),), (Tagged(Number, "sorted"),)),
        )
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Tagged(Number, "sorted"),)))),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((Tagged(Number, "sorted"),)),
        )

    def test_tag_application_adds_tag_to_top_stack_value(self):
        env = Environment()
        env.add_computed_tag("sorted")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Number,)))),
            (TagApplicationNode(DataTag("sorted")),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((Tagged(Number, "sorted"),)),
        )

    def test_absent_tag_application_removes_tag_from_top_stack_value(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(
                AnalysisBranch(stack=TypeStack((Tagged(Number, "infinite"),)))
            ),
            (TagApplicationNode(DataTag("infinite", absent=True)),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_constructed_tags_propagate_through_generic_returns(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        tagged_list = Tagged(C(ListExactType, Number), "infinite")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((tagged_list,)))),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((tagged_list,)))

    def test_constructed_tags_propagate_to_output_depth(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(
                AnalysisBranch(
                    stack=TypeStack(
                        (Tagged(C(ListExactType, Number, 2), "infinite"),)
                    )
                )
            ),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack(
                (Tagged(C(ListExactType, Number, 2), DataTag("infinite", depth=1)),)
            ),
        )

    def test_multiple_sticky_tags_propagate_together(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.add_unit_tag("km")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        tagged = Tagged(Number, "infinite", "km")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((tagged,)))),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((tagged,)))

    def test_unit_tags_do_not_satisfy_untagged_concrete_parameters(self):
        env = Environment()
        env.add_unit_tag("km")
        env.define_overload(OP, Overload((Number,), (Number,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(AnalysisBranch(stack=TypeStack((Tagged(Number, "km"),)))),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 0)

    def test_constructed_tags_do_not_propagate_when_rank_drops(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(
            OP,
            Overload((Tagged(C(ListExactType, Number), "infinite"),), (Number,)),
        )
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet.one(
                AnalysisBranch(
                    stack=TypeStack((Tagged(C(ListExactType, Number), "infinite"),))
                )
            ),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_length_of_finite_list_returns_number(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("print length [1, 2, 3, 4, 5]"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            [node.typ for node in typed],
            [C(ListExactType, Number), Number, None],
        )

    def test_length_rejects_infinite_list(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[1, 2, 3] | #infinite | length"))

        self.assertEqual([node.typ for node in typed], [None, None, None])
        self.assertEqual(
            analyser.diagnostics,
            [
                "1:25: no overloads for element 'length' match stack [#infinite "
                "Number+]; available overloads: Function[#!infinite Item+ -> Number]"
            ],
        )

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

    def test_analyses_assert_while_and_unfold(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                """
$n = 1
assert => $n 0 > end
while ($n 3 <) =>
  $n = $n 1 +
end
$n
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)

        analyser = Analyser()
        typed = analyser.analyse(parse("1 unfold (< 5) -> (n: Number) => $n 1 + end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, WithTag(ExactList(Number), "infinite"))

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

    def test_imports_public_definition_from_relative_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n"
                "define hidden => 2\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            main.write_text(
                "import { math.[add_one] }\n41 add_one\n",
                encoding="utf-8",
            )

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse(main.read_text(encoding="utf-8")))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)
        self.assertIsInstance(typed[0], TypedFunctionNode)

    def test_import_namespace_uses_qualified_element_name(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "public define add_one(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse("import { math }\n41 math.add_one"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)

    def test_private_module_definition_is_not_importable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "define hidden => 2\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("import { math.[hidden] }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: module 'math' has no public component 'hidden'"],
        )

    def test_inline_code_cannot_use_local_imports_without_source_file(self):
        analyser = Analyser(module_loader=ModuleLoader())

        analyser.analyse(parse("import { math }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: local imports require a source file"],
        )


if __name__ == "__main__":
    unittest.main()
