"""Trait dependency edits, fresh/reused analysis agreement and session recovery."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.analysis import Analyser
from valiance.incremental import CompilationDatabase
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.vtypes.symbols import Symbol

from trait_plan_support import SIZED, SOURCE
from test_trait_plan_imports import IMPORT_BASE, IMPORT_PLAIN, PROVIDERS


BASE = 'public trait Reader => extend read -> Int end\n'
PROVIDER = '''
import { base.Reader }
public object Box => $value: Int end
object Box as Reader => define read -> Int => $self.value end
'''
MAIN = 'import { provider.Box }\nBox(7) read\n'


class IncrementalPlanTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / 'base.vlnc'
        self.provider = self.root / 'provider.vlnc'
        self.main = self.root / 'main.vlnc'
        for path, source in ((self.base, BASE), (self.provider, PROVIDER), (self.main, MAIN)):
            path.write_text(source, encoding='utf-8')
        self.db = CompilationDatabase(self.root)

    def execute(self, expected):
        executable = self.db.compile_current(self.main)
        self.assertEqual(executable.analysis.analyser.diagnostics, [])
        self.assertEqual(run(executable.program), expected)
        self.assertEqual(run(loads(dumps(executable.program))), expected)
        return executable

    def test_IN_001(self):
        source = '''
trait Required => extend read -> Int end
object Bad => $id: Int end
object Bad as Required => end
object Good => $id: Int end
object Good as Required => define read => $self.id end
'''
        analyser = Analyser()
        analyser.analyse(parse(source))
        self.assertTrue(any('Bad' in message and 'read' in message for message in analyser.diagnostics))
        self.assertFalse(any('Good' in message for message in analyser.diagnostics), analyser.diagnostics)
        self.assertIsNotNone(analyser.env.lookup_object(Symbol('Good')))
        self.assertTrue(analyser.env.context.implements(Symbol('Good'), Symbol('Required')))

    def test_IN_002(self):
        invalid = Analyser()
        invalid.analyse(parse('trait Required => extend read -> Int end\n'
                              'object Bad => $id: Int end\nobject Bad as Required => end'))
        self.assertTrue(invalid.diagnostics)
        valid = Analyser(source_file=self.main)
        typed = valid.analyse(parse(SIZED + 'Item(7) size'))
        self.assertEqual(valid.diagnostics, [])
        self.assertEqual(run(compile_program(typed)), [7])

    def test_IN_003(self):
        identities = []
        for _ in range(2):
            analyser = Analyser(source_file=self.main)
            typed = analyser.analyse(parse(SIZED + 'Item(7) size'))
            self.assertEqual(analyser.diagnostics, [])
            identities.append(typed[-1].implementation_id)
            self.assertEqual(run(compile_program(typed)), [7])
        self.assertIsNotNone(identities[0])
        self.assertEqual(identities[0], identities[1])

    def test_IN_005(self):
        self.execute([7])
        self.db.open_document(self.base, BASE.replace('Int', 'String'))
        self.assertTrue(self.db.analyse(self.main).analyser.diagnostics)
        with self.assertRaises(RuntimeError):
            self.db.compile_current(self.main)
        self.db.replace_document(self.base, BASE)
        self.execute([7])

    def test_IN_006(self):
        first = self.execute([7])
        self.db.open_document(self.provider, PROVIDER.replace('$self.value end', '$self.value + 1 end'))
        second = self.execute([8])
        self.assertIsNot(first.analysis, second.analysis)

    def test_IN_007(self):
        self.execute([7])
        self.db.open_document(self.provider, PROVIDER.split('object Box as Reader')[0])
        self.assertTrue(self.db.analyse(self.main).analyser.diagnostics)
        with self.assertRaises(RuntimeError):
            self.db.compile_current(self.main)
        self.db.replace_document(self.provider, PROVIDER)
        self.execute([7])

    def test_IN_008(self):
        for name, source in PROVIDERS.items():
            (self.root / name).write_text(source, encoding='utf-8')
        self.main.write_text(IMPORT_BASE + IMPORT_PLAIN + 'Item(1) as[Target] | label', encoding='utf-8')
        self.db = CompilationDatabase(self.root)
        self.execute(['plain'])
        replacement = IMPORT_BASE + 'import { rich.trait Origin as Target }\nItem(1) as[Target] | label'
        self.db.open_document(self.main, replacement)
        self.execute(['rich'])

    def test_IN_009(self):
        self.execute([7])
        self.db.open_document(self.base, BASE.replace('Int', 'String'))
        self.assertTrue(self.db.analyse(self.main).analyser.diagnostics)
        replacement = PROVIDER.replace('read -> Int => $self.value', 'read -> String => "${$self.value}"')
        self.db.open_document(self.provider, replacement)
        self.execute(['7'])

    def test_IN_010(self):
        self.db.open_document(self.provider, PROVIDER + '\nprivate define unused(n: Int) -> Int => $n end')
        self.execute([7])
        self.db.replace_document(self.provider, PROVIDER + '\nprivate define renamed(n: Int) -> Int => $n end')
        self.execute([7])

    def test_IN_011(self):
        for traits in ('trait One => end\ntrait Two => end\n', 'trait Two => end\ntrait One => end\n'):
            self.db.open_document(self.provider, PROVIDER + traits)
            self.execute([7])

    def test_IN_014(self):
        provider = SOURCE.replace('trait[T] Source', 'public trait[T] Source')
        provider = provider.replace('object[T] Box =>', 'public object[T] Box =>')
        provider += '\npublic define[T] version(value: Source[T]) -> Int => 1 end\n'
        self.provider.write_text(provider, encoding='utf-8')
        self.main.write_text('import { provider.[Box, version] }\nBox(7) version', encoding='utf-8')
        self.db = CompilationDatabase(self.root)
        self.execute([1])
        updated = provider.replace('-> Int => 1 end', '-> Int => 2 end')
        self.db.open_document(self.provider, updated)
        self.execute([2])
        clean = Analyser(module_loader=ModuleLoader(source_overrides={self.provider: updated}), source_file=self.main)
        typed = clean.analyse(parse(self.main.read_text(encoding='utf-8')))
        self.assertEqual(clean.diagnostics, [])
        self.assertEqual(run(compile_program(typed)), [2])
