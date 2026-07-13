import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime_values import LazyList, Number

ROOT = Path(__file__).resolve().parents[1]


def _materialize(value):
    if isinstance(value, LazyList):
        value = list(value)
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


class OverloadMarkerIntegrationTests(unittest.TestCase):
    def test_risk_scoring_workload_survives_optimization_and_serialization(self):
        source = (ROOT / "samples" / "RiskScoring.vlnc").read_text(encoding="utf-8")
        analyser = Analyser()
        typed = analyser.analyse(parse(source))

        self.assertEqual(analyser.diagnostics, [])
        expected = [
            [Number("13"), Number("7"), Number("4")],
            ["high", "medium", "low"],
        ]
        programs = (
            (False, compile_program(typed, optimize=False)),
            (True, compile_program(typed, optimize=True)),
        )
        for optimized, program in programs:
            with self.subTest(optimized=optimized):
                self.assertEqual(_materialize(run(program)), expected)
                self.assertEqual(_materialize(run(loads(dumps(program)))), expected)

    def test_first_class_generic_atomic_callable_enforces_rank(self):
        scalar_source = """
$f = fn[T] (values: T atomic +) -> T+ => $values end
$f([1, 2, 3])
"""
        analyser = Analyser()
        typed = analyser.analyse(parse(scalar_source))

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            _materialize(run(compile_program(typed))),
            [[Number("1"), Number("2"), Number("3")]],
        )

        matrix_analyser = Analyser()
        matrix_analyser.analyse(parse("""
$f = fn[T] (values: T atomic +) -> T+ => $values end
$f([[1, 2], [3, 4]])
"""))
        self.assertEqual(len(matrix_analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'call' match",
            matrix_analyser.diagnostics[0],
        )

    def test_rank_one_generic_rejects_matrix_at_call_site(self):
        analyser = Analyser()

        analyser.analyse(parse("""
define[T] rankOneCopy(values: T atomic +) -> T+ => $values end
[[1, 2], [3, 4]] rankOneCopy
"""))

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn(
            "no overloads for element 'rankOneCopy' match",
            analyser.diagnostics[0],
        )


if __name__ == "__main__":
    unittest.main()
