"""Replay a bounded family slice across selected multi-module graph/artifact states.

This is a human-readable complement to the seeded, batched CI campaigns:
family start/stop are half-open indices into the stable insertion-ordered
registry.  Each result records its exact family/graph/layout, seed, and
number of independently checked execution modes.

Examples:
  python -m tools.multimodule_matrix_slices --start 0 --stop 12 --layout source
  python -m tools.multimodule_matrix_slices --start 12 --stop 24 --layout compiled --graphs nested,alias
  python -m tools.multimodule_matrix_slices --negative --start 0 --stop 12
  python -m tools.multimodule_matrix_slices --start 41 --stop 52 --layout compiled --case-timeout 12
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from tools.multimodule_interactions import (
    GRAPHS, INVALID_INTERACTIONS, LAYOUTS, SPECS,
    check_invalid_interaction, execute_interaction, interaction_case,
)


def _execute_case(family: str, graph: str, layout: str, seed: int,
                  negative: bool) -> dict:
    if negative:
        diagnostic = check_invalid_interaction(family, graph, seed, layout)
        return {"family": family, "graph": graph, "layout": layout,
                "seed": seed, "diagnostic": diagnostic}
    count = execute_interaction(interaction_case(family, graph, seed), layout)
    return {"family": family, "graph": graph, "layout": layout,
            "seed": seed, "execution_checks": count}


def _execute_isolated(family: str, graph: str, layout: str, seed: int,
                      negative: bool, case_timeout: float) -> dict:
    """Every finite candidate gets a separate, bounded process lifetime.

    The worker command has no artifact outputs and cannot recursively spawn.
    A crash, timeout, or malformed worker record is a test *failure*.
    """
    argv = [sys.executable, "-m", "tools.multimodule_matrix_slices",
            "--worker", "--family", family, "--graph", graph,
            "--layout", layout, "--seed", str(seed)]
    if negative:
        argv.append("--negative")
    completed = subprocess.run(argv, capture_output=True, text=True,
                               timeout=case_timeout)
    if completed.returncode:
        raise RuntimeError(f"worker exited {completed.returncode}: "
                           f"{completed.stderr[-1600:]} {completed.stdout[-800:]}")
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or result.get("family") != family or \
       result.get("graph") != graph or result.get("layout") != layout:
        raise AssertionError("worker returned incorrect case identity")
    if negative and not result.get("diagnostic"):
        raise AssertionError("worker reported no negative diagnostic")
    if not negative and result.get("execution_checks") != 4:
        raise AssertionError("worker did not verify all four execution modes")
    return result


def run_slice(*, start: int, stop: int, seed: int, graphs: tuple[str, ...],
              layout: str, negative: bool, case_timeout: float | None = None) -> dict:
    registry = INVALID_INTERACTIONS if negative else SPECS
    if start < 0 or stop < start or stop > len(registry):
        raise ValueError("invalid half-open family slice")
    if not graphs or any(graph not in GRAPHS for graph in graphs):
        raise ValueError("unknown graph")
    if layout not in LAYOUTS:
        raise ValueError("unknown artifact layout")
    begun = time.monotonic()
    successes, failures = [], []
    for family in tuple(registry)[start:stop]:
        for graph in graphs:
            try:
                result = (_execute_isolated(family, graph, layout, seed, negative,
                                            case_timeout)
                          if case_timeout is not None
                          else _execute_case(family, graph, layout, seed, negative))
            except Exception as error:
                failures.append({"family": family, "graph": graph, "layout": layout,
                                 "seed": seed, "error": repr(error)})
            else:
                successes.append(result)
    return {
        "claim": "Executed family slice, not exhaustive global language testing",
        "start": start, "stop": stop, "seed": seed, "graphs": list(graphs),
        "layout": layout, "negative": negative, "case_timeout_seconds": case_timeout,
        "families_available": len(registry),
        "projects_attempted": (stop - start) * len(graphs),
        "projects_passed": len(successes), "projects_failed": len(failures),
        "case_timeouts": sum("TimeoutExpired" in row["error"] for row in failures),
        "execution_checks_passed": (0 if negative else sum(x["execution_checks"] for x in successes)),
        "diagnostics_passed": (len(successes) if negative else 0),
        "elapsed_seconds": round(time.monotonic() - begun, 2),
        "passed": successes, "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--case-timeout", type=float,
                        help="isolate each case in a subprocess with this deadline in seconds")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--family", help=argparse.SUPPRESS)
    parser.add_argument("--graph", help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, default=907)
    parser.add_argument("--graphs", default=",".join(GRAPHS))
    parser.add_argument("--layout", choices=LAYOUTS, default="source")
    parser.add_argument("--negative", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.family is None or args.graph is None or args.case_timeout is not None:
            parser.error("worker requires family/graph and cannot set case timeout")
        try:
            report = _execute_case(args.family, args.graph, args.layout, args.seed,
                                   args.negative)
        except Exception as error:
            print(repr(error), file=sys.stderr)
            return 1
        print(json.dumps(report))
        return 0
    if args.start is None or args.stop is None:
        parser.error("--start and --stop are required")
    if args.case_timeout is not None and args.case_timeout <= 0:
        parser.error("--case-timeout must be positive")
    report = run_slice(start=args.start, stop=args.stop, seed=args.seed,
                       graphs=tuple(args.graphs.split(",")), layout=args.layout,
                       negative=args.negative, case_timeout=args.case_timeout)
    result = json.dumps(report, sort_keys=True, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n", encoding="utf-8")
    print(result)
    return int(bool(report["projects_failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
