"""Connected cross-feature semantics across real module/source-artifact graphs.

Keep these in normal unittest discovery. The full 330-case product and
negative mutation expansion are deterministic scheduled campaigns rather than
blocking PR test execution with a redundant all-mode matrix.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.multimodule_fuzzing import write_project
from tools.multimodule_interactions import (
    EXECUTION_MODES, GRAPHS, INTERACTIONS, INVALID_INTERACTIONS, LAYOUTS,
    candidate_at, check_invalid_interaction, combinations, coverage_inventory,
    execute_interaction, interaction_case, prepare_layout,
)
from valiance.incremental import BuildDisposition, CompilationCoordinator
from valiance.runtime import loads, run
from valiance.runtime.compiled_module import load_module_file


class MultiModuleInteractionTests(unittest.TestCase):
    def test_each_connected_interaction_across_every_source_graph(self):
        for family in INTERACTIONS:
            for graph in GRAPHS:
                with self.subTest(family=family, graph=graph):
                    self.assertEqual(execute_interaction(
                        interaction_case(family, graph, 709), "source"), 4)

    def test_each_interaction_through_compiled_nested_provider(self):
        # Targets the recursive coordinator dependency-name invariant too.
        for family in INTERACTIONS:
            for layout in ("compiled", "dependent_binary"):
                with self.subTest(family=family, layout=layout):
                    self.assertEqual(execute_interaction(
                        interaction_case(family, "nested", 211), layout), 4)

    def test_mixed_graphs_and_tagged_facades(self):
        for family in ("constructed_tag_generic", "generic_trait_variant",
                       "overload_generic_object", "object_tag_result"):
            for graph in ("diamond", "alias"):
                for layout in ("core_binary", "alternating_binary"):
                    with self.subTest(family=family, graph=graph, layout=layout):
                        self.assertEqual(execute_interaction(
                            interaction_case(family, graph, 181), layout), 4)

    def test_semantic_negative_mutations_preserved_after_compilation(self):
        for family in INVALID_INTERACTIONS:
            for layout in ("source", "compiled"):
                with self.subTest(family=family, layout=layout):
                    self.assertTrue(check_invalid_interaction(
                        family, "facade", 512, layout))

    def test_reproducible_complete_finite_matrix_without_duplicates(self):
        all_cases = combinations()
        self.assertEqual(len(all_cases), len(set(all_cases)))
        self.assertEqual(len(all_cases), len(INTERACTIONS) * len(GRAPHS) * len(LAYOUTS))
        sampled = [candidate_at(449, i) for i in range(len(all_cases))]
        self.assertEqual(
            {(case.family, case.graph, layout) for case, layout in sampled},
            set(all_cases),
        )
        self.assertEqual(sampled, [candidate_at(449, i) for i in range(len(all_cases))])
        self.assertNotEqual(sampled, [candidate_at(450, i) for i in range(len(all_cases))])
        self.assertEqual(coverage_inventory()["possible_execution_checks"],
                         len(all_cases) * len(EXECUTION_MODES))
        self.assertTrue(coverage_inventory()["uncovered_feature_pairs"])

    def test_overloaded_generic_specialization_change_invalidates_facades(self):
        case = interaction_case("overload_generic_object", "deep_facade", 41)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, case)
            coordinator = CompilationCoordinator(root)

            def compile_all():
                for name in case.build_order:
                    path = root.joinpath(*name.split(".")).with_suffix(".vlnc")
                    coordinator.build_module(path, path.with_suffix(".vbcm"),
                                             module_name=name)

            compile_all()
            output = root / "main.vbc"
            coordinator.build_executable(root / "main.vlnc", output,
                                         target_identity="main")
            self.assertEqual(run(loads(output.read_bytes())), list(case.expected))
            self.assertEqual(coordinator.build_executable(
                root / "main.vlnc", output, target_identity="main").disposition,
                BuildDisposition.REUSED)
            logic = root / "logic.vlnc"
            old = logic.read_text(encoding="utf-8")
            self.assertIn('=>$value.value end', old)
            logic.write_text(old.replace('=>$value.value end',
                                         '=>$value.value+11 end', 1), encoding="utf-8")
            compile_all()
            result = coordinator.build_executable(root / "main.vlnc", output,
                                                  target_identity="main")
            self.assertNotEqual(result.disposition, BuildDisposition.REUSED)
            expected = list(case.expected)
            expected[0] += 11
            self.assertEqual(run(loads(output.read_bytes())), expected)
            for name in case.build_order:
                root.joinpath(*name.split(".")).with_suffix(".vlnc").unlink()
            coordinator.build_executable(root / "main.vlnc", output,
                                         target_identity="main")
            self.assertEqual(run(loads(output.read_bytes())), expected)

    def test_trait_impl_change_relinks_variant_consumer(self):
        case = interaction_case("trait_variant_dispatch", "facade", 41)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, case)
            coordinator = CompilationCoordinator(root)

            def compile_all():
                for name in case.build_order:
                    path = root.joinpath(*name.split(".")).with_suffix(".vlnc")
                    coordinator.build_module(path, path.with_suffix(".vbcm"),
                                             module_name=name)

            compile_all()
            target = root / "main.vbc"
            coordinator.build_executable(root / "main.vlnc", target,
                                         target_identity="main")
            self.assertEqual(run(loads(target.read_bytes())), list(case.expected))
            provider = root / "core.vlnc"
            source = provider.read_text(encoding="utf-8")
            self.assertIn('=> $self.value end', source)
            provider.write_text(source.replace('=> $self.value end',
                                               '=> $self.value+9 end'), encoding="utf-8")
            compile_all()
            result = coordinator.build_executable(root / "main.vlnc", target,
                                                  target_identity="main")
            self.assertNotEqual(result.disposition, BuildDisposition.REUSED)
            self.assertEqual(run(loads(target.read_bytes())), [case.expected[0] + 9])

    def test_compiled_nested_dependency_preserves_qualified_identity(self):
        case = interaction_case("generic_object_variant", "nested", 31)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, case)
            deleted = prepare_layout(root, case, "compiled")
            self.assertIn("pkg.core", deleted)
            self.assertEqual(load_module_file(root / "pkg" / "core.vbcm").module_name,
                             "pkg.core")
            self.assertFalse((root / "pkg" / "core.vlnc").exists())
            self.assertEqual(execute_interaction(case, "compiled"), 4)


if __name__ == "__main__":
    unittest.main()
