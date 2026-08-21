import unittest

from valiance.asts import ConcurrentNode, ElementNode, pretty_ast
from valiance.parsing import ParseError, parse
from valiance.vtypes import Int, String


class ConcurrentParserTests(unittest.TestCase):
    def test_explicit_parameters_and_multiple_returns(self):
        nodes = parse(
            """10 20
concurrent (a: Int, b: Int) -> Int, String =>
  $a
  $b
end"""
        )
        block = nodes[-1]
        self.assertIsInstance(block, ConcurrentNode)
        self.assertEqual(tuple(param.name.text for param in block.params or ()), ("a", "b"))
        self.assertEqual(block.returns, (Int, String))
        self.assertEqual(len(block.body), 2)

    def test_inferred_input_concurrent(self):
        block = parse("concurrent => 1 end")[0]
        self.assertIsInstance(block, ConcurrentNode)
        self.assertIsNone(block.params)
        self.assertIsNone(block.returns)

    def test_spawn_and_wait_remain_ordinary_elements(self):
        block = parse("concurrent => spawn wait end")[0]
        names = [node.name.text for node in block.body if isinstance(node, ElementNode)]
        self.assertCountEqual(names, ["spawn", "wait"])

    def test_missing_arrow_is_rejected(self):
        with self.assertRaises(ParseError):
            parse("concurrent (x: Int) x end")

    def test_pretty_print_includes_scope_contract(self):
        rendered = pretty_ast(parse("concurrent () -> Int => 1 end"))
        self.assertIn("ConcurrentNode", rendered)
        self.assertIn("Int", rendered)


if __name__ == "__main__":
    unittest.main()
