"""Feedback-driven source fuzzing with measured analyser/VM line-transition arcs.

This is a portable Python tracing campaign, not native bytecode edge coverage.
Seeds remain typed by construction; the independent source oracle checks each
candidate before its arcs can be admitted to the retained corpus.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import FrameType
from typing import Any

from tools.invariant_fuzzing import (
    SOURCE_FAMILIES,
    TypedSourceCase,
    check_typed_case,
    mutate_typed_case,
    random_typed_case,
    typed_case,
)

_SOURCE_ROOT = (Path(__file__).resolve().parents[1] / "src" / "valiance").as_posix() + "/"


class ArcTracer:
    """Collect per-frame line arcs from the real semantic and runtime modules."""

    def __init__(self) -> None:
        self.arcs: set[tuple[str, int, int]] = set()
        self._previous: dict[FrameType, int] = {}

    def trace(self, frame: FrameType, event: str, _arg: Any):
        path = frame.f_code.co_filename.replace("\\", "/")
        if not path.startswith(_SOURCE_ROOT):
            return None
        relative = path[len(_SOURCE_ROOT):]
        if not (relative.startswith("analysis/") or relative.startswith("runtime/")):
            return None
        if event == "call":
            self._previous[frame] = -1
            return self.trace
        if event == "line":
            previous = self._previous.get(frame, -1)
            current = frame.f_lineno
            self.arcs.add((relative, previous, current))
            self._previous[frame] = current
        elif event in ("return", "exception"):
            if event == "return":
                self._previous.pop(frame, None)
        return self.trace


@lru_cache(maxsize=1)
def syntactic_branch_sites() -> frozenset[tuple[str, int]]:
    """Inventory source branch *sites*, including ones the campaign never hit.

    This is a syntactic upper bound, not a list of feasible control-flow edges;
    one reached ``if`` line can still have an untested true/false edge.
    """
    root = Path(_SOURCE_ROOT)
    sites = set()
    branch_nodes = (ast.If, ast.IfExp, ast.Match, ast.For, ast.AsyncFor, ast.While, ast.Try)
    for area in ("analysis", "runtime"):
        for path in sorted((root / area).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            relative = path.relative_to(root).as_posix()
            sites.update(
                (relative, node.lineno)
                for node in ast.walk(tree)
                if isinstance(node, branch_nodes)
            )
    return frozenset(sites)


@dataclass(frozen=True, slots=True)
class CoverageResult:
    seed: int
    iterations: int
    arcs: frozenset[tuple[str, int, int]]
    corpus: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a stable, portable report with retained replayable sources."""
        by_area: dict[str, int] = {}
        for path, _from, _to in self.arcs:
            area = "analyser" if path.startswith("analysis/") else "runtime"
            by_area[area] = by_area.get(area, 0) + 1
        sites = syntactic_branch_sites()
        reached_lines = {(path, destination) for path, _, destination in self.arcs}
        unvisited = sites - reached_lines
        return {
            "seed": self.seed,
            "iterations": self.iterations,
            "observed_arcs": len(self.arcs),
            "arcs_by_area": dict(sorted(by_area.items())),
            "syntactic_branch_sites": len(sites),
            "visited_branch_sites": len(sites) - len(unvisited),
            "unvisited_branch_sites": len(unvisited),
            "unvisited_branch_site_sample": [list(site) for site in sorted(unvisited)[:80]],
            "arcs": [list(arc) for arc in sorted(self.arcs)],
            "corpus": list(self.corpus),
        }


def run_coverage_campaign(*, seed: int = 0xC0FE, iterations: int = 40) -> CoverageResult:
    """Retain inputs that introduce new arcs and mutate those winners next.

    Replay deterministically from the initial seed: corpus selection depends on
    the preceding instrumentation feedback, not an isolated iteration index.
    """
    if iterations < 0:
        raise ValueError("iterations must be non-negative")
    rng = random.Random(seed)
    retained: list[TypedSourceCase] = []
    reports: list[dict[str, Any]] = []
    covered: set[tuple[str, int, int]] = set()
    for iteration in range(iterations):
        if iteration < len(SOURCE_FAMILIES):
            # Guaranteed minimal diversity before feedback starts steering.
            family = SOURCE_FAMILIES[iteration]
            count = 2 if family == "generic-call" else 4
            case = typed_case(family, tuple(rng.randint(-10, 10) for _ in range(count)))
        elif retained and rng.randrange(5) != 0:
            case = mutate_typed_case(rng, rng.choice(retained))
        else:
            case = random_typed_case(rng)
        tracer = ArcTracer()
        previous = sys.gettrace()
        try:
            sys.settrace(tracer.trace)
            check_typed_case(case, all_modes=True)
        except BaseException as exc:
            raise AssertionError(
                f"coverage campaign seed={seed} iteration={iteration} "
                f"family={case.family} values={case.values} source={case.source!r}"
            ) from exc
        finally:
            sys.settrace(previous)
        additions = tracer.arcs - covered
        if additions:
            covered.update(additions)
            retained.append(case)
            reports.append({
                "iteration": iteration,
                "family": case.family,
                "values": list(case.values),
                "source": case.source,
                "expected": list(case.expected),
                "new_arcs": len(additions),
            })
    return CoverageResult(seed, iterations, frozenset(covered), tuple(reports))


def replay_report(path: Path) -> int:
    """Re-execute the exact retained corpus from a saved JSON campaign report."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    for winner in payload["corpus"]:
        case = typed_case(winner["family"], tuple(winner["values"]))
        if case.source != winner["source"] or list(case.expected) != winner["expected"]:
            raise AssertionError(f"saved corpus source/model mismatch: {winner!r}")
        check_typed_case(case)
    return len(payload["corpus"])


def main(argv: list[str] | None = None) -> int:
    """Run a deterministic corpus-improving campaign and save its exact inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0xC0FE)
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--replay", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.replay is not None:
        count = replay_report(args.replay)
        print(f"PASS retained corpus replay: cases={count}")
        return 0
    report = run_coverage_campaign(seed=args.seed, iterations=args.iterations)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8"
        )
    print(
        f"PASS feedback coverage: seed={args.seed} cases={args.iterations} "
        f"arcs={len(report.arcs)} retained={len(report.corpus)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
