"""Type relations, generic solving, overload resolution, and stack merging."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from valiance.symbols import Symbol
from valiance.types.builders import (
    C,
    Fn,
    I,
    N,
    NoneType,
    Tagged,
    Tup,
    U,
    _is_optional,
    _optional_inner,
    normalize,
    optional,
    same,
)
from valiance.types.context import Context, Variance
from valiance.types.nodes import (
    AppliedOverload,
    ArrayExactType,
    ArrayMinType,
    AtomicType,
    CallSiteCheckedFunctionType,
    CollectionType,
    DataTag,
    ExactType,
    FunctionType,
    GenericConstraint,
    IntersectionType,
    ListExactType,
    ListMinType,
    ListRuggedType,
    NeverType,
    NominalType,
    NoneTypeNode,
    Overload,
    OverloadSetType,
    ResolvedOverload,
    RowType,
    Specificity,
    TaggedType,
    TupleType,
    Type,
    UnionType,
    VariadicTupleType,
    VarType,
)
from valiance.types.stack import StackApplication, TypeStack

CollectionClass = type[CollectionType]
INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")


def _match_variadic_tuple(
    pattern: tuple[object, ...],
    actual: tuple[Type, ...],
    match: Callable[[Type, Type], bool],
) -> bool:
    """Return whether a fixed tuple matches a repeated tuple pattern."""
    seen: set[tuple[int, int]] = set()

    def rec(pattern_index: int, actual_index: int) -> bool:
        state = (pattern_index, actual_index)
        if state in seen:
            return False
        seen.add(state)
        if pattern_index == len(pattern):
            return actual_index == len(actual)
        item = pattern[pattern_index]
        typ = item.typ
        if item.repeated:
            if rec(pattern_index + 1, actual_index):
                return True
            for index in range(actual_index, len(actual)):
                if not match(typ, actual[index]):
                    return False
                if rec(pattern_index + 1, index + 1):
                    return True
            return False
        return actual_index < len(actual) and match(typ, actual[actual_index]) and rec(
            pattern_index + 1,
            actual_index + 1,
        )

    return rec(0, 0)


def subtype(source: Type, target: Type, ctx: Context | None = None) -> bool:
    """Return whether ``source`` can be treated as ``target`` by subsumption."""
    ctx = ctx or Context()
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or isinstance(source, NeverType):
        return True

    if isinstance(source, TaggedType):
        if not _has_unit_tag(source.tags, ctx) and subtype(source.inner, target, ctx):
            return True

    if isinstance(target, TaggedType):
        # Positive tag requirements must be present; absent tag requirements
        # are encoded as ``DataTag(absent=True)``.
        actual_tags = source.tags if isinstance(source, TaggedType) else frozenset()
        inner = source.inner if isinstance(source, TaggedType) else source
        if not _tag_requirements_met(actual_tags, target.tags):
            return False
        return subtype(inner, target.inner, ctx)

    if isinstance(source, UnionType):
        return all(subtype(s, target, ctx) for s in source.items)

    if isinstance(target, UnionType):
        return any(subtype(source, t, ctx) for t in target.items)

    if isinstance(target, IntersectionType):
        return all(subtype(source, t, ctx) for t in target.items)

    if isinstance(source, IntersectionType):
        return any(subtype(s, target, ctx) for s in source.items)

    if isinstance(target, RowType):
        return _row_subtype(source, target, ctx)

    if isinstance(source, RowType):
        return subtype(source.base, target, ctx)

    if isinstance(source, NominalType) and isinstance(target, NominalType):
        if source.name == target.name and len(source.args) == len(target.args):
            return _nominal_args_subtype(source, target, ctx)
        if ctx.implements(source.name, target.name):
            return True
        if ctx.variant_members.get(source.name) == target.name:
            return True
        if source.name in {INTEGER, REAL} and target.name == NUMBER:
            return True

    if isinstance(source, TupleType) and isinstance(target, TupleType):
        return len(source.params) == len(target.params) and all(
            assignable(a, b, ctx)
            for a, b in zip(source.params, target.params, strict=False)
        )

    if isinstance(source, TupleType) and isinstance(target, VariadicTupleType):
        return _match_variadic_tuple(
            target.items,
            source.params,
            lambda expected, actual: assignable(actual, expected, ctx),
        )

    if isinstance(source, CollectionType) and isinstance(target, CollectionType):
        return _collection_subtype(source, target, ctx)

    return False


def _nominal_args_subtype(
    source: NominalType,
    target: NominalType,
    ctx: Context,
) -> bool:
    """Return whether nominal args satisfy declared variance for this constructor."""
    variances = ctx.variance_for(source.name, len(source.args))
    for actual, expected, variance in zip(
        source.args,
        target.args,
        variances,
        strict=True,
    ):
        if variance is Variance.COVARIANT:
            if not assignable(actual, expected, ctx):
                return False
        elif variance is Variance.CONTRAVARIANT:
            if not assignable(expected, actual, ctx):
                return False
        elif not same(actual, expected):
            return False
    return True


def _row_subtype(source: Type, target: RowType, ctx: Context) -> bool:
    """Return whether ``source`` satisfies a row-constrained target."""
    if isinstance(source, RowType):
        if not subtype(source.base, target.base, ctx):
            return False
        source_fields = {field.name: field.typ for field in source.fields}
        for field in target.fields:
            actual = source_fields.get(field.name)
            if actual is None or not assignable(actual, field.typ, ctx):
                return False
        return True
    return not target.fields and subtype(source, target.base, ctx)


def _collection_subtype(source: Type, target: Type, ctx: Context) -> bool:
    """Return whether one collection type subsumes another by rank rules."""
    if isinstance(source, (ListMinType, ListRuggedType)) and isinstance(
        target, ListExactType
    ):
        # A minimum/rugged list can satisfy an exact outer-list pattern if the
        # peeled remainder is exactly the expected element type.
        if (
            isinstance(source.rank, int)
            and isinstance(target.rank, int)
            and source.rank >= target.rank
        ):
            remainder = _collection_remainder(
                type(source), source.base, source.rank - target.rank
            )
            return assignable(remainder, target.base, ctx)
    if not assignable(source.base, target.base, ctx):
        return False
    sk, tk = type(source), type(target)
    sr, tr = source.rank, target.rank

    if sk is tk and sr == tr:
        return True
    if sk is ArrayExactType and tk is ListExactType and sr == tr:
        return True
    if sk is ArrayMinType and tk is ListMinType and sr == tr:
        return True
    if sk in {ListExactType, ArrayExactType} and tk in {ListMinType, ArrayMinType}:
        return _rank_ge(sr, tr) and ((sk is ArrayExactType) == (tk is ArrayMinType))
    if tk is ListRuggedType and sk in {
        ListExactType,
        ListMinType,
        ArrayExactType,
        ArrayMinType,
    }:
        return _rank_ge(sr, tr)
    return False


def _rank_ge(left: object, right: object) -> bool:
    return isinstance(left, int) and isinstance(right, int) and left >= right


def assignable(source: Type, target: Type, ctx: Context | None = None) -> bool:
    """Return whether a value of ``source`` can be stored in ``target``."""
    ctx = ctx or Context()
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or subtype(source, target, ctx):
        return True

    if _is_optional(target):
        # Assignment can implicitly wrap a present value into Some[T], and None
        # can be stored in any optional.
        inner = _optional_inner(target)
        return isinstance(source, NoneTypeNode) or (
            inner is not None and assignable(source, inner, ctx)
        )

    if isinstance(source, UnionType):
        return all(assignable(s, target, ctx) for s in source.items)
    if isinstance(target, UnionType):
        return any(assignable(source, t, ctx) for t in target.items)

    return False


def _solve(pattern: Type, actual: Type) -> dict[str, list[Type]] | None:
    """Collect generic constraints by matching a parameter pattern to an argument."""
    constraints: dict[str, list[Type]] = {}

    def add(name: str, value: Type) -> None:
        """Append a candidate solution for one type variable."""
        constraints.setdefault(name, []).append(normalize(value))

    def rec(p: Type, a: Type) -> bool:
        """Recursively match a pattern node against an actual type node."""
        p, a = normalize(p), normalize(a)
        if isinstance(p, VarType):
            # Solving does not decide whether this is globally valid; it only
            # records what this one parameter says the generic must be.
            add(p.name, a)
            return True
        if same(p, a):
            return True
        if isinstance(p, AtomicType):
            return True
        if _is_optional(p):
            if isinstance(a, NoneTypeNode):
                # None does not constrain T in T?. Another argument or context
                # must provide T, otherwise the generic remains underconstrained.
                return True
            inner = _optional_inner(p)
            actual_inner = _optional_inner(a) if _is_optional(a) else a
            return (
                inner is not None
                and actual_inner is not None
                and rec(inner, actual_inner)
            )
        if isinstance(p, UnionType):
            # Ordinary union solving is allowed only when exactly one branch
            # matches. Ambiguous generic unions should require explicit types.
            matches: list[dict[str, list[Type]]] = []
            for branch in p.items:
                saved = {key: list(values) for key, values in constraints.items()}
                if rec(branch, a):
                    matches.append(
                        {key: list(values) for key, values in constraints.items()}
                    )
                constraints.clear()
                constraints.update(saved)
            if len(matches) != 1:
                return False
            constraints.clear()
            constraints.update(matches[0])
            return True
        if isinstance(p, NominalType) and isinstance(a, NominalType):
            return (
                p.name == a.name
                and len(p.args) == len(a.args)
                and all(rec(x, y) for x, y in zip(p.args, a.args, strict=False))
            )
        if isinstance(p, RowType):
            actual_base = a.base if isinstance(a, RowType) else a
            if not rec(p.base, actual_base):
                return False
            if not p.fields:
                return True
            if not isinstance(a, RowType):
                return False
            actual_fields = {field.name: field.typ for field in a.fields}
            for field in p.fields:
                actual_field = actual_fields.get(field.name)
                if actual_field is None or not rec(field.typ, actual_field):
                    return False
            return True
        if isinstance(p, TupleType) and isinstance(a, TupleType):
            return len(p.params) == len(a.params) and all(
                rec(x, y) for x, y in zip(p.params, a.params, strict=False)
            )
        if isinstance(p, VariadicTupleType) and isinstance(a, TupleType):
            return solve_variadic_tuple(p, a)
        if isinstance(p, FunctionType) and isinstance(a, FunctionType):
            return (
                len(p.params) == len(a.params)
                and len(p.returns) == len(a.returns)
                and all(
                    rec(x, y)
                    for x, y in zip(
                        p.params + p.returns, a.params + a.returns, strict=False
                    )
                )
            )
        if isinstance(p, FunctionType) and isinstance(a, OverloadSetType):
            # Overloaded callables are checked after other generics are known.
            # Solving through every overload here would guess too early.
            return True
        if isinstance(p, TaggedType):
            if not isinstance(a, TaggedType):
                return all(tag.absent for tag in p.tags) and rec(p.inner, a)
            if not _tag_requirements_met(a.tags, p.tags):
                return False
            return rec(p.inner, a.inner)
        if isinstance(p, CollectionType) and isinstance(a, CollectionType):
            return _solve_collection(p, a, add)
        return False

    def solve_variadic_tuple(pattern: VariadicTupleType, actual: TupleType) -> bool:
        def save() -> dict[str, list[Type]]:
            return {key: list(values) for key, values in constraints.items()}

        def restore(saved: dict[str, list[Type]]) -> None:
            constraints.clear()
            constraints.update(saved)

        def rec_tuple(pattern_index: int, actual_index: int) -> bool:
            if pattern_index == len(pattern.items):
                return actual_index == len(actual.params)
            item = pattern.items[pattern_index]
            if item.repeated:
                saved = save()
                if rec_tuple(pattern_index + 1, actual_index):
                    return True
                restore(saved)
                for index in range(actual_index, len(actual.params)):
                    if not rec(item.typ, actual.params[index]):
                        restore(saved)
                        return False
                    consumed = save()
                    if rec_tuple(pattern_index + 1, index + 1):
                        return True
                    restore(consumed)
                restore(saved)
                return False
            if actual_index >= len(actual.params):
                return False
            saved = save()
            if rec(item.typ, actual.params[actual_index]) and rec_tuple(
                pattern_index + 1,
                actual_index + 1,
            ):
                return True
            restore(saved)
            return False

        return rec_tuple(0, 0)

    return constraints if rec(pattern, actual) else None


def _solve_collection(
    pattern: Type, actual: Type, add: Callable[[str, Type], None]
) -> bool:
    """Solve a generic collection pattern against an actual collection type."""
    if not isinstance(pattern.base, VarType):
        # This helper is only for patterns like T+. Non-generic collection
        # patterns are handled by normal compatibility.
        return same(pattern, actual)
    n, m = pattern.rank, actual.rank
    if not isinstance(n, int) or not isinstance(m, int):
        return same(pattern, actual)
    if m < n:
        return False
    diff = m - n
    pk, ak = type(pattern), type(actual)
    base = actual.base

    def bind_as(collection_type: CollectionClass) -> bool:
        """Bind the pattern base variable to the peeled actual collection type."""
        add(pattern.base.name, _collection_remainder(collection_type, base, diff))
        return True

    if pk is ListExactType and ak in {ListExactType, ArrayExactType}:
        return bind_as(ListExactType)
    if pk is ListExactType and ak in {ListMinType, ListRuggedType}:
        return bind_as(ak)
    if pk is ListMinType:
        if ak in {ListExactType, ArrayExactType}:
            return bind_as(ListExactType)
        if ak in {ListMinType, ArrayMinType}:
            return bind_as(ListMinType)
    if pk is ListRuggedType:
        if ak in {ListExactType, ArrayExactType}:
            return bind_as(ListExactType)
        if ak in {ListMinType, ArrayMinType}:
            return bind_as(ListRuggedType)
        if ak is ListRuggedType:
            return bind_as(ListRuggedType)
    if pk is ArrayExactType and ak is ArrayExactType:
        return bind_as(ArrayExactType)
    if pk is ArrayMinType:
        if ak is ArrayExactType:
            return bind_as(ArrayExactType)
        if ak is ArrayMinType:
            return bind_as(ArrayMinType)
    return False


def _collection_remainder(
    collection_type: CollectionClass, base: Type, rank: int
) -> Type:
    """Return the element type left after peeling collection rank from a type."""
    if rank > 0:
        return C(collection_type, base, rank)
    # Peeling all known rank from a minimum/rugged type leaves an element that
    # may be atomic or may still contain more nesting at runtime.
    if collection_type in {ListMinType, ArrayMinType}:
        return U(base, C(collection_type, base, 1))
    if collection_type is ListRuggedType:
        return U(base, C(ListRuggedType, base, 1))
    return base


def collection_item_type(t: Type) -> Type | None:
    """Return the type produced by iterating one rank of a collection."""
    t = normalize(t)
    if isinstance(t, TaggedType):
        return collection_item_type(t.inner)
    if not isinstance(t, CollectionType):
        return None
    return _collection_remainder(type(t), t.base, t.rank - 1)


def _collection_view(t: Type) -> CollectionType | None:
    """Return a collection node through transparent wrappers, if present."""
    t = normalize(t)
    if isinstance(t, TaggedType):
        return _collection_view(t.inner)
    if isinstance(t, CollectionType):
        return t
    return None


def _combine(a: Type, b: Type) -> Type | None:
    """Merge two candidate generic solutions into a shared solution."""
    a, b = normalize(a), normalize(b)
    if same(a, b):
        return a
    if _is_optional(a) and _is_optional(b):
        ai, bi = _optional_inner(a), _optional_inner(b)
        if ai is None:
            return b
        if bi is None:
            return a
        inner = _combine(ai, bi)
        return optional(inner) if inner else None
    if (
        isinstance(a, CollectionType)
        and isinstance(b, CollectionType)
        and same(a.base, b.base)
    ):
        # Collection solutions can widen from exact to minimum/rugged when
        # multiple constraints need one shared generic type.
        return _combine_collections(a, b)
    return None


def _combine_collections(a: Type, b: Type) -> Type | None:
    """Merge two collection solutions using conservative rank widening."""
    ak, bk = type(a), type(b)
    ar, br = a.rank, b.rank
    base = a.base
    if ak is bk is ListExactType:
        return a if ar == br else None
    if ak is bk is ArrayExactType:
        return a if ar == br else None
    if ak is bk is ListMinType:
        return C(ListMinType, base, min(ar, br))
    if ak is bk is ArrayMinType:
        return C(ArrayMinType, base, min(ar, br))
    if {ak, bk} <= {ListExactType, ListMinType}:
        return C(ListMinType, base, min(ar, br))
    if {ak, bk} <= {ArrayExactType, ArrayMinType}:
        return C(ArrayMinType, base, min(ar, br))
    if ListRuggedType in {ak, bk}:
        return C(ListRuggedType, base, min(ar, br))
    if {ak, bk} == {ListExactType, ArrayExactType} and ar == br:
        return C(ListExactType, base, ar)
    if {ak, bk} == {ListMinType, ArrayMinType}:
        return C(ListMinType, base, min(ar, br))
    return None


def _combine_all(values: Iterable[Type]) -> Type | None:
    """Merge a sequence of generic solutions, returning ``None`` on conflict."""
    vals = list(values)
    if not vals:
        return None
    out = vals[0]
    for value in vals[1:]:
        out = _combine(out, value)
        if out is None:
            return None
    return out


def merge_types(a: Type, b: Type) -> Type:
    """Merge two branch result types using union/optional normalization."""
    if isinstance(normalize(a), NoneTypeNode):
        return optional(b)
    if isinstance(normalize(b), NoneTypeNode):
        return optional(a)
    return U(a, b)


def merge_stacks(
    a: TypeStack,
    b: TypeStack,
) -> TypeStack:
    """Merge two branch stacks pairwise, padding shorter stacks with ``None``."""
    # Padding on the left treats missing values as absent lower stack outputs.
    # For branch result stacks this gives the same optional-padding behaviour
    # as the language design.
    length = max(len(a), len(b))
    left = (NoneType(),) * (length - len(a)) + a.items
    right = (NoneType(),) * (length - len(b)) + b.items
    return TypeStack(
        tuple(merge_types(x, y) for x, y in zip(left, right, strict=False))
    )


def _substitute(t: Type, subst: dict[str, Type]) -> Type:
    """Replace type variables in ``t`` using a solved substitution map."""
    t = normalize(t)
    if isinstance(t, VarType):
        return subst.get(t.name, t)
    if isinstance(t, NominalType):
        return N(t.name, *(_substitute(a, subst) for a in t.args))
    if isinstance(t, UnionType):
        return U(*(_substitute(i, subst) for i in t.items))
    if isinstance(t, IntersectionType):
        return I(*(_substitute(i, subst) for i in t.items))
    if isinstance(t, TupleType):
        return Tup(*(_substitute(p, subst) for p in t.params))
    if isinstance(t, VariadicTupleType):
        return VariadicTupleType(
            tuple(
                type(item)(_substitute(item.typ, subst), item.repeated)
                for item in t.items
            )
        )
    if isinstance(t, RowType):
        return normalize(
            RowType(
                _substitute(t.base, subst),
                tuple(
                    type(field)(field.name, _substitute(field.typ, subst))
                    for field in t.fields
                ),
            )
        )
    if isinstance(t, CollectionType):
        return normalize(C(type(t), _substitute(t.base, subst), t.rank))
    if isinstance(t, FunctionType):
        return Fn(
            (_substitute(p, subst) for p in t.params),
            (_substitute(r, subst) for r in t.returns),
        )
    if isinstance(t, TaggedType):
        return Tagged(_substitute(t.inner, subst), *t.tags)
    if isinstance(t, AtomicType) and isinstance(t.inner, VarType):
        solved = subst.get(t.inner.name)
        return _atomic_of(solved) if solved else t
    return t


def _generic_constraints_met(
    generic_constraints: tuple[GenericConstraint, ...],
    substitution: dict[str, Type],
    ctx: Context,
) -> bool:
    """Return whether solved generic variables satisfy their declared bounds."""
    for constraint in generic_constraints:
        solution = substitution.get(constraint.name)
        if solution is None:
            return False
        bound = _substitute(constraint.bound, substitution)
        if not assignable(solution, bound, ctx):
            return False
    return True


def _atomic_of(t: Type) -> Type:
    """Return the atomic base type of a collection-like solved generic."""
    t = normalize(t)
    if isinstance(t, CollectionType):
        return _atomic_of(t.base)
    return t


def compatible(argument: Type, parameter: Type, ctx: Context | None = None) -> bool:
    """Return whether an argument type can satisfy a call parameter type."""
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if assignable(argument, parameter, ctx):
        return True
    if isinstance(parameter, FunctionType):
        # Function compatibility is callability-based. A scalar function can be
        # compatible with a vector function type if calling it vectorises.
        return _callable_compatible(argument, parameter, ctx)
    if isinstance(parameter, IntersectionType):
        return all(compatible(argument, p, ctx) for p in parameter.items)
    if isinstance(parameter, UnionType):
        return any(compatible(argument, p, ctx) for p in parameter.items)
    if isinstance(argument, UnionType):
        return all(compatible(a, parameter, ctx) for a in argument.items)
    if _is_optional(parameter):
        inner = _optional_inner(parameter)
        return isinstance(argument, NoneTypeNode) or (
            inner is not None and compatible(argument, inner, ctx)
        )
    if _can_vectorise(argument, parameter, ctx):
        return True
    constraints = _solve(parameter, argument)
    if constraints is not None:
        # Compatibility can use generic solving as a fallback, but only when it
        # actually substitutes something. Otherwise concrete mismatches could
        # recurse forever.
        subst = {k: _combine_all(v) for k, v in constraints.items()}
        substituted = _substitute(parameter, subst)
        if (
            subst
            and all(v is not None for v in subst.values())
            and not same(substituted, parameter)
        ):
            return compatible(argument, substituted, ctx)
    return False


def _callable_compatible(argument: Type, parameter: Type, ctx: Context) -> bool:
    """Return whether a callable can act as an expected ``Function[...]`` type."""
    if isinstance(argument, FunctionType):
        actual_returns = _overload_result_for_args(
            Overload(argument.params, argument.returns), parameter.params, ctx
        )
        return (
            actual_returns is not None
            and len(actual_returns) == len(parameter.returns)
            and all(
                compatible(a, p, ctx)
                for a, p in zip(actual_returns, parameter.returns, strict=False)
            )
        )
    if isinstance(argument, OverloadSetType):
        # The expected Function[...] supplies the call input types for choosing
        # an overload from the callable value.
        matches = [
            o
            for o in argument.overloads
            if _overload_callable_compatible(o, parameter, ctx)
        ]
        return len(matches) == 1 or bool(
            resolve_overload_result(argument.overloads, parameter.params, ctx)
        )
    if isinstance(argument, CallSiteCheckedFunctionType):
        result = argument.checker(parameter.params)
        return (
            result is not None
            and len(result) == len(parameter.returns)
            and all(
                compatible(a, p, ctx)
                for a, p in zip(result, parameter.returns, strict=False)
            )
        )
    return False


def _overload_callable_compatible(
    overload: Overload, expected: Type, ctx: Context
) -> bool:
    """Return whether one overload can be used as an expected function type."""
    if len(overload.params) != len(expected.params) or len(overload.returns) != len(
        expected.returns
    ):
        return False
    actual_returns = _overload_result_for_args(overload, expected.params, ctx)
    return actual_returns is not None and all(
        compatible(r, e, ctx)
        for r, e in zip(actual_returns, expected.returns, strict=False)
    )


def _overload_result_for_args(
    overload: Overload, args: tuple[Type, ...], ctx: Context
) -> tuple[Type, ...] | None:
    """Compute an overload's result stack when called with concrete argument types."""
    if len(overload.params) != len(args):
        return None
    if not all(
        compatible(a, p, ctx) for a, p in zip(args, overload.params, strict=False)
    ):
        return None

    vector_rank = 0
    vector_type: CollectionClass | None = None
    for arg, param in zip(args, overload.params, strict=False):
        # Track how much vectorisation was needed. Return types are wrapped in
        # that outer vector shape after the scalar overload is applied.
        arg_collection = _collection_view(arg)
        param_collection = _collection_view(param)
        if arg_collection is None:
            continue
        if param_collection is not None:
            excess = arg_collection.rank - param_collection.rank
        else:
            excess = arg_collection.rank
        if excess > vector_rank:
            vector_rank = excess
        if excess > 0:
            if vector_type is None:
                vector_type = type(arg_collection)
            elif vector_type is not type(arg_collection):
                vector_type = ListExactType

    if vector_rank <= 0:
        return overload.returns

    out_type = ArrayExactType if vector_type is ArrayExactType else ListExactType
    return tuple(C(out_type, ret, vector_rank) for ret in overload.returns)


