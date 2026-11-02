"""Trait-evidence and generic-specialization invalidation across module graphs.

These tests deliberately build real compiled modules and linked executable files.
They distinguish interface changes, implementation-only edits, removed trait
implications, and safe reuse of independent artifacts. No source-only synthetic
cache entries are used as evidence for an incremental build's correctness.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.incremental import BuildDisposition, CompilationCoordinator
from valiance.modules_system.modules import ModuleLoadError
from valiance.runtime import loads, loads_module, run


BASE = (
    'public trait Origin => end\n'
    'public trait Target => extend label -> String end\n'
    'public object Item => $id:Int end\n'
    'object Item as Origin => end\n'
)


def bridge(label: str = 'one') -> str:
    return (
        'import {base.[Origin,Target]}\n'
        'trait Origin as Target => '
        f'define label -> String => "{label}" end\n'
    )


FACADE = 'public import {base.[Item,Origin,Target],bridge.trait Origin as Target}\n'
CALLER = 'import {front.[Item,Origin,Target]}\nItem(1) as[Target] | label\n'

GENERIC_BASE = (
    'public trait[T] Source => extend read -> T end\n'
    'public trait[T] Target => extend label -> String end\n'
    'public object[T] Box => $value:T end\n'
    'object[T] Box as Source[T] => define read -> T => $self.value end\n'
)


def generic_bridge(label: str = 'first') -> str:
    return (
        'import {base.[Source,Target]}\n'
        'trait[T] Source as Target[T] => '
        f'define label -> String => "{label}" end\n'
    )


GENERIC_FACADE = (
    'public import {base.[Box,Source,Target],bridge.trait Source as Target}\n'
)
GENERIC_CALLER = (
    'import {front.[Box,Source,Target]}\n'
    'Box(3) as[Target[Int]] | label\n'
    'Box("x") as[Target[String]] | label\n'
)


class IncrementalTraitMultiModuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.coordinator = CompilationCoordinator(self.root)

    def write(self, name: str, source: str):
        path = self.root / f'{name}.vlnc'
        path.write_text(source, encoding='utf-8')
        return path

    def configure(self, *, generic=False):
        if generic:
            self.write('base', GENERIC_BASE)
            self.write('bridge', generic_bridge())
            self.write('front', GENERIC_FACADE)
            self.write('main', GENERIC_CALLER)
        else:
            self.write('base', BASE)
            self.write('bridge', bridge())
            self.write('front', FACADE)
            self.write('main', CALLER)

    def modules(self, names=('base', 'bridge', 'front')):
        return {
            name: self.coordinator.build_module(
                self.root / f'{name}.vlnc', self.root / f'{name}.vbcm',
                module_name=name,
            ).disposition
            for name in names
        }

    def execute(self, *, optimize=True):
        source = self.root / 'main.vlnc'
        output = self.root / ('main_opt.vbc' if optimize else 'main_plain.vbc')
        result = self.coordinator.build_executable(
            source, output, target_identity=f'main:{optimize}', optimize=optimize,
        )
        return result.disposition, run(loads(output.read_bytes()))

    def source_free(self, names=('base', 'bridge', 'front')):
        for name in names:
            (self.root / f'{name}.vlnc').unlink()

    def test_trait_implementation_body_edit_invalidates_three_module_chain(self):
        self.configure()
        self.assertEqual(set(self.modules().values()), {BuildDisposition.RECOMPILED})
        self.assertEqual(self.execute(), (BuildDisposition.RELINKED, ['one']))
        original = loads_module((self.root / 'bridge.vbcm').read_bytes())
        self.write('bridge', bridge('two'))
        changed = self.modules()
        updated = loads_module((self.root / 'bridge.vbcm').read_bytes())
        self.assertEqual(original.interface_hash, updated.interface_hash)
        self.assertNotEqual(original.implementation_hash, updated.implementation_hash)
        self.assertEqual(changed['base'], BuildDisposition.REUSED)
        self.assertEqual(changed['bridge'], BuildDisposition.RECOMPILED)
        self.assertEqual(changed['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute(), (BuildDisposition.RELINKED, ['two']))
        self.assertEqual(self.execute(), (BuildDisposition.REUSED, ['two']))

    def test_reexported_trait_body_edit_is_correct_without_any_source_modules(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        self.write('bridge', bridge('updated'))
        self.modules()
        self.source_free()
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                self.assertEqual(self.execute(optimize=optimize)[1], ['updated'])

    def test_unchanged_trait_dependencies_reuse_provider_and_consumer(self):
        self.configure()
        self.modules()
        self.execute()
        self.assertEqual(set(self.modules().values()), {BuildDisposition.REUSED})
        self.assertEqual(self.execute(), (BuildDisposition.REUSED, ['one']))

    def test_trait_requirement_edit_cannot_leave_a_stale_valid_implementation(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        self.write('base', BASE.replace('extend label -> String', 'extend label -> Int'))
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.modules()
        self.write('base', BASE)
        # The provider was restored byte-for-byte; reusing the old validated artifact is safe.
        self.assertEqual(self.modules()['front'], BuildDisposition.REUSED)
        self.assertEqual(self.execute()[1], ['one'])

    def test_deleted_trait_implication_cannot_be_reused_from_compiled_facade(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        self.write('bridge', 'import {base.[Origin,Target]}\n')
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.modules()
        self.write('bridge', bridge('restored'))
        self.modules()
        self.assertEqual(self.execute()[1], ['restored'])

    def test_generic_trait_impl_edit_updates_both_type_specializations(self):
        self.configure(generic=True)
        self.modules()
        self.assertEqual(self.execute()[1], ['first', 'first'])
        self.write('bridge', generic_bridge('second'))
        self.assertEqual(self.modules()['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], ['second', 'second'])

    def test_generic_trait_impl_source_free_after_reexport_update(self):
        self.configure(generic=True)
        self.modules()
        self.write('bridge', generic_bridge('new'))
        self.modules()
        self.source_free()
        self.assertEqual(self.execute(optimize=False)[1], ['new', 'new'])
        self.assertEqual(self.execute(optimize=True)[1], ['new', 'new'])

    def test_unrelated_trait_provider_remains_reusable(self):
        self.configure()
        self.write('independent', 'public trait Other => extend read -> Int end\n')
        self.modules(('base', 'bridge', 'front', 'independent'))
        self.write('bridge', bridge('after'))
        result = self.modules(('base', 'bridge', 'front', 'independent'))
        self.assertEqual(result['independent'], BuildDisposition.REUSED)
        self.assertEqual(self.execute()[1], ['after'])

    def test_changed_impl_does_not_reuse_stale_executable_across_coordinators(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        self.write('bridge', bridge('three'))
        other = CompilationCoordinator(self.root)
        self.coordinator = other
        self.assertEqual(self.modules()['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], ['three'])

    def test_generic_trait_metadata_survives_rebuild_as_compiled_module(self):
        self.configure(generic=True)
        self.modules()
        front = loads_module((self.root / 'front.vbcm').read_bytes())
        self.assertTrue(front.dependency_implementation_hashes)
        self.assertEqual(self.execute()[1], ['first', 'first'])
        self.assertEqual(self.modules()['front'], BuildDisposition.REUSED)


    def test_generic_constraint_edit_rejects_now_inapplicable_consumer(self):
        self.configure(generic=True)
        self.modules()
        self.assertEqual(self.execute()[1], ['first', 'first'])
        self.write('bridge', generic_bridge().replace('trait[T] Source',
                                                       'trait[T:Number] Source'))
        self.assertEqual(self.modules()['front'], BuildDisposition.RECOMPILED)
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.execute()
        # Restoring the unconstrained contract permits both instantiations.
        self.write('bridge', generic_bridge())
        self.modules()
        self.assertEqual(self.execute()[1], ['first', 'first'])

    def test_source_free_stale_facade_reloads_new_trait_implementation(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        # Rebuild only the provider; the facade artifact is deliberately stale.
        self.write('bridge', bridge('revised'))
        self.modules(('bridge',))
        self.source_free()
        self.assertEqual(self.execute()[1], ['revised'])

    def test_source_free_stale_facade_refuses_deleted_trait_implementation(self):
        self.configure()
        self.modules()
        self.assertEqual(self.execute()[1], ['one'])
        self.write('bridge', 'import {base.[Origin,Target]}\n')
        self.modules(('bridge',))
        self.source_free()
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.execute()


    def test_diamond_trait_reexports_preserve_single_implication_identity(self):
        self.configure()
        # Both facades publish the same originating trait implication.
        self.write('left', FACADE)
        self.write('right', FACADE)
        self.write('main', (
            'import {left.[Item,Origin,Target],right.trait Origin as Target}\n'
            'Item(3) as[Target] | label\n'
        ))
        self.modules(('base', 'bridge', 'left', 'right'))
        self.assertEqual(self.execute()[1], ['one'])
        self.write('bridge', bridge('changed'))
        changes = self.modules(('base', 'bridge', 'left', 'right'))
        self.assertEqual(changes['left'], BuildDisposition.RECOMPILED)
        self.assertEqual(changes['right'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], ['changed'])

    def test_diamond_trait_reexports_survive_source_free_bytecode(self):
        self.configure()
        self.write('left', FACADE)
        self.write('right', FACADE)
        self.write('main', (
            'import {left.[Item,Origin,Target],right.trait Origin as Target}\n'
            'Item(3) as[Target] | label\n'
        ))
        self.modules(('base', 'bridge', 'left', 'right'))
        self.source_free(('base', 'bridge', 'left', 'right'))
        self.assertEqual(self.execute(optimize=False)[1], ['one'])
        self.assertEqual(self.execute(optimize=True)[1], ['one'])

    def test_bounded_implications_use_projected_trait_parameters(self):
        # The Source argument is Pair's SECOND parameter, not its first.
        # Providers also use a different generic name from Source's T.
        self.write('base', (
            'public trait[T] Source => extend read -> T end\n'
            'public trait[T] Target => extend label -> String end\n'
            'public object[A,B] Pair =>\n$key:A\n$value:B\nend\n'
            'object[A,B] Pair as Source[B] => '
            'define read -> B => $self.value end\n'
        ))
        self.write('numeric', (
            'import {base.[Source,Target]}\n'
            'trait[U:Number] Source as Target[U] => '
            'define label -> String => "numeric" end\n'
        ))
        self.write('textual', (
            'import {base.[Source,Target]}\n'
            'trait[U:String] Source as Target[U] => '
            'define label -> String => "textual" end\n'
        ))
        self.write('front', (
            'public import {base.[Pair,Source,Target],'
            'numeric.trait Source as Target,textual.trait Source as Target}\n'
        ))
        self.write('main', (
            'import {front.[Pair,Source,Target]}\n'
            'Pair("key",3) as[Target[Int]] | label\n'
            'Pair(1,"hello") as[Target[String]] | label\n'
        ))
        self.modules(('base', 'numeric', 'textual', 'front'))
        self.assertEqual(self.execute(optimize=False)[1], ['numeric', 'textual'])
        self.source_free(('base', 'numeric', 'textual', 'front'))
        self.assertEqual(self.execute(optimize=True)[1], ['numeric', 'textual'])

    def test_overlapping_generic_bounds_are_statically_ambiguous(self):
        self.write('base', GENERIC_BASE)
        self.write('numeric', (
            'import {base.[Source,Target]}\n'
            'trait[T:Number] Source as Target[T] => '
            'define label -> String => "numeric" end\n'
        ))
        self.write('real', (
            'import {base.[Source,Target]}\n'
            'trait[T:Real] Source as Target[T] => '
            'define label -> String => "real" end\n'
        ))
        self.write('front', (
            'public import {base.[Box,Source,Target],'
            'numeric.trait Source as Target,real.trait Source as Target}\n'
        ))
        self.write('main', (
            'import {front.[Box,Source,Target]}\n'
            'Box(3) as[Target[Int]] | label\n'
        ))
        self.modules(('base', 'numeric', 'real', 'front'))
        with self.assertRaisesRegex(RuntimeError, 'ambiguous implementation'):
            self.execute()

    def test_disjoint_generic_trait_implications_dispatch_by_instantiated_bound(self):
        # A Number-constrained and a String-constrained blanket implication
        # must not both be callable for the same concrete Box[T].
        self.write('base', GENERIC_BASE)
        self.write('numeric', (
            'import {base.[Source,Target]}\n'
            'trait[T:Number] Source as Target[T] => '
            'define label -> String => "numeric" end\n'
        ))
        self.write('textual', (
            'import {base.[Source,Target]}\n'
            'trait[T:String] Source as Target[T] => '
            'define label -> String => "textual" end\n'
        ))
        self.write('front', (
            'public import {base.[Box,Source,Target],'
            'numeric.trait Source as Target,textual.trait Source as Target}\n'
        ))
        self.write('main', (
            'import {front.[Box,Source,Target]}\n'
            'Box(3) as[Target[Int]] | label\n'
            'Box("x") as[Target[String]] | label\n'
        ))
        self.modules(('base', 'numeric', 'textual', 'front'))
        for optimize in (False, True):
            with self.subTest(optimize=optimize):
                self.assertEqual(self.execute(optimize=optimize)[1], ['numeric', 'textual'])
        self.source_free(('base', 'numeric', 'textual', 'front'))
        for optimize in (False, True):
            with self.subTest(source_free=True, optimize=optimize):
                self.assertEqual(self.execute(optimize=optimize)[1], ['numeric', 'textual'])


if __name__ == '__main__':
    unittest.main()
