"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

import builtins as _py_builtins
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import zip_longest
from typing import Any, cast

from valiance.analysis.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    runtime_elements,
)
from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    OpCode,
    Program,
    ResolvedElementReference,
)
from valiance.runtime_values import (
    DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    LazyList,
    ObjectRuntimeType,
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    is_eager_sequence,
    is_list_like,
)
from valiance.stdlib_native import runtime_stdlib_elements


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


@dataclass(slots=True)
class FunctionValue:
    """A function closure."""

    code: FunctionCode
    globals: dict[str, Any]
    owned_names: frozenset[str] = frozenset()
    refcount: int = 1

    def __repr__(self) -> str:
        return f"<{_function_name(self.code)}/{len(self.code.params)}>"

    __str__ = __repr__


@dataclass(slots=True)
class OverloadedFunctionValue:
    """A closure with one compiled body per statically analysed overload."""

    overloads: tuple[FunctionValue, ...]
    refcount: int = 1

    def __repr__(self) -> str:
        arities = ", ".join(
            str(len(overload.code.params)) for overload in self.overloads
        )
        return f"<overloaded function [{arities}]>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class BuiltinValue:
    """A built-in element implementation."""

    element: BuiltinElement
    context: RuntimeContext

    def __repr__(self) -> str:
        return f"<builtin {self.element.name.text}>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class ObjectConstructorValue:
    """Runtime constructor for nominal structured values."""

    type_name: str
    fields: tuple[str, ...]
    required: tuple[str, ...]
    defaults: dict[str, Any]
    runtime_type: ObjectRuntimeType | None = None

    def __repr__(self) -> str:
        return f"<constructor {self.type_name}>"

    __str__ = __repr__


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
    retained_locals: frozenset[str] = frozenset()
    panic_handlers: list[_PanicHandler] = field(default_factory=list)
    cycle_scopes: list[tuple[tuple[Any, ...], int]] = field(default_factory=list)

    def source_args(self, arity: int) -> tuple[tuple[Any, ...], int, int]:
        if arity == 0:
            return (), 0, self.cycle_index
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


