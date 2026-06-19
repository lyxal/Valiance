from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable


class Kind:
    NEVER = "Never"
    NONE = "None"
    NOMINAL = "Nominal"
    VAR = "Var"
    UNION = "Union"
    INTERSECTION = "Intersection"
    TUPLE = "Tuple"
    DICT = "Dict"
    COLLECTION = "Collection"
    FUNCTION = "Function"
    OVERLOAD_SET = "OverloadSet"
    TAGGED = "Tagged"
    EXACT = "Exact"
    ATOMIC = "Atomic"
    CSTC = "CallSiteCheckedFunction"


class Coll:
    LIST_EXACT = "list_exact"      # T+n
    LIST_MIN = "list_min"          # T*n
    LIST_RUGGED = "list_rugged"    # T~n
    ARRAY_EXACT = "array_exact"    # T^n
    ARRAY_MIN = "array_min"        # T>n


class Specificity(IntEnum):
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
    kind: str
    name: str | None = None
    args: tuple["Type", ...] = ()
    items: frozenset["Type"] = field(default_factory=frozenset)
    fields: tuple[tuple[str, "Type"], ...] = ()
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
        return show(self)


@dataclass(frozen=True)
class Overload:
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    generics: frozenset[str] = field(default_factory=frozenset)
    is_cstc: bool = False


@dataclass(frozen=True)
class InferState:
    inputs: tuple[Type, ...]
    stack: tuple[Type, ...]


@dataclass(frozen=True)
class ResolvedOverload:
    overload: Overload
    substitution: dict[str, Type]
    params: tuple[Type, ...]
    returns: tuple[Type, ...]
    scores: tuple[Specificity, ...]


@dataclass
class Context:
    trait_impls: dict[str, set[str]] = field(default_factory=dict)
    trait_parents: dict[str, set[str]] = field(default_factory=dict)
    variant_members: dict[str, str] = field(default_factory=dict)
    unit_tags: set[str] = field(default_factory=set)

    def implements(self, type_name: str, trait_name: str) -> bool:
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


def Never() -> Type:
    return Type(Kind.NEVER)


def NoneType() -> Type:
    return Type(Kind.NONE)


def N(name: str, *args: Type) -> Type:
    return Type(Kind.NOMINAL, name=name, args=tuple(args))


def V(name: str) -> Type:
    return Type(Kind.VAR, name=name)


def Some(inner: Type) -> Type:
    return N("Some", inner)


def Result(ok: Type, err: Type) -> Type:
    return N("Result", ok, err)


def U(*types: Type) -> Type:
    return normalize(Type(Kind.UNION, items=frozenset(types)))


def I(*types: Type) -> Type:
    return normalize(Type(Kind.INTERSECTION, items=frozenset(types)))


def Tup(*types: Type) -> Type:
    return Type(Kind.TUPLE, params=tuple(types))


def C(coll_kind: str, base: Type, rank: int = 1) -> Type:
    return Type(Kind.COLLECTION, coll_kind=coll_kind, base=base, rank=rank)


def Fn(params: Iterable[Type], returns: Iterable[Type]) -> Type:
    return Type(Kind.FUNCTION, params=tuple(params), returns=tuple(returns))


def Overloads(*overloads: Overload) -> Type:
    return Type(Kind.OVERLOAD_SET, overloads=tuple(overloads))


def Tagged(inner: Type, *tags: str) -> Type:
    return normalize(Type(Kind.TAGGED, tags=frozenset(tags), inner=inner))


def Exact(inner: Type) -> Type:
    return Type(Kind.EXACT, inner=inner)


def Atomic(var: Type) -> Type:
    return Type(Kind.ATOMIC, inner=var)


def CSTC(checker: Callable[..., tuple[Type, ...] | None]) -> Type:
    return Type(Kind.CSTC, checker=checker)


def optional(inner: Type) -> Type:
    return U(Some(inner), NoneType())


def is_optional(t: Type) -> bool:
    t = normalize(t)
    return t.kind == Kind.UNION and any(x.kind == Kind.NONE for x in t.items)


