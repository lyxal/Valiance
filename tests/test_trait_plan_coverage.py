"""Prevent numbered trait-plan cases from silently losing executable coverage."""

import unittest

from trait_plan_registry import COUNTS, RETIRED, coverage, plan_rows


class TraitPlanCoverageTests(unittest.TestCase):
    def test_every_plan_id_has_runnable_unskipped_tests(self):
        expected = {f'{suite}-{index:03}' for suite, count in COUNTS.items()
                    for index in range(1, count + 1)}
        rows = plan_rows()
        registered = coverage()
        self.assertEqual(len(expected), 309)
        self.assertEqual(set(rows), expected)
        self.assertEqual(set(registered) | set(RETIRED), expected)
        self.assertFalse(set(registered) & set(RETIRED))
        for identifier, references in registered.items():
            self.assertTrue(references, identifier)
            for reference in references:
                with self.subTest(case=identifier, test=reference):
                    loader = unittest.TestLoader()
                    suite = loader.loadTestsFromName(reference)
                    self.assertFalse(loader.errors, loader.errors)
                    self.assertEqual(suite.countTestCases(), 1)
                    case = next(iter(suite))
                    self.assertFalse(getattr(type(case), '__unittest_skip__', False))
                    self.assertFalse(getattr(getattr(case, case._testMethodName), '__unittest_skip__', False))
