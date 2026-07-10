"""Command-line entry point for deterministic Valiance fuzzing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tools.fuzzing import (  # noqa: E402
    DEFAULT_ITERATIONS,
    DEFAULT_SEED,
    FuzzConfig,
    FuzzFailure,
    TARGETS,
    run_targets,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run deterministic fuzz targets against Valiance.",
    )
    parser.add_argument(
        "--target",
        action="append",
        choices=tuple(TARGETS) + ("all",),
        help="target to run; repeat for several targets (default: all)",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-source-length", type=int, default=192)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse command-line options, run fuzzers, and report reproducible failures."""
    args = _parser().parse_args(argv)
    selected = args.target or ["all"]
    names = list(TARGETS) if "all" in selected else list(dict.fromkeys(selected))
    config = FuzzConfig(
        seed=args.seed,
        iterations=args.iterations,
        start=args.start,
        max_depth=args.max_depth,
        max_source_length=args.max_source_length,
    )

    try:
        stats = run_targets(names, config)
    except FuzzFailure as exc:
        print(exc, file=sys.stderr)
        return 1

    for result in stats:
        print(
            f"PASS {result.target}: seed={result.seed} "
            f"iterations={result.iterations} start={result.start}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