def _vectorisation_excess(argument: Type, expected: Type, ctx: Context) -> int | None:
    argument_collection = _collection_view(argument)
    if argument_collection is None:
        return 0 if compatible(argument, expected, ctx) else None
    expected_collection = _collection_view(expected)
    if expected_collection is not None:
        if not same(argument_collection.base, expected_collection.base):
            return None
        excess = argument_collection.rank - expected_collection.rank
    else:
        excess = argument_collection.rank
    return excess if excess >= 0 else None


def _wrap_returns_for_vector_depth(
    returns: tuple[Type, ...],
    args: tuple[Type, ...],
    depths: tuple[int, ...],
) -> tuple[Type, ...]:
    vector_rank = max(depths, default=0)
    if vector_rank <= 0:
        return returns
    vector_type: CollectionClass | None = None
    for arg, depth in zip(args, depths, strict=False):
        if depth <= 0:
            continue
        arg_collection = _collection_view(arg)
        if arg_collection is None:
            continue
        if vector_type is None:
            vector_type = type(arg_collection)
        elif vector_type is not type(arg_collection):
            vector_type = ListExactType
    out_type = ArrayExactType if vector_type is ArrayExactType else ListExactType
    return tuple(C(out_type, ret, vector_rank) for ret in returns)


def _can_vectorise(argument: Type, parameter: Type, ctx: Context) -> bool:
    """Return whether compatibility can be achieved through vectorisation."""
    if isinstance(parameter, ExactType):
        return compatible(argument, parameter.inner, ctx) and not (
            _collection_view(argument) is not None
            and _collection_view(parameter.inner) is None
        )
    argument_collection = _collection_view(argument)
    parameter_collection = _collection_view(parameter)
    if argument_collection is None:
        return False
    if parameter_collection is not None:
        return (
            same(argument_collection.base, parameter_collection.base)
            and isinstance(argument_collection.rank, int)
            and isinstance(parameter_collection.rank, int)
            and argument_collection.rank > parameter_collection.rank
        )
    return compatible(argument_collection.base, parameter, ctx)


