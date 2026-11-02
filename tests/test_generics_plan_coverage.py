"""Prevent regressions in the generics hardening inventory and coverage mapping."""

import unittest

from generics_plan_registry import COUNTS, DECISIONS, coverage, plan_rows


class GenericsPlanCoverageTests(unittest.TestCase):
    def test_inventory_matches_all_351_specification_cases(self):
        identifiers = {f"{suite}-{index:03d}" for suite, count in COUNTS.items()
                       for index in range(1, count + 1)}
        rows = plan_rows()
        self.assertEqual(len(rows), 351)
        self.assertEqual(identifiers, set(rows))
        self.assertEqual(sum(row["priority"] == "P0" for row in rows.values()), 221)
        self.assertEqual(sum(row["priority"] == "P1" for row in rows.values()), 103)
        self.assertEqual(sum(row["priority"] == "P2" for row in rows.values()), 27)

    def test_every_mapped_oracle_is_discoverable_and_unskipped(self):
        rows, registered = plan_rows(), coverage()
        self.assertFalse(set(registered) - set(rows))
        self.assertFalse(set(DECISIONS) - set(rows))
        self.assertGreaterEqual(len(registered), 179)
        for identifier, references in registered.items():
            self.assertTrue(references, identifier)
            for ref in references:
                with self.subTest(identifier=identifier, ref=ref):
                    loader = unittest.TestLoader()
                    suite = loader.loadTestsFromName(ref)
                    self.assertFalse(loader.errors, loader.errors)
                    self.assertEqual(suite.countTestCases(), 1)
                    case = next(iter(suite))
                    self.assertFalse(getattr(type(case), "__unittest_skip__", False))
                    self.assertFalse(getattr(getattr(case, case._testMethodName), "__unittest_skip__", False))
