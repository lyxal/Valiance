"""Declared and inferred language-contract validation."""

from __future__ import annotations

from typing import Any

from .lifecycle import _LifecycleContracts
from .tags import _TagContracts
from .validation import _AnnotationContracts

_CONTRACT_OWNERS = (
    _TagContracts,
    _LifecycleContracts,
    _AnnotationContracts,
)


class ContractAnalyser(*_CONTRACT_OWNERS):
    """Coordinate contract services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _CONTRACT_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )

    def __init__(self, context: Any) -> None:
        """Retain the orchestration context used by contract operations."""
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


__all__ = ["ContractAnalyser"]