def _match_specificity(
    argument: Type, parameter: Type, ctx: Context | None = None
) -> Specificity:
    """Classify how specifically an argument matches a parameter."""
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if same(argument, parameter):
        return Specificity.EXACT
    # The order here mirrors the language's specificity ladder. The first
    # applicable category wins.
    if isinstance(parameter, TaggedType) and isinstance(argument, TaggedType):
        if _tag_requirements_met(argument.tags, parameter.tags) and same(
            argument.inner, parameter.inner
        ):
            return Specificity.TAGGED
    if _is_optional(parameter) and (
        isinstance(argument, NoneTypeNode)
        or compatible(argument, _optional_inner(parameter), ctx)
    ):
        return Specificity.OPTIONAL
    if isinstance(parameter, IntersectionType) and compatible(argument, parameter, ctx):
        return Specificity.INTERSECTION
    if isinstance(argument, NominalType) and isinstance(parameter, NominalType):
        if ctx.implements(argument.name, parameter.name):
            return Specificity.TRAIT
    if (
        isinstance(argument, CollectionType)
        and isinstance(parameter, CollectionType)
        and _collection_subtype(argument, parameter, ctx)
    ):
        return Specificity.RANK
    if isinstance(parameter, UnionType) and compatible(argument, parameter, ctx):
        return Specificity.UNION
    if _can_vectorise(argument, parameter, ctx):
        return Specificity.VECTORISED
    if isinstance(argument, CallSiteCheckedFunctionType) and compatible(
        argument, parameter, ctx
    ):
        return Specificity.CALL_SITE_CHECKED
    if compatible(argument, parameter, ctx):
        return Specificity.EXACT_GENERIC
    return Specificity.NO_MATCH


