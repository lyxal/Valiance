import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.main import main_vln
from valiance.modules_system.modules import ModuleLoader
from valiance.modules_system.packages import load_manifest
from valiance.parsing import parse
from valiance.runtime import BytecodeFormatError, loads_module


class CompiledModuleTests(unittest.TestCase):
    def test_compile_module_and_import_without_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "mathlib.vlnc"
            library.write_text(
                "public define triple(n: Number) -> Number => $n * 3\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "mathlib.vbcm"),
                ])
            self.assertEqual(result, 0)
            library.unlink()

            main = root / "main.vlnc"
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            typed = analyser.analyse(parse("import { mathlib }\n5 mathlib.triple"))

            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_source_takes_precedence_over_compiled_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "thing.vlnc"
            source.write_text(
                "public define old(n: Number) -> Number => $n\n", encoding="utf-8"
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(source)]), 0)
            source.write_text(
                "public define fresh(n: Number) -> Number => $n\n", encoding="utf-8"
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import { thing.[fresh] }"))
            self.assertEqual(analyser.diagnostics, [])

    def test_manifest_build_target_and_named_build(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "library.vlnc").write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            (root / "valiance.toml").write_text(
                "[project]\nname = \"demo\"\nversion = \"0.1.0\"\n\n"
                "[entries]\nlib = \"src/library.vlnc\"\n\n"
                "[build.library]\nkind = \"module\"\nentry = \"lib\"\n"
                "output = \"dist/library.vbcm\"\noptimize = true\n",
                encoding="utf-8",
            )
            manifest = load_manifest(root)
            self.assertEqual(manifest.builds["library"].kind, "module")
            previous = Path.cwd()
            try:
                os.chdir(root)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = main_vln(["build", "library"])
            finally:
                os.chdir(previous)
            self.assertEqual(result, 0)
            artifact = root / "dist" / "library.vbcm"
            self.assertTrue(artifact.is_file())
            self.assertEqual(loads_module(artifact.read_bytes()).module_name, "library")

    def test_vbcm_is_not_plain_vbc(self):
        with self.assertRaises(BytecodeFormatError):
            loads_module(b"VLNCBC\\x16")


if __name__ == "__main__":
    unittest.main()
