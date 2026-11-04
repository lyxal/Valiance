"""Filesystem-backed module graph, feature interaction, and invalid-input oracles.

The deterministic campaign's full feature/graph cross-product runs separately;
these tests make the essential matrix discoverable in the ordinary unittest
suite and preserve regressions for source-free and incremental imports.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.multimodule_fuzzing import (
    FAMILIES, GRAPHS, campaign, check_rejected, execute_case, project_case,
    rejected_projects, write_project,
)
from valiance.analysis import Analyser
from valiance.incremental import BuildDisposition, CompilationCoordinator
from valiance.parsing import parse
from valiance.runtime import loads, run


class MultiModuleProjectTests(unittest.TestCase):
    def test_all_language_families_across_six_source_import_graphs(self):
        """Exercise every contract as a real source project in four modes."""
        for family, graph in product(FAMILIES, GRAPHS):
            with self.subTest(family=family, graph=graph):
                self.assertEqual(execute_case(project_case(family, graph, 711)), 4)

    def test_every_family_from_binary_only_dependencies(self):
        """Compiled metadata and link information must not depend on source."""
        for family in FAMILIES:
            # Direct and multi-hop imported interface contracts for each kind.
            for graph in ("direct", "deep_facade"):
                with self.subTest(family=family, graph=graph):
                    self.assertEqual(execute_case(
                        project_case(family, graph, 1406), source_free=True,
                    ), 4)

    def test_rejected_imports_are_specific_in_source_and_binary_modes(self):
        for case in rejected_projects():
            for source_free in (False, True):
                with self.subTest(case=case.name, source_free=source_free):
                    check_rejected(case, source_free=source_free)

    def test_source_generation_is_replayable(self):
        first = project_case("generic_trait", "diamond", 991)
        self.assertEqual(first, project_case("generic_trait", "diamond", 991))
        self.assertNotEqual(first, project_case("generic_trait", "diamond", 992))
        self.assertEqual(execute_case(first), 4)

    def test_fuzz_campaign_samples_each_pair_before_repeating(self):
        report = campaign(seed=911, iterations=12,
                          families=("element", "generic_object", "variant"),
                          graphs=("direct", "facade", "diamond", "alias"))
        self.assertEqual(report["failed_cases"], 0, report["failures"])
        self.assertEqual(report["successful_cases"], 12)
        self.assertEqual(report["executions"], 48)
        self.assertEqual(report["covered_feature_graph_pairs"], 12)

    def test_reexported_overloads_keep_all_signatures_through_three_hops(self):
        for graph in ("facade", "deep_facade", "diamond", "alias"):
            for source_free in (False, True):
                with self.subTest(graph=graph, source_free=source_free):
                    case = project_case("overloaded_element", graph, 17)
                    self.assertEqual(execute_case(case, source_free=source_free), 4)

    def test_selected_reexport_never_leaks_an_unselected_overload(self):
        """Selecting one overload must not import siblings through a facade."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'core.vlnc').write_text(
                'overload(String -> String)\n'
                'overload(Number -> Number)\n'
                'public define identity(value) => $value\n', encoding='utf-8')
            (root / 'api.vlnc').write_text(
                'public import {core.[identity(Number)]}\n', encoding='utf-8')
            for source_free in (False, True):
                if source_free:
                    coordinator = CompilationCoordinator(root)
                    for name in ('core', 'api'):
                        path = root / f'{name}.vlnc'
                        coordinator.build_module(
                            path, path.with_suffix('.vbcm'), module_name=name,
                        )
                    for name in ('core', 'api'):
                        (root / f'{name}.vlnc').unlink()
                analyser = Analyser(source_file=root / 'main.vlnc')
                typed = analyser.analyse(parse('import {api.identity}\nidentity(7)'))
                self.assertEqual(analyser.diagnostics, [])
                from valiance.runtime import compile_program, dumps
                for optimize in (False, True):
                    program = compile_program(typed, optimize=optimize)
                    self.assertEqual(run(program), [7])
                    self.assertEqual(run(loads(dumps(program))), [7])
                analyser = Analyser(source_file=root / 'main.vlnc')
                analyser.analyse(parse('import {api.identity}\nidentity("wrong")'))
                self.assertTrue(any('no overload' in str(message).lower()
                                    for message in analyser.diagnostics),
                                analyser.diagnostics)

    def test_diamond_generic_aliases_retain_one_nominal_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'core.vlnc').write_text(
                'public object[T] Box => public $value:T end\n', encoding='utf-8')
            (root / 'left.vlnc').write_text(
                'public import {core.[Box as LeftBox]}\n', encoding='utf-8')
            (root / 'right.vlnc').write_text(
                'public import {core.[Box as RightBox]}\n', encoding='utf-8')
            consumer = ('import {left.LeftBox,right.RightBox}\n'
                        '$same:LeftBox[Int]=RightBox(41)\n$same.value\n')
            for source_free in (False, True):
                if source_free:
                    coordinator = CompilationCoordinator(root)
                    for name in ('core', 'left', 'right'):
                        path = root / f'{name}.vlnc'
                        coordinator.build_module(path, path.with_suffix('.vbcm'),
                                                 module_name=name)
                        # Keep sources until dependencies have all compiled.
                    for name in ('core', 'left', 'right'):
                        (root / f'{name}.vlnc').unlink()
                analyser = Analyser(source_file=root / 'main.vlnc')
                typed = analyser.analyse(parse(consumer))
                self.assertEqual(analyser.diagnostics, [])
                from valiance.runtime import compile_program, dumps
                for optimize in (False, True):
                    compiled = compile_program(typed, optimize=optimize)
                    self.assertEqual(run(compiled), [41])
                    self.assertEqual(run(loads(dumps(compiled))), [41])

    def test_incremental_change_in_overload_body_relinks_deep_facades(self):
        case = project_case("overloaded_element", "deep_facade", 32)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, case)
            coordinator = CompilationCoordinator(root)
            for name in case.build_order:
                source = root / f'{name}.vlnc'
                coordinator.build_module(source, source.with_suffix('.vbcm'),
                                         module_name=name)
            destination = root / 'main.vbc'
            first = coordinator.build_executable(root / 'main.vlnc', destination, target_identity='main')
            self.assertEqual(run(loads(destination.read_bytes())), list(case.expected))
            self.assertEqual(
                coordinator.build_executable(root / 'main.vlnc', destination, target_identity='main').disposition,
                BuildDisposition.REUSED,
            )
            original = (root / 'core.vlnc').read_text(encoding='utf-8')
            (root / 'core.vlnc').write_text(
                original.replace('value+1', 'value+7'), encoding='utf-8')
            for name in case.build_order:
                source = root / f'{name}.vlnc'
                coordinator.build_module(source, source.with_suffix('.vbcm'),
                                         module_name=name)
            result = coordinator.build_executable(root / 'main.vlnc', destination, target_identity='main')
            self.assertNotEqual(result.disposition, BuildDisposition.REUSED)
            expected = list(case.expected)
            expected[0] += 6
            self.assertEqual(run(loads(destination.read_bytes())), expected)
            for name in case.build_order:
                (root / f'{name}.vlnc').unlink()
            coordinator.build_executable(root / 'main.vlnc', destination, target_identity='main')
            self.assertEqual(run(loads(destination.read_bytes())), expected)

    def test_incremental_enum_value_edit_relinks_transitive_callers(self):
        case = project_case("enum", "facade", 55)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, case)
            coordinator = CompilationCoordinator(root)
            for name in case.build_order:
                path = root / f'{name}.vlnc'
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
            target = root / 'main.vbc'
            coordinator.build_executable(root / 'main.vlnc', target, target_identity='main')
            self.assertEqual(run(loads(target.read_bytes())), ['yes', 'no'])
            core = root / 'core.vlnc'
            core.write_text(core.read_text().replace('"yes"', '"YES_EDITED"'),
                            encoding='utf-8')
            for name in case.build_order:
                path = root / f'{name}.vlnc'
                coordinator.build_module(path, path.with_suffix('.vbcm'), module_name=name)
            coordinator.build_executable(root / 'main.vlnc', target, target_identity='main')
            self.assertEqual(run(loads(target.read_bytes())), ['YES_EDITED', 'no'])


if __name__ == '__main__':
    unittest.main()
