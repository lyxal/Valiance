"""Cross-module identity: checked generic bodies and private destructor ownership.

A public alias is a source spelling, not a fresh type declaration. Tests run
independent expected results through four VM modes and multiple module layouts.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.analysis import Analyser
from valiance.asts import ImportPath
from valiance.modules_system.modules import ModuleLoader
from valiance.runtime.compiled_module import interface_hash
from valiance.vtypes.symbols import Symbol
from valiance.incremental.coordinator import CompilationCoordinator
from valiance.parsing import parse

import tests.test_multimodule_reconstructed_repairs as project_runner
from tools.multimodule_lifetime_concurrency_contracts import IMPORTED_DESTRUCTOR_REGRESSIONS


class AliasedNominalRepairs(unittest.TestCase):
    def test_deep_generic_reflection_preserves_imported_owner(self):
        modules = {
            "core": "public object[T] Box=>public $value:T end\n",
            "logic": (
                "import {core.Box}\n"
                "public define[T] reflect(x:Box[T])->T =>\n"
                ' $field=fields[Box[T]]["value"]\n'
                " read?($x,$field) ?!\nend\n"
            ),
            "gateway": "public import {core.Box as Renamed,logic.reflect}\n",
            "api": "public import {gateway.Renamed as Final,gateway.reflect}\n",
        }
        for layout in (
            "source", "compiled", frozenset({"core", "gateway"}),
            frozenset({"logic", "api"}),
        ):
            with self.subTest(layout=layout):
                actual = project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules, "import {api.[Final,reflect]}\nreflect(Final(9))\n", layout,
                )
                self.assertEqual(actual, [[9]] * 4)

    def test_private_destructor_survives_repeated_facade_renames_once(self):
        (_features, provider, _names, dependent, _dependent_names,
         _consumer, _values, _stdout) = IMPORTED_DESTRUCTOR_REGRESSIONS[
             "destructor_through_trait"
         ]
        modules = {
            "core": provider,
            "logic": dependent,
            "gateway": "public import {core.Marker,core.Item as Renamed,logic.consume}\n",
            "api": "public import {gateway.Marker,gateway.Renamed as Final,gateway.consume}\n",
            "deep": "public import {api.Marker,api.Final as Deep,api.consume}\n",
        }
        for layout in (
            "source", "compiled", frozenset({"core", "gateway"}),
            frozenset({"logic", "api"}),
        ):
            with self.subTest(layout=layout):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    actual = project_runner.ReconstructedMultiModuleRepairs.run_project(
                        modules,
                        "import {deep.[Marker,Deep,consume]}\nconsume(Deep(17))\n",
                        layout,
                    )
                self.assertEqual(actual, [[1]] * 4)
                self.assertEqual(stdout.getvalue(), "released 17\n" * 4)

    def test_distinct_provider_destructors_do_not_merge(self):
        modules = {
            "alpha": (
                "public object Item =>\n public $value:Int\n"
                ' define ~Item => "alpha ${$self.value}" println\nend\n'
            ),
            "beta": (
                "public object Item =>\n public $value:Int\n"
                ' define ~Item => "beta ${$self.value}" println\nend\n'
            ),
            "gateway": "public import {alpha.Item as Left,beta.Item as Right}\n",
        }
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    actual = project_runner.ReconstructedMultiModuleRepairs.run_project(
                        modules,
                        "import {gateway.[Left,Right]}\n"
                        "Left(4) pop\nRight(7) pop\n1\n", layout,
                    )
                self.assertEqual(actual, [[1]] * 4)
                self.assertEqual(stdout.getvalue(), "alpha 4\nbeta 7\n" * 4)
    def test_provider_nominal_identity_changes_semantic_interface_hash(self):
        """An origin-only dependency change must invalidate compiled consumers."""
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "core.vlnc").write_text(
                "public object[T] Box=>public $value:T end\n", encoding="utf-8",
            )
            (root / "logic.vlnc").write_text(
                "import {core.Box}\n"
                "public define[T] reflect(x:Box[T])->T=>$x.value end\n",
                encoding="utf-8",
            )
            exported = ModuleLoader().load(
                ImportPath(("logic",)), current_file=root / "main.vlnc",
            )
            declared = exported.definitions[0]
            self.assertEqual(
                declared.private_nominals,
                ((Symbol("Box"), Symbol("Box", ("core",))),),
            )
            changed = replace(
                exported, definitions=(replace(
                    declared,
                    private_nominals=((Symbol("Box"), Symbol("Box", ("other",))),),
                ),),
            )
            self.assertNotEqual(interface_hash(exported), interface_hash(changed))

    def test_reflection_specialization_rejects_other_provider_alias(self):
        """Equal structural fields do not make imported generic types equal."""
        modules = {
            "alpha": "public object[T] Box=>public $value:T end\n",
            "beta": "public object[T] Box=>public $value:T end\n",
            "logic": (
                "import {alpha.Box}\n"
                "public define[T] reflect(x:Box[T])->T =>\n"
                ' $field=fields[Box[T]]["value"]\n'
                " read?($x,$field) ?!\nend\n"
            ),
            "gateway": (
                "public import {alpha.Box as First,beta.Box as Second,logic.reflect}\n"
            ),
        }
        consumer = "import {gateway.[First,Second,reflect]}\nreflect(Second(7))\n"
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                for name, code in modules.items():
                    (root / f"{name}.vlnc").write_text(code, encoding="utf-8")
                if layout == "compiled":
                    coordinator = CompilationCoordinator(root)
                    for name in modules:
                        path = root / f"{name}.vlnc"
                        coordinator.build_module(
                            path, path.with_suffix(".vbcm"), module_name=name,
                        )
                        path.unlink()
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse(consumer))
                self.assertTrue(
                    any("no overloads" in diagnostic for diagnostic in analyser.diagnostics),
                    (layout, analyser.diagnostics),
                )


if __name__ == "__main__":
    unittest.main()
