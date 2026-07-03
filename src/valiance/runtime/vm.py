"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

import builtins as _py_builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import zip_longest
from typing import Any

from valiance.analysis.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    runtime_elements,
)
from valiance.runtime.bytecode import FunctionCode, FunctionSetCode, OpCode, Program
from valiance.runtime_values import (
    DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    LazyList,
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    is_eager_sequence,
    is_list_like,
)


class RuntimeError(_py_builtins.RuntimeError):
    """Raised when bytecode execution fails."""

    def __init__(self, message: object) -> None:
        super().__init__(message)
        self.message = str(message)
        self.call_details: list[tuple[str, tuple[Any, ...]]] = []
        self.execution_contexts: list[_ExecutionContext] = []

    def add_call_detail(self, target: str, args: tuple[Any, ...]) -> None:
        detail = target, args
        if detail not in self.call_details:
            self.call_details.append(detail)

    def add_execution_context(
        self,
        function_name: str,
        ip: int,
        instruction: object,
        stack: list[Any],
    ) -> None:
        context = _ExecutionContext(function_name, ip, instruction, tuple(stack))
        if context not in self.execution_contexts:
            self.execution_contexts.append(context)

    def __str__(self) -> str:
        lines = [self.message]
        if self.call_details:
            lines.append("runtime call:")
            for target, args in self.call_details:
                lines.append(f"  - target: {target}")
                lines.append(f"    arguments: {_format_stack(list(args))}")
                lines.append(f"    argument types: {_format_stack_types(list(args))}")
        if self.execution_contexts:
            lines.append("runtime context:")
            for context in self.execution_contexts:
                lines.append(f"  - {_format_execution_context(context)}")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    function_name: str
    ip: int
    instruction: object
    stack: tuple[Any, ...]


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


def _static_reference_values(value: object) -> tuple[Any, ...]:
    if not isinstance(value, tuple):
        raise RuntimeError(f"invalid static reference values {value!r}")
    result: list[Any] = []
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], int)
        ):
            raise RuntimeError(f"invalid static reference value {item!r}")
        result.append(Decimal(item[1]))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ObjectConstructorValue:
    """Runtime constructor for nominal structured values."""

    type_name: str
    fields: tuple[str, ...]
    required: tuple[str, ...]
    defaults: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _PanicHandler:
    handlers: tuple[tuple[str | None, int], ...]
    stack_depth: int


