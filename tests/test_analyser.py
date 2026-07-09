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
from valiance.analysis.analyser import _branch_argument_substitution
from valiance.analysis.annotations import AnnotationSpec, register_annotation
from valiance.analysis.builtins import BUILTIN_ELEMENTS
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
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedTagApplicationNode,
)
from valiance.modules import ModuleLoader
from valiance.parsing import parse
from valiance.symbols import Symbol
from valiance.types import (
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
    U,
    UnknownElement,
    V,
    Variance,
    WithTag,
    assignable,
    optional,
    show,
)
from valiance.types.default_types import Boolean

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
        analyser.analyse(
            parse(
                """
object WriteFile =>
  @error("Writeable files cannot be duplicated")
  define dup => end
end

WriteFile
copy(file -> file)
"""
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("cannot copy value of type WriteFile", analyser.diagnostics[0])
        self.assertIn(
            "Writeable files cannot be duplicated",
            analyser.diagnostics[0],
        )

    def test_stack_shuffle_move_rejects_uncopyable_repeated_output(self):
        analyser = Analyser()
        analyser.analyse(
            parse(
                """
object WriteFile =>
  @error("Writeable files cannot be duplicated")
  define dup => end
end

WriteFile
move(file -> file, file)
"""
            )
        )

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
            ],
        )

        self.assertEqual([node.typ for node in typed], [Integer, Real, Real, Integer])

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

    def test_explicit_element_tag_sets_reject_undeclared_body_effects(self):
        analyser = Analyser()

        analyser.analyse(parse("define log(value: Number)<> -> => $value println"))

        self.assertIn("was not declared", analyser.diagnostics[-1])

    def test_element_tag_disjoint_rules_reject_simultaneous_tags(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                """
tag Read as property
tag Write as property
tag Read disjoint Write
define \\f<Read, Write> => 1
"""
            )
        )

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

    def test_element_records_vectorised_overload_application(self):
        typed = analyse(parse("[1, 2, 3] + [4, 5, 6]"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(
            typed[-1].overload.actual_returns,
            (C(ListExactType, Integer),),
        )

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
            [
                "1:18: extend default must be compatible with every "
                "element parameter"
            ],
        )

    def test_extend_selector_arity_must_match_target(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                """
define choose(a: Integer?) -> Integer? => $a end
[1, 2] [3] + extend: choose
"""
            )
        )

        self.assertEqual(
            analyser.diagnostics,
            ["3:14: extend selector arity must match the target element arity"],
        )

    def test_element_disambiguation_controls_vectorisation_depth(self):
        typed = analyse(parse("[[1, 2], [3, 4]] +[Number+, _] [10, 20]"))

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

    def test_object_mustcall_methods_must_exist(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                """
@mustcall(all = ["commit"])
object Tx =>
  define rollback => end
end
"""
            )
        )

        self.assertIsNone(typed[0].typ)
        self.assertTrue(
            any(
                "@mustcall method 'commit' is not defined on Tx" in message
                for message in analyser.diagnostics
            )
        )

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
            env.context.variant_members[Symbol("Some", ("Maybe",))],
            Symbol("Maybe"),
        )
        self.assertEqual(
            env.lookup_variant(Symbol("Maybe")).members[0],
            Symbol("Some", ("Maybe",)),
        )

    def test_generic_variant_constructor_preserves_type_argument(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(
            parse(
                """
variant[T] Maybe =>
  Some => $value: T end
  None => end
end
1
Some
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Maybe"), Integer))

    def test_object_generic_variance_is_inferred_from_readable_fields(self):
        env = Environment()
        analyser = Analyser(env)

        typed = analyser.analyse(
            parse(
                """
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
"""
            )
        )

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

        analyser.analyse(
            parse(
                """
trait Vehicle => end
object[T: any Vehicle] Source => $value: T end
object[T: above Vehicle] Sink => $consume: Function[T ->] end
"""
            )
        )

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

        typed = analyser.analyse(
            parse(
                """
object Car => end
object[T] Box =>
  $value: T
end
Car
Box
$.value
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Car")))

    def test_labelled_generic_upper_bound_rejects_supertype_solution(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
trait Vehicle => end
object Car => end
object Car as Vehicle => end
define \\asVehicle -> Vehicle => Car end
define[T: any Car] accept(value: T) -> T => $value end
\\asVehicle
accept
"""
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'accept' match stack [Vehicle]",
            analyser.diagnostics[0],
        )

    def test_labelled_generic_lower_bound_accepts_supertype_solution(self):
        analyser = Analyser(Environment())

        typed = analyser.analyse(
            parse(
                """
trait Vehicle => end
object Car => end
object Car as Vehicle => end
define \\asVehicle -> Vehicle => Car end
define[T: above Car] accept(value: T) -> T => $value end
\\asVehicle
accept
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Vehicle")))

    def test_invariant_generic_object_rejects_substituted_supertype_argument(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
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
"""
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'accept' match stack [Cell[Car]]",
            analyser.diagnostics[0],
        )

    def test_anonymous_trait_function_parameter_is_structural(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
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
"""
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'keep'", analyser.diagnostics[0])

    def test_anonymous_trait_collection_parameter_solves_item_type(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                """
define[T] sum(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => fold: +
[1, 2, 3]
sum
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Integer")))

    def test_anonymous_trait_requirement_contributes_generic_constraints(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                """
define[T, U] dotProd(
  left: trait =>
    extend +(:T, :T) -> T
    extend *(:T, :U) -> T
  end +,
  right: U+
) =>
  * | fold: +
end

[1, 2, 3] dotProd [4, 5, 6]
"""
            )
        )

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

    def test_generic_function_literal_uses_declared_generics(self):
        [typed] = analyse(parse("fn[T] (item: T) -> T => $item"))

        self.assertEqual(typed.typ, Fn((V("T"),), (V("T"),)))

    def test_match_infers_missing_inputs_from_multiple_patterns(self):
        typ = analyse_function(
            FunctionNode(
                body=tuple(
                    parse(
                        """
match =>
  1, "x" => "hit"
  _, _ => "miss"
end
"""
                    )
                )
            ),
            Environment(),
        )

        self.assertEqual(typ, Fn((String, Number), (String,)))

    def test_match_requires_cases_to_have_same_arity(self):
        analyser = Analyser(Environment())

        analyser.analyse(
            parse(
                """
1 2
match =>
  1, 2 => "two"
  _ => "one"
end
"""
            )
        )

        self.assertEqual(
            analyser.diagnostics,
            ["3:1: match cases must match the same number of values"],
        )

    def test_result_question_unwraps_success_type(self):
        typed = analyse(
            parse(
                """
object ParseError => end
object ParseError as Err => end
define unwrap(x: Result[Number, ParseError]) -> Number =>
  $x ?
end
"""
            )
        )

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

        analyser.analyse(
            parse(
                """
$x = 5
define timesFive(y: Number) -> Number => $x $y *
"""
            )
        )

        self.assertEqual(
            analyser.diagnostics,
            ["3:42: cannot capture top-level assignment 'x'"],
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
where ($n = length $shape) => $xs as! T+$n
[[1, 2, 3], [4, 5, 6]] reshape {4, 5, 6}
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
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

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'invalid_static' match",
            analyser.diagnostics[0],
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
            BranchSet((AnalysisBranch(stack=TypeStack((N(FOO),))),)),
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

    def test_err_type_variant_marks_members_and_parent_as_err(self):
        analyser = Analyser()

        analyser.analyse(
            parse(
                """
@errType variant DBError =>
  ConnectionClosedError => end
end
"""
            )
        )

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
        typed = analyse(parse("'+ | call(1, 2)"))

        self.assertIsInstance(typed[-1], TypedElementNode)
        self.assertEqual(typed[-1].typ, Integer)
        self.assertEqual(typed[-1].overload.actual_returns, (Integer,))
        self.assertEqual(len(typed[-1].overload.params), 3)

    def test_optional_parameters_do_not_change_plain_element_arity(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(
                parse(
                    """
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick
"""
                )
            ),
        )

        self.assertFalse(branches)
        self.assertEqual(
            analyser.diagnostics,
            [
                "3:1: no overloads for element 'pick' match stack [Number]; "
                "available overloads: Function[Number, Number -> Number]"
            ],
        )

    def test_optional_parameters_can_be_overridden_with_named_ecs(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(
                parse(
                    """
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick(b = 3)
"""
                )
            ),
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

    def test_optional_parameters_can_be_overridden_with_placeholder_ecs(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(stack=TypeStack((Number,))),)),
            tuple(
                parse(
                    """
define pick(a: Number, b: Number = 2) -> Number => $a $b +
pick(_, 4)
"""
                )
            ),
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

        analyser.analyse(
            parse(
                """
define countdown(n: Number) -> Number =>
  if ($n 0 >) => $n 1 - countdown else => 0 end
end
3 countdown
"""
            )
        )

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

    def test_constructed_tags_drop_without_overlay_preservation(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(parse("tag #sticky as constructed\n1 #sticky 2 +")),
        )

        self.assertEqual(branch.stack, TypeStack((Integer,)))

    def test_tag_overlay_preserves_computed_tag_without_runtime_override(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(
                parse(
                    """
tag #sorted as computed
#sorted: + =>
  (#sorted Number, Number) -> #sorted Number
end
1 #sorted | 2 +
"""
                )
            ),
        )

        self.assertEqual(branch.stack, TypeStack((Tagged(Number, "sorted"),)))
        self.assertIsInstance(branch.typed_body[-1], TypedElementNode)
        self.assertIsNotNone(branch.typed_body[-1].overload_index)

    def test_static_true_tag_validator_is_eliminated_at_application(self):
        analyser = Analyser()
        [branch] = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(
                parse(
                    """
tag #checked as computed
define #checked(:Number) -> #boolean Number => true end
1 #checked
"""
                )
            ),
        )

        self.assertEqual(branch.stack, TypeStack((Tagged(Integer, "checked"),)))
        self.assertIsInstance(branch.typed_body[-1], TypedTagApplicationNode)
        self.assertIsNone(branch.typed_body[-1].validator_index)

    def test_static_false_tag_validator_is_rejected_at_application(self):
        analyser = Analyser()
        analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(
                parse(
                    """
tag #checked as computed
define #checked(:Number) -> #boolean Number => false end
1 #checked
"""
                )
            ),
        )

        self.assertIn("statically false", analyser.diagnostics[-1])

    def test_tag_validator_missing_overload_is_diagnostic(self):
        analyser = Analyser()
        branches = analyser.analyse_block(
            BranchSet((AnalysisBranch(),)),
            tuple(
                parse(
                    """
tag #checked as computed
define #checked(:String) -> #boolean Number => true end
1 #checked
"""
                )
            ),
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

        analyser.analyse(
            parse(
                """
tag #left as computed
tag #right as computed
tag #left disjoint #right
define f(value: #left #right Number) -> Number => $value
"""
            )
        )

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

    def test_constructed_tags_do_not_propagate_through_generic_returns(self):
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
            TypeStack((C(ListExactType, Number),)),
        )

    def test_constructed_tags_do_not_propagate_to_output_depth(self):
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
            TypeStack((C(ListExactType, Number, 2),)),
        )

    def test_multiple_constructed_like_tags_drop_without_overlay(self):
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
        self.assertEqual(next(iter(branches)).stack, TypeStack((Number,)))

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

    def test_length_of_finite_list_returns_number(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("print length [1, 2, 3, 4, 5]"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            [node.typ for node in typed],
            [C(ListExactType, Integer), Number, None],
        )

    def test_length_rejects_infinite_list(self):
        analyser = Analyser()

        typed = analyser.analyse(parse("[1, 2, 3] | #infinite | length"))

        self.assertEqual([node.typ for node in typed], [None, None, None])
        self.assertEqual(
            analyser.diagnostics,
            [
                "1:25: no overloads for element 'length' match stack [#infinite "
                "Integer+]; available overloads: Function[#!infinite Item+ -> Number]"
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

    def test_list_indexing_requires_integer_index(self):
        analyser = Analyser()

        analyser.analyse(parse("[1, 2] $[1.5]"))

        self.assertEqual(
            analyser.diagnostics,
            ["1:8: list indexing requires Integer index value(s)"],
        )

    def test_sum_accumulator_widens_from_integer_initializer(self):
        analyser = Analyser()

        typed = analyser.analyse(
            parse(
                """
define sum =>
  $res = 0
  foreach (item) => $res := + $item
  $res
end
"""
            )
        )

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
        self.assertEqual(typed[-1].typ, Integer)

        analyser = Analyser()
        typed = analyser.analyse(parse("1 unfold (< 5) -> (n: Number) => $n 1 + end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, WithTag(ExactList(Number), "infinite"))

        analyser = Analyser()
        typed = analyser.analyse(parse("0 1 unfold => + end"))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, WithTag(ExactList(Integer), "infinite"))

    def test_unfold_rejects_more_than_state_plus_emission(self):
        analyser = Analyser()
        analyser.analyse(
            parse(
                """
1 unfold -> (n: Integer) =>
  $n
  dup
  dup
end
"""
            )
        )

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
        typed = analyse(
            [
                ListLiteralNode(
                    (
                        (NumberLiteralNode("1"),),
                        (StringLiteralNode("x"),),
                    )
                ),
                ElementNode(Symbol("head")),
                CastNode(Number, checked=True),
            ]
        )

        self.assertEqual(typed[-1].typ, Number)

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


if __name__ == "__main__":
    unittest.main()
