"""Generic callable signatures, substitutions, and variance analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import field, fields, replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
from valiance.analysis.state.transformations import transform_overload_types

_transform_overload_types = transform_overload_types
from valiance.vtypes.structural import anonymous_trait_subject_name

_anonymous_trait_subject_name = anonymous_trait_subject_name


def _binder_scope_id(function: FunctionNode) -> int:
    """Return a stable-in-session identity for one function generic binder."""
    if function.generic_scope_id is not None:
        return function.generic_scope_id
    if function.location is not None:
        return 1_000_000 + function.location.offset
    return id(function)


def _generic_scope(
    generics: tuple[Symbol, ...],
    scope_id: int | None,
) -> T.TypeVarScope | None:
    """Build the identity allocator for a generic binder when one is assigned."""
    if not generics or scope_id is None:
        return None
    return T.TypeVarScope(scope_id, tuple(generic.text for generic in generics))
import valiance.analysis.contracts.where_clauses as static_where
from valiance.asts import (
    ASTNode,
    CallArgument,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ListLiteralNode,
    MatchNode,
    ReturnNode,
    TryNode,
    TypedNode,
)
from valiance.asts.nodes import (
    GetVariableNode,
    IfNode,
    SetVariableNode,
    SetVariablesNode,
)
from valiance.vtypes.symbols import Symbol

from .. import analyser as _core
from . import candidates as _calls
from ..support import analysis_utils as _utils



def _declared_params(node: FunctionNode) -> tuple[T.Type, ...]:
    """Return declared parameter types for signature transformations."""
    if node.params is None:
        return ()
    return tuple(param.typ or T.Any for param in node.params)


def _parameter_value_type(typ: T.Type) -> T.Type:
    """Remove parameter-only markers from a value type."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.NoVecType, T.ExactType)):
        return _parameter_value_type(typ.inner)
    return typ

