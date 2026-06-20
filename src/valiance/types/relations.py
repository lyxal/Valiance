from __future__ import annotations

"""Type relations, generic solving, overload resolution, and stack merging."""

from typing import Callable, Iterable

from valiance.types.builders import (
    Atomic,
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
from valiance.types.model import (
    AppliedOverload,
    Coll,
    Context,
    Kind,
    Overload,
    ResolvedOverload,
    Specificity,
    StackApplication,
    Type,
)


def subtype(source: Type, target: Type, ctx: Context | None = None) -> bool:
    """Return whether ``source`` can be treated as ``target`` by subsumption."""
    ctx = ctx or Context()
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or source.kind == Kind.NEVER:
        return True

    if source.kind == Kind.TAGGED:
        if not (source.tags & ctx.unit_tags) and subtype(source.inner, target, ctx):
            return True

    if target.kind == Kind.TAGGED:
        # Positive tag requirements must be present; absent tag requirements
        # are encoded as strings beginning with '!'.
        actual_tags = source.tags if source.kind == Kind.TAGGED else frozenset()
        inner = source.inner if source.kind == Kind.TAGGED else source
        for tag in target.tags:
            if tag.startswith("!"):
                if tag[1:] in actual_tags:
                    return False
            elif tag not in actual_tags:
                return False
        return subtype(inner, target.inner, ctx)

    if source.kind == Kind.UNION:
        return all(subtype(s, target, ctx) for s in source.items)

    if target.kind == Kind.UNION:
        return any(subtype(source, t, ctx) for t in target.items)

    if target.kind == Kind.INTERSECTION:
        return all(subtype(source, t, ctx) for t in target.items)

    if source.kind == Kind.INTERSECTION:
        return any(subtype(s, target, ctx) for s in source.items)

    if source.kind == Kind.NOMINAL and target.kind == Kind.NOMINAL:
        # Generic nominal types are invariant. Trait/variant relationships are
        # the only nominal widening currently supported.
        if source.name == target.name and len(source.args) == len(target.args):
            return all(same(a, b) for a, b in zip(source.args, target.args))
        if ctx.implements(source.name, target.name):
            return True
        if ctx.variant_members.get(source.name) == target.name:
            return True
        if source.name in {"Integer", "Real"} and target.name == "Number":
            return True

    if source.kind == Kind.TUPLE and target.kind == Kind.TUPLE:
        return len(source.params) == len(target.params) and all(
            assignable(a, b, ctx) for a, b in zip(source.params, target.params)
        )

    if source.kind == Kind.COLLECTION and target.kind == Kind.COLLECTION:
        return _collection_subtype(source, target, ctx)

    return False


def _collection_subtype(source: Type, target: Type, ctx: Context) -> bool:
    """Return whether one collection type subsumes another by rank rules."""
    if source.coll_kind in {Coll.LIST_MIN, Coll.LIST_RUGGED} and target.coll_kind == Coll.LIST_EXACT:
        # A minimum/rugged list can satisfy an exact outer-list pattern if the
        # peeled remainder is exactly the expected element type.
        if source.rank >= target.rank:
            remainder = _collection_remainder(source.coll_kind, source.base, source.rank - target.rank)
            return same(remainder, target.base)
    if not same(source.base, target.base):
        return False
    sk, tk = source.coll_kind, target.coll_kind
    sr, tr = source.rank, target.rank

    if sk == tk and sr == tr:
        return True
    if sk == Coll.ARRAY_EXACT and tk == Coll.LIST_EXACT and sr == tr:
        return True
    if sk == Coll.ARRAY_MIN and tk == Coll.LIST_MIN and sr == tr:
        return True
    if sk in {Coll.LIST_EXACT, Coll.ARRAY_EXACT} and tk in {Coll.LIST_MIN, Coll.ARRAY_MIN}:
        return sr >= tr and ((sk.startswith("array")) == (tk.startswith("array")))
    if tk == Coll.LIST_RUGGED and sk in {
        Coll.LIST_EXACT,
        Coll.LIST_MIN,
        Coll.ARRAY_EXACT,
        Coll.ARRAY_MIN,
    }:
        return sr >= tr
    return False


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
        return source.kind == Kind.NONE or (inner is not None and assignable(source, inner, ctx))

    if source.kind == Kind.UNION:
        return all(assignable(s, target, ctx) for s in source.items)
    if target.kind == Kind.UNION:
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
        if p.kind == Kind.VAR:
            # Solving does not decide whether this is globally valid; it only
            # records what this one parameter says the generic must be.
            add(p.name, a)
            return True
        if same(p, a):
            return True
        if p.kind == Kind.ATOMIC:
            return True
        if _is_optional(p):
            if a.kind == Kind.NONE:
                # None does not constrain T in T?. Another argument or context
                # must provide T, otherwise the generic remains underconstrained.
                return True
            inner = _optional_inner(p)
            actual_inner = _optional_inner(a) if _is_optional(a) else a
            return inner is not None and actual_inner is not None and rec(inner, actual_inner)
        if p.kind == Kind.UNION:
            # Ordinary union solving is allowed only when exactly one branch
            # matches. Ambiguous generic unions should require explicit types.
            matches: list[dict[str, list[Type]]] = []
            for branch in p.items:
                saved = {key: list(values) for key, values in constraints.items()}
                if rec(branch, a):
                    matches.append({key: list(values) for key, values in constraints.items()})
                constraints.clear()
                constraints.update(saved)
            if len(matches) != 1:
                return False
            constraints.clear()
            constraints.update(matches[0])
            return True
        if p.kind == Kind.NOMINAL and a.kind == Kind.NOMINAL:
            return p.name == a.name and len(p.args) == len(a.args) and all(
                rec(x, y) for x, y in zip(p.args, a.args)
            )
        if p.kind == Kind.TUPLE and a.kind == Kind.TUPLE:
            return len(p.params) == len(a.params) and all(rec(x, y) for x, y in zip(p.params, a.params))
        if p.kind == Kind.FUNCTION and a.kind == Kind.FUNCTION:
            return len(p.params) == len(a.params) and len(p.returns) == len(a.returns) and all(
                rec(x, y) for x, y in zip(p.params + p.returns, a.params + a.returns)
            )
        if p.kind == Kind.FUNCTION and a.kind == Kind.OVERLOAD_SET:
            # Overloaded callables are checked after other generics are known.
            # Solving through every overload here would guess too early.
            return True
        if p.kind == Kind.TAGGED:
            if a.kind != Kind.TAGGED:
                return all(tag.startswith("!") for tag in p.tags) and rec(p.inner, a)
            if not _tag_requirements_met(a.tags, p.tags):
                return False
            return rec(p.inner, a.inner)
        if p.kind == Kind.COLLECTION and a.kind == Kind.COLLECTION:
            return _solve_collection(p, a, add)
        return False

    return constraints if rec(pattern, actual) else None


def _solve_collection(pattern: Type, actual: Type, add: Callable[[str, Type], None]) -> bool:
    """Solve a generic collection pattern against an actual collection type."""
    if pattern.base.kind != Kind.VAR:
        # This helper is only for patterns like T+. Non-generic collection
        # patterns are handled by normal compatibility.
        return same(pattern, actual)
    n, m = pattern.rank, actual.rank
    if m < n:
        return False
    diff = m - n
    pk, ak = pattern.coll_kind, actual.coll_kind
    base = actual.base

    def bind_as(kind: str) -> bool:
        """Bind the pattern base variable to the peeled actual collection type."""
        add(pattern.base.name, _collection_remainder(kind, base, diff))
        return True

    if pk == Coll.LIST_EXACT and ak in {Coll.LIST_EXACT, Coll.ARRAY_EXACT}:
        return bind_as(Coll.LIST_EXACT)
    if pk == Coll.LIST_EXACT and ak in {Coll.LIST_MIN, Coll.LIST_RUGGED}:
        return bind_as(ak)
    if pk == Coll.LIST_MIN:
        if ak in {Coll.LIST_EXACT, Coll.ARRAY_EXACT}:
            return bind_as(Coll.LIST_EXACT)
        if ak == Coll.LIST_MIN or ak == Coll.ARRAY_MIN:
            return bind_as(Coll.LIST_MIN)
    if pk == Coll.LIST_RUGGED:
        if ak in {Coll.LIST_EXACT, Coll.ARRAY_EXACT}:
            return bind_as(Coll.LIST_EXACT)
        if ak == Coll.LIST_MIN or ak == Coll.ARRAY_MIN:
            return bind_as(Coll.LIST_RUGGED)
        if ak == Coll.LIST_RUGGED:
            return bind_as(Coll.LIST_RUGGED)
    if pk == Coll.ARRAY_EXACT and ak == Coll.ARRAY_EXACT:
        return bind_as(Coll.ARRAY_EXACT)
    if pk == Coll.ARRAY_MIN:
        if ak == Coll.ARRAY_EXACT:
            return bind_as(Coll.ARRAY_EXACT)
        if ak == Coll.ARRAY_MIN:
            return bind_as(Coll.ARRAY_MIN)
    return False


def _collection_remainder(kind: str, base: Type, rank: int) -> Type:
    """Return the element type left after peeling collection rank from a type."""
    if rank > 0:
        return C(kind, base, rank)
    # Peeling all known rank from a minimum/rugged type leaves an element that
    # may be atomic or may still contain more nesting at runtime.
    if kind in {Coll.LIST_MIN, Coll.ARRAY_MIN}:
        return U(base, C(kind, base, 1))
    if kind == Coll.LIST_RUGGED:
        return U(base, C(Coll.LIST_RUGGED, base, 1))
    return base


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
    if a.kind == Kind.COLLECTION and b.kind == Kind.COLLECTION and same(a.base, b.base):
        # Collection solutions can widen from exact to minimum/rugged when
        # multiple constraints need one shared generic type.
        return _combine_collections(a, b)
    return None


def _combine_collections(a: Type, b: Type) -> Type | None:
    """Merge two collection solutions using conservative rank widening."""
    ak, bk = a.coll_kind, b.coll_kind
    ar, br = a.rank, b.rank
    base = a.base
    if ak == bk == Coll.LIST_EXACT:
        return a if ar == br else None
    if ak == bk == Coll.ARRAY_EXACT:
        return a if ar == br else None
    if ak == bk == Coll.LIST_MIN:
        return C(Coll.LIST_MIN, base, min(ar, br))
    if ak == bk == Coll.ARRAY_MIN:
        return C(Coll.ARRAY_MIN, base, min(ar, br))
    if {ak, bk} <= {Coll.LIST_EXACT, Coll.LIST_MIN}:
        return C(Coll.LIST_MIN, base, min(ar, br))
    if {ak, bk} <= {Coll.ARRAY_EXACT, Coll.ARRAY_MIN}:
        return C(Coll.ARRAY_MIN, base, min(ar, br))
    if Coll.LIST_RUGGED in {ak, bk}:
        return C(Coll.LIST_RUGGED, base, min(ar, br))
    if {ak, bk} == {Coll.LIST_EXACT, Coll.ARRAY_EXACT} and ar == br:
        return C(Coll.LIST_EXACT, base, ar)
    if {ak, bk} == {Coll.LIST_MIN, Coll.ARRAY_MIN}:
        return C(Coll.LIST_MIN, base, min(ar, br))
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
    if normalize(a).kind == Kind.NONE:
        return optional(b)
    if normalize(b).kind == Kind.NONE:
        return optional(a)
    return U(a, b)


def merge_stacks(a: tuple[Type, ...], b: tuple[Type, ...]) -> tuple[Type, ...]:
    """Merge two branch stacks pairwise, padding shorter stacks with ``None``."""
    # Padding on the left treats missing values as absent lower stack outputs.
    # For branch result stacks this gives the same optional-padding behaviour
    # as the language design.
    length = max(len(a), len(b))
    left = (NoneType(),) * (length - len(a)) + a
    right = (NoneType(),) * (length - len(b)) + b
    return tuple(merge_types(x, y) for x, y in zip(left, right))


def _substitute(t: Type, subst: dict[str, Type]) -> Type:
    """Replace type variables in ``t`` using a solved substitution map."""
    t = normalize(t)
    if t.kind == Kind.VAR:
        return subst.get(t.name, t)
    if t.kind == Kind.NOMINAL:
        return N(t.name, *(_substitute(a, subst) for a in t.args))
    if t.kind == Kind.UNION:
        return U(*(_substitute(i, subst) for i in t.items))
    if t.kind == Kind.INTERSECTION:
        return I(*(_substitute(i, subst) for i in t.items))
    if t.kind == Kind.TUPLE:
        return Tup(*(_substitute(p, subst) for p in t.params))
    if t.kind == Kind.COLLECTION:
        return normalize(C(t.coll_kind, _substitute(t.base, subst), t.rank))
    if t.kind == Kind.FUNCTION:
        return Fn((_substitute(p, subst) for p in t.params), (_substitute(r, subst) for r in t.returns))
    if t.kind == Kind.TAGGED:
        return Tagged(_substitute(t.inner, subst), *t.tags)
    if t.kind == Kind.ATOMIC and t.inner.kind == Kind.VAR:
        solved = subst.get(t.inner.name)
        return _atomic_of(solved) if solved else t
    return t


def _atomic_of(t: Type) -> Type:
    """Return the atomic base type of a collection-like solved generic."""
    t = normalize(t)
    if t.kind == Kind.COLLECTION:
        return _atomic_of(t.base)
    return t


def compatible(argument: Type, parameter: Type, ctx: Context | None = None) -> bool:
    """Return whether an argument type can satisfy a call parameter type."""
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if assignable(argument, parameter, ctx):
        return True
    if parameter.kind == Kind.FUNCTION:
        # Function compatibility is callability-based. A scalar function can be
        # compatible with a vector function type if calling it vectorises.
        return _callable_compatible(argument, parameter, ctx)
    if parameter.kind == Kind.INTERSECTION:
        return all(compatible(argument, p, ctx) for p in parameter.items)
    if parameter.kind == Kind.UNION:
        return any(compatible(argument, p, ctx) for p in parameter.items)
    if argument.kind == Kind.UNION:
        return all(compatible(a, parameter, ctx) for a in argument.items)
    if _is_optional(parameter):
        inner = _optional_inner(parameter)
        return argument.kind == Kind.NONE or (inner is not None and compatible(argument, inner, ctx))
    if _can_vectorise(argument, parameter, ctx):
        return True
    constraints = _solve(parameter, argument)
    if constraints is not None:
        # Compatibility can use generic solving as a fallback, but only when it
        # actually substitutes something. Otherwise concrete mismatches could
        # recurse forever.
        subst = {k: _combine_all(v) for k, v in constraints.items()}
        substituted = _substitute(parameter, subst)
        if subst and all(v is not None for v in subst.values()) and not same(substituted, parameter):
            return compatible(argument, substituted, ctx)
    return False


def _callable_compatible(argument: Type, parameter: Type, ctx: Context) -> bool:
    """Return whether a callable can act as an expected ``Function[...]`` type."""
    if argument.kind == Kind.FUNCTION:
        actual_returns = _overload_result_for_args(Overload(argument.params, argument.returns), parameter.params, ctx)
        return actual_returns is not None and len(actual_returns) == len(parameter.returns) and all(
            compatible(a, p, ctx) for a, p in zip(actual_returns, parameter.returns)
        )
    if argument.kind == Kind.OVERLOAD_SET:
        # The expected Function[...] supplies the call input types for choosing
        # an overload from the callable value.
        matches = [o for o in argument.overloads if _overload_callable_compatible(o, parameter, ctx)]
        return len(matches) == 1 or bool(resolve_overload_result(argument.overloads, parameter.params, ctx))
    if argument.kind == Kind.CSTC:
        result = argument.checker(parameter.params)
        return result is not None and len(result) == len(parameter.returns) and all(
            compatible(a, p, ctx) for a, p in zip(result, parameter.returns)
        )
    return False


def _overload_callable_compatible(overload: Overload, expected: Type, ctx: Context) -> bool:
    """Return whether one overload can be used as an expected function type."""
    if len(overload.params) != len(expected.params) or len(overload.returns) != len(expected.returns):
        return False
    actual_returns = _overload_result_for_args(overload, expected.params, ctx)
    return actual_returns is not None and all(
        compatible(r, e, ctx) for r, e in zip(actual_returns, expected.returns)
    )


def _overload_result_for_args(overload: Overload, args: tuple[Type, ...], ctx: Context) -> tuple[Type, ...] | None:
    """Compute an overload's result stack when called with concrete argument types."""
    if len(overload.params) != len(args):
        return None
    if not all(compatible(a, p, ctx) for a, p in zip(args, overload.params)):
        return None

    vector_rank = 0
    vector_kind: str | None = None
    for arg, param in zip(args, overload.params):
        # Track how much vectorisation was needed. Return types are wrapped in
        # that outer vector shape after the scalar overload is applied.
        if arg.kind != Kind.COLLECTION:
            continue
        if param.kind == Kind.COLLECTION:
            excess = arg.rank - param.rank
        else:
            excess = arg.rank
        if excess > vector_rank:
            vector_rank = excess
        if excess > 0:
            if vector_kind is None:
                vector_kind = arg.coll_kind
            elif vector_kind != arg.coll_kind:
                vector_kind = Coll.LIST_EXACT

    if vector_rank <= 0:
        return overload.returns

    out_kind = Coll.ARRAY_EXACT if vector_kind == Coll.ARRAY_EXACT else Coll.LIST_EXACT
    return tuple(C(out_kind, ret, vector_rank) for ret in overload.returns)


def _can_vectorise(argument: Type, parameter: Type, ctx: Context) -> bool:
    """Return whether compatibility can be achieved through vectorisation."""
    if parameter.kind == Kind.EXACT:
        return compatible(argument, parameter.inner, ctx) and not (
            argument.kind == Kind.COLLECTION and parameter.inner.kind != Kind.COLLECTION
        )
    if argument.kind != Kind.COLLECTION:
        return False
    if parameter.kind == Kind.COLLECTION:
        return same(argument.base, parameter.base) and argument.rank > parameter.rank
    return compatible(argument.base, parameter, ctx)


def _match_specificity(argument: Type, parameter: Type, ctx: Context | None = None) -> Specificity:
    """Classify how specifically an argument matches a parameter."""
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if same(argument, parameter):
        return Specificity.EXACT
    # The order here mirrors the language's specificity ladder. The first
    # applicable category wins.
    if parameter.kind == Kind.TAGGED and argument.kind == Kind.TAGGED:
        if _tag_requirements_met(argument.tags, parameter.tags) and same(argument.inner, parameter.inner):
            return Specificity.TAGGED
    if _is_optional(parameter) and (argument.kind == Kind.NONE or compatible(argument, _optional_inner(parameter), ctx)):
        return Specificity.OPTIONAL
    if parameter.kind == Kind.INTERSECTION and compatible(argument, parameter, ctx):
        return Specificity.INTERSECTION
    if argument.kind == Kind.NOMINAL and parameter.kind == Kind.NOMINAL:
        if ctx.implements(argument.name, parameter.name):
            return Specificity.TRAIT
    if argument.kind == Kind.COLLECTION and parameter.kind == Kind.COLLECTION and _collection_subtype(argument, parameter, ctx):
        return Specificity.RANK
    if parameter.kind == Kind.UNION and compatible(argument, parameter, ctx):
        return Specificity.UNION
    if _can_vectorise(argument, parameter, ctx):
        return Specificity.VECTORISED
    if argument.kind == Kind.CSTC and compatible(argument, parameter, ctx):
        return Specificity.CALL_SITE_CHECKED
    if compatible(argument, parameter, ctx):
        return Specificity.EXACT_GENERIC
    return Specificity.NO_MATCH


def apply_overload(overload: Overload, args: tuple[Type, ...], ctx: Context | None = None) -> AppliedOverload | None:
    """Apply one overload to concrete argument types, returning details on success."""
    ctx = ctx or Context()
    if len(overload.params) != len(args):
        return None

    constraints: dict[str, list[Type]] = {}
    for param, arg in zip(overload.params, args):
        if param.kind == Kind.FUNCTION and arg.kind in {Kind.FUNCTION, Kind.OVERLOAD_SET, Kind.CSTC}:
            # Defer function argument solving. Other parameters should usually
            # determine T before we ask whether this callable fits Function[T].
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

    params = tuple(_substitute(param, substitution) for param in overload.params)
    returns = tuple(_substitute(ret, substitution) for ret in overload.returns)
    if not all(compatible(arg, param, ctx) for arg, param in zip(args, params)):
        return None

    actual_returns = _overload_result_for_args(Overload(params, returns), args, ctx)
    if actual_returns is None:
        return None
    # returns = declared returns after generic substitution.
    # actual_returns = returns after call adaptation such as vectorisation.

    scores = tuple(_match_specificity(arg, param, ctx) for arg, param in zip(args, params))
    if any(score == Specificity.NO_MATCH for score in scores):
        return None

    return AppliedOverload(overload, substitution, params, returns, actual_returns, scores)


def apply_overload_to_stack(
    overload: Overload,
    stack: tuple[Type, ...],
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
    available = stack[-available_count:] if available_count else ()
    remaining_stack = stack[:-available_count] if available_count else stack

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
    new_stack = remaining_stack + applied.actual_returns
    return StackApplication(
        overload=overload,
        substitution=applied.substitution,
        inputs=solved_inputs,
        stack=new_stack,
        params=applied.params,
        returns=applied.returns,
        actual_returns=applied.actual_returns,
        scores=applied.scores,
    )


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
            ResolvedOverload(applied.overload, applied.substitution, applied.params, applied.returns, applied.scores)
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
    if t.kind == Kind.VAR:
        return True
    if t.kind == Kind.NOMINAL:
        return any(_contains_type_var(arg) for arg in t.args)
    if t.kind in {Kind.UNION, Kind.INTERSECTION}:
        return any(_contains_type_var(item) for item in t.items)
    if t.kind == Kind.TUPLE:
        return any(_contains_type_var(item) for item in t.params)
    if t.kind == Kind.COLLECTION:
        return _contains_type_var(t.base)
    if t.kind == Kind.FUNCTION:
        return any(_contains_type_var(item) for item in t.params + t.returns)
    if t.kind in {Kind.TAGGED, Kind.EXACT, Kind.ATOMIC}:
        return _contains_type_var(t.inner)
    return False


def _dominates(a: tuple[Specificity, ...], b: tuple[Specificity, ...]) -> bool:
    """Return whether specificity vector ``a`` strictly dominates ``b``."""
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def _tag_requirements_met(actual: frozenset[str], required: frozenset[str]) -> bool:
    """Return whether an actual tag set satisfies required/present tags."""
    for tag in required:
        if tag.startswith("!"):
            if tag[1:] in actual:
                return False
        elif tag not in actual:
            return False
    return True

