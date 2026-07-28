import unittest

from valiance.analysis import Analyser
from valiance.asts import DefineNode, FunctionNode, OverloadSignature
from valiance.parsing import ParseError, parse
from valiance.runtime import compile_program, run
from valiance.vtypes import OverloadSetType
from valiance.runtime.runtime_values import RuntimeNumber


class OverloadKeywordTests(unittest.TestCase):
    def test_parser_attaches_signatures_to_following_define(self):
        nodes = parse("""
overload(Number+ -> Number)
#? the signatures remain attached across comments and whitespace
overload(String+ -> String)
define sharedSum(xs) => reduce: +
""")
        self.assertEqual(len(nodes), 1)
        self.assertIsInstance(nodes[0], DefineNode)
        self.assertEqual(len(nodes[0].function.overloads), 2)
        self.assertTrue(
            all(
                isinstance(item, OverloadSignature)
                for item in nodes[0].function.overloads
            )
        )

    def test_parser_attaches_signature_to_following_fn(self):
        nodes = parse("overload(Number -> Number)\nfn (value) => double")
        self.assertEqual(len(nodes), 1)
        self.assertIsInstance(nodes[0], FunctionNode)
        self.assertEqual(len(nodes[0].overloads), 1)

    def test_dangling_overload_is_rejected(self):
        with self.assertRaises(ParseError):
            parse("overload(Number -> Number)\n1")

    def test_untyped_define_uses_only_declared_overloads(self):
        node = parse("""
overload(Number+ -> Number)
overload(String+ -> String)
define sharedSum(xs) => reduce: +
""")[0]
        analyser = Analyser()
        typed = analyser.analyse([node])
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[0].typ, OverloadSetType)
        self.assertEqual(len(analyser.env.overloads_for(node.name)), 2)

    def test_typed_define_keeps_own_signature_and_adds_overloads(self):
        node = parse("""
overload(String+ -> String)
define sum(xs: Number+) -> Number => reduce: +
""")[0]
        analyser = Analyser()
        analyser.analyse([node])
        self.assertEqual(analyser.diagnostics, [])
        overloads = analyser.env.overloads_for(node.name)
        self.assertEqual(len(overloads), 2)

    def test_parameter_count_must_match(self):
        node = parse("""
overload(Number, Number -> Number)
define identity(value) => top
""")[0]
        analyser = Analyser()
        analyser.analyse([node])
        self.assertTrue(analyser.diagnostics)
        self.assertIn("2 parameter type(s)", str(analyser.diagnostics[0]))

    def test_runtime_compiles_each_shared_body_overload(self):
        source = """
overload(Number+ -> Number)
overload(String+ -> String)
define sharedSum(xs) => reduce: +
sharedSum([1, 2, 3])
sharedSum(["a", "b", "c"])
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            run(compile_program(typed, optimize=False)), [RuntimeNumber("6"), "abc"]
        )


if __name__ == "__main__":
    unittest.main()
