"""Exact decimal, high-magnitude arithmetic and numeric text regressions."""
from __future__ import annotations

import unittest
from pathlib import Path
from decimal import Decimal

from tests.regression_support import (FIXTURES, MODES, assert_fixture_cases,
    assert_fixture_inventory, read_fixture, execute)
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.bigdecimal import BigDecimal
from valiance.runtime.runtime_values import (DictValue, LazyList, ListValue,
    ObjectValue, RuntimeNumber, format_runtime_value, runtime_collection_rank)
from valiance.runtime.vm import RuntimeError as ValianceRuntimeError, VirtualMachine
from valiance.std.json import _render_json_value

NUMERIC_CASES = {
    'numeric/complex_power_half_large.vlnc': ('square_root', None),
    'numeric/complex_zero_complex.vlnc': ('fault', 'DomainFault'),
    'numeric/complex_zero_neg.vlnc': ('fault', 'DivisionByZeroFault'),
    'numeric/imaginary_huge_multiple_four.vlnc': ('value', '1'),
    'numeric/large_integer_print.vlnc': ('print', '9' * 101 + '\n'),
    'numeric/long_integer_conversion.vlnc': ('number', '9' * 5000),
    'numeric/long_integer_predicate.vlnc': ('value', '1'),
    'numeric/long_integer_stringification.vlnc': ('value', '9' * 5000),
    'numeric/neg_one_huge_even.vlnc': ('value', '1'),
    'numeric/neg_one_negative_huge_even.vlnc': ('value', '1'),
    'numeric/precision_101.vlnc': ('value', '9' * 101),
    'numeric/precision_101_literal.vlnc': ('value', '9' * 101),
    'numeric/precision_101_negative.vlnc': ('value', '-' + '9' * 101),
    'numeric/precision_101_real.vlnc': ('value', '9' * 100 + '.9'),
    'numeric/precision_102.vlnc': ('value', '9' * 102),
    'numeric/real_power_half_large.vlnc': ('square_root', None),
    'numeric/real_zero_neg.vlnc': ('fault', 'DivisionByZeroFault'),
    'numeric/real_zero_negative_fraction.vlnc': ('fault', 'DivisionByZeroFault'),
    'numeric/sqrt_extreme.vlnc': ('square_root', None),
    'numeric/sqrt_large.vlnc': ('square_root', None),
}

class NumericBoundaryTests(unittest.TestCase):
    MODES = MODES

    def execute(self, source, optimize=False, serialize=False):
        return execute(source, optimize, serialize)

    def test_large_numeric_programs(self):
        assert_fixture_inventory(self, 'numeric', NUMERIC_CASES)
        assert_fixture_cases(self, NUMERIC_CASES)

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

    def test_exact_decimal_literals_and_arithmetic(self):
        self.assertEqual(RuntimeNumber(Decimal('0.3')),
                         RuntimeNumber('0.1') + RuntimeNumber('0.2'))
        self.assertNotEqual(RuntimeNumber('0.1') + RuntimeNumber('0.2'),
                            RuntimeNumber('0.30000000000000004'))
        self.assertEqual(str(RuntimeNumber(0.1)), '0.1')
        self.assertEqual(str(RuntimeNumber(0.1) + RuntimeNumber(0.2)), '0.3')
        for opt, saved in self.MODES:
            with self.subTest(opt=opt, saved=saved):
                self.assertEqual('1', format_runtime_value(
                    self.execute('0.1 0.2 + | 0.3 ==', opt, saved)[0]))

    def test_exact_floor_division_with_huge_exponents(self):
        for numerator, denominator, expected in (
            (BigDecimal(0), BigDecimal(1, 1_000_001), 0),
            (BigDecimal(1), BigDecimal(1, 1_000_001), 0),
            (BigDecimal(-1), BigDecimal(1, 1_000_001), -1),
            (BigDecimal(1), BigDecimal(-1, 1_000_001), -1),
            (BigDecimal(-1), BigDecimal(-1, 1_000_001), 0),
            (BigDecimal(7), BigDecimal(2), 3),
            (BigDecimal(-7), BigDecimal(2), -4),
        ):
            self.assertEqual(numerator.floor_div(denominator), expected)

    def test_compact_powers_and_square_roots(self):
        for number in ('1e100001', '1e999999'):
            root = RuntimeNumber(number) ** RuntimeNumber('0.5')
            self.assertGreater(root.real.coefficient, 0)
            self.assertGreater(root.real.exponent, 1000)
            self.assertFalse(root.is_complex())
        self.assertEqual(RuntimeNumber(-1) ** RuntimeNumber('1e100001'), RuntimeNumber(1))
        self.assertEqual(RuntimeNumber('0i1') ** RuntimeNumber('1e100001'), RuntimeNumber(1))

    def test_exact_decimal_and_host_int_contract(self):
        self.assertEqual(str(RuntimeNumber('0.1') + RuntimeNumber('0.2')), '0.3')
        self.assertEqual(int(RuntimeNumber('-0.1')), 0)
        self.assertEqual(int(RuntimeNumber('-1.9')), -1)
        self.assertEqual(format(RuntimeNumber('9' * 5000)), '9' * 5000)

