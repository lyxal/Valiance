"""Regression corpus from the Round 7 source-guided audit.

All 41 cases are run in direct/serialized and optimized/unoptimized modes.
The assertions validate outcomes rather than only verifying execution.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.runtime_values import RuntimeNumber, format_runtime_value
from valiance.runtime.vm import RuntimeError as ValianceRuntimeError, VirtualMachine


class Round7SourceRegressions(unittest.TestCase):
    root = Path(__file__).parent / 'round7_programs'
    modes = ((False, False), (False, True), (True, False), (True, True))

    def test_all_audit_reproducers(self):
        files = sorted(self.root.glob('*.vlnc'))
        self.assertEqual(len(files), 41)
        lexical = {f'L{i:02}' for i in range(7, 13)}
        diagnostic = {'A31', 'A32', 'A40'}
        faults = {
            'R22': 'IndexFault', 'R23': 'IndexFault', 'R24': 'IndexFault',
            'A39': 'IndexFault', 'A41': 'IndexFault',
            'R25': 'RangeFault', 'R26': 'RangeFault',
            'R27': 'RangeFault', 'R28': 'RangeFault',
        }
        ordinary = {
            'L01': '0', 'L02': '0', 'L03': '0', 'L04': '0i1',
            'L05': '0i-3', 'L06': '0', 'N13': '1',
            'R18': 'bca', 'R19': '[2, 3, 1]', 'R20': 'cab',
            'R21': '[1, 2, 3]', 'R29': '', 'R30': '',
        }
        for file in files:
            ident = file.stem[:3]
            source = file.read_text(encoding='utf-8')
            for optimize, serialize in self.modes:
                with self.subTest(id=ident, optimize=optimize, serialized=serialize):
                    if ident in lexical:
                        with self.assertRaises(Exception) as cm:
                            parse(source)
                        self.assertIn(type(cm.exception).__name__, ('LexError', 'ParseErrors'))
                        continue
                    parsed = parse(source)
                    analyser = Analyser()
                    typed = analyser.analyse(parsed)
                    if ident in diagnostic:
                        self.assertTrue(analyser.diagnostics)
                        continue
                    self.assertFalse(analyser.diagnostics, analyser.diagnostics)
                    program = compile_program(typed, optimize=optimize)
                    if serialize:
                        program = loads(dumps(program))
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        if ident in faults:
                            with self.assertRaisesRegex(ValianceRuntimeError, faults[ident]):
                                VirtualMachine().run(program)
                            continue
                        values = VirtualMachine().run(program)
                    rendered = [format_runtime_value(v) for v in values]
                    if ident in ordinary:
                        self.assertEqual(rendered, [ordinary[ident]])
                    elif ident == 'N36':
                        self.assertEqual(output.getvalue().strip(), '9' * 101)
                    elif ident in {'N14', 'S16'}:
                        self.assertEqual(str(values[0]), '9' * 5000)
                    elif ident == 'S17':
                        self.assertEqual(str(values[0]), '-' + '9' * 5000)
                    elif ident == 'N15':
                        self.assertEqual(rendered, ['9' * 5000])
                    elif ident in {'N33', 'N35'}:
                        self.assertEqual(rendered, ['9' * 101])
                    elif ident == 'N34':
                        self.assertEqual(rendered, ['9' * 102])
                    elif ident == 'N37':
                        self.assertEqual(rendered, ['-' + '9' * 101])
                    elif ident == 'N38':
                        self.assertEqual(rendered, ['9' * 100 + '.9'])
                    else:
                        self.fail(f'Unclassified audit case: {ident}')

    def test_exact_decimal_and_host_int_contract(self):
        self.assertEqual(str(RuntimeNumber('0.1') + RuntimeNumber('0.2')), '0.3')
        self.assertEqual(int(RuntimeNumber('-0.1')), 0)
        self.assertEqual(int(RuntimeNumber('-1.9')), -1)
        self.assertEqual(format(RuntimeNumber('9' * 5000)), '9' * 5000)

    def test_bounded_repeat_preserves_nonempty_string(self):
        # No newly introduced 100k result-size ceiling for ordinary repeats.
        analyser = Analyser()
        typed = analyser.analyse(parse('"a" 100001 * | length'))
        self.assertFalse(analyser.diagnostics, analyser.diagnostics)
        values = VirtualMachine().run(compile_program(typed))
        self.assertEqual([str(v) for v in values], ['100001'])


if __name__ == '__main__':
    unittest.main()
