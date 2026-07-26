"""Bytecode interpreter for Valiance's stack runtime."""

from __future__ import annotations

import builtins as _py_builtins
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from functools import partial
from itertools import islice, zip_longest
from typing import Any, cast

from valiance.elements.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    runtime_elements,
)
from valiance.runtime.bytecode import (
    FunctionCode,
    FunctionSetCode,
    IndexOperationSpec,
    IndexSelectorSpec,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
    decode_stack_shuffle_spec,
)
from valiance.runtime.runtime_values import (
    DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    DictValue,
    LazyList,
    PlannedLazyList,
    ListValue,
    RuntimeNumber,
    RecordValue,
    ObjectRuntimeType,
    ObjectValue,
    PanicSignal,
    TaggedValue,
    format_runtime_value,
    is_eager_sequence,
    is_list_like,
    object_type_name,
    runtime_collection_rank,
    runtime_value_tags,
    unwrap_runtime_value,
    update_runtime_tags,
    with_runtime_collection_rank,
)
from valiance.elements.stdlib_native import runtime_stdlib_elements
from valiance.analysis.contracts.where_clauses import MAX_COMPILE_TIME_RANK
from valiance.vtypes import (
    ExactType,
    CollectionType,
    DataTag,
    NoVecType,
    RankVariable,
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


_stack_shuffle_spec = partial(decode_stack_shuffle_spec, error_type=RuntimeError)


@dataclass(frozen=True, slots=True)
class _ExecutionContext:
    function_name: str
    ip: int
    instruction: object
    stack: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class PreparedCall:
    """Reusable fixed-shape execution plan for one higher-order callable."""

    arity: int
    multiplicity: int
    strategy: str
    implementation: Callable[..., tuple[Any, ...]] = field(repr=False)
    parameter_ranks: tuple[int | None, ...] = ()

    def __call__(self, *args: Any) -> tuple[Any, ...]:
        """Invoke this prepared plan after validating its fixed argument shape."""
        if len(args) != self.arity:
            raise RuntimeError(
                f"prepared callable expected {self.arity} arguments, got {len(args)}"
            )
        return self.invoke_checked(args)

    def invoke_checked(self, args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Invoke pre-grouped arguments and validate the result multiplicity."""
        result = self.implementation(*args)
        if len(result) != self.multiplicity:
            raise RuntimeError(
                f"prepared callable expected {self.multiplicity} results, "
                f"got {len(result)}"
            )
        return result

    def invoke_proven(self, args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Invoke at a call site whose fixed input/output shape is already proved."""
        return self.implementation(*args)

    def invoke0(self) -> tuple[Any, ...]:
        """Invoke a proved niladic plan without constructing an argument tuple."""
        return self.implementation()

    def invoke1(self, value: Any) -> tuple[Any, ...]:
        """Invoke a proved unary plan without variadic argument collection."""
        return self.implementation(value)

    def invoke2(self, left: Any, right: Any) -> tuple[Any, ...]:
        """Invoke a proved binary plan without variadic argument collection."""
        return self.implementation(left, right)


@dataclass(frozen=True, slots=True)
class ScalarKernel:
    """Uniform scalar execution kernel consumed by vectorisation machinery."""

    arity: int
    multiplicity: int
    invoke: Callable[[tuple[Any, ...]], tuple[Any, ...]] = field(repr=False)

    @classmethod
    def from_prepared(cls, prepared: PreparedCall) -> ScalarKernel:
        """Adapt a prepared function call into a vector scalar kernel."""
        if prepared.arity == 1:
            return cls(
                1,
                prepared.multiplicity,
                lambda args: prepared.invoke1(args[0]),
            )
        if prepared.arity == 2:
            return cls(
                2,
                prepared.multiplicity,
                lambda args: prepared.invoke2(args[0], args[1]),
            )
        return cls(
            prepared.arity,
            prepared.multiplicity,
            prepared.invoke_proven,
        )


@dataclass(slots=True)
class FunctionValue:
    """A function closure."""

    code: FunctionCode
    globals: dict[str, Any]
    owned_names: frozenset[str] = frozenset()
    refcount: int = 1
    direct_leaf: bool | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    predicate_test: Callable[[Any], bool] | None | bool = field(
        default=None,
        repr=False,
        compare=False,
    )
    prepared_calls: dict[tuple[int, int], PreparedCall] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    def __repr__(self) -> str:
        """Return a developer-facing representation of this function value."""
        return f"<{_function_name(self.code)}/{len(self.code.params)}>"

    __str__ = __repr__


_NO_EXTENSION_DEFAULT = object()
_MISSING_VECTOR_ITEM = object()
_UNINITIALIZED_OBJECT_FIELD = object()
_MISSING_NAME = object()
_SCALAR_RUNTIME_TYPES = (RuntimeNumber, str, int, bool, type(None))


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
    """A built-in element implementation with cached dispatch order."""

    element: BuiltinElement
    context: RuntimeContext
    candidates: tuple[BuiltinOverload, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Cache dynamic overload order instead of sorting on every call."""
        object.__setattr__(
            self,
            "candidates",
            tuple(
                sorted(
                    self.element.definitions,
                    key=lambda overload: len(overload.signature.params),
                    reverse=True,
                )
            ),
        )

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


@dataclass(frozen=True, slots=True)
class _FunctionCallRequest:
    """A suspended user-function call executed by the VM trampoline."""

    callee: FunctionValue
    args: tuple[Any, ...]
    target: str
    release_after: FunctionValue | OverloadedFunctionValue | None = None
    isolate_captures: bool = True
    return_tags: tuple[tuple[DataTag, ...], ...] = ()
    return_tag_specs: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class _BorrowedValue:
    """A non-owning assignment receiver reference carried on the VM stack."""

    value: Any


@dataclass(slots=True)
class _Frame:
    stack: list[Any]
    locals: dict[str, Any]
    globals: dict[str, Any]
    cycle_values: tuple[Any, ...] = ()
    cycle_index: int = 0
    cycle_stack_remaining: int = 0
    cycle_from_top: bool = False
    retained_locals: frozenset[str] = frozenset()
    is_global_scope: bool = False
    panic_handlers: list[_PanicHandler] = field(default_factory=list)
    cycle_scopes: list[tuple[tuple[Any, ...], int, int, bool]] = field(default_factory=list)
    isolated_stacks: list[list[Any]] = field(default_factory=list)

    def source_args(
        self,
        arity: int,
    ) -> tuple[tuple[Any, ...], int, int, int]:
        """Preview arguments from physical values and the conceptual input stack."""
        if arity == 0:
            return (), 0, self.cycle_index, self.cycle_stack_remaining

        stack_length = len(self.stack)
        if stack_length >= arity:
            return (
                tuple(self.stack[-arity:]),
                arity,
                self.cycle_index,
                self.cycle_stack_remaining,
            )

        stack_count = stack_length
        stack_args = tuple(self.stack) if stack_count else ()
        missing = arity - stack_count
        if missing and not self.cycle_values:
            raise _StackUnderflow

        cycle_len = len(self.cycle_values)
        if not self.cycle_from_top:
            cycle_args = tuple(
                self.cycle_values[(self.cycle_index + index) % cycle_len]
                for index in range(missing)
            )
            return (
                cycle_args + stack_args,
                stack_count,
                (self.cycle_index + missing) % cycle_len,
                self.cycle_stack_remaining,
            )

        initial_count = min(self.cycle_stack_remaining, missing)
        initial_start = self.cycle_stack_remaining - initial_count
        initial_args = self.cycle_values[initial_start:self.cycle_stack_remaining]
        cyclic_count = missing - initial_count
        cyclic_popped = tuple(
            self.cycle_values[(-1 - self.cycle_index - offset) % cycle_len]
            for offset in range(cyclic_count)
        )
        cyclic_args = tuple(reversed(cyclic_popped))
        next_cycle_index = (self.cycle_index + cyclic_count) % cycle_len
        return (
            cyclic_args + initial_args + stack_args,
            stack_count,
            next_cycle_index,
            initial_start,
        )



@dataclass(slots=True)
class _Activation:
    """One resumable Valiance bytecode frame on the VM activation stack."""

    code: FunctionCode
    frame: _Frame
    ip: int = 0
    pending_call: _FunctionCallRequest | None = None


class _StackUnderflow(Exception):
    """Internal signal for trying another runtime overload shape."""


@dataclass(frozen=True, slots=True)
class _LoopBreak(Exception):
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _FunctionReturn(Exception):
    values: tuple[Any, ...]


@dataclass(slots=True)
class OptimizationStats:
    """Optional debug counters for prepared runtime execution decisions."""

    counters: Counter[str] = field(default_factory=Counter)

    def increment(self, name: str) -> None:
        """Record one optimisation event."""
        self.counters[name] += 1

    def snapshot(self) -> dict[str, int]:
        """Return an immutable-style copy suitable for diagnostics and tests."""
        return dict(self.counters)


class VirtualMachine:
    """A small stack-based bytecode interpreter."""

    def __init__(
        self,
        *,
        output: Callable[[str], None] | None = None,
        list_preview_limit: int | None = None,
        collect_optimization_stats: bool = False,
    ) -> None:
        """Initialize this virtual machine."""
        self.output = (lambda value: print(value, end="")) if output is None else output
        self.format_value = lambda value: format_runtime_value(
            value,
            lazy_preview_limit=list_preview_limit,
        )
        self.tag_parents: dict[str, str] = {}
        self.optimization_stats = (
            OptimizationStats() if collect_optimization_stats else None
        )
        self._resolved_builtin_cache: dict[
            int,
            tuple[ResolvedElementReference, BuiltinValue, BuiltinOverload],
        ] = {}
        self.globals = {
            name: BuiltinValue(
                element,
                RuntimeContext(
                    self.output,
                    self.call_value,
                    self.format_value,
                    self.call_value_overload,
                    test_predicate=self.test_predicate,
                    prepare_call=self.prepare_call,
                ),
            )
            for name, element in (
                runtime_elements() | runtime_stdlib_elements()
            ).items()
        }

    def run(self, program: Program) -> list[Any]:
        """Execute a compiled program and return the final stack."""
        try:
            self.tag_parents = _validated_tag_parent_mapping(program.tag_parents)
            return self.call(FunctionValue(program.main, self.globals), [])
        except PanicSignal as exc:
            raise RuntimeError(f"uncaught panic: {_format_value(exc.value)}") from exc
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"invalid bytecode: {exc}") from exc

    def call(
        self,
        function: FunctionValue,
        args: list[Any] | tuple[Any, ...],
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
        return_tag_specs = _resolve_static_rank_variables(
            function.code.return_tag_specs, locals_
        )
        result = self.execute(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
            retained_locals,
        )
        ranked = (
            _apply_runtime_collection_ranks(
                result,
                function.code.return_collection_ranks,
            )
            if function.code.return_collection_ranks
            else result
        )
        tagged = (
            _canonicalize_runtime_return_tags(
                ranked,
                function.code.return_tags,
                self.tag_parents,
            )
            if function.code.return_tags
            else ranked
        )
        contracted = (
            _canonicalize_runtime_tag_contracts(
                tagged,
                return_tag_specs,
                self.tag_parents,
            )
            if return_tag_specs
            else tagged
        )
        return list(contracted)

    def prepare_call(
        self,
        value: Any,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall:
        """Prepare and cache a fixed-shape callable for repeated higher-order use."""
        if arity < 0 or multiplicity < 0:
            raise RuntimeError("prepared call shape cannot be negative")
        key = (arity, multiplicity)
        if isinstance(value, FunctionValue):
            cached = value.prepared_calls.get(key)
            if cached is not None:
                if self.optimization_stats is not None:
                    self.optimization_stats.increment("prepared.reused")
                    self.optimization_stats.increment(
                        f"prepared.strategy.{cached.strategy}"
                    )
                return cached
            builders = (
                self._prepare_constant_call,
                self._prepare_identity_call,
                self._prepare_resolved_builtin_call,
                self._prepare_straight_line_call,
                self._prepare_symbolic_match_call,
                self._prepare_match_dispatch_call,
                self._prepare_direct_leaf_call,
            )
            for builder in builders:
                plan = builder(value, arity, multiplicity)
                if plan is not None:
                    if not plan.parameter_ranks:
                        plan = replace(
                            plan,
                            parameter_ranks=value.code.param_collection_ranks,
                        )
                    value.prepared_calls[key] = plan
                    if self.optimization_stats is not None:
                        self.optimization_stats.increment("prepared.created")
                        self.optimization_stats.increment(
                            f"prepared.strategy.{plan.strategy}"
                        )
                    return plan

        def invoke_general(*args: Any) -> tuple[Any, ...]:
            """Invoke through the fully general overload/vectorisation path."""
            return tuple(self.call_value(value, list(args)))

        plan = PreparedCall(arity, multiplicity, "general", invoke_general)
        if self.optimization_stats is not None:
            self.optimization_stats.increment("prepared.created")
            self.optimization_stats.increment("prepared.strategy.general")
        return plan

    def _prepare_constant_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Prepare a pure niladic scalar function as a reusable constant result."""
        if (
            arity != 0
            or value.code.params
            or value.code.accepts_stack_inputs
            or value.code.element_tags
            or _prepared_call_needs_return_contracts(value.code)
        ):
            return None
        instructions = value.code.instructions
        if (
            len(instructions) != multiplicity + 1
            or instructions[-1].op is not OpCode.RETURN
            or any(item.op is not OpCode.PUSH_CONST for item in instructions[:-1])
        ):
            return None
        result = tuple(item.arg for item in instructions[:-1])
        if not all(isinstance(item, _SCALAR_RUNTIME_TYPES) for item in result):
            return None

        def invoke_constant() -> tuple[Any, ...]:
            """Return the prepared immutable scalar result."""
            return result

        return PreparedCall(arity, multiplicity, "constant", invoke_constant)

    def _prepare_identity_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Prepare a scalar unary identity function without entering the VM."""
        if (
            arity != 1
            or multiplicity != 1
            or len(value.code.params) != 1
            or value.code.accepts_stack_inputs
            or value.code.element_tags
            or _prepared_call_needs_return_contracts(value.code)
        ):
            return None
        instructions = value.code.instructions
        if not (
            len(instructions) == 2
            and instructions[0].op is OpCode.LOAD_VAR
            and instructions[0].arg == value.code.params[0]
            and instructions[1].op is OpCode.RETURN
        ):
            return None

        def invoke_identity(argument: Any) -> tuple[Any, ...]:
            """Return the supplied scalar argument unchanged."""
            return (argument,)

        return PreparedCall(arity, multiplicity, "identity", invoke_identity)

    def _prepare_symbolic_match_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Compile a pure expression followed by a literal-pattern decision tree."""
        if (
            arity != 1
            or multiplicity != 1
            or len(value.code.params) != 1
            or value.code.accepts_stack_inputs
            or value.code.element_tags
        ):
            return None
        instructions = value.code.instructions
        source_index = next(
            (
                index
                for index, instruction in enumerate(instructions)
                if instruction.op is OpCode.SOURCE_ARGS
            ),
            None,
        )
        if source_index is None or source_index == 0:
            return None
        stack: list[object] = []
        locals_: dict[str, object] = {value.code.params[0]: ("arg", 0)}
        for instruction in instructions[:source_index]:
            op = instruction.op
            if op is OpCode.PUSH_CONST:
                stack.append(("const", instruction.arg))
            elif op in {OpCode.LOAD_VAR, OpCode.LOAD_VAR_BORROW}:
                expression = locals_.get(instruction.arg)
                if expression is None:
                    return None
                stack.append(expression)
            elif op is OpCode.STORE_VAR:
                if not stack:
                    return None
                locals_[instruction.arg] = stack.pop()
            elif op in {OpCode.CYCLE_BEGIN, OpCode.CYCLE_END}:
                continue
            elif op is OpCode.GET_INDEX:
                if len(stack) < 2:
                    return None
                index_expression = stack.pop()
                receiver = stack.pop()
                stack.append(("index", receiver, index_expression))
            elif op is OpCode.BUILD_LIST:
                count = (
                    instruction.arg[0]
                    if isinstance(instruction.arg, tuple)
                    else instruction.arg
                )
                if not isinstance(count, int) or count < 0 or len(stack) < count:
                    return None
                items = tuple(stack[-count:]) if count else ()
                if count:
                    del stack[-count:]
                stack.append(("aggregate", items))
            elif op is OpCode.CALL_RESOLVED_ELEMENT:
                reference = instruction.arg
                if not isinstance(reference, ResolvedElementReference):
                    return None
                builtin = value.globals.get(reference.name)
                if not isinstance(builtin, BuiltinValue):
                    return None
                try:
                    overload = builtin.element.definitions[reference.overload_index]
                except IndexError:
                    return None
                width = len(overload.signature.params)
                if (
                    reference.vectorised
                    or reference.extension is not None
                    or reference.static_values
                    or reference.type_args
                    or reference.multidispatch
                    or overload.implementation is None
                    or len(overload.signature.returns) != 1
                    or len(stack) < width
                ):
                    return None
                arguments = tuple(stack[-width:]) if width else ()
                if width:
                    del stack[-width:]
                stack.append(
                    ("call", reference.name, overload, builtin.context, arguments)
                )
            else:
                return None
        if len(stack) != 1:
            return None
        subject = stack[0]

        branches: list[tuple[tuple[object, ...], Any]] = []
        index = source_index + 1
        while index < len(instructions):
            instruction = instructions[index]
            if instruction.op is OpCode.MATCH_ERROR:
                break
            if instruction.op is OpCode.RETURN:
                break
            if instruction.op is not OpCode.JUMP_IF_MATCH:
                return None
            patterns, target = instruction.arg
            if (
                not isinstance(patterns, tuple)
                or len(patterns) != 1
                or not isinstance(target, int)
                or not 0 <= target < len(instructions)
                or instructions[target].op is not OpCode.PUSH_CONST
            ):
                return None
            branches.append((patterns, instructions[target].arg))
            index += 1
        if not branches:
            return None

        def evaluate(node: object, argument: Any) -> Any:
            """Evaluate one pure symbolic node, applying projection reductions."""
            if not isinstance(node, tuple) or not node:
                raise RuntimeError("invalid symbolic match expression")
            kind = node[0]
            if kind == "arg":
                return argument
            if kind == "const":
                return node[1]
            if kind == "index":
                receiver = evaluate(node[1], argument)
                position = int(evaluate(node[2], argument))
                return receiver[position]
            if kind == "aggregate":
                return tuple(evaluate(item, argument) for item in node[1])
            if kind == "call":
                _kind, name, overload, context, arguments = node
                # A reduction over one removal projection can consume the source
                # directly. This avoids constructing the intermediate collection.
                if (
                    name == "sum"
                    and len(arguments) == 1
                    and isinstance(arguments[0], tuple)
                    and arguments[0]
                    and arguments[0][0] == "call"
                    and arguments[0][1] == "removeAt"
                ):
                    removal = arguments[0]
                    removal_args = removal[4]
                    if len(removal_args) == 2:
                        source = evaluate(removal_args[0], argument)
                        omitted = int(evaluate(removal_args[1], argument))
                        total: Any = RuntimeNumber(0)
                        for item_index, item in enumerate(source):
                            if item_index != omitted:
                                total += item
                        return total
                values = tuple(evaluate(item, argument) for item in arguments)
                result = overload.implementation(
                    overload.runtime_arguments(values),
                    context,
                )
                if len(result) != 1:
                    raise RuntimeError("symbolic match call must return one value")
                return result[0]
            raise RuntimeError(f"unsupported symbolic match node {kind!r}")

        def pattern_matches(pattern: object, candidate: Any) -> bool:
            """Match allocation-free literal, wildcard, and aggregate patterns."""
            if not isinstance(pattern, tuple) or not pattern:
                return False
            kind = pattern[0]
            if kind == "wildcard":
                return True
            if kind == "literal":
                literal = pattern[1]
                if isinstance(candidate, int) and isinstance(literal, RuntimeNumber):
                    return (
                        literal.imag.coefficient == 0
                        and literal.real.exponent >= 0
                        and candidate
                        == literal.real.coefficient * (10 ** literal.real.exponent)
                    )
                if isinstance(candidate, RuntimeNumber) and isinstance(literal, RuntimeNumber):
                    return (
                        candidate.real.coefficient == literal.real.coefficient
                        and candidate.real.exponent == literal.real.exponent
                        and candidate.imag.coefficient == literal.imag.coefficient
                        and candidate.imag.exponent == literal.imag.exponent
                    )
                return candidate == literal
            if kind == "list" and len(pattern) == 2:
                children = pattern[1]
                return (
                    isinstance(candidate, (list, tuple))
                    and len(candidate) == len(children)
                    and all(
                        pattern_matches(child, item)
                        for child, item in zip(children, candidate, strict=True)
                    )
                )
            return False

        def compile_node(node: object) -> Callable[[Any], Any]:
            """Compile one symbolic node into a reusable unary Python closure."""
            if not isinstance(node, tuple) or not node:
                raise RuntimeError("invalid symbolic match expression")
            kind = node[0]
            if kind == "arg":
                return lambda argument: argument
            if kind == "const":
                constant = node[1]
                return lambda _argument: constant
            if kind == "index":
                receiver = compile_node(node[1])
                position = compile_node(node[2])
                return lambda argument: receiver(argument)[int(position(argument))]
            if kind == "aggregate":
                items = tuple(compile_node(item) for item in node[1])
                return lambda argument: tuple(item(argument) for item in items)
            if kind == "call":
                _kind, name, overload, context, arguments = node
                if (
                    name == "sum"
                    and len(arguments) == 1
                    and isinstance(arguments[0], tuple)
                    and arguments[0]
                    and arguments[0][0] == "call"
                    and arguments[0][1] == "removeAt"
                    and len(arguments[0][4]) == 2
                ):
                    source = compile_node(arguments[0][4][0])
                    omitted = compile_node(arguments[0][4][1])

                    def reduce_projection(argument: Any) -> Any:
                        """Reduce a collection while skipping one projected index."""
                        values = source(argument)
                        omitted_index = int(omitted(argument))
                        integer_total = 0
                        projected: list[Any] = []
                        integral = True
                        for item_index, item in enumerate(values):
                            if item_index == omitted_index:
                                continue
                            projected.append(item)
                            if (
                                integral
                                and isinstance(item, RuntimeNumber)
                                and item.imag.coefficient == 0
                                and item.real.exponent >= 0
                            ):
                                integer_total += (
                                    item.real.coefficient * (10 ** item.real.exponent)
                                )
                            else:
                                integral = False
                        if integral:
                            return integer_total
                        total: Any = RuntimeNumber(0)
                        for item in projected:
                            total += item
                        return total

                    return reduce_projection
                compiled_arguments = tuple(compile_node(item) for item in arguments)
                implementation = overload.implementation
                assert implementation is not None

                def invoke_resolved(argument: Any) -> Any:
                    """Invoke one resolved scalar operation from compiled operands."""
                    values = tuple(item(argument) for item in compiled_arguments)
                    result = implementation(overload.runtime_arguments(values), context)
                    if len(result) != 1:
                        raise RuntimeError("symbolic match call must return one value")
                    return result[0]

                return invoke_resolved
            raise RuntimeError(f"unsupported symbolic match node {kind!r}")

        subject_evaluator = compile_node(subject)
        compiled_branches = tuple(
            (
                (lambda candidate, pattern=patterns[0]: pattern_matches(pattern, candidate)),
                result,
            )
            for patterns, result in branches
        )

        def invoke_symbolic(argument: Any) -> tuple[Any, ...]:
            """Evaluate the subject graph once and dispatch its prepared patterns."""
            candidate = subject_evaluator(argument)
            for matcher, result in compiled_branches:
                if matcher(candidate):
                    return (result,)
            raise RuntimeError("non-exhaustive match at runtime")

        return PreparedCall(arity, multiplicity, "symbolic-match", invoke_symbolic)

    def _prepare_match_dispatch_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Prepare a scalar guarded-match decision tree without VM activations."""
        if (
            arity != 1
            or multiplicity != 1
            or len(value.code.params) != 1
            or value.code.accepts_stack_inputs
            or value.code.element_tags
            or _prepared_call_needs_return_contracts(value.code)
        ):
            return None
        instructions = value.code.instructions
        if (
            len(instructions) < 4
            or instructions[0].op is not OpCode.SOURCE_ARGS
            or instructions[-1].op is not OpCode.RETURN
        ):
            return None
        branches: list[tuple[Callable[[Any], bool], Callable[[Any], Any]]] = []
        index = 1
        while index < len(instructions) - 1:
            instruction = instructions[index]
            if instruction.op is OpCode.MATCH_ERROR:
                index += 1
                continue
            if instruction.op is not OpCode.JUMP_IF_MATCH:
                break
            patterns, target = instruction.arg
            if not isinstance(patterns, tuple) or len(patterns) != 1:
                return None
            pattern = patterns[0]
            if not isinstance(pattern, tuple) or not pattern:
                return None
            if pattern[0] == "guard" and len(pattern) == 2:
                guard_code = pattern[1]
                if not isinstance(guard_code, FunctionCode):
                    return None
                guard = self._prepare_builtin_stack_test(guard_code, value.globals)
                if guard is None:
                    return None
            elif pattern[0] == "wildcard":
                guard = lambda _subject: True
            else:
                return None
            if not isinstance(target, int) or not 0 <= target < len(instructions):
                return None
            producer = self._prepare_match_branch_result(
                instructions, target, value.globals
            )
            if producer is None:
                return None
            branches.append((guard, producer))
            index += 1
        if not branches:
            return None

        def invoke_match(subject: Any) -> tuple[Any, ...]:
            """Evaluate prepared guards in source order and produce one branch value."""
            if is_list_like(subject):
                return tuple(self.call_value(value, [subject]))
            for guard, producer in branches:
                if guard(subject):
                    return (producer(subject),)
            raise RuntimeError("non-exhaustive match at runtime")

        return PreparedCall(arity, multiplicity, "match-dispatch", invoke_match)

    def _prepare_builtin_stack_test(
        self,
        code: FunctionCode,
        globals_: dict[str, Any],
    ) -> Callable[[Any], bool] | None:
        """Compile one pure scalar built-in stack program into a Boolean test."""
        operations: list[tuple[str, Any]] = []
        pending_builtin: BuiltinValue | None = None
        for instruction in code.instructions:
            if instruction.op is OpCode.PUSH_CONST:
                operations.append(("const", instruction.arg))
            elif instruction.op is OpCode.LOAD_VAR:
                operations.append(("arg", None))
            elif instruction.op is OpCode.LOAD_ELEMENT:
                candidate = globals_.get(instruction.arg)
                if not isinstance(candidate, BuiltinValue):
                    return None
                pending_builtin = candidate
            elif instruction.op is OpCode.CALL:
                if pending_builtin is None:
                    return None
                operations.append(("call", pending_builtin))
                pending_builtin = None
            elif instruction.op is OpCode.RETURN:
                continue
            else:
                return None
        if pending_builtin is not None:
            return None

        selection_cache: dict[
            tuple[int, tuple[type[Any], ...]],
            BuiltinOverload,
        ] = {}

        def test(subject: Any) -> bool:
            """Run one prepared guard using direct selected built-in implementations."""
            stack: list[Any] = [subject]
            for kind, payload in operations:
                if kind == "const":
                    stack.append(payload)
                    continue
                if kind == "arg":
                    stack.append(subject)
                    continue
                builtin = payload
                selected: BuiltinOverload | None = None
                selected_args: tuple[Any, ...] = ()
                for overload in builtin.candidates:
                    width = len(overload.signature.params)
                    if len(stack) < width:
                        continue
                    call_args = tuple(stack[-width:]) if width else ()
                    cache_key = (id(builtin), tuple(type(arg) for arg in call_args))
                    cached = selection_cache.get(cache_key)
                    if cached is not None and cached.runtime_matches(call_args):
                        selected = cached
                        selected_args = call_args
                        break
                    if (
                        overload.implementation is not None
                        and overload.ownership_trivial
                        and not overload.signature.element_tags
                        and overload.runtime_matches(call_args)
                    ):
                        selected = overload
                        selected_args = call_args
                        selection_cache[cache_key] = overload
                        break
                if selected is None or selected.implementation is None:
                    raise RuntimeError("prepared guard found no matching built-in overload")
                width = len(selected.signature.params)
                if width:
                    del stack[-width:]
                stack.extend(
                    selected.implementation(
                        selected.runtime_arguments(selected_args),
                        builtin.context,
                    )
                )
            if len(stack) != 1:
                raise RuntimeError("prepared guard produced an invalid stack")
            return _truthy(stack[0])

        return test

    def _prepare_match_branch_result(
        self,
        instructions: tuple[Any, ...],
        target: int,
        globals_: dict[str, Any],
    ) -> Callable[[Any], Any] | None:
        """Prepare a constant or scalar-formatting result for one match branch."""
        instruction = instructions[target]
        if instruction.op is OpCode.PUSH_CONST:
            result = instruction.arg
            return lambda _subject: result
        if (
            target + 2 < len(instructions)
            and instruction.op is OpCode.LOAD_ELEMENT
            and instruction.arg == "top"
            and instructions[target + 1].op is OpCode.CALL
            and instructions[target + 2].op is OpCode.BUILD_STRING
        ):
            return self.format_value
        return None

    def _prepare_direct_leaf_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Prepare a direct-leaf plan when no call can suspend into user code."""
        if (
            len(value.code.params) != arity
            or value.code.accepts_stack_inputs
            or not self._can_execute_direct_leaf(value)
        ):
            return None
        needs_contracts = _prepared_call_needs_return_contracts(value.code)

        target_ranks = value.code.param_collection_ranks

        def invoke_leaf(*args: Any) -> tuple[Any, ...]:
            """Invoke a direct leaf while preserving collection adaptation."""
            collection_args_are_scalar = (
                len(target_ranks) == len(args)
                and all(
                    target_rank is not None
                    and runtime_collection_rank(arg) == target_rank
                    for arg, target_rank in zip(args, target_ranks, strict=True)
                    if is_list_like(arg)
                )
            )
            if any(is_list_like(arg) for arg in args) and not collection_args_are_scalar:
                return tuple(self.call_value(value, list(args)))
            if needs_contracts:
                return tuple(self._execute_direct_leaf(value, tuple(args)))
            return tuple(self._execute_direct_leaf_body(value, tuple(args)))

        return PreparedCall(arity, multiplicity, "direct-leaf", invoke_leaf)

    def _prepare_straight_line_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Prepare a short stack program made only from pure resolved built-ins."""
        if (
            len(value.code.params) != arity
            or value.code.accepts_stack_inputs
            or value.code.element_tags
            or multiplicity < 1
            or _prepared_call_needs_return_contracts(value.code)
        ):
            return None
        instructions = value.code.instructions
        if not instructions or instructions[-1].op is not OpCode.RETURN:
            return None
        operations: list[tuple[str, Any]] = []
        has_call = False
        used_arguments: set[int] = set()
        stack_depth = 0
        for instruction in instructions[:-1]:
            if instruction.op is OpCode.LOAD_VAR and instruction.arg in value.code.params:
                argument_index = value.code.params.index(instruction.arg)
                operations.append(("arg", argument_index))
                used_arguments.add(argument_index)
                stack_depth += 1
                continue
            if instruction.op is OpCode.PUSH_CONST:
                operations.append(("const", instruction.arg))
                stack_depth += 1
                continue
            if instruction.op is not OpCode.CALL_RESOLVED_ELEMENT:
                return None
            reference = instruction.arg
            if not isinstance(reference, ResolvedElementReference):
                return None
            if (
                reference.vectorised
                or reference.extension is not None
                or reference.static_values
                or reference.type_args
                or reference.multidispatch
                or reference.arity_override is not None
                or reference.consumed_override is not None
                or reference.return_tags
                or reference.return_tag_specs
                or reference.return_collection_ranks
            ):
                return None
            builtin = value.globals.get(reference.name)
            if not isinstance(builtin, BuiltinValue):
                return None
            try:
                overload = builtin.element.definitions[reference.overload_index]
            except IndexError:
                return None
            if (
                overload.implementation is None
                or not overload.ownership_trivial
                or overload.runtime_return_tags
                or overload.signature.element_tags
            ):
                return None
            width = len(overload.signature.params)
            if stack_depth < width:
                return None
            stack_depth += len(overload.signature.returns) - width
            operations.append(("call", (overload, builtin.context)))
            has_call = True
        if (
            not has_call
            or stack_depth != multiplicity
            or used_arguments != set(range(arity))
        ):
            return None

        def invoke_straight_line(*args: Any) -> tuple[Any, ...]:
            """Evaluate a prepared scalar stack program without a VM frame."""
            if any(is_list_like(arg) for arg in args):
                return tuple(self.call_value(value, list(args)))
            stack: list[Any] = []
            for kind, payload in operations:
                if kind == "arg":
                    stack.append(args[payload])
                elif kind == "const":
                    stack.append(payload)
                else:
                    overload, context = payload
                    width = len(overload.signature.params)
                    if len(stack) < width:
                        raise RuntimeError("prepared straight-line stack underflow")
                    call_args = tuple(stack[-width:]) if width else ()
                    if width:
                        del stack[-width:]
                    implementation = overload.implementation
                    assert implementation is not None
                    stack.extend(
                        implementation(overload.runtime_arguments(call_args), context)
                    )
            if len(stack) != multiplicity:
                raise RuntimeError(
                    "prepared straight-line callable produced an invalid stack"
                )
            return tuple(stack)

        return PreparedCall(arity, multiplicity, "straight-line", invoke_straight_line)

    def _prepare_resolved_builtin_call(
        self,
        value: FunctionValue,
        arity: int,
        multiplicity: int,
    ) -> PreparedCall | None:
        """Collapse a straight-line wrapper into its selected built-in implementation."""
        if (
            len(value.code.params) != arity
            or value.code.accepts_stack_inputs
            or multiplicity != 1
        ):
            return None
        instructions = value.code.instructions
        if len(instructions) < arity + 2 or instructions[-1].op is not OpCode.RETURN:
            return None
        call = instructions[-2]
        if call.op is not OpCode.CALL_RESOLVED_ELEMENT:
            return None
        reference = call.arg
        if not isinstance(reference, ResolvedElementReference):
            return None
        if (
            reference.vectorised
            or reference.extension is not None
            or reference.static_values
            or reference.type_args
            or reference.multidispatch
            or reference.arity_override is not None
            or reference.consumed_override is not None
            or reference.return_tags
            or reference.return_tag_specs
            or reference.return_collection_ranks
        ):
            return None
        builtin = value.globals.get(reference.name)
        if not isinstance(builtin, BuiltinValue):
            return None
        try:
            overload = builtin.element.definitions[reference.overload_index]
        except IndexError:
            return None
        if (
            overload.implementation is None
            or not overload.ownership_trivial
            or overload.runtime_return_tags
            or overload.signature.element_tags
            or len(overload.signature.returns) != multiplicity
        ):
            return None

        operands: list[tuple[str, Any]] = []
        argument_names = set(value.code.params)
        for instruction in instructions[:-2]:
            if instruction.op is OpCode.LOAD_VAR and instruction.arg in argument_names:
                operands.append(("arg", value.code.params.index(instruction.arg)))
            elif instruction.op is OpCode.PUSH_CONST:
                operands.append(("const", instruction.arg))
            else:
                return None
        if len(operands) != len(overload.signature.params):
            return None
        used = [payload for kind, payload in operands if kind == "arg"]
        if sorted(used) != list(range(arity)):
            return None
        implementation = overload.implementation
        context = builtin.context

        def invoke_builtin(*args: Any) -> tuple[Any, ...]:
            """Invoke the selected built-in without a wrapper function frame."""
            if any(is_list_like(arg) for arg in args):
                return tuple(self.call_value(value, list(args)))
            call_args = tuple(
                args[payload] if kind == "arg" else payload
                for kind, payload in operands
            )
            return implementation(overload.runtime_arguments(call_args), context)

        return PreparedCall(arity, multiplicity, "resolved-builtin", invoke_builtin)

    def test_predicate(self, value: Any, argument: Any) -> bool:
        """Invoke a unary predicate through a cached extensible preparation path."""
        if isinstance(value, FunctionValue):
            if value.predicate_test is None:
                specializers = (self._prepare_symbolic_predicate,)
                prepared_test = next(
                    (
                        candidate
                        for specializer in specializers
                        if (candidate := specializer(value)) is not None
                    ),
                    None,
                )
                if prepared_test is None and self._can_execute_direct_leaf(value):
                    def prepared_test(argument: Any) -> bool:
                        """Execute a proved predicate leaf without return-tag rebuilding."""
                        result = self._execute_direct_leaf_body(value, (argument,))
                        if len(result) != 1:
                            raise RuntimeError(
                                "predicate must return exactly one value"
                            )
                        return bool(unwrap_runtime_value(result[0]))
                if prepared_test is None:
                    prepared = self.prepare_call(value, 1, 1)

                    def prepared_test(argument: Any) -> bool:
                        """Convert one prepared unary result to predicate truth."""
                        return bool(unwrap_runtime_value(prepared(argument)[0]))

                value.predicate_test = prepared_test
            assert value.predicate_test is not False
            return value.predicate_test(argument)
        result = self.call_value(value, [argument])
        if len(result) != 1:
            raise RuntimeError("predicate must return exactly one value")
        return bool(unwrap_runtime_value(result[0]))

    def _prepare_symbolic_predicate(
        self,
        function: FunctionValue,
    ) -> Callable[[Any], bool] | None:
        """Prepare a pure straight-line predicate from its symbolic stack graph."""
        if len(function.code.params) != 1 or function.code.accepts_stack_inputs:
            return None
        stack: list[object] = []
        locals_: dict[str, object] = {
            function.code.params[0]: ("arg", 0),
        }
        for instruction in function.code.instructions:
            op = instruction.op
            if op is OpCode.PUSH_CONST:
                stack.append(("const", instruction.arg))
            elif op in {OpCode.LOAD_VAR, OpCode.LOAD_VAR_BORROW}:
                expression = locals_.get(instruction.arg)
                if expression is None:
                    return None
                stack.append(expression)
            elif op is OpCode.STORE_VAR:
                if not stack:
                    return None
                locals_[instruction.arg] = stack.pop()
            elif op in {OpCode.CYCLE_BEGIN, OpCode.CYCLE_END}:
                continue
            elif op is OpCode.BUILD_LIST:
                count = (
                    instruction.arg[0]
                    if isinstance(instruction.arg, tuple)
                    else instruction.arg
                )
                if not isinstance(count, int) or count < 0 or len(stack) < count:
                    return None
                items = tuple(stack[-count:]) if count else ()
                if count:
                    del stack[-count:]
                stack.append(("list", items))
            elif op is OpCode.STACK_SHUFFLE:
                try:
                    _mode, prestack, poststack, permutation = _stack_shuffle_spec(
                        instruction.arg
                    )
                except RuntimeError:
                    return None
                width = len(prestack)
                if len(stack) < width:
                    return None
                values = stack[-width:]
                if permutation is not None:
                    stack[-width:] = [values[index] for index in permutation]
                else:
                    labelled = {
                        label: value
                        for label, value in zip(prestack, values, strict=True)
                        if label is not None
                    }
                    stack[-width:] = [labelled[label] for label in poststack]
            elif op is OpCode.CALL_RESOLVED_ELEMENT:
                reference = instruction.arg
                if not isinstance(reference, ResolvedElementReference):
                    return None
                builtin = function.globals.get(reference.name)
                if not isinstance(builtin, BuiltinValue):
                    return None
                try:
                    overload = builtin.element.definitions[reference.overload_index]
                except IndexError:
                    return None
                width = len(overload.signature.params)
                if len(stack) < width:
                    return None
                arguments = tuple(stack[-width:]) if width else ()
                if reference.name == "swap" and width == 2:
                    del stack[-width:]
                    stack.extend(reversed(arguments))
                    continue
                if reference.name == "top" and width == 1:
                    del stack[-width:]
                    stack.append(arguments[0])
                    continue
                if (
                    overload.implementation is None
                    or overload.signature.element_tags
                    or reference.extension is not None
                    or reference.static_values
                    or reference.type_args
                    or reference.multidispatch
                ):
                    return None
                if width:
                    del stack[-width:]
                if len(overload.signature.returns) != 1:
                    return None
                kind = "vector-call" if reference.vectorised else "call"
                stack.append((kind, overload, builtin.context, arguments, reference))
            elif op is OpCode.RETURN:
                continue
            else:
                return None
        if len(stack) != 1:
            return None
        expression = stack[0]

        def compile_expression(node: object) -> Callable[[Any], Any]:
            """Compile one normalized symbolic node into a reusable evaluator."""
            if not isinstance(node, tuple) or not node:
                raise RuntimeError("invalid prepared expression node")
            kind = node[0]
            if kind == "arg":
                return lambda argument: argument
            if kind == "const":
                return lambda _argument, value=node[1]: value
            if kind == "list":
                items = tuple(compile_expression(item) for item in node[1])
                return lambda argument: [evaluate(argument) for evaluate in items]
            if kind == "vector-call":
                _kind, overload, context, arguments, reference = node
                operands = tuple(compile_expression(item) for item in arguments)

                def evaluate_vector(argument: Any) -> Any:
                    """Evaluate one compiled symbolic vector call."""
                    values = tuple(evaluate(argument) for evaluate in operands)
                    result = _call_vectorized_resolved_builtin(
                        overload,
                        values,
                        context,
                        reference.vectorised_depths,
                        reference.vectorised_target_ranks,
                    )
                    if len(result) != 1:
                        raise RuntimeError("prepared expression call must return one value")
                    return result[0]

                return evaluate_vector
            if kind == "call":
                _kind, overload, context, arguments, _reference = node
                implementation = overload.implementation
                assert implementation is not None
                if (
                    overload.signature.params
                    and len(arguments) == 2
                    and isinstance(arguments[1], tuple)
                    and arguments[1]
                    and arguments[1][0] == "vector-call"
                ):
                    needle_evaluator = compile_expression(arguments[0])
                    vector_node = arguments[1]
                    _, projected, projected_context, projected_args, reference = vector_node
                    projected_operands = tuple(
                        compile_expression(item) for item in projected_args
                    )
                    depths = reference.vectorised_depths
                    vector_positions = tuple(
                        index for index, depth in enumerate(depths) if depth == 1
                    )
                    if len(vector_positions) == 1:
                        position = vector_positions[0]
                        projected_implementation = projected.implementation
                        assert projected_implementation is not None

                        def evaluate_fused_search(argument: Any) -> bool:
                            """Search a symbolic scalar projection without a temporary vector."""
                            needle = needle_evaluator(argument)
                            evaluated = tuple(
                                evaluate(argument) for evaluate in projected_operands
                            )
                            for item in evaluated[position]:
                                scalar_args = tuple(
                                    item if index == position else value
                                    for index, value in enumerate(evaluated)
                                )
                                produced = projected_implementation(
                                    projected.runtime_arguments(scalar_args),
                                    projected_context,
                                )
                                if len(produced) != 1:
                                    raise RuntimeError(
                                        "prepared vector projection must return one value"
                                    )
                                if unwrap_runtime_value(produced[0]) == unwrap_runtime_value(needle):
                                    return True
                            return False

                        return evaluate_fused_search
                operands = tuple(compile_expression(item) for item in arguments)

                def evaluate_call(argument: Any) -> Any:
                    """Evaluate one compiled symbolic scalar call."""
                    values = tuple(evaluate(argument) for evaluate in operands)
                    result = implementation(overload.runtime_arguments(values), context)
                    if len(result) != 1:
                        raise RuntimeError("prepared expression call must return one value")
                    return result[0]

                return evaluate_call
            raise RuntimeError(f"unknown prepared expression node {kind!r}")

        evaluate = compile_expression(expression)

        def test(argument: Any) -> bool:
            """Evaluate the compiled symbolic predicate for one scalar argument."""
            return _truthy(evaluate(argument))

        return test

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
            if self._can_execute_direct_leaf(value):
                return self._execute_direct_leaf(value, tuple(args))
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
        static_values: tuple[Any, ...] = (),
        vectorised: bool = False,
        vectorised_depths: tuple[int, ...] = (),
        vectorised_target_ranks: tuple[int | None, ...] = (),
    ) -> list[Any]:
        """Invoke one statically selected overload of a runtime callable."""
        if isinstance(value, FunctionValue):
            if overload_index != 0:
                raise RuntimeError(f"function has no overload {overload_index}")
            selected = value
        elif isinstance(value, OverloadedFunctionValue):
            try:
                selected = value.overloads[overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"function has no overload {overload_index}"
                ) from exc
        else:
            raise RuntimeError(f"cannot call value {_format_value(value)}")

        call_args = (*args, *static_values)
        if not vectorised:
            return self.call(selected, list(call_args))
        static_count = len(static_values)
        depths = (
            (*vectorised_depths, *(0 for _ in range(static_count)))
            if vectorised_depths
            else ()
        )
        target_ranks = (
            (*vectorised_target_ranks, *(None for _ in range(static_count)))
            if vectorised_target_ranks
            else ()
        )
        return list(
            _vectorize_function(
                self,
                selected,
                call_args,
                depths=depths,
                target_ranks=target_ranks,
            )
        )

    def execute(
        self,
        code: FunctionCode,
        locals_: dict[str, Any],
        globals_: dict[str, Any],
        cycle_values: tuple[Any, ...] = (),
        initial_stack: list[Any] | None = None,
        retained_locals: frozenset[str] = frozenset(),
    ) -> list[Any]:
        """Execute bytecode with an explicit, non-recursive activation stack."""
        return self._drive_frames(
            self._new_activation(
                code,
                locals_,
                globals_,
                cycle_values,
                initial_stack,
                retained_locals,
            )
        )

    def _drive_frames(self, root: _Activation) -> list[Any]:
        """Drive bytecode activations without nesting Python function calls."""
        frames: list[tuple[_Activation, _FunctionCallRequest | None]] = [(root, None)]
        while frames:
            activation, completed_request = frames[-1]
            try:
                event = self._run_activation(activation)
            except Exception as exc:
                frames.pop()
                if not frames:
                    raise
                self._propagate_frame_error(frames, exc)
                continue
            if isinstance(event, _FunctionCallRequest):
                try:
                    child = self._function_request_activation(event)
                except Exception as exc:
                    self._propagate_frame_error(frames, exc)
                else:
                    frames.append((child, event))
                continue

            frames.pop()
            try:
                result = (
                    self._finalize_function_request(completed_request, event)
                    if completed_request is not None
                    else event
                )
            except Exception as exc:
                if not frames:
                    raise
                self._propagate_frame_error(frames, exc)
                continue
            if not frames:
                return result
            try:
                self._resume_call_success(frames[-1][0], result)
            except Exception as exc:
                frames.pop()
                if not frames:
                    raise
                self._propagate_frame_error(frames, exc)
        raise RuntimeError("VM activation driver terminated without a result")

    def _propagate_frame_error(
        self,
        frames: list[tuple[_Activation, _FunctionCallRequest | None]],
        error: Exception,
    ) -> None:
        """Propagate one failed child call until an activation handles it."""
        while frames:
            activation = frames[-1][0]
            try:
                self._resume_call_error(activation, error)
            except Exception as exc:
                frames.pop()
                error = exc
            else:
                return
        raise error

    def _function_request_activation(
        self,
        request: _FunctionCallRequest,
    ) -> _Activation:
        """Prepare one user-function activation for the VM frame stack."""
        function = request.callee
        args = request.args
        parameter_count = len(function.code.params)
        if function.code.accepts_stack_inputs:
            if len(args) < parameter_count:
                raise RuntimeError(
                    f"{_function_name(function.code)} expected at least "
                    f"{parameter_count} arguments, got {len(args)}"
                )
            explicit_args = args[-parameter_count:] if parameter_count else ()
            stack_args = args[:-parameter_count] if parameter_count else args
        else:
            if len(args) != parameter_count:
                raise RuntimeError(
                    f"{_function_name(function.code)} expected "
                    f"{parameter_count} arguments, got {len(args)}"
                )
            explicit_args = args
            stack_args = ()
        locals_, retained_locals = _function_call_locals(
            function,
            explicit_args,
            isolate_captures=request.isolate_captures,
        )
        cycle_values = tuple(explicit_args) if function.code.cycle_params else ()
        initial_stack = list(stack_args)
        if function.code.params and not cycle_values:
            initial_stack.extend(explicit_args)
        return self._new_activation(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
            retained_locals,
        )

    def _finalize_function_request(
        self,
        request: _FunctionCallRequest,
        result: list[Any],
    ) -> list[Any]:
        """Apply one completed function's compiled return metadata."""
        function = request.callee
        ranked = (
            _apply_runtime_collection_ranks(
                result,
                function.code.return_collection_ranks,
            )
            if function.code.return_collection_ranks
            else result
        )
        tagged = (
            _canonicalize_runtime_return_tags(
                ranked,
                function.code.return_tags,
                self.tag_parents,
            )
            if function.code.return_tags
            else ranked
        )
        function_locals = dict(zip(function.code.params, request.args, strict=False))
        function_return_specs = _resolve_static_rank_variables(
            function.code.return_tag_specs, function_locals
        )
        function_contracted = (
            _canonicalize_runtime_tag_contracts(
                tagged,
                function_return_specs,
                self.tag_parents,
            )
            if function_return_specs
            else tagged
        )
        call_site_tagged = (
            _canonicalize_runtime_return_tags(
                function_contracted,
                request.return_tags,
                self.tag_parents,
            )
            if request.return_tags
            else function_contracted
        )
        call_site_contracted = (
            _canonicalize_runtime_tag_contracts(
                call_site_tagged,
                request.return_tag_specs,
                self.tag_parents,
            )
            if request.return_tag_specs
            else call_site_tagged
        )
        return list(call_site_contracted)

    def _resume_call_success(
        self,
        activation: _Activation,
        result: list[Any],
    ) -> None:
        """Resume a suspended caller after a successful child activation."""
        request = activation.pending_call
        if request is None:
            raise RuntimeError("completed function has no suspended caller")
        activation.pending_call = None
        try:
            if request.release_after is not None:
                _release_value(request.release_after, self)
            _mark_mustcall_method(request.args, result, request.callee)
            activation.frame.stack.extend(result)
        except Exception as exc:
            self._raise_activation_error(activation, exc)
        activation.ip += 1

    def _resume_call_error(
        self,
        activation: _Activation,
        error: Exception,
    ) -> None:
        """Resume a suspended caller by injecting a failed child call."""
        request = activation.pending_call
        if request is None:
            self._raise_activation_error(activation, error)
            return
        activation.pending_call = None
        if isinstance(error, _py_builtins.RuntimeError):
            error = _with_call_detail(error, request.target, request.args)
        if request.release_after is not None:
            try:
                _release_value(request.release_after, self)
            except Exception as exc:
                error = exc
        self._raise_activation_error(activation, error)

    def _raise_activation_error(
        self,
        activation: _Activation,
        error: Exception,
    ) -> None:
        """Handle or propagate an exception at an activation's current opcode."""
        frame = activation.frame
        if isinstance(error, PanicSignal):
            target = self._handle_panic(frame, error)
            if target is not None:
                activation.ip = target
                return
        if isinstance(error, _py_builtins.RuntimeError):
            runtime_error = (
                error if isinstance(error, RuntimeError) else RuntimeError(error)
            )
            runtime_error.add_execution_context(
                _function_name(activation.code),
                activation.ip,
                activation.code.instructions[activation.ip],
                frame.stack,
            )
            self._discard_frame(frame)
            raise runtime_error from error
        self._discard_frame(frame)
        raise error

    def _new_activation(
        self,
        code: FunctionCode,
        locals_: dict[str, Any],
        globals_: dict[str, Any],
        cycle_values: tuple[Any, ...] = (),
        initial_stack: list[Any] | None = None,
        retained_locals: frozenset[str] = frozenset(),
    ) -> _Activation:
        """Create one resumable bytecode activation."""
        return _Activation(
            code,
            _Frame(
                stack=list(initial_stack or ()),
                locals=locals_,
                globals=globals_,
                cycle_values=cycle_values,
                cycle_stack_remaining=(len(cycle_values) if code.cycle_params else 0),
                cycle_from_top=bool(code.cycle_params),
                retained_locals=retained_locals,
                is_global_scope=code.name == "<main>",
            ),
        )

    def _run_activation(self, activation: _Activation) -> object:
        """Run one activation until it calls another function or returns."""
        code = activation.code
        frame = activation.frame
        ip = activation.ip
        instructions = code.instructions
        try:
            while ip < len(instructions):
                instruction = instructions[ip]
                try:
                    match instruction.op:
                        case OpCode.PUSH_CONST:
                            frame.stack.append(instruction.arg)
                        case OpCode.LOAD_VAR:
                            value = _load_name(
                                instruction.arg,
                                frame.locals,
                                frame.globals,
                            )
                            frame.stack.append(
                                value
                                if isinstance(value, _SCALAR_RUNTIME_TYPES)
                                or not _needs_release(value)
                                else _retain_value(value)
                            )
                        case OpCode.LOAD_VAR_BORROW:
                            frame.stack.append(
                                _BorrowedValue(
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
                            if isinstance(
                                existing,
                                (FunctionValue, OverloadedFunctionValue),
                            ) and isinstance(
                                value,
                                (FunctionValue, OverloadedFunctionValue),
                            ):
                                stored = _store_value(existing, value)
                            else:
                                stored = value
                            if (
                                existing is not None
                                and existing is not stored
                                and _needs_release(existing)
                            ):
                                _release_value(existing, self)
                            target[instruction.arg] = stored
                            if code.name == "<main>":
                                frame.globals[instruction.arg] = stored
                            if isinstance(
                                stored,
                                (FunctionValue, OverloadedFunctionValue),
                            ):
                                _bind_recursive_value(stored, instruction.arg)
                        case OpCode.LOAD_ELEMENT:
                            value = _load_element_name(
                                instruction.arg,
                                frame.locals,
                                frame.globals,
                            )
                            frame.stack.append(
                                value
                                if isinstance(value, _SCALAR_RUNTIME_TYPES)
                                or not _needs_release(value)
                                else _retain_value(value)
                            )
                        case OpCode.MAKE_FUNCTION:
                            frame.stack.append(
                                _make_function_value(
                                    instruction.arg,
                                    frame.globals,
                                    _closure_locals(frame),
                                )
                            )
                        case OpCode.APPLY_DISPATCH_PLAN:
                            value = _pop(frame.stack, "function dispatch plan")
                            if (
                                not isinstance(value, OverloadedFunctionValue)
                                or not isinstance(instruction.arg, FunctionSetCode)
                            ):
                                raise RuntimeError(
                                    "function dispatch plan requires an overload set"
                                )
                            for overload in value.overloads:
                                _retain_value(overload)
                            planned = OverloadedFunctionValue(
                                value.overloads, instruction.arg.dispatch_plan
                            )
                            _release_value(value, self)
                            frame.stack.append(planned)
                        case OpCode.CALL:
                            try:
                                if instruction.arg is None:
                                    return_tag_specs = ()
                                elif isinstance(instruction.arg, tuple):
                                    return_tag_specs = instruction.arg
                                else:
                                    raise RuntimeError(
                                        "invalid bytecode: malformed call-site tag contract"
                                    )
                                request = self._call_stack_top(
                                    frame,
                                    return_tag_specs=return_tag_specs,
                                )
                                if request is not None:
                                    activation.pending_call = request
                                    activation.ip = ip
                                    return request
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
                        case OpCode.CALL_RESOLVED_ELEMENT:
                            try:
                                request = self._call_resolved_element(
                                    frame, instruction.arg
                                )
                                if request is not None:
                                    activation.pending_call = request
                                    activation.ip = ip
                                    return request
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
                        case OpCode.CHECK_CAST:
                            value = _pop(frame.stack, "checked cast")
                            cast_spec = _resolve_static_rank_variables(
                                instruction.arg, frame.locals
                            )
                            if not _matches_cast_type(value, cast_spec):
                                raise RuntimeError(
                                    f"checked cast failed: {_format_value(value)} is "
                                    f"{_runtime_type_name(value)}"
                                )
                            frame.stack.append(value)
                        case OpCode.TRY_CAST:
                            value = _pop(frame.stack, "optional cast")
                            if not isinstance(instruction.arg, tuple) or len(instruction.arg) != 2:
                                raise RuntimeError("invalid optional cast payload")
                            raw_cast, raw_contract = instruction.arg
                            cast_spec = _resolve_static_rank_variables(raw_cast, frame.locals)
                            if _matches_cast_type(value, cast_spec):
                                contract = _resolve_static_rank_variables(raw_contract, frame.locals)
                                refined = _canonicalize_runtime_value_tag_contract(value, contract, self.tag_parents)
                                frame.stack.append(ObjectValue("Some", {"value": refined}))
                            else:
                                frame.stack.append(ObjectValue("None", {}))
                        case OpCode.CANONICALIZE_TAGS:
                            value = _pop(frame.stack, "tag canonicalization")
                            contract_spec = _resolve_static_rank_variables(
                                instruction.arg, frame.locals
                            )
                            frame.stack.append(
                                _canonicalize_runtime_value_tag_contract(
                                    value, contract_spec, self.tag_parents
                                )
                            )
                        case OpCode.BUILD_LIST:
                            count, rank = (
                                instruction.arg
                                if isinstance(instruction.arg, tuple)
                                else (instruction.arg, None)
                            )
                            items, lifted_tags = _lift_common_collection_tags(
                                _pop_many(frame.stack, count)
                            )
                            value = ListValue(items, runtime_rank=rank)
                            frame.stack.append(
                                update_runtime_tags(value, add=lifted_tags)
                                if lifted_tags
                                else value
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
                                RecordValue(zip(instruction.arg, values, strict=True))
                            )
                        case OpCode.BUILD_DICT:
                            values = _pop_many(frame.stack, instruction.arg * 2)
                            frame.stack.append(
                                DictValue(zip(values[::2], values[1::2], strict=True))
                            )
                        case OpCode.ISOLATE_STACK_BEGIN:
                            frame.isolated_stacks.append(frame.stack)
                            frame.stack = []
                        case OpCode.ISOLATE_STACK_END:
                            if not frame.isolated_stacks or len(frame.stack) != 1:
                                raise RuntimeError("isolated literal expression must leave exactly one value")
                            value = frame.stack.pop()
                            frame.stack = frame.isolated_stacks.pop()
                            frame.stack.append(value)
                        case OpCode.MAKE_OBJECT_CONSTRUCTOR:
                            constructor = _object_constructor_reference(instruction.arg)
                            initializer = (
                                None
                                if constructor.initializer is None
                                else _make_function_value(
                                    constructor.initializer,
                                    frame.globals,
                                    _closure_locals(frame),
                                )
                            )
                            frame.stack.append(
                                ObjectConstructorValue(
                                    constructor.type_name,
                                    constructor.fields,
                                    constructor.required,
                                    dict(constructor.defaults),
                                    _object_runtime_type(constructor.runtime_metadata),
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
                            frame.cycle_stack_remaining = next_cycle_stack_remaining
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
                                frame.stack.append(
                                    _consume_receiver_result(
                                        receiver,
                                        _get_field(receiver, field),
                                        self,
                                    )
                                )
                        case OpCode.SET_FIELD:
                            field, optional_safe = _field_instruction_arg(
                                instruction.arg
                            )
                            receiver, value = _source_args(
                                frame,
                                2,
                                "field assignment",
                            )
                            borrowed = isinstance(receiver, _BorrowedValue)
                            if borrowed:
                                receiver = receiver.value
                            frame.stack.append(
                                _optional_safe_set_field(
                                    receiver,
                                    field,
                                    value,
                                    self,
                                )
                                if optional_safe
                                else _set_field(
                                    receiver,
                                    field,
                                    value,
                                    in_place=borrowed,
                                )
                            )
                        case OpCode.GET_INDEX:
                            try:
                                values = _pop_index_values(frame.stack, instruction.arg)
                                if frame.stack:
                                    receiver = frame.stack.pop()
                                else:
                                    try:
                                        (
                                            args,
                                            stack_count,
                                            next_cycle_index,
                                            next_cycle_stack_remaining,
                                        ) = frame.source_args(1)
                                    except _StackUnderflow as exc:
                                        raise RuntimeError(
                                            "stack underflow during indexing"
                                        ) from exc
                                    if stack_count:
                                        del frame.stack[-stack_count:]
                                    frame.cycle_index = next_cycle_index
                                    frame.cycle_stack_remaining = (
                                        next_cycle_stack_remaining
                                    )
                                    receiver = args[0]
                                result = _consume_receiver_result(
                                    receiver,
                                    _get_index(receiver, instruction.arg, values, self),
                                    self,
                                )
                                if instruction.arg.spread:
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
                                borrowed = isinstance(receiver, _BorrowedValue)
                                if borrowed:
                                    receiver = receiver.value
                                value = _pop(frame.stack, "indexed assignment")
                                frame.stack.append(
                                    _set_index(
                                        receiver,
                                        instruction.arg,
                                        values,
                                        value,
                                        in_place=borrowed,
                                        vm=self,
                                    )
                                )
                            except PanicSignal as exc:
                                target = self._handle_panic(frame, exc)
                                if target is None:
                                    raise
                                ip = target
                                continue
                        case OpCode.JUMP:
                            ip = _validated_jump_target(
                                instruction.arg,
                                len(instructions),
                            )
                            continue
                        case OpCode.JUMP_IF_FALSE:
                            target = _validated_jump_target(
                                instruction.arg,
                                len(instructions),
                            )
                            if not _truthy(_pop(frame.stack, "conditional jump")):
                                ip = target
                                continue
                        case OpCode.JUMP_IF_MATCH:
                            patterns, target = instruction.arg
                            target = _validated_jump_target(
                                target,
                                len(instructions),
                            )
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
                                        frame.cycle_from_top,
                                    )
                                )
                                frame.cycle_values = values
                                frame.cycle_index = 0
                                frame.cycle_stack_remaining = 0
                                frame.cycle_from_top = False
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
                                _resolve_pop_count(instruction.arg, frame.locals),
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
                            handlers = tuple(instruction.arg)
                            for _, target in handlers:
                                _validated_jump_target(target, len(instructions))
                            frame.panic_handlers.append(
                                _PanicHandler(handlers, len(frame.stack))
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
                                return self._finalize_frame(frame, result, code.return_count)
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
                        case OpCode.POP_N:
                            count = _resolve_pop_count(
                                instruction.arg, frame.locals
                            )
                            for value in _pop_many(frame.stack, count):
                                _release_value(value, self)
                        case OpCode.RETURN:
                            result = frame.stack
                            frame.stack = []
                            count = (
                                instruction.arg
                                if isinstance(instruction.arg, int)
                                else code.return_count
                            )
                            return self._finalize_frame(frame, result, count)
                        case OpCode.RETURN_SIGNAL:
                            result = frame.stack
                            frame.stack = []
                            count = (
                                instruction.arg
                                if isinstance(instruction.arg, int)
                                else code.return_count
                            )
                            result = self._select_return_values(result, count)
                            raise _FunctionReturn(tuple(result))
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
                activation.ip = ip
            result = frame.stack
            frame.stack = []
            return self._finalize_frame(frame, result, code.return_count)
        except _FunctionReturn as signal:
            if code.name == "foreach.body":
                self._discard_frame(frame)
                raise
            return self._finalize_frame(frame, list(signal.values), code.return_count)
        except Exception:
            self._discard_frame(frame)
            raise

    def _finalize_frame(
        self,
        frame: _Frame,
        result: list[Any],
        return_count: int | None,
    ) -> list[Any]:
        """Apply the analysed return multiplicity and release the completed frame."""
        result = self._select_return_values(result, return_count)
        self._release_frame_locals(frame)
        return result

    def _select_return_values(
        self,
        result: list[Any],
        return_count: int | None,
    ) -> list[Any]:
        """Keep exactly the selected topmost return values and release the rest."""
        if return_count is None:
            return result
        discarded = max(len(result) - return_count, 0)
        if discarded:
            discarded_values = result[:discarded]
            del result[:discarded]
            for value in discarded_values:
                _release_value(value, self)
        return result

    def _discard_frame(self, frame: _Frame) -> None:
        """Update discard frame state during VM execution."""
        _release_stack_tail(frame.stack, len(frame.stack), self)
        while frame.isolated_stacks:
            saved = frame.isolated_stacks.pop()
            _release_stack_tail(saved, len(saved), self)
        self._release_frame_locals(frame)

    def _release_frame_locals(self, frame: _Frame) -> None:
        """Release frame locals during VM execution."""
        for name, value in frame.locals.items():
            if (
                name in frame.retained_locals or frame.globals.get(name) is not value
            ) and _needs_release(value):
                _release_value(value, self)
        frame.locals.clear()

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

    def _call_stack_top(
        self,
        frame: _Frame,
        *,
        return_tag_specs: tuple[object, ...] = (),
    ) -> _FunctionCallRequest | None:
        """Invoke stack top or suspend for a user-function activation."""
        callee = _pop(frame.stack, "call")
        release_callee = isinstance(
            callee,
            (FunctionValue, OverloadedFunctionValue),
        )
        try:
            if isinstance(callee, BuiltinValue):
                _call_builtin(callee, frame)
                _canonicalize_frame_return_tag_contracts(
                    frame,
                    return_tag_specs,
                    self.tag_parents,
                )
                return None
            if isinstance(callee, FunctionValue):
                request = self._call_function(
                    callee,
                    frame,
                    return_tag_specs=return_tag_specs,
                )
                if request is None:
                    return None
                release_callee = False
                return replace(request, release_after=callee)
            if isinstance(callee, ObjectConstructorValue):
                _call_object_constructor(callee, frame, self)
                _canonicalize_frame_return_tag_contracts(
                    frame,
                    return_tag_specs,
                    self.tag_parents,
                )
                return None
            if isinstance(callee, OverloadedFunctionValue):
                if len(callee.overloads) == 1:
                    request = self._call_function(
                        callee.overloads[0],
                        frame,
                        return_tag_specs=return_tag_specs,
                    )
                    if request is None:
                        return None
                    release_callee = False
                    return replace(request, release_after=callee)
                raise RuntimeError(
                    "cannot call overloaded function without resolved slot"
                )
            raise RuntimeError(f"cannot call value {_format_value(callee)}")
        finally:
            if release_callee:
                _release_value(callee, self)

    def _call_resolved_element(
        self,
        frame: _Frame,
        reference: object,
    ) -> _FunctionCallRequest | None:
        """Invoke resolved element during VM execution."""
        if not isinstance(reference, ResolvedElementReference):
            raise RuntimeError(f"invalid resolved element reference {reference!r}")
        cache_key = id(reference)
        cached_builtin = self._resolved_builtin_cache.get(cache_key)
        if (
            cached_builtin is not None
            and cached_builtin[0] is reference
            and reference.name not in frame.locals
            and frame.globals.get(reference.name) is cached_builtin[1]
        ):
            _call_resolved_builtin(
                cached_builtin[1],
                cached_builtin[2],
                frame,
                self,
                reference.vectorised,
                reference.vectorised_depths,
                reference.vectorised_target_ranks,
                reference.return_collection_ranks,
                reference.return_tags,
                reference.return_tag_specs,
                reference.arity_override,
                reference.consumed_override,
                reference.static_values,
                reference.type_args,
                reference.extension,
            )
            return None
        value = _load_name(reference.name, frame.locals, frame.globals)
        if isinstance(value, BuiltinValue):
            try:
                overload = value.element.definitions[reference.overload_index]
            except IndexError as exc:
                raise RuntimeError(
                    f"resolved element '{reference.name}' has no overload "
                    f"{reference.overload_index}"
                ) from exc
            if reference.name not in frame.locals:
                self._resolved_builtin_cache[cache_key] = (
                    reference,
                    value,
                    overload,
                )
            _call_resolved_builtin(
                value,
                overload,
                frame,
                self,
                reference.vectorised,
                reference.vectorised_depths,
                reference.vectorised_target_ranks,
                reference.return_collection_ranks,
                reference.return_tags,
                reference.return_tag_specs,
                reference.arity_override,
                reference.consumed_override,
                reference.static_values,
                reference.type_args,
                reference.extension,
            )
            return None
        if isinstance(value, FunctionValue):
            return self._call_resolved_function_value(value, frame, reference)
        if isinstance(value, OverloadedFunctionValue):
            return self._call_resolved_overloaded_function(value, frame, reference)
        if isinstance(value, ObjectConstructorValue):
            _call_object_constructor(
                value,
                frame,
                self,
                reference.type_args,
                reference.overload_index,
            )
            return None
        if isinstance(value, ObjectValue):
            _require_single_resolved_slot(reference, "enum member")
            frame.stack.append(_retain_value(value))
            return None
        if reference.overload_index == 0 and not callable(value):
            frame.stack.append(_retain_value(value))
            return None
        raise RuntimeError(f"resolved element '{reference.name}' is not callable")

    def _call_resolved_function_value(
        self,
        value: FunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> _FunctionCallRequest | None:
        """Invoke resolved function value during VM execution."""
        _require_single_resolved_slot(reference, "function")
        frame.stack.extend(reference.static_values)
        return self._call_function(
            value,
            frame,
            vectorised=reference.vectorised,
            vectorised_depths=reference.vectorised_depths,
            vectorised_target_ranks=reference.vectorised_target_ranks,
            extension_reference=reference.extension,
            return_tags=reference.return_tags,
            return_tag_specs=reference.return_tag_specs,
            arity_override=reference.arity_override,
            consumed_override=reference.consumed_override,
        )

    def _call_resolved_overloaded_function(
        self,
        value: OverloadedFunctionValue,
        frame: _Frame,
        reference: ResolvedElementReference,
    ) -> _FunctionCallRequest | None:
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
        return self._call_function(
            overload,
            frame,
            vectorised=reference.vectorised,
            vectorised_depths=reference.vectorised_depths,
            vectorised_target_ranks=reference.vectorised_target_ranks,
            extension_reference=reference.extension,
            return_tags=reference.return_tags,
            return_tag_specs=reference.return_tag_specs,
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
        body = _make_function_value(body_code, frame.globals, _closure_locals(frame))
        condition = (
            None
            if condition_code is None
            else _make_function_value(
                condition_code,
                frame.globals,
                _closure_locals(frame),
            )
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
        condition = _make_function_value(
            condition_code,
            frame.globals,
            _closure_locals(frame),
        )
        body = _make_function_value(
            body_code,
            frame.globals,
            _closure_locals(frame),
        )
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
        body = _make_function_value(body_code, frame.globals, _closure_locals(frame))
        for index, item in enumerate(iterable):
            args = [item]
            if has_index:
                args.append(RuntimeNumber(index))
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
        return_tags: tuple[tuple[DataTag, ...], ...] = (),
        return_tag_specs: tuple[object, ...] = (),
        arity_override: int | None = None,
        consumed_override: int | None = None,
    ) -> _FunctionCallRequest | None:
        """Invoke or suspend a user function during VM execution."""
        arity = (
            arity_override
            if arity_override is not None
            else len(callee.code.params)
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
                    f"function '{_function_name(callee.code)}'",
                    frame.stack,
                    [f"{arity} argument(s)"],
                )
            ) from exc
        consumed_count = (
            min(consumed_override, stack_count)
            if consumed_override is not None
            else stack_count
        )
        if consumed_count:
            del frame.stack[-consumed_count:]
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
            if return_tags:
                result = _canonicalize_runtime_return_tags(
                    result,
                    return_tags,
                    self.tag_parents,
                )
            if return_tag_specs:
                result = _canonicalize_runtime_tag_contracts(
                    result,
                    return_tag_specs,
                    self.tag_parents,
                )
            frame.stack.extend(result)
        else:
            target = f"function '{_function_name(callee.code)}'"
            if self._can_execute_direct_leaf(callee):
                try:
                    result = self._execute_direct_leaf(callee, args)
                except _py_builtins.RuntimeError as exc:
                    raise _with_call_detail(exc, target, args) from exc
                _mark_mustcall_method(args, result, callee)
                if return_tags:
                    result = _canonicalize_runtime_return_tags(
                        result,
                        return_tags,
                        self.tag_parents,
                    )
                if return_tag_specs:
                    result = _canonicalize_runtime_tag_contracts(
                        result,
                        return_tag_specs,
                        self.tag_parents,
                    )
                frame.stack.extend(result)
            else:
                return _FunctionCallRequest(
                    callee,
                    args,
                    target,
                    return_tags=return_tags,
                    return_tag_specs=return_tag_specs,
                )
        return None

    def _can_execute_direct_leaf(
        self,
        function: FunctionValue,
    ) -> bool:
        """Return whether a proved leaf can bypass activation scheduling."""
        if function.direct_leaf is None:
            direct_leaf = True
            for instruction in function.code.instructions:
                if instruction.op in {
                    OpCode.CALL,
                    OpCode.FOREACH,
                    OpCode.LOAD_ELEMENT,
                    OpCode.MAKE_FUNCTION,
                    OpCode.VALIDATE_TAG,
                }:
                    direct_leaf = False
                    break
                if instruction.op is OpCode.CALL_RESOLVED_ELEMENT:
                    reference = instruction.arg
                    if (
                        not isinstance(reference, ResolvedElementReference)
                        or reference.extension is not None
                    ):
                        direct_leaf = False
                        break
                    value = function.globals.get(reference.name)
                    if not isinstance(value, BuiltinValue):
                        direct_leaf = False
                        break
                    try:
                        overload = value.element.definitions[reference.overload_index]
                    except IndexError:
                        direct_leaf = False
                        break
                elif (
                    instruction.op in {OpCode.LOAD_VAR, OpCode.LOAD_VAR_BORROW}
                    and instruction.arg not in function.code.params
                    and not str(instruction.arg).startswith("\x00literal_")
                ):
                    value = function.globals.get(instruction.arg, _MISSING_NAME)
                    if value is _MISSING_NAME or _needs_release(value):
                        direct_leaf = False
                        break
            function.direct_leaf = direct_leaf
        return function.direct_leaf and not function.code.accepts_stack_inputs

    def _execute_direct_leaf_body(
        self,
        function: FunctionValue,
        args: tuple[Any, ...],
    ) -> list[Any]:
        """Execute a proved leaf and return its raw stack result."""
        locals_, retained_locals = _function_call_locals(function, args)
        cycle_values = tuple(args) if function.code.cycle_params else ()
        initial_stack = [] if cycle_values else list(args)
        activation = self._new_activation(
            function.code,
            locals_,
            function.globals,
            cycle_values,
            initial_stack,
            retained_locals,
        )
        result = self._run_activation(activation)
        if isinstance(result, _FunctionCallRequest):
            raise RuntimeError("direct leaf unexpectedly suspended at a call")
        return result

    def _execute_direct_leaf(
        self,
        function: FunctionValue,
        args: tuple[Any, ...],
    ) -> list[Any]:
        """Execute a proved leaf without entering the frame scheduler."""
        locals_ = dict(zip(function.code.params, args, strict=False))
        return_tag_specs = _resolve_static_rank_variables(
            function.code.return_tag_specs, locals_
        )
        result = self._execute_direct_leaf_body(function, args)
        ranked = (
            _apply_runtime_collection_ranks(
                result,
                function.code.return_collection_ranks,
            )
            if function.code.return_collection_ranks
            else result
        )
        tagged = (
            _canonicalize_runtime_return_tags(
                ranked,
                function.code.return_tags,
                self.tag_parents,
            )
            if function.code.return_tags
            else ranked
        )
        contracted = (
            _canonicalize_runtime_tag_contracts(
                tagged,
                return_tag_specs,
                self.tag_parents,
            )
            if return_tag_specs
            else tagged
        )
        return list(contracted)

    def _match_patterns(
        self,
        frame: _Frame,
        patterns: tuple[object, ...],
    ) -> tuple[dict[str, Any], tuple[Any, ...]] | None:
        """Match patterns during VM execution."""
        if len(frame.stack) < len(patterns):
            return None
        if len(patterns) == 1:
            pattern = patterns[0]
            value = frame.stack[-1]
            if isinstance(pattern, tuple) and pattern:
                if pattern[0] == "literal":
                    if value != pattern[1]:
                        return None
                    return {"top": value}, (value,)
                if pattern[0] == "wildcard":
                    return {"top": value}, (value,)
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
        if isinstance(pattern, tuple) and pattern and pattern[0] == "rest":
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
            unwrapped = unwrap_runtime_value(value)
            raw_wrapper = (
                isinstance(type_spec, tuple)
                and len(type_spec) >= 3
                and type_spec[0] == "nominal"
                and type_spec[1] in {"Some", "OK"}
                and isinstance(type_spec[2], tuple)
                and len(type_spec[2]) == 1
                and not (
                    isinstance(unwrapped, ObjectValue)
                    and unwrapped.type_name == type_spec[1]
                )
            )
            if raw_wrapper:
                values = (unwrapped,)
            elif isinstance(unwrapped, ObjectValue):
                values = tuple(unwrapped.fields.values())
            else:
                return False
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


def _closure_locals(frame: _Frame) -> dict[str, Any] | None:
    """Return lexical locals that a newly created closure must own."""
    return None if frame.is_global_scope else frame.locals


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
                _closure_locals(frame),
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
                _closure_locals(frame),
            )
            created.append(function)
            rules.append((rule.presence, function))

        selector = None
        if reference.selector is not None:
            selector = _make_function_value(
                reference.selector,
                frame.globals,
                _closure_locals(frame),
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
    args: list[Any] | tuple[Any, ...],
    *,
    isolate_captures: bool = True,
) -> tuple[dict[str, Any], frozenset[str]]:
    """Compute function call locals during VM execution."""
    params = function.code.params
    if not params:
        locals_: dict[str, Any] = {}
    elif len(params) == 1:
        locals_ = {params[0]: args[0]}
    elif len(params) == 2:
        locals_ = {params[0]: args[0], params[1]: args[1]}
    else:
        locals_ = dict(zip(params, args, strict=True))
    if not isolate_captures or not function.owned_names:
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
            frame.cycle_from_top,
        )
    )
    frame.cycle_values = values
    frame.cycle_index = 0
    frame.cycle_stack_remaining = 0
    frame.cycle_from_top = False
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
        frame.cycle_from_top,
    ) = frame.cycle_scopes.pop()


def _store_value(existing: Any, value: Any) -> Any:
    """Store value during VM execution."""
    function_types = (FunctionValue, OverloadedFunctionValue)
    if isinstance(existing, function_types) and isinstance(value, function_types):
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


def _object_runtime_type(value: object) -> ObjectRuntimeType | None:
    """Determine the type of object runtime during VM execution."""
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) not in {6, 9, 10}:
        raise RuntimeError(f"invalid object runtime metadata {value!r}")
    accepted_names: tuple[str, ...] = ()
    generic_variances: tuple[str, ...] = ()
    type_facts: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    generic_supertypes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    if len(value) in {9, 10}:
        accepted_names = _runtime_string_tuple(value[6], "accepted type names")
        generic_variances = _runtime_string_tuple(value[7], "generic variances")
        if not isinstance(value[8], tuple):
            raise RuntimeError(f"invalid runtime type facts {value[8]!r}")
        facts: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
        for fact in value[8]:
            if (
                not isinstance(fact, tuple)
                or len(fact) != 3
                or not isinstance(fact[0], str)
            ):
                raise RuntimeError(f"invalid runtime type fact {fact!r}")
            facts.append(
                (
                    fact[0],
                    _runtime_string_tuple(fact[1], "accepted type names"),
                    _runtime_string_tuple(fact[2], "generic variances"),
                )
            )
        type_facts = tuple(facts)
    if len(value) == 10:
        if not isinstance(value[9], tuple):
            raise RuntimeError(f"invalid generic supertype metadata {value[9]!r}")
        supertypes: list[tuple[str, tuple[str, ...]]] = []
        for supertype in value[9]:
            if (
                not isinstance(supertype, tuple)
                or len(supertype) != 2
                or not isinstance(supertype[0], str)
            ):
                raise RuntimeError(f"invalid generic supertype metadata {supertype!r}")
            supertypes.append(
                (
                    supertype[0],
                    _runtime_string_tuple(supertype[1], "supertype arguments"),
                )
            )
        generic_supertypes = tuple(supertypes)
    return ObjectRuntimeType(
        destructor_name=cast(str | None, value[0]),
        pop_name=cast(str | None, value[1]),
        dup_name=cast(str | None, value[2]),
        dup_error=cast(str | None, value[3]),
        mustcall_mode=cast(str | None, value[4]),
        mustcall_methods=cast(tuple[str, ...], value[5]),
        accepted_names=accepted_names,
        generic_variances=generic_variances,
        type_facts=type_facts,
        generic_supertypes=generic_supertypes,
    )


def _runtime_string_tuple(value: object, description: str) -> tuple[str, ...]:
    """Validate one tuple of serializable runtime type metadata."""
    if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"invalid {description} {value!r}")
    return value


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
    if isinstance(value, _SCALAR_RUNTIME_TYPES):
        return False
    if isinstance(value, TaggedValue):
        value = value.value
        if isinstance(value, _SCALAR_RUNTIME_TYPES):
            return False
    if isinstance(value, (ListValue, DictValue)):
        return True
    if isinstance(value, list):
        return not _list_ownership_is_trivial(value)
    if isinstance(value, dict):
        return not _dict_ownership_is_trivial(value)
    return isinstance(value, _RELEASE_VALUE_TYPES)


def _list_ownership_is_trivial(value: list[Any]) -> bool:
    """Return whether a list has no direct values needing ownership traversal."""
    if isinstance(value, ListValue):
        cached = value._ownership_trivial
        if cached is not None:
            return cached
    trivial = not any(_needs_release(item) for item in value)
    if isinstance(value, ListValue):
        value._ownership_trivial = trivial
    return trivial


def _dict_ownership_is_trivial(value: dict[Any, Any]) -> bool:
    """Return whether a mapping has no values needing ownership traversal."""
    if isinstance(value, DictValue):
        cached = value._ownership_trivial
        if cached is not None:
            return cached
    trivial = not any(_needs_release(item) for item in value.values())
    if isinstance(value, DictValue):
        value._ownership_trivial = trivial
    return trivial


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
            raise RuntimeError(f"use after destruction of {object_type_name(value)}")
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
    if isinstance(value, (ListValue, DictValue)):
        value.refcount += 1
        return value
    if isinstance(value, list):
        if _list_ownership_is_trivial(value):
            return value
        for item in value:
            _retain_value(item, check_duplication=check_duplication)
        return value
    if isinstance(value, tuple):
        for item in value:
            _retain_value(item, check_duplication=check_duplication)
        return value
    if isinstance(value, dict):
        if _dict_ownership_is_trivial(value):
            return value
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
    if isinstance(value, ListValue):
        value.refcount -= 1
        if value.refcount > 0:
            return
        if not _list_ownership_is_trivial(value):
            for item in value:
                _release_value(item, vm)
        return
    if isinstance(value, DictValue):
        value.refcount -= 1
        if value.refcount > 0:
            return
        if not _dict_ownership_is_trivial(value):
            for item in value.values():
                _release_value(item, vm)
        return
    if isinstance(value, list):
        if _list_ownership_is_trivial(value):
            return
        for item in value:
            _release_value(item, vm)
        return
    if isinstance(value, tuple):
        for item in value:
            _release_value(item, vm)
        return
    if isinstance(value, dict):
        if _dict_ownership_is_trivial(value):
            return
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
    if runtime is not None and runtime.mustcall_mode and runtime.mustcall_methods:
        required = set(runtime.mustcall_methods)
        called = value.mustcall_called
        satisfied = (
            required.issubset(called)
            if runtime.mustcall_mode == "all"
            else bool(required & set(called))
        )
        if not satisfied:
            names = ", ".join(runtime.mustcall_methods)
            pop_error = PanicSignal(
                _fault_object(
                    "CleanupFault",
                    f"{object_type_name(value)} requires one of: {names}",
                )
            )
    if runtime is not None and runtime.destructor_name is not None:
        try:
            vm.call_value(
                _load_name(runtime.destructor_name, {}, vm.globals),
                [_retain_value(value)],
            )
        except PanicSignal as exc:
            raise RuntimeError(
                f"destructor for {object_type_name(value)} must not panic"
            ) from exc
    value.cleaning_up = False
    value.destroyed = True
    for item in value.fields.values():
        _release_value(item, vm)
    if pop_error is not None:
        raise pop_error


def _fault_object(type_name: str, message: str) -> ObjectValue:
    """Compute fault object during VM execution."""
    return ObjectValue(type_name, {"message": message})


def _vectorisation_fault() -> PanicSignal:
    """Build the intrinsic fault used for unequal vectorisation lengths."""
    return PanicSignal(
        _fault_object(
            "VectorisationFault",
            "cannot vectorise lists with different lengths",
        )
    )


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
        if isinstance(value, TaggedValue):
            value = value.value
        if isinstance(value, list):
            if not _list_ownership_is_trivial(value):
                return True
            continue
        if isinstance(value, dict):
            if not _dict_ownership_is_trivial(value):
                return True
            continue
        if isinstance(
            value, (ObjectValue, FunctionValue, OverloadedFunctionValue, tuple)
        ):
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
        return isinstance(value, RuntimeNumber) and value == value.to_integral_value()
    if type_name == "Real":
        return isinstance(value, RuntimeNumber)
    if type_name == "Number":
        return isinstance(value, RuntimeNumber)
    return False


def _function_overloads(
    value: FunctionValue | OverloadedFunctionValue,
) -> tuple[FunctionValue, ...]:
    """Collect the overloads for function during VM execution."""
    if isinstance(value, FunctionValue):
        return (value,)
    return value.overloads


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
        return (
            isinstance(value, tuple)
            and len(value) == len(pattern.children)
            and all(
                _runtime_pattern_matches(item, child)
                for item, child in zip(value, pattern.children, strict=True)
            )
        )
    if pattern.kind == "collection":
        return _runtime_collection_pattern_matches(value, pattern)
    if pattern.kind != "nominal":
        return False
    unwrapped = unwrap_runtime_value(value)
    if pattern.name == "Err":
        return _is_error_result_value(unwrapped)
    if pattern.name == "Fault":
        return _runtime_object_implements(unwrapped, "Fault") or (
            isinstance(unwrapped, ObjectValue)
            and (
                unwrapped.type_name == "Fault"
                or unwrapped.type_name.endswith("Fault")
                or unwrapped.type_name.rsplit(".", 1)[-1].endswith("Fault")
            )
        )
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
        runtime_type = value.runtime_type
        facts = () if runtime_type is None else runtime_type.type_facts
        accepted_names = (
            (value.type_name,)
            if runtime_type is None or not runtime_type.accepted_names
            else runtime_type.accepted_names
        )
        variances = (
            ()
            if runtime_type is None
            else tuple(
                _runtime_variance(marker) for marker in runtime_type.generic_variances
            )
        )
        return RuntimeTypePattern(
            "nominal",
            name=value.type_name,
            children=tuple(
                _parse_runtime_type_pattern(arg, facts) for arg in value.type_args
            ),
            accepted_names=accepted_names,
            variances=variances,
        )
    if isinstance(value, RuntimeNumber):
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
    if (
        actual.name not in target.accepted_names
        and target.name not in actual.accepted_names
    ):
        return False
    if actual.name != target.name:
        return not target.children
    if not target.children:
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


def _parse_runtime_type_pattern(
    text: str,
    type_facts: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (),
) -> RuntimeTypePattern:
    """Compute parse runtime type pattern during VM execution."""
    text = text.strip()
    union_parts = _split_runtime_type_args(text, "|")
    if len(union_parts) > 1:
        return RuntimeTypePattern(
            "union",
            children=tuple(
                _parse_runtime_type_pattern(part, type_facts) for part in union_parts
            ),
        )
    bracket = text.find("[")
    if bracket < 0 or not text.endswith("]"):
        accepted, variance_markers = _runtime_type_fact(text, type_facts)
        if not accepted:
            accepted = {
                "Number": ("Integer", "Real", "Number"),
                "Real": ("Integer", "Real"),
            }.get(text, (text,))
        return RuntimeTypePattern(
            "nominal",
            text,
            accepted_names=accepted,
            variances=tuple(_runtime_variance(item) for item in variance_markers),
        )
    name = text[:bracket].strip()
    inner = text[bracket + 1 : -1]
    children = tuple(
        _parse_runtime_type_pattern(part, type_facts)
        for part in _split_runtime_type_args(inner, ",")
    )
    accepted, variance_markers = _runtime_type_fact(name, type_facts)
    variances = tuple(_runtime_variance(item) for item in variance_markers)
    if not variances and name in {"Some", "OK"}:
        variances = (Variance.COVARIANT,) * len(children)
    return RuntimeTypePattern(
        "nominal",
        name,
        children,
        accepted or (name,),
        variances,
    )


def _runtime_type_fact(
    name: str,
    type_facts: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Look up closed-world nominal facts embedded in object metadata."""
    for fact_name, accepted, variances in type_facts:
        if fact_name == name:
            return accepted, variances
    return (), ()


def _runtime_variance(marker: str) -> Variance:
    """Decode a serialized generic variance marker."""
    if marker == "covariant":
        return Variance.COVARIANT
    if marker == "contravariant":
        return Variance.CONTRAVARIANT
    return Variance.INVARIANT


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
    if isinstance(value, RuntimeNumber):
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
        overloads = (
            (initializer,)
            if isinstance(initializer, FunctionValue)
            else initializer.overloads
        )
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
                    _release_stack_tail(
                        frame.stack,
                        stack_count,
                        callee.context.call.__self__,
                    )
                frame.cycle_index = next_cycle_index
                frame.cycle_stack_remaining = next_cycle_stack_remaining
                frame.stack.extend(result)
                return
    for overload in callee.candidates:
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
            result = implementation(overload.runtime_arguments(args), callee.context)
            if overload.runtime_return_tags:
                result = _apply_cached_runtime_return_tags(
                    result,
                    overload.runtime_return_tag_deltas,
                )
        except _py_builtins.RuntimeError as exc:
            raise _with_call_detail(
                exc,
                f"element '{callee.element.name}'",
                args,
            ) from exc
        if overload.ownership_trivial:
            if stack_count:
                del frame.stack[-stack_count:]
        else:
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
        if name not in value.fields or value.fields[name] is _UNINITIALIZED_OBJECT_FIELD
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
    return_tags: tuple[tuple[DataTag, ...], ...] = (),
    return_tag_specs: tuple[object, ...] = (),
    arity_override: int | None = None,
    consumed_override: int | None = None,
    static_values: tuple[Any, ...] = (),
    type_args: tuple[str, ...] = (),
    extension_reference: VectorExtensionReference | None = None,
) -> None:
    """Invoke resolved builtin during VM execution."""
    arity = (
        arity_override if arity_override is not None else len(overload.signature.params)
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
        replace(
            callee.context,
            static_values=static_values,
            type_args=type_args,
        )
        if static_values or type_args
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
            ownership_args = (*args, *_extension_owned_values(extension))
            vectorized = _bind_lazy_result_owners(ownership_args, vectorized)
            vectorized = _apply_runtime_collection_ranks(
                vectorized,
                return_collection_ranks,
            )
            if return_tags:
                vectorized = _canonicalize_runtime_return_tags(
                    vectorized,
                    return_tags,
                    vm.tag_parents,
                )
            if return_tag_specs:
                vectorized = _canonicalize_runtime_tag_contracts(
                    vectorized,
                    return_tag_specs,
                    vm.tag_parents,
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
        result = implementation(overload.runtime_arguments(args), context)
        if overload.runtime_return_tags:
            result = _apply_cached_runtime_return_tags(
                result,
                overload.runtime_return_tag_deltas,
            )
        if return_collection_ranks:
            result = _apply_runtime_collection_ranks(
                result,
                return_collection_ranks,
            )
        if return_tags:
            result = _canonicalize_runtime_return_tags(
                result,
                return_tags,
                vm.tag_parents,
            )
        if return_tag_specs:
            result = _canonicalize_runtime_tag_contracts(
                result,
                return_tag_specs,
                vm.tag_parents,
            )
    except _py_builtins.RuntimeError as exc:
        raise _with_call_detail(
            exc,
            f"element '{callee.element.name}'",
            args,
        ) from exc
    if overload.ownership_trivial:
        if consumed_count:
            del frame.stack[-consumed_count:]
    else:
        result = _bind_lazy_result_owners(args, result)
        _finalize_builtin_result_ownership(args, result)
        if consumed_count:
            _release_stack_tail(frame.stack, consumed_count, context.call.__self__)
    frame.cycle_index = next_cycle_index
    frame.cycle_stack_remaining = next_cycle_stack_remaining
    frame.stack.extend(result)


def _stack_shuffle(frame: _Frame, spec: object, vm: VirtualMachine) -> None:
    """Update stack shuffle state during VM execution."""
    mode, prestack, poststack, permutation = _stack_shuffle_spec(spec)
    arity = len(prestack)
    if permutation is not None and len(frame.stack) >= arity:
        if permutation == (1, 0) and arity == 2:
            frame.stack[-2], frame.stack[-1] = frame.stack[-1], frame.stack[-2]
            return
        values = frame.stack[-arity:]
        frame.stack[-arity:] = [values[index] for index in permutation]
        return
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


def _extract_object_field(receiver: ObjectValue, field: str, vm: VirtualMachine) -> Any:
    """Compute extract object field during VM execution."""
    try:
        value = receiver.fields[field]
    except KeyError as exc:
        raise RuntimeError(f"{receiver.type_name} has no field '{field}'") from exc
    if value is _UNINITIALIZED_OBJECT_FIELD:
        raise RuntimeError(f"{receiver.type_name} field '{field}' is not initialized")
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
        isinstance(value, ObjectValue) and value.type_name.rsplit(".", 1)[-1] == "None"
    )


def _is_error_result_value(value: Any) -> bool:
    """Return whether the value is error result value."""
    return isinstance(value, ObjectValue) and (
        _runtime_object_implements(value, "Err")
        or value.type_name == "Err"
        or value.type_name.endswith("Error")
        or value.type_name.rsplit(".", 1)[-1].endswith("Error")
    )


def _runtime_object_implements(value: Any, name: str) -> bool:
    """Return whether object metadata proves a nominal implementation."""
    return (
        isinstance(value, ObjectValue)
        and value.runtime_type is not None
        and name in value.runtime_type.accepted_names
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
) -> tuple[Any, ...]:
    """Execute the vectorisation plan selected by static analysis."""
    implementation = overload.implementation
    assert implementation is not None

    def typed_implementation(
        item_args: tuple[Any, ...],
        item_context: RuntimeContext,
    ) -> tuple[Any, ...]:
        """Compute typed implementation during VM execution."""
        result = implementation(overload.runtime_arguments(item_args), item_context)
        return (
            _apply_cached_runtime_return_tags(
                result,
                overload.runtime_return_tag_deltas,
            )
            if overload.runtime_return_tags
            else result
        )

    if vectorised_depths or vectorised_target_ranks:
        resolved_depths = _resolve_vectorisation_depths(
            args,
            vectorised_depths,
            vectorised_target_ranks,
        )
        stop_at_zero = (
            any(rank is not None for rank in vectorised_target_ranks)
            or any(
                _parameter_stops_vectorisation(param)
                for param in overload.signature.params
            )
        )
        return _vectorize_resolved_depths(
            typed_implementation,
            args,
            context,
            resolved_depths,
            extension,
            stop_at_zero=stop_at_zero,
        )
    return _vectorize_resolved(typed_implementation, args, context, extension)


def _vectorize(
    overload: BuiltinOverload,
    args: tuple[Any, ...],
    context: RuntimeContext,
) -> tuple[Any, ...]:
    """Vectorise one built-in through the shared resolved-call traversal."""
    implementation = overload.implementation
    if implementation is None:
        raise _CannotVectorize

    def invoke(
        item_args: tuple[Any, ...],
        item_context: RuntimeContext,
    ) -> tuple[Any, ...]:
        """Invoke the built-in after vector traversal reaches scalar arguments."""
        if not overload.runtime_matches(item_args):
            raise _CannotVectorize
        result = implementation(overload.runtime_arguments(item_args), item_context)
        return (
            _apply_cached_runtime_return_tags(
                result,
                overload.runtime_return_tag_deltas,
            )
            if overload.runtime_return_tags
            else result
        )

    return _vectorize_resolved(invoke, args, context)


def _vectorize_resolved(
    implementation: Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]],
    args: tuple[Any, ...],
    context: RuntimeContext,
    extension: _RuntimeVectorExtension | None = None,
) -> tuple[Any, ...]:
    """Vectorize resolved during VM execution."""
    depths = tuple(1 if is_list_like(arg) else 0 for arg in args)
    if not any(depths):
        return implementation(args, context)
    return _vectorize_resolved_depths(
        implementation,
        args,
        context,
        depths,
        extension,
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
            return implementation(args, context)
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
    return (LazyList(lazy_items),)


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
    if isinstance(typ, (TaggedType, NoVecType, ExactType)):
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
    """Vectorize a user function through one reusable scalar execution plan."""
    prepared = vm.prepare_call(
        callee,
        len(callee.code.params),
        _function_return_multiplicity(callee.code),
    )
    kernel = ScalarKernel.from_prepared(prepared)

    def implementation(item_args: tuple[Any, ...], _context: RuntimeContext):
        """Execute one statically shaped scalar item call."""
        return kernel.invoke(item_args)

    resolved_depths = (
        _resolve_vectorisation_depths(args, depths, target_ranks)
        if depths or target_ranks
        else tuple(1 if is_list_like(arg) else 0 for arg in args)
    )
    if (
        extension is None
        and resolved_depths
        and all(depth in (0, 1) for depth in resolved_depths)
        and any(depth == 1 for depth in resolved_depths)
        and all(
            depth == 0 or is_eager_sequence(arg)
            for arg, depth in zip(args, resolved_depths, strict=True)
        )
    ):
        result = _vectorize_eager_kernel(kernel, args, resolved_depths)
        return _bind_lazy_result_owners(args, result)

    context = RuntimeContext(
        vm.output,
        vm.call_value,
        vm.format_value,
        vm.call_value_overload,
    )
    result = _vectorize_resolved_depths(
        implementation,
        args,
        context,
        resolved_depths,
        extension,
        stop_at_zero=bool(depths or target_ranks),
    )
    ownership_args = (*args, *_extension_owned_values(extension))
    return _bind_lazy_result_owners(ownership_args, result)


def _vectorize_eager_kernel(
    kernel: ScalarKernel,
    args: tuple[Any, ...],
    depths: tuple[int, ...],
) -> tuple[Any, ...]:
    """Run a rank-one eager vector loop directly against one scalar kernel."""
    vectors = tuple(
        arg
        for arg, depth in zip(args, depths, strict=True)
        if depth == 1
    )
    lengths = {len(vector) for vector in vectors}
    if len(lengths) != 1:
        raise _vectorisation_fault()
    length = next(iter(lengths), 0)
    result_items: list[tuple[Any, ...]] = []
    if kernel.arity == 1 and depths == (1,):
        vector = args[0]
        result_items = [kernel.invoke((vector[index],)) for index in range(length)]
    elif kernel.arity == 2:
        left, right = args
        left_vector = depths[0] == 1
        right_vector = depths[1] == 1
        if left_vector and right_vector:
            result_items = [
                kernel.invoke((left[index], right[index]))
                for index in range(length)
            ]
        elif left_vector:
            result_items = [
                kernel.invoke((left[index], right)) for index in range(length)
            ]
        else:
            result_items = [
                kernel.invoke((left, right[index])) for index in range(length)
            ]
    else:
        for index in range(length):
            item_args = tuple(
                arg[index] if depth == 1 else arg
                for arg, depth in zip(args, depths, strict=True)
            )
            result_items.append(kernel.invoke(item_args))
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
        raise _vectorisation_fault()

    item_depths = tuple(max(depth - 1, 0) for depth in depths)
    result_items = []
    for index in range(max(vector_lengths)):
        item_args = tuple(
            (
                (arg[index] if index < len(arg) else _MISSING_VECTOR_ITEM)
                if depth > 0 and is_eager_sequence(arg)
                else arg
            )
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
            raise _vectorisation_fault()
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
        raise _vectorisation_fault()

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
            (
                ObjectValue("None", {})
                if value is _MISSING_VECTOR_ITEM
                else ObjectValue("Some", {"value": value})
            )
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
            pieces.append(format_runtime_value(next(values)))
        elif isinstance(part, str):
            pieces.append(part)
        else:
            raise RuntimeError(f"invalid string interpolation part {part!r}")
    return "".join(pieces)


def _load_name(name: str, locals_: dict[str, Any], globals_: dict[str, Any]) -> Any:
    """Load a lexical name with one dictionary probe per scope."""
    value = locals_.get(name, _MISSING_NAME)
    if value is not _MISSING_NAME:
        return value
    value = globals_.get(name, _MISSING_NAME)
    if value is not _MISSING_NAME:
        return value
    raise RuntimeError(f"undefined name '{name}'")


def _load_element_name(
    name: str,
    locals_: dict[str, Any],
    globals_: dict[str, Any],
) -> Any:
    """Load an element name with global precedence and one probe per scope."""
    value = globals_.get(name, _MISSING_NAME)
    if value is not _MISSING_NAME:
        return value
    value = locals_.get(name, _MISSING_NAME)
    if value is not _MISSING_NAME:
        return value
    raise RuntimeError(f"undefined name '{name}'")


def _truthy(value: Any) -> bool:
    """Return the Boolean result of truthy during virtual-machine execution."""
    if isinstance(value, TaggedValue):
        value = value.value
    return value is not None and value != 0


def _matches_type_pattern(value: Any, pattern: str) -> bool:
    """Return whether the value matches type pattern."""
    value = unwrap_runtime_value(value)
    if pattern == "Integer":
        return isinstance(value, RuntimeNumber) and value == value.to_integral_value()
    if pattern == "Real":
        return isinstance(value, RuntimeNumber)
    if pattern == "Number":
        return isinstance(value, RuntimeNumber)
    if pattern == "String":
        return isinstance(value, str)
    if pattern == "Err":
        return _is_error_result_value(value)
    if pattern == "Fault":
        return _runtime_object_implements(value, "Fault") or (
            isinstance(value, ObjectValue)
            and (
                value.type_name == "Fault"
                or value.type_name.endswith("Fault")
                or value.type_name.rsplit(".", 1)[-1].endswith("Fault")
            )
        )
    if pattern == "Dict":
        return isinstance(value, DictValue)
    if not isinstance(value, ObjectValue):
        return False
    accepted_names = (
        () if value.runtime_type is None else value.runtime_type.accepted_names
    )
    if value.type_name == pattern or pattern in accepted_names:
        return True
    if value.type_name.rsplit(".", 1)[-1] == pattern or any(
        name.rsplit(".", 1)[-1] == pattern for name in accepted_names
    ):
        return True
    member_name = value.fields.get("name")
    return isinstance(member_name, str) and (
        member_name == pattern or f"{value.type_name}.{member_name}" == pattern
    )


def _resolve_pop_count(spec: object, locals_: dict[str, object]) -> int:
    """Resolve a validated literal or hidden static pop count."""
    value = spec
    if (
        isinstance(spec, tuple)
        and len(spec) == 2
        and spec[0] == "static"
        and isinstance(spec[1], str)
    ):
        value = locals_.get(spec[1])
    if (
        isinstance(value, RuntimeNumber)
        and value.is_finite()
        and value.is_integer()
    ):
        value = int(str(value))
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(
            "invalid bytecode: POP_N count is not a non-negative integer"
        )
    return value


def _resolve_static_rank_variables(
    spec: object,
    locals_: dict[str, Any],
) -> object:
    """Resolve rank-variable placeholders from validated hidden parameters."""
    if isinstance(spec, RankVariable):
        value = locals_.get(spec.name)
        if isinstance(value, RuntimeNumber) and value.is_finite():
            integral = value.to_integral_value()
            if integral == value and 0 < value <= MAX_COMPILE_TIME_RANK:
                return int(integral)
        if type(value) is int and 0 < value <= MAX_COMPILE_TIME_RANK:
            return value
        raise RuntimeError(f"invalid bytecode: unresolved rank variable '${spec.name}'")
    if isinstance(spec, tuple):
        return tuple(_resolve_static_rank_variables(item, locals_) for item in spec)
    return spec


def _matches_cast_type(value: Any, spec: object) -> bool:
    """Return whether the value matches cast type."""
    if not isinstance(spec, tuple) or not spec:
        return False
    kind = spec[0]
    if kind == "none":
        return _is_none_result_value(value)
    if kind == "var":
        return not is_list_like(value)
    if kind == "nominal":
        if not isinstance(spec[1], str):
            return False
        name = spec[1]
        expected_args = spec[2] if len(spec) > 2 else ()
        unwrapped = unwrap_runtime_value(value)
        type_facts = (
            unwrapped.runtime_type.type_facts
            if isinstance(unwrapped, ObjectValue) and unwrapped.runtime_type is not None
            else ()
        )

        # Results are represented by raw successful values, explicit OK values,
        # or concrete Err implementations.  Check the corresponding branch
        # rather than requiring a nonexistent Result wrapper object.
        if name == "Result":
            if len(expected_args) != 2:
                return False
            ok_pattern = _parse_runtime_type_pattern(expected_args[0], type_facts)
            err_pattern = _parse_runtime_type_pattern(expected_args[1], type_facts)
            if isinstance(unwrapped, ObjectValue) and unwrapped.type_name == "OK":
                return _matches_cast_type(
                    unwrapped,
                    ("nominal", "OK", (expected_args[0],)),
                )
            if _is_error_result_value(unwrapped):
                return _runtime_pattern_matches(unwrapped, err_pattern)
            return _runtime_pattern_matches(unwrapped, ok_pattern)

        # Optional and Result success values may use their documented raw
        # representation.  Explicit wrappers retain their inferred type args;
        # raw values are checked directly against the success payload.
        if (
            name == "Some"
            and len(expected_args) == 1
            and not (
                isinstance(unwrapped, ObjectValue) and unwrapped.type_name == "Some"
            )
        ):
            return (
                not _is_none_result_value(unwrapped)
                and len(expected_args) == 1
                and _runtime_pattern_matches(
                    unwrapped,
                    _parse_runtime_type_pattern(expected_args[0], type_facts),
                )
            )
        if (
            name == "OK"
            and len(expected_args) == 1
            and not (isinstance(unwrapped, ObjectValue) and unwrapped.type_name == "OK")
        ):
            return (
                not _is_error_result_value(unwrapped)
                and len(expected_args) == 1
                and _runtime_pattern_matches(
                    unwrapped,
                    _parse_runtime_type_pattern(expected_args[0], type_facts),
                )
            )

        if name == "Dict":
            if not isinstance(unwrapped, DictValue):
                return False
            if not expected_args:
                return True
            if len(expected_args) != 2:
                return False
            key_pattern = _parse_runtime_type_pattern(expected_args[0], type_facts)
            value_pattern = _parse_runtime_type_pattern(expected_args[1], type_facts)
            return all(
                _runtime_pattern_matches(key, key_pattern)
                and _runtime_pattern_matches(item, value_pattern)
                for key, item in unwrapped.items()
            )

        if not _matches_type_pattern(unwrapped, name):
            return False
        if not expected_args:
            return True
        if not isinstance(unwrapped, ObjectValue):
            return False
        target_pattern = _parse_runtime_type_pattern(
            f"{name}[{', '.join(expected_args)}]",
            type_facts,
        )
        if unwrapped.type_name == name:
            actual_pattern = _runtime_value_pattern(unwrapped)
            return actual_pattern is not None and _runtime_pattern_subtype(
                actual_pattern, target_pattern
            )
        projection = _runtime_generic_supertype(unwrapped, name)
        if projection is None:
            return False
        return _runtime_pattern_subtype(
            _parse_runtime_type_pattern(
                f"{name}[{', '.join(projection)}]",
                type_facts,
            ),
            target_pattern,
        )
    if kind == "tagged":
        if len(spec) != 3 or not isinstance(spec[2], tuple):
            return False
        actual_tags = runtime_value_tags(value)
        for item in spec[2]:
            if (
                not isinstance(item, tuple)
                or len(item) != 3
                or not isinstance(item[0], str)
                or type(item[1]) is not int
                or type(item[2]) is not bool
            ):
                return False
            name, depth, absent = item
            present = any(
                str(tag.name) == name and tag.depth == depth and not tag.absent
                for tag in actual_tags
            )
            if absent == present:
                return False
        return _matches_cast_type(unwrap_runtime_value(value), spec[1])
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


def _runtime_generic_supertype(
    value: ObjectValue,
    target_name: str,
) -> tuple[str, ...] | None:
    """Instantiate a constructor's generic supertype projection."""
    runtime_type = value.runtime_type
    if runtime_type is None:
        return None
    for name, templates in runtime_type.generic_supertypes:
        if name != target_name:
            continue
        instantiated: list[str] = []
        for template in templates:
            rendered = template
            for index in range(len(value.type_args) - 1, -1, -1):
                rendered = rendered.replace(f"${index}", value.type_args[index])
            if "$" in rendered:
                return None
            instantiated.append(rendered)
        return tuple(instantiated)
    return None


def _validated_jump_target(target: object, instruction_count: int) -> int:
    """Return a bytecode jump target after strict bounds validation."""
    if type(target) is not int or not 0 <= target <= instruction_count:
        raise RuntimeError(
            f"invalid jump target {target!r} for {instruction_count} instructions"
        )
    return target


def _matches_collection_cast(
    value: Any,
    kind: str,
    rank: int,
    base: object,
) -> bool:
    """Return whether the value matches collection cast.

    Runtime casts must be bounded and non-consuming.  Lazy collections cannot
    be exhaustively validated without changing program behaviour (or hanging
    on an infinite input), so they conservatively fail checked casts.
    """
    value = unwrap_runtime_value(value)
    if rank <= 0:
        return _matches_cast_type(value, base)
    if not is_eager_sequence(value):
        return False
    actual_rank = runtime_collection_rank(value)
    if kind in {"list_exact", "array_exact"} and actual_rank != rank:
        return False
    if kind in {"list_min", "array_min"} and (
        actual_rank is None or actual_rank < rank
    ):
        return False
    if kind in {"list_exact", "array_exact"}:
        return all(
            _matches_collection_cast(item, kind, rank - 1, base) for item in value
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


def _pop_index_values(
    stack: list[Any],
    spec: IndexOperationSpec,
) -> list[tuple[bool, Any, Any, Any]]:
    """Pop the runtime values described by an index-operation payload."""
    if spec.selectors == (
        IndexSelectorSpec(False, True, False, False),
    ):
        return [(False, _pop(stack, "index"), None, None)]
    values = iter(_pop_many(stack, spec.value_count))
    selectors = []
    for selector in spec.selectors:
        start = next(values) if selector.has_start else None
        stop = next(values) if selector.has_stop else None
        step = next(values) if selector.has_step else None
        selectors.append((selector.is_slice, start, stop, step))
    return selectors


def _consume_receiver_result(
    receiver: Any,
    result: Any,
    vm: VirtualMachine,
) -> Any:
    """Transfer a consumed receiver's ownership to its extracted result."""
    if not _needs_release(receiver):
        return result
    if isinstance(result, LazyList):
        result.owned_values = (*result.owned_values, receiver)
        return result
    retained = _retain_value(result)
    _release_value(receiver, vm)
    return retained


def _get_index(
    receiver: Any,
    spec: IndexOperationSpec,
    selectors: list[tuple[bool, Any, Any, Any]],
    vm: VirtualMachine,
) -> Any:
    """Find the index for get during VM execution."""
    if len(selectors) == 1 and not selectors[0][0]:
        selection = _selection_positions(receiver, selectors[0][1], vm)
        if selection is not None:
            return _gather_selection(receiver, selection)
    if len(selectors) > 1 and all(not item[0] for item in selectors):
        grouped_update = spec.grouped_update
        if grouped_update:
            _validate_distinct_selection_indices(receiver, selectors)
        if is_list_like(receiver) and not is_eager_sequence(receiver):
            result = _index_many_lazy(receiver, tuple(item[1] for item in selectors))
        else:
            result = [_index_path(receiver, item[1]) for item in selectors]
        if grouped_update and isinstance(receiver, str):
            return "".join(result)
        return result
    result = receiver
    for is_slice, start, stop, step in selectors:
        if is_slice:
            result = _slice_value(result, start, stop, step)
        else:
            result = _index_path(result, start)
    return result


def _set_index(
    receiver: Any,
    spec: IndexOperationSpec,
    selectors: list[tuple[bool, Any, Any, Any]],
    value: Any,
    *,
    in_place: bool = False,
    vm: VirtualMachine,
) -> Any:
    """Update index during VM execution."""
    grouped_update = spec.grouped_update
    if len(selectors) == 1 and not selectors[0][0]:
        selection = _selection_positions(receiver, selectors[0][1], vm)
        if selection is not None:
            if grouped_update and len({_selection_identity(item) for item in selection}) != len(selection):
                raise RuntimeError(
                    "whole-selection augmented assignment contains duplicate indices"
                )
            return _set_selected_positions(
                receiver,
                selection,
                value,
                grouped_update=grouped_update,
                in_place=in_place,
            )
    if len(selectors) == 1 and selectors[0][0]:
        _, start, stop, step = selectors[0]
        return _set_slice_value(receiver, start, stop, step, value, in_place=in_place)
    if len(selectors) != 1:
        if all(not item[0] for item in selectors):
            if grouped_update:
                _validate_distinct_selection_indices(receiver, selectors)
                replacements = value
            else:
                replacements = [value] * len(selectors)
            return _set_many_indices(
                receiver, selectors, replacements, in_place=in_place
            )
        raise RuntimeError("indexed assignment requires non-slice indices")
    return _set_index_path(receiver, selectors[0][1], value, in_place=in_place)


def _boolean_mask_values(selector: Any) -> list[Any] | None:
    """Return a reified Boolean mask payload, or ``None`` for another selector."""
    if not isinstance(selector, TaggedValue):
        return None
    if not any(
        tag.name == "boolean" and not tag.absent and tag.depth == 1
        for tag in selector.tags
    ):
        return None
    return list(selector.value)


def _selection_positions(
    receiver: Any,
    selector: Any,
    vm: VirtualMachine,
) -> list[Any] | None:
    """Resolve a mask or predicate selector to concrete list positions or keys."""
    mask = _boolean_mask_values(selector)
    if mask is not None:
        if isinstance(receiver, dict):
            return None
        if not (isinstance(receiver, str) or is_list_like(receiver)):
            raise RuntimeError("Boolean masks require a list or string receiver")
        if not is_eager_sequence(receiver) and not isinstance(receiver, str):
            receiver = list(receiver)
        if len(mask) > len(receiver):
            raise RuntimeError("Boolean mask is longer than the indexed value")
        return [index for index, flag in enumerate(mask) if _truthy(flag)]
    if is_list_like(selector):
        return _sequence_selection_positions(receiver, selector)
    if not isinstance(selector, (FunctionValue, OverloadedFunctionValue)):
        return None
    if isinstance(receiver, dict):
        selected: list[Any] = []
        for key, item in receiver.items():
            result = vm.call_value(selector, [key, item])
            if len(result) != 1:
                raise RuntimeError("dictionary selector must return one Boolean value")
            if _truthy(result[0]):
                selected.append(key)
        return selected
    if isinstance(receiver, str) or is_list_like(receiver):
        selected = []
        for index, item in enumerate(receiver):
            result = vm.call_value(selector, [item])
            if len(result) != 1:
                raise RuntimeError("selector must return one Boolean value")
            if _truthy(result[0]):
                selected.append(index)
        return selected
    raise RuntimeError("function selectors require a list, string, or dictionary")


def _selection_identity(value: Any) -> Any:
    """Return a hashable identity for scalar or multidimensional positions."""
    if _is_path(value):
        return tuple(_selection_identity(item) for item in value)
    return value


def _sequence_selection_positions(receiver: Any, selector: Any) -> list[Any]:
    """Resolve an Integer+ or Integer++ selector through index-path semantics."""
    requested = list(selector)
    if isinstance(receiver, dict):
        for key in requested:
            _index_one(receiver, key)
        return requested
    if not (isinstance(receiver, str) or is_list_like(receiver)):
        raise RuntimeError(
            "a sequence selector requires a list, string, or dictionary receiver"
        )
    paths = any(_is_path(value) for value in requested)
    if paths and not all(_is_path(value) for value in requested):
        raise RuntimeError("multidimensional selectors require one path per item")
    if paths:
        for path in requested:
            if not path:
                raise RuntimeError("multidimensional index paths cannot be empty")
            _index_path(receiver, path)
        return requested
    if not is_eager_sequence(receiver) and not isinstance(receiver, str):
        positions: list[int] = []
        for value in requested:
            index = _int_index(value)
            if index < 0:
                raise RuntimeError(
                    "lazy list selection does not support negative indices"
                )
            positions.append(index)
        return positions
    length = len(receiver)
    positions = []
    for value in requested:
        index = _int_index(value)
        if not -length <= index < length:
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(index, length),
                )
            )
        positions.append(_normal_index(index, length))
    return positions


def _gather_selection(receiver: Any, positions: list[Any]) -> Any:
    """Gather selected positions while preserving the receiver collection kind."""
    if isinstance(receiver, str):
        return "".join(receiver[index] for index in positions)
    if isinstance(receiver, dict):
        return DictValue((key, receiver[key]) for key in positions)
    if is_eager_sequence(receiver):
        return _copy_eager_list(
            receiver,
            (_index_path(receiver, position) for position in positions),
        )
    if is_list_like(receiver):
        return _index_many_lazy(
            receiver,
            tuple(RuntimeNumber(index) for index in positions),
        )
    raise RuntimeError("value is not selectable")


def _set_selected_positions(
    receiver: Any,
    positions: list[Any],
    value: Any,
    *,
    grouped_update: bool,
    in_place: bool,
) -> Any:
    """Replace values chosen by a mask or predicate selector."""
    if positions and all(_is_path(position) for position in positions):
        return _set_selected_paths(
            receiver,
            positions,
            value,
            grouped_update=grouped_update,
        )
    if isinstance(receiver, dict):
        if isinstance(value, dict):
            if set(value) != set(positions):
                raise RuntimeError(
                    "dictionary selection replacement must contain exactly the selected keys"
                )
            replacements = [value[key] for key in positions]
        elif grouped_update:
            raise RuntimeError(
                "augmented dictionary selection must return a dictionary"
            )
        else:
            replacements = [value] * len(positions)
        updated = type(receiver)(receiver) if isinstance(receiver, DictValue) else dict(receiver)
        for key, replacement in zip(positions, replacements, strict=True):
            updated[key] = replacement
        return updated
    if isinstance(receiver, str):
        if isinstance(value, str):
            replacements = list(value)
            if len(replacements) != len(positions):
                raise RuntimeError("selection assignment replacement length mismatch")
        elif grouped_update:
            raise RuntimeError("augmented string selection must return a string")
        elif isinstance(value, str) and len(value) == 1:
            replacements = [value] * len(positions)
        else:
            replacements = [value] * len(positions)
        if any(not isinstance(item, str) or len(item) != 1 for item in replacements):
            raise RuntimeError("string selection assignment requires characters")
        updated = list(receiver)
        for index, replacement in zip(positions, replacements, strict=True):
            updated[index] = replacement
        return "".join(updated)
    if is_list_like(value):
        replacements = list(value)
        if len(replacements) != len(positions):
            raise RuntimeError("selection assignment replacement length mismatch")
    elif grouped_update:
        raise RuntimeError("augmented list selection must return a list")
    else:
        replacements = [value] * len(positions)
    if is_eager_sequence(receiver):
        updated = (
            receiver
            if in_place
            and isinstance(receiver, ListValue)
            and receiver.refcount == 1
            and _list_ownership_is_trivial(receiver)
            else _copy_eager_list(receiver, skip_indexes=frozenset(positions))
        )
        for index, replacement in zip(positions, replacements, strict=True):
            list.__setitem__(updated, index, replacement)
        _update_list_ownership_after_replacements(updated, replacements)
        return updated
    replacement_by_index = dict(zip(positions, replacements, strict=True))

    def updated_items():
        """Yield a lazy list with selected positions replaced."""
        for index, item in enumerate(receiver):
            yield replacement_by_index.get(index, item)

    return LazyList(updated_items())


def _set_selected_paths(
    receiver: Any,
    paths: list[Any],
    value: Any,
    *,
    grouped_update: bool,
) -> Any:
    """Scatter replacement values through multidimensional index paths."""
    if is_list_like(value):
        replacements = list(value)
        if len(replacements) != len(paths):
            raise RuntimeError("selection assignment replacement length mismatch")
    elif grouped_update:
        raise RuntimeError("augmented path selection must return a list")
    else:
        replacements = [value] * len(paths)
    updated = receiver
    for path, replacement in zip(paths, replacements, strict=True):
        updated = _set_index_path(updated, path, replacement)
    return updated


def _validate_distinct_selection_indices(
    receiver: Any,
    selectors: list[tuple[bool, Any, Any, Any]],
) -> None:
    """Reject repeated positions in a whole-selection augmented assignment."""
    if not (isinstance(receiver, str) or is_list_like(receiver)):
        raise RuntimeError(
            "whole-selection augmented assignment requires a list or string"
        )
    length = len(receiver) if isinstance(receiver, str) or is_eager_sequence(receiver) else None
    normalized: list[int] = []
    for _, index, _, _ in selectors:
        if _is_path(index):
            raise RuntimeError(
                "whole-selection augmented assignment requires scalar indices"
            )
        target = _int_index(index)
        if length is None:
            if target < 0:
                raise RuntimeError(
                    "lazy whole-selection assignment does not support negative indices"
                )
            normalized.append(target)
        else:
            if not -length <= target < length:
                raise PanicSignal(
                    _fault_object(
                        "IndexFault",
                        _index_fault_message(target, length),
                    )
                )
            normalized.append(_normal_index(target, length))
    if len(set(normalized)) != len(normalized):
        raise RuntimeError(
            "whole-selection augmented assignment contains duplicate indices"
        )


def _set_many_indices(
    receiver: Any,
    selectors: list[tuple[bool, Any, Any, Any]],
    value: Any,
    *,
    in_place: bool = False,
) -> Any:
    """Scatter one transformed selection back into its source positions."""
    raw_keys = [item[1] for item in selectors]
    if isinstance(receiver, dict):
        if not is_list_like(value):
            raise RuntimeError("whole-selection dictionary assignment requires a list result")
        replacements = list(value)
        if len(replacements) != len(raw_keys):
            raise RuntimeError("selection assignment replacement length mismatch")
        updated = receiver if in_place else DictValue(receiver)
        for key, replacement in zip(raw_keys, replacements, strict=True):
            updated[key] = replacement
        return updated
    raw_indices = [_int_index(key) for key in raw_keys]
    if isinstance(receiver, str):
        if not isinstance(value, str):
            raise RuntimeError(
                "whole-selection string assignment requires a string result"
            )
        replacements = list(value)
        if len(replacements) != len(raw_indices):
            raise RuntimeError("selection assignment replacement length mismatch")
        normalized = [_normal_index(index, len(receiver)) for index in raw_indices]
        updated = list(receiver)
        for index, replacement in zip(normalized, replacements, strict=True):
            updated[index] = replacement
        return "".join(updated)
    if not is_list_like(value):
        raise RuntimeError("whole-selection list assignment requires a list result")
    replacements = list(value)
    if len(replacements) != len(raw_indices):
        raise RuntimeError("selection assignment replacement length mismatch")
    if is_eager_sequence(receiver):
        normalized = [_normal_index(index, len(receiver)) for index in raw_indices]
        updated = (
            receiver
            if in_place
            and isinstance(receiver, ListValue)
            and receiver.refcount == 1
            and _list_ownership_is_trivial(receiver)
            else _copy_eager_list(receiver, skip_indexes=frozenset(normalized))
        )
        for index, replacement in zip(normalized, replacements, strict=True):
            list.__setitem__(updated, index, replacement)
        _update_list_ownership_after_replacements(updated, replacements)
        return updated
    if is_list_like(receiver):
        replacement_by_index = dict(zip(raw_indices, replacements, strict=True))

        def updated_items():
            """Yield a lazy list with selected positions replaced."""
            seen: set[int] = set()
            for offset, item in enumerate(receiver):
                if offset in replacement_by_index:
                    seen.add(offset)
                    yield replacement_by_index[offset]
                else:
                    yield item
            missing = set(replacement_by_index) - seen
            if missing:
                target = min(missing)
                raise PanicSignal(
                    _fault_object("IndexFault", _index_fault_message(target))
                )

        return LazyList(updated_items())
    raise RuntimeError(
        "whole-selection augmented assignment requires a list or string"
    )


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
    if not isinstance(target, RuntimeNumber):
        raise RuntimeError("lazy list indexing requires a numeric index")
    target_int = _int_index(target)
    if target_int < 0:
        raise RuntimeError("lazy list indexing does not support negative indices")
    return target_int, tail


def _index_one(receiver: Any, index: Any) -> Any:
    """Index one value and project nested tag evidence by one depth."""
    projected_tags = tuple(
        DataTag(tag.name, tag.depth - 1)
        for tag in runtime_value_tags(receiver)
        if not tag.absent and tag.depth > 0
    )
    receiver = unwrap_runtime_value(receiver)

    def finish(result: Any) -> Any:
        """Attach the tags projected from the indexed receiver."""
        return (
            update_runtime_tags(result, add=projected_tags)
            if projected_tags
            else result
        )

    if isinstance(receiver, dict):
        try:
            return finish(receiver[index])
        except KeyError as exc:
            raise PanicSignal(
                _fault_object(
                    "KeyFault",
                    f"dictionary has no key {_format_value(index)}",
                )
            ) from exc
    if isinstance(receiver, (tuple, str, list)) or is_eager_sequence(receiver):
        target = _int_index(index)
        try:
            return finish(receiver[target])
        except IndexError as exc:
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            ) from exc
    if is_list_like(receiver):
        if not isinstance(index, RuntimeNumber):
            raise RuntimeError("lazy list indexing requires a numeric index")
        target = _int_index(index)
        if target < 0:
            raise RuntimeError("lazy list indexing does not support negative indices")
        for offset, item in enumerate(receiver):
            if offset == target:
                return finish(item)
        raise PanicSignal(_fault_object("IndexFault", _index_fault_message(target)))
    raise RuntimeError("value is not indexable")


