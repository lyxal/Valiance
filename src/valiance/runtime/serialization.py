"""Portable binary bytecode serialization for Valiance programs."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    FunctionSpecializationReference,
    IndexOperationSpec,
    IndexSelectorSpec,
    Instruction,
    NativeCallReference,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    ReflectionFieldReference,
    ReflectionFieldsReference,
    ReflectionForeachReference,
    DispatchKind,
    VectorExtensionReference,
)
from valiance.runtime.runtime_values import (
    FFIBufferValue, FFIScalarValue, FFIStructValue, RuntimeNumber,
)
from valiance.vtypes import (
    DataTag,
    FFICallbackSpec,
    FFIFieldSpec,
    FFIStructSpec,
    RuntimeTypePattern,
    UnionDispatchBranch,
    Variance,
)

MAGIC_PREFIX = b"VLNCBC"
BYTECODE_VERSION = 0x44  # CALL_NUMERIC adds an opcode; older readers must reject it.
MAGIC = MAGIC_PREFIX + bytes((BYTECODE_VERSION,))

_OP_TO_BYTE = {
    OpCode.PUSH_CONST: 0x01,
    OpCode.MAKE_TYPE_METADATA: 0x51,
    OpCode.MAKE_FIELDS: 0x48,
    OpCode.CONSTRUCT: 0x50,
    OpCode.REFLECT_READ: 0x49,
    OpCode.SPECIALIZE_FUNCTION: 0x4A,
    OpCode.REFLECT_WRITE: 0x4B,
    OpCode.REFLECT_CALL: 0x4C,
    OpCode.TYPE_DISPATCH: 0x4D,
    OpCode.CONVERSION_MISSING: 0x4E,
    OpCode.SOURCE_ARGS_BORROW: 0x4F,
    OpCode.LOAD_VAR: 0x02,
    OpCode.STORE_VAR: 0x03,
    OpCode.LOAD_ELEMENT: 0x04,
    OpCode.MAKE_FUNCTION: 0x05,
    OpCode.MAKE_CLOSED_FUNCTION: 0x46,
    OpCode.CALL: 0x06,
    OpCode.BUILD_LIST: 0x07,
    OpCode.BUILD_STRING: 0x08,
    OpCode.BUILD_TUPLE: 0x09,
    OpCode.BUILD_RECORD: 0x0A,
    OpCode.BUILD_DICT: 0x0B,
    OpCode.MAKE_OBJECT_CONSTRUCTOR: 0x0C,
    OpCode.MAKE_ENUM_MEMBER: 0x0D,
    OpCode.GET_FIELD: 0x0E,
    OpCode.SET_FIELD: 0x0F,
    OpCode.JUMP: 0x10,
    OpCode.JUMP_IF_FALSE: 0x11,
    OpCode.POP: 0x12,
    OpCode.RETURN: 0x13,
    OpCode.CALL_RESOLVED_ELEMENT: 0x14,
    OpCode.CALL_NUMERIC: 0x52,
    OpCode.CALL_NATIVE: 0x42,
    OpCode.JUMP_IF_MATCH: 0x15,
    OpCode.MATCH_ERROR: 0x16,
    OpCode.ASSERT_TRUE: 0x17,
    OpCode.ASSERT_PEEK_BEGIN: 0x31,
    OpCode.ASSERT_PEEK_END: 0x32,
    OpCode.UNFOLD: 0x18,
    OpCode.GET_INDEX: 0x19,
    OpCode.SET_INDEX: 0x1A,
    OpCode.CHECK_CAST: 0x1B,
    OpCode.TRY_CAST: 0x30,
    OpCode.TRY_BEGIN: 0x1C,
    OpCode.TRY_END: 0x1D,
    OpCode.PANIC: 0x1E,
    OpCode.TRY_UNWRAP: 0x1F,
    OpCode.STACK_SHUFFLE: 0x20,
    OpCode.CYCLE_BEGIN: 0x21,
    OpCode.CYCLE_END: 0x22,
    OpCode.FOREACH: 0x23,
    OpCode.LOOP_BREAK: 0x24,
    OpCode.WHILE: 0x25,
    OpCode.SOURCE_ARGS: 0x26,
    OpCode.VALIDATE_TAG: 0x27,
    OpCode.RETURN_SIGNAL: 0x28,
    OpCode.WRAP_ASSERT_ERROR: 0x29,
    OpCode.LOAD_VAR_BORROW: 0x2A,
    OpCode.CANONICALIZE_TAGS: 0x2B,
    OpCode.BIND_TRAIT_WITNESSES: 0x47,
    OpCode.POP_N: 0x2C,
    OpCode.APPLY_DISPATCH_PLAN: 0x2D,
    OpCode.ISOLATE_STACK_BEGIN: 0x2E,
    OpCode.ISOLATE_STACK_END: 0x2F,
    OpCode.ENSURE_MIN_RANK: 0x33,
    OpCode.MATCH_BRANCH_BEGIN: 0x34,
    OpCode.MATCH_BRANCH_END: 0x35,
    OpCode.SPAWN_CALL: 0x36,
    OpCode.WAIT_TASK: 0x37,
    OpCode.WAIT_TASKS_VECTORISED: 0x38,
    OpCode.CANCEL_TASK: 0x44,
    OpCode.TIMEOUT_TASK: 0x45,
    OpCode.SCOPE_BEGIN: 0x39,
    OpCode.SCOPE_END: 0x3A,
    OpCode.CHANNEL_NEW: 0x3B,
    OpCode.CHANNEL_SEND: 0x3C,
    OpCode.CHANNEL_RECEIVE: 0x3D,
    OpCode.CHANNEL_CLOSE: 0x3E,
    OpCode.CANCEL_POLL: 0x3F,
    OpCode.LOAD_VAR_MATERIALIZE: 0x40,
    OpCode.LOAD_VAR_FORWARD: 0x41,
}
_BYTE_TO_OP = {value: key for key, value in _OP_TO_BYTE.items()}

_NONE = 0x00
_INT = 0x01
_DECIMAL = 0x02
_STRING = 0x03
_TUPLE = 0x04
_FUNCTION = 0x05
_FUNCTION_SET = 0x06
_RESOLVED_ELEMENT_REFERENCE = 0x07
_EXTENSION_RULE_REFERENCE = 0x08
_VECTOR_EXTENSION_REFERENCE = 0x09
_OBJECT_CONSTRUCTOR_REFERENCE = 0x0A
_BOOL = 0x0B
_INDEX_OPERATION_SPEC = 0x0C
_FFI_SCALAR = 0x0D
_FLOAT = 0x0E
_NATIVE_CALL_REFERENCE = 0x0F
_FFI_STRUCT = 0x10
_FFI_BUFFER = 0x11
_REFLECTION_FIELDS_REFERENCE = 0x12
_REFLECTION_FOREACH_REFERENCE = 0x13
_FUNCTION_SPECIALIZATION_REFERENCE = 0x14


def _validate_occurrence_effects(
    params: tuple[str, ...],
    return_count: int | None,
    effects: tuple[int | None, ...],
) -> None:
    """Validate one function's serialized occurrence-flow contract."""
    if return_count is not None and len(effects) != return_count:
        raise BytecodeFormatError(
            "function occurrence effects must match its return count"
        )
    if any(
        item is not None and (item < 0 or item >= len(params))
        for item in effects
    ):
        raise BytecodeFormatError(
            "function occurrence effect references an invalid parameter"
        )


class BytecodeFormatError(Exception):
    """Raised when bytecode bytes cannot be decoded."""