@dataclass(frozen=True, slots=True)
class _LoopBreak(Exception):
    values: tuple[Any, ...]


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
                RuntimeContext(
                    self.output,
                    self.call_value,
                    self.format_value,
                    self.call_value_overload,
                ),
            )
            for name, element in (
                runtime_elements() | runtime_stdlib_elements()
            ).items()
        }

    def run(self, program: Program) -> list[Any]:
        """Execute a compiled program and return the final stack."""
        try:
            return self.call(FunctionValue(program.main, self.globals), [])
        except PanicSignal as exc:
            raise RuntimeError(f"uncaught panic: {_format_value(exc.value)}") from exc

    def call(
        self,
        function: FunctionValue,
        args: list[Any],
        *,
        isolate_captures: bool = True,
    ) -> list[Any]:
        if len(args) != len(function.code.params):
            raise RuntimeError(
                f"{_function_name(function.code)} expected "
                f"{len(function.code.params)} arguments, got {len(args)}"
            )
        locals_, retained_locals = _function_call_locals(
            function,
            args,
            isolate_captures=isolate_captures,
        )
        cycle_values = tuple(args) if function.code.cycle_params else ()
        initial_stack = list(args) if function.code.params and not cycle_values else []
        return self.execute(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
            retained_locals,
        )

    def call_value(self, value: Any, args: list[Any]) -> list[Any]:
        if isinstance(value, FunctionValue):
            if any(is_list_like(arg) for arg in args):
                try:
                    return list(_vectorize_function(self, value, tuple(args)))
                except PanicSignal:
                    raise
                except Exception:
                    pass
            return self.call(value, args)
        if isinstance(value, OverloadedFunctionValue):
            if len(value.overloads) == 1:
                return self.call(value.overloads[0], args)
            matches = tuple(
                overload
                for overload in value.overloads
                if len(overload.code.params) == len(args)
            )
            if len(matches) == 1:
                return self.call(matches[0], args)
            errors: list[Exception] = []
            if any(is_list_like(arg) for arg in args):
                for overload in matches:
                    try:
                        return list(_vectorize_function(self, overload, tuple(args)))
                    except PanicSignal:
                        raise
                    except Exception as exc:
                        errors.append(exc)
            for overload in matches:
                try:
                    return self.call(overload, args)
                except PanicSignal:
                    raise
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError(errors[-1]) from errors[-1]
        raise RuntimeError(f"cannot call value {_format_value(value)}")

    def call_value_overload(
        self,
        value: Any,
        args: list[Any],
        overload_index: int,
    ) -> list[Any]:
        if isinstance(value, FunctionValue):
            if overload_index == 0:
                return self.call(value, args)
            raise RuntimeError(f"function has no overload {overload_index}")
        if isinstance(value, OverloadedFunctionValue):
            try:
                return self.call(value.overloads[overload_index], args)
            except IndexError as exc:
                raise RuntimeError(
                    f"function has no overload {overload_index}"
                ) from exc
        raise RuntimeError(f"cannot call value {_format_value(value)}")

    def execute(
        self,
        code: FunctionCode,
        locals_: dict[str, Any],
        globals_: dict[str, Any],
        cycle_values: tuple[Any, ...] = (),
        initial_stack: list[Any] | None = None,
        retained_locals: frozenset[str] = frozenset(),
    ) -> list[Any]:
        frame = _Frame(
            list(initial_stack or ()),
            locals_,
            globals_,
            cycle_values,
            retained_locals=retained_locals,
        )
        ip = 0
        instructions = code.instructions
        try:
            while ip < len(instructions):
                instruction = instructions[ip]
                try:
                    match instruction.op:
                        case OpCode.PUSH_CONST:
                            frame.stack.append(_constant(instruction.arg))
                        case OpCode.LOAD_VAR:
                            frame.stack.append(
                                _retain_value(
                                    _load_name(
                                        instruction.arg,
                                        frame.locals,
                                        frame.globals,
                                    )
                                )
                            )
                        case OpCode.STORE_VAR:
                            value = _pop(frame.stack, "store variable")
                            target = (
                                frame.globals
                                if instruction.arg not in frame.locals
                                and instruction.arg in frame.globals
                                else frame.locals
                            )
                            existing = target.get(instruction.arg)
                            stored = _store_value(existing, value)
                            if existing is not None:
                                _release_value(existing, self)
                            target[instruction.arg] = stored
                            if code.name == "<main>":
                                frame.globals[instruction.arg] = stored
                            _bind_recursive_value(stored, instruction.arg)
                        case OpCode.LOAD_ELEMENT:
                            frame.stack.append(
                                _retain_value(
                                    _load_element_name(
                                        instruction.arg,
                                        frame.locals,
                                        frame.globals,
                                    )
                                )
                            )
                        case OpCode.MAKE_FUNCTION:
                            frame.stack.append(
                                _make_function_value(
                                    instruction.arg,
                                    frame.globals,
                                    frame.locals,
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
                            frame.stack.append(
                                _build_string(frame.stack, instruction.arg)
                            )
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
                            type_name, fields, required, defaults, runtime_meta = (
                                instruction.arg
                            )
                            frame.stack.append(
                                ObjectConstructorValue(
                                    type_name,
                                    tuple(fields),
                                    tuple(required),
                                    dict(defaults),
                                    _object_runtime_type(runtime_meta),
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
                                args, stack_count, next_cycle_index = (
                                    frame.source_args(1)
                                )
                            except _StackUnderflow as exc:
                                raise RuntimeError(
                                    "stack underflow during field access"
                                ) from exc
                            if stack_count:
                                del frame.stack[-stack_count:]
                            frame.cycle_index = next_cycle_index
                            receiver = args[0]
                            if isinstance(receiver, ObjectValue):
                                frame.stack.append(
                                    _extract_object_field(
                                        receiver,
                                        instruction.arg,
                                        self,
                                    )
                                )
                            else:
                                frame.stack.append(
                                    _get_field(receiver, instruction.arg)
                                )
                        case OpCode.SET_FIELD:
                            value = _pop(frame.stack, "field assignment")
                            receiver = _pop(frame.stack, "field assignment")
                            frame.stack.append(
                                _set_field(receiver, instruction.arg, value)
                            )
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
                            match_result = self._match_patterns(frame, patterns)
                            if match_result is not None:
                                bindings, values = match_result
                                _release_stack_tail(frame.stack, len(patterns), self)
                                frame.locals.update(bindings)
                                frame.cycle_scopes.append(
                                    (frame.cycle_values, frame.cycle_index)
                                )
                                frame.cycle_values = values
                                frame.cycle_index = 0
                                ip = target
                                continue
                        case OpCode.MATCH_ERROR:
                            raise RuntimeError("non-exhaustive match at runtime")
                        case OpCode.ASSERT_TRUE:
                            if not _truthy(_pop(frame.stack, "assert")):
                                raise RuntimeError("assertion failed")
                        case OpCode.UNFOLD:
                            frame.stack.append(self._unfold(frame, instruction.arg))
                        case OpCode.WHILE:
                            self._while(frame, instruction.arg)
                        case OpCode.CYCLE_BEGIN:
                            _enter_cycle(frame, instruction.arg)
                        case OpCode.CYCLE_END:
                            _exit_cycle(frame)
                        case OpCode.SOURCE_ARGS:
                            args = _source_args(
                                frame,
                                instruction.arg,
                                "argument source",
                            )
                            frame.stack.extend(args)
                        case OpCode.FOREACH:
                            self._foreach(frame, instruction.arg)
                        case OpCode.LOOP_BREAK:
                            if instruction.arg is None:
                                raise _LoopBreak(tuple(frame.stack))
                            raise _LoopBreak(
                                tuple(_pop_many(frame.stack, instruction.arg))
                            )
                        case OpCode.TRY_BEGIN:
                            frame.panic_handlers.append(
                                _PanicHandler(tuple(instruction.arg), len(frame.stack))
                            )
                        case OpCode.TRY_END:
                            if frame.panic_handlers:
                                frame.panic_handlers.pop()
                        case OpCode.PANIC:
                            value = _pop(frame.stack, "panic")
                            target = self._handle_panic(frame, PanicSignal(value))
                            if target is None:
                                raise PanicSignal(value)
                            ip = target
                            continue
                        case OpCode.TRY_UNWRAP:
                            if _try_unwrap(frame.stack, self):
                                result = frame.stack
                                frame.stack = []
                                return self._finalize_frame(frame, result)
                        case OpCode.VALIDATE_TAG:
                            try:
                                self._validate_tag(frame, instruction.arg)
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
                        case OpCode.STACK_SHUFFLE:
                            _stack_shuffle(frame, instruction.arg, self)
                        case OpCode.POP:
                            _release_value(_pop(frame.stack, "pop"), self)
                        case OpCode.RETURN:
                            result = frame.stack
                            frame.stack = []
                            return self._finalize_frame(frame, result)
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
            result = frame.stack
            frame.stack = []
            return self._finalize_frame(frame, result)
        except Exception:
            self._discard_frame(frame)
            raise

    def _finalize_frame(self, frame: _Frame, result: list[Any]) -> list[Any]:
        self._release_frame_locals(frame)
        return result

    def _discard_frame(self, frame: _Frame) -> None:
        _release_stack_tail(frame.stack, len(frame.stack), self)
        self._release_frame_locals(frame)

    def _release_frame_locals(self, frame: _Frame) -> None:
        for name, value in tuple(frame.locals.items()):
            if name in frame.retained_locals or frame.globals.get(name) is not value:
                _release_value(value, self)
            del frame.locals[name]

    def _handle_panic(self, frame: _Frame, panic: PanicSignal) -> int | None:
        while frame.panic_handlers:
            handler = frame.panic_handlers.pop()
            for type_name, target in handler.handlers:
                if type_name is None or _panic_matches(panic.value, type_name):
                    _release_stack_tail(
                        frame.stack,
                        len(frame.stack) - handler.stack_depth,
                        self,
                    )
                    return target
        return None

    def _call_stack_top(self, frame: _Frame) -> None:
        callee = _pop(frame.stack, "call")
        try:
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
                raise RuntimeError(
                    "cannot call overloaded function without resolved slot"
                )
            raise RuntimeError(f"cannot call value {_format_value(callee)}")
        finally:
            if isinstance(callee, (FunctionValue, OverloadedFunctionValue)):
                _release_value(callee, self)

    def _call_resolved_element(
        self,
        frame: _Frame,
        reference: object,
    ) -> None:
        if not isinstance(reference, ResolvedElementReference):
            raise RuntimeError(f"invalid resolved element reference {reference!r}")
        value = _load_name(reference.name, frame.locals, frame.globals)
        if isinstance(value, BuiltinValue):
            self._call_resolved_builtin_value(value, frame, reference)
            return
        if isinstance(value, FunctionValue):
            self._call_resolved_function_value(value, frame, reference)
            return
        if isinstance(value, OverloadedFunctionValue):
            self._call_resolved_overloaded_function(value, frame, reference)
            return
        if isinstance(value, ObjectConstructorValue):
            _require_single_resolved_slot(reference, "constructor")
            _call_object_constructor(value, frame, reference.type_args)
            return
        if isinstance(value, ObjectValue):
            _require_single_resolved_slot(reference, "enum member")
            frame.stack.append(_retain_value(value))
            return
        if reference.overload_index == 0 and not callable(value):
            frame.stack.append(_retain_value(value))
            return
        raise RuntimeError(f"resolved element '{reference.name}' is not callable")

    def _call_resolved_builtin_value(
        self,
        value: BuiltinValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> None:
        try:
            overload = value.element.definitions[reference.overload_index]
        except IndexError as exc:
            raise RuntimeError(
                f"resolved element '{reference.name}' has no overload "
                f"{reference.overload_index}"
            ) from exc
        _call_resolved_builtin(
            value,
            overload,
            frame,
            reference.vectorised,
            reference.vectorised_depths,
            reference.arity_override,
            reference.consumed_override,
            reference.static_values,
        )

    def _call_resolved_function_value(
        self,
        value: FunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> None:
        _require_single_resolved_slot(reference, "function")
        frame.stack.extend(reference.static_values)
        self._call_function(value, frame, vectorised=reference.vectorised)

    def _call_resolved_overloaded_function(
        self,
        value: OverloadedFunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> None:
        try:
            overload = value.overloads[reference.overload_index]
        except IndexError as exc:
            raise RuntimeError(
                f"resolved function '{reference.name}' has no overload "
                f"{reference.overload_index}"
            ) from exc
        if reference.multidispatch:
            overload = _select_multimethod_overload(value, overload, frame)
        frame.stack.extend(reference.static_values)
        self._call_function(overload, frame, vectorised=reference.vectorised)

    def _validate_tag(self, frame: _Frame, spec: object) -> None:
        tag_name, overload_index = spec
        if not frame.stack:
            raise RuntimeError(f"cannot validate {tag_name} on an empty stack")
        validator = _load_name(tag_name, frame.locals, frame.globals)
        value = _retain_value(frame.stack[-1])
        try:
            result = self.call_value_overload(validator, [value], overload_index)
        finally:
            _release_value(value, self)
        if not result or not _truthy(result[-1]):
            raise PanicSignal(
                ObjectValue(
                    "PanicError",
                    {"message": f"tag validator {tag_name} failed"},
                )
            )

    def _unfold(self, frame: _Frame, config: object) -> LazyList:
        condition_code, body_code, arity = config
        state = list(_source_args(frame, arity, "unfold"))
        body = _make_function_value(body_code, frame.globals, frame.locals)
        condition = (
            None
            if condition_code is None
            else _make_function_value(condition_code, frame.globals, frame.locals)
        )

        def generated():
            nonlocal state
            while True:
                if condition is not None:
                    keep_going = _call_unfold_function(self, condition, state)
                    if not keep_going or not _truthy(keep_going[-1]):
                        return
                outputs = _call_unfold_function(self, body, state)
                if len(outputs) > arity + 1:
                    raise RuntimeError(
                        "unfold body produced more than state arity plus one value"
                    )
                if len(outputs) == arity + 1:
                    state = list(outputs[:arity])
                    emitted = _unfold_present_emission(outputs[-1])
                    if emitted is _SKIP_UNFOLD_EMISSION:
                        continue
                    yield emitted
                else:
                    missing = arity - len(outputs)
                    state = list(state[-missing:] if missing else ()) + list(outputs)
                    yield state[-1]

        return LazyList(generated())

    def _while(self, frame: _Frame, config: object) -> None:
        condition_code, body_code, arity = config
        state = list(_source_args(frame, arity, "while"))
        condition = _make_function_value(condition_code, frame.globals, frame.locals)
        body = _make_function_value(body_code, frame.globals, frame.locals)
        while True:
            keep_going = self.call(condition, list(state), isolate_captures=False)
            if not keep_going or not _truthy(keep_going[-1]):
                frame.stack.extend(state)
                return
            try:
                outputs = self.call(body, list(state), isolate_captures=False)
            except _LoopBreak as signal:
                frame.stack.extend(signal.values)
                return
            state = list(outputs[:arity])

    def _foreach(self, frame: _Frame, config: object) -> None:
        body_code, has_index, completion_count = config
        iterable = _source_args(frame, 1, "foreach")[0]
        if not is_list_like(iterable):
            raise RuntimeError("foreach requires a list value")
        body = _make_function_value(body_code, frame.globals, frame.locals)
        for index, item in enumerate(iterable):
            args = [item]
            if has_index:
                args.append(Decimal(index))
            try:
                self.call(body, args, isolate_captures=False)
            except _LoopBreak as signal:
                _sync_captured_globals(frame, body.globals)
                frame.stack.extend(signal.values)
                return
        _sync_captured_globals(frame, body.globals)
        frame.stack.extend(ObjectValue("None", {}) for _ in range(completion_count))

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
            _mark_mustcall_method(args, result, callee)
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
            _mark_mustcall_method(args, result, callee)
            frame.stack.extend(result)

    def _match_patterns(
        self,
        frame: _Frame,
        patterns: tuple[object, ...],
    ) -> tuple[dict[str, Any], tuple[Any, ...]] | None:
        if len(frame.stack) < len(patterns):
            return None
        bindings: dict[str, Any] = {}
        values = tuple(reversed(frame.stack[-len(patterns) :]))
        if values:
            bindings["top"] = values[0]
        for pattern, value in zip(patterns, values, strict=True):
            if not self._match_pattern(value, pattern, bindings):
                return None
        return bindings, values

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
        _, type_spec, binding_name, fields, guard = pattern
        if type_spec is not None and not _matches_cast_type(value, type_spec):
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


def _require_single_resolved_slot(
    reference: ResolvedElementReference,
    target_kind: str,
) -> None:
    if reference.overload_index == 0:
        return
    raise RuntimeError(
        f"resolved {target_kind} '{reference.name}' has no overload "
        f"{reference.overload_index}"
    )


def _make_function_value(
    code: object,
    globals_: dict[str, Any],
    locals_: dict[str, Any] | None = None,
) -> FunctionValue | OverloadedFunctionValue:
    captured = dict(globals_)
    local_values = {} if locals_ is None else dict(locals_)
    captured.update(local_values)
    owned_names = frozenset(local_values)
    for name in owned_names:
        captured[name] = _retain_value(captured[name])
    if isinstance(code, FunctionCode):
        value = FunctionValue(code, captured, owned_names)
        if code.recursive:
            value.globals["this"] = value
        return value
    if isinstance(code, FunctionSetCode):
        value = OverloadedFunctionValue(
            tuple(
                FunctionValue(overload, captured, owned_names)
                for overload in code.overloads
            )
        )
        for overload in value.overloads:
            if overload.code.recursive:
                overload.globals["this"] = value
        return value
    raise RuntimeError(f"invalid function bytecode value {code!r}")


def _function_call_locals(
    function: FunctionValue,
    args: list[Any],
    *,
    isolate_captures: bool = True,
) -> tuple[dict[str, Any], frozenset[str]]:
    locals_: dict[str, Any] = dict(zip(function.code.params, args, strict=True))
    if not isolate_captures:
        return locals_, frozenset()
    retained: set[str] = set()
    for name in function.owned_names:
        if name in locals_ or name not in function.globals:
            continue
        locals_[name] = _retain_value(function.globals[name])
        retained.add(name)
    return locals_, frozenset(retained)


def _source_args(frame: _Frame, arity: int, context: str) -> tuple[Any, ...]:
    try:
        args, stack_count, next_cycle_index = frame.source_args(arity)
    except _StackUnderflow as exc:
        raise RuntimeError(f"stack underflow during {context}") from exc
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    return args


_SKIP_UNFOLD_EMISSION = object()


def _call_unfold_function(
    vm: VirtualMachine,
    value: Any,
    state: list[Any],
) -> list[Any]:
    if isinstance(value, FunctionValue):
        return _execute_unfold_function(vm, value, state)
    if isinstance(value, OverloadedFunctionValue):
        matches = tuple(
            overload
            for overload in value.overloads
            if len(overload.code.params) == len(state)
        )
        errors: list[Exception] = []
        for overload in matches:
            try:
                return _execute_unfold_function(vm, overload, state)
            except PanicSignal:
                raise
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(errors[-1]) from errors[-1]
    raise RuntimeError(f"cannot call value {_format_value(value)}")


def _execute_unfold_function(
    vm: VirtualMachine,
    function: FunctionValue,
    state: list[Any],
) -> list[Any]:
    if len(state) != len(function.code.params):
        raise RuntimeError(
            f"{_function_name(function.code)} expected "
            f"{len(function.code.params)} state value(s), got {len(state)}"
        )
    locals_, retained_locals = _function_call_locals(
        function,
        state,
        isolate_captures=False,
    )
    return vm.execute(
        function.code,
        locals_,
        function.globals,
        tuple(state),
        [],
        retained_locals,
    )


def _unfold_present_emission(value: Any) -> Any:
    if _is_none_result_value(value):
        return _SKIP_UNFOLD_EMISSION
    if (
        isinstance(value, ObjectValue)
        and value.type_name.rsplit(".", 1)[-1] == "Some"
        and "value" in value.fields
    ):
        return value.fields["value"]
    return value


def _sync_captured_globals(frame: _Frame, captured: dict[str, Any]) -> None:
    frame.globals.update(captured)
    for name in tuple(frame.locals):
        if name in captured:
            frame.locals[name] = captured[name]


def _enter_cycle(frame: _Frame, spec: object) -> None:
    if not isinstance(spec, tuple) or len(spec) != 2:
        raise RuntimeError(f"invalid cycle scope {spec!r}")
    arity, seed_stack = spec
    if arity is None:
        values = tuple(frame.stack)
    elif isinstance(arity, int):
        values = _source_args(frame, arity, "cycle scope")
    else:
        raise RuntimeError(f"invalid cycle arity {arity!r}")
    frame.cycle_scopes.append((frame.cycle_values, frame.cycle_index))
    frame.cycle_values = values
    frame.cycle_index = 0
    if seed_stack:
        frame.stack.extend(values)


def _exit_cycle(frame: _Frame) -> None:
    if not frame.cycle_scopes:
        raise RuntimeError("cycle scope underflow")
    frame.cycle_values, frame.cycle_index = frame.cycle_scopes.pop()


def _store_value(existing: Any, value: Any) -> Any:
    if _is_function_value(existing) and _is_function_value(value):
        for overload in _function_overloads(existing) + _function_overloads(value):
            _retain_value(overload)
        return OverloadedFunctionValue(
            _function_overloads(existing) + _function_overloads(value)
        )
    return value


def _bind_recursive_value(value: Any, name: str) -> None:
    if isinstance(value, FunctionValue):
        value.globals[name] = value
        return
    if isinstance(value, OverloadedFunctionValue):
        for overload in value.overloads:
            overload.globals[name] = value


def _is_function_value(value: Any) -> bool:
    return isinstance(value, (FunctionValue, OverloadedFunctionValue))


def _object_runtime_type(value: object) -> ObjectRuntimeType | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 6:
        raise RuntimeError(f"invalid object runtime metadata {value!r}")
    return ObjectRuntimeType(
        destructor_name=cast(str | None, value[0]),
        pop_name=cast(str | None, value[1]),
        dup_name=cast(str | None, value[2]),
        dup_error=cast(str | None, value[3]),
        mustcall_mode=cast(str | None, value[4]),
        mustcall_methods=cast(tuple[str, ...], value[5]),
    )


def _release_stack_tail(stack: list[Any], count: int, vm: VirtualMachine) -> None:
    if count <= 0:
        return
    values = _pop_many(stack, count)
    for value in values:
        _release_value(value, vm)


def _retain_value(value: Any, *, check_duplication: bool = True) -> Any:
    if isinstance(value, LazyList):
        value.refcount += 1
        return value
    if isinstance(value, ObjectValue):
        if value.destroyed:
            raise RuntimeError(f"use after destruction of {_object_type_name(value)}")
        if (
            check_duplication
            and value.runtime_type is not None
            and value.runtime_type.dup_error is not None
            and value.refcount >= 1
        ):
            raise PanicSignal(
                _fault_object("DuplicationFault", value.runtime_type.dup_error)
            )
        value.refcount += 1
        return value
    if isinstance(value, FunctionValue):
        value.refcount += 1
        return value
    if isinstance(value, OverloadedFunctionValue):
        value.refcount += 1
        return value
    if isinstance(value, list):
        for item in value:
            _retain_value(item, check_duplication=check_duplication)
        return value
    if isinstance(value, tuple):
        for item in value:
            _retain_value(item, check_duplication=check_duplication)
        return value
    if isinstance(value, dict):
        for item in value.values():
            _retain_value(item, check_duplication=check_duplication)
        return value
    return value


def _release_value(value: Any, vm: VirtualMachine) -> None:
    if isinstance(value, LazyList):
        value.refcount -= 1
        if value.refcount > 0:
            return
        for item in value.owned_values:
            _release_value(item, vm)
        return
    if isinstance(value, ObjectValue):
        value.refcount -= 1
        if value.refcount > 0:
            return
        if value.cleaning_up:
            return
        _run_object_cleanup(value, vm)
        return
    if isinstance(value, FunctionValue):
        value.refcount -= 1
        if value.refcount > 0:
            return
        for name in value.owned_names:
            if name in value.globals:
                _release_value(value.globals[name], vm)
        value.globals.clear()
        return
    if isinstance(value, OverloadedFunctionValue):
        value.refcount -= 1
        if value.refcount > 0:
            return
        for overload in value.overloads:
            _release_value(overload, vm)
        return
    if isinstance(value, list):
        for item in value:
            _release_value(item, vm)
        return
    if isinstance(value, tuple):
        for item in value:
            _release_value(item, vm)
        return
    if isinstance(value, dict):
        for item in value.values():
            _release_value(item, vm)


def _run_object_cleanup(value: ObjectValue, vm: VirtualMachine) -> None:
    if value.destroyed:
        return
    value.cleaning_up = True
    runtime = value.runtime_type
    pop_error: PanicSignal | None = None
    if runtime is not None and runtime.pop_name is not None:
        try:
            vm.call_value(
                _load_name(runtime.pop_name, {}, vm.globals),
                [_retain_value(value)],
            )
        except PanicSignal as exc:
            pop_error = exc
    if runtime is not None and not _mustcall_satisfied(value):
        pop_error = PanicSignal(
            _fault_object("CleanupFault", _cleanup_fault_message(value))
        )
    if runtime is not None and runtime.destructor_name is not None:
        try:
            vm.call_value(
                _load_name(runtime.destructor_name, {}, vm.globals),
                [_retain_value(value)],
            )
        except PanicSignal as exc:
            raise RuntimeError(
                f"destructor for {_object_type_name(value)} must not panic"
            ) from exc
    value.cleaning_up = False
    value.destroyed = True
    for item in value.fields.values():
        _release_value(item, vm)
    if pop_error is not None:
        raise pop_error


def _mustcall_satisfied(value: ObjectValue) -> bool:
    runtime = value.runtime_type
    if runtime is None or runtime.mustcall_mode is None or not runtime.mustcall_methods:
        return True
    called = value.mustcall_called
    required = set(runtime.mustcall_methods)
    if runtime.mustcall_mode == "all":
        return required.issubset(called)
    if runtime.mustcall_mode == "any":
        return bool(required & set(called))
    return True


def _cleanup_fault_message(value: ObjectValue) -> str:
    runtime = value.runtime_type
    if runtime is None or not runtime.mustcall_methods:
        return f"{_object_type_name(value)} was dropped without required cleanup"
    names = ", ".join(runtime.mustcall_methods)
    return f"{_object_type_name(value)} requires one of: {names}"


def _fault_object(type_name: str, message: str) -> ObjectValue:
    return ObjectValue(type_name, {"message": message})


def _mark_mustcall_method(
    args: tuple[Any, ...],
    result: list[Any] | tuple[Any, ...],
    callee: FunctionValue,
) -> None:
    if not args or not isinstance(args[0], ObjectValue):
        return
    runtime = args[0].runtime_type
    if runtime is None or not runtime.mustcall_methods:
        return
    method_name = _function_name(callee.code).rsplit("::", 1)[-1]
    if method_name not in runtime.mustcall_methods:
        return
    called = frozenset((*args[0].mustcall_called, method_name))
    args[0].mustcall_called = called
    for value in result:
        if isinstance(value, ObjectValue) and value.type_name == args[0].type_name:
            value.mustcall_called = called


def _finalize_builtin_result_ownership(
    args: tuple[Any, ...],
    result: tuple[Any, ...],
) -> None:
    counts: Counter[int] = Counter()
    values: dict[int, Any] = {}
    arg_ids = {
        id(value)
        for value in args
        if isinstance(
            value,
            (ObjectValue, FunctionValue, OverloadedFunctionValue, list, tuple, dict),
        )
    }
    for value in result:
        if not isinstance(
            value,
            (ObjectValue, FunctionValue, OverloadedFunctionValue, list, tuple, dict),
        ):
            continue
        ident = id(value)
        counts[ident] += 1
        values[ident] = value
    for ident, count in counts.items():
        retains = count if ident in arg_ids else count - 1
        for _ in range(max(retains, 0)):
            _retain_value(values[ident])


def _bind_lazy_result_owners(
    args: tuple[Any, ...],
    result: tuple[Any, ...],
) -> tuple[Any, ...]:
    bound: list[Any] = []
    for value in result:
        if not isinstance(value, LazyList):
            bound.append(value)
            continue
        owned: list[Any] = []
        for arg in args:
            retained = _retain_value(arg)
            owned.append(retained)
        value.owned_values = tuple(owned)
        bound.append(value)
    return tuple(bound)


def _panic_matches(value: Any, type_name: str) -> bool:
    if isinstance(value, ObjectValue):
        return value.type_name == type_name
    if type_name == "String":
        return isinstance(value, str)
    if type_name == "Integer":
        return isinstance(value, Decimal) and value == value.to_integral_value()
    if type_name == "Real":
        return isinstance(value, Decimal)
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


def _select_multimethod_overload(
    value: OverloadedFunctionValue,
    fallback: FunctionValue,
    frame: _Frame,
) -> FunctionValue:
    arity = len(fallback.code.params)
    try:
        args, _, _ = frame.source_args(arity)
    except _StackUnderflow:
        return fallback
    for overload in value.overloads:
        if overload is fallback or not overload.code.multi:
            continue
        if len(overload.code.params) != arity:
            continue
        if _runtime_types_match(args, overload.code.dispatch_types):
            return overload
    return fallback


def _runtime_types_match(
    args: tuple[Any, ...],
    dispatch_types: tuple[str | None, ...],
) -> bool:
    if len(args) != len(dispatch_types):
        return False
    return all(
        expected is not None and _runtime_type_name(arg) == expected
        for arg, expected in zip(args, dispatch_types, strict=True)
    )


def _runtime_type_name(value: Any) -> str | None:
    if isinstance(value, ObjectValue):
        if not value.type_args:
            return value.type_name
        return f"{value.type_name}[{', '.join(value.type_args)}]"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return "Integer"
        return "Real"
    if isinstance(value, str):
        return "String"
    if isinstance(value, bool):
        return "Boolean"
    return None


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
            vectorized = _bind_lazy_result_owners(args, vectorized)
            _finalize_builtin_result_ownership(args, vectorized)
            if stack_count:
                _release_stack_tail(
                    frame.stack,
                    stack_count,
                    callee.context.call.__self__,
                )
            frame.cycle_index = next_cycle_index
            frame.stack.extend(vectorized)
            return
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
        result = _bind_lazy_result_owners(args, result)
        _finalize_builtin_result_ownership(args, result)
        if stack_count:
            _release_stack_tail(frame.stack, stack_count, callee.context.call.__self__)
        frame.cycle_index = next_cycle_index
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
    frame.stack.append(
        ObjectValue(
            callee.type_name,
            fields,
            type_args,
            runtime_type=callee.runtime_type,
        )
    )


def _call_resolved_builtin(
    callee: BuiltinValue,
    overload: BuiltinOverload,
    frame: _Frame,
    vectorised: bool,
    vectorised_depths: tuple[int, ...] = (),
    arity_override: int | None = None,
    consumed_override: int | None = None,
    static_values: tuple[Any, ...] = (),
) -> None:
    arity = (
        arity_override
        if arity_override is not None
        else len(overload.signature.params)
    )
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
    consumed_count = (
        min(consumed_override, stack_count)
        if consumed_override is not None
        else stack_count
    )
    context = (
        replace(callee.context, static_values=static_values)
        if static_values
        else callee.context
    )
    if vectorised:
        try:
            vectorized = _call_vectorized_resolved_builtin(
                overload,
                args,
                context,
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
        vectorized = _bind_lazy_result_owners(args, vectorized)
        _finalize_builtin_result_ownership(args, vectorized)
        if consumed_count:
            _release_stack_tail(frame.stack, consumed_count, context.call.__self__)
        frame.cycle_index = next_cycle_index
        frame.stack.extend(vectorized)
        return
    implementation = overload.implementation
    assert implementation is not None
    try:
        result = implementation(args, context)
    except _py_builtins.RuntimeError as exc:
        raise _with_call_detail(
            exc,
            f"element '{callee.element.name}'",
            args,
        ) from exc
    result = _bind_lazy_result_owners(args, result)
    _finalize_builtin_result_ownership(args, result)
    if consumed_count:
        _release_stack_tail(frame.stack, consumed_count, context.call.__self__)
    frame.cycle_index = next_cycle_index
    frame.stack.extend(result)


def _stack_shuffle(frame: _Frame, spec: object, vm: VirtualMachine) -> None:
    mode, prestack, poststack = _stack_shuffle_spec(spec)
    arity = len(prestack)
    try:
        args, stack_count, next_cycle_index = frame.source_args(arity)
    except _StackUnderflow as exc:
        raise RuntimeError(f"stack underflow during {mode}") from exc

    labelled = {
        label: value
        for label, value in zip(prestack, args, strict=True)
        if label is not None
    }
    outputs = tuple(labelled[label] for label in poststack)
    if mode == "copy":
        for value in outputs:
            _retain_value(value, check_duplication=False)
        frame.cycle_index = next_cycle_index
        frame.stack.extend(outputs)
        return

    output_counts = Counter(poststack)
    stack_arg_start = arity - stack_count
    retained_outputs: set[str] = set()
    for index, label in enumerate(prestack):
        if label is None:
            if index < stack_arg_start:
                _retain_value(args[index], check_duplication=False)
            continue
        count = output_counts[label]
        retains = count if index < stack_arg_start else max(count - 1, 0)
        for _ in range(retains):
            _retain_value(args[index], check_duplication=False)
        if count:
            retained_outputs.add(label)

    if stack_count:
        _pop_many(frame.stack, stack_count)
    frame.cycle_index = next_cycle_index
    for index, (label, value) in enumerate(zip(prestack, args, strict=True)):
        if label is None:
            frame.stack.append(value)
        elif index >= stack_arg_start and label not in retained_outputs:
            _release_value(value, vm)
    frame.stack.extend(outputs)


def _stack_shuffle_spec(
    spec: object,
) -> tuple[str, tuple[str | None, ...], tuple[str, ...]]:
    if not isinstance(spec, tuple) or len(spec) != 3:
        raise RuntimeError(f"invalid stack shuffle spec {spec!r}")
    mode, prestack, poststack = spec
    if mode not in {"copy", "move"}:
        raise RuntimeError(f"invalid stack shuffle mode {mode!r}")
    if not isinstance(prestack, tuple) or not all(
        label is None or isinstance(label, str) for label in prestack
    ):
        raise RuntimeError(f"invalid stack shuffle prestack {prestack!r}")
    if not isinstance(poststack, tuple) or not all(
        isinstance(label, str) for label in poststack
    ):
        raise RuntimeError(f"invalid stack shuffle poststack {poststack!r}")
    labels = {label for label in prestack if label is not None}
    for label in poststack:
        if label not in labels:
            raise RuntimeError(
                f"stack shuffle poststack label {label!r} is not in prestack"
            )
    return mode, prestack, poststack


def _extract_object_field(receiver: ObjectValue, field: str, vm: VirtualMachine) -> Any:
    try:
        value = receiver.fields[field]
    except KeyError as exc:
        raise RuntimeError(f"{receiver.type_name} has no field '{field}'") from exc
    retained = _retain_value(value)
    _release_value(receiver, vm)
    return retained


def _try_unwrap(stack: list[Any], vm: VirtualMachine) -> bool:
    value = _pop(stack, "?")
    if _is_none_result_value(value) or _is_error_result_value(value):
        stack.append(value)
        return True
    if isinstance(value, ObjectValue):
        short_name = value.type_name.rsplit(".", 1)[-1]
        if value.type_name == "OK" or short_name in {"OK", "Some"}:
            retained = _retain_value(value.fields.get("value"))
            _release_value(value, vm)
            stack.append(retained)
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
        RuntimeContext(
            vm.output,
            vm.call_value,
            vm.format_value,
            vm.call_value_overload,
        ),
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


def _load_element_name(
    name: str,
    locals_: dict[str, Any],
    globals_: dict[str, Any],
) -> Any:
    if name in globals_:
        return globals_[name]
    if name in locals_:
        return locals_[name]
    raise RuntimeError(f"undefined name '{name}'")


def _constant(value: Any) -> Any:
    return value


def _truthy(value: Any) -> bool:
    return value != 0 and value is not None


def _matches_type_pattern(value: Any, pattern: str) -> bool:
    if pattern == "Integer":
        return isinstance(value, Decimal) and value == value.to_integral_value()
    if pattern == "Real":
        return isinstance(value, Decimal)
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
    if kind == "var":
        return not is_list_like(value)
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
        return ObjectValue(
            receiver.type_name,
            fields,
            receiver.type_args,
            runtime_type=receiver.runtime_type,
            mustcall_called=receiver.mustcall_called,
        )
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
    compact = _compact_diagnostic_value(value)
    if compact is not None:
        return compact
    return format_runtime_value(
        value,
        quote_strings=True,
        lazy_preview_limit=DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    )


def _compact_diagnostic_value(value: Any) -> str | None:
    if isinstance(value, FunctionValue):
        name = _function_name(value.code)
        arity = len(value.code.params)
        return f"<{name}/{arity}>"
    if isinstance(value, OverloadedFunctionValue):
        arities = ", ".join(
            str(len(overload.code.params)) for overload in value.overloads
        )
        return f"<overloaded function [{arities}]>"
    if isinstance(value, BuiltinValue):
        return f"<builtin {value.element.name.text}>"
    if isinstance(value, ObjectConstructorValue):
        return f"<constructor {value.type_name}>"
    if isinstance(value, LazyList):
        return "<lazy list>"
    if isinstance(value, list):
        return _compact_sequence("[", "]", value)
    if isinstance(value, tuple):
        return _compact_sequence("(", ")", value)
    if isinstance(value, dict):
        return _compact_mapping(value)
    return None


def _compact_sequence(opening: str, closing: str, values: Iterable[Any]) -> str:
    preview = []
    has_more = False
    for index, item in enumerate(values):
        if index >= DIAGNOSTIC_LIST_PREVIEW_LIMIT:
            has_more = True
            break
        preview.append(_format_value(item))
    inner = ", ".join(preview)
    if has_more:
        inner = f"{inner}, ..." if inner else "..."
    return opening + inner + closing


def _compact_mapping(value: dict[Any, Any]) -> str:
    items = []
    for index, (key, item) in enumerate(value.items()):
        if index >= DIAGNOSTIC_LIST_PREVIEW_LIMIT:
            items.append("...")
            break
        items.append(f"{_format_value(key)}: {_format_value(item)}")
    return "{" + ", ".join(items) + "}"


def _string_value(value: Any) -> str:
    return format_runtime_value(value)


def _runtime_type_name(value: Any) -> str:
    if isinstance(value, Decimal):
        return "Integer" if value == value.to_integral_value() else "Real"
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
