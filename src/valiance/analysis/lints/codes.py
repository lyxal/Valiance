"""Stable long and short identifiers for built-in lint rules."""

from __future__ import annotations


LINT_SHORT_CODES: dict[str, str] = {
    "captured-write-not-persistent": "L001",
    "constant-never-reassigned": "L002",
    "duplicate-match-case": "L003",
    "duplicate-pattern-alternative": "L004",
    "explicit-map-can-vectorise": "L005",
    "no-op-copy": "L006",
    "no-op-move": "L007",
    "prefer-filter": "L008",
    "prefer-fold": "L009",
    "prefer-match": "L010",
    "prefer-sum": "L011",
    "prefer-vectorisation-or-map": "L012",
    "redundant-cast": "L013",
    "redundant-checked-cast": "L014",
    "safe-checked-cast": "L015",
    "unknown-lint-code": "L016",
    "unreachable-code": "L017",
    "unreachable-match-case": "L018",
    "unused-lint-suppression": "L019",
    "unused-loop-index": "L020",
    "unicode-identifier-security": "L021",
    "while-can-be-foreach": "L022",
}

LINT_LONG_CODES: dict[str, str] = {
    short.lower(): long for long, short in LINT_SHORT_CODES.items()
}


def canonical_lint_code(code: str) -> str | None:
    """Return the stable long code for a long or short lint identifier."""
    if code in LINT_SHORT_CODES:
        return code
    return LINT_LONG_CODES.get(code.lower())


def short_lint_code(code: str) -> str | None:
    """Return the short display code for a built-in long lint identifier."""
    return LINT_SHORT_CODES.get(code)