def _slice_value(receiver: Any, start: Any, stop: Any, step: Any) -> Any:
    """Slice a value while preserving tag depth at the unchanged rank."""
    tags = tuple(tag for tag in runtime_value_tags(receiver) if not tag.absent)
    receiver = unwrap_runtime_value(receiver)

    def finish(result: Any) -> Any:
        """Restore the receiver's same-rank tag evidence on the slice."""
        return update_runtime_tags(result, add=tags) if tags else result

    if _is_path(start) or _is_path(stop):
        return finish(_slice_path(receiver, start, stop, step))
    if not (is_eager_sequence(receiver) or isinstance(receiver, str)):
        if is_list_like(receiver):
            return finish(_slice_lazy(receiver, start, stop, step))
        raise RuntimeError("slicing requires a list or string")
    step_int = 1 if step is None else _int_index(step)
    if step_int == 0:
        raise RuntimeError("slice step cannot be 0")
    length = len(receiver)
    start_int = 0 if start is None else _normal_index(_int_index(start), length)
    stop_int = length - 1 if stop is None else _normal_index(_int_index(stop), length)
    python_stop = stop_int + (1 if step_int > 0 else -1)
    sliced = receiver[start_int:python_stop:step_int]
    if isinstance(receiver, str):
        return finish("".join(sliced))
    return finish(_copy_eager_list(receiver, sliced))


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
    """Slice a multidimensional list path or raise a catchable slice fault."""
    if not is_eager_sequence(receiver):
        raise PanicSignal(
            _fault_object(
                "SliceFault",
                "multidimensional slicing requires a list at every dimension",
            )
        )
    if not (_is_path(start) and _is_path(stop)):
        raise RuntimeError("multidimensional slices need start and stop paths")
    if len(start) != len(stop):
        raise RuntimeError("multidimensional slice bounds must have the same rank")
    if not start:
        return receiver
    step_value = RuntimeNumber(1) if step is None else step
    head = _slice_value(receiver, start[0], stop[0], step_value)
    if len(start) == 1:
        return head
    return [_slice_path(item, start[1:], stop[1:], step_value) for item in head]


