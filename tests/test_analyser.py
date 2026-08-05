import unittest

import valiance.vtypes as T
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import (
    Analyser,
    AnalysisBranch,
    BranchSet,
    BranchVariables,
    InputMode,
    RewriteKind,
    analyse,
    analyse_function,
    analyse_function_details,
    default_environment,
)
from valiance.analysis.analyser import _branch_argument_substitution
from valiance.analysis.contracts.annotations import AnnotationSpec, register_annotation
from valiance.elements.builtins import (
    BUILTIN_ELEMENTS,
    BUILTIN_ERROR_TYPES,
    BUILTIN_FAULT_TYPES,
)
from valiance.asts import (
    BreakNode,
    CallNode,
    CastNode,
    DefineNode,
    ElementNode,
    FieldAccessNode,
    ForNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    NumberLiteralNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TypedAtNode,
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedTagApplicationNode,
)
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.vtypes.symbols import Symbol
from valiance.vtypes import (
    AppliedElement,
    C,
    DataTag,
    ElementTag,
    Environment,
    ExactList,
    Field,
    Fn,
    FunctionType,
    ListExactType,
    ListMinType,
    N,
    Never,
    NoMatchingOverload,
    NoneType,
    ObjectAttribute,
    Overload,
    Overloads,
    Row,
    Tagged,
    Tup,
    TupleTypeItem,
    TupVariadic,
    TypeStack,
    TypeVarId,
    U,
    UnknownElement,
    V,
    Variance,
    WithTag,
    assignable,
    optional,
    same,
    show,
)
from valiance.vtypes.default_types import Boolean
from valiance.runtime.runtime_values import RuntimeNumber as NumberRuntime

NUMBER = Symbol("Number")
REAL = Symbol("Real")
STRING = Symbol("String")
BOOL = Symbol("Bool")
BAX = Symbol("Bax")
INTEGER = Symbol("Integer")

Number = N(NUMBER)
Real = N(REAL)
Integer = N(INTEGER)
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
LEFT = Symbol("Left")
RIGHT = Symbol("Right")
HALT = Symbol("halt")


