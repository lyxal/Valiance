from __future__ import annotations

"""Core data model for Valiance type values and type-checking context."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


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

    name: str
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
class CollectionType(Type):
    """Base class for collection types with a base type and rank."""

    base: Type
    rank: int = 1


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


@dataclass(frozen=True)
class FunctionType(Type):
    """A stack-effect function type."""

    params: tuple[Type, ...] = ()
    returns: tuple[Type, ...] = ()


@dataclass(frozen=True)
class OverloadSetType(Type):
    """An overloaded callable value."""

    overloads: tuple["Overload", ...] = ()


@dataclass(frozen=True)
class TaggedType(Type):
    """A type decorated with present or absent tag requirements."""

    inner: Type
    tags: frozenset[str] = field(default_factory=frozenset[str])


@dataclass(frozen=True)
class ExactType(Type):
    """A parameter wrapper that disables vectorisation for the inner type."""

    inner: Type


@dataclass(frozen=True)
class AtomicType(Type):
    """An atomic-view marker for a type variable."""

    inner: Type


@dataclass(frozen=True)
class CallSiteCheckedFunctionType(Type):
    """A callable whose compatibility is decided by a callback."""

    checker: Callable[..., tuple[Type, ...] | None] = field(
        compare=False, hash=False
    )


@dataclass(frozen=True)
class Overload:
    """Element/function overload signature before generic substitution."""

    params: tuple[Type, ...]
    returns: tuple[Type, ...]


@dataclass(frozen=True)
class ResolvedOverload:
    """Chosen overload plus the substitution and instantiated signature."""

    overload: Overload
    substitution: dict[str, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]


@dataclass(frozen=True)
class AppliedOverload:
    """Result of applying one overload to concrete argument types."""

    overload: Overload
    substitution: dict[str, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    actual_returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]


@dataclass(frozen=True)
class StackApplication:
    """Result of applying an overload to a stack during checking/inference."""

    overload: Overload
    substitution: dict[str, Type]
    inputs: tuple[Type, ...]
    stack: tuple[Type, ...]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    actual_returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]


@dataclass
class Context:
    """Mutable registry of relationships needed by type checks."""

    # The parser/symbol-table layer owns declarations. Context only stores the
    # relationships that the relation functions need to answer questions.
    trait_impls: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    trait_parents: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    variant_members: dict[str, str] = field(default_factory=dict[str, str])
    unit_tags: set[str] = field(default_factory=set[str])

    def implements(self, type_name: str, trait_name: str) -> bool:
        """Return whether a nominal type implements a trait, following parents."""
        seen: set[str] = set()
        pending = list(self.trait_impls.get(type_name, set()))
        if type_name == trait_name:
            return True
        while pending:
            trait = pending.pop()
            if trait in seen:
                continue
            if trait == trait_name:
                return True
            seen.add(trait)
            pending.extend(self.trait_parents.get(trait, set()))
        return False