def _set_index_path(
    receiver: Any,
    index: Any,
    value: Any,
    *,
    in_place: bool = False,
) -> Any:
    """Update index path during VM execution."""
    if _is_path(index):
        if not index:
            return value
        head, *tail = index
        current = _index_one(receiver, head)
        updated = _set_index_path(current, tail, value)
        return _set_index_one(receiver, head, updated, in_place=in_place)
    return _set_index_one(receiver, index, value, in_place=in_place)


def _set_slice_value(
    receiver: Any,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
    *,
    in_place: bool = False,
) -> Any:
    """Update slice value during VM execution."""
    if _is_path(start) or _is_path(stop):
        raise RuntimeError("multidimensional slice assignment is not implemented")
    if isinstance(receiver, str):
        return _set_string_slice(receiver, start, stop, step, value)
    if is_eager_sequence(receiver):
        return _set_eager_slice(receiver, start, stop, step, value, in_place=in_place)
    if is_list_like(receiver):
        return _set_lazy_slice(receiver, start, stop, step, value)
    raise RuntimeError("value is not slice-assignable")


def _set_eager_slice(
    receiver: Any,
    start: Any,
    stop: Any,
    step: Any,
    value: Any,
    *,
    in_place: bool = False,
) -> list[Any]:
    """Update eager slice during VM execution."""
    indexes = _eager_slice_indexes(len(receiver), start, stop, step)
    if is_list_like(value):
        replacements = list(value)
        if len(replacements) != len(indexes):
            raise RuntimeError("slice assignment replacement length mismatch")
    else:
        replacements = [value] * len(indexes)
    updated = (
        receiver
        if in_place
        and isinstance(receiver, ListValue)
        and receiver.refcount == 1
        and _list_ownership_is_trivial(receiver)
        else _copy_eager_list(
            receiver,
            skip_indexes=frozenset(indexes),
        )
    )
    for index, replacement in zip(indexes, replacements, strict=True):
        list.__setitem__(updated, index, replacement)
    _update_list_ownership_after_replacements(updated, replacements)
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
    return list(range(length)[start_int:python_stop:step_int])


