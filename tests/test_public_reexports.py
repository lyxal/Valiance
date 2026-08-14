import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.asts import ImportPath
from valiance.main import main_vln
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.vtypes.symbols import Symbol


class PublicReExportTests(unittest.TestCase):
    def analyse(self, root: Path, source: str):
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return typed

    def test_selective_public_import_reexports_function(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_selective_public_import_preserves_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double as twice] }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade.[twice] }\n4 twice")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_public_namespace_import_reexports_qualified_surface(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal as api }\n",
                encoding="utf-8",
            )

            typed = self.analyse(root, "import { facade }\n4 facade.api.double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_private_import_is_not_reexported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "import { internal.[double] }\n",
                encoding="utf-8",
            )
            analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            analyser.analyse(parse("import { facade.[double] }"))
            self.assertTrue(any("no public component 'double'" in d for d in analyser.diagnostics))

    def test_public_import_reexports_object(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public object Box =>\n  $value: Number\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[Box] }\n",
                encoding="utf-8",
            )

            exports = ModuleLoader().load(
                ImportPath(("facade",)),
                current_file=root / "main.vlnc",
            )
            self.assertIn(Symbol("Box"), {obj.name for obj in exports.public_objects()})

    def test_public_import_reexports_tag_and_overlay(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tags.vlnc").write_text(
                "tag #sorted as computed\n"
                "#sorted: + =>\n"
                "  (#sorted Number, Number) -> #sorted Number\n"
                "end\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { tags.#sorted }\n",
                encoding="utf-8",
            )

            loader = ModuleLoader()
            exports = loader.load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertIn(Symbol("sorted"), {tag.name for tag in exports.tags})
            self.assertTrue(any(overlay.tag == Symbol("sorted") for overlay in exports.overlays))
            self.analyse(root, "import { facade.#sorted }\n1 #sorted | 2 +")

    def test_public_import_inside_function_does_not_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "define use(n: Number) -> Number =>\n"
                "  public import { internal.[double] }\n"
                "  $n double\n"
                "end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertNotIn(Symbol("double"), {d.name for d in exports.public_definitions()})

    def test_public_import_in_conditional_is_not_reexported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "if true =>\n"
                "  public import { internal.[double] }\n"
                "end\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            self.assertNotIn(Symbol("double"), {d.name for d in exports.public_definitions()})

    def test_repeated_public_import_is_deduplicated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "facade.vlnc").write_text(
                "public import { internal.[double] }\n"
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )
            exports = ModuleLoader().load(ImportPath(("facade",)), current_file=root / "main.vlnc")
            matches = [d for d in exports.public_definitions() if d.name == Symbol("double")]
            self.assertEqual(len(matches), 1)

    def test_two_layer_selective_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "internal.vlnc").write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            (root / "middle.vlnc").write_text(
                "public import { internal.[double] }\n", encoding="utf-8"
            )
            (root / "facade.vlnc").write_text(
                "public import { middle.[double] }\n", encoding="utf-8"
            )
            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")

    def test_compiled_facade_preserves_public_reexport(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal = root / "internal.vlnc"
            facade = root / "facade.vlnc"
            internal.write_text(
                "public define double(n: Number) -> Number => $n * 2\n",
                encoding="utf-8",
            )
            facade.write_text(
                "public import { internal.[double] }\n",
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                result = main_vln([
                    "compile-module", "--file", str(facade),
                    "--output", str(root / "facade.vbcm"),
                ])
            self.assertEqual(result, 0)
            facade.unlink()
            internal.unlink()

            typed = self.analyse(root, "import { facade.[double] }\n4 double")
            self.assertEqual(str(typed[-1].typ.name), "Number")


if __name__ == "__main__":
    unittest.main()
