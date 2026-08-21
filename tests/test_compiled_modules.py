import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.main import main_vln
from valiance.asts import ImportPath
from valiance.modules_system.modules import ModuleLoadError, ModuleLoader
from valiance.modules_system.packages import load_manifest
from valiance.parsing import parse
from valiance.runtime import BytecodeFormatError, loads_module


class CompiledModuleTests(unittest.TestCase):
    def test_missing_module_reports_matching_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "moduleA").mkdir()

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("moduleA",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("module 'moduleA' was not found", message)
            self.assertIn(f"found directory: {root / 'moduleA'}", message)
            self.assertIn("directories cannot be imported", message)
            self.assertIn("import a .vlnc file from the directory", message)
            self.assertNotIn("make sure the source file exists", message)

    def test_missing_module_suggests_renaming_similar_unimportable_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "json-parser.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("jsonParser",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("json-parser.vlnc", message)
            self.assertIn("not a valid Valiance identifier", message)
            self.assertIn("rename 'json-parser.vlnc' to 'jsonParser.vlnc'", message)

    def test_missing_module_suggests_similar_importable_module(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "geometry2.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("geometry",)),
                    current_file=root / "main.vlnc",
                )

            message = str(raised.exception)
            self.assertIn("module 'geometry' was not found", message)
            self.assertIn("looked for:", message)
            self.assertIn("did you mean 'geometry2'?", message)
            self.assertNotIn("[Errno 2]", message)

    def test_missing_module_does_not_suggest_unrelated_unimportable_file(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "totally-unrelated.vlnc").write_text("", encoding="utf-8")

            with self.assertRaises(ModuleLoadError) as raised:
                ModuleLoader().load(
                    ImportPath(("jsonParser",)),
                    current_file=root / "main.vlnc",
                )

            self.assertNotIn("help: rename", str(raised.exception))

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

    def test_valid_analysed_interface_skips_reanalysis(self):
        from unittest.mock import patch

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "cached.vlnc"
            library.write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            main = root / "main.vlnc"
            with patch("valiance.analysis.Analyser.analyse", side_effect=AssertionError("reanalyzed")):
                exports = ModuleLoader().load(parse("import { cached }")[0].specs[0].path, current_file=main)
            self.assertEqual(exports.module_name, "cached")

    def test_interface_hash_is_canonical_and_detects_tampering(self):
        from valiance.runtime import dumps_module

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "hashes.vlnc"
            library.write_text(
                "public define identity(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            data = library.with_suffix(".vbcm").read_bytes()
            module = loads_module(data)
            self.assertTrue(module.interface_hash)
            self.assertEqual(dumps_module(module), data)
            import struct
            damaged = bytearray(data)
            header = len(b"VLNCBM\x02")
            metadata_size, interface_size, _ = struct.unpack(">III", data[header:header + 12])
            self.assertGreater(interface_size, 0)
            damaged[header + 12 + metadata_size] ^= 1
            with self.assertRaises(BytecodeFormatError):
                loads_module(bytes(damaged))

    def test_source_hash_invalidates_persisted_interface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "invalidate.vlnc"
            library.write_text(
                "public define old(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main_vln(["compile-module", "--file", str(library)]), 0)
            library.write_text(
                "public define fresh(n: Number) -> Number => $n\n",
                encoding="utf-8",
            )
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import { invalidate.[fresh] }"))
            self.assertEqual(analyser.diagnostics, [])

    def test_vbcm_is_not_plain_vbc(self):
        with self.assertRaises(BytecodeFormatError):
            loads_module(b"VLNCBC\\x16")


    def test_compiled_module_exports_mutually_recursive_definitions(self):
        """Preserve declaration prescanning through module compilation and import."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "parity.vlnc"
            library.write_text(
                "public define even(n: Int) -> #boolean Int => "
                "if ($n == 0) => true else => odd($n - 1) end end\n"
                "public define odd(n: Int) -> #boolean Int => "
                "if ($n == 0) => false else => even($n - 1) end end\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "parity.vbcm"),
                ])
            self.assertEqual(result, 0)
            library.unlink()

            main = root / "main.vlnc"
            analyser = Analyser(module_loader=ModuleLoader(), source_file=main)
            typed = analyser.analyse(parse("import { parity }\nparity.even(8)"))
            self.assertEqual(analyser.diagnostics, [])
            self.assertIsNotNone(typed[-1].typ)


if __name__ == "__main__":
    unittest.main()


class CompiledGenericTraitImplementationTests(unittest.TestCase):
    def _compile_without_source(self, root: Path, source: str) -> None:
        library = root / "generic.vlnc"
        library.write_text(source, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main_vln([
                    "compile-module", "--file", str(library),
                    "--output", str(root / "generic.vbcm"),
                ]),
                0,
            )
        library.unlink()

    def test_generic_trait_implication_round_trips_without_source(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                "Box(1) as[Iterable[Int]]\n"
            ))
            self.assertEqual(analyser.diagnostics, [])

    def test_compiled_generic_trait_implication_preserves_correlation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                'Box("value") as[Iterable[Int]]\n'
            ))
            self.assertTrue(any(
                "cannot safely cast Box[String] to Iterable[Int]" in diagnostic
                for diagnostic in analyser.diagnostics
            ))

    def test_compiled_generic_trait_implication_preserves_constraints(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._compile_without_source(
                root,
                "public trait[T] Producer => end\n"
                "public trait[T] Iterable => end\n"
                "public object[T] Box => $value: T end\n"
                "object[T] Box as Producer[T] => end\n"
                "trait[T: Number] Producer as Iterable[T] => end\n",
            )
            analyser = Analyser(
                module_loader=ModuleLoader(), source_file=root / "main.vlnc"
            )
            analyser.analyse(parse(
                "import { generic.[Box, Producer, Iterable] }\n"
                'Box("value") as[Iterable[String]]\n'
            ))
            self.assertTrue(any(
                "cannot safely cast Box[String] to Iterable[String]" in diagnostic
                for diagnostic in analyser.diagnostics
            ))
