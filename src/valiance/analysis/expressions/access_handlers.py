"""Concrete access handlers expression handlers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from decimal import DecimalException, InvalidOperation
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.contracts.release_effects import release_effects
from valiance.analysis.lints import KNOWN_LINT_CODES, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementNode,
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
from valiance.runtime.runtime_values import RuntimeNumber
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
    reconstructed = branch.with_stack(stack).with_element_tags(
        release_effects(self.env, field_type)
    )
    return _core.BranchSet(
        (reconstructed.emit(TypedNode(node, result_type)),)
    )

def _literal_integer_index(node: IndexAccessNode) -> int | None:
    """Return a scalar index only when its source is one integer literal."""
    expression = _scalar_index_expression(node)
    if not isinstance(expression, tuple) or len(expression) != 1:
        return None
    literal = expression[0]
    if not isinstance(literal, NumberLiteralNode):
        return None
    try:
        value = RuntimeNumber(literal.value)
    except InvalidOperation:
        return None
    return int(value) if value.is_integer() else None


def _scalar_index_expression(node: IndexAccessNode) -> tuple[ASTNode, ...] | None:
    """Return the AST chain for one non-slice scalar selector."""
    if len(node.selectors) != 1 or node.selectors[0].is_slice:
        return None
    return node.selectors[0].start


def _constant_integer_index(node: IndexAccessNode) -> int | None:
    """Evaluate a conservative set of obviously constant index expressions."""
    expression = _scalar_index_expression(node)
    if expression is None or len(expression) == 1:
        return None
    stack: list[RuntimeNumber | int] = []
    for item in expression:
        if isinstance(item, NumberLiteralNode):
            try:
                stack.append(RuntimeNumber(item.value))
            except InvalidOperation:
                return None
            continue
        if isinstance(item, (ListLiteralNode, TupleLiteralNode)):
            stack.append(len(item.items))
            continue
        if not isinstance(item, ElementNode) or item.name.namespace or item.call_args:
            return None
        name = item.name.text
        if name == "length":
            if not stack or not isinstance(stack[-1], int):
                return None
            stack[-1] = RuntimeNumber(stack[-1])
            continue
        if name not in {"+", "-", "*", "**"} or len(stack) < 2:
            return None
        right = stack.pop()
        left = stack.pop()
        if not isinstance(left, RuntimeNumber) or not isinstance(right, RuntimeNumber):
            return None
        try:
            result = {
                "+": lambda: left + right,
                "-": lambda: left - right,
                "*": lambda: left * right,
                "**": lambda: left**right,
            }[name]()
        except (DecimalException, ValueError, ZeroDivisionError):
            return None
        stack.append(result)
    if len(stack) != 1 or not isinstance(stack[0], RuntimeNumber):
        return None
    return int(stack[0]) if stack[0].is_integer() else None


def _literal_tuple_index_type(
    typ: T.Type,
    index: int,
) -> tuple[T.Type | None, bool]:
    """Return an exact tuple item type and whether the index is out of bounds."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        result, out_of_bounds = _literal_tuple_index_type(typ.inner, index)
        if result is None:
            return None, out_of_bounds
        carried = tuple(
            T.DataTag(tag.name, tag.depth - 1, tag.absent)
            for tag in typ.tags
            if tag.depth > 0
        )
        return (T.Tagged(result, *carried) if carried else result), out_of_bounds
    if not isinstance(typ, T.TupleType):
        return None, False
    normalized = index if index >= 0 else len(typ.params) + index
    if normalized < 0 or normalized >= len(typ.params):
        return None, True
    return typ.params[normalized], False


