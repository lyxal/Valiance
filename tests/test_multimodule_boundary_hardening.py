"""Project and native-boundary invariants not captured by scalar source fixtures.

Avoid treating these as extra unordered language-feature pairs: they are
subsystem state machines and integration checks with independent oracles.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.incremental import CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import NativeCallReference
from valiance.runtime.runtime_values import FFIHandleValue
from valiance.runtime.vm import VirtualMachine, _invoke_native_leased
import valiance.runtime.vm as vm_module


class NativeBoundaryOwnershipTests(TestCase):
    """Lease state is modelled independently from the mocked C call."""

    def test_destroy_is_deferred_until_last_lease_and_cannot_be_reused(self):
        vm = VirtualMachine()
        handle = FFIHandleValue('&Widget', 101)
        ref = NativeCallReference('', 'destroy_widget', ('&Widget',), None,
                                  destroy_params=(0,))
        def native(_ref, args, _vm):
            self.assertIs(args[0], handle)
            self.assertEqual(handle.active_leases, 1)
            self.assertTrue(handle.alive)
            self.assertFalse(handle.destroy_pending)
            return (17,)
        try:
            with patch.object(vm_module, '_invoke_native', side_effect=native):
                self.assertEqual(_invoke_native_leased(ref, (handle,), vm), (17,))
            self.assertEqual(handle.active_leases, 0)
            self.assertTrue(handle.destroy_pending)
            self.assertFalse(handle.alive)
            with self.assertRaisesRegex(ValueError, 'not live'):
                _invoke_native_leased(ref, (handle,), vm)
        finally:
            vm.close()

    def test_failed_native_call_releases_lease_without_destroying_handle(self):
        vm = VirtualMachine()
        handle = FFIHandleValue('&Widget', 102)
        ref = NativeCallReference('', 'fail_widget', ('&Widget',))
        try:
            with patch.object(vm_module, '_invoke_native', side_effect=RuntimeError('native fault')):
                with self.assertRaisesRegex(RuntimeError, 'native fault'):
                    _invoke_native_leased(ref, (handle,), vm)
            self.assertEqual(handle.active_leases, 0)
            self.assertTrue(handle.alive)
            self.assertFalse(handle.destroy_pending)
            with patch.object(vm_module, '_invoke_native', return_value=(3,)):
                self.assertEqual(_invoke_native_leased(ref, (handle,), vm), (3,))
        finally:
            vm.close()

    def test_duplicate_native_handle_is_leased_only_once(self):
        vm = VirtualMachine()
        handle = FFIHandleValue('&Widget', 103)
        ref = NativeCallReference('', 'echo_widget', ('&Widget', '&Widget'))
        def native(_ref, _args, _vm):
            self.assertEqual(handle.active_leases, 1)
            return (7,)
        try:
            with patch.object(vm_module, '_invoke_native', side_effect=native):
                self.assertEqual(_invoke_native_leased(ref, (handle, handle), vm), (7,))
            self.assertEqual(handle.active_leases, 0)
            self.assertTrue(handle.alive)
        finally:
            vm.close()

    def test_destroy_metadata_targets_only_selected_argument(self):
        vm = VirtualMachine()
        first = FFIHandleValue('&Widget', 104)
        second = FFIHandleValue('&Widget', 105)
        ref = NativeCallReference('', 'destroy_second', ('&Widget', '&Widget'),
                                  destroy_params=(1,))
        try:
            with patch.object(vm_module, '_invoke_native', return_value=()):
                self.assertEqual(_invoke_native_leased(ref, (first, second), vm), ())
            self.assertTrue(first.alive)
            self.assertFalse(first.destroy_pending)
            self.assertFalse(second.alive)
            self.assertTrue(second.destroy_pending)
            self.assertEqual((first.active_leases, second.active_leases), (0, 0))
        finally:
            vm.close()

    def test_partial_acquisition_rolls_back_earlier_handle(self):
        vm = VirtualMachine()
        first = FFIHandleValue('&Widget', 106)
        second = FFIHandleValue('&Widget', 107)
        second.request_destroy()
        ref = NativeCallReference('', 'read', ('&Widget', '&Widget'))
        try:
            with self.assertRaisesRegex(ValueError, 'not live'):
                _invoke_native_leased(ref, (first, second), vm)
            self.assertEqual(first.active_leases, 0)
            self.assertTrue(first.alive)
            self.assertFalse(first.destroy_pending)
        finally:
            vm.close()

    def test_destroy_request_during_host_fault_finishes_teardown(self):
        vm = VirtualMachine()
        handle = FFIHandleValue('&Widget', 108)
        ref = NativeCallReference('', 'fault_after_release', ('&Widget',))
        def native(_ref, _args, _vm):
            handle.request_destroy()
            self.assertTrue(handle.alive)
            self.assertEqual(handle.active_leases, 1)
            raise RuntimeError('host fault')
        try:
            with patch.object(vm_module, '_invoke_native', side_effect=native):
                with self.assertRaisesRegex(RuntimeError, 'host fault'):
                    _invoke_native_leased(ref, (handle,), vm)
            self.assertFalse(handle.alive)
            self.assertEqual(handle.active_leases, 0)
        finally:
            vm.close()


class DistinctPackageIdentityTests(TestCase):
    """Source and source-free packages must not alias nominal type identities."""

    @staticmethod
    def _create(root: Path) -> None:
        for name in ('alpha', 'beta'):
            path = root / name / 'model.vlnc'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('public object Item => public $value:Int end\n'
                            'public define read(value:Item)->Int=>$value.value end\n',
                            encoding='utf-8')
        (root / 'main.vlnc').write_text(
            'import {alpha.model.[Item as Alpha,read as alphaRead],'
            'beta.model.[Item as Beta,read as betaRead]}\n'
            '$a=Alpha(17)\n$a.value\n$b=Beta(23)\n$b.value\n', encoding='utf-8')

    def test_same_named_objects_from_distinct_packages(self):
        for layout in ('source', 'compiled'):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                self._create(root)
                if layout == 'compiled':
                    coordinator = CompilationCoordinator(root)
                    for name in ('alpha.model', 'beta.model'):
                        path = root.joinpath(*name.split('.')).with_suffix('.vlnc')
                        coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
                        path.unlink()
                analyser = Analyser(source_file=root / 'main.vlnc')
                typed = analyser.analyse(parse((root / 'main.vlnc').read_text()))
                self.assertEqual(analyser.diagnostics, [])
                for optimize in (False, True):
                    bytecode = compile_program(typed, optimize=optimize)
                    self.assertEqual(run(bytecode), [17, 23])
                    self.assertEqual(run(loads(dumps(bytecode))), [17, 23])

    def test_renamed_functions_from_distinct_packages_retain_nominal_identity(self):
        """A selected nominal alias must update same-import callable contracts."""
        for layout in ('source', 'compiled'):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                self._create(root)
                if layout == 'compiled':
                    coordinator = CompilationCoordinator(root)
                    for name in ('alpha.model', 'beta.model'):
                        path = root.joinpath(*name.split('.')).with_suffix('.vlnc')
                        coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
                        path.unlink()
                imported = ('import {alpha.model.[Item as Alpha,read as alphaRead],'
                            'beta.model.[Item as Beta,read as betaRead]}\n')
                analyser = Analyser(source_file=root / 'main.vlnc')
                typed = analyser.analyse(parse(
                    imported + 'alphaRead(Alpha(17))\nbetaRead(Beta(23))\n'
                ))
                self.assertEqual(analyser.diagnostics, [])
                for optimize in (False, True):
                    compiled = compile_program(typed, optimize=optimize)
                    self.assertEqual(run(compiled), [17, 23])
                    self.assertEqual(run(loads(dumps(compiled))), [17, 23])
                # Same spelling in the two providers is not shared identity.
                rejected = Analyser(source_file=root / 'main.vlnc')
                rejected.analyse(parse(imported + 'betaRead(Alpha(17))\n'))
                self.assertIn('no overloads', '\n'.join(rejected.diagnostics))

    def test_nested_package_incremental_rebuild_and_source_free_reuse(self):
        """Qualified package identities survive body edits and source removal."""
        from valiance.incremental import BuildDisposition
        with TemporaryDirectory() as folder:
            root = Path(folder)
            core = root / 'pkg' / 'core.vlnc'
            core.parent.mkdir(parents=True)
            core.write_text('public define answer(x:Int)->Int=>$x+1 end\n', encoding='utf-8')
            api = root / 'api.vlnc'
            api.write_text('public import {pkg.core.answer}\n', encoding='utf-8')
            main = root / 'main.vlnc'
            main.write_text('import {api.answer}\nanswer(10)\n', encoding='utf-8')
            coordinator = CompilationCoordinator(root)
            for name, path in (('pkg.core', core), ('api', api)):
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
            destination = root / 'main.vbc'
            coordinator.build_executable(main, destination, target_identity='main')
            self.assertEqual(run(loads(destination.read_bytes())), [11])
            self.assertEqual(coordinator.build_executable(
                main, destination, target_identity='main').disposition,
                BuildDisposition.REUSED)
            core.write_text('public define answer(x:Int)->Int=>$x+9 end\n', encoding='utf-8')
            for name, path in (('pkg.core', core), ('api', api)):
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
            self.assertNotEqual(coordinator.build_executable(
                main, destination, target_identity='main').disposition,
                BuildDisposition.REUSED)
            self.assertEqual(run(loads(destination.read_bytes())), [19])
            core.unlink()
            api.unlink()
            coordinator.build_executable(main, destination, target_identity='main')
            self.assertEqual(run(loads(destination.read_bytes())), [19])

    def test_corrupt_binary_without_source_never_compiles_as_a_valid_import(self):
        """Malformed persisted interfaces must fail closed."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            provider = root / 'provider.vlnc'
            provider.write_text('public define answer(x:Int)->Int=>$x end\n', encoding='utf-8')
            CompilationCoordinator(root).build_module(
                provider, provider.with_suffix('.vbcm'), module_name='provider')
            provider.unlink()
            artifact = provider.with_suffix('.vbcm')
            artifact.write_bytes(artifact.read_bytes()[:9] + b'CORRUPT')
            analyser = Analyser(source_file=root / 'main.vlnc')
            try:
                typed = analyser.analyse(parse('import {provider.answer}\nanswer(1)\n'))
            except Exception as error:
                # Structured, explicit load rejection is also fail-closed. An
                # unchecked Python traceback is not counted as a clean diagnostic.
                self.assertTrue(any(key in type(error).__name__.lower()
                                    for key in ('module', 'bytecode', 'import', 'value')),
                                type(error).__name__)
            else:
                self.assertTrue(analyser.diagnostics)
                self.assertFalse(any('compiled successfully' in d.lower()
                                     for d in analyser.diagnostics))

    def test_private_import_is_not_silently_reexported_in_binary_facade(self):
        for layout in ('source', 'compiled'):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'provider.vlnc').write_text(
                    'public object Thing=>public $value:Int end\n', encoding='utf-8')
                (root / 'facade.vlnc').write_text('import {provider.Thing}\n', encoding='utf-8')
                if layout == 'compiled':
                    coordinator = CompilationCoordinator(root)
                    for name in ('provider', 'facade'):
                        path = root / f'{name}.vlnc'
                        coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
                    for name in ('provider', 'facade'):
                        (root / f'{name}.vlnc').unlink()
                analyser = Analyser(source_file=root / 'main.vlnc')
                analyser.analyse(parse('import {facade.Thing}\nThing(7)\n'))
                self.assertTrue(analyser.diagnostics)
                self.assertTrue(
                    any('not public' in message.lower() or
                        'no public component' in message.lower()
                        for message in analyser.diagnostics), analyser.diagnostics)
