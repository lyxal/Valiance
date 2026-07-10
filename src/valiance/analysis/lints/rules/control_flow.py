"""Control-flow lint rules for unreachable statements."""

from __future__ import annotations

from valiance.asts import ReturnNode
from valiance.asts.nodes import BreakNode

from ..contexts import BlockLintContext
from ..models import LintRewrite, RewriteKind, finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register control-flow rules with a lint registry."""
    registry.register_block(unreachable_suffix)


def unreachable_suffix(context: BlockLintContext):
    """Report the first statement after a direct return or break."""
    nodes = context.nodes
    for index, node in enumerate(nodes[:-1]):
        if not isinstance(node, (ReturnNode, BreakNode)):
            continue
        keyword = "return" if isinstance(node, ReturnNode) else "break"
        return (
            finding(
                "unreachable-code",
                f"code after `{keyword}` is unreachable; "
                f"remove it or move it before the {keyword}",
                nodes[index + 1],
                rewrite=LintRewrite(RewriteKind.REMOVE_UNREACHABLE_SUFFIX),
            ),
        )
    return ()
