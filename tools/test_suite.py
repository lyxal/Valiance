"""Run the unittest suite in balanced, isolated worker processes.

The standard unittest discovery command runs every module in one process.  That
makes the wall clock time the sum of several mostly independent compiler and
runtime suites.  This runner partitions modules into a small number of balanced
batches and executes each batch in its own long-lived subprocess, preserving
normal unittest semantics within every batch while avoiding one interpreter
startup per module.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class TestBatch:
    """A group of test files assigned to one worker."""

    files: tuple[Path, ...]
    estimated_weight: float


def _test_weight(path: Path) -> int:
    """Estimate runtime from test count and source size without importing it."""
    source = path.read_text(encoding="utf-8")
    test_count = source.count("def test_")
    # Test count is the strongest cheap signal. Source size helps distinguish
    # large generated/program cases from small assertion-heavy modules.
    return max(1, test_count * 10 + len(source) // 2_000)


def discover_test_files(test_dir: Path, patterns: Sequence[str]) -> tuple[Path, ...]:
    """Return sorted test files matching any supplied glob pattern."""
    found: set[Path] = set()
    for pattern in patterns:
        found.update(path for path in test_dir.glob(pattern) if path.is_file())
    return tuple(sorted(found))


def partition_test_files(
    files: Sequence[Path], workers: int, timings: Mapping[str, float] | None = None
) -> tuple[TestBatch, ...]:
    """Greedily balance work using observed module durations when available."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    slots: list[list[Path]] = [[] for _ in range(min(workers, len(files)))]
    weights = [0.0 for _ in slots]
    estimates = {path: _test_weight(path) for path in files}
    observed = {
        path: timings[path.as_posix()]
        for path in files
        if timings is not None
        and path.as_posix() in timings
        and type(timings[path.as_posix()]) in (int, float)
        and math.isfinite(timings[path.as_posix()])
        and timings[path.as_posix()] > 0
    }
    # Normalize estimates to measured seconds for new or previously unmeasured files.
    scale = (
        median(observed[path] / estimates[path] for path in observed)
        if observed
        else 1.0
    )
    weighted = sorted(
        ((observed.get(path, estimates[path] * scale), path) for path in files),
        reverse=True,
    )
    for weight, path in weighted:
        index = min(range(len(slots)), key=weights.__getitem__)
        slots[index].append(path)
        weights[index] += weight
    return tuple(
        TestBatch(tuple(sorted(batch)), weight)
        for batch, weight in zip(slots, weights, strict=True)
        if batch
    )


def _worker_command(
    files: Sequence[Path], *, verbose: bool, timings_file: Path | None = None
) -> list[str]:
    command = [sys.executable, "-m", "tools.test_worker"]
    if verbose:
        command.append("--verbose")
    if timings_file is not None:
        command.extend(("--timings-file", str(timings_file)))
    command.extend(str(path) for path in files)
    return command


def _run_batch(
    index: int,
    batch: TestBatch,
    *,
    verbose: bool,
    env: dict[str, str],
    timings_file: Path | None = None,
) -> tuple[int, TestBatch, int, str]:
    completed = subprocess.run(
        _worker_command(batch.files, verbose=verbose, timings_file=timings_file),
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
                index, batch, returncode, output = future.result()
                results[index] = (batch, returncode, output)
                status = "FAILED" if returncode else "passed"
                print(
                    f"worker batch {index}/{len(batches)}: {status} "
                    f"({len(batch.files)} modules)",
                    flush=True,
                )
                if returncode and fail_fast:
                    for other in pending:
                        other.cancel()
                    break
        if timing_dir:
            for path in sorted(timing_dir.glob("*.json")):
                data = json.loads(path.read_text(encoding="utf-8"))
                measured_modules.update(data["modules"])
                measured_tests.extend(data["tests"])
        if timings_file is not None:
            timings_file.parent.mkdir(parents=True, exist_ok=True)
            timings_file.write_text(
                json.dumps({"version": 1, "modules": measured_modules}, indent=2)
                + "\n",
                encoding="utf-8",
            )
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
    parser.add_argument("--list", action="store_true", help="show worker allocation only")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument(
        "--timings",
        type=Path,
        help="read previous module timings and save fresh timings for the next run",
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
    args = _parser().parse_args(argv)
    if args.workers < 1 or args.durations < 0:
        print(
            "error: --workers must be at least 1 and --durations nonnegative",
            file=sys.stderr,
        )
        return 2
    files = discover_test_files(args.test_dir, args.pattern or ["test_*.py"])
    if not files:
        print("error: no test modules matched", file=sys.stderr)
        return 2
    # More batches than workers give the scheduler room to compensate when a
    # module's true runtime differs from its cheap static estimate. Each batch
    # still amortizes interpreter startup across several modules.
    timings = None
    if args.timings and args.timings.is_file():
        try:
            data = json.loads(args.timings.read_text(encoding="utf-8"))
            if data["version"] != 1 or not isinstance(data["modules"], dict):
                raise ValueError("unrecognized timings format")
            timings = data["modules"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"warning: cannot read timings ({error}); using estimates", file=sys.stderr)
    batches = partition_test_files(files, min(len(files), args.workers * 4), timings)
    if args.list:
        for index, batch in enumerate(batches, start=1):
            print(f"worker {index} (weight {batch.estimated_weight}):")
            for path in batch.files:
                print(f"  {path}")
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
