"""Regression controls for nominal import provenance and explicit trait evidence.

These are independent of the 79 source-chapter cases and exercise execution
in both optimization modes plus serialized bytecode where applicable.
"""

from trait_plan_support import SourceCase, TraitPlanTestCase


class TypeSoundnessRepairsTests(TraitPlanTestCase):
    def test_alias_is_same_generic_nominal_at_runtime(self):
        self.check_case(SourceCase(
            'D02-runtime',
            'import {lib.Box as Alias}\n'
            'import {lib.Box}\n'
            '$box: Box[Int] = Alias(2)\n'
            '$box.value', [2],
            modules={'lib.vlnc': 'public object[T] Box => public $value:T end\n'},
        ))

    def test_alias_unifies_in_generic_parameter_position(self):
        self.check_case(SourceCase(
            'D02-generic-parameter',
            'import {lib.Box as Alias}\n'
            'import {lib.Box}\n'
            'define[T] getBox(b:Box[T])->T=>$b.value end\n'
            'Alias(3) getBox', [3],
            modules={'lib.vlnc': 'public object[T] Box => public $value:T end\n'},
        ))

    def test_independent_module_declarations_remain_nominally_distinct(self):
        self.check_case(SourceCase(
            'A01-distinct-modules',
            'import {a.Box as A, b.Box as B}\n'
            '$invalid:B = A(2)', reject=('cannot assign',),
            modules={
                'a.vlnc': 'public object Box => public $value:Int end\n',
                'b.vlnc': 'public object Box => public $value:Int end\n',
            },
        ))

    def test_private_type_return_does_not_match_local_same_name(self):
        self.check_case(SourceCase(
            'A01-private-output',
            'import {lib.make}\n'
            'object Secret => $value:Int end\n'
            '$local:Secret = make(3)', reject=('cannot assign',),
            modules={
                'lib.vlnc': 'object Secret => $value:Int end\n'
                             'public define make(n:Int)->Secret=>Secret($n) end\n',
            },
        ))

    def test_named_bound_dispatches_multiple_implementations(self):
        self.check_case(SourceCase(
            'B01-two-implementations',
            'trait Sized => extend size -> Int end\n'
            'object Item => $value:Int end\n'
            'object Other => $value:Int end\n'
            'object Item as Sized => define size => $self.value end\n'
            'object Other as Sized => define size => $self.value + 10 end\n'
            'define[T:Sized] readSize(x:T)->Int=>$x size end\n'
            'Item(3) readSize\nOther(4) readSize', [3, 14],
        ))

    def test_bound_does_not_admit_nonimplementing_type(self):
        self.check_case(SourceCase(
            'B01-bound-enforcement',
            'trait Sized => extend size -> Int end\n'
            'object Item => $value:Int end\n'
            'object Item as Sized => define size => $self.value end\n'
            'define[T:Sized] readSize(x:T)->Int=>$x size end\n'
            'readSize(3)', reject=('no overloads',),
        ))

    def test_named_trait_method_is_not_inferred_on_unbounded_generic(self):
        self.check_case(SourceCase(
            'B01-no-hidden-constraints',
            'trait Sized => extend size -> Int end\n'
            'object Item => $value:Int end\n'
            'object Item as Sized => define size => $self.value end\n'
            'define[T] readSize(x:T)->Int=>$x size end',
            reject=('no overloads',),
        ))

class ProviderPrivateRuntimeTests(TraitPlanTestCase):
    def test_public_function_can_construct_private_object(self):
        self.check_case(SourceCase(
            'A01-provider-runtime',
            'import {lib}\nlib.read(lib.make(3))', [3],
            modules={'lib.vlnc':
                     'object Secret => $value:Int end\n'
                     'public define make(n:Int)->Secret=>Secret($n) end\n'
                     'public define read(secret:Secret)->Int=>$secret.value end\n'},
        ))

    def test_private_constructor_does_not_shadow_local_same_name(self):
        self.check_case(SourceCase(
            'A01-provider-isolated',
            'import {lib}\n'
            'object Secret => public $value:String end\n'
            '$local=Secret("local")\n'
            '$local.value\nlib.read(lib.make(3))', ['local', 3],
            modules={'lib.vlnc':
                     'object Secret => $value:Int end\n'
                     'public define make(n:Int)->Secret=>Secret($n) end\n'
                     'public define read(secret:Secret)->Int=>$secret.value end\n'},
        ))

    def test_private_constructors_from_two_providers_do_not_collide(self):
        self.check_case(SourceCase(
            'A01-two-providers-runtime',
            'import {a,b}\na.read(a.make(3))\nb.read(b.make("b"))', [3, 'b'],
            modules={
                'a.vlnc': 'object Secret => $value:Int end\n'
                          'public define make(n:Int)->Secret=>Secret($n) end\n'
                          'public define read(s:Secret)->Int=>$s.value end\n',
                'b.vlnc': 'object Secret => $value:String end\n'
                          'public define make(n:String)->Secret=>Secret($n) end\n'
                          'public define read(s:Secret)->String=>$s.value end\n',
            },
        ))

    def test_public_function_can_call_private_helper(self):
        self.check_case(SourceCase(
            'provider-private-helper-runtime',
            'import {lib.make}\nmake(3)', [4],
            modules={'lib.vlnc':
                     'define secretAdd(n:Int)->Int=>$n + 1 end\n'
                     'public define make(n:Int)->Int=>secretAdd($n) end\n'},
        ))

    def test_private_constructor_runtime_type_is_provider_qualified(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a', 'b'):
                (root / f'{name}.vlnc').write_text(
                    'object Secret => $value:Int end\n'
                    'public define make(n:Int)->Secret=>Secret($n) end\n',
                    encoding='utf-8',
                )
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('import {a,b}\na.make(3)\nb.make(4)'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                program = compile_program(typed, optimize=optimize)
                for candidate in (program, loads(dumps(program))):
                    values = run(candidate)
                    self.assertEqual([value.type_name for value in values],
                                     ['a.Secret', 'b.Secret'])
                    self.assertEqual([value.fields['value'] for value in values],
                                     [3, 4])

    def test_private_constructor_linked_from_source_free_module(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.analysis import Analyser
        from valiance.incremental import CompilationCoordinator
        from valiance.parsing import parse
        from valiance.runtime import compile_program, dumps, loads, run

        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'lib.vlnc'
            library.write_text(
                'object Secret => $value:Int end\n'
                'public define make(n:Int)->Secret=>Secret($n) end\n'
                'public define read(s:Secret)->Int=>$s.value end\n',
                encoding='utf-8',
            )
            CompilationCoordinator(root).build_module(
                library, library.with_suffix('.vbcm'), module_name='lib',
            )
            library.unlink()
            analyser = Analyser(source_file=root / 'main.vlnc')
            typed = analyser.analyse(parse('import {lib}\nlib.read(lib.make(17))'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                program = compile_program(typed, optimize=optimize)
                self.assertEqual(run(program), [17])
                self.assertEqual(run(loads(dumps(program))), [17])