def optional_inner(t: Type) -> Type | None:
    t = normalize(t)
    if not is_optional(t):
        return None
    non_none: list[Type] = []
    for item in t.items:
        if item.kind == Kind.NONE:
            continue
        if item.kind == Kind.NOMINAL and item.name == "Some" and len(item.args) == 1:
            non_none.append(item.args[0])
        else:
            non_none.append(item)
    if not non_none:
        return None
    return U(*non_none) if len(non_none) > 1 else non_none[0]


def normalize(t: Type) -> Type:
    if t.kind == Kind.UNION:
        flat: set[Type] = set()
        for item in t.items:
            item = normalize(item)
            if item.kind == Kind.NEVER:
                continue
            if item.kind == Kind.UNION:
                flat.update(item.items)
            else:
                flat.add(item)
        if not flat:
            return Never()
        if len(flat) == 1:
            return next(iter(flat))
        return Type(Kind.UNION, items=frozenset(flat))

    if t.kind == Kind.INTERSECTION:
        flat: set[Type] = set()
        for item in t.items:
            item = normalize(item)
            if item.kind == Kind.INTERSECTION:
                flat.update(item.items)
            else:
                flat.add(item)
        if len(flat) == 1:
            return next(iter(flat))
        return Type(Kind.INTERSECTION, items=frozenset(flat))

    if t.kind == Kind.COLLECTION:
        base = normalize(t.base)
        if base.kind == Kind.COLLECTION and base.coll_kind == t.coll_kind:
            return C(t.coll_kind or "", base.base, (base.rank or 0) + (t.rank or 0))
        return Type(t.kind, coll_kind=t.coll_kind, base=base, rank=t.rank)

    if t.kind == Kind.FUNCTION:
        return Fn((normalize(p) for p in t.params), (normalize(r) for r in t.returns))

    if t.kind == Kind.NOMINAL:
        return N(t.name or "", *(normalize(a) for a in t.args))

    if t.kind == Kind.TAGGED:
        inner = normalize(t.inner)
        if inner.kind == Kind.TAGGED:
            return Tagged(inner.inner, *(set(t.tags) | set(inner.tags)))
        return Type(Kind.TAGGED, tags=t.tags, inner=inner)

    return t


def same(a: Type, b: Type) -> bool:
    return normalize(a) == normalize(b)


def subtype(source: Type, target: Type, ctx: Context | None = None) -> bool:
    ctx = ctx or Context()
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or source.kind == Kind.NEVER:
        return True

    if source.kind == Kind.TAGGED:
        if not (source.tags & ctx.unit_tags) and subtype(source.inner, target, ctx):
            return True

    if target.kind == Kind.TAGGED:
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
        if source.name == target.name and len(source.args) == len(target.args):
            return all(same(a, b) for a, b in zip(source.args, target.args))
        if ctx.implements(source.name or "", target.name or ""):
            return True
        if ctx.variant_members.get(source.name or "") == target.name:
            return True
        if source.name in {"Integer", "Real"} and target.name == "Number":
            return True

    if source.kind == Kind.TUPLE and target.kind == Kind.TUPLE:
        return len(source.params) == len(target.params) and all(
            assignable(a, b, ctx) for a, b in zip(source.params, target.params)
        )

    if source.kind == Kind.COLLECTION and target.kind == Kind.COLLECTION:
        return collection_subtype(source, target, ctx)

    return False


def collection_subtype(source: Type, target: Type, ctx: Context) -> bool:
    if source.coll_kind in {Coll.LIST_MIN, Coll.LIST_RUGGED} and target.coll_kind == Coll.LIST_EXACT:
        if (source.rank or 0) >= (target.rank or 0):
            remainder = collection_remainder(source.coll_kind, source.base, (source.rank or 0) - (target.rank or 0))
            return same(remainder, target.base)
    if not same(source.base, target.base):
        return False
    sk, tk = source.coll_kind, target.coll_kind
    sr, tr = source.rank or 0, target.rank or 0

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
    ctx = ctx or Context()
    source = normalize(source)
    target = normalize(target)

    if same(source, target) or subtype(source, target, ctx):
        return True

    if is_optional(target):
        inner = optional_inner(target)
        return source.kind == Kind.NONE or (inner is not None and assignable(source, inner, ctx))

    if source.kind == Kind.UNION:
        return all(assignable(s, target, ctx) for s in source.items)
    if target.kind == Kind.UNION:
        return any(assignable(source, t, ctx) for t in target.items)

    return False


