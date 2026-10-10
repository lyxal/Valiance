"""Immutable session presentation and worker lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from valiance.analysis.diagnostics import Diagnostic
    from valiance.incremental.snapshots import WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class CompletionItem:
    """One language candidate, independent of a terminal rendering framework."""

    text: str
    meta: str


@dataclass(frozen=True, slots=True)
class OutputEvent:
    """Printed program output, separate from implicit stack results."""

    text: str


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """A structured diagnostic and the immutable source it describes."""

    diagnostic: Diagnostic
    source: str


@dataclass(frozen=True, slots=True)
class ResultEvent:
    """Formatted runtime stack from top to bottom; no live VM references."""

    values: tuple[str, ...]
    types: tuple[str, ...]
    implicit: bool


@dataclass(frozen=True, slots=True)
class SessionStarted:
    """A successful fresh-load commit, before any top-level effects execute."""

    document_id: str
    revision: int


type SessionEvent = OutputEvent | DiagnosticEvent | ResultEvent | SessionStarted


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """Serializable worker intent, tagged independently from program stdout.

    Load carries a captured workspace. Command carries source. Shutdown and Stop
    belong to the control channel and must never wait behind output messages.
    Prepared compiler objects remain exclusively inside the worker process.
    """

    request_id: int
    operation: Literal["load", "command", "reset", "stop", "shutdown"]
    source: str = ""
    snapshot: WorkspaceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class SessionResponse:
    """Correlated lifecycle boundary or immutable presentation event."""

    request_id: int
    status: Literal["ready", "event", "finished", "failed", "stopped", "closed"]
    event: SessionEvent | None = None
