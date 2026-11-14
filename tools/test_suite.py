"""Run the unittest suite in balanced, isolated worker processes.

The standard unittest discovery command runs every module in one process. That
makes the wall clock time the sum of several mostly independent compiler and
runtime suites. This runner balances modules into worker subprocesses. Tests
that explicitly opt in can also be scheduled by method, without skipping the
rest of their module.
"""

from __future__ import annotations

import ast
import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

from tools.test_worker import timing_module_key


@dataclass(frozen=True)
class TestTarget:
    """Run a whole module, one test method, or everything but selected methods."""

    path: Path
    method: str | None = None
    exclude: tuple[str, ...] = ()

    def argument(self) -> str:
        if self.method is not None:
            return f"{self.path}::{self.method}"
        if self.exclude:
            return f"{self.path}::!{','.join(self.exclude)}"
        return str(self.path)


@dataclass(frozen=True)
class TestBatch:
    """A group of disjoint test targets assigned to one worker."""

    targets: tuple[TestTarget, ...]
    estimated_weight: float

    @property
    def files(self) -> tuple[Path, ...]:
        """Paths represented in this batch (not necessarily complete modules)."""
        return tuple(target.path for target in self.targets)


def _parallel_safe_methods(path: Path) -> tuple[str, ...]:
    """Read a module's explicit opt-in without importing its test dependencies.

    Tests using global state or class fixtures must not opt in: each selected
    method may execute in a different process from the remaining methods.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    methods = {
        f"{node.name}.{child.name}"
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for child in node.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        and child.name.startswith("test_")
    }
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        if targets[0].id != "PARALLEL_SAFE_METHODS":
            continue
        declared = ast.literal_eval(node.value)
        if (
            not isinstance(declared, tuple)
            or any(not isinstance(name, str) for name in declared)
            or len(set(declared)) != len(declared)
        ):
            raise ValueError(
                f"{path}: PARALLEL_SAFE_METHODS must be unique method names"
            )
        missing = set(declared) - methods
        if missing:
            raise ValueError(f"{path}: missing parallel-safe methods: {sorted(missing)}")
        return declared
    return ()


def _test_weight(path: Path) -> int:
    """Estimate runtime from test count and source size without importing it."""
    source = path.read_text(encoding="utf-8")
    test_count = source.count("def test_")
    # Test count is the strongest cheap signal. Source size helps distinguish
    # large generated/program cases from small assertion-heavy modules.
    return max(1, test_count * 10 + len(source) // 2_000)


def discover_test_files(
    test_dir: Path, patterns: Sequence[str], suite: str = "all"
) -> tuple[Path, ...]:
    """Return sorted test files matching any supplied glob and the selected suite."""
    found: set[Path] = set()
    for pattern in patterns:
        found.update(path for path in test_dir.glob(pattern) if path.is_file())
    if suite != "all":
        standard_library = {
            path for path in found
            if path.name.startswith(("test_std_", "test_stdlib_"))
            or path.name == "test_filesystem_core.py"
        }
        found = standard_library if suite == "standard-library" else found - standard_library
    return tuple(sorted(found))


def partition_test_files(
    files: Sequence[Path],
    workers: int,
    timings: Mapping[str, float] | None = None,
    test_timings: Mapping[str, float] | None = None,
) -> tuple[TestBatch, ...]:
    """Balance whole modules and explicitly opted-in method-level targets.

    Opted-in methods execute in their own selections. All other tests from
    that module remain in an exclusion-filtered selection, so new tests are
    still discovered and run without changing the runner's configuration.
    """
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if not files:
        return ()
    estimates = {path: _test_weight(path) for path in files}
    module_keys = {path: timing_module_key(path) for path in files}
    # Older reports may use absolute paths or Windows path separators.
    module_timings = {Path(key).as_posix(): value for key, value in (timings or {}).items()}
    observed = {}
    for path, key in module_keys.items():
        seconds = module_timings.get(path.as_posix(), module_timings.get(key))
        if type(seconds) in (int, float) and math.isfinite(seconds) and seconds > 0:
            observed[path] = seconds
    scale = (
        median(observed[path] / estimates[path] for path in observed)
        if observed
        else 1.0
    )
    weighted: list[tuple[float, TestTarget]] = []
    for path in files:
        total = observed.get(path, estimates[path] * scale)
        selected = _parallel_safe_methods(path)
        if not selected:
            weighted.append((total, TestTarget(path)))
            continue

        # Per-method measurements are available after the first run with the
        # v2 timing report. Earlier reports still supply useful module totals.
        measured: dict[str, float] = {}
        for name in selected:
            value = None if test_timings is None else test_timings.get(
                f"{path.as_posix()}::{name}", test_timings.get(f"{module_keys[path]}::{name}"),
            )
            if type(value) in (int, float) and math.isfinite(value) and value > 0:
                measured[name] = value
        missing_slots = len(selected) + 1 - len(measured)
        # Preserve a positive allowance for the rest of the module and for
        # tests with missing per-method history.
        unmeasured_weight = max(
            total - sum(measured.values()), total / (len(selected) + 1) * 0.1
        ) / missing_slots
        for name in selected:
            weighted.append(
                (measured.get(name, unmeasured_weight), TestTarget(path, method=name))
            )
        weighted.append((unmeasured_weight, TestTarget(path, exclude=selected)))

    slots: list[list[TestTarget]] = [[] for _ in range(min(workers, len(weighted)))]
    weights = [0.0 for _ in slots]
    for weight, target in sorted(weighted, key=lambda row: row[0], reverse=True):
        index = min(range(len(slots)), key=weights.__getitem__)
        slots[index].append(target)
        weights[index] += weight
    return tuple(
        TestBatch(tuple(batch), weight)
        for batch, weight in zip(slots, weights, strict=True)
        if batch
    )


def _worker_command(
    targets: Sequence[TestTarget], *, verbose: bool, timings_file: Path | None = None
) -> list[str]:
    command = [sys.executable, "-m", "tools.test_worker"]
    if verbose:
        command.append("--verbose")
    if timings_file is not None:
        command.extend(("--timings-file", str(timings_file)))
    command.extend(target.argument() for target in targets)
    return command


def _run_batch(
    index: int,
    batch: TestBatch,
    *,
    verbose: bool,
    env: dict[str, str],
    timings_file: Path | None = None,
) -> tuple[int, TestBatch, int, str]:
    selections = ", ".join(target.argument() for target in batch.targets)
    print(f"worker batch {index} starting: {selections}", flush=True)
    completed = subprocess.run(
        _worker_command(batch.targets, verbose=verbose, timings_file=timings_file),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    return index, batch, completed.returncode, completed.stdout


def run_batches(
    batches: Sequence[TestBatch],
    *,
    workers: int,
    verbose: bool,
    fail_fast: bool,
    timings_file: Path | None = None,
    durations: int = 0,
) -> int:
    """Run balanced batches through a bounded dynamic worker queue."""
    started = time.perf_counter()
    env = os.environ.copy()
    root = Path.cwd()
    # Several test modules import shared fixtures (such as ffi_support)
    # directly from tests/. Parallel workers must match ordinary test discovery.
    pythonpath = [str(root / "src"), str(root / "tests"), str(root)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    results: dict[int, tuple[TestBatch, int, str]] = {}
    measured_modules: dict[str, float] = {}
    measured_tests: list[dict[str, str | float]] = []
    measured_test_durations: dict[str, float] = {}
    # Worker-side measurements capture individual tests, not merely total batch
    # times, so subsequent runs can distribute expensive modules more evenly.
    with TemporaryDirectory() as directory:
        timing_dir = Path(directory) if timings_file or durations else None
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {
                executor.submit(
                    _run_batch,
                    index,
                    batch,
                    verbose=verbose,
                    env=env,
                    **({"timings_file": timing_dir / f"{index}.json"} if timing_dir else {}),
                ): index
                for index, batch in enumerate(batches, start=1)
            }
            for future in as_completed(pending):
                if future.cancelled():
                    continue
                index, batch, returncode, output = future.result()
                results[index] = (batch, returncode, output)
                status = "FAILED" if returncode else "passed"
                print(
                    f"worker batch {index}/{len(batches)}: {status} "
                    f"({len(set(batch.files))} modules, {len(batch.targets)} selections)",
                    flush=True,
                )
                if timing_dir is not None:
                    path = timing_dir / f"{index}.json"
                    if path.is_file():
                        data = json.loads(path.read_text(encoding="utf-8"))
                        for module, seconds in data["modules"].items():
                            measured_modules[module] = measured_modules.get(module, 0.0) + seconds
                        measured_tests.extend(data["tests"])
                        measured_test_durations.update(data.get("test_durations", {}))
                if timings_file is not None:
                    timings_file.parent.mkdir(parents=True, exist_ok=True)
                    checkpoint = timings_file.with_suffix(timings_file.suffix + ".tmp")
                    checkpoint.write_text(
                        json.dumps(
                            {"version": 2, "modules": measured_modules,
                             "tests": measured_test_durations},
                            indent=2,
                        ) + "\n",
                        encoding="utf-8",
                    )
                    checkpoint.replace(timings_file)
                if returncode and fail_fast:
                    for other in pending:
                        other.cancel()
        if durations:
            print(f"\nSlowest {durations} individual tests (excluding class/module setup):")
            slowest = sorted(
                measured_tests, key=lambda row: row["seconds"], reverse=True
            )[:durations]
            for item in slowest:
                print(f"  {item['seconds']:8.3f}s  {item['name']}")
            print("\nSlowest test modules (summed test durations):")
            modules_by_duration = sorted(
                measured_modules.items(), key=lambda row: row[1], reverse=True
            )[:10]
            for name, seconds in modules_by_duration:
                print(f"  {seconds:8.3f}s  {name}")

    failed = any(returncode for _, returncode, _ in results.values())
    for index in sorted(results):
        batch, returncode, output = results[index]
        if not (verbose or returncode):
            continue
        status = "FAILED" if returncode else "passed"
        print(f"\n=== batch {index}: {status} ===")
        print(output.rstrip())

    elapsed = time.perf_counter() - started
    print(f"\nParallel suite {'FAILED' if failed else 'passed'} in {elapsed:.2f}s.")
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help="number of persistent worker processes (default: up to 4)",
    )
    parser.add_argument(
        "--pattern",
        action="append",
        default=[],
        help="test file glob; repeat for multiple patterns (default: test_*.py)",
    )
    parser.add_argument("--test-dir", type=Path, default=Path("tests"))
    parser.add_argument("--shards", type=int, default=1, help="number of CI shards")
    parser.add_argument("--shard", type=int, default=0, help="zero-based CI shard index")
    parser.add_argument(
        "--suite",
        choices=("all", "language-core", "standard-library"),
        default="all",
        help="select a test suite (default: all)",
    )
    parser.add_argument("--list", action="store_true", help="show worker allocation only")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--timings",
        type=Path,
        help="reuse previous module/method timings and save new timings",
    )
    parser.add_argument(
        "--durations",
        type=int,
        default=0,
        metavar="N",
        help="print the N slowest tests and 10 slowest modules",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not 0 <= args.shard < args.shards:
        parser.error("--shard must be between 0 and --shards minus one")
    if args.workers < 1 or args.durations < 0:
        print(
            "error: --workers must be at least 1 and --durations nonnegative",
            file=sys.stderr,
        )
        return 2
    files = discover_test_files(args.test_dir, args.pattern or ["test_*.py"], args.suite)
    if not files:
        print("error: no test modules matched", file=sys.stderr)
        return 2
    # More batches than workers give the scheduler room to compensate when a
    # module's true runtime differs from its cheap static estimate. Each batch
    # still amortizes interpreter startup across several modules.
    timings = None
    test_timings = None
    # Shards must agree on membership even when their timing reports differ.
    if args.shards == 1 and args.timings and args.timings.is_file():
        try:
            data = json.loads(args.timings.read_text(encoding="utf-8"))
            if data["version"] not in (1, 2) or not isinstance(data["modules"], dict):
                raise ValueError("unrecognized timings format")
            timings = data["modules"]
            test_timings = data.get("tests", {}) if data["version"] == 2 else None
            if test_timings is not None and not isinstance(test_timings, dict):
                raise ValueError("invalid per-test timings")
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(
                f"warning: cannot read timings ({error}); using estimates",
                file=sys.stderr,
            )
    try:
        batches = partition_test_files(
            files, args.workers * 4 * args.shards, timings, test_timings
        )
        if args.shards > 1:
            batches = tuple(sorted(
                batches, key=lambda batch: batch.estimated_weight, reverse=True
            ))[args.shard::args.shards]
    except (OSError, SyntaxError, ValueError) as error:
        print(f"error: cannot schedule tests ({error})", file=sys.stderr)
        return 2
    if args.list:
        for index, batch in enumerate(batches, start=1):
            print(f"worker {index} (weight {batch.estimated_weight}):")
            for target in batch.targets:
                print(f"  {target.argument()}")
        return 0
    return run_batches(
        batches,
        workers=args.workers,
        verbose=args.verbose,
        fail_fast=args.fail_fast,
        timings_file=args.timings,
        durations=args.durations,
    )


if __name__ == "__main__":
    raise SystemExit(main())
