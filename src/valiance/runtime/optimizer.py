"""Extensible bytecode optimisation passes for compiled Valiance programs."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol

from valiance.runtime.bytecode import (
    ExtensionRuleReference,
    FunctionCode,
    FunctionSetCode,
    Instruction,
    ObjectConstructorReference,
    OpCode,
    Program,
    ResolvedElementReference,
    VectorExtensionReference,
    decode_stack_shuffle_spec,
)
from valiance.runtime.runtime_values import RuntimeNumber


class OptimizationError(ValueError):
    """Raised when an optimisation pass receives malformed bytecode."""


_stack_shuffle_spec = partial(decode_stack_shuffle_spec, error_type=OptimizationError)


class OptimizationPass(Protocol):
    """Contract implemented by one ordered whole-program optimisation pass."""

    name: str

    def optimize(self, program: Program) -> Program:
        """Return an optimised replacement for a compiled program."""
        raise NotImplementedError


class FunctionOptimizationPass:
    """Base class for passes that independently rewrite every function body.

    Subclasses implement :meth:`optimize_function`; this base class supplies the
    recursive traversal across all bytecode payloads. Whole-program passes can
    implement :class:`OptimizationPass` directly instead.
    """

    name = "function"

    def optimize(self, program: Program) -> Program:
        """Optimise every nested function in a compiled program."""
        return Program(
            self._optimize_nested_function(program.main),
            program.tag_parents,
        )

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Return an optimised replacement for one function body."""
        raise NotImplementedError

    def _optimize_nested_function(self, function: FunctionCode) -> FunctionCode:
        """Optimise nested functions before rewriting their containing function."""
        instructions = tuple(
            Instruction(
                instruction.op,
                _map_nested_functions(
                    instruction.arg,
                    self._optimize_nested_function,
                ),
            )
            for instruction in function.instructions
        )
        current = replace(function, instructions=instructions)
        optimized = self.optimize_function(current)
        if not isinstance(optimized, FunctionCode):
            raise OptimizationError(
                f"optimisation pass {self.name!r} did not return FunctionCode"
            )
        return optimized


@dataclass(frozen=True, slots=True)
class OptimizationPipeline:
    """Apply an ordered set of whole-program optimisation passes."""

    passes: tuple[OptimizationPass, ...]

    def optimize(self, program: Program) -> Program:
        """Run every configured pass in order over a compiled program."""
        current = program
        for optimization_pass in self.passes:
            current = optimization_pass.optimize(current)
            if not isinstance(current, Program):
                raise OptimizationError(
                    f"optimisation pass {optimization_pass.name!r} did not return "
                    "Program"
                )
        return current


_SCALAR_PARAMETER_DISPATCH_TYPES = frozenset({"Number", "Real", "Integer", "String"})


@dataclass(frozen=True, slots=True)
class ExplicitArgumentOptimizationPass(FunctionOptimizationPass):
    """Replace safe scalar parameter cycling with direct parameter loads.

    The pass handles straight-line functions whose underflowing resolved calls
    target ownership-trivial built-in overloads. It deliberately leaves mixed
    physical/cyclic argument sourcing, lifecycle-bearing values, and control-flow
    joins untouched.
    """

    name: str = "explicit-arguments"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Materialise deterministic cyclic arguments for one function."""
        if not function.cycle_params or not function.params:
            return function
        if len(function.dispatch_types) != len(function.params) or any(
            dispatch_type not in _SCALAR_PARAMETER_DISPATCH_TYPES
            for dispatch_type in function.dispatch_types
        ):
            return function
        if function.param_collection_ranks and (
            len(function.param_collection_ranks) != len(function.params)
            or any(rank != 0 for rank in function.param_collection_ranks)
        ):
            return function
        if _contains_control_flow(function.instructions):
            return function
        if any(
            instruction.op in {OpCode.CYCLE_BEGIN, OpCode.CYCLE_END, OpCode.SOURCE_ARGS}
            for instruction in function.instructions
        ):
            return function

        depth = 0
        cycle_index = 0
        cycle_remaining = len(function.params)
        replacements: list[_Replacement] = []
        for index, instruction in enumerate(function.instructions):
            if instruction.op is OpCode.CALL_RESOLVED_ELEMENT:
                shape = _resolved_builtin_shape(instruction.arg)
                if shape is None:
                    return function
                arity, returns, ownership_trivial = shape
                if depth < arity:
                    if depth or not ownership_trivial:
                        return function
                    names, cycle_index, cycle_remaining = _source_cycle_names(
                        function.params,
                        cycle_index,
                        cycle_remaining,
                        arity,
                    )
                    replacements.append(
                        _Replacement(
                            index,
                            1,
                            tuple(Instruction(OpCode.LOAD_VAR, name) for name in names)
                            + (instruction,),
                        )
                    )
                    depth = returns
                else:
                    depth = depth - arity + returns
                continue

            next_depth = _exact_straight_line_depth(instruction, depth)
            if next_depth is None:
                return function
            depth = next_depth

        if not replacements:
            return function
        instructions = _rewrite_ranges(function.instructions, replacements)
        return replace(function, instructions=instructions)


_PURE_FOLDABLE_BUILTINS = frozenset(
    {
        "+",
        "-",
        "*",
        "/",
        "%",
        "**",
        "double",
        "square",
        "squared",
        "inc",
        "positive?",
        "==",
        "<",
        "<=",
        ">",
        ">=",
        "numeric?",
        "true",
        "false",
    }
)


@dataclass(frozen=True, slots=True)
class ConstantFoldingOptimizationPass(FunctionOptimizationPass):
    """Fold serialisable constants and selected pure resolved built-ins."""

    name: str = "constant-folding"

    def optimize(self, program: Program) -> Program:
        """Fold constants while respecting names bound anywhere in the program."""
        shadowed = _collect_bound_names(program.main)

        def transform(function: FunctionCode) -> FunctionCode:
            """Recursively fold one function with program-wide binding facts."""
            instructions = tuple(
                Instruction(
                    instruction.op,
                    _map_nested_functions(instruction.arg, transform),
                )
                for instruction in function.instructions
            )
            return self._optimize_with_shadowed(
                replace(function, instructions=instructions),
                shadowed,
            )

        return Program(transform(program.main), program.tag_parents)

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Fold constants in one independently supplied function."""
        return self._optimize_with_shadowed(function, _collect_bound_names(function))

    def _optimize_with_shadowed(
        self,
        function: FunctionCode,
        shadowed: set[str],
    ) -> FunctionCode:
        """Fold one function with a closed set of potentially rebound names."""
        instructions = function.instructions
        while True:
            folded = _constant_fold_once(instructions, shadowed)
            if folded == instructions:
                return (
                    function
                    if instructions is function.instructions
                    else replace(
                        function,
                        instructions=instructions,
                    )
                )
            instructions = folded


