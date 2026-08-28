"""Loop Handlers for branch-producing constructs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    LintSuppressionNode,
    NumberLiteralNode,
    ObjectNode,
    PopNNode,
    RecordLiteralNode,
    ReturnNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedAtNode,
    TypedCallNode,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypedWhileNode,
    UnfoldNode,
)
from valiance.asts.nodes import (
    BreakNode,
    CallNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    SetVariableNode,
    SetVariablesNode,
    WhileNode,
)
from valiance.modules_system.modules import (
    ModuleLoadError,
    import_environment_facts,
    import_objects,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean
from valiance.vtypes.relations import merge_stacks

from .. import analyser as _core
from ..calls import callable_values as _functions
from ..calls import candidates as _calls
from . import patterns as _patterns
from ..support import analysis_utils as _utils



def _loop_iterable_requirement(
    iterable_type: T.Type,
    outputs: _core.BranchSet,
) -> T.Type | None:
    """Lift negative loop-item tag requirements onto the iterable input."""
    absent_tags: set[T.DataTag] = set()
    for output in outputs:
        if not output.cycle_params:
            continue
        item = T.normalize(output.cycle_params[0])
        if not isinstance(item, T.TaggedType):
            continue
        absent_tags.update(
            T.DataTag(tag.name, tag.depth + 1, absent=True)
            for tag in item.tags
            if tag.absent
        )
    if not absent_tags:
        return None
    normalized = T.normalize(iterable_type)
    if isinstance(normalized, T.TaggedType):
        return T.Tagged(
            normalized.inner,
            *normalized.tags,
            *absent_tags,
            exact=normalized.exact,
        )
    return T.Tagged(normalized, *absent_tags)

@_core.register(ForNode)
def _for_node(
    self: _core.Analyser,
    node: ForNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ForNode` node and return the surviving branches."""
    consumes_stack_iterable = bool(branch.stack)
    if not branch.stack:
        item = _functions._anonymous_type_var(branch, 1)
        sourced = branch.source_arguments((T.ExactList(item),))
        if sourced is None:
            self._diagnose("for loop requires iterable on the stack", node)
            return _core.BranchSet()
        (iterable_type,), branch = sourced
    else:
        iterable_type = branch.stack[-1]

    item_type = T.collection_item_type(iterable_type)
    if not item_type:
        source = branch.typed_body[-1].node if branch.typed_body else None
        if (
            isinstance(source, GetVariableNode)
            and source.name in branch.input_names
            and isinstance(T.normalize(iterable_type), T.MetaVarType)
        ):
            item_type = _functions._anonymous_type_var(branch, 1)
            inferred_iterable = T.ExactList(item_type)
            branch = branch.refine_named_input_requirement(
                source.name, iterable_type, inferred_iterable
            )
            branch = branch.with_stack(
                T.TypeStack((*branch.stack.items[:-1], inferred_iterable))
            )
            iterable_type = inferred_iterable
        else:
            self._diagnose(
                "for loop iterable must actually be iterable. "
                f"Got {T.show(iterable_type)}",
                node,
            )
            return _core.BranchSet()

    body_stack = branch.stack.pop() if consumes_stack_iterable else branch.stack
    body_branch = branch.with_stack(body_stack)
    cycle_params = (item_type,)
    if node.index_variable is not None:
        cycle_params = (item_type, T.Int)
    body_branch = replace(
        body_branch,
        input_mode=_core.InputMode.CYCLE_EXPLICIT_PARAMS,
        cycle_params=cycle_params,
        cycle_index=0,
        cycle_stack_remaining=len(cycle_params),
        cycle_from_top=True,
    )
    body_branch = body_branch.with_variables(
        body_branch.variables.with_block_local(node.variable, item_type)
    )
    if node.index_variable is not None:
        body_branch = body_branch.with_variables(
            body_branch.variables.with_block_local(node.index_variable, T.Int)
        )

    body_outputs = self.analyse_from(body_branch, node.body)
    if not body_outputs:
        return _core.BranchSet()

    refined_iterable = _loop_iterable_requirement(iterable_type, body_outputs)
    if refined_iterable is not None:
        source = branch.typed_body[-1].node if branch.typed_body else None
        if isinstance(source, GetVariableNode) and source.name in branch.input_names:
            branch = branch.refine_named_input_requirement(
                source.name, iterable_type, refined_iterable
            )
            body_branch = body_branch.refine_named_input_requirement(
                source.name, iterable_type, refined_iterable
            )
        else:
            branch = branch.refine_input_requirement(iterable_type, refined_iterable)
            body_branch = body_branch.refine_input_requirement(
                iterable_type, refined_iterable
            )

    refined_item_type = _utils._loop_variable_output_type(
        node.variable,
        body_outputs,
        self.env.context,
    )
    if (
        refined_item_type is not None
        and _functions._contains_type_var(item_type)
        and not T.same(item_type, refined_item_type)
    ):
        body_branch = body_branch.refine_type(item_type, refined_item_type)
        body_outputs = _core.BranchSet.collect(
            output.refine_type(item_type, refined_item_type) for output in body_outputs
        )

    break_outputs = tuple(
        output for output in body_outputs if output.break_type is not None
    )
    break_types = tuple(output.break_type for output in break_outputs)
    result_type = _utils._loop_break_result_type(break_types)
    result_types = (result_type,)
    loop_locals = (node.variable,) + (
        (node.index_variable,) if node.index_variable is not None else ()
    )
    variables = _utils._merge_loop_variables(
        body_branch.variables,
        body_outputs,
        loop_locals,
        self.env.context,
    )
    typed_for = TypedForNode(
        node,
        result_type,
        body=_patterns._typed_block(
            body_outputs,
            len(body_branch.typed_body),
            node.body,
        ),
    )
    body_element_tags = frozenset(
        tag for output in body_outputs for tag in output.element_tags
    )
    body_data_element_uses = frozenset(
        use for output in body_outputs for use in output.data_element_uses
    )
    completed = (
        _calls._refine_branch_like(branch, body_branch)
        .with_element_tags(body_element_tags)
        .with_data_element_uses(body_data_element_uses)
        .with_stack(body_branch.stack.push(*result_types))
        .with_variables(variables)
        .emit(typed_for)
    )
    returned = tuple(
        _calls._refine_branch_like(branch, output)
        .with_element_tags(body_element_tags)
        .with_data_element_uses(body_data_element_uses)
        .emit(typed_for)
        .with_return(output.return_stack, exact=output.return_exact)
        for output in body_outputs
        if output.return_stack is not None
    )
    return _core.BranchSet.collect((completed, *returned))