def solve(pattern: Type, actual: Type) -> dict[str, list[Type]] | None:
    constraints: dict[str, list[Type]] = {}

    def add(name: str, value: Type) -> None:
        constraints.setdefault(name, []).append(normalize(value))

    def rec(p: Type, a: Type) -> bool:
        p, a = normalize(p), normalize(a)
        if p.kind == Kind.VAR:
            add(p.name or "", a)
            return True
        if same(p, a):
            return True
        if p.kind == Kind.ATOMIC:
            return True
        if is_optional(p):
            if a.kind == Kind.NONE:
                return True
            inner = optional_inner(p)
            actual_inner = optional_inner(a) if is_optional(a) else a
            return inner is not None and actual_inner is not None and rec(inner, actual_inner)
        if p.kind == Kind.UNION:
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
            return True
        if p.kind == Kind.TAGGED:
            if a.kind != Kind.TAGGED:
                return all(tag.startswith("!") for tag in p.tags) and rec(p.inner, a)
            if not tag_requirements_met(a.tags, p.tags):
                return False
            return rec(p.inner, a.inner)
        if p.kind == Kind.COLLECTION and a.kind == Kind.COLLECTION:
            return solve_collection(p, a, add)
        return False

    return constraints if rec(pattern, actual) else None


def solve_collection(pattern: Type, actual: Type, add: Callable[[str, Type], None]) -> bool:
    if pattern.base.kind != Kind.VAR:
        return same(pattern, actual)
    n, m = pattern.rank or 0, actual.rank or 0
    if m < n:
        return False
    diff = m - n
    pk, ak = pattern.coll_kind, actual.coll_kind
    base = actual.base

    def bind_as(kind: str) -> bool:
        add(pattern.base.name or "", collection_remainder(kind, base, diff))
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


def collection_remainder(kind: str, base: Type, rank: int) -> Type:
    if rank > 0:
        return C(kind, base, rank)
    if kind in {Coll.LIST_MIN, Coll.ARRAY_MIN}:
        return U(base, C(kind, base, 1))
    if kind == Coll.LIST_RUGGED:
        return U(base, C(Coll.LIST_RUGGED, base, 1))
    return base


def combine(a: Type, b: Type) -> Type | None:
    a, b = normalize(a), normalize(b)
    if same(a, b):
        return a
    if is_optional(a) and is_optional(b):
        ai, bi = optional_inner(a), optional_inner(b)
        if ai is None:
            return b
        if bi is None:
            return a
        inner = combine(ai, bi)
        return optional(inner) if inner else None
    if a.kind == Kind.COLLECTION and b.kind == Kind.COLLECTION and same(a.base, b.base):
        return combine_collections(a, b)
    return None


def combine_collections(a: Type, b: Type) -> Type | None:
    ak, bk = a.coll_kind, b.coll_kind
    ar, br = a.rank or 0, b.rank or 0
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


def combine_all(values: Iterable[Type]) -> Type | None:
    vals = list(values)
    if not vals:
        return None
    out = vals[0]
    for value in vals[1:]:
        out = combine(out, value)
        if out is None:
            return None
    return out


def substitute(t: Type, subst: dict[str, Type]) -> Type:
    t = normalize(t)
    if t.kind == Kind.VAR:
        return subst.get(t.name or "", t)
    if t.kind == Kind.NOMINAL:
        return N(t.name or "", *(substitute(a, subst) for a in t.args))
    if t.kind == Kind.UNION:
        return U(*(substitute(i, subst) for i in t.items))
    if t.kind == Kind.INTERSECTION:
        return I(*(substitute(i, subst) for i in t.items))
    if t.kind == Kind.TUPLE:
        return Tup(*(substitute(p, subst) for p in t.params))
    if t.kind == Kind.COLLECTION:
        return C(t.coll_kind or "", substitute(t.base, subst), t.rank or 0)
    if t.kind == Kind.FUNCTION:
        return Fn((substitute(p, subst) for p in t.params), (substitute(r, subst) for r in t.returns))
    if t.kind == Kind.TAGGED:
        return Tagged(substitute(t.inner, subst), *t.tags)
    if t.kind == Kind.ATOMIC and t.inner.kind == Kind.VAR:
        solved = subst.get(t.inner.name or "")
        return atomic_of(solved) if solved else t
    return t


