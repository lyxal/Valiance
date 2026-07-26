"""Run repeatable end-to-end Valiance performance baselines and comparisons."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import resource
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Iterable

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program, dumps, loads
from valiance.runtime.bytecode import FunctionCode, FunctionSetCode, Program

SCHEMA_VERSION = 1
DEFAULT_REGRESSION_LIMIT = 0.15
DEFAULT_ABSOLUTE_TOLERANCE = 0.002


@dataclass(frozen=True, slots=True)
class PerformanceWorkload:
    """One deterministic source program and its benchmark category."""

    name: str
    category: str
    source: str


@dataclass(frozen=True, slots=True)
class StageMeasurement:
    """Robust timing and memory summary for one measured pipeline stage."""

    median_seconds: float
    minimum_seconds: float
    samples_seconds: tuple[float, ...]
    peak_rss_kib: int


WORKLOADS = (
    PerformanceWorkload("startup-small", "startup", "1 2 +"),
    PerformanceWorkload(
        "arithmetic-loop",
        "numeric",
        "range(1, 10000) fold: +",
    ),
    PerformanceWorkload(
        "lazy-pipeline",
        "higher-order",
        "range(1, 10000) map: fn (n) => ($n * 2) + 1 end | "
        "filter: fn (n) => $n % 3 == 0 end | sum",
    ),
    PerformanceWorkload(
        "vectorised-user-function",
        "vectorisation",
        "const $f = fn (n: Integer) -> Integer => ($n * 2) + 1 end\n"
        "$f(range(1, 10000)) sum",
    ),
    PerformanceWorkload(
        "guarded-classification",
        "control-flow",
        """range(1, 10000) map fn (n: Integer) =>
  match =>
    if % 15 == 0 => "FizzBuzz"
    if % 5 == 0 => "Buzz"
    if % 3 == 0 => "Fizz"
    _ => "${top}"
  end
end | length""",
    ),
    PerformanceWorkload(
        "string-processing",
        "text",
        'range(1, 3000) map: fn (n) => "item-${$n}" end | "," join | length',
    ),
    PerformanceWorkload(
        "object-construction",
        "objects",
        """object Point =>
  public $x: Integer
  public $y: Integer
end
range(1, 3000) map: fn (n) => Point($n, $n + 1) end | length""",
    ),
    PerformanceWorkload(
        "stdlib-import-analysis",
        "imports",
        """import { std.arithmetic.square, std.arithmetic.cube }
range(1, 2000) map: fn (n) => square($n) + cube($n) end | sum""",
    ),
    PerformanceWorkload(
        "nested-collections",
        "collections",
        "[[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11, 12, 13, 14, 15, 16, 17, 18, 19, 20], [21, 22, 23, 24, 25, 26, 27, 28, 29, 30], [31, 32, 33, 34, 35, 36, 37, 38, 39, 40], [41, 42, 43, 44, 45, 46, 47, 48, 49, 50], [51, 52, 53, 54, 55, 56, 57, 58, 59, 60], [61, 62, 63, 64, 65, 66, 67, 68, 69, 70], [71, 72, 73, 74, 75, 76, 77, 78, 79, 80], [81, 82, 83, 84, 85, 86, 87, 88, 89, 90], [91, 92, 93, 94, 95, 96, 97, 98, 99, 100]] map: fn (row) => $row sum end | sum",
    ),
    PerformanceWorkload(
        "recursive-function",
        "calls",
        """$sumTo = @recursive fn (n: Integer, total: Integer) -> Integer =>
  if $n == 0 => $total
  else => this($n - 1, $total + $n)
  end
