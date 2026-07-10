"""Structured lint findings and semantics-preserving rewrite hints.

Lint findings are diagnostics first: the compiler does not currently apply their
rewrites.  The structured rewrite metadata is intentionally small so a future
optimiser can consume proven-safe patterns without parsing human-facing text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from valiance.asts import ASTNode, SourceLocation


class RewriteKind(str, Enum):
    """A structural action that could realise a lint recommendation."""

    REMOVE_NODE = "remove-node"
    REPLACE_NODE = "replace-node"
    REMOVE_UNREACHABLE_SUFFIX = "remove-unreachable-suffix"
    REMOVE_MATCH_CASE = "remove-match-case"
    REMOVE_PATTERN_ALTERNATIVE = "remove-pattern-alternative"


@dataclass(frozen=True)
class LintRewrite:
    """Machine-readable rewrite metadata attached to a lint finding.

    ``replacement`` is optional display text, not an executable source edit.
    Optimisers should dispatch on ``kind`` and inspect the finding's AST node.
    """

    kind: RewriteKind
    replacement: str | None = None
    semantics_preserving: bool = True


@dataclass(frozen=True)
class LintFinding:
    """One actionable lint with stable identity and optional rewrite metadata."""

    code: str
    message: str
    location: SourceLocation | None
    rewrite: LintRewrite | None = None
    node: ASTNode | None = field(default=None, compare=False, repr=False)

    def render(self) -> str:
        """Render the backwards-compatible location-prefixed lint message."""
        if self.location is None:
            return self.message
        return f"{self.location.line}:{self.location.column}: {self.message}"
