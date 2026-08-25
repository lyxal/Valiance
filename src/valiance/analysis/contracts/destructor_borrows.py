"""Static escape checks for the borrowed receiver visible inside ``~Type``."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from valiance.asts import (
    ASTNode,
    DictLiteralNode,
    ElementNode,
    FunctionNode,
    GetVariableNode,
    ListLiteralNode,
    RecordLiteralNode,
    ReturnNode,
    SetVariableNode,
    SetVariablesNode,
    TupleLiteralNode,
)
from valiance.vtypes.symbols import Symbol


_AGGREGATE_NODES = (
    DictLiteralNode,
    ListLiteralNode,
    RecordLiteralNode,
    TupleLiteralNode,
)


def destructor_borrow_violations(
    body: tuple[ASTNode, ...],
) -> tuple[tuple[str, ASTNode], ...]:
    """Return statically recognizable attempts to escape destructor ``$self``."""
    aliases: set[Symbol] = {Symbol("self")}
    stack: list[bool] = []
    violations: list[tuple[str, ASTNode]] = []

    for node in body:
        if isinstance(node, GetVariableNode):
            stack.append(node.name in aliases)
            continue
        if isinstance(node, SetVariableNode):
            borrowed = stack.pop() if stack else False
            if borrowed:
                aliases.add(node.name)
            else:
                aliases.discard(node.name)
            continue
        if isinstance(node, SetVariablesNode):
            values = [stack.pop() if stack else False for _ in node.targets]
            for target, borrowed in zip(reversed(node.targets), values, strict=False):
                if borrowed:
                    aliases.add(target.name)
                else:
                    aliases.discard(target.name)
            continue
        if isinstance(node, ReturnNode) and _contains_alias(node, aliases):
            violations.append(("destructor receiver cannot be returned", node))
            continue
        if isinstance(node, FunctionNode) and _contains_alias(node, aliases):
            violations.append(("destructor receiver cannot be captured by a closure", node))
            stack.append(False)
            continue
        if isinstance(node, _AGGREGATE_NODES) and _contains_alias(node, aliases):
            violations.append(("destructor receiver cannot be stored in an aggregate", node))
            stack.append(False)
            continue
        if isinstance(node, ElementNode):
            name = node.name.text
            borrowed = bool(stack and stack[-1])
            if name == "dup" and borrowed:
                violations.append(("destructor receiver cannot be duplicated", node))
            elif name == "spawn" and any(stack[-2:]):
                violations.append(("destructor receiver cannot be transferred to a task", node))
            elif name == "send" and any(stack[-2:]):
                violations.append(("destructor receiver cannot be sent through a channel", node))
            # Keep the model bounded. Calls consume their obvious receiver/input;
            # ordinary cleanup methods remain legal and return an unknown value.
            if name in {"dup", "spawn"}:
                if stack:
                    stack.pop()
                stack.append(False)
            elif name == "send":
                if stack:
                    stack.pop()
                if stack:
                    stack.pop()
            elif stack:
                stack.pop()
            continue

        # Branches and other structured nodes are inspected for direct forbidden
        # nesting even when the compact flow model does not simulate their stack.
        if _contains_nested_forbidden(node, aliases):
            violations.append(("destructor receiver cannot escape its destructor", node))

    return tuple(_deduplicate(violations))


def _contains_alias(value: object, aliases: set[Symbol]) -> bool:
    """Return whether one AST subtree reads a borrowed-receiver alias."""
    if isinstance(value, GetVariableNode):
        return value.name in aliases
    if isinstance(value, FunctionNode):
        return any(_contains_alias(item, aliases) for item in value.body)
    if isinstance(value, ASTNode) and is_dataclass(value):
        return any(
            _contains_alias(getattr(value, item.name), aliases)
            for item in fields(value)
            if item.name != "location"
        )
    if isinstance(value, (tuple, list)):
        return any(_contains_alias(item, aliases) for item in value)
    if isinstance(value, dict):
        return any(_contains_alias(item, aliases) for item in value.values())
    return False


def _contains_nested_forbidden(node: ASTNode, aliases: set[Symbol]) -> bool:
    """Recognize forbidden receiver use nested inside structured expressions."""
    if not is_dataclass(node):
        return False
    for item in fields(node):
        if item.name == "location":
            continue
        value = getattr(node, item.name)
        if isinstance(value, (FunctionNode, *_AGGREGATE_NODES)) and _contains_alias(
            value, aliases
        ):
            return True
        if isinstance(value, ReturnNode) and _contains_alias(value, aliases):
            return True
        if isinstance(value, (tuple, list)):
            for child in value:
                if isinstance(child, (FunctionNode, ReturnNode, *_AGGREGATE_NODES)) and _contains_alias(
                    child, aliases
                ):
                    return True
    return False


def _deduplicate(
    violations: list[tuple[str, ASTNode]],
) -> list[tuple[str, ASTNode]]:
    """Preserve the first occurrence of each message and source location."""
    seen: set[tuple[str, object | None]] = set()
    result: list[tuple[str, ASTNode]] = []
    for message, node in violations:
        key = message, node.location
        if key not in seen:
            seen.add(key)
            result.append((message, node))
    return result
