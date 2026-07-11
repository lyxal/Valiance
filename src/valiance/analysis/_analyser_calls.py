"""Element call planning, overload application, and tag-flow helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from itertools import count, permutations
from typing import cast

import valiance.types as T
from valiance.asts import (
    ASTNode,
    CallArgument,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    NumberLiteralNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.asts.nodes import FieldAccessNode, GetVariableNode, SetVariableNode
from valiance.symbols import Symbol

from . import analyser as _core
from . import _analyser_functions as _functions
from . import _analyser_utils as _utils


def _overload_index(
    overloads: tuple[T.Overload, ...],
    overload: T.Overload,
) -> int | None:
    """Find the index for overload during static analysis."""
    try:
        return overloads.index(overload)
    except ValueError:
        return None


def _atomic_call_requirements_satisfied(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    scalar_generics: frozenset[str],
) -> bool:
    """Check analysis-only scalar guarantees required by atomic markers."""
    return len(args) == len(params) and all(
        _atomic_requirement_satisfied(actual, expected, scalar_generics)
        for actual, expected in zip(args, params, strict=True)
    )


def _atomic_requirement_satisfied(
    actual: T.Type,
    expected: T.Type,
    scalar_generics: frozenset[str],
) -> bool:
    """Check one parameter tree without exposing markers as value types."""
    actual = T.normalize(actual)
    expected = T.normalize(expected)
    if isinstance(expected, T.AtomicType):
        return _analysis_type_is_scalar(actual, scalar_generics)
    if isinstance(actual, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _atomic_requirement_satisfied(
            actual.inner,
            expected,
            scalar_generics,
        )
    if isinstance(expected, (T.TaggedType, T.ExactType)):
        return _atomic_requirement_satisfied(
            actual,
            expected.inner,
            scalar_generics,
        )
    if isinstance(actual, T.CollectionType) and isinstance(
        expected,
        T.CollectionType,
    ):
        return _atomic_requirement_satisfied(
            actual.base,
            expected.base,
            scalar_generics,
        )
    if isinstance(actual, T.NominalType) and isinstance(expected, T.NominalType):
        if actual.name != expected.name or len(actual.args) != len(expected.args):
            return True
        return all(
            _atomic_requirement_satisfied(left, right, scalar_generics)
            for left, right in zip(actual.args, expected.args, strict=True)
        )
    if isinstance(actual, T.TupleType) and isinstance(expected, T.TupleType):
        if len(actual.params) != len(expected.params):
            return True
        return all(
            _atomic_requirement_satisfied(left, right, scalar_generics)
            for left, right in zip(actual.params, expected.params, strict=True)
        )
    if isinstance(actual, T.RowType) and isinstance(expected, T.RowType):
        if not _atomic_requirement_satisfied(
            actual.base,
            expected.base,
            scalar_generics,
        ):
            return False
        actual_fields = {field.name: field.typ for field in actual.fields}
        return all(
            field.name not in actual_fields
            or _atomic_requirement_satisfied(
                actual_fields[field.name],
                field.typ,
                scalar_generics,
            )
            for field in expected.fields
        )
    # Compound alternatives are resolved by the normal overload solver. Atomic
    # constraints in the common collection/nominal/tuple paths above are the
    # ones that can otherwise be erased by symbolic generic substitution.
    return True


def _analysis_type_is_scalar(
    typ: T.Type,
    scalar_generics: frozenset[str],
) -> bool:
    """Return whether analysis proves a type has rank zero."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AtomicType):
        return True
    if isinstance(typ, (T.TaggedType, T.ExactType)):
        return _analysis_type_is_scalar(typ.inner, scalar_generics)
    if isinstance(typ, T.VarType):
        return typ.name in scalar_generics
    if isinstance(typ, T.CollectionType):
        return False
    if isinstance(typ, T.UnionType):
        return all(
            _analysis_type_is_scalar(item, scalar_generics)
            for item in typ.items
        )
    if isinstance(typ, T.IntersectionType):
        return any(
            _analysis_type_is_scalar(item, scalar_generics)
            for item in typ.items
        )
    if isinstance(typ, T.RowType):
        return _analysis_type_is_scalar(typ.base, scalar_generics)
    if isinstance(typ, T.AnonymousTraitType):
        return False
    return True


def _source_element_arguments(
    branch: _core.AnalysisBranch,
    overload: T.Overload,
    modifier_args: tuple[_core.ModifierArgumentAnalysis, ...],
    ctx: T.Context,
    call_arg_order: tuple[int, ...] = (),
    analyser: _core.Analyser | None = None,
) -> Iterator[
    tuple[
        tuple[T.Type, ...],
        _core.AnalysisBranch,
        tuple[_core.ModifierArgumentAnalysis, ...],
    ]
]:
    """Source element arguments during static analysis."""
    if not modifier_args:
        params = _call_args_in_current_order(overload.params, call_arg_order)
        sourced = branch.source_arguments(params)
        if sourced is not None:
            current_args, popped = sourced
            args = _call_args_in_parameter_order(current_args, call_arg_order)
            if not _atomic_call_requirements_satisfied(
                args,
                overload.params,
                popped.atomic_type_vars,
            ):
                return
            for specialized_args, specialized_popped in (
                _contextual_stack_argument_variants(
                    args,
                    overload.params,
                    popped,
                    ctx,
                    analyser,
                )
            ):
                yield specialized_args, specialized_popped, ()
        return

    modifier_indexes = _modifier_param_indexes(overload.params)
    if len(modifier_indexes) != len(modifier_args):
        return

    stack_params = tuple(
        param
        for index, param in enumerate(overload.params)
        if index not in modifier_indexes
    )
    current_stack_params = _call_args_in_current_order(stack_params, call_arg_order)
    sourced = branch.source_arguments(current_stack_params)
    if sourced is None:
        return
    current_stack_args, popped = sourced
    stack_args = _call_args_in_parameter_order(current_stack_args, call_arg_order)
    stack_substitution = _branch_argument_substitution(stack_args, stack_params, ctx)
    if stack_substitution is None:
        return

    modifier_orders = (
        (modifier_args,)
        if _overload_needs_call_site_checking(overload)
        else _unique_permutations(modifier_args)
    )
    for ordered_modifiers in modifier_orders:
        for substitution, specialized_modifiers in _specialized_modifier_orders(
            overload.params,
            modifier_indexes,
            ordered_modifiers,
            stack_substitution,
            ctx,
            analyser,
        ):
            specialized_stack_args = tuple(
                _substitute_branch_type(arg, substitution) for arg in stack_args
            )
            specialized_popped = _specialize_branch_arguments(popped, substitution)
            merged_args = _merge_element_arguments(
                overload.params,
                modifier_indexes,
                specialized_stack_args,
                specialized_modifiers,
            )
            if not _atomic_call_requirements_satisfied(
                merged_args,
                overload.params,
                specialized_popped.atomic_type_vars,
            ):
                continue
            yield (
                merged_args,
                specialized_popped,
                specialized_modifiers,
            )