def apply_overload(
    overload: Overload,
    args: tuple[Type, ...],
    ctx: Context | None = None,
    *,
    disambiguation: tuple[Type | None, ...] = (),
) -> AppliedOverload | None:
    """Apply one overload to concrete argument types, returning details on success."""
    ctx = ctx or Context()
    if len(overload.params) != len(args):
        return None
    if disambiguation and len(disambiguation) != len(args):
        return None

    base_args = args
    vectorised_depths: tuple[int, ...] = ()
    if disambiguation:
        depths: list[int] = []
        adapted_args: list[Type] = []
        for arg, hint in zip(args, disambiguation, strict=True):
            if hint is None:
                depths.append(0)
                adapted_args.append(arg)
                continue
            if not compatible(arg, hint, ctx):
                return None
            excess = _vectorisation_excess(arg, hint, ctx)
            if excess is None:
                return None
            depths.append(excess)
            adapted_args.append(hint)
        base_args = tuple(adapted_args)
        vectorised_depths = tuple(depths)

    constraints: dict[str, list[Type]] = {}
    deferred_function_args: list[tuple[FunctionType, FunctionType]] = []
    for param, arg in zip(overload.params, base_args, strict=False):
        if isinstance(param, FunctionType) and isinstance(
            arg, (FunctionType, OverloadSetType, CallSiteCheckedFunctionType)
        ):
            # Defer function argument solving. Other parameters should usually
            # determine T before we ask whether this callable fits Function[T].
            if isinstance(arg, FunctionType):
                deferred_function_args.append((param, arg))
            continue
        result = _solve(param, arg)
        if result is None:
            if _contains_type_var(param):
                return None
            continue
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)

    substitution: dict[str, Type] = {}
    for key, values in constraints.items():
        combined = _combine_all(values)
        if combined is None:
            return None
        substitution[key] = combined

    for param, arg in deferred_function_args:
        substituted_param = _substitute(param, substitution)
        if not _contains_type_var(substituted_param):
            continue
        result = _solve(substituted_param, arg)
        if result is None:
            return None
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)

    substitution = {}
    for key, values in constraints.items():
        combined = _combine_all(values)
        if combined is None:
            return None
        substitution[key] = combined

    params = tuple(_substitute(param, substitution) for param in overload.params)
    returns = tuple(_substitute(ret, substitution) for ret in overload.returns)
    if not _generic_constraints_met(
        overload.generic_constraints,
        substitution,
        ctx,
    ):
        return None
    if not all(
        compatible(arg, param, ctx)
        for arg, param in zip(base_args, params, strict=False)
    ):
        return None

    actual_returns = _overload_result_for_args(
        Overload(params, returns),
        base_args,
        ctx,
    )
    if actual_returns is None:
        return None
    actual_returns = _wrap_returns_for_vector_depth(
        actual_returns,
        args,
        vectorised_depths,
    )
    # returns = declared returns after generic substitution.
    # actual_returns = returns after call adaptation such as vectorisation.

    scores = tuple(
        _match_specificity(arg, param, ctx)
        for arg, param in zip(base_args, params, strict=False)
    )
    if any(score == Specificity.NO_MATCH for score in scores):
        return None

    return AppliedOverload(
        overload,
        substitution,
        params,
        returns,
        actual_returns,
        scores,
        any(score == Specificity.VECTORISED for score in scores)
        or any(depth > 0 for depth in vectorised_depths),
        vectorised_depths,
    )


