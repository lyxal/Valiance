"""Element call planning, overload application, and tag-flow helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from itertools import count, permutations
from typing import cast

import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
from valiance.asts import (
    ASTNode,
    CallArgument,
    ElementNode,
    FunctionNode,
    GetVariableNode,
    FunctionOverloadTyping,
    TypedFunctionNode,
    TypedNode,
)
from valiance.vtypes.symbols import Symbol

from .. import analyser as _core
from . import callable_values as _functions
from ..support import analysis_utils as _utils


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
    """Return true; exact-shape filtering is owned by overload application."""
    return len(args) == len(params)

def _analysis_type_is_scalar(
    typ: T.Type,
    scalar_generics: frozenset[str],
) -> bool:
    """Return whether analysis proves a type has rank zero."""
    typ = T.normalize(typ)
    if isinstance(typ, T.ExactType):
        return True
    if isinstance(typ, (T.TaggedType, T.NoVecType)):
        return _analysis_type_is_scalar(typ.inner, scalar_generics)
    if isinstance(typ, T.VarType):
        return typ.name in scalar_generics
    if isinstance(typ, T.CollectionType):
        return False
    if isinstance(typ, T.UnionType):
        return all(
            _analysis_type_is_scalar(item, scalar_generics) for item in typ.items
        )
    if isinstance(typ, T.IntersectionType):
        return any(
            _analysis_type_is_scalar(item, scalar_generics) for item in typ.items
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
            for (
                specialized_args,
                specialized_popped,
            ) in _contextual_stack_argument_variants(
                args,
                overload.params,
                popped,
                ctx,
                analyser,
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
    elif isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
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
        normalized.overloads if isinstance(normalized, T.OverloadSetType) else ()
    )
    if not overloads:
        return None
    source_nodes = tuple(
        overload.call_site_body[1]
        for overload in overloads
        if isinstance(overload.call_site_body, tuple)
        and len(overload.call_site_body) == 2
        and isinstance(overload.call_site_body[1], FunctionNode)
    )
    for typed_node in reversed(branch.typed_body):
        if not isinstance(typed_node, TypedFunctionNode):
            continue
        if T.same(typed_node.typ, typ) or any(
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
    env: T.Environment | None = None,
    analyser: _core.Analyser | None = None,
) -> list[_core.CallCandidate]:
    """Collect viable candidates for call element during static analysis."""
    candidates: list[_core.CallCandidate] = []
    if disambiguation and len(disambiguation) != len(explicit_args):
        return candidates
    application_branch = branch.with_stack(T.TypeStack(base_stack))
    for callable_index, callable_overload in enumerate(
        _functions._callable_overloads(function_type)
    ):
        declared = _call_site_static_overload(callable_overload)
        uses_static_values = bool(
            declared.where_clause
            or static_where.rank_variable_names(declared.params + declared.returns)
        )
        if uses_static_values:
            callable_result = _apply_overload_to_branch(
                callable_overload,
                explicit_args,
                application_branch,
                ctx,
                env,
                disambiguation,
                analyser,
            )
            if callable_result is None:
                continue
            callable_application = callable_result.applied
            result_branch = callable_result.branch
        else:
            callable_application = T.try_apply_overload(
                callable_overload,
                explicit_args,
                ctx,
                disambiguation=disambiguation,
            ).applied
            if callable_application is None:
                continue
            result_branch = application_branch
        concrete_function_type = T.Fn(
            callable_application.params,
            callable_application.actual_returns,
            callable_application.element_tags,
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
            callable_application.returns,
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
                    callable_application.rank_values,
                    runtime_consumed_count=len(explicit_args) + 1,
                    element_tags=_propagated_element_tags(
                        concrete_overload,
                        concrete_args,
                        concrete_application.substitution,
                    ),
                    vectorised_target_ranks=(
                        concrete_application.vectorised_target_ranks
                    ),
                    runtime_static_values=(
                        "__call_static__",
                        callable_application.vectorised,
                        callable_application.vectorised_depths,
                        callable_application.vectorised_target_ranks,
                        *callable_application.runtime_static_values,
                    ),
                ),
                branch=result_branch,
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
    implicit_stack_slots: list[int] = []
    placeholder_slots: list[int] = []
    explicit_slots: list[int] = []
    for index in range(param_count):
        if index in modifier_indexes:
            continue
        assigned = assignments[index]
        if isinstance(assigned, CallArgument):
            if assigned.placeholder:
                placeholder_slots.append(index)
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
        implicit_stack_slots.append(index)
    # Values omitted from the written call remain at the deep end of the
    # consumed stack segment. Explicit placeholders then fill from the top
    # right-to-left, which is their left-to-right order in the segment.
    current_slots.extend(implicit_stack_slots)
    current_slots.extend(placeholder_slots)
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
        original_expected = params[param_index]
        specialized_expected = _substitute_branch_type(
            original_expected,
            current_substitution,
        )
        expectations = (specialized_expected,)
        if (
            not T.same(original_expected, specialized_expected)
            and all(
                not _functions._contains_type_var(value)
                for value in current_substitution.values()
            )
        ):
            # The ordinary arguments provide useful provisional evidence, but
            # must not permanently fix a generic before a higher-order argument
            # contributes its own bounds. Try the contextualized type first,
            # then the original generic shape to permit a coherent widening.
            expectations += (original_expected,)

        seen: set[tuple[T.Type, tuple[tuple[str, T.Type], ...]]] = set()
        for expected in expectations:
            for modifier, modifier_substitution in _modifier_variants_for_expected(
                modifiers[position],
                expected,
                ctx,
                analyser,
            ):
                key = (
                    modifier.typ,
                    tuple(sorted(modifier_substitution.items())),
                )
                if key in seen:
                    continue
                seen.add(key)
                merged = _merge_substitutions(
                    current_substitution,
                    modifier_substitution,
                    ctx,
                )
                if merged is None:
                    continue
                yield from rec(
                    position + 1,
                    merged,
                    current_modifiers + (modifier,),
                )

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
    matches: list[
        tuple[_core.ModifierArgumentAnalysis, dict[str, T.Type], bool]
    ] = []
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
        matches.append(
            (
                _core.ModifierArgumentAnalysis(
                    concrete_expected,
                    TypedFunctionNode(
                        modifier.typed_node.node,
                        concrete_expected,
                        (overload,),
                    ),
                ),
                substitution,
                T.same(typ, concrete_expected),
            )
        )
    exact = tuple(match for match in matches if match[2])
    for specialized, substitution, _ in exact or tuple(matches):
        yield specialized, substitution


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
    ctx: T.Context | None = None,
) -> dict[str, T.Type] | None:
    """Merge independently collected generic evidence coherently."""
    merged = dict(left)
    for name, typ in right.items():
        existing = merged.get(name)
        if existing is not None and not T.same(existing, typ):
            combined = T._combine_all((existing, typ), ctx)
            if combined is None:
                return None
            merged[name] = combined
            continue
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
    for initial_rank_values in _initial_rank_value_candidates(overload.params, args):
        rank_bound = _substitute_overload_ranks(overload, initial_rank_values)
        preliminary_substitution = (
            _static_type_substitution(args, rank_bound.params, ctx)
            if overload.where_clause
            else _branch_argument_substitution(args, rank_bound.params, ctx)
        )
        if preliminary_substitution is None:
            continue
        where_result = _evaluate_where_clause(
            overload,
            args,
            initial_rank_values,
            preliminary_substitution,
        )
        if where_result is None:
            continue
        rank_values = dict(where_result.rank_values)
        specialized_overload = _substitute_overload_ranks(overload, rank_values)
        substitution = _branch_argument_substitution(
            args, specialized_overload.params, ctx
        )
        if substitution is None:
            continue
        specialized_branch = _specialize_branch_arguments(branch, substitution)
        specialized_args = tuple(
            _substitute_branch_type(arg, substitution) for arg in args
        )
        attempt = T.try_apply_overload(
            specialized_overload,
            specialized_args,
            ctx,
            disambiguation=disambiguation,
        )
        applied = attempt.applied
        if applied is None:
            continue
        actual_returns = _apply_data_tag_flow(
            specialized_args,
            specialized_overload.returns,
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
            where_result.rank_values,
            element_tags=_propagated_element_tags(
                specialized_overload,
                specialized_args,
                applied.substitution,
            ),
            vectorised_target_ranks=applied.vectorised_target_ranks,
            runtime_static_values=(
                where_result.runtime_values
                if where_result.runtime_values
                else applied.runtime_static_values
            ),
        )
        specialized_branch = _propagate_absent_parameter_requirements(
            specialized_branch, specialized_args, applied.params
        )
        return _core.OverloadApplication(applied, specialized_branch)
    return None


def _propagate_absent_parameter_requirements(
    branch: _core.AnalysisBranch,
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
) -> _core.AnalysisBranch:
    """Propagate negative data-tag requirements back to function inputs."""
    for arg, param in zip(args, params, strict=True):
        if not _contains_absent_data_tag(param):
            continue
        source = branch.typed_body[-1].node if branch.typed_body else None
        if isinstance(source, GetVariableNode) and source.name in branch.input_names:
            branch = branch.refine_named_input_requirement(source.name, arg, param)
            continue
        branch = branch.refine_input_requirement(arg, param)
        branch = _propagate_union_requirement_to_inputs(branch, arg, param)
    return branch


def _propagate_union_requirement_to_inputs(
    branch: _core.AnalysisBranch,
    arg: T.Type,
    param: T.Type,
) -> _core.AnalysisBranch:
    """Conservatively project a union call requirement onto matching inputs.

    Aggregate construction and later projection can erase the exact source path
    while retaining its type as one member of a union. If an input could supply
    such a member, preserve the negative tag requirement on that input.
    """
    normalized_arg = T.normalize(arg)
    normalized_param = T.normalize(param)
    if not isinstance(normalized_arg, T.UnionType):
        return branch
    required_shape = (
        normalized_param.inner
        if isinstance(normalized_param, T.TaggedType)
        else normalized_param
    )
    required_tags = (
        tuple(tag for tag in normalized_param.tags if tag.absent)
        if isinstance(normalized_param, T.TaggedType)
        else ()
    )
    if not required_tags:
        return branch
    for index, input_type in enumerate(branch.inputs):
        input_value = _utils._erase_absent_tag_requirements(input_type)
        if not T.assignable(input_value, required_shape, T.Context()):
            continue
        if not any(
            T.assignable(input_value, member, T.Context())
            or T.assignable(member, input_value, T.Context())
            for member in normalized_arg.items
        ):
            continue
        refined = T.Tagged(input_value, *required_tags)
        name = branch.input_names[index] if index < len(branch.input_names) else None
        if name is not None:
            branch = branch.refine_named_input_requirement(name, input_type, refined)
        else:
            branch = branch.refine_input_requirement(input_type, refined)
    return branch


def _contains_absent_data_tag(typ: T.Type) -> bool:
    """Return whether a type contains a negative data-tag requirement."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return any(tag.absent for tag in typ.tags) or _contains_absent_data_tag(
            typ.inner
        )
    if isinstance(typ, T.NominalType):
        return any(_contains_absent_data_tag(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_absent_data_tag(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_absent_data_tag(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_absent_data_tag(item.typ) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_absent_data_tag(typ.base) or any(
            _contains_absent_data_tag(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.CollectionType):
        return _contains_absent_data_tag(typ.base)
    if isinstance(typ, T.FunctionType):
        return any(
            _contains_absent_data_tag(item)
            for item in (*(typ.params or ()), *(typ.returns or ()))
        )
    if isinstance(typ, (T.NoVecType, T.ExactType)):
        return _contains_absent_data_tag(typ.inner)
    if isinstance(typ, T.AnonymousTraitType):
        return any(
            _contains_absent_data_tag(item)
            for requirement in typ.requirements
            for item in (*requirement.overload.params, *requirement.overload.returns)
        )
    return False


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
            overlay_tag=overlay.tag.text,
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
    if isinstance(typ, T.NoVecType):
        return T.NoVec(_erase_overlay_owned_tag(typ.inner, name))
    if isinstance(typ, T.ExactType):
        return T.Exact(_erase_overlay_owned_tag(typ.inner, name))
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
    """Apply a deferred overload after solving its static program."""
    static_source = _call_site_static_overload(overload)
    args = _row_views_for_arguments(args, static_source.params, env)
    if len(args) != len(static_source.params):
        return None
    if disambiguation and len(disambiguation) != len(args):
        return None

    for initial_rank_values in _initial_rank_value_candidates(
        static_source.params, args
    ):
        rank_bound = _substitute_overload_ranks(static_source, initial_rank_values)
        preliminary_substitution = (
            _static_type_substitution(args, rank_bound.params, ctx)
            if static_source.where_clause
            else _branch_argument_substitution(args, rank_bound.params, ctx)
        )
        if preliminary_substitution is None:
            continue
        where_result = _evaluate_where_clause(
            static_source,
            args,
            initial_rank_values,
            preliminary_substitution,
        )
        if where_result is None:
            continue
        rank_values = dict(where_result.rank_values)
        static_names = static_where.static_parameter_names(
            params=static_source.params,
            returns=static_source.returns,
            param_names=static_source.param_names,
            clause=static_source.where_clause,
        )
        static_values = {
            name: int(str(value))
            for name, value in zip(
                static_names, where_result.runtime_values, strict=True
            )
            if value.is_integer() and int(str(value)) >= 0
        }
        specialized_source = _functions._transform_overload_types(
            static_source,
            lambda typ: static_where.substitute_static_type(typ, ranks=rank_values, types=preliminary_substitution),
        )
        if not _call_site_explicit_args_match(specialized_source.params, args, ctx):
            continue

        deferred = replace(
            overload,
            params=specialized_source.params,
            returns=specialized_source.returns,
            generic_constraints=specialized_source.generic_constraints,
        )
        conceptual_count = (
            len(branch.cycle_params)
            if branch.input_mode is _core.InputMode.CYCLE_EXPLICIT_PARAMS
            else 0
        )
        for extra_count in range(len(branch.stack) + conceptual_count + 1):
            if extra_count <= len(branch.stack):
                stack_args = branch.stack.items[-extra_count:] if extra_count else ()
            else:
                preview = branch.source_arguments((branch.cycle_params[0],) * extra_count)
                if preview is None:
                    continue
                stack_args, _ = preview
            call_params = stack_args + args
            concrete = _call_site_checked_overload_signature(
                deferred,
                call_params,
                ctx,
                analyser,
                rank_values=rank_values,
                type_values=preliminary_substitution,
                where_evaluated=True,
                static_values=static_values,
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
                where_result.rank_values,
                consumed_count + len(args),
                element_tags=_propagated_element_tags(
                    concrete,
                    concrete_args,
                    candidate.substitution,
                ),
                vectorised_target_ranks=candidate.vectorised_target_ranks,
                runtime_static_values=(
                    where_result.runtime_values
                    if where_result.runtime_values
                    else concrete.runtime_static_values
                ),
            )
            return _core.OverloadApplication(applied, result_branch)
    return None


def _call_site_static_overload(overload: T.Overload) -> T.Overload:
    """Restore the declared signature hidden by deferred body checking."""
    body = overload.call_site_body
    if not (isinstance(body, tuple) and len(body) == 2):
        return overload
    _, node = body
    if not isinstance(node, FunctionNode):
        return overload
    params = _functions._declared_params(node)
    return replace(
        overload,
        params=params,
        returns=node.returns or (),
        where_clause=node.where_clause,
        param_names=_functions._function_param_names_for_overload(node, params),
    )


def _overload_needs_call_site_checking(overload: T.Overload) -> bool:
    """Return whether an overload requires call-site checking."""
    return any(
        _functions._is_call_site_checked_param(param) for param in overload.params
    )



from .vectorisation import (
    _call_site_explicit_args_match,
    _call_site_checked_overload_signature,
    _call_site_consumed_count,
    _propagated_element_tags,
    _initial_rank_value_candidates,
    _rank_value_candidates,
    _rank_candidates_for_pairs,
    _extend_rank_candidates,
    _variadic_tuple_rank_candidates,
    _rank_binding_key,
    _deduplicate_rank_binding_keys,
    _deduplicate_rank_candidates,
    _match_variadic_tuple_types,
    _evaluate_where_clause,
    _substitute_overload_ranks,
    _substitute_rank_values,
    _substitute_rank_values_in_element_tags,
    _row_views_for_arguments,
    _row_view_for_argument,
    _specialize_branch_arguments,
    _apply_data_tag_flow,
    _constructed_tag_source_ranks,
    _guaranteed_constructed_tag_sources,
    _constructed_flow_is_suppressed,
    _has_exact_tag_contract,
    _explicit_tags,
    _strip_implicit_computed_tags,
    _with_data_tags,
    _remove_data_tag,
    _show_tag,
    _validator_overload_ok,
    _static_validator_result,
    _disjoint_data_tags,
    _type_rank,
    _refine_branch_like,
    _branch_pair_substitution,
    _branch_argument_substitution,
    _static_type_substitution,
    _collect_static_element_bindings,
    _solve_type_argument,
    _solve_branch_argument,
    _substitute_branch_type,
    _substitute_branch_element_tags,
    _dominates,
    _returns_result_type,
    _consistent_function_returns,
    _single_function_return,
)