class AnalyserTests(unittest.TestCase):
    def test_stack_shuffle_copy_preserves_stack_and_pushes_labelled_copies(self):
        branches = Analyser().analyse_node(
            BranchSet((AnalysisBranch(stack=TypeStack((String, Bool, Number))),)),
            StackShuffleNode(
                Symbol("copy"),
                (Symbol("a"), Symbol("b")),
                (Symbol("a"), Symbol("b"), Symbol("b")),
            ),
        )

        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((String, Bool, Number, Bool, Number, Number)),
        )

    def test_stack_shuffle_move_removes_labelled_values_and_keeps_skips(self):
        branches = Analyser().analyse_node(
            BranchSet(
                (AnalysisBranch(stack=TypeStack((String, Bool, Integer, Number))),)
            ),
            StackShuffleNode(
                Symbol("move"),
                (Symbol("a"), None, Symbol("b")),
                (Symbol("a"), Symbol("a"), Symbol("b")),
            ),
        )

        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((String, Integer, Bool, Bool, Number)),
        )

    def test_stack_shuffle_copy_rejects_uncopyable_object(self):
        analyser = Analyser()
        analyser.analyse(parse("""
object WriteFile =>
  @error("Writeable files cannot be duplicated")
  define dup => end
end

WriteFile
copy(file -> file)
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("cannot copy value of type WriteFile", analyser.diagnostics[0])
        self.assertIn(
            "Writeable files cannot be duplicated",
            analyser.diagnostics[0],
        )

    def test_stack_shuffle_move_rejects_uncopyable_repeated_output(self):
        analyser = Analyser()
        analyser.analyse(parse("""
object WriteFile =>
  @error("Writeable files cannot be duplicated")
  define dup => end
end

WriteFile
move(file -> file, file)
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("cannot copy value of type WriteFile", analyser.diagnostics[0])
        self.assertIn(
            "Writeable files cannot be duplicated",
            analyser.diagnostics[0],
        )

    def test_default_environment_includes_builtin_plus(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("2"),
                ElementNode(PLUS),
            ],
        )

        self.assertEqual([node.typ for node in typed], [Integer, Integer, Integer])

    def test_number_literals_infer_integer_or_real_precision(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                NumberLiteralNode("1.5"),
                NumberLiteralNode("1e-2"),
                NumberLiteralNode("1e2"),
                NumberLiteralNode("1i0"),
                NumberLiteralNode("1.5i0"),
                NumberLiteralNode("1i2"),
                NumberLiteralNode("1.3e5.2"),
                NumberLiteralNode("1e2.0"),
            ],
        )

        self.assertEqual(
            [node.typ for node in typed],
            [Integer, Real, Real, Integer, Integer, Real, Number, Real, Integer],
        )

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

    def test_default_environment_predefines_element_tags(self):
        env = default_environment()

        self.assertIsNotNone(env.lookup_element_tag("IO"))
        self.assertIsNotNone(env.lookup_element_tag("Eager"))

    def test_eager_tag_propagates_from_called_element(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse("define log(value: Number) -> => $value println")
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertIn(ElementTag(Symbol("Eager")), typed[0].typ.element_tags)
        self.assertIn(ElementTag(Symbol("IO")), typed[0].typ.element_tags)

    def test_property_element_tag_declarations_are_user_attachable(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("tag Log as property\ndefine \\f<Log> => 1"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertIn(ElementTag(Symbol("Log")), typed[-1].typ.element_tags)

    def test_companion_element_tags_cannot_be_directly_attached(self):
        analyser = Analyser()

        analyser.analyse(parse("define \\f<Eager> => 1"))

        self.assertIn("cannot be directly attached", analyser.diagnostics[-1])

    def test_function_literals_cannot_directly_attach_companion_tags(self):
        analyser = Analyser()

        analyser.analyse(parse("fn<Eager> => 1"))

        self.assertIn("cannot be directly attached", analyser.diagnostics[-1])

    def test_element_tags_are_validated_in_absences_and_function_types(self):
        absent = Analyser()
        absent.analyse(parse("define f(value: Number)<!Missing> -> Number => $value"))

        nested = Analyser()
        nested.analyse(
            parse("define use(f: Function[Number -> Number]<Missing>) -> => end")
        )

        self.assertIn("undeclared element tag 'Missing'", absent.diagnostics[-1])
        self.assertIn("undeclared element tag 'Missing'", nested.diagnostics[-1])

    def test_required_element_tag_absence_rejects_inferred_effects(self):
        analyser = Analyser()

        analyser.analyse(
            parse("define log(value: Number)<IO, !Eager> -> => $value println")
        )

        self.assertIn("required to be absent", analyser.diagnostics[-1])

        parameterized = Analyser()
        parameterized.analyse(
            parse(
                "define fail(error: Fault)<!Panic[RuntimeFault]> -> => " "$error panic"
            )
        )

        self.assertIn(
            "required to be absent",
            parameterized.diagnostics[-1],
        )

    def test_nested_calls_propagate_element_tags(self):
        sources = (
            "define f(x: Number) -> Number => " "if true => $x println 1 else => 1 end",
            "define \\f => [1 println 1, 2]",
            'define \\f => "${1 println 2}"',
        )

        for source in sources:
            with self.subTest(source=source):
                analyser = Analyser()
                typed = analyser.analyse(parse(source))

                self.assertEqual(analyser.diagnostics, [])
                self.assertIn(
                    ElementTag(Symbol("Eager")),
                    typed[-1].typ.element_tags,
                )
                self.assertIn(
                    ElementTag(Symbol("IO")),
                    typed[-1].typ.element_tags,
                )

    def test_declared_generic_property_tag_covers_narrower_inferred_effect(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                "define fail(error: RuntimeFault)<Panic[Fault]> -> => " "$error panic"
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ.element_tags,
            frozenset(
                {
                    ElementTag(
                        Symbol("Panic"),
                        (N(Symbol("Fault")),),
                    )
                }
            ),
        )

    def test_data_element_tag_disjoints_reject_effectful_use(self):
        analyser = Analyser()

        analyser.analyse(parse("""
tag #infinite as constructed
tag #infinite disjoint Eager
[1, 2] | #infinite | println
"""))

        self.assertIn("cannot be used by an element", analyser.diagnostics[-1])

    def test_data_element_tag_disjoints_reject_effectful_use_in_function(self):
        analyser = Analyser()

        analyser.analyse(parse("""
tag #infinite as constructed
tag #infinite disjoint Eager
define \\f => [1, 2] | #infinite | println
"""))

        self.assertIn("cannot be used by an element", analyser.diagnostics[-1])

    def test_explicit_element_tag_sets_reject_undeclared_body_effects(self):
        analyser = Analyser()

        analyser.analyse(parse("define log(value: Number)<> -> => $value println"))

        self.assertIn("was not declared", analyser.diagnostics[-1])

    def test_element_tag_disjoint_rules_reject_simultaneous_tags(self):
        analyser = Analyser()

        analyser.analyse(parse("""
tag Read as property
tag Write as property
tag Read disjoint Write
define \\f<Read, Write> => 1
"""))

        self.assertIn("cannot both apply", analyser.diagnostics[-1])

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
        self.assertEqual(modifier.typ, Fn((Integer,), (Number,)))

    def test_modifier_overloads_cover_union_item_type(self):
        typed = analyse(parse('$lst = [1, 2, "A", "B"]\n$lst map: * 2'))

        mapped = typed[-1]
        self.assertIsInstance(mapped, TypedElementNode)
        self.assertEqual(mapped.typ, C(ListExactType, U(Integer, String)))
        self.assertEqual(len(mapped.modifier_args), 1)
        self.assertEqual(
            mapped.modifier_args[0].typ,
            Fn((U(Integer, String),), (U(Integer, String),)),
        )
        self.assertGreater(len(mapped.modifier_args[0].overloads), 1)

    def test_modifier_function_refines_inferred_generic_inputs(self):
        typed = analyse(parse("define sum => /: +"))

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((C(ListExactType, Integer),), (Integer,)),
                Overload((C(ListExactType, Number),), (Number,)),
                Overload((C(ListExactType, Real),), (Real,)),
                Overload((C(ListExactType, String),), (String,)),
            ),
        )

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

        self.assertEqual([node.typ for node in typed], [Integer, Integer, Number])
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].overload_index, 0)
        self.assertEqual(
            typed[-1].overload.overload,
            Overload((Number, Number), (Number,)),
        )
        self.assertFalse(typed[-1].overload.vectorised)

    def test_vectorised_comparison_infers_item_level_boolean_tag(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("$lst = [5, 1, 2, 7]\n$lst < 5"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            Tagged(
                C(ListExactType, Integer),
                DataTag("boolean", depth=1),
            ),
        )

    def test_element_records_vectorised_overload_application(self):
        typed = analyse(parse("[1, 2, 3] + [4, 5, 6]"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(
            typed[-1].overload.actual_returns,
            (C(ListExactType, Integer),),
        )

    def test_exact_function_parameter_is_visible_in_type_but_not_body(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
$myfun = fn (:Number novec) => double
$myfun(10)
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[0].typ), "Function[Number novec -> Number]")
        self.assertEqual(typed[-1].typ, Number)

    def test_call_policy_markers_are_erased_from_value_returns_and_casts(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
fn () -> Number exact => 1
fn () -> Number novec => 1
1 as[Number exact]
1 as[Number novec]
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[0].typ), "Function[ -> Number]")
        self.assertEqual(show(typed[1].typ), "Function[ -> Number]")
        self.assertEqual(typed[3].typ, Number)
        self.assertEqual(typed[5].typ, Number)

    def test_atomic_marker_is_visible_in_signature_but_not_function_body(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define[T] rankOne(xs: T+ exact) -> T+ => $xs end
[1, 2, 3] rankOne
"""))

        self.assertEqual(analyser.diagnostics, [])
        definition = typed[0]
        self.assertIsInstance(definition, TypedFunctionNode)
        self.assertEqual(
            show(definition.overloads[0].overload.params[0]),
            "T+ exact",
        )
        self.assertEqual(show(definition.overloads[0].body[0].typ), "T+")
        self.assertIsNotNone(definition.overloads[0].body[0].typ.base.identity)
        self.assertEqual(typed[-1].typ, C(ListExactType, Integer))

    def test_atomic_requirement_is_retained_across_generic_forwarding(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define[T] rankOne(xs: T+ exact) -> T+ => $xs end
define[U] forward(xs: U+ exact) -> U+ => $xs rankOne end
"""))

        self.assertEqual(analyser.diagnostics, [])
        definition = typed[1]
        self.assertIsInstance(definition, TypedFunctionNode)
        self.assertEqual(
            show(definition.overloads[0].overload.params[0]),
            "U+ exact",
        )
        self.assertEqual(
            [show(node.typ) for node in definition.overloads[0].body],
            ["U+", "U+"],
        )

    def test_unmarked_generic_cannot_forward_to_atomic_parameter(self):
        analyser = Analyser()

        analyser.analyse(parse("""
define[T] rankOne(xs: T+ exact) -> T+ => $xs end
define[U] unsafeForward(xs: U+) -> U+ => $xs rankOne end
"""))

        self.assertEqual(analyser.diagnostics, [])

    def test_atomic_collection_marker_rejects_higher_rank_argument(self):
        analyser = Analyser()

        analyser.analyse(parse("""
define[T] rankOne(xs: T+ exact) -> T+ => $xs end
[[1, 2], [3, 4]] rankOne
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'rankOne' match",
            analyser.diagnostics[0],
        )

    def test_exact_function_parameter_rejects_higher_rank_argument(self):
        analyser = Analyser()

        analyser.analyse(parse("""
$myfun = fn (:Number novec) => double
$myfun([1, 2, 3])
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'call' match", analyser.diagnostics[0])

    def test_exact_parameter_preserves_rank_variable_solving(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define rank(xs: Number+$n novec) -> Number => $n end
[[1], [2]] rank
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)
        self.assertEqual(typed[-1].overload.rank_values, (("n", 2),))

    def test_minimum_rank_argument_adapts_to_exact_collection_parameter(self):
        typed = analyse(parse("""
define exactIn(:Number+) => 1
define \\min -> Number* => []
exactIn \\min
"""))

        self.assertEqual(typed[-1].overload.vectorised_depths, (0,))
        self.assertEqual(typed[-1].overload.vectorised_target_ranks, (1,))

    def test_user_err_implementation_infers_result_signatures(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
object Problem => $message: String
object Problem as Err => define message -> String => $self.message

define choose(flag: #boolean Number) =>
  if ($flag) => 1 else => Problem("bad") end
end

$chooseFn = fn (flag: #boolean Number) =>
  if ($flag) => 1 else => Problem("bad") end
end
"""))

        problem = N(Symbol("Problem"))
        expected_result = N(Symbol("Result"), Integer, problem)
        [choose] = analyser.env.overloads_for(Symbol("choose"))
        self.assertEqual(choose.returns, (expected_result,))
        function_nodes = [node for node in typed if isinstance(node, TypedFunctionNode)]
        self.assertEqual(function_nodes[-1].typ.returns, (expected_result,))
        self.assertEqual(analyser.diagnostics, [])

    def test_explicit_non_result_return_requires_result_annotation(self):
        analyser = Analyser()

        analyser.analyse(parse("""
object Problem => $message: String
object Problem as Err => define message -> String => $self.message

define choose(flag: #boolean Number) -> Number =>
  if ($flag) => 1 else => Problem("bad") end
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            [
                "5:1: function body can return Result[Integer, Problem], but the "
                "explicit return annotation is Number; declare a compatible Result "
                "return type"
            ],
        )
        [choose] = analyser.env.overloads_for(Symbol("choose"))
        self.assertEqual(choose.params, (Tagged(Number, DataTag("boolean")),))
        self.assertEqual(choose.returns, (Number,))

    def test_empty_return_list_is_inferred_from_explicit_return_type(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define \\exact -> Number+ => []
define \\minimum -> Number* => []
define \\rugged -> Number~ => []
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(typed), 3)

    def test_analyses_vectorisation_extensions(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[1, 2, 3] [4, 5] + extend: or"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertIsNotNone(typed[-1].extension)
        self.assertIsNotNone(typed[-1].extension.selector)

    def test_extend_default_must_match_every_parameter(self):
        analyser = Analyser()

        analyser.analyse(parse('["a", "b"] [2] * extend(1)'))

        self.assertEqual(
            analyser.diagnostics,
            ["1:18: extend default must be compatible with every " "element parameter"],
        )

    def test_extend_selector_arity_must_match_target(self):
        analyser = Analyser()

        analyser.analyse(parse("""
define choose(a: Integer?) -> Integer? => $a end
[1, 2] [3] + extend: choose
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["3:14: extend selector arity must match the target element arity"],
        )

    def test_explicit_element_generic_arguments_fix_and_partially_infer_types(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
define[T, U] choose(left: T, right: U) -> T => $left
choose[Number, _](1, "value")
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(set(typed[-1].overload.substitution.values()), {Number, String})
        self.assertTrue(
            all(isinstance(key, TypeVarId) for key in typed[-1].overload.substitution)
        )
        self.assertEqual(typed[-1].overload.actual_returns, (Number,))

    def test_element_disambiguation_controls_vectorisation_depth(self):
        typed = analyse(parse("[[1, 2], [3, 4]] +{Number+, _} [10, 20]"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(typed[-1].overload.vectorised_depths, (1, 0))
        self.assertEqual(
            typed[-1].overload.actual_returns,
            (C(ListExactType, Number, 2),),
        )

    def test_element_disambiguation_selects_matching_overload(self):
        env = Environment()
        env.add_trait_impl(FOO, LEFT)
        env.add_trait_impl(FOO, RIGHT)
        env.define_overload(OP, Overload((N(LEFT),), (Number,)))
        env.define_overload(OP, Overload((N(RIGHT),), (String,)))

        analyser = Analyser(env)
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((N(FOO),))),)),
            (ElementNode(OP, disambiguation=(N(LEFT),)),),
        )

        [branch] = branches
        typed = branch.typed_body
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, Number)
        self.assertEqual(typed[-1].overload_index, 0)

    def test_non_inference_element_rejects_ambiguous_overload(self):
        env = Environment()
        env.define_overload(AMB, Overload((Number,), (Number,)))
        env.define_overload(AMB, Overload((Number,), (String,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
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

    def test_invalid_modifier_body_fails_containing_function_analysis(self):
        analyser = Analyser()

        analyser.analyse(parse("""
                define discrim(:Integer, :Integer, :Integer) -> Integer =>
                  copy(a, b, c -> a, c)
                  * * 4
                  dip: (^^ 2)
                  -
                end
                """))

        self.assertEqual(analyser.diagnostics, ["5:25: unknown element '^^'"])

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

    def test_child_scope_overloads_shadow_parent_unless_builtin_qualified(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))
        child = env.child_scope()
        child.define_overload(PLUS, Overload((String, String), (String,)))

        self.assertEqual(
            child.overloads_for(PLUS),
            (Overload((String, String), (String,)),),
        )
        self.assertEqual(child.overloads_for(Symbol("*::+")), env.overloads_for(PLUS))

    def test_analyser_can_analyse_one_branch_block(self):
        analyser = Analyser(Environment())
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            (NumberLiteralNode("1"),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Integer,)))

    def test_branch_set_condition_validation_pops_control_value(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            (NumberLiteralNode("1"),),
        )
        branches = analyser.require_stack_top_assignable(
            branches,
            expected=Number,
            location=None,
            message="expected Number on top of stack",
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack())
        self.assertEqual([node.typ for node in branch.typed_body], [Integer])

    def test_branch_set_condition_validation_rejects_any_non_bool_path(self):
        env = Environment()
        env.define_overload(COND, Overload((), (Boolean,)))
        env.define_overload(COND, Overload((), (Number,)))
        analyser = Analyser(env)
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(input_mode=InputMode.INFER_INPUTS),)),
            (ElementNode(COND),),
        )
        branches = analyser.require_stack_top_assignable(
            branches,
            expected=Bool,
            location=None,
            message="expected Bool on top of stack",
        )

        self.assertTrue(any(branch.failed for branch in branches))

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

        analyser.analyse(parse("""
object Person =>
  $name: String
  $age: Number
  define label -> String => $self.name
end
Person("Ada", 36) $.name
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.env.lookup_attribute(Symbol("Person"), NAME), String)
        self.assertEqual(
            analyser.env.overloads_for(Symbol("Person"))[0],
            Overload((String, Number), (N(Symbol("Person")),)),
        )
        self.assertTrue(analyser.env.overloads_for(Symbol("Person::label")))

    def test_explicit_object_constructor_replaces_synthesized_constructor(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Counter =>
  $value: Number = 0
  private $timesIncremented = 0
  define Counter(initialValue: Number) => $self.value = $initialValue
end
Counter(7)
"""))

        self.assertEqual(analyser.diagnostics, [])
        [constructor] = analyser.env.overloads_for(Symbol("Counter"))
        self.assertEqual(constructor.params, (Number,))
        self.assertEqual(constructor.returns, (N(Symbol("Counter")),))
        self.assertEqual(constructor.param_names, (Symbol("initialValue"),))

    def test_explicit_constructor_requires_all_non_default_fields_on_every_path(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Person =>
  $name: String
  $age: Number
  define Person(name: String, includeAge: Bool) =>
    $self.name = $name
    if ($includeAge) => $self.age = 1 end
  end
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["5:3: constructor 'Person' does not initialize field(s): age"],
        )
        self.assertEqual(analyser.env.overloads_for(Symbol("Person")), ())

    def test_augmented_field_write_does_not_initialize_required_constructor_field(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Counter =>
  $value: Number
  define Counter => $self.value := + 1
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["4:3: constructor 'Counter' does not initialize field(s): value"],
        )
        self.assertEqual(analyser.env.overloads_for(Symbol("Counter")), ())

    def test_explicit_constructor_arity_mismatch_is_a_diagnostic(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Person =>
  $name: String = "unknown"
  define Person => end
  define Person(name: String) => $self.name = $name
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            [
                "5:3: constructor overloads for 'Person' must all take "
                "0 inputs, got 1"
            ],
        )
        [constructor] = analyser.env.overloads_for(Symbol("Person"))
        self.assertEqual(constructor.params, ())

    def test_object_mustcall_methods_must_exist(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
@mustcall(all = ["commit"])
object Tx =>
  define rollback => end
end
"""))

        self.assertIsNone(typed[0].typ)
        self.assertTrue(
            any(
                "@mustcall method 'commit' is not defined on Tx" in message
                for message in analyser.diagnostics
            )
        )

    def test_object_member_access_levels_are_enforced(self):
        private_read = Analyser(Environment())
        private_read.analyse(parse("""
object Secret =>
  private $code: String
end
Secret("x") $.code
"""))
        self.assertEqual(
            private_read.diagnostics,
            ["5:13: type Secret has no known field 'code'"],
        )

        readable_write = Analyser(Environment())
        readable_write.analyse(parse("""
object Person =>
  $name: String
end
Person("Ada") | $.name = "Grace"
"""))
        self.assertEqual(
            readable_write.diagnostics,
            ["5:17: type Person has no writable field 'name'"],
        )

        public_write = Analyser(Environment())
        typed = public_write.analyse(parse("""
object Person =>
  public $name: String
end
Person("Ada") | $.name = "Grace"
"""))
        self.assertEqual(public_write.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Person")))

    def test_object_friendly_elements_can_read_private_and_write_readable_fields(
        self,
    ):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Secret =>
  private $code: String
  $label: String
  define reveal -> String => $self.code
  define relabel(label: String) -> Secret => $self.label = $label
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(analyser.env.overloads_for(Symbol("Secret::reveal")))
        self.assertTrue(analyser.env.overloads_for(Symbol("Secret::relabel")))

    def test_trait_and_variant_declarations_register_relationships(self):
        env = Environment()
        analyser = Analyser(env)

        analyser.analyse(parse("""
trait Shape => extend area -> Number end
object Circle =>
  $radius: Number
end
object Circle as Shape => end
variant Maybe =>
  Some => $value: Number end
  None => end
end
"""))

        self.assertTrue(env.context.implements(Symbol("Circle"), Symbol("Shape")))
        self.assertEqual(
            env.context.variant_members[Symbol("Some", ("Maybe",))],
            Symbol("Maybe"),
        )
        self.assertEqual(
            env.lookup_variant(Symbol("Maybe")).members[0],
            Symbol("Some", ("Maybe",)),
        )

    def test_variant_member_elements_implement_and_publish_extend_interface(self):
        analyser = Analyser()

        analyser.analyse(parse("""
variant Shape =>
  extend getArea -> Number
  Circle =>
    $radius: Number
    define getArea => squared $self.radius * 3.14
  end
  Rectangle =>
    $width: Number
    $height: Number
    define getArea => $self.width * $self.height
  end
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        overloads = analyser.env.overloads_for(Symbol("getArea"))
        self.assertEqual(len(overloads), 2)
        self.assertTrue(
            all(overload.params == (N(Symbol("Shape")),) for overload in overloads)
        )
        self.assertTrue(all(overload.is_multi for overload in overloads))
        self.assertTrue(analyser.env.overloads_for(Symbol("Shape.Circle::getArea")))
        self.assertTrue(analyser.env.overloads_for(Symbol("Shape.Rectangle::getArea")))

    def test_variant_member_must_implement_every_extend_declaration(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
variant Shape =>
  extend getArea -> Number
  Circle => $radius: Number end
end
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "variant member 'Circle' must implement element 'getArea'",
            analyser.diagnostics[0],
        )

    def test_generic_variant_constructor_preserves_type_argument(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(parse("""
variant[T] Maybe =>
  Some => $value: T end
  None => end
end
1
Some
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Maybe"), Integer))

    def test_object_generic_variance_is_inferred_from_readable_fields(self):
        env = Environment()
        analyser = Analyser(env)

        typed = analyser.analyse(parse("""
trait Vehicle => end
object Car => end
object Car as Vehicle => end
object[T] Box =>
  $value: T
end
define accept(box: Box[Vehicle]) -> Box[Vehicle] => $box end
Car
Box
accept
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            env.context.variance_for(Symbol("Box"), 1),
            (Variance.COVARIANT,),
        )
        self.assertEqual(typed[-1].typ, N(Symbol("Box"), N(Symbol("Vehicle"))))

    def test_public_generic_fields_are_inferred_invariant(self):
        env = Environment()
        analyser = Analyser(env)

        analyser.analyse(parse("object[T] Cell => public $value: T end"))

        self.assertEqual(
            env.context.variance_for(Symbol("Cell"), 1),
            (Variance.INVARIANT,),
        )

    def test_function_typed_fields_infer_contravariant_generic_use(self):
        env = Environment()
        analyser = Analyser(env)

        analyser.analyse(parse("object[T] Sink => $consume: Function[T ->] end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            env.context.variance_for(Symbol("Sink"), 1),
            (Variance.CONTRAVARIANT,),
        )

    def test_labelled_generic_bounds_publish_declaration_variance(self):
        env = Environment()
        analyser = Analyser(env)

        analyser.analyse(parse("""
trait Vehicle => end
object[T: any Vehicle] Source => $value: T end
object[T: above Vehicle] Sink => $consume: Function[T ->] end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            env.context.variance_for(Symbol("Source"), 1),
            (Variance.COVARIANT,),
        )
        self.assertEqual(
            env.context.variance_for(Symbol("Sink"), 1),
            (Variance.CONTRAVARIANT,),
        )

    def test_generic_object_field_access_substitutes_receiver_argument(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(parse("""
object Car => end
object[T] Box =>
  $value: T
end
Car
Box
$.value
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Car")))

    def test_generic_object_assigns_one_scope_to_fields_and_constructor(self):
        analyser = Analyser()

        analyser.analyse(parse("""
object[T] Box => $value: T
"""))

        self.assertEqual(analyser.diagnostics, [])
        definition = analyser.env.lookup_object(Symbol("Box"))
        constructor = analyser.env.overloads_for(Symbol("Box"))[0]
        field_var = definition.attributes[0].typ
        parameter_var = constructor.params[0]
        result_var = constructor.returns[0].args[0]
        self.assertIsNotNone(field_var.identity)
        self.assertEqual(field_var.identity, parameter_var.identity)
        self.assertEqual(field_var.identity, result_var.identity)
        self.assertEqual(field_var.name, "T")

    def test_nested_generic_calls_keep_caller_and_callee_variables_scoped(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
object[T] Box => $v: T
define[T, U] Map(
  b: Box[T],
  f: Function[T -> U]
) -> Box[U] => Box($f($b.v))
define[A] Nested(x: A) -> Box[Box[A]] => Box(Box($x))
"""))

        self.assertEqual(analyser.diagnostics, [])
        map_overload = analyser.env.overloads_for(Symbol("Map"))[0]
        nested_overload = analyser.env.overloads_for(Symbol("Nested"))[0]
        self.assertEqual(
            show(Fn(map_overload.params, map_overload.returns)),
            "Function[Box[T], Function[T -> U] -> Box[U]]",
        )
        self.assertEqual(
            show(Fn(nested_overload.params, nested_overload.returns)),
            "Function[A -> Box[Box[A]]]",
        )
        map_t = map_overload.params[0].args[0]
        nested_a = nested_overload.params[0]
        self.assertIsInstance(map_t, V("T").__class__)
        self.assertIsNotNone(map_t.identity)
        self.assertIsNotNone(nested_a.identity)
        self.assertNotEqual(map_t.identity, nested_a.identity)
        self.assertIsInstance(typed[-1], TypedFunctionNode)

    def test_labelled_generic_upper_bound_rejects_supertype_solution(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
trait Vehicle => end
object Car => end
object Car as Vehicle => end
define \\asVehicle -> Vehicle => Car end
define[T: any Car] accept(value: T) -> T => $value end
\\asVehicle
accept
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'accept' match stack [Vehicle]",
            analyser.diagnostics[0],
        )

    def test_labelled_generic_lower_bound_accepts_supertype_solution(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(parse("""
trait Vehicle => end
object Car => end
object Car as Vehicle => end
define \\asVehicle -> Vehicle => Car end
define[T: above Car] accept(value: T) -> T => $value end
\\asVehicle
accept
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Vehicle")))

    def test_invariant_generic_object_rejects_substituted_supertype_argument(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
trait Vehicle => end
object Car => end
object Car as Vehicle => end
object[T] Cell =>
  public $value: T
end
define accept(cell: Cell[Vehicle]) -> Cell[Vehicle] => $cell end
Car
Cell
accept
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'accept' match stack [Cell[Car]]",
            analyser.diagnostics[0],
        )

    def test_anonymous_trait_function_parameter_is_structural(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Car => end
define combine(left: Car, right: Car) -> Car => $left
define[T] keep(
  value: trait[T] =>
    extend combine(:T, :T) -> T
  end
) -> T => $value
Car
keep
"not a car"
keep
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'keep'", analyser.diagnostics[0])

    def test_fold_without_seed_suggests_reduce_when_reduce_matches(self):
        analyser = Analyser()

        analyser.analyse(parse("[4, 12] ** 2 | fold: +"))

        self.assertEqual(len(analyser.diagnostics), 1)
        diagnostic = analyser.diagnostics[0]
        self.assertIn("no overloads for element 'fold' match stack [Number+]", diagnostic)
        self.assertIn("help: `fold` requires an explicit accumulator seed", diagnostic)
        self.assertIn("`0 fold: +`", diagnostic)
        self.assertIn("`reduce: +`", diagnostic)

    def test_fold_near_miss_help_is_not_shown_when_reduce_also_fails(self):
        analyser = Analyser()

        analyser.analyse(parse('["a", "b"] | fold: *'))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertNotIn("help: `fold` requires", analyser.diagnostics[0])

    def test_anonymous_trait_collection_parameter_solves_item_type(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define[T] sum(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => reduce: +
[1, 2, 3]
sum
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Integer")))

    def test_anonymous_trait_requirement_contributes_generic_constraints(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define[T, U] dotProd(
  left: trait =>
    extend +(:T, :T) -> T
    extend *(:T, :U) -> T
  end +,
  right: U+
) =>
  * | reduce: +
end

[1, 2, 3] dotProd [4, 5, 6]
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Integer")))

    def test_enum_declaration_registers_niladic_members(self):
        env = Environment()
        analyser = Analyser(env)

        typed = analyser.analyse(parse("enum Colour => RED GREEN end\nColour.RED"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Colour")))
        self.assertTrue(env.overloads_for(Symbol("RED", ("Colour",))))

    def test_match_on_enum_requires_all_members_without_default(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
enum Colour => RED GREEN BLUE end
Colour.RED
match =>
  as :RED => "red"
  as :GREEN => "green"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["4:1: non-exhaustive match for Colour; missing cases: Colour.BLUE"],
        )

    def test_match_on_variant_is_exhaustive_by_member_cases(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(parse("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(1)
match =>
  as :Some => pop_n(1) "some"
  as :None => pop_n(1) "none"
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_function_infers_missing_inputs(self):
        env = Environment()
        env.define_overload(PLUS, Overload((Number, Number), (Number,)))

        typ = analyse_function(FunctionNode(body=(ElementNode(PLUS),)), env)

        self.assertEqual(typ, Fn((Number, Number), (Number,)))

    def test_generic_function_literal_uses_declared_generics(self):
        [typed] = analyse(parse("fn[T] (item: T) -> T => $item"))

        self.assertEqual(show(typed.typ), "Function[T -> T]")
        self.assertIsNotNone(typed.typ.params[0].identity)
        self.assertEqual(typed.typ.params[0].identity, typed.typ.returns[0].identity)

    def test_generic_function_literal_uses_explicit_row_constraint(self):
        analyser = Analyser()
        [typed] = analyser.analyse(parse("fn[T, U] (x: T(.bar: U)) -> U => $x.bar"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed.typ), "Function[T(.bar: U) -> U]")
        row = typed.typ.params[0]
        self.assertIsNotNone(row.base.identity)
        self.assertIsNotNone(row.fields[0].typ.identity)
        self.assertEqual(row.fields[0].typ.identity, typed.typ.returns[0].identity)
        self.assertNotEqual(row.base.identity, row.fields[0].typ.identity)

    def test_match_infers_missing_inputs_from_multiple_patterns(self):
        typ = analyse_function(
            FunctionNode(body=tuple(parse("""
match =>
  1, "x" => "hit"
  _, _ => "miss"
end
"""))),
            Environment(),
        )

        self.assertEqual(typ, Fn((String, Number), (String,)))

    def test_match_requires_cases_to_have_same_arity(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
1 2
match =>
  1, 2 => "two"
  _ => "one"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["3:1: match cases must match the same number of values"],
        )

    def test_result_question_unwraps_success_type(self):
        typed = analyse(parse("""
object ParseError => end
object ParseError as Err => end
define unwrap(x: Result[Number, ParseError]) -> Number =>
  $x ?
end
"""))

        self.assertEqual(
            typed[-1].typ,
            Fn(
                (N(Symbol("Result"), Number, N(Symbol("ParseError"))),),
                (Number,),
            ),
        )

    def test_function_infers_overload_set_when_missing_inputs_are_ambiguous(self):
        typed = analyse([FunctionNode(body=(ElementNode(PLUS),))])

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Integer, Integer), (Integer,)),
                Overload((Integer, Real), (Real,)),
                Overload((Number, Number), (Number,)),
                Overload((Real, Integer), (Real,)),
                Overload((Real, Real), (Real,)),
                Overload((String, String), (String,)),
            ),
        )

    def test_unannotated_named_parameter_specializes_from_overload_use(self):
        typed = analyse(
            [
                FunctionNode(
                    params=(FunctionParam(X, None),),
                    body=(ElementNode(PLUS),),
                )
            ]
        )

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Integer,), (Integer,)),
                Overload((Number,), (Number,)),
                Overload((Real,), (Real,)),
                Overload((String,), (String,)),
            ),
        )

    def test_unannotated_parameter_keeps_distinct_literal_specializations(self):
        typed = analyse(parse("define double(n) => $n 2 *"))

        self.assertEqual(
            typed[0].typ,
            Overloads(
                Overload((Integer,), (Integer,)),
                Overload((Number,), (Number,)),
                Overload((Real,), (Real,)),
                Overload((String,), (String,)),
            ),
        )

    def test_define_inferred_nilad_requires_backslash_name(self):
        analyser = Analyser()

        analyser.analyse(parse("define PI => 3.14"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: PI inferred as nilad, but not named as one"],
        )

    def test_backslash_define_must_infer_niladic_stack_effect(self):
        analyser = Analyser()

        analyser.analyse(parse("define \\actually_a_monad => length"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "1:1: \\actually_a_monad named as nilad, "
                "but inferred as popping 1 value(s)"
            ],
        )

    def test_backslash_define_allows_niladic_stack_effect(self):
        typed = analyse(parse("define \\PI => 3.14"))

        self.assertEqual(typed[0].typ, Fn((), (Real,)))

    def test_niladic_function_literal_does_not_require_backslash(self):
        typed = analyse(parse("fn () => 3.14"))

        self.assertEqual(typed[0].typ, Fn((), (Real,)))

    def test_top_level_assignment_is_not_captured_by_define(self):
        analyser = Analyser()

        analyser.analyse(parse("""
$x = 5
define timesFive(y: Number) -> Number => $x $y *
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["3:42: cannot capture top-level variable 'x'"],
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
                Overload((Integer,), (Integer,)),
                Overload((Number,), (Number,)),
                Overload((Real,), (Real,)),
                Overload((String,), (String,)),
            ),
        )
        self.assertIsInstance(function, TypedFunctionNode)
        self.assertEqual(
            [
                [body_node.typ for body_node in overload.body]
                for overload in function.overloads
            ],
            [
                [Integer, Integer, Integer],
                [Number, Number, Number],
                [Real, Real, Real],
                [String, String, String],
            ],
        )

    def test_overloaded_function_node_keeps_typed_body_per_overload(self):
        typed = analyse([FunctionNode(body=(ElementNode(PLUS),))])
        function = typed[0]

        self.assertIsInstance(function, TypedFunctionNode)
        self.assertEqual(
            [overload.typ for overload in function.overloads],
            [
                Fn((Integer, Integer), (Integer,)),
                Fn((Integer, Real), (Real,)),
                Fn((Number, Number), (Number,)),
                Fn((Real, Integer), (Real,)),
                Fn((Real, Real), (Real,)),
                Fn((String, String), (String,)),
            ],
        )
        self.assertEqual(
            [
                [body_node.typ for body_node in overload.body]
                for overload in function.overloads
            ],
            [[Integer], [Real], [Number], [Real], [Real], [String]],
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

    def test_where_clause_specializes_return_rank_from_parameter_rank(self):
        source = """
define id_rank(xs: Number+$n) -> Number+$n => $xs
[[1], [2]] id_rank
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Number, 2))

    def test_where_rank_specializes_first_class_postfix_call(self):
        source = """
[[1], [2]] (fn (xs: Number+$n) -> Number+$n => $xs end) call
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Number, 2))
        self.assertEqual(typed[-1].overload.runtime_static_values[0], 0)
        self.assertEqual(
            typed[-1].overload.runtime_static_values[-1],
            NumberRuntime("2"),
        )

    def test_where_clause_rejects_namespaced_static_operation(self):
        source = """
define invalid_static(x: Number) -> Number where (1 2 unsafe.max) => 1
1 invalid_static
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "static operations cannot be namespaced" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_computes_return_rank_from_tuple_length(self):
        source = """
define shaped(xs: Number*, shape: {Number, Number}) -> Number+$n
where ($n = $shape length) => [[1], [2]]
[1, 2] {2, 1} shaped
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Number, 2))
        self.assertEqual(
            typed[-1].overload.params,
            (C(ListMinType, Number), Tup(Number, Number)),
        )

    def test_where_clause_computes_rank_from_variadic_tuple_length(self):
        source = """
define[T] reshape(xs: T*, shape: {Number...}) -> T+$n
where ($n = length $shape) => $xs as![T+$n]
[[1, 2, 3], [4, 5, 6]] reshape {4, 5, 6}
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Integer, 3))
        self.assertEqual(typed[-1].overload.rank_values, (("n", 3),))

    def test_arbitrary_length_tuple_parameter_matches_mixed_pattern(self):
        source = """
define accept(shape: {Number..., String..., Number}) -> String => "ok"
{1, 2, "x", "y", 3} accept
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_where_assertion_rejects_current_overload(self):
        source = """
define rank_case(xs: Number+$n) -> String where ($n 2 == ?) => "matrix"
define rank_case(xs: Number+) -> String => "vector"
[1, 2] rank_case
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)
        self.assertEqual(typed[-1].overload.params, (C(ListExactType, Number),))

    def test_where_clause_supports_arithmetic_min_and_max(self):
        source = """
define widen(xs: Number+$n) -> Number+$m
where ($m = max($n, min(3, 5))) => [[[1]]]
[[1], [2]] widen
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Number, 3))

    def test_where_clause_supports_boolean_operations(self):
        source = """
define middle_rank(xs: Number+$n) -> String
where (and(1, not(0)) ?) => "middle"
define middle_rank(xs: Number+) -> String => "fallback"
[[1], [2]] middle_rank
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)
        self.assertEqual(
            typed[-1].overload.params,
            (C(ListExactType, Number, 2),),
        )

    def test_where_clause_computed_variable_is_visible_in_body(self):
        source = """
define next_rank(xs: Number+$n) -> Number where ($m = $n 1 +) => $m
[[1], [2]] next_rank
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)

    def test_where_clause_can_reject_all_overloads(self):
        source = """
define only_matrix(xs: Number+$n) -> String where ($n 2 == ?) => "matrix"
[1, 2] only_matrix
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'only_matrix' match",
            analyser.diagnostics[0],
        )

    def test_where_clause_rejects_arbitrary_element_calls(self):
        source = """
define invalid_static(xs: Number+$n) -> String where ($n double ?) => "bad"
[[1], [2]] invalid_static
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "invalid where clause: operation 'double' is not allowed" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_accepts_matching_assertion(self):
        source = """
define rank_case(xs: Number+$n) -> String where ($n 2 == ?) => "matrix"
define rank_case(xs: Number+) -> String => "vector"
[[1], [2]] rank_case
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].overload.params,
            (C(ListExactType, Number, 2),),
        )

    def test_where_clause_backtracks_variadic_tuple_rank_bindings(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("""
define ranks(xs: {Number+$n..., String+...}) -> Number => $n
{[1], [\"a\"]} ranks
""")),
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_where_clause_rejects_conflicting_input_rank_bindings(self):
        source = """
define same_rank(left: Number+$n, right: Number+$n) -> String => "same"
[1, 2] [[1], [2]] same_rank
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "no overloads for element 'same_rank' match" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_malformed_numeric_literals_without_crashing(self):
        source = """
define invalid_number(x: Number) -> String where (3i4 pop) => "bad"
1 invalid_number
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "contains invalid numeric literal '3i4'" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_generated_parameter_name_assignment(self):
        source = """
define collision(: Number) -> Number where ($_0 = 1) => $_0
2 collision
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "static variable '$_0' uses a reserved generated name" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_generated_parameter_rank_name(self):
        source = """
define collision(: Number+$_0) -> Number => 1
[1] collision
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "rank variable name(s) are reserved for generated parameters: $_0"
                in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_assignment_to_input_rank(self):
        source = """
define overwrite(xs: Number+$n) -> Number where ($n = 2) => 1
[1] overwrite
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "cannot assign read-only static variable '$n'" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_requires_output_rank_assignment(self):
        source = """
define missing_rank(x: Number) -> Number+$n => [1]
1 missing_rank
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "does not assign return rank variable(s): $n" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_rank_parameter_name_collision(self):
        source = """
define conflict(n: Number+$n) -> Number => 1
[1] conflict
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "parameter name(s) conflict with rank variable(s): $n" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_assertion_requires_number(self):
        source = """
define invalid_truth(x: Number) -> String
where ($t = Number, $t ?) => "bad"
1 invalid_truth
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "'?' requires a number" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_result_type_literals(self):
        source = """
define invalid_result(x: Number) -> String
where (Result[Number, String] pop) => "bad"
1 invalid_result
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "cannot use Result types; use optionals instead" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_compares_generic_type_literals(self):
        source = """
define[T] same_type(xs: T) -> String where ($xs T == ?) => "same"
1 same_type
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_where_clause_type_assignment_is_available_to_later_expressions(self):
        source = """
define same_type(x: Number) -> String
where ($t = Number, $t Number == ?) => "same"
1 same_type
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_where_clause_computed_rank_substitutes_in_later_type_literal(self):
        source = """
define next_type(xs: Number+$n) -> String
where ($m = $n 1 +, Number+$m Number+3 == ?) => "next"
[[1], [2]] next_type
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_where_clause_rejects_static_stack_underflow(self):
        source = """
define invalid_stack(x: Number) -> String where (swap) => "bad"
1 invalid_stack
"""
        analyser = Analyser()
        analyser.analyse(parse(source))

        self.assertTrue(
            any(
                "'swap' underflows the static stack" in diagnostic
                for diagnostic in analyser.diagnostics
            )
        )

    def test_where_clause_rejects_invalid_output_ranks_without_crashing(self):
        for value in ("0", "-1", "1.5", "65536"):
            with self.subTest(value=value):
                source = f"""
define invalid_rank(x: Number) -> Number+$n
where ($n = {value}) => [1]
1 invalid_rank
"""
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(
                    any(
                        "no overloads for element 'invalid_rank' match" in diagnostic
                        for diagnostic in analyser.diagnostics
                    )
                )

    def test_where_clause_introspects_function_parameters(self):
        source = """
define arity_rank(f: Function[Number, String -> Number]) -> Number+$n
where ($n = $f.arity) => [[1]]
fn (x: Number, y: String) -> Number => 1 end arity_rank
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, C(ListExactType, Number, 2))

    def test_function_inference_suppresses_trimmed_branch_diagnostics(self):
        analyser = Analyser(default_environment())

        typ = analyser.analyse_function(
            FunctionNode(body=(ElementNode(PLUS), ElementNode(DOUBLE)))
        )

        self.assertEqual(
            typ,
            Overloads(
                Overload((Integer, Integer), (Number,)),
                Overload((Integer, Real), (Number,)),
                Overload((Number, Number), (Number,)),
                Overload((Real, Integer), (Number,)),
                Overload((Real, Real), (Number,)),
            ),
        )
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

        self.assertEqual(typ, Fn((), (Integer,)))

    def test_omitted_returns_keep_only_top_stack_value(self):
        env = Environment()
        node = FunctionNode(
            params=(),
            body=(NumberLiteralNode("1"), NumberLiteralNode("2")),
        )

        typ = analyse_function(node, env)

        self.assertEqual(typ, Fn((), (Integer,)))

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

    def test_explicit_function_rejects_undefined_named_parameter_read(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("""
object Foo =>
  $x: Number
end

define get(:Foo) => $f.x + 5
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["6:21: undefined variable 'f'"],
        )

    def test_function_infers_row_constraint_from_field_access(self):
        typ = analyse_function(
            FunctionNode(body=(FieldAccessNode(BAR),)),
            Environment(),
        )

        self.assertEqual(show(typ), "Function[@1(.bar: @2) -> @2]")
        self.assertIsInstance(typ.params[0].base, T.MetaVarType)
        self.assertIsInstance(typ.params[0].fields[0].typ, T.MetaVarType)

    def test_later_collection_consumer_refines_implicit_function_input(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("fn => ** 2 | reduce: + | sqrt"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            show(typed[0].typ),
            "Function[Number* -> Number | Number*]",
        )
        function = typed[0]
        self.assertIsInstance(function, TypedFunctionNode)
        body = function.overloads[0].body
        power = next(
            node for node in body
            if isinstance(node, TypedElementNode) and node.node.name == Symbol("**")
        )
        root = next(
            node for node in body
            if isinstance(node, TypedElementNode) and node.node.name == Symbol("sqrt")
        )
        self.assertTrue(power.overload.vectorised)
        self.assertEqual(power.overload.vectorised_target_ranks, (0, None))
        self.assertTrue(root.overload.vectorised)
        self.assertEqual(root.overload.vectorised_target_ranks, (0,))

    def test_successful_scalar_implicit_inference_is_not_widened(self):
        typed = analyse(parse("fn => + 1 end"))

        self.assertEqual(
            show(typed[0].typ),
            "OverloadSet[Function[Integer -> Integer], "
            "Function[Number -> Number], Function[Real -> Real]]",
        )

    def test_inferred_return_function_rejects_multiple_values(self):
        analyser = Analyser()
        analyser.analyse(parse("""
$f = fn (a: Number, b: String, c: Number) =>
  return ($a, $b)
end
"""))

        self.assertTrue(
            any(
                "inferred-return functions may return at most one value" in message
                for message in analyser.diagnostics
            )
        )

    def test_explicit_return_branches_require_equal_multiplicity(self):
        analyser = Analyser()
        analyser.analyse(parse("""
fn (a: String, b: Integer) =>
  if (0 == 0) => return ($a, $a)
  else => return $b
end
"""))

        self.assertTrue(
            any(
                "return branches must return the same number of values" in message
                for message in analyser.diagnostics
            )
        )

    def test_declared_and_return_all_functions_allow_multiple_values(self):
        for source in (
            "fn (a: String) -> String, String => return ($a, $a) end",
            "@returnAll fn (a: String) => return ($a, $a) end",
        ):
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            function = typed[0]
            self.assertIsInstance(function, TypedFunctionNode)
            self.assertEqual(function.typ, Fn((String,), (String, String)))

    def test_each_explicit_return_argument_must_produce_one_value(self):
        analyser = Analyser()
        analyser.analyse(parse("""
$f = fn (a: Number, b: Number) =>
  return (1 2, $b)
end
"""))

        self.assertTrue(
            any(
                "return argument 1 must produce exactly one value" in message
                for message in analyser.diagnostics
            )
        )

    def test_declared_return_refines_inferred_row_field_type(self):
        details = analyse_function_details(
            FunctionNode(body=(FieldAccessNode(NAME),), returns=(String,)),
            Environment(),
        )

        self.assertIsNotNone(details)
        self.assertEqual(show(details.typ), "Function[@1(.name: String) -> String]")
        self.assertIsInstance(details.typ.params[0].base, T.MetaVarType)
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
        self.assertEqual(show(definition.typ), "Function[@1(.name: String) -> String]")
        self.assertIsInstance(definition.typ.params[0].base, T.MetaVarType)
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, String)

    def test_chained_field_access_refines_nested_row_constraint(self):
        typ = analyse_function(
            FunctionNode(body=(FieldAccessNode(BAR), FieldAccessNode(NAME))),
            Environment(),
        )

        self.assertEqual(
            show(typ),
            "Function[@1(.bar: @2(.name: @3)) -> @3]",
        )
        outer = typ.params[0]
        nested = outer.fields[0].typ
        self.assertIsInstance(outer.base, T.MetaVarType)
        self.assertIsInstance(nested.base, T.MetaVarType)
        self.assertIsInstance(nested.fields[0].typ, T.MetaVarType)
        self.assertEqual(
            nested.fields[0].typ.meta_identity,
            typ.returns[0].meta_identity,
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

        self.assertEqual(show(typ), "Function[x(.foo: Number) -> Number]")
        self.assertIsInstance(typ.params[0].base, T.MetaVarType)

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
            show(typ),
            "Function[x(.foo: Number+) -> Number+]",
        )
        self.assertIsInstance(typ.params[0].base, T.MetaVarType)

    def test_field_access_uses_environment_object_attributes(self):
        env = Environment()
        env.define_object(FOO, (ObjectAttribute(BAR, String),))

        branches = Analyser(env).analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((N(FOO),))),)),
            (FieldAccessNode(BAR),),
        )

        self.assertEqual(len(branches), 1)
        branch = next(iter(branches))
        self.assertEqual(branch.stack, TypeStack((String,)))
        self.assertEqual([node.typ for node in branch.typed_body], [String])

    def test_explicit_parameter_cycle_pops_from_the_conceptual_stack_top(self):
        branch = AnalysisBranch(
            input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
            cycle_params=(Integer, Real, String),
            cycle_stack_remaining=3,
            cycle_from_top=True,
        )

        observed = []
        for _ in range(4):
            sourced = branch.source_arguments((String,))
            self.assertIsNotNone(sourced)
            args, branch = sourced
            observed.extend(args)

        self.assertEqual(observed, [String, Real, Integer, String])

    def test_dip_modifier_can_source_explicit_parameter_cycle(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("fn (a: Number, b: Number, c: Number) => dip: -")),
        )

        self.assertTrue(branches)
        self.assertEqual(analyser.diagnostics, [])
        function_type = next(iter(branches)).stack[-1]
        self.assertEqual(function_type, Fn((Number, Number, Number), (Number,)))

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

    def test_branch_refinement_rejects_scoped_rigid_variable(self):
        rigid = V("T", TypeVarId(900, 0))
        branch = AnalysisBranch(
            stack=TypeStack((rigid,)),
            inputs=(rigid,),
        )

        refined = branch.refine_type(rigid, Number)

        self.assertEqual(refined.stack, TypeStack((rigid,)))
        self.assertEqual(refined.inputs, (rigid,))

    def test_branch_refinement_accepts_metavariable(self):
        inferred = T.M("@cleanup", T.MetaVarId(901, 0))
        branch = AnalysisBranch(
            stack=TypeStack((inferred,)),
            inputs=(inferred,),
        )

        refined = branch.refine_type(inferred, Number)

        self.assertEqual(refined.stack, TypeStack((Number,)))
        self.assertEqual(refined.inputs, (Number,))

    def test_branch_variables_are_branch_local(self):
        number_vars = BranchVariables()
        string_vars = BranchVariables()

        number_write = number_vars.write(X, Number)
        string_write = string_vars.write(X, String)

        self.assertIsNone(number_write.error)
        self.assertIsNone(string_write.error)
        number_vars = number_write.variables
        string_vars = string_write.variables
        self.assertIsNotNone(number_vars)
        self.assertIsNotNone(string_vars)
        self.assertEqual(number_vars.read(X), Number)
        self.assertEqual(string_vars.read(X), String)

    def test_branch_variables_reject_incompatible_reassignment(self):
        variables = BranchVariables(function_locals=((X, Number),))

        write = variables.write(X, String)

        self.assertIsNone(write.variables)
        self.assertEqual(
            write.error,
            "cannot assign String to variable 'x' of type Number",
        )

    def test_branch_variables_allow_assignable_reassignment(self):
        ctx = Environment().context
        ctx.trait_impls.setdefault(INTEGER, set()).add(NUMBER)
        variables = BranchVariables(function_locals=((X, Number),))

        write = variables.write(X, N(INTEGER), ctx=ctx)

        self.assertIsNone(write.error)
        self.assertIsNotNone(write.variables)
        self.assertEqual(write.variables.read(X), Number)

    def test_branch_variables_widen_mutable_numeric_reassignment(self):
        variables = BranchVariables(function_locals=((X, Integer),))

        write = variables.write(X, Number)

        self.assertIsNone(write.error)
        self.assertIsNotNone(write.variables)
        self.assertEqual(write.variables.read(X), Number)

    def test_branch_variables_check_existing_block_local_assignment(self):
        variables = BranchVariables(block_locals=((ITEM, Number),))

        write = variables.write(ITEM, String)

        self.assertIsNone(write.variables)
        self.assertEqual(
            write.error,
            "cannot assign String to variable 'item' of type Number",
        )

    def test_function_parameters_are_read_only_in_nested_scopes(self):
        sources = (
            "define f(x: Integer) -> Integer => if (true) => $x = 2 end | $x end",
            "define f(x: Integer) -> Integer => if (true) => $x := + 1 end | $x end",
            "define f(x: Integer) -> Integer => [1] foreach (n) => $x = $n end | $x end",
        )
        for source in sources:
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertEqual(len(analyser.diagnostics), 1)
                self.assertIn(
                    "cannot assign to read-only parameter 'x'",
                    analyser.diagnostics[0],
                )

    def test_nested_function_parameter_remains_read_only(self):
        analyser = Analyser()
        analyser.analyse(
            parse("define f(x: Integer) => fn (y: Integer) => $y = $x end end")
        )
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "cannot assign to read-only parameter 'y'",
            analyser.diagnostics[0],
        )

    def test_branch_variables_reject_parameter_writes(self):
        variables = BranchVariables(parameters=((X, Number),))

        write = variables.write(X, String)

        self.assertIsNone(write.variables)
        self.assertEqual(write.error, "cannot assign to read-only parameter 'x'")

    def test_branch_variables_reject_constant_writes(self):
        write = BranchVariables().write(X, Number, constant=True)
        self.assertIsNone(write.error)
        self.assertIsNotNone(write.variables)
        variables = write.variables

        write = variables.write(X, Number)

        self.assertIsNone(write.variables)
        self.assertEqual(write.error, "cannot assign to constant 'x'")

    def test_branch_variables_shadow_captures_on_write(self):
        variables = BranchVariables(captures=((X, Number),))

        write = variables.write(X, String)

        self.assertIsNone(write.error)
        self.assertIsNotNone(write.variables)
        self.assertEqual(write.variables.read(X), String)
        self.assertEqual(write.variables.captures, ((X, Number),))

    def test_branch_variables_drop_block_locals(self):
        variables = BranchVariables().with_block_local(ITEM, Number)

        self.assertEqual(variables.read(ITEM), Number)
        self.assertIsNone(variables.drop_block_locals().read(ITEM))

    def test_explicit_variable_type_sets_declared_type(self):
        typed = analyse(parse("$n: Number? = 5\n$n"))

        self.assertEqual(typed[-1].typ, optional(Number))

    def test_constant_reassignment_reports_diagnostic(self):
        analyser = Analyser()
        analyser.analyse(parse("const $n = 5\n$n = 6"))

        self.assertEqual(analyser.diagnostics, ["2:1: cannot assign to constant 'n'"])

    def test_multiple_assignment_sets_corresponding_types(self):
        typed = analyse(parse('$(a, b) = 1 "x"\n$a\n$b'))

        self.assertEqual(typed[-2].typ, Integer)
        self.assertEqual(typed[-1].typ, String)

    def test_explicit_variable_type_rejects_incompatible_initializer(self):
        analyser = Analyser()

        analyser.analyse(parse('$n: Number = "five"'))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: cannot assign String to variable 'n' of declared type Number"],
        )

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

    def test_return_all_annotation_returns_full_function_stack(self):
        typed = analyse(parse("@returnAll define \\pair => 1 2\n\\pair"))

        self.assertEqual(typed[0].typ, Fn((), (Integer, Integer)))
        self.assertEqual(typed[-1].overload.actual_returns, (Integer, Integer))

    def test_error_annotation_reports_selected_overload_message(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '@error("no strings") define bad(x: String) -> String => $x\n"hi" bad'
            )
        )

        self.assertEqual(analyser.diagnostics, ["2:6: no strings"])

    def test_warn_annotation_reports_selected_overload_warning(self):
        analyser = Analyser()

        analyser.analyse(
            parse('@warn("prefer safer") define old(x: Number) -> Number => $x\n1 old')
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.warnings, ["2:3: prefer safer"])

    def test_deprecated_annotation_has_default_warning(self):
        analyser = Analyser()

        analyser.analyse(parse("@deprecated define \\old -> Number => 1\n\\old"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.warnings, ["2:1: selected overload is deprecated"])

    def test_tupled_element_annotation_wraps_static_returns(self):
        typed = analyse(
            parse("define \\pair -> Number, Number => 1 2\n@@tupled \\pair")
        )

        self.assertEqual(typed[-1].typ, Tup(Number, Number))

    def test_err_type_annotation_adds_message_field_and_err_impl(self):
        analyser = Analyser()

        analyser.analyse(parse("@errType object DivisionByZeroError => end"))

        error_type = N(Symbol("DivisionByZeroError"))
        definition = analyser.env.lookup_object(Symbol("DivisionByZeroError"))
        self.assertIsNotNone(definition)
        self.assertEqual(definition.attribute_type(Symbol("message")), String)
        self.assertTrue(assignable(error_type, N(Symbol("Err")), analyser.env.context))
        self.assertTrue(analyser.env.overloads_for(Symbol("message")))

    def test_builtin_error_types_have_constructors_messages_and_err_impls(self):
        env = default_environment()

        for error_name in BUILTIN_ERROR_TYPES:
            with self.subTest(error_type=error_name.text):
                error_type = N(error_name)
                definition = env.lookup_object(error_name)
                self.assertIsNotNone(definition)
                self.assertEqual(
                    definition.attribute_type(Symbol("message")),
                    String,
                )
                self.assertTrue(assignable(error_type, N(Symbol("Err")), env.context))

                [constructor] = env.overloads_for(error_name)
                self.assertEqual(constructor.params, (String,))
                self.assertEqual(constructor.returns, (error_type,))
                self.assertEqual(constructor.param_names, (Symbol("message"),))

    def test_builtin_fault_types_have_constructors_messages_and_fault_impls(self):
        env = default_environment()

        for fault_name in BUILTIN_FAULT_TYPES:
            with self.subTest(fault_type=fault_name.text):
                fault_type = N(fault_name)
                definition = env.lookup_object(fault_name)
                self.assertIsNotNone(definition)
                self.assertEqual(
                    definition.attribute_type(Symbol("message")),
                    String,
                )
                self.assertTrue(assignable(fault_type, N(Symbol("Fault")), env.context))

                constructors = env.overloads_for(fault_name)
                if fault_name == Symbol("VectorisationFault"):
                    self.assertEqual(constructors, ())
                else:
                    [constructor] = constructors
                    self.assertEqual(constructor.params, (String,))
                    self.assertEqual(constructor.returns, (fault_type,))
                    self.assertEqual(constructor.param_names, (Symbol("message"),))

    def test_vectorisation_fault_is_intrinsic_but_handleable(self):
        constructor = Analyser()
        constructor.analyse(parse('VectorisationFault("forged")'))
        self.assertTrue(constructor.diagnostics)
        self.assertIn(
            "unknown element 'VectorisationFault'", constructor.diagnostics[0]
        )

        handler = Analyser()
        handler.analyse(parse("""
try =>
  [1, 2, 3] + [4, 5]
handle VectorisationFault =>
  "handled"
end
"""))
        self.assertEqual(handler.diagnostics, [])

    def test_panic_requires_fault_and_preserves_concrete_fault_tag(self):
        invalid = Analyser()
        invalid.analyse(parse('"boom" panic'))

        self.assertEqual(len(invalid.diagnostics), 1)
        self.assertIn("no overloads for element 'panic' match", invalid.diagnostics[0])

        valid = Analyser()
        typed = valid.analyse(parse('RuntimeFault("boom") panic'))

        self.assertEqual(valid.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(
            typed[-1].overload.element_tags,
            frozenset(
                {
                    ElementTag(
                        Symbol("Panic"),
                        (N(Symbol("RuntimeFault")),),
                    )
                }
            ),
        )

    def test_try_handler_type_must_implement_fault(self):
        analyser = Analyser()
        analyser.analyse(parse("""
try =>
  RuntimeFault("boom") panic
handle String =>
  "not a fault"
end
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "try handler type String does not implement Fault",
            analyser.diagnostics[0],
        )

    def test_try_handler_type_must_be_a_concrete_fault_type(self):
        analyser = Analyser()
        analyser.analyse(parse("""
try =>
  ValueFault("boom") panic
handle Fault =>
  "not concrete"
end
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "try handler type Fault is not a concrete runtime fault type",
            analyser.diagnostics[0],
        )

    def test_err_type_variant_marks_members_and_parent_as_err(self):
        analyser = Analyser()

        analyser.analyse(parse("""
@errType variant DBError =>
  ConnectionClosedError => end
end
"""))

        self.assertTrue(
            assignable(N(Symbol("DBError")), N(Symbol("Err")), analyser.env.context)
        )
        self.assertTrue(
            assignable(
                N(Symbol("ConnectionClosedError", ("DBError",))),
                N(Symbol("Err")),
                analyser.env.context,
            )
        )

    def test_annotation_registry_accepts_external_specs(self):
        register_annotation(AnnotationSpec("pluginCheck", frozenset({"define"})))
        analyser = Analyser()

        analyser.analyse(parse("@pluginCheck define \\value -> Number => 1"))

        self.assertEqual(analyser.diagnostics, [])

    def test_call_node_calls_function_from_stack_with_explicit_arguments(self):
        typed = analyse(
            [
                FunctionNode(body=(ElementNode(PLUS),)),
                CallNode(args=(NumberLiteralNode("1"), NumberLiteralNode("2"))),
            ],
        )

        self.assertEqual(typed[-1].typ, Integer)
        self.assertIsInstance(typed[-1], TypedCallNode)
        self.assertEqual(typed[-1].overload.params, (Integer, Integer))
        self.assertEqual(typed[-1].overload.actual_returns, (Integer,))
        self.assertFalse(typed[-1].overload.vectorised)

    def test_branch_substitution_solves_generic_optional_payload(self):
        substitution = _branch_argument_substitution(
            (optional(Number),),
            (optional(V("T")),),
            default_environment().context,
        )

        self.assertEqual(substitution, {"T": Number})

    def test_call_node_named_arguments_explain_function_value_rule(self):
        analyser = Analyser()

        analyser.analyse(parse("$double = fn (v: Integer) => * 2\n" "$double(v = 5)"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "2:1: named arguments are not supported when calling a function "
                "value; function values use positional arguments, so remove the "
                "argument names. Named arguments are available when calling a "
                "named element directly"
            ],
        )

    def test_call_node_falls_back_to_stack_values_for_missing_arguments(self):
        typed = analyse(
            [
                NumberLiteralNode("1"),
                FunctionNode(body=(ElementNode(PLUS),)),
                CallNode(args=(NumberLiteralNode("2"),)),
            ],
        )

        self.assertEqual(typed[0].typ, Integer)
        self.assertIsInstance(typed[1], TypedFunctionNode)
        self.assertEqual(typed[2].typ, Integer)
        self.assertEqual(typed[3].typ, Integer)

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

    def test_call_element_with_ecs_calls_function_argument(self):
        typed = analyse(parse("call(fn (:Number, :Number) => + end, 1, 2)"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, Number)
        self.assertEqual(typed[-1].overload.actual_returns, (Number,))
        self.assertEqual(len(typed[-1].overload.params), 3)

    def test_call_element_with_ecs_can_use_function_from_stack(self):
        typed = analyse(parse("fn => + end | call(1, 2)"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, Integer)
        self.assertEqual(typed[-1].overload.actual_returns, (Integer,))
        self.assertEqual(len(typed[-1].overload.params), 3)

    def test_optional_parameters_do_not_change_plain_element_arity(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(parse("""
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick
""")),
        )

        self.assertFalse(branches)
        self.assertEqual(
            analyser.diagnostics,
            [
                "3:1: no overloads for element 'pick' match stack [Number]\n"
                "available overloads:\n"
                "  - pick(a: Number, b: Number) -> Number"
            ],
        )

    def test_optional_parameters_can_be_overridden_with_named_ecs(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(parse("""
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick(b = 3)
""")),
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_optional_parameters_can_be_overridden_with_placeholder_ecs(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(parse("""
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick(_, 4)
""")),
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_function_with_generic_function_parameter_is_call_site_checked(self):
        typ = analyse_function(
            FunctionNode(
                params=(FunctionParam(Symbol("f"), Fn()),),
                body=(CallNode(),),
            ),
            default_environment(),
        )

        self.assertEqual(len(typ.overloads), 1)
        self.assertEqual(typ.overloads[0].params, (Fn(),))
        self.assertIsNotNone(typ.overloads[0].call_site_body)

    def test_call_site_checked_function_uses_concrete_stack_parameter_types(self):
        analyser = Analyser()
        function_name = Symbol("callit")
        function_param = Symbol("function")
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            (
                DefineNode(
                    function_name,
                    FunctionNode(
                        params=(FunctionParam(function_param, Fn()),),
                        body=(GetVariableNode(function_param), CallNode()),
                    ),
                ),
                FunctionNode(body=(ElementNode(DOUBLE),)),
                ElementNode(function_name),
            ),
        )

        branch = next(iter(branches))
        typed = branch.typed_body[-1]
        self.assertIsInstance(typed, TypedElementNode)
        self.assertEqual(typed.overload.params, (Number, Fn((Number,), (Number,))))
        self.assertEqual(typed.overload.actual_returns, (Number,))

    def test_call_site_checked_function_rejects_incompatible_call_site_body(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            (
                DefineNode(
                    Symbol("bad_callit"),
                    FunctionNode(
                        params=(FunctionParam(Symbol("f"), Fn()),),
                        body=(
                            StringLiteralNode("x"),
                            GetVariableNode(Symbol("f")),
                            CallNode(),
                        ),
                    ),
                ),
                FunctionNode(body=(ElementNode(DOUBLE),)),
                ElementNode(Symbol("bad_callit")),
            ),
        )

        self.assertFalse(branches)

    def test_dip_peek_and_fork_exercise_concrete_function_arguments(self):
        analyser = Analyser()
        peek = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number, Number))),)),
            tuple(parse("peek: *")),
        )
        self.assertEqual(
            next(iter(peek)).stack,
            TypeStack((Number, Number, Number)),
        )

        dip = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number, Number, Number))),)),
            tuple(parse("dip: *")),
        )
        self.assertEqual(next(iter(dip)).stack, TypeStack((Number, Number)))

        fork = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            (
                ElementNode(
                    Symbol("fork"),
                    modifier_args=(
                        FunctionNode(body=(ElementNode(DOUBLE),)),
                        FunctionNode(body=(ElementNode(IS_POSITIVE),)),
                    ),
                ),
            ),
        )
        self.assertEqual(next(iter(fork)).stack, TypeStack((Number, Boolean)))

    def test_both_and_sequence_type_check_callable_arity_at_each_call_site(self):
        analyser = Analyser()

        both = analyser.analyse_block(
            BranchSet(
                (
                    AnalysisBranch(
                        stack=TypeStack(
                            (Number, Number, Number, Number, Number, Number)
                        )
                    ),
                )
            ),
            tuple(parse("both: fn (:Number, :Number, :Number) => + + end")),
        )
        both_branch = next(iter(both))
        self.assertEqual(both_branch.stack, TypeStack((Number, Number)))
        both_node = both_branch.typed_body[-1]
        self.assertIsInstance(both_node, TypedElementNode)
        self.assertEqual(both_node.overload.runtime_static_values, (3,))

        sequence = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number, String, String))),)),
            tuple(parse("sequence: (double, +)")),
        )
        sequence_branch = next(iter(sequence))
        self.assertEqual(
            sequence_branch.stack,
            TypeStack((Number, String)),
        )
        sequence_node = sequence_branch.typed_body[-1]
        self.assertIsInstance(sequence_node, TypedElementNode)
        self.assertEqual(
            sequence_node.overload.runtime_static_values,
            (1, 2),
        )

    def test_both_rejects_a_group_that_does_not_match_the_callable(self):
        analyser = Analyser()

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number, String))),)),
            tuple(parse("both: double")),
        )

        self.assertFalse(branches)
        self.assertIn("no overloads for element 'both'", analyser.diagnostics[-1])

    def test_fork_infers_missing_shared_input_from_callable_arguments(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse("$avg = fn => fork: (sum, length) /\n" "println $avg([4, 6, 1, 7])")
        )

        self.assertFalse(analyser.diagnostics)
        self.assertEqual(
            show(typed[0].typ),
            "Function[#-infinite Number+ -> Number]",
        )

    def test_fork_shared_input_rejects_infinite_value(self):
        analyser = Analyser()

        analyser.analyse(parse("define f => fork: (sum, length) | /"))
        overload = analyser.env.overloads_for(Symbol("f"))[0]
        self.assertEqual(show(overload.params[0]), "#-infinite Number+")

        analyser.analyse(parse("f(#infinite [1, 2, 3, 4, 5])"))
        self.assertIn(
            "no overloads for element 'f' match",
            str(analyser.diagnostics[0]),
        )

    def test_list_of_functions_intersects_inferred_input_requirements(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse("define f => [sum, length] | reduce: /\n" "f([1, 2, 3, 4])")
        )

        self.assertFalse(analyser.diagnostics)
        self.assertEqual(show(typed[0].typ), "Function[#-infinite Number+ -> Number]")

    def test_peek_infers_partially_missing_callable_inputs(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse("$f = fn => 1 peek: " "fn (a: Number, b: Number) => + end end")
        )

        self.assertFalse(analyser.diagnostics)
        self.assertEqual(show(typed[0].typ), "Function[Number -> Number]")

    def test_dip_infers_missing_callable_inputs_below_a_known_held_value(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse('$f = fn => "held" dip: sum end\n' "$f([1, 2, 3])")
        )

        self.assertFalse(analyser.diagnostics)
        self.assertEqual(show(typed[0].typ), "Function[Number+ -> String]")

    def test_fork_pops_maximum_modifier_parameter_count(self):
        analyser = Analyser()

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((String, Number))),)),
            (
                ElementNode(
                    Symbol("fork"),
                    modifier_args=(
                        FunctionNode(
                            params=(
                                FunctionParam(NAME, String),
                                FunctionParam(Symbol("n"), Number),
                            ),
                            body=(GetVariableNode(NAME),),
                            returns=(String,),
                        ),
                        FunctionNode(body=(ElementNode(DOUBLE),)),
                    ),
                ),
            ),
        )

        self.assertEqual(next(iter(branches)).stack, TypeStack((String, Number)))

    def test_positive_predicate_analyses_as_one_element(self):
        typed = analyse(parse("1 positive?"))

        self.assertEqual(typed[-1].typ, Boolean)

    def test_fully_typed_definition_can_call_itself(self):
        analyser = Analyser()

        analyser.analyse(parse("""
define countdown(n: Number) -> Number =>
  if ($n 0 >) => $n 1 - countdown else => 0 end
end
3 countdown
"""))

        self.assertEqual(analyser.diagnostics, [])

    def test_user_defined_call_site_checked_function_uses_outer_stack_inputs(self):
        function_name = Symbol("callit")
        function_param = Symbol("function")
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            (
                DefineNode(
                    function_name,
                    FunctionNode(
                        params=(FunctionParam(function_param, Fn()),),
                        body=(GetVariableNode(function_param), CallNode()),
                    ),
                ),
                NumberLiteralNode("1"),
                FunctionNode(body=(ElementNode(DOUBLE),)),
                ElementNode(function_name),
            ),
        )

        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_variadic_tuple_parameter_is_call_site_checked_and_substituted(self):
        function_name = Symbol("accept")
        xs = Symbol("xs")
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Tup(Number, String),))),)),
            (
                DefineNode(
                    function_name,
                    FunctionNode(
                        params=(
                            FunctionParam(
                                xs,
                                TupVariadic(
                                    TupleTypeItem(Number, repeated=True),
                                    TupleTypeItem(String),
                                ),
                            ),
                        ),
                        body=(GetVariableNode(xs),),
                    ),
                ),
                ElementNode(function_name),
            ),
        )

        self.assertEqual(next(iter(branches)).stack, TypeStack((Tup(Number, String),)))

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
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
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
            BranchSet((AnalysisBranch(stack=TypeStack((Tagged(Number, "sorted"),))),)),
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
            BranchSet((AnalysisBranch(stack=TypeStack((Tagged(Number, "sorted"),))),)),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((Tagged(Number, "sorted"),)),
        )

    def test_declared_return_tag_guarantees_untagged_body_value(self):
        analyser = Analyser()

        analyser.analyse(parse("""
tag #sorted as computed
define sort(:Number+) -> #sorted Number+ => top
"""))

        self.assertEqual(analyser.diagnostics, [])
        [overload] = analyser.env.overloads_for(Symbol("sort"))
        self.assertEqual(overload.returns, (Tagged(ExactList(Number), "sorted"),))

    def test_constructed_tags_propagate_without_overlay_preservation(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("tag #sticky as constructed\n1 #sticky 2 +")),
        )

        self.assertEqual(
            branch.stack,
            TypeStack((Tagged(Integer, "sticky"),)),
        )

    def test_tag_overlay_preserves_computed_tag_without_runtime_override(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("""
tag #sorted as computed
#sorted: + =>
  (#sorted Number, Number) -> #sorted Number
end
1 #sorted | 2 +
""")),
        )

        self.assertEqual(branch.stack, TypeStack((Tagged(Number, "sorted"),)))
        self.assertIsInstance(branch.typed_body[-1], TypedElementNode)
        self.assertIsNotNone(branch.typed_body[-1].overload_index)

    def test_static_true_tag_validator_is_eliminated_at_application(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("""
tag #checked as computed
define #checked(:Number) -> #boolean Number => true end
1 #checked
""")),
        )

        self.assertEqual(branch.stack, TypeStack((Tagged(Integer, "checked"),)))
        self.assertIsInstance(branch.typed_body[-1], TypedTagApplicationNode)
        self.assertIsNone(branch.typed_body[-1].validator_index)

    def test_static_false_tag_validator_is_rejected_at_application(self):
        analyser = Analyser()
        analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("""
tag #checked as computed
define #checked(:Number) -> #boolean Number => false end
1 #checked
""")),
        )

        self.assertIn("statically false", analyser.diagnostics[-1])

    def test_tag_validator_missing_overload_is_diagnostic(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("""
tag #checked as computed
define #checked(:String) -> #boolean Number => true end
1 #checked
""")),
        )

        self.assertEqual(len(branches), 1)
        self.assertIn("no validator overload", analyser.diagnostics[-1])

    def test_explicit_tag_import_installs_tag_and_public_overlays(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                """
public tag #sorted as computed
public #sorted: + =>
  (#sorted Number, Number) -> #sorted Number
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            main.write_text("", encoding="utf-8")
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            [branch] = analyser.analyse_block(
                BranchSet((AnalysisBranch(),)),
                tuple(parse("import { tags.#sorted }\n1 #sorted | 2 +")),
            )

        self.assertEqual(branch.stack, TypeStack((Tagged(Number, "sorted"),)))

    def test_explicit_tag_import_installs_attached_public_elements(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                """
public tag #sorted as computed
public define #sorted normalize(value: Number) -> Number => $value
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            main.write_text("", encoding="utf-8")
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            [branch] = analyser.analyse_block(
                BranchSet((AnalysisBranch(),)),
                tuple(parse("import { tags.#sorted }\n1 normalize")),
            )

        self.assertEqual(branch.stack, TypeStack((Number,)))

    def test_disjoint_data_tags_are_rejected_when_declared_together(self):
        analyser = Analyser()

        analyser.analyse(parse("""
tag #left as computed
tag #right as computed
tag #left disjoint #right
define f(value: #left #right Number) -> Number => $value
"""))

        self.assertIn("cannot both apply", analyser.diagnostics[-1])

    def test_tag_application_adds_tag_to_top_stack_value(self):
        env = Environment()
        env.add_computed_tag("sorted")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
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
            BranchSet(
                (AnalysisBranch(stack=TypeStack((Tagged(Number, "infinite"),))),)
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
            BranchSet((AnalysisBranch(stack=TypeStack((tagged_list,))),)),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((Tagged(C(ListExactType, Number), "infinite"),)),
        )

    def test_constructed_tags_propagate_to_output_depth(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet(
                (
                    AnalysisBranch(
                        stack=TypeStack(
                            (Tagged(C(ListExactType, Number, 2), "infinite"),)
                        )
                    ),
                )
            ),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack(
                (
                    Tagged(
                        C(ListExactType, Number, 2),
                        DataTag("infinite", depth=1),
                    ),
                )
            ),
        )

    def test_multiple_constructed_like_tags_propagate_through_generic_flow(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.add_unit_tag("km")
        env.define_overload(OP, Overload((V("T"),), (V("T"),)))
        tagged = Tagged(Number, "infinite", "km")
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((tagged,))),)),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(
            next(iter(branches)).stack,
            TypeStack((Tagged(Number, "infinite", "km"),)),
        )

    def test_unit_tags_do_not_satisfy_untagged_concrete_parameters(self):
        env = Environment()
        env.add_unit_tag("km")
        env.define_overload(OP, Overload((Number,), (Number,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Tagged(Number, "km"),))),)),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 0)

    def test_constructed_tags_satisfy_untagged_concrete_parameters(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(OP, Overload((Number,), (Number,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet(
                (AnalysisBranch(stack=TypeStack((Tagged(Number, "infinite"),))),)
            ),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)

    def test_computed_tags_satisfy_untagged_concrete_parameters(self):
        env = Environment()
        env.add_computed_tag("sorted")
        env.define_overload(OP, Overload((Number,), (Number,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Tagged(Number, "sorted"),))),)),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)

    def test_constructed_tags_do_not_propagate_when_rank_drops(self):
        env = Environment()
        env.add_constructed_tag("infinite")
        env.define_overload(
            OP,
            Overload((Tagged(C(ListExactType, Number), "infinite"),), (Number,)),
        )
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet(
                (
                    AnalysisBranch(
                        stack=TypeStack((Tagged(C(ListExactType, Number), "infinite"),))
                    ),
                )
            ),
            (ElementNode(OP),),
        )

        self.assertEqual(len(branches), 1)
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_length_of_finite_list_returns_integer(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("print length [1, 2, 3, 4, 5]"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            [node.typ for node in typed],
            [C(ListExactType, Integer), Integer, None],
        )

    def test_negative_tag_requirement_refines_only_the_used_parameter(self):
        analyser = Analyser()

        analyser.analyse(
            parse("define onlySecond(a: Number+, b: Number+) => length $b")
        )

        overload = analyser.env.overloads_for(Symbol("onlySecond"))[0]
        self.assertEqual(
            tuple(show(param) for param in overload.params),
            ("Number+", "#-infinite Number+"),
        )

    def test_negative_tag_requirement_propagates_through_local_variable(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define remingle(ns: Number+) =>\n"
                "  $xs = $ns\n"
                "  length $xs + 5\n"
                "end\n"
                "remingle(#infinite [1, 2, 3, 4])"
            )
        )

        overload = analyser.env.overloads_for(Symbol("remingle"))[0]
        self.assertEqual(
            show(overload.params[0]),
            "#-infinite Number+",
        )
        self.assertIn("no overloads for element 'remingle'", analyser.diagnostics[0])

    def test_negative_tag_requirement_propagates_through_branch_join(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define conditional(ns: Number+) =>\n"
                "  if ($ns[0] == 0) => $ns\n"
                "  else => [1, 2, 3]\n"
                "  length | + 5\n"
                "end\n"
                "conditional(#infinite [1, 2, 3, 4])"
            )
        )

        overload = analyser.env.overloads_for(Symbol("conditional"))[0]
        self.assertEqual(
            show(overload.params[0]),
            "#-infinite Number+",
        )
        self.assertIn("no overloads for element 'conditional'", analyser.diagnostics[0])

    def test_negative_tag_requirement_propagates_through_for_item(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define insideAForLoop(nss: Number++) =>\n"
                "  $nss foreach (ns) =>\n"
                "    length $ns\n"
                "  end\n"
                "end"
            )
        )

        overload = analyser.env.overloads_for(Symbol("insideAForLoop"))[0]
        self.assertEqual(show(overload.params[0]), "#-infinite+ Number+2")

    def test_negative_tag_requirement_propagates_through_list_selection(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define selectionFromList(ns: Number+) =>\n"
                "  $items = [[1, 2], [1, 2, 3], $ns]\n"
                "  length $items[1]\n"
                "end\n"
                "selectionFromList(#infinite [1, 2, 3])"
            )
        )

        overload = analyser.env.overloads_for(Symbol("selectionFromList"))[0]
        self.assertEqual(show(overload.params[0]), "#-infinite Number+")
        self.assertIn(
            "no overloads for element 'selectionFromList'",
            analyser.diagnostics[0],
        )

    def test_length_still_rejects_indexing_heterogeneous_list_to_union(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define selectionFromList(ns: Number+) =>\n"
                "  $items = [1, 2, [1, 2, 3], $ns]\n"
                "  length $items[1]\n"
                "end"
            )
        )

        self.assertIn(
            "no overloads for element 'length' match stack "
            "[Integer | Integer+ | Number+]",
            analyser.diagnostics[0],
        )

    def test_negative_tag_requirement_propagates_through_closure_capture(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "define closures(ns: Number+) =>\n"
                "  define \\inner => $ns\n"
                "  length \\inner + 5\n"
                "end\n"
                "closures(#infinite [1, 2, 3, 4])"
            )
        )

        overload = analyser.env.overloads_for(Symbol("closures"))[0]
        self.assertEqual(
            show(overload.params[0]),
            "#-infinite Number+",
        )
        self.assertIn("no overloads for element 'closures'", analyser.diagnostics[0])

    def test_length_rejects_infinite_list(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[1, 2, 3] | #infinite | length"))

        self.assertEqual([node.typ for node in typed], [None, None, None])
        self.assertEqual(
            analyser.diagnostics,
            [
                "1:25: no overloads for element 'length' match stack [#infinite "
                "Integer+]\navailable overloads:\n"
                "  - length(String) -> Integer\n"
                "  - length(#-infinite Item+) -> Integer"
            ],
        )

    def test_for_loop_without_break_returns_none(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((C(ListExactType, Number),))),)),
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
            BranchSet((AnalysisBranch(stack=TypeStack((C(ListExactType, Number),))),)),
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

    def test_tuple_literal_index_returns_type_at_position(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('{1, "two", 3} $[1]'))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.warnings, [])
        self.assertEqual(typed[-1].typ, String)

    def test_tuple_negative_literal_index_returns_type_at_position(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('{1, "two", 3} $[-1]'))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

    def test_tuple_literal_index_out_of_bounds_is_compile_error(self):
        analyser = Analyser()
        analyser.analyse(parse('{1, "two", 3} $[3]'))
        self.assertIn("tuple index 3 is out of bounds", "\n".join(analyser.diagnostics))

    def test_tuple_constant_expression_index_warns_and_returns_union(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('{1, "two", 3} $[0 + 1]'))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, U(Integer, String))
        self.assertIn(
            "expression index will not return the exact type of item 1",
            "\n".join(analyser.warnings),
        )
        self.assertIn("try writing `$[1]` instead", "\n".join(analyser.warnings))

    def test_tuple_constant_expression_out_of_bounds_is_compile_error(self):
        analyser = Analyser()
        analyser.analyse(parse('{1, "two", 3} $[1 + 2]'))
        self.assertIn("tuple index 3 is out of bounds", "\n".join(analyser.diagnostics))
        self.assertEqual(analyser.warnings, [])

    def test_tuple_constant_length_index_out_of_bounds_is_compile_error(self):
        analyser = Analyser()
        analyser.analyse(parse('{1, "two", 3} $[length [1, 2, 3]]'))
        self.assertIn("tuple index 3 is out of bounds", "\n".join(analyser.diagnostics))

    def test_tuple_dynamic_integer_index_returns_union_without_warning(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('$i: Integer = 1\n{1, "two", 3} $[$i]'))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.warnings, [])
        self.assertEqual(typed[-1].typ, U(Integer, String))

    def test_tuple_slicing_is_rejected_during_analysis(self):
        analyser = Analyser()
        analyser.analyse(parse("{1, 2, 3} $[0:1]"))

        self.assertTrue(analyser.diagnostics)
        self.assertIn("tuple slicing is not supported", analyser.diagnostics[0])

    def test_tuple_slice_assignment_is_rejected_during_analysis(self):
        analyser = Analyser()
        analyser.analyse(parse("$value = {1, 2, 3}\n$value[0:1] = 9"))

        self.assertTrue(analyser.diagnostics)
        self.assertIn("tuple slicing is not supported", analyser.diagnostics[0])

    def test_list_indexing_rejects_real_typed_indices(self):
        analyser = Analyser()

        analyser.analyse(parse("[1, 2, 3, 4, 5] $[5 / 2]"))

        self.assertIn(
            "list indexing requires Integer index value(s)",
            "\n".join(str(item) for item in analyser.diagnostics),
        )

    def test_list_indexing_accepts_integer_typed_indices(self):
        analyser = Analyser()

        analyser.analyse(parse("[1, 2] $[1]"))

        self.assertEqual(analyser.diagnostics, [])

    def test_multi_index_access_and_assignment_preserve_item_types(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                "$list = [1, 2, 3]\n$list[0, 1]\n"
                '$dict = dict{"a" => 1, "b" => 2}\n$dict["a", "b"] = 8'
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[1].typ, ExactList(Integer))

    def test_sum_accumulator_widens_from_integer_initializer(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
define sum =>
  $res = 0
  foreach (item) => $res := + $item
  $res
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[0].typ, Fn((C(ListExactType, Number),), (Number,)))

    def test_for_loop_collects_break_types_from_if_branches(self):
        env = Environment()
        env.define_overload(COND, Overload((), (Boolean,)))
        analyser = Analyser(env)

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((C(ListExactType, Number),))),)),
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
        self.assertEqual(branch.stack, TypeStack((optional(U(Integer, String)),)))

    def test_analyses_assert_while_and_unfold(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("""
$n = 1
assert => $n 0 > end
while ($n 3 <) =>
  $n = $n 1 +
end
$n
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

        analyser = Analyser()
        typed = analyser.analyse(parse("1 unfold (< 5) -> (n: Number) => $n 1 + end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, WithTag(ExactList(Number), "infinite"))

        analyser = Analyser()
        typed = analyser.analyse(parse("0 1 unfold => + end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, WithTag(ExactList(Integer), "infinite"))

    def test_unfold_condition_uses_explicit_untyped_state_params(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
0 1 unfold (< 100) -> (prev, next) =>
  $prev + $next
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            WithTag(ExactList(Integer), "infinite"),
        )

    def test_unfold_condition_uses_inferred_state_params(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
0 1 unfold (< 100) =>
  +
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            WithTag(ExactList(Integer), "infinite"),
        )

    def test_unfold_condition_can_reference_named_state_param(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
0 1 unfold ($next < 100) -> (prev, next) =>
  $prev + $next
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            WithTag(ExactList(Integer), "infinite"),
        )

    def test_at_binds_named_levels_and_tracks_stop_ranks(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
[[1, 2], [3, 4]]
[5, 6]
at (list+, item) => $list append $item
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedAtNode)
        self.assertEqual(typed[-1].typ, ExactList(ExactList(Integer)))
        self.assertEqual(typed[-1].overload.vectorised_depths, (1, 1))
        self.assertEqual(
            typed[-1].overload.vectorised_target_ranks,
            (1, 0),
        )

    def test_at_rejects_a_stop_rank_above_the_input_rank(self):
        analyser = Analyser()
        analyser.analyse(parse("[1, 2] at (items++) => top"))

        self.assertIn(
            "1:8: at level 'items' requires rank 2, but received Integer+",
            analyser.diagnostics,
        )

    def test_unfold_rejects_more_than_state_plus_emission(self):
        analyser = Analyser()
        analyser.analyse(parse("""
1 unfold -> (n: Integer) =>
  $n
  dup
  dup
end
"""))

        self.assertIn(
            "2:3: unfold body may not produce more than state arity plus one value",
            analyser.diagnostics,
        )

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

        self.assertEqual(typed[0].typ, C(ListExactType, U(Integer, String)))

    def test_union_argument_preserves_scalar_or_vectorised_result(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("(if true => 1 else => [2] end) + 3"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, U(Integer, ExactList(Integer)))

    def test_generic_consumer_does_not_vectorise_matching_union_argument(self):
        typed = analyse(parse("""
define Mag(:Real*) => ** 2 | reduce: + | sqrt
println Mag [[3, 5], [4, 12]]
"""))

        println = typed[-1]
        self.assertIsInstance(println, TypedElementNode)
        self.assertEqual(println.node.name, Symbol("println"))
        self.assertFalse(println.overload.vectorised)
        self.assertEqual(println.overload.vectorised_target_ranks, ())

    def test_union_arguments_join_all_vectorised_result_ranks(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                "+((if true => 1 else => [2] end), " "(if true => 3 else => [[4]] end))"
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            U(Integer, ExactList(Integer), ExactList(Integer, 2)),
        )

    def test_generic_vectorisation_preserves_nested_union_shape(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                "define[T] identity(value: T) -> T => $value end\n"
                "identity([1, [2, 3]])"
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            ExactList(U(Integer, ExactList(Integer))),
        )

    def test_vectorised_addition_preserves_heterogeneous_list_shape(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[1, [2, 3, 4]] + [5, [6, 7, 8]]"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            ExactList(U(Integer, ExactList(Integer))),
        )

    def test_list_literal_factors_common_exact_list_rank(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[[[1, 2, 3]], [[4, 5, 6], 7], [8, [[9]], 10]]"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            ExactList(U(Integer, ExactList(Integer), ExactList(Integer, 2)), 2),
        )

    def test_list_literal_keeps_scalar_and_list_items_as_a_union(self):
        typed = analyse(parse("[1, [2, 3]]"))

        self.assertEqual(typed[-1].typ, ExactList(U(Integer, ExactList(Integer))))

    def test_empty_list_literal_requires_annotation_or_cast(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            (ListLiteralNode(),),
        )

        self.assertEqual(len(branches), 0)
        self.assertEqual(
            analyser.diagnostics,
            ["empty list literal requires a type annotation or cast"],
        )

    def test_empty_list_cast_supplies_list_type(self):
        typed = analyse([ListLiteralNode((), C(ListExactType, Number))])

        self.assertEqual(typed[0].typ, C(ListExactType, Number))

    def test_safe_cast_requires_assignability(self):
        analyser = Analyser(Environment())

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            (StringLiteralNode("x"), CastNode(Number)),
        )

        self.assertEqual(len(branches), 0)
        self.assertEqual(
            analyser.diagnostics,
            ["cannot safely cast String to Number"],
        )

    def test_checked_cast_narrows_broader_static_type(self):
        analyser = Analyser(Environment())
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((U(Number, String),))),)),
            (CastNode(Number, checked=True),),
        )

        self.assertEqual(analyser.diagnostics, [])
        [branch] = branches
        self.assertEqual(branch.stack[-1], Number)
        self.assertEqual(branch.typed_body[-1].typ, Number)

    def test_optional_cast_returns_optional_target_type(self):
        analyser = Analyser(Environment())
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((U(Number, String),))),)),
            (CastNode(Number, optional=True),),
        )

        self.assertEqual(analyser.diagnostics, [])
        [branch] = branches
        self.assertEqual(branch.stack[-1], optional(Number))
        self.assertEqual(branch.typed_body[-1].typ, optional(Number))

    def test_list_literal_forks_stack_and_pops_max_consumed_inputs(self):
        analyser = Analyser(default_environment())

        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((String, Number, Number))),)),
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
                "define hidden(n: Number) -> Number => $n\n",
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

    def test_imported_overloads_with_different_arities_report_diagnostic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text(
                "public define foo(:Number) => 1\n",
                encoding="utf-8",
            )
            (root / "b.vlnc").write_text(
                "public define foo(:Number, :Number) => 2\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { a.foo, b.foo }\n"
            main.write_text(source, encoding="utf-8")

            analyser = Analyser(source_file=main)
            analyser.analyse(parse(source))

        self.assertEqual(
            analyser.diagnostics,
            [
                "1:1: overloads for 'foo' must all take 1 input, got 2\n"
                "help: either remove one of these imports: `a.foo` or `b.foo`\n"
                "help: or keep both namespaced with `import { a, b }` and use "
                "`a.foo` or `b.foo`"
            ],
        )

    def test_local_definition_conflicting_with_imported_arity_reports_diagnostic(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.vlnc").write_text(
                "public define foo(:Number) => 1\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = "import { a.foo }\n" "define foo(:Number, :Number, :Number) => 3\n"
            main.write_text(source, encoding="utf-8")

            analyser = Analyser(source_file=main)
            analyser.analyse(parse(source))

        self.assertEqual(
            analyser.diagnostics,
            [
                "2:1: overloads for 'foo' must all take 1 input, got 3\n"
                "help: either rename the local definition `foo` or remove the "
                "import `a.foo`\n"
                "help: or keep the import namespaced with `import { a }` and "
                "use `a.foo`\n"
                "help: or change the local definition to take 1 input"
            ],
        )

    def test_import_inside_define_is_visible_only_in_define_body(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            valid_source = """
define apply(n: Number) -> Number =>
  import { helper.[bump] }
  $n bump
end
41 apply
"""
            main.write_text(valid_source, encoding="utf-8")

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse(valid_source))

            outside = Analyser(source_file=main)
            outside.analyse(parse(f"{valid_source}\n1 bump\n"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)
        self.assertEqual(outside.diagnostics, ["8:3: unknown element 'bump'"])

    def test_import_inside_if_branch_is_not_visible_in_sibling_branch(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("""
if true =>
  import { helper.[bump] }
  1 bump
else =>
  2 bump
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["6:5: unknown element 'bump'"],
        )

    def test_repeated_block_imports_share_one_runtime_prelude_definition(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "helper.vlnc").write_text(
                "public define bump(n: Number) -> Number => $n 1 +\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"
            source = """
if true =>
  import { helper.[bump] }
  1 bump
else => 0
end
if true =>
  import { helper.[bump] }
  2 bump
else => 0
end
"""

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse(source))

        imported = [
            node
            for node in typed
            if isinstance(node, TypedFunctionNode)
            and isinstance(node.node, DefineNode)
            and node.node.name == Symbol("bump")
        ]
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(imported), 1)

    def test_imported_tag_inside_block_does_not_escape_relation_context(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                "public tag #local as computed\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("""
if true =>
  import { tags.#local }
  1 #local
else => 2
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertIsNone(analyser.env.lookup_tag(Symbol("local")))
        self.assertNotIn(Symbol("local"), analyser.env.context.data_tags)

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

    def test_import_namespace_exports_public_object_constructor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  $age: Number
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(
                parse('import { person }\nperson.Person("Joe", 67)')
            )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "person.Person")

    def test_direct_import_exports_public_object_constructor(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  $age: Number
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(
                parse('import { person.Person }\nPerson("Joe", 67)')
            )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "Person")

    def test_direct_object_import_exports_object_friendly_elements(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
public object Person =>
  $name: String
  $age: Number
  define label -> String => $self.name
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(
                parse('import { person.Person }\nPerson("Joe", 67) label')
            )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, String)

    def test_direct_object_import_exports_trait_impl_friendly_elements(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shape.vlnc").write_text(
                """
public trait Shape =>
  extend getArea -> Number
end
""",
                encoding="utf-8",
            )
            (root / "rectangle.vlnc").write_text(
                """
import {shape.Shape}

public object Rectangle =>
  $shortSide: Number
  $longSide: Number
end

object Rectangle as Shape =>
  define getArea => $self.shortSide * $self.longSide
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(
                parse("import {rectangle.Rectangle}\nRectangle(6, 7) getArea")
            )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)

    def test_namespace_object_import_keeps_friendly_elements_qualified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rectangle.vlnc").write_text(
                """
public object Rectangle =>
  $shortSide: Number
  $longSide: Number
  define getArea => $self.shortSide * $self.longSide
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(
                parse("import {rectangle}\nrectangle.Rectangle(6, 7) getArea")
            )

        self.assertEqual(
            analyser.diagnostics,
            ["2:27: unknown element 'getArea'"],
        )

    def test_private_object_is_not_importable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "person.vlnc").write_text(
                """
object Person =>
  $name: String
end
""",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("import { person.Person }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: module 'person' has no public component 'Person'"],
        )

    def test_root_import_resolves_from_project_manifest_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "src" / "app"
            nested.mkdir(parents=True)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n',
                encoding="utf-8",
            )
            (root / "shared.vlnc").write_text(
                "public define answer(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            main = nested / "main.vlnc"

            analyser = Analyser(source_file=main)
            typed = analyser.analyse(parse("import { root.shared.answer }\n42 answer"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Number)

    def test_dep_import_requires_direct_manifest_dependency(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n[dependencies]\n',
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("import { dep.somelib }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: dependency 'somelib' is not declared"],
        )

    def test_private_module_definition_is_not_importable(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "math.vlnc").write_text(
                "define hidden(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            main = root / "main.vlnc"

            analyser = Analyser(source_file=main)
            analyser.analyse(parse("import { math.[hidden] }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: module 'math' has no public component 'hidden'"],
        )

    def test_imports_python_backed_standard_library_namespace(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse('import { std.regex }\n"a+" "aaa" regex.matches')
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Boolean)

    def test_python_backed_standard_library_runtime_names_are_not_global(self):
        analyser = Analyser()

        analyser.analyse(parse('"a+" "aaa" std.regex.matches'))

        self.assertEqual(
            analyser.diagnostics,
            ["1:12: unknown element 'std.regex.matches'"],
        )

    def test_inline_code_cannot_use_local_imports_without_source_file(self):
        analyser = Analyser(module_loader=ModuleLoader())

        analyser.analyse(parse("import { math }"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:1: local imports require a source file"],
        )


    def test_generic_arguments_infer_union_for_unrelated_lower_bounds(self):
        analyser = Analyser()
        typed = analyser.analyse(parse(
            'define[T] choose(left: T, right: T) => $left\n'
            'choose(1, "hello")'
        ))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            typed[-1].typ,
            U(Integer, String),
        )

    def test_generic_numeric_arguments_infer_real_for_integer_and_decimal(self):
        analyser = Analyser()
        typed = analyser.analyse(parse(
            'define[T] choose(left: T, right: T) => $left\n'
            'choose(1, 2.5)'
        ))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Real)


class NeverDiagnosticRecoveryTests(unittest.TestCase):
    def _analyser_with_halt(self) -> Analyser:
        env = default_environment().child_scope()
        env.define_overload(HALT, Overload((), (Never(),)))
        return Analyser(env)

    def test_nested_primary_errors_do_not_emit_wrapper_diagnostics(self):
        cases = (
            ("1 +(missing)", "1:5: unknown element 'missing'"),
            ("if missing => 1 else => 2 end", "1:4: unknown element 'missing'"),
            ("while missing => 1 end", "1:7: unknown element 'missing'"),
            ("[missing]", "1:2: unknown element 'missing'"),
        )

        for source, expected in cases:
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertEqual(analyser.diagnostics, [expected])

    def test_never_result_stops_following_top_level_analysis(self):
        analyser = self._analyser_with_halt()

        typed = analyser.analyse([ElementNode(HALT), ElementNode(MISSING)])

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(typed), 1)
        self.assertIsInstance(typed[0], TypedElementNode)
        self.assertEqual(typed[0].node.name, HALT)
        self.assertEqual(typed[0].typ, Never())

    def test_never_condition_is_terminal_not_a_boolean_mismatch(self):
        analyser = self._analyser_with_halt()

        typed = analyser.analyse(parse("if halt => 1 else => 2 end missing"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(typed), 1)
        self.assertIsInstance(typed[0], TypedElementNode)
        self.assertEqual(typed[0].node.name, HALT)

    def test_never_explicit_argument_terminates_the_enclosing_call(self):
        analyser = self._analyser_with_halt()

        typed = analyser.analyse(parse("1 +(halt) missing"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Never())

    def test_never_literal_item_makes_the_literal_terminal(self):
        analyser = self._analyser_with_halt()

        typed = analyser.analyse(parse("[halt] missing"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Never())


class SmartDiagnosticTests(unittest.TestCase):
    def test_unknown_element_ranks_the_nearest_typo_first(self):
        analyser = Analyser()

        analyser.analyse(parse("1 pritn"))

        [message] = analyser.diagnostics
        self.assertIn("  - print(", message)
        if "  - println(" in message:
            self.assertLess(message.index("  - print("), message.index("  - println("))

    def test_unknown_element_suggests_only_similar_viable_overloads(self):
        env = Environment()
        env.define_overload(
            Symbol("increment"),
            Overload((Integer,), (Integer,), param_names=(Symbol("value"),)),
        )
        env.define_overload(
            Symbol("incrementText"),
            Overload((String,), (String,), param_names=(Symbol("value"),)),
        )
        analyser = Analyser(env)

        analyser.analyse(parse("1 incremnt"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "1:3: unknown element 'incremnt'\n"
                "did you mean:\n"
                "  - increment(value: Integer) -> Integer"
            ],
        )

    def test_unknown_explicit_call_suggests_only_compatible_signature(self):
        env = Environment()
        env.define_overload(
            Symbol("format"),
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        env.define_overload(
            Symbol("format"),
            Overload((String,), (String,), param_names=(Symbol("text"),)),
        )
        analyser = Analyser(env)

        analyser.analyse(parse("formt(1)"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "1:1: unknown element 'formt'\n"
                "did you mean:\n"
                "  - format(value: Integer) -> String"
            ],
        )

    def test_unknown_named_argument_suggests_parameter_name(self):
        env = Environment()
        env.define_overload(
            Symbol("convert"),
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        analyser = Analyser(env)

        analyser.analyse(parse("convert(vaule = 1)"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "1:1: unknown named argument 'vaule' for element 'convert'\n"
                "did you mean 'value'?\n"
                "available overloads:\n"
                "  - convert(value: Integer) -> String"
            ],
        )

    def test_unknown_element_does_not_suggest_similar_but_unusable_element(self):
        env = Environment()
        env.define_overload(
            Symbol("increment"),
            Overload((String,), (String,), param_names=(Symbol("value"),)),
        )
        analyser = Analyser(env)

        analyser.analyse(parse("1 incremnt"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:3: unknown element 'incremnt'"],
        )

    def test_unknown_variable_suggests_visible_similar_variable(self):
        analyser = Analyser(Environment())

        analyser.analyse(parse("$counter = 1\n$countre"))

        self.assertEqual(
            analyser.diagnostics,
            ["2:1: undefined variable 'countre'\n" "did you mean '$counter'?"],
        )

    def test_overload_formatting_right_aligns_partial_parameter_names(self):
        env = Environment()
        env.define_overload(
            Symbol("join"),
            Overload(
                (Integer, String),
                (String,),
                param_names=(Symbol("text"),),
            ),
        )
        analyser = Analyser(env)

        analyser.analyse_node(
            BranchSet((AnalysisBranch(stack=TypeStack((NoneType(),))),)),
            ElementNode(Symbol("join")),
        )

        [message] = analyser.diagnostics
        self.assertIn("  - join(Integer, text: String) -> String", message)
        self.assertNotIn("join(text: Integer, String)", message)

    def test_overload_failure_formats_signatures_as_multiline_list(self):
        env = Environment()
        env.define_overload(
            Symbol("convert"),
            Overload((Integer,), (String,), param_names=(Symbol("value"),)),
        )
        env.define_overload(
            Symbol("convert"),
            Overload((String,), (Integer,), param_names=(Symbol("text"),)),
        )
        analyser = Analyser(env)
        branches = analyser.analyse_node(
            BranchSet((AnalysisBranch(stack=TypeStack((NoneType(),))),)),
            ElementNode(Symbol("convert")),
        )

        self.assertFalse(branches)
        self.assertEqual(
            analyser.diagnostics,
            [
                "no overloads for element 'convert' match stack [None]\n"
                "available overloads:\n"
                "  - convert(value: Integer) -> String\n"
                "  - convert(text: String) -> Integer"
            ],
        )
        self.assertNotIn("Function", analyser.diagnostics[0])

    def test_noop_move_is_a_lint_and_analysis_continues(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("1 move(value -> value) 2 +"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "1:3: [L007/no-op-move] this move leaves the stack unchanged; "
                "remove `move(value -> value)`"
            ],
        )
        self.assertEqual(typed[-1].typ, Integer)

    def test_identity_cast_is_a_lint_and_analysis_continues(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("1 as[Integer] 2 +"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "1:3: [L013/redundant-cast] unnecessary cast to Integer; remove `as[Integer]`"
            ],
        )
        self.assertEqual(typed[-1].typ, Integer)

    def test_statically_safe_checked_cast_is_a_lint_with_replacement(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("1 as![Number] 2 +"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "1:3: [L015/safe-checked-cast] checked cast to Number is statically safe; "
                "write `as[Number]` instead of `as![Number]`"
            ],
        )
        self.assertEqual(typed[-1].typ, Number)

    def test_lint_findings_expose_structured_rewrite_metadata(self):
        analyser = Analyser()

        analyser.analyse(parse("1 as[Integer]"))

        [finding] = analyser.lint_findings
        self.assertEqual(finding.code, "redundant-cast")
        self.assertEqual(
            finding.message,
            "unnecessary cast to Integer; remove `as[Integer]`",
        )
        self.assertIsInstance(finding.node, CastNode)
        self.assertIsNotNone(finding.rewrite)
        self.assertEqual(finding.rewrite.kind, RewriteKind.REMOVE_NODE)
        self.assertTrue(finding.rewrite.semantics_preserving)
        self.assertEqual(finding.render(), analyser.lints[0])

    def test_nested_function_propagates_structured_lint_findings(self):
        analyser = Analyser()

        analyser.analyse(parse("fn => 1 as[Integer] end"))

        self.assertEqual(len(analyser.lints), 1)
        self.assertEqual(len(analyser.lint_findings), 1)
        self.assertEqual(analyser.lint_findings[0].code, "redundant-cast")

    def test_empty_copy_is_a_lint_and_analysis_continues(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("1 copy(value ->) 2 +"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "1:3: [L006/no-op-copy] this copy produces no values and has no effect; "
                "remove `copy(value ->)`"
            ],
        )
        self.assertEqual(analyser.lint_findings[0].code, "no-op-copy")
        self.assertEqual(
            analyser.lint_findings[0].rewrite.kind,
            RewriteKind.REMOVE_NODE,
        )
        self.assertEqual(typed[-1].typ, Integer)

    def test_code_after_explicit_return_is_linted_as_unreachable(self):
        analyser = Analyser()

        analyser.analyse(parse("""
fn -> Number =>
  return 1
  2
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "4:3: [L017/unreachable-code] code after `return` is unreachable; "
                "remove it or move it before the return"
            ],
        )
        [finding] = analyser.lint_findings
        self.assertEqual(finding.code, "unreachable-code")
        self.assertEqual(
            finding.rewrite.kind,
            RewriteKind.REMOVE_UNREACHABLE_SUFFIX,
        )

    def test_match_case_after_default_is_linted_as_unreachable(self):
        analyser = Analyser()

        analyser.analyse(parse("""
1
match =>
  default => pop_n(1) "first"
  1 => "second"
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "5:3: [L018/unreachable-match-case] match case is unreachable because an earlier case "
                "matches every value; remove this case"
            ],
        )
        self.assertEqual(
            analyser.lint_findings[0].rewrite.kind,
            RewriteKind.REMOVE_MATCH_CASE,
        )

    def test_duplicate_literal_match_case_is_linted(self):
        analyser = Analyser()

        analyser.analyse(parse("""
1
match =>
  1 => "first"
  1 => "second"
  _ => "other"
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "5:3: [L003/duplicate-match-case] duplicate match case; remove this case because the "
                "same literal pattern appears earlier"
            ],
        )
        self.assertEqual(
            analyser.lint_findings[0].code,
            "duplicate-match-case",
        )

    def test_duplicate_literal_match_alternative_is_linted(self):
        analyser = Analyser()

        analyser.analyse(parse("""
1
match =>
  1 || 1 => "one"
  _ => "other"
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            analyser.lints,
            [
                "4:8: [L004/duplicate-pattern-alternative] duplicate match alternative; remove the repeated "
                "literal pattern"
            ],
        )
        [finding] = analyser.lint_findings
        self.assertEqual(finding.code, "duplicate-pattern-alternative")
        self.assertEqual(
            finding.rewrite.kind,
            RewriteKind.REMOVE_PATTERN_ALTERNATIVE,
        )

    def test_repeated_guard_match_cases_are_not_assumed_redundant(self):
        analyser = Analyser()

        analyser.analyse(parse("""
1
match =>
  if > 0 => "positive"
  if > 0 => "also"
  _ => "other"
end
"""))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.lints, [])
        self.assertEqual(analyser.lint_findings, [])

    def test_guarded_named_match_pattern_does_not_hide_later_case(self):
        analyser = Analyser()

        analyser.analyse(
            parse('1\nmatch =>\n  as x if > 0 => "positive"\n' '  _ => "other"\nend')
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.lints, [])
        self.assertEqual(analyser.lint_findings, [])

    def test_guarded_named_match_pattern_is_not_exhaustive_by_itself(self):
        analyser = Analyser()

        analyser.analyse(parse('1\nmatch =>\n  as x if > 0 => "positive"\nend'))

        self.assertEqual(
            analyser.diagnostics,
            ["2:1: match without default requires enum or variant value"],
        )
        self.assertEqual(analyser.lints, [])

    def test_destructuring_named_match_pattern_does_not_hide_later_case(self):
        analyser = Analyser()

        analyser.analyse(
            parse('1\nmatch =>\n  as x(field) => "structured"\n' '  _ => "other"\nend')
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(analyser.lints, [])
        self.assertEqual(analyser.lint_findings, [])

    def test_destructuring_named_match_pattern_is_not_exhaustive_by_itself(self):
        analyser = Analyser()

        analyser.analyse(parse('1\nmatch =>\n  as x(field) => "structured"\nend'))

        self.assertEqual(
            analyser.diagnostics,
            ["2:1: match without default requires enum or variant value"],
        )
        self.assertEqual(analyser.lints, [])

    def test_guarded_variant_member_does_not_make_match_exhaustive(self):
        analyser = Analyser()

        analyser.analyse(parse("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(2)
match =>
  as :Some if 0 1 == => "impossible"
  as :None => "none"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["7:1: non-exhaustive match for Maybe; missing cases: Maybe.Some"],
        )

    def test_restrictive_variant_destructure_does_not_make_match_exhaustive(self):
        analyser = Analyser()

        analyser.analyse(parse("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(2)
match =>
  as :Some(1) => "one"
  as :None => "none"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["7:1: non-exhaustive match for Maybe; missing cases: Maybe.Some"],
        )

    def test_type_pattern_destructuring_arity_is_checked(self):
        analyser = Analyser()

        analyser.analyse(parse("""
variant Maybe =>
  Some => $value: Number end
  None => end
end
Some(2)
match =>
  as :Some(_, _) => "two"
  as :None => "none"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            [
                "8:3: pattern for Maybe.Some destructures 2 fields, but the type "
                "declares 1"
            ],
        )

    def test_default_exposes_its_subject_as_a_cycle_input(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("1\nmatch =>\n  default => + 0\nend"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

    def test_match_result_can_supply_an_explicit_call_argument_above_outer_stack(self):
        analyser = Analyser()
        source = """
        define[T] flatten(xs: T~) -> T+ =>
          $res: T+ = []
          $xs foreach (x) =>
            $res := addAll($x match =>
              as xss: T~~ => flatten $xss
              default => ^+
            end)
          end
          $res
        end
        """

        analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertNotIn(
            "minimum-rank-never-wraps",
            tuple(finding.code for finding in analyser.lint_findings),
        )

    def test_or_pattern_with_wildcard_is_exhaustive_and_hides_later_cases(self):
        analyser = Analyser()

        analyser.analyse(
            parse('1\nmatch =>\n  1 || _ => "first"\n' '  2 => "second"\nend')
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            tuple(finding.code for finding in analyser.lint_findings),
            ("unreachable-match-case",),
        )

    def test_or_pattern_requires_the_same_bindings_in_every_alternative(self):
        analyser = Analyser()

        analyser.analyse(parse("1\nmatch =>\n  1 || $x = _ => $x\n" "  _ => 0\nend"))

        self.assertEqual(
            analyser.diagnostics,
            [
                "3:3: every alternative in an or-pattern must bind the same "
                "names; missing from some alternatives: x"
            ],
        )

    def test_or_pattern_binding_types_are_merged_across_alternatives(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '"s"\nmatch =>\n'
                "  as x: Number if 1 1 == || as x: String => $x + 1\n"
                "  _ => 0\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("overload", analyser.diagnostics[0])
        self.assertIn("Number | String", analyser.diagnostics[0])

    def test_match_binding_shadows_an_outer_variable(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                '$x = 1\n"abc"\nmatch =>\n'
                "  as x: String => pop_n(1) $x length\n"
                "  _ => 0\nend"
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

    def test_guarded_type_pattern_does_not_narrow_the_default_branch(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '$x = (if 1 1 == => 1 else => "s" end)\n'
                "$x\nmatch =>\n"
                "  as :Number if 0 1 == => 0\n"
                "  _ => $x length\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'length'", analyser.diagnostics[0])
        self.assertIn("Integer | String", analyser.diagnostics[0])

    def test_literal_pattern_does_not_narrow_the_default_branch_by_type(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '$x = (if 1 1 == => 2 else => "s" end)\n'
                "$x\nmatch =>\n"
                "  1 => 0\n"
                "  _ => $x length\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'length'", analyser.diagnostics[0])
        self.assertIn("Integer | String", analyser.diagnostics[0])

    def test_catchall_or_pattern_does_not_narrow_to_only_its_typed_arm(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '$x = (if 0 1 == => 1 else => "s" end)\n'
                "$x\nmatch =>\n"
                "  1 || _ => $x + 1\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("overload", analyser.diagnostics[0])
        self.assertIn("Integer | String", analyser.diagnostics[0])

    def test_list_pattern_does_not_narrow_unconstrained_items(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '$x = [1, "s"]\n$x\nmatch =>\n' "  [1, _] => $x sum\n" "  _ => 0\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'sum'", analyser.diagnostics[0])
        self.assertIn("String", analyser.diagnostics[0])

    def test_correlated_match_case_does_not_narrow_each_subject_independently(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                '$x = (if 1 1 == => 1 else => "x" end)\n'
                '$y = (if 0 1 == => 1 else => "y" end)\n'
                "$x $y\nmatch =>\n"
                "  as :Number, as :Number => 0\n"
                "  _, _ => $x length\nend"
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'length'", analyser.diagnostics[0])
        self.assertIn("Integer | String", analyser.diagnostics[0])

    def test_wildcard_coordinate_allows_independent_subject_narrowing(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                '$x = (if 0 1 == => 1 else => "x" end)\n'
                '$y = (if 1 1 == => 1 else => "y" end)\n'
                "$x $y\nmatch =>\n"
                "  _, as :Number => pop_n(1) 0\n"
                "  _, _ => $x length\nend"
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

    def test_repeated_case_binding_is_not_an_exhaustive_catchall(self):
        analyser = Analyser()

        analyser.analyse(parse("1 2\nmatch =>\n" '  $x = _, $x = _ => "same"\nend'))

        self.assertEqual(
            analyser.diagnostics,
            ["2:1: match without default requires one enum or variant value"],
        )

    def test_repeated_destructure_binding_does_not_cover_a_variant_member(self):
        analyser = Analyser()

        analyser.analyse(parse("""variant Pairish =>
  Pair =>
    $left: Number
    $right: Number
  end
end
Pair(1, 2)
match =>
  as :Pair(x, x) => "same"
end
"""))

        self.assertEqual(
            analyser.diagnostics,
            ["8:1: non-exhaustive match for Pairish; missing cases: " "Pairish.Pair"],
        )

    def test_repeated_binding_does_not_hide_a_fallback_case(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                "1 2\nmatch =>\n"
                '  $x = _, $x = _ => "same"\n'
                '  _, _ => "different"\nend'
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertNotIn(
            "unreachable-match-case",
            tuple(finding.code for finding in analyser.lint_findings),
        )

    def test_nested_nominal_variance_composes_automatically(self):
        from valiance.analysis.calls.signatures import _infer_generic_variance
        from valiance.vtypes import Context, N, ObjectAttribute, V, Variance
        from valiance.vtypes.symbols import Symbol

        ctx = Context()
        consumer = Symbol("Consumer")
        ctx.set_generic_variance(consumer, (Variance.CONTRAVARIANT,))
        inferred = _infer_generic_variance(
            (Symbol("T"),),
            (
                ObjectAttribute(
                    Symbol("consumer"),
                    N(consumer, V("T")),
                    Symbol("readable"),
                ),
            ),
            (),
            ctx,
        )
        self.assertEqual(inferred, (Variance.CONTRAVARIANT,))

    def test_double_negative_nested_variance_becomes_covariant(self):
        from valiance.analysis.calls.signatures import _infer_generic_variance
        from valiance.vtypes import Context, Fn, N, ObjectAttribute, String, V, Variance
        from valiance.vtypes.symbols import Symbol

        ctx = Context()
        consumer = Symbol("Consumer")
        ctx.set_generic_variance(consumer, (Variance.CONTRAVARIANT,))
        inferred = _infer_generic_variance(
            (Symbol("T"),),
            (
                ObjectAttribute(
                    Symbol("run"),
                    Fn((N(consumer, V("T")),), (String,)),
                    Symbol("readable"),
                ),
            ),
            (),
            ctx,
        )
        self.assertEqual(inferred, (Variance.COVARIANT,))





class WildcardPathSelectionAnalysisTests(unittest.TestCase):
    """Keep wildcard-path ranks aligned with runtime path gathering."""

    def test_two_component_wildcard_path_preserves_remaining_item_rank(self):
        """A rank-three receiver selected through two levels returns rank two."""
        source = """
[
  [[1,2,3],[4,5,6],[7,8,9]],
  [[10,11,12],[13,14,15],[16,17,18]],
  [[19,20,21],[22,23,24],[25,26,27]]
] $[[_, _]]
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(same(typed[-1].typ, C(ListExactType, Integer, 2)))

    def test_wildcard_path_rank_is_gather_plus_unconsumed_receiver_rank(self):
        """Literal path depth consumes ranks before one gathered rank is added."""
        cases = (
            ("$xs = [[[1]]]\n$xs[[\\None]]", 3),
            ("$xs = [[[1]]]\n$xs[[\\None, \\None]]", 2),
            ("$xs = [[[1]]]\n$xs[[\\None, \\None, \\None]]", 1),
        )
        for source, rank in cases:
            with self.subTest(source=source):
                analyser = Analyser()
                typed = analyser.analyse(parse(source))
                self.assertEqual(analyser.diagnostics, [])
                self.assertTrue(same(typed[-1].typ, C(ListExactType, Integer, rank)))

if __name__ == "__main__":
    unittest.main()


class ForeachRefactoringLintTests(unittest.TestCase):
    def _analyse(self, source: str):
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return analyser

    def test_stateless_foreach_suggests_vectorisation(self):
        analyser = self._analyse("[1, 2, 3] foreach (n) => $n 1 + end")
        self.assertEqual(
            [finding.code for finding in analyser.lint_findings],
            ["prefer-vectorisation-or-map"],
        )
        self.assertIn("prefer vectorisation", analyser.lints[0])

    def test_length_foreach_suggests_map_not_vectorisation(self):
        analyser = self._analyse("[[1], [2], [3], [4]] foreach (n) => length $n end")
        self.assertIn("prefer map", analyser.lints[0])
        self.assertNotIn("prefer vectorisation", analyser.lints[0])

    def test_stateless_foreach_suggests_map_for_non_vectorising_body(self):
        analyser = self._analyse(
            "define first(xs: Number+ novec) -> Number => $xs $[0] end\n"
            "[[1], [2], [3]] foreach (xs) => first($xs) end"
        )
        self.assertEqual(
            [finding.code for finding in analyser.lint_findings],
            ["prefer-vectorisation-or-map"],
        )
        self.assertIn("prefer map", analyser.lints[0])

    def test_foreach_mutating_outer_variable_is_not_linted(self):
        analyser = self._analyse(
            "$total = 0\n[1, 2, 3] foreach (n) => $total := + $n end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertIn("prefer-sum", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_foreach_modifying_two_outer_variables_does_not_suggest_fold(self):
        analyser = self._analyse(
            "$left = 0\n$right = 0\n"
            "[1, 2, 3] foreach (n) => "
            "$left := + $n | $right := + $n end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_foreach_update_unrelated_to_loop_item_does_not_suggest_fold(self):
        analyser = self._analyse(
            "$total = 0\n[1, 2, 3] foreach (n) => $total := + 1 end"
        )
        self.assertNotIn(
            "prefer-fold",
            [finding.code for finding in analyser.lint_findings],
        )

    def test_stateless_foreach_with_break_is_not_linted(self):
        analyser = self._analyse(
            "[1, 2, 3] foreach (n) => if ($n 2 ==) => break $n end end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_accumulating_foreach_with_break_is_not_linted(self):
        analyser = self._analyse(
            "$total = 0\n"
            "[1, 2, 3] foreach (n) => "
            "$total := + $n | if ($n 2 ==) => break end end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_foreach_with_return_is_not_linted(self):
        analyser = self._analyse(
            "define firstPositive(xs: Number+) -> Number =>\n"
            "  $xs foreach (n) => if ($n 0 >) => return $n end end\n"
            "  0\n"
            "end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertNotIn("prefer-fold", codes)

    def test_node_lint_off_suppresses_all_lints_for_foreach(self):
        analyser = self._analyse(
            "$total = 0\n@lintOff\n" "[1, 2, 3] foreach (n) => $total := + $n end"
        )
        self.assertEqual(analyser.lint_findings, [])

    def test_node_lint_off_can_suppress_only_prefer_fold(self):
        analyser = self._analyse(
            '$total = 0\n@lintOff("prefer-fold")\n'
            "[1, 2, 3] foreach (n) => $total := + ($n as[Integer]) end"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-fold", codes)
        self.assertIn("redundant-cast", codes)

    def test_file_lint_off_suppresses_one_lint_code(self):
        analyser = self._analyse(
            '@lintFileOff("prefer-fold")\n'
            "$total = 0\n"
            "[1, 2, 3] foreach (n) => $total := + $n end\n"
            "1 as[Integer]"
        )
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-fold", codes)
        self.assertIn("redundant-cast", codes)

    def test_file_lint_off_without_codes_suppresses_all_lints(self):
        analyser = self._analyse(
            "@lintFileOff\n"
            "$total = 0\n"
            "[1, 2, 3] foreach (n) => $total := + $n end\n"
            "1 as[Integer]"
        )
        self.assertEqual(analyser.lint_findings, [])

    def test_foreach_with_element_tags_is_not_linted(self):
        analyser = self._analyse("[1, 2, 3] foreach (n) => println($n) end")
        codes = [finding.code for finding in analyser.lint_findings]
        self.assertNotIn("prefer-vectorisation-or-map", codes)
        self.assertNotIn("prefer-fold", codes)


class BareParameterElementDiagnosticTests(unittest.TestCase):
    def test_bare_parameter_name_is_suggested_and_prioritised(self):
        analyser = Analyser()
        source = (
            "define quad(a, b, c) => "
            "0 - $b | sqrt(4 * $a * c - (2 * $b)) | [+, -] / (2 * $a)"
        )

        analyser.analyse(parse(source))

        self.assertTrue(analyser.diagnostics)
        self.assertEqual(
            analyser.diagnostics[0],
            "1:48: unknown element 'c'\ndid you mean '$c'?",
        )


class DeclaredDefinitionInterfaceTests(unittest.TestCase):
    def test_fully_typed_definition_is_visible_inside_nested_call_arguments(self):
        analyser = Analyser()
        source = """
        define countdown(n: Integer) -> Integer =>
          if ($n == 0) => 0
          else => +(countdown($n - 1), 1)
          end
        end
        countdown 5
        """

        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, Integer)

    def test_declared_interface_remains_visible_after_invalid_body(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse(
                "define bad(x: Integer) -> String => $x end\n"
                "bad 1"
            )
        )

        self.assertEqual(typed[-1].typ, String)
        self.assertFalse(
            any("unknown element 'bad'" in item for item in analyser.diagnostics)
        )


class RecursiveBindingAnalysisTests(unittest.TestCase):
    def test_unannotated_this_in_unreachable_match_case_is_rejected(self):
        analyser = Analyser()
        source = """define[T] flatten(list: T~) -> T+ =>
  $flattened: T+ = []
  $list foreach (item) =>
    $piece = $item match =>
      as lst: T+ => $lst
      as scl: T => [$scl]
      _ => this($item)
    end
    $flattened := addAll($piece)
  end
  $flattened
end
"""
        analyser.analyse(parse(source))
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("'this' is only available in functions annotated @recursive", analyser.diagnostics[0])


class FirstClassOverloadSetCallAnalysisTests(unittest.TestCase):
    def test_empty_variable_call_selects_generic_list_overload_for_rank_two(self):
        analyser = Analyser()
        typed = analyser.analyse(parse("""
$f = fn (xs) => $xs 1 rotate end
[[1, 2], [3, 4], [5, 6]] $f()
"""))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(T.show(typed[-1].typ), "Integer+2")
