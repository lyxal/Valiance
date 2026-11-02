"""Collection indexing, lazy replay, ownership metadata and deep-value regressions."""
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

COLLECTION_CASES = {
    'collections/empty_string_repeat_left.vlnc': ('value', ''),
    'collections/empty_string_repeat_right.vlnc': ('value', ''),
    'collections/huge_finite_take.vlnc': ('value', '[1, 2, 3]'),
    'collections/huge_index.vlnc': ('fault', 'IndexFault'),
    'collections/huge_list_rotation.vlnc': ('value', '[2, 3, 1]'),
    'collections/huge_negative_index.vlnc': ('fault', 'IndexFault'),
    'collections/huge_negative_rotation.vlnc': ('value', 'cab'),
    'collections/huge_remove_at.vlnc': ('fault', 'IndexFault'),
    'collections/huge_string_index.vlnc': ('fault', 'IndexFault'),
    'collections/huge_string_rotation.vlnc': ('value', 'bca'),
    'collections/huge_update_at.vlnc': ('fault', 'IndexFault'),
}

class CollectionBoundaryTests(unittest.TestCase):
    MODES = MODES

    def execute(self, source, optimized=False, serialized=False):
        return execute(source, optimized, serialized)

    def test_collection_source_cases(self):
        assert_fixture_inventory(self, 'collections', COLLECTION_CASES)
        assert_fixture_cases(self, COLLECTION_CASES)

    def test_index_errors_have_consistent_eager_lazy_and_deferred_faults(self):
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

    def test_bounded_repeat_preserves_nonempty_string(self):
        # No newly introduced 100k result-size ceiling for ordinary repeats.
        analyser = Analyser()
        typed = analyser.analyse(parse('"a" 100001 * | length'))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        values = VirtualMachine().run(compile_program(typed))
        self.assertEqual([str(v) for v in values], ['100001'])

