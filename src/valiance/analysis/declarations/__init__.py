"""Declaration registration and type-shape construction."""

from __future__ import annotations

from valiance.analysis.service_facade import AnalysisServiceFacade

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


class DeclarationAnalyser(AnalysisServiceFacade, *_DECLARATION_OWNERS):
    """Coordinate declaration services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _DECLARATION_OWNERS
        for name in owner.__dict__
        if (name.startswith("_") and not name.startswith("__"))
        or name == "prepare_defined_overload"
    )






__all__ = ["DeclarationAnalyser"]