def dumps(program: Program) -> bytes:
    """Serialize a bytecode program to portable binary bytes."""
    try:
        writer = _Writer()
        writer.bytes(MAGIC)
        _validate_tag_parent_metadata(program.tag_parents)
        _validate_concurrency_bytecode(program.main)
        writer.function(program.main)
        writer.u32(len(program.tag_parents))
        for variant, parent in program.tag_parents:
            writer.string(variant)
            writer.string(parent)
        return writer.finish()
    except BytecodeFormatError:
        raise
    except (OverflowError, RecursionError, struct.error, UnicodeEncodeError) as exc:
        raise BytecodeFormatError("invalid Valiance bytecode value") from exc


def loads(data: bytes) -> Program:
    """Deserialize a bytecode program from portable binary bytes."""
    reader = _Reader(data)
    import decimal

    try:
        prefix = reader.take(len(MAGIC_PREFIX))
        if prefix != MAGIC_PREFIX:
            raise BytecodeFormatError("invalid Valiance bytecode magic")
        version = reader.u8()
        if version not in (0x43, BYTECODE_VERSION):
            raise BytecodeFormatError(
                f"unsupported Valiance bytecode version {version}; "
                f"expected 0x43 or {BYTECODE_VERSION}"
            )
        main = reader.function()
        tag_parents = tuple(
            (reader.string(), reader.string()) for _ in range(reader.u32())
        )
        _validate_tag_parent_metadata(tag_parents)
        program = Program(main, tag_parents)
        _validate_concurrency_bytecode(program.main)
        reader.expect_eof()
        return program
    except (
        decimal.InvalidOperation,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        struct.error,
    ) as exc:
        raise BytecodeFormatError("invalid Valiance bytecode payload") from exc


def _validate_implementation_identity(identity: object, *, allow_none: bool = False) -> None:
    """Reject missing or positional selectors in the current portable format."""
    if identity is None and allow_none:
        return
    if not isinstance(identity, str) or not identity:
        raise BytecodeFormatError("invalid or missing implementation identity")


def _validate_concurrency_bytecode(code: FunctionCode) -> None:
    """Reject malformed concurrency payloads and unbalanced scope bytecode."""
    if type(code.requires_specialization) is not bool:
        raise BytecodeFormatError("invalid function specialization flag")
    scope_depth = 0
    for index, instruction in enumerate(code.instructions):
        op = instruction.op
        argument = instruction.arg
        if op is OpCode.TRY_UNWRAP and argument is not None and argument is not True:
            raise BytecodeFormatError("invalid unwrap return policy")
        if op is OpCode.BUILD_RECORD:
            if (not isinstance(argument, tuple) or len(argument) != 2 or not isinstance(argument[0], tuple)
                    or any(not isinstance(name, str) or not name for name in argument[0])
                    or len(set(argument[0])) != len(argument[0])
                    or not isinstance(argument[1], str) or not argument[1]):
                raise BytecodeFormatError("invalid reified record layout")
        if op is OpCode.SOURCE_ARGS_BORROW and (type(argument) is not int or argument < 0):
            raise BytecodeFormatError("invalid borrowed argument source count")
        if op is OpCode.CONSTRUCT:
            if (not isinstance(argument, tuple) or len(argument) != 4
                    or not isinstance(argument[0], str)
                    or argument[0] not in {"uninit", "finished?", "finish", "finish?", "finish+args", "finish?+args"}
                    or (argument[2] is not None and (not isinstance(argument[2], str) or not argument[2]))
                    or not isinstance(argument[3], tuple)
                    or not (
                        len(argument[3]) == 3 and argument[3][0] == "nominal"
                        and isinstance(argument[3][1], str) and bool(argument[3][1])
                        and isinstance(argument[3][2], tuple)
                        and all(isinstance(item, str) and bool(item) for item in argument[3][2])
                        or len(argument[3]) == 2 and argument[3][0] == "ffi"
                        and isinstance(argument[3][1], str) and argument[3][1].startswith("&")
                        and argument[2] is None
                    )):
                raise BytecodeFormatError("invalid reflection construction payload")
            _validate_reflection_fields(argument[1])
            if argument[0].endswith("+args") and argument[3][0] != "ffi":
                raise BytecodeFormatError("native constructor arguments require opaque storage")
            if (argument[3][0] == "ffi" and
                    (argument[1].owner != argument[3][1] or argument[1].fields)):
                raise BytecodeFormatError("native construction must have empty opaque storage")
        if op is OpCode.TYPE_DISPATCH:
            if (not isinstance(argument, tuple) or len(argument) != 2
                    or not isinstance(argument[0], tuple)
                    or type(argument[1]) is not int or not 0 <= argument[1] < len(code.instructions)):
                raise BytecodeFormatError("invalid type descriptor dispatch payload")
            signatures = set()
            for entry in argument[0]:
                if (not isinstance(entry, tuple) or len(entry) != 2
                        or not isinstance(entry[0], str) or not entry[0]
                        or type(entry[1]) is not int or not 0 <= entry[1] < len(code.instructions)
                        or entry[0] in signatures):
                    raise BytecodeFormatError("invalid type descriptor dispatch entry")
                signatures.add(entry[0])
        if op is OpCode.CONVERSION_MISSING and type(argument) is not bool:
            raise BytecodeFormatError("invalid missing conversion policy")
        if op is OpCode.REFLECT_CALL:
            if (not isinstance(argument, tuple) or len(argument) != 6
                    or type(argument[0]) is not bool or type(argument[2]) is not bool
                    or not isinstance(argument[3], tuple) or not isinstance(argument[4], tuple)
                    or any(type(depth) is not int or depth < 0 for depth in argument[3])
                    or any(rank is not None and (type(rank) is not int or rank < 0) for rank in argument[4])
                    or (argument[5] is not None and not isinstance(argument[5], VectorExtensionReference))):
                raise BytecodeFormatError("invalid fallible reflection call payload")
            width = 3 if argument[0] else 2
            if (argument[3] and len(argument[3]) != width) or (argument[4] and len(argument[4]) != width):
                raise BytecodeFormatError("invalid fallible reflection vectorisation plan")
            op = OpCode.REFLECT_WRITE if argument[0] else OpCode.REFLECT_READ
            argument = argument[1]
        if op in {OpCode.REFLECT_READ, OpCode.REFLECT_WRITE}:
            _validate_reflection_access(argument, op is OpCode.REFLECT_WRITE)
        if op is OpCode.SPECIALIZE_FUNCTION:
            if isinstance(argument, FunctionSpecializationReference):
                _validate_function_specialization(argument)
            elif not isinstance(argument, FunctionCode) or argument.requires_specialization:
                raise BytecodeFormatError("specialization requires checked function bytecode")
        if op is OpCode.MAKE_TYPE_METADATA:
            _validate_type_metadata(argument)
        if op is OpCode.MAKE_FIELDS:
            if isinstance(argument, tuple):
                if (len(argument) != 2 or argument[0] != "choices"
                        or not isinstance(argument[1], tuple) or not argument[1]):
                    raise BytecodeFormatError("invalid value reflection layout choices")
                owners = []
                for choice in argument[1]:
                    if (not isinstance(choice, tuple) or len(choice) != 2
                            or not isinstance(choice[0], tuple) or not choice[0]):
                        raise BytecodeFormatError("invalid value reflection layout choice")
                    _validate_reflection_fields(choice[1])
                    owners.append(choice[1].owner)
                if len(set(owners)) != len(owners):
                    raise BytecodeFormatError("duplicate value reflection owner")
            else:
                _validate_reflection_fields(argument)
        if op is OpCode.FOREACH and isinstance(argument, ReflectionForeachReference):
            _validate_reflection_foreach(argument)
        if op is OpCode.FOREACH and isinstance(argument, tuple) and argument and argument[0] == "reflection_choices":
            if len(argument) != 2 or not isinstance(argument[1], tuple) or not argument[1]:
                raise BytecodeFormatError("invalid reflective iteration choices")
            owners = []
            for plan in argument[1]:
                if not isinstance(plan, ReflectionForeachReference):
                    raise BytecodeFormatError("invalid reflective iteration choice")
                _validate_reflection_foreach(plan)
                owners.append(plan.layout.owner)
            if len(set(owners)) != len(owners):
                raise BytecodeFormatError("duplicate reflective iteration owner")
        if op is OpCode.SCOPE_BEGIN:
            _validate_scope_payload(argument, "scope begin")
            scope_depth += 1
        elif op is OpCode.SCOPE_END:
            _validate_scope_payload(argument, "scope end")
            scope_depth -= 1
            if scope_depth < 0:
                raise BytecodeFormatError(
                    f"scope end without matching begin at instruction {index}"
                )
        elif op is OpCode.MAKE_CLOSED_FUNCTION and not isinstance(argument, FunctionCode):
            raise BytecodeFormatError("closed concurrent function must contain function bytecode")
        elif op is OpCode.VALIDATE_TAG:
            if not isinstance(argument, tuple) or len(argument) != 4:
                raise BytecodeFormatError("invalid tag validator payload")
            _validate_implementation_identity(argument[1], allow_none=True)
        elif op is OpCode.SPAWN_CALL:
            if (
                not isinstance(argument, tuple)
                or len(argument) != 9
                or not all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in argument[:2]
                )
                or (argument[2] is not None and (
                    not isinstance(argument[2], str) or not argument[2]
                ))
                or (
                    len(argument) >= 4
                    and (
                        not isinstance(argument[3], tuple)
                        or len(argument[3]) != argument[0]
                        or not all(isinstance(item, bool) for item in argument[3])
                    )
                )
                or (
                    len(argument) in {8, 9}
                    and (
                        not isinstance(argument[4], bool)
                        or not isinstance(argument[5], tuple)
                        or not all(isinstance(item, int) and item >= 0 for item in argument[5])
                        or not isinstance(argument[6], tuple)
                        or not all(item is None or isinstance(item, int) and item >= 0 for item in argument[6])
                        or not isinstance(argument[7], tuple)
                        or (
                            len(argument) == 9
                            and argument[8] is not None
                            and not isinstance(argument[8], str)
                        )
                    )
                )
            ):
                raise BytecodeFormatError("invalid spawn call payload")
        elif op in {OpCode.WAIT_TASK, OpCode.WAIT_TASKS_VECTORISED, OpCode.TIMEOUT_TASK}:
            count = argument[0] if isinstance(argument, tuple) and len(argument) == 2 else argument
            location = argument[1] if isinstance(argument, tuple) and len(argument) == 2 else None
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
                or (location is not None and not isinstance(location, str))
            ):
                raise BytecodeFormatError(f"invalid {op.name.lower()} payload")
        elif op is OpCode.CHANNEL_NEW:
            if not (
                isinstance(argument, bool)
                or (
                    isinstance(argument, tuple)
                    and len(argument) == 2
                    and isinstance(argument[0], bool)
                    and (argument[1] is None or isinstance(argument[1], str))
                )
            ):
                raise BytecodeFormatError("invalid channel construction payload")
        elif op in {
            OpCode.CHANNEL_SEND,
            OpCode.CHANNEL_RECEIVE,
            OpCode.CHANNEL_CLOSE,
        } and argument is not None and not (
            isinstance(argument, str)
            and argument.count(":") == 1
            and all(part.isdigit() for part in argument.split(":"))
        ):
            raise BytecodeFormatError(
                f"{op.name.lower()} must carry only a source location"
            )
        if op is OpCode.CANCEL_POLL and argument is not None:
            raise BytecodeFormatError("cancel_poll must not carry a payload")
        _validate_nested_concurrency_values(argument)
    if scope_depth:
        raise BytecodeFormatError(
            f"function ends with {scope_depth} unclosed concurrency scope(s)"
        )