def atomic_of(t: Type) -> Type:
    t = normalize(t)
    if t.kind == Kind.COLLECTION:
        return atomic_of(t.base)
    return t


def compatible(argument: Type, parameter: Type, ctx: Context | None = None) -> bool:
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if assignable(argument, parameter, ctx):
        return True
    if parameter.kind == Kind.FUNCTION:
        return callable_compatible(argument, parameter, ctx)
    if parameter.kind == Kind.INTERSECTION:
        return all(compatible(argument, p, ctx) for p in parameter.items)
    if parameter.kind == Kind.UNION:
        return any(compatible(argument, p, ctx) for p in parameter.items)
    if argument.kind == Kind.UNION:
        return all(compatible(a, parameter, ctx) for a in argument.items)
    if is_optional(parameter):
        inner = optional_inner(parameter)
        return argument.kind == Kind.NONE or (inner is not None and compatible(argument, inner, ctx))
    if can_vectorise(argument, parameter, ctx):
        return True
    constraints = solve(parameter, argument)
    if constraints is not None:
        subst = {k: combine_all(v) for k, v in constraints.items()}
        substituted = substitute(parameter, subst)
        if subst and all(v is not None for v in subst.values()) and not same(substituted, parameter):
            return compatible(argument, substituted, ctx)
    return False


def callable_compatible(argument: Type, parameter: Type, ctx: Context) -> bool:
    if argument.kind == Kind.FUNCTION:
        actual_returns = overload_result_for_args(Overload(argument.params, argument.returns), parameter.params, ctx)
        return actual_returns is not None and len(actual_returns) == len(parameter.returns) and all(
            compatible(a, p, ctx) for a, p in zip(actual_returns, parameter.returns)
        )
    if argument.kind == Kind.OVERLOAD_SET:
        matches = [o for o in argument.overloads if overload_callable_compatible(o, parameter, ctx)]
        return len(matches) == 1 or bool(resolve_overload(argument.overloads, parameter.params, ctx))
    if argument.kind == Kind.CSTC:
        if argument.checker is None:
            return False
        result = argument.checker(parameter.params)
        return result is not None and len(result) == len(parameter.returns) and all(
            compatible(a, p, ctx) for a, p in zip(result, parameter.returns)
        )
    return False


def overload_callable_compatible(overload: Overload, expected: Type, ctx: Context) -> bool:
    if len(overload.params) != len(expected.params) or len(overload.returns) != len(expected.returns):
        return False
    actual_returns = overload_result_for_args(overload, expected.params, ctx)
    return actual_returns is not None and all(
        compatible(r, e, ctx) for r, e in zip(actual_returns, expected.returns)
    )


def overload_result_for_args(overload: Overload, args: tuple[Type, ...], ctx: Context) -> tuple[Type, ...] | None:
    if len(overload.params) != len(args):
        return None
    if not all(compatible(a, p, ctx) for a, p in zip(args, overload.params)):
        return None

    vector_rank = 0
    vector_kind: str | None = None
    for arg, param in zip(args, overload.params):
        if arg.kind != Kind.COLLECTION:
            continue
        if param.kind == Kind.COLLECTION:
            excess = (arg.rank or 0) - (param.rank or 0)
        else:
            excess = arg.rank or 0
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


def can_vectorise(argument: Type, parameter: Type, ctx: Context) -> bool:
    if parameter.kind == Kind.EXACT:
        return compatible(argument, parameter.inner, ctx) and not (
            argument.kind == Kind.COLLECTION and parameter.inner.kind != Kind.COLLECTION
        )
    if argument.kind != Kind.COLLECTION:
        return False
    if parameter.kind == Kind.COLLECTION:
        return same(argument.base, parameter.base) and (argument.rank or 0) > (parameter.rank or 0)
    return compatible(argument.base, parameter, ctx)


