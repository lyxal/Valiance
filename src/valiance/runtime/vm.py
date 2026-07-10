"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

import builtins as _py_builtins
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from decimal import Decimal
from itertools import islice, zip_longest
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
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
)
from valiance.runtime_values import (
    DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    LazyList,
    ListValue,
    ObjectRuntimeType,
    ObjectValue,
    PanicSignal,
    TaggedValue,
    format_runtime_value,
    is_eager_sequence,
    is_list_like,
    runtime_collection_rank,
    runtime_value_tags,
    unwrap_runtime_value,
    update_runtime_tags,
    with_runtime_collection_rank,
)
from valiance.stdlib_native import runtime_stdlib_elements
from valiance.types import (
    AtomicType,
    CollectionType,
    DataTag,
    ExactType,
    RuntimeTypePattern,
    TaggedType,
    UnionDispatchBranch,
    Variance,
    normalize,
)


class RuntimeError(_py_builtins.RuntimeError):
    """Raised when bytecode execution fails."""

    def __init__(self, message: object) -> None:
        """Initialize this runtime error."""
        super().__init__(message)
        self.message = str(message)
        self.call_details: list[tuple[str, tuple[Any, ...]]] = []
        self.execution_contexts: list[_ExecutionContext] = []

    def add_call_detail(self, target: str, args: tuple[Any, ...]) -> None:
        """Attach one unique failed-call description to this runtime error."""
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
        """Attach one unique instruction and stack snapshot to this error."""
        context = _ExecutionContext(function_name, ip, instruction, tuple(stack))
        if context not in self.execution_contexts:
            self.execution_contexts.append(context)

    def __str__(self) -> str:
        """Return the human-readable representation of this runtime error."""
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


class AssertionFailure(RuntimeError):
    """Raised when a bare Valiance assertion fails."""


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
        """Return a developer-facing representation of this function value."""
        return f"<{_function_name(self.code)}/{len(self.code.params)}>"

    __str__ = __repr__


_NO_EXTENSION_DEFAULT = object()
_MISSING_VECTOR_ITEM = object()
_UNINITIALIZED_OBJECT_FIELD = object()


@dataclass(slots=True)
class _RuntimeVectorExtension:
    default: Any = _NO_EXTENSION_DEFAULT
    rules: tuple[
        tuple[tuple[bool, ...], FunctionValue | OverloadedFunctionValue],
        ...,
    ] = ()
    selector: FunctionValue | OverloadedFunctionValue | None = None

    def owned_values(self) -> tuple[Any, ...]:
        """Return values whose ownership is retained by this vector extension."""
        values: list[Any] = []
        if self.default is not _NO_EXTENSION_DEFAULT:
            values.append(self.default)
        values.extend(function for _, function in self.rules)
        if self.selector is not None:
            values.append(self.selector)
        return tuple(values)


@dataclass(slots=True)
class OverloadedFunctionValue:
    """A closure with one compiled body per statically analysed overload."""

    overloads: tuple[FunctionValue, ...]
    dispatch_plan: tuple[UnionDispatchBranch, ...] = ()
    refcount: int = 1

    def __repr__(self) -> str:
        """Return a developer-facing representation of this overloaded function value."""
        arities = ", ".join(
            str(len(overload.code.params)) for overload in self.overloads
        )
        return f"<overloaded function [{arities}]>"

    __str__ = __repr__


_OWNERSHIP_VALUE_TYPES = (
    ObjectValue,
    FunctionValue,
    OverloadedFunctionValue,
    list,
    tuple,
    dict,
)
_RELEASE_VALUE_TYPES = (LazyList, *_OWNERSHIP_VALUE_TYPES)


@dataclass(frozen=True, slots=True)
class BuiltinValue:
    """A built-in element implementation."""

    element: BuiltinElement
    context: RuntimeContext

    def __repr__(self) -> str:
        """Return a developer-facing representation of this builtin value."""
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
    initializer: FunctionValue | OverloadedFunctionValue | None = None

    def __repr__(self) -> str:
        """Return a developer-facing representation of this object constructor value."""
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
    cycle_stack_remaining: int = 0
    retained_locals: frozenset[str] = frozenset()
    panic_handlers: list[_PanicHandler] = field(default_factory=list)
    cycle_scopes: list[tuple[tuple[Any, ...], int, int]] = field(default_factory=list)

    def source_args(
        self,
        arity: int,
    ) -> tuple[tuple[Any, ...], int, int, int]:
        """Preview arguments from physical values and the conceptual input stack."""
        if arity == 0:
            return (), 0, self.cycle_index, self.cycle_stack_remaining

        stack_count = min(len(self.stack), arity)
        stack_args = tuple(self.stack[-stack_count:]) if stack_count else ()
        if arity == 1 and stack_count == 0 and self.cycle_values:
            value = self.cycle_values[self.cycle_index % len(self.cycle_values)]
            return (
                (value,),
                0,
                (self.cycle_index + 1) % len(self.cycle_values),
                0,
            )
        initial_count = min(
            self.cycle_stack_remaining,
            arity - stack_count,
        )
        initial_start = self.cycle_stack_remaining - initial_count
        initial_args = self.cycle_values[
            initial_start : self.cycle_stack_remaining
        ]
        missing = arity - stack_count - initial_count
        if missing and not self.cycle_values:
            raise _StackUnderflow
        cycle_args = tuple(
            self.cycle_values[(self.cycle_index + index) % len(self.cycle_values)]
            for index in range(missing)
        )
        next_cycle_index = (
            (self.cycle_index + missing) % len(self.cycle_values)
            if self.cycle_values
            else self.cycle_index
        )
        return (
            cycle_args + initial_args + stack_args,
            stack_count,
            next_cycle_index,
            initial_start,
        )


class _StackUnderflow(Exception):
    """Internal signal for trying another runtime overload shape."""


@dataclass(frozen=True, slots=True)
class _LoopBreak(Exception):
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _FunctionReturn(Exception):
    values: tuple[Any, ...]