@_core.register(UnfoldNode)
def _unfold_node(
    self: _core.Analyser,
    node: UnfoldNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `UnfoldNode` node and return the surviving branches."""
    body_function = FunctionNode(
        params=node.params,
        body=node.body,
        annotations=(AnnotationNode(Symbol("returnAll")),),
        element_tags=frozenset(),
        location=node.location,
    )
    body_analysis = self._analyse_unfold_body_function(branch, body_function)
    if body_analysis is None:
        return _core.BranchSet()

    candidates: list[_core.CallCandidate] = []
    for overload in _functions._callable_overloads(body_analysis.typ):
        condition_element_tags: frozenset[T.ElementTag] = frozenset()
        state_arity = len(overload.params)
        if state_arity == 0:
            self._diagnose("unfold requires at least one state value", node)
            continue
        if len(overload.returns) > state_arity + 1:
            self._diagnose(
                "unfold body may not produce more than state arity plus one value",
                node,
            )
            continue

        sourced = branch.source_arguments(overload.params)
        if sourced is None:
            self._diagnose("unfold inputs do not match stack", node)
            continue
        args, popped = sourced
        applied = T.try_apply_overload(overload, args, self.env.context).applied
        if applied is None:
            continue

        if node.condition:
            condition_function = FunctionNode(
                params=(
                    tuple(
                        FunctionParam(param.name, typ)
                        for param, typ in zip(
                            node.params or (),
                            applied.params,
                            strict=False,
                        )
                    )
                    if node.params is not None
                    else tuple(FunctionParam(None, typ) for typ in applied.params)
                ),
                body=node.condition,
                returns=(Boolean,),
                element_tags=frozenset(),
                location=node.location,
            )
            condition_analysis = self._analyse_unfold_body_function(
                popped,
                condition_function,
            )
            if condition_analysis is None:
                self._diagnose("unfold condition must return a boolean value", node)
                continue
            condition_element_tags = frozenset(
                tag
                for candidate_overload in _functions._callable_overloads(
                    condition_analysis.typ
                )
                for tag in candidate_overload.element_tags
                if not tag.absent
            )
        candidates.append(
            _core.CallCandidate(
                applied=applied,
                branch=popped.with_element_tags(condition_element_tags),
                callable_overload_index=state_arity,
            )
        )

    results: list[_core.AnalysisBranch] = []
    for candidate in _functions._best_candidates(candidates, branch):
        generated = _patterns._unfold_emitted_type(
            candidate.applied.params,
            candidate.applied.actual_returns,
        )
        list_type = T.WithTag(T.ExactList(generated), "infinite")
        results.append(
            candidate.branch.with_element_tags(candidate.applied.element_tags)
            .push(list_type)
            .emit(
                TypedUnfoldNode(
                    node,
                    list_type,
                    state_arity=cast(int, candidate.callable_overload_index),
                    function=TypedFunctionNode(
                        body_function,
                        body_analysis.typ,
                        body_analysis.overloads,
                    ),
                )
            )
        )
    return _core.BranchSet.collect(results)

