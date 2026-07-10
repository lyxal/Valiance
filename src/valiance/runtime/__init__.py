"""Bytecode compiler and interpreter for executable Valiance programs."""

from __future__ import annotations

from valiance.runtime.bytecode import FunctionCode, Instruction, OpCode, Program
from valiance.runtime.compiler import CompileError, compile_program
from valiance.runtime.optimizer import (
    DEFAULT_OPTIMIZATION_PIPELINE,
    ControlFlowOptimizationPass,
    FunctionOptimizationPass,
    OptimizationError,
    OptimizationPass,
    OptimizationPipeline,
    optimize_program,
)
from valiance.runtime.serialization import BytecodeFormatError, dumps, loads
from valiance.runtime.vm import AssertionFailure, RuntimeError, VirtualMachine, run
from valiance.runtime_values import LazyList

__all__ = [
    "AssertionFailure",
    "BytecodeFormatError",
    "CompileError",
    "ControlFlowOptimizationPass",
    "DEFAULT_OPTIMIZATION_PIPELINE",
    "FunctionCode",
    "FunctionOptimizationPass",
    "Instruction",
    "LazyList",
    "OpCode",
    "OptimizationError",
    "OptimizationPass",
    "OptimizationPipeline",
    "Program",
    "RuntimeError",
    "VirtualMachine",
    "compile_program",
    "dumps",
    "loads",
    "optimize_program",
    "run",
]
