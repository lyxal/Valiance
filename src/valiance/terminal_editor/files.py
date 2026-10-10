"""Document disk operations and small editor preferences, without execution."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .models import Document, EditorWorkspace


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a complete file from its own directory, cleaning failed writes."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except PermissionError:
                temporary.chmod(0o600)
                temporary.unlink(missing_ok=True)


def disk_bytes(path: Path) -> bytes | None:
    """Distinguish a deleted file from an empty saved file."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class ExternalChange:
    """One observed disk revision awaiting an explicit editor decision."""

    document_id: str
    path: Path
    data: bytes | None


class DiskConflict(OSError):
    """Require a reload/keep decision before overwriting changed disk contents."""


@dataclass(frozen=True)
class FileOperation:
    """Captured file intent: threads own bytes, the UI alone commits workspace state."""

    document_id: str
    target: Path
    source_path: Path | None
    source: str
    saved_source: str | None
    expected: bytes | None
    overwrite: bool
    rename: bool

    def perform(self) -> None:
        """Validate disk expectations and perform I/O without touching the workspace."""
        if self.source_path and (self.rename or self.target == self.source_path):
            if disk_bytes(self.source_path) != self.expected:
                raise DiskConflict(
                    "The saved file changed. Choose Reload or Keep first."
                )
        if (
            self.target != self.source_path
            and self.target.exists()
            and not self.overwrite
        ):
            raise FileExistsError(self.target)
        if self.rename:
            if self.target != self.source_path:
                os.replace(self.source_path, self.target)
        else:
            atomic_write(self.target, self.source.encode("utf-8"))


class DocumentFiles:
    """Apply successful file operations to workspace overlays only after disk I/O."""

    def __init__(self, workspace: EditorWorkspace) -> None:
        """Keep observed disk revisions independently from unsaved source."""
        self.workspace = workspace
        self.observed: dict[str, bytes | None] = {}

    def open(self, path: Path) -> Document:
        """Activate canonical duplicates without replacing their editor state."""
        path = path.resolve()
        for document in self.workspace.documents.values():
            if document.path == path:
                self.workspace.active_document = document.id
                return document
        return self.accept_open(path, path.read_bytes())

    def accept_open(self, path: Path, data: bytes) -> Document:
        """Commit bytes read by a worker on the workspace owner's thread."""
        source = data.decode("utf-8")
        document = self.workspace.open_document(path, source, saved_source=source)
        self.observed[document.id] = data
        return document

    def _target(self, document: Document, path: Path) -> Path:
        """Reject a path owned by another mounted document before touching disk."""
        path = path.resolve()
        if any(
            item.id != document.id and item.path == path
            for item in self.workspace.documents.values()
        ):
            raise ValueError("That file is already open in another tab.")
        return path

    def prepare(
        self,
        document_id: str,
        path: Path | None = None,
        *,
        overwrite: bool = False,
        rename: bool = False,
    ) -> FileOperation:
        """Capture text and observed disk state before asynchronous I/O."""
        document = self.workspace.documents[document_id]
        if path is None and document.path is None:
            raise ValueError("Choose a path for this untitled document.")
        target = self._target(document, path or document.path)
        return FileOperation(
            document_id,
            target,
            document.path,
            document.source,
            document.saved_source,
            self.observed.get(document_id),
            overwrite,
            rename and document.path is not None,
        )

    def commit(self, operation: FileOperation) -> Document:
        """Commit a completed write, preserving text typed during I/O."""
        baseline = operation.saved_source if operation.rename else operation.source
        document = self.workspace.associate_path(
            operation.document_id, operation.target, saved_source=baseline
        )
        self.observed[document.id] = (
            operation.expected if operation.rename else operation.source.encode("utf-8")
        )
        return document

    def save(
        self, document_id: str, path: Path | None = None, *, overwrite: bool = False
    ) -> Document:
        """Synchronously save through the same captured transaction used by the UI."""
        operation = self.prepare(document_id, path, overwrite=overwrite)
        operation.perform()
        return self.commit(operation)

    def rename(
        self, document_id: str, path: Path, *, overwrite: bool = False
    ) -> Document:
        """Move saved bytes while retaining unsaved source, baseline and view state."""
        operation = self.prepare(document_id, path, overwrite=overwrite, rename=True)
        operation.perform()
        return self.commit(operation)

    def external_changes(self) -> tuple[ExternalChange, ...]:
        """Read disk away from the UI loop without modifying document state."""
        changes = []
        for document in tuple(self.workspace.documents.values()):
            if document.path is None:
                continue
            try:
                data = disk_bytes(document.path)
            except OSError:
                continue
            if data != self.observed.get(document.id):
                changes.append(ExternalChange(document.id, document.path, data))
        return tuple(changes)

    def keep(self, change: ExternalChange) -> Document:
        """Acknowledge disk changes and preserve editor text as unsaved if different."""
        try:
            baseline = change.data.decode("utf-8") if change.data is not None else None
        except UnicodeError:
            baseline = None
        document = self.workspace.associate_path(
            change.document_id, change.path, saved_source=baseline
        )
        self.observed[document.id] = change.data
        return document

    def reload(self, document_id: str) -> Document:
        """Apply an explicitly approved disk reload without executing source."""
        document = self.workspace.documents[document_id]
        data = document.path.read_bytes()
        return self.accept_reload(document_id, data)

    def accept_reload(self, document_id: str, data: bytes) -> Document:
        """Commit an approved reload on the owning thread after successful decoding."""
        document = self.workspace.documents[document_id]
        source = data.decode("utf-8")
        self.workspace.update(document_id, source)
        document = self.workspace.associate_path(
            document_id, document.path, saved_source=source
        )
        self.observed[document_id] = data
        return document


@dataclass
class EditorPreferences:
    """Bounded recent paths and editing/layout preferences; never saved source."""

    recent: list[str] = field(default_factory=list)
    inspector_width: int = 32
    repl_height: int = 12
    indent_width: int = 2
    soft_wrap: bool = True

    @classmethod
    def load(cls, path: Path | None) -> EditorPreferences:
        """Validate optional persisted preferences and tolerate corrupt settings."""
        preferences = cls()
        if path is None:
            return preferences
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return preferences
            recent = data.get("recent", [])
            if isinstance(recent, list):
                preferences.recent = list(
                    dict.fromkeys(value for value in recent if isinstance(value, str))
                )[:20]
            for name, low, high in (
                ("inspector_width", 24, 200),
                ("repl_height", 8, 100),
                ("indent_width", 1, 8),
            ):
                value = data.get(name)
                if type(value) is int and low <= value <= high:
                    setattr(preferences, name, value)
            if type(data.get("soft_wrap")) is bool:
                preferences.soft_wrap = data["soft_wrap"]
        except OSError, ValueError:
            pass
        return preferences

    def remember(self, path: Path) -> None:
        """Move a successful open/save/rename to the front of bounded recent paths."""
        value = str(path.resolve())
        self.recent = [value, *(item for item in self.recent if item != value)][:20]

    def save(self, path: Path | None) -> None:
        """Persist preferences atomically without document text or runtime state."""
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(path, json.dumps(vars(self), indent=2).encode("utf-8"))
