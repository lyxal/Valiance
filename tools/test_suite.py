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
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TestBatch:
    """A group of test files assigned to one worker."""

    files: tuple[Path, ...]
    estimated_weight: int


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


def partition_test_files(files: Sequence[Path], workers: int) -> tuple[TestBatch, ...]:
    """Greedily balance estimated work while keeping each worker long-lived."""
    if workers < 1:
        raise ValueError("workers must be at least 1")
    slots: list[list[Path]] = [[] for _ in range(min(workers, len(files)))]
    weights = [0 for _ in slots]
    weighted = sorted(((_test_weight(path), path) for path in files), reverse=True)
    for weight, path in weighted:
        index = min(range(len(slots)), key=weights.__getitem__)
        slots[index].append(path)
        weights[index] += weight
    return tuple(
        TestBatch(tuple(sorted(batch)), weight)
        for batch, weight in zip(slots, weights, strict=True)
        if batch
    )


def _worker_command(files: Sequence[Path], *, verbose: bool) -> list[str]:
    command = [sys.executable, "-m", "tools.test_worker"]
    if verbose:
        command.append("--verbose")
    command.extend(str(path) for path in files)
    return command


def _run_batch(
    index: int, batch: TestBatch, *, verbose: bool, env: dict[str, str]
) -> tuple[int, TestBatch, int, str]:
    completed = subprocess.run(
        _worker_command(batch.files, verbose=verbose),
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
) -> int:
    """Run balanced batches through a bounded dynamic worker queue."""
    started = time.perf_counter()
    env = os.environ.copy()
    root = Path.cwd()
    pythonpath = [str(root / "src"), str(root)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    results: dict[int, tuple[TestBatch, int, str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(_run_batch, index, batch, verbose=verbose, env=env): index
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workers < 1:
        print("error: --workers must be at least 1", file=sys.stderr)
        return 2
    files = discover_test_files(args.test_dir, args.pattern or ["test_*.py"])
    if not files:
        print("error: no test modules matched", file=sys.stderr)
        return 2
    # More batches than workers give the scheduler room to compensate when a
    # module's true runtime differs from its cheap static estimate. Each batch
    # still amortizes interpreter startup across several modules.
    batches = partition_test_files(files, min(len(files), args.workers * 4))
    if args.list:
        for index, batch in enumerate(batches, start=1):
            print(f"worker {index} (weight {batch.estimated_weight}):")
            for path in batch.files:
                print(f"  {path}")
        return 0
    return run_batches(
        batches, workers=args.workers, verbose=args.verbose, fail_fast=args.fail_fast
    )


if __name__ == "__main__":
    raise SystemExit(main())
