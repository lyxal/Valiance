"""Immutable stack state and stack-application result records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from valiance.vtypes.nodes import Overload, Specificity, Type

if TYPE_CHECKING:
    from valiance.vtypes.context import Context


@dataclass(frozen=True)
class TypeStack:
    """Immutable type stack used while checking stack effects."""

    items: tuple[Type, ...] = ()

    def __iter__(self):
        """Iterate over values stored by this type stack."""
        return iter(self.items)

    def __len__(self) -> int:
        """Return the number of values stored by this type stack."""
        return len(self.items)

    def __getitem__(self, index: int):
        """Return an item selected from this type stack."""
        return self.items[index]

    def __bool__(self) -> bool:
        """Return whether this type stack contains a meaningful value."""
        return bool(self.items)

    def push(self, *types: Type) -> TypeStack:
        """Return a new stack with ``types`` appended on top."""
        return TypeStack(self.items + tuple(types))

    def pop(self, n: int = 1) -> TypeStack:
        """Return a new stack with the top ``n`` items removed"""
        if n == 0:
            return self
        if n > len(self):
            raise ValueError("Cannot pop more items than are on the stack")
        return TypeStack(self.items[:-n])

    def apply(
        self,
        overloads: Iterable[Overload],
        ctx: Context | None = None,
        *,
        infer_missing: bool = False,
    ) -> StackApplication | None:
        """Choose and apply one overload candidate to this stack."""
        from valiance.vtypes.relations import apply_overloads_to_stack

        return apply_overloads_to_stack(
            overloads,
            self,
            ctx,
            infer_missing=infer_missing,
        )

    def apply_one(
        self,
        overload: Overload,
        ctx: Context | None = None,
        *,
        infer_missing: bool = False,
    ) -> StackApplication | None:
        """Apply one known overload candidate to this stack."""
        from valiance.vtypes.relations import apply_overload_to_stack

        return apply_overload_to_stack(
            overload,
            self,
            ctx,
            infer_missing=infer_missing,
        )

    def merge(
        self,
        other: TypeStack,
        ctx: Context | None = None,
    ) -> TypeStack:
        """Merge this stack with another branch stack."""
        from valiance.vtypes.relations import merge_stacks

        return merge_stacks(self, other, ctx)


@dataclass(frozen=True)
class StackApplication:
    """Result of applying an overload to a stack during checking/inference."""

    overload: Overload
    substitution: dict[str, Type]
    inputs: tuple[Type, ...]
    stack: TypeStack
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    actual_returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]
    vectorised: bool = False