def _contextual_stack_argument_variants(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    branch: _core.AnalysisBranch,
    ctx: T.Context,
    analyser: _core.Analyser | None,
) -> Iterator[tuple[tuple[T.Type, ...], _core.AnalysisBranch]]:
    """Contextualize deferred function literals passed on the value stack."""
    inferred_literal_vars: set[str] = set()
    for arg, param in zip(args, params, strict=True):
        if not isinstance(T.normalize(param), T.FunctionType):
            continue
        if _stack_function_literal(arg, branch) is None:
            continue
        inferred_literal_vars.update(_type_variable_names(arg))

    if inferred_literal_vars:
        inferred = _branch_argument_substitution(args, params, ctx)
        if inferred is not None:
            literal_substitution = {
                name: typ
                for name, typ in inferred.items()
                if name in inferred_literal_vars
            }
            if literal_substitution:
                branch = _specialize_branch_arguments(branch, literal_substitution)
                args = tuple(
                    _substitute_branch_type(arg, literal_substitution) for arg in args
                )

    deferred: list[tuple[int, _core.ModifierArgumentAnalysis]] = []
    for index, (arg, param) in enumerate(zip(args, params, strict=True)):
        if not isinstance(T.normalize(param), T.FunctionType):
            continue
        modifier = _deferred_stack_function_argument(arg, branch)
        if modifier is not None:
            deferred.append((index, modifier))

    if not deferred:
        yield args, branch
        return

    deferred_indexes = {index for index, _ in deferred}
    ordinary_args = tuple(
        arg for index, arg in enumerate(args) if index not in deferred_indexes
    )
    ordinary_params = tuple(
        param for index, param in enumerate(params) if index not in deferred_indexes
    )
    substitution = _branch_argument_substitution(ordinary_args, ordinary_params, ctx)
    if substitution is None:
        return

    def rec(
        position: int,
        current_substitution: dict[str, T.Type],
        replacements: tuple[TypedFunctionNode, ...],
    ) -> Iterator[tuple[tuple[T.Type, ...], _core.AnalysisBranch]]:
        """Recursively specialize each deferred stack function argument."""
        if position == len(deferred):
            specialized_args = list(
                _substitute_branch_type(arg, current_substitution) for arg in args
            )
            specialized_branch = _specialize_branch_arguments(
                branch,
                current_substitution,
            )
            for (argument_index, _), replacement in zip(
                deferred,
                replacements,
                strict=True,
            ):
                concrete_type = _substitute_branch_type(
                    replacement.typ,
                    current_substitution,
                )
                concrete_node = replace(replacement, typ=concrete_type)
                specialized_args[argument_index] = concrete_type
                specialized_branch = _replace_contextual_function_node(
                    specialized_branch,
                    concrete_node,
                )
            yield tuple(specialized_args), specialized_branch
            return

        argument_index, modifier = deferred[position]
        expected = _substitute_branch_type(
            params[argument_index],
            current_substitution,
        )
        for specialized, modifier_substitution in _modifier_variants_for_expected(
            modifier,
            expected,
            ctx,
            analyser,
        ):
            merged = _merge_substitutions(
                current_substitution,
                modifier_substitution,
            )
            if merged is None:
                continue
            yield from rec(
                position + 1,
                merged,
                (*replacements, specialized.typed_node),
            )

    yield from rec(0, substitution, ())


def _stack_function_literal(
    typ: T.Type,
    branch: _core.AnalysisBranch,
) -> TypedFunctionNode | None:
    """Return the most recent function literal carrying the requested type."""
    for typed_node in reversed(branch.typed_body):
        if isinstance(typed_node, TypedFunctionNode) and T.same(typed_node.typ, typ):
            return typed_node
    return None