def _copy_eager_list(
    receiver: Any,
    values: Iterable[Any] | None = None,
    *,
    skip_indexes: frozenset[int] = frozenset(),
) -> list[Any]:
    """Copy an eager list while preserving metadata and child ownership."""
    source = receiver if values is None else values
    if not isinstance(receiver, ListValue):
        return list(source)
    copied = ListValue(source, runtime_rank=receiver.runtime_rank)
    copied._ownership_trivial = receiver._ownership_trivial
    if copied._ownership_trivial is not True:
        for index, item in enumerate(copied):
            if index not in skip_indexes:
                _retain_value(item)
    return copied


def _update_list_ownership_after_replacements(
    value: list[Any],
    replacements: Iterable[Any],
) -> None:
    """Keep a known-trivial list cache valid after scalar replacements."""
    if not isinstance(value, ListValue) or value._ownership_trivial is not True:
        return
    if any(_needs_release(item) for item in replacements):
        value._ownership_trivial = False


def _set_index_one(
    receiver: Any,
    index: Any,
    value: Any,
    *,
    in_place: bool = False,
) -> Any:
    """Update index one during VM execution."""
    if isinstance(receiver, dict):
        if (
            in_place
            and isinstance(receiver, DictValue)
            and receiver.refcount == 1
            and _dict_ownership_is_trivial(receiver)
        ):
            updated = receiver
        else:
            updated = (
                type(receiver)(receiver)
                if isinstance(receiver, DictValue)
                else dict(receiver)
            )
            if isinstance(updated, DictValue):
                updated._ownership_trivial = receiver._ownership_trivial
                if updated._ownership_trivial is not True:
                    for key, item in updated.items():
                        if key != index:
                            _retain_value(item)
        if isinstance(updated, DictValue):
            dict.__setitem__(updated, index, value)
            if updated._ownership_trivial is True and _needs_release(value):
                updated._ownership_trivial = False
        else:
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
        return receiver[:normalized_target] + value + receiver[normalized_target + 1 :]
    if is_eager_sequence(receiver):
        target = _int_index(index)
        if not -len(receiver) <= target < len(receiver):
            raise PanicSignal(
                _fault_object(
                    "IndexFault",
                    _index_fault_message(target, len(receiver)),
                )
            )
        normalized_target = _normal_index(target, len(receiver))
        updated = (
            receiver
            if in_place
            and isinstance(receiver, ListValue)
            and receiver.refcount == 1
            and _list_ownership_is_trivial(receiver)
            else _copy_eager_list(
                receiver,
                skip_indexes=frozenset((normalized_target,)),
            )
        )
        list.__setitem__(updated, target, value)
        _update_list_ownership_after_replacements(updated, (value,))
        return updated
    raise RuntimeError("value is not index-assignable")