def match_specificity(argument: Type, parameter: Type, ctx: Context | None = None) -> Specificity:
    ctx = ctx or Context()
    argument, parameter = normalize(argument), normalize(parameter)
    if same(argument, parameter):
        return Specificity.EXACT
    if parameter.kind == Kind.TAGGED and argument.kind == Kind.TAGGED:
        if tag_requirements_met(argument.tags, parameter.tags) and same(argument.inner, parameter.inner):
            return Specificity.TAGGED
    if is_optional(parameter) and (argument.kind == Kind.NONE or compatible(argument, optional_inner(parameter), ctx)):
        return Specificity.OPTIONAL
    if parameter.kind == Kind.INTERSECTION and compatible(argument, parameter, ctx):
        return Specificity.INTERSECTION
    if argument.kind == Kind.NOMINAL and parameter.kind == Kind.NOMINAL:
        if ctx.implements(argument.name or "", parameter.name or ""):
            return Specificity.TRAIT
    if argument.kind == Kind.COLLECTION and parameter.kind == Kind.COLLECTION and collection_subtype(argument, parameter, ctx):
        return Specificity.RANK
    if parameter.kind == Kind.UNION and compatible(argument, parameter, ctx):
        return Specificity.UNION
    if can_vectorise(argument, parameter, ctx):
        return Specificity.VECTORISED
    if argument.kind == Kind.CSTC and compatible(argument, parameter, ctx):
        return Specificity.CALL_SITE_CHECKED
    if compatible(argument, parameter, ctx):
        return Specificity.EXACT_GENERIC
    return Specificity.NO_MATCH


def resolve_overload_result(
    overloads: Iterable[Overload], args: tuple[Type, ...], ctx: Context | None = None
) -> ResolvedOverload | None:
    ctx = ctx or Context()
    candidates: list[ResolvedOverload] = []
    for overload in overloads:
        if len(overload.params) != len(args):
            continue
        constraints: dict[str, list[Type]] = {}
        failed = False
        for param, arg in zip(overload.params, args):
            if param.kind == Kind.FUNCTION and arg.kind in {Kind.FUNCTION, Kind.OVERLOAD_SET, Kind.CSTC}:
                continue
            result = solve(param, arg)
            if result is None:
                if contains_type_var(param):
                    failed = True
                    break
                continue
            for key, values in result.items():
                constraints.setdefault(key, []).extend(values)
        if failed:
            continue
        subst: dict[str, Type] = {}
        for key, values in constraints.items():
            combined = combine_all(values)
            if combined is None:
                failed = True
                break
            subst[key] = combined
        if failed:
            continue
        params = tuple(substitute(p, subst) for p in overload.params)
        if not all(compatible(a, p, ctx) for a, p in zip(args, params)):
            continue
        scores = tuple(match_specificity(a, p, ctx) for a, p in zip(args, params))
        if any(s == Specificity.NO_MATCH for s in scores):
            continue
        returns = tuple(substitute(r, subst) for r in overload.returns)
        candidates.append(ResolvedOverload(overload, subst, params, returns, scores))
    winners = []
    for candidate in candidates:
        if not any(dominates(other.scores, candidate.scores) for other in candidates):
            winners.append(candidate)
    return winners[0] if len(winners) == 1 else None


def resolve_overload(overloads: Iterable[Overload], args: tuple[Type, ...], ctx: Context | None = None) -> Overload | None:
    result = resolve_overload_result(overloads, args, ctx)
    return result.overload if result else None


def contains_type_var(t: Type) -> bool:
    t = normalize(t)
    if t.kind == Kind.VAR:
        return True
    if t.kind == Kind.NOMINAL:
        return any(contains_type_var(arg) for arg in t.args)
    if t.kind in {Kind.UNION, Kind.INTERSECTION}:
        return any(contains_type_var(item) for item in t.items)
    if t.kind == Kind.TUPLE:
        return any(contains_type_var(item) for item in t.params)
    if t.kind == Kind.COLLECTION:
        return contains_type_var(t.base)
    if t.kind == Kind.FUNCTION:
        return any(contains_type_var(item) for item in t.params + t.returns)
    if t.kind in {Kind.TAGGED, Kind.EXACT, Kind.ATOMIC}:
        return contains_type_var(t.inner)
    return False