def _type_variable_names(typ: T.Type) -> frozenset[str]:
    """Collect free type-variable names from a type tree."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return frozenset((typ.name,))
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
        children = (
            ()
            if typ.params is None or typ.returns is None
            else typ.params + typ.returns
        )
    elif isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        children = (typ.inner,)
    else:
        children = ()
    names: set[str] = set()
    for child in children:
        names.update(_type_variable_names(child))
    return frozenset(names)


def _deferred_stack_function_argument(
    typ: T.Type,
    branch: _core.AnalysisBranch,
) -> _core.ModifierArgumentAnalysis | None:
    """Find the typed literal backing a deferred stack function type."""
    normalized = T.normalize(typ)
    overloads = (
        normalized.overloads
        if isinstance(normalized, T.OverloadSetType)
        else ()
    )
    source_nodes = tuple(
        overload.call_site_body[1]
        for overload in overloads
        if isinstance(overload.call_site_body, tuple)
        and len(overload.call_site_body) == 2
        and isinstance(overload.call_site_body[1], FunctionNode)
    )
    if not source_nodes:
        return None
    for typed_node in reversed(branch.typed_body):
        if not isinstance(typed_node, TypedFunctionNode):
            continue
        if any(
            typed_node.node is source or typed_node.node == source
            for source in source_nodes
        ):
            return _core.ModifierArgumentAnalysis(typ, typed_node)
    return None


def _replace_contextual_function_node(
    branch: _core.AnalysisBranch,
    replacement: TypedFunctionNode,
) -> _core.AnalysisBranch:
    """Replace one deferred function literal with its contextual typing."""
    typed_body = list(branch.typed_body)
    for index in range(len(typed_body) - 1, -1, -1):
        node = typed_body[index]
        if not isinstance(node, TypedFunctionNode):
            continue
        if node.node is replacement.node or node.node == replacement.node:
            typed_body[index] = replacement
            return replace(branch, typed_body=tuple(typed_body))
    return branch


def _call_args_in_current_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    """Compute call args in current order during static analysis."""
    if not call_arg_order:
        return items
    return tuple(items[index] for index in _invert_call_arg_order(call_arg_order))


def _call_args_in_parameter_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    """Compute call args in parameter order during static analysis."""
    if not call_arg_order:
        return items
    return tuple(items[index] for index in call_arg_order)


def _invert_call_arg_order(call_arg_order: tuple[int, ...]) -> tuple[int, ...]:
    """Compute invert call arg order during static analysis."""
    current_to_parameter = [0] * len(call_arg_order)
    for parameter_index, current_index in enumerate(call_arg_order):
        current_to_parameter[current_index] = parameter_index
    return tuple(current_to_parameter)


def _call_element_candidates(
    branch: _core.AnalysisBranch,
    call_overload: T.Overload,
    function_type: T.Type,
    explicit_args: tuple[T.Type, ...],
    base_stack: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
    disambiguation: tuple[T.Type | None, ...],
    ctx: T.Context,
) -> list[_core.CallCandidate]:
    """Collect viable candidates for call element during static analysis."""
    candidates: list[_core.CallCandidate] = []
    if disambiguation and len(disambiguation) != len(explicit_args):
        return candidates
    for callable_index, callable_overload in enumerate(
        _functions._callable_overloads(function_type)
    ):
        callable_application = T.try_apply_overload(
            callable_overload,
            explicit_args,
            ctx,
            disambiguation=disambiguation,
        ).applied
        if callable_application is None:
            continue
        concrete_function_type = T.Fn(
            callable_application.params,
            callable_application.actual_returns,
            callable_overload.element_tags,
        )
        concrete_args = (*explicit_args, concrete_function_type)
        concrete_overload = T.Overload(
            params=concrete_args,
            returns=callable_application.actual_returns,
            call_site_body=len(explicit_args),
        )
        concrete_application = T.try_apply_overload(
            concrete_overload,
            concrete_args,
            ctx,
        ).applied
        if concrete_application is None:
            continue
        actual_returns = _apply_data_tag_flow(
            explicit_args,
            callable_overload.returns,
            callable_application.actual_returns,
            ctx,
        )
        candidates.append(
            _core.CallCandidate(
                applied=T.AppliedOverload(
                    call_overload,
                    concrete_application.substitution,
                    concrete_application.params,
                    concrete_application.returns,
                    actual_returns,
                    concrete_application.scores,
                    concrete_application.vectorised,
                    concrete_application.vectorised_depths,
                    runtime_consumed_count=len(explicit_args) + 1,
                    element_tags=_propagated_element_tags(
                        concrete_overload,
                        concrete_args,
                        concrete_application.substitution,
                    ),
                    vectorised_target_ranks=(
                        concrete_application.vectorised_target_ranks
                    ),
                ),
                branch=branch.with_stack(T.TypeStack(base_stack)),
                call_arg_order=call_arg_order,
                callable_overload_index=callable_index,
            )
        )
    return candidates


def _prepare_element_call_branches(
    branch: _core.AnalysisBranch,
    overload: T.Overload,
    call_args: tuple[CallArgument, ...],
    has_modifier_args: bool,
    analyser: _core.Analyser,
) -> tuple[_core.ElementCallPreparation, ...]:
    """Prepare element call branches during static analysis."""
    plan = _element_call_argument_plan(overload, call_args, has_modifier_args)
    if plan is None:
        return ()
    current = _core.BranchSet((branch,))
    expressions, call_arg_order = plan
    for expression in expressions:
        current = analyser.analyse_block(current, expression)
        if not current:
            return ()
    return tuple(
        _core.ElementCallPreparation(prepared, call_arg_order) for prepared in current
    )


def _element_call_argument_plan(
    overload: T.Overload,
    call_args: tuple[CallArgument, ...],
    has_modifier_args: bool,
) -> tuple[tuple[tuple[ASTNode, ...], ...], tuple[int, ...]] | None:
    """Build the plan for element call argument during static analysis."""
    param_count = len(overload.params)
    if param_count == 0:
        return ((), ()) if not call_args else None
    param_names = overload.param_names or (None,) * param_count
    param_defaults = overload.param_defaults or (None,) * param_count
    if len(param_names) < param_count:
        param_names = (None,) * (param_count - len(param_names)) + param_names
    if len(param_defaults) < param_count:
        param_defaults = (None,) * (param_count - len(param_defaults)) + param_defaults

    modifier_indexes = (
        set(_modifier_param_indexes(overload.params)) if has_modifier_args else set()
    )
    assignments: list[CallArgument | tuple[ASTNode, ...] | None] = [None] * param_count
    cursor = 0

    for arg in call_args:
        if arg.name is not None:
            try:
                index = next(
                    candidate
                    for candidate, name in enumerate(param_names)
                    if name == arg.name
                )
            except StopIteration:
                return None
            if index in modifier_indexes or assignments[index] is not None:
                return None
            assignments[index] = arg
            continue

        while cursor < param_count and (
            cursor in modifier_indexes or assignments[cursor] is not None
        ):
            cursor += 1
        if cursor >= param_count:
            return None
        assignments[cursor] = arg
        cursor += 1

    ordered: list[tuple[ASTNode, ...]] = []
    current_slots: list[int] = []
    stack_sourced_slots: list[int] = []
    explicit_slots: list[int] = []
    for index in range(param_count):
        if index in modifier_indexes:
            continue
        assigned = assignments[index]
        if isinstance(assigned, CallArgument):
            if assigned.placeholder:
                stack_sourced_slots.append(index)
                continue
            ordered.append(assigned.value)
            explicit_slots.append(index)
            continue
        if assigned is not None:
            ordered.append(assigned)
            explicit_slots.append(index)
            continue
        default = param_defaults[index]
        if default is not None:
            ordered.append(cast("tuple[ASTNode, ...]", default))
            explicit_slots.append(index)
            continue
        stack_sourced_slots.append(index)
    current_slots.extend(stack_sourced_slots)
    current_slots.extend(explicit_slots)
    desired_slots = tuple(
        index for index in range(param_count) if index not in modifier_indexes
    )
    call_arg_order = tuple(current_slots.index(index) for index in desired_slots)
    identity = tuple(range(len(call_arg_order)))
    return tuple(ordered), (() if call_arg_order == identity else call_arg_order)


def _merge_element_arguments(
    params: tuple[T.Type, ...],
    modifier_indexes: tuple[int, ...],
    stack_args: tuple[T.Type, ...],
    modifiers: tuple[_core.ModifierArgumentAnalysis, ...],
) -> tuple[T.Type, ...]:
    """Merge element arguments during static analysis."""
    args: list[T.Type] = []
    stack_index = 0
    modifier_index = 0
    modifier_index_set = set(modifier_indexes)
    for index in range(len(params)):
        if index in modifier_index_set:
            args.append(modifiers[modifier_index].typ)
            modifier_index += 1
        else:
            args.append(stack_args[stack_index])
            stack_index += 1
    return tuple(args)


def _specialized_modifier_orders(
    params: tuple[T.Type, ...],
    modifier_indexes: tuple[int, ...],
    modifiers: tuple[_core.ModifierArgumentAnalysis, ...],
    substitution: dict[str, T.Type],
    ctx: T.Context,
    analyser: _core.Analyser | None = None,
) -> Iterator[tuple[dict[str, T.Type], tuple[_core.ModifierArgumentAnalysis, ...]]]:
    """Compute specialized modifier orders during static analysis."""
    if not modifier_indexes:
        yield substitution, ()
        return

    def rec(
        position: int,
        current_substitution: dict[str, T.Type],
        current_modifiers: tuple[_core.ModifierArgumentAnalysis, ...],
    ) -> Iterator[tuple[dict[str, T.Type], tuple[_core.ModifierArgumentAnalysis, ...]]]:
        """Recursively continue the specialized modifier orders algorithm."""
        if position == len(modifier_indexes):
            yield current_substitution, current_modifiers
            return
        param_index = modifier_indexes[position]
        expected = _substitute_branch_type(params[param_index], current_substitution)
        for modifier, modifier_substitution in _modifier_variants_for_expected(
            modifiers[position],
            expected,
            ctx,
            analyser,
        ):
            merged = _merge_substitutions(current_substitution, modifier_substitution)
            if merged is None:
                continue
            yield from rec(position + 1, merged, current_modifiers + (modifier,))

    yield from rec(0, substitution, ())


def _modifier_variants_for_expected(
    modifier: _core.ModifierArgumentAnalysis,
    expected: T.Type,
    ctx: T.Context,
    analyser: _core.Analyser | None = None,
) -> Iterator[tuple[_core.ModifierArgumentAnalysis, dict[str, T.Type]]]:
    """Compute modifier variants for expected during static analysis."""
    expected = T.normalize(expected)
    if not isinstance(
        expected,
        T.FunctionType,
    ) or _functions._is_bare_function_type(expected):
        if T.compatible(modifier.typ, expected, ctx):
            yield modifier, {}
        return

    if _function_has_union_parameter(expected):
        substitution = _branch_argument_substitution((modifier.typ,), (expected,), ctx)
        if substitution is not None:
            concrete_expected = _substitute_branch_type(expected, substitution)
            dispatch_plan = (
                _union_dispatch_plan_for_function(
                    modifier.typed_node,
                    concrete_expected,
                    ctx,
                )
                if isinstance(T.normalize(concrete_expected), T.FunctionType)
                else None
            )
            if (
                isinstance(T.normalize(concrete_expected), T.FunctionType)
                and T.compatible(
                    modifier.typ,
                    concrete_expected,
                    ctx,
                )
                and dispatch_plan is not None
            ):
                yield (
                    _core.ModifierArgumentAnalysis(
                        concrete_expected,
                        TypedFunctionNode(
                            modifier.typed_node.node,
                            concrete_expected,
                            modifier.typed_node.overloads,
                            dispatch_plan,
                        ),
                    ),
                    substitution,
                )
                return

    overloads = _contextual_modifier_overloads(modifier, expected, analyser, ctx)
    for overload in overloads:
        typ = T.normalize(overload.typ)
        if not isinstance(typ, T.FunctionType):
            continue
        substitution = _branch_argument_substitution((typ,), (expected,), ctx)
        if substitution is None:
            continue
        concrete_expected = _substitute_branch_type(expected, substitution)
        if not isinstance(T.normalize(concrete_expected), T.FunctionType):
            continue
        if not _function_overload_matches_type(overload, concrete_expected, ctx):
            continue
        yield (
            _core.ModifierArgumentAnalysis(
                concrete_expected,
                TypedFunctionNode(
                    modifier.typed_node.node,
                    concrete_expected,
                    (overload,),
                ),
            ),
            substitution,
        )


def _contextual_modifier_overloads(
    modifier: _core.ModifierArgumentAnalysis,
    expected: T.Type,
    analyser: _core.Analyser | None,
    ctx: T.Context,
) -> tuple[FunctionOverloadTyping, ...]:
    """Analyse deferred untyped modifier functions against concrete inputs."""
    expected = T.normalize(expected)
    if analyser is None or not isinstance(expected, T.FunctionType):
        return modifier.typed_node.overloads
    if expected.params is None:
        return modifier.typed_node.overloads

    resolved: list[FunctionOverloadTyping] = []
    deferred = False
    for typing in modifier.typed_node.overloads:
        overload = typing.overload
        if not (
            isinstance(overload, T.Overload)
            and isinstance(overload.call_site_body, tuple)
            and len(overload.call_site_body) == 2
        ):
            resolved.append(typing)
            continue
        deferred = True
        outer, node = overload.call_site_body
        if not isinstance(outer, _core.AnalysisBranch) or not isinstance(
            node,
            FunctionNode,
        ):
            continue
        for params in _modifier_call_param_variants(expected.params):
            analysis = analyser._analyse_function_at_call_site(outer, node, params)
            if analysis is None:
                continue
            compatible = tuple(
                candidate
                for candidate in analysis.overloads
                if _contextual_modifier_overload_matches(candidate, expected, ctx)
            )
            if compatible:
                resolved.extend(compatible)
                break

    if deferred:
        unique: list[FunctionOverloadTyping] = []
        for typing in resolved:
            if typing not in unique:
                unique.append(typing)
        return tuple(unique)
    return modifier.typed_node.overloads


def _contextual_modifier_overload_matches(
    overload: FunctionOverloadTyping,
    expected: T.FunctionType,
    ctx: T.Context,
) -> bool:
    """Return whether a contextual modifier overload matches its expected type."""
    typ = T.normalize(overload.typ)
    if not isinstance(typ, T.FunctionType):
        return False
    substitution = _branch_argument_substitution((typ,), (expected,), ctx)
    if substitution is None:
        return False
    concrete_expected = _substitute_branch_type(expected, substitution)
    if not isinstance(T.normalize(concrete_expected), T.FunctionType):
        return False
    return _function_overload_matches_type(overload, concrete_expected, ctx)


def _modifier_call_param_variants(
    params: tuple[T.Type, ...],
) -> tuple[tuple[T.Type, ...], ...]:
    """Return concrete and progressively scalarized modifier input shapes."""
    variants: list[tuple[T.Type, ...]] = [()]
    for param in params:
        choices = _modifier_param_rank_variants(param)
        variants = [prefix + (choice,) for prefix in variants for choice in choices]
    return tuple(variants)


def _modifier_param_rank_variants(typ: T.Type) -> tuple[T.Type, ...]:
    """Return a type plus lower-rank views usable for vectorized callables."""
    normalized = T.normalize(typ)
    if not isinstance(normalized, T.CollectionType):
        return (typ,)
    if not isinstance(normalized.rank, int):
        return (typ,)
    return (normalized.base,) + tuple(
        T.C(type(normalized), normalized.base, rank)
        for rank in range(1, normalized.rank + 1)
    )


def _merge_substitutions(
    left: dict[str, T.Type],
    right: dict[str, T.Type],
) -> dict[str, T.Type] | None:
    """Merge substitutions during static analysis."""
    merged = dict(left)
    for name, typ in right.items():
        existing = merged.get(name)
        if existing is not None and not T.same(existing, typ):
            return None
        merged[name] = typ
    return merged


def _function_has_union_parameter(typ: T.FunctionType) -> bool:
    """Return whether a function has a union parameter."""
    return typ.params is not None and any(
        isinstance(T.normalize(param), T.UnionType) for param in typ.params
    )


def _modifier_arity_matches(
    overloads: tuple[T.Overload, ...],
    modifier_args: tuple[_core.ModifierArgumentAnalysis, ...],
) -> bool:
    """Return the Boolean result of modifier arity matches during static analysis."""
    return len(modifier_args) in {
        len(_modifier_param_indexes(overload.params)) for overload in overloads
    }


def _specialize_modifier_arguments(
    applied: T.AppliedOverload,
    modifier_args: tuple[_core.ModifierArgumentAnalysis, ...],
    ctx: T.Context,
) -> tuple[TypedFunctionNode, ...]:
    """Specialize modifier arguments during static analysis."""
    if not modifier_args:
        return ()

    offset = len(applied.params) - len(applied.overload.params)
    if offset < 0:
        return tuple(item.typed_node for item in modifier_args)

    typed_nodes: list[TypedFunctionNode] = []
    for item, original_index in zip(
        modifier_args,
        _modifier_param_indexes(applied.overload.params),
        strict=True,
    ):
        index = offset + original_index
        expected = applied.params[index] if index < len(applied.params) else None
        expected = T.normalize(expected) if expected is not None else None
        if item.typed_node.dispatch_plan is not None:
            typed_nodes.append(item.typed_node)
            continue
        if isinstance(expected, T.FunctionType):
            overloads = tuple(
                overload
                for overload in item.typed_node.overloads
                if _function_overload_matches_type(overload, expected, ctx)
            )
            if overloads:
                typed_nodes.append(
                    TypedFunctionNode(item.typed_node.node, expected, overloads)
                )
                continue
        typed_nodes.append(item.typed_node)
    return tuple(typed_nodes)


def _union_dispatch_plan_for_function(
    function: TypedFunctionNode,
    expected: T.Type,
    ctx: T.Context,
) -> T.UnionDispatchPlan | None:
    """Compute union dispatch plan for function during static analysis."""
    expected = T.normalize(expected)
    if not isinstance(expected, T.FunctionType):
        return None
    overloads = tuple(
        typing.overload
        for typing in function.overloads
        if isinstance(typing.overload, T.Overload)
    )
    if len(overloads) != len(function.overloads):
        return None
    return T.union_dispatched_callable_plan(T.Overloads(*overloads), expected, ctx)


def _function_overload_matches_type(
    overload: FunctionOverloadTyping,
    expected: T.FunctionType,
    ctx: T.Context,
) -> bool:
    """Return whether a function overload matches the expected type."""
    typ = T.normalize(overload.typ)
    return isinstance(typ, T.FunctionType) and (
        T.same(typ, expected) or T.compatible(typ, expected, ctx)
    )


def _show_modifier_counts(overloads: tuple[T.Overload, ...]) -> str:
    """Format modifier counts during static analysis."""
    counts = sorted(
        {len(_modifier_param_indexes(overload.params)) for overload in overloads}
    )
    if len(counts) == 1:
        return str(counts[0])
    return " or ".join(str(count) for count in counts)


def _modifier_param_indexes(params: tuple[T.Type, ...]) -> tuple[int, ...]:
    """Compute modifier param indexes during static analysis."""
    return tuple(
        index for index, param in enumerate(params) if _is_callable_parameter(param)
    )


def _is_callable_parameter(param: T.Type) -> bool:
    """Return whether the value is callable parameter."""
    param = T.normalize(param)
    return isinstance(param, T.FunctionType) or _functions._is_bare_function_type(param)


def _unique_permutations(
    modifier_args: tuple[_core.ModifierArgumentAnalysis, ...],
) -> Iterator[tuple[_core.ModifierArgumentAnalysis, ...]]:
    """Compute unique permutations during static analysis."""
    seen: set[tuple[T.Type, ...]] = set()
    for candidate in permutations(modifier_args):
        key = tuple(item.typ for item in candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _apply_overload_to_branch(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    branch: _core.AnalysisBranch,
    ctx: T.Context,
    env: T.Environment | None = None,
    disambiguation: tuple[T.Type | None, ...] = (),
    analyser: _core.Analyser | None = None,
) -> _core.OverloadApplication | None:
    """Apply overload to branch during static analysis."""
    if _overload_needs_call_site_checking(overload):
        return _apply_call_site_checked_overload(
            overload,
            args,
            branch,
            ctx,
            env,
            disambiguation,
            analyser,
        )
    args = _row_views_for_arguments(args, overload.params, env)
    original_overload = overload
    rank_values = _initial_rank_values(overload.params, args)
    rank_values = _evaluate_where_clause(overload, args, rank_values)
    if rank_values is None:
        return None
    overload = _substitute_overload_ranks(overload, rank_values)
    substitution = _branch_argument_substitution(args, overload.params, ctx)
    if substitution is None:
        return None
    specialized_branch = _specialize_branch_arguments(branch, substitution)
    specialized_args = tuple(_substitute_branch_type(arg, substitution) for arg in args)
    attempt = T.try_apply_overload(
        overload,
        specialized_args,
        ctx,
        disambiguation=disambiguation,
    )
    applied = attempt.applied
    if applied is None:
        return None
    actual_returns = _apply_data_tag_flow(
        specialized_args,
        overload.returns,
        applied.actual_returns,
        ctx,
    )
    applied = T.AppliedOverload(
        original_overload,
        applied.substitution,
        applied.params,
        applied.returns,
        actual_returns,
        applied.scores,
        applied.vectorised,
        applied.vectorised_depths,
        tuple(sorted(rank_values.items())),
        element_tags=_propagated_element_tags(
            overload,
            specialized_args,
            applied.substitution,
        ),
        vectorised_target_ranks=applied.vectorised_target_ranks,
        runtime_static_values=applied.runtime_static_values,
    )
    return _core.OverloadApplication(applied, specialized_branch)


def _apply_tag_overlay(
    element: Symbol,
    args: tuple[T.Type, ...],
    applied: T.AppliedOverload,
    ctx: T.Context,
    env: T.Environment,
) -> T.AppliedOverload:
    """Apply tag overlay during static analysis."""
    matches: list[T.AppliedOverload] = []
    for overlay in env.overlays_for(element):
        candidate = T.try_apply_overload(overlay.overload, args, ctx).applied
        if candidate is None:
            continue
        actual_returns = _apply_data_tag_flow(
            args,
            overlay.overload.returns,
            candidate.actual_returns,
            ctx,
        )
        matches.append(
            T.AppliedOverload(
                applied.overload,
                candidate.substitution,
                applied.params,
                candidate.returns,
                actual_returns,
                applied.scores,
                applied.vectorised,
                applied.vectorised_depths,
                applied.rank_values,
                applied.runtime_consumed_count,
                applied.element_tags,
                vectorised_target_ranks=applied.vectorised_target_ranks,
                runtime_static_values=applied.runtime_static_values,
            )
        )
    if not matches:
        return applied
    return sorted(
        matches,
        key=lambda item: tuple(score.value for score in item.scores),
        reverse=True,
    )[0]


def _apply_overload_via_unit_overlay(
    element: Symbol,
    overload: T.Overload,
    args: tuple[T.Type, ...],
    branch: _core.AnalysisBranch,
    ctx: T.Context,
    env: T.Environment,
    disambiguation: tuple[T.Type | None, ...] = (),
    analyser: _core.Analyser | None = None,
) -> _core.OverloadApplication | None:
    """Apply an implementation through a matching unit-tag overlay.

    Unit values deliberately cannot satisfy ordinary untagged parameters. A
    matching overlay is the explicit permission that allows the implementation
    to consume the underlying value while the overlay controls the unit tag's
    return contract. Only the overlay's own unit tag is erased for this check;
    unrelated units remain protected.
    """
    for overlay in env.overlays_for(element):
        if not ctx.is_unit_tag(overlay.tag):
            continue
        if T.try_apply_overload(overlay.overload, args, ctx).applied is None:
            continue
        erased_args = tuple(
            _erase_overlay_owned_tag(arg, overlay.tag.text) for arg in args
        )
        candidate = _apply_overload_to_branch(
            overload,
            erased_args,
            branch,
            ctx,
            env,
            disambiguation,
            analyser,
        )
        if candidate is not None:
            return candidate
    return None


def _erase_overlay_owned_tag(typ: T.Type, name: str) -> T.Type:
    """Erase one overlay-owned tag without laundering any other unit tag."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        kept = tuple(tag for tag in typ.tags if tag.name != name)
        inner = _erase_overlay_owned_tag(typ.inner, name)
        return T.Tagged(inner, *kept, exact=typ.exact) if kept or typ.exact else inner
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _erase_overlay_owned_tag(typ.base, name), typ.rank)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_erase_overlay_owned_tag(arg, name) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_erase_overlay_owned_tag(item, name) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_erase_overlay_owned_tag(item, name) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_erase_overlay_owned_tag(item, name) for item in typ.params))
    if isinstance(typ, T.ExactType):
        return T.Exact(_erase_overlay_owned_tag(typ.inner, name))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_erase_overlay_owned_tag(typ.inner, name))
    return typ


