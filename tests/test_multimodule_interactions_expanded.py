"""Independent controls for the extended, connected multi-module contracts.

Ordinary discovery already runs the whole *source* family/graph matrix in
`test_multimodule_interactions`. Here we target high-risk source-free paths,
reproducible negative semantics, runtime faults, and interface hardening.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import patch

from tools.multimodule_additional_contracts import ADDITIONAL_CONTRACTS
from tools.multimodule_composition_contracts import COMPOSITION_CONTRACTS
from tools.multimodule_matrix_slices import run_slice
from tools.multimodule_fuzzing import ProjectCase, write_project
from tools.multimodule_interactions import (
    GRAPHS,
    INVALID_INTERACTIONS,
    LANGUAGE_AXES,
    SPECS,
    check_invalid_interaction,
    combinations,
    coverage_inventory,
    execute_interaction,
    interaction_case,
    negative_combinations,
    prepare_layout,
)
from valiance.analysis import Analyser
from valiance.elements.builtins import _call_call_site
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.interface_serialization import MAGIC, dumps_interface, loads_interface
from valiance.runtime.serialization import BytecodeFormatError


class ExtendedMultiModuleContracts(unittest.TestCase):
    def test_facade_object_fields_keep_the_variant_providers_identity(self):
        case = ProjectCase(
            'variant_field_provider_identity', 'facade', 709,
            (
                ('core', 'public variant Choice =>\n'
                         ' Present => $value: Int end\n Empty\nend\n'),
                ('logic', 'import {core.Choice}\n'
                          'public object Holder => public $choice: Choice end\n'),
                ('gateway', 'public import {core.Choice, logic.Holder}\n'
                            'public object LocalHolder => public $choice: Choice end\n'),
            ),
            'import {gateway.[Choice, Holder, LocalHolder]}\n'
            '$first = Holder(Choice.Present(7))\n'
            '$second = LocalHolder(Choice.Present(11))\n'
            '$first.choice match =>\n as :Present(value) => $value\n as :Empty => 0\nend\n'
            '$second.choice match =>\n as :Present(value) => $value\n as :Empty => 0\nend\n',
            (7, 11), ('core', 'logic', 'gateway'),
        )
        for layout in ('source', 'compiled'):
            with self.subTest(layout=layout):
                self.assertEqual(execute_interaction(case, layout), 4)

    def test_new_families_are_connected_and_explicitly_exported(self):
        for name in ADDITIONAL_CONTRACTS:
            case = interaction_case(name, "direct", 409)
            self.assertEqual(len(case.modules), 2, name)
            self.assertEqual(case.build_order, ("core", "logic"), name)
            self.assertTrue(SPECS[name].core_exports, name)
            self.assertTrue(SPECS[name].dependent_exports, name)
            self.assertTrue(case.expected, name)

    def test_new_contracts_retain_semantics_across_source_alias_and_diamond(self):
        # Other source topologies are handled by test_multimodule_interactions.
        for name in ADDITIONAL_CONTRACTS:
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, "alias", 409), "source"), 4)

    def test_high_risk_compiled_only_and_mixed_contracts(self):
        for name in (
            "higher_order_generic_object", "tag_validator_overload",
            "tag_overlay_generic", "reflected_object_field",
            "json_object_roundtrip", "lazy_variant_map",
            "list_object_mutation", "dict_object_mutation",
        ):
            for graph, layout in (("nested", "compiled"), ("diamond", "core_binary")):
                with self.subTest(family=name, graph=graph, layout=layout):
                    self.assertEqual(execute_interaction(
                        interaction_case(name, graph, 503), layout), 4)

    def test_new_negative_contracts_survive_binary_and_renamed_facades(self):
        for name in (
            "higher_order_generic_object", "structural_record_generic",
            "optional_cast_union", "tag_validator_overload",
            "reflected_object_field", "json_object_roundtrip",
            "list_object_mutation", "dict_object_mutation",
        ):
            with self.subTest(family=name):
                self.assertEqual(
                    check_invalid_interaction(name, "alias", 503, "compiled"),
                    INVALID_INTERACTIONS[name][1],
                )

    def test_new_compositions_are_real_two_owner_projects_with_independent_oracles(self):
        for name in COMPOSITION_CONTRACTS:
            with self.subTest(family=name):
                case = interaction_case(name, "direct", 409)
                self.assertEqual(case.build_order, ("core", "logic"))
                self.assertEqual(len(case.modules), 2)
                self.assertTrue(SPECS[name].core_exports)
                self.assertTrue(SPECS[name].dependent_exports)
                self.assertTrue(case.expected)
                self.assertEqual(execute_interaction(case, "source"), 4)

    def test_new_compositions_have_binary_and_negative_alias_oracles(self):
        for name in (
            "enum_in_generic_variant", "variant_in_dictionary_mutation",
            "trait_inherited_default_in_variant", "trait_default_overload_from_enum",
            "union_object_list_match", "generic_option_overload",
            "computed_tag_through_dictionary", "json_enum_value",
            "lazy_object_trait_dispatch",
        ):
            with self.subTest(family=name):
                case = interaction_case(name, "alias", 151)
                self.assertEqual(execute_interaction(case, "compiled"), 4)
                self.assertEqual(
                    check_invalid_interaction(name, "alias", 151, "compiled"),
                    INVALID_INTERACTIONS[name][1],
                )

    def test_composition_inventory_counts_and_replay_are_explicit(self):
        report = coverage_inventory()
        self.assertGreaterEqual(len(COMPOSITION_CONTRACTS), 42)
        self.assertGreaterEqual(report["pair_backlog_counts"]["fixture_defined"], 389)
        self.assertEqual(sum(report["pair_backlog_counts"].values()), 1830)
        self.assertEqual(len(combinations()), len(SPECS) * len(GRAPHS) * 5)
        self.assertEqual(len(negative_combinations()),
                         len(INVALID_INTERACTIONS) * len(GRAPHS) * 5)
        for name in COMPOSITION_CONTRACTS:
            self.assertIn(name, INVALID_INTERACTIONS)
            self.assertEqual(interaction_case(name, "nested", 17),
                             interaction_case(name, "nested", 17))

    def test_isolated_slice_reports_both_success_and_timeout(self):
        index = list(SPECS).index("enum_in_generic_box")
        report = run_slice(start=index, stop=index + 1, seed=19,
                           graphs=("direct",), layout="source",
                           negative=False, case_timeout=8)
        self.assertEqual((report["projects_passed"], report["execution_checks_passed"]),
                         (1, 4))
        with patch("tools.multimodule_matrix_slices.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("worker", 0.01)):
            timed_out = run_slice(start=index, stop=index + 1, seed=19,
                                  graphs=("direct",), layout="source",
                                  negative=False, case_timeout=0.01)
        self.assertEqual(timed_out["projects_failed"], 1)
        self.assertEqual(timed_out["case_timeouts"], 1)
        self.assertEqual(timed_out["execution_checks_passed"], 0)

    def test_imported_tag_validator_panics_in_every_vm_mode(self):
        case = interaction_case("tag_validator_overload", "nested", 673)
        with TemporaryDirectory(prefix="valiance-tag-validator-negative-") as folder:
            root = Path(folder)
            write_project(root, case)
            removed = prepare_layout(root, case, "compiled")
            self.assertIn("pkg.core", removed)
            source = case.consumer.partition("\n")[0] + "\n-1 #valid\n"
            analyser = Analyser(source_file=root / "main.vlnc")
            typed = analyser.analyse(parse(source))
            self.assertFalse(analyser.diagnostics, analyser.diagnostics)
            for optimized in (False, True):
                program = compile_program(typed, optimize=optimized)
                for serialized, executable in ((False, program), (True, loads(dumps(program)))):
                    with self.subTest(optimized=optimized, serialized=serialized):
                        with self.assertRaisesRegex(Exception, r"tag validator .*#valid failed"):
                            run(executable)

    def test_interface_serializer_allows_only_explicit_compiler_callback(self):
        encoded = dumps_interface((_call_call_site,))
        self.assertIs(loads_interface(encoded)[0], _call_call_site)
        self.assertIn(b"compiler_callback", encoded)
        self.assertNotIn(b"pickle", encoded)
        with self.assertRaisesRegex(BytecodeFormatError, "unsupported compiler callback"):
            dumps_interface((lambda: None,))
        document = json.loads(encoded[len(MAGIC) :])
        document["value"]["items"][0]["id"] = "os.system"
        tampered = MAGIC + json.dumps(document, sort_keys=True).encode()
        with self.assertRaisesRegex(BytecodeFormatError, "unknown compiler callback"):
            loads_interface(tampered)

    def test_backlog_counts_exactly_partition_61_axis_universe(self):
        report = coverage_inventory()
        pairs = report["semantic_pair_backlog"]
        self.assertEqual(len(pairs), len(LANGUAGE_AXES) * (len(LANGUAGE_AXES) - 1) // 2)
        self.assertEqual(len({tuple(pair["features"]) for pair in pairs}), len(pairs))
        self.assertEqual(
            sum(report["pair_backlog_counts"].values()), len(pairs)
        )
        self.assertEqual(len(combinations()), len(SPECS) * len(GRAPHS) * 5)
        self.assertEqual(len(negative_combinations()), len(INVALID_INTERACTIONS) * len(GRAPHS) * 5)
        self.assertGreater(report["pair_backlog_counts"]["fixture_defined"], 54)
        # A fixture is never called verified without an actual campaign record.
        self.assertEqual({entry["status"] for entry in pairs},
                         {"fixture_defined", "needs_semantic_contract"})


if __name__ == "__main__":
    unittest.main()