@dataclass(frozen=True, slots=True)
class SmallFunctionInliningPass(FunctionOptimizationPass):
    """Inline small, stack-closed constant functions at direct call sites."""

    max_bytecode_size: int = 8
    name: str = "small-function-inlining"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Inline constant function bodies without changing frame-local semantics."""
        if self.max_bytecode_size < 1 or _contains_control_flow(function.instructions):
            return function

        store_counts: dict[str, int] = {}
        candidates: dict[str, tuple[int, tuple[Instruction, ...]]] = {}
        instructions = function.instructions
        for index, instruction in enumerate(instructions):
            if instruction.op is OpCode.STORE_VAR and isinstance(instruction.arg, str):
                store_counts[instruction.arg] = store_counts.get(instruction.arg, 0) + 1
            if (
                index + 1 < len(instructions)
                and instruction.op is OpCode.MAKE_FUNCTION
                and isinstance(instruction.arg, FunctionCode)
                and instructions[index + 1].op is OpCode.STORE_VAR
                and isinstance(instructions[index + 1].arg, str)
            ):
                body = _constant_inline_body(
                    instruction.arg,
                    self.max_bytecode_size,
                )
                if body is not None:
                    candidates[instructions[index + 1].arg] = (index + 1, body)

        replacements: list[_Replacement] = []
        for index, instruction in enumerate(instructions):
            if (
                instruction.op is OpCode.CALL
                and index > 0
                and instructions[index - 1].op is OpCode.MAKE_FUNCTION
                and isinstance(instructions[index - 1].arg, FunctionCode)
            ):
                body = _constant_inline_body(
                    instructions[index - 1].arg,
                    self.max_bytecode_size,
                )
                if body is not None:
                    replacements.append(_Replacement(index - 1, 2, body))
                continue

            if instruction.op is not OpCode.CALL_RESOLVED_ELEMENT:
                continue
            reference = instruction.arg
            if not _simple_zero_argument_reference(reference):
                continue
            candidate = candidates.get(reference.name)
            if candidate is None or store_counts.get(reference.name) != 1:
                continue
            definition_index, body = candidate
            if definition_index >= index or reference.name in function.params:
                continue
            replacements.append(_Replacement(index, 1, body))

        if not replacements:
            return function
        return replace(
            function,
            instructions=_rewrite_ranges(instructions, replacements),
        )


@dataclass(frozen=True, slots=True)
class PopNOptimizationPass(FunctionOptimizationPass):
    """Combine adjacent scalar and counted pops into one POP_N instruction."""

    name: str = "pop-n"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Collapse each adjacent pop run while preserving branch targets."""
        instructions = function.instructions
        replacements: list[_Replacement] = []
        index = 0
        while index < len(instructions):
            if instructions[index].op not in {OpCode.POP, OpCode.POP_N}:
                index += 1
                continue
            start = index
            count = 0
            while index < len(instructions):
                current = instructions[index]
                if current.op is OpCode.POP:
                    count += 1
                elif (
                    current.op is OpCode.POP_N
                    and isinstance(current.arg, int)
                    and not isinstance(current.arg, bool)
                    and current.arg >= 0
                ):
                    count += current.arg
                else:
                    break
                index += 1
            if index - start > 1 or instructions[start].op is OpCode.POP:
                replacement = (
                    ()
                    if count == 0
                    else (Instruction(OpCode.POP_N, count),)
                )
                replacements.append(
                    _Replacement(start, index - start, replacement)
                )
        if not replacements:
            return function
        return replace(
            function,
            instructions=_rewrite_ranges(instructions, replacements),
        )