def _apply_call_site_checked_overload(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    branch: _core.AnalysisBranch,
    ctx: T.Context,
    env: T.Environment | None,
    disambiguation: tuple[T.Type | None, ...],
    analyser: _core.Analyser | None,
) -> _core.OverloadApplication | None:
    """Apply call site checked overload during static analysis."""
    args = _row_views_for_arguments(args, overload.params, env)
    if len(args) != len(overload.params):
        return None
    if not _call_site_explicit_args_match(overload.params, args, ctx):
        return None
    if disambiguation and len(disambiguation) != len(args):
        return None

    for extra_count in range(len(branch.stack) + 1):
        stack_args = branch.stack.items[-extra_count:] if extra_count else ()
        call_params = stack_args + args
        concrete = _call_site_checked_overload_signature(
            overload, call_params, ctx, analyser
        )
        if concrete is None or len(concrete.params) < len(args):
            continue
        consumed_count = _call_site_consumed_count(overload, concrete, extra_count)
        if consumed_count is None:
            continue
        concrete_stack_count = len(concrete.params) - len(args)
        if concrete_stack_count < 0:
            continue
        if concrete_stack_count <= len(branch.stack):
            concrete_stack_args = (
                branch.stack.items[-concrete_stack_count:]
                if concrete_stack_count
                else ()
            )
            result_branch = branch.with_stack(branch.stack.pop(consumed_count))
        else:
            stack_params = concrete.params[:concrete_stack_count]
            sourced = branch.source_arguments(stack_params)
            if sourced is None:
                continue
            concrete_stack_args, sourced_branch = sourced
            preserved = concrete_stack_args[: concrete_stack_count - consumed_count]
            result_branch = sourced_branch.push(*preserved)
        concrete_args = concrete_stack_args + args
        if len(concrete.params) != len(concrete_args):
            continue
        rank_values = _initial_rank_values(concrete.params, concrete_args)
        rank_values = _evaluate_where_clause(concrete, concrete_args, rank_values)
        if rank_values is None:
            continue
        concrete = _substitute_overload_ranks(concrete, rank_values)
        candidate = T.try_apply_overload(concrete, concrete_args, ctx).applied
        if candidate is None:
            continue
        actual_returns = _apply_data_tag_flow(
            concrete_args,
            concrete.returns,
            candidate.actual_returns,
            ctx,
        )
        applied = T.AppliedOverload(
            overload,
            candidate.substitution,
            concrete.params,
            concrete.returns,
            actual_returns,
            candidate.scores,
            candidate.vectorised,
            candidate.vectorised_depths,
            tuple(sorted(rank_values.items())),
            consumed_count + len(args),
            element_tags=_propagated_element_tags(
                concrete,
                concrete_args,
                candidate.substitution,
            ),
            vectorised_target_ranks=candidate.vectorised_target_ranks,
            runtime_static_values=concrete.runtime_static_values,
        )
        return _core.OverloadApplication(applied, result_branch)
    return None