end
$sumTo(4000, 0)""",
    ),
)


def _peak_rss_kib() -> int:
    """Return the process peak resident-set size in KiB on supported hosts."""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(value // 1024)
    return int(value)


def _instruction_count(code: FunctionCode) -> int:
    """Count instructions recursively across nested function payloads."""
    total = len(code.instructions)
    for instruction in code.instructions:
        payload = instruction.arg
        if isinstance(payload, FunctionCode):
            total += _instruction_count(payload)
        elif isinstance(payload, FunctionSetCode):
            total += sum(_instruction_count(item) for item in payload.overloads)
    return total


def _measure(operation: Callable[[], Any], runs: int) -> tuple[StageMeasurement, Any]:
    """Measure an operation repeatedly and retain the final successful result."""
    samples: list[float] = []
    result: Any = None
    gc.collect()
    for _ in range(runs):
        started = perf_counter()
        result = operation()
        samples.append(perf_counter() - started)
    return (
        StageMeasurement(
            statistics.median(samples),
            min(samples),
            tuple(samples),
            _peak_rss_kib(),
        ),
        result,
    )


def _analyse(nodes: list[Any]) -> list[Any]:
    """Analyse parsed nodes and reject benchmark sources with diagnostics."""
    analyser = Analyser()
    typed = analyser.analyse(nodes)
    if analyser.diagnostics:
        raise RuntimeError("; ".join(analyser.diagnostics))
    return typed


def _result_digest(value: object) -> str:
    """Create a stable compact identity for benchmark result validation."""
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def benchmark_workload(
    workload: PerformanceWorkload,
    *,
    runs: int,
    warmups: int,
) -> dict[str, Any]:
    """Measure each compiler/runtime stage and an uncached end-to-end pass."""
    source = workload.source
    parsed = parse(source)
    typed = _analyse(parsed)
    program = compile_program(typed)
    encoded = dumps(program)
    restored = loads(encoded)

    for _ in range(warmups):
        VirtualMachine(output=lambda _value: None).run(restored)

    parse_measurement, parsed = _measure(lambda: parse(source), runs)
    analysis_measurement, typed = _measure(lambda: _analyse(parsed), runs)
    compile_measurement, program = _measure(lambda: compile_program(typed), runs)
    serialize_measurement, encoded = _measure(lambda: dumps(program), runs)
    load_measurement, restored = _measure(lambda: loads(encoded), runs)

    def execute() -> list[Any]:
        """Execute in a fresh VM so retained runtime state cannot cross samples."""
        return VirtualMachine(output=lambda _value: None).run(restored)

    execution_measurement, result = _measure(execute, runs)

    def end_to_end() -> list[Any]:
        """Run the complete cold compiler and runtime pipeline once."""
        nodes = parse(source)
        analysed = _analyse(nodes)
        compiled = compile_program(analysed)
        round_tripped = loads(dumps(compiled))
        return VirtualMachine(output=lambda _value: None).run(round_tripped)

    end_to_end_measurement, end_result = _measure(end_to_end, runs)
    if result != end_result:
        raise RuntimeError(f"{workload.name}: staged and end-to-end results differ")

    stats_vm = VirtualMachine(
        output=lambda _value: None,
        collect_optimization_stats=True,
    )
    stats_result = stats_vm.run(restored)
    if stats_result != result:
        raise RuntimeError(f"{workload.name}: instrumented execution changed result")
    assert stats_vm.optimization_stats is not None

    stages = {
        "parse": asdict(parse_measurement),
        "analyse": asdict(analysis_measurement),
        "compile": asdict(compile_measurement),
        "serialize": asdict(serialize_measurement),
        "load": asdict(load_measurement),
        "execute": asdict(execution_measurement),
        "end_to_end": asdict(end_to_end_measurement),
    }
    return {
        "name": workload.name,
        "category": workload.category,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "result_sha256": _result_digest(result),
        "bytecode_bytes": len(encoded),
        "instruction_count": _instruction_count(program.main),
        "stages": stages,
        "optimization_stats": stats_vm.optimization_stats.snapshot(),
    }


def build_report(
    workloads: Iterable[PerformanceWorkload],
    *,
    runs: int,
    warmups: int,
) -> dict[str, Any]:
    """Build a machine-readable performance report for selected workloads."""
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor_count": os.cpu_count(),
        },
        "settings": {"runs": runs, "warmups": warmups},
        "workloads": [
            benchmark_workload(item, runs=runs, warmups=warmups)
            for item in workloads
        ],
    }


def compare_reports(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    limit: float = DEFAULT_REGRESSION_LIMIT,
    absolute_tolerance: float = DEFAULT_ABSOLUTE_TOLERANCE,
) -> list[dict[str, Any]]:
    """Return stage regressions exceeding both relative and absolute tolerances."""
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported baseline schema version")
    baseline_items = {item["name"]: item for item in baseline["workloads"]}
    regressions: list[dict[str, Any]] = []
    for item in current["workloads"]:
        previous = baseline_items.get(item["name"])
        if previous is None:
            continue
        if previous["source_sha256"] != item["source_sha256"]:
            continue
        if previous["result_sha256"] != item["result_sha256"]:
            regressions.append(
                {"workload": item["name"], "stage": "result", "reason": "changed"}
            )
            continue
        for stage, measurement in item["stages"].items():
            old = previous["stages"][stage]["median_seconds"]
            new = measurement["median_seconds"]
            delta = new - old
            ratio = delta / old if old > 0 else float("inf")
            if delta > absolute_tolerance and ratio > limit:
                regressions.append(
                    {
                        "workload": item["name"],
                        "stage": stage,
                        "baseline_seconds": old,
                        "current_seconds": new,
                        "relative_change": ratio,
                    }
                )
    return regressions


def _write_report(report: dict[str, Any], path: Path | None) -> None:
    """Write deterministic JSON to a file or standard output."""
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


def _selected_workloads(patterns: list[str]) -> tuple[PerformanceWorkload, ...]:
    """Select workloads by name or category substring."""
    if not patterns:
        return WORKLOADS
    selected = tuple(
        item
        for item in WORKLOADS
        if any(pattern in item.name or pattern in item.category for pattern in patterns)
    )
    if not selected:
        raise ValueError("no performance workloads matched --filter")
    return selected


def main(argv: list[str] | None = None) -> int:
    """Run the suite, write a report, and optionally enforce a stored baseline."""
    parser = argparse.ArgumentParser(
        description="Measure Valiance pipeline stages and detect regressions."
    )
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--filter", action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--regression-limit", type=float, default=DEFAULT_REGRESSION_LIMIT)
    parser.add_argument(
        "--absolute-tolerance",
        type=float,
        default=DEFAULT_ABSOLUTE_TOLERANCE,
    )
    args = parser.parse_args(argv)
    if args.runs < 1 or args.warmups < 0:
        parser.error("--runs must be positive and --warmups non-negative")
    if args.regression_limit < 0 or args.absolute_tolerance < 0:
        parser.error("regression tolerances must be non-negative")
    try:
        selected = _selected_workloads(args.filter)
    except ValueError as exc:
        parser.error(str(exc))
    report = build_report(selected, runs=args.runs, warmups=args.warmups)
    _write_report(report, args.output)
    if args.compare is None:
        return 0
    baseline = json.loads(args.compare.read_text(encoding="utf-8"))
    regressions = compare_reports(
        baseline,
        report,
        limit=args.regression_limit,
        absolute_tolerance=args.absolute_tolerance,
    )
    if not regressions:
        print("PASS: no performance regressions exceeded the configured tolerances")
        return 0
    print(json.dumps({"regressions": regressions}, indent=2, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
