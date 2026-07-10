"""Lint rules for stack shuffles that cannot change program state."""

from __future__ import annotations

from typing import cast

import valiance.types as T
from valiance.asts import StackShuffleNode
from valiance.symbols import Symbol

from ..contexts import NodeLintContext
from ..models import LintRewrite, RewriteKind, finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register stack-shuffle rules with a lint registry."""
    registry.register_node(StackShuffleNode, lint_stack_shuffle)


def lint_stack_shuffle(context: NodeLintContext):
    """Report moves and copies that leave the stack unchanged."""
    node = context.node
    if not isinstance(node, StackShuffleNode):
        return ()

    params = tuple(
        T.V(f"_lint_shuffle_{index}")
        for index, _ in enumerate(node.prestack)
    )
    if context.branch.source_arguments(params) is None:
        return ()

    if _is_noop_move(node):
        labels = ", ".join(str(label) for label in node.poststack)
        return (
            finding(
                "no-op-move",
                "this move leaves the stack unchanged; "
                f"remove `move({labels} -> {labels})`",
                node,
                rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
            ),
        )
    if node.mode == Symbol("copy") and not node.poststack:
        labels = ", ".join(
            "_" if label is None else str(label) for label in node.prestack
        )
        return (
            finding(
                "no-op-copy",
                "this copy produces no values and has no effect; "
                f"remove `copy({labels} ->)`",
                node,
                rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
            ),
        )
    return ()


def _is_noop_move(node: StackShuffleNode) -> bool:
    """Return whether a move names the same unique stack segment in order."""
    if node.mode != Symbol("move"):
        return False
    if any(label is None for label in node.prestack):
        return False
    labels = tuple(cast(Symbol, label) for label in node.prestack)
    return labels == node.poststack and len(set(labels)) == len(labels)
