"""Run the 351-row generics hardening inventory against executable oracles.

Examples:
  python -m tools.generics_plan --smoke --report generics-results.json
  python -m tools.generics_plan --priority P0 --report generics-results.json
  python -m tools.generics_plan --case GD-001 --case CP-004
  python -m tools.generics_plan --list

Unimplemented inventory rows are reported as *uncovered*, never as passing.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = set("""
GD-001 GD-002 GD-009 UN-001 UN-006 UN-007 UN-008 UN-013 UN-016
CP-001 CP-004 CP-006 CP-009 CP-013 CP-018 OV-001 OV-006 OV-011
IN-001 IN-005 IN-009 OB-001 OB-009 OB-010 TB-002 TB-004
RK-003 RK-005 RK-007 FV-003 FV-010 CS-001 CS-012
WH-001 WH-007 WH-010 WH-016 UC-001 UC-003 UC-004
IM-001 IM-006 RT-002 RT-004 RT-007 CX-003 CX-006 CX-009
""".split())


def main(argv=None) -> int:
    """Execute only registered tests and preserve uncovered/deferred evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--suite", action="append", default=[])
    parser.add_argument("--priority", choices=("P0", "P1", "P2"), action="append", default=[])
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--list", action="store_true", help="show mapped and unmapped inventory")
    parser.add_argument("--report", type=Path, help="JSON inventory, outcomes and diagnostics")
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per isolated test")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--batch", action="store_true",
                        help="run mapped tests in one process (faster; no per-test timeout or isolation)")
    parser.add_argument("--jobs", type=int, default=1,
                        help="concurrent isolated processes (default: 1)")
    parser.add_argument("--strict-coverage", action="store_true",
                        help="also fail if a selected plan row lacks an oracle")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    if args.fail_fast and args.jobs > 1:
        parser.error("--fail-fast requires --jobs 1")
    if args.batch and args.jobs != 1:
        parser.error("--batch cannot be combined with --jobs > 1")
    sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src"), str(ROOT)]
    from generics_plan_registry import DECISIONS, coverage, plan_rows

    rows, registered = plan_rows(), coverage()
    unknown = set(args.case) - set(rows)
    if unknown:
        parser.error("unknown cases: " + ", ".join(sorted(unknown)))
    unknown_suites = set(args.suite) - {identifier.split("-")[0] for identifier in rows}
    if unknown_suites:
        parser.error("unknown suites: " + ", ".join(sorted(unknown_suites)))

    selected = [identifier for identifier, row in rows.items()
                if (not args.case or identifier in args.case)
                and (not args.suite or identifier.split("-")[0] in args.suite)
                and (not args.priority or row["priority"] in args.priority)
                and (not args.smoke or identifier in SMOKE)]
    if not selected:
        parser.error("selection contains no cases")
    if args.list:
        for identifier in selected:
            print(identifier, rows[identifier]["priority"],
                  "mapped" if identifier in registered else "uncovered",
                  "[Decision]" if identifier in DECISIONS else "")
        return 0

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT / "tests"), str(ROOT)))
    env["PYTHONIOENCODING"] = "utf-8"
    def execute(reference):
        begin = time.monotonic()
        try:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", reference, "-q"],
                cwd=ROOT, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=args.timeout,
            )
            status = "passed" if result.returncode == 0 else "failed"
            diagnostics = result.stdout + result.stderr
        except subprocess.TimeoutExpired as exc:
            status, diagnostics = "timed_out", f"{reference} exceeded {args.timeout}s: {exc}"
        return {
            "status": status, "seconds": round(time.monotonic() - begin, 3),
            "diagnostics": diagnostics,
        }

    ordered_references = list(dict.fromkeys(
        reference for identifier in selected
        for reference in registered.get(identifier, ())
    ))
    outcomes = {}
    if args.batch:
        loader = unittest.TestLoader()
        for reference in ordered_references:
            begin = time.monotonic()
            test = loader.loadTestsFromName(reference)
            result = unittest.TestResult()
            test.run(result)
            errors = result.errors + result.failures + result.unexpectedSuccesses
            diagnostics = "\n".join(
                error if isinstance(error, str) else error[1]
                for error in errors
            )
            if result.skipped:
                diagnostics += "\n" + "\n".join(
                    f"skipped: {reason}" for _, reason in result.skipped
                )
            status = "passed" if result.wasSuccessful() and not result.skipped and result.testsRun == 1 else "failed"
            outcomes[reference] = {
                "status": status, "seconds": round(time.monotonic() - begin, 3),
                "diagnostics": diagnostics,
            }
            if args.verbose or status != "passed":
                print(f"{status}: {reference}", flush=True)
                if status != "passed":
                    print(diagnostics, flush=True)
            if args.fail_fast and status != "passed":
                break
    elif args.jobs == 1:
        for reference in ordered_references:
            outcome = execute(reference)
            outcomes[reference] = outcome
            if args.verbose or outcome["status"] != "passed":
                print(f"{outcome['status']}: {reference}", flush=True)
                if outcome["status"] != "passed":
                    print("\n".join(outcome["diagnostics"].splitlines()[-25:]), flush=True)
            if args.fail_fast and outcome["status"] != "passed":
                break
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(execute, ref): ref for ref in ordered_references}
            for future in as_completed(futures):
                reference = futures[future]
                outcome = future.result()
                outcomes[reference] = outcome
                if args.verbose or outcome["status"] != "passed":
                    print(f"{outcome['status']}: {reference}", flush=True)
                    if outcome["status"] != "passed":
                        print("\n".join(outcome["diagnostics"].splitlines()[-25:]), flush=True)

    cases = {}
    for identifier, spec in rows.items():
        references = registered.get(identifier, ())
        if identifier not in selected:
            status = "not_selected"
        elif not references:
            status = "uncovered"
        elif any(ref not in outcomes for ref in references):
            status = "not_run"
        elif all(outcomes[ref]["status"] == "passed" for ref in references):
            status = "passed"
        elif any(outcomes[ref]["status"] == "timed_out" for ref in references):
            status = "timed_out"
        else:
            status = "failed"
        cases[identifier] = {
            **spec, "status": status, "tests": references,
            **({"decision": DECISIONS[identifier]} if identifier in DECISIONS else {}),
        }
    summary = {
        "inventory": len(rows),
        "mode": "in_process_batch" if args.batch else "isolated_process",
        "selected": len(selected),
        "mapped_selected": sum(bool(registered.get(identifier)) for identifier in selected),
        "passed": sum(item["status"] == "passed" for item in cases.values()),
        "failed": sum(item["status"] in ("failed", "timed_out") for item in cases.values()),
        "uncovered_selected": sum(item["status"] == "uncovered" for item in cases.values()),
        "mapped_inventory": len(registered),
        "uncovered_inventory": len(rows) - len(registered),
        "unique_tests_executed": len(outcomes),
    }
    print("Generics plan: "
          f"{summary['passed']}/{summary['mapped_selected']} mapped cases passed; "
          f"{summary['uncovered_selected']} selected cases uncovered; "
          f"{summary['uncovered_inventory']} uncovered across 351; "
          f"{len(outcomes)} unique tests executed.")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"summary": summary, "cases": cases,
                                           "tests": outcomes}, indent=2), encoding="utf-8")
    return int(summary["failed"] != 0 or
               any(item["status"] == "not_run" for item in cases.values()) or
               (args.strict_coverage and summary["uncovered_selected"] != 0))


if __name__ == "__main__":
    raise SystemExit(main())
