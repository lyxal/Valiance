"""Owned/borrowed values and cooperative tasks across compiled module owners.

The fixtures live in the shared interaction registry; the general project
matrix covers every graph/layout.  These fast oracles additionally prove that
borrowed ownership operations are emitted and channel payloads have the correct
nominal shapes, not merely equal execution-mode output.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.multimodule_fuzzing import ProjectCase, write_project
from tools.multimodule_lifetime_concurrency_contracts import (
    INVALID_LIFETIME_CONCURRENCY_CONTRACTS, LIFETIME_CONCURRENCY_CONTRACTS,
)
from tools.multimodule_interactions import (
    INVALID_INTERACTIONS, SPECS, check_invalid_interaction,
    coverage_inventory, execute_interaction, interaction_case, prepare_layout,
)
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program
from valiance.runtime.bytecode import OpCode
from valiance.runtime.concurrency import Receive
from valiance.runtime.runtime_values import ObjectValue


class MultiModuleLifetimeConcurrencyTests(unittest.TestCase):
    def test_every_contract_has_distinct_positive_and_negative_oracles(self):
        self.assertEqual(len(LIFETIME_CONCURRENCY_CONTRACTS), 28)
        self.assertEqual(set(LIFETIME_CONCURRENCY_CONTRACTS),
                         set(INVALID_LIFETIME_CONCURRENCY_CONTRACTS))
        for name in LIFETIME_CONCURRENCY_CONTRACTS:
            with self.subTest(family=name):
                self.assertEqual(SPECS[name].output,
                                 LIFETIME_CONCURRENCY_CONTRACTS[name][6])
                self.assertEqual(INVALID_INTERACTIONS[name],
                                 INVALID_LIFETIME_CONCURRENCY_CONTRACTS[name])
                self.assertTrue(SPECS[name].features)
                self.assertTrue(SPECS[name].core_exports)
                self.assertTrue(SPECS[name].dependent_exports)

    def test_independent_values_in_all_four_execution_modes(self):
        for name in LIFETIME_CONCURRENCY_CONTRACTS:
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, "direct", 73), "source"), 4)

    def test_borrowing_is_actually_lowered_as_a_nonowning_access(self):
        names = tuple(name for name in LIFETIME_CONCURRENCY_CONTRACTS
                      if name.startswith("borrowed_nodup_"))
        self.assertEqual(len(names), 6)
        for name in names:
            with self.subTest(family=name), TemporaryDirectory() as tmp:
                case = interaction_case(name, "direct", 73)
                root = Path(tmp)
                write_project(root, case)
                analyser = Analyser(source_file=root / "main.vlnc")
                typed = analyser.analyse(parse(case.consumer))
                self.assertFalse(analyser.diagnostics, analyser.diagnostics)
                code = compile_program(typed, optimize=False)
                borrows = [instruction for instruction in code.main.instructions
                           if instruction.op is OpCode.LOAD_VAR_BORROW
                           and instruction.arg == "resource"]
                self.assertEqual(len(borrows), 2, name)

    def test_channel_protocol_distinguishes_payloads_and_closed_state(self):
        names = tuple(name for name in LIFETIME_CONCURRENCY_CONTRACTS
                      if name.startswith("channel_"))
        self.assertEqual(len(names), 5)
        for name in names:
            with self.subTest(family=name):
                case = interaction_case(name, "direct", 73)
                self.assertEqual(len(case.expected), 1)
                received, = case.expected
                self.assertIsInstance(received, Receive)
                self.assertFalse(received.closed)
                self.assertIsNotNone(received.value)
                self.assertEqual(execute_interaction(case, "source"), 4)
        self.assertIsInstance(interaction_case(
            "channel_generic_object_payload", "direct", 73).expected[0].value,
            ObjectValue)

    def test_compiled_only_nominals_tasks_and_borrows(self):
        for name in ("spawn_generic_object_field", "spawn_trait_dynamic_dispatch",
                     "spawn_variant_match", "spawn_enum_overload",
                     "spawn_generic_higher_order", "channel_trait_payload",
                     "channel_variant_payload", "borrowed_nodup_generic_repetition",
                     "borrowed_nodup_stack_permutation", "mutable_trait_reconstruction"):
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, "nested", 117), "compiled"), 4)

    def test_invalid_compiled_facades_preserve_diagnostic_class(self):
        for name in ("mutable_trait_reconstruction", "trait_duplication_through_generic",
                     "spawn_enum_overload", "channel_generic_object_payload",
                     "borrowed_nodup_generic_repetition", "borrowed_nodup_tag_view"):
            with self.subTest(family=name):
                self.assertEqual(check_invalid_interaction(
                    name, "alias", 117, "compiled"),
                    INVALID_LIFETIME_CONCURRENCY_CONTRACTS[name][1])

    def test_inventory_marks_fixtures_as_defined_not_full_campaign_verified(self):
        report = coverage_inventory()
        self.assertGreaterEqual(report["pair_backlog_counts"]["fixture_defined"], 565)
        self.assertEqual(sum(report["pair_backlog_counts"].values()), 1830)
        self.assertEqual(report["interaction_families"], len(SPECS))
        self.assertGreaterEqual(len(INVALID_INTERACTIONS), 170)
        self.assertEqual(report["project_combinations"], len(SPECS)*6*5)
        self.assertEqual(report["possible_negative_graph_layout_cases"], len(INVALID_INTERACTIONS)*6*5)
        self.assertTrue(all(item["status"] != "verified"
                            for item in report["semantic_pair_backlog"]))

    def test_isolated_resource_cannot_cross_spawn_boundary_in_compiled_modules(self):
        case = ProjectCase(
            "isolated_spawn", "direct", 1,
            (("core", '@mustcall(any = ["close"])\npublic object Resource =>\n'
                       ' public $value:Int\n public define close => $self end\nend\n'),
             ("logic", 'import {core.Resource}\n'
                        'public define consume(value:Resource)->Resource=>$value close end\n')),
            'import {core.Resource,logic.consume}\n'
            '$resource=Resource(4)\n'
            '$resource fn(value:Resource)->Resource=>$value consume end | spawn\n',
            (), ("core", "logic"),
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_project(root, case)
                prepare_layout(root, case, layout)
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse(case.consumer))
                self.assertIn("argument[0] has isolated type Resource",
                              "\n".join(analyser.diagnostics))

    def test_nonduplicable_imported_resource_rejects_explicit_copy(self):
        case = ProjectCase(
            "nonduplicable_copy", "direct", 1,
            (("core", '@nodup("Resource cannot be copied")\n'
                       'public object Resource => public $value:Int end\n'),
             ("logic", 'import {core.Resource}\n'
                        'public define use(value:Resource)->Int=>$value.value end\n')),
            'import {core.Resource,logic.use}\nResource(7) dup\n',
            (), ("core", "logic"),
        )
        for layout in ("source", "compiled"):
            with self.subTest(layout=layout), TemporaryDirectory() as tmp:
                root = Path(tmp)
                write_project(root, case)
                prepare_layout(root, case, layout)
                analyser = Analyser(source_file=root / "main.vlnc")
                analyser.analyse(parse(case.consumer))
                self.assertIn("Resource cannot be copied", "\n".join(analyser.diagnostics))

    def test_cli_timeout_is_propagated_to_bounded_case_executor(self):
        """The advertised deadline must actually reach the slice executor."""
        from tools import multimodule_matrix_slices as slices
        from argparse import Namespace
        with patch("sys.argv", ["multimodule_matrix_slices", "--start", "0",
                                "--stop", "0", "--case-timeout", "3.5"]), \
             patch.object(slices, "run_slice", return_value={"projects_failed": 0}) as runner:
            self.assertEqual(slices.main(), 0)
        self.assertEqual(runner.call_args.kwargs["case_timeout"], 3.5)


if __name__ == "__main__":
    unittest.main()