@dataclass(frozen=True, slots=True)
class BytecodePeepholeOptimizationPass(FunctionOptimizationPass):
    """Apply local bytecode simplifications independent of source syntax."""

    name: str = "bytecode-peephole"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Remove dead scalar pushes and fold constant conditional branches."""
        instructions = function.instructions
        while True:
            replacements: list[_Replacement] = []
            index = 0
            while index < len(instructions):
                if index + 1 < len(instructions):
                    first = instructions[index]
                    second = instructions[index + 1]
                    if (
                        first.op is OpCode.PUSH_CONST
                        and _is_scalar_constant(first.arg)
                        and second.op is OpCode.POP
                    ):
                        replacements.append(_Replacement(index, 2, ()))
                        index += 2
                        continue
                    if (
                        first.op is OpCode.PUSH_CONST
                        and _is_scalar_constant(first.arg)
                        and second.op is OpCode.JUMP_IF_FALSE
                    ):
                        target = _jump_target(second.arg, len(instructions))
                        replacement = (
                            ()
                            if _truthy_constant(first.arg)
                            else (Instruction(OpCode.JUMP, target),)
                        )
                        replacements.append(_Replacement(index, 2, replacement))
                        index += 2
                        continue
                if index + 2 < len(instructions):
                    first = instructions[index]
                    middle = instructions[index + 1]
                    last = instructions[index + 2]
                    if (
                        first.op is OpCode.PUSH_CONST
                        and _is_scalar_constant(first.arg)
                        and _is_nonvalidating_tag_update(middle)
                        and last.op is OpCode.JUMP_IF_FALSE
                    ):
                        target = _jump_target(last.arg, len(instructions))
                        replacement = (
                            ()
                            if _truthy_constant(first.arg)
                            else (Instruction(OpCode.JUMP, target),)
                        )
                        replacements.append(_Replacement(index, 3, replacement))
                        index += 3
                        continue
                index += 1

            if not replacements:
                return (
                    function
                    if instructions is function.instructions
                    else replace(
                        function,
                        instructions=instructions,
                    )
                )
            rewritten = _rewrite_ranges(instructions, replacements)
            if rewritten == instructions:
                return (
                    function
                    if instructions is function.instructions
                    else replace(
                        function,
                        instructions=instructions,
                    )
                )
            instructions = rewritten


@dataclass(frozen=True, slots=True)
class StackShuffleOptimizationPass(FunctionOptimizationPass):
    """Canonicalise, compose, and remove redundant physical stack shuffles."""

    name: str = "stack-shuffles"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Simplify stack shuffles when the physical stack depth proves safety."""
        instructions = tuple(
            _canonicalize_shuffle_instruction(instruction)
            for instruction in function.instructions
        )
        while True:
            depths = _guaranteed_stack_depths(instructions)
            replacements: list[_Replacement] = []
            index = 0
            while index < len(instructions):
                instruction = instructions[index]
                if instruction.op is not OpCode.STACK_SHUFFLE:
                    index += 1
                    continue
                mode, prestack, poststack, permutation = _stack_shuffle_spec(
                    instruction.arg
                )
                arity = len(prestack)
                physical = depths[index] >= arity

                if physical and mode == "move" and permutation == tuple(range(arity)):
                    replacements.append(_Replacement(index, 1, ()))
                    index += 1
                    continue
                if physical and mode == "copy" and not poststack:
                    replacements.append(_Replacement(index, 1, ()))
                    index += 1
                    continue
                if (
                    physical
                    and mode == "copy"
                    and len(poststack) == 1
                    and index + 1 < len(instructions)
                    and instructions[index + 1].op is OpCode.POP
                ):
                    replacements.append(_Replacement(index, 2, ()))
                    index += 2
                    continue
                if (
                    physical
                    and mode == "move"
                    and permutation is not None
                    and index + 1 < len(instructions)
                    and instructions[index + 1].op is OpCode.STACK_SHUFFLE
                ):
                    next_mode, next_pre, _next_post, next_permutation = (
                        _stack_shuffle_spec(instructions[index + 1].arg)
                    )
                    if (
                        next_mode == "move"
                        and next_permutation is not None
                        and len(next_pre) == arity
                    ):
                        combined = tuple(
                            permutation[position] for position in next_permutation
                        )
                        if combined == tuple(range(arity)):
                            replacement: tuple[Instruction, ...] = ()
                        else:
                            labels = tuple(str(position) for position in range(arity))
                            replacement = (
                                Instruction(
                                    OpCode.STACK_SHUFFLE,
                                    (
                                        "move",
                                        labels,
                                        tuple(
                                            labels[position] for position in combined
                                        ),
                                    ),
                                ),
                            )
                        replacements.append(_Replacement(index, 2, replacement))
                        index += 2
                        continue
                index += 1

            if not replacements:
                if instructions == function.instructions:
                    return function
                return replace(function, instructions=instructions)
            rewritten = _rewrite_ranges(instructions, replacements)
            if rewritten == instructions:
                return replace(function, instructions=instructions)
            instructions = rewritten