class VirtualMachine:
    """A small stack-based bytecode interpreter."""

    def __init__(
        self,
        *,
        output: Callable[[str], None] | None = None,
        list_preview_limit: int | None = None,
    ) -> None:
        """Initialize this virtual machine."""
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
        """Invoke one compiled function with explicit runtime arguments."""
        parameter_count = len(function.code.params)
        if function.code.accepts_stack_inputs:
            if len(args) < parameter_count:
                raise RuntimeError(
                    f"{_function_name(function.code)} expected at least "
                    f"{parameter_count} arguments, got {len(args)}"
                )
            explicit_args = args[-parameter_count:] if parameter_count else []
            stack_args = args[:-parameter_count] if parameter_count else args
        else:
            if len(args) != parameter_count:
                raise RuntimeError(
                    f"{_function_name(function.code)} expected "
                    f"{parameter_count} arguments, got {len(args)}"
                )
            explicit_args = args
            stack_args = []
        locals_, retained_locals = _function_call_locals(
            function,
            explicit_args,
            isolate_captures=isolate_captures,
        )
        cycle_values = tuple(explicit_args) if function.code.cycle_params else ()
        initial_stack = list(stack_args)
        if function.code.params and not cycle_values:
            initial_stack.extend(explicit_args)
        result = self.execute(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
            retained_locals,
        )
        ranked = _apply_runtime_collection_ranks(
            result,
            function.code.return_collection_ranks,
        )
        return list(_apply_runtime_return_tags(ranked, function.code.return_tags))

    def call_value(self, value: Any, args: list[Any]) -> list[Any]:
        """Invoke a runtime callable, resolving overload and vectorisation behaviour."""
        if isinstance(value, FunctionValue):
            if any(is_list_like(arg) for arg in args):
                try:
                    ranks = value.code.param_collection_ranks
                    if ranks and all(rank is not None for rank in ranks):
                        return list(
                            _vectorize_function(
                                self,
                                value,
                                tuple(args),
                                target_ranks=ranks,
                            )
                        )
                    return list(_vectorize_function(self, value, tuple(args)))
                except PanicSignal:
                    raise
                except Exception:
                    pass
            return self.call(value, args)
        if isinstance(value, OverloadedFunctionValue):
            if len(value.overloads) == 1:
                return self.call(value.overloads[0], args)
            if value.dispatch_plan:
                selected = _select_union_dispatch_overload(value, tuple(args))
                return self.call(selected, args)
            matches = tuple(
                overload
                for overload in value.overloads
                if len(overload.code.params) == len(args)
            )
            exact = _select_exact_runtime_overload(matches, tuple(args))
            if exact is not None:
                return self.call(exact, args)
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
        """Invoke one statically selected overload of a runtime callable."""
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
        """Execute bytecode in a new frame and return its final stack."""
        frame = _Frame(
            stack=list(initial_stack or ()),
            locals=locals_,
            globals=globals_,
            cycle_values=cycle_values,
            cycle_stack_remaining=(len(cycle_values) if code.cycle_params else 0),
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
                            count, rank = (
                                instruction.arg
                                if isinstance(instruction.arg, tuple)
                                else (instruction.arg, None)
                            )
                            frame.stack.append(
                                ListValue(
                                    _pop_many(frame.stack, count),
                                    runtime_rank=rank,
                                )
                            )
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
                            constructor = _object_constructor_reference(
                                instruction.arg
                            )
                            initializer = (
                                None
                                if constructor.initializer is None
                                else _make_function_value(
                                    constructor.initializer,
                                    frame.globals,
                                    frame.locals,
                                )
                            )
                            frame.stack.append(
                                ObjectConstructorValue(
                                    constructor.type_name,
                                    constructor.fields,
                                    constructor.required,
                                    dict(constructor.defaults),
                                    _object_runtime_type(
                                        constructor.runtime_metadata
                                    ),
                                    initializer,
                                )
                            )
                        case OpCode.MAKE_ENUM_MEMBER:
                            enum_name, member_name, value = instruction.arg
                            fields = {"name": member_name}
                            if value is not None:
                                fields["value"] = value
                            frame.stack.append(ObjectValue(enum_name, fields))
                        case OpCode.GET_FIELD:
                            field, optional_safe = _field_instruction_arg(
                                instruction.arg
                            )
                            try:
                                (
                                    args,
                                    stack_count,
                                    next_cycle_index,
                                    next_cycle_stack_remaining,
                                ) = frame.source_args(1)
                            except _StackUnderflow as exc:
                                raise RuntimeError(
                                    "stack underflow during field access"
                                ) from exc
                            if stack_count:
                                del frame.stack[-stack_count:]
                            frame.cycle_index = next_cycle_index
                            frame.cycle_stack_remaining = (
                                next_cycle_stack_remaining
                            )
                            receiver = args[0]
                            if optional_safe:
                                frame.stack.append(
                                    _optional_safe_get_field(
                                        receiver,
                                        field,
                                        self,
                                    )
                                )
                            elif isinstance(receiver, ObjectValue):
                                frame.stack.append(
                                    _extract_object_field(
                                        receiver,
                                        field,
                                        self,
                                    )
                                )
                            else:
                                frame.stack.append(_get_field(receiver, field))
                        case OpCode.SET_FIELD:
                            field, optional_safe = _field_instruction_arg(
                                instruction.arg
                            )
                            receiver, value = _source_args(
                                frame,
                                2,
                                "field assignment",
                            )
                            frame.stack.append(
                                _optional_safe_set_field(
                                    receiver,
                                    field,
                                    value,
                                    self,
                                )
                                if optional_safe
                                else _set_field(receiver, field, value)
                            )
                        case OpCode.GET_INDEX:
                            try:
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
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
                        case OpCode.SET_INDEX:
                            try:
                                values = _pop_index_values(frame.stack, instruction.arg)
                                receiver = _pop(frame.stack, "indexed assignment")
                                value = _pop(frame.stack, "indexed assignment")
                                frame.stack.append(
                                    _set_index(
                                        receiver,
                                        instruction.arg,
                                        values,
                                        value,
                                    )
                                )
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
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
                                    (
                                        frame.cycle_values,
                                        frame.cycle_index,
                                        frame.cycle_stack_remaining,
                                    )
                                )
                                frame.cycle_values = values
                                frame.cycle_index = 0
                                frame.cycle_stack_remaining = 0
                                ip = target
                                continue
                        case OpCode.MATCH_ERROR:
                            raise RuntimeError("non-exhaustive match at runtime")
                        case OpCode.ASSERT_TRUE:
                            if not _truthy(_pop(frame.stack, "assert")):
                                raise AssertionFailure("assertion failed")
                        case OpCode.WRAP_ASSERT_ERROR:
                            value = _pop(frame.stack, "assert else")
                            frame.stack.append(
                                ObjectValue(
                                    "AssertError",
                                    {
                                        "value": value,
                                        "message": self.format_value(value),
                                    },
                                )
                            )
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
                        case OpCode.RETURN_SIGNAL:
                            result = tuple(frame.stack)
                            frame.stack = []
                            raise _FunctionReturn(result)
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
        except _FunctionReturn as signal:
            if code.name == "foreach.body":
                self._discard_frame(frame)
                raise
            return self._finalize_frame(frame, list(signal.values))
        except Exception:
            self._discard_frame(frame)
            raise

    def _finalize_frame(self, frame: _Frame, result: list[Any]) -> list[Any]:
        """Compute finalize frame during VM execution."""
        self._release_frame_locals(frame)
        return result

    def _discard_frame(self, frame: _Frame) -> None:
        """Update discard frame state during VM execution."""
        _release_stack_tail(frame.stack, len(frame.stack), self)
        self._release_frame_locals(frame)

    def _release_frame_locals(self, frame: _Frame) -> None:
        """Release frame locals during VM execution."""
        for name, value in tuple(frame.locals.items()):
            if name in frame.retained_locals or frame.globals.get(name) is not value:
                _release_value(value, self)
            del frame.locals[name]

    def _handle_panic(self, frame: _Frame, panic: PanicSignal) -> int | None:
        """Handle panic during VM execution."""
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
        """Invoke stack top during VM execution."""
        callee = _pop(frame.stack, "call")
        try:
            if isinstance(callee, BuiltinValue):
                _call_builtin(callee, frame)
                return
            if isinstance(callee, FunctionValue):
                self._call_function(callee, frame)
                return
            if isinstance(callee, ObjectConstructorValue):
                _call_object_constructor(callee, frame, self)
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
        """Invoke resolved element during VM execution."""
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
            _call_object_constructor(
                value,
                frame,
                self,
                reference.type_args,
                reference.overload_index,
            )
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
        """Invoke resolved builtin value during VM execution."""
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
            self,
            reference.vectorised,
            reference.vectorised_depths,
            reference.vectorised_target_ranks,
            reference.return_collection_ranks,
            reference.arity_override,
            reference.consumed_override,
            reference.static_values,
            reference.extension,
        )

    def _call_resolved_function_value(
        self,
        value: FunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> None:
        """Invoke resolved function value during VM execution."""
        _require_single_resolved_slot(reference, "function")
        frame.stack.extend(reference.static_values)
        self._call_function(
            value,
            frame,
            vectorised=reference.vectorised,
            vectorised_depths=reference.vectorised_depths,
            vectorised_target_ranks=reference.vectorised_target_ranks,
            extension_reference=reference.extension,
        )

    def _call_resolved_overloaded_function(
        self,
        value: OverloadedFunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> None:
        """Invoke resolved overloaded function during VM execution."""
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
        self._call_function(
            overload,
            frame,
            vectorised=reference.vectorised,
            vectorised_depths=reference.vectorised_depths,
            vectorised_target_ranks=reference.vectorised_target_ranks,
            extension_reference=reference.extension,
        )

    def _validate_tag(self, frame: _Frame, spec: object) -> None:
        """Update validate tag state during VM execution."""
        tag_name, overload_index, added, removed = spec
        if not frame.stack:
            raise RuntimeError(f"cannot validate {tag_name} on an empty stack")
        if overload_index is not None:
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
        frame.stack[-1] = update_runtime_tags(
            frame.stack[-1],
            add=tuple(DataTag(name, depth) for name, depth in added),
            remove=tuple(DataTag(name, depth) for name, depth in removed),
        )

    def _unfold(self, frame: _Frame, config: object) -> LazyList:
        """Compute unfold during VM execution."""
        condition_code, body_code, arity = config
        state = list(_source_args(frame, arity, "unfold"))
        body = _make_function_value(body_code, frame.globals, frame.locals)
        condition = (
            None
            if condition_code is None
            else _make_function_value(condition_code, frame.globals, frame.locals)
        )

        def generated():
            """Handle generated during VM execution."""
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
        """Update while state during VM execution."""
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
        """Update foreach state during VM execution."""
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
            except _FunctionReturn:
                _sync_captured_globals(frame, body.globals)
                raise
        _sync_captured_globals(frame, body.globals)
        frame.stack.extend(ObjectValue("None", {}) for _ in range(completion_count))

    def _call_function(
        self,
        callee: FunctionValue,
        frame: _Frame,
        *,
        vectorised: bool = False,
        vectorised_depths: tuple[int, ...] = (),
        vectorised_target_ranks: tuple[int | None, ...] = (),
        extension_reference: VectorExtensionReference | None = None,
    ) -> None:
        """Invoke function during VM execution."""
        arity = len(callee.code.params)
        try:
            (
                args,
                stack_count,
                next_cycle_index,
                next_cycle_stack_remaining,
            ) = frame.source_args(arity)
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
        frame.cycle_stack_remaining = next_cycle_stack_remaining
        if vectorised:
            extension = _materialize_vector_extension(
                self,
                extension_reference,
                frame,
            )
            try:
                result = _vectorize_function(
                    self,
                    callee,
                    args,
                    vectorised_depths,
                    vectorised_target_ranks,
                    extension,
                )
            except _py_builtins.RuntimeError as exc:
                raise _with_call_detail(
                    exc,
                    f"function '{_function_name(callee.code)}'",
                    args,
                ) from exc
            finally:
                _release_runtime_vector_extension(extension, self)
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
        """Match patterns during VM execution."""
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
        """Return the Boolean result of match pattern during virtual-machine execution."""
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
        """Return the Boolean result of match list pattern during virtual-machine execution."""
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
        """Return the Boolean result of match list items during virtual-machine execution."""
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
        """Return the Boolean result of match pattern sequence during virtual-machine execution."""
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
        """Return the Boolean result of match type pattern during virtual-machine execution."""
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
        """Return the Boolean result of guard truthy during virtual-machine execution."""
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
    """Update require single resolved slot state during VM execution."""
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
    """Create function value during VM execution."""
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
            ),
            code.dispatch_plan,
        )
        for overload in value.overloads:
            if overload.code.recursive:
                overload.globals["this"] = value
        return value
    raise RuntimeError(f"invalid function bytecode value {code!r}")


def _materialize_vector_extension(
    vm: VirtualMachine,
    reference: VectorExtensionReference | None,
    frame: _Frame,
) -> _RuntimeVectorExtension | None:
    """Compute materialize vector extension during VM execution."""
    if reference is None:
        return None

    created: list[FunctionValue | OverloadedFunctionValue] = []
    default: Any = _NO_EXTENSION_DEFAULT
    try:
        if reference.default is not None:
            function = _make_function_value(
                reference.default,
                frame.globals,
                frame.locals,
            )
            try:
                result = _call_extension_function(vm, function, ())
            finally:
                _release_value(function, vm)
            if len(result) != 1:
                raise RuntimeError("extend default must produce exactly one value")
            default = result[0]

        rules = []
        for rule in reference.rules:
            function = _make_function_value(
                rule.function,
                frame.globals,
                frame.locals,
            )
            created.append(function)
            rules.append((rule.presence, function))

        selector = None
        if reference.selector is not None:
            selector = _make_function_value(
                reference.selector,
                frame.globals,
                frame.locals,
            )
            created.append(selector)

        return _RuntimeVectorExtension(default, tuple(rules), selector)
    except Exception:
        if default is not _NO_EXTENSION_DEFAULT:
            _release_value(default, vm)
        for function in created:
            _release_value(function, vm)
        raise


def _call_extension_function(
    vm: VirtualMachine,
    function: FunctionValue | OverloadedFunctionValue,
    args: tuple[Any, ...],
) -> list[Any]:
    """Invoke extension function during VM execution."""
    if isinstance(function, FunctionValue):
        return vm.call(function, list(args))
    matches = tuple(
        overload
        for overload in function.overloads
        if len(overload.code.params) == len(args)
    )
    if len(matches) != 1:
        raise RuntimeError(
            "extend function does not have one unambiguous runtime overload"
        )
    return vm.call(matches[0], list(args))


