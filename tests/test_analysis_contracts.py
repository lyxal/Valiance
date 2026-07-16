"""Boundary tests for declared and inferred language contracts."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.analysis.contracts import ContractAnalyser
from valiance.analysis.contracts import annotations, where_clauses
from valiance.parsing import parse


class ContractBoundaryTests(unittest.TestCase):
    """Verify contract validation is owned outside the analyser façade."""

    def test_analyser_constructs_a_contract_service(self) -> None:
        """The façade exposes the dedicated contract-validation subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.contracts, ContractAnalyser)
        self.assertTrue(analyser.contracts.provides("_validate_data_tags"))
        self.assertTrue(analyser.contracts.provides("_validate_object_lifecycle"))
        self.assertTrue(analyser.contracts.provides("_validate_annotations"))

    def test_data_tag_validation_is_owned_by_contracts(self) -> None:
        """Impossible tag depth remains a contract diagnostic."""
        analyser = Analyser()
        analyser.analyse(parse("tag #nested as constructed\n1 #nested+"))
        self.assertTrue(analyser.diagnostics)
        self.assertIn("has depth 1", analyser.diagnostics[0])
        self.assertIn("has rank 0", analyser.diagnostics[0])

    def test_annotation_registry_lives_under_contracts(self) -> None:
        """Annotation specifications remain available from the contracts package."""
        self.assertTrue(hasattr(annotations, "AnnotationSpec"))
        self.assertTrue(hasattr(annotations, "register_annotation"))

    def test_where_clause_helpers_live_under_contracts(self) -> None:
        """Static where-clause rank operations use their contract module."""
        self.assertTrue(hasattr(where_clauses, "evaluate_where_clause"))
        self.assertTrue(hasattr(where_clauses, "substitute_rank_variables"))

    def test_contract_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve contract-service context delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("tag #checked as computed"))
        self.assertEqual(analyser.diagnostics, [])

    def test_tag_handlers_live_under_contracts(self) -> None:
        """Tag declaration and application continue to emit valid typed nodes."""
        analyser = Analyser()
        analyser.analyse(
            parse("tag #checked as computed\ndefine #checked(value: Number) -> #boolean Number => true\n1 #checked")
        )
        self.assertEqual(analyser.diagnostics, [])

    def test_where_clause_module_moved_from_analysis_root(self) -> None:
        """The old where-clause implementation path has been removed."""
        root = Path(__file__).parents[1] / "src" / "valiance" / "analysis"
        self.assertFalse((root / "where_clause.py").exists())


if __name__ == "__main__":
    unittest.main()
