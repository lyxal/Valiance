"""Deterministic model-based tests for exact arithmetic and mutable values.

A small independent oracle complements fixed reproducers: the same VM bug can
otherwise appear in every optimization/serialization mode. Seeds are fixed so
CI failures are reproducible and the failing source is shown in the subtest.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
from random import Random
import unittest

from tests.regression_support import MODES, execute
from valiance.runtime.runtime_values import DictValue, LazyList, ListValue


class ExactDecimalReferenceTests(unittest.TestCase):
    def test_add_subtract_and_multiply_against_decimal(self):
        random = Random(8328)
        with localcontext() as context:
            context.prec = 100
            for _ in range(80):
                a, b = random.randint(-10**8, 10**8), random.randint(-10**8, 10**8)
                ea, eb = random.randint(-8, 8), random.randint(-8, 8)
                op = random.choice(('+', '-', '*'))
                left, right = f'{a}e{ea:+d}', f'{b}e{eb:+d}'
                expected = {'+': lambda: Decimal(left) + Decimal(right),
                            '-': lambda: Decimal(left) - Decimal(right),
                            '*': lambda: Decimal(left) * Decimal(right)}[op]()
                source = f'{left} {right} {op}'
                for optimize, serialize in MODES:
                    with self.subTest(source=source, optimize=optimize, serialized=serialize):
                        values = execute(source, optimize, serialize)
                        self.assertEqual(len(values), 1)
                        self.assertEqual(Decimal(str(values[0])), expected)


class MutableCollectionReferenceTests(unittest.TestCase):
    def test_list_mutations_agree_with_python_lists(self):
        random = Random(808801)
        for case in range(60):
            actual, expected = ListValue(), []
            for step in range(25):
                operation = random.choice(('append', 'extend', 'pop', 'insert', 'reverse', 'set', 'clear'))
                value, index = random.randint(-10, 10), random.randint(-12, 12)
                if operation == 'append':
                    actual.append(value); expected.append(value)
                elif operation == 'extend':
                    actual.extend(iter((value, value + 1)))
                    expected.extend((value, value + 1))
                elif operation == 'pop' and expected:
                    actual.pop(); expected.pop()
                elif operation == 'insert':
                    actual.insert(index, value); expected.insert(index, value)
                elif operation == 'reverse':
                    actual.reverse(); expected.reverse()
                elif operation == 'set' and expected:
                    index %= len(expected)
                    actual[index] = value; expected[index] = value
                elif operation == 'clear':
                    actual.clear(); expected.clear()
                with self.subTest(case=case, step=step, operation=operation):
                    self.assertEqual(list(actual), expected)

    def test_dict_mutations_agree_with_python_dicts(self):
        random = Random(908801)
        for case in range(60):
            actual, expected = DictValue(), {}
            for step in range(25):
                operation = random.choice(('set', 'update', 'pop', 'clear', 'setdefault'))
                key, value = random.randint(0, 14), random.randint(-10, 10)
                if operation == 'set':
                    actual[key] = value; expected[key] = value
                elif operation == 'update':
                    update = {key: value, key + 1: value + 1}
                    actual.update(update); expected.update(update)
                elif operation == 'pop':
                    actual.pop(key, None); expected.pop(key, None)
                elif operation == 'clear':
                    actual.clear(); expected.clear()
                elif operation == 'setdefault':
                    actual.setdefault(key, value); expected.setdefault(key, value)
                with self.subTest(case=case, step=step, operation=operation):
                    self.assertEqual(dict(actual), expected)

    def test_lazy_replay_preserves_values_and_terminal_failures(self):
        random = Random(38180)
        for case in range(80):
            limit, fail = random.randint(0, 20), random.choice((True, False))
            def source():
                yield from range(limit)
                if fail:
                    raise ValueError('injected lazy failure')
            lazy = LazyList(source())
            for replay in range(3):
                got = []
                with self.subTest(case=case, replay=replay, limit=limit, fail=fail):
                    if fail:
                        with self.assertRaisesRegex(ValueError, 'injected lazy failure'):
                            for value in lazy:
                                got.append(value)
                    else:
                        got.extend(lazy)
                    self.assertEqual(got, list(range(limit)))


if __name__ == '__main__':
    unittest.main()
