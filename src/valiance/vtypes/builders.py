"""Type constructors, normalization, equality, and display formatting."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from valiance.vtypes.symbols import Symbol
from valiance.vtypes.nodes import (
    AnonymousTraitRequirement,
    AnonymousTraitType,
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


def AnonymousTrait(
    generics: Iterable[Symbol],
    requirements: Iterable[AnonymousTraitRequirement],
) -> Type:
    """Create an inline structural trait type."""
    return AnonymousTraitType(tuple(generics), tuple(requirements))


TagSpec = str | DataTag


def Tagged(inner: Type, *tags: TagSpec, exact: bool = False) -> Type:
    """Create a tagged type, merging nested tag wrappers during normalization."""
    return normalize(
        TaggedType(inner, frozenset(_tag(tag) for tag in tags), exact=exact)
    )


def ExactTags(inner: Type, *tags: TagSpec) -> Type:
    """Create a type that accepts exactly the listed positive data tags."""
    return Tagged(inner, *tags, exact=True)


def WithTag(inner: Type, name: str, *, depth: int = 0) -> Type:
    """Create a type that requires a present data tag."""
    return Tagged(inner, DataTag(name, depth=depth))


def WithoutTag(inner: Type, name: str, *, depth: int = 0) -> Type:
    """Create a type that requires a data tag to be absent."""
    return Tagged(inner, DataTag(name, depth=depth, absent=True))


def Exact(inner: Type) -> Type:
    """Create call-policy metadata that disables parameter vectorisation."""
    return ExactType(inner)


def Atomic(var: Type) -> Type:
    """Create call-policy metadata requiring a scalar argument position."""
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
        optional_result = _normalize_optional_union(flat)
        if optional_result is not None:
            return optional_result
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
            if isinstance(item, NeverType):
                return Never()
            if isinstance(item, IntersectionType):
                flat.update(item.items)
            else:
                flat.add(item)
        flat = _normalize_numeric_intersection(flat)
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
                return normalize(collapsed)
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

    if isinstance(t, AnonymousTraitType):
        return AnonymousTraitType(
            t.generics,
            tuple(
                AnonymousTraitRequirement(
                    requirement.name,
                    Overload(
                        tuple(normalize(p) for p in requirement.overload.params),
                        tuple(normalize(r) for r in requirement.overload.returns),
                        requirement.overload.generic_constraints,
                        requirement.overload.where_clause,
                        requirement.overload.param_names,
                        requirement.overload.call_site_body,
                        requirement.overload.element_tags,
                        requirement.overload.annotation_error,
                        requirement.overload.annotation_warning,
                        requirement.overload.param_defaults,
                        requirement.overload.is_multi,
                        requirement.overload.runtime_static_values,
                    ),
                )
                for requirement in t.requirements
            ),
        )

    if isinstance(t, VariadicTupleType):
        return VariadicTupleType(
            tuple(TupleTypeItem(normalize(item.typ), item.repeated) for item in t.items)
        )

    if isinstance(t, NominalType):
        args = tuple(normalize(arg) for arg in t.args)
        if t.name == SOME and len(args) == 1 and isinstance(args[0], NeverType):
            return Never()
        return N(t.name, *args)

    if isinstance(t, TaggedType):
        inner = normalize(t.inner)
        if isinstance(inner, TaggedType):
            return Tagged(
                inner.inner,
                *(set(t.tags) | set(inner.tags)),
                exact=t.exact or inner.exact,
            )
        if isinstance(inner, ExactType):
            return Exact(Tagged(inner.inner, *t.tags, exact=t.exact))
        return TaggedType(inner, t.tags, exact=t.exact)

    if isinstance(t, ExactType):
        inner = normalize(t.inner)
        return inner if isinstance(inner, ExactType) else ExactType(inner)

    if isinstance(t, AtomicType):
        inner = normalize(t.inner)
        return inner if isinstance(inner, AtomicType) else AtomicType(inner)

    return t


def _normalize_optional_union(items: set[Type]) -> Type | None:
    """Normalize raw and explicitly wrapped present optional branches.

    A present value may be written either as ``T`` or ``Some[T]``.  Once an
    explicit ``Some`` branch appears, all non-``None`` branches describe the
    same present payload and must be merged inside one wrapper.  Construct the
    final union directly to avoid recursively re-entering this normalization.
    """
    payloads: list[Type] = []
    saw_some = False
    saw_none = False
    for item in items:
        if isinstance(item, NoneTypeNode):
            saw_none = True
        elif (
            isinstance(item, NominalType) and item.name == SOME and len(item.args) == 1
        ):
            payloads.append(item.args[0])
            saw_some = True
        else:
            payloads.append(item)

    if not saw_some or not payloads:
        return None
    payload = U(*payloads) if len(payloads) > 1 else payloads[0]
    present = Some(payload)
    if not saw_none:
        return present
    return UnionType(frozenset((present, NoneType())))


def _normalize_result_union(items: set[Type]) -> Type | None:
    """Normalize result union for type construction and display."""
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


def _normalize_numeric_intersection(items: set[Type]) -> set[Type]:
    """Remove numeric supertypes made redundant by narrower intersections."""
    names = {
        item.name for item in items if isinstance(item, NominalType) and not item.args
    }
    remove: set[Type] = set()
    if INTEGER in names:
        remove.update(
            item
            for item in items
            if isinstance(item, NominalType)
            and not item.args
            and item.name in {REAL, NUMBER}
        )
    elif REAL in names:
        remove.update(
            item
            for item in items
            if isinstance(item, NominalType) and not item.args and item.name == NUMBER
        )
    return items - remove


def _normalize_numeric_union(items: set[Type]) -> set[Type]:
    """Normalize numeric union for type construction and display."""
    names = {
        item.name for item in items if isinstance(item, NominalType) and not item.args
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
    """Return whether the value is err nominal."""
    return (
        isinstance(t, NominalType)
        and not t.args
        and (t.name == ERR or t.name.text.endswith("Error"))
    )


def _normalize_row_fields(fields: tuple[RowField, ...]) -> tuple[RowField, ...]:
    """Normalize row fields for type construction and display."""
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

    # Do not flatten a list whose item type is an array.  Arrays are usable as
    # lists at a relation boundary, but an outer list of arrays still carries a
    # meaningful item boundary.  Erasing it would widen the type and can break
    # covariance when the target item type specifically requires an array.
    return None


def same(a: Type, b: Type) -> bool:
    """Return canonical equality, including alpha-equivalent local generics."""
    left = normalize(a)
    right = normalize(b)
    if left == right:
        return True
    return _alpha_canonicalize(left) == _alpha_canonicalize(right)


def _alpha_canonicalize(
    t: Type,
    scope: dict[str, str] | None = None,
    depth: int = 0,
) -> Type:
    """Rename locally bound generics to deterministic, capture-free names."""
    scope = {} if scope is None else scope
    if isinstance(t, VarType):
        return VarType(scope.get(t.name, t.name))
    if isinstance(t, NominalType):
        return NominalType(
            t.name,
            tuple(_alpha_canonicalize(arg, scope, depth) for arg in t.args),
        )
    if isinstance(t, UnionType):
        return UnionType(
            frozenset(_alpha_canonicalize(item, scope, depth) for item in t.items)
        )
    if isinstance(t, IntersectionType):
        return IntersectionType(
            frozenset(_alpha_canonicalize(item, scope, depth) for item in t.items)
        )
    if isinstance(t, TupleType):
        return TupleType(
            tuple(_alpha_canonicalize(item, scope, depth) for item in t.params)
        )
    if isinstance(t, VariadicTupleType):
        return VariadicTupleType(
            tuple(
                TupleTypeItem(
                    _alpha_canonicalize(item.typ, scope, depth),
                    item.repeated,
                )
                for item in t.items
            )
        )
    if isinstance(t, RowType):
        return RowType(
            _alpha_canonicalize(t.base, scope, depth),
            tuple(
                RowField(
                    field.name,
                    _alpha_canonicalize(field.typ, scope, depth),
                )
                for field in t.fields
            ),
        )
    if isinstance(t, CollectionType):
        return type(t)(_alpha_canonicalize(t.base, scope, depth), t.rank)
    if isinstance(t, FunctionType):
        params = (
            None
            if t.params is None
            else tuple(_alpha_canonicalize(param, scope, depth) for param in t.params)
        )
        returns = (
            None
            if t.returns is None
            else tuple(_alpha_canonicalize(ret, scope, depth) for ret in t.returns)
        )
        tags = frozenset(
            ElementTag(
                tag.name,
                tuple(_alpha_canonicalize(arg, scope, depth) for arg in tag.args),
                tag.absent,
            )
            for tag in t.element_tags
        )
        return FunctionType(params, returns, tags)
    if isinstance(t, AnonymousTraitType):
        local = dict(scope)
        canonical_generics: list[Symbol] = []
        for index, generic in enumerate(t.generics):
            canonical = f"\x00trait:{depth}:{index}"
            local[generic.text] = canonical
            canonical_generics.append(Symbol(canonical))
        requirements = tuple(
            AnonymousTraitRequirement(
                requirement.name,
                _alpha_canonicalize_overload(
                    requirement.overload,
                    local,
                    depth + 1,
                    index,
                ),
            )
            for index, requirement in enumerate(t.requirements)
        )
        return AnonymousTraitType(tuple(canonical_generics), requirements)
    if isinstance(t, OverloadSetType):
        return OverloadSetType(
            tuple(
                _alpha_canonicalize_overload(overload, scope, depth + 1, index)
                for index, overload in enumerate(t.overloads)
            )
        )
    if isinstance(t, TaggedType):
        return TaggedType(
            _alpha_canonicalize(t.inner, scope, depth),
            t.tags,
            exact=t.exact,
        )
    if isinstance(t, ExactType):
        return ExactType(_alpha_canonicalize(t.inner, scope, depth))
    if isinstance(t, AtomicType):
        return AtomicType(_alpha_canonicalize(t.inner, scope, depth))
    return t


def _alpha_canonicalize_overload(
    overload: Overload,
    scope: dict[str, str],
    depth: int,
    overload_index: int,
) -> Overload:
    """Canonicalize one overload while respecting its local generic bounds."""
    local = dict(scope)
    for index, constraint in enumerate(overload.generic_constraints):
        local[constraint.name] = f"\x00overload:{depth}:{overload_index}:{index}"
    constraints = tuple(
        type(constraint)(
            local[constraint.name],
            _alpha_canonicalize(constraint.bound, local, depth),
            constraint.variance,
        )
        for constraint in overload.generic_constraints
    )
    return Overload(
        tuple(_alpha_canonicalize(param, local, depth) for param in overload.params),
        tuple(_alpha_canonicalize(ret, local, depth) for ret in overload.returns),
        constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        frozenset(
            ElementTag(
                tag.name,
                tuple(_alpha_canonicalize(arg, local, depth) for arg in tag.args),
                tag.absent,
            )
            for tag in overload.element_tags
        ),
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
        overload.is_multi,
        overload.runtime_static_values,
    )


def show(
    t: Type,
    *,
    type_variable_name: Callable[[str], str] | None = None,
) -> str:
    """Render a type as compact user-facing syntax.

    ``type_variable_name`` lets source emitters replace analyser-local variable
    names without mutating the type. Variables bound by an anonymous trait or
    overload remain local and bypass the callback.
    """

    return _show(t, type_variable_name, frozenset())


def _show(
    t: Type,
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Render one type while tracking locally bound generic variables."""

    t = normalize(t)
    if isinstance(t, NeverType):
        return "Never"
    if isinstance(t, NoneTypeNode):
        return "None"
    if isinstance(t, VarType):
        if type_variable_name is None or t.name in bound:
            return t.name
        return type_variable_name(t.name)
    if isinstance(t, NominalType):
        if not t.args:
            return str(t.name)
        args = ", ".join(_show(a, type_variable_name, bound) for a in t.args)
        return f"{t.name}[{args}]"
    if isinstance(t, UnionType):
        items = (
            _show(item, type_variable_name, bound) for item in sorted(t.items, key=repr)
        )
        return " | ".join(sorted(items))
    if isinstance(t, IntersectionType):
        items = (
            _show(item, type_variable_name, bound) for item in sorted(t.items, key=repr)
        )
        return " & ".join(sorted(items))
    if isinstance(t, TupleType):
        return (
            "{"
            + ", ".join(_show(param, type_variable_name, bound) for param in t.params)
            + "}"
        )
    if isinstance(t, VariadicTupleType):
        return (
            "{"
            + ", ".join(
                _show(item.typ, type_variable_name, bound)
                + ("..." if item.repeated else "")
                for item in t.items
            )
            + "}"
        )
    if isinstance(t, RowType):
        base = _show(t.base, type_variable_name, bound)
        rendered_fields = ", ".join(
            f".{field.name}: {_show(field.typ, type_variable_name, bound)}"
            for field in t.fields
        )
        return f"{base}({rendered_fields})"
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
        base = _show_collection_base(t.base, type_variable_name, bound)
        return f"{base}{suffix}{rank}"
    if isinstance(t, FunctionType):
        if t.params is None and t.returns is None:
            return _show_function_with_tags(
                "Function",
                t.element_tags,
                type_variable_name,
                bound,
            )
        params = ", ".join(
            _show(param, type_variable_name, bound) for param in t.params
        )
        returns = ", ".join(_show(ret, type_variable_name, bound) for ret in t.returns)
        return _show_function_with_tags(
            f"Function[{params} -> {returns}]",
            t.element_tags,
            type_variable_name,
            bound,
        )
    if isinstance(t, AnonymousTraitType):
        trait_bound = bound | frozenset(str(generic) for generic in t.generics)
        generics = f"[{', '.join(str(g) for g in t.generics)}]" if t.generics else ""
        requirements = tuple(
            _show_anonymous_trait_requirement(
                requirement,
                type_variable_name,
                trait_bound,
            )
            for requirement in t.requirements
        )
        if len(requirements) <= 1:
            body = requirements[0] if requirements else ""
            return f"trait{generics} => {body} end"
        body = "\n  ".join(requirements)
        return f"trait{generics} =>\n  {body}\nend"
    if isinstance(t, TaggedType):
        tags = " ".join(_show_tag(tag) for tag in sorted(t.tags))
        if t.exact:
            tags = f"[{tags}]"
        return f"{tags} {_show(t.inner, type_variable_name, bound)}"
    if isinstance(t, ExactType):
        return f"{_show(t.inner, type_variable_name, bound)} exact"
    if isinstance(t, AtomicType):
        return f"{_show(t.inner, type_variable_name, bound)} atomic"
    if isinstance(t, OverloadSetType):
        entries = ", ".join(
            _show_overload(overload, type_variable_name, bound)
            for overload in t.overloads
        )
        return f"OverloadSet[{entries}]"
    return type(t).__name__


