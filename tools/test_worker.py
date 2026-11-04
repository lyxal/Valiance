"""Internal persistent worker used by :mod:`tools.test_suite`."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import unittest
from collections.abc import Sequence
from pathlib import Path


class TimedTestResult(unittest.TextTestResult):
    """Optionally collect per-test timings without changing unittest outcomes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._started: dict[int, float] = {}
        self.timings: list[tuple[str, str, float]] = []

    def startTest(self, test):
        self._started[id(test)] = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        elapsed = time.perf_counter() - self._started.pop(id(test))
        module = sys.modules.get(type(test).__module__)
        source = Path(module.__file__) if module and hasattr(module, "__file__") else None
        if source is not None:
            try:
                filename = source.resolve().relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                filename = source.name
            name = f"{source.stem}.{type(test).__qualname__}.{test._testMethodName}"
            self.timings.append((filename, name, elapsed))
        super().stopTest(test)


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
    parser.add_argument("--timings-file", type=Path)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for index, path in enumerate(args.files):
        suite.addTests(loader.loadTestsFromModule(_load_module(path, index)))
    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        resultclass=TimedTestResult if args.timings_file else None,
    )
    result = runner.run(suite)
    if args.timings_file:
        modules: dict[str, float] = {}
        slow_tests = []
        for filename, name, elapsed in result.timings:
            modules[filename] = modules.get(filename, 0.0) + elapsed
            slow_tests.append({"name": name, "seconds": elapsed})
        args.timings_file.write_text(
            json.dumps({"modules": modules, "tests": slow_tests}),
            encoding="utf-8",
        )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