@dataclass(frozen=True, slots=True)
class ControlFlowOptimizationPass(FunctionOptimizationPass):
    """Thread jumps, remove unreachable code, and remove next-instruction jumps."""

    name: str = "control-flow"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Simplify control flow until no further instruction can be removed."""
        instructions = function.instructions
        while True:
            simplified = _thread_jump_targets(instructions)
            simplified = _remove_unreachable(simplified)
            simplified = _remove_redundant_jumps(simplified)
            if simplified == instructions:
                return (
                    function
                    if instructions is function.instructions
                    else replace(
                        function,
                        instructions=instructions,
                    )
                )
            instructions = simplified


DEFAULT_OPTIMIZATION_PIPELINE = OptimizationPipeline(
    (
        ExplicitArgumentOptimizationPass(),
        ConstantFoldingOptimizationPass(),
        SmallFunctionInliningPass(),
        ConstantFoldingOptimizationPass(),
        BytecodePeepholeOptimizationPass(),
        PopNOptimizationPass(),
        StackShuffleOptimizationPass(),
        ControlFlowOptimizationPass(),
    )
)


def optimize_program(
    program: Program,
    *,
    pipeline: OptimizationPipeline = DEFAULT_OPTIMIZATION_PIPELINE,
) -> Program:
    """Run a bytecode optimisation pipeline over a compiled program."""
    return pipeline.optimize(program)


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    count: int
    instructions: tuple[Instruction, ...]


def _map_nested_functions(
    value: object,
    transform: Callable[[FunctionCode], FunctionCode],
) -> object:
    """Apply ``transform`` to every function-code payload inside ``value``."""
    if isinstance(value, FunctionCode):
        return transform(value)
    if isinstance(value, FunctionSetCode):
        return replace(
            value,
            overloads=tuple(transform(overload) for overload in value.overloads),
        )
    if isinstance(value, ObjectConstructorReference):
        return replace(
            value,
            initializer=_map_nested_functions(value.initializer, transform),
        )
    if isinstance(value, ResolvedElementReference):
        return replace(
            value,
            extension=_map_nested_functions(value.extension, transform),
        )
    if isinstance(value, VectorExtensionReference):
        return replace(
            value,
            default=_map_nested_functions(value.default, transform),
            rules=tuple(_map_nested_functions(rule, transform) for rule in value.rules),
            selector=_map_nested_functions(value.selector, transform),
        )
    if isinstance(value, ExtensionRuleReference):
        return replace(
            value,
            function=_map_nested_functions(value.function, transform),
        )
    if isinstance(value, tuple):
        return tuple(_map_nested_functions(item, transform) for item in value)
    return value


def _collect_bound_names(function: FunctionCode) -> set[str]:
    """Collect parameters and stores from every nested function payload."""
    names = set(function.params)
    for instruction in function.instructions:
        if instruction.op is OpCode.STORE_VAR and isinstance(instruction.arg, str):
            names.add(instruction.arg)
        _collect_bound_names_from_value(instruction.arg, names)
    return names


def _collect_bound_names_from_value(value: object, names: set[str]) -> None:
    """Add bound names from nested bytecode payloads to ``names``."""
    if isinstance(value, FunctionCode):
        names.update(_collect_bound_names(value))
    elif isinstance(value, FunctionSetCode):
        for overload in value.overloads:
            names.update(_collect_bound_names(overload))
    elif isinstance(value, ObjectConstructorReference):
        _collect_bound_names_from_value(value.initializer, names)
    elif isinstance(value, ResolvedElementReference):
        _collect_bound_names_from_value(value.extension, names)
    elif isinstance(value, VectorExtensionReference):
        _collect_bound_names_from_value(value.default, names)
        for rule in value.rules:
            _collect_bound_names_from_value(rule, names)
        _collect_bound_names_from_value(value.selector, names)
    elif isinstance(value, ExtensionRuleReference):
        _collect_bound_names_from_value(value.function, names)
    elif isinstance(value, tuple):
        for item in value:
            _collect_bound_names_from_value(item, names)


def _constant_fold_once(
    instructions: tuple[Instruction, ...],
    shadowed: set[str],
) -> tuple[Instruction, ...]:
    """Perform one non-overlapping sweep of constant folds."""
    replacements: list[_Replacement] = []
    index = 0
    while index < len(instructions):
        instruction = instructions[index]
        if instruction.op is OpCode.CALL_RESOLVED_ELEMENT:
            folded = _fold_resolved_call(instructions, index, shadowed)
            if folded is not None:
                replacements.append(folded)
                index += 1
                continue
        if instruction.op is OpCode.BUILD_TUPLE:
            folded = _fold_tuple_builder(instructions, index)
            if folded is not None:
                replacements.append(folded)
                index += 1
                continue
        if instruction.op is OpCode.BUILD_STRING:
            folded = _fold_string_builder(instructions, index)
            if folded is not None:
                replacements.append(folded)
                index += 1
                continue
        index += 1
    if not replacements:
        return instructions
    return _rewrite_ranges(instructions, replacements)


def _fold_resolved_call(
    instructions: tuple[Instruction, ...],
    call_index: int,
    shadowed: set[str],
) -> _Replacement | None:
    """Fold one pure resolved call whose complete input is literal bytecode."""
    reference = instructions[call_index].arg
    if not isinstance(reference, ResolvedElementReference):
        return None
    if reference.name in shadowed or reference.name not in _PURE_FOLDABLE_BUILTINS:
        return None
    if not _simple_resolved_reference(reference):
        return None

    from valiance.elements.builtins import RuntimeContext, runtime_elements

    element = runtime_elements().get(reference.name)
    if element is None or not 0 <= reference.overload_index < len(element.definitions):
        return None
    overload = element.definitions[reference.overload_index]
    if overload.implementation is None or not overload.ownership_trivial:
        return None
    arity = len(overload.signature.params)
    start = call_index - arity
    if start < 0:
        return None
    inputs = instructions[start:call_index]
    if len(inputs) != arity or any(item.op is not OpCode.PUSH_CONST for item in inputs):
        return None
    values = tuple(item.arg for item in inputs)
    if not all(_is_scalar_constant(value) for value in values):
        return None

    def unavailable(*_args: object, **_kwargs: object) -> list[object]:
        """Reject pure-fold candidates that attempt any runtime-only service."""
        raise RuntimeError("compile-time builtin attempted a runtime service")

    context = RuntimeContext(
        lambda _value: None,
        unavailable,
        call_overload=unavailable,
    )
    try:
        result = overload.implementation(values, context)
    except Exception:
        return None
    if not isinstance(result, tuple) or not all(
        _is_serializable_constant(value) for value in result
    ):
        return None
    folded = tuple(Instruction(OpCode.PUSH_CONST, value) for value in result)
    if overload.runtime_return_tags:
        if len(result) != 1 or len(overload.runtime_return_tag_deltas) != 1:
            return None
        added, removed = overload.runtime_return_tag_deltas[0]
        folded += (
            Instruction(
                OpCode.VALIDATE_TAG,
                (
                    "#constant-fold",
                    None,
                    tuple((tag.name, tag.depth) for tag in added),
                    tuple((tag.name, tag.depth) for tag in removed),
                ),
            ),
        )
    return _Replacement(start, arity + 1, folded)


def _fold_tuple_builder(
    instructions: tuple[Instruction, ...],
    index: int,
) -> _Replacement | None:
    """Fold a tuple builder fed only by serialisable constants."""
    count = instructions[index].arg
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    start = index - count
    if start < 0:
        return None
    inputs = instructions[start:index]
    if any(item.op is not OpCode.PUSH_CONST for item in inputs):
        return None
    values = tuple(item.arg for item in inputs)
    if not all(_is_serializable_constant(value) for value in values):
        return None
    return _Replacement(
        start,
        count + 1,
        (Instruction(OpCode.PUSH_CONST, values),),
    )


def _fold_string_builder(
    instructions: tuple[Instruction, ...],
    index: int,
) -> _Replacement | None:
    """Fold a string interpolation whose expressions are constants."""
    template = instructions[index].arg
    if not isinstance(template, tuple) or not all(
        part is None or isinstance(part, str) for part in template
    ):
        return None
    count = sum(part is None for part in template)
    start = index - count
    if start < 0:
        return None
    inputs = instructions[start:index]
    if any(item.op is not OpCode.PUSH_CONST for item in inputs):
        return None
    values = tuple(item.arg for item in inputs)
    if not all(_is_scalar_constant(value) for value in values):
        return None

    from valiance.runtime.runtime_values import format_runtime_value

    value_iter = iter(values)
    pieces = [
        format_runtime_value(next(value_iter)) if part is None else part
        for part in template
    ]
    return _Replacement(
        start,
        count + 1,
        (Instruction(OpCode.PUSH_CONST, "".join(pieces)),),
    )


def _simple_resolved_reference(reference: ResolvedElementReference) -> bool:
    """Return whether a resolved call has ordinary scalar call semantics."""
    return not (
        reference.vectorised
        or reference.vectorised_depths
        or reference.vectorised_target_ranks
        or reference.return_collection_ranks
        or reference.return_tags
        or reference.return_tag_specs
        or reference.type_args
        or reference.static_values
        or reference.arity_override is not None
        or reference.consumed_override is not None
        or reference.multidispatch
        or reference.extension is not None
    )


def _simple_zero_argument_reference(value: object) -> bool:
    """Return whether ``value`` is a direct zero-argument resolved call."""
    return (
        isinstance(value, ResolvedElementReference)
        and value.overload_index == 0
        and _simple_resolved_reference(value)
    )


def _resolved_builtin_shape(value: object) -> tuple[int, int, bool] | None:
    """Return arity, return count, and lifecycle triviality for one builtin call."""
    if not isinstance(value, ResolvedElementReference):
        return None
    if not _simple_resolved_reference(value):
        return None

    from valiance.elements.builtins import runtime_elements

    element = runtime_elements().get(value.name)
    if element is None or not 0 <= value.overload_index < len(element.definitions):
        return None
    overload = element.definitions[value.overload_index]
    if overload.implementation is None:
        return None
    return (
        len(overload.signature.params),
        len(overload.signature.returns),
        overload.ownership_trivial,
    )


def _source_cycle_names(
    params: tuple[str, ...],
    cycle_index: int,
    cycle_remaining: int,
    arity: int,
) -> tuple[tuple[str, ...], int, int]:
    """Mirror ``_Frame.source_args`` for an empty physical stack."""
    if arity == 0:
        return (), cycle_index, cycle_remaining
    if arity == 1:
        return (
            (params[cycle_index % len(params)],),
            (cycle_index + 1) % len(params),
            0,
        )
    initial_count = min(cycle_remaining, arity)
    initial_start = cycle_remaining - initial_count
    initial = params[initial_start:cycle_remaining]
    missing = arity - initial_count
    cycled = tuple(
        params[(cycle_index + offset) % len(params)] for offset in range(missing)
    )
    next_index = (cycle_index + missing) % len(params) if params else cycle_index
    return cycled + initial, next_index, initial_start


def _constant_inline_body(
    function: FunctionCode,
    maximum: int,
) -> tuple[Instruction, ...] | None:
    """Return a safe constant body for frame-free inlining."""
    if (
        function.params
        or function.cycle_params
        or function.accepts_stack_inputs
        or function.element_tags
        or function.recursive
        or function.multi
        or function.dispatch_types
        or any(function.return_tags)
        or function.return_collection_ranks
        or function.param_collection_ranks
        or not function.instructions
        or len(function.instructions) > maximum
        or function.instructions[-1].op is not OpCode.RETURN
    ):
        return None
    body = function.instructions[:-1]
    if any(
        instruction.op is not OpCode.PUSH_CONST
        or not _is_serializable_constant(instruction.arg)
        for instruction in body
    ):
        return None
    return body


def _is_scalar_constant(value: object) -> bool:
    """Return whether a value has immutable, lifecycle-free runtime semantics."""
    return value is None or isinstance(value, (int, RuntimeNumber, str))


def _is_serializable_constant(value: object) -> bool:
    """Return whether a folded value is accepted by bytecode serialization."""
    if _is_scalar_constant(value):
        return True
    return isinstance(value, tuple) and all(
        _is_serializable_constant(item) for item in value
    )


def _is_nonvalidating_tag_update(instruction: Instruction) -> bool:
    """Return whether a tag opcode only applies static add/remove metadata."""
    if instruction.op is not OpCode.VALIDATE_TAG:
        return False
    value = instruction.arg
    return (
        isinstance(value, tuple)
        and len(value) == 4
        and value[1] is None
        and isinstance(value[2], tuple)
        and isinstance(value[3], tuple)
    )


def _truthy_constant(value: object) -> bool:
    """Apply the VM's scalar truthiness rule at compile time."""
    return value is not None and value != 0


