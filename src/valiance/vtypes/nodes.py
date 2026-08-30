"""Core immutable type nodes and overload result records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum, auto
from typing import Any

from valiance.vtypes.symbols import Symbol


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


class Variance(Enum):
    """How a generic argument or bound participates in subtyping."""

    INVARIANT = auto()
    COVARIANT = auto()
    CONTRAVARIANT = auto()


class Type:
    """Base class for immutable type-system nodes."""

    def __str__(self) -> str:
        """Render the type using the compact display syntax."""
        from valiance.vtypes.builders import show

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
class TaskType(Type):
    """A task whose payload is an ordered native output stack row.

    The outputs are deliberately not represented as nominal generic arguments or
    a tuple. Waiting splices this row back onto the operand stack.
    """

    outputs: tuple[Type, ...] = ()
    effects: frozenset["ElementTag"] = field(default_factory=frozenset)


@dataclass(frozen=True, order=True, slots=True)
class TypeVarId:
    """Lexical identity of one bound type variable.

    ``scope`` identifies the binder and ``index`` identifies the variable inside
    that binder. The source spelling is intentionally not part of identity.
    """

    scope: int
    index: int


@dataclass(frozen=True)
class VarType(Type):
    """A generic type variable with an optional lexical identity.

    Unscoped variables remain available for compiler metadata that has not yet
    migrated to binder identities. Bound source generics should use ``identity``.
    """

    name: str
    identity: TypeVarId | None = None


@dataclass(frozen=True, order=True, slots=True)
class MetaVarId:
    """Identity of one compiler-created, refinable inference variable."""

    origin: int
    index: int


@dataclass(frozen=True)
class MetaVarType(VarType):
    """A compiler-created inference variable that may be refined in a branch."""

    meta_identity: MetaVarId = MetaVarId(0, 0)


TypeVarKey = str | TypeVarId | MetaVarId


def type_var_key(variable: VarType) -> TypeVarKey:
    """Return the semantic identity key for a rigid or inference variable."""
    if isinstance(variable, MetaVarType):
        return variable.meta_identity
    return variable.identity if variable.identity is not None else variable.name


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
    """Base class for collection types with a base type and positive rank."""

    base: Type
    rank: int | RankVariable = 1

    def __post_init__(self) -> None:
        """Reject malformed ranks before they can enter relation algorithms."""
        if isinstance(self.rank, int) and self.rank < 1:
            raise ValueError("collection rank must be a positive integer")


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
class AnonymousTraitRequirement:
    """One required element signature in an anonymous structural trait."""

    name: Symbol
    overload: Overload


@dataclass(frozen=True)
class AnonymousTraitType(Type):
    """An inline structural trait type."""

    generics: tuple[Symbol, ...] = ()
    requirements: tuple[AnonymousTraitRequirement, ...] = ()


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
class RuntimeTypePattern:
    """Serializable runtime predicate derived from one static type branch.

    ``accepted_names`` is the closed-world nominal subtype set known during
    analysis. ``children`` stores nominal arguments or the members of compound
    patterns, depending on ``kind``.
    """

    kind: str
    name: str | None = None
    children: tuple[RuntimeTypePattern, ...] = ()
    accepted_names: tuple[str, ...] = ()
    variances: tuple[Variance, ...] = ()
    tags: tuple[DataTag, ...] = ()
    rank: int | None = None
    collection_kind: str | None = None


@dataclass(frozen=True)
class UnionDispatchBranch:
    """One cartesian union branch and its statically selected overload."""

    params: tuple[RuntimeTypePattern, ...]
    overload_index: int


@dataclass(frozen=True)
class UnionDispatchPlan:
    """Runtime branch dispatch plus the statically merged return stack."""

    branches: tuple[UnionDispatchBranch, ...]
    returns: tuple[Type, ...]


@dataclass(frozen=True)
class TaggedType(Type):
    """A type decorated with present or absent tag requirements."""

    inner: Type
    tags: frozenset[DataTag] = field(default_factory=frozenset[DataTag])
    exact: bool = False


@dataclass(frozen=True)
class NoVecType(Type):
    """Call-policy metadata that disables parameter vectorisation."""

    inner: Type


@dataclass(frozen=True)
class ExactType(Type):
    """Call-policy metadata requiring an exact structural argument match."""

    inner: Type


@dataclass(frozen=True)
class GenericConstraint:
    """A bound that a solved generic type variable must satisfy."""

    name: str
    bound: Type
    variance: Variance = Variance.COVARIANT


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
    annotation_error: str | None = None
    annotation_warning: str | None = None
    param_defaults: tuple[tuple[object, ...] | None, ...] = field(
        default=(),
        compare=False,
        hash=False,
    )
    is_multi: bool = field(default=False, compare=False, hash=False)
    runtime_static_values: tuple[object, ...] = field(
        default=(),
        compare=False,
        hash=False,
    )
    generic_params: tuple[str, ...] = ()
    index_target: Type | None = field(default=None, compare=False, hash=False)
    update_target: Type | None = field(default=None, compare=False, hash=False)


@dataclass(frozen=True)
class ResolvedOverload:
    """Chosen overload plus the substitution and instantiated signature."""

    overload: Overload
    substitution: dict[TypeVarKey, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]

    def __hash__(self) -> int:
        """Return a stable hash for this resolved overload."""
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
    substitution: dict[TypeVarKey, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    actual_returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]
    vectorised: bool = False
    vectorised_depths: tuple[int, ...] = ()
    rank_values: tuple[tuple[str, int], ...] = ()
    runtime_consumed_count: int | None = None
    element_tags: frozenset[ElementTag] = field(default_factory=frozenset[ElementTag])
    multidispatch: bool = field(default=False, compare=False, hash=False)
    union_dispatch_plan: UnionDispatchPlan | None = field(
        default=None, compare=False, hash=False
    )
    vectorised_target_ranks: tuple[int | None, ...] = ()
    runtime_static_values: tuple[object, ...] = field(
        default=(),
        compare=False,
        hash=False,
    )

    def __hash__(self) -> int:
        """Return a stable hash for this applied overload."""
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
                self.vectorised_target_ranks,
            )
        )


class OverloadMismatchReason(Enum):
    """Why a concrete argument list did not apply to an overload."""

    STACK_UNDERFLOW = auto()
    ARGUMENT_TYPE = auto()
    GENERIC_CONSTRAINT = auto()
    WHERE_CLAUSE = auto()
    DISAMBIGUATION = auto()
    VECTORISATION = auto()
    NAMED_ARGUMENT = auto()
    DEFAULT_ARGUMENT = auto()
    CALL_SITE_CHECK = auto()
    ARITY = auto()
    RESULT = auto()


@dataclass(frozen=True)
class OverloadMismatch:
    reason: OverloadMismatchReason
    matched_arguments: int = 0
    argument_index: int | None = None
    parameter_name: Symbol | None = None
    expected: Type | None = None
    actual: Type | None = None
    detail: str | None = None


@dataclass(frozen=True)
class OverloadAttempt:
    applied: AppliedOverload | None
    mismatch: OverloadMismatch | None = None


def _substitution_items(substitution: dict[TypeVarKey, Type]) -> tuple[tuple[str, Type], ...]:
    """Collect the items for substitution for immutable type-system records."""
    return tuple(sorted(substitution.items(), key=lambda item: repr(item[0])))
