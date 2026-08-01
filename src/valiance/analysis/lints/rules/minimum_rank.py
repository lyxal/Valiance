"""Lint rules for minimum-rank assurance."""

from __future__ import annotations

import valiance.vtypes as T
from valiance.asts import MinimumRankNode

from ..contexts import NodeLintContext
from ..models import LintRewrite, RewriteKind, finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register minimum-rank assurance rules with a lint registry."""
    registry.register_node(MinimumRankNode, lint_minimum_rank)


def _never_wrapped(typ: T.Type, target_rank: int) -> bool:
    """Return whether every value described by ``typ`` already meets the rank."""
    typ = T.normalize(typ)
    if isinstance(typ, T.UnionType):
        return all(_never_wrapped(item, target_rank) for item in typ.items)
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _never_wrapped(typ.inner, target_rank)
    return (
        isinstance(typ, (T.ListExactType, T.ListMinType, T.ListRuggedType))
        and isinstance(typ.rank, int)
        and typ.rank >= target_rank
    )


def lint_minimum_rank(context: NodeLintContext):
    """Report assurances for which no possible input value is wrapped."""
    node = context.node
    if not isinstance(node, MinimumRankNode) or not context.branch.stack:
        return ()
    source = context.branch.stack[-1]
    if not _never_wrapped(source, node.rank):
        return ()
    spelling = "^+" if node.rank == 1 else f"^+{node.rank}"
    return (
        finding(
            "minimum-rank-never-wraps",
            f"{T.show(source)} already has minimum rank {node.rank}; "
            f"`{spelling}` never wraps this type",
            node,
            rewrite=LintRewrite(RewriteKind.REMOVE_NODE),
        ),
    )