def _contains_control_flow(instructions: tuple[Instruction, ...]) -> bool:
    """Return whether an instruction stream has non-linear execution."""
    return any(
        instruction.op
        in {
            OpCode.JUMP,
            OpCode.JUMP_IF_FALSE,
            OpCode.JUMP_IF_MATCH,
            OpCode.MATCH_ERROR,
            OpCode.UNFOLD,
            OpCode.WHILE,
            OpCode.FOREACH,
            OpCode.LOOP_BREAK,
            OpCode.RETURN_SIGNAL,
            OpCode.TRY_BEGIN,
            OpCode.TRY_END,
            OpCode.PANIC,
            OpCode.TRY_UNWRAP,
        }
        for instruction in instructions
    )


def _exact_straight_line_depth(
    instruction: Instruction,
    depth: int,
) -> int | None:
    """Return exact physical depth after one supported straight-line opcode."""
    if instruction.op in {
        OpCode.PUSH_CONST,
        OpCode.LOAD_VAR,
        OpCode.LOAD_VAR_BORROW,
        OpCode.LOAD_ELEMENT,
        OpCode.MAKE_FUNCTION,
        OpCode.MAKE_OBJECT_CONSTRUCTOR,
        OpCode.MAKE_ENUM_MEMBER,
    }:
        return depth + 1
    if instruction.op in {OpCode.STORE_VAR, OpCode.POP}:
        return depth - 1 if depth else None
    if instruction.op in {OpCode.RETURN, OpCode.CYCLE_BEGIN, OpCode.CYCLE_END}:
        return depth
    if instruction.op in {
        OpCode.CHECK_CAST,
        OpCode.CANONICALIZE_TAGS,
        OpCode.VALIDATE_TAG,
    }:
        return depth if depth else None
    if instruction.op is OpCode.BUILD_TUPLE:
        return _builder_depth(depth, instruction.arg, 1)
    if instruction.op is OpCode.BUILD_LIST:
        count = (
            instruction.arg[0]
            if isinstance(instruction.arg, tuple)
            else instruction.arg
        )
        return _builder_depth(depth, count, 1)
    if instruction.op is OpCode.BUILD_RECORD:
        return (
            _builder_depth(depth, len(instruction.arg), 1)
            if isinstance(instruction.arg, tuple)
            else None
        )
    if instruction.op is OpCode.BUILD_DICT:
        return (
            _builder_depth(depth, instruction.arg * 2, 1)
            if isinstance(instruction.arg, int)
            else None
        )
    if instruction.op is OpCode.BUILD_STRING:
        if not isinstance(instruction.arg, tuple):
            return None
        return _builder_depth(depth, sum(part is None for part in instruction.arg), 1)
    return None


