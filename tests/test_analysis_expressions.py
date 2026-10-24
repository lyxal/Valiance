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
