"""Declaration registration and type-shape construction."""

from __future__ import annotations

from typing import Any

from .enums import _EnumDeclarations
from .functions import _FunctionDeclarations
from .imports import _ImportDeclarations
from .objects import _ObjectDeclarations
from .traits import _TraitDeclarations
from .variants import _VariantDeclarations

_DECLARATION_OWNERS = (
    _FunctionDeclarations,
    _ObjectDeclarations,
    _TraitDeclarations,
    _VariantDeclarations,
    _EnumDeclarations,
    _ImportDeclarations,
)


class DeclarationAnalyser(*_DECLARATION_OWNERS):
    """Coordinate declaration services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _DECLARATION_OWNERS
        for name in owner.__dict__
        if (name.startswith("_") and not name.startswith("__"))
        or name == "prepare_defined_overload"
    )

    def __init__(self, context: Any) -> None:
        """Retain the orchestration context used by declaration operations."""
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


__all__ = ["DeclarationAnalyser"]