def _builder_depth(depth: int, consumed: object, produced: int) -> int | None:
    """Return an exact builder stack effect after validating its count."""
    if (
        not isinstance(consumed, int)
        or isinstance(consumed, bool)
        or consumed < 0
        or depth < consumed
    ):
        return None
    return depth - consumed + produced


def _canonicalize_shuffle_instruction(instruction: Instruction) -> Instruction:
    """Rename stack-shuffle labels to stable short positional identifiers."""
    if instruction.op is not OpCode.STACK_SHUFFLE:
        return instruction
    mode, prestack, poststack, _permutation = _stack_shuffle_spec(instruction.arg)
    mapping: dict[str, str] = {}
    for label in prestack:
        if label is not None and label not in mapping:
            mapping[label] = str(len(mapping))
    canonical_pre = tuple(
        None if label is None else mapping[label] for label in prestack
    )
    canonical_post = tuple(mapping[label] for label in poststack)
    return Instruction(OpCode.STACK_SHUFFLE, (mode, canonical_pre, canonical_post))


def _guaranteed_stack_depths(
    instructions: tuple[Instruction, ...],
) -> tuple[int, ...]:
    """Compute a conservative physical-stack lower bound before each opcode."""
    targets = _control_flow_targets(instructions)
    depths: list[int] = []
    depth = 0
    for index, instruction in enumerate(instructions):
        if index and index in targets:
            depth = 0
        depths.append(depth)
        depth = _minimum_depth_after(instruction, depth)
        if instruction.op in {
            OpCode.JUMP,
            OpCode.RETURN,
            OpCode.RETURN_SIGNAL,
            OpCode.PANIC,
            OpCode.MATCH_ERROR,
            OpCode.LOOP_BREAK,
        }:
            depth = 0
    return tuple(depths)


