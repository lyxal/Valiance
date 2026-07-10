"""Regression tests for optional-safe member access and assignment."""

from __future__ import annotations

import unittest
from decimal import Decimal

from valiance.analysis import Analyser
from valiance.asts import (
    FieldAccessNode,
    FieldSetNode,
    GetVariableNode,
    SetVariableNode,
)
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime_values import ObjectValue
from valiance.symbols import Symbol


PERSON = """
object Person =>
  public $name: String
  public $age: Number
end
"""

NESTED = """
object Leaf =>
  public $value: String
end
object Branch =>
  public $leaf: Leaf?
end
object Root =>
  public $branch: Branch
end
"""


def analyse(source: str):
    """Parse and analyse source, returning both analyser and typed nodes."""
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute(source: str, *, serialize: bool = False):
    """Execute a source program, optionally through bytecode serialization."""
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=False)
    if serialize:
        program = loads(dumps(program))
    return run(program)


class OptionalMemberParserTests(unittest.TestCase):
    def test_parses_named_and_stack_optional_safe_access(self):
        named = parse("$value->name")
        stack = parse("$->name")

        self.assertEqual(named[0], GetVariableNode(Symbol("value")))
        self.assertEqual(
            named[1],
            FieldAccessNode(Symbol("name"), optional_safe=True),
        )
        self.assertEqual(
            stack,
            [FieldAccessNode(Symbol("name"), optional_safe=True)],
        )

    def test_parses_optional_safe_assignment(self):
        nodes = parse('$value->name = "Ada"')

        self.assertTrue(
            any(
                isinstance(node, FieldSetNode) and node.optional_safe
                for node in nodes
            )
        )
        self.assertEqual(nodes[-1], SetVariableNode(Symbol("value")))

    def test_parses_multiple_optional_safe_accesses(self):
        nodes = parse("$value->a->b->c")

        self.assertEqual(nodes[0], GetVariableNode(Symbol("value")))
        accesses = [node for node in nodes if isinstance(node, FieldAccessNode)]
        self.assertEqual(
            [(node.name, node.optional_safe) for node in accesses],
            [
                (Symbol("a"), True),
                (Symbol("b"), True),
                (Symbol("c"), True),
            ],
        )

    def test_parses_mixed_member_chain(self):
        named = parse("$value.a->b.c->d")
        stack = parse("$->a.b->c")

        named_accesses = [
            node for node in named if isinstance(node, FieldAccessNode)
        ]
        stack_accesses = [
            node for node in stack if isinstance(node, FieldAccessNode)
        ]
        self.assertEqual(
            [(node.name, node.optional_safe) for node in named_accesses],
            [
                (Symbol("a"), False),
                (Symbol("b"), True),
                (Symbol("c"), False),
                (Symbol("d"), True),
            ],
        )
        self.assertEqual(
            [(node.name, node.optional_safe) for node in stack_accesses],
            [
                (Symbol("a"), True),
                (Symbol("b"), False),
                (Symbol("c"), True),
            ],
        )


