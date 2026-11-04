"""New connected source/binary contracts plus exhaustive bounded channel oracle."""
from __future__ import annotations

import unittest
import random

from tools.channel_schedule_oracle import explore_channel_orders
from tools.multimodule_deep_contracts import DEEP_CONTRACTS, INVALID_DEEP_CONTRACTS
from tools.multimodule_boundary_contracts import BOUNDARY_CONTRACTS, INVALID_BOUNDARY_CONTRACTS
from tools.multimodule_interactions import (
    GRAPHS, SPECS, INVALID_INTERACTIONS, check_invalid_interaction,
    coverage_inventory, execute_interaction, interaction_case,
    combinations, negative_combinations, candidate_at, negative_at,
)

NEW_NAMES = tuple(DEEP_CONTRACTS) + tuple(BOUNDARY_CONTRACTS)
# Full 61-family campaigns are scheduled and replayable. Keep PR smoke bounded
# so default unittest discovery does not multiply the same full matrix twice.
SMOKE_NAMES = (
    'try_trait_dispatch', 'try_optional_variant_match', 'branch_enum_overload',
    'loop_trait_dispatch', 'higher_order_inherited_trait',
    'generic_variant_dictionary', 'trait_default_in_option',
    'borrowed_try_method', 'task_generic_variant',
    'reflection_generic_optional', 'reflection_try_generic',
    'json_generic_box', 'json_trait_default_record',
    'tag_validator_variant_match', 'tag_overlay_trait_method',
    'unit_tag_generic_option', 'task_json_generic',
)



class DeepMultiModuleTests(unittest.TestCase):
    def test_positive_and_invalid_contracts_have_exact_registry_correspondence(self):
        self.assertEqual(len(DEEP_CONTRACTS), 36)
        self.assertEqual(len(BOUNDARY_CONTRACTS), 25)
        self.assertEqual(set(DEEP_CONTRACTS), set(INVALID_DEEP_CONTRACTS))
        self.assertEqual(set(BOUNDARY_CONTRACTS), set(INVALID_BOUNDARY_CONTRACTS))
        for name in NEW_NAMES:
            with self.subTest(family=name):
                self.assertEqual(INVALID_INTERACTIONS[name][1],
                                 (INVALID_DEEP_CONTRACTS | INVALID_BOUNDARY_CONTRACTS)[name][1])
                self.assertTrue(SPECS[name].core_exports)
                self.assertTrue(SPECS[name].dependent_exports)
                self.assertTrue(SPECS[name].features)
                self.assertTrue(interaction_case(name, 'nested', 173).expected)

    def test_source_semantics_representative_new_contracts(self):
        for name in SMOKE_NAMES:
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, 'direct', 193), 'source'), 4)

    def test_compiled_nested_semantics_selected_mixed_features(self):
        for name in (
            'try_optional_variant_match', 'higher_order_inherited_trait',
            'borrowed_generic_trait', 'task_generic_variant',
            'reflection_generic_optional', 'reflection_try_generic',
            'json_trait_default_record', 'json_generic_list',
            'tag_validator_generic_box', 'tag_overlay_trait_method',
            'task_reflection_object', 'task_tag_generic',
        ):
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, 'nested', 193), 'compiled'), 4)

    def test_renamed_facade_preserves_representative_behavior(self):
        # Full six-graph and mixed-artifact diamond runs are CI campaigns;
        # keep normal unittest discovery bounded to fast, representative aliases.
        for name in (
            'try_overload_object', 'generic_variant_dictionary',
            'reflection_list_map', 'tag_validator_variant_match',
        ):
            with self.subTest(family=name):
                self.assertEqual(execute_interaction(
                    interaction_case(name, 'alias', 193), 'source'), 4)

    def test_designated_negative_diagnostics_representative_families(self):
        for name in SMOKE_NAMES:
            with self.subTest(family=name):
                self.assertEqual(check_invalid_interaction(name, 'direct', 193),
                                 INVALID_INTERACTIONS[name][1])

    def test_invalid_nominal_and_tag_contracts_with_binary_facade(self):
        for name in (
            'try_generic_trait_bound', 'branch_enum_overload',
            'higher_order_generic_box', 'borrowed_generic_trait',
            'reflection_generic_dictionary', 'json_trait_default_record',
            'tag_validator_variant_match', 'tag_overlay_trait_method',
            'task_reflection_object',
        ):
            with self.subTest(family=name):
                self.assertEqual(check_invalid_interaction(name, 'alias', 193, 'compiled'),
                                 INVALID_INTERACTIONS[name][1])

    def test_registry_inventory_is_finite_and_never_mislabels_success(self):
        report = coverage_inventory()
        self.assertEqual(len(SPECS), 172)
        self.assertEqual(len(INVALID_INTERACTIONS), 170)
        self.assertEqual(report['pair_backlog_counts'],
                         {'fixture_defined': 565, 'needs_semantic_contract': 1265})
        self.assertEqual(report['project_combinations'], 5160)
        self.assertEqual(report['possible_negative_graph_layout_cases'], 5100)
        self.assertEqual({item['status'] for item in report['semantic_pair_backlog']},
                         {'fixture_defined', 'needs_semantic_contract'})

    def test_cached_campaign_indices_match_independent_seeded_shuffle(self):
        """Memoization must not silently change a previously replayable case."""
        for seed in (49103, 19):
            for negative in (False, True):
                universe = negative_combinations() if negative else combinations()
                order = list(range(len(universe)))
                random.Random(seed).shuffle(order)
                for index in (0, 7, len(universe) // 2, len(universe) - 1):
                    expected = universe[order[index]]
                    if negative:
                        self.assertEqual(negative_at(seed, index), expected)
                    else:
                        case, layout = candidate_at(seed, index)
                        self.assertEqual((case.family, case.graph, layout), expected)

    def test_channel_operations_match_independent_model_for_all_bounded_orders(self):
        report = explore_channel_orders()
        self.assertEqual(report.orders, 5040)
        self.assertEqual(report.executions, 15120)
        self.assertEqual(report.capacities, (0, 1, 2))
        with self.assertRaisesRegex(ValueError, 'limit too small'):
            explore_channel_orders(max_orders=5039)


if __name__ == '__main__':
    unittest.main()
