"""Type constructors, normalization, equality, and display formatting."""

from __future__ import annotations

from collections.abc import Iterable

from valiance.symbols import Symbol
from valiance.types.nodes import (
    ArrayExactType,
    ArrayMinType,
    AtomicType,
    CollectionType,
    DataTag,
    ElementTag,
    ExactType,
    FunctionType,
    IntersectionType,
    ListExactType,
    ListMinType,
    ListRuggedType,
    NeverType,
    NominalType,
    NoneTypeNode,
    Overload,
    OverloadSetType,
    RankVariable,
    RowField,
    RowType,
    TaggedType,
    TupleType,
    TupleTypeItem,
    Type,
    UnionType,
    VariadicTupleType,
    VarType,
)

CollectionClass = type[CollectionType]
SOME = Symbol("Some")
OK = Symbol("OK")
RESULT = Symbol("Result")
ERR = Symbol("Err")
NUMBER = Symbol("Number")
REAL = Symbol("Real")
INTEGER = Symbol("Integer")


def Never() -> Type:
    """Create the bottom type, assignable to every type."""
    return NeverType()


def NoneType() -> Type:
    """Create the ``None`` type."""
    return NoneTypeNode()


def N(name: Symbol, *args: Type) -> Type:
    """Create a nominal type, optionally with invariant generic arguments."""
    return NominalType(name, tuple(args))


def V(name: str) -> Type:
    """Create a generic type variable."""
    return VarType(name)


def TypeVariable(name: str) -> Type:
    """Create a generic type variable with a readable constructor name."""
    return V(name)


def Some(inner: Type) -> Type:
    """Create the explicit ``Some[T]`` wrapper used by optional types."""
    return N(SOME, inner)


def OKType(inner: Type) -> Type:
    """Create the explicit ``OK[T]`` wrapper used by Result success values."""
    return N(OK, inner)


def Result(ok: Type, err: Type) -> Type:
    """Create a nominal ``Result[ok, err]`` type."""
    return N(RESULT, ok, err)


def U(*types: Type) -> Type:
    """Create and normalize a union type."""
    return normalize(UnionType(frozenset(types)))


def I(*types: Type) -> Type:  # noqa: E743
    """Create and normalize an intersection type."""
    return normalize(IntersectionType(frozenset(types)))


def Tup(*types: Type) -> Type:
    """Create a fixed positional tuple type."""
    return TupleType(tuple(types))


def TupVariadic(*items: Type | TupleTypeItem) -> Type:
    """Create an arbitrary-length tuple pattern type."""
    parts = tuple(
        item if isinstance(item, TupleTypeItem) else TupleTypeItem(item)
        for item in items
    )
    return VariadicTupleType(parts)


def TupRepeat(item: Type) -> Type:
    """Create a homogeneous arbitrary-length tuple type."""
    return TupVariadic(TupleTypeItem(item, repeated=True))


def Field(name: Symbol, typ: Type) -> RowField:
    """Create one required field for a row-constrained type."""
    return RowField(name, typ)


def Row(base: Type, *fields: RowField) -> Type:
    """Create a type constrained by required fields."""
    return normalize(RowType(base, tuple(fields)))


def C(
    collection_type: CollectionClass,
    base: Type,
    rank: int | RankVariable = 1,
) -> Type:
    """Create a collection type with a rank mode, base type, and rank."""
    return collection_type(base, rank)


def ExactList(base: Type, rank: int = 1) -> Type:
    """Create a fixed-rank list type."""
    return C(ListExactType, base, rank)


def AtLeastList(base: Type, rank: int = 1) -> Type:
    """Create a minimum-rank list type."""
    return C(ListMinType, base, rank)


def RuggedList(base: Type, rank: int = 1) -> Type:
    """Create a potentially-ragged list type."""
    return C(ListRuggedType, base, rank)


def ExactArray(base: Type, rank: int = 1) -> Type:
    """Create a fixed-rank array type."""
    return C(ArrayExactType, base, rank)


def AtLeastArray(base: Type, rank: int = 1) -> Type:
    """Create a minimum-rank array type."""
    return C(ArrayMinType, base, rank)


