"""Type relations, generic solving, overload resolution, and stack merging."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from valiance.symbols import Symbol
from valiance.types.builders import (
    ERR,
    OK,
    RESULT,
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
    AnonymousTraitRequirement,
    AnonymousTraitType,
    AppliedOverload,
    ArrayExactType,
    ArrayMinType,
    AtomicType,
    CollectionType,
    DataTag,
    ElementTag,
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
    OverloadAttempt,
    OverloadMismatch,
    OverloadMismatchReason,
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

    if isinstance(target, AnonymousTraitType):
        return _satisfies_anonymous_trait(source, target, ctx)

    if isinstance(source, NominalType) and isinstance(target, NominalType):
        if target.name == RESULT and len(target.args) == 2:
            return _source_subtypes_result(source, target, ctx)
        if source.name == target.name and len(source.args) == len(target.args):
            return _nominal_args_subtype(source, target, ctx)
        if ctx.implements(source.name, target.name):
            return True
        if target.name == ERR and _is_builtin_err(source):
            return True
        if ctx.variant_members.get(source.name) == target.name:
            return True
        if source.name == INTEGER and target.name in {REAL, NUMBER}:
            return True
        if source.name == REAL and target.name == NUMBER:
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


def _satisfies_anonymous_trait(
    source: Type,
    target: AnonymousTraitType,
    ctx: Context,
) -> bool:
    return _solve_anonymous_trait(source, target, ctx) is not None


def _solve_anonymous_trait(
    source: Type,
    target: AnonymousTraitType,
    ctx: Context,
) -> dict[str, list[Type]] | None:
    constraints: dict[str, list[Type]] = {}
    subject = _anonymous_trait_subject_name(target)
    if subject is not None:
        constraints[subject] = [source]
    for requirement in target.requirements:
        updated = _anonymous_requirement_constraints(requirement, constraints, ctx)
        if updated is None:
            return None
        constraints = updated
    return constraints


def _anonymous_trait_subject_name(target: AnonymousTraitType) -> str | None:
    if target.generics:
        return target.generics[0].text
    for requirement in target.requirements:
        for item in requirement.overload.params + requirement.overload.returns:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    return None


def _first_type_var_name(typ: Type) -> str | None:
    typ = normalize(typ)
    if isinstance(typ, VarType):
        return typ.name
    if isinstance(typ, NominalType):
        for arg in typ.args:
            name = _first_type_var_name(arg)
            if name is not None:
                return name
    if isinstance(typ, (UnionType, IntersectionType)):
        for item in typ.items:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, TupleType):
        for item in typ.params:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, VariadicTupleType):
        for item in typ.items:
            name = _first_type_var_name(item.typ)
            if name is not None:
                return name
    if isinstance(typ, RowType):
        name = _first_type_var_name(typ.base)
        if name is not None:
            return name
        for field in typ.fields:
            name = _first_type_var_name(field.typ)
            if name is not None:
                return name
    if isinstance(typ, CollectionType):
        return _first_type_var_name(typ.base)
    if isinstance(typ, FunctionType):
        if typ.params is not None:
            for item in typ.params:
                name = _first_type_var_name(item)
                if name is not None:
                    return name
        if typ.returns is not None:
            for item in typ.returns:
                name = _first_type_var_name(item)
                if name is not None:
                    return name
    if isinstance(typ, AnonymousTraitType):
        return _anonymous_trait_subject_name(typ)
    if isinstance(typ, (TaggedType, ExactType, AtomicType)):
        return _first_type_var_name(typ.inner)
    return None


def _anonymous_requirement_constraints(
    requirement: AnonymousTraitRequirement,
    constraints: dict[str, list[Type]],
    ctx: Context,
) -> dict[str, list[Type]] | None:
    for candidate in ctx.overloads_for_structural_trait(requirement.name):
        merged = {key: list(values) for key, values in constraints.items()}
        subst = _combined_substitution(merged)
        if subst is None:
            continue
        expected = _substitute_overload(requirement.overload, subst)
        inferred = _solve_overload_shape(expected, candidate, ctx)
        if inferred is None:
            continue
        for key, values in inferred.items():
            merged.setdefault(key, []).extend(values)
        subst = _combined_substitution(merged)
        if subst is None:
            continue
        expected = _substitute_overload(requirement.overload, subst)
        if _overload_satisfies_requirement(candidate, expected, ctx):
            return merged
    return None


def _combined_substitution(
    constraints: dict[str, list[Type]],
) -> dict[str, Type] | None:
    substitution: dict[str, Type] = {}
    for key, values in constraints.items():
        combined = _combine_all(values)
        if combined is None:
            return None
        substitution[key] = combined
    return substitution


def _solve_overload_shape(
    expected: Overload,
    candidate: Overload,
    ctx: Context,
) -> dict[str, list[Type]] | None:
    if len(expected.params) != len(candidate.params) or len(expected.returns) != len(
        candidate.returns
    ):
        return None
    constraints: dict[str, list[Type]] = {}
    for pattern, actual in zip(
        expected.params + expected.returns,
        candidate.params + candidate.returns,
        strict=True,
    ):
        result = _solve(pattern, actual, ctx)
        if result is None:
            if _contains_type_var(pattern):
                return None
            continue
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)
    return constraints


def _overload_satisfies_requirement(
    candidate: Overload,
    expected: Overload,
    ctx: Context,
) -> bool:
    if len(candidate.params) == len(expected.params) and len(candidate.returns) == len(
        expected.returns
    ) and all(
        same(candidate_param, expected_param)
        for candidate_param, expected_param in zip(
            candidate.params,
            expected.params,
            strict=False,
        )
    ) and all(
        same(candidate_return, expected_return)
        for candidate_return, expected_return in zip(
            candidate.returns,
            expected.returns,
            strict=False,
        )
    ):
        return True
    actual_returns = _overload_result_for_args(candidate, expected.params, ctx)
    return actual_returns is not None and len(actual_returns) == len(
        expected.returns
    ) and all(
        assignable(actual, required, ctx)
        for actual, required in zip(actual_returns, expected.returns, strict=False)
    )


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

    if same(source, target):
        return True
    if isinstance(target, ExactType):
        return assignable(source, target.inner, ctx)

    if subtype(source, target, ctx):
        return True

    if _is_boolean_number_to_integer(source, target):
        return True

    if (
        isinstance(source, AnonymousTraitType)
        and source.generics
        and isinstance(target, VarType)
        and target.name == source.generics[0].text
    ):
        return True

    if (
        isinstance(target, NominalType)
        and target.name == RESULT
        and len(target.args) == 2
    ):
        ok, err = target.args
        if (
            isinstance(source, NominalType)
            and source.name == OK
            and len(source.args) == 1
        ):
            return assignable(source.args[0], ok, ctx)
        return assignable(source, ok, ctx) or assignable(source, err, ctx)

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


def _is_boolean_number_to_integer(source: Type, target: Type) -> bool:
    source = normalize(source)
    target = normalize(target)
    if not isinstance(source, TaggedType) or not isinstance(target, TaggedType):
        return False
    if DataTag("boolean") not in source.tags or DataTag("boolean") not in target.tags:
        return False
    return same(source.inner, N(NUMBER)) and same(target.inner, N(INTEGER))


def _source_subtypes_result(
    source: NominalType,
    target: NominalType,
    ctx: Context,
) -> bool:
    ok, err = target.args
    if source.name == OK and len(source.args) == 1:
        return assignable(source.args[0], ok, ctx)
    return assignable(source, ok, ctx) or assignable(source, err, ctx)


def _is_builtin_err(source: NominalType) -> bool:
    return not source.args and source.name.text.endswith("Error")


def _solve(
    pattern: Type,
    actual: Type,
    ctx: Context | None = None,
) -> dict[str, list[Type]] | None:
    """Collect generic constraints by matching a parameter pattern to an argument."""
    ctx = ctx or Context()
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
            if _contains_named_type_var(a, p.name):
                return True
            add(p.name, a)
            return True
        if same(p, a):
            return True
        if isinstance(p, ExactType):
            return rec(p.inner, a)
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
            if p.name == RESULT and len(p.args) == 2:
                if a.name == OK and len(a.args) == 1:
                    if not rec(p.args[0], a.args[0]):
                        return False
                    if isinstance(normalize(p.args[1]), VarType):
                        add(normalize(p.args[1]).name, NeverType())
                    return True
                if _is_builtin_err(a):
                    if isinstance(normalize(p.args[0]), VarType):
                        add(normalize(p.args[0]).name, NeverType())
                    return rec(p.args[1], a)
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
            if p.params is None or p.returns is None:
                return True
            if a.params is None or a.returns is None:
                return False
            if len(p.params) != len(a.params) or len(p.returns) != len(a.returns):
                return False
            for expected, actual in zip(p.params, a.params, strict=False):
                if _contains_type_var(expected):
                    if not rec(expected, actual):
                        return False
                elif not compatible(expected, actual):
                    return False
            actual_returns = _overload_result_for_args(
                Overload(a.params, a.returns),
                p.params,
                ctx,
            )
            if actual_returns is None:
                return False
            return all(
                rec(expected, actual)
                for expected, actual in zip(p.returns, actual_returns, strict=False)
            )
        if isinstance(p, FunctionType) and isinstance(a, OverloadSetType):
            matches: list[dict[str, list[Type]]] = []
            for overload in a.overloads:
                saved = {key: list(values) for key, values in constraints.items()}
                candidate = Fn(overload.params, overload.returns, overload.element_tags)
                if rec(p, candidate):
                    matches.append(
                        {key: list(values) for key, values in constraints.items()}
                    )
                constraints.clear()
                constraints.update(saved)
            if not matches:
                return False
            merged: dict[str, list[Type]] = {}
            for match in matches:
                for key, values in match.items():
                    merged.setdefault(key, []).extend(values)
            constraints.clear()
            constraints.update(merged)
            return True
        if isinstance(p, TaggedType):
            if not isinstance(a, TaggedType):
                return all(tag.absent for tag in p.tags) and rec(p.inner, a)
            if not _tag_requirements_met(a.tags, p.tags):
                return False
            return rec(p.inner, a.inner)
        if isinstance(a, TaggedType):
            return rec(p, a.inner)
        if isinstance(p, CollectionType) and isinstance(a, CollectionType):
            if (
                not isinstance(p.base, VarType)
                and type(p) is type(a)
                and p.rank == a.rank
            ):
                return rec(p.base, a.base)
            return _solve_collection(p, a, add)
        if isinstance(p, AnonymousTraitType):
            result = _solve_anonymous_trait(a, p, ctx)
            if result is None:
                return False
            for key, values in result.items():
                constraints.setdefault(key, []).extend(values)
            return True
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
    if isinstance(t, (TaggedType, ExactType)):
        return _collection_view(t.inner)
    if isinstance(t, CollectionType):
        return t
    return None


def _combine(a: Type, b: Type) -> Type | None:
    """Merge two candidate generic solutions into a shared solution."""
    a, b = normalize(a), normalize(b)
    if same(a, b):
        return a
    if assignable(a, b):
        return b
    if assignable(b, a):
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
        and _combine(a.base, b.base) is not None
    ):
        # Collection solutions can widen from exact to minimum/rugged when
        # multiple constraints need one shared generic type.
        return _combine_collections(
            type(a)(_combine(a.base, b.base), a.rank),
            type(b)(_combine(a.base, b.base), b.rank),
        )
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
    if assignable(a, b):
        return b
    if assignable(b, a):
        return a
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
        if t.params is None or t.returns is None:
            return Fn(None, None, _substitute_element_tags(t.element_tags, subst))
        return Fn(
            (_substitute(p, subst) for p in t.params),
            (_substitute(r, subst) for r in t.returns),
            _substitute_element_tags(t.element_tags, subst),
        )
    if isinstance(t, AnonymousTraitType):
        return AnonymousTraitType(
            t.generics,
            tuple(
                AnonymousTraitRequirement(
                    requirement.name,
                    _substitute_overload(requirement.overload, subst),
                )
                for requirement in t.requirements
            ),
        )
    if isinstance(t, TaggedType):
        return Tagged(_substitute(t.inner, subst), *t.tags)
    if isinstance(t, ExactType):
        return ExactType(_substitute(t.inner, subst))
    if isinstance(t, AtomicType) and isinstance(t.inner, VarType):
        solved = subst.get(t.inner.name)
        return ExactType(_atomic_of(solved)) if solved else t
    if isinstance(t, AtomicType):
        return ExactType(_atomic_of(_substitute(t.inner, subst)))
    return t


def _substitute_overload(overload: Overload, subst: dict[str, Type]) -> Overload:
    return Overload(
        tuple(_substitute(param, subst) for param in overload.params),
        tuple(_substitute(ret, subst) for ret in overload.returns),
        overload.generic_constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        overload.element_tags,
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
        overload.is_multi,
    )


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
        if constraint.variance is Variance.CONTRAVARIANT:
            bound_satisfied = assignable(bound, solution, ctx)
        elif constraint.variance is Variance.INVARIANT:
            bound_satisfied = same(solution, bound)
        else:
            bound_satisfied = assignable(solution, bound, ctx)
        if not bound_satisfied:
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
    if (
        isinstance(parameter, VarType)
        and isinstance(argument, AtomicType)
        and _contains_named_type_var(argument.inner, parameter.name)
    ):
        return True
    if isinstance(parameter, FunctionType):
        # Function compatibility is callability-based. A scalar function can be
        # compatible with a vector function type if calling it vectorises.
        if parameter.params is None or parameter.returns is None:
            return isinstance(argument, (FunctionType, OverloadSetType))
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
    constraints = _solve(parameter, argument, ctx)
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
        if not _element_tag_requirements_met(
            argument.element_tags,
            parameter.element_tags,
        ):
            return False
        if argument.params is None or argument.returns is None:
            return parameter.params is None and parameter.returns is None
        applied = apply_overload(
            Overload(argument.params, argument.returns),
            parameter.params,
            ctx,
        )
        if applied is not None and len(applied.actual_returns) == len(
            parameter.returns
        ) and all(
            compatible(a, p, ctx)
            for a, p in zip(
                applied.actual_returns,
                parameter.returns,
                strict=False,
            )
        ):
            return True
        actual_returns = _overload_result_for_args(
            Overload(argument.params, argument.returns),
            parameter.params,
            ctx,
        )
        return actual_returns is not None and len(actual_returns) == len(
            parameter.returns
        ) and all(
            compatible(a, p, ctx)
            for a, p in zip(actual_returns, parameter.returns, strict=False)
        )
    if isinstance(argument, OverloadSetType):
        # The expected Function[...] supplies the call input types for choosing
        # an overload from the callable value.
        matches = [
            o
            for o in argument.overloads
            if _overload_callable_compatible(o, parameter, ctx)
        ]
        if parameter.element_tags:
            return bool(matches)
        return len(matches) == 1 or bool(
            resolve_overload_result(argument.overloads, parameter.params, ctx)
        )
    return False


def _overload_callable_compatible(
    overload: Overload, expected: Type, ctx: Context
) -> bool:
    """Return whether one overload can be used as an expected function type."""
    if not _element_tag_requirements_met(overload.element_tags, expected.element_tags):
        return False
    if len(overload.params) != len(expected.params) or len(overload.returns) != len(
        expected.returns
    ):
        return False
    applied = apply_overload(overload, expected.params, ctx)
    return applied is not None and all(
        compatible(r, e, ctx)
        for r, e in zip(applied.actual_returns, expected.returns, strict=False)
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
    expected = normalize(expected)
    if isinstance(expected, ExactType):
        return 0 if assignable(argument, expected.inner, ctx) else None
    argument_collection = _collection_view(argument)
    if argument_collection is None:
        return 0 if compatible(argument, expected, ctx) else None
    expected_collection = _collection_view(expected)
    if expected_collection is not None:
        # Rugged rank does not guarantee a uniform outer prefix that can be
        # peeled to leave values of another collection type. It may only
        # vectorise all the way down to an atomic parameter.
        if isinstance(argument_collection, ListRuggedType):
            return None
        if not compatible(argument_collection.base, expected_collection.base, ctx):
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
        return False
    argument_collection = _collection_view(argument)
    parameter_collection = _collection_view(parameter)
    if argument_collection is None:
        return False
    if parameter_collection is not None:
        # Unlike exact and minimum-rank collections, rugged collections do not
        # promise enough uniform nesting to vectorise into a collection-valued
        # parameter. They can only vectorise where an atomic value is expected.
        if isinstance(argument_collection, ListRuggedType):
            return False
        return (
            compatible(argument_collection.base, parameter_collection.base, ctx)
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
    if isinstance(parameter, ExactType) and compatible(argument, parameter.inner, ctx):
        return Specificity.EXACT_GENERIC
    if _can_vectorise(argument, parameter, ctx):
        return Specificity.VECTORISED
    if compatible(argument, parameter, ctx):
        return Specificity.EXACT_GENERIC
    return Specificity.NO_MATCH


def try_apply_overload(
    overload: Overload,
    args: tuple[Type, ...],
    ctx: Context | None = None,
    *,
    disambiguation: tuple[Type | None, ...] = (),
) -> OverloadAttempt:
    """Apply one overload to concrete argument types with mismatch evidence."""
    ctx = ctx or Context()
    if len(overload.params) != len(args):
        return OverloadAttempt(
            None,
            OverloadMismatch(
                OverloadMismatchReason.ARITY,
                matched_arguments=min(len(overload.params), len(args)),
                detail=(
                    f"expected {len(overload.params)} argument(s), "
                    f"received {len(args)}"
                ),
            ),
        )
    if disambiguation and len(disambiguation) != len(args):
        return OverloadAttempt(
            None,
            OverloadMismatch(
                OverloadMismatchReason.DISAMBIGUATION,
                matched_arguments=min(len(disambiguation), len(args)),
                detail=(
                    f"expected {len(args)} disambiguation hint(s), "
                    f"received {len(disambiguation)}"
                ),
            ),
        )

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
                return OverloadAttempt(
                    None,
                    OverloadMismatch(
                        OverloadMismatchReason.DISAMBIGUATION,
                        argument_index=len(adapted_args),
                        expected=hint,
                        actual=arg,
                    ),
                )
            excess = _vectorisation_excess(arg, hint, ctx)
            if excess is None:
                return OverloadAttempt(
                    None,
                    OverloadMismatch(
                        OverloadMismatchReason.VECTORISATION,
                        argument_index=len(adapted_args),
                        expected=hint,
                        actual=arg,
                    ),
                )
            depths.append(excess)
            adapted_args.append(hint)
        base_args = tuple(adapted_args)
        vectorised_depths = tuple(depths)

    constraints: dict[str, list[Type]] = {}
    deferred_function_args: list[
        tuple[FunctionType, FunctionType | OverloadSetType]
    ] = []
    for index, (param, arg) in enumerate(zip(overload.params, base_args, strict=False)):
        if isinstance(param, FunctionType) and isinstance(
            arg, (FunctionType, OverloadSetType)
        ):
            # Defer function argument solving. Other parameters should usually
            # determine T before we ask whether this callable fits Function[T].
            deferred_function_args.append((param, arg))
            continue
        result = _solve(param, arg, ctx)
        if result is None:
            if _contains_type_var(param):
                return OverloadAttempt(
                    None,
                    OverloadMismatch(
                        OverloadMismatchReason.ARGUMENT_TYPE,
                        matched_arguments=index,
                        argument_index=index,
                        expected=param,
                        actual=arg,
                    ),
                )
            continue
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)

    substitution: dict[str, Type] = {}
    for key, values in constraints.items():
        combined = _combine_all(values)
        if combined is None:
            return OverloadAttempt(
                None,
                OverloadMismatch(
                    OverloadMismatchReason.GENERIC_CONSTRAINT,
                    detail=f"generic '{key}' has incompatible inferred bounds",
                ),
            )
        substitution[key] = combined

    for param, arg in deferred_function_args:
        substituted_param = _substitute(param, substitution)
        if isinstance(arg, OverloadSetType):
            result = _solve(substituted_param, arg, ctx)
        else:
            if (
                not _contains_type_var(substituted_param)
                and compatible(arg, substituted_param, ctx)
            ):
                continue
            result = _solve(
                substituted_param if _contains_type_var(substituted_param) else param,
                arg,
                ctx,
            )
        if result is None:
            return OverloadAttempt(
                None,
                OverloadMismatch(
                    OverloadMismatchReason.ARGUMENT_TYPE,
                    expected=substituted_param,
                    actual=arg,
                ),
            )
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)

    substitution = {}
    for key, values in constraints.items():
        combined = _combine_all(values)
        if combined is None:
            return OverloadAttempt(
                None,
                OverloadMismatch(
                    OverloadMismatchReason.GENERIC_CONSTRAINT,
                    detail=f"generic '{key}' has incompatible inferred bounds",
                ),
            )
        substitution[key] = combined

    params = tuple(_substitute(param, substitution) for param in overload.params)
    returns = tuple(_substitute(ret, substitution) for ret in overload.returns)
    if not _generic_constraints_met(
        overload.generic_constraints,
        substitution,
        ctx,
    ):
        return OverloadAttempt(
            None,
            OverloadMismatch(OverloadMismatchReason.GENERIC_CONSTRAINT),
        )
    for index, (arg, param) in enumerate(zip(base_args, params, strict=False)):
        if not compatible(arg, param, ctx):
            return OverloadAttempt(
                None,
                OverloadMismatch(
                    OverloadMismatchReason.ARGUMENT_TYPE,
                    matched_arguments=index,
                    argument_index=index,
                    expected=param,
                    actual=arg,
                ),
            )

    actual_returns = _overload_result_for_args(
        Overload(params, returns),
        base_args,
        ctx,
    )
    if actual_returns is None:
        return OverloadAttempt(
            None,
            OverloadMismatch(OverloadMismatchReason.RESULT),
        )
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
        index = next(
            index for index, score in enumerate(scores) if score == Specificity.NO_MATCH
        )
        return OverloadAttempt(
            None,
            OverloadMismatch(
                OverloadMismatchReason.ARGUMENT_TYPE,
                matched_arguments=index,
                argument_index=index,
                expected=params[index],
                actual=base_args[index],
            ),
        )

    if not disambiguation:
        # Automatic vectorisation also needs explicit per-argument depths at
        # runtime.  A zero depth broadcasts the argument unchanged; this is
        # essential for exact parameters when a different argument vectorises.
        automatic_depths: list[int] = []
        for arg, param, score in zip(base_args, params, scores, strict=False):
            if score != Specificity.VECTORISED:
                automatic_depths.append(0)
                continue
            excess = _vectorisation_excess(arg, param, ctx)
            if excess is None or excess <= 0:
                return OverloadAttempt(
                    None,
                    OverloadMismatch(OverloadMismatchReason.VECTORISATION),
                )
            automatic_depths.append(excess)
        if any(depth > 0 for depth in automatic_depths):
            vectorised_depths = tuple(automatic_depths)

    return OverloadAttempt(
        AppliedOverload(
            overload,
            substitution,
            params,
            returns,
            actual_returns,
            scores,
            any(score == Specificity.VECTORISED for score in scores)
            or any(depth > 0 for depth in vectorised_depths),
            vectorised_depths,
            element_tags=overload.element_tags,
        )
    )


def apply_overload(
    overload: Overload,
    args: tuple[Type, ...],
    ctx: Context | None = None,
    *,
    disambiguation: tuple[Type | None, ...] = (),
) -> AppliedOverload | None:
    """Apply one overload to concrete argument types, returning details on success."""
    return try_apply_overload(
        overload,
        args,
        ctx,
        disambiguation=disambiguation,
    ).applied


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
        if not any(
            _application_dominates(other, candidate, ctx)
            for other in candidates
        ):
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
        if not any(
            _resolved_overload_dominates(other, candidate, ctx)
            for other in candidates
        ):
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
        if t.params is None or t.returns is None:
            return _element_tags_contain_type_var(t.element_tags)
        return any(_contains_type_var(item) for item in t.params + t.returns) or (
            _element_tags_contain_type_var(t.element_tags)
        )
    if isinstance(t, AnonymousTraitType):
        return any(
            _contains_type_var(item)
            for requirement in t.requirements
            for item in requirement.overload.params + requirement.overload.returns
        )
    if isinstance(t, (TaggedType, ExactType, AtomicType)):
        return _contains_type_var(t.inner)
    return False


def _contains_named_type_var(t: Type, name: str) -> bool:
    """Return whether a type tree contains the named generic type variable."""
    t = normalize(t)
    if isinstance(t, VarType):
        return t.name == name
    if isinstance(t, NominalType):
        return any(_contains_named_type_var(arg, name) for arg in t.args)
    if isinstance(t, (UnionType, IntersectionType)):
        return any(_contains_named_type_var(item, name) for item in t.items)
    if isinstance(t, TupleType):
        return any(_contains_named_type_var(item, name) for item in t.params)
    if isinstance(t, VariadicTupleType):
        return any(_contains_named_type_var(item.typ, name) for item in t.items)
    if isinstance(t, RowType):
        return _contains_named_type_var(t.base, name) or any(
            _contains_named_type_var(field.typ, name) for field in t.fields
        )
    if isinstance(t, CollectionType):
        return _contains_named_type_var(t.base, name)
    if isinstance(t, FunctionType):
        if t.params is None or t.returns is None:
            return _element_tags_contain_named_type_var(t.element_tags, name)
        return any(
            _contains_named_type_var(item, name) for item in t.params + t.returns
        ) or _element_tags_contain_named_type_var(t.element_tags, name)
    if isinstance(t, AnonymousTraitType):
        return any(
            _contains_named_type_var(item, name)
            for requirement in t.requirements
            for item in requirement.overload.params + requirement.overload.returns
        )
    if isinstance(t, (TaggedType, ExactType, AtomicType)):
        return _contains_named_type_var(t.inner, name)
    return False


def _dominates(a: tuple[Specificity, ...], b: tuple[Specificity, ...]) -> bool:
    """Return whether specificity vector ``a`` strictly dominates ``b``."""
    return all(x <= y for x, y in zip(a, b, strict=False)) and any(
        x < y for x, y in zip(a, b, strict=False)
    )


def _application_dominates(
    left: StackApplication,
    right: StackApplication,
    ctx: Context,
) -> bool:
    return _overload_match_dominates(
        left.scores,
        left.params,
        right.scores,
        right.params,
        ctx,
    )


def _resolved_overload_dominates(
    left: ResolvedOverload,
    right: ResolvedOverload,
    ctx: Context,
) -> bool:
    return _overload_match_dominates(
        left.scores,
        left.params,
        right.scores,
        right.params,
        ctx,
    )


def _overload_match_dominates(
    left_scores: tuple[Specificity, ...],
    left_params: tuple[Type, ...],
    right_scores: tuple[Specificity, ...],
    right_params: tuple[Type, ...],
    ctx: Context,
) -> bool:
    if _dominates(left_scores, right_scores):
        return True
    if left_scores != right_scores:
        return False
    return _params_more_specific(left_params, right_params, ctx)


def _params_more_specific(
    left: tuple[Type, ...],
    right: tuple[Type, ...],
    ctx: Context,
) -> bool:
    return all(
        _type_more_specific_or_same(left_item, right_item, ctx)
        for left_item, right_item in zip(left, right, strict=False)
    ) and any(
        not _type_more_specific_or_same(right_item, left_item, ctx)
        for left_item, right_item in zip(left, right, strict=False)
    )


def _type_more_specific_or_same(left: Type, right: Type, ctx: Context) -> bool:
    left = normalize(left)
    right = normalize(right)
    if same(left, right) or subtype(left, right, ctx):
        return True
    if isinstance(left, FunctionType) and isinstance(right, FunctionType):
        if left.params is None or left.returns is None:
            return right.params is None and right.returns is None
        if right.params is None or right.returns is None:
            return True
        if len(left.params) != len(right.params) or len(left.returns) != len(
            right.returns
        ):
            return False
        return all(
            _type_more_specific_or_same(left_item, right_item, ctx)
            for left_item, right_item in zip(
                left.params,
                right.params,
                strict=True,
            )
        ) and all(
            _type_more_specific_or_same(left_item, right_item, ctx)
            for left_item, right_item in zip(
                left.returns,
                right.returns,
                strict=True,
            )
        )
    return False


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


def _element_tag_requirements_met(
    actual: frozenset[ElementTag],
    required: frozenset[ElementTag],
) -> bool:
    for tag in required:
        positive = ElementTag(tag.name, tag.args)
        if tag.absent:
            if positive in actual:
                return False
        elif positive not in actual:
            return False
    return True


def _substitute_element_tags(
    tags: frozenset[ElementTag],
    subst: dict[str, Type],
) -> tuple[ElementTag, ...]:
    return tuple(
        ElementTag(
            tag.name,
            tuple(_substitute(arg, subst) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _element_tags_contain_type_var(tags: frozenset[ElementTag]) -> bool:
    return any(_contains_type_var(arg) for tag in tags for arg in tag.args)


def _element_tags_contain_named_type_var(
    tags: frozenset[ElementTag],
    name: str,
) -> bool:
    return any(
        _contains_named_type_var(arg, name) for tag in tags for arg in tag.args
    )


def _has_unit_tag(tags: frozenset[DataTag], ctx: Context) -> bool:
    return any(not tag.absent and ctx.is_unit_tag(tag.name) for tag in tags)