def _extension_owned_values(
    extension: _RuntimeVectorExtension | None,
) -> tuple[Any, ...]:
    """Collect the values for extension owned during VM execution."""
    return () if extension is None else extension.owned_values()


def _release_runtime_vector_extension(
    extension: _RuntimeVectorExtension | None,
    vm: VirtualMachine,
) -> None:
    """Release runtime vector extension during VM execution."""
    for value in _extension_owned_values(extension):
        _release_value(value, vm)


def _function_call_locals(
    function: FunctionValue,
    args: list[Any],
    *,
    isolate_captures: bool = True,
) -> tuple[dict[str, Any], frozenset[str]]:
    """Compute function call locals during VM execution."""
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
    """Compute source args during VM execution."""
    try:
        (
            args,
            stack_count,
            next_cycle_index,
            next_cycle_stack_remaining,
        ) = frame.source_args(arity)
    except _StackUnderflow as exc:
        raise RuntimeError(f"stack underflow during {context}") from exc
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    frame.cycle_stack_remaining = next_cycle_stack_remaining
    return args


_SKIP_UNFOLD_EMISSION = object()


def _call_unfold_function(
    vm: VirtualMachine,
    value: Any,
    state: list[Any],
) -> list[Any]:
    """Invoke unfold function during VM execution."""
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
    """Execute unfold function during VM execution."""
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
    """Compute unfold present emission during VM execution."""
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
    """Update sync captured globals state during VM execution."""
    frame.globals.update(captured)
    for name in tuple(frame.locals):
        if name in captured:
            frame.locals[name] = captured[name]


def _enter_cycle(frame: _Frame, spec: object) -> None:
    """Update enter cycle state during VM execution."""
    if not isinstance(spec, tuple) or len(spec) != 2:
        raise RuntimeError(f"invalid cycle scope {spec!r}")
    arity, seed_stack = spec
    if arity == "current":
        values = frame.cycle_values
    elif arity is None:
        values = tuple(frame.stack)
    elif isinstance(arity, int):
        values = _source_args(frame, arity, "cycle scope")
    else:
        raise RuntimeError(f"invalid cycle arity {arity!r}")
    frame.cycle_scopes.append(
        (
            frame.cycle_values,
            frame.cycle_index,
            frame.cycle_stack_remaining,
        )
    )
    frame.cycle_values = values
    frame.cycle_index = 0
    frame.cycle_stack_remaining = 0
    if seed_stack:
        frame.stack.extend(values)


def _exit_cycle(frame: _Frame) -> None:
    """Update exit cycle state during VM execution."""
    if not frame.cycle_scopes:
        raise RuntimeError("cycle scope underflow")
    (
        frame.cycle_values,
        frame.cycle_index,
        frame.cycle_stack_remaining,
    ) = frame.cycle_scopes.pop()


def _store_value(existing: Any, value: Any) -> Any:
    """Store value during VM execution."""
    if _is_function_value(existing) and _is_function_value(value):
        for overload in _function_overloads(existing) + _function_overloads(value):
            _retain_value(overload)
        return OverloadedFunctionValue(
            _function_overloads(existing) + _function_overloads(value)
        )
    return value


def _bind_recursive_value(value: Any, name: str) -> None:
    """Bind recursive value during VM execution."""
    if isinstance(value, FunctionValue):
        value.globals.setdefault(name, value)
        return
    if isinstance(value, OverloadedFunctionValue):
        for overload in value.overloads:
            overload.globals.setdefault(name, value)


def _is_function_value(value: Any) -> bool:
    """Return whether the value is function value."""
    return isinstance(value, (FunctionValue, OverloadedFunctionValue))


def _object_runtime_type(value: object) -> ObjectRuntimeType | None:
    """Determine the type of object runtime during VM execution."""
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


def _object_constructor_reference(value: object) -> ObjectConstructorReference:
    """Normalize named and legacy positional constructor bytecode payloads."""
    if isinstance(value, ObjectConstructorReference):
        return value
    if isinstance(value, tuple):
        if len(value) == 5:
            type_name, fields, required, defaults, runtime_metadata = value
            initializer = None
        elif len(value) == 6:
            (
                type_name,
                fields,
                required,
                defaults,
                runtime_metadata,
                initializer,
            ) = value
        else:
            raise RuntimeError(f"invalid object constructor metadata {value!r}")
        if not isinstance(type_name, str):
            raise RuntimeError(f"invalid object constructor type {type_name!r}")
        if not isinstance(fields, tuple) or not all(
            isinstance(field, str) for field in fields
        ):
            raise RuntimeError(f"invalid object constructor fields {fields!r}")
        if not isinstance(required, tuple) or not all(
            isinstance(field, str) for field in required
        ):
            raise RuntimeError(
                f"invalid object constructor required fields {required!r}"
            )
        if not isinstance(defaults, tuple):
            raise RuntimeError(f"invalid object constructor defaults {defaults!r}")
        if initializer is not None and not isinstance(
            initializer,
            (FunctionCode, FunctionSetCode),
        ):
            raise RuntimeError(
                f"invalid object constructor initializer {initializer!r}"
            )
        return ObjectConstructorReference(
            type_name,
            fields,
            required,
            defaults,
            runtime_metadata,
            initializer,
        )
    raise RuntimeError(f"invalid object constructor metadata {value!r}")


def _release_stack_tail(stack: list[Any], count: int, vm: VirtualMachine) -> None:
    """Release stack tail during VM execution."""
    if count <= 0:
        return
    start = len(stack) - count
    for index in range(start, len(stack)):
        if _needs_release(stack[index]):
            break
    else:
        del stack[start:]
        return
    values = _pop_many(stack, count)
    for value in values:
        _release_value(value, vm)


def _needs_release(value: Any) -> bool:
    """Return whether dropping a value requires ownership bookkeeping."""
    if isinstance(value, TaggedValue):
        value = value.value
    return isinstance(value, _RELEASE_VALUE_TYPES)


def _retain_value(value: Any, *, check_duplication: bool = True) -> Any:
    """Retain value during VM execution."""
    if isinstance(value, TaggedValue):
        _retain_value(value.value, check_duplication=check_duplication)
        return value
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
    """Release value during VM execution."""
    if isinstance(value, TaggedValue):
        _release_value(value.value, vm)
        return
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
    """Update run object cleanup state during VM execution."""
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
    """Return the Boolean result of mustcall satisfied during virtual-machine execution."""
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
    """Format the message for cleanup fault during VM execution."""
    runtime = value.runtime_type
    if runtime is None or not runtime.mustcall_methods:
        return f"{_object_type_name(value)} was dropped without required cleanup"
    names = ", ".join(runtime.mustcall_methods)
    return f"{_object_type_name(value)} requires one of: {names}"


def _fault_object(type_name: str, message: str) -> ObjectValue:
    """Compute fault object during VM execution."""
    return ObjectValue(type_name, {"message": message})


def _mark_mustcall_method(
    args: tuple[Any, ...],
    result: list[Any] | tuple[Any, ...],
    callee: FunctionValue,
) -> None:
    """Update mark mustcall method state during VM execution."""
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
    """Update finalize builtin result ownership state during VM execution."""
    if not _contains_owned_value(args) and not _contains_owned_value(result):
        return
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

    visited: set[int] = set()
    for value in result:
        if id(value) in arg_ids:
            continue
        _retain_embedded_builtin_args(value, arg_ids, visited)


def _contains_owned_value(values: tuple[Any, ...]) -> bool:
    """Return whether values include a result requiring ownership finalization."""
    for value in values:
        if isinstance(value, _OWNERSHIP_VALUE_TYPES):
            return True
    return False


def _retain_embedded_builtin_args(
    value: Any,
    arg_ids: set[int],
    visited: set[int],
) -> None:
    """Retain input values newly owned by a builtin result container."""
    if not isinstance(
        value,
        (ObjectValue, FunctionValue, OverloadedFunctionValue, list, tuple, dict),
    ):
        return
    ident = id(value)
    if ident in arg_ids:
        _retain_value(value)
        return
    if ident in visited:
        return
    visited.add(ident)

    if isinstance(value, ObjectValue):
        children = value.fields.values()
    elif isinstance(value, dict):
        children = value.values()
    elif isinstance(value, (list, tuple)):
        children = value
    else:
        return
    for child in children:
        _retain_embedded_builtin_args(child, arg_ids, visited)


def _bind_lazy_result_owners(
    args: tuple[Any, ...],
    result: tuple[Any, ...],
) -> tuple[Any, ...]:
    """Bind lazy result owners during VM execution."""
    for value in result:
        if isinstance(value, LazyList):
            break
    else:
        return result
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
    """Return the Boolean result of panic matches during virtual-machine execution."""
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
    """Collect the overloads for function during VM execution."""
    if isinstance(value, FunctionValue):
        return (value,)
    return value.overloads


def _select_exact_runtime_overload(
    overloads: tuple[FunctionValue, ...],
    args: tuple[Any, ...],
) -> FunctionValue | None:
    """Select an overload by exact runtime type without executing candidates."""
    matches = tuple(
        overload
        for overload in overloads
        if overload.code.dispatch_types
        and _runtime_types_match(args, overload.code.dispatch_types)
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            "ambiguous overloaded function call for runtime types "
            f"{tuple(_runtime_type_name(arg) for arg in args)}"
        )
    return None


def _select_union_dispatch_overload(
    value: OverloadedFunctionValue,
    args: tuple[Any, ...],
) -> FunctionValue:
    """Select union dispatch overload during VM execution."""
    matches = tuple(
        branch
        for branch in value.dispatch_plan
        if len(branch.params) == len(args)
        and all(
            _runtime_pattern_matches(arg, pattern)
            for arg, pattern in zip(args, branch.params, strict=True)
        )
    )
    indexes = {branch.overload_index for branch in matches}
    if len(indexes) != 1:
        detail = "no branch" if not indexes else "ambiguous branches"
        raise RuntimeError(
            f"cannot dispatch overloaded function: {detail} for runtime types "
            f"{tuple(_runtime_type_name(arg) for arg in args)}"
        )
    index = indexes.pop()
    try:
        return value.overloads[index]
    except IndexError as exc:
        raise RuntimeError(f"function has no overload {index}") from exc


