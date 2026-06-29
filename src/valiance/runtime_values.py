"""Helpers for runtime values shared by builtins, the VM, and the CLI."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence, Sized
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LazyList:
    """A list-like value backed by an iterable that may be lazy or infinite."""

    iterable: Iterable[Any]

    def __iter__(self):
        return iter(self.iterable)


def is_list_like(value: Any) -> bool:
    """Return whether a runtime value behaves like a Valiance list."""
    return (
        isinstance(value, Iterable)
        and not isinstance(value, (str, bytes, tuple, Mapping))
    )


def is_finite_list_like(value: Any) -> bool:
    """Return whether a list-like value has a known finite length."""
    return is_list_like(value) and isinstance(value, Sized)


def is_eager_sequence(value: Any) -> bool:
    """Return whether a list-like value can be indexed without consumption."""
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, tuple))
