"""Bytecode compiler and interpreter for executable Valiance programs."""

from __future__ import annotations

from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.runtime.compiler import CompileError, compile_program
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads
from valiance.runtime.vm import RuntimeError, VirtualMachine, run
from valiance.runtime_values import LazyList

__all__ = [
    "BytecodeFormatError",
    "CompileError",
    "FunctionCode",
    "Instruction",
    "LazyList",
    "OpCode",
    "Program",
    "RuntimeError",
    "VirtualMachine",
    "compile_program",
    "dumps",
    "loads",
    "run",
]