def Fn(
    params: Iterable[Type] | None = None,
    returns: Iterable[Type] | None = None,
    element_tags: Iterable[ElementTag | str] = (),
) -> Type:
    """Create a stack-effect function type."""
    tags = frozenset(_element_tag(tag) for tag in element_tags)
    if params is None and returns is None:
        return FunctionType(None, None, tags)
    return FunctionType(tuple(params or ()), tuple(returns or ()), tags)


def Overloads(*overloads: Overload) -> Type:
    """Create an overloaded callable value from one or more signatures."""
    return OverloadSetType(tuple(overloads))


TagSpec = str | DataTag


def Tagged(inner: Type, *tags: TagSpec) -> Type:
    """Create a tagged type, merging nested tag wrappers during normalization."""
    return normalize(TaggedType(inner, frozenset(_tag(tag) for tag in tags)))


def WithTag(inner: Type, name: str, *, depth: int = 0) -> Type:
    """Create a type that requires a present data tag."""
    return Tagged(inner, DataTag(name, depth=depth))


def WithoutTag(inner: Type, name: str, *, depth: int = 0) -> Type:
    """Create a type that requires a data tag to be absent."""
    return Tagged(inner, DataTag(name, depth=depth, absent=True))


def Exact(inner: Type) -> Type:
    """Create a parameter wrapper that disables vectorisation for the inner type."""
    return ExactType(inner)


def Atomic(var: Type) -> Type:
    """Create an atomic-view marker for a type variable."""
    return AtomicType(var)


def optional(inner: Type) -> Type:
    """Create the optional form of a type as ``Some[T] | None``."""
    # Optionals are represented in the same shape the language describes them:
    # a union of an explicit present value and None.
    return U(Some(inner), NoneType())


def _is_optional(t: Type) -> bool:
    """Return whether a normalized type contains ``None`` as a union member."""
    t = normalize(t)
    return isinstance(t, UnionType) and any(
        isinstance(x, NoneTypeNode) for x in t.items
    )


def _optional_inner(t: UnionType) -> Type | None:
    """Return the non-None payload of an optional type, if it has one."""
    normal = normalize(t)
    if not _is_optional(normal):
        return None
    non_none: list[Type] = []
    for item in t.items:
        if isinstance(item, NoneTypeNode):
            continue
        if isinstance(item, NominalType) and item.name == SOME and len(item.args) == 1:
            non_none.append(item.args[0])
        else:
            non_none.append(item)
    if not non_none:
        return None
    return U(*non_none) if len(non_none) > 1 else non_none[0]


def normalize(t: Type) -> Type:
    """Canonicalize unions, intersections, nested collections, and wrappers."""
    if isinstance(t, UnionType):
        # Flattening/deduplication means equality can stay structural. This is
        # also where Never disappears from ordinary unions.
        flat: set[Type] = set()
        for item in t.items:
            item = normalize(item)
            if isinstance(item, NeverType):
                continue
            if isinstance(item, UnionType):
                flat.update(item.items)
            else:
                flat.add(item)
        if not flat:
            return Never()
        result = _normalize_result_union(flat)
        if result is not None:
            return result
        flat = _normalize_numeric_union(flat)
        if len(flat) == 1:
            return next(iter(flat))
        return UnionType(frozenset(flat))

    if isinstance(t, IntersectionType):
        flat: set[Type] = set()
        for item in t.items:
            item = normalize(item)
            if isinstance(item, IntersectionType):
                flat.update(item.items)
            else:
                flat.add(item)
        if len(flat) == 1:
            return next(iter(flat))
        return IntersectionType(frozenset(flat))

    if isinstance(t, CollectionType):
        base = normalize(t.base)
        if (
            isinstance(base, CollectionType)
            and isinstance(t.rank, int)
            and isinstance(base.rank, int)
        ):
            # Surface syntax can produce nested collection nodes, e.g.
            # Number++* parses as (Number+2)*. Collapse those into the weakest
            # rank mode that preserves the meaning: Number*3.
            collapsed = collapse_nested_collection(type(t), base, t.rank)
            if collapsed is not None:
                return collapsed
        return type(t)(base, t.rank)

    if isinstance(t, RowType):
        return RowType(
            normalize(t.base),
            _normalize_row_fields(t.fields),
        )

    if isinstance(t, FunctionType):
        if t.params is None and t.returns is None:
            return t
        return Fn(
            (normalize(p) for p in t.params or ()),
            (normalize(r) for r in t.returns or ()),
            t.element_tags,
        )

    if isinstance(t, VariadicTupleType):
        return VariadicTupleType(
            tuple(TupleTypeItem(normalize(item.typ), item.repeated) for item in t.items)
        )

    if isinstance(t, NominalType):
        return N(t.name, *(normalize(a) for a in t.args))

    if isinstance(t, TaggedType):
        inner = normalize(t.inner)
        if isinstance(inner, TaggedType):
            return Tagged(inner.inner, *(set(t.tags) | set(inner.tags)))
        return TaggedType(inner, t.tags)

    return t


