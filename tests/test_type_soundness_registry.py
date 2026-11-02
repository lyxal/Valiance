"""Prevent the counterexample catalog from silently losing or miscounting rows."""
import unittest
from type_soundness_registry import registry

class TypeSoundnessRegistryTests(unittest.TestCase):
    def test_all_seventy_risks_and_nine_sentinels_tracked(self):
        entries = registry()
        self.assertEqual(len(entries), 79)
        self.assertEqual(len([e for e in entries if e.pack == 'X']), 9)
        self.assertEqual(sum(e.test_name is not None for e in entries), 79)

    def test_tracked_unimplemented_cases_not_credited_as_covered(self):
        entries = registry()
        pending = {e.id for e in entries if e.test_name is None}
        self.assertEqual(pending, set())
        # Unsupported recursive type aliases have explicit N/A test oracles.
        self.assertNotIn('U01', pending)
        self.assertNotIn('X03', pending)