def _validate_scope_payload(value: object, operation: str) -> None:
    """Accept scope counts plus optional source location metadata."""
    if value is None:
        return
    if (
        not isinstance(value, tuple)
        or len(value) not in {2, 3}
        or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in value[:2]
        )
        or (len(value) == 3 and value[2] is not None and not isinstance(value[2], str))
    ):
        raise BytecodeFormatError(f"invalid {operation} payload")


def _validate_reflection_access(value: object, writing: bool) -> None:
    """Validate scalar access contracts or a finite owner dispatch table."""
    if isinstance(value, tuple) and value and value[0] == "choices":
        if len(value) != 2 or not isinstance(value[1], tuple) or not value[1]:
            raise BytecodeFormatError("invalid reflective access choices")
        owners = []
        for choice in value[1]:
            if not isinstance(choice, tuple) or not choice or not isinstance(choice[0], ReflectionFieldsReference):
                raise BytecodeFormatError("invalid reflective access choice")
            _validate_reflection_access(choice, writing)
            owners.append(choice[0].owner)
        if len(set(owners)) != len(owners):
            raise BytecodeFormatError("duplicate reflective access owner")
        return
    if (not isinstance(value, tuple) or len(value) != (4 if writing else 3)
            or type(value[1]) is not bool or not isinstance(value[2], tuple)):
        raise BytecodeFormatError("invalid reflective write payload" if writing else "invalid reflective read payload")
    _validate_reflection_fields(value[0])
    if writing and (not isinstance(value[3], tuple) or len(value[3]) != len(value[0].fields)
                    or any(spec is not None and not isinstance(spec, tuple) for spec in value[3])):
        raise BytecodeFormatError("invalid reflective write field contracts")


def _validate_type_metadata(value: object) -> None:
    """Reject malformed allocation recipes before executing metadata bytecode."""
    if not isinstance(value, tuple) or not value or not isinstance(value[0], str):
        raise BytecodeFormatError("invalid type metadata recipe")
    kind = value[0]
    if kind in {"type", "text", "int"}:
        if (len(value) != 2 or (type(value[1]) is not int if kind == "int" else
                               not isinstance(value[1], str) or kind == "type" and not value[1])):
            raise BytecodeFormatError("invalid type metadata scalar")
        return
    if kind == "list" and len(value) == 2 and isinstance(value[1], tuple):
        for item in value[1]:
            _validate_type_metadata(item)
        return
    if kind in {"info", "record"}:
        if len(value) != 3 or not isinstance(value[1], str) or not value[1]:
            raise BytecodeFormatError("invalid type metadata owner")
        value = ("record", value[2])
        kind = "record"
    if kind == "record" and len(value) == 2 and isinstance(value[1], tuple):
        names = set()
        for item in value[1]:
            if (not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str)
                    or not item[0] or item[0] in names):
                raise BytecodeFormatError("invalid type metadata properties")
            names.add(item[0])
            _validate_type_metadata(item[1])
        return
    raise BytecodeFormatError("invalid type metadata recipe")


