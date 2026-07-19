"""Shared façade behavior for focused analyser service objects."""

from __future__ import annotations

from typing import Any


class AnalysisServiceFacade:
    """Forward shared analysis state to an owning analyser context."""

    _OPERATIONS: frozenset[str] = frozenset()

    def __init__(self, context: Any) -> None:
        """Retain the orchestration context used by service operations."""
        object.__setattr__(self, "_context", context)

    def provides(self, name: str) -> bool:
        """Return whether this service owns an operation name."""
        return name in self._OPERATIONS

    def __getattr__(self, name: str):
        """Read shared state and cross-service operations from the context."""
        return getattr(object.__getattribute__(self, "_context"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Commit shared analysis state changes to the context."""
        if name == "_context":
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_context"), name, value)
