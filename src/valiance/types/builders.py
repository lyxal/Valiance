from __future__ import annotations

"""Type constructors, normalization, equality, and display formatting."""

from typing import Callable, Iterable

from valiance.types.model import Coll, Kind, Overload, Type


def Never() -> Type:
    """Create the bottom type, assignable to every type."""
    return Type(Kind.NEVER)


def NoneType() -> Type:
    """Create the ``None`` type."""
    return Type(Kind.NONE)


def N(name: str, *args: Type) -> Type:
    """Create a nominal type, optionally with invariant generic arguments."""
    return Type(Kind.NOMINAL, name=name, args=tuple(args))


def V(name: str) -> Type:
    """Create a generic type variable."""
    return Type(Kind.VAR, name=name)


def Some(inner: Type) -> Type:
    """Create the explicit ``Some[T]`` wrapper used by optional types."""
    return N("Some", inner)


def Result(ok: Type, err: Type) -> Type:
    """Create a nominal ``Result[ok, err]`` type."""
    return N("Result", ok, err)


def U(*types: Type) -> Type:
    """Create and normalize a union type."""
    return normalize(Type(Kind.UNION, items=frozenset(types)))


def I(*types: Type) -> Type:
    """Create and normalize an intersection type."""
    return normalize(Type(Kind.INTERSECTION, items=frozenset(types)))


def Tup(*types: Type) -> Type:
    """Create a fixed positional tuple type."""
    return Type(Kind.TUPLE, params=tuple(types))


def C(coll_kind: str, base: Type, rank: int = 1) -> Type:
    """Create a collection type with a rank mode, base type, and rank."""
    return Type(Kind.COLLECTION, coll_kind=coll_kind, base=base, rank=rank)


def Fn(params: Iterable[Type], returns: Iterable[Type]) -> Type:
    """Create a stack-effect function type."""
    return Type(Kind.FUNCTION, params=tuple(params), returns=tuple(returns))


def Overloads(*overloads: Overload) -> Type:
    """Create an overloaded callable value from one or more signatures."""
    return Type(Kind.OVERLOAD_SET, overloads=tuple(overloads))


def Tagged(inner: Type, *tags: str) -> Type:
    """Create a tagged type, merging nested tag wrappers during normalization."""
    return normalize(Type(Kind.TAGGED, tags=frozenset(tags), inner=inner))


def Exact(inner: Type) -> Type:
    """Create a parameter wrapper that disables vectorisation for the inner type."""
    return Type(Kind.EXACT, inner=inner)


def Atomic(var: Type) -> Type:
    """Create an atomic-view marker for a type variable."""
    return Type(Kind.ATOMIC, inner=var)


def CSTC(checker: Callable[..., tuple[Type, ...] | None]) -> Type:
    """Create a call-site-checked function value backed by a checker callback."""
    return Type(Kind.CSTC, checker=checker)


def optional(inner: Type) -> Type:
    """Create the optional form of a type as ``Some[T] | None``."""
    # Optionals are represented in the same shape the language describes them:
    # a union of an explicit present value and None.
    return U(Some(inner), NoneType())


def _is_optional(t: Type) -> bool:
    """Return whether a normalized type contains ``None`` as a union member."""
    t = normalize(t)
    return t.kind == Kind.UNION and any(x.kind == Kind.NONE for x in t.items)


def _optional_inner(t: Type) -> Type | None:
    """Return the non-None payload of an optional type, if it has one."""
    t = normalize(t)
    if not _is_optional(t):
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
    """Canonicalize unions, intersections, nested collections, and wrappers."""
    if t.kind == Kind.UNION:
        # Flattening/deduplication means equality can stay structural. This is
        # also where Never disappears from ordinary unions.
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
        if base.kind == Kind.COLLECTION:
            # Surface syntax can produce nested collection nodes, e.g.
            # Number++* parses as (Number+2)*. Collapse those into the weakest
            # rank mode that preserves the meaning: Number*3.
            collapsed = collapse_nested_collection(t.coll_kind or "", base, t.rank or 0)
            if collapsed is not None:
                return collapsed
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


def collapse_nested_collection(outer_kind: str, inner: Type, outer_rank: int) -> Type | None:
    """Collapse nested collection ranks when mixed rank modes have a clear form."""
    total_rank = (inner.rank or 0) + outer_rank
    inner_kind = inner.coll_kind
    if inner_kind == outer_kind:
        return C(outer_kind, inner.base, total_rank)

    list_like = {Coll.LIST_EXACT, Coll.LIST_MIN, Coll.LIST_RUGGED}
    array_like = {Coll.ARRAY_EXACT, Coll.ARRAY_MIN}

    if inner_kind in list_like and outer_kind in list_like:
        # Within list ranks, rugged is weakest, then minimum, then exact.
        if Coll.LIST_RUGGED in {inner_kind, outer_kind}:
            return C(Coll.LIST_RUGGED, inner.base, total_rank)
        if Coll.LIST_MIN in {inner_kind, outer_kind}:
            return C(Coll.LIST_MIN, inner.base, total_rank)
        return C(Coll.LIST_EXACT, inner.base, total_rank)

    if inner_kind in array_like and outer_kind in array_like:
        if Coll.ARRAY_MIN in {inner_kind, outer_kind}:
            return C(Coll.ARRAY_MIN, inner.base, total_rank)
        return C(Coll.ARRAY_EXACT, inner.base, total_rank)

    if inner_kind in array_like and outer_kind in list_like:
        # Arrays can be treated as lists, but once a list wrapper is involved
        # the result is list-shaped rather than array-shaped.
        if outer_kind == Coll.LIST_RUGGED:
            return C(Coll.LIST_RUGGED, inner.base, total_rank)
        if outer_kind == Coll.LIST_MIN or inner_kind == Coll.ARRAY_MIN:
            return C(Coll.LIST_MIN, inner.base, total_rank)
        return C(Coll.LIST_EXACT, inner.base, total_rank)

    return None


def same(a: Type, b: Type) -> bool:
    """Return canonical type equality without subtyping or compatibility."""
    return normalize(a) == normalize(b)

def show(t: Type) -> str:
    """Render a type as compact user-facing syntax."""
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
