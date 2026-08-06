"""CLI for replayable deterministic concurrency fuzz campaigns."""

from __future__ import annotations

import argparse

from tools.concurrency_fuzzing import ConcurrencyFuzzConfig, run_concurrency_fuzz


def main(argv: list[str] | None = None) -> int:
    """Run a bounded campaign selected entirely by command-line replay data."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0xC0_2026)
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--operations", type=int, default=64)
    args = parser.parse_args(argv)
    result = run_concurrency_fuzz(ConcurrencyFuzzConfig(**vars(args)))
    print(
        f"PASS concurrency: seed={result.seed} start={result.start} "
        f"iterations={result.iterations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