def _minimum_depth_after(instruction: Instruction, depth: int) -> int:
    """Return a conservative physical-stack lower bound after one opcode."""
    exact = _exact_straight_line_depth(instruction, depth)
    if exact is not None:
        return max(0, exact)
    if instruction.op is OpCode.CALL_RESOLVED_ELEMENT:
        shape = _resolved_builtin_shape(instruction.arg)
        if shape is None:
            return 0
        arity, returns, _trivial = shape
        return max(0, depth - arity) + returns
    if instruction.op is OpCode.SOURCE_ARGS:
        if isinstance(instruction.arg, int) and instruction.arg >= 0:
            return max(depth, instruction.arg)
        return 0
    if instruction.op is OpCode.STACK_SHUFFLE:
        mode, prestack, poststack, _permutation = _stack_shuffle_spec(instruction.arg)
        arity = len(prestack)
        if mode == "copy":
            return depth + len(poststack)
        preserved = sum(label is None for label in prestack)
        return max(0, depth - arity) + preserved + len(poststack)
    return 0


def _rewrite_ranges(
    instructions: tuple[Instruction, ...],
    replacements: list[_Replacement],
) -> tuple[Instruction, ...]:
    """Apply non-overlapping rewrites and retarget absolute control-flow offsets."""
    if not replacements:
        return instructions
    instruction_count = len(instructions)
    targets = _control_flow_targets(instructions)
    accepted: list[_Replacement] = []
    occupied_until = 0
    for replacement in sorted(replacements, key=lambda item: item.start):
        if (
            replacement.count < 1
            or replacement.start < occupied_until
            or replacement.start < 0
            or replacement.start + replacement.count > instruction_count
        ):
            continue
        interior = range(replacement.start + 1, replacement.start + replacement.count)
        if any(index in targets for index in interior):
            continue
        accepted.append(replacement)
        occupied_until = replacement.start + replacement.count
    if not accepted:
        return instructions

    chunks: list[tuple[Instruction, ...]] = [
        (instruction,) for instruction in instructions
    ]
    for replacement in accepted:
        chunks[replacement.start] = replacement.instructions
        for index in range(
            replacement.start + 1,
            replacement.start + replacement.count,
        ):
            chunks[index] = ()

    boundaries = [0]
    for chunk in chunks:
        boundaries.append(boundaries[-1] + len(chunk))

    def remap(target: int) -> int:
        """Map one original instruction boundary into the rewritten stream."""
        return boundaries[target]

    return tuple(
        _retarget_instruction(instruction, instruction_count, remap)
        for chunk in chunks
        for instruction in chunk
    )


def _control_flow_targets(instructions: tuple[Instruction, ...]) -> set[int]:
    """Collect every absolute target encoded in an instruction stream."""
    targets: set[int] = set()
    count = len(instructions)
    for instruction in instructions:
        if instruction.op in {OpCode.JUMP, OpCode.JUMP_IF_FALSE}:
            targets.add(_jump_target(instruction.arg, count))
        elif instruction.op is OpCode.JUMP_IF_MATCH:
            _patterns, target = _match_jump_argument(instruction.arg)
            targets.add(_jump_target(target, count))
        elif instruction.op is OpCode.TRY_BEGIN:
            targets.update(_handler_targets(instruction.arg, count))
    return targets


def _thread_jump_targets(
    instructions: tuple[Instruction, ...],
) -> tuple[Instruction, ...]:
    """Redirect branches through chains of unconditional jumps."""
    count = len(instructions)

    def resolve(target: int) -> int:
        """Follow unconditional jumps without looping on malformed cycles."""
        seen: set[int] = set()
        while target < count and instructions[target].op is OpCode.JUMP:
            if target in seen:
                break
            seen.add(target)
            target = _jump_target(instructions[target].arg, count)
        return target

    return tuple(
        _retarget_instruction(
            instruction,
            count,
            lambda target: resolve(target),
        )
        for instruction in instructions
    )


