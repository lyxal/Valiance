"""Connected cross-feature semantics across real module/source-artifact graphs.

Keep these in normal unittest discovery. The full 330-case product and
negative mutation expansion are deterministic scheduled campaigns rather than
blocking PR test execution with a redundant all-mode matrix.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.multimodule_fuzzing import write_project
from tools.multimodule_interactions import (
    EXECUTION_MODES, GRAPHS, INTERACTIONS, INVALID_INTERACTIONS, LAYOUTS,
    batched_campaign, candidate_at, check_invalid_interaction, combinations,
    coverage_inventory,
    execute_interaction, interaction_case, prepare_layout,
)
from valiance.incremental import BuildDisposition, CompilationCoordinator
from valiance.runtime import loads, run
from valiance.runtime.compiled_module import load_module_file


# These tests use independent TemporaryDirectory fixtures and no shared class
# state. Allow the parallel runner to distribute their expensive methods while
# executing all remaining tests in this module as another selection.
PARALLEL_SAFE_METHODS = (
    "MultiModuleInteractionTests.test_source_direct_group_0",
    "MultiModuleInteractionTests.test_source_direct_group_1",
    "MultiModuleInteractionTests.test_source_direct_group_2",
    "MultiModuleInteractionTests.test_source_direct_group_3",
    "MultiModuleInteractionTests.test_source_facade_group_0",
    "MultiModuleInteractionTests.test_source_facade_group_1",
    "MultiModuleInteractionTests.test_source_facade_group_2",
    "MultiModuleInteractionTests.test_source_facade_group_3",
    "MultiModuleInteractionTests.test_source_deep_facade_group_0",
    "MultiModuleInteractionTests.test_source_deep_facade_group_1",
    "MultiModuleInteractionTests.test_source_deep_facade_group_2",
    "MultiModuleInteractionTests.test_source_deep_facade_group_3",
    "MultiModuleInteractionTests.test_source_diamond_group_0",
    "MultiModuleInteractionTests.test_source_diamond_group_1",
    "MultiModuleInteractionTests.test_source_diamond_group_2",
    "MultiModuleInteractionTests.test_source_diamond_group_3",
    "MultiModuleInteractionTests.test_source_nested_group_0",
    "MultiModuleInteractionTests.test_source_nested_group_1",
    "MultiModuleInteractionTests.test_source_nested_group_2",
    "MultiModuleInteractionTests.test_source_nested_group_3",
    "MultiModuleInteractionTests.test_source_alias_group_0",
    "MultiModuleInteractionTests.test_source_alias_group_1",
    "MultiModuleInteractionTests.test_source_alias_group_2",
    "MultiModuleInteractionTests.test_source_alias_group_3",
    "MultiModuleInteractionTests.test_compiled_nested_group_0",
    "MultiModuleInteractionTests.test_compiled_nested_group_1",
    "MultiModuleInteractionTests.test_compiled_nested_group_2",
    "MultiModuleInteractionTests.test_compiled_nested_group_3",
    "MultiModuleInteractionTests.test_negative_group_0",
    "MultiModuleInteractionTests.test_negative_group_1",
    "MultiModuleInteractionTests.test_negative_group_2",
    "MultiModuleInteractionTests.test_negative_group_3",
)


class MultiModuleInteractionTests(unittest.TestCase):
    # Each feature is assigned to exactly one group per graph. Keeping these
    # as separate, explicitly schedulable tests prevents a single unittest
    # method from monopolising a CI worker for the full interaction matrix.
    def _check_source_graph(self, graph: str, group: int):
        for family in INTERACTIONS[group::4]:
            with self.subTest(family=family, graph=graph):
                self.assertEqual(execute_interaction(
                    interaction_case(family, graph, 709), "source"), 4)

    def _check_compiled_nested_group(self, group: int):
        # Also exercises recursive coordinator dependency-name handling.
        for family in INTERACTIONS[group::4]:
            for layout in ("compiled", "dependent_binary"):
                with self.subTest(family=family, layout=layout):
                    self.assertEqual(execute_interaction(
                        interaction_case(family, "nested", 211), layout), 4)

    def _check_negative_group(self, group: int):
        for family in tuple(INVALID_INTERACTIONS)[group::4]:
            for layout in ("source", "compiled"):
                with self.subTest(family=family, layout=layout):
                    self.assertTrue(check_invalid_interaction(
                        family, "facade", 512, layout))

    def test_source_direct_group_0(self):
        self._check_source_graph("direct", 0)

    def test_source_direct_group_1(self):
        self._check_source_graph("direct", 1)

    def test_source_direct_group_2(self):
        self._check_source_graph("direct", 2)

    def test_source_direct_group_3(self):
        self._check_source_graph("direct", 3)

    def test_source_facade_group_0(self):
        self._check_source_graph("facade", 0)

    def test_source_facade_group_1(self):
        self._check_source_graph("facade", 1)

    def test_source_facade_group_2(self):
        self._check_source_graph("facade", 2)

    def test_source_facade_group_3(self):
        self._check_source_graph("facade", 3)

    def test_source_deep_facade_group_0(self):
        self._check_source_graph("deep_facade", 0)

    def test_source_deep_facade_group_1(self):
        self._check_source_graph("deep_facade", 1)

    def test_source_deep_facade_group_2(self):
        self._check_source_graph("deep_facade", 2)

    def test_source_deep_facade_group_3(self):
        self._check_source_graph("deep_facade", 3)

    def test_source_diamond_group_0(self):
        self._check_source_graph("diamond", 0)

    def test_source_diamond_group_1(self):
        self._check_source_graph("diamond", 1)

    def test_source_diamond_group_2(self):
        self._check_source_graph("diamond", 2)

    def test_source_diamond_group_3(self):
        self._check_source_graph("diamond", 3)

    def test_source_nested_group_0(self):
        self._check_source_graph("nested", 0)

    def test_source_nested_group_1(self):
        self._check_source_graph("nested", 1)

    def test_source_nested_group_2(self):
        self._check_source_graph("nested", 2)

    def test_source_nested_group_3(self):
        self._check_source_graph("nested", 3)

    def test_source_alias_group_0(self):
        self._check_source_graph("alias", 0)

    def test_source_alias_group_1(self):
        self._check_source_graph("alias", 1)

    def test_source_alias_group_2(self):
        self._check_source_graph("alias", 2)

    def test_source_alias_group_3(self):
        self._check_source_graph("alias", 3)

    def test_compiled_nested_group_0(self):
        self._check_compiled_nested_group(0)

    def test_compiled_nested_group_1(self):
        self._check_compiled_nested_group(1)

    def test_compiled_nested_group_2(self):
        self._check_compiled_nested_group(2)

    def test_compiled_nested_group_3(self):
        self._check_compiled_nested_group(3)

    def test_negative_group_0(self):
        self._check_negative_group(0)

    def test_negative_group_1(self):
        self._check_negative_group(1)

    def test_negative_group_2(self):
        self._check_negative_group(2)

    def test_negative_group_3(self):
        self._check_negative_group(3)

    def test_mixed_graphs_and_tagged_facades(self):
        for family in ("constructed_tag_generic", "generic_trait_variant",
                       "overload_generic_object", "object_tag_result"):
            for graph in ("diamond", "alias"):
                for layout in ("core_binary", "alternating_binary"):
                    with self.subTest(family=family, graph=graph, layout=layout):
                        self.assertEqual(execute_interaction(
                            interaction_case(family, graph, 181), layout), 4)

    def test_scheduled_interaction_groups_preserve_all_matrix_cases(self):
        # Verify the explicit method selections remain a lossless partition as
        # the feature registries grow (without executing thousands of programs).
        with patch(__name__ + ".execute_interaction", return_value=4) as execute, \
             patch(__name__ + ".check_invalid_interaction", return_value=True) as invalid:
            for graph in GRAPHS:
                for group in range(4):
                    getattr(self, f"test_source_{graph}_group_{group}")()
            for group in range(4):
                getattr(self, f"test_compiled_nested_group_{group}")()
                getattr(self, f"test_negative_group_{group}")()

        source = [(call.args[0].family, call.args[0].graph)
                  for call in execute.call_args_list if call.args[1] == "source"]
        nested = [(call.args[0].family, call.args[1])
                  for call in execute.call_args_list
                  if call.args[1] in ("compiled", "dependent_binary")]
        rejected = [(call.args[0], call.args[3]) for call in invalid.call_args_list]
        self.assertCountEqual(source, [(family, graph)
                                       for family in INTERACTIONS for graph in GRAPHS])
        self.assertCountEqual(nested, [(family, layout)
                                       for family in INTERACTIONS
                                       for layout in ("compiled", "dependent_binary")])
        self.assertCountEqual(rejected, [(family, layout)
                                         for family in INVALID_INTERACTIONS
                                         for layout in ("source", "compiled")])

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


class MultiModuleCampaignRunnerTests(unittest.TestCase):
    def test_timeout_retries_smaller_windows_without_losing_healthy_cases(self):
        attempted = []

        def execute(argv, *, capture_output, text, timeout):
            offset = int(argv[argv.index("--start") + 1])
            count = int(argv[argv.index("--iterations") + 1])
            attempted.append((offset, count))
            if count > 2 or offset == 2 and count > 1 or offset == 3:
                raise subprocess.TimeoutExpired(argv, timeout)
            passed = [
                {"index": index, "family": "example", "graph": "direct",
                 "layout": "source"}
                for index in range(offset, offset + count)
            ]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "passed": passed, "failures": [],
                "execution_checks_passed": 4 * count,
                "diagnostic_checks_passed": 0,
            }), "")

        with patch("tools.multimodule_interactions.subprocess.run", side_effect=execute):
            report = batched_campaign(
                seed=49103, start=0, iterations=4, batch_size=4,
                timeout=3, fail_fast=False,
            )
        self.assertEqual(attempted, [(0, 4), (0, 2), (2, 2), (2, 1), (3, 1)])
        self.assertEqual([case["index"] for case in report["passed"]], [0, 1, 2])
        self.assertEqual(report["execution_checks_passed"], 12)
        self.assertEqual(report["cases_passed"], 3)
        self.assertEqual(report["cases_failed"], 1)
        self.assertEqual(report["cases_not_verified"], 1)
        self.assertEqual(report["failures"][0]["index_range"], [3, 4])

    def test_invalid_worker_report_is_isolated_to_its_case(self):
        attempted = []

        def execute(argv, *, capture_output, text, timeout):
            offset = int(argv[argv.index("--start") + 1])
            count = int(argv[argv.index("--iterations") + 1])
            attempted.append((offset, count))
            if count > 1 or offset == 1:
                return subprocess.CompletedProcess(argv, 1, "not json", "broken")
            passed = [{"index": offset, "family": "example", "graph": "facade",
                       "layout": "compiled"}]
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "passed": passed, "failures": [], "execution_checks_passed": 4,
                "diagnostic_checks_passed": 0,
            }), "")

        with patch("tools.multimodule_interactions.subprocess.run", side_effect=execute):
            report = batched_campaign(
                seed=49103, start=0, iterations=2, batch_size=2,
                timeout=3, fail_fast=False,
            )
        self.assertEqual(attempted, [(0, 2), (0, 1), (1, 1)])
        self.assertEqual(report["cases_passed"], 1)
        self.assertEqual(report["cases_failed"], 1)
        self.assertEqual(report["cases_not_verified"], 1)



if __name__ == "__main__":
    unittest.main()