@dataclass(slots=True)
class _Frame:
    stack: list[Any]
    locals: dict[str, Any]
    globals: dict[str, Any]
    cycle_values: tuple[Any, ...] = ()
    cycle_index: int = 0
    panic_handlers: list[_PanicHandler] = field(default_factory=list)

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

    def __init__(
        self,
        *,
        output: Callable[[str], None] | None = None,
        list_preview_limit: int | None = None,
    ) -> None:
        self.output = (lambda value: print(value, end="")) if output is None else output
        self.format_value = lambda value: format_runtime_value(
            value,
            lazy_preview_limit=list_preview_limit,
        )
        self.globals = {
            name: BuiltinValue(
                element,
                RuntimeContext(self.output, self.call_value, self.format_value),
            )
            for name, element in runtime_elements().items()
        }

    def run(self, program: Program) -> list[Any]:
        """Execute a compiled program and return the final stack."""
        try:
            return self.call(FunctionValue(program.main, self.globals), [])
        except PanicSignal as exc:
            raise RuntimeError(f"uncaught panic: {_format_value(exc.value)}") from exc

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
            try:
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
                        try:
                            self._call_stack_top(frame)
                        except PanicSignal as exc:
                            target = self._handle_panic(frame, exc)
                            if target is None:
                                raise
                            ip = target
                            continue
                    case OpCode.CALL_RESOLVED_ELEMENT:
                        try:
                            self._call_resolved_element(frame, instruction.arg)
                        except PanicSignal as exc:
                            target = self._handle_panic(frame, exc)
                            if target is None:
                                raise
                            ip = target
                            continue
                    case OpCode.CHECK_CAST:
                        value = _pop(frame.stack, "checked cast")
                        if not _matches_cast_type(value, instruction.arg):
                            raise RuntimeError(
                                f"checked cast failed: {_format_value(value)} is "
                                f"{_runtime_type_name(value)}"
                            )
                        frame.stack.append(value)
                    case OpCode.BUILD_LIST:
                        frame.stack.append(_pop_many(frame.stack, instruction.arg))
                    case OpCode.BUILD_STRING:
                        frame.stack.append(_build_string(frame.stack, instruction.arg))
                    case OpCode.BUILD_TUPLE:
                        frame.stack.append(
                            tuple(_pop_many(frame.stack, instruction.arg))
                        )
                    case OpCode.BUILD_RECORD:
                        values = _pop_many(frame.stack, len(instruction.arg))
                        frame.stack.append(
                            dict(zip(instruction.arg, values, strict=True))
                        )
                    case OpCode.BUILD_DICT:
                        values = _pop_many(frame.stack, instruction.arg * 2)
                        frame.stack.append(
                            dict(zip(values[::2], values[1::2], strict=True))
                        )
                    case OpCode.MAKE_OBJECT_CONSTRUCTOR:
                        type_name, fields, required, defaults = instruction.arg
                        frame.stack.append(
                            ObjectConstructorValue(
                                type_name,
                                tuple(fields),
                                tuple(required),
                                dict(defaults),
                            )
                        )
                    case OpCode.MAKE_ENUM_MEMBER:
                        enum_name, member_name, value = instruction.arg
                        fields = {"name": member_name}
                        if value is not None:
                            fields["value"] = value
                        frame.stack.append(ObjectValue(enum_name, fields))
                    case OpCode.GET_FIELD:
                        try:
                            args, stack_count, next_cycle_index = frame.source_args(1)
                        except _StackUnderflow as exc:
                            raise RuntimeError(
                                "stack underflow during field access"
                            ) from exc
                        if stack_count:
                            del frame.stack[-stack_count:]
                        frame.cycle_index = next_cycle_index
                        receiver = args[0]
                        frame.stack.append(_get_field(receiver, instruction.arg))
                    case OpCode.SET_FIELD:
                        value = _pop(frame.stack, "field assignment")
                        receiver = _pop(frame.stack, "field assignment")
                        frame.stack.append(_set_field(receiver, instruction.arg, value))
                    case OpCode.GET_INDEX:
                        values = _pop_index_values(frame.stack, instruction.arg)
                        receiver = _index_receiver(frame)
                        result = _get_index(receiver, instruction.arg, values)
                        if instruction.arg[1]:
                            if not isinstance(result, list):
                                raise RuntimeError(
                                    "spread indexing requires a list result"
                                )
                            frame.stack.extend(result)
                        else:
                            frame.stack.append(result)
                    case OpCode.SET_INDEX:
                        values = _pop_index_values(frame.stack, instruction.arg)
                        receiver = _pop(frame.stack, "indexed assignment")
                        value = _pop(frame.stack, "indexed assignment")
                        frame.stack.append(
                            _set_index(receiver, instruction.arg, values, value)
                        )
                    case OpCode.JUMP:
                        ip = instruction.arg
                        continue
                    case OpCode.JUMP_IF_FALSE:
                        if not _truthy(_pop(frame.stack, "conditional jump")):
                            ip = instruction.arg
                            continue
                    case OpCode.JUMP_IF_MATCH:
                        patterns, target = instruction.arg
                        bindings = self._match_patterns(frame, patterns)
                        if bindings is not None:
                            del frame.stack[-len(patterns) :]
                            frame.locals.update(bindings)
                            ip = target
                            continue
                    case OpCode.MATCH_ERROR:
                        raise RuntimeError("non-exhaustive match at runtime")
                    case OpCode.ASSERT_TRUE:
                        if not _truthy(_pop(frame.stack, "assert")):
                            raise RuntimeError("assertion failed")
                    case OpCode.UNFOLD:
                        frame.stack.append(self._unfold(frame, instruction.arg))
                    case OpCode.TRY_BEGIN:
                        frame.panic_handlers.append(
                            _PanicHandler(tuple(instruction.arg), len(frame.stack))
                        )
                    case OpCode.TRY_END:
                        if frame.panic_handlers:
                            frame.panic_handlers.pop()
                    case OpCode.PANIC:
                        value = _pop(frame.stack, "panic")
                        target = self._handle_panic(
                            frame,
                            PanicSignal(value),
                        )
                        if target is None:
                            raise PanicSignal(value)
                        ip = target
                        continue
                    case OpCode.TRY_UNWRAP:
                        if _try_unwrap(frame.stack):
                            return frame.stack
                    case OpCode.POP:
                        _pop(frame.stack, "pop")
                    case OpCode.RETURN:
                        return frame.stack
            except _py_builtins.RuntimeError as exc:
                error = exc if isinstance(exc, RuntimeError) else RuntimeError(exc)
                error.add_execution_context(
                    _function_name(code),
                    ip,
                    instruction,
                    frame.stack,
                )
                raise error from exc
            ip += 1
        return frame.stack

    def _handle_panic(self, frame: _Frame, panic: PanicSignal) -> int | None:
        while frame.panic_handlers:
            handler = frame.panic_handlers.pop()
            for type_name, target in handler.handlers:
                if type_name is None or _panic_matches(panic.value, type_name):
                    del frame.stack[handler.stack_depth :]
                    return target
        return None

    def _call_stack_top(self, frame: _Frame) -> None:
        callee = _pop(frame.stack, "call")
        if isinstance(callee, BuiltinValue):
            _call_builtin(callee, frame)
            return
        if isinstance(callee, FunctionValue):
            self._call_function(callee, frame)
            return
        if isinstance(callee, ObjectConstructorValue):
            _call_object_constructor(callee, frame)
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
            or len(reference) not in {2, 3, 4, 5, 6}
            or not isinstance(reference[0], str)
            or not isinstance(reference[1], int)
        ):
            raise RuntimeError(f"invalid resolved element reference {reference!r}")
        name, overload_index = reference[:2]
        vectorised = bool(reference[2]) if len(reference) >= 3 else False
        vectorised_depths = (
            tuple(int(depth) for depth in reference[3]) if len(reference) >= 4 else ()
        )
        type_args = (
            tuple(str(type_arg) for type_arg in reference[4])
            if len(reference) >= 5
            else ()
        )
        static_values = (
            _static_reference_values(reference[5]) if len(reference) >= 6 else ()
        )
        value = _load_name(name, frame.locals, frame.globals)
        if isinstance(value, BuiltinValue):
            try:
                overload = value.element.definitions[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved element '{name}' has no overload {overload_index}"
                ) from exc
            _call_resolved_builtin(
                value,
                overload,
                frame,
                vectorised,
                vectorised_depths,
            )
            return
        if isinstance(value, FunctionValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                )
            frame.stack.extend(static_values)
            self._call_function(value, frame, vectorised=vectorised)
            return
        if isinstance(value, OverloadedFunctionValue):
            try:
                overload = value.overloads[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                ) from exc
            frame.stack.extend(static_values)
            self._call_function(overload, frame, vectorised=vectorised)
            return
        if isinstance(value, ObjectConstructorValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved constructor '{name}' has no overload {overload_index}"
                )
            _call_object_constructor(value, frame, type_args)
            return
        if isinstance(value, ObjectValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved enum member '{name}' has no overload {overload_index}"
                )
            frame.stack.append(value)
            return
        if overload_index == 0 and not callable(value):
            frame.stack.append(value)
            return
        raise RuntimeError(f"resolved element '{name}' is not callable")

    def _unfold(self, frame: _Frame, config: object) -> LazyList:
        condition_code, body_code, arity = config
        state = _pop_many(frame.stack, arity)
        body = _make_function_value(body_code, frame.globals | frame.locals)
        condition = (
            None
            if condition_code is None
            else _make_function_value(condition_code, frame.globals | frame.locals)
        )

        def generated():
            nonlocal state
            while True:
                if condition is not None:
                    keep_going = self.call_value(condition, list(state))
                    if not _truthy(keep_going[0]):
                        return
                outputs = self.call_value(body, list(state))
                generated_value = outputs[-1]
                if arity == 1 and len(outputs) == 1:
                    state = [generated_value]
                else:
                    state = list(outputs[:arity])
                if generated_value is not None:
                    yield generated_value

        return LazyList(generated())

    def _call_function(
        self,
        callee: FunctionValue,
        frame: _Frame,
        *,
        vectorised: bool = False,
    ) -> None:
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
        if vectorised:
            try:
                result = _vectorize_function(self, callee, args)
            except _py_builtins.RuntimeError as exc:
                raise _with_call_detail(
                    exc,
                    f"function '{_function_name(callee.code)}'",
                    args,
                ) from exc
            frame.stack.extend(result)
        else:
            try:
                result = self.call(callee, list(args))
            except _py_builtins.RuntimeError as exc:
                raise _with_call_detail(
                    exc,
                    f"function '{_function_name(callee.code)}'",
                    args,
                ) from exc
            frame.stack.extend(result)

    def _match_patterns(
        self,
        frame: _Frame,
        patterns: tuple[object, ...],
    ) -> dict[str, Any] | None:
        if len(frame.stack) < len(patterns):
            return None
        bindings: dict[str, Any] = {}
        values = tuple(reversed(frame.stack[-len(patterns) :]))
        for pattern, value in zip(patterns, values, strict=True):
            if not self._match_pattern(value, pattern, bindings):
                return None
        return bindings

    def _match_pattern(
        self,
        value: Any,
        pattern: object,
        bindings: dict[str, Any],
    ) -> bool:
        if not isinstance(pattern, tuple) or not pattern:
            return False
        kind = pattern[0]
        if kind == "literal":
            return value == pattern[1]
        if kind == "guard":
            return self._guard_truthy(pattern[1], value)
        if kind == "wildcard":
            return True
        if kind == "rest":
            name = pattern[1]
            return name is None or _bind_match_name(bindings, name, value)
        if kind == "bind":
            name, inner = pattern[1], pattern[2]
            snapshot = dict(bindings)
            if not self._match_pattern(value, inner, bindings):
                bindings.clear()
                bindings.update(snapshot)
                return False
            return _bind_match_name(bindings, name, value)
        if kind == "or":
            for option in pattern[1]:
                snapshot = dict(bindings)
                if self._match_pattern(value, option, bindings):
                    return True
                bindings.clear()
                bindings.update(snapshot)
            return False
        if kind == "list":
            return self._match_list_pattern(value, pattern[1], bindings)
        if kind == "type":
            return self._match_type_pattern(value, pattern, bindings)
        return False

    def _match_list_pattern(
        self,
        value: Any,
        items: tuple[object, ...],
        bindings: dict[str, Any],
    ) -> bool:
        if not is_eager_sequence(value):
            return False
        return self._match_list_items(tuple(value), items, bindings, 0, 0)

    def _match_list_items(
        self,
        values: tuple[Any, ...],
        patterns: tuple[object, ...],
        bindings: dict[str, Any],
        value_index: int,
        pattern_index: int,
    ) -> bool:
        if pattern_index == len(patterns):
            return value_index == len(values)
        pattern = patterns[pattern_index]
        if _is_rest_pattern(pattern):
            name = pattern[1]
            for end in range(value_index, len(values) + 1):
                snapshot = dict(bindings)
                if name is None or _bind_match_name(
                    bindings,
                    name,
                    list(values[value_index:end]),
                ):
                    if self._match_list_items(
                        values,
                        patterns,
                        bindings,
                        end,
                        pattern_index + 1,
                    ):
                        return True
                bindings.clear()
                bindings.update(snapshot)
            return False
        if value_index >= len(values):
            return False
        snapshot = dict(bindings)
        if self._match_pattern(values[value_index], pattern, bindings):
            if self._match_list_items(
                values,
                patterns,
                bindings,
                value_index + 1,
                pattern_index + 1,
            ):
                return True
        bindings.clear()
        bindings.update(snapshot)
        return False

    def _match_pattern_sequence(
        self,
        values: tuple[Any, ...],
        patterns: tuple[object, ...],
        bindings: dict[str, Any],
    ) -> bool:
        snapshot = dict(bindings)
        for value, pattern in zip(values, patterns, strict=True):
            if not self._match_pattern(value, pattern, bindings):
                bindings.clear()
                bindings.update(snapshot)
                return False
        return True

    def _match_type_pattern(
        self,
        value: Any,
        pattern: tuple[object, ...],
        bindings: dict[str, Any],
    ) -> bool:
        _, type_name, binding_name, fields, guard = pattern
        if type_name is not None and not _matches_type_pattern(value, type_name):
            return False
        if binding_name is not None and not _bind_match_name(
            bindings,
            binding_name,
            value,
        ):
            return False
        if fields:
            if not isinstance(value, ObjectValue):
                return False
            values = tuple(value.fields.values())
            if len(values) != len(fields):
                return False
            if not self._match_pattern_sequence(values, fields, bindings):
                return False
        return guard is None or self._guard_truthy(guard, value)

    def _guard_truthy(self, guard: FunctionCode, value: Any) -> bool:
        result = self.call(FunctionValue(guard, self.globals), [value])
        return bool(result) and _truthy(result[-1])