def _is_path(value: Any) -> bool:
    """Return whether the value is path."""
    return isinstance(value, list)


def _int_index(value: Any) -> int:
    """Find the index for int during VM execution."""
    value = unwrap_runtime_value(value)
    if isinstance(value, RuntimeNumber) and value == value.to_integral_value():
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
            return [_optional_safe_get_field(item, field, vm) for item in receiver]
        return LazyList(_optional_safe_get_field(item, field, vm) for item in receiver)

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
    if isinstance(receiver, ObjectValue):
        try:
            return receiver.fields[field]
        except KeyError as exc:
            raise RuntimeError(f"{receiver.type_name} has no field '{field}'") from exc
    if isinstance(receiver, dict):
        try:
            return receiver[field]
        except KeyError as exc:
            raise RuntimeError(f"record has no field '{field}'") from exc
    if is_list_like(receiver):
        if is_eager_sequence(receiver):
            return [_get_field(item, field) for item in receiver]
        return LazyList(_get_field(item, field) for item in receiver)
    try:
        return getattr(receiver, field)
    except AttributeError as exc:
        raise RuntimeError(f"value has no field '{field}'") from exc


def _set_field(
    receiver: Any,
    field: str,
    value: Any,
    *,
    in_place: bool = False,
) -> Any:
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
        if (
            in_place
            and isinstance(receiver, DictValue)
            and receiver.refcount == 1
            and _dict_ownership_is_trivial(receiver)
        ):
            fields = receiver
        else:
            fields = (
                type(receiver)(receiver)
                if isinstance(receiver, DictValue)
                else dict(receiver)
            )
            if isinstance(fields, DictValue):
                fields._ownership_trivial = receiver._ownership_trivial
                if fields._ownership_trivial is not True:
                    for key, item in fields.items():
                        if key != field:
                            _retain_value(item)
        if isinstance(fields, DictValue):
            dict.__setitem__(fields, field, value)
            if fields._ownership_trivial is True and _needs_release(value):
                fields._ownership_trivial = False
        else:
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
    if isinstance(arg, RuntimeNumber):
        rendered = _format_value(arg)
    elif isinstance(arg, FunctionCode):
        rendered = f"<function {_function_name(arg)}>"
    elif isinstance(arg, FunctionSetCode):
        rendered = f"<function set {len(arg.overloads)} overload(s)>"
    else:
        rendered = repr(arg)
    return f"{name} {rendered}"


