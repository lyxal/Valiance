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


def timing_module_key(source: Path) -> str:
    """Name a timed module relative to the project when possible."""
    try:
        return source.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return source.name


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
            filename = timing_module_key(source)
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


def _test_name(test: unittest.TestCase) -> str:
    return f"{type(test).__qualname__}.{test._testMethodName}"


def _flatten(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _flatten(test)
        else:
            yield test


def _target(argument: str) -> tuple[Path, str | None, tuple[str, ...]]:
    """Decode a module selection; unqualified modules retain full discovery."""
    filename, sep, selector = argument.partition("::")
    if not filename or (sep and not selector):
        raise ValueError(f"invalid test target: {argument}")
    if not sep:
        return Path(filename), None, ()
    if selector.startswith("!"):
        excluded = tuple(selector[1:].split(","))
        if not all(excluded):
            raise ValueError(f"invalid exclusions: {argument}")
        return Path(filename), None, excluded
    return Path(filename), selector, ()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--timings-file", type=Path)
    parser.add_argument("files", nargs="+", help="module paths with optional ::Class.method selector")
    args = parser.parse_args(argv)

    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    loaded: dict[Path, list[unittest.TestCase]] = {}
    for index, argument in enumerate(args.files):
        path, method, excluded = _target(argument)
        if path not in loaded:
            loaded[path] = list(_flatten(loader.loadTestsFromModule(_load_module(path, index))))
        tests = loaded[path]
        if method is not None:
            matching = [test for test in tests if _test_name(test) == method]
            if len(matching) != 1:
                raise ValueError(f"{path}: expected one test named {method}, found {len(matching)}")
            suite.addTests(matching)
        elif excluded:
            suite.addTests(test for test in tests if _test_name(test) not in excluded)
        else:
            suite.addTests(tests)
    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        resultclass=TimedTestResult if args.timings_file else None,
    )
    result = runner.run(suite)
    if args.timings_file:
        modules: dict[str, float] = {}
        slow_tests = []
        test_durations: dict[str, float] = {}
        for filename, name, elapsed in result.timings:
            modules[filename] = modules.get(filename, 0.0) + elapsed
            slow_tests.append({"name": name, "seconds": elapsed})
            test_id = f"{filename}::{name.removeprefix(Path(filename).stem + '.')}"
            test_durations[test_id] = elapsed
        args.timings_file.write_text(
            json.dumps({"modules": modules, "tests": slow_tests,
                        "test_durations": test_durations}),
            encoding="utf-8",
        )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
