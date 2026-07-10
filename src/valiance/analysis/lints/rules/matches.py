"""Lint rules for unreachable and duplicated match patterns."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast

from valiance.asts import (
    BindingPatternNode,
    ListPatternNode,
    LiteralPatternNode,
    MatchPatternNode,
    NumberLiteralNode,
    OrPatternNode,
    RestPatternNode,
    StringLiteralNode,
    TypePatternNode,
    WildcardPatternNode,
)
from valiance.symbols import Symbol

from ..contexts import MatchLintContext
from ..models import LintFinding, LintRewrite, RewriteKind, finding
from ..registry import LintRegistry


def register(registry: LintRegistry) -> None:
    """Register match-pattern rules with a lint registry."""
    registry.register_match(lint_match_patterns)


def lint_match_patterns(context: MatchLintContext):
    """Report unreachable cases and duplicate stable literal patterns."""
    findings: list[LintFinding] = []
    seen_literal_cases: set[tuple[tuple[str, object], ...]] = set()
    default_seen = False
    for case in context.node.cases:
        for pattern in case.patterns:
            findings.extend(_duplicate_pattern_alternatives(pattern))

        if default_seen:
            findings.append(
                finding(
                    "unreachable-match-case",
                    "match case is unreachable because an earlier case "
                    "matches every value; remove this case",
                    case,
                    rewrite=LintRewrite(RewriteKind.REMOVE_MATCH_CASE),
                )
            )
            continue

        key = _literal_match_case_key(case.patterns)
        if key is not None:
            if key in seen_literal_cases:
                findings.append(
                    finding(
                        "duplicate-match-case",
                        "duplicate match case; remove this case because the "
                        "same literal pattern appears earlier",
                        case,
                        rewrite=LintRewrite(RewriteKind.REMOVE_MATCH_CASE),
                    )
                )
            else:
                seen_literal_cases.add(key)

        if _is_default_match_case(case.patterns):
            default_seen = True
    return findings


def _duplicate_pattern_alternatives(
    pattern: MatchPatternNode,
) -> tuple[LintFinding, ...]:
    """Return findings for repeated literals nested in one or-pattern."""
    findings: list[LintFinding] = []
    if isinstance(pattern, OrPatternNode):
        seen: set[tuple[str, object]] = set()
        for option in pattern.options:
            key = _literal_match_pattern_key(option)
            if key is not None:
                if key in seen:
                    findings.append(
                        finding(
                            "duplicate-pattern-alternative",
                            "duplicate match alternative; remove the repeated "
                            "literal pattern",
                            option,
                            rewrite=LintRewrite(
                                RewriteKind.REMOVE_PATTERN_ALTERNATIVE
                            ),
                        )
                    )
                else:
                    seen.add(key)
            findings.extend(_duplicate_pattern_alternatives(option))
    elif isinstance(pattern, BindingPatternNode):
        findings.extend(_duplicate_pattern_alternatives(pattern.pattern))
    elif isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            findings.extend(_duplicate_pattern_alternatives(item))
    elif isinstance(pattern, TypePatternNode):
        for field_pattern in pattern.fields:
            findings.extend(_duplicate_pattern_alternatives(field_pattern))
    return tuple(findings)


def _literal_match_pattern_key(
    pattern: MatchPatternNode,
) -> tuple[str, object] | None:
    """Return a stable key only for side-effect-free literal patterns."""
    if not isinstance(pattern, LiteralPatternNode):
        return None
    value = pattern.value
    if isinstance(value, NumberLiteralNode):
        try:
            return ("number", Decimal(value.value))
        except InvalidOperation:
            return None
    if isinstance(value, StringLiteralNode):
        return ("string", value.value)
    return None


def _literal_match_case_key(
    patterns: tuple[MatchPatternNode, ...],
) -> tuple[tuple[str, object], ...] | None:
    """Return a key when every case item is a stable literal pattern."""
    keys = tuple(_literal_match_pattern_key(pattern) for pattern in patterns)
    if not keys or any(key is None for key in keys):
        return None
    return cast(tuple[tuple[str, object], ...], keys)


def _is_default_match_case(patterns: tuple[MatchPatternNode, ...]) -> bool:
    """Return whether a case accepts every combination of subject values."""
    return (
        bool(patterns)
        and not _has_repeated_match_bindings(patterns)
        and all(_is_default_match_pattern(pattern) for pattern in patterns)
    )


def _is_default_match_pattern(pattern: MatchPatternNode) -> bool:
    """Return whether a pattern unconditionally accepts every subject value."""
    if _has_repeated_match_bindings((pattern,)):
        return False
    if isinstance(pattern, (WildcardPatternNode, RestPatternNode)):
        return True
    if isinstance(pattern, BindingPatternNode):
        return _is_default_match_pattern(pattern.pattern)
    if isinstance(pattern, OrPatternNode):
        return any(_is_default_match_pattern(option) for option in pattern.options)
    return (
        isinstance(pattern, TypePatternNode)
        and pattern.typ is None
        and not pattern.fields
        and not pattern.guard
    )


def _pattern_binding_counts(pattern: MatchPatternNode) -> dict[Symbol, int]:
    """Return maximum binding occurrences along one successful pattern path."""

    def combine(
        left: dict[Symbol, int],
        right: dict[Symbol, int],
    ) -> dict[Symbol, int]:
        """Add binding counts from conjunctive child patterns."""
        result = dict(left)
        for name, count in right.items():
            result[name] = result.get(name, 0) + count
        return result

    if isinstance(pattern, BindingPatternNode):
        result = _pattern_binding_counts(pattern.pattern)
        result[pattern.name] = result.get(pattern.name, 0) + 1
        return result
    if isinstance(pattern, RestPatternNode):
        return {} if pattern.name is None else {pattern.name: 1}
    if isinstance(pattern, TypePatternNode):
        result = {} if pattern.name is None else {pattern.name: 1}
        for field in pattern.fields:
            result = combine(result, _pattern_binding_counts(field))
        return result
    if isinstance(pattern, ListPatternNode):
        result: dict[Symbol, int] = {}
        for item in pattern.items:
            result = combine(result, _pattern_binding_counts(item))
        return result
    if isinstance(pattern, OrPatternNode):
        result: dict[Symbol, int] = {}
        for option in pattern.options:
            for name, count in _pattern_binding_counts(option).items():
                result[name] = max(result.get(name, 0), count)
        return result
    return {}


def _has_repeated_match_bindings(
    patterns: tuple[MatchPatternNode, ...],
) -> bool:
    """Return whether a successful case path can bind one name twice."""
    counts: dict[Symbol, int] = {}
    for pattern in patterns:
        for name, count in _pattern_binding_counts(pattern).items():
            counts[name] = counts.get(name, 0) + count
    return any(count > 1 for count in counts.values())
