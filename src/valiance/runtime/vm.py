"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from valiance.analysis.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    runtime_elements,
)
from valiance.runtime.bytecode import FunctionCode, OpCode, Program


class RuntimeError(Exception):
    """Raised when bytecode execution fails."""


@dataclass(frozen=True, slots=True)
class FunctionValue:
    """A function closure."""

    code: FunctionCode
    globals: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BuiltinValue:
    """A built-in element implementation."""

    element: BuiltinElement
    context: RuntimeContext


class VirtualMachine:
    """A small stack-based bytecode interpreter."""

    def __init__(self, *, output: Callable[[str], None] | None = None) -> None:
        self.output = print if output is None else output
        self.globals = {
            name: BuiltinValue(element, RuntimeContext(self.output))
            for name, element in runtime_elements().items()
        }

    def run(self, program: Program) -> list[Any]:
        """Execute a compiled program and return the final stack."""
        return self.call(FunctionValue(program.main, self.globals), [])

    def call(self, function: FunctionValue, args: list[Any]) -> list[Any]:
        if len(args) != len(function.code.params):
            raise RuntimeError(
                f"{_function_name(function.code)} expected "
                f"{len(function.code.params)} arguments, got {len(args)}"
            )
        locals_: dict[str, Any] = dict(zip(function.code.params, args, strict=True))
        return self.execute(function.code, locals_, function.globals)

    def execute(
        self,
        code: FunctionCode,
        locals_: dict[str, Any],
        globals_: dict[str, Any],
    ) -> list[Any]:
        stack: list[Any] = []
        ip = 0
        instructions = code.instructions
        while ip < len(instructions):
            instruction = instructions[ip]
            match instruction.op:
                case OpCode.PUSH_CONST:
                    stack.append(_constant(instruction.arg))
                case OpCode.LOAD_VAR:
                    stack.append(_load_name(instruction.arg, locals_, globals_))
                case OpCode.STORE_VAR:
                    locals_[instruction.arg] = _pop(stack, "store variable")
                case OpCode.LOAD_ELEMENT:
                    stack.append(_load_name(instruction.arg, locals_, globals_))
                case OpCode.MAKE_FUNCTION:
                    stack.append(FunctionValue(instruction.arg, globals_ | locals_))
                case OpCode.CALL:
                    self._call_stack_top(stack)
                case OpCode.BUILD_LIST:
                    stack.append(_pop_many(stack, instruction.arg))
                case OpCode.BUILD_TUPLE:
                    stack.append(tuple(_pop_many(stack, instruction.arg)))
                case OpCode.BUILD_RECORD:
                    values = _pop_many(stack, len(instruction.arg))
                    stack.append(dict(zip(instruction.arg, values, strict=True)))
                case OpCode.BUILD_DICT:
                    values = _pop_many(stack, instruction.arg * 2)
                    stack.append(dict(zip(values[::2], values[1::2], strict=True)))
                case OpCode.GET_FIELD:
                    receiver = _pop(stack, "field access")
                    stack.append(_get_field(receiver, instruction.arg))
                case OpCode.JUMP:
                    ip = instruction.arg
                    continue
                case OpCode.JUMP_IF_FALSE:
                    if not _truthy(_pop(stack, "conditional jump")):
                        ip = instruction.arg
                        continue
                case OpCode.POP:
                    _pop(stack, "pop")
                case OpCode.RETURN:
                    return stack
            ip += 1
        return stack

    def _call_stack_top(self, stack: list[Any]) -> None:
        callee = _pop(stack, "call")
        if isinstance(callee, BuiltinValue):
            _call_builtin(callee, stack)
            return
        if isinstance(callee, FunctionValue):
            arity = len(callee.code.params)
            args = _pop_many(stack, arity)
            stack.extend(self.call(callee, args))
            return
        raise RuntimeError(f"cannot call value {callee!r}")


def run(program: Program, *, output: Callable[[str], None] | None = None) -> list[Any]:
    """Execute a bytecode program with a fresh VM."""
    return VirtualMachine(output=output).run(program)


def _call_builtin(callee: BuiltinValue, stack: list[Any]) -> None:
    candidates = sorted(
        callee.element.definitions,
        key=lambda overload: len(overload.signature.params),
        reverse=True,
    )
    for overload in candidates:
        arity = len(overload.signature.params)
        if len(stack) < arity:
            continue
        args = tuple(stack[-arity:]) if arity else ()
        if not overload.runtime_accepts(args):
            vectorized = _call_vectorized_builtin(overload, args, callee.context)
            if vectorized is None:
                continue
            if arity:
                del stack[-arity:]
            stack.extend(vectorized)
            return
        if arity:
            del stack[-arity:]
        implementation = overload.implementation
        if implementation is None:
            continue
        stack.extend(implementation(args, callee.context))
        return
    raise RuntimeError(f"no runtime overload for element '{callee.element.name}'")


def _call_vectorized_builtin(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...] | None:
    if overload.implementation is None or not any(
        isinstance(arg, list) for arg in args
    ):
        return None
    try:
        return _vectorize(overload, args, context)
    except _CannotVectorize:
        return None


def _vectorize(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    vector_lengths = {len(arg) for arg in args if isinstance(arg, list)}
    if not vector_lengths:
        if not overload.runtime_accepts(args):
            raise _CannotVectorize
        implementation = overload.implementation
        if implementation is None:
            raise _CannotVectorize
        return implementation(args, context)
    if len(vector_lengths) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    result_items = []
    for index in range(next(iter(vector_lengths))):
        item_args = tuple(arg[index] if isinstance(arg, list) else arg for arg in args)
        result_items.append(_vectorize(overload, item_args, context))

    if not result_items:
        return ([],)
    width = len(result_items[0])
    if any(len(item) != width for item in result_items):
        raise RuntimeError("vectorised overload returned inconsistent stack shapes")
    return tuple([item[position] for item in result_items] for position in range(width))


class _CannotVectorize(Exception):
    """Internal signal for trying the next runtime overload."""


def _pop(stack: list[Any], context: str) -> Any:
    if not stack:
        raise RuntimeError(f"stack underflow during {context}")
    return stack.pop()


def _pop_many(stack: list[Any], count: int) -> list[Any]:
    if count < 0:
        raise RuntimeError(f"cannot pop negative count {count}")
    if len(stack) < count:
        raise RuntimeError(f"stack underflow popping {count} values")
    if count == 0:
        return []
    values = stack[-count:]
    del stack[-count:]
    return values


def _load_name(name: str, locals_: dict[str, Any], globals_: dict[str, Any]) -> Any:
    if name in locals_:
        return locals_[name]
    if name in globals_:
        return globals_[name]
    raise RuntimeError(f"undefined name '{name}'")


def _constant(value: Any) -> Any:
    return value


def _truthy(value: Any) -> bool:
    return value != 0 and value is not None


def _get_field(receiver: Any, field: str) -> Any:
    if isinstance(receiver, dict):
        try:
            return receiver[field]
        except KeyError as exc:
            raise RuntimeError(f"record has no field '{field}'") from exc
    try:
        return getattr(receiver, field)
    except AttributeError as exc:
        raise RuntimeError(f"value has no field '{field}'") from exc


def _function_name(code: FunctionCode) -> str:
    return "function" if code.name is None else code.name
