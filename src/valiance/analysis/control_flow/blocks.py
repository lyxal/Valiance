"""Blocks for branch-producing constructs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
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



@_core.register(IfNode)
def _if_node(
    self: _core.Analyser,
    node: IfNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a conditional and retain typed nodes for both runtime branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("if condition must be a boolean value", node)
        return _core.BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="if condition must be a boolean value",
        code="if-condition-type",
    )

    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("if condition must be a boolean value", node)
        return terminal

    outputs: list[_core.AnalysisBranch] = list(terminal.branches)
    saw_mismatched_inputs = False
    for body_input in body_inputs:
        condition_body = body_input.typed_body[len(branch.typed_body) :]
        then_outputs = self.analyse_from(body_input, node.then_branch)
        else_outputs = self.analyse_from(body_input, node.else_branch)

        for left in then_outputs:
            for right in else_outputs:
                if left.inputs != right.inputs:
                    saw_mismatched_inputs = True
                    continue

                if left.break_type is not None or right.break_type is not None:
                    for output in (left, right):
                        typ = output.break_type
                        if typ is None:
                            typ = _calls._returns_result_type(output.stack.items)
                        outputs.append(output.emit(TypedNode(node, typ)))
                    continue

                stack = merge_stacks(left.stack, right.stack, self.env.context)
                base = (
                    replace(
                        _calls._refine_branch_like(branch, left),
                        inputs=left.inputs,
                    )
                    .with_element_tags(right.element_tags)
                    .with_data_element_uses(right.data_element_uses)
                )
                variables = left.variables.merge_against(
                    right.variables,
                    base.variables,
                    self.env.context,
                )
                typ = _calls._returns_result_type(stack.items)
                typed_if = TypedIfNode(
                    node,
                    typ,
                    condition=condition_body,
                    then_branch=left.typed_body[len(body_input.typed_body) :],
                    else_branch=right.typed_body[len(body_input.typed_body) :],
                    then_padding=(
                        0
                        if left.terminal or right.terminal
                        else max(len(right.stack) - len(left.stack), 0)
                    ),
                    else_padding=(
                        0
                        if left.terminal or right.terminal
                        else max(len(left.stack) - len(right.stack), 0)
                    ),
                )
                outputs.append(
                    base.with_stack(stack).with_variables(variables).emit(typed_if)
                )

    if not outputs and saw_mismatched_inputs:
        self._diagnose("if branches inferred different inputs", node)

    return _core.BranchSet.collect(outputs)

@_core.register(AssertNode)
def _assert_node(
    self: _core.Analyser,
    node: AssertNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `AssertNode` node and return the surviving branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("assert condition must be a boolean value", node)
        return _core.BranchSet()
    condition = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="assert condition must be a boolean value",
        code="assert-condition-type",
    )

    if not condition or any(output.failed for output in condition):
        self._diagnose("assert condition must be a boolean value", node)
        return terminal

    condition_tags = frozenset(
        tag for output in condition for tag in output.element_tags
    )
    condition_uses = frozenset(
        use for output in condition for use in output.data_element_uses
    )
    typed_condition = _patterns._typed_block(
        condition,
        len(branch.typed_body),
        node.condition,
    )
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
    )
    success = (
        branch.with_element_tags(condition_tags)
        .with_data_element_uses(condition_uses)
        .emit(typed_assert)
    )
    if not node.else_branch:
        return _core.BranchSet.collect((*terminal.branches, success))

    else_outputs = self.analyse_from(branch, node.else_branch)
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
        else_branch=_patterns._typed_block(
            else_outputs,
            len(branch.typed_body),
            node.else_branch,
        ),
    )
    success = (
        replace(
            success,
            typed_body=(*success.typed_body[:-1], typed_assert),
        )
        .with_element_tags(
            tag for output in else_outputs for tag in output.element_tags
        )
        .with_data_element_uses(
            use for output in else_outputs for use in output.data_element_uses
        )
    )
    error_types = tuple(_utils._top_or_none(output.stack) for output in else_outputs)
    error_type = T.U(*error_types) if error_types else T.NoneType()
    assert_error = T.N(Symbol("AssertError"), error_type)
    return _core.BranchSet.collect((*terminal.branches, success.push(assert_error)))

@_core.register(BreakNode)
def _break_node(
    self: _core.Analyser,
    node: BreakNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `BreakNode` node and return the surviving branches."""
    value_outputs = self.analyse_from(branch, node.values)
    return _core.BranchSet.collect(
        value_branch.emit(
            TypedNode(node, _utils._top_or_none(value_branch.stack))
        ).with_break(_utils._top_or_none(value_branch.stack))
        for value_branch in value_outputs
    )

@_core.register(WhileNode)
def _while_node(
    self: _core.Analyser,
    node: WhileNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `WhileNode` node and return the surviving branches."""
    loop_input = branch
    if node.params is not None:
        params = _functions._params_to_types(node.params)
        sourced = branch.source_arguments(params)
        if sourced is None:
            self._diagnose("while loop inputs do not match stack", node)
            return _core.BranchSet()

        _, loop_input = sourced
        loop_input = loop_input.push(*params)
        named = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, params, strict=True)
            if param.name is not None
        )
        if named:
            loop_input = loop_input.with_variables(
                _core.BranchVariables.from_parameters(
                    named,
                    captures=loop_input.variables,
                )
            )
        loop_input = replace(
            loop_input,
            input_mode=_core.InputMode.CYCLE_EXPLICIT_PARAMS,
            cycle_params=params,
        )

    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(loop_input, node.condition)
    terminal, condition = _utils._split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("while condition must be a boolean value", node)
        return _core.BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="while condition must be a boolean value",
        code="while-condition-type",
    )
    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("while condition must be a boolean value", node)
        return terminal

    body_outputs = self.analyse_scoped_block(body_inputs, node.body)
    if not body_outputs:
        return _core.BranchSet()

    joined: _core.AnalysisBranch | None = None
    for output in body_outputs:
        if joined is None:
            joined = output
            continue

        if joined.inputs != output.inputs:
            self._diagnose("while body inferred different inputs", node)
            return _core.BranchSet()

        stack = merge_stacks(joined.stack, output.stack, self.env.context)
        variables = joined.variables.merge_against(
            output.variables,
            loop_input.variables,
            self.env.context,
        )
        joined = joined.with_stack(stack).with_variables(variables)
        joined = joined.with_element_tags(output.element_tags)
        joined = joined.with_data_element_uses(output.data_element_uses)

    if joined is None:
        return _core.BranchSet()

    variables = (
        joined.variables
        if node.params is None
        else joined.variables.merge_against(
            loop_input.variables,
            branch.variables,
            self.env.context,
        )
    )
    result = _calls._refine_branch_like(branch, joined).with_variables(variables)
    condition_body = _patterns._typed_block(
        condition,
        len(loop_input.typed_body),
        node.condition,
    )
    body_start = (
        len(body_inputs.branches[0].typed_body)
        if body_inputs.branches
        else len(loop_input.typed_body)
    )
    body = _patterns._typed_block(body_outputs, body_start, node.body)
    return _core.BranchSet.collect(
        (
            *terminal.branches,
            result.emit(
                TypedWhileNode(
                    node,
                    _calls._returns_result_type(result.stack.items),
                    condition=condition_body,
                    body=body,
                )
            ),
        )
    )

@_core.register(ReturnNode)
def _return_node(
    self: _core.Analyser,
    node: ReturnNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ReturnNode` node and return the surviving branches."""
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))

