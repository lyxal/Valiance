"""Contract tests for data-tag parsing, analysis, and runtime behaviour."""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse, parse_type
from valiance.runtime import RuntimeError, compile_program, dumps, loads, run
from valiance.runtime_values import TaggedValue
from valiance.types import (
    ExactTags,
    Integer,
    Number,
    Tagged,
    assignable,
    show,
)


def analyse_source(source: str, *, source_file: Path | None = None):
    """Return an analyser and typed program for one source snippet."""
    analyser = Analyser(source_file=source_file)
    typed = analyser.analyse(parse(source))
    return analyser, typed


def diagnostics_text(analyser: Analyser) -> str:
    """Render diagnostics uniformly across structured and legacy diagnostics."""
    return "\n".join(str(item) for item in analyser.diagnostics)


def execute(source: str):
    """Analyse and execute source that is expected to be valid."""
    analyser, typed = analyse_source(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=False))


class DataTagUnitTests(unittest.TestCase):
    def test_exact_tag_set_syntax_parses_and_round_trips(self):
        self.assertEqual(show(parse_type("[#a, #b+] Number")), "[#a #b+] Number")
        self.assertEqual(show(parse_type("[] Number")), "[] Number")

    def test_exact_tag_sets_require_exactly_the_present_tags(self):
        self.assertTrue(assignable(Tagged(Integer, "a"), ExactTags(Number, "a")))
        self.assertFalse(
            assignable(Tagged(Integer, "a", "b"), ExactTags(Number, "a"))
        )
        self.assertTrue(assignable(Integer, ExactTags(Number)))
        self.assertFalse(assignable(Tagged(Integer, "a"), ExactTags(Number)))

    def test_unit_tag_cannot_be_laundered_into_an_index(self):
        analyser, _ = analyse_source(
            """
tag #km as unit
[10, 20] $[1 #km]
"""
        )
        self.assertIn(
            "list indexing requires Integer index",
            diagnostics_text(analyser),
        )

    def test_explicit_unit_removal_allows_indexing(self):
        self.assertEqual(
            execute(
                """
tag #km as unit
$index = 1 #km
[10, 20] $[#-km $index]
"""
            ),
            [Decimal("20")],
        )

    def test_dropped_tag_evidence_is_removed_at_function_boundary(self):
        self.assertEqual(
            execute(
                """
tag #sorted as computed
define erase(value: Number) -> Number => $value end
define classify(value: [] Number) -> String =>
  $value |
  match =>
    as :#sorted Number => "stale"
    _ => "plain"
  end
end
1 #sorted | erase | classify
"""
            ),
            ["plain"],
        )

    def test_exact_parameter_rejects_extra_compile_time_tags(self):
        analyser, _ = analyse_source(
            """
tag #a as computed
tag #b as computed
define exact(value: [#a] Number) -> String => "exact" end
1 #a #b | exact
"""
        )
        self.assertIn(
            "no overloads for element 'exact' match",
            diagnostics_text(analyser),
        )

    def test_variant_is_parent_only_statically_but_reified_at_runtime(self):
        analyser, typed = analyse_source(
            """
tag #sorted as computed
tag #ascending as #sorted
1 #ascending
"""
        )
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "#sorted Integer")

        [value] = run(compile_program(typed, optimize=False))
        self.assertIsInstance(value, TaggedValue)
        self.assertEqual(
            {tag.name for tag in value.tags},
            {"sorted", "ascending"},
        )

    def test_variant_can_match_at_runtime_through_parent_signature(self):
        self.assertEqual(
            execute(
                """
tag #sorted as computed
tag #ascending as #sorted
define classify(value: [#sorted] Number) -> String =>
  $value |
  match =>
    as :#ascending Number => "ascending"
    _ => "sorted"
  end
end
1 #ascending | classify
"""
            ),
            ["ascending"],
        )

    def test_function_return_keeps_parent_variant_but_drops_unrelated_tags(self):
        analyser, typed = analyse_source(
            """
tag #sorted as computed
tag #ascending as #sorted
tag #other as computed
define retag(value: Number) -> #sorted Number =>
  $value | #ascending
end
define classify(value: [#sorted] Number) -> String =>
  $value |
  match =>
    as :#other Number => "leaked"
    as :#ascending Number => "ascending"
    _ => "sorted"
  end
end
1 #other | retag | classify
"""
        )
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), ["ascending"])
        self.assertEqual(run(loads(dumps(program))), ["ascending"])

    def test_variant_parent_must_be_declared_and_computed(self):
        missing, _ = analyse_source("tag #ascending as #sorted")
        self.assertIn("requires declared computed parent", diagnostics_text(missing))

        unit, _ = analyse_source(
            """
tag #distance as unit
tag #km as #distance
"""
        )
        self.assertIn("must be computed", diagnostics_text(unit))

    def test_variant_tags_are_rejected_in_compile_time_signatures(self):
        analyser, _ = analyse_source(
            """
tag #sorted as computed
tag #ascending as #sorted
define classify(value: #ascending Number) -> String => "ascending" end
"""
        )
        self.assertIn("runtime-only", diagnostics_text(analyser))

    def test_variant_tags_are_rejected_as_cast_result_types(self):
        analyser, _ = analyse_source(
            """
tag #sorted as computed
tag #ascending as #sorted
1 as! #ascending Number
"""
        )
        self.assertIn("runtime-only", diagnostics_text(analyser))

    def test_parent_validator_runs_when_a_variant_is_applied(self):
        analyser, typed = analyse_source(
            """
tag #sorted as computed
tag #ascending as #sorted
define #sorted(value: Number) -> #boolean Number => $value 0 > end
-1 #ascending
"""
        )
        self.assertEqual(analyser.diagnostics, [])
        with self.assertRaisesRegex(RuntimeError, "validator #sorted failed"):
            run(compile_program(typed, optimize=False))

    def test_validator_overloads_use_specificity_not_declaration_order(self):
        [value] = execute(
            """
tag #positive as computed
define #positive(value: Number) -> #boolean Number => false end
define #positive(value: Integer) -> #boolean Number => true end
1 #positive
"""
        )
        self.assertIsInstance(value, TaggedValue)
        self.assertEqual({tag.name for tag in value.tags}, {"positive"})

    def test_missing_validator_overload_is_a_compile_error(self):
        analyser, _ = analyse_source(
            """
tag #checked as computed
define #checked(value: String) -> #boolean Number => true end
1 #checked
"""
        )
        self.assertIn("no validator overload", diagnostics_text(analyser))

    def test_disjoint_parent_removes_variant_evidence_in_both_directions(self):
        [ascending] = execute(
            """
tag #unsorted as computed
tag #sorted as computed
tag #ascending as #sorted
tag #sorted disjoint #unsorted
1 #unsorted | #ascending
"""
        )
        self.assertEqual(
            {tag.name for tag in ascending.tags},
            {"sorted", "ascending"},
        )

        [unsorted] = execute(
            """
tag #unsorted as computed
tag #sorted as computed
tag #ascending as #sorted
tag #sorted disjoint #unsorted
1 #ascending | #unsorted
"""
        )
        self.assertEqual({tag.name for tag in unsorted.tags}, {"unsorted"})

    def test_unknown_direct_tag_application_is_rejected(self):
        analyser, _ = analyse_source("1 #missing")
        self.assertIn("unknown data tag '#missing'", diagnostics_text(analyser))

    def test_nested_present_and_absent_tag_conflict_is_rejected(self):
        analyser, _ = analyse_source(
            """
tag #checked as computed
define invalid(value: Result[#checked #!checked Number, String]) -> Number => 0 end
"""
        )
        self.assertIn("cannot be both present and absent", diagnostics_text(analyser))


class DataTagRealWorldTests(unittest.TestCase):
    def test_data_tag_safety_sample(self):
        path = Path(__file__).parents[1] / "samples" / "DataTagsSafety.vlnc"
        analyser, typed = analyse_source(
            path.read_text(encoding="utf-8"),
            source_file=path,
        )
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            run(compile_program(typed, optimize=False)),
            ["ascending", Decimal("7")],
        )


if __name__ == "__main__":
    unittest.main()