def _show_overload_inputs(overloads: tuple[BuiltinOverload, ...]) -> list[str]:
    """Compute show overload inputs during VM execution."""
    return [
        "(" + ", ".join(str(param) for param in overload.signature.params) + ")"
        for overload in overloads
    ]


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
    """Format one value compactly for VM diagnostics."""
    if isinstance(value, FunctionValue):
        return f"<{_function_name(value.code)}/{len(value.code.params)}>"
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
    if isinstance(value, (list, tuple)):
        opening, closing = ("[", "]") if isinstance(value, list) else ("(", ")")
        preview = [
            _format_value(item)
            for item in value[:DIAGNOSTIC_LIST_PREVIEW_LIMIT]
        ]
        if len(value) > DIAGNOSTIC_LIST_PREVIEW_LIMIT:
            preview.append("...")
        return opening + ", ".join(preview) + closing
    if isinstance(value, dict):
        items = []
        for index, (key, item) in enumerate(value.items()):
            if index >= DIAGNOSTIC_LIST_PREVIEW_LIMIT:
                items.append("...")
                break
            items.append(f"{_format_value(key)}: {_format_value(item)}")
        return "{" + ", ".join(items) + "}"
    return format_runtime_value(
        value,
        quote_strings=True,
        lazy_preview_limit=DIAGNOSTIC_LIST_PREVIEW_LIMIT,
    )


