"""Lexer acceptance and diagnostics for numeric token boundaries."""
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

LEXER_CASES = {
    'lexer/negative_000.vlnc': ('parse_error', None),
    'lexer/negative_01.vlnc': ('parse_error', None),
    'lexer/negative_superscript.vlnc': ('parse_error', None),
    'lexer/superscript_digit.vlnc': ('parse_error', None),
    'lexer/superscript_exponent.vlnc': ('parse_error', None),
    'lexer/superscript_suffix.vlnc': ('parse_error', None),
    'lexer/zero_complex_imaginary.vlnc': ('value', '0i1'),
    'lexer/zero_complex_negative.vlnc': ('value', '0i-3'),
    'lexer/zero_complex_zero.vlnc': ('value', '0'),
    'lexer/zero_scientific.vlnc': ('value', '0'),
    'lexer/zero_scientific_negative_exponent.vlnc': ('value', '0'),
    'lexer/zero_scientific_uppercase.vlnc': ('value', '0'),
}

class NumericLiteralLexerTests(unittest.TestCase):
    def test_valid_zeros_and_rejected_non_ascii_numeric_tokens(self):
        assert_fixture_inventory(self, 'lexer', LEXER_CASES)
        for path, (outcome, expected) in LEXER_CASES.items():
            source = read_fixture(path)
            with self.subTest(fixture=path):
                if outcome == 'parse_error':
                    with self.assertRaises(Exception) as cm:
                        parse(source)
                    self.assertIn(type(cm.exception).__name__, ('LexError', 'ParseErrors'))
                else:
                    assert_fixture_cases(self, {path: (outcome, expected)})