class OptionalMemberRuntimeTests(unittest.TestCase):
    def test_reads_member_from_present_optional(self):
        result = execute(
            PERSON
            + '''
$person: Person? = Some(Person("Ada", 36))
$person->name ?!
'''
        )

        self.assertEqual(result, ["Ada"])

    def test_stack_form_reads_member(self):
        result = execute(
            PERSON
            + '''
$person: Person? = Some(Person("Ada", 36))
$person $->age ?!
'''
        )

        self.assertEqual(result, [Decimal("36")])

    def test_absent_optional_propagates_none(self):
        [result] = execute(
            PERSON
            + '''
$person: Person? = None
$person->name
'''
        )

        self.assertIsInstance(result, ObjectValue)
        self.assertEqual(result.type_name, "None")

    def test_access_vectorises_over_optional_values(self):
        [result] = execute(
            PERSON
            + '''
$people: Person?+ = [Some(Person("Ada", 36)), None]
$people $->name
'''
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].type_name, "Some")
        self.assertEqual(result[0].fields, {"value": "Ada"})
        self.assertEqual(result[1].type_name, "None")

    def test_optional_member_is_not_double_wrapped(self):
        [result] = execute(
            '''
object Profile =>
  public $nickname: String?
end
$profile: Profile? = Some(Profile(Some("Ada")))
$profile->nickname
'''
        )

        self.assertIsInstance(result, ObjectValue)
        self.assertEqual(result.type_name, "Some")
        self.assertEqual(result.fields, {"value": "Ada"})

    def test_assignment_updates_present_optional(self):
        result = execute(
            PERSON
            + '''
$person: Person? = Some(Person("Ada", 36))
$person->age = 37
$person->age ?!
'''
        )

        self.assertEqual(result, [Decimal("37")])

    def test_assignment_through_none_is_cancelled(self):
        [result] = execute(
            PERSON
            + '''
$person: Person? = None
$person->age = 37
$person
'''
        )

        self.assertIsInstance(result, ObjectValue)
        self.assertEqual(result.type_name, "None")

    def test_safe_access_survives_bytecode_round_trip(self):
        result = execute(
            PERSON
            + '''
$person: Person? = Some(Person("Ada", 36))
$person->name ?!
''',
            serialize=True,
        )

        self.assertEqual(result, ["Ada"])

    def test_multiple_safe_accesses_reach_deep_member(self):
        result = execute(
            NESTED
            + '''
$root: Root? = Some(Root(Branch(Some(Leaf("deep")))))
$root->branch->leaf->value ?!
'''
        )

        self.assertEqual(result, ["deep"])

    def test_multiple_safe_accesses_propagate_intermediate_none(self):
        [result] = execute(
            NESTED
            + '''
$root: Root? = Some(Root(Branch(None)))
$root->branch->leaf->value
'''
        )

        self.assertIsInstance(result, ObjectValue)
        self.assertEqual(result.type_name, "None")

    def test_mixed_plain_then_safe_access_chain(self):
        result = execute(
            NESTED
            + '''
$root = Root(Branch(Some(Leaf("mixed"))))
$root.branch.leaf->value ?!
'''
        )

        self.assertEqual(result, ["mixed"])

    def test_stack_form_supports_multiple_safe_accesses(self):
        result = execute(
            NESTED
            + '''
$root: Root? = Some(Root(Branch(Some(Leaf("stack")))))
$root $->branch->leaf->value ?!
'''
        )

        self.assertEqual(result, ["stack"])

    def test_multiple_safe_accesses_survive_bytecode_round_trip(self):
        result = execute(
            NESTED
            + '''
$root: Root? = Some(Root(Branch(Some(Leaf("serialized")))))
$root->branch->leaf->value ?!
''',
            serialize=True,
        )

        self.assertEqual(result, ["serialized"])


class OptionalMemberAnalysisTests(unittest.TestCase):
    def test_plain_dot_access_remains_unsafe_for_optional(self):
        analyser, _ = analyse(
            PERSON
            + '''
$person: Person? = Some(Person("Ada", 36))
$person.name
'''
        )

        self.assertTrue(analyser.diagnostics)
        self.assertIn("has no known field 'name'", str(analyser.diagnostics[-1]))

    def test_safe_access_requires_an_optional_receiver(self):
        analyser, _ = analyse(
            PERSON
            + '''
$person = Person("Ada", 36)
$person->name
'''
        )

        self.assertTrue(analyser.diagnostics)
        self.assertIn("optional type Person", str(analyser.diagnostics[-1]))

    def test_plain_access_after_safe_access_remains_unsafe(self):
        analyser, _ = analyse(
            NESTED
            + '''
$root: Root? = Some(Root(Branch(Some(Leaf("Ada")))))
$root->branch.leaf
'''
        )

        self.assertTrue(analyser.diagnostics)
        self.assertIn(
            "type None | Some[Branch] has no known field 'leaf'",
            str(analyser.diagnostics[-1]),
        )


if __name__ == "__main__":
    unittest.main()
