"""Concrete access handlers expression handlers."""

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
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils



@_core.register(FieldAccessNode)
def _field_access_node(
    self: _core.Analyser,
    node: FieldAccessNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FieldAccessNode` node and return the surviving branches."""
    sourced = self._source_field_receiver(
        branch,
        node.name,
        optional_safe=node.optional_safe,
    )
    if sourced is None:
        action = "safely access" if node.optional_safe else "access"
        self._diagnose(
            f"empty stack when trying to {action} field '{node.name}'",
            node,
        )
        return _core.BranchSet()

    receiver_type, field_type, branch = sourced
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no known field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no known field '{node.name}'",
                node,
            )
        return _core.BranchSet()

    return _core.BranchSet((branch.push(field_type).emit(TypedNode(node, field_type)),))

@_core.register(FieldSetNode)
def _field_set_node(
    self: _core.Analyser,
    node: FieldSetNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FieldSetNode` node and return the surviving branches."""
    if len(branch.stack) < 2:
        self._diagnose(
            f"field assignment to '{node.name}' requires receiver and value",
            node,
        )
        return _core.BranchSet()

    receiver_type = branch.stack[-2]
    value_type = branch.stack[-1]
    if node.optional_safe:
        field_type, refined_receiver = self._safe_field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    else:
        field_type, refined_receiver = self._field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no writable field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no writable field '{node.name}'",
                node,
            )
        return _core.BranchSet()

    if not T.assignable(value_type, field_type, self.env.context):
        self._diagnose(
            f"cannot assign {T.show(value_type)} to field '{node.name}' "
            f"of type {T.show(field_type)}",
            node,
        )
        return _core.BranchSet()

    result_type = receiver_type if refined_receiver is None else refined_receiver
    stack = T.TypeStack(branch.stack.items[:-2]).push(result_type)
    return _core.BranchSet(
        (branch.with_stack(stack).emit(TypedNode(node, result_type)),)
    )

@_core.register(IndexAccessNode)
def _index_access_node(
    self: _core.Analyser,
    node: IndexAccessNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `IndexAccessNode` node and return the surviving branches."""
    selector_values = sum(
        bool(selector.start) + bool(selector.stop) + bool(selector.step)
        for selector in node.selectors
    )
    required = selector_values + 1
    if len(branch.stack) >= required:
        receiver_type = branch.stack[-required]
        base_branch = branch.with_stack(T.TypeStack(branch.stack.items[:-required]))
    elif len(branch.stack) == selector_values:
        source_branch = branch.with_stack(
            T.TypeStack(branch.stack.items[: len(branch.stack) - selector_values])
        )
        sourced = source_branch.source_arguments((T.V("IndexReceiver"),))
        if sourced is None:
            self._diagnose("indexing requires receiver and index value(s)", node)
            return _core.BranchSet()
        (receiver_type,), base_branch = sourced
    else:
        self._diagnose("indexing requires receiver and index value(s)", node)
        return _core.BranchSet()

    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    grouped = node.grouped_update and (
        len(node.selectors) > 1 or any(item.is_slice for item in node.selectors)
    )
    if grouped and not _patterns._grouped_update_receiver(receiver_type):
        self._diagnose(
            "whole-selection augmented assignment requires a list or string",
            node,
        )
        return _core.BranchSet()
    result_type = _patterns._indexed_type(
        receiver_type,
        node.selectors,
        node.spread,
        grouped_update=grouped,
    )
    return _core.BranchSet(
        (base_branch.push(result_type).emit(TypedNode(node, result_type)),)
    )

@_core.register(IndexSetNode)
def _index_set_node(
    self: _core.Analyser,
    node: IndexSetNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `IndexSetNode` node and return the surviving branches."""
    selector_values = sum(
        bool(selector.start) + bool(selector.stop) + bool(selector.step)
        for selector in node.selectors
    )
    required = selector_values + 2
    if len(branch.stack) < required:
        self._diagnose(
            "indexed assignment requires value, receiver, and index",
            node,
        )
        return _core.BranchSet()

    value_type = branch.stack[-required]
    receiver_type = branch.stack[-selector_values - 1]
    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    grouped = node.grouped_update and (
        len(node.selectors) > 1 or any(item.is_slice for item in node.selectors)
    )
    if grouped and not _patterns._grouped_update_receiver(receiver_type):
        self._diagnose(
            "whole-selection augmented assignment requires a list or string",
            node,
        )
        return _core.BranchSet()
    item_type = _patterns._indexed_type(
        receiver_type,
        node.selectors,
        spread=False,
        grouped_update=grouped,
    )
    updated_receiver_type = _patterns._indexed_assignment_type(
        receiver_type,
        node.selectors,
        value_type,
        self.env.context,
        grouped_update=grouped,
    )
    if updated_receiver_type is None:
        self._diagnose(
            f"cannot assign {T.show(value_type)} to indexed item "
            f"of type {T.show(item_type)}",
            node,
        )
        return _core.BranchSet()

    stack = T.TypeStack(branch.stack.items[:-required]).push(updated_receiver_type)
    return _core.BranchSet(
        (branch.with_stack(stack).emit(TypedNode(node, updated_receiver_type)),)
    )