def _genericize_overload(
    overload: T.Overload,
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> T.Overload:
    """Generalize overload during static analysis."""
    if not generics:
        return overload
    return transform_overload_types(
        overload,
        lambda typ: _genericize_type(typ, generics, scope),
    )

def _genericize_function_node(
    function: FunctionNode,
    generics: tuple[Symbol, ...],
) -> FunctionNode:
    """Generalize a function and assign one identity scope to its binder."""
    binder_id = _binder_scope_id(function)
    has_structural_generic = any(
        param.typ is not None and _contains_anonymous_trait(param.typ)
        for param in function.params or ()
    )
    # Anonymous structural traits still bind their subject by source name in the
    # structural solver. Keep that path unscoped until its substitution map is
    # migrated to TypeVarId in the next phase.
    scope_id = (
        binder_id
        if generics and not has_structural_generic
        else function.generic_scope_id
    )
    scope = _generic_scope(generics, scope_id)
    params = None
    if function.params is not None:
        params = tuple(
            replace(
                cast(FunctionParam, _genericize_ast_value(param, generics, scope)),
                inference_identity=(
                    param.inference_identity
                    if param.inference_identity is not None
                    else T.MetaVarId(binder_id, index)
                ),
            )
            for index, param in enumerate(function.params)
        )
    returns = None
    if function.returns is not None:
        returns = tuple(
            _parameter_value_type(_genericize_type(ret, generics, scope))
            for ret in function.returns
        )
    generic_constraints = tuple(
        None if bound is None else _genericize_type(bound, generics, scope)
        for bound in function.generic_constraints
    )
    return FunctionNode(
        generics=function.generics,
        generic_scope_id=scope_id,
        generic_variances=function.generic_variances,
        params=params,
        body=tuple(_genericize_ast_node(node, generics, scope) for node in function.body),
        returns=returns,
        where_clause=tuple(
            _genericize_ast_node(node, generics, scope) for node in function.where_clause
        ),
        element_tags=frozenset(
            _genericize_element_tags(function.element_tags, generics, scope)
        ),
        annotations=function.annotations,
        element_tags_explicit=function.element_tags_explicit,
        companion_tags_allowed=frozenset(
            _genericize_element_tags(function.companion_tags_allowed, generics, scope)
        ),
        generic_constraints=generic_constraints,
        location=function.location,
        object_friendly_receiver=function.object_friendly_receiver,
    )

def _contextualize_function_empty_returns(function: FunctionNode) -> FunctionNode:
    """Infer empty list literals that are syntactically returned by a function."""
    if not function.returns:
        return function
    body = _contextualize_return_block(function.body, function.returns)
    return function if body == function.body else replace(function, body=body)

def _contextualize_return_block(
    body: tuple[ASTNode, ...],
    returns: tuple[T.Type, ...],
) -> tuple[ASTNode, ...]:
    """Compute contextualize return block during static analysis."""
    nodes = tuple(_contextualize_explicit_return(node, returns) for node in body)
    if not nodes:
        return nodes
    if len(returns) == 1:
        final = _contextualize_return_expression(nodes[-1], returns[0])
        return (*nodes[:-1], final)
    if len(nodes) >= len(returns):
        prefix = nodes[: -len(returns)]
        suffix = tuple(
            _contextualize_return_expression(node, expected)
            for node, expected in zip(
                nodes[-len(returns) :],
                returns,
                strict=True,
            )
        )
        return prefix + suffix
    return nodes

def _contextualize_explicit_return(
    node: ASTNode,
    returns: tuple[T.Type, ...],
) -> ASTNode:
    """Compute contextualize explicit return during static analysis."""
    if (
        isinstance(node, ReturnNode)
        and node.explicit_values
        and len(node.values) == len(returns)
    ):
        return replace(
            node,
            values=tuple(
                _contextualize_return_block(expression, (expected,))
                for expression, expected in zip(node.values, returns, strict=True)
            ),
        )
    return node

def _contextualize_return_expression(node: ASTNode, expected: T.Type) -> ASTNode:
    """Compute contextualize return expression during static analysis."""
    if isinstance(node, ListLiteralNode) and not node.items and node.typ is None:
        inferred = _empty_list_return_type(expected)
        return node if inferred is None else replace(node, typ=inferred)
    if isinstance(node, IfNode):
        return replace(
            node,
            then_branch=_contextualize_return_block(node.then_branch, (expected,)),
            else_branch=_contextualize_return_block(node.else_branch, (expected,)),
        )
    if isinstance(node, MatchNode):
        return replace(
            node,
            cases=tuple(
                replace(
                    case,
                    body=_contextualize_return_block(case.body, (expected,)),
                )
                for case in node.cases
            ),
        )
    if isinstance(node, TryNode):
        return replace(
            node,
            body=_contextualize_return_block(node.body, (expected,)),
            handlers=tuple(
                replace(
                    handler,
                    body=_contextualize_return_block(handler.body, (expected,)),
                )
                for handler in node.handlers
            ),
        )
    return node

def _empty_list_return_type(expected: T.Type) -> T.Type | None:
    """Determine the type of empty list return during static analysis."""
    expected = T.normalize(expected)
    if isinstance(expected, (T.TaggedType, T.NoVecType)):
        return _empty_list_return_type(expected.inner)
    if isinstance(expected, (T.ListExactType, T.ListMinType, T.ListRuggedType)):
        return T.C(T.ListExactType, expected.base, expected.rank)
    if isinstance(expected, (T.ArrayExactType, T.ArrayMinType)):
        return T.C(T.ArrayExactType, expected.base, expected.rank)
    return None

def _substitute_rank_variables_in_ast(
    node: ASTNode,
    ranks: dict[str, int],
    types: dict[T.TypeVarKey, T.Type] | None = None,
    *,
    root: bool = True,
) -> ASTNode:
    """Substitute solved static bindings in AST types without capture."""
    active_ranks = ranks
    active_types = types or {}
    if isinstance(node, FunctionNode) and not root:
        declared = _declared_params(node) + (node.returns or ())
        shadowed_ranks = static_where.rank_variable_names(declared)
        active_ranks = {
            name: value for name, value in ranks.items() if name not in shadowed_ranks
        }
        shadowed_types = {generic.text for generic in node.generics}
        active_types = {
            name: value
            for name, value in active_types.items()
            if name not in shadowed_types
        }
        if not active_ranks and not active_types:
            return node
    updates: dict[str, object] = {}
    for item in fields(node):
        value = getattr(node, item.name)
        updated = _substitute_rank_variables_in_ast_value(
            value, active_ranks, active_types
        )
        if updated is not value:
            updates[item.name] = updated
    return replace(node, **updates) if updates else node

def _substitute_rank_variables_in_ast_value(
    value: object,
    ranks: dict[str, int],
    types: dict[T.TypeVarKey, T.Type],
) -> object:
    """Substitute static bindings through one recursively nested AST value."""
    if isinstance(value, T.Type):
        return static_where.substitute_static_type(value, ranks=ranks, types=types)
    if isinstance(value, FunctionParam):
        typ = (
            None
            if value.typ is None
            else static_where.substitute_static_type(
                value.typ, ranks=ranks, types=types
            )
        )
        default = tuple(
            cast(
                ASTNode,
                _substitute_rank_variables_in_ast(node, ranks, types, root=False),
            )
            for node in value.default
        )
        if typ is value.typ and default == value.default:
            return value
        return replace(
            value,
            typ=typ,
            default=default,
            inference_identity=value.inference_identity,
        )
    if isinstance(value, CallArgument):
        argument = tuple(
            cast(
                ASTNode,
                _substitute_rank_variables_in_ast(node, ranks, types, root=False),
            )
            for node in value.value
        )
        if argument == value.value:
            return value
        return replace(value, value=argument)
    if isinstance(value, ASTNode):
        return _substitute_rank_variables_in_ast(value, ranks, types, root=False)
    if isinstance(value, tuple):
        return tuple(
            _substitute_rank_variables_in_ast_value(item, ranks, types)
            for item in value
        )
    if isinstance(value, frozenset):
        return frozenset(
            _substitute_rank_variables_in_ast_value(item, ranks, types)
            for item in value
        )
    return value

def _genericize_ast_node(
    node: ASTNode,
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> ASTNode:
    """Generalize AST node during static analysis."""
    if isinstance(node, FunctionNode) and node.generics:
        shadowed = {generic.text for generic in node.generics}
        generics = tuple(
            generic for generic in generics if generic.text not in shadowed
        )
        if not generics:
            return node
    updates: dict[str, object] = {}
    for item in fields(node):
        value = getattr(node, item.name)
        updated = _genericize_ast_value(value, generics, scope)
        if updated is not value:
            updates[item.name] = updated
    return replace(node, **updates) if updates else node

def _genericize_ast_value(
    value: object,
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> object:
    """Generalize AST value during static analysis."""
    if isinstance(value, T.Type):
        return _genericize_type(value, generics, scope)
    if isinstance(value, FunctionParam):
        typ = None if value.typ is None else _genericize_type(value.typ, generics, scope)
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics, scope))
            for node in value.default
        )
        if typ is value.typ and default == value.default:
            return value
        return replace(value, typ=typ, default=default)
    if isinstance(value, CallArgument):
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics, scope)) for node in value.value
        )
        if default == value.value:
            return value
        return replace(value, value=default)
    if isinstance(value, ASTNode):
        return _genericize_ast_node(value, generics, scope)
    if isinstance(value, tuple):
        return tuple(_genericize_ast_value(item, generics, scope) for item in value)
    return value