def infer_function(tokens: Iterable[str], overloads: dict[str, list[Overload]], ctx: Context | None = None) -> Type | None:
    ctx = ctx or Context()
    states = {InferState((), ())}
    for token in tokens:
        next_states: set[InferState] = set()
        literal = literal_type(token)
        if literal is not None:
            for state in states:
                next_states.add(InferState(state.inputs, state.stack + (literal,)))
        elif token in overloads:
            for state in states:
                for overload in overloads[token]:
                    applied = apply_inference_overload(state, overload, ctx)
                    if applied is not None:
                        next_states.add(applied)
        else:
            return None
        if not next_states:
            return None
        states = next_states

    inferred = sorted((Overload(state.inputs, state.stack) for state in states), key=lambda o: str(Fn(o.params, o.returns)))
    if not inferred:
        return None
    if len(inferred) == 1:
        return Fn(inferred[0].params, inferred[0].returns)
    return Overloads(*inferred)


def apply_inference_overload(state: InferState, overload: Overload, ctx: Context) -> InferState | None:
    arity = len(overload.params)
    available_count = min(len(state.stack), arity)
    missing_count = arity - available_count
    available = state.stack[-available_count:] if available_count else ()
    remaining_stack = state.stack[:-available_count] if available_count else state.stack
    expected_for_available = overload.params[missing_count:]

    constraints: dict[str, list[Type]] = {}
    for expected, actual in zip(expected_for_available, available):
        result = solve(expected, actual)
        if result is None:
            return None
        for key, values in result.items():
            constraints.setdefault(key, []).extend(values)

    subst: dict[str, Type] = {}
    for key, values in constraints.items():
        combined = combine_all(values)
        if combined is None:
            return None
        subst[key] = combined

    params = tuple(substitute(param, subst) for param in overload.params)
    returns = tuple(substitute(ret, subst) for ret in overload.returns)

    if not all(compatible(actual, expected, ctx) for actual, expected in zip(available, params[missing_count:])):
        return None

    new_inputs = state.inputs + params[:missing_count]
    new_stack = remaining_stack + returns
    return InferState(new_inputs, new_stack)


def literal_type(token: str) -> Type | None:
    if token == "None":
        return NoneType()
    if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
        return N("Number")
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return N("String")
    return None


def dominates(a: tuple[Specificity, ...], b: tuple[Specificity, ...]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def tag_requirements_met(actual: frozenset[str], required: frozenset[str]) -> bool:
    for tag in required:
        if tag.startswith("!"):
            if tag[1:] in actual:
                return False
        elif tag not in actual:
            return False
    return True


def show(t: Type) -> str:
    t = normalize(t)
    if t.kind in {Kind.NEVER, Kind.NONE}:
        return t.kind
    if t.kind == Kind.VAR:
        return t.name or "?"
    if t.kind == Kind.NOMINAL:
        if not t.args:
            return t.name or "?"
        return f"{t.name}[{', '.join(show(a) for a in t.args)}]"
    if t.kind == Kind.UNION:
        return " | ".join(sorted(show(i) for i in t.items))
    if t.kind == Kind.INTERSECTION:
        return " & ".join(sorted(show(i) for i in t.items))
    if t.kind == Kind.TUPLE:
        return "{" + ", ".join(show(p) for p in t.params) + "}"
    if t.kind == Kind.COLLECTION:
        suffix = {
            Coll.LIST_EXACT: "+",
            Coll.LIST_MIN: "*",
            Coll.LIST_RUGGED: "~",
            Coll.ARRAY_EXACT: "^",
            Coll.ARRAY_MIN: ">",
        }[t.coll_kind]
        rank = "" if t.rank == 1 else str(t.rank)
        return f"{show(t.base)}{suffix}{rank}"
    if t.kind == Kind.FUNCTION:
        return f"Function[{', '.join(show(p) for p in t.params)} -> {', '.join(show(r) for r in t.returns)}]"
    if t.kind == Kind.TAGGED:
        return f"{' '.join(sorted(t.tags))} {show(t.inner)}"
    if t.kind == Kind.OVERLOAD_SET:
        entries = ", ".join(show(Fn(overload.params, overload.returns)) for overload in t.overloads)
        return f"OverloadSet[{entries}]"
    if t.kind == Kind.CSTC:
        return "CallSiteCheckedFunction"
    return t.kind
