"""Shared AST helpers for explicit object constructors."""

from __future__ import annotations

from dataclasses import replace

from valiance.asts import (
    ASTNode,
    AssertNode,
    AtNode,
    DefineNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    MatchNode,
    SetVariableNode,
    StackShuffleNode,
    TryNode,
    UnfoldNode,
    WhileNode,
)
from valiance.symbols import Symbol

_SELF = Symbol("self")


def constructor_definitions(
    owner: Symbol,
    definitions: tuple[DefineNode, ...],
) -> tuple[DefineNode, ...]:
    """Return definitions whose local name matches the owning object."""
    return tuple(
        definition
        for definition in definitions
        if definition.name.text == owner.text
    )


def prepare_constructor_body(body: tuple[ASTNode, ...]) -> tuple[ASTNode, ...]:
    """Make direct ``$self.field = ...`` writes update the local receiver."""
    transformed: list[ASTNode] = []
    for index, node in enumerate(body):
        targets_self = isinstance(node, FieldSetNode) and _field_set_targets_self(
            body,
            index,
        )
        if targets_self and _field_set_reads_target(body, index):
            transformed.extend(
                (
                    GetVariableNode(_SELF, location=node.location),
                    StackShuffleNode(
                        Symbol("move"),
                        (Symbol("value"), Symbol("receiver")),
                        (Symbol("receiver"), Symbol("value")),
                        location=node.location,
                    ),
                )
            )
        transformed.append(_prepare_constructor_child(node))
        if targets_self:
            transformed.append(SetVariableNode(_SELF, location=node.location))
    return tuple(transformed)


def definitely_initialized_fields(
    body: tuple[ASTNode, ...],
    initialized: frozenset[Symbol],
) -> frozenset[Symbol]:
    """Conservatively compute fields initialized on every continuing path."""
    current = set(initialized)
    index = 0
    while index < len(body):
        node = body[index]
        if (
            isinstance(node, FieldSetNode)
            and index + 1 < len(body)
            and isinstance(body[index + 1], SetVariableNode)
            and body[index + 1].name == _SELF
        ):
            if not _field_set_reads_target(body, index):
                current.add(node.name)
            index += 2
            continue
        if isinstance(node, IfNode):
            before = frozenset(current)
            after_condition = definitely_initialized_fields(node.condition, before)
            then_fields = definitely_initialized_fields(
                node.then_branch,
                after_condition,
            )
            else_fields = (
                definitely_initialized_fields(node.else_branch, after_condition)
                if node.else_branch
                else after_condition
            )
            current = set(then_fields & else_fields)
        elif isinstance(node, MatchNode) and node.cases:
            paths = tuple(
                definitely_initialized_fields(case.body, frozenset(current))
                for case in node.cases
            )
            current = set.intersection(*(set(path) for path in paths))
        elif isinstance(node, TryNode):
            paths = [
                definitely_initialized_fields(node.body, frozenset(current)),
                *(
                    definitely_initialized_fields(
                        handler.body,
                        frozenset(current),
                    )
                    for handler in node.handlers
                ),
            ]
            current = set.intersection(*(set(path) for path in paths))
        elif isinstance(node, WhileNode):
            current = set(
                definitely_initialized_fields(
                    node.condition,
                    frozenset(current),
                )
            )
        elif isinstance(node, AssertNode):
            current = set(
                definitely_initialized_fields(
                    node.condition,
                    frozenset(current),
                )
            )
        elif isinstance(node, AtNode):
            current = set(
                definitely_initialized_fields(
                    node.body,
                    frozenset(current),
                )
            )
        index += 1
    return frozenset(current)


def _prepare_constructor_child(node: ASTNode) -> ASTNode:
    """Prepare constructor child while analysing object construction."""
    if isinstance(node, IfNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            then_branch=prepare_constructor_body(node.then_branch),
            else_branch=prepare_constructor_body(node.else_branch),
        )
    if isinstance(node, AssertNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            else_branch=prepare_constructor_body(node.else_branch),
        )
    if isinstance(node, MatchNode):
        return replace(
            node,
            cases=tuple(
                replace(case, body=prepare_constructor_body(case.body))
                for case in node.cases
            ),
        )
    if isinstance(node, TryNode):
        return replace(
            node,
            body=prepare_constructor_body(node.body),
            handlers=tuple(
                replace(handler, body=prepare_constructor_body(handler.body))
                for handler in node.handlers
            ),
        )
    if isinstance(node, WhileNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            body=prepare_constructor_body(node.body),
        )
    if isinstance(node, UnfoldNode):
        return replace(
            node,
            condition=prepare_constructor_body(node.condition),
            body=prepare_constructor_body(node.body),
        )
    if isinstance(node, ForNode):
        return replace(node, body=prepare_constructor_body(node.body))
    if isinstance(node, AtNode):
        return replace(node, body=prepare_constructor_body(node.body))
    return node


def _field_set_targets_self(body: tuple[ASTNode, ...], index: int) -> bool:
    """Return the Boolean result of field set targets self while analysing object construction."""
    target = body[index]
    if not isinstance(target, FieldSetNode) or target.location is None:
        return False
    for candidate in reversed(body[:index]):
        if candidate.location != target.location:
            continue
        if isinstance(candidate, GetVariableNode):
            return candidate.name == _SELF
    return False


def _field_set_reads_target(body: tuple[ASTNode, ...], index: int) -> bool:
    """Return whether a direct self write is an augmented assignment."""
    target = body[index]
    if not isinstance(target, FieldSetNode) or target.location is None:
        return False
    return any(
        isinstance(candidate, FieldAccessNode)
        and candidate.name == target.name
        and candidate.location == target.location
        for candidate in body[:index]
    )
