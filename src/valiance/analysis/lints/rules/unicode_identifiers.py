"""Warnings for Unicode identifiers that may be difficult to distinguish."""

from __future__ import annotations

from valiance.asts import GetVariableNode, SetVariableNode
from valiance.parsing.unicode_identifiers import identifier_security_issues

from ..contexts import NodeLintContext
from ..models import finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register Unicode identifier security diagnostics."""
    registry.register_node(GetVariableNode, suspicious_identifier)
    registry.register_node(SetVariableNode, suspicious_identifier)


def suspicious_identifier(context: NodeLintContext):
    """Warn without rejecting legitimate multilingual identifiers."""
    node = context.node
    if not isinstance(node, (GetVariableNode, SetVariableNode)):
        return ()
    name = str(node.name)
    return tuple(
        finding(
            "unicode-identifier-security",
            f"identifier '${name}' {issue}; consider a single, visually distinct script",
            node,
        )
        for issue in identifier_security_issues(name)
    )
