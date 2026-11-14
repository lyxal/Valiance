"""Tests for the balanced parallel unittest runner."""

import os
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess, TimeoutExpired

from tools.test_suite import (
    TestBatch,
    TestTarget,
    discover_test_files,
    partition_test_files,
    run_batches,
)


class ParallelTestSuiteTests(unittest.TestCase):
    def test_suite_selection_partitions_matching_modules(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            core_names = ["test_parser.py", "test_programs.py"]
            library_names = [
                "test_filesystem_core.py", "test_std_web.py", "test_stdlib_native.py",
            ]
            for name in core_names + library_names:
                (root / name).touch()
            for suite, names in (
                ("all", core_names + library_names),
                ("language-core", core_names),
                ("standard-library", library_names),
            ):
                with self.subTest(suite=suite):
                    found = discover_test_files(root, ("test_*.py",), suite)
                    self.assertEqual([path.name for path in found], sorted(names))
            self.assertEqual(
                discover_test_files(root, ("test_std_web*.py",), "standard-library"),
                (root / "test_std_web.py",),
            )

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

    def test_measured_durations_override_static_test_counts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            large = root / "test_large.py"
            small = root / "test_small.py"
            large.write_text("\n".join(f"def test_{i}(): pass" for i in range(20)))
            small.write_text("def test_one(): pass")
            durations = {small.as_posix(): 40.0, large.as_posix(): 1.0}
            batches = partition_test_files((large, small), 2, durations)
        self.assertEqual(batches[0].files, (small,))
        self.assertEqual(batches[0].estimated_weight, 40.0)

    def test_partial_and_invalid_timings_fall_back_to_scaled_estimates(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            measured = root / "test_measured.py"
            missing = root / "test_missing.py"
            measured.write_text("def test_one(): pass")
            missing.write_text("def test_two(): pass")
            batches = partition_test_files(
                (measured, missing), 2,
                {measured.as_posix(): 2.0, missing.as_posix(): "invalid"},
            )
        self.assertEqual({batch.estimated_weight for batch in batches}, {2.0})

    def test_timing_report_is_saved_by_real_workers(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            case = root / "test_timing_probe.py"
            key = f"{root.name}/test_timing_probe.py"
            case.write_text(
                "import unittest\n"
                "class TimingProbe(unittest.TestCase):\n"
                "    def test_ok(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            report = root / "timings.json"
            with redirect_stdout(StringIO()) as output:
                status = run_batches(
                    (TestBatch((TestTarget(case),), 1),), workers=1, verbose=False,
                    fail_fast=False, timings_file=report, durations=1,
                )
            data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(data["version"], 2)
        self.assertIn(key, data["modules"])
        self.assertEqual(len(data["tests"]), 1)
        self.assertIn(f"{key}::TimingProbe.test_ok", data["tests"])
        self.assertIn("TimingProbe.test_ok", output.getvalue())

    def test_parallel_safe_methods_execute_exactly_once_across_workers(self):
        with TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            case = root / "test_sharded.py"
            key = f"{root.name}/test_sharded.py"
            case.write_text(
                "import unittest\n"
                "PARALLEL_SAFE_METHODS = ('Example.test_slow_a', 'Example.test_slow_b')\n"
                "class Example(unittest.TestCase):\n"
                "    def test_slow_a(self): self.assertTrue(True)\n"
                "    def test_slow_b(self): self.assertTrue(True)\n"
                "    def test_remainder(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            batches = partition_test_files((case,), 3)
            self.assertCountEqual(
                [target.method for batch in batches for target in batch.targets],
                ["Example.test_slow_a", "Example.test_slow_b", None],
            )
            remainder = next(target for batch in batches for target in batch.targets
                             if target.method is None)
            self.assertEqual(remainder.exclude,
                             ("Example.test_slow_a", "Example.test_slow_b"))
            report = root / "timings.json"
            with redirect_stdout(StringIO()):
                status = run_batches(
                    batches, workers=3, verbose=False, fail_fast=False,
                    timings_file=report,
                )
            data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(status, 0)
        self.assertEqual(len(data["tests"]), 3)
        self.assertEqual(set(data["tests"]), {
            f"{key}::Example.test_slow_a",
            f"{key}::Example.test_slow_b",
            f"{key}::Example.test_remainder",
        })
        self.assertEqual(len(data["modules"]), 1)

    def test_measured_methods_are_scheduled_independently(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test_optin.py"
            path.write_text(
                "PARALLEL_SAFE_METHODS = ('Example.test_one', 'Example.test_two')\n"
                "class Example:\n"
                "    def test_one(self): pass\n"
                "    def test_two(self): pass\n"
                "    def test_three(self): pass\n",
                encoding="utf-8",
            )
            batches = partition_test_files(
                (path,), 3, {path.as_posix(): 65},
                {f"{path.as_posix()}::Example.test_one": 45,
                 f"{path.as_posix()}::Example.test_two": 15},
            )
        durations = sorted(batch.estimated_weight for batch in batches)
        self.assertEqual(durations, [5, 15, 45])

    def test_invalid_parallel_opt_in_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test_typo.py"
            path.write_text(
                "PARALLEL_SAFE_METHODS = ('Example.test_missing',)\n"
                "class Example:\n"
                "    def test_present(self): pass\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "missing parallel-safe methods"):
                partition_test_files((path,), 2)

    def test_failing_selected_method_causes_suite_failure(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "test_failure.py"
            path.write_text(
                "import unittest\n"
                "PARALLEL_SAFE_METHODS = ('Example.test_fail',)\n"
                "class Example(unittest.TestCase):\n"
                "    def test_fail(self): self.fail('expected failure')\n"
                "    def test_pass(self): self.assertTrue(True)\n",
                encoding="utf-8",
            )
            batches = partition_test_files((path,), 2)
            with redirect_stdout(StringIO()) as output:
                status = run_batches(batches, workers=2, verbose=False, fail_fast=False)
        self.assertEqual(status, 1)
        self.assertIn("expected failure", output.getvalue())

    def test_v1_timings_remain_compatible(self):
        from tools.test_suite import main
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test_v1.py").write_text("def test_one(): pass\n")
            timings = root / "v1.json"
            timings.write_text(json.dumps({"version": 1, "modules": {
                str(root / "test_v1.py"): 7.0,
            }}))
            with redirect_stdout(StringIO()) as output:
                status = main(["--test-dir", str(root), "--workers", "1",
                               "--timings", str(timings), "--list"])
        self.assertEqual(status, 0)
        self.assertIn("weight 7.0", output.getvalue())

    def test_partition_rejects_zero_workers(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            partition_test_files((), 0)

    def test_worker_search_path_includes_shared_test_fixtures(self):
        batch = TestBatch((TestTarget(Path("tests/test_bytecode_serialization.py")),), 1)
        with patch("tools.test_suite._run_batch", return_value=(1, batch, 0, "")) as worker:
            self.assertEqual(run_batches((batch,), workers=1, verbose=False, fail_fast=False), 0)
        search_path = worker.call_args.kwargs["env"]["PYTHONPATH"].split(os.pathsep)
        self.assertIn(str(Path.cwd() / "tests"), search_path)


class TraitPlanRunnerTests(unittest.TestCase):
    """Check isolated selection, deadlines, and machine-readable outcomes."""

    def run_plan(self, arguments, *, result=None, error=None):
        from tools.trait_plan import main
        rows = {"TD-001": {"priority": "P0"}, "TD-002": {"priority": "P1"},
                "WH-001": {"priority": "P0"}, "LC-003": {"priority": "P2"}}
        coverage = {"TD-001": ("example.shared",), "TD-002": ("example.shared",),
                    "WH-001": ("example.static",)}
        with patch("trait_plan_registry.plan_rows", return_value=rows), \
             patch("trait_plan_registry.coverage", return_value=coverage), \
             patch("trait_plan_registry.RETIRED", {"LC-003": "scrapped"}), \
             patch("tools.trait_plan.subprocess.run", return_value=result or CompletedProcess([], 0, "", ""),
                   side_effect=error) as execute, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = main(arguments)
        return status, execute

    def test_shared_oracles_execute_once(self):
        status, execute = self.run_plan(["--suite", "TD"])
        self.assertEqual(status, 0)
        self.assertEqual(execute.call_count, 1)
        self.assertIn("example.shared", execute.call_args.args[0])

    def test_priority_and_suite_filters_intersect(self):
        status, execute = self.run_plan(["--suite", "TD", "--priority", "P0"])
        self.assertEqual(status, 0)
        self.assertEqual(execute.call_count, 1)

    def test_deadline_is_a_failure_and_is_recorded(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            status, execute = self.run_plan(["--case", "WH-001", "--timeout", "2", "--report", str(report)],
                                           error=TimeoutExpired("unittest", 2))
            data = json.loads(report.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(execute.call_args.kwargs["timeout"], 2)
        self.assertEqual(data["tests"]["example.static"]["status"], "timed_out")
        self.assertEqual(data["retired"], {"LC-003": "scrapped"})

    def test_fail_fast_leaves_unexecuted_cases_visible(self):
        with TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            status, execute = self.run_plan(["--fail-fast", "--report", str(report)],
                                           result=CompletedProcess([], 1, "", "regression"))
            data = json.loads(report.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(data["cases"]["WH-001"]["status"], "not_run")
        self.assertEqual(data["tests"]["example.shared"]["diagnostics"], "regression")

    def test_unknown_case_is_rejected_before_execution(self):
        with self.assertRaises(SystemExit) as caught:
            self.run_plan(["--case", "TD-999"])
        self.assertEqual(caught.exception.code, 2)

    def test_listing_does_not_execute_tests(self):
        status, execute = self.run_plan(["--list"])
        self.assertEqual(status, 0)
        execute.assert_not_called()
