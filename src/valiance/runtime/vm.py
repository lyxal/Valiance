"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from valiance.analysis.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    runtime_elements,
)
from valiance.runtime.bytecode import FunctionCode, FunctionSetCode, OpCode, Program


class RuntimeError(Exception):
    """Raised when bytecode execution fails."""


@dataclass(frozen=True, slots=True)
class FunctionValue:
    """A function closure."""

    code: FunctionCode
    globals: dict[str, Any]


@dataclass(frozen=True, slots=True)
class OverloadedFunctionValue:
    """A closure with one compiled body per statically analysed overload."""

    overloads: tuple[FunctionValue, ...]


@dataclass(frozen=True, slots=True)
class BuiltinValue:
    """A built-in element implementation."""

    element: BuiltinElement
    context: RuntimeContext


@dataclass(slots=True)
class _Frame:
    stack: list[Any]
    locals: dict[str, Any]
    globals: dict[str, Any]
    cycle_values: tuple[Any, ...] = ()
    cycle_index: int = 0

    def source_args(self, arity: int) -> tuple[tuple[Any, ...], int, int]:
        available = min(len(self.stack), arity)
        missing = arity - available
        stack_args = tuple(self.stack[-available:]) if available else ()
        if missing == 0:
            return stack_args, available, self.cycle_index
        if not self.cycle_values:
            raise _StackUnderflow
        cycle_args = tuple(
            self.cycle_values[(self.cycle_index + index) % len(self.cycle_values)]
            for index in range(missing)
        )
        next_cycle_index = (self.cycle_index + missing) % len(self.cycle_values)
        return cycle_args + stack_args, available, next_cycle_index


class _StackUnderflow(Exception):
    """Internal signal for trying another runtime overload shape."""


