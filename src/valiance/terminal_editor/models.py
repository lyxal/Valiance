"""Independent document, transcript, history and session presentation state."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from valiance.incremental.database import CompilationDatabase
from valiance.incremental.snapshots import RootSource, WorkspaceSnapshot
from valiance.sessions.events import (
    DiagnosticEvent,
    OutputEvent,
    ResultEvent,
    SessionStarted,
)
from valiance.sessions.service import ExecutionResult, SessionService


@dataclass(frozen=True, slots=True)
class DocumentView:
    """Source coordinates and viewport; native TextArea owns editing undo state."""

    cursor: tuple[int, int] = (0, 0)
    selection_start: tuple[int, int] = (0, 0)
    scroll: tuple[int, int] = (0, 0)


@dataclass(frozen=True, slots=True)
class Document:
    """Stable identity with a separate saved baseline and current source revision."""

    id: str
    source: str
    saved_source: str | None
    launch_directory: Path
    path: Path | None = None
    revision: int = 0
    suggested_name: str = "Untitled"
    view: DocumentView = DocumentView()

    @property
    def dirty(self) -> bool:
        """Treat a blank untitled document as clean until its text changes."""
        return self.source != self.saved_source

    @property
    def root_source(self) -> RootSource:
        """Represent untitled identity without inventing a saved file."""
        base = self.path.parent if self.path else self.launch_directory
        return RootSource(self.id, self.source, self.revision, base, self.path)


class EditorWorkspace:
    """Own document identity and feed every open disk document to one database."""

    def __init__(
        self, launch_directory: Path, database: CompilationDatabase | None = None
    ) -> None:
        """Start with a blank untitled document and no restored source execution."""
        self.launch_directory = launch_directory.resolve()
        self.database = database or CompilationDatabase()
        self.documents: dict[str, Document] = {}
        self.revision = 0
        self._untitled_count = 0
        self.active_document = self.new_document().id

    def new_document(self, source: str = "") -> Document:
        """Create a stable untitled identity; source is not written to disk."""
        self._untitled_count += 1
        document = Document(
            uuid4().hex,
            source,
            "",
            self.launch_directory,
            suggested_name=f"Untitled {self._untitled_count}",
        )
        self.documents[document.id] = document
        self.active_document = document.id
        self.revision += 1
        return document

    def open_document(
        self, path: Path, source: str | None = None, *, saved_source: str | None = None
    ) -> Document:
        """Reuse canonical path identity and preserve an already-open overlay."""
        path = path.resolve()
        for document in self.documents.values():
            if document.path == path:
                self.active_document = document.id
                return document
        saved = (
            path.read_bytes().decode("utf-8") if saved_source is None else saved_source
        )
        document = Document(
            uuid4().hex,
            saved if source is None else source,
            saved,
            self.launch_directory,
            path,
        )
        self.documents[document.id] = document
        self.active_document = document.id
        self.database.open_document(path, document.source, version=document.revision)
        self.revision += 1
        return document

    def update(
        self, document_id: str, source: str, *, view: DocumentView | None = None
    ) -> Document:
        """Advance text revisions and invalidate importer overlays on every edit."""
        old = self.documents[document_id]
        changed = source != old.source
        document = replace(
            old,
            source=source,
            revision=old.revision + int(changed),
            view=old.view if view is None else view,
        )
        self.documents[document_id] = document
        if changed:
            self.revision += 1
            if document.path:
                self.database.replace_document(
                    document.path, source, version=document.revision
                )
        return document

    def associate_path(
        self, document_id: str, path: Path, *, saved_source: str | None
    ) -> Document:
        """Apply a successful save's identity without writing or executing source."""
        path = path.resolve()
        if any(
            doc.path == path and doc.id != document_id
            for doc in self.documents.values()
        ):
            raise ValueError("path is already open")
        old = self.documents[document_id]
        if old.path and old.path != path:
            self.database.close_document(old.path)
        document = replace(
            old, path=path, saved_source=saved_source, revision=old.revision + 1
        )
        self.documents[document_id] = document
        self.database.open_document(path, document.source, version=document.revision)
        self.revision += 1
        return document

    def close_document(self, document_id: str) -> None:
        """Apply an approved close; dirty-file prompts belong to the frontend."""
        document = self.documents.pop(document_id)
        if document.path:
            self.database.close_document(document.path)
        self.revision += 1
        if not self.documents:
            self.new_document()
        elif self.active_document == document_id:
            self.active_document = next(iter(self.documents))

    def capture(self, document_id: str) -> WorkspaceSnapshot:
        """Freeze root and import closure for preparation in a worker."""
        return self.database.capture_workspace(
            self.documents[document_id].root_source,
            workspace_revision=self.revision,
        )

    def is_current(self, snapshot: WorkspaceSnapshot) -> bool:
        """Reject obsolete document/dependency responses, including closed roots."""
        document = self.documents.get(snapshot.root.document_id)
        return bool(
            document
            and snapshot.workspace_revision == self.revision
            and self.database.snapshot_is_current(snapshot, document.root_source)
        )


class RuntimeStatus(StrEnum):
    """Runtime lifecycle is independent from active document and input lock."""

    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """A semantic transcript entry before terminal styling and wrapping."""

    kind: str
    text: str


