"""Cross-layer correctness, soundness, and realistic workload regressions."""

from __future__ import annotations

import unittest

from valiance.analysis.analyser import Analyser
from valiance.parsing.parser import parse
from valiance.runtime import RuntimeError as ValianceRuntimeError
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.runtime_values import LazyList
from valiance.symbols import Symbol
from valiance.types import (
    Integer,
    N,
    Number,
    OKType,
    Result,
    Some,
    String,
    U,
    assignable,
    merge_types,
    optional,
    same,
    show,
    subtype,
)
from valiance.runtime_values import RuntimeNumber as RuntimeNumber


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute_both(source: str):
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    program = compile_program(typed, optimize=False)
    direct = run(program)
    restored = run(loads(dumps(program)))
    if direct != restored:
        raise AssertionError((direct, restored))
    return direct


OPTIONAL_RETRY_POLICY = """
define retryStatus(value: Integer?) -> String =>
  $value |
  match =>
    as :Some[Integer] => "scheduled"
    as :None => "disabled"
    _ => "invalid"
  end
end |
retryStatus(3) |
retryStatus(Some(5)) |
retryStatus(None)
"""


RESULT_SETTLEMENT_FLOW = """
define settlementStatus(value: Result[Number, ValueError]) -> String =>
  $value |
  match =>
    as :OK[Number] => "accepted"
    as :ValueError => "rejected"
    _ => "invalid"
  end
end |
settlementStatus(125) |
settlementStatus(OK(250)) |
settlementStatus(ValueError("declined"))
"""


DICTIONARY_CONFIGURATION_FLOW = """
define configKind(value: Dict[String, Integer] | String) -> String =>
  $value |
  match =>
    as :Dict[String, Integer] => "mapping"
    _ => "preset"
  end
end |
configKind(dict{"retries": 3}) |
configKind("default")
"""


OPTIONAL_PAYLOAD_WORKFLOW = """
define retryDelay(value: Integer?) -> Integer =>
  $value |
  match =>
    as :Some[Integer](seconds) => +($seconds, 5)
    _ => 0
  end
end |
retryDelay(7) |
retryDelay(Some(3)) |
retryDelay(None)
"""


RESULT_VALIDATION_GATEWAY = """
define requireResult(value: Number | String | ValueError) -> Result[Number, ValueError] =>
  $value as! Result[Number, ValueError]
end |
define resultKind(value: Number | String | ValueError) -> String =>
  requireResult($value) |
  match =>
    as :OK[Number] => "accepted"
    as :Err => "rejected"
    _ => "invalid"
  end
end |
resultKind(42) |
resultKind(ValueError("declined"))
"""


TRAIT_FLEET_WORKFLOW = """
trait Vehicle => end |
object Car => $model: String end |
object Car as Vehicle => end |
define classifyVehicle(value: Vehicle | String) -> String =>
  $value |
  match =>
    as :Vehicle => "vehicle"
    _ => "other"
  end
end |
classifyVehicle(Car("sedan")) |
classifyVehicle("walk")
"""


COVARIANT_CONTAINER_WORKFLOW = """
trait Vehicle => end |
object Car => $model: String end |
object Car as Vehicle => end |
object[T] Box => $value: T end |
define classifyBox(value: Box[Vehicle] | String) -> String =>
  $value |
  match =>
    as :Box[Vehicle] => "vehicle box"
    _ => "other"
  end
end |
classifyBox(Box(Car("sedan")))
"""


EXHAUSTIVE_OPTIONAL_WORKFLOW = """
define optionalState(value: Integer?) -> String =>
  $value |
  match =>
    as :Some[Integer] => "some"
    as :None => "none"
  end
end |
optionalState(1) |
optionalState(None)
"""


EMPTY_MATRIX_GUARD = """
define requireMatrix(values: Number+ | Number+2) -> Number+2 =>
  $values as! Number+2
end |
requireMatrix([] as Number+)
"""


USER_ERROR_PIPELINE = """
object Problem => $message: String end |
object Problem as Err => end |
define classifyResult(value: Result[Integer, Problem]) -> String =>
  $value |
  match =>
    as :Err => "error"
    _ => "success"
  end
end |
classifyResult(Problem("bad")) |
classifyResult(7)
"""


USER_FAULT_CLASSIFIER = """
object Abort => $message: String end |
object Abort as Fault => end |
define classifyFault(value: Fault | String) -> String =>
  $value |
  match =>
    as :Fault => "fault"
    _ => "ordinary"
  end
end |
classifyFault(Abort("stop")) |
classifyFault("continue")
"""


INHERITED_TRAIT_WORKFLOW = """
trait Vehicle => end |
trait Electric as Vehicle => end |
object Car => $model: String end |
object Car as Electric => end |
define classifyVehicle(value: Vehicle | String) -> String =>
  $value |
  match =>
    as :Vehicle => "vehicle"
    _ => "other"
  end
end |
classifyVehicle(Car("sedan"))
"""


