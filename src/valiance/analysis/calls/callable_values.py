"""Function typing, genericisation, and callable-shape helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import field, fields, replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
from valiance.analysis.support.function_shapes import function_param_names_for_overload

_function_param_names_for_overload = function_param_names_for_overload
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
    """Determine the parameters for declared during static analysis."""
    if node.params is None:
        return ()
    return _params_to_types(node.params)


def _atomic_type_var_names(typ: T.Type) -> frozenset[str]:
    """Collect generics whose own rank determines an atomic position."""
    typ = T.normalize(typ)
    if isinstance(typ, T.ExactType):
        return _atomic_subject_type_var_names(typ.inner)
    if isinstance(typ, T.NominalType):
        children = typ.args
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        children = typ.items
    elif isinstance(typ, T.TupleType):
        children = typ.params
    elif isinstance(typ, T.VariadicTupleType):
        children = tuple(item.typ for item in typ.items)
    elif isinstance(typ, T.RowType):
        children = (typ.base, *(field.typ for field in typ.fields))
    elif isinstance(typ, T.CollectionType):
        children = (typ.base,)
    elif isinstance(typ, T.FunctionType):
        # Markers in a nested callable signature constrain calls through that
        # value, not the outer function's generic arguments.
        children = ()
    elif isinstance(typ, (T.TaggedType, T.NoVecType)):
        children = (typ.inner,)
    else:
        children = ()
    names: set[str] = set()
    for child in children:
        names.update(_atomic_type_var_names(child))
    return frozenset(names)


def _atomic_subject_type_var_names(typ: T.Type) -> frozenset[str]:
    """Collect variables whose substitution can change subject rank."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return frozenset((typ.name,))
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _atomic_subject_type_var_names(typ.inner)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        names: set[str] = set()
        for item in typ.items:
            names.update(_atomic_subject_type_var_names(item))
        return frozenset(names)
    # Nominals, tuples, rows, and callable values are scalar independently of
    # their type arguments. Collections are never scalar at the marked level.
    return frozenset()


def _atomic_parameter_type_vars(
    params: tuple[T.Type, ...],
) -> frozenset[str]:
    """Collect scalar generic guarantees declared by function parameters."""
    names: set[str] = set()
    for param in params:
        names.update(_atomic_type_var_names(param))
    return frozenset(names)


