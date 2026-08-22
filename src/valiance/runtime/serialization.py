"""Portable binary bytecode serialization for Valiance programs."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    IndexOperationSpec,
    IndexSelectorSpec,
    Instruction,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.vtypes import (
    DataTag,
    RuntimeTypePattern,
    UnionDispatchBranch,
    Variance,
)

MAGIC_PREFIX = b"VLNCBC"
BYTECODE_VERSION = 0x22
MAGIC = MAGIC_PREFIX + bytes((BYTECODE_VERSION,))

_OP_TO_BYTE = {
    OpCode.PUSH_CONST: 0x01,
    OpCode.LOAD_VAR: 0x02,
    OpCode.STORE_VAR: 0x03,
    OpCode.LOAD_ELEMENT: 0x04,
    OpCode.MAKE_FUNCTION: 0x05,
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
        if version != BYTECODE_VERSION:
            raise BytecodeFormatError(
                f"unsupported Valiance bytecode version {version}; "
                f"expected {BYTECODE_VERSION}"
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


def _validate_concurrency_bytecode(code: FunctionCode) -> None:
    """Reject malformed concurrency payloads and unbalanced scope bytecode."""
    scope_depth = 0
    for index, instruction in enumerate(code.instructions):
        op = instruction.op
        argument = instruction.arg
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
        elif op is OpCode.SPAWN_CALL:
            if (
                not isinstance(argument, tuple)
                or len(argument) not in {2, 3, 4, 8, 9}
                or not all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in argument[:3]
                )
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
        elif op in {OpCode.WAIT_TASK, OpCode.WAIT_TASKS_VECTORISED}:
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


def _validate_nested_concurrency_values(value: object) -> None:
    """Validate nested function bytecode found inside instruction payloads."""
    if isinstance(value, FunctionCode):
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
        elif isinstance(value, RuntimeNumber):
            self.u8(_DECIMAL)
            self.string(str(value))
        elif isinstance(value, str):
            self.u8(_STRING)
            self.string(value)
        elif isinstance(value, tuple):
            self.u8(_TUPLE)
            self.u32(len(value))
            for item in value:
                self.value(item)
        elif isinstance(value, FunctionCode):
            self.u8(_FUNCTION)
            self.function(value)
        elif isinstance(value, FunctionSetCode):
            self.u8(_FUNCTION_SET)
            self.function_set(value)
        elif isinstance(value, ResolvedElementReference):
            self.u8(_RESOLVED_ELEMENT_REFERENCE)
            self.resolved_element_reference(value)
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
        self.string(reference.name)
        self.i64(reference.overload_index)
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
        self.bool(reference.multidispatch)
        self.value(reference.extension)

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
        self.i64(branch.overload_index)

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
        if tag == _DECIMAL:
            return RuntimeNumber(self.string())
        if tag == _STRING:
            return self.string()
        if tag == _TUPLE:
            return tuple(self.value() for _ in range(self.u32()))
        if tag == _FUNCTION:
            return self.function()
        if tag == _FUNCTION_SET:
            return self.function_set()
        if tag == _RESOLVED_ELEMENT_REFERENCE:
            return self.resolved_element_reference()
        if tag == _EXTENSION_RULE_REFERENCE:
            return self.extension_rule_reference()
        if tag == _VECTOR_EXTENSION_REFERENCE:
            return self.vector_extension_reference()
        if tag == _OBJECT_CONSTRUCTOR_REFERENCE:
            return self.object_constructor_reference()
        if tag == _INDEX_OPERATION_SPEC:
            return self.index_operation_spec()
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
        overload_index = self.i64()
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
        multidispatch = self.bool()
        extension = self.value()
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
            overload_index=overload_index,
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
            multidispatch=multidispatch,
            extension=extension,
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
        overload_index = self.i64()
        if not 0 <= overload_index:
            raise BytecodeFormatError("invalid union dispatch overload index")
        return UnionDispatchBranch(params, overload_index)

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
            return_count=return_count,
            occurrence_effects=occurrence_effects,
            return_tags=return_tags,
            return_tag_specs=return_tag_specs,
            return_collection_ranks=return_collection_ranks,
            param_collection_ranks=param_collection_ranks,
        )
