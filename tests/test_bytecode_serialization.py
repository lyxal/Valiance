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
                        ResolvedElementReference("+", 0),
                    ),
                    Instruction(OpCode.TRY_UNWRAP),
                    Instruction(OpCode.STACK_SHUFFLE, ("copy", ("x",), ("x",))),
                    Instruction(OpCode.SOURCE_ARGS, 1),
                    Instruction(OpCode.VALIDATE_TAG, ("#checked", 0)),
                    Instruction(OpCode.WRAP_ASSERT_ERROR),
                    Instruction(OpCode.RETURN),
                ),
                name="<main>",
            )
        )

        data = dumps(program)
        decoded = loads(data)

        self.assertTrue(data.startswith(b"VLNCBC\x2a"))
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
                            0,
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
                    Instruction(OpCode.SPAWN_CALL, (0, 1, 0)),
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
