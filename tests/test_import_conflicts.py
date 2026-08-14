import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse


class ImportConflictTests(unittest.TestCase):
    def analyse(self, root: Path, source: str) -> Analyser:
        analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
        analyser.analyse(parse(source))
        return analyser

    def write_hash_modules(self, root: Path) -> None:
        (root / "first.vlnc").write_text(
            "overload(String -> String)\n"
            "overload(Number -> Number)\n"
            "public define hash(value) => $value\n",
            encoding="utf-8",
        )
        (root / "second.vlnc").write_text(
            "overload(String -> String)\n"
            "overload(Boolean -> Boolean)\n"
            "public define hash(value) => $value\n",
            encoding="utf-8",
        )

    def test_distinct_imported_overload_signatures_coexist(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_hash_modules(root)
            analyser = self.analyse(
                root,
                "import { first.[hash(Number)], second.[hash(Boolean)] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])

    def test_identical_imported_overload_signatures_conflict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_hash_modules(root)
            analyser = self.analyse(root, "import { first.[hash], second.[hash] }\n")
            self.assertTrue(any("conflicting imported overload 'hash'" in d for d in analyser.diagnostics))

    def test_alias_resolves_overload_conflict(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_hash_modules(root)
            analyser = self.analyse(
                root,
                "import { first.[hash as firstHash], second.[hash as secondHash] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])

    def test_exclusion_is_applied_before_conflict_check(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_hash_modules(root)
            analyser = self.analyse(
                root,
                "import { first.[hash except [(String)]], second.[hash(String)] }\n",
            )
            self.assertEqual(analyser.diagnostics, [])

    def test_unrelated_remaining_conflict_after_exclusion_is_reported(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "first.vlnc").write_text(
                "overload(String -> String)\n"
                "overload(Number -> Number)\n"
                "public define hash(value) => $value\n",
                encoding="utf-8",
            )
            (root / "second.vlnc").write_text(
                "overload(String -> String)\n"
                "overload(Number -> Number)\n"
                "public define hash(value) => $value\n",
                encoding="utf-8",
            )
            analyser = self.analyse(
                root,
                "import { first.[hash except [(String)]], second.[hash except [(String)]] }\n",
            )
            self.assertTrue(any("conflicting imported overload 'hash'" in d for d in analyser.diagnostics))

    def test_non_overload_object_conflict_requires_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("first", "second"):
                (root / f"{name}.vlnc").write_text(
                    "public object Parser =>\n  $value: Number\n",
                    encoding="utf-8",
                )
            analyser = self.analyse(root, "import { first.[Parser], second.[Parser] }\n")
            self.assertTrue(any("conflicting imported symbol 'Parser'" in d for d in analyser.diagnostics))

            aliased = self.analyse(
                root,
                "import { first.[Parser as FirstParser], second.[Parser as SecondParser] }\n",
            )
            self.assertEqual(aliased.diagnostics, [])

    def test_namespace_imports_avoid_direct_conflicts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_hash_modules(root)
            analyser = self.analyse(root, "import { first, second }\n")
            self.assertEqual(analyser.diagnostics, [])

    def test_same_default_namespace_requires_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "first").mkdir()
            (root / "second").mkdir()
            (root / "first" / "common.vlnc").write_text("", encoding="utf-8")
            (root / "second" / "common.vlnc").write_text("", encoding="utf-8")

            analyser = self.analyse(root, "import { first.common, second.common }\n")
            self.assertTrue(any("conflicting imported namespace 'common'" in d for d in analyser.diagnostics))

            aliased = self.analyse(
                root,
                "import { first.common as firstCommon, second.common as secondCommon }\n",
            )
            self.assertEqual(aliased.diagnostics, [])


if __name__ == "__main__":
    unittest.main()
