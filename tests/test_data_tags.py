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


def execute_all_modes(source: str):
    """Execute valid source directly, optimized, and after bytecode round trips."""
    analyser, typed = analyse_source(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    outputs = []
    for optimize in (False, True):
        program = compile_program(typed, optimize=optimize)
        outputs.append(run(program))
        outputs.append(run(loads(dumps(program))))
    return typed, outputs


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


    def test_constructed_tag_automatically_flows_through_vector_arithmetic(self):
        source = """
tag #infinite as constructed
#infinite [1, 2, 3] + 4
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#infinite Integer+")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(
                value.value,
                [Decimal("5"), Decimal("6"), Decimal("7")],
            )
            self.assertEqual(
                {(tag.name, tag.depth) for tag in value.tags},
                {("infinite", 0)},
            )

    def test_constructed_tag_from_either_operand_flows_to_result(self):
        sources = (
            """
tag #sticky as constructed
1 #sticky | 2 +
""",
            """
tag #sticky as constructed
1 | 2 #sticky | +
""",
        )
        for source in sources:
            with self.subTest(source=source):
                typed, outputs = execute_all_modes(source)
                self.assertEqual(show(typed[-1].typ), "#sticky Integer")
                for output in outputs:
                    [value] = output
                    self.assertIsInstance(value, TaggedValue)
                    self.assertEqual(value.value, Decimal("3"))
                    self.assertEqual({tag.name for tag in value.tags}, {"sticky"})

    def test_constructed_tag_flows_through_generic_identity(self):
        source = """
tag #sticky as constructed
define[T] identity(value: T) -> T => $value end
#sticky [1, 2, 3] | identity
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#sticky Integer+")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual({tag.name for tag in value.tags}, {"sticky"})

    def test_constructed_tag_flows_through_ordinary_user_function(self):
        source = """
tag #stream as constructed
define offset(value: Number+, amount: Number) -> Number+ =>
  $value | $amount +
end
#stream [1, 2, 3] | 4 offset
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#stream Number+")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual({tag.name for tag in value.tags}, {"stream"})

    def test_computed_tag_drops_while_constructed_tag_survives_same_call(self):
        source = """
tag #stream as constructed
tag #sorted as computed
#stream #sorted [1, 2, 3] + 4
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#stream Integer+")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual({tag.name for tag in value.tags}, {"stream"})

    def test_multiple_constructed_tags_flow_together(self):
        source = """
tag #cached as constructed
tag #stream as constructed
#cached #stream [1, 2, 3] + 4
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#cached #stream Integer+")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(
                {tag.name for tag in value.tags},
                {"cached", "stream"},
            )

    def test_constructed_tag_flows_through_widening_cast(self):
        source = """
tag #sticky as constructed
1 #sticky as Number
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#sticky Number")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(value.value, Decimal("1"))
            self.assertEqual({tag.name for tag in value.tags}, {"sticky"})

    def test_rank_increase_projects_constructed_tag_to_output_depth(self):
        source = """
tag #stream as constructed
define wrap(value: Number+) -> Number++ => [$value] end
#stream [1, 2, 3] | wrap
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#stream+ Number+2")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(
                {(tag.name, tag.depth) for tag in value.tags},
                {("stream", 1)},
            )

    def test_same_rank_matrix_flow_uses_output_rank_minus_one_depth(self):
        source = """
tag #grid as constructed
define keep(value: Number+2) -> Number+2 => $value end
#grid [[1, 2], [3, 4]] | keep
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#grid+ Number+2")
        for output in outputs:
            [value] = output
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(
                {(tag.name, tag.depth) for tag in value.tags},
                {("grid", 1)},
            )

    def test_rank_drop_does_not_carry_constructed_tag(self):
        source = """
tag #stream as constructed
define first(value: Number+) -> Number => $value $[0] end
#stream [1, 2, 3] | first
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "Number")
        for output in outputs:
            self.assertEqual(output, [Decimal("1")])
            self.assertNotIsInstance(output[0], TaggedValue)

    def test_explicit_absent_return_removes_constructed_tag(self):
        source = """
tag #stream as constructed
define materialize(value: #stream Number+) -> #!stream Number+ => $value end
#stream [1, 2, 3] | materialize
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "#!stream Number+")
        for output in outputs:
            self.assertEqual(
                output,
                [[Decimal("1"), Decimal("2"), Decimal("3")]],
            )
            self.assertNotIsInstance(output[0], TaggedValue)


    def test_exact_empty_return_contract_excludes_constructed_tag(self):
        source = """
tag #sticky as constructed
define strip(value: #sticky Number) -> [] Number => #-sticky $value end
1 #sticky | strip
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "[] Number")
        for output in outputs:
            self.assertEqual(output, [Decimal("1")])
            self.assertNotIsInstance(output[0], TaggedValue)

    def test_later_disjoint_constructed_input_wins_automatic_flow(self):
        cases = (
            ("1 #a 2 #b +", "#b Integer", {"b"}),
            ("1 #b 2 #a +", "#a Integer", {"a"}),
        )
        for expression, expected_type, expected_tags in cases:
            source = f"""
tag #a as constructed
tag #b as constructed
tag #a disjoint #b
{expression}
"""
            with self.subTest(expression=expression):
                typed, outputs = execute_all_modes(source)
                self.assertEqual(show(typed[-1].typ), expected_type)
                for output in outputs:
                    [value] = output
                    self.assertIsInstance(value, TaggedValue)
                    self.assertEqual({tag.name for tag in value.tags}, expected_tags)

    def test_owning_overlay_omission_still_explicitly_removes_constructed_tag(self):
        source = """
tag #sticky as constructed
define keep(value: #sticky Number) -> #sticky Number => $value end
#sticky: keep =>
  (#sticky Number) -> Number
end
1 #sticky | keep
"""
        typed, outputs = execute_all_modes(source)
        self.assertEqual(show(typed[-1].typ), "Number")
        for output in outputs:
            self.assertEqual(output, [Decimal("1")])
            self.assertNotIsInstance(output[0], TaggedValue)

    def test_constructed_overlay_reifies_runtime_evidence(self):
        source = """
tag #sticky as constructed
#sticky: + =>
  (#sticky Number, Number) -> #sticky Number
end
1 #sticky | 2 +
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "#sticky Number")

        program = compile_program(typed, optimize=False)
        for executable in (program, loads(dumps(program))):
            [value] = run(executable)
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(value.value, Decimal("3"))
            self.assertEqual(
                {(tag.name, tag.depth) for tag in value.tags},
                {("sticky", 0)},
            )

    def test_constructed_overlay_survives_suspended_user_function_call(self):
        source = """
tag #sticky as constructed
define increment(value: Number) -> Number => $value 1 + end
#sticky: increment =>
  (#sticky Number) -> #sticky Number
end
1 #sticky | increment
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "#sticky Number")

        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for executable in (program, loads(dumps(program))):
                [value] = run(executable)
                self.assertIsInstance(value, TaggedValue)
                self.assertEqual(value.value, Decimal("2"))
                self.assertEqual({tag.name for tag in value.tags}, {"sticky"})

    def test_impossible_tag_depth_is_rejected(self):
        direct, _ = analyse_source(
            """
tag #nested as constructed
1 #nested+
"""
        )
        self.assertIn("has depth 1", diagnostics_text(direct))
        self.assertIn("has rank 0", diagnostics_text(direct))

        signature, _ = analyse_source(
            """
tag #nested as constructed
define invalid(value: #nested++ Number+) -> Number => 0 end
"""
        )
        self.assertIn("has depth 2", diagnostics_text(signature))
        self.assertIn("has rank 1", diagnostics_text(signature))

    def test_constructed_tag_erasure_recurses_through_casts(self):
        source = """
tag #sticky as constructed
$values = [1 #sticky, 2]
$plain = $values as Integer+
$plain $[0] |
match =>
  as :#sticky Integer => "leaked"
  _ => "plain"
end
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), ["plain"])
            self.assertEqual(run(loads(dumps(program))), ["plain"])

    def test_constructed_tag_erasure_recurses_through_function_returns(self):
        source = """
tag #sticky as constructed
define make(dummy: Number) -> Integer+ => [1 #sticky, 2] end
$plain = make 0
$plain $[0] |
match =>
  as :#sticky Integer => "leaked"
  _ => "plain"
end
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), ["plain"])
            self.assertEqual(run(loads(dumps(program))), ["plain"])

    def test_indexing_projects_constructed_tag_depth(self):
        source = """
tag #nested as constructed
$outer = [[1, 2] #nested]
$inner = $outer $[0]
$inner |
match =>
  as :#nested Integer+ => "tagged"
  _ => "plain"
end
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), ["tagged"])
            self.assertEqual(run(loads(dumps(program))), ["tagged"])

    def test_slicing_preserves_constructed_tag_depth(self):
        source = """
tag #nested as constructed
$outer = [[1, 2] #nested, [3, 4] #nested]
$slice = $outer[0:0]
$slice |
match =>
  as :#nested+ Integer+2 => "tagged"
  _ => "plain"
end
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), ["tagged"])
        self.assertEqual(run(loads(dumps(program))), ["tagged"])

    def test_constructed_overlay_can_explicitly_remove_tag(self):
        source = """
tag #sticky as constructed
define keep(value: #sticky Number) -> #sticky Number => $value end
#sticky: keep =>
  (#sticky Number) -> Number
end
1 #sticky | keep
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "Number")

        program = compile_program(typed, optimize=False)
        for executable in (program, loads(dumps(program))):
            [value] = run(executable)
            self.assertEqual(value, Decimal("1"))
            self.assertNotIsInstance(value, TaggedValue)

    def test_unit_overlay_is_explicit_permission_for_plain_implementation(self):
        source = """
tag #km as unit
#km: + =>
  (#km Number, Number) -> #km Number
end
1 #km | 2 +
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        for executable in (program, loads(dumps(program))):
            [value] = run(executable)
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(value.value, Decimal("3"))
            self.assertEqual({tag.name for tag in value.tags}, {"km"})

    def test_unit_overlay_does_not_launder_unrelated_units(self):
        analyser, _ = analyse_source(
            """
tag #km as unit
tag #seconds as unit
#km: + =>
  (#km Number, Number) -> #km Number
end
1 #seconds | 2 +
"""
        )
        self.assertIn("no overloads for element '+' match", diagnostics_text(analyser))

    def test_constructed_depth_contract_survives_serialization(self):
        source = """
tag #nested as constructed
define keep(value: Number+) -> #nested+ Number+ => $value end
[1, 2] | keep
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        for executable in (program, loads(dumps(program))):
            [value] = run(executable)
            self.assertIsInstance(value, TaggedValue)
            self.assertEqual(
                {(tag.name, tag.depth) for tag in value.tags},
                {("nested", 1)},
            )

    def test_collection_construction_lifts_common_tag_depth(self):
        source = """
tag #nested as constructed
[[1, 2] #nested]
"""
        analyser, typed = analyse_source(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(show(typed[-1].typ), "#nested+ Integer+2")
        [value] = run(compile_program(typed, optimize=False))
        self.assertIsInstance(value, TaggedValue)
        self.assertEqual(
            {(tag.name, tag.depth) for tag in value.tags},
            {("nested", 1)},
        )
        self.assertNotIsInstance(value.value[0], TaggedValue)

    def test_constructed_overlay_rejects_unsafe_rank_or_foreign_tag_flow(self):
        rank, _ = analyse_source(
            """
tag #nested as constructed
#nested: [T] wrap =>
  (#nested T+) -> #nested T++
end
"""
        )
        self.assertIn("unsafe rank/depth flow", diagnostics_text(rank))

        foreign, _ = analyse_source(
            """
tag #sticky as constructed
tag #other as computed
#sticky: + =>
  (#sticky Number, Number) -> #other Number
end
"""
        )
        self.assertIn("foreign tag '#other'", diagnostics_text(foreign))


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


    def test_constructed_tag_interaction_matrix_across_execution_modes(self):
        path = Path(__file__).parents[1] / "samples" / "ConstructedTagInteractions.vlnc"
        analyser, typed = analyse_source(
            path.read_text(encoding="utf-8"),
            source_file=path,
        )
        self.assertEqual(analyser.diagnostics, [])
        expected = [
            "arithmetic-sticky",
            "generic-sticky",
            "rank-up",
            "rank-drop",
            "explicit-removal",
            "multiple-sticky",
            "computed-dropped",
            "cast-sticky",
            Decimal("15"),
        ]
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), expected)
            self.assertEqual(run(loads(dumps(program))), expected)

    def test_telemetry_tag_pipeline_across_execution_modes(self):
        path = Path(__file__).parents[1] / "samples" / "TelemetryTagPipeline.vlnc"
        analyser, typed = analyse_source(
            path.read_text(encoding="utf-8"),
            source_file=path,
        )
        self.assertEqual(analyser.diagnostics, [])
        expected = [
            "calibrated-live",
            "windowed-live",
            "head-finite",
            "archive-encrypted",
            Decimal("25"),
        ]
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), expected)
            self.assertEqual(run(loads(dumps(program))), expected)

    def test_constructed_tag_flow_sample_across_execution_modes(self):
        path = Path(__file__).parents[1] / "samples" / "ConstructedTagFlow.vlnc"
        analyser, typed = analyse_source(
            path.read_text(encoding="utf-8"),
            source_file=path,
        )
        self.assertEqual(analyser.diagnostics, [])
        expected = [
            "nested-stream",
            "projected-stream",
            Decimal("15"),
            "finite",
        ]
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            self.assertEqual(run(program), expected)
            self.assertEqual(run(loads(dumps(program))), expected)


if __name__ == "__main__":
    unittest.main()