class VirtualMachine:
    """A small stack-based bytecode interpreter."""

    def __init__(self, *, output: Callable[[str], None] | None = None) -> None:
        self.output = print if output is None else output
        self.globals = {
            name: BuiltinValue(element, RuntimeContext(self.output, self.call_value))
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
        cycle_values = tuple(args) if function.code.cycle_params else ()
        initial_stack = list(args) if function.code.params and not cycle_values else []
        return self.execute(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
        )

    def call_value(self, value: Any, args: list[Any]) -> list[Any]:
        if isinstance(value, FunctionValue):
            return self.call(value, args)
        if isinstance(value, OverloadedFunctionValue) and len(value.overloads) == 1:
            return self.call(value.overloads[0], args)
        raise RuntimeError(f"cannot call value {value!r}")

    def execute(
        self,
        code: FunctionCode,
        locals_: dict[str, Any],
        globals_: dict[str, Any],
        cycle_values: tuple[Any, ...] = (),
        initial_stack: list[Any] | None = None,
    ) -> list[Any]:
        frame = _Frame(list(initial_stack or ()), locals_, globals_, cycle_values)
        ip = 0
        instructions = code.instructions
        while ip < len(instructions):
            instruction = instructions[ip]
            match instruction.op:
                case OpCode.PUSH_CONST:
                    frame.stack.append(_constant(instruction.arg))
                case OpCode.LOAD_VAR:
                    frame.stack.append(
                        _load_name(instruction.arg, frame.locals, frame.globals)
                    )
                case OpCode.STORE_VAR:
                    value = _pop(
                        frame.stack,
                        "store variable",
                    )
                    frame.locals[instruction.arg] = _store_value(
                        frame.locals.get(instruction.arg),
                        value,
                    )
                case OpCode.LOAD_ELEMENT:
                    frame.stack.append(
                        _load_name(instruction.arg, frame.locals, frame.globals)
                    )
                case OpCode.MAKE_FUNCTION:
                    frame.stack.append(
                        _make_function_value(
                            instruction.arg,
                            frame.globals | frame.locals,
                        )
                    )
                case OpCode.CALL:
                    self._call_stack_top(frame)
                case OpCode.CALL_RESOLVED_ELEMENT:
                    self._call_resolved_element(frame, instruction.arg)
                case OpCode.BUILD_LIST:
                    frame.stack.append(_pop_many(frame.stack, instruction.arg))
                case OpCode.BUILD_TUPLE:
                    frame.stack.append(tuple(_pop_many(frame.stack, instruction.arg)))
                case OpCode.BUILD_RECORD:
                    values = _pop_many(frame.stack, len(instruction.arg))
                    frame.stack.append(dict(zip(instruction.arg, values, strict=True)))
                case OpCode.BUILD_DICT:
                    values = _pop_many(frame.stack, instruction.arg * 2)
                    frame.stack.append(
                        dict(zip(values[::2], values[1::2], strict=True))
                    )
                case OpCode.GET_FIELD:
                    receiver = _pop(frame.stack, "field access")
                    frame.stack.append(_get_field(receiver, instruction.arg))
                case OpCode.JUMP:
                    ip = instruction.arg
                    continue
                case OpCode.JUMP_IF_FALSE:
                    if not _truthy(_pop(frame.stack, "conditional jump")):
                        ip = instruction.arg
                        continue
                case OpCode.POP:
                    _pop(frame.stack, "pop")
                case OpCode.RETURN:
                    return frame.stack
            ip += 1
        return frame.stack

    def _call_stack_top(self, frame: _Frame) -> None:
        callee = _pop(frame.stack, "call")
        if isinstance(callee, BuiltinValue):
            _call_builtin(callee, frame)
            return
        if isinstance(callee, FunctionValue):
            self._call_function(callee, frame)
            return
        if isinstance(callee, OverloadedFunctionValue):
            if len(callee.overloads) == 1:
                self._call_function(callee.overloads[0], frame)
                return
            raise RuntimeError("cannot call overloaded function without resolved slot")
        raise RuntimeError(f"cannot call value {callee!r}")

    def _call_resolved_element(
        self,
        frame: _Frame,
        reference: object,
    ) -> None:
        if (
            not isinstance(reference, tuple)
            or len(reference) != 2
            or not isinstance(reference[0], str)
            or not isinstance(reference[1], int)
        ):
            raise RuntimeError(f"invalid resolved element reference {reference!r}")
        name, overload_index = reference
        value = _load_name(name, frame.locals, frame.globals)
        if isinstance(value, BuiltinValue):
            try:
                overload = value.element.definitions[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved element '{name}' has no overload {overload_index}"
                ) from exc
            _call_resolved_builtin(value, overload, frame)
            return
        if isinstance(value, FunctionValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                )
            self._call_function(value, frame)
            return
        if isinstance(value, OverloadedFunctionValue):
            try:
                overload = value.overloads[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                ) from exc
            self._call_function(overload, frame)
            return
        raise RuntimeError(f"resolved element '{name}' is not callable")

    def _call_function(self, callee: FunctionValue, frame: _Frame) -> None:
        arity = len(callee.code.params)
        try:
            args, stack_count, next_cycle_index = frame.source_args(arity)
        except _StackUnderflow as exc:
            raise RuntimeError(
                _format_call_error(
                    f"function '{_function_name(callee.code)}'",
                    frame.stack,
                    [f"{arity} argument(s)"],
                )
            ) from exc
        if stack_count:
            del frame.stack[-stack_count:]
        frame.cycle_index = next_cycle_index
        frame.stack.extend(self.call(callee, list(args)))


def run(program: Program, *, output: Callable[[str], None] | None = None) -> list[Any]:
    """Execute a bytecode program with a fresh VM."""
    return VirtualMachine(output=output).run(program)


def _make_function_value(
    code: object,
    globals_: dict[str, Any],
) -> FunctionValue | OverloadedFunctionValue:
    if isinstance(code, FunctionCode):
        return FunctionValue(code, globals_)
    if isinstance(code, FunctionSetCode):
        return OverloadedFunctionValue(
            tuple(FunctionValue(overload, globals_) for overload in code.overloads)
        )
    raise RuntimeError(f"invalid function bytecode value {code!r}")


def _store_value(existing: Any, value: Any) -> Any:
    if _is_function_value(existing) and _is_function_value(value):
        return OverloadedFunctionValue(
            _function_overloads(existing) + _function_overloads(value)
        )
    return value


def _is_function_value(value: Any) -> bool:
    return isinstance(value, (FunctionValue, OverloadedFunctionValue))


def _function_overloads(value: FunctionValue | OverloadedFunctionValue) -> tuple[
    FunctionValue,
    ...
]:
    if isinstance(value, FunctionValue):
        return (value,)
    return value.overloads


def _call_builtin(callee: BuiltinValue, frame: _Frame) -> None:
    candidates = sorted(
        callee.element.definitions,
        key=lambda overload: len(overload.signature.params),
        reverse=True,
    )
    for overload in candidates:
        arity = len(overload.signature.params)
        try:
            args, stack_count, next_cycle_index = frame.source_args(arity)
        except _StackUnderflow:
            continue
        if not overload.runtime_accepts(args):
            vectorized = _call_vectorized_builtin(overload, args, callee.context)
            if vectorized is None:
                continue
            if stack_count:
                del frame.stack[-stack_count:]
            frame.cycle_index = next_cycle_index
            frame.stack.extend(vectorized)
            return
        if stack_count:
            del frame.stack[-stack_count:]
        frame.cycle_index = next_cycle_index
        implementation = overload.implementation
        if implementation is None:
            continue
        frame.stack.extend(implementation(args, callee.context))
        return
    raise RuntimeError(
        _format_call_error(
            f"element '{callee.element.name}'",
            frame.stack,
            _show_overload_inputs(callee.element.definitions),
        )
    )


def _call_resolved_builtin(
    callee: BuiltinValue,
    overload: BuiltinOverload,
    frame: _Frame,
) -> None:
    arity = len(overload.signature.params)
    try:
        args, stack_count, next_cycle_index = frame.source_args(arity)
    except _StackUnderflow as exc:
        raise RuntimeError(
            _format_call_error(
                f"element '{callee.element.name}'",
                frame.stack,
                _show_overload_inputs((overload,)),
            )
        ) from exc
    if not overload.runtime_accepts(args):
        vectorized = _call_vectorized_builtin(overload, args, callee.context)
        if vectorized is None:
            raise RuntimeError(
                _format_call_error(
                    f"element '{callee.element.name}'",
                    frame.stack,
                    _show_overload_inputs((overload,)),
                )
            )
        if stack_count:
            del frame.stack[-stack_count:]
        frame.cycle_index = next_cycle_index
        frame.stack.extend(vectorized)
        return
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    implementation = overload.implementation
    if implementation is None:
        raise RuntimeError(f"resolved element '{callee.element.name}' is not callable")
    frame.stack.extend(implementation(args, callee.context))


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


def _format_call_error(
    target: str,
    stack: list[Any],
    expected_inputs: list[str],
) -> str:
    lines = [f"cannot call {target} with current stack"]
    lines.append(f"stack: {_format_stack(stack)}")
    lines.append(f"stack types: {_format_stack_types(stack)}")
    if expected_inputs:
        lines.append("attempted input shapes:")
        lines.extend(f"  - {shape}" for shape in expected_inputs)
    return "\n".join(lines)


def _show_overload_inputs(overloads: tuple[BuiltinOverload, ...]) -> list[str]:
    return [
        "(" + ", ".join(str(param) for param in overload.signature.params) + ")"
        for overload in overloads
    ]


def _format_stack(stack: list[Any]) -> str:
    if not stack:
        return "[]"
    return "[" + ", ".join(_format_value(value) for value in stack) + "]"


def _format_stack_types(stack: list[Any]) -> str:
    if not stack:
        return "[]"
    return "[" + ", ".join(_runtime_type_name(value) for value in stack) + "]"


def _format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if isinstance(value, tuple):
        return "(" + ", ".join(_format_value(item) for item in value) + ")"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_format_value(key)}: {_format_value(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    return repr(value)


def _runtime_type_name(value: Any) -> str:
    if isinstance(value, Decimal):
        return "Number"
    if isinstance(value, str):
        return "String"
    if isinstance(value, list):
        item_types = sorted({_runtime_type_name(item) for item in value})
        base = " | ".join(item_types) if item_types else "Unknown"
        return f"{base}+"
    if isinstance(value, tuple):
        return "{" + ", ".join(_runtime_type_name(item) for item in value) + "}"
    if isinstance(value, dict):
        return "record"
    return type(value).__name__
