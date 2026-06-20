from __future__ import annotations

"""Core data model for Valiance type values and type-checking context."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, ClassVar


class Kind:
    """String constants for every internal type node kind."""

    NEVER = "Never"
    NONE = "None"
    NOMINAL = "Nominal"
    VAR = "Var"
    UNION = "Union"
    INTERSECTION = "Intersection"
    TUPLE = "Tuple"
    COLLECTION = "Collection"
    FUNCTION = "Function"
    OVERLOAD_SET = "OverloadSet"
    TAGGED = "Tagged"
    EXACT = "Exact"
    ATOMIC = "Atomic"
    CSTC = "CallSiteCheckedFunction"


class Coll:
    """String constants for collection rank modes."""

    LIST_EXACT = "list_exact"  # T+n
    LIST_MIN = "list_min"  # T*n
    LIST_RUGGED = "list_rugged"  # T~n
    ARRAY_EXACT = "array_exact"  # T^n
    ARRAY_MIN = "array_min"  # T>n


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

    kind: ClassVar[str]

    def __str__(self) -> str:
        """Render the type using the compact display syntax."""
        from valiance.types.builders import show

        return show(self)


@dataclass(frozen=True)
class NeverType(Type):
    """The bottom type, assignable to every type."""

    kind: ClassVar[str] = Kind.NEVER


@dataclass(frozen=True)
class NoneTypeNode(Type):
    """The ``None`` type."""

    kind: ClassVar[str] = Kind.NONE


@dataclass(frozen=True)
class NominalType(Type):
    """A named type, optionally with invariant generic arguments."""

    name: str
    args: tuple[Type, ...] = ()
    kind: ClassVar[str] = Kind.NOMINAL


@dataclass(frozen=True)
class VarType(Type):
    """A generic type variable."""

    name: str
    kind: ClassVar[str] = Kind.VAR


@dataclass(frozen=True)
class UnionType(Type):
    """A normalized-or-normalizable union type."""

    items: frozenset[Type] = field(default_factory=frozenset[Type])
    kind: ClassVar[str] = Kind.UNION


@dataclass(frozen=True)
class IntersectionType(Type):
    """A normalized-or-normalizable intersection type."""

    items: frozenset[Type] = field(default_factory=frozenset[Type])
    kind: ClassVar[str] = Kind.INTERSECTION


@dataclass(frozen=True)
class TupleType(Type):
    """A fixed positional tuple type."""

    params: tuple[Type, ...] = ()
    kind: ClassVar[str] = Kind.TUPLE


@dataclass(frozen=True)
class CollectionType(Type):
    """A collection type with a rank mode, base type, and rank."""

    coll_kind: str
    base: Type
    rank: int = 1
    kind: ClassVar[str] = Kind.COLLECTION


@dataclass(frozen=True)
class FunctionType(Type):
    """A stack-effect function type."""

    params: tuple[Type, ...] = ()
    returns: tuple[Type, ...] = ()
    kind: ClassVar[str] = Kind.FUNCTION


@dataclass(frozen=True)
class OverloadSetType(Type):
    """An overloaded callable value."""

    overloads: tuple["Overload", ...] = ()
    kind: ClassVar[str] = Kind.OVERLOAD_SET


@dataclass(frozen=True)
class TaggedType(Type):
    """A type decorated with present or absent tag requirements."""

    inner: Type
    tags: frozenset[str] = field(default_factory=frozenset[str])
    kind: ClassVar[str] = Kind.TAGGED


@dataclass(frozen=True)
class ExactType(Type):
    """A parameter wrapper that disables vectorisation for the inner type."""

    inner: Type
    kind: ClassVar[str] = Kind.EXACT


@dataclass(frozen=True)
class AtomicType(Type):
    """An atomic-view marker for a type variable."""

    inner: Type
    kind: ClassVar[str] = Kind.ATOMIC


@dataclass(frozen=True)
class CallSiteCheckedFunctionType(Type):
    """A callable whose compatibility is decided by a callback."""

    checker: Callable[..., tuple[Type, ...] | None] = field(
        compare=False, hash=False
    )
    kind: ClassVar[str] = Kind.CSTC


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
