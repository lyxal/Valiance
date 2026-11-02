"""Round 3 regressions: indexing, lazy host faults, deep ranks, exact arithmetic."""

from __future__ import annotations

import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.bigdecimal import BigDecimal
from valiance.runtime.runtime_values import format_runtime_value
from valiance.runtime.vm import RuntimeError as ValianceRuntimeError, VirtualMachine


class Round3RuntimeRegressions(unittest.TestCase):
    MODES = ((False, False), (True, False), (False, True), (True, True))

    def execute(self, source, optimized=False, serialized=False):
        analyser = Analyser()
        ast = analyser.analyse(parse(source))
        self.assertEqual([], analyser.diagnostics, f"source: {source}")
        program = compile_program(ast, optimize=optimized)
        if serialized:
            program = loads(dumps(program))
        return VirtualMachine().run(program)

    def test_original_round3_reproducers(self):
        # Each entry specifies whether the error is raised during the VM run,
        # when a returned lazy list is later traversed, or not at all.
        examples = [
            ("update(range(1,3),[0,9],9) | to[String]", "during", "IndexFault"),
            ("update(range(2,1),[0],9) | to[String]", "during", "IndexFault"),
            ("range(1,3) $[-1]", "success", "3"),
            ("update(range(1,3),-1,9) | to[String]", "success", "[1, 2, 9]"),
            ("1 ^+1000 | length", "success", "1"),
            ("1e9999999 1 %", "success", "0"),
            ("1 1e9999999 %", "success", "1"),
            ("1 0 %", "during", "DivisionByZeroFault"),
            ("0 1e100001 **", "success", "0"),
            ("[1,2,3]\n$[0,9] = 9", "during", "IndexFault"),
            ("update(range(1,3),9,9)", "deferred", "IndexFault"),
            ("range(1,3)\n$[0,9] = 9", "deferred", "IndexFault"),
            ("1e9999999 0 %", "during", "DivisionByZeroFault"),
            ("1 ^+1000 | first", "success", "["),
        ]
        for source, phase, expected in examples:
            for optimize, serialize in self.MODES:
                with self.subTest(source=source, phase=phase, optimize=optimize,
                                  serialize=serialize):
                    if phase == "during":
                        with self.assertRaisesRegex(ValianceRuntimeError, expected):
                            self.execute(source, optimize, serialize)
                    else:
                        result = self.execute(source, optimize, serialize)
                        self.assertEqual(1, len(result))
                        if phase == "deferred":
                            with self.assertRaisesRegex(ValianceRuntimeError, expected):
                                format_runtime_value(result[0])
                        else:
                            rendered = format_runtime_value(result[0])
                            if source.endswith("| first"):
                                self.assertTrue(rendered.startswith(expected))
                                self.assertGreater(len(rendered), 1000)
                            else:
                                self.assertEqual(expected, rendered)

    def test_negative_and_mixed_lazy_indices(self):
        examples = (
            ("range(1,3) $[-2]", "2"),
            ("update(range(1,3),[0,-1],9) | to[String]", "[9, 2, 9]"),
            ("range(1,3) $[-4]", "IndexFault"),
            ("update(range(1,3),-4,9) | to[String]", "IndexFault"),
            ("update(range(1,3),[0,-4],9) | to[String]", "IndexFault"),
        )
        for source, expected in examples:
            for optimize, serialize in self.MODES:
                with self.subTest(source=source, optimize=optimize, serialize=serialize):
                    if expected == "IndexFault":
                        with self.assertRaisesRegex(ValianceRuntimeError, expected):
                            self.execute(source, optimize, serialize)
                    else:
                        value, = self.execute(source, optimize, serialize)
                        self.assertEqual(expected, format_runtime_value(value))

    def test_bigdecimal_modulo_exact_sign_and_scale(self):
        # BigDecimal has the same divisor-sign floor-remainder convention as
        # Python integers after both operands are scaled to an integer grid.
        for left in (-11, -4, -1, 0, 1, 4, 11):
            for right in (-5, -3, -1, 1, 3, 5):
                for left_exp, right_exp in ((0, 0), (2, 0), (0, 2), (-2, 0), (0, -2)):
                    with self.subTest(left=left, right=right, scales=(left_exp,right_exp)):
                        a, b = BigDecimal(left, left_exp), BigDecimal(right, right_exp)
                        base = min(a.exponent, b.exponent)
                        numerator = a.coefficient * 10 ** (a.exponent - base)
                        denominator = b.coefficient * 10 ** (b.exponent - base)
                        result = a % b
                        self.assertEqual(
                            BigDecimal(numerator % denominator, base), result
                        )
        self.assertEqual(BigDecimal(0), BigDecimal(1, 10_000_000) % BigDecimal(1))
        self.assertEqual(BigDecimal(1), BigDecimal(1) % BigDecimal(1, 10_000_000))
        with self.assertRaises(ZeroDivisionError):
            BigDecimal(1, 10_000_000) % BigDecimal(0)


if __name__ == "__main__":
    unittest.main()