def _runtime_pattern_matches(value: Any, pattern: RuntimeTypePattern) -> bool:
    """Return the Boolean result of runtime pattern matches during virtual-machine execution."""
    if pattern.kind == "tagged":
        actual_tags = runtime_value_tags(value)
        for required in pattern.tags:
            present = DataTag(required.name, required.depth) in actual_tags
            if required.absent == present:
                return False
        return _runtime_pattern_matches(
            unwrap_runtime_value(value),
            pattern.children[0],
        )
    if pattern.kind == "union":
        return any(_runtime_pattern_matches(value, item) for item in pattern.children)
    if pattern.kind == "none":
        return isinstance(value, ObjectValue) and value.type_name == "None"
    if pattern.kind == "tuple":
        return isinstance(value, tuple) and len(value) == len(pattern.children) and all(
            _runtime_pattern_matches(item, child)
            for item, child in zip(value, pattern.children, strict=True)
        )
    if pattern.kind == "collection":
        return _runtime_collection_pattern_matches(value, pattern)
    if pattern.kind != "nominal":
        return False
    actual = _runtime_value_pattern(value)
    return actual is not None and _runtime_pattern_subtype(actual, pattern)


def _runtime_collection_pattern_matches(
    value: Any,
    pattern: RuntimeTypePattern,
) -> bool:
    """Return the Boolean result of runtime collection pattern matches during virtual-machine execution."""
    if not is_list_like(value) or pattern.rank is None or not pattern.children:
        return False

    def matches_rank(item: Any, rank: int) -> bool:
        """Return whether the value matches rank."""
        if rank == 0:
            return _runtime_pattern_matches(item, pattern.children[0])
        if not is_list_like(item):
            return False
        return all(matches_rank(child, rank - 1) for child in item)

    return matches_rank(value, pattern.rank)


def _runtime_value_pattern(value: Any) -> RuntimeTypePattern | None:
    """Compute runtime value pattern during VM execution."""
    value = unwrap_runtime_value(value)
    if isinstance(value, ObjectValue):
        return RuntimeTypePattern(
            "nominal",
            name=value.type_name,
            children=tuple(_parse_runtime_type_pattern(arg) for arg in value.type_args),
            accepted_names=(value.type_name,),
        )
    if isinstance(value, Decimal):
        name = "Integer" if value == value.to_integral_value() else "Real"
        return RuntimeTypePattern("nominal", name=name, accepted_names=(name,))
    if isinstance(value, str):
        return RuntimeTypePattern("nominal", name="String", accepted_names=("String",))
    return None


def _runtime_pattern_subtype(
    actual: RuntimeTypePattern,
    target: RuntimeTypePattern,
) -> bool:
    """Return the Boolean result of runtime pattern subtype during virtual-machine execution."""
    if target.kind == "union":
        return any(_runtime_pattern_subtype(actual, item) for item in target.children)
    if actual.kind != target.kind:
        return False
    if target.kind != "nominal":
        return actual == target
    if actual.name not in target.accepted_names:
        return False
    if actual.name != target.name or not target.children:
        return True
    if len(actual.children) != len(target.children):
        return False
    variances = target.variances or (Variance.INVARIANT,) * len(target.children)
    for actual_arg, target_arg, variance in zip(
        actual.children,
        target.children,
        variances,
        strict=True,
    ):
        if variance is Variance.COVARIANT:
            if not _runtime_pattern_subtype(actual_arg, target_arg):
                return False
        elif variance is Variance.CONTRAVARIANT:
            if not _runtime_pattern_subtype(target_arg, actual_arg):
                return False
        elif not _runtime_patterns_same_type(actual_arg, target_arg):
            return False
    return True


def _runtime_patterns_same_type(
    left: RuntimeTypePattern,
    right: RuntimeTypePattern,
) -> bool:
    """Return the Boolean result of runtime patterns same type during virtual-machine execution."""
    return (
        left.kind == right.kind
        and left.name == right.name
        and len(left.children) == len(right.children)
        and all(
            _runtime_patterns_same_type(a, b)
            for a, b in zip(left.children, right.children, strict=True)
        )
        and left.tags == right.tags
        and left.rank == right.rank
        and left.collection_kind == right.collection_kind
    )


def _parse_runtime_type_pattern(text: str) -> RuntimeTypePattern:
    """Compute parse runtime type pattern during VM execution."""
    text = text.strip()
    union_parts = _split_runtime_type_args(text, "|")
    if len(union_parts) > 1:
        return RuntimeTypePattern(
            "union",
            children=tuple(_parse_runtime_type_pattern(part) for part in union_parts),
        )
    bracket = text.find("[")
    if bracket < 0 or not text.endswith("]"):
        return RuntimeTypePattern("nominal", text, accepted_names=(text,))
    name = text[:bracket].strip()
    inner = text[bracket + 1 : -1]
    return RuntimeTypePattern(
        "nominal",
        name,
        tuple(
            _parse_runtime_type_pattern(part)
            for part in _split_runtime_type_args(inner, ",")
        ),
        (name,),
    )


def _split_runtime_type_args(text: str, separator: str) -> tuple[str, ...]:
    """Compute split runtime type args during VM execution."""
    depth = 0
    start = 0
    parts: list[str] = []
    for index, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == separator and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return tuple(part for part in parts if part)


def _select_multimethod_overload(
    value: OverloadedFunctionValue,
    fallback: FunctionValue,
    frame: _Frame,
) -> FunctionValue:
    """Select multimethod overload during VM execution."""
    arity = len(fallback.code.params)
    try:
        args, _, _, _ = frame.source_args(arity)
    except _StackUnderflow:
        return fallback
    matches: list[FunctionValue] = []
    for overload in value.overloads:
        if not overload.code.multi:
            continue
        if len(overload.code.params) != arity:
            continue
        if _runtime_multimethod_types_match(args, overload.code.dispatch_types):
            matches.append(overload)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(
            "ambiguous multimethod call for runtime types "
            f"{tuple(_runtime_type_name(arg) for arg in args)}"
        )
    return fallback


def _runtime_types_match(
    args: tuple[Any, ...],
    dispatch_types: tuple[str | None, ...],
) -> bool:
    """Return the Boolean result of runtime types match during virtual-machine execution."""
    if len(args) != len(dispatch_types):
        return False
    return all(
        expected is not None and _runtime_type_name(arg) == expected
        for arg, expected in zip(args, dispatch_types, strict=True)
    )


def _runtime_multimethod_types_match(
    args: tuple[Any, ...],
    dispatch_types: tuple[str | None, ...],
) -> bool:
    """Return the Boolean result of runtime multimethod types match during virtual-machine execution."""
    if len(args) != len(dispatch_types) or not any(
        expected is not None for expected in dispatch_types
    ):
        return False
    return all(
        expected is None or _runtime_argument_type_matches(arg, expected)
        for arg, expected in zip(args, dispatch_types, strict=True)
    )


def _runtime_argument_type_matches(value: Any, expected: str) -> bool:
    """Return the Boolean result of runtime argument type matches during virtual-machine execution."""
    value = unwrap_runtime_value(value)
    if isinstance(value, ObjectValue) and value.type_name == expected:
        return True
    return _runtime_type_name(value) == expected


def _runtime_type_name(value: Any) -> str | None:
    """Return the canonical name for runtime type during VM execution."""
    value = unwrap_runtime_value(value)
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



def _dynamic_callable_arity(value: Any) -> int | None:
    """Return one unambiguous runtime arity for a callable value."""
    if isinstance(value, FunctionValue):
        return len(value.code.params)
    if isinstance(value, OverloadedFunctionValue):
        arities = {len(overload.code.params) for overload in value.overloads}
        return next(iter(arities)) if len(arities) == 1 else None
    if isinstance(value, ObjectConstructorValue):
        if value.initializer is None:
            return len(value.required)
        initializer = value.initializer
        overloads = (initializer,) if isinstance(initializer, FunctionValue) else initializer.overloads
        arities = {max(0, len(overload.code.params) - 1) for overload in overloads}
        return next(iter(arities)) if len(arities) == 1 else None
    return None

def _call_builtin(callee: BuiltinValue, frame: _Frame) -> None:
    """Invoke builtin during VM execution."""
    if callee.element.name.text == "call" and frame.stack:
        callable_arity = _dynamic_callable_arity(frame.stack[-1])
        if callable_arity is not None:
            overload = callee.element.definitions[0]
            try:
                (
                    args,
                    stack_count,
                    next_cycle_index,
                    next_cycle_stack_remaining,
                ) = frame.source_args(callable_arity + 1)
            except _StackUnderflow:
                pass
            else:
                implementation = overload.implementation
                assert implementation is not None
                try:
                    result = implementation(_unwrapped_args(args), callee.context)
                except _py_builtins.RuntimeError as exc:
                    raise _with_call_detail(
                        exc,
                        f"element '{callee.element.name}'",
                        args,
                    ) from exc
                result = _bind_lazy_result_owners(args, result)
                _finalize_builtin_result_ownership(args, result)
                if stack_count:
                    _release_stack_tail(
                        frame.stack,
                        stack_count,
                        callee.context.call.__self__,
                    )
                frame.cycle_index = next_cycle_index
                frame.cycle_stack_remaining = next_cycle_stack_remaining
                frame.stack.extend(result)
                return
    candidates = sorted(
        callee.element.definitions,
        key=lambda overload: len(overload.signature.params),
        reverse=True,
    )
    for overload in candidates:
        arity = len(overload.signature.params)
        try:
            (
                args,
                stack_count,
                next_cycle_index,
                next_cycle_stack_remaining,
            ) = frame.source_args(arity)
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
            frame.cycle_stack_remaining = next_cycle_stack_remaining
            frame.stack.extend(vectorized)
            return
        implementation = overload.implementation
        if implementation is None:
            continue
        try:
            result = _apply_declared_return_tags(
                implementation(_unwrapped_args(args), callee.context),
                overload.signature.returns,
            )
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
        frame.cycle_stack_remaining = next_cycle_stack_remaining
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
    vm: VirtualMachine,
    type_args: tuple[str, ...] = (),
    overload_index: int | None = None,
) -> None:
    """Invoke object constructor during VM execution."""
    if callee.initializer is None:
        arity = len(callee.required)
    else:
        initializer = _object_constructor_initializer(callee, overload_index)
        arity = len(initializer.code.params) - 1
    try:
        (
            args,
            stack_count,
            next_cycle_index,
            next_cycle_stack_remaining,
        ) = frame.source_args(arity)
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
    frame.cycle_stack_remaining = next_cycle_stack_remaining

    if callee.initializer is None:
        fields = dict(callee.defaults)
        fields.update(dict(zip(callee.required, args, strict=True)))
        value = ObjectValue(
            callee.type_name,
            fields,
            type_args,
            runtime_type=callee.runtime_type,
        )
    else:
        fields = {name: _UNINITIALIZED_OBJECT_FIELD for name in callee.fields}
        fields.update(callee.defaults)
        self_value = ObjectValue(
            callee.type_name,
            fields,
            type_args,
            runtime_type=callee.runtime_type,
        )
        try:
            result = vm.call(initializer, [self_value, *args])
        except _py_builtins.RuntimeError as exc:
            raise _with_call_detail(
                exc,
                f"constructor '{callee.type_name}'",
                args,
            ) from exc
        if len(result) != 1 or not isinstance(result[0], ObjectValue):
            raise RuntimeError(
                f"constructor '{callee.type_name}' must produce exactly one object"
            )
        value = result[0]
        if value.type_name != callee.type_name:
            raise RuntimeError(
                f"constructor '{callee.type_name}' returned {value.type_name}"
            )

    missing = [
        name
        for name in callee.fields
        if name not in value.fields
        or value.fields[name] is _UNINITIALIZED_OBJECT_FIELD
    ]
    if missing:
        error = RuntimeError(
            f"constructor '{callee.type_name}' missing fields: {', '.join(missing)}"
        )
        error.add_call_detail(f"constructor '{callee.type_name}'", args)
        raise error
    frame.stack.append(value)


