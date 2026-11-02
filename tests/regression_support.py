"""Common execution and expectation helpers for permanent regression fixtures.

These checks are discovered with ordinary ``unittest discover``. A source fixture
has a descriptive name, a behaviour-specific expected outcome, and runs in all
four compiler/bytecode execution configurations.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads
from valiance.runtime.runtime_values import RuntimeNumber, format_runtime_value
from valiance.runtime.vm import RuntimeError as ValianceRuntimeError, VirtualMachine

FIXTURES = Path(__file__).parent / "fixtures" / "regressions"
MODES = ((False, False), (True, False), (False, True), (True, True))


def read_fixture(relative_path: str) -> str:
    return (FIXTURES / relative_path).read_text(encoding="utf-8")


def compile_source(source: str, *, optimize: bool, serialize: bool):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(f"Unexpected analysis diagnostics: {analyser.diagnostics}")
    program = compile_program(typed, optimize=optimize)
    return loads(dumps(program)) if serialize else program


def execute(source: str, optimize: bool = False, serialize: bool = False):
    return VirtualMachine().run(compile_source(source, optimize=optimize, serialize=serialize))


def assert_fixture_cases(testcase, cases: dict[str, tuple[str, str | None]]):
    """Assert exact outcomes, fault classes, or compact square-root properties."""
    for relative_path, (outcome, expected) in cases.items():
        source = read_fixture(relative_path)
        for optimize, serialize in MODES:
            with testcase.subTest(fixture=relative_path, optimize=optimize, serialized=serialize):
                output = StringIO()
                with redirect_stdout(output):
                    if outcome == "fault":
                        with testcase.assertRaisesRegex(ValianceRuntimeError, expected):
                            execute(source, optimize, serialize)
                        continue
                    values = execute(source, optimize, serialize)
                if outcome != "print":
                    testcase.assertEqual(len(values), 1)
                if outcome == "value":
                    testcase.assertEqual([format_runtime_value(v) for v in values], [expected])
                elif outcome == "number":
                    testcase.assertEqual(str(values[0]), expected)
                elif outcome == "print":
                    testcase.assertEqual(values, [])
                    testcase.assertEqual(output.getvalue(), expected)
                elif outcome == "square_root":
                    value, = values
                    testcase.assertIsInstance(value, RuntimeNumber)
                    testcase.assertFalse(value.is_complex())
                    testcase.assertGreater(value.real.coefficient, 0)
                    testcase.assertGreater(value.real.exponent, 1000)
                else:
                    testcase.fail(f"Unknown expectation kind: {outcome}")


def assert_fixture_inventory(testcase, folder: str, cases):
    """Catch accidentally omitted or orphaned Valiance source fixtures."""
    actual = {str(path.relative_to(FIXTURES)) for path in (FIXTURES / folder).glob('*.vlnc')}
    testcase.assertEqual(actual, set(cases))
