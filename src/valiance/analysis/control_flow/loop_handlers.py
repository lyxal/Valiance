"""Loop Handlers for branch-producing constructs."""

from __future__ import annotations

from valiance.analysis.contracts.release_effects import released_types_effects

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
    TypedReflectiveForNode,
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
    if T.collection_item_type(iterable_type) is None:
        return None
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

    normalized = T.normalize(iterable_type)
    branch = branch.with_element_tags(released_types_effects(self.env, (iterable_type,)))
    from valiance.vtypes.reflection import fields_type_owners
    collection_owners = fields_type_owners(normalized)
    if collection_owners is not None and len(collection_owners) > 1:
        checked = []
        layouts = []
        bodies = []
        for owner in collection_owners:
            alternative = branch.with_stack(T.TypeStack((*branch.stack.items[:-1], T.N(Symbol("Fields"), owner))))
            outputs = _reflective_for_node(self, node, alternative, owner, consumes_stack_iterable)
            if not outputs:
                return _core.BranchSet()
            plan = outputs.branches[0].typed_body[-1]
            layouts.append(plan.metadata)
            bodies.extend(plan.bodies)
            checked.extend(outputs)
        from valiance.vtypes.reflection import FieldsDescriptor, TypeDescriptor
        owner = T.U(*collection_owners)
        metadata = FieldsDescriptor(TypeDescriptor(owner),
                                    tuple(field for layout in layouts for field in layout.fields), tuple(layouts))
        completed = tuple(output for output in checked if output.return_stack is None)
        result_type = T.U(*(output.top for output in completed)) if completed else T.NoneType()
        typed = TypedReflectiveForNode(node, result_type, metadata, tuple(bodies))
        baseline = branch.pop() if consumes_stack_iterable else branch
        variables = _utils._merge_loop_variables(baseline.variables, _core.BranchSet(completed), (), self.env.context)
        result = baseline.with_variables(variables).push(result_type).emit(typed)
        result = result.with_element_tags(frozenset(tag for output in checked for tag in output.element_tags))
        result = result.with_data_element_uses(frozenset(use for output in checked for use in output.data_element_uses))
        returned = _reflective_returns(self, branch, typed,
                                      tuple(output for output in checked if output.return_stack is not None))
        return _core.BranchSet.collect((*((result,) if completed else ()), *returned))
    if (isinstance(normalized, T.NominalType) and normalized.name == Symbol("Fields")
            and len(normalized.args) == 1):
        return _reflective_for_node(self, node, branch, normalized.args[0], consumes_stack_iterable)
    dictionary_type = normalized.inner if isinstance(normalized, T.TaggedType) else normalized
    if (isinstance(dictionary_type, T.NominalType) and dictionary_type.name == Symbol("Dict")
            and len(dictionary_type.args) == 2):
        return _analyse_for_item(
            self, node, branch, iterable_type, dictionary_type.args[0], consumes_stack_iterable,
            second_type=dictionary_type.args[1],
        )
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

    return _analyse_for_item(self, node, branch, iterable_type, item_type, consumes_stack_iterable)


def _analyse_for_item(
    self: _core.Analyser, node: ForNode, branch: _core.AnalysisBranch,
    iterable_type: T.Type, item_type: T.Type, consumes_stack_iterable: bool,
    *, nonempty: bool = False, second_type: T.Type = T.Int,
) -> _core.BranchSet:
    """Check one iteration contract using the ordinary loop stack and variable rules."""
    body_stack = branch.stack.pop() if consumes_stack_iterable else branch.stack
    body_branch = branch.with_stack(body_stack)
    cycle_params = (item_type,)
    if node.index_variable is not None:
        cycle_params = (item_type, second_type)
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
            body_branch.variables.with_block_local(node.index_variable, second_type)
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
        _calls._refine_branch_like(branch, body_branch).with_traces(*body_outputs)
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
    completes = not nonempty or any(output.return_stack is None for output in body_outputs)
    return _core.BranchSet.collect((*((completed,) if completes else ()), *returned))


def _reflective_returns(self, branch, typed, outputs):
    """Join escaping field-specialized returns without manufacturing overloads."""
    groups = {}
    for output in outputs:
        groups.setdefault((len(output.return_stack), output.return_exact), []).append(output)
    returned = []
    for (count, exact), group in groups.items():
        stack = T.TypeStack(tuple(T.U(*(output.return_stack[index] for output in group)) for index in range(count)))
        variables = _utils._merge_loop_variables(branch.variables, _core.BranchSet(tuple(group)), (), self.env.context)
        returned.append(replace(group[0], typed_body=branch.typed_body).with_variables(variables)
                        .with_return(stack, exact=exact).emit(typed)
                        .with_element_tags(frozenset(tag for output in group for tag in output.element_tags))
                        .with_data_element_uses(frozenset(use for output in group for use in output.data_element_uses)))
    return tuple(returned)


def _reflective_for_node(
    self: _core.Analyser, node: ForNode, branch: _core.AnalysisBranch,
    owner: T.Type, consumes_stack_iterable: bool,
) -> _core.BranchSet:
    """Validate every field specialization and merge conservative escaping results."""
    from valiance.vtypes.reflection import opened_field_type

    if not self.require_reflection_permission(owner, node):
        return _core.BranchSet()
    if _functions._contains_type_var(owner):
        if self.defer_reflection:
            raise _core.ReflectionSpecializationRequired(owner)
        self._diagnose("reflective iteration requires concrete field layouts at a call site", node)
        return _core.BranchSet()
    metadata = self.env.field_metadata(owner)
    outputs: list[_core.AnalysisBranch] = []
    bodies = []
    variables = branch.variables
    for field in metadata.fields:
        diagnostics_before = len(self.diagnostics)
        checked = _analyse_for_item(
            self, node, branch.with_variables(variables), T.N(Symbol("Fields"), owner),
            opened_field_type(field), consumes_stack_iterable,
            nonempty=True,
        )
        if not checked or len(self.diagnostics) != diagnostics_before:
            return _core.BranchSet()
        bodies.append(checked.branches[0].typed_body[-1].body)
        outputs.extend(checked)
        variables = _utils._merge_loop_variables(
            variables, _core.BranchSet(tuple(output for output in checked
                                             if output.return_stack is None)),
            (), self.env.context,
        )
    baseline = branch.pop() if consumes_stack_iterable else branch
    completed = tuple(output for output in outputs if output.return_stack is None)
    result_type = T.U(*(output.top for output in completed)) if completed else T.NoneType()
    variables = _utils._merge_loop_variables(
        baseline.variables, _core.BranchSet(tuple(completed)), (), self.env.context,
    )
    typed = TypedReflectiveForNode(node, result_type, metadata, tuple(bodies))
    tags = frozenset(tag for output in outputs for tag in output.element_tags)
    uses = frozenset(use for output in outputs for use in output.data_element_uses)
    result = baseline.with_variables(variables).push(result_type).emit(typed)
    result = result.with_element_tags(tags).with_data_element_uses(uses)
    returned = _reflective_returns(self, branch, typed,
                                  tuple(output for output in outputs if output.return_stack is not None))
    return _core.BranchSet.collect((*((result,) if completed or not metadata.fields else ()), *returned))

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
                state_arity=state_arity,
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
                    state_arity=cast(int, candidate.state_arity),
                    function=TypedFunctionNode(
                        body_function,
                        body_analysis.typ,
                        body_analysis.overloads,
                    ),
                )
            )
        )
    return _core.BranchSet.collect(results)

