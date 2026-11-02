"""Standard-library faults and Unicode scalar validation boundaries."""
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

STDLIB_CASES = {
    'stdlib/acos_2.vlnc': ('fault', 'DomainFault'),
    'stdlib/acos_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/acos_neg2.vlnc': ('fault', 'DomainFault'),
    'stdlib/asin_2.vlnc': ('fault', 'DomainFault'),
    'stdlib/asin_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/asin_neg2.vlnc': ('fault', 'DomainFault'),
    'stdlib/atan_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/cos_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/cos_huge.vlnc': ('fault', 'RangeFault'),
    'stdlib/transliterate_long_target.vlnc': ('fault', 'ValueFault'),
    'stdlib/regex_unknown_named_group_with_named_capture.vlnc': ('fault', 'ValueFault'),
    'stdlib/regex_unknown_named_group.vlnc': ('fault', 'ValueFault'),
    'stdlib/regex_unknown_named_group_with_capture.vlnc': ('fault', 'ValueFault'),
    'stdlib/random_invalid_bounds.vlnc': ('fault', 'RangeFault'),
    'stdlib/transliterate_short_target.vlnc': ('fault', 'ValueFault'),
    'stdlib/sin_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/sin_huge.vlnc': ('fault', 'RangeFault'),
    'stdlib/tan_complex.vlnc': ('fault', 'DomainFault'),
    'stdlib/tan_huge.vlnc': ('fault', 'RangeFault'),
}
UNICODE_CASES = {
    'unicode/huge_from_charcode.vlnc': ('fault', 'RangeFault'),
    'unicode/low_surrogate_codepoint.vlnc': ('fault', 'RangeFault'),
    'unicode/high_surrogate_codepoint.vlnc': ('fault', 'RangeFault'),
    'unicode/print_high_surrogate.vlnc': ('fault', 'RangeFault'),
}

class StandardLibraryFaultTests(unittest.TestCase):
    def test_standard_library_fault_contracts(self):
        assert_fixture_inventory(self, 'stdlib', STDLIB_CASES)
        assert_fixture_cases(self, STDLIB_CASES)

    def test_unicode_scalar_range_contract(self):
        assert_fixture_inventory(self, 'unicode', UNICODE_CASES)
        assert_fixture_cases(self, UNICODE_CASES)
