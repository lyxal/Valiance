"""Portable binary bytecode serialization for Valiance programs."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    Instruction,
    OpCode,
    Program,
)

MAGIC = b"VLNCBC\x02"

_OP_TO_BYTE = {
    OpCode.PUSH_CONST: 0x01,
    OpCode.LOAD_VAR: 0x02,
    OpCode.STORE_VAR: 0x03,
    OpCode.LOAD_ELEMENT: 0x04,
    OpCode.MAKE_FUNCTION: 0x05,
    OpCode.CALL: 0x06,
    OpCode.BUILD_LIST: 0x07,
    OpCode.BUILD_TUPLE: 0x08,
    OpCode.BUILD_RECORD: 0x09,
    OpCode.BUILD_DICT: 0x0A,
    OpCode.MAKE_OBJECT_CONSTRUCTOR: 0x0B,
    OpCode.MAKE_ENUM_MEMBER: 0x0C,
    OpCode.GET_FIELD: 0x0D,
    OpCode.JUMP: 0x0E,
    OpCode.JUMP_IF_FALSE: 0x0F,
    OpCode.POP: 0x10,
    OpCode.RETURN: 0x11,
    OpCode.CALL_RESOLVED_ELEMENT: 0x12,
}
_BYTE_TO_OP = {value: key for key, value in _OP_TO_BYTE.items()}

_NONE = 0x00
_INT = 0x01
_DECIMAL = 0x02
_STRING = 0x03
_TUPLE = 0x04
_FUNCTION = 0x05
_FUNCTION_SET = 0x06


class BytecodeFormatError(Exception):
    """Raised when bytecode bytes cannot be decoded."""


def dumps(program: Program) -> bytes:
    """Serialize a bytecode program to portable binary bytes."""
    writer = _Writer()
    writer.bytes(MAGIC)
    writer.function(program.main)
    return writer.finish()


def loads(data: bytes) -> Program:
    """Deserialize a bytecode program from portable binary bytes."""
    reader = _Reader(data)
    try:
        reader.expect(MAGIC)
        program = Program(reader.function())
        reader.expect_eof()
        return program
    except (UnicodeDecodeError, struct.error) as exc:
        raise BytecodeFormatError("invalid Valiance bytecode payload") from exc


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def finish(self) -> bytes:
        return bytes(self.buffer)

    def bytes(self, value: bytes) -> None:
        self.buffer.extend(value)

    def u8(self, value: int) -> None:
        if not 0 <= value <= 0xFF:
            raise BytecodeFormatError(f"u8 out of range: {value}")
        self.buffer.append(value)

    def u32(self, value: int) -> None:
        if not 0 <= value <= 0xFFFFFFFF:
            raise BytecodeFormatError(f"u32 out of range: {value}")
        self.buffer.extend(struct.pack(">I", value))

    def i64(self, value: int) -> None:
        self.buffer.extend(struct.pack(">q", value))

    def string(self, value: str) -> None:
        encoded = value.encode("utf-8")
        self.u32(len(encoded))
        self.bytes(encoded)

    def optional_string(self, value: str | None) -> None:
        self.u8(0 if value is None else 1)
        if value is not None:
            self.string(value)

    def value(self, value: Any) -> None:
        if value is None:
            self.u8(_NONE)
        elif isinstance(value, int):
            self.u8(_INT)
            self.i64(value)
        elif isinstance(value, Decimal):
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
            self.u32(len(value.overloads))
            for overload in value.overloads:
                self.function(overload)
        else:
            raise BytecodeFormatError(f"cannot serialize bytecode value {value!r}")

    def function(self, function: FunctionCode) -> None:
        self.optional_string(function.name)
        self.u8(1 if function.cycle_params else 0)
        self.u32(len(function.params))
        for param in function.params:
            self.string(param)
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
        if self.data[self.offset : self.offset + len(value)] != value:
            raise BytecodeFormatError("not a Valiance bytecode file")
        self.offset += len(value)

    def expect_eof(self) -> None:
        if self.offset != len(self.data):
            raise BytecodeFormatError("trailing bytes after Valiance bytecode")

    def take(self, count: int) -> bytes:
        end = self.offset + count
        if end > len(self.data):
            raise BytecodeFormatError("truncated Valiance bytecode")
        value = self.data[self.offset : end]
        self.offset = end
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def string(self) -> str:
        return self.take(self.u32()).decode("utf-8")

    def optional_string(self) -> str | None:
        marker = self.u8()
        if marker == 0:
            return None
        if marker == 1:
            return self.string()
        raise BytecodeFormatError(f"invalid optional string marker {marker}")

    def value(self) -> Any:
        tag = self.u8()
        if tag == _NONE:
            return None
        if tag == _INT:
            return self.i64()
        if tag == _DECIMAL:
            return Decimal(self.string())
        if tag == _STRING:
            return self.string()
        if tag == _TUPLE:
            return tuple(self.value() for _ in range(self.u32()))
        if tag == _FUNCTION:
            return self.function()
        if tag == _FUNCTION_SET:
            return FunctionSetCode(tuple(self.function() for _ in range(self.u32())))
        raise BytecodeFormatError(f"unknown bytecode value tag {tag}")

    def function(self) -> FunctionCode:
        name = self.optional_string()
        cycle_params = self.u8()
        if cycle_params not in {0, 1}:
            raise BytecodeFormatError(f"invalid function cycle flag {cycle_params}")
        params = tuple(self.string() for _ in range(self.u32()))
        instructions = []
        for _ in range(self.u32()):
            op_byte = self.u8()
            try:
                op = _BYTE_TO_OP[op_byte]
            except KeyError as exc:
                raise BytecodeFormatError(f"unknown bytecode op {op_byte}") from exc
            instructions.append(Instruction(op, self.value()))
        return FunctionCode(tuple(instructions), params, name, bool(cycle_params))
