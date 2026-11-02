"""Adversarial incremental/re-export regressions for nominal and runtime safety.

All tests operate on real source/modules and the persistent compilation cache.
They intentionally use new coordinators as well as a reused coordinator and run
published bytecode, so a stale analysis that merely *compiles* cannot pass.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.incremental import BuildDisposition, CompilationCoordinator
from valiance.modules_system.modules import ModuleLoadError
from valiance.runtime import loads, loads_module, run


class IncrementalMultiModuleSoundnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name: str, source: str) -> Path:
        path = self.root / f'{name}.vlnc'
        path.write_text(source, encoding='utf-8')
        return path

    def build(self, names=('core', 'middle', 'front'), *, coordinator=None):
        coordinator = coordinator or CompilationCoordinator(self.root)
        results = {}
        for name in names:
            source = self.root / f'{name}.vlnc'
            results[name] = coordinator.build_module(
                source, source.with_suffix('.vbcm'), module_name=name,
            ).disposition
        return results

    def execute(self, *, coordinator=None, optimize=True):
        coordinator = coordinator or CompilationCoordinator(self.root)
        source = self.root / 'main.vlnc'
        binary = self.root / 'main.vbc'
        outcome = coordinator.build_executable(
            source, binary, target_identity='entry:main', optimize=optimize,
        )
        return outcome.disposition, run(loads(binary.read_bytes()))

    def chain(self, provider='public define value(n:Int)->Int=>$n+1 end\n'):
        self.write('core', provider)
        self.write('middle', 'public import {core.value}\n')
        self.write('front', 'public import {middle.value}\n')
        self.write('main', 'import {front.value}\nvalue(4)\n')

    def test_three_hop_body_change_invalidates_implementation_not_contract(self):
        self.chain()
        self.assertEqual(self.build(), dict.fromkeys(('core', 'middle', 'front'), BuildDisposition.RECOMPILED))
        self.assertEqual(self.execute()[1], [5])
        original = loads_module((self.root / 'core.vbcm').read_bytes())
        self.write('core', 'public define value(n:Int)->Int=>$n+2 end\n')
        changed = self.build()
        updated = loads_module((self.root / 'core.vbcm').read_bytes())
        self.assertEqual(original.interface_hash, updated.interface_hash)
        self.assertNotEqual(original.implementation_hash, updated.implementation_hash)
        self.assertEqual(changed, dict.fromkeys(('core', 'middle', 'front'), BuildDisposition.RECOMPILED))
        self.assertEqual(self.execute(), (BuildDisposition.RELINKED, [6]))

    def test_private_generic_helper_edit_invalidates_public_reexports(self):
        self.chain('define[T] hidden(value:T)->T=>$value end\n'
                   'public define value(n:Int)->Int=>hidden($n)+1 end\n')
        self.build()
        self.assertEqual(self.execute()[1], [5])
        self.write('core', 'define[T] hidden(value:T)->T=>$value end\n'
                    'public define value(n:Int)->Int=>hidden($n)+3 end\n')
        self.assertEqual(self.build()['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], [7])

    def test_unchanged_source_reuses_modules_and_target(self):
        self.chain()
        self.build()
        self.execute()
        self.assertEqual(self.build(), dict.fromkeys(('core', 'middle', 'front'), BuildDisposition.REUSED))
        self.assertEqual(self.execute(), (BuildDisposition.REUSED, [5]))

    def test_reusing_coordinator_after_disk_edit_does_not_reuse_stale_exports(self):
        self.chain()
        coordinator = CompilationCoordinator(self.root)
        self.build(coordinator=coordinator)
        self.assertEqual(self.execute(coordinator=coordinator)[1], [5])
        self.write('core', 'public define value(n:Int)->Int=>$n+4 end\n')
        self.assertEqual(self.build(coordinator=coordinator)['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute(coordinator=coordinator), (BuildDisposition.RELINKED, [8]))

    def test_interface_edit_reanalyses_facades_and_rejects_old_consumer(self):
        self.chain()
        self.build()
        self.execute()
        old_output = (self.root / 'main.vbc').read_bytes()
        self.write('core', 'public define value(n:String)->String=>$n end\n')
        self.assertEqual(self.build()['front'], BuildDisposition.RECOMPILED)
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertEqual((self.root / 'main.vbc').read_bytes(), old_output)
        self.write('main', 'import {front.value}\nvalue("ok")\n')
        self.assertEqual(self.execute()[1], ['ok'])

    def test_source_free_chain_uses_latest_compiled_private_helper(self):
        self.chain('define[T] hidden(value:T)->T=>$value end\n'
                   'public define value(n:Int)->Int=>hidden($n)+1 end\n')
        self.build()
        self.write('core', 'define[T] hidden(value:T)->T=>$value end\n'
                    'public define value(n:Int)->Int=>hidden($n)+7 end\n')
        self.build()
        for name in ('core', 'middle', 'front'):
            (self.root / f'{name}.vlnc').unlink()
        self.assertEqual(self.execute()[1], [11])

    def test_source_free_stale_facade_cannot_execute_old_provider_body(self):
        self.chain()
        self.build()
        self.write('core', 'public define value(n:Int)->Int=>$n+8 end\n')
        self.build(('core',))  # Preserve stale middle/front binary artifacts.
        for name in ('core', 'middle', 'front'):
            (self.root / f'{name}.vlnc').unlink()
        try:
            _, values = self.execute()
        except (RuntimeError, ModuleLoadError) as exc:
            # A controlled refusal is safer than a silently stale binary.
            self.assertTrue(str(exc))
        else:
            self.assertEqual(values, [12])

    def test_diamond_generic_alias_still_has_original_nominal_identity(self):
        self.write('core', 'public object[T] Box => public $value:T end\n')
        self.write('left', 'public import {core.[Box as LeftBox]}\n')
        self.write('right', 'public import {core.[Box as RightBox]}\n')
        self.write('main', 'import {left.LeftBox,right.RightBox}\n'
                  '$x:LeftBox[Int]=RightBox(8)\n$x.value\n')
        self.build(('core', 'left', 'right'))
        self.assertEqual(self.execute()[1], [8])
        self.write('core', 'public object[T] Box =>\n public $value:T\n public $extra:Int=0\nend\n')
        self.build(('core', 'left', 'right'))
        self.assertEqual(self.execute()[1], [8])

    def test_two_providers_private_nominals_remain_distinct_after_edit(self):
        core = ('object[T] Secret => $value:T end\n'
                'public define[T] make(value:T)->Secret[T]=>Secret[T]($value) end\n')
        self.write('a', core)
        self.write('b', core)
        self.write('main', 'import {a as left,b as right}\n'
                  'left.make(1)\nright.make("text")\n')
        self.build(('a', 'b'))
        self.write('a', core + 'public define extra(n:Int)->Int=>$n+2 end\n')
        self.assertEqual(self.build(('a','b'))['b'], BuildDisposition.REUSED)
        _, values = self.execute()
        self.assertEqual([value.type_name for value in values], ['a.Secret','b.Secret'])

    def test_implementation_dependencies_roundtrip_as_bytecode_metadata(self):
        self.chain()
        self.build()
        module = loads_module((self.root / 'front.vbcm').read_bytes())
        self.assertEqual(len(module.dependency_implementation_hashes), 1)
        self.assertEqual(module.dependency_implementation_hashes[0][0], 'local:middle')
        self.assertEqual(module.implementation_options, 'optimize=true')

    def test_deleting_provider_function_does_not_allow_stale_facade(self):
        self.chain()
        self.build()
        self.execute()
        self.write('core', 'public define other(n:Int)->Int=>$n end\n')
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.build()

    def test_rebuild_does_not_touch_unrelated_module(self):
        self.chain()
        self.write('unrelated', 'public define other(n:Int)->Int=>$n end\n')
        self.build(('core', 'middle', 'front', 'unrelated'))
        self.write('core', 'public define value(n:Int)->Int=>$n+11 end\n')
        changed = self.build(('core','middle','front','unrelated'))
        self.assertEqual(changed['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(changed['unrelated'], BuildDisposition.REUSED)

    def test_private_generic_layout_edit_recompiles_reexports(self):
        initial = ('object[T] Secret => $value:T end\n'
                   'public define[T] make(value:T)->Secret[T]=>Secret[T]($value) end\n'
                   'public define[T] read(value:Secret[T])->T=>$value.value end\n')
        self.write('core', initial)
        self.write('middle', 'public import {core.[make,read]}\n')
        self.write('front', 'public import {middle.[make,read]}\n')
        self.write('main', 'import {front.[make,read]}\nread(make(2))\nread(make("x"))\n')
        self.build()
        self.assertEqual(self.execute()[1], [2, 'x'])
        updated = initial.replace('object[T] Secret => $value:T end',
                                  'object[T] Secret =>\n $value:T\n $new:Int=7\nend')
        self.write('core', updated)
        self.assertEqual(self.build()['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], [2, 'x'])

    def test_generic_specializations_update_across_reexport_chain(self):
        self.write('core', 'public define[T] label(value:T)->String=>"old" end\n')
        self.write('middle', 'public import {core.label}\n')
        self.write('front', 'public import {middle.label}\n')
        self.write('main', 'import {front.label}\nlabel(1)\nlabel("s")\n')
        self.build()
        self.assertEqual(self.execute()[1], ['old', 'old'])
        self.write('core', 'public define[T] label(value:T)->String=>"new" end\n')
        self.build()
        self.assertEqual(self.execute()[1], ['new', 'new'])

    def test_overlay_edit_invalidates_transitive_importer_analysis(self):
        from valiance.incremental import CompilationDatabase
        self.chain()
        database = CompilationDatabase(self.root)
        original = database.analyse(self.root / 'main.vlnc')
        self.assertTrue(original.successful, original.analyser.diagnostics)
        self.assertEqual(run(database.compile_current(self.root / 'main.vlnc').program), [5])
        core = self.root / 'core.vlnc'
        database.open_document(core, 'public define value(n:Int)->Int=>$n+9 end\n')
        updated = database.analyse(self.root / 'main.vlnc')
        self.assertIsNot(original, updated)
        self.assertTrue(updated.successful, updated.analyser.diagnostics)
        self.assertEqual(run(database.compile_current(self.root / 'main.vlnc').program), [13])
        database.close_document(core)
        self.assertEqual(run(database.compile_current(self.root / 'main.vlnc').program), [5])

    def test_recreated_source_supersedes_compiled_source_free_module(self):
        self.chain()
        self.build()
        for name in ('core','middle','front'):
            (self.root / f'{name}.vlnc').unlink()
        self.assertEqual(self.execute()[1], [5])
        self.write('core', 'public define value(n:Int)->Int=>$n+5 end\n')
        # Facades still exist only as compiled bytecode; fresh analysis must
        # follow the latest provider, or explicitly reject stale binaries.
        try:
            _, values = self.execute()
        except (RuntimeError, ModuleLoadError) as exc:
            self.assertTrue(str(exc))
        else:
            self.assertEqual(values, [9])

    def test_renamed_reexport_function_tracks_provider_implementation_edit(self):
        self.write('core', 'public define[T] label(value:T)->String=>"before" end\n')
        self.write('middle', 'public import {core.[label as mark]}\n')
        self.write('front', 'public import {middle.[mark as labelled]}\n')
        self.write('main', 'import {front.labelled}\nlabelled(7)\nlabelled("s")\n')
        self.build()
        self.assertEqual(self.execute()[1], ['before', 'before'])
        self.write('core', 'public define[T] label(value:T)->String=>"after" end\n')
        self.assertEqual(self.build()['front'], BuildDisposition.RECOMPILED)
        self.assertEqual(self.execute()[1], ['after', 'after'])

    def test_source_free_changed_signature_cannot_reuse_old_generic_call(self):
        self.write('core', 'public define[T] pick(value:T)->T=>$value end\n')
        self.write('middle', 'public import {core.pick}\n')
        self.write('front', 'public import {middle.pick}\n')
        self.write('main', 'import {front.pick}\npick(3)\n')
        self.build()
        self.assertEqual(self.execute()[1], [3])
        self.write('core', 'public define pick(value:String)->String=>$value end\n')
        self.build(('core',))
        for name in ('core', 'middle', 'front'):
            (self.root / f'{name}.vlnc').unlink()
        # Earlier generic pick(Int) must not survive an incompatible edit.
        with self.assertRaises((RuntimeError, ModuleLoadError)):
            self.execute()

    def test_independent_private_providers_only_relink_changed_specialization(self):
        template = ('object Secret => $value:Int end\n'
                    'public define make(n:Int)->Secret=>Secret($n) end\n'
                    'public define read(x:Secret)->Int=>$x.value + {offset} end\n')
        self.write('a', template.format(offset=1))
        self.write('b', template.format(offset=10))
        self.write('main', 'import {a as left,b as right}\n'
                  'left.read(left.make(3))\nright.read(right.make(3))\n')
        self.build(('a', 'b'))
        self.assertEqual(self.execute()[1], [4, 13])
        self.write('a', template.format(offset=2))
        changed = self.build(('a', 'b'))
        self.assertEqual(changed['a'], BuildDisposition.RECOMPILED)
        self.assertEqual(changed['b'], BuildDisposition.REUSED)
        self.assertEqual(self.execute()[1], [5, 13])

    def test_invalid_compiled_dependency_metadata_is_rejected(self):
        import json
        import struct
        from valiance.runtime import BytecodeFormatError
        from valiance.runtime.compiled_module import MAGIC
        self.chain()
        self.build()
        data = (self.root / 'front.vbcm').read_bytes()
        pos = len(MAGIC)
        metadata_size, interface_size, code_size = struct.unpack('>III', data[pos:pos+12])
        start = pos + 12
        metadata = json.loads(data[start:start+metadata_size])
        self.assertTrue(metadata['dependency_implementations'])
        metadata['dependency_implementations'] = {'wrong': 'shape'}
        modified_metadata = json.dumps(metadata, separators=(',', ':'), sort_keys=True).encode()
        damaged = (MAGIC + struct.pack('>III', len(modified_metadata), interface_size, code_size)
                   + modified_metadata + data[start+metadata_size:])
        with self.assertRaises(BytecodeFormatError):
            loads_module(damaged)


if __name__ == '__main__':
    unittest.main()