EXHAUSTIVE_RESULT_WORKFLOW = """
object Problem => $message: String end |
object Problem as Err => end |
define resultState(value: Result[Integer, Problem]) -> String =>
  $value |
  match =>
    as :OK[Integer] => "success"
    as :Problem => "error"
  end
end |
resultState(7) |
resultState(Problem("bad"))
"""


GENERIC_TRAIT_WORKFLOW = """
trait[T] Producer => end |
object[T] Box => $value: T end |
object[T] Box as Producer[T] => end |
define classifyProducer(value: Producer[Integer] | String) -> String =>
  $value |
  match =>
    as :Producer[String] => "wrong"
    as :Producer[Integer] => "right"
    _ => "other"
  end
end |
classifyProducer(Box(1))
"""


TRANSITIVE_GENERIC_TRAIT_WORKFLOW = """
trait[T] Source => end |
trait[T] Producer as Source[T] => end |
object[T] Box => $value: T end |
object[T] Box as Producer[T] => end |
define classifySource(value: Source[Integer] | String) -> String =>
  $value |
  match =>
    as :Source[String] => "wrong"
    as :Source[Integer] => "right"
    _ => "other"
  end
end |
classifySource(Box(1))
"""


class TypeAlgebraCorrectnessTests(unittest.TestCase):
    def test_raw_and_explicit_some_normalize_to_one_present_branch(self):
        self.assertTrue(
            same(
                U(Integer, Some(String)),
                Some(U(Integer, String)),
            )
        )

    def test_optional_join_is_associative_with_explicit_some_values(self):
        from valiance.types import NoneType

        left = merge_types(merge_types(NoneType(), Integer), Some(String))
        right = merge_types(NoneType(), merge_types(Integer, Some(String)))

        self.assertTrue(same(left, right), (show(left), show(right)))
        self.assertTrue(same(left, optional(U(Integer, String))))

    def test_result_types_are_covariant_in_success_and_error_types(self):
        value_error = N(Symbol("ValueError"))
        err = N(Symbol("Err"))
        narrow = Result(Integer, value_error)
        broad = Result(Number, err)

        self.assertTrue(subtype(narrow, broad))
        self.assertTrue(assignable(narrow, broad))

    def test_result_joins_are_upper_bounds_for_raw_and_wrapped_successes(self):
        value_error = N(Symbol("ValueError"))
        result = Result(String, value_error)
        cases = (
            (Integer, OKType(String)),
            (Integer, result),
            (OKType(Integer), result),
        )

        for left, right in cases:
            with self.subTest(left=show(left), right=show(right)):
                merged = merge_types(left, right)
                self.assertTrue(assignable(left, merged), show(merged))
                self.assertTrue(assignable(right, merged), show(merged))


