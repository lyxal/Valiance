"""Declared and inferred language-contract validation."""

from __future__ import annotations

from valiance.analysis.service_facade import AnalysisServiceFacade

from .lifecycle import _LifecycleContracts
from .tags import _TagContracts
from .validation import _AnnotationContracts

_CONTRACT_OWNERS = (
    _TagContracts,
    _LifecycleContracts,
    _AnnotationContracts,
)


class ContractAnalyser(AnalysisServiceFacade, *_CONTRACT_OWNERS):
    """Coordinate contract services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _CONTRACT_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )






__all__ = ["ContractAnalyser"]