def _object_constructor_initializer(
    callee: ObjectConstructorValue,
    overload_index: int | None,
) -> FunctionValue:
    """Compute object constructor initializer during VM execution."""
    initializer = callee.initializer
    if isinstance(initializer, FunctionValue):
        if overload_index not in (None, 0):
            raise RuntimeError(
                f"constructor '{callee.type_name}' has no overload {overload_index}"
            )
        return initializer
    if isinstance(initializer, OverloadedFunctionValue):
        if overload_index is None:
            if len(initializer.overloads) != 1:
                raise RuntimeError(
                    f"cannot call overloaded constructor '{callee.type_name}' "
                    "without a resolved slot"
                )
            return initializer.overloads[0]
        try:
            return initializer.overloads[overload_index]
        except IndexError as exc:
            raise RuntimeError(
                f"constructor '{callee.type_name}' has no overload {overload_index}"
            ) from exc
    raise RuntimeError(f"constructor '{callee.type_name}' has no initializer")


def _call_resolved_builtin(
    callee: BuiltinValue,
    overload: BuiltinOverload,
    frame: _Frame,
    vm: VirtualMachine,
    vectorised: bool,
    vectorised_depths: tuple[int, ...] = (),
    vectorised_target_ranks: tuple[int | None, ...] = (),
    return_collection_ranks: tuple[int | None, ...] = (),
    arity_override: int | None = None,
    consumed_override: int | None = None,
    static_values: tuple[Any, ...] = (),
    extension_reference: VectorExtensionReference | None = None,
) -> None:
    """Invoke resolved builtin during VM execution."""
    arity = (
        arity_override
        if arity_override is not None
        else len(overload.signature.params)
    )
    try:
        (
            args,
            stack_count,
            next_cycle_index,
            next_cycle_stack_remaining,
        ) = frame.source_args(arity)
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
        extension = _materialize_vector_extension(vm, extension_reference, frame)
        try:
            vectorized = _call_vectorized_resolved_builtin(
                overload,
                args,
                context,
                vectorised_depths,
                vectorised_target_ranks,
                extension,
            )
            if vectorized is None:
                raise RuntimeError(
                    _format_call_error(
                        f"element '{callee.element.name}'",
                        frame.stack,
                        _show_overload_inputs((overload,)),
                    )
                )
            ownership_args = (*args, *_extension_owned_values(extension))
            vectorized = _bind_lazy_result_owners(ownership_args, vectorized)
            vectorized = _apply_runtime_collection_ranks(
                vectorized,
                return_collection_ranks,
            )
            _finalize_builtin_result_ownership(ownership_args, vectorized)
        except _py_builtins.RuntimeError as exc:
            raise _with_call_detail(
                exc,
                f"element '{callee.element.name}'",
                args,
            ) from exc
        finally:
            _release_runtime_vector_extension(extension, vm)
        if consumed_count:
            _release_stack_tail(frame.stack, consumed_count, context.call.__self__)
        frame.cycle_index = next_cycle_index
        frame.cycle_stack_remaining = next_cycle_stack_remaining
        frame.stack.extend(vectorized)
        return
    implementation = overload.implementation
    assert implementation is not None
    try:
        result = _apply_declared_return_tags(
            implementation(_unwrapped_args(args), context),
            overload.signature.returns,
        )
        result = _apply_runtime_collection_ranks(result, return_collection_ranks)
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
    frame.cycle_stack_remaining = next_cycle_stack_remaining
    frame.stack.extend(result)


def _stack_shuffle(frame: _Frame, spec: object, vm: VirtualMachine) -> None:
    """Update stack shuffle state during VM execution."""
    mode, prestack, poststack = _stack_shuffle_spec(spec)
    arity = len(prestack)
    try:
        (
            args,
            stack_count,
            next_cycle_index,
            next_cycle_stack_remaining,
        ) = frame.source_args(arity)
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
        frame.cycle_stack_remaining = next_cycle_stack_remaining
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
    frame.cycle_stack_remaining = next_cycle_stack_remaining
    for index, (label, value) in enumerate(zip(prestack, args, strict=True)):
        if label is None:
            frame.stack.append(value)
        elif index >= stack_arg_start and label not in retained_outputs:
            _release_value(value, vm)
    frame.stack.extend(outputs)


def _stack_shuffle_spec(
    spec: object,
) -> tuple[str, tuple[str | None, ...], tuple[str, ...]]:
    """Compute stack shuffle spec during VM execution."""
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
    """Compute extract object field during VM execution."""
    try:
        value = receiver.fields[field]
    except KeyError as exc:
        raise RuntimeError(f"{receiver.type_name} has no field '{field}'") from exc
    if value is _UNINITIALIZED_OBJECT_FIELD:
        raise RuntimeError(
            f"{receiver.type_name} field '{field}' is not initialized"
        )
    retained = _retain_value(value)
    _release_value(receiver, vm)
    return retained


def _try_unwrap(stack: list[Any], vm: VirtualMachine) -> bool:
    """Return the Boolean result of try unwrap during virtual-machine execution."""
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
    """Return whether the value is none result value."""
    return value is None or (
        isinstance(value, ObjectValue)
        and value.type_name.rsplit(".", 1)[-1] == "None"
    )


def _is_error_result_value(value: Any) -> bool:
    """Return whether the value is error result value."""
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
    """Invoke vectorized builtin during VM execution."""
    if (
        overload.implementation is None
        or not overload.vectorisable
        or not any(is_list_like(arg) for arg in args)
    ):
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
    vectorised_target_ranks: tuple[int | None, ...] = (),
    extension: _RuntimeVectorExtension | None = None,
) -> tuple[Any, ...] | None:
    """Invoke vectorized resolved builtin during VM execution."""
    implementation = overload.implementation
    assert implementation is not None
    if not overload.vectorisable:
        return None

    def typed_implementation(
        item_args: tuple[Any, ...],
        item_context: RuntimeContext,
    ) -> tuple[Any, ...]:
        """Compute typed implementation during VM execution."""
        return _apply_declared_return_tags(
            implementation(_unwrapped_args(item_args), item_context),
            overload.signature.returns,
        )
    try:
        if vectorised_depths or vectorised_target_ranks:
            resolved_depths = _resolve_vectorisation_depths(
                args,
                vectorised_depths,
                vectorised_target_ranks,
            )
            return _vectorize_resolved_depths(
                typed_implementation,
                args,
                context,
                resolved_depths,
                extension,
                stop_at_zero=(
                    any(rank is not None for rank in vectorised_target_ranks)
                    or any(
                        _parameter_stops_vectorisation(param)
                        for param in overload.signature.params
                    )
                ),
            )
        return _vectorize_resolved(typed_implementation, args, context, extension)
    except _CannotVectorize:
        return None


def _vectorize(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    """Compute vectorize during VM execution."""
    vector_args = tuple(arg for arg in args if is_list_like(arg))
    if not vector_args:
        if not overload.runtime_matches(args):
            raise _CannotVectorize
        implementation = overload.implementation
        if implementation is None:
            raise _CannotVectorize
        return _apply_declared_return_tags(
            implementation(_unwrapped_args(args), context),
            overload.signature.returns,
        )
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager(overload, args, context)
    return (LazyList(_vectorize_lazy(overload, args, context)),)


def _vectorize_resolved(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    extension: _RuntimeVectorExtension | None = None,
) -> tuple[Any, ...]:
    """Vectorize resolved during VM execution."""
    vector_args = tuple(arg for arg in args if is_list_like(arg))
    if not vector_args:
        return implementation(_unwrapped_args(args), context)
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager_resolved(implementation, args, context, extension)
    return (
        LazyList(_vectorize_lazy_resolved(implementation, args, context, extension)),
    )


def _vectorize_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
    extension: _RuntimeVectorExtension | None = None,
    *,
    stop_at_zero: bool = False,
) -> tuple[Any, ...]:
    """Vectorize resolved depths during VM execution."""
    if not any(depth > 0 for depth in depths):
        if stop_at_zero:
            return implementation(_unwrapped_args(args), context)
        return _vectorize_resolved(implementation, args, context, extension)
    vector_args = tuple(
        arg for arg, depth in zip(args, depths, strict=False) if depth > 0
    )
    if not vector_args or not all(is_list_like(arg) for arg in vector_args):
        raise _CannotVectorize
    if all(is_eager_sequence(arg) for arg in vector_args):
        return _vectorize_eager_resolved_depths(
            implementation,
            args,
            context,
            depths,
            extension,
            stop_at_zero=stop_at_zero,
        )
    lazy_items = _vectorize_lazy_resolved_depths(
        implementation,
        args,
        context,
        depths,
        extension,
        stop_at_zero=stop_at_zero,
    )
    return (
        LazyList(lazy_items),
    )