def _validate_reflection_fields(value: object) -> None:
    """Reject malformed storage evidence before serializing or executing a file."""
    if (not isinstance(value, ReflectionFieldsReference)
            or not isinstance(value.owner, str) or not value.owner
            or not isinstance(value.fields, tuple)
            or any(not isinstance(field, ReflectionFieldReference) for field in value.fields)):
        raise BytecodeFormatError("invalid reflection field metadata")
    if (any(not isinstance(field.name, str) or not field.name
                   or not isinstance(field.value_type, str) or not field.value_type
                   or not isinstance(field.visibility, str)
                   or field.visibility not in {"public", "readable", "private"}
                   for field in value.fields)
            or len({field.name for field in value.fields}) != len(value.fields)):
        raise BytecodeFormatError("invalid reflection field metadata")


def _validate_reflection_foreach(value: ReflectionForeachReference) -> None:
    """Validate specialized callback contracts and their complete storage layout."""
    _validate_reflection_fields(value.layout)
    if (type(value.has_index) is not bool or type(value.completion_count) is not int
            or value.completion_count < 1 or not isinstance(value.bodies, tuple)
            or len(value.bodies) != len(value.layout.fields)
            or any(not isinstance(body, FunctionCode)
                   or len(body.params) != 1 + int(value.has_index) for body in value.bodies)):
        raise BytecodeFormatError("invalid reflective foreach metadata")


def _validate_function_specialization(reference: FunctionSpecializationReference) -> None:
    """Require complete, uniquely linked executable union specializations."""
    if (not isinstance(reference.overloads, tuple) or not reference.overloads
            or not isinstance(reference.dispatch_plan, tuple) or not reference.dispatch_plan
            or any(not isinstance(branch, UnionDispatchBranch) for branch in reference.dispatch_plan)):
        raise BytecodeFormatError("empty function specialization dispatch")
    identities = set()
    if (not isinstance(reference.runtime_names, tuple)
            or reference.runtime_names and len(reference.runtime_names) != len(reference.overloads)
            or any(name is not None and (not isinstance(name, str) or not name)
                   for name in reference.runtime_names)):
        raise BytecodeFormatError("invalid specialization declaration bindings")
    for entry in reference.overloads:
        if not isinstance(entry, tuple) or len(entry) != 4:
            raise BytecodeFormatError("invalid function specialization entry")
        original, identity, code, static_values = entry
        if ((original is not None and (not isinstance(original, str) or not original)) or not isinstance(identity, str)
                or not identity or identity in identities or not isinstance(static_values, tuple)
                or (code is not None and (not isinstance(code, FunctionCode)
                    or code.requires_specialization or code.implementation_id != identity
                    or len(static_values) > len(code.params)))):
            raise BytecodeFormatError("invalid checked function specialization")
        identities.add(identity)
    if {branch.implementation_id for branch in reference.dispatch_plan} != identities:
        raise BytecodeFormatError("unlinked function specialization dispatch")
    codes = {identity: (code, static_values) for _, identity, code, static_values in reference.overloads}
    for branch in reference.dispatch_plan:
        code, static_values = codes[branch.implementation_id]
        if code is not None and len(branch.params) != len(code.params) - len(static_values):
            raise BytecodeFormatError("function specialization dispatch arity mismatch")


def _validate_nested_concurrency_values(value: object) -> None:
    """Validate nested function bytecode found inside instruction payloads."""
    if isinstance(value, FunctionSpecializationReference):
        _validate_function_specialization(value)
        for _, _, code, _ in value.overloads:
            if code is not None:
                _validate_concurrency_bytecode(code)
    elif isinstance(value, ReflectionForeachReference):
        _validate_reflection_foreach(value)
        for body in value.bodies:
            _validate_concurrency_bytecode(body)
    elif isinstance(value, FunctionCode):
        _validate_concurrency_bytecode(value)
    elif isinstance(value, FunctionSetCode):
        for overload in value.overloads:
            _validate_concurrency_bytecode(overload)
    elif isinstance(value, tuple):
        for item in value:
            _validate_nested_concurrency_values(item)
    elif isinstance(value, list):
        for item in value:
            _validate_nested_concurrency_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_nested_concurrency_values(item)


def _validate_tag_parent_metadata(
    tag_parents: tuple[tuple[str, str], ...],
) -> None:
    """Reject malformed variant-parent metadata at the bytecode boundary."""
    mapping = dict(tag_parents)
    if len(mapping) != len(tag_parents):
        raise BytecodeFormatError("duplicate variant tag parent metadata")
    if any(variant == parent for variant, parent in tag_parents):
        raise BytecodeFormatError("variant tag cannot be its own parent")
    if any(parent in mapping for parent in mapping.values()):
        raise BytecodeFormatError("variant tag parent must be a computed tag")


