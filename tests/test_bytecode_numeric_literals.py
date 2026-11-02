"""Bytecode preserves full-precision numeric literals across serialization."""
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

BYTECODE_CASES = {
    'bytecode/large_literal_negative_serialization.vlnc': ('number', '-' + '9' * 5000),
    'bytecode/large_literal_serialization.vlnc': ('number', '9' * 5000),
}

class LargeNumericBytecodeTests(unittest.TestCase):
    def test_large_literals_survive_serialization(self):
        assert_fixture_inventory(self, 'bytecode', BYTECODE_CASES)
        assert_fixture_cases(self, BYTECODE_CASES)
