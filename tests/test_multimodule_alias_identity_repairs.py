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

    def test_runtime_patterns_preserve_distinct_import_aliases(self):
        """Pattern evidence uses the provider's type, never an alias short name."""
        modules = {
            "alpha": "public object Item => public $value:Int end\n",
            "beta": "public object Item => public $value:Int end\n",
            "gateway": "public import {alpha.Item as Left,beta.Item as Right}\n",
            "api": "public import {gateway.Left as FinalLeft,gateway.Right as FinalRight}\n",
        }
        for facade, left, right in (
            ("gateway", "Left", "Right"),
            ("api", "FinalLeft", "FinalRight"),
        ):
            consumer = (
                f"import {{{facade}.[{left},{right}]}}\n"
                f"{right}(2) match =>\n"
                f" as item:{left} => 99\n"
                f" as item:{right} => $item.value\n"
                " _ => 0\n"
                "end\n"
                f"{left}(3) match =>\n"
                f" as item:{right} => 99\n"
                f" as item:{left} => $item.value\n"
                " _ => 0\n"
                "end\n"
            )
            for layout in ("source", "compiled", frozenset({"alpha", "gateway"})):
                with self.subTest(facade=facade, layout=layout):
                    actual = project_runner.ReconstructedMultiModuleRepairs.run_project(
                        modules, consumer, layout,
                    )
                    self.assertEqual(actual, [[2, 3]] * 4)

    def test_public_method_works_with_private_and_visible_provider_imports(self):
        """One method body is shared across consumer and provider runtime names."""
        modules = {
            "core": (
                "public object Item =>\n"
                " public $value:Int\n"
                " public define read -> Int => $self.value end\n"
                "end\n"
            ),
            "logic": (
                "import {core.Item}\n"
                "public define \\readInternal -> Int => Item(8) read end\n"
            ),
        }
        for layout in ("source", "compiled", frozenset({"core"})):
            with self.subTest(layout=layout):
                values = project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules,
                    "import {core.Item,logic.\\readInternal}\n"
                    "Item(4) read\n"
                    "\\readInternal\n",
                    layout,
                )
                self.assertEqual(values, [[4, 8]] * 4)

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


    def test_private_trait_dispatch_links_through_deep_facades(self):
        """A public callable brings its private receiver's method code with it."""
        modules = {
            "core": (
                "public trait Sized=>extend size->Int end\n"
                "object Box=>public $value:Int end\n"
                "object Box as Sized=>define size=>$self.value end\n"
                "public define measure(value:Int)->Int=>"
                "Box($value) as[Sized] | size end\n"
            ),
            "gateway": "public import {core.[measure as read]}\n",
            "api": "public import {gateway.[read as fetch]}\n",
        }
        source = "import {api.[fetch]}\nfetch(17)\n"
        for layout in (
            "source", "compiled", frozenset({"core", "api"}),
            frozenset({"gateway"}),
        ):
            with self.subTest(layout=layout):
                values = project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules, source, layout,
                )
                self.assertEqual(values, [[17]] * 4)

    def test_private_generic_trait_and_exception_function_survive_artifact_layouts(self):
        """Return types and trait dispatch retain their checked provider scope."""
        modules = {
            "core": (
                "public trait[T] Readable=>extend read->T end\n"
                "object[T] Box=>public $value:T end\n"
                "object[T] Box as Readable[T]=>define read=>$self.value end\n"
                "public define[T] build(value:T)->T=>"
                "Box($value) as[Readable[T]] | read end\n"
                "public define safe(value:Int)->Int =>\n"
                " try => Box($value) as[Readable[Int]] | read\n"
                " handle => -1 end ?!\n"
                "end\n"
            ),
            "gateway": "public import {core.[build as lift,safe]}\n",
            "api": "public import {gateway.[lift as forward,safe]}\n",
        }
        source = (
            "import {api.[forward,safe]}\n"
            'forward(31)\nforward("hello")\nsafe(32)\n'
        )
        for layout in (
            "source", "compiled", frozenset({"core", "gateway"}),
            frozenset({"api"}),
        ):
            with self.subTest(layout=layout):
                values = project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules, source, layout,
                )
                self.assertEqual(values, [[31, "hello", 32]] * 4)

    def test_distinct_private_trait_receivers_are_dispatched_by_provider(self):
        """Two providers' equal short names must never share a trait method."""
        provider = (
            "public trait Sized=>extend size->Int end\n"
            "object Box=>public $value:Int end\n"
            "object Box as Sized=>define size=>EXPRESSION end\n"
            "public define read(value:Int)->Int=>"
            "Box($value) as[Sized] | size end\n"
        )
        modules = {
            "alpha": provider.replace("EXPRESSION", "$self.value"),
            "beta": provider.replace("EXPRESSION", "$self.value+100"),
            "facade": "public import {alpha.[read as left],beta.[read as right]}\n",
        }
        source = "import {facade.[left,right]}\nleft(4)\nright(4)\n"
        for layout in (
            "source", "compiled", frozenset({"alpha", "facade"}),
            frozenset({"beta"}),
        ):
            with self.subTest(layout=layout):
                values = project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules, source, layout,
                )
                self.assertEqual(values, [[4, 104]] * 4)

    def test_private_variant_payload_can_cross_public_call_boundary(self):
        """Runtime dependencies do not make the payload object's name public."""
        modules = {
            "core": (
                "object Box=>public $value:Int end\n"
                "public variant Result =>\n"
                " Some => public $value:Box end\n"
                " Empty => end\n"
                "end\n"
                "public define wrap(value:Int)->Result=>Result.Some(Box($value)) end\n"
            ),
            "facade": "public import {core.[Result,wrap]}\n",
        }
        source = "import {facade.[Result,wrap]}\nwrap(12)\n"
        for layout in ("source", "compiled", frozenset({"core"})):
            with self.subTest(layout=layout):
                for (outer,) in project_runner.ReconstructedMultiModuleRepairs.run_project(
                    modules, source, layout,
                ):
                    self.assertEqual(outer.type_name, "Result.Some")
                    self.assertEqual(outer.fields["value"].type_name, "core.Box")
                    self.assertEqual(outer.fields["value"].fields["value"], 12)

        # Private runtime dependencies must never be accepted as public imports.
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "core.vlnc").write_text(modules["core"], encoding="utf-8")
                if layout == "compiled":
                    coordinator = CompilationCoordinator(root)
                    coordinator.build_module(
                        root / "core.vlnc", root / "core.vbcm", module_name="core",
                    )
                    (root / "core.vlnc").unlink()
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse("import {core.Box}\n"))
                self.assertTrue(analyser.diagnostics)
                self.assertIn("no public component", "\n".join(analyser.diagnostics))


if __name__ == "__main__":
    unittest.main()