class _Writer:
    def __init__(self) -> None:
        """Initialize this writer."""
        self.buffer = bytearray()

    def finish(self) -> bytes:
        """Return the complete immutable bytecode buffer."""
        return bytes(self.buffer)

    def bytes(self, value: bytes) -> None:
        """Append raw bytes to the bytecode buffer."""
        self.buffer.extend(value)

    def u8(self, value: int) -> None:
        """Encode an unsigned eight-bit integer."""
        if not 0 <= value <= 0xFF:
            raise BytecodeFormatError(f"u8 out of range: {value}")
        self.buffer.append(value)

    def u32(self, value: int) -> None:
        """Encode an unsigned thirty-two-bit integer."""
        if not 0 <= value <= 0xFFFFFFFF:
            raise BytecodeFormatError(f"u32 out of range: {value}")
        self.buffer.extend(struct.pack(">I", value))

    def i64(self, value: int) -> None:
        """Encode a signed sixty-four-bit integer."""
        self.buffer.extend(struct.pack(">q", value))

    def string(self, value: str) -> None:
        """Encode a length-prefixed UTF-8 string."""
        encoded = value.encode("utf-8")
        self.u32(len(encoded))
        self.bytes(encoded)

    def optional_string(self, value: str | None) -> None:
        """Encode an optional UTF-8 string with a presence marker."""
        self.u8(0 if value is None else 1)
        if value is not None:
            self.string(value)

    def value(self, value: Any) -> None:
        """Encode one supported tagged bytecode value."""
        if value is None:
            self.u8(_NONE)
        elif isinstance(value, bool):
            self.u8(_BOOL)
            self.bool(value)
        elif isinstance(value, int):
            self.u8(_INT)
            self.i64(value)
        elif isinstance(value, float):
            self.u8(_FLOAT)
            self.bytes(struct.pack(">d", value))
        elif isinstance(value, RuntimeNumber):
            self.u8(_DECIMAL)
            self.string(str(value))
        elif isinstance(value, str):
            self.u8(_STRING)
            self.string(value)
        elif isinstance(value, FFIScalarValue):
            self.u8(_FFI_SCALAR)
            self.string(value.ffi_type)
            self.value(value.value)
        elif isinstance(value, FFIBufferValue):
            self.u8(_FFI_BUFFER)
            self.string(value.element_type)
            self.value(value.values)
        elif isinstance(value, FFIStructValue):
            self.u8(_FFI_STRUCT)
            self.string(value.ffi_type)
            self.value(value.fields)
        elif isinstance(value, tuple):
            self.u8(_TUPLE)
            self.u32(len(value))
            for item in value:
                self.value(item)
        elif isinstance(value, FunctionCode):
            self.u8(_FUNCTION)
            self.function(value)
        elif isinstance(value, FunctionSpecializationReference):
            self.u8(_FUNCTION_SPECIALIZATION_REFERENCE)
            self.value(value.overloads)
            self.u32(len(value.dispatch_plan))
            for branch in value.dispatch_plan:
                self.union_dispatch_branch(branch)
            self.value(value.runtime_names)
        elif isinstance(value, FunctionSetCode):
            self.u8(_FUNCTION_SET)
            self.function_set(value)
        elif isinstance(value, ResolvedElementReference):
            self.u8(_RESOLVED_ELEMENT_REFERENCE)
            self.resolved_element_reference(value)
        elif isinstance(value, NativeCallReference):
            self.u8(_NATIVE_CALL_REFERENCE)
            self.string(value.library)
            self.string(value.symbol)
            self.value(value.param_types)
            self.optional_string(value.return_type)
            self.value(
                tuple(
                    (
                        item.name,
                        tuple(
                            (field.name, field.type_name, field.fixed_size)
                            for field in item.fields
                        ),
                    )
                    for item in value.structs
                )
            )
            self.value(value.handles)
            self.value(value.destroy_params)
            self.optional_string(value.owned_return_free)
            self.value(value.owned_return_count)
            self.bool(value.nullable_return)
            self.value(tuple(
                (item.index, item.param_types, item.return_type)
                for item in value.callbacks
            ))
        elif isinstance(value, ExtensionRuleReference):
            self.u8(_EXTENSION_RULE_REFERENCE)
            self.extension_rule_reference(value)
        elif isinstance(value, VectorExtensionReference):
            self.u8(_VECTOR_EXTENSION_REFERENCE)
            self.vector_extension_reference(value)
        elif isinstance(value, ObjectConstructorReference):
            self.u8(_OBJECT_CONSTRUCTOR_REFERENCE)
            self.object_constructor_reference(value)
        elif isinstance(value, IndexOperationSpec):
            self.u8(_INDEX_OPERATION_SPEC)
            self.index_operation_spec(value)
        elif isinstance(value, ReflectionFieldsReference):
            self.u8(_REFLECTION_FIELDS_REFERENCE)
            self.string(value.owner)
            self.u32(len(value.fields))
            for field in value.fields:
                self.string(field.name)
                self.string(field.value_type)
                self.string(field.visibility)
        elif isinstance(value, ReflectionForeachReference):
            _validate_reflection_foreach(value)
            self.u8(_REFLECTION_FOREACH_REFERENCE)
            self.value(value.layout)
            self.value(value.bodies)
            self.bool(value.has_index)
            self.u32(value.completion_count)
        else:
            raise BytecodeFormatError(f"cannot serialize bytecode value {value!r}")

    def index_operation_spec(self, spec: IndexOperationSpec) -> None:
        """Encode one named indexed-read or indexed-write payload."""
        self.u32(len(spec.selectors))
        for selector in spec.selectors:
            self.bool(selector.is_slice)
            self.bool(selector.has_start)
            self.bool(selector.has_stop)
            self.bool(selector.has_step)
        self.bool(spec.spread)
        self.bool(spec.grouped_update)

    def bool(self, value: bool) -> None:
        """Encode a Boolean presence byte."""
        self.u8(1 if value else 0)

    def optional_int(self, value: int | None) -> None:
        """Encode an optional integer with a presence marker."""
        self.u8(0 if value is None else 1)
        if value is not None:
            self.i64(value)

    def resolved_element_reference(self, reference: ResolvedElementReference) -> None:
        """Encode resolved element reference in the portable bytecode stream."""
        _validate_implementation_identity(reference.implementation_id, allow_none=bool(reference.dispatch_kind & DispatchKind.TRAIT_WITNESS))
        self.string(reference.name)
        self.bool(reference.vectorised)
        self.value(reference.vectorised_depths)
        self.value(reference.vectorised_target_ranks)
        self.value(reference.return_collection_ranks)
        self.u32(len(reference.return_tags))
        for tags in reference.return_tags:
            self.u32(len(tags))
            for tag in tags:
                self.string(tag.name)
                self.i64(tag.depth)
                self.bool(tag.absent)
        self.value(reference.return_tag_specs)
        self.value(reference.type_args)
        self.value(reference.static_values)
        self.optional_int(reference.arity_override)
        self.optional_int(reference.consumed_override)
        self.u8(int(reference.dispatch_kind))
        self.u32(len(reference.union_dispatch_plan))
        for branch in reference.union_dispatch_plan:
            self.union_dispatch_branch(branch)
        self.value(reference.extension)
        self.value(reference.structural_signature)
        self.optional_string(reference.implementation_id)
        self.value(reference.structural_witnesses)
        self.value(reference.callable_witnesses)

    def extension_rule_reference(self, reference: ExtensionRuleReference) -> None:
        """Encode extension rule reference in the portable bytecode stream."""
        self.u32(len(reference.presence))
        for present in reference.presence:
            self.bool(present)
        self.value(reference.function)

    def vector_extension_reference(
        self,
        reference: VectorExtensionReference,
    ) -> None:
        """Encode vector extension reference in the portable bytecode stream."""
        self.value(reference.default)
        self.u32(len(reference.rules))
        for rule in reference.rules:
            self.extension_rule_reference(rule)
        self.value(reference.selector)

    def object_constructor_reference(
        self,
        reference: ObjectConstructorReference,
    ) -> None:
        """Encode object constructor reference in the portable bytecode stream."""
        self.string(reference.type_name)
        self.value(reference.fields)
        self.value(reference.required)
        self.value(reference.defaults)
        self.value(reference.runtime_metadata)
        self.value(reference.initializer)

    def function_set(self, function_set: FunctionSetCode) -> None:
        """Encode function set in the portable bytecode stream."""
        self.u32(len(function_set.overloads))
        for overload in function_set.overloads:
            self.function(overload)
        self.u32(len(function_set.dispatch_plan))
        for branch in function_set.dispatch_plan:
            self.union_dispatch_branch(branch)

    def union_dispatch_branch(self, branch: UnionDispatchBranch) -> None:
        """Encode union dispatch branch in the portable bytecode stream."""
        self.u32(len(branch.params))
        for pattern in branch.params:
            self.runtime_type_pattern(pattern)
        if branch.implementation_id is None:
            raise BytecodeFormatError("union dispatch branch is missing its implementation identity")
        self.string(branch.implementation_id)

    def runtime_type_pattern(self, pattern: RuntimeTypePattern) -> None:
        """Encode runtime type pattern in the portable bytecode stream."""
        self.string(pattern.kind)
        self.optional_string(pattern.name)
        self.u32(len(pattern.children))
        for child in pattern.children:
            self.runtime_type_pattern(child)
        self.u32(len(pattern.accepted_names))
        for name in pattern.accepted_names:
            self.string(name)
        self.u32(len(pattern.variances))
        for variance in pattern.variances:
            self.u8(
                {
                    Variance.INVARIANT: 0,
                    Variance.COVARIANT: 1,
                    Variance.CONTRAVARIANT: 2,
                }[variance]
            )
        self.u32(len(pattern.tags))
        for tag in pattern.tags:
            self.string(tag.name)
            self.i64(tag.depth)
            self.bool(tag.absent)
        self.optional_int(pattern.rank)
        self.optional_string(pattern.collection_kind)

    def function(self, function: FunctionCode) -> None:
        """Encode function in the portable bytecode stream."""
        _validate_occurrence_effects(
            function.params,
            function.return_count,
            function.occurrence_effects,
        )
        self.optional_string(function.name)
        self.u8(1 if function.cycle_params else 0)
        self.u32(function.cycle_param_offset)
        self.u8(1 if function.accepts_stack_inputs else 0)
        self.u8(1 if function.recursive else 0)
        self.u32(len(function.params))
        for param in function.params:
            self.string(param)
        self.u32(len(function.element_tags))
        for tag in function.element_tags:
            self.string(tag)
        self.u8(1 if function.multi else 0)
        self.u32(len(function.dispatch_types))
        for typ in function.dispatch_types:
            self.optional_string(typ)
        self.u32(len(function.signature_types))
        for typ in function.signature_types:
            self.string(typ)
        self.u32(len(function.result_signature_types))
        for typ in function.result_signature_types:
            self.string(typ)
        self.u32(len(function.generic_type_parameters))
        for name in function.generic_type_parameters:
            self.string(name)
        self.value(function.structural_parameters)
        self.optional_string(function.implementation_id)
        self.optional_int(function.return_count)
        self.value(function.occurrence_effects)
        self.u32(len(function.return_tags))
        for tags in function.return_tags:
            self.u32(len(tags))
            for tag in tags:
                self.string(tag.name)
                self.i64(tag.depth)
                self.bool(tag.absent)
        self.value(function.return_tag_specs)
        self.value(function.return_collection_ranks)
        self.value(function.param_collection_ranks)
        self.bool(function.requires_specialization)
        self.u32(len(function.instructions))
        for instruction in function.instructions:
            try:
                op = _OP_TO_BYTE[instruction.op]
            except KeyError as exc:
                raise BytecodeFormatError(
                    f"unknown bytecode operation {instruction.op!r}"
                ) from exc
            self.u8(op)
            self.value(instruction.arg)


