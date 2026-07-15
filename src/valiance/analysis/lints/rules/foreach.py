"""Lint rules for foreach loops that do not require shared mutable state."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any

import valiance.vtypes as T
from valiance.asts import (
    ASTNode,
    ForNode,
    FunctionNode,
    SetVariableNode,
    SetVariablesNode,
    TypedForNode,
    TypedNode,
)

from ..contexts import NodeLintContext
from ..models import finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register foreach refactoring guidance with a lint registry."""
    registry.register_node(ForNode, lint_stateless_foreach)


def lint_stateless_foreach(context: NodeLintContext):
    """Prefer vectorisation or map for effect-free, stateless foreach loops."""
    node = context.node
    if not isinstance(node, ForNode):
        return ()

    outer_names = frozenset(context.branch.variables.visible_names())
    if _writes_outer_variable(node.body, outer_names):
        return ()

    typed_loops = tuple(_typed_loops(context.outputs))
    if not typed_loops or any(_has_element_tags(loop.body) for loop in typed_loops):
        return ()

    recommendation = (
        "vectorisation"
        if all(_all_calls_vectorise(loop.body) for loop in typed_loops)
        else "map"
    )
    reason = (
        "all calls in the loop body support vectorisation"
        if recommendation == "vectorisation"
        else "the loop body is not entirely expressible with vectorising calls"
    )
    return (
        finding(
            "prefer-vectorisation-or-map",
            "foreach loop does not modify an outer variable and its body has no "
            f"element tags; prefer {recommendation} because {reason}",
            node,
        ),
    )


def _typed_loops(outputs: Any):
    """Yield the typed foreach emitted on each surviving analysis branch."""
    for output in outputs:
        for typed in reversed(output.typed_body):
            if isinstance(typed, TypedForNode):
                yield typed
                break


def _writes_outer_variable(nodes: tuple[ASTNode, ...], outer_names: frozenset) -> bool:
    """Return whether executable loop code assigns a name visible before the loop."""
    for node in nodes:
        if isinstance(node, FunctionNode):
            # Merely creating a closure does not execute its body.
            continue
        if isinstance(node, SetVariableNode) and node.name in outer_names:
            return True
        if isinstance(node, SetVariablesNode) and any(
            target.name in outer_names for target in node.targets
        ):
            return True
        if is_dataclass(node):
            for field in fields(node):
                value = getattr(node, field.name)
                nested = _ast_nodes(value)
                if nested and _writes_outer_variable(nested, outer_names):
                    return True
    return False


def _ast_nodes(value: object) -> tuple[ASTNode, ...]:
    """Return direct AST children contained in a dataclass field value."""
    if isinstance(value, ASTNode):
        return (value,)
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, ASTNode))
    return ()


def _walk_typed(values: object):
    """Yield typed nodes recursively from typed control-flow structures."""
    if isinstance(values, TypedNode):
        yield values
        if is_dataclass(values):
            for field in fields(values):
                if field.name in {"node", "typ"}:
                    continue
                yield from _walk_typed(getattr(values, field.name))
    elif isinstance(values, (tuple, list)):
        for value in values:
            yield from _walk_typed(value)


def _applied_overloads(nodes: tuple[ASTNode | TypedNode, ...]):
    """Yield resolved calls and validators found in a typed loop body."""
    for typed in _walk_typed(nodes):
        for name in ("overload", "validator"):
            applied = getattr(typed, name, None)
            if isinstance(applied, T.AppliedOverload):
                yield applied


def _has_element_tags(nodes: tuple[ASTNode | TypedNode, ...]) -> bool:
    """Return whether any resolved body operation carries element tags."""
    return any(applied.element_tags for applied in _applied_overloads(nodes))


def _all_calls_vectorise(nodes: tuple[ASTNode | TypedNode, ...]) -> bool:
    """Return whether every body call permits collection vectorisation."""
    calls = tuple(_applied_overloads(nodes))
    return bool(calls) and all(
        all(not isinstance(T.normalize(param), T.ExactType) for param in applied.overload.params)
        for applied in calls
    )