class RuntimeRepresentationAgreementTests(unittest.TestCase):
    def test_optional_present_values_match_raw_and_explicit_some_forms(self):
        self.assertEqual(
            execute_both(OPTIONAL_RETRY_POLICY),
            ["scheduled", "scheduled", "disabled"],
        )

    def test_result_success_values_match_raw_and_explicit_ok_forms(self):
        self.assertEqual(
            execute_both(RESULT_SETTLEMENT_FLOW),
            ["accepted", "accepted", "rejected"],
        )

    def test_dictionary_type_patterns_match_runtime_dictionary_values(self):
        self.assertEqual(
            execute_both(DICTIONARY_CONFIGURATION_FLOW),
            ["mapping", "preset"],
        )

    def test_empty_flat_list_does_not_pass_an_exact_matrix_check(self):
        analyser, typed = analyse(EMPTY_MATRIX_GUARD)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)

        for candidate in (program, loads(dumps(program))):
            with self.subTest(round_tripped=candidate is not program):
                with self.assertRaisesRegex(
                    ValianceRuntimeError,
                    "checked cast failed",
                ):
                    run(candidate)

    def test_checked_collection_cast_does_not_consume_a_lazy_value(self):
        consumed = 0

        def values():
            nonlocal consumed
            consumed += 1
            yield RuntimeNumber("1")

        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, LazyList(values(), runtime_rank=1)),
                    Instruction(
                        OpCode.CHECK_CAST,
                        ("collection", "list_exact", 1, ("nominal", "Number", ())),
                    ),
                ),
                name="<lazy-cast>",
            )
        )

        with self.assertRaisesRegex(ValianceRuntimeError, "checked cast failed"):
            run(program)
        self.assertEqual(consumed, 0)

    def test_non_reified_function_type_pattern_is_rejected_during_analysis(self):
        source = """
define kind(value: Function[Number -> Number] | String) -> String =>
  $value |
  match =>
    as :Function[Number -> Number] => "function"
    _ => "other"
  end
end
"""
        analyser, _typed = analyse(source)

        self.assertTrue(analyser.diagnostics)
        self.assertIn("cannot be checked at runtime", analyser.diagnostics[0])
        self.assertIn("Function[Number -> Number]", analyser.diagnostics[0])

    def test_raw_optional_payloads_can_be_destructured_without_wrapping(self):
        self.assertEqual(
            execute_both(OPTIONAL_PAYLOAD_WORKFLOW),
            [RuntimeNumber("12"), RuntimeNumber("8"), RuntimeNumber("0")],
        )

    def test_checked_result_casts_and_err_trait_patterns_agree_at_runtime(self):
        self.assertEqual(
            execute_both(RESULT_VALIDATION_GATEWAY),
            ["accepted", "rejected"],
        )

    def test_invalid_checked_result_cast_still_fails(self):
        source = RESULT_VALIDATION_GATEWAY.rsplit("resultKind(42)", 1)[0] + (
            'requireResult("not a result")'
        )
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        with self.assertRaisesRegex(ValianceRuntimeError, "checked cast failed"):
            run(compile_program(typed, optimize=False))

    def test_non_reified_function_checked_cast_is_rejected_during_analysis(self):
        source = """
define force(value: Function[Number -> Number] | String) -> Function[Number -> Number] =>
  $value as! Function[Number -> Number]
end
"""
        analyser, _typed = analyse(source)

        self.assertTrue(analyser.diagnostics)
        self.assertIn("cannot be checked at runtime", analyser.diagnostics[0])
        self.assertIn("Function[Number -> Number]", analyser.diagnostics[0])

    def test_user_trait_patterns_use_reified_implementation_facts(self):
        self.assertEqual(
            execute_both(TRAIT_FLEET_WORKFLOW),
            ["vehicle", "other"],
        )

    def test_covariant_generic_patterns_use_reified_variance_facts(self):
        self.assertEqual(
            execute_both(COVARIANT_CONTAINER_WORKFLOW),
            ["vehicle box"],
        )

    def test_optional_some_and_none_patterns_are_recognized_as_exhaustive(self):
        self.assertEqual(
            execute_both(EXHAUSTIVE_OPTIONAL_WORKFLOW),
            ["some", "none"],
        )

    def test_user_defined_err_implementations_are_runtime_errors(self):
        self.assertEqual(
            execute_both(USER_ERROR_PIPELINE),
            ["error", "success"],
        )

    def test_user_defined_fault_implementations_match_fault_patterns(self):
        self.assertEqual(
            execute_both(USER_FAULT_CLASSIFIER),
            ["fault", "ordinary"],
        )

    def test_runtime_trait_facts_include_transitive_trait_inheritance(self):
        self.assertEqual(
            execute_both(INHERITED_TRAIT_WORKFLOW),
            ["vehicle"],
        )

    def test_closed_result_branches_are_recognized_as_exhaustive(self):
        self.assertEqual(
            execute_both(EXHAUSTIVE_RESULT_WORKFLOW),
            ["success", "error"],
        )

    def test_generic_trait_patterns_preserve_implementation_arguments(self):
        self.assertEqual(
            execute_both(GENERIC_TRAIT_WORKFLOW),
            ["right"],
        )

    def test_transitive_generic_trait_patterns_compose_arguments(self):
        self.assertEqual(
            execute_both(TRANSITIVE_GENERIC_TRAIT_WORKFLOW),
            ["right"],
        )


class RealWorldProgramTests(unittest.TestCase):
    def test_retry_policy_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(OPTIONAL_RETRY_POLICY),
            ["scheduled", "scheduled", "disabled"],
        )

    def test_settlement_result_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(RESULT_SETTLEMENT_FLOW),
            ["accepted", "accepted", "rejected"],
        )

    def test_configuration_shape_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(DICTIONARY_CONFIGURATION_FLOW),
            ["mapping", "preset"],
        )

    def test_optional_payload_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(OPTIONAL_PAYLOAD_WORKFLOW),
            [RuntimeNumber("12"), RuntimeNumber("8"), RuntimeNumber("0")],
        )

    def test_result_validation_gateway_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(RESULT_VALIDATION_GATEWAY),
            ["accepted", "rejected"],
        )

    def test_trait_fleet_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(TRAIT_FLEET_WORKFLOW),
            ["vehicle", "other"],
        )

    def test_covariant_container_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(COVARIANT_CONTAINER_WORKFLOW),
            ["vehicle box"],
        )

    def test_exhaustive_optional_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(EXHAUSTIVE_OPTIONAL_WORKFLOW),
            ["some", "none"],
        )

    def test_user_error_pipeline_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(USER_ERROR_PIPELINE),
            ["error", "success"],
        )

    def test_user_fault_classifier_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(USER_FAULT_CLASSIFIER),
            ["fault", "ordinary"],
        )

    def test_inherited_trait_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(INHERITED_TRAIT_WORKFLOW),
            ["vehicle"],
        )

    def test_exhaustive_result_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(EXHAUSTIVE_RESULT_WORKFLOW),
            ["success", "error"],
        )

    def test_generic_trait_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(GENERIC_TRAIT_WORKFLOW),
            ["right"],
        )

    def test_transitive_generic_trait_workflow_survives_bytecode_round_trip(self):
        self.assertEqual(
            execute_both(TRANSITIVE_GENERIC_TRAIT_WORKFLOW),
            ["right"],
        )


if __name__ == "__main__":
    unittest.main()
