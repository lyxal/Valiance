from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True, slots=True)
class Symbol:
    """A language-level name."""

    text: str
    namespace: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("symbol text cannot be empty")

    def __str__(self) -> str:
        return self.dotted()

    def dotted(self) -> str:
        if not self.namespace:
            return self.text
        return ".".join((*self.namespace, self.text))
