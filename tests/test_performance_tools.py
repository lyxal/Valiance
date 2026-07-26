"""Tests for deterministic performance baselines and paired comparisons."""

from __future__ import annotations

import unittest

from tools.performance import (
    PerformanceWorkload,
    benchmark_workload,
    classify_report_changes,
    compare_reports,
    merge_reports,
)


class PerformanceToolTests(unittest.TestCase):
    """Protect report structure, fixture support, and threshold semantics."""

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
        self.assertIn(
            "median_absolute_deviation_seconds",
            result["stages"]["execute"],
        )

    def test_benchmark_materializes_multi_module_fixture(self):
        """Include local import loading in analysis and end-to-end measurements."""
        result = benchmark_workload(
            PerformanceWorkload(
                "modules",
                "test",
                "import { helper.double }\ndouble(4)",
                (("helper.vlnc", "public define double(n: Integer) -> Integer => $n * 2\n"),),
            ),
            runs=1,
            warmups=0,
        )

        self.assertGreater(result["stages"]["analyse"]["median_seconds"], 0)
        self.assertNotEqual(result["fixture_sha256"], "")

    def test_comparison_distinguishes_noise_borderline_and_regression(self):
        """Classify material slowdowns while ignoring ordinary timer noise."""
        baseline = self._report(0.010)
        noisy = self._report(0.011)
        borderline = self._report(0.014)
        regressed = self._report(0.030)

        self.assertEqual(
            classify_report_changes(
                baseline, noisy, limit=0.50, absolute_tolerance=0.003
            ),
            [],
        )
        self.assertEqual(
            classify_report_changes(
                baseline, borderline, limit=0.50, absolute_tolerance=0.003
            )[0]["status"],
            "borderline",
        )
        findings = compare_reports(
            baseline,
            regressed,
            limit=0.50,
            absolute_tolerance=0.003,
        )
        self.assertTrue(
            any(item["workload"] == "tiny" and item["stage"] == "execute" for item in findings)
        )

    def test_report_merge_pools_samples_and_recomputes_robust_statistics(self):
        """Combine alternating passes without averaging their medians."""
        left = self._report(0.010, samples=[0.009, 0.010, 0.011])
        right = self._report(0.020, samples=[0.019, 0.020, 0.021])

        merged = merge_reports((left, right))
        measurement = merged["workloads"][0]["stages"]["execute"]

        self.assertEqual(len(measurement["samples_seconds"]), 6)
        self.assertAlmostEqual(measurement["median_seconds"], 0.015)
        self.assertGreater(measurement["median_absolute_deviation_seconds"], 0)
        self.assertEqual(merged["settings"]["passes"], 2)

    @staticmethod
    def _report(seconds: float, *, samples: list[float] | None = None) -> dict:
        """Build a minimal schema-v2 report with one measured stage."""
        values = samples or [seconds]
        return {
            "schema_version": 2,
            "settings": {"runs": len(values), "warmups": 0},
            "calibration": {"median_seconds": 0.01},
            "workloads": [
                {
                    "name": "tiny",
                    "source_sha256": "source",
                    "fixture_sha256": "fixture",
                    "result_sha256": "result",
                    "stages": {
                        "execute": {
                            "median_seconds": seconds,
                            "minimum_seconds": min(values),
                            "median_absolute_deviation_seconds": 0.0,
                            "samples_seconds": values,
                            "peak_rss_kib": 1,
                        }
                    },
                }
            ],
        }


if __name__ == "__main__":
    unittest.main()
