"""Extensible lint registry, contexts, findings, and built-in rule discovery.

Adding a built-in lint only requires a new module under ``lints/rules`` that
exports ``register(registry)``. Rule modules are discovered automatically and
never need to be wired into analyser implementation files.
"""

from .contexts import BlockLintContext, MatchLintContext, NodeLintContext
from .models import LintFinding, LintRewrite, RewriteKind, finding
from .registry import LintRegistry
from .rules import load_rules


KNOWN_LINT_CODES = frozenset(
    {
        "captured-write-not-persistent",
        "constant-never-reassigned",
        "duplicate-match-case",
        "duplicate-pattern-alternative",
        "explicit-map-can-vectorise",
        "no-op-copy",
        "no-op-move",
        "prefer-filter",
        "prefer-fold",
        "prefer-match",
        "prefer-sum",
        "prefer-vectorisation-or-map",
        "redundant-cast",
        "redundant-checked-cast",
        "safe-checked-cast",
        "unknown-lint-code",
        "unreachable-code",
        "unreachable-match-case",
        "unused-lint-suppression",
        "unused-loop-index",
        "unicode-identifier-security",
        "while-can-be-foreach",
    }
)

DEFAULT_REGISTRY = LintRegistry()
load_rules(DEFAULT_REGISTRY)

__all__ = [
    "BlockLintContext",
    "DEFAULT_REGISTRY",
    "KNOWN_LINT_CODES",
    "LintFinding",
    "LintRegistry",
    "LintRewrite",
    "MatchLintContext",
    "NodeLintContext",
    "RewriteKind",
    "finding",
]
