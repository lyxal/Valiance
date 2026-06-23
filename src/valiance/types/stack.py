from __future__ import annotations

"""Immutable stack state and stack-application result records."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from valiance.types.nodes import Overload, Specificity, Type

if TYPE_CHECKING:
    from valiance.types.context import Context


@dataclass(frozen=True)
class TypeStack:
    """Immutable type stack used while checking stack effects."""

    items: tuple[Type, ...] = ()

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int):
        return self.items[index]

    def __bool__(self) -> bool:
        return bool(self.items)

    def push(self, *types: Type) -> TypeStack:
        """Return a new stack with ``types`` appended on top."""
        return TypeStack(self.items + tuple(types))

    def apply(
        self,
        overloads: Iterable[Overload],
        ctx: Context | None = None,
        *,
        infer_missing: bool = False,
    ) -> StackApplication | None:
        """Choose and apply one overload candidate to this stack."""
        from valiance.types.relations import apply_overloads_to_stack

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
        from valiance.types.relations import apply_overload_to_stack

        return apply_overload_to_stack(
            overload,
            self,
            ctx,
            infer_missing=infer_missing,
        )

    def merge(self, other: TypeStack) -> TypeStack:
        """Merge this stack with another branch stack."""
        from valiance.types.relations import merge_stacks

        return merge_stacks(self, other)


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
