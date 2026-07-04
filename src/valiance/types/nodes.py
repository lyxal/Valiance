"""Core immutable type nodes and overload result records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from valiance.symbols import Symbol


class Specificity(IntEnum):
    """Ordered match categories used to compare overload candidates."""

    EXACT = 0
    EXACT_GENERIC = 1
    TAGGED = 2
    OPTIONAL = 3
    INTERSECTION = 4
    TRAIT = 5
    RANK = 6
    UNION = 7
    VECTORISED = 8
    CALL_SITE_CHECKED = 9
    NO_MATCH = 10_000


class Type:
    """Base class for immutable type-system nodes."""

    def __str__(self) -> str:
        """Render the type using the compact display syntax."""
        from valiance.types.builders import show

        return show(self)


@dataclass(frozen=True)
class NeverType(Type):
    """The bottom type, assignable to every type."""


@dataclass(frozen=True)
class NoneTypeNode(Type):
    """The ``None`` type."""


@dataclass(frozen=True)
class NominalType(Type):
    """A named type, optionally with invariant generic arguments."""

    name: Symbol
    args: tuple[Type, ...] = ()


@dataclass(frozen=True)
class VarType(Type):
    """A generic type variable."""

    name: str


@dataclass(frozen=True)
class UnionType(Type):
    """A normalized-or-normalizable union type."""

    items: frozenset[Type] = field(default_factory=frozenset[Type])


@dataclass(frozen=True)
class IntersectionType(Type):
    """A normalized-or-normalizable intersection type."""

    items: frozenset[Type] = field(default_factory=frozenset[Type])


@dataclass(frozen=True)
class TupleType(Type):
    """A fixed positional tuple type."""

    params: tuple[Type, ...] = ()


@dataclass(frozen=True)
class TupleTypeItem:
    """One item in an arbitrary-length tuple type pattern."""

    typ: Type
    repeated: bool = False


@dataclass(frozen=True)
class VariadicTupleType(Type):
    """An arbitrary-length tuple type pattern."""

    items: tuple[TupleTypeItem, ...] = ()


@dataclass(frozen=True)
class RowField:
    """One required field in a row constraint."""

    name: Symbol
    typ: Type


@dataclass(frozen=True)
class RowType(Type):
    """A base type with required structural fields."""

    base: Type
    fields: tuple[RowField, ...] = ()


@dataclass(frozen=True)
class CollectionType(Type):
    """Base class for collection types with a base type and rank."""

    base: Type
    rank: int | RankVariable = 1


@dataclass(frozen=True, order=True)
class RankVariable:
    """A compile-time rank variable used by where clauses."""

    name: str


@dataclass(frozen=True)
class ListExactType(CollectionType):
    """A list with exactly the specified rank."""


@dataclass(frozen=True)
class ListMinType(CollectionType):
    """A list with at least the specified rank."""


@dataclass(frozen=True)
class ListRuggedType(CollectionType):
    """A rugged list with at least the specified rank."""


@dataclass(frozen=True)
class ArrayExactType(CollectionType):
    """An array with exactly the specified rank."""


@dataclass(frozen=True)
class ArrayMinType(CollectionType):
    """An array with at least the specified rank."""


@dataclass(frozen=True, order=True)
class ElementTag:
    """An element/function tag requirement or propagated effect fact."""

    name: Symbol
    args: tuple[Type, ...] = ()
    absent: bool = False


@dataclass(frozen=True)
class FunctionType(Type):
    """A stack-effect function type."""

    params: tuple[Type, ...] | None = ()
    returns: tuple[Type, ...] | None = ()
    element_tags: frozenset[ElementTag] = field(default_factory=frozenset[ElementTag])


@dataclass(frozen=True)
class OverloadSetType(Type):
    """An overloaded callable value."""

    overloads: tuple[Overload, ...] = ()


@dataclass(frozen=True, order=True)
class DataTag:
    """A data-tag requirement or fact, including collection-depth metadata."""

    name: str
    depth: int = 0
    absent: bool = False


@dataclass(frozen=True)
class TaggedType(Type):
    """A type decorated with present or absent tag requirements."""

    inner: Type
    tags: frozenset[DataTag] = field(default_factory=frozenset[DataTag])


@dataclass(frozen=True)
class ExactType(Type):
    """A parameter wrapper that disables vectorisation for the inner type."""

    inner: Type


@dataclass(frozen=True)
class AtomicType(Type):
    """An atomic-view marker for a type variable."""

    inner: Type


@dataclass(frozen=True)
class GenericConstraint:
    """A bound that a solved generic type variable must satisfy."""

    name: str
    bound: Type


@dataclass(frozen=True)
class Overload:
    """Element/function overload signature before generic substitution."""

    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    generic_constraints: tuple[GenericConstraint, ...] = ()
    where_clause: tuple[object, ...] = ()
    param_names: tuple[Symbol | None, ...] = field(
        default=(),
        compare=False,
        hash=False,
    )
    call_site_body: Any = field(default=None, compare=False, hash=False)
    element_tags: frozenset[ElementTag] = field(default_factory=frozenset[ElementTag])


@dataclass(frozen=True)
class ResolvedOverload:
    """Chosen overload plus the substitution and instantiated signature."""

    overload: Overload
    substitution: dict[str, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]

    def __hash__(self) -> int:
        return hash(
            (
                self.overload,
                _substitution_items(self.substitution),
                self.params,
                self.returns,
                self.scores,
            )
        )


@dataclass(frozen=True)
class AppliedOverload:
    """Result of applying one overload to concrete argument types."""

    overload: Overload
    substitution: dict[str, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    actual_returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]
    vectorised: bool = False
    vectorised_depths: tuple[int, ...] = ()
    rank_values: tuple[tuple[str, int], ...] = ()
    runtime_consumed_count: int | None = None
    element_tags: frozenset[ElementTag] = field(default_factory=frozenset[ElementTag])

    def __hash__(self) -> int:
        return hash(
            (
                self.overload,
                _substitution_items(self.substitution),
                self.params,
                self.returns,
                self.actual_returns,
                self.scores,
                self.vectorised,
                self.vectorised_depths,
                self.rank_values,
                self.runtime_consumed_count,
                self.element_tags,
            )
        )


def _substitution_items(substitution: dict[str, Type]) -> tuple[tuple[str, Type], ...]:
    return tuple(sorted(substitution.items()))