def run(
    program: Program,
    *,
    output: Callable[[str], None] | None = None,
    list_preview_limit: int | None = None,
) -> list[Any]:
    """Execute a bytecode program with a fresh VM."""
    return VirtualMachine(
        output=output,
        list_preview_limit=list_preview_limit,
    ).run(program)


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


def _panic_matches(value: Any, type_name: str) -> bool:
    if isinstance(value, ObjectValue):
        return value.type_name == type_name
    if type_name == "String":
        return isinstance(value, str)
    if type_name == "Number":
        return isinstance(value, Decimal)
    return False


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
        if not overload.runtime_matches(args):
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
        try:
            result = implementation(args, callee.context)
        except _py_builtins.RuntimeError as exc:
            raise _with_call_detail(
                exc,
                f"element '{callee.element.name}'",
                args,
            ) from exc
        frame.stack.extend(result)
        return
    raise RuntimeError(
        _format_call_error(
            f"element '{callee.element.name}'",
            frame.stack,
            _show_overload_inputs(callee.element.definitions),
        )
    )


def _call_object_constructor(
    callee: ObjectConstructorValue,
    frame: _Frame,
    type_args: tuple[str, ...] = (),
) -> None:
    arity = len(callee.required)
    try:
        args, stack_count, next_cycle_index = frame.source_args(arity)
    except _StackUnderflow as exc:
        raise RuntimeError(
            _format_call_error(
                f"constructor '{callee.type_name}'",
                frame.stack,
                [f"{arity} argument(s)"],
            )
        ) from exc
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    fields = dict(callee.defaults)
    fields.update(dict(zip(callee.required, args, strict=True)))
    missing = [name for name in callee.fields if name not in fields]
    if missing:
        error = RuntimeError(
            f"constructor '{callee.type_name}' missing fields: {', '.join(missing)}"
        )
        error.add_call_detail(f"constructor '{callee.type_name}'", args)
        raise error
    frame.stack.append(ObjectValue(callee.type_name, fields, type_args))


