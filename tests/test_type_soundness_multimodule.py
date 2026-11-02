"""Multi-hop nominal provenance and provider-private dependency regressions.

All positive fixtures run from source and source-free interfaces, in both
optimizer modes, before and after program-bytecode round trips.  Private
provider declarations must execute without becoming public imports.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.analysis import Analyser
from valiance.incremental import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run


SECRET = (
    'object[T] Secret => $value:T end\n'
    'public define[T] make(value:T)->Secret[T]=>Secret[T]($value) end\n'
    'public define[T] read(value:Secret[T])->T=>$value.value end\n'
)


class MultiModuleSoundnessTests(unittest.TestCase):
    def check_modules(self, modules, source, *, expected=None, diagnostic=None,
                      expected_nominals=None):
        for source_free in (False, True):
            with self.subTest(source_free=source_free), TemporaryDirectory() as directory:
                root = Path(directory)
                for module_name, module_source in modules.items():
                    path = root / f'{module_name}.vlnc'
                    path.write_text(module_source, encoding='utf-8')
                    if source_free:
                        # Compile in dependency order while sources remain present.
                        CompilationCoordinator(root).build_module(
                            path, path.with_suffix('.vbcm'), module_name=module_name,
                        )
                if source_free:
                    for module_name in modules:
                        (root / f'{module_name}.vlnc').unlink()
                analyser = Analyser(source_file=root / 'main.vlnc')
                typed = analyser.analyse(parse(source))
                if diagnostic is not None:
                    self.assertTrue(analyser.diagnostics, 'expected compile rejection')
                    self.assertIn(diagnostic, '\n'.join(analyser.diagnostics))
                    continue
                self.assertEqual(analyser.diagnostics, [])
                for optimize in (False, True):
                    compiled = compile_program(typed, optimize=optimize)
                    for persisted, candidate in ((False, compiled), (True, loads(dumps(compiled)))):
                        with self.subTest(optimize=optimize, persisted=persisted):
                            values = run(candidate)
                            if expected_nominals is not None:
                                self.assertEqual(
                                    [value.type_name for value in values],
                                    expected_nominals,
                                )
                            else:
                                self.assertEqual(values, expected)

    def test_two_hop_private_generic_constructor_and_read(self):
        self.check_modules({
            'core': SECRET,
            'middle': 'public import {core.[make,read]}\n',
            'front': 'public import {middle.[make,read]}\n',
        }, 'import {front.[make,read]}\nread(make(4))\nread(make("hello"))',
            expected=[4, 'hello'])

    def test_two_hop_private_generic_helper(self):
        self.check_modules({
            'core': 'define[T] privateId(value:T)->T=>$value end\n'
                    'public define[T] relay(value:T)->T=>privateId($value) end\n',
            'middle': 'public import {core.[relay]}\n',
            'front': 'public import {middle.[relay]}\n',
        }, 'import {front.[relay]}\nrelay(4)\nrelay("hello")',
            expected=[4, 'hello'])

    def test_two_hop_renamed_generic_nominal_is_original_declaration(self):
        self.check_modules({
            'core': 'public object[T] Box => public $value:T end\n',
            'middle': 'public import {core.[Box as Package]}\n',
            'front': 'public import {middle.[Package as Wrapped]}\n',
        }, 'import {front.[Wrapped]}\nimport {core.Box}\n'
           '$box:Box[Int]=Wrapped(4)\n$box.value', expected=[4])

    def test_two_providers_private_generic_constructors_do_not_collide(self):
        self.check_modules({
            'a': SECRET,
            'b': SECRET,
            'middle': 'public import {a as left,b as right}\n',
            'front': 'public import {middle}\n',
        }, 'import {front}\n'
           'front.middle.left.read(front.middle.left.make(4))\n'
           'front.middle.right.read(front.middle.right.make("str"))',
            expected=[4, 'str'])

    def test_two_provider_runtime_nominals_remain_distinct_after_reexport(self):
        self.check_modules({
            'a': SECRET,
            'b': SECRET,
            'middle': 'public import {a as left,b as right}\n',
            'front': 'public import {middle}\n',
        }, 'import {front}\n'
           'front.middle.left.make(4)\n'
           'front.middle.right.make(4)',
            expected_nominals=['a.Secret', 'b.Secret'])

    def test_private_return_cannot_be_misassigned_to_local_lookalike(self):
        self.check_modules({
            'core': SECRET,
            'middle': 'public import {core.[make]}\n',
            'front': 'public import {middle.[make]}\n',
        }, 'import {front.[make]}\n'
           'object Secret => $value:Int end\n'
           '$fake:Secret=make(4)',
            diagnostic='cannot assign')

    def test_diamond_aliases_share_one_original_nominal_declaration(self):
        self.check_modules({
            'core': 'public object[T] Box => public $value:T end\n',
            'left': 'public import {core.[Box as LeftBox]}\n',
            'right': 'public import {core.[Box as RightBox]}\n',
        }, 'import {left.[LeftBox],right.[RightBox]}\n'
           '$box:LeftBox[Int]=RightBox(6)\n$box.value', expected=[6])

    def test_private_object_is_still_not_public_through_facades(self):
        self.check_modules({
            'core': SECRET,
            'middle': 'public import {core.[make,read]}\n',
            'front': 'public import {middle.[make,read]}\n',
        }, 'import {front.[make,read]}\nimport {core.Secret}',
            diagnostic="has no public component 'Secret'")

    def test_normal_reexported_call_still_works(self):
        self.check_modules({
            'core': 'public define twice(n:Int)->Int=>$n * 2 end\n',
            'middle': 'public import {core.[twice]}\n',
            'front': 'public import {middle.[twice]}\n',
        }, 'import {front.[twice]}\ntwice(9)', expected=[18])


if __name__ == '__main__':
    unittest.main()