def _runtime_type_name(value: Any) -> str:
    """Return the canonical name for runtime type during VM execution."""
    value = unwrap_runtime_value(value)
    if isinstance(value, RuntimeNumber):
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
        return object_type_name(value)
    return type(value).__name__


def _lift_common_collection_tags(
    values: list[Any],
) -> tuple[list[Any], tuple[DataTag, ...]]:
    """Lift tags shared by every collection item to one deeper tag fact."""
    if not values:
        return values, ()
    common = set(runtime_value_tags(values[0]))
    for value in values[1:]:
        common.intersection_update(runtime_value_tags(value))
        if not common:
            return values, ()
    if not common:
        return values, ()
    ordered = tuple(sorted(common))
    cleaned = [update_runtime_tags(value, remove=ordered) for value in values]
    lifted = tuple(
        DataTag(tag.name, tag.depth + 1) for tag in ordered if not tag.absent
    )
    return cleaned, lifted



def _apply_cached_runtime_return_tags(
    values: tuple[Any, ...] | list[Any],
    deltas: tuple[tuple[tuple[DataTag, ...], tuple[DataTag, ...]], ...],
) -> tuple[Any, ...]:
    """Apply pre-split built-in return tags without hashing metadata per call."""
    if len(values) != len(deltas):
        return tuple(values)
    if len(values) == 1:
        additions, removals = deltas[0]
        return (
            update_runtime_tags(
                values[0],
                add=additions,
                remove=removals,
            ),
        )
    return tuple(
        update_runtime_tags(value, add=additions, remove=removals)
        for value, (additions, removals) in zip(values, deltas, strict=True)
    )


