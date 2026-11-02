"""Round 6 runtime safety and exact-decimal semantics regressions."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.bigdecimal import BigDecimal
from valiance.runtime.runtime_values import (
    DictValue, LazyList, ListValue, ObjectValue, RuntimeNumber,
    format_runtime_value, runtime_collection_rank,
)
from valiance.runtime.vm import RuntimeError as VMRuntimeError, VirtualMachine
from valiance.std.json import _render_json_value


class Round6RuntimeRegressions(unittest.TestCase):
    MODES = ((False, False), (False, True), (True, False), (True, True))

    def execute(self, source: str, optimize: bool, serialize: bool):
        analyser = Analyser()
        analysed = analyser.analyse(parse(source))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        program = compile_program(analysed, optimize=optimize)
        if serialize:
            program = loads(dumps(program))
        return VirtualMachine().run(program)

    def test_round6_language_reproducers_all_modes(self):
        # A self-contained corpus, avoiding dependencies on the audit ZIP.
        cases = (
            ('import {std.trig} 2 trig.asin', 'DomainFault'),
            ('import {std.trig} -2 trig.acos', 'DomainFault'),
            ('import {std.trig} 1i2 trig.sin', 'DomainFault'),
            ('import {std.trig} 1i2 trig.atan', 'DomainFault'),
            ('import {std.trig} 1e100001 trig.cos', 'RangeFault'),
            ('0 -1 **', 'DivisionByZeroFault'),
            ('0 -0.5 **', 'DivisionByZeroFault'),
            ('0 -1 **c', 'DivisionByZeroFault'),
            ('0 1i1 **c', 'DomainFault'),
            ('-1 1e100001 **c', '1'),
            ('-1 -1e100001 **c', '1'),
            ('1i1 1 - | 1e100001 **c', '1'),
            ('import {std.regex} "a" "\\\\g<unknown>" "a" regex.replace', 'ValueFault'),
            ('import {std.random} 10 1 random.between', 'RangeFault'),
            ('import {std.string} "abc" "abc" "Z" string.transliterate', 'ValueFault'),
        )
        for source, expected in cases:
            for optimize, serialize in self.MODES:
                with self.subTest(source=source, opt=optimize, saved=serialize):
                    if expected.endswith('Fault'):
                        with self.assertRaisesRegex(VMRuntimeError, expected):
                            self.execute(source, optimize, serialize)
                    else:
                        result = self.execute(source, optimize, serialize)
                        self.assertEqual([expected], [format_runtime_value(v) for v in result])

    def test_all_thirty_original_round6_reproducers(self):
        """The full audit corpus is committed with the repaired project."""
        root = Path(__file__).parent / 'round6_programs'
        files = sorted(root.glob('*.vlnc'))
        self.assertEqual(len(files), 30)
        for file in files:
            case = int(file.stem.split('_')[0])
            source = file.read_text()
            if case <= 10 or case == 17:
                expected = 'DomainFault'
            elif case <= 13:
                expected = 'RangeFault'
            elif case <= 16:
                expected = 'DivisionByZeroFault'
            elif case <= 20:
                expected = '1'
            elif case <= 23:
                expected = 'ValueFault'
            elif case == 24:
                expected = 'RangeFault'
            elif case <= 26:
                expected = 'ValueFault'
            else:
                expected = 'positive compact square root'
            for optimize, serialize in self.MODES:
                with self.subTest(case=case, opt=optimize, saved=serialize):
                    if expected.endswith('Fault'):
                        with self.assertRaisesRegex(VMRuntimeError, expected):
                            self.execute(source, optimize, serialize)
                    else:
                        result = self.execute(source, optimize, serialize)
                        self.assertEqual(len(result), 1)
                        if expected == '1':
                            self.assertEqual(format_runtime_value(result[0]), '1')
                        else:
                            self.assertIsInstance(result[0], RuntimeNumber)
                            self.assertFalse(result[0].is_complex())
                            self.assertGreater(result[0].real.coefficient, 0)
                            self.assertGreater(result[0].real.exponent, 1000)

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

    def test_lazy_terminal_failure_is_replayed(self):
        def generate():
            yield 7
            raise ValueError('deliberate iteration failure')
        value = LazyList(generate())
        for _ in range(3):
            with self.assertRaisesRegex(ValueError, 'deliberate iteration failure'):
                list(value)
        self.assertEqual([7], value._cache)

    def test_partial_mutations_invalidate_ownership_metadata(self):
        def items():
            yield ObjectValue('Some', {'value': 1})
            raise ValueError('broken iterator')
        for method in ('extend', 'iadd'):
            with self.subTest(method=method):
                value = ListValue([1])
                value._ownership_trivial = True
                value._tag_free = True
                with self.assertRaisesRegex(ValueError, 'broken iterator'):
                    if method == 'extend':
                        value.extend(items())
                    else:
                        value += items()
                self.assertEqual(len(value), 2)
                self.assertIsNone(value._ownership_trivial)
                self.assertIsNone(value._tag_free)

        def pairs():
            yield ('item', ObjectValue('Some', {'value': 1}))
            raise ValueError('broken iterator')
        value = DictValue({'first': 1})
        value._ownership_trivial = True
        with self.assertRaisesRegex(ValueError, 'broken iterator'):
            value.update(pairs())
        self.assertEqual(len(value), 2)
        self.assertIsNone(value._ownership_trivial)

        # Emptying an owner-bearing list must also clear its cached tag scan.
        for action in ('clear', 'imul'):
            value = ListValue([ObjectValue('Some', {'value': 1})])
            value._ownership_trivial = False
            value._tag_free = False
            if action == 'clear':
                value.clear()
            else:
                value *= 0
            self.assertEqual(value, [])
            self.assertTrue(value._ownership_trivial)
            self.assertTrue(value._tag_free)

    def test_deep_json_dict_and_list_encode(self):
        for kind in ('list', 'dict'):
            value = 1
            for _ in range(600):
                value = [value] if kind == 'list' else {'nested': value}
            encoded = _render_json_value(value, set(), None)
            self.assertTrue(encoded.startswith('[' if kind == 'list' else '{'))
            self.assertEqual(encoded.count('1'), 1)

    def test_rank_and_cyclic_display(self):
        value = 1
        for _ in range(600):
            value = [value]
        self.assertEqual(runtime_collection_rank(value), 600)
        for value in ([], {}):
            if isinstance(value, list):
                value.append(value)
            else:
                value['loop'] = value
            with self.assertRaisesRegex(ValueError, 'cyclic'):
                format_runtime_value(value)

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


if __name__ == '__main__':
    unittest.main()
