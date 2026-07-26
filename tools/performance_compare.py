"""Compare two Valiance checkouts on one host with alternating benchmark passes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from tools.performance import classify_report_changes, merge_reports


def _run_checkout(
    checkout: Path,
    output: Path,
    *,
    runs: int,
    warmups: int,
    filters: tuple[str, ...],
) -> dict[str, Any]:
    """Run the benchmark module in one checkout and return its JSON report."""
    command = [
        sys.executable,
        "-m",
        "tools.performance",
        "--runs",
        str(runs),
        "--warmups",
        str(warmups),
        "--output",
        str(output),
    ]
    for pattern in filters:
        command.extend(("--filter", pattern))
    environment = os.environ.copy()
    source_path = str(checkout / "src")
    root_path = str(checkout)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, root_path, existing) if part
    )
    subprocess.run(command, cwd=checkout, env=environment, check=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _calibration_drift(baseline: dict[str, Any], candidate: dict[str, Any]) -> float:
    """Return absolute relative calibration drift between two merged reports."""
    left = baseline["calibration"]["median_seconds"]
    right = candidate["calibration"]["median_seconds"]
    return abs(right - left) / left if left else float("inf")


def main(argv: list[str] | None = None) -> int:
    """Run alternating passes, rerun borderline results, and emit merged reports."""
    parser = argparse.ArgumentParser(
        description="Compare two Valiance checkouts on the same benchmark host."
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--borderline-reruns", type=int, default=1)
    parser.add_argument("--filter", action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path(".performance/paired"))
    parser.add_argument("--regression-limit", type=float, default=0.15)
    parser.add_argument("--absolute-tolerance", type=float, default=0.002)
    parser.add_argument("--calibration-limit", type=float, default=0.20)
    args = parser.parse_args(argv)
    for checkout in (args.baseline, args.candidate):
        if not (checkout / "tools" / "performance.py").is_file():
            parser.error(f"{checkout} is not a Valiance checkout with tools/performance.py")
    if args.runs < 1 or args.passes < 1 or args.warmups < 0:
        parser.error("runs and passes must be positive; warmups must be non-negative")
    if args.borderline_reruns < 0:
        parser.error("--borderline-reruns must be non-negative")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline_reports: list[dict[str, Any]] = []
    candidate_reports: list[dict[str, Any]] = []
    filters = tuple(args.filter)

    def run_pass(index: int) -> None:
        """Run one balanced pass, reversing order on every other pass."""
        order = (
            (("baseline", args.baseline), ("candidate", args.candidate))
            if index % 2 == 0
            else (("candidate", args.candidate), ("baseline", args.baseline))
        )
        for label, checkout in order:
            output = args.output_dir / f"{label}-pass-{index + 1}.json"
            report = _run_checkout(
                checkout,
                output,
                runs=args.runs,
                warmups=args.warmups,
                filters=filters,
            )
            (baseline_reports if label == "baseline" else candidate_reports).append(
                report
            )

    for index in range(args.passes):
        run_pass(index)

    reruns_remaining = args.borderline_reruns
    while True:
        baseline = merge_reports(baseline_reports)
        candidate = merge_reports(candidate_reports)
        findings = classify_report_changes(
            baseline,
            candidate,
            limit=args.regression_limit,
            absolute_tolerance=args.absolute_tolerance,
        )
        borderline = [item for item in findings if item["status"] == "borderline"]
        if not borderline or reruns_remaining == 0:
            break
        run_pass(len(baseline_reports))
        reruns_remaining -= 1

    baseline_path = args.output_dir / "baseline-merged.json"
    candidate_path = args.output_dir / "candidate-merged.json"
    baseline_path.write_text(
        json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    candidate_path.write_text(
        json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    drift = _calibration_drift(baseline, candidate)
    regressions = [item for item in findings if item["status"] == "regression"]
    summary = {
        "calibration_drift": drift,
        "calibration_status": "unstable" if drift > args.calibration_limit else "stable",
        "findings": findings,
        "baseline_report": str(baseline_path),
        "candidate_report": str(candidate_path),
    }
    summary_path = args.output_dir / "comparison.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if drift > args.calibration_limit:
        return 2
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
