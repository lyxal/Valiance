"""Call candidate generation, selection, and callable-value planning."""

from __future__ import annotations

from valiance.analysis.service_facade import AnalysisServiceFacade

from .arguments import _CallArguments
from .callable_values import choose_best_overload
from .elements import _ElementCalls
from .extensions import _CallExtensions
from .functions import _CallableValues
from .selection import _CallSelection

_CALL_OWNERS = (
    _CallArguments,
    _CallSelection,
    _CallExtensions,
    _CallableValues,
    _ElementCalls,
)


class CallAnalyser(AnalysisServiceFacade, *_CALL_OWNERS):
    """Coordinate call-planning services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _CALL_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )






__all__ = ["CallAnalyser", "choose_best_overload"]