def _genericize_attribute(
    attribute: T.ObjectAttribute,
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> T.ObjectAttribute:
    """Generalize attribute during static analysis."""
    return T.ObjectAttribute(
        attribute.name,
        _genericize_type(attribute.typ, generics, scope),
        attribute.access,
        attribute.has_default,
    )

def _genericize_requirement(
    requirement: T.TraitRequirement,
    generics: tuple[Symbol, ...],
) -> T.TraitRequirement:
    """Generalize requirement during static analysis."""
    return T.TraitRequirement(
        requirement.name,
        transform_overload_types(
            requirement.overload,
            lambda typ: _genericize_type(typ, generics),
            element_tags=frozenset(
                _genericize_element_tags(
                    requirement.overload.element_tags,
                    generics,
                )
            ),
        ),
    )


def _transform_type_children(
    typ: T.Type,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: Callable[
        [frozenset[T.ElementTag]],
        frozenset[T.ElementTag],
    ] = lambda tags: tags,
) -> T.Type:
    """Compute transform type children during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(transform(arg) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(transform(item) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(transform(item) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(transform(item) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(T.TupleTypeItem(transform(item.typ), item.repeated) for item in typ.items)
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            transform(typ.base),
            *(T.Field(field.name, transform(field.typ)) for field in typ.fields),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), transform(typ.base), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(None, None, element_tags(typ.element_tags))
        return T.Fn(
            tuple(transform(param) for param in typ.params),
            tuple(transform(ret) for ret in typ.returns),
            element_tags(typ.element_tags),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(transform(typ.inner), *typ.tags, exact=typ.exact)
    if isinstance(typ, T.NoVecType):
        return T.NoVec(transform(typ.inner))
    if isinstance(typ, T.ExactType):
        return T.Exact(transform(typ.inner))
    return typ

def _genericize_type(
    typ: T.Type,
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> T.Type:
    """Generalize type during static analysis."""
    names = {generic.text for generic in generics}
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in names:
            return scope.variable(typ.name.text) if scope is not None else T.V(typ.name.text)
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _genericize_overload(requirement.overload, generics, scope),
                )
                for requirement in typ.requirements
            ),
        )
    return _transform_type_children(
        typ,
        lambda child: _genericize_type(child, generics, scope),
        element_tags=lambda tags: _genericize_element_tags(tags, generics, scope),
    )

def _anonymous_trait_overloads(*types: T.Type) -> tuple[tuple[Symbol, T.Overload], ...]:
    """Collect the overloads for anonymous trait during static analysis."""
    overloads: list[tuple[Symbol, T.Overload]] = []
    for typ in types:
        _collect_anonymous_trait_overloads(T.normalize(typ), overloads)
    return tuple(overloads)

def _anonymous_trait_subject_view(typ: T.Type) -> T.Type:
    """Build the view of anonymous trait subject during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AnonymousTraitType):
        subject = anonymous_trait_subject_name(typ)
        if subject is not None:
            return T.V(subject)
        return typ
    return _transform_type_children(typ, _anonymous_trait_subject_view)


