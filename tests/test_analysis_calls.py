"""Boundary tests for the call-planning subsystem."""

from __future__ import annotations

import copy
import unittest

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.analysis.calls import CallAnalyser, choose_best_overload
from valiance.parsing import parse


class CallPlanningBoundaryTests(unittest.TestCase):
    """Verify call planning is owned outside the analyser façade."""

    def test_analyser_constructs_a_call_service(self) -> None:
        """The façade exposes the dedicated call-planning subsystem."""
        analyser = Analyser()
        self.assertIsInstance(analyser.calls, CallAnalyser)
        self.assertTrue(analyser.calls.provides("element_call_candidates"))
        self.assertTrue(analyser.calls.provides("_analyse_element_extension"))

    def test_selected_overload_is_committed_to_the_typed_call(self) -> None:
        """Analysis records its overload decision for downstream compilation."""
        analyser = Analyser()
        typed = analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsNotNone(typed[-1].overload)
        self.assertEqual(len(typed[-1].overload.params), 2)

    def test_vectorisation_plan_is_committed_during_analysis(self) -> None:
        """Vectorised calls carry selected depths rather than deferring intent."""
        analyser = Analyser()
        typed = analyser.analyse(parse("[1, 2] 10 +"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertTrue(typed[-1].overload.vectorised)
        self.assertEqual(typed[-1].overload.vectorised_depths, (1, 0))

    def test_call_service_survives_session_copy(self) -> None:
        """REPL preview copies preserve call-service context delegation."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("1 2 +"))
        self.assertEqual(analyser.diagnostics, [])

    def test_copied_session_preserves_every_service(self) -> None:
        """REPL-style deep copies retain all service-to-context links."""
        analyser = copy.deepcopy(Analyser())
        analyser.analyse(parse("define twice(x: Number) -> Number => $x 2 *\n3 twice"))
        self.assertEqual(analyser.diagnostics, [])
        self.assertIs(analyser.declarations._context, analyser)
        self.assertIs(analyser.calls._context, analyser)
        self.assertIs(analyser.control_flow._context, analyser)
        self.assertIs(analyser.contracts._context, analyser)
        self.assertIs(analyser.expressions._context, analyser)

    def test_element_diagnostics_are_owned_by_calls(self) -> None:
        """Smart element suggestions are exposed by the call subsystem."""
        analyser = Analyser()
        self.assertTrue(analyser.calls.provides("_unknown_element_message"))
        analyser.analyse(parse("1 prntln"))
        self.assertTrue(analyser.diagnostics)
        self.assertIn("did you mean", analyser.diagnostics[0])

    def test_callback_collection_match_beats_scalar_vectorisation(self) -> None:
        for body in ("length", "$row length"):
            for rows in (
                '[["a", "bb"], ["ccc"]]',
                '[[["a", "bb"]], [["ccc"], ["d"]]]',
            ):
                with self.subTest(body=body, rows=rows):
                    analyser = Analyser()
                    typed = analyser.analyse(parse(
                        f"{rows} map fn (row) => {body}"
                    ))
                    self.assertEqual(analyser.diagnostics, [])
                    self.assertEqual(
                        typed[-1].overload.actual_returns, (T.ExactList(T.Int),)
                    )

    def test_callback_rank_match_beats_scalar_vectorisation(self) -> None:
        analyser = Analyser()
        typed = analyser.analyse(parse('''
define measure(:Number*) -> Int => 10
define measure(:Number) -> Int => 20
[[1, 2], [3]] map fn (row) => measure
'''))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].overload.actual_returns, (T.ExactList(T.Int),))

    def test_indexed_callback_receives_concrete_row_and_field_types(self) -> None:
        analyser = Analyser()
        typed = analyser.analyse(parse('''
[["aa", "bb", "cc", "dd", "ee", "ff", "gg", "hh"]]
map fn (row) => record{first => $row[0], sixth => $row[5], eighth => $row[7]}
'''))
        self.assertEqual(analyser.diagnostics, [])
        callback = typed[-2]
        self.assertEqual(callback.typ.params, (T.ExactList(T.String),))
        fields = callback.typ.returns[0].fields
        self.assertEqual(len(fields), 3)
        self.assertTrue(all(T.same(field.typ, T.String) for field in fields))
        self.assertEqual(callback.overloads[0].typ, callback.typ)

    def test_equally_specific_callback_matches_remain_ambiguous(self) -> None:
        analyser = Analyser()
        analyser.analyse(parse('''
define measure(:Int|String) -> Int => 1
define measure(:Int|Boolean) -> String => "a"
[1, 2] map fn (row) => measure
'''))
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("ambiguous overloads for element 'map'", analyser.diagnostics[0])


class CallableOverloadSelectionTests(unittest.TestCase):
    """Verify callable overload choice against an immutable mini stack."""

    def test_concrete_callable_applies_to_stack(self) -> None:
        callable_type = T.Fn((T.Number,), (T.String,))

        chosen = choose_best_overload(
            callable_type,
            T.TypeStack((T.Number,)),
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.applications[0].actual_returns, (T.String,))
        self.assertEqual(chosen.applications[0].stack, T.TypeStack((T.String,)))

    def test_overload_set_chooses_more_specific_candidate(self) -> None:
        broad = T.Overload((T.Number,), (T.String,))
        specific = T.Overload((T.Int,), (T.Boolean,))
        callable_type = T.Overloads(broad, specific)

        chosen = choose_best_overload(
            callable_type,
            T.TypeStack((T.Int,)),
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertIs(chosen.overload, specific)
        self.assertEqual(chosen.applications[0].actual_returns, (T.Boolean,))

    def test_missing_inputs_are_inferred_from_selected_overload(self) -> None:
        overload = T.Overload((T.Number, T.String), (T.Boolean,))

        chosen = choose_best_overload(
            T.Overloads(overload),
            T.TypeStack((T.String,)),
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertIs(chosen.overload, overload)
        self.assertEqual(chosen.applications[0].inputs, (T.Number,))
        self.assertEqual(chosen.applications[0].stack, T.TypeStack((T.Boolean,)))

    def test_ambiguous_best_overloads_are_rejected(self) -> None:
        left = T.Overload((T.Number, T.U(T.Number, T.String)), (T.Number,))
        right = T.Overload((T.U(T.Number, T.String), T.Number), (T.String,))

        chosen = choose_best_overload(
            T.Overloads(left, right),
            T.TypeStack((T.Number, T.Number)),
        )

        self.assertIsNone(chosen)


    def test_multiple_stack_states_choose_one_exact_overload(self) -> None:
        shared = T.Overload((T.Number,), (T.String,))
        first_only = T.Overload((T.Int,), (T.Boolean,))

        chosen = choose_best_overload(
            T.Overloads(shared, first_only),
            (
                T.TypeStack((T.Int,)),
                T.TypeStack((T.Real,)),
            ),
        )

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertIs(chosen.overload, shared)
        self.assertEqual(len(chosen.applications), 2)

    def test_non_callable_type_is_rejected(self) -> None:
        self.assertIsNone(
            choose_best_overload(T.Number, T.TypeStack((T.Number,)))
        )


if __name__ == "__main__":
    unittest.main()
