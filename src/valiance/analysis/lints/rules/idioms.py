"""Conservative source-pattern lints for common Valiance idioms."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import valiance.vtypes as T
from valiance.asts import (
    ASTNode,
    ElementNode,
    ForNode,
    FunctionNode,
    GetVariableNode,
    IfNode,
    IndexAccessNode,
    NumberLiteralNode,
    SetVariableNode,
    TypedElementNode,
    TypedNode,
    WhileNode,
)
from valiance.elements.builtins import BUILTIN_ELEMENTS
from valiance.vtypes.symbols import Symbol

from ..contexts import NodeLintContext
from ..models import finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register idiom-oriented lint rules."""
    registry.register_node(ForNode, prefer_sum)
    registry.register_node(ForNode, prefer_filter)
    registry.register_node(ElementNode, map_can_vectorise)
    registry.register_node(IfNode, prefer_match)
    registry.register_node(WhileNode, while_can_be_foreach)


def prefer_sum(context: NodeLintContext):
    """Recognise a single numeric accumulator updated by addition."""
    node = context.node
    if not isinstance(node, ForNode) or not _simple_sum_body(node):
        return ()
    return (
        finding(
            "prefer-sum",
            "foreach loop only adds each item to one accumulator; prefer sum",
            node,
        ),
    )


def prefer_filter(context: NodeLintContext):
    """Recognise a conditional collection of the unchanged loop item."""
    node = context.node
    if not isinstance(node, ForNode) or len(node.body) != 1:
        return ()
    conditional = node.body[0]
    if not isinstance(conditional, IfNode) or conditional.else_branch:
        return ()
    branch = conditional.then_branch
    if not _contains_name(branch, node.variable):
        return ()
    if not any(
        isinstance(item, ElementNode) and item.name in {Symbol("append"), Symbol("wrap")}
        for item in branch
    ):
        return ()
    return (
        finding(
            "prefer-filter",
            "foreach loop conditionally collects its unchanged item; prefer filter",
            node,
        ),
    )


def map_can_vectorise(context: NodeLintContext):
    """Suggest direct vectorisation for a map whose callable fully vectorises."""
    node = context.node
    if not isinstance(node, ElementNode) or node.name != Symbol("map"):
        return ()
    typed = _typed_element(context.outputs, node)
    if typed is None or not typed.modifier_args:
        return ()
    bodies = tuple(
        item
        for function in typed.modifier_args
        for overload in function.overloads
        for item in overload.body
    )
    calls = tuple(item for item in _walk_typed(bodies) if isinstance(item, TypedElementNode))
    if not calls or not all(_element_call_vectorisable(item) for item in calls):
        return ()
    if any(
        applied.element_tags
        for item in calls
        if (applied := item.overload) is not None
    ):
        return ()
    return (
        finding(
            "explicit-map-can-vectorise",
            "this map contains only vectorising element calls; prefer direct vectorisation",
            node,
        ),
    )


def prefer_match(context: NodeLintContext):
    """Suggest match for an equality-based else-if chain over one variable."""
    node = context.node
    if not isinstance(node, IfNode):
        return ()
    subjects = []
    current = node
    branches = 0
    while True:
        subject = _equality_subject(current.condition)
        if subject is None:
            return ()
        subjects.append(subject)
        branches += 1
        if len(current.else_branch) == 1 and isinstance(current.else_branch[0], IfNode):
            current = current.else_branch[0]
            continue
        break
    if branches < 2 or len(set(subjects)) != 1:
        return ()
    return (
        finding(
            "prefer-match",
            f"conditional repeatedly compares '${subjects[0]}' with literals; consider match",
            node,
        ),
    )


def while_can_be_foreach(context: NodeLintContext):
    """Recognise a narrow index-from-zero collection traversal pattern."""
    node = context.node
    if not isinstance(node, WhileNode) or not node.body:
        return ()
    condition_names = _variable_names(node.condition)
    if len(condition_names) < 2:
        return ()
    has_length = any(
        isinstance(item, ElementNode) and item.name == Symbol("length")
        for item in node.condition
    )
    has_index = any(isinstance(item, IndexAccessNode) for item in _walk_ast(node.body))
    has_increment = any(
        isinstance(item, ElementNode) and item.name in {Symbol("inc"), Symbol("+")}
        for item in _walk_ast(node.body)
    )
    if not (has_length and has_index and has_increment):
        return ()
    return (
        finding(
            "while-can-be-foreach",
            "while loop appears to traverse a collection by index; consider foreach",
            node,
        ),
    )


def _simple_sum_body(node: ForNode) -> bool:
    """Return whether a loop body is exactly one additive accumulator update."""
    writes = [item for item in node.body if isinstance(item, SetVariableNode)]
    additions = [
        item
        for item in node.body
        if isinstance(item, ElementNode) and item.name == Symbol("+")
    ]
    return (
        len(writes) == 1
        and len(additions) == 1
        and _contains_name(node.body, node.variable)
        and _contains_name(node.body, writes[0].name)
    )


def _contains_name(values: object, name: object) -> bool:
    """Return whether AST content reads one variable name."""
    return name in _variable_names(values)


def _variable_names(values: object) -> set[object]:
    """Collect variable reads from AST content outside nested functions."""
    if isinstance(values, FunctionNode):
        return set()
    if isinstance(values, GetVariableNode):
        return {values.name}
    if isinstance(values, ASTNode) and is_dataclass(values):
        result = set()
        for field in fields(values):
            if field.name != "location":
                result.update(_variable_names(getattr(values, field.name)))
        return result
    if isinstance(values, (tuple, list)):
        result = set()
        for value in values:
            result.update(_variable_names(value))
        return result
    return set()


def _equality_subject(condition: tuple[ASTNode, ...]):
    """Return the compared variable for a simple variable/literal equality test."""
    if len(condition) != 3:
        return None
    variables = [item.name for item in condition if isinstance(item, GetVariableNode)]
    equality = any(
        isinstance(item, ElementNode) and item.name in {Symbol("=="), Symbol("===")}
        for item in condition
    )
    literal = any(isinstance(item, NumberLiteralNode) for item in condition)
    return variables[0] if len(variables) == 1 and equality and literal else None


def _typed_element(outputs: object, node: ElementNode):
    """Find the typed element corresponding to a raw node."""
    for output in outputs:
        for typed in reversed(output.typed_body):
            if isinstance(typed, TypedElementNode) and typed.node is node:
                return typed
    return None


def _walk_typed(value: object):
    """Yield typed nodes recursively."""
    if isinstance(value, TypedNode):
        yield value
        if is_dataclass(value):
            for field in fields(value):
                if field.name not in {"node", "typ"}:
                    yield from _walk_typed(getattr(value, field.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_typed(item)


def _element_call_vectorisable(node: TypedElementNode) -> bool:
    """Return whether the selected built-in overload permits vectorisation."""
    applied = node.overload
    if applied is None or any(isinstance(T.normalize(p), T.ExactType) for p in applied.overload.params):
        return False
    name = node.runtime_name or getattr(node.node, "name", None)
    for element in BUILTIN_ELEMENTS:
        if element.name == name and node.overload_index is not None:
            return (
                node.overload_index < len(element.definitions)
                and element.definitions[node.overload_index].vectorisable
            )
    return True


def _walk_ast(value: object):
    """Yield AST nodes recursively outside nested functions."""
    if isinstance(value, FunctionNode):
        return
    if isinstance(value, ASTNode):
        yield value
        if is_dataclass(value):
            for field in fields(value):
                if field.name != "location":
                    yield from _walk_ast(getattr(value, field.name))
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_ast(item)
