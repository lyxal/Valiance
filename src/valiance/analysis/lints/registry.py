"""Registration and dispatch for analyser-independent lint rules."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from valiance.asts import ASTNode

from .contexts import BlockLintContext, MatchLintContext, NodeLintContext
from .models import LintFinding

BlockLintRule = Callable[[BlockLintContext], Iterable[LintFinding]]
NodeLintRule = Callable[[NodeLintContext], Iterable[LintFinding]]
MatchLintRule = Callable[[MatchLintContext], Iterable[LintFinding]]


class LintRegistry:
    """Collection of lint rules dispatched by analyser lifecycle event."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._block_rules: list[BlockLintRule] = []
        self._node_rules: dict[type[ASTNode], list[NodeLintRule]] = defaultdict(list)
        self._match_rules: list[MatchLintRule] = []

    def register_block(self, rule: BlockLintRule) -> BlockLintRule:
        """Register a rule that inspects each lexical block."""
        self._block_rules.append(rule)
        return rule

    def register_node(
        self,
        node_type: type[ASTNode],
        rule: NodeLintRule,
    ) -> NodeLintRule:
        """Register a rule for one AST node type."""
        self._node_rules[node_type].append(rule)
        return rule

    def register_match(self, rule: MatchLintRule) -> MatchLintRule:
        """Register a rule for semantically valid match patterns."""
        self._match_rules.append(rule)
        return rule

    def check_block(self, context: BlockLintContext) -> tuple[LintFinding, ...]:
        """Run every rule registered for lexical blocks."""
        return tuple(
            finding
            for rule in self._block_rules
            for finding in rule(context)
        )

    def check_node(self, context: NodeLintContext) -> tuple[LintFinding, ...]:
        """Run rules registered for the analysed node's concrete type."""
        return tuple(
            finding
            for rule in self._node_rules.get(type(context.node), ())
            for finding in rule(context)
        )

    def check_match(self, context: MatchLintContext) -> tuple[LintFinding, ...]:
        """Run every rule registered for validated match patterns."""
        return tuple(
            finding
            for rule in self._match_rules
            for finding in rule(context)
        )
