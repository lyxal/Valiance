import unittest

from valiance.analysis import Analyser
from valiance.asts import TypedConcurrentNode
from valiance.parsing import parse
from valiance.vtypes import Integer


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


class ConcurrentAnalysisTests(unittest.TestCase):
    def test_explicit_parameters_consume_inputs_and_restore_outputs(self):
        analyser, typed = analyse(
            """10
concurrent (value: Integer) -> Integer =>
  $value
end"""
        )
        self.assertEqual(analyser.diagnostics, [])
        block = typed[-1]
        self.assertIsInstance(block, TypedConcurrentNode)
        self.assertEqual(block.input_stack, (Integer,))
        self.assertEqual(block.output_stack, (Integer,))

    def test_inferred_inputs_use_closed_body_contract(self):
        analyser, typed = analyse("10 concurrent => 1 + end")
        self.assertEqual(analyser.diagnostics, [])
        block = typed[-1]
        self.assertIsInstance(block, TypedConcurrentNode)
        self.assertEqual(block.input_stack, (Integer,))
        self.assertEqual(block.output_stack, (Integer,))

    def test_direct_outer_capture_is_rejected_with_help(self):
        analyser, _ = analyse(
            """$value = 10
concurrent =>
  $value
end"""
        )
        self.assertTrue(any("cannot capture outer variable 'value'" in item for item in analyser.diagnostics))

    def test_closure_is_one_composite_stack_input(self):
        analyser, typed = analyse(
            """$value = 10
fn -> Integer => $value end
concurrent (operation: Function[-> Integer]) -> Function[-> Integer] =>
  $operation
end"""
        )
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-1], TypedConcurrentNode)

    def test_wrong_explicit_input_type_is_rejected(self):
        analyser, _ = analyse('"text" concurrent (value: Integer) => $value end')
        self.assertTrue(any("input type mismatch" in item for item in analyser.diagnostics))


if __name__ == "__main__":
    unittest.main()
