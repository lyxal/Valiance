"""Measure representative Valiance higher-order execution workloads."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from statistics import median
from time import perf_counter

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program


@dataclass(frozen=True, slots=True)
class Workload:
    """One source program used by the higher-order benchmark suite."""

    name: str
    source: str


WORKLOADS = (
    Workload(
        "filter-vector-membership-prefix-needle",
        "range(1, 10000) filter: fn (n) => "
        "[3, 5] | $n | swap | % | 0 | swap | in end sum",
    ),
    Workload(
        "filter-vector-membership-suffix-needle",
        "range(1, 10000) filter: fn (n) => "
        "$n % [3, 5] in swap 0 end sum",
    ),
    Workload(
        "filter-vector-membership-pipeline",
        "range(1, 10000) filter: fn (n) => "
        "[3, 5] | $n | swap | % | 0 | swap | in end sum",
    ),
    Workload(
        "unary-map",
        "range(1, 10000) map: fn (n) => $n % 7 end sum",
    ),
    Workload("binary-reduce", "range(1, 10000) reduce: +"),
    Workload(
        "map-filter-sum",
        "range(1, 10000) "
        "map: fn (n) => $n % 11 end | "
        "filter: fn (n) => $n > 4 end | sum",
    ),
    Workload(
        "straight-line-map",
        "range(1, 10000) "
        "map: fn (n) => ($n * 2) + 1 end | sum",
    ),
    Workload(
        "vectorised-user-function",
        "const $f = fn (n: Int) -> Int => ($n * 2) + 1 end\n"
        "$f(range(1, 10000)) sum",
    ),
    Workload(
        "map-take-sum",
        "range(1, 1000000) "
        "map: fn (n) => $n 2 * end | take 100 | sum",
    ),
    Workload(
        "map-drop-take-sum",
        "range(1, 1000000) "
        "map: fn (n) => $n * 2 end | drop 50 | take 100 | sum",
    ),
    Workload(
        "filter-first",
        "range(1, 1000000) "
        "filter: fn (n) => $n > 100 end | first",
    ),
    Workload(
        "guarded-match-map",
        """range(1, 10000) map fn (n: Int) =>
  match =>
    if % 15 == 0 => "FizzBuzz"
    if % 5 == 0 => "Buzz"
    if % 3 == 0 => "Fizz"
    _ => "${top}"
  end
end | length""",
    ),
    Workload(
        "niladic-map",
        "range(1, 10000) map: fn () => 1 end sum",
    ),
)


def compile_workload(workload: Workload):
    """Compile one workload once so measurements isolate VM execution."""
    analyser = Analyser()
    typed = analyser.analyse(parse(workload.source))
    if analyser.diagnostics:
        raise RuntimeError("; ".join(analyser.diagnostics))
    return compile_program(typed)


def benchmark(workload: Workload, runs: int) -> tuple[float, list[object]]:
    """Return median execution seconds and the final stack for one workload."""
    program = compile_workload(workload)
    timings: list[float] = []
    result: list[object] = []
    for _ in range(runs):
        vm = VirtualMachine(output=lambda _value: None)
        started = perf_counter()
        result = vm.run(program)
        timings.append(perf_counter() - started)
    return median(timings), result


def main() -> None:
    """Run all selected workloads and print stable median execution timings."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--filter", default="")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    selected = tuple(item for item in WORKLOADS if args.filter in item.name)
    if not selected:
        parser.error("no workloads matched --filter")
    for workload in selected:
        elapsed, result = benchmark(workload, args.runs)
        print(f"{workload.name}: {elapsed:.6f}s {result}")


if __name__ == "__main__":
    main()