def _literal_path_pattern_depth(
    node: IndexAccessNode | IndexSetNode,
    selector_mode: str | None,
) -> int | None:
    """Return the component count of one literal wildcard path selector."""
    if selector_mode != "path_pattern" or len(node.selectors) != 1:
        return None
    selector = node.selectors[0]
    if selector.is_slice or len(selector.start) != 1:
        return None
    expression = selector.start[0]
    return len(expression.items) if isinstance(expression, (ListLiteralNode, TupleLiteralNode)) else None


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
        sourced = source_branch.source_arguments((
            T.V(
                "IndexReceiver",
                T.TypeVarId(
                    branch.origin,
                    60_000 + (node.location.offset if node.location is not None else 0),
                ),
            ),
        ))
        if sourced is None:
            self._diagnose("indexing requires receiver and index value(s)", node)
            return _core.BranchSet()
        (receiver_type,), base_branch = sourced
    else:
        self._diagnose("indexing requires receiver and index value(s)", node)
        return _core.BranchSet()

    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _patterns._selectors_supported(receiver_type, node.selectors):
        self._diagnose("tuple slicing is not supported", node)
        return _core.BranchSet()
    if (
        len(node.selectors) == 1
        and not node.selectors[0].is_slice
        and len(index_types) == 1
        and isinstance(T.normalize(index_types[0]), T.NoneTypeNode)
    ):
        self._diagnose(
            "None is not a valid scalar index; "
            "use it inside a multidimensional index path",
            node,
        )
        return _core.BranchSet()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    selector_mode = (
        _patterns._selector_mode(receiver_type, index_types[0], self.env.context)
        if len(node.selectors) == 1 and len(index_types) == 1
        else None
    )
    grouped = node.grouped_update and (
        len(node.selectors) > 1
        or any(item.is_slice for item in node.selectors)
        or selector_mode is not None
    )
    if grouped and not _patterns._grouped_update_receiver(receiver_type):
        self._diagnose(
            "whole-selection augmented assignment requires a list or string",
            node,
        )
        return _core.BranchSet()
    literal_index = _literal_integer_index(node)
    constant_index = (
        None if literal_index is not None else _constant_integer_index(node)
    )
    checked_index = literal_index if literal_index is not None else constant_index
    if checked_index is not None:
        tuple_result, out_of_bounds = _literal_tuple_index_type(
            receiver_type, checked_index
        )
        if out_of_bounds:
            self._diagnose(
                f"tuple index {checked_index} is out of bounds",
                node,
            )
            return _core.BranchSet()
        if constant_index is not None and tuple_result is not None:
            self._warn(
                "expression index will not return the exact type of item "
                f"{constant_index}; try writing `$[{constant_index}]` instead. "
                "It's simpler and clearer than an expression. Alternatively, "
                "consider using a different data model",
                node,
            )
    else:
        tuple_result = None

    result_type = (
        tuple_result if literal_index is not None else None
    ) or _patterns._selection_type(
        receiver_type,
        selector_mode,
        path_depth=_literal_path_pattern_depth(node, selector_mode),
    )
    if result_type is None:
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
    if not _patterns._selectors_supported(receiver_type, node.selectors):
        self._diagnose("tuple slicing is not supported", node)
        return _core.BranchSet()
    if (
        len(node.selectors) == 1
        and not node.selectors[0].is_slice
        and len(index_types) == 1
        and isinstance(T.normalize(index_types[0]), T.NoneTypeNode)
    ):
        self._diagnose(
            "None is not a valid scalar index; "
            "use it inside a multidimensional index path",
            node,
        )
        return _core.BranchSet()
    if not _patterns._selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return _core.BranchSet()

    selector_mode = (
        _patterns._selector_mode(receiver_type, index_types[0], self.env.context)
        if len(node.selectors) == 1 and len(index_types) == 1
        else None
    )
    grouped = node.grouped_update and (
        len(node.selectors) > 1
        or any(item.is_slice for item in node.selectors)
        or selector_mode is not None
    )
    if grouped and not _patterns._grouped_update_receiver(receiver_type):
        self._diagnose(
            "whole-selection augmented assignment requires a list or string",
            node,
        )
        return _core.BranchSet()
    item_type = _patterns._selection_type(
        receiver_type,
        selector_mode,
        path_depth=_literal_path_pattern_depth(node, selector_mode),
    )
    if item_type is None:
        item_type = _patterns._indexed_type(
            receiver_type,
            node.selectors,
            spread=False,
            grouped_update=grouped,
        )
    if selector_mode is not None:
        replacement_item = _patterns._selection_replacement_item_type(
            receiver_type,
            selector_mode,
        )
        if T.assignable(value_type, item_type, self.env.context) or (
            not grouped
            and T.assignable(value_type, replacement_item, self.env.context)
        ):
            updated_receiver_type = receiver_type
        else:
            updated_receiver_type = None
    else:
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
    reconstructed = branch.with_stack(stack).with_element_tags(
        release_effects(self.env, item_type)
    )
    return _core.BranchSet(
        (reconstructed.emit(TypedNode(node, updated_receiver_type)),)
    )

