"""Trace numbered plan oracles to independently executable regression tests."""

import importlib
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs" / "Valiance_Trait_Comptime_Runtime_Test_Plan.md"
COUNTS = dict(TD=17, DF=17, IH=14, BL=21, AN=15, GN=17, OV=22, TY=17,
              UC=15, MM=20, IM=25, WH=18, CA=14, VS=16, LC=17, TG=14, CC=10, IN=20)
ALIASES = {
    "A": "test_analyser.AnalyserTests",
    "R": "test_runtime.RuntimeTests",
    "S": "test_traits_and_variants.TraitSemanticTests",
    "V": "test_traits_and_variants.VariantSemanticTests",
    "T": "test_types.TypeLibraryTests",
    "I": "test_trait_implementation_imports.TraitImplementationImportTests",
    "B": "test_trait_implementation_imports.GenericTraitToTraitImplementationTests",
    "D": "test_compiled_modules.CompiledModuleTests",
    "C": "test_correctness_rescan.RuntimeRepresentationAgreementTests",
    "P": "test_programs.TentativeExampleTests",
}

# Removed by the language owner: these rows exclusively concern managed
# shared-state objects and per-type arenas, not ordinary immutable objects.
RETIRED = {
    "LC-003": "Managed shared-state objects were scrapped.",
    "LC-004": "Per-type shared-state arenas were scrapped.",
    "CC-006": "Shared-state object handles across tasks were scrapped.",
}

# Existing tests are reused only where their assertions exercise the row's
# oracle. Multiple references combine acceptance, execution and rejection checks.
EXISTING = {
    "DF-010": ("test_trait_plan_lifetimes.LifetimePlanTests.LC_002",),
    "OV-013": ("test_trait_plan_calls.OverloadPlanTests.OV_008",),
    "TG-001": ("test_trait_plan_interactions.InteractionPlanTests.OV_007",),
    "TG-014": ("test_trait_plan_tags.TagPlanTests.TG_002",),
    "IM-025": ("test_trait_plan_incremental.IncrementalPlanTests.IN_006", "D.dependency_body_edit_relinks_executable_target"),
    "IN-004": ("S.generic_trait_keeps_independent_instantiations",),
    "IN-012": ("test_trait_plan_dispatch.MultimethodPlanTests.MM_009",),
    "IN-018": ("test_trait_plan_imports.BehaviourPlanTests.IM_022",),
    "TD-003": ("S.missing_abstract_requirement_is_rejected",),
    "TD-004": ("S.requirement_signature_rejections",),
    "TD-005": ("S.requirement_signature_rejections",),
    "TD-011": ("S.implementation_can_reuse_object_method",),
    "DF-003": ("S.default_calls_abstract_requirement",),
    "DF-007": ("test_runtime.SelfTypeRuntimeTests.trait_implementation_resolves_self_to_object",),
    "DF-014": ("test_runtime.SelfTypeRuntimeTests.trait_implementation_resolves_self_to_object",),
    "IH-004": ("S.inherited_default_calls_inherited_requirement",),
    "IH-005": ("P.trait_can_implement_another_trait_without_repeating_defaults",),
    "IH-012": ("test_runtime.SelfTypeRuntimeTests.trait_typed_call_resolves_self_to_trait_receiver",),
    "BL-001": ("S.transitive_trait_behaviour_preserves_generic_result",),
    "BL-002": ("B.generic_trait_implication_correlates_arguments",),
    "BL-003": ("test_compiled_modules.CompiledGenericTraitImplementationTests.compiled_generic_trait_implication_preserves_correlation",),
    "BL-004": ("test_trait_implementation_imports.TraitImplementationEndToEndDispatchTests.generic_transitive_behaviour_dispatch_runs_and_retains_definition",),
    "BL-006": ("test_trait_implementation_imports.TraitToTraitImplementationTests.trait_implication_chains_are_cycle_safe",),
    "BL-007": ("B.competing_trait_implication_providers_are_ambiguous",),
    "BL-009": ("B.generic_trait_implication_enforces_constraints",),
    "BL-011": ("test_trait_implementation_imports.TraitImplementationEvidencePathTests.competing_trait_implication_paths_are_retained",),
    "BL-012": ("test_trait_implementation_imports.TraitToTraitImplementationTests.owned_trait_implication_is_imported_from_its_subject",),
    "BL-014": ("test_trait_implementation_imports.TraitToTraitImplementationTests.explicit_trait_behaviour_set_import_is_supported",),
    "AN-005": ("R.structural_witness_instantiates_method_generics",),
    "AN-006": ("S.anonymous_trait_rejects_incompatible_method",),
    "AN-011": ("P.generic_find",),
    "AN-012": ("R.structural_witness_uses_imported_nominal_implementation_evidence",),
    "AN-014": ("T.anonymous_trait_alpha_renaming_is_semantics_preserving",),
    "AN-015": ("test_structural_types.AnonymousTraitTests.trait_satisfaction_is_independent_of_candidate_order",),
    "GN-002": ("S.generic_trait_rejects_wrong_instantiation",),
    "GN-005": ("S.generic_trait_keeps_independent_instantiations",),
    "GN-009": ("S.concrete_generic_trait_implementation_respects_variance",),
    "GN-014": ("test_structural_analysis.StructuralTypeAnalysisTests.anonymous_trait_rejects_incoherent_shared_generic",),
    "GN-016": ("T.anonymous_trait_alpha_renaming_is_semantics_preserving",),
    "OV-020": ("A.where_assertion_rejects_current_overload",),
    "UC-002": ("T.union_function_coverage_requires_every_branch",),
    "UC-003": ("T.union_function_coverage_requires_unambiguous_branches",),
    "UC-004": ("T.union_function_coverage_rejects_overlapping_tag_dispatch",),
    "UC-005": ("R.union_dispatch_uses_statically_selected_broad_branch",),
    "UC-007": ("R.union_dispatch_accepts_broad_numeric_overload",),
    "UC-008": ("R.union_dispatch_uses_reified_disjoint_data_tags",),
    "UC-009": ("R.map_dispatches_overloaded_functions_across_union_items",),
    "UC-013": ("D.generic_union_dispatch_specializes_from_source_free_modules",),
    "UC-014": ("test_runtime.OverloadResolutionRuntimeMatrixTests.union_callback_keeps_static_branch_winners",),
    "UC-015": ("R.union_dispatch_uses_linked_ids_after_reordering_bodies",),
    "IM-001": ("test_trait_implementation_imports.TraitImplementationVisibilityAndWitnessTests.object_owned_external_trait_imports_automatically",),
    "IM-002": ("test_trait_implementation_imports.TraitImplementationVisibilityAndWitnessTests.foreign_object_implementation_requires_explicit_import",),
    "IM-003": ("I.explicit_import_installs_trait_implementation",),
    "IM-004": ("I.trait_argument_reports_ambiguous_behaviour_sets",),
    "IM-005": ("I.multiple_behaviour_sets_can_dispatch_through_qualified_casts",),
    "IM-006": ("I.multiple_behaviour_sets_can_dispatch_through_qualified_casts",),
    "IM-007": ("I.multiple_behaviour_sets_expose_qualified_elements",),
    "IM-008": ("I.missing_trait_implementation_is_an_error",),
    "IM-010": ("I.block_scoped_behaviour_set_does_not_escape",),
    "IM-012": ("I.namespace_import_does_not_install_trait_implementation",),
    "IM-015": ("test_public_reexports.PublicReExportTests.public_trait_implementation_import_reexports_relationship",),
    "IM-019": ("I.explicit_import_installs_trait_implementation",),
    "IM-020": ("test_trait_implementation_imports.TraitImplementationVisibilityAndWitnessTests.private_endpoint_blocks_explicit_behaviour_import",),
    "IM-024": ("test_compiled_modules.CompiledGenericTraitImplementationTests.generic_trait_implication_round_trips_without_source",),
    "CA-009": ("V.nullary_and_payload_members_are_both_executed",),
    "CA-010": ("C.user_trait_patterns_use_reified_implementation_facts",),
    "TG-011": ("test_analyser.TraitElementTagConformanceTests.trait_implementation_accepts_exact_declared_element_tags",),
    "TG-013": ("A.err_type_variant_marks_members_and_parent_as_err",),
    "IN-013": ("test_correctness_rescan.RealWorldProgramTests.trait_fleet_workflow_survives_bytecode_round_trip",),
    "IN-019": ("test_runtime.OverloadResolutionRuntimeMatrixTests.multimethod_specializations_and_fallback_survive_round_trip",),
}