def _resolve_vectorisation_depths(
    args: tuple[Any, ...],
    depths: tuple[int, ...],
    target_ranks: tuple[int | None, ...],
) -> tuple[int, ...]:
    """Resolve runtime-selected depths for minimum-rank call arguments."""
    width = len(args)
    if depths and len(depths) != width:
        raise RuntimeError("invalid vectorisation depth metadata")
    if target_ranks and len(target_ranks) != width:
        raise RuntimeError("invalid vectorisation target-rank metadata")
    fixed = depths or (0,) * width
    targets = target_ranks or (None,) * width
    resolved: list[int] = []
    for value, depth, target in zip(args, fixed, targets, strict=True):
        if target is None:
            resolved.append(depth)
            continue
        if target == 0 and not is_list_like(value):
            resolved.append(0)
            continue
        actual_rank = runtime_collection_rank(value)
        if actual_rank is None:
            raise RuntimeError(
                "cannot determine the runtime rank of a minimum-rank list "
                "for exact-rank parameter adaptation"
            )
        if actual_rank < target:
            raise RuntimeError(
                f"runtime list rank {actual_rank} is below required rank {target}"
            )
        resolved.append(actual_rank - target)
    return tuple(resolved)


def _parameter_stops_vectorisation(typ: Any) -> bool:
    """Return the Boolean result of parameter stops vectorisation during virtual-machine execution."""
    typ = normalize(typ)
    if isinstance(typ, (TaggedType, ExactType, AtomicType)):
        return _parameter_stops_vectorisation(typ.inner)
    return isinstance(typ, CollectionType)


def _vectorize_function(
    vm: VirtualMachine,
    callee: FunctionValue,
    args: tuple[Any, ...],
    depths: tuple[int, ...] = (),
    target_ranks: tuple[int | None, ...] = (),
    extension: _RuntimeVectorExtension | None = None,
) -> tuple[Any, ...]:
    """Vectorize function during VM execution."""
    def implementation(item_args: tuple[Any, ...], _context: RuntimeContext):
        """Handle implementation during VM execution."""
        return tuple(vm.call(callee, list(item_args)))

    context = RuntimeContext(
        vm.output,
        vm.call_value,
        vm.format_value,
        vm.call_value_overload,
    )
    result = (
        _vectorize_resolved_depths(
            implementation,
            args,
            context,
            _resolve_vectorisation_depths(args, depths, target_ranks),
            extension,
            stop_at_zero=True,
        )
        if depths or target_ranks
        else _vectorize_resolved(implementation, args, context, extension)
    )
    ownership_args = (*args, *_extension_owned_values(extension))
    return _bind_lazy_result_owners(ownership_args, result)


def _vectorize_eager(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    """Vectorize eager during VM execution."""
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
    extension: _RuntimeVectorExtension | None = None,
) -> tuple[Any, ...]:
    """Vectorize eager resolved during VM execution."""
    vector_lengths = tuple(len(arg) for arg in args if is_eager_sequence(arg))
    if not vector_lengths:
        raise _CannotVectorize
    if extension is None and len(set(vector_lengths)) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    result_items = []
    for index in range(max(vector_lengths)):
        item_args = tuple(
            arg[index]
            if is_eager_sequence(arg) and index < len(arg)
            else (_MISSING_VECTOR_ITEM if is_eager_sequence(arg) else arg)
            for arg in args
        )
        item_args = _extend_vector_args(item_args, extension, context)
        result_items.append(
            _vectorize_resolved(implementation, item_args, context, extension)
        )

    return _transpose_vectorized_items(result_items)


def _vectorize_eager_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
    extension: _RuntimeVectorExtension | None = None,
    *,
    stop_at_zero: bool = False,
) -> tuple[Any, ...]:
    """Vectorize eager resolved depths during VM execution."""
    vector_lengths = tuple(
        len(arg)
        for arg, depth in zip(args, depths, strict=False)
        if depth > 0 and is_eager_sequence(arg)
    )
    if not vector_lengths:
        raise _CannotVectorize
    if extension is None and len(set(vector_lengths)) != 1:
        raise RuntimeError("cannot vectorise lists with different lengths")

    item_depths = tuple(max(depth - 1, 0) for depth in depths)
    result_items = []
    for index in range(max(vector_lengths)):
        item_args = tuple(
            (
                arg[index]
                if index < len(arg)
                else _MISSING_VECTOR_ITEM
            )
            if depth > 0 and is_eager_sequence(arg)
            else arg
            for arg, depth in zip(args, depths, strict=False)
        )
        item_args = _extend_vector_args(item_args, extension, context)
        result_items.append(
            _vectorize_resolved_depths(
                implementation,
                item_args,
                context,
                item_depths,
                extension,
                stop_at_zero=stop_at_zero,
            )
        )

    return _transpose_vectorized_items(result_items)


def _vectorize_lazy(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
):
    """Vectorize lazy during VM execution."""
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
    extension: _RuntimeVectorExtension | None = None,
):
    """Vectorize lazy resolved during VM execution."""
    iterators = tuple(iter(arg) if is_list_like(arg) else None for arg in args)
    for items in zip_longest(
        *(iterator for iterator in iterators if iterator is not None),
        fillvalue=_MISSING_VECTOR_ITEM,
    ):
        if extension is None and _MISSING_VECTOR_ITEM in items:
            raise RuntimeError("cannot vectorise lists with different lengths")
        item_iter = iter(items)
        item_args = tuple(
            next(item_iter) if is_list_like(arg) else arg for arg in args
        )
        item_args = _extend_vector_args(item_args, extension, context)
        result = _vectorize_resolved(implementation, item_args, context, extension)
        if len(result) != 1:
            raise RuntimeError("lazy vectorised overload must return one value")
        yield result[0]


def _vectorize_lazy_resolved_depths(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    depths: tuple[int, ...],
    extension: _RuntimeVectorExtension | None = None,
    *,
    stop_at_zero: bool = False,
):
    """Vectorize lazy resolved depths during VM execution."""
    iterators = tuple(
        iter(arg) if depth > 0 and is_list_like(arg) else None
        for arg, depth in zip(args, depths, strict=False)
    )
    item_depths = tuple(max(depth - 1, 0) for depth in depths)
    for items in zip_longest(
        *(iterator for iterator in iterators if iterator is not None),
        fillvalue=_MISSING_VECTOR_ITEM,
    ):
        if extension is None and _MISSING_VECTOR_ITEM in items:
            raise RuntimeError("cannot vectorise lists with different lengths")
        item_iter = iter(items)
        item_args = tuple(
            next(item_iter) if depth > 0 and is_list_like(arg) else arg
            for arg, depth in zip(args, depths, strict=False)
        )
        item_args = _extend_vector_args(item_args, extension, context)
        result = _vectorize_resolved_depths(
            implementation,
            item_args,
            context,
            item_depths,
            extension,
            stop_at_zero=stop_at_zero,
        )
        if len(result) != 1:
            raise RuntimeError("lazy vectorised overload must return one value")
        yield result[0]


def _extend_vector_args(
    args: tuple[Any, ...],
    extension: _RuntimeVectorExtension | None,
    context: RuntimeContext,
) -> tuple[Any, ...]:
    """Extend vector args during VM execution."""
    missing_positions = tuple(
        index for index, value in enumerate(args) if value is _MISSING_VECTOR_ITEM
    )
    if not missing_positions:
        return args
    if extension is None:
        raise RuntimeError("cannot vectorise lists with different lengths")

    substitutions: tuple[Any, ...]
    if extension.default is not _NO_EXTENSION_DEFAULT:
        substitutions = (extension.default,) * len(missing_positions)
    elif extension.rules:
        presence = tuple(value is not _MISSING_VECTOR_ITEM for value in args)
        rule = next(
            (function for pattern, function in extension.rules if pattern == presence),
            None,
        )
        if rule is None:
            shown = ", ".join("present" if item else "missing" for item in presence)
            raise RuntimeError(f"no extend pattern matches ({shown})")
        present_args = tuple(
            value for value in args if value is not _MISSING_VECTOR_ITEM
        )
        vm = cast(VirtualMachine, context.call.__self__)
        substitutions = tuple(_call_extension_function(vm, rule, present_args))
        if len(substitutions) != len(missing_positions):
            raise RuntimeError(
                "extend pattern rule returned the wrong number of substitutions"
            )
    elif extension.selector is not None:
        selector_args = tuple(
            ObjectValue("None", {})
            if value is _MISSING_VECTOR_ITEM
            else ObjectValue("Some", {"value": value})
            for value in args
        )
        vm = cast(VirtualMachine, context.call.__self__)
        selected = _call_extension_function(vm, extension.selector, selector_args)
        if len(selected) != 1:
            raise RuntimeError("extend selector must produce exactly one value")
        substitution = _unwrap_extension_selector_result(selected[0])
        substitutions = (substitution,) * len(missing_positions)
    else:
        raise RuntimeError("invalid vector extension")

    values = list(args)
    for index, substitution in zip(
        missing_positions,
        substitutions,
        strict=True,
    ):
        values[index] = substitution
    return tuple(values)


def _unwrap_extension_selector_result(value: Any) -> Any:
    """Compute the result for unwrap extension selector during VM execution."""
    if isinstance(value, ObjectValue):
        short_name = value.type_name.rsplit(".", 1)[-1]
        if short_name == "Some" and "value" in value.fields:
            return value.fields["value"]
        if short_name == "None":
            raise RuntimeError("extend selector returned None for a missing value")
    return value


def _transpose_vectorized_items(result_items: list[tuple[Any, ...]]) -> tuple[Any, ...]:
    """Transpose vectorized items during VM execution."""
    if not result_items:
        return ([],)
    width = len(result_items[0])
    if any(len(item) != width for item in result_items):
        raise RuntimeError("vectorised overload returned inconsistent stack shapes")
    return tuple([item[position] for item in result_items] for position in range(width))


class _CannotVectorize(Exception):
    """Internal signal for trying the next runtime overload."""


def _pop(stack: list[Any], context: str) -> Any:
    """Compute pop during VM execution."""
    if not stack:
        raise RuntimeError(f"stack underflow during {context}")
    return stack.pop()


def _pop_many(stack: list[Any], count: int) -> list[Any]:
    """Pop many during VM execution."""
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
    """Compute build string during VM execution."""
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
    """Load name during VM execution."""
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
    """Load element name during VM execution."""
    if name in globals_:
        return globals_[name]
    if name in locals_:
        return locals_[name]
    raise RuntimeError(f"undefined name '{name}'")