def apply_overload_to_stack(
    overload: Overload,
    stack: TypeStack,
    ctx: Context | None = None,
    *,
    infer_missing: bool = False,
) -> StackApplication | None:
    """Apply one overload to a stack, optionally inferring missing inputs."""
    ctx = ctx or Context()
    arity = len(overload.params)
    if len(stack) < arity and not infer_missing:
        return None

    available_count = min(len(stack), arity)
    missing_count = arity - available_count
    available = stack.items[-available_count:] if available_count else ()
    remaining_stack = stack.items[:-available_count] if available_count else stack.items

    if infer_missing:
        # During definition-site inference, missing stack values become inferred
        # function inputs. During normal checking, missing values are an error.
        seed_args = overload.params[:missing_count] + available
        inputs = overload.params[:missing_count]
    else:
        seed_args = available
        inputs = ()

    applied = apply_overload(overload, seed_args, ctx)
    if applied is None:
        return None

    solved_inputs = tuple(_substitute(param, applied.substitution) for param in inputs)
    new_stack = TypeStack(remaining_stack).push(*applied.actual_returns)
    return StackApplication(
        overload=overload,
        substitution=applied.substitution,
        inputs=solved_inputs,
        stack=new_stack,
        params=applied.params,
        returns=applied.returns,
        actual_returns=applied.actual_returns,
        scores=applied.scores,
        vectorised=applied.vectorised,
    )


