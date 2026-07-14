"""Tests for statically counted stack popping."""
from __future__ import annotations
import unittest
from valiance.analysis import Analyser
from valiance.asts import PopNNode
from valiance.parsing import ParseError, parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.runtime.optimizer import PopNOptimizationPass
from valiance.runtime_values import RuntimeNumber
from valiance.symbols import Symbol

class PopNTests(unittest.TestCase):
    def analyse(self, source: str):
        """Parse and analyse one source program."""
        analyser = Analyser(); typed = analyser.analyse(parse(source)); return analyser, typed

    def test_parser_accepts_literal_and_static_variable_operands(self):
        self.assertEqual(parse("pop_n(3)"), [PopNNode(3)])
        self.assertEqual(parse("pop_n($count)"), [PopNNode(Symbol("count"))])

    def test_parser_rejects_non_integral_and_negative_counts(self):
        with self.assertRaises(ParseError): parse("pop_n(1.5)")
        with self.assertRaises(ParseError): parse("pop_n(-1)")

    def test_literal_count_executes_and_round_trips(self):
        analyser, typed = self.analyse("1 2 3 4 pop_n(3)")
        self.assertFalse(analyser.diagnostics)
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), [RuntimeNumber(1)])
        self.assertEqual(run(loads(dumps(program))), [RuntimeNumber(1)])

    def test_where_count_can_depend_on_function_arity(self):
        source = '''
        define cleanup(f: Function, g: Function)
        where ($n = max($f.arity, $g.arity)) => pop_n($n) end
        1 2 fn (:Number) => double end fn (:Number, :Number) => + end cleanup
        '''
        analyser, typed = self.analyse(source)
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), [])
        self.assertEqual(run(loads(dumps(program))), [])

    def test_runtime_variable_is_rejected(self):
        analyser, _ = self.analyse("1 2 pop_n($count)")
        self.assertTrue(analyser.diagnostics)
        self.assertIn("not compile-time known", str(analyser.diagnostics[0]))

    def test_dedicated_optimization_combines_adjacent_pops(self):
        program = Program(FunctionCode((Instruction(OpCode.POP), Instruction(OpCode.POP_N, 2), Instruction(OpCode.POP), Instruction(OpCode.RETURN))))
        optimized = PopNOptimizationPass().optimize(program)
        self.assertEqual(optimized.main.instructions, (Instruction(OpCode.POP_N, 4), Instruction(OpCode.RETURN)))

if __name__ == "__main__": unittest.main()
