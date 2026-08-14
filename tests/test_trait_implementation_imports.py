import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.vtypes.symbols import Symbol


class TraitImplementationImportTests(unittest.TestCase):
    def analyse(self, root: Path, source: str) -> Analyser:
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        analyser.analyse(parse(source))
        return analyser

    def write_shapes(self, root: Path, name: str = "shapes") -> None:
        (root / f"{name}.vlnc").write_text(
            "public trait Shape => end\n"
            "public object Rectangle =>\n  $width: Number\nend\n"
            "object Rectangle as Shape => end\n",
            encoding="utf-8",
        )

    def test_explicit_import_installs_trait_implementation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(
                root,
                "import { shapes.[Rectangle, Shape, object Rectangle as Shape] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])
            self.assertIn(
                Symbol("Shape"),
                analyser.env.context.trait_impls.get(Symbol("Rectangle"), set()),
            )

    def test_missing_trait_implementation_is_an_error(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(
                root,
                "import { shapes.[object Rectangle as Missing] }\n",
            )
            self.assertTrue(any("defines no implementation" in d for d in analyser.diagnostics))

    def test_same_trait_implementation_from_two_modules_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root, "first")
            self.write_shapes(root, "second")
            analyser = self.analyse(
                root,
                "import { first.[object Rectangle as Shape], "
                "second.[object Rectangle as Shape] }\n",
            )
            self.assertTrue(any("conflicting imported trait implementation" in d for d in analyser.diagnostics))

    def test_namespace_import_does_not_install_trait_implementation(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_shapes(root)
            analyser = self.analyse(root, "import { shapes }\n")
            self.assertEqual(analyser.diagnostics, [])
            self.assertNotIn(
                Symbol("Shape"),
                analyser.env.context.trait_impls.get(Symbol("Rectangle"), set()),
            )


if __name__ == "__main__":
    unittest.main()
