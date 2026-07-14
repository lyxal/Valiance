"""Function typing, genericisation, and callable-shape helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import field, fields, replace
from typing import cast

import valiance.analysis.annotations as annotation_hooks
import valiance.types as T
import valiance.analysis.where_clause as static_where
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
from valiance.types.symbols import Symbol

from . import analyser as _core
from . import _analyser_calls as _calls
from . import _analyser_utils as _utils


def _declared_params(node: FunctionNode) -> tuple[T.Type, ...]:
    """Determine the parameters for declared during static analysis."""
    if node.params is None:
        return ()
    return _params_to_types(node.params)


def _atomic_type_var_names(typ: T.Type) -> frozenset[str]:
    """Collect generics whose own rank determines an atomic position."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AtomicType):
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
    elif isinstance(typ, (T.TaggedType, T.ExactType)):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.ExactType, T.AtomicType)):
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
    if isinstance(declared, T.ExactType):
        return T.Exact(_restore_type_markers(declared.inner, inferred))
    if isinstance(declared, T.AtomicType):
        return T.Atomic(_restore_type_markers(declared.inner, inferred))
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
        where_clause=where_clause,
        param_names=_function_param_names_for_overload(node, params),
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
            substituted.append(FunctionParam(param.name, actual, param.default))
            continue
        if not _call_site_placeholder_accepts(typ, actual, ctx):
            return None
        substituted.append(
            FunctionParam(
                param.name,
                _call_site_substitute_type(typ, actual),
                param.default,
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


def _function_param_names_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[Symbol | None, ...]:
    """Return parameter names aligned with an overload."""
    if node.params is None:
        return (None,) * len(inputs)
    names = tuple(param.name for param in node.params)
    if len(names) < len(inputs):
        return (None,) * (len(inputs) - len(names)) + names
    return names


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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
        param_names=_function_param_names_for_overload(node, params),
        clause=node.where_clause,
    )
    return tuple(Symbol(name) for name in names)


def _params_to_types(params: tuple[FunctionParam, ...]) -> tuple[T.Type, ...]:
    """Determine the types used for params to during static analysis."""
    return tuple(_utils._param_type(param, index) for index, param in enumerate(params))


def _function_capture_source(
    outer: _core.AnalysisBranch,
) -> _core.BranchVariables | None:
    """Return bindings whose types are available inside a function body."""
    if outer.input_mode is not _core.InputMode.TOP_LEVEL:
        return outer.variables
    constants = outer.variables.constant_items()
    if not constants:
        return None
    return _core.BranchVariables(
        function_locals=constants,
        function_constants=tuple(name for name, _typ in constants),
    )


def _top_level_assignment_capture_nodes(
    outer: _core.AnalysisBranch,
    node: FunctionNode,
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture nodes during static analysis."""
    if outer.input_mode is not _core.InputMode.TOP_LEVEL:
        return ()
    visible = set(outer.variables.nonconstant_names())
    if not visible:
        return ()
    return _top_level_assignment_capture_reads_in_function(node, visible, frozenset())


def _top_level_assignment_capture_reads_in_function(
    node: FunctionNode,
    visible: set[Symbol],
    inherited_bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in function during static analysis."""
    bound = inherited_bound | _function_bound_variable_names(node)
    return _top_level_assignment_capture_reads_in_nodes(node.body, visible, bound)


def _top_level_assignment_capture_reads_in_nodes(
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
                _top_level_assignment_capture_reads_in_function(
                    node,
                    visible,
                    bound,
                )
            )
            continue
        for item in fields(node):
            reads.extend(
                _top_level_assignment_capture_reads_in_value(
                    getattr(node, item.name),
                    visible,
                    bound,
                )
            )
    return tuple(reads)


def _top_level_assignment_capture_reads_in_value(
    value: object,
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in value during static analysis."""
    if isinstance(value, FunctionNode):
        return _top_level_assignment_capture_reads_in_function(value, visible, bound)
    if isinstance(value, ASTNode):
        return _top_level_assignment_capture_reads_in_nodes((value,), visible, bound)
    if isinstance(value, tuple):
        reads: list[GetVariableNode] = []
        for item in value:
            reads.extend(
                _top_level_assignment_capture_reads_in_value(item, visible, bound)
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.ExactType, T.AtomicType)):
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
                candidate.overload_index
                if candidate.overload_index is not None
                else -1
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


def _genericize_overload(
    overload: T.Overload,
    generics: tuple[Symbol, ...],
) -> T.Overload:
    """Generalize overload during static analysis."""
    if not generics:
        return overload
    return _transform_overload_types(
        overload,
        lambda typ: _genericize_type(typ, generics),
    )


def _genericize_function_node(
    function: FunctionNode,
    generics: tuple[Symbol, ...],
) -> FunctionNode:
    """Generalize a function and erase call-policy markers from value returns."""
    params = None
    if function.params is not None:
        params = tuple(
            cast(FunctionParam, _genericize_ast_value(param, generics))
            for param in function.params
        )
    returns = None
    if function.returns is not None:
        returns = tuple(
            _parameter_value_type(_genericize_type(ret, generics))
            for ret in function.returns
        )
    generic_constraints = tuple(
        None if bound is None else _genericize_type(bound, generics)
        for bound in function.generic_constraints
    )
    return FunctionNode(
        generics=function.generics,
        generic_variances=function.generic_variances,
        params=params,
        body=tuple(_genericize_ast_node(node, generics) for node in function.body),
        returns=returns,
        where_clause=tuple(
            _genericize_ast_node(node, generics) for node in function.where_clause
        ),
        element_tags=frozenset(
            _genericize_element_tags(function.element_tags, generics)
        ),
        annotations=function.annotations,
        element_tags_explicit=function.element_tags_explicit,
        companion_tags_allowed=frozenset(
            _genericize_element_tags(function.companion_tags_allowed, generics)
        ),
        generic_constraints=generic_constraints,
        location=function.location,
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
    if isinstance(node, ReturnNode) and len(node.values) == len(returns):
        return replace(
            node,
            values=tuple(
                _contextualize_return_expression(value, expected)
                for value, expected in zip(node.values, returns, strict=True)
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
    if isinstance(expected, (T.TaggedType, T.ExactType)):
        return _empty_list_return_type(expected.inner)
    if isinstance(expected, (T.ListExactType, T.ListMinType, T.ListRuggedType)):
        return T.C(T.ListExactType, expected.base, expected.rank)
    if isinstance(expected, (T.ArrayExactType, T.ArrayMinType)):
        return T.C(T.ArrayExactType, expected.base, expected.rank)
    return None


def _substitute_rank_variables_in_ast(
    node: ASTNode,
    ranks: dict[str, int],
    types: dict[str, T.Type] | None = None,
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
            name: value
            for name, value in ranks.items()
            if name not in shadowed_ranks
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
    types: dict[str, T.Type],
) -> object:
    """Substitute static bindings through one recursively nested AST value."""
    if isinstance(value, T.Type):
        return static_where.substitute_static_type(
            value, ranks=ranks, types=types
        )
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
                _substitute_rank_variables_in_ast(
                    node, ranks, types, root=False
                ),
            )
            for node in value.default
        )
        if typ is value.typ and default == value.default:
            return value
        return replace(value, typ=typ, default=default)
    if isinstance(value, CallArgument):
        argument = tuple(
            cast(
                ASTNode,
                _substitute_rank_variables_in_ast(
                    node, ranks, types, root=False
                ),
            )
            for node in value.value
        )
        if argument == value.value:
            return value
        return replace(value, value=argument)
    if isinstance(value, ASTNode):
        return _substitute_rank_variables_in_ast(
            value, ranks, types, root=False
        )
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


def _genericize_ast_node(node: ASTNode, generics: tuple[Symbol, ...]) -> ASTNode:
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
        updated = _genericize_ast_value(value, generics)
        if updated is not value:
            updates[item.name] = updated
    return replace(node, **updates) if updates else node


def _genericize_ast_value(value: object, generics: tuple[Symbol, ...]) -> object:
    """Generalize AST value during static analysis."""
    if isinstance(value, T.Type):
        return _genericize_type(value, generics)
    if isinstance(value, FunctionParam):
        typ = None if value.typ is None else _genericize_type(value.typ, generics)
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics))
            for node in value.default
        )
        if typ is value.typ and default == value.default:
            return value
        return replace(value, typ=typ, default=default)
    if isinstance(value, CallArgument):
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics)) for node in value.value
        )
        if default == value.value:
            return value
        return replace(value, value=default)
    if isinstance(value, ASTNode):
        return _genericize_ast_node(value, generics)
    if isinstance(value, tuple):
        return tuple(_genericize_ast_value(item, generics) for item in value)
    return value


def _genericize_attribute(
    attribute: T.ObjectAttribute,
    generics: tuple[Symbol, ...],
) -> T.ObjectAttribute:
    """Generalize attribute during static analysis."""
    return T.ObjectAttribute(
        attribute.name,
        _genericize_type(attribute.typ, generics),
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
        _transform_overload_types(
            requirement.overload,
            lambda typ: _genericize_type(typ, generics),
        ),
    )


def _transform_overload_types(
    overload: T.Overload,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: frozenset[T.ElementTag] | None = None,
) -> T.Overload:
    """Determine the types used for transform overload during static analysis."""
    return replace(
        overload,
        params=tuple(transform(param) for param in overload.params),
        returns=tuple(transform(ret) for ret in overload.returns),
        element_tags=overload.element_tags if element_tags is None else element_tags,
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
            *(
                T.TupleTypeItem(transform(item.typ), item.repeated)
                for item in typ.items
            )
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
    if isinstance(typ, T.ExactType):
        return T.Exact(transform(typ.inner))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(transform(typ.inner))
    return typ


def _genericize_type(typ: T.Type, generics: tuple[Symbol, ...]) -> T.Type:
    """Generalize type during static analysis."""
    names = {generic.text for generic in generics}
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in names:
            return T.V(typ.name.text)
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _genericize_overload(requirement.overload, generics),
                )
                for requirement in typ.requirements
            ),
        )
    return _transform_type_children(
        typ,
        lambda child: _genericize_type(child, generics),
        element_tags=lambda tags: _genericize_element_tags(tags, generics),
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
        subject = _anonymous_trait_subject_name(typ)
        if subject is not None:
            return T.V(subject)
        return typ
    return _transform_type_children(typ, _anonymous_trait_subject_view)


def _anonymous_trait_subject_name(typ: T.AnonymousTraitType) -> str | None:
    """Return the canonical name for anonymous trait subject during static analysis."""
    if typ.generics:
        return typ.generics[0].text
    for requirement in typ.requirements:
        for item in requirement.overload.params + requirement.overload.returns:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    return None


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
        return _anonymous_trait_subject_name(typ)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_anonymous_trait_overloads(typ.inner, overloads)


def _genericize_element_tags(
    tags: frozenset[T.ElementTag],
    generics: tuple[Symbol, ...],
) -> tuple[T.ElementTag, ...]:
    """Generalize element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_genericize_type(arg, generics) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _declared_or_inferred_variance(
    generics: tuple[Symbol, ...],
    explicit: tuple[Symbol | None, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
) -> tuple[T.Variance, ...]:
    """Compute declared or inferred variance during static analysis."""
    inferred = _infer_generic_variance(generics, attributes, requirements)
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
) -> tuple[T.Variance, ...]:
    """Infer generic variance during static analysis."""
    usage = {generic.text: [False, False] for generic in generics}
    for attribute in attributes:
        _record_variance_use(attribute.typ, +1, usage)
        if attribute.access.text == "public":
            _record_variance_use(attribute.typ, -1, usage)
    for requirement in requirements:
        for param in requirement.overload.params:
            _record_variance_use(param, -1, usage)
        for ret in requirement.overload.returns:
            _record_variance_use(ret, +1, usage)
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
) -> None:
    """Record variance use during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        if typ.name in usage:
            usage[typ.name][0 if polarity > 0 else 1] = True
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _record_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _record_variance_use(item, polarity, usage)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _record_variance_use(item, polarity, usage)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _record_variance_use(item.typ, polarity, usage)
        return
    if isinstance(typ, T.RowType):
        _record_variance_use(typ.base, polarity, usage)
        for field in typ.fields:
            _record_variance_use(field.typ, polarity, usage)
        return
    if isinstance(typ, T.CollectionType):
        _record_variance_use(typ.base, polarity, usage)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            for tag in typ.element_tags:
                for arg in tag.args:
                    _record_variance_use(arg, polarity, usage)
            return
        for param in typ.params:
            _record_variance_use(param, -polarity, usage)
        for ret in typ.returns:
            _record_variance_use(ret, polarity, usage)
        for tag in typ.element_tags:
            for arg in tag.args:
                _record_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for param in requirement.overload.params:
                _record_variance_use(param, -polarity, usage)
            for ret in requirement.overload.returns:
                _record_variance_use(ret, polarity, usage)
        return
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _record_variance_use(typ.inner, polarity, usage)


def _anonymous_type_var(branch: _core.AnalysisBranch, offset: int) -> T.Type:
    """Compute anonymous type var during static analysis."""
    taken = _anonymous_type_indices(
        *branch.stack.items,
        *branch.inputs,
        *branch.cycle_params,
        *(typ for _, typ in branch.variables.visible_items()),
    )
    start = max(taken, default=0)
    return T.V(f"@{start + offset}")


def _anonymous_type_indices(*types: T.Type) -> set[int]:
    """Compute anonymous type indices during static analysis."""
    indices: set[int] = set()
    for typ in types:
        _collect_anonymous_type_indices(T.normalize(typ), indices)
    return indices


def _collect_anonymous_type_indices(typ: T.Type, indices: set[int]) -> None:
    """Collect anonymous type indices during static analysis."""
    if isinstance(typ, T.VarType) and typ.name.startswith("@"):
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
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_anonymous_type_indices(typ.inner, indices)