def _parameter_value_type(typ: T.Type) -> T.Type:
    """Return the type visible inside a function body for one parameter."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.NoVecType, T.ExactType)):
        return _parameter_value_type(typ.inner)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_parameter_value_type(arg) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_parameter_value_type(item) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_parameter_value_type(item) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_parameter_value_type(item) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(
                    _parameter_value_type(item.typ),
                    item.repeated,
                )
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _parameter_value_type(typ.base),
            *(
                T.Field(field.name, _parameter_value_type(field.typ))
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _parameter_value_type(typ.base), typ.rank)
    if isinstance(typ, T.TaggedType):
        return T.Tagged(
            _parameter_value_type(typ.inner),
            *typ.tags,
            exact=typ.exact,
        )
    # Markers inside a callable value describe calls through that value, not
    # the outer function parameter, so preserve the callable signature intact.
    return typ


def _restore_parameter_markers(
    declared: tuple[T.Type, ...],
    inferred: tuple[T.Type, ...],
) -> tuple[T.Type, ...]:
    """Reapply call-policy markers after analysing parameter values."""
    if len(declared) > len(inferred):
        return inferred
    offset = len(inferred) - len(declared)
    restored = tuple(
        _restore_type_markers(expected, actual)
        for expected, actual in zip(declared, inferred[offset:], strict=True)
    )
    return inferred[:offset] + restored


def _restore_type_markers(declared: T.Type, inferred: T.Type) -> T.Type:
    """Overlay parameter-only markers from ``declared`` onto ``inferred``."""
    declared = T.normalize(declared)
    inferred = T.normalize(inferred)
    if isinstance(declared, T.NoVecType):
        return T.NoVec(_restore_type_markers(declared.inner, inferred))
    if isinstance(declared, T.ExactType):
        return T.Exact(_restore_type_markers(declared.inner, inferred))
    if isinstance(declared, T.NominalType) and isinstance(inferred, T.NominalType):
        if declared.name == inferred.name and len(declared.args) == len(inferred.args):
            return T.N(
                inferred.name,
                *(
                    _restore_type_markers(expected, actual)
                    for expected, actual in zip(
                        declared.args,
                        inferred.args,
                        strict=True,
                    )
                ),
            )
    if isinstance(declared, T.TupleType) and isinstance(inferred, T.TupleType):
        if len(declared.params) == len(inferred.params):
            return T.Tup(
                *(
                    _restore_type_markers(expected, actual)
                    for expected, actual in zip(
                        declared.params,
                        inferred.params,
                        strict=True,
                    )
                )
            )
    if isinstance(declared, T.VariadicTupleType) and isinstance(
        inferred,
        T.VariadicTupleType,
    ):
        if len(declared.items) == len(inferred.items):
            return T.TupVariadic(
                *(
                    T.TupleTypeItem(
                        _restore_type_markers(expected.typ, actual.typ),
                        actual.repeated,
                    )
                    for expected, actual in zip(
                        declared.items,
                        inferred.items,
                        strict=True,
                    )
                )
            )
    if isinstance(declared, T.RowType) and isinstance(inferred, T.RowType):
        declared_fields = {field.name: field.typ for field in declared.fields}
        return T.Row(
            _restore_type_markers(declared.base, inferred.base),
            *(
                T.Field(
                    field.name,
                    _restore_type_markers(
                        declared_fields.get(field.name, field.typ),
                        field.typ,
                    ),
                )
                for field in inferred.fields
            ),
        )
    if isinstance(declared, T.CollectionType) and isinstance(
        inferred,
        T.CollectionType,
    ):
        if type(declared) is type(inferred) and declared.rank == inferred.rank:
            return T.C(
                type(inferred),
                _restore_type_markers(declared.base, inferred.base),
                inferred.rank,
            )
    if isinstance(declared, T.TaggedType) and isinstance(inferred, T.TaggedType):
        return T.Tagged(
            _restore_type_markers(declared.inner, inferred.inner),
            *inferred.tags,
            exact=declared.exact,
        )
    if isinstance(declared, (T.UnionType, T.IntersectionType)) and isinstance(
        inferred,
        type(declared),
    ):
        unmatched = list(declared.items)
        restored_items: list[T.Type] = []
        for actual in inferred.items:
            match_index = next(
                (
                    index
                    for index, expected in enumerate(unmatched)
                    if T.same(_parameter_value_type(expected), actual)
                ),
                None,
            )
            if match_index is None:
                restored_items.append(actual)
                continue
            restored_items.append(
                _restore_type_markers(unmatched.pop(match_index), actual)
            )
        constructor = T.U if isinstance(declared, T.UnionType) else T.I
        return constructor(*restored_items)
    return inferred


def _is_result_type(typ: T.Type) -> bool:
    """Return whether ``typ`` is an explicit Result type."""
    typ = T.normalize(typ)
    return (
        isinstance(typ, T.NominalType)
        and typ.name == Symbol("Result")
        and len(typ.args) == 2
    )


def _function_overload(
    node: FunctionNode,
    *,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    where_clause: tuple[ASTNode, ...] = (),
    element_tags: frozenset[T.ElementTag] | None = None,
    call_site_body: object | None = None,
) -> T.Overload:
    """Build or resolve the overload for function during static analysis."""
    return T.Overload(
        params=params,
        returns=returns,
        generic_params=tuple(generic.text for generic in node.generics),
        where_clause=where_clause,
        param_names=function_param_names_for_overload(node, params),
        call_site_body=call_site_body,
        element_tags=frozenset() if element_tags is None else element_tags,
        annotation_error=annotation_hooks.annotation_error_message(node.annotations),
        annotation_warning=annotation_hooks.annotation_warning_message(
            node.annotations
        ),
        param_defaults=_function_param_defaults_for_overload(node, params),
    )


def _fully_typed_overload(node: FunctionNode) -> T.Overload | None:
    """Build or resolve the overload for fully typed during static analysis."""
    if node.params is None or node.returns is None:
        return None
    if any(param.typ is None for param in node.params):
        return None
    params = tuple(param.typ for param in node.params if param.typ is not None)
    return _function_overload(
        node,
        params=params,
        returns=node.returns,
        where_clause=node.where_clause,
        element_tags=node.element_tags,
    )


def _validate_define_niladic_name(name: Symbol, overload: T.Overload) -> bool:
    """Return whether a niladic definition name is valid."""
    is_named_nilad = name.text.startswith("\\")
    is_inferred_nilad = len(overload.params) == 0
    return is_named_nilad == is_inferred_nilad


def _body_references_element(body: tuple[ASTNode, ...], name: Symbol) -> bool:
    """Return the Boolean result of body references element during static analysis."""
    return any(_node_references_element(node, name) for node in body)


def _node_references_element(node: ASTNode, name: Symbol) -> bool:
    """Return the Boolean result of node references element during static analysis."""
    if isinstance(node, ElementNode) and node.name == name:
        return True
    for item in fields(node):
        value = getattr(node, item.name)
        if isinstance(value, ASTNode):
            if _node_references_element(value, name):
                return True
        elif isinstance(value, tuple) and _tuple_references_element(value, name):
            return True
    return False


def _tuple_references_element(value: tuple[object, ...], name: Symbol) -> bool:
    """Return the Boolean result of tuple references element during static analysis."""
    for item in value:
        if isinstance(item, ASTNode) and _node_references_element(item, name):
            return True
        if isinstance(item, tuple) and _tuple_references_element(item, name):
            return True
    return False


def _is_call_site_checked_param(typ: T.Type | None) -> bool:
    """Return whether the value is call site checked param."""
    if typ is None:
        return False
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name == Symbol("Function") and not typ.args
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return True
        return any(_is_call_site_checked_type(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return True
    return _is_call_site_checked_type(typ)


def _is_call_site_checked_type(typ: T.Type) -> bool:
    """Return whether the value is call site checked type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return any(_is_call_site_checked_type(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_is_call_site_checked_type(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_is_call_site_checked_type(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return True
    if isinstance(typ, T.RowType):
        return _is_call_site_checked_type(typ.base) or any(
            _is_call_site_checked_type(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.CollectionType):
        return _is_call_site_checked_type(typ.base)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return True
        return any(
            _is_call_site_checked_type(item) for item in typ.params + typ.returns
        )
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _is_call_site_checked_type(typ.inner)
    return False


def _call_site_substituted_params(
    params: tuple[FunctionParam, ...],
    actuals: tuple[T.Type, ...],
    ctx: T.Context,
) -> tuple[FunctionParam, ...] | None:
    """Determine the parameters for call site substituted during static analysis."""
    if len(params) != len(actuals):
        return None
    substituted: list[FunctionParam] = []
    for param, actual in zip(params, actuals, strict=True):
        typ = param.typ
        if typ is None:
            inferred = _utils._param_type(param, len(substituted))
            identity = (
                inferred.meta_identity
                if isinstance(inferred, T.MetaVarType)
                else param.inference_identity
            )
            substituted.append(
                FunctionParam(param.name, actual, param.default, identity)
            )
            continue
        if not _call_site_placeholder_accepts(typ, actual, ctx):
            return None
        substituted.append(
            FunctionParam(
                param.name,
                _call_site_substitute_type(typ, actual),
                param.default,
                param.inference_identity,
            )
        )
    return tuple(substituted)


def _call_site_placeholder_accepts(
    declared: T.Type,
    actual: T.Type,
    ctx: T.Context,
) -> bool:
    """Return whether a call-site placeholder accepts an actual type."""
    declared = T.normalize(declared)
    if _is_bare_function_type(declared):
        return isinstance(T.normalize(actual), (T.FunctionType, T.OverloadSetType))
    return T.compatible(actual, declared, ctx)


def _call_site_substitute_type(declared: T.Type, actual: T.Type) -> T.Type:
    """Determine the type of call site substitute during static analysis."""
    declared = T.normalize(declared)
    if _is_bare_function_type(declared) or isinstance(declared, T.VariadicTupleType):
        return actual
    return declared


def _is_bare_function_type(typ: T.Type) -> bool:
    """Return whether the value is bare function type."""
    typ = T.normalize(typ)
    return (
        isinstance(typ, T.FunctionType) and typ.params is None and typ.returns is None
    )




def _function_param_defaults_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[tuple[object, ...] | None, ...]:
    """Return parameter defaults aligned with an overload."""
    if node.params is None:
        return (None,) * len(inputs)
    defaults = tuple(param.default or None for param in node.params)
    if len(defaults) < len(inputs):
        return (None,) * (len(inputs) - len(defaults)) + defaults
    return defaults


def _contains_rank_var(types: tuple[T.Type, ...]) -> bool:
    """Return whether the value contains rank var."""
    return any(_type_contains_rank_var(typ) for typ in types)


def _type_contains_rank_var(typ: T.Type) -> bool:
    """Return the Boolean result of type contains rank var during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.CollectionType):
        return isinstance(typ.rank, T.RankVariable) or _type_contains_rank_var(typ.base)
    if isinstance(typ, T.NominalType):
        return any(_type_contains_rank_var(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_type_contains_rank_var(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_type_contains_rank_var(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_type_contains_rank_var(item.typ) for item in typ.items)
    if isinstance(typ, T.FunctionType):
        return _contains_rank_var(typ.params) or _contains_rank_var(typ.returns)
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _type_contains_rank_var(typ.inner)
    return False


def _contains_type_var(typ: T.Type) -> bool:
    """Return whether the value contains type var."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return True
    if isinstance(typ, T.CollectionType):
        return _contains_type_var(typ.base)
    if isinstance(typ, T.NominalType):
        return any(_contains_type_var(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_type_var(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_type_var(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_type_var(item.typ) for item in typ.items)
    if isinstance(typ, T.FunctionType):
        return any(_contains_type_var(item) for item in typ.params or ()) or any(
            _contains_type_var(item) for item in typ.returns or ()
        )
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _contains_type_var(typ.inner)
    return False


def _contains_named_type_var(typ: T.Type, name: str) -> bool:
    """Return whether the value contains named type var."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return typ.name == name
    if isinstance(typ, T.CollectionType):
        return _contains_named_type_var(typ.base, name)
    if isinstance(typ, T.NominalType):
        return any(_contains_named_type_var(arg, name) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_named_type_var(item, name) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_named_type_var(item, name) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_named_type_var(item.typ, name) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_named_type_var(typ.base, name) or any(
            _contains_named_type_var(field.typ, name) for field in typ.fields
        )
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return _element_tags_contain_named_type_var(typ.element_tags, name)
        return any(
            _contains_named_type_var(item, name) for item in typ.params + typ.returns
        ) or _element_tags_contain_named_type_var(typ.element_tags, name)
    if isinstance(typ, T.AnonymousTraitType):
        return any(
            _contains_named_type_var(item, name)
            for requirement in typ.requirements
            for item in requirement.overload.params + requirement.overload.returns
        )
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _contains_named_type_var(typ.inner, name)
    return False


def _element_tags_contain_named_type_var(
    tags: frozenset[T.ElementTag],
    name: str,
) -> bool:
    """Return whether element tags contain a named type variable."""
    return any(_contains_named_type_var(arg, name) for tag in tags for arg in tag.args)


def _static_body_variable_names(node: FunctionNode) -> tuple[Symbol, ...]:
    """Collect numeric compile-time values exposed inside a function body."""
    params = _declared_params(node)
    names = static_where.static_parameter_names(
        params=params,
        returns=node.returns or (),
        param_names=function_param_names_for_overload(node, params),
        clause=node.where_clause,
    )
    return tuple(Symbol(name) for name in names)


def _params_to_types(params: tuple[FunctionParam, ...]) -> tuple[T.Type, ...]:
    """Determine the types used for params to during static analysis."""
    return tuple(_utils._param_type(param, index) for index, param in enumerate(params))


def _function_capture_source(
    outer: _core.AnalysisBranch,
    *,
    allow_top_level_assignments: bool = False,
) -> _core.BranchVariables | None:
    """Return bindings whose types are available inside a function body."""
    if outer.input_mode is not _core.InputMode.TOP_LEVEL:
        return outer.variables
    if allow_top_level_assignments:
        return outer.variables
    return None


def _top_level_capture_nodes(
    outer: _core.AnalysisBranch,
    node: FunctionNode,
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture nodes during static analysis."""
    if outer.input_mode is not _core.InputMode.TOP_LEVEL:
        return ()
    visible = set(outer.variables.visible_names())
    if not visible:
        return ()
    return _top_level_capture_reads_in_function(node, visible, frozenset())


def _top_level_capture_reads_in_function(
    node: FunctionNode,
    visible: set[Symbol],
    inherited_bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in function during static analysis."""
    bound = inherited_bound | _function_bound_variable_names(node)
    return _top_level_capture_reads_in_nodes(node.body, visible, bound)


def _top_level_capture_reads_in_nodes(
    nodes: tuple[ASTNode, ...],
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in nodes during static analysis."""
    reads: list[GetVariableNode] = []
    for node in nodes:
        if isinstance(node, GetVariableNode):
            if node.name in visible and node.name not in bound:
                reads.append(node)
            continue
        if isinstance(node, FunctionNode):
            reads.extend(
                _top_level_capture_reads_in_function(
                    node,
                    visible,
                    bound,
                )
            )
            continue
        for item in fields(node):
            reads.extend(
                _top_level_capture_reads_in_value(
                    getattr(node, item.name),
                    visible,
                    bound,
                )
            )
    return tuple(reads)


def _top_level_capture_reads_in_value(
    value: object,
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in value during static analysis."""
    if isinstance(value, FunctionNode):
        return _top_level_capture_reads_in_function(value, visible, bound)
    if isinstance(value, ASTNode):
        return _top_level_capture_reads_in_nodes((value,), visible, bound)
    if isinstance(value, tuple):
        reads: list[GetVariableNode] = []
        for item in value:
            reads.extend(
                _top_level_capture_reads_in_value(item, visible, bound)
            )
        return tuple(reads)
    return ()


def _function_bound_variable_names(node: FunctionNode) -> frozenset[Symbol]:
    """Collect the names for function bound variable during static analysis."""
    names = {param.name for param in node.params or () if param.name is not None}
    names.update(
        assigned.name for assigned in node.body if isinstance(assigned, SetVariableNode)
    )
    for assigned in node.body:
        if isinstance(assigned, SetVariablesNode):
            names.update(target.name for target in assigned.targets)
    return frozenset(names)


def _parameter_write_nodes(
    node: FunctionNode,
) -> tuple[tuple[ASTNode, Symbol], ...]:
    """Return writes that target a parameter of this or an enclosing function."""
    parameters = frozenset(
        param.name for param in node.params or () if param.name is not None
    )
    return _parameter_writes_in_nodes(node.body, parameters)


def _parameter_writes_in_nodes(
    nodes: tuple[ASTNode, ...],
    parameters: frozenset[Symbol],
) -> tuple[tuple[ASTNode, Symbol], ...]:
    """Find parameter writes recursively through control flow and closures."""
    writes: list[tuple[ASTNode, Symbol]] = []
    for node in nodes:
        if isinstance(node, SetVariableNode):
            if node.name in parameters:
                writes.append((node, node.name))
            continue
        if isinstance(node, SetVariablesNode):
            writes.extend(
                (node, target.name)
                for target in node.targets
                if target.name in parameters
            )
            continue
        if isinstance(node, FunctionNode):
            nested = frozenset(
                param.name
                for param in node.params or ()
                if param.name is not None
            )
            writes.extend(_parameter_writes_in_nodes(node.body, nested))
            continue
        for item in fields(node):
            writes.extend(
                _parameter_writes_in_value(getattr(node, item.name), parameters)
            )
    return tuple(writes)


def _parameter_writes_in_value(
    value: object,
    parameters: frozenset[Symbol],
) -> tuple[tuple[ASTNode, Symbol], ...]:
    """Find parameter writes in a recursively nested AST field value."""
    if isinstance(value, FunctionNode):
        nested = frozenset(
            param.name for param in value.params or () if param.name is not None
        )
        return _parameter_writes_in_nodes(value.body, nested)
    if isinstance(value, ASTNode):
        return _parameter_writes_in_nodes((value,), parameters)
    if isinstance(value, tuple):
        writes: list[tuple[ASTNode, Symbol]] = []
        for item in value:
            writes.extend(_parameter_writes_in_value(item, parameters))
        return tuple(writes)
    return ()


def _function_analysis_from_signatures(
    signatures: dict[T.Overload, tuple[TypedNode, ...]],
) -> _core.FunctionAnalysis | None:
    """Build the signatures for function analysis from during static analysis."""
    if not signatures:
        return None

    ordered = tuple(
        sorted(
            signatures,
            key=lambda overload: T.show(T.Fn(overload.params, overload.returns)),
        )
    )
    overload_typings = tuple(
        FunctionOverloadTyping(
            T.Fn(signature.params, signature.returns, signature.element_tags),
            signatures[signature],
            signature,
        )
        for signature in ordered
    )
    if len(ordered) == 1 and not ordered[0].where_clause:
        signature = ordered[0]
        typ = T.Fn(signature.params, signature.returns, signature.element_tags)
    else:
        typ = T.Overloads(*ordered)
    return _core.FunctionAnalysis(typ, overload_typings)


def _callable_overloads(typ: T.Type) -> tuple[T.Overload, ...]:
    """Collect the overloads for callable during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return ()
        return (
            T.Overload(
                params=typ.params,
                returns=typ.returns,
                element_tags=typ.element_tags,
            ),
        )
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _element_tag_covers(
    requirement: T.ElementTag,
    actual: T.ElementTag,
    ctx: T.Context,
) -> bool:
    """Return whether one declared tag covers a concrete propagated effect."""
    if requirement.name != actual.name:
        return False
    if not requirement.args:
        return True
    if len(requirement.args) != len(actual.args):
        return False
    return all(
        T.assignable(actual_arg, required_arg, ctx)
        for actual_arg, required_arg in zip(
            actual.args,
            requirement.args,
            strict=True,
        )
    )


def _element_tag_absence_conflicts(
    forbidden: T.ElementTag,
    actual: T.ElementTag,
    ctx: T.Context,
) -> bool:
    """Return whether a propagated effect may overlap a declared absence."""
    if forbidden.name != actual.name:
        return False
    if not forbidden.args:
        return True
    if len(forbidden.args) != len(actual.args):
        return False
    return all(
        _types_may_overlap(forbidden_arg, actual_arg, ctx)
        for forbidden_arg, actual_arg in zip(
            forbidden.args,
            actual.args,
            strict=True,
        )
    )


def _types_may_overlap(
    left: T.Type,
    right: T.Type,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of types may overlap during static analysis."""
    left = T.normalize(left)
    right = T.normalize(right)
    if isinstance(left, T.UnionType):
        return any(_types_may_overlap(item, right, ctx) for item in left.items)
    if isinstance(right, T.UnionType):
        return any(_types_may_overlap(left, item, ctx) for item in right.items)
    return T.assignable(left, right, ctx) or T.assignable(right, left, ctx)


def _final_function_element_tags(
    node: FunctionNode,
    body_tags: frozenset[T.ElementTag],
    env: T.Environment,
) -> frozenset[T.ElementTag]:
    """Compute final function element tags during static analysis."""
    declared = set(node.element_tags)
    if not node.element_tags_explicit:
        return frozenset(declared | set(body_tags))

    final = set(declared)
    declared_properties = tuple(
        tag
        for tag in node.element_tags
        if not tag.absent
        and (definition := env.lookup_element_tag(tag.name)) is not None
        and definition.kind is T.ElementTagKind.PROPERTY
    )
    for tag in body_tags:
        definition = env.lookup_element_tag(tag.name)
        if definition is not None and definition.kind is T.ElementTagKind.COMPANION:
            final.add(tag)
            continue
        if not any(
            _element_tag_covers(declared_tag, tag, env.context)
            for declared_tag in declared_properties
        ):
            final.add(tag)
    return frozenset(final)


def _function_type_element_tag_sets(
    typ: T.Type,
) -> Iterator[frozenset[T.ElementTag]]:
    """Yield every function-tag set nested in a type annotation."""
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        yield typ.element_tags
        for tag in typ.element_tags:
            for arg in tag.args:
                yield from _function_type_element_tag_sets(arg)
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _function_type_element_tag_sets(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _function_type_element_tag_sets(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _function_type_element_tag_sets(typ.base)
        for field in typ.fields:
            yield from _function_type_element_tag_sets(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _function_type_element_tag_sets(typ.base)
        return
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        yield from _function_type_element_tag_sets(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            yield requirement.overload.element_tags
            for item in (*requirement.overload.params, *requirement.overload.returns):
                yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            yield overload.element_tags
            for item in (*overload.params, *overload.returns):
                yield from _function_type_element_tag_sets(item)


def _present_data_tags(typ: T.Type) -> Iterator[T.DataTag]:
    """Yield present data tags anywhere inside a call argument type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        yield from (tag for tag in typ.tags if not tag.absent)
        yield from _present_data_tags(typ.inner)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _present_data_tags(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _present_data_tags(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _present_data_tags(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _present_data_tags(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _present_data_tags(typ.base)
        for field in typ.fields:
            yield from _present_data_tags(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _present_data_tags(typ.base)
        return
    if isinstance(typ, T.FunctionType):
        for tag in typ.element_tags:
            for arg in tag.args:
                yield from _present_data_tags(arg)
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _present_data_tags(item)
        return
    if isinstance(typ, (T.NoVecType, T.ExactType)):
        yield from _present_data_tags(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in (*requirement.overload.params, *requirement.overload.returns):
                yield from _present_data_tags(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in (*overload.params, *overload.returns):
                yield from _present_data_tags(item)


def _best_candidates(
    candidates: Iterable[_core.CallCandidate],
    original: _core.AnalysisBranch | None = None,
) -> tuple[_core.CallCandidate, ...]:
    """Collect viable candidates for best during static analysis."""
    ordered = list(candidates)
    winners: list[_core.CallCandidate] = []
    for candidate in ordered:
        if not any(
            other is not candidate
            and _candidate_dominates(other, candidate)
            and not _preserve_distinct_inferred_specializations(
                other,
                candidate,
                original,
            )
            for other in ordered
        ):
            winners.append(candidate)
    return tuple(winners)


def _candidate_dominates(
    left: _core.CallCandidate,
    right: _core.CallCandidate,
) -> bool:
    """Return the Boolean result of candidate dominates during static analysis."""
    left_applied = left.applied
    right_applied = right.applied
    if left.dispatch_priority != right.dispatch_priority:
        return left.dispatch_priority > right.dispatch_priority
    if _calls._dominates(left_applied.scores, right_applied.scores):
        return True
    if left_applied.scores != right_applied.scores:
        return False
    return _params_more_specific(left_applied.params, right_applied.params)


def _params_more_specific(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    """Return the Boolean result of params more specific during static analysis."""
    return all(
        _type_more_specific_or_same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=False)
    ) and any(
        not _type_more_specific_or_same(right_item, left_item)
        for left_item, right_item in zip(left, right, strict=False)
    )


def _type_more_specific_or_same(left: T.Type, right: T.Type) -> bool:
    """Return whether a type is at least as specific as another."""
    left = T.normalize(left)
    right = T.normalize(right)
    if T.same(left, right) or T.assignable(left, right):
        return True
    if isinstance(left, T.FunctionType) and isinstance(right, T.FunctionType):
        if left.params is None or left.returns is None:
            return right.params is None and right.returns is None
        if right.params is None or right.returns is None:
            return True
        if len(left.params) != len(right.params) or len(left.returns) != len(
            right.returns
        ):
            return False
        return all(
            _type_more_specific_or_same(left_item, right_item)
            for left_item, right_item in zip(
                left.params,
                right.params,
                strict=True,
            )
        ) and all(
            _type_more_specific_or_same(left_item, right_item)
            for left_item, right_item in zip(
                left.returns,
                right.returns,
                strict=True,
            )
        )
    return False


def _preserve_distinct_inferred_specializations(
    left: _core.CallCandidate,
    right: _core.CallCandidate,
    original: _core.AnalysisBranch | None,
) -> bool:
    """Return whether inferred specializations must remain distinct."""
    if original is None:
        return False
    left_key = _inferred_specialization_key(left.branch, original)
    right_key = _inferred_specialization_key(right.branch, original)
    return left_key is not None and right_key is not None and left_key != right_key


def _inferred_specialization_key(
    branch: _core.AnalysisBranch,
    original: _core.AnalysisBranch,
) -> tuple[object, ...] | None:
    """Build the comparison key for inferred specialization during static analysis."""
    if branch.inputs != original.inputs:
        return ("inputs", branch.inputs)
    if branch.cycle_params != original.cycle_params:
        return ("cycle_params", branch.cycle_params)
    if branch.variables != original.variables:
        return ("variables", branch.variables)
    return None


def _winners_specialize_inputs(
    winners: tuple[_core.CallCandidate, ...],
    original: _core.AnalysisBranch,
) -> bool:
    """Return the Boolean result of winners specialize inputs during static analysis."""
    return all(candidate.branch.inputs != original.inputs for candidate in winners)


def _generic_constraints(
    generics: tuple[Symbol, ...],
    variances: tuple[Symbol | None, ...],
    constraints: tuple[T.Type | None, ...],
) -> tuple[T.GenericConstraint, ...]:
    """Compute generic constraints during static analysis."""
    if len(generics) != len(constraints):
        return ()
    if len(variances) != len(generics):
        variances = (None,) * len(generics)
    return tuple(
        T.GenericConstraint(
            generic.text,
            _genericize_type(bound, generics),
            _constraint_variance_from_marker(marker),
        )
        for generic, marker, bound in zip(generics, variances, constraints, strict=True)
        if bound is not None
    )


def _constraint_variance_from_marker(marker: Symbol | None) -> T.Variance:
    """Compute constraint variance from marker during static analysis."""
    if marker is None or marker.text == "any":
        return T.Variance.COVARIANT
    if marker.text == "above":
        return T.Variance.CONTRAVARIANT
    return _variance_from_marker(marker)


def _with_generic_constraints(
    overload: T.Overload,
    constraints: tuple[T.GenericConstraint, ...],
) -> T.Overload:
    """Compute with generic constraints during static analysis."""
    if not constraints:
        return overload
    return replace(
        overload,
        generic_constraints=overload.generic_constraints + constraints,
    )


def _has_multimethod_fallback(
    overload: T.Overload,
    candidates: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
    """Return whether the analyser helper has multimethod fallback."""
    return any(
        not candidate.is_multi
        and len(candidate.params) == len(overload.params)
        and _multimethod_params_covered_by(overload.params, candidate.params, ctx)
        and _same_returns(overload.returns, candidate.returns)
        for candidate in candidates
    )


def _mark_multidispatch(
    applied: T.AppliedOverload,
    overloads: tuple[T.Overload, ...],
    ctx: T.Context,
) -> T.AppliedOverload:
    """Compute mark multidispatch during static analysis."""
    if applied.overload.is_multi:
        if any(
            candidate is not applied.overload
            and candidate.is_multi
            and candidate.params == applied.overload.params
            and candidate.returns == applied.overload.returns
            for candidate in overloads
        ):
            return replace(applied, multidispatch=True)
        return applied
    if not _has_runtime_multimethod_candidate(applied.overload, overloads, ctx):
        return applied
    return replace(applied, multidispatch=True)


def _collapse_equivalent_call_winners(
    winners: tuple[_core.CallCandidate, ...],
) -> tuple[_core.CallCandidate, ...]:
    """Collapse inference paths that resolve to the same concrete invocation."""
    unique: list[_core.CallCandidate] = []
    for candidate in winners:
        equivalent_index = next(
            (
                index
                for index, existing in enumerate(unique)
                if candidate.applied.params == existing.applied.params
                and candidate.applied.actual_returns == existing.applied.actual_returns
                and candidate.branch == existing.branch
                and candidate.call_arg_order == existing.call_arg_order
                and candidate.callable_overload_index
                == existing.callable_overload_index
                and candidate.overload_index == existing.overload_index
                and candidate.dispatch_priority == existing.dispatch_priority
            ),
            None,
        )
        if equivalent_index is None:
            unique.append(candidate)
            continue
        existing = unique[equivalent_index]
        if _contextual_modifier_quality(candidate) > _contextual_modifier_quality(
            existing
        ):
            unique[equivalent_index] = candidate
    return tuple(unique)


def _contextual_modifier_quality(candidate: _core.CallCandidate) -> int:
    """Prefer modifier typings already specialized to their contextual type."""
    return sum(
        typing.typ == modifier.typ
        for modifier in candidate.modifiers
        for typing in modifier.typed_node.overloads
    )


def _collapse_equivalent_friendly_multidispatch_winners(
    winners: tuple[_core.CallCandidate, ...],
) -> tuple[_core.CallCandidate, ...]:
    """Collapse equivalent friendly multidispatch winners."""
    if len(winners) <= 1:
        return winners
    first = winners[0]
    if first.dispatch_priority != 0 or not first.applied.multidispatch:
        return winners
    if not all(
        candidate.dispatch_priority == 0
        and candidate.applied.multidispatch
        and candidate.applied.params == first.applied.params
        and candidate.applied.actual_returns == first.applied.actual_returns
        and candidate.branch == first.branch
        for candidate in winners[1:]
    ):
        return winners
    return (
        min(
            winners,
            key=lambda candidate: (
                candidate.overload_index if candidate.overload_index is not None else -1
            ),
        ),
    )


def _has_runtime_multimethod_candidate(
    fallback: T.Overload,
    overloads: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
    """Return whether the analyser helper has runtime multimethod candidate."""
    return any(
        candidate.is_multi
        and candidate is not fallback
        and len(candidate.params) == len(fallback.params)
        and _multimethod_params_covered_by(candidate.params, fallback.params, ctx)
        and _same_returns(candidate.returns, fallback.returns)
        for candidate in overloads
    )


def _multimethod_params_covered_by(
    specific: tuple[T.Type, ...],
    fallback: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return whether one multimethod parameter set covers another."""
    return all(
        T.assignable(specific_param, fallback_param, ctx)
        for specific_param, fallback_param in zip(specific, fallback, strict=True)
    )


def _same_returns(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    """Return the Boolean result of same returns during static analysis."""
    return len(left) == len(right) and all(
        T.same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )



from .signatures import (
    _genericize_overload,
    _genericize_function_node,
    _contextualize_function_empty_returns,
    _contextualize_return_block,
    _contextualize_explicit_return,
    _contextualize_return_expression,
    _empty_list_return_type,
    _substitute_rank_variables_in_ast,
    _substitute_rank_variables_in_ast_value,
    _genericize_ast_node,
    _genericize_ast_value,
    _genericize_attribute,
    _genericize_requirement,
    _transform_overload_types,
    _transform_type_children,
    _genericize_type,
    _anonymous_trait_overloads,
    _anonymous_trait_subject_view,
    _anonymous_trait_subject_name,
    _first_type_var_name,
    _contains_anonymous_trait,
    _collect_anonymous_trait_overloads,
    _genericize_element_tags,
    _declared_or_inferred_variance,
    _variance_from_marker,
    _infer_generic_variance,
    _record_variance_use,
    _anonymous_type_var,
    _anonymous_type_indices,
    _collect_anonymous_type_indices,
)