def _first_type_var_name(typ: T.Type) -> str | None:
    """Return the canonical name for first type var during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return typ.name
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            name = _first_type_var_name(arg)
            if name is not None:
                return name
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            name = _first_type_var_name(item.typ)
            if name is not None:
                return name
    if isinstance(typ, T.RowType):
        name = _first_type_var_name(typ.base)
        if name is not None:
            return name
        for field in typ.fields:
            name = _first_type_var_name(field.typ)
            if name is not None:
                return name
    if isinstance(typ, T.CollectionType):
        return _first_type_var_name(typ.base)
    if isinstance(typ, T.FunctionType):
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
    if isinstance(typ, T.AnonymousTraitType):
        return anonymous_trait_subject_name(typ)
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _first_type_var_name(typ.inner)
    return None

def _contains_anonymous_trait(typ: T.Type) -> bool:
    """Return whether the value contains anonymous trait."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AnonymousTraitType):
        return True
    if isinstance(typ, T.NominalType):
        return any(_contains_anonymous_trait(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_anonymous_trait(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_anonymous_trait(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_anonymous_trait(item.typ) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_anonymous_trait(typ.base) or any(
            _contains_anonymous_trait(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.CollectionType):
        return _contains_anonymous_trait(typ.base)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return False
        return any(_contains_anonymous_trait(item) for item in typ.params) or any(
            _contains_anonymous_trait(item) for item in typ.returns
        )
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _contains_anonymous_trait(typ.inner)
    return False

def _collect_anonymous_trait_overloads(
    typ: T.Type,
    overloads: list[tuple[Symbol, T.Overload]],
) -> None:
    """Collect anonymous trait overloads during static analysis."""
    if isinstance(typ, T.AnonymousTraitType):
        overloads.extend(
            (requirement.name, requirement.overload) for requirement in typ.requirements
        )
        for requirement in typ.requirements:
            for item in requirement.overload.params + requirement.overload.returns:
                _collect_anonymous_trait_overloads(T.normalize(item), overloads)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _collect_anonymous_trait_overloads(arg, overloads)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _collect_anonymous_trait_overloads(item.typ, overloads)
        return
    if isinstance(typ, T.RowType):
        _collect_anonymous_trait_overloads(typ.base, overloads)
        for field in typ.fields:
            _collect_anonymous_trait_overloads(field.typ, overloads)
        return
    if isinstance(typ, T.CollectionType):
        _collect_anonymous_trait_overloads(typ.base, overloads)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return
        for item in typ.params + typ.returns:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        _collect_anonymous_trait_overloads(typ.inner, overloads)

def _genericize_element_tags(
    tags: frozenset[T.ElementTag],
    generics: tuple[Symbol, ...],
    scope: T.TypeVarScope | None = None,
) -> tuple[T.ElementTag, ...]:
    """Generalize element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_genericize_type(arg, generics, scope) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )

def _declared_or_inferred_variance(
    generics: tuple[Symbol, ...],
    explicit: tuple[Symbol | None, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
    ctx: T.Context | None = None,
) -> tuple[T.Variance, ...]:
    """Compute declared or inferred variance during static analysis."""
    inferred = _infer_generic_variance(generics, attributes, requirements, ctx)
    if len(explicit) != len(generics):
        return inferred
    return tuple(
        _variance_from_marker(marker) if marker is not None else inferred[index]
        for index, marker in enumerate(explicit)
    )

def _variance_from_marker(marker: Symbol) -> T.Variance:
    """Compute variance from marker during static analysis."""
    if marker.text in {"any", "covariant"}:
        return T.Variance.COVARIANT
    if marker.text in {"above", "contravariant"}:
        return T.Variance.CONTRAVARIANT
    return T.Variance.INVARIANT

def _infer_generic_variance(
    generics: tuple[Symbol, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
    ctx: T.Context | None = None,
) -> tuple[T.Variance, ...]:
    """Infer generic variance during static analysis."""
    ctx = ctx or T.Context()
    usage = {generic.text: [False, False] for generic in generics}
    for attribute in attributes:
        _record_variance_use(attribute.typ, +1, usage, ctx)
        if attribute.access.text == "public":
            _record_variance_use(attribute.typ, -1, usage, ctx)
    for requirement in requirements:
        for param in requirement.overload.params:
            _record_variance_use(param, -1, usage, ctx)
        for ret in requirement.overload.returns:
            _record_variance_use(ret, +1, usage, ctx)
    variances: list[T.Variance] = []
    for generic in generics:
        positive, negative = usage[generic.text]
        if positive and not negative:
            variances.append(T.Variance.COVARIANT)
        elif negative and not positive:
            variances.append(T.Variance.CONTRAVARIANT)
        else:
            variances.append(T.Variance.INVARIANT)
    return tuple(variances)

def _record_variance_use(
    typ: T.Type,
    polarity: int,
    usage: dict[str, list[bool]],
    ctx: T.Context,
) -> None:
    """Record variance use during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        if typ.name in usage:
            usage[typ.name][0 if polarity > 0 else 1] = True
        return
    if isinstance(typ, T.NominalType):
        variances = ctx.variance_for(typ.name, len(typ.args))
        for arg, variance in zip(typ.args, variances, strict=True):
            if variance is T.Variance.INVARIANT:
                _record_variance_use(arg, +1, usage, ctx)
                _record_variance_use(arg, -1, usage, ctx)
            elif variance is T.Variance.CONTRAVARIANT:
                _record_variance_use(arg, -polarity, usage, ctx)
            else:
                _record_variance_use(arg, polarity, usage, ctx)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _record_variance_use(item, polarity, usage, ctx)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _record_variance_use(item, polarity, usage, ctx)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _record_variance_use(item.typ, polarity, usage, ctx)
        return
    if isinstance(typ, T.RowType):
        _record_variance_use(typ.base, polarity, usage, ctx)
        for field in typ.fields:
            _record_variance_use(field.typ, polarity, usage, ctx)
        return
    if isinstance(typ, T.CollectionType):
        _record_variance_use(typ.base, polarity, usage, ctx)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            for tag in typ.element_tags:
                for arg in tag.args:
                    _record_variance_use(arg, polarity, usage, ctx)
            return
        for param in typ.params:
            _record_variance_use(param, -polarity, usage, ctx)
        for ret in typ.returns:
            _record_variance_use(ret, polarity, usage, ctx)
        for tag in typ.element_tags:
            for arg in tag.args:
                _record_variance_use(arg, polarity, usage, ctx)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for param in requirement.overload.params:
                _record_variance_use(param, -polarity, usage, ctx)
            for ret in requirement.overload.returns:
                _record_variance_use(ret, polarity, usage, ctx)
        return
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        _record_variance_use(typ.inner, polarity, usage, ctx)

def _anonymous_type_var(branch: _core.AnalysisBranch, offset: int) -> T.Type:
    """Compute anonymous type var during static analysis."""
    taken = _anonymous_type_indices(
        *branch.stack.items,
        *branch.inputs,
        *branch.cycle_params,
        *(typ for _, typ in branch.variables.visible_items()),
    )
    start = max(taken, default=0)
    return T.M(
        f"@{start + offset}",
        T.MetaVarId(branch.origin, start + offset),
    )

def _anonymous_type_indices(*types: T.Type) -> set[int]:
    """Compute anonymous type indices during static analysis."""
    indices: set[int] = set()
    for typ in types:
        _collect_anonymous_type_indices(T.normalize(typ), indices)
    return indices

def _collect_anonymous_type_indices(typ: T.Type, indices: set[int]) -> None:
    """Collect anonymous type indices during static analysis."""
    if isinstance(typ, T.MetaVarType) and typ.name.startswith("@"):
        suffix = typ.name[1:]
        if suffix.isdecimal():
            indices.add(int(suffix))
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _collect_anonymous_type_indices(arg, indices)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _collect_anonymous_type_indices(item.typ, indices)
        return
    if isinstance(typ, T.RowType):
        _collect_anonymous_type_indices(typ.base, indices)
        for row_field in typ.fields:
            _collect_anonymous_type_indices(row_field.typ, indices)
        return
    if isinstance(typ, T.CollectionType):
        _collect_anonymous_type_indices(typ.base, indices)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return
        for item in typ.params + typ.returns:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in requirement.overload.params + requirement.overload.returns:
                _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        _collect_anonymous_type_indices(typ.inner, indices)
