"""Branch-producing control-flow analysis."""

from __future__ import annotations

from valiance.analysis.service_facade import AnalysisServiceFacade

from .exceptions import _ExceptionAnalysis
from .exhaustiveness import _ExhaustivenessAnalysis
from .loops import _LoopAnalysis
from .matches import _MatchAnalysis

_CONTROL_FLOW_OWNERS = (
    _MatchAnalysis,
    _ExhaustivenessAnalysis,
    _ExceptionAnalysis,
    _LoopAnalysis,
)


class ControlFlowAnalyser(AnalysisServiceFacade, *_CONTROL_FLOW_OWNERS):
    """Coordinate control-flow services over a shared analysis context."""

    _OPERATIONS = frozenset(
        name
        for owner in _CONTROL_FLOW_OWNERS
        for name in owner.__dict__
        if not name.startswith("__")
    )






__all__ = ["ControlFlowAnalyser"]
