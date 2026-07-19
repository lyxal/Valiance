"""Language-level symbol values and namespace formatting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class Symbol:
    """A language-level name."""

    text: str
    namespace: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate invariants after constructing this symbol."""
        if not self.text:
            raise ValueError("symbol text cannot be empty")

    def __str__(self) -> str:
        """Return the human-readable representation of this symbol."""
        return self.dotted()

    def dotted(self) -> str:
        """Return the namespace-qualified spelling of this symbol."""
        if not self.namespace:
            return self.text
        return ".".join((*self.namespace, self.text))


def tag_symbol(name: str | Symbol) -> Symbol:
    """Normalize parser-facing tag names into symbol-table keys."""
    return name if isinstance(name, Symbol) else Symbol(name)
