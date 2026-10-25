"""Boundary tests for value-level expression analysis."""

from __future__ import annotations

import copy
import unittest

from valiance.analysis import Analyser
from valiance.analysis.expressions import ExpressionAnalyser
from valiance.asts import TypedLiteralNode, TypedNode
from valiance.asts.nodes import FieldAccessNode, FieldSetNode, ListLiteralNode, RecordLiteralNode
from valiance.parsing import parse


class ExpressionBoundaryTests(unittest.TestCase):
    """Verify value expressions are owned outside the analyser façade."""

    def test_intersection_reflection_checks_all_reachable_callbacks_and_protected_layouts(self):
        declaration = '''
trait View => end
object A => public $number: Int end
object B => public $text: String end
object A as View => end
object B as View => end
$owner: (A | B) & View = B("hello")
'''
        for source in (
            declaration + '$owner fields foreach(field) => $owner[$field] | length end',
            declaration.replace('object A =>', '@nodup("protected")\nobject A =>')
            + '$owner fields',
            'trait Left => end\ntrait Right => end\n'
            'define inspect(value: Left & Right) => unsafe => $value fields end end\n'
            'object Owner => end\nobject Owner as Left => end\nobject Owner as Right => end\n'
            '$erased: Left & Right = Owner\ninspect($erased)',
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics)

    def test_trait_reflection_specialization_enforces_conformance_and_lexical_permissions(self):
        declarations = '''
trait View => end
@nodup("protected")
object Resource => public $number: Int end
object Resource as View => end
object Other => end
'''
        for definition, call in (
            ('define inspect(value: View) => $value fields | length end',
             'unsafe => inspect(Resource(7)) end'),
            ('define inspect(value: View) => unsafe => $value fields | length end end',
             'inspect(Other)'),
            ('define inspect(value: View) => unsafe => $value fields | length end end',
             '$erased: View = Resource(7)\ninspect($erased)'),
        ):
            with self.subTest(call=call):
                analyser = Analyser()
                analyser.analyse(parse(declarations + definition + '\n' + call))
                self.assertTrue(analyser.diagnostics)

    def test_trait_reflection_specialization_checks_every_union_callback(self):
        analyser = Analyser()
        analyser.analyse(parse('''
trait View => end
object A => public $number: Int end
object B => public $text: String end
object A as View => end
object B as View => end
define inspect(value: View) =>
  $value fields foreach(field) => $value[$field] | length end
end
define pick(flag: Int) -> A | B => if $flag == 1 => A(7) else => B("hello") end end
inspect(pick(0))
'''))
        self.assertTrue(analyser.diagnostics)

    def test_erased_structural_storage_is_diagnosed_instead_of_empty_metadata(self):
        for source in (
            'unsafe => record{number => 7} fields end',
            'define inspect(value: trait => end) => unsafe => $value fields end end\n'
            '$erased: trait => end = record{number => 7}\ninspect($erased)',
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics)

    def test_variant_reflection_checks_callbacks_for_inactive_members(self):
        analyser = Analyser()
        analyser.analyse(parse('''
variant Value =>
  Numeric => public $number: Int end
  Textual => public $text: String end
end
$owner = Textual("hello")
$owner fields foreach(field) => $owner[$field] | length end
'''))
        self.assertTrue(any('length' in message for message in analyser.diagnostics))

    def test_variant_reflection_requires_unsafe_for_protected_member_storage(self):
        analyser = Analyser()
        analyser.analyse(parse('''
@nodup("protected")
object Resource => end
variant Value =>
  Protected => public $resource: Resource end
  Empty => end
end
$owner = Empty
$owner fields
'''))
        self.assertTrue(any('lexical unsafe' in message for message in analyser.diagnostics))

    def test_union_dispatch_retains_checked_generic_reflection_bodies(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('''
object A => public $number: Int end
object B => public $label: String end
define pick(flag: Int) -> A | B => if $flag == 1 => A(7) else => B("hello") end end
define[T] nameOf(field: Field[T]) => $field.name end
$owner = pick(0)
$metadata = $owner fields
nameOf($metadata["label"])
'''))
        from valiance.asts import TypedElementNode, TypedFunctionDispatchNode

        self.assertEqual(analyser.diagnostics, [])
        call = typed[-1]
        self.assertIsInstance(call, TypedElementNode)
        self.assertIsInstance(call.specialization, TypedFunctionDispatchNode)
        self.assertEqual(len(call.specialization.specializations), 2)
        self.assertTrue(all(body is not None for _, body, _ in call.specialization.specializations))

    def test_union_specialization_rejects_an_invalid_body_for_any_owner(self):
        declaration = '''
object A => public $number: Int end
object B => public $label: String end
define pick(flag: Int) -> A | B => if $flag == 1 => A(7) else => B("hello") end end
$metadata = pick(0) fields
'''
        for definition in (
            'define[T] inspect(field: Field[T]) => fields[T] foreach(f) => 1 + $f.name end end',
            'define[T] inspect(field: Field[T]) => fields[T] foreach(f) => "7" to[$f.type] | length end end',
        ):
            with self.subTest(definition=definition):
                analyser = Analyser()
                analyser.analyse(parse(declaration + definition + '\ninspect($metadata["label"])'))
                self.assertTrue(analyser.diagnostics)

    def test_union_specialization_does_not_inherit_callers_unsafe_authority(self):
        analyser = Analyser()
        analyser.analyse(parse('''
@nodup("protected")
object A => public $number: Int end
object B => public $label: String end
define[T] inspect(field: Field[T]) => $field.name end
unsafe =>
  $field: Field[A] | Field[B] = fields[B]["label"]
  inspect($field)
end
'''))
        self.assertTrue(analyser.diagnostics)

    def test_runtime_union_reflection_rejects_invalid_callback_in_any_layout(self):
        analyser = Analyser()
        analyser.analyse(parse('''
object A => public $number: Int end
object B => public $label: String end
define pick(flag: Int) -> A | B =>
  if $flag == 1 => A(7) else => B("hello") end
end
$owner = pick(0)
$owner fields foreach(field) => $owner[$field] | length end
'''))
        self.assertTrue(analyser.diagnostics)
        self.assertTrue(any('length' in message for message in analyser.diagnostics))

    def test_runtime_union_metadata_keeps_transitive_permission_gate_after_escape(self):
        declaration = '''
@nodup("protected")
object A => public $number: Int end
object B => public $label: String end
define pick(flag: Int) -> A | B =>
  if $flag == 1 => A(7) else => B("hello") end
end
unsafe =>
  $owner = pick(0)
  $metadata = $owner fields
  $field = $metadata["label"]
end
'''
        for operation in ('$metadata | length', 'field?($metadata, "label")', '$field.name',
                          '$metadata foreach(field) => $field.name end'):
            analyser = Analyser()
            analyser.analyse(parse(declaration + operation))
            self.assertTrue(analyser.diagnostics, operation)
            self.assertTrue(any('lexical unsafe' in message for message in analyser.diagnostics))

    def test_managed_construction_requires_lexical_unsafe_at_every_operation(self):
        declaration = 'object Owner => public $value: Int end\n'
        for source in (
            'uninit[Owner]',
            'define inspect(builder: Uninit[Owner]) => finished? $builder end',
            'define complete(builder: Uninit[Owner]) => finish $builder end',
            'define complete(builder: Uninit[Owner]) => finish? $builder end',
            'unsafe => $builder = uninit[Owner] end\n$builder[fields[Owner]["value"]] = 1',
        ):
            analyser = Analyser()
            analyser.analyse(parse(declaration + source))
            self.assertTrue(analyser.diagnostics)
            self.assertTrue(any('lexical unsafe' in item for item in analyser.diagnostics))

    def test_managed_construction_rejects_non_storage_types_and_invalid_access(self):
        for source in (
            'uninit[Int]', 'uninit[String]', 'uninit[Fields[Owner]]',
            'uninit[Owner | Int]', 'uninit[Owner*]',
            '$builder = uninit[Owner]\n$builder[fields[Owner]["value"]]',
            '$builder = uninit[Owner]\n$builder.value',
            '$builder = uninit[Owner]\n$builder as![Owner]',
            '$builder = uninit[Owner]\n$builder[fields[Other]["value"]] = 1',
            '$builder = uninit[Owner]\n$builder[fields[Owner]["value"]] = "wrong"',
        ):
            analyser = Analyser()
            analyser.analyse(parse('object Owner => public $value: Int end\n'
                                   'object Other => public $value: Int end\nunsafe =>\n' + source + '\nend'))
            self.assertTrue(analyser.diagnostics, source)

    def test_fallible_access_rejects_provable_owner_and_replacement_mismatches(self):
        declarations = '''
object Owner => public $value: Int end
object Other => public $value: Int end
$owner = Owner(7)
'''
        for call in ('read?($owner, fields[Other]["value"])',
                     'write?($owner, fields[Other]["value"], 8)',
                     'write?($owner, fields[Owner]["value"], "wrong")'):
            analyser = Analyser()
            analyser.analyse(parse(declarations + call))
            with self.subTest(call=call):
                self.assertTrue(analyser.diagnostics)

    def test_fallible_read_keeps_the_dynamic_field_union(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('''
object Owner =>
  public $value: Int
  public $text: String
end
$owner = Owner(7, "text")
read?($owner, fields[Owner]["value"])
'''))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(str(typed[-1].typ), 'Result[Int | String, ReflectionReadError]')

    def test_fallible_access_requires_unsafe_at_the_operation(self):
        for call in ('read?($owner, $field)', 'write?($owner, $field, 8)'):
            analyser = Analyser()
            analyser.analyse(parse('''
@nodup("resource")
object Owner => public $value: Int end
$owner = Owner(7)
unsafe => $field = fields[Owner]["value"] end
''' + call))
            with self.subTest(call=call):
                self.assertTrue(any('requires lexical unsafe' in str(item) for item in analyser.diagnostics))

    def test_fallible_read_preserves_receiver_cleanup_effects(self):
        analyser = Analyser()
        typed = analyser.analyse(parse('''
object Owner =>
  public $value: Int
  define ~Owner => RuntimeFault("cleanup") panic end
end
define inspect(seed: Int) => read?(Owner(7), fields[Owner]["value"]) end
'''))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIn('Panic[RuntimeFault]', str(typed[-1].typ))

    def test_reflective_write_rejects_impossible_types_and_other_owners(self):
        declarations = ('object Owner => public $value: Int end\n'
                        'object Other => public $value: Int end\n'
                        '$owner = Owner(7)\n')
        for update, message in (
            ('$owner[fields[Owner]["value"]] = "wrong"', "cannot assign String"),
            ('$owner[fields[Other]["value"]] = 8', "owner does not match"),
        ):
            with self.subTest(update=update):
                analyser = Analyser()
                analyser.analyse(parse(declarations + update))
                self.assertTrue(any(message in str(item) for item in analyser.diagnostics))

    def test_protected_owner_read_requires_unsafe_at_the_read_site(self):
        """An unsafe descriptor factory does not authorize later value reads."""
        analyser = Analyser()
        analyser.analyse(parse(
            '@nodup("resource")\nobject Resource => public $value: Int end\n'
            '$resource = Resource(7)\n'
            'unsafe =>\n$field = fields[Resource]["value"]\nend\n'
            '$resource[$field]'
        ))
        self.assertTrue(any("requires lexical unsafe" in str(d) for d in analyser.diagnostics))

    def test_dynamic_reflective_read_keeps_all_field_types(self):
        """Literal lookup names do not narrow a dynamic descriptor."""
        analyser = Analyser()
        typed = analyser.analyse(parse(
            'object Pair =>\npublic $first: Int\npublic $second: String\nend\n'
            '$pair = Pair(7, "text")\n$pair[fields[Pair]["first"]]'
        ))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(str(typed[-1].typ), "Int | String")

    def test_reflective_read_rejects_other_owner_and_generic_arguments(self):
        """Same-layout owners and different generic instantiations stay distinct."""
        for source in (
            'object A => public $value: Int end\n'
            'object B => public $value: Int end\n$a = A(7)\n$a[fields[B]["value"]]',
            'object[T] Box => public $value: T end\n'
            '$box = Box(7)\n$box[fields[Box[String]]["value"]]',
        ):
            with self.subTest(source=source):
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(any("owner does not match" in str(d) for d in analyser.diagnostics))

    def test_analyser_constructs_an_expression_service(self) -> None:
        """The façade exposes the dedicated expression subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.expressions, ExpressionAnalyser)
        self.assertTrue(analyser.expressions.provides("_field_type"))
        self.assertTrue(analyser.expressions.provides("_can_access_attribute"))

    def test_collection_literal_handler_emits_typed_node(self) -> None:
        """Collection literal semantics remain committed in the typed AST."""
        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2, 3]"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedLiteralNode)
        self.assertIsInstance(typed[-1].node, ListLiteralNode)

    def test_record_literal_handler_emits_typed_node(self) -> None:
        """Record literal field typing remains available downstream."""
        analyser = Analyser()
        typed = analyser.analyse(parse("record{name => \"Ada\", score => 10}"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedLiteralNode)
        self.assertIsInstance(typed[-1].node, RecordLiteralNode)

    def test_field_handlers_use_expression_service(self) -> None:
        """Field reads and writes retain explicit typed operations."""
        source = """object Counter =>
  public $value: Number
end
1
Counter
dup
$.value
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(any(isinstance(node, TypedNode) and isinstance(node.node, FieldAccessNode) for node in typed))

    def test_expression_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve expression-service delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("[1, 2]"))
        self.assertEqual(analyser.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
