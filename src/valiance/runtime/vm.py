"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
    LazyList,
    ObjectValue,
    is_eager_sequence,
    is_list_like,
)


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


@dataclass(frozen=True, slots=True)
class ObjectConstructorValue:
    """Runtime constructor for nominal structured values."""

    type_name: str
    fields: tuple[str, ...]
    required: tuple[str, ...]
    defaults: dict[str, Any]


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
                case OpCode.BUILD_STRING:
                    frame.stack.append(_build_string(frame.stack, instruction.arg))
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
                    receiver = _pop(frame.stack, "field access")
                    frame.stack.append(_get_field(receiver, instruction.arg))
                case OpCode.SET_FIELD:
                    value = _pop(frame.stack, "field assignment")
                    receiver = _pop(frame.stack, "field assignment")
                    frame.stack.append(_set_field(receiver, instruction.arg, value))
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
            or len(reference) not in {2, 3}
            or not isinstance(reference[0], str)
            or not isinstance(reference[1], int)
        ):
            raise RuntimeError(f"invalid resolved element reference {reference!r}")
        name, overload_index = reference[:2]
        vectorised = bool(reference[2]) if len(reference) == 3 else False
        value = _load_name(name, frame.locals, frame.globals)
        if isinstance(value, BuiltinValue):
            try:
                overload = value.element.definitions[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved element '{name}' has no overload {overload_index}"
                ) from exc
            _call_resolved_builtin(value, overload, frame, vectorised)
            return
        if isinstance(value, FunctionValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                )
            self._call_function(value, frame, vectorised=vectorised)
            return
        if isinstance(value, OverloadedFunctionValue):
            try:
                overload = value.overloads[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved function '{name}' has no overload {overload_index}"
                ) from exc
            self._call_function(overload, frame, vectorised=vectorised)
            return
        if isinstance(value, ObjectConstructorValue):
            if overload_index != 0:
                raise RuntimeError(
                    f"resolved constructor '{name}' has no overload {overload_index}"
                )
            _call_object_constructor(value, frame)
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
            frame.stack.extend(_vectorize_function(self, callee, args))
        else:
            frame.stack.extend(self.call(callee, list(args)))

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
        frame.stack.extend(implementation(args, callee.context))
        return
    raise RuntimeError(
        _format_call_error(
            f"element '{callee.element.name}'",
            frame.stack,
            _show_overload_inputs(callee.element.definitions),
        )
    )


def _call_object_constructor(callee: ObjectConstructorValue, frame: _Frame) -> None:
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
        raise RuntimeError(
            f"constructor '{callee.type_name}' missing fields: {', '.join(missing)}"
        )
    frame.stack.append(ObjectValue(callee.type_name, fields))


def _call_resolved_builtin(
    callee: BuiltinValue,
    overload: BuiltinOverload,
    frame: _Frame,
    vectorised: bool,
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
        vectorized = _call_vectorized_resolved_builtin(overload, args, callee.context)
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
    frame.stack.extend(implementation(args, callee.context))


def _call_vectorized_builtin(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...] | None:
    if overload.implementation is None or not any(is_list_like(arg) for arg in args):
        return None
    try:
        return _vectorize(overload, args, context)
    except _CannotVectorize:
        return None


def _call_vectorized_resolved_builtin(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...] | None:
    implementation = overload.implementation
    assert implementation is not None
    try:
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
        RuntimeContext(vm.output, vm.call_value),
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


def _bind_match_name(bindings: dict[str, Any], name: str, value: Any) -> bool:
    if name in bindings:
        return bindings[name] == value
    bindings[name] = value
    return True


def _is_rest_pattern(pattern: object) -> bool:
    return isinstance(pattern, tuple) and bool(pattern) and pattern[0] == "rest"


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
        return ObjectValue(receiver.type_name, fields)
    if isinstance(receiver, dict):
        if field not in receiver:
            raise RuntimeError(f"record has no field '{field}'")
        fields = dict(receiver)
        fields[field] = value
        return fields
    raise RuntimeError(f"value has no field '{field}'")


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
    if is_list_like(value):
        return "<lazy list>"
    if isinstance(value, tuple):
        return "(" + ", ".join(_format_value(item) for item in value) + ")"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_format_value(key)}: {_format_value(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, ObjectValue):
        items = ", ".join(
            f"{name}: {_format_value(item)}" for name, item in value.fields.items()
        )
        return f"{value.type_name}{{{items}}}"
    return repr(value)


def _string_value(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + ", ".join(_string_value(item) for item in value) + "]"
    if is_list_like(value):
        return "<lazy list>"
    if isinstance(value, tuple):
        return "(" + ", ".join(_string_value(item) for item in value) + ")"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_string_value(key)}: {_string_value(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, ObjectValue):
        items = ", ".join(
            f"{name}: {_string_value(item)}" for name, item in value.fields.items()
        )
        return f"{value.type_name}{{{items}}}"
    return str(value)


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
        return value.type_name
    return type(value).__name__