def _constant(value: Any) -> Any:
    """Compute constant during VM execution."""
    return value


def _truthy(value: Any) -> bool:
    """Return the Boolean result of truthy during virtual-machine execution."""
    value = unwrap_runtime_value(value)
    return value != 0 and value is not None


def _matches_type_pattern(value: Any, pattern: str) -> bool:
    """Return whether the value matches type pattern."""
    value = unwrap_runtime_value(value)
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
    """Return whether the value matches cast type."""
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
    """Return whether the value matches collection cast."""
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
    """Return the Boolean result of bind match name during virtual-machine execution."""
    if name in bindings:
        return bindings[name] == value
    bindings[name] = value
    return True


def _is_rest_pattern(pattern: object) -> bool:
    """Return whether the value is rest pattern."""
    return isinstance(pattern, tuple) and bool(pattern) and pattern[0] == "rest"


def _pop_index_values(
    stack: list[Any],
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
) -> list[tuple[bool, Any, Any, Any]]:
    """Pop index values during VM execution."""
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
    """Index value count during VM execution."""
    return sum(
        has_start + has_stop + has_step
        for _, has_start, has_stop, has_step in spec[0]
    )


def _index_receiver(frame: _Frame) -> Any:
    """Index receiver during VM execution."""
    if frame.stack:
        return frame.stack.pop()
    try:
        (
            args,
            stack_count,
            next_cycle_index,
            next_cycle_stack_remaining,
        ) = frame.source_args(1)
    except _StackUnderflow as exc:
        raise RuntimeError("stack underflow during indexing") from exc
    if stack_count:
        del frame.stack[-stack_count:]
    frame.cycle_index = next_cycle_index
    frame.cycle_stack_remaining = next_cycle_stack_remaining
    return args[0]


def _get_index(
    receiver: Any,
    spec: tuple[tuple[tuple[int, int, int, int], ...], int],
    selectors: list[tuple[bool, Any, Any, Any]],
) -> Any:
    """Find the index for get during VM execution."""
    if len(selectors) > 1 and all(not item[0] for item in selectors):
        if is_list_like(receiver) and not is_eager_sequence(receiver):
            return _index_many_lazy(receiver, tuple(item[1] for item in selectors))
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
    """Update index during VM execution."""
    if len(selectors) == 1 and selectors[0][0]:
        _, start, stop, step = selectors[0]
        return _set_slice_value(receiver, start, stop, step, value)
    if len(selectors) != 1:
        raise RuntimeError("indexed assignment requires one non-slice index")
    return _set_index_path(receiver, selectors[0][1], value)


def _index_path(receiver: Any, index: Any) -> Any:
    """Index path during VM execution."""
    if _is_path(index):
        result = receiver
        for item in index:
            result = _index_one(result, item)
        return result
    return _index_one(receiver, index)


def _index_many_lazy(receiver: Any, indices: tuple[Any, ...]) -> list[Any]:
    """Index many lazy during VM execution."""
    requests = tuple(_lazy_index_request(index) for index in indices)
    targets = sorted({target for target, _ in requests})
    results: dict[int, Any] = {}
    target_iter = iter(targets)
    try:
        next_target = next(target_iter)
    except StopIteration:
        return []
    for offset, item in enumerate(receiver):
        if offset != next_target:
            continue
        results[offset] = item
        try:
            next_target = next(target_iter)
        except StopIteration:
            break
    missing = [target for target in targets if target not in results]
    if missing:
        missing_text = ", ".join(str(target) for target in missing)
        raise PanicSignal(
            _fault_object("IndexFault", f"index out of range: {missing_text}")
        )
    return [
        _index_path(results[target], tail) if tail else results[target]
        for target, tail in requests
    ]


def _lazy_index_request(index: Any) -> tuple[int, list[Any]]:
    """Compute lazy index request during VM execution."""
    tail: list[Any] = []
    target = index
    if _is_path(index):
        if not index:
            raise RuntimeError("empty index path is invalid")
        target = index[0]
        tail = list(index[1:])
    if not isinstance(target, Decimal):
        raise RuntimeError("lazy list indexing requires a numeric index")
    target_int = _int_index(target)
    if target_int < 0:
        raise RuntimeError("lazy list indexing does not support negative indices")
    return target_int, tail


def _index_one(receiver: Any, index: Any) -> Any:
    """Index one during VM execution."""
    if isinstance(receiver, dict):
        try:
            return receiver[index]
        except KeyError as exc:
            raise PanicSignal(
                _fault_object(
                    "KeyFault",
                    f"dictionary has no key {_format_value(index)}",
                )
            ) from exc
    if isinstance(receiver, tuple) or isinstance(receiver, str) or is_eager_sequence(
        receiver
    ):
        target = _int_index(index)
        try:
            return receiver[target]
        except IndexError as exc:
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            ) from exc
    if is_list_like(receiver):
        if not isinstance(index, Decimal):
            raise RuntimeError("lazy list indexing requires a numeric index")
        target = _int_index(index)
        if target < 0:
            raise RuntimeError("lazy list indexing does not support negative indices")
        for offset, item in enumerate(receiver):
            if offset == target:
                return item
        raise PanicSignal(
            _fault_object("IndexFault", _index_fault_message(target))
        )
    raise RuntimeError("value is not indexable")


def _slice_value(receiver: Any, start: Any, stop: Any, step: Any) -> Any:
    """Slice value during VM execution."""
    if _is_path(start) or _is_path(stop):
        return _slice_path(receiver, start, stop, step)
    if not (is_eager_sequence(receiver) or isinstance(receiver, str)):
        if is_list_like(receiver):
            return _slice_lazy(receiver, start, stop, step)
        raise RuntimeError("slicing requires a list or string")
    step_int = 1 if step is None else _int_index(step)
    if step_int == 0:
        raise RuntimeError("slice step cannot be 0")
    length = len(receiver)
    start_int = 0 if start is None else _normal_index(_int_index(start), length)
    stop_int = length - 1 if stop is None else _normal_index(_int_index(stop), length)
    python_stop = stop_int + (1 if step_int > 0 else -1)
    sliced = receiver[start_int:python_stop:step_int]
    return "".join(sliced) if isinstance(receiver, str) else list(sliced)


def _slice_lazy(receiver: Any, start: Any, stop: Any, step: Any) -> LazyList:
    """Slice lazy during VM execution."""
    step_int = 1 if step is None else _int_index(step)
    if step_int <= 0:
        raise RuntimeError("lazy list slicing requires a positive step")
    start_int = 0 if start is None else _int_index(start)
    if start_int < 0:
        raise RuntimeError("lazy list slicing does not support negative start")
    stop_int = None if stop is None else _int_index(stop)
    if stop_int is not None and stop_int < 0:
        raise RuntimeError("lazy list slicing does not support negative stop")
    python_stop = None if stop_int is None else stop_int + 1
    return LazyList(islice(iter(receiver), start_int, python_stop, step_int))


def _slice_path(receiver: Any, start: Any, stop: Any, step: Any) -> Any:
    """Slice path during VM execution."""
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
    """Update index path during VM execution."""
    if _is_path(index):
        if not index:
            return value
        head, *tail = index
        current = _index_one(receiver, head)
        return _set_index_one(receiver, head, _set_index_path(current, tail, value))
    return _set_index_one(receiver, index, value)


def _set_slice_value(
    receiver: Any,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
) -> Any:
    """Update slice value during VM execution."""
    if _is_path(start) or _is_path(stop):
        raise RuntimeError("multidimensional slice assignment is not implemented")
    if isinstance(receiver, str):
        return _set_string_slice(receiver, start, stop, step, value)
    if is_eager_sequence(receiver):
        return _set_eager_slice(receiver, start, stop, step, value)
    if is_list_like(receiver):
        return _set_lazy_slice(receiver, start, stop, step, value)
    raise RuntimeError("value is not slice-assignable")


def _set_eager_slice(
    receiver: Any,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
) -> list[Any]:
    """Update eager slice during VM execution."""
    indexes = _eager_slice_indexes(len(receiver), start, stop, step)
    replacements = _slice_replacements(value, len(indexes))
    updated = list(receiver)
    for index, replacement in zip(indexes, replacements, strict=True):
        updated[index] = replacement
    return updated


def _set_string_slice(
    receiver: str,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
) -> str:
    """Update string slice during VM execution."""
    if not isinstance(value, str):
        raise RuntimeError("string slice assignment requires a string value")
    indexes = _eager_slice_indexes(len(receiver), start, stop, step)
    replacements = list(value)
    if len(replacements) != len(indexes):
        raise RuntimeError("slice assignment replacement length mismatch")
    updated = list(receiver)
    for index, replacement in zip(indexes, replacements, strict=True):
        updated[index] = replacement
    return "".join(updated)


def _set_lazy_slice(
    receiver: Any,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
) -> LazyList:
    """Update lazy slice during VM execution."""
    step_int = 1 if step is None else _int_index(step)
    if step_int <= 0:
        raise RuntimeError("lazy list slice assignment requires a positive step")
    start_int = 0 if start is None else _int_index(start)
    if start_int < 0:
        raise RuntimeError("lazy list slice assignment does not support negative start")
    stop_int = None if stop is None else _int_index(stop)
    if stop_int is not None and stop_int < 0:
        raise RuntimeError("lazy list slice assignment does not support negative stop")
    replacement_iter = iter(value) if is_list_like(value) else None

    def updated_items():
        """Collect the items for updated during VM execution."""
        for offset, item in enumerate(receiver):
            in_slice = offset >= start_int and (stop_int is None or offset <= stop_int)
            if in_slice and (offset - start_int) % step_int == 0:
                if replacement_iter is None:
                    yield value
                else:
                    try:
                        yield next(replacement_iter)
                    except StopIteration as exc:
                        raise RuntimeError(
                            "slice assignment replacement length mismatch"
                        ) from exc
            else:
                yield item
        if replacement_iter is not None:
            try:
                next(replacement_iter)
            except StopIteration:
                return
            raise RuntimeError("slice assignment replacement length mismatch")

    return LazyList(updated_items())


def _eager_slice_indexes(
    length: int,
    start: Any,
    stop: Any,
    step: Any,
) -> list[int]:
    """Compute eager slice indexes during VM execution."""
    step_int = 1 if step is None else _int_index(step)
    if step_int == 0:
        raise RuntimeError("slice step cannot be 0")
    start_int = 0 if start is None else _normal_index(_int_index(start), length)
    stop_int = length - 1 if stop is None else _normal_index(_int_index(stop), length)
    python_stop = stop_int + (1 if step_int > 0 else -1)
    return list(range(length))[start_int:python_stop:step_int]


