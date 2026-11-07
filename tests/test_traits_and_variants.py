"""Source-level contracts for trait analysis and closed variant semantics.

Keep acceptance, diagnostic and executable expectations together so a permissive
analyser cannot hide incorrect dispatch or lost generic evidence.
"""

import unittest

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.interface_serialization import dumps_interface, loads_interface


class SemanticTestCase(unittest.TestCase):
    def analyse(self, source):
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [], source)
        return typed

    def assert_executes(self, source, expected):
        typed = self.analyse(source)
        restored = loads_interface(dumps_interface(tuple(typed)))
        for optimize in (False, True):
            for interface, nodes in ((False, typed), (True, restored)):
                with self.subTest(optimize=optimize, interface=interface):
                    program = compile_program(nodes, optimize=optimize)
                    self.assertEqual(run(program), expected)
                    self.assertEqual(run(loads(dumps(program))), expected)

    def assert_rejected(self, source, message):
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(
            any(message in diagnostic for diagnostic in analyser.diagnostics),
            analyser.diagnostics,
        )


class TraitSemanticTests(SemanticTestCase):
    def test_variant_implementation_target_diagnostic(self):
        for target in ("Entry", "Entry[Int]"):
            with self.subTest(target=target):
                source = (
                    "variant[T] Entry => File => end end\n"
                    "object \\NotAFile => end\n"
                    f"object \\NotAFile as {target} => end\n"
                )
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertIn(
                    "3:21: cannot implement 'Entry': it is a variant, not a trait",
                    analyser.diagnostics,
                )

    def test_invalid_implementation_target_location(self):
        analyser = Analyser()
        analyser.analyse(parse(
            "object \\Item => end\nobject \\Item as Missing => end\n"
        ))
        self.assertIn(
            "2:17: trait implementation target must be a named trait",
            analyser.diagnostics,
        )

    def test_multiple_requirements_and_receiver_arguments(self):
        self.assert_executes('''
trait Counter =>
  extend count -> Int
  extend plus(:Int) -> Int
end
object Item => $value: Int end
object Item as Counter =>
  define count => $self.value
  define plus(n: Int) => $self.value + $n
end
define total(item: Counter) -> Int => ($item count) + (5 | plus $item) end
Item(7) total
''', [19])

    def test_default_can_be_overridden_per_object(self):
        self.assert_executes('''
trait Named => define label -> String => "default" end
object First => $id: Int end
object Second => $id: Int end
object First as Named => end
object Second as Named => define label -> String => "override" end
First(1) label
Second(2) label
First(3) label
''', ['default', 'override', 'default'])

    def test_inherited_requirement_dispatch(self):
        self.assert_executes('''
trait Parent => extend size -> Int end
trait Child as Parent => end
object Item => $value: Int end
object Item as Child => define size => $self.value end
define measure(item: Parent) -> Int => $item size end
Item(11) measure
''', [11])

    def test_missing_abstract_requirement_is_rejected(self):
        self.assert_rejected('''
trait Named => extend label -> String end
object Item => $id: Int end
object Item as Named => end
''', 'label')

    def test_inherited_default_calls_inherited_requirement(self):
        self.assert_executes('''
trait Parent =>
  extend size -> Int
  define doubled -> Int => ($self size) * 2
end
trait Child as Parent => end
object Item => $value: Int end
object Item as Child => define size => $self.value end
Item(6) doubled
''', [12])

    def test_requirement_signature_rejections(self):
        for implementation in (
            'define convert(value: String) -> Int => 1',
            'define convert(value: Int) -> String => "wrong"',
            'define convert -> Int => 1',
        ):
            with self.subTest(implementation=implementation):
                self.assert_rejected(
                    'trait Converter => extend convert(:Int) -> Int end\n'
                    'object Item => end\n'
                    f'object Item as Converter => {implementation} end',
                    'convert',
                )

    def test_nominal_trait_requires_explicit_implementation(self):
        self.assert_rejected('''
trait Named => extend name -> String end
object Item =>
  $label: String
  public define name => $self.label
end
define accept(item: Named) => $item name end
Item("item") accept
''', 'accept')

    def test_implementation_can_reuse_object_method(self):
        self.assert_executes('''
trait Named => extend name -> String end
object Item =>
  $label: String
  public define name => $self.label
end
object Item as Named => end
define describe(item: Named) => $item name end
Item("item") describe
''', ['item'])

    def test_default_calls_abstract_requirement(self):
        self.assert_executes('''
trait Sized =>
  extend size -> Int
  define doubled -> Int => ($self size) * 2
end
object Item => $value: Int end
object Item as Sized => define size => $self.value end
Item(7) doubled
''', [14])

    def test_generic_trait_keeps_independent_instantiations(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
define readInt(item: Source[Int]) -> Int => $item read end
define readText(item: Source[String]) -> String => $item read end
Box(7) readInt
Box("text") readText
Box(9) readInt
''', [7, 'text', 9])

    def test_generic_trait_rejects_wrong_instantiation(self):
        self.assert_rejected('''
trait[T] Source => extend read -> T end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[String]) => $item read end
Box(7) accept
''', 'accept')

    def test_concrete_object_implements_exact_generic_trait(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
trait[T] Reader => extend first -> T end
trait[T] Source as Reader[T] => define first -> T => $self read end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[Int]) -> Int => $item read end
Box(7) accept
Box(8) first
''', [7, 8])

    def test_concrete_generic_trait_implementation_respects_variance(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
object Box => $value: Int end
object Box as Source[Int] => define read => $self.value end
define accept(item: Source[Number]) -> Number => $item read end
Box(7) accept
''', [7])

    def test_transitive_trait_behaviour_preserves_generic_result(self):
        self.assert_executes('''
trait[T] Source => extend read -> T end
trait[T] Reader => extend first -> T end
trait[T] Source as Reader[T] => define first -> T => $self read end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
Box(8) first
Box("eight") first
''', [8, 'eight'])

    def test_trait_union_requires_every_alternative_to_support_call(self):
        self.assert_rejected('''
trait Sized => extend size -> Int end
object Item => end
object Item as Sized => define size => 1 end
define invalid(item: Sized | Int) => $item size end
''', 'size')

    def test_anonymous_trait_accepts_structural_method(self):
        self.assert_executes('''
object Item => $id: Int
  define size(item: $self) -> Int => 12 end
define measure(item: trait => extend size -> Int end) => $item size end
Item(0) measure
''', [12])

    def test_anonymous_trait_rejects_incompatible_method(self):
        self.assert_rejected('''
object Item => $id: Int
  define size(item: $self) -> String => "wrong" end
define measure(item: trait => extend size -> Int end) => $item size end
Item(0) measure
''', 'measure')


MAYBE = '''
variant[T] Maybe =>
  Some => $value: T end
  Empty => end
end
'''


class VariantSemanticTests(SemanticTestCase):
    def test_case_constructors_pass_directly_to_trait_parameters(self):
        self.assert_executes(r'''
trait Drawable => extend draw -> String end
variant Colour =>
  RED
  RED as Drawable => define draw => "red" end
  BLUE
end
variant Shape =>
  Circle => $radius: Int end
  Circle as Drawable => define draw => "circle" end
end
define render(value: Drawable) -> String => $value draw end
render(Colour.\RED)
render(Shape.Circle(8))
''', ['red', 'circle'])

    def test_nested_case_trait_implementation_inherits_variant_generics(self):
        self.assert_executes('''
trait[T] Drawable => extend draw -> T end
variant[T] Shape =>
  Circle => $radius: T end
  Circle as Drawable[T] => define draw => $self.radius end
  Empty
end
define render(value: Drawable[Int]) -> Int => $value draw end
Shape.Circle(8) match =>
  as :Circle => render
  as :Empty => 0
end
''', [8])

    def test_nested_case_trait_implementation_checks_requirements(self):
        self.assert_rejected('''
trait Drawable => extend draw -> Int end
variant Shape => Circle Circle as Drawable => end end
''', 'does not satisfy required element')

    def test_bare_and_explicit_empty_cases_have_the_same_value_behavior(self):
        self.assert_executes(r'''
variant Colour =>
  RED
  GREEN => end
end
Colour.\RED Colour.\RED ===
Colour.\RED Colour.\GREEN ===
dict{Colour.\RED => 7, Colour.\GREEN => 9} $[Colour.\RED]
''', [1, 0, 7])

    def test_inline_bare_cases_are_exhaustive_objects(self):
        self.assert_executes(r'''
variant Colour => RED GREEN BLUE end
define code(value: Colour) -> Int =>
  $value match =>
    as :RED => 1
    as :GREEN => 2
    as :BLUE => 3
  end
end
Colour.\GREEN code
''', [2])

    def test_backing_values_are_readable_nominal_object_fields(self):
        self.assert_executes(r'''
variant Token =>
  NUMBER = "number"
  DIGIT = "number"
  SYMBOL = "+"
end
Token.\NUMBER.value
Token.\NUMBER Token.\DIGIT ===
dict{Token.\NUMBER => 7, Token.\DIGIT => 9} $[Token.\DIGIT]
''', ['number', 0, 9])

    def test_backing_value_cases_support_friendly_implementations(self):
        self.assert_executes(r'''
variant Colour =>
  extend label -> String
  define twice -> String => ($self label) + ($self label)
  RED = "red" =>
    define label => $self.value
  end
  GREEN = "green" =>
    define label => $self.value
  end
end
Colour.\RED twice
''', ['redred'])

    def test_bare_cases_mix_with_generic_payload_objects(self):
        self.assert_executes(r'''
variant[T] Choice =>
  Empty
  Item => $value: T end
end
define unwrap(value: Choice[Int]) -> Int =>
  $value match =>
    as :Empty => 0
    as :Item(item) => $item
  end
end
Choice.\Empty[Int] unwrap
Item(8) unwrap
dict{Item(3) => 9} $[Item(3)]
''', [0, 8, 9])

    def test_bare_cases_can_implement_additional_traits(self):
        self.assert_executes(r'''
trait Named => extend label -> String end
variant Colour => RED GREEN end
object Colour.RED as Named => define label => "red" end
define read(value: Named) -> String => $value label end
Colour.\RED match =>
  as :RED => read
  as :GREEN => "green"
end
''', ['red'])

    def test_backing_value_cannot_be_supplied_as_a_constructor_argument(self):
        self.assert_rejected('''
variant Token => NUMBER = "number" end
Token.NUMBER("other")
''', 'no overloads')

    def test_backing_value_cases_can_store_additional_payload_fields(self):
        self.assert_executes(r'''
variant Code =>
  Entry = 1 => $message: String end
end
$entry = Code.Entry("hello")
$entry.value
$entry.message
dict{Code.Entry("hello") => 7} $[Code.Entry("hello")]
''', [1, 'hello', 7])

    def test_tagged_object_payloads_preserve_dictionary_key_equality(self):
        self.assert_executes('''
variant Entry => Item => $value: #boolean Int end end
dict{Item(true) => 7} $[Item(true)]
''', [7])

    def test_parent_field_access_requires_the_field_on_every_case(self):
        self.assert_rejected(r'''
variant Choice =>
  Empty
  Item => $value: Int end
end
Choice.\Empty.value
''', 'value')

    def test_backing_value_is_read_only(self):
        self.assert_rejected(r'''
variant Token => NUMBER = "number" end
$token = Token.\NUMBER
$token.value = "other"
''', 'value')

    def test_bare_cases_still_require_exhaustive_matching(self):
        self.assert_rejected('''
variant Choice => First Second end
define choose(value: Choice) -> Int =>
  $value match => as :First => 1 end
end
''', 'exhaustive')

    def test_niladic_member_constructors(self):
        self.assert_executes(r'''
variant Colour =>
  \RED => end
  \GREEN => end
  \BLUE => end
end
define code(value: Colour) -> Int => 1 end
\RED code
\GREEN code
\BLUE code
''', [1, 1, 1])

    def test_nullary_and_payload_members_are_both_executed(self):
        self.assert_executes('''
variant Choice =>
  Present => $value: Int end
  Absent => end
end
define unwrap(value: Choice) -> Int =>
  $value match =>
    as :Present(item) => $item
    as :Absent => 0
  end
end
Present(8) unwrap
Absent
unwrap
''', [8, 0])

    def test_match_bindings_do_not_escape_case(self):
        self.assert_rejected(MAYBE + '''
Some(1) match =>
  as :Some(item) => $item
  as :Empty => 0
end
$item
''', 'item')

    def test_payload_field_order_is_preserved(self):
        self.assert_executes('''
variant Entry =>
  Pair =>
    $first: Int
    $second: String
  end
end
Pair(7, "seven") match =>
  as :Pair(number, text) => $number $text
end
''', [7, 'seven'])

    def test_distinct_variants_are_not_interchangeable(self):
        self.assert_rejected('''
variant Left => First => $value: Int end end
variant Right => Second => $value: Int end end
define accept(value: Left) => $value end
Second(1) accept
''', 'accept')

    def test_generic_constructor_runtime_evidence(self):
        typed = self.analyse(MAYBE + 'Some(1)\nSome("text")')
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                program = compile_program(typed, optimize=optimize)
                for candidate in (program, loads(dumps(program))):
                    values = run(candidate)
                    self.assertEqual([value.type_name for value in values],
                                     ['Maybe.Some', 'Maybe.Some'])
                    self.assertEqual([value.type_args for value in values],
                                     [('Int',), ('String',)])

    def test_constructor_inference_keeps_independent_arguments(self):
        for expression, expected in (('Some(1)', 'Maybe.Some[Int]'),
                                     ('Some("text")', 'Maybe.Some[String]'),
                                     ('Some([1, 2])', 'Maybe.Some[Int+]')):
            with self.subTest(expression=expression):
                typed = self.analyse(MAYBE + expression)
                self.assertEqual(T.show(typed[-1].typ), expected)

    def test_qualified_and_short_constructors_agree(self):
        typed = self.analyse(MAYBE + 'Some(4)\nMaybe.Some(9)')
        self.assertEqual(T.show(typed[-1].typ), 'Maybe.Some[Int]')
        values = run(loads(dumps(compile_program(typed))))
        self.assertEqual([value.fields['value'] for value in values], [4, 9])
        self.assertEqual([value.type_args for value in values], [('Int',), ('Int',)])

    def test_generic_match_specializes_payload_type(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    as :Some(item) => $item
    as :Empty => 0
  end
end
Some(4) unwrap
Maybe.Some(9) unwrap
''', [4, 9])

    def test_constructor_argument_type_and_arity(self):
        declaration = 'variant Choice => Item => $value: Int end end\n'
        for expression in ('Item("wrong")', 'Item(1, 2)'):
            with self.subTest(expression=expression):
                self.assert_rejected(declaration + expression, 'Item')

    SHAPES = '''
variant Shape =>
  extend area -> Int
  Square =>
    $side: Int
    define area => $self.side * $self.side
  end
  Rectangle =>
    $width: Int
    $height: Int
    define area => $self.width * $self.height
  end
end
'''

    def test_all_members_dispatch_through_parent_interface(self):
        self.assert_executes(self.SHAPES + '''
define measure(shape: Shape) -> Int => $shape area end
Square(3) measure
Rectangle(4, 5) measure
''', [9, 20])

    def test_variant_default_calls_abstract_method_and_supports_overrides(self):
        self.assert_executes('''
variant Shape =>
  extend area -> Int
  define doubled -> Int => ($self area) * 2
  define label -> String => "shape"
  Square =>
    $side: Int
    define area => $self.side * $self.side
  end
  Rectangle =>
    $width: Int
    $height: Int
    define area => $self.width * $self.height
    define label -> String => "rectangle"
  end
end
define describe(shape: Shape) -> String => $shape label end
Square(3) doubled
Rectangle(2, 3) doubled
Square(2) label
Rectangle(2, 3) label
Square(4) describe
Rectangle(4, 5) describe
''', [18, 12, "shape", "rectangle", "shape", "rectangle"])

    def test_variant_extend_may_supply_default_and_be_overridden(self):
        self.assert_executes('''
variant Choice =>
  extend score -> Int
  define score -> Int => 2
  First => $id: Int end
  Second =>
    $id: Int
    define score => 9
  end
end
define value(item: Choice) -> Int => $item score end
First(1) score
Second(2) score
First(3) value
Second(4) value
''', [2, 9, 2, 9])

    def test_generic_variant_default_specializes_overrides(self):
        self.assert_executes('''
variant[T] Choice =>
  define score -> Int => 3
  First => $value: T end
  Second =>
    $value: T
    define score -> Int => 5
  end
end
First(1) score
Second(2) score
Second("text") score
First("text") score
''', [3, 5, 5, 3])

    def test_generic_variant_default_parameter_and_result_types(self):
        self.assert_executes('''
variant[T] Value =>
  define fallback(value: T) -> T => $value
  First => $value: T end
  Second =>
    $value: T
    define fallback(value: T) -> T => $self.value
  end
end
First(8) | fallback 9
Second(8) | fallback 9
First("a") | fallback "b"
Second("a") | fallback "b"
''', [9, 8, "b", "a"])

    def test_generic_variant_default_can_call_abstract_method(self):
        self.assert_executes('''
variant[T] Value =>
  extend read -> T
  define keep -> T => $self read
  First =>
    $value: T
    define read => $self.value
  end
  Second =>
    $value: T
    define read => $self.value
  end
end
First(8) keep
Second("a") keep
''', [8, "a"])

    def test_variant_default_override_must_match_signature(self):
        for implementation, diagnostic in (
            ('define score(value: String) -> Int => 2', 'parameter type'),
            ('define score(value: Int) -> String => "bad"', 'return signature'),
            ('define score -> Int => 2', 'explicit parameter'),
        ):
            with self.subTest(implementation=implementation):
                self.assert_rejected('''
variant Choice =>
  define score(value: Int) -> Int => $value
  First => $id: Int end
  Second =>
    $id: Int
''' + implementation + '''
  end
end
''', diagnostic)

    def test_heterogeneous_variant_collection_dispatch(self):
        self.assert_executes(self.SHAPES + '''
$shapes: Shape+ = [Square(2), Rectangle(3, 4)]
area $shapes
''', [[4, 12]])

    def test_nested_variant_collection_dispatch(self):
        self.assert_executes(self.SHAPES + '''
$shapes: Shape++ = [[Square(2), Rectangle(3, 4)], [Rectangle(4, 5), Square(3)]]
area $shapes
''', [[[4, 12], [20, 9]]])

    def test_recursive_variant_method_dispatches_collection_fields(self):
        self.assert_executes('''
variant Entry =>
  extend storedBytes -> Int
  File =>
    $fileSize: Int
    define storedBytes => $self.fileSize
  end
  Shortcut =>
    $target: String
    define storedBytes => 0
  end
  Folder =>
    $entries: Entry+
    define storedBytes => sum storedBytes $self.entries
  end
end
Folder([File(1200), File(4500), Folder([File(8000), File(6000)]), Shortcut("essay")]) storedBytes
Folder([] as[Entry+]) storedBytes
Folder([Folder([Shortcut("essay")]), File(3)]) storedBytes
''', [19700, 0, 3])

    def test_variant_collection_dispatch_broadcasts_other_arguments(self):
        self.assert_executes('''
variant Entry =>
  extend add(:Int) -> Int
  First =>
    $value: Int
    define add(n: Int) => $self.value + $n
  end
  Second =>
    $value: Int
    define add(n: Int) => $self.value + $n + 1
  end
end
$items: Entry+ = [First(3), Second(4)]
$items | add 10
''', [[13, 15]])

    def test_vectorized_multimethod_retains_scalar_fallback(self):
        self.assert_executes(self.SHAPES + '''
define describe(shape: Shape) -> String => "fallback" end
multi define describe(shape: Shape.Square) -> String => "square" end
$shapes: Shape+ = [Square(2), Rectangle(3, 4), Square(5)]
describe $shapes
''', [['square', 'fallback', 'square']])

    def test_generic_match_preserves_nested_field_arguments(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int+]) -> Int+ =>
  $value match =>
    as :Some(items) => $items
    as :Empty => [] as[Int+]
  end
