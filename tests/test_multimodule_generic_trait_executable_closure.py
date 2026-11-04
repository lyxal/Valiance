"""Cross-module generic trait dispatch and provider-private executable closure.

All checks use independently specified VM outputs in optimized/unoptimized and
serialized/in-memory runs. No source-level import grants private dependencies.
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.multimodule_fuzzing import execute_case, project_case
from valiance.analysis import Analyser
from valiance.parsing import parse


_GENERIC_PROVIDER = (
    "public trait[T] Source => extend read -> T end\n"
    "public object[T] Box => public $value:T end\n"
    "object[T] Box as Source[T] => define read -> T => $self.value end\n"
)

_PRIVATE_BOX = (
    "object Box =>\n"
    "  public $value:Int\n"
    "  define read -> Int => $self.value end\n"
    "end\n"
)


class MultiModuleGenericTraitExecutableClosureTests(unittest.TestCase):
    def test_generic_trait_dispatch_across_all_graphs_and_artifact_modes(self):
        for graph in ("direct", "facade", "deep_facade", "diamond", "nested", "alias"):
            for source_free in (False, True):
                with self.subTest(graph=graph, source_free=source_free):
                    self.assertEqual(execute_case(
                        project_case("generic_trait", graph, 711),
                        source_free=source_free,
                    ), 4)

    def test_two_providers_with_generic_traits_remain_distinct(self):
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs
        modules = {
            "alpha": _GENERIC_PROVIDER,
            "beta": _GENERIC_PROVIDER,
            "api": (
                "public import {alpha.[Box as AlphaBox,Source as AlphaSource],"
                "beta.[Box as BetaBox,Source as BetaSource]}\n"
            ),
        }
        consumer = (
            "import {api.[AlphaBox,AlphaSource,BetaBox,BetaSource]}\n"
            "AlphaBox(7) as[AlphaSource[Int]] | read\n"
            "BetaBox(9) as[BetaSource[Int]] | read\n"
        )
        for layout in ("source", "compiled", frozenset({"alpha"}),
                       frozenset({"beta"}), frozenset({"api"})):
            with self.subTest(layout=layout):
                self.assertEqual(
                    ReconstructedMultiModuleRepairs.run_project(modules, consumer, layout),
                    [[7, 9]] * 4,
                )
        # Identical short names and generic arguments must never manufacture
        # an implementation witness for the wrong nominal provider.
        for expression, diagnostic in (
            ("AlphaBox(7) as[BetaSource[Int]] | read", "cannot safely cast"),
            ("AlphaBox(7) as[AlphaSource[String]] | read", "cannot safely cast"),
        ):
            with self.subTest(expression=expression):
                with TemporaryDirectory() as temp:
                    root = Path(temp)
                    for name, source in modules.items():
                        (root / f"{name}.vlnc").write_text(source, encoding="utf-8")
                    analyser = Analyser(source_file=root / "main.vlnc")
                    analyser.analyse(parse(
                        "import {api.[AlphaBox,AlphaSource,BetaBox,BetaSource]}\n"
                        + expression
                    ))
                    self.assertIn(diagnostic, str(analyser.diagnostics))

    def test_unselected_private_method_links_through_facades_and_binaries(self):
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs
        modules = {
            "core": _PRIVATE_BOX + (
                "public define fetch(n:Int)->Int =>\n"
                "  Box($n) read\n"
                "end\n"
            ),
            "gateway": "public import {core.fetch}\n",
            "api": "public import {gateway.fetch}\n",
        }
        for layout in ("source", "compiled", frozenset({"core"}),
                       frozenset({"gateway"}), frozenset({"core", "api"})):
            with self.subTest(layout=layout):
                self.assertEqual(ReconstructedMultiModuleRepairs.run_project(
                    modules, "import {api.fetch}\nfetch(9)", layout,
                ), [[9]] * 4)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for name, source in modules.items():
                (root / f"{name}.vlnc").write_text(source, encoding="utf-8")
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import {api.fetch}\nBox(9)"))
            self.assertIn("unknown element 'Box'", str(analyser.diagnostics))

    def test_private_method_inside_two_stage_returned_callable(self):
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs
        modules = {
            "core": _PRIVATE_BOX + (
                "public define factory(n:Int)->Function[->Function[->Int]] =>\n"
                "  fn -> Function[->Int] =>\n"
                "    fn -> Int => Box($n) read end\n"
                "  end\n"
                "end\n"
            ),
            "gateway": "public import {core.factory}\n",
            "api": "public import {gateway.factory}\n",
        }
        consumer = "import {api.factory}\nfactory(9) call | call"
        for layout in ("source", "compiled", frozenset({"core"}),
                       frozenset({"gateway"}), frozenset({"api"}),
                       frozenset({"core", "api"})):
            with self.subTest(layout=layout):
                self.assertEqual(ReconstructedMultiModuleRepairs.run_project(
                    modules, consumer, layout,
                ), [[9]] * 4)


    def test_diamond_reexports_same_generic_nominal_with_split_trait_evidence(self):
        """One base declaration may be re-exported by multiple facades.

        The two facades also carry *different* bounded trait implementations.
        Deduplicating the nominal must not drop either implementation, and
        importing it twice must not install duplicate constructors or methods.
        """
        from tests.test_multimodule_reconstructed_repairs import ReconstructedMultiModuleRepairs
        modules = {
            "base": (
                "public trait[T] Source => extend read -> T end\n"
                "public trait[T] Target => extend label -> String end\n"
                "public object[A,B] Pair =>\n$key:A\n$value:B\nend\n"
                "object[A,B] Pair as Source[B] => "
                "define read -> B => $self.value end\n"
            ),
            "numeric": (
                "import {base.[Source,Target]}\n"
                "trait[U:Number] Source as Target[U] => "
                'define label -> String => "numeric" end\n'
            ),
            "textual": (
                "import {base.[Source,Target]}\n"
                "trait[U:String] Source as Target[U] => "
                'define label -> String => "textual" end\n'
            ),
            "left": (
                "public import {base.[Pair,Source,Target],"
                "numeric.trait Source as Target}\n"
            ),
            "right": (
                "public import {base.[Pair,Source,Target],"
                "textual.trait Source as Target}\n"
            ),
            "api": "public import {left.[Pair,Source,Target],right.[Pair,Source,Target]}\n",
        }
        consumer = (
            "import {api.[Pair,Source,Target]}\n"
            'Pair("key",3) as[Target[Int]] | label\n'
            'Pair(1,"hello") as[Target[String]] | label\n'
        )
        layouts = (
            "source", "compiled", frozenset({"base", "numeric", "textual"}),
            frozenset({"left", "api"}),
        )
        for layout in layouts:
            with self.subTest(layout=layout):
                self.assertEqual(
                    ReconstructedMultiModuleRepairs.run_project(modules, consumer, layout),
                    [["numeric", "textual"]] * 4,
                )

    def test_diamond_reexports_different_nominals_still_conflict(self):
        """A shared display name is not evidence of shared provider identity."""
        modules = {
            "alpha": "public object[T] Pair => public $item:T end\n",
            "beta": "public object[T] Pair => public $item:T end\n",
            "left": "public import {alpha.Pair}\n",
            "right": "public import {beta.Pair}\n",
            "api": "public import {left.Pair,right.Pair}\n",
        }
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for name, source in modules.items():
                (root / f"{name}.vlnc").write_text(source, encoding="utf-8")
            analyser = Analyser(source_file=root / "main.vlnc")
            analyser.analyse(parse("import {api.Pair}\n"))
            self.assertIn("conflicting imported symbol 'Pair'", str(analyser.diagnostics))


if __name__ == "__main__":
    unittest.main()