def _call_resolved_builtin(
    callee: BuiltinValue,
    overload: BuiltinOverload,
    frame: _Frame,
    vectorised: bool,
    vectorised_depths: tuple[int, ...] = (),
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
    if vectorised:
        try:
            vectorized = _call_vectorized_resolved_builtin(
                overload,
                args,
                callee.context,
                vectorised_depths,
            )
        except _py_builtins.RuntimeError as exc:
            raise _with_call_detail(
                exc,
                f"element '{callee.element.name}'",
                args,
            ) from exc
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
    assert implementation is not None
    try:
        result = implementation(args, callee.context)
    except _py_builtins.RuntimeError as exc:
        raise _with_call_detail(
            exc,
            f"element '{callee.element.name}'",
            args,
        ) from exc
    frame.stack.extend(result)


def _try_unwrap(stack: list[Any]) -> bool:
    value = _pop(stack, "?")
    if _is_none_result_value(value) or _is_error_result_value(value):
        stack.append(value)
        return True
    if isinstance(value, ObjectValue):
        short_name = value.type_name.rsplit(".", 1)[-1]
        if value.type_name == "OK" or short_name in {"OK", "Some"}:
            stack.append(value.fields.get("value"))
            return False
    stack.append(value)
    return False


def _is_none_result_value(value: Any) -> bool:
    return value is None or (
        isinstance(value, ObjectValue)
        and value.type_name.rsplit(".", 1)[-1] == "None"
    )


def _is_error_result_value(value: Any) -> bool:
    return isinstance(value, ObjectValue) and (
        value.type_name == "Err"
        or value.type_name.endswith("Error")
        or value.type_name.rsplit(".", 1)[-1].endswith("Error")
    )


def _call_vectorized_builtin(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...] | None:
    if overload.implementation is None or not any(is_list_like(arg) for arg in args):
        return None
    if not overload.runtime_vector_matches(args):
        return None
    try:
        return _vectorize(overload, args, context)
    except _CannotVectorize:
        return None


def _call_vectorized_resolved_builtin(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
    vectorised_depths: tuple[int, ...] = (),
) -> tuple[Any, ...] | None:
    implementation = overload.implementation
    assert implementation is not None
    try:
        if vectorised_depths:
            return _vectorize_resolved_depths(
                implementation,
                args,
                context,
                vectorised_depths,
            )
        return _vectorize_resolved(implementation, args, context)
    except _CannotVectorize:
        return None


def _vectorize(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    vector_args = tuple(arg for arg in args if is_list_like(arg))
    if not vector_args:
        if not overload.runtime_matches(args):
            raise _CannotVectorize
        implementation = overload.implementation
        if implementation is None:
            raise _CannotVectorize
        return implementation(args, context)
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager(overload, args, context)
    return (LazyList(_vectorize_lazy(overload, args, context)),)


def _vectorize_resolved(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    vector_args = tuple(arg for arg in args if is_list_like(arg))
    if not vector_args:
        return implementation(args, context)
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager_resolved(implementation, args, context)
    return (LazyList(_vectorize_lazy_resolved(implementation, args, context)),)


def _vectorize_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
) -> tuple[Any, ...]:
    if not any(depth > 0 for depth in depths):
        return _vectorize_resolved(implementation, args, context)
    vector_args = tuple(
        arg for arg, depth in zip(args, depths, strict=False) if depth > 0
    )
    if not vector_args or not all(is_list_like(arg) for arg in vector_args):
        raise _CannotVectorize
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager_resolved_depths(implementation, args, context, depths)
    lazy_items = _vectorize_lazy_resolved_depths(
        implementation,
        args,
        context,
        depths,
    )
    return (
        LazyList(lazy_items),
    )


def _vectorize_function(
    vm: VirtualMachine,
    callee: FunctionValue,
    args: tuple[Any, ...],
) -> tuple[Any, ...]:
    def implementation(item_args: tuple[Any, ...], _context: RuntimeContext):
        return tuple(vm.call(callee, list(item_args)))

    return _vectorize_resolved(
        implementation,
        args,
        RuntimeContext(vm.output, vm.call_value, vm.format_value),
    )


def _vectorize_eager(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    vector_lengths = {len(arg) for arg in args if is_eager_sequence(arg)}
    if len(vector_lengths) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    result_items = []
    for index in range(next(iter(vector_lengths))):
        item_args = tuple(
            arg[index] if is_eager_sequence(arg) else arg for arg in args
        )
        result_items.append(_vectorize(overload, item_args, context))

    return _transpose_vectorized_items(result_items)


def _vectorize_eager_resolved(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    vector_lengths = {len(arg) for arg in args if is_eager_sequence(arg)}
    if len(vector_lengths) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    result_items = []
    for index in range(next(iter(vector_lengths))):
        item_args = tuple(
            arg[index] if is_eager_sequence(arg) else arg for arg in args
        )
        result_items.append(_vectorize_resolved(implementation, item_args, context))

    return _transpose_vectorized_items(result_items)


def _vectorize_eager_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
) -> tuple[Any, ...]:
    vector_lengths = {
        len(arg)
        for arg, depth in zip(args, depths, strict=False)
        if depth > 0 and is_eager_sequence(arg)
    }
    if len(vector_lengths) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    item_depths = tuple(max(depth - 1, 0) for depth in depths)
    result_items = []
    for index in range(next(iter(vector_lengths))):
        item_args = tuple(
            arg[index] if depth > 0 and is_eager_sequence(arg) else arg
            for arg, depth in zip(args, depths, strict=False)
        )
        result_items.append(
            _vectorize_resolved_depths(
                implementation,
                item_args,
                context,
                item_depths,
            )
        )

    return _transpose_vectorized_items(result_items)


def _vectorize_lazy(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
):
    sentinel = object()
    iterators = tuple(iter(arg) if is_list_like(arg) else None for arg in args)
    for items in zip_longest(
        *(iterator for iterator in iterators if iterator is not None),
        fillvalue=sentinel,
    ):
        if sentinel in items:
            raise RuntimeError("cannot vectorise lists with different lengths")
        item_iter = iter(items)
        item_args = tuple(next(item_iter) if is_list_like(arg) else arg for arg in args)
        result = _vectorize(overload, item_args, context)
        if len(result) != 1:
            raise RuntimeError("lazy vectorised overload must return one value")
        yield result[0]


def _vectorize_lazy_resolved(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
):
    sentinel = object()
    iterators = tuple(iter(arg) if is_list_like(arg) else None for arg in args)
    for items in zip_longest(
        *(iterator for iterator in iterators if iterator is not None),
        fillvalue=sentinel,
    ):
        if sentinel in items:
            raise RuntimeError("cannot vectorise lists with different lengths")
        item_iter = iter(items)
        item_args = tuple(next(item_iter) if is_list_like(arg) else arg for arg in args)
        result = _vectorize_resolved(implementation, item_args, context)
        if len(result) != 1:
            raise RuntimeError("lazy vectorised overload must return one value")
        yield result[0]


def _vectorize_lazy_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
):
    sentinel = object()
    iterators = tuple(
        iter(arg) if depth > 0 and is_list_like(arg) else None
        for arg, depth in zip(args, depths, strict=False)
    )
    item_depths = tuple(max(depth - 1, 0) for depth in depths)
    for items in zip_longest(
        *(iterator for iterator in iterators if iterator is not None),
        fillvalue=sentinel,
    ):
        if sentinel in items:
            raise RuntimeError("cannot vectorise lists with different lengths")
        item_iter = iter(items)
        item_args = tuple(
            next(item_iter) if depth > 0 and is_list_like(arg) else arg
            for arg, depth in zip(args, depths, strict=False)
        )
        result = _vectorize_resolved_depths(
            implementation,
            item_args,
            context,
            item_depths,
        )
        if len(result) != 1:
            raise RuntimeError("lazy vectorised overload must return one value")
        yield result[0]


def _transpose_vectorized_items(result_items: list[tuple[Any, ...]]) -> tuple[Any, ...]:
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


def _build_string(stack: list[Any], template: tuple[object, ...]) -> str:
    expression_count = sum(part is None for part in template)
    values = iter(_pop_many(stack, expression_count))
    pieces: list[str] = []
    for part in template:
        if part is None:
            pieces.append(_string_value(next(values)))
        elif isinstance(part, str):
            pieces.append(part)
        else:
            raise RuntimeError(f"invalid string interpolation part {part!r}")
    return "".join(pieces)


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


def _matches_type_pattern(value: Any, pattern: str) -> bool:
    if pattern == "Number":
        return isinstance(value, Decimal)
    if pattern == "String":
        return isinstance(value, str)
    if not isinstance(value, ObjectValue):
        return False
    if value.type_name == pattern:
        return True
    if value.type_name.rsplit(".", 1)[-1] == pattern:
        return True
    member_name = value.fields.get("name")
    return isinstance(member_name, str) and (
        member_name == pattern or f"{value.type_name}.{member_name}" == pattern
    )


def _matches_cast_type(value: Any, spec: object) -> bool:
    if not isinstance(spec, tuple) or not spec:
        return False
    kind = spec[0]
    if kind == "none":
        return value is None
    if kind == "nominal":
        return isinstance(spec[1], str) and _matches_type_pattern(value, spec[1])
    if kind == "union":
        return any(_matches_cast_type(value, item) for item in spec[1])
    if kind == "intersection":
        return all(_matches_cast_type(value, item) for item in spec[1])
    if kind == "tuple":
        if not isinstance(value, tuple) or len(value) != len(spec[1]):
            return False
        return all(
            _matches_cast_type(item, item_spec)
            for item, item_spec in zip(value, spec[1], strict=True)
        )
    if kind == "collection":
        _, collection_kind, rank, base = spec
        if not is_list_like(value):
            return False
        return _matches_collection_cast(value, collection_kind, rank, base)
    return False


def _matches_collection_cast(
    value: Any,
    kind: str,
    rank: int,
    base: object,
) -> bool:
    if kind in {"array_exact", "array_min"} and not is_eager_sequence(value):
        return False
    if rank <= 0:
        return _matches_cast_type(value, base)
    if not is_list_like(value):
        return False
    if kind in {"list_exact", "array_exact"}:
        return all(
            _matches_collection_cast(item, kind, rank - 1, base)
            for item in value
        )
    if kind in {"list_min", "array_min"}:
        return all(
            _matches_collection_cast(item, kind, rank - 1, base)
            or _matches_collection_cast(item, kind, rank, base)
            for item in value
        )
    if kind == "list_rugged":
        return all(
            _matches_cast_type(item, base)
            or _matches_collection_cast(item, kind, rank, base)
            for item in value
        )
    return False


def _bind_match_name(bindings: dict[str, Any], name: str, value: Any) -> bool:
    if name in bindings:
        return bindings[name] == value
    bindings[name] = value
    return True


def _is_rest_pattern(pattern: object) -> bool:
    return isinstance(pattern, tuple) and bool(pattern) and pattern[0] == "rest"


def _pop_index_values(
    stack: list[Any],
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
) -> list[tuple[bool, Any, Any, Any]]:
    values = iter(_pop_many(stack, _index_value_count(spec)))
    selectors = []
    for is_slice, has_start, has_stop, has_step in spec[0]:
        start = next(values) if has_start else None
        stop = next(values) if has_stop else None
        step = next(values) if has_step else None
        selectors.append((bool(is_slice), start, stop, step))
    return selectors


def _index_value_count(
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
) -> int:
    return sum(
        has_start + has_stop + has_step
        for _, has_start, has_stop, has_step in spec[0]
    )


def _index_receiver(frame: _Frame) -> Any:
    if frame.stack:
        return frame.stack.pop()
    try:
        args, stack_count, next_cycle_index = frame.source_args(1)
    except _StackUnderflow as exc:
        raise RuntimeError("stack underflow during indexing") from exc
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    return args[0]


def _get_index(
    receiver: Any,
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
    selectors: list[tuple[bool, Any, Any, Any]],
) -> Any:
    if len(selectors) > 1 and all(not item[0] for item in selectors):
        return [_index_path(receiver, item[1]) for item in selectors]
    result = receiver
    for is_slice, start, stop, step in selectors:
        if is_slice:
            result = _slice_value(result, start, stop, step)
        else:
            result = _index_path(result, start)
    return result


def _set_index(
    receiver: Any,
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
    selectors: list[tuple[bool, Any, Any, Any]],
    value: Any,
) -> Any:
    if len(selectors) != 1 or selectors[0][0]:
        raise RuntimeError("indexed assignment requires one non-slice index")
    return _set_index_path(receiver, selectors[0][1], value)


def _index_path(receiver: Any, index: Any) -> Any:
    if _is_path(index):
        result = receiver
        for item in index:
            result = _index_one(result, item)
        return result
    return _index_one(receiver, index)


def _index_one(receiver: Any, index: Any) -> Any:
    if isinstance(receiver, dict):
        try:
            return receiver[index]
        except KeyError as exc:
            raise RuntimeError(f"dictionary has no key {_format_value(index)}") from exc
    if isinstance(receiver, tuple):
        return receiver[_int_index(index)]
    if isinstance(receiver, str):
        return receiver[_int_index(index)]
    if is_eager_sequence(receiver):
        return receiver[_int_index(index)]
    if is_list_like(receiver):
        if not isinstance(index, Decimal):
            raise RuntimeError("lazy list indexing requires a numeric index")
        target = _int_index(index)
        if target < 0:
            raise RuntimeError("lazy list indexing does not support negative indices")
        for offset, item in enumerate(receiver):
            if offset == target:
                return item
        raise RuntimeError("index out of range")
    raise RuntimeError("value is not indexable")


def _slice_value(receiver: Any, start: Any, stop: Any, step: Any) -> Any:
    if _is_path(start) or _is_path(stop):
        return _slice_path(receiver, start, stop, step)
    if not (is_eager_sequence(receiver) or isinstance(receiver, str)):
        raise RuntimeError("slicing requires an eager list or string")
    step_int = 1 if step is None else _int_index(step)
    if step_int == 0:
        raise RuntimeError("slice step cannot be 0")
    length = len(receiver)
    start_int = 0 if start is None else _normal_index(_int_index(start), length)
    stop_int = length - 1 if stop is None else _normal_index(_int_index(stop), length)
    python_stop = stop_int + (1 if step_int > 0 else -1)
    sliced = receiver[start_int:python_stop:step_int]
    return "".join(sliced) if isinstance(receiver, str) else list(sliced)


def _slice_path(receiver: Any, start: Any, stop: Any, step: Any) -> Any:
    if not (_is_path(start) and _is_path(stop)):
        raise RuntimeError("multidimensional slices need start and stop paths")
    if len(start) != len(stop):
        raise RuntimeError("multidimensional slice bounds must have the same rank")
    if not start:
        return receiver
    step_value = Decimal(1) if step is None else step
    head = _slice_value(receiver, start[0], stop[0], step_value)
    if len(start) == 1:
        return head
    return [_slice_path(item, start[1:], stop[1:], step_value) for item in head]


def _set_index_path(receiver: Any, index: Any, value: Any) -> Any:
    if _is_path(index):
        if not index:
            return value
        head, *tail = index
        current = _index_one(receiver, head)
        return _set_index_one(receiver, head, _set_index_path(current, tail, value))
    return _set_index_one(receiver, index, value)


def _set_index_one(receiver: Any, index: Any, value: Any) -> Any:
    if isinstance(receiver, dict):
        if index not in receiver:
            raise RuntimeError(f"dictionary has no key {_format_value(index)}")
        updated = dict(receiver)
        updated[index] = value
        return updated
    if isinstance(receiver, tuple):
        updated = list(receiver)
        updated[_int_index(index)] = value
        return tuple(updated)
    if isinstance(receiver, str):
        if not isinstance(value, str) or len(value) != 1:
            raise RuntimeError("string indexed assignment requires one character")
        ind = _int_index(index)
        return receiver[:ind] + value + receiver[ind + 1 :]
    if is_eager_sequence(receiver):
        updated = list(receiver)
        updated[_int_index(index)] = value
        return updated
    raise RuntimeError("value is not index-assignable")


def _is_path(value: Any) -> bool:
    return isinstance(value, list)


def _int_index(value: Any) -> int:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, int):
        return value
    raise RuntimeError("index must be an integer")


def _normal_index(index: int, length: int) -> int:
    return index + length if index < 0 else index


def _get_field(receiver: Any, field: str) -> Any:
    if is_list_like(receiver):
        if is_eager_sequence(receiver):
            return [_get_field(item, field) for item in receiver]
        return LazyList(_get_field(item, field) for item in receiver)
    if isinstance(receiver, ObjectValue):
        try:
            return receiver.fields[field]
        except KeyError as exc:
            raise RuntimeError(
                f"{receiver.type_name} has no field '{field}'"
            ) from exc
    if isinstance(receiver, dict):
        try:
            return receiver[field]
        except KeyError as exc:
            raise RuntimeError(f"record has no field '{field}'") from exc
    try:
        return getattr(receiver, field)
    except AttributeError as exc:
        raise RuntimeError(f"value has no field '{field}'") from exc


def _set_field(receiver: Any, field: str, value: Any) -> Any:
    if isinstance(receiver, ObjectValue):
        if field not in receiver.fields:
            raise RuntimeError(f"{receiver.type_name} has no field '{field}'")
        fields = dict(receiver.fields)
        fields[field] = value
        return ObjectValue(receiver.type_name, fields, receiver.type_args)
    if isinstance(receiver, dict):
        if field not in receiver:
            raise RuntimeError(f"record has no field '{field}'")
        fields = dict(receiver)
        fields[field] = value
        return fields
    raise RuntimeError(f"value has no field '{field}'")


def _function_name(code: FunctionCode) -> str:
    return "function" if code.name is None else code.name


def _with_call_detail(
    exc: _py_builtins.RuntimeError,
    target: str,
    args: tuple[Any, ...],
) -> RuntimeError:
    error = exc if isinstance(exc, RuntimeError) else RuntimeError(exc)
    error.add_call_detail(target, args)
    return error


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


def _format_execution_context(context: _ExecutionContext) -> str:
    lines = [
        f"{context.function_name} ip {context.ip}: "
        f"{_format_instruction(context.instruction)}"
    ]
    lines.append(f"    stack: {_format_stack(list(context.stack))}")
    lines.append(f"    stack types: {_format_stack_types(list(context.stack))}")
    return "\n".join(lines)


def _format_instruction(instruction: object) -> str:
    if not hasattr(instruction, "op"):
        return repr(instruction)
    op = instruction.op
    name = op.value if isinstance(op, OpCode) else str(op)
    arg = getattr(instruction, "arg", None)
    if arg is None:
        return name
    return f"{name} {_format_instruction_arg(arg)}"


def _format_instruction_arg(arg: object) -> str:
    if isinstance(arg, str):
        return repr(arg)
    if isinstance(arg, Decimal):
        return _format_value(arg)
    if isinstance(arg, tuple):
        return repr(arg)
    if isinstance(arg, FunctionCode):
        return f"<function {_function_name(arg)}>"
    if isinstance(arg, FunctionSetCode):
        return f"<function set {len(arg.overloads)} overload(s)>"
    return repr(arg)


def _show_overload_inputs(overloads: tuple[BuiltinOverload, ...]) -> list[str]:
    return [
        "(" + ", ".join(str(param) for param in overload.signature.params) + ")"
        for overload in overloads
    ]


def _object_type_name(value: ObjectValue) -> str:
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"


def _format_stack(stack: list[Any]) -> str:
    if not stack:
        return "[]"
    return "[" + ", ".join(_format_value(value) for value in stack) + "]"


def _format_stack_types(stack: list[Any]) -> str:
    if not stack:
        return "[]"
    return "[" + ", ".join(_runtime_type_name(value) for value in stack) + "]"


def _format_value(value: Any) -> str:
    return format_runtime_value(
        value,
        quote_strings=True,
        lazy_preview_limit=DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    )


def _string_value(value: Any) -> str:
    return format_runtime_value(value)


def _runtime_type_name(value: Any) -> str:
    if isinstance(value, Decimal):
        return "Number"
    if isinstance(value, str):
        return "String"
    if isinstance(value, list):
        item_types = sorted({_runtime_type_name(item) for item in value})
        base = " | ".join(item_types) if item_types else "Unknown"
        return f"{base}+"
    if is_list_like(value):
        return "Unknown+"
    if isinstance(value, tuple):
        return "{" + ", ".join(_runtime_type_name(item) for item in value) + "}"
    if isinstance(value, dict):
        return "record"
    if isinstance(value, ObjectValue):
        return _object_type_name(value)
    return type(value).__name__
