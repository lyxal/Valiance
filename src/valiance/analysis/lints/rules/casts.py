"""Lint rules for redundant or statically safe casts."""

from __future__ import annotations

import valiance.types as T
from valiance.asts import CastNode

from ..contexts import NodeLintContext
from ..models import LintRewrite, RewriteKind, finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register cast rules with a lint registry."""
    registry.register_node(CastNode, lint_cast)


def lint_cast(context: NodeLintContext):
    """Report casts whose runtime check or conversion is unnecessary."""
    node = context.node
    if not isinstance(node, CastNode) or not context.branch.stack:
        return ()

    source = context.branch.stack[-1]
    target = T.normalize(node.typ)
    if node.checked and T.assignable(source, target, context.env.context):
        if T.same(source, target):
            return (
                finding(
                    "redundant-checked-cast",
                    f"unnecessary checked cast to {T.show(target)}; "
                    f"remove `as! {T.show(target)}`",
                    node,
                    rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
                ),
            )
        return (
            finding(
                "safe-checked-cast",
                f"checked cast to {T.show(target)} is statically safe; "
                f"write `as {T.show(target)}` instead of "
                f"`as! {T.show(target)}`",
                node,
                rewrite=LintRewrite(
                    RewriteKind.REPLACE_NODE,
                    replacement=f"as {T.show(target)}",
                ),
            ),
        )

    if (
        not node.checked
        and T.assignable(source, target, context.env.context)
        and T.same(source, target)
    ):
        return (
            finding(
                "redundant-cast",
                f"unnecessary cast to {T.show(target)}; "
                f"remove `as {T.show(target)}`",
                node,
                rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
            ),
        )
    return ()
