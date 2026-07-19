"""Type relations, generic solving, overload resolution, and stack merging."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from itertools import product

from valiance.vtypes.symbols import Symbol
from valiance.vtypes.structural import anonymous_trait_subject_name
from valiance.vtypes.builders import (
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
    show,
)
from valiance.vtypes.context import Context, Variance
from valiance.vtypes.nodes import (
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
    RuntimeTypePattern,
    Specificity,
    TaggedType,
    TupleType,
    Type,
    UnionDispatchBranch,
    UnionDispatchPlan,
    UnionType,
    VariadicTupleType,
    VarType,
)
from valiance.vtypes.stack import StackApplication, TypeStack

CollectionClass = type[CollectionType]
BOOLEAN = Symbol("Boolean")
INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")
SOME = Symbol("Some")


def _match_variadic_tuple(
    pattern: tuple[object, ...],
    actual: tuple[Type, ...],
    match: Callable[[Type, Type], bool],
) -> bool:
    """Return whether a fixed tuple matches a repeated tuple pattern."""
    seen: set[tuple[int, int]] = set()

    def rec(pattern_index: int, actual_index: int) -> bool:
        """Recursively continue the match variadic tuple algorithm."""
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
    if isinstance(source, CollectionType) and isinstance(target, CollectionType):
        if _collection_subtype(source, target, ctx):
            return True
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or isinstance(source, NeverType):
        return True

    if _is_optional(source) and _is_optional(target):
        source_inner = _optional_inner(source)
        target_inner = _optional_inner(target)
        if source_inner is None or target_inner is None:
            return source_inner is target_inner
        return assignable(source_inner, target_inner, ctx)

    # Decompose algebraic source/target forms before applying target-specific
    # refinements. Otherwise a tagged/row target sees the aggregate node rather
    # than each union branch or one guaranteed intersection constituent.
    if isinstance(source, UnionType):
        return all(subtype(s, target, ctx) for s in source.items)

    if isinstance(target, UnionType):
        return any(subtype(source, t, ctx) for t in target.items)

    if isinstance(target, IntersectionType):
        return all(subtype(source, t, ctx) for t in target.items)

    if isinstance(source, IntersectionType):
        return any(subtype(s, target, ctx) for s in source.items)

    if isinstance(target, TaggedType):
        # Resolve tag requirements before allowing erasable source tags to be
        # forgotten. Otherwise ``#a T`` could satisfy ``#-a T`` or ``[] T``
        # by erasing the very evidence the target is checking.
        actual_tags = source.tags if isinstance(source, TaggedType) else frozenset()
        inner = source.inner if isinstance(source, TaggedType) else source
        if not _unit_tags_preserved(actual_tags, target.tags, ctx):
            return False
        if not _tag_requirements_met(actual_tags, target.tags, exact=target.exact):
            return False
        return subtype(inner, target.inner, ctx)

    if isinstance(source, TaggedType):
        if not _has_unit_tag(source.tags, ctx) and subtype(source.inner, target, ctx):
            return True

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
    """Return whether the value satisfies anonymous trait."""
    return _solve_anonymous_trait(source, target, ctx) is not None


def _solve_anonymous_trait(
    source: Type,
    target: AnonymousTraitType,
    ctx: Context,
) -> dict[str, list[Type]] | None:
    """Solve a trait across every coherent requirement/overload path."""
    constraints: dict[str, list[Type]] = {}
    subject = anonymous_trait_subject_name(target)
    if subject is not None:
        constraints[subject] = [source]

    completed: list[dict[str, Type]] = []
    seen: set[tuple[int, tuple[tuple[str, Type], ...]]] = set()

    def solve_requirement(
        index: int,
        current: dict[str, list[Type]],
    ) -> None:
        """Collect complete paths while deduplicating equivalent solver states."""
        substitution = _combined_substitution(current, ctx)
        if substitution is None:
            return
        state = (index, tuple(sorted(substitution.items())))
        if state in seen:
            return
        seen.add(state)
        if index == len(target.requirements):
            completed.append(substitution)
            return
        requirement = target.requirements[index]
        for updated in _anonymous_requirement_constraint_options(
            requirement,
            current,
            ctx,
        ):
            solve_requirement(index + 1, updated)

    solve_requirement(0, constraints)
    if not completed:
        return None

    merged: dict[str, list[Type]] = {}
    for solution in completed:
        for name, typ in solution.items():
            merged.setdefault(name, []).append(typ)
    return merged if _combined_substitution(merged, ctx) is not None else None




def _first_type_var_name(typ: Type) -> str | None:
    """Return the first nested type-variable name, if any."""
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
        return anonymous_trait_subject_name(typ)
    if isinstance(typ, (TaggedType, ExactType, AtomicType)):
        return _first_type_var_name(typ.inner)
    return None


def _anonymous_requirement_constraint_options(
    requirement: AnonymousTraitRequirement,
    constraints: dict[str, list[Type]],
    ctx: Context,
) -> tuple[dict[str, list[Type]], ...]:
    """Return every coherent candidate solution for one trait requirement."""
    options: list[dict[str, list[Type]]] = []
    for candidate in ctx.overloads_for_structural_trait(requirement.name):
        merged = {key: list(values) for key, values in constraints.items()}
        subst = _combined_substitution(merged, ctx)
        if subst is None:
            continue
        expected = _substitute_overload(requirement.overload, subst)
        inferred = _solve_overload_shape(expected, candidate, ctx)
        if inferred is None:
            continue
        for key, values in inferred.items():
            merged.setdefault(key, []).extend(values)
        subst = _combined_substitution(merged, ctx)
        if subst is None:
            continue
        expected = _substitute_overload(requirement.overload, subst)
        if _overload_satisfies_requirement(candidate, expected, ctx):
            options.append(merged)
    return tuple(options)


def _combined_substitution(
    constraints: dict[str, list[Type]],
    ctx: Context | None = None,
) -> dict[str, Type] | None:
    """Compute a coherent substitution for accumulated generic evidence."""
    substitution: dict[str, Type] = {}
    for key, values in constraints.items():
        combined = _combine_all(values, ctx)
        if combined is None:
            return None
        substitution[key] = combined
    return substitution


def _solve_overload_shape(
    expected: Overload,
    candidate: Overload,
    ctx: Context,
) -> dict[str, list[Type]] | None:
    """Solve overload shape during type and overload solving."""
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
    """Return the Boolean result of overload satisfies requirement during type solving and overload resolution."""
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
    applied = apply_overload(candidate, expected.params, ctx)
    return applied is not None and len(applied.actual_returns) == len(
        expected.returns
    ) and all(
        assignable(actual, required, ctx)
        for actual, required in zip(
            applied.actual_returns,
            expected.returns,
            strict=False,
        )
    )


def _nominal_args_subtype(
    source: NominalType,
    target: NominalType,
    ctx: Context,
) -> bool:
    """Return whether nominal args satisfy declared variance for this constructor."""
    # ``Some`` is the reified present branch of Optional and therefore follows
    # the same covariance as Optional itself, even in a bare Context that has
    # not loaded the built-in environment declarations.
    variances = (
        (Variance.COVARIANT,)
        if source.name == SOME and len(source.args) == 1
        else ctx.variance_for(source.name, len(source.args))
    )
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
    if _direct_collection_subtype(source, target, ctx):
        return True

    # A nested array can be viewed as the corresponding nested list without
    # discarding its canonical array boundary.  This matters when the other
    # side has already collapsed adjacent list ranks during normalization.
    source_view = _collection_list_view(source)
    normalized_target = normalize(target)
    if source_view != source or normalized_target != target:
        return _direct_collection_subtype(source_view, normalized_target, ctx)
    return False


def _direct_collection_subtype(source: Type, target: Type, ctx: Context) -> bool:
    """Check collection compatibility without changing explicit item layers."""
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


def _collection_list_view(collection: CollectionType) -> CollectionType:
    """Return the list-shaped view available to a source collection value."""
    base = collection.base
    if isinstance(base, CollectionType):
        base = _collection_list_view(base)
    collection_type = {
        ArrayExactType: ListExactType,
        ArrayMinType: ListMinType,
    }.get(type(collection), type(collection))
    viewed = collection_type(base, collection.rank)
    normalized = normalize(viewed)
    if not isinstance(normalized, CollectionType):
        raise TypeError("collection list view normalized to a non-collection type")
    return normalized


def _rank_ge(left: object, right: object) -> bool:
    """Return the Boolean result of rank ge during type solving and overload resolution."""
    return isinstance(left, int) and isinstance(right, int) and left >= right


def assignable(source: Type, target: Type, ctx: Context | None = None) -> bool:
    """Return whether a value of ``source`` can be stored in ``target``."""
    ctx = ctx or Context()
    if isinstance(source, CollectionType) and isinstance(target, CollectionType):
        if _collection_subtype(source, target, ctx):
            return True
    source = normalize(source)
    target = normalize(target)

    if same(source, target):
        return True
    if isinstance(target, ExactType):
        return assignable(source, target.inner, ctx)
    if isinstance(target, AtomicType):
        return _is_scalar_type(source) and assignable(source, target.inner, ctx)

    if subtype(source, target, ctx):
        return True

    if _is_boolean_number_to_integer(source, target):
        return True

    # A union source must satisfy target-specific assignment rules branch by
    # branch. In particular, None | T is assignable to Optional[U] when None
    # and T are each accepted, even though the union node itself is not a
    # present value that can be implicitly wrapped.
    if isinstance(source, UnionType):
        return all(assignable(item, target, ctx) for item in source.items)

    if (
        isinstance(source, AnonymousTraitType)
        and source.generics
        and isinstance(target, VarType)
        and target.name == source.generics[0].text
    ):
        return True

    if (
        isinstance(target, NominalType)
        and target.name == OK
        and len(target.args) == 1
    ):
        if (
            isinstance(source, NominalType)
            and source.name == OK
            and len(source.args) == 1
        ):
            return assignable(source.args[0], target.args[0], ctx)
        return assignable(source, target.args[0], ctx)

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
        # can be stored in any optional. An already wrapped Some[S] checks its
        # payload against T, which makes Optional covariant in its payload.
        inner = _optional_inner(target)
        if isinstance(source, NoneTypeNode):
            return True
        if (
            inner is not None
            and isinstance(source, NominalType)
            and source.name == SOME
            and len(source.args) == 1
        ):
            return assignable(source.args[0], inner, ctx)
        return inner is not None and assignable(source, inner, ctx)

    if isinstance(target, UnionType):
        return any(assignable(source, t, ctx) for t in target.items)

    return False


def _is_boolean_number_to_integer(source: Type, target: Type) -> bool:
    """Return whether the value is boolean number to integer."""
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
    """Return the Boolean result of source subtypes result during type solving and overload resolution."""
    ok, err = target.args
    if source.name == RESULT and len(source.args) == 2:
        source_ok, source_err = source.args
        return assignable(source_ok, ok, ctx) and assignable(source_err, err, ctx)
    if source.name == OK and len(source.args) == 1:
        return assignable(source.args[0], ok, ctx)
    return assignable(source, ok, ctx) or assignable(source, err, ctx)


def _is_result_injection(source: Type, target: Type, ctx: Context) -> bool:
    """Return whether ``source`` is implicitly injected into a ``Result``."""
    source = normalize(source)
    target = normalize(target)
    if not (
        isinstance(target, NominalType)
        and target.name == RESULT
        and len(target.args) == 2
    ):
        return False
    if isinstance(source, NominalType) and source.name == RESULT:
        return False
    ok, err = target.args
    if (
        isinstance(source, NominalType)
        and source.name == OK
        and len(source.args) == 1
    ):
        return assignable(source.args[0], ok, ctx)
    return assignable(source, ok, ctx) or assignable(source, err, ctx)


def _is_builtin_err(source: NominalType) -> bool:
    """Return whether the value is builtin err."""
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

    def rec_element_tags(
        required: frozenset[ElementTag],
        actual: frozenset[ElementTag],
    ) -> bool:
        """Return the Boolean result of rec element tags during type solving and overload resolution."""
        present = tuple(tag for tag in actual if not tag.absent)
        for requirement in required:
            candidates = tuple(
                tag for tag in present if tag.name == requirement.name
            )
            if requirement.absent:
                if not requirement.args:
                    if candidates:
                        return False
                    continue
                if any(_contains_type_var(arg) for arg in requirement.args):
                    if candidates:
                        return False
                    continue
                if any(
                    _element_tag_conflicts(candidate, requirement, ctx)
                    for candidate in candidates
                ):
                    return False
                continue
            if not candidates:
                return False
            if not requirement.args:
                continue

            matches: list[dict[str, list[Type]]] = []
            original = {key: list(values) for key, values in constraints.items()}
            for candidate in candidates:
                constraints.clear()
                constraints.update(
                    {key: list(values) for key, values in original.items()}
                )
                if len(candidate.args) != len(requirement.args):
                    continue
                matched = all(
                    rec(required_arg, actual_arg)
                    if _contains_type_var(required_arg)
                    else assignable(actual_arg, required_arg, ctx)
                    for required_arg, actual_arg in zip(
                        requirement.args,
                        candidate.args,
                        strict=True,
                    )
                )
                if matched:
                    matches.append(
                        {key: list(values) for key, values in constraints.items()}
                    )
            if not matches:
                constraints.clear()
                constraints.update(original)
                return False
            merged = {key: list(values) for key, values in original.items()}
            for match in matches:
                for key, values in match.items():
                    existing_count = len(original.get(key, ()))
                    merged.setdefault(key, []).extend(values[existing_count:])
            constraints.clear()
            constraints.update(merged)
        return True

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
            if not _is_scalar_type(a):
                return False
            if isinstance(p.inner, VarType):
                # Atomic occurrences are validation evidence, not ordinary
                # unification evidence.  A non-atomic occurrence of the same
                # variable wins when present; when this is the only evidence,
                # the scalar actual still provides a useful fallback solution.
                add(p.inner.name, AtomicType(a))
                return True
            return rec(p.inner, a)
        if _is_optional(p):
            if isinstance(a, NoneTypeNode):
                # None does not constrain T in T?. Another argument or context
                # must provide T, otherwise the generic remains underconstrained.
                return True
            inner = _optional_inner(p)
            if (
                isinstance(a, NominalType)
                and a.name == SOME
                and len(a.args) == 1
            ):
                actual_inner = a.args[0]
            else:
                actual_inner = _optional_inner(a) if _is_optional(a) else a
            return (
                inner is not None
                and actual_inner is not None
                and rec(inner, actual_inner)
            )
        if isinstance(a, UnionType):
            # A non-union parameter can accept a union argument only when every
            # possible actual branch matches it. Gather generic evidence from
            # every branch so the solution conservatively covers the complete
            # runtime union.
            for branch in a.items:
                if not rec(p, branch):
                    return False
            return True
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
            if _contains_type_var(p.base):
                if not rec(p.base, actual_base):
                    return False
            elif not assignable(actual_base, p.base, ctx):
                return False
            if not p.fields:
                return True
            if not isinstance(a, RowType):
                return False
            actual_fields = {field.name: field.typ for field in a.fields}
            for field in p.fields:
                actual_field = actual_fields.get(field.name)
                if actual_field is None:
                    return False
                if _contains_type_var(field.typ):
                    if not rec(field.typ, actual_field):
                        return False
                elif not assignable(actual_field, field.typ, ctx):
                    return False
            return True
        if isinstance(p, TupleType) and isinstance(a, TupleType):
            return len(p.params) == len(a.params) and all(
                rec(x, y) for x, y in zip(p.params, a.params, strict=False)
            )
        if isinstance(p, VariadicTupleType) and isinstance(a, TupleType):
            return solve_variadic_tuple(p, a)
        if isinstance(p, FunctionType) and isinstance(a, FunctionType):
            if not rec_element_tags(p.element_tags, a.element_tags):
                return False
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
                elif not compatible(expected, actual, ctx):
                    return False
            substitution = _combined_substitution(constraints, ctx)
            if substitution is None:
                return False
            solved_params = tuple(
                _substitute(param, substitution) for param in p.params
            )
            actual_returns = _overload_result_for_args(
                Overload(a.params, a.returns),
                solved_params,
                ctx,
            )
            if actual_returns is None:
                return False
            return all(
                rec(expected, actual)
                for expected, actual in zip(p.returns, actual_returns, strict=False)
            )
        if isinstance(p, FunctionType) and isinstance(a, OverloadSetType):
            union_returns = _union_dispatched_callable_returns(a, p, ctx)
            if union_returns is not None:
                return all(
                    rec(expected, actual)
                    for expected, actual in zip(
                        p.returns,
                        union_returns,
                        strict=False,
                    )
                )
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
                return (
                    all(tag.absent for tag in p.tags)
                    and not p.exact
                    and rec(p.inner, a)
                ) or (
                    p.exact
                    and not any(not tag.absent for tag in p.tags)
                    and rec(p.inner, a)
                )
            if not _tag_requirements_met(a.tags, p.tags, exact=p.exact):
                return False
            return rec(p.inner, a.inner)
        if isinstance(a, TaggedType):
            return rec(p, a.inner)
        if isinstance(p, CollectionType) and isinstance(a, CollectionType):
            if isinstance(p.base, AtomicType):
                return _atomic_collection_shape_matches(p, a) and rec(
                    p.base,
                    a.base,
                )
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
        """Return the Boolean result of solve variadic tuple during type solving and overload resolution."""
        def save() -> dict[str, list[Type]]:
            """Save the current backtracking state for later restoration."""
            return {key: list(values) for key, values in constraints.items()}

        def restore(saved: dict[str, list[Type]]) -> None:
            """Restore the most recently saved backtracking state."""
            constraints.clear()
            constraints.update(saved)

        def rec_tuple(pattern_index: int, actual_index: int) -> bool:
            """Return the Boolean result of rec tuple during type solving and overload resolution."""
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


def _atomic_collection_shape_matches(
    pattern: CollectionType,
    actual: CollectionType,
) -> bool:
    """Return whether an atomic-base collection matches without rank peeling."""
    n, m = pattern.rank, actual.rank
    if not isinstance(n, int) or not isinstance(m, int):
        return type(pattern) is type(actual) and n == m
    pk, ak = type(pattern), type(actual)
    if pk is ListExactType:
        return m == n and ak in {ListExactType, ArrayExactType}
    if pk is ListMinType:
        return m >= n and ak in {
            ListExactType,
            ArrayExactType,
            ListMinType,
            ArrayMinType,
        }
    if pk is ListRuggedType:
        return m >= n and ak in {
            ListExactType,
            ArrayExactType,
            ListMinType,
            ArrayMinType,
            ListRuggedType,
        }
    if pk is ArrayExactType:
        return m == n and ak is ArrayExactType
    if pk is ArrayMinType:
        return m >= n and ak in {ArrayExactType, ArrayMinType}
    return False


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


def _type_join_key(typ: Type) -> tuple[int, str, str]:
    """Order equivalent join candidates by least-refined stable syntax."""
    rendered = show(typ)
    return (len(rendered), rendered, repr(typ))


def _combine(
    a: Type,
    b: Type,
    ctx: Context | None = None,
) -> Type | None:
    """Merge two candidate generic solutions into a shared solution."""
    a, b = normalize(a), normalize(b)
    if same(a, b):
        return a
    a_to_b = assignable(a, b, ctx)
    b_to_a = assignable(b, a, ctx)
    if a_to_b and b_to_a:
        return min((a, b), key=_type_join_key)
    if a_to_b:
        return b
    if b_to_a:
        return a
    if _is_optional(a) and _is_optional(b):
        ai, bi = _optional_inner(a), _optional_inner(b)
        if ai is None:
            return b
        if bi is None:
            return a
        inner = _combine(ai, bi, ctx)
        return optional(inner) if inner else None
    if isinstance(a, CollectionType) and isinstance(b, CollectionType):
        base = _combine(a.base, b.base, ctx)
        if base is not None:
            # Collection solutions can widen from exact to minimum/rugged when
            # multiple constraints need one shared generic type.
            return _combine_collections(
                type(a)(base, a.rank),
                type(b)(base, b.rank),
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


def _combine_all(
    values: Iterable[Type],
    ctx: Context | None = None,
) -> Type | None:
    """Merge generic evidence without depending on its collection order."""
    ctx = ctx or Context()
    vals = list(dict.fromkeys(normalize(value) for value in values))
    if not vals:
        return None

    # ``AtomicType`` values are solver-only evidence.  They must not narrow or
    # widen a solution supplied by an ordinary occurrence of the generic.  If
    # every occurrence is atomic, however, their scalar payloads are sufficient
    # to infer the generic rather than leaving the overload permanently
    # underconstrained (for example ``T atomic +``).
    ordinary = [value for value in vals if not isinstance(value, AtomicType)]
    if ordinary:
        vals = ordinary
    else:
        vals = [value.inner for value in vals if isinstance(value, AtomicType)]

    # Prefer an evidence type that already accepts every other observation.
    # This handles chains such as Integer -> Real -> Number and bridge types
    # such as None, Integer, Optional[Integer] without an order-sensitive fold.
    common = [
        candidate
        for candidate in vals
        if all(assignable(value, candidate, ctx) for value in vals)
    ]
    if common:
        common.sort(key=_type_join_key)
        for candidate in common:
            if not any(
                not same(other, candidate)
                and assignable(other, candidate, ctx)
                and not assignable(candidate, other, ctx)
                for other in common
            ):
                return candidate
        return common[0]

    # Some collection joins synthesize a rank-widened type that is not already
    # present in the evidence. A canonical ordering keeps that fold stable.
    vals.sort(key=_type_join_key)
    out = vals[0]
    for value in vals[1:]:
        out = _combine(out, value, ctx)
        if out is None:
            return None
    return out


def meet_required_inputs(
    requirements: Iterable[Type],
    ctx: Context | None = None,
) -> Type | None:
    """Return the narrowest inferred input satisfying every requirement.

    This is a meet over call/input requirements rather than a branch-result join.
    It preserves refinements such as absent data tags and rejects unrelated
    alternatives instead of widening them into a union. Generic requirements are
    specialized against the other observations before viable meets are selected.
    """
    ctx = ctx or Context()
    candidates = tuple(dict.fromkeys(normalize(item) for item in requirements))
    if not candidates:
        return None
    refined = list(candidates)
    for pattern in candidates:
        for actual in candidates:
            constraints = _solve(pattern, actual, ctx)
            if constraints is None:
                continue
            substitution = {
                name: _combine_all(values, ctx)
                for name, values in constraints.items()
            }
            if all(value is not None for value in substitution.values()):
                refined.append(_substitute(pattern, substitution))
    viable = tuple(
        candidate
        for candidate in dict.fromkeys(refined)
        if all(
            _required_input_accepts(candidate, required, ctx)
            for required in candidates
        )
    )
    if not viable:
        return None
    return min(viable, key=lambda typ: (-len(show(typ)), show(typ), repr(typ)))


def _required_input_accepts(
    candidate: Type,
    required: Type,
    ctx: Context,
) -> bool:
    """Return whether one concrete inferred input satisfies a requirement."""
    if assignable(candidate, required, ctx):
        return True
    constraints = _solve(required, candidate, ctx)
    if constraints is None:
        return False
    substitution = {
        name: _combine_all(values, ctx)
        for name, values in constraints.items()
    }
    if any(value is None for value in substitution.values()):
        return False
    return assignable(candidate, _substitute(required, substitution), ctx)


def _reduced_union(*types: Type, ctx: Context | None = None) -> Type:
    """Build a deterministic union with assignable subtype members removed."""
    ctx = ctx or Context()
    members: set[Type] = set()
    for typ in types:
        normalized = normalize(typ)
        if isinstance(normalized, UnionType):
            members.update(normalized.items)
        else:
            members.add(normalized)

    ordered = sorted(members, key=_type_join_key)
    kept: list[Type] = []
    for index, member in enumerate(ordered):
        redundant = False
        for other_index, other in enumerate(ordered):
            if index == other_index or same(member, other):
                continue
            if not assignable(member, other, ctx):
                continue
            if not assignable(other, member, ctx) or other_index < index:
                redundant = True
                break
        if not redundant:
            kept.append(member)
    return U(*kept)


def merge_types(a: Type, b: Type, ctx: Context | None = None) -> Type:
    """Merge branch result types as a commutative, associative type join."""
    ctx = ctx or Context()
    a, b = normalize(a), normalize(b)
    if same(a, b):
        return a
    if isinstance(a, NeverType):
        return b
    if isinstance(b, NeverType):
        return a
    if isinstance(a, NoneTypeNode):
        return b if _is_optional(b) else optional(_present_payload(b))
    if isinstance(b, NoneTypeNode):
        return a if _is_optional(a) else optional(_present_payload(a))

    a_optional = _optional_inner(a) if _is_optional(a) else None
    b_optional = _optional_inner(b) if _is_optional(b) else None
    if a_optional is not None and b_optional is not None:
        return optional(merge_types(a_optional, b_optional, ctx))
    if a_optional is not None:
        return optional(merge_types(a_optional, _present_payload(b), ctx))
    if b_optional is not None:
        return optional(merge_types(_present_payload(a), b_optional, ctx))

    a_to_b = assignable(a, b, ctx)
    b_to_a = assignable(b, a, ctx)
    if a_to_b and b_to_a:
        return min((a, b), key=_type_join_key)
    if a_to_b:
        return b
    if b_to_a:
        return a
    return _reduced_union(a, b, ctx=ctx)


def _present_payload(typ: Type) -> Type:
    """Return the payload represented by an explicit ``Some[T]`` value."""
    typ = normalize(typ)
    if isinstance(typ, NominalType) and typ.name == SOME and len(typ.args) == 1:
        return typ.args[0]
    return typ


def merge_stacks(
    a: TypeStack,
    b: TypeStack,
    ctx: Context | None = None,
) -> TypeStack:
    """Merge two branch stacks pairwise, padding shorter stacks with ``None``."""
    # Padding on the left treats missing values as absent lower stack outputs.
    # For branch result stacks this gives the same optional-padding behaviour
    # as the language design.
    length = max(len(a), len(b))
    left = (NoneType(),) * (length - len(a)) + a.items
    right = (NoneType(),) * (length - len(b)) + b.items
    return TypeStack(
        tuple(merge_types(x, y, ctx) for x, y in zip(left, right, strict=False))
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
        bound_names = {generic.text for generic in t.generics}
        local_subst = {
            name: typ for name, typ in subst.items() if name not in bound_names
        }
        return AnonymousTraitType(
            t.generics,
            tuple(
                AnonymousTraitRequirement(
                    requirement.name,
                    _substitute_overload(requirement.overload, local_subst),
                )
                for requirement in t.requirements
            ),
        )
    if isinstance(t, TaggedType):
        return Tagged(_substitute(t.inner, subst), *t.tags, exact=t.exact)
    if isinstance(t, ExactType):
        return ExactType(_substitute(t.inner, subst))
    if isinstance(t, AtomicType):
        return AtomicType(_substitute(t.inner, subst))
    return t


def _substitute_overload(overload: Overload, subst: dict[str, Type]) -> Overload:
    """Substitute free overload variables without capturing local generics."""
    local_names = {constraint.name for constraint in overload.generic_constraints}
    local_subst = {
        name: typ for name, typ in subst.items() if name not in local_names
    }
    constraints = tuple(
        GenericConstraint(
            constraint.name,
            _substitute(constraint.bound, local_subst),
            constraint.variance,
        )
        for constraint in overload.generic_constraints
    )
    return Overload(
        tuple(_substitute(param, local_subst) for param in overload.params),
        tuple(_substitute(ret, local_subst) for ret in overload.returns),
        constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        frozenset(_substitute_element_tags(overload.element_tags, local_subst)),
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
        overload.is_multi,
        overload.runtime_static_values,
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


def _is_scalar_type(t: Type) -> bool:
    """Return whether every value represented by ``t`` has collection rank zero."""
    t = normalize(t)
    if isinstance(t, (TaggedType, ExactType, AtomicType)):
        return _is_scalar_type(t.inner)
    if isinstance(t, CollectionType):
        return False
    if isinstance(t, UnionType):
        return all(_is_scalar_type(item) for item in t.items)
    if isinstance(t, IntersectionType):
        return any(_is_scalar_type(item) for item in t.items)
    if isinstance(t, RowType):
        return _is_scalar_type(t.base)
    if isinstance(t, AnonymousTraitType):
        return False
    return True


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
        subst = {k: _combine_all(v, ctx) for k, v in constraints.items()}
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
            ctx,
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
        union_returns = _union_dispatched_callable_returns(argument, parameter, ctx)
        if union_returns is not None:
            return len(union_returns) == len(parameter.returns) and all(
                compatible(actual, expected, ctx)
                for actual, expected in zip(
                    union_returns,
                    parameter.returns,
                    strict=False,
                )
            )
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


def union_dispatched_callable_plan(
    argument: OverloadSetType,
    expected: FunctionType,
    ctx: Context | None = None,
) -> UnionDispatchPlan | None:
    """Build deterministic runtime dispatch for a union-accepting callable.

    Every cartesian input branch is resolved statically. Runtime dispatch only
    identifies the branch and invokes that already-selected overload; it never
    repeats overload resolution from the concrete runtime value.
    """
    ctx = ctx or Context()
    if expected.params is None or expected.returns is None:
        return None
    if not any(isinstance(normalize(param), UnionType) for param in expected.params):
        return None
    if any(_contains_type_var(param) for param in expected.params):
        return None

    alternatives = tuple(_union_input_alternatives(param) for param in expected.params)
    branches: list[UnionDispatchBranch] = []
    branch_results: list[tuple[Type, ...]] = []
    for branch_args in product(*alternatives):
        applied = _resolve_applied_overload(argument.overloads, branch_args, ctx)
        if applied is None:
            return None
        if not _element_tag_requirements_met(
            applied.overload.element_tags,
            expected.element_tags,
            ctx,
        ):
            return None
        if len(applied.actual_returns) != len(expected.returns):
            return None
        patterns = tuple(_runtime_dispatch_pattern(arg, ctx) for arg in branch_args)
        if any(pattern is None for pattern in patterns):
            return None
        branches.append(
            UnionDispatchBranch(
                tuple(pattern for pattern in patterns if pattern is not None),
                argument.overloads.index(applied.overload),
            )
        )
        branch_results.append(applied.actual_returns)

    if not branch_results or _dispatch_plan_has_ambiguous_overlap(tuple(branches), ctx):
        return None
    returns = tuple(
        U(*(result[index] for result in branch_results))
        for index in range(len(expected.returns))
    )
    return UnionDispatchPlan(tuple(branches), returns)


def _union_dispatched_callable_returns(
    argument: OverloadSetType,
    expected: FunctionType,
    ctx: Context,
) -> tuple[Type, ...] | None:
    """Determine the return types for union dispatched callable during type and overload solving."""
    plan = union_dispatched_callable_plan(argument, expected, ctx)
    return None if plan is None else plan.returns


def _union_input_alternatives(typ: Type) -> tuple[Type, ...]:
    """Compute union input alternatives during type and overload solving."""
    typ = normalize(typ)
    if isinstance(typ, UnionType):
        return tuple(sorted(typ.items, key=show))
    return (typ,)


def _runtime_dispatch_pattern(
    typ: Type,
    ctx: Context,
) -> RuntimeTypePattern | None:
    """Compute runtime dispatch pattern during type and overload solving."""
    typ = normalize(typ)
    if isinstance(typ, (ExactType, AtomicType)):
        return _runtime_dispatch_pattern(typ.inner, ctx)
    if isinstance(typ, TaggedType):
        inner = _runtime_dispatch_pattern(typ.inner, ctx)
        if inner is None:
            return None
        return RuntimeTypePattern(
            "tagged",
            children=(inner,),
            tags=tuple(sorted(typ.tags)),
        )
    if isinstance(typ, NominalType):
        children = tuple(_runtime_dispatch_pattern(arg, ctx) for arg in typ.args)
        if any(child is None for child in children):
            return None
        accepted = _runtime_nominal_subtypes(typ, ctx)
        return RuntimeTypePattern(
            "nominal",
            name=str(typ.name),
            children=tuple(child for child in children if child is not None),
            accepted_names=tuple(sorted(accepted)),
            variances=ctx.variance_for(typ.name, len(typ.args)),
        )
    if isinstance(typ, UnionType):
        children = tuple(
            _runtime_dispatch_pattern(item, ctx)
            for item in sorted(typ.items, key=show)
        )
        if any(child is None for child in children):
            return None
        return RuntimeTypePattern(
            "union",
            children=tuple(child for child in children if child is not None),
        )
    if isinstance(typ, TupleType):
        children = tuple(_runtime_dispatch_pattern(item, ctx) for item in typ.params)
        if any(child is None for child in children):
            return None
        return RuntimeTypePattern(
            "tuple",
            children=tuple(child for child in children if child is not None),
        )
    if isinstance(typ, CollectionType):
        # Collection element types are not reified on runtime values. Inspecting
        # elements here would also consume lazy or infinite collections, so a
        # collection cannot currently be used as a union-dispatch guard.
        return None
    if isinstance(typ, NoneTypeNode):
        return RuntimeTypePattern("none")
    return None


def _runtime_nominal_subtypes(typ: NominalType, ctx: Context) -> set[str]:
    """Compute runtime nominal subtypes during type and overload solving."""
    names = {str(typ.name)}
    candidates = set(ctx.trait_impls) | set(ctx.variant_members)
    candidates.update({INTEGER, REAL, NUMBER})
    for candidate in candidates:
        if subtype(N(candidate), typ, ctx):
            names.add(str(candidate))
    return names


def _dispatch_plan_has_ambiguous_overlap(
    branches: tuple[UnionDispatchBranch, ...],
    ctx: Context,
) -> bool:
    """Return the Boolean result of dispatch plan has ambiguous overlap during type solving and overload resolution."""
    for index, left in enumerate(branches):
        for right in branches[index + 1 :]:
            if left.overload_index == right.overload_index:
                continue
            if all(
                _runtime_patterns_overlap(a, b, ctx)
                for a, b in zip(left.params, right.params, strict=True)
            ):
                return True
    return False


def _runtime_patterns_overlap(
    left: RuntimeTypePattern,
    right: RuntimeTypePattern,
    ctx: Context,
) -> bool:
    """Return the Boolean result of runtime patterns overlap during type solving and overload resolution."""
    left_inner, left_tags = _runtime_pattern_tags(left)
    right_inner, right_tags = _runtime_pattern_tags(right)
    if not _runtime_tag_requirements_overlap(left_tags, right_tags, ctx):
        return False
    left = left_inner
    right = right_inner
    if left.kind == "union":
        return any(
            _runtime_patterns_overlap(item, right, ctx) for item in left.children
        )
    if right.kind == "union":
        return any(
            _runtime_patterns_overlap(left, item, ctx) for item in right.children
        )
    if left.kind != right.kind:
        return False
    if left.kind == "nominal":
        if not set(left.accepted_names).intersection(right.accepted_names):
            return False
        if left.name == right.name and left.children and right.children:
            return all(
                _runtime_patterns_overlap(a, b, ctx)
                for a, b in zip(left.children, right.children, strict=True)
            )
        return True
    if left.kind == "tuple":
        return len(left.children) == len(right.children) and all(
            _runtime_patterns_overlap(a, b, ctx)
            for a, b in zip(left.children, right.children, strict=True)
        )
    if left.kind == "collection":
        return True
    return left.kind == "none"


def _runtime_pattern_tags(
    pattern: RuntimeTypePattern,
) -> tuple[RuntimeTypePattern, tuple[DataTag, ...]]:
    """Compute runtime pattern tags during type and overload solving."""
    if pattern.kind != "tagged":
        return pattern, ()
    return pattern.children[0], pattern.tags


def _runtime_tag_requirements_overlap(
    left: tuple[DataTag, ...],
    right: tuple[DataTag, ...],
    ctx: Context,
) -> bool:
    """Return the Boolean result of runtime tag requirements overlap during type solving and overload resolution."""
    left_present = {tag for tag in left if not tag.absent}
    right_present = {tag for tag in right if not tag.absent}
    left_absent = {(tag.name, tag.depth) for tag in left if tag.absent}
    right_absent = {(tag.name, tag.depth) for tag in right if tag.absent}
    if any((tag.name, tag.depth) in right_absent for tag in left_present):
        return False
    if any((tag.name, tag.depth) in left_absent for tag in right_present):
        return False
    return not any(
        other.name in {str(name) for name in ctx.tag_disjoints(tag.name)}
        for tag in left_present
        for other in right_present
        if tag.depth == other.depth
    )


def _overload_callable_compatible(
    overload: Overload, expected: Type, ctx: Context
) -> bool:
    """Return whether one overload can be used as an expected function type."""
    if not _element_tag_requirements_met(
        overload.element_tags,
        expected.element_tags,
        ctx,
    ):
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
    """Compute vectorisation excess during type and overload solving."""
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


def _dynamic_vectorisation_target_rank(
    argument: Type,
    parameter: Type,
    ctx: Context,
) -> int | None:
    """Return the exact parameter rank reached dynamically from a minimum rank."""
    argument_collection = _collection_view(argument)
    parameter_collection = _collection_view(parameter)
    if argument_collection is None or parameter_collection is None:
        return None
    if not isinstance(argument_collection, (ListMinType, ArrayMinType)):
        return None
    if not isinstance(parameter_collection, (ListExactType, ArrayExactType)):
        return None
    if not isinstance(argument_collection.rank, int) or not isinstance(
        parameter_collection.rank,
        int,
    ):
        return None
    if argument_collection.rank < parameter_collection.rank:
        return None
    if isinstance(parameter_collection, ArrayExactType) and not isinstance(
        argument_collection,
        ArrayMinType,
    ):
        return None
    if not compatible(argument_collection.base, parameter_collection.base, ctx):
        return None
    if assignable(argument, parameter, ctx):
        return None
    return parameter_collection.rank


def _wrap_returns_for_vector_depth(
    returns: tuple[Type, ...],
    args: tuple[Type, ...],
    depths: tuple[int, ...],
) -> tuple[Type, ...]:
    """Compute wrap returns for vector depth during type and overload solving."""
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


def _wrap_returns_for_vectorisation(
    returns: tuple[Type, ...],
    args: tuple[Type, ...],
    depths: tuple[int, ...],
    target_ranks: tuple[int | None, ...],
) -> tuple[Type, ...]:
    """Apply fixed or runtime-selected vector depth to an overload result stack."""
    minimum_rank = max(depths, default=0)
    dynamic = any(rank is not None for rank in target_ranks)
    if not dynamic:
        return _wrap_returns_for_vector_depth(returns, args, depths)

    vector_args = tuple(
        arg
        for arg, depth, target in zip(
            args,
            depths,
            target_ranks,
            strict=False,
        )
        if depth > 0 or target is not None
    )
    array_only = bool(vector_args) and all(
        isinstance(_collection_view(arg), (ArrayExactType, ArrayMinType))
        for arg in vector_args
    )
    output_type = ArrayMinType if array_only else ListMinType
    if minimum_rank > 0:
        return tuple(C(output_type, ret, minimum_rank) for ret in returns)
    return tuple(U(ret, C(output_type, ret, 1)) for ret in returns)


def _can_vectorise(argument: Type, parameter: Type, ctx: Context) -> bool:
    """Return whether compatibility can be achieved through vectorisation."""
    if isinstance(parameter, ExactType):
        return False
    argument_collection = _collection_view(argument)
    parameter_collection = _collection_view(parameter)
    if argument_collection is None:
        return False
    if _dynamic_vectorisation_target_rank(argument, parameter, ctx) is not None:
        return True
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
    argument: Type,
    parameter: Type,
    ctx: Context | None = None,
    *,
    declared_parameter: Type | None = None,
) -> Specificity:
    """Classify how specifically an argument matches a parameter."""
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if same(argument, parameter):
        if declared_parameter is not None and _contains_type_var(
            declared_parameter
        ):
            return Specificity.EXACT_GENERIC
        return Specificity.EXACT
    # The order here mirrors the language's specificity ladder. The first
    # applicable category wins.
    if isinstance(parameter, TaggedType) and isinstance(argument, TaggedType):
        if _tag_requirements_met(
            argument.tags,
            parameter.tags,
            exact=parameter.exact,
        ) and same(
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
    if _is_result_injection(argument, parameter, ctx):
        # Result accepts bare success/error values through an implicit sum-type
        # injection. Rank that like a union arm, below a direct generic match.
        return Specificity.UNION
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
    vectorised_target_ranks: tuple[int | None, ...] = ()
    if disambiguation:
        depths: list[int] = []
        target_ranks: list[int | None] = []
        adapted_args: list[Type] = []
        for arg, hint in zip(args, disambiguation, strict=True):
            if hint is None:
                depths.append(0)
                target_ranks.append(None)
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
            target_ranks.append(_dynamic_vectorisation_target_rank(arg, hint, ctx))
            adapted_args.append(hint)
        base_args = tuple(adapted_args)
        vectorised_depths = tuple(depths)
        vectorised_target_ranks = tuple(target_ranks)

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
        combined = _combine_all(values, ctx)
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
        combined = _combine_all(values, ctx)
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

    scores = tuple(
        _match_specificity(
            arg,
            param,
            ctx,
            declared_parameter=declared,
        )
        for arg, param, declared in zip(
            base_args,
            params,
            overload.params,
            strict=False,
        )
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
        automatic_target_ranks: list[int | None] = []
        for arg, param, score in zip(base_args, params, scores, strict=False):
            if score != Specificity.VECTORISED:
                automatic_depths.append(0)
                automatic_target_ranks.append(None)
                continue
            excess = _vectorisation_excess(arg, param, ctx)
            target_rank = _dynamic_vectorisation_target_rank(arg, param, ctx)
            if excess is None or (excess <= 0 and target_rank is None):
                return OverloadAttempt(
                    None,
                    OverloadMismatch(OverloadMismatchReason.VECTORISATION),
                )
            automatic_depths.append(excess)
            automatic_target_ranks.append(target_rank)
        if any(depth > 0 for depth in automatic_depths) or any(
            rank is not None for rank in automatic_target_ranks
        ):
            vectorised_depths = tuple(automatic_depths)
            vectorised_target_ranks = tuple(automatic_target_ranks)

    base_returns = returns
    if disambiguation:
        inferred_returns = _overload_result_for_args(
            Overload(params, returns),
            base_args,
            ctx,
        )
        if inferred_returns is None:
            return OverloadAttempt(
                None,
                OverloadMismatch(OverloadMismatchReason.RESULT),
            )
        base_returns = inferred_returns
    actual_returns = _wrap_returns_for_vectorisation(
        base_returns,
        args,
        vectorised_depths,
        vectorised_target_ranks,
    )
    # returns = declared returns after generic substitution.
    # actual_returns = returns after call adaptation such as vectorisation.

    return OverloadAttempt(
        AppliedOverload(
            overload,
            substitution,
            params,
            returns,
            actual_returns,
            scores,
            any(score == Specificity.VECTORISED for score in scores)
            or any(depth > 0 for depth in vectorised_depths)
            or any(rank is not None for rank in vectorised_target_ranks),
            vectorised_depths,
            element_tags=frozenset(
                _substitute_element_tags(overload.element_tags, substitution)
            ),
            vectorised_target_ranks=vectorised_target_ranks,
            runtime_static_values=overload.runtime_static_values,
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
    applied = _resolve_applied_overload(overloads, args, ctx)
    if applied is None:
        return None
    return ResolvedOverload(
        applied.overload,
        applied.substitution,
        applied.params,
        applied.returns,
        applied.scores,
    )


def _resolve_applied_overload(
    overloads: Iterable[Overload],
    args: tuple[Type, ...],
    ctx: Context,
) -> AppliedOverload | None:
    """Resolve applied overload during type and overload solving."""
    candidates = tuple(
        applied
        for overload in overloads
        if (applied := apply_overload(overload, args, ctx)) is not None
    )
    winners = tuple(
        candidate
        for candidate in candidates
        if not any(
            _overload_match_dominates(
                other.scores,
                other.params,
                candidate.scores,
                candidate.params,
                ctx,
            )
            for other in candidates
            if other is not candidate
        )
    )
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
    """Return the Boolean result of application dominates during type solving and overload resolution."""
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
    """Return the Boolean result of overload match dominates during type solving and overload resolution."""
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
    """Return the Boolean result of params more specific during type solving and overload resolution."""
    return all(
        _type_more_specific_or_same(left_item, right_item, ctx)
        for left_item, right_item in zip(left, right, strict=False)
    ) and any(
        not _type_more_specific_or_same(right_item, left_item, ctx)
        for left_item, right_item in zip(left, right, strict=False)
    )


def _type_more_specific_or_same(left: Type, right: Type, ctx: Context) -> bool:
    """Return the Boolean result of type more specific or same during type solving and overload resolution."""
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
    *,
    exact: bool = False,
) -> bool:
    """Return whether an actual tag set satisfies required/present tags."""
    for tag in required:
        positive = DataTag(tag.name, tag.depth)
        if tag.absent:
            if positive in actual:
                return False
        elif positive not in actual:
            return False
    if exact:
        actual_present = {tag for tag in actual if not tag.absent}
        required_present = {
            DataTag(tag.name, tag.depth)
            for tag in required
            if not tag.absent
        }
        if actual_present != required_present:
            return False
    return True


def _unit_tags_preserved(
    actual: frozenset[DataTag],
    required: frozenset[DataTag],
    ctx: Context,
) -> bool:
    """Return whether a tagged target preserves every actual unit dimension."""
    required_positive = {
        DataTag(tag.name, tag.depth)
        for tag in required
        if not tag.absent
    }
    return all(
        tag in required_positive
        for tag in actual
        if not tag.absent and ctx.is_unit_tag(tag.name)
    )


def _element_tag_requirements_met(
    actual: frozenset[ElementTag],
    required: frozenset[ElementTag],
    ctx: Context,
) -> bool:
    """Return the Boolean result of element tag requirements met during type solving and overload resolution."""
    present = tuple(tag for tag in actual if not tag.absent)
    for tag in required:
        matches = tuple(
            candidate
            for candidate in present
            if (
                _element_tag_conflicts(candidate, tag, ctx)
                if tag.absent
                else _element_tag_matches(candidate, tag, ctx)
            )
        )
        if tag.absent:
            if matches:
                return False
        elif not matches:
            return False
    return True


def _element_tag_matches(
    actual: ElementTag,
    required: ElementTag,
    ctx: Context,
) -> bool:
    """Return the Boolean result of element tag matches during type solving and overload resolution."""
    if actual.name != required.name:
        return False
    if not required.args:
        return True
    if len(actual.args) != len(required.args):
        return False
    return all(
        assignable(actual_arg, required_arg, ctx)
        for actual_arg, required_arg in zip(
            actual.args,
            required.args,
            strict=True,
        )
    )


def _element_tag_conflicts(
    actual: ElementTag,
    forbidden: ElementTag,
    ctx: Context,
) -> bool:
    """Return whether an actual effect may overlap a forbidden effect."""
    if actual.name != forbidden.name:
        return False
    if not forbidden.args:
        return True
    if len(actual.args) != len(forbidden.args):
        return False
    return all(
        _types_may_overlap(actual_arg, forbidden_arg, ctx)
        for actual_arg, forbidden_arg in zip(
            actual.args,
            forbidden.args,
            strict=True,
        )
    )


def _types_may_overlap(left: Type, right: Type, ctx: Context) -> bool:
    """Return the Boolean result of types may overlap during type solving and overload resolution."""
    left = normalize(left)
    right = normalize(right)
    if isinstance(left, UnionType):
        return any(_types_may_overlap(item, right, ctx) for item in left.items)
    if isinstance(right, UnionType):
        return any(_types_may_overlap(left, item, ctx) for item in right.items)
    return assignable(left, right, ctx) or assignable(right, left, ctx)


def _substitute_element_tags(
    tags: frozenset[ElementTag],
    subst: dict[str, Type],
) -> tuple[ElementTag, ...]:
    """Substitute element tags during type and overload solving."""
    return tuple(
        ElementTag(
            tag.name,
            tuple(_substitute(arg, subst) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _element_tags_contain_type_var(tags: frozenset[ElementTag]) -> bool:
    """Return the Boolean result of element tags contain type var during type solving and overload resolution."""
    return any(_contains_type_var(arg) for tag in tags for arg in tag.args)


def _element_tags_contain_named_type_var(
    tags: frozenset[ElementTag],
    name: str,
) -> bool:
    """Return the Boolean result of element tags contain named type var during type solving and overload resolution."""
    return any(
        _contains_named_type_var(arg, name) for tag in tags for arg in tag.args
    )


def _has_unit_tag(tags: frozenset[DataTag], ctx: Context) -> bool:
    """Return whether the relations helper has unit tag."""
    return any(not tag.absent and ctx.is_unit_tag(tag.name) for tag in tags)