def _overload_needs_call_site_checking(overload: T.Overload) -> bool:
    """Return whether an overload requires call-site checking."""
    return any(
        _functions._is_call_site_checked_param(param)
        for param in overload.params
    )


def _call_site_explicit_args_match(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return whether explicit call arguments match an overload."""
    return all(
        _functions._call_site_placeholder_accepts(param, arg, ctx)
        for param, arg in zip(params, args, strict=True)
    )


def _call_site_checked_overload_signature(
    overload: T.Overload,
    call_params: tuple[T.Type, ...],
    ctx: T.Context,
    analyser: _core.Analyser | None,
) -> T.Overload | None:
    """Build the signature for call site checked overload during static analysis."""
    if callable(overload.call_site_body):
        if (
            analyser is not None
            and getattr(overload.call_site_body, "__name__", "") == "_call_call_site"
            and call_params
        ):
            function_type = call_params[-1]
            explicit = call_params[:-1]
            deferred = False
            for candidate in _functions._callable_overloads(function_type):
                if not (
                    isinstance(candidate.call_site_body, tuple)
                    and len(candidate.call_site_body) == 2
                ):
                    continue
                deferred = True
                outer, node = candidate.call_site_body
                if not isinstance(outer, _core.AnalysisBranch) or not isinstance(
                    node, FunctionNode
                ):
                    continue
                analysis = analyser._analyse_function_at_call_site(
                    outer,
                    node,
                    explicit,
                )
                if analysis is None:
                    continue
                candidates = _functions._callable_overloads(analysis.typ)
                if len(candidates) != 1:
                    continue
                concrete = candidates[0]
                return T.Overload(
                    (*concrete.params, function_type),
                    concrete.returns,
                    call_site_body=len(concrete.params),
                )
            if deferred:
                return None
        return overload.call_site_body(call_params)
    if overload.call_site_body is not None and analyser is not None:
        outer, node = overload.call_site_body
        analysis = analyser._analyse_function_at_call_site(outer, node, call_params)
        if analysis is None:
            return None
        overloads = _functions._callable_overloads(analysis.typ)
        return overloads[0] if len(overloads) == 1 else None
    if len(call_params) < len(overload.params):
        return None
    explicit = call_params[-len(overload.params) :] if overload.params else ()
    if not _call_site_explicit_args_match(overload.params, explicit, ctx):
        return None
    return T.Overload(
        params=call_params,
        returns=overload.returns,
        generic_constraints=overload.generic_constraints,
        where_clause=overload.where_clause,
        param_names=(None,) * (len(call_params) - len(overload.params))
        + overload.param_names,
        element_tags=overload.element_tags,
        annotation_error=overload.annotation_error,
        annotation_warning=overload.annotation_warning,
        param_defaults=(None,) * len(call_params),
    )


def _call_site_consumed_count(
    overload: T.Overload,
    concrete: T.Overload,
    extra_count: int,
) -> int | None:
    """Compute call site consumed count during static analysis."""
    consumed = (
        concrete.call_site_body
        if isinstance(concrete.call_site_body, int)
        else len(concrete.params) - len(overload.params)
    )
    if consumed < 0:
        return None
    return consumed


def _propagated_element_tags(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    substitution: dict[str, T.Type] | None = None,
) -> frozenset[T.ElementTag]:
    """Compute propagated element tags during static analysis."""
    tags = {
        tag
        for tag in _substitute_branch_element_tags(
            overload.element_tags,
            substitution or {},
        )
        if not tag.absent
    }
    for arg in args:
        arg = T.normalize(arg)
        if isinstance(arg, T.FunctionType):
            tags.update(tag for tag in arg.element_tags if not tag.absent)
        elif isinstance(arg, T.OverloadSetType):
            for candidate in arg.overloads:
                tags.update(tag for tag in candidate.element_tags if not tag.absent)
    return frozenset(tags)


def _initial_rank_values(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
) -> dict[str, int]:
    """Collect the values for initial rank during static analysis."""
    values: dict[str, int] = {}
    for param, arg in zip(params, args, strict=False):
        _collect_rank_values(param, arg, values)
    return values


def _collect_rank_values(
    pattern: T.Type,
    actual: T.Type,
    values: dict[str, int],
) -> None:
    """Collect rank values during static analysis."""
    pattern = T.normalize(pattern)
    actual = T.normalize(actual)
    if isinstance(pattern, T.CollectionType) and isinstance(actual, T.CollectionType):
        if isinstance(pattern.rank, T.RankVariable) and isinstance(actual.rank, int):
            values.setdefault(pattern.rank.name, actual.rank)
        _collect_rank_values(pattern.base, actual.base, values)
    elif isinstance(pattern, T.NominalType) and isinstance(actual, T.NominalType):
        for left, right in zip(pattern.args, actual.args, strict=False):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.FunctionType) and isinstance(actual, T.FunctionType):
        for left, right in zip(
            pattern.params + pattern.returns,
            actual.params + actual.returns,
            strict=False,
        ):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.TupleType) and isinstance(actual, T.TupleType):
        for left, right in zip(pattern.params, actual.params, strict=False):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.VariadicTupleType) and isinstance(actual, T.TupleType):
        _match_variadic_tuple_types(
            pattern,
            actual,
            lambda left, right: _collect_rank_values(left, right, values) or True,
        )
    elif isinstance(pattern, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_rank_values(pattern.inner, actual, values)
    elif isinstance(actual, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_rank_values(pattern, actual.inner, values)


def _match_variadic_tuple_types(
    pattern: T.VariadicTupleType,
    actual: T.TupleType,
    match: Callable[[T.Type, T.Type], bool],
) -> bool:
    """Return whether variadic tuple types match."""
    def rec(pattern_index: int, actual_index: int) -> bool:
        """Recursively continue the match variadic tuple types algorithm."""
        if pattern_index == len(pattern.items):
            return actual_index == len(actual.params)
        item = pattern.items[pattern_index]
        if item.repeated:
            if rec(pattern_index + 1, actual_index):
                return True
            for index in range(actual_index, len(actual.params)):
                if not match(item.typ, actual.params[index]):
                    return False
                if rec(pattern_index + 1, index + 1):
                    return True
            return False
        return (
            actual_index < len(actual.params)
            and match(
                item.typ,
                actual.params[actual_index],
            )
            and rec(pattern_index + 1, actual_index + 1)
        )

    return rec(0, 0)


def _evaluate_where_clause(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    rank_values: dict[str, int],
) -> dict[str, int] | None:
    """Evaluate where clause during static analysis."""
    if not overload.where_clause:
        return rank_values
    variables: dict[str, StaticValue] = {
        name: value for name, value in rank_values.items()
    }
    for param_name, arg in zip(overload.param_names, args, strict=False):
        if param_name is not None:
            variables[param_name.text] = arg
    stack: list[StaticValue] = []
    for node in overload.where_clause:
        if not _static_eval_node(node, stack, variables):
            return None
    result = dict(rank_values)
    for name, value in variables.items():
        if isinstance(value, int) and not isinstance(value, bool):
            result[name] = value
    return result


StaticValue = int | bool | T.Type | tuple[T.Type, ...]


def _static_eval_node(
    node: ASTNode,
    stack: list[StaticValue],
    variables: dict[str, StaticValue],
) -> bool:
    """Return the Boolean result of static eval node during static analysis."""
    match node:
        case NumberLiteralNode(value):
            stack.append(int(value))
            return True
        case GetVariableNode(name):
            value = variables.get(name.text)
            if value is None:
                return False
            stack.append(value)
            return True
        case SetVariableNode(name):
            if not stack:
                return False
            variables[name.text] = stack.pop()
            return True
        case FieldAccessNode(name):
            if not stack:
                return False
            value = stack.pop()
            if isinstance(value, T.FunctionType):
                if value.params is None or value.returns is None:
                    return False
                match name.text:
                    case "inputs":
                        stack.append(value.params)
                        return True
                    case "outputs":
                        stack.append(value.returns)
                        return True
                    case "arity":
                        stack.append(len(value.params))
                        return True
                    case "multiplicity":
                        stack.append(len(value.returns))
                        return True
            return False
        case ElementNode(name, _, _, call_args):
            if call_args:
                for arg in call_args:
                    if arg.placeholder or arg.name is not None:
                        return False
                    for value_node in arg.value:
                        if not _static_eval_node(value_node, stack, variables):
                            return False
            return _static_eval_element(name.text, stack)
        case _:
            return False


def _static_eval_element(name: str, stack: list[StaticValue]) -> bool:
    """Return the Boolean result of static eval element during static analysis."""
    def pop_truthy_values(count: int) -> tuple[int | bool, ...] | None:
        """Collect the values for pop truthy during static analysis."""
        if len(stack) < count:
            return None
        values = tuple(stack[-count:])
        if not all(isinstance(value, (int, bool)) for value in values):
            return None
        del stack[-count:]
        return values

    if name in {"+", "-", "*", "max", "min", "<", ">", "<=", ">=", "==", "!="}:
        if len(stack) < 2:
            return False
        right = stack.pop()
        left = stack.pop()
        if name in {"==", "!="}:
            equal = left == right
            stack.append(equal if name == "==" else not equal)
            return True
        if not (
            isinstance(left, int)
            and not isinstance(left, bool)
            and isinstance(right, int)
            and not isinstance(right, bool)
        ):
            return False
        match name:
            case "+":
                stack.append(left + right)
            case "-":
                stack.append(left - right)
            case "*":
                stack.append(left * right)
            case "max":
                stack.append(max(left, right))
            case "min":
                stack.append(min(left, right))
            case "<":
                stack.append(left < right)
            case ">":
                stack.append(left > right)
            case "<=":
                stack.append(left <= right)
            case ">=":
                stack.append(left >= right)
        return True
    if name == "length":
        if not stack:
            return False
        value = stack.pop()
        if isinstance(value, T.TupleType):
            stack.append(len(value.params))
            return True
        if isinstance(value, tuple):
            stack.append(len(value))
            return True
        return False
    if name == "and":
        values = pop_truthy_values(2)
        if values is None:
            return False
        stack.append(bool(values[0]) and bool(values[1]))
        return True
    if name == "or":
        values = pop_truthy_values(2)
        if values is None:
            return False
        stack.append(bool(values[0]) or bool(values[1]))
        return True
    if name == "not":
        values = pop_truthy_values(1)
        if values is None:
            return False
        stack.append(not bool(values[0]))
        return True
    if name == "?":
        if not stack:
            return False
        return bool(stack.pop())
    if name == "dup":
        if not stack:
            return False
        stack.append(stack[-1])
        return True
    if name == "pop":
        if not stack:
            return False
        stack.pop()
        return True
    if name == "swap":
        if len(stack) < 2:
            return False
        stack[-1], stack[-2] = stack[-2], stack[-1]
        return True
    return False


def _substitute_overload_ranks(
    overload: T.Overload,
    ranks: dict[str, int],
) -> T.Overload:
    """Substitute overload ranks during static analysis."""
    return _functions._transform_overload_types(
        overload,
        lambda typ: _substitute_rank_values(typ, ranks),
        element_tags=frozenset(
            _substitute_rank_values_in_element_tags(overload.element_tags, ranks)
        ),
    )


def _substitute_rank_values(typ: T.Type, ranks: dict[str, int]) -> T.Type:
    """Substitute rank values during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.CollectionType):
        rank = typ.rank
        if isinstance(rank, T.RankVariable):
            solved = ranks.get(rank.name)
            rank = solved if solved is not None else rank
        return T.C(type(typ), _substitute_rank_values(typ.base, ranks), rank)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(_substitute_rank_values(arg, ranks) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_rank_values(item, ranks) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_rank_values(item, ranks) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_substitute_rank_values(item, ranks) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(_substitute_rank_values(item.typ, ranks), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _substitute_rank_values_in_element_tags(typ.element_tags, ranks),
            )
        return T.Fn(
            (_substitute_rank_values(item, ranks) for item in typ.params),
            (_substitute_rank_values(item, ranks) for item in typ.returns),
            _substitute_rank_values_in_element_tags(typ.element_tags, ranks),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(
            _substitute_rank_values(typ.inner, ranks),
            *typ.tags,
            exact=typ.exact,
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_rank_values(typ.inner, ranks))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_rank_values(typ.inner, ranks))
    return typ


def _substitute_rank_values_in_element_tags(
    tags: frozenset[T.ElementTag],
    ranks: dict[str, int],
) -> tuple[T.ElementTag, ...]:
    """Substitute rank values in element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_rank_values(arg, ranks) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _row_views_for_arguments(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    env: T.Environment | None,
) -> tuple[T.Type, ...]:
    """Determine the arguments for row views for during static analysis."""
    if env is None:
        return args
    return tuple(
        _row_view_for_argument(arg, param, env)
        for arg, param in zip(args, params, strict=True)
    )


def _row_view_for_argument(
    arg: T.Type,
    param: T.Type,
    env: T.Environment,
) -> T.Type:
    """Compute row view for argument during static analysis."""
    arg = T.normalize(arg)
    param = T.normalize(param)
    if not isinstance(param, T.RowType):
        return arg
    if isinstance(arg, T.RowType):
        return arg
    if not isinstance(arg, T.NominalType):
        return arg
    definition = env.lookup_object(arg.name)
    if definition is None:
        return arg
    return T.Row(
        arg,
        *(
            T.Field(attribute.name, attribute.typ)
            for attribute in definition.attributes
            if attribute.access.text in {"public", "readable"}
        ),
    )


def _specialize_branch_arguments(
    branch: _core.AnalysisBranch,
    substitution: dict[str, T.Type],
) -> _core.AnalysisBranch:
    """Specialize branch arguments during static analysis."""
    for name, typ in substitution.items():
        if _functions._contains_named_type_var(typ, name):
            continue
        branch = branch.refine_type(T.V(name), typ)
    return branch


def _apply_data_tag_flow(
    args: tuple[T.Type, ...],
    declared_returns: tuple[T.Type, ...],
    actual_returns: tuple[T.Type, ...],
    ctx: T.Context,
) -> tuple[T.Type, ...]:
    """Strip implicit tags that are not preserved by the chosen signature."""
    explicit_tags = tuple(_explicit_tags(ret) for ret in declared_returns)
    return tuple(
        _strip_implicit_computed_tags(
            ret,
            explicit_tags[index] if index < len(explicit_tags) else frozenset(),
            ctx,
        )
        for index, ret in enumerate(actual_returns)
    )


def _explicit_tags(typ: T.Type) -> frozenset[T.DataTag]:
    """Compute explicit tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return typ.tags | _explicit_tags(typ.inner)
    if isinstance(typ, T.CollectionType):
        return _explicit_tags(typ.base)
    if isinstance(typ, T.UnionType):
        result: set[T.DataTag] = set()
        for item in typ.items:
            result.update(_explicit_tags(item))
        return frozenset(result)
    return frozenset()


def _strip_implicit_computed_tags(
    typ: T.Type,
    explicit_tags: frozenset[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute strip implicit computed tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        kept = tuple(tag for tag in typ.tags if tag in explicit_tags)
        inner = _strip_implicit_computed_tags(typ.inner, explicit_tags, ctx)
        if typ.exact:
            return T.Tagged(inner, *kept, exact=True)
        return _with_data_tags(inner, kept, ctx) if kept else inner
    if isinstance(typ, T.CollectionType):
        return T.C(
            type(typ),
            _strip_implicit_computed_tags(typ.base, explicit_tags, ctx),
            typ.rank,
        )
    if isinstance(typ, T.UnionType):
        return T.U(
            *(
                _strip_implicit_computed_tags(item, explicit_tags, ctx)
                for item in typ.items
            )
        )
    return typ


def _with_data_tags(
    typ: T.Type,
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute with data tags during static analysis."""
    existing: set[T.DataTag] = set()
    exact = False
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        existing.update(typ.tags)
        exact = typ.exact
        typ = typ.inner
    for tag in tags:
        existing = {
            item
            for item in existing
            if Symbol(item.name) not in ctx.tag_disjoints(tag.name)
        }
        existing.add(tag)
        parent = ctx.tag_parent(tag.name)
        if parent is not None:
            existing.add(T.DataTag(parent.text, tag.depth))
    return T.Tagged(typ, *sorted(existing), exact=exact) if existing or exact else typ


def _remove_data_tag(typ: T.Type, tag: T.DataTag) -> T.Type | None:
    """Remove data tag during static analysis."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.TaggedType):
        return None
    existing = set(typ.tags)
    positive = T.DataTag(tag.name, tag.depth)
    if positive not in existing:
        return None
    existing.remove(positive)
    return (
        T.Tagged(typ.inner, *sorted(existing), exact=typ.exact)
        if existing or typ.exact
        else typ.inner
    )


def _show_tag(tag: T.DataTag) -> str:
    """Format tag during static analysis."""
    prefix = "#!" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"


def _validator_overload_ok(overload: T.Overload, ctx: T.Context) -> bool:
    """Return the Boolean result of validator overload ok during static analysis."""
    return len(overload.returns) == 1 and T.assignable(
        overload.returns[0],
        T.WithTag(T.Number, "boolean"),
        ctx,
    )


def _static_validator_result(body: tuple[TypedNode, ...]) -> bool | None:
    """Compute the result for static validator during static analysis."""
    if len(body) != 1:
        return None
    node = body[0].node
    if isinstance(node, ElementNode):
        if node.name == Symbol("true"):
            return True
        if node.name == Symbol("false"):
            return False
    return None


def _disjoint_data_tags(
    typ: T.Type,
    ctx: T.Context,
) -> tuple[Symbol, Symbol] | None:
    """Compute disjoint data tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        positive = [Symbol(tag.name) for tag in typ.tags if not tag.absent]
        seen: set[Symbol] = set()
        for tag in positive:
            conflict = next(
                (name for name in seen if name in ctx.tag_disjoints(tag)),
                None,
            )
            if conflict is not None:
                return conflict, tag
            seen.add(tag)
        return _disjoint_data_tags(typ.inner, ctx)
    if isinstance(typ, T.CollectionType):
        return _disjoint_data_tags(typ.base, ctx)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    return None


def _type_rank(typ: T.Type) -> int:
    """Determine the collection rank for type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _type_rank(typ.inner)
    if isinstance(typ, T.CollectionType):
        return typ.rank
    return 0


def _refine_branch_like(
    branch: _core.AnalysisBranch,
    refined: _core.AnalysisBranch,
) -> _core.AnalysisBranch:
    """Refine branch like during static analysis."""
    substitution = _branch_pair_substitution(branch.inputs, refined.inputs)
    if substitution is None:
        return replace(
            branch,
            element_tags=refined.element_tags,
            data_element_uses=refined.data_element_uses,
        )
    return replace(
        _specialize_branch_arguments(branch, substitution),
        element_tags=refined.element_tags,
        data_element_uses=refined.data_element_uses,
    )


def _branch_pair_substitution(
    source: tuple[T.Type, ...],
    target: tuple[T.Type, ...],
) -> dict[str, T.Type] | None:
    """Compute branch pair substitution during static analysis."""
    if len(source) != len(target):
        return None
    substitution: dict[str, T.Type] = {}
    for left, right in zip(source, target, strict=True):
        constraints = _solve_branch_argument(left, right, T.Context())
        if constraints is None:
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution


def _branch_argument_substitution(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute branch argument substitution during static analysis."""
    substitution: dict[str, T.Type] = {}
    for arg, param in zip(args, params, strict=True):
        arg = _substitute_branch_type(arg, substitution)
        param = _substitute_branch_type(param, substitution)
        constraints = _solve_branch_argument(arg, param, ctx)
        if constraints is None or (
            not constraints and _functions._contains_type_var(param)
        ):
            constraints = _solve_type_argument(arg, param, ctx)
        if constraints is None:
            if T.compatible(arg, param, ctx):
                continue
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution


def _solve_type_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context | None = None,
) -> dict[str, T.Type] | None:
    """Compute solve type argument during static analysis."""
    solved = T._solve(param, arg, ctx)
    if solved is None:
        return None
    substitution: dict[str, T.Type] = {}
    for name, values in solved.items():
        combined = T._combine_all(values, ctx)
        if combined is None:
            return None
        substitution[name] = combined
    return substitution


def _solve_branch_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute solve branch argument during static analysis."""
    constraints: dict[str, T.Type] = {}

    def bind(name: str, typ: T.Type) -> bool:
        """Bind one inferred value during static analysis."""
        previous = constraints.get(name)
        if previous is None:
            constraints[name] = typ
            return True
        return T.same(previous, typ)

    def rec(actual: T.Type, expected: T.Type) -> bool:
        """Recursively continue the solve branch argument algorithm."""
        actual = T.normalize(actual)
        expected = T.normalize(expected)
        if isinstance(actual, T.VarType):
            if _functions._contains_named_type_var(expected, actual.name):
                return True
            return bind(actual.name, expected)
        if isinstance(expected, T.VarType):
            if _functions._contains_named_type_var(actual, expected.name):
                return True
            return bind(expected.name, actual)
        if (
            isinstance(actual, T.FunctionType)
            and isinstance(expected, T.FunctionType)
            and actual.params is not None
            and actual.returns is not None
            and expected.params is not None
            and expected.returns is not None
            and (
                _functions._contains_type_var(actual)
                or _functions._contains_type_var(expected)
            )
        ):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if T.compatible(actual, expected, ctx):
            return True
        if isinstance(actual, T.RowType):
            if isinstance(expected, T.RowType):
                if not rec(actual.base, expected.base):
                    return False
                expected_fields = {field.name: field.typ for field in expected.fields}
                for field in actual.fields:
                    expected_field = expected_fields.get(field.name)
                    if expected_field is None or not rec(field.typ, expected_field):
                        return False
                return True
            return rec(actual.base, expected)
        if isinstance(actual, T.NominalType) and isinstance(expected, T.NominalType):
            return (
                actual.name == expected.name
                and len(actual.args) == len(expected.args)
                and all(
                    rec(left, right)
                    for left, right in zip(actual.args, expected.args, strict=True)
                )
            )
        if isinstance(actual, T.CollectionType) and isinstance(
            expected,
            T.CollectionType,
        ):
            return (
                type(actual) is type(expected)
                and actual.rank == expected.rank
                and rec(actual.base, expected.base)
            )
        if isinstance(actual, T.CollectionType):
            return rec(actual.base, expected)
        if isinstance(actual, T.FunctionType) and isinstance(expected, T.FunctionType):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if isinstance(actual, T.TupleType) and isinstance(expected, T.TupleType):
            return len(actual.params) == len(expected.params) and all(
                rec(left, right)
                for left, right in zip(actual.params, expected.params, strict=True)
            )
        if isinstance(actual, T.TupleType) and isinstance(
            expected,
            T.VariadicTupleType,
        ):
            return _match_variadic_tuple_types(
                expected,
                actual,
                lambda left, right: rec(right, left),
            )
        return False

    return constraints if rec(arg, param) else None


def _substitute_branch_type(typ: T.Type, substitution: dict[str, T.Type]) -> T.Type:
    """Substitute branch type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return substitution.get(typ.name, typ)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_substitute_branch_type(arg, substitution) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(
            *(_substitute_branch_type(item, substitution) for item in typ.params)
        )
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(
                    _substitute_branch_type(item.typ, substitution),
                    item.repeated,
                )
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _substitute_branch_type(typ.base, substitution),
            *(
                T.Field(
                    field.name,
                    _substitute_branch_type(field.typ, substitution),
                )
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _substitute_branch_type(typ.base, substitution), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _substitute_branch_element_tags(typ.element_tags, substitution),
            )
        return T.Fn(
            (_substitute_branch_type(item, substitution) for item in typ.params),
            (_substitute_branch_type(item, substitution) for item in typ.returns),
            _substitute_branch_element_tags(typ.element_tags, substitution),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(
            _substitute_branch_type(typ.inner, substitution),
            *typ.tags,
            exact=typ.exact,
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_branch_type(typ.inner, substitution))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_branch_type(typ.inner, substitution))
    return typ


def _substitute_branch_element_tags(
    tags: frozenset[T.ElementTag],
    substitution: dict[str, T.Type],
) -> tuple[T.ElementTag, ...]:
    """Substitute branch element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_branch_type(arg, substitution) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _dominates(
    left: tuple[T.Specificity, ...],
    right: tuple[T.Specificity, ...],
) -> bool:
    """Return the Boolean result of dominates during static analysis."""
    if len(left) != len(right):
        return False
    saw_strict = False
    for a, b in zip(left, right, strict=True):
        if a.value > b.value:
            return False
        if a.value < b.value:
            saw_strict = True
    return saw_strict


def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    """Determine the type of returns result during static analysis."""
    if len(returns) == 1:
        return returns[0]
    return None


def _consistent_function_returns(
    function: TypedFunctionNode,
) -> tuple[T.Type, ...] | None:
    """Determine the return types for consistent function during static analysis."""
    returns: tuple[T.Type, ...] | None = None
    for overload in function.overloads:
        typ = overload.typ
        if not isinstance(typ, T.FunctionType) or typ.returns is None:
            return None
        current = tuple(typ.returns)
        if returns is None:
            returns = current
            continue
        if len(returns) != len(current) or not all(
            T.same(left, right)
            for left, right in zip(returns, current, strict=True)
        ):
            return None
    return returns


def _single_function_return(function: TypedFunctionNode) -> T.Type | None:
    """Compute single function return during static analysis."""
    returns = _consistent_function_returns(function)
    if returns is None or len(returns) != 1:
        return None
    return returns[0]