@dataclass(slots=True)
class _Reader:
    data: bytes
    offset: int = 0

    def expect(self, value: bytes) -> None:
        """Consume and validate an expected byte sequence."""
        if self.data[self.offset : self.offset + len(value)] != value:
            raise BytecodeFormatError("not a Valiance bytecode file")
        self.offset += len(value)

    def expect_eof(self) -> None:
        """Reject trailing data after the expected bytecode payload."""
        if self.offset != len(self.data):
            raise BytecodeFormatError("trailing bytes after Valiance bytecode")

    def take(self, count: int) -> bytes:
        """Consume an exact number of bytes or reject truncated input."""
        end = self.offset + count
        if end > len(self.data):
            raise BytecodeFormatError("truncated Valiance bytecode")
        value = self.data[self.offset : end]
        self.offset = end
        return value

    def u8(self) -> int:
        """Decode an unsigned eight-bit integer."""
        return self.take(1)[0]

    def u32(self) -> int:
        """Decode an unsigned thirty-two-bit integer."""
        return struct.unpack(">I", self.take(4))[0]

    def i64(self) -> int:
        """Decode a signed sixty-four-bit integer."""
        return struct.unpack(">q", self.take(8))[0]

    def string(self) -> str:
        """Decode a length-prefixed UTF-8 string."""
        return self.take(self.u32()).decode("utf-8")

    def optional_string(self) -> str | None:
        """Decode an optional UTF-8 string."""
        marker = self.u8()
        if marker == 0:
            return None
        if marker == 1:
            return self.string()
        raise BytecodeFormatError(f"invalid optional string marker {marker}")

    def value(self) -> Any:
        """Decode one supported tagged bytecode value."""
        tag = self.u8()
        if tag == _NONE:
            return None
        if tag == _INT:
            return self.i64()
        if tag == _BOOL:
            return self.bool()
        if tag == _FLOAT:
            return struct.unpack(">d", self.take(8))[0]
        if tag == _DECIMAL:
            return RuntimeNumber(self.string())
        if tag == _STRING:
            return self.string()
        if tag == _FFI_BUFFER:
            element_type, values = self.string(), self.value()
            if not isinstance(values, tuple):
                raise BytecodeFormatError("invalid FFI buffer values")
            try:
                return FFIBufferValue(element_type, values)
            except (TypeError, ValueError) as exc:
                raise BytecodeFormatError(f"invalid FFI buffer: {exc}") from exc
        if tag == _FFI_STRUCT:
            ffi_type, fields = self.string(), self.value()
            if not isinstance(fields, tuple):
                raise BytecodeFormatError("invalid FFI struct fields")
            try:
                return FFIStructValue(ffi_type, fields)
            except (TypeError, ValueError) as exc:
                raise BytecodeFormatError(f"invalid FFI struct value: {exc}") from exc
        if tag == _FFI_SCALAR:
            ffi_type = self.string()
            payload = self.value()
            try:
                return FFIScalarValue(ffi_type, payload)
            except (TypeError, ValueError) as exc:
                raise BytecodeFormatError(f"invalid FFI scalar value: {exc}") from exc
        if tag == _TUPLE:
            return tuple(self.value() for _ in range(self.u32()))
        if tag == _FUNCTION:
            return self.function()
        if tag == _FUNCTION_SPECIALIZATION_REFERENCE:
            reference = FunctionSpecializationReference(
                self.value(), tuple(self.union_dispatch_branch() for _ in range(self.u32())),
                self.value(),
            )
            _validate_function_specialization(reference)
            return reference
        if tag == _FUNCTION_SET:
            return self.function_set()
        if tag == _RESOLVED_ELEMENT_REFERENCE:
            return self.resolved_element_reference()
        if tag == _NATIVE_CALL_REFERENCE:
            library, symbol = self.string(), self.string()
            (
                params, result, raw_structs, handles, destroy_params,
                owned_return_free, owned_return_count, nullable_return, raw_callbacks,
            ) = (
                self.value(), self.optional_string(), self.value(), self.value(),
                self.value(), self.optional_string(), self.value(), self.bool(), self.value(),
            )
            if not isinstance(params, tuple) or not all(isinstance(x, str) for x in params):
                raise BytecodeFormatError("invalid native call parameter types")
            try:
                structs = tuple(
                    FFIStructSpec(
                        name,
                        tuple(
                            FFIFieldSpec(field_name, type_name, fixed_size)
                            for field_name, type_name, fixed_size in fields
                        ),
                    )
                    for name, fields in raw_structs
                )
            except (TypeError, ValueError) as exc:
                raise BytecodeFormatError("invalid native struct metadata") from exc
            if not isinstance(handles, tuple) or not all(
                isinstance(item, str) and item.startswith("&") for item in handles
            ) or len(set(handles)) != len(handles):
                raise BytecodeFormatError("invalid native handle metadata")
            if not isinstance(destroy_params, tuple) or not all(
                isinstance(item, int) and 0 <= item < len(params)
                for item in destroy_params
            ):
                raise BytecodeFormatError("invalid native destroy parameter metadata")
            if owned_return_count is not None and (
                not isinstance(owned_return_count, int) or owned_return_count < 0
            ):
                raise BytecodeFormatError("invalid native owned-return size")
            if owned_return_free is None and (
                owned_return_count is not None or nullable_return
            ):
                raise BytecodeFormatError("owned-return metadata requires a free symbol")
            try:
                callbacks = tuple(
                    FFICallbackSpec(index, callback_params, callback_return)
                    for index, callback_params, callback_return in raw_callbacks
                )
            except (TypeError, ValueError) as exc:
                raise BytecodeFormatError("invalid native callback metadata") from exc
            if any(
                item.index < 0 or item.index >= len(params)
                or params[item.index] != "&callback"
                or not all(isinstance(value, str) for value in item.param_types)
                for item in callbacks
            ):
                raise BytecodeFormatError("invalid native callback metadata")
            return NativeCallReference(
                library, symbol, params, result, structs, handles, destroy_params,
                owned_return_free, owned_return_count, nullable_return, callbacks,
            )
        if tag == _EXTENSION_RULE_REFERENCE:
            return self.extension_rule_reference()
        if tag == _VECTOR_EXTENSION_REFERENCE:
            return self.vector_extension_reference()
        if tag == _OBJECT_CONSTRUCTOR_REFERENCE:
            return self.object_constructor_reference()
        if tag == _INDEX_OPERATION_SPEC:
            return self.index_operation_spec()
        if tag == _REFLECTION_FIELDS_REFERENCE:
            owner = self.string()
            fields = tuple(ReflectionFieldReference(self.string(), self.string(), self.string())
                           for _ in range(self.u32()))
            reference = ReflectionFieldsReference(owner, fields)
            _validate_reflection_fields(reference)
            return reference
        if tag == _REFLECTION_FOREACH_REFERENCE:
            reference = ReflectionForeachReference(self.value(), self.value(), self.bool(), self.u32())
            _validate_reflection_foreach(reference)
            return reference
        raise BytecodeFormatError(f"unknown bytecode value tag {tag}")

    def index_operation_spec(self) -> IndexOperationSpec:
        """Decode one named indexed-read or indexed-write payload."""
        selectors = tuple(
            IndexSelectorSpec(
                is_slice=self.bool(),
                has_start=self.bool(),
                has_stop=self.bool(),
                has_step=self.bool(),
            )
            for _ in range(self.u32())
        )
        return IndexOperationSpec(
            selectors=selectors,
            spread=self.bool(),
            grouped_update=self.bool(),
        )

    def bool(self) -> bool:
        """Decode a Boolean byte and validate its representation."""
        value = self.u8()
        if value not in {0, 1}:
            raise BytecodeFormatError(f"invalid boolean marker {value}")
        return bool(value)

    def optional_int(self) -> int | None:
        """Decode an optional integer."""
        marker = self.u8()
        if marker == 0:
            return None
        if marker == 1:
            return self.i64()
        raise BytecodeFormatError(f"invalid optional integer marker {marker}")

    def resolved_element_reference(self) -> ResolvedElementReference:
        """Decode resolved element reference in the portable bytecode stream."""
        name = self.string()
        vectorised = self.bool()
        vectorised_depths = self.value()
        vectorised_target_ranks = self.value()
        return_collection_ranks = self.value()
        return_tags = tuple(
            tuple(
                DataTag(self.string(), self.i64(), self.bool())
                for _ in range(self.u32())
            )
            for _ in range(self.u32())
        )
        return_tag_specs = self.value()
        type_args = self.value()
        static_values = self.value()
        arity_override = self.optional_int()
        consumed_override = self.optional_int()
        kind_bits = self.u8()
        if kind_bits & ~int(
            DispatchKind.TRAIT_WITNESS | DispatchKind.MULTIMETHOD | DispatchKind.UNION
        ):
            raise BytecodeFormatError("unknown resolved dispatch mode")
        dispatch_kind = DispatchKind(kind_bits)
        union_dispatch_plan = tuple(
            self.union_dispatch_branch() for _ in range(self.u32())
        )
        extension = self.value()
        structural_signature = self.value()
        implementation_id = self.optional_string()
        _validate_implementation_identity(implementation_id, allow_none=bool(dispatch_kind & DispatchKind.TRAIT_WITNESS))
        structural_witnesses = self.value()
        callable_witnesses = self.value()
        if (not isinstance(callable_witnesses, tuple) or any(
            not isinstance(item, tuple) or len(item) != 6
            or any(type(number) is not int or number < 0 for number in item[:3])
            or not isinstance(item[3], str) or not isinstance(item[5], str)
            or not isinstance(item[4], tuple) or len(item[4]) != 2
            or any(not isinstance(part, tuple)
                   or any(not isinstance(t, str) for t in part)
                   for part in item[4])
            for item in callable_witnesses
        )):
            raise BytecodeFormatError("invalid checked callback witness")
        if (not isinstance(structural_witnesses, tuple) or any(
            not isinstance(item, tuple) or len(item) != 5
            or not isinstance(item[0], int) or not isinstance(item[1], int)
            or not isinstance(item[2], str) or not isinstance(item[4], str)
            or not isinstance(item[3], tuple) or len(item[3]) != 2
            or any(not isinstance(part, tuple) or not all(isinstance(t, str) for t in part)
                   for part in item[3])
            for item in structural_witnesses
        )):
            raise BytecodeFormatError("invalid checked trait witness")
        if bool(dispatch_kind & DispatchKind.TRAIT_WITNESS) != (structural_signature is not None):
            raise BytecodeFormatError("inconsistent structural dispatch mode")
        if bool(dispatch_kind & DispatchKind.UNION) != bool(union_dispatch_plan):
            raise BytecodeFormatError("inconsistent union dispatch mode")
        if structural_signature is not None and (
            not isinstance(structural_signature, tuple)
            or len(structural_signature) != 2
            or any(
                not isinstance(part, tuple)
                or not all(isinstance(item, str) for item in part)
                for part in structural_signature
            )
        ):
            raise BytecodeFormatError("invalid structural dispatch contract")
        if not isinstance(vectorised_depths, tuple) or not all(
            isinstance(depth, int) for depth in vectorised_depths
        ):
            raise BytecodeFormatError("invalid resolved element vectorised depths")
        if not isinstance(vectorised_target_ranks, tuple) or not all(
            rank is None or isinstance(rank, int) for rank in vectorised_target_ranks
        ):
            raise BytecodeFormatError(
                "invalid resolved element vectorised target ranks"
            )
        if not isinstance(return_collection_ranks, tuple) or not all(
            rank is None or isinstance(rank, int) for rank in return_collection_ranks
        ):
            raise BytecodeFormatError(
                "invalid resolved element return collection ranks"
            )
        if not isinstance(return_tag_specs, tuple):
            raise BytecodeFormatError("invalid resolved element return tag contracts")
        if not isinstance(type_args, tuple) or not all(
            isinstance(type_arg, str) for type_arg in type_args
        ):
            raise BytecodeFormatError("invalid resolved element type arguments")
        if not isinstance(static_values, tuple):
            raise BytecodeFormatError("invalid resolved element static values")
        if extension is not None and not isinstance(
            extension,
            VectorExtensionReference,
        ):
            raise BytecodeFormatError("invalid resolved element extension")
        return ResolvedElementReference(
            name=name,
            vectorised=vectorised,
            vectorised_depths=vectorised_depths,
            vectorised_target_ranks=vectorised_target_ranks,
            return_collection_ranks=return_collection_ranks,
            return_tags=return_tags,
            return_tag_specs=return_tag_specs,
            type_args=type_args,
            static_values=static_values,
            arity_override=arity_override,
            consumed_override=consumed_override,
            dispatch_kind=dispatch_kind,
            union_dispatch_plan=union_dispatch_plan,
            extension=extension,
            structural_signature=structural_signature,
            implementation_id=implementation_id,
            structural_witnesses=structural_witnesses,
            callable_witnesses=callable_witnesses,
        )

    def extension_rule_reference(self) -> ExtensionRuleReference:
        """Decode extension rule reference in the portable bytecode stream."""
        presence = tuple(self.bool() for _ in range(self.u32()))
        function = self.value()
        if not isinstance(function, (FunctionCode, FunctionSetCode)):
            raise BytecodeFormatError("invalid extend pattern function")
        return ExtensionRuleReference(presence, function)

    def vector_extension_reference(self) -> VectorExtensionReference:
        """Decode vector extension reference in the portable bytecode stream."""
        default = self.value()
        rules = tuple(self.extension_rule_reference() for _ in range(self.u32()))
        selector = self.value()
        for name, function in (("default", default), ("selector", selector)):
            if function is not None and not isinstance(
                function,
                (FunctionCode, FunctionSetCode),
            ):
                raise BytecodeFormatError(f"invalid extend {name} function")
        configured = sum((default is not None, bool(rules), selector is not None))
        if configured != 1:
            raise BytecodeFormatError("invalid vector extension configuration")
        return VectorExtensionReference(default, rules, selector)

    def object_constructor_reference(self) -> ObjectConstructorReference:
        """Decode object constructor reference in the portable bytecode stream."""
        type_name = self.string()
        fields = self.value()
        required = self.value()
        defaults = self.value()
        runtime_metadata = self.value()
        initializer = self.value()
        if not isinstance(fields, tuple) or not all(
            isinstance(field, str) for field in fields
        ):
            raise BytecodeFormatError("invalid object constructor fields")
        if not isinstance(required, tuple) or not all(
            isinstance(field, str) for field in required
        ):
            raise BytecodeFormatError("invalid object constructor required fields")
        if not isinstance(defaults, tuple) or not all(
            isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
            for item in defaults
        ):
            raise BytecodeFormatError("invalid object constructor defaults")
        if initializer is not None and not isinstance(
            initializer,
            (FunctionCode, FunctionSetCode),
        ):
            raise BytecodeFormatError("invalid object constructor initializer")
        return ObjectConstructorReference(
            type_name,
            fields,
            required,
            defaults,
            runtime_metadata,
            initializer,
        )

    def function_set(self) -> FunctionSetCode:
        """Decode function set in the portable bytecode stream."""
        overloads = tuple(self.function() for _ in range(self.u32()))
        dispatch_plan = tuple(self.union_dispatch_branch() for _ in range(self.u32()))
        return FunctionSetCode(overloads, dispatch_plan)

    def union_dispatch_branch(self) -> UnionDispatchBranch:
        """Decode union dispatch branch in the portable bytecode stream."""
        params = tuple(self.runtime_type_pattern() for _ in range(self.u32()))
        implementation_id = self.string()
        if not implementation_id:
            raise BytecodeFormatError("empty union implementation identity")
        return UnionDispatchBranch(params, implementation_id=implementation_id)

    def runtime_type_pattern(self) -> RuntimeTypePattern:
        """Decode runtime type pattern in the portable bytecode stream."""
        kind = self.string()
        name = self.optional_string()
        children = tuple(self.runtime_type_pattern() for _ in range(self.u32()))
        accepted_names = tuple(self.string() for _ in range(self.u32()))
        variances = []
        for _ in range(self.u32()):
            marker = self.u8()
            try:
                variances.append(
                    {
                        0: Variance.INVARIANT,
                        1: Variance.COVARIANT,
                        2: Variance.CONTRAVARIANT,
                    }[marker]
                )
            except KeyError as exc:
                raise BytecodeFormatError(
                    f"invalid runtime type variance marker {marker}"
                ) from exc
        tags = tuple(
            DataTag(self.string(), self.i64(), self.bool()) for _ in range(self.u32())
        )
        rank = self.optional_int()
        collection_kind = self.optional_string()
        return RuntimeTypePattern(
            kind,
            name,
            children,
            accepted_names,
            tuple(variances),
            tags,
            rank,
            collection_kind,
        )

    def function(self) -> FunctionCode:
        """Decode function in the portable bytecode stream."""
        name = self.optional_string()
        cycle_params = self.u8()
        if cycle_params not in {0, 1}:
            raise BytecodeFormatError(f"invalid function cycle flag {cycle_params}")
        cycle_param_offset = self.u32()
        accepts_stack_inputs = self.u8()
        if accepts_stack_inputs not in {0, 1}:
            raise BytecodeFormatError(
                f"invalid function stack-input flag {accepts_stack_inputs}"
            )
        recursive = self.u8()
        if recursive not in {0, 1}:
            raise BytecodeFormatError(f"invalid function recursive flag {recursive}")
        params = tuple(self.string() for _ in range(self.u32()))
        element_tags = tuple(self.string() for _ in range(self.u32()))
        multi = self.u8()
        if multi not in {0, 1}:
            raise BytecodeFormatError(f"invalid function multi flag {multi}")
        dispatch_types = tuple(self.optional_string() for _ in range(self.u32()))
        signature_types = tuple(self.string() for _ in range(self.u32()))
        result_signature_types = tuple(self.string() for _ in range(self.u32()))
        generic_type_parameters = tuple(self.string() for _ in range(self.u32()))
        structural_parameters = self.value()
        if not isinstance(structural_parameters, tuple) or any(
            not isinstance(item, tuple) or len(item) != 3
            or not isinstance(item[0], int) or not 0 <= item[0] < len(params)
            or not isinstance(item[1], str)
            or not isinstance(item[2], tuple) or len(item[2]) != 2
            or any(not isinstance(part, tuple) or not all(isinstance(t, str) for t in part)
                   for part in item[2])
            for item in structural_parameters
        ):
            raise BytecodeFormatError("invalid structural parameter contracts")
        implementation_id = self.optional_string()
        if len(set(generic_type_parameters)) != len(generic_type_parameters):
            raise BytecodeFormatError("duplicate method generic parameter")
        return_count = self.optional_int()
        if return_count is not None and return_count < 0:
            raise BytecodeFormatError("invalid function return count")
        occurrence_effects = self.value()
        if not isinstance(occurrence_effects, tuple) or not all(
            item is None or isinstance(item, int) for item in occurrence_effects
        ):
            raise BytecodeFormatError("invalid function occurrence effects")
        _validate_occurrence_effects(params, return_count, occurrence_effects)
        return_tags = tuple(
            tuple(
                DataTag(self.string(), self.i64(), self.bool())
                for _ in range(self.u32())
            )
            for _ in range(self.u32())
        )
        return_tag_specs = self.value()
        if not isinstance(return_tag_specs, tuple):
            raise BytecodeFormatError("invalid function return tag contracts")
        return_collection_ranks = self.value()
        if not isinstance(return_collection_ranks, tuple) or not all(
            rank is None or isinstance(rank, int) for rank in return_collection_ranks
        ):
            raise BytecodeFormatError("invalid function return collection ranks")
        param_collection_ranks = self.value()
        if not isinstance(param_collection_ranks, tuple) or not all(
            rank is None or isinstance(rank, int) for rank in param_collection_ranks
        ):
            raise BytecodeFormatError("invalid function parameter collection ranks")
        requires_specialization = self.bool()
        instructions = []
        for _ in range(self.u32()):
            op_byte = self.u8()
            try:
                op = _BYTE_TO_OP[op_byte]
            except KeyError as exc:
                raise BytecodeFormatError(f"unknown bytecode op {op_byte}") from exc
            instructions.append(Instruction(op, self.value()))
        return FunctionCode(
            instructions=tuple(instructions),
            params=params,
            name=name,
            cycle_params=bool(cycle_params),
            cycle_param_offset=cycle_param_offset,
            accepts_stack_inputs=bool(accepts_stack_inputs),
            element_tags=element_tags,
            recursive=bool(recursive),
            multi=bool(multi),
            dispatch_types=dispatch_types,
            signature_types=signature_types,
            result_signature_types=result_signature_types,
            generic_type_parameters=generic_type_parameters,
            structural_parameters=structural_parameters,
            implementation_id=implementation_id,
            return_count=return_count,
            occurrence_effects=occurrence_effects,
            return_tags=return_tags,
            return_tag_specs=return_tag_specs,
            return_collection_ranks=return_collection_ranks,
            param_collection_ranks=param_collection_ranks,
            requires_specialization=requires_specialization,
        )