def plan_rows():
    """Read identifiers, priorities and oracles as data from the checked-in plan."""
    rows = {}
    for line in PLAN.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| \*\*([A-Z]{2}-\d{3})\*\* \| (P[012]) \| ([^|]+) \|", line)
        if match:
            identifier, priority, oracle = match.groups()
            rows[identifier] = {"priority": priority, "oracle": oracle.strip()}
    return rows


def coverage():
    """Combine existing named tests with separately discoverable source fixtures."""
    result = {}
    for identifier, references in EXISTING.items():
        expanded = []
        for reference in references:
            owner, method = reference.rsplit(".", 1)
            expanded.append(ALIASES.get(owner, owner) + ".test_" + method)
        result[identifier] = tuple(expanded)
    for path in sorted((ROOT / "tests").glob("test_trait_plan_*.py")):
        if path.stem == "test_trait_plan_coverage":
            continue
        module = importlib.import_module(path.stem)
        for name, cls in vars(module).items():
            if not isinstance(cls, type) or not issubclass(cls, unittest.TestCase) or cls.__module__ != module.__name__:
                continue
            for method in unittest.defaultTestLoader.getTestCaseNames(cls):
                match = re.fullmatch(r"test_([A-Z]{2})_(\d{3})", method)
                if match:
                    identifier = "-".join(match.groups())
                    if identifier in result:
                        raise ValueError(f"duplicate coverage registration: {identifier}")
                    result[identifier] = (f"{path.stem}.{name}.{method}",)
    return result
