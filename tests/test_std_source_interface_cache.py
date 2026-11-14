"""Content-validated process caching for source-only standard-library interfaces."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from valiance.analysis import Analyser
from valiance.modules_system.modules import (
    ModuleLoader,
    _PROCESS_SOURCE_INTERFACE_CACHE,
)
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run


def _path(name: str):
    return parse(f"import {{std.{name}}}")[0].specs[0].path


class SourceStdlibCacheTests(TestCase):
    def test_repeated_std_web_source_import_reuses_checked_interface(self):
        with patch.dict(_PROCESS_SOURCE_INTERFACE_CACHE, {}, clear=True):
            first = ModuleLoader()
            exported = first.load(_path("web"))
            second = ModuleLoader()
            reused = second.load(_path("web"))
            self.assertIs(exported, reused)
            self.assertEqual(
                second.interface_hash_for(reused), first.interface_hash_for(exported)
            )
            self.assertEqual(
                second.dependency_hashes_for(second.resolve(_path("web"))),
                first.dependency_hashes_for(first.resolve(_path("web"))),
            )

            analyser = Analyser(module_loader=second)
            typed = analyser.analyse(parse('import {std.web}\nweb.Response(body="ok")'))
            self.assertEqual(analyser.diagnostics, [])
            response, = run(loads(dumps(compile_program(typed))))
            self.assertEqual(response.type_name, "web.Response")
            self.assertEqual(response.fields["body"], "ok")

    def test_changed_source_invalidates_cache_across_new_loaders(self):
        with TemporaryDirectory() as tmp, patch.dict(
            _PROCESS_SOURCE_INTERFACE_CACHE, {}, clear=True
        ):
            root = Path(tmp)
            source = root / "mini.vlnc"
            source.write_text('public define value(n: Int) -> Int => $n + 1\n', encoding='utf-8')
            first = ModuleLoader(std_root=root).load(_path('mini'))
            self.assertIs(first, ModuleLoader(std_root=root).load(_path('mini')))
            source.write_text('public define value(n: Int) -> Int => $n + 2\n', encoding='utf-8')
            second = ModuleLoader(std_root=root).load(_path('mini'))
            self.assertIsNot(first, second)
            self.assertIs(second, ModuleLoader(std_root=root).load(_path('mini')))

    def test_transitive_std_source_edits_invalidate_dependent_interface(self):
        with TemporaryDirectory() as tmp, patch.dict(
            _PROCESS_SOURCE_INTERFACE_CACHE, {}, clear=True
        ):
            root = Path(tmp)
            (root / "base.vlnc").write_text(
                'public define value(n: Int) -> Int => $n + 1\n', encoding='utf-8'
            )
            (root / "top.vlnc").write_text(
                'import {std.base}\npublic define answer(n: Int) -> Int => $n + 42\n',
                encoding='utf-8',
            )
            first = ModuleLoader(std_root=root).load(_path('top'))
            self.assertIs(first, ModuleLoader(std_root=root).load(_path('top')))
            (root / "base.vlnc").write_text(
                'public define value(n: Int) -> Int => $n + 2\n', encoding='utf-8'
            )
            second = ModuleLoader(std_root=root).load(_path('top'))
            self.assertIsNot(first, second)

            # Reconstructed dependency bookkeeping must still expose exact
            # implementation digests for incremental compilation.
            current = ModuleLoader(std_root=root)
            current.load(_path('top'))
            self.assertEqual(len(current.dependency_hashes_for(root / 'top.vlnc')), 1)
            self.assertEqual(len(current.dependency_implementation_hashes_for(root / 'top.vlnc')), 1)

    def test_source_overrides_bypass_process_cache(self):
        with TemporaryDirectory() as tmp, patch.dict(
            _PROCESS_SOURCE_INTERFACE_CACHE, {}, clear=True
        ):
            root = Path(tmp)
            source = root / 'mini.vlnc'
            source.write_text('public define value(n: Int) -> Int => $n + 1\n', encoding='utf-8')
            first = ModuleLoader(std_root=root).load(_path('mini'))
            overridden = ModuleLoader(
                std_root=root,
                source_overrides={source.resolve(): 'public define value(n: Int) -> Int => $n + 2\n'},
            ).load(_path('mini'))
            self.assertIsNot(first, overridden)
            self.assertIs(first, ModuleLoader(std_root=root).load(_path('mini')))
