"""Tests for the deterministic performance baseline and comparison tooling."""

from __future__ import annotations

import unittest

from tools.performance import (
    PerformanceWorkload,
    benchmark_workload,
    compare_reports,
)


class PerformanceToolTests(unittest.TestCase):
    """Protect report structure and regression threshold semantics."""

    def test_benchmark_records_every_pipeline_stage(self):
        """Produce stable stage, result, bytecode, and instrumentation metadata."""
        result = benchmark_workload(
            PerformanceWorkload("tiny", "test", "1 2 +"),
            runs=1,
            warmups=0,
        )

        self.assertEqual(
            set(result["stages"]),
            {"parse", "analyse", "compile", "serialize", "load", "execute", "end_to_end"},
        )
        self.assertGreater(result["bytecode_bytes"], 0)
        self.assertGreater(result["instruction_count"], 0)
        self.assertEqual(len(result["result_sha256"]), 64)

    def test_comparison_requires_relative_and_absolute_regression(self):
        """Ignore timer noise but report a material same-source slowdown."""
        baseline = self._report(0.010)
        noisy = self._report(0.011)
        regressed = self._report(0.030)

        self.assertEqual(
            compare_reports(baseline, noisy, limit=0.05, absolute_tolerance=0.002),
            [],
        )
        findings = compare_reports(
            baseline,
            regressed,
            limit=0.05,
            absolute_tolerance=0.002,
        )
        self.assertTrue(
            any(item["workload"] == "tiny" and item["stage"] == "execute" for item in findings)
        )

    @staticmethod
    def _report(seconds: float) -> dict:
        """Build a minimal valid report with one measured stage."""
        return {
            "schema_version": 1,
            "workloads": [
                {
                    "name": "tiny",
                    "source_sha256": "source",
                    "result_sha256": "result",
                    "stages": {"execute": {"median_seconds": seconds}},
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
