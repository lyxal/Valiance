"""Extensible bytecode optimisation passes for compiled Valiance programs."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from dataclasses import dataclass, replace
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
)


class OptimizationError(ValueError):
    """Raised when an optimisation pass receives malformed bytecode."""


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
        return Program(self._optimize_nested_function(program.main))

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


@dataclass(frozen=True, slots=True)
class ControlFlowOptimizationPass(FunctionOptimizationPass):
    """Remove unreachable instructions and jumps to the next instruction."""

    name: str = "control-flow"

    def optimize_function(self, function: FunctionCode) -> FunctionCode:
        """Simplify control flow until no further instruction can be removed."""
        instructions = function.instructions
        while True:
            simplified = _remove_unreachable(instructions)
            simplified = _remove_redundant_jumps(simplified)
            if simplified == instructions:
                return function if instructions is function.instructions else replace(
                    function,
                    instructions=instructions,
                )
            instructions = simplified


DEFAULT_OPTIMIZATION_PIPELINE = OptimizationPipeline(
    (ControlFlowOptimizationPass(),)
)


def optimize_program(
    program: Program,
    *,
    pipeline: OptimizationPipeline = DEFAULT_OPTIMIZATION_PIPELINE,
) -> Program:
    """Run a bytecode optimisation pipeline over a compiled program."""
    return pipeline.optimize(program)


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
            rules=tuple(
                _map_nested_functions(rule, transform) for rule in value.rules
            ),
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
