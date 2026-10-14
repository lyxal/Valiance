"""Shared structural-type inspection helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import valiance.vtypes as T


def anonymous_trait_subject_name(target: T.AnonymousTraitType) -> str | None:
    """Return the subject generic name for an anonymous structural trait."""
    if target.generics:
        return target.generics[0].text
    for requirement in target.requirements:
        for item in requirement.overload.params + requirement.overload.returns:
            name = first_type_var_name(item)
            if name is not None:
                return name
    return None


def anonymous_trait_requirement_signature(
    target: T.AnonymousTraitType,
    requirement: T.AnonymousTraitRequirement,
    receiver: T.Type,
) -> T.Overload:
    """Include the implicit receiver for member-style inline requirements.

    Colon-prefixed operands and symbolic operator requirements use explicit
    stack operands; named member requirements use a method-style receiver.
    This distinction is retained during type substitution, so specializing
    ``T`` does not accidentally change a structural call's arity.
    """
    if not requirement.implicit_receiver:
        return requirement.overload
    params = requirement.overload.params
    return replace(
        requirement.overload,
        params=(receiver, *params),
        param_names=(None, *requirement.overload.param_names),
        is_structural_requirement=True,
    )


def first_type_var_name(typ: T.Type) -> str | None:
    """Return the first nested type-variable name, if any."""
    for nested in nested_types(typ):
        if isinstance(nested, T.VarType):
            return nested.name
    return None


def nested_types(typ: T.Type) -> Iterator[T.Type]:
    """Yield a normalized type and its recursively nested type children."""
    typ = T.normalize(typ)
    yield typ
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        yield from nested_types(typ.inner)
    elif isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from nested_types(arg)
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from nested_types(item)
    elif isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from nested_types(item)
    elif isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from nested_types(item.typ)
    elif isinstance(typ, T.RowType):
        yield from nested_types(typ.base)
        for field in typ.fields:
            yield from nested_types(field.typ)
    elif isinstance(typ, T.CollectionType):
        yield from nested_types(typ.base)
    elif isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from nested_types(item)
    elif isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in requirement.overload.params + requirement.overload.returns:
                yield from nested_types(item)
