"""Extensible lint registry, contexts, findings, and built-in rule discovery.

Adding a built-in lint only requires a new module under ``lints/rules`` that
exports ``register(registry)``. Rule modules are discovered automatically and
never need to be wired into analyser implementation files.
"""

from .contexts import BlockLintContext, MatchLintContext, NodeLintContext
from .models import LintFinding, LintRewrite, RewriteKind, finding
from .registry import LintRegistry
from .rules import load_rules


from .codes import LINT_SHORT_CODES, canonical_lint_code, short_lint_code


KNOWN_LINT_CODES = frozenset(LINT_SHORT_CODES)
KNOWN_LINT_IDENTIFIERS = frozenset((*LINT_SHORT_CODES, *LINT_SHORT_CODES.values()))

DEFAULT_REGISTRY = LintRegistry()
load_rules(DEFAULT_REGISTRY)

__all__ = [
    "BlockLintContext",
    "DEFAULT_REGISTRY",
    "KNOWN_LINT_CODES",
    "KNOWN_LINT_IDENTIFIERS",
    "LINT_SHORT_CODES",
    "canonical_lint_code",
    "short_lint_code",
    "LintFinding",
    "LintRegistry",
    "LintRewrite",
    "MatchLintContext",
    "NodeLintContext",
    "RewriteKind",
    "finding",
]
