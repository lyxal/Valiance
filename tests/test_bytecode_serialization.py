import os
import subprocess
import sys
import textwrap

from ffi_support import (
    build_shared_library, native_library_directory, shared_library_path,
)
import unittest

from valiance.runtime import BytecodeFormatError, RuntimeError, dumps, loads, run
from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    IndexOperationSpec,
    IndexSelectorSpec,
    Instruction,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)

from valiance.runtime.runtime_values import FFIScalarValue, RuntimeNumber


class BytecodeSerializationTests(unittest.TestCase):
    def test_descriptor_conversion_plan_survives_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse(
            'object Box => public $value: String end\n'
            '$field = fields[Box]["value"]\n42 to[$field.type]'
        )))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads_interface(dumps_interface(typed))
        self.assertEqual(restored, typed)
        for optimize in (False, True):
            self.assertEqual(run(loads(dumps(compile_program(restored, optimize=optimize)))), ["42"])

    def test_malformed_descriptor_conversion_plans_are_rejected(self):
        payloads = (None, (), ((), -1), ((), True), ((("Int", 0), ("Int", 0)), 0),
                    ((("", 0),), 0), ((("Int", 1),), 0), ((("Int", True),), 0))
        cases = [(OpCode.TYPE_DISPATCH, payload) for payload in payloads]
        cases += [(OpCode.CONVERSION_MISSING, 1), (OpCode.CONVERSION_MISSING, None),
                  (OpCode.SOURCE_ARGS_BORROW, True), (OpCode.SOURCE_ARGS_BORROW, -1)]
        for op, payload in cases:
            with self.subTest(op=op, payload=payload), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(op, payload),))))

    def test_fallible_reflection_plans_survive_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse('''
object Box => public $value: Int end
$box = Box(7)
read?($box, fields[Box]["value"]) ?!
$updated = write?($box, fields[Box]["value"], 8) ?!
$updated.value
$box.value
''')))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads_interface(dumps_interface(typed))
        self.assertEqual(restored, typed)
        for optimize in (False, True):
            self.assertEqual(run(loads(dumps(compile_program(restored, optimize=optimize)))), [7, 8, 7])

    def test_malformed_fallible_reflection_plans_are_rejected(self):
        from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference

        layout = ReflectionFieldsReference('Box', (ReflectionFieldReference('value', 'Int', 'public'),))
        access = (layout, False, ('nominal', 'Box', ()))
        valid = (False, access, False, (), (), None)
        payloads = (None, (), (1, *valid[1:]), (False, None, False, (), (), None),
                    (False, access, 1, (), (), None), (False, access, True, (1,), (), None),
                    (False, access, True, (-1, 0), (), None),
                    (False, access, True, (1, 0), (None, -1), None),
                    (False, access, True, (1, 0), (), 7),
                    (True, access, False, (), (), None))
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(OpCode.REFLECT_CALL, payload),))))

    def test_reflective_write_plan_survives_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse(
            'object Box => public $value: Int end\n'
            '$box = Box(7)\n$box[fields[Box]["value"]] = 8\n$box.value'
        )))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads_interface(dumps_interface(typed))
        self.assertEqual(restored, typed)
        for optimize in (False, True):
            self.assertEqual(run(loads(dumps(compile_program(restored, optimize=optimize)))), [RuntimeNumber(8)])

    def test_malformed_reflective_write_contracts_are_rejected(self):
        from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference

        layout = ReflectionFieldsReference("Box", (ReflectionFieldReference("value", "Int", "public"),))
        for payload in (None, (layout, False, ("nominal", "Box", ())),
                        (layout, 1, (), (None,)), (layout, False, (), ()),
                        (layout, False, (), (42,)), (None, False, (), (None,))):
            with self.subTest(payload=payload), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(OpCode.REFLECT_WRITE, payload),))))

    def test_generic_reflection_templates_and_specializations_survive_interfaces(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse(
            'object Box => public $value: Int end\n'
            'define[T] read(value: T) => $value[fields[T]["value"]] end\n'
            '$box = Box(7)\nread($box)'
        )))
        self.assertEqual(analyser.diagnostics, [])
        restored = loads_interface(dumps_interface(typed))
        self.assertEqual(restored, typed)
        for optimize in (False, True):
            program = compile_program(restored, optimize=optimize)
            self.assertEqual(run(loads(dumps(program))), [RuntimeNumber(7)])

    def test_specialization_rejects_unchecked_bytecode(self):
        for payload in (None, (), FunctionCode((), requires_specialization=True)):
            with self.subTest(payload=payload), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(OpCode.SPECIALIZE_FUNCTION, payload),))))
        with self.assertRaises(BytecodeFormatError):
            dumps(Program(FunctionCode((), requires_specialization=1)))

    def test_uninstantiated_reflection_templates_cannot_execute(self):
        code = FunctionCode((), requires_specialization=True)
        program = Program(FunctionCode((Instruction(OpCode.MAKE_FUNCTION, code), Instruction(OpCode.CALL))))
        for candidate in (program, loads(dumps(program))):
            with self.assertRaisesRegex(RuntimeError, "requires a checked specialization"):
                run(candidate)

    def test_reflective_read_plan_survives_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse(
            'object Box => public $value: Int end\n'
            '$box = Box(7)\n$box[fields[Box]["value"]]'
        )))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(loads_interface(dumps_interface(typed)), typed)

    def test_malformed_reflective_read_contracts_are_rejected(self):
        from valiance.runtime.bytecode import ReflectionFieldsReference

        layout = ReflectionFieldsReference("Box")
        for payload in (None, (layout,), (layout, 1, ("nominal", "Box")),
                        (layout, False, None), (None, False, ("nominal", "Box"))):
            with self.subTest(payload=payload), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(OpCode.REFLECT_READ, payload),))))

    def test_reflection_semantic_metadata_survives_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse("object Point => public $x: Int end\nfields[Point]")))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(loads_interface(dumps_interface(typed)), typed)

    def test_reflective_foreach_checked_bodies_survive_interface_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime.interface_serialization import dumps_interface, loads_interface

        analyser = Analyser()
        typed = tuple(analyser.analyse(parse(
            'object Point =>\npublic $x: Int\npublic $label: String\nend\n'
            'fields[Point] foreach (field) => $field.type end'
        )))
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(loads_interface(dumps_interface(typed)), typed)

    def test_reflective_foreach_payload_round_trip(self):
        from valiance.runtime.bytecode import (
            ReflectionFieldReference, ReflectionFieldsReference, ReflectionForeachReference,
        )

        layout = ReflectionFieldsReference("Box", (
            ReflectionFieldReference("value", "Int", "public"),))
        body = FunctionCode((), params=("field",), name="foreach.body")
        reference = ReflectionForeachReference(layout, (body,))
        program = Program(FunctionCode((Instruction(OpCode.MAKE_FIELDS, layout),
                                        Instruction(OpCode.FOREACH, reference))))
        restored = loads(dumps(program))
        self.assertEqual(restored, program)
        self.assertEqual(run(restored)[0].type_name, "None")

    def test_malformed_reflective_foreach_contracts_are_rejected(self):
        from valiance.runtime.bytecode import (
            ReflectionFieldReference, ReflectionFieldsReference, ReflectionForeachReference,
        )

        layout = ReflectionFieldsReference("Box", (
            ReflectionFieldReference("value", "Int", "public"),))
        body = FunctionCode((), params=("field",), name="foreach.body")
        for reference in (
            ReflectionForeachReference(layout),
            ReflectionForeachReference(layout, (FunctionCode(()),)),
            ReflectionForeachReference(layout, (body,), has_index=True),
            ReflectionForeachReference(layout, (body,), completion_count=0),
            ReflectionForeachReference(layout, (body,), has_index=1),
            ReflectionForeachReference(ReflectionFieldsReference(""), ()),
        ):
            with self.subTest(reference=reference), self.assertRaises(BytecodeFormatError):
                dumps(Program(FunctionCode((Instruction(OpCode.FOREACH, reference),))))

    def test_reflection_layout_round_trip_retains_owner_types_order_and_visibility(self):
        from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference

        reference = ReflectionFieldsReference("Box[Int]", (
            ReflectionFieldReference("first", "Int", "public"),
            ReflectionFieldReference("second", "String", "private"),
        ))
        program = Program(FunctionCode((Instruction(OpCode.MAKE_FIELDS, reference),), name="<main>"))
        restored = loads(dumps(program))
        self.assertEqual(restored, program)
        fields = run(restored)[0]
        self.assertEqual(fields.type_args, ("Box[Int]",))
        self.assertEqual([item.fields["name"] for item in fields.fields["#fields"]],
                         ["first", "second"])

    def test_malformed_reflection_layouts_are_rejected(self):
        from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference

        for reference in (None, ("Int", ()), ReflectionFieldsReference(""),
                          ReflectionFieldsReference("Int", (
                              ReflectionFieldReference("field", "Int", "hidden"),)),
                          ReflectionFieldsReference("Int", (
                              ReflectionFieldReference("x", "Int", "public"),
                              ReflectionFieldReference("x", "Int", "public"),))):
            with self.subTest(reference=reference):
                program = Program(FunctionCode((Instruction(OpCode.MAKE_FIELDS, reference),)))
                with self.assertRaises(BytecodeFormatError):
                    dumps(program)

    def test_corrupted_reflection_visibility_is_rejected_when_decoding(self):
        from valiance.runtime.bytecode import ReflectionFieldReference, ReflectionFieldsReference

        reference = ReflectionFieldsReference("Box", (
            ReflectionFieldReference("value", "Int", "public"),))
        program = Program(FunctionCode((Instruction(OpCode.MAKE_FIELDS, reference),)))
        data = dumps(program).replace(b"public", b"hidden")
        with self.assertRaises(BytecodeFormatError):
            loads(data)

    def test_ffi_scalar_constants_round_trip_with_exact_type_identity(self):
        values = (
            FFIScalarValue("&i32", 42),
            FFIScalarValue("&f64", 2.5),
            FFIScalarValue("&bool", True),
            FFIScalarValue("&char", "A"),
            FFIScalarValue("&void", None),
        )
        for value in values:
            with self.subTest(value=value):
                program = Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, value),
                            Instruction(OpCode.STORE_VAR, "value"),
                            Instruction(OpCode.LOAD_VAR, "value"),
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )
                decoded = loads(dumps(program))
                restored = decoded.main.instructions[0].arg
                self.assertEqual(restored, value)
                self.assertIsInstance(restored, FFIScalarValue)
                self.assertEqual(restored.ffi_type, value.ffi_type)
                self.assertEqual(run(decoded), [value])

    def test_ffi_scalar_can_be_nested_in_serialized_metadata_tuples(self):
        value = (FFIScalarValue("&u8", 255), "payload")
        program = Program(
            FunctionCode((Instruction(OpCode.PUSH_CONST, value),), name="<main>")
        )
        self.assertEqual(
            loads(dumps(program)).main.instructions[0].arg,
            value,
        )

    def test_malformed_ffi_scalar_payload_is_rejected(self):
        value = FFIScalarValue("&i32", 42)
        program = Program(
            FunctionCode((Instruction(OpCode.PUSH_CONST, value),), name="<main>")
        )
        data = dumps(program)
        corrupted = data.replace(b"&i32", b" i32", 1)
        with self.assertRaises(BytecodeFormatError):
            loads(corrupted)

    def test_index_operation_specs_round_trip_as_named_payloads(self):
        spec = IndexOperationSpec(
            selectors=(
                IndexSelectorSpec(False, True, False, False),
                IndexSelectorSpec(True, True, True, True),
            ),
            spread=True,
            grouped_update=True,
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.GET_INDEX, spec),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        decoded = loads(dumps(program))

        self.assertEqual(decoded, program)
        self.assertIsInstance(decoded.main.instructions[0].arg, IndexOperationSpec)
        self.assertEqual(spec.value_count, 4)

    def test_boolean_constants_preserve_boolean_type(self):
        for value in (False, True):
            with self.subTest(value=value):
                program = Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, value),
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )

                decoded = loads(dumps(program))
                decoded_value = decoded.main.instructions[0].arg

                self.assertEqual(decoded_value, value)
                self.assertIs(type(decoded_value), bool)
                self.assertEqual(run(decoded), [value])

    def test_nested_boolean_instruction_arguments_preserve_boolean_type(self):
        argument = (("ascending", 0, False), ("descending", 1, True))
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, argument),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        decoded = loads(dumps(program))
        decoded_argument = decoded.main.instructions[0].arg

        self.assertEqual(decoded_argument, argument)
        self.assertIs(type(decoded_argument[0][2]), bool)
        self.assertIs(type(decoded_argument[1][2]), bool)

    def test_serializes_variant_parent_metadata(self):
        program = Program(
            FunctionCode((Instruction(OpCode.RETURN),), name="<main>"),
            (("ascending", "sorted"), ("descending", "sorted")),
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_rejects_malformed_variant_parent_metadata(self):
        malformed = (
            (("ascending", "sorted"), ("ascending", "ordered")),
            (("ascending", "ascending"),),
            (("ascending", "descending"), ("descending", "sorted")),
        )
        for tag_parents in malformed:
            with self.subTest(tag_parents=tag_parents):
                program = Program(
                    FunctionCode((Instruction(OpCode.RETURN),), name="<main>"),
                    tag_parents,
                )
                with self.assertRaises(BytecodeFormatError):
                    dumps(program)
                with self.assertRaises(RuntimeError):
                    run(program)

    def test_invalid_decimal_payload_raises_bytecode_format_error(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.PUSH_CONST, RuntimeNumber("42")),),
                name="<main>",
            )
        )
        data = dumps(program)
        corrupted = data.replace(b"42", b"x2", 1)

        with self.assertRaises(BytecodeFormatError):
            loads(corrupted)

    def test_every_truncation_is_reported_as_a_format_error(self):
        nested = FunctionCode(
            (
                Instruction(OpCode.PUSH_CONST, RuntimeNumber("123.45")),
                Instruction(
                    OpCode.MAKE_FUNCTION,
                    FunctionCode(
                        (Instruction(OpCode.LOAD_VAR, "value"),),
                        params=("value",),
                        name="identity",
                    ),
                ),
                Instruction(OpCode.RETURN),
            ),
            name="<main>",
        )
        data = dumps(Program(nested))

        for end in range(len(data)):
            with self.subTest(end=end):
                with self.assertRaises(BytecodeFormatError):
                    loads(data[:end])

    def test_trailing_bytes_are_rejected(self):
        data = dumps(Program(FunctionCode((Instruction(OpCode.RETURN),))))

        with self.assertRaises(BytecodeFormatError):
            loads(data + b"\x00")

    def test_deeply_nested_values_fail_through_bytecode_format_error(self):
        value = None
        for _ in range(2_000):
            value = (value,)
        program = Program(
            FunctionCode((Instruction(OpCode.PUSH_CONST, value),), name="<main>")
        )

        with self.assertRaises(BytecodeFormatError):
            dumps(program)

    def test_deeply_nested_payloads_fail_through_bytecode_format_error(self):
        from tools.fuzzing import _nested_tuple_bytecode

        with self.assertRaises(BytecodeFormatError):
            loads(_nested_tuple_bytecode(2_000))

    def test_invalid_jump_targets_are_rejected_without_hanging(self):
        root = os.path.dirname(os.path.dirname(__file__))
        script = textwrap.dedent("""
            from valiance.runtime import RuntimeError, run
            from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program

            for target in (-1, 3):
                program = Program(
                    FunctionCode((Instruction(OpCode.JUMP, target),), name="<main>")
                )
                try:
                    run(program)
                except RuntimeError as exc:
                    if "invalid jump target" not in str(exc):
                        raise
                else:
                    raise AssertionError(f"jump target {target} was accepted")
            """)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(root, "src")

        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_malformed_decoded_instructions_raise_language_runtime_errors(self):
        instructions = (
            Instruction(OpCode.BUILD_LIST, "not-a-count"),
            Instruction(OpCode.MAKE_FUNCTION, "not-function-code"),
            Instruction(OpCode.CALL_RESOLVED_ELEMENT, None),
            Instruction(OpCode.SOURCE_ARGS, "not-an-arity"),
        )

        for instruction in instructions:
            with self.subTest(instruction=instruction):
                program = Program(
                    FunctionCode(
                        (
                            Instruction(OpCode.PUSH_CONST, 1),
                            instruction,
                            Instruction(OpCode.RETURN),
                        ),
                        name="<main>",
                    )
                )
                decoded = loads(dumps(program))

                with self.assertRaises(RuntimeError):
                    run(decoded)

    def test_serializes_byte_oriented_format_without_op_names(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, RuntimeNumber("42")),
                    Instruction(OpCode.PUSH_CONST, "answer"),
                    Instruction(OpCode.BUILD_STRING, ("value=", None)),
                    Instruction(OpCode.BUILD_TUPLE, 2),
                    Instruction(
                        OpCode.CALL_RESOLVED_ELEMENT,
                        ResolvedElementReference("+", "test:plus"),
                    ),
                    Instruction(OpCode.TRY_UNWRAP),
                    Instruction(OpCode.STACK_SHUFFLE, ("copy", ("x",), ("x",))),
                    Instruction(OpCode.SOURCE_ARGS, 1),
                    Instruction(OpCode.VALIDATE_TAG, ("#checked", "test:validator", (), ())),
                    Instruction(OpCode.WRAP_ASSERT_ERROR),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        data = dumps(program)
        decoded = loads(data)

        self.assertTrue(data.startswith(b"VLNCBC\x3C"))
        self.assertNotIn(b"push_const", data)
        self.assertNotIn(b"valiance-bytecode", data)
        self.assertEqual(decoded, program)

    def test_serializes_arbitrarily_large_decimal_constants(self):
        value = RuntimeNumber("99999999999999999999999999999")
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.PUSH_CONST, value),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_function_return_count(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.RETURN),),
                name="one",
                return_count=1,
                occurrence_effects=(None,),
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_rejects_occurrence_effects_with_wrong_return_arity(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.RETURN),),
                params=("value",),
                return_count=1,
                occurrence_effects=(),
            )
        )

        with self.assertRaisesRegex(
            BytecodeFormatError,
            "occurrence effects must match",
        ):
            dumps(program)

    def test_rejects_occurrence_effects_with_invalid_parameter_index(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.RETURN),),
                params=("value",),
                return_count=1,
                occurrence_effects=(1,),
            )
        )

        with self.assertRaisesRegex(
            BytecodeFormatError,
            "invalid parameter",
        ):
            dumps(program)

    def test_serializes_nested_function_code(self):
        inner = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "x"),
                Instruction(OpCode.RETURN),
            ),
            params=("x",),
            name="id",
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, inner),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_function_parameter_collection_ranks(self):
        function = FunctionCode(
            (Instruction(OpCode.RETURN),),
            params=("cells", "count"),
            param_collection_ranks=(1, 0),
        )

        self.assertEqual(loads(dumps(Program(function))), Program(function))

    def test_serializes_recursive_function_flag(self):
        program = Program(
            FunctionCode(
                (Instruction(OpCode.RETURN),),
                name="loop",
                recursive=True,
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_function_set_code(self):
        overloads = FunctionSetCode(
            (
                FunctionCode((Instruction(OpCode.RETURN),), params=("x",)),
                FunctionCode((Instruction(OpCode.RETURN),), params=("x", "y")),
            )
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, overloads),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_serializes_vector_extension_references(self):
        identity = FunctionCode(
            (
                Instruction(OpCode.LOAD_VAR, "value"),
                Instruction(OpCode.RETURN),
            ),
            params=("value",),
        )
        extension = VectorExtensionReference(
            rules=(ExtensionRuleReference((True, False), identity),),
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(
                        OpCode.CALL_RESOLVED_ELEMENT,
                        ResolvedElementReference(
                            "+",
                            "test:plus",
                            vectorised=True,
                            vectorised_depths=(0, 1),
                            vectorised_target_ranks=(1, None),
                            extension=extension,
                        ),
                    ),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        self.assertEqual(loads(dumps(program)), program)

    def test_rejects_malformed_concurrency_instruction_payloads(self):
        cases = (
            Instruction(OpCode.SPAWN_CALL, None),
            Instruction(OpCode.SPAWN_CALL, (1, -1)),
            Instruction(OpCode.SPAWN_CALL, (True, 1)),
            Instruction(OpCode.WAIT_TASK, None),
            Instruction(OpCode.WAIT_TASKS_VECTORISED, -1),
            Instruction(OpCode.CHANNEL_NEW, 1),
            Instruction(OpCode.CHANNEL_SEND, "unexpected"),
            Instruction(OpCode.CHANNEL_RECEIVE, False),
            Instruction(OpCode.CHANNEL_CLOSE, ()),
            Instruction(OpCode.CANCEL_POLL, 0),
        )
        for instruction in cases:
            with self.subTest(instruction=instruction):
                program = Program(
                    FunctionCode((instruction, Instruction(OpCode.RETURN)))
                )
                with self.assertRaises(BytecodeFormatError):
                    dumps(program)

    def test_rejects_unbalanced_concurrency_scope_nesting(self):
        cases = (
            (Instruction(OpCode.SCOPE_END), Instruction(OpCode.RETURN)),
            (Instruction(OpCode.SCOPE_BEGIN), Instruction(OpCode.RETURN)),
            (
                Instruction(OpCode.SCOPE_BEGIN),
                Instruction(OpCode.SCOPE_END),
                Instruction(OpCode.SCOPE_END),
                Instruction(OpCode.RETURN),
            ),
            (Instruction(OpCode.SCOPE_BEGIN, 0), Instruction(OpCode.RETURN)),
            (Instruction(OpCode.SCOPE_END, 0), Instruction(OpCode.RETURN)),
        )
        for instructions in cases:
            with self.subTest(instructions=instructions):
                with self.assertRaises(BytecodeFormatError):
                    dumps(Program(FunctionCode(instructions)))

    def test_accepts_nested_balanced_scopes_and_valid_payloads(self):
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.SCOPE_BEGIN, (0, 1)),
                    Instruction(OpCode.SCOPE_BEGIN, (1, 2)),
                    Instruction(OpCode.SPAWN_CALL, (0, 1, None, (), False, (), (), (), None)),
                    Instruction(OpCode.WAIT_TASK, 1),
                    Instruction(OpCode.WAIT_TASKS_VECTORISED, 2),
                    Instruction(OpCode.CHANNEL_NEW, False),
                    Instruction(OpCode.CHANNEL_SEND),
                    Instruction(OpCode.CHANNEL_RECEIVE),
                    Instruction(OpCode.CHANNEL_CLOSE),
                    Instruction(OpCode.CANCEL_POLL),
                    Instruction(OpCode.SCOPE_END, (1, 2)),
                    Instruction(OpCode.SCOPE_END, (0, 1)),
                    Instruction(OpCode.RETURN),
                )
            )
        )
        self.assertEqual(loads(dumps(program)), program)

    def test_validates_concurrency_inside_nested_function_payloads(self):
        nested = FunctionCode(
            (Instruction(OpCode.SCOPE_BEGIN), Instruction(OpCode.RETURN)),
            name="invalid-nested",
        )
        program = Program(
            FunctionCode(
                (
                    Instruction(OpCode.MAKE_FUNCTION, nested),
                    Instruction(OpCode.RETURN),
                )
            )
        )
        with self.assertRaisesRegex(BytecodeFormatError, "unclosed concurrency scope"):
            dumps(program)


class FFIPlainStructTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows CRT integration")
    def test_windows_crt_dll_cdecl_function(self):
        """Load a real Windows system DLL without compiling a fixture first."""
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        source = (
            'import {ffi("ucrtbase.dll") as crt}\n'
            'link crt.abs(&int) -> &int as cAbs\n'
            'FFI.&int(-7) cAbs\n'
        )
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        program = loads(dumps(compile_program(typed)))
        self.assertEqual(run(program), [FFIScalarValue("&int", 7)])

    def test_plain_struct_native_call_round_trip(self):
        from valiance.runtime.bytecode import NativeCallReference
        from valiance.runtime.runtime_values import FFIScalarValue, FFIStructValue
        from valiance.vtypes import FFIFieldSpec, FFIStructSpec

        with native_library_directory() as directory:
            source = os.path.join(directory, "point.c")
            library = shared_library_path(directory, "point")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { int x; int y; } Point;\n"
                    "FFI_EXPORT Point add(Point a, Point b) { Point r = {a.x+b.x,a.y+b.y}; return r; }\n"
                )
            build_shared_library(source, library)
            spec = FFIStructSpec(
                "&Point",
                (FFIFieldSpec("x", "&int"), FFIFieldSpec("y", "&int")),
            )
            left = FFIStructValue("&Point", (("x", FFIScalarValue("&int", 2)), ("y", FFIScalarValue("&int", 3))))
            right = FFIStructValue("&Point", (("x", FFIScalarValue("&int", 5)), ("y", FFIScalarValue("&int", 7))))
            reference = NativeCallReference(library, "add", ("&Point", "&Point"), "&Point", (spec,))
            program = Program(FunctionCode((
                Instruction(OpCode.PUSH_CONST, left),
                Instruction(OpCode.PUSH_CONST, right),
                Instruction(OpCode.CALL_NATIVE, reference),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            decoded = loads(dumps(program))
            self.assertEqual(decoded, program)
            result = run(decoded)[0]
            self.assertEqual(result.type_name, "&Point")
            self.assertEqual(result.fields["x"], FFIScalarValue("&int", 7))
            self.assertEqual(result.fields["y"], FFIScalarValue("&int", 10))



class FFIOpaqueHandleTests(unittest.TestCase):
    def test_opaque_handle_lifecycle_calls_survive_bytecode(self):
        from valiance.runtime.bytecode import NativeCallReference
        from valiance.runtime.runtime_values import FFIScalarValue, FFIHandleValue

        with native_library_directory() as directory:
            source = os.path.join(directory, "counter.c")
            library = shared_library_path(directory, "counter")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <stdlib.h>\n"
                    "typedef struct Counter { int value; } Counter;\n"
                    "FFI_EXPORT Counter* counter_create(int n) { Counter* c=malloc(sizeof(Counter)); c->value=n; return c; }\n"
                    "FFI_EXPORT void counter_inc(Counter* c) { c->value++; }\n"
                    "FFI_EXPORT int counter_get(Counter* c) { return c->value; }\n"
                    "FFI_EXPORT void counter_destroy(Counter* c) { free(c); }\n"
                )
            build_shared_library(source, library)
            handles = ("&Counter",)
            create = NativeCallReference(library, "counter_create", ("&int",), "&Counter", (), handles)
            get = NativeCallReference(library, "counter_get", ("&Counter",), "&int", (), handles)
            program = Program(FunctionCode((
                Instruction(OpCode.PUSH_CONST, FFIScalarValue("&int", 41)),
                Instruction(OpCode.CALL_NATIVE, create),
                Instruction(OpCode.CALL_NATIVE, get),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            decoded = loads(dumps(program))
            self.assertEqual(decoded, program)
            self.assertEqual(run(decoded), [FFIScalarValue("&int", 41)])



class FFIFlatBufferAndEmbeddedArrayTests(unittest.TestCase):
    def test_flat_buffer_and_embedded_array_round_trip_through_native_abi(self):
        from valiance.runtime.bytecode import NativeCallReference
        from valiance.runtime.runtime_values import FFIBufferValue, FFIScalarValue
        from valiance.vtypes import FFIFieldSpec, FFIStructSpec

        with native_library_directory() as directory:
            source = os.path.join(directory, "buffer.c")
            library = shared_library_path(directory, "buffer")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { int values[4]; int checksum; } Packet;\n"
                    "FFI_EXPORT int sum_values(const int* v, int n) { int s=0; for(int i=0;i<n;i++) s+=v[i]; return s; }\n"
                    "FFI_EXPORT Packet make_packet(void) { Packet p={{1,2,3,4},10}; return p; }\n"
                )
            build_shared_library(source, library)
            values = FFIBufferValue(
                "&int", tuple(FFIScalarValue("&int", item) for item in (1,2,3,4))
            )
            sum_ref = NativeCallReference(
                library, "sum_values", ("&int+", "&int"), "&int"
            )
            sum_program = Program(FunctionCode((
                Instruction(OpCode.PUSH_CONST, values),
                Instruction(OpCode.PUSH_CONST, FFIScalarValue("&int", 4)),
                Instruction(OpCode.CALL_NATIVE, sum_ref),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            self.assertEqual(
                run(loads(dumps(sum_program))), [FFIScalarValue("&int", 10)]
            )

            packet = FFIStructSpec(
                "&Packet",
                (
                    FFIFieldSpec("values", "&int", 4),
                    FFIFieldSpec("checksum", "&int"),
                ),
            )
            packet_ref = NativeCallReference(
                library, "make_packet", (), "&Packet", (packet,)
            )
            packet_program = Program(FunctionCode((
                Instruction(OpCode.CALL_NATIVE, packet_ref),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            result = run(loads(dumps(packet_program)))[0]
            self.assertEqual(
                tuple(item.value for item in result.fields["values"].values),
                (1,2,3,4),
            )
            self.assertEqual(result.fields["checksum"], FFIScalarValue("&int", 10))


    def test_signed_char_input_and_scalar_return_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        with native_library_directory() as directory:
            source_path = os.path.join(directory, "char_input.c")
            library = shared_library_path(directory, "char_input")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "FFI_EXPORT int signed_sum(const char* data, int n) {"
                    " int sum=0; for(int i=0;i<n;i++) sum+=(signed char)data[i];"
                    " return sum; }\n"
                    "FFI_EXPORT char echo_char(char value) { return value; }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as chars}}\n'
                'link chars.signed_sum(:&char+, :&int) -> &int as signedSum\n'
                'link chars.echo_char(:&char) -> &char as echoChar\n'
                '[-1, 0, 65] to[&char+]\n'
                '3 to[&int]\n'
                'signedSum\n'
                'to[Int]\n'
                '-1 to[&char]\n'
                'echoChar\n'
                'to[Int]\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result, [RuntimeNumber(64), RuntimeNumber(-1)])

    def test_borrowed_buffer_return_and_buffer_callbacks_are_rejected(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse

        cases = (
            (
                'link f.values() -> &int+ as values\n',
                "native buffer returns require @owned",
            ),
            (
                'link f.apply(:Function[&int+ -> &int]) -> &int as apply\n',
                "FFI callback parameters and returns must be primitive",
            ),
            (
                'link f.apply(:Function[&int -> &int+]) -> &int as apply\n',
                "FFI callback parameters and returns must be primitive",
            ),
        )
        for declaration, message in cases:
            with self.subTest(declaration=declaration):
                analyser = Analyser()
                analyser.analyse(parse('import {ffi("/tmp/list") as f}\n' + declaration))
                self.assertTrue(
                    any(message in diagnostic for diagnostic in analyser.diagnostics),
                    analyser.diagnostics,
                )

    def test_embedded_char_array_preserves_null_and_signed_bytes(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        with native_library_directory() as directory:
            source_path = os.path.join(directory, "char_struct.c")
            library = shared_library_path(directory, "char_struct")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { char data[4]; int count; } Packet;\n"
                    "FFI_EXPORT Packet make_packet(void) {"
                    " Packet p = {{65, 0, (char)255, 90}, 4}; return p; }\n"
                    "FFI_EXPORT int sum_packet(Packet p) {"
                    " int sum=p.count; for(int i=0;i<4;i++) sum+=(signed char)p.data[i];"
                    " return sum; }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as chars}}\n'
                'link chars.Packet as &Packet =>\n'
                '  $data: &char+ size => 4\n'
                '  $count: &int\nend\n'
                'link chars.make_packet() -> &Packet as makePacket\n'
                'link chars.sum_packet(:&Packet) -> &int as sumPacket\n'
                '$packet = makePacket\n'
                '$packet\n'
                'sumPacket\n'
                'to[Int]\n'
                '$packet.data to[Int+]\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(
                result,
                [RuntimeNumber(158), [RuntimeNumber(x) for x in (65, 0, -1, 90)]],
            )


class FFIStructArrayTests(unittest.TestCase):
    def test_embedded_struct_array_round_trip_through_native_call(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.runtime_values import FFIBufferValue, ObjectValue

        with native_library_directory() as directory:
            source_path = os.path.join(directory, "struct_arrays.c")
            library = shared_library_path(directory, "struct_arrays")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { int values[3]; } Row;\n"
                    "typedef struct { Row rows[2]; } Grid;\n"
                    "FFI_EXPORT Grid make_grid(void) {"
                    " Grid grid={{{{1,2,3}}, {{4,5,6}}}}; return grid; }\n"
                    "FFI_EXPORT int sum_grid(Grid grid) { int total=0;"
                    " for(int i=0;i<2;i++) for(int j=0;j<3;j++)"
                    " total+=grid.rows[i].values[j]; return total; }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as native}}\n'
                'link native.Row as &Row =>\n'
                '  $values: &int+ size => 3\nend\n'
                'link native.Grid as &Grid =>\n'
                '  $rows: &Row+ size => 2\nend\n'
                'link native.make_grid() -> &Grid as makeGrid\n'
                'link native.sum_grid(:&Grid) -> &int as sumGrid\n'
                '$grid = makeGrid\n'
                '$grid\nsumGrid\nto[Int]\n'
                '$grid.rows\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result[0], RuntimeNumber(21))
            buffer = result[1]
            self.assertIsInstance(buffer, FFIBufferValue)
            self.assertEqual(buffer.ffi_type, "&Row+")
            self.assertEqual(len(buffer), 2)
            self.assertTrue(all(isinstance(row, ObjectValue) for row in buffer))
            self.assertEqual(
                [[item.value for item in row.fields["values"].values] for row in buffer],
                [[1, 2, 3], [4, 5, 6]],
            )
            self.assertIn("&Row+", str(buffer))

    def test_nested_struct_char_arrays_preserve_embedded_nul_on_round_trip(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        with native_library_directory() as directory:
            source_path = os.path.join(directory, "nested_chars.c")
            library = shared_library_path(directory, "nested_chars")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { char bytes[4]; } Row;\n"
                    "typedef struct { Row rows[2]; } Grid;\n"
                    "FFI_EXPORT Grid make_grid(void) {"
                    " Grid result={{{{65,0,(char)255,90}},{{1,2,3,4}}}};"
                    " return result; }\n"
                    "FFI_EXPORT int sum_grid(Grid grid) { int result=0;"
                    " for(int i=0;i<2;i++) for(int j=0;j<4;j++)"
                    " result+=(signed char)grid.rows[i].bytes[j];"
                    " return result; }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as native}}\n'
                'link native.Row as &Row =>\n'
                '  $bytes: &char+ size => 4\nend\n'
                'link native.Grid as &Grid =>\n'
                '  $rows: &Row+ size => 2\nend\n'
                'link native.make_grid() -> &Grid as makeGrid\n'
                'link native.sum_grid(:&Grid) -> &int as sumGrid\n'
                '$grid = makeGrid\n'
                '$grid.rows\n'
                '$grid\nsumGrid\nto[Int]\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result[1], RuntimeNumber(164))
            self.assertEqual(
                [[item.value for item in row.fields["bytes"].values]
                 for row in result[0]],
                [[65, 0, -1, 90], [1, 2, 3, 4]],
            )

    def test_owned_struct_array_is_copied_and_freed(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.runtime_values import FFIBufferValue

        with native_library_directory() as directory:
            source_path = os.path.join(directory, "owned_rows.c")
            library = shared_library_path(directory, "owned_rows")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <stdlib.h>\n"
                    "typedef struct { int values[3]; } Row;\n"
                    "static int release_count=0;\n"
                    "FFI_EXPORT Row* make_rows(void) {"
                    " Row* rows=malloc(2*sizeof(Row));"
                    " for(int i=0;i<2;i++) for(int j=0;j<3;j++)"
                    " rows[i].values[j]=i*3+j+1; return rows; }\n"
                    "FFI_EXPORT void release(void* p) { release_count++; free(p); }\n"
                    "FFI_EXPORT int releases(void) { return release_count; }\n"
                    "FFI_EXPORT int sum_rows(const Row* rows, int n) { int total=0;"
                    " for(int i=0;i<n;i++) for(int j=0;j<3;j++)"
                    " total+=rows[i].values[j]; return total; }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as native}}\n'
                'link native.Row as &Row =>\n'
                '  $values: &int+ size => 3\nend\n'
                '@owned("release", size = 2) '
                'link native.make_rows() -> &Row+ as makeRows\n'
                'link native.sum_rows(:&Row+, :&int) -> &int as sumRows\n'
                'link native.releases() -> &int as releases\n'
                '$rows = makeRows\n'
                '$rows\n2 to[&int]\nsumRows\nto[Int]\n'
                'releases\nto[Int]\n'
                '$rows\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result[0:2], [RuntimeNumber(21), RuntimeNumber(1)])
            self.assertIsInstance(result[2], FFIBufferValue)
            self.assertEqual(len(result[2]), 2)
            self.assertEqual(result[2].values[1].fields["values"].values[2].value, 6)

    def test_struct_buffer_constant_survives_bytecode_serialization(self):
        from valiance.runtime.runtime_values import FFIBufferValue, FFIStructValue
        from valiance.runtime.runtime_values import FFIScalarValue

        rows = FFIBufferValue(
            "&Row", (
                FFIStructValue("&Row", (("x", FFIScalarValue("&int", 1)),)),
                FFIStructValue("&Row", (("x", FFIScalarValue("&int", 2)),)),
            ),
        )
        program = Program(FunctionCode((
            Instruction(OpCode.PUSH_CONST, rows), Instruction(OpCode.RETURN),
        ), name="<main>"))
        self.assertEqual(run(loads(dumps(program))), [rows])
        with self.assertRaises(TypeError):
            FFIBufferValue("&Row", (FFIScalarValue("&int", 3),))


class FFIRectangularMatrixTests(unittest.TestCase):
    def _execute(self, source):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        return run(loads(dumps(compile_program(typed, optimize=True))))

    def test_matrix_and_tensor_flatten_row_major_with_shape_checking(self):
        from valiance.runtime.runtime_values import FFIBufferValue

        result = self._execute(
            '[[1,2,3],[4,5,6]] {2,3} cArr\n'
            '[[[1,2],[3,4]],[[5,6],[7,8]]] {2,2,2} cArr\n'
            '[[1.5,2.5],[3.5,4.5]] [2,2] cArr\n'
        )
        self.assertEqual(len(result), 3)
        for index, expected in enumerate(((1, 2, 3, 4, 5, 6),
                                          (1, 2, 3, 4, 5, 6, 7, 8),
                                          (1.5, 2.5, 3.5, 4.5))):
            self.assertIsInstance(result[index], FFIBufferValue)
            self.assertEqual(tuple(item.value for item in result[index].values), expected)

    def test_carr_element_type_is_inferred_or_explicit(self):
        from valiance.runtime.runtime_values import FFIBufferValue

        result = self._execute(
            '[[1,2],[3,4]] {2,2} cArr\n'
            '[[1.25,2.5],[3.75,4.0]] {2,2} cArr\n'
            '[[1,2],[3,4]] {2,2} cArr[&short]\n'
            '[[-1,0],[127,23]] {2,2} cArr[&char]\n'
            '[] as[Int++] {0,3} cArr[&unsignedint]\n'
            '[[1.5,2.5],[3.5,4.5]] {2,2} cArr[&float]\n'
        )
        self.assertEqual([v.element_type for v in result],
                         ['&int', '&double', '&short', '&char',
                          '&unsignedint', '&float'])
        self.assertTrue(all(isinstance(v, FFIBufferValue) for v in result))
        self.assertEqual([v.value for v in result[0].values], [1,2,3,4])
        self.assertEqual([v.value for v in result[1].values], [1.25,2.5,3.75,4.0])
        self.assertEqual([v.value for v in result[2].values], [1,2,3,4])
        self.assertEqual([v.value for v in result[3].values], [-1,0,127,23])
        self.assertEqual(len(result[4].values), 0)

    def test_carr_checks_element_ranges_and_types(self):
        with self.assertRaisesRegex(RuntimeError, 'RangeFault'):
            self._execute('[[0,40000]] {1,2} cArr[&short]\n')
        with self.assertRaisesRegex(RuntimeError, 'RangeFault'):
            self._execute('[[-1,3]] {1,2} cArr[&unsignedchar]\n')
        for source in (
            '[["a","b"]] {1,2} cArr',
            '[[1.5]] {1,1} cArr[&short]',
            '[[1,2]] {1,2} cArr[&CString]',
        ):
            with self.subTest(source=source):
                from valiance.analysis import Analyser
                from valiance.parsing import parse
                analyser = Analyser()
                analyser.analyse(parse(source))
                self.assertTrue(analyser.diagnostics)

    def test_shaped_ffi_conversion_is_contiguous_row_major(self):
        from valiance.runtime.runtime_values import FFIBufferValue

        result = self._execute(
            '[[1,2,3],[4,5,6]] to[&int{2,3}]\n'
            '[[[1,2],[3,4]],[[5,6],[7,8]]] to[&int{2,2,2}]\n'
            '[[1.5,2.5],[3.5,4.5]] to[&double{2,2}]\n'
            '[4,5,6] to[&int{3}]\n'
            '[] as[Int++] to[&int{0,3}]\n'
        )
        for item, expected in zip(
            result,
            ((1, 2, 3, 4, 5, 6), (1, 2, 3, 4, 5, 6, 7, 8),
             (1.5, 2.5, 3.5, 4.5), (4, 5, 6), ()),
            strict=True,
        ):
            self.assertIsInstance(item, FFIBufferValue)
            self.assertEqual(tuple(value.value for value in item.values), expected)

    def test_shaped_ffi_conversion_rejects_invalid_shapes(self):
        for matrix, target in (
            ('[[1,2],[3]]', '&int{2,2}'),
            ('[[1,2,3],[4]]', '&int{2,2}'),
            ('[1,2,3,4]', '&int{2,2}'),
            ('[[1,2],[3,4]]', '&int{4}'),
            ('[[1,2],[3,4]]', '&int{2,3}'),
            ('[[1,2],[3,4]]', '&int{1,4}'),
        ):
            with self.subTest(matrix=matrix, target=target):
                with self.assertRaisesRegex(RuntimeError, 'ShapeFault'):
                    self._execute(f'{matrix} to[{target}]\n')

    def test_shaped_ffi_conversion_rejects_invalid_dimension_syntax(self):
        from valiance.parsing import ParseError, parse

        for target in ('&int{}', '&int{-1,2}', '&int{1.5,2}',
                       '&int{2,}', '&int{2,*}'):
            with self.subTest(target=target):
                with self.assertRaises(ParseError):
                    parse(f'[[1,2]] to[{target}]\n')

    def test_shape_mismatches_raise_shape_fault(self):
        cases = (
            ('[[1,2],[3]]', '{2,2}'),  # ragged, total would be valid for other shapes
            ('[[1,2,3],[4]]', '{2,2}'),  # total count is correct but layout is ragged
            ('[1,2,3,4]', '{2,2}'),  # correct element count, insufficient nesting
            ('[[1,2],[3,4]]', '{4}'),  # correct count, excessive nesting
            ('[[1,2],[3,4]]', '{2,3}'),
            ('[[1,2],[3,4]]', '{1,4}'),
            ('[[1,2],[3,4]]', '{-1,2}'),
            ('[[1,2],[3,4]]', '{1.5,2}'),
        )
        for matrix, shape in cases:
            with self.subTest(matrix=matrix, shape=shape):
                with self.assertRaisesRegex(RuntimeError, "ShapeFault"):
                    self._execute(f'{matrix} {shape} cArr\n')

    def test_zero_sized_axes_and_empty_shape(self):
        from valiance.elements.builtins import _flatten_rectangular_values
        from valiance.elements.builtins import PanicSignal

        dim = lambda *dims: tuple(RuntimeNumber(n) for n in dims)
        self.assertEqual(_flatten_rectangular_values([[], []], dim(2, 0)), ())
        self.assertEqual(_flatten_rectangular_values([], dim(0, 3)), ())
        with self.assertRaises(PanicSignal):
            _flatten_rectangular_values([[]], dim(2, 0))
        with self.assertRaises(PanicSignal):
            _flatten_rectangular_values([], ())

    def test_native_matrix_arguments_and_owned_flat_return(self):
        with native_library_directory() as directory:
            source_path = os.path.join(directory, "matrices.c")
            library = shared_library_path(directory, "matrices")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <stdlib.h>\n"
                    "FFI_EXPORT int matrix_sum(const int* a, int rows, int cols) {"
                    " int result=0; for(int i=0;i<rows*cols;i++) result+=a[i];"
                    " return result; }\n"
                    "FFI_EXPORT int sum_short(const short* a,int n){int r=0;"
                    " for(int i=0;i<n;i++)r+=a[i];return r;}\n"
                    "FFI_EXPORT int* make_matrix(void) {"
                    " int* a=malloc(sizeof(int)*6);"
                    " for(int i=0;i<6;i++) a[i]=i+1; return a; }\n"
                    "FFI_EXPORT void release(void* ptr) { free(ptr); }\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as native}}\n'
                'link native.matrix_sum(:&int+, :&int, :&int) -> &int as matrixSum\n'
                'link native.sum_short(:&short+, :&int) -> &int as sumShort\n'
                '@owned("release", size = 6) '
                'link native.make_matrix() -> &int+ as makeMatrix\n'
                '[[1,2,3],[4,5,6]] {2,3} cArr\n'
                '2 to[&int]\n3 to[&int]\n'
                'matrixSum\nto[Int]\n'
                '[[1,2],[3,4]] {2,2} cArr[&short]\n'
                '4 to[&int]\nsumShort\nto[Int]\n'
                'makeMatrix\nto[Int+]\nreshape {2,3}\n'
            )
            result = self._execute(source)
            self.assertEqual(result, [RuntimeNumber(21), RuntimeNumber(10),
                                      [[RuntimeNumber(n) for n in (1,2,3)],
                                       [RuntimeNumber(n) for n in (4,5,6)]]])


class FFIOwnedHandleLeaseTests(unittest.TestCase):
    def test_destroy_metadata_and_handle_state_survive_native_execution(self):
        from valiance.runtime.bytecode import NativeCallReference
        from valiance.runtime.runtime_values import FFIHandleValue, FFIScalarValue

        with native_library_directory() as directory:
            source = os.path.join(directory, "owned.c")
            library = shared_library_path(directory, "owned")
            with open(source, "w", encoding="utf-8") as stream:
                stream.write(
                    "#include <stdlib.h>\n"
                    "typedef struct H { int value; } H;\n"
                    "static int destroyed=0;\n"
                    "FFI_EXPORT H* make_h(int n){H* h=malloc(sizeof(H));h->value=n;return h;}\n"
                    "FFI_EXPORT void free_h(H* h){destroyed++;free(h);}\n"
                    "FFI_EXPORT int destroyed_count(void){return destroyed;}\n"
                )
            build_shared_library(source, library)
            handles = ("&H",)
            make = NativeCallReference(library, "make_h", ("&int",), "&H", (), handles)
            destroy = NativeCallReference(
                library, "free_h", ("&H",), None, (), handles, (0,)
            )
            count = NativeCallReference(library, "destroyed_count", (), "&int")
            program = Program(FunctionCode((
                Instruction(OpCode.PUSH_CONST, FFIScalarValue("&int", 7)),
                Instruction(OpCode.CALL_NATIVE, make),
                Instruction(OpCode.STORE_VAR, "handle"),
                Instruction(OpCode.LOAD_VAR, "handle"),
                Instruction(OpCode.CALL_NATIVE, destroy),
                Instruction(OpCode.CALL_NATIVE, count),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            restored = loads(dumps(program))
            self.assertEqual(restored, program)
            self.assertEqual(run(restored), [FFIScalarValue("&int", 1)])

    def test_handle_destroy_waits_for_active_leases(self):
        from valiance.runtime.runtime_values import FFIHandleValue
        handle = FFIHandleValue("&H", 1)
        handle.acquire_lease()
        handle.request_destroy()
        self.assertTrue(handle.alive)
        self.assertTrue(handle.destroy_pending)
        with self.assertRaises(ValueError):
            handle.acquire_lease()
        handle.release_lease()
        self.assertFalse(handle.alive)
        with self.assertRaises(ValueError):
            handle.request_destroy()



if __name__ == "__main__":
    unittest.main()

class FFIOwnedReturnTests(unittest.TestCase):
    def _compile_program(self, typed):
        from valiance.runtime import compile_program
        return compile_program(typed)

    def _compile_library(self, directory):
        source = os.path.join(directory, "owned_returns.c")
        library = shared_library_path(directory, "owned_returns")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write(
                "#include <stdlib.h>\n#include <string.h>\n"
                "static int frees=0;\n"
                "FFI_EXPORT char* greeting(void){char* p=malloc(6);memcpy(p,\"hello\",6);return p;}\n"
                "FFI_EXPORT char* bad_utf8(void){char* p=malloc(2);p[0]=(char)0xff;p[1]=0;return p;}\n"
                "FFI_EXPORT char* maybe_null(void){return 0;}\n"
                "FFI_EXPORT int* numbers(void){int* p=malloc(3*sizeof(int));p[0]=2;p[1]=4;p[2]=6;return p;}\n"
                "FFI_EXPORT char* bytes(void){char* p=malloc(4);p[0]=65;p[1]=0;p[2]=(char)255;p[3]=90;return p;}\n"
                "FFI_EXPORT void release(void* p){frees++;free(p);}\n"
                "FFI_EXPORT int free_count(void){return frees;}\n"
            )
        build_shared_library(source, library)
        return library

    def test_owned_string_and_buffer_copy_then_free(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime.runtime_values import FFIBufferValue
        with native_library_directory() as directory:
            library = self._compile_library(directory)
            source = (
                f'import {{ffi("{library}") as owned}}\n'
                '@owned("release") link owned.greeting() -> &CString as greeting\n'
                '@owned("release", size = 3) link owned.numbers() -> &int+ as numbers\n'
                'greeting\nnumbers\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(self._compile_program(typed))))
            self.assertEqual(result[0], "hello")
            self.assertIsInstance(result[1], FFIBufferValue)
            self.assertEqual(tuple(item.value for item in result[1].values), (2, 4, 6))

    def test_invalid_utf8_is_freed_before_failure(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        with native_library_directory() as directory:
            library = self._compile_library(directory)
            source = (
                f'import {{ffi("{library}") as owned}}\n'
                '@owned("release") link owned.bad_utf8() -> &CString as bad\n'
                'bad\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            with self.assertRaises(RuntimeError):
                run(self._compile_program(typed))
            from valiance.runtime.bytecode import NativeCallReference
            count_program = Program(FunctionCode((
                Instruction(OpCode.CALL_NATIVE, NativeCallReference(
                    library, "free_count", (), "&int"
                )),
                Instruction(OpCode.RETURN),
            ), name="<main>"))
            self.assertEqual(run(count_program), [FFIScalarValue("&int", 1)])

    def test_nullable_owned_return_maps_null_to_none_without_free(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        with native_library_directory() as directory:
            library = self._compile_library(directory)
            source = (
                f'import {{ffi("{library}") as owned}}\n'
                '@owned("release") @nullable '
                'link owned.maybe_null() -> &CString as maybe\n'
                'maybe\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertEqual(run(loads(dumps(self._compile_program(typed)))), [None])


    def test_owned_signed_char_buffer_converts_to_integer_list(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        with native_library_directory() as directory:
            library = self._compile_library(directory)
            source = (
                f'import {{ffi("{library}") as owned}}\n'
                '@owned("release", size = 4) link owned.bytes() -> &char+ as bytes\n'
                'bytes\n'
                'to[Int+]\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result, [[RuntimeNumber(x) for x in (65, 0, -1, 90)]])

    def test_owned_buffer_linked_conversion_returns_visible_type(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        import valiance.vtypes as T

        with native_library_directory() as directory:
            library = self._compile_library(directory)
            source = (
                f'import {{ffi("{library}") as owned}}\n'
                '@owned("release", size = 3) '
                'link owned.numbers() -> (Int+) &int+ as numbers\n'
                'numbers\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            self.assertTrue(T.same(typed[-1].typ, T.ExactList(T.Int)))
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result, [[RuntimeNumber(x) for x in (2, 4, 6)]])

    def test_unsupported_owned_linked_conversions_are_rejected(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse

        with native_library_directory() as directory:
            library = self._compile_library(directory)
            declarations = (
                '@owned("release") '
                'link owned.greeting() -> (String) &CString as greeting\n',
                '@owned("release", size = 3) @nullable '
                'link owned.numbers() -> (Int+) &int+ as numbers\n',
            )
            for declaration in declarations:
                with self.subTest(declaration=declaration):
                    source = f'import {{ffi("{library}") as owned}}\n' + declaration
                    analyser = Analyser()
                    analyser.analyse(parse(source))
                    self.assertTrue(
                        any("@owned linked-return conversion requires" in item
                            for item in analyser.diagnostics),
                        analyser.diagnostics,
                    )

class FFIComputedLinkedFieldTests(unittest.TestCase):
    def test_nested_scalar_and_embedded_array_fields_survive_bytecode(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        from valiance.runtime.runtime_values import FFIBufferValue, FFIScalarValue
        with native_library_directory() as directory:
            source_path = os.path.join(directory, "fields.c")
            library = shared_library_path(directory, "fields")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write(
                    "typedef struct { int x; int y; } Point;\n"
                    "typedef struct { Point origin; int samples[3]; } Shape;\n"
                    "FFI_EXPORT Shape make_shape(void){Shape s={{4,7},{2,3,5}};return s;}\n"
                )
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as f}}\n'
                'link f.Point as &Point =>\n  $x: &int\n  $y: &int\nend\n'
                'link f.Shape as &Shape =>\n'
                '  $origin: &Point\n  $samples: &int+ size => 3\nend\n'
                'link f.make_shape() -> &Shape as makeShape\n'
                '$shape = makeShape\n$shape.origin.x\n$shape.samples\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            result = run(loads(dumps(compile_program(typed, optimize=True))))
            self.assertEqual(result[0], FFIScalarValue("&int", 4))
            self.assertIsInstance(result[1], FFIBufferValue)
            self.assertEqual(tuple(item.value for item in result[1].values), (2, 3, 5))

class FFILinkedReturnConversionTests(unittest.TestCase):
    def test_same_module_declared_conversion_survives_optimization_and_bytecode(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        with native_library_directory() as directory:
            source_path = os.path.join(directory, "converted.c")
            library = shared_library_path(directory, "converted")
            with open(source_path, "w", encoding="utf-8") as stream:
                stream.write("FFI_EXPORT int answer(void){return 42;}\n")
            build_shared_library(source_path, library)
            source = (
                f'import {{ffi("{library}") as converted}}\n'
                '@convert(&int -> String)\n'
                'define to(value: &int) -> String => "converted" end\n'
                'link converted.answer() -> (String) &int as answer\n'
                'answer\n'
            )
            analyser = Analyser()
            typed = analyser.analyse(parse(source))
            self.assertEqual(analyser.diagnostics, [])
            program = compile_program(typed, optimize=True)
            self.assertEqual(run(program), ["converted"])
            self.assertEqual(run(loads(dumps(program))), ["converted"])

class FFICallbackMetadataTests(unittest.TestCase):
    def test_callback_metadata_survives_bytecode(self):
        from valiance.runtime.bytecode import NativeCallReference
        from valiance.vtypes import FFICallbackSpec
        reference = NativeCallReference(
            "/tmp/callback.so",
            "apply",
            ("&callback",),
            "&int",
            callbacks=(FFICallbackSpec(0, ("&int",), "&int"),),
        )
        program = Program(FunctionCode((
            Instruction(OpCode.CALL_NATIVE, reference),
            Instruction(OpCode.RETURN),
        ), name="<main>"))
        self.assertEqual(loads(dumps(program)), program)
