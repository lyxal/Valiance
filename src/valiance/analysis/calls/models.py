"""Value models exchanged between call-analysis stages."""

from __future__ import annotations

from dataclasses import dataclass

import valiance.vtypes as T
from valiance.asts import FunctionOverloadTyping, TypedFunctionNode, TypedFunctionDispatchNode, TypedNode

from ..state import AnalysisBranch


class ReflectionSpecializationRequired(Exception):
    """Defer a generic body until its reflected owner is concrete."""

    def __init__(self, owner: T.Type) -> None:
        """Retain owner evidence so enclosing binders can resolve captures."""
        self.owner = owner
        super().__init__(str(owner))


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
    outer: AnalysisBranch | None = None

@dataclass(frozen=True)
class ElementArguments:
    overload: T.Overload
    implementation_id: str
    arguments: tuple[T.Type, ...]
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()

@dataclass(frozen=True)
class OverloadApplication:
    applied: T.AppliedOverload
    branch: AnalysisBranch
    specialization: TypedFunctionNode | TypedFunctionDispatchNode | None = None

@dataclass(frozen=True)
class CallCandidate:
    applied: T.AppliedOverload
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()
    callable_implementation_id: str | None = None
    implementation_id: str | None = None
    dispatch_priority: int = 1
    # Concrete argument types, retained for compile-time trait evidence.
    source_arguments: tuple[T.Type, ...] = ()
    state_arity: int | None = None
    specialization: TypedFunctionNode | TypedFunctionDispatchNode | None = None

@dataclass(frozen=True)
class ElementCallPreparation:
    """Analysed explicit call arguments plus their runtime stack order."""

    branch: AnalysisBranch
    call_arg_order: tuple[int, ...]

