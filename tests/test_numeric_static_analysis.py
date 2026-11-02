"""Static analysis diagnostics on out-of-domain numeric operations."""
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

ANALYSIS_CASES = {
    'analysis/complex_positive.vlnc': 'no overloads',
    'analysis/pure_imaginary_positive.vlnc': 'no overloads',
    'analysis/huge_tuple_index.vlnc': 'out of bounds',
}

class StaticNumericDomainTests(unittest.TestCase):
    def test_complex_ordering_and_huge_constant_index_diagnostics(self):
        assert_fixture_inventory(self, 'analysis', ANALYSIS_CASES)
        for path, expected in ANALYSIS_CASES.items():
            with self.subTest(fixture=path):
                analyser = Analyser()
                analyser.analyse(parse(read_fixture(path)))
                self.assertTrue(analyser.diagnostics, f'Expected diagnostic: {path}')
                self.assertIn(expected, str(analyser.diagnostics[0]))
