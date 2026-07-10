"""Extensible lint registry, contexts, findings, and built-in rule discovery.

Adding a built-in lint only requires a new module under ``lints/rules`` that
exports ``register(registry)``. Rule modules are discovered automatically and
never need to be wired into analyser implementation files.
"""

from .contexts import BlockLintContext, MatchLintContext, NodeLintContext
from .models import LintFinding, LintRewrite, RewriteKind, finding
from .registry import LintRegistry
from .rules import load_rules

DEFAULT_REGISTRY = LintRegistry()
load_rules(DEFAULT_REGISTRY)

__all__ = [
    "BlockLintContext",
    "DEFAULT_REGISTRY",
    "LintFinding",
    "LintRegistry",
    "LintRewrite",
    "MatchLintContext",
    "NodeLintContext",
    "RewriteKind",
    "finding",
]
