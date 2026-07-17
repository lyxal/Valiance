"""Unicode identifier validation and security-oriented diagnostics."""

from __future__ import annotations

import unicodedata

_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs"})
_COMMON_SCRIPTS = frozenset({"COMMON", "INHERITED"})
_CONFUSABLE_WITH_ASCII = frozenset(
    "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧϲаеорсухіјѕԁԛ"
)


def is_xid_start(char: str) -> bool:
    """Return whether one scalar is XID_Start, with underscore as an extension."""
    return len(char) == 1 and (char == "_" or char.isidentifier())


def is_xid_continue(char: str) -> bool:
    """Return whether one scalar is XID_Continue."""
    return len(char) == 1 and ("A" + char).isidentifier()


def forbidden_identifier_character(char: str) -> bool:
    """Reject controls, formats, private-use characters, and surrogate code points."""
    return bool(char) and unicodedata.category(char) in _FORBIDDEN_CATEGORIES


def normalize_identifier(value: str) -> str:
    """Return the canonical NFC spelling used for identifier comparison."""
    return unicodedata.normalize("NFC", value)


def identifier_scripts(value: str) -> frozenset[str]:
    """Return significant scripts approximated from Unicode character names."""
    scripts: set[str] = set()
    for char in value:
        if char == "_" or char.isdigit() or unicodedata.combining(char):
            continue
        name = unicodedata.name(char, "")
        script = name.split(" ", 1)[0] if name else "COMMON"
        if script not in _COMMON_SCRIPTS:
            scripts.add(script)
    return frozenset(scripts)


def identifier_security_issues(value: str) -> tuple[str, ...]:
    """Return non-fatal mixed-script and common confusable warnings."""
    issues: list[str] = []
    scripts = identifier_scripts(value)
    if len(scripts) > 1:
        issues.append("identifier uses multiple scripts " + ", ".join(sorted(scripts)))
    if any(char in _CONFUSABLE_WITH_ASCII for char in value) and any(
        char.isascii() and char.isalpha() for char in value
    ):
        issues.append(
            "identifier contains non-ASCII characters visually confusable with ASCII"
        )
    return tuple(issues)
