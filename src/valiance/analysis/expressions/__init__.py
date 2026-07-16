"""Value-level expression semantics."""

from __future__ import annotations

from typing import Any

from .fields import _FieldExpressions

_EXPRESSION_OWNERS = (_FieldExpressions,)


class ExpressionAnalyser(*_EXPRESSION_OWNERS):
    """Coordinate expression services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _EXPRESSION_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )

    def __init__(self, context: Any) -> None:
        """Retain the orchestration context used by expression operations."""
        object.__setattr__(self, "_context", context)

    def provides(self, name: str) -> bool:
        """Return whether this subsystem owns an operation name."""
        return name in self._OPERATIONS

    def __getattr__(self, name: str):
        """Read shared state and cross-subsystem operations from the façade."""
        return getattr(object.__getattribute__(self, "_context"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Commit shared analysis state changes to the orchestration context."""
        if name == "_context":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_context"), name, value)


__all__ = ["ExpressionAnalyser"]