end
Some([1, 2, 3]) unwrap
''', [[1, 2, 3]])

    def test_generic_object_destructuring_specializes_fields(self):
        self.assert_executes('''
object[T] Box => $value: T end
define unwrap(value: Box[Int]) -> Int =>
  $value match =>
    as :Box(item) => $item
    as _ => 0
  end
end
Box(19) unwrap
''', [19])

    def test_generic_match_extraction_specializes_named_capture(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    extract as :Some(item) => $item * 2
    as :Empty => 0
  end
end
Some(7) unwrap
''', [14])

    def test_generic_match_specializes_whole_member_binding(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int]) -> Int =>
  $value match =>
    as item: Some => $item.value
    as :Empty => 0
  end
end
Some(21) unwrap
''', [21])

    def test_generic_match_retains_arguments_in_subject_union(self):
        self.assert_executes(MAYBE + '''
define unwrap(value: Maybe[Int] | Maybe[String]) -> Int | String =>
  $value match =>
    as :Some(item) => $item
    as _ => "empty"
  end
end
Some(3) unwrap
Some("three") unwrap
''', [3, 'three'])

    def test_member_requirement_diagnostics(self):
        for body, message in (
            ('', "must implement element 'convert'"),
            ('define convert(x: Int) =>\n $x\n end\n'
             'define convert(x: Int) =>\n $x\n end',
             'exactly once'),
            ('define convert => 1', 'explicit parameter'),
            ('define convert(x: String) => 1', 'parameter type'),
            ('define convert(x: Int) -> String => "wrong"', 'return'),
        ):
            with self.subTest(body=body):
                self.assert_rejected(
                    'variant Choice =>\n  extend convert(:Int) -> Int\n'
                    f'Item =>\n{body}\nend\nend', message,
                )

    def test_match_requires_every_member(self):
        self.assert_rejected(MAYBE + '''
