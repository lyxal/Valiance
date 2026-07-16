"""Call candidate generation, selection, and callable-value planning."""

from __future__ import annotations

from typing import Any

from .arguments import _CallArguments
from .extensions import _CallExtensions
from .elements import _ElementCalls
from .functions import _CallableValues
from .selection import _CallSelection

_CALL_OWNERS = (
    _CallArguments,
    _CallSelection,
    _CallExtensions,
    _CallableValues,
    _ElementCalls,
)


class CallAnalyser(*_CALL_OWNERS):
    """Coordinate call-planning services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _CALL_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )

    def __init__(self, context: Any) -> None:
        """Retain the orchestration context used by call operations."""
        object.__setattr__(self, "_context", context)

    def provides(self, name: str) -> bool:
        """Return whether this subsystem owns an operation name."""
        return name in self._OPERATIONS

    def __getattr__(self, name: str):
        """Read shared state and cross-subsystem operations from the façade."""
        context = object.__getattribute__(self, "_context")
        return getattr(context, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Commit shared analysis state changes to the orchestration context."""
        if name == "_context":
            object.__setattr__(self, name, value)
            return
        context = object.__getattribute__(self, "_context")
        setattr(context, name, value)


__all__ = ["CallAnalyser"]
