"""Coalesced background analysis and revision-safe presentation for the editor."""

from __future__ import annotations

import asyncio
from time import monotonic

from valiance.analysis.inspection import InspectionSnapshot, analyse_sources

from .editor import SourceEmphasis


class EditorAnalysis:
    """Give each request private compiler ownership and discard obsolete results."""

    def __init__(self, app) -> None:
        """Initialize presentation without borrowing the execution session's state."""
        self.app = app
        self.snapshot: InspectionSnapshot | None = None
        self.requested = None
        self.observed = None
        self.task: asyncio.Task | None = None
        self.started = 0.0
        self.closed = False
        self.failure = ""
        self.disk_task: asyncio.Task | None = None
        self.checked_disk = 0.0

    @property
    def key(self):
        """Include open dependencies and active identity, not just root text."""
        return self.app.workspace.revision, self.app.workspace.active_document

    @property
    def label(self) -> str:
        """Delay progress text while replacing obsolete facts immediately."""
        if self.snapshot is not None:
            return str(len(self.snapshot.diagnostics))
        return "Updating…" if monotonic() - self.started >= 0.5 else "unavailable"

    def invalidate(self) -> None:
        """Clear revision-specific diagnostics and cursor facts on every source edit."""
        self.snapshot = None
        self.failure = ""
        self.started = monotonic()
        for editor in self.app.editor_screen.query("DocumentEditor"):
            editor.set_emphasis(())
        self.app.refresh_inspection()

    def poll(self) -> None:
        """Coalesce rapid edits, prioritizing the active root."""
        if self.closed:
            return
        if self.observed != self.key:
            self.invalidate()
            self.observed = self.key
        if self.requested != self.key and (self.task is None or self.task.done()):
            self.requested = self.key
            roots = tuple(
                item.root_source for item in self.app.workspace.documents.values()
            )
            root = self.app.workspace.documents[self.key[1]].root_source
            self.task = asyncio.create_task(self._analyse(roots, root, self.key))
        elif self.snapshot is None:
            self.app.refresh_inspection()
        if (
            self.snapshot is not None
            and monotonic() - self.checked_disk >= 1
            and (self.disk_task is None or self.disk_task.done())
        ):
            self.checked_disk = monotonic()
            self.disk_task = asyncio.create_task(self._check_disk(self.snapshot))

    async def _check_disk(self, product) -> None:
        """Invalidate unopened saved dependencies when their captured bytes change."""
        current = await asyncio.to_thread(product.workspace.disk_is_current)
        if not current and not self.closed and product is self.snapshot:
            self.invalidate()
            self.requested = None

    async def _analyse(self, roots, root, key) -> None:
        """Run checks off the UI loop and accept only current facts."""
        try:
            product = await asyncio.to_thread(analyse_sources, roots, root, key[0])
            disk_current = await asyncio.to_thread(product.workspace.disk_is_current)
            if self.closed or key != self.key:
                return
            if not disk_current:
                self.requested = None
                return
            self.snapshot = product
            for editor in self.app.editor_screen.query("DocumentEditor"):
                document = self.app.workspace.documents.get(
                    editor.id.removeprefix("source-")
                )
                if document is None:
                    continue
                source = document.source
                starts = [0]
                starts.extend(
                    index + 1 for index, char in enumerate(source) if char == "\n"
                )
                spans = []
                for diagnostic in product.diagnostics:
                    if (
                        diagnostic.source_file != document.path
                        or diagnostic.location is None
                    ):
                        continue
                    row = diagnostic.location.line - 1
                    if row >= len(starts):
                        continue
                    start = starts[row] + diagnostic.location.column - 1
                    end = start + 1
                    while end < len(source) and not source[end].isspace():
                        end += 1
                    spans.append(SourceEmphasis(start, end, "underline #ef8794"))
                editor.set_emphasis(tuple(spans))
            self.app.refresh_inspection()
        except Exception as error:
            if not self.closed and key == self.key:
                self.failure = f"Analysis unavailable: {error}"
                self.app.refresh_inspection()

    async def close(self) -> None:
        """Detach pending presentation so application exit cannot restore UI facts."""
        self.closed = True
        tasks = [task for task in (self.task, self.disk_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