define unpack(value: Maybe[Int]) -> Int =>
  $value match => as :Some(value) => $value end
end
''', 'missing cases: Maybe.Empty')

    def test_guard_and_literal_pattern_do_not_prove_exhaustiveness(self):
        for pattern in ('as :Some(1)', 'as :Some(_) if 0 1 =='):
            with self.subTest(pattern=pattern):
                self.assert_rejected(MAYBE + f'''
Some(2) match =>
  {pattern} => 1
  as :Empty => 0
end
''', 'missing cases: Maybe.Some')

    def test_destructure_arity_is_checked(self):
        self.assert_rejected(MAYBE + '''
Some(1) match =>
  as :Some(_, _) => 1
  as :Empty => 0
end
''', 'destructures 2 fields')

    def test_match_payload_types_are_checked(self):
        self.assert_rejected(MAYBE + '''
define invalid(value: Maybe[String]) -> Int =>
  $value match =>
    as :Some(item) => $item
    as :Empty => 0
  end
end
''', 'return')

    def test_wildcard_handles_unmatched_payload(self):
        self.assert_executes(MAYBE + '''
Some(2) match =>
  as :Some(1) => "one"
  as _ => "other"
end
''', ['other'])

    def test_nested_generic_payload_survives_matching(self):
        self.assert_executes(MAYBE + '''
define unpack(value: Maybe[Maybe[Int]]) -> Int =>
  $value match =>
    as :Some(inner) =>
      $inner match =>
        as :Some(item) => $item
        as :Empty => -1
      end
    as :Empty => -2
  end
end
Some(Some(17)) unpack
''', [17])

    def test_trait_dispatch_inside_variant_payload_match(self):
        self.assert_executes('''
trait Sized => extend size -> Int end
object Item => $value: Int end
object Item as Sized => define size => $self.value end
''' + MAYBE + '''
define measure(value: Maybe[Sized]) -> Int =>
  $value match =>
    as :Some(item) => $item size
    as :Empty => 0
  end
end
Some(Item(23) as[Sized]) measure
''', [23])


if __name__ == '__main__':
    unittest.main()
