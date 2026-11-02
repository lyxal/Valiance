"""Auditable correspondence between the 351 generics-plan rows and real tests.

Only link an existing test when its assertions exercise the named behaviour.
Unmapped rows are explicitly pending: a test blueprint is not a test result.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "Valiance_Generics_Comptime_Runtime_Test_Plan.md"
COUNTS = {
    "GD": 20, "UN": 22, "CP": 19, "OV": 20, "IN": 18,
    "OB": 20, "TB": 16, "VA": 15, "RK": 20, "FV": 17,
    "CS": 16, "WH": 20, "UC": 16, "TG": 16, "IM": 16,
    "RT": 18, "EC": 14, "IX": 14, "CX": 18, "MT": 16,
}

# A narrow mapping of independently existing regressions. The source-level
# fixtures in test_generics_plan are discovered and added automatically.
A = "test_analyser.AnalyserTests.test_"
R = "test_runtime.RuntimeTests.test_"
RR = "test_runtime.ReflectionRuntimeTests.test_"
T = "test_types.TypeLibraryTests.test_"
OT = "test_types.OverloadResolutionMatrixTests.test_"
V = "test_traits_and_variants.VariantSemanticTests.test_"
S = "test_traits_and_variants.TraitSemanticTests.test_"
D = "test_data_tags.DataTagUnitTests.test_"
C = "test_compiled_modules.CompiledModuleTests.test_"
EXISTING = {
    "UN-011": (T + "generic_rugged_call_keeps_leaf_type_across_rank_widening",),
    "UN-018": (T + "generic_evidence_combination_is_permutation_invariant",),
    "UN-020": (T + "optional_union_displays_with_question_mark_syntax",),
    "CP-013": (T + "atomic_evidence_validates_without_overriding_regular_evidence",
               R + "generic_atomic_scalar_search_uses_equality_constraint"),
    "CP-015": (T + "atomic_marker_can_supply_the_only_generic_evidence",),
    "OV-010": (T + "generic_constraints_are_checked_after_solving",),
    "IN-017": (T + "anonymous_generic_scopes_do_not_capture_named_generic",),
    "OB-009": (T + "nominal_generic_arguments_follow_declared_covariance",),
    "OB-010": (T + "nominal_generic_arguments_remain_invariant",),
    "OB-011": (T + "nominal_generic_arguments_follow_declared_contravariance",),
    "OB-014": (T + "nominal_generic_arguments_support_mixed_variance",),
    "TB-003": (S + "transitive_trait_behaviour_preserves_generic_result",),
    "VA-009": (V + "generic_match_retains_arguments_in_subject_union",),
    "VA-010": (V + "heterogeneous_variant_collection_dispatch",),
    "RK-010": (T + "higher_rugged_rank_satisfies_lower_rugged_rank",),
    "RK-011": (T + "minimum_rank_is_parameter_compatible_with_exact_rank",),
    "FV-006": (RR + "generic_function_values_specialize_through_higher_order_calls",),
    "CS-001": (A + "call_site_checked_function_uses_concrete_stack_parameter_types",),
    "CS-003": (A + "call_site_checked_function_rejects_incompatible_call_site_body",),
    "CS-005": (A + "variadic_tuple_parameter_is_call_site_checked_and_substituted",),
    "WH-001": (A + "where_clause_computes_return_rank_from_tuple_length",
               R + "where_rank_variable_in_return_type_executes"),
    "WH-004": (A + "where_clause_rejects_assignment_to_input_rank",),
    "WH-005": (A + "where_clause_compares_generic_type_literals",),
    "WH-006": (A + "where_clause_supports_arithmetic_min_and_max",),
    "WH-008": (A + "where_clause_can_reject_all_overloads",),
    "WH-012": (A + "where_clause_rejects_namespaced_static_operation",),
    "WH-011": (A + "where_clause_rejects_arbitrary_element_calls",),
    "WH-013": ("test_trait_plan_static.WherePlanTests.test_WH_012",),
    "WH-014": ("test_trait_plan_static.WherePlanTests.test_WH_014",),
    "WH-015": (A + "where_clause_rejects_result_type_literals",),
    "WH-019": (A + "where_clause_assertion_requires_number",),
    "UC-003": (T + "union_function_coverage_requires_unambiguous_branches",),
    "UC-005": (T + "union_function_coverage_accepts_nominal_supertypes",),
    "UC-009": (RR + "generic_union_dispatch_function_values_and_transitive_helpers",),
    "UC-010": (C + "generic_union_dispatch_specializes_from_source_free_modules",),
    "UC-012": (RR + "generic_union_dispatch_returns_exact_type_evidence_for_conversion",),
    "UC-013": (R + "map_dispatches_overloaded_functions_across_union_items",),
    "TG-001": (D + "constructed_tag_flows_through_generic_identity",),
    "TG-002": (D + "constructed_tag_automatically_flows_through_vector_arithmetic",),
    "TG-003": (D + "computed_tag_drops_while_constructed_tag_survives_same_call",),
    "TG-009": (D + "exact_tag_sets_require_exactly_the_present_tags",),
    "TG-010": (D + "later_disjoint_constructed_input_wins_automatic_flow",),
    "TG-012": (D + "indexing_projects_constructed_tag_depth",),
    "TG-013": (D + "slicing_preserves_constructed_tag_depth",),
    "IM-005": ("test_compiled_modules.CompiledGenericTraitImplementationTests.test_compiled_generic_trait_implication_preserves_correlation",),
    "IM-008": ("test_public_reexports.PublicReExportTests.test_public_trait_implementation_import_reexports_relationship",),
    "RT-011": (RR + "generic_union_dispatch_specializes_scalar_named_and_vector_calls",),
    "RT-014": (R + "generic_assignment_materialises_duplicatable_values",),
    "IX-013": (RR + "generic_replacement_specializes_a_write_to_a_concrete_owner",),
    "CX-003": (OT + "generic_solutions_do_not_leak_between_candidates_or_calls",),
}

# The specification explicitly labels some behaviours unresolved. Do not
# silently resolve them based on whichever compiler behaviour happens today.
DECISIONS = {
    "GD-005": "The trait form rejects bounds, but bounded define[T: Constraint] currently parses.",
}



def plan_rows() -> dict[str, dict[str, str]]:
    """Extract the canonical suite, ID, priority, oracle and scenario from the plan."""
    rows = {}
    pattern = re.compile(r"^\| \*\*([A-Z]{2}-\d{3})\*\* \| (P[012]) \| ([^|]+) \| (.*?) \|")
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            identifier, priority, oracle, scenario = match.groups()
            if identifier in rows:
                raise ValueError(f"duplicate generics plan row: {identifier}")
            rows[identifier] = dict(priority=priority, oracle=oracle.strip(), scenario=scenario)
    return rows


def coverage() -> dict[str, tuple[str, ...]]:
    """Collect only source fixtures and manually verified legacy regression links."""
    references = dict(EXISTING)
    module = importlib.import_module("test_generics_plan")
    for obj in vars(module).values():
        if not isinstance(obj, type) or not issubclass(obj, unittest.TestCase) or obj.__module__ != module.__name__:
            continue
        for method in unittest.defaultTestLoader.getTestCaseNames(obj):
            match = re.fullmatch(r"test_([A-Z]{2})_(\d{3})", method)
            if match:
                identifier = f"{match[1]}-{match[2]}"
                source_ref = f"test_generics_plan.{obj.__name__}.{method}"
                references[identifier] = (*references.get(identifier, ()), source_ref)
    return references
