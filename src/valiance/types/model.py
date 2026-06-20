from __future__ import annotations

"""Core data model for Valiance type values and type-checking context."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


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

    LIST_EXACT = "list_exact"      # T+n
    LIST_MIN = "list_min"          # T*n
    LIST_RUGGED = "list_rugged"    # T~n
    ARRAY_EXACT = "array_exact"    # T^n
    ARRAY_MIN = "array_min"        # T>n


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


@dataclass(frozen=True)
class Type:
    """Canonical-ish immutable type node used by all type-system relations."""

    # This single dataclass represents all type nodes. Most fields are only
    # meaningful for one or two kinds; keeping one compact node avoids a large
    # class hierarchy while the compiler-facing API is still small.
    kind: str
    name: str | None = None
    args: tuple["Type", ...] = ()
    items: frozenset["Type"] = field(default_factory=frozenset)
    coll_kind: str | None = None
    base: "Type | None" = None
    rank: int | None = None
    params: tuple["Type", ...] = ()
    returns: tuple["Type", ...] = ()
    tags: frozenset[str] = field(default_factory=frozenset)
    inner: "Type | None" = None
    overloads: tuple["Overload", ...] = ()
    checker: Callable[..., tuple["Type", ...] | None] | None = field(
        default=None, compare=False, hash=False
    )

    def __str__(self) -> str:
        """Render the type using the compact display syntax."""
        from valiance.types.builders import show

        return show(self)


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
    trait_impls: dict[str, set[str]] = field(default_factory=dict)
    trait_parents: dict[str, set[str]] = field(default_factory=dict)
    variant_members: dict[str, str] = field(default_factory=dict)
    unit_tags: set[str] = field(default_factory=set)

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