def _normalize_result_union(items: set[Type]) -> Type | None:
    ok_items: list[Type] = []
    err_items: list[Type] = []
    saw_explicit_ok = False
    for item in items:
        if isinstance(item, NominalType) and item.name == OK and len(item.args) == 1:
            ok_items.append(item.args[0])
            saw_explicit_ok = True
        elif _is_err_nominal(item):
            err_items.append(item)
        elif (
            isinstance(item, NominalType)
            and item.name == RESULT
            and len(item.args) == 2
        ):
            ok_items.append(item.args[0])
            err_items.append(item.args[1])
        else:
            ok_items.append(item)

    if not err_items or not ok_items:
        if saw_explicit_ok and ok_items and not err_items:
            ok = U(*ok_items) if len(ok_items) > 1 else ok_items[0]
            return OKType(ok)
        return None

    ok = U(*ok_items) if len(ok_items) > 1 else ok_items[0]
    err = U(*err_items) if len(err_items) > 1 else err_items[0]
    return Result(ok, err)


def _normalize_numeric_union(items: set[Type]) -> set[Type]:
    names = {
        item.name
        for item in items
        if isinstance(item, NominalType) and not item.args
    }
    remove: set[Type] = set()
    if NUMBER in names:
        remove.update(
            item
            for item in items
            if isinstance(item, NominalType)
            and not item.args
            and item.name in {INTEGER, REAL}
        )
    elif REAL in names:
        remove.update(
            item
            for item in items
            if isinstance(item, NominalType) and not item.args and item.name == INTEGER
        )
    return items - remove


def _is_err_nominal(t: Type) -> bool:
    return (
        isinstance(t, NominalType)
        and not t.args
        and (t.name == ERR or t.name.text.endswith("Error"))
    )


def _normalize_row_fields(fields: tuple[RowField, ...]) -> tuple[RowField, ...]:
    merged: dict[Symbol, Type] = {}
    for field in fields:
        typ = normalize(field.typ)
        previous = merged.get(field.name)
        merged[field.name] = typ if previous is None else U(previous, typ)
    return tuple(RowField(name, typ) for name, typ in sorted(merged.items()))


def collapse_nested_collection(
    outer_type: CollectionClass, inner: CollectionType, outer_rank: int
) -> Type | None:
    """Collapse nested collection ranks when mixed rank modes have a clear form."""
    total_rank = inner.rank + outer_rank
    inner_type = type(inner)
    if inner_type is outer_type:
        return C(outer_type, inner.base, total_rank)

    list_like = (ListExactType, ListMinType, ListRuggedType)
    array_like = (ArrayExactType, ArrayMinType)

    if issubclass(inner_type, list_like) and issubclass(outer_type, list_like):
        # Within list ranks, rugged is weakest, then minimum, then exact.
        if ListRuggedType in {inner_type, outer_type}:
            return C(ListRuggedType, inner.base, total_rank)
        if ListMinType in {inner_type, outer_type}:
            return C(ListMinType, inner.base, total_rank)
        return C(ListExactType, inner.base, total_rank)

    if issubclass(inner_type, array_like) and issubclass(outer_type, array_like):
        if ArrayMinType in {inner_type, outer_type}:
            return C(ArrayMinType, inner.base, total_rank)
        return C(ArrayExactType, inner.base, total_rank)

    if issubclass(inner_type, array_like) and issubclass(outer_type, list_like):
        # Arrays can be treated as lists, but once a list wrapper is involved
        # the result is list-shaped rather than array-shaped.
        if outer_type is ListRuggedType:
            return C(ListRuggedType, inner.base, total_rank)
        if outer_type is ListMinType or inner_type is ArrayMinType:
            return C(ListMinType, inner.base, total_rank)
        return C(ListExactType, inner.base, total_rank)

    return None


