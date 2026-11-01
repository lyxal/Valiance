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
        for expression, expected in (('Some(1)', 'Maybe[Int]'),
                                     ('Some("text")', 'Maybe[String]'),
                                     ('Some([1, 2])', 'Maybe[Int+]')):
            with self.subTest(expression=expression):
                typed = self.analyse(MAYBE + expression)
                self.assertEqual(T.show(typed[-1].typ), expected)

    def test_qualified_and_short_constructors_agree(self):
        typed = self.analyse(MAYBE + 'Some(4)\nMaybe.Some(9)')
        self.assertEqual(T.show(typed[-1].typ), 'Maybe[Int]')
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
Some(1) match => as :Some(value) => $value end
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
