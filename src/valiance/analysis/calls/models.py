"""Value models exchanged between call-analysis stages."""

from __future__ import annotations

from dataclasses import dataclass

import valiance.vtypes as T
from valiance.asts import FunctionOverloadTyping, TypedFunctionNode, TypedNode

from ..state import AnalysisBranch


@dataclass(frozen=True)
class FunctionAnalysis:
    """Typed function literal result, including per-overload typed bodies."""

    typ: T.Type
    overloads: tuple[FunctionOverloadTyping, ...]

@dataclass(frozen=True)
class ListItemAnalysis:
    """One possible analysis result for a forked literal item."""

    branch: AnalysisBranch
    typ: T.Type
    consumed: int
    typed_body: tuple[TypedNode, ...]

@dataclass(frozen=True)
class ModifierArgumentAnalysis:
    """Analysed function value supplied by an element modifier."""

    typ: T.Type
    typed_node: TypedFunctionNode

@dataclass(frozen=True)
class ElementArguments:
    overload: T.Overload
    overload_index: int
    arguments: tuple[T.Type, ...]
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()

@dataclass(frozen=True)
class OverloadApplication:
    applied: T.AppliedOverload
    branch: AnalysisBranch

@dataclass(frozen=True)
class CallCandidate:
    applied: T.AppliedOverload
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()
    callable_overload_index: int | None = None
    overload_index: int | None = None
    dispatch_priority: int = 1

@dataclass(frozen=True)
class ElementCallPreparation:
    """Analysed explicit call arguments plus their runtime stack order."""

    branch: AnalysisBranch
    call_arg_order: tuple[int, ...]

