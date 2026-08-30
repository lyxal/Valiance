"""Internal persistent worker used by :mod:`tools.test_suite`."""

from __future__ import annotations

import argparse
import importlib.util
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path


def _load_module(path: Path, index: int):
    name = f"_valiance_test_worker_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load test module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for index, path in enumerate(args.files):
        suite.addTests(loader.loadTestsFromModule(_load_module(path, index)))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