def _show_anonymous_trait_requirement(
    requirement: AnonymousTraitRequirement,
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Render one structural-trait requirement with its local generic scope."""

    overload_bound = bound | frozenset(
        constraint.name for constraint in requirement.overload.generic_constraints
    )
    params = ", ".join(
        _show(param, type_variable_name, overload_bound)
        for param in requirement.overload.params
    )
    returns = ", ".join(
        _show(ret, type_variable_name, overload_bound)
        for ret in requirement.overload.returns
    )
    suffix = f" -> {returns}" if returns else ""
    return f"extend {requirement.name}({params}){suffix}"


def _show_overload(
    overload: Overload,
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Render an overload-set entry while respecting its generic binders."""

    overload_bound = bound | frozenset(
        constraint.name for constraint in overload.generic_constraints
    )
    params = ", ".join(
        _show(param, type_variable_name, overload_bound) for param in overload.params
    )
    returns = ", ".join(
        _show(ret, type_variable_name, overload_bound) for ret in overload.returns
    )
    return f"Function[{params} -> {returns}]"


def _show_collection_base(
    t: Type,
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Format collection base for type construction and display."""

    rendered = _show(t, type_variable_name, bound)
    if isinstance(normalize(t), (UnionType, IntersectionType)):
        return f"({rendered})"
    return rendered


def _show_function_with_tags(
    base: str,
    tags: frozenset[ElementTag],
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Format function with tags for type construction and display."""

    if not tags:
        return base
    rendered = ", ".join(
        _show_element_tag(tag, type_variable_name, bound) for tag in sorted(tags)
    )
    return f"{base}<{rendered}>"


def _show_element_tag(
    tag: ElementTag,
    type_variable_name: Callable[[str], str] | None,
    bound: frozenset[str],
) -> str:
    """Format element tag for type construction and display."""

    prefix = "!" if tag.absent else ""
    if not tag.args:
        return f"{prefix}{tag.name}"
    args = ", ".join(_show(arg, type_variable_name, bound) for arg in tag.args)
    return f"{prefix}{tag.name}[{args}]"


def _show_tag(tag: DataTag) -> str:
    """Format tag for type construction and display."""
    prefix = "#-" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"


def _tag(tag: TagSpec) -> DataTag:
    """Compute tag for type construction and display."""
    if isinstance(tag, DataTag):
        return tag
    absent = tag.startswith("!")
    name = tag[1:] if absent else tag
    return DataTag(name, absent=absent)


def _element_tag(tag: ElementTag | str) -> ElementTag:
    """Compute element tag for type construction and display."""
    if isinstance(tag, ElementTag):
        return tag
    absent = tag.startswith("!")
    name = tag[1:] if absent else tag
    return ElementTag(Symbol(name), absent=absent)