def _function_return_multiplicity(code: FunctionCode) -> int:
    """Return the statically reified number of values produced by a function."""
    if code.return_count is not None:
        return code.return_count
    widths = (
        len(code.return_tag_specs),
        len(code.return_collection_ranks),
        len(code.return_tags),
    )
    multiplicity = max(widths, default=0)
    if multiplicity == 0 and code.name == "<main>":
        return 0
    return multiplicity


def _prepared_call_needs_return_contracts(code: FunctionCode) -> bool:
    """Return whether a prepared leaf must apply post-execution return metadata."""
    if any(code.return_tags) or any(
        rank is not None for rank in code.return_collection_ranks
    ):
        return True

    def structurally_plain(spec: object) -> bool:
        """Return whether a contract only removes impossible top-level wrappers."""
        return (
            isinstance(spec, tuple)
            and bool(spec)
            and spec[0] in {"any", "none", "nominal"}
        )

    return any(not structurally_plain(spec) for spec in code.return_tag_specs)


def _validated_tag_parent_mapping(
    tag_parents: tuple[tuple[str, str], ...],
) -> dict[str, str]:
    """Validate program-level variant-parent metadata before execution."""
    mapping = dict(tag_parents)
    if len(mapping) != len(tag_parents):
        raise RuntimeError("invalid bytecode: duplicate variant tag parent metadata")
    if any(variant == parent for variant, parent in tag_parents):
        raise RuntimeError("invalid bytecode: variant tag cannot parent itself")
    if any(parent in mapping for parent in mapping.values()):
        raise RuntimeError(
            "invalid bytecode: variant tag parent must be a computed tag"
        )
    return mapping


def _canonicalize_runtime_tag_contracts(
    values: tuple[Any, ...] | list[Any],
    specs: tuple[object, ...],
    tag_parents: dict[str, str],
) -> tuple[Any, ...]:
    """Recursively align runtime tag evidence with static return contracts."""
    if len(values) != len(specs):
        # Some stack-polymorphic functions have a conservative declared return
        # shape whose arity is refined only by the caller. Preserve the result
        # rather than applying a contract to the wrong stack slot.
        return tuple(values)
    return tuple(
        _canonicalize_runtime_value_tag_contract(value, spec, tag_parents)
        for value, spec in zip(values, specs, strict=True)
    )


def _tag_contract_declares_tags(spec: object) -> bool:
    """Return whether one structural contract can add or retain runtime tags."""
    if not isinstance(spec, tuple) or not spec:
        return True
    kind = spec[0]
    if kind == "tagged":
        return True
    if kind == "collection" and len(spec) == 4:
        return _tag_contract_declares_tags(spec[3])
    if kind in {"union", "intersection", "tuple"} and len(spec) == 2:
        return any(_tag_contract_declares_tags(item) for item in spec[1])
    return False


def _canonicalize_runtime_value_tag_contract(
    value: Any,
    spec: object,
    tag_parents: dict[str, str],
) -> Any:
    """Recursively enforce one structural runtime tag contract."""
    if not isinstance(spec, tuple) or not spec or not isinstance(spec[0], str):
        raise RuntimeError("invalid bytecode: malformed runtime tag contract")
    if (
        isinstance(value, ListValue)
        and value._tag_free is True
        and not _tag_contract_declares_tags(spec)
    ):
        return value
    kind = spec[0]
    if kind == "tagged":
        if len(spec) != 3 or not isinstance(spec[2], tuple):
            raise RuntimeError("invalid bytecode: malformed tagged contract")
        declared: list[DataTag] = []
        for item in spec[2]:
            if (
                not isinstance(item, tuple)
                or len(item) != 3
                or not isinstance(item[0], str)
                or type(item[1]) is not int
                or type(item[2]) is not bool
                or item[1] < 0
            ):
                raise RuntimeError("invalid bytecode: malformed tag fact")
            declared.append(DataTag(item[0], item[1], item[2]))
        existing = tuple(runtime_value_tags(value))
        payload = _canonicalize_runtime_value_tag_contract(
            unwrap_runtime_value(value), spec[1], tag_parents
        )
        additions = tuple(tag for tag in declared if not tag.absent)
        declared_keys = {(tag.name, tag.depth) for tag in additions}
        retained = tuple(
            tag
            for tag in existing
            if _runtime_variant_parent_is_retained(tag, declared_keys, tag_parents)
        )
        return update_runtime_tags(payload, add=(*additions, *retained))

    payload = unwrap_runtime_value(value)
    if kind in {"any", "none", "nominal"}:
        return payload
    if kind == "union":
        if len(spec) != 2 or not isinstance(spec[1], tuple) or not spec[1]:
            raise RuntimeError("invalid bytecode: malformed union tag contract")
        branch = next(
            (
                item
                for item in spec[1]
                if _runtime_tag_contract_matches(value, item, require_tags=True)
            ),
            None,
        )
        if branch is None:
            branch = next(
                (
                    item
                    for item in spec[1]
                    if _runtime_tag_contract_matches(value, item, require_tags=False)
                ),
                spec[1][0],
            )
        return _canonicalize_runtime_value_tag_contract(value, branch, tag_parents)
    if kind == "intersection":
        if len(spec) != 2 or not isinstance(spec[1], tuple):
            raise RuntimeError("invalid bytecode: malformed intersection tag contract")
        result = value
        for item in spec[1]:
            result = _canonicalize_runtime_value_tag_contract(result, item, tag_parents)
        return result
    if kind == "tuple":
        if len(spec) != 2 or not isinstance(spec[1], tuple):
            raise RuntimeError("invalid bytecode: malformed tuple tag contract")
        if not isinstance(payload, tuple) or len(payload) != len(spec[1]):
            return payload
        return tuple(
            _canonicalize_runtime_value_tag_contract(item, item_spec, tag_parents)
            for item, item_spec in zip(payload, spec[1], strict=True)
        )
    if kind == "collection":
        if (
            len(spec) != 4
            or not isinstance(spec[1], str)
            or type(spec[2]) is not int
            or spec[2] < 1
        ):
            raise RuntimeError("invalid bytecode: malformed collection tag contract")
        return _canonicalize_runtime_collection_tag_contract(
            payload, spec[1], spec[2], spec[3], tag_parents
        )
    raise RuntimeError(f"invalid bytecode: unknown tag contract kind {kind!r}")


def _canonicalize_runtime_collection_tag_contract(
    value: Any,
    kind: str,
    rank: int,
    base_spec: object,
    tag_parents: dict[str, str],
) -> Any:
    """Canonicalize tags below one collection without consuming lazy inputs."""
    if not is_list_like(value):
        return value

    def item_spec(item: Any) -> object:
        """Return the child contract appropriate for one collection item."""
        if kind in {"list_exact", "array_exact"}:
            return base_spec if rank == 1 else ("collection", kind, rank - 1, base_spec)
        if is_list_like(unwrap_runtime_value(item)):
            next_rank = max(rank - 1, 1)
            return ("collection", kind, next_rank, base_spec)
        return base_spec

    def converted_items() -> Iterable[Any]:
        """Yield collection items with recursively canonicalized evidence."""
        for item in value:
            yield _canonicalize_runtime_value_tag_contract(
                item, item_spec(item), tag_parents
            )

    if isinstance(value, PlannedLazyList) and (
        isinstance(base_spec, tuple)
        and base_spec
        and base_spec[0] in {"any", "none", "nominal"}
    ):
        return value
    if isinstance(value, LazyList):
        return LazyList(converted_items(), runtime_rank=value.runtime_rank)
    converted, lifted_tags = _lift_common_collection_tags(list(converted_items()))
    if isinstance(value, ListValue):
        value[:] = converted
        result: Any = value
    elif isinstance(value, list):
        value[:] = converted
        result = value
    else:
        result = converted
    return update_runtime_tags(result, add=lifted_tags) if lifted_tags else result


def _runtime_tag_contract_matches(
    value: Any,
    spec: object,
    *,
    require_tags: bool,
) -> bool:
    """Check enough shape and tag facts to choose a union contract branch."""
    if not isinstance(spec, tuple) or not spec or not isinstance(spec[0], str):
        return False
    kind = spec[0]
    if kind == "tagged":
        if len(spec) != 3 or not isinstance(spec[2], tuple):
            return False
        if require_tags:
            actual = runtime_value_tags(value)
            for item in spec[2]:
                if not isinstance(item, tuple) or len(item) != 3:
                    return False
                required = DataTag(item[0], item[1])
                present = required in actual
                if bool(item[2]) == present:
                    return False
        return _runtime_tag_contract_matches(
            unwrap_runtime_value(value), spec[1], require_tags=require_tags
        )
    payload = unwrap_runtime_value(value)
    if kind == "any":
        return True
    if kind == "none":
        return _is_none_result_value(payload)
    if kind == "nominal":
        return (
            len(spec) == 2
            and isinstance(spec[1], str)
            and _matches_type_pattern(payload, spec[1])
        )
    if kind == "union":
        return (
            len(spec) == 2
            and isinstance(spec[1], tuple)
            and any(
                _runtime_tag_contract_matches(value, item, require_tags=require_tags)
                for item in spec[1]
            )
        )
    if kind == "intersection":
        return (
            len(spec) == 2
            and isinstance(spec[1], tuple)
            and all(
                _runtime_tag_contract_matches(value, item, require_tags=require_tags)
                for item in spec[1]
            )
        )
    if kind == "tuple":
        return (
            len(spec) == 2
            and isinstance(spec[1], tuple)
            and isinstance(payload, tuple)
            and len(payload) == len(spec[1])
        )
    if kind == "collection":
        return len(spec) == 4 and is_list_like(payload)
    return False


def _canonicalize_frame_return_tag_contracts(
    frame: _Frame,
    specs: tuple[object, ...],
    tag_parents: dict[str, str],
) -> None:
    """Apply a dynamic call's static tag contract to its output stack tail."""
    if not specs:
        return
    if len(frame.stack) < len(specs):
        raise RuntimeError(
            "invalid bytecode: call returned fewer values than its tag contract"
        )
    start = len(frame.stack) - len(specs)
    frame.stack[start:] = _canonicalize_runtime_tag_contracts(
        frame.stack[start:],
        specs,
        tag_parents,
    )


def _canonicalize_runtime_return_tags(
    values: tuple[Any, ...] | list[Any],
    tag_sets: tuple[tuple[DataTag, ...], ...],
    tag_parents: dict[str, str],
) -> tuple[Any, ...]:
    """Make runtime tag evidence agree with a function return contract.

    Ordinary tags not named by the return type are removed. Runtime variants
    are retained only when their declared computed parent is itself retained;
    this lets a function return newly established variant evidence without
    allowing unrelated tags to leak through a broader return type.
    """
    if len(values) != len(tag_sets):
        return tuple(values)
    outputs: list[Any] = []
    for value, declared in zip(values, tag_sets, strict=True):
        additions = tuple(tag for tag in declared if not tag.absent)
        declared_keys = {(tag.name, tag.depth) for tag in additions}
        existing = tuple(runtime_value_tags(value))
        retained_variants = {
            (tag.name, tag.depth)
            for tag in existing
            if _runtime_variant_parent_is_retained(
                tag,
                declared_keys,
                tag_parents,
            )
        }
        removals = tuple(
            tag
            for tag in existing
            if (tag.name, tag.depth) not in declared_keys | retained_variants
        )
        outputs.append(update_runtime_tags(value, add=additions, remove=removals))
    return tuple(outputs)


def _runtime_variant_parent_is_retained(
    tag: DataTag,
    declared: set[tuple[str, int]],
    tag_parents: dict[str, str],
) -> bool:
    """Return whether a runtime variant descends from a retained parent tag."""
    seen: set[str] = set()
    current = tag.name
    while current in tag_parents and current not in seen:
        seen.add(current)
        current = tag_parents[current]
        if (current, tag.depth) in declared:
            return True
    return False


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
