"""Tests for the balanced parallel unittest runner."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.test_suite import discover_test_files, partition_test_files


class ParallelTestSuiteTests(unittest.TestCase):
    def test_discovery_deduplicates_overlapping_patterns(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_one.py").write_text("", encoding="utf-8")
            (root / "helper.py").write_text("", encoding="utf-8")
            found = discover_test_files(root, ("test_*.py", "*.py"))
        self.assertEqual([path.name for path in found], ["helper.py", "test_one.py"])

    def test_partition_assigns_every_file_once_and_balances_heavy_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for index, count in enumerate((20, 10, 5, 1)):
                path = root / f"test_{index}.py"
                path.write_text("\n".join(f"def test_{n}(): pass" for n in range(count)))
                files.append(path)
            batches = partition_test_files(files, 2)
        assigned = [path for batch in batches for path in batch.files]
        self.assertCountEqual(assigned, files)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertLessEqual(
            abs(batches[0].estimated_weight - batches[1].estimated_weight), 60
        )

    def test_partition_rejects_zero_workers(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            partition_test_files((), 0)