def apply_overloads_to_stack(
    overloads: Iterable[Overload],
    stack: TypeStack,
    ctx: Context | None = None,
    *,
    infer_missing: bool = False,
) -> StackApplication | None:
    """Choose and apply one overload candidate to a stack."""
    winners = apply_overload_candidates_to_stack(
        overloads,
        stack,
        ctx,
        infer_missing=infer_missing,
    )
    return winners[0] if len(winners) == 1 else None


def apply_overload_candidates_to_stack(
    overloads: Iterable[Overload],
    stack: TypeStack,
    ctx: Context | None = None,
    *,
    infer_missing: bool = False,
) -> tuple[StackApplication, ...]:
    """Return all non-dominated overload candidates for a stack application."""
    ctx = ctx or Context()
    candidates: list[StackApplication] = []
    for overload in overloads:
        applied = apply_overload_to_stack(
            overload,
            stack,
            ctx,
            infer_missing=infer_missing,
        )
        if applied is not None:
            candidates.append(applied)

    winners = []
    for candidate in candidates:
        if not any(_dominates(other.scores, candidate.scores) for other in candidates):
            winners.append(candidate)
    return tuple(winners)


def resolve_overload_result(
    overloads: Iterable[Overload], args: tuple[Type, ...], ctx: Context | None = None
) -> ResolvedOverload | None:
    """Resolve overloads and return the winner with solved generic details."""
    ctx = ctx or Context()
    candidates: list[ResolvedOverload] = []
    for overload in overloads:
        applied = apply_overload(overload, args, ctx)
        if applied is None:
            continue
        candidates.append(
            ResolvedOverload(
                applied.overload,
                applied.substitution,
                applied.params,
                applied.returns,
                applied.scores,
            )
        )
    winners = []
    for candidate in candidates:
        # Specificity is a partial order, not a summed score. If two candidates
        # each win on different parameters, neither dominates and the call is
        # ambiguous.
        if not any(_dominates(other.scores, candidate.scores) for other in candidates):
            winners.append(candidate)
    return winners[0] if len(winners) == 1 else None


