"""Cross-layer properties for static and runtime vectorisation plans."""

from __future__ import annotations

import contextlib
import io
import unittest
from itertools import product

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import RuntimeError as ValianceRuntimeError
from valiance.runtime.compiler import compile_program
from valiance.runtime.runtime_values import format_runtime_value
from valiance.runtime.serialization import dumps, loads
from valiance.runtime.vm import VirtualMachine
from valiance.vtypes import (
    AtLeastList,
    ExactList,
    Overload,
    RuggedList,
    U,
    apply_overload,
    assignable,
    same,
)
from valiance.vtypes.default_types import Int, Number, Real, String


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


    def test_every_vectorised_argument_has_complete_runtime_metadata(self):
        applied = apply_overload(
            Overload((Int, ExactList(Int), Int), (String,)),
            (
                AtLeastList(Int, 2),
                ExactList(Int),
                Int,
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

    def test_mapping_lengths_prefers_whole_rows_across_execution_modes(self):
        for body in ("length", "$row length"):
            for rows, expected in (
                ('[["a", "bb"], ["ccc"]]', "[2, 1]"),
                ('[[["a", "bb"]], [["ccc"], ["d"]]]', "[1, 2]"),
            ):
                with self.subTest(body=body, rows=rows):
                    self.assert_all_modes(
                        f"{rows} map fn (row) => {body}", (expected,)
                    )

    def test_callback_rank_match_beats_vectorisation_across_execution_modes(self):
        self.assert_all_modes(
            '''
define measure(:Number*) -> Int => 10
define measure(:Number) -> Int => 20
[[1, 2], [3]] map fn (row) => measure
''',
            ("[10, 10]",),
        )

    def test_callback_scalar_vectorisation_remains_available(self):
        self.assert_all_modes(
            '[[1, 2], [3]] map fn (row) => double', ("[[2, 4], [6]]",)
        )
        self.assert_all_modes(
            '[["a", "bb"], ["ccc"]] map fn (row: String) => length',
            ("[[1, 2], [3]]",),
        )

    def test_mapping_indexed_records_consumes_whole_rows(self):
        for mapping in (
            'map fn (row) => record{first => $row[0], sixth => $row[5], eighth => $row[7]}',
            'map: fn (row) => record{first => $row[0], sixth => $row[5], eighth => $row[7]}',
        ):
            with self.subTest(mapping=mapping):
                self.assert_all_modes(
                    '[["aa", "bb", "cc", "dd", "ee", "ff", "gg", "hh"], '
                    '["ii", "jj", "kk", "ll", "mm", "nn", "oo", "pp"]] '
                    + mapping,
                    ("[record{first => 'aa', sixth => 'ff', eighth => 'hh'}, "
                     "record{first => 'ii', sixth => 'nn', eighth => 'pp'}]",),
                )

    def test_indexed_callback_preserves_captured_values(self):
        self.assert_all_modes(
            '''
$label = "row"
[["a", "b"]] map fn (row) => record{label => $label, value => $row[1]}
''',
            ("[record{label => 'row', value => 'b'}]",),
        )

    def test_indexed_callback_still_rejects_short_rows_at_runtime(self):
        source = '[["aa", "bb"]] map fn (row) => $row[5] end | println'
        for optimize, serialized in product((False, True), repeat=2):
            with self.subTest(optimize=optimize, serialized=serialized):
                with self.assertRaisesRegex(ValianceRuntimeError, "IndexFault"):
                    _execute_mode(source, optimize=optimize, serialized=serialized)

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
define \\empty -> Int* => []
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
define shift(xs: Int*, delta: Int) => +
shift([[1, 2], [3, 4]], 10)
""",
            ("[[11, 12], [13, 14]]",),
        )

    def test_exact_collection_parameter_consumes_each_runtime_slice_whole(self):
        self.assert_all_modes(
            """
define outerLength(xs: Number+) -> Int => $xs | length
outerLength [[1, 2], [3, 4]]
""",
            ("[2, 2]",),
        )


if __name__ == "__main__":
    unittest.main()
