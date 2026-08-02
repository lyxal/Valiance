"""Immutable analysis branches and branch collections."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from itertools import count

import valiance.vtypes as T
from valiance.asts import (
    SourceLocation, TypedCallNode, TypedElementNode, TypedNode,
    TypedTagApplicationNode,
)
from valiance.vtypes.symbols import Symbol

from . import transformations as _ops
from .variables import BranchVariables

_branch_ids = count(1)

class DiagnosticSeverity(Enum):
    ERROR = auto()
    WARNING = auto()

@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    location: SourceLocation | None
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    expected: T.Type | None = None
    actual: T.Type | None = None
    notes: tuple[str, ...] = ()
    help: tuple[str, ...] = ()

class InputMode(Enum):
    """How a branch may satisfy missing element inputs."""

    TOP_LEVEL = auto()
    INFER_INPUTS = auto()
    CYCLE_EXPLICIT_PARAMS = auto()
    NILADIC = auto()

@dataclass(frozen=True)
class AnalysisBranch:
    """One possible analysis path.

    A branch owns the current stack, inferred inputs, branch-specific variables,
    typed AST emitted on this path, and the input sourcing mode.
    """

    stack: T.TypeStack = field(default_factory=T.TypeStack)
    inputs: tuple[T.Type, ...] = ()
    input_names: tuple[Symbol | None, ...] = ()
    variables: BranchVariables = field(default_factory=BranchVariables)
    typed_body: tuple[TypedNode, ...] = ()
    element_tags: frozenset[T.ElementTag] = field(default_factory=frozenset)
    data_element_uses: frozenset[tuple[Symbol, Symbol]] = field(
        default_factory=frozenset
    )
    input_mode: InputMode = InputMode.TOP_LEVEL
    cycle_params: tuple[T.Type, ...] = ()
    atomic_type_vars: frozenset[str] = field(default_factory=frozenset)
    cycle_index: int = 0
    cycle_stack_remaining: int = 0
    cycle_from_top: bool = False
    break_type: T.Type | None = None
    return_stack: T.TypeStack | None = None
    return_exact: bool = False
    errors: tuple[Diagnostic, ...] = ()
    warnings: tuple[Diagnostic, ...] = ()
    origin: int = field(default_factory=lambda: next(_branch_ids))

    @property
    def top(self) -> T.Type | None:
        """Return the top stack type, or `None` for an empty stack."""
        return self.stack[-1] if self.stack else None

    @property
    def failed(self) -> bool:
        """Return whether this analysis path contains an error diagnostic."""
        return bool(self.errors)

    @property
    def terminal(self) -> bool:
        """Return whether this path cannot continue because it contains ``Never``."""
        return self.return_stack is not None or any(_ops._is_never(typ) for typ in self.stack)

    def with_stack(self, stack: T.TypeStack) -> AnalysisBranch:
        """Return a branch with its type stack replaced."""
        return replace(self, stack=stack)

    def push(self, *types: T.Type) -> AnalysisBranch:
        """Return a branch with additional types pushed onto its stack."""
        return replace(self, stack=self.stack.push(*types))

    def pop(self, count: int = 1) -> AnalysisBranch:
        """Return a branch with the requested stack types removed."""
        return replace(self, stack=self.stack.pop(count))

    def with_variables(self, variables: BranchVariables) -> AnalysisBranch:
        """Return a branch with updated branch-local variable facts."""
        return replace(self, variables=variables)

    def with_element_tags(
        self,
        tags: Iterable[T.ElementTag],
    ) -> AnalysisBranch:
        """Return a branch carrying the additional positive element tags."""
        return replace(
            self,
            element_tags=frozenset(
                (*self.element_tags, *(tag for tag in tags if not tag.absent))
            ),
        )

    def with_data_element_uses(
        self,
        uses: Iterable[tuple[Symbol, Symbol]],
    ) -> AnalysisBranch:
        """Return a branch recording additional data-tag element uses."""
        return replace(
            self,
            data_element_uses=frozenset((*self.data_element_uses, *uses)),
        )

    def emit(self, typed_node: TypedNode) -> AnalysisBranch:
        """Append a typed node and accumulate its element-tag effects."""
        element_tags = set(self.element_tags)
        data_element_uses = set(self.data_element_uses)
        applied: T.AppliedOverload | None = None
        if isinstance(typed_node, (TypedElementNode, TypedCallNode)):
            if typed_node.overload is not None:
                applied = typed_node.overload
        elif isinstance(typed_node, TypedTagApplicationNode):
            if typed_node.validator is not None:
                applied = typed_node.validator
        if applied is not None:
            positives = tuple(tag for tag in applied.element_tags if not tag.absent)
            element_tags.update(positives)
            data_names = {
                Symbol(tag.name)
                for param in applied.params
                for tag in _ops.present_data_tags(param)
            }
            data_element_uses.update(
                (data_name, element_tag.name)
                for data_name in data_names
                for element_tag in positives
            )
        return replace(
            self,
            typed_body=(*self.typed_body, typed_node),
            element_tags=frozenset(element_tags),
            data_element_uses=frozenset(data_element_uses),
        )

    def error(
        self,
        message: str,
        location: SourceLocation | None,
        *,
        code: str,
        expected: T.Type | None = None,
        actual: T.Type | None = None,
        notes: tuple[str, ...] = (),
        help: tuple[str, ...] = (),
    ) -> AnalysisBranch:
        """Return a failed branch containing a structured error diagnostic."""
        return replace(
            self,
            errors=(
                *self.errors,
                Diagnostic(
                    code=code,
                    message=message,
                    location=location,
                    expected=expected,
                    actual=actual,
                    notes=notes,
                    help=help,
                ),
            ),
        )

    def warning(
        self,
        message: str,
        location: SourceLocation | None,
        *,
        code: str,
        notes: tuple[str, ...] = (),
    ) -> AnalysisBranch:
        """Return a branch containing a structured warning diagnostic."""
        return replace(
            self,
            warnings=(
                *self.warnings,
                Diagnostic(
                    code=code,
                    message=message,
                    location=location,
                    severity=DiagnosticSeverity.WARNING,
                    notes=notes,
                ),
            ),
        )

    def with_break(self, typ: T.Type | None) -> AnalysisBranch:
        """Return a branch carrying the merged type of a break value."""
        return replace(self, break_type=typ)

    def with_return(
        self,
        stack: T.TypeStack,
        *,
        exact: bool,
    ) -> AnalysisBranch:
        """Return a terminal branch carrying a selected function result stack."""
        return replace(self, return_stack=stack, return_exact=exact)

    def refine_type(self, old: T.Type, new: T.Type) -> AnalysisBranch:
        """Refine an inference variable or a concrete structural branch fact."""
        if (
            isinstance(old, T.VarType)
            and not isinstance(old, T.MetaVarType)
            and old.identity is not None
        ):
            return self
        return replace(
            self,
            stack=_ops._refine_stack(self.stack, old, new),
            return_stack=(
                None
                if self.return_stack is None
                else _ops._refine_stack(self.return_stack, old, new)
            ),
            inputs=tuple(_ops._refine_type(item, old, new) for item in self.inputs),
            variables=self.variables.refine_type(old, new),
            typed_body=_ops._refine_typed_body(self.typed_body, old, new),
            element_tags=frozenset(
                T.ElementTag(
                    tag.name,
                    tuple(_ops._refine_type(arg, old, new) for arg in tag.args),
                    tag.absent,
                )
                for tag in self.element_tags
            ),
            cycle_params=tuple(
                _ops._refine_type(item, old, new) for item in self.cycle_params
            ),
        )

    def refine_input_requirement(
        self, old: T.Type, new: T.Type
    ) -> AnalysisBranch:
        """Propagate a call constraint into enclosing function input facts."""
        return replace(
            self,
            inputs=tuple(
                _ops._refine_input_requirement(item, old, new)
                for item in self.inputs
            ),
            variables=self.variables.refine_input_requirement(old, new),
            cycle_params=tuple(
                _ops._refine_input_requirement(item, old, new)
                for item in self.cycle_params
            ),
        )

    def refine_named_input_requirement(
        self, name: Symbol, old: T.Type, new: T.Type
    ) -> AnalysisBranch:
        """Refine one named explicit input without affecting equal-typed peers."""
        inputs = tuple(
            (
                _ops._refine_input_requirement(item, old, new)
                if index < len(self.input_names) and self.input_names[index] == name
                else item
            )
            for index, item in enumerate(self.inputs)
        )
        parameters = tuple(
            (
                param_name,
                _ops._refine_input_requirement(typ, old, new)
                if param_name == name
                else typ,
            )
            for param_name, typ in self.variables.parameters
        )
        return replace(
            self,
            inputs=inputs,
            variables=replace(self.variables, parameters=parameters),
            cycle_params=tuple(
                _ops._refine_input_requirement(item, old, new)
                if index < len(self.input_names) and self.input_names[index] == name
                else item
                for index, item in enumerate(self.cycle_params)
            ),
        )

    def source_arguments(
        self,
        params: tuple[T.Type, ...],
    ) -> tuple[tuple[T.Type, ...], AnalysisBranch] | None:
        """Pop, infer, or cycle enough values to call an overload."""
        arity = len(params)
        if arity == 0:
            return (), self

        available = min(len(self.stack), arity)
        missing = arity - available
        remaining = T.TypeStack(self.stack.items[: len(self.stack) - available])
        stack_args = self.stack.items[len(self.stack) - available :]

        if missing == 0:
            return stack_args, self.with_stack(remaining)

        match self.input_mode:
            case InputMode.INFER_INPUTS:
                inferred = params[:missing]
                return (
                    inferred + stack_args,
                    replace(
                        self,
                        stack=remaining,
                        inputs=self.inputs + inferred,
                    ),
                )
            case InputMode.CYCLE_EXPLICIT_PARAMS if self.cycle_params:
                cycle_len = len(self.cycle_params)
                if not self.cycle_from_top:
                    cycled = tuple(
                        self.cycle_params[(self.cycle_index + index) % cycle_len]
                        for index in range(missing)
                    )
                    return (
                        cycled + stack_args,
                        replace(
                            self,
                            stack=remaining,
                            cycle_index=(self.cycle_index + missing) % cycle_len,
                        ),
                    )

                initial_count = min(self.cycle_stack_remaining, missing)
                initial_start = self.cycle_stack_remaining - initial_count
                initial_args = self.cycle_params[
                    initial_start : self.cycle_stack_remaining
                ]
                cyclic_count = missing - initial_count
                # Explicit parameters form a conceptual stack in declaration
                # order. Pop right to left, then restore the group to ordinary
                # lower-to-upper call argument order.
                cyclic_popped = tuple(
                    self.cycle_params[
                        (-1 - self.cycle_index - offset) % cycle_len
                    ]
                    for offset in range(cyclic_count)
                )
                cyclic_args = tuple(reversed(cyclic_popped))
                return (
                    cyclic_args + initial_args + stack_args,
                    replace(
                        self,
                        stack=remaining,
                        cycle_index=(self.cycle_index + cyclic_count) % cycle_len,
                        cycle_stack_remaining=initial_start,
                    ),
                )
            case _:
                return None

@dataclass(frozen=True)
class BranchSet:
    """A set of possible analysis branches."""

    branches: tuple[AnalysisBranch, ...] = ()

    @classmethod
    def collect(cls, branches: Iterable[AnalysisBranch]) -> BranchSet:
        """Flatten, diagnose, and deduplicate a collection of analysis branches."""
        unique: list[AnalysisBranch] = []
        seen: set[AnalysisBranch] = set()

        for branch in branches:
            if branch in seen:
                continue

            seen.add(branch)
            unique.append(branch)

        return cls(tuple(unique))

    def __bool__(self) -> bool:
        """Return whether at least one analysis branch survives."""
        return bool(self.branches)

    def __iter__(self) -> Iterator[AnalysisBranch]:
        """Iterate over the surviving analysis branches."""
        return iter(self.branches)

    def __len__(self) -> int:
        """Return the number of surviving analysis branches."""
        return len(self.branches)
