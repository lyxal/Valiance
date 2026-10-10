"""Connect editor intents to an isolated, persistent session process."""

from __future__ import annotations

import asyncio
from time import monotonic

from textual import events
from textual.message import Message
from textual.widgets import RichLog, TextArea

from valiance.incremental.database import CompilationDatabase
from valiance.sessions.worker import SessionWorker

from .models import LoadedSource, TranscriptEntry


class CommandInput(TextArea):
    """Submit with Enter; preserve explicit multiline editing with Shift+Enter."""

    class Submitted(Message):
        """Signal submission without exposing program stdin to a widget."""

    async def _on_key(self, event: events.Key) -> None:
        """Keep command submission separate from the native newline action."""
        if event.key in {"enter", "ctrl+enter"}:
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
        else:
            await super()._on_key(event)


def _capture(documents, root, revision):
    """Discover imports using an isolated database and frozen editor sources."""
    database = CompilationDatabase()
    for document in documents:
        if document.path is not None:
            database.open_document(
                document.path, document.source, version=document.revision
            )
    return database.capture_workspace(root, workspace_revision=revision)


class EditorRuntime:
    """Own process lifecycle while all document and widget mutations stay on UI."""

    def __init__(self, app) -> None:
        """Initialize transport without executing restored or blank source."""
        self.app = app
        self.worker = SessionWorker()
        self.label = "Starting session"
        self.pending = None
        self.preparing = False
        self.closing = False
        self.waiting_input = False
        self.generation = 0
        self.partial = ""

    def start(self) -> None:
        """Spawn once and poll bounded transport batches on the UI timer."""
        self.worker.start()

    def write(self, kind: str, text: str) -> None:
        """Retain bounded transcript entries and preserve streamed line breaks."""
        self.app.state.transcript.append(TranscriptEntry(kind, text))
        self.partial += text
        log = self.app.query_one("#transcript", RichLog)
        while "\n" in self.partial or len(self.partial) >= 2048:
            if "\n" in self.partial[:2049]:
                line, self.partial = self.partial.split("\n", 1)
            else:
                line, self.partial = self.partial[:2048], self.partial[2048:]
            log.write(line)

    async def run_file(self) -> None:
        """Capture and prepare active source; stale preparations never execute."""
        if self.preparing or self.worker.busy or self.closing:
            self.app.notify("Stop the current execution before running another file")
            return
        self.preparing = True
        generation = self.generation
        self.label = "Preparing file"
        self.app._status()
        try:
            workspace = self.app.workspace
            workspace.update(workspace.active_document, self.app.source_editor.text)
            document = workspace.documents[workspace.active_document]
            snapshot = await asyncio.to_thread(
                _capture,
                tuple(workspace.documents.values()),
                document.root_source,
                workspace.revision,
            )
            deadline = monotonic() + 10
            while not self.worker.ready and generation == self.generation:
                if monotonic() >= deadline:
                    raise RuntimeError("Session worker did not start")
                await asyncio.sleep(0.05)
            if generation != self.generation:
                return
            if snapshot.workspace_revision != workspace.revision:
                self.write("notice", "Source changed during preparation; run again\n")
                return
            self.pending = snapshot
            self.worker.submit(snapshot=snapshot)
            self.app.repl_requested = True
            self.app._layout()
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            self.app.notify(str(exc), title="Run failed", severity="error")
        finally:
            self.preparing = False
            if not self.worker.busy and not self.closing:
                self.label = "Ready"
            self.app._status()

    def submit_command(self) -> None:
        """Send program input while waiting, otherwise submit a persistent command."""
        editor = self.app.query_one("#command", TextArea)
        source = editor.text
        if self.waiting_input:
            self.worker.provide_input(source)
            self.waiting_input = False
            self.label = "Running"
        elif (
            self.preparing or self.closing or not self.worker.ready or self.worker.busy
        ):
            self.app.notify("Session is busy; Stop before submitting another command")
            return
        elif not source.strip():
            return
        else:
            self.write("command", f"> {source}\n")
            self.worker.submit(source)
            self.label = "Running command"
        editor.clear()
        self.app._status()

    def poll(self) -> None:
        """Apply correlated lifecycle events without blocking rendering on output."""
        if self.closing:
            return
        batch = self.worker.poll()
        # stdout uses a separate bounded queue; drain it before completion labels.
        batch.sort(key=lambda item: item.kind in {"finished", "failed", "cancelled"})
        for event in batch:
            if event.kind == "ready":
                self.label = "Ready"
            elif event.kind == "prepared":
                current = bool(
                    self.pending
                    and self.pending.workspace_revision == self.app.workspace.revision
                )
                self.worker.commit(event.request, current)
            elif event.kind == "loaded":
                snapshot = self.pending
                self.app.state.loaded_source = LoadedSource(
                    snapshot.root.document_id,
                    snapshot.root.revision,
                    snapshot.root.path,
                )
                self.write("load", f"— Run {snapshot.root.path or 'Untitled'} —\n")
                self.label = "Running file"
            elif event.kind == "input":
                self.waiting_input = True
                self.label = "Waiting for program input"
                self.app.query_one("#command").focus()
            elif event.kind in {"output", "diagnostic", "truncated"}:
                self.write(
                    event.kind, event.text + ("\n" if event.kind == "truncated" else "")
                )
            elif event.kind in {"closed", "interrupted"}:
                self.app.state.loaded_source = None
                self.label = "Session worker exited"
                self.app.action_stop_execution()
            elif event.kind == "reset":
                self.app.state.loaded_source = None
            elif event.kind in {"finished", "failed", "cancelled"}:
                if event.text:
                    self.write("result", event.text + "\n")
                if self.partial:
                    self.write("output", "\n")
                self.pending = None
                self.waiting_input = False
                self.label = (
                    "Ready"
                    if event.kind == "finished"
                    else "Run failed"
                    if event.kind == "failed"
                    else "Run cancelled"
                )
        if batch:
            self.app._status()

    async def stop(self) -> None:
        """Reap the worker off the UI loop and replace its discarded session."""
        if self.closing:
            return
        self.closing = True
        self.generation += 1
        self.label = "Stopping"
        self.app._status()
        try:
            await asyncio.to_thread(self.worker.close)
            self.pending = None
            self.waiting_input = False
            self.app.state.loaded_source = None
            self.write("notice", "Stopped; session reset\n")
            self.label = "Starting session"
            self.worker = SessionWorker()
            self.worker.start()
        finally:
            self.closing = False
            self.app._status()

    async def close(self) -> None:
        """Ensure normal quit and test teardown leave no session process behind."""
        self.closing = True
        self.generation += 1
        await asyncio.to_thread(self.worker.close)
