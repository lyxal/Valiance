"""Value-level expression semantics."""

from __future__ import annotations

from valiance.analysis.service_facade import AnalysisServiceFacade

from .fields import _FieldExpressions

_EXPRESSION_OWNERS = (_FieldExpressions,)


class ExpressionAnalyser(AnalysisServiceFacade, *_EXPRESSION_OWNERS):
    """Coordinate expression services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _EXPRESSION_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )






__all__ = ["ExpressionAnalyser"]