def _remove_unreachable(
    instructions: tuple[Instruction, ...],
) -> tuple[Instruction, ...]:
    """Remove instructions not reachable through normal or handler control flow."""
    if not instructions:
        return instructions

    reachable: set[int] = set()
    pending = [0]
    while pending:
        index = pending.pop()
        if index == len(instructions) or index in reachable:
            continue
        if index < 0 or index > len(instructions):
            raise OptimizationError(f"invalid instruction target {index}")
        reachable.add(index)
        pending.extend(_successors(index, instructions[index], len(instructions)))

    if len(reachable) == len(instructions):
        return instructions
    return _filter_and_retarget(instructions, reachable)


def _remove_redundant_jumps(
    instructions: tuple[Instruction, ...],
) -> tuple[Instruction, ...]:
    """Remove unconditional jumps whose target is the following instruction."""
    keep = {
        index
        for index, instruction in enumerate(instructions)
        if not (
            instruction.op is OpCode.JUMP
            and _jump_target(instruction.arg, len(instructions)) == index + 1
        )
    }
    if len(keep) == len(instructions):
        return instructions
    return _filter_and_retarget(instructions, keep)


def _successors(
    index: int,
    instruction: Instruction,
    instruction_count: int,
) -> tuple[int, ...]:
    """Return conservative control-flow successors for one instruction."""
    fallthrough = index + 1
    if instruction.op is OpCode.JUMP:
        return (_jump_target(instruction.arg, instruction_count),)
    if instruction.op is OpCode.JUMP_IF_FALSE:
        return (
            fallthrough,
            _jump_target(instruction.arg, instruction_count),
        )
    if instruction.op is OpCode.JUMP_IF_MATCH:
        _patterns, target = _match_jump_argument(instruction.arg)
        return (fallthrough, _jump_target(target, instruction_count))
    if instruction.op is OpCode.TRY_BEGIN:
        handlers = _handler_targets(instruction.arg, instruction_count)
        return (fallthrough, *handlers)
    if instruction.op in {
        OpCode.JUMP,
        OpCode.LOOP_BREAK,
        OpCode.MATCH_ERROR,
        OpCode.PANIC,
        OpCode.RETURN,
        OpCode.RETURN_SIGNAL,
    }:
        return ()
    return (fallthrough,)


def _filter_and_retarget(
    instructions: tuple[Instruction, ...],
    keep: set[int],
) -> tuple[Instruction, ...]:
    """Filter instruction indexes and rewrite every absolute control-flow target."""
    removed = tuple(index for index in range(len(instructions)) if index not in keep)

    def remap(target: int) -> int:
        """Map one old absolute target into the filtered instruction stream."""
        return target - bisect_left(removed, target)

    return tuple(
        _retarget_instruction(instruction, len(instructions), remap)
        for index, instruction in enumerate(instructions)
        if index in keep
    )


def _retarget_instruction(
    instruction: Instruction,
    instruction_count: int,
    remap: Callable[[int], int],
) -> Instruction:
    """Rewrite absolute targets carried by a control-flow instruction."""
    if instruction.op in {OpCode.JUMP, OpCode.JUMP_IF_FALSE}:
        target = _jump_target(instruction.arg, instruction_count)
        return Instruction(instruction.op, remap(target))
    if instruction.op is OpCode.JUMP_IF_MATCH:
        patterns, target = _match_jump_argument(instruction.arg)
        target = _jump_target(target, instruction_count)
        return Instruction(instruction.op, (patterns, remap(target)))
    if instruction.op is OpCode.TRY_BEGIN:
        handlers = _handler_entries(instruction.arg, instruction_count)
        return Instruction(
            instruction.op,
            tuple((type_name, remap(target)) for type_name, target in handlers),
        )
    return instruction


def _jump_target(value: object, instruction_count: int) -> int:
    """Validate and return one absolute bytecode target."""
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= instruction_count
    ):
        raise OptimizationError(f"invalid instruction target {value!r}")
    return value


def _match_jump_argument(value: object) -> tuple[object, object]:
    """Validate the outer shape of a match-jump payload."""
    if not isinstance(value, tuple) or len(value) != 2:
        raise OptimizationError(f"invalid match jump payload {value!r}")
    return value


def _handler_entries(
    value: object,
    instruction_count: int,
) -> tuple[tuple[str | None, int], ...]:
    """Validate and return panic-handler entries from ``TRY_BEGIN``."""
    if not isinstance(value, tuple):
        raise OptimizationError(f"invalid try handler payload {value!r}")
    entries: list[tuple[str | None, int]] = []
    for entry in value:
        if (
            not isinstance(entry, tuple)
            or len(entry) != 2
            or (entry[0] is not None and not isinstance(entry[0], str))
        ):
            raise OptimizationError(f"invalid try handler entry {entry!r}")
        entries.append((entry[0], _jump_target(entry[1], instruction_count)))
    return tuple(entries)


def _handler_targets(value: object, instruction_count: int) -> tuple[int, ...]:
    """Return the absolute targets contained in a panic-handler payload."""
    return tuple(
        target for _type_name, target in _handler_entries(value, instruction_count)
    )