def _contains_type_var(t: Type) -> bool:
    """Return whether a type tree contains any generic type variable."""
    t = normalize(t)
    if isinstance(t, VarType):
        return True
    if isinstance(t, NominalType):
        return any(_contains_type_var(arg) for arg in t.args)
    if isinstance(t, (UnionType, IntersectionType)):
        return any(_contains_type_var(item) for item in t.items)
    if isinstance(t, TupleType):
        return any(_contains_type_var(item) for item in t.params)
    if isinstance(t, VariadicTupleType):
        return any(_contains_type_var(item.typ) for item in t.items)
    if isinstance(t, RowType):
        return _contains_type_var(t.base) or any(
            _contains_type_var(field.typ) for field in t.fields
        )
    if isinstance(t, CollectionType):
        return _contains_type_var(t.base)
    if isinstance(t, FunctionType):
        return any(_contains_type_var(item) for item in t.params + t.returns)
    if isinstance(t, (TaggedType, ExactType, AtomicType)):
        return _contains_type_var(t.inner)
    return False


def _dominates(a: tuple[Specificity, ...], b: tuple[Specificity, ...]) -> bool:
    """Return whether specificity vector ``a`` strictly dominates ``b``."""
    return all(x <= y for x, y in zip(a, b, strict=False)) and any(
        x < y for x, y in zip(a, b, strict=False)
    )


def _tag_requirements_met(
    actual: frozenset[DataTag],
    required: frozenset[DataTag],
) -> bool:
    """Return whether an actual tag set satisfies required/present tags."""
    for tag in required:
        positive = DataTag(tag.name, tag.depth)
        if tag.absent:
            if positive in actual:
                return False
        elif positive not in actual:
            return False
    return True


def _has_unit_tag(tags: frozenset[DataTag], ctx: Context) -> bool:
    return any(not tag.absent and ctx.is_unit_tag(tag.name) for tag in tags)
