import contextlib
import io
import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime_values import LazyList


ROOT = Path(__file__).resolve().parents[1]


def _materialize(value):
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _execute(program):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        stack = run(program)
    return _materialize(stack), output.getvalue()


class SampleProgramRegressionTests(unittest.TestCase):
    def test_checked_in_samples_match_across_execution_modes(self):
        samples = sorted((ROOT / "samples").glob("*.vlnc"))
        self.assertTrue(samples)

        for path in samples:
            with self.subTest(sample=path.name):
                analyser = Analyser()
                typed = analyser.analyse(
                    parse(path.read_text(encoding="utf-8"))
                )
                self.assertEqual(analyser.diagnostics, [])

                executions = []
                for optimize in (False, True):
                    program = compile_program(typed, optimize=optimize)
                    direct = _execute(program)
                    restored = _execute(loads(dumps(program)))
                    self.assertEqual(direct, restored)
                    executions.append(direct)

                self.assertEqual(executions[0], executions[1])


if __name__ == "__main__":
    unittest.main()