def same(a: Type, b: Type) -> bool:
    """Return canonical type equality without subtyping or compatibility."""
    return normalize(a) == normalize(b)


def show(t: Type) -> str:
    """Render a type as compact user-facing syntax."""
    t = normalize(t)
    if isinstance(t, NeverType):
        return "Never"
    if isinstance(t, NoneTypeNode):
        return "None"
    if isinstance(t, VarType):
        return t.name
    if isinstance(t, NominalType):
        if not t.args:
            return str(t.name)
        return f"{t.name}[{', '.join(show(a) for a in t.args)}]"
    if isinstance(t, UnionType):
        return " | ".join(sorted(show(i) for i in t.items))
    if isinstance(t, IntersectionType):
        return " & ".join(sorted(show(i) for i in t.items))
    if isinstance(t, TupleType):
        return "{" + ", ".join(show(p) for p in t.params) + "}"
    if isinstance(t, VariadicTupleType):
        return (
            "{"
            + ", ".join(
                show(item.typ) + ("..." if item.repeated else "") for item in t.items
            )
            + "}"
        )
    if isinstance(t, RowType):
        fields = ", ".join(f".{field.name}: {show(field.typ)}" for field in t.fields)
        return f"{show(t.base)}({fields})"
    if isinstance(t, CollectionType):
        suffix = {
            ListExactType: "+",
            ListMinType: "*",
            ListRuggedType: "~",
            ArrayExactType: "^",
            ArrayMinType: ">",
        }[type(t)]
        if isinstance(t.rank, RankVariable):
            rank = f"${t.rank.name}"
        else:
            rank = "" if t.rank == 1 else str(t.rank)
        return f"{_show_collection_base(t.base)}{suffix}{rank}"
    if isinstance(t, FunctionType):
        if t.params is None and t.returns is None:
            return _show_function_with_tags("Function", t.element_tags)
        params = ", ".join(show(p) for p in t.params)
        returns = ", ".join(show(r) for r in t.returns)
        return _show_function_with_tags(
            f"Function[{params} -> {returns}]",
            t.element_tags,
        )
    if isinstance(t, TaggedType):
        return f"{' '.join(_show_tag(tag) for tag in sorted(t.tags))} {show(t.inner)}"
    if isinstance(t, OverloadSetType):
        entries = ", ".join(
            show(Fn(overload.params, overload.returns)) for overload in t.overloads
        )
        return f"OverloadSet[{entries}]"
    return type(t).__name__


def _show_collection_base(t: Type) -> str:
    rendered = show(t)
    if isinstance(normalize(t), (UnionType, IntersectionType)):
        return f"({rendered})"
    return rendered


def _tag(tag: TagSpec) -> DataTag:
    if isinstance(tag, DataTag):
        return tag
    absent = tag.startswith("!")
    name = tag[1:] if absent else tag
    return DataTag(name, absent=absent)


def _element_tag(tag: ElementTag | str) -> ElementTag:
    if isinstance(tag, ElementTag):
        return tag
    absent = tag.startswith("!")
    name = tag[1:] if absent else tag
    return ElementTag(Symbol(name), absent=absent)


def _show_function_with_tags(base: str, tags: frozenset[ElementTag]) -> str:
    if not tags:
        return base
    return f"{base}<{', '.join(_show_element_tag(tag) for tag in sorted(tags))}>"


def _show_element_tag(tag: ElementTag) -> str:
    prefix = "!" if tag.absent else ""
    if not tag.args:
        return f"{prefix}{tag.name}"
    return f"{prefix}{tag.name}[{', '.join(show(arg) for arg in tag.args)}]"


def _show_tag(tag: DataTag) -> str:
    prefix = "#!" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"