def _slice_replacements(value: Any, count: int) -> list[Any]:
    """Slice replacements during VM execution."""
    if is_list_like(value):
        replacements = list(value)
        if len(replacements) != count:
            raise RuntimeError("slice assignment replacement length mismatch")
        return replacements
    return [value for _ in range(count)]


def _set_index_one(receiver: Any, index: Any, value: Any) -> Any:
    """Update index one during VM execution."""
    if isinstance(receiver, dict):
        updated = dict(receiver)
        updated[index] = value
        return updated
    if isinstance(receiver, tuple):
        target = _int_index(index)
        updated = list(receiver)
        try:
            updated[target] = value
        except IndexError as exc:
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            ) from exc
        return tuple(updated)
    if isinstance(receiver, str):
        if not isinstance(value, str) or len(value) != 1:
            raise RuntimeError("string indexed assignment requires one character")
        target = _int_index(index)
        if not -len(receiver) <= target < len(receiver):
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            )
        normalized_target = _normal_index(target, len(receiver))
        return (
            receiver[:normalized_target]
            + value
            + receiver[normalized_target + 1 :]
        )
    if is_eager_sequence(receiver):
        target = _int_index(index)
        updated = list(receiver)
        try:
            updated[target] = value
        except IndexError as exc:
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            ) from exc
        return updated
    raise RuntimeError("value is not index-assignable")


def _is_path(value: Any) -> bool:
    """Return whether the value is path."""
    return isinstance(value, list)


def _int_index(value: Any) -> int:
    """Find the index for int during VM execution."""
    value = unwrap_runtime_value(value)
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, int):
        return value
    raise RuntimeError("index must be an integer")


def _normal_index(index: int, length: int) -> int:
    """Find the index for normal during VM execution."""
    return index + length if index < 0 else index


def _index_fault_message(index: int, length: int | None = None) -> str:
    """Index fault message during VM execution."""
    if length is None:
        return f"index {index} is out of range"
    return f"index {index} is out of range for length {length}"


def _field_instruction_arg(argument: object) -> tuple[str, bool]:
    """Normalize ordinary and optional-safe field bytecode arguments."""
    if isinstance(argument, str):
        return argument, False
    if (
        isinstance(argument, tuple)
        and len(argument) == 2
        and isinstance(argument[0], str)
        and argument[1] in {True, 1, "optional"}
    ):
        return argument[0], True
    raise RuntimeError(f"invalid field instruction argument {argument!r}")


def _optional_runtime_kind(value: Any) -> str | None:
    """Return ``some``/``none`` for runtime optional wrappers."""
    if value is None:
        return "none"
    if not isinstance(value, ObjectValue):
        return None
    short_name = value.type_name.rsplit(".", 1)[-1]
    if short_name == "None":
        return "none"
    if short_name == "Some" and "value" in value.fields:
        return "some"
    return None


def _optional_safe_get_field(
    receiver: Any,
    field: str,
    vm: VirtualMachine,
) -> Any:
    """Read a member through ``Some`` and propagate ``None``."""
    if is_list_like(receiver):
        if is_eager_sequence(receiver):
            return [
                _optional_safe_get_field(item, field, vm)
                for item in receiver
            ]
        return LazyList(
            _optional_safe_get_field(item, field, vm)
            for item in receiver
        )

    kind = _optional_runtime_kind(receiver)
    if kind == "none":
        return receiver
    if kind != "some" or not isinstance(receiver, ObjectValue):
        raise RuntimeError("optional-safe field access requires Some or None")

    payload = _retain_value(receiver.fields["value"])
    _release_value(receiver, vm)
    if isinstance(payload, ObjectValue):
        result = _extract_object_field(payload, field, vm)
    else:
        result = _get_field(payload, field)
    if _optional_runtime_kind(result) is not None:
        return result
    return ObjectValue("Some", {"value": result})


def _optional_safe_set_field(
    receiver: Any,
    field: str,
    value: Any,
    vm: VirtualMachine,
) -> Any:
    """Write a member through ``Some`` or cancel the write for ``None``."""
    kind = _optional_runtime_kind(receiver)
    if kind == "none":
        _release_value(value, vm)
        return receiver
    if kind != "some" or not isinstance(receiver, ObjectValue):
        raise RuntimeError("optional-safe field assignment requires Some or None")

    payload = receiver.fields["value"]
    updated = _set_field(payload, field, value)
    return ObjectValue("Some", {"value": updated})


def _get_field(receiver: Any, field: str) -> Any:
    """Compute get field during VM execution."""
    receiver = unwrap_runtime_value(receiver)
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
    """Update field during VM execution."""
    receiver = unwrap_runtime_value(receiver)
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
    """Return the canonical name for function during VM execution."""
    return "function" if code.name is None else code.name


def _with_call_detail(
    exc: _py_builtins.RuntimeError,
    target: str,
    args: tuple[Any, ...],
) -> RuntimeError:
    """Compute with call detail during VM execution."""
    error = exc if isinstance(exc, RuntimeError) else RuntimeError(exc)
    error.add_call_detail(target, args)
    return error


def _format_call_error(
    target: str,
    stack: list[Any],
    expected_inputs: list[str],
) -> str:
    """Format call error during VM execution."""
    lines = [f"cannot call {target} with current stack"]
    lines.append(f"stack: {_format_stack(stack)}")
    lines.append(f"stack types: {_format_stack_types(stack)}")
    if expected_inputs:
        lines.append("attempted input shapes:")
        lines.extend(f"  - {shape}" for shape in expected_inputs)
    return "\n".join(lines)


def _format_execution_context(context: _ExecutionContext) -> str:
    """Format execution context during VM execution."""
    lines = [
        f"{context.function_name} ip {context.ip}: "
        f"{_format_instruction(context.instruction)}"
    ]
    lines.append(f"    stack: {_format_stack(list(context.stack))}")
    lines.append(f"    stack types: {_format_stack_types(list(context.stack))}")
    return "\n".join(lines)


def _format_instruction(instruction: object) -> str:
    """Format instruction during VM execution."""
    if not hasattr(instruction, "op"):
        return repr(instruction)
    op = instruction.op
    name = op.value if isinstance(op, OpCode) else str(op)
    arg = getattr(instruction, "arg", None)
    if arg is None:
        return name
    return f"{name} {_format_instruction_arg(arg)}"


def _format_instruction_arg(arg: object) -> str:
    """Format instruction arg during VM execution."""
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
    """Compute show overload inputs during VM execution."""
    return [
        "(" + ", ".join(str(param) for param in overload.signature.params) + ")"
        for overload in overloads
    ]


def _object_type_name(value: ObjectValue) -> str:
    """Return the canonical name for object type during VM execution."""
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"


def _format_stack(stack: list[Any]) -> str:
    """Format stack during VM execution."""
    if not stack:
        return "[]"
    return "[" + ", ".join(_format_value(value) for value in stack) + "]"


def _format_stack_types(stack: list[Any]) -> str:
    """Format stack types during VM execution."""
    if not stack:
        return "[]"
    return "[" + ", ".join(_runtime_type_name(value) for value in stack) + "]"


def _format_value(value: Any) -> str:
    """Format value during VM execution."""
    compact = _compact_diagnostic_value(value)
    if compact is not None:
        return compact
    return format_runtime_value(
        value,
        quote_strings=True,
        lazy_preview_limit=DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    )


def _compact_diagnostic_value(value: Any) -> str | None:
    """Compute compact diagnostic value during VM execution."""
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
    """Compute compact sequence during VM execution."""
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
    """Compute compact mapping during VM execution."""
    items = []
    for index, (key, item) in enumerate(value.items()):
        if index >= DIAGNOSTIC_LIST_PREVIEW_LIMIT:
            items.append("...")
            break
        items.append(f"{_format_value(key)}: {_format_value(item)}")
    return "{" + ", ".join(items) + "}"


def _string_value(value: Any) -> str:
    """Compute string value during VM execution."""
    return format_runtime_value(value)


def _runtime_type_name(value: Any) -> str:
    """Return the canonical name for runtime type during VM execution."""
    value = unwrap_runtime_value(value)
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


def _unwrapped_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    """Compute unwrapped args during VM execution."""
    return tuple(unwrap_runtime_value(arg) for arg in args)


def _apply_declared_return_tags(
    values: tuple[Any, ...],
    types: tuple[Any, ...],
) -> tuple[Any, ...]:
    """Apply declared return tags during VM execution."""
    for typ in types:
        if isinstance(typ, TaggedType):
            break
    else:
        return values
    tag_sets = tuple(_declared_runtime_tags(typ) for typ in types)
    return _apply_runtime_return_tags(values, tag_sets)


def _declared_runtime_tags(typ: Any) -> tuple[DataTag, ...]:
    """Compute declared runtime tags during VM execution."""
    if not isinstance(typ, TaggedType):
        return ()
    typ = normalize(typ)
    if isinstance(typ, TaggedType):
        return tuple(sorted(tag for tag in typ.tags if tag.depth == 0))
    return ()


def _apply_runtime_return_tags(
    values: tuple[Any, ...] | list[Any],
    tag_sets: tuple[tuple[DataTag, ...], ...],
) -> tuple[Any, ...]:
    """Apply runtime return tags during VM execution."""
    if len(values) != len(tag_sets):
        return tuple(values)
    for tags in tag_sets:
        if tags:
            break
    else:
        return values if isinstance(values, tuple) else tuple(values)
    return tuple(
        update_runtime_tags(
            value,
            add=tuple(tag for tag in tags if not tag.absent),
            remove=tuple(tag for tag in tags if tag.absent),
        )
        for value, tags in zip(values, tag_sets, strict=True)
    )


def _apply_runtime_collection_ranks(
    values: tuple[Any, ...] | list[Any],
    ranks: tuple[int | None, ...],
) -> tuple[Any, ...]:
    """Apply runtime collection ranks during VM execution."""
    if len(values) != len(ranks):
        return tuple(values)
    for rank in ranks:
        if rank is not None:
            break
    else:
        return values if isinstance(values, tuple) else tuple(values)
    return tuple(
        with_runtime_collection_rank(value, rank)
        for value, rank in zip(values, ranks, strict=True)
    )
