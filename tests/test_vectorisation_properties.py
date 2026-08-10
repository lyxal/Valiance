"""Cross-layer properties for static and runtime vectorisation plans."""

from __future__ import annotations

import contextlib
import io
import unittest
from itertools import product

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime.compiler import compile_program
from valiance.runtime.runtime_values import format_runtime_value
from valiance.runtime.serialization import dumps, loads
from valiance.runtime.vm import VirtualMachine
from valiance.vtypes import (
    AtLeastArray,
    AtLeastList,
    ExactArray,
    ExactList,
    Overload,
    RuggedList,
    U,
    apply_overload,
    assignable,
    same,
)
from valiance.vtypes.default_types import Integer, Number, Real, String


def _analyse(source: str):
    """Analyse valid source and fail with the compiler diagnostics when invalid."""
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return typed


def _execute_mode(
    source: str,
    *,
    optimize: bool,
    serialized: bool,
) -> tuple[tuple[str, ...], str]:
    """Execute one source program under one bytecode mode."""
    program = compile_program(_analyse(source), optimize=optimize)
    if serialized:
        program = loads(dumps(program))
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = VirtualMachine().run(program)
    return tuple(format_runtime_value(value) for value in result), output.getvalue()


def _all_execution_modes(source: str) -> tuple[tuple[tuple[str, ...], str], ...]:
    """Return direct, optimized, serialized, and optimized-serialized results."""
    return tuple(
        _execute_mode(source, optimize=optimize, serialized=serialized)
        for optimize, serialized in (
            (False, False),
            (True, False),
            (False, True),
            (True, True),
        )
    )


class VectorisationPlanPropertyTests(unittest.TestCase):
    """Check metadata invariants over a deterministic type-shape matrix."""

    def test_plan_metadata_is_consistent_across_collection_shapes(self):
        atoms = (Integer, Real, Number, String)
        collections = tuple(
            constructor(atom, rank)
            for atom, rank, constructor in product(
                atoms,
                (1, 2, 3),
                (ExactList, AtLeastList, RuggedList, ExactArray, AtLeastArray),
            )
        )
        arguments = (
            *atoms,
            *collections,
            *(U(atom, ExactList(atom)) for atom in atoms),
            *(U(atom, AtLeastList(atom)) for atom in atoms),
            *(U(atom, RuggedList(atom)) for atom in atoms),
            *(U(ExactList(atom), ExactList(atom, 2)) for atom in atoms),
            *(U(ExactList(atom), AtLeastList(atom)) for atom in atoms),
        )
        parameters = (
            *atoms,
            *collections,
            U(Integer, AtLeastList(Integer)),
            U(Number, AtLeastList(Number)),
        )

        applicable = 0
        for argument, parameter in product(arguments, parameters):
            with self.subTest(argument=argument, parameter=parameter):
                applied = apply_overload(
                    Overload((parameter,), (String,)),
                    (argument,),
                )
                if applied is None:
                    continue
                applicable += 1
                has_plan = bool(
                    applied.vectorised_depths or applied.vectorised_target_ranks
                )
                self.assertEqual(applied.vectorised, has_plan)
                if assignable(argument, parameter):
                    self.assertFalse(has_plan)
                if not has_plan:
                    self.assertTrue(same(applied.actual_returns[0], String))

        self.assertGreater(applicable, 1000)

    def test_every_vectorised_argument_has_complete_runtime_metadata(self):
        applied = apply_overload(
            Overload((Integer, ExactList(Integer), Integer), (String,)),
            (
                AtLeastList(Integer, 2),
                ExactList(Integer),
                Integer,
            ),
        )

        self.assertIsNotNone(applied)
        assert applied is not None
        self.assertTrue(applied.vectorised)
        self.assertEqual(len(applied.vectorised_depths), len(applied.params))
        self.assertEqual(len(applied.vectorised_target_ranks), len(applied.params))
        self.assertEqual(applied.vectorised_depths, (2, 0, 0))
        self.assertEqual(applied.vectorised_target_ranks, (0, None, None))


class VectorisationRuntimePropertyTests(unittest.TestCase):
    """Check vectorisation behavior across every supported execution route."""

    def assert_all_modes(
        self,
        source: str,
        expected_stack: tuple[str, ...],
        expected_output: str = "",
    ) -> None:
        """Require identical direct, optimized, and serialized results."""
        outcomes = _all_execution_modes(source)
        self.assertEqual(len(set(outcomes)), 1)
        self.assertEqual(outcomes[0], (expected_stack, expected_output))

    def test_minimum_rank_scalar_pipeline_agrees_across_execution_modes(self):
        self.assert_all_modes(
            """
define Mag(:Real*) => ** 2 | reduce: + | sqrt
Mag [3, 4]
Mag [[3, 5], [4, 12]]
""",
            ("5", "[5, 13]"),
        )

    def test_whole_value_effectful_consumer_executes_once(self):
        self.assert_all_modes(
            """
define Mag(:Real*) => ** 2 | reduce: + | sqrt
println Mag [[3, 5], [4, 12]]
""",
            (),
            "[5, 13]\n",
        )

    def test_nested_union_vectorisation_recurses_at_runtime(self):
        self.assert_all_modes(
            """
[
  (if true => 4 else => [9] end),
  (if false => 16 else => [25] end),
] | sqrt
""",
            ("[2, [5]]",),
        )

    def test_minimum_rank_empty_value_retains_rank_evidence(self):
        self.assert_all_modes(
            """
define \\empty -> Integer* => []
\\empty | + 1
""",
            ("[]",),
        )

    def test_filter_preserves_correlated_minimum_rank_and_empty_runtime_rank(self):
        self.assert_all_modes(
            """
define washed?(:Number*) =>
  match =>
    as :Number+++ => "No"
    _ => "Yes"
  end
end

define wash(:Number*) =>
  filter: fn (:Number) => false end
end

const $subject = [[[10]]]
println washed? $subject
println washed? wash $subject
""",
            (),
            "No\nNo\n",
        )

    def test_mixed_dynamic_and_broadcast_arguments_keep_their_policies(self):
        self.assert_all_modes(
            """
define shift(xs: Integer*, delta: Integer) => +
shift([[1, 2], [3, 4]], 10)
""",
            ("[[11, 12], [13, 14]]",),
        )

    def test_exact_collection_parameter_consumes_each_runtime_slice_whole(self):
        self.assert_all_modes(
            """
define outerLength(xs: Number+) -> Integer => $xs | length
outerLength [[1, 2], [3, 4]]
""",
            ("[2, 2]",),
        )


if __name__ == "__main__":
    unittest.main()