class Transcript:
    """Bound retained entries and characters, with explicit export truncation."""

    def __init__(self, *, max_entries: int = 300, max_characters: int = 64_000) -> None:
        """Validate retention bounds rather than allowing unbounded output."""
        if max_entries < 1 or max_characters < 1:
            raise ValueError("transcript limits must be positive")
        self.max_entries, self.max_characters = max_entries, max_characters
        self.entries: deque[TranscriptEntry] = deque()
        self.characters = 0
        self.truncated = False

    def append(self, entry: TranscriptEntry) -> None:
        """Retain newest entries and record both item and character truncation."""
        if len(entry.text) > self.max_characters:
            entry = replace(entry, text=entry.text[-self.max_characters :])
            self.truncated = True
        while self.entries and (
            len(self.entries) >= self.max_entries
            or self.characters + len(entry.text) > self.max_characters
        ):
            self.characters -= len(self.entries.popleft().text)
            self.truncated = True
        self.entries.append(entry)
        self.characters += len(entry.text)

    def clear(self) -> None:
        """Clear presentation only; this model has no runtime references."""
        self.entries.clear()
        self.characters = 0
        self.truncated = False

    def export_text(self) -> str:
        """Export only retained text with an explicit truncation marker."""
        marker = "[Earlier transcript output dropped]\n" if self.truncated else ""
        return marker + "\n".join(entry.text for entry in self.entries)


@dataclass(frozen=True, slots=True)
class InputDraft:
    """Draft text, caret, selection and scroll preserved during history recall."""

    source: str = ""
    view: DocumentView = DocumentView()


@dataclass
class InputState:
    """Lock and history preferences never imply source or runtime restoration."""

    locked: bool = False
    history: list[str] = field(default_factory=list)
    limit: int = 500
    _index: int | None = None
    _draft: InputDraft = InputDraft()

    def __post_init__(self) -> None:
        """Apply a positive retention bound to externally supplied history."""
        if self.limit < 1:
            raise ValueError("history limit must be positive")
        self.history = list(self.history[-self.limit :])

    def record(self, source: str) -> None:
        """Record nonempty submissions and reset browsing without changing lock."""
        if source.strip():
            self.history.append(source)
            del self.history[: -self.limit]
        self._index = None

    def previous(self, draft: InputDraft) -> InputDraft:
        """Recall history only while unlocked, preserving the unfinished draft."""
        if self.locked or not self.history:
            return draft
        if self._index is None:
            self._draft = draft
            self._index = len(self.history)
        self._index = max(0, self._index - 1)
        return InputDraft(self.history[self._index])

    def next(self, draft: InputDraft) -> InputDraft:
        """Restore the exact draft beyond the newest history entry."""
        if self.locked or self._index is None:
            return draft
        self._index += 1
        if self._index >= len(self.history):
            self._index = None
            return self._draft
        return InputDraft(self.history[self._index])


@dataclass(frozen=True, slots=True)
class LoadedSource:
    """Label the executed revision independently of the active editor tab."""

    document_id: str
    revision: int
    path: Path | None


@dataclass(frozen=True, slots=True)
class InspectorSelection:
    """Revision-bound selection; trace/world facts are supplied by later stages."""

    document_id: str
    revision: int
    world_id: str | None = None
    diagnostic_id: str | None = None
    pinned: bool = False


@dataclass
class EditorState:
    """Testable load/clear/reset transitions without terminal or VM inspection."""

    workspace: EditorWorkspace
    transcript: Transcript = field(default_factory=Transcript)
    input: InputState = field(default_factory=InputState)
    inspector: InspectorSelection | None = None
    loaded_source: LoadedSource | None = None
    runtime_status: RuntimeStatus = RuntimeStatus.IDLE

    def edit_document(self, document_id: str, source: str) -> Document:
        """Dismiss revision-bound inspection on root or imported source edits."""
        before = self.workspace.documents[document_id]
        document = self.workspace.update(document_id, source)
        if document.revision != before.revision:
            self.inspector = None
        return document

    def clear(self) -> None:
        """Clear retained transcript without changing the session or input."""
        self.transcript.clear()

    def reset(self, session: SessionService) -> None:
        """Clear session and transcript, retaining documents, history and lock."""
        session.reset()
        self.clear()
        self.loaded_source = None
        self.runtime_status = RuntimeStatus.IDLE

    def load(self, session: SessionService, document_id: str) -> ExecutionResult:
        """Prepare/validate/commit a load without altering documents or drafts.

        This synchronous coordinator is for service tests and worker ownership;
        the terminal frontend must invoke preparation/execution off its UI loop.
        """
        if self.runtime_status != RuntimeStatus.IDLE:
            raise ValueError("stop execution before loading another document")
        self.runtime_status = RuntimeStatus.PREPARING
        try:
            snapshot = self.workspace.capture(document_id)
            result = session.prepare(snapshot=snapshot)
            if result.prepared is None:
                outcome = ExecutionResult(
                    False,
                    tuple(
                        DiagnosticEvent(item, snapshot.root.source)
                        for item in result.diagnostics
                    ),
                )
            else:
                if not self.workspace.is_current(snapshot):
                    raise ValueError("workspace changed after preparation")
                self.runtime_status = RuntimeStatus.RUNNING
                outcome = session.execute(
                    result.prepared, workspace_revision=self.workspace.revision
                )
            for event in outcome.events:
                if isinstance(event, SessionStarted):
                    self.loaded_source = LoadedSource(
                        event.document_id, event.revision, snapshot.root.path
                    )
                    self.transcript.append(
                        TranscriptEntry("separator", "── New session ──")
                    )
                elif isinstance(event, OutputEvent):
                    self.transcript.append(TranscriptEntry("output", event.text))
                elif isinstance(event, ResultEvent):
                    self.transcript.append(
                        TranscriptEntry("result", "\n".join(event.values))
                    )
                elif isinstance(event, DiagnosticEvent):
                    self.transcript.append(
                        TranscriptEntry("diagnostic", event.diagnostic.message)
                    )
            if session.loaded_source is None:
                self.loaded_source = None
            return outcome
        finally:
            self.runtime_status = RuntimeStatus.IDLE
